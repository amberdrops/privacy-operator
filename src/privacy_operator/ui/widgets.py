"""공통 화면 부품: 문구 묶기(Binder), 베젤, 진행 단계, 진행률 원.

베젤은 Flow 에셋을 9분할로 그린다. 에셋이 없으면 색 토큰으로 그린 기본 모양을 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from privacy_operator.ui import icons
from privacy_operator.ui.style import draw_nine, font, nine_pixmap, paint_status, semibold_weight, smooth_pixmap
from privacy_operator.ui.theme import Theme
from privacy_operator.ui.voice import Voice


class Binder:
    """위젯 글자를 말투 키에 묶어 둔다. 말투를 바꾸면 apply_all()로 모두 다시 쓴다."""

    def __init__(self, voice: Voice):
        self.voice = voice
        self.items: list[tuple[QWidget, str, Callable[[str], None], Callable[[], dict] | None, bool]] = []

    def text(self, widget, key: str, setter: Callable[[str], None] | None = None,
             values: Callable[[], dict] | None = None, tip: bool = True):
        item = (widget, key, setter or widget.setText, values, tip)
        self.items.append(item)
        self._apply(item)
        return widget

    def _apply(self, item) -> None:
        widget, key, setter, values, tip = item
        setter(self.voice.t(key, **(values() if values else {})))
        if tip and hasattr(widget, "setToolTip"):
            widget.setToolTip(self.voice.tip(key))

    def apply_all(self) -> None:
        for item in self.items:
            self._apply(item)

    def refresh(self, widget) -> None:
        for item in self.items:
            if item[0] is widget:
                self._apply(item)


CHIN_FLAT = (0.2, 0.85)  # 베젤 아래 턱에서 평평한 띠(위 안쪽 테두리와 아래 둥근 가장자리를 뺀 곳). 테마 값이 없을 때
PEARL_STRAND_PX = 11  # 턱 진주 띠 높이


def asset_pixmap(path) -> QPixmap:
    """에셋을 이미지 픽셀 그대로 쓴다(@2x 이름이어도 밀도를 1로). 9분할 자를 곳이 이미지 픽셀 기준이다."""
    pm = QPixmap.fromImage(QImage(str(path)))
    pm.setDevicePixelRatio(1.0)
    return pm


class Bezel(QWidget):
    """모니터 베젤. 안쪽 화면은 setScreen으로 넣는다.

    테마에 베젤 에셋이 있으면 9분할로 그리고(두께는 theme.json nine_slice.bezel.border), 없으면 색 토큰으로 그린다.
    아래 턱 왼쪽에 크레딧. 핑크 테마는 그 오른쪽을 진주 띠로 끝까지 잇고, 단정한 모니터는 오른쪽 끝에 전원 표시등을 둔다.
    """

    CHIN = 54
    SIDE = 22

    def __init__(self, theme: Theme, label: str, parent=None):
        super().__init__(parent)
        self.label = label
        QVBoxLayout(self)
        self.set_theme(theme)

    def setScreen(self, widget: QWidget) -> None:
        self.layout().addWidget(widget)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        nine = theme.slices("bezel")
        self.frame = asset_pixmap(theme.asset("bezel")) if nine else None
        self.cut, self.border = nine if nine else (None, [self.SIDE, self.SIDE, self.CHIN, self.SIDE])
        self.pearls = asset_pixmap(theme.asset("pearl_strand")) if theme.asset("pearl_strand") else None
        self.flat = tuple(theme.nine_slice.get("bezel", {}).get("chin_flat", CHIN_FLAT))
        self._pearls_small: dict[float, QPixmap] = {}
        top, right, bottom, left = self.border
        self.layout().setContentsMargins(left, top, right, bottom)
        self.update()

    def paintEvent(self, _event) -> None:
        if self.frame is not None:
            self._paint_asset()
            return
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(t["bezel_rim"]), 3))
        p.setBrush(QColor(t["bezel"]))
        p.drawRoundedRect(r, 26, 26)
        # 안쪽 테두리(유리 가장자리 몇 px)
        inner = QRectF(self.SIDE - 4, self.SIDE - 4, self.width() - 2 * self.SIDE + 8,
                       self.height() - self.SIDE - self.CHIN + 8)
        p.setPen(QPen(QColor(t["metal"]), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(inner, 10, 10)
        # 아래 턱: 크레딧과 전원 표시(장식 테마는 표시등 없이)
        p.setFont(font("small"))
        p.setPen(QColor(t["bezel_ink"]))
        chin = QRectF(self.SIDE + 8, self.height() - self.CHIN + 6, 480, self.CHIN - 16)
        p.drawText(chin, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.label)
        if not t.decorated:
            size = 20
            pm = icons.pixmap("power", t["signal_dot"], size)
            p.drawPixmap(int(self.width() - self.SIDE - 8 - size), int(self.height() - self.CHIN / 2 - size / 2 - 2), pm)
        p.end()

    def _paint_asset(self) -> None:
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ratio = self.devicePixelRatioF()
        frame, cut = nine_pixmap(self.theme.asset("bezel"), self.cut, self.border, ratio)
        draw_nine(p, QRectF(self.rect()), frame, cut, self.border)
        _top, right, bottom, left = self.border
        # 아래 턱의 평평한 띠(위 안쪽 테두리, 아래 둥근 가장자리를 뺀 곳) 세로 가운데에 크레딧, 진주 띠(또는 전원 표시)
        top_f, bottom_f = self.flat
        chin = QRectF(left + 6, self.height() - bottom + bottom * top_f, self.width() - left - right - 12,
                      bottom * (bottom_f - top_f))
        # 크레딧(왼쪽)
        p.setFont(font("small"))
        p.setPen(QColor(t["bezel_ink"]))
        name_w = p.fontMetrics().horizontalAdvance(self.label) + 16
        p.drawText(chin, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.label)
        end = chin.right()
        if self.pearls is None:  # 진주 띠가 없는 테마는 오른쪽에 전원 표시등(둥근 모서리에 걸치지 않게 조금 안쪽)
            size = min(20, int(chin.height()) - 4)
            p.drawPixmap(QPointF(chin.right() - size - 28, chin.center().y() - size / 2),
                         icons.pixmap("power", t["signal_dot"], size))
        # 진주 띠: 크레딧 오른쪽부터 턱 끝까지 반복으로 잇는다
        if self.pearls is not None:
            h = PEARL_STRAND_PX
            if ratio not in self._pearls_small:  # 큰 그림을 한 번만 부드럽게 줄여 둔다(그릴 때 줄이면 거칠다)
                self._pearls_small[ratio] = QPixmap.fromImage(self.pearls.toImage().scaledToHeight(
                    round(h * ratio), Qt.TransformationMode.SmoothTransformation))
            small = self._pearls_small[ratio]
            w = small.width() / ratio
            x = chin.left() + name_w
            y = chin.center().y() - h / 2
            p.save()
            p.setClipRect(QRectF(x, y, max(0, end - x), h))
            while x < end:
                p.drawPixmap(QRectF(x, y, w, h), small, QRectF(small.rect()))
                x += w
            p.restore()
        p.end()


PEARL_PX = 30  # 진행 단계 진주 지름
DOT_PX = 22  # 진주가 없는 테마의 진행 단계 동그라미 지름
_PEARL_KEYS = {"current": "step_pearl_current", "done": "step_pearl_done", "todo": "step_pearl"}


def has_pearls(theme: Theme) -> bool:
    return all(theme.asset(k) for k in _PEARL_KEYS.values())


class StepChip(QWidget):
    """진행 단계 하나. 글자 모양만으로도 상태를 알 수 있게 그린다(색만으로 구분하지 않는다).

    번호는 넣지 않는다. 진주 에셋이 있으면: 지금 = 연분홍 진주와 굵은 이름, 끝남 = 흰 진주에 로즈골드 체크(그림),
    남음 = 흐린 빈 흰 진주. 없으면(단정한 모니터) 같은 자리에 코드로 그린 동그라미(paint_status): 끝남 = 채움과 체크,
    지금 = 굵은 테두리와 가운데 점, 남음 = 가는 테두리. 알약 모양은 쓰지 않는다(2026-11-08).
    """

    def __init__(self, theme: Theme, index: int, parent=None):
        super().__init__(parent)
        self.theme, self.index, self.state, self.name = theme, index, "todo", ""
        self.setFont(font("small"))
        self.setFixedHeight(34)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt 이름 규칙)
        self.name = text
        self._changed()

    def set_state(self, state: str, theme: Theme | None = None) -> None:
        self.state = state
        self.theme = theme or self.theme
        self._changed()

    def _label_font(self):
        f = font("small")
        f.setWeight(QFont.Weight(700 if self.state == "current" else semibold_weight()))
        return f

    def _changed(self) -> None:
        self.setAccessibleName(f"{self.index + 1} {self.name}")
        text_w = QFontMetrics(self._label_font()).horizontalAdvance(self.name)
        dot = PEARL_PX if has_pearls(self.theme) else DOT_PX
        self.setFixedWidth(6 + dot + 8 + text_w + 6)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if has_pearls(self.theme):
            self._paint_pearl(p)
        else:
            self._paint_dot(p)
        p.end()

    def _paint_pearl(self, p: QPainter) -> None:
        t, state = self.theme, self.state
        dot = QRectF(6, (self.height() - PEARL_PX) / 2, PEARL_PX, PEARL_PX)
        pm = smooth_pixmap(t.asset(_PEARL_KEYS[state]), round(PEARL_PX * self.devicePixelRatioF()))
        p.save()
        if state == "todo":
            p.setOpacity(0.5)  # 남은 단계는 흐리게
        p.drawPixmap(dot, pm, QRectF(pm.rect()))
        p.restore()
        # 진주 안에는 번호를 넣지 않는다. 끝남은 그림 속 체크, 지금은 분홍 진주와 굵은 이름, 남음은 흐린 빈 진주
        p.setFont(self._label_font())
        p.setPen(QColor({"current": t["ink"], "done": t["ink_sub"], "todo": t["step_todo_text"]}[state]))
        p.drawText(QRectF(dot.right() + 8, 0, self.width() - dot.right() - 8, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.name)

    def _paint_dot(self, p: QPainter) -> None:
        t, state = self.theme, self.state
        paint_status(p, QPointF(6 + DOT_PX / 2, self.height() / 2), DOT_PX, state, t)
        p.setFont(self._label_font())
        p.setPen(QColor({"current": t["ink"], "done": t["ink_sub"], "todo": t["step_todo_text"]}[state]))
        p.drawText(QRectF(6 + DOT_PX + 8, 0, self.width() - DOT_PX - 14, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.name)


class StepLine(QWidget):
    """진행 단계 사이 짧은 선. 지나온 쪽은 진하게(핑크는 로즈골드), 남은 쪽은 흐리게."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 34)
        self.color = None

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.color:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.color))
            p.drawRoundedRect(QRectF(0, self.height() / 2 - 1, self.width(), 2), 1, 1)
            p.end()


