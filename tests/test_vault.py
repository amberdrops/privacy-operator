"""금고와 대응표 암호화(3단계 완료 기준 1, 2). 비밀번호와 값은 모두 지어낸 것이다."""

from __future__ import annotations

import json
import os
from datetime import date

import pytest

from privacy_operator.alias.crypto import BadSealedFile, WrongPassword, seal, unseal
from privacy_operator.alias.vault import CHECK_FILE, MAPPING_FILE, Vault, VaultError

PW = "first-password-1"
PW2 = "second-password-2"


def _mapping(mission_id, value="지어낸이름"):
    return {"mission_id": mission_id, "entries": [
        {"alias_code": "[강사-01]", "category": "이름", "raw_value": value, "has_batchim": False,
         "source_files": ["a.hwpx"], "created_at": "2026-11-07T00:00:00+00:00"}]}


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault")


def test_seal_roundtrip_and_wrong_password():
    blob = seal("가나다".encode(), PW)
    assert unseal(blob, PW) == "가나다".encode()
    with pytest.raises(WrongPassword):
        unseal(blob, PW2)


def test_sealed_file_is_json_envelope_without_plain_text():
    blob = seal("지어낸이름".encode(), PW)
    env = json.loads(blob)
    assert env["schema"] == "privacy-operator/sealed@1" and env["kdf"] == "scrypt"
    assert "지어낸이름".encode() not in blob


def test_each_seal_uses_new_salt():
    assert json.loads(seal(b"x", PW))["salt"] != json.loads(seal(b"x", PW))["salt"]


def test_damaged_file_is_not_reported_as_wrong_password():
    with pytest.raises(BadSealedFile):
        unseal(b"not json", PW)


def test_short_password_is_refused(vault):
    with pytest.raises(VaultError):
        vault.create("short")


def test_wrong_password_does_not_open_vault_or_mapping(vault):
    """완료 기준: 비밀번호가 틀리면 대응표가 열리지 않는다."""
    unlocked = vault.create(PW)
    mid = unlocked.new_mission_id(date(2026, 11, 7))
    path = unlocked.save_mapping(mid, _mapping(mid))
    with pytest.raises(WrongPassword):
        vault.unlock(PW2)
    with pytest.raises(WrongPassword):
        unseal(path.read_bytes(), PW2)
    assert vault.unlock(PW).load_mapping(mid) == _mapping(mid)


def test_change_password_reseals_previous_missions(vault):
    """완료 기준: 비밀번호를 바꾸면 이전 작전의 대응표도 새 비밀번호로 열린다."""
    unlocked = vault.create(PW)
    ids = []
    for _ in range(3):
        mid = unlocked.new_mission_id(date(2026, 11, 7))
        unlocked.save_mapping(mid, _mapping(mid, f"값{len(ids)}"))
        ids.append(mid)
    assert ids == ["M-20261107-01", "M-20261107-02", "M-20261107-03"]

    unlocked.change_password(PW2)

    with pytest.raises(WrongPassword):
        vault.unlock(PW)
    reopened = vault.unlock(PW2)
    assert [reopened.load_mapping(m)["entries"][0]["raw_value"] for m in ids] == ["값0", "값1", "값2"]
    for m in ids:
        with pytest.raises(WrongPassword):
            unseal((vault.root / m / MAPPING_FILE).read_bytes(), PW)
    assert not list(vault.root.rglob("*.new"))


def test_interrupted_change_before_switch_keeps_old_password(vault):
    unlocked = vault.create(PW)
    mid = unlocked.new_mission_id(date(2026, 11, 7))
    unlocked.save_mapping(mid, _mapping(mid))
    # 1단계까지만 하고 끊긴 상태: 새 사본만 있고 아무것도 바뀌지 않았다
    (vault.root / mid / (MAPPING_FILE + ".new")).write_bytes(seal(b"{}", PW2))
    (vault.root / (CHECK_FILE + ".new")).write_bytes(b"{}")
    assert vault.unlock(PW).load_mapping(mid) == _mapping(mid)
    assert not list(vault.root.rglob("*.new"))


def test_interrupted_change_after_switch_finishes_with_new_password(vault):
    unlocked = vault.create(PW)
    a = unlocked.new_mission_id(date(2026, 11, 7))
    b = unlocked.new_mission_id(date(2026, 11, 7))
    unlocked.save_mapping(a, _mapping(a))
    unlocked.save_mapping(b, _mapping(b))
    # 2단계 중 끊긴 상태: 확인 파일과 a는 바뀌었고 b의 새 사본이 남았다
    for m in (a, b):
        plain = unseal((vault.root / m / MAPPING_FILE).read_bytes(), PW)
        (vault.root / m / (MAPPING_FILE + ".new")).write_bytes(seal(plain, PW2))
    os.replace(vault.root / a / (MAPPING_FILE + ".new"), vault.root / a / MAPPING_FILE)
    (vault.root / CHECK_FILE).write_bytes(seal(b"privacy-operator vault check", PW2))

    reopened = vault.unlock(PW2)
    assert reopened.load_mapping(a) == _mapping(a) and reopened.load_mapping(b) == _mapping(b)


@pytest.mark.parametrize("bad", ["../escape", "M-2026-1", "M-20261107-01/../../x", ""])
def test_mission_id_must_match_format(vault, bad):
    unlocked = vault.create(PW)
    with pytest.raises(VaultError):
        unlocked.load_mapping(bad)


def test_mapping_mission_id_must_match(vault):
    unlocked = vault.create(PW)
    with pytest.raises(VaultError):
        unlocked.save_mapping("M-20261107-01", _mapping("M-20261107-02"))


def test_vault_files_other_than_mapping_have_no_values(vault):
    unlocked = vault.create(PW)
    mid = unlocked.new_mission_id(date(2026, 11, 7))
    unlocked.save_mapping(mid, _mapping(mid, "지어낸이름"))
    for p in vault.root.rglob("*"):
        if p.is_file():
            assert "지어낸이름".encode() not in p.read_bytes(), p.name


def test_unlocked_vault_repr_has_no_password(vault):
    unlocked = vault.create(PW)
    assert PW not in repr(unlocked) and PW not in str(vars(unlocked).keys())
