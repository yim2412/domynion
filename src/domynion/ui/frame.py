"""프레임 생성 — **지형은 한 번 굽고, 소유자만 매 프레임 얹는다.**

실측이 설계를 두 번 정했다.

1차 (타일 해상도 vs 픽셀 해상도):

| | 500×250 한 장 |
|---|---|
| 픽셀 해상도로 `np.repeat` 후 합성 | 69ms → 14.5 fps |
| **타일 해상도로 만들고 확대는 Qt** | 4.0ms → 253 fps |

2차 (지형+소유자를 매번 합성 vs 소유자만 오버레이):

| | 1000×500 | 2000×1000 |
|---|---|---|
| 매 프레임 float 블렌드 후 uint8 변환 | 16.2ms (62fps) | 59.6ms (**17fps**) |
| **소유자 RGBA 오버레이만** | 4.4ms (227fps) | 17.2ms (**58fps**) |

지형은 판 내내 안 바뀐다(핵이 터질 때만). 그러니 지형+질감을 **한 번만** 굽고,
매 프레임 하는 일은 `owner` 배열을 RGBA 로 조회하는 **uint8 gather 하나**로 줄인다.
합성은 Qt 가 알파로 한다 — 그쪽이 GPU 일이다.
"""

from __future__ import annotations

import numpy as np

from ..core.constants import Terrain
from ..core.gamemap import GameMap
from . import palette as P

_TERRAIN_LUT = np.array(
    [P.TERRAIN_COLORS[Terrain(i)] for i in range(len(Terrain))], dtype=np.float32)


def _owner_rgba_lut() -> np.ndarray:
    """`owner + 1` 로 조회하는 표. 0번(중립)은 **완전 투명**이다."""
    lut = np.zeros((len(P.PLAYER_COLORS) + 1, 4), dtype=np.uint8)
    alpha = int(round(P.OWNER_BLEND * 255))
    for i, (r, g, b) in enumerate(P.PLAYER_COLORS):
        lut[i + 1] = (r, g, b, alpha)
    return lut


_OWNER_LUT = _owner_rgba_lut()


