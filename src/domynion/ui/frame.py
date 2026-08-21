"""프레임 생성 — **타일 해상도로 만들고 확대는 Qt 에 맡긴다.**

실측이 설계를 정했다:

| 방식 | 500×250 지도 한 장 |
|---|---|
| 픽셀 해상도로 `np.repeat` 후 합성 (`render.py`) | 69ms → **14.5 fps** |
| 타일 해상도(500×250)로 만들고 Qt 가 확대 | **4.0ms → 253 fps** |

17배 차이는 전부 픽셀 확대에서 나온다. 12만 타일을 scale 2 로 펼치면 50만 픽셀 ×
3채널을 매 프레임 만들고 자르고 형변환하게 되는데, 그 일은 GPU 가 공짜로 해 준다.

**지형 바닥은 한 번만 굽는다.** 지형은 핵이 터질 때만 바뀌므로 그때만 다시 굽는다.
매 프레임 하는 일은 소유자 색을 섞는 것 하나뿐이다.

`render.py`(PIL 렌더러)는 그대로 둔다 — 그쪽은 창 없이 그림 파일을 뽑는 용도라
프레임률이 상관없고, 라벨·질감을 더 곱게 그린다.
"""

from __future__ import annotations

import numpy as np

from ..core.constants import Terrain
from ..core.gamemap import GameMap
from . import palette as P

_TERRAIN_LUT = np.array(
    [P.TERRAIN_COLORS[Terrain(i)] for i in range(len(Terrain))], dtype=np.float32)
_PLAYER_LUT = np.array(P.PLAYER_COLORS, dtype=np.float32)


class FrameBuilder:
    """지형 바닥을 캐시하고 매 프레임 소유자 색만 얹는다."""

    __slots__ = ("gmap", "_base", "_land", "_terrain_version")

    def __init__(self, gmap: GameMap, seed: int = 0):
        self.gmap = gmap
        self._terrain_version = -1
        self._base = np.zeros((gmap.height, gmap.width, 3), dtype=np.float32)
        self._land = np.zeros((gmap.height, gmap.width), dtype=bool)
        self.rebake(seed)

    # --- 지형 바닥 --------------------------------------------------------

    def rebake(self, seed: int = 0) -> None:
        """지형 + 질감을 굽는다. **핵이 지형을 바꿨을 때만** 다시 부른다."""
        h, w = self.gmap.height, self.gmap.width
        terrain = self.gmap.terrain.reshape(h, w)
        base = _TERRAIN_LUT[terrain]

        # 질감은 타일보다 큰 주기에서 뽑는다. 타일마다 값을 뽑으면 격자를 지운 자리에
        # 체크무늬가 생긴다 — 설계 5절의 함정 그대로다.
        rng = np.random.default_rng(seed)
        small = rng.random((max(2, h // P.TEXTURE_PERIOD + 2),
                            max(2, w // P.TEXTURE_PERIOD + 2))).astype(np.float32)
        tex = _upscale_bilinear(small, h, w) * 2.0 - 1.0
        self._land = terrain != Terrain.OCEAN
        amp = np.where(self._land, P.TEXTURE_AMP, P.TEXTURE_AMP * 0.5)
        self._base = np.clip(base * (1.0 + tex * amp)[..., None], 0, 255)

    # --- 매 프레임 --------------------------------------------------------

    def rgb(self) -> np.ndarray:
        """소유자 색이 섞인 (h, w, 3) uint8. **연속 메모리**로 돌려준다 —
        QImage 가 그 버퍼를 그대로 참조하기 때문이다."""
        h, w = self.gmap.height, self.gmap.width
        owner = self.gmap.owner.reshape(h, w)
        owned = owner >= 0
        out = self._base
        if owned.any():
            idx = np.where(owned, owner, 0) % len(P.PLAYER_COLORS)
            b = P.OWNER_BLEND
            out = np.where(owned[..., None],
                           self._base * (1 - b) + _PLAYER_LUT[idx] * b, self._base)
        return np.ascontiguousarray(out.astype(np.uint8))

    def border_segments(self) -> tuple[np.ndarray, np.ndarray]:
        """국경선으로 그릴 변들. `(세로변, 가로변)` 각각 (n, 2) 타일 좌표.

        **소유자가 다르고 양쪽 다 육지인 변만**이다. 해안선은 국경이 아니라 색으로
        이미 갈리고, 그걸 함께 그리면 화면이 선으로 뒤덮인다."""
        h, w = self.gmap.height, self.gmap.width
        owner = self.gmap.owner.reshape(h, w)
        land = self._land
        vb = (owner[:, :-1] != owner[:, 1:]) & land[:, :-1] & land[:, 1:]
        hb = (owner[:-1, :] != owner[1:, :]) & land[:-1, :] & land[1:, :]
        vy, vx = np.nonzero(vb)
        hy, hx = np.nonzero(hb)
        return (np.stack([vx + 1, vy], axis=1) if len(vx) else np.empty((0, 2), int),
                np.stack([hx, hy + 1], axis=1) if len(hx) else np.empty((0, 2), int))

    def label_anchors(self, players) -> list[tuple[int, float, float, float]]:
        """`(pid, 중심x, 중심y, 영토 폭)`. 폰트 크기는 **영토 덩어리의 실제 폭**에서
        뽑아야 한다 — 타일 비례로 잡으면 큰 나라 이름이 화면을 덮는다."""
        h, w = self.gmap.height, self.gmap.width
        owner = self.gmap.owner.reshape(h, w)
        out = []
        for p in players:
            ys, xs = np.nonzero(owner == p.pid)
            if len(xs) < 30:
                continue
            out.append((p.pid, float(xs.mean()), float(ys.mean()),
                        float(xs.max() - xs.min() + 1)))
        return out


def _upscale_bilinear(small: np.ndarray, h: int, w: int) -> np.ndarray:
    """작은 격자를 부드럽게 늘린다. PIL 없이 numpy 만으로."""
    sh, sw = small.shape
    ys = np.linspace(0, sh - 1, h, dtype=np.float32)
    xs = np.linspace(0, sw - 1, w, dtype=np.float32)
    y0 = np.clip(np.floor(ys), 0, sh - 2).astype(np.int32)
    x0 = np.clip(np.floor(xs), 0, sw - 2).astype(np.int32)
    fy = (ys - y0)[:, None]
    fx = (xs - x0)[None, :]
    fy = fy * fy * (3 - 2 * fy)          # smoothstep — 선형보다 이음매가 덜 보인다
    fx = fx * fx * (3 - 2 * fx)
    g = small
    top = g[np.ix_(y0, x0)] * (1 - fx) + g[np.ix_(y0, x0 + 1)] * fx
    bot = g[np.ix_(y0 + 1, x0)] * (1 - fx) + g[np.ix_(y0 + 1, x0 + 1)] * fx
    return top * (1 - fy) + bot * fy
