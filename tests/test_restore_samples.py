"""샘플 4종 왕복: 원문 → 가명처리 → (암호 대응표 저장, 다시 열기) → 복원 = 원문.

삭제한 값(주민등록번호, 계좌번호)은 복원되지 않는 것이 맞으므로 기대값에도 `[삭제]`로 둔다.
실패 메시지에는 문서 이름과 줄 번호만 쓴다.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest
from answer_utils import SAMPLE_FILES
from test_alias import decide

from privacy_operator.alias.codes import AliasBook
from privacy_operator.alias.pseudonymize import DELETED, pseudonymize
from privacy_operator.alias.vault import Vault
from privacy_operator.detect import detect
from privacy_operator.restore import restore
from privacy_operator.writer.markdown import to_markdown


def _expected_blocks(res, findings, file_id):
    """원문 블록에 삭제만 반영한 것."""
    dels = {}
    for f in findings:
        if f.file_id == file_id and f.replacement == DELETED:
            dels.setdefault(f.block_id, []).append(f)
    out = []
    for b in res.blocks:
        text = b.text
        for f in sorted(dels.get(b.block_id, []), key=lambda f: -f.start):
            text = text[:f.start] + DELETED + text[f.end:]
        out.append(dataclasses.replace(b, text=text))
    return out


def _first_diff_line(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a.splitlines(), b.splitlines()), start=1):
        if x != y:
            return i
    return min(len(a.splitlines()), len(b.splitlines())) + 1


@pytest.fixture(scope="module")
def roundtrip(results, tmp_path_factory):
    docs_in = [results[n] for n in SAMPLE_FILES]
    findings = decide(detect(docs_in))
    vault = Vault(tmp_path_factory.mktemp("rt") / "vault").create("roundtrip-password")
    mid = vault.new_mission_id(date(2026, 11, 7))
    book = AliasBook(mid)
    pdocs = pseudonymize(docs_in, findings, book)
    vault.save_mapping(mid, book.to_mapping())
    mapping = vault.load_mapping(mid)  # 암호 파일을 거쳐 다시 연 대응표로 복원한다
    return docs_in, findings, pdocs, mapping


@pytest.mark.parametrize("index", range(len(SAMPLE_FILES)), ids=SAMPLE_FILES)
def test_sample_roundtrip(roundtrip, index):
    """완료 기준: 원문 → 가명처리 → 복원 결과가 원문과 같다."""
    docs_in, findings, pdocs, mapping = roundtrip
    expected = to_markdown(_expected_blocks(docs_in[index], findings, f"D{index + 1:02d}"))
    sent = to_markdown(pdocs[index].blocks)
    back = restore(sent, mapping)
    assert sent != expected, "가명처리가 아무것도 바꾸지 않았습니다"
    assert back.text == expected, f"{SAMPLE_FILES[index]} {_first_diff_line(back.text, expected)}번째 줄이 다릅니다"
    assert not back.unknown_codes and not back.variants


def test_missing_codes_are_only_those_left_out_of_the_copy(roundtrip):
    """사라진 가명코드는 마크다운 사본에 넣지 않는 곳(숨김 시트, 문서 속성, 메모)에만 있던 것이어야 한다."""
    docs_in, findings, pdocs, mapping = roundtrip
    everything = "\n".join(to_markdown(d.blocks) for d in pdocs)
    back = restore(everything, mapping)
    kinds = {(f"D{i + 1:02d}", b.block_id): (b.kind, b.hidden) for i, r in enumerate(docs_in) for b in r.blocks}
    for code in back.missing_codes:
        places = [kinds[(f.file_id, f.block_id)] for f in findings if f.alias_code == code]
        assert places and all(hidden or kind in ("metadata", "comment") for kind, hidden in places), code
    assert len(back.restored) >= 25 and len(back.missing_codes) <= 2
