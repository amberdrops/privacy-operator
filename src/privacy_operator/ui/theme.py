"""테마: `ui/themes/<이름>/theme.json`의 색 토큰. 화면 코드는 색을 직접 쓰지 않고 역할 이름으로 쓴다.

새 테마는 폴더만 넣으면 된다. 테마마다 글씨-바탕 쌍의 대비를 시험한다(tests/test_ui_theme.py).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from privacy_operator.detect.dictionaries import (
    ACCOUNT,
    ADDRESS,
    BIRTH,
    EMAIL,
    FRN,
    GRAY_CATEGORIES,
    NAME,
    PHONE,
    RRN,
)

THEMES_DIR = Path(__file__).resolve().parent / "themes"
ASSET_PREFIX = "asset"  # Qt 경로 접두어(style.register_assets). 경로에 공백과 한글이 있어도 스타일시트에 쓸 수 있다


def assets_dir() -> Path:
    """Flow 에셋 폴더(assets/<테마>/). 시험이나 배포에서는 PRIVACY_OPERATOR_ASSETS로 바꾼다."""
    override = os.environ.get("PRIVACY_OPERATOR_ASSETS")
    return Path(override) if override else Path(__file__).resolve().parents[3] / "assets"
REQUIRED = (
    "paper", "paper_head", "line", "ink", "ink_sub", "accent", "accent_deep", "on_accent", "row_selected",
    # 연한 강조 채움(고른 칸, 지금 단계)과 그 위 글씨, 주 단추 그림 위 글씨, 고리 단추 글씨, 체크박스 반쯤 표시
    "accent_fill", "on_accent_fill", "on_primary", "ring_text", "ring_text_deep", "metal_deep",
    "band", "band_2", "band_line", "label", "metal", "signal_text", "signal_dot", "night", "focus",
    "step_done_border", "step_todo_text", "step_todo_border", "disabled_bg", "bezel", "bezel_rim", "bezel_ink",
)
# 유형 → 보석 점 색 이름(theme.json의 type_colors 키)
_TYPE_GROUP = {NAME: "name", PHONE: "phone", EMAIL: "email", ADDRESS: "address", RRN: "number", FRN: "number",
               ACCOUNT: "number", BIRTH: "number"}


@dataclass
class Theme:
    name: str
    label_key: str
    decorated: bool
    colors: dict[str, str]
    type_colors: dict[str, str]
    text_pairs: list[tuple[str, str]] = field(default_factory=list)
    graphic_pairs: list[tuple[str, str]] = field(default_factory=list)
    assets: dict = field(default_factory=dict)
    nine_slice: dict = field(default_factory=dict)

    def __getitem__(self, token: str) -> str:
        return self.colors[token]

    def type_group(self, category: str) -> str:
        return "gray" if category in GRAY_CATEGORIES else _TYPE_GROUP.get(category, "gray")

    def type_color(self, category: str) -> str:
        return self.type_colors.get(self.type_group(category), self.type_colors["gray"])

    # ---------------------------------------------------------------- 에셋(10단계)
    # 파일이 없으면 None을 돌려주고, 화면은 색 토큰으로 그린 기본 모양을 쓴다(디자인 명세 'Google Flow 에셋')

    def asset(self, key: str) -> Path | None:
        name, folder = self.assets.get(key), self.assets.get("dir")
        if not isinstance(name, str) or not folder:
            return None
        path = assets_dir() / folder / name
        return path if path.is_file() else None

    def asset_url(self, key: str) -> str | None:
        """스타일시트용 `"asset:<테마>/<파일>"`. 따옴표가 있어야 Qt가 접두어 경로로 읽는다."""
        return f'"{ASSET_PREFIX}:{self.assets["dir"]}/{self.assets[key]}"' if self.asset(key) else None

    def gem(self, category: str) -> Path | None:
        gems, folder = self.assets.get("gems"), self.assets.get("dir")
        name = gems.get(self.type_group(category)) if isinstance(gems, dict) else None
        path = assets_dir() / folder / name if name and folder else None
        return path if path and path.is_file() else None

    def slices(self, key: str) -> tuple[list[int], list[int]] | None:
        """9분할 (자를 곳, 그릴 두께). 둘 다 CSS 순서(위, 오른, 아래, 왼). 자를 곳은 이미지 픽셀, 두께는 화면 px."""
        spec = self.nine_slice.get(key)
        return (list(spec["slice"]), list(spec["border"])) if spec and self.asset(key) else None


def available_themes() -> list[str]:
    return sorted(p.parent.name for p in THEMES_DIR.glob("*/theme.json"))


def load_theme(name: str) -> Theme:
    path = THEMES_DIR / name / "theme.json"
    if not path.is_file():
        path = THEMES_DIR / "plain" / "theme.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in d["colors"]]
    if missing:
        raise ValueError(f"테마 {d['name']}에 색 토큰이 없습니다: {missing}")
    return Theme(d["name"], d["label_key"], d.get("decorated", False), d["colors"], d["type_colors"],
                 [tuple(p) for p in d.get("text_pairs", [])], [tuple(p) for p in d.get("graphic_pairs", [])],
                 d.get("assets", {}), d.get("nine_slice", {}))


# ---------------------------------------------------------------- 대비 (WCAG)

def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)
