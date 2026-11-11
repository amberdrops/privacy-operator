"""절대 규칙 1: src/ 안에서 외부 통신 모듈을 쓰지 않는다.

src/ 아래 모든 .py 파일을 구문 분석해서 금지 모듈을 불러오는 곳을 찾는다.
- import a.b / from a.b import c / from a import b(하위 모듈) 모양
- __import__("a"), importlib.import_module("a") 처럼 문자열로 불러오는 모양

예외 파일은 없다. 로컬 모델(Ollama) 연동을 지운 뒤로 src/ 어디에서도 http.client를 포함한
통신 모듈을 쓰지 못한다.
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
