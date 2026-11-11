"""AI 탐지는 선택이다. 쓸 때와 안 쓸 때 모두 작전 한 번(불러오기 → 판정 → 개시 → 복원)이 끝까지 되는지 본다.

안 쓸 때: 꺼 둠(기본), Schift 켬인데 미설치.
쓸 때의 탐지와 판정은 test_ai_detect.py(가짜 작업 프로세스), test_ai_schift_real.py(실제 모델, 설치되어 있을 때만).

모든 경우에 규칙 탐지 결과와 보낼 문서는 꺼 둔 때와 같아야 한다. 값은 모두 지어낸 것이다(samples/).
"""

from __future__ import annotations

import pytest
from answer_utils import SAMPLES

from privacy_operator.alias.vault import Vault
from privacy_operator.detect.finding import STATUS_PENDING
from privacy_operator.mission.package import ProcessingRecord
from privacy_operator.settings import Settings
from privacy_operator.workflow import MissionWorkflow

FILES = [SAMPLES / "연수운영계획_샘플.hwpx", SAMPLES / "만족도조사_샘플.xlsx"]


def _run(tmp_path, reader, monkeypatch, *, schift=False):
    """작전 한 번: 불러오기, (켰으면) AI 탐지, 판정, 개시, 가명 문서를 결과함에 넣고 복원."""
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path / "schift-없음"))
    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"), ai_schift=schift)
    wf = MissionWorkflow(settings, Vault(settings.vault_path).create("modes-password"),
                         reader_factory=lambda: reader)
    try:
        wf.add_files(FILES)
        rule = [(f.finding_id, f.category, f.start, f.end, f.tier, f.review_status) for f in wf.findings]
        errors, added = [], []
        if wf.can_find_ai():
            run = wf.find_ai()
            errors = run.errors
            added = wf.add_ai(run)
        wf.exclude([f.finding_id for f in wf.findings if f.exclude_hint and f.review_status == STATUS_PENDING])
        while wf.counts().pending:
            wf.confirm([f.finding_id for f in wf.findings if f.review_status == STATUS_PENDING])
        package = wf.start(ProcessingRecord("연수 결과 정리", "채팅형 AI", 30))
        docs = {p.name: p.read_text(encoding="utf-8") for p in sorted((package / "docs").glob("*.md"))}
        # 받은 결과함에 가명 문서를 그대로 넣고 복원하면 원문 값이 돌아온다
        mid = wf.mission_id
        name = next(iter(docs))
        (wf.settings.from_ai_path / mid).mkdir(parents=True, exist_ok=True)
        (wf.settings.from_ai_path / mid / f"{mid}_결과.md").write_text(docs[name], encoding="utf-8")
        [item] = wf.results_waiting()
        restored = wf.restore(item)
        return {"rule": rule, "errors": errors, "added": added, "docs": docs, "restored": restored}
    finally:
        wf.close()


@pytest.fixture
def baseline(tmp_path_factory, reader):
    mp = pytest.MonkeyPatch()
    try:
        return _run(tmp_path_factory.mktemp("off"), reader, mp)
    finally:
        mp.undo()


def test_off_by_default_completes(baseline):
    """꺼 두면(기본) AI 탐지를 하지 않고 작전이 끝까지 된다."""
    assert baseline["errors"] == [] and baseline["added"] == []
    assert baseline["restored"].result.complete


def test_schift_on_but_not_installed_still_completes_like_off(tmp_path, reader, monkeypatch, baseline):
    out = _run(tmp_path, reader, monkeypatch, schift=True)
    assert out["errors"] == ["SCHIFT_NOT_INSTALLED"] and out["added"] == []
    assert out["rule"] == baseline["rule"], "AI를 못 써도 규칙 탐지는 그대로여야 한다"
    assert out["docs"] == baseline["docs"], "보낼 문서가 꺼 둔 때와 같아야 한다"
    assert out["restored"].result.complete
