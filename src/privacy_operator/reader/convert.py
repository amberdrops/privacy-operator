"""kordoc 결과 JSON(result.raw)을 공통 블록으로 바꾼다. 엔진 없이 시험할 수 있게 순수 함수로 둔다."""

from __future__ import annotations

from typing import Any

from privacy_operator.detect.dictionaries import (
    ADDRESS_HEADERS,
    FORMAT_HEADERS,
    GRAY_HEADERS,
    NAME_HEADER_SUFFIXES,
    NAME_HEADERS,
    normalize_header,
)
from privacy_operator.reader.blocks import (
    CELL,
    COMMENT,
    HEADING,
    METADATA,
    PARAGRAPH,
    BlindSpot,
    Block,
    Location,
)

# kordoc metadata 중 사람이 쓴 글이 아닌 칸
_NON_TEXT_METADATA = {"pageCount", "pageMode", "createdAt", "modifiedAt", "version"}


def _is_header_word(text: str) -> bool:
    """'강사명', '성명', '연락처'처럼 탐지 규칙이 아는 칸 제목인가."""
    h = normalize_header(text or "")
    return bool(h) and (h in NAME_HEADERS or h.endswith(NAME_HEADER_SUFFIXES) or h in FORMAT_HEADERS
                        or h in ADDRESS_HEADERS or h in GRAY_HEADERS)


def header_rows(cells: list[list[dict]], has_header: bool) -> list[int]:
    """머리글 행 번호(위에서부터).

    kordoc이 isHeader를 맨 윗줄에만 붙이는 두 줄 머리글이 있다(맨 윗줄에 합친 큰 제목, 아랫줄에 '강사명' 같은
    작은 제목). 바로 윗 머리글 줄에 가로로 합친 칸이 있고 아랫줄에 아는 칸 제목이 있으면 그 줄도 머리글로 본다.
    아는 칸 제목이 있을 때만 넓혀서, 보통 데이터 줄을 머리글로 착각하지 않는다(2026-11-08, 실제 연수 문서의
    강사명 칸 아래 이름 네 개를 놓쳤다).
    """
    rows = [r for r, row in enumerate(cells) if any(c.get("isHeader") for c in row)]
    if not rows and has_header and cells:
        rows = [0]
    while rows:
        nxt = rows[-1] + 1
        if nxt >= len(cells) or nxt in rows:
            break
        merged_above = any(int(c.get("colSpan") or 1) > 1 for c in cells[rows[-1]])
        if not (merged_above and any(_is_header_word(c.get("text")) for c in cells[nxt])):
            break
        rows.append(nxt)
    return rows


def column_headers(cells: list[list[dict]], has_header: bool) -> dict[int, tuple[str, ...]]:
    """열 번호마다 위에서부터의 머리글 글자 목록.

    kordoc은 표를 행×열 격자로 주고, 병합된 칸은 시작 칸에만 글자와 colSpan을 둔다.
    그래서 머리글의 colSpan이 덮는 열에도 같은 제목을 붙인다.
    """
    headers: dict[int, list[str]] = {}
    for r in header_rows(cells, has_header):
        for c, cell in enumerate(cells[r]):
            text = (cell.get("text") or "").strip()
            if not text:
                continue
            for cc in range(c, c + max(1, int(cell.get("colSpan") or 1))):
                headers.setdefault(cc, []).append(text)
    return {c: tuple(v) for c, v in headers.items()}


def _header_row_set(cells: list[list[dict]], has_header: bool) -> set[int]:
    return set(header_rows(cells, has_header))


def _a1(row0: int, col0: int) -> str:
    letters = ""
    n = col0 + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row0 + 1}"


def _bbox(raw_block: dict) -> tuple[float, float, float, float] | None:
    b = raw_block.get("bbox")
    if not b:
        return None
    return (b.get("x", 0), b.get("y", 0), b.get("width", 0), b.get("height", 0))


