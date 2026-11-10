"""가명코드 부여. 형식 `[유형-번호]`. 같은 작전 안에서 같은 값은 같은 가명코드다.

이름은 모두 `[이름-01]`이다. 직함이나 역할(강사, 원장)을 가명코드에 넣지 않는다. 결재란의 사무관이 부장으로
붙는 것처럼 탐지 근거에서 짐작한 역할이 틀리는 일이 있어서다(2026-11-09 사용자 결정). 이름이 아닌 유형은
유형 이름을 쓴다(`[전화-01]`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from privacy_operator.alias.batchim import has_batchim
from privacy_operator.detect.dictionaries import (
    ADDRESS,
    AFFILIATION,
    AGE,
    BIRTH,
    EMAIL,
    GRADE,
    INDIRECT,
    NAME,
    PHONE,
    POSITION,
    SCHOOL,
    WORKPLACE,
)

NAME_ROLE = "이름"

_CATEGORY_ROLES = {
    NAME: NAME_ROLE, PHONE: "전화", EMAIL: "이메일", ADDRESS: "주소", BIRTH: "생년월일",
    AFFILIATION: "소속", WORKPLACE: "근무지", SCHOOL: "학교", POSITION: "직위", GRADE: "직급", AGE: "나이대",
    INDIRECT: "소속직급",
}
WIDE_ROLES: frozenset[str] = frozenset()  # 세 자리로 매길 유형(지금은 없음. 100번째부터는 저절로 세 자리)


def role_for(category: str, role_hint: str | None) -> str:
    """가명코드 앞부분. 이름은 역할과 상관없이 `이름`(role_hint는 AI 간접식별 후보에만 쓴다)."""
    if category == INDIRECT and role_hint:  # AI가 찾은 간접식별 후보: [간접식별-01]
        return role_hint
    return _CATEGORY_ROLES.get(category, category)


def value_key(category: str, value: str) -> tuple[str, str]:
    """같은 값인지 가르는 열쇠. 전화는 숫자만, 이메일은 소문자, 나머지는 공백을 뺀다."""
    if category == PHONE:
        return (category, re.sub(r"\D", "", value))
    if category == EMAIL:
        return (category, value.strip().lower())
    return (category, re.sub(r"\s", "", value))


@dataclass
class MappingEntry:
    alias_code: str
    category: str
    raw_value: str
    has_batchim: bool
    source_files: list[str] = field(default_factory=list)
    created_at: str = ""

    def __repr__(self) -> str:
        return f"MappingEntry({self.alias_code!r}, category={self.category!r})"

    def to_dict(self) -> dict:
        return {"alias_code": self.alias_code, "category": self.category, "raw_value": self.raw_value,
                "has_batchim": self.has_batchim, "source_files": self.source_files, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> MappingEntry:
        return cls(d["alias_code"], d["category"], d["raw_value"], d["has_batchim"],
                   list(d.get("source_files", [])), d.get("created_at", ""))


class AliasBook:
    """작전 하나의 가명코드 장부. 대응표(mapping)의 내용이 된다."""

    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self._by_key: dict[tuple[str, str], MappingEntry] = {}
        self._next: dict[str, int] = {}

    def __repr__(self) -> str:
        return f"AliasBook({self.mission_id!r}, entries={len(self._by_key)})"

    @classmethod
    def continue_from(cls, mission_id: str, previous: dict) -> AliasBook:
        """'이전 작전 가명코드 이어 쓰기'(옵션). 이전 대응표의 가명코드와 번호를 이어받는다."""
        book = cls(mission_id)
        for d in previous.get("entries", []):
            e = MappingEntry.from_dict(d)
            e.source_files = []
            book._by_key[value_key(e.category, e.raw_value)] = e
            m = re.fullmatch(r"\[(.+)-(\d+)\]", e.alias_code)
            if m:
                book._next[m.group(1)] = max(book._next.get(m.group(1), 1), int(m.group(2)) + 1)
        return book

    def code_for(self, category: str, value: str, role_hint: str | None, source_file: str) -> str:
        key = value_key(category, value)
        entry = self._by_key.get(key)
        if entry is None:
            role = role_for(category, role_hint)
            n = self._next.get(role, 1)
            self._next[role] = n + 1
            width = 3 if role in WIDE_ROLES else 2
            entry = MappingEntry(f"[{role}-{n:0{width}d}]", category, value, has_batchim(value),
                                 created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            self._by_key[key] = entry
        if source_file not in entry.source_files:
            entry.source_files.append(source_file)
        return entry.alias_code

    def entries(self) -> list[MappingEntry]:
        used = [e for e in self._by_key.values() if e.source_files]
        return sorted(used, key=lambda e: e.alias_code)

    def to_mapping(self) -> dict:
        """대응표(복호화했을 때의 형태, docs/04_작전패키지_규격.md)."""
        return {"mission_id": self.mission_id, "entries": [e.to_dict() for e in self.entries()]}

    def legend(self) -> list[dict]:
        """mission.json의 alias_legend. 원문 값 없이 역할과 번호만."""
        out = []
        for e in self.entries():
            m = re.fullmatch(r"\[(.+)-(\d+)\]", e.alias_code)
            role, num = (m.group(1), int(m.group(2))) if m else (e.category, 0)
            meaning = f"{num}번 사람" if e.category == NAME else f"{role} {num}"
            out.append({"alias_code": e.alias_code, "meaning": meaning})
        return out
