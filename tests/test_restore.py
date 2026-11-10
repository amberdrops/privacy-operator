"""복원: 정확한 가명코드, 변형 가명코드(확인 필요), 미복원 목록, 왕복. 값은 모두 지어낸 것이다."""

from __future__ import annotations

import logging

import pytest
from test_alias import decide, doc, para, table

from privacy_operator.alias.codes import AliasBook
from privacy_operator.alias.pseudonymize import pseudonymize
from privacy_operator.detect import detect
from privacy_operator.restore import find_variants, restore
from privacy_operator.writer.markdown import to_markdown


def mapping(**codes):
    return {"mission_id": "M-20261107-01", "entries": [
        {"alias_code": c, "category": "이름", "raw_value": v, "has_batchim": b, "source_files": []}
        for c, (v, b) in codes.items()]}


M = mapping(**{"[강사-01]": ("김민수", False), "[강사-02]": ("이도윤", True), "[응답자-001]": ("한가온", True)})


# ---------------------------------------------------------------- 정확한 가명코드

def test_exact_codes_are_restored_and_counted():
    res = restore("[강사-01]와 [강사-02], 다시 [강사-01].", M)
    assert res.text == "김민수와 이도윤, 다시 김민수."
    assert res.restored == {"[강사-01]": 2, "[강사-02]": 1}


def test_unknown_code_is_left_and_listed():
    res = restore("[강사-01]과 [강사-09]와 [다른-01]", M)
    assert res.text == "김민수와 [강사-09]와 [다른-01]"
    assert res.unknown_codes == {"[강사-09]": 1, "[다른-01]": 1}
    assert not res.complete


def test_missing_codes_are_listed():
    res = restore("[강사-01]만 남았다", M)
    assert res.missing_codes == ["[강사-02]", "[응답자-001]"]


def test_deleted_marker_is_untouched():
    assert restore("주민등록번호: [삭제]", M).text == "주민등록번호: [삭제]"


# ---------------------------------------------------------------- 변형 가명코드: 자동으로 바꾸지 않는다

@pytest.mark.parametrize("text,variant,code,kind", [
    ("담당은 강사-01 입니다", "강사-01", "[강사-01]", "bare"),
    ("담당은 [강사 01] 입니다", "[강사 01]", "[강사-01]", "bracketed"),
    ("담당은 [강사-1] 입니다", "[강사-1]", "[강사-01]", "bracketed"),
    ("담당은 (강사-02) 입니다", "(강사-02)", "[강사-02]", "bracketed"),
    ("담당은 ［강사－02］ 입니다", "［강사－02］", "[강사-02]", "bracketed"),
    ("담당은 강사 1 입니다", "강사 1", "[강사-01]", "bare"),
    ("담당은 강사2가 맡는다", "강사2", "[강사-02]", "bare"),
    ("담당은 강사 1번 입니다", "강사 1번", "[강사-01]", "bare"),
    ("담당은 01번 강사 입니다", "01번 강사", "[강사-01]", "number_first"),
    ("담당은 2번강사.", "2번강사", "[강사-02]", "number_first"),
    ("응답자 1의 의견", "응답자 1", "[응답자-001]", "bare"),
])
def test_variant_is_listed_not_replaced(text, variant, code, kind):
    res = restore(text, M)
    assert res.text == text  # 바꾸지 않았다
    [v] = res.variants
    assert (v.text, v.suggested_code, v.kind) == (variant, code, kind)
    assert code not in res.missing_codes  # 확인 필요에 있으니 '사라짐'으로 세지 않는다
    assert not res.complete


@pytest.mark.parametrize("text", [
    "강사 1명이 더 필요하다", "강사 3인", "1일차 강사 2일차", "강사 9 입니다",  # 9번 강사는 대응표에 없다
    "다른 역할-01", "강사 1번째", "강사 158", "[강사-01]",
])
def test_ordinary_text_is_not_a_variant(text):
    assert find_variants(text, ["[강사-01]", "[강사-02]"]) == []


def test_accepted_variant_is_restored_with_josa():
    first = restore("오전은 강사 2가 맡고 [강사-01]는 오후", M)
    [v] = first.variants
    second = restore("오전은 강사 2가 맡고 [강사-01]는 오후", M, accept={v.variant_id})
    assert second.text == "오전은 이도윤이 맡고 김민수는 오후"
    assert second.accepted == [v.variant_id] and second.complete


def test_result_repr_and_logs_have_no_values(caplog):
    with caplog.at_level(logging.DEBUG, logger="privacy_operator"):
        res = restore("[강사-01]는 강사 2와", M)
    assert "김민수" not in repr(res) and "김민수" not in caplog.text and caplog.records


# ---------------------------------------------------------------- 왕복 (4단계 완료 기준)

def test_roundtrip_with_josa_and_tables():
    original = doc(
        table(["강사", "연락처"], ["김민수", "010-0000-0012"], ["이도윤", "031-000-0013"]),
        para("오전은 김민수 강사가 맡고, 이도윤은 실습을 진행한다.", 10),
        para("김민수와 이도윤이 함께 발표하며, 문의는 010-0000-0012로 한다.", 11),
    )
    found = decide(detect([original]))
    book = AliasBook("M-20261107-01")
    [pdoc] = pseudonymize([original], found, book)
    sent = to_markdown(pdoc.blocks)
    assert "김민수" not in sent and "이도윤" not in sent
    back = restore(sent, book.to_mapping())
    assert back.text == to_markdown(original.blocks)
    assert back.complete and back.missing_codes == []


def test_ai_wrote_wrong_josa_and_it_is_fixed():
    """완료 기준: '[이름-01]는' 같은 어색한 조사가 실명 기준으로 고쳐진다."""
    original = doc(table(["강사"], ["김민수"], ["이도윤"]))
    found = decide(detect([original]))
    book = AliasBook("M-20261107-01")
    pseudonymize([original], found, book)
    ai_text = "[이름-01]는 이론을, [이름-02]는 실습을 맡았다. [이름-02]가 [이름-01]을 도왔다."
    assert restore(ai_text, book.to_mapping()).text == "김민수는 이론을, 이도윤은 실습을 맡았다. 이도윤이 김민수를 도왔다."
