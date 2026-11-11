"""받침, 가명코드 부여, 가명처리, 차단 목록. 엔진 없이 지어낸 블록으로 시험한다."""

from __future__ import annotations

import pytest

from privacy_operator.alias.batchim import has_batchim, has_rieul_batchim
from privacy_operator.alias.codes import AliasBook
from privacy_operator.alias.pseudonymize import DELETED, PendingFindings, pseudonymize
from privacy_operator.detect import detect
from privacy_operator.detect.finding import (
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    STATUS_REFERENCE,
)
from privacy_operator.reader.blocks import PARAGRAPH, Block, Location, ReadResult
from privacy_operator.reader.convert import convert
from privacy_operator.writer.blocklist import BlocklistHit, blocklist_hits, check_blocklist
from privacy_operator.writer.markdown import to_markdown


def decide(findings):
    """사람 판정 흉내(시험 전용): 제외 후보는 제외, 나머지 대기는 확정."""
    for f in findings:
        if f.review_status == STATUS_PENDING:
            f.review_status = STATUS_EXCLUDED if f.exclude_hint else STATUS_CONFIRMED
    return findings


def para(text, i=0):
    return Block(f"b{i:04d}", PARAGRAPH, text, Location(page=1, block_index=i))


def table(*rows):
    cells = [[{"text": t, "colSpan": 1, "rowSpan": 1} for t in row] for row in rows]
    raw = {"blocks": [{"type": "table", "pageNumber": 1,
                       "table": {"rows": len(rows), "cols": len(rows[0]), "cells": cells, "hasHeader": True}}]}
    return convert(raw, "hwpx")[0]


def doc(*blocks, name="t.hwpx"):
    flat = []
    for b in blocks:
        flat.extend(b if isinstance(b, list) else [b])
    return ReadResult(name, "hwpx", blocks=flat)


def texts(pdoc):
    return [b.text for b in pdoc.blocks]


# ---------------------------------------------------------------- 받침

@pytest.mark.parametrize("value,expected", [
    ("김민수", False), ("이도윤", True), ("박서연", True), ("정하람", True), ("남궁별", True),
    ("010-0000-0002", False), ("010-0000-0001", True), ("010-0000-0003", True),
    ("a@example.kr", True), ("a@example.com", True), ("a@example.net", True), ("a@example.org", False),
    ("[강사-01]", True),
    ("", False),
])
def test_has_batchim(value, expected):
    assert has_batchim(value) is expected


@pytest.mark.parametrize("value,expected", [("이도윤", False), ("정하람", False), ("한별", True), ("7", True),
                                            ("3", False), ("a@b.kr", True), ("a@b.net", False), ("a@b.com", False)])
def test_has_rieul_batchim(value, expected):
    assert has_rieul_batchim(value) is expected


# ---------------------------------------------------------------- 가명코드

@pytest.mark.parametrize("text", ["강사: 김민수", "김민수 사무관", "원장 김민수", "담당자 김민수에게", "김민수 님"])
def test_names_are_always_the_name_code_whatever_the_role(text):
    """이름 가명코드에 역할이나 직함을 넣지 않는다(`[이름-01]`, 2026-11-09). 짐작한 역할이 틀리는 일이 있어서다."""
    res = doc(para(text))
    found = decide(detect([res]))
    pseudonymize([res], found, AliasBook("M-20261109-01"))
    assert [f.alias_code for f in found if f.category == "이름"] == ["[이름-01]"]


def test_code_prefix_is_the_category_unless_a_role_is_given():
    book = AliasBook("M-20261110-01")
    assert book.code_for("계좌번호", "000-000-000012", "a") == "[계좌번호-01]"
    assert book.code_for("학교명", "가람초등학교", "a") == "[학교-01]"
    assert book.code_for("간접식별", "가람초 3학년 담임", "a") == "[소속직급-01]"
    assert book.code_for("간접식별", "올해 전입한 유일한 교사", "a", "간접식별") == "[간접식별-01]"


def test_spaced_name_shares_the_code_and_is_kept_written_together():
    """결재란의 `홍 길 동`과 본문의 `홍길동`은 같은 사람이다. 대응표에는 붙여 쓴 이름을 적는다."""
    book = AliasBook("M-20261110-01")
    assert book.code_for("이름", "홍 길 동", "a") == book.code_for("이름", "홍길동", "a") == "[이름-01]"
    assert [e.raw_value for e in book.entries()] == ["홍길동"]


