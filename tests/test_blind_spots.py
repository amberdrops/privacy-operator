"""사각지대(검사 못 한 영역) 찾기(7단계). 시험 파일은 임시 폴더에 지어낸 값으로 만든다."""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pymupdf
import pytest
from answer_utils import SAMPLES
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

from privacy_operator.ui.dialogs import blind_spot_lines
from privacy_operator.ui.voice import Voice

MALGUN = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "malgun.ttf"


def _font():
    return {"fontname": "malgun", "fontfile": str(MALGUN)} if MALGUN.is_file() else {"fontname": "korea"}


@pytest.fixture
def scanned_pdf(tmp_path) -> Path:
    """1쪽 글자만, 2쪽 그림만(스캔), 3쪽 글자와 그림."""
    src = pymupdf.open()
    p = src.new_page()
    p.insert_text((72, 100), "담당자: 가나다 스캔한 쪽", fontsize=14, **_font())
    png = p.get_pixmap(dpi=72).tobytes("png")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "첫 쪽 글자", fontsize=12, **_font())
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=png)
    third = doc.new_page()
    third.insert_text((72, 100), "셋째 쪽 글자", fontsize=12, **_font())
    third.insert_image(pymupdf.Rect(72, 200, 272, 300), stream=png)
    path = tmp_path / "스캔섞임.pdf"
    doc.save(path)
    return path


def test_pdf_scanned_page_and_image_page(reader, scanned_pdf):
    spots = [(b.kind, b.page) for b in reader.read(scanned_pdf).blind_spots]
    # kordoc은 글자가 적은 1, 3쪽도 OCR 필요로 보지만, 쪽마다 직접 본 결과로 바꾼다
    assert spots == [("needs_ocr", 2), ("image", 3)]


def test_xlsx_chart_is_a_blind_spot(reader, tmp_path):
    wb = Workbook()
    ws = wb.active
    for row in (("이름", "점수"), ("가나다", 3), ("라마바", 5)):
        ws.append(row)
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=3), titles_from_data=True)
    ws.add_chart(chart, "D2")
    path = tmp_path / "차트.xlsx"
    wb.save(path)
    assert [(b.kind, b.detail) for b in reader.read(path).blind_spots] == [("drawing", "2")]


def test_hwpx_picture_is_a_blind_spot(reader, tmp_path):
    path = tmp_path / "그림.hwpx"
    shutil.copy(SAMPLES / "연수운영계획_샘플.hwpx", path)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("BinData/image1.png", b"\x89PNG fake")
    assert [(b.kind, b.detail) for b in reader.read(path).blind_spots] == [("image", "1")]


def test_samples_have_no_blind_spots(results):
    assert all(not r.blind_spots for r in results.values())


def test_blind_spot_lines_use_voice(reader, scanned_pdf):
    res = reader.read(scanned_pdf)
    lines = blind_spot_lines(Voice("plain"), [res])
    assert lines == [
        "스캔섞임.pdf, 2쪽: 글자 없이 그림만 있는 쪽(스캔)입니다. 글자를 읽지 못했습니다.",
        "스캔섞임.pdf, 3쪽: 그림 1개. 그림 속 글자는 검사하지 않았습니다.",
    ]
