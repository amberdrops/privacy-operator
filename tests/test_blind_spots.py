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


# ---------------------------------------------------------------- 그림으로 미리 보기(2026-11-15)

def _with_pictures(path: Path, folder: str, pictures: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "a") as z:
        for name, data in pictures.items():
            z.writestr(f"{folder}{name}", data)
    return path


def _png(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=200, height=120).insert_text((20, 60), text, fontsize=14, **_font())
    return doc[0].get_pixmap(dpi=72).tobytes("png")


def test_pdf_blind_spot_is_shown_as_the_page_picture(reader, scanned_pdf):
    """스캔한 쪽과 그림이 든 쪽은 그 쪽을 그려 보여 준다. 그림은 메모리로만 돌려준다(파일을 만들지 않는다)."""
    from privacy_operator.reader import preview

    before = sorted(p.name for p in scanned_pdf.parent.iterdir())
    res = reader.read(scanned_pdf)
    for spot in res.blind_spots:
        assert preview.image_count(scanned_pdf, "pdf", spot) == 1
        data = preview.image_bytes(scanned_pdf, "pdf", spot)
        picture = pymupdf.Pixmap(data)
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and picture.width == preview.PAGE_WIDTH and picture.height > picture.width
    assert sorted(p.name for p in scanned_pdf.parent.iterdir()) == before


def test_pictures_inside_xlsx_and_hwpx_are_shown_in_number_order(reader, tmp_path):
    from privacy_operator.reader import preview
    from privacy_operator.reader.blocks import BlindSpot

    pictures = {f"image{i}.png": _png(f"그림 {i}") for i in (1, 2, 10)}
    wb = Workbook()
    wb.active.append(("이름",))
    xlsx = tmp_path / "그림.xlsx"
    wb.save(xlsx)
    _with_pictures(xlsx, "xl/media/", {**pictures, "image3.emf": b"emf"})
    hwpx = tmp_path / "그림.hwpx"
    shutil.copy(SAMPLES / "연수운영계획_샘플.hwpx", hwpx)
    _with_pictures(hwpx, "BinData/", pictures)
    for path, fmt in ((xlsx, "xlsx"), (hwpx, "hwpx")):
        [spot] = [b for b in reader.read(path).blind_spots if b.kind == "image"]
        assert preview.image_count(path, fmt, spot) == 3  # EMF는 화면이 그리지 못해 세지 않는다
        assert [preview.image_bytes(path, fmt, spot, i) for i in range(3)] == [
            pictures["image1.png"], pictures["image2.png"], pictures["image10.png"]]
        assert preview.image_bytes(path, fmt, spot, 3) is None
    # 도형과 차트, 옛 한글 파일, 깨진 파일은 보여 주지 못한다(오류를 내지 않는다)
    assert preview.image_count(xlsx, "xlsx", BlindSpot("drawing", detail="2")) == 0
    assert preview.image_count(tmp_path / "없음.hwp", "hwp", BlindSpot("image", detail="1")) == 0
    broken = tmp_path / "깨짐.xlsx"
    broken.write_bytes(b"not a zip")
    assert preview.image_count(broken, "xlsx", BlindSpot("image", detail="1")) == 0
    assert preview.image_bytes(broken, "pdf", BlindSpot("needs_ocr", page=1)) is None


def test_blind_spot_dialog_shows_the_picture_of_the_chosen_line(reader, scanned_pdf, tmp_path, qtbot):
    """줄을 고르면 오른쪽에 그 자리의 그림. 그림이 여러 개면 넘겨 본다. 보여 줄 수 없는 줄은 글로 알린다."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from privacy_operator.ui.dialogs import BlindSpotDialog
    from privacy_operator.ui.style import stylesheet
    from privacy_operator.ui.theme import load_theme

    QApplication.instance().setStyleSheet(stylesheet(load_theme("pink")))
    wb = Workbook()
    ws = wb.active
    for row in (("이름", "점수"), ("가나다", 3)):
        ws.append(row)
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=2), titles_from_data=True)
    ws.add_chart(chart, "D2")
    xlsx = tmp_path / "차트와그림.xlsx"
    wb.save(xlsx)
    _with_pictures(xlsx, "xl/media/", {"image1.png": _png("그림 하나"), "image2.png": _png("그림 둘")})
    paths = [scanned_pdf, xlsx]
    results = [reader.read(p) for p in paths]
    dialog = BlindSpotDialog(Voice("mission"), results, None, paths)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    kinds = [b.kind for r in results for b in r.blind_spots]
    assert kinds == ["needs_ocr", "image", "drawing", "image"] and dialog.items.count() == 4
    # 첫 줄(스캔한 쪽)이 골라져 있고 그림이 떠 있다. 한 장뿐이라 넘기는 단추는 없다
    assert not dialog.preview.pixmap().isNull() and not dialog.next_btn.isVisible()
    shots = Path(__file__).resolve().parent / "screenshots"
    shots.mkdir(exist_ok=True)
    dialog.grab().save(str(shots / "blind_preview_pdf.png"))
    dialog.items.setCurrentRow(2)  # 도형과 차트
    assert dialog.preview.pixmap().isNull() and dialog.preview.text() == Voice("mission").t("blind.preview_none")
    dialog.items.setCurrentRow(3)  # 그림 두 개
    first = dialog.preview.pixmap().toImage()
    assert dialog.next_btn.isVisible() and dialog.page_label.text() == "1 / 2"
    qtbot.mouseClick(dialog.next_btn, Qt.MouseButton.LeftButton)
    assert dialog.page_label.text() == "2 / 2" and dialog.preview.pixmap().toImage() != first
    qtbot.mouseClick(dialog.next_btn, Qt.MouseButton.LeftButton)  # 끝에서 처음으로
    assert dialog.page_label.text() == "1 / 2" and dialog.preview.pixmap().toImage() == first
    dialog.grab().save(str(shots / "blind_preview_xlsx.png"))
    small = [w for w in dialog.findChildren(type(dialog.preview)) + [dialog.next_btn, dialog.prev_btn]
             if w.isVisible() and (w.font().pixelSize() if w.font().pixelSize() > 0
                                   else w.font().pointSizeF() * w.logicalDpiY() / 72) < 14.99]
    assert not small
    # 경로를 주지 않으면 예전처럼 목록만 보인다
    plain = BlindSpotDialog(Voice("plain"), results)
    qtbot.addWidget(plain)
    assert not hasattr(plain, "preview")