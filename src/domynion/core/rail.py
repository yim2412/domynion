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


def train_spawn_rate(num_factories: int) -> int:
    """기차가 뜰 확률은 `1 / 이 값`. 공장이 많을수록 자주 뜬다(중점 10개)."""
    return (num_factories + 10) * 15


def train_gold(rel: str, cities_visited: int) -> int:
    """`trainGold` — **관계에 따라 벌이가 다르다.**

    동맹 35,000 · 남/팀 25,000 · 자기 10,000. 남의 역에 닿는 것이 자기 역보다
    2.5배 벌리므로, 철도를 깔면 이웃과 사이가 좋을 이유가 생긴다."""
    visited = max(0, cities_visited - 9)     # 처음 10곳은 페널티가 없다
    base = {"ally": 35_000, "team": 25_000, "other": 25_000, "self": 10_000}[rel]
    return max(5_000, base - visited * 5_000)


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
    _visited: dict[int, int] = field(default_factory=dict)   # pid -> 누적 방문 수

    def rebuild(self, players) -> None:
        """건물 목록에서 역을 다시 만든다. 건물이 핵에 날아가면 역도 같이 사라진다."""
        self.stations = [
            Station(tile=u.tile, owner=p.pid, unit=u)
            for p in players
            for u in p.units.units
            if u.active and not u.under_construction
            and u.utype in RAIL_STATION_UNITS
        ]

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

    def dispatch(self, gmap: GameMap, diplomacy, pid: int,
                 rng: random.Random) -> Train | None:
        """기차 한 대를 낸다. 이어진 역이 없으면 None."""
        mine = [s for s in self.stations if s.owner == pid]
        if not mine:
            return None
        src = rng.choice(mine)
        targets = [s for s in self.stations
                   if s.tile != src.tile and station_range_ok(gmap, src.tile, s.tile)]
        if not targets:
            return None
        dst = rng.choice(targets)
        self._visited[pid] = self._visited.get(pid, 0) + 1
        return Train(owner=pid, src=src.tile, dst=dst.tile, dst_owner=dst.owner,
                     rel=self.relation(diplomacy, pid, dst.owner),
                     cities_visited=self._visited[pid])
