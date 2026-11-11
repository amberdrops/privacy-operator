"""10단계: Flow 디자인 에셋 입히기.

- 테마 파일이 가리키는 에셋이 모두 있고, 9분할 자를 곳이 실제 그림과 맞는다.
- 주 단추 그림 위 흰 글씨 대비가 4.5:1 이상이다(색 토큰이 아니라 실제 그림 색으로 잰다).
- 띠 질감의 평균 색이 테마 band 토큰과 같다(글씨 대비가 토큰으로 잰 값 그대로).
- 에셋이 없으면 색 토큰으로 그린 기본 모양으로 돌아간다. 테마를 바꾸면 창을 다시 열지 않아도 바뀐다.
- 작전 개시 직후 핑크는 반짝임, 단정한 모니터는 깜빡임이 한 번 나오고 사라진다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from privacy_operator.alias.vault import Vault
from privacy_operator.mission.package import ProcessingRecord
from privacy_operator.settings import Settings
from privacy_operator.ui.main_window import MainWindow
from privacy_operator.ui.style import BUTTON_HEIGHT, stylesheet
from privacy_operator.ui.theme import _luminance, assets_dir, available_themes, contrast, load_theme
from privacy_operator.ui.voice import Voice
from privacy_operator.ui.widgets import FlashOverlay, SparkleOverlay
from privacy_operator.workflow import MissionWorkflow

SHOTS = Path(__file__).resolve().parent / "screenshots"
FILE_KEYS = ("bezel", "btn_primary", "btn_primary_hover", "btn_primary_pressed", "btn_secondary", "band_texture")


def _rgb(img: QImage, x: int, y: int) -> tuple[int, int, int, int]:
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue(), c.alpha()


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(v):02X}" for v in rgb[:3])


# ---------------------------------------------------------------- 에셋 파일과 9분할 값

@pytest.mark.parametrize("name", available_themes())
def test_theme_assets_exist(name):
    t = load_theme(name)
    for key in FILE_KEYS:
        assert t.asset(key), f"{name}: {key} 에셋이 없습니다"
    if t.decorated:
        for key in ("pearl_strand", "sparkle", "splash", "check_on", "check_off", "step_pearl",
                    "step_pearl_current", "step_pearl_done", "progress_band", "progress_band_fill", "chip_confirmed", "chip_pending", "emblem"):
            assert t.asset(key), f"{name}: {key} 에셋이 없습니다"
        for group in t.type_colors:
            assert (assets_dir() / t.assets["dir"] / t.assets["gems"][group]).is_file(), group


@pytest.mark.parametrize("name", available_themes())
def test_bezel_slices_match_the_screen_hole(name):
    """자를 곳(위, 오른, 아래, 왼)이 그림의 화면 자리(투명한 가운데) 가장자리와 4px 안으로 맞는다."""
    t = load_theme(name)
    cut, border = t.slices("bezel")
    img = QImage(str(t.asset("bezel")))
    w, h, cx, cy = img.width(), img.height(), img.width() // 2, img.height() // 2

    def solid(x, y):
        return img.pixelColor(x, y).alpha() > 128

    top = next(y for y in range(cy, -1, -1) if solid(cx, y)) + 1
    bottom = h - next(y for y in range(cy, h) if solid(cx, y))
    left = next(x for x in range(cx, -1, -1) if solid(x, cy)) + 1
    right = w - next(x for x in range(cx, w) if solid(x, cy))
    assert all(abs(a - b) <= 4 for a, b in zip(cut, [top, right, bottom, left])), (cut, [top, right, bottom, left])
    assert min(border) >= 16 and border[2] >= 30, "아래 턱에 15px 글자가 들어가야 한다"


@pytest.mark.parametrize("name", available_themes())
def test_button_slices_keep_44px_and_round_caps(name):
    """핑크는 알약(양 끝 반원), 단정한 모니터는 모서리만 둥근 판. 어느 쪽이든 둥근 모서리가 통째로 모서리 칸에 들어가야 한다."""
    t = load_theme(name)
    for key in ("btn_primary", "btn_secondary"):
        cut, border = t.slices(key)
        img = QImage(str(t.asset(key)))
        w, h = img.width(), img.height()

        def solid(x, y):
            return img.pixelColor(x, y).alpha() > 128

        # 왼쪽 칸이 끝나는 곳에서는 이미 판 위 가장자리까지 꽉 차 있어야 한다(둥근 모서리가 가운데 칸으로 새지 않게)
        assert next(y for y in range(h) if solid(cut[3], y)) <= 4, (key, "왼쪽 모서리가 칸 밖으로 샌다")
        # 위아래는 44px 높이에서 모서리와 같은 비율로 줄어야 한다(알약의 반원, 판의 둥근 모서리가 늘어나지 않게)
        mid = (h - cut[0] - cut[2]) / (BUTTON_HEIGHT - border[0] - border[2])
        assert abs(mid - cut[0] / border[0]) / mid < 0.05, (key, mid, cut[0] / border[0])
        assert border[1] == border[3] and border[1] <= BUTTON_HEIGHT // 2
        # 가로, 세로 줄이는 비율이 같아야 모서리가 찌그러지지 않는다
        assert abs(cut[0] / border[0] - cut[1] / border[1]) / (cut[1] / border[1]) < 0.05, key


@pytest.mark.parametrize("name", available_themes())
@pytest.mark.parametrize("key", ["btn_primary", "btn_primary_hover", "btn_primary_pressed"])
def test_primary_button_text_contrast_on_the_image(name, key):
    """글자가 놓이는 가운데(세로 30~70%, 가로 20~80%)의 평균 색과 단추 글씨(on_primary) 대비.
    단정한 모니터는 흰 글씨, 핑크는 연분홍 진주 위 짙은 모브 글씨."""
    t = load_theme(name)
    img = QImage(str(t.asset(key)))
    xs = range(int(img.width() * 0.2), int(img.width() * 0.8), 7)
    ys = range(int(img.height() * 0.3), int(img.height() * 0.7), 5)
    px = [_rgb(img, x, y) for x in xs for y in ys]
    mean = [sum(p[i] for p in px) / len(px) for i in range(3)]
    ratio = contrast(t["on_primary"], _hex(mean))
    assert ratio >= 4.5, f"{name} {key}: {ratio:.2f}"


@pytest.mark.parametrize("name", available_themes())
def test_band_texture_averages_to_band_token(name):
    t = load_theme(name)
    img = QImage(str(t.asset("band_texture")))
    px = [_rgb(img, x, y) for x in range(0, img.width(), 9) for y in range(0, img.height(), 9)]
    mean = [sum(p[i] for p in px) / len(px) for i in range(3)]
    band = [int(t["band"][i:i + 2], 16) for i in (1, 3, 5)]
    assert all(abs(a - b) <= 2 for a, b in zip(mean, band)), (mean, band)
    lum = [_luminance(_hex(p)) for p in px]
    assert max(lum) - min(lum) < 0.08, "질감 명암이 너무 크다(글씨 대비를 떨어뜨린다)"


def test_stylesheet_uses_assets_only_when_present(qapp, tmp_path, monkeypatch):  # 글꼴 목록에 Qt 앱이 필요하다
    t = load_theme("pink")
    assert "band-texture" in stylesheet(t) and t.slices("btn_primary") and t.slices("btn_secondary")
    monkeypatch.setenv("PRIVACY_OPERATOR_ASSETS", str(tmp_path))  # 빈 폴더
    empty = load_theme("pink")
    sheet = stylesheet(empty)
    assert "band-texture" not in sheet and not empty.slices("btn_primary") and not empty.slices("btn_secondary")


@pytest.mark.parametrize("ratio", [1.0, 1.25, 1.5, 2.0])
def test_nine_slice_art_is_shrunk_to_screen_size_first(qapp, ratio):
    """그림 단추와 베젤은 미리 면적 평균으로 줄여 두고 모서리를 거의 1:1로 찍는다(한 번에 크게 줄이면 테두리가 우글거린다)."""
    from privacy_operator.ui.style import nine_pixmap

    t = load_theme("pink")
    for key in ("btn_primary", "btn_secondary", "bezel"):
        cut, border = t.slices(key)
        pm, small = nine_pixmap(t.asset(key), cut, border, ratio)
        for c, b in zip(small, border):
            assert abs(c - b * ratio) <= max(2, 0.25 * b * ratio), (key, small, border)  # 모서리 크기 = 그릴 크기
        assert pm.height() < QImage(str(t.asset(key))).height()


# ---------------------------------------------------------------- 창

@pytest.fixture
def make_win(qtbot, tmp_path, reader):
    made = []

    def make(theme="pink"):
        settings = Settings(vault_dir=str(tmp_path / f"금고{len(made)}"), workspace_dir=str(tmp_path / "작전실"),
                            theme=theme)
        vault = Vault(settings.vault_path).create("assets-password")
        wf = MissionWorkflow(settings, vault, reader_factory=lambda: reader)
        w = MainWindow(wf, settings, None, Voice("plain"), load_theme(theme))
        qtbot.addWidget(w)
        w.resize(1440, 900)
        w.show()
        qtbot.waitExposed(w)
        made.append(wf)
        return w

    yield make
    for wf in made:
        wf.close()


def test_bezel_uses_asset_and_switches_without_reopening(make_win):
    w = make_win("plain")
    plain = w.bezel.frame.toImage().pixelColor(5, w.bezel.frame.height() // 2)
    assert w.bezel.frame is not None and w.bezel.border == load_theme("plain").slices("bezel")[1]
    w.apply_theme("pink")
    pink = w.bezel.frame.toImage().pixelColor(5, w.bezel.frame.height() // 2)
    assert (plain.red(), plain.green(), plain.blue()) != (pink.red(), pink.green(), pink.blue())
    assert w.bezel.pearls is not None
    w.apply_theme("plain")
    assert w.bezel.pearls is None


def test_missing_assets_fall_back_to_token_drawing(make_win, monkeypatch, tmp_path):
    monkeypatch.setenv("PRIVACY_OPERATOR_ASSETS", str(tmp_path / "없음"))
    w = make_win("pink")
    assert w.bezel.frame is None
    m = w.bezel.layout().contentsMargins()
    assert (m.left(), m.top(), m.bottom()) == (w.bezel.SIDE, w.bezel.SIDE, w.bezel.CHIN)
    assert not w.grab().isNull()


def _mission(w):
    from answer_utils import SAMPLES

    w.load_files([SAMPLES / "연수운영계획_샘플.hwpx"])
    wf = w.workflow
    wf.exclude([f.finding_id for f in wf.findings if f.exclude_hint])
    while wf.counts().pending:
        wf.confirm([f.finding_id for f in wf.findings if f.review_status == "대기"])
    w.refresh()
    w.start_mission(ProcessingRecord("연수 결과 정리", "채팅형 AI", 30))
    w.done_dialog.close()


def test_pink_plays_sparkle_once_after_start(make_win, qtbot):
    w = make_win("pink")
    _mission(w)
    fx = w.effect
    assert isinstance(fx, SparkleOverlay) and fx.isVisible()
    assert fx.width() == int(w.width() * fx.SHARE)
    qtbot.wait(1800)
    SHOTS.mkdir(exist_ok=True)
    w.grab().save(str(SHOTS / "16_sparkle_pink.png"))
    with qtbot.waitSignal(fx.finished, timeout=10000):
        pass
    assert not fx.isVisible()


def test_plain_flashes_once_after_start(make_win, qtbot):
    w = make_win("plain")
    _mission(w)
    fx = w.effect
    assert isinstance(fx, FlashOverlay)
    with qtbot.waitSignal(fx.finished, timeout=3000):
        pass
    assert not fx.isVisible()


def test_effects_do_not_block_clicks(make_win):
    from PySide6.QtCore import Qt

    w = make_win("pink")
    fx = w.celebrate()
    assert fx.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    fx.stop()


def test_splash_only_in_decorated_theme(qtbot):
    from privacy_operator.ui.app import show_splash

    assert show_splash(load_theme("plain"), ms=0) is None
    splash = show_splash(load_theme("pink"), ms=0)
    assert splash is not None and not splash.isVisible()
    assert splash.pixmap().deviceIndependentSize().width() == pytest.approx(720, abs=1)


@pytest.mark.parametrize("name", available_themes())
def test_button_images_are_actually_drawn(make_win, name):
    """스타일시트 규칙이 엉키면 Qt가 그림을 조용히 빼먹는다(border-radius, 상태 규칙, 우선순위). 실제로 찍어 본다.
    보조 단추: 위 가장자리 가운데에 고리가 있다. 주 단추: 가운데가 단추 그림 색이다(종이색이 아니다)."""
    w = make_win(name)
    t = w.theme
    ring = w.confirm_btn.grab().toImage()
    top = [ring.pixelColor(ring.width() // 2, y) for y in range(round(ring.height() * 0.3))]
    assert any(c.alpha() > 128 for c in top), "보조 단추 고리가 그려지지 않았다"
    w.run_btn.setEnabled(True)
    w.run_btn.setStyleSheet("")  # 앱 스타일시트 그대로
    pri = w.run_btn.grab().toImage()
    mid = _hex(_rgb(pri, pri.width() // 2, pri.height() // 2))
    assert contrast(t["on_primary"], mid) >= 3 and mid != t["band"], "주 단추 그림이 그려지지 않았다"


# ---------------------------------------------------------------- 정리안(2026-11-07): 진주 체크박스, 진주 단계, 진주 진척 원

def test_check_mark_stands_out_from_the_pearl_tile():
    """켜진 체크박스(2026-11-07 결정): 타일은 베젤 색, 체크는 흰 진주에 모브 윤곽.
    흰 몸통만으로는 베젤 분홍과 대비가 1.6:1이라, 윤곽(짙은 쪽 5%)이 타일과 2.5:1 이상 떨어져 22px에서도 모양이 선다.
    (로즈골드 체크는 1.4:1이었다.)"""
    t = load_theme("pink")
    img = QImage(str(t.asset("check_on")))
    w, h = img.width(), img.height()
    px = [_rgb(img, x, y) for x in range(int(w * 0.18), int(w * 0.82), 9) for y in range(int(h * 0.18), int(h * 0.82), 9)]
    px.sort(key=lambda p: _luminance(_hex(p)))
    tile = px[len(px) // 2]
    light = px[-(len(px) * 15 // 100):]
    body = [sum(p[i] for p in light) / len(light) for i in range(3)]
    dark = px[: len(px) * 5 // 100]
    rim = [sum(p[i] for p in dark) / len(dark) for i in range(3)]
    assert min(body) > 235, "체크 몸통은 흰색"
    assert contrast(_hex(rim), _hex(tile)) >= 2.5
    bezel = (0xE2, 0xB7, 0xC7)  # 베젤 옆면 평균 색(tools/assets.py)
    assert all(abs(a - b) <= 16 for a, b in zip(tile, bezel)), (tile, bezel)


def test_table_checkbox_is_drawn_from_the_asset(make_win):
    from answer_utils import SAMPLES
    from PySide6.QtCore import Qt

    from privacy_operator.ui.review import CHECK

    w = make_win("pink")
    w.load_files([SAMPLES / "연수운영계획_샘플.hwpx"])
    table = w.review.table
    w.review.model.set_checked(w.review.model.ids_at(0), True)
    rect = table.visualRect(table.model().index(0, CHECK))
    shot = table.viewport().grab(rect).toImage()
    center = shot.pixelColor(shot.width() // 2, shot.height() // 2)
    assert table.model().index(0, CHECK).data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    # 진주 그림이면 체크 자리가 모브 계열(빨강, 파랑이 초록보다 크다). Windows 기본 체크박스는 파란 채움이다
    assert center.red() > center.green() and center.blue() > center.green(), center.name()
    SHOTS.mkdir(exist_ok=True)
    w.grab().save(str(SHOTS / "17_redesign_pink.png"))


def test_header_checkbox_states_and_click(make_win):
    from answer_utils import SAMPLES
    from PySide6.QtCore import Qt

    w = make_win("pink")
    w.load_files([SAMPLES / "연수운영계획_샘플.hwpx"])
    box, model = w.all_btn, w.review.model
    assert box.checkState() == Qt.CheckState.Unchecked and not w.none_btn.isVisible()
    model.set_checked(model.ids_at(0), True)
    assert box.checkState() == Qt.CheckState.PartiallyChecked and w.none_btn.isVisible()
    box.click()  # 고른 것이 있으면 모두 푼다
    assert not model.checked and box.checkState() == Qt.CheckState.Unchecked
    box.click()  # 없으면 보이는 대기 항목을 고른다
    assert model.checked and box.checkState() == Qt.CheckState.Checked
    pos = box.mapTo(w.review.table, box.rect().center())
    assert pos.x() < 44 and pos.y() < w.review.table.horizontalHeader().height(), "머리 체크 칸 위에 있어야 한다"


def test_progress_ring_reveals_the_pink_band_up_to_the_angle(make_win):
    w = make_win("pink")
    ring = w.ring
    s = ring.SIZE
    probe = (round(s * 0.5 + s * 0.42 * 0.7071), round(s * 0.5 - s * 0.42 * 0.7071))  # 띠 위 1시 30분 자리

    def at(done, total):
        ring.set_value(done, total)
        return _rgb(ring.grab().toImage(), *probe)

    empty, half = at(0, 10), at(5, 10)
    assert half != empty  # 채운 쪽은 분홍 진주 그림
    r, g, b, _ = half
    assert r > g and b > g, "채움은 분홍(빨강, 파랑이 초록보다 크다)"


def test_step_bar_uses_pearls_only_with_assets(make_win):
    """핑크는 진주 그림, 단정한 모니터는 코드로 그린 동그라미(알약 없음, 번호 없음). 단계 사이 선은 두 테마 모두."""
    w = make_win("pink")
    chip = w.steps.chips[0]
    assert all(line.color for line in w.steps.lines)
    pearl_w = chip.width()
    w.apply_theme("plain")
    assert all(line.color for line in w.steps.lines) and chip.width() < pearl_w


def test_plain_theme_has_no_pills_or_step_numbers(make_win):
    """단정한 모니터: 진행 단계에 번호나 ✓ 글자가 없고, 체크박스는 화면 색으로 그린다(Windows 기본 모양이 아니다)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from privacy_operator.ui.style import paint_check

    w = make_win("plain")
    t = w.theme
    assert all(not hasattr(c, "_prefix") for c in w.steps.chips)  # 번호, ✓ 글자를 만드는 길이 없다
    img = QImage(40, 40, QImage.Format.Format_ARGB32)
    img.fill(QColor(t["paper"]))
    p = QPainter(img)
    paint_check(p, QRectF(0, 0, 40, 40), Qt.CheckState.Checked, t)
    p.end()
    center = img.pixelColor(13, 13)  # 체크 선이 지나지 않는 채움 자리
    assert center.name().upper() == t["accent"].upper()
    # 단추와 칸 고르기는 알약이 아니라 모서리만 둥근 판이다(은빛 판 그림 단추와 같은 모양)
    import re

    from privacy_operator.ui.style import stylesheet

    radii = [int(r) for r in re.findall(r"border-radius:\s*(\d+)px", stylesheet(t))]
    assert radii and max(radii) <= 12, radii
    cut, _border = t.slices("btn_primary")
    assert cut[1] < QImage(str(t.asset("btn_primary"))).height() // 2, "주 단추 그림이 알약(반원 끝)이다"


