"""XLSX 원본 형식 저장(openpyxl): 셀 글과 수식, 메모, 시트 이름, 머리글과 바닥글, 문서 속성을 바꾼다.

openpyxl은 도형, 그림, 차트를 다시 쓰지 못해 결과 파일에서 빠진다(사각지대로 이미 알린 곳).
시트 이름에는 대괄호를 쓸 수 없어 가명코드의 대괄호를 뺀다.
"""

from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook

from privacy_operator.writer.plan import ReplacementPlan

_PROPS = ("creator", "lastModifiedBy", "title", "subject", "description", "keywords", "category", "identifier",
          "contentStatus", "version")
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _sheet_title(plan: ReplacementPlan, title: str, used: set[str]) -> str:
    new = _BAD_SHEET_CHARS.sub("", plan.replace(title))[:31] or "Sheet"
    base, n = new, 2
    while new in used:
        new = f"{base[:28]}_{n}"
        n += 1
    return new


def write_xlsx(src: Path, dst: Path, plan: ReplacementPlan) -> None:
    wb = load_workbook(src)
    try:
        used: set[str] = set()
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str):
                        new = plan.replace(cell.value)
                        if new != cell.value:
                            cell.value = new
                    if cell.comment is not None:
                        text, author = plan.replace(cell.comment.text or ""), plan.replace(cell.comment.author or "")
                        if (text, author) != (cell.comment.text, cell.comment.author):
                            cell.comment.text, cell.comment.author = text, author
            for hf in (ws.oddHeader, ws.oddFooter, ws.evenHeader, ws.evenFooter, ws.firstHeader, ws.firstFooter):
                for part in (hf.left, hf.center, hf.right):
                    if part.text:
                        part.text = plan.replace(part.text)
            title = _sheet_title(plan, ws.title, used) if plan.replace(ws.title) != ws.title else ws.title
            used.add(title)
            if title != ws.title:
                ws.title = title
        for name in _PROPS:
            value = getattr(wb.properties, name, None)
            if isinstance(value, str):
                setattr(wb.properties, name, plan.replace(value))
        for prop in list(getattr(wb, "custom_doc_props", []) or []):
            if isinstance(getattr(prop, "value", None), str):
                prop.value = plan.replace(prop.value)
        wb.save(dst)
    finally:
        wb.close()
