"""작전 한 번의 흐름: 불러오기 → 탐지 → 판정 → 가명처리 → AI에게 보내기 → 복원.

화면(ui/)은 이 객체만 부른다. Qt 없이 시험할 수 있다.
판정('확정', '제외')은 사람이 화면에서 고른 것만 반영한다(절대 규칙 4).
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from privacy_operator.alias.codes import AliasBook, value_key
from privacy_operator.alias.pseudonymize import preview_codes, pseudonymize
from privacy_operator.alias.vault import UnlockedVault
from privacy_operator.detect import detect, escalate, rescan
from privacy_operator.detect.ai.indirect import IndirectReport, find_indirect
from privacy_operator.detect.ai.ollama_local import PARTIAL, LocalOllama, OllamaError
from privacy_operator.detect.ai.schift import SchiftEngine, SchiftError, SchiftReport, find_names, is_installed
from privacy_operator.detect.ai.spans import is_ai
from privacy_operator.detect.dictionaries import GRAY_CATEGORIES
from privacy_operator.detect.finding import (
    ACTION_ALIAS,
    ACTION_KEEP,
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    STATUS_REFERENCE,
    TIER_REFERENCE,
    TIER_REVIEW,
    Finding,
)
from privacy_operator.detect.patterns import fold, value_pattern
from privacy_operator.mission.package import ProcessingRecord, agent_rules_text, build_package
from privacy_operator.mission.results import ResultFile, RestoredFile, find_results, restore_result
from privacy_operator.reader import DocumentReader, ReadError, ReadResult
from privacy_operator.reader.blocks import BlindSpot
from privacy_operator.reader.read import SUPPORTED
from privacy_operator.settings import Settings
from privacy_operator.writer.original import OriginalOutput

log = logging.getLogger(__name__)

# 진행 단계(voice의 step.* 키와 같은 순서)
STEPS = ("load", "detect", "review", "alias", "send", "restore")


@dataclass
class Counts:
    total: int = 0
    pending: int = 0
    confirmed: int = 0
    excluded: int = 0
    reference: int = 0


@dataclass
class ValueGroup:
    key: tuple[str, str]
    category: str
    value: str
    members: list[Finding]

    @property
    def ids(self) -> list[str]:
        return [f.finding_id for f in self.members]

    @property
    def status(self) -> str | None:
        """구성원 판정이 모두 같으면 그 판정, 섞여 있으면 None."""
        statuses = {f.review_status for f in self.members}
        return statuses.pop() if len(statuses) == 1 else None

    @property
    def files(self) -> list[str]:
        return list(dict.fromkeys(f.filename for f in self.members))

    def __repr__(self) -> str:
        return f"ValueGroup(category={self.category!r}, members={len(self.members)})"


@dataclass
class AiRun:
    """AI 탐지 한 번의 결과. find_ai(다른 스레드)가 만들고 add_ai(화면 스레드)가 반영한다."""

    doc_indexes: list[int]
    findings: list[Finding]
    errors: list[str]  # 오류 코드(예: OLLAMA_NOT_RUNNING). 원문 없음
    schift: SchiftReport | None = None
    indirect: IndirectReport | None = None
    cancelled: bool = False

    def __repr__(self) -> str:
        return f"AiRun(docs={self.doc_indexes}, findings={len(self.findings)}, errors={self.errors})"


def is_reference(f: Finding) -> bool:
    """회색 영역 접힌 구역에 들어가는 항목: 아직 '참고'인 것."""
    return f.tier == TIER_REFERENCE and f.review_status == STATUS_REFERENCE


class MissionWorkflow:
    def __init__(self, settings: Settings, vault: UnlockedVault, reader_factory=DocumentReader):
        self.settings = settings
        self.vault = vault
        self._reader_factory = reader_factory
        self._owns_reader = reader_factory is DocumentReader  # 밖에서 준 읽기 도구는 준 쪽이 닫는다
        self._reader: DocumentReader | None = None
        self._reader_lock = threading.Lock()
        self.results: list[ReadResult] = []
        self.paths: list[Path] = []
        self.findings: list[Finding] = []
        self.mission_id: str | None = None
        self.package_path: Path | None = None
        self.removed_categories: list[str] = []
        self.originals: list[OriginalOutput] = []  # 원본 형식 사본 결과(작전 개시 뒤)
        self.ai_scanned: set[int] = set()  # AI 탐지를 마친 문서 순번
        self._preview: dict[str, str] = {}

    def close(self) -> None:
        with self._reader_lock:
            if self._reader is not None and self._owns_reader:
                self._reader.close()
            self._reader = None

    def _ensure_reader(self) -> DocumentReader:
        with self._reader_lock:
            if self._reader is None:
                self._reader = self._reader_factory()
            return self._reader

    def warm_up(self) -> None:
        """읽기 엔진을 미리 켠다. 첫 문서를 넣을 때 엔진 켜는 1초쯤을 덜 기다린다(2026-11-10).
        다른 스레드에서 불러도 된다. 실패하면 조용히 넘어가고, 문서를 읽을 때 다시 켠다(그때 오류를 알린다)."""
        try:
            self._ensure_reader()
        except Exception as e:  # noqa: BLE001
            log.info("reader warm-up skipped: %s", type(e).__name__)

    # ---------------------------------------------------------------- 불러오기, 탐지

    def add_files(self, paths) -> list[tuple[Path, str]]:
        """읽지 못한 파일은 (경로, 오류 코드)로 돌려준다. 이미 작전을 개시했으면 새 작전으로 시작한다."""
        read, failed = self.read_files(paths)
        self.add_results(read)
        return failed

    def read_files(self, paths, progress=None) -> tuple[list[tuple[Path, ReadResult]], list[tuple[Path, str]]]:
        """파일만 읽는다. 작전 상태는 건드리지 않으므로 다른 스레드에서 불러도 된다.
        progress(done, total)를 주면 파일 하나를 읽을 때마다 부른다."""
        paths = [Path(p) for p in paths]
        known = set() if self.mission_id else {q.resolve() for q in self.paths}
        read, failed = [], []
        for i, p in enumerate(paths, start=1):
            if p.suffix.lower() not in SUPPORTED:
                failed.append((p, "UNSUPPORTED_FORMAT"))
            elif p.resolve() not in known:
                known.add(p.resolve())
                try:
                    read.append((p, self._ensure_reader().read(p)))
                except ReadError as e:
                    failed.append((p, e.code))
                except Exception as e:  # noqa: BLE001
                    # 예상하지 못한 오류로 읽기 스레드가 죽으면 화면의 로딩이 끝나지 않는다(2026-11-11).
                    # 그 파일만 읽지 못한 것으로 돌린다. 예외 글에는 원문이 섞일 수 있어 종류만 적는다
                    log.error("read failed: %s", type(e).__name__)
                    failed.append((p, "READ_FAILED"))
            if progress:
                progress(i, len(paths))
        return read, failed

    def add_results(self, read: list[tuple[Path, ReadResult]]) -> None:
        if self.mission_id:
            self.reset()
        for p, res in read:
            if all(p.resolve() != q.resolve() for q in self.paths):
                self.paths.append(p)
                self.results.append(res)
        self._detect()

    def reset(self) -> None:
        self.results, self.paths, self.findings = [], [], []
        self.mission_id, self.package_path, self.removed_categories = None, None, []
        self.originals = []
        self.ai_scanned = set()
        self._preview = {}

    def _detect(self) -> None:
        """다시 탐지해도 사람이 이미 한 판정은 같은 자리의 탐지에 이어 붙인다. AI 탐지는 그대로 둔다."""
        before = {self._key(f): f for f in self.findings}
        ai = [f for f in self.findings if is_ai(f)]
        self.findings = detect(self.results) if self.results else []
        self._restore_verdicts(self.findings, before)
        self.findings += ai
        # 확정값 재검색으로 생겼던 탐지도 다시 만들고 판정을 이어 붙인다
        added = rescan(self.results, self.findings, self._confirmed())
        self._restore_verdicts(added, before)
        self.findings += added
        escalate(self.findings)
        self._refresh_preview()

    def _restore_verdicts(self, findings: list[Finding], before: dict) -> None:
        for f in findings:
            old = before.get(self._key(f))
            if old and (old.review_status in (STATUS_CONFIRMED, STATUS_EXCLUDED) or old.tier_set_by_human):
                f.review_status, f.tier, f.action = old.review_status, old.tier, old.action
                f.tier_set_by_human = old.tier_set_by_human

    def _confirmed(self, ids=None) -> list[Finding]:
        wanted = None if ids is None else set(ids)
        return [f for f in self.findings if f.review_status == STATUS_CONFIRMED
                and (wanted is None or f.finding_id in wanted)]

    @staticmethod
    def _key(f: Finding) -> tuple:
        return (f.filename, f.block_id, f.start, f.end, f.category)

    # ---------------------------------------------------------------- AI 탐지(9단계, 선택)

    def ai_enabled(self) -> bool:
        return self.settings.ai_schift or bool(self.settings.ai_ollama_model.strip())

    def ai_pending_docs(self) -> list[int]:
        """AI 탐지를 아직 하지 않은 문서 순번."""
        return [i for i in range(len(self.results)) if i not in self.ai_scanned]

    def can_find_ai(self) -> bool:
        return self.ai_enabled() and bool(self.ai_pending_docs()) and self.mission_id is None

    def find_ai(self, progress=None, cancelled=None) -> AiRun:
        """AI로 더 찾는다. 작전 상태는 바꾸지 않으므로 다른 스레드에서 불러도 된다(그동안 판정은 막는다).
        progress(done, total), cancelled() -> bool."""
        docs = self.ai_pending_docs()
        run = AiRun(docs, [], [])
        existing = list(self.findings)
        steps = (["schift"] if self.settings.ai_schift else []) + \
            (["ollama"] if self.settings.ai_ollama_model.strip() else [])

        def report(i):
            return (lambda done, total: progress(i * 100 + done * 100 // max(total, 1), len(steps) * 100))                 if progress else None

        for i, step in enumerate(steps):
            if cancelled and cancelled():
                break
            try:
                if step == "schift":
                    if not is_installed():
                        raise SchiftError("SCHIFT_NOT_INSTALLED")
                    with SchiftEngine() as engine:
                        new, run.schift = find_names(self.results, existing, engine, docs, report(i), cancelled)
                else:
                    client = LocalOllama(self.settings.ollama_port)
                    new, run.indirect = find_indirect(self.results, existing, client,
                                                      self.settings.ai_ollama_model.strip(), docs, report(i),
                                                      cancelled)
                    if run.indirect.failed_chunks:
                        run.errors.append(PARTIAL)  # 찾은 것은 넣고, 문서는 '마침'으로 두지 않는다
            except (SchiftError, OllamaError) as e:
                run.errors.append(e.code)
                log.warning("AI 탐지(%s) 실패: %s", step, e.code)
                continue
            existing += new
            run.findings += new
        run.cancelled = bool(cancelled and cancelled())
        return run

    def add_ai(self, run: AiRun) -> list[Finding]:
        """find_ai 결과를 반영한다. 오류 없이 끝까지 한 문서만 '마침'으로 둔다."""
        if self.mission_id:
            return []
        self.findings += run.findings
        if not run.errors and not run.cancelled:
            self.ai_scanned |= set(run.doc_indexes)
        escalate(self.findings)  # 간접식별 후보도 조합 규칙을 같은 방식으로 적용한다
        self._refresh_preview()
        return run.findings

    # ---------------------------------------------------------------- 판정

    def by_id(self, ids) -> list[Finding]:
        wanted = set(ids)
        return [f for f in self.findings if f.finding_id in wanted]

    def confirm(self, ids) -> list[Finding]:
        """확정하고, 확정한 값을 모든 문서에서 다시 찾는다. 새로 찾은 곳(대기)을 돌려준다."""
        for f in self.by_id(ids):
            if is_reference(f):
                continue  # 참고는 먼저 '가명처리로 올리기'를 해야 한다
            f.review_status = STATUS_CONFIRMED
        added = rescan(self.results, self.findings, self._confirmed(ids))
        self.findings += added
        escalate(self.findings)  # 확정한 이름 옆의 참고 항목이 올라온다
        self._refresh_preview()
        return added

    def leftovers(self, hit_ids) -> list[str]:
        """송출이 차단 목록에 걸려 멈춘 뒤: 확정한 값이 아직 남은 자리를 표에 모은다(2026-11-08).

        - 걸린 값을 확정한 줄 자체(이것을 제외하면 풀린다. 오탐을 확정한 경우, 2026-11-08)
        - 같은 값(또는 그 값을 품은 글자)인데 확정하지 않은 탐지(제외 등)
        - 탐지가 없던 자리(확정 뒤 문서가 바뀐 경우 등)는 새 탐지로 만든다. '대기'로 들어온다(자동 확정 금지)
        돌려주는 것은 사람이 판정할 finding_id다. 원문 값은 돌려주지 않는다."""
        sources = [f for f in self._confirmed(hit_ids) if f.category not in GRAY_CATEGORIES]
        added = rescan(self.results, self.findings, sources)
        self.findings += added
        patterns = [value_pattern(f.category, f.raw_value) for f in sources]
        ids = [f.finding_id for f in self.findings
               if not is_reference(f) and any(p.search(fold(f.raw_value)) for p in patterns)]
        self._refresh_preview()
        log.info("송출 멈춤 뒤 남은 자리 %d곳(새로 찾은 곳 %d)", len(ids), len(added))
        return ids

    def exclude(self, ids) -> None:
        for f in self.by_id(ids):
            if not is_reference(f):
                f.review_status = STATUS_EXCLUDED
        self._refresh_preview()

    def unjudge(self, ids) -> None:
        """확정이나 제외를 대기로 되돌린다(사람이 고름). 작전 개시 전에만 쓴다.
        확정 때 재검색으로 늘어난 탐지와 올라온 참고 항목은 그대로 둔다(등급을 내리는 것은 사람만 따로 한다)."""
        for f in self.by_id(ids):
            if f.review_status in (STATUS_CONFIRMED, STATUS_EXCLUDED):
                f.review_status = STATUS_PENDING
        self._refresh_preview()

    def raise_reference(self, ids) -> None:
        """참고 항목을 가명처리 대상으로 올린다(사람이 고름)."""
        for f in self.by_id(ids):
            if is_reference(f):
                f.tier, f.review_status, f.action = TIER_REVIEW, STATUS_PENDING, ACTION_ALIAS
                f.tier_set_by_human = True
        self._refresh_preview()

    def lower_to_reference(self, ids) -> None:
        """검토 항목을 참고로 내린다. 내리는 것은 사람만 한다."""
        for f in self.by_id(ids):
            if f.tier == TIER_REVIEW and f.review_status == STATUS_PENDING:
                f.tier, f.review_status, f.action = TIER_REFERENCE, STATUS_REFERENCE, ACTION_KEEP
                f.tier_set_by_human = True
        self._refresh_preview()

    def counts(self) -> Counts:
        c = Counts(total=len(self.findings))
        for f in self.findings:
            if f.review_status == STATUS_PENDING:
                c.pending += 1
            elif f.review_status == STATUS_CONFIRMED:
                c.confirmed += 1
            elif f.review_status == STATUS_EXCLUDED:
                c.excluded += 1
            elif f.review_status == STATUS_REFERENCE:
                c.reference += 1
        return c

    def groups(self) -> list[ValueGroup]:
        """같은 값 묶어 보기: 참고가 아닌 탐지를 (유형, 값)으로 묶는다. 처음 나온 순서대로."""
        groups: dict[tuple[str, str], ValueGroup] = {}
        for f in self.findings:
            if is_reference(f):
                continue
            key = value_key(f.category, f.raw_value)
            groups.setdefault(key, ValueGroup(key, f.category, f.raw_value, [])).members.append(f)
        return list(groups.values())

    def code_preview(self, finding_id: str) -> str | None:
        return self._preview.get(finding_id)

    def _refresh_preview(self) -> None:
        self._preview = preview_codes(self.results, self.findings) if self.findings else {}

    def blind_spots(self) -> list[BlindSpot]:
        return [b for r in self.results for b in r.blind_spots]

    def step(self) -> int:
        """지금 단계 번호(STEPS 순서)."""
        if self.package_path:
            return STEPS.index("send")
        if not self.results:
            return STEPS.index("load")
        return STEPS.index("review") if self.counts().pending else STEPS.index("alias")

    def can_start(self) -> bool:
        return bool(self.results) and self.counts().pending == 0 and self.mission_id is None

    # ---------------------------------------------------------------- 작전 개시

    def start(self, record: ProcessingRecord) -> Path:
        if not self.can_start():
            raise RuntimeError("판정하지 않은 항목이 있거나 문서가 없습니다")
        previous = self.vault.missions()
        mission_id = self.vault.new_mission_id()
        try:
            book = (AliasBook.continue_from(mission_id, self.vault.load_mapping(previous[-1]))
                    if self.settings.alias_continue and previous else AliasBook(mission_id))
            docs = pseudonymize(self.results, self.findings, book)
            self.vault.save_mapping(mission_id, book.to_mapping())
            policy = "continued" if self.settings.alias_continue else "per_mission"
            self.originals = []
            self.package_path = build_package(self.settings, mission_id, docs, self.findings, book, self.vault,
                                              record=record, alias_policy=policy, sources=self.paths,
                                              reader=self._reader, originals=self.originals)
        except BaseException:
            # 송출이 멈추면(차단 목록 등) 번호와 대응표를 반납한다. 남겨 두면 복원 목록에 빈 작전이 생기고
            # 다시 개시할 때 번호가 -02로 넘어간다(2026-11-08)
            self.vault.discard(mission_id)
            raise
        self.mission_id = mission_id
        self.removed_categories = sorted({f.category for f in self.findings if f.replacement == "[삭제]"})
        return self.package_path

    def rules_text(self) -> str:
        return agent_rules_text(self.mission_id or "", self.removed_categories)

    # ---------------------------------------------------------------- 복원

    def results_waiting(self) -> list[ResultFile]:
        return find_results(self.settings)

    def missions(self) -> list[str]:
        return self.vault.missions()

    def restore(self, item: ResultFile, mission_id: str | None = None,
                accept: frozenset[str] = frozenset()) -> RestoredFile:
        return restore_result(self.settings, self.vault, item, mission_id, accept)

    def find_code(self, text: str) -> list[tuple[str, str, str]]:
        """코드명 하나로 모든 작전의 대응표를 찾는다. [(작전 번호, 코드명, 원문 값)], 최근 작전부터.
        작전마다 번호를 새로 매기므로 같은 코드명이 여러 작전에 있을 수 있다. 화면에만 보여 주고 어디에도 남기지 않는다.
        괄호를 빼거나 띄어 쓰거나 전각 괄호로 적거나 0을 빼고 적어도 찾는다(`이름-1`, `[ 이름 - 01 ]`, `［이름-01］`)."""
        want = code_key(text)
        if not want:
            return []
        found = []
        for mid in reversed(self.vault.missions()):
            for e in self.vault.load_mapping(mid).get("entries", []):
                if code_key(e.get("alias_code", "")) == want:
                    found.append((mid, e["alias_code"], e.get("raw_value", "")))
        log.info("코드명 찾기: %d건", len(found))  # 코드명과 값은 남기지 않는다(절대 규칙 3)
        return found


_CODE_PARTS = re.compile(r"\[?\s*([^\[\]\s-]+(?:\s+[^\[\]\s-]+)*)\s*-\s*(\d+)\s*\]?")


def code_key(text: str) -> tuple[str, int] | None:
    """코드명을 (유형, 번호)로. `[이름-01]`, `이름-1`, `[ 이름 - 01 ]`, `［이름-01］`이 모두 ('이름', 1). 코드명 모양이 아니면 None."""
    t = (text or "").strip().translate(str.maketrans("［］【】", "[][]"))
    m = _CODE_PARTS.fullmatch(t)
    return (m.group(1).replace(" ", ""), int(m.group(2))) if m else None
