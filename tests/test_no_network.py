"""절대 규칙 1: src/ 안에서 외부 통신 모듈을 쓰지 않는다.

src/ 아래 모든 .py 파일을 구문 분석해서 금지 모듈을 불러오는 곳을 찾는다.
- import a.b / from a.b import c / from a import b(하위 모듈) 모양
- __import__("a"), importlib.import_module("a") 처럼 문자열로 불러오는 모양

9단계에서 Ollama(127.0.0.1) 연동을 붙일 때는 그 모듈 파일 하나만
ALLOWED_FILES에 넣고, 해당 파일에서 접속 주소를 검사하는 테스트를 따로 둔다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

FORBIDDEN_MODULES = {
    "requests",
    "httpx",
    "urllib.request",
    "urllib3",
    "socket",
    "socketserver",
    "ssl",
    "http.client",
    "http.server",
    "xmlrpc",
    "aiohttp",
    "websocket",
    "websockets",
    "ftplib",
    "smtplib",
    "telnetlib",
}

# src/ 기준 상대 경로. Ollama(127.0.0.1) 연결 파일 하나만. 그 파일은 아래 test_ollama_*로 따로 검사한다.
OLLAMA_FILE = "privacy_operator/detect/ai/ollama_local.py"
ALLOWED_FILES: set[str] = {OLLAMA_FILE}
# 예외 파일에서도 쓸 수 있는 것은 http.client 하나
ALLOWED_IN_OLLAMA = {"http.client"}


def _is_forbidden(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in FORBIDDEN_MODULES)


def _dynamic_import_target(node: ast.Call) -> str | None:
    """__import__("x"), importlib.import_module("x")의 문자열 인자를 돌려준다."""
    func = node.func
    is_dunder = isinstance(func, ast.Name) and func.id == "__import__"
    is_importlib = (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        or isinstance(func, ast.Name)
        and func.id == "import_module"
    )
    if not (is_dunder or is_importlib) or not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def find_forbidden_imports(source: str, filename: str = "<source>") -> list[tuple[int, str]]:
    """소스에서 금지 모듈을 불러오는 (줄 번호, 모듈 이름) 목록을 돌려준다."""
    tree = ast.parse(source, filename=filename)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    hits.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue  # 패키지 안 상대 import
            if _is_forbidden(node.module):
                hits.append((node.lineno, node.module))
                continue
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if _is_forbidden(full):
                    hits.append((node.lineno, full))
        elif isinstance(node, ast.Call):
            target = _dynamic_import_target(node)
            if target and _is_forbidden(target):
                hits.append((node.lineno, target))
    return hits


def _src_files() -> list[Path]:
    return sorted(SRC_DIR.rglob("*.py"))


def test_src_has_python_files():
    assert _src_files(), "src/ 안에서 .py 파일을 찾지 못했습니다. 검사가 헛돌고 있습니다."


def test_src_has_no_network_imports():
    problems = []
    for path in _src_files():
        rel = path.relative_to(SRC_DIR).as_posix()
        if rel in ALLOWED_FILES:
            continue
        for lineno, module in find_forbidden_imports(path.read_text(encoding="utf-8"), rel):
            problems.append(f"{rel}:{lineno} {module}")
    assert not problems, "외부 통신 모듈 사용 금지(CLAUDE.md 절대 규칙 1):\n" + "\n".join(problems)


@pytest.mark.parametrize(
    "source",
    [
        "import requests",
        "import socket",
        "import urllib.request",
        "import http.client as hc",
        "from urllib import request",
        "from urllib.request import urlopen",
        "from http import client",
        "from httpx import Client",
        "import aiohttp",
        "import websockets",
        "def f():\n    import socket",
        "__import__('socket')",
        "import importlib\nimportlib.import_module('requests')",
        "from importlib import import_module\nimport_module('http.client')",
    ],
)
def test_checker_catches_forbidden(source):
    assert find_forbidden_imports(source)


@pytest.mark.parametrize(
    "source",
    [
        "import json",
        "import urllib.parse",
        "from urllib.parse import quote",
        "from http import HTTPStatus",
        "import socket_tools_local",
        "from . import socket",
        "import pathlib",
    ],
)
def test_checker_allows_safe(source):
    assert not find_forbidden_imports(source)


# ---------------------------------------------------------------- Ollama 예외 파일 (9단계)

def _ollama_source() -> str:
    return (SRC_DIR / OLLAMA_FILE).read_text(encoding="utf-8")


def test_ollama_file_uses_only_http_client():
    modules = {m for _, m in find_forbidden_imports(_ollama_source(), OLLAMA_FILE)}
    assert modules <= ALLOWED_IN_OLLAMA, f"Ollama 파일에서 허용하지 않은 통신 모듈: {modules - ALLOWED_IN_OLLAMA}"


def test_ollama_host_is_fixed_loopback():
    """접속 주소는 상수 HOST = "127.0.0.1" 하나이고, 연결은 모두 그 상수로 만든다."""
    tree = ast.parse(_ollama_source())
    hosts = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "HOST" for t in n.targets)]
    assert len(hosts) == 1 and isinstance(hosts[0].value, ast.Constant) and hosts[0].value.value == "127.0.0.1"
    conns = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("HTTPConnection", "HTTPSConnection", "create_connection")]
    assert conns, "연결을 만드는 곳을 찾지 못했습니다. 검사가 헛돌고 있습니다."
    for c in conns:
        assert c.func.attr == "HTTPConnection"
        assert isinstance(c.args[0], ast.Name) and c.args[0].id == "HOST", "연결 주소는 HOST 상수만 씁니다"


def test_ollama_file_has_no_other_addresses():
    text = _ollama_source()
    for bad in ("http://", "https://", "localhost", "0.0.0.0", "ollama.com", "OLLAMA_HOST"):
        assert bad not in text.replace("127.0.0.1", ""), f"Ollama 파일에 다른 주소가 있습니다: {bad}"


def test_only_ollama_file_talks_to_ollama():
    """다른 파일이 HTTPConnection을 직접 만들지 않는다(불러오기 검사와 별개로 한 번 더)."""
    for path in _src_files():
        rel = path.relative_to(SRC_DIR).as_posix()
        if rel != OLLAMA_FILE:
            assert "HTTPConnection" not in path.read_text(encoding="utf-8"), rel
