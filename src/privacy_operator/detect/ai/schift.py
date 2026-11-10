"""Schift 한국어 개인정보 탐지 모델로 이름과 주소를 더 찾는다(9단계, 선택).

모델: Hugging Face `schift-io/schift-ko-pii-v7`, 파이썬 패키지 `schift-ko-pii`(Schift License v2.0).
torch가 필요해 프로그램 본체와 다른 파이썬 환경(engine/schift/.venv)에 설치하고, 작업 프로세스
(schift_worker.py)로 띄워 표준 입출력으로 주고받는다. 설치는 docs/AI탐지_검증기록.md.

- 찾은 이름과 주소는 '검토' 등급, '대기' 상태로 들어온다(AI 판단만으로 '확실'이 되지 않는다).
- 규칙 탐지가 이미 찾은 자리와 겹치면 버린다.
- 작업 프로세스는 통신을 모두 막고 켜진다. 표준 오류는 버린다(라이브러리 경고에 글이 섞일 수 있다).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from privacy_operator.detect.ai.spans import AiSpan, to_findings
from privacy_operator.detect.dictionaries import ADDRESS, NAME
from privacy_operator.detect.finding import BY_AI_NER, TIER_REVIEW, Finding
from privacy_operator.reader.blocks import ReadResult

log = logging.getLogger(__name__)

WORKER = Path(__file__).resolve().with_name("schift_worker.py")
_ROOT = Path(__file__).resolve().parents[4]
CATEGORY = {"private_person": NAME, "private_address": ADDRESS}
BATCH = 40  # 한 번에 보내는 블록 수(진행 표시 단위)

# 오류 코드
NOT_INSTALLED = "SCHIFT_NOT_INSTALLED"
LOAD_FAILED = "SCHIFT_LOAD_FAILED"
WORKER_DIED = "SCHIFT_WORKER_DIED"


class SchiftError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def engine_home() -> Path:
    """설치 폴더. 시험이나 배포에서는 PRIVACY_OPERATOR_SCHIFT_HOME으로 바꾼다."""
    override = os.environ.get("PRIVACY_OPERATOR_SCHIFT_HOME")
    return Path(override) if override else _ROOT / "engine" / "schift"


def engine_python(home: Path | None = None) -> Path:
    home = home or engine_home()
    return home / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def is_installed(home: Path | None = None) -> bool:
    home = home or engine_home()
    return engine_python(home).is_file() and (home / "hf_home").is_dir()


@dataclass
class SchiftReport:
    blocks: int = 0
    spans: int = 0
    added: int = 0


class SchiftEngine:
    """작업 프로세스 하나. with 문으로 쓰거나 close()로 닫는다."""

    def __init__(self, home: Path | None = None, python: Path | None = None, worker: Path = WORKER):
        self.home = home or engine_home()
        self.python = python or engine_python(self.home)
        self.worker = worker
        self.proc: subprocess.Popen | None = None
        self.version = ""

    def __enter__(self) -> SchiftEngine:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        if self.proc is not None:
            return
        if not Path(self.python).is_file():
            raise SchiftError(NOT_INSTALLED)
        env = {k: v for k, v in os.environ.items() if not k.upper().endswith("_PROXY")}
        env.update({"HF_HOME": str(self.home / "hf_home"), "PYTHONIOENCODING": "utf-8"})
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen([str(self.python), "-I", str(self.worker)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                     cwd=str(self.home), creationflags=flags)
        first = self._read()
        if not first.get("ready"):
            kind = first.get("kind", "")
            self.close()
            log.warning("Schift 모델을 읽지 못함(%s)", kind)
            raise SchiftError(LOAD_FAILED)
        self.version = str(first.get("version", ""))

    def _read(self) -> dict:
        line = self.proc.stdout.readline() if self.proc else b""
        if not line:
            raise SchiftError(WORKER_DIED)
        try:
            return json.loads(line.decode("ascii"))
        except ValueError:
            raise SchiftError(WORKER_DIED) from None

    def detect(self, texts: list[str]) -> list[list[tuple[int, int, str, float]]]:
        """글마다 (시작, 끝, 이름표, 점수) 목록."""
        self.start()
        try:
            self.proc.stdin.write((json.dumps({"texts": texts}, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            raise SchiftError(WORKER_DIED) from None
        out = self._read()
        if "spans" not in out or len(out["spans"]) != len(texts):
            log.warning("Schift 탐지 실패(%s)", out.get("kind", ""))
            return [[] for _ in texts]
        return [[(int(s), int(e), str(label), float(score)) for s, e, label, score in row] for row in out["spans"]]

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        self.proc = None


def find_names(results: list[ReadResult], existing: list[Finding], engine: SchiftEngine,
               doc_indexes: list[int] | None = None, progress: Callable[[int, int], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> tuple[list[Finding], SchiftReport]:
    report = SchiftReport()
    todo = [(di, b) for di in (doc_indexes if doc_indexes is not None else range(len(results)))
            for b in results[di].blocks if b.text.strip()]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    spans: list[AiSpan] = []
    for n, batch in enumerate(batches, start=1):
        if cancelled and cancelled():
            break
        rows = engine.detect([b.text for _, b in batch])
        report.blocks += len(batch)
        for (di, b), row in zip(batch, rows):
            for s, e, label, _score in row:
                if label in CATEGORY:
                    spans.append(AiSpan(di, b.block_id, s, e, CATEGORY[label]))
        if progress:
            progress(n, len(batches))
    report.spans = len(spans)
    new = to_findings(results, existing, spans, BY_AI_NER, TIER_REVIEW)
    report.added = len(new)
    log.info("Schift: 블록 %d, 모델 탐지 %d, 새 탐지 %d", report.blocks, report.spans, report.added)
    return new, report
