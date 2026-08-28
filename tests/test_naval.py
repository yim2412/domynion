"""P4 — 수송선 · 무역선 · 기부.

바다는 육지와 규칙이 다르다. 배는 프론티어처럼 번지지 않고 **경로를 따라 tick 당 한 칸**
움직이며, 도착해서야 상륙 지점을 정복하고 **그 자리에서 육상 공격이 새로 시작된다.**
"""

from __future__ import annotations

import random
from collections import deque

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.constants import Terrain
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.naval import (best_spawn, port_check_due,
                                 proximity_bonus_count, shoreline_tiles,
                                 trade_gold, trade_spawn_rate, trading_ports,
                                 water_path)
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType
from domynion.ui.rates import gold_pip


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
    # 스폰 면역(5초)을 지난 시점 — 사람은 그전에 사람을 못 친다(`canAttackPlayer`).
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
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

    # ⚠ **막힌 것을 알린다**(§5.67). 없으면 클릭이 아무 일도 안 일어난 것처럼
    # 보인다 — 병력도 안 줄고 배도 안 뜨니 사람은 3척 제한이 아니라 "지도를
    # 잘못 눌렀나"를 의심한다. 두 번 막혔으니 소식도 둘이다.
    from domynion.core.events import EventKind
    failed = [e for e in st.log.items if e.kind is EventKind.ATTACK_FAILED]
    assert len(failed) == 2 and all(e.who == 0 for e in failed)
    assert failed[0].amount == C.BOAT_MAX_NUMBER


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
    ports = [(st.gmap.ref(0, 1), 0, 1, st.players[0].units.units[0]),
             (st.gmap.ref(9, 1), 1, 1, st.players[1].units.units[0])]
    assert st._spawn_trade_ship(st.gmap.ref(0, 1), 0, ports)
    ship = st.trade_ships[0]
    g0, g1 = st.players[0].gold, st.players[1].gold
    t0 = st.tick_count                     # 0 이 아니다 — 헬퍼가 면역 뒤에서 시작한다
    for _ in range(len(ship.path) + 2):
        st.tick()
        if ship not in st.trade_ships:
            break
    ticked = st.tick_count - t0
    gained0 = st.players[0].gold - g0 - ticked * C.GOLD_PER_TICK_HUMAN
    gained1 = st.players[1].gold - g1 - ticked * C.GOLD_PER_TICK_HUMAN
    assert gained0 > 0 and gained0 == gained1, "양쪽이 같이 벌어야 한다"
    # HUD 의 `+N` 도 같은 자리에서 나온다(§5.69) — 원본 `addGold(gold, tile)` 의
    # `BonusEvent`. 배선이 끊기면 무역 수입이 화면에 한 번도 안 뜬다.
    assert gold_pip(st, 0) == gained0 and gold_pip(st, 1) == gained1


def test_embargo_stops_trade():
    st = state(["." + "~" * 8 + "."] * 3, {0: (0, 0), 1: (9, 0)})
    st.diplomacy.start_embargo(0, 1)
    ports = [(st.gmap.ref(0, 1), 0, 1, None), (st.gmap.ref(9, 1), 1, 1, None)]
    # 금수는 양방향이다(`canTrade`) — 어느 쪽에서 출발해도 막혀야 한다
    for src, owner in ((st.gmap.ref(0, 1), 0), (st.gmap.ref(9, 1), 1)):
        for _ in range(10):
            assert not st._spawn_trade_ship(src, owner, ports)


# --- 기부 -------------------------------------------------------------------

def test_donations_move_resources():
    st = state(["....."], {0: (0, 0), 1: (4, 0)})
    st.players[0].gold = 5_000
    assert not st.donate_gold(0, 1, 2_000), "모르는 사이에게는 못 준다 (§5.63)"

    st.diplomacy.form(0, 1, st.tick_count)
    assert st.donate_gold(0, 1, 2_000)
    assert st.players[0].gold == 3_000 and st.players[1].gold == 2_000
    assert not st.donate_gold(0, 1, 999_999), "없는 골드는 못 준다"
    assert not st.donate_gold(0, 0, 100), "자기 자신에게는 못 준다"

    before = st.players[1].troops
    assert not st.donate_troops(0, 1, 1_000.0),         "쿨다운은 골드·병력 **공용**이다 — 원본 `sentDonations` 가 하나다"
    st.tick_count += C.DONATE_COOLDOWN_TICKS
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


# --- 무역선 스폰은 항구마다 돈다 (이식 누락 열아홉) --------------------------

