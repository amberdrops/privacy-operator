"""판정이 끝난 탐지를 문서 블록에 반영한다: 가명처리는 가명코드로, 삭제는 `[삭제]`로 바꾼다.

사람이 '확정'한 것만 처리한다. '대기'가 하나라도 남아 있으면 거부한다(절대 규칙 4).
원본 블록은 바꾸지 않고 새 블록 목록을 만든다.
"""

from __future__ import annotations

import dataclasses
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from privacy_operator.alias.codes import AI_INDIRECT_ROLE, AliasBook
from privacy_operator.detect.dictionaries import GRAY_CATEGORIES, INDIRECT
from privacy_operator.detect.finding import (
    ACTION_ALIAS,
    ACTION_DELETE,
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    Finding,
)
from privacy_operator.detect.ai.spans import is_ai
from privacy_operator.reader.blocks import Block, ReadResult

log = logging.getLogger(__name__)

DELETED = "[삭제]"


class PendingFindings(Exception):
    def __init__(self, count: int):
        super().__init__(f"아직 판정하지 않은 탐지 항목이 {count}건 있습니다")
        self.count = count


@dataclass
class PseudoDocument:
    source_name: str
    format: str
    blocks: list[Block]
    applied: list[str] = field(default_factory=list)  # 반영한 finding_id
    bundles: dict[str, str] = field(default_factory=dict, repr=False)  # group_id → 묶음 원문(차단 목록용)

    def __repr__(self) -> str:
        return f"PseudoDocument({self.source_name!r}, blocks={len(self.blocks)}, applied={len(self.applied)})"


def _ai_role(f: Finding) -> str | None:
    """AI가 찾은 간접식별 후보는 `[간접식별-01]`, 규칙이 묶은 회색 영역은 `[소속직급-01]`.
    탐지의 유형이 간접식별인 것은 AI가 찾은 것(과 그것을 확정해 다시 찾은 것)뿐이다."""
    return AI_INDIRECT_ROLE if f.category == INDIRECT else None


Edits = dict[tuple[str, str], list[tuple[int, int, str, list[Finding]]]]


def plan(results: list[ReadResult], findings: list[Finding], book: AliasBook) -> tuple[Edits, dict[str, str]]:
    """확정된 탐지에 가명코드를 매기고 블록마다 바꿀 곳을 정한다. 블록과 탐지는 바꾸지 않는다.

    대기가 남아 있어도 부를 수 있다(검토 표의 가명코드 미리 보기). 확정된 것만 본다.
    """
    todo = [f for f in findings if f.review_status == STATUS_CONFIRMED and f.action in (ACTION_ALIAS, ACTION_DELETE)]
    file_of = {f"D{i + 1:02d}": res for i, res in enumerate(results)}

    # 회색 영역 묶음: 모두 확정되어 가명처리이고 같은 블록이면 가명코드 하나로
    groups: dict[str, list[Finding]] = defaultdict(list)
    for f in todo:
        if f.group_id and f.action == ACTION_ALIAS:
            groups[f.group_id].append(f)
    whole_groups = {}
    for gid, members in groups.items():
        # 사람이 제외한 AI 후보는 없던 것으로 본다. 규칙만으로 된 묶음이 AI 후보 때문에 풀리지 않게
        all_members = [f for f in findings if f.group_id == gid
                       and not (is_ai(f) and f.review_status == STATUS_EXCLUDED)]
        if len(members) == len(all_members) and len({(f.file_id, f.block_id) for f in members}) == 1:
            # 묶음 사이에 이름 같은 다른 값이 끼어 있으면 묶지 않고 하나씩 바꾼다. 공문 결재란(주무관 윤채아 …
            # 교장 11/10 남궁하린)에서 처음 직위부터 끝 직위까지가 통째로 [소속직급-01] 하나가 되었다(2026-11-10)
            start, end = min(f.start for f in members), max(f.end for f in members)
            ids = {f.finding_id for f in members}
            between = [f for f in todo if f.finding_id not in ids and f.file_id == members[0].file_id
                       and f.block_id == members[0].block_id and start <= f.start and f.end <= end
                       and f.category not in GRAY_CATEGORIES and f.category != INDIRECT]
            if not between:
                whole_groups[gid] = members

    # (file_id, block_id) → [(start, end, 바꿀 글자, finding들)]
    edits: dict[tuple[str, str], list[tuple[int, int, str, list[Finding]]]] = defaultdict(list)
    texts = {(fid, b.block_id): b.text for fid, res in file_of.items() for b in res.blocks}

    bundles: dict[str, str] = {}
    for gid, members in sorted(whole_groups.items()):
        first = min(members, key=lambda f: f.start)
        start, end = first.start, max(f.end for f in members)
        bundle = texts[(first.file_id, first.block_id)][start:end]
        bundles[gid] = bundle
        # AI가 찾은 간접식별 후보가 든 묶음은 [간접식별-01], 아니면 [소속직급-01]
        role = next((r for r in map(_ai_role, members) if r), None)
        code = book.code_for(INDIRECT, bundle, first.filename, role)
        edits[(first.file_id, first.block_id)].append((start, end, code, members))

    grouped = {f.finding_id for ms in whole_groups.values() for f in ms}
    for f in sorted(todo, key=lambda f: (f.file_id, f.finding_id)):  # 가명코드 번호는 문서에 나온 순서
        if f.finding_id in grouped:
            continue
        if f.action == ACTION_DELETE:
            edits[(f.file_id, f.block_id)].append((f.start, f.end, DELETED, [f]))
        else:
            code = book.code_for(f.category, f.raw_value, f.filename, _ai_role(f))
            edits[(f.file_id, f.block_id)].append((f.start, f.end, code, [f]))
    return edits, bundles


