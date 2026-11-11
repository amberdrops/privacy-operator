"""XML 안의 글자 부분(태그 사이 글과 속성 값)에만 함수를 적용한다. HWPX 쓰기와 zip 재검사가 함께 쓴다.

엔터티(&amp; 등)를 풀어 함수에 넘기고, 바뀐 부분만 다시 이스케이프한다. 바뀌지 않은 부분은 바이트 그대로 둔다.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from xml.sax.saxutils import escape

_TEXT = re.compile(r">([^<]+)<")
_ATTR = re.compile(r'(=")([^"]*)(")')


def sweep(xml: str, fn: Callable[[str], str], attributes: bool = True) -> tuple[str, int]:
    changed = 0

    def text_node(m: re.Match) -> str:
        nonlocal changed
        raw = m.group(1)
        new = fn(html.unescape(raw))
        if new == html.unescape(raw):
            return m.group(0)
        changed += 1
        return ">" + escape(new) + "<"

    def attr(m: re.Match) -> str:
        nonlocal changed
        raw = m.group(2)
        new = fn(html.unescape(raw))
        if new == html.unescape(raw):
            return m.group(0)
        changed += 1
        return m.group(1) + escape(new, {'"': "&quot;"}) + m.group(3)

    out = _TEXT.sub(text_node, xml)
    if attributes:
        out = _ATTR.sub(attr, out)
    return out, changed


def decode_text(data: bytes) -> tuple[str, str] | None:
    """zip 안 글 파일의 (글, 인코딩). 글이 아니면 None. 한글 미리보기 글(PrvText.txt)은 UTF-16이다."""
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return None


def text_segments(xml: str) -> list[str]:
    """검사용: 태그 사이 글과 속성 값(엔터티를 푼 것)."""
    return [html.unescape(t) for t in _TEXT.findall(xml)] + [html.unescape(a) for _, a, _ in _ATTR.findall(xml)]
