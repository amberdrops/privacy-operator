"""블록 하나에서 후보를 만든다: 형식, 열 제목, 레이블, 역할어, 본문 회색 영역, 문서 속성 작성자."""

from __future__ import annotations

import re

from privacy_operator.detect.dictionaries import (
    ADDRESS,
    ADDRESS_HEADERS,
    ADDRESS_LABELS,
    FORMAT_HEADERS,
    FORMAT_LABELS,
    GRAY_HEADERS,
    GRAY_LABELS,
    NAME,
    NAME_HEADER_SUFFIXES,
    NAME_LABELS,
    NOT_NAMES,
    ROLE_AFTER,
    ROLE_BEFORE,
    SIGN_ROLES,
    TITLE_WORDS,
    looks_like_name,
    normalize_header,
    strip_josa,
)
from privacy_operator.detect.finding import (
    BY_COLUMN,
    BY_LABEL,
    BY_METADATA,
    BY_ROLE,
    TIER_CERTAIN,
    TIER_RANK,
    TIER_REFERENCE,
    TIER_REVIEW,
)
from privacy_operator.detect.patterns import Candidate, _alt, format_candidates, gray_text_candidates
from privacy_operator.reader.blocks import CELL, COMMENT, METADATA, Block

AUTHOR_FIELDS = frozenset({"author", "creator", "lastModifiedBy", "last_modified_by"})
_ORG_TAIL = tuple("팀과부실청원교회사소처단국")

# 역할어 앞뒤 이름이 이 글자로 끝나면 조사가 붙은 낱말로 본다(`문의는 담당자`, `강사 소개를`)
_JOSA_END = frozenset("는를의에로도와께며랑만을과가")

# `/`는 날짜(`11/09`) 안에서는 나누지 않는다
_NAME_SPLIT = re.compile(r"[,，、·;\n]|/(?!\d)|(?<!\d)/|\s{2,}")
_NEXT_LABEL = re.compile(r"\s[^\s:：]{1,12}\s*[:：]|\n")
_PAREN_INNER = re.compile(r"[(（\[]([^()（）\[\]]*)[)）\]]")


# 직위 뒤에 붙어도 되는 조사(`교무부장과 협의`). 다른 낱말이 붙으면(`부장회의`) 직위가 아니다
_JOSA_AFTER = "(?:께서|에게|으로|과|와|이|가|은|는|을|를|의|께|님|도|만|로)?"
def _spaced_alt(words) -> str:
    """결재란은 칸을 맞추려고 직위 글자 사이를 띄운다(`원 장`, `교 감`, `주 무 관`). 글자 사이 빈칸 세 칸까지."""
    return "|".join(r"\s{0,3}".join(map(re.escape, w)) for w in sorted(words, key=len, reverse=True))


_TITLE_ALT = _spaced_alt(TITLE_WORDS)
# 직위로 끝나는 한 낱말(앞에 붙은 말은 10자까지): 주무관, 교육행정주무관, 행정연수담당사무관, 원 장
_TITLE_STEM = rf"[가-힣]{{0,10}}?(?:{_TITLE_ALT})"
_COMPOUND_TITLE = rf"{_TITLE_STEM}(?![가-힣])"
# 직위와 이름 사이: 빈칸, 쌍점(`원장: 박서연`), 괄호(`원장(박서연)`). 직위에 `님`이 붙기도 한다(`원장님 박서연`)
_TITLE_GAP = r"(?:\s*[:：(]\s*|\s+)"
# 최종 결재자는 직위와 이름 사이에 결재 날짜가 찍힌다(`원장 11/09 박서연`, `원장 2026. 11. 9. 박서연`)
_SIGN_DATE = r"\d{1,4}(?:\s*[./-]\s*\d{1,2}){1,2}\.?"


