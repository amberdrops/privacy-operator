"""HWPX(와 HWP) 원본 형식 저장.

1. HWP는 kordoc `to-hwpx`로 서식 그대로 HWPX로 바꾼다. HWP 안의 메모와 미리보기는 압축된 이진 기록이라
   고치고 검사할 수 없어서다(docs/kordoc_검증기록.md). 결과 형식은 .hwpx가 된다.
2. kordoc `patch`로 본문과 표를 바꾼다(글자 조각이 나뉘어 있어도 문단 단위로 다시 쓴다).
3. patch가 건드리지 않는 곳을 직접 바꾼다: 메모, 머리말과 꼬리말, 문서 속성(content.hpf),
   미리보기 글(Preview/PrvText.txt). 미리보기 그림(Preview/PrvImage.png)은 같은 크기의 흰 그림으로 바꾼다.

작업 파일은 모두 workdir(임시 폴더) 안에 둔다. 부르는 쪽이 끝나면 지운다.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from kordoc import KordocClient

from privacy_operator.reader.engine import FORCED_ENV, NO_WINDOW, find_cli, find_node, make_config
from privacy_operator.writer.plan import ReplacementPlan
from privacy_operator.writer.xmlsweep import sweep

log = logging.getLogger(__name__)

_XML_SUFFIXES = (".xml", ".hpf", ".rdf")
PREVIEW_IMAGE = "Preview/PrvImage.png"


class HwpxWriteError(RuntimeError):
    pass


def _engine(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, **FORCED_ENV}
    return subprocess.run([find_node(), str(find_cli()), *args], cwd=cwd, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300,
                          creationflags=NO_WINDOW)


class PendingValidate:
    """kordoc 구조 검사(창 없이). 만들자마자 돌기 시작하고 finish가 끝을 기다린다."""

    def __init__(self, dst: Path, cwd: Path):
        env = {**os.environ, **FORCED_ENV}
        self.proc = subprocess.Popen([find_node(), str(find_cli()), "validate", str(dst)], cwd=cwd, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=NO_WINDOW)

    def finish(self) -> None:
        """끝날 때까지 기다린다. 구조가 깨졌으면 HwpxWriteError."""
        try:
            code = self.proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            self.cancel()
            raise HwpxWriteError("HWPX_INVALID") from None
        if code != 0:
            raise HwpxWriteError("HWPX_INVALID")

    def cancel(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def blank_png(data: bytes) -> bytes:
    """같은 크기의 흰 그림."""
    import pymupdf

    try:
        src = pymupdf.Pixmap(data)
        w, h = src.width, src.height
    except Exception:  # noqa: BLE001 - 깨진 그림이면 작은 그림으로
        w, h = 16, 16
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.clear_with(255)
    return pix.tobytes("png")


def _decode(data: bytes) -> tuple[str, str] | None:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(enc)
            return text, enc
        except UnicodeDecodeError:
            continue
    return None


def write_hwpx(src: Path, dst: Path, plan: ReplacementPlan, workdir: Path, is_hwp: bool = False, reader=None) -> None:
    """reader(DocumentReader)를 주면 그 엔진으로 마크다운을 읽는다. 없으면 엔진을 새로 켠다(0.8초)."""
    base = workdir / "source.hwpx"
    if is_hwp:
        hwp = workdir / "source.hwp"
        shutil.copyfile(src, hwp)
        r = _engine("to-hwpx", str(hwp), "-o", str(base), cwd=workdir)
        if r.returncode != 0 or not base.is_file():
            raise HwpxWriteError("HWP_CONVERT_FAILED")
    else:
        shutil.copyfile(src, base)

    # 2. 본문과 표: 마크다운을 바꿔 patch
    if reader is not None and hasattr(reader, "markdown"):
        md = reader.markdown(base)
    else:
        with KordocClient(make_config()) as client:
            md = client.parse(str(base)).raw.get("markdown") or ""
    edited, n = plan.apply(md)
    patched = base
    if n:
        (workdir / "edited.md").write_text(edited, encoding="utf-8")
        out = workdir / "patched.hwpx"
        r = _engine("patch", str(base), str(workdir / "edited.md"), "-o", str(out), "--silent", "--json", cwd=workdir)
        if r.returncode == 0 and out.is_file():
            patched = out
        else:  # 제자리 반영을 못 하면 아래 직접 바꾸기에 맡긴다. 검사가 남은 값을 잡는다
            log.warning("kordoc patch 실패(종료 코드 %s), 직접 바꾸기로 진행", r.returncode)

    # 3. 나머지: zip 안의 모든 글
    with zipfile.ZipFile(patched) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name = info.filename
            if name.lower().endswith(_XML_SUFFIXES):
                data = sweep(data.decode("utf-8"), plan.replace)[0].encode("utf-8")
            elif name.lower().endswith(".txt"):
                decoded = _decode(data)
                if decoded:
                    text, enc = decoded
                    data = plan.replace(text).encode("utf-16" if enc == "utf-16" else "utf-8")
            elif name == PREVIEW_IMAGE:
                data = blank_png(data)
            zout.writestr(info, data)  # mimetype은 압축 없이 맨 앞 그대로

    PendingValidate(dst, workdir).finish()
