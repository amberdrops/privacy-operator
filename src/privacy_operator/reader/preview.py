"""사각지대(검사 못 한 영역)를 그림으로 보여 주기 위한 미리 보기(2026-11-15 사용자 요청).

글 목록만으로는 그 자리에 무엇이 있는지 알 수 없어, 원본에서 그 그림을 꺼내 화면에 보여 준다.
- PDF: 그 쪽을 그린다(스캔한 쪽, 그림이 든 쪽).
- XLSX, HWPX: 파일(zip) 안의 그림 파일을 그대로 꺼낸다. 화면이 그릴 수 있는 형식만(PNG, JPG, GIF, BMP).
- 도형과 차트, HWP 속 그림, 그 밖의 형식(EMF, WMF)은 보여 주지 못한다.

원본은 읽기만 한다. 그림은 메모리로만 돌려준다. 디스크에 쓰지 않고 로그에도 남기지 않는다.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from privacy_operator.reader.blocks import BlindSpot
from privacy_operator.reader.extras import IMAGE, NEEDS_OCR

SHOWABLE = (".png", ".jpg", ".jpeg", ".gif", ".bmp")
_MEDIA_DIR = {"xlsx": "xl/media/", "hwpx": "BinData/"}
PAGE_WIDTH = 1100  # PDF 쪽을 그릴 때의 가로 픽셀(화면에서 줄여 보여 준다)


def _media(path: Path, fmt: str) -> list[str]:
    """보여 줄 수 있는 그림 파일 이름. image2가 image10보다 먼저 오게 숫자 순으로."""
    prefix = _MEDIA_DIR.get(fmt)
    if not prefix:
        return []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.startswith(prefix) and n.lower().endswith(SHOWABLE)]
    return sorted(names, key=lambda n: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", n)])


def image_count(path: Path, fmt: str, spot: BlindSpot) -> int:
    """이 사각지대에서 보여 줄 수 있는 그림 수. 못 보여 주면 0."""
    try:
        if fmt == "pdf":
            return 1 if spot.kind in (NEEDS_OCR, IMAGE) and spot.page else 0
        return len(_media(path, fmt)) if spot.kind == IMAGE else 0
    except Exception:  # noqa: BLE001 - 미리 보기는 덤이다. 못 열면 글 안내만 남긴다
        return 0


def image_bytes(path: Path, fmt: str, spot: BlindSpot, index: int = 0) -> bytes | None:
    """index번째 그림(PNG 등 화면이 읽는 형식의 바이트). 못 만들면 None."""
    try:
        if fmt == "pdf":
            import pymupdf

            with pymupdf.open(path) as doc:
                page = doc[spot.page - 1]
                zoom = min(3.0, PAGE_WIDTH / max(1.0, page.rect.width))
                return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
        names = _media(path, fmt)
        if not 0 <= index < len(names):
            return None
        with zipfile.ZipFile(path) as z:
            return z.read(names[index])
    except Exception:  # noqa: BLE001
        return None
