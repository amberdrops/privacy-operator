# 개인정보작전실

개인정보가 담긴 업무 문서를 **AI에 올리기 전에**, 내 컴퓨터 안에서 이름과 연락처 같은 개인정보를 찾아 `[이름-01]` 같은 가명코드로 바꿉니다. AI 작업이 끝난 결과물을 다시 넣으면 가명코드를 원래 이름으로 되돌립니다.

- 한글(HWP, HWPX), PDF, 엑셀(XLSX)을 넣을 수 있습니다.
- 인터넷에 연결하지 않습니다. 문서는 내 컴퓨터 밖으로 나가지 않습니다.
- 원본 파일은 바꾸지 않고 항상 새 파일을 만듭니다.

Microsoft Store에서도 받을 수 있습니다. Store판과 아래 설치 파일판은 같은 프로그램이고, 다른 점은 [AI 정찰 모델](#ai-정찰-모델-선택)을 설치할 수 있느냐 하나입니다.

## 설치

1. [Releases](https://github.com/amberdrops/privacy-operator/releases)에서 `PrivacyOperator-Setup-1.0.0.exe`를 내려받습니다. 써 보시고 잘못 찾거나 놓친 것을 [Issues](https://github.com/amberdrops/privacy-operator/issues)에 알려 주세요.
2. 두 번 눌러 설치합니다. Windows 10, 11 (64비트)에서 돌아가고, 관리자 권한은 없어도 됩니다.
3. "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행**을 누릅니다. 아직 코드서명을 하지 않아서 뜨는 창입니다.

프로그램을 지워도 금고(대응표), 작업 폴더, 복원본은 지우지 않습니다.

## 쓰는 법

1. **투입**: 문서를 창에 끌어다 놓습니다.
2. **판정**: 프로그램이 찾은 개인정보 후보를 하나씩 보고 확정하거나 제외합니다. 프로그램은 스스로 확정하지 않습니다.
3. **작전 개시**: 확정한 값을 가명코드로 바꾼 문서가 `문서\작전실\to_ai` 폴더에 생깁니다. 이 문서를 AI에 올립니다.
4. **귀환**: AI가 만든 결과물을 `문서\작전실\from_ai` 폴더에 넣고 귀환을 누르면 실명으로 되돌린 파일이 생깁니다.

가명코드와 원래 값의 짝(대응표)은 내가 정한 비밀번호로 암호화해 금고 폴더에만 둡니다. 비밀번호를 잊으면 되돌릴 수 없습니다.

궁금한 점은 [자주 묻는 질문](docs/05_자주묻는질문.md)에 모았습니다. 설치하면 시작 메뉴에서도 열 수 있습니다.

## AI 정찰 모델 (선택)

규칙만으로도 개인정보를 찾지만, 모델을 설치하면 `어제 윤서진과 이야기했다`처럼 직위나 이름표가 없는 이름과 주소를 더 찾아 줍니다. 찾은 것은 '검토'로 올라올 뿐이고, 확정은 사람이 합니다.

- **설치 파일판에만 있습니다.** 설정의 **모델 설치**를 누르면 약 1GB를 내려받습니다. 이때 한 번만 인터넷에 연결하고, 그 뒤로는 인터넷 없이 내 컴퓨터 안에서만 돕니다. Store판에는 이 기능이 없습니다.
- 모델은 Schift 사가 공개한 [schift-ko-pii](https://huggingface.co/schift-io/schift-ko-pii-v7)입니다. 설치 파일에는 들어 있지 않으며, 설치 도구가 Schift의 저장소(Hugging Face)에서, 실행 도구는 PyTorch와 PyPI의 공식 저장소에서 직접 받습니다.
- 이 모델은 개인정보작전실(AGPL-3.0)과 따로 도는 별도 구성요소이고 [Schift License v2.0](licenses/Schift-License-2.0.txt)을 따릅니다. 연구, 교육, 평가, 개인, 비영리 사용은 무료이고, 연 매출이 미화 1천만 달러를 넘는 곳의 상업적 사용은 Schift의 별도 허락이 필요합니다.
- 설정의 **모델 지우기**로 지울 수 있고, 프로그램을 제거하면 함께 지워집니다.

## 알아 둘 점

- 스캔한 PDF처럼 글자 없이 그림으로만 된 쪽, 문서 속 그림과 도형 안의 글자는 아직 읽지 못합니다. 이런 곳은 화면에 **사각지대**로 알려 주고, 보낼 문서에 넣지 않습니다.
- 자동 탐지는 사람의 판정을 돕는 도구입니다. 결과물을 AI에 올리기 전에 한 번 더 살펴보세요.
- 이 프로그램은 보증 없이 제공합니다.

## 소스에서 실행하기

Python 3.11 이상과 Node.js 20 이상이 필요합니다.

```
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
cd engine && npm install --ignore-scripts && cd ..
.venv\Scripts\python -m privacy_operator
```

- 테스트: `.venv\Scripts\python -m pytest`
- 설치 파일 만들기: `.venv\Scripts\python tools/build_installer.py` (Inno Setup 6 필요)

실제 개인정보가 담긴 문서를 저장소에 넣지 마세요. 테스트 문서는 모두 지어낸 값입니다(`samples/`).

## 라이선스

GNU AGPL-3.0 ([LICENSE](LICENSE)). 같은 조건으로 고치거나 나눠 줄 수 있습니다.

함께 쓰는 오픈소스: [kordoc](https://github.com/chrisryugj/kordoc)(문서 읽기, MIT), PyMuPDF(PDF 글자 지우기, AGPL-3.0), PySide6(화면, LGPL-3.0), openpyxl(MIT), cryptography(Apache-2.0, BSD), keyring(MIT), Node.js(MIT), Noto Sans KR 글꼴(OFL-1.1). 설치판에는 전체 고지가 `THIRD_PARTY_NOTICES.txt`로 들어 있습니다.

© 2026 Ally Hyerim Lee (알리)