class StepBar(QWidget):
    def __init__(self, theme: Theme, binder: Binder, keys: list[str], parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.chips, self.lines = [], []
        for i, key in enumerate(keys):
            if i:
                line = StepLine()
                lay.addWidget(line)
                self.lines.append(line)
            chip = StepChip(theme, i)
            binder.text(chip, key)
            lay.addWidget(chip)
            self.chips.append(chip)

    def set_current(self, index: int, theme: Theme) -> None:
        for i, chip in enumerate(self.chips):
            chip.set_state("done" if i < index else "current" if i == index else "todo", theme)
        for i, line in enumerate(self.lines, 1):  # 단계 사이 선: 지나온 쪽은 진하게, 남은 쪽은 흐리게
            line.color = theme["step_done_border"] if i <= index else theme["step_todo_border"]
            line.update()


class ProgressRing(QWidget):
    """판정 진행률 원.

    에셋이 있으면: 흰 진주 띠(바탕) 위에 같은 크기의 연분홍 진주 띠(채움)를 12시부터 진행한 각도까지만 드러낸다.
    코드는 각도만 정한다. 없으면 색 토큰으로 고리와 호를 그린다.
    """

    SIZE = 116

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme, self.done, self.total = theme, 0, 0
        self.setFixedSize(self.SIZE, self.SIZE)

    def set_value(self, done: int, total: int, theme: Theme | None = None) -> None:
        self.done, self.total = done, total
        self.theme = theme or self.theme
        self.setAccessibleName(f"{round(self.ratio() * 100)}%")
        self.update()

    def ratio(self) -> float:
        return self.done / self.total if self.total else 0

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ratio = self.ratio()
        base, fill = t.asset("progress_band"), t.asset("progress_band_fill")
        if base and fill:
            px = round(self.SIZE * self.devicePixelRatioF())
            box = QRectF(self.rect())
            band = smooth_pixmap(base, px)
            p.drawPixmap(box, band, QRectF(band.rect()))
            if ratio:
                pie = QPainterPath(box.center())
                pie.arcTo(box.adjusted(-2, -2, 2, 2), 90, -360 * ratio)  # 12시에서 시계 방향
                pie.closeSubpath()
                p.save()
                p.setClipPath(pie)
                pink = smooth_pixmap(fill, px)
                p.drawPixmap(box, pink, QRectF(pink.rect()))
                p.restore()
        else:
            r = QRectF(8, 8, self.width() - 16, self.height() - 16)
            p.setPen(QPen(QColor(t["band_line"]), 9))
            p.drawEllipse(r)
            if ratio:
                pen = QPen(QColor(t["accent"]), 9)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawArc(r, 90 * 16, int(-360 * 16 * ratio))
        p.setPen(QColor(t["ink"]))
        f = font("heading")
        f.setPixelSize(20)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, f"{round(ratio * 100)}%")
        p.end()


