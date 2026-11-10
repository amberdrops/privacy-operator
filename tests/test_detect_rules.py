"""규칙 하나하나. 엔진 없이 지어낸 블록으로 시험한다. 값은 모두 지어낸 것이다."""

from __future__ import annotations

import pytest

from privacy_operator.detect import DetectConfig, detect, escalate
from privacy_operator.detect.finding import (
    ACTION_ALIAS,
    ACTION_DELETE,
    ACTION_KEEP,
    BY_PROPAGATION,
    ESC_GRAY_PAIR_ROW,
    ESC_GRAY_PAIR_SENTENCE,
    ESC_NAME_SAME_ROW,
    HINT_EXCEPTION,
    HINT_REPRESENTATIVE,
    STATUS_PENDING,
    STATUS_REFERENCE,
    TIER_CERTAIN,
    TIER_REFERENCE,
    TIER_REVIEW,
)
from privacy_operator.reader.blocks import COMMENT, METADATA, PARAGRAPH, Block, Location, ReadResult
from privacy_operator.reader.convert import convert


def para(text, i=0):
    return Block(f"b{i:04d}", PARAGRAPH, text, Location(page=1, block_index=i))


def table(*rows):
    """첫 행이 머리글인 표를 kordoc 결과 모양으로 만들어 블록으로 바꾼다."""
    cells = [[{"text": t, "colSpan": 1, "rowSpan": 1} for t in row] for row in rows]
    raw = {"blocks": [{"type": "table", "pageNumber": 1,
                       "table": {"rows": len(rows), "cols": len(rows[0]), "cells": cells, "hasHeader": True}}]}
    return convert(raw, "hwpx")[0]


def run(*blocks, **cfg):
    flat = []
    for b in blocks:
        flat.extend(b if isinstance(b, list) else [b])
    return detect([ReadResult("t.hwpx", "hwpx", blocks=flat)], DetectConfig(**cfg))


def values(found, **where):
    return [f.raw_value for f in found if all(getattr(f, k) == v for k, v in where.items())]


# ---------------------------------------------------------------- 형식

def test_rrn_with_impossible_month_is_still_found():
    [f] = run(para("주민번호 901301-1000000 확인"))
    assert (f.category, f.tier, f.action) == ("주민등록번호", TIER_CERTAIN, ACTION_DELETE)
    assert "날짜 아님" in f.validation


def test_rrn_without_hyphen_is_review_and_foreigner_number_is_separate():
    found = run(para("번호 9013011000000, 외국인 900101-5000000"))
    assert values(found, category="주민등록번호", tier=TIER_REVIEW) == ["9013011000000"]
    assert values(found, category="외국인등록번호") == ["900101-5000000"]


@pytest.mark.parametrize("text", ["010-0000-0012", "01000000012", "010.0000.0012", "010 0000 0012",
                                  "02-000-0000", "031-000-0001", "(031) 000-0001", "070-0000-0001"])
def test_phone_formats(text):
    assert values(run(para(f"연락 {text} 으로")), category="전화") == [text]


def test_phone_inside_longer_number_is_not_phone():
    assert not values(run(para("코드 9010-0000-00123")), category="전화")


def test_representative_number_is_exclusion_candidate():
    [f] = run(para("대표번호 1588-0000"))
    assert f.exclude_hint == HINT_REPRESENTATIVE
    assert f.review_status == STATUS_PENDING  # 빼는 것은 사람이 한다


def test_email():
    assert values(run(para("메일은 a.b-c@example.kr 로")), category="이메일") == ["a.b-c@example.kr"]


def test_account_tier_depends_on_context():
    found = run(para("입금 계좌 000-0000-0000-01"), para("번호 000-0000-0000-02", 1))
    assert values(found, category="계좌번호", tier=TIER_CERTAIN) == ["000-0000-0000-01"]
    assert values(found, category="계좌번호", tier=TIER_REVIEW) == ["000-0000-0000-02"]
    assert all(f.action == ACTION_DELETE for f in found if f.category == "계좌번호")


def test_dates_and_phones_are_not_accounts():
    assert not values(run(para("2026-11-07 회의, 010-0000-0012")), category="계좌번호")


