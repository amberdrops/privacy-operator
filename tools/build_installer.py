"""설치 파일 만들기(11단계). 개발 PC에서 돌린다. 인터넷은 쓰지 않는다.

    .venv\\Scripts\\python tools/build_installer.py            # 묶기 + 설치 파일
    .venv\\Scripts\\python tools/build_installer.py --no-setup # 묶은 폴더까지만

1. PyInstaller로 프로그램 본체를 묶는다(dist/PrivacyOperator/PrivacyOperator.exe, _internal/).
2. 묶은 폴더 옆에 kordoc 엔진(engine/node_modules)과 Node(engine/node/node.exe), 에셋, 글꼴을 넣는다.
   프로그램은 소스와 같은 깊이로 경로를 찾으므로(_internal/privacy_operator/... → 묶은 폴더) 코드를 바꾸지 않아도 된다.
3. 자주 묻는 질문(HTML), 라이선스(LICENSE.txt, licenses/, THIRD_PARTY_NOTICES.txt)를 넣는다.
4. Inno Setup으로 dist/PrivacyOperator-Setup-<판>.exe를 만든다.

넣지 않는 것
- Schift(선택 설치, 첫 판 제외. 2026-11-09 결정)
- 엔진의 onnxruntime-node, @huggingface: 스캔 PDF 글자 인식과 수식 인식용이다. OCR은 1.1(2026-11-08 결정)이고,
  KORDOC_OFFLINE=1이라 모델도 받지 않으므로 쓰이지 않는다. 필요할 때만 불러오는 부품이라 없어도 읽기는 된다.
- numpy, PIL: openpyxl이 깔려 있을 때만 끌어오는 부품이다(개발용 묶음에만 있다).
- assets/_source: Flow 원본 그림(에셋을 만드는 재료)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from importlib.metadata import PackageNotFoundError, distribution, requires
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / "PrivacyOperator"
ENGINE_SRC = ROOT / "engine" / "node_modules"
LICENSES = ROOT / "licenses"
ISS = ROOT / "installer" / "PrivacyOperator.iss"

ENGINE_SKIP = {"onnxruntime-node", "@huggingface", ".bin", ".package-lock.json"}
ENGINE_DROP = re.compile(r"\.(map|[cm]?ts|md|markdown)$", re.I)  # .d.ts, .d.cts도 여기에 걸린다
PY_EXCLUDE = ["numpy", "PIL", "pytest", "_pytest", "pytestqt", "imageio", "imageio_ffmpeg", "tkinter",
              "onnxruntime", "networkx", "yaml", "pymupdf4llm", "pymupdf_layout", "psutil", "tabulate",
              "privacy_operator.detect.ai.schift_worker"]
PY_ROOTS = ["pyside6-essentials", "shiboken6", "pymupdf", "openpyxl", "cryptography", "keyring", "kordoc"]
PY_SKIP = {"numpy", "onnxruntime", "networkx", "pyyaml", "pymupdf-layout", "pymupdf4llm", "psutil", "tabulate",
           "pillow"}
LICENSE_FILE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE|AUTHORS|THIRD.?PARTY)[^/]*$", re.I)

# AGPL-3.0: 배포판을 받은 사람이 소스를 받을 수 있어야 한다. GitHub에 올린 뒤 주소를 적는다.
SOURCE_URL = "https://github.com/amberdrops/privacy-operator"


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


# ---------------------------------------------------------------- 1. 본체

def make_icon() -> Path:
    from PIL import Image  # 개발용 묶음(pillow)

    src = Image.open(ROOT / "assets" / "pink" / "crown@2x.png").convert("RGBA")
    side = max(src.size) + 16
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(src, ((side - src.width) // 2, (side - src.height) // 2), src)
    out = BUILD / "icon.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    square.resize((256, 256), Image.LANCZOS).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                              (128, 128), (256, 256)])
    return out


def freeze(icon: Path) -> None:
    import PyInstaller.__main__

    args = [str(ROOT / "tools" / "launcher.py"), "--name", "PrivacyOperator", "--windowed", "--noconfirm",
            "--clean", "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
            "--specpath", str(BUILD), "--paths", str(ROOT / "src"), "--icon", str(icon),
            "--collect-data", "privacy_operator", "--collect-submodules", "privacy_operator"]
    for m in PY_EXCLUDE:
        args += ["--exclude-module", m]
    PyInstaller.__main__.run(args)


# ---------------------------------------------------------------- 2. 엔진과 에셋

def _engine_ignore(folder: str, names: list[str]) -> set[str]:
    """빼는 부품과, 실행에 쓰지 않는 파일(TypeScript 원본과 형식 정의, .map, 설명서). 파일 수가 절반쯤 줄어
    설치가 빨라진다(설치할 때 파일마다 풀고 보안 검사를 한다). 라이선스 파일은 남긴다."""
    skip = {n for n in names if n in ENGINE_SKIP} if Path(folder) == ENGINE_SRC else set()
    return skip | {n for n in names if ENGINE_DROP.search(n) and not LICENSE_FILE.search(n)
                   and (Path(folder) / n).is_file()}


def add_engine() -> None:
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js가 없습니다.")
    shutil.copytree(ENGINE_SRC, APP / "engine" / "node_modules", ignore=_engine_ignore)
    (APP / "engine" / "node").mkdir(parents=True)
    shutil.copy2(node, APP / "engine" / "node" / "node.exe")


def add_assets() -> None:
    # 화면이 쓰지 않는 그림은 싣지 않는다(나눠 쓰기 전의 원판과 만들 때만 쓰는 재료, 약 600KB)
    skip = {"_source", "README.md", "progress-pearls@2x.png", "gems@2x.png", "heart@2x.png"}
    shutil.copytree(ROOT / "assets", APP / "assets", ignore=lambda folder, names: {n for n in names if n in skip})
    fonts = APP / "assets" / "fonts"
    if not (fonts / "OFL.txt").is_file() or not list(fonts.glob("NotoSansKR*.ttf")):
        raise SystemExit("assets/fonts에 Noto Sans KR과 OFL.txt가 없습니다.")


# ---------------------------------------------------------------- 3. 안내 파일

FAQ_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>자주 묻는 질문, 개인정보작전실</title>
<style>
@font-face {{ font-family: "Noto Sans KR"; font-weight: 100 900; src: url("assets/fonts/NotoSansKR-VF.ttf"); }}
:root {{ --paper: #FFFFFF; --ink: #24212A; --sub: #5E5862; --line: #E3DEE3; --head: #F7F4F6; }}
body {{ margin: 0; background: var(--paper); color: var(--ink);
       font: 16px/1.7 "Noto Sans KR", "Malgun Gothic", sans-serif; }}
main {{ max-width: 760px; margin: 0 auto; padding: 40px 20px 80px; }}
h1, h2, h3 {{ font-weight: 600; line-height: 1.4; }}
h2 {{ margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--line); }}
h3 {{ margin-top: 28px; }}
hr {{ display: none; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid var(--line); padding: 8px 12px; text-align: left; vertical-align: top; }}
th {{ background: var(--head); font-weight: 600; }}
code {{ background: var(--head); padding: 1px 6px; border-radius: 4px; font-size: 15px; }}
</style></head>
<body><main>
{body}
</main></body></html>
"""


