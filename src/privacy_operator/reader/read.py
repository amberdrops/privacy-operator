"""문서 읽기 진입점.

    with DocumentReader() as reader:
        result = reader.read(path)

원본 파일은 읽기만 한다. 예외 메시지에는 파일 경로나 내용 대신 형식과 오류 코드만 넣는다.
"""

from __future__ import annotations

from pathlib import Path

from kordoc import KordocClient

from privacy_operator.reader.blocks import BlindSpot, ReadResult
from privacy_operator.reader.convert import convert
from privacy_operator.reader.engine import make_config
from privacy_operator.reader.extras import (
    IMAGE,
    NEEDS_OCR,
    pdf_blind_spots,
    pdf_extras,
    xlsx_extras,
    zip_blind_spots,
)

SUPPORTED = {".hwpx": "hwpx", ".hwp": "hwp", ".pdf": "pdf", ".xlsx": "xlsx"}

# 이미지 바이트는 받지 않는다. OCR은 끈다(모델 내려받기가 필요하고, 오프라인에서 막힌다).
_PARSE_OPTIONS = {"images": False, "ocr": False}


class ReadError(RuntimeError):
    def __init__(self, fmt: str, code: str):
        super().__init__(f"{fmt} 문서를 읽지 못했습니다 (코드: {code})")
        self.format = fmt
        self.code = code


class DocumentReader:
    def __init__(self, **config_overrides):
        self._client = KordocClient(make_config(**config_overrides)).start()

    def __enter__(self) -> DocumentReader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def markdown(self, path: str | Path) -> str:
        """엔진의 마크다운(기본 설정). 한글 파일을 쓸 때 이미 켜 둔 엔진을 다시 쓴다(새로 켜면 0.8초)."""
        return self._client.parse(str(path)).raw.get("markdown") or ""

    def read(self, path: str | Path) -> ReadResult:
        path = Path(path)
        fmt = SUPPORTED.get(path.suffix.lower())
        if fmt is None:
            raise ReadError(path.suffix.lower().lstrip(".") or "알 수 없는", "UNSUPPORTED_FORMAT")
        result = self._client.parse(str(path), **_PARSE_OPTIONS)
        if not result.success:
            raise ReadError(fmt, str(result.code or "PARSE_FAILED"))
        raw = result.raw
        blocks, blind = convert(raw, fmt)
        try:
            blocks, blind = self._extras(path, fmt, blocks, blind)
        except ReadError:
            raise
        except Exception:  # noqa: BLE001
            # 메모와 숨긴 시트, 문서 속성을 따로 읽다 멈춘 문서(서식 표가 어긋난 엑셀 등). 그 부분을 검사하지 못한 채
            # 넘기지 않고 읽지 못한 문서로 돌린다. 화면까지 오류가 올라가 프로그램이 꺼지던 것을 막는다(2026-11-11)
            raise ReadError(fmt, "EXTRAS_FAILED") from None
        return ReadResult(source_name=path.name, format=fmt, blocks=blocks, blind_spots=_unique(blind),
                          page_count=raw.get("pageCount"))

    @staticmethod
    def _extras(path: Path, fmt: str, blocks: list, blind: list) -> tuple[list, list]:
        if fmt == "xlsx":
            blocks = xlsx_extras(path, blocks)
            blind += zip_blind_spots(path, fmt)
        elif fmt == "hwpx":
            blind += zip_blind_spots(path, fmt)
        elif fmt == "pdf":
            blocks = pdf_extras(path, blocks)
            # PDF는 쪽마다 직접 본 결과로 kordoc의 OCR 판정과 이미지 표시를 바꾼다
            blind = [b for b in blind if b.kind not in (NEEDS_OCR, IMAGE) and b.detail != "NEEDS_OCR"]
            blind += pdf_blind_spots(path)
        return blocks, blind


def _unique(spots: list[BlindSpot]) -> list[BlindSpot]:
    """같은 쪽의 같은 종류는 하나로. 이미지만 있는 쪽(needs_ocr)이 있으면 그 쪽의 image는 뺀다."""
    ocr_pages = {b.page for b in spots if b.kind == NEEDS_OCR}
    merged: dict[tuple, BlindSpot] = {}
    for b in spots:
        if b.kind == IMAGE and b.page is not None and b.page in ocr_pages:
            continue
        key = (b.kind, b.page)
        old = merged.get(key)
        if old is None:
            merged[key] = b
        elif old.detail.isdigit() and b.detail.isdigit() and int(b.detail) > int(old.detail):
            merged[key] = b  # 개수는 큰 쪽(kordoc과 zip 검사가 같은 그림을 세었을 때)
    return list(merged.values())
