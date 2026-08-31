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

import heapq
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


def land_path_len(gmap: GameMap, a: TileRef, b: TileRef,
                  limit: int) -> int | None:
    """`a`~`b` 를 **선로가 실제로 지나갈 수 있는가**, 지나간다면 몇 칸인가.
    `limit` 칸을 넘으면 None — 넘는지만 알면 되므로 넘는 순간 끊는다.

    원본 `AStarRail` 의 통행 규칙을 따른다(`isTraversable`):

    - 통행불가 지형(산 이상)은 못 지난다
    - 육지는 지난다
    - **물은 해안선일 때만** 지난다 — 좁은 해협은 건너지만 먼바다는 못 나간다.
      원본은 `fromShoreline || isShoreline(to)` 라 해안선 칸에서 한 칸 나가는
      것까지 허용한다. 그대로 옮겼다

    ⚠ **비용은 균일 1로 둔다. 원본은 물 +5 · 방향 전환 +3 이다.** 우리가 쓰는
    것은 경로의 *모양*이 아니라 *길이가 상한을 넘는가* 뿐이고, 최단 길이는
    원본이 고른 비용최적 경로의 길이보다 **항상 짧거나 같다.** 즉 우리 판정은
    원본보다 **너그러운 쪽**으로만 틀린다 — 원본이 잇는 것을 우리가 끊는 일은
    없다. 페널티까지 옮기면 다익스트라가 되고 이 자리는 판마다 수천 번 돈다.

    상자로 탐색을 묶지 않는다(`water_path` 와 다른 점). `limit` 이 이미 곧
    반경 상한이라 A* 가 `f > limit` 에서 스스로 멈춘다."""
    if a == b:
        return 0
    w = gmap.width
    bx, by = b % w, b // w

    def h(t: TileRef) -> int:
        return abs(t % w - bx) + abs(t // w - by)

    def traversable(t: TileRef, from_shore: bool) -> bool:
        if gmap.is_impassable(t):
            return False
        if not gmap.is_ocean(t):
            return True
        return from_shore or gmap.is_shoreline(t)

    if h(a) > limit:
        return None
    g: dict[TileRef, int] = {a: 0}
    heap: list[tuple[int, int, TileRef]] = [(h(a), 0, a)]
    while heap:
        f, gc, cur = heapq.heappop(heap)
        if f > limit:
            return None              # 힙의 머리가 이미 상한을 넘었다 = 전부 넘는다
        if gc > g.get(cur, 1 << 30):
            continue
        from_shore = gmap.is_shoreline(cur)
        for n in gmap.neighbors(cur):
            if n in g:
                continue             # 4방향 균일 비용이라 다시 볼 일이 없다
            if not traversable(n, from_shore):
                continue
            if n == b:
                return gc + 1
            g[n] = gc + 1
            heapq.heappush(heap, (gc + 1 + h(n), gc + 1, n))
    return None


def _is_trade_station(s: "Station") -> bool:
    """도시·항구만 판다(`Cluster.isTradeStation`). 공장은 정거장이다."""
    return s.unit.utype in (UnitType.CITY, UnitType.PORT)


@dataclass
class Station:
    """건물에 붙은 역. 건물이 사라지면 역도 사라진다."""
    tile: TileRef
    owner: int
    unit: Unit


@dataclass
class TrainStop:
    """기차가 설 역 하나의 **스냅숏**.

    `Station` 을 그대로 들면 안 된다 — `rebuild()` 가 매 tick 역 목록을 새로
    만들므로, 이미 부서진 건물의 역을 붙들고 달리게 된다. 여정 중에 역이
    살아 있는지는 그때그때 다시 확인한다(원본 `stations[1].isActive()`)."""
    tile: TileRef
    owner: int
    trade: bool          # 도시·항구면 True. **공장 역은 지나가기만 한다**


@dataclass
class Train:
    """역들을 **차례로** 들른다. 서는 역마다 돈이 오간다(`onTrainStop`).

    ⚠ 전에는 출발지에서 목적지까지 직선으로 한 번에 날아가 **끝에서 한 번만**
    벌었다(§5.70, 이식 누락 쉰). 원본은 정거장마다 판다 — 그래서 긴 노선이
    실제로 더 벌고, 방문 수 페널티(`train_gold`)도 그제야 뜻을 갖는다."""
    owner: int
    stops: list = field(default_factory=list)   # 아직 안 들른 역들(TrainStop)
    leg_src: TileRef = 0                        # 지금 구간의 출발 역 자리
    # 여정을 시작한 역. `leg_src` 는 달리면서 바뀌므로 **누가 냈는지**는 여기 남긴다
    # (공장 역만 낸다는 규칙을 재려면 출발지를 알아야 한다).
    origin: TileRef = 0
    progress: float = 0.0
    cities_visited: int = 0

    def advance(self) -> None:
        self.progress += C.TRAIN_SPEED

    def leg_length(self, gmap: GameMap) -> float:
        w = gmap.width
        a, b = self.leg_src, self.stops[0].tile
        return math.hypot(a % w - b % w, a // w - b // w)

    def leg_done(self, gmap: GameMap) -> bool:
        return bool(self.stops) and self.progress >= self.leg_length(gmap)

    def begin_next_leg(self, gmap: GameMap) -> None:
        """다음 구간으로 넘어간다. **남은 거리를 이월한다** — 원본도
        `currentTile = leftOver` 로 넘긴다. 버리면 역이 많은 노선일수록
        기차가 느려진다."""
        over = self.progress - self.leg_length(gmap)
        self.leg_src = self.stops[0].tile
        self.stops.pop(0)
        self.progress = max(0.0, over)


@dataclass
class RailNetwork:
    """역들과 그 사이 노선. 노선은 역 쌍이 사거리 안이고 **선로가 실제로 닿을 때**
    생긴다."""

    stations: list[Station] = field(default_factory=list)
    # 역 쌍 → 이어지는가. **판 전체에 걸쳐 남긴다** — 원본도 역이 생길 때 한 번
    # `findTilePath` 를 돌고 그 결과를 `Railroad` 로 들고 있지, 매 tick 다시
    # 재지 않는다. 키는 정렬한 칸 쌍이라 방향이 없다.
    _linked: dict[tuple[int, int], bool] = field(default_factory=dict)
    # 캐시를 언제 버릴지. 지형이 바뀌면(핵이 육지를 바다로) 선로도 끊긴다.
    _terrain_epoch: int = -1

    def connected(self, gmap: GameMap, a: TileRef, b: TileRef) -> bool:
        """두 역이 이어지는가 — **거리도 맞고 선로도 닿아야 한다.**

        원본 `RailNetworkImpl.connect` 는 거리 검사를 통과한 쌍에 대해
        `findTilePath` 를 돌고 **길이가 `railroadMaxSize`(=110×√2≈155) 미만일
        때만** 노선을 만든다(§5.84). 그래서 만 사이로 15칸 떨어진 두 도시라도
        육로가 크게 돌아가면 안 이어지고, 다른 섬이면 아예 못 이어진다.

        전에는 직선 거리만 봤다 — **바다 건너 110칸도 이어졌다.**"""
        if not station_range_ok(gmap, a, b):
            return False
        epoch = gmap.terrain_epoch
        if epoch != self._terrain_epoch:
            self._linked.clear()
            self._terrain_epoch = epoch
        key = (a, b) if a < b else (b, a)
        hit = self._linked.get(key)
        if hit is None:
            hit = land_path_len(gmap, a, b, int(C.RAILROAD_MAX_SIZE)) is not None
            self._linked[key] = hit
        return hit

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
                   if s.tile not in seen
                   and self.connected(gmap, here.tile, s.tile)]
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
        # ⚠ **목적지는 반드시 도시·항구다**(원본 `Cluster.randomTradeDestination`
        # 이 `tradeStations` 에서만 고른다). 공장은 지나가는 역일 뿐이라, 공장에서
        # 끝나는 여정은 **아무도 벌지 않는다** — 원본은 그런 기차를 애초에 안 낸다
        # (`hasAnyTradeDestination`). 뒤에서부터 공장을 잘라 낸다.
        while path and not _is_trade_station(path[-1]):
            path.pop()
        if not path:
            return None
        # ⚠ 관계·방문 수를 **여기서 굳히지 않는다.** 정거장마다 그 역 주인과의
        # 관계로 값을 매기고(원본 `rel(trainOwner, stationOwner)`), 방문 수는
        # 달리면서 쌓인다(`_tradeStopsVisited`). 다음 기차는 0부터 다시 센다.
        stops = [TrainStop(tile=s.tile, owner=s.owner, trade=_is_trade_station(s))
                 for s in path]
        return Train(owner=pid, stops=stops, leg_src=start.tile,
                     origin=start.tile)
