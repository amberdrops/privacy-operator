"""kordoc 결과 → 공통 블록 변환. 엔진 없이 지어낸 결과 JSON으로 시험한다."""

from privacy_operator.reader.blocks import CELL, COMMENT, METADATA, Block, Location, ReadResult
from privacy_operator.reader.convert import column_headers, convert


def _cell(text, colspan=1, rowspan=1, header=False):
    c = {"text": text, "colSpan": colspan, "rowSpan": rowspan}
    if header:
        c["isHeader"] = True
    return c


def _table_raw(cells, has_header=True, **extra):
    return {"blocks": [{"type": "table", "pageNumber": 1,
                        "table": {"rows": len(cells), "cols": len(cells[0]), "cells": cells,
                                  "hasHeader": has_header, **extra}}]}


def _by_pos(blocks):
    return {(b.location.row, b.location.col): b for b in blocks if b.kind == CELL}


def test_merged_header_covers_spanned_columns():
    # 머리글 '강사'가 두 열을 덮는다. 가려진 칸은 kordoc이 빈 글자로 준다.
    cells = [
        [_cell("구분", header=True), _cell("강사", colspan=2, header=True), _cell("", header=True)],
        [_cell("1일차"), _cell("가나다"), _cell("라마바")],
    ]
    assert column_headers(cells, True) == {0: ("구분",), 1: ("강사",), 2: ("강사",)}
    blocks, _ = convert(_table_raw(cells), "hwpx")
    pos = _by_pos(blocks)
    assert pos[(1, 1)].location.header == "강사"
    assert pos[(1, 2)].location.header == "강사"
    assert (0, 2) not in pos  # 빈 칸은 블록으로 만들지 않는다


def test_two_header_rows_keep_path_and_use_lowest():
    cells = [
        [_cell("강사", colspan=2, header=True), _cell("", header=True)],
        [_cell("성명", header=True), _cell("소속", header=True)],
        [_cell("가나다"), _cell("예시학교")],
    ]
    pos = _by_pos(convert(_table_raw(cells), "hwpx")[0])
    assert pos[(2, 0)].location.header == "성명"
    assert pos[(2, 0)].location.header_path == ("강사", "성명")
    assert pos[(1, 0)].is_header and not pos[(2, 0)].is_header


def test_second_header_row_under_merged_title_is_found_without_flags():
    """kordoc이 맨 윗줄에만 isHeader를 붙인 두 줄 머리글: 합친 큰 제목 아래 '강사명'도 머리글로 본다."""
    cells = [
        [_cell("교시", rowspan=2, header=True), _cell("강사 및 평가", colspan=2, header=True), _cell("", header=True)],
        [_cell(""), _cell("강사명"), _cell("만족도")],
        [_cell("1교시"), _cell("가나다"), _cell("4.5")],
        [_cell("2교시"), _cell("라마바"), _cell("4.7")],
    ]
    pos = _by_pos(convert(_table_raw(cells), "hwpx")[0])
    assert pos[(1, 1)].is_header and not pos[(2, 1)].is_header
    assert pos[(2, 1)].location.header == "강사명" and pos[(3, 1)].location.header == "강사명"
    assert pos[(2, 1)].location.header_path == ("강사 및 평가", "강사명")


def test_data_row_under_merged_title_stays_data():
    """합친 제목 아래라도 아는 칸 제목이 없는 줄은 데이터다(이름이 머리글로 빠지면 안 된다)."""
    cells = [
        [_cell("구분", header=True), _cell("강사", colspan=2, header=True), _cell("", header=True)],
        [_cell("1일차"), _cell("가나다"), _cell("라마바")],
    ]
    pos = _by_pos(convert(_table_raw(cells), "hwpx")[0])
    assert not pos[(1, 1)].is_header and pos[(1, 1)].location.header == "강사"


def test_has_header_without_cell_flags_uses_first_row():
    # XLSX 결과는 칸에 isHeader가 없고 표에 hasHeader만 있다
    cells = [[_cell("응답자"), _cell("이메일")], [_cell("가나다"), _cell("a@example.kr")]]
    pos = _by_pos(convert(_table_raw(cells), "xlsx")[0])
    assert pos[(1, 1)].location.header == "이메일"
    assert pos[(0, 0)].is_header


def test_xlsx_sheet_name_and_a1_address_follow_source_rows():
    # 빈 행과 열이 잘려 나가도 sourceRows/sourceCols로 원래 셀 주소를 찾는다
    raw = _table_raw([[_cell("성명")], [_cell("가나다")]], sourceRows=[2, 5], sourceCols=[3])
    raw["blocks"].insert(0, {"type": "heading", "text": "명단", "pageNumber": 1, "level": 2})
    blocks, _ = convert(raw, "xlsx")
    cell = _by_pos(blocks)[(1, 0)]
    assert cell.location.sheet == "명단"
    assert cell.location.cell == "D6"


def test_table_index_counts_tables_in_order():
    one = _table_raw([[_cell("가")]])["blocks"][0]
    raw = {"blocks": [one, {"type": "paragraph", "text": "문단", "pageNumber": 1}, one]}
    cells = [b for b in convert(raw, "hwpx")[0] if b.kind == CELL]
    assert [b.location.table_index for b in cells] == [0, 1]


def test_memos_and_metadata_become_blocks():
    raw = {"blocks": [], "memos": [{"text": "메모 내용", "author": "작성자", "blockIndex": 3}],
           "metadata": {"author": "작성자", "title": "제목", "pageCount": 2, "createdAt": "2026-01-01"}}
    blocks, _ = convert(raw, "hwpx")
    comments = [b for b in blocks if b.kind == COMMENT]
    assert {b.location.field for b in comments} == {"text", "author"}
    assert comments[0].location.block_index == 3
    meta = {b.location.field for b in blocks if b.kind == METADATA}
    assert meta == {"author", "title"}  # 숫자와 날짜 칸은 뺀다


def test_blind_spots_from_ocr_pages_images_and_warnings():
    raw = {"blocks": [{"type": "image", "pageNumber": 2}],
           "pageQuality": [{"page": 1, "needsOcr": False}, {"page": 3, "needsOcr": True}],
           "warnings": [{"code": "SOME_CODE", "page": 4, "message": "본문 조각"}]}
    _, blind = convert(raw, "pdf")
    kinds = sorted((b.kind, b.page) for b in blind)
    assert kinds == [("engine_warning", 4), ("image", 2), ("needs_ocr", 3)]
    assert all("본문 조각" not in b.detail for b in blind)  # 경고 문장은 옮기지 않는다


def test_repr_does_not_show_text():
    b = Block("b0001", "paragraph", "지어낸값가나다", Location(page=1))
    r = ReadResult("x.hwpx", "hwpx", blocks=[b])
    assert "지어낸값가나다" not in repr(b)
    assert "지어낸값가나다" not in repr(r)


def test_cells_keep_the_nearest_left_cell_as_row_label():
    cells = [[{"text": t, "colSpan": 1, "rowSpan": 1} for t in row] for row in
             [["원 장", "", "2026. 10. 7.\n박서연"], ["협조자", "x", "y"]]]
    raw = {"blocks": [{"type": "table", "pageNumber": 1,
                       "table": {"rows": 2, "cols": 3, "cells": cells, "hasHeader": False}}]}
    blocks = {b.text: b.location.row_label for b in convert(raw, "hwpx")[0]}
    assert blocks["2026. 10. 7.\n박서연"] == "원 장" and blocks["y"] == "x" and blocks["원 장"] is None
