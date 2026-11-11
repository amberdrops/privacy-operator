"""9단계 AI 탐지 화면(pytest-qt). 가짜 Schift 작업 프로세스(test_ai_detect.FAKE_WORKER)로 시험한다. 값은 모두 지어낸 것이다."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from test_ai_detect import FAKE_WORKER, FAMILY, PLAIN, doc
from test_ui_window import _small_texts

from privacy_operator import workflow as workflow_module
from privacy_operator.alias.vault import Vault
from privacy_operator.detect.ai.schift import SchiftEngine
from privacy_operator.detect.ai.spans import is_ai
from privacy_operator.settings import Settings
from privacy_operator.ui.dialogs import SettingsDialog
from privacy_operator.ui.main_window import MainWindow
from privacy_operator.ui.theme import load_theme
from privacy_operator.ui.voice import Voice
from privacy_operator.workflow import MissionWorkflow

SHOTS = Path(__file__).resolve().parent / "screenshots"
NAME_LINE = "이번 연수의 숨은 공로자는 박소윤이다."  # 가짜 작업 프로세스가 이름으로 찾는 문장


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """workflow가 쓰는 Schift를 가짜 작업 프로세스로 바꾼다. state["delay"]초 뒤에 시작한다."""
    state = {"delay": 0.0}
    worker = tmp_path / "worker_ok.py"
    worker.write_text(FAKE_WORKER.format(mode="ok"), encoding="utf-8")

    def make():
        time.sleep(state["delay"])
        return SchiftEngine(home=tmp_path, python=sys.executable, worker=worker)

    monkeypatch.setattr(workflow_module, "is_installed", lambda: True)
    monkeypatch.setattr(workflow_module, "SchiftEngine", make)
    return state


@pytest.fixture
def win(qtbot, tmp_path, fake_engine):
    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"))
    vault = Vault(settings.vault_path).create("window-password")
    wf = MissionWorkflow(settings, vault, reader_factory=lambda: None)
    w = MainWindow(wf, settings, None, Voice("plain"), load_theme("pink"))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    wf.add_results([(tmp_path / "연수계획.hwpx", doc(PLAIN, FAMILY, NAME_LINE))])
    w.refresh()
    yield w
    wf.close()


def _enable(win):
    win.settings.ai_schift = True
    win.refresh()


def test_ai_button_hidden_until_enabled(win):
    assert not win.review.ai_btn.isVisible()
    _enable(win)
    assert win.review.ai_btn.isVisible() and win.review.ai_btn.isEnabled()


def test_ai_find_runs_in_background_and_lands_in_review(win, qtbot, fake_engine):
    fake_engine["delay"] = 0.4
    _enable(win)
    with qtbot.waitSignal(win.ai_finished, timeout=30000):
        assert win.find_ai()
        assert win.guide.text().startswith("AI로 찾는 중")
        assert win.review.ai_btn.text() == "멈추기" and win.review.ai_btn.isEnabled()
        assert not win.confirm_btn.isEnabled() and not win.run_btn.isEnabled() and not win.add_btn.isEnabled()
    [f] = [g for g in win.workflow.findings if is_ai(g)]
    assert win.guide.text() == "AI가 새 후보 1건을 찾았습니다."
    assert f.finding_id in win.review.visible_ids()
    assert win.review.ai_btn.text() == "정밀 탐지" and not win.review.ai_btn.isEnabled()  # 이 문서는 마쳤다
    qtbot.wait(100)  # 단추 글자가 바뀐 뒤 배치가 끝나게
    SHOTS.mkdir(exist_ok=True)
    win.grab().save(str(SHOTS / "14_ai_found_pink.png"))


def test_ai_error_is_reported_by_code(win, qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(workflow_module, "is_installed", lambda: False)
    _enable(win)
    with qtbot.waitSignal(win.ai_finished, timeout=30000):
        win.find_ai()
    assert shown == ["schift-ko-pii가 설치되어 있지 않습니다."]
    assert win.review.ai_btn.isEnabled()  # 다시 해 볼 수 있다


def test_mission_voice_ai_texts(win, qtbot):
    win.apply_voice("mission")
    _enable(win)
    assert win.review.ai_btn.text() == "AI 정찰"
    with qtbot.waitSignal(win.ai_finished, timeout=30000):
        win.find_ai()
    assert win.guide.text().startswith("AI 정찰로 새 타깃 1건 발견.")


def test_settings_ai_section_without_schift_and_save(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path / "없는곳"))
    s = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"), ai_schift=True)
    dlg = SettingsDialog(Voice("plain"), s, lambda: None)
    qtbot.addWidget(dlg)
    assert not dlg.ai_schift.isEnabled() and not dlg.ai_schift.isChecked()  # Schift가 없으면 켤 수 없다
    assert not hasattr(dlg, "ai_model")
    dlg._save()
    assert not dlg.result_settings.ai_schift
    dlg.show()
    qtbot.waitExposed(dlg)
    assert not _small_texts(dlg)
    SHOTS.mkdir(exist_ok=True)
    dlg.grab().save(str(SHOTS / "15_settings_ai.png"))
