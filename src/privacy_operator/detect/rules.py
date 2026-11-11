"""블록 하나에서 후보를 만든다: 형식, 열 제목, 레이블, 역할어, 본문 회색 영역, 문서 속성 작성자."""

from __future__ import annotations

import re
from datetime import date

from privacy_operator.detect.dictionaries import (
    ADDRESS,
    ADDRESS_HEADERS,
    ADDRESS_LABELS,
    BIRTH,
    FORMAT_HEADERS,
    COMPOUND_SURNAMES,
    FORMAT_LABELS,
    GRAY_HEADERS,
    GRAY_LABELS,
    LONE_WORDS,
    NAME,
    NAME_HEADER_SUFFIXES,
    NOT_NAMES,
    NOUN_ENDINGS,
    RANK_WORDS,
    ROLE_AFTER,
    ROLE_BEFORE,
    SHORT_NOT_NAMES,
    SIGN_ROLES,
    SPACED_NAME_TAILS,
    TITLE_WORDS,
    WEAK_NAME_HEADER_SUFFIXES,
    WEAK_NAME_HEADERS,
    WEAK_ROLE_AFTER,
    WEAK_ROLE_BEFORE,
    looks_like_name,
    normalize_header,
    strip_josa,
)
from privacy_operator.detect.finding import (
    BY_AFTER_NAME,
    BY_COLUMN,
    BY_LABEL,
    BY_METADATA,
    BY_ROLE,
    TIER_CERTAIN,
    TIER_RANK,
    TIER_REFERENCE,
    TIER_REVIEW,
)
from privacy_operator.detect.patterns import (
    BIRTH_PAREN_RE,
    SPACE,
    Candidate,
    _alt,
    date_year,
    fold,
    format_candidates,
    gray_text_candidates,
)
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
# 이름 여럿을 잇는 말: 쉼표, '및', '와/과'. 이름 뒤 괄호(`김민수(교장), 이도윤`)는 건너뛴다
_LIST_SEP = r"(?:\s*[,、]\s*|\s+및\s+|[와과]\s+)"
_LIST_NEXT = re.compile(rf"(?:\s*\([^()]*\))?{_LIST_SEP}([가-힣]{{2,4}})(?![가-힣])")
_LIST_PREV = re.compile(rf"(?<![가-힣])([가-힣]{{2,4}}){_LIST_SEP}$")
# 이름에서 말이 끊기는 자리: 글 끝, 쉼표와 마침표, 괄호, 줄바꿈, 넓은 빈칸, 서명 표시, '등', '외', '및'
_STOP = re.compile(r"\s*(?:$|[,、.;:)(/\n]|㊞|[등외](?![가-힣])|및\s)|\s{2,}")
_SIGN_MARK = r"(?:\(\s?(?:인|서명|서명 또는 인|직인|印)\s?\)|㊞)"
# 사람에게 붙는 직위(부서 이름 뒤에 붙는 부장, 팀장, 실장은 뺀다). 두 글자 이름은 이 옆에서만 본다
_PERSON_RANKS = tuple(w for w in RANK_WORDS
                      if not w.endswith(("부장", "팀장", "과장", "실장", "국장", "처장", "원장")))
# 한 글자씩 띄어 쓴 한글(`홍 길 동`, `원 장  박 서 연`). 숫자에 붙은 글자는 단위라 뺀다(`2반 김 민 수`의 반)
_LONE = r"(?<![가-힣0-9])[가-힣](?![가-힣])"
_SPACED_RUN = re.compile(rf"{_LONE}(?:{SPACE}+{_LONE})+")


