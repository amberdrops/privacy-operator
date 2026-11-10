"""규칙 탐지 진입점.

    findings = detect([read_result, ...])

여러 문서를 한 번에 주면 한 문서에서 '확실' 등급으로 찾은 이름을 모든 문서에서 다시 찾는다.
로그에는 finding_id, 유형, 등급, 근거만 쓴다.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from functools import cached_property

from privacy_operator.detect.dictionaries import (
    EXCEPTIONS,
    GRAY_CATEGORIES,
    NAME,
    NAME_HEADERS,
    NAME_LABELS,
    looks_like_name,
    normalize_header,
    normalize_value,
)
from privacy_operator.detect.escalate import escalate
from privacy_operator.detect.finding import (
    BY_CONFIRMED,
    BY_PROPAGATION,
    HINT_EXCEPTION,
    STATUS_PENDING,
    TIER_CERTAIN,
    TIER_REVIEW,
    Finding,
    default_action,
    default_status,
)
from privacy_operator.detect.patterns import Candidate, value_pattern
from privacy_operator.detect.rules import RuleSet, merge
from privacy_operator.reader.blocks import CELL, COMMENT, METADATA, Block, ReadResult

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+|\n+")


@dataclass
class DetectConfig:
    public_terms: frozenset[str] = frozenset()  # 공개 정보 사전: 탐지하지 않을 말. 기본은 비어 있다
    exceptions: frozenset[str] = EXCEPTIONS  # 예외 목록: '제외 후보'로 표시할 말
    extra_name_headers: frozenset[str] = frozenset()
    extra_name_labels: frozenset[str] = frozenset()
    propagate_names: bool = True

    @cached_property
    def rules(self) -> RuleSet:
        headers = frozenset(normalize_header(h) for h in NAME_HEADERS | self.extra_name_headers)
        return RuleSet(headers, NAME_LABELS | self.extra_name_labels)


def detect(results: list[ReadResult], config: DetectConfig | None = None) -> list[Finding]:
    cfg = config or DetectConfig()
    rules = cfg.rules
    public = {normalize_value(t) for t in cfg.public_terms}
    exceptions = {normalize_value(t) for t in cfg.exceptions}

    per_block: dict[tuple[int, str], list[Candidate]] = {}
    for di, res in enumerate(results):
        for block in res.blocks:
            per_block[(di, block.block_id)] = merge(rules.block_candidates(block))

    if cfg.propagate_names:
        _propagate(results, per_block, exceptions)

    findings: list[Finding] = []
    for di, res in enumerate(results):
        file_id = f"D{di + 1:02d}"
        rows = _row_index(res)
        n = 0
        for block in res.blocks:
            bounds = _sentences(block.text)
            for c in per_block[(di, block.block_id)]:
                value = block.text[c.start:c.end]
                key = normalize_value(value)
                if key in public:
                    continue
                if key in exceptions:
                    c.exclude_hint = HINT_EXCEPTION
                n += 1
                findings.append(Finding(
                    finding_id=f"{file_id}-{n:04d}", file_id=file_id, filename=res.source_name,
                    format=res.format, category=c.category, raw_value=value,
                    context=_context(block, c, rows), block_id=block.block_id, start=c.start, end=c.end,
                    location=block.location, sentence=_sentence_of(bounds, c.start),
                    detected_by=tuple(sorted(c.detected_by)), tier=c.tier,
                    review_status=default_status(c.tier), action=default_action(c.tier, c.category),
                    role_hint=c.role, hidden=block.hidden, exclude_hint=c.exclude_hint,
                    validation=c.validation,
                ))

    escalate(findings)
    for f in findings:
        log.debug("탐지 %s 유형=%s 등급=%s 근거=%s 승격=%s 제외후보=%s", f.finding_id, f.category, f.tier,
                  ",".join(f.detected_by), f.escalated_by, f.exclude_hint)
    log.info("탐지 %d건 %s", len(findings), dict(Counter((f.category, f.tier) for f in findings)))
    return findings


def rescan(results: list[ReadResult], findings: list[Finding], sources: list[Finding],
           word_start: bool = False) -> list[Finding]:
    """사람이 확정한 값(sources)을 모든 문서에서 다시 찾는다(7단계 '확정값 자동 재검색').

    아직 아무 탐지도 없는 자리만 새 탐지로 만든다. 새 탐지는 '검토' 등급, '대기' 상태다(자동 확정 금지).
    회색 영역 낱개 값은 다시 찾지 않는다(그 자체로는 식별 정보가 아니다).
    기본은 차단 목록과 같은 기준으로 더 긴 낱말 속 값까지 찾는다(2026-11-08). 낱말 속 자리를 건너뛰면 그 자리는
    송출 직전 차단 목록 검사에서 결국 걸려, 판정이 100%인데도 작전 개시가 멈췄다. 확정하는 순간 대기로 보여 준다.
    """
    taken: dict[tuple[str, str], list[tuple[int, int]]] = {}
    last: dict[str, int] = {}
    for f in findings:
        taken.setdefault((f.file_id, f.block_id), []).append((f.start, f.end))
        tail = f.finding_id.rsplit("-", 1)[1]
        if tail.isdigit():  # AI 탐지(`D01-A0001`)는 따로 매긴다
            last[f.file_id] = max(last.get(f.file_id, 0), int(tail))

    patterns, seen = [], set()
    for src in sources:
        if src.category in GRAY_CATEGORIES or len(src.raw_value.strip()) < 2:
            continue
        key = (src.category, normalize_value(src.raw_value))
        if key not in seen:
            seen.add(key)
            patterns.append((src, value_pattern(src.category, src.raw_value, word_start=word_start)))

    new: list[Finding] = []
    for di, res in enumerate(results):
        file_id = f"D{di + 1:02d}"
        rows = _row_index(res)
        for block in res.blocks:
            spans = taken.setdefault((file_id, block.block_id), [])
            for src, pattern in patterns:
                for m in pattern.finditer(block.text):
                    s, e = m.start(), m.end()
                    if any(s < te and ts < e for ts, te in spans):
                        continue
                    spans.append((s, e))
                    last[file_id] = last.get(file_id, 0) + 1
                    c = Candidate(s, e, src.category, TIER_REVIEW, {BY_CONFIRMED}, role=src.role_hint)
                    new.append(Finding(
                        finding_id=f"{file_id}-{last[file_id]:04d}", file_id=file_id, filename=res.source_name,
                        format=res.format, category=src.category, raw_value=block.text[s:e],
                        context=_context(block, c, rows), block_id=block.block_id, start=s, end=e,
                        location=block.location, sentence=_sentence_of(_sentences(block.text), s),
                        detected_by=(BY_CONFIRMED,), tier=TIER_REVIEW, review_status=STATUS_PENDING,
                        action=default_action(TIER_REVIEW, src.category), role_hint=src.role_hint,
                        hidden=block.hidden))
    for f in new:
        log.debug("재검색 %s 유형=%s", f.finding_id, f.category)
    if new:
        log.info("확정값 재검색: 새 탐지 %d건", len(new))
    return new


def _propagate(results, per_block, exceptions) -> None:
    """확실 등급 이름을 모든 블록에서 다시 찾아 '검토' 후보로 더한다(기획서 4장 '확정 인명 자동 확산')."""
    texts = {(di, b.block_id): b.text for di, res in enumerate(results) for b in res.blocks}
    names: set[str] = set()
    for key, cands in per_block.items():
        text = texts[key]
        for c in cands:
            value = text[c.start:c.end]
            if (c.category == NAME and c.tier == TIER_CERTAIN and c.exclude_hint is None
                    and looks_like_name(value) and normalize_value(value) not in exceptions):
                names.add(value)
    if not names:
        return
    pattern = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))
    for di, res in enumerate(results):
        for block in res.blocks:
            new = []
            for m in pattern.finditer(block.text):
                if m.start() > 0 and re.match(r"[가-힣]", block.text[m.start() - 1]):
                    continue  # 더 긴 낱말의 뒷부분
                new.append(Candidate(m.start(), m.end(), NAME, TIER_REVIEW, {BY_PROPAGATION}))
            if new:
                key = (di, block.block_id)
                per_block[key] = merge(per_block[key] + new)


def _sentences(text: str) -> list[int]:
    """문장 시작 위치 목록."""
    return [0] + [m.end() for m in _SENTENCE_END.finditer(text)]


def _sentence_of(starts: list[int], pos: int) -> int:
    idx = 0
    for i, s in enumerate(starts):
        if s <= pos:
            idx = i
    return idx


def _row_index(res: ReadResult) -> dict[tuple[int, int], list[Block]]:
    rows: dict[tuple[int, int], list[Block]] = {}
    for b in res.blocks:
        if b.kind == CELL and not b.is_header:
            rows.setdefault((b.location.table_index, b.location.row), []).append(b)
    return rows


def _context(block: Block, c: Candidate, rows) -> str:
    """화면에 보여 줄 주변 원문 한 줄. 원문이므로 로그에 쓰지 않는다."""
    loc = block.location
    if block.kind == CELL and not block.is_header:
        cells = rows.get((loc.table_index, loc.row), [block])
        line = " | ".join(f"{b.location.header}: {b.text}" if b.location.header else b.text for b in cells)
        return line[:200]
    if block.kind == METADATA:
        return f"문서 속성 {loc.field}: {block.text}"[:200]
    if block.kind == COMMENT:
        return f"메모: {block.text}"[:200]
    left = max(0, c.start - 40)
    right = min(len(block.text), c.end + 40)
    return ("…" if left else "") + block.text[left:right] + ("…" if right < len(block.text) else "")
