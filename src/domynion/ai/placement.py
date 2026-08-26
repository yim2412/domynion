"""자리 고르기 값 함수 — 원본 `NationStructureBehavior` 의 `*Value()` 다섯.

⚠ **이식 누락 스물넷.** §5.31 에서 보류해 뒀던 자리다. 그동안 우리는
`find_spot` 으로 **무작위 한 칸 근처의 가장 가까운 빈자리**를 썼다. 원본은 다르다:

    영토에서 25칸을 뽑고 → 종류별 값 함수로 점수를 매겨 → 가장 높은 칸에 짓는다

무작위 한 칸이면 지형·국경 거리·같은 종류와의 간격이 **아무 일도 안 한다.**
산 위 사일로도, 국경에 붙은 도시도, 항구 옆 항구도 그냥 나온다.

값은 전부 **더해서** 쌓인다(곱이 아니다). 상한이 걸린 항이 많은데, 그게
"충분히 멀면 그만"이라는 뜻이다 — 상한이 없으면 영토 구석으로만 몰린다.

| 종류 | 무엇을 본다 |
|---|---|
| 도시 | 고도 · 국경에서 멀리 · 도시끼리 · **공장과도** 벌리기 · 철도 연결성 |
| 공장 | 고도 · 국경에서 멀리 · 공장끼리 · **도시와도** 벌리기 · 철도 연결성 |
| 사일로 | 고도 · 국경에서 멀리 · 사일로끼리 벌리기 |
| 항구 | **다른 항구에서 멀리** (그것만 본다) |
| SAM | 고도 · 국경 · SAM 끼리 · **지킬 건물이 사거리 안에 몇이나 있나** |
"""

from __future__ import annotations

import random

import numpy as np

from ..core import constants as C
from ..core.gamemap import GameMap, TileRef
from ..core.nukes import NUKE_MAGNITUDES, sam_range
from ..core.rail import station_range_ok, train_gold
from ..core.units import UnitType

# 값 함수가 보는 후보 수 (`randTerritoryTileArray(..., 25)`).
# 늘리면 더 좋은 자리를 찾지만 판당 비용이 그만큼 는다.
SPAWN_TILE_SAMPLES = 25

# 철도 연결성 점수를 쓸 확률 (`shouldUseConnectivityScore`). 난이도가 높을수록
# AI 가 철도망을 의식하고 짓는다.
CONNECTIVITY_CHANCE = {"easy": 0, "medium": 60, "hard": 75, "impossible": 100}

# SAM 이 커버 중복을 따질 확률 (`useCoverageWeighting`). easy 는 아예 안 본다.
SAM_COVERAGE_CHANCE = 25

# 값 함수가 지키는 건물들 (`samLauncherValue` 의 `protectEntries`)
PROTECTED = (UnitType.CITY, UnitType.FACTORY, UnitType.MISSILE_SILO, UnitType.PORT)
# 역이 붙는 건물들 — 연결성 점수의 대상
STATIONED = (UnitType.CITY, UnitType.PORT, UnitType.FACTORY)


