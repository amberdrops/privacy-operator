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
from privacy_operator.writer.blocklist import BlocklistHit, blocklist_hits
from privacy_operator.writer.markdown import UnsafeOutputPath, to_markdown, write_markdown


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

@pytest.mark.parametrize("hint", ["강사", "담당자", "사무관", "원장", "응답자", "님", None])
def test_names_are_always_the_name_code_whatever_the_role(hint):
    """이름 가명코드에 역할이나 직함을 넣지 않는다(`[이름-01]`, 2026-11-09). 짐작한 역할이 틀리는 일이 있어서다."""
    assert AliasBook("M-20261109-01").code_for("이름", "김민수", hint, "a") == "[이름-01]"


def test_same_value_same_code_and_numbering_per_category():
    book = AliasBook("M-20261107-01")
    assert book.code_for("이름", "김민수", "강사", "a") == "[이름-01]"
    assert book.code_for("이름", "이도윤", "원장", "a") == "[이름-02]"
    assert book.code_for("이름", "김민수", "예금주", "b") == "[이름-01]"  # 같은 사람은 같은 가명코드
    assert book.code_for("전화", "010-0000-0001", None, "a") == "[전화-01]"
    assert book.code_for("전화", "010 0000 0001", None, "b") == "[전화-01]"


def test_hundredth_name_gets_three_digits():
    book = AliasBook("M-20261109-01")
    codes = [book.code_for("이름", f"사람{i:03d}", None, "a") for i in range(100)]
    assert codes[0] == "[이름-01]" and codes[98] == "[이름-99]" and codes[99] == "[이름-100]"


def test_mapping_and_legend():
    book = AliasBook("M-20261107-01")
    book.code_for("이름", "김민수", "강사", "a.hwpx")
    book.code_for("이름", "김민수", "강사", "b.pdf")
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
    old.code_for("이름", "김민수", None, "a")
    old.code_for("이름", "이도윤", None, "a")
    book = AliasBook.continue_from("M-20261107-01", old.to_mapping())
    assert book.code_for("이름", "이도윤", None, "b") == "[이름-02]"
    assert book.code_for("이름", "정하람", "강사", "b") == "[이름-03]"
    assert [e.alias_code for e in book.entries()] == ["[이름-02]", "[이름-03]"]  # 이번에 쓴 것만


def test_continue_keeps_codes_from_missions_before_the_change():
    """역할 가명코드를 쓰던 때의 대응표를 이어 쓰면 그 사람은 예전 가명코드 그대로다."""
    old = {"mission_id": "M-20261101-01", "entries": [
        {"alias_code": "[강사-01]", "category": "이름", "raw_value": "김민수", "has_batchim": False,
         "source_files": ["a"], "created_at": "2026-11-01T00:00:00+00:00"}]}
    book = AliasBook.continue_from("M-20261109-01", old)
    assert book.code_for("이름", "김민수", None, "b") == "[강사-01]"
    assert book.code_for("이름", "이도윤", None, "b") == "[이름-01]"


def test_per_mission_numbering_is_default():
    a, b = AliasBook("M-20261107-01"), AliasBook("M-20261107-02")
    a.code_for("이름", "김민수", "강사", "x")
    assert b.code_for("이름", "이도윤", "강사", "x") == "[이름-01]"


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


def test_single_gray_value_is_not_blocklisted_but_bundle_is():
    res = doc(table(["성명", "직위"], ["김민수", "교사"]), para("제가 예시교육지원청 회계팀 7급일 때", 5))
    found = decide(detect([res]))
    [pdoc] = pseudonymize([res], found, AliasBook("M-20261107-01"))
    assert blocklist_hits("신규 교사 연수", found, pdoc.bundles) == []
    assert blocklist_hits("예시교육지원청 회계팀 7급", found, pdoc.bundles)


def test_write_markdown_refuses_when_values_remain(tmp_path):
    res = doc(table(["성명"], ["김민수"]), para("본문 김민수", 3))
    found = decide(detect([res]))
    body = next(f for f in found if f.block_id == "b0003")
    found.remove(body)  # 본문 탐지를 놓쳤다고 치자
    [pdoc] = pseudonymize([res], found, AliasBook("M-20261107-01"))
    with pytest.raises(BlocklistHit) as e:
        write_markdown(pdoc, tmp_path / "out", found, tmp_path / "vault")
    assert "김민수" not in str(e.value)  # 오류 메시지에도 원문 값이 없다
    assert not (tmp_path / "out").exists()


def test_write_markdown_refuses_vault_folder(tmp_path):
    res = doc(para("본문"))
    [pdoc] = pseudonymize([res], [], AliasBook("M-20261107-01"))
    with pytest.raises(UnsafeOutputPath):
        write_markdown(pdoc, tmp_path / "vault" / "x", [], tmp_path / "vault")


def test_markdown_skips_metadata_comments_and_hidden_blocks():
    blocks = [para("본문"), Block("meta-author", "metadata", "작성자값", Location(field="author")),
              Block("memo000-text", "comment", "메모값", Location(field="text")),
              Block("h", PARAGRAPH, "숨김값", Location(), hidden=True)]
    md = to_markdown(blocks)
    assert md == "본문\n"
    assert "숨김값" in to_markdown(blocks, include_hidden=True)


def test_markdown_table_escapes_pipes_and_keeps_empty_cells():
    md = to_markdown(table(["가", "나"], ["a|b", ""], ["", "c"]))
    assert md == "| 가 | 나 |\n| --- | --- |\n| a\\|b |  |\n|  | c |\n"


def test_quick_check_in_the_plan_finds_exactly_what_each_rule_finds():
    """작전 개시를 빠르게 하는 합친 검사가 하나라도 놓치면 개인정보가 남는다. 하나씩 본 결과와 같아야 한다."""
    import re

    from privacy_operator.writer.plan import ReplacementPlan

    res = doc(para("담당자: 박서연, 연락처: 010-0000-0001, 메일 a.b@example.kr"), table(["강사"], ["김민수"]))
    found = decide(detect([res]))
    book = AliasBook("M-20261109-01")
    pseudonymize([res], found, book)
    plan = ReplacementPlan(found)
    assert plan._any() is not None
    samples = ["0", "true", "박서연", "x박서연y", "01000000001", "010 0000 0001", "A.B@EXAMPLE.KR", "a.b@example.kr",
               "김민수는", "가김민수", "", "   ", "<박서연>"]
    for s in samples:
        one_by_one = any(r.pattern.search(s) for r in plan.rules)
        assert bool(plan._any().search(s)) == one_by_one, s
        if not one_by_one:
            assert plan.apply(s) == (s, 0)
    plan.rules[0].pattern = re.compile(plan.rules[0].pattern.pattern, re.IGNORECASE)
    del plan._any_re
    assert plan._any() is None  # 옵션이 다른 규칙이 있으면 합치지 않는다


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
