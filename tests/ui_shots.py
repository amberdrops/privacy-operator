"""화면 스크린샷 만들기. 배율을 바꿔 보려면 따로 실행한다(배율은 QApplication을 만들기 전에 정해야 한다).

    set QT_SCALE_FACTOR=1.5 && python tests/ui_shots.py 150

임시 폴더에 금고를 만들고 샘플(지어낸 값)로 화면을 채운다. 결과는 tests/screenshots/에 저장한다.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "tests" / "screenshots"
SAMPLES = ROOT / "samples"


def build(root: Path, theme: str = "plain", voice_name: str = "plain"):
    from privacy_operator.alias.vault import Vault
    from privacy_operator.settings import Settings
    from privacy_operator.ui.main_window import MainWindow
    from privacy_operator.ui.style import font, load_bundled_fonts, stylesheet
    from privacy_operator.ui.theme import load_theme
    from privacy_operator.ui.voice import Voice
    from privacy_operator.workflow import MissionWorkflow
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    load_bundled_fonts()
    settings = Settings(vault_dir=str(root / "금고"), workspace_dir=str(root / "작전실"), theme=theme,
                        voice=voice_name)
    vault = Vault(settings.vault_path)
    unlocked = vault.unlock("screenshot-password") if vault.is_initialized() else vault.create("screenshot-password")
    t = load_theme(theme)
    app.setFont(font("body"))
    app.setStyleSheet(stylesheet(t))
    wf = MissionWorkflow(settings, unlocked)
    win = MainWindow(wf, settings, None, Voice(voice_name), t)
    return win, wf


def save(widget, name: str, suffix: str) -> Path:
    from PySide6.QtWidgets import QApplication

    for _ in range(3):
        QApplication.processEvents()
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}_{suffix}.png"
    widget.grab().save(str(path))
    return path


def main(suffix: str = "100") -> list[Path]:
    from privacy_operator.mission.package import ProcessingRecord
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(sys.argv)
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        win, wf = build(root, "plain", "plain")
        win.show()
        out.append(save(win, "01_drop_plain", suffix))
        win.load_files([SAMPLES / "연수운영계획_샘플.hwpx", SAMPLES / "만족도조사_샘플.xlsx"])
        out.append(save(win, "02_review_plain", suffix))
        win.apply_theme("pink")
        win.apply_voice("mission")
        win.review.fold.setChecked(True)
        out.append(save(win, "03_review_pink_mission", suffix))
        win.review.fold.setChecked(False)
        win.review.group_btn.setChecked(True)
        out.append(save(win, "10_group_view_pink", suffix))
        win.review.group_btn.setChecked(False)
        from privacy_operator.reader.blocks import BlindSpot
        from privacy_operator.ui.dialogs import BlindSpotDialog

        wf.results[0].blind_spots = [BlindSpot("needs_ocr", page=2, detail="1"), BlindSpot("drawing", detail="3")]
        win.refresh()
        out.append(save(win, "11_blind_band_pink", suffix))
        dlg = BlindSpotDialog(win.voice, wf.results, win)
        dlg.show()
        out.append(save(dlg, "12_blind_dialog", suffix))
        dlg.close()
        from privacy_operator.ui.dialogs import SettingsDialog

        sdlg = SettingsDialog(win.voice, wf.settings, lambda: None, win)  # 높이가 화면을 넘지 않는지
        sdlg.show()
        out.append(save(sdlg, "13_settings_dialog", suffix))
        sdlg.close()
        wf.results[0].blind_spots = []
        win.select_all()
        win._judge(wf.confirm)
        out.append(save(win, "04_confirmed_pink", suffix))
        pending = [f.finding_id for f in wf.findings if f.review_status == "대기"]
        wf.exclude([f.finding_id for f in wf.findings if f.exclude_hint])
        wf.confirm(pending)
        win.refresh()
        out.append(save(win, "05_ready_pink", suffix))
        win.resize(win.minimumSize())
        out.append(save(win, "09_min_size_pink", suffix))
        win.resize(1440, 900)
        win.start_mission(ProcessingRecord("연수 결과 보고서 초안", "채팅형 AI", 30))
        win.effect.stop()  # 반짝임을 건너뛰면 송출 완료 창이 열린다
        out.append(save(win.done_dialog, "06_done_dialog", suffix))
        win.done_dialog.close()
        mid = wf.mission_id
        result = wf.settings.from_ai_path / mid / f"{mid}_결과보고서.md"
        result.write_text("[이름-01]는 이론을, 이름 2는 실습을 맡았다.", encoding="utf-8")
        win.apply_voice("plain")
        win.show_page(2)
        win.restore_page.run()
        out.append(save(win, "07_restore_plain", suffix))
        win.apply_theme("plain")
        out.append(save(win, "08_restore_plain_theme", suffix))
        win.close()
    return out


if __name__ == "__main__":
    for p in main(sys.argv[1] if len(sys.argv) > 1 else "100"):
        print(p.name)
