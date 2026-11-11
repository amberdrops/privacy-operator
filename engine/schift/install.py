"""Schift 설치(선택). 인터넷이 되는 PC에서 한 번 돌린다. 프로그램 본체(src/)가 아니라 설치 도구다.

    py -3.12 engine/schift/install.py

1. engine/schift/.venv 에 파이썬 환경을 만든다(본체 .venv와 따로).
2. torch CPU 판과 requirements.txt의 고정 판을 설치한다.
3. 모델 schift-io/schift-ko-pii-v7 을 MODEL_REVISION 판으로 engine/schift/hf_home 에 받는다.
   오프라인에서 찾는 main 꼬리표를 이 판으로 적어 둔다(모델 저장소가 바뀌어도 이 판만 쓴다).
4. 작업 프로세스를 통신을 막은 채로 띄워 모델이 읽히는지 확인한다.

설치가 끝난 engine/schift 폴더는 인터넷 없는 PC에 그대로 복사해 쓸 수 있다(11단계).

설치판의 '모델 설치' 단추가 부르는 방식(프로그램 본체는 통신하지 않고 이 도구를 따로 띄운다):

    <깨끗한 내장형 파이썬>/python.exe install.py --embedded <내장형 파이썬 폴더> --home <깔 곳>

내장형 파이썬을 <home>/python 으로 복사하고(표준 라이브러리만 쓴다), 같은 폴더의 pip.whl로 torch와 부품을 설치하고,
모델을 <home>/hf_home 에 받는다. 단계마다 표준 출력에 JSON 한 줄만 쓴다(pip과 내려받기의 출력은 표준 오류).
    {"step": 2, "of": 5, "key": "runtime"} ... {"done": true}  또는  {"error": "<코드>"}와 종료 코드 1
오류 코드: PATH_TOO_LONG, NO_SPACE, DOWNLOAD_FAILED, VERIFY_FAILED, SETUP_BROKEN. 다시 실행하면 이어서 받는다.
"""

from __future__ import annotations

import argparse
import datetime
import errno
import hashlib
import json
import os
import shutil
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
MIN_FREE_BYTES = 2 * 1024**3
# 깔리는 파일 가운데 가장 긴 상대 경로(torch 2.15.0, transformers 5.22.2에서 130자)에 여유를 둔 값.
# Windows는 긴 경로 설정이 꺼져 있으면 전체 경로가 259자를 넘는 파일을 만들지 못한다(2026-11-11 진짜 설치에서 겪음)
LONGEST_RELATIVE_PATH = 140
WINDOWS_MAX_PATH = 259
STEPS = ("python", "runtime", "packages", "model", "verify")


def run(*args: str, env: dict | None = None) -> None:
    print(">", " ".join(args))
    subprocess.run(args, check=True, env=env)


def find_worker() -> Path:
    """작업 프로세스 파일: 이 파일 옆(설치판) 먼저, 없으면 저장소 src(소스)."""
    beside = HERE / "schift_worker.py"
    return beside if beside.is_file() else ROOT / "src" / "privacy_operator" / "detect" / "ai" / "schift_worker.py"


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
    worker = find_worker()
    check = subprocess.run([str(PY), "-I", str(worker)], input=json.dumps({"texts": ["확인"]}).encode() + b"\n",
                           capture_output=True, env={**os.environ, "HF_HOME": str(HF_HOME)}, timeout=300)
    first = json.loads(check.stdout.decode("ascii").splitlines()[0])
    if not first.get("ready"):
        print("모델을 읽지 못했습니다:", first)
        return 1
    print("설치 완료. 설정 화면에서 '이름과 주소를 AI로 더 찾기 (Schift)'를 켜세요.")
    return 0


# ---------------------------------------------------------------- 내장형 파이썬 방식(--embedded)