class RuleSet:
    """사용자 사전을 반영한 정규식 묶음."""

    def __init__(self, name_headers: frozenset[str], name_labels: frozenset[str]):
        self.name_headers = name_headers
        labels = set(name_labels) | set(FORMAT_LABELS) | set(ADDRESS_LABELS) | set(GRAY_LABELS)
        self.name_labels = frozenset(normalize_header(x) for x in name_labels)
        self.label_re = re.compile(rf"(?<![가-힣A-Za-z])({_alt(labels)})\s*[:：]\s*")
        self.role_after_re = re.compile(rf"(?<![가-힣])([가-힣]{{2,5}}?)\s?({_alt(ROLE_AFTER)})")
        # 결재란처럼 역할어가 이어지면(`기안자 주무관 박서연`) 모두 건너뛰고 이름을 본다.
        # 직위로 끝나는 합성어(`행정연수담당사무관`, `교무부장`)도 직위로 본다(2026-11-08)
        self.role_before_re = re.compile(
            rf"(?<![가-힣])((?:{_alt(ROLE_BEFORE)}|{_COMPOUND_TITLE})(?:\s+{_COMPOUND_TITLE})*|{_TITLE_STEM}님)"
            rf"{_TITLE_GAP}(?:{_SIGN_DATE}\s+)?([가-힣]{{2,8}})")
        # `김민수 교무부장`, `오세린 원 장과`: 이름 뒤 합성 직위나 띄어 쓴 직위
        self.role_after_compound_re = re.compile(
            rf"(?<![가-힣])([가-힣]{{2,5}})\s+([가-힣]{{0,10}}?(?:{_TITLE_ALT}))(?={_JOSA_AFTER}(?![가-힣]))")

    def is_name_header(self, h: str) -> bool:
        return h in self.name_headers or h.endswith(NAME_HEADER_SUFFIXES)

    def _names_under(self, h: str) -> bool:
        """이름이 들어가는 칸의 이름표: 이름 열 제목, 결재 역할, 직위."""
        return bool(h) and (self.is_name_header(h) or h in _SIGN_HEADERS or _is_title(h))

    def _known_header(self, h: str) -> bool:
        return self._names_under(h) or h in FORMAT_HEADERS or h in ADDRESS_HEADERS or h in GRAY_HEADERS

    def header_key(self, raw: str | None) -> str:
        """열 제목을 사전과 맞출 글자로. 괄호 밖이 모르는 말이면 괄호 안을 본다:
        `변경 전(성명)`, `변경 후(성명)`은 성명 열이다(2026-11-10)."""
        h = normalize_header(raw or "")
        if self._known_header(h):
            return h
        for inner in _PAREN_INNER.findall(raw or ""):
            k = normalize_header(inner)
            if self._known_header(k):
                return k
        return h

    # ---------------------------------------------------------------- 블록 전체

    def block_candidates(self, block: Block) -> list[Candidate]:
        text = block.text
        header = self.header_key(block.location.header)
        header_cat = FORMAT_HEADERS.get(header)

        if block.kind == METADATA and block.location.field in AUTHOR_FIELDS:
            return self._author(text)
        if block.kind == COMMENT and block.location.field == "author":
            return self._author(text)

        out = format_candidates(text, header_cat)
        if block.kind == CELL and not block.is_header and header:
            out += self._column(text, header, out)
        label = normalize_header(block.location.row_label or "")
        # 열 제목이 따로 정해진 칸(직위, 소속, 연락처 …)은 왼쪽 칸이 직위여도 이름 칸이 아니다
        # (`직급 | 직위` 표에서 `교육연구사 | 위원`의 위원이 이름으로 잡혔다, 2026-11-10)
        if block.kind == CELL and label and self._names_under(label) and not self._known_header(header):
            # 이름표가 왼쪽 칸에 있는 표(`원 장 | 2026. 10. 7. 박서연`, `성명 | 김민수`). 머리글 줄로 읽힌 칸은
            # 결재 날짜가 있을 때만 본다(첫 줄이 결재자인 결재란)
            if not block.is_header or re.search(_SIGN_DATE, text):
                out += self._column(text, label, out)
        out += self._labels(text, out)
        out += self._role_context(text)
        out += gray_text_candidates(text)
        return out

    # ---------------------------------------------------------------- 열 제목

    def _column(self, text: str, header: str, found: list[Candidate]) -> list[Candidate]:
        out = []
        # 결재란 머리글(직위, 결재 역할)은 이름 칸일 수 있지만 확실하지 않다. 이름 모양일 때만 '검토'로 본다
        sign = not self.is_name_header(header) and (header in _SIGN_HEADERS or _is_title(header))
        if self.is_name_header(header) or sign:
            for start, end in _parts(text):
                start = _skip_titles(text, start, end)
                if start is None:
                    continue  # 칸 조각이 직위뿐(`주무관`)
                value = text[start:end]
                first = value.split()[0]
                if len(value.split()) > 1 and looks_like_name(first):  # `김민수 교수` → 이름만
                    end = start + len(first)
                    value = first
                if not looks_like_name(value) and _is_sentence(value):
                    # 이름 칸에 든 문장(`현직 교사 중 선발 예정`, `행정지원과 주무관 박서연`):
                    # 통째로 잡지 않고 그 안의 이름 모양 낱말만(2026-11-09)
                    for m in _WORD.finditer(text, start, end):
                        word = strip_josa(m.group())
                        if _role_name_ok(word):
                            out.append(Candidate(m.start(), m.start() + len(word), NAME, TIER_REVIEW, {BY_COLUMN},
                                                 role=header))
                    continue
                if sign and not looks_like_name(value):
                    continue
                tier = TIER_CERTAIN if looks_like_name(value) and not sign else TIER_REVIEW
                out.append(Candidate(start, end, NAME, tier, {BY_COLUMN}, role=header))
        elif header in FORMAT_HEADERS:
            if not found:
                span = _trimmed(text, 0, len(text))
                if span and re.search(r"[\d@]", text[span[0]:span[1]]):
                    out.append(Candidate(*span, FORMAT_HEADERS[header], TIER_REVIEW, {BY_COLUMN}))
        elif header in ADDRESS_HEADERS:
            span = _trimmed(text, 0, _address_end(text, 0, len(text)))
            if span:
                out.append(Candidate(*span, ADDRESS, TIER_REVIEW, {BY_COLUMN}))
        elif header in GRAY_HEADERS:
            span = _trimmed(text, 0, len(text))
            if span:
                out.append(Candidate(*span, GRAY_HEADERS[header], TIER_REFERENCE, {BY_COLUMN}))
        return out

    # ---------------------------------------------------------------- 레이블

    def _labels(self, text: str, found: list[Candidate]) -> list[Candidate]:
        out = []
        for m in self.label_re.finditer(text):
            label = normalize_header(m.group(1))
            stop = _NEXT_LABEL.search(text, m.end())
            region = _trimmed(text, m.end(), stop.start() if stop else len(text))
            if not region:
                continue
            rs, re_ = region
            if label in self.name_labels:
                tok = re.match(r"[^\s,()\[\]]+", text[rs:re_])
                if not tok:
                    continue
                value = strip_josa(tok.group())
                tier = TIER_CERTAIN if looks_like_name(value) else TIER_REVIEW
                out.append(Candidate(rs, rs + len(value), NAME, tier, {BY_LABEL}, role=label))
            elif label in FORMAT_LABELS:
                if not any(c.start < re_ and rs < c.end for c in found):
                    out.append(Candidate(rs, re_, FORMAT_LABELS[label], TIER_REVIEW, {BY_LABEL}))
            elif label in ADDRESS_LABELS:
                span = _trimmed(text, rs, _address_end(text, rs, re_))
                if span:
                    out.append(Candidate(*span, ADDRESS, TIER_REVIEW, {BY_LABEL}))
            elif label in GRAY_LABELS:
                cut = re.search(r"[,;]", text[rs:re_])
                end = rs + cut.start() if cut else re_
                span = _trimmed(text, rs, end)
                if span:
                    out.append(Candidate(*span, GRAY_LABELS[label], TIER_REFERENCE, {BY_LABEL}))
        return out

    # ---------------------------------------------------------------- 역할어

    def _role_context(self, text: str) -> list[Candidate]:
        out = []
        for m in self.role_after_re.finditer(text):
            name = m.group(1)
            if _role_name_ok(name):
                out.append(Candidate(m.start(1), m.end(1), NAME, TIER_REVIEW, {BY_ROLE}, role=m.group(2)))
        for m in self.role_after_compound_re.finditer(text):
            name = m.group(1)
            if _role_name_ok(name):
                out.append(Candidate(m.start(1), m.end(1), NAME, TIER_REVIEW, {BY_ROLE},
                                     role=_base_title(m.group(2))))
        for m in self.role_before_re.finditer(text):
            name = strip_josa(m.group(2))
            if _role_name_ok(name):
                out.append(Candidate(m.start(2), m.start(2) + len(name), NAME, TIER_REVIEW, {BY_ROLE},
                                     role=_base_title(m.group(1))))  # 이름 바로 앞 직위(없으면 역할어)
        return out

    # ---------------------------------------------------------------- 작성자

    @staticmethod
    def _author(text: str) -> list[Candidate]:
        span = _trimmed(text, 0, len(text))
        if not span:
            return []
        value = text[span[0]:span[1]]
        if looks_like_name(value):
            return [Candidate(*span, NAME, TIER_CERTAIN, {BY_METADATA}, role="작성자")]
        if re.search(r"[가-힣]", value) and not value.endswith(_ORG_TAIL):
            return [Candidate(*span, NAME, TIER_REVIEW, {BY_METADATA}, role="작성자")]
        return []  # 프로그램 이름(영문)이나 부서 이름


