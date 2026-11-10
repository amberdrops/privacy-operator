"""kordoc 엔진과 Node 찾기. 설치판은 engine/node/node.exe를 함께 넣는다(11단계)."""

from pathlib import Path

from privacy_operator.reader import engine


def test_bundled_node_comes_before_node_on_path(tmp_path, monkeypatch):
    node = tmp_path / "node.exe"
    node.write_bytes(b"")
    monkeypatch.delenv("KORDOC_NODE", raising=False)
    monkeypatch.setattr(engine, "BUNDLED_NODE", node)
    monkeypatch.setattr(engine.shutil, "which", lambda name: "C:/other/node.exe")
    assert engine.find_node() == str(node)


def test_node_on_path_when_nothing_is_bundled(tmp_path, monkeypatch):
    monkeypatch.delenv("KORDOC_NODE", raising=False)
    monkeypatch.setattr(engine, "BUNDLED_NODE", tmp_path / "없음" / "node.exe")
    monkeypatch.setattr(engine.shutil, "which", lambda name: "C:/other/node.exe")
    assert engine.find_node() == "C:/other/node.exe"


def test_env_override_wins(tmp_path, monkeypatch):
    node = tmp_path / "node.exe"
    node.write_bytes(b"")
    monkeypatch.setattr(engine, "BUNDLED_NODE", node)
    monkeypatch.setenv("KORDOC_NODE", "D:/mine/node.exe")
    assert engine.find_node() == "D:/mine/node.exe"


def test_bundled_paths_sit_next_to_the_program_folder():
    """묶은 판에서도 같은 자리(프로그램 폴더/engine)를 가리키게 src 구조와 같은 깊이를 쓴다."""
    assert engine.BUNDLED_NODE.parent.parent == engine.BUNDLED_CLI.parents[3]
    assert Path(engine.__file__).resolve().parents[3] == engine.PROJECT_ROOT


def test_kordoc_engine_starts_without_a_console_window(monkeypatch):
    """설치판은 명령창 없는 프로그램이다. kordoc SDK가 엔진(node)을 띄울 때 창 숨김을 더한다."""
    import asyncio

    from kordoc import _worker

    assert isinstance(_worker.asyncio, engine._NoWindowAsyncio)
    assert _worker.asyncio.subprocess is asyncio.subprocess  # 나머지는 진짜 asyncio
    seen = {}

    async def fake(*args, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    asyncio.run(_worker.asyncio.create_subprocess_exec("node", "cli.js"))
    assert seen["creationflags"] == engine.NO_WINDOW != 0
    assert asyncio.create_subprocess_exec is fake  # 다른 곳의 asyncio는 바꾸지 않는다


def test_hwpx_engine_call_hides_the_console_window(monkeypatch, tmp_path):
    from privacy_operator.writer import hwpx

    seen = {}
    monkeypatch.setattr(hwpx.subprocess, "run", lambda *a, **k: seen.update(k))
    hwpx._engine("validate", "x.hwpx", cwd=tmp_path)
    assert seen["creationflags"] == engine.NO_WINDOW


def test_background_validate_hides_the_window_and_reports_a_broken_file(monkeypatch, tmp_path):
    """한글 파일 구조 검사는 남은 값 검사와 동시에 뒤에서 돈다. 창을 띄우지 않고, 깨진 파일은 실패로 알린다."""
    import subprocess

    from privacy_operator.writer import hwpx

    seen = {}
    real = subprocess.Popen

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(hwpx.subprocess, "Popen", spy)
    broken = tmp_path / "broken.hwpx"
    broken.write_bytes(b"not a zip")
    pending = hwpx.PendingValidate(broken, tmp_path)
    assert seen["creationflags"] == engine.NO_WINDOW
    import pytest

    with pytest.raises(hwpx.HwpxWriteError):
        pending.finish()


def test_background_validate_can_be_cancelled(tmp_path):
    from privacy_operator.writer import hwpx

    pending = hwpx.PendingValidate(tmp_path / "없음.hwpx", tmp_path)
    pending.cancel()
    assert pending.proc.poll() is not None
