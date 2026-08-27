"""철도 — 역·노선·기차. openfront `TrainStationExecution` / `RailNetwork`.

무역선이 **바다**로 골드를 벌듯 기차는 **육지**로 번다. 다른 점 셋:

1. 역은 건물(도시·항구·공장 등) 자리에 붙는다. 별도로 짓는 것이 아니다
2. 역끼리 거리가 **15~110** 이어야 이어진다(`trainStationMinRange/MaxRange`).
   너무 가까우면 골드 찍어내기가 되고, 너무 멀면 노선이 지도를 가로지른다
3. **누구의 역에 닿았는가로 벌이가 다르다** — 동맹 35,000 > 남/팀 25,000 > 자기 10,000.
   남의 역에 닿는 것이 더 벌리므로 철도가 외교를 만든다

거리 페널티는 **누적 방문 도시 수**에 붙는다(10곳까지는 없고, 그 뒤 한 곳당 5,000).
바닥은 5,000 이라 아무리 멀어도 손해는 아니다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import constants as C
from .gamemap import GameMap, TileRef
from .units import Unit, UnitType

# 역이 붙는 건물들. 원본은 `Structures` 전체가 아니라 도시·항구·공장 계통에 붙는다.
RAIL_STATION_UNITS = (UnitType.CITY, UnitType.PORT, UnitType.FACTORY)

# ⚠ **역은 공장이 있어야 생긴다**(§5.60, 이식 누락 마흔둘). 공장은 항상 역이
# 되고(`TrainStationExecution(factory, true)`), 도시·항구는 **사거리 안에 공장이
# 있을 때만** 역이 된다(`CityExecution` · `PortExecution` 의 `createStation`).
# 공장을 지으면 그 주변 도시·항구가 그때 역이 된다(`FactoryExecution`).
#
# 즉 **공장이 철도망의 전제**다 — 어디에 놓느냐가 어느 도시가 붙느냐를 정한다.
# 전에는 셋 다 무조건 역이라, 공장을 한 채도 안 지은 나라도 철도로 벌었다.
STATION_NEEDS_FACTORY = (UnitType.CITY, UnitType.PORT)


def train_spawn_rate(num_factories: int) -> int:
    """기차가 뜰 확률은 `1 / 이 값`. 공장이 많을수록 자주 뜬다(중점 10개)."""
    return (num_factories + 10) * 15


def train_gold(rel: str, cities_visited: int) -> int:
    """`trainGold` — **관계에 따라 벌이가 다르다.**

    동맹 35,000 · 남/팀 25,000 · 자기 10,000. 남의 역에 닿는 것이 자기 역보다
    2.5배 벌리므로, 철도를 깔면 이웃과 사이가 좋을 이유가 생긴다.

    ⚠ `cities_visited` 는 **그 여정에서 들른 도시/항구 수**다(원본
    `_tradeStopsVisited`). 다음 기차는 0부터 다시 센다. 전에는 여기에 **판 전체
    누적 발차 수**를 넣고 있어서, 철도를 깐 나라는 **기차 열다섯 대째부터 영원히
    최저 수입(5,000)** 을 받았다(§5.60, 이식 누락 마흔하나)."""
    visited = max(0, cities_visited - 9)     # 처음 10곳은 페널티가 없다
    base = {"ally": 35_000, "team": 25_000, "other": 25_000, "self": 10_000}[rel]
    return max(5_000, base - visited * 5_000)


def near_station(gmap: GameMap, a: TileRef, b: TileRef) -> bool:
    """`trainStationMaxRange` 안인가 — 공장이 도시·항구를 역으로 만들 때 쓴다."""
    w = gmap.width
    d = math.hypot(a % w - b % w, a // w - b // w)
    return 0 < d <= C.TRAIN_STATION_MAX_RANGE


def station_range_ok(gmap: GameMap, a: TileRef, b: TileRef) -> bool:
    """역 사이 거리가 15~110 이어야 이어진다."""
    w = gmap.width
    d = math.hypot(a % w - b % w, a // w - b // w)
    return C.TRAIN_STATION_MIN_RANGE <= d <= C.TRAIN_STATION_MAX_RANGE


@dataclass
class Station:
    """건물에 붙은 역. 건물이 사라지면 역도 사라진다."""
    tile: TileRef
    owner: int
    unit: Unit


@dataclass
class Train:
    """역에서 역으로 달린다. 도착하면 양쪽이 아니라 **출발한 쪽만** 번다."""
    owner: int
    src: TileRef
    dst: TileRef
    dst_owner: int
    rel: str
    cities_visited: int
    progress: float = 0.0
    # 지나온 역들(마지막이 목적지). 원본은 선로를 따라가지만 우리는 직선이라
    # **어디를 들렀는지**만 기록한다 — 수입 계산에 그 수가 들어간다.
    path: list = field(default_factory=list)

    def advance(self) -> None:
        self.progress += C.TRAIN_SPEED

    def arrived(self, gmap: GameMap) -> bool:
        w = gmap.width
        return self.progress >= math.hypot(self.src % w - self.dst % w,
                                           self.src // w - self.dst // w)


@dataclass
class RailNetwork:
    """역들과 그 사이 노선. 노선은 역 쌍이 사거리 안이면 자동으로 생긴다."""

    stations: list[Station] = field(default_factory=list)

    def rebuild(self, gmap: GameMap, players) -> None:
        """건물 목록에서 역을 다시 만든다. 건물이 핵에 날아가면 역도 같이 사라진다.

        ⚠ **공장이 있어야 역이 된다**(§5.60). 공장은 항상 역이고, 도시·항구는
        사거리 안에 **자기 공장**이 있을 때만 역이다. 전에는 셋 다 무조건
        역이라 공장을 한 채도 안 지은 나라도 철도로 벌었다."""
        self.stations = []
        for p in players:
            live = [u for u in p.units.units
                    if u.active and not u.under_construction]
            factories = [u for u in live if u.utype is UnitType.FACTORY]
            for u in live:
                if u.utype is UnitType.FACTORY:
                    self.stations.append(Station(tile=u.tile, owner=p.pid, unit=u))
                elif u.utype in STATION_NEEDS_FACTORY:
                    if any(near_station(gmap, u.tile, f.tile) for f in factories):
                        self.stations.append(
                            Station(tile=u.tile, owner=p.pid, unit=u))

    def links(self, gmap: GameMap, pid: int) -> list[Station]:
        """내 역에서 이어지는 상대 역들."""
        mine = [s for s in self.stations if s.owner == pid]
        out: list[Station] = []
        for s in self.stations:
            if any(station_range_ok(gmap, m.tile, s.tile) and m.tile != s.tile
                   for m in mine):
                out.append(s)
        return out

    def relation(self, diplomacy, pid: int, other: int) -> str:
        if pid == other:
            return "self"
        if diplomacy.allied(pid, other):
            return "ally"
        if diplomacy.same_team(pid, other):
            return "team"
        return "other"

    def route(self, gmap: GameMap, src: Station, rng: random.Random,
              hops: int) -> list[Station]:
        """`findStationsPath` 의 자리 — **역에서 역으로 이어 걷는다.**

        원본은 역 그래프를 A* 로 푼다. 우리는 선로를 안 깔고 직선으로 나므로
        (§5.60 의 범위 결정) **닿는 역 중에서 무작위로 이어 간다.** 중요한 것은
        경로의 모양이 아니라 **여러 역을 거친다**는 사실이다 — 여정마다 방문 수가
        쌓여야 수입 페널티가 뜻을 갖는다."""
        path: list[Station] = []
        here = src
        seen = {src.tile}
        for _ in range(hops):
            nxt = [s for s in self.stations
                   if s.tile not in seen and near_station(gmap, here.tile, s.tile)]
            if not nxt:
                break
            here = rng.choice(nxt)
            seen.add(here.tile)
            path.append(here)
        return path

    def dispatch(self, gmap: GameMap, diplomacy, pid: int,
                 rng: random.Random, hops: int = C.TRAIN_MAX_HOPS,
                 src: Station | None = None) -> Train | None:
        """기차 한 대를 낸다. 이어진 역이 없으면 None.

        ⚠ `src` 를 주면 **그 역이 낸다**(원본은 역마다 따로 굴린다). 안 주면
        내 역 중 하나를 고른다 — 옛 호출부 호환."""
        mine = [s for s in self.stations if s.owner == pid]
        if not mine:
            return None
        start = src if src is not None else rng.choice(mine)
        path = self.route(gmap, start, rng, hops)
        if not path:
            return None
        dst = path[-1]
        # ⚠ **이 여정에서 들른 도시/항구 수**다(원본 `_tradeStopsVisited`).
        # 공장 역은 안 센다. 다음 기차는 0부터 다시 센다.
        visited = sum(1 for s in path
                      if s.unit.utype in (UnitType.CITY, UnitType.PORT))
        return Train(owner=pid, src=start.tile, dst=dst.tile, dst_owner=dst.owner,
                     rel=self.relation(diplomacy, pid, dst.owner),
                     cities_visited=visited, path=[s.tile for s in path])
