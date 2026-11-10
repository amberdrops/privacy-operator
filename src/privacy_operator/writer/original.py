"""원본 형식 사본 만들기와 저장 뒤 재검사(8단계, 절대 규칙 6).

1. 임시 폴더에 새 파일을 만든다(원본은 읽기만 한다).
2. 새 파일을 두 길로 다시 검사한다.
   - 우리 reader로 다시 읽어 본문, 표, 메모, 주석, 문서 속성, 숨김 시트를 본다.
   - 형식별로 날것을 본다: HWPX, XLSX는 zip 안의 모든 글 파일, PDF는 쪽 글, XMP, 주석, 책갈피, 입력 칸.
3. 확정한 값이 하나라도 남으면 그 파일은 보낼 문서함에 넣지 않는다(마크다운 사본만 보낸다).
   임시 폴더는 성공, 실패와 관계없이 지운다.

결과에는 남은 곳(파일 안 이름, 쪽)과 finding_id만 쓴다. 원문 값은 쓰지 않는다.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from privacy_operator.writer.plan import ReplacementPlan
from privacy_operator.writer.xmlsweep import text_segments

log = logging.getLogger(__name__)

OUTPUT_SUFFIX = {"hwpx": ".hwpx", "hwp": ".hwpx", "pdf": ".pdf", "xlsx": ".xlsx"}
_BINARY = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".emf", ".wmf", ".bin", ".ole", ".ttf", ".otf")

# 결과 사유
OK = "ok"
LEFTOVER = "leftover"  # 확정한 값이 남음
ERROR = "error"  # 만들다가 오류
UNSUPPORTED = "unsupported"


@dataclass
class OriginalOutput:
    source_name: str
    status: str
    path: Path | None = None
    places: list[str] = field(default_factory=list)  # 남은 곳(값 없음)
    finding_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == OK


def _zip_leftovers(path: Path, plan: ReplacementPlan) -> tuple[list[str], list[str]]:
    places, ids = [], []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith("/") or name.lower().endswith(_BINARY):
                continue
            data = z.read(name)
            text = None
            for enc in ("utf-8-sig", "utf-16"):
                try:
                    text = data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                continue
            hits = plan.leftovers(text) + [i for seg in text_segments(text) for i in plan.leftovers(seg)]
            if hits:
                places.append(name)
                ids += hits
    return places, ids


def _pdf_leftovers(path: Path, plan: ReplacementPlan) -> tuple[list[str], list[str]]:
    import pymupdf

    places, ids = [], []

    def check(where: str, text: str) -> None:
        hits = plan.leftovers(text or "", wrapped=True)  # 줄에 걸친 값도 남은 것으로 본다
        if hits:
            places.append(where)
            ids.extend(hits)

    with pymupdf.open(path) as doc:
        for pno, page in enumerate(doc, start=1):
            check(f"page {pno}", page.get_text())
            for annot in page.annots() or []:
                check(f"page {pno} annot", " ".join(str(v) for v in annot.info.values()))
            for widget in page.widgets() or []:
                check(f"page {pno} field", str(widget.field_value or ""))
        check("metadata", " ".join(str(v) for v in (doc.metadata or {}).values()))
        check("xmp", doc.get_xml_metadata())
        check("bookmarks", " ".join(t for _, t, _ in doc.get_toc(simple=True)))
    return places, ids


def verify(path: Path, fmt: str, plan: ReplacementPlan, reader) -> tuple[list[str], list[str]]:
    """남은 곳과 finding_id. 둘 다 비면 통과."""
    places, ids = [], []
    if reader is not None:
        for b in reader.read(path).blocks:
            hits = plan.leftovers(b.text)
            if hits:
                places.append(b.block_id)
                ids += hits
    p2, i2 = _pdf_leftovers(path, plan) if fmt == "pdf" else _zip_leftovers(path, plan)
    return places + p2, list(dict.fromkeys(ids + i2))


def write_original(src: Path, fmt: str, plan: ReplacementPlan, out_dir: Path, stem: str, reader) -> OriginalOutput:
    """src를 원본 형식으로 가명처리해 out_dir/<stem><확장자>에 둔다. 검사를 통과할 때만."""
    from privacy_operator.writer.hwpx import write_hwpx
    from privacy_operator.writer.pdf import write_pdf
    from privacy_operator.writer.xlsx import write_xlsx

    if fmt not in OUTPUT_SUFFIX:
        return OriginalOutput(src.name, UNSUPPORTED)
    suffix = OUTPUT_SUFFIX[fmt]
    with tempfile.TemporaryDirectory(prefix="po-") as tmp:
        work = Path(tmp)
        staged = work / f"output{suffix}"
        try:
            if fmt in ("hwpx", "hwp"):
                # 구조 검사를 남은 값 검사와 동시에 돌려 봤지만 엔진 두 개가 서로 느려져 더 오래 걸렸다(2026-11-10)
                write_hwpx(src, staged, plan, work, is_hwp=(fmt == "hwp"), reader=reader)
            elif fmt == "pdf":
                write_pdf(src, staged, plan)
            else:
                write_xlsx(src, staged, plan)
        except Exception as e:  # noqa: BLE001 - 형식 하나가 실패해도 나머지는 만든다
            log.warning("원본 형식 사본 실패 (%s, %s)", fmt, type(e).__name__)  # 메시지에는 경로가 있을 수 있다
            return OriginalOutput(src.name, ERROR)
        places, ids = verify(staged, "pdf" if fmt == "pdf" else "zip", plan, reader)
        if places:
            log.warning("원본 형식 사본에 확정 값이 남음 (%s): 곳 %d, 탐지 %s", fmt, len(places), ids[:10])
            return OriginalOutput(src.name, LEFTOVER, places=places, finding_ids=ids)
        out_dir.mkdir(parents=True, exist_ok=True)
        final = out_dir / f"{stem}{suffix}"
        n = 2
        while final.exists():
            final = out_dir / f"{stem}_{n}{suffix}"
            n += 1
        shutil.copyfile(staged, final)
    log.info("원본 형식 사본 (%s) 만듦", fmt)
    return OriginalOutput(src.name, OK, path=final)
