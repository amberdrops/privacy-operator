"""kordoc 엔진(Node) 위치 찾기와 SDK 설정.

kordoc Python SDK는 엔진을 품고 있지 않고 `node dist/cli.js parse-worker`를 띄워 쓴다.
엔진 찾는 순서: KORDOC_CLI 환경변수 → 프로젝트의 engine/node_modules/kordoc/dist/cli.js
Node 찾는 순서: KORDOC_NODE 환경변수 → 설치판의 engine/node/node.exe → PATH의 node
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

from kordoc import KordocConfig
from kordoc import _worker as _kordoc_worker

# 설치판은 명령창 없는 프로그램이라, 콘솔 프로그램(node.exe)을 띄우면 그때마다 검은 창이 새로 뜬다.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _NoWindowAsyncio:
    """kordoc SDK(0.2.1)는 엔진을 띄울 때 창 숨김 옵션을 주지 않는다. 그 모듈이 보는 asyncio만 이것으로 바꿔
    엔진 띄우기에 창 숨김을 더한다. 나머지는 진짜 asyncio를 그대로 쓴다(다른 곳의 asyncio는 건드리지 않는다)."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def create_subprocess_exec(*args, **kwargs):
        kwargs.setdefault("creationflags", NO_WINDOW)
        return await asyncio.create_subprocess_exec(*args, **kwargs)


if NO_WINDOW and not isinstance(_kordoc_worker.asyncio, _NoWindowAsyncio):
    _kordoc_worker.asyncio = _NoWindowAsyncio()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUNDLED_CLI = PROJECT_ROOT / "engine" / "node_modules" / "kordoc" / "dist" / "cli.js"
BUNDLED_NODE = PROJECT_ROOT / "engine" / "node" / "node.exe"  # 설치판에만 있다

# 사용자가 다른 값을 줘도 덮어쓴다. 엔진의 모델 내려받기와 webhook이 이 값으로 막힌다.
FORCED_ENV = {"KORDOC_OFFLINE": "1"}


class EngineNotFound(RuntimeError):
    pass


def find_cli() -> Path:
    env_cli = os.environ.get("KORDOC_CLI")
    if env_cli:
        return Path(env_cli)
    if BUNDLED_CLI.is_file():
        return BUNDLED_CLI
    raise EngineNotFound("kordoc 엔진이 없습니다. engine/ 폴더에서 npm install --ignore-scripts 를 하세요.")


def find_node() -> str:
    """설치판은 engine/node/node.exe를 함께 넣는다. 그 PC에 깔린 Node보다 먼저 쓴다(판이 낮을 수 있다)."""
    bundled = str(BUNDLED_NODE) if BUNDLED_NODE.is_file() else None
    node = os.environ.get("KORDOC_NODE") or bundled or shutil.which("node")
    if not node:
        raise EngineNotFound("Node.js를 찾지 못했습니다. Node 20 이상이 필요합니다.")
    return node


def make_config(**overrides) -> KordocConfig:
    env = {**overrides.pop("env", {}), **FORCED_ENV}
    return KordocConfig(node=find_node(), cli=str(find_cli()), env=env, **overrides)
