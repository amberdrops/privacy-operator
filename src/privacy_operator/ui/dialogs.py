"""대화상자: 금고 비밀번호(만들기, 열기, 바꾸기), 설정, 개시 완료.

작전 개시 전 확인 창은 없앴다(2026-11-08). 처리 기록은 자동으로 남긴다(main_window.start_mission)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from privacy_operator.alias.crypto import MIN_PASSWORD_LENGTH, WrongPassword
from privacy_operator.detect.ai.schift import is_installed as schift_installed
from privacy_operator.detect.ai.schift import remove_model, setup_available
from privacy_operator.detect.ai.schift_setup import CANCELLED, ModelSetup
from privacy_operator import BETA, __version__
from privacy_operator.settings import PROJECT_ROOT, Settings, SettingsError, validate_paths
from privacy_operator.ui.theme import available_themes, load_theme
from privacy_operator.ui.review import SegButton
from privacy_operator.ui.voice import VOICES, Voice
from privacy_operator.ui.widgets import Binder


class _Dialog(QDialog):
    def __init__(self, voice: Voice, title_key: str, parent=None):
        super().__init__(parent)
        self.voice = voice
        self.binder = Binder(voice)
        self.binder.text(self, title_key, setter=self.setWindowTitle, tip=False)
        self.setMinimumWidth(560)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(28, 24, 28, 24)
        self.lay.setSpacing(14)

    def label(self, key: str, object_name: str = "", **values) -> QLabel:
        lab = QLabel()
        lab.setWordWrap(True)
        if object_name:
            lab.setObjectName(object_name)
        self.binder.text(lab, key, values=(lambda: values) if values else None, tip=False)
        return lab

    def button(self, key: str, primary: bool = False) -> QPushButton:
        b = QPushButton()
        if primary:
            b.setObjectName("Primary")
        self.binder.text(b, key)
        return b

    def buttons(self, ok_key: str, cancel_key: str) -> tuple[QPushButton, QPushButton]:
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = self.button(cancel_key), self.button(ok_key, primary=True)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.lay.addLayout(row)
        ok.setDefault(True)
        return ok, cancel


class PasswordDialog(_Dialog):
    """mode: create(두 번 입력), unlock(한 번), change(지금 + 새 비밀번호 두 번).

    submit(password) 또는 submit(current, new)가 WrongPassword를 내면 오류를 보여 주고 창을 닫지 않는다.
    """

    def __init__(self, voice: Voice, mode: str, submit: Callable, parent=None):
        titles = {"create": "pw.create_title", "unlock": "pw.unlock_title", "change": "pw.change_title"}
        super().__init__(voice, titles[mode], parent)
        self.mode, self.submit, self.result_value = mode, submit, None
        texts = {"create": "pw.create_text", "unlock": "pw.unlock_text", "change": "pw.change_text"}
        self.lay.addWidget(self.label(texts[mode]))
        form = QFormLayout()
        form.setSpacing(10)
        self.current = self._field() if mode == "change" else None
        self.first = self._field()
        self.second = self._field() if mode in ("create", "change") else None
        if self.current:
            form.addRow(self.label("pw.current"), self.current)
        form.addRow(self.label("pw.password"), self.first)
        if self.second:
            form.addRow(self.label("pw.repeat"), self.second)
        self.lay.addLayout(form)
        self.error = QLabel()
        self.error.setObjectName("Error")
        self.lay.addWidget(self.error)
        ok, _ = self.buttons("pw.ok", "pw.cancel")
        ok.clicked.connect(self._ok)

    @staticmethod
    def _field() -> QLineEdit:
        e = QLineEdit()
        e.setEchoMode(QLineEdit.EchoMode.Password)
        return e

    def _ok(self) -> None:
        pw = self.first.text()
        if self.mode != "unlock" and len(pw) < MIN_PASSWORD_LENGTH:
            self.error.setText(self.voice.t("pw.short"))
            return
        if self.second is not None and pw != self.second.text():
            self.error.setText(self.voice.t("pw.mismatch"))
            return
        try:
            self.result_value = self.submit(self.current.text(), pw) if self.current else self.submit(pw)
        except WrongPassword:
            self.error.setText(self.voice.t("pw.wrong"))
            return
        self.accept()


class PathField(QLabel):
    """폴더 경로 보기. 길면 가운데를 줄여(…) 앞쪽 드라이브와 끝 폴더 이름이 함께 보이게 한다. 전체 경로는 풍선 도움말."""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.setObjectName("PathField")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_path(path)

    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = path
        self.setToolTip(path)
        self._elide()

    def _elide(self) -> None:
        width = max(40, self.contentsRect().width())
        self.setText(self.fontMetrics().elidedText(self._path, Qt.TextElideMode.ElideMiddle, width))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def sizeHint(self):  # noqa: N802  글자 길이로 창이 넓어지지 않게
        return QSize(260, super().sizeHint().height())

    def minimumSizeHint(self):  # noqa: N802
        return QSize(120, super().minimumSizeHint().height())


class Choice(QWidget):
    """두세 가지 중 하나 고르기. 펼침 목록 대신 한 덩어리 칸(#Segment, #Seg)으로 보인다."""

    def __init__(self, options, current: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Segment")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(self)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)
        self.group = QButtonGroup(self)
        self.values = []
        for i, (text, value) in enumerate(options):
            b = SegButton(text)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setChecked(value == current)
            self.group.addButton(b, i)
            self.values.append(value)
            h.addWidget(b)
        if self.group.checkedId() < 0 and self.values:
            self.group.button(0).setChecked(True)

    def currentData(self) -> str:  # noqa: N802  펼침 목록과 같은 이름
        return self.values[max(0, self.group.checkedId())]


def license_file(root: Path = PROJECT_ROOT) -> Path:
    """설치판은 LICENSE.txt(메모장으로 열린다), 소스에서는 LICENSE."""
    txt = root / "LICENSE.txt"
    return txt if txt.is_file() else root / "LICENSE"


class SettingsDialog(_Dialog):
    """설정. 저장하면 새 Settings를 돌려준다(result_settings). 금고 위치는 금고를 연 뒤에는 바꾸지 않는다."""

    def __init__(self, voice: Voice, settings: Settings, change_password: Callable[[], None], parent=None):
        super().__init__(voice, "settings.title", parent)
        self.settings, self.result_settings = settings, None
        # 항목이 많아 낮은 화면(1080p 150%)에서는 내용이 스크롤된다. 저장, 취소 단추는 늘 보인다
        outer = self.lay
        scroll = QScrollArea()
        scroll.setObjectName("DialogBody")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("DialogBody")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(0, 0, 8, 0)
        self.lay.setSpacing(outer.spacing())
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        # 한 격자에 묶음(폴더, 금고, 화면, AI)별로. 이름 칸 너비가 같아 입력 칸이 한 세로줄에 선다(2026-11-08 정리)
        self.grid = grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid)
        self._section("settings.sec_folders")
        self.workspace = self._path_row("settings.workspace_dir", settings.workspace_dir)
        self.restored = self._path_row("settings.restored_dir", settings.restored_dir)
        self._section("settings.sec_vault")
        self.vault = self._path_row("settings.vault_dir", settings.vault_dir, editable=False)
        self.retention = QSpinBox()
        self.retention.setRange(1, 3650)
        self.retention.setValue(settings.retention_days)
        # 위아래 화살표 없이 숫자만: 화살표 자리 너비 0은 스타일시트(style.py)에서 준다.
        # NoButtons는 쓰지 않는다. 스타일시트와 함께 쓰면 글자 칸 너비가 1px로 잡혀 숫자가 안 보인다
        self.retention.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.retention.setFixedWidth(96)
        days = QHBoxLayout()
        days.setSpacing(8)
        days.addWidget(self.retention)
        days.addWidget(self.label("settings.days"))
        days.addStretch(1)
        self._row("settings.retention", days)
        self.remember = QCheckBox()
        self.binder.text(self.remember, "settings.remember", tip=False)
        self.remember.setChecked(settings.remember_password)
        self._row(None, self.remember)
        self.alias_continue = QCheckBox()
        self.binder.text(self.alias_continue, "settings.alias_continue", tip=False)
        self.alias_continue.setChecked(settings.alias_continue)
        self._row(None, self.alias_continue)
        pw = self.button("settings.change_password")
        pw.clicked.connect(change_password)
        self._row(None, pw, Qt.AlignmentFlag.AlignLeft)
        self._section("settings.sec_look")
        # 둘 중 하나 고르기는 펼침 목록 대신 칸 고르기(검토 보드의 전체, 대기, 확정 칸과 같은 모양)
        self.theme = self._choice("settings.theme",
                                  [(voice.t(load_theme(n).label_key), n) for n in available_themes()], settings.theme)
        self.voice_box = self._choice("settings.voice", [(voice.t(f"voice.{n}"), n) for n in VOICES], settings.voice)
        self._ai_section(settings)
        self._about_section()
        self.error = QLabel()
        self.error.setObjectName("Error")
        self.lay.addWidget(self.error)
        self.lay.addStretch(1)
        self.lay = outer
        ok, _ = self.buttons("settings.save", "settings.cancel")
        ok.clicked.connect(self._save)
        self._fit(body)

    def _fit(self, body: QWidget) -> None:
        """내용이 다 보이는 높이로 열되, 화면 높이의 90%를 넘지 않는다."""
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        margins = self.layout().contentsMargins()
        want = body.sizeHint().height() + 72 + margins.top() + margins.bottom() + self.layout().spacing()
        self.resize(max(self.minimumWidth(), body.sizeHint().width() + 60), min(want, int(screen.height() * 0.9)))

    def _about_section(self) -> None:
        """판과 라이선스. 화면이 있는 AGPL 프로그램은 저작권, 보증 없음, 같은 조건으로 나눠 줄 수 있음,
        라이선스 보는 법을 화면에서 알린다(AGPL-3.0 5조 d). 저작권은 화면 아래 띠에 있다."""
        self._section("settings.sec_about")
        self.version = QLabel(self.voice.t("settings.version_beta", v=__version__) if BETA else __version__)
        self._row("settings.version", self.version)
        self._row("settings.license", self.label("settings.license_note", "Guide"))
        self.license_btn = self.button("settings.license_view")
        self.license_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(license_file()))))
        self._row(None, self.license_btn, Qt.AlignmentFlag.AlignLeft)

    def _ai_section(self, settings: Settings) -> None:
        """AI 탐지(선택). 모델(schift-ko-pii)이 깔려 있으면 켜는 칸과 지우기, 아직 없고 설치 도구가 있으면 설치 단추.
        둘 다 없으면(Store판) 칸 전체를 두지 않는다. 켤 수 없는 칸은 두지 않는다."""
        self.ai_schift: QCheckBox | None = None
        self.ai_error: QLabel | None = None
        self._ai_title = self._ai_box = None
        self._ai_on = settings.ai_schift
        self._ai_widgets: list[QWidget] = []
        if not (schift_installed() or setup_available()):
            return
        self._ai_title = self._section("settings.ai")
        self._ai_box = QWidget()
        self._ai_lay = QVBoxLayout(self._ai_box)
        self._ai_lay.setContentsMargins(0, 0, 0, 0)
        self._ai_lay.setSpacing(10)
        self._row(None, self._ai_box)
        self._fill_ai()

    def _fill_ai(self) -> None:
        """AI 칸을 지금 상태(깔림, 설치 가능)에 맞게 다시 그린다."""
        if self.ai_schift is not None:
            self._ai_on = self.ai_schift.isChecked()
        gone = set(map(id, self._ai_widgets))
        self.binder.items = [i for i in self.binder.items if id(i[0]) not in gone]
        for w in self._ai_widgets:
            self._ai_lay.removeWidget(w)
            w.hide()
            w.deleteLater()
        self._ai_widgets, self.ai_schift, self.ai_error = [], None, None
        installed = schift_installed()
        shown = installed or setup_available()
        self._ai_title.setVisible(shown)
        self._ai_box.setVisible(shown)

        def add(w: QWidget, align=None) -> QWidget:
            self._ai_widgets.append(w)
            if align is None:
                self._ai_lay.addWidget(w)
            else:
                self._ai_lay.addWidget(w, 0, align)
            w.show()  # 이미 열린 창에 나중에 더한 칸도 보이게(부모에 붙인 뒤에)
            return w

        left = Qt.AlignmentFlag.AlignLeft
        if installed:
            add(self.label("settings.ai_note", "Guide"))
            self.ai_schift = QCheckBox()
            self.binder.text(self.ai_schift, "settings.ai_schift", tip=False)
            self.ai_schift.setChecked(self._ai_on)
            add(self.ai_schift)
            self.ai_remove = self.button("settings.ai_remove")
            self.ai_remove.clicked.connect(self._remove_model)
            add(self.ai_remove, left)
        elif setup_available():
            add(self.label("settings.ai_install_note", "Guide"))
            self.ai_install = self.button("settings.ai_install")
            self.ai_install.clicked.connect(self._install_model)
            add(self.ai_install, left)
            self.ai_error = QLabel()
            self.ai_error.setObjectName("Error")
            self.ai_error.setWordWrap(True)
            add(self.ai_error)
            self.ai_error.hide()  # 문구가 있을 때만 보인다

    def _install_model(self) -> None:
        """안내 창에서 계속하면 설치 도구를 돌린다(진행 창). 성공하면 칸이 켜진 체크 상자로 바뀐다."""
        if ModelNoticeDialog(self.voice, self).exec() != QDialog.DialogCode.Accepted:
            return
        self.ai_error.setText("")
        self.ai_error.hide()
        dlg = ModelSetupDialog(self.voice, ModelSetup(), self)
        dlg.exec()
        code = dlg.code
        dlg.deleteLater()  # 설정 창이 열려 있는 동안 쌓이지 않게
        if code == "":
            self._ai_on = True
            self._fill_ai()
        elif code and code != CANCELLED and self.ai_error is not None:
            key = f"settings.ai_err.{code}"
            self.ai_error.setText(self.voice.t(key) if key in self.voice.keys()
                                  else self.voice.t("settings.ai_err.other", code=code))
            self.ai_error.show()

    def _confirm_remove(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(self.voice.t("settings.ai"))
        box.setText(self.voice.t("settings.ai_remove_ask"))
        go = box.addButton(self.voice.t("settings.ai_remove_yes"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.voice.t("settings.ai_notice_cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is go

    def _remove_model(self) -> None:
        if not self._confirm_remove():
            return
        if not remove_model():
            QMessageBox.warning(self, self.voice.t("settings.ai"), self.voice.t("settings.ai_remove_fail"))
        self._ai_on = False  # 지우면 AI 설정도 끈다
        self.ai_schift.setChecked(False)
        self._fill_ai()

    # ---------------------------------------------------------------- 격자 줄

    def _section(self, key: str) -> QLabel:
        """묶음 제목. 첫 묶음이 아니면 위에 여백 줄을 둔다."""
        g = self.grid
        r = g.rowCount() if g.count() else 0
        if r:
            g.setRowMinimumHeight(r, 12)
            r += 1
        title = self.label(key, "PanelLabel")
        g.addWidget(title, r, 0, 1, 2)
        return title

    def _row(self, key, item, align=None) -> None:
        """이름(없으면 비움)과 칸 한 줄. 이름 없는 줄은 입력 칸 세로줄에서 시작한다."""
        g = self.grid
        r = g.rowCount()
        if key:
            g.addWidget(self.label(key), r, 0, Qt.AlignmentFlag.AlignVCenter)
        if isinstance(item, QHBoxLayout):
            g.addLayout(item, r, 1)
        elif align is not None:
            g.addWidget(item, r, 1, align)
        else:
            g.addWidget(item, r, 1)

    def _choice(self, key: str, options, current: str) -> "Choice":
        box = Choice(options, current)
        row = QHBoxLayout()
        row.addWidget(box)
        row.addStretch(1)
        self._row(key, row)
        return box

    def _path_row(self, key: str, value: str, editable: bool = True) -> "PathField":
        field = PathField(value)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(field, 1)
        if editable:
            browse = self.button("settings.browse")
            browse.clicked.connect(lambda: self._browse(field))
            row.addWidget(browse)
        self._row(key, row)
        return field

    def _browse(self, field: "PathField") -> None:
        folder = QFileDialog.getExistingDirectory(self, "", field.path())
        if folder:
            field.set_path(str(Path(folder)))

    def _save(self) -> None:
        new = replace(self.settings, workspace_dir=self.workspace.path(), restored_dir=self.restored.path(),
                      remember_password=self.remember.isChecked(), retention_days=self.retention.value(),
                      theme=self.theme.currentData(), voice=self.voice_box.currentData(),
                      alias_continue=self.alias_continue.isChecked(),
                      ai_schift=bool(self.ai_schift and self.ai_schift.isChecked()))  # 모델이 없으면 끈다
        try:
            validate_paths(new)
        except SettingsError as e:
            self.error.setText(str(e))
            return
        self.result_settings = new
        self.accept()


def blind_spot_lines(voice: Voice, results) -> list[str]:
    """사각지대 한 곳당 한 줄: '파일, 쪽: 이유'."""
    lines = []
    for res in results:
        for b in res.blind_spots:
            where = (voice.t("blind.where.page", file=res.source_name, page=b.page) if b.page
                     else voice.t("blind.where.file", file=res.source_name))
            if b.kind == "engine_warning":
                why = voice.t("blind.kind.engine_warning", code=b.detail)
            else:
                why = voice.t(f"blind.kind.{b.kind}", n=b.detail if b.detail.isdigit() else 1)
            lines.append(f"{where}: {why}")
    return lines


class ModelNoticeDialog(_Dialog):
    """모델 설치 전 안내: 내려받는 크기, 인터넷에 한 번 연결함, 모델 라이선스."""

    def __init__(self, voice: Voice, parent=None):
        super().__init__(voice, "settings.ai_notice_title", parent)
        self.lay.addWidget(self.label("settings.ai_notice_text"))
        self.lay.addWidget(self.label("settings.ai_notice_license", "Guide"))
        self.buttons("settings.ai_notice_go", "settings.ai_notice_cancel")


_LIVE_THREADS: set = set()


class SetupThread(QThread):
    """모델 설치 도구를 다른 스레드에서 돌린다(화면이 멈추지 않게). 끝나면 오류 코드(성공은 빈 글자)를 넘긴다."""

    progress = Signal(int, int, str)
    done = Signal(str)

    def __init__(self, setup: ModelSetup, parent=None):
        super().__init__(parent)
        self.setup = setup

    def run(self) -> None:
        self.done.emit(self.setup.run(self.progress.emit))


class ModelSetupDialog(_Dialog):
    """모델 설치 진행. 지금 단계 글, 끝을 모르는 진행 막대, 중지 단추. 끝나면 창이 스스로 닫히고 code에 결과가 남는다."""

    def __init__(self, voice: Voice, setup: ModelSetup, parent=None):
        super().__init__(voice, "settings.ai_setup_title", parent)
        self.setup, self.code, self._thread = setup, None, None
        self.setMinimumWidth(480)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.text = QLabel(voice.t("settings.ai_step.python"))
        self.text.setWordWrap(True)
        self.lay.addWidget(self.text)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        self.lay.addWidget(bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.stop_btn = self.button("settings.ai_setup_stop")
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.stop_btn)
        self.lay.addLayout(row)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._thread is None:
            self._thread = SetupThread(self.setup)  # 창과 따로 산다(창이 먼저 지워져도 스레드가 끝나야 지워진다)
            _LIVE_THREADS.add(self._thread)
            self._thread.finished.connect(lambda t=self._thread: _LIVE_THREADS.discard(t))
            self._thread.progress.connect(self._step)
            self._thread.done.connect(self._finished)
            self._thread.start()

    def _step(self, step: int, total: int, key: str) -> None:
        k = f"settings.ai_step.{key}"
        if k in self.voice.keys():
            self.text.setText(self.voice.t("settings.ai_setup_step", what=self.voice.t(k), step=step, of=total))

    def stop(self) -> None:
        self.stop_btn.setEnabled(False)
        self.setup.cancel()

    def _finished(self, code: str) -> None:
        self.code = code
        self._thread.wait()
        self.accept()

    def reject(self) -> None:  # Esc는 중지와 같다
        self.stop()


class BlindSpotDialog(_Dialog):
    """검사 못 한 영역(사각지대) 목록."""

    def __init__(self, voice: Voice, results, parent=None):
        super().__init__(voice, "blind.details", parent)
        self.setMinimumWidth(680)
        title = QLabel(voice.t("blind.title", term=voice.t("term.blind_spot")))
        title.setObjectName("Heading")
        self.lay.addWidget(title)
        self.items = QListWidget()
        self.items.setWordWrap(True)
        for line in blind_spot_lines(voice, results):
            self.items.addItem(line)
        self.lay.addWidget(self.items)
        self.lay.addWidget(self.label("blind.note", "Guide"))
        row = QHBoxLayout()
        row.addStretch(1)
        close = self.button("blind.close", primary=True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.lay.addLayout(row)


class BusyDialog(_Dialog):
    """작전 개시 중, 문서 읽는 중. 누르자마자 떠서 처리가 돌고 있다는 것을 보여 준다. 끝나면 창이 닫는다(닫기 단추 없음).
    문서 읽기는 `progress(done, total)`로 몇 개째인지 바꿔 보여 준다(`침투 중 2/4`)."""

    def __init__(self, voice: Voice, parent=None, title_key: str = "run.title", text_key: str = "run.working",
                 **values):
        super().__init__(voice, title_key, parent)
        self.voice, self.text_key = voice, text_key
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.text = QLabel(voice.t(text_key, **values))
        self.text.setWordWrap(True)
        self.lay.addWidget(self.text)
        bar = QProgressBar()
        bar.setRange(0, 0)  # 끝을 모르는 진행(움직이는 막대)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        self.lay.addWidget(bar)

    def progress(self, done: int, total: int) -> None:
        self.text.setText(self.voice.t(self.text_key, done=done, total=total))

    def reject(self) -> None:  # Esc로 닫히지 않게(처리는 멈출 수 없다)
        pass


class RunDoneDialog(_Dialog):
    def __init__(self, voice: Voice, mission_id: str, package: Path, rules_text: str, originals=(), parent=None):
        super().__init__(voice, "run.done_title", parent)
        self.rules_text, self.package = rules_text, package
        title = self.label("run.done_title", "Heading")
        self.lay.addWidget(title)
        # 글은 두 줄만: 할 일 한 줄, 왜(복원)와 작전 번호를 작게 한 줄. 학습 허용 안내는 작전 개시 창에 있다
        self.lay.addWidget(self.label("run.done_text"))
        self.lay.addWidget(self.label("run.rules_hint", "Guide"))
        # 작전 번호는 따로 한 줄에(결과물 이름 앞에 붙이고, 귀환할 때 찾는 번호라 눈에 띄게)
        self.lay.addWidget(self.label("run.mission_no", "MissionNo", mission=mission_id))
        made = [o for o in originals if o.ok]
        failed = [o for o in originals if not o.ok]
        if made:
            self.lay.addWidget(self.label("run.originals", n=len(made)))
        if any(o.source_name.lower().endswith(".hwp") for o in made):
            self.lay.addWidget(self.label("run.hwp_note", "Guide"))
        if failed:
            self.lay.addWidget(self.label("run.originals_failed", "Error",
                                          names=", ".join(o.source_name for o in failed)))
        self.copied = QLabel()
        self.copied.setObjectName("Guide")
        self.lay.addWidget(self.copied)
        row = QHBoxLayout()
        copy, open_folder, close = (self.button("run.copy_rules", primary=True), self.button("run.open_folder"),
                                    self.button("run.close"))
        copy.clicked.connect(self._copy)
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(package))))
        close.clicked.connect(self.accept)
        for b in (copy, open_folder):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        self.lay.addLayout(row)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.rules_text)
        self.copied.setText(self.voice.t("run.copied"))
