"""9단계 AI 탐지(선택). 실제 Ollama와 Schift 없이 시험한다.

- Ollama: 테스트 안에서 127.0.0.1에 가짜 Ollama 서버를 띄워 실제 HTTP로 주고받는다.
- Schift: 같은 주고받기를 하는 가짜 작업 프로세스로 시험한다. 실제 모델 시험은 test_ai_schift_real.py.
문서 글은 모두 지어낸 값이다. 실패 메시지에 원문을 찍지 않는다(건수와 finding_id만).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from privacy_operator.alias.codes import AliasBook
from privacy_operator.alias.pseudonymize import pseudonymize
from privacy_operator.detect import detect, escalate
from privacy_operator.detect.ai import indirect
from privacy_operator.detect.ai.indirect import find_indirect, locate
from privacy_operator.detect.ai.ollama_local import (
    CLOUD_MODEL,
    MODEL_NOT_INSTALLED,
    NOT_RUNNING,
    LocalOllama,
    OllamaError,
    is_cloud,
    is_cloud_name,
)
from privacy_operator.detect.ai.schift import SchiftEngine, SchiftError, find_names
from privacy_operator.detect.ai.spans import is_ai
from privacy_operator.detect.dictionaries import ADDRESS, GRAY_CATEGORIES, INDIRECT, NAME
from privacy_operator.detect.finding import (
    BY_AI_INDIRECT,
    BY_AI_NER,
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    STATUS_REFERENCE,
    TIER_REFERENCE,
    TIER_REVIEW,
)
from privacy_operator.reader.blocks import PARAGRAPH, Block, Location, ReadResult
from privacy_operator.restore.restore import restore
from privacy_operator.writer.blocklist import blocklist_hits

# 지어낸 문서
CAREER = "제가 교육지원청 장학사로 있을 때 처음 이 연수를 맡았습니다."
FAMILY = "작년에 셋째 아이를 낳고 복직한 3학년 담임이 이번 연수를 신청했다."
AWARD = "김하늘 강사가 지난해 전국 수업나눔 대상을 받았다."
PLAIN = "연수는 오전 9시에 시작하며 점심은 제공합니다."

PHRASE_CAREER = "교육지원청 장학사로 있을 때"
PHRASE_FAMILY = "셋째 아이를 낳고 복직한 3학년 담임"
PHRASE_AWARD = "지난해 전국 수업나눔 대상을 받았다"


def doc(*texts: str, name: str = "연수계획.hwpx") -> ReadResult:
    blocks = [Block(f"b{i:04d}", PARAGRAPH, t, Location(page=1, block_index=i)) for i, t in enumerate(texts)]
    return ReadResult(name, "hwpx", blocks)


# ---------------------------------------------------------------- 가짜 Ollama

class FakeOllama:
    """요청을 기록하고, /api/chat에는 answer(블록 목록) 함수의 답을 준다."""

    def __init__(self):
        self.models = [{"name": "qwen3:4b", "model": "qwen3:4b", "size": 2_500_000_000,
                        "details": {"family": "qwen3", "parameter_size": "4.0B"}},
                       {"name": "gemma4:cloud", "model": "gemma4:cloud", "size": 0, "details": {}},
                       {"name": "gpt-oss:120b-cloud", "model": "gpt-oss:120b-cloud", "size": 0, "details": {}},
                       {"name": "sneaky:latest", "model": "sneaky:latest", "size": 0,
                        "remote_host": "https://example.invalid", "remote_model": "x", "details": {}}]
        self.show: dict[str, dict] = {}
        self.requests: list[tuple[str, str, dict | None]] = []
        self.answer = lambda blocks: {"candidates": []}
        self.chat_status = 200
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 테스트 출력에 요청 글을 찍지 않는다
                pass

            def _reply(self, status, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                fake.requests.append(("GET", self.path, None))
                if self.path == "/api/tags":
                    self._reply(200, {"models": fake.models})
                elif self.path == "/api/version":
                    self._reply(200, {"version": "0.13.0"})
                else:
                    self._reply(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                fake.requests.append(("POST", self.path, body))
                if self.path == "/api/show":
                    self._reply(200, fake.show.get(body["model"], {"details": {"family": "qwen3"}}))
                elif self.path == "/api/chat":
                    if fake.chat_status != 200:
                        self._reply(fake.chat_status, {"error": "보낸 글이 되돌아오는 오류 " + body["messages"][1]["content"]})
                        return
                    user = body["messages"][1]["content"]
                    blocks = {int(m.group(1)): m.group(2) for m in re.finditer(r"^\[(\d+)\] (.*)$", user, re.M)}
                    content = json.dumps(fake.answer(blocks), ensure_ascii=False)
                    self._reply(200, {"model": body["model"], "message": {"role": "assistant", "content": content},
                                      "done": True})
                else:
                    self._reply(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def chats(self) -> list[dict]:
        return [b for m, p, b in self.requests if p == "/api/chat"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    fake = FakeOllama()
    yield fake
    fake.close()


def answer_with(*phrases: str, extra=()):
    """블록에서 phrases를 찾아 후보로 돌려주는 가짜 모델."""

    def answer(blocks: dict[int, str]) -> dict:
        out = []
        for no, text in blocks.items():
            for p in phrases:
                if p in text:
                    out.append({"block": no, "text": p, "reason": "경력으로 사람을 좁힐 수 있음"})
        return {"candidates": out + list(extra)}

    return answer


# ---------------------------------------------------------------- 클라우드 모델 차단

@pytest.mark.parametrize("name", ["gemma4:cloud", "GLM-4.6:Cloud", "gpt-oss:120b-cloud", "qwen3-coder:480b-cloud",
                                  " deepseek-v3.1:671b-cloud "])
def test_cloud_names_are_detected(name):
    assert is_cloud_name(name)


@pytest.mark.parametrize("name", ["qwen3:4b", "gemma3:4b", "exaone3.5:7.8b", "llama3.2", "cloudy-model:7b"])
def test_local_names_are_not_cloud(name):
    assert not is_cloud_name(name)


def test_remote_fields_mark_cloud():
    assert is_cloud({"name": "x:latest", "remote_host": "https://example.invalid"})
    assert is_cloud({"name": "x:latest", "details": {"remote_model": "y"}})
    assert not is_cloud({"name": "x:latest", "details": {"family": "qwen3"}})


def test_model_list_hides_cloud_models(ollama):
    names = [m.name for m in LocalOllama(ollama.port).models()]
    assert names == ["qwen3:4b"]


def test_cloud_model_is_refused_before_any_chat(ollama):
    client = LocalOllama(ollama.port)
    for name in ("gemma4:cloud", "gpt-oss:120b-cloud", "sneaky:latest"):
        with pytest.raises(OllamaError) as e:
            client.chat_json(name, "s", "u", indirect.SCHEMA)
        assert e.value.code in (CLOUD_MODEL, MODEL_NOT_INSTALLED)
    assert not ollama.chats(), "클라우드 모델에 글을 보냈습니다"


def test_model_that_turns_out_remote_on_show_is_refused(ollama):
    ollama.show["qwen3:4b"] = {"remote_host": "https://example.invalid", "details": {}}
    with pytest.raises(OllamaError) as e:
        LocalOllama(ollama.port).chat_json("qwen3:4b", "s", "u", indirect.SCHEMA)
    assert e.value.code == CLOUD_MODEL
    assert not ollama.chats()


def test_model_not_installed_is_refused(ollama):
    with pytest.raises(OllamaError) as e:
        LocalOllama(ollama.port).chat_json("qwen3:8b", "s", "u", indirect.SCHEMA)
    assert e.value.code == MODEL_NOT_INSTALLED
    assert not ollama.chats()


def test_not_running(ollama):
    port = ollama.port
    ollama.close()
    with pytest.raises(OllamaError) as e:
        LocalOllama(port).models()
    assert e.value.code == NOT_RUNNING


def test_error_does_not_carry_response_text(ollama):
    ollama.chat_status = 500
    with pytest.raises(OllamaError) as e:
        LocalOllama(ollama.port).chat_json("qwen3:4b", "s", CAREER, indirect.SCHEMA)
    assert str(e.value) == "HTTP_500"
    assert "장학사" not in repr(e.value)


@pytest.mark.parametrize("port", [0, 70000, "11434", True, -1])
def test_bad_port_is_rejected(port):
    with pytest.raises(ValueError):
        LocalOllama(port)


def test_chat_request_shape(ollama):
    LocalOllama(ollama.port).chat_json("qwen3:4b", "s", "u", indirect.SCHEMA)
    [body] = ollama.chats()
    assert body["stream"] is False and body["format"] == indirect.SCHEMA
    assert body["options"]["temperature"] == 0


# ---------------------------------------------------------------- 간접식별 후보

def _run(ollama, res, findings=None, **kw):
    findings = detect([res]) if findings is None else findings
    new, report = find_indirect([res], findings, LocalOllama(ollama.port), "qwen3:4b", **kw)
    return findings, new, report


def test_candidates_start_as_reference_and_are_marked(ollama):
    ollama.answer = answer_with(PHRASE_FAMILY)
    _, new, report = _run(ollama, doc(PLAIN, FAMILY))
    assert len(new) == 1 and report.added == 1
    f = new[0]
    assert f.category == INDIRECT and f.tier == TIER_REFERENCE and f.review_status == STATUS_REFERENCE
    assert f.detected_by == (BY_AI_INDIRECT,) and is_ai(f) and f.finding_id == "D01-A0001"
    assert f.raw_value == PHRASE_FAMILY and f.note


def test_made_up_or_rewritten_candidates_are_dropped(ollama):
    ollama.answer = answer_with(extra=[{"block": 1, "text": "문서에 없는 문장입니다", "reason": "r"},
                                       {"block": 1, "text": "셋째 아이 낳고 복직", "reason": "r"},  # 고쳐 씀
                                       {"block": 9, "text": PHRASE_FAMILY, "reason": "r"},  # 없는 번호
                                       {"block": "1", "text": PHRASE_FAMILY, "reason": "r"}])
    _, new, report = _run(ollama, doc(FAMILY))
    assert not new and report.dropped == 4


def test_whitespace_difference_is_tolerated():
    text = "셋째 아이를  낳고\n복직한 3학년 담임"
    assert locate(text, PHRASE_FAMILY) == [(0, len(text))]
    assert locate(text, "아") == []  # 한 글자는 받지 않는다


def test_candidate_overlapping_a_name_is_dropped_and_rest_escalates(ollama):
    ollama.answer = answer_with("김하늘 강사가 지난해", PHRASE_AWARD)
    res = doc(AWARD)
    findings = detect([res])
    names = [f for f in findings if f.category == NAME]
    assert names, "규칙 탐지가 이름을 찾지 못했습니다"
    _, new, _ = _run(ollama, res, findings)
    assert [f.raw_value for f in new] == [PHRASE_AWARD]
    # 사람이 이름을 확정하면 같은 문장의 간접식별 후보는 '검토'로 오른다(AI 판단만으로는 오르지 않는다)
    assert new[0].tier == TIER_REFERENCE
    for f in names:
        f.review_status = STATUS_CONFIRMED
    escalate(findings + new)
    assert new[0].tier == TIER_REVIEW and new[0].review_status == STATUS_PENDING


def test_candidate_containing_one_gray_value_raises_nothing(ollama):
    """회색 영역 값 하나를 품은 후보는 그 값과 같은 글자다. 둘을 짝으로 세어 올리지 않는다(AI 판단만으로 오르지 않는다)."""
    res = doc(CAREER)
    findings = detect([res])
    gray = [f for f in findings if f.category in GRAY_CATEGORIES]
    assert len(gray) == 1, "시험 전제: 회색 영역 값이 하나"
    ollama.answer = answer_with(PHRASE_CAREER)
    _, new, _ = _run(ollama, res, findings)
    assert len(new) == 1 and new[0].start <= gray[0].start and gray[0].end <= new[0].end
    escalate(findings + new)
    assert gray[0].tier == TIER_REFERENCE and new[0].tier == TIER_REFERENCE
    assert gray[0].group_id is None and new[0].group_id is None


def test_candidate_with_a_separate_gray_value_is_a_pair(ollama):
    """다른 자리의 회색 영역 값과 같은 문장에 있으면 조합 규칙을 같은 방식으로 적용한다."""
    text = "40대 교사 한 분이 " + FAMILY
    res = doc(text)
    findings = detect([res])
    ollama.answer = answer_with(PHRASE_FAMILY)
    _, new, _ = _run(ollama, res, findings)
    others = [f for f in findings if f.category in GRAY_CATEGORIES
              and (f.end <= new[0].start or new[0].end <= f.start)]
    assert others, "시험 전제: 후보 밖에 회색 영역 값이 있다"
    escalate(findings + new)
    assert new[0].tier == TIER_REVIEW and all(f.tier == TIER_REVIEW for f in others)


def test_rule_findings_are_the_same_with_or_without_ai(ollama):
    """AI는 더하기만 한다. 규칙 탐지 결과(등급, 상태, 처리)는 AI를 써도 그대로다(이름이 확정되기 전)."""
    texts = (PLAIN, FAMILY, CAREER, AWARD, "담당자: 박서연 (010-2345-6789)")
    without = detect([doc(*texts)])
    escalate(without)
    res = doc(*texts)
    with_ai = detect([res])
    ollama.answer = answer_with(PHRASE_FAMILY, PHRASE_CAREER, PHRASE_AWARD)
    _, new, _ = _run(ollama, res, with_ai)
    assert new
    escalate(with_ai + new)

    def snap(fs):
        return [(f.finding_id, f.category, f.start, f.end, f.tier, f.review_status, f.action, f.group_id) for f in fs]

    assert snap(with_ai) == snap(without)


def test_existing_ai_candidate_is_not_duplicated(ollama):
    ollama.answer = answer_with(PHRASE_FAMILY)
    res = doc(FAMILY)
    findings, first, _ = _run(ollama, res)
    _, again, _ = _run(ollama, res, findings + first)
    assert not again


def test_cancel_stops_before_sending(ollama):
    _run(ollama, doc(FAMILY), cancelled=lambda: True)
    assert not ollama.chats()


def test_broken_answer_skips_chunk_only(ollama):
    ollama.answer = lambda blocks: {"wrong": 1}
    _, new, report = _run(ollama, doc(FAMILY))
    assert not new and report.chunks == 1 and report.candidates == 0


def test_short_blocks_and_metadata_are_not_sent():
    res = doc("짧다", FAMILY)
    res.blocks.append(Block("meta-author", "metadata", "작성자 이름이 여기에 들어감", Location(field="author")))
    [chunk] = indirect.chunks(res)
    assert [b.block_id for b in chunk] == ["b0001"]


# ---------------------------------------------------------------- 가명처리, 차단 목록, 복원 왕복

def test_confirmed_candidate_roundtrip(ollama):
    ollama.answer = answer_with(PHRASE_FAMILY)
    res = doc(PLAIN, FAMILY)
    findings, new, _ = _run(ollama, res)
    all_f = findings + new
    for f in all_f:
        f.review_status = STATUS_CONFIRMED if f in new else (STATUS_EXCLUDED if f.review_status == STATUS_PENDING
                                                              else f.review_status)
    new[0].action = "가명처리"
    book = AliasBook("M-20261107-01")
    [pdoc] = pseudonymize([res], all_f, book)
    text = "\n".join(b.text for b in pdoc.blocks)
    assert "[간접식별-01]" in text
    assert not blocklist_hits(text, all_f, pdoc.bundles)
    # 확정한 간접식별 후보는 차단 목록에 들어간다(낱개 회색 값과 다르다)
    assert blocklist_hits("\n".join(b.text for b in res.blocks), all_f, pdoc.bundles) == [new[0].finding_id]
    back = restore(text, book.to_mapping())
    assert back.text == "\n".join(b.text for b in res.blocks)


def test_bundle_with_ai_candidate_keeps_whole_phrase(ollama):
    """간접식별 후보 안에 든 회색 영역 값까지 모두 확정하면 후보 전체가 가명코드 하나가 된다."""
    res = doc(CAREER)
    findings = detect([res])
    ollama.answer = answer_with(PHRASE_CAREER)
    _, new, _ = _run(ollama, res, findings)
    all_f = findings + new
    escalate(all_f)
    for f in all_f:
        f.review_status = STATUS_CONFIRMED
        f.action = "가명처리"
    book = AliasBook("M-20261107-01")
    [pdoc] = pseudonymize([res], all_f, book)
    text = pdoc.blocks[0].text
    assert "[간접식별-01]" in text and "장학사" not in text
    assert restore(text, book.to_mapping()).text == CAREER


def test_containing_candidate_wins_when_bundle_is_partial(ollama):
    """묶음 일부만 확정해도 후보가 안의 회색 값을 덮는다(반쯤 바뀐 글이 남지 않는다)."""
    res = doc(CAREER)
    findings = detect([res])
    ollama.answer = answer_with(PHRASE_CAREER)
    _, new, _ = _run(ollama, res, findings)
    all_f = findings + new
    escalate(all_f)
    gray = [f for f in findings if f.category in GRAY_CATEGORIES]
    for f in all_f:
        f.review_status, f.action = STATUS_CONFIRMED, "가명처리"
    gray[0].review_status = STATUS_EXCLUDED  # 묶음이 깨진다
    book = AliasBook("M-20261107-01")
    [pdoc] = pseudonymize([res], all_f, book)
    text = pdoc.blocks[0].text
    assert "[간접식별-01]" in text and not blocklist_hits(text, all_f, pdoc.bundles)
    assert restore(text, book.to_mapping()).text == CAREER


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
def ai_wf(tmp_path, ollama):
    from privacy_operator.alias.vault import Vault
    from privacy_operator.settings import Settings
    from privacy_operator.workflow import MissionWorkflow

    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"),
                        ai_ollama_model="qwen3:4b", ollama_port=ollama.port)
    vault = Vault(settings.vault_path).create("workflow-password")
    w = MissionWorkflow(settings, vault, reader_factory=lambda: None)
    yield w
    w.close()


def _add(wf, tmp_path, *texts, name="연수계획.hwpx"):
    wf.add_results([(tmp_path / name, doc(*texts, name=name))])


def test_workflow_ai_off_by_default(tmp_path):
    from privacy_operator.settings import Settings

    s = Settings(vault_dir=str(tmp_path / "v"), workspace_dir=str(tmp_path / "w"))
    assert not s.ai_schift and not s.ai_ollama_model and s.ollama_port == 11434


def test_workflow_find_and_add_ai(ai_wf, ollama, tmp_path):
    ollama.answer = answer_with(PHRASE_FAMILY)
    _add(ai_wf, tmp_path, PLAIN, FAMILY)
    assert ai_wf.can_find_ai()
    run = ai_wf.find_ai()
    assert not run.errors and len(run.findings) == 1
    assert not any(is_ai(f) for f in ai_wf.findings), "find_ai는 작전 상태를 바꾸지 않습니다"
    added = ai_wf.add_ai(run)
    assert len(added) == 1 and added[0] in ai_wf.findings
    assert not ai_wf.can_find_ai()  # 이 문서는 마쳤다


def test_workflow_ai_error_keeps_doc_pending(ai_wf, ollama, tmp_path):
    _add(ai_wf, tmp_path, FAMILY)
    ollama.close()
    run = ai_wf.find_ai()
    assert run.errors == [NOT_RUNNING] and not run.findings
    ai_wf.add_ai(run)
    assert ai_wf.can_find_ai()  # 다시 해 볼 수 있다


def test_workflow_schift_not_installed(ai_wf, tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVACY_OPERATOR_SCHIFT_HOME", str(tmp_path / "없는곳"))
    ai_wf.settings.ai_schift, ai_wf.settings.ai_ollama_model = True, ""
    _add(ai_wf, tmp_path, FAMILY)
    assert ai_wf.find_ai().errors == ["SCHIFT_NOT_INSTALLED"]


def test_workflow_ai_findings_survive_new_files(ai_wf, ollama, tmp_path):
    ollama.answer = answer_with(PHRASE_FAMILY)
    _add(ai_wf, tmp_path, FAMILY)
    [f] = ai_wf.add_ai(ai_wf.find_ai())
    ai_wf.raise_reference([f.finding_id])
    ai_wf.confirm([f.finding_id])
    _add(ai_wf, tmp_path, PLAIN, name="두번째.hwpx")  # 다시 탐지한다
    [kept] = [g for g in ai_wf.findings if is_ai(g)]
    assert kept.finding_id == f.finding_id and kept.review_status == STATUS_CONFIRMED
    assert ai_wf.ai_pending_docs() == [1]  # 새 문서만 남았다


def test_workflow_package_with_ai_candidate_has_no_raw_phrase(ai_wf, ollama, tmp_path):
    from privacy_operator.mission.package import ProcessingRecord

    ollama.answer = answer_with(PHRASE_FAMILY)
    _add(ai_wf, tmp_path, PLAIN, FAMILY)
    [f] = ai_wf.add_ai(ai_wf.find_ai())
    ai_wf.raise_reference([f.finding_id])
    ai_wf.confirm([f.finding_id])
    ai_wf.exclude([g.finding_id for g in ai_wf.findings if g.review_status == STATUS_PENDING])
    package = ai_wf.start(ProcessingRecord("연수 결과 정리", "채팅형 AI", 30))
    texts = [p.read_text(encoding="utf-8") for p in package.rglob("*") if p.suffix in (".md", ".json")]
    assert any("[간접식별-01]" in t for t in texts)
    assert not any(PHRASE_FAMILY in t for t in texts)
