"""지도 — 평탄 numpy 배열. 타일 객체는 없다.

**타일 하나가 파이썬 객체이면 안 된다.** 원본 지도는 육지가 3.7만~13만 칸이고
(축소판 map16x 기준), 그만큼의 dataclass 를 만들면 생성 시간도 메모리도 감당이 안 된다.
지형과 소유자를 각각 배열 하나로 두고, 타일은 **정수 인덱스**로 가리킨다.

    t = y * width + x

이건 원본의 `TileRef` 와 같은 표현이다. 좌표 튜플로 바꾸지 말 것 — 힙에 수만 개가
들어가는데 튜플이면 그만큼 객체가 생긴다.

지도는 생성하지 않고 **OpenFront 의 원본 파일을 그대로 읽는다.** 실제 지형이라
밸런스를 원본과 같은 조건에서 잴 수 있고, 노이즈 생성기·임계값 튜닝이 통째로 사라진다.
포맷과 라이선스는 `resources/maps/ATTRIBUTION.md`.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

from . import constants as C
from .constants import Terrain

TileRef = int

_RESOURCES = Path(__file__).resolve().parents[3] / "resources" / "maps"


# 해상도. 원본이 같은 지도를 세 크기로 굽는다 — 이름이 곧 파일명이다.
#
# **크기 선택은 밸런스에 직접 영향을 준다.** 원본 공식이 전체 크기(육지 65만~234만)
# 기준이라, 작은 지도에서는 상수항이 지배하고 핵 반경의 비중이 커진다(계획서 4.5절).
SIZES = ("map16x", "map4x", "map")
# 기본은 **`map`(원본 크기)** 이다. `map4x` 에서는 **규칙이 제대로 안 돈다** —
# §5.47 실측: 같은 seed 로 핵이 `map4x` 0발 대 `map` 10발이고, 생존도 18 대 39명이다.
#
# ⚠ 이유는 **핵 반경이 원본 절대값**이라서다. 수폭의 반경 검사 상자가 한 변
# 201칸인데 `map4x` 는 생존 24명일 때 1인당 영토가 한 변 81칸이라 **들어갈 수가
# 없다.** 그러면 AI 가 수폭을 고른 뒤 아무 데도 못 쏘고(원본도 원자탄으로
# 되돌아가지 않는다) 핵이 통째로 멈춘다. §4.5 가 "작은 지도에 그대로 넣으면
# 다른 게임이 된다"고 적어 둔 것의 세 번째 얼굴이다.
#
# 성능은 §5.45(A* · 금수 O(N²)) 이후 다시 쟀다. 나라 72 + 봇 400 기준:
#   map4x 21.6ms/tick · map 73.6ms/tick — **10Hz 예산(100ms) 안에 들어간다.**
# 초반(472명)이 최악이므로 후반은 더 여유롭다.
DEFAULT_SIZE = "map"


def available_maps(root: Path | None = None) -> list[str]:
    base = root or _RESOURCES
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "map16x.bin").is_file())


def available_sizes(name: str, root: Path | None = None) -> list[str]:
    base = (root or _RESOURCES) / name
    return [s for s in SIZES if (base / f"{s}.bin").is_file()]


def _load_nations(base: Path, w: int, h: int) -> list[tuple[str, "TileRef"]]:
    """manifest 의 나라 좌표를 이 해상도의 TileRef 로 옮긴다.

    저장된 좌표는 **원본(가장 큰) 해상도 기준**이라 축소본에서는 나눠야 한다.
    `nation_coord_space` 가 없으면(옛 manifest) 나라 없이 간다 — 여기서 죽으면
    지도 하나 때문에 판 전체가 안 열린다."""
    meta = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    space = meta.get("nation_coord_space")
    if not space or not meta.get("nations"):
        return []
    sw, sh = space
    out: list[tuple[str, TileRef]] = []
    for n in meta["nations"]:
        cx, cy = n["coordinates"]
        x = min(w - 1, max(0, int(cx * w / sw)))
        y = min(h - 1, max(0, int(cy * h / sh)))
        out.append((n.get("name", "?"), y * w + x))
    return out


class GameMap:
    """지형은 읽기 전용, 소유자만 바뀐다.

    `terrain` 은 원본 바이트를 **그대로** 들고 있고, 지형 종류는 필요할 때 뽑는다.
    미리 변환해 두지 않는 이유: 해안선·대양 비트가 나중에(보트·항구) 필요하고,
    원본 바이트를 남겨 둬야 우리가 잘못 변환했는지 대조할 수 있기 때문이다.
    """

    __slots__ = ("width", "height", "size", "raw", "owner",
                 "terrain", "land_count", "name", "_ocean_cc", "nations",
                 "_touch_cc", "_passable", "terrain_epoch")

    def __init__(self, width: int, height: int, raw: np.ndarray, name: str = ""):
        if raw.size != width * height:
            raise ValueError(f"{width}x{height} 인데 바이트가 {raw.size}개다")
        self.width = width
        self.height = height
        self.size = width * height
        self.nations: list[tuple[str, TileRef]] = []   # manifest 의 실제 국가들
        self.name = name
        self.raw = raw
        self.terrain = _terrain_from_raw(raw)
        self.owner = np.full(self.size, -1, dtype=np.int16)   # -1 = 중립
        self._passable: np.ndarray | None = None
        self.land_count = int(self.passable_mask().sum())
        self._ocean_cc: np.ndarray | None = None
        # 칸이 접한 바다 연결성분. 지형이 안 바뀌므로 한 번 재면 끝이다.
        # ⚠ 핵이 육지를 바다로 만들면 여기도 비워야 한다(`_path_cache` 와 함께).
        self._touch_cc: dict[int, frozenset[int]] = {}
        # 지형이 바뀔 때마다 오른다. `GameMap` **밖**에 사는 캐시(철도의 선로
        # 캐시)가 이 값을 들고 있다가 달라지면 스스로 버린다 — 무효화 목록에
        # 남의 모듈을 끌어들이지 않으려는 것이다.
        self.terrain_epoch = 0

    # --- 적재 -------------------------------------------------------------

    @classmethod
    def load(cls, name: str = "world", root: Path | None = None,
             size: str = DEFAULT_SIZE) -> "GameMap":
        """`size` 는 `map16x`(1/16) · `map4x`(1/4) · `map`(원본 크기).

        기본이 `map4x` 인 이유: 원본 공식이 전체 크기 기준이라 `map16x` 에서는
        병력 상한의 상수항이 지배하고 핵 반경의 비중이 16배가 된다."""
        base = (root or _RESOURCES) / name
        if not (base / f"{size}.bin").is_file():
            have = available_sizes(name, root)
            raise FileNotFoundError(f"{name}/{size}.bin 이 없다. 있는 것: {have}")
        meta = json.loads((base / "manifest.json").read_text(encoding="utf-8"))[size]
        # ⚠ `.copy()` 가 필수다. `frombuffer` 는 **읽기 전용** 배열을 준다.
        # 핵이 육지를 바다로 바꿀 때(`P5`) 여기에 쓰기 때문에, 빼면 실전에서만
        # `ValueError: assignment destination is read-only` 로 죽는다.
        # 테스트가 못 잡았던 이유: `from_rows` 는 쓰기 가능한 배열을 만든다.
        raw = np.frombuffer((base / f"{size}.bin").read_bytes(), dtype=np.uint8).copy()
        gm = cls(meta["width"], meta["height"], raw, name=f"{name}/{size}")
        # 나라 좌표는 **원본 해상도 기준**으로 저장돼 있다(`nation_coord_space`).
        # 여기서 이 해상도로 옮긴다 — 저장할 때 미리 나누면 어느 기준이었는지 잊는다.
        gm.nations = _load_nations(base, gm.width, gm.height)
        declared = meta.get("num_land_tiles")
        if declared is not None and int((gm.raw & C.LAND_BIT).astype(bool).sum()) != declared:
            # 조용히 어긋나면 이후 모든 측정이 무의미해진다. 여기서 죽는 편이 낫다.
            raise ValueError(f"{name}: 육지 수가 manifest 와 다르다")
        return gm

    @classmethod
    def from_rows(cls, rows: list[str], name: str = "test") -> "GameMap":
        """테스트용. `~`바다 `.`평야 `n`구릉 `A`산악 `#`통행불가."""
        mag = {"~": 0, ".": 0, "n": 12, "A": 22, "#": C.IMPASSABLE_MAGNITUDE}
        w, h = len(rows[0]), len(rows)
        raw = np.zeros(w * h, dtype=np.uint8)
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                b = mag[ch]
                if ch != "~":
                    b |= C.LAND_BIT
                else:
                    b |= C.OCEAN_BIT
                raw[y * w + x] = b
        # ⚠ 해안선 비트를 안 세우면 **테스트 지도에만 해안선이 없다.** 실제
        # 지도는 바이너리에 이미 들어 있어서, 이걸 빼먹으면 `is_shoreline` 에
        # 걸린 규칙(무역선 나포 보호 등)이 테스트에서 영원히 발동하지 않는다.
        # 원본 `WaterManager` — **통행불가 이웃은 해안선을 만들지 않는다**(빈
        # 공간이지 해안이 아니다).
        #
        # 파이썬 루프로 두면 안 된다. 테스트가 지도를 수백 번 만드는데 그때마다
        # 전 칸을 도는 순수 루프면 스위트가 2.4초에서 8초로 뛴다(실측).
        grid = raw.reshape(h, w)
        land = (grid & C.LAND_BIT) != 0
        ok = (grid & C.MAGNITUDE_MASK) < C.IMPASSABLE_MAGNITUDE
        opp = np.zeros((h, w), dtype=bool)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nl = np.roll(land, (dy, dx), axis=(0, 1))
            no = np.roll(ok, (dy, dx), axis=(0, 1))
            edge = np.ones((h, w), dtype=bool)      # 가장자리는 이웃이 없다
            if dy == -1: edge[-1, :] = False
            elif dy == 1: edge[0, :] = False
            elif dx == -1: edge[:, -1] = False
            else: edge[:, 0] = False
            opp |= (nl != land) & no & edge
        grid[opp & ok] |= C.SHORELINE_BIT
        return cls(w, h, raw, name=name)

    # --- 지형 -------------------------------------------------------------

    def magnitude(self, t: TileRef) -> int:
        return int(self.raw[t] & C.MAGNITUDE_MASK)

    def is_impassable(self, t: TileRef) -> bool:
        return (bool(self.raw[t] & C.LAND_BIT)
                and (self.raw[t] & C.MAGNITUDE_MASK) >= C.IMPASSABLE_MAGNITUDE)

    def is_shoreline(self, t: TileRef) -> bool:
        return bool(self.raw[t] & C.SHORELINE_BIT)

    def is_ocean(self, t: TileRef) -> bool:
        return bool(self.raw[t] & C.OCEAN_BIT)

    def terrain_at(self, t: TileRef) -> Terrain:
        return Terrain(int(self.terrain[t]))

    def passable(self, t: TileRef) -> bool:
        """공격이 지나갈 수 있는가. 바다도 통행불가 육지도 아니어야 한다."""
        return C.Terrain.PLAINS <= self.terrain[t] <= C.Terrain.MOUNTAIN

    def passable_mask(self) -> np.ndarray:
        """통행 가능한 칸의 불린 배열. **한 번 재고 캐시한다.**

        ⚠ 실측(§5.50): 원본 크기 판에서 이 함수가 **전체 시간의 28%** 였다.
        1,200 tick 에 11,138번 불리는데 매번 200만 칸 배열을 새로 만들고 있었다.
        지형은 `WATER_NUKES` 로 육지가 바다가 될 때만 바뀌고, 그 자리에는 이미
        `_ocean_cc` 와 경로 캐시를 버리는 코드가 있다 — 거기에 이 캐시를 얹었다.

        돌려주는 배열을 **쓰는 쪽이 고치면 안 된다.** 지금은 아무도 안 고친다
        (`border_targets` 는 reshape 해서 읽기만 한다)."""
        if self._passable is None:
            self._passable = ((self.terrain >= Terrain.PLAINS)
                              & (self.terrain <= Terrain.MOUNTAIN))
        return self._passable

    def invalidate_terrain_caches(self) -> None:
        """지형이 바뀌었을 때 버려야 하는 것 전부. **한 곳에 모아 둔다** —
        새 캐시를 늘릴 때 무효화를 빠뜨리는 것이 이 자리의 유일한 위험이다.

        ⚠ `GameMap` 밖에 사는 캐시는 여기서 못 지운다(철도의 선로 캐시가 그렇다).
        그쪽은 `terrain_epoch` 를 보고 스스로 버린다."""
        self._ocean_cc = None
        self._passable = None
        self._touch_cc.clear()
        self.terrain_epoch += 1

    def ocean_components(self) -> np.ndarray:
        """바다 연결성분 라벨(육지는 -1). 처음 부를 때 한 번 계산해 둔다."""
        if self._ocean_cc is None:
            self._ocean_cc = _flood_ocean_components(self)
        return self._ocean_cc

    def is_shore(self, t: TileRef) -> bool:
        """육지이면서 바다에 접한 칸. 항구는 여기에만 지을 수 있다."""
        return self.passable(t) and any(
            self.terrain[n] == Terrain.OCEAN for n in self.neighbors(t))

    # --- 이웃 -------------------------------------------------------------

    def neighbors(self, t: TileRef) -> tuple[TileRef, ...]:
        """4방향. 지도 가장자리에서 반대편으로 새지 않게 x 를 확인한다.

        리스트가 아니라 튜플을 돌려주는 이유는 하나다 — 이 함수가 확장 루프의
        가장 안쪽에서 수만 번 불린다."""
        x = t % self.width
        out = []
        if x > 0:
            out.append(t - 1)
        if x < self.width - 1:
            out.append(t + 1)
        if t >= self.width:
            out.append(t - self.width)
        if t + self.width < self.size:
            out.append(t + self.width)
        return tuple(out)

    def xy(self, t: TileRef) -> tuple[int, int]:
        return t % self.width, t // self.width

    def ref(self, x: int, y: int) -> TileRef:
        return y * self.width + x

    # --- 소유 -------------------------------------------------------------

    def tile_counts(self, num_players: int) -> np.ndarray:
        """전수 순회. **런타임에는 쓰지 않는다** — 엔진이 증분으로 센다.
        테스트가 증분 값과 대조할 때만 쓴다."""
        owned = self.owner[self.owner >= 0]
        return np.bincount(owned, minlength=num_players)

    def owned_refs(self, pid: int) -> np.ndarray:
        return np.flatnonzero(self.owner == pid)

    # --- 시작 위치 --------------------------------------------------------

    def place_starts(self, count: int, rng: random.Random,
                     attempts: int = 400) -> list[TileRef]:
        """서로 멀리 떨어진 육지 칸을 고른다.

        원본의 스폰 규칙(`SpawnExecution`)은 P6 에서 옮긴다. 지금은 측정을 돌릴 수
        있을 만큼만 — **가장 큰 대륙 안에서** 최대한 떨어뜨린다. 한 명이 섬에서
        시작하면 그 판은 시작과 동시에 끝난 것이나 같다."""
        land = np.flatnonzero(self.passable_mask())
        if len(land) < count:
            raise ValueError("육지가 인원보다 적다")
        picks: list[TileRef] = []
        for _ in range(count):
            best, best_d = None, -1.0
            for _ in range(attempts):
                cand = int(rng.choice(land))
                if not picks:
                    best = cand
                    break
                cx, cy = self.xy(cand)
                d = min((cx - self.xy(p)[0]) ** 2 + (cy - self.xy(p)[1]) ** 2
                        for p in picks)
                if d > best_d:
                    best, best_d = cand, d
            picks.append(int(best))
        return picks


def _flood_ocean_components(gm: "GameMap") -> np.ndarray:
    """바다를 연결성분으로 라벨링한다. 육지는 -1.

    **경로 탐색의 조기 기각용이다.** 이게 없으면 닿을 수 없는 목적지에 대해 BFS 가
    바다 전체를 훑는다 — 판당 실행 시간이 15초에서 91초가 됐던 원인이 그것이다."""
    from collections import deque

    lab = np.full(gm.size, -1, dtype=np.int32)
    ocean = gm.terrain == Terrain.OCEAN
    nxt = 0
    for start in np.flatnonzero(ocean).tolist():
        if lab[start] >= 0:
            continue
        q = deque([start])
        lab[start] = nxt
        while q:
            cur = q.popleft()
            for n in gm.neighbors(cur):
                if ocean[n] and lab[n] < 0:
                    lab[n] = nxt
                    q.append(n)
        nxt += 1
    return lab


def _terrain_from_raw(raw: np.ndarray) -> np.ndarray:
    """원본 바이트 → 지형 코드. GameMap.ts :: terrainType() 과 같은 순서다."""
    mag = raw & C.MAGNITUDE_MASK
    land = (raw & C.LAND_BIT) != 0
    out = np.full(raw.size, Terrain.OCEAN, dtype=np.uint8)
    out[land] = Terrain.PLAINS
    out[land & (mag >= C.HIGHLAND_MAGNITUDE)] = Terrain.HIGHLAND
    out[land & (mag >= C.MOUNTAIN_MAGNITUDE)] = Terrain.MOUNTAIN
    out[land & (mag >= C.IMPASSABLE_MAGNITUDE)] = Terrain.IMPASSABLE
    return out
