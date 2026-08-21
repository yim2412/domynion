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
DEFAULT_SIZE = "map4x"


def available_maps(root: Path | None = None) -> list[str]:
    base = root or _RESOURCES
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "map16x.bin").is_file())


def available_sizes(name: str, root: Path | None = None) -> list[str]:
    base = (root or _RESOURCES) / name
    return [s for s in SIZES if (base / f"{s}.bin").is_file()]


class GameMap:
    """지형은 읽기 전용, 소유자만 바뀐다.

    `terrain` 은 원본 바이트를 **그대로** 들고 있고, 지형 종류는 필요할 때 뽑는다.
    미리 변환해 두지 않는 이유: 해안선·대양 비트가 나중에(보트·항구) 필요하고,
    원본 바이트를 남겨 둬야 우리가 잘못 변환했는지 대조할 수 있기 때문이다.
    """

    __slots__ = ("width", "height", "size", "raw", "owner",
                 "terrain", "land_count", "name", "_ocean_cc")

    def __init__(self, width: int, height: int, raw: np.ndarray, name: str = ""):
        if raw.size != width * height:
            raise ValueError(f"{width}x{height} 인데 바이트가 {raw.size}개다")
        self.width = width
        self.height = height
        self.size = width * height
        self.name = name
        self.raw = raw
        self.terrain = _terrain_from_raw(raw)
        self.owner = np.full(self.size, -1, dtype=np.int16)   # -1 = 중립
        self.land_count = int(self.passable_mask().sum())
        self._ocean_cc: np.ndarray | None = None

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
        return cls(w, h, raw, name=name)

    # --- 지형 -------------------------------------------------------------

    def is_land(self, t: TileRef) -> bool:
        return bool(self.raw[t] & C.LAND_BIT)

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
        return (self.terrain >= Terrain.PLAINS) & (self.terrain <= Terrain.MOUNTAIN)

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
