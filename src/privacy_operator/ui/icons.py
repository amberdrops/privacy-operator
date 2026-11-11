"""아이콘은 SVG로 그린다(이미지 생성 금지). 색은 부르는 쪽이 테마 토큰으로 넘긴다."""

from __future__ import annotations

import math

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG = {
    "heart": '<path d="M12 21 C5 15.5 2.5 12.5 2.5 8.8 C2.5 6 4.6 4 7.2 4 C9 4 10.9 5.1 12 6.8 '
             'C13.1 5.1 15 4 16.8 4 C19.4 4 21.5 6 21.5 8.8 C21.5 12.5 19 15.5 12 21 Z" fill="{c}"/>',
    # 대기 판정(핑크): 하트 보석과 같은 하트를 테두리로만
    "heart_outline": '<path d="M12 20.2 C5.6 15.2 3.4 12.4 3.4 9 C3.4 6.5 5.3 4.7 7.6 4.7 C9.2 4.7 10.9 5.7 12 7.3 '
                     'C13.1 5.7 14.8 4.7 16.4 4.7 C18.7 4.7 20.6 6.5 20.6 9 C20.6 12.4 18.4 15.2 12 20.2 Z" fill="none" '
                     'stroke="{c}" stroke-width="1.9" stroke-linejoin="round"/>',
    "power": '<circle cx="12" cy="12" r="7" fill="{c}"/>',
    # 열린 자물쇠: 고리 오른쪽이 몸통에서 떨어져 위로 들려 있다(금고 열림)
    "lock_open": '<rect x="5" y="10.5" width="14" height="10" rx="2" fill="{c}"/>'
                 '<path d="M8 10.5 V6.5 A4 4 0 0 1 16 6.5 V7.5" fill="none" stroke="{c}" stroke-width="2.2" '
                 'stroke-linecap="round"/>',
    "warning": '<path d="M12 3 L22 20.5 H2 Z" fill="{c}"/>'
               '<rect x="11" y="9" width="2" height="6.5" fill="{bg}"/><rect x="11" y="17" width="2" height="2" fill="{bg}"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12.5" rx="2" fill="none" stroke="{c}" stroke-width="2"/>'
               '<rect x="9" y="18.5" width="6" height="2" rx="1" fill="{c}"/>',
    # 작전 패널 목록용 선 아이콘
    "restore": '<g fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
               '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/></g>',
    "plus": '<path d="M12 5v14M5 12h14" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
}


def _gear(teeth: int = 8, outer: float = 10.2, inner: float = 7.6, hole: float = 3.0) -> str:
    """톱니바퀴 윤곽(톱니 teeth개). 이 위에 가운데 구멍을 뚫는다. 해 모양(가는 빛살)과 헷갈리지 않게 톱니를 넓게 한다."""
    pts = []
    step = 2 * math.pi / teeth
    for i in range(teeth):
        a = i * step - math.pi / 2
        for da, r in ((-0.30, inner), (-0.17, outer), (0.17, outer), (0.30, inner)):
            pts.append((12 + r * math.cos(a + da * step * 1.6), 12 + r * math.sin(a + da * step * 1.6)))
    d = "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z"
    return (f'<g fill="none" stroke="{{c}}" stroke-width="1.8" stroke-linejoin="round">'
            f'<path d="{d}"/><circle cx="12" cy="12" r="{hole}"/></g>')


_SVG["gear"] = _gear()


def svg(name: str, color: str, background: str = "none") -> str:
    body = _SVG[name].format(c=color, bg=background)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'


def pixmap(name: str, color: str, size: int, background: str = "none", ratio: float = 2.0) -> QPixmap:
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color, background).encode("utf-8")))
    painter = QPainter(px)
    renderer.render(painter, QRectF(0, 0, px.width(), px.height()))
    painter.end()
    px.setDevicePixelRatio(ratio)
    return px
