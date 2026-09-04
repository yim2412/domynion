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

from ..core import constants as C
from ..core.constants import Terrain
from ..core.gamemap import GameMap
from . import palette as P

# 이름이 걸쳐도 되는 "얕은 바다"의 문턱. 원본 `magnitude(tile) < 10`.
NAME_SHALLOW_MAGNITUDE = 10
# 경계상자의 짧은 변이 이 값을 넘을 때마다 격자를 성기게 뽑는다 — 원본의 사다리
# 그대로다(<25 → 1, <50 → 2, <100 → 4, <250 → 8, <500 → 16, 그 위 32).
NAME_SCALE_STEPS: tuple[tuple[int, int], ...] = (
    (25, 1), (50, 2), (100, 4), (250, 8), (500, 16),
)
NAME_SCALE_MAX = 32


def _name_scale(span: int) -> int:
    for limit, scale in NAME_SCALE_STEPS:
        if span < limit:
            return scale
    return NAME_SCALE_MAX


# 이만큼도 안 되는 영토에는 이름을 안 쓴다 — 글자가 영토보다 커진다.
NAME_MIN_TILES = 30


def _bounding_boxes(owner: np.ndarray, w: int, h: int
                    ) -> tuple[np.ndarray, np.ndarray]:
    """나라별 `(x0, x1, y0, y1)` 과 칸 수를 **한 번에** 낸다.

    ⚠ **예전에는 나라마다 `np.nonzero(owner == pid)` 를 돌렸다.** 원본 해상도
    (2000x1000) · 나라 400명에서 **1,150ms** 다 — 1초에 한 번 도는 자리라
    화면이 그만큼 멈춘다. 문서에는 *"12~14ms 실측"* 이라고 적혀 있었는데
    그 측정은 나라가 몇 명일 때 것이었다(§5.97).

    `np.minimum.at` 로 한 번에 접으면 **29ms** 다(40배). 값은 나라 400명에서
    옛 방식과 전부 일치하는 것을 확인했다."""
    idx = np.flatnonzero(owner >= 0)
    if len(idx) == 0:
        return np.empty((0, 4), np.int32), np.zeros(0, np.int32)
    pid = owner[idx]
    n = int(pid.max()) + 1
    xs = (idx % w).astype(np.int32)
    ys = (idx // w).astype(np.int32)
    box = np.empty((n, 4), np.int32)
    box[:, 0] = w
    box[:, 1] = -1
    box[:, 2] = h
    box[:, 3] = -1
    np.minimum.at(box[:, 0], pid, xs)
    np.maximum.at(box[:, 1], pid, xs)
    np.minimum.at(box[:, 2], pid, ys)
    np.maximum.at(box[:, 3], pid, ys)
    return box, np.bincount(pid, minlength=n)


def _largest_rectangle(grid: np.ndarray) -> tuple[int, int, int, int]:
    """참으로 채워진 가장 큰 축정렬 사각형 `(x, y, 폭, 높이)`.

    행마다 히스토그램을 세우고 스택으로 최대 넓이를 찾는다 — 원본
    `findLargestInscribedRectangle` + `largestRectangleInHistogram` 그대로다."""
    rows, cols = grid.shape
    heights = [0] * cols
    best = (0, 0, 0, 0)
    best_area = 0
    for r in range(rows):
        row = grid[r]
        for c in range(cols):
            heights[c] = heights[c] + 1 if row[c] else 0
        stack: list[int] = []
        for i in range(cols + 1):
            cur = 0 if i == cols else heights[i]
            while stack and cur < heights[stack[-1]]:
                hgt = heights[stack.pop()]
                left = stack[-1] + 1 if stack else 0
                wid = i - left
                if hgt * wid > best_area:
                    best_area = hgt * wid
                    best = (left, r - hgt + 1, wid, hgt)
            stack.append(i)
    return best


_TERRAIN_LUT = np.array(
    [P.TERRAIN_COLORS[Terrain(i)] for i in range(len(Terrain))], dtype=np.float32)


def _owner_rgba_lut(n: int, kinds: dict[int, str] | None) -> np.ndarray:
    """`owner + 1` 로 조회하는 표. 0번(중립)은 **완전 투명**이다.

    ⚠ **pid 마다 한 칸씩 만든다**(§5.95). 예전에는 색 수만큼만 만들고 조회하는
    쪽에서 `pid % 색수` 로 감쌌는데, 그러면 종류별로 통을 나눌 수가 없다 —
    감싸기는 `player_color` 안으로 들어갔다."""
    lut = np.zeros((n + 1, 4), dtype=np.uint8)
    alpha = int(round(P.OWNER_BLEND * 255))
    for pid in range(n):
        r, g, b = P.player_color(pid, (kinds or {}).get(pid, "nation"))
        lut[pid + 1] = (r, g, b, alpha)
    return lut


class FrameBuilder:
    """지형 바닥(RGB, 고정)과 소유자 층(RGBA, 매 프레임)을 따로 낸다."""

    __slots__ = ("gmap", "_base", "_land", "_overlay", "_lod",
                 "_kinds", "_owner_lut", "_name_bg")

    def __init__(self, gmap: GameMap, seed: int = 0,
                 kinds: dict[int, str] | None = None):
        self.gmap = gmap
        # pid -> "nation"/"bot"/"human". 판 내내 안 바뀌므로 표를 한 번만 만든다.
        # ⚠ **생성자로만 받는다.** `set_kinds` 를 따로 뒀다가 지웠다(2026-09-04) —
        # 주석이 *"판이 시작될 때 넣는다"* 고 적어 뒀는데 정작 그 자리는
        # `MapWidget` 이 **생성자로** 넘기고 있어 호출부가 0 이었다.
        self._kinds = kinds
        self._owner_lut = _owner_rgba_lut(0, kinds)
        self._name_bg: np.ndarray | None = None
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
        self._name_bg = None          # 지형이 바뀌면 같이 버린다
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
        # ⚠ 예전에는 `len(PLAYER_COLORS) < 64` 일 때만 감쌌다. **조건이 거꾸로였다** —
        # 색을 64개 넘게 늘리면 감싸기가 꺼지고, pid 가 표를 넘는 순간 IndexError 다.
        # 이제 표가 pid 를 전부 덮으므로 감쌀 일 자체가 없다(§5.95).
        self._ensure_lut(int(owner.max()) if owner.size else -1)
        self._overlay = np.ascontiguousarray(self._owner_lut[idx])
        return self._overlay

    def _ensure_lut(self, max_pid: int) -> None:
        """표가 `max_pid` 를 덮게 키운다. 나라가 늘 수는 있어도 줄지는 않는다."""
        if max_pid + 2 > len(self._owner_lut):
            self._owner_lut = _owner_rgba_lut(max_pid + 1, self._kinds)

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

    def label_anchors(self, players, fallout: np.ndarray | None = None
                      ) -> list[tuple[int, float, float, float, float]]:
        """`(pid, 중심x, 중심y, 자리폭, 자리높이)` — 원본 `NameBoxCalculator`.

        ⚠ **이식 누락 백둘**(§5.97). 우리는 이름을 영토의 **무게중심**에 놓고
        크기를 경계상자 폭에서 뽑았다. 무게중심은 **영토 밖에 떨어질 수 있다** —
        초승달 모양이거나 해협 양쪽에 걸친 나라는 이름이 바다나 남의 땅 위에
        뜬다. 원본은 그래서 **가장 큰 내접 사각형**을 찾아 거기에 놓는다.

        ⚠ **해안과 얕은 바다도 자리로 친다**(`isShore || (isOcean && magnitude
        < 10) || 내 땅 || 낙진`). 해안선을 낀 나라의 이름이 물 쪽으로 조금
        걸치는 것을 허용해야 사각형이 쓸 만한 크기로 나온다. 이걸 빼면 길쭉한
        해안 나라의 이름이 한 줄짜리 사각형에 갇혀 못 읽게 작아진다."""
        h, w = self.gmap.height, self.gmap.width
        owner = self.gmap.owner.reshape(h, w)
        bg = self._name_background()
        boxes, counts = _bounding_boxes(self.gmap.owner, w, h)
        out = []
        for p in players:
            if p.pid >= len(counts) or counts[p.pid] < NAME_MIN_TILES:
                continue
            x0, x1, y0, y1 = (int(v) for v in boxes[p.pid])
            scale = _name_scale(min(x1 - x0, y1 - y0))
            sx0, sy0 = x0 // scale, y0 // scale
            sx1, sy1 = x1 // scale, y1 // scale
            gx = np.clip(np.arange(sx0, sx1 + 1) * scale, 0, w - 1)
            gy = np.clip(np.arange(sy0, sy1 + 1) * scale, 0, h - 1)
            grid = bg[np.ix_(gy, gx)] | (owner[np.ix_(gy, gx)] == p.pid)
            if fallout is not None:
                grid |= fallout.reshape(h, w)[np.ix_(gy, gx)]
            rx, ry, rw, rh = _largest_rectangle(grid)
            if rw == 0 or rh == 0:
                continue
            # ⚠ **원본과 한 곳이 다르다.** 원본은 격자 좌표에 `scale` 을 곱한 뒤
            # `boundingBox.min` 을 그대로 더한다 — 격자 원점은 `min // scale *
            # scale` 이라 최대 `scale - 1` 만큼 어긋난다. 이름을 영토 **안**에
            # 놓는 것이 이 계산의 전부라, 그 어긋남을 물려받지 않는다.
            cx = (rx + rw / 2) * scale + sx0 * scale
            cy = (ry + rh / 2) * scale + sy0 * scale
            out.append((p.pid, float(cx), float(cy),
                        float(rw * scale), float(rh * scale)))
        return out

    def _name_background(self) -> np.ndarray:
        """이름이 걸쳐도 되는 바탕 — 해안 + 얕은 바다. **지형이므로 한 번만 잰다.**"""
        if self._name_bg is None:
            raw = self.gmap.raw.reshape(self.gmap.height, self.gmap.width)
            shore = (raw & C.SHORELINE_BIT) != 0
            shallow = ((raw & C.OCEAN_BIT) != 0) & ((raw & C.MAGNITUDE_MASK)
                                                    < NAME_SHALLOW_MAGNITUDE)
            self._name_bg = shore | shallow
        return self._name_bg


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
