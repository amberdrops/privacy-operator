"""화면 규칙을 코드와 파일에서 바로 검사한다(창을 띄우지 않는다).

- 말투 문구표 두 개의 키가 같다(6단계 완료 기준).
- 테마마다 글씨와 바탕 대비 4.5:1 이상, 그래픽 3:1 이상. 밝은 바탕, 노란 강조 없음.
- 화면 코드에 색 값과 한국어 문구를 직접 쓰지 않는다. 글자 15px 이상.
"""

from __future__ import annotations

import ast
import colorsys
import json
import re
from pathlib import Path

import pytest

from privacy_operator.ui.style import MIN_PX, SIZES
from privacy_operator.ui.theme import REQUIRED, _luminance, available_themes, contrast, load_theme
from privacy_operator.ui.voice import VOICE_DIR

UI_DIR = Path(__file__).resolve().parent.parent / "src" / "privacy_operator" / "ui"
PLAIN = json.loads((VOICE_DIR / "voice_plain.json").read_text(encoding="utf-8"))
MISSION = json.loads((VOICE_DIR / "voice_mission.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 말투

def test_voice_files_have_same_keys():
    assert set(PLAIN) == set(MISSION)


def test_voice_placeholders_match():
    for key in PLAIN:
        assert set(re.findall(r"\{(\w+)\}", PLAIN[key])) == set(re.findall(r"\{(\w+)\}", MISSION[key])), key


def test_voice_values_are_not_empty_and_have_no_middle_dot():
    for table in (PLAIN, MISSION):
        for key, value in table.items():
            assert value.strip(), key
            assert "·" not in value, key


@pytest.mark.parametrize("key,plain,mission", [
    ("step.load", "불러오기", "침투"), ("step.detect", "탐지", "스캔"), ("step.review", "판정", "판정"),
    ("step.alias", "가명처리", "은폐"), ("step.send", "AI에게 보내기", "송출"), ("step.restore", "복원", "귀환"),
    ("term.finding", "탐지 항목", "타깃"), ("term.alias_code", "가명코드", "코드명"),
    ("term.blind_spot", "검사 못 한 영역", "사각지대"), ("term.gray_zone", "참고 (소속, 직위)", "요주의 구역"),
    ("term.vault_locked", "대응표 잠김", "금고 잠김"), ("term.to_ai", "보낼 문서함", "송출함"),
    ("term.from_ai", "받은 결과함", "귀환함"), ("action.run", "전처리 실행", "작전 개시"),
    ("action.exclude", "오탐 제외", "타깃 해제"), ("status.done", "처리 완료", "임무 완수"),
    ("status.warning", "확인 필요", "경보"),
])
def test_voice_matches_design_spec_table(key, plain, mission):
    assert (PLAIN[key], MISSION[key]) == (plain, mission)


def test_every_voice_key_used_in_code_exists():
    used = set()
    for p in UI_DIR.rglob("*.py"):
        used |= set(re.findall(r"""["']((?:step|term|action|status|bar|blind|panel|nav|drop|col|chip|gray|where|by|esc|
                                    hint|action_bar|guide|run|restore|settings|theme|voice|pw|error|app)\.[\w.]+)["']""",
                               p.read_text(encoding="utf-8"), re.X))
    missing = sorted(k for k in used if k not in PLAIN and not k.endswith((".", ".json", ".py")))
    assert not missing


@pytest.mark.parametrize("name", ["plain", "mission"])
def test_reason_text_fills_every_placeholder(name):
    """머리글 줄이 없는 세로 이름표 표(`성명 | 김민수`)에서 근거 글에 `{header} 열`이 그대로 나왔다(2026-11-10).
    탐지 근거, 승격 근거, 제외 후보 사유를 모두 넣어 본다(문구표에 없는 근거가 생기면 여기서 걸린다)."""
    from privacy_operator.detect import finding as F
    from privacy_operator.reader.blocks import Location
    from privacy_operator.ui.review import where_text
    from privacy_operator.ui.voice import Voice

    def all_of(prefix):
        return [v for k, v in vars(F).items() if k.startswith(prefix)]

    voice = Voice(name)
    for loc in (Location(table_index=0, row=1, col=1), Location(table_index=0, row=1, col=1, header="성명")):
        for esc in all_of("ESC_"):
            for hint in all_of("HINT_"):
                f = F.Finding("D01-0001", "D01", "a.hwpx", "hwpx", "이름", "김민수", "", "t000-r001-c001", 0, 3, loc,
                              0, tuple(all_of("BY_")), "검토", "대기", "가명처리", escalated_by=esc, exclude_hint=hint)
                text = where_text(f, voice)
                assert "{" not in text and "}" not in text
        assert ("성명 열" in text) == bool(loc.header)


# ---------------------------------------------------------------- 테마

@pytest.mark.parametrize("name", available_themes())
def test_theme_has_all_tokens(name):
    assert set(REQUIRED) <= set(load_theme(name).colors)


@pytest.mark.parametrize("name", available_themes())
def test_theme_contrast(name):
    t = load_theme(name)
    assert t.text_pairs and t.graphic_pairs
    low = [(a, b, round(contrast(t[a], t[b]), 2)) for a, b in t.text_pairs if contrast(t[a], t[b]) < 4.5]
    assert not low
    low = [(a, b) for a, b in t.graphic_pairs if contrast(t[a], t[b]) < 3.0]
    assert not low


@pytest.mark.parametrize("name", available_themes())
def test_screen_backgrounds_are_light(name):
    """화면 안에 어두운 바탕 금지. 어두운 색은 글씨, 확정 칩, 아이콘에만."""
    t = load_theme(name)
    for token in ("paper", "paper_head", "row_selected", "band", "band_2", "band_line", "disabled_bg"):
        assert _luminance(t[token]) > 0.6, token


@pytest.mark.parametrize("name", available_themes())
def test_no_yellow(name):
    t = load_theme(name)
    for token, value in {**t.colors, **t.type_colors}.items():
        r, g, b = (int(value.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, light, s = colorsys.rgb_to_hls(r, g, b)
        assert not (40 <= h * 360 <= 70 and s > 0.4 and 0.25 < light < 0.85), token


def test_pink_theme_uses_design_spec_values():
    t = load_theme("pink")
    # 2026-11-07 정리안: 흰 바탕에 베젤 모브 핑크에서 뽑은 파스텔 로즈 강조
    assert (t["paper"], t["ink"], t["ink_sub"], t["accent"], t["band"], t["metal"], t["label"]) == (
        "#FFFFFF", "#211E24", "#5E5862", "#8E5470", "#FBF9F9", "#E8C4B0", "#7A5566")
    assert (t["accent_fill"], t["on_primary"], t["ring_text"]) == ("#EBBCCB", "#4A2A3A", "#7D5F28")


def test_plain_theme_uses_design_spec_values():
    t = load_theme("plain")
    assert (t["accent"], t["band"], t["metal"], t["label"], t["row_selected"]) == (
        "#2F5D8A", "#EEF1F4", "#C3CBD3", "#5A6470", "#E8F0F8")


def test_review_table_is_the_same_in_both_themes():
    """가운데 검토 표의 바탕과 글씨는 테마와 상관없이 같다. 머리 줄과 선은 테마 결을 따른다
    (단정한 모니터에 핑크의 따뜻한 베이지가 섞여 갈색으로 보였다, 2026-11-08)."""
    a, b = load_theme("pink"), load_theme("plain")
    for token in ("paper", "ink", "ink_sub"):
        assert a[token] == b[token], token


# ---------------------------------------------------------------- 코드 규칙

def _ui_sources():
    return sorted(UI_DIR.rglob("*.py"))


def test_no_color_values_in_ui_code():
    hits = []
    for p in _ui_sources():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"#[0-9A-Fa-f]{6}\b|rgb\(", line):
                hits.append(f"{p.name}:{i}")
    assert not hits


def test_no_korean_text_in_ui_code():
    """화면 문구는 말투 문구표에서만. 주석, docstring, 개발자용 예외 메시지(raise)는 괜찮다."""
    hits = []
    for p in _ui_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        skip = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        skip |= {id(c) for r in ast.walk(tree) if isinstance(r, ast.Raise) for c in ast.walk(r)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                    and re.search(r"[가-힣]", node.value)):
                hits.append(f"{p.name}:{node.lineno}")
    assert not hits


def test_font_sizes_are_at_least_15px():
    assert all(px >= MIN_PX for px, _ in SIZES.values())
    for p in _ui_sources():
        for px in re.findall(r"font-size:\s*(\d+)px", p.read_text(encoding="utf-8")):
            assert int(px) >= MIN_PX, p.name
