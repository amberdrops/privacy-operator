"""Microsoft Store 로고 그림(Partner Center '스토어 목록 → Microsoft Store 로고').

    .venv\\Scripts\\python tools/store_logos.py      → dist/store/logo_*.png

- 9:16 포스터 아트 1440x2160, 1:1 박스 아트 2160x2160: 핑크 테마 바탕에 하트 골드림, 그 위 왕관, 아래 프로그램 이름
  (왕관 원본이 작아 두 배까지만 키운다. 하트는 assets/pink/heart-set@2x.png, 1433px)
- 1:1 앱 타일 아이콘 300, 150, 71: 투명 바탕에 왕관만
왕관은 Flow 원본(assets/_source/pink/crown-heart.jpg)에서 tools/assets.py와 같은 방법으로 원본 크기 그대로 잘라 쓴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
OUT = ROOT / "dist" / "store"
NAME = "개인정보작전실"


def crown() -> Image.Image:
    from assets import SRC, _crop, _rgba

    a = _rgba(SRC / "pink" / "crown-heart.jpg")
    cols = (a[..., 3] > 128).any(axis=0)
    xs = np.where(cols)[0]
    gap = next(x for x in range(xs[0], xs[-1]) if not cols[x] and cols[x + 1:x + 12].sum() == 0)  # 왕관과 하트 사이
    part = np.zeros_like(a)
    part[:, :gap] = a[:, :gap]
    return Image.fromarray(np.clip(_crop(part), 0, 255).astype(np.uint8), "RGBA")


def fit(img: Image.Image, w: int, h: int) -> Image.Image:
    s = min(w / img.width, h / img.height)
    return img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)


def icon(src: Image.Image, px: int) -> Image.Image:
    canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    c = fit(src, int(px * 0.86), int(px * 0.86))
    canvas.paste(c, ((px - c.width) // 2, (px - c.height) // 2), c)
    return canvas


def art(src: Image.Image, w: int, h: int) -> Image.Image:
    """핑크 테마 바탕에 왕관, 하트 골드림, 이름. 글자는 앱과 같은 Noto Sans KR."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
    from PySide6.QtWidgets import QApplication

    from privacy_operator.ui.style import load_bundled_fonts
    from privacy_operator.ui.theme import load_theme

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    load_bundled_fonts()
    t = load_theme("pink")
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(t["band"]))
    g.setColorAt(1, QColor(t["paper_head"]))
    p.fillRect(img.rect(), g)
    def q(im: Image.Image) -> QImage:
        return QImage(im.tobytes("raw", "RGBA"), im.width, im.height, QImage.Format.Format_RGBA8888).copy()

    heart = Image.open(ROOT / "assets" / "pink" / "heart-set@2x.png").convert("RGBA")
    hh = fit(heart, int(w * 0.56), int(h * (0.30 if h > w else 0.40)))
    c = src.resize((src.width * 2, src.height * 2), Image.LANCZOS)  # 두 배까지만
    gap = int(w * 0.02)
    block = c.height + gap + hh.height
    top = int(h * 0.44) - block // 2
    p.drawImage((w - c.width) // 2, top, q(c))
    p.drawImage((w - hh.width) // 2, top + c.height + gap, q(hh))
    name_top = top + block + min(w, h) * 0.05  # 이름은 하트 아래
    f = QFont("Noto Sans KR")
    f.setPixelSize(int(min(w, h) * 0.095))
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QColor(t["ink"]))
    p.drawText(QRectF(0, name_top, w, min(w, h) * 0.16), Qt.AlignmentFlag.AlignHCenter, NAME)
    p.end()
    out = Image.frombuffer("RGBA", (w, h), bytes(img.constBits()), "raw", "BGRA", 0, 1)
    return out.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    c = crown()
    print("왕관 원본", c.size)
    art(c, 1440, 2160).save(OUT / "logo_poster_1440x2160.png")
    art(c, 2160, 2160).save(OUT / "logo_box_2160x2160.png")
    for px in (300, 150, 71):
        icon(c, px).save(OUT / f"logo_tile_{px}x{px}.png")
    print(OUT)


if __name__ == "__main__":
    main()