def _sea(width: int = 60) -> GameMap:
    """가로로 긴 바다 — 항구를 원하는 거리에 놓을 수 있다."""
    return GameMap.from_rows(["." + "~" * (width - 2) + "."] * 3)


def _port(st: GameState, pid: int, x: int, level: int = 1) -> Unit:
    u = Unit(UnitType.PORT, pid, tile=st.gmap.ref(x, 1), level=level)
    st.gmap.owner[u.tile] = pid
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(UnitType.PORT)
    return u


def test_port_check_offset_spreads_the_rolls():
    """`(ticks + checkOffset) % 10` — 항구마다 다른 tick 에 굴린다.

    한꺼번에 굴리면 무역선이 10 tick 주기로 뭉쳐 나온다."""
    fired = {off: [t for t in range(10) if port_check_due(off, t)]
             for off in range(10)}
    assert all(len(v) == 1 for v in fired.values()), "10 tick 에 정확히 한 번"
    assert len({v[0] for v in fired.values()}) == 10, "오프셋마다 다른 tick 이어야"


def test_proximity_bonus_count_is_clamped():
    """`within(전체/3, 4, 전체)` — 후보가 적으면 전부, 많으면 1/3."""
    assert proximity_bonus_count(30) == 10
    assert proximity_bonus_count(6) == 4, "바닥 4"
    assert proximity_bonus_count(2) == 2, "전체보다 클 수 없다"


def test_trading_ports_weights_by_level():
    """레벨이 곧 가중치다 — Lv3 항구는 Lv1 보다 세 배 잘 뽑힌다.

    ⚠ 균등 무작위로 두면 **레벨이 아무 일도 안 한다**(`unitsOwned` 때와 같은 누락)."""
    gm = _sea()
    src = gm.ref(0, 1)
    # 둘 다 300 이상 밖이 되도록 — 보너스가 끼면 레벨 몫이 안 보인다
    cands = [(gm.ref(30, 1), 1, 1), (gm.ref(31, 1), 2, 3)]
    w = trading_ports(gm, src, cands, friendly=set())
    assert w.count((gm.ref(31, 1), 2)) == 3 * w.count((gm.ref(30, 1), 1))


def test_trading_ports_bonus_goes_to_the_nearest_third():
    """근접 보너스는 **거리순 상위 1/3** 에만 간다.

    ⚠ 후보가 `proximity_bonus_count` 이하면 전원이 받으므로 **정렬을 지워도
    결과가 같다** — 그래서 후보를 12개 두고 가장 먼 것이 보너스를 못 받는지를 본다.
    (변이가 처음에 살아남았던 자리다. 재료가 문제였다.)"""
    gm = _sea(width=4000)
    src = gm.ref(0, 1)
    # 전부 300 밖 — 근접 보너스만 남기고 debuff 를 배제한다
    # ⚠ **먼 것부터** 넘긴다. 가까운 순으로 넘기면 정렬을 지워도 순서가 같아
    # 변이가 살아남는다(2차에서 실제로 살아남았다).
    cands = [(gm.ref(400 + i * 300, 1), 1 + i, 1) for i in reversed(range(12))]
    w = trading_ports(gm, src, cands, friendly=set())
    assert proximity_bonus_count(12) == 4
    nearest = [(gm.ref(400 + i * 300, 1), 1 + i) for i in range(4)]
    farthest = [(gm.ref(400 + i * 300, 1), 1 + i) for i in range(4, 12)]
    assert all(w.count(c) == 2 for c in nearest), "가까운 4곳은 레벨 몫 + 보너스"
    assert all(w.count(c) == 1 for c in farthest), "나머지는 레벨 몫뿐"


def test_trading_ports_gives_no_bonus_under_the_debuff_range():
    """300 미만은 근접·동맹 보너스에서 **빠진다**.

    `trade_gold` 시그모이드가 그 구간을 크게 깎으므로, 가까운 항구끼리 왕복하는
    것이 이득이 되면 안 된다. 대조군으로 300 밖 항구는 보너스를 받는다."""
    gm = _sea(width=800)
    src = gm.ref(0, 1)
    near = [(gm.ref(10, 1), 1, 1)]
    far = [(gm.ref(500, 1), 1, 1)]
    assert len(trading_ports(gm, src, near, friendly={1})) == 1, "가까우면 레벨 몫뿐"
    # 거리순 상위 1/3(바닥 4) + 동맹 → 레벨 몫의 3배
    assert len(trading_ports(gm, src, far, friendly={1})) == 3
    assert len(trading_ports(gm, src, far, friendly=set())) == 2, "동맹 몫만 빠진다"