_TITLES = frozenset(TITLE_WORDS) | frozenset(SIGN_ROLES) | frozenset(ROLE_AFTER) | frozenset(ROLE_BEFORE)
_SIGN_HEADERS = frozenset(SIGN_ROLES) | frozenset(TITLE_WORDS)
_LEADING_TITLE = re.compile(rf"(?:{_alt(SIGN_ROLES)}|{_COMPOUND_TITLE}|{_SIGN_DATE})(?:\s+|$)")


def _is_title(word: str) -> bool:
    """직위나 결재 역할, 또는 직위로 끝나는 합성어(`행정연수담당사무관`, `교무부장`)."""
    return word in _TITLES or any(word.endswith(t) and len(word) > len(t) for t in TITLE_WORDS)


def _base_title(word: str) -> str:
    """합성 직위는 끝의 직위만(가명코드 이름이 길어지지 않게): 행정연수담당사무관 → 사무관, `기안자 원 장` → 원장."""
    word = re.sub(r"\s+", "", word).removesuffix("님")
    if word in _TITLES:
        return word
    ends = [t for t in TITLE_WORDS if word.endswith(t)]
    return max(ends, key=len) if ends else word


def _role_name_ok(name: str) -> bool:
    # 조사를 떼면 이름이 아닌 말(`위원장은 연수원장이`, `위원은 부원장이`)도 뺀다(2026-11-10).
    # 이름 끝 '은', '이'는 이름일 수도 있어(김하은) 뗀 말이 사전에 있을 때만 뺀다
    bases = {name, strip_josa(name), name[:-1] if name[-1] in "은이" else name}
    return (len(name) >= 3 and looks_like_name(name) and name[-1] not in _JOSA_END
            and not any(b in NOT_NAMES or _is_title(b) for b in bases))


