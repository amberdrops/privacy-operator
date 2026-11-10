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
from privacy_operator.detect.patterns import NUMERIC_CATEGORIES, value_pattern

_JOSA = "에서|에게|이나|이랑|으로|은|는|이|가|을|를|과|와|의|에|로|도|만|나|랑"


def gray_word_pattern(value: str) -> re.Pattern:
    """홀로 쓰였거나 조사가 붙은 낱말로만 찾는다."""
    return re.compile(rf"(?<![가-힣]){re.escape(value.strip())}(?=$|[^가-힣]|(?:{_JOSA})(?![가-힣]))")


_WRAP = r"(?:[ \t]*\n\s*)?"  # 글자 사이: 줄바꿈이 낀 경우만 허용(그냥 띄어 쓴 것은 다른 낱말일 수 있다)


def line_wrap_pattern(value: str) -> re.Pattern:
    """PDF 쪽 글처럼 값 중간에서 줄이 바뀔 수 있는 곳에서 쓰는 정규식.
    읽기 엔진(kordoc)은 줄바꿈을 이어 붙여 읽는데 PyMuPDF 쪽 글에는 줄바꿈이 남아,
    줄에 걸친 주소를 지우지 못하고 사본이 빠졌다(2026-11-10)."""
    out = []
    for part in re.split(r"(\s+)", value.strip()):
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
    wrap: re.Pattern | None = None  # 줄바꿈이 낀 값도 찾는 정규식(PDF 쪽 글). 없으면 pattern

    @property
    def wrapped(self) -> re.Pattern:
        return self.wrap or self.pattern

    def __repr__(self) -> str:
        return f"Rule(replacement={self.replacement!r}, ids={len(self.finding_ids)}, secret={self.secret})"


class ReplacementPlan:
    def __init__(self, findings: list[Finding], docs: list[PseudoDocument] | None = None):
        bundles: dict[str, str] = {}
        for d in docs or []:
            bundles.update(d.bundles)
        rules: dict[tuple, Rule] = {}
        for gid, text in bundles.items():
            members = [f for f in findings if f.group_id == gid and f.replacement]
            if members and text.strip():
                rules[("bundle", text)] = Rule(re.compile(re.escape(text)), members[0].replacement,
                                               [f.finding_id for f in members], True, len(text),
                                               line_wrap_pattern(text))
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
            wrap = None if gray or f.category in NUMERIC_CATEGORIES else line_wrap_pattern(f.raw_value)
            rules[key] = Rule(pattern, f.replacement, [f.finding_id], not gray, len(f.raw_value.strip()), wrap)
        self.rules = sorted(rules.values(), key=lambda r: -r.length)

    def __repr__(self) -> str:
        return f"ReplacementPlan(rules={len(self.rules)})"

    def __bool__(self) -> bool:
        return bool(self.rules)

    def _any(self) -> re.Pattern | None:
        """모든 규칙을 합친 검사 하나. 한글 파일 속 XML에는 바꿀 것이 없는 짧은 값("0", "true")이 수만 개라,
        먼저 이것으로 한 번만 훑고 걸린 글만 규칙마다 본다(작전 개시가 2초쯤 빨라진다). 합칠 수 없으면 None."""
        if not hasattr(self, "_any_re"):
            plain = re.compile("").flags
            if any(r.pattern.flags != plain or "\\1" in r.pattern.pattern for r in self.rules):
                self._any_re = None  # 옵션이 다른 규칙은 합치면 놓칠 수 있다. 하나씩 본다
                return None
            try:
                self._any_re = re.compile("|".join(f"(?:{r.pattern.pattern})" for r in self.rules))
            except re.error:
                self._any_re = None
        return self._any_re

    def apply(self, text: str) -> tuple[str, int]:
        """바꾼 글과 바꾼 수. 긴 값이 먼저, 겹치면 먼저 고른 것만."""
        if not text or not self.rules:
            return text, 0
        quick = self._any()
        if quick is not None and not quick.search(text):
            return text, 0
        found = [(m.start(), m.end(), i) for i, r in enumerate(self.rules) for m in r.pattern.finditer(text)
                 if m.end() > m.start()]
        found.sort(key=lambda x: (x[2], x[0]))  # 규칙 순서(긴 값 먼저)
        taken: list[tuple[int, int, int]] = []
        for s, e, i in found:
            if all(e <= ts or te <= s for ts, te, _ in taken):
                taken.append((s, e, i))
        if not taken:
            return text, 0
        out, pos = [], 0
        for s, e, i in sorted(taken):
            out.append(text[pos:s])
            out.append(self.rules[i].replacement)
            pos = e
        out.append(text[pos:])
        return "".join(out), len(taken)

    def replace(self, text: str) -> str:
        return self.apply(text)[0]

    def leftovers(self, text: str, wrapped: bool = False) -> list[str]:
        """남아 있는 비밀 값의 finding_id(규칙마다 하나). 값은 돌려주지 않는다.
        wrapped=True면 값 중간에 줄이 바뀐 것도 찾는다(PDF 쪽 글)."""
        return [r.finding_ids[0] for r in self.rules
                if r.secret and text and (r.wrapped if wrapped else r.pattern).search(text)]