def add_faq() -> None:
    """docs/05_자주묻는질문.md를 엔진에 들어 있는 markdown-it(kordoc이 쓰는 부품)으로 HTML로 바꾼다."""
    md = (ROOT / "docs" / "05_자주묻는질문.md").read_text(encoding="utf-8")
    script = ("let s='';process.stdin.on('data',d=>s+=d).on('end',()=>"
              "process.stdout.write(require('markdown-it')({html:false}).render(s)))")
    body = subprocess.run([shutil.which("node"), "-e", script], input=md.encode("utf-8"), capture_output=True,
                          check=True, cwd=ENGINE_SRC.parent).stdout.decode("utf-8")
    (APP / "자주묻는질문.html").write_text(FAQ_HTML.format(body=body), encoding="utf-8")


def _py_dists() -> list:
    """설치판에 들어가는 파이썬 부품: 본체가 쓰는 부품과 그 부품이 쓰는 부품(넣지 않는 것은 뺀다)."""
    seen, todo, found = set(), list(PY_ROOTS), []
    while todo:
        name = re.split(r"[ ;<>=!~\[(]", todo.pop())[0].lower().replace("_", "-")
        if name in seen or name in PY_SKIP:
            continue
        seen.add(name)
        try:
            d = distribution(name)
        except PackageNotFoundError:
            continue
        found.append(d)
        for r in requires(name) or []:
            if "extra ==" in r or "linux" in r or "python_version < \"3.1" in r or "python_full_version <" in r:
                continue
            todo.append(r)
    return sorted(found, key=lambda d: d.metadata["Name"].lower())


def _texts(files: list[Path]) -> str:
    return "\n\n".join(f"--- {p.name}\n\n{p.read_text(encoding='utf-8', errors='replace').strip()}" for p in files)


def _node_packages(root: Path):
    for p in sorted(root.iterdir()):
        if p.name.startswith("@") and p.is_dir():
            yield from (q for q in sorted(p.iterdir()) if (q / "package.json").is_file())
        elif (p / "package.json").is_file():
            yield p


