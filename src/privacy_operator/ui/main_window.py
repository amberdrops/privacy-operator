"""주 창(docs/02_디자인명세.md '화면 구조').

    베젤 ─ 작전 바(72) ─ 사각지대 띠(44, 있을 때만) ─ [작전 패널(252) | 가운데 화면] ─ 액션바(72)

가운데 화면은 불러오기, 검토 보드, 복원 셋 중 하나다. 장식은 가장자리 띠에만, 가운데는 밝은 종이.
"""

from __future__ import annotations

import gc
import logging
import threading
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from privacy_operator.alias.vault import Vault
from privacy_operator.detect.finding import STATUS_CONFIRMED, STATUS_EXCLUDED, STATUS_PENDING, TIER_REVIEW
from privacy_operator.mission.package import DEFAULT_PURPOSE, ProcessingRecord
from privacy_operator.reader.read import SUPPORTED
from privacy_operator.settings import Settings, VaultSession, save_settings
from privacy_operator.ui import icons
from privacy_operator.ui.dialogs import BlindSpotDialog, BusyDialog, PasswordDialog, RunDoneDialog, SettingsDialog
from privacy_operator.ui.restore_page import RestorePage
from privacy_operator.ui.review import ReviewPage
from privacy_operator.ui.style import font, smooth_pixmap, stylesheet
from privacy_operator.ui.theme import Theme, load_theme
from privacy_operator.ui.voice import Voice
from privacy_operator.ui.widgets import (
    Bezel,
    Binder,
    FlashOverlay,
    ProgressRing,
    SparkleOverlay,
    StepBar,
    scaled_asset,
)
from privacy_operator.workflow import STEPS, MissionWorkflow, is_reference
from privacy_operator.writer.blocklist import BlocklistHit

DROP, REVIEW, RESTORE = range(3)
COUNT_ROLE = Qt.ItemDataRole.UserRole + 1  # 유형 목록 줄의 개수


TITLE_BAR = 40  # 창 제목 표시줄과 테두리 몫(대략)
EMBLEM_DIAL_PX = 44  # 단정한 모니터 엠블럼(금고 다이얼). 작전 바 높이 72px 안


def needs_maximize(window: QSize, available: QSize) -> bool:
    """창을 그 크기로 열면 화면(작업 표시줄 뺀 곳) 밖으로 나가는가."""
    return window.width() > available.width() or window.height() + TITLE_BAR > available.height()


def expand_folders(paths: list[Path]) -> list[Path]:
    """끌어다 놓은 것 중 폴더는 그 안(하위 폴더 포함)의 HWP, HWPX, PDF, XLSX로 펼친다.
    한글, 엑셀이 열려 있을 때 생기는 잠금 파일(~$로 시작)은 뺀다."""
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in SUPPORTED
                          and not f.name.startswith("~$"))
        else:
            out.append(p)
    return list(dict.fromkeys(out))