def test_trading_ports_skips_unreachable_water():
    """수로가 안 이어지면 후보에서 아예 빠진다(`hasWaterComponent`)."""
    gm = GameMap.from_rows(["~~.~~"] * 3)      # 가운데 육지로 바다가 둘로 갈린다
    assert trading_ports(gm, gm.ref(0, 1), [(gm.ref(4, 1), 1, 1)], set()) == []


def test_each_port_keeps_its_own_pity_timer():
    """거절 카운터는 **항구마다** 쌓인다.

    ⚠ 판 전체에 하나로 두면 아무 항구나 성공했을 때 모두의 pity 가 리셋된다 —
    항구를 아무리 지어도 유통량이 안 늘던 원인이다."""
    st = GameState(gmap=_sea(), players={}, rng=random.Random(0))
    st._counts, st._posts = {}, DefensePostIndex(st.gmap.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    for pid, x in ((0, 2), (1, 50)):
        st.players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False,
                                      start=st.gmap.ref(x, 1))
        st._counts[pid] = 1
    a, b = _port(st, 0, 2), _port(st, 1, 50)
    # 굴리는 tick 을 어긋나게 둔다 — b 만 굴러야 a 의 카운터가 안 움직인다
    a.check_offset, b.check_offset = 0, 5
    a.spawn_rejections = 40                   # a 에만 빚을 쌓아 둔다
    before = a.spawn_rejections
    for _ in range(60):
        st.tick_count += 1
        if port_check_due(a.check_offset, st.tick_count):
            continue                          # a 가 굴 차례는 건너뛴다
        st._advance_trade()
    # ⚠ "둘이 다르다"만 보면 **둘 다 0 일 때도 통과한다.** a 가 굴지 않은 동안
    # a 의 값이 그대로였는지를 단언해야 공유 여부가 드러난다.
    assert a.spawn_rejections == before,         f"a 는 굴지도 않았는데 카운터가 {before} -> {a.spawn_rejections} 로 움직였다"
    # ⚠ `b != before` 로 두면 b 가 **한 번도 안 올라도** 통과한다(b 는 0, before 는 40).
    # b 자신의 출발값에서 실제로 올랐는지를 봐야 카운터가 도는 것이 드러난다.
    assert b.spawn_rejections > 0, "b 는 굴었는데 자기 카운터가 안 올랐다"


def _trade_bed(n_ports: int = 1, level: int = 1, width: int = 900,
               seed: int = 7, offsets: bool = False) -> GameState:
    """항구를 양쪽에 놓은 시험대. 서로 300 밖이라 debuff 를 안 받는다."""
    st = GameState(gmap=_sea(width=width), players={}, rng=random.Random(seed))
    st._counts, st._posts = {}, DefensePostIndex(st.gmap.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    for pid in range(2):
        st.players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False,
                                      start=st.gmap.ref(1 + pid * (width - 3), 1))
        st._counts[pid] = 1
    for i in range(n_ports):
        for pid, x in ((0, 2 + i), (1, width - 3 - i)):
            u = _port(st, pid, x, level=level)
            u.check_offset = (i if offsets else 0)
    return st


def _count_spawns(st: GameState, ticks: int) -> int:
    total = 0
    for _ in range(ticks):
        st.tick_count += 1
        before = len(st.trade_ships)
        st._advance_trade()
        total += max(0, len(st.trade_ships) - before)
        st.trade_ships.clear()             # 도착·수를 빼고 **스폰만** 센다
    return total


def test_port_level_multiplies_the_spawn_rolls():
    """`shouldSpawnTradeShip()` 은 **레벨 횟수만큼** 굴린다.

    ⚠ 항구를 전부 Lv1 로 두면 이 규칙을 지워도 결과가 같다 — §5.34 에서 사일로에
    똑같이 당했다(레벨 합과 개수가 같아 변이가 살아남았다). 그래서 Lv1 대조군과
    Lv4 를 함께 잰다."""
    lv1 = _count_spawns(_trade_bed(level=1), 600)
    lv4 = _count_spawns(_trade_bed(level=4), 600)
    assert lv4 > lv1, f"Lv4 인데 굴림 수가 안 늘었다 ({lv1} -> {lv4})"


