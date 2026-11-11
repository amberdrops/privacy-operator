"""모델 설치 도구(engine/schift/install.py)를 띄우고 진행을 읽는다.

통신은 설치 도구만 한다. 이 파일은 도구를 `subprocess`로 띄워 표준 출력의 JSON 줄만 읽는다(통신 모듈 없음).
도구는 단계마다 한 줄을 쓴다: {"step": 2, "of": 5, "key": "runtime"}, 끝나면 {"done": true},
실패하면 {"error": "<코드>"}와 종료 코드 1. 로그에는 오류 코드만 남긴다.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from privacy_operator.detect.ai.schift import engine_home, setup_dir, setup_python_dir

log = logging.getLogger(__name__)

# 오류 코드(설치 도구와 같다)
NO_SPACE = "NO_SPACE"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
VERIFY_FAILED = "VERIFY_FAILED"
SETUP_BROKEN = "SETUP_BROKEN"
CANCELLED = "CANCELLED"  # 사용자가 멈춤(도구가 보내는 코드가 아니다)
STEP_KEYS = ("python", "runtime", "packages", "model", "verify")


def default_command(home: Path) -> list[str]:
    exe = "python.exe" if sys.platform == "win32" else "python"
    py = setup_python_dir()
    return [str(py / exe), str(setup_dir() / "install.py"), "--embedded", str(py), "--home", str(home)]


class ModelSetup:
    """설치 도구 한 번 실행. run()은 끝날 때까지 막으니 스레드에서 부르고, cancel()은 다른 스레드에서 부른다."""

    def __init__(self, home: Path | None = None, command: list[str] | None = None):
        self.home = home or engine_home()
        self.command = command  # None이면 default_command (시험에서 가짜 도구로 바꾼다)
        self.proc: subprocess.Popen | None = None
        self._cancelled = False
        self._lock = threading.Lock()

    def run(self, progress: Callable[[int, int, str], None] | None = None) -> str:
        """성공하면 빈 글자, 아니면 오류 코드. 중간에 멈추면 CANCELLED."""
        cmd = self.command or default_command(self.home)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with self._lock:
            if self._cancelled:
                return CANCELLED
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL, env=env, creationflags=flags)
            except OSError:
                log.warning("모델 설치 도구를 띄우지 못함")
                return SETUP_BROKEN
        done, error = False, ""
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("done"):
                done = True
            elif "error" in msg:
                error = str(msg["error"])
            elif "step" in msg and progress:
                progress(int(msg["step"]), int(msg.get("of", len(STEP_KEYS))), str(msg.get("key", "")))
        code = self.proc.wait()
        if self._cancelled:
            return CANCELLED
        if done and code == 0:
            return ""
        error = error or SETUP_BROKEN
        log.warning("모델 설치 실패(%s)", error)
        return error

    def cancel(self) -> None:
        """도구와 그 자식(pip 등)을 모두 끝낸다."""
        with self._lock:
            self._cancelled = True
            proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
