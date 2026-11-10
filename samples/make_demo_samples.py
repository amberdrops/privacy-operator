"""데모 영상과 Store 화면용 문서 3종(통학버스 조정 결과, 체험학습 신청, 민원 회신). 모든 값은 지어낸 값이다(samples/README.md).

연수 이야기는 넣지 않는다(일반 업무 문서, 2026-11-10). 결과 보고는 공문 형식이고, 기안자가 행정실 주무관이므로
행정실 업무(통학버스)로 쓴다(교사 업무를 주무관이 올리면 어색하다, 2026-11-10).
맨 아래 결재란(마지막 결재자는 `교장 11/10 이름`), 시행, 주소, 전화, 이메일 줄과 메모가 있다.

    python samples/make_demo_samples.py

시험용 샘플(make_samples.py)과 따로 둔다. 시험은 정답 목록이 있는 그쪽 샘플을 쓰고, 이쪽은 화면 시연에만 쓴다.
결과는 samples/demo/에 만든다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from make_samples import ENGINE_CLI, _font_args
from openpyxl import Workbook
from openpyxl.styles import Font

DEMO = Path(__file__).resolve().parent / "demo"

HWPX_NAME = "통학버스_조정결과_샘플.hwpx"
HWPX_MARKDOWN = """\
# 가상초등학교

수신 내부결재

(경유)

제목 2026학년도 2학기 통학버스 승하차 장소 조정 결과 보고

1. 2026학년도 2학기 통학버스 승하차 장소 조정 신청을 아래와 같이 처리하였기에 그 결과를 보고합니다.

2. 조정 내역

| 학생명 | 보호자 | 연락처 | 바뀐 장소 |
|---|---|---|---|
| 도하늘 | 도민재 | 010-0000-0101 | 가상아파트 정문 |
| 표서진 | 하유정 | 010-0000-0102 | 중앙공원 앞 |
| 석지안 | 석태오 | 010-0000-0103 | 나무마을 삼거리 |

3. 처리 요약

가. 도하늘 학생 보호자 도민재 님이 하교 정차 위치 변경을 요청하였음.

나. 석지안 학생은 운행 기사 변소담 님과 새 정차 위치를 확인하였음.

붙임 조정 신청서 3부. 끝.

주무관 윤채아    행정실장 선우진    교장 11/10 남궁하린

협조자

시행 가상초등학교-1234 (2026. 11. 10.)    접수

우 12345 경기도 가상시 예시로 45 / http://www.example.kr

전화 031-000-0101 / 전송 031-000-0109 / chaea.yun@example.kr / 대국민 공개
"""
HWPX_MEMO = "윤채아 주무관 확인 후 보호자 안내 문자 발송"

XLSX_NAME = "체험학습_신청명단_샘플.xlsx"
XLSX_ROWS = [
    ("학생명", "보호자", "보호자 연락처", "이메일", "비고"),
    ("도하늘", "도민재", "010-0000-0101", "minjae.do@example.kr", "알레르기 없음"),
    ("표서진", "하유정", "010-0000-0102", "yujeong.ha@example.kr", "버스 멀미"),
    ("석지안", "석태오", "010-0000-0103", "taeo.seok@example.com", ""),
    ("모은결", "모시우", "010-0000-0104", "siu.mo@example.kr", "보호자 동행 희망"),
]

PDF_NAME = "민원회신_샘플.pdf"
PDF_LINES = [
    ("민원 처리 결과 알림", 18),
    ("", 11),
    ("신청인: 하유정", 11),
    ("주소: 경기도 가상시 예시로 45, 202동 1203호", 11),
    ("연락처: 010-0000-0102", 11),
    ("이메일: yujeong.ha@example.kr", 11),
    ("", 11),
    ("요청하신 통학버스 정차 위치 변경은 다음 학기부터 반영합니다.", 11),
    ("처리 담당자: 윤채아 (031-000-0101)", 11),
    ("학교 행정실: 031-000-0100", 11),
]


def make_hwpx() -> None:
    node = shutil.which("node")
    if not node or not ENGINE_CLI.is_file():
        raise SystemExit("node 또는 kordoc 엔진이 없습니다. engine/ 에서 npm install --ignore-scripts 를 먼저 하세요.")
    env = {**os.environ, "KORDOC_OFFLINE": "1"}
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / "source.md"
        md.write_text(HWPX_MARKDOWN, encoding="utf-8")
        plain = Path(tmp) / "plain.hwpx"
        subprocess.run([node, str(ENGINE_CLI), "generate", str(md), "-o", str(plain)], check=True, env=env)
        # 한글 메모: 메모 속 이름도 찾는 장면(사용법 영상 1부, 2부)
        subprocess.run([node, str(ENGINE_CLI), "memo", str(plain), "--anchor", "3. 처리 요약", "--text", HWPX_MEMO,
                        "-o", str(DEMO / HWPX_NAME)], check=True, env=env)
    subprocess.run([node, str(ENGINE_CLI), "validate", str(DEMO / HWPX_NAME)], check=True, env=env)


def make_xlsx() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "신청명단"
    for row in XLSX_ROWS:
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for col, width in zip("ABCDE", (10, 10, 16, 26, 18)):
        ws.column_dimensions[col].width = width
    wb.properties.creator = "윤채아"
    wb.save(DEMO / XLSX_NAME)


def make_pdf() -> None:
    font = _font_args()
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for text, size in PDF_LINES:
        if text:
            page.insert_text((72, y), text, fontsize=size, **font)
        y += size + 12
    doc.set_metadata({"author": "윤채아", "title": "민원 처리 결과 알림"})
    doc.subset_fonts()
    doc.save(DEMO / PDF_NAME, garbage=4, deflate=True)


if __name__ == "__main__":
    DEMO.mkdir(exist_ok=True)
    make_hwpx()
    make_xlsx()
    make_pdf()
    print(f"데모 문서 3종을 {DEMO}에 만들었습니다.")
