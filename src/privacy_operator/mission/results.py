"""받은 결과함(`from_ai/`)의 결과물을 찾아 작전 번호에 맞는 대응표로 복원한다.

- 작전 번호는 폴더 이름(`from_ai/M-20261107-01/`)이나 파일 이름 앞(`M-20261107-01_결과.md`)에서 찾는다.
- 작전 번호가 없거나 폴더와 파일 이름이 서로 다르면 추정하지 않는다. 사람이 고르게 한다.
- 복원본은 설정의 복원본 폴더 `<작전번호>/`에 새 파일로 쓴다. 받은 파일은 바꾸지 않는다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from privacy_operator.alias.vault import UnlockedVault
from privacy_operator.restore.restore import RestoreResult, restore
from privacy_operator.restore.zipdoc import leftover_codes, restore_zip
from privacy_operator.settings import Settings, validate_paths

log = logging.getLogger(__name__)

RESTORABLE = frozenset({".md", ".txt", ".hwpx", ".docx", ".xlsx"})
# 서식 그대로 XML 글 칸만 복원(8단계). XLSX도 XML 묶음이라 같은 길로 한다(2026-11-08):
# 셀 글자(sharedStrings, 칸 안 글), 메모, 문서 속성이 글 칸에 있고 서식, 수식, 차트, 그림은 그대로 남는다
ZIP_DOCS = frozenset({".hwpx", ".docx", ".xlsx"})
LATER = frozenset({".pdf"})  # 받는 형식이 아니다. 필요해지면 PyMuPDF로
_MISSION_RE = re.compile(r"M-\d{8}-\d{2,}")

# 찾은 결과물의 상태
READY = "ready"
NO_MISSION = "no_mission"  # 작전 번호를 못 찾음. 사람이 골라야 한다
CONFLICT = "conflict"  # 폴더와 파일 이름의 작전 번호가 다름
NOT_YET = "format_not_yet"  # 아직 복원하지 못하는 형식
UNSUPPORTED = "unsupported"


class NeedsMissionChoice(Exception):
    def __init__(self, name: str):
        super().__init__("어느 작전의 결과물인지 골라 주세요")
        self.name = name


@dataclass
class ResultFile:
    path: Path
    mission_id: str | None
    state: str


@dataclass
class RestoredFile:
    source: Path
    output: Path
    mission_id: str
    result: RestoreResult = field(repr=False)

    @property
    def complete(self) -> bool:
        return self.result.complete


def _mission_of(path: Path, from_ai: Path) -> tuple[str | None, str]:
    rel = path.relative_to(from_ai)
    folder = rel.parts[0] if len(rel.parts) > 1 and _MISSION_RE.fullmatch(rel.parts[0]) else None
    m = _MISSION_RE.match(path.name)
    prefix = m.group(0) if m else None
    if folder and prefix and folder != prefix:
        return None, CONFLICT
    found = folder or prefix
    return found, (READY if found else NO_MISSION)


def find_results(settings: Settings) -> list[ResultFile]:
    from_ai = settings.from_ai_path
    if not from_ai.is_dir():
        return []
    out = []
    for p in sorted(from_ai.rglob("*")):
        if not p.is_file() or p.name.startswith("~$"):
            continue
        mission_id, state = _mission_of(p, from_ai)
        suffix = p.suffix.lower()
        if suffix in LATER:
            state = NOT_YET
        elif suffix not in RESTORABLE:
            state = UNSUPPORTED
        out.append(ResultFile(p, mission_id, state))
    return out


def _free_path(folder: Path, name: str) -> Path:
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return path


def restore_result(settings: Settings, vault: UnlockedVault, item: ResultFile, mission_id: str | None = None,
                   accept: frozenset[str] = frozenset()) -> RestoredFile:
    """mission_id를 주면 그 작전으로 복원한다(사람이 고른 경우). 안 주면 찾은 작전 번호를 쓴다."""
    validate_paths(settings)
    if item.state in (NOT_YET, UNSUPPORTED):
        raise ValueError("아직 복원할 수 없는 형식입니다")
    chosen = mission_id or (item.mission_id if item.state == READY else None)
    if not chosen:
        raise NeedsMissionChoice(item.path.name)
    mapping = vault.load_mapping(chosen)
    folder = settings.restored_path / chosen
    folder.mkdir(parents=True, exist_ok=True)
    output = _free_path(folder, item.path.name)
    if item.path.suffix.lower() in ZIP_DOCS:
        result = restore_zip(item.path, output, mapping, accept)
        result.leftover_codes = leftover_codes(output, mapping)
    else:
        result = restore(item.path.read_text(encoding="utf-8-sig"), mapping, accept)
        output.write_text(result.text, encoding="utf-8", newline="\n")
    log.info("결과물 복원 %s: 되돌림 %d건, 확인 필요 %d건, 대응표에 없는 가명코드 %d개", chosen,
             sum(result.restored.values()), len(result.variants), len(result.unknown_codes))
    return RestoredFile(item.path, output, chosen, result)