class RuleSet:
    """사용자 사전을 반영한 정규식 묶음."""

    def __init__(self, name_headers: frozenset[str], name_labels: frozenset[str]):
        self.name_headers = name_headers
        labels = set(name_labels) | set(FORMAT_LABELS) | set(ADDRESS_LABELS) | set(GRAY_LABELS)
        self.name_labels = frozenset(normalize_header(x) for x in name_labels)
        self.label_re = re.compile(rf"(?<![가-힣A-Za-z])({_alt(labels)})\s*:\s*", re.IGNORECASE)
        self.role_after_re = re.compile(
            rf"(?<![가-힣])([가-힣]{{2,5}}?)\s?({_alt(ROLE_AFTER + WEAK_ROLE_AFTER)})")
        # 결재란처럼 역할어가 이어지면(`기안자 주무관 박서연`) 모두 건너뛰고 이름을 본다.
        # 직위로 끝나는 합성어(`행정연수담당사무관`, `교무부장`)도 직위로 본다(2026-11-08)
        lead = (rf"(?<![가-힣])((?:{_alt(ROLE_BEFORE)}|{_COMPOUND_TITLE})(?:\s+{_COMPOUND_TITLE})*|{_TITLE_STEM}님)"
                rf"{_TITLE_GAP}(?:{_SIGN_DATE}\s+)?")
        self.role_before_re = re.compile(lead + r"([가-힣]{2,8})")
        self.role_lead_re = re.compile(lead + "$")  # 역할어로 끝나는 글(뒤에 띄어 쓴 이름이 올 때)
        # `김민수 교무부장`, `오세린 원 장과`: 이름 뒤 합성 직위나 띄어 쓴 직위
        self.role_after_compound_re = re.compile(
            rf"(?<![가-힣])([가-힣]{{2,5}})\s+([가-힣]{{0,10}}?(?:{_TITLE_ALT}))(?={_JOSA_AFTER}(?![가-힣]))")

        # `담임 김민수, 교사 이도윤`, `보호자 이도윤 (인)`: 근거가 약한 역할어. 이름에서 말이 끊길 때만 본다
        self.weak_before_re = re.compile(
            rf"(?<![가-힣])[가-힣]{{0,6}}?(?:{_alt(WEAK_ROLE_BEFORE)}){_TITLE_GAP}([가-힣]{{3,4}})(?![가-힣])")
        # `허준 주무관`, `교장 허준`: 두 글자 이름은 사람 직위와 띄어 쓴 자리에서만
        ranks = _alt(_PERSON_RANKS + ("선생님",))
        self.short_after_re = re.compile(
            rf"(?<![가-힣])([가-힣]{{2}})\s+(?:{ranks})(?={_JOSA_AFTER}(?![가-힣]))")
        self.short_before_re = re.compile(rf"(?<![가-힣])(?:{ranks})\s+([가-힣]{{2}})(?![가-힣])")
        self.sign_mark_re = re.compile(rf"(?<![가-힣])([가-힣]{{2,4}})\s{{0,3}}{_SIGN_MARK}")
        # `3학년 2반 김민수`, `1학년 3반 12번 김민수`
        self.class_re = re.compile(r"\d\s?학년\s?\d{1,2}\s?반(?:\s?\d{1,2}\s?번)?\s+([가-힣]{3,5})(?![가-힣])")
        # `행정지원과 총무팀 김민수`: 부서 이름 둘 뒤의 이름
        self.unit_re = re.compile(
            r"(?<![가-힣])(?:[가-힣]{2,8}(?:국|과|팀|실|부|센터|단|관)\s+){2}([가-힣]{3,4})(?![가-힣])")

    def is_name_header(self, h: str) -> bool:
        return h in self.name_headers or h.endswith(NAME_HEADER_SUFFIXES)

    def is_weak_name_header(self, h: str) -> bool:
        """이름이 올 수도 있는 칸의 이름표: 결재 역할, 직위, `학생`, `~교사`, `~자`, `~인`.
        값이 이름 모양일 때만 '검토'로 본다."""
        return (h in _SIGN_HEADERS or _is_title(h) or h in WEAK_NAME_HEADERS
                or (len(h) >= 3 and h.endswith(WEAK_NAME_HEADER_SUFFIXES)))

    def _names_under(self, h: str) -> bool:
        """이름이 들어가는 칸의 이름표: 이름 열 제목과 이름이 올 수도 있는 제목."""
        return bool(h) and (self.is_name_header(h) or self.is_weak_name_header(h))

    def _known_header(self, h: str) -> bool:
        """사전에 뜻이 정해진 열 제목. 끝 글자로 짐작하는 제목(`~자`, `~인`)은 넣지 않는다."""
        return bool(h) and (self.is_name_header(h) or h in _SIGN_HEADERS or _is_title(h) or h in WEAK_NAME_HEADERS
                            or h in FORMAT_HEADERS or h in ADDRESS_HEADERS or h in GRAY_HEADERS)

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
        # 전각 숫자와 여러 줄표를 한 모양으로 맞춘 글에서 찾는다. 글자 수가 같아 위치는 원문 그대로다
        text = fold(block.text)
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
        out += self._spaced_context(text)
        out += _birth_after_name(text, out)
        out += gray_text_candidates(text)
        return out

    # ---------------------------------------------------------------- 열 제목

    def _column(self, text: str, header: str, found: list[Candidate]) -> list[Candidate]:
        out = []
        # 결재란 머리글(직위, 결재 역할)과 `학생`, `~교사` 같은 제목은 이름 칸일 수 있지만 확실하지 않다.
        # 이름 모양일 때만 '검토'로 본다
        sign = not self.is_name_header(header) and self.is_weak_name_header(header)
        loose = sign and not (header in _SIGN_HEADERS or _is_title(header))  # 사람 칸인지조차 모르는 제목
        if self.is_name_header(header) or sign:
            spaced = spaced_names(text)
            for start, end in _parts(text, spaced):
                start = _skip_titles(text, start, end)
                if start is None:
                    continue  # 칸 조각이 직위뿐(`주무관`)
                inside = [(s, e) for s, e in spaced if start <= s and e <= end]
                if inside:  # 한 글자씩 띄어 쓴 이름(`홍 길 동`)
                    tier = TIER_REVIEW if sign else TIER_CERTAIN
                    out += [Candidate(s, e, NAME, tier, {BY_COLUMN}) for s, e in inside]
                    continue
                value = text[start:end]
                first = value.split()[0]
                if len(value.split()) > 1 and looks_like_name(first):  # `김민수 교수` → 이름만
                    end = start + len(first)
                    value = first
                if not looks_like_name(value) and _is_sentence(value):
                    if loose:
                        continue
                    # 이름 칸에 든 문장(`현직 교사 중 선발 예정`, `행정지원과 주무관 박서연`):
                    # 통째로 잡지 않고 그 안의 이름 모양 낱말만(2026-11-09)
                    for m in _WORD.finditer(text, start, end):
                        word = strip_josa(m.group())
                        if _role_name_ok(word):
                            out.append(Candidate(m.start(), m.start() + len(word), NAME, TIER_REVIEW, {BY_COLUMN}))
                    continue
                if sign and not looks_like_name(value):
                    continue
                if loose and not _weak_name_ok(value):
                    continue  # `수혜자 | 전교생`, `보건교사 | 미정`
                tier = TIER_CERTAIN if looks_like_name(value) and not sign else TIER_REVIEW
                out.append(Candidate(start, end, NAME, tier, {BY_COLUMN}))
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
                first = next(((s, e) for s, e in spaced_names(text, rs, re_) if s == rs), None)
                if first:  # `이름 : 김 민 수`
                    end = first[1]
                    out.append(Candidate(rs, end, NAME, TIER_CERTAIN, {BY_LABEL}))
                else:
                    tok = re.match(r"[^\s,()\[\]]+", text[rs:re_])
                    if not tok:
                        continue
                    value = strip_josa(tok.group())
                    if len(value) < 2 or value in NOT_NAMES or _is_title(value):
                        continue  # `참석자: 전 교직원`의 '전', `참석자: 공무원`, `참석자: 사무관`: 한 글자, 사전의 낱말, 직위
                    tier = TIER_CERTAIN if looks_like_name(value) else TIER_REVIEW
                    end = rs + len(value)
                    out.append(Candidate(rs, end, NAME, tier, {BY_LABEL}))
                    if tier != TIER_CERTAIN:
                        continue
                # `참석자: 김민수, 이도윤, 박서연`: 이어지는 이름 모양 낱말도 함께
                out += [Candidate(s, e, NAME, TIER_REVIEW, {BY_LABEL}) for s, e in _names_after(text, end, re_, 2)]
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
        spans: list[tuple[int, int]] = []
        for m in self.role_after_re.finditer(text):
            name = m.group(1)
            if _weak_name_ok(name) if m.group(2) in WEAK_ROLE_AFTER else _role_name_ok(name):
                spans.append(m.span(1))
                spans += _names_before(text, m.start(1))  # `김민수, 이도윤, 박서연 주무관`
        for m in self.role_after_compound_re.finditer(text):
            if _role_name_ok(m.group(1)):
                spans.append(m.span(1))
                spans += _names_before(text, m.start(1))
        for m in self.role_before_re.finditer(text):
            name = strip_josa(m.group(2))
            end = m.start(2) + len(name)
            if _role_name_ok(name):
                spans.append((m.start(2), end))
                spans += _names_after(text, end, len(text), 3)  # `강사 김민수, 이도윤`
        for m in self.weak_before_re.finditer(text):
            if _weak_name_ok(m.group(1)) and _STOP.match(text, m.end(1)):
                spans.append(m.span(1))
                spans += _names_after(text, m.end(1), len(text), 3)
        for m in self.short_after_re.finditer(text):
            if _short_name_ok(m.group(1)):
                spans.append(m.span(1))
        for m in self.short_before_re.finditer(text):
            if _short_name_ok(m.group(1)) and _STOP.match(text, m.end(1)):
                spans.append(m.span(1))
        for m in self.sign_mark_re.finditer(text):  # `보호자 이도윤 (인)`
            name = m.group(1)
            role_word = name in self.name_headers or name in WEAK_NAME_HEADERS or _is_title(name)
            if looks_like_name(name) and not role_word:
                spans.append(m.span(1))
        for s, e in spaced_names(text):  # `홍 길 동 (인)`, `교장  박 서 연`
            if re.match(rf"\s{{0,3}}{_SIGN_MARK}", text[e:]) or self.role_lead_re.search(text, max(0, s - 40), s):
                spans.append((s, e))
        for m in self.class_re.finditer(text):
            name = strip_josa(m.group(1))
            if _weak_name_ok(name):
                spans.append((m.start(1), m.start(1) + len(name)))
        for m in self.unit_re.finditer(text):
            if _weak_name_ok(m.group(1)) and _STOP.match(text, m.end(1)):
                spans.append(m.span(1))
        return [Candidate(s, e, NAME, TIER_REVIEW, {BY_ROLE}) for s, e in dict.fromkeys(spans)]

    def _spaced_context(self, text: str) -> list[Candidate]:
        """한 글자씩 띄어 쓴 이름(`허 준 주무관`, `담임 김  민  수`, `참석자: 김민수, 허 준, 이도윤`)을 붙여 쓴 이름과
        같은 자리 규칙으로 본다: 띄어 쓴 이름 모양을 붙인 글에서 레이블과 역할어 규칙을 한 번 더 돌리고,
        찾은 이름의 위치를 원래 글로 되돌린다. 빈칸이 몇 칸이든 같다. 새 추정은 없다(자리 규칙은 그대로다)."""
        # 낱자 낱말이 이어진 글(`이 두 분 선생님께`, `이 때 교장은`)은 이름 모양이어도 붙이지 않는다
        spans = [(s, e) for s, e in spaced_names(text)
                 if not all(ch in LONE_WORDS for ch in re.sub(r"\s", "", text[s:e])[1:])]
        if not spans:
            return []
        joined, at, pos = [], [], 0  # at[i]: 붙인 글의 i번째 글자가 원래 글에서 있던 자리
        for s, e in spans:
            keep = [*range(pos, s), *(i for i in range(s, e) if text[i] not in " \t")]
            joined += [text[i] for i in keep]
            at += keep
            pos = e
        joined += text[pos:]
        at += range(pos, len(text))
        compact = "".join(joined)
        return [Candidate(at[c.start], at[c.end - 1] + 1, NAME, c.tier, set(c.detected_by))
                for c in self._labels(compact, []) + self._role_context(compact) if c.category == NAME]

    # ---------------------------------------------------------------- 작성자

    @staticmethod
    def _author(text: str) -> list[Candidate]:
        span = _trimmed(text, 0, len(text))
        if not span:
            return []
        value = text[span[0]:span[1]]
        if looks_like_name(value):
            return [Candidate(*span, NAME, TIER_CERTAIN, {BY_METADATA})]
        if re.search(r"[가-힣]", value) and not value.endswith(_ORG_TAIL):
            return [Candidate(*span, NAME, TIER_REVIEW, {BY_METADATA})]
        return []  # 프로그램 이름(영문)이나 부서 이름


