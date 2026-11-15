"""kordoc이 읽지 않는 곳을 형식별 라이브러리로 보충한다(docs/kordoc_검증기록.md).

- XLSX: 셀 메모, 숨김 시트 표시, 문서 속성 전체 (openpyxl. 열지 못하면 zip 안의 XML에서 직접)
- PDF: 주석, 문서 속성 전체 (PyMuPDF)
- 사각지대: PDF는 쪽마다 이미지와 글자층을 직접 본다(kordoc의 needsOcr는 글자가 적은 쪽도 잡는다).
  XLSX, HWPX는 zip 안의 도형, 차트, 그림 파일을 본다.
"""

from __future__ import annotations

import dataclasses
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from privacy_operator.reader.blocks import COMMENT, BlindSpot, Block, Location
from privacy_operator.reader.convert import metadata_blocks

# 사각지대 종류
NEEDS_OCR = "needs_ocr"  # 글자층 없이 이미지만 있는 쪽(스캔)
IMAGE = "image"  # 이미지 속 글자는 검사하지 않음
DRAWING = "drawing"  # 엑셀 도형, 차트 속 글자

_XLSX_PROPS = ("creator", "lastModifiedBy", "title", "subject", "description", "keywords", "category")


def existing_cells(ws) -> list:
    """시트에 실제로 있는 칸(행, 열 순서). 병합에 가려진 칸은 뺀다.

    `ws.iter_rows()`는 줄마다 시트의 가장 오른쪽 열까지 빈 칸을 만들어 가며 돈다. 첫 행을 행 전체로 병합한
    시트(A1:XFD1)는 오른쪽 끝이 16,384열이라 300줄짜리 명단을 읽는 데 1분이 넘게 걸렸다(2026-11-15).
    """
    from openpyxl.cell.cell import MergedCell

    return [cell for _, cell in sorted(ws._cells.items()) if not isinstance(cell, MergedCell)]


def xlsx_extras(path: Path, blocks: list[Block]) -> list[Block]:
    """메모, 숨긴 시트 표시, 문서 속성. openpyxl이 열지 못하는 파일(서식 표가 어긋난 엑셀 등)은 zip 안의 XML에서
    같은 것을 직접 읽는다. 그런 파일도 읽을 수는 있게 한다(원본 형식 사본은 만들지 못해 마크다운만 나간다)."""
    try:
        return _xlsx_extras_openpyxl(path, blocks)
    except Exception:  # noqa: BLE001
        return _xlsx_extras_raw(path, blocks)


def _comment_blocks(sheet: str, cell: str, text: str | None, author: str | None, hidden: bool) -> list[Block]:
    return [Block(f"xcm-{sheet}-{cell}-{fld}", COMMENT, value, Location(sheet=sheet, cell=cell, field=fld),
                  hidden=hidden)
            for fld, value in (("text", text), ("author", author)) if value and value.strip()]


def _xlsx_extras_openpyxl(path: Path, blocks: list[Block]) -> list[Block]:
    from openpyxl import load_workbook

    wb = load_workbook(path)  # 메모는 read_only 모드에서 읽히지 않는다
    try:
        hidden = {ws.title for ws in wb.worksheets if ws.sheet_state != "visible"}
        out = [dataclasses.replace(b, hidden=True) if b.location.sheet in hidden else b for b in blocks]

        for ws in wb.worksheets:
            for cell in existing_cells(ws):
                cm = cell.comment
                if cm is not None:
                    out += _comment_blocks(ws.title, cell.coordinate, cm.text, cm.author, ws.title in hidden)

        props = {k: getattr(wb.properties, k, None) for k in _XLSX_PROPS}
        out.extend(_new_metadata(out, metadata_blocks(props, prefix="xprop")))
        return out
    finally:
        wb.close()


def _local(name: str) -> str:
    """XML 이름에서 이름공간을 뗀 것(`{…}sheet` → `sheet`). 한셀은 `x:` 같은 접두사를 붙여 쓴다."""
    return name.rsplit("}", 1)[-1]


def _xlsx_extras_raw(path: Path, blocks: list[Block]) -> list[Block]:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())

        def xml(name: str):
            return ElementTree.fromstring(z.read(name))

        def rels(part: str) -> dict[str, tuple[str, str]]:
            """part에 딸린 관계: Id → (종류, zip 안 경로). 파일 밖을 가리키는 것은 뺀다."""
            folder, base = posixpath.split(part)
            name = f"{folder}/_rels/{base}.rels"
            found = {}
            for r in xml(name) if name in names else ():
                target = r.get("Target") or ""
                if r.get("TargetMode") == "External" or not target:
                    continue
                full = target.lstrip("/") if target.startswith("/") else posixpath.normpath(f"{folder}/{target}")
                found[r.get("Id")] = ((r.get("Type") or "").rsplit("/", 1)[-1], full)
            return found

        book_rels = rels("xl/workbook.xml")
        sheets = []  # (시트 이름, 숨김, 시트 파일)
        for el in xml("xl/workbook.xml").iter():
            if _local(el.tag) == "sheet":
                rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), None)
                sheets.append((el.get("name") or "", (el.get("state") or "visible") != "visible",
                               book_rels.get(rid, ("", ""))[1]))
        hidden = {title for title, is_hidden, _ in sheets if is_hidden}
        out = [dataclasses.replace(b, hidden=True) if b.location.sheet in hidden else b for b in blocks]

        for title, is_hidden, part in sheets:
            for kind, target in rels(part).values() if part else ():
                if kind != "comments" or target not in names:
                    continue
                root = xml(target)
                authors = [a.text or "" for a in root.iter() if _local(a.tag) == "author"]
                for cm in root.iter():
                    if _local(cm.tag) != "comment":
                        continue
                    text = "".join(t.text or "" for t in cm.iter() if _local(t.tag) == "t")
                    aid = cm.get("authorId") or ""
                    author = authors[int(aid)] if aid.isdigit() and int(aid) < len(authors) else ""
                    out += _comment_blocks(title, cm.get("ref") or "", text, author, is_hidden)

        props = {}
        if "docProps/core.xml" in names:
            props = {_local(el.tag): el.text for el in xml("docProps/core.xml") if _local(el.tag) in _XLSX_PROPS}
        out.extend(_new_metadata(out, metadata_blocks(props, prefix="xprop")))
        return out


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
