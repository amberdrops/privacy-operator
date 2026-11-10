"""9단계 AI 탐지 화면(pytest-qt). 가짜 Ollama 서버(test_ai_detect.FakeOllama)로 시험한다. 값은 모두 지어낸 것이다."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from test_ai_detect import FAMILY, PHRASE_FAMILY, PLAIN, FakeOllama, answer_with, doc
from test_ui_window import _small_texts

from privacy_operator.alias.vault import Vault
from privacy_operator.detect.ai.spans import is_ai
from privacy_operator.settings import Settings
from privacy_operator.ui.dialogs import SettingsDialog
from privacy_operator.ui.main_window import MainWindow
from privacy_operator.ui.theme import load_theme
from privacy_operator.ui.voice import Voice
from privacy_operator.workflow import MissionWorkflow

SHOTS = Path(__file__).resolve().parent / "screenshots"


@pytest.fixture
def ollama():
    fake = FakeOllama()
    yield fake
    fake.close()


@pytest.fixture
def win(qtbot, tmp_path, ollama, monkeypatch):
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path / "없는곳"))
    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"),
                        ollama_port=ollama.port)
    vault = Vault(settings.vault_path).create("window-password")
    wf = MissionWorkflow(settings, vault, reader_factory=lambda: None)
    w = MainWindow(wf, settings, None, Voice("plain"), load_theme("pink"))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    wf.add_results([(tmp_path / "연수계획.hwpx", doc(PLAIN, FAMILY, "담당자: 박서연 (010-2345-6789)"))])
    w.refresh()
    yield w
    wf.close()


def _enable(win, model="qwen3:4b"):
    win.settings.ai_ollama_model = model
    win.refresh()


def test_ai_button_hidden_until_enabled(win):
    assert not win.review.ai_btn.isVisible()
    _enable(win)
    assert win.review.ai_btn.isVisible() and win.review.ai_btn.isEnabled()


def test_ai_find_runs_in_background_and_lands_in_gray_zone(win, qtbot, ollama):
    def slow(blocks):
        time.sleep(0.4)
        return answer_with(PHRASE_FAMILY)(blocks)

    ollama.answer = slow
    _enable(win)
    with qtbot.waitSignal(win.ai_finished, timeout=30000):
        assert win.find_ai()
        assert win.guide.text().startswith("AI로 찾는 중")
        assert win.review.ai_btn.text() == "멈추기" and win.review.ai_btn.isEnabled()
        assert not win.confirm_btn.isEnabled() and not win.run_btn.isEnabled() and not win.add_btn.isEnabled()
    [f] = [g for g in win.workflow.findings if is_ai(g)]
    assert win.guide.text() == "AI가 새 후보 1건을 찾았습니다. 간접식별 후보 1건은 회색 영역에 있습니다."
    assert win.review.fold.isChecked() and f.finding_id in win.review.gray_proxy.visible_ids()
    assert win.review.ai_btn.text() == "정밀 탐지" and not win.review.ai_btn.isEnabled()  # 이 문서는 마쳤다
    qtbot.wait(100)  # 단추 글자가 바뀐 뒤 배치가 끝나게
    SHOTS.mkdir(exist_ok=True)
    win.grab().save(str(SHOTS / "14_ai_found_pink.png"))


def test_ai_error_is_reported_by_code(win, qtbot, ollama, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    _enable(win)
    ollama.close()
    with qtbot.waitSignal(win.ai_finished, timeout=30000):
        win.find_ai()
    assert shown == ["Ollama가 켜져 있지 않습니다."]
    assert win.review.ai_btn.isEnabled()  # 다시 해 볼 수 있다


def test_mission_voice_ai_texts(win, qtbot, ollama):
    ollama.answer = answer_with(PHRASE_FAMILY)
    win.apply_voice("mission")
    _enable(win)
    assert win.review.ai_btn.text() == "AI 정찰"
    with qtbot.waitSignal(win.ai_finished, timeout=30000):
        win.find_ai()
    assert win.guide.text().startswith("AI 정찰로 새 타깃 1건 발견.")


def test_settings_lists_only_local_models_and_saves_choice(qtbot, ollama, tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path / "없는곳"))
    s = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"), ollama_port=ollama.port)
    dlg = SettingsDialog(Voice("plain"), s, lambda: None)
    qtbot.addWidget(dlg)
    items = [dlg.ai_model.itemData(i) for i in range(dlg.ai_model.count())]
    assert items == ["", "qwen3:4b"]  # 클라우드 모델 셋은 목록에 없다
    assert dlg.ai_state.text().startswith("내 컴퓨터의 모델 1개")
    assert not dlg.ai_schift.isEnabled()  # Schift가 없으면 켤 수 없다
    dlg.ai_model.setCurrentIndex(1)
    dlg._save()
    assert dlg.result_settings.ai_ollama_model == "qwen3:4b" and not dlg.result_settings.ai_schift
    dlg.show()
    qtbot.waitExposed(dlg)
    assert not _small_texts(dlg)
    SHOTS.mkdir(exist_ok=True)
    dlg.grab().save(str(SHOTS / "15_settings_ai.png"))


def test_settings_keeps_choice_when_ollama_is_off_but_drops_cloud(qtbot, ollama, tmp_path):
    port = ollama.port
    ollama.close()
    base = dict(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"), ollama_port=port)
    dlg = SettingsDialog(Voice("plain"), Settings(**base, ai_ollama_model="qwen3:4b"), lambda: None)
    qtbot.addWidget(dlg)
    assert dlg.ai_state.text() == "Ollama가 켜져 있지 않습니다." and dlg.ai_model.currentData() == "qwen3:4b"
    dlg2 = SettingsDialog(Voice("plain"), Settings(**base, ai_ollama_model="gemma4:cloud"), lambda: None)
    qtbot.addWidget(dlg2)
    assert dlg2.ai_model.currentData() == ""  # 손으로 적어 넣은 클라우드 모델은 고른 것으로 두지 않는다
