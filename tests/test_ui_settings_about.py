"""설정 창의 정보 묶음(판, 라이선스)과 화면 아래 저작권 문구."""

from PySide6.QtWidgets import QLabel

from privacy_operator.settings import Settings
from privacy_operator.ui.dialogs import SettingsDialog
from privacy_operator.ui.voice import Voice

def test_settings_show_version_and_license(qtbot, tmp_path):
    """AGPL: 화면에서 라이선스와 보증 없음, 소스 위치를 알린다. 판은 pyproject와 같다."""
    import tomllib
    from pathlib import Path

    from privacy_operator import __version__
    from privacy_operator.ui.dialogs import license_file

    dlg = SettingsDialog(Voice("mission"), Settings(vault_dir=str(tmp_path / "v"), workspace_dir=str(tmp_path / "w")),
                         lambda: None)
    qtbot.addWidget(dlg)
    root = Path(__file__).resolve().parents[1]
    assert dlg.version.text().split()[0] == __version__ == tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    texts = " ".join(w.text() for w in dlg.findChildren(QLabel))
    assert "AGPL-3.0" in texts and "보증 없이" in texts and "github.com/amberdrops/privacy-operator" in texts
    assert dlg.license_btn.text() == "라이선스 보기"
    assert license_file(root).read_text(encoding="utf-8").lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE")
    (tmp_path / "LICENSE.txt").write_text("x", encoding="utf-8")
    assert license_file(tmp_path).name == "LICENSE.txt"


def test_bezel_has_copyright_without_all_rights_reserved():
    for name in ("mission", "plain"):
        text = Voice(name).t("app.bezel")
        assert text.startswith("© 2026 Ally Hyerim Lee") and "All rights reserved" not in text


def test_program_name_is_the_korean_name():
    """프로그램 이름은 '개인정보작전실'(2026-11-09). 창 제목과 설치 마법사가 같은 이름을 쓴다."""
    from pathlib import Path

    for name in ("mission", "plain"):
        assert Voice(name).t("app.title") == "개인정보작전실"
    iss = (Path(__file__).resolve().parents[1] / "installer" / "PrivacyOperator.iss").read_text(encoding="utf-8-sig")
    assert "AppName=개인정보작전실" in iss and "프라이버시" not in iss


def test_beta_is_shown_next_to_the_version(qtbot, tmp_path):
    from privacy_operator import BETA, __version__

    dlg = SettingsDialog(Voice("plain"), Settings(vault_dir=str(tmp_path / "v"), workspace_dir=str(tmp_path / "w")),
                         lambda: None)
    qtbot.addWidget(dlg)
    assert dlg.version.text() == (f"{__version__} 베타" if BETA else __version__)
