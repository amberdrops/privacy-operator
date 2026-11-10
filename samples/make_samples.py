"""테스트 문서 3종과 정답 목록을 만든다. 모든 값은 지어낸 값이다(samples/README.md).

    python samples/make_samples.py

- HWPX: 마크다운을 kordoc `generate`로 HWPX로 만들고 `validate`로 구조를 검사한다.
- XLSX: openpyxl
- PDF: PyMuPDF (내장 한글 글꼴 "korea")

정답 목록의 location은 reader가 돌려주는 블록 위치와 같은 말로 쓴다.
- table: 문서 안 표 순번(table), 행(row), 열(col), 열 제목(header). 행 0이 머리글
- paragraph: 쪽(page)
- metadata: 속성 이름(field)
- comment: 한글 메모, 엑셀 셀 메모, PDF 주석
- hidden_sheet: 숨김 시트의 표 칸
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from openpyxl import Workbook
from openpyxl.comments import Comment

SAMPLES = Path(__file__).resolve().parent
ROOT = SAMPLES.parent
ENGINE_CLI = ROOT / "engine" / "node_modules" / "kordoc" / "dist" / "cli.js"


def _answer(answer_id, value, category, tier, **location):
    return {"answer_id": answer_id, "value": value, "category": category, "tier": tier, "location": location}


def _write_answers(filename: str, answers: list[dict], must_not_detect: list[dict]) -> None:
    data = {"file": filename, "answers": answers, "must_not_detect": must_not_detect}
    path = SAMPLES / f"{filename}.answers.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- HWPX

HWPX_NAME = "연수운영계획_샘플.hwpx"

HWPX_MARKDOWN = """\
# 2026년 하반기 직무연수 운영계획

담당자: 박서연

연락처: 031-000-0001

## 강사 현황

| 강사 | 소속 | 직위 | 연락처 |
|---|---|---|---|
| 김민수 | 예시대학교 | 교수 | 010-0000-0001 |
| 이도윤 | 가상교육지원청 | 장학사 | 010-0000-0002 |
| 정하람 | 샘플초등학교 | 교사 | 010-0000-0003 |
| 미정 | 미정 | | |

## 운영 방법

1일차 오전은 김민수 강사가 맡고, 오후에는 이도윤 강사님이 사례 발표를 진행한다.

