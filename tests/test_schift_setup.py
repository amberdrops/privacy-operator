"""모델 설치(설정 창의 '모델 설치' 단추). 진짜 내려받기는 하지 않는다.

- 폴더 규칙(engine_home, setup_dir, is_installed, remove_model)
- ModelSetup: 가짜 설치 도구(JSON 줄을 쓰는 작은 스크립트)로 진행, 오류 코드, 취소
- engine/schift/install.py --embedded 의 1단계와 JSON 출력(pip과 내려받기는 바꿔 끼운다)
- 설정 창: 깔림, 도구 있음, 둘 다 없음 세 경우와 설치, 실패, 지우기. 스크린샷은 tests/screenshots/.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QScrollArea
from test_ui_window import _small_texts

from privacy_operator.detect.ai import schift
from privacy_operator.detect.ai.schift import (
    engine_home,
    engine_python,
    is_installed,
    remove_model,
    setup_available,
    setup_dir,
    worker_path,
)
from privacy_operator.detect.ai.schift_setup import CANCELLED, ModelSetup
from privacy_operator.settings import Settings
from privacy_operator.ui import dialogs
from privacy_operator.ui.dialogs import ModelNoticeDialog, ModelSetupDialog, SettingsDialog
from privacy_operator.ui.style import stylesheet
from privacy_operator.ui.theme import load_theme
from privacy_operator.ui.voice import Voice

SHOTS = Path(__file__).resolve().parent / "screenshots"
WIN = sys.platform == "win32"
EXE = "python.exe" if WIN else "bin/python"
VENV_EXE = "Scripts/python.exe" if WIN else "bin/python"
INSTALL_PY = Path(__file__).resolve().parents[1] / "engine" / "schift" / "install.py"


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def new_layout(home: Path, mark: bool = True) -> None:
    touch(home / "python" / EXE)
    (home / "hf_home").mkdir(parents=True, exist_ok=True)
    if mark:
        (home / "installed.json").write_text("{}", encoding="utf-8")


def old_layout(home: Path) -> None:
    touch(home / ".venv" / VENV_EXE)
    (home / "hf_home").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 폴더 규칙

def test_engine_home_source_default(monkeypatch):
    monkeypatch.delenv("PRIVACY_OPERATOR_SCHIFT_HOME", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert engine_home() == schift._ROOT / "engine" / "schift"
    assert setup_dir() == schift._ROOT / "engine" / "schift"


def test_engine_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert engine_home() == tmp_path


def test_engine_home_frozen_is_outside_app_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("PRIVACY_OPERATOR_SCHIFT_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(schift, "_ROOT", tmp_path / "app")
    assert engine_home() == tmp_path / "local" / "PrivacyOperator" / "schift"
    assert setup_dir() == tmp_path / "app" / "engine" / "schift-setup"  # 앱 폴더 안(판을 올릴 때 다시 깔린다)


def test_is_installed_new_layout_needs_mark(tmp_path):
    new_layout(tmp_path, mark=False)
    assert not is_installed(tmp_path)  # 받다 만 것
    (tmp_path / "installed.json").write_text("{}", encoding="utf-8")
    assert is_installed(tmp_path)
    assert engine_python(tmp_path) == tmp_path / "python" / EXE


def test_is_installed_old_layout(tmp_path):
    assert not is_installed(tmp_path)
    old_layout(tmp_path)
    assert is_installed(tmp_path)
    assert engine_python(tmp_path) == tmp_path / ".venv" / VENV_EXE


def test_engine_python_prefers_new_layout(tmp_path):
    old_layout(tmp_path)
    new_layout(tmp_path)
    assert engine_python(tmp_path) == tmp_path / "python" / EXE


def test_worker_path_source_and_frozen(monkeypatch, tmp_path):
    assert worker_path() == schift.WORKER and schift.WORKER.is_file()  # 소스: 옆 파일
    monkeypatch.setattr(schift, "WORKER", tmp_path / "없음.py")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(schift, "_ROOT", tmp_path)
    assert worker_path() == tmp_path / "engine" / "schift-setup" / "schift_worker.py"


def test_setup_available_needs_tool_python_and_pip(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(schift, "_ROOT", tmp_path)
    assert not setup_available()  # Store판 모양
    touch(tmp_path / "engine" / "schift" / "install.py")
    touch(tmp_path / "engine" / "pyembed" / ("python.exe" if WIN else "python"))
    assert not setup_available()
    touch(tmp_path / "engine" / "pyembed" / "pip.whl")
    assert setup_available()


def test_setup_available_frozen_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(schift, "_ROOT", tmp_path)
    base = tmp_path / "engine" / "schift-setup"
    touch(base / "install.py")
    touch(base / "python" / ("python.exe" if WIN else "python"))
    touch(base / "python" / "pip.whl")
    assert setup_available()


def test_remove_model_deletes_only_named_items(tmp_path):
    old_layout(tmp_path)
    new_layout(tmp_path)
    for name in ("install.py", "requirements.txt", "schift_worker.py"):
        touch(tmp_path / name)
    touch(tmp_path / "다른폴더" / "내파일.txt")
    assert remove_model(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["install.py", "requirements.txt", "schift_worker.py",
                                                         "다른폴더"]
    assert not is_installed(tmp_path)


def test_remove_model_removes_empty_user_folder(tmp_path):
    home = tmp_path / "schift"
    new_layout(home)
    assert remove_model(home)
    assert not home.exists()


# ---------------------------------------------------------------- ModelSetup (가짜 설치 도구)

FAKE_TOOL = textwrap.dedent("""
    import json, pathlib, sys, time
    mode, home = sys.argv[1], pathlib.Path(sys.argv[2])
    def emit(o):
        print(json.dumps(o), flush=True)
    keys = ["python", "runtime", "packages", "model", "verify"]
    for i, k in enumerate(keys, 1):
        emit({"step": i, "of": 5, "key": k})
        if mode == "slow":
            time.sleep(60)
        if mode.startswith("fail:") and i == 3:
            emit({"error": mode[5:]})
            sys.exit(1)
        time.sleep(0.02)
    if mode == "crash":
        sys.exit(3)
    exe = home / "python" / ("python.exe" if sys.platform == "win32" else "bin/python")
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    (home / "hf_home").mkdir(exist_ok=True)
    (home / "installed.json").write_text("{}")
    emit({"done": True})
