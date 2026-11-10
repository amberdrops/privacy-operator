"""프로그램 시작: 설정 읽기 → 금고 열기(처음이면 만들기) → 주 창."""

from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QElapsedTimer
from PySide6.QtWidgets import QApplication, QSplashScreen

from privacy_operator.alias.crypto import WrongPassword
from privacy_operator.settings import SETTINGS_FILE, VaultSession, config_dir, load_settings, save_settings
from privacy_operator.ui.dialogs import PasswordDialog
from privacy_operator.ui.main_window import MainWindow
from privacy_operator.ui.style import font, load_bundled_fonts, stylesheet
from privacy_operator.ui.theme import Theme, load_theme
from privacy_operator.ui.widgets import scaled_width
from privacy_operator.ui.voice import Voice
from privacy_operator.workflow import MissionWorkflow


def open_vault(session: VaultSession, voice: Voice):
    """기억해 둔 비밀번호가 있으면 쓰고, 없으면 묻는다. 금고가 없으면 만든다."""
    if not session.vault.is_initialized():
        dlg = PasswordDialog(voice, "create", session.create)
        return dlg.result_value if dlg.exec() == PasswordDialog.DialogCode.Accepted else None
    if session.remembered_password():
        try:
            return session.open()
        except WrongPassword:
            pass
    dlg = PasswordDialog(voice, "unlock", session.open)
    return dlg.result_value if dlg.exec() == PasswordDialog.DialogCode.Accepted else None


SPLASH_MS = 800
SPLASH_WIDTH = 720


def show_splash(theme: Theme, ms: int = SPLASH_MS, during=None) -> QSplashScreen | None:
    """시작 화면(핑크 테마에만 있다). 잠깐 보여 주고 닫는다. 금고 비밀번호 창보다 먼저 닫아 가리지 않게 한다.
    during: 시작 화면이 떠 있는 동안 할 일(부품 미리 불러오기). 시작 화면이 없으면 바로 한다."""
    path = theme.asset("splash")
    if not path:
        if during:
            during()
        return None
    splash = QSplashScreen(scaled_width(path, SPLASH_WIDTH))
    splash.show()
    QApplication.processEvents()
    clock = QElapsedTimer()
    clock.start()
    if during:
        during()  # 1초쯤. 시작 화면 시간과 겹쳐 켜지는 시간이 거의 늘지 않는다
    while clock.elapsed() < ms:
        QApplication.processEvents()
    splash.close()
    return splash


def preload_pdf_engine() -> None:
    """읽기, 작전 개시 스레드가 함수 안에서 늦게 불러오는 부품을 화면 스레드에서 미리 불러 둔다.
    C로 만든 부품(PyMuPDF, openpyxl이 끌어오는 numpy)을 다른 스레드에서 처음 불러오면 화면 스레드의
    메모리 정리와 겹쳐 프로그램이 꺼졌다(2026-11-08, PDF와 엑셀을 넣자마자 꺼짐).
    불러오는 쪽은 그대로 두고, 여기서 한 번 불러 두면 이미 불러온 것을 쓴다.
    함수 안에서 늦게 불러오는 부품을 새로 쓰면 여기에도 넣는다(tests/test_ui_window.py가 검사한다)."""
    try:  # numpy는 깔려 있을 때만 openpyxl이 끌어온다(개발용 묶음에만 있다)
        import numpy  # noqa: F401
    except ImportError:
        pass
    import openpyxl  # noqa: F401
    import pymupdf  # noqa: F401

    import privacy_operator.restore.zipdoc  # noqa: F401
    import privacy_operator.writer.hwpx  # noqa: F401
    import privacy_operator.writer.original  # noqa: F401
    import privacy_operator.writer.pdf  # noqa: F401
    import privacy_operator.writer.xlsx  # noqa: F401


def main(argv=None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    load_bundled_fonts()
    settings = load_settings()
    if not (config_dir() / SETTINGS_FILE).is_file():
        save_settings(settings)
    voice, theme = Voice(settings.voice), load_theme(settings.theme)
    app.setFont(font("body"))
    app.setStyleSheet(stylesheet(theme))
    show_splash(theme, during=preload_pdf_engine)

    session = VaultSession(settings)
    unlocked = open_vault(session, voice)
    if unlocked is None:
        return 0
    workflow = MissionWorkflow(settings, unlocked)
    window = MainWindow(workflow, settings, session, voice, theme)
    window.fit_to_screen()  # 화면보다 크면 최대화(작업 표시줄에 액션바가 가리지 않게)
    threading.Thread(target=workflow.warm_up, name="reader-warm-up", daemon=True).start()
    try:
        return app.exec()
    finally:
        workflow.close()
