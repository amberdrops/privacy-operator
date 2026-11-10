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


def test_pdf_blocks_have_bbox(results):
    res = results["강사계약_샘플.pdf"]
    assert all(b.location.bbox for b in res.blocks if b.kind in (PARAGRAPH, HEADING, CELL))


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_reading_does_not_modify_original(reader, name):
    path = SAMPLES / name
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    reader.read(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