_TITLES = frozenset(TITLE_WORDS) | frozenset(SIGN_ROLES) | frozenset(ROLE_AFTER) | frozenset(ROLE_BEFORE)
_SIGN_HEADERS = frozenset(SIGN_ROLES) | frozenset(TITLE_WORDS)
_LEADING_TITLE = re.compile(rf"(?:{_alt(SIGN_ROLES)}|{_COMPOUND_TITLE}|{_SIGN_DATE})(?:\s+|$)")


def _is_title(word: str) -> bool:
    """직위나 결재 역할, 또는 직위로 끝나는 합성어(`행정연수담당사무관`, `교무부장`)."""
    return word in _TITLES or any(word.endswith(t) and len(word) > len(t) for t in TITLE_WORDS)


def _role_name_ok(name: str) -> bool:
    # 조사를 떼면 이름이 아닌 말(`위원장은 연수원장이`, `위원은 부원장이`)도 뺀다(2026-11-10).
    # 이름 끝 '은', '이'는 이름일 수도 있어(김하은) 뗀 말이 사전에 있을 때만 뺀다
    bases = {name, strip_josa(name), name[:-1] if name[-1] in "은이" else name}
    return (len(name) >= 3 and looks_like_name(name) and name[-1] not in _JOSA_END
            and not any(b in NOT_NAMES or _is_title(b) for b in bases))


