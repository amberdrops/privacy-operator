"""9단계 AI 탐지(선택). 실제 Schift 없이 시험한다.

- Schift: 같은 주고받기를 하는 가짜 작업 프로세스로 시험한다. 실제 모델 시험은 test_ai_schift_real.py.
문서 글은 모두 지어낸 값이다. 실패 메시지에 원문을 찍지 않는다(건수와 finding_id만).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from privacy_operator.detect import detect
from privacy_operator.detect.ai.schift import SchiftEngine, SchiftError, find_names
from privacy_operator.detect.ai.spans import is_ai
from privacy_operator.detect.dictionaries import ADDRESS, NAME
from privacy_operator.detect.finding import BY_AI_NER, STATUS_PENDING, TIER_REVIEW
from privacy_operator.reader.blocks import PARAGRAPH, Block, Location, ReadResult

# 지어낸 문서
FAMILY = "작년에 셋째 아이를 낳고 복직한 3학년 담임이 이번 연수를 신청했다."
AWARD = "김하늘 강사가 지난해 전국 수업나눔 대상을 받았다."
PLAIN = "연수는 오전 9시에 시작하며 점심은 제공합니다."



def doc(*texts: str, name: str = "연수계획.hwpx") -> ReadResult:
    blocks = [Block(f"b{i:04d}", PARAGRAPH, t, Location(page=1, block_index=i)) for i, t in enumerate(texts)]
    return ReadResult(name, "hwpx", blocks)


# ---------------------------------------------------------------- Schift (가짜 작업 프로세스)

FAKE_WORKER = textwrap.dedent("""
    import json, sys
    out = sys.stdout.buffer
    def send(o):
        out.write((json.dumps(o) + "\\n").encode("ascii")); out.flush()
    MODE = {mode!r}
    if MODE == "fail":
        send({{"error": "LOAD_FAILED", "kind": "OSError"}}); sys.exit(2)
    send({{"ready": True, "version": "0.6.0"}})
    for raw in sys.stdin.buffer:
        texts = json.loads(raw.decode("utf-8"))["texts"]
        rows = []
        for t in texts:
            row = []
            for word, label in (("박소윤", "private_person"), ("김하늘", "private_person"),
                                ("한빛로 12", "private_address"), ("연수는", "private_url")):
                i = t.find(word)
                if i >= 0:
                    row.append([i, i + len(word), label, 0.9])
            rows.append(row)
        send({{"spans": rows}})
""")


@pytest.fixture
def fake_schift(tmp_path):
    def make(mode="ok"):
        worker = tmp_path / f"worker_{mode}.py"
        worker.write_text(FAKE_WORKER.format(mode=mode), encoding="utf-8")
        return SchiftEngine(home=tmp_path, python=sys.executable, worker=worker)
    return make


def test_schift_names_and_addresses_start_pending(fake_schift):
    res = doc("이번 연수의 숨은 공로자는 박소윤이다.", "강의실은 한빛로 12 3층이다.", AWARD)
    findings = detect([res])
    with fake_schift() as engine:
        new, report = find_names([res], findings, engine)
    got = sorted((f.category, f.raw_value) for f in new)
    assert got == sorted([(ADDRESS, "한빛로 12"), (NAME, "박소윤")])  # 김하늘은 규칙이 이미 찾았다, url은 받지 않는다
    for f in new:
        assert f.tier == TIER_REVIEW and f.review_status == STATUS_PENDING and f.detected_by == (BY_AI_NER,)
        assert is_ai(f)
    assert report.added == 2


def test_schift_load_failure_is_a_code(fake_schift):
    with pytest.raises(SchiftError) as e:
        fake_schift("fail").start()
    assert e.value.code == "SCHIFT_LOAD_FAILED"


def test_schift_not_installed(tmp_path):
    with pytest.raises(SchiftError) as e:
        SchiftEngine(home=tmp_path).start()
    assert e.value.code == "SCHIFT_NOT_INSTALLED"


def test_ai_ids_survive_rule_rescan():
    """AI 탐지 번호(A0001)가 있어도 확정값 재검색 번호 매기기가 깨지지 않는다."""
    from privacy_operator.detect import rescan

    res = doc(AWARD, "다음 시간에도 김하늘 강사가 맡는다.")
    findings = detect([res])
    src = [f for f in findings if f.category == NAME][:1]
    findings[0].finding_id = "D01-A0007"
    rescan([res], findings, src)  # 예외 없이


# ---------------------------------------------------------------- 통신 차단 장치

GUARD = textwrap.dedent("""
    import socket, sys
    from privacy_operator import netguard
    netguard.install(loopback={loopback})
    results = []
    for host in ("8.8.8.8", "example.invalid", "127.0.0.1"):
        try:
            socket.create_connection((host, 9), timeout=0.5)
            results.append("open")
        except netguard.NetworkBlocked:
            results.append("blocked")
        except OSError:
            results.append("allowed")  # 막지 않았고 접속만 실패
    print(",".join(results))
