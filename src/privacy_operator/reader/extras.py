"""kordoc이 읽지 않는 곳을 형식별 라이브러리로 보충한다(docs/kordoc_검증기록.md).

- XLSX: 셀 메모, 숨김 시트 표시, 문서 속성 전체 (openpyxl)
- PDF: 주석, 문서 속성 전체 (PyMuPDF)
- 사각지대: PDF는 쪽마다 이미지와 글자층을 직접 본다(kordoc의 needsOcr는 글자가 적은 쪽도 잡는다).
  XLSX, HWPX는 zip 안의 도형, 차트, 그림 파일을 본다.
"""

from __future__ import annotations

import dataclasses
import zipfile
from pathlib import Path

from privacy_operator.reader.blocks import COMMENT, BlindSpot, Block, Location
from privacy_operator.reader.convert import metadata_blocks

# 사각지대 종류
NEEDS_OCR = "needs_ocr"  # 글자층 없이 이미지만 있는 쪽(스캔)
IMAGE = "image"  # 이미지 속 글자는 검사하지 않음
DRAWING = "drawing"  # 엑셀 도형, 차트 속 글자

_XLSX_PROPS = ("creator", "lastModifiedBy", "title", "subject", "description", "keywords", "category")


def xlsx_extras(path: Path, blocks: list[Block]) -> list[Block]:
    from openpyxl import load_workbook

    wb = load_workbook(path)  # 메모는 read_only 모드에서 읽히지 않는다
    try:
        hidden = {ws.title for ws in wb.worksheets if ws.sheet_state != "visible"}
        out = [dataclasses.replace(b, hidden=True) if b.location.sheet in hidden else b for b in blocks]

        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    cm = cell.comment
                    if cm is None:
                        continue
                    for fld, text in (("text", cm.text), ("author", cm.author)):
                        if text and text.strip():
                            out.append(Block(f"xcm-{ws.title}-{cell.coordinate}-{fld}", COMMENT, text,
                                             Location(sheet=ws.title, cell=cell.coordinate, field=fld),
                                             hidden=ws.title in hidden))

        props = {k: getattr(wb.properties, k, None) for k in _XLSX_PROPS}
        out.extend(_new_metadata(out, metadata_blocks(props, prefix="xprop")))
        return out
    finally:
        wb.close()


def pdf_extras(path: Path, blocks: list[Block]) -> list[Block]:
    import pymupdf

    out = list(blocks)
    with pymupdf.open(path) as doc:
        for pno, page in enumerate(doc, start=1):
            for ai, annot in enumerate(page.annots() or []):
                info = annot.info
                for fld, key in (("text", "content"), ("author", "title"), ("subject", "subject")):
                    text = info.get(key) or ""
                    if text.strip():
                        out.append(Block(f"pan-p{pno:03d}-{ai:03d}-{fld}", COMMENT, text,
                                         Location(page=pno, field=fld)))
        meta = {k: v for k, v in (doc.metadata or {}).items() if k not in ("format", "encryption")}
        out.extend(_new_metadata(out, metadata_blocks(meta, prefix="pprop")))
    return out


def pdf_blind_spots(path: Path) -> list[BlindSpot]:
    import pymupdf

    out = []
    with pymupdf.open(path) as doc:
        for pno, page in enumerate(doc, start=1):
            images = len(page.get_images())
            if not images:
                continue
            if not page.get_text().strip():
                out.append(BlindSpot(NEEDS_OCR, page=pno, detail=str(images)))
            else:
                out.append(BlindSpot(IMAGE, page=pno, detail=str(images)))
    return out


def zip_blind_spots(path: Path, fmt: str) -> list[BlindSpot]:
    """XLSX의 도형, 차트, 그림과 HWPX의 그림(BinData)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        return []
    out = []
    if fmt == "xlsx":
        drawings = [n for n in names if n.startswith(("xl/drawings/drawing", "xl/charts/chart"))
                    and n.endswith(".xml")]
        media = [n for n in names if n.startswith("xl/media/")]
        if drawings:
            out.append(BlindSpot(DRAWING, detail=str(len(drawings))))
        if media:
            out.append(BlindSpot(IMAGE, detail=str(len(media))))
    elif fmt == "hwpx":
        media = [n for n in names if n.startswith("BinData/") and not n.endswith("/")]
        if media:
            out.append(BlindSpot(IMAGE, detail=str(len(media))))
    return out


def _new_metadata(existing: list[Block], extra: list[Block]) -> list[Block]:
    """kordoc이 이미 준 속성과 이름과 값이 같은 것은 빼고 돌려준다."""
    seen = {(b.location.field, b.text) for b in existing if b.kind == "metadata"}
    return [b for b in extra if (b.location.field, b.text) not in seen]
