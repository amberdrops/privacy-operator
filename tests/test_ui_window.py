"""주 창(pytest-qt). 6단계 완료 기준:

- 화면만으로 0부터 끝까지 작전 한 번을 할 수 있다.
- 말투를 바꾸면 모든 화면 문구가 바로 바뀐다.
- 모든 글자 15px 이상(대비는 test_ui_static.py).
값은 모두 지어낸 것이다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from answer_utils import SAMPLES
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget

from privacy_operator.alias.vault import Vault
from privacy_operator.detect.finding import STATUS_PENDING
from privacy_operator.mission.package import ProcessingRecord
from privacy_operator.reader.blocks import BlindSpot
from privacy_operator.settings import Settings
from privacy_operator.ui.dialogs import BlindSpotDialog, PasswordDialog, SettingsDialog
from privacy_operator.ui.main_window import DROP, RESTORE, REVIEW, MainWindow
from privacy_operator.ui.review import CHECK, CONTEXT, GROUP_ROLE, VALUE
from privacy_operator.ui.style import MIN_PX
from privacy_operator.ui.theme import load_theme
from privacy_operator.ui.voice import Voice
from privacy_operator.ui.widgets import StepChip
from privacy_operator.workflow import MissionWorkflow

ROOT = Path(__file__).resolve().parent.parent
HWPX = SAMPLES / "연수운영계획_샘플.hwpx"
XLSX = SAMPLES / "만족도조사_샘플.xlsx"


@pytest.fixture
def win(qtbot, tmp_path, reader):
    settings = Settings(vault_dir=str(tmp_path / "금고"), workspace_dir=str(tmp_path / "작전실"))
    vault = Vault(settings.vault_path).create("window-password")
    wf = MissionWorkflow(settings, vault, reader_factory=lambda: reader)
    w = MainWindow(wf, settings, None, Voice("plain"), load_theme("plain"))
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    yield w
    wf.close()


def _px(widget: QWidget) -> float:
    f = widget.font()
    return f.pixelSize() if f.pixelSize() > 0 else f.pointSizeF() * widget.logicalDpiY() / 72


def _small_texts(root: QWidget) -> list[str]:
    small = []
    for w in [root] + root.findChildren(QWidget):
        if w.isVisible() and _px(w) < MIN_PX - 0.01:
            small.append(f"{type(w).__name__}:{w.objectName()}:{_px(w):.1f}")
    return small


def _decide_all(win):
    wf = win.workflow
    hints = [f.finding_id for f in wf.findings if f.exclude_hint and f.review_status == STATUS_PENDING]
    win.review.model.set_checked(hints, True)
    win.exclude_btn.click()
    while wf.counts().pending:
        win.all_btn.click()
        win.confirm_btn.click()


# ---------------------------------------------------------------- 글자 크기

def test_every_visible_text_is_at_least_15px(win, qtbot):
    assert not _small_texts(win), "불러오기 화면"
    win.load_files([HWPX, XLSX])
    win.review.fold.setChecked(True)
    assert not _small_texts(win), "검토 보드"
    win.show_page(RESTORE)
    assert not _small_texts(win), "복원 화면"


@pytest.mark.parametrize("make", [
    lambda v: PasswordDialog(v, "change", lambda a, b: None),
    lambda v: SettingsDialog(v, Settings(vault_dir="C:/x/금고", workspace_dir="C:/x/작전실"), lambda: None),
])
def test_dialog_texts_are_at_least_15px(qtbot, make):
    from privacy_operator.ui.style import stylesheet

    QApplication.instance().setStyleSheet(stylesheet(load_theme("pink")))  # 실제 앱처럼 스타일시트를 먼저(app.py)
    dlg = make(Voice("plain"))
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    assert not _small_texts(dlg)


# ---------------------------------------------------------------- 말투, 테마

def _bound_texts_match(win) -> list[str]:
    wrong = []
    for widget, key, setter, values, _tip in win.binder.items:
        expected = win.voice.t(key, **(values() if values else {}))
        if isinstance(widget, StepChip):
            actual = widget.name
        elif getattr(setter, "__name__", "") == "setText" and hasattr(widget, "text"):
            actual = widget.text()
        elif getattr(setter, "__name__", "") == "setWindowTitle":
            actual = widget.windowTitle()
        else:
            continue  # 값을 조합해 쓰는 글자(제목, 안내)는 아래에서 따로 본다
        if actual != expected:
            wrong.append(key)
    return wrong


def test_voice_switch_changes_all_texts_at_once(win):
    win.load_files([HWPX])
    assert not _bound_texts_match(win)
    win.apply_voice("mission")
    assert not _bound_texts_match(win)
    assert win.run_btn.text() == "작전 개시"
    assert win.review.model.headerData(VALUE, Qt.Orientation.Horizontal) == "타깃"
    assert win.title.text().startswith("작전 대기")
    assert "전처리 실행" in win.run_btn.toolTip()  # 작전 말투에서는 쉬운 말 뜻을 함께
    assert [c.name for c in win.steps.chips] == ["침투", "스캔", "판정", "은폐", "송출", "귀환"]
    win.apply_voice("plain")
    assert not _bound_texts_match(win)
    assert win.run_btn.text() == "전처리 실행"


def test_theme_switch_applies_without_reopening(win):
    app = QApplication.instance()
    win.apply_theme("pink")
    pink = load_theme("pink")
    assert pink["accent"] in app.styleSheet() and win.bezel.theme.name == "pink"
    assert win.review.delegate.theme.name == "pink"
    win.apply_theme("plain")
    assert load_theme("plain")["accent"] in app.styleSheet() and pink["accent"] not in app.styleSheet()


# ---------------------------------------------------------------- 작전 한 번 (완료 기준)

def test_whole_mission_with_the_window_only(win, qtbot):
    assert win.stack.currentIndex() == DROP and not win.run_btn.isEnabled()

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(HWPX))])
    drop = QDropEvent(QPointF(200, 200), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    with qtbot.waitSignal(win.loaded, timeout=60000):
        win.dropEvent(drop)  # 다른 스레드에서 읽는다
    assert win.stack.currentIndex() == REVIEW
    assert win.review.main_proxy.rowCount() + win.review.gray_proxy.rowCount() == len(win.workflow.findings)
    assert not win.run_btn.isEnabled()

    _decide_all(win)
    assert win.run_btn.isEnabled()
    assert win.ring.done == win.ring.total > 0

    package = win.start_mission(ProcessingRecord("연수 보고서 초안", "채팅형 AI", 30))
    assert package and (package / "mission.json").is_file()
    assert not win.done_dialog.isVisible()  # 깜빡임이 끝난 뒤에 연다
    qtbot.waitUntil(win.done_dialog.isVisible, timeout=5000)
    copy = next(b for b in win.done_dialog.findChildren(type(win.run_btn)) if b.objectName() == "Primary")
    copy.click()
    assert QGuiApplication.clipboard().text() == win.workflow.rules_text()
    win.done_dialog.close()

    mid = win.workflow.mission_id
    code = next(e["alias_code"] for e in win.workflow.vault.load_mapping(mid)["entries"] if e["raw_value"] == "김민수")
    (win.settings.from_ai_path / mid / f"{mid}_결과.md").write_text(f"{code}는 이론을 맡았다.", encoding="utf-8")
    win.nav[RESTORE].click()
    assert win.stack.currentIndex() == RESTORE and win.restore_page.table.rowCount() == 1
    win.restore_page.run_btn.click()
    out = win.restore_page.last.output
    assert out.read_text(encoding="utf-8") == "김민수는 이론을 맡았다."
    assert win.steps.chips[-1].state == "current"


def test_select_all_does_not_overturn_earlier_verdicts(win):
    """제외한 항목이 '전체 선택 → 확정'으로 다시 확정되면 안 된다."""
    win.load_files([HWPX])
    hint = next(f for f in win.workflow.findings if f.exclude_hint and f.review_status == STATUS_PENDING)
    win.review.model.set_checked([hint.finding_id], True)
    win.exclude_btn.click()
    win.all_btn.click()
    assert hint.finding_id not in win.review.model.checked
    win.confirm_btn.click()
    assert hint.review_status == "제외"


def test_checkbox_click_and_space_toggle(win, qtbot):
    win.load_files([HWPX])
    table = win.review.table
    idx = table.model().index(0, VALUE)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(idx).center())
    assert len(win.review.model.checked) == 1 and win.selected.text() == "1건 선택"
    table.setCurrentIndex(table.model().index(1, CHECK))
    qtbot.keyClick(table, Qt.Key.Key_Space)
    assert len(win.review.model.checked) == 2


def test_gray_zone_is_folded_until_opened(win):
    win.load_files([HWPX])
    assert not win.review.gray_table.isVisible() and win.review.fold.text().endswith("▸")
    win.review.fold.click()
    assert win.review.gray_table.isVisible() and win.review.fold.text().endswith("▾")


def test_select_all_picks_judged_rows_when_no_pending_is_shown(win):
    """제외 칸만 걸러 보면 대기가 없다. 그때 전체 선택은 보이는 제외 항목을 모두 고른다(판정 취소, 확정으로 바꾸기용)."""
    win.load_files([HWPX])
    _decide_all(win)
    win.review.status_buttons.button(3).click()  # 제외
    shown = win.review.visible_ids()
    assert shown and all(f.review_status == "제외" for f in win.workflow.by_id(shown))
    win.all_btn.click()
    assert win.review.model.checked == set(shown)
    assert win.unjudge_btn.isVisible() and win.confirm_btn.isEnabled()
    win.all_btn.click()  # 다시 누르면 풀린다
    assert not win.review.model.checked


def test_gray_zone_has_its_own_select_all(win):
    """요주의 구역 표는 자기 머리 체크박스로 모두 고르고 푼다. 위 표의 전체 선택은 위 표만 고른다."""
    win.load_files([HWPX])
    win.review.fold.click()
    gray = set(win.review.gray_proxy.visible_ids())
    assert gray
    box = win.review.gray_check.box
    assert box.isEnabled()
    box.click()
    assert win.review.model.checked == gray and box.checkState() == Qt.CheckState.Checked
    assert win.raise_btn.isVisible()  # 고른 참고 항목은 올리기로 처리한다
    win.all_btn.click()  # 위 표 전체 선택: 위 표 대기 항목이 더해지고, 요주의 구역은 그대로
    assert gray < win.review.model.checked
    assert not (set(win.review.visible_ids()) & gray)
    box.click()  # 요주의 구역만 풀린다
    assert not (win.review.model.checked & gray) and win.review.model.checked
    assert box.checkState() == Qt.CheckState.Unchecked


def test_blind_spot_band_shows_only_when_needed(win, monkeypatch):
    win.load_files([HWPX])
    assert not win.blind.isVisible()
    monkeypatch.setattr(win.workflow, "blind_spots", lambda: [BlindSpot("needs_ocr", page=3)])
    win.refresh()
    assert win.blind.isVisible() and win.blind_text.text() == "검사 못 한 영역 1곳"


def test_reference_buttons_appear_only_when_relevant(win):
    win.load_files([HWPX])
    assert not win.raise_btn.isVisible() and not win.lower_btn.isVisible()
    ref = next(f for f in win.workflow.findings if f.review_status == "참고")
    win.review.model.set_checked([ref.finding_id], True)
    assert win.raise_btn.isVisible()


# ---------------------------------------------------------------- 7단계: 검토 보드 다듬기

def test_group_view_judges_every_place_of_a_value(win, qtbot):
    win.load_files([HWPX])
    win.review.group_btn.click()
    table = win.review.table
    assert table.model() is win.review.group_proxy
    rows = {table.model().index(r, VALUE).data(): r for r in range(table.model().rowCount())}
    row = rows["김민수"]
    group = table.model().index(row, VALUE).data(GROUP_ROLE)
    assert len(group.members) >= 2  # 표 칸과 본문
    assert "곳" in table.model().index(row, CONTEXT).data()
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                     pos=table.visualRect(table.model().index(row, VALUE)).center())
    assert set(group.ids) <= win.review.model.checked
    win.confirm_btn.click()
    assert all(f.review_status == "확정" for f in group.members)


def test_double_click_group_shows_each_place(win, qtbot):
    win.load_files([HWPX])
    win.review.group_btn.click()
    table = win.review.table
    row = next(r for r in range(table.model().rowCount()) if table.model().index(r, VALUE).data() == "김민수")
    n = len(table.model().index(row, VALUE).data(GROUP_ROLE).members)
    qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton,
                      pos=table.visualRect(table.model().index(row, VALUE)).center())
    assert not win.review.group_btn.isChecked() and table.model() is win.review.main_proxy
    assert table.model().rowCount() == n and win.review.show_all_btn.isVisible()
    win.review.show_all_btn.click()
    assert table.model().rowCount() > n


def test_status_filter_shows_only_pending(win):
    win.load_files([HWPX])
    some = [f.finding_id for f in win.workflow.findings if f.review_status == "대기"][:3]
    win.workflow.confirm(some)
    win.refresh()
    pending = win.review.status_buttons.button(1)
    assert pending.text() == f"대기 {win.workflow.counts().pending}"
    pending.click()
    shown = win.review.visible_ids()
    assert shown and all(f.review_status == "대기" for f in win.workflow.by_id(shown))
    win.select_all()
    assert win.review.model.checked == set(shown)


def test_shift_click_checks_a_range(win, qtbot):
    win.load_files([HWPX])
    table = win.review.table
    first, last = table.model().index(0, VALUE), table.model().index(3, VALUE)
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(first).center())
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                     table.visualRect(last).center())
    assert len(win.review.model.checked) == 4


def test_loading_runs_in_background(win, qtbot):
    with qtbot.waitSignal(win.loaded, timeout=60000):
        assert win.load_files_async([HWPX, XLSX])
        assert win.guide.text().startswith("읽는 중")
        assert not win.load_files_async([HWPX])  # 읽는 동안 또 넣으면 무시
        assert not win.run_btn.isEnabled() and not win.add_btn.isEnabled()
    assert len(win.workflow.results) == 2 and win.stack.currentIndex() == REVIEW
    assert win.add_btn.isEnabled()


def test_rescan_notice_after_confirm(win):
    from test_alias import doc, para

    win.workflow.add_results([(Path("a.hwpx"), doc(para("오전은 김민수 강사가 맡는다"), name="a.hwpx")),
                              (Path("b.pdf"), doc(para("김민수는 결석"), name="b.pdf"))])
    win.refresh()
    win.all_btn.click()
    win.confirm_btn.click()
    assert win.guide.text() == "같은 값을 1곳 더 찾아 대기에 올렸습니다."
    assert win.workflow.counts().pending == 1


def test_blind_spot_details_dialog(win, qtbot, monkeypatch):
    win.load_files([HWPX])
    res = win.workflow.results[0]
    monkeypatch.setattr(res, "blind_spots", [BlindSpot("needs_ocr", page=2, detail="1"),
                                              BlindSpot("drawing", detail="3")])
    win.refresh()
    assert win.blind.isVisible() and win.blind_text.text() == "검사 못 한 영역 2곳"
    dlg = BlindSpotDialog(win.voice, win.workflow.results, win)
    qtbot.addWidget(dlg)
    lines = [dlg.items.item(i).text() for i in range(dlg.items.count())]
    assert lines == [f"{res.source_name}, 2쪽: 글자 없이 그림만 있는 쪽(스캔)입니다. 글자를 읽지 못했습니다.",
                     f"{res.source_name}: 도형이나 차트 3개. 그 안의 글자는 검사하지 않았습니다."]
    # 작전 개시 때 검사 못 한 영역이 있으면 한 번 묻는다. 취소하면 개시하지 않는다
    asked = []
    monkeypatch.setattr(win, "confirm_blind_spots", lambda n: asked.append(n) or False)
    _decide_all(win)
    assert win.start_mission() is None and asked == [2] and win.workflow.mission_id is None


# ---------------------------------------------------------------- 스크린샷 (CLAUDE.md 작업 방식)

def test_screenshots_at_current_scale(qapp):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_shots

    suffix = str(round(qapp.primaryScreen().devicePixelRatio() * 100))
    paths = ui_shots.main(suffix)
    assert len(paths) == 13 and all(p.stat().st_size > 10_000 for p in paths)


def test_screenshots_at_150_percent():
    env = {**os.environ, "QT_SCALE_FACTOR": str(round(1.5 / _native_ratio(), 3)), "PYTHONIOENCODING": "utf-8"}
    run = subprocess.run([sys.executable, str(ROOT / "tests" / "ui_shots.py"), "150"], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-2000:]
    assert len(run.stdout.split()) == 13


def _native_ratio() -> float:
    app = QApplication.instance()
    return app.primaryScreen().devicePixelRatio() if app else 1.0


def test_unjudge_button_returns_verdicts_to_pending(win):
    """확정한 것을 골라 '대기로 되돌리기'를 누르면 대기로 돌아간다. 확정, 제외를 고른 때만 보인다."""
    win.load_files([HWPX])
    f = next(x for x in win.workflow.findings if x.review_status == STATUS_PENDING)
    win.review.model.set_checked([f.finding_id], True)
    assert not win.unjudge_btn.isVisible()
    win.confirm_btn.click()
    assert f.review_status == "확정"
    win.review.model.set_checked([f.finding_id], True)
    assert win.unjudge_btn.isVisible()
    win.unjudge_btn.click()
    assert f.review_status == STATUS_PENDING and not win.review.model.checked


def test_dropping_a_folder_loads_supported_files_inside(tmp_path):
    from privacy_operator.ui.main_window import expand_folders

    (tmp_path / "하위").mkdir()
    for name in ("a.hwpx", "하위/b.PDF", "c.txt", "~$잠금.xlsx", "d.xlsx"):
        (tmp_path / name).write_bytes(b"x")
    single = tmp_path / "a.hwpx"
    got = expand_folders([tmp_path, single])
    assert sorted(p.name for p in got) == ["a.hwpx", "b.PDF", "d.xlsx"]


def test_window_maximizes_when_it_does_not_fit_the_screen():
    """125% 배율 노트북(작업 표시줄 뺀 곳 약 1536x816)에서는 1440x900 창이 넘쳐 최대화로 연다."""
    from PySide6.QtCore import QSize

    from privacy_operator.ui.main_window import needs_maximize

    assert needs_maximize(QSize(1440, 900), QSize(1536, 816))
    assert not needs_maximize(QSize(1440, 900), QSize(1920, 1040))
    assert needs_maximize(QSize(1440, 900), QSize(1280, 1000))



def test_start_mission_runs_without_a_dialog_and_records_defaults(win, qtbot):
    """작전 개시는 확인 창 없이 바로 한다. 처리 기록의 목적은 고정 문구, 보관 기간은 설정 값."""
    import json

    from privacy_operator.mission.package import DEFAULT_PURPOSE

    win.load_files([HWPX])
    _decide_all(win)
    win.settings.retention_days = 45
    package = win.start_mission()
    assert package
    qtbot.waitUntil(win.done_dialog.isVisible, timeout=5000)
    log = json.loads((win.settings.vault_path / win.workflow.mission_id / "log.json").read_text(encoding="utf-8"))
    rec = log if "purpose" in log else log["record"]
    assert (rec["purpose"], rec["retention_days"]) == (DEFAULT_PURPOSE, 45)
    win.done_dialog.close()


def test_restore_page_finds_a_code_name(win):
    """귀환 화면의 코드명 찾기: 적으면 '작전 번호  코드명 = 원문 값'을 화면에만 보여 준다. 값은 지어낸 것이다."""
    v = win.workflow.vault
    mid = v.new_mission_id()
    v.save_mapping(mid, {"mission_id": mid, "entries": [{"alias_code": "[학생-01]", "category": "이름",
                                                          "raw_value": "도하늘"}]})
    win.show_page(RESTORE)
    page = win.restore_page
    page.find.setText("학생-1")
    assert page.find_code() == [(mid, "[학생-01]", "도하늘")]
    assert page.found.text() == f"{mid}  [학생-01] = 도하늘"
    page.find.setText("[학생-07]")
    page.find_code()
    assert page.found.text() == "대응표에서 찾지 못했습니다."
    page.find.setText("그냥 글")
    page.find_code()
    assert page.found.text() == ""


def test_hover_paints_the_whole_row(win, qtbot):
    """마우스를 올리면 칸 하나가 아니라 그 줄 전체가 머리글 색으로 바뀐다."""
    from privacy_operator.ui.review import CONTEXT, TYPE

    win.load_files([HWPX])
    table = win.review.table
    table.hover_row = 1
    table.viewport().update()
    qtbot.wait(30)
    shot = table.viewport().grab().toImage()
    ratio = shot.devicePixelRatio()
    y = round(table.visualRect(table.model().index(1, TYPE)).top() * ratio + 3)
    xs = [round(table.visualRect(table.model().index(1, c)).left() * ratio + 3) for c in (TYPE, CONTEXT)]
    colors = {shot.pixelColor(x, y).name().upper() for x in xs}
    assert colors == {win.theme["paper_head"].upper()}, colors


def test_blocklist_stop_shows_what_is_left(win, monkeypatch):
    """송출이 멈추면 남은 자리만 표에 모아 보여 주고, 무엇을 할지 짧게 알린다. 확정하면 다시 개시된다."""
    from dataclasses import replace

    from privacy_operator.detect.dictionaries import NAME

    win.load_files([HWPX])
    _decide_all(win)
    wf = win.workflow
    name = next(f for f in wf.findings if f.category == NAME and f.review_status == "확정")
    blocks = wf.results[0].blocks
    k = max(i for i, b in enumerate(blocks) if b.kind == "paragraph")  # 결과 문서에 나가는 본문 문단
    blocks[k] = replace(blocks[k], text=blocks[k].text + " 이" + name.raw_value)
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: said.append(a[2]))
    assert win.start_mission() is None
    shown = win.review.visible_ids()
    rows = wf.by_id(shown)
    assert all(f.raw_value == name.raw_value for f in rows)
    assert name.finding_id in shown and any(f.review_status == "대기" for f in rows)  # 확정한 원래 줄도 함께
    assert said and "모두 확정하거나 모두 제외" in said[0]
    assert win.review.value_label.isVisible() and win.review.show_all_btn.isVisible()
    assert not wf.vault.missions()
    win.all_btn.click()
    win.confirm_btn.click()
    win.review.show_all_btn.click()
    assert len(win.review.visible_ids()) > len(shown)  # 모두 보기로 풀린다
    _decide_all(win)
    assert win.start_mission() is not None
    win.done_dialog.close()


def test_run_button_shows_busy_dialog_at_once_then_done(win, qtbot):
    """작전 개시 단추: 누르자마자 처리 중 창이 뜨고(처리는 다른 스레드), 끝나면 닫히고 송출 완료 창이 뜬다."""
    win.load_files([HWPX])
    _decide_all(win)
    win.run_btn.click()
    assert win.busy_dialog is not None and win.busy_dialog.isVisible()
    assert not win.run_btn.isEnabled() and not win.add_btn.isEnabled()  # 그동안 다른 일은 막는다
    qtbot.waitUntil(lambda: win.starter is None, timeout=30000)
    assert win.busy_dialog is None and win.workflow.package_path
    qtbot.waitUntil(lambda: win.done_dialog.isVisible(), timeout=5000)
    win.done_dialog.close()


def test_busy_dialog_text_is_from_voice(qtbot):
    from privacy_operator.ui.dialogs import BusyDialog

    dlg = BusyDialog(Voice("mission"))
    qtbot.addWidget(dlg)
    dlg.reject()  # Esc로 닫히지 않는다
    dlg.show()
    assert dlg.isVisible() and dlg.findChild(QLabel).text() == "작전 수행 중입니다. 잠시만 기다려 주세요."


def test_pdf_engine_is_loaded_on_the_main_thread_at_start(monkeypatch):
    """PyMuPDF를 읽기 스레드에서 처음 불러오면 프로그램이 꺼졌다. 시작할 때 화면 스레드에서 미리 불러 둔다."""
    import sys
    import threading

    from privacy_operator.ui import app as app_module

    seen = []
    real = app_module.preload_pdf_engine
    monkeypatch.setattr(app_module, "preload_pdf_engine",
                        lambda: (seen.append(threading.current_thread() is threading.main_thread()), real()))
    monkeypatch.setattr(app_module, "open_vault", lambda *a: None)  # 금고 창 없이 끝낸다
    monkeypatch.setattr(app_module, "show_splash", lambda *a, during=None, **k: during and during())
    monkeypatch.setattr(app_module, "load_settings", lambda: Settings())  # 사용자 설정 파일을 건드리지 않는다
    monkeypatch.setattr(app_module, "save_settings", lambda s: None)
    qapp = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app_module, "QApplication", lambda *a: qapp)  # 테스트가 이미 만든 것을 쓴다
    sheet, fnt = qapp.styleSheet(), qapp.font()
    try:
        assert app_module.main([]) == 0
    finally:
        qapp.setStyleSheet(sheet)  # 다른 테스트에 시작 코드의 모양이 남지 않게
        qapp.setFont(fnt)
    assert seen == [True] and "pymupdf" in sys.modules


def test_every_late_import_used_by_worker_threads_is_preloaded():
    """함수 안에서 늦게 불러오는 부품은 시작할 때 화면 스레드에서 미리 불러 둔다(다른 스레드에서 처음 불러오면 꺼진다).
    화면에서만 쓰는 것, 따로 도는 프로세스(Schift 작업 프로세스)는 뺀다."""
    import inspect
    import re
    from pathlib import Path

    from privacy_operator.ui import app as app_module

    src = Path(app_module.__file__).resolve().parents[1]
    preload = inspect.getsource(app_module.preload_pdf_engine)
    skip = {"__main__.py", "app.py", "schift_worker.py", "settings.py"}  # 화면 스레드, 다른 프로세스, 시작 전
    missing = set()
    for path in src.rglob("*.py"):
        if path.name in skip:
            continue
        for m in re.finditer(r"^ {4,}(?:import ([\w.]+)|from ([\w.]+) import)", path.read_text(encoding="utf-8"), re.M):
            mod = (m.group(1) or m.group(2)).split(".")[0] if not (m.group(1) or m.group(2)).startswith(
                "privacy_operator") else (m.group(1) or m.group(2))
            if mod in ("privacy_operator.reader.engine",):
                continue  # 이미 시작할 때 불러온다(workflow가 불러온다)
            if f"import {mod}" not in preload and not (mod == "kordoc" and "restore.zipdoc" in preload):
                missing.add(f"{path.name}: {mod}")
    assert not missing, missing


def test_worker_threads_pause_cyclic_gc_while_running(win, qtbot, monkeypatch):
    """작업 스레드(읽기, 작전 개시)가 도는 동안 자동 메모리 정리를 멈추고, 끝나면 다시 켠다(PyMuPDF는 한 스레드만)."""
    import gc

    seen = []
    real_read, real_start = win.workflow.read_files, win.workflow.start
    monkeypatch.setattr(win.workflow, "read_files", lambda *a, **k: (seen.append(gc.isenabled()), real_read(*a, **k))[1])
    monkeypatch.setattr(win.workflow, "start", lambda *a, **k: (seen.append(gc.isenabled()), real_start(*a, **k))[1])
    assert win.load_files_async([HWPX])
    qtbot.waitUntil(lambda: win.loader is None, timeout=30000)
    _decide_all(win)
    win.run_btn.click()
    qtbot.waitUntil(lambda: win.starter is None, timeout=30000)
    assert seen == [False, False] and gc.isenabled()
    qtbot.waitUntil(lambda: win.done_dialog.isVisible(), timeout=5000)
    win.done_dialog.close()


def test_loading_shows_a_busy_dialog_at_once_with_the_count(win, qtbot):
    """문서를 넣자마자 '읽는 중 0/1' 창이 뜨고, 다 읽으면 닫힌다(첫 문서는 엔진을 켜느라 몇 초 걸린다)."""
    assert win.load_files_async([HWPX])
    assert win.load_dialog is not None and win.load_dialog.isVisible()
    assert win.load_dialog.text.text() == "읽는 중 0/1"
    assert win.load_dialog.windowTitle() == "문서 읽기"
    qtbot.waitUntil(lambda: win.loader is None, timeout=30000)
    assert win.load_dialog is None


def test_busy_dialog_progress_updates_the_count(qtbot):
    from privacy_operator.ui.dialogs import BusyDialog

    dlg = BusyDialog(Voice("plain"), None, "load.title", "load.reading", done=0, total=4)
    qtbot.addWidget(dlg)
    dlg.progress(2, 4)
    assert dlg.text.text() == "읽는 중 2/4" and dlg.windowTitle() == "문서 읽기"