2일차 실습은 정하람이 진행하며, 문의는 담당자 박서연에게 한다.
"""

HWPX_MEMO = "정하람 강사 일정 재확인 필요"


def make_hwpx() -> None:
    node = shutil.which("node")
    if not node or not ENGINE_CLI.is_file():
        raise SystemExit("node 또는 kordoc 엔진이 없습니다. engine/ 에서 npm install --ignore-scripts 를 먼저 하세요.")
    env = {**os.environ, "KORDOC_OFFLINE": "1"}
    out = SAMPLES / HWPX_NAME
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / "source.md"
        md.write_text(HWPX_MARKDOWN, encoding="utf-8")
        plain = Path(tmp) / "plain.hwpx"
        subprocess.run([node, str(ENGINE_CLI), "generate", str(md), "-o", str(plain)], check=True, env=env)
        # 한글 메모(메모 넣기): 메모 속 이름도 찾아야 한다
        subprocess.run([node, str(ENGINE_CLI), "memo", str(plain), "--anchor", "강사 현황",
                        "--text", HWPX_MEMO, "-o", str(out)], check=True, env=env)
    subprocess.run([node, str(ENGINE_CLI), "validate", str(out)], check=True, env=env)

    t = "table"
    answers = [
        _answer("H01", "박서연", "이름", "확실", kind="paragraph", page=1, label="담당자"),
        _answer("H02", "031-000-0001", "전화", "확실", kind="paragraph", page=1, label="연락처"),
        _answer("H03", "김민수", "이름", "확실", kind=t, table=0, row=1, col=0, header="강사"),
        _answer("H04", "예시대학교", "소속", "참고", kind=t, table=0, row=1, col=1, header="소속"),
        _answer("H05", "교수", "직위", "참고", kind=t, table=0, row=1, col=2, header="직위"),
        _answer("H06", "010-0000-0001", "전화", "확실", kind=t, table=0, row=1, col=3, header="연락처"),
        _answer("H07", "이도윤", "이름", "확실", kind=t, table=0, row=2, col=0, header="강사"),
        _answer("H08", "가상교육지원청", "소속", "참고", kind=t, table=0, row=2, col=1, header="소속"),
        _answer("H09", "장학사", "직위", "참고", kind=t, table=0, row=2, col=2, header="직위"),
        _answer("H10", "010-0000-0002", "전화", "확실", kind=t, table=0, row=2, col=3, header="연락처"),
        _answer("H11", "정하람", "이름", "확실", kind=t, table=0, row=3, col=0, header="강사"),
        _answer("H12", "샘플초등학교", "소속", "참고", kind=t, table=0, row=3, col=1, header="소속"),
        _answer("H13", "교사", "직위", "참고", kind=t, table=0, row=3, col=2, header="직위"),
        _answer("H14", "010-0000-0003", "전화", "확실", kind=t, table=0, row=3, col=3, header="연락처"),
        _answer("H15", "김민수", "이름", "검토", kind="paragraph", page=1, note="본문 반복"),
        _answer("H16", "이도윤", "이름", "검토", kind="paragraph", page=1, note="본문 반복, 호칭 붙음"),
        _answer("H17", "정하람", "이름", "검토", kind="paragraph", page=1, note="본문 반복"),
        _answer("H18", "박서연", "이름", "검토", kind="paragraph", page=1, note="본문 반복"),
        _answer("H19", "정하람", "이름", "검토", kind="comment", note="한글 메모"),
    ]
    must_not = [
        {"answer_id": "HN01", "value": "미정", "reason": "예외 목록(강사 열의 미정)",
         "location": {"kind": t, "table": 0, "row": 4, "col": 0, "header": "강사"}},
    ]
    _write_answers(HWPX_NAME, answers, must_not)


# ---------------------------------------------------------------- XLSX

XLSX_NAME = "만족도조사_샘플.xlsx"

XLSX_ROWS = [
    ("응답자", "이메일", "소속", "주관식 의견"),
    ("한가온", "gaon.han@example.kr", "가상중학교", "강의 자료가 알찼습니다."),
    ("오세린", "serin.oh@example.kr", "샘플고등학교", "실습 시간이 더 길면 좋겠습니다."),
    ("유다온", "daon.yu@example.com", "예시교육지원청", "제가 예시교육지원청 회계팀 7급으로 있을 때 겪은 사례와 비슷했습니다."),
    ("남궁별", "byeol.namgung@example.kr", "가상중학교", "특별히 없습니다."),
    ("서이현", "ihyeon.seo@example.com", "샘플초등학교", "올해 신규 교사라서 도움이 많이 되었습니다."),
]


def make_xlsx() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "응답"
    for row in XLSX_ROWS:
        ws.append(row)
    ws["A3"].comment = Comment("오세린 선생님 재응답 요청", "운영팀")

    hidden = wb.create_sheet("내부메모")
    hidden.append(("확인자", "메모"))
    hidden.append(("구하윤", "응답 원본은 별도 보관"))
    hidden.sheet_state = "hidden"

    wb.properties.creator = "구하윤"
    wb.save(SAMPLES / XLSX_NAME)

    t = "table"
    answers = []
    for i, (name, email, org, _) in enumerate(XLSX_ROWS[1:], start=1):
        n = f"{i:02d}"
        answers.append(_answer(f"X{n}a", name, "이름", "확실", kind=t, sheet="응답", row=i, col=0, header="응답자"))
        answers.append(_answer(f"X{n}b", email, "이메일", "확실", kind=t, sheet="응답", row=i, col=1, header="이메일"))
        answers.append(_answer(f"X{n}c", org, "소속", "참고", kind=t, sheet="응답", row=i, col=2, header="소속"))
    answers += [
        _answer("X20", "예시교육지원청 회계팀 7급", "간접식별", "참고", kind=t, sheet="응답", row=3, col=3,
                header="주관식 의견", note="소속+부서+직급 조합"),
        _answer("X21", "오세린", "이름", "검토", kind="comment", sheet="응답", cell="A3"),
        _answer("X22", "구하윤", "이름", "확실", kind="hidden_sheet", sheet="내부메모", row=1, col=0, header="확인자"),
        _answer("X23", "구하윤", "이름", "확실", kind="metadata", field="creator"),
        _answer("X24", "구하윤", "이름", "확실", kind="metadata", field="author", note="kordoc이 author로 알려 줌"),
    ]
    _write_answers(XLSX_NAME, answers, [])


# ---------------------------------------------------------------- PDF

PDF_NAME = "강사계약_샘플.pdf"

PDF_LINES = [
    ("강사 위촉 계약서", 18),
    ("", 11),
    ("성명: 김민수", 11),
    ("주민등록번호: 901301-1000000", 11),
    ("주소: 경기도 가상시 예시로 123, 101동 1001호", 11),
    ("연락처: 010-0000-0001", 11),
    ("이메일: minsu.kim@example.kr", 11),
    ("", 11),
    ("강사료는 아래 계좌로 지급한다.", 11),
]

PDF_TABLE = [
    ("은행", "계좌번호", "예금주"),
    ("가상은행", "000-0000-0000-01", "김민수"),
]

PDF_FOOTER = "문의: 가상연수원 대표번호 1588-0000"
PDF_ANNOT = "김민수 강사 서명 받기 전"

# 실제 업무 PDF처럼 비례폭 한글 글꼴을 넣는다. 내장 "korea" 글꼴은 숫자와 띄어쓰기가
# 전각 너비라 표 칸을 넘치고 kordoc이 낱말을 블록 둘로 나눈다(docs/kordoc_검증기록.md).
MALGUN = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "malgun.ttf"


def _font_args() -> dict:
    if MALGUN.is_file():
        return {"fontname": "malgun", "fontfile": str(MALGUN)}
    return {"fontname": "korea"}


def make_pdf() -> None:
    font = _font_args()
    doc = pymupdf.open()
    page = doc.new_page()  # A4
    y = 72
    for text, size in PDF_LINES:
        if text:
            page.insert_text((72, y), text, fontsize=size, **font)
        y += size + 12

    # 테두리 있는 표: kordoc PDF 표 감지 확인용
    col_w, row_h, x0 = (110, 190, 110), 26, 72
    top = y
    for r, row in enumerate(PDF_TABLE):
        x = x0
        for c, text in enumerate(row):
            rect = pymupdf.Rect(x, top + r * row_h, x + col_w[c], top + (r + 1) * row_h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((x + 6, rect.y1 - 8), text, fontsize=11, **font)
            x += col_w[c]
    y = top + len(PDF_TABLE) * row_h + 30
    page.insert_text((72, y), PDF_FOOTER, fontsize=11, **font)

    annot = page.add_text_annot((400, 72), PDF_ANNOT)
    annot.set_info(title="운영팀")

    doc.set_metadata({"author": "김민수", "title": "강사 위촉 계약서"})
    doc.subset_fonts()  # 쓴 글자만 남겨 파일을 작게
    doc.save(SAMPLES / PDF_NAME, garbage=4, deflate=True)

    answers = [
        _answer("P01", "김민수", "이름", "확실", kind="paragraph", page=1, label="성명"),
        _answer("P02", "901301-1000000", "주민등록번호", "확실", kind="paragraph", page=1, label="주민등록번호",
                note="월 13: 날짜 검증 실패해도 후보"),
        _answer("P03", "경기도 가상시 예시로 123, 101동 1001호", "주소", "검토", kind="paragraph", page=1, label="주소"),
        _answer("P04", "010-0000-0001", "전화", "확실", kind="paragraph", page=1, label="연락처"),
        _answer("P05", "minsu.kim@example.kr", "이메일", "확실", kind="paragraph", page=1, label="이메일"),
        _answer("P06", "000-0000-0000-01", "계좌번호", "확실", kind="table", table=0, row=1, col=1, header="계좌번호"),
        _answer("P07", "김민수", "이름", "확실", kind="table", table=0, row=1, col=2, header="예금주"),
        _answer("P08", "김민수", "이름", "확실", kind="metadata", field="author"),
        _answer("P09", "김민수", "이름", "검토", kind="comment", page=1, note="PDF 주석"),
    ]
    must_not = [
        {"answer_id": "PN01", "value": "1588-0000", "reason": "기관 대표번호(오탐 확인용)",
         "location": {"kind": "paragraph", "page": 1}},
    ]
    _write_answers(PDF_NAME, answers, must_not)


if __name__ == "__main__":
    make_hwpx()
    make_xlsx()
    make_pdf()
    print("샘플 3종과 정답 목록을 만들었습니다.")
