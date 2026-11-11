"""차단 목록 검사(절대 규칙 6). 확정된 원문 값이 결과물에 하나라도 남아 있으면 완료가 아니다.

결과로는 finding_id만 돌려준다. 원문 값은 돌려주지 않는다.

회색 영역 낱개 값(소속, 직위, 직급 …)은 차단 목록에 넣지 않는다. 그 자체로는 식별 정보가 아니고
조합일 때만 식별되기 때문이다. '교사' 하나를 막으면 다른 문장의 '신규 교사'까지 막혀 내보낼 수 없다.
조합(group_id 묶음)은 묶음 전체 글자로 차단 목록에 넣는다(pseudonymize가 replacement를 채운 뒤).
"""

from __future__ import annotations

from privacy_operator.detect.dictionaries import GRAY_CATEGORIES
from privacy_operator.detect.finding import ACTION_KEEP, STATUS_CONFIRMED, Finding
from privacy_operator.detect.patterns import ValueIndex, fold, probe, value_pattern


class BlocklistHit(Exception):
    def __init__(self, finding_ids: list[str]):
        super().__init__(f"확정된 원문 값이 결과물에 남아 있습니다: {len(finding_ids)}건 ({', '.join(finding_ids[:10])})")
        self.finding_ids = finding_ids


def blocklist_hits(text: str, findings: list[Finding], bundles: dict[str, str] | None = None) -> list[str]:
    """bundles: group_id → 묶음 원문. 묶음은 group의 첫 finding_id로 보고한다.

    같은 값은 한 번만 찾고, 값이 글에 있을 수 없으면 정규식을 만들지 않는다(큰 명단에서 글 하나에 4초 걸렸다).
    찾는 기준은 value_pattern 그대로다(낱말 경계 없이 엄격하게)."""
    values: dict[tuple[str, str], list[str]] = {}  # (유형, 값) → finding_id들
    for f in findings:
        if f.review_status != STATUS_CONFIRMED or f.action == ACTION_KEEP or not f.raw_value.strip():
            continue
        if f.category in GRAY_CATEGORIES:
            continue
        values.setdefault((f.category, f.raw_value.strip()), []).append(f.finding_id)
    keys = list(values)
    text = fold(text)  # 값 정규식은 모양을 맞춘 글에서 쓴다(전각 숫자, 여러 가지 줄표)
    left = {fid for i in ValueIndex([probe(*k) for k in keys]).candidates(text)
            if value_pattern(*keys[i]).search(text) for fid in values[keys[i]]}
    hits = [f.finding_id for f in findings if f.finding_id in left]
    for gid, bundle in (bundles or {}).items():
        if bundle.strip() and fold(bundle) in text:
            first = min((f.finding_id for f in findings if f.group_id == gid), default=gid)
            hits.append(first)
    return hits


def check_blocklist(text: str, findings: list[Finding], bundles: dict[str, str] | None = None) -> None:
    hits = blocklist_hits(text, findings, bundles)
    if hits:
        raise BlocklistHit(hits)
