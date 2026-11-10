"""Flow 에셋 손질(10단계). 프로그램 실행에는 쓰지 않는 개발 도구다.

    python tools/assets.py all        # assets/_source/ → assets/plain/, assets/pink/ 를 모두 다시 만든다

- 초록 배경 지우기: 초록 정도(G가 R, B보다 얼마나 큰가)로 투명도를 정하고, 가장자리 초록 기운을 걷어 낸다.
- Flow가 오른쪽 아래 구석에 넣는 작은 반짝임 표시(워터마크)는 지우고, 남은 물건의 범위로 잘라 낸다.
- 버튼의 마우스 올림, 누름 모양은 기본 모양을 밝게, 어둡게 손질해 만든다. 모양이 같아야 9분할 기준선이 맞다.
- 원본(assets/_source/)은 바꾸지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SRC, OUT = ROOT / "assets" / "_source", ROOT / "assets"
LOW, HIGH = 25, 70  # 초록 정도가 LOW 이하면 불투명, HIGH 이상이면 투명
WATERMARK = (0.88, 0.80)  # 이 비율보다 오른쪽 아래는 워터마크 자리
LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def _rgba(src: Path, low: float = LOW, high: float = HIGH) -> np.ndarray:
    rgb = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    greenness = g - np.maximum(r, b)
    alpha = np.clip((high - greenness) / (high - low), 0, 1)
    g2 = np.where(greenness > 0, np.maximum(r, b), g)  # 초록 기운 걷어 내기
    out = np.dstack([r, g2, b, alpha * 255])
    h, w = alpha.shape
    out[int(h * WATERMARK[1]):, int(w * WATERMARK[0]):, 3] = 0
    return out


def _crop(a: np.ndarray, pad: int = 2) -> np.ndarray:
    solid = a[..., 3] > 128
    ys, xs = np.where(solid)
    if not len(ys):
        raise SystemExit("물건을 찾지 못했습니다")
    t, b = max(ys.min() - pad, 0), min(ys.max() + 1 + pad, a.shape[0])
    l, r = max(xs.min() - pad, 0), min(xs.max() + 1 + pad, a.shape[1])
    return a[t:b, l:r]


def _save(a: np.ndarray, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA").save(dst, optimize=True)
    print(f"{dst.relative_to(ROOT).as_posix()} {a.shape[1]}x{a.shape[0]}")
    return dst


def key(src: Path, dst: Path, low: float = LOW, high: float = HIGH) -> np.ndarray:
    """low, high: 초록이 든 물건(민트색 보석)은 기준을 올려 물건이 같이 지워지지 않게 한다."""
    a = _crop(_rgba(src, low, high))
    _save(a, dst)
    return a


def split_x(src: Path, dsts: list[Path], low: float = LOW, high: float = HIGH) -> None:
    """가로로 떨어져 놓인 물건들을 왼쪽부터 따로 저장한다(왕관과 하트, 보석 6개)."""
    a = _rgba(src, low, high)
    cols = (a[..., 3] > 128).any(axis=0)
    runs, start = [], None
    for x, on in enumerate(list(cols) + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            if x - start > 8:
                runs.append((start, x))
            start = None
    if len(runs) != len(dsts):
        raise SystemExit(f"물건 {len(dsts)}개를 기대했는데 {len(runs)}개를 찾았습니다")
    for (l, r), dst in zip(runs, dsts):
        part = np.zeros_like(a)
        part[:, l:r] = a[:, l:r]
        _save(_crop(part), dst)


def states(base: np.ndarray, stem: Path, pressed: float = 0.82) -> None:
    """마우스 올림(조금 밝게), 누름(조금 어둡게). 모양과 크기는 그대로다.
    밝히고 어둡히는 정도는 단추 글씨 대비(4.5:1, tests/test_ui_assets.py)가 지켜지는 만큼만.
    흰 글씨 단추는 밝힐수록, 짙은 글씨 단추(핑크 진주)는 어둡힐수록 대비가 떨어진다."""
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGBA")
    rgb, alpha = img.convert("RGB"), img.getchannel("A")
    for name, factor in (("hover", 1.05), ("pressed", pressed)):
        out = ImageEnhance.Brightness(rgb).enhance(factor).convert("RGBA")
        out.putalpha(alpha)
        _save(np.asarray(out).astype(np.float32), stem.with_name(f"{stem.stem.replace('@2x', '')}-{name}@2x.png"))


def recolor_check(a: np.ndarray, dst: Path, stops: list[tuple[float, str]], tile_hex: str | None = None) -> None:
    """체크 표시를 다시 칠한다. 모양과 음영(밝기 순서)은 그대로 두고 밝기를 색 띠(stops)에 옮긴다.
    체크(로즈골드)는 G가 B보다 크고 타일(연분홍 진주)은 B가 G보다 커서 둘을 가를 수 있다. 테두리는 안쪽 76%로 걸러 낸다.
    tile_hex가 있으면 타일의 평균 색을 그 색으로 옮긴다(진주 결과 음영은 그대로). 타일 모양(둥근 안쪽)을 색으로 골라 따라간다.
    로즈골드 그대로는 타일과의 대비가 1.4:1뿐이라 22px에서 체크가 보이지 않았다."""
    h, w = a.shape[:2]
    y, x = np.mgrid[:h, :w]
    inner = (x > 0.12 * w) & (x < 0.88 * w) & (y > 0.12 * h) & (y < 0.88 * h)
    m = Image.fromarray((((a[..., 1] - a[..., 2]) > -2) & inner).astype(np.uint8) * 255)
    for f in (ImageFilter.MaxFilter(15), ImageFilter.MinFilter(15), ImageFilter.MinFilter(9), ImageFilter.MaxFilter(9),
              ImageFilter.GaussianBlur(2)):  # 하이라이트 구멍을 메우고 잔티를 지운 뒤 가장자리만 부드럽게
        m = m.filter(f)
    mask = np.asarray(m).astype(np.float32)[..., None] / 255
    lum = a[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    lo, hi = np.percentile(lum[mask[..., 0] > 0.9], [3, 99.5])
    t = np.clip((lum - lo) / (hi - lo), 0, 1) ** 1.5
    cs = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for _, c in stops], np.float32)
    ramp = np.stack([np.interp(t, [s for s, _ in stops], cs[:, k]) for k in range(3)], -1)
    out = a.copy()
    base = a[..., :3]
    if tile_hex:
        box = (x > 0.04 * w) & (x < 0.96 * w) & (y > 0.04 * h) & (y < 0.96 * h)
        tm = Image.fromarray((((a[..., 2] - a[..., 1]) > 2) & box & (mask[..., 0] < 0.5)).astype(np.uint8) * 255)
        for f in (ImageFilter.MaxFilter(9), ImageFilter.MinFilter(9), ImageFilter.MinFilter(5), ImageFilter.MaxFilter(5),
                  ImageFilter.GaussianBlur(3)):
            tm = tm.filter(f)
        tmask = np.asarray(tm).astype(np.float32)[..., None] / 255
        target = np.array([int(tile_hex[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
        gain = target / base[tmask[..., 0] > 0.9].mean(0)
        base = np.clip(base * (1 - tmask + tmask * gain), 0, 255)
    out[..., :3] = base * (1 - mask) + ramp * mask
    _save(out, dst)


def cool(a: np.ndarray, dst: Path, tint: tuple[float, float, float] = (0.97, 1.0, 1.035)) -> None:
    """누런 기운을 걷고 차가운 은회색으로(단정한 모니터 베젤, 2026-11-08 '갈색' 지적). 밝기와 결은 그대로다."""
    out = a.copy()
    lum = (a[..., :3] * LUMA).sum(-1, keepdims=True)
    out[..., :3] = lum * np.array(tint, np.float32)
    _save(out, dst)


def fill_ring(a: np.ndarray, dst: Path, top_hex: str, bottom_hex: str, under: int = 4) -> None:
    """테두리만 있는 단추(가운데가 뚫린 고리)에 면을 깐다. 가운데에서 이어진 빈 곳을 재서 under px만큼 테두리 밑으로
    넓힌 뒤 색 띠(위→아래)를 칠하고 테두리를 위에 덮는다. 뚫린 고리는 단추가 아니라 구멍처럼 보였다(2026-11-08)."""
    h, w = a.shape[:2]
    hole = np.zeros((h, w), bool)
    stack = [(h // 2, w // 2)]
    alpha = a[..., 3]
    while stack:
        y, x = stack.pop()
        if 0 <= y < h and 0 <= x < w and not hole[y, x] and alpha[y, x] < 128:
            hole[y, x] = True
            stack += [(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)]
    grown = hole.copy()
    for _ in range(under):  # 테두리 안쪽 가장자리(반투명) 밑까지
        g = grown.copy()
        g[1:] |= grown[:-1]; g[:-1] |= grown[1:]; g[:, 1:] |= grown[:, :-1]; g[:, :-1] |= grown[:, 1:]
        grown = g
    ys = np.where(grown.any(axis=1))[0]
    hx = lambda c: np.array([int(c[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    ramp = np.clip((np.arange(h) - ys.min()) / max(1, ys.max() - ys.min()), 0, 1)[:, None, None]
    face = np.zeros_like(a)
    face[..., :3] = hx(top_hex) * (1 - ramp) + hx(bottom_hex) * ramp
    face[..., 3] = grown * 255.0
    fa, ra = face[..., 3:] / 255, a[..., 3:] / 255  # 테두리를 면 위에 덮기
    out_a = ra + fa * (1 - ra)
    out = np.zeros_like(a)
    out[..., :3] = (a[..., :3] * ra + face[..., :3] * fa * (1 - ra)) / np.maximum(out_a, 1e-6)
    out[..., 3] = out_a[..., 0] * 255
    _save(out, dst)


def face_swap(a: np.ndarray, dst: Path, top_hex: str, bottom_hex: str) -> None:
    """어두운 면에 은빛 테두리를 두른 단추 판에서 면만 밝은 색 띠(위→아래)로 바꾼다(주 단추로 보조 단추 만들기).
    테두리와 경계는 밝기로 가른다: 어두운 곳은 새 면 색, 밝은 테두리는 그대로."""
    out = a.copy()
    h, w = a.shape[:2]
    lum = (a[..., :3] * LUMA).sum(-1)
    cy, cx = h // 2, w // 2

    def inner(line: np.ndarray) -> int:  # 가장자리에서 들어와 밝은 테두리를 지난 뒤 처음 어두워지는 곳
        bright = np.where(line > 100)[0]
        return int(bright[bright < len(line) // 2].max()) + 1

    top, left = inner(lum[:, cx]), inner(lum[cy, :])
    bottom, right = h - inner(lum[::-1, cx]), w - inner(lum[cy, ::-1])
    hx = lambda s: np.array([int(s[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    ramp = np.linspace(0, 1, bottom - top)[:, None, None]
    face = hx(top_hex) * (1 - ramp) + hx(bottom_hex) * ramp
    t = np.clip((lum[top:bottom, left:right] - 60) / 80, 0, 1)[..., None]
    out[top:bottom, left:right, :3] = face * (1 - t) + a[top:bottom, left:right, :3] * t
    _save(out, dst)


def set_gem(rim_path: Path, gem_path: Path, dst: Path, tuck: float = 1.06) -> None:
    """빈 테두리(rim) 안에 보석(gem)을 박는다. 테두리 안쪽 빈 곳을 가운데부터 채워 재고, 보석을 그 크기보다
    tuck만큼 크게 넣은 뒤 테두리를 위에 덮는다. 보석 가장자리가 테두리 밑으로 들어가 박힌 것처럼 보인다."""
    rim = Image.open(rim_path).convert("RGBA")
    gem = Image.open(gem_path).convert("RGBA")
    a = np.asarray(rim)[..., 3]
    h, w = a.shape
    hole = np.zeros_like(a, dtype=bool)  # 가운데에서 이어진 투명한 곳 = 안쪽 빈 곳
    stack = [(h // 2, w // 2)]
    while stack:
        y, x = stack.pop()
        if 0 <= y < h and 0 <= x < w and not hole[y, x] and a[y, x] < 128:
            hole[y, x] = True
            stack += [(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)]
    ys, xs = np.where(hole)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    gw = round((x1 - x0) * tuck)
    gh = round(gw * gem.height / gem.width)
    small = gem.resize((gw, gh), Image.LANCZOS)
    out = Image.new("RGBA", rim.size, (0, 0, 0, 0))
    out.alpha_composite(small, (round((x0 + x1) / 2 - gw / 2), round((y0 + y1) / 2 - gh / 2)))
    out.alpha_composite(rim)
    _save(np.asarray(out).astype(np.float32), dst)


def texture(src: Path, dst: Path, base_hex: str, box: tuple[int, int, int, int] | None = None,
            size: int = 512, std: float = 1.3) -> None:
    """띠 질감: 고운 결만 남겨(큰 얼룩, 조명 차이는 걷어 냄) 관제 띠 색(base_hex)에 아주 약하게 얹는다.
    평균 색이 색 토큰과 같으므로 글씨 대비는 토큰으로 잰 값 그대로다. 거울 이어 붙이기로 이음매 없이 반복된다."""
    img = Image.open(src).convert("L")
    if box:
        img = img.crop(box)
    s = min(img.size)
    left, top = (img.width - s) // 2, (img.height - s) // 2
    tile = img.crop((left, top, left + s, top + s)).resize((size // 2, size // 2), Image.LANCZOS)
    g = np.asarray(tile).astype(np.float32)
    fine = g - np.asarray(tile.filter(ImageFilter.GaussianBlur(6))).astype(np.float32)
    fine = fine / (fine.std() or 1) * std
    base = np.array([int(base_hex[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    rgb = np.clip(base[None, None, :] + fine[..., None], 0, 255).astype(np.uint8)
    full = np.vstack([np.hstack([rgb, rgb[:, ::-1]]), np.hstack([rgb[::-1], rgb[::-1, ::-1]])])
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(full).save(dst, optimize=True)
    print(f"{dst.relative_to(ROOT).as_posix()} {full.shape[1]}x{full.shape[0]}")


def splash(src: Path, dst: Path) -> None:
    """시작 화면: 워터마크가 든 아래쪽을 잘라 내고 16:9를 지킨다(가로는 가운데를 남긴다)."""
    img = Image.open(src).convert("RGB")
    h = int(img.height * WATERMARK[1]) - 4
    w = min(img.width, h * 16 // 9)
    left = (img.width - w) // 2
    if left + w > img.width * WATERMARK[0] and h > img.height * WATERMARK[1]:
        raise SystemExit("워터마크가 남습니다")
    out = img.crop((left, 0, left + w, h))
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, optimize=True)
    print(f"{dst.relative_to(ROOT).as_posix()} {out.width}x{out.height}")


def sparkle(src: Path, dst: Path, seconds: float = 4.0, fps: int = 15, width: int = 480) -> None:
    """반짝임 영상 → 투명 배경 움직이는 WebP. 반짝임이 가장 적은 두 순간 사이를 골라 이음매 없이 반복되게 한다."""
    import imageio.v2 as imageio

    reader = imageio.get_reader(str(src), "ffmpeg")
    src_fps = reader.get_meta_data().get("fps", 24)
    step = max(1, round(src_fps / fps))
    fps = src_fps / step  # 실제로 쓰는 초당 장 수(재생 속도가 원본과 같게)
    frames, bg = [], None
    for i, frame in enumerate(reader):
        if i % step:
            continue
        small = Image.fromarray(frame).resize((width, round(frame.shape[0] * width / frame.shape[1])), Image.LANCZOS)
        rgb = np.asarray(small).astype(np.float32)
        if bg is None:  # 배경색: 첫 장 네 귀퉁이의 중앙값(영상 초록은 어두운 초록이다)
            corners = np.concatenate([rgb[:8, :8], rgb[:8, -8:], rgb[-8:, :8], rgb[-8:, -8:]]).reshape(-1, 3)
            bg = np.median(corners, axis=0)
            bg_luma = float(bg @ LUMA)
        # 투명도는 밝기로 정한다. 영상은 색 정보를 밝기보다 거칠게 저장해서, 초록 정도로 정하면 알갱이 가장자리에
        # 어두운 테두리가 남는다. 배경은 어두운 초록, 반짝임은 밝으므로 밝기 차이가 곧 반짝임의 양이다
        alpha = np.clip((rgb @ LUMA - bg_luma - 15) / 110, 0, 1)
        h, w = alpha.shape
        alpha[int(h * WATERMARK[1]):, int(w * WATERMARK[0]):] = 0
        # 색 분리: 가장자리에 섞인 배경 초록을 빼서 알갱이 본래 색을 되살린다(어두운 테두리가 남지 않게)
        a = np.maximum(alpha, 0.05)[..., None]
        fg = np.clip((rgb - (1 - a) * bg[None, None, :]) / a, 0, 255)
        fg[..., 1] = np.minimum(fg[..., 1], np.maximum(fg[..., 0], fg[..., 2]))
        frames.append(np.dstack([fg, alpha * 255]))
    reader.close()
    n = max(2, round(seconds * fps))
    if len(frames) <= n:
        raise SystemExit("영상이 너무 짧습니다")
    cover = [float((f[..., 3] > 76).mean()) for f in frames]
    start = min(range(len(frames) - n), key=lambda s: cover[s] + cover[s + n])
    clip = frames[start:start + n]
    fade = max(3, n // 8)  # 끝에 남은 반짝임은 서서히 지워, 다시 처음으로 돌아가도 튀지 않게 한다
    for k in range(fade):
        clip[-1 - k][..., 3] *= k / fade
    clip[0][..., 3] *= 0.5
    out = [Image.fromarray(np.clip(f, 0, 255).astype(np.uint8), "RGBA") for f in clip]
    dst.parent.mkdir(parents=True, exist_ok=True)
    out[0].save(dst, save_all=True, append_images=out[1:], duration=round(1000 / fps), loop=0, lossless=False,
                quality=80, method=4)
    print(f"{dst.relative_to(ROOT).as_posix()} {out[0].width}x{out[0].height} 프레임 {len(out)} "
          f"(원본 {start * step / src_fps:.1f}초부터)")


def _band(theme: str) -> str:
    import json
    path = ROOT / "src" / "privacy_operator" / "ui" / "themes" / theme / "theme.json"
    return json.loads(path.read_text(encoding="utf-8"))["colors"]["band"]


def build_all() -> None:
    for theme in ("plain", "pink"):
        s, o = SRC / theme, OUT / theme
        bezel = key(s / "bezel_2K.jpg", o / "bezel@2x.png")
        if theme == "plain":
            cool(bezel, o / "bezel@2x.png")
        # 핑크 주 단추: 연분홍 진주 알약(로즈골드 테두리, 짙은 모브 글씨). 처음 만든 로즈 단추는 btn-primary.jpg로 남긴다
        # 단정한 모니터 주 단추: 모서리만 둥근 남색 에나멜 판에 은빛 테두리(2026-11-08, 알약 모양 대신)
        primary = s / "btn-primary-pearl-b_2K.jpg" if theme == "pink" else s / "btn-navy-a_2K.jpg"
        # 핑크는 짙은 모브 글씨라 누름을 살짝만 어둡힌다(0.82면 대비 3.9:1)
        base = key(primary, o / "btn-primary@2x.png")
        states(base, o / "btn-primary@2x.png", 0.92 if theme == "pink" else 0.82)
        if theme == "plain":  # 보조 단추는 같은 판에서 면만 밝게 바꿔 모양과 테두리를 맞춘다
            face_swap(base, o / "btn-secondary@2x.png", "#FBFCFD", "#E9EDF1")
        else:  # 핑크 보조 단추: 금빛 고리 안에 연한 진주 면(뚫린 고리는 구멍처럼 보였다)
            fill_ring(key(s / "btn-secondary.jpg", o / "btn-secondary@2x.png"), o / "btn-secondary@2x.png",
                      "#FFFDFE", "#F1DCE5")
    # 단정한 모니터 엠블럼: 남색 에나멜 원판의 은빛 금고 다이얼(2026-11-08, 모니터 아이콘 대신)
    key(SRC / "plain" / "emblem-dial_2K.jpg", OUT / "plain" / "emblem@2x.png")
    # 띠 질감: 핑크는 Flow 공단 질감, 단정한 모니터는 Flow 회색 질감이 정책 검사에 세 번 걸려 베젤 몸체(왼쪽 변)의 결을 쓴다
    texture(SRC / "pink" / "band-texture.jpg", OUT / "pink" / "band-texture.png", _band("pink"))
    texture(SRC / "plain" / "bezel_2K.jpg", OUT / "plain" / "band-texture.png", _band("plain"), box=(380, 420, 520, 1120))
    p = SRC / "pink"
    splash(p / "splash_2K.jpg", OUT / "pink" / "splash@2x.png")
    sparkle(p / "sparkle.mp4", OUT / "pink" / "sparkle.webp")  # Veo 3.1 Lite. 첫 주기(터지고 사라짐) 4초
    key(p / "gems.jpg", OUT / "pink" / "gems@2x.png", low=60, high=110)
    # 유형 표시에 하나씩 쓴다: gem-1(장미) gem-2(라벤더) gem-3(하늘) gem-4(샴페인) gem-5(진주) gem-6(민트)
    split_x(p / "gems.jpg", [OUT / "pink" / f"gem-{i}@2x.png" for i in range(1, 7)], low=60, high=110)
    key(p / "pearl-strand_2K.jpg", OUT / "pink" / "pearl-strand@2x.png")
    split_x(p / "crown-heart.jpg", [OUT / "pink" / "crown@2x.png", OUT / "pink" / "heart@2x.png"])
    # 대기 판정: 하트 보석과 같은 모양의 빈 금색 테두리(heart-gem-ref.jpg를 Flow에서 편집, 2026-11-08)
    key(p / "heart-rim_2K.jpg", OUT / "pink" / "heart-rim@2x.png")
    # 확정 판정: 같은 테두리 안에 하트 보석을 박는다(세공). 대기와 테두리가 같아 크기, 자리가 그대로다
    set_gem(OUT / "pink" / "heart-rim@2x.png", OUT / "pink" / "heart@2x.png", OUT / "pink" / "heart-set@2x.png")
    # 진척 원 후보: 골드 테두리 흰 진주 띠(띠 = 반지름 67~100%), 골드 줄에 꿴 진주 알(88~100%)
    base = key(p / "progress-band.jpg", OUT / "pink" / "progress-band@2x.png")
    key(p / "progress-pearls.jpg", OUT / "pink" / "progress-pearls@2x.png")
    # 진척 원 채움: 같은 고리를 Flow에서 연분홍 진주로 편집한 것. 바탕 고리와 정확히 겹치게 같은 크기로 맞춘다
    fill = key(p / "progress-band-fill_2K.jpg", OUT / "pink" / "progress-band-fill@2x.png")
    img = Image.fromarray(np.clip(fill, 0, 255).astype(np.uint8), "RGBA").resize(
        (base.shape[1], base.shape[0]), Image.LANCZOS)
    _save(np.asarray(img).astype(np.float32), OUT / "pink" / "progress-band-fill@2x.png")
    # 진행 단계 동그라미: 흰 진주(끝남, 남음), 연분홍 진주(지금). 번호와 체크는 코드로 얹는다
    key(p / "step-pearl.jpg", OUT / "pink" / "step-pearl@2x.png")
    key(p / "step-pearl-current.jpg", OUT / "pink" / "step-pearl-current@2x.png")
    key(p / "step-pearl-done.jpg", OUT / "pink" / "step-pearl-done@2x.png")  # 흰 진주에 로즈골드 체크(Flow 편집)
    # 목록 체크박스: 켜짐 = 베젤색 진주 타일에 흰 진주 체크(로즈골드 타일 check-on.jpg를 Flow에서 편집), 꺼짐 = 로즈골드 테두리
    # 타일은 베젤 옆면 평균 색(#E2B7C7). 흰 몸통만으로는 타일과 대비가 약해, 체크 가장자리 음영을 모브로 잡아 윤곽을 세운다
    recolor_check(key(p / "check-on-pearl_2K.jpg", OUT / "pink" / "check-on@2x.png"), OUT / "pink" / "check-on@2x.png",
                  [(0, "#6E3A53"), (0.08, "#A9708A"), (0.22, "#F3E9EE"), (0.5, "#FFFFFF"), (1, "#FFFFFF")],
                  tile_hex="#E2B7C7")
    key(p / "check-off.jpg", OUT / "pink" / "check-off@2x.png")


def main(argv: list[str]) -> int:
    if argv[:1] == ["all"]:
        build_all()
        return 0
    if len(argv) >= 3 and argv[0] == "key":
        key(Path(argv[1]), Path(argv[2]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
