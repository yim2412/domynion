"""P4 — 수송선 · 무역선 · 기부.

바다는 육지와 규칙이 다르다. 배는 프론티어처럼 번지지 않고 **경로를 따라 tick 당 한 칸**
움직이며, 도착해서야 상륙 지점을 정복하고 **그 자리에서 육상 공격이 새로 시작된다.**
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.constants import Terrain
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.naval import (best_spawn, shoreline_tiles, trade_gold,
                                 trade_spawn_rate, water_path)
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(rows: list[str], owners: dict[int, tuple[int, int]]) -> GameState:
    """⚠ 배를 띄우려면 **소유 칸이 바다에 닿아야** 한다. 안쪽 칸만 가지면
    `best_spawn` 이 None 을 돌려 `send_boat` 가 조용히 실패한다."""
    gm = GameMap.from_rows(rows)
    players = {}
    for pid, (x, y) in owners.items():
        t = gm.ref(x, y)
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 1 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    return st


# --- 경로 -------------------------------------------------------------------

def test_water_path_only_crosses_ocean():
    gm = GameMap.from_rows(["..~~~..", "..AAA..", "..~~~.."])
    path = water_path(gm, gm.ref(1, 0), gm.ref(5, 0))
    assert path is not None
    assert all(gm.terrain[t] == Terrain.OCEAN for t in path[:-1]), "육지를 밟았다"
    assert path[-1] == gm.ref(5, 0)


def test_water_path_is_shortest():
    gm = GameMap.from_rows(["." + "~" * 8 + "."] * 3)
    path = water_path(gm, gm.ref(0, 1), gm.ref(9, 1))
    assert len(path) == 9, "BFS 인데 최단이 아니다"


def test_no_path_when_land_blocks_the_way():
    gm = GameMap.from_rows(["..AA..", "..AA..", "..AA.."])
    assert water_path(gm, gm.ref(1, 1), gm.ref(4, 1)) is None


def test_shoreline_and_best_spawn():
    gm = GameMap.from_rows(["...~~", "...~~", "...~~"])
    for y in range(3):
        for x in range(3):
            gm.owner[gm.ref(x, y)] = 0
    shore = set(shoreline_tiles(gm, 0).tolist())
    assert gm.ref(2, 1) in shore
    assert gm.ref(0, 1) not in shore, "안쪽 칸은 해안이 아니다"
    assert best_spawn(gm, 0, gm.ref(4, 0)) == gm.ref(2, 0)


# --- 수송선 -----------------------------------------------------------------

def test_boat_carries_a_fifth_of_troops():
    """`boatAttackAmount()` = 병력 / 5."""
    st = state(["..~~~.."], {0: (1, 0), 1: (5, 0)})   # 둘 다 바다에 닿는다
    p = st.players[0]
    p.troops = 50_000.0
    boat = st.send_boat(0, st.gmap.ref(5, 0))
    assert boat is not None
    assert boat.troops == pytest.approx(50_000.0 * C.BOAT_ATTACK_RATIO)
    assert p.troops == pytest.approx(50_000.0 * 0.8)


def test_boat_limit_is_three():
    """`boatMaxNumber()` = 3. 없으면 배로 무한히 상륙할 수 있다."""
    st = state(["..~~~..", "..~~~..", "..~~~.."], {0: (1, 0), 1: (5, 0)})
    st.players[0].troops = 1_000_000.0
    for y in range(3):
        st.gmap.owner[st.gmap.ref(1, y)] = 0
        st.gmap.owner[st.gmap.ref(5, y)] = 1
    st._counts = {0: 3, 1: 3}
    made = [st.send_boat(0, st.gmap.ref(5, y % 3)) for y in range(5)]
    assert sum(1 for b in made if b is not None) == C.BOAT_MAX_NUMBER


def test_boat_moves_one_tile_per_tick_then_lands_and_attacks():
    """도착하면 상륙 지점을 먹고 **그 자리에서 육상 공격이 시작된다.**

    막지 않았으면: 배가 계속 육지를 먹거나, 상륙만 하고 멈춘다."""
    # 육지 비율에 주의 — 한쪽이 80% 를 넘으면 **지배 승리로 판이 끝나** tick 이
    # 곧바로 반환되고 배가 영원히 안 움직인다 (실제로 그렇게 한 번 속았다).
    st = state(["." * 6 + "~~~" + "." * 6] * 2, {0: (1, 0), 1: (9, 0)})
    for y in range(2):
        for x in range(0, 6):
            st.gmap.owner[st.gmap.ref(x, y)] = 0
        for x in range(9, 15):
            st.gmap.owner[st.gmap.ref(x, y)] = 1
    st._counts = {0: 12, 1: 12}
    st.players[0].troops = 200_000.0
    boat = st.send_boat(0, st.gmap.ref(9, 0))
    assert boat is not None
    start = boat.step_i
    st.tick()
    assert boat.step_i == start + C.BOAT_TICKS_PER_MOVE

    for _ in range(30):
        st.tick()
        if boat not in st.boats:
            break
    assert boat not in st.boats
    assert int(st.gmap.owner[st.gmap.ref(9, 0)]) == 0, "상륙 지점을 못 먹었다"
    assert st.attacks, "상륙 후 육상 공격이 시작되지 않았다"


def test_boat_returns_troops_if_target_becomes_friendly():
    st = state(["..~~~.."], {0: (1, 0), 1: (5, 0)})
    p = st.players[0]
    p.troops = 50_000.0
    boat = st.send_boat(0, st.gmap.ref(5, 0))
    assert boat is not None and boat.target == 1
    before = p.troops
    st.diplomacy.form(0, 1, tick=0)
    st.tick()
    assert boat not in st.boats
    assert p.troops > before, "병력이 돌아오지 않았다"


def test_cannot_boat_to_an_ally():
    st = state(["..~~~.."], {0: (1, 0), 1: (5, 0)})
    st.players[0].troops = 50_000.0
    st.diplomacy.form(0, 1, tick=0)
    assert st.send_boat(0, st.gmap.ref(5, 0)) is None


# --- 무역선 -----------------------------------------------------------------

def test_trade_gold_punishes_short_routes():
    """`75000/(1+e^(−0.03×(거리−300))) + 50×거리` — 300 아래는 시그모이드가 누른다."""
    short, mid, long = trade_gold(100), trade_gold(300), trade_gold(600)
    assert short < mid < long
    assert mid == pytest.approx(75_000 / 2 + 50 * 300, rel=1e-6)
    assert short < mid / 5, "단거리 페널티가 약하다"


def test_trade_spawn_rate_has_a_pity_timer():
    """계속 안 뜨면 확률이 올라간다(분모가 작아진다)."""
    assert trade_spawn_rate(3, 0) < trade_spawn_rate(0, 0)
    assert trade_spawn_rate(0, 400) > trade_spawn_rate(0, 0), "배가 많으면 잘 안 뜬다"


def test_trade_pays_both_port_owners():
    st = state(["." + "~" * 8 + "."] * 3, {0: (0, 0), 1: (9, 0)})
    for pid, x in ((0, 0), (1, 9)):
        u = Unit(UnitType.PORT, pid, tile=st.gmap.ref(x, 1))
        st.gmap.owner[u.tile] = pid
        st.players[pid].units.units.append(u)
        st.players[pid].units.record_constructed(UnitType.PORT)
    st._counts = {0: 2, 1: 2}
    assert st._spawn_trade_ship([(st.gmap.ref(0, 1), 0), (st.gmap.ref(9, 1), 1)])
    ship = st.trade_ships[0]
    g0, g1 = st.players[0].gold, st.players[1].gold
    for _ in range(len(ship.path) + 2):
        st.tick()
        if ship not in st.trade_ships:
            break
    gained0 = st.players[0].gold - g0 - st.tick_count * C.GOLD_PER_TICK_HUMAN
    gained1 = st.players[1].gold - g1 - st.tick_count * C.GOLD_PER_TICK_HUMAN
    assert gained0 > 0 and gained0 == gained1, "양쪽이 같이 벌어야 한다"


def test_embargo_stops_trade():
    st = state(["." + "~" * 8 + "."] * 3, {0: (0, 0), 1: (9, 0)})
    st.diplomacy.start_embargo(0, 1)
    ports = [(st.gmap.ref(0, 1), 0), (st.gmap.ref(9, 1), 1)]
    # 금수는 양방향이다(`canTrade`) — 뽑히는 순서와 무관하게 막혀야 한다
    for _ in range(20):
        assert not st._spawn_trade_ship(ports)


# --- 기부 -------------------------------------------------------------------

def test_donations_move_resources():
    st = state(["....."], {0: (0, 0), 1: (4, 0)})
    st.players[0].gold = 5_000
    assert st.donate_gold(0, 1, 2_000)
    assert st.players[0].gold == 3_000 and st.players[1].gold == 2_000
    assert not st.donate_gold(0, 1, 999_999), "없는 골드는 못 준다"
    assert not st.donate_gold(0, 0, 100), "자기 자신에게는 못 준다"

    before = st.players[1].troops
    assert st.donate_troops(0, 1, 1_000.0)
    assert st.players[1].troops == before + 1_000.0


def _slow_shoreline(gm, pid):
    """벡터화 전의 구현. 대조용."""
    import numpy as np
    return np.array([t for t in gm.owned_refs(pid).tolist() if gm.is_shore(t)],
                    dtype=np.int64)


def test_shoreline_matches_the_loop_it_replaced():
    """`shoreline_tiles` 를 numpy 로 폈다(영토 17만 칸에서 589ms → 수 ms).

    빨라져도 답이 다르면 소용없다."""
    import numpy as np
    gm = GameMap.from_rows(["~....~", "..~...", "......", "~~...~"])
    for t in range(gm.size):
        if gm.passable(t):
            gm.owner[t] = 0
    fast = np.sort(shoreline_tiles(gm, 0))
    slow = np.sort(_slow_shoreline(gm, 0))
    assert np.array_equal(fast, slow)
    assert len(fast) > 0 and len(fast) < int((gm.owner == 0).sum())


def test_shoreline_is_empty_without_territory():
    import numpy as np
    gm = GameMap.from_rows(["~..~"])
    assert len(shoreline_tiles(gm, 3)) == 0
    assert np.array_equal(shoreline_tiles(gm, 3), _slow_shoreline(gm, 3))


def test_inland_tiles_are_not_shoreline():
    gm = GameMap.from_rows(["~~~~~", "~...~", "~...~", "~...~", "~~~~~"])
    for t in range(gm.size):
        if gm.passable(t):
            gm.owner[t] = 0
    shore = set(shoreline_tiles(gm, 0).tolist())
    assert gm.ref(2, 2) not in shore, "한가운데 칸이 해안으로 잡혔다"
    assert gm.ref(1, 1) in shore