class TypeListDelegate(QStyledItemDelegate):
    """유형 목록 한 줄: 보석(없으면 색 점), 이름, 오른쪽 끝에 개수. 고른 줄은 연한 강조로 채우고 굵게."""

    HEIGHT = 32  # 유형이 많아도 스크롤이 덜 생기게(줄 바탕 28px)

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self.window = window

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(option.rect.width(), self.HEIGHT)

    def paint(self, p: QPainter, option, index) -> None:
        t = self.window.theme
        r = QRectF(option.rect).adjusted(0, 2, 0, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if selected or option.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["row_selected"] if selected else t["paper_head"]))
            p.drawRoundedRect(r, 10, 10)
        cat = index.data(Qt.ItemDataRole.UserRole)
        dot = QRectF(r.left() + 14, r.center().y() - 8, 16, 16)
        if cat is not None:
            gem = t.gem(cat)
            if gem:
                pm = smooth_pixmap(gem, round(16 * p.device().devicePixelRatioF()))
                p.drawPixmap(dot, pm, QRectF(pm.rect()))
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.type_color(cat)))
                p.drawEllipse(dot.adjusted(3, 3, -3, -3))
        name_font = font("body")
        if selected:
            name_font.setWeight(QFont.Weight.Bold)
        count = str(index.data(COUNT_ROLE) or 0)
        p.setFont(font("body"))
        count_w = p.fontMetrics().horizontalAdvance(count)
        p.setPen(QColor(t["ink_sub"]))
        p.drawText(r.adjusted(0, 0, -14, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, count)
        p.setFont(name_font)
        p.setPen(QColor(t["ink"]))
        text_r = QRectF(dot.right() + 10, r.top(), r.right() - 14 - count_w - 10 - dot.right() - 10, r.height())
        name = p.fontMetrics().elidedText(index.data(), Qt.TextElideMode.ElideRight, int(text_r.width()))
        p.drawText(text_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        p.restore()


class DropArea(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropArea")
        self.setProperty("hover", False)


log = logging.getLogger(__name__)

_gc_lock = threading.Lock()
_gc_holds = 0


@contextmanager
def worker_gc_pause():
    """작업 스레드가 도는 동안 자동 메모리 정리(순환 참조 수거)를 멈춘다. 끝나면 다시 켠다.

    PyMuPDF는 여러 스레드에서 동시에 쓰면 안 된다. 화면 스레드가 메모리를 정리하다 작업 스레드가 쓰는 PDF 객체를
    건드려 프로그램이 꺼졌다(2026-11-08, PDF 읽기와 작전 개시 중 세 번). 멈추는 동안 쌓인 것은 다시 켠 뒤 정리된다.
    """
    global _gc_holds
    with _gc_lock:
        _gc_holds += 1
        gc.disable()
    try:
        yield
    finally:
        with _gc_lock:
            _gc_holds -= 1
            if _gc_holds == 0:
                gc.enable()


class ReaderThread(QThread):
    """문서 읽기를 다른 스레드에서. 작전 상태는 바꾸지 않고, 다 읽으면 결과만 넘긴다(add_results는 창에서)."""

    progress = Signal(int, int)
    done = Signal(object, object)

    def __init__(self, workflow: MissionWorkflow, paths: list[Path], parent=None):
        super().__init__(parent)
        self.workflow, self.paths = workflow, paths

    def run(self) -> None:
        with worker_gc_pause():
            read, failed = self.workflow.read_files(self.paths, progress=self.progress.emit)
        self.done.emit(read, failed)


class AiThread(QThread):
    """AI로 더 찾기를 다른 스레드에서. 그동안 판정은 막는다. 다 찾으면 결과만 넘긴다(add_ai는 창에서)."""

    progress = Signal(int, int)
    done = Signal(object)

    def __init__(self, workflow: MissionWorkflow, parent=None):
        super().__init__(parent)
        self.workflow = workflow
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True

    def run(self) -> None:
        with worker_gc_pause():
            found = self.workflow.find_ai(progress=self.progress.emit, cancelled=lambda: self.stopped)
        self.done.emit(found)


class StartThread(QThread):
    """작전 개시(가명처리, 패키지 쓰기, 원본 형식 사본)를 다른 스레드에서. 결과나 오류만 넘긴다."""

    done = Signal(object, object)  # (패키지 경로, 오류)

    def __init__(self, workflow: MissionWorkflow, record: ProcessingRecord, parent=None):
        super().__init__(parent)
        self.workflow, self.record = workflow, record

    def run(self) -> None:
        try:
            with worker_gc_pause():
                package = self.workflow.start(self.record)
        except BaseException as e:  # noqa: BLE001  창에서 오류 종류별로 알린다
            self.done.emit(None, e)
        else:
            self.done.emit(package, None)


class MainWindow(QMainWindow):
    loaded = Signal()
    ai_finished = Signal()

    def __init__(self, workflow: MissionWorkflow, settings: Settings, session: VaultSession | None,
                 voice: Voice, theme: Theme):
        super().__init__()
        self.workflow, self.settings, self.session = workflow, settings, session
        self.voice, self.theme = voice, theme
        self.binder = Binder(voice)
        self.filter: set[str] | None = None
        self.notice: str | None = None  # 판정 직후 한 번 보여 줄 안내(재검색 결과 등)
        self.loader: ReaderThread | None = None
        self.ai: AiThread | None = None
        self.starter: StartThread | None = None  # 작전 개시 중
        self.busy_dialog: BusyDialog | None = None
        self.load_dialog: BusyDialog | None = None
        self.effect: QWidget | None = None  # 작전 개시 반짝임
        self.setAcceptDrops(True)
        self.resize(1440, 900)
        self.setMinimumSize(1100, 680)  # 1080p 화면 150% 배율에서도 들어가게
        self.binder.text(self, "app.title", setter=self.setWindowTitle, tip=False)

        screen = QWidget()
        screen.setObjectName("Screen")
        col = QVBoxLayout(screen)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._operation_bar())
        col.addWidget(self._blind_band())
        mid = QHBoxLayout()
        mid.setSpacing(0)
        mid.addWidget(self._side_panel())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._drop_page())
        self.review = ReviewPage(workflow, voice, theme, self.binder)
        self.review.model.changed.connect(self._update_actions)
        self.review.set_verdict_handler(self._toggle_verdict)
        self.review.ai_btn.clicked.connect(self.find_ai)
        self.stack.addWidget(self.review)
        self.restore_page = RestorePage(workflow, voice, self.binder)
        self.stack.addWidget(self.restore_page)
        mid.addWidget(self.stack, 1)
        col.addLayout(mid, 1)
        col.addWidget(self._action_bar())

        self.bezel = Bezel(theme, voice.t("app.bezel"))
        self.bezel.setScreen(screen)
        self.setCentralWidget(self.bezel)
        self.apply_theme(theme.name)
        self.refresh()

    def fit_to_screen(self) -> None:
        """창(제목 표시줄 포함)이 화면에서 작업 표시줄을 뺀 곳보다 크면 최대화로 연다.
        125% 배율의 노트북(약 1536x816)에서 1440x900 창은 아래 액션바가 작업 표시줄에 가려졌다."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None and needs_maximize(self.size(), screen.availableGeometry().size()):
            self.showMaximized()
        else:
            self.show()

    # ---------------------------------------------------------------- 띠와 패널

    def _operation_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("OperationBar")
        bar.setFixedHeight(72)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(14)
        self.emblem = QLabel()
        h.addWidget(self.emblem)
        texts = QVBoxLayout()
        texts.setSpacing(2)  # 제목과 집계를 붙여 한 덩어리로, 띠 세로 가운데에(위아래 늘림)
        texts.addStretch(1)
        self.title = QLabel()
        self.title.setObjectName("BarTitle")
        self.counts = QLabel()
        self.counts.setObjectName("BarSub")
        self.binder.text(self.title, "bar.no_mission", setter=self._set_title, tip=False)
        self.binder.text(self.counts, "bar.counts", values=self._count_values, tip=False)
        texts.addWidget(self.title)
        texts.addWidget(self.counts)
        texts.addStretch(1)
        h.addLayout(texts)
        h.addStretch(1)
        self.steps = StepBar(self.theme, self.binder, [f"step.{s}" for s in STEPS])
        h.addWidget(self.steps)
        h.addSpacing(10)
        self.offline_dot = QLabel()
        self.offline = QLabel()
        self.offline.setObjectName("Offline")
        self.binder.text(self.offline, "bar.offline", tip=False)
        h.addWidget(self.offline_dot)
        h.addWidget(self.offline)
        return bar

    def _blind_band(self) -> QWidget:
        self.blind = QWidget()
        self.blind.setObjectName("BlindBand")
        self.blind.setFixedHeight(52)  # 명세는 44지만 안의 버튼이 44 이상이어야 해서
        h = QHBoxLayout(self.blind)
        h.setContentsMargins(20, 0, 20, 0)
        self.blind_icon = QLabel()
        self.blind_text = QLabel()
        self.blind_text.setObjectName("BlindText")
        self.binder.text(self.blind_text, "blind.band", tip=False,
                         values=lambda: {"term": self.voice.t("term.blind_spot"),
                                         "n": len(self.workflow.blind_spots())})
        h.addWidget(self.blind_icon)
        h.addWidget(self.blind_text, 1)
        self.blind_btn = self.binder.text(QPushButton(), "blind.details", tip=False)
        self.blind_btn.clicked.connect(self.show_blind_spots)
        h.addWidget(self.blind_btn)
        return self.blind

    def _side_panel(self) -> QWidget:
        """작전 패널(너비 252). 창이 낮으면 패널 안이 스크롤된다."""
        scroll = QScrollArea()
        scroll.setObjectName("SidePanel")
        scroll.setFixedWidth(252)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel = QWidget()
        panel.setObjectName("SidePanelBody")
        scroll.setWidget(panel)
        v = QVBoxLayout(panel)
        v.setContentsMargins(14, 18, 14, 18)
        v.setSpacing(4)
        # 이동 목록: 상자 없는 줄. 지금 화면만 연한 강조로 채운다
        self.nav = {}
        for page, key in ((REVIEW, "nav.review"), (RESTORE, "nav.restore")):
            b = self.binder.text(QPushButton(), key)
            b.setObjectName("Nav")
            b.clicked.connect(lambda _=False, p=page: self.show_page(p))
            self.nav[page] = b
            v.addWidget(b)
        self.add_btn = self.binder.text(QPushButton(), "action.add_files")
        self.add_btn.setObjectName("Nav")
        self.add_btn.clicked.connect(self.pick_files)
        v.addWidget(self.add_btn)
        rule = QFrame()
        rule.setObjectName("PanelRule")
        rule.setFixedHeight(1)
        v.addSpacing(12)
        v.addWidget(rule)
        v.addSpacing(12)
        label = QLabel()
        label.setObjectName("PanelLabel")
        self.binder.text(label, "panel.progress", tip=False)
        v.addWidget(label)
        self.ring = ProgressRing(self.theme)
        v.addSpacing(6)
        v.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(10)
        flabel = QLabel()
        flabel.setObjectName("PanelLabel")
        self.binder.text(flabel, "panel.filter", tip=False)
        v.addWidget(flabel)
        self.filters = QListWidget()
        self.filters.setMinimumHeight(120)
        self.filters.setItemDelegate(TypeListDelegate(self))
        self.filters.setMouseTracking(True)
        self.filters.itemClicked.connect(self._filter_clicked)
        v.addWidget(self.filters, 1)
        vault_row = QHBoxLayout()
        vault_row.setContentsMargins(6, 0, 0, 0)
        self.lock = QLabel()
        self.vault_state = QLabel()
        self.vault_state.setObjectName("VaultState")
        self.binder.text(self.vault_state, "panel.vault_open")
        self.settings_btn = self.binder.text(QPushButton(), "nav.settings")
        self.settings_btn.setObjectName("TextButton")
        self.settings_btn.clicked.connect(self.open_settings)
        vault_row.addWidget(self.lock)
        vault_row.addWidget(self.vault_state, 1)
        vault_row.addWidget(self.settings_btn)
        v.addLayout(vault_row)
        return scroll

    def _drop_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DropPage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(48, 48, 48, 48)
        self.drop_area = DropArea()
        inner = QVBoxLayout(self.drop_area)
        inner.setSpacing(14)
        inner.addStretch(1)
        title, hint = QLabel(), QLabel()
        title.setObjectName("DropTitle")
        hint.setObjectName("DropHint")
        for w in (title, hint):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.binder.text(title, "drop.title", tip=False)
        self.binder.text(hint, "drop.hint", tip=False)
        pick = self.binder.text(QPushButton(), "drop.button")
        pick.setObjectName("Primary")
        pick.clicked.connect(self.pick_files)
        inner.addWidget(title)
        inner.addWidget(hint)
        inner.addWidget(pick, 0, Qt.AlignmentFlag.AlignHCenter)
        inner.addStretch(1)
        outer.addWidget(self.drop_area)
        return page

    def _action_bar(self) -> QWidget:
        self.actions = QWidget()
        self.actions.setObjectName("ActionBar")
        self.actions.setFixedHeight(72)
        h = QHBoxLayout(self.actions)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(10)
        self.selected = QLabel()
        self.selected.setObjectName("BarSub")
        self.binder.text(self.selected, "action_bar.selected", tip=False,
                         values=lambda: {"n": len(self.review.model.checked)})
        h.addWidget(self.selected)
        self.confirm_btn = self.binder.text(QPushButton(), "action.confirm")
        self.exclude_btn = self.binder.text(QPushButton(), "action.exclude")
        # 모두 고르기는 표 머리 체크박스다. 선택 해제는 고른 것이 있을 때만 글자 단추로 보인다
        self.all_btn = self.review.all_check.box
        self.none_btn = self.binder.text(QPushButton(), "action.select_none")
        self.none_btn.setObjectName("TextButton")
        self.raise_btn = self.binder.text(QPushButton(), "action.raise")
        self.lower_btn = self.binder.text(QPushButton(), "action.lower")
        self.unjudge_btn = self.binder.text(QPushButton(), "action.unjudge")
        self.unjudge_btn.clicked.connect(lambda: self._judge(self.workflow.unjudge))
        self.confirm_btn.clicked.connect(lambda: self._judge(self.workflow.confirm))
        self.exclude_btn.clicked.connect(lambda: self._judge(self.workflow.exclude))
        self.raise_btn.clicked.connect(lambda: self._judge(self.workflow.raise_reference))
        self.lower_btn.clicked.connect(lambda: self._judge(self.workflow.lower_to_reference))
        self.all_btn.clicked.connect(self._all_clicked)
        self.review.gray_check.box.clicked.connect(self._gray_all_clicked)
        self.none_btn.clicked.connect(lambda: self.review.model.set_checked(list(self.review.model.checked), False))
        for b in (self.confirm_btn, self.exclude_btn, self.unjudge_btn, self.raise_btn, self.lower_btn, self.none_btn):
            h.addWidget(b)
        h.addStretch(1)
        self.guide = QLabel()
        self.guide.setObjectName("Guide")
        self.binder.text(self.guide, "guide.empty", setter=self._set_guide, tip=False)
        h.addWidget(self.guide)
        self.run_btn = self.binder.text(QPushButton(), "action.run")
        self.run_btn.setObjectName("Primary")
        self.run_btn.clicked.connect(self.start_mission_async)  # 누르자마자 처리 중 창(2026-11-08)
        h.addWidget(self.run_btn)
        return self.actions

    # ---------------------------------------------------------------- 상태 반영

    def _count_values(self) -> dict:
        c = self.workflow.counts()
        return {"total": c.total, "pending": c.pending, "confirmed": c.confirmed, "excluded": c.excluded}

    def _set_title(self, _text: str) -> None:
        mission = self.workflow.mission_id or self.voice.t("bar.no_mission")
        names = [p.name for p in self.workflow.paths]
        files = names[0] if len(names) == 1 else self.voice.t("bar.files", n=len(names)) if names else ""
        self.title.setText(f"{mission}   {files}".strip())

    def _set_guide(self, _text: str) -> None:
        wf = self.workflow
        if self.loader is not None or self.ai is not None or self.starter is not None:
            return  # 읽거나 AI로 찾거나 개시하는 동안은 진행 글자를 그대로 둔다
        if self.notice:
            self.guide.setText(self.notice)
        elif wf.package_path:
            self.guide.setText(self.voice.t("guide.sent"))
        elif not wf.results:
            self.guide.setText(self.voice.t("guide.empty"))
        elif wf.counts().pending:
            self.guide.setText(self.voice.t("guide.pending", n=wf.counts().pending))
        else:
            self.guide.setText(self.voice.t("guide.ready"))

    def refresh(self) -> None:
        self.review.reload()
        for w in (self.title, self.counts, self.guide, self.blind_text, self.selected):
            self.binder.refresh(w)
        c = self.workflow.counts()
        judged = c.confirmed + c.excluded
        self.ring.set_value(judged, judged + c.pending, self.theme)
        self._update_steps()
        self.blind.setVisible(bool(self.workflow.blind_spots()))
        self._fill_filters()
        if self.stack.currentIndex() != RESTORE:
            self.stack.setCurrentIndex(REVIEW if self.workflow.results else DROP)
        self._update_nav()
        self._update_actions()

    def _update_actions(self) -> None:
        checked = self.workflow.by_id(self.review.model.checked)
        busy = self.loader is not None or self.ai is not None or self.starter is not None
        on_review = self.stack.currentIndex() == REVIEW and not self.workflow.mission_id and not busy
        any_main = any(not is_reference(f) for f in checked)
        self.confirm_btn.setEnabled(on_review and any_main)
        self.exclude_btn.setEnabled(on_review and any_main)
        # 올리기, 내리기, 판정 취소는 해당하는 항목을 골랐을 때만 보인다
        self.unjudge_btn.setVisible(on_review and any(f.review_status in (STATUS_CONFIRMED, STATUS_EXCLUDED)
                                                      for f in checked))
        self.raise_btn.setVisible(on_review and any(is_reference(f) for f in checked))
        self.lower_btn.setVisible(on_review and any(f.tier == TIER_REVIEW and f.review_status == STATUS_PENDING
                                                    for f in checked))
        self.all_btn.setEnabled(on_review)
        self.review.gray_check.box.setEnabled(on_review)
        self.none_btn.setVisible(on_review and bool(checked))
        self.review.sync_all_check()
        self.run_btn.setEnabled(self.workflow.can_start() and not busy)
        self.add_btn.setEnabled(not busy)
        ai_btn = self.review.ai_btn
        ai_btn.setVisible(self.workflow.ai_enabled() and bool(self.workflow.results) and not self.workflow.mission_id)
        ai_btn.setEnabled(self.ai is not None or (self.workflow.can_find_ai() and self.loader is None))
        self.binder.refresh(self.selected)
        self.actions.setVisible(self.stack.currentIndex() != RESTORE)

    def _update_steps(self) -> None:
        on_restore = self.stack.currentIndex() == RESTORE
        self.steps.set_current(STEPS.index("restore") if on_restore else self.workflow.step(), self.theme)

    def _update_nav(self) -> None:
        for page, b in self.nav.items():
            b.setProperty("current", self.stack.currentIndex() == page)
            b.style().unpolish(b)
            b.style().polish(b)
        self.nav[REVIEW].setEnabled(bool(self.workflow.results) or self.stack.currentIndex() == RESTORE)

    def _fill_filters(self) -> None:
        self.filters.clear()
        counts = Counter(f.category for f in self.workflow.findings)
        all_item = QListWidgetItem(self.voice.t("panel.all"))
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        all_item.setData(COUNT_ROLE, sum(counts.values()))
        self.filters.addItem(all_item)
        for cat, n in counts.most_common():
            item = QListWidgetItem(cat)
            item.setData(Qt.ItemDataRole.UserRole, cat)
            item.setData(COUNT_ROLE, n)
            self.filters.addItem(item)
        current = 0 if self.filter is None else next(
            (i for i in range(self.filters.count()) if self.filters.item(i).data(Qt.ItemDataRole.UserRole)
             in self.filter), 0)
        self.filters.setCurrentRow(current)

    def _filter_clicked(self, item: QListWidgetItem) -> None:
        cat = item.data(Qt.ItemDataRole.UserRole)
        self.filter = None if cat is None else {cat}
        self.review.set_categories(self.filter)
        self.show_page(REVIEW)

    # ---------------------------------------------------------------- 테마, 말투

    def apply_theme(self, name: str) -> None:
        self.theme = load_theme(name)
        t = self.theme
        app = QApplication.instance()
        app.setStyleSheet(stylesheet(t))
        self.bezel.set_theme(t)
        self.review.set_theme(t)
        self.ring.set_value(self.ring.done, self.ring.total, t)
        self._update_steps()
        # 작전 바 엠블럼: 핑크는 왕관(2026-11-08, 하트 보석은 판정 표시에 쓴다). 그림이 없으면 코드로 그린 표시
        # 단정한 모니터의 금고 다이얼은 결이 잘 보이게 크게(2026-11-08), 핑크 하트는 그대로
        emblem = t.asset("emblem")
        size = 30 if t.decorated else EMBLEM_DIAL_PX  # 왕관은 가로로 넓어 높이 30이면 너비 43
        self.emblem.setPixmap(scaled_asset(emblem, size) if emblem else
                              icons.pixmap("heart" if t.decorated else "monitor", t["accent"], size))
        self.offline_dot.setPixmap(icons.pixmap("power", t["signal_dot"], 14))
        self.blind_icon.setPixmap(icons.pixmap("warning", t["accent"], 20, background=t["paper_head"]))
        self.lock.setPixmap(icons.pixmap("lock_open", t["label"], 18))  # '금고 열림' 글자와 맞춰 열린 자물쇠
        # 검토 보드는 지금 화면 표시라 아이콘 없이 글자만(2026-11-08). 복원, 파일 더 넣기는 할 일이라 아이콘을 둔다
        self.nav[REVIEW].setIcon(QIcon())
        for b, name in ((self.nav[RESTORE], "restore"), (self.add_btn, "plus")):
            b.setIcon(QIcon(icons.pixmap(name, t["ink"], 20)))
            b.setIconSize(QSize(20, 20))
        self.settings_btn.setIcon(QIcon(icons.pixmap("gear", t["ink_sub"], 18)))
        self.settings_btn.setIconSize(QSize(18, 18))
        self._fill_filters()

    def apply_voice(self, name: str) -> None:
        self.voice.set(name)
        self.bezel.label = self.voice.t("app.bezel")
        self.binder.apply_all()
        self.review.retranslate()
        self.restore_page.retranslate()
        self.refresh()

    # ---------------------------------------------------------------- 동작

    def show_page(self, page: int) -> None:
        if page == REVIEW and not self.workflow.results:
            page = DROP
        if page == RESTORE:
            self.restore_page.reload()
        self.stack.setCurrentIndex(page)
        self._update_nav()
        self._update_steps()
        self._update_actions()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_area.setProperty("hover", True)
            self.drop_area.style().polish(self.drop_area)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.drop_area.setProperty("hover", False)
        self.drop_area.style().polish(self.drop_area)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.dragLeaveEvent(event)
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        self.load_files_async(expand_folders(paths))

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, self.voice.t("drop.dialog"), "",
                                                "HWP, HWPX, PDF, XLSX (*.hwp *.hwpx *.pdf *.xlsx)")
        if files:
            self.load_files_async([Path(f) for f in files])

    def load_files(self, paths: list[Path]) -> list[tuple[Path, str]]:
        """바로 읽는다(창이 잠깐 멈춘다). 화면에서는 load_files_async를 쓴다."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            read, failed = self.workflow.read_files(paths)
        finally:
            QApplication.restoreOverrideCursor()
        self._loaded(read, failed)
        return failed

    def load_files_async(self, paths: list[Path]) -> bool:
        """다른 스레드에서 읽는다. 다 읽으면 loaded 신호. 이미 읽는 중이면 무시한다."""
        if self.loader is not None or not paths:
            return False
        self.loader = ReaderThread(self.workflow, paths, self)
        # 첫 문서는 읽기 엔진을 켜느라 몇 초 걸린다. 넣자마자 창을 띄워 읽는 중임을 알린다(2026-11-09)
        self.load_dialog = BusyDialog(self.voice, self, "load.title", "load.reading", done=0, total=len(paths))
        self.loader.progress.connect(self._load_progress)
        self.loader.done.connect(self._loaded)
        self.guide.setText(self.voice.t("load.reading", done=0, total=len(paths)))
        self._update_actions()
        self.load_dialog.show()
        self.loader.start()
        return True

    def _load_progress(self, done: int, total: int) -> None:
        self.guide.setText(self.voice.t("load.reading", done=done, total=total))
        if self.load_dialog is not None:
            self.load_dialog.progress(done, total)

    def _loaded(self, read, failed) -> None:
        if self.loader is not None:
            self.loader.wait()
            self.loader = None
        if self.load_dialog is not None:
            self.load_dialog.hide()
            self.load_dialog.deleteLater()
            self.load_dialog = None
        self.workflow.add_results(read)
        self.notice = None
        self.stack.setCurrentIndex(REVIEW if self.workflow.results else DROP)
        self.refresh()
        self.loaded.emit()
        if failed:
            names = "\n".join(p.name for p, _ in failed)
            QMessageBox.warning(self, self.voice.t("error.title"), f"{self.voice.t('error.read')}\n{names}")

    def _all_clicked(self) -> None:
        """위 표 머리 체크박스: 위 표에서 고른 것이 있으면 모두 풀고, 없으면 보이는 대기 항목을 고른다."""
        model = self.review.model
        gray = set(self.review.gray_proxy.visible_ids())
        mine = model.checked - gray
        if mine:
            model.set_checked(list(mine), False)
        else:
            self.select_all()
        self.review.sync_all_check()

    def _gray_all_clicked(self) -> None:
        """요주의 구역 표 머리 체크박스: 그 표에서 고른 것이 있으면 풀고, 없으면 그 표 항목을 모두 고른다(올리기용)."""
        model = self.review.model
        gray = self.review.gray_proxy.visible_ids()
        mine = [i for i in gray if i in model.checked]
        model.set_checked(mine or gray, not mine)
        self.review.sync_all_check()

    def select_all(self) -> None:
        """위 표에 보이는 대기 항목을 고른다(이미 판정한 것을 한꺼번에 뒤집지 않게).
        보이는 대기 항목이 없으면(제외, 확정 칸만 걸러 볼 때 등) 보이는 항목을 모두 고른다(2026-11-08).
        요주의 구역 표는 자기 머리 체크박스로 고른다."""
        pending = {f.finding_id for f in self.workflow.findings if f.review_status == STATUS_PENDING}
        visible = self.review.visible_ids()
        ids = [i for i in visible if i in pending] or visible
        self.review.model.set_checked(ids, True)

    def _judge(self, action) -> None:
        added = action(list(self.review.model.checked))
        self.review.model.checked.clear()
        self.notice = self.voice.t("guide.rescan", n=len(added)) if action == self.workflow.confirm and added \
            else None
        self.refresh()

    def _toggle_verdict(self, ids: list[str], status: str | None) -> None:
        """판정 칩을 두 번 누르면 확정과 제외를 바꾼다. 묶음 행이면 그 값 전체."""
        targets = [f for f in self.workflow.by_id(ids) if not is_reference(f)]
        if self.workflow.mission_id or not targets or self.ai is not None or self.starter is not None:
            return
        self.review.model.checked = set(f.finding_id for f in targets)
        self._judge(self.workflow.exclude if status == STATUS_CONFIRMED else self.workflow.confirm)

    def show_blind_spots(self) -> None:
        BlindSpotDialog(self.voice, self.workflow.results, self).exec()

    # ---------------------------------------------------------------- AI로 더 찾기(9단계, 선택)

    def find_ai(self) -> bool:
        """다른 스레드에서 AI로 더 찾는다. 찾는 중에 다시 누르면 멈춘다. 다 찾으면 ai_finished 신호."""
        if self.ai is not None:
            self.ai.stop()
            return False
        if not self.workflow.can_find_ai() or self.loader is not None:
            return False
        self.ai = AiThread(self.workflow, self)
        self.ai.progress.connect(lambda done, total: self.guide.setText(
            self.voice.t("ai.running", done=done, total=total)))
        self.ai.done.connect(self._ai_done)
        self.guide.setText(self.voice.t("ai.running", done=0, total=100))
        self.review.ai_btn.setText(self.voice.t("ai.stop"))
        self._update_actions()
        self.ai.start()
        return True

    def _ai_done(self, run) -> None:
        if self.ai is not None:
            self.ai.wait()
            self.ai = None
        self.binder.refresh(self.review.ai_btn)
        added = self.workflow.add_ai(run)
        indirect = sum(1 for f in added if is_reference(f))
        if indirect:
            self.review.fold.setChecked(True)  # 간접식별 후보는 회색 영역에 들어간다
        self.notice = (self.voice.t("ai.done_indirect", n=len(added), m=indirect) if indirect
                       else self.voice.t("ai.done", n=len(added)) if added else self.voice.t("ai.done_none"))
        self.refresh()
        self.ai_finished.emit()
        if run.errors:
            lines = [self.voice.t(f"ai.error.{code}") if f"ai.error.{code}" in self.voice.keys()
                     else self.voice.t("ai.error.other", code=code) for code in dict.fromkeys(run.errors)]
            QMessageBox.warning(self, self.voice.t("error.title"), "\n".join(lines))

    def _start_record(self, record: ProcessingRecord | None) -> ProcessingRecord | None:
        """작전 개시는 확인 창 없이 바로 한다(2026-11-08 결정). 처리 기록의 목적은 고정 문구, 보관 기간은 설정 값.
        검사 못 한 영역이 있을 때만 그 글자가 빠진다는 것을 알리고 계속할지 묻는다. 그만두면 None."""
        if not self.workflow.can_start() or self.starter is not None:
            return None
        if record is None:
            blind = len(self.workflow.blind_spots())
            if blind and not self.confirm_blind_spots(blind):
                return None
            record = ProcessingRecord(DEFAULT_PURPOSE, "", self.settings.retention_days)
        return record

    def start_mission(self, record: ProcessingRecord | None = None) -> Path | None:
        """바로 개시한다(창이 잠깐 멈춘다). 화면의 단추는 start_mission_async를 쓴다."""
        record = self._start_record(record)
        if record is None:
            return None
        try:
            package = self.workflow.start(record)
        except BlocklistHit as e:
            self._blocked(e)
            return None
        return self._started(package)

    def start_mission_async(self) -> bool:
        """다른 스레드에서 개시하고, 누르자마자 처리 중 창을 띄운다. 끝나면 창을 닫고 결과를 보여 준다."""
        record = self._start_record(None)
        if record is None:
            return False
        self.starter = StartThread(self.workflow, record, self)
        self.starter.done.connect(self._start_done)
        self.busy_dialog = BusyDialog(self.voice, self)
        self.busy_dialog.show()
        self._update_actions()
        self.starter.start()
        return True

    def _start_done(self, package: Path | None, error: BaseException | None) -> None:
        if self.starter is not None:
            self.starter.wait()
            self.starter = None
        if self.busy_dialog is not None:
            self.busy_dialog.hide()
            self.busy_dialog.deleteLater()
            self.busy_dialog = None
        if isinstance(error, BlocklistHit):
            self._blocked(error)
        elif error is not None:
            log.error("start failed: %s", type(error).__name__)  # 예외 글에는 원문이 섞일 수 있어 종류만
            self.refresh()
            QMessageBox.warning(self, self.voice.t("error.title"), self.voice.t("error.start"))
        else:
            self._started(package)

    def _blocked(self, e: BlocklistHit) -> None:
        """차단 목록에 걸려 멈춤: 같은 값 줄을 표에 모으고 한 줄로 알린다."""
        ids = self.workflow.leftovers(e.finding_ids)
        self.refresh()
        if ids:  # 남은 자리를 표에 모아 두고 판정을 기다린다(새로 찾은 곳은 대기)
            self.show_page(REVIEW)
            self.review.show_ids(ids)
        QMessageBox.warning(self, self.voice.t("error.title"),
                            self.voice.t("error.blocklist" if ids else "error.blocklist_unknown",
                                         n=len(ids) or len(e.finding_ids)))

    def _started(self, package: Path) -> Path:
        self.refresh()
        self.done_dialog = RunDoneDialog(self.voice, self.workflow.mission_id, package, self.workflow.rules_text(),
                                         self.workflow.originals, self)
        # 반짝임(깜빡임)이 끝난 뒤에 송출 완료 창을 연다. 같이 열면 창이 반짝임을 가리고 깜빡여 보였다
        self.celebrate().finished.connect(self.done_dialog.open)
        return package

    def confirm_blind_spots(self, n: int) -> bool:
        """검사 못 한 영역 n곳의 글자는 보낼 문서에 들어가지 않는다. 계속하면 True."""
        box = QMessageBox(self)
        box.setWindowTitle(self.voice.t("run.title"))
        box.setText(self.voice.t("run.blind", term=self.voice.t("term.blind_spot"), n=n))
        go = box.addButton(self.voice.t("run.ok"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.voice.t("run.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is go

    def celebrate(self) -> QWidget:
        """작전 개시 직후 한 번. 테마에 반짝임 영상이 있으면 가운데에서 재생하고, 없으면 화면을 짧게 깜빡인다.
        마우스를 가로채지 않고, 끝나면 스스로 사라진다."""
        if self.effect is not None:
            self.effect.deleteLater()
        path = self.theme.asset("sparkle")
        if path:
            self.effect = SparkleOverlay(path, self)
        else:
            self.effect = FlashOverlay(self.theme, self)
        self.effect.play()
        return self.effect

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.voice, self.settings, self.change_password, self)
        if dlg.exec() != SettingsDialog.DialogCode.Accepted or dlg.result_settings is None:
            return
        new = dlg.result_settings
        if self.session:
            self.session.settings = new
            self.session.set_remember(new.remember_password)
        self.settings.__dict__.update(new.__dict__)
        save_settings(self.settings)
        self.apply_theme(new.theme)
        self.apply_voice(new.voice)
        self._update_actions()  # AI를 켜고 끄면 'AI로 더 찾기' 단추가 보이고 숨는다

    def change_password(self) -> None:
        if not self.session:
            return

        def submit(current: str, new: str) -> None:
            Vault(self.settings.vault_path).unlock(current)  # 지금 비밀번호 확인
            self.session.change_password(self.workflow.vault, new)

        dlg = PasswordDialog(self.voice, "change", submit, self)
        if dlg.exec() == PasswordDialog.DialogCode.Accepted:
            QMessageBox.information(self, self.voice.t("settings.title"), self.voice.t("pw.changed"))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.starter is not None:  # 쓰는 도중에 끊으면 송출함이 반쯤 남는다. 끝날 때까지 기다린다
            self.starter.wait()
        if self.loader is not None:
            self.loader.wait()
        if self.ai is not None:
            self.ai.stop()
            self.ai.wait()
        self.workflow.close()
        super().closeEvent(event)
