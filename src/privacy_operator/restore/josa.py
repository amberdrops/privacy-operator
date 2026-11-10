"""조사 보정. 가명코드를 실명으로 되돌린 뒤 바로 뒤 조사를 실명 받침에 맞춘다.

    [이름-01]는 → 이도윤은 (받침 있음)      [이름-01]은 → 김민수는 (받침 없음)
    [이름-02]으로 → 정하람으로 / 한별로 (ㄹ 받침 뒤는 '로')

조사 뒤에 띄어쓰기, 문장부호, 글 끝이 올 때만 조사로 본다. `이다`, `이에요` 같은 서술격 조사나
조사처럼 보이는 다른 낱말(`은행`, `가게`)은 건드리지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from privacy_operator.alias.batchim import has_rieul_batchim

# (받침 있을 때, 받침 없을 때). 긴 것부터 맞춰 본다
PAIRS: tuple[tuple[str, str], ...] = (
    ("이랑", "랑"),
    ("이나", "나"),
    ("은", "는"),
    ("이", "가"),
    ("을", "를"),
    ("과", "와"),
)
_FORM_TO_PAIR = {form: pair for pair in PAIRS for form in pair}

_BOUNDARY = r"(?=$|[\s.,!?;:)\]}'\"’”…~」』>·\x00-])"  # \x00: restore_many의 조각 경계
_PAIR_RE = re.compile("(" + "|".join(sorted(_FORM_TO_PAIR, key=len, reverse=True)) + ")" + _BOUNDARY)
# 으로/로 뒤에는 서, 써, 부터, 는, 도, 의, 만이 더 붙을 수 있다
_RO_RE = re.compile(r"(으로|로)(서|써|부터|는|도|의|만)?" + _BOUNDARY)


@dataclass(frozen=True)
class JosaFix:
    end: int  # 원래 조사가 끝나는 위치(text 기준)
    josa: str  # 바꿔 쓸 조사(원래와 같을 수 있다)


def pick(pair: tuple[str, str], batchim: bool) -> str:
    return pair[0] if batchim else pair[1]


def pick_ro(batchim: bool, rieul: bool) -> str:
    """받침이 있고 ㄹ이 아니면 '으로', 받침이 없거나 ㄹ 받침이면 '로'."""
    return "으로" if batchim and not rieul else "로"


def josa_after(text: str, pos: int, value: str, batchim: bool) -> JosaFix | None:
    """text[pos:]가 조사로 시작하면 value에 맞는 조사를 돌려준다. 조사가 아니면 None."""
    m = _RO_RE.match(text, pos)
    if m:
        return JosaFix(m.end(), pick_ro(batchim, has_rieul_batchim(value)) + (m.group(2) or ""))
    m = _PAIR_RE.match(text, pos)
    if m:
        return JosaFix(m.end(), pick(_FORM_TO_PAIR[m.group(1)], batchim))
    return None