class SetupError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def emit(obj: dict) -> None:
    """표준 출력에는 JSON 한 줄만 쓴다."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def long_paths_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except (ImportError, OSError):
        return False


def path_too_long(home: Path, enabled: bool | None = None) -> bool:
    """깔 곳이 너무 깊어 부품 파일의 전체 경로가 Windows 제한을 넘는가. 넘으면 pip이 중간에 실패한다."""
    if sys.platform != "win32":
        return False
    if long_paths_enabled() if enabled is None else enabled:
        return False
    deepest = len(str(home.resolve() / "python" / "Lib" / "site-packages")) + 1 + LONGEST_RELATIVE_PATH
    return deepest > WINDOWS_MAX_PATH


def fix_pth(py_root: Path) -> None:
    """python*._pth 에 Lib\\site-packages 를 더하고 import site 를 켠다(없으면 pip이 설치한 부품을 못 찾는다)."""
    pths = sorted(py_root.glob("python*._pth"))
    if not pths:
        raise SetupError("SETUP_BROKEN")
    for pth in pths:
        out, has_site, has_pkgs = [], False, False
        for line in pth.read_text(encoding="utf-8").splitlines():
            body = line.strip().lstrip("#").strip()
            if body == "import site":
                line, has_site = "import site", True
            elif body.replace("/", "\\") == "Lib\\site-packages" and not line.strip().startswith("#"):
                has_pkgs = True
            out.append(line)
        if not has_pkgs:
            at = next((i for i, v in enumerate(out) if v.strip() == "import site"), len(out))
            out.insert(at, "Lib\\site-packages")
        if not has_site:
            out.append("import site")
        pth.write_text("\n".join(out) + "\n", encoding="utf-8")


def step_python(embedded: Path, home: Path) -> Path:
    """1단계. 내장형 파이썬을 <home>/python 으로 복사(이미 있으면 건너뜀)하고 _pth 를 고친다. 복사본의 python.exe 를 돌려준다."""
    exe = "python.exe" if sys.platform == "win32" else "bin/python"
    if not (embedded / ("python.exe" if sys.platform == "win32" else "python")).is_file():
        raise SetupError("SETUP_BROKEN")
    if not (embedded / "pip.whl").is_file():
        raise SetupError("SETUP_BROKEN")
    target = home / "python"
    if not (target / exe).is_file():
        try:
            if target.exists():
                shutil.rmtree(target)  # 받다 만 복사본
            shutil.copytree(embedded, target, ignore=shutil.ignore_patterns("pip.whl"))
        except OSError as e:
            raise SetupError("NO_SPACE" if e.errno == errno.ENOSPC else "SETUP_BROKEN") from None
    fix_pth(target)
    return target / exe


def pip_install(py: Path, pip_whl: Path, *args: str) -> None:
    """pip 은 깔지 않고 바퀴 파일을 바로 실행한다. 출력은 표준 출력에 섞이지 않게 버린다(오류는 표준 오류)."""
    cmd = [str(py), str(pip_whl / "pip"), "install", "--no-warn-script-location", "--disable-pip-version-check", *args]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def download_model(py: Path, home: Path) -> None:
    env = {**os.environ, "HF_HOME": str(home / "hf_home"), "HF_HUB_DISABLE_TELEMETRY": "1"}
    code = ("from huggingface_hub import snapshot_download; "
            f"snapshot_download({MODEL_ID!r}, revision={MODEL_REVISION!r})")
    subprocess.run([str(py), "-I", "-c", code], check=True, env=env, stdout=subprocess.DEVNULL)
    refs = home / "hf_home" / "hub" / ("models--" + MODEL_ID.replace("/", "--")) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(MODEL_REVISION, encoding="ascii")


def verify_model(py: Path, home: Path) -> bool:
    """통신을 막은 작업 프로세스가 모델을 읽는가."""
    try:
        check = subprocess.run([str(py), "-I", str(find_worker())],
                               input=json.dumps({"texts": ["확인"]}).encode() + b"\n", capture_output=True,
                               env={**os.environ, "HF_HOME": str(home / "hf_home")}, timeout=600)
        first = json.loads(check.stdout.decode("ascii").splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return False
    return bool(first.get("ready"))


def write_installed(home: Path) -> None:
    req = (HERE / "requirements.txt").read_bytes() if (HERE / "requirements.txt").is_file() else b""
    mark = {"model": MODEL_ID, "revision": MODEL_REVISION, "torch": TORCH,
            "packages": hashlib.sha256(req).hexdigest()[:16], "date": datetime.date.today().isoformat()}
    (home / "installed.json").write_text(json.dumps(mark, indent=2), encoding="utf-8")


def main_embedded(embedded: Path, home: Path) -> int:
    """5단계. 실패하면 {"error": 코드}를 쓰고 1, 끝나면 {"done": true}를 쓰고 0."""
    n = len(STEPS)
    try:
        if path_too_long(home):
            raise SetupError("PATH_TOO_LONG")
        if free_bytes(home) < MIN_FREE_BYTES:
            raise SetupError("NO_SPACE")
        home.mkdir(parents=True, exist_ok=True)
        (home / "installed.json").unlink(missing_ok=True)  # 다시 받는 동안은 설치된 것으로 보지 않는다
        emit({"step": 1, "of": n, "key": "python"})
        py = step_python(embedded, home)
        pip_whl = embedded / "pip.whl"
        try:
            emit({"step": 2, "of": n, "key": "runtime"})
            pip_install(py, pip_whl, "--index-url", TORCH_INDEX, TORCH)
            emit({"step": 3, "of": n, "key": "packages"})
            pip_install(py, pip_whl, "-r", str(HERE / "requirements.txt"))
            emit({"step": 4, "of": n, "key": "model"})
            download_model(py, home)
        except (subprocess.CalledProcessError, OSError):
            raise SetupError("NO_SPACE" if free_bytes(home) < 200 * 1024**2 else "DOWNLOAD_FAILED") from None
        emit({"step": 5, "of": n, "key": "verify"})
        if not verify_model(py, home):
            raise SetupError("VERIFY_FAILED")
        write_installed(home)
    except SetupError as e:
        emit({"error": e.code})
        return 1
    emit({"done": True})
    return 0


def cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedded", type=Path, help="깨끗한 내장형 파이썬 폴더(설치판 방식)")
    ap.add_argument("--home", type=Path, help="깔 곳(--embedded와 함께)")
    args = ap.parse_args(argv)
    if args.embedded:
        if not args.home:
            ap.error("--home 이 필요합니다")
        return main_embedded(args.embedded, args.home)
    return main()


if __name__ == "__main__":
    sys.exit(cli())
