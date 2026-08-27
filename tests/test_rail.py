"""철도 — 역·노선·기차.

무역선이 **바다**로 골드를 벌듯 기차는 **육지**로 번다. 원본에서 가장 특이한 규칙은
**남의 역에 닿는 것이 자기 역보다 2.5배 벌린다**는 것이다 — 그래서 철도를 깔면
이웃과 사이가 좋을 이유가 생긴다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.diplomacy import Diplomacy
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.rail import (RAIL_STATION_UNITS, RailNetwork, station_range_ok,
                                train_gold, train_spawn_rate)
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state() -> GameState:
    gm = GameMap.from_rows(["." * 200] * 100)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid * 60 + 10, 10)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def give(st: GameState, pid: int, utype: UnitType, x: int, y: int) -> Unit:
    """건물을 놓는다. **그 칸도 준다.**

    ⚠ §5.58("땅을 잃으면 건물도 잃는다")부터는 주인 없는 칸의 건물이 매 tick
    부서진다. 땅을 안 주면 `tick()` 한 번에 역이 전부 사라져, 기차가 안 뜨는
    것을 "발차 규칙 버그"로 볼 뻔했다."""
    t = st.gmap.ref(x, y)
    if int(st.gmap.owner[t]) != pid:
        old = int(st.gmap.owner[t])
        if old >= 0:
            st._counts[old] = max(0, st._counts.get(old, 0) - 1)
        st.gmap.owner[t] = pid
        st._counts[pid] = st._counts.get(pid, 0) + 1
    u = Unit(utype, pid, tile=t)
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(utype)
    return u


# --- 골드 -------------------------------------------------------------------

def test_reaching_someone_elses_station_pays_far_more():
    """동맹 35,000 · 남/팀 25,000 · **자기 10,000**.

    막지 않았으면(전부 같게 두면): 자기 건물끼리만 이어 골드를 찍어내는 게 최적이 되고
    철도가 외교를 만드는 이유가 사라진다."""
    assert train_gold("ally", 0) == 35_000
    assert train_gold("other", 0) == 25_000
    assert train_gold("team", 0) == 25_000
    assert train_gold("self", 0) == 10_000
    assert train_gold("ally", 0) > train_gold("self", 0) * 3


def test_first_ten_cities_have_no_penalty_then_five_thousand_each():
    assert train_gold("other", 9) == 25_000
    assert train_gold("other", 10) == 25_000 - 5_000
    assert train_gold("other", 12) == 25_000 - 15_000


def test_gold_never_drops_below_the_floor():
    """바닥 5,000 — 아무리 많이 다녀도 손해는 아니다."""
    assert train_gold("self", 1_000) == 5_000
    assert train_gold("ally", 1_000) == 5_000


def test_spawn_rate_improves_with_factories():
    """`(공장수 + 10) × 15` — 확률은 그 역수다. 공장이 많을수록 자주 뜬다."""
    assert train_spawn_rate(0) == 150
    assert train_spawn_rate(10) == 300
    # 기대 대수 = 공장수 / rate 이므로 공장이 늘수록 커진다
    assert 1 / train_spawn_rate(1) * 1 < 10 / train_spawn_rate(10) * 1


# --- 노선 -------------------------------------------------------------------

def test_stations_link_only_within_range():
    """15~110. 너무 가까우면 골드 찍어내기가 되고, 너무 멀면 지도를 가로지른다."""
    gm = GameMap.from_rows(["." * 200] * 50)
    a = gm.ref(50, 25)
    assert not station_range_ok(gm, a, gm.ref(50 + C.TRAIN_STATION_MIN_RANGE - 1, 25))
    assert station_range_ok(gm, a, gm.ref(50 + C.TRAIN_STATION_MIN_RANGE, 25))
    assert station_range_ok(gm, a, gm.ref(50 + C.TRAIN_STATION_MAX_RANGE, 25))
    assert not station_range_ok(gm, a, gm.ref(50 + C.TRAIN_STATION_MAX_RANGE + 1, 25))


def test_stations_come_from_buildings_not_built_separately():
    st = state()
    give(st, 0, UnitType.CITY, 20, 20)
    give(st, 0, UnitType.FACTORY, 60, 20)
    give(st, 0, UnitType.DEFENSE_POST, 30, 30)   # 역이 안 붙는 건물
    st.rail.rebuild(st.gmap, st.alive)
    tiles = {s.tile for s in st.rail.stations}
    assert st.gmap.ref(20, 20) in tiles and st.gmap.ref(60, 20) in tiles
    assert st.gmap.ref(30, 30) not in tiles, "방어초소에 역이 붙었다"
    assert set(RAIL_STATION_UNITS) == {UnitType.CITY, UnitType.PORT, UnitType.FACTORY}


def test_buildings_under_construction_have_no_station():
    st = state()
    u = give(st, 0, UnitType.CITY, 20, 20)
    u.ticks_left = 20
    st.rail.rebuild(st.gmap, st.alive)
    assert st.rail.stations == []


def test_destroyed_buildings_lose_their_stations():
    """건물이 핵에 날아가면 역도 같이 사라진다 — `rebuild` 가 매번 다시 만든다."""
    st = state()
    # ⚠ **공장으로 만든다.** §5.60 부터 도시는 사거리 안에 공장이 있어야 역이다.
    u = give(st, 0, UnitType.FACTORY, 20, 20)
    st.rail.rebuild(st.gmap, st.alive)
    assert len(st.rail.stations) == 1
    st.players[0].units.units.remove(u)
    st.rail.rebuild(st.gmap, st.alive)
    assert st.rail.stations == []


# --- 관계 -------------------------------------------------------------------

def test_relation_reflects_diplomacy():
    net = RailNetwork()
    d = Diplomacy(teams={0: 1, 2: 1})
    assert net.relation(d, 0, 0) == "self"
    assert net.relation(d, 0, 2) == "team"
    assert net.relation(d, 0, 3) == "other"
    d.form(0, 3, tick=0)
    assert net.relation(d, 0, 3) == "ally"


# --- 배차·수익 --------------------------------------------------------------

def test_dispatch_needs_a_reachable_station():
    st = state()
    give(st, 0, UnitType.CITY, 20, 20)
    st.rail.rebuild(st.gmap, st.alive)
    assert st.rail.dispatch(st.gmap, st.diplomacy, 0, random.Random(0)) is None, \
        "역이 하나뿐인데 기차가 떴다"
    give(st, 0, UnitType.FACTORY, 60, 20)
    st.rail.rebuild(st.gmap, st.alive)
    assert st.rail.dispatch(st.gmap, st.diplomacy, 0, random.Random(0)) is not None


def test_arriving_train_pays_the_owner():
    st = state()
    give(st, 0, UnitType.FACTORY, 20, 20)
    give(st, 1, UnitType.FACTORY, 60, 20)
    st.gmap.owner[st.gmap.ref(60, 20)] = 1
    st.rail.rebuild(st.gmap, st.alive)
    t = st.rail.dispatch(st.gmap, st.diplomacy, 0, random.Random(0))
    assert t is not None
    st.trains.append(t)
    before = st.players[0].gold
    for _ in range(200):
        st.tick()
        if t not in st.trains:
            break
    gained = st.players[0].gold - before - st.tick_count * C.GOLD_PER_TICK_HUMAN
    assert gained > 0, "기차가 골드를 안 벌었다"


def test_no_factories_means_no_trains():
    st = state()
    give(st, 0, UnitType.CITY, 20, 20)
    give(st, 0, UnitType.CITY, 60, 20)
    for _ in range(300):
        st.tick()
    assert st.trains == [], "공장이 없는데 기차가 떴다"


# --- 공장이 철도망의 전제다 (§5.60) -------------------------------------------

def test_a_city_without_a_factory_is_not_a_station():
    """⚠ **공장이 있어야 역이 된다**(이식 누락 마흔둘).

    도시·항구는 사거리 안에 공장이 있을 때만 역이다. 전에는 무조건 역이라
    **공장을 한 채도 안 지은 나라도 철도로 벌었다.**"""
    st = state()
    give(st, 0, UnitType.CITY, 20, 20)
    give(st, 0, UnitType.PORT, 24, 20)
    st.rail.rebuild(st.gmap, st.alive)
    assert st.rail.stations == [], "공장 없이 역이 생겼다"

    give(st, 0, UnitType.FACTORY, 60, 20)        # 사거리(110) 안이다
    st.rail.rebuild(st.gmap, st.alive)
    assert len(st.rail.stations) == 3, "공장이 도시·항구를 역으로 안 만들었다"


def test_a_factory_too_far_away_does_not_promote_the_city():
    """대조군 — 사거리 밖 공장은 도시를 역으로 만들지 않는다."""
    st = state()
    give(st, 0, UnitType.CITY, 5, 20)
    give(st, 0, UnitType.FACTORY, 5 + C.TRAIN_STATION_MAX_RANGE + 10, 20)
    st.rail.rebuild(st.gmap, st.alive)
    tiles = {s.tile for s in st.rail.stations}
    assert st.gmap.ref(5, 20) not in tiles, "사거리 밖 공장이 역을 만들었다"


def test_someone_elses_factory_does_not_promote_my_city():
    """**자기 공장**이어야 한다. 남의 공장 옆이라고 내 도시가 역이 되지 않는다."""
    st = state()
    give(st, 0, UnitType.CITY, 20, 20)
    give(st, 1, UnitType.FACTORY, 60, 20)
    st.rail.rebuild(st.gmap, st.alive)
    tiles = {s.tile for s in st.rail.stations}
    assert st.gmap.ref(20, 20) not in tiles, "남의 공장이 내 도시를 역으로 만들었다"
    assert st.gmap.ref(60, 20) in tiles


# --- 여정별 방문 수 (§5.60) ---------------------------------------------------

def test_visited_count_resets_every_journey():
    """⚠ **그 여정에서 들른 도시/항구 수**다(이식 누락 마흔하나).

    전에는 판 전체 누적 발차 수를 넣고 있어서, 철도를 깐 나라는 **기차 열다섯
    대째부터 영원히 최저 수입(5,000)** 을 받았다."""
    st = state()
    give(st, 0, UnitType.FACTORY, 20, 20)
    give(st, 0, UnitType.CITY, 60, 20)
    st.rail.rebuild(st.gmap, st.alive)
    counts = []
    for seed in range(20):
        t = st.rail.dispatch(st.gmap, st.diplomacy, 0, random.Random(seed))
        if t is not None:
            counts.append(t.cities_visited)
    assert counts, "한 대도 안 떴다"
    assert max(counts) <= 2, f"방문 수가 누적된다: {counts}"


def test_factory_stops_do_not_count_as_visits():
    """공장 역은 **안 센다** — 원본이 `City`·`Port` 만 센다."""
    st = state()
    give(st, 0, UnitType.FACTORY, 20, 20)
    give(st, 0, UnitType.FACTORY, 60, 20)
    st.rail.rebuild(st.gmap, st.alive)
    t = st.rail.dispatch(st.gmap, st.diplomacy, 0, random.Random(0), hops=1)
    assert t is not None
    assert t.cities_visited == 0, "공장을 방문으로 셌다"


def test_a_train_visits_several_stations():
    """⚠ **여러 역을 거친다**(원본 `nextStation`). 전에는 역→역 한 번이었다.

    페널티가 "긴 노선일수록 한 정거장이 싸진다"는 뜻이므로, 긴 노선이 없으면
    그 페널티는 걸릴 일이 없다."""
    st = state()
    give(st, 0, UnitType.FACTORY, 20, 20)
    for i, x in enumerate((60, 100, 140, 180)):
        give(st, 0, UnitType.CITY, x, 20)
    st.rail.rebuild(st.gmap, st.alive)
    longest = 0
    for seed in range(20):
        t = st.rail.dispatch(st.gmap, st.diplomacy, 0, random.Random(seed))
        if t is not None:
            longest = max(longest, len(t.path))
    assert longest >= 2, f"가장 긴 여정이 {longest} 정거장 — 한 번만 간다"


# --- 발차 (§5.60) -------------------------------------------------------------

def test_only_factories_send_trains():
    """**공장 역만 기차를 낸다.** 도시·항구 역은 지나가는 정거장이다."""
    st = state()
    give(st, 0, UnitType.FACTORY, 20, 20)
    give(st, 0, UnitType.CITY, 60, 20)
    st.rail.rebuild(st.gmap, st.alive)
    # ⚠ **첫 기차에서 멈추면 안 된다.** 공장 역이 먼저 낼 확률이 높아, 도시 역도
    # 낸다는 변이(AA4)를 못 잡는다 — 오래 돌려 **출발지를 모아** 본다.
    srcs = set()
    for _ in range(4000):
        st.tick()
        for t in st.trains:
            srcs.add(t.src)
    assert srcs, "기차가 한 대도 안 떴다"
    assert srcs == {st.gmap.ref(20, 20)},         f"공장이 아닌 역에서도 기차가 떴다: {srcs}"


def test_a_station_waits_out_its_cooldown():
    """한 역은 **10 tick 안에 두 번 못 낸다**(`ticksCooldown`).

    ⚠ 확률이 1/165 라 그냥 돌리면 10 tick 안에 두 번 뜰 일이 거의 없어 **쿨다운을
    지워도 안 깨진다.** 그래서 발차 확률을 1 에 가깝게 올려야 하는데, **레벨을
    올리면 오히려 낮아진다** — `train_spawn_rate` 가 `owned()`(레벨 합)를 받아
    Lv200 이면 분모가 3,150 이 된다(실측). 대신 `rate` 를 직접 1 로 낮춘다."""
    import domynion.core.engine as _eng
    st = state()
    f = give(st, 0, UnitType.FACTORY, 20, 20)
    f.level = 30                                 # 판정을 30번 굴린다
    give(st, 0, UnitType.CITY, 60, 20)
    # 발차 확률을 1 로 만든다 — 쿨다운만 남겨 두고 재기 위해서다
    monkey = _eng.train_spawn_rate
    _eng.train_spawn_rate = lambda n: 1
    st.rail.rebuild(st.gmap, st.alive)
    # ⚠ `st.trains` 를 세면 안 된다 — 역 사이가 40칸이고 속도가 4 라 기차가
    # **10 tick 만에 도착해 사라진다.** 누적 발차 수를 따로 센다.
    fired = 0
    seen = set()
    for _ in range(C.TRAIN_STATION_COOLDOWN_TICKS * 3):
        st.tick()
        for t in st.trains:
            if id(t) not in seen:
                seen.add(id(t))
                fired += 1
    _eng.train_spawn_rate = monkey
    # 30 tick 동안 쿨다운(10)이 있으면 **세 대**, 없으면 서른 대다
    assert 0 < fired <= 5, f"{fired}대 — 쿨다운이 안 걸린다"
