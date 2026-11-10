"""검토 보드: 탐지 항목 표, 회색 영역 접힌 구역, 판정 칩, 같은 값 묶어 보기.

- 탐지값은 바탕을 칠하지 않고 굵게 + 포인트색 밑줄 2px.
- 판정은 색만으로 구분하지 않는다: ○ 대기(테두리), ● 확정(채움), 제외(취소선), ◌ 참고(점선), ◐ 섞임.
- 표 행 40px, 줄무늬 없이 1px 구분선. 체크한 행은 선택 행 색, 마우스 올린 행은 머리글 색.
- 여러 행 고르기: 체크, Space, Shift+클릭(범위). 판정 상태와 유형으로 거르기.
- 같은 값 묶어 보기: 값마다 한 줄. 두 번 누르면 그 값의 발견 위치만 따로 본다.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPointF,
    QRectF,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from privacy_operator.alias.codes import value_key
from privacy_operator.detect.finding import STATUS_CONFIRMED, STATUS_EXCLUDED, STATUS_PENDING, Finding
from privacy_operator.ui import icons
from privacy_operator.ui.style import font, paint_check, paint_status, semibold_weight, smooth_pixmap
from privacy_operator.ui.theme import Theme
from privacy_operator.ui.voice import Voice
from privacy_operator.ui.widgets import Binder
from privacy_operator.workflow import MissionWorkflow, ValueGroup, is_reference

CHECK, TYPE, VALUE, CONTEXT, WHERE, CODE, VERDICT = range(7)
HEADER_KEYS = {TYPE: "col.type", VALUE: "col.value", CONTEXT: "col.context", WHERE: "col.where",
               CODE: "col.code", VERDICT: "col.verdict"}
FINDING_ROLE = Qt.ItemDataRole.UserRole + 1  # 행의 탐지(묶음이면 대표 탐지)
GROUP_ROLE = Qt.ItemDataRole.UserRole + 2  # 묶음 행이면 ValueGroup
ROW_HEIGHT = 40  # 2026-11-07 48에서 줄여 한 화면에 2~3줄 더 보이게(글자 크기는 그대로)
PAD = 12
CHECK_W = 44  # 체크 칸 너비(누를 자리 44px)
MARK_H = 20  # 판정 하트 높이(핑크): 대기 = 빈 금색 테두리, 확정 = 테두리에 박힌 보석
STATUS_DOT = 16  # 하트가 없을 때 판정 동그라미 지름(단정한 모니터, 핑크의 참고와 섞임)
STATUS_FILTERS = (("filter.all", None), ("filter.pending", STATUS_PENDING), ("filter.confirmed", STATUS_CONFIRMED),
                  ("filter.excluded", STATUS_EXCLUDED))

_META_PREFIX = ("meta-", "xprop-", "pprop-")
_COMMENT_PREFIX = ("memo", "xcm-", "pan-")


def place_text(f: Finding, voice: Voice) -> str:
    L = f.location
    if f.block_id.startswith(_META_PREFIX):
        place = voice.t("where.metadata")
    elif f.block_id.startswith(_COMMENT_PREFIX):
        place = voice.t("where.comment")
    elif L.sheet and L.cell:
        place = voice.t("where.cell", sheet=L.sheet, cell=L.cell)
    elif L.table_index is not None:
        place = voice.t("where.table", table=L.table_index + 1, row=L.row)
    else:
        place = voice.t("where.page", page=L.page or 1)
    if f.hidden:
        place = f"{place} ({voice.t('where.hidden')})"
    return place


def where_text(f: Finding, voice: Voice) -> str:
    reasons = []
    for by in f.detected_by:
        if by == "column" and f.location.header:
            reasons.append(voice.t("by.column", header=f.location.header))
        else:
            reasons.append(voice.t(f"by.{by}"))
    if f.escalated_by:
        reasons.append(voice.t(f"esc.{f.escalated_by}"))
    if f.exclude_hint:
        reasons.append(voice.t(f"hint.{f.exclude_hint}"))
    return f"{place_text(f, voice)}, " + ", ".join(dict.fromkeys(reasons))


def _tail_fit(text: str, fm: QFontMetrics, width: int) -> str:
    """text의 뒷부분만 width에 맞게 남긴다. 줄였으면 앞에 '…'."""
    if fm.horizontalAdvance(text) <= width:
        return text
    for i in range(1, len(text) + 1):
        candidate = "…" + text[i:]
        if fm.horizontalAdvance(candidate) <= width:
            return candidate
    return ""


# ---------------------------------------------------------------- 모델

class FindingsModel(QAbstractTableModel):
    """탐지 하나가 한 행. 체크 상태(checked)는 묶음 모델과 함께 쓴다."""

    changed = Signal()

    def __init__(self, workflow: MissionWorkflow, voice: Voice, parent=None):
        super().__init__(parent)
        self.workflow, self.voice = workflow, voice
        self.checked: set[str] = set()
        self._rows: list[Finding] = []

    def reload(self) -> None:
        self.beginResetModel()
        self._rows = list(self.workflow.findings)
        self.checked &= {f.finding_id for f in self._rows}
        self.endResetModel()
        self.changed.emit()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 7

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and section in HEADER_KEYS:
            return self.voice.t(HEADER_KEYS[section])
        return None

    def finding(self, row: int) -> Finding:
        return self._rows[row]

    def ids_at(self, row: int) -> list[str]:
        return [self._rows[row].finding_id]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        f = self._rows[index.row()]
        col = index.column()
        if role == FINDING_ROLE:
            return f
        if role == Qt.ItemDataRole.CheckStateRole and col == CHECK:
            return Qt.CheckState.Checked if f.finding_id in self.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            if col == TYPE:
                return f.category
            if col == VALUE:
                return f.raw_value
            if col == CONTEXT:
                return f.context
            if col == WHERE:
                return where_text(f, self.voice)
            if col == CODE:
                return f.alias_code or self.workflow.code_preview(f.finding_id) or "—"
        if role == Qt.ItemDataRole.ToolTipRole and col in (CONTEXT, WHERE):
            text = self.data(index)
            return f"{text}\n{f.note}" if col == CONTEXT and f.note else text  # AI가 댄 까닭
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return base | Qt.ItemFlag.ItemIsUserCheckable if index.column() == CHECK else base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == CHECK:
            self.set_checked(self.ids_at(index.row()), Qt.CheckState(value) == Qt.CheckState.Checked)
            return True
        return False

    def toggle(self, finding_id: str, on: bool | None = None) -> None:
        on = (finding_id not in self.checked) if on is None else on
        self.set_checked([finding_id], on)

    def set_checked(self, ids, on: bool) -> None:
        for fid in ids:
            (self.checked.add if on else self.checked.discard)(fid)
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 6))
        self.changed.emit()


class GroupsModel(QAbstractTableModel):
    """같은 값 묶어 보기. 값 하나가 한 행이고, 체크하면 그 값의 탐지가 모두 체크된다."""

    def __init__(self, findings: FindingsModel, parent=None):
        super().__init__(parent)
        self.findings, self.workflow, self.voice = findings, findings.workflow, findings.voice
        self._rows: list[ValueGroup] = []
        findings.changed.connect(self._repaint)

    def reload(self) -> None:
        self.beginResetModel()
        self._rows = self.workflow.groups()
        self.endResetModel()

    def _repaint(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 6))

    @property
    def checked(self) -> set[str]:
        return self.findings.checked

    def group(self, row: int) -> ValueGroup:
        return self._rows[row]

    def ids_at(self, row: int) -> list[str]:
        return self._rows[row].ids

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 7

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == CONTEXT:
                return self.voice.t("col.places")
            if section in HEADER_KEYS:
                return self.voice.t(HEADER_KEYS[section])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        g = self._rows[index.row()]
        first = g.members[0]
        col = index.column()
        if role == GROUP_ROLE:
            return g
        if role == FINDING_ROLE:
            return first
        if role == Qt.ItemDataRole.CheckStateRole and col == CHECK:
            on = all(i in self.checked for i in g.ids)
            return Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if col == TYPE:
                return g.category
            if col == VALUE:
                return g.value
            if col == CONTEXT:
                return self.voice.t("group.places", n=len(g.members), files=", ".join(g.files))
            if col == WHERE:
                place = place_text(first, self.voice)
                n = len(g.members) - 1
                return self.voice.t("where.more", first=place, n=n) if n else place
            if col == CODE:
                return first.alias_code or self.workflow.code_preview(first.finding_id) or "—"
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return base | Qt.ItemFlag.ItemIsUserCheckable if index.column() == CHECK else base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == CHECK:
            self.findings.set_checked(self.ids_at(index.row()), Qt.CheckState(value) == Qt.CheckState.Checked)
            return True
        return False


class FindingsProxy(QSortFilterProxyModel):
    """reference=True면 회색 영역(참고)만, False면 나머지. 유형, 판정 상태, 값 하나로 거른다."""

    def __init__(self, reference: bool, parent=None):
        super().__init__(parent)
        self.reference = reference
        self.categories: set[str] | None = None
        self.status: str | None = None
        self.value: tuple[str, str] | None = None
        self.ids: set[str] | None = None  # 송출이 멈춘 뒤 남은 자리만 보기

    def set_filter(self, categories=..., status=..., value=..., ids=...) -> None:
        self.beginFilterChange()
        if categories is not ...:
            self.categories = categories
        if status is not ...:
            self.status = status
        if value is not ...:
            self.value = value
        if ids is not ...:
            self.ids = ids
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def _accept(self, members: list[Finding]) -> bool:
        if self.categories is not None and members[0].category not in self.categories:
            return False
        if self.status is not None and all(f.review_status != self.status for f in members):
            return False
        if self.ids is not None and all(f.finding_id not in self.ids for f in members):
            return False
        return self.value is None or value_key(members[0].category, members[0].raw_value) == self.value

    def filterAcceptsRow(self, row, parent):  # noqa: N802
        src = self.sourceModel()
        if isinstance(src, GroupsModel):
            return self._accept(src.group(row).members)
        f = src.finding(row)
        return is_reference(f) == self.reference and self._accept([f])

    def visible_ids(self) -> list[str]:
        src = self.sourceModel()
        return [i for r in range(self.rowCount()) for i in src.ids_at(self.mapToSource(self.index(r, 0)).row())]


# ---------------------------------------------------------------- 그리기

class Delegate(QStyledItemDelegate):
    """행 바탕(체크, 마우스 올림)과 칸별 그리기. 탐지 행과 묶음 행을 함께 그린다."""

    def __init__(self, theme: Theme, voice: Voice, model: FindingsModel, parent=None):
        super().__init__(parent)
        self.theme, self.voice, self.model = theme, voice, model

    def sizeHint(self, option, index):  # noqa: N802
        s = super().sizeHint(option, index)
        s.setHeight(ROW_HEIGHT)
        return s

    def editorEvent(self, event, model, option, index):  # noqa: N802
        """체크 칸 누름은 표(FindingsTable._clicked)가 칸 전체를 누를 자리로 처리한다. 여기서 또 바꾸지 않는다."""
        if index.column() == CHECK and event.type() in (QEvent.Type.MouseButtonRelease,
                                                        QEvent.Type.MouseButtonDblClick):
            return False
        return super().editorEvent(event, model, option, index)

    def paint(self, p: QPainter, option, index) -> None:
        t = self.theme
        f: Finding = index.data(FINDING_ROLE)
        group: ValueGroup | None = index.data(GROUP_ROLE)
        ids = group.ids if group else [f.finding_id]
        p.save()
        if all(i in self.model.checked for i in ids):
            p.fillRect(option.rect, QColor(t["row_selected"]))
        elif getattr(option.widget, "hover_row", -1) == index.row():  # 마우스가 있는 줄 전체(칸 하나만이 아니라)
            p.fillRect(option.rect, QColor(t["paper_head"]))
        else:
            p.fillRect(option.rect, QColor(t["paper"]))
        p.setPen(QPen(QColor(t["line"]), 1))
        p.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        p.restore()

        col = index.column()
        r = option.rect.adjusted(PAD, 0, -PAD, 0)
        if col == CHECK:
            # 체크박스 그림은 칸 가운데(머리 체크박스와 같은 세로줄). 그림이 없는 테마는 Qt 기본 모양
            state = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole))
            if not paint_check(p, QRectF(option.rect), state, t):
                opt = option
                opt.state &= ~QStyle.StateFlag.State_HasFocus
                super().paint(p, opt, index)
        elif col == TYPE:
            self._type(p, r, f)
        elif col == VALUE:
            self._text(p, r, index.data(), "ink", bold=True)
        elif col == CONTEXT:
            if group:
                self._text(p, r, index.data(), "ink")
            else:
                self._context(p, r, f)
        elif col == WHERE:
            self._text(p, r, index.data(), "ink_sub", small=True)
        elif col == CODE:
            self._text(p, r, index.data(), "accent_deep", mono=True)
        elif col == VERDICT:
            self._chip(p, r, group.status if group else f.review_status)

    def _text(self, p, r, text, color, bold=False, small=False, mono=False) -> None:
        f = font("small" if small else "body", mono=mono)
        if bold:
            f.setBold(True)
        p.save()
        p.setFont(f)
        p.setPen(QColor(self.theme[color]))
        text = QFontMetrics(f).elidedText(text or "", Qt.TextElideMode.ElideRight, r.width())
        p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.restore()

    def _gem(self, category: str, ratio: float) -> QPixmap | None:
        """핑크 테마의 보석 점(유형마다 하나). 화면 배율에 맞는 크기로 부드럽게 줄인 것. 없으면 None, 색 점을 그린다."""
        path = self.theme.gem(category)
        return smooth_pixmap(path, round(14 * ratio)) if path else None

    def _type(self, p, r, f: Finding) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        gem = self._gem(f.category, p.device().devicePixelRatioF())
        if gem is not None:
            p.drawPixmap(QRectF(r.left() - 2, r.center().y() - 7, 14, 14), gem, QRectF(gem.rect()))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.theme.type_color(f.category)))
            p.drawEllipse(QRectF(r.left(), r.center().y() - 4, 8, 8))
        p.restore()
        self._text(p, r.adjusted(20, 0, 0, 0), f.category, "ink", small=True)

    def _context(self, p, r, f: Finding) -> None:
        """문맥 한 줄. 탐지값만 굵게 + 밑줄 2px."""
        text, value = f.context or "", f.raw_value
        pos = text.find(value) if value else -1
        normal, bold = font("body"), font("body")
        bold.setBold(True)
        fm, fmb = QFontMetrics(normal), QFontMetrics(bold)
        if pos < 0:
            self._text(p, r, text, "ink")
            return
        before, hit, after = text[:pos], value, text[pos + len(value):]
        if fmb.horizontalAdvance(hit) >= r.width():  # 칸이 탐지값보다 좁으면 탐지값만 줄여서
            hit = fmb.elidedText(hit, Qt.TextElideMode.ElideRight, r.width())
            before, after = "", ""
        room = r.width() - fmb.horizontalAdvance(hit)
        before = _tail_fit(before, fm, int(room * 0.5))  # 탐지값이 보이게 앞을 줄인다
        x, base = r.left(), r.center().y() + fm.ascent() / 2 - 2
        p.save()
        p.setPen(QColor(self.theme["ink"]))
        p.setFont(normal)
        p.drawText(int(x), int(base), before)
        x += fm.horizontalAdvance(before)
        p.setFont(bold)
        p.drawText(int(x), int(base), hit)
        w = fmb.horizontalAdvance(hit)
        p.fillRect(QRectF(x, base + 3, w, 2), QColor(self.theme["accent"]))
        x += w
        p.setFont(normal)
        rest = fm.elidedText(after, Qt.TextElideMode.ElideRight, max(0, int(r.right() - x)))
        p.drawText(int(x), int(base), rest)
        p.restore()

    def _chip(self, p, r, status: str | None) -> None:
        """판정 표시. 알약 바탕은 쓰지 않는다(2026-11-08). 핑크의 대기와 확정은 하트 그림, 나머지는 상태 동그라미와 글자.
        제외는 그림 없이 취소선 글자. 글자는 모두 같은 세로줄에서 시작한다."""
        t, v = self.theme, self.voice
        key = {STATUS_PENDING: "chip.pending", STATUS_CONFIRMED: "chip.confirmed", STATUS_EXCLUDED: "chip.excluded",
               None: "chip.mixed"}.get(status, "chip.reference")
        label = v.t(key)
        word = label.partition(" ")[2] or label  # 문구표의 글자 기호(○, ●, ◌, ◐)는 그림이 대신한다
        if t.asset("chip_confirmed") and status in (STATUS_CONFIRMED, STATUS_PENDING):
            self._heart(p, r, status, word)
            return
        fnt = font("button")
        fnt.setPixelSize(15)
        kind, color = {STATUS_PENDING: ("pending", t["ink"]), STATUS_CONFIRMED: ("confirmed", t["ink"]),
                       STATUS_EXCLUDED: (None, t["ink_sub"]), None: ("mixed", t["ink"])}.get(status, ("reference",
                                                                                                    t["ink_sub"]))
        # 굵기는 모든 상태가 같다. 확정만 굵게 하면 글자가 넓어져 바뀔 때 자리가 움직여 보인다(2026-11-08)
        if status == STATUS_EXCLUDED:
            fnt.setStrikeOut(True)
        cy = r.center().y()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind:
            paint_status(p, QPointF(r.left() + MARK_H / 2, cy), STATUS_DOT, kind, t)
        self._label(p, r, cy, word, fnt, color)
        p.restore()

    def _label(self, p, r, cy: float, text: str, fnt, color: str) -> None:
        """판정 글자. 글자 상자가 아니라 실제 윤곽 가운데를 그림 가운데(cy)와 같은 높이에 둔다.
        윤곽은 판정 낱말 전부를 이어 붙인 글자로 잰다. 낱말마다 재면 '대기'와 '확정'의 기준선이 달라
        대기에서 확정으로 바뀔 때 글자가 위아래로 움직여 보인다(2026-11-08)."""
        regular = QFont(fnt)
        regular.setWeight(QFont.Weight.Normal)
        regular.setStrikeOut(False)
        tight = QFontMetrics(regular).tightBoundingRect(self._words())
        p.setFont(fnt)
        p.setPen(QColor(color))
        p.drawText(QPointF(r.left() + MARK_H + 6, cy - (tight.top() + tight.height() / 2)), text)

    def _words(self) -> str:
        v = self.voice
        keys = ("chip.pending", "chip.confirmed", "chip.excluded", "chip.mixed", "chip.reference")
        return "".join(v.t(k).partition(" ")[2] or v.t(k) for k in keys)

    def _heart(self, p, r, status: str, label: str) -> None:
        """핑크: 알약 없이 하트와 글자(2026-11-08). 대기 = 빈 금색 하트 테두리,
        확정 = 같은 테두리에 하트 보석이 박힌 그림. 글자 색과 굵기는 같다.
        글자 상자가 아니라 글자의 실제 윤곽 가운데를 하트 가운데와 같은 높이에 둔다(한글은 상자 가운데보다 아래에 앉는다)."""
        t = self.theme
        ratio = p.device().devicePixelRatioF()
        if status == STATUS_CONFIRMED:
            mark_h = MARK_H
            mark = smooth_pixmap(t.asset("chip_confirmed"), round(mark_h * 1.5 * ratio))
            color, weight = t["ink"], QFont.Weight(semibold_weight())  # 대기와 같은 굵기(제자리에서 바뀌게)
        elif t.asset("chip_pending"):  # 빈 금색 테두리. 확정 그림과 테두리가 같아 크기, 자리가 그대로다
            mark_h = MARK_H
            mark = smooth_pixmap(t.asset("chip_pending"), round(mark_h * 1.5 * ratio))
            color, weight = t["ink"], QFont.Weight(semibold_weight())  # 글자는 확정과 같다. 바뀌는 건 하트뿐
        else:  # 선 아이콘은 둘레에 여백이 있어 조금 크게 그려야 보석 하트와 같은 크기로 보인다
            mark_h = MARK_H * 1.3
            mark = icons.pixmap("heart_outline", t["accent"], round(mark_h), ratio=max(2.0, ratio))
            color, weight = t["accent"], QFont.Weight(semibold_weight())
        mark_w = mark.width() / mark.height() * mark_h
        fnt = font("button")
        fnt.setPixelSize(15)
        fnt.setWeight(weight)
        cy = r.center().y()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        left = r.left() - (mark_w - MARK_H) / 2  # 하트 가운데를 두 상태에서 같은 세로줄에
        p.drawPixmap(QRectF(left, cy - mark_h / 2, mark_w, mark_h), mark, QRectF(mark.rect()))
        self._label(p, r, cy, label, fnt, color)
        p.restore()


# ---------------------------------------------------------------- 표

class FindingsTable(QTableView):
    def __init__(self, proxy: FindingsProxy, delegate: Delegate, parent=None):
        super().__init__(parent)
        self.setModel(proxy)
        self.setItemDelegate(delegate)
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setFont(font("small"))
        self._layout_columns()
        self.clicked.connect(self._clicked)
        self.verdict_toggled = None  # (ids, 지금 판정) -> None, 창이 넣는다
        self.drill = None  # (값 열쇠) -> None, 묶음 행을 두 번 누르면
        self._anchor: int | None = None

    def _layout_columns(self) -> None:
        h = self.horizontalHeader()
        for col, width in ((CHECK, CHECK_W), (TYPE, 112), (VALUE, 170), (CODE, 140), (VERDICT, 112)):
            h.resizeSection(col, width)
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        # 문맥과 위치는 남은 너비를 나눠 쓴다(좁은 창, 150% 배율에서도 문맥이 사라지지 않게)
        h.setSectionResizeMode(CONTEXT, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(WHERE, QHeaderView.ResizeMode.Stretch)
        h.setMinimumSectionSize(44)

    def setModel(self, model) -> None:  # noqa: N802
        super().setModel(model)
        if self.horizontalHeader().count():
            self._layout_columns()

    def source_rows(self, first: int, last: int) -> list[str]:
        proxy = self.model()
        lo, hi = sorted((first, last))
        return [i for r in range(lo, hi + 1) for i in proxy.sourceModel().ids_at(proxy.mapToSource(proxy.index(r, 0)).row())]

    hover_row = -1  # 마우스가 올라간 줄. 칸이 아니라 줄 전체를 칠한다(Delegate.paint)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        row = self.indexAt(event.position().toPoint()).row()
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_row = -1
        self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)  # 이 클릭의 Shift
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """doubleClicked 신호는 앞의 누름 기록에 따라 빠질 수 있어, 포인터 아래 칸을 직접 본다."""
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            self._double(index)

    def _clicked(self, index) -> None:
        proxy = self.model()
        src = proxy.sourceModel()
        row = index.row()
        if getattr(self, "_shift", False) and self._anchor is not None:
            src_model = src.findings if isinstance(src, GroupsModel) else src
            src_model.set_checked(self.source_rows(self._anchor, row), True)
        else:
            ids = src.ids_at(proxy.mapToSource(index).row())
            src_model = src.findings if isinstance(src, GroupsModel) else src
            src_model.set_checked(ids, not all(i in src_model.checked for i in ids))
        self._anchor = row

    def _double(self, index) -> None:
        group = index.data(GROUP_ROLE)
        if index.column() == VERDICT and self.verdict_toggled:
            f = index.data(FINDING_ROLE)
            self.verdict_toggled(group.ids if group else [f.finding_id], group.status if group else f.review_status)
        elif group and self.drill:
            self.drill(group.key)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space and self.currentIndex().isValid():
            proxy = self.model()
            src = proxy.sourceModel()
            ids = src.ids_at(proxy.mapToSource(self.currentIndex()).row())
            src_model = src.findings if isinstance(src, GroupsModel) else src
            src_model.set_checked(ids, not all(i in src_model.checked for i in ids))
            return
        super().keyPressEvent(event)


class SegButton(QPushButton):
    """판정 거르기 칸. 고르면 글자가 굵어지므로, 처음부터 굵은 글자 너비로 자리를 잡아 고른 뒤에도 잘리지 않게 한다."""

    def sizeHint(self):  # noqa: N802
        s = super().sizeHint()
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.Bold)
        extra = QFontMetrics(bold).horizontalAdvance(self.text()) - QFontMetrics(self.font()).horizontalAdvance(self.text())
        return QSize(s.width() + max(0, extra) + 2, s.height())

    def minimumSizeHint(self):  # noqa: N802
        return self.sizeHint()


class CenteredCheck(QCheckBox):
    """글자 없는 체크박스. 그림을 44px 누를 자리 한가운데에 그린다(Qt 기본은 왼쪽에 붙인다)."""

    def __init__(self, table: QTableView):
        super().__init__()
        self.table = table

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        drawn = paint_check(p, QRectF(self.rect()), self.checkState(), self.table.itemDelegate().theme,
                            self.isEnabled())
        p.end()
        if not drawn:
            super().paintEvent(event)

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)


class HeaderCheck(QWidget):
    """표 머리 체크 칸에 얹는 '모두 고르기' 체크박스. 머리 크기가 바뀌면 따라 움직인다.
    누르면 무엇을 고를지는 창이 정한다(box.clicked)."""

    def __init__(self, table: QTableView):
        super().__init__(table.horizontalHeader())
        self.header = table.horizontalHeader()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.box = CenteredCheck(table)
        self.box.setFixedSize(CHECK_W, CHECK_W)
        lay.addWidget(self.box, 0, Qt.AlignmentFlag.AlignCenter)
        self.header.installEventFilter(self)
        self.header.sectionResized.connect(self._place)
        self._place()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.header and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place()
        return False

    def _place(self, *_args) -> None:
        self.setGeometry(self.header.sectionViewportPosition(CHECK), 0,
                         max(CHECK_W, self.header.sectionSize(CHECK)), self.header.height())
        self.raise_()


class ReviewPage(QWidget):
    """위 거르기 띠 + 가운데 검토 표 + 아래 회색 영역 접힌 구역."""

    def __init__(self, workflow: MissionWorkflow, voice: Voice, theme: Theme, binder: Binder, parent=None):
        super().__init__(parent)
        self.setObjectName("Paper")
        self.voice, self.workflow, self._binder = voice, workflow, binder
        self.model = FindingsModel(workflow, voice, self)
        self.groups = GroupsModel(self.model, self)
        self.delegate = Delegate(theme, voice, self.model, self)
        self.main_proxy, self.gray_proxy = FindingsProxy(False, self), FindingsProxy(True, self)
        self.group_proxy = FindingsProxy(False, self)
        self.main_proxy.setSourceModel(self.model)
        self.gray_proxy.setSourceModel(self.model)
        self.group_proxy.setSourceModel(self.groups)
        self.table = FindingsTable(self.main_proxy, self.delegate)
        self.table.drill = self.show_value
        self.gray_table = FindingsTable(self.gray_proxy, self.delegate)
        self.gray_table.hide()

        self.all_check = HeaderCheck(self.table)
        binder.text(self.all_check.box, "action.select_all", setter=self.all_check.box.setAccessibleName)
        # 요주의 구역(회색 영역) 표도 자기 머리 체크박스로 모두 고르고 푼다(2026-11-08, 위 표 체크박스는 위 표만)
        self.gray_check = HeaderCheck(self.gray_table)
        binder.text(self.gray_check.box, "action.select_all", setter=self.gray_check.box.setAccessibleName)
        self.model.changed.connect(self.sync_all_check)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._toolbar())
        lay.addWidget(self.table, 1)
        self.fold = QPushButton()
        self.fold.setObjectName("Fold")
        self.fold.setCheckable(True)
        self.fold.toggled.connect(self._fold)
        binder.text(self.fold, "gray.folded", setter=self._fold_text, values=self._fold_values, tip=False)
        lay.addWidget(self.fold)
        lay.addWidget(self.gray_table)
        self.model.changed.connect(lambda: binder.refresh(self.fold))
        self.model.changed.connect(self._size_gray)

    # ---------------------------------------------------------------- 거르기 띠

    def _toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("ReviewBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 12, 20, 12)
        h.setSpacing(16)
        # 판정으로 거르기: 한 덩어리 칸 고르기. 고른 칸만 연한 강조로 채운다
        segment = QWidget()
        segment.setObjectName("Segment")
        seg = QHBoxLayout(segment)
        seg.setContentsMargins(3, 3, 3, 3)
        seg.setSpacing(2)
        self.status_buttons = QButtonGroup(self)
        self.status_buttons.setExclusive(True)
        for i, (key, status) in enumerate(STATUS_FILTERS):
            b = SegButton()
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setChecked(status is None)
            self._binder.text(b, key, tip=False, values=lambda s=status: {"n": self._count(s)})
            self.status_buttons.addButton(b, i)
            seg.addWidget(b)
        self.status_buttons.idClicked.connect(lambda i: self.set_status(STATUS_FILTERS[i][1]))
        h.addWidget(segment)
        self.group_btn = self._binder.text(QCheckBox(), "view.group", tip=False)
        self.group_btn.setMinimumHeight(44)
        self.group_btn.toggled.connect(self.set_grouped)
        h.addWidget(self.group_btn)
        h.addStretch(1)
        self.value_label = QLabel()
        self.value_label.setObjectName("Guide")
        self.show_all_btn = self._binder.text(QPushButton(), "view.show_all", tip=False)
        self.show_all_btn.clicked.connect(lambda: self.show_value(None))
        h.addWidget(self.value_label)
        h.addWidget(self.show_all_btn)
        self.value_label.hide()
        self.show_all_btn.hide()
        # AI로 더 찾기(9단계, 선택). 누르면 주 창이 처리한다. 설정에서 AI를 켰을 때만 보인다
        self.ai_btn = self._binder.text(QPushButton(), "view.ai_find")
        self.ai_btn.hide()
        h.addWidget(self.ai_btn)
        return bar

    def _count(self, status: str | None) -> int:
        main = [f for f in self.workflow.findings if not is_reference(f)]
        return len(main) if status is None else sum(f.review_status == status for f in main)

    def set_status(self, status: str | None) -> None:
        for proxy in (self.main_proxy, self.group_proxy):
            proxy.set_filter(status=status)
        self.sync_all_check()

    def sync_all_check(self) -> None:
        """머리 체크박스: 아무것도 안 골랐으면 꺼짐, 보이는 항목을 모두 골랐으면 켜짐, 그 사이면 반쯤."""
        checked = self.model.checked
        gray = set(self.gray_proxy.visible_ids())
        main = set(self.visible_ids())
        for box, visible in ((self.all_check.box, main), (self.gray_check.box, gray)):
            mine = checked & visible if box is self.gray_check.box else checked - gray
            state = (Qt.CheckState.Unchecked if not mine else
                     Qt.CheckState.Checked if visible and visible <= checked else Qt.CheckState.PartiallyChecked)
            box.blockSignals(True)
            box.setCheckState(state)
            box.blockSignals(False)

    def set_grouped(self, on: bool) -> None:
        if on:
            self.show_value(None, keep_grouping=True)
            self.groups.reload()
        self.table.setModel(self.group_proxy if on else self.main_proxy)
        self.sync_all_check()

    def show_ids(self, ids: list[str]) -> None:
        """송출이 멈춘 뒤: 남은 자리만 낱낱이 본다. '모두 보기'로 풀린다."""
        if self.group_btn.isChecked():
            self.group_btn.setChecked(False)
        self.status_buttons.button(0).setChecked(True)
        self.set_status(None)
        self.main_proxy.set_filter(value=None, ids=set(ids))
        self.value_label.setText(self.voice.t("view.leftovers", n=len(ids)))
        self.value_label.setVisible(True)
        self.show_all_btn.setVisible(True)
        self.sync_all_check()

    def show_value(self, key: tuple[str, str] | None, keep_grouping: bool = False) -> None:
        """묶음 행을 두 번 누르면 그 값의 발견 위치만 낱낱이 본다. None이면 모두 보기."""
        self.main_proxy.set_filter(value=key, ids=None)
        if key is not None and self.group_btn.isChecked() and not keep_grouping:
            self.group_btn.setChecked(False)
        self.value_label.setVisible(key is not None)
        self.show_all_btn.setVisible(key is not None)
        if key is not None:
            self.value_label.setText(f"{self.voice.t('view.one_value')}: {key[0]} {key[1]}")
        self.sync_all_check()

    # ---------------------------------------------------------------- 회색 영역

    def _fold_values(self) -> dict:
        return {"term": self.voice.t("term.gray_zone"), "n": self.gray_proxy.rowCount()}

    def _fold_text(self, text: str) -> None:
        if self.fold.isChecked():
            text = self.voice.t("gray.unfolded", **self._fold_values())
        self.fold.setText(text)

    def _fold(self, on: bool) -> None:
        self._size_gray()
        self.gray_table.setVisible(on)
        self._binder.refresh(self.fold)

    def _size_gray(self) -> None:
        """접힌 구역은 행 수만큼만(5행까지, 그 이상은 스크롤) 차지한다."""
        rows = min(max(self.gray_proxy.rowCount(), 1), 5)
        self.gray_table.setFixedHeight(self.gray_table.horizontalHeader().height() + rows * ROW_HEIGHT + 4)

    # ---------------------------------------------------------------- 바깥에서

    def reload(self) -> None:
        self.model.reload()
        if self.group_btn.isChecked():
            self.groups.reload()
        for b in self.status_buttons.buttons():
            self._binder.refresh(b)

    def set_verdict_handler(self, handler) -> None:
        self.table.verdict_toggled = handler
        self.gray_table.verdict_toggled = handler

    def visible_ids(self) -> list[str]:
        proxy = self.table.model()
        return proxy.visible_ids()

    def set_theme(self, theme: Theme) -> None:
        self.delegate.theme = theme
        self.table.viewport().update()
        self.gray_table.viewport().update()

    def set_categories(self, categories: set[str] | None) -> None:
        for proxy in (self.main_proxy, self.gray_proxy, self.group_proxy):
            proxy.set_filter(categories=categories)
        self.sync_all_check()

    def retranslate(self) -> None:
        for m in (self.model, self.groups):
            m.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 6)
        self.table.viewport().update()
        self.gray_table.viewport().update()