def test_road_address_in_text():
    [f] = run(para("거주지는 경기도 가상시 예시로 12-3 입니다"))
    assert (f.category, f.raw_value, f.tier) == ("주소", "경기도 가상시 예시로 12-3", TIER_REVIEW)


# ---------------------------------------------------------------- 레이블

def test_label_value_stops_at_next_label():
    # PDF에서 두 줄이 한 문단으로 합쳐진 모양
    found = run(para("주소: 가상시 예시동 1번지 연락처: 010-0000-0012"))
    assert values(found, category="주소") == ["가상시 예시동 1번지"]
    assert values(found, category="전화") == ["010-0000-0012"]


def test_name_label_strips_josa_and_parentheses():
    found = run(para("담당자: 박서연(운영팀)"), para("작성자 : 남궁별이 작성", 1))
    assert values(found, category="이름", tier=TIER_CERTAIN) == ["박서연", "남궁별"]


def test_name_label_with_non_name_value_is_review():
    assert values(run(para("담당자: 운영지원팀")), category="이름", tier=TIER_REVIEW) == ["운영지원팀"]


def test_gray_label_is_reference_and_kept():
    [f] = run(para("소속: 예시중앙회, 비고 없음"))
    assert (f.category, f.raw_value, f.tier, f.review_status, f.action) == (
        "소속", "예시중앙회", TIER_REFERENCE, STATUS_REFERENCE, ACTION_KEEP)


# ---------------------------------------------------------------- 역할어

def test_name_before_role_word():
    found = run(para("오전은 김민수 강사가, 오후는 이도윤강사님이, 남궁별 선생님도"))
    assert values(found, category="이름") == ["김민수", "이도윤", "남궁별"]
    assert all(f.tier == TIER_REVIEW for f in found)


@pytest.mark.parametrize("text", ["외부 강사가 온다", "문의는 담당자에게", "신규 교사 연수", "김산 강사"])
def test_role_word_without_a_name(text):
    # 마지막은 두 글자 이름: 역할어 규칙으로는 잡지 않는다(열, 레이블, 확산으로 잡는다)
    assert not values(run(para(text)), category="이름")


def test_name_after_role_word():
    found = run(para("문의는 담당자 박서연에게 한다"))
    assert values(found, category="이름") == ["박서연"]


@pytest.mark.parametrize("text", ["1. 강사 현황", "강사 소개를 한다", "강사 위촉 계약서", "담당자 명단을 본다"])
def test_role_word_followed_by_ordinary_word(text):
    assert not values(run(para(text)), category="이름")


# ---------------------------------------------------------------- 열 제목

def test_name_column_splits_names_and_drops_title():
    found = run(table(["강사", "비고"], ["김민수, 이도윤", ""], ["정하람 교수", ""]))
    assert values(found, category="이름", tier=TIER_CERTAIN) == ["김민수", "이도윤", "정하람"]


def test_name_column_header_variants():
    found = run(table(["강사 성명(정)", "교수요원"], ["김민수", "이도윤"]))
    assert values(found, category="이름", tier=TIER_CERTAIN) == ["김민수", "이도윤"]


def test_contact_column_without_format_is_review():
    found = run(table(["성명", "연락처"], ["김민수", "내선 0012"]))
    assert values(found, category="전화", tier=TIER_REVIEW) == ["내선 0012"]


def test_address_column():
    found = run(table(["성명", "주소"], ["김민수", "가상시 어딘가 마을"]))
    assert values(found, category="주소") == ["가상시 어딘가 마을"]


def test_exception_word_is_marked_not_dropped():
    found = run(table(["강사", "소속"], ["미정", "미정"]))
    assert len(found) == 2
    assert all(f.exclude_hint == HINT_EXCEPTION for f in found)
    assert found[0].review_status == STATUS_PENDING


def test_public_terms_are_not_detected():
    found = run(table(["성명", "소속"], ["김민수", "예시대학교"]), public_terms=frozenset({"예시대학교"}))
    assert values(found) == ["김민수"]


# ---------------------------------------------------------------- 조합 승격 (2단계 완료 기준)

