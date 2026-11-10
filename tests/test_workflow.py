"""작전 흐름(화면 없이): 불러오기 → 판정 → 작전 개시 → 복원. 값은 모두 지어낸 것이다."""

from __future__ import annotations

from datetime import date

import pytest
from answer_utils import SAMPLES

from privacy_operator.alias.vault import Vault
from privacy_operator.detect.finding import (
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    STATUS_REFERENCE,
    TIER_REFERENCE,
    TIER_REVIEW,
)
from privacy_operator.mission.package import ProcessingRecord
from privacy_operator.settings import Settings
from privacy_operator.workflow import STEPS, MissionWorkflow, is_reference

HWPX = SAMPLES / "연수운영계획_샘플.hwpx"
XLSX = SAMPLES / "만족도조사_샘플.xlsx"


@pytest.fixture
def wf(tmp_path, reader):
    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"))
    vault = Vault(settings.vault_path).create("workflow-password")
    w = MissionWorkflow(settings, vault, reader_factory=lambda: reader)  # 세션의 엔진을 같이 쓴다
    yield w
    w.close()


def _decide(wf):
    wf.exclude([f.finding_id for f in wf.findings if f.exclude_hint and f.review_status == STATUS_PENDING])
    while wf.counts().pending:  # 이름을 확정하면 옆 참고 항목이 올라올 수 있다
        wf.confirm([f.finding_id for f in wf.findings if f.review_status == STATUS_PENDING])


def test_starts_empty(wf):
    assert wf.counts().total == 0 and not wf.can_start() and wf.step() == STEPS.index("load")


def test_load_detect_and_counts(wf):
    failed = wf.add_files([HWPX, SAMPLES / "README.md"])
    assert [(p.name, code) for p, code in failed] == [("README.md", "UNSUPPORTED_FORMAT")]
    c = wf.counts()
    assert c.total == len(wf.findings) > 0 and c.pending > 0 and c.confirmed == c.excluded == 0
    assert all(f.review_status in (STATUS_PENDING, STATUS_REFERENCE) for f in wf.findings)
    assert wf.step() == STEPS.index("review") and not wf.can_start()


def test_same_file_twice_is_read_once(wf):
    wf.add_files([HWPX])
    n = len(wf.findings)
    wf.add_files([HWPX])
    assert len(wf.results) == 1 and len(wf.findings) == n


def test_verdicts_survive_adding_another_file(wf):
    wf.add_files([HWPX])
    name = next(f for f in wf.findings if f.category == "이름" and f.review_status == STATUS_PENDING)
    wf.confirm([name.finding_id])
    wf.add_files([XLSX])
    again = next(f for f in wf.findings if (f.filename, f.block_id, f.start) == (name.filename, name.block_id,
                                                                                 name.start))
    assert again.review_status == STATUS_CONFIRMED


def test_confirming_a_name_raises_its_neighbours(wf):
    wf.add_files([HWPX])
    # 사람이 회색 영역 하나를 참고로 내려도, 이름 확정 뒤에 다시 올리지 않는다
    gray = next(f for f in wf.findings if f.category == "소속" and f.tier == TIER_REVIEW)
    wf.lower_to_reference([gray.finding_id])
    assert is_reference(gray)
    wf.confirm([f.finding_id for f in wf.findings if f.category == "이름" and f.review_status == STATUS_PENDING])
    assert gray.tier == TIER_REFERENCE


def test_reference_items_need_raising_before_confirm(wf):
    wf.add_files([HWPX])
    ref = next(f for f in wf.findings if is_reference(f))
    wf.confirm([ref.finding_id])
    assert ref.review_status == STATUS_REFERENCE
    wf.raise_reference([ref.finding_id])
    assert (ref.tier, ref.review_status) == (TIER_REVIEW, STATUS_PENDING)
    wf.exclude([ref.finding_id])
    assert ref.review_status == STATUS_EXCLUDED


def test_code_preview_matches_real_codes(wf):
    wf.add_files([HWPX, XLSX])
    _decide(wf)
    preview = {f.finding_id: wf.code_preview(f.finding_id) for f in wf.findings}
    wf.start(ProcessingRecord("시험", "", 30))
    real = {f.finding_id: f.replacement for f in wf.findings}
    assert {k: v for k, v in preview.items() if v} == {k: v for k, v in real.items() if v}