def scaled_asset(path, height: int) -> QPixmap:
    """작은 장식(왕관 등)을 화면 높이 height px로. 고해상도 화면에서도 또렷하게 화면 배율만큼 크게 줄인다."""
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    img = QImage(str(path))
    pm = QPixmap.fromImage(img.scaledToHeight(round(height * ratio), Qt.TransformationMode.SmoothTransformation))
    pm.setDevicePixelRatio(ratio)
    return pm


class SparkleOverlay(QLabel):
    """작전 개시 직후 반짝임(핑크 테마). 창 가운데에서 한 번 재생하고 사라진다. 마우스를 가로채지 않는다."""

    finished = Signal()
    SHARE = 0.6  # 창 너비 대비 크기

    def __init__(self, path, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")
        self.movie = QMovie(str(path), parent=self)
        self.movie.jumpToFrame(0)
        size = self.movie.currentImage().size()
        w = int(parent.width() * self.SHARE)
        h = int(w * size.height() / max(1, size.width()))
        self.movie.setScaledSize(QSize(w, h))
        self.setMovie(self.movie)
        self.setGeometry((parent.width() - w) // 2, (parent.height() - h) // 2, w, h)
        self.movie.frameChanged.connect(self._frame)
        self.hide()

    def play(self) -> None:
        self.show()
        self.raise_()
        self.movie.start()
        QTimer.singleShot(8000, self.stop)  # 장 수를 모르는 형식이어도 끝나게

    def _frame(self, n: int) -> None:
        if n >= self.movie.frameCount() - 1 > 0:
            self.stop()

    def stop(self) -> None:
        if self.isVisible():
            self.movie.stop()
            self.hide()
            self.finished.emit()


class FlashOverlay(QWidget):
    """작전 개시 직후 짧은 화면 깜빡임(단정한 모니터 테마, 기획서 6장). 0.3초 동안 밝게 덮었다가 사라진다."""

    finished = Signal()

    def __init__(self, theme: Theme, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(f"background: {theme['paper']};")
        self.setGeometry(parent.rect())
        self.fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.fx)
        self.anim = QPropertyAnimation(self.fx, b"opacity", self)
        self.anim.setDuration(300)
        self.anim.setStartValue(0.6)
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self._done)
        self.hide()

    def play(self) -> None:
        self.show()
        self.raise_()
        self.anim.start()

    def _done(self) -> None:
        self.hide()
        self.finished.emit()


def scaled_width(path, width: int) -> QPixmap:
    """큰 그림(시작 화면)을 화면 너비 width px로."""
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    img = QImage(str(path))
    pm = QPixmap.fromImage(img.scaledToWidth(round(width * ratio), Qt.TransformationMode.SmoothTransformation))
    pm.setDevicePixelRatio(ratio)
    return pm
