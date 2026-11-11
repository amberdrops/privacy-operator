"""원본 형식 저장에 쓰는 바꿀 목록(8단계). 판정과 가명처리(pseudonymize)가 끝난 탐지에서 만든다.

원본 파일은 블록 위치를 그대로 알 수 없으므로 값으로 찾아 바꾼다.
- 이름, 연락처, 번호, 주소: 어디에 있든 바꾼다(차단 목록과 같은 기준).
- 회색 영역 묶음: 묶음 전체 글자를 바꾼다.
- 회색 영역 낱개(소속, 직위 …): 홀로 쓰였거나 조사가 붙은 낱말만 바꾼다. '교사'를 바꾸면서 '신규 교사라서'까지 바꾸지 않게.

한 번에 모든 규칙의 자리를 찾아 겹치지 않게 고른 뒤 바꾼다. 바꿔 넣은 가명코드 안을 다시 찾지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from privacy_operator.alias.pseudonymize import PseudoDocument
from privacy_operator.detect.dictionaries import GRAY_CATEGORIES
from privacy_operator.detect.finding import ACTION_KEEP, STATUS_CONFIRMED, Finding
from privacy_operator.detect.patterns import NUMERIC_CATEGORIES, ValueIndex, fold, probe, value_pattern

_JOSA = "에서|에게|이나|이랑|으로|은|는|이|가|을|를|과|와|의|에|로|도|만|나|랑"


def gray_word_pattern(value: str) -> re.Pattern:
    """홀로 쓰였거나 조사가 붙은 낱말로만 찾는다. 다른 값 정규식처럼 fold()한 글에서 쓴다."""
    return re.compile(rf"(?<![가-힣]){re.escape(fold(value.strip()))}(?=$|[^가-힣]|(?:{_JOSA})(?![가-힣]))")


_WRAP = r"(?:[ \t]*\n\s*)?"  # 글자 사이: 줄바꿈이 낀 경우만 허용(그냥 띄어 쓴 것은 다른 낱말일 수 있다)


def line_wrap_pattern(value: str) -> re.Pattern:
    """PDF 쪽 글처럼 값 중간에서 줄이 바뀔 수 있는 곳에서 쓰는 정규식.
    읽기 엔진(kordoc)은 줄바꿈을 이어 붙여 읽는데 PyMuPDF 쪽 글에는 줄바꿈이 남아,
    줄에 걸친 주소를 지우지 못하고 사본이 빠졌다(2026-11-10)."""
    out = []
    for part in re.split(r"(\s+)", fold(value.strip())):
        if not part:
            continue
        out.append(r"\s+" if part.isspace() else _WRAP.join(re.escape(c) for c in part))
    return re.compile("".join(out))


@dataclass
class Rule:
    pattern: re.Pattern
    replacement: str
    finding_ids: list[str] = field(default_factory=list)
    secret: bool = True  # 결과물에 남으면 안 되는 값(차단 목록)
    length: int = 0
    wrap_value: str | None = None  # 줄바꿈이 낀 값도 찾을 값(PDF 쪽 글). 없으면 pattern을 그대로 쓴다
    probe: tuple[bool, str] = (False, "")  # 정규식을 돌리기 전에 먼저 볼 열쇠(Haystack)
    _wrap: re.Pattern | None = field(default=None, repr=False, compare=False)

    @property
    def wrapped(self) -> re.Pattern:
        """줄바꿈이 낀 값도 찾는 정규식. PDF에서만 쓰므로 처음 쓸 때 만든다."""
        if self.wrap_value is None:
            return self.pattern
        if self._wrap is None:
            self._wrap = line_wrap_pattern(self.wrap_value)
        return self._wrap

    def __repr__(self) -> str:
        return f"Rule(replacement={self.replacement!r}, ids={len(self.finding_ids)}, secret={self.secret})"


class ReplacementPlan:
    def __init__(self, findings: list[Finding], docs: list[PseudoDocument] | None = None):
        bundles: dict[str, str] = {}
        for d in docs or []:
            bundles.update(d.bundles)
        rules: dict[tuple, Rule] = {}
        grouped: dict[str, list[Finding]] = {}
        for f in findings:
            if f.group_id in bundles and f.replacement:
                grouped.setdefault(f.group_id, []).append(f)
        for gid, text in bundles.items():
            members = grouped.get(gid)
            if members and text.strip():
                rules[("bundle", text)] = Rule(re.compile(re.escape(fold(text))), members[0].replacement,
                                               [f.finding_id for f in members], True, len(text),
                                               text, probe("", text))
        for f in findings:
            if f.review_status != STATUS_CONFIRMED or f.action == ACTION_KEEP or not f.replacement:
                continue
            if f.group_id in bundles or not f.raw_value.strip():
                continue
            gray = f.category in GRAY_CATEGORIES
            key = ("gray" if gray else f.category, f.raw_value.strip())
            if key in rules:
                rules[key].finding_ids.append(f.finding_id)
                continue
            pattern = gray_word_pattern(f.raw_value) if gray else value_pattern(f.category, f.raw_value)
            # 번호는 value_pattern이 이미 숫자 사이 공백(줄바꿈 포함)을 허용한다
            wrap = None if gray or f.category in NUMERIC_CATEGORIES else f.raw_value
            rules[key] = Rule(pattern, f.replacement, [f.finding_id], not gray, len(f.raw_value.strip()), wrap,
                              probe("" if gray else f.category, f.raw_value))
        self.rules = sorted(rules.values(), key=lambda r: -r.length)
        self._index: ValueIndex | None = None

    def __repr__(self) -> str:
        return f"ReplacementPlan(rules={len(self.rules)})"

    def __bool__(self) -> bool:
        return bool(self.rules)

    def candidates(self, text: str) -> list[int]:
        """이 글에서 찾을 수도 있는 규칙의 번호(긴 값 먼저). 한글 파일 속 XML에는 바꿀 것이 없는 짧은 값
        ("0", "true")이 수만 개이고 큰 명단은 규칙이 수천 개라, 규칙마다 정규식을 돌리지 않고 먼저 거른다.
        여기서 빠진 규칙은 그 글에서 찾을 수 없다(ValueIndex)."""
        if self._index is None:
            self._index = ValueIndex([r.probe for r in self.rules])
        return self._index.candidates(text)

    def pick(self, text: str, wrapped: bool = False) -> list[tuple[int, int, int]]:
        """바꿀 자리 (시작, 끝, 규칙 번호)를 앞에서부터. 긴 값이 먼저, 겹치면 먼저 고른 것만.
        wrapped=True면 값 중간에 줄이 바뀐 것도 찾는다(PDF 쪽 글)."""
        text = fold(text)  # 규칙의 정규식은 모양을 맞춘 글에서 쓴다. 글자 수가 같아 위치는 원문 그대로다
        found = [(m.start(), m.end(), i) for i in self.candidates(text)
                 for m in (self.rules[i].wrapped if wrapped else self.rules[i].pattern).finditer(text)
                 if m.end() > m.start()]
        found.sort(key=lambda x: (x[2], x[0]))  # 규칙 순서(긴 값 먼저)
        taken: list[tuple[int, int, int]] = []
        for s, e, i in found:
            if all(e <= ts or te <= s for ts, te, _ in taken):
                taken.append((s, e, i))
        return sorted(taken)

    def apply(self, text: str) -> tuple[str, int]:
        """바꾼 글과 바꾼 수."""
        taken = self.pick(text) if text and self.rules else []
        if not taken:
            return text, 0
        out, pos = [], 0
        for s, e, i in taken:
            out.append(text[pos:s])
            out.append(self.rules[i].replacement)
            pos = e
        out.append(text[pos:])
        return "".join(out), len(taken)

    def replace(self, text: str) -> str:
        return self.apply(text)[0]

    def leftovers(self, text: str, wrapped: bool = False, limit: int | None = None) -> list[str]:
        """남아 있는 비밀 값의 finding_id(규칙마다 하나). 값은 돌려주지 않는다.
        wrapped=True면 값 중간에 줄이 바뀐 것도 찾는다(PDF 쪽 글).
        limit을 주면 그만큼 찾고 멈춘다(순서 없음). 남았는지만 알면 되는 재검사에서 쓴다: 값이 통째로 남은 큰
        명단을 끝까지 훑으면 몇 분이 걸린다."""
        text = fold(text)
        if self._index is None:
            self.candidates("")
        out = []
        for i in self._index.scan(text) if limit else self.candidates(text):
            r = self.rules[i]
            if r.secret and (r.wrapped if wrapped else r.pattern).search(text):
                out.append(r.finding_ids[0])
                if limit and len(out) >= limit:
                    break
        return out
