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


def test_validate_reports_a_broken_file(tmp_path):
    """한글 파일 구조 검사: 깨진 파일은 실패로 알린다."""
    import pytest

    from privacy_operator.writer import hwpx

    broken = tmp_path / "broken.hwpx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(hwpx.HwpxWriteError):
        hwpx.validate(broken, tmp_path)


def test_a_workbook_openpyxl_cannot_open_is_a_read_failure_not_a_crash(reader, monkeypatch):
    """메모와 숨긴 시트를 읽다 멈춘 엑셀(서식 표가 어긋난 파일)은 읽지 못한 문서로 돌린다.
    예외가 그대로 올라가면 화면의 읽기 스레드에서 프로그램이 꺼진다(2026-11-11)."""
    import pytest

    from privacy_operator.reader import read

    def broken(path, blocks):
        raise IndexError("list index out of range")

    monkeypatch.setattr(read, "xlsx_extras", broken)
    with pytest.raises(read.ReadError) as e:
        reader.read(Path("samples") / "만족도조사_샘플.xlsx")
    assert e.value.code == "EXTRAS_FAILED" and "샘플" not in str(e.value)
