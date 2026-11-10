"""실제 Schift 모델(engine/schift)로 시험한다. 설치되어 있지 않으면 건너뛴다(docs/AI탐지_검증기록.md).

작업 프로세스는 통신을 모두 막고 켜진다. 그래도 모델을 읽으면 오프라인 로딩이 되는 것이다.
문장은 모두 지어낸 것이다. 실패 메시지에는 건수만 쓴다.
"""

from __future__ import annotations

import time

import pytest

from privacy_operator.detect import detect
from privacy_operator.detect.ai.schift import SchiftEngine, find_names, is_installed
from privacy_operator.detect.dictionaries import NAME
from privacy_operator.detect.finding import STATUS_PENDING, TIER_REVIEW
from privacy_operator.reader.blocks import PARAGRAPH, Block, Location, ReadResult

pytestmark = pytest.mark.skipif(not is_installed(), reason="Schift가 설치되어 있지 않습니다")

# 규칙 탐지가 못 찾는 이름: 열 제목, 레이블, 역할어가 없다
UNLABELED = [
    ("어제 회의에서 윤서진과 한 시간 넘게 이야기했다.", "윤서진"),
    ("발표는 정하람, 질의응답은 오세빈이 맡기로 했다.", "정하람"),
    ("발표는 정하람, 질의응답은 오세빈이 맡기로 했다.", "오세빈"),
    ("보고서는 금요일까지 서은채에게 보내 주세요.", "서은채"),
]
PLAIN = ["연수는 오전 9시에 시작하며 점심은 제공합니다.", "교육과정 운영 계획과 이해 증진 방안을 논의했다.",
         "3층 대회의실에서 모둠별로 토의합니다."]


@pytest.fixture(scope="module")
def engine():
    t = time.perf_counter()
    with SchiftEngine() as e:
        e.load_seconds = time.perf_counter() - t
        yield e


def _doc(texts):
    return ReadResult("t.hwpx", "hwpx", [Block(f"b{i:04d}", PARAGRAPH, t, Location(page=1, block_index=i))
                                        for i, t in enumerate(texts)])


def test_loads_offline_in_reasonable_time(engine):
    assert engine.version and engine.load_seconds < 60


def test_finds_unlabeled_names_rules_miss(engine):
    texts = list(dict.fromkeys(t for t, _ in UNLABELED))
    res = _doc(texts)
    rule = detect([res])
    wanted = {name for _, name in UNLABELED}
    assert not wanted & {f.raw_value for f in rule if f.category == NAME}, "규칙이 이미 찾으면 시험이 헛돈다"
    new, _ = find_names([res], rule, engine)
    found = {f.raw_value for f in new if f.category == NAME}
    assert len(wanted & found) >= 3, f"단서 없는 이름 {len(wanted)}개 중 {len(wanted & found)}개만 찾음"
    assert all(f.tier == TIER_REVIEW and f.review_status == STATUS_PENDING for f in new)


def test_plain_sentences_have_no_names(engine):
    new, _ = find_names([_doc(PLAIN)], [], engine)
    assert not new, f"이름 없는 문장에서 {len(new)}건을 찾음"


def test_speed(engine):
    texts = [t for t, _ in UNLABELED] * 25
    t = time.perf_counter()
    engine.detect(texts)
    per_char = (time.perf_counter() - t) / sum(map(len, texts))
    assert per_char < 0.01, f"글자당 {per_char * 1000:.2f}ms"  # 10만 자에 1000초를 넘지 않게