def test_spawn_rolls_only_every_ten_ticks():
    """10 tick 게이트가 없으면 유통량이 그대로 10배가 된다.

    ⚠ "늘었다"만 보는 단언은 이 게이트를 지워도 통과한다(더 늘 뿐이다).
    **막지 않았으면 무엇이 일어났을 것인가**를 단언한다 — 오프셋을 전부 0으로
    맞춘 항구는 10 tick 에 한 번만 굴 수 있으므로 스폰 수가 tick 수의 1/10 을
    넘을 수 없다."""
    ticks = 500
    st = _trade_bed(n_ports=3, level=1)      # 오프셋 전부 0
    spawned = _count_spawns(st, ticks)
    assert 0 < spawned <= ticks // C.TRADE_SPAWN_CHECK_PERIOD, (
        f"{ticks} tick 에 {spawned}회 — 10 tick 게이트를 지나쳤다")


def test_destination_is_weighted_not_uniform():
    """목적지는 **가중 목록**에서 뽑는다 — 균등 무작위가 아니다.

    ⚠ 후보가 하나뿐이면 무엇을 뽑아도 같다. Lv1 하나와 Lv5 하나를 두고
    Lv5 쪽이 더 자주 뽑히는지를 본다."""
    gm = _sea(width=4000)
    src = gm.ref(0, 1)
    cands = [(gm.ref(1000, 1), 1, 1), (gm.ref(1001, 1), 2, 5)]
    w = trading_ports(gm, src, cands, friendly=set())
    lv5 = w.count((gm.ref(1001, 1), 2))
    lv1 = w.count((gm.ref(1000, 1), 1))
    assert lv5 == 5 * lv1, f"Lv5 가 Lv1 의 5배여야 한다 ({lv1} vs {lv5})"
    assert lv1 + lv5 > 2, "균등 목록이면 각각 1개뿐이다"


def test_engine_uses_the_weighted_list_for_destinations():
    """**배선** 검사 — `_spawn_trade_ship` 이 가중 목록을 실제로 쓰는가.

    ⚠ `trading_ports` 만 단위 테스트하면 엔진이 균등 무작위로 뽑도록 바뀌어도
    통과한다(로직과 배선을 따로 재야 한다). 상대 항구를 Lv1 하나와 Lv9 하나로
    두고, 어디로 떠났는지를 센다."""
    width = 2000
    st = GameState(gmap=_sea(width=width), players={}, rng=random.Random(3))
    st._counts, st._posts = {}, DefensePostIndex(st.gmap.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    for pid in range(3):
        st.players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False,
                                      start=st.gmap.ref(1 + pid, 1))
        st._counts[pid] = 1
    src = _port(st, 0, 2)
    lo = _port(st, 1, 1000, level=1)
    hi = _port(st, 2, 1001, level=9)
    ports = [(u.tile, u.owner, u.level, u) for u in (src, lo, hi)]

    hits = {lo.tile: 0, hi.tile: 0}
    for _ in range(300):
        st.trade_ships.clear()
        if st._spawn_trade_ship(src.tile, 0, ports):
            hits[st.trade_ships[0].dst_port] += 1
    assert hits[hi.tile] > hits[lo.tile] * 3, (
        f"Lv9 로 {hits[hi.tile]}회, Lv1 로 {hits[lo.tile]}회 — 균등으로 뽑고 있다")


def test_more_ports_means_more_trade():
    """항구를 더 지으면 유통량이 는다.

    ⚠ 판 전체에서 한 번만 굴리면 이 단언이 **깨진다** — 항구가 몇이든 같아진다.
    실측에서 항구 120곳에 무역선 도착이 9,000 tick 동안 29회뿐이었다."""
    def spawns(n_ports: int) -> int:
        st = GameState(gmap=_sea(width=200), players={}, rng=random.Random(7))
        st._counts, st._posts = {}, DefensePostIndex(st.gmap.size)
        st.tick_count = C.SPAWN_IMMUNITY_TICKS
        for pid in range(2):
            st.players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False,
                                          start=st.gmap.ref(1 + pid * 190, 1))
            st._counts[pid] = 1
        for i in range(n_ports):
            _port(st, 0, 2 + i)
            _port(st, 1, 190 - i)
        total = 0
        for _ in range(200):
            before = len(st.trade_ships)
            st._advance_trade()
            total += max(0, len(st.trade_ships) - before)
        return total
    few, many = spawns(1), spawns(8)
    assert many > few, f"항구를 8배로 늘렸는데 유통량이 {few} -> {many}"


