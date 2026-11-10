"""샘플 정답 목록(samples/*.answers.json)을 다루는 도움 함수. 실패 메시지에는 answer_id만 쓴다."""

from __future__ import annotations

import json
from pathlib import Path

from privacy_operator.reader.blocks import CELL, COMMENT, HEADING, METADATA, PARAGRAPH

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SAMPLE_FILES = ["연수운영계획_샘플.hwpx", "연수운영계획_샘플.hwp", "만족도조사_샘플.xlsx", "강사계약_샘플.pdf"]
# HWP는 HWPX를 한글에서 다시 저장한 것이라 정답 목록을 같이 쓴다
ANSWERS_OF = {"연수운영계획_샘플.hwp": "연수운영계획_샘플.hwpx"}


def load_answers(name: str) -> dict:
    name = ANSWERS_OF.get(name, name)
    return json.loads((SAMPLES / f"{name}.answers.json").read_text(encoding="utf-8"))


def block_at(block, loc: dict) -> bool:
    """블록이 정답 location이 가리키는 곳인가(값은 보지 않는다)."""
    kind, L = loc["kind"], block.location
    if kind in ("table", "hidden_sheet"):
        return (block.kind == CELL and block.hidden == (kind == "hidden_sheet")
                and L.row == loc["row"] and L.col == loc["col"] and L.header == loc["header"]
                and ("table" not in loc or L.table_index == loc["table"])
                and ("sheet" not in loc or L.sheet == loc["sheet"]))
    if kind == "paragraph":
        return block.kind in (PARAGRAPH, HEADING) and ("page" not in loc or L.page == loc["page"])
    if kind == "metadata":
        return block.kind == METADATA and L.field == loc["field"]
    if kind == "comment":
        return (block.kind == COMMENT
                and ("sheet" not in loc or L.sheet == loc["sheet"])
                and ("cell" not in loc or L.cell == loc["cell"])
                and ("page" not in loc or L.page == loc["page"]))
    raise AssertionError(f"모르는 location.kind: {kind}")


def occurrences(block, value: str) -> list[tuple[int, int]]:
    out, i = [], block.text.find(value)
    while i >= 0:
        out.append((i, i + len(value)))
        i = block.text.find(value, i + 1)
    return out


def all_planted_values(names=SAMPLE_FILES) -> set[str]:
    values = set()
    for n in names:
        data = load_answers(n)
        values |= {a["value"] for a in data["answers"]}
    return values
