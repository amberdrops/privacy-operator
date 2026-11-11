"""조합 승격(기획서 4장). 등급은 올리기만 한다. 내리는 것은 사람만 한다.

- 같은 문장(또는 칸)에 참고 항목이 둘 이상 → 모두 '검토', 하나의 묶음(group_id)
- 같은 표 행에 참고 항목이 둘 이상 → 모두 '검토'
- 참고 항목이 이름과 같은 문장이나 행에 있음 → '검토'

이름은 '확실' 등급이거나 사람이 '확정'한 것만 센다. 판정 뒤에 다시 불러도 된다.
"""

from __future__ import annotations

from collections import defaultdict

from privacy_operator.detect.dictionaries import GRAY_CATEGORIES, INDIRECT, NAME, normalize_value
from privacy_operator.detect.finding import (
    ACTION_ALIAS,
    ACTION_KEEP,
    ESC_GRAY_PAIR_ROW,
    ESC_GRAY_PAIR_SENTENCE,
    ESC_NAME_SAME_ROW,
    ESC_NAME_SAME_SENTENCE,
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    STATUS_REFERENCE,
    TIER_CERTAIN,
    TIER_REFERENCE,
    TIER_REVIEW,
    Finding,
)


def _live(f: Finding) -> bool:
    return f.exclude_hint is None and f.review_status != STATUS_EXCLUDED


def _is_gray(f: Finding) -> bool:
    """AI가 찾은 간접식별 후보도 회색 영역처럼 센다(기획서 4장 'AI 탐지와의 관계')."""
    return (f.category in GRAY_CATEGORIES or f.category == INDIRECT) and _live(f)


def _pairs(gray: list[Finding]) -> bool:
    """서로 다른 항목이 둘 이상인가. 회색 영역 값을 품은 간접식별 후보는 그 값과 같은 글자이므로 따로 세지 않는다.
    (세면 AI 후보 하나가 안의 값을 '검토'로 올린다. AI 판단만으로는 올리지 않는다.)
    같은 값이 두 번 나온 것도 하나로 센다: `허준 주무관, 김솔 주무관`의 주무관 둘은 조합이 아니다(2026-11-10)."""
    distinct = [f for f in gray if not (f.category == INDIRECT and any(
        o is not f and o.start < f.end and f.start < o.end and o.block_id == f.block_id for o in gray))]
    return len({normalize_value(f.raw_value) for f in distinct}) >= 2


def _is_name(f: Finding) -> bool:
    return f.category == NAME and _live(f) and (f.tier == TIER_CERTAIN or f.review_status == STATUS_CONFIRMED)


def _raise(f: Finding, reason: str) -> None:
    if f.tier_set_by_human:
        return
    if f.tier == TIER_REFERENCE:
        f.tier = TIER_REVIEW
        if f.review_status == STATUS_REFERENCE:
            f.review_status = STATUS_PENDING
        if f.action == ACTION_KEEP:
            f.action = ACTION_ALIAS
    if f.escalated_by is None:
        f.escalated_by = reason


def escalate(findings: list[Finding]) -> None:
    sentences: dict[tuple, list[Finding]] = defaultdict(list)
    rows: dict[tuple, list[Finding]] = defaultdict(list)
    for f in findings:
        sentences[(f.file_id, f.block_id, f.sentence)].append(f)
        loc = f.location
        if loc.table_index is not None and loc.row is not None:
            rows[(f.file_id, loc.table_index, loc.row)].append(f)

    group_no = sum(1 for f in findings if f.group_id) + 1
    for unit in sentences.values():
        gray = [f for f in unit if _is_gray(f)]
        if any(_is_name(f) for f in unit):
            for f in gray:
                _raise(f, ESC_NAME_SAME_SENTENCE)
        if _pairs(gray):
            gid = next((f.group_id for f in gray if f.group_id), None) or f"G{group_no:04d}"
            group_no += 1
            for f in gray:
                _raise(f, ESC_GRAY_PAIR_SENTENCE)
                f.group_id = f.group_id or gid

    for unit in rows.values():
        gray = [f for f in unit if _is_gray(f)]
        if any(_is_name(f) for f in unit):
            for f in gray:
                _raise(f, ESC_NAME_SAME_ROW)
        if _pairs(gray):
            for f in gray:
                _raise(f, ESC_GRAY_PAIR_ROW)