def _weak_name_ok(name: str) -> bool:
    """근거가 약한 자리(`학생`, `학부모` 옆, 학년 반 뒤)의 이름: 세 글자(두 글자 성씨는 네 글자까지)이고
    끝 글자가 낱말 끝에 흔한 글자(연구회의 회, 방송부의 부)가 아닐 때만."""
    longest = 4 if name[:2] in COMPOUND_SURNAMES else 3
    return 3 <= len(name) <= longest and _role_name_ok(name) and name[-1] not in NOUN_ENDINGS


def _list_name_ok(name: str) -> bool:
    """이름에 쉼표로 이어진 낱말(`담당 김민수, 연구부`의 연구부는 아니다)."""
    return _role_name_ok(name) and name[-1] not in NOUN_ENDINGS


def _short_name_ok(name: str) -> bool:
    """사람 직위 옆의 두 글자 이름(`허준 주무관`)."""
    return (looks_like_name(name) and not _is_title(name) and name[-1] not in _JOSA_END
            and name not in SHORT_NOT_NAMES)


_BIRTH_YEARS_AGO = 3  # 이름으로 잡히지 않은 낱말 뒤 괄호 날짜는 이만큼 지난 해일 때만 생년월일 후보로 본다


def _birth_after_name(text: str, found: list[Candidate]) -> list[Candidate]:
    """이름 바로 뒤 괄호에 날짜만 있으면 생년월일 후보다(`김민수(1990. 1. 1.)`, `김민수(900101)`).
    '생'이 붙지 않은 날짜는 시행일, 회의일과 가를 수 없어, 앞이 이름일 때만 그리고 검토로만 올린다.
    - 앞 낱말이 이 글에서 이미 이름으로 잡혔으면 올해까지의 날짜는 모두 본다.
    - 이름 모양일 뿐이면(`조례안(2026. 3. 1.)`, `정수기(260301)`도 이름 모양이다) 날짜가 몇 해 전일 때만 본다.
      낱말 뒤 괄호 날짜는 대개 그 문서나 행사의 날짜라 올해 언저리이고, 생년월일은 유치원생도 세 해 전이다.
      `임용일(2020. 3. 1.)`처럼 '일'로 끝나는 낱말은 날짜 이름이 많아 뺀다."""
    out = []
    this_year = date.today().year
    for m in BIRTH_PAREN_RE.finditer(text):
        year = date_year(m.group(1), this_year)
        if year is None or year > this_year or any(c.start < m.end(1) and m.start(1) < c.end for c in found):
            continue
        end = m.start() - (text[m.start() - 1: m.start()] == " ")
        if not any(c.category == NAME and c.end == end for c in found):
            word = re.search(r"[가-힣]*$", text[max(0, end - 6): end]).group()
            shaped = ((_weak_name_ok(word) and word[-1] != "일")
                      or any(e == end for _, e in spaced_names(text, max(0, end - 12), end)))
            if not shaped or year > this_year - _BIRTH_YEARS_AGO:
                continue
        out.append(Candidate(m.start(1), m.end(1), BIRTH, TIER_REVIEW, {BY_AFTER_NAME}))
    return out


