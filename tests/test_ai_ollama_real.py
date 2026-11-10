"""실제 Ollama(127.0.0.1)로 시험한다. Ollama가 켜져 있고 모델이 받아져 있을 때만 돈다. 아니면 건너뛴다.

    모델 바꾸기: set PO_TEST_OLLAMA_MODEL=qwen3:4b (기본)
    건너뛰기:   set PO_SKIP_REAL_AI=1  (CPU에서 몇 분 걸린다)

문장과 샘플은 모두 지어낸 것이다. 실패 메시지에는 건수만 쓴다.
"""

from __future__ import annotations

import os

import pytest
from test_ai_modes import _run, baseline  # noqa: F401 - baseline은 fixture

from privacy_operator.detect import detect
from privacy_operator.detect.ai.indirect import find_indirect, locate
from privacy_operator.detect.ai.ollama_local import CLOUD_MODEL, LocalOllama, OllamaError, is_cloud_name
from privacy_operator.detect.dictionaries import INDIRECT
from privacy_operator.detect.finding import TIER_REFERENCE
from privacy_operator.reader.blocks import PARAGRAPH, Block, Location, ReadResult

MODEL = os.environ.get("PO_TEST_OLLAMA_MODEL", "qwen3:4b")


def _available() -> str | None:
    if os.environ.get("PO_SKIP_REAL_AI"):
        return "PO_SKIP_REAL_AI"
    try:
        names = [m.name for m in LocalOllama().models()]
    except OllamaError:
        return "Ollama가 켜져 있지 않습니다"
    return None if MODEL in names else f"모델 {MODEL}이 없습니다"


_reason = _available()
pytestmark = pytest.mark.skipif(_reason is not None, reason=_reason or "")

# 사람을 좁히는 문장(번호)과 평범한 문장
SENTENCES = [
    "제가 교육지원청 장학사로 있을 때 처음 이 연수를 맡았습니다.",  # 0
    "작년에 셋째 아이를 낳고 복직한 3학년 담임이 이번 연수를 신청했다.",  # 1
    "연수는 오전 9시에 시작하며 점심은 제공합니다.",  # 2 평범
    "우리 학교에서 유일한 남자 보건교사인 그분은 올해 정년을 앞두고 있다.",  # 3
    "참가자들은 모둠별로 수업 사례를 나누고 소감을 적었습니다.",  # 4 평범
    "작년 도 대회에서 금상을 받은 2학년 5반 담임교사가 사례를 발표했다.",  # 5
]
IDENTIFYING, PLAIN = {0, 1, 3, 5}, {2, 4}


def test_real_server_lists_only_local_models():
    models = LocalOllama().models()
    assert MODEL in [m.name for m in models]
    assert not any(is_cloud_name(m.name) for m in models)


def test_real_server_refuses_cloud_name():
    with pytest.raises(OllamaError) as e:
        LocalOllama().check_model("gemma4:cloud")
    assert e.value.code == CLOUD_MODEL


def test_real_model_finds_identifying_phrases():
    res = ReadResult("t.hwpx", "hwpx", [Block(f"b{i}", PARAGRAPH, t, Location(page=1, block_index=i))
                                       for i, t in enumerate(SENTENCES)])
    new, report = find_indirect([res], detect([res]), LocalOllama(), MODEL)
    assert report.failed_chunks == 0
    hit = {int(f.block_id[1:]) for f in new}
    assert len(hit & IDENTIFYING) >= 3, f"사람을 좁히는 문장 {len(IDENTIFYING)}개 중 {len(hit & IDENTIFYING)}개"
    assert not hit & PLAIN, f"평범한 문장에서 {len(hit & PLAIN)}건"
    for f in new:
        assert f.category == INDIRECT and f.tier == TIER_REFERENCE  # 사람이 판정하기 전에는 참고
        assert locate(SENTENCES[int(f.block_id[1:])], f.raw_value)  # 문서에 있는 글자 그대로


def test_real_mission_with_ollama(tmp_path, reader, monkeypatch, baseline):
    """실제 Ollama를 켜고 작전 한 번: 규칙 탐지는 꺼 둔 때와 같고, 확정한 AI 후보는 보낼 문서에 없다."""
    out = _run(tmp_path, reader, monkeypatch, model=MODEL, ai_verdict="confirm")
    assert out["errors"] == []
    assert out["rule"] == baseline["rule"]
    for f in out["confirmed_ai"]:
        assert all(f.raw_value not in t for t in out["docs"].values())
    assert out["restored"].result.complete