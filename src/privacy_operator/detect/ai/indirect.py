"""Ollama 로컬 모델로 간접식별 후보를 찾는다(9단계, 선택).

이름 없이도 한 사람을 알아볼 수 있는 표현(소속과 직급과 나이, "제가 ○○과에 있을 때")을 '참고' 등급,
'간접식별' 유형으로 표시만 한다. 자동으로 처리하지 않는다. 같은 문장에 이름이 있거나 다른 회색 영역
값과 함께 있으면 조합 승격이 '검토'로 올린다(AI 판단만으로는 올리지 않는다).

- 모델이 답한 글자가 블록 안에 그대로 있을 때만 받는다(띄어쓰기 차이는 허용). 없으면 버린다.
- 모델에 보내는 글은 원문이지만 내 컴퓨터(127.0.0.1) 안에서만 오간다. 로그에는 건수만 쓴다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from privacy_operator.detect.ai.ollama_local import (
    CLOUD_MODEL,
    MODEL_NOT_INSTALLED,
    NOT_RUNNING,
    LocalOllama,
    OllamaError,
)
from privacy_operator.detect.ai.spans import AiSpan, to_findings
from privacy_operator.detect.dictionaries import INDIRECT
from privacy_operator.detect.finding import BY_AI_INDIRECT, TIER_REFERENCE, Finding
from privacy_operator.reader.blocks import CELL, METADATA, Block, ReadResult

log = logging.getLogger(__name__)

CHUNK_CHARS = 1800
MIN_BLOCK_CHARS = 8
MAX_CANDIDATE_CHARS = 120

SYSTEM = """너는 문서에서 간접식별 정보를 찾는 도우미다.
간접식별 정보는 이름이 없어도 특정한 한 사람을 알아볼 수 있게 하는 표현이다. 예:
- 소속, 직위나 직급, 나이대, 성별, 경력 연차가 함께 쓰여 한 사람으로 좁혀지는 표현
- "제가 ○○과에 있을 때"처럼 자기 경력이나 근무 이력을 밝히는 말
- 한 명뿐인 직책(예: ○○학교 교무부장, ○○과 팀장)
- 특이한 경력, 수상, 사건, 건강 상태, 가족 관계처럼 주변 사람이 알아볼 수 있는 사실
고르지 않는 것:
- 이름, 전화번호, 이메일, 주소, 주민등록번호, 계좌번호(이미 따로 찾았다)
- 기관이나 부서 전체를 가리킬 뿐 사람을 가리키지 않는 말, 일반적인 업무 설명, 일정
규칙:
- text에는 문서에 있는 글자를 한 글자도 바꾸지 말고 그대로 옮긴다. 요약하거나 고쳐 쓰지 않는다.
- text는 사람을 좁히는 말(소속, 직책, 학년과 반, 경력, 사건, 가족 관계)을 모두 담은 이어진 한 구간으로 고른다.
  문장 전체가 아니라 그 구간만 고른다. 예: "지난달 둘째를 입양한 행정실 주무관이"에서 "둘째를 입양한 행정실 주무관".
- block에는 그 글이 있는 [번호]의 번호를 쓴다.
- reason에는 왜 사람을 알아볼 수 있는지 20자 안으로 쓴다. 문서의 이름이나 연락처를 옮겨 쓰지 않는다.
- 찾은 것이 없으면 candidates를 빈 목록으로 둔다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block": {"type": "integer"},
                    "text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["block", "text", "reason"],
            },
        }
    },
    "required": ["candidates"],
}


@dataclass
class IndirectReport:
    chunks: int = 0
    candidates: int = 0  # 모델이 낸 후보
    dropped: int = 0  # 문서에서 찾지 못해 버린 후보
    added: int = 0
    failed_chunks: int = 0


def _eligible(block: Block) -> bool:
    if block.kind == METADATA or block.is_header:
        return False
    text = block.text.strip()
    return len(text) >= (MIN_BLOCK_CHARS * 2 if block.kind == CELL else MIN_BLOCK_CHARS)


def chunks(res: ReadResult) -> list[list[Block]]:
    """모델에 한 번에 보낼 블록 묶음."""
    out, cur, size = [], [], 0
    for b in res.blocks:
        if not _eligible(b):
            continue
        if cur and size + len(b.text) > CHUNK_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += len(b.text)
    if cur:
        out.append(cur)
    return out


def prompt(blocks: list[Block]) -> str:
    lines = [f"[{i}] {b.text.strip()}" for i, b in enumerate(blocks, start=1)]
    return "다음 문서 조각에서 간접식별 정보를 찾아라.\n\n" + "\n\n".join(lines)


def locate(text: str, needle: str) -> list[tuple[int, int]]:
    """needle이 text에 있는 자리. 띄어쓰기 차이만 허용한다."""
    needle = needle.strip()
    if len(needle) < 2 or len(needle) > MAX_CANDIDATE_CHARS:
        return []
    parts = needle.split()
    pattern = r"\s*".join(re.escape(p) for p in parts) if len(parts) > 1 else re.escape(needle)
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)]


def parse(answer: dict, blocks: list[Block], doc_index: int, report: IndirectReport) -> list[AiSpan]:
    spans = []
    items = answer.get("candidates")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        report.candidates += 1
        no, text, reason = item.get("block"), item.get("text"), item.get("reason")
        if not isinstance(no, int) or not isinstance(text, str) or not 1 <= no <= len(blocks):
            report.dropped += 1
            continue
        block = blocks[no - 1]
        where = locate(block.text, text)
        if not where:  # 모델이 고쳐 썼거나 지어낸 글. 다른 블록에 있어도 받지 않는다
            report.dropped += 1
            continue
        note = reason.strip()[:200] if isinstance(reason, str) and reason.strip() else None
        spans += [AiSpan(doc_index, block.block_id, s, e, INDIRECT, note) for s, e in where]
    return spans


def find_indirect(results: list[ReadResult], existing: list[Finding], client: LocalOllama, model: str,
                  doc_indexes: list[int] | None = None, progress: Callable[[int, int], None] | None = None,
                  cancelled: Callable[[], bool] | None = None) -> tuple[list[Finding], IndirectReport]:
    """새 간접식별 후보와 보고. 모델을 부를 수 없으면 OllamaError(첫 묶음에서)."""
    client.check_model(model)
    report = IndirectReport()
    todo = [(di, c) for di in (doc_indexes if doc_indexes is not None else range(len(results)))
            for c in chunks(results[di])]
    spans: list[AiSpan] = []
    for n, (di, blocks) in enumerate(todo, start=1):
        if cancelled and cancelled():
            break
        report.chunks += 1
        try:
            answer = client.chat_json(model, SYSTEM, prompt(blocks), SCHEMA)
        except OllamaError as e:
            if e.code in (CLOUD_MODEL, MODEL_NOT_INSTALLED, NOT_RUNNING):
                raise
            report.failed_chunks += 1  # 답 모양이 틀림. 다음 묶음으로
            log.warning("간접식별 묶음 %d 실패: %s", n, e.code)
        else:
            spans += parse(answer, blocks, di, report)
        if progress:
            progress(n, len(todo))
    new = to_findings(results, existing, spans, BY_AI_INDIRECT, TIER_REFERENCE, gray_overlap=True)
    report.added = len(new)
    log.info("간접식별 후보: 묶음 %d, 모델 후보 %d, 버림 %d, 새 탐지 %d, 실패 묶음 %d", report.chunks,
             report.candidates, report.dropped, report.added, report.failed_chunks)
    return new, report