def test_row_with_only_affiliation_stays_reference():
    [f] = run(table(["소속", "비고"], ["예시학교", "특이사항 없음"]))
    assert (f.category, f.tier, f.review_status, f.action, f.escalated_by) == (
        "소속", TIER_REFERENCE, STATUS_REFERENCE, ACTION_KEEP, None)


def test_row_with_affiliation_and_grade_is_raised_to_review():
    found = run(table(["소속", "직급"], ["예시학교", "7급"]))
    assert [(f.category, f.tier, f.review_status, f.action, f.escalated_by) for f in found] == [
        ("소속", TIER_REVIEW, STATUS_PENDING, ACTION_ALIAS, ESC_GRAY_PAIR_ROW),
        ("직급", TIER_REVIEW, STATUS_PENDING, ACTION_ALIAS, ESC_GRAY_PAIR_ROW),
    ]


def test_reference_item_in_same_row_as_name_is_raised():
    found = run(table(["성명", "근무지"], ["김민수", "예시청사"]))
    gray = [f for f in found if f.category == "근무지"]
    assert gray[0].tier == TIER_REVIEW and gray[0].escalated_by == ESC_NAME_SAME_ROW


def test_gray_pair_in_one_sentence_becomes_one_group():
    found = run(para("제가 예시교육지원청 회계팀 7급으로 있을 때. 다른 문장에 30대."))
    pair = [f for f in found if f.sentence == 0]
    assert {f.category for f in pair} == {"소속", "직급"}
    assert {f.escalated_by for f in pair} == {ESC_GRAY_PAIR_SENTENCE}
    assert len({f.group_id for f in pair}) == 1 and pair[0].group_id
    [alone] = [f for f in found if f.sentence == 1]
    assert (alone.category, alone.tier, alone.group_id) == ("나이대", TIER_REFERENCE, None)


def test_escalation_only_raises_and_respects_human_choice():
    found = run(table(["소속", "직급"], ["예시학교", "7급"]))
    # 사람이 하나를 '참고'로 내렸다
    found[0].tier, found[0].review_status, found[0].tier_set_by_human = TIER_REFERENCE, STATUS_REFERENCE, True
    escalate(found)
    assert found[0].tier == TIER_REFERENCE
    assert found[1].tier == TIER_REVIEW


def test_excluded_name_does_not_raise_neighbours():
    found = run(table(["강사", "소속"], ["미정", "예시학교"]))
    gray = [f for f in found if f.category == "소속"]
    assert gray[0].tier == TIER_REFERENCE


# ---------------------------------------------------------------- 확산, 작성자

def test_certain_name_propagates_to_body_and_other_documents():
    doc1 = ReadResult("a.hwpx", "hwpx", blocks=table(["성명"], ["정하람"]))
    doc2 = ReadResult("b.pdf", "pdf", blocks=[para("2일차는 정하람이 진행, 가정하람은 다른 말")])
    found = detect([doc1, doc2])
    body = [f for f in found if f.file_id == "D02"]
    assert [(f.raw_value, f.tier, f.start) for f in body] == [("정하람", TIER_REVIEW, 5)]
    assert BY_PROPAGATION in body[0].detected_by


@pytest.mark.parametrize("author,expected", [("구하윤", TIER_CERTAIN), ("kordoc", None), ("운영팀", None),
                                             ("Microsoft Office User", None)])
def test_author_fields(author, expected):
    blocks = [Block("meta-author", METADATA, author, Location(field="author")),
              Block("memo000-author", COMMENT, author, Location(field="author"))]
    found = run(blocks, propagate_names=False)
    assert [f.tier for f in found] == ([expected, expected] if expected else [])


def test_finding_repr_hides_values():
    [f] = run(para("담당자: 박서연"))
    assert "박서연" not in repr(f) and "박서연" not in str(f)


# ---------------------------------------------------------------- 공문서 결재란 (2026-11-08)

