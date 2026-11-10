"""설정과 비밀번호 기억하기(3단계 완료 기준 3). 실제 Windows 자격 증명 관리자는 건드리지 않는다."""

from __future__ import annotations

import pytest

from privacy_operator.alias.crypto import WrongPassword
from privacy_operator.settings import (
    PROJECT_ROOT,
    Settings,
    SettingsError,
    VaultSession,
    config_dir,
    load_settings,
    save_settings,
    validate_paths,
)

PW = "settings-password-1"


class FakeStore:
    def __init__(self):
        self.data = {}

    def get(self, vault_dir):
        return self.data.get(vault_dir)

    def set(self, vault_dir, password):
        self.data[vault_dir] = password

    def delete(self, vault_dir):
        self.data.pop(vault_dir, None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVACY_OPERATOR_HOME", str(tmp_path / "config"))
    return tmp_path


def _settings(home, **kw):
    return Settings(vault_dir=str(home / "금고"), workspace_dir=str(home / "작전실"), **kw)


def test_settings_file_never_has_password(home):
    """완료 기준: 설정 파일 어디에도 비밀번호 평문이 없다."""
    settings = _settings(home, remember_password=True)
    store = FakeStore()
    session = VaultSession(settings, store)
    unlocked = session.create(PW)
    session.change_password(unlocked, PW + "-new")
    save_settings(settings)

    files = [p for p in config_dir().rglob("*") if p.is_file()]
    assert files, "설정 파일이 없으면 검사가 헛돕니다"
    for p in files:
        data = p.read_bytes()
        assert PW.encode() not in data and (PW + "-new").encode() not in data
    assert [k for k in vars(load_settings()) if "password" in k] == ["remember_password"]  # 비밀번호 칸이 없다
    assert store.get(settings.vault_dir) == PW + "-new"  # 기억하기를 켜면 저장소에만


def test_settings_roundtrip(home):
    settings = _settings(home, theme="pink", voice="mission", retention_days=30)
    save_settings(settings)
    assert load_settings() == settings


def test_default_locations_are_under_documents(home):
    s = Settings()
    assert s.vault_dir.endswith("작전실금고") and s.workspace_dir.endswith("작전실")


def test_vault_inside_project_is_refused(home):
    with pytest.raises(SettingsError):
        validate_paths(Settings(vault_dir=str(PROJECT_ROOT / "vault"), workspace_dir=str(home / "w")))


@pytest.mark.parametrize("vault,work", [("작전실/금고", "작전실"), ("금고", "금고/작전실"), ("같은곳", "같은곳")])
def test_vault_and_workspace_cannot_nest(home, vault, work):
    with pytest.raises(SettingsError):
        validate_paths(Settings(vault_dir=str(home / vault), workspace_dir=str(home / work)))


def test_open_uses_remembered_password_only_when_enabled(home):
    store = FakeStore()
    settings = _settings(home, remember_password=True)
    VaultSession(settings, store).create(PW)
    assert VaultSession(settings, store).open()  # 묻지 않고 열림

    settings.remember_password = False
    with pytest.raises(SettingsError):
        VaultSession(settings, store).open()
    assert VaultSession(settings, store).open(PW)


def test_turning_off_remember_deletes_stored_password(home):
    store = FakeStore()
    settings = _settings(home, remember_password=True)
    session = VaultSession(settings, store)
    session.create(PW)
    session.set_remember(False)
    assert store.get(settings.vault_dir) is None


def test_wrong_password_is_not_remembered(home):
    store = FakeStore()
    settings = _settings(home, remember_password=True)
    VaultSession(settings, store).create(PW)
    store.delete(settings.vault_dir)
    with pytest.raises(WrongPassword):
        VaultSession(settings, store).open("wrong-password-9")
    assert store.get(settings.vault_dir) is None
