"""XLSX 원본 형식 저장(openpyxl): 셀 글과 수식, 메모, 시트 이름, 머리글과 바닥글, 문서 속성을 바꾼다.
칸 밖에 저장되는 글자(표의 열 이름, 하이퍼링크, 목록 유효성, 필터 값)도 함께 바꾼다.

openpyxl은 도형, 그림, 차트를 다시 쓰지 못해 결과 파일에서 빠진다(사각지대로 이미 알린 곳).
시트 이름에는 대괄호를 쓸 수 없어 가명코드의 대괄호를 뺀다.
"""

from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook

from privacy_operator.reader.blocks import CELL, HEADING
from privacy_operator.reader.extras import existing_cells
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


def _changed(plan: ReplacementPlan, value) -> bool:
    return isinstance(value, str) and plan.replace(value) != value


def _replace_attrs(plan: ReplacementPlan, obj, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(obj, name, None)
        if _changed(plan, value):
            setattr(obj, name, plan.replace(value))


def _filters(plan: ReplacementPlan, auto_filter) -> None:
    """자동 필터에 골라 둔 값(`박서연`만 보기). 칸과 같은 가명코드로 바꿔 필터가 그대로 맞게 한다."""
    for column in getattr(auto_filter, "filterColumn", None) or []:
        if column.filters is not None and column.filters.filter:
            column.filters.filter = [plan.replace(v) for v in column.filters.filter]
        for custom in getattr(column.customFilters, "customFilter", None) or []:
            _replace_attrs(plan, custom, ("val",))


def _beyond_cells(plan: ReplacementPlan, ws) -> None:
    """칸 밖에 칸 글자가 한 벌 더 저장되는 곳(2026-11-15). 칸만 바꾸면 여기에 원문이 남아 재검사에서 사본이 빠졌다.
    - 엑셀 '표'의 열 이름: 머리글 칸과 같은 글자다(설문 응답 시트의 문항에 강사 이름이 든 경우).
    - 하이퍼링크: 이메일 칸에 엑셀이 자동으로 건 `mailto:` 링크. 대상이 바뀌는 링크는 지운다(칸 글자는 남는다).
    - 목록 유효성(`김민수,이도윤` 가운데 고르기)과 자동 필터에 골라 둔 값."""
    for table in ws.tables.values():
        for column in table.tableColumns:
            _replace_attrs(plan, column, ("name", "totalsRowLabel"))
        _filters(plan, table.autoFilter)
    _filters(plan, ws.auto_filter)
    for dv in getattr(ws.data_validations, "dataValidation", None) or []:
        _replace_attrs(plan, dv, ("formula1", "formula2", "error", "errorTitle", "prompt", "promptTitle"))
    for cell in existing_cells(ws):  # 링크는 칸마다 달려 있다(저장할 때 openpyxl이 칸에서 모은다)
        link = cell.hyperlink
        if link is not None and any(_changed(plan, v) for v in (link.target, link.location, link.tooltip, link.display)):
            cell.hyperlink = None


_NUMBER = re.compile(r"-?(?:0|[1-9]\d{0,14})(?:\.\d+)?")  # 0으로 시작하는 번호(`0012`)와 긴 번호는 글자로 둔다


def write_xlsx_values(blocks, dst: Path, plan: ReplacementPlan) -> None:
    """옛 엑셀(.xls)의 사본. openpyxl은 그 형식을 열지 못하므로, 읽기 엔진이 읽은 칸 글자를 새 XLSX의 같은 시트,
    같은 칸에 값으로만 옮긴다. 서식, 열 너비, 병합, 수식, 메모는 옮기지 못한다. 바꾸지 않은 숫자는 숫자로 넣는다."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    sheets: dict[str | None, object] = {}
    used: set[str] = set()

    def sheet(name: str | None):
        if name not in sheets:
            title = _sheet_title(plan, name or "Sheet", used)
            used.add(title)
            sheets[name] = wb.create_sheet(title)
        return sheets[name]

    for b in blocks:
        loc = b.location
        if b.kind == HEADING and loc.sheet == b.text:
            sheet(loc.sheet)  # 빈 시트도 자리를 지킨다
        elif b.kind == CELL and loc.cell:
            text = plan.replace(b.text)
            number = text == b.text and _NUMBER.fullmatch(text)
            sheet(loc.sheet)[loc.cell] = (float(text) if "." in text else int(text)) if number else text
    if not sheets:
        sheet(None)
    try:
        wb.save(dst)
    finally:
        wb.close()


def write_xlsx(src: Path, dst: Path, plan: ReplacementPlan,
               cell_texts: dict[tuple[str, str], str] | None = None) -> None:
    """cell_texts: (시트 이름, 칸 주소) → 읽기 엔진이 읽은 칸 글자. 글자가 아닌 칸(숫자, 날짜)을 바꿀 때 쓴다.

    엑셀은 전화번호, 계좌번호를 숫자로, 생년월일을 날짜로 저장하기도 한다. 탐지는 읽기 엔진이 글자로 바꿔 준
    것(`1000000012`, `1990-01-01`)에서 하므로, 그 글자에 바꿀 것이 있으면 칸을 글자 칸으로 바꿔 넣는다.
    글자 칸만 바꾸던 때는 이런 칸에 원문이 남아 재검사에서 사본이 빠졌다(2026-11-15)."""
    wb = load_workbook(src)
    try:
        used: set[str] = set()
        for ws in wb.worksheets:
            _beyond_cells(plan, ws)  # 칸 글자를 바꾸기 전에(링크의 보이는 글자는 칸 글자와 같다)
            for cell in existing_cells(ws):
                if isinstance(cell.value, str):
                    new = plan.replace(cell.value)
                    if new != cell.value:
                        cell.value = new
                elif cell.value is not None and cell_texts:
                    text = cell_texts.get((ws.title, cell.coordinate))
                    if text is not None and plan.replace(text) != text:
                        cell.value = plan.replace(text)
                        cell.number_format = "General"
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