# --- A* 전환 (§5.45) ----------------------------------------------------------

def test_water_path_is_still_shortest():
    """A* 로 바꿔도 **길이는 최단**이다(맨해튼 휴리스틱은 허용적이다).

    ⚠ 같은 길이의 경로가 여럿일 때 어느 것을 고르는지는 달라질 수 있다.
    그래서 경로 자체가 아니라 **길이**를 단언한다 — 장애물이 없는 바다에서는
    최단 길이가 맨해튼 거리와 같다."""
    gm = GameMap.from_rows(["." + "~" * 60 + "."] * 30)
    for (sx, sy), (dx, dy) in (((1, 1), (30, 1)), ((1, 1), (30, 20)),
                               ((5, 25), (55, 3))):
        src, dst = gm.ref(sx, sy), gm.ref(dx, dy)
        path = water_path(gm, src, dst)
        assert path is not None, f"({sx},{sy})->({dx},{dy}) 경로가 없다"
        assert len(path) == abs(dx - sx) + abs(dy - sy), \
            f"최단이 아니다 ({len(path)} vs {abs(dx-sx)+abs(dy-sy)})"


def test_water_path_still_goes_around_land():
    """장애물이 있으면 돌아간다 — 그리고 육지를 밟지 않는다."""
    rows = ["~" * 40 for _ in range(20)]
    for y in range(0, 15):                      # 위에서 내려오는 벽
        rows[y] = rows[y][:20] + "A" + rows[y][21:]
    gm = GameMap.from_rows(rows)
    src, dst = gm.ref(5, 5), gm.ref(35, 5)
    path = water_path(gm, src, dst)
    assert path is not None, "돌아갈 길이 있는데 못 찾았다"
    assert all(gm.terrain[t] == Terrain.OCEAN for t in path[:-1]), "육지를 밟았다"
    # (5,5) → 벽 끝(y=15) → x=35 → y=5 = 10 + 30 + 10
    assert len(path) == 50, f"최단이 아니다({len(path)})"


def test_water_path_matches_bfs_on_random_maps():
    """**BFS 를 기준으로 무작위 지도에서 대조한다.**

    ⚠ 손으로 만든 지도로는 A* 의 최단성을 못 잰다. 벽 하나짜리 지도에서는
    휴리스틱을 3배로 부풀려도 답이 같았다 — 돌아가는 길이 하나뿐이라
    어느 순서로 펼치든 같은 길이가 나온다. 실측으로 확인했다.

    장애물이 흩어진 지도라야 갈린다: 같은 조건 273판에서 정상은 불일치 0,
    휴리스틱을 3배로 부풀린 변이는 **68판**이 어긋났다."""
    def bfs_len(gm, src, dst):
        prev = {src: 0}
        q = deque([src])
        while q:
            cur = q.popleft()
            for n in gm.neighbors(cur):
                if n == dst:
                    return prev[cur] + 1
                if n in prev or gm.terrain[n] != Terrain.OCEAN:
                    continue
                prev[n] = prev[cur] + 1
                q.append(n)
        return None

    rng = random.Random(7)
    checked = 0
    # ⚠ 표본을 늘리면 **BFS 가 지도를 통째로 훑어** 테스트가 통째로 느려진다.
    # 40판 × 20×14 로도 변이를 잡는다(273판에서 68판이 어긋났으니 25%다 —
    # 40판이면 못 잡을 확률이 0.75^40 ≈ 1/100,000 이다).
    for _ in range(40):
        rows = ["".join("A" if rng.random() < 0.28 else "~" for _ in range(20))
                for _ in range(14)]
        gm = GameMap.from_rows(rows)
        sea = [t for t in range(gm.size) if gm.terrain[t] == Terrain.OCEAN]
        if len(sea) < 20:
            continue
        src, dst = rng.sample(sea, 2)
        want = bfs_len(gm, src, dst)
        if want is None:
            continue
        got = water_path(gm, src, dst)
        assert got is not None, "BFS 는 찾았는데 A* 가 못 찾았다"
        assert len(got) == want, f"최단이 아니다({len(got)} vs {want})"
        checked += 1
    assert checked > 20, f"{checked}판만 재졌다 — 재료가 약하다"


def test_water_path_still_rejects_unreachable():
    """이어지지 않은 바다는 여전히 None 이다(연결성분 검사)."""
    gm = GameMap.from_rows(["~~AA~~"] * 10)
    assert water_path(gm, gm.ref(0, 5), gm.ref(5, 5)) is None
