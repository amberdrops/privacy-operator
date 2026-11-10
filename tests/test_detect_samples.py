"""샘플에 심어 둔 값을 규칙 탐지가 모두 찾는지(재현율 100%), 잡지 말아야 할 것을 잡지 않는지.

실패 메시지에는 answer_id만 쓴다. 원문 값은 출력하지 않는다.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import pytest
from answer_utils import SAMPLE_FILES, all_planted_values, block_at, load_answers, occurrences

from privacy_operator.detect import detect
from privacy_operator.detect.finding import (
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    TIER_REFERENCE,
    TIER_REVIEW,
)

INDIRECT = "간접식별"


@pytest.fixture(scope="session")
def findings(results):
    return {name: detect([results[name]]) for name in SAMPLE_FILES}


def _tier_ok(f, expected: str) -> bool:
    # 정답의 tier는 처음 등급이다. 참고 항목은 조합 승격으로 검토가 될 수 있다
    return f.tier == expected or (expected == TIER_REFERENCE and f.tier == TIER_REVIEW and f.escalated_by)


def _covering(block_findings, span, answer):
    """span을 덮는 탐지(또는 회색 영역 묶음)를 돌려준다. 제외 후보는 세지 않는다."""
    s, e = span
    live = [f for f in block_findings if f.exclude_hint is None]
    if answer["category"] == INDIRECT:
        groups = defaultdict(list)
        for f in live:
            if f.group_id:
                groups[f.group_id].append(f)
        return [g for g in groups.values() if min(f.start for f in g) <= s and max(f.end for f in g) >= e]
    return [[f] for f in live if f.start <= s and f.end >= e and f.category == answer["category"]]


def _check(result, found, answer):
    """('ok' | 'missing' | 'tier')"""
    by_block = defaultdict(list)
    for f in found:
        by_block[f.block_id].append(f)
    covered = False
    for block in result.blocks:
        if not block_at(block, answer["location"]):
            continue
        for span in occurrences(block, answer["value"]):
            for group in _covering(by_block[block.block_id], span, answer):
                covered = True
                if all(_tier_ok(f, answer["tier"]) for f in group):
                    return "ok"
    return "tier" if covered else "missing"


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_recall_is_complete(results, findings, name):
    checks = {a["answer_id"]: _check(results[name], findings[name], a) for a in load_answers(name)["answers"]}
    missing = [k for k, v in checks.items() if v == "missing"]
    wrong_tier = [k for k, v in checks.items() if v == "tier"]
    assert not missing, f"놓친 정답: {missing}"
    assert not wrong_tier, f"등급이 다른 정답: {wrong_tier}"


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_must_not_detect_is_left_or_marked(results, findings, name):
    """잡지 말아야 할 값은 탐지되지 않거나 '제외 후보'로 표시돼야 한다."""
    wrong = []
    for item in load_answers(name)["must_not_detect"]:
        for block in results[name].blocks:
            if not block_at(block, item["location"]):
                continue
            for s, e in occurrences(block, item["value"]):
                if any(f.block_id == block.block_id and f.start < e and s < f.end and f.exclude_hint is None
                       for f in findings[name]):
                    wrong.append(item["answer_id"])
    assert not wrong, f"제외 후보 표시 없이 잡힌 값: {sorted(set(wrong))}"


def test_detection_never_confirms_or_excludes(findings):
    """절대 규칙 4: 탐지는 '대기'나 '참고'로만 시작한다."""
    statuses = {f.review_status for fs in findings.values() for f in fs}
    assert not statuses & {STATUS_CONFIRMED, STATUS_EXCLUDED}


def test_hidden_sheet_findings_are_marked(findings):
    hidden = [f for f in findings["만족도조사_샘플.xlsx"] if f.hidden]
    assert hidden and all(f.location.sheet == "내부메모" for f in hidden)


def test_logs_and_reprs_have_no_planted_values(results, caplog):
    """절대 규칙 3: 로그와 출력에는 finding_id와 유형만."""
    values = all_planted_values()
    with caplog.at_level(logging.DEBUG, logger="privacy_operator"):
        found = detect(list(results.values()))
    shown = caplog.text + "\n".join(repr(f) + str(f) for f in found)
    leaked = sorted({f.finding_id for f in found if f.raw_value in shown})
    assert caplog.records, "로그가 하나도 없으면 이 검사가 헛돕니다"
    assert not leaked, f"원문 값이 로그나 repr에 나온 탐지: {leaked}"
    assert sum(v in shown for v in values) == 0, "정답 값이 로그나 repr에 나왔습니다"
