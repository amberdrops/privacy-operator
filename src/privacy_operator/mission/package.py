"""작전 패키지 만들기: `to_ai/<작전번호>/` (docs/04_작전패키지_규격.md).

    to_ai/M-20261107-01/
      mission.json      작전 번호, 파일 목록과 해시, 가명코드 뜻풀이. 원문 값 없음
      AGENT_RULES.md    에이전트 규칙. 같은 내용을 CLAUDE.md, AGENTS.md로도 둔다
      docs/*.md         가명처리된 AI용 사본

다 쓴 뒤 패키지 안 모든 파일을 차단 목록으로 다시 검사한다. 하나라도 걸리면 패키지를 지운다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from privacy_operator.alias.codes import AliasBook
from privacy_operator.alias.pseudonymize import DELETED, PseudoDocument
from privacy_operator.alias.vault import UnlockedVault
from privacy_operator.detect.dictionaries import GRAY_CATEGORIES
from privacy_operator.detect.finding import ACTION_DELETE, ACTION_KEEP, STATUS_CONFIRMED, Finding
from privacy_operator.restore.restore import CODE_RE
from privacy_operator.settings import Settings, validate_paths
from privacy_operator.writer.blocklist import BlocklistHit, blocklist_hits, check_blocklist
from privacy_operator.writer.markdown import to_markdown
from privacy_operator.writer.original import OriginalOutput, write_original
from privacy_operator.writer.plan import ReplacementPlan

log = logging.getLogger(__name__)

SCHEMA = "privacy-operator/mission@1"
RULE_FILES = ("AGENT_RULES.md", "CLAUDE.md", "AGENTS.md")


class PackageError(Exception):
    pass


# 처리 기록의 목적. 이 프로그램의 쓰임은 늘 같아 사람이 적지 않는다(2026-11-08 결정)
DEFAULT_PURPOSE = "업무 문서를 AI에 맡기기 전 가명처리"


@dataclass
class ProcessingRecord:
    """처리 기록(개인정보 보호법 제28조의4 제3항). 금고의 log.json에 남긴다."""

    purpose: str = ""
    ai_service: str = ""
    retention_days: int = 90


def agent_rules_text(mission_id: str, removed_categories: list[str]) -> str:
    """에이전트 규칙. 채팅형 AI에는 '규칙 복사'로 이 글을 그대로 붙여 넣는다."""
    rules = [
        "대괄호로 된 가명코드(예: [이름-01])는 글자 하나도 바꾸지 말고 그대로 쓰세요.\n"
        "   띄어쓰기, 괄호, 숫자 자리수까지 유지합니다.",
        "가명코드 뒤 조사는 자연스럽게 써도 됩니다. 나중에 실명에 맞춰 고칩니다.",
        "가명코드가 누구인지 추측하거나, 실명이나 연락처를 지어내지 마세요.",
    ]
    if removed_categories:
        rules.append(f"삭제된 정보({', '.join(removed_categories)})는 문서에 없는 것으로 다루세요. "
                     f"문서의 {DELETED} 표시는 그대로 두세요.")
    rules += [
        f"결과물은 from_ai/{mission_id}/ 폴더에 저장하고,\n"
        f"   파일 이름 앞에 작전 번호를 붙이세요. 예: {mission_id}_결과보고서.md",
        "이 폴더 밖의 파일은 열지 마세요.",
    ]
    body = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, start=1))
    return f"# 이 작전의 규칙\n\n이 폴더의 문서는 개인정보를 가명코드로 바꾼 것입니다.\n\n{body}\n"


def _secret_findings(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.review_status == STATUS_CONFIRMED and f.action != ACTION_KEEP
            and f.category not in GRAY_CATEGORIES and f.raw_value.strip()]


def safe_name(name: str, findings: list[Finding]) -> str:
    """파일 이름 속 확정 값을 가명코드(삭제는 '삭제')로 바꾼다. `김민수_계약서` → `[이름-01]_계약서`."""
    for f in sorted(_secret_findings(findings), key=lambda f: -len(f.raw_value)):
        if f.raw_value in name:
            name = name.replace(f.raw_value, f.alias_code or "삭제")
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def _unique(name: str, used: set[str], fmt: str) -> str:
    if name not in used:
        return name
    stem = name[:-3]
    candidate = f"{stem}_{fmt}.md"
    n = 2
    while candidate in used:
        candidate = f"{stem}_{fmt}_{n}.md"
        n += 1
    return candidate


def build_package(settings: Settings, mission_id: str, docs: list[PseudoDocument], findings: list[Finding],
                  book: AliasBook, vault: UnlockedVault, record: ProcessingRecord | None = None,
                  alias_policy: str = "per_mission", now: datetime | None = None,
                  sources: list[Path] | None = None, reader=None,
                  originals: list[OriginalOutput] | None = None) -> Path:
    """sources(원본 파일 경로, docs와 같은 순서)와 reader를 주면 원본 형식 사본도 만든다(8단계).
    원본 형식 사본은 재검사를 통과한 것만 넣고, 결과는 originals에 담는다."""
    validate_paths(settings)
    if book.mission_id != mission_id:
        raise PackageError("가명코드 장부의 작전 번호가 다릅니다")
    root = settings.to_ai_path / mission_id
    if root.exists():
        raise PackageError("같은 작전 번호의 패키지가 이미 있습니다. 덮어쓰지 않습니다")
    bundles: dict[str, str] = {}
    for d in docs:
        bundles.update(d.bundles)

    now = now or datetime.now(timezone.utc).astimezone()
    removed = sorted({f.category for f in findings if f.review_status == STATUS_CONFIRMED
                      and f.action == ACTION_DELETE})

    # 1. 쓰기 전에 문서마다 차단 목록 검사
    texts: list[tuple[str, str, str]] = []  # (파일 이름, 내용, 원본 형식)
    used: set[str] = set()
    for d in docs:
        text = to_markdown(d.blocks)
        check_blocklist(text, findings, bundles)
        name = _unique(safe_name(Path(d.source_name).stem, findings) + ".md", used, d.format)
        used.add(name)
        texts.append((name, text, d.format))

    files = [{"path": f"docs/{name}", "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
              "source_format": fmt, "form": "markdown"} for name, text, fmt in texts]
    present = {m.group(0) for _, text, _ in texts for m in CODE_RE.finditer(text)}
    rules = agent_rules_text(mission_id, removed)

    # 2. 쓰고, 패키지 전체를 다시 검사
    try:
        (root / "docs").mkdir(parents=True)
        for name, text, _ in texts:
            (root / "docs" / name).write_text(text, encoding="utf-8", newline="\n")
        if sources and reader is not None:
            plan = ReplacementPlan(findings, docs)
            for d, src, (md_name, _, _) in zip(docs, sources, texts):
                out = write_original(Path(src), d.format, plan, root / "docs", md_name[:-3], reader)
                if originals is not None:
                    originals.append(out)
                if out.ok:
                    files.append({"path": f"docs/{out.path.name}",
                                  "sha256": hashlib.sha256(out.path.read_bytes()).hexdigest(),
                                  "source_format": d.format, "form": "original"})
        mission = {
            "schema": SCHEMA,
            "mission_id": mission_id,
            "created_at": now.isoformat(timespec="seconds"),
            "alias_policy": alias_policy,
            "files": files,
            "alias_legend": [x for x in book.legend() if x["alias_code"] in present],
            "removed_categories": removed,
            "return_to": f"from_ai/{mission_id}/",
        }
        (root / "mission.json").write_text(json.dumps(mission, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8", newline="\n")
        for rule_file in RULE_FILES:
            (root / rule_file).write_text(rules, encoding="utf-8", newline="\n")
        for p in root.rglob("*"):
            # 글 파일만 여기서 본다. 원본 형식 사본은 write_original이 형식별로 이미 검사했다
            if p.is_file() and p.suffix.lower() in (".md", ".json", ".txt"):
                hits = blocklist_hits(p.read_text(encoding="utf-8"), findings, bundles)
                if hits:
                    raise BlocklistHit(hits)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise

    (settings.from_ai_path / mission_id).mkdir(parents=True, exist_ok=True)

    rec = record or ProcessingRecord(retention_days=settings.retention_days)
    log_record = {
        "mission_id": mission_id,
        "processed_at": now.isoformat(timespec="seconds"),
        "purpose": rec.purpose,
        "ai_service": rec.ai_service,
        "retention_days": rec.retention_days,
        "delete_after": (now + timedelta(days=rec.retention_days)).date().isoformat(),
        "files": [f["path"] for f in files],
        "alias_count": len(book.entries()),
        "removed_categories": removed,
    }
    check_blocklist(json.dumps(log_record, ensure_ascii=False), findings, bundles)
    vault.save_log(mission_id, log_record)
    log.info("작전 패키지 %s: 문서 %d개, 가명코드 %d개", mission_id, len(files), len(mission["alias_legend"]))
    return root
