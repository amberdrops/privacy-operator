"""가명처리된 블록을 AI가 읽기 좋은 마크다운으로 쓴다.

- 문서 속성과 메모는 넣지 않는다. 숨김 시트도 기본으로 넣지 않는다.
- 쓰기 전에 차단 목록 검사를 한다. 걸리면 파일을 만들지 않는다.
- 금고 안에는 쓰지 않는다(절대 규칙 7).
"""

from __future__ import annotations

from pathlib import Path

from privacy_operator.alias.pseudonymize import PseudoDocument
from privacy_operator.detect.finding import Finding
from privacy_operator.reader.blocks import CELL, COMMENT, HEADING, METADATA, Block
from privacy_operator.writer.blocklist import check_blocklist


class UnsafeOutputPath(ValueError):
    pass


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


def to_markdown(blocks: list[Block], include_hidden: bool = False) -> str:
    parts: list[str] = []
    tables: dict[int, list[Block]] = {}
    order: list[tuple[str, object]] = []
    for b in blocks:
        if b.kind in (METADATA, COMMENT) or (b.hidden and not include_hidden):
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


def write_markdown(doc: PseudoDocument, out_dir: str | Path, findings: list[Finding],
                   vault_root: str | Path, include_hidden: bool = False) -> Path:
    out_dir, vault_root = Path(out_dir).resolve(), Path(vault_root).resolve()
    if out_dir == vault_root or vault_root in out_dir.parents:
        raise UnsafeOutputPath("금고 안에는 가명처리 문서를 저장할 수 없습니다")
    text = to_markdown(doc.blocks, include_hidden)
    check_blocklist(text, findings, doc.bundles)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (Path(doc.source_name).stem + ".md")
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