def test_same_value_same_code_and_numbering_per_category():
    book = AliasBook("M-20261107-01")
    assert book.code_for("이름", "김민수", "a") == "[이름-01]"
    assert book.code_for("이름", "이도윤", "a") == "[이름-02]"
    assert book.code_for("이름", "김민수", "b") == "[이름-01]"  # 같은 사람은 같은 가명코드
    assert book.code_for("전화", "010-0000-0001", "a") == "[전화-01]"
    assert book.code_for("전화", "010 0000 0001", "b") == "[전화-01]"
    assert book.code_for("전화", "０１０－００００－０００１", "b") == "[전화-01]"  # 전각 숫자


def test_hundredth_name_gets_three_digits():
    book = AliasBook("M-20261109-01")
    codes = [book.code_for("이름", f"사람{i:03d}", "a") for i in range(100)]
    assert codes[0] == "[이름-01]" and codes[98] == "[이름-99]" and codes[99] == "[이름-100]"


def test_mapping_and_legend():
    book = AliasBook("M-20261107-01")
    book.code_for("이름", "김민수", "a.hwpx")
    book.code_for("이름", "김민수", "b.pdf")
    mapping = book.to_mapping()
    [entry] = mapping["entries"]
    assert entry["alias_code"] == "[이름-01]" and entry["raw_value"] == "김민수"
    assert entry["has_batchim"] is False and entry["source_files"] == ["a.hwpx", "b.pdf"]
    assert entry["created_at"]
    legend = book.legend()
    assert legend == [{"alias_code": "[이름-01]", "meaning": "1번 사람"}]
    assert "김민수" not in repr(legend) and "김민수" not in repr(book)


def test_continue_from_previous_mission():
    old = AliasBook("M-20261106-01")
    old.code_for("이름", "김민수", "a")
    old.code_for("이름", "이도윤", "a")
    book = AliasBook.continue_from("M-20261107-01", old.to_mapping())
    assert book.code_for("이름", "이도윤", "b") == "[이름-02]"
    assert book.code_for("이름", "정하람", "b") == "[이름-03]"
    assert [e.alias_code for e in book.entries()] == ["[이름-02]", "[이름-03]"]  # 이번에 쓴 것만


def test_continue_keeps_codes_from_missions_before_the_change():
    """역할 가명코드를 쓰던 때의 대응표를 이어 쓰면 그 사람은 예전 가명코드 그대로다."""
    old = {"mission_id": "M-20261101-01", "entries": [
        {"alias_code": "[강사-01]", "category": "이름", "raw_value": "김민수", "has_batchim": False,
         "source_files": ["a"], "created_at": "2026-11-01T00:00:00+00:00"}]}
    book = AliasBook.continue_from("M-20261109-01", old)
    assert book.code_for("이름", "김민수", "b") == "[강사-01]"
    assert book.code_for("이름", "이도윤", "b") == "[이름-01]"


def test_per_mission_numbering_is_default():
    a, b = AliasBook("M-20261107-01"), AliasBook("M-20261107-02")
    a.code_for("이름", "김민수", "x")
    assert b.code_for("이름", "이도윤", "x") == "[이름-01]"


# ---------------------------------------------------------------- 가명처리

def test_pending_findings_block_pseudonymize():
    res = doc(para("담당자: 박서연"))
    found = detect([res])
    with pytest.raises(PendingFindings):
        pseudonymize([res], found, AliasBook("M-20261107-01"))


def test_same_name_gets_same_code_everywhere():
    res = doc(table(["강사", "연락처"], ["김민수", "010-0000-0012"]), para("오전은 김민수 강사가, 다시 김민수.", 5))
    found = decide(detect([res]))
    [pdoc] = pseudonymize([res], found, AliasBook("M-20261107-01"))
    assert texts(pdoc) == ["강사", "연락처", "[이름-01]", "[전화-01]", "오전은 [이름-01] 강사가, 다시 [이름-01]."]


def test_excluded_and_reference_items_are_left_alone():
    res = doc(table(["강사", "소속", "비고"], ["미정", "예시학교", ""]))
    found = decide(detect([res]))
    assert {f.review_status for f in found} == {STATUS_EXCLUDED, STATUS_REFERENCE}
    [pdoc] = pseudonymize([res], found, AliasBook("M-20261107-01"))
    assert "미정" in texts(pdoc) and "예시학교" in texts(pdoc)


