"""글자 규칙과 스타일시트. 색은 테마 토큰에서만 가져온다.

PySide6에서 px 단위는 QFont.setPixelSize와 스타일시트 px로 지정한다. 모든 글자 15px 이상.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QProxyStyle, QPushButton, QStyle

from privacy_operator.ui.theme import ASSET_PREFIX, Theme, assets_dir

ASSETS_FONTS = Path(__file__).resolve().parents[3] / "assets" / "fonts"
# 글꼴은 Noto Sans KR(가변, assets/fonts에 함께 싣는다). Pretendard를 써 봤지만 Noto가 더 예쁘다(2026-11-10 사용자 결정)
FAMILIES = ("Noto Sans KR", "Malgun Gothic")
MONO = ("Consolas", "D2Coding", "Malgun Gothic")

# 쓰임 → (px, 굵기). "semi"는 SemiBold이지만 글꼴에 따라 바뀐다(semibold_weight)
SIZES = {
    "bar_title": (19, QFont.Weight.Bold),
    "body": (16, QFont.Weight.Medium),
    "button": (16, "semi"),
    "small": (15, QFont.Weight.Medium),
    "primary": (17, QFont.Weight.Bold),
    "heading": (18, "semi"),
    "drop": (22, "semi"),
}
MIN_PX = 15
BUTTON_HEIGHT = 44  # 누를 수 있는 단추의 높이(디자인 명세)
CHECK_PX = 22  # 체크박스 그림 한 변
PRIMARY_GAP = 16  # 주 단추 진주 끝(둥근 테두리)과 글자 사이. 10이면 켜진 단추에서 첫 글자가 테두리에 닿아 보였다


def load_bundled_fonts() -> None:
    """assets/fonts/의 Noto Sans KR을 등록한다(없으면 그 PC에 깔린 Noto Sans KR, 맑은 고딕)."""
    if ASSETS_FONTS.is_dir():
        for p in sorted(ASSETS_FONTS.glob("*.[ot]tf")):
            QFontDatabase.addApplicationFont(str(p))
    _families.clear()


# 고른 글꼴 이름. 표를 그릴 때마다 font()를 부르므로 깔린 글꼴 목록을 매번 훑지 않게 담아 둔다
_families: dict[tuple[str, ...], str] = {}


def _pick(wanted: tuple[str, ...]) -> str:
    if wanted not in _families:
        installed = set(QFontDatabase.families())
        _families[wanted] = next((f for f in wanted if f in installed), wanted[-1])
    return _families[wanted]


def family() -> str:
    return _pick(tuple(FAMILIES))


def mono_family() -> str:
    return _pick(tuple(MONO))


def semibold_weight() -> int:
    """SemiBold 자리에 Bold(700). Noto Sans KR 가변 글꼴은 600에서 획이 깨진다."""
    return 700


def font(use: str = "body", mono: bool = False) -> QFont:
    px, weight = SIZES[use]
    f = QFont(mono_family() if mono else family())
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(semibold_weight()) if weight == "semi" else weight)
    f.setHintingPreference(hinting())
    return f


def hinting() -> QFont.HintingPreference:
    """획을 화소 격자에 맞춘다. Noto Sans KR 가변 글꼴은 125%, 150% 배율에서 이래야 가장자리가 번지지 않고 또렷하다.
    (Pretendard는 이렇게 하면 글자가 우글거려 세로만 맞췄었다, 2026-11-09)"""
    return QFont.HintingPreference.PreferFullHinting


def register_assets() -> None:
    """스타일시트의 `"asset:<테마>/<파일>"`이 assets/ 폴더를 가리키게 한다."""
    QDir.setSearchPaths(ASSET_PREFIX, [str(assets_dir())])


# ---------------------------------------------------------------- 체크박스 에셋

_scaled: dict[tuple[str, int], QPixmap] = {}


def smooth_pixmap(path, px: int) -> QPixmap:
    """큰 에셋을 한 변 px(기기 픽셀)로 부드럽게 줄여 담아 둔다. 그릴 때마다 줄이면 거칠고 느리다."""
    key = (str(path), px)
    if key not in _scaled:
        img = QImage(str(path)).scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
        _scaled[key] = QPixmap.fromImage(img)
    return _scaled[key]


def paint_check(p: QPainter, rect: QRectF, state: Qt.CheckState, t: Theme, enabled: bool = True) -> None:
    """rect 가운데에 체크박스 그림(켜짐 = 진주 타일, 꺼짐 = 로즈골드 테두리, 반쯤 = 테두리 + 가로 막대).
    테마에 그림이 없으면 화면 색으로 그린다(_paint_plain_check)."""
    on, off = t.asset("check_on"), t.asset("check_off")
    box = QRectF(rect.center().x() - CHECK_PX / 2, rect.center().y() - CHECK_PX / 2, CHECK_PX, CHECK_PX)
    if not (on and off):
        _paint_plain_check(p, box, state, t, enabled)
        return
    ratio = p.device().devicePixelRatioF() if p.device() else 1.0
    pm = smooth_pixmap(on if state == Qt.CheckState.Checked else off, round(CHECK_PX * ratio))
    p.save()
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not enabled:
        p.setOpacity(0.45)
    p.drawPixmap(box, pm, QRectF(pm.rect()))
    if state == Qt.CheckState.PartiallyChecked:
        bar = QRectF(box.center().x() - 5, box.center().y() - 1.5, 10, 3)
        grad = QLinearGradient(QPointF(0, bar.top()), QPointF(0, bar.bottom()))
        grad.setColorAt(0, QColor(t["metal"]))
        grad.setColorAt(1, QColor(t["metal_deep"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(bar, 1.5, 1.5)
    p.restore()


def _paint_plain_check(p: QPainter, box: QRectF, state: Qt.CheckState, t: Theme, enabled: bool) -> None:
    """그림이 없는 테마(단정한 모니터)의 체크박스. Windows 기본 모양 대신 화면 색으로 그린다.
    꺼짐 = 테두리, 켜짐 = 강조색 채움에 흰 체크, 반쯤 = 테두리에 강조색 가로 막대."""
    b = box.adjusted(2, 2, -2, -2)  # 그림 체크박스와 같은 22px 자리 안에 18px
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not enabled:
        p.setOpacity(0.45)
    if state == Qt.CheckState.Checked:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["accent"]))
        p.drawRoundedRect(b, 4, 4)
        pen = QPen(QColor(t["on_accent"]), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        w = b.width()
        p.drawPolyline([QPointF(b.left() + w * fx, b.top() + w * fy) for fx, fy in ((0.24, 0.52), (0.42, 0.7), (0.76, 0.32))])
    else:
        p.setPen(QPen(QColor(t["metal_deep"]), 1.5))
        p.setBrush(QColor(t["paper"]))
        p.drawRoundedRect(b.adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)
        if state == Qt.CheckState.PartiallyChecked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["accent"]))
            p.drawRoundedRect(QRectF(b.center().x() - 5, b.center().y() - 1.25, 10, 2.5), 1.2, 1.2)
    p.restore()


def paint_status(p: QPainter, center: QPointF, d: float, kind: str, t: Theme) -> None:
    """알약 없이 쓰는 작은 상태 동그라미(지름 d). 모양만으로 갈린다(색만으로 구분하지 않는다).

    진행 단계: done = 채운 동그라미에 흰 체크, current = 굵은 테두리에 가운데 점, todo = 가는 흐린 테두리.
    판정: pending = 빈 동그라미, confirmed = 채운 동그라미에 흰 체크, reference = 점선 동그라미, mixed = 반만 채움.
    """
    r = QRectF(center.x() - d / 2, center.y() - d / 2, d, d)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    line = max(1.5, d * 0.09)
    if kind in ("done", "confirmed"):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["accent"]))
        p.drawEllipse(r)
        pen = QPen(QColor(t["on_accent"]), max(1.6, d * 0.12))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPointF(r.left() + d * fx, r.top() + d * fy) for fx, fy in ((0.28, 0.52), (0.44, 0.67), (0.73, 0.36))])
    elif kind == "current":
        p.setPen(QPen(QColor(t["accent"]), line * 1.4))
        p.setBrush(QColor(t["paper"]))
        inset = line * 0.7
        p.drawEllipse(r.adjusted(inset, inset, -inset, -inset))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["accent"]))
        p.drawEllipse(center, d * 0.2, d * 0.2)
    elif kind == "reference":
        p.setPen(QPen(QColor(t["ink_sub"]), line, Qt.PenStyle.DotLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r.adjusted(line / 2, line / 2, -line / 2, -line / 2))
    elif kind == "mixed":
        inner = r.adjusted(line / 2, line / 2, -line / 2, -line / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["ink_sub"]))
        p.drawPie(inner, 90 * 16, 180 * 16)  # 왼쪽 반
        p.setPen(QPen(QColor(t["ink_sub"]), line))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(inner)
    else:  # pending, todo
        color = t["metal_deep"] if kind == "pending" else t["step_todo_border"]  # 대기는 회색, 색은 확정(채움)에만
        p.setPen(QPen(QColor(color), line if kind == "pending" else line * 0.9))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r.adjusted(line / 2, line / 2, -line / 2, -line / 2))
    p.restore()


class AssetStyle(QProxyStyle):
    """체크박스를 테마 그림(없으면 화면 색)으로 그린다. 표의 체크 칸, 대화상자 체크박스가 모두 이 길로 그려진다.
    스타일시트에 ::indicator 규칙이 없으면 Qt가 체크박스를 이 스타일에 맡긴다."""

    def __init__(self):
        super().__init__()
        self.theme: Theme | None = None

    def _ready(self) -> bool:
        return self.theme is not None  # 그림이 없는 테마도 화면 색으로 그린다(paint_check)

    def pixelMetric(self, metric, option=None, widget=None):  # noqa: N802
        if metric in (QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight) and self._ready():
            return CHECK_PX
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        if element in (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
                       QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck) and self._ready():
            s = option.state
            state = (Qt.CheckState.Checked if s & QStyle.StateFlag.State_On else
                     Qt.CheckState.PartiallyChecked if s & QStyle.StateFlag.State_NoChange else Qt.CheckState.Unchecked)
            paint_check(painter, QRectF(option.rect), state, self.theme, bool(s & QStyle.StateFlag.State_Enabled))
            return
        super().drawPrimitive(element, option, painter, widget)


_asset_style: AssetStyle | None = None


def install_asset_style(t: Theme) -> None:
    """앱에 AssetStyle을 한 번 끼우고, 테마가 바뀌면 그림만 바꾼다. 스타일시트를 넣기 전에 부른다."""
    global _asset_style
    app = QApplication.instance()
    if app is None:
        return
    if _asset_style is None:
        _asset_style = AssetStyle()
        app.setStyle(_asset_style)
    _asset_style.theme = t


# ---------------------------------------------------------------- 9분할 그림(베젤, 그림 단추)

_nine_cache: dict[tuple[str, float], QPixmap] = {}


def nine_pixmap(path, cut: list[int], border: list[int], ratio: float) -> tuple[QPixmap, list[int]]:
    """9분할 그림을 그릴 두께(border, 화면 px)에 맞춰 미리 부드럽게 줄인다(기기 픽셀 기준). 줄인 그림과 그 그림의 자를 곳.

    스타일시트 border-image나 그릴 때마다 줄이기는 큰 그림을 한 번에 6~10배 줄이면서 계단이 져 테두리가 우글거린다.
    미리 면적 평균으로 줄여 두면 모서리는 1:1로 찍히고 가운데만 늘어난다. 줄이는 비율은 왼쪽 두께 기준이다.
    """
    scale = border[3] * ratio / cut[3]
    key = (str(path), round(scale, 4))
    if key not in _nine_cache:
        img = QImage(str(path))
        _nine_cache[key] = QPixmap.fromImage(img.scaled(
            max(1, round(img.width() * scale)), max(1, round(img.height() * scale)),
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation))
    return _nine_cache[key], [max(1, round(c * scale)) for c in cut]


def draw_nine(p: QPainter, target: QRectF, pm: QPixmap, cut: list[int], border: list[int], fill: bool = False) -> None:
    """9분할로 그린다. cut은 그림 픽셀, border는 화면 두께(둘 다 위, 오른, 아래, 왼). fill이 아니면 가운데 칸은 비운다."""
    ct, cr, cb, cl = cut
    bt, br, bb, bl = border
    sw, sh = pm.width(), pm.height()
    xs = [0, cl, sw - cr, sw]
    ys = [0, ct, sh - cb, sh]
    x, y, w, h = target.x(), target.y(), target.width(), target.height()
    xd = [x, x + bl, x + w - br, x + w]
    yd = [y, y + bt, y + h - bb, y + h]
    for i in range(3):
        for j in range(3):
            if i == 1 and j == 1 and not fill:
                continue
            p.drawPixmap(QRectF(xd[j], yd[i], xd[j + 1] - xd[j], yd[i + 1] - yd[i]), pm,
                         QRectF(xs[j], ys[i], xs[j + 1] - xs[j], ys[i + 1] - ys[i]))


def button_art(t: Theme, btn: QPushButton) -> str | None:
    """이 단추에 깔 그림 열쇠. 주 단추(켜진 것만, 꺼진 것은 스타일시트의 회색 판), 이름 없는 단추는 모두 보조 단추 판.
    거르기, 이동 목록, 글자 단추처럼 이름이 있는 단추는 코드 모양 그대로다."""
    name = btn.objectName()
    if name == "Primary":
        if not btn.isEnabled() or not t.slices("btn_primary"):
            return None
        state = "btn_primary_pressed" if btn.isDown() else "btn_primary_hover" if (
            btn.underMouse() or btn.hasFocus()) else "btn_primary"
        return state if t.asset(state) else "btn_primary"
    if name or not t.slices("btn_secondary"):
        return None
    return "btn_secondary"  # 이름 없는 단추는 어디서나 같은 판(2026-11-08: 액션바 밖 단추가 테두리만 있는 알약으로 남았다)


class ArtButtons(QObject):
    """그림 단추의 바탕을 단추 글자보다 먼저 그린다(앱 전체 이벤트 거르개). 글자와 색은 스타일시트가 맡는다."""

    def __init__(self):
        super().__init__()
        self.theme: Theme | None = None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Paint and isinstance(obj, QPushButton) and self.theme is not None:
            key = button_art(self.theme, obj)
            if key:
                base = "btn_primary" if key.startswith("btn_primary") else key
                cut, border = self.theme.slices(base)
                pm, small = nine_pixmap(self.theme.asset(key), cut, border, obj.devicePixelRatioF())
                p = QPainter(obj)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                draw_nine(p, QRectF(obj.rect()), pm, small, border, fill=True)
                p.end()
        return False


_art_buttons: ArtButtons | None = None


def _install_art_buttons(t: Theme) -> None:
    global _art_buttons
    app = QApplication.instance()
    if app is None:
        return
    if _art_buttons is None:
        _art_buttons = ArtButtons()
        app.installEventFilter(_art_buttons)
        app.setFont(font("body"))  # 스타일시트 글자도 이 글꼴의 힌팅을 물려받는다
    _art_buttons.theme = t


def corner(t: Theme, pill: int, flat: int = 6) -> int:
    """핑크는 알약(높이의 절반), 단정한 모니터는 모서리만 살짝 둥근 판(그림 단추의 은빛 판과 같은 둥글기)."""
    return pill if t.decorated else flat


def _asset_rules(t: Theme) -> str:
    """Flow 에셋: 관제 띠는 질감. 그림 단추는 바탕을 ArtButtons가 그리고, 여기서는 테두리를 걷고 글자 색만 정한다.
    파일이 없으면 아무것도 덧붙이지 않는다(색 토큰으로 그린 기본 모양)."""
    rules = []
    chevron = t.asset_url("chevron")
    if chevron:  # 펼침 목록 화살표: 운영체제 삼각형 대신 가는 꺾쇠(글씨 회색 ink_sub)
        rules.append(f"QComboBox::down-arrow {{ image: url({chevron}); width: 14px; height: 14px; }}")
    tex = t.asset_url("band_texture")
    if tex:
        rules.append(f"#OperationBar, #ActionBar {{ background-color: {t['band']}; background-image: url({tex}); }}")
    sec = t.slices("btn_secondary")
    if sec:
        # 보조 단추 바탕은 이름 없는 단추 모두에 쓴다(확정, 제외, 자세히, 다시 찾기 등).
        # 상태 규칙마다 테두리를 다시 걷는다. 원래 규칙의 `border:`가 상태 규칙에서 되살아나기 때문이다
        _cut, border = sec
        scope = "QMainWindow QPushButton, QDialog QPushButton"  # 이름 있는 단추(#Nav 등)는 id 규칙이 이긴다
        for state, color in (("", t["ring_text"]), (":hover", t["ring_text_deep"]), (":focus", t["ring_text_deep"]),
                             (":pressed", t["ring_text_deep"]), (":disabled", t["ink_sub"])):
            sel = ", ".join(f"{s.strip()}{state}" for s in scope.split(","))
            rules.append(f"{sel} {{ border: none; background: transparent; min-height: {BUTTON_HEIGHT}px; "
                         f"padding: 0 {border[1] + 6}px; color: {color}; }}")
    pri = t.slices("btn_primary")
    if pri:
        # 주 단추는 보조 단추 규칙 뒤에 둔다. `#ActionBar QPushButton`과 `QPushButton#Primary`는 우선순위가 같아
        # 뒤에 온 규칙이 이긴다. 범위를 붙인 선택자도 함께 써서 대화상자 안에서도 이기게 한다
        _cut, border = pri
        sels = ("QPushButton#Primary", "#ActionBar QPushButton#Primary", "QDialog QPushButton#Primary")
        for state in ("", ":hover", ":focus", ":pressed"):
            rules.append(f"{', '.join(s + state for s in sels)} {{ border: none; background: transparent; "
                         f"min-height: {BUTTON_HEIGHT}px; padding: 0 {border[1] + PRIMARY_GAP}px; "
                         f"color: {t['on_primary']}; }}")
        rules.append(f"{', '.join(s + ':disabled' for s in sels)} {{ border-image: none; "
                     f"border: 2px solid {t['band_line']}; border-radius: {corner(t, 22)}px; background: {t['disabled_bg']}; "
                     f"color: {t['ink_sub']}; min-height: {BUTTON_HEIGHT - 4}px; padding: 0 {border[1] + PRIMARY_GAP - 2}px; }}")
    return "\n    ".join(rules)


def stylesheet(t: Theme) -> str:
    fam = family()
    semi = semibold_weight()
    register_assets()
    install_asset_style(t)
    _install_art_buttons(t)
    return f"""
    QWidget {{ font-family: "{fam}"; font-size: 16px; color: {t['ink']}; }}
    QMainWindow, QDialog {{ background: {t['bezel']}; }}
    QDialog {{ background: {t['paper']}; }}
    #DialogBody {{ background: {t['paper']}; }}
    #Screen {{ background: {t['paper']}; border: 2px solid {t['band_line']}; border-radius: 6px; }}
    #OperationBar, #ActionBar {{ background: {t['band']}; }}
    #OperationBar {{ border-bottom: 1px solid {t['band_line']}; }}
    #ActionBar {{ border-top: 1px solid {t['band_line']}; }}
    #SidePanel {{ background: {t['band_2']}; border: none; border-right: 1px solid {t['band_line']}; }}
    #SidePanelBody {{ background: {t['band_2']}; }}
    #PanelRule {{ background: {t['band_line']}; border: none; margin: 0 4px; }}
    #BlindBand {{ background: {t['paper_head']}; border-bottom: 1px solid {t['line']}; }}
    #Paper, #DropPage, #RestorePage, QStackedWidget {{ background: {t['paper']}; }}
    #BarTitle {{ font-size: 19px; font-weight: 700; }}
    #BarSub, #PanelLabel, #Guide {{ font-size: 15px; color: {t['ink_sub']}; }}
    #PanelLabel {{ color: {t['label']}; font-weight: {semi}; }}
    #Offline {{ font-size: 15px; color: {t['signal_text']}; font-weight: {semi}; }}
    #VaultState {{ font-size: 15px; color: {t['label']}; }}
    #BlindText {{ font-size: 16px; color: {t['ink']}; font-weight: {semi}; }}
    #DropTitle {{ font-size: 22px; font-weight: {semi}; color: {t['ink']}; }}
    #DropHint {{ font-size: 16px; color: {t['ink_sub']}; }}
    #DropArea {{ border: 2px dashed {t['metal']}; border-radius: 12px; background: {t['paper']}; }}
    #DropArea[hover="true"] {{ border-color: {t['accent']}; background: {t['row_selected']}; }}
    #Heading {{ font-size: 18px; font-weight: {semi}; }}
    #Summary {{ font-size: 16px; }}
    #MissionNo {{ font-size: 17px; font-weight: 700; color: {t['ink']}; }}

    QPushButton {{ font-size: 16px; font-weight: {semi}; min-height: 44px; padding: 0 18px;
                   border: 1.5px solid {t['accent']}; border-radius: {corner(t, 22)}px; background: {t['paper']};
                   color: {t['accent']}; }}
    QPushButton:hover {{ background: {t['row_selected']}; }}
    QPushButton:focus {{ border: 2px solid {t['focus']}; }}
    QPushButton:disabled {{ color: {t['ink_sub']}; border-color: {t['band_line']}; background: {t['disabled_bg']}; }}
    QPushButton#Primary {{ font-size: 17px; font-weight: 700; background: {t['accent']}; color: {t['on_accent']};
                           border: 2px solid {t['accent']}; padding: 0 28px; }}
    QPushButton#Primary:hover {{ background: {t['accent_deep']}; border-color: {t['accent_deep']}; }}
    QPushButton#Primary:focus {{ border: 2px solid {t['focus']}; }}
    QPushButton#Primary:disabled {{ background: {t['disabled_bg']}; color: {t['ink_sub']};
                                    border-color: {t['band_line']}; }}
    QPushButton#Nav {{ border: none; border-radius: 10px; background: transparent; color: {t['ink']};
                       text-align: left; padding-left: 14px; }}
    QPushButton#Nav:hover {{ background: {t['paper_head']}; }}
    QPushButton#Nav:focus {{ border: 2px solid {t['focus']}; }}
    QPushButton#Nav:disabled {{ background: transparent; color: {t['ink_sub']}; }}
    QPushButton#Nav[current="true"] {{ background: {t['row_selected']}; font-weight: 700; }}
    QPushButton#TextButton {{ border: none; background: transparent; color: {t['ink_sub']}; padding: 0 10px; }}
    QPushButton#TextButton:hover {{ color: {t['ink']}; background: transparent; }}
    QPushButton#TextButton:focus {{ border: 2px solid {t['focus']}; }}
    #ActionBar QPushButton#TextButton {{ border-image: none; border: none; background: transparent;
                                         color: {t['ink_sub']}; min-height: 44px; padding: 0 12px; }}
    #ActionBar QPushButton#TextButton:hover {{ border-image: none; color: {t['ink']}; }}
    #ActionBar QPushButton#TextButton:focus {{ border-image: none; border: 2px solid {t['focus']}; }}
    #ReviewBar {{ background: {t['paper']}; border-bottom: 1px solid {t['line']}; }}
    #Segment {{ background: {t['paper_head']}; border: 1px solid {t['line']}; border-radius: {corner(t, 22, 8)}px; }}
    QPushButton#Seg {{ border: 2px solid transparent; border-radius: {corner(t, 19)}px; min-height: 34px; padding: 0 14px; background: transparent;
                       color: {t['ink']}; font-weight: 400; }}
    QPushButton#Seg:hover {{ background: {t['row_selected']}; }}
    QPushButton#Seg:focus {{ border: 2px solid {t['focus']}; }}
    QPushButton#Seg:checked {{ background: {t['accent_fill']}; color: {t['on_accent_fill']}; font-weight: 700; }}
    QPushButton#Fold {{ border: none; background: transparent; color: {t['ink_sub']}; text-align: left;
                        padding-left: 12px; font-size: 16px; }}

    QTableView {{ background: {t['paper']}; gridline-color: {t['line']}; border: none;
                  selection-background-color: {t['row_selected']}; selection-color: {t['ink']}; }}
    QTableWidget::item {{ border: none; border-bottom: 1px solid {t['line']}; padding: 0 12px; }}
    QTableWidget::item:selected {{ background: {t['row_selected']}; color: {t['ink']}; }}
    QHeaderView::section {{ background: {t['paper_head']}; color: {t['ink_sub']}; font-size: 15px;
                            font-weight: {semi}; border: none; border-bottom: 1px solid {t['line']};
                            padding: 0 12px; min-height: 40px; }}
    QListWidget {{ background: transparent; border: none; font-size: 16px; }}
    QListWidget::item {{ min-height: 36px; padding-left: 6px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {t['row_selected']}; color: {t['ink']}; }}
    QLineEdit, QSpinBox, QComboBox {{ font-size: 16px; min-height: 40px; padding: 0 10px; background: {t['paper']};
                           border: 1px solid {t['line']}; border-radius: 6px; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 2px solid {t['focus']}; }}
    #PathField {{ font-size: 16px; min-height: 40px; padding: 0 10px; background: {t['paper_head']};
                  border: 1px solid {t['line']}; border-radius: 6px; color: {t['ink']}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}
    QComboBox::drop-down {{ border: none; width: 34px; subcontrol-origin: padding; subcontrol-position: center right; }}
    QComboBox QAbstractItemView {{ background: {t['paper']}; border: 1px solid {t['line']}; outline: 0;
                                   selection-background-color: {t['row_selected']}; selection-color: {t['ink']}; }}
    QCheckBox {{ font-size: 16px; spacing: 10px; }}
    QProgressBar {{ border: none; border-radius: 4px; background: {t['band_line']}; }}
    QProgressBar::chunk {{ border-radius: 4px; background: {t['accent']}; }}
    QLabel#Error {{ color: {t['accent_deep']}; font-size: 15px; font-weight: {semi}; }}
    QToolTip {{ font-size: 15px; color: {t['ink']}; background: {t['paper']}; border: 1px solid {t['metal']}; }}
    {_asset_rules(t)}
    """
