"""사각지대(검사 못 한 영역) 찾기(7단계). 시험 파일은 임시 폴더에 지어낸 값으로 만든다."""

from __future__ import annotations

import json
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

# ---------------------------------------------------------------- 마크다운 사본만 보낼 문서 고르기(2026-11-16)

def _workflow(tmp_path, reader):
    from privacy_operator.alias.vault import Vault
    from privacy_operator.settings import Settings
    from privacy_operator.workflow import MissionWorkflow

    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"))
    return MissionWorkflow(settings, Vault(settings.vault_path).create("image-pii-password"), lambda: reader)


def _start(wf):
    from privacy_operator.detect.finding import STATUS_PENDING
    from privacy_operator.mission.package import ProcessingRecord

    wf.exclude([f.finding_id for f in wf.findings if f.exclude_hint])
    while wf.counts().pending:
        wf.confirm([f.finding_id for f in wf.findings if f.review_status == STATUS_PENDING])
    return wf.start(ProcessingRecord())


def test_documents_with_blind_spots_still_get_original_copies_by_default(reader, scanned_pdf, tmp_path):
    """그림이 있다고 사본을 빼지 않는다(사용자 결정: 공문마다 로고가 있다). 스캔한 쪽도 그림일 뿐이라 짐작하지 않는다."""
    from privacy_operator.writer.original import OK

    wf = _workflow(tmp_path, reader)
    wf.add_files([scanned_pdf])
    assert not wf.markdown_only(0)
    _start(wf)
    assert [o.status for o in wf.originals] == [OK]


def test_chosen_documents_go_out_as_markdown_only(reader, scanned_pdf, tmp_path):
    """사람이 사각지대를 보고 고른 문서는 마크다운 사본만 보낸다. 까닭은 완료 창과 처리 기록에 남는다."""
    from privacy_operator.writer.original import MARKDOWN_ONLY, OK

    logo = tmp_path / "로고공문.hwpx"
    shutil.copy(SAMPLES / "연수운영계획_샘플.hwpx", logo)
    _with_pictures(logo, "BinData/", {"image1.png": _png("기관 로고")})
    wf = _workflow(tmp_path, reader)
    wf.add_files([scanned_pdf, logo])
    wf.set_markdown_only(0, True)
    wf.set_markdown_only(1, True)
    wf.set_markdown_only(1, False)  # 다시 풀 수 있다
    wf.set_markdown_only(7, True)  # 없는 문서는 무시한다
    package = _start(wf)
    assert [(o.source_name, o.status) for o in wf.originals] == [("스캔섞임.pdf", MARKDOWN_ONLY), ("로고공문.hwpx", OK)]
    names = sorted(p.name for p in (package / "docs").iterdir())
    assert not any(n.endswith(".pdf") for n in names) and any(n.endswith(".hwpx") for n in names)
    assert sum(n.endswith(".md") for n in names) == 2  # 마크다운 사본은 둘 다 있다
    record = json.loads((wf.settings.vault_path / wf.mission_id / "log.json").read_text(encoding="utf-8"))
    assert [(s["format"], s["reason"]) for s in record["originals_skipped"]] == [("pdf", "markdown_only")]
    wf.set_markdown_only(0, False)  # 개시한 뒤에는 바꾸지 않는다
    assert wf.markdown_only(0)
    wf.reset()
    assert not wf.markdown_docs


def test_blind_spot_dialog_chooses_markdown_only_per_document(reader, scanned_pdf, tmp_path, qtbot):
    """사각지대 줄을 고르고 체크 상자를 켜면 그 문서가 마크다운 사본만 나간다. 그림으로 보여 줄 수 없는 줄에서도 된다."""
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
    xlsx = tmp_path / "차트.xlsx"
    wb.save(xlsx)
    wf = _workflow(tmp_path, reader)
    wf.add_files([scanned_pdf, xlsx])
    dialog = BlindSpotDialog(Voice("mission"), wf.results, None, wf.paths, wf)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert dialog.pii.isVisible() and not dialog.pii.isChecked()  # 기본은 꺼져 있다
    qtbot.mouseClick(dialog.pii, Qt.MouseButton.LeftButton)
    assert wf.markdown_docs == {0}
    shots = Path(__file__).resolve().parent / "screenshots"
    dialog.grab().save(str(shots / "blind_preview_markdown_only.png"))
    dialog.items.setCurrentRow(1)  # 같은 문서의 다른 줄도 켜져 보인다
    assert dialog.pii.isChecked()
    dialog.items.setCurrentRow(2)  # 차트: 그림은 못 보여 주지만 고를 수는 있다
    assert dialog.preview.pixmap().isNull() and dialog.pii.isVisible() and not dialog.pii.isChecked()
    qtbot.mouseClick(dialog.pii, Qt.MouseButton.LeftButton)
    assert wf.markdown_docs == {0, 1}
    dialog.items.setCurrentRow(0)
    qtbot.mouseClick(dialog.pii, Qt.MouseButton.LeftButton)
    assert wf.markdown_docs == {1}