def test_rrn_and_account_are_deleted_and_not_in_mapping():
    res = doc(para("주민등록번호: 901301-1000000, 입금 계좌 000-0000-0000-01"))
    found = decide(detect([res]))
    book = AliasBook("M-20261107-01")
    [pdoc] = pseudonymize([res], found, book)
    assert texts(pdoc) == [f"주민등록번호: {DELETED}, 입금 계좌 {DELETED}"]
    assert book.to_mapping()["entries"] == []


def test_gray_bundle_becomes_one_code():
    res = doc(para("제가 예시교육지원청 회계팀 7급으로 있을 때"))
    found = decide(detect([res]))
    book = AliasBook("M-20261107-01")
    [pdoc] = pseudonymize([res], found, book)
    assert texts(pdoc) == ["제가 [소속직급-01]으로 있을 때"]
    assert [e.raw_value for e in book.entries()] == ["예시교육지원청 회계팀 7급"]


def test_original_blocks_are_not_changed():
    res = doc(para("담당자: 박서연"))
    found = decide(detect([res]))
    pseudonymize([res], found, AliasBook("M-20261107-01"))
    assert res.blocks[0].text == "담당자: 박서연"


# ---------------------------------------------------------------- 차단 목록, 마크다운

def test_blocklist_catches_leftover_and_reformatted_values():
    res = doc(para("담당자: 박서연, 010-0000-0012"))
    found = decide(detect([res]))
    ids = {f.category: f.finding_id for f in found}
    leftover = "결과: 박서연 그리고 010 0000 0012"
    assert sorted(blocklist_hits(leftover, found)) == sorted([ids["이름"], ids["전화"]])
    assert blocklist_hits("결과: 가명만 있음", found) == []


def test_new_shapes_round_trip_and_leave_nothing():
    """띄어 쓴 이름, 전각 숫자, 괄호 구분 전화, 가린 주민등록번호: 원문 → 가명 → 복원 = 원문(띄어 쓴 이름은
    붙여 쓴 이름으로 돌아온다). 가명처리한 글과 원본 형식 사본용 바꾸기 모두에 값이 남지 않는다."""
    from privacy_operator.restore.restore import restore
    from privacy_operator.writer.plan import ReplacementPlan

    res = doc(table(["성명", "연락처", "주민등록번호"], ["홍 길 동", "０１０－００００－００１２", "900101-1******"]),
              para("홍길동 강사에게 010)0000-0012 로 연락하고, sample@example.com 으로 보냄", 5),
              para("결재  교장  박 서 연", 6))
    found = decide(detect([res]))
    assert sorted(f.raw_value for f in found if f.category == "이름") == ["박 서 연", "홍 길 동", "홍길동"]
    book = AliasBook("M-20261110-01")
    [pdoc] = pseudonymize([res], found, book)
    out = "\n".join(texts(pdoc))
    assert out.count("[이름-01]") == 2 and out.count("[전화-01]") == 2 and "[삭제]" in out and "[이메일-01]" in out
    assert not blocklist_hits(out, found, pdoc.bundles)
    for left in ("홍 길 동", "홍길동", "박  서  연", "010 0000 0012", "900101-1******", "sample@example.com"):
        assert blocklist_hits(f"남은 글 {left}", found, pdoc.bundles), left

    plan = ReplacementPlan(found, [pdoc])
    for block in res.blocks:
        assert not plan.leftovers(plan.replace(block.text)), block.block_id
    assert plan.replace("홍  길  동, sample@example.com, 900101-1******") == "[이름-01], [이메일-01], [삭제]"

    back = restore("[이름-01]는 [전화-01]로, [이름-02]가 결재", book.to_mapping()).text
    assert back == "홍길동은 ０１０－００００－００１２로, 박서연이 결재"


def test_blocklist_reports_every_finding_of_a_value_that_is_left():
    """같은 값은 한 번만 찾지만, 남았으면 그 값의 탐지를 모두 알린다."""
    res = doc(para("담당자: 박서연, 010-0000-0012"), para("박서연 강사에게 010 0000 0012로 연락", 1))
    found = decide(detect([res]))
    names = [f.finding_id for f in found if f.category == "이름"]
    phones = [f.finding_id for f in found if f.category == "전화"]
    assert len(names) == 2 and len(phones) == 2
    assert blocklist_hits("박서연", found) == names
    assert blocklist_hits("연락 01000000012", found) == phones
    assert blocklist_hits("박 서연, 0100000001", found) == []  # 띄어 쓴 두 낱말, 자리수가 모자란 번호


