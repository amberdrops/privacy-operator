"""실제 kordoc 엔진으로 샘플 3종을 읽는다. 엔진이 없으면 건너뛴다.

실패 메시지에는 answer_id만 쓰고 값은 쓰지 않는다(samples/README.md).
"""

from __future__ import annotations

import hashlib

import pytest
from answer_utils import SAMPLE_FILES, SAMPLES, block_at, load_answers

from privacy_operator.reader.blocks import CELL, HEADING, PARAGRAPH
from privacy_operator.reader.engine import make_config


def _matches(block, answer) -> bool:
    return answer["value"] in block.text and block_at(block, answer["location"])


def test_offline_flag_is_forced(monkeypatch):
    monkeypatch.setenv("KORDOC_OFFLINE", "0")
    cfg = make_config(env={"KORDOC_OFFLINE": "0"})
    assert cfg.environment()["KORDOC_OFFLINE"] == "1"


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_sample_reads_as_blocks(results, name):
    res = results[name]
    assert res.blocks, "블록이 하나도 없습니다"
    assert res.cells(), "표 칸 블록이 없습니다"
    assert not res.blind_spots, [b.kind for b in res.blind_spots]
    assert len({b.block_id for b in res.blocks}) == len(res.blocks), "block_id가 겹칩니다"


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_every_planted_value_is_read_at_its_location(results, name):
    """탐지 이전 단계: 심어 둔 값이 정답 위치의 블록 안에 있어야 한다(읽기 누락 0)."""
    data = load_answers(name)
    expected = data["answers"] + [m for m in data["must_not_detect"] if "location" in m]
    missing = [a["answer_id"] for a in expected
               if not any(_matches(b, a) for b in results[name].blocks)]
    assert not missing, f"읽지 못한 정답: {missing}"


@pytest.mark.parametrize("name", ["연수운영계획_샘플.hwpx", "연수운영계획_샘플.hwp"])
def test_header_links_cells_under_instructor_column(results, name):
    """완료 기준: '강사' 열 아래 칸이 열 제목과 연결된다."""
    res = results[name]
    under = [b for b in res.cells() if b.location.header == "강사" and not b.is_header]
    assert [b.location.row for b in under] == [1, 2, 3, 4]
    assert all(b.location.col == 0 for b in under)


def test_xlsx_cells_have_sheet_and_address(results):
    res = results["만족도조사_샘플.xlsx"]
    body = [b for b in res.cells() if not b.hidden]
    assert all(b.location.sheet == "응답" and b.location.cell for b in body)
    hidden = [b for b in res.cells() if b.hidden]
    assert hidden and all(b.location.sheet == "내부메모" for b in hidden)


def test_old_excel_file_is_read_with_headers_and_addresses(reader):
    """옛 엑셀(.xls)도 읽는다(2026-11-15). 엑셀에서 직접 저장한 파일이다(값은 지어낸 것, 작성자 정보는 지움)."""
    from privacy_operator.detect import detect

    res = reader.read(SAMPLES / "참가자명단_샘플.xls")
    assert res.format == "xls"
    names = [b for b in res.cells() if b.location.header == "성명" and not b.is_header]
    assert [b.location.cell for b in names] == ["B3", "B4", "B5"] and all(b.location.sheet == "명단" for b in names)
    found = detect([res])
    assert sorted((f.category, f.tier) for f in found if f.category in ("이름", "전화")) == (
        [("이름", "확실")] * 3 + [("전화", "확실")] * 3)


def test_old_excel_file_goes_out_as_a_values_only_xlsx(reader, tmp_path):
    """옛 엑셀은 읽은 값만 담은 XLSX로 낸다(2026-11-15 사용자 결정). 같은 시트, 같은 칸에 값만 옮긴다."""
    from openpyxl import load_workbook
    from test_alias import decide

    from privacy_operator.alias.codes import AliasBook
    from privacy_operator.alias.pseudonymize import pseudonymize
    from privacy_operator.detect import detect
    from privacy_operator.writer.original import OK, UNSUPPORTED, write_original
    from privacy_operator.writer.plan import ReplacementPlan

    src = SAMPLES / "참가자명단_샘플.xls"
    res = reader.read(src)
    findings = decide(detect([res]))
    docs = pseudonymize([res], findings, AliasBook("M-20261107-01"))
    plan = ReplacementPlan(findings, docs)
    out = write_original(src, "xls", plan, tmp_path / "out", "명단", reader)
    assert out.status == OK and out.path.name == "명단.xlsx", out.places
    ws = load_workbook(out.path)["명단"]
    assert [c.value for c in ws[2]] == ["연번", "성명", "소속", "연락처"]
    assert (ws["A3"].value, ws["B3"].value, ws["D3"].value) == (1, "[이름-01]", "[전화-01]")  # 바꾸지 않은 숫자는 숫자로
    assert ws["A1"].value == "2026학년도 지어낸 연수 참가자 명단"
    # 읽기 엔진 없이는 만들 수 없다
    assert write_original(src, "xls", plan, tmp_path / "out2", "명단", None).status == UNSUPPORTED


def _workbook_openpyxl_cannot_open(path):
    """메모, 숨긴 시트, 작성자가 있고 칸의 서식 번호가 서식 표 밖인 엑셀(실제로 겪은 꼴, 2026-11-10)."""
    import zipfile

    from openpyxl import Workbook
    from openpyxl.comments import Comment

    wb = Workbook()
    ws = wb.active
    ws.title = "명단"
    ws.append(("성명", "연락처"))
    ws.append(("김민수", "010-0000-0012"))
    ws["A2"].comment = Comment("이도윤에게 확인", "박서연")
    hidden = wb.create_sheet("내부")
    hidden.sheet_state = "hidden"
    hidden.append(("확인자", "최지호"))
    wb.properties.creator = "정하람"
    good = path.with_name("good.xlsx")
    wb.save(good)
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b'<c r="B2"', b'<c r="B2" s="99"')
            dst.writestr(item, data)
    good.unlink()
    return path


def test_workbook_openpyxl_cannot_open_is_still_read(reader, tmp_path):
    """openpyxl이 열지 못하는 엑셀도 읽는다: 메모, 숨긴 시트, 작성자는 zip 안의 XML에서 직접 읽는다(2026-11-15)."""
    from openpyxl import load_workbook

    from privacy_operator.reader.blocks import COMMENT, METADATA

    path = _workbook_openpyxl_cannot_open(tmp_path / "서식어긋남.xlsx")
    with pytest.raises(Exception):  # noqa: B017, PT011 - 어떤 오류든 열지 못하면 된다
        load_workbook(path)
    res = reader.read(path)
    comments = {(b.location.sheet, b.location.cell, b.location.field): b.text for b in res.blocks if b.kind == COMMENT}
    assert comments == {("명단", "A2", "text"): "이도윤에게 확인", ("명단", "A2", "author"): "박서연"}
    assert [b.text for b in res.cells() if b.hidden] == ["확인자", "최지호"]
    assert not any(b.hidden for b in res.cells() if b.location.sheet == "명단")
    assert "정하람" in [b.text for b in res.blocks if b.kind == METADATA]


def test_raw_extras_match_openpyxl_on_the_sample(reader):
    """zip에서 직접 읽은 것이 openpyxl로 읽은 것과 같다(샘플의 메모, 숨긴 시트, 문서 속성)."""
    from privacy_operator.reader.extras import _xlsx_extras_openpyxl, _xlsx_extras_raw

    path = SAMPLES / "만족도조사_샘플.xlsx"
    base = [b for b in reader.read(path).blocks if not b.block_id.startswith(("xcm-", "xprop-"))]

    def view(blocks):
        return [(b.block_id, b.kind, b.text, b.hidden, b.location.sheet, b.location.cell, b.location.field) for b in blocks]

    assert view(_xlsx_extras_raw(path, base)) == view(_xlsx_extras_openpyxl(path, base))


def test_existing_cells_skip_the_empty_grid_of_a_whole_row_merge(tmp_path):
    """첫 행을 행 전체로 병합한 시트(A1:XFD1): 줄마다 16,384칸을 돌지 않고 있는 칸만 본다."""
    from openpyxl import Workbook, load_workbook

    from privacy_operator.reader.extras import existing_cells

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "지어낸 명단"
    ws.merge_cells("A1:XFD1")
    for i in range(50):
        ws.append((i + 1, "김민수"))
    path = tmp_path / "행병합.xlsx"
    wb.save(path)
    ws = load_workbook(path).active
    cells = existing_cells(ws)
    assert len(cells) == 101 and [c.coordinate for c in cells[:3]] == ["A1", "A2", "B2"]
    assert len(ws._cells) < 20000  # 돌면서 빈 칸을 새로 만들지 않았다


def test_pdf_blocks_have_bbox(results):
    res = results["강사계약_샘플.pdf"]
    assert all(b.location.bbox for b in res.blocks if b.kind in (PARAGRAPH, HEADING, CELL))


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_reading_does_not_modify_original(reader, name):
    path = SAMPLES / name
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    reader.read(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
