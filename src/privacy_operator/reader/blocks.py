"""문서를 읽은 결과의 공통 형태: 블록과 위치.

형식(HWPX, HWP, PDF, XLSX)과 상관없이 탐지기는 이 형태만 본다.
블록의 text는 원문이므로 로그나 예외 메시지에 넣지 않는다. 로그에는 block_id와 kind만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 블록 종류
PARAGRAPH = "paragraph"
HEADING = "heading"
CELL = "cell"
METADATA = "metadata"
COMMENT = "comment"  # 한글 메모, 엑셀 셀 메모, PDF 주석


@dataclass(frozen=True)
class Location:
    """블록 위치. 형식마다 쓰는 칸만 채운다."""

    page: int | None = None  # 쪽. XLSX는 kordoc이 시트 순번을 쪽으로 준다
    block_index: int | None = None  # kordoc 블록 순번
    table_index: int | None = None  # 문서 안 표 순번(0부터)
    row: int | None = None  # 표 안 행(0부터, 머리글 포함)
    col: int | None = None  # 표 안 열(0부터)
    header: str | None = None  # 이 칸이 속한 열 제목(가장 아래 머리글 행)
    header_path: tuple[str, ...] = ()  # 머리글이 여러 줄이면 위에서부터
    row_label: str | None = None  # 같은 줄 바로 왼쪽 칸 글자. 세로 이름표 표(`원 장 | 2026. 10. 7. 박서연`)
    sheet: str | None = None  # XLSX 시트 이름
    cell: str | None = None  # XLSX 셀 주소(A1)
    field: str | None = None  # 문서 속성 이름, 메모의 text/author
    bbox: tuple[float, float, float, float] | None = None  # PDF (x, y, 너비, 높이), kordoc 좌표


@dataclass(frozen=True)
class Block:
    block_id: str
    kind: str
    text: str
    location: Location
    is_header: bool = False  # 표 머리글 칸
    hidden: bool = False  # 숨김 시트처럼 화면에 안 보이는 곳

    def __repr__(self) -> str:  # 원문이 디버그 출력에 새지 않게
        return f"Block({self.block_id!r}, kind={self.kind!r}, len={len(self.text)})"


@dataclass(frozen=True)
class BlindSpot:
    """검사하지 못한 영역(사각지대). 화면 띠와 내보내기 차단에 쓴다."""

    kind: str  # needs_ocr, image, engine_warning, unsupported
    page: int | None = None
    detail: str = ""  # 원문 값을 넣지 않는다. 개수나 코드만


@dataclass
class ReadResult:
    source_name: str  # 파일 이름(경로 없이)
    format: str  # hwpx, hwp, pdf, xlsx
    blocks: list[Block] = field(default_factory=list)
    blind_spots: list[BlindSpot] = field(default_factory=list)
    page_count: int | None = None

    def __repr__(self) -> str:
        return (f"ReadResult(format={self.format!r}, blocks={len(self.blocks)}, "
                f"blind_spots={len(self.blind_spots)})")

    def cells(self) -> list[Block]:
        return [b for b in self.blocks if b.kind == CELL]