def test_sign_off_line_finds_every_signer_but_not_titles():
    """`기안자 주무관 박서연  검토자 행정실장 김민수  결재권자 교장 이도윤`: 직위 뒤 이름을 모두 찾고, 직위는 이름이 아니다."""
    found = run(para("기안자 주무관 박서연  검토자 행정실장 김민수  결재권자 교장 이도윤"))
    assert sorted(values(found, category="이름")) == ["김민수", "박서연", "이도윤"]
    roles = {f.raw_value: f.role_hint for f in found if f.category == "이름"}
    assert roles["박서연"] == "주무관" and roles["이도윤"] == "교장"


def test_title_alone_is_not_a_name():
    found = run(para("주무관 회의는 장학사 연수 뒤에 연구사 협의회와 함께 합니다"))
    assert not values(found, category="이름")


def test_sign_off_table_cells_skip_titles():
    """결재란 표: 머리 칸이 결재 역할이나 직위, 아래 칸이 `주무관 박서연`처럼 직위와 이름."""
    found = run(table(["기안자", "검토자", "교장"], ["주무관  박서연", "행정실장 김민수", "이도윤"]))
    assert sorted(values(found, category="이름")) == ["김민수", "박서연", "이도윤"]
    assert "주무관" not in values(found, category="이름")  # 직위로는 참고(요주의 구역)에 들어간다


def test_title_header_takes_only_name_like_values():
    """머리 칸이 직위(`부장`)인 표는 이름 모양인 값만 본다(업무 설명 같은 글은 이름이 아니다)."""
    found = run(table(["부장", "업무"], ["학년 협의 운영", "교육과정"], ["오세린", "평가"]))
    assert values(found, category="이름") == ["오세린"]


@pytest.mark.parametrize("text,name,role", [
    ("행정연수담당사무관 박서연", "박서연", "사무관"),
    ("교무부장 김민수", "김민수", "부장"),
    ("교육행정주무관 이도윤", "이도윤", "주무관"),
    ("김민수 교무부장과 협의", "김민수", "부장"),
    ("검토자 행정지원과장 오세린", "오세린", "과장"),
])
def test_compound_titles_before_and_after_names(text, name, role):
    """직위로 끝나는 합성어(앞에 업무, 부서 이름이 붙은 직위)도 직위로 본다. 가명코드 역할은 끝의 직위."""
    found = run(para(text))
    names = [f for f in found if f.category == "이름"]
    assert [f.raw_value for f in names] == [name]
    assert names[0].role_hint == role


@pytest.mark.parametrize("text", ["교무부장회의 일정 안내", "학년부장 협의회 자료", "행정연수담당사무관 업무 분장"])
def test_compound_titles_alone_are_not_names(text):
    assert not values(run(para(text)), category="이름")


@pytest.mark.parametrize("text,name,role", [
    ("원 장 박서연", "박서연", "원장"),
    ("교 장  이도윤", "이도윤", "교장"),
    ("교   감 정하람", "정하람", "교감"),  # 글자 사이 세 칸
    ("기안자 주 무 관 김민수  결재권자 교 감 오세린", "김민수", "주무관"),
    ("오세린 원 장과 협의", "오세린", "원장"),
])
def test_spaced_titles_in_sign_off_lines(text, name, role):
    """결재란은 칸을 맞추려고 직위 글자 사이를 띄운다(`원 장`, `교 감`). 띄어 써도 같은 직위다."""
    names = [f for f in run(para(text)) if f.category == "이름"]
    assert name in [f.raw_value for f in names]
    assert next(f for f in names if f.raw_value == name).role_hint == role


def test_spaced_title_in_sign_off_table_cell():
    found = run(table(["결재권자"], ["원 장 박서연"]))
    assert values(found, category="이름") == ["박서연"]


@pytest.mark.parametrize("text", ["학교 장학사 연수", "교 장 선생님 말씀", "원 장 회의 안내"])
def test_spaced_titles_do_not_make_names_from_words(text):
    assert not values(run(para(text)), category="이름")


@pytest.mark.parametrize("text", ["원장: 박서연", "원장 : 박서연", "원장(박서연)", "원장님 박서연", "교 장(이도윤)"])
def test_title_then_name_with_colon_paren_or_nim(text):
    names = [f for f in run(para(text)) if f.category == "이름"]
    assert len(names) == 1 and names[0].raw_value in ("박서연", "이도윤")
    assert names[0].role_hint in ("원장", "교장")