def add_licenses() -> None:
    shutil.copy2(ROOT / "LICENSE", APP / "LICENSE.txt")
    shutil.copytree(LICENSES, APP / "licenses", ignore=shutil.ignore_patterns("THIRD_PARTY_NOTICES.txt"))
    parts = [
        "개인정보작전실이 함께 싣고 있는 다른 사람의 소프트웨어와 그 라이선스입니다.",
        "개인정보작전실 자체는 GNU AGPL-3.0으로 배포합니다(LICENSE.txt).",
        f"소스 코드: {SOURCE_URL or '공개 주소는 GitHub에 올린 뒤 이 자리에 적습니다.'}",
        "라이선스 원문: licenses/ 폴더(AGPL-3.0, GPL-3.0, LGPL-3.0, Node.js, Python), 글꼴은 assets/fonts/OFL.txt",
        "",
        "=" * 72,
        "Python 3.12 (PSF License): licenses/Python-LICENSE.txt",
        "Node.js (MIT, 함께 싣는 부품의 라이선스 포함): licenses/Node.js-LICENSE.txt",
        "Noto Sans KR 글꼴, Google과 Adobe (SIL Open Font License 1.1): assets/fonts/OFL.txt",
        "Qt, PySide6 (LGPL-3.0): licenses/LGPL-3.0.txt, licenses/GPL-3.0.txt",
        "  Qt와 PySide6는 _internal/PySide6 폴더에 따로 들어 있어 사용자가 바꿔 끼울 수 있습니다.",
        "PyMuPDF, MuPDF (AGPL-3.0): licenses/AGPL-3.0.txt",
    ]
    parts += ["", "=" * 72, "파이썬 부품", "=" * 72]
    for d in _py_dists():
        meta = d.metadata
        lic = meta.get("License-Expression") or (meta.get("License") or "").splitlines()[0:1]
        lic = lic if isinstance(lic, str) else (lic[0] if lic else "")
        files = [Path(d.locate_file(f)) for f in (d.files or []) if LICENSE_FILE.search(str(f))]
        parts += ["", "#" * 72, f"{meta['Name']} {meta['Version']}  ({lic[:80]})",
                  meta.get("Home-page") or "", _texts([f for f in files if f.is_file()])]
    parts += ["", "=" * 72, "kordoc 엔진(engine/node_modules)", "=" * 72]
    for p in _node_packages(APP / "engine" / "node_modules"):
        pkg = json.loads((p / "package.json").read_text(encoding="utf-8"))
        files = [f for f in sorted(p.iterdir()) if f.is_file() and LICENSE_FILE.search(f.name)]
        files += sorted(f for f in (p / "THIRD_PARTY").glob("*") if f.is_file())  # kordoc이 옮겨 쓴 부품의 고지
        lic = pkg.get("license") if isinstance(pkg.get("license"), str) else ""
        parts += ["", "#" * 72, f"{pkg.get('name', p.name)} {pkg.get('version', '')}  ({lic})", _texts(files)]
    notices = "\n".join(parts) + "\n"
    (APP / "THIRD_PARTY_NOTICES.txt").write_text(notices, encoding="utf-8")
    # 저장소에도 둔다. Store판은 설치 폴더(WindowsApps)를 열기 어려워, Store 사용 조건에서 이 파일의 GitHub 주소를 알린다
    (LICENSES / "THIRD_PARTY_NOTICES.txt").write_text(notices, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- 4. 설치 파일

def find_iscc() -> Path:
    bases = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"] + [
        Path(os.environ[k]) for k in ("ProgramFiles(x86)", "ProgramFiles") if k in os.environ]
    for base in bases:
        p = base / "Inno Setup 6" / "ISCC.exe"
        if p.is_file():
            return p
    raise SystemExit("Inno Setup 6(ISCC.exe)이 없습니다.")


def make_setup(icon: Path) -> Path:
    v = version()
    subprocess.run([str(find_iscc()), f"/DAppVersion={v}", f"/DDistDir={APP}", f"/DOutDir={DIST}",
                    f"/DIconFile={icon}", str(ISS)], check=True)
    return DIST / f"PrivacyOperator-Setup-{v}.exe"


# ---------------------------------------------------------------- 5. Microsoft Store용 MSIX

