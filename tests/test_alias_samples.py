"""샘플 4종으로 가명처리 전체를 돌린다(3단계 완료 기준 4, 5).

읽기 → 탐지 → 판정(시험용 흉내) → 가명처리 → 마크다운 → 대응표 저장.
그다음 디스크의 모든 파일을 훑어 원문 값이 어디에 남았는지 본다. 실패 메시지에는 파일 이름과 개수만 쓴다.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import pytest
from answer_utils import SAMPLE_FILES
from test_alias import decide

from privacy_operator.alias.codes import AliasBook
from privacy_operator.alias.crypto import unseal
from privacy_operator.alias.pseudonymize import pseudonymize
from privacy_operator.detect import detect
from privacy_operator.detect.dictionaries import GRAY_CATEGORIES, normalize_header
from privacy_operator.detect.finding import ACTION_KEEP, STATUS_CONFIRMED
from privacy_operator.settings import Settings, VaultSession, save_settings
from privacy_operator.writer.blocklist import check_blocklist
from privacy_operator.writer.markdown import to_markdown

PW = "sample-mission-password"


def write_markdown(doc, out_dir, findings):
    """가명처리한 문서 하나를 마크다운으로 쓴다(차단 목록 검사 뒤). 작전 패키지 전체는 test_mission이 본다."""
    text = to_markdown(doc.blocks)
    check_blocklist(text, findings, doc.bundles)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (doc.source_name.rsplit(".", 1)[0] + ".md")
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


class MemoryStore:
    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v

    def delete(self, k):
        self.data.pop(k, None)


@pytest.fixture(scope="module")
def mission(results, tmp_path_factory):
    root = tmp_path_factory.mktemp("mission")
    import os
    os.environ["PRIVACY_OPERATOR_HOME"] = str(root / "config")
    settings = Settings(vault_dir=str(root / "금고"), workspace_dir=str(root / "작전실"), remember_password=True)
    save_settings(settings)
    session = VaultSession(settings, MemoryStore())
    unlocked = session.create(PW)

    docs_in = [results[n] for n in SAMPLE_FILES]
    findings = decide(detect(docs_in))
    mission_id = unlocked.new_mission_id(date(2026, 11, 7))
    book = AliasBook(mission_id)
    pdocs = pseudonymize(docs_in, findings, book)
    out = root / "작전실" / "to_ai" / mission_id / "docs"
    paths = [write_markdown(d, out, findings) for d in pdocs]
    unlocked.save_mapping(mission_id, book.to_mapping())
    yield {"root": root, "settings": settings, "findings": findings, "book": book, "paths": paths,
           "mission_id": mission_id, "unlocked": unlocked}
    os.environ.pop("PRIVACY_OPERATOR_HOME", None)


def _secret_values(findings, bundles=()):
    """대응표에만 있어야 하는 값: 확정되어 바꾼 것 중 회색 영역 낱개를 뺀 것, 그리고 묶음 원문."""
    vals = {f.raw_value for f in findings
            if f.review_status == STATUS_CONFIRMED and f.action != ACTION_KEEP and f.category not in GRAY_CATEGORIES}
    return vals | set(bundles)


def test_same_person_has_one_code_across_documents(mission):
    """완료 기준: 같은 사람 이름이 여러 번 나와도 같은 가명코드."""
    codes = {}
    for f in mission["findings"]:
        if f.category == "이름" and f.alias_code:
            codes.setdefault(f.raw_value, set()).add(f.alias_code)
    assert codes and all(len(c) == 1 for c in codes.values())
    md = mission["paths"][0].read_text(encoding="utf-8")  # 연수운영계획: 표, 본문, 메모에 같은 강사
    first = next(iter(codes[next(f.raw_value for f in mission["findings"]
                                 if f.filename.endswith(".hwpx") and f.category == "이름"
                                 and normalize_header(f.location.header or "").startswith("강사"))]))
    assert md.count(first) >= 2


def test_no_values_outside_mapping_file(mission):
    """완료 기준: 금고 밖에 원문 값이 남는 파일이 없고, 금고 안에서도 mapping.enc 외에는 없다."""
    bundles = {e.raw_value for e in mission["book"].entries() if e.category == "간접식별"}
    secrets = _secret_values(mission["findings"], bundles)
    assert len(secrets) > 20, "확정 값이 너무 적으면 검사가 헛돕니다"
    files = [p for p in mission["root"].rglob("*") if p.is_file()]
    assert {p.suffix for p in files} >= {".md", ".enc", ".json", ".check"}
    leaks = {}
    for p in files:
        data = p.read_bytes()
        n = sum(v.encode("utf-8") in data for v in secrets)
        if n:
            leaks[p.name] = n
    assert not leaks, f"원문 값이 남은 파일(이름: 개수): {leaks}"


def test_mapping_file_really_holds_the_values(mission):
    path = mission["settings"].vault_path / mission["mission_id"] / "mapping.enc"
    mapping = __import__("json").loads(unseal(path.read_bytes(), PW))
    raw = {e["raw_value"] for e in mapping["entries"]}
    aliased = {f.raw_value for f in mission["findings"] if f.alias_code and not f.group_id}
    deleted = {f.raw_value for f in mission["findings"] if f.replacement == "[삭제]"}
    # 값을 출력하지 않도록 개수로만 비교한다
    assert len(aliased - raw) == 0, f"대응표에 없는 가명코드 값 {len(aliased - raw)}개"
    assert deleted and len(deleted & raw) == 0, f"삭제했는데 대응표에 들어간 값 {len(deleted & raw)}개"
    assert mapping["mission_id"] == mission["mission_id"]


def test_markdown_copies_contain_only_codes(mission):
    for p in mission["paths"]:
        text = p.read_text(encoding="utf-8")
        assert re.search(r"\[[가-힣]+-\d{2,3}\]", text), p.name
    assert [p.name for p in mission["paths"]] == [
        "연수운영계획_샘플.md", "연수운영계획_샘플.md", "만족도조사_샘플.md", "강사계약_샘플.md"]


def test_logs_have_no_values(results, caplog, tmp_path):
    with caplog.at_level(logging.DEBUG, logger="privacy_operator"):
        docs_in = [results[n] for n in SAMPLE_FILES]
        findings = decide(detect(docs_in))
        book = AliasBook("M-20261107-09")
        pseudonymize(docs_in, findings, book)
    secrets = _secret_values(findings)
    assert caplog.records
    assert sum(v in caplog.text for v in secrets) == 0