@pytest.mark.parametrize("word", ["전결", "대결", "서명", "공석", "출장"])
def test_sign_off_words_under_title_header_are_not_names(word):
    assert not values(run(table(["원장"], [word])), category="이름")


@pytest.mark.parametrize("text", ["원장(학교장) 결재", "원장: 회의 주재", "원장님 말씀", "원장(전결)"])
def test_title_with_colon_or_paren_does_not_make_names_from_words(text):
    assert not values(run(para(text)), category="이름")


@pytest.mark.parametrize("text", [
    "원장 11/09 박서연",
    "기안자 주무관 김민수  검토자 행정실장 정하람  결재권자 원장 11/09 박서연",
    "원장 2026. 11. 9. 박서연",
    "원장 2026.11.09. 박서연",
    "원장 11.9. 박서연",
    "원장 11/09\n박서연",
    "원장\n11/09\n박서연",
    "원 장 2026-11-09 박서연",
])
def test_final_approver_with_sign_date_between_title_and_name(text):
    """최종 결재자는 직위와 이름 사이에 결재 날짜가 찍힌다(2026-11-09 사용자 문서)."""
    names = [f for f in run(para(text)) if f.category == "이름"]
    assert "박서연" in [f.raw_value for f in names]
    assert next(f for f in names if f.raw_value == "박서연").role_hint == "원장"


@pytest.mark.parametrize("header,cell", [
    ("원장", "11/09\n박서연"),
    ("원장", "2026. 11. 9. 박서연"),
    ("결재", "원장 11/09 박서연"),
    ("결재권자", "원장 11/09 박서연"),
])
def test_sign_date_in_sign_off_table_cell(header, cell):
    assert values(run(table([header], [cell])), category="이름") == ["박서연"]


@pytest.mark.parametrize("header,cell", [("원장", "11/09"), ("결재권자", "원장 11/09"), ("원장", "전결 11/09")])
def test_sign_date_alone_is_not_a_name(header, cell):
    assert not values(run(table([header], [cell])), category="이름")


def test_slash_still_splits_names():
    assert values(run(table(["강사"], ["김민수/이도윤"])), category="이름") == ["김민수", "이도윤"]


def test_sign_off_table_with_titles_in_the_left_column():
    """결재란이 세로로 놓인 표: 왼쪽 칸이 직위, 오른쪽 칸이 결재 날짜와 이름(2026-11-09 사용자 문서 모양)."""
    found = run(table(["기안자", "주무관 김민수"], ["검토자", "행정실장 정하람"],
                      ["원 장", "2026. 10. 7.\n박서연"], ["협조자", ""]))
    names = {f.raw_value: f for f in found if f.category == "이름"}
    assert set(names) == {"김민수", "정하람", "박서연"}
    assert names["박서연"].role_hint == "원장"


def test_left_label_in_the_first_row_with_a_sign_date():
    found = run(table(["원 장", "2026. 10. 7.\n박서연"], ["협조자", ""]))
    assert values(found, category="이름") == ["박서연"]


def test_application_form_with_name_label_on_the_left():
    found = run(table(["구분", "내용"], ["성명", "김민수"], ["소속", "예시초등학교"]))
    assert values(found, category="이름") == ["김민수"]


@pytest.mark.parametrize("left,right", [("원 장", "2026. 10. 7."), ("원장", "전결"), ("구분", "박서연"),
                                         ("일시", "2026. 10. 7.")])
def test_left_label_does_not_make_names_from_other_values(left, right):
    assert not values(run(table(["항목", "값"], [left, right])), category="이름")


@pytest.mark.parametrize("rows", [
    (["항목", "내용"], ["담당자", "연수 운영 전반을 맡아 진행합니다."]),
    (["구분", "내용"], ["강사", "현직 교사와 외부 전문가로 구성"]),
    (["강사", "내용"], ["현직 교사 중 선발 예정", "x"]),
])
def test_sentence_in_a_name_cell_is_not_a_name(rows):
    """이름 칸에 든 문장을 통째로 이름 후보로 잡지 않는다(2026-11-09 사용자 문서)."""
    assert not values(run(table(*rows)), category="이름")