def test_segment_labels_fit_even_when_bold(make_win, qtbot):
    """고른 칸은 굵게 바뀐다. 굵은 글자도 칸 안에 다 들어가야 한다(잘린 '전체 2(' 같은 글자가 없게)."""
    from answer_utils import SAMPLES
    from PySide6.QtGui import QFont, QFontMetrics

    from privacy_operator.ui.style import font

    w = make_win("pink")
    w.load_files([SAMPLES / "연수운영계획_샘플.hwpx"])
    for pick in (1, 0):
        w.review.status_buttons.button(pick).click()
        qtbot.wait(50)
        bold = font("body")
        bold.setWeight(QFont.Weight.Bold)
        for b in w.review.status_buttons.buttons():
            # 글자 자리 = 너비 - 좌우 여백 14px씩 - 테두리 2px씩(고르면 포커스 테두리가 생긴다)
            assert b.width() - 28 - 4 >= QFontMetrics(bold).horizontalAdvance(b.text()), b.text()
    # 머리 체크박스와 행 체크박스는 둘 다 자기 칸 가운데에 그린다(paint_check). 두 칸의 가운데가 같은 세로줄이다
    from privacy_operator.ui.review import CHECK

    table, box = w.review.table, w.all_btn
    cell = table.visualRect(table.model().index(0, CHECK))
    head_x = box.mapTo(table, box.rect().center()).x()
    row_x = table.viewport().mapTo(table, cell.center()).x()
    assert abs(head_x - row_x) <= 1, (head_x, row_x)
    w.grab().save(str(SHOTS / "18_segment_pink.png"))