def _choose(items):
    """겹치는 바꿀 곳 중 긴 것을 고른다. 고른 것 안에 통째로 든 탐지는 그 가명코드로 함께 처리한 것으로 본다
    (회색 영역 낱개 값을 품은 간접식별 후보). 반쯤 겹치는 것은 버린다. 남은 값은 차단 목록 검사가 잡는다."""
    chosen, inside = [], {}
    for item in sorted(items, key=lambda e: (-(e[1] - e[0]), e[0])):
        start, end = item[0], item[1]
        host = next((c for c in chosen if start < c[1] and c[0] < end), None)
        if host is None:
            chosen.append(item)
        elif host[0] <= start and end <= host[1]:
            inside.setdefault((host[0], host[1]), []).extend(item[3])
    return chosen, inside


def preview_codes(results: list[ReadResult], findings: list[Finding]) -> dict[str, str]:
    """finding_id → 지금 판정대로 작전을 개시하면 붙을 가명코드(삭제는 `[삭제]`)."""
    edits, _ = plan(results, findings, AliasBook("preview"))
    return {f.finding_id: new for items in edits.values() for _, _, new, fs in items for f in fs}


def pseudonymize(results: list[ReadResult], findings: list[Finding], book: AliasBook) -> list[PseudoDocument]:
    pending = [f for f in findings if f.review_status == STATUS_PENDING]
    if pending:
        raise PendingFindings(len(pending))

    file_of = {f"D{i + 1:02d}": res for i, res in enumerate(results)}
    edits, bundles = plan(results, findings, book)
    docs = []
    for fid, res in file_of.items():
        doc = PseudoDocument(res.source_name, res.format, [], bundles=bundles)
        for block in res.blocks:
            text = block.text
            chosen, inside = _choose(edits.get((fid, block.block_id), []))
            for start, end, new, fs in sorted(chosen, key=lambda e: -e[0]):
                text = text[:start] + new + text[end:]
                own = {id(f) for f in fs}
                for f in fs + inside.get((start, end), []):
                    # 안에 든 값은 바깥 가명코드로 함께 바뀌었다. replacement를 두지 않아
                    # 원본 형식 저장이 그 값을 따로(바깥 가명코드로) 바꾸지 않게 한다
                    f.replacement = new if id(f) in own else None
                    f.alias_code = None if new == DELETED else new
                    doc.applied.append(f.finding_id)
            doc.blocks.append(dataclasses.replace(block, text=text) if text != block.text else block)
        docs.append(doc)
    log.info("가명처리 %s: 문서 %d개, 반영 %d건, 가명코드 %d개", book.mission_id, len(docs),
             sum(len(d.applied) for d in docs), len(book.entries()))
    return docs
