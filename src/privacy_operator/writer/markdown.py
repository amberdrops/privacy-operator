"""가명처리된 블록을 AI가 읽기 좋은 마크다운 글로 바꾼다.

문서 속성과 메모, 숨김 시트는 넣지 않는다. 파일로 쓰는 일과 차단 목록 검사는 작전 패키지(mission/package.py)가 한다.
"""

from __future__ import annotations

from privacy_operator.reader.blocks import CELL, COMMENT, HEADING, METADATA, Block


def _cell_text(text: str) -> str:
    return text.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _table(cells: list[Block]) -> str:
    rows = max(b.location.row for b in cells) + 1
    cols = max(b.location.col for b in cells) + 1
    grid = [[""] * cols for _ in range(rows)]
    for b in cells:
        grid[b.location.row][b.location.col] = _cell_text(b.text)
    lines = ["| " + " | ".join(grid[0]) + " |", "| " + " | ".join(["---"] * cols) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
    return "\n".join(lines)


def to_markdown(blocks: list[Block]) -> str:
    parts: list[str] = []
    tables: dict[int, list[Block]] = {}
    order: list[tuple[str, object]] = []
    for b in blocks:
        if b.kind in (METADATA, COMMENT) or b.hidden:
            continue
        if b.kind == CELL:
            t = b.location.table_index
            if t not in tables:
                tables[t] = []
                order.append(("table", t))
            tables[t].append(b)
        else:
            order.append(("block", b))
    for kind, item in order:
        if kind == "table":
            parts.append(_table(tables[item]))
        elif item.kind == HEADING:
            parts.append("## " + item.text.strip())
        else:
            parts.append(item.text.strip())
    return "\n\n".join(p for p in parts if p) + "\n"