STORE_IDENTITY = ROOT / "installer" / "store_identity.json"  # Partner Center의 제품 ID(공개 값)
MSIX_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap rescap">
  <Identity Name="{name}" Publisher="{publisher}" Version="{version}.0" ProcessorArchitecture="x64"/>
  <Properties>
    <DisplayName>{display}</DisplayName>
    <PublisherDisplayName>{publisher_display}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0"/>
  </Dependencies>
  <Resources>
    <Resource Language="ko-KR"/>
  </Resources>
  <Applications>
    <Application Id="PrivacyOperator" Executable="PrivacyOperator.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="{display}" Description="{description}" BackgroundColor="transparent"
                          Square150x150Logo="Assets\\Square150x150Logo.png" Square44x44Logo="Assets\\Square44x44Logo.png"/>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust"/>
  </Capabilities>
</Package>
"""


def find_makeappx() -> Path:
    """Windows SDK의 makeappx.exe. MAKEAPPX 환경변수가 있으면 그것을 쓴다(SDK 빌드 도구 묶음을 풀어 쓸 때)."""
    if os.environ.get("MAKEAPPX"):
        return Path(os.environ["MAKEAPPX"])
    kits = Path(os.environ.get("ProgramFiles(x86)", "")) / "Windows Kits" / "10" / "bin"
    found = sorted(kits.glob("10.*/x64/makeappx.exe"))
    if not found:
        raise SystemExit("makeappx.exe가 없습니다. Windows SDK를 깔거나 MAKEAPPX에 경로를 주세요.")
    return found[-1]


def msix_assets(folder: Path) -> None:
    """Store와 시작 메뉴 그림. 왕관(핑크 테마 엠블럼)을 투명 바탕 정사각형에 앉힌다."""
    from PIL import Image

    src = Image.open(ROOT / "assets" / "pink" / "crown@2x.png").convert("RGBA")

    def square(px: int, pad: float) -> Image.Image:
        canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        inner = int(px * (1 - 2 * pad))
        scale = inner / max(src.size)
        img = src.resize((max(1, round(src.width * scale)), max(1, round(src.height * scale))), Image.LANCZOS)
        canvas.paste(img, ((px - img.width) // 2, (px - img.height) // 2), img)
        return canvas

    folder.mkdir(parents=True, exist_ok=True)
    square(50, 0.06).save(folder / "StoreLogo.png")
    square(150, 0.18).save(folder / "Square150x150Logo.png")
    square(44, 0.06).save(folder / "Square44x44Logo.png")
    for px in (16, 24, 32, 48, 256):  # 작업 표시줄, 시작 메뉴 목록: 바탕 판 없이
        square(px, 0.04).save(folder / f"Square44x44Logo.targetsize-{px}_altform-unplated.png")


def make_msix() -> Path:
    ident = json.loads(STORE_IDENTITY.read_text(encoding="utf-8"))
    stage = BUILD / "msix"
    remove(stage)
    shutil.copytree(APP, stage)
    msix_assets(stage / "Assets")
    (stage / "AppxManifest.xml").write_text(MSIX_MANIFEST.format(
        name=ident["name"], publisher=ident["publisher"], publisher_display=ident["publisher_display_name"],
        display=ident["display_name"], description=ident["description"], version=version()), encoding="utf-8")
    out = DIST / f"PrivacyOperator-{version()}.msix"
    out.unlink(missing_ok=True)
    subprocess.run([str(find_makeappx()), "pack", "/d", str(stage), "/p", str(out), "/o"], check=True,
                   stdout=subprocess.DEVNULL)
    return out


def size(path: Path) -> str:
    total = path.stat().st_size if path.is_file() else sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return f"{total / 1024 / 1024:.0f}MB"


def remove(path: Path, tries: int = 10) -> None:
    """지난 묶음 지우기. 방금 만든 파일은 Windows 보안 검사나 편집기가 잠깐 잡고 있어 지우기가 실패하곤 한다."""
    for i in range(tries):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-setup", action="store_true", help="설치 파일은 만들지 않고 묶은 폴더까지만")
    ap.add_argument("--msix", action="store_true", help="Microsoft Store용 MSIX도 만든다(installer/store_identity.json)")
    args = ap.parse_args()
    remove(APP)
    step("아이콘")
    icon = make_icon()
    step("PyInstaller로 본체 묶기")
    freeze(icon)
    step("엔진, Node, 에셋 넣기")
    add_engine()
    add_assets()
    step("자주 묻는 질문, 라이선스 안내")
    add_faq()
    add_licenses()
    print(f"묶은 폴더: {APP} ({size(APP)})")
    if not args.no_setup:
        step("Inno Setup으로 설치 파일 만들기")
        out = make_setup(icon)
        print(f"설치 파일: {out} ({size(out)})")
    if args.msix:
        step("Microsoft Store용 MSIX 만들기")
        out = make_msix()
        print(f"MSIX: {out} ({size(out)})")


if __name__ == "__main__":
    sys.exit(main())
