"""형식 탐지: 주민등록번호, 외국인등록번호, 전화, 이메일, 계좌번호, 주소, 그리고 본문 속 회색 영역."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from privacy_operator.detect.dictionaries import (
    ACCOUNT,
    ADDRESS,
    AFFILIATION,
    AGE,
    BANK_NAMES,
    BIRTH,
    CARD,
    EMAIL,
    FRN,
    GRADE,
    LICENSE,
    NAME,
    ORG_SUFFIXES,
    PASSPORT,
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

    def overlaps(self, other: Candidate) -> bool:
        return self.start < other.end and other.start < self.end


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


# 모양만 다른 글자를 한 글자씩 같은 자리에 바꾼다(전각 숫자와 기호, 여러 가지 줄표, 넓은 빈칸, 가림 동그라미).
# 글자 수가 그대로라 바꾼 글에서 찾은 위치를 원문에 그대로 쓴다
_FOLD = str.maketrans(
    "０１２３４５６７８９（）：＠．，＋＊" + "‐‑‒–—―−－" + "\u00a0\u3000" + "●○",
    "0123456789():@.,+*" + "-" * 8 + "  " + "**")


def fold(text: str) -> str:
    """탐지와 값 찾기는 모두 이 함수로 모양을 맞춘 글에서 한다(줄표는 `-`, 전각 숫자는 반각).
    정규식에 영문 범위 밖 글자 묶음을 넣으면 만드는 데 열 배쯤 걸려, 글 쪽을 맞추는 편이 빠르기도 하다."""
    return text.translate(_FOLD)


# 아래 정규식은 모두 fold()로 모양을 맞춘 글에서 쓴다
MASK_CHARS = "*xX#"  # 번호를 가릴 때 쓰는 글자(●, ○는 fold()가 *로 바꾼다)
_MASK = f"[{re.escape(MASK_CHARS)}]"
RRN_RE = re.compile(r"(?<![\d-])(\d{6})\s?-\s?([1-8])(\d{6})(?![\d-])")
# 뒷자리를 가린 번호(`900101-1******`)와 성별 숫자까지만 쓴 번호(`900101-1`). 앞 여섯 자리와 성별 숫자만으로도
# 생년월일과 성별이다
RRN_MASKED_RE = re.compile(rf"(?<![\d-])(\d{{6}})\s?-\s?([1-8]?{_MASK}{{1,7}})(?![\d-]|{_MASK})")
RRN_SHORT_RE = re.compile(rf"(?<![\d-])(\d{{6}})\s?-\s?([1-8])(?![\d-]|{_MASK})")
RRN_PLAIN_RE = re.compile(r"(?<!\d)(\d{6})([1-8])(\d{6})(?!\d)")
# 전화 숫자 사이: 하이픈, 점, 닫는 괄호(`031)000-0001`)와 그 둘레 빈칸 하나(`010 - 0000 - 0012`), 또는 빈칸
_PSEP = r"(?:\s?[-.)]\s?|\s)"
_AREA = r"(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)"
MOBILE_RE = re.compile(rf"(?<![\d-])01[016789]{_PSEP}?\d{{3,4}}{_PSEP}?\d{{4}}(?![\d-])")
LANDLINE_RE = re.compile(
    rf"(?<![\d-])(?:\(0{_AREA}\)\s?|0{_AREA}{_PSEP})\d{{3,4}}{_PSEP}\d{{4}}(?![\d-])")
INTL_PHONE_RE = re.compile(
    rf"(?<![\d+])\+82[-.\s]?(?:\(0\)[-.\s]?)?(?:1[016789]|{_AREA})[-.\s]?\d{{3,4}}[-.\s]?\d{{4}}(?![\d-])")
REPRESENTATIVE_RE = re.compile(r"(?<![\d-])1[5-9]\d{2}-\d{4}(?![\d-])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ACCOUNT_RE = re.compile(r"(?<![\d-])\d{2,6}(?:-\d{2,7}){1,4}(?![\d-])")
ACCOUNT_PLAIN_RE = re.compile(r"(?<![\d-])\d{10,14}(?![\d-])")  # 구분자 없는 계좌. 은행 문맥이 있을 때만 본다
ACCOUNT_CONTEXT_RE = re.compile(r"계좌|은행|입금|예금주|뱅크|송금|농협|신협|수협|우체국|새마을금고")
# 흔한 낱말이기도 한 은행 이름(국민, 우리, 하나 …)은 번호 바로 앞이나 바로 뒤(`(신한)`)에 붙었을 때만 문맥으로 본다
_BANK_NAMES = _alt(BANK_NAMES)
_BANK_BEFORE_RE = re.compile(rf"(?:{_BANK_NAMES})\s*[:：]?\s*$")
_BANK_AFTER_RE = re.compile(rf"\s*\(?\s*(?:{_BANK_NAMES}|[가-힣]{{1,6}}은행)")
# 고유식별정보(개인정보 보호법 시행령 제19조): 주민등록번호, 여권번호, 운전면허번호, 외국인등록번호
LICENSE_RE = re.compile(r"(?<![\d-])\d{2}-\d{2}-\d{6}-\d{2}(?![\d-])")
PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[MSRODG](?:\d{8}|\d{3}[A-Z]\d{4})(?![A-Za-z0-9])")
PASSPORT_CONTEXT_RE = re.compile(r"여권|passport", re.IGNORECASE)
CARD_RE = re.compile(r"(?<![\d-])\d{4}([-\s])[\d*]{4}\1[\d*]{4}\1[\d*]{4}(?![\d*-])")
CARD_CONTEXT_RE = re.compile(r"카드")
# 본문 속 생년월일: 뒤에 '생'이 붙은 날짜(`1990. 1. 1.생`)와 앞에 '생년월일'이 온 날짜
_YMD = r"(?:19|20)\d{2}\s?[.년/-]\s?\d{1,2}\s?[.월/-]\s?\d{1,2}(?:\s?[.일])?"
BIRTH_BORN_RE = re.compile(rf"(?<![\d.])({_YMD})\s?(?:생|출생)(?![가-힣])")
BIRTH_LABEL_RE = re.compile(rf"(?:생년월일|출생일|생일)\s*[:：]?\s*({_YMD}|\d{{6}}(?![\d-]))")
# 괄호 속에 날짜나 여섯 자리 숫자만 있는 꼴(`김민수(1990. 1. 1.)`, `김민수(900101)`, `(90. 1. 1.)`).
# 바로 앞이 이름일 때만 생년월일로 본다. 그 판단은 rules가 한다
BIRTH_PAREN_RE = re.compile(rf"\(\s?({_YMD}|\d{{2}}\s?\.\s?\d{{1,2}}\s?\.\s?\d{{1,2}}\.?|\d{{6}})\s?\)")
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
AGE_RE = re.compile(r"(?<!\d)[1-9]0대(?:\s?(?:초반|중반|후반))?|(?<!\d)[1-9]\d세(?![대기])"
                    r"|(?<!\d)(?:19|20)?\d{2}년생(?![가-힣])")


def _date_ok(front: str) -> bool:
    return 1 <= int(front[2:4]) <= 12 and 1 <= int(front[4:6]) <= 31


def date_year(value: str, this_year: int) -> int | None:
    """`900101`, `90. 1. 1.`, `1990. 1. 1.`의 연도. 달과 날이 달력에 없는 숫자면 None.
    두 자리 연도는 올해보다 뒤가 되지 않는 쪽으로 읽는다(올해가 2026이면 `26`은 2026, `27`은 1927)."""
    nums = [value[0:2], value[2:4], value[4:6]] if value.isdigit() else re.findall(r"\d+", value)
    if len(nums) != 3 or not (1 <= int(nums[1]) <= 12 and 1 <= int(nums[2]) <= 31):
        return None
    year = int(nums[0])
    if len(nums[0]) == 2:
        year += 2000 if year <= this_year % 100 else 1900
    return year


def _rrn_validation(front: str, gender: str, back: str) -> str | None:
    """날짜와 검증숫자를 본다. 틀려도 후보에서 빼지 않고 메모만 남긴다."""
    notes = []
    if not _date_ok(front):
        notes.append("날짜 아님")
    digits = [int(d) for d in front + gender + back]
    check = (11 - sum(d * w for d, w in zip(digits[:12], (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5))) % 11) % 10
    if check != digits[12]:
        notes.append("검증숫자 불일치")  # 2020년 10월 이후 번호는 검증숫자가 없다
    return ", ".join(notes) or None


def _luhn_ok(number: str) -> bool:
    """카드번호 검증식. 가린 글자가 있으면 볼 수 없다."""
    digits = [int(c) for c in number if c in _DIGITS]
    if len(digits) != 16 or "*" in number:
        return False
    total = sum(digits[-1::-2]) + sum(sum(divmod(d * 2, 10)) for d in digits[-2::-2])
    return total % 10 == 0


def _bank_context(text: str, start: int, end: int) -> bool:
    """번호 둘레에 은행 문맥이 있는가: 앞 20자 안의 계좌, 입금 같은 말, 또는 바로 붙은 은행 이름."""
    before = text[max(0, start - 20): start]
    return bool(ACCOUNT_CONTEXT_RE.search(before) or _BANK_BEFORE_RE.search(before)
                or _BANK_AFTER_RE.match(text, end, end + 12))


def format_candidates(text: str, header_category: str | None = None) -> list[Candidate]:
    """text는 fold()로 모양을 맞춘 글.
    header_category: 이 텍스트가 속한 열이나 레이블이 가리키는 유형(계좌, 여권, 차량 문맥 판단용)."""
    out: list[Candidate] = []

    def add(m, category, tier, group=0, **kw):
        c = Candidate(m.start(group), m.end(group), category, tier, {BY_FORMAT}, **kw)
        if not any(c.overlaps(o) for o in out):
            out.append(c)

    def near(m, context: re.Pattern, width: int = 20) -> bool:
        return bool(context.search(text[max(0, m.start() - width): m.start()]))

    for m in RRN_RE.finditer(text):
        cat = FRN if m.group(2) in "5678" else RRN
        add(m, cat, TIER_CERTAIN, validation=_rrn_validation(*m.groups()))
    for m in RRN_MASKED_RE.finditer(text):
        cat = FRN if m.group(2)[0] in "5678" else RRN
        add(m, cat, TIER_CERTAIN if _date_ok(m.group(1)) else TIER_REVIEW)
    for m in RRN_PLAIN_RE.finditer(text):  # 붙여 쓴 13자리: 다른 번호일 수 있어 검토
        if _date_ok(m.group(1)):
            cat = FRN if m.group(2) in "5678" else RRN
            add(m, cat, TIER_REVIEW, validation=_rrn_validation(*m.groups()))
        else:  # 앞 여섯 자리가 날짜가 아니면 주민등록번호일 수 없다. 계좌번호 후보로 본다
            sure = header_category == ACCOUNT or _bank_context(text, m.start(), m.end())
            add(m, ACCOUNT, TIER_CERTAIN if sure else TIER_REVIEW)
    for m in RRN_SHORT_RE.finditer(text):  # 날짜 뒤에 번호 하나: 다른 번호일 수 있어 날짜가 맞을 때만 검토
        if _date_ok(m.group(1)):
            add(m, FRN if m.group(2) in "5678" else RRN, TIER_REVIEW)
    for m in EMAIL_RE.finditer(text):
        add(m, EMAIL, TIER_CERTAIN)
    for regex in (INTL_PHONE_RE, MOBILE_RE, LANDLINE_RE):
        for m in regex.finditer(text):
            add(m, PHONE, TIER_CERTAIN)
    for m in REPRESENTATIVE_RE.finditer(text):
        add(m, PHONE, TIER_REVIEW, exclude_hint=HINT_REPRESENTATIVE)
    for m in LICENSE_RE.finditer(text):
        add(m, LICENSE, TIER_CERTAIN)
    for m in CARD_RE.finditer(text):
        sure = header_category == CARD or _luhn_ok(m.group()) or near(m, CARD_CONTEXT_RE)
        add(m, CARD, TIER_CERTAIN if sure else TIER_REVIEW)
    for m in PASSPORT_RE.finditer(text):
        sure = header_category == PASSPORT or near(m, PASSPORT_CONTEXT_RE)
        add(m, PASSPORT, TIER_CERTAIN if sure else TIER_REVIEW)
    for regex in (BIRTH_BORN_RE, BIRTH_LABEL_RE):
        for m in regex.finditer(text):
            add(m, BIRTH, TIER_CERTAIN, group=1)
    for m in ACCOUNT_RE.finditer(text):
        digits = sum(ch.isdigit() for ch in m.group())
        if not 10 <= digits <= 16:
            continue
        sure = header_category == ACCOUNT or _bank_context(text, m.start(), m.end())
        add(m, ACCOUNT, TIER_CERTAIN if sure else TIER_REVIEW)
    for m in ACCOUNT_PLAIN_RE.finditer(text):
        if header_category == ACCOUNT or _bank_context(text, m.start(), m.end()):
            add(m, ACCOUNT, TIER_CERTAIN)
    for m in ADDRESS_RE.finditer(text):
        add(m, ADDRESS, TIER_REVIEW)
    return out


NUMERIC_CATEGORIES = frozenset({PHONE, RRN, FRN, ACCOUNT, LICENSE, CARD})  # 숫자 사이 구분자를 바꿔 써도 잡는 유형
_DIGITS = "0123456789"
_TOKENS = _DIGITS + MASK_CHARS
_NUMERIC_SEP = r"[-.()\s]{0,3}"  # 숫자 사이에 올 수 있는 구분자
_NUMERIC_BODY = re.compile(rf"[{_DIGITS}](?:{_NUMERIC_SEP}[{re.escape(_TOKENS)}])*")
SPACE = r"[ \t]"  # 한 줄 안의 빈칸(줄바꿈은 넣지 않는다)
_SPACED_SYLLABLES = re.compile(rf"[가-힣](?:{SPACE}+[가-힣])+")


_HANGUL = re.compile(r"[가-힣]")
# 빈칸으로 이어진 한글 낱자들. 가운데 글자는 홀로 떨어져 있고, 첫 글자 앞과 끝 글자 뒤에는 글자가 붙어 있어도 된다
_SPACED_SEQ = re.compile(rf"[가-힣](?:{SPACE}+[가-힣](?={SPACE}))*{SPACE}+[가-힣]")


def spaced_known(text: str, names: set[str] | frozenset[str]) -> list[tuple[int, int]]:
    """아는 이름(붙여 쓴 꼴의 집합)을 한 글자씩 띄어 쓴 자리(`김 민 수`, `허    준`). fold()한 글에서 쓴다.
    value_pattern이 이름 하나로 찾는 것과 같은 자리를, 이름이 수천 개여도 글을 한 번만 훑어 찾는다:
    세 글자 이상은 앞뒤를 따지지 않고(`김 민 수는`), 두 글자는 앞뒤가 한글이 아닐 때만."""
    out = []
    for run in _SPACED_SEQ.finditer(text):
        at = [run.start() + m.start() for m in _HANGUL.finditer(run.group())]
        word = "".join(text[p] for p in at)
        joined_left = bool(_HANGUL.match(text[run.start() - 1: run.start()]))
        joined_right = bool(_HANGUL.match(text[run.end(): run.end() + 1]))
        i = 0
        while i < len(word) - 1:
            for n in range(min(5, len(word) - i), 1, -1):
                lone = not (i == 0 and joined_left) and not (i + n == len(word) and joined_right)
                if word[i: i + n] in names and (n > 2 or lone):
                    out.append((at[i], at[i + n - 1] + 1))
                    i += n - 1
                    break
            i += 1
    return out


def is_spaced_name(value: str) -> bool:
    """한 글자씩 띄어 쓴 한글인가(`홍 길 동`)."""
    return bool(_SPACED_SYLLABLES.fullmatch(fold(value.strip())))


def _numeric_tokens(category: str, folded: str) -> str | None:
    """번호 유형 값의 숫자와 가린 글자(`010-0000-0012` → `01000000012`, `900101-1******` → `9001011******`).
    번호 유형이 아니거나, 숫자 사이에 구분자가 아닌 글자가 낀 값(`내선 12, 34`)이면 None: 글자 그대로 찾는다."""
    if category not in NUMERIC_CATEGORIES:
        return None
    m = _NUMERIC_BODY.search(folded)
    if not m or any(c in _DIGITS for c in folded[:m.start()] + folded[m.end():]):
        return None
    return "".join(c for c in m.group() if c in _TOKENS)


def value_pattern(category: str, raw_value: str) -> re.Pattern:
    """확정된 값이 글 안에 또 있는지 찾는 정규식(차단 목록, 확정값 재검색, 원본 형식 사본이 함께 쓴다).
    **fold()로 모양을 맞춘 글에서 쓴다.** 찾은 위치는 원문에 그대로 쓸 수 있다.

    - 번호 유형은 구분자를 바꿔 쓴 것도 잡는다(010-0000-0001, 010 0000 0001, 010)0000-0001, 전각 숫자).
    - 이름은 한 글자씩 띄어 쓴 것도 잡는다(결재란의 `김 민 수`, 빈칸이 몇 칸이든). 두 글자 이름의 띄어 쓴 꼴은
      앞뒤가 한글이 아닌 자리에서만(`허 준`은 잡고 `불허 준수`, `허 준비`는 아니다. 2026-11-11부터 붙여 쓴
      두 글자 이름을 확정했을 때도 본다).
    - 그 밖에는 글자 그대로. 한글 값이 더 긴 낱말 속에 있어도 잡는다(가정하람 안의 정하람): 건너뛴 자리가
      송출 직전 차단 목록에 걸리지 않게 확정하는 순간 대기로 보여 준다(2026-11-08).
    어떤 값이든 그 값 자신은 반드시 찾는다.
    """
    value = fold(raw_value.strip())
    tokens = _numeric_tokens(category, value)
    if tokens:
        parts = [_MASK if c in MASK_CHARS else c for c in tokens]
        return re.compile(r"(?<!\d)" + _NUMERIC_SEP.join(parts) + r"(?!\d)")
    compact = re.sub(r"\s+", "", value)
    if category == NAME and re.fullmatch(r"[가-힣]{2,5}", compact):
        if compact == value or _SPACED_SYLLABLES.fullmatch(value):
            wide = f"{SPACE}+".join(compact)
            if len(compact) >= 3:
                # 가운데 글자가 홀로 떨어져 있어야 하므로 다른 낱말과 겹칠 일이 드물다. 앞뒤는 따지지 않는다
                # (`[가-힣]` 묶음은 정규식을 만드는 데 1ms씩 걸려, 이름이 수천 개인 명단에서 몇 초가 된다)
                return re.compile(f"{compact}|{wide}")
            return re.compile(rf"{compact}|(?<![가-힣]){wide}(?![가-힣])")  # `허 준`: `불허 준수`와 가린다
    return re.compile(re.escape(value))


def probe(category: str, raw_value: str) -> tuple[bool, str]:
    """값이 글에 있을 수 있는지 싸게 먼저 볼 열쇠: (숫자인가, 숫자만 또는 공백을 뺀 값). ValueIndex에 넣는다.
    유형이 없는 글자(회색 영역 낱말, 묶음)는 category를 빈 글자로 준다."""
    value = fold(raw_value.strip())
    tokens = _numeric_tokens(category, value)
    if tokens:
        return True, "".join(c for c in tokens if c in _DIGITS)
    return False, re.sub(r"\s+", "", value)


class ValueIndex:
    """값 여러 개 가운데 이 글에 있을 수 있는 것만 골라 주는 선검사. 큰 명단은 값이 수천 개라 값마다
    정규식으로 글을 훑으면 문서 하나에 몇 초씩 걸렸다(2026-11-10).

    `candidates(text)`에 없는 값은 value_pattern, 줄바꿈 허용 정규식, 묶음 글자 어느 것으로도 그 글에서
    찾지 못한다(공백을 모두 뺀 글, 숫자만 모은 글에서 본다). 나온 값은 정규식으로 다시 확인해야 한다.
    값은 probe()가 준 열쇠로 넣는다. 돌려주는 것은 넣은 순서 번호다. 글은 fold()한 것이든 아니든 된다.
    """

    def __init__(self, keys: list[tuple[bool, str]]):
        self.keys = keys
        self._pairs: dict[tuple[str, str], list[int]] = {}  # 값의 첫 두 글자 → 번호
        self._tails: dict[tuple[str, ...], list[int]] = {}  # 번호의 끝 네 자리 → 번호
        self._rest: list[int] = []  # 너무 짧아 색인에 못 넣는 값. 늘 하나씩 본다
        for i, (numeric, key) in enumerate(keys):
            if numeric and len(key) >= 4:
                self._tails.setdefault(tuple(key[-4:]), []).append(i)
            elif not numeric and len(key) >= 2:
                self._pairs.setdefault((key[0], key[1]), []).append(i)
            else:
                self._rest.append(i)

    def candidates(self, text: str) -> list[int]:
        return sorted(self.scan(text))

    def scan(self, text: str):
        """candidates와 같은 번호를 순서 없이 하나씩 낸다. 몇 개만 찾으면 될 때 다 보지 않고 멈출 수 있다."""
        if not text or not self.keys:
            return
        text = fold(text)
        packed = re.sub(r"\s+", "", text)
        digits = re.sub(r"\D+", "", text) if self._tails or self._rest else ""
        for index, grams, body in (
                (self._pairs, zip(packed, packed[1:]), packed),
                (self._tails, zip(digits, digits[1:], digits[2:], digits[3:]), digits)):
            if not index:
                continue
            seen = set(grams)
            groups = ([index[g] for g in seen if g in index] if len(seen) < len(index)
                      else [ids for g, ids in index.items() if g in seen])
            for ids in groups:
                for i in ids:
                    if self.keys[i][1] in body:
                        yield i
        for i in self._rest:
            numeric, key = self.keys[i]
            if not key or key in (digits if numeric else packed):
                yield i


def gray_text_candidates(text: str) -> list[Candidate]:
    out = []
    for regex, cat in ((ORG_RE, AFFILIATION), (POSITION_RE, POSITION), (GRADE_RE, GRADE), (AGE_RE, AGE)):
        for m in regex.finditer(text):
            out.append(Candidate(m.start(), m.end(), cat, TIER_REFERENCE, {BY_GRAY_TEXT}))
    return out
