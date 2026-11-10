"""비밀번호로 잠그는 암호화: scrypt로 키를 만들고 Fernet으로 잠근다.

파일 형태(봉투)는 JSON이다. 원문 값은 token 안에만 있다.
    {"schema": "privacy-operator/sealed@1", "kdf": "scrypt", "n": 32768, "r": 8, "p": 1,
     "salt": "<base64>", "token": "<Fernet token>"}
파일마다 salt를 새로 만든다.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SCHEMA = "privacy-operator/sealed@1"
SCRYPT_N = 2**15  # 메모리 약 32MB, 한 번에 0.1초 안팎
SCRYPT_R = 8
SCRYPT_P = 1
MIN_PASSWORD_LENGTH = 8


class WrongPassword(Exception):
    def __init__(self):
        super().__init__("금고 비밀번호가 맞지 않습니다")


class BadSealedFile(Exception):
    pass


def _key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    raw = Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def seal(data: bytes, password: str) -> bytes:
    salt = os.urandom(16)
    token = Fernet(_key(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)).encrypt(data)
    envelope = {"schema": SCHEMA, "kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "salt": base64.b64encode(salt).decode("ascii"), "token": token.decode("ascii")}
    return json.dumps(envelope).encode("utf-8")


def unseal(blob: bytes, password: str) -> bytes:
    try:
        env = json.loads(blob)
        if env.get("schema") != SCHEMA or env.get("kdf") != "scrypt":
            raise BadSealedFile("알 수 없는 암호 파일 형식입니다")
        salt = base64.b64decode(env["salt"])
        key = _key(password, salt, int(env["n"]), int(env["r"]), int(env["p"]))
        token = env["token"].encode("ascii")
    except (ValueError, KeyError, TypeError) as e:
        raise BadSealedFile("암호 파일이 손상되었습니다") from e
    try:
        return Fernet(key).decrypt(token)
    except InvalidToken:
        raise WrongPassword() from None
