"""지도를 그림 한 장으로 굽는다 (PIL·numpy). Qt 없이 도는 순수 렌더러다.

Qt 위젯이 아니라 여기 둔 이유: **화면 없이 판을 찍어 볼 수 있어야 한다.** 헤드리스로
240판을 돌리다가 "이 판이 왜 이렇게 끝났지" 를 볼 때, 창을 띄우지 않고 프레임을
꺼내 보는 쪽이 훨씬 빠르다. Qt 위젯은 나중에 이 색·규칙을 그대로 가져다 쓴다.

5절 그래픽 함정을 지킨다:
- 격자선을 그리지 않는다. 소유자가 다른 변에만 경계선을 넣는다
- 질감 노이즈는 타일보다 **큰 주기**에서 뽑아 보간한다. 타일 안에 가두면 체크무늬가 된다
- 타일 사각형은 정수 픽셀에 맞춘다 (`np.repeat` 이라 자동으로 맞는다)
- 라벨 폰트는 타일 크기가 아니라 **영토 덩어리의 실제 폭**에 맞춘다
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..core.constants import Terrain
from ..core.gamemap import GameMap
from . import palette as P

TERRAIN_ORDER = list(Terrain)
_TERRAIN_INDEX = {t: i for i, t in enumerate(TERRAIN_ORDER)}


def _texture(h: int, w: int, tile: int, seed: int) -> np.ndarray:
    """타일보다 큰 주기의 부드러운 노이즈를 픽셀 해상도로 펼친다.

    작은 배열을 만들어 **BICUBIC 으로 확대**하는 것이 핵심이다. 타일마다 값을 뽑으면
    칸 경계에서 값이 끊겨, 격자를 지운 자리에 체크무늬가 다시 생긴다."""
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, h // P.TEXTURE_PERIOD + 2),
                        max(2, w // P.TEXTURE_PERIOD + 2)))
    img = Image.fromarray((small * 255).astype(np.uint8))
    img = img.resize((w * tile, h * tile), Image.BICUBIC)
    return (np.asarray(img, dtype=np.float32) / 255.0 - 0.5) * 2.0


def render(gmap: GameMap, tile: int = 14, seed: int = 0,
           labels: dict[int, str] | None = None,
           title: str | None = None) -> Image.Image:
    """지도 한 장. `labels` 는 pid → 화면에 쓸 이름."""
    h, w = gmap.height, gmap.width

    terrain = np.zeros((h, w), dtype=np.int8)
    owner = np.full((h, w), -1, dtype=np.int16)
    for (x, y), t in gmap.tiles.items():
        terrain[y, x] = _TERRAIN_INDEX[t.terrain]
        if t.owner is not None:
            owner[y, x] = t.owner

    # 1) 지형 색 -----------------------------------------------------------
    lut = np.array([P.TERRAIN_COLORS[t] for t in TERRAIN_ORDER], dtype=np.float32)
    base = lut[terrain]                                    # (h, w, 3)

    # 2) 소유자 색을 섞는다. 바다에는 섞지 않는다 — 물은 누구 것도 아니다.
    water = terrain == _TERRAIN_INDEX[Terrain.WATER]
    owned = (owner >= 0) & ~water
    if owned.any():
        pc = np.array(P.PLAYER_COLORS, dtype=np.float32)
        tint = pc[np.clip(owner, 0, len(P.PLAYER_COLORS) - 1) % len(P.PLAYER_COLORS)]
        b = P.OWNER_BLEND
        base = np.where(owned[..., None], base * (1 - b) + tint * b, base)

    # 3) 픽셀로 펼치고 질감을 얹는다 ----------------------------------------
    px = np.repeat(np.repeat(base, tile, axis=0), tile, axis=1)
    tex = _texture(h, w, tile, seed)[..., None]
    water_px = np.repeat(np.repeat(water, tile, axis=0), tile, axis=1)[..., None]
    amp = np.where(water_px, P.TEXTURE_AMP * 0.5, P.TEXTURE_AMP)   # 물은 잔잔하게
    px = np.clip(px * (1.0 + tex * amp), 0, 255).astype(np.uint8)

    img = Image.fromarray(px, mode="RGB")
    draw = ImageDraw.Draw(img)

    # 4) 경계선 — 소유자가 다른 변에만. 이 선이 화면의 유일한 선이다 ---------
    _draw_edges(draw, owner, water, tile)

    # 5) 이름 --------------------------------------------------------------
    if labels:
        _draw_labels(draw, owner, labels, tile)
    if title:
        _draw_text(draw, (10, 8), title, _font(15), anchor="la")
    return img


def _draw_edges(draw: ImageDraw.ImageDraw, owner: np.ndarray,
                water: np.ndarray, tile: int) -> None:
    """소유자가 갈리는 변에만 선을 긋는다.

    같은 소유자끼리는 선이 없으므로 영토가 하나의 덩어리로 읽힌다. 육지-바다 경계는
    더 얇고 어둡게 — 해안선은 국경이 아니다."""
    h, w = owner.shape
    thick = max(2, tile // 6)

    for y in range(h):
        for x in range(w):
            if water[y, x]:
                continue
            o = owner[y, x]
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = x + dx, y + dy
                if nx >= w or ny >= h:
                    continue
                n_water = water[ny, nx]
                if not n_water and owner[ny, nx] == o:
                    continue
                if n_water and o < 0:
                    continue           # 중립 육지와 바다 사이는 색만으로 충분하다
                color = P.COAST_COLOR if n_water else P.BORDER_COLOR
                width = 1 if n_water else thick
                if dx:
                    ex = (x + 1) * tile
                    draw.line([(ex, y * tile), (ex, (y + 1) * tile)],
                              fill=color, width=width)
                else:
                    ey = (y + 1) * tile
                    draw.line([(x * tile, ey), ((x + 1) * tile, ey)],
                              fill=color, width=width)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("malgun.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_text(draw, xy, text: str, font, anchor: str = "mm") -> None:
    x, y = xy
    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.text((x + ox, y + oy), text, font=font, fill=P.LABEL_SHADOW, anchor=anchor)
    draw.text(xy, text, font=font, fill=P.LABEL_COLOR, anchor=anchor)


def _draw_labels(draw: ImageDraw.ImageDraw, owner: np.ndarray,
                 labels: dict[int, str], tile: int) -> None:
    """이름을 영토 중심에 쓴다.

    폰트 크기는 **영토가 실제로 차지한 폭**에서 뽑는다. 타일 크기에 비례시키면 큰
    나라 이름이 화면을 덮고, 고정하면 작은 나라 위에서 넘친다."""
    for pid, name in labels.items():
        ys, xs = np.nonzero(owner == pid)
        if len(xs) < 8:
            continue                    # 너무 작으면 글자가 영토보다 커진다
        span = max(xs.max() - xs.min() + 1, 1)
        size = int(np.clip(span * tile / max(len(name), 3) * 0.85, 11, 40))
        cx = (xs.mean() + 0.5) * tile
        cy = (ys.mean() + 0.5) * tile
        _draw_text(draw, (cx, cy), name, _font(size))
