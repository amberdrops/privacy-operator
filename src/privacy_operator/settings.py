"""사용자 설정과 금고 비밀번호 다루기.

- 설정 파일(settings.json)에는 비밀번호를 넣지 않는다. 필드 자체가 없다.
- 비밀번호는 프로그램을 켤 때 한 번 받아 메모리(UnlockedVault)에만 둔다.
- '이 컴퓨터에서 기억하기'를 켜면 운영체제 자격 증명 저장소(Windows 자격 증명 관리자)에 넣는다.
- 금고는 프로젝트 폴더 안에도, 작업 폴더(to_ai, from_ai가 생기는 곳) 안에도 둘 수 없다. 반대도 안 된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Protocol

from privacy_operator.alias.vault import UnlockedVault, Vault

APP_DIR_NAME = "PrivacyOperator"
SETTINGS_FILE = "settings.json"
KEYRING_SERVICE = "PrivacyOperator.vault"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SettingsError(ValueError):
    pass


def config_dir() -> Path:
    """설정 폴더. 시험할 때는 PRIVACY_OPERATOR_HOME으로 바꾼다."""
    override = os.environ.get("PRIVACY_OPERATOR_HOME")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_DIR_NAME


def _documents() -> Path:
    return Path.home() / "Documents"


@dataclass
class Settings:
    vault_dir: str = ""
    workspace_dir: str = ""  # 이 아래에 to_ai/, from_ai/가 생긴다. AI 에이전트가 여는 쪽
    restored_dir: str = ""  # 복원본(실명). 기본은 금고 옆 '작전실복원'
    remember_password: bool = False
    retention_days: int = 90
    theme: str = "pink"  # 처음 켤 때 핑크 오퍼레이터(2026-11-08 결정). 설정에서 바꾸면 그 값이 저장되어 다음에도 쓰인다
    voice: str = "mission"  # 처음 켤 때 작전 말투(2026-11-08 결정)
    alias_continue: bool = False
    # 9단계 AI 탐지(선택). 둘 다 꺼 두는 것이 기본
    ai_schift: bool = False  # 이름과 주소를 Schift 모델로 더 찾기
    ai_ollama_model: str = ""  # 간접식별 후보를 찾을 Ollama 로컬 모델. 비면 쓰지 않음. 클라우드 모델은 거부
    ollama_port: int = 11434  # 접속 주소는 127.0.0.1 고정. 포트만 바꿀 수 있다

    def __post_init__(self):
        self.vault_dir = self.vault_dir or str(_documents() / "작전실금고")
        self.workspace_dir = self.workspace_dir or str(_documents() / "작전실")
        self.restored_dir = self.restored_dir or str(Path(self.vault_dir).parent / "작전실복원")

    @property
    def vault_path(self) -> Path:
        return Path(self.vault_dir)

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_dir)

    @property
    def restored_path(self) -> Path:
        return Path(self.restored_dir)

    @property
    def to_ai_path(self) -> Path:
        return self.workspace_path / "to_ai"

    @property
    def from_ai_path(self) -> Path:
        return self.workspace_path / "from_ai"


def _inside(child: Path, parent: Path) -> bool:
    child, parent = child.resolve(), parent.resolve()
    return child == parent or parent in child.parents


def _apart(a: Path, b: Path) -> bool:
    return not (_inside(a, b) or _inside(b, a))


def validate_paths(settings: Settings, project_root: Path = PROJECT_ROOT) -> None:
    vault, work, restored = settings.vault_path, settings.workspace_path, settings.restored_path
    if _inside(vault, project_root):
        raise SettingsError("금고는 프로그램 폴더 안에 둘 수 없습니다")
    if not _apart(vault, work):
        raise SettingsError("금고와 작업 폴더(보낼 문서함, 받은 결과함)는 서로 안에 둘 수 없습니다")
    if _inside(restored, project_root):
        raise SettingsError("복원본 폴더는 프로그램 폴더 안에 둘 수 없습니다")
    if not _apart(restored, work):
        raise SettingsError("복원본 폴더와 작업 폴더는 서로 안에 둘 수 없습니다. 실명이 AI 쪽으로 갈 수 있습니다")
    if not _apart(restored, vault):
        raise SettingsError("복원본 폴더와 금고는 서로 안에 둘 수 없습니다")


def load_settings() -> Settings:
    path = config_dir() / SETTINGS_FILE
    if not path.is_file():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known})


def save_settings(settings: Settings, project_root: Path = PROJECT_ROOT) -> Path:
    validate_paths(settings, project_root)
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SETTINGS_FILE
    path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 비밀번호 기억하기

class CredentialStore(Protocol):
    def get(self, vault_dir: str) -> str | None: ...
    def set(self, vault_dir: str, password: str) -> None: ...
    def delete(self, vault_dir: str) -> None: ...


class KeyringStore:
    """운영체제 자격 증명 저장소. 금고 위치마다 하나."""

    def get(self, vault_dir: str) -> str | None:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, _account(vault_dir))

    def set(self, vault_dir: str, password: str) -> None:
        import keyring
        keyring.set_password(KEYRING_SERVICE, _account(vault_dir), password)

    def delete(self, vault_dir: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError
        try:
            keyring.delete_password(KEYRING_SERVICE, _account(vault_dir))
        except PasswordDeleteError:
            pass


def _account(vault_dir: str) -> str:
    return str(Path(vault_dir).resolve())


class VaultSession:
    """프로그램을 켤 때 금고를 여는 흐름."""

    def __init__(self, settings: Settings, store: CredentialStore | None = None):
        self.settings = settings
        self.store = store or KeyringStore()
        self.vault = Vault(settings.vault_path)

    def remembered_password(self) -> str | None:
        if not self.settings.remember_password:
            return None
        return self.store.get(self.settings.vault_dir)

    def open(self, password: str | None = None) -> UnlockedVault:
        """password가 없으면 기억해 둔 것을 쓴다. 둘 다 없으면 물어봐야 한다(호출하는 쪽 몫)."""
        password = password or self.remembered_password()
        if password is None:
            raise SettingsError("금고 비밀번호를 입력하세요")
        unlocked = self.vault.unlock(password)
        self._remember(password)
        return unlocked

    def create(self, password: str) -> UnlockedVault:
        validate_paths(self.settings)
        unlocked = self.vault.create(password)
        self._remember(password)
        return unlocked

    def change_password(self, unlocked: UnlockedVault, new_password: str) -> None:
        unlocked.change_password(new_password)
        self._remember(new_password)

    def set_remember(self, on: bool, password: str | None = None) -> None:
        self.settings.remember_password = on
        if on and password:
            self.store.set(self.settings.vault_dir, password)
        if not on:
            self.store.delete(self.settings.vault_dir)

    def _remember(self, password: str) -> None:
        if self.settings.remember_password:
            self.store.set(self.settings.vault_dir, password)