_WORD = re.compile(r"[가-힣]+")
# 주소 뒤에 문장이 이어지는 낱말(`3층 회의실에서 진행합니다`). 도로명의 `로`, `길`은 끊지 않는다
_ADDRESS_TAIL = re.compile(r"\s[가-힣]*(?:에서|에서는|으로써|입니다|합니다|됩니다|하며|하고|하여|예정)(?![가-힣])")


def _is_sentence(value: str) -> bool:
    """이름 칸 값이 문장인지: 한글 낱말이 셋 이상이거나, 두 낱말인데 이름 모양 낱말이 없으면."""
    words = _WORD.findall(value)
    return len(words) >= 3 or (len(words) == 2 and not any(looks_like_name(w) for w in words))


def _address_end(text: str, start: int, end: int) -> int:
    m = _ADDRESS_TAIL.search(text, start, end)
    return m.start() if m else end


def _skip_titles(text: str, start: int, end: int) -> int | None:
    """칸 조각 앞의 직위와 결재 날짜(`원장 11/09 박서연`의 원장, 11/09)를 건너뛴 시작점. 조각이 그것뿐이면 None."""
    while start < end:
        m = _LEADING_TITLE.match(text, start, end)
        if not m:
            return start
        start = m.end()
    return None


def _trimmed(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and (text[start].isspace() or text[start] in ",.;"):
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in ",.;"):
        end -= 1
    return (start, end) if start < end else None


def _parts(text: str):
    """이름 칸 하나에 여러 이름이 쉼표 등으로 들어 있으면 나눈다."""
    pos = 0
    for m in list(_NAME_SPLIT.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        span = _trimmed(text, pos, end)
        if span:
            yield span
        if m:
            pos = m.end()


# ---------------------------------------------------------------- 합치기

def merge(cands: list[Candidate]) -> list[Candidate]:
    """겹치는 후보를 하나로. 같은 유형이면 범위를 합치고 근거를 모은다.
    유형이 다르면 등급이 높은 쪽, 같으면 긴 쪽을 남긴다."""
    result: list[Candidate] = []
    for c in sorted(cands, key=lambda c: (c.start, -c.end)):
        c = Candidate(c.start, c.end, c.category, c.tier, set(c.detected_by), c.exclude_hint, c.validation, c.role)
        while True:
            hit = next((r for r in result if r.overlaps(c)), None)
            if hit is None:
                result.append(c)
                break
            result.remove(hit)
            if hit.category == c.category:
                c = Candidate(min(hit.start, c.start), max(hit.end, c.end), c.category,
                              max(hit.tier, c.tier, key=TIER_RANK.get), hit.detected_by | c.detected_by,
                              hit.exclude_hint if hit.exclude_hint == c.exclude_hint else None,
                              hit.validation or c.validation, _better_role(hit, c))
            else:
                c = max(hit, c, key=lambda x: (TIER_RANK[x.tier], x.end - x.start))
    return sorted(result, key=lambda c: c.start)


def _better_role(a: Candidate, b: Candidate) -> str | None:
    """열 제목이나 레이블에서 온 역할을 역할어보다 앞세운다."""
    def rank(c):
        return 2 if c.detected_by & {BY_COLUMN, BY_LABEL} else (1 if c.role else 0)
    best = max((a, b), key=lambda c: (rank(c), c.role is not None))
    return best.role or a.role or b.role
