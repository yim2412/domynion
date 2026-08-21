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
    u = Unit(utype, pid, tile=st.gmap.ref(x, y))
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
    st.rail.rebuild(st.alive)
    tiles = {s.tile for s in st.rail.stations}
    assert st.gmap.ref(20, 20) in tiles and st.gmap.ref(60, 20) in tiles
    assert st.gmap.ref(30, 30) not in tiles, "방어초소에 역이 붙었다"
    assert set(RAIL_STATION_UNITS) == {UnitType.CITY, UnitType.PORT, UnitType.FACTORY}


def test_buildings_under_construction_have_no_station():
    st = state()
    u = give(st, 0, UnitType.CITY, 20, 20)
    u.ticks_left = 20
    st.rail.rebuild(st.alive)
    assert st.rail.stations == []


def test_destroyed_buildings_lose_their_stations():
    """건물이 핵에 날아가면 역도 같이 사라진다 — `rebuild` 가 매번 다시 만든다."""
    st = state()
    u = give(st, 0, UnitType.CITY, 20, 20)
    st.rail.rebuild(st.alive)
    assert len(st.rail.stations) == 1
    st.players[0].units.units.remove(u)
    st.rail.rebuild(st.alive)
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
    st.rail.rebuild(st.alive)
    assert st.rail.dispatch(st.gmap, st.diplomacy, 0, random.Random(0)) is None, \
        "역이 하나뿐인데 기차가 떴다"
    give(st, 0, UnitType.FACTORY, 60, 20)
    st.rail.rebuild(st.alive)
    assert st.rail.dispatch(st.gmap, st.diplomacy, 0, random.Random(0)) is not None


def test_arriving_train_pays_the_owner():
    st = state()
    give(st, 0, UnitType.CITY, 20, 20)
    give(st, 1, UnitType.CITY, 60, 20)
    st.gmap.owner[st.gmap.ref(60, 20)] = 1
    st.rail.rebuild(st.alive)
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
