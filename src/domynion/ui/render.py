"""지도를 그림 한 장으로 굽는다 (PIL·numpy). Qt 없이 도는 순수 렌더러다.

화면 없이 판을 찍어 볼 수 있어야 한다 — 헤드리스로 수백 판을 돌리다 "이 판은 왜
이렇게 끝났나" 를 볼 때, 창을 띄우는 것보다 프레임을 꺼내 보는 쪽이 빠르다.

**타일별 파이썬 루프를 쓰지 않는다.** 지도가 12만~30만 칸이라 칸마다 무언가 하면
그림 한 장에 수십 초가 걸린다. 경계선까지 전부 배열 연산으로 처리한다
(실측: 경계 마스크 0.1ms).

색 규칙은 `palette.py`. 격자선을 그리지 않고 소유자가 다른 변에만 선을 넣는다 —
그래야 영토가 덩어리로 읽힌다.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..core.constants import Terrain
from ..core.gamemap import GameMap
from . import palette as P

_TERRAIN_LUT = np.array(
    [P.TERRAIN_COLORS[Terrain(i)] for i in range(len(Terrain))], dtype=np.float32)


def _texture(h: int, w: int, scale: int, seed: int) -> np.ndarray:
    """타일보다 큰 주기의 부드러운 노이즈.

    작은 배열을 만들어 **BICUBIC 으로 확대**하는 것이 핵심이다. 타일마다 값을 뽑으면
    칸 경계에서 끊겨, 격자를 지운 자리에 체크무늬가 다시 생긴다."""
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, h // P.TEXTURE_PERIOD + 2),
                        max(2, w // P.TEXTURE_PERIOD + 2)))
    img = Image.fromarray((small * 255).astype(np.uint8))
    img = img.resize((w * scale, h * scale), Image.BICUBIC)
    return (np.asarray(img, dtype=np.float32) / 255.0 - 0.5) * 2.0


def render(gmap: GameMap, scale: int = 2, seed: int = 0,
           labels: dict[int, str] | None = None,
           title: str | None = None,
           kinds: dict[int, str] | None = None) -> Image.Image:
    """지도 한 장. `scale` 은 타일 한 변의 픽셀 수.

    `kinds` 를 주면 **종류별 통**에서 색을 뽑는다(§5.95). 안 주면 전부 나라
    색으로 그린다 — 이 렌더러는 지도 그림만 뽑는 용도라 종류를 모를 때가 있다."""
    h, w = gmap.height, gmap.width
    terrain = gmap.terrain.reshape(h, w)
    owner = gmap.owner.reshape(h, w)

    base = _TERRAIN_LUT[terrain]                       # (h, w, 3)

    owned = owner >= 0
    if owned.any():
        n = int(owner.max()) + 1
        lut = np.array([P.player_color(pid, (kinds or {}).get(pid, "nation"))
                        for pid in range(n)], dtype=np.float32)
        idx = np.where(owned, owner, 0)
        b = P.OWNER_BLEND
        base = np.where(owned[..., None], base * (1 - b) + lut[idx] * b, base)

    px = np.repeat(np.repeat(base, scale, axis=0), scale, axis=1)
    water = np.repeat(np.repeat(terrain == Terrain.OCEAN, scale, axis=0),
                      scale, axis=1)[..., None]
    tex = _texture(h, w, scale, seed)[..., None]
    px = np.clip(px * (1.0 + tex * np.where(water, P.TEXTURE_AMP * 0.5, P.TEXTURE_AMP)),
                 0, 255).astype(np.uint8)

    _paint_borders(px, owner, terrain, scale)

    img = Image.fromarray(px, mode="RGB")
    draw = ImageDraw.Draw(img)
    if labels:
        _draw_labels(draw, owner, labels, scale)
    if title:
        _draw_text(draw, (10, 8), title, _font(15), anchor="la")
    return img


def _paint_borders(px: np.ndarray, owner: np.ndarray, terrain: np.ndarray,
                   scale: int) -> None:
    """소유자가 갈리는 변에만 선을 긋는다 — 전부 배열 연산으로.

    같은 소유자끼리는 선이 없으므로 영토가 하나의 덩어리로 읽힌다. 육지-바다 경계는
    건드리지 않는다: 해안선은 국경이 아니고, 색만으로 이미 갈린다."""
    land = terrain != Terrain.OCEAN
    color = np.array(P.BORDER_COLOR, dtype=np.uint8)
    thick = max(1, scale // 3)

    # 세로 경계 — (x, x+1) 이 다른 소유자이고 둘 다 육지
    v = (owner[:, :-1] != owner[:, 1:]) & land[:, :-1] & land[:, 1:]
    ys, xs = np.nonzero(v)
    for off in range(thick):
        col = np.clip((xs + 1) * scale - thick // 2 + off, 0, px.shape[1] - 1)
        for r in range(scale):
            px[ys * scale + r, col] = color

    # 가로 경계
    hbor = (owner[:-1, :] != owner[1:, :]) & land[:-1, :] & land[1:, :]
    ys, xs = np.nonzero(hbor)
    for off in range(thick):
        row = np.clip((ys + 1) * scale - thick // 2 + off, 0, px.shape[0] - 1)
        for c in range(scale):
            px[row, xs * scale + c] = color


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
                 labels: dict[int, str], scale: int) -> None:
    """폰트 크기는 **영토가 실제로 차지한 폭**에서 뽑는다. 타일 크기에 비례시키면
    큰 나라 이름이 화면을 덮고, 고정하면 작은 나라 위에서 넘친다."""
    for pid, name in labels.items():
        ys, xs = np.nonzero(owner == pid)
        if len(xs) < 30:
            continue
        span = max(int(xs.max() - xs.min()) + 1, 1)
        size = int(np.clip(span * scale / max(len(name), 3) * 0.8, 11, 44))
        _draw_text(draw, ((xs.mean() + 0.5) * scale, (ys.mean() + 0.5) * scale),
                   name, _font(size))
