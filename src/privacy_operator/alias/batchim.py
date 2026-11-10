"""받침 판정. 복원할 때 조사(은/는, 이/가 …)를 고르는 데 쓴다.

한글은 마지막 글자로, 숫자와 영문은 한국어로 읽었을 때의 끝소리로 판단한다.
"""

from __future__ import annotations

# 0 영, 1 일, 2 이, 3 삼, 4 사, 5 오, 6 육, 7 칠, 8 팔, 9 구
_DIGIT_BATCHIM = {"0": True, "1": True, "2": False, "3": True, "4": False,
                  "5": False, "6": True, "7": True, "8": True, "9": False}
# 영문 글자 이름: 엘, 엠, 엔, 알만 받침이 있다
_LATIN_BATCHIM = set("lmnrLMNR")
_TRAILING_SKIP = set(" )]}>\"'.,-_·")
# 글자 이름이 아니라 낱말로 읽는 끝: (받침 있음, ㄹ 받침). net은 '넷', com은 '컴'
_WORD_ENDINGS = {"net": (True, False), "com": (True, False), "org": (False, False)}


def _word_ending(text: str) -> tuple[bool, bool] | None:
    i = len(text)
    while i and text[i - 1] in _TRAILING_SKIP:
        i -= 1
    j = i
    while j and text[j - 1].isascii() and text[j - 1].isalpha():
        j -= 1
    return _WORD_ENDINGS.get(text[j:i].lower())


def has_batchim(text: str) -> bool:
    word = _word_ending(text or "")
    if word:
        return word[0]
    for ch in reversed(text or ""):
        if ch in _TRAILING_SKIP:
            continue
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch in _DIGIT_BATCHIM:
            return _DIGIT_BATCHIM[ch]
        if ch.isascii() and ch.isalpha():
            return ch in _LATIN_BATCHIM
        return False
    return False


def has_rieul_batchim(text: str) -> bool:
    """'ㄹ' 받침인가. '으로/로'는 ㄹ 받침 뒤에서 '로'를 쓴다."""
    word = _word_ending(text or "")
    if word:
        return word[1]
    for ch in reversed(text or ""):
        if ch in _TRAILING_SKIP:
            continue
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 == 8
        if ch in _DIGIT_BATCHIM:
            return ch in "178"  # 일, 칠, 팔
        if ch.isascii() and ch.isalpha():
            return ch in "lLrR"  # 엘, 알
        return False
    return False
