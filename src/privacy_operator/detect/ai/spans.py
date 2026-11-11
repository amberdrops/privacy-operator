"""AI가 찾은 자리를 탐지(finding)로 바꾼다.

- 등급은 넘겨받은 것으로 시작한다. AI 판단만으로 '확실'이 되지 않는다(Schift는 '검토').
- 규칙 탐지가 이미 찾은 자리와 겹치면 버린다. 규칙이 먼저다.
- finding_id는 `D01-A0001`처럼 A를 붙여 규칙 탐지 번호와 섞이지 않게 한다. 문서를 더 불러와 다시
  탐지해도 AI 탐지 번호는 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass

from privacy_operator.detect.detector import _context, _row_index, _sentence_of, _sentences
from privacy_operator.detect.finding import Finding, default_action, default_status
from privacy_operator.detect.patterns import Candidate
from privacy_operator.reader.blocks import ReadResult

AI_ID_MARK = "A"


@dataclass(frozen=True)
class AiSpan:
    doc_index: int
    block_id: str
    start: int
    end: int
    category: str
    note: str | None = None  # AI가 댄 까닭. 원문이 섞일 수 있으므로 화면에만 쓰고 로그에 쓰지 않는다

    def __repr__(self) -> str:
        return f"AiSpan(doc={self.doc_index}, block={self.block_id!r}, category={self.category!r})"


def is_ai(f: Finding) -> bool:
    return f.finding_id.rsplit("-", 1)[-1].startswith(AI_ID_MARK)


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def to_findings(results: list[ReadResult], existing: list[Finding], spans: list[AiSpan], by: str,
                tier: str) -> list[Finding]:
    taken: dict[tuple[str, str], list[Finding]] = {}
    last: dict[str, int] = {}
    for f in existing:
        taken.setdefault((f.file_id, f.block_id), []).append(f)
        tail = f.finding_id.rsplit("-", 1)[-1]
        if tail.startswith(AI_ID_MARK) and tail[1:].isdigit():
            last[f.file_id] = max(last.get(f.file_id, 0), int(tail[1:]))

    new: list[Finding] = []
    rows_of: dict[int, dict] = {}
    for sp in sorted(spans, key=lambda s: (s.doc_index, s.block_id, s.start)):
        if not 0 <= sp.doc_index < len(results):
            continue
        res = results[sp.doc_index]
        block = next((b for b in res.blocks if b.block_id == sp.block_id), None)
        if block is None or not 0 <= sp.start < sp.end <= len(block.text):
            continue
        start, end = _trim(block.text, sp.start, sp.end)
        if end - start < 2:
            continue
        file_id = f"D{sp.doc_index + 1:02d}"
        others = taken.setdefault((file_id, block.block_id), [])
        if any(start < o.end and o.start < end for o in others):
            continue
        last[file_id] = last.get(file_id, 0) + 1
        rows = rows_of.setdefault(sp.doc_index, _row_index(res))
        c = Candidate(start, end, sp.category, tier, {by})
        f = Finding(
            finding_id=f"{file_id}-{AI_ID_MARK}{last[file_id]:04d}", file_id=file_id, filename=res.source_name,
            format=res.format, category=sp.category, raw_value=block.text[start:end],
            context=_context(block, c, rows), block_id=block.block_id, start=start, end=end,
            location=block.location, sentence=_sentence_of(_sentences(block.text), start),
            detected_by=(by,), tier=tier, review_status=default_status(tier),
            action=default_action(tier, sp.category), hidden=block.hidden, note=sp.note)
        others.append(f)
        new.append(f)
    return new