def _left_text(row: list[dict], c: int) -> str | None:
    """같은 줄에서 글자가 있는 가장 가까운 왼쪽 칸. 결재란, 신청서처럼 이름표가 왼쪽에 있는 표에서 쓴다."""
    for left in reversed(row[:c]):
        text = (left.get("text") or "").strip()
        if text:
            return text
    return None


def convert(raw: dict[str, Any], fmt: str) -> tuple[list[Block], list[BlindSpot]]:
    blocks: list[Block] = []
    blind: list[BlindSpot] = []
    table_index = -1
    sheet: str | None = None  # XLSX: 표 앞의 heading이 시트 이름이다

    for bi, rb in enumerate(raw.get("blocks") or []):
        btype = rb.get("type")
        page = rb.get("pageNumber")
        if btype in ("paragraph", "heading", "list", "quote"):
            text = rb.get("text") or ""
            if fmt == "xlsx" and btype == "heading":
                sheet = text
            if text.strip():
                kind = HEADING if btype == "heading" else PARAGRAPH
                blocks.append(Block(f"b{bi:04d}", kind, text,
                                    Location(page=page, block_index=bi, bbox=_bbox(rb), sheet=sheet)))
        elif btype == "table":
            table_index += 1
            t = rb.get("table") or {}
            cells = t.get("cells") or []
            headers = column_headers(cells, bool(t.get("hasHeader")))
            header_rows = _header_row_set(cells, bool(t.get("hasHeader")))
            src_rows, src_cols = t.get("sourceRows"), t.get("sourceCols")
            for r, row in enumerate(cells):
                for c, cell in enumerate(row):
                    text = cell.get("text") or ""
                    if not text.strip():
                        continue
                    path = headers.get(c, ())
                    a1 = None
                    if fmt == "xlsx" and src_rows and src_cols and r < len(src_rows) and c < len(src_cols):
                        a1 = _a1(src_rows[r], src_cols[c])
                    loc = Location(page=page, block_index=bi, table_index=table_index, row=r, col=c,
                                   header=path[-1] if path else None, header_path=path,
                                   row_label=_left_text(row, c),
                                   sheet=sheet, cell=a1, bbox=_bbox(rb))
                    blocks.append(Block(f"t{table_index:03d}-r{r:03d}-c{c:03d}", CELL, text, loc,
                                        is_header=r in header_rows))
        elif btype == "image":
            blind.append(BlindSpot("image", page=page, detail="1"))  # detail은 개수(화면 문구는 말투표에서)
        # 그 밖의 블록(구분선 등)은 글자가 없다

    for i, memo in enumerate(raw.get("memos") or []):
        anchor_block = memo.get("blockIndex")
        for fld in ("text", "author"):
            text = memo.get(fld) or ""
            if text.strip():
                blocks.append(Block(f"memo{i:03d}-{fld}", COMMENT, text,
                                    Location(block_index=anchor_block, field=fld)))

    blocks.extend(metadata_blocks(raw.get("metadata") or {}, prefix="meta"))

    for pq in raw.get("pageQuality") or []:
        if pq.get("needsOcr"):
            blind.append(BlindSpot("needs_ocr", page=pq.get("page"), detail="1"))
    images = raw.get("images") or []
    if images:
        blind.append(BlindSpot("image", detail=str(len(images))))
    for w in raw.get("warnings") or []:
        code = w.get("code") if isinstance(w, dict) else None
        blind.append(BlindSpot("engine_warning", page=w.get("page") if isinstance(w, dict) else None,
                               detail=str(code or "warning")))
    return blocks, blind


def metadata_blocks(meta: dict[str, Any], prefix: str) -> list[Block]:
    out = []
    for key, value in meta.items():
        if key in _NON_TEXT_METADATA or not isinstance(value, str) or not value.strip():
            continue
        out.append(Block(f"{prefix}-{key}", METADATA, value, Location(field=key)))
    return out