""")


@pytest.fixture
def fake_tool(tmp_path):
    script = tmp_path / "fake_install.py"
    script.write_text(FAKE_TOOL, encoding="utf-8")
    home = tmp_path / "home"

    def make(mode: str = "ok") -> ModelSetup:
        return ModelSetup(home=home, command=[sys.executable, str(script), mode, str(home)])
    make.home = home
    return make


def test_setup_success_reports_steps(fake_tool):
    seen = []
    assert fake_tool("ok").run(lambda step, of, key: seen.append((step, of, key))) == ""
    assert seen == [(1, 5, "python"), (2, 5, "runtime"), (3, 5, "packages"), (4, 5, "model"), (5, 5, "verify")]
    assert is_installed(fake_tool.home)


@pytest.mark.parametrize("code", ["NO_SPACE", "DOWNLOAD_FAILED", "VERIFY_FAILED", "SETUP_BROKEN"])
def test_setup_error_code(fake_tool, code):
    assert fake_tool(f"fail:{code}").run() == code
    assert not is_installed(fake_tool.home)


def test_setup_without_error_line_is_broken(fake_tool):
    assert fake_tool("crash").run() == "SETUP_BROKEN"


def test_setup_cannot_start(tmp_path):
    assert ModelSetup(home=tmp_path, command=[str(tmp_path / "없는도구.exe")]).run() == "SETUP_BROKEN"


def test_setup_cancel_kills_tool(fake_tool):
    setup = fake_tool("slow")
    result, steps = [], []
    t = threading.Thread(target=lambda: result.append(setup.run(lambda *a: steps.append(a))))
    t.start()
    deadline = time.time() + 20
    while not steps and time.time() < deadline:
        time.sleep(0.05)
    assert steps
    start = time.time()
    setup.cancel()
    t.join(timeout=20)
    assert not t.is_alive() and time.time() - start < 15
    assert result == [CANCELLED]
    assert setup.proc.poll() is not None


def test_setup_cancel_before_start(fake_tool):
    setup = fake_tool("ok")
    setup.cancel()
    assert setup.run() == CANCELLED


# ---------------------------------------------------------------- install.py --embedded (통신 없이)

@pytest.fixture
def inst(monkeypatch):
    spec = importlib.util.spec_from_file_location("po_schift_install", INSTALL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.real_path_too_long = mod.path_too_long
    # pytest의 임시 폴더는 깊어서 경로 길이 검사에 걸릴 수 있다. 그 검사는 아래에서 따로 본다
    monkeypatch.setattr(mod, "path_too_long", lambda home, enabled=None: False)
    return mod


@pytest.mark.skipif(not WIN, reason="Windows 경로 길이 제한")
def test_path_too_long_only_when_deep_and_long_paths_off(inst):
    """실제 설치 위치(사용자 이름 20자)는 들어가고, 아주 깊은 폴더는 긴 경로 설정이 꺼져 있을 때만 걸린다."""
    real = Path("C:/Users") / ("가" * 20) / "AppData" / "Local" / "PrivacyOperator" / "schift"
    assert not inst.real_path_too_long(real, enabled=False)
    deep = Path("C:/Users/abc/AppData/Local/Temp") / ("d" * 40) / ("e" * 40) / "schift"
    assert inst.real_path_too_long(deep, enabled=False)
    assert not inst.real_path_too_long(deep, enabled=True)


def test_embedded_path_too_long_stops_before_anything(inst, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(inst, "path_too_long", lambda home, enabled=None: True)
    rc = inst.main_embedded(tmp_path / "emb", tmp_path / "home")
    assert rc == 1 and json.loads(capsys.readouterr().out.splitlines()[-1]) == {"error": "PATH_TOO_LONG"}
    assert not (tmp_path / "home").exists()


PTH = "python312.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n"


def make_embedded(folder: Path, pip: bool = True) -> Path:
    touch(folder / ("python.exe" if WIN else "python"))
    touch(folder / "python312.dll")
    (folder / "python312._pth").write_text(PTH, encoding="utf-8")
    if pip:
        touch(folder / "pip.whl")
    return folder


def test_fix_pth_enables_site_packages_once(inst, tmp_path):
    make_embedded(tmp_path)
    inst.fix_pth(tmp_path)
    inst.fix_pth(tmp_path)  # 다시 해도 같다
    lines = (tmp_path / "python312._pth").read_text(encoding="utf-8").splitlines()
    assert lines.count("Lib\\site-packages") == 1 and lines.count("import site") == 1
    assert "#import site" not in lines
    assert lines.index("Lib\\site-packages") < lines.index("import site")


def test_step_python_copies_and_skips(inst, tmp_path):
    emb = make_embedded(tmp_path / "emb")
    home = tmp_path / "home"
    py = inst.step_python(emb, home)
    assert py == home / "python" / EXE and py.is_file()
    assert not (home / "python" / "pip.whl").exists()  # 복사본에는 pip.whl이 필요 없다
    assert "Lib\\site-packages" in (home / "python" / "python312._pth").read_text(encoding="utf-8")
    keep = touch(home / "python" / "Lib" / "site-packages" / "x.txt")
    inst.step_python(emb, home)  # 이미 있으면 건너뛴다
    assert keep.is_file()


def test_step_python_broken_setup(inst, tmp_path):
    with pytest.raises(inst.SetupError) as e:
        inst.step_python(make_embedded(tmp_path / "a", pip=False), tmp_path / "h")
    assert e.value.code == "SETUP_BROKEN"
    with pytest.raises(inst.SetupError):
        inst.step_python(tmp_path / "없는폴더", tmp_path / "h")


def run_embedded(inst, tmp_path, capsys, monkeypatch, *, pip=None, verify=True, free=(10**12,)):
    emb = make_embedded(tmp_path / "emb")
    calls = []
    frees = iter(list(free) + [free[-1]] * 20)
    monkeypatch.setattr(inst, "free_bytes", lambda p: next(frees))
    monkeypatch.setattr(inst, "pip_install", pip or (lambda py, whl, *a: calls.append(a)))
    monkeypatch.setattr(inst, "download_model", lambda py, home: calls.append(("model",)))
    monkeypatch.setattr(inst, "verify_model", lambda py, home: verify)
    rc = inst.main_embedded(emb, tmp_path / "home")
    out = capsys.readouterr().out
    return rc, [json.loads(line) for line in out.splitlines()], calls


def test_embedded_success_output_is_json_lines(inst, tmp_path, capsys, monkeypatch):
    rc, lines, calls = run_embedded(inst, tmp_path, capsys, monkeypatch)
    assert rc == 0
    assert lines == [{"step": i, "of": 5, "key": k} for i, k in
                     enumerate(["python", "runtime", "packages", "model", "verify"], 1)] + [{"done": True}]
    assert calls[0] == ("--index-url", inst.TORCH_INDEX, inst.TORCH)
    assert calls[1][0] == "-r" and calls[2] == ("model",)
    assert json.loads((tmp_path / "home" / "installed.json").read_text(encoding="utf-8"))["revision"] \
        == inst.MODEL_REVISION


def test_embedded_no_space_at_start(inst, tmp_path, capsys, monkeypatch):
    rc, lines, _ = run_embedded(inst, tmp_path, capsys, monkeypatch, free=(10**8,))
    assert rc == 1 and lines == [{"error": "NO_SPACE"}]


def test_embedded_download_failed_then_no_space(inst, tmp_path, capsys, monkeypatch):
    def boom(py, whl, *a):
        raise subprocess.CalledProcessError(1, "pip")
    rc, lines, _ = run_embedded(inst, tmp_path, capsys, monkeypatch, pip=boom)
    assert rc == 1 and lines[-1] == {"error": "DOWNLOAD_FAILED"}
    assert not (tmp_path / "home" / "installed.json").exists()
    rc, lines, _ = run_embedded(inst, tmp_path, capsys, monkeypatch, pip=boom, free=(10**12, 10**6))
    assert lines[-1] == {"error": "NO_SPACE"}


def test_embedded_verify_failed(inst, tmp_path, capsys, monkeypatch):
    rc, lines, _ = run_embedded(inst, tmp_path, capsys, monkeypatch, verify=False)
    assert rc == 1 and lines[-1] == {"error": "VERIFY_FAILED"}
    assert not (tmp_path / "home" / "installed.json").exists()


def test_embedded_broken_setup(inst, tmp_path, capsys):
    rc = inst.main_embedded(tmp_path / "없는폴더", tmp_path / "home")
    assert rc == 1 and json.loads(capsys.readouterr().out.splitlines()[-1]) == {"error": "SETUP_BROKEN"}


# ---------------------------------------------------------------- 설정 창

@pytest.fixture
def ai_env(tmp_path, monkeypatch, fake_tool):
    """모델 폴더는 비어 있고 설치 도구는 있다. 설치를 누르면 가짜 도구가 돈다(mode["v"]: ok, fail:코드)."""
    home = fake_tool.home
    mode = {"v": "ok"}
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(home))
    monkeypatch.setattr(dialogs, "setup_available", lambda: True)
    monkeypatch.setattr(dialogs, "ModelSetup", lambda: fake_tool(mode["v"]))
    monkeypatch.setattr(ModelNoticeDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    QApplication.instance().setStyleSheet(stylesheet(load_theme("pink")))
    mode["home"] = home
    mode["fake_tool"] = fake_tool
    return mode


def settings_dialog(qtbot, tmp_path, voice="plain", **kw) -> SettingsDialog:
    s = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"), **kw)
    dlg = SettingsDialog(Voice(voice), s, lambda: None)
    qtbot.addWidget(dlg)
    return dlg


def texts(dlg) -> list[str]:
    return [w.text() for w in dlg.findChildren(QLabel) if w.isVisible()]


def test_settings_install_button_when_tool_present(qtbot, tmp_path, ai_env):
    dlg = settings_dialog(qtbot, tmp_path)
    v = Voice("plain")
    assert dlg.ai_schift is None and dlg.ai_install.text() == v.t("settings.ai_install")
    dlg.show()
    qtbot.waitExposed(dlg)
    assert v.t("settings.ai") in texts(dlg) and v.t("settings.ai_install_note") in texts(dlg)
    assert not _small_texts(dlg)
    SHOTS.mkdir(exist_ok=True)
    scroll = dlg.findChild(QScrollArea)
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum() // 2)  # AI 칸이 보이게(아래 정보 칸 위)
    qtbot.wait(100)
    dlg.grab().save(str(SHOTS / "16_settings_ai_install.png"))
    dlg._save()
    assert not dlg.result_settings.ai_schift


def test_settings_hidden_without_model_and_tool(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path / "없는곳"))
    monkeypatch.setattr(dialogs, "setup_available", lambda: False)  # Store판
    dlg = settings_dialog(qtbot, tmp_path, ai_schift=True)
    dlg.show()
    qtbot.waitExposed(dlg)
    v = Voice("plain")
    assert dlg.ai_schift is None and v.t("settings.ai") not in texts(dlg)
    assert not hasattr(dlg, "ai_install")


def test_settings_installed_shows_checkbox_and_remove(qtbot, tmp_path, ai_env):
    new_layout(ai_env["home"])
    dlg = settings_dialog(qtbot, tmp_path, ai_schift=True)
    assert dlg.ai_schift is not None and dlg.ai_schift.isChecked()
    assert dlg.ai_remove.text() == Voice("plain").t("settings.ai_remove")
    assert not hasattr(dlg, "ai_install")


def test_settings_install_success_turns_into_checked_box(qtbot, tmp_path, ai_env):
    dlg = settings_dialog(qtbot, tmp_path)
    dlg.show()
    qtbot.waitExposed(dlg)
    dlg.ai_install.click()
    qtbot.waitUntil(lambda: dlg.ai_schift is not None, timeout=30000)
    assert dlg.ai_schift.isChecked() and is_installed(ai_env["home"])
    assert dlg.ai_remove.isVisible()
    dlg._save()
    assert dlg.result_settings.ai_schift
    assert not _small_texts(dlg)


@pytest.mark.parametrize("voice", ["plain", "mission"])
@pytest.mark.parametrize("code", ["NO_SPACE", "DOWNLOAD_FAILED", "VERIFY_FAILED", "SETUP_BROKEN", "WEIRD"])
def test_settings_install_failure_message_and_retry(qtbot, tmp_path, ai_env, voice, code):
    ai_env["v"] = f"fail:{code}"
    dlg = settings_dialog(qtbot, tmp_path, voice=voice)
    v = Voice(voice)
    dlg.ai_install.click()
    qtbot.waitUntil(lambda: bool(dlg.ai_error.text()), timeout=30000)
    expect = v.t(f"settings.ai_err.{code}") if code != "WEIRD" else v.t("settings.ai_err.other", code=code)
    assert dlg.ai_error.text() == expect
    assert dlg.ai_schift is None and dlg.ai_install.isEnabled()  # 다시 누를 수 있다
    ai_env["v"] = "ok"
    dlg.ai_install.click()
    qtbot.waitUntil(lambda: dlg.ai_schift is not None, timeout=30000)


def test_settings_notice_cancel_does_not_install(qtbot, tmp_path, ai_env, monkeypatch):
    monkeypatch.setattr(ModelNoticeDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    dlg = settings_dialog(qtbot, tmp_path)
    dlg.ai_install.click()
    assert dlg.ai_schift is None and not is_installed(ai_env["home"])


def test_settings_remove_model(qtbot, tmp_path, ai_env, monkeypatch):
    home = ai_env["home"]
    old_layout(home)
    touch(home / "install.py")
    dlg = settings_dialog(qtbot, tmp_path, ai_schift=True)
    monkeypatch.setattr(dlg, "_confirm_remove", lambda: False)
    dlg.ai_remove.click()
    assert is_installed(home) and dlg.ai_schift is not None  # 아니오
    monkeypatch.setattr(dlg, "_confirm_remove", lambda: True)
    dlg.ai_remove.click()
    assert not is_installed(home) and (home / "install.py").is_file()
    assert dlg.ai_schift is None and dlg.ai_install.text() == Voice("plain").t("settings.ai_install")
    dlg._save()
    assert not dlg.result_settings.ai_schift  # 지우면 설정도 꺼진다


def test_settings_remove_hides_section_when_no_tool(qtbot, tmp_path, ai_env, monkeypatch):
    monkeypatch.setattr(dialogs, "setup_available", lambda: False)
    old_layout(ai_env["home"])
    dlg = settings_dialog(qtbot, tmp_path, ai_schift=True)
    monkeypatch.setattr(dlg, "_confirm_remove", lambda: True)
    dlg.show()
    qtbot.waitExposed(dlg)
    dlg.ai_remove.click()
    assert dlg.ai_schift is None and not dlg._ai_title.isVisible() and not dlg._ai_box.isVisible()


def test_notice_and_progress_dialogs(qtbot, tmp_path, ai_env):
    voice = Voice("mission")
    SHOTS.mkdir(exist_ok=True)
    notice = ModelNoticeDialog(voice)
    qtbot.addWidget(notice)
    notice.show()
    qtbot.waitExposed(notice)
    shown = texts(notice)
    assert voice.t("settings.ai_notice_text") in shown and voice.t("settings.ai_notice_license") in shown
    assert "Schift License v2.0" in voice.t("settings.ai_notice_license")
    assert not _small_texts(notice)
    notice.grab().save(str(SHOTS / "17_ai_notice.png"))

    dlg = ModelSetupDialog(voice, ai_env["fake_tool"]("slow"))
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    qtbot.waitUntil(lambda: "(1/5)" in dlg.text.text(), timeout=20000)
    assert dlg.text.text() == voice.t("settings.ai_setup_step", what=voice.t("settings.ai_step.python"), step=1, of=5)
    assert not _small_texts(dlg)
    dlg.grab().save(str(SHOTS / "18_ai_progress.png"))
    dlg.stop()
    qtbot.waitUntil(lambda: dlg.code is not None, timeout=30000)
    assert dlg.code == CANCELLED
