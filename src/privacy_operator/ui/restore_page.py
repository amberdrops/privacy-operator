"""복원 화면: 받은 결과함의 파일을 골라 작전 번호에 맞는 대응표로 되돌린다."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from privacy_operator.mission.results import READY, NeedsMissionChoice, ResultFile, RestoredFile
from privacy_operator.ui.voice import Voice
from privacy_operator.ui.widgets import Binder
from privacy_operator.workflow import MissionWorkflow, code_key

RESTORABLE_STATES = {READY, "no_mission", "conflict"}


class RestorePage(QWidget):
    def __init__(self, workflow: MissionWorkflow, voice: Voice, binder: Binder, parent=None):
        super().__init__(parent)
        self.setObjectName("RestorePage")
        self.workflow, self.voice, self.binder = workflow, voice, binder
        self.items: list[ResultFile] = []
        self.last: RestoredFile | None = None

        title, hint = QLabel(), QLabel()
        title.setObjectName("Heading")
        hint.setObjectName("Guide")
        hint.setWordWrap(True)
        binder.text(title, "restore.title", tip=False)
        binder.text(hint, "restore.hint", tip=False)

        self.refresh_btn = binder.text(QPushButton(), "restore.refresh")
        self.refresh_btn.clicked.connect(self.reload)

        # 코드명 찾기: 코드명 하나를 적으면 모든 작전의 대응표에서 찾아 화면에만 보여 준다(파일로 남기지 않는다)
        self.find = QLineEdit()
        self.find.setClearButtonEnabled(True)
        binder.text(self.find, "restore.find_code", setter=self.find.setPlaceholderText)
        self.found = QLabel()
        self.found.setObjectName("Summary")
        self.found.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._find_timer = QTimer(self)
        self._find_timer.setSingleShot(True)
        self._find_timer.setInterval(300)  # 한 글자마다 금고를 열지 않게 조금 기다렸다 찾는다
        self._find_timer.timeout.connect(self.find_code)
        self.find.textChanged.connect(lambda _t: self._find_timer.start())
        self.find.returnPressed.connect(self.find_code)

        self.table = QTableWidget(0, 3)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        # 고른 줄은 줄 전체를 한 색으로(칸마다 끊긴 회색 상자와 점선 초점 테두리가 나오지 않게), 머리글은 왼쪽 정렬
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().resizeSection(1, 200)
        self.table.horizontalHeader().resizeSection(2, 260)
        self.table.itemSelectionChanged.connect(self._selected)
        self.empty = QLabel()
        self.empty.setObjectName("Guide")
        binder.text(self.empty, "restore.empty", tip=False)

        self.mission = QComboBox()
        self.mission.setMinimumWidth(220)
        choose = QLabel()
        binder.text(choose, "restore.choose", tip=False)
        self.run_btn = binder.text(QPushButton(), "restore.run")
        self.run_btn.setObjectName("Primary")
        self.run_btn.clicked.connect(lambda: self.run())

        self.summary, self.output = QLabel(), QLabel()
        self.summary.setObjectName("Summary")
        self.output.setObjectName("Guide")
        self.output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.open_btn = binder.text(QPushButton(), "restore.open_folder")
        self.open_btn.clicked.connect(self._open_folder)
        self.open_btn.hide()

        self.variants_title = QLabel()
        binder.text(self.variants_title, "restore.variants", tip=False)
        self.variants = QListWidget()
        self.accept_btn = binder.text(QPushButton(), "restore.accept")
        self.accept_btn.clicked.connect(self._accept)
        for w in (self.variants_title, self.variants, self.accept_btn):
            w.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.refresh_btn)
        lay.addLayout(top)
        lay.addWidget(hint)
        find_row = QHBoxLayout()
        find_row.addWidget(self.find, 1)
        find_row.addWidget(self.found, 2)
        lay.addLayout(find_row)
        lay.addWidget(self.table, 2)
        lay.addWidget(self.empty)
        row = QHBoxLayout()
        row.addWidget(choose)
        row.addWidget(self.mission)
        row.addStretch(1)
        row.addWidget(self.run_btn)
        lay.addLayout(row)
        lay.addWidget(self.summary)
        out_row = QHBoxLayout()
        out_row.addWidget(self.output, 1)
        out_row.addWidget(self.open_btn)
        lay.addLayout(out_row)
        lay.addWidget(self.variants_title)
        lay.addWidget(self.variants)
        lay.addWidget(self.accept_btn, 0, Qt.AlignmentFlag.AlignLeft)  # 고르는 목록 바로 아래
        lay.addStretch(1)
        self.retranslate()

    def retranslate(self) -> None:
        self.table.setHorizontalHeaderLabels([self.voice.t("restore.col.file"), self.voice.t("restore.col.mission"),
                                              self.voice.t("restore.col.state")])
        self._fill()

    def reload(self) -> None:
        self.items = self.workflow.results_waiting()
        self.mission.clear()
        for m in reversed(self.workflow.missions()):
            self.mission.addItem(m, m)
        self._fill()

    def _fill(self) -> None:
        self.table.setRowCount(len(self.items))
        for r, item in enumerate(self.items):
            cells = (item.path.name, item.mission_id or "", self.voice.t(f"restore.state.{item.state}"))
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.empty.setVisible(not self.items)
        if self.items and not self.table.selectedItems():
            self.table.selectRow(0)
        self._selected()

    def current(self) -> ResultFile | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.items[rows[0].row()] if rows and rows[0].row() < len(self.items) else None

    def _selected(self) -> None:
        item = self.current()
        ok = item is not None and item.state in RESTORABLE_STATES
        self.run_btn.setEnabled(ok)
        self.mission.setEnabled(ok and item.state != READY)
        if item and item.mission_id:
            idx = self.mission.findData(item.mission_id)
            if idx >= 0:
                self.mission.setCurrentIndex(idx)

    def run(self, accept: frozenset[str] = frozenset()) -> RestoredFile | None:
        item = self.current()
        if item is None:
            return None
        chosen = None if item.state == READY else self.mission.currentData()
        try:
            self.last = self.workflow.restore(item, chosen, accept)
        except NeedsMissionChoice:
            self.summary.setText(self.voice.t("restore.need_choice"))
            return None
        res = self.last.result
        self.summary.setText(self.voice.t("restore.summary", restored=sum(res.restored.values()),
                                          variants=len(res.variants), unknown=len(res.unknown_codes),
                                          missing=len(res.missing_codes)))
        self.output.setText(self.voice.t("restore.output", path=str(self.last.output)))
        self.open_btn.show()
        self.variants.clear()
        for v in res.variants:
            li = QListWidgetItem(f"{v.text}  →  {v.suggested_code}")
            li.setData(Qt.ItemDataRole.UserRole, v.variant_id)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Unchecked)
            self.variants.addItem(li)
        for w in (self.variants_title, self.variants, self.accept_btn):
            w.setVisible(bool(res.variants))
        # 목록은 항목 수만큼만 높게(6줄까지, 넘으면 스크롤). 아래 단추가 목록 바로 밑에 붙는다
        rows = min(max(len(res.variants), 1), 6)
        self.variants.setFixedHeight(rows * max(36, self.variants.sizeHintForRow(0)) + 2 * self.variants.frameWidth() + 4)
        return self.last

    def _accept(self) -> None:
        ids = {self.variants.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.variants.count())
               if self.variants.item(i).checkState() == Qt.CheckState.Checked}
        if ids:
            self.run(frozenset(ids))

    def find_code(self) -> list[tuple[str, str, str]]:
        """적은 코드명을 모든 작전의 대응표에서 찾아 '작전 번호: 원문 값'으로 보여 준다. 코드명 모양이 아니면 비운다."""
        self._find_timer.stop()
        text = self.find.text()
        if code_key(text) is None:
            self.found.clear()
            return []
        hits = self.workflow.find_code(text)
        self.found.setText("\n".join(self.voice.t("restore.find_hit", mission=m, code=c, value=v) for m, c, v in hits)
                           if hits else self.voice.t("restore.find_none"))
        return hits

    def _open_folder(self) -> None:
        if self.last:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last.output.parent)))