""")


@pytest.mark.parametrize("loopback, expected", [(True, "blocked,blocked,allowed"),
                                                (False, "blocked,blocked,blocked")])
def test_netguard_blocks_outside_connections(loopback, expected):
    out = subprocess.run([sys.executable, "-c", GUARD.format(loopback=loopback)], capture_output=True, text=True,
                         timeout=30)
    assert out.stdout.strip() == expected, out.stderr[-500:]


def test_schift_worker_blocks_network(tmp_path):
    """작업 프로세스는 모델을 읽기 전에 통신을 막는다. 통신하려는 가짜 schift_ko_pii로 시험한다."""
    from privacy_operator.detect.ai.schift import WORKER

    pkg = tmp_path / "schift_ko_pii"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent("""
        import socket
        def detect(text, **kw):
            socket.create_connection(("8.8.8.8", 443), timeout=1)
            return []
    """), encoding="utf-8")
    env = {"PYTHONPATH": str(tmp_path), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
    out = subprocess.run([sys.executable, str(WORKER)], input=b"", capture_output=True, env=env, timeout=60)
    first = json.loads(out.stdout.decode("ascii").splitlines()[0])
    assert first == {"error": "LOAD_FAILED", "kind": "RuntimeError"}


# ---------------------------------------------------------------- 작전 흐름에 붙이기

@pytest.fixture
def ai_wf(tmp_path):
    from privacy_operator.alias.vault import Vault
    from privacy_operator.settings import Settings
    from privacy_operator.workflow import MissionWorkflow

    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"))
    vault = Vault(settings.vault_path).create("workflow-password")
    w = MissionWorkflow(settings, vault, reader_factory=lambda: None)
    yield w
    w.close()


def _add(wf, tmp_path, *texts, name="연수계획.hwpx"):
    wf.add_results([(tmp_path / name, doc(*texts, name=name))])


def test_workflow_ai_off_by_default(tmp_path):
    from privacy_operator.settings import Settings

    s = Settings(vault_dir=str(tmp_path / "v"), workspace_dir=str(tmp_path / "w"))
    assert not s.ai_schift


def test_workflow_schift_not_installed(ai_wf, tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path / "없는곳"))
    ai_wf.settings.ai_schift = True
    _add(ai_wf, tmp_path, FAMILY)
    assert ai_wf.can_find_ai()
    run = ai_wf.find_ai()
    assert run.errors == ["SCHIFT_NOT_INSTALLED"] and not run.findings
    ai_wf.add_ai(run)
    assert ai_wf.can_find_ai()  # 다시 해 볼 수 있다


def test_workflow_find_and_add_ai_with_fake_worker(ai_wf, fake_schift, tmp_path, monkeypatch):
    from privacy_operator import workflow

    monkeypatch.setattr(workflow, "is_installed", lambda: True)
    monkeypatch.setattr(workflow, "SchiftEngine", fake_schift)
    ai_wf.settings.ai_schift = True
    _add(ai_wf, tmp_path, PLAIN, "이번 연수의 숨은 공로자는 박소윤이다.")
    run = ai_wf.find_ai()
    assert not run.errors and len(run.findings) == 1
    assert not any(is_ai(f) for f in ai_wf.findings), "find_ai는 작전 상태를 바꾸지 않습니다"
    [f] = ai_wf.add_ai(run)
    assert f in ai_wf.findings and f.review_status == STATUS_PENDING
    assert not ai_wf.can_find_ai()  # 이 문서는 마쳤다
    _add(ai_wf, tmp_path, PLAIN, name="두번째.hwpx")  # 다시 탐지해도 AI 탐지는 남는다
    assert [g.finding_id for g in ai_wf.findings if is_ai(g)] == [f.finding_id]
    assert ai_wf.ai_pending_docs() == [1]  # 새 문서만 남았다