class FrameBuilder:
    """지형 바닥(RGB, 고정)과 소유자 층(RGBA, 매 프레임)을 따로 낸다."""

    __slots__ = ("gmap", "_base", "_land", "_overlay", "_lod")

    def __init__(self, gmap: GameMap, seed: int = 0):
        self.gmap = gmap
        self._base = np.zeros((gmap.height, gmap.width, 3), dtype=np.uint8)
        self._land = np.zeros((gmap.height, gmap.width), dtype=bool)
        self._overlay: np.ndarray | None = None
        # 축소해서 볼 때 쓰는 지형 사본(stride → 배열). 매번 슬라이스하면 연속화
        # 비용이 붙으므로 미리 만들어 둔다.
        self._lod: dict[int, np.ndarray] = {}
        self.rebake(seed)

    # --- 지형 바닥 (한 번만) ----------------------------------------------

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
        self._base = np.ascontiguousarray(
            np.clip(base * (1.0 + tex * amp)[..., None], 0, 255).astype(np.uint8))
        self._lod = {1: self._base}

    @property
    def terrain_rgb(self) -> np.ndarray:
        """지형 바닥 (h, w, 3) uint8. 판 내내 같은 배열이다."""
        return self._base

    def terrain_lod(self, stride: int) -> np.ndarray:
        """`stride` 칸마다 한 번 뽑은 지형. 축소해서 볼 때 쓴다."""
        if stride not in self._lod:
            self._lod[stride] = np.ascontiguousarray(self._base[::stride, ::stride])
        return self._lod[stride]

    # --- 소유자 층 (매 프레임) --------------------------------------------

    def owner_rgba(self, stride: int = 1, y0: int = 0,
                   y1: int | None = None) -> np.ndarray:
        """소유자 색 (h, w, 4) uint8. 중립은 알파 0 이라 지형이 그대로 비친다.

        `stride` 는 **축소해서 볼 때** 몇 칸마다 뽑을지다. 화면에서 이미 줄어들어
        보이는데 원본 해상도로 만들 이유가 없다 — 2000×1000 에서 stride 2 로 하면
        17.0ms → 4.3ms 다(실측).

        `y0`/`y1` 은 **보이는 줄만** 만들라는 뜻이다. 확대하면 화면에 몇 줄 안 보이는데
        전체를 만들면 2000×1000 에서 165ms(6fps)가 된다 — 그때가 가장 느리다.
        x 는 자르지 않는다: 가로가 순환해서 범위가 두 조각으로 갈릴 수 있고,
        그 처리 비용이 아끼는 것보다 크다.

        **연속 메모리**로 돌려준다 — QImage 가 이 버퍼를 그대로 참조한다."""
        h, w = self.gmap.height, self.gmap.width
        y1 = h if y1 is None else min(h, y1)
        y0 = max(0, min(y0, y1))
        owner = self.gmap.owner.reshape(h, w)[y0:y1]
        if stride > 1:
            owner = owner[::stride, ::stride]
        idx = owner + 1
        if len(P.PLAYER_COLORS) < 64:            # pid 가 색 수를 넘으면 감싼다
            over = idx > len(P.PLAYER_COLORS)
            if over.any():
                idx = np.where(over, (owner % len(P.PLAYER_COLORS)) + 1, idx)
        self._overlay = np.ascontiguousarray(_OWNER_LUT[idx])
        return self._overlay

    # --- 국경·라벨 --------------------------------------------------------

    def border_segments(self, view: tuple[int, int, int, int] | None = None
                        ) -> tuple[np.ndarray, np.ndarray]:
        """국경선으로 그릴 변들. `(세로변, 가로변)` 각각 (n, 2) 타일 좌표.

        `view` 는 `(x0, y0, x1, y1)` 타일 범위 — **화면에 보이는 곳만** 계산한다.
        원본 크기 지도(200만 칸)에서 전체를 훑으면 18ms 가 든다.

        소유자가 다르고 양쪽 다 육지인 변만이다. 해안선은 국경이 아니라 색으로 이미
        갈리고, 그걸 함께 그리면 화면이 선으로 뒤덮인다."""
        h, w = self.gmap.height, self.gmap.width
        x0, y0, x1, y1 = view if view else (0, 0, w, h)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            return np.empty((0, 2), int), np.empty((0, 2), int)

        owner = self.gmap.owner.reshape(h, w)[y0:y1, x0:x1]
        land = self._land[y0:y1, x0:x1]
        # 세로 변과 가로 변을 **따로** 본다. 하나로 묶어 "폭이든 높이든 2 미만이면
        # 포기"로 두면 1행짜리 지도에서 세로 국경이 통째로 사라진다(실제로 그랬다).
        if owner.shape[1] >= 2:
            vb = (owner[:, :-1] != owner[:, 1:]) & land[:, :-1] & land[:, 1:]
            vy, vx = np.nonzero(vb)
        else:
            vy = vx = np.empty(0, dtype=int)
        if owner.shape[0] >= 2:
            hb = (owner[:-1, :] != owner[1:, :]) & land[:-1, :] & land[1:, :]
            hy, hx = np.nonzero(hb)
        else:
            hy = hx = np.empty(0, dtype=int)
        return (np.stack([vx + x0 + 1, vy + y0], axis=1) if len(vx)
                else np.empty((0, 2), int),
                np.stack([hx + x0, hy + y0 + 1], axis=1) if len(hx)
                else np.empty((0, 2), int))

    def border_pairs(self, v: np.ndarray, h: np.ndarray
                     ) -> tuple[np.ndarray, np.ndarray]:
        """국경 변마다 **양쪽 칸 번호**. `(세로변용, 가로변용)` 각각 (n, 2).

        소유자가 아니라 칸을 돌려주는 이유는, 색을 정하는 데 **관계**(소유자)와
        **방어 여부**(칸 + 소유자)가 둘 다 필요하기 때문이다.

        `border_segments` 가 좌표만 돌려주는데, 국경 색을 관계로 갈리게 하려면
        (원본 `PlayerView.borderColor`) 누구와 누구 사이인지를 알아야 한다.
        시그니처를 안 건드리고 따로 뽑는다 — 좌표만 필요한 자리가 이미 있다.

        세로 변 `(X, Y)` 는 `(X-1, Y)` 와 `(X, Y)` 사이,
        가로 변 `(X, Y)` 는 `(X, Y-1)` 과 `(X, Y)` 사이다(`border_segments` 규약)."""
        w = self.gmap.width
        owner = self.gmap.owner
        vt = (np.stack([v[:, 1] * w + v[:, 0] - 1, v[:, 1] * w + v[:, 0]], axis=1)
              if len(v) else np.empty((0, 2), int))
        ht = (np.stack([(h[:, 1] - 1) * w + h[:, 0], h[:, 1] * w + h[:, 0]], axis=1)
              if len(h) else np.empty((0, 2), int))
        return vt, ht

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
