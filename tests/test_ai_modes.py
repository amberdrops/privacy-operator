"""AI 탐지는 선택이다. 쓸 때와 안 쓸 때 모두 작전 한 번(불러오기 → 판정 → 개시 → 복원)이 끝까지 되는지 본다.

안 쓸 때: 꺼 둠(기본), Schift 미설치, Ollama 꺼짐, 모델 없음, 클라우드 모델을 적어 넣음.
쓸 때: 가짜 Ollama(항상), 실제 Ollama(test_ai_ollama_real.py, 켜져 있을 때만).

모든 경우에 규칙 탐지 결과는 꺼 둔 때와 같아야 한다. AI 후보를 제외하면 보낼 문서도 꺼 둔 때와 같아야 한다.
값은 모두 지어낸 것이다(samples/).
"""

from __future__ import annotations

import pytest
from answer_utils import SAMPLES
from test_ai_detect import FakeOllama, answer_with

from privacy_operator.alias.vault import Vault
from privacy_operator.detect.ai.spans import is_ai
from privacy_operator.detect.finding import STATUS_CONFIRMED, STATUS_PENDING
from privacy_operator.mission.package import ProcessingRecord
from privacy_operator.settings import Settings
from privacy_operator.workflow import MissionWorkflow

FILES = [SAMPLES / "연수운영계획_샘플.hwpx", SAMPLES / "만족도조사_샘플.xlsx"]
PHRASE = "예시교육지원청 회계팀 7급으로 있을 때"  # 샘플에 심어 둔 간접식별 문장의 일부


@pytest.fixture
def fake():
    f = FakeOllama()
    yield f
    f.close()


def _closed_port() -> int:
    f = FakeOllama()
    port = f.port
    f.close()
    return port


def _run(tmp_path, reader, monkeypatch, *, schift=False, model="", port=11434, ai_verdict=None):
    """작전 한 번. ai_verdict: AI 후보를 'confirm'(올려서 확정) 또는 'exclude'(그대로 둠)."""
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path / "schift-없음"))
    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"),
                        ai_schift=schift, ai_ollama_model=model, ollama_port=port)
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
        ai = [f for f in wf.findings if is_ai(f)]
        if ai_verdict == "confirm":
            wf.raise_reference([f.finding_id for f in ai])
            wf.confirm([f.finding_id for f in ai])
        elif ai_verdict == "exclude":  # 조합 승격으로 '검토'가 된 후보는 사람이 제외한다
            wf.exclude([f.finding_id for f in ai if f.review_status == STATUS_PENDING])
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
        return {"rule": rule, "errors": errors, "added": added, "docs": docs, "restored": restored,
                "confirmed_ai": [f for f in ai if f.review_status == STATUS_CONFIRMED]}
    finally:
        wf.close()


@pytest.fixture
def baseline(tmp_path_factory, reader):
    mp = pytest.MonkeyPatch()
    try:
        return _run(tmp_path_factory.mktemp("off"), reader, mp)
    finally:
        mp.undo()


# ---------------------------------------------------------------- 안 쓸 때

def test_off_never_contacts_ollama(tmp_path, reader, monkeypatch, fake):
    """꺼 두면(기본) 작전 내내 Ollama에 한 번도 접속하지 않는다."""
    out = _run(tmp_path, reader, monkeypatch, port=fake.port)
    assert fake.requests == []
    assert out["errors"] == [] and out["added"] == []
    assert out["restored"].result.complete


@pytest.mark.parametrize("mode, expected", [
    ("schift_missing", ["SCHIFT_NOT_INSTALLED"]),
    ("ollama_not_running", ["OLLAMA_NOT_RUNNING"]),
    ("model_missing", ["MODEL_NOT_INSTALLED"]),
    ("cloud_model", ["CLOUD_MODEL"]),
])
def test_ai_unavailable_still_completes_like_off(tmp_path, reader, monkeypatch, fake, baseline, mode, expected):
    kw = {"schift_missing": dict(schift=True),
          "ollama_not_running": dict(model="qwen3:4b", port=_closed_port()),
          "model_missing": dict(model="llama3.2:3b", port=fake.port),
          "cloud_model": dict(model="gemma4:cloud", port=fake.port)}[mode]
    out = _run(tmp_path, reader, monkeypatch, **kw)
    assert out["errors"] == expected and out["added"] == []
    assert out["rule"] == baseline["rule"], "AI를 못 써도 규칙 탐지는 그대로여야 한다"
    assert out["docs"] == baseline["docs"], "보낼 문서가 꺼 둔 때와 같아야 한다"
    assert out["restored"].result.complete
    assert not fake.chats(), "글을 보내지 않았어야 한다"


# ---------------------------------------------------------------- 쓸 때(가짜 Ollama)

def test_on_with_candidate_excluded_matches_off(tmp_path, reader, monkeypatch, fake, baseline):
    """AI가 후보를 냈어도 사람이 제외하면 보낼 문서는 꺼 둔 때와 같다.
    이 후보는 규칙만으로 된 '소속+직급' 묶음을 감싸므로 같은 묶음에 들어가 '검토'가 된다(규칙 근거)."""
    fake.answer = answer_with(PHRASE)
    out = _run(tmp_path, reader, monkeypatch, model="qwen3:4b", port=fake.port, ai_verdict="exclude")
    assert out["errors"] == [] and len(out["added"]) == 1
    assert out["rule"] == baseline["rule"]
    assert out["docs"] == baseline["docs"]
    assert fake.chats(), "켜 두면 Ollama에 글을 보낸다(127.0.0.1)"


def test_on_with_candidate_confirmed(tmp_path, reader, monkeypatch, fake, baseline):
    """AI 후보를 확정하면 그 구간만 [간접식별-01]로 바뀌고, 나머지는 꺼 둔 때와 같다. 복원하면 돌아온다."""
    fake.answer = answer_with(PHRASE)
    out = _run(tmp_path, reader, monkeypatch, model="qwen3:4b", port=fake.port, ai_verdict="confirm")
    assert out["rule"] == baseline["rule"]
    [f] = out["confirmed_ai"]
    changed = [n for n in out["docs"] if out["docs"][n] != baseline["docs"][n]]
    assert len(changed) == 1
    # 꺼 둔 때는 규칙 묶음 '소속+직급'만 [소속직급-01]이었다. 켜고 확정하면 '…으로 있을 때'까지 하나가 된다
    expected = baseline["docs"][changed[0]].replace("[소속직급-01]으로 있을 때", "[간접식별-01]")
    assert out["docs"][changed[0]] == expected, "바뀐 곳은 AI 후보 자리 하나뿐이어야 한다"
    assert all(PHRASE not in t for t in out["docs"].values())
    assert out["restored"].result.complete
