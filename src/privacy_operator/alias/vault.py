"""금고: 작전마다 `<금고>/<작전번호>/mapping.enc`. 비밀번호는 금고 하나에 하나다.

금고 안에서 원문 값이 있는 파일은 mapping.enc뿐이다(암호화됨). vault.check는 비밀번호 확인용으로,
정해진 문구만 잠가 둔다. 금고에 원본 사본은 두지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path

from privacy_operator.alias.crypto import MIN_PASSWORD_LENGTH, WrongPassword, seal, unseal

log = logging.getLogger(__name__)

CHECK_FILE = "vault.check"
MAPPING_FILE = "mapping.enc"
LOG_FILE = "log.json"
_CHECK_TEXT = b"privacy-operator vault check"
MISSION_ID_RE = re.compile(r"M-\d{8}-\d{2,}")
_NEW = ".new"


class VaultError(Exception):
    pass


def _check_password_rule(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise VaultError(f"금고 비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다")


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _mission_dir(root: Path, mission_id: str) -> Path:
    if not MISSION_ID_RE.fullmatch(mission_id):
        raise VaultError("작전 번호 형식이 아닙니다 (M-YYYYMMDD-NN)")
    return root / mission_id


class Vault:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def is_initialized(self) -> bool:
        return (self.root / CHECK_FILE).is_file()

    def create(self, password: str) -> UnlockedVault:
        """처음 실행할 때 금고를 만든다."""
        _check_password_rule(password)
        if self.is_initialized():
            raise VaultError("이미 만든 금고입니다")
        self.root.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.root / CHECK_FILE, seal(_CHECK_TEXT, password))
        log.info("금고를 만들었습니다")
        return UnlockedVault(self.root, password)

    def unlock(self, password: str) -> UnlockedVault:
        if not self.is_initialized():
            raise VaultError("금고가 아직 없습니다. 설정에서 금고 비밀번호를 먼저 만드세요")
        self._recover_password_change()
        if unseal((self.root / CHECK_FILE).read_bytes(), password) != _CHECK_TEXT:
            raise WrongPassword()
        return UnlockedVault(self.root, password)

    def _recover_password_change(self) -> None:
        """비밀번호 바꾸기가 중간에 끊겼으면 정리한다(UnlockedVault.change_password 참고)."""
        leftovers = list(self.root.glob(f"*/{MAPPING_FILE}{_NEW}"))
        check_new = self.root / (CHECK_FILE + _NEW)
        if check_new.exists():  # 2단계 전에 끊김: 아직 아무것도 바뀌지 않았다. 새 파일을 버린다
            check_new.unlink()
            for p in leftovers:
                p.unlink()
            log.warning("끊긴 비밀번호 바꾸기를 취소했습니다")
        elif leftovers:  # 2단계 중에 끊김: 확인 파일은 이미 새 비밀번호다. 나머지를 마저 바꾼다
            for p in leftovers:
                os.replace(p, p.with_name(MAPPING_FILE))
            log.warning("끊긴 비밀번호 바꾸기를 마쳤습니다 (%d개)", len(leftovers))


class UnlockedVault:
    """비밀번호는 이 객체의 메모리에만 있다. 파일이나 로그에 쓰지 않는다."""

    def __init__(self, root: Path, password: str):
        self.root = root
        self.__password = password

    def __repr__(self) -> str:
        return f"UnlockedVault(missions={len(self.missions())})"

    def missions(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob(f"*/{MAPPING_FILE}")
                      if MISSION_ID_RE.fullmatch(p.parent.name))

    def new_mission_id(self, today: date | None = None) -> str:
        """M-YYYYMMDD-NN. 같은 날 작전은 번호를 이어서 매긴다. 폴더를 만들어 번호를 잡아 둔다."""
        day = (today or date.today()).strftime("%Y%m%d")
        n = 1
        while True:
            mission_id = f"M-{day}-{n:02d}"
            try:
                (self.root / mission_id).mkdir()
                return mission_id
            except FileExistsError:
                n += 1

    def save_mapping(self, mission_id: str, mapping: dict) -> Path:
        if mapping.get("mission_id") != mission_id:
            raise VaultError("대응표의 작전 번호가 다릅니다")
        folder = _mission_dir(self.root, mission_id)
        folder.mkdir(exist_ok=True)
        path = folder / MAPPING_FILE
        data = json.dumps(mapping, ensure_ascii=False).encode("utf-8")
        _write_atomic(path, seal(data, self.__password))
        log.info("대응표 저장 %s 항목 %d개", mission_id, len(mapping.get("entries", [])))
        return path

    def discard(self, mission_id: str) -> None:
        """송출하지 못한 작전의 번호와 대응표를 지운다. 처리 기록(log.json)이 있는 작전은 지우지 않는다."""
        folder = _mission_dir(self.root, mission_id)
        if not folder.is_dir() or (folder / LOG_FILE).exists():
            return
        for p in folder.iterdir():
            p.unlink()
        folder.rmdir()
        log.info("송출하지 못한 작전 번호 %s를 지웠습니다", mission_id)

    def save_log(self, mission_id: str, record: dict) -> Path:
        """처리 기록(log.json). 원문 값을 넣지 않는다. 부르는 쪽이 차단 목록 검사를 한다."""
        folder = _mission_dir(self.root, mission_id)
        folder.mkdir(exist_ok=True)
        path = folder / LOG_FILE
        _write_atomic(path, json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"))
        return path

    def load_mapping(self, mission_id: str) -> dict:
        path = _mission_dir(self.root, mission_id) / MAPPING_FILE
        if not path.is_file():
            raise VaultError(f"대응표가 없습니다: {mission_id}")
        return json.loads(unseal(path.read_bytes(), self.__password))

    def change_password(self, new_password: str) -> None:
        """모든 대응표를 새 비밀번호로 다시 잠근다.

        1단계: 모든 파일의 새 사본(.new)을 만들고 새 비밀번호로 열리는지 확인한다. 끊기면 아무것도 안 바뀐다.
        2단계: 확인 파일부터 바꾸고, 대응표를 차례로 바꾼다. 끊기면 다음 unlock 때 마저 바꾼다.
        """
        _check_password_rule(new_password)
        staged: list[Path] = []
        check_new = self.root / (CHECK_FILE + _NEW)
        try:
            for mission_id in self.missions():
                path = self.root / mission_id / MAPPING_FILE
                plain = unseal(path.read_bytes(), self.__password)
                sealed = seal(plain, new_password)
                if unseal(sealed, new_password) != plain:
                    raise VaultError("다시 잠근 대응표를 열 수 없습니다")
                new = path.with_name(MAPPING_FILE + _NEW)
                new.write_bytes(sealed)
                staged.append(new)
            check_new.write_bytes(seal(_CHECK_TEXT, new_password))
        except BaseException:
            for p in staged + [check_new]:
                p.unlink(missing_ok=True)
            raise
        os.replace(check_new, self.root / CHECK_FILE)
        for new in staged:
            os.replace(new, new.with_name(MAPPING_FILE))
        self.__password = new_password
        log.info("금고 비밀번호를 바꿨습니다 (대응표 %d개 다시 잠금)", len(staged))