@pytest.mark.parametrize("name", ["pink", "plain"])
def test_verdict_text_keeps_its_baseline_when_status_changes(make_win, name):
    """대기에서 확정으로 바뀌어도 글자는 제자리(같은 기준선, 같은 시작점)에서 바뀐다."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    from privacy_operator.detect.finding import STATUS_CONFIRMED, STATUS_EXCLUDED, STATUS_PENDING

    w = make_win(name)
    d = w.review.delegate
    spots, colors = {}, {}
    img = QImage(200, 40, QImage.Format.Format_ARGB32)
    for status in (STATUS_PENDING, STATUS_CONFIRMED, STATUS_EXCLUDED):
        p = QPainter(img)
        seen = []
        p.drawText = lambda *a, _s=seen, _p=p: _s.append((a[0], _p.font().weight(), _p.pen().color().name()))
        d._chip(p, QRect(0, 0, 200, 40), status)
        p.end()
        at, weight, color = seen[-1]
        spots[status] = (round(at.x(), 2), round(at.y(), 2), weight)  # 굵기가 바뀌면 글자 폭이 달라져 움직여 보인다
        colors[status] = color
    assert colors[STATUS_PENDING] == colors[STATUS_CONFIRMED], colors  # 대기와 확정은 그림만 다르다
    assert len(set(spots.values())) == 1, spots
