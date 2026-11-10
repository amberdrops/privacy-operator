"""HWPX, DOCX, XLSX 결과물 복원(8단계, XLSX는 2026-11-08). 서식은 그대로 두고 XML 글 칸 안의 가명코드만 되돌린다.

- 모든 XML과 미리보기 글(.txt)의 글 칸을 한 문서처럼 restore_many로 복원한다(조사 보정 포함).
- AI 프로그램이 가명코드를 글자 모양 조각 둘로 나눠 저장하면(`[강사-` + `01]`) 글 칸 단위로는 찾지 못한다.
  그래서 다 바꾼 뒤 kordoc으로 다시 읽어 남은 가명코드를 센다(leftover_codes). 남아 있으면 완료가 아니다.
"""

from __future__ import annotations

import html
import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

from privacy_operator.restore.restore import CODE_RE, RestoreResult, restore_many

_TEXT = re.compile(r">([^<]+)<")


def _texts(data: bytes, name: str) -> tuple[str, list[str]] | None:
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    if name.lower().endswith(".txt"):
        return decoded, [decoded]
    return decoded, [html.unescape(t) for t in _TEXT.findall(decoded)]


def restore_zip(src: Path, dst: Path, mapping: dict, accept=frozenset()) -> RestoreResult:
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        datas = {i.filename: zin.read(i.filename) for i in infos}
    targets: list[tuple[str, str, list[str]]] = []  # (이름, 원래 글, 글 칸들)
    for info in infos:
        name = info.filename
        if name.lower().endswith((".xml", ".txt", ".hpf")):
            parsed = _texts(datas[name], name)
            if parsed and any("[" in t or any(ch.isdigit() for ch in t) for t in parsed[1]):
                targets.append((name, parsed[0], parsed[1]))

    pieces = [t for _, _, texts in targets for t in texts]
    restored, result = restore_many(pieces, mapping, accept)
    it = iter(restored)
    for name, decoded, texts in targets:
        if name.lower().endswith(".txt"):
            datas[name] = next(it).encode("utf-8")
            continue
        new_texts = [next(it) for _ in texts]
        pos = iter(zip(texts, new_texts))

        def put(m: re.Match) -> str:
            old, new = next(pos)
            return m.group(0) if old == new else ">" + escape(new) + "<"

        datas[name] = _TEXT.sub(put, decoded).encode("utf-8")

    with zipfile.ZipFile(dst, "w") as zout:
        for info in infos:
            zout.writestr(info, datas[info.filename])
    result.text = ""  # 결과 글은 파일에 있다
    return result


def leftover_codes(path: Path, mapping: dict) -> Counter:
    """복원본을 kordoc으로 다시 읽어, 대응표에 있는 가명코드가 아직 남았는지 센다(조각으로 나뉜 것 포함)."""
    from kordoc import KordocClient

    from privacy_operator.reader.engine import make_config

    known = {e["alias_code"] for e in mapping.get("entries", [])}
    with KordocClient(make_config()) as client:
        r = client.parse(str(path), images=False, ocr=False)
    text = (r.raw.get("markdown") or "") if r.success else ""
    return Counter(m.group(0) for m in CODE_RE.finditer(text) if m.group(0) in known)
