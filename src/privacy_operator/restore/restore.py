"""복원: AI 결과물의 가명코드를 대응표의 원래 값으로 되돌린다.

- 정확한 가명코드(`[이름-01]`)는 되돌리고 뒤 조사를 고친다.
- 변형 가명코드(`강사-01`, `[강사 01]`, `강사 1`, `01번 강사` …)는 자동으로 바꾸지 않는다.
  '확인 필요' 목록(variants)에 올리고, 사람이 승인한 variant_id만 다음 restore 때 바꾼다.
- 미복원: 대응표에 없는 가명코드(unknown_codes), 결과에서 사라진 가명코드(missing_codes).

결과 text에는 원문 값이 들어 있다. repr과 로그에는 가명코드와 개수만 쓴다.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from privacy_operator.restore.josa import josa_after

log = logging.getLogger(__name__)

CODE_RE = re.compile(r"\[([가-힣A-Za-z]+)-(\d{2,3})\]")

_OPEN = r"\[［【(（〔"
_CLOSE = r"\]］】)）〕"
# 변형 1: 괄호는 있는데 모양이 다름  [강사 01]  [이름-1]  (강사-01)  ［강사－01］
_BRACKETED = r"[{o}]\s*({role})\s*[-–—_－\s]?\s*(\d{{1,3}})\s*[{c}]"
# 변형 2: 괄호 없음  강사-01  강사 1  강사01번
_BARE = (r"(?<![{o}가-힣\d])({role})\s?[-–—_－]?\s?(\d{{1,3}})(?!\d)(?:번(?!째))?"
         r"(?![{c}])(?=$|[\s.,!?;:'\"’”…\x00]|은|는|이|가|을|를|과|와|의|에|로|으로|도|만|님)")
# 변형 3: 번호가 앞  01번 강사  1번강사
_NUM_FIRST = r"(?<![\d{o}])(\d{{1,3}})\s?번\s?({role})(?![가-힣]*[{c}])"


@dataclass(frozen=True)
class Variant:
    variant_id: str
    start: int
    end: int
    text: str  # 변형된 가명코드 글자(역할과 번호뿐, 원문 값 아님)
    suggested_code: str
    kind: str  # bracketed, bare, number_first


@dataclass
class RestoreResult:
    text: str
    restored: Counter = field(default_factory=Counter)  # 가명코드 → 되돌린 횟수
    unknown_codes: Counter = field(default_factory=Counter)  # 대응표에 없는 가명코드
    missing_codes: list[str] = field(default_factory=list)  # 대응표에 있는데 결과에 없는 가명코드
    variants: list[Variant] = field(default_factory=list)  # 확인 필요(아직 바꾸지 않음)
    accepted: list[str] = field(default_factory=list)  # 이번에 바꾼 variant_id
    leftover_codes: Counter = field(default_factory=Counter)  # 복원본에 남은 가명코드(조각으로 나뉜 것 등)

    @property
    def complete(self) -> bool:
        return not (self.unknown_codes or self.variants or self.leftover_codes)

    def __repr__(self) -> str:
        return (f"RestoreResult(restored={sum(self.restored.values())}, unknown={dict(self.unknown_codes)}, "
                f"missing={self.missing_codes}, variants={len(self.variants)})")

    __str__ = __repr__


def _entries(mapping: dict) -> dict[str, dict]:
    return {e["alias_code"]: e for e in mapping.get("entries", [])}


def _numbering(codes) -> dict[str, dict[int, str]]:
    roles: dict[str, dict[int, str]] = {}
    for code in codes:
        m = CODE_RE.fullmatch(code)
        if m:
            roles.setdefault(m.group(1), {})[int(m.group(2))] = code
    return roles


def find_variants(text: str, codes) -> list[Variant]:
    roles = _numbering(codes)
    if not roles:
        return []
    exact = [(m.start(), m.end()) for m in CODE_RE.finditer(text)]
    role_alt = "|".join(re.escape(r) for r in sorted(roles, key=len, reverse=True))
    patterns = (
        ("bracketed", re.compile(_BRACKETED.format(o=_OPEN, c=_CLOSE, role=role_alt)), 1, 2),
        ("bare", re.compile(_BARE.format(o=_OPEN, c=_CLOSE, role=role_alt)), 1, 2),
        ("number_first", re.compile(_NUM_FIRST.format(o=_OPEN, c=_CLOSE, role=role_alt)), 2, 1),
    )
    found: list[tuple[int, int, str, str]] = []
    for kind, regex, role_g, num_g in patterns:
        for m in regex.finditer(text):
            s, e = m.start(), m.end()
            if any(s < xe and xs < e for xs, xe in exact):
                continue  # 정확한 가명코드의 일부
            if any(s < fe and fs < e for fs, fe, _, _ in found):
                continue
            code = roles[m.group(role_g)].get(int(m.group(num_g)))
            if code:
                found.append((s, e, code, kind))
    found.sort()
    return [Variant(f"V{i + 1:03d}", s, e, text[s:e], code, kind) for i, (s, e, code, kind) in enumerate(found)]


def restore(text: str, mapping: dict, accept: set[str] | frozenset[str] = frozenset()) -> RestoreResult:
    entries = _entries(mapping)
    result = RestoreResult(text="")
    edits: list[tuple[int, int, str]] = []  # (start, end, 바꿀 글자)

    def replace(start: int, end: int, code: str) -> None:
        entry = entries[code]
        value = entry["raw_value"]
        fix = josa_after(text, end, value, bool(entry.get("has_batchim")))
        if fix:
            edits.append((start, fix.end, value + fix.josa))
        else:
            edits.append((start, end, value))
        result.restored[code] += 1

    for m in CODE_RE.finditer(text):
        code = m.group(0)
        if code in entries:
            replace(m.start(), m.end(), code)
        else:
            result.unknown_codes[code] += 1

    variants = find_variants(text, entries)
    for v in variants:
        if v.variant_id in accept:
            replace(v.start, v.end, v.suggested_code)
            result.accepted.append(v.variant_id)
        else:
            result.variants.append(v)

    out, pos = [], 0
    for start, end, new in sorted(edits):
        out.append(text[pos:start])
        out.append(new)
        pos = end
    out.append(text[pos:])
    result.text = "".join(out)

    pending = {v.suggested_code for v in result.variants}
    result.missing_codes = sorted(c for c in entries if c not in result.restored and c not in pending)
    log.info("복원: 되돌림 %d건, 대응표에 없는 가명코드 %s, 사라진 가명코드 %d개, 확인 필요 %d건",
             sum(result.restored.values()), sorted(result.unknown_codes), len(result.missing_codes),
             len(result.variants))
    return result


SEP = "\x00"  # 조각 사이 구분. 가명코드와 변형 모양에 들어갈 수 없는 글자


def restore_many(texts: list[str], mapping: dict, accept=frozenset()) -> tuple[list[str], RestoreResult]:
    """여러 조각(XML 글 칸 등)을 한 문서처럼 복원한다. 변형 번호와 '사라진 가명코드'를 문서 전체로 센다."""
    joined = SEP.join(t.replace(SEP, "") for t in texts)
    result = restore(joined, mapping, accept)
    parts = result.text.split(SEP)
    if len(parts) != len(texts):  # 일어나지 않아야 하지만, 어긋나면 원래 글을 돌려준다
        raise ValueError("restore_many: 조각 수가 맞지 않습니다")
    return parts, result
