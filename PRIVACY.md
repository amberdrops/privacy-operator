# 개인정보 처리방침

개인정보작전실(Privacy Operator)은 사용자의 개인정보를 수집하지 않습니다.

- **수집하는 정보: 없음.** 이름, 이메일, 사용 기록, 오류 보고, 기기 정보 등 어떤 정보도 개발자에게 보내지 않습니다.
- **인터넷 연결: 하지 않음.** 프로그램은 내 컴퓨터 안에서만 동작합니다. 문서 읽기, 개인정보 찾기, 가명코드로 바꾸기, 되돌리기가 모두 내 컴퓨터에서 이루어집니다. 예외는 아래 '모델 설치(선택)' 하나이며, 사용자가 직접 눌렀을 때만 연결합니다.
- **문서와 대응표.** 사용자가 넣은 문서와 결과물은 사용자가 정한 폴더(기본: 문서 폴더 아래 `작전실`, `작전실금고`, `작전실복원`)에만 저장됩니다. 가명코드와 원래 값의 짝(대응표)은 사용자가 정한 비밀번호로 암호화해 금고 폴더에만 둡니다.
- **비밀번호 기억하기(선택).** 켜면 금고 비밀번호를 Windows 자격 증명 관리자에 저장합니다. 이 컴퓨터 밖으로 나가지 않습니다.
- **AI 정찰(선택).** 켜면 내 컴퓨터에 설치한 한국어 개인정보 탐지 모델(schift-ko-pii)이 내 컴퓨터 안에서만 돌며, 통신 없이 작동합니다. 0.9.2까지의 버전에 있던 Ollama 모델 선택은 내 컴퓨터 안의 주소(127.0.0.1)로만 연결하며, 인터넷의 AI 서비스(클라우드 모델)는 선택할 수 없게 막혀 있습니다.
- **모델 설치(선택, GitHub 설치 파일판에만 있음).** 설정에서 '모델 설치'를 누르면, 프로그램과 따로 도는 설치 도구가 이때 한 번 인터넷에 연결해 탐지 모델(schift-ko-pii)과 그 실행 도구를 내려받습니다. 받는 곳은 PyTorch 내려받기 서버(download.pytorch.org), 파이썬 꾸러미 저장소(pypi.org), Hugging Face(huggingface.co)입니다. 문서 내용이나 개인정보는 보내지 않습니다. 다만 파일을 내려받는 여느 접속처럼 그 서버에는 접속 기록(IP 주소, 운영체제와 프로그램 버전 같은 기술 정보)이 남을 수 있으며, 이는 각 서비스의 개인정보 처리방침을 따릅니다. 누르지 않으면 연결하지 않습니다. Microsoft Store판에는 이 기능이 없습니다.
- **AI에 올리는 것.** 결과물을 AI 서비스에 올리는 일은 사용자가 직접 합니다. 그때 올라가는 것은 개인정보가 가명코드로 바뀐 문서이며, 그 서비스의 개인정보 처리방침을 따릅니다.

문의: https://github.com/amberdrops/privacy-operator/issues

시행일: 2026년 11월 9일 (2026년 11월 11일 고침: 모델 설치 항목을 더함)

---

## Privacy Policy (English)

Privacy Operator does not collect any personal data. The app works entirely on your computer and does not connect to the internet. Documents, results and the encrypted mapping file stay in folders you choose on your own device. The optional "remember password" feature stores the vault password in Windows Credential Manager on this device. The optional AI scan runs a local detection model entirely on your computer, with network access blocked. In versions up to 0.9.2, the optional Ollama model setting connects only to a local Ollama model at 127.0.0.1; cloud models cannot be selected. The one exception to "no internet" is the optional model setup in the GitHub installer edition: only when you press "Install model" in Settings, a separate setup tool connects once to download the detection model (schift-ko-pii) and its runtime from download.pytorch.org, pypi.org and huggingface.co. No document content or personal data is sent; as with any download, those servers may log connection data (IP address, OS and client version) under their own privacy policies. The Microsoft Store edition does not include this feature. Uploading the pseudonymized results to an AI service is something you do yourself, under that service's privacy policy.

Contact: https://github.com/amberdrops/privacy-operator/issues — Effective November 9, 2026 (revised November 11, 2026: added the model setup item).