def test_start_and_restore(wf):
    wf.add_files([HWPX])
    _decide(wf)
    assert wf.can_start() and wf.step() == STEPS.index("alias")
    package = wf.start(ProcessingRecord("연수 보고서", "채팅형 AI", 30))
    assert (package / "mission.json").is_file() and wf.mission_id and not wf.can_start()
    assert wf.step() == STEPS.index("send")
    assert wf.rules_text().startswith("# 이 작전의 규칙") and wf.mission_id in wf.rules_text()

    result = wf.settings.from_ai_path / wf.mission_id / f"{wf.mission_id}_결과.md"
    code = next(e["alias_code"] for e in wf.vault.load_mapping(wf.mission_id)["entries"] if e["raw_value"] == "김민수")
    result.write_text(f"{code}는 발표를 맡았다.", encoding="utf-8")
    [item] = wf.results_waiting()
    restored = wf.restore(item)
    assert restored.output.read_text(encoding="utf-8") == "김민수는 발표를 맡았다."
    assert wf.missions() == [wf.mission_id]


def test_adding_files_after_start_begins_a_new_mission(wf):
    wf.add_files([HWPX])
    _decide(wf)
    wf.start(ProcessingRecord())
    wf.add_files([XLSX])
    assert wf.mission_id is None and [p.name for p in wf.paths] == [XLSX.name]


def test_alias_continue_option(wf):
    wf.add_files([HWPX])
    _decide(wf)
    wf.start(ProcessingRecord())
    first = dict(wf.vault.load_mapping(wf.mission_id)["entries"][0])
    wf.settings.alias_continue = True
    wf.add_files([HWPX])
    _decide(wf)
    wf.start(ProcessingRecord())
    codes = {e["raw_value"]: e["alias_code"] for e in wf.vault.load_mapping(wf.mission_id)["entries"]}
    assert codes[first["raw_value"]] == first["alias_code"]
    assert wf.mission_id.startswith(f"M-{date.today():%Y%m%d}-")


def test_unjudge_returns_verdicts_to_pending(wf):
    """확정이나 제외를 대기로 되돌린다. 되돌리면 다시 판정해야 작전을 개시할 수 있다(자동 확정 없음)."""
    wf.add_files([HWPX])
    a, b = [f for f in wf.findings if not is_reference(f) and f.review_status == STATUS_PENDING][:2]
    wf.confirm([a.finding_id])
    wf.exclude([b.finding_id])
    wf.unjudge([a.finding_id, b.finding_id])
    assert a.review_status == b.review_status == STATUS_PENDING
    assert not wf.can_start()
    ref = next(f for f in wf.findings if is_reference(f))
    wf.unjudge([ref.finding_id])  # 참고 항목은 판정이 아니라 그대로
    assert ref.review_status == STATUS_REFERENCE


def test_find_code_looks_in_every_mission(wf, caplog):
    """코드명 하나로 모든 작전의 대응표를 찾는다. 작전마다 번호를 새로 매겨 같은 코드명이 여러 작전에 있을 수 있다.
    괄호, 띄어쓰기, 전각 괄호, 앞 0이 달라도 찾는다. 로그에는 값을 남기지 않는다. 값은 지어낸 것이다."""
    import logging
    from datetime import date

    from privacy_operator.workflow import code_key

    v = wf.vault
    a, b = v.new_mission_id(date(2026, 11, 7)), v.new_mission_id(date(2026, 11, 8))
    v.save_mapping(a, {"mission_id": a, "entries": [{"alias_code": "[학생-01]", "category": "이름", "raw_value": "석지안"}]})
    v.save_mapping(b, {"mission_id": b, "entries": [{"alias_code": "[학생-01]", "category": "이름", "raw_value": "도하늘"},
                                                     {"alias_code": "[연수생-001]", "category": "이름", "raw_value": "모은결"}]})
    caplog.set_level(logging.INFO)
    assert wf.find_code("[학생-01]") == [(b, "[학생-01]", "도하늘"), (a, "[학생-01]", "석지안")]  # 최근 작전부터
    for typed in ("학생-1", "[ 학생 - 01 ]", "［학생-01］"):
        assert [m for m, _, _ in wf.find_code(typed)] == [b, a], typed
    assert wf.find_code("연수생-1") == [(b, "[연수생-001]", "모은결")]
    assert wf.find_code("[학생-09]") == [] and wf.find_code("도하늘") == [] and code_key("아무 글") is None
    assert "도하늘" not in caplog.text and "석지안" not in caplog.text