def test_single_gray_value_is_not_blocklisted_but_bundle_is():
    res = doc(table(["성명", "직위"], ["김민수", "교사"]), para("제가 예시교육지원청 회계팀 7급일 때", 5))
    found = decide(detect([res]))
    [pdoc] = pseudonymize([res], found, AliasBook("M-20261107-01"))
    assert blocklist_hits("신규 교사 연수", found, pdoc.bundles) == []
    assert blocklist_hits("예시교육지원청 회계팀 7급", found, pdoc.bundles)


def test_blocklist_error_has_ids_but_no_values():
    """가명처리한 글에 값이 남으면 멈춘다. 오류 메시지에도 원문 값이 없다(작전 패키지는 test_mission이 본다)."""
    res = doc(table(["성명"], ["김민수"]), para("본문 김민수", 3))
    found = decide(detect([res]))
    body = next(f for f in found if f.block_id == "b0003")
    found.remove(body)  # 본문 탐지를 놓쳤다고 치자
    [pdoc] = pseudonymize([res], found, AliasBook("M-20261107-01"))
    with pytest.raises(BlocklistHit) as e:
        check_blocklist(to_markdown(pdoc.blocks), found, pdoc.bundles)
    assert "김민수" not in str(e.value) and e.value.finding_ids


def test_markdown_skips_metadata_comments_and_hidden_blocks():
    blocks = [para("본문"), Block("meta-author", "metadata", "작성자값", Location(field="author")),
              Block("memo000-text", "comment", "메모값", Location(field="text")),
              Block("h", PARAGRAPH, "숨김값", Location(), hidden=True)]
    assert to_markdown(blocks) == "본문\n"


def test_markdown_table_escapes_pipes_and_keeps_empty_cells():
    md = to_markdown(table(["가", "나"], ["a|b", ""], ["", "c"]))
    assert md == "| 가 | 나 |\n| --- | --- |\n| a\\|b |  |\n|  | c |\n"


def test_quick_check_in_the_plan_finds_exactly_what_each_rule_finds():
    """작전 개시를 빠르게 하는 선검사가 하나라도 놓치면 개인정보가 남는다. 하나씩 본 결과와 같아야 한다."""
    from privacy_operator.writer.plan import ReplacementPlan

    res = doc(para("담당자: 박서연, 연락처: 010-0000-0001, 메일 a.b@example.kr, 주소: 가상시 하늘로 12"),
              table(["강사", "직위"], ["김민수", "교사"]))
    found = decide(detect([res]))
    book = AliasBook("M-20261109-01")
    pseudonymize([res], found, book)
    plan = ReplacementPlan(found)
    samples = ["0", "true", "박서연", "x박서연y", "01000000001", "010 0000 0001", "A.B@EXAMPLE.KR", "a.b@example.kr",
               "김민수는", "가김민수", "", "   ", "<박서연>", "박 서연", "0100000000", "010-0000-00012", "교사가",
               "가상시 하늘로\n12", "가상시\n하늘로 12", "김민\n수", "신규 교사라서", "000 1", "0 1 0 0 0 0 0 0 0 0 1"]
    for s in samples:
        for wrapped in (False, True):
            one_by_one = [i for i, r in enumerate(plan.rules) if (r.wrapped if wrapped else r.pattern).search(s)]
            assert set(one_by_one) <= set(plan.candidates(s)), s
        if not any(r.pattern.search(s) for r in plan.rules):
            assert plan.apply(s) == (s, 0)
    assert plan.replace("x박서연y, 가김민수 교사") == "x[이름-01]y, 가[이름-02] [직위-01]"


def test_gray_bundle_does_not_swallow_names_in_between():
    """공문 결재란: 직위 둘 사이에 이름이 있으면 묶지 않고 하나씩 바꾼다(이름이 직위 묶음 가명코드에 먹히지 않게)."""
    res = doc(para("주무관 윤채아      행정실장 선우진      교장 11/10 남궁하린"))
    found = detect([res])
    for f in found:
        if f.review_status in (STATUS_PENDING, STATUS_REFERENCE):
            f.review_status = STATUS_CONFIRMED
    book = AliasBook("M-20261110-01")
    [pdoc] = pseudonymize([res], found, book)
    text = texts(pdoc)[0]
    names = {f.raw_value: f.alias_code for f in found if f.category == "이름"}
    assert all(c.startswith("[이름-") for c in names.values()), names
    for code in names.values():
        assert code in text
    assert "소속직급" not in text and not any(n in text for n in names)