def spaced_names(text: str, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """한 글자씩 띄어 쓴 이름의 범위(`홍 길 동`, `김  민  수`). 이름이 올 자리에서만 부른다.
    간격이 넓은 곳에서 나누고(`원 장  박 서 연`), 앞뒤에 붙은 직위는 뺀다(`원 장 박 서 연`)."""
    out = []
    for run in _SPACED_RUN.finditer(text, start, len(text) if end is None else end):
        at = [run.start() + m.start() for m in _WORD.finditer(run.group())]
        gaps = [b - a - 1 for a, b in zip(at, at[1:])]
        groups, group = [], [at[0]]
        for pos, gap in zip(at[1:], gaps):
            if gap > min(gaps):
                groups.append(group)
                group = [pos]
            else:
                group.append(pos)
        groups.append(group)
        for g in groups:
            while len(g) > 2 and text[g[-1]] in SPACED_NAME_TAILS:  # `김 민 수 외 2명`, `허 준 등`
                g = g[:-1]
            word = "".join(text[p] for p in g)
            cut = next(((i, j) for i in range(len(word)) for j in range(len(word), i + 1, -1)
                        if looks_like_name(word[i:j]) and not _is_title(word[i:j])
                        and (i == 0 or _is_title(word[:i])) and (j == len(word) or _is_title(word[j:]))), None)
            if cut:
                out.append((g[cut[0]], g[cut[1] - 1] + 1))
    return out


def _names_after(text: str, pos: int, end: int, min_len: int) -> list[tuple[int, int]]:
    """이름 뒤에 쉼표, '및', '와/과'로 이어진 이름 모양 낱말의 범위. 말이 끊기지 않는 낱말에서 멈춘다
    (`강사 김민수, 강의실 201호`의 강의실은 아니다)."""
    out = []
    while True:
        m = _LIST_NEXT.match(text, pos, end)
        if not m:
            return out
        name = m.group(1)
        ok = _list_name_ok(name) if len(name) >= 3 else (min_len <= 2 and _short_name_ok(name))
        if not ok or not _STOP.match(text, m.end(1), end):
            return out
        out.append(m.span(1))
        pos = m.end(1)


def _names_before(text: str, pos: int) -> list[tuple[int, int]]:
    """역할어 앞 이름의 앞쪽으로 이어진 이름들(`김민수, 이도윤, 박서연 주무관`의 김민수, 이도윤)."""
    out = []
    while True:
        m = _LIST_PREV.search(text, max(0, pos - 12), pos)
        if not m or not _list_name_ok(m.group(1)):
            return out
        out.append(m.span(1))
        pos = m.start(1)


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


def _parts(text: str, keep=()):
    """이름 칸 하나에 여러 이름이 쉼표 등으로 들어 있으면 나눈다. keep(띄어 쓴 이름의 범위) 안에서는 나누지 않는다."""
    pos = 0
    splits = [m for m in _NAME_SPLIT.finditer(text) if not any(s < m.start() and m.end() < e for s, e in keep)]
    for m in splits + [None]:
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
        c = Candidate(c.start, c.end, c.category, c.tier, set(c.detected_by), c.exclude_hint, c.validation)
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
                              hit.validation or c.validation)
            else:
                c = max(hit, c, key=lambda x: (TIER_RANK[x.tier], x.end - x.start))
    return sorted(result, key=lambda c: c.start)
