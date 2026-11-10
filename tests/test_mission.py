"""작전 패키지와 받은 결과함(5단계). 값은 모두 지어낸 것이다. 실패 메시지에는 파일 이름과 개수만 쓴다."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

import pytest
from answer_utils import SAMPLE_FILES
from test_alias import decide, doc, para, table

from privacy_operator.alias.codes import AliasBook
from privacy_operator.alias.pseudonymize import pseudonymize
from privacy_operator.alias.vault import Vault
from privacy_operator.detect import detect
from privacy_operator.detect.dictionaries import GRAY_CATEGORIES
from privacy_operator.detect.finding import ACTION_KEEP, STATUS_CONFIRMED
from privacy_operator.mission import NeedsMissionChoice, ProcessingRecord, build_package, find_results, restore_result
from privacy_operator.mission.package import PackageError, agent_rules_text
from privacy_operator.mission.results import CONFLICT, NO_MISSION, NOT_YET, READY, UNSUPPORTED
from privacy_operator.settings import Settings, SettingsError
from privacy_operator.writer.blocklist import BlocklistHit

PW = "mission-password-1"
NOW = datetime(2026, 11, 7, 11, 20, tzinfo=timezone.utc)


def _settings(root):
    return Settings(vault_dir=str(root / "금고"), workspace_dir=str(root / "작전실"))


def _secrets(findings, docs=()):
    vals = {f.raw_value for f in findings if f.review_status == STATUS_CONFIRMED and f.action != ACTION_KEEP
            and f.category not in GRAY_CATEGORIES}
    for d in docs:
        vals |= set(d.bundles.values())
    return vals


def _run(settings, vault, results, today=date(2026, 11, 7), record=None):
    findings = decide(detect(results))
    mid = vault.new_mission_id(today)
    book = AliasBook(mid)
    docs = pseudonymize(results, findings, book)
    vault.save_mapping(mid, book.to_mapping())
    root = build_package(settings, mid, docs, findings, book, vault, record=record, now=NOW)
    return mid, root, findings, docs, book


@pytest.fixture(scope="module")
def sample_mission(results, tmp_path_factory):
    root = tmp_path_factory.mktemp("pkg")
    settings = _settings(root)
    vault = Vault(settings.vault_path).create(PW)
    record = ProcessingRecord(purpose="연수 결과 보고서 초안", ai_service="채팅형 AI", retention_days=30)
    mid, pkg, findings, docs, book = _run(settings, vault, [results[n] for n in SAMPLE_FILES], record=record)
    return {"root": root, "settings": settings, "vault": vault, "mid": mid, "pkg": pkg,
            "findings": findings, "docs": docs, "book": book}


# ---------------------------------------------------------------- 보낼 문서함

def test_package_layout(sample_mission):
    pkg, mid = sample_mission["pkg"], sample_mission["mid"]
    assert pkg == sample_mission["settings"].to_ai_path / mid
    assert sorted(p.name for p in pkg.iterdir()) == ["AGENTS.md", "AGENT_RULES.md", "CLAUDE.md", "docs",
                                                    "mission.json"]
    # 같은 이름의 hwpx와 hwp는 형식으로 나눈다
    assert sorted(p.name for p in (pkg / "docs").iterdir()) == [
        "강사계약_샘플.md", "만족도조사_샘플.md", "연수운영계획_샘플.md", "연수운영계획_샘플_hwp.md"]
    assert (sample_mission["settings"].from_ai_path / mid).is_dir()


def test_mission_json_contents(sample_mission):
    pkg, mid = sample_mission["pkg"], sample_mission["mid"]
    m = json.loads((pkg / "mission.json").read_text(encoding="utf-8"))
    assert m["schema"] == "privacy-operator/mission@1" and m["mission_id"] == mid
    assert m["alias_policy"] == "per_mission" and m["return_to"] == f"from_ai/{mid}/"
    assert m["removed_categories"] == ["계좌번호", "주민등록번호"]
    for f in m["files"]:
        assert hashlib.sha256((pkg / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    assert {f["source_format"] for f in m["files"]} == {"hwpx", "hwp", "xlsx", "pdf"}
    legend = {x["alias_code"]: x["meaning"] for x in m["alias_legend"]}
    assert legend["[이름-01]"] == "1번 사람" and legend["[전화-01]"] == "전화 1"
    assert all(v.endswith("번 사람") for k, v in legend.items() if k.startswith("[이름-"))


def test_package_files_have_no_values(sample_mission):
    """완료 기준: mission.json과 AGENT_RULES.md 어디에도 원문 값이 없다(패키지 전체를 본다)."""
    secrets = _secrets(sample_mission["findings"], sample_mission["docs"])
    assert len(secrets) > 20
    leaks = {}
    for p in sample_mission["pkg"].rglob("*"):
        if p.is_file():
            n = sum(v in p.read_text(encoding="utf-8") for v in secrets)
            if n:
                leaks[p.name] = n
    assert not leaks, f"원문 값이 있는 패키지 파일(이름: 개수): {leaks}"


def test_vault_log_has_record_but_no_values(sample_mission):
    path = sample_mission["settings"].vault_path / sample_mission["mid"] / "log.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert (rec["purpose"], rec["ai_service"], rec["retention_days"]) == ("연수 결과 보고서 초안", "채팅형 AI", 30)
    assert rec["delete_after"] == "2026-12-07"
    text = path.read_text(encoding="utf-8")
    assert sum(v in text for v in _secrets(sample_mission["findings"])) == 0


def test_agent_rules_follow_template(sample_mission):
    rules = (sample_mission["pkg"] / "AGENT_RULES.md").read_text(encoding="utf-8")
    mid = sample_mission["mid"]
    assert rules.startswith("# 이 작전의 규칙")
    assert f"from_ai/{mid}/" in rules and f"{mid}_결과보고서.md" in rules
    assert "삭제된 정보(계좌번호, 주민등록번호)" in rules
    for name in ("CLAUDE.md", "AGENTS.md"):
        assert (sample_mission["pkg"] / name).read_text(encoding="utf-8") == rules


def test_rules_without_deletions_skip_that_rule():
    assert "삭제된 정보" not in agent_rules_text("M-20261107-01", [])


def test_file_name_with_a_name_is_renamed(tmp_path):
    settings = _settings(tmp_path)
    vault = Vault(settings.vault_path).create(PW)
    res = doc(table(["강사"], ["김민수"]), name="김민수_계약서.hwpx")
    mid, pkg, *_ = _run(settings, vault, [res])
    assert [p.name for p in (pkg / "docs").iterdir()] == ["[이름-01]_계약서.md"]
    assert "김민수" not in (pkg / "mission.json").read_text(encoding="utf-8")
    assert "김민수" not in (settings.vault_path / mid / "log.json").read_text(encoding="utf-8")


def test_package_is_not_made_when_a_value_remains(tmp_path):
    settings = _settings(tmp_path)
    vault = Vault(settings.vault_path).create(PW)
    res = doc(table(["성명"], ["김민수"]), para("본문 김민수", 3))
    findings = decide(detect([res]))
    findings.remove(next(f for f in findings if f.block_id == "b0003"))  # 본문 탐지를 놓쳤다고 치자
    mid = vault.new_mission_id(date(2026, 11, 7))
    book = AliasBook(mid)
    docs = pseudonymize([res], findings, book)
    with pytest.raises(BlocklistHit):
        build_package(settings, mid, docs, findings, book, vault, now=NOW)
    assert not (settings.to_ai_path / mid).exists()


def test_purpose_text_with_a_name_is_refused(tmp_path):
    settings = _settings(tmp_path)
    vault = Vault(settings.vault_path).create(PW)
    with pytest.raises(BlocklistHit):
        _run(settings, vault, [doc(table(["성명"], ["김민수"]))], record=ProcessingRecord(purpose="김민수 강사 보고"))


def test_existing_package_is_not_overwritten(tmp_path):
    settings = _settings(tmp_path)
    vault = Vault(settings.vault_path).create(PW)
    res = doc(table(["성명"], ["김민수"]))
    mid, pkg, findings, docs, book = _run(settings, vault, [res])
    with pytest.raises(PackageError):
        build_package(settings, mid, docs, findings, book, vault, now=NOW)


def test_restored_folder_cannot_be_in_workspace(tmp_path):
    s = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"),
                 restored_dir=str(tmp_path / "작전실" / "restored"))
    vault = Vault(s.vault_path).create(PW)
    with pytest.raises(SettingsError):
        _run(s, vault, [doc(table(["성명"], ["김민수"]))])


def test_default_restored_folder_is_next_to_vault(tmp_path):
    s = _settings(tmp_path)
    assert s.restored_path == tmp_path / "작전실복원"


# ---------------------------------------------------------------- 받은 결과함

def _two_missions(tmp_path):
    """같은 가명코드 [이름-01]이 작전마다 다른 사람인 상황."""
    settings = _settings(tmp_path)
    vault = Vault(settings.vault_path).create(PW)
    a, *_ = _run(settings, vault, [doc(table(["강사"], ["김민수"]), name="a.hwpx")])
    b, *_ = _run(settings, vault, [doc(table(["강사"], ["이도윤"]), name="b.hwpx")])
    return settings, vault, a, b


def test_result_is_restored_with_the_mapping_of_its_mission(tmp_path):
    """완료 기준: 받은 결과함에 넣은 파일이 작전 번호로 맞는 대응표와 연결되어 복원된다."""
    settings, vault, a, b = _two_missions(tmp_path)
    src = settings.from_ai_path / b / f"{b}_결과보고서.md"
    src.write_text("[이름-01]는 실습을 맡았다.", encoding="utf-8")
    before = src.read_bytes()

    [item] = find_results(settings)
    assert (item.mission_id, item.state) == (b, READY)
    restored = restore_result(settings, vault, item)
    assert restored.output == settings.restored_path / b / f"{b}_결과보고서.md"
    assert restored.output.read_text(encoding="utf-8") == "이도윤은 실습을 맡았다."
    assert restored.complete
    assert src.read_bytes() == before  # 받은 파일은 그대로


def test_mission_from_file_name_prefix(tmp_path):
    settings, vault, a, b = _two_missions(tmp_path)
    (settings.from_ai_path / f"{a}_요약.txt").write_text("[이름-01]과 회의", encoding="utf-8")
    [item] = find_results(settings)
    assert (item.mission_id, item.state) == (a, READY)
    assert restore_result(settings, vault, item).output.read_text(encoding="utf-8") == "김민수와 회의"


def test_no_mission_id_means_ask(tmp_path):
    settings, vault, a, b = _two_missions(tmp_path)
    (settings.from_ai_path / "결과.md").write_text("[이름-01]", encoding="utf-8")
    [item] = find_results(settings)
    assert (item.mission_id, item.state) == (None, NO_MISSION)
    with pytest.raises(NeedsMissionChoice):
        restore_result(settings, vault, item)
    assert restore_result(settings, vault, item, mission_id=a).output.read_text(encoding="utf-8") == "김민수"


def test_folder_and_name_disagree_means_ask(tmp_path):
    settings, vault, a, b = _two_missions(tmp_path)
    (settings.from_ai_path / a / f"{b}_결과.md").write_text("[이름-01]", encoding="utf-8")
    [item] = find_results(settings)
    assert (item.mission_id, item.state) == (None, CONFLICT)
    with pytest.raises(NeedsMissionChoice):
        restore_result(settings, vault, item)


def test_other_formats_are_listed_not_restored(tmp_path):
    settings, vault, a, b = _two_missions(tmp_path)
    for name in (f"{a}_결과.hwpx", f"{a}_결과.docx", f"{a}_결과.xlsx", f"{a}_결과.pdf", f"{a}_그림.png"):
        (settings.from_ai_path / a / name).write_bytes(b"x")
    states = {i.path.suffix: i.state for i in find_results(settings)}
    assert states == {".hwpx": READY, ".docx": READY, ".xlsx": READY, ".pdf": NOT_YET, ".png": UNSUPPORTED}


def test_restoring_twice_keeps_both_outputs(tmp_path):
    settings, vault, a, b = _two_missions(tmp_path)
    (settings.from_ai_path / a / f"{a}_결과.md").write_text("[이름-01]", encoding="utf-8")
    [item] = find_results(settings)
    first = restore_result(settings, vault, item).output
    second = restore_result(settings, vault, item).output
    assert first != second and first.exists() and second.exists()


def test_variants_in_result_are_reported(tmp_path):
    settings, vault, a, b = _two_missions(tmp_path)
    (settings.from_ai_path / a / f"{a}_결과.md").write_text("이름-01이 맡는다", encoding="utf-8")
    [item] = find_results(settings)
    first = restore_result(settings, vault, item)
    assert not first.complete and first.output.read_text(encoding="utf-8") == "이름-01이 맡는다"
    [v] = first.result.variants
    again = restore_result(settings, vault, item, accept=frozenset({v.variant_id}))
    assert again.output.read_text(encoding="utf-8") == "김민수가 맡는다" and again.complete


def test_package_roundtrip_through_folders(sample_mission):
    """보낼 문서함의 사본을 AI가 그대로 돌려줬다고 치면 원래 글로 돌아온다."""
    s, mid = sample_mission["settings"], sample_mission["mid"]
    sent = (sample_mission["pkg"] / "docs" / "만족도조사_샘플.md").read_text(encoding="utf-8")
    (s.from_ai_path / mid / f"{mid}_만족도조사_샘플.md").write_text(sent, encoding="utf-8")
    item = next(i for i in find_results(s) if i.path.name.startswith(mid))
    out = restore_result(s, sample_mission["vault"], item)
    text = out.output.read_text(encoding="utf-8")
    assert out.complete and "[응답자-" not in text and "[소속직급-01]" not in text
    assert sum(v in text for v in _secrets(sample_mission["findings"], sample_mission["docs"])) >= 10
