"""말투 문구표. 화면 문구는 코드에 쓰지 않고 여기서 가져온다(docs/02_디자인명세.md '말투 설정').

- voice_plain.json(쉬운 말, 기본), voice_mission.json(작전 말투). 두 파일의 키는 같아야 한다.
- 작전 말투일 때는 툴팁에 쉬운 말 뜻을 함께 보여 준다.
- 말투가 바뀌어도 폴더 이름, 파일 형식, mission.json, 로그는 그대로다.
"""

from __future__ import annotations

import json
from pathlib import Path

VOICE_DIR = Path(__file__).resolve().parent
VOICES = ("plain", "mission")


def _load(name: str) -> dict[str, str]:
    return json.loads((VOICE_DIR / f"voice_{name}.json").read_text(encoding="utf-8"))


class Voice:
    def __init__(self, name: str = "plain"):
        self._plain = _load("plain")
        self.set(name)

    def set(self, name: str) -> None:
        self.name = name if name in VOICES else "plain"
        self._table = self._plain if self.name == "plain" else _load(self.name)

    def t(self, key: str, **values) -> str:
        text = self._table[key]
        return text.format(**values) if values else text

    def tip(self, key: str) -> str:
        """툴팁. 쉬운 말에서는 설명만, 작전 말투에서는 쉬운 말 이름과 설명을 함께."""
        meaning = self._plain.get(f"tip.{key}", "")
        if self.name == "plain":
            return meaning
        name = self._plain.get(key, "")
        if name == self._table.get(key):
            return meaning
        return f"{name}: {meaning}" if meaning else name

    def keys(self) -> set[str]:
        return set(self._table)
