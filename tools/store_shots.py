"""Microsoft Store 화면 캡처(1920x1080). 지어낸 샘플 문서만 쓴다(samples/).

    .venv\\Scripts\\python tools/store_shots.py      → dist/store/*.png

배율이 125%인 PC 기준으로 창을 1536x864로 띄워 1920x1080으로 찍힌다. 배율이 다르면 STORE_SCALE을 맞춘다.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
OUT = ROOT / "dist" / "store"
# 연수 이야기 없는 일반 문서(samples/demo, 2026-11-10)
SAMPLES = [ROOT / "samples" / "demo" / n for n in ("통학버스_조정결과_샘플.hwpx", "민원회신_샘플.pdf", "체험학습_신청명단_샘플.xlsx")]


def _over(back, front, out: Path) -> None:
    """설정 창을 흐리게 한 본 창 가운데에 얹는다(Store 캡처는 1366x768 이상이어야 한다)."""
    from PySide6.QtGui import QColor, QPainter

    img = back.toImage()
    p = QPainter(img)
    p.fillRect(img.rect(), QColor(0, 0, 0, 70))
    fr = front.toImage()
    p.drawImage((img.width() - fr.width()) // 2, (img.height() - fr.height()) // 2, fr)
    p.end()
    img.save(str(out))


def main() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    import ui_shots

    from privacy_operator.ui.dialogs import SettingsDialog

    scale = float(os.environ.get("STORE_SCALE", app.primaryScreen().devicePixelRatio()))
    w, h = round(1920 / scale), round(1080 / scale)
    OUT.mkdir(parents=True, exist_ok=True)

    def pump(n: int = 20) -> None:
        for _ in range(n):
            app.processEvents()

    for theme, voice in (("pink", "mission"), ("plain", "plain")):
        with tempfile.TemporaryDirectory() as d:
            win, wf = ui_shots.build(Path(d), theme, voice)
            win.resize(w, h)
            win.show()
            pump()
            win.grab().save(str(OUT / f"1_시작_{theme}.png"))
            win.load_files(SAMPLES)
            # 확실 등급을 확정해 코드명과 진척이 채워진 모습으로
            wf.confirm([f.finding_id for f in wf.findings if f.tier == "확실"])
            win.refresh()
            pump()
            win.grab().save(str(OUT / f"2_판정_{theme}.png"))
            if theme == "pink":
                docs = "C:\\Users\\사용자\\Documents\\"  # 캡처용 보기 경로(실제 사용자 이름이 찍히지 않게)
                shown = replace(wf.settings, vault_dir=docs + "작전실금고", workspace_dir=docs + "작전실",
                                restored_dir=docs + "작전실복원")
                dlg = SettingsDialog(win.voice, shown, lambda: None, win)
                dlg.show()
                pump()
                _over(win.grab(), dlg.grab(), OUT / "3_설정_pink.png")
                dlg.close()
            win.close()
    print(OUT)


if __name__ == "__main__":
    main()
