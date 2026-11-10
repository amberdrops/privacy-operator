"""형식 탐지: 주민등록번호, 외국인등록번호, 전화, 이메일, 계좌번호, 주소, 그리고 본문 속 회색 영역."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from privacy_operator.detect.dictionaries import (
    ACCOUNT,
    ADDRESS,
    AFFILIATION,
    AGE,
    EMAIL,
    FRN,
    GRADE,
    ORG_SUFFIXES,
    PHONE,
    POSITION,
    POSITION_WORDS,
    RRN,
)
from privacy_operator.detect.finding import (
    BY_FORMAT,
    BY_GRAY_TEXT,
    HINT_REPRESENTATIVE,
    TIER_CERTAIN,
    TIER_REFERENCE,
    TIER_REVIEW,
)


@dataclass
class Candidate:
    start: int
    end: int
    category: str
    tier: str
    detected_by: set[str] = field(default_factory=set)
    exclude_hint: str | None = None
    validation: str | None = None
    role: str | None = None  # 열 제목, 레이블, 역할어. 가명코드의 역할 이름이 된다

    def overlaps(self, other: Candidate) -> bool:
        return self.start < other.end and other.start < self.end


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


RRN_RE = re.compile(r"(?<![\d-])(\d{6})\s?[-–]\s?([1-8])(\d{6})(?![\d-])")
RRN_PLAIN_RE = re.compile(r"(?<!\d)(\d{6})([1-8])(\d{6})(?!\d)")
MOBILE_RE = re.compile(r"(?<![\d-])01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?![\d-])")
LANDLINE_RE = re.compile(
    r"(?<![\d-])(?:\(0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)\)\s?|0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)[-.\s])"
    r"\d{3,4}[-.\s]\d{4}(?![\d-])")
REPRESENTATIVE_RE = re.compile(r"(?<![\d-])1[5-9]\d{2}-\d{4}(?![\d-])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ACCOUNT_RE = re.compile(r"(?<![\d-])\d{2,6}(?:-\d{2,7}){1,4}(?![\d-])")
ACCOUNT_CONTEXT_RE = re.compile(r"계좌|은행|입금|예금주|뱅크|송금")
# 주소: 시도, 시군구 뒤에 도로명(○○로 45) 또는 지번(○○동 123-4번지). 뒤에 층, 동, 호, 괄호 속 동 이름이 붙으면
# 함께 잡는다(`센텀중앙로 79, 3층`, `효원로 1 (인계동)`, 2026-11-10)
_ADDR_HEAD = r"(?:[가-힣]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면)\s+){1,4}"
_ADDR_ROAD = r"[가-힣0-9]+(?:로|길)\s?\d+(?:-\d+)?(?:\s?번길\s?\d+)?"
_ADDR_LOT = r"[가-힣][가-힣0-9]*(?:동|리|가)\s?\d+(?:-\d+)?(?:\s?번지)?"
_ADDR_TAIL = r"(?:,?\s?(?:지하\s?)?\d+층)?(?:,?\s?\d+동)?(?:\s?\d+호)?(?:\s?\([가-힣0-9,\s]{1,20}\))?"
ADDRESS_RE = re.compile(rf"{_ADDR_HEAD}(?:{_ADDR_ROAD}|{_ADDR_LOT}){_ADDR_TAIL}")

ORG_RE = re.compile(rf"(?<![가-힣])[가-힣]{{1,12}}(?:{_alt(ORG_SUFFIXES)})")
GRADE_RE = re.compile(r"(?<!\d)[1-9]급(?![수여식])")
POSITION_RE = re.compile(rf"(?<![가-힣])(?:{_alt(POSITION_WORDS)})")
AGE_RE = re.compile(r"(?<!\d)[1-9]0대(?:\s?(?:초반|중반|후반))?|(?<!\d)[1-9]\d세(?![대기])")


def _rrn_validation(front: str, gender: str, back: str) -> str | None:
    """날짜와 검증숫자를 본다. 틀려도 후보에서 빼지 않고 메모만 남긴다."""
    notes = []
    mm, dd = int(front[2:4]), int(front[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        notes.append("날짜 아님")
    digits = [int(d) for d in front + gender + back]
    check = (11 - sum(d * w for d, w in zip(digits[:12], (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5))) % 11) % 10
    if check != digits[12]:
        notes.append("검증숫자 불일치")  # 2020년 10월 이후 번호는 검증숫자가 없다
    return ", ".join(notes) or None


def format_candidates(text: str, header_category: str | None = None) -> list[Candidate]:
    """header_category: 이 텍스트가 속한 열이나 레이블이 가리키는 유형(계좌 문맥 판단용)."""
    out: list[Candidate] = []

    def add(m, category, tier, **kw):
        c = Candidate(m.start(), m.end(), category, tier, {BY_FORMAT}, **kw)
        if not any(c.overlaps(o) for o in out):
            out.append(c)

    for m in RRN_RE.finditer(text):
        cat = FRN if m.group(2) in "5678" else RRN
        add(m, cat, TIER_CERTAIN, validation=_rrn_validation(*m.groups()))
    for m in RRN_PLAIN_RE.finditer(text):  # 붙여 쓴 13자리: 다른 번호일 수 있어 검토
        cat = FRN if m.group(2) in "5678" else RRN
        add(m, cat, TIER_REVIEW, validation=_rrn_validation(*m.groups()))
    for m in EMAIL_RE.finditer(text):
        add(m, EMAIL, TIER_CERTAIN)
    for m in MOBILE_RE.finditer(text):
        add(m, PHONE, TIER_CERTAIN)
    for m in LANDLINE_RE.finditer(text):
        add(m, PHONE, TIER_CERTAIN)
    for m in REPRESENTATIVE_RE.finditer(text):
        add(m, PHONE, TIER_REVIEW, exclude_hint=HINT_REPRESENTATIVE)
    for m in ACCOUNT_RE.finditer(text):
        digits = sum(ch.isdigit() for ch in m.group())
        if not 10 <= digits <= 16:
            continue
        before = text[max(0, m.start() - 20): m.start()]
        sure = header_category == ACCOUNT or ACCOUNT_CONTEXT_RE.search(before)
        add(m, ACCOUNT, TIER_CERTAIN if sure else TIER_REVIEW)
    for m in ADDRESS_RE.finditer(text):
        add(m, ADDRESS, TIER_REVIEW)
    return out


_NUMERIC = frozenset({PHONE, RRN, FRN, ACCOUNT})
NUMERIC_CATEGORIES = _NUMERIC  # 숫자 사이 구분자를 바꿔 써도 잡는 유형(writer/plan.py)


def value_pattern(category: str, raw_value: str, word_start: bool = False) -> re.Pattern:
    """확정된 값이 글 안에 또 있는지 찾는 정규식(차단 목록, 확정값 재검색이 함께 쓴다).

    번호 유형은 구분자를 바꿔 쓴 것도 잡는다(010-0000-0001, 010 0000 0001, 01000000001).
    word_start=True면 한글 값이 더 긴 낱말의 뒷부분일 때는 잡지 않는다(가정하람 안의 정하람).
    처음 탐지의 확산은 True로 후보를 줄이고, 확정값 재검색과 차단 목록은 False로 하나도 놓치지 않는다
    (재검색이 True면 건너뛴 자리가 송출 직전에 걸렸다, 2026-11-08).
    """
    value = raw_value.strip()
    if category in _NUMERIC:
        digits = re.sub(r"\D", "", value)
        if digits:
            return re.compile(r"(?<!\d)" + r"[-.\s]?".join(digits) + r"(?!\d)")
    prefix = r"(?<![가-힣])" if word_start and re.match(r"[가-힣]", value) else ""
    return re.compile(prefix + re.escape(value))


def gray_text_candidates(text: str) -> list[Candidate]:
    out = []
    for regex, cat in ((ORG_RE, AFFILIATION), (POSITION_RE, POSITION), (GRADE_RE, GRADE), (AGE_RE, AGE)):
        for m in regex.finditer(text):
            out.append(Candidate(m.start(), m.end(), cat, TIER_REFERENCE, {BY_GRAY_TEXT}))
    return out