def test_blocklist_stop_gathers_what_is_left(wf):
    """송출이 차단 목록에 걸려 멈추면: 작전 번호를 반납하고, 남은 자리(제외한 같은 값, 더 긴 낱말 속 값)를
    표에 모은다. 새로 찾은 자리는 '대기'로 들어오고(자동 확정 금지), 확정하면 다시 개시할 수 있다."""
    from dataclasses import replace

    from privacy_operator.detect.dictionaries import NAME
    from privacy_operator.writer.blocklist import BlocklistHit

    wf.add_files([HWPX])
    _decide(wf)
    name = next(f for f in wf.findings if f.category == NAME and f.review_status == STATUS_CONFIRMED)
    twin = next((f for f in wf.findings if f is not name and f.raw_value == name.raw_value
                 and f.review_status == STATUS_CONFIRMED), None)
    if twin:
        wf.exclude([twin.finding_id])  # 같은 값을 한 곳은 확정, 다른 곳은 제외
    blocks = wf.results[0].blocks
    k = max(i for i, b in enumerate(blocks) if b.kind == "paragraph")  # 재검색이 건너뛰는 낱말 속 값(본문 문단)
    blocks[k] = replace(blocks[k], text=blocks[k].text + " 이" + name.raw_value)
    record = ProcessingRecord("연수 보고서", "", 30)
    with pytest.raises(BlocklistHit) as e:
        wf.start(record)
    assert wf.vault.missions() == [] and wf.mission_id is None  # 번호와 대응표를 반납했다

    ids = wf.leftovers(e.value.finding_ids)
    left = wf.by_id(ids)
    assert name.finding_id in ids  # 걸린 값을 확정한 줄도 함께 보여 준다(오탐이면 이것을 제외해서 푼다)
    new = [f for f in left if f.review_status == STATUS_PENDING]
    assert new and all(f.raw_value == name.raw_value for f in new)
    if twin:
        assert twin.finding_id in ids
    assert not wf.can_start()  # 새로 찾은 자리를 판정해야 한다
    wf.confirm(ids)
    _decide(wf)
    package = wf.start(record)
    assert package.name.endswith("-01")  # 실패한 시도가 번호를 먹지 않았다
    assert name.raw_value not in "".join(p.read_text(encoding="utf-8") for p in package.rglob("*.md"))


def test_wrongly_confirmed_value_is_freed_by_excluding_every_row(wf):
    """오탐을 이름으로 확정한 뒤 재검색으로 올라온 줄만 제외하면 송출이 멈춘다. 멈춘 뒤 모은 줄을 모두 제외하면 개시된다."""
    from privacy_operator.detect.dictionaries import NAME
    from privacy_operator.writer.blocklist import BlocklistHit

    wf.add_files([HWPX])
    _decide(wf)
    name = next(f for f in wf.findings if f.category == NAME and f.review_status == STATUS_CONFIRMED)
    twins = [f.finding_id for f in wf.findings if f is not name and f.raw_value == name.raw_value]
    if not twins:
        pytest.skip("샘플에 같은 이름이 한 번뿐이다")
    wf.exclude(twins)  # 재검색으로 올라온 줄만 제외(원래 확정한 줄은 그대로)
    record = ProcessingRecord("연수 보고서", "", 30)
    with pytest.raises(BlocklistHit) as e:
        wf.start(record)
    ids = wf.leftovers(e.value.finding_ids)
    assert name.finding_id in ids and set(twins) <= set(ids)
    wf.exclude(ids)  # 모은 줄을 모두 제외
    _decide(wf)
    assert wf.start(record)


def test_reader_is_warmed_up_once_and_shared(tmp_path):
    """프로그램을 켤 때 읽기 엔진을 미리 켠다. 여러 번 불러도 하나만 만들고, 읽기가 그것을 쓴다."""
    import threading

    made = []

    class Fake:
        def __init__(self):
            made.append(self)

        def read(self, path):
            raise AssertionError("not used")

        def close(self):
            pass

    s = Settings(vault_dir=str(tmp_path / "v"), workspace_dir=str(tmp_path / "w"))
    wf = MissionWorkflow(s, Vault(s.vault_path).create("test-password"), reader_factory=Fake)
    threads = [threading.Thread(target=wf.warm_up) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(made) == 1 and wf._ensure_reader() is made[0]


def test_warm_up_failure_is_quiet(tmp_path):
    def broken():
        raise RuntimeError("no engine")

    s = Settings(vault_dir=str(tmp_path / "v"), workspace_dir=str(tmp_path / "w"))
    wf = MissionWorkflow(s, Vault(s.vault_path).create("test-password"), reader_factory=broken)
    wf.warm_up()  # 조용히 넘어간다. 문서를 읽을 때 다시 켜고 그때 알린다
    assert wf._reader is None
