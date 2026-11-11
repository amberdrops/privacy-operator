"""원본 형식 그대로 저장과 재검사(8단계). 값은 모두 지어낸 것이다.

완료 기준: 저장 후 새 파일을 다시 검사하고, 실패하면 완료로 표시하지 않는다(보낼 문서함에 넣지 않는다).
실패 메시지에는 값 대신 개수와 곳(파일 안 이름)만 쓴다.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from answer_utils import SAMPLE_FILES, SAMPLES
from openpyxl import Workbook, load_workbook
from test_alias import decide

from privacy_operator.alias.codes import AliasBook
from privacy_operator.alias.pseudonymize import pseudonymize
from privacy_operator.alias.vault import Vault
from privacy_operator.detect import detect
from privacy_operator.detect.dictionaries import GRAY_CATEGORIES
from privacy_operator.detect.finding import ACTION_KEEP, STATUS_CONFIRMED
from privacy_operator.mission import find_results, restore_result
from privacy_operator.mission.package import ProcessingRecord, build_package
from privacy_operator.restore.zipdoc import leftover_codes, restore_zip
from privacy_operator.settings import Settings
from privacy_operator.writer import original as original_mod
from privacy_operator.writer.original import ERROR, LEFTOVER, OK, verify, write_original
from privacy_operator.writer.plan import ReplacementPlan
from privacy_operator.writer.xmlsweep import sweep


def _secrets(findings, docs=()) -> set[str]:
    vals = {f.raw_value for f in findings if f.review_status == STATUS_CONFIRMED and f.action != ACTION_KEEP
            and f.category not in GRAY_CATEGORIES}
    for d in docs:
        vals |= set(d.bundles.values())
    return vals


def _raw_hits(path: Path, secrets: set[str]) -> int:
    """우리 검사기와 따로, 날것에서 값을 찾는다(zip이면 모든 항목, PDF면 글과 속성)."""
    if path.suffix == ".pdf":
        with pymupdf.open(path) as doc:
            text = "".join(p.get_text() for p in doc) + json.dumps(doc.metadata, ensure_ascii=False)
            text += doc.get_xml_metadata() + "".join(str(a.info) for p in doc for a in (p.annots() or []))
        return sum(v in text for v in secrets)
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            data = z.read(name)
            n += sum(v.encode("utf-8") in data or v.encode("utf-16-le") in data for v in secrets)
    return n


# ---------------------------------------------------------------- 바꿀 목록

def _finding(fid, category, value, replacement, group=None):
    from privacy_operator.detect.finding import Finding
    from privacy_operator.reader.blocks import Location
    return Finding(fid, "D01", "a.hwpx", "hwpx", category, value, "", "b0000", 0, len(value), Location(), 0,
                   ("format",), "검토", STATUS_CONFIRMED, "가명처리", replacement=replacement, group_id=group)


def test_plan_never_rematches_inside_inserted_codes():
    plan = ReplacementPlan([_finding("D01-0001", "이름", "김민수", "[이름-01]"),
                            _finding("D01-0002", "직위", "강사", "[직위-01]")])
    assert plan.replace("김민수 강사, 강사 김민수") == "[이름-01] [직위-01], [직위-01] [이름-01]"


def test_gray_values_only_as_words():
    plan = ReplacementPlan([_finding("D01-0001", "직위", "교사", "[직위-01]")])
    assert plan.replace("교사, 교사가, 신규 교사라서, 보건교사") == "[직위-01], [직위-01]가, 신규 교사라서, 보건교사"
    assert plan.leftovers("교사") == []  # 회색 낱개는 차단 목록이 아니다


def test_numbers_match_other_separators_and_names_anywhere():
    plan = ReplacementPlan([_finding("D01-0001", "전화", "010-0000-0012", "[전화-01]"),
                            _finding("D01-0002", "이름", "김민수", "[이름-01]")])
    assert plan.replace("010 0000 0012, 01000000012, 김민수는") == "[전화-01], [전화-01], [이름-01]는"
    assert plan.leftovers("가김민수") == ["D01-0002"]  # 차단 목록은 낱말 경계 없이 엄격하게


ADDR = "경기도 가상시 나무읍 하늘로 123(구름리, 가나다라아파트)"  # 지어낸 주소


def test_line_wrapped_value_is_found_only_across_line_breaks():
    plan = ReplacementPlan([_finding("D01-0001", "주소", ADDR, "[주소-01]"),
                            _finding("D01-0002", "이름", "김민수", "[이름-01]")])
    wrapped = "경기도 가상시 나무읍 하늘로 123(구름리, 가나\n다라아파트)"
    assert plan.leftovers(wrapped) == []  # 그대로 찾으면 못 찾는다
    assert plan.leftovers(wrapped, wrapped=True) == ["D01-0001"]
    assert plan.leftovers("김\n민수", wrapped=True) == ["D01-0002"]
    assert plan.leftovers("김 민수", wrapped=True) == []  # 그냥 띄어 쓴 것은 다른 낱말일 수 있다


def test_pdf_address_across_lines_is_removed(tmp_path, reader):
    """주소가 줄에 걸치면 PyMuPDF 쪽 글에는 줄바꿈이 끼어 지우지 못하고 사본이 빠졌다(2026-11-10)."""
    from privacy_operator.writer.pdf import MALGUN
    if not MALGUN.is_file():
        pytest.skip("맑은 고딕 없음")
    src = tmp_path / "wrap.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 100), "주소: 경기도 가상시 나무읍 하늘로 123(구름리, 가나\n다라아파트) 끝",
                         fontsize=11, fontname="Malgun", fontfile=str(MALGUN))
        doc.save(src)
    plan = ReplacementPlan([_finding("D01-0001", "주소", ADDR, "[주소-01]")])
    out = write_original(src, "pdf", plan, tmp_path / "out", "wrap", reader)
    assert out.status == OK
    with pymupdf.open(out.path) as doc:
        text = doc[0].get_text()
    assert "[주소-01]" in text and "구름리" not in text and "다라아파트" not in text and "끝" in text


def test_xml_sweep_changes_text_and_attributes_only():
    plan = ReplacementPlan([_finding("D01-0001", "이름", "김민수", "[이름-01]")])
    xml = '<?xml version="1.0"?><a author="김민수"><t>김민수 &amp; 그</t><t>그대로 &lt;x&gt;</t></a>'
    out, n = sweep(xml, plan.replace)
    assert out == '<?xml version="1.0"?><a author="[이름-01]"><t>[이름-01] &amp; 그</t><t>그대로 &lt;x&gt;</t></a>'
    assert n == 2


# ---------------------------------------------------------------- 샘플 4종

@pytest.fixture(scope="module")
def outputs(results, reader, tmp_path_factory):
    out = tmp_path_factory.mktemp("orig")
    docs_in = [results[n] for n in SAMPLE_FILES]
    findings = decide(detect(docs_in))
    book = AliasBook("M-20261107-01")
    docs = pseudonymize(docs_in, findings, book)
    plan = ReplacementPlan(findings, docs)
    made = {}
    for name, res in zip(SAMPLE_FILES, docs_in):
        before = hashlib.sha256((SAMPLES / name).read_bytes()).hexdigest()
        made[name] = write_original(SAMPLES / name, res.format, plan, out, Path(name).stem + "_" + res.format, reader)
        assert hashlib.sha256((SAMPLES / name).read_bytes()).hexdigest() == before  # 원본은 그대로
    return {"made": made, "findings": findings, "docs": docs, "plan": plan, "book": book}


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_sample_saved_in_original_format_without_values(outputs, name):
    out = outputs["made"][name]
    assert out.status == OK, (out.status, out.places)
    secrets = _secrets(outputs["findings"], outputs["docs"])
    assert len(secrets) > 20
    assert _raw_hits(out.path, secrets) == 0, f"{out.path.name}에 값이 남음"


def test_hwp_comes_out_as_hwpx(outputs):
    assert outputs["made"]["연수운영계획_샘플.hwp"].path.suffix == ".hwpx"


@pytest.mark.parametrize("name", ["연수운영계획_샘플.hwpx", "연수운영계획_샘플.hwp"])
def test_hwpx_memo_and_preview_are_replaced(outputs, reader, name):
    path = outputs["made"][name].path
    res = reader.read(path)
    memo = next(b for b in res.blocks if b.block_id == "memo000-text")
    assert memo.text.startswith("[이름-")
    with zipfile.ZipFile(path) as z:
        preview = z.read("Preview/PrvText.txt").decode("utf-8")
        assert "[이름-01]" in preview
        if "Preview/PrvImage.png" in z.namelist():  # 한글로 저장한 HWP에서 온 미리보기 그림
            pix = pymupdf.Pixmap(z.read("Preview/PrvImage.png"))
            assert set(pix.samples) == {255}, "미리보기 그림이 흰 그림이 아님"


def test_pdf_text_is_removed_and_codes_written(outputs):
    path = outputs["made"]["강사계약_샘플.pdf"].path
    # 같은 작전의 HWPX 표에서 먼저 '강사' 역할을 받았으므로 그 가명코드를 쓴다
    name_code = next(f.replacement for f in outputs["findings"] if f.raw_value == "김민수" and f.replacement)
    with pymupdf.open(path) as doc:
        text = doc[0].get_text()
        assert "[삭제]" in text and name_code in text and "[주소-01]" in text
        assert doc.get_xml_metadata() == ""
        assert doc.metadata["author"] == name_code
        assert name_code in doc[0].first_annot.info["content"]
        # 표의 선은 남는다
        assert any(d["items"] for d in doc[0].get_drawings())


def test_xlsx_hidden_sheet_comment_and_properties(outputs):
    wb = load_workbook(outputs["made"]["만족도조사_샘플.xlsx"].path)
    hidden = wb["내부메모"]["A2"].value
    assert wb["내부메모"].sheet_state == "hidden" and re.fullmatch(r"\[이름-\d+\]", hidden)
    assert re.match(r"\[이름-\d+\]", wb["응답"]["A3"].comment.text)
    assert wb.properties.creator == hidden  # 숨김 시트의 확인자와 작성자는 같은 사람
    assert wb["응답"]["D4"].value == "제가 [소속직급-01]으로 있을 때 겪은 사례와 비슷했습니다."
    assert wb["응답"]["D6"].value == "올해 신규 교사라서 도움이 많이 되었습니다."  # 회색 낱개는 낱말만


def test_xlsx_formula_and_sheet_name(tmp_path, reader):
    wb = Workbook()
    ws = wb.active
    ws.title = "김민수 명단"
    ws.append(("성명", "인사"))
    ws.append(("김민수", '="김민수"&" 님"'))
    src = tmp_path / "식.xlsx"
    wb.save(src)
    res = reader.read(src)
    findings = decide(detect([res]))
    docs = pseudonymize([res], findings, AliasBook("M-20261107-01"))
    out = write_original(src, "xlsx", ReplacementPlan(findings, docs), tmp_path / "out", "식", reader)
    assert out.status == OK, out.places
    wb2 = load_workbook(out.path)
    ws2 = wb2.worksheets[0]
    assert ws2.title == "이름-01 명단"  # 시트 이름에는 대괄호를 쓸 수 없다
    assert ws2["B2"].value == '="[이름-01]"&" 님"'


# ---------------------------------------------------------------- 재검사가 막는다

def test_verify_finds_values_in_any_zip_part(tmp_path):
    plan = ReplacementPlan([_finding("D01-0001", "이름", "김민수", "[이름-01]")])
    path = tmp_path / "x.hwpx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml", "<t>[이름-01]</t>")
        z.writestr("settings.xml", '<s last="김민수"/>')  # 숨은 곳
    places, ids = verify(path, "zip", plan, reader=None)
    assert places == ["settings.xml"] and ids == ["D01-0001"]


def test_verify_reads_text_pieces_as_one_but_never_joins_them(tmp_path):
    """태그 사이 글을 한 글로 이어 한 번에 본다. 조각을 넘어 이어진 글자는 값으로 보지 않는다."""
    plan = ReplacementPlan([_finding("D01-0001", "이름", "김민수", "[이름-01]"),
                            _finding("D01-0002", "전화", "010-0000-0012", "[전화-01]")])
    path = tmp_path / "x.hwpx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.xml", "<p><t>김민</t><t>수</t><t>010</t><t>0000</t><t>0012</t></p>")  # 조각마다 다른 글
        z.writestr("b.xml", "<p><t>가</t><t>010 0000 0012</t><t>나</t></p>")
        z.writestr("c.xml", '<p note="김민수"><t>다</t></p>')
    places, ids = verify(path, "zip", plan, reader=None)
    assert places == ["b.xml", "c.xml"] and sorted(ids) == ["D01-0001", "D01-0002"]


def test_leftovers_same_answer_with_many_rules():
    """규칙이 많아도 답은 하나씩 정규식으로 본 것과 같다(선검사는 없는 것을 거르기만 한다)."""
    names = [a + b + c for a in "김이박최정" for b in "민서지수현" for c in "준우진하도"]
    findings = [_finding(f"D01-{i:04d}", "이름", n, f"[이름-{i:02d}]") for i, n in enumerate(names, start=1)]
    findings += [_finding(f"D01-9{i:03d}", "전화", f"010-0000-{i:04d}", f"[전화-{i:02d}]") for i in range(1, 40)]
    plan = ReplacementPlan(findings)
    text = "가김 민준나 이지 수진, 가박수하도 01000000007 010.0000.0031 0100000 00390"
    slow = [r.finding_ids[0] for r in plan.rules if r.secret and r.pattern.search(text)]
    assert plan.leftovers(text) == slow and len(slow) == 3
    assert plan.leftovers("") == []
    # 남았는지만 알면 될 때는 몇 개 찾고 멈춘다
    some = plan.leftovers(text, limit=2)
    assert len(some) == 2 and set(some) <= set(slow)
    assert plan.leftovers("남은 것 없음", limit=2) == []


def test_failed_check_keeps_file_out_and_temp_is_removed(tmp_path, monkeypatch, reader):
    """원본 형식 저장이 값을 남기면(여기서는 원본을 그대로 베끼게 망가뜨림) 결과를 넣지 않는다."""
    import tempfile

    made_dirs = []
    real = tempfile.TemporaryDirectory

    def tracking(*a, **k):
        d = real(*a, **k)
        made_dirs.append(Path(d.name))
        return d

    monkeypatch.setattr(original_mod.tempfile, "TemporaryDirectory", tracking)
    monkeypatch.setattr("privacy_operator.writer.xlsx.write_xlsx",
                        lambda src, dst, plan: shutil.copyfile(src, dst))
    src = SAMPLES / "만족도조사_샘플.xlsx"
    res = reader.read(src)
    findings = decide(detect([res]))
    docs = pseudonymize([res], findings, AliasBook("M-20261107-01"))
    out = write_original(src, "xlsx", ReplacementPlan(findings, docs), tmp_path / "out", "x", reader)
    assert out.status == LEFTOVER and out.path is None and out.places
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())
    assert made_dirs and not any(d.exists() for d in made_dirs)


def test_writer_error_is_reported_not_raised(tmp_path, monkeypatch, reader):
    def boom(*a, **k):
        raise RuntimeError("지어낸값 김민수가 든 메시지")

    monkeypatch.setattr("privacy_operator.writer.pdf.write_pdf", boom)
    plan = ReplacementPlan([])
    out = write_original(SAMPLES / "강사계약_샘플.pdf", "pdf", plan, tmp_path, "x", reader)
    assert out.status == ERROR


# ---------------------------------------------------------------- 패키지와 복원

@pytest.fixture(scope="module")
def package(results, reader, tmp_path_factory):
    root = tmp_path_factory.mktemp("pkg8")
    settings = Settings(vault_dir=str(root / "금고"), workspace_dir=str(root / "작전실"))
    vault = Vault(settings.vault_path).create("stage8-password")
    names = ["연수운영계획_샘플.hwpx", "강사계약_샘플.pdf", "만족도조사_샘플.xlsx"]
    docs_in = [results[n] for n in names]
    findings = decide(detect(docs_in))
    mid = vault.new_mission_id(date(2026, 11, 7))
    book = AliasBook(mid)
    docs = pseudonymize(docs_in, findings, book)
    vault.save_mapping(mid, book.to_mapping())
    originals = []
    pkg = build_package(settings, mid, docs, findings, book, vault, record=ProcessingRecord(),
                        sources=[SAMPLES / n for n in names], reader=reader, originals=originals)
    return {"settings": settings, "vault": vault, "mid": mid, "pkg": pkg, "findings": findings, "docs": docs,
            "originals": originals, "names": names}


def test_package_has_original_copies_listed(package):
    pkg = package["pkg"]
    assert all(o.ok for o in package["originals"])
    m = json.loads((pkg / "mission.json").read_text(encoding="utf-8"))
    forms = {(f["path"].rsplit(".", 1)[1], f["form"]) for f in m["files"]}
    assert forms >= {("md", "markdown"), ("hwpx", "original"), ("pdf", "original"), ("xlsx", "original")}
    for f in m["files"]:
        assert hashlib.sha256((pkg / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    secrets = _secrets(package["findings"], package["docs"])
    for p in (pkg / "docs").iterdir():
        if p.suffix != ".md":
            assert _raw_hits(p, secrets) == 0, p.name


def test_hwpx_roundtrip_through_ai_and_back(package, reader):
    """보낸 HWPX를 AI가 그대로 돌려줬다고 치면, 복원한 HWPX의 글이 원본과 같다."""
    s, mid, pkg = package["settings"], package["mid"], package["pkg"]
    sent = next(p for p in (pkg / "docs").iterdir() if p.suffix == ".hwpx")
    back = s.from_ai_path / mid / f"{mid}_결과.hwpx"
    shutil.copyfile(sent, back)
    item = next(i for i in find_results(s) if i.path == back)
    restored = restore_result(s, package["vault"], item)
    assert restored.complete, restored.result
    orig = [b.text for b in reader.read(SAMPLES / "연수운영계획_샘플.hwpx").blocks if b.kind != "metadata"]
    again = [b.text for b in reader.read(restored.output).blocks if b.kind != "metadata"]
    assert again == orig


def test_xlsx_roundtrip_through_ai_and_back(package, reader):
    """보낸 XLSX를 AI가 그대로 돌려줬다고 치면, 복원한 XLSX의 셀, 메모, 숨김 시트 글이 원본과 같다.
    서식과 시트 구성은 그대로 남는다(받은 파일의 XML 글 칸만 바꾼다)."""
    from openpyxl import load_workbook

    s, mid, pkg = package["settings"], package["mid"], package["pkg"]
    sent = next(p for p in (pkg / "docs").iterdir() if p.suffix == ".xlsx")
    back = s.from_ai_path / mid / f"{mid}_결과표.xlsx"
    shutil.copyfile(sent, back)
    item = next(i for i in find_results(s) if i.path == back)
    restored = restore_result(s, package["vault"], item)
    assert restored.complete, restored.result
    src = SAMPLES / "만족도조사_샘플.xlsx"
    orig = [b.text for b in reader.read(src).blocks if b.kind != "metadata"]
    again = [b.text for b in reader.read(restored.output).blocks if b.kind != "metadata"]
    assert again == orig
    a, b = load_workbook(src), load_workbook(restored.output)
    assert a.sheetnames == b.sheetnames and b["내부메모"].sheet_state == "hidden"
    assert b["응답"]["A3"].comment.text == a["응답"]["A3"].comment.text
    assert b.properties.creator == a.properties.creator


def test_split_code_in_result_is_reported(package, tmp_path):
    mapping = package["vault"].load_mapping(package["mid"])
    sent = next(p for p in (package["pkg"] / "docs").iterdir() if p.suffix == ".hwpx")
    broken = tmp_path / "쪼개짐.hwpx"
    with zipfile.ZipFile(sent) as zin, zipfile.ZipFile(broken, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "Contents/section0.xml":  # AI 프로그램이 글자 모양을 바꿔 가명코드를 둘로 나눔
                text = data.decode("utf-8")
                code = re.search(r"\[이름-\d+\]", text).group()
                data = text.replace(code, code.replace("-", "-</hp:t><hp:t>"), 1).encode("utf-8")
            zout.writestr(info, data)
    out = tmp_path / "복원.hwpx"
    result = restore_zip(broken, out, mapping)
    result.leftover_codes = leftover_codes(out, mapping)
    assert result.leftover_codes[code] == 1 and not result.complete


def _docx(path: Path, body: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats'
                   f'.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body>'
                   '</w:document>')


def test_docx_result_is_restored_with_josa(package):
    s, mid = package["settings"], package["mid"]
    src = s.from_ai_path / mid / f"{mid}_보고서.docx"
    code = next(e["alias_code"] for e in package["vault"].load_mapping(mid)["entries"] if e["raw_value"] == "이도윤")
    _docx(src, f"{code}는 실습을 맡았다.")
    item = next(i for i in find_results(s) if i.path == src)
    restored = restore_result(s, package["vault"], item)
    with zipfile.ZipFile(restored.output) as z:
        assert "이도윤은 실습을 맡았다." in z.read("word/document.xml").decode("utf-8")
    assert restored.complete