@pytest.mark.parametrize("cell,name", [("행정지원과 주무관 박서연", "박서연"), ("연수 담당 박서연 주무관", "박서연"),
                                       ("문의: 교무부 김민수 선생님께", "김민수")])
def test_name_inside_a_sentence_in_a_name_cell(cell, name):
    assert values(run(table(["담당자"], [cell])), category="이름") == [name]


@pytest.mark.parametrize("cell", ["김진", "남궁별", "제임스"])
def test_short_value_in_a_name_cell_is_still_a_candidate(cell):
    """한 낱말은 지금처럼 후보로 둔다(`미정` 같은 예외 낱말은 다음 단계의 예외 목록이 거른다)."""
    assert values(run(table(["강사"], [cell])), category="이름") == [cell]


@pytest.mark.parametrize("text,addr", [
    ("주소: 경기도 가상시 예시로 12, 3층 회의실에서 진행합니다.", "경기도 가상시 예시로 12, 3층"),
    ("주소: 경기도 가상시 예시길 7-3", "경기도 가상시 예시길 7-3"),
    ("주소: 가상시 예시대로 100 예시빌딩 5층", "가상시 예시대로 100 예시빌딩 5층"),
])
def test_address_label_stops_before_the_sentence_goes_on(text, addr):
    assert values(run(para(text)), category="주소") == [addr]


def test_long_opinion_next_to_an_instructor_label_keeps_only_the_name():
    """왼쪽 칸이 `강사`, 오른쪽 칸이 긴 의견 글인 표(2026-11-09 사용자 문서, 표12). 문장을 통째로 잡지 않는다."""
    cell = ("김민수 실장님 강의 진짜 최고입니다. 사례 위주라 이해가 잘 되었고 실무에 바로 쓸 수 있을 것 같습니다.\n"
            "다음 기수에도 같은 강의가 있으면 좋겠습니다.")
    found = [f for f in run(table(["구분", "주 요 내 용"], ["강사", cell])) if f.category == "이름"]
    assert [f.raw_value for f in found] == ["김민수"]
    assert all(len(f.raw_value) <= 5 for f in found)


@pytest.mark.parametrize("text,addr", [
    ("가상시 예시동 123-4번지에 거주", "가상시 예시동 123-4번지"),
    ("경기도 수원시 팔달구 효원로 1 (인계동)", "경기도 수원시 팔달구 효원로 1 (인계동)"),
    ("부산광역시 해운대구 센텀중앙로 79, 3층", "부산광역시 해운대구 센텀중앙로 79, 3층"),
    ("서울특별시 종로구 세종대로 209", "서울특별시 종로구 세종대로 209"),
    ("가상군 예시면 샘플리 45", "가상군 예시면 샘플리 45"),
])
def test_lot_number_and_address_details(text, addr):
    """지번 주소(○○동 123-4번지)와 도로명 뒤의 층, 괄호 속 동 이름까지 잡는다(2026-11-10)."""
    assert values(run(para(text)), category="주소") == [addr]


@pytest.mark.parametrize("text", ["연구 1동 3층 회의실", "2동 3호 열람실", "행정실 3층"])
def test_building_words_are_not_addresses(text):
    assert not values(run(para(text)), category="주소")


@pytest.mark.parametrize("text,addr", [
    ("우 12345  가상도 예시시 샘플읍 예시서원로 184(샘플읍, 가상도\n교육청연수원)",
     "가상도 예시시 샘플읍 예시서원로 184(샘플읍, 가상도\n교육청연수원)"),
    ("가상도 예시시 샘플읍 예시서원로 184", "가상도 예시시 샘플읍 예시서원로 184"),
])
def test_road_address_after_eup_with_a_note_in_parentheses(text, addr):
    """공문 아래 주소: 읍 다음 도로명, 뒤 괄호에 읍 이름과 기관 이름(줄이 바뀌기도 한다, 2026-11-10 사용자 문서 모양)."""
    assert values(run(para(text)), category="주소") == [addr]