def manhattan(gmap: GameMap, a: TileRef, b: TileRef) -> int:
    w = gmap.width
    return abs(a % w - b % w) + abs(a // w - b // w)


def euclid_sq(gmap: GameMap, a: TileRef, b: TileRef) -> int:
    w = gmap.width
    dx, dy = a % w - b % w, a // w - b // w
    return dx * dx + dy * dy


def closest_dist(gmap: GameMap, tiles, tile: TileRef):
    """`closestTile` — 맨해튼 최단 거리. 대상이 없으면 None."""
    best = None
    for t in tiles:
        if t == tile:
            continue
        d = manhattan(gmap, t, tile)
        if best is None or d < best:
            best = d
    return best


def border_tiles(gmap: GameMap, pid: int) -> np.ndarray:
    """내 영토 중 남(또는 빈 곳)에 접한 칸.

    ⚠ numpy 로 한 번에 낸다. 파이썬 루프로 두면 값 함수를 부를 때마다 영토
    전체를 도는데, 후보가 25칸이고 나라가 수십이라 판이 통째로 느려진다."""
    owner = gmap.owner.reshape(gmap.height, gmap.width)
    mine = owner == pid
    if not mine.any():
        return np.empty(0, dtype=np.int64)
    edge = np.zeros_like(mine)
    edge[:, :-1] |= mine[:, :-1] & ~mine[:, 1:]
    edge[:, 1:] |= mine[:, 1:] & ~mine[:, :-1]
    edge[:-1, :] |= mine[:-1, :] & ~mine[1:, :]
    edge[1:, :] |= mine[1:, :] & ~mine[:-1, :]
    edge[0, :] |= mine[0, :]              # 지도 가장자리도 국경이다
    edge[-1, :] |= mine[-1, :]
    edge[:, 0] |= mine[:, 0]
    edge[:, -1] |= mine[:, -1]
    return np.flatnonzero(edge.reshape(-1))


def rail_clusters(gmap: GameMap, stations) -> dict:
    """역 타일 → 클러스터 대표. 사거리 안이면 같은 클러스터다(유니온-파인드).

    원본 `StationManager` 의 `Cluster` 를 대신한다. 연결성 점수가 **클러스터마다
    한 번만** 세는 이유는, 같은 노선에 붙은 역 열 개가 열 배로 쳐지면 이미 이어진
    자리에 계속 겹쳐 짓게 되기 때문이다 — 새 클러스터를 잇는 자리가 값져야 한다.
    """
    tiles = [s.tile for s in stations]
    parent = {t: t for t in tiles}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(tiles):
        for b in tiles[i + 1:]:
            if station_range_ok(gmap, a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
    return {t: find(t) for t in tiles}


class Placement:
    """한 나라의 자리 고르기. 값 함수가 쓰는 것들을 tick 마다 한 번만 모은다."""

    __slots__ = ("st", "pid", "rng", "difficulty", "_border", "_stations")

    def __init__(self, st, pid: int, rng: random.Random, difficulty: str):
        self.st, self.pid, self.rng, self.difficulty = st, pid, rng, difficulty
        self._border = None
        self._stations = None

    # --- 재료 -------------------------------------------------------------

    @property
    def border(self) -> np.ndarray:
        if self._border is None:
            self._border = border_tiles(self.st.gmap, self.pid)
        return self._border

    def _spacing(self):
        """`spacingConstants()` — 원자탄 바깥 반경과 그 두 배.

        ⚠ 상수를 새로 두지 않는다. 핵 반경이 지도 규모에 맞춰져 있으므로
        여기도 자동으로 따라온다(§5.10 에서 한 번 데인 자리)."""
        border = NUKE_MAGNITUDES[UnitType.ATOM_BOMB][1]
        return border, border * 2

    def _use_connectivity(self) -> bool:
        return self.rng.randrange(100) < CONNECTIVITY_CHANCE[self.difficulty]

    def reachable_stations(self):
        """`buildReachableStations` — (역 타일, 클러스터, 무게).

        무게는 **기차 벌이의 비율**이다: 동맹 1.0 > 남/팀 ~0.71 > 자기 ~0.29.
        남의 역에 닿는 것이 더 벌리므로, 이웃과 이어지는 자리가 값지다.
        ⚠ 봇과 금수 상대는 뺀다 — 기차가 안 가는 곳이라 세면 안 된다."""
        if self._stations is not None:
            return self._stations
        st = self.st
        max_gold = max(train_gold("ally", 0), 1)
        clusters = rail_clusters(st.gmap, st.rail.stations)
        out = []
        for s in st.rail.stations:
            if s.unit.utype not in STATIONED:
                continue
            owner = st.players.get(s.owner)
            if owner is None or not owner.alive:
                continue
            if s.owner == self.pid:
                rel = "self"
            else:
                if owner.is_bot:
                    continue
                if (st.diplomacy.embargoed(self.pid, s.owner)
                        or st.diplomacy.embargoed(s.owner, self.pid)):
                    continue
                rel = st.rail.relation(st.diplomacy, self.pid, s.owner)
            out.append((s.tile, clusters.get(s.tile), train_gold(rel, 0) / max_gold))
        self._stations = out
        return out

    def connectivity(self, tile: TileRef, stations) -> float:
        """`computeConnectivityScore` — 사거리 안 역들의 무게를
        **클러스터마다 최대 하나씩만** 더한다."""
        gmap = self.st.gmap
        lo = C.TRAIN_STATION_MIN_RANGE ** 2
        hi = C.TRAIN_STATION_MAX_RANGE ** 2
        per_cluster = {}
        isolated = 0.0
        for stile, cluster, weight in stations:
            d = euclid_sq(gmap, tile, stile)
            if d < lo or d > hi:
                continue
            if cluster is None:
                isolated += weight
            else:
                per_cluster[cluster] = max(per_cluster.get(cluster, 0.0), weight)
        return isolated + sum(per_cluster.values())

    # --- 값 함수 ----------------------------------------------------------

    def value_fn(self, utype: UnitType):
        if utype is UnitType.PORT:
            return self._port_value()
        if utype is UnitType.MISSILE_SILO:
            return self._silo_value()
        if utype is UnitType.SAM_LAUNCHER:
            return self._sam_value()
        if utype in (UnitType.CITY, UnitType.FACTORY):
            return self._interior_value(utype)
        return None

    def _port_value(self):
        """`portValue` — **다른 항구에서 먼 것만** 본다. 상한도 없다.

        고도도 국경도 안 본다 — 항구는 해안에만 서므로 자리가 이미 좁고,
        무역선 벌이가 거리에 붙어 있어 퍼뜨리는 것 자체가 이득이다."""
        gmap = self.st.gmap
        others = [u.tile for u in self.st.players[self.pid].units.of(UnitType.PORT)]

        def value(tile: TileRef) -> float:
            d = closest_dist(gmap, others, tile)
            return float(d) if d is not None else 0.0
        return value

    def _silo_value(self):
        """`missileSiloValue` — 고도 · 국경에서 멀리 · 사일로끼리 벌리기."""
        gmap = self.st.gmap
        border, spacing = self._spacing()
        border_ts = self.border
        others = [u.tile for u in
                  self.st.players[self.pid].units.of(UnitType.MISSILE_SILO)]

        def value(tile: TileRef) -> float:
            w = float(gmap.magnitude(tile))
            d = closest_dist(gmap, border_ts, tile)
            if d is not None:
                w += min(d, border)
            d = closest_dist(gmap, others, tile)
            if d is not None:
                w += min(d, spacing)
            return w
        return value

    def _interior_value(self, utype: UnitType):
        """`cityValue` / `factoryValue` — 둘이 거의 같다.

        다른 점은 **교차 간격의 상대**와 같은 종류 간격의 상한뿐이다:
        도시는 도시끼리 `structureSpacing`, 공장은 공장끼리 `stationRange`(110).
        공장이 더 넓게 벌어지는 이유는 역 사거리를 넘겨야 노선이 생기기 때문이다."""
        gmap = self.st.gmap
        border, spacing = self._spacing()
        border_ts = self.border
        p = self.st.players[self.pid]
        others = [u.tile for u in p.units.of(utype)]
        cross_type = UnitType.FACTORY if utype is UnitType.CITY else UnitType.CITY
        cross = [u.tile for u in p.units.of(cross_type)]
        same_cap = spacing if utype is UnitType.CITY else C.TRAIN_STATION_MAX_RANGE

        use_conn = self._use_connectivity()
        stations = self.reachable_stations() if use_conn else []

        def value(tile: TileRef) -> float:
            w = float(gmap.magnitude(tile))
            d = closest_dist(gmap, border_ts, tile)
            if d is not None:
                w += min(d, border)
            d = closest_dist(gmap, others, tile)
            if d is not None:
                w += min(d, same_cap)
            d = closest_dist(gmap, cross, tile)
            if d is not None:
                w += min(d, spacing)
            if use_conn:
                w += self.connectivity(tile, stations) * spacing
            return w
        return value

    def _sam_value(self):
        """`samLauncherValue` — 지킬 건물이 사거리 안에 **몇이나 있나**가 핵심이다.

        건물 하나당 `structureSpacing` 을 통째로 더하므로 지형·간격 항보다 훨씬
        크게 작동한다. SAM 은 자리보다 **무엇을 덮느냐**가 전부다."""
        st = self.st
        gmap = st.gmap
        border, spacing = self._spacing()
        border_ts = self.border
        p = st.players[self.pid]
        others = [u.tile for u in p.units.of(UnitType.SAM_LAUNCHER)]
        by_level = self.difficulty in ("hard", "impossible")
        protect = [(u.tile, (u.level if by_level else 1))
                   for u in p.units.units
                   if u.active and u.utype in PROTECTED]

        rng_sq = sam_range(1) ** 2
        # 이미 덮인 건물은 덜 값지게 본다(`useCoverageWeighting`)
        use_cov = (self.difficulty != "easy"
                   and self.rng.randrange(100) < SAM_COVERAGE_CHANCE)
        coverage = {}
        if use_cov:
            for tile, _ in protect:
                score = 0
                for s in p.units.of(UnitType.SAM_LAUNCHER):
                    r = sam_range(s.level)
                    if euclid_sq(gmap, tile, s.tile) <= r * r:
                        score += s.level
                coverage[tile] = score

        def value(tile: TileRef) -> float:
            w = float(gmap.magnitude(tile))
            d = closest_dist(gmap, border_ts, tile)
            if d is not None:
                w += min(d, border)
            d = closest_dist(gmap, others, tile)
            if d is not None:
                w += min(d, spacing)
            if self.difficulty != "easy":
                for ptile, weight in protect:
                    if euclid_sq(gmap, tile, ptile) > rng_sq:
                        continue
                    if use_cov:
                        w += spacing * weight / (1 + coverage.get(ptile, 0))
                    else:
                        w += spacing * weight
            return w
        return value
