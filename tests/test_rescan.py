"""확정값 재검색과 같은 값 묶기(7단계). 엔진 없이 지어낸 블록으로 시험한다."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_alias import doc, para, table

from privacy_operator.alias.vault import Vault
from privacy_operator.detect import detect, rescan
from privacy_operator.detect.finding import (
    BY_CONFIRMED,
    STATUS_CONFIRMED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    TIER_REVIEW,
)
from privacy_operator.mission.package import ProcessingRecord
from privacy_operator.settings import Settings
from privacy_operator.workflow import MissionWorkflow
from privacy_operator.writer.blocklist import blocklist_hits


def _confirm(findings, **where):
    hit = [f for f in findings if all(getattr(f, k) == v for k, v in where.items())]
    for f in hit:
        f.review_status = STATUS_CONFIRMED
    return hit


def test_rescan_finds_confirmed_name_in_other_documents():
    a = doc(para("오전은 김민수 강사가 맡는다"), name="a.hwpx")  # 역할어로 찾은 '검토' 이름
    b = doc(para("김민수는 오후에 결석했다"), name="b.pdf")  # 역할어도 표도 없어 탐지되지 않음
    found = detect([a, b])
    assert [f.file_id for f in found] == ["D01"]
    src = _confirm(found, raw_value="김민수")
    new = rescan([a, b], found, src)
    assert [(f.file_id, f.raw_value, f.start) for f in new] == [("D02", "김민수", 0)]
    f = new[0]
    assert (f.tier, f.review_status, f.detected_by) == (TIER_REVIEW, STATUS_PENDING, (BY_CONFIRMED,))
    assert f.role_hint == src[0].role_hint and f.context
    assert f.finding_id not in {x.finding_id for x in found}


def test_rescan_skips_places_already_found_but_not_longer_words():
    """이미 찾은 자리는 건너뛴다. 더 긴 낱말 속 값('가정하람' 속 '정하람')은 차단 목록에 걸릴 자리라 대기로 보여 준다."""
    a = doc(table(["강사"], ["정하람"]), para("정하람이 진행, 가정하람은 다른 말", 5))
    found = detect([a])
    src = _confirm(found, raw_value="정하람")
    new = rescan([a], found, src)  # 본문 첫 '정하람'은 확산으로 이미 찾았다
    assert [(f.raw_value, f.start, f.review_status) for f in new] == [("정하람", 10, STATUS_PENDING)]


def test_rescan_matches_reformatted_numbers():
    a = doc(table(["성명", "연락처"], ["김민수", "010-0000-0012"]), para("다시 연락: 010 0000 0012", 5))
    found = detect([a])
    body = [f for f in found if f.block_id == "b0005"]
    found = [f for f in found if f not in body]  # 본문 번호를 놓쳤다고 치자
    src = _confirm(found, category="전화")
    [new] = rescan([a], found, src)
    assert new.raw_value == "010 0000 0012"


def test_gray_values_are_not_rescanned():
    a = doc(table(["성명", "소속"], ["김민수", "예시학교"]), para("예시학교 행사 안내", 5))
    found = detect([a])
    src = _confirm(found, category="소속")
    assert rescan([a], found, src) == []


# ---------------------------------------------------------------- 작전 흐름에서

@pytest.fixture
def wf(tmp_path):
    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"))
    return MissionWorkflow(settings, Vault(settings.vault_path).create("rescan-password"),
                           reader_factory=lambda: None)


def _add(wf, *docs):
    wf.add_results([(Path(d.source_name), d) for d in docs])


def test_confirm_adds_pending_places_and_never_confirms_them(wf):
    _add(wf, doc(para("오전은 김민수 강사가 맡는다"), name="a.hwpx"), doc(para("김민수는 결석"), name="b.pdf"))
    [name] = [f for f in wf.findings if f.raw_value == "김민수"]
    added = wf.confirm([name.finding_id])
    assert len(added) == 1 and added[0].review_status == STATUS_PENDING
    assert wf.counts().pending == 1 and not wf.can_start()


def test_excluded_values_are_not_rescanned(wf):
    _add(wf, doc(para("오전은 김민수 강사가 맡는다"), name="a.hwpx"), doc(para("김민수는 결석"), name="b.pdf"))
    [name] = [f for f in wf.findings if f.raw_value == "김민수"]
    wf.exclude([name.finding_id])
    assert len(wf.findings) == 1


def test_rescanned_places_and_verdicts_survive_adding_files(wf):
    _add(wf, doc(para("오전은 김민수 강사가 맡는다"), name="a.hwpx"), doc(para("김민수는 결석"), name="b.pdf"))
    [name] = [f for f in wf.findings if f.raw_value == "김민수"]
    [extra] = wf.confirm([name.finding_id])
    wf.exclude([extra.finding_id])
    _add(wf, doc(para("새 문서"), name="c.pdf"))
    again = [f for f in wf.findings if f.filename == "b.pdf"]
    assert [(f.raw_value, f.review_status) for f in again] == [("김민수", STATUS_EXCLUDED)]


def test_rescan_lets_a_mission_start_that_the_blocklist_would_stop(wf):
    """재검색이 없으면 놓친 곳 때문에 차단 목록에서 멈춘다(3단계 시험 참고). 재검색이 그곳을 미리 올린다."""
    _add(wf, doc(para("오전은 김민수 강사가 맡는다"), para("보고: 김민수 참석", 1), name="a.hwpx"))
    while wf.counts().pending:
        wf.confirm([f.finding_id for f in wf.findings if f.review_status == STATUS_PENDING])
    assert wf.can_start()
    package = wf.start(ProcessingRecord())
    text = next((package / "docs").iterdir()).read_text(encoding="utf-8")
    assert "김민수" not in text and blocklist_hits(text, wf.findings) == []


def test_groups_collect_same_value(wf):
    _add(wf, doc(table(["성명", "연락처"], ["김민수", "010-0000-0012"]),
                 para("김민수 강사에게 010 0000 0012로 연락", 5), name="a.hwpx"))
    groups = {(g.category, g.value): g for g in wf.groups()}
    assert len(groups[("이름", "김민수")].members) == 2
    phone = next(g for g in wf.groups() if g.category == "전화")
    assert len(phone.members) == 2  # 구분자가 달라도 같은 번호
    assert phone.status == STATUS_PENDING and phone.files == ["a.hwpx"]
    wf.confirm([phone.ids[0]])
    assert phone.status is None  # 섞임
