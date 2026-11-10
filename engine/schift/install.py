"""Schift 설치(선택). 인터넷이 되는 PC에서 한 번 돌린다. 프로그램 본체(src/)가 아니라 설치 도구다.

    py -3.12 engine/schift/install.py

1. engine/schift/.venv 에 파이썬 환경을 만든다(본체 .venv와 따로).
2. torch CPU 판과 requirements.txt의 고정 판을 설치한다.
3. 모델 schift-io/schift-ko-pii-v7 을 MODEL_REVISION 판으로 engine/schift/hf_home 에 받는다.
   오프라인에서 찾는 main 꼬리표를 이 판으로 적어 둔다(모델 저장소가 바뀌어도 이 판만 쓴다).
4. 작업 프로세스를 통신을 막은 채로 띄워 모델이 읽히는지 확인한다.

설치가 끝난 engine/schift 폴더는 인터넷 없는 PC에 그대로 복사해 쓸 수 있다(11단계).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VENV = HERE / ".venv"
HF_HOME = HERE / "hf_home"
PY = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

MODEL_ID = "schift-io/schift-ko-pii-v7"
MODEL_REVISION = "9d9bc145c57371fd5fd70575b47f37af47a39728"  # 2026-11-07 코드와 가중치를 열어 확인한 판
TORCH = "torch==2.15.0"
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


def run(*args: str, env: dict | None = None) -> None:
    print(">", " ".join(args))
    subprocess.run(args, check=True, env=env)


def main() -> int:
    if not PY.is_file():
        run(sys.executable, "-m", "venv", str(VENV))
    run(str(PY), "-m", "pip", "install", "--upgrade", "pip")
    run(str(PY), "-m", "pip", "install", "--index-url", TORCH_INDEX, TORCH)
    run(str(PY), "-m", "pip", "install", "-r", str(HERE / "requirements.txt"))

    env = {**os.environ, "HF_HOME": str(HF_HOME), "HF_HUB_DISABLE_TELEMETRY": "1"}
    code = ("from huggingface_hub import snapshot_download; "
            f"print(snapshot_download({MODEL_ID!r}, revision={MODEL_REVISION!r}))")
    run(str(PY), "-I", "-c", code, env=env)
    refs = HF_HOME / "hub" / ("models--" + MODEL_ID.replace("/", "--")) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(MODEL_REVISION, encoding="ascii")

    # 확인: 통신을 막은 작업 프로세스가 모델을 읽는가
    worker = ROOT / "src" / "privacy_operator" / "detect" / "ai" / "schift_worker.py"
    check = subprocess.run([str(PY), "-I", str(worker)], input=json.dumps({"texts": ["확인"]}).encode() + b"\n",
                           capture_output=True, env={**os.environ, "HF_HOME": str(HF_HOME)}, timeout=300)
    first = json.loads(check.stdout.decode("ascii").splitlines()[0])
    if not first.get("ready"):
        print("모델을 읽지 못했습니다:", first)
        return 1
    print("설치 완료. 설정 화면에서 '이름과 주소를 AI로 더 찾기 (Schift)'를 켜세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
