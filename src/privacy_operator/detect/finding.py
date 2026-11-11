"""탐지 1건(finding). 필드는 docs/01_기획서.md 7장.

raw_value와 context는 원문이다. repr, 로그, 예외 메시지에 넣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from privacy_operator.detect.dictionaries import ACCOUNT, CARD, FRN, LICENSE, PASSPORT, RRN
from privacy_operator.reader.blocks import Location

# 등급
TIER_CERTAIN = "확실"
TIER_REVIEW = "검토"
TIER_REFERENCE = "참고"
TIER_RANK = {TIER_REFERENCE: 0, TIER_REVIEW: 1, TIER_CERTAIN: 2}

# 판정 상태. 탐지는 '대기'나 '참고'로만 만든다. '확정', '제외'는 사람만 정한다
STATUS_PENDING = "대기"
STATUS_REFERENCE = "참고"
STATUS_CONFIRMED = "확정"
STATUS_EXCLUDED = "제외"

# 처리
ACTION_ALIAS = "가명처리"
ACTION_DELETE = "삭제"
ACTION_KEEP = "유지"

DELETE_BY_DEFAULT = frozenset({RRN, FRN, ACCOUNT, PASSPORT, LICENSE, CARD})

# 탐지 근거(detected_by)
BY_FORMAT = "format"  # 형식(정규식)
BY_COLUMN = "column"  # 열 제목
BY_COLUMN_SHAPE = "column_shape"  # 제목은 모르지만 값 대부분이 이름 모양인 열
BY_AFTER_NAME = "after_name"  # 이름 바로 뒤 괄호 속 날짜(`김민수(1990. 1. 1.)`)
BY_LABEL = "label"  # `담당자: 박서연`
BY_ROLE = "role_context"  # `김민수 강사가`, `담당자 박서연에게`
BY_PROPAGATION = "name_propagation"  # 확실 등급 이름을 다른 곳에서 다시 찾음
BY_METADATA = "metadata"  # 문서 속성, 메모 작성자
BY_GRAY_TEXT = "gray_text"  # 본문 속 기관명, 직급, 나이대
BY_CONFIRMED = "confirmed_value"  # 사람이 확정한 값을 다른 곳에서 다시 찾음(7단계)
BY_AI_NER = "ai_schift"  # Schift 모델이 찾은 이름, 주소(9단계)
BY_AI_INDIRECT = "ai_ollama"  # Ollama 로컬 모델이 찾은 간접식별 후보(9단계)

# 승격 근거(escalated_by)
ESC_NAME_SAME_ROW = "name_same_row"
ESC_NAME_SAME_SENTENCE = "name_same_sentence"
ESC_GRAY_PAIR_ROW = "gray_pair_row"
ESC_GRAY_PAIR_SENTENCE = "gray_pair_sentence"

# 제외 후보 사유(exclude_hint)
HINT_EXCEPTION = "exception_list"
HINT_REPRESENTATIVE = "representative_number"


def default_status(tier: str) -> str:
    return STATUS_REFERENCE if tier == TIER_REFERENCE else STATUS_PENDING


def default_action(tier: str, category: str) -> str:
    if tier == TIER_REFERENCE:
        return ACTION_KEEP
    return ACTION_DELETE if category in DELETE_BY_DEFAULT else ACTION_ALIAS


@dataclass
class Finding:
    finding_id: str
    file_id: str
    filename: str
    format: str
    category: str
    raw_value: str
    context: str
    block_id: str
    start: int  # 블록 text 안 위치
    end: int
    location: Location
    sentence: int  # 블록 안 문장 순번. 조합 승격 단위
    detected_by: tuple[str, ...]
    tier: str
    review_status: str
    action: str
    escalated_by: str | None = None
    hidden: bool = False  # 숨김 시트처럼 화면에 안 보이는 곳에서 찾음
    tier_set_by_human: bool = False  # 사람이 등급을 바꿨으면 조합 승격이 다시 올리지 않는다
    exclude_hint: str | None = None  # 있으면 '제외 후보'. 빼는 것은 사람이 한다
    group_id: str | None = None  # 같은 문장의 회색 영역 묶음. 하나의 가명코드로 처리할 수 있다
    validation: str | None = None  # 형식 검증 메모(예: 날짜 아님). 검증 실패만으로 빼지 않는다
    note: str | None = None  # AI가 댄 까닭(원문이 섞일 수 있다). 화면에만 쓴다
    replacement: str | None = None
    alias_code: str | None = None

    def __repr__(self) -> str:
        return (f"Finding({self.finding_id!r}, category={self.category!r}, tier={self.tier!r}, "
                f"status={self.review_status!r}, by={self.detected_by!r})")

    __str__ = __repr__
