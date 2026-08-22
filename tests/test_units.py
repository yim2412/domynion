"""P2 — 골드·유닛 비용·건설·방어초소.

비용 공식은 **테스트 안에서 다시 계산하지 않고** 원본 표를 그대로 적는다. 여기서만은
하드코딩이 맞다 — 원본 숫자와 대조하는 것이 목적이기 때문이다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex, can_place_structure, euclid_sq
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState
from domynion.core.units import UNIT_INFO, Unit, UnitStore, UnitType


def state(rows: list[str], owners: dict[int, tuple[int, int]]) -> GameState:
    """⚠ 상대를 하나 이상 둘 것. 혼자면 첫 tick 에 정복 승리로 판이 끝나고
    `tick()` 이 곧바로 반환해 건설이 진행되지 않는다 (실제로 세 번 당했다)."""
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


# --- 비용 -------------------------------------------------------------------

def test_city_cost_doubles_then_caps():
    """`min(1e6, 2^n × 125000)` — 4채째부터 상한."""
    s = UnitStore()
    assert [s.cost(UnitType.CITY, extra=n) for n in range(6)] == [
        125_000, 250_000, 500_000, 1_000_000, 1_000_000, 1_000_000]


def test_defense_post_cost_is_linear_and_caps():
    """`min(250000, (n+1) × 50000)`."""
    s = UnitStore()
    assert [s.cost(UnitType.DEFENSE_POST, extra=n) for n in range(7)] == [
        50_000, 100_000, 150_000, 200_000, 250_000, 250_000, 250_000]


def test_port_and_factory_share_a_cost_counter():
    """`costWrapper(fn, Port, Factory)` — 둘을 섞어 지어도 값이 같이 오른다.

    따로 세면 원본보다 싸진다: 항구1+공장1 이 각각 125000 이 되어 버린다."""
    s = UnitStore()
    s.units.append(Unit(UnitType.PORT, 0, tile=0))
    s.record_constructed(UnitType.PORT)  # 원본은 건설 시작 시점에 올린다
    assert s.cost(UnitType.FACTORY) == 250_000, "항구를 지었으면 공장도 비싸져야 한다"
    assert s.cost(UnitType.PORT) == 250_000


def test_cost_rises_the_moment_construction_starts():
    """원본은 `buildUnit()` 안에서, **건설이 끝나기 전에** 완공 카운터를 올린다.

    막지 않았으면: 짓는 동안 같은 건물을 원본보다 싸게 연달아 지을 수 있다."""
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    p = st.players[0]
    p.gold = 10_000_000
    st.build(0, UnitType.CITY, st.gmap.ref(0, 0))
    assert p.units.units[0].under_construction
    assert p.units.cost(UnitType.CITY) == 250_000


def test_cost_uses_min_of_owned_and_constructed():
    """`min(보유, 완공)` — 유닛을 잃으면(보유 감소) 값이 도로 싸진다."""
    s = UnitStore()
    s.record_constructed(UnitType.CITY)
    s.record_constructed(UnitType.CITY)
    assert s.cost(UnitType.CITY) == 125_000, "보유 0 이면 완공수와 무관하게 첫 값"
    s.units.append(Unit(UnitType.CITY, 0, tile=0))
    assert s.cost(UnitType.CITY) == 250_000


def test_nuke_costs_match_original():
    s = UnitStore()
    assert s.cost(UnitType.ATOM_BOMB) == 750_000
    assert s.cost(UnitType.HYDROGEN_BOMB) == 5_000_000
    assert s.cost(UnitType.SAM_LAUNCHER) == 1_500_000
    assert s.cost(UnitType.MISSILE_SILO) == 1_000_000
    assert s.cost(UnitType.WARSHIP) == 250_000
    assert UNIT_INFO[UnitType.WARSHIP].max_health == 1000


# --- 골드 -------------------------------------------------------------------

def test_gold_accrues_per_tick_not_per_second():
    """`goldAdditionRate()` 는 tick 당이다. 초당으로 바꾸면 10배 느려진다."""
    st = state(["....", "...."], {0: (0, 0), 1: (3, 1)})
    st.players[0].is_bot = False
    st.players[1].is_bot = True
    before = st.players[0].gold
    st.tick()
    assert st.players[0].gold - before == C.GOLD_PER_TICK_HUMAN
    assert st.players[1].gold == C.GOLD_PER_TICK_BOT


# --- 건설 -------------------------------------------------------------------

def test_build_requires_gold_and_own_land():
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    p = st.players[0]
    assert st.build(0, UnitType.CITY, st.gmap.ref(0, 0)) is None, "골드가 없다"
    p.gold = 1_000_000
    assert st.build(0, UnitType.CITY, st.gmap.ref(0, 0)) is not None
    assert p.gold == 1_000_000 - 125_000


def test_cannot_build_on_someone_elses_land():
    """`validStructureSpawnTiles` 는 **내 소유 칸만** 후보로 낸다.

    주의: `build(near=적_타일)` 이 None 이 되는 것은 아니다 — 원본도 목표 근처에서
    내 땅을 찾아 거기 짓는다. 규칙은 '그 칸에 지을 수 있는가' 쪽에 있다."""
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    enemy = st.gmap.ref(39, 19)
    assert not can_place_structure(st.gmap, enemy, 0, [])
    assert can_place_structure(st.gmap, st.gmap.ref(0, 0), 0, [])


def test_structures_keep_minimum_distance():
    """`structureMinDist()` = 15. 붙여 지으면 도시를 한 칸에 몰아 지을 수 있다."""
    gm = GameMap.from_rows(["." * 60] * 40)
    gm.owner[:] = 0
    first = gm.ref(30, 20)
    assert can_place_structure(gm, first, 0, [])
    near = gm.ref(30 + C.STRUCTURE_MIN_DIST - 1, 20)
    far = gm.ref(30 + C.STRUCTURE_MIN_DIST, 20)
    assert not can_place_structure(gm, near, 0, [first])
    assert can_place_structure(gm, far, 0, [first])
    assert euclid_sq(gm, first, far) == C.STRUCTURE_MIN_DIST ** 2


def test_construction_takes_time_before_the_city_counts():
    """건설 중에는 병력 상한이 안 오른다 — 상한 공식이 `!under_construction` 을 본다.

    (완공 카운터는 다른 이야기다. 그건 건설 시작 시점에 오른다 — 위 테스트 참고.)"""
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    p = st.players[0]
    p.gold = 1_000_000
    base = p.max_troops(100)
    u = st.build(0, UnitType.CITY, st.gmap.ref(0, 0))
    assert u.under_construction
    assert p.max_troops(100) == base, "짓는 중인데 상한이 올랐다"
    for _ in range(UNIT_INFO[UnitType.CITY].construction_ticks):
        st.tick()
    assert not u.under_construction
    assert p.max_troops(100) == base + C.CITY_TROOP_INCREASE


def test_upgrade_raises_level_and_next_price():
    """업그레이드 값은 **올릴수록 뛴다.** 250,000 → 500,000 → 1,000,000(상한).

    ⚠ 이 테스트는 오래 **틀린 값을 못 박고 있었다.** "도시 1채를 계속 올리면 값이
    그대로다 — min(보유1, 완공N) 이 1 에 묶인다. 직관과 다르지만 원본이 그렇다"고
    적혀 있었는데, `unitsOwned` 가 개수가 아니라 **레벨 합**이라 1 에 안 묶인다.
    아래 값은 원본 `Config.unitInfo(City).cost` 를 실제로 실행해 받은 것이다
    (2026-08-22, `tools/oracle.mts` 와 같은 방식).

    막지 않았으면: 도시 하나를 무한히 250,000 에 올려 병력 상한을 공짜로 늘린다."""
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    p = st.players[0]
    p.gold = 100_000_000
    u = st.build(0, UnitType.CITY, st.gmap.ref(0, 0))
    for _ in range(UNIT_INFO[UnitType.CITY].construction_ticks):
        st.tick()
    prices = []
    for _ in range(4):
        before = p.gold
        assert st.upgrade(0, u)
        prices.append(before - p.gold)
    assert u.level == 5
    assert prices == [250_000, 500_000, 1_000_000, 1_000_000]


def test_units_owned_counts_levels_not_units():
    """`unitsOwned` 는 레벨 합이다. 개수로 세면 위 곡선이 통째로 평평해진다."""
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    p = st.players[0]
    p.gold = 100_000_000
    u = st.build(0, UnitType.CITY, st.gmap.ref(0, 0))
    for _ in range(UNIT_INFO[UnitType.CITY].construction_ticks):
        st.tick()
    assert p.units.owned(UnitType.CITY) == 1
    assert p.units.num(UnitType.CITY) == 1
    st.upgrade(0, u)
    assert p.units.owned(UnitType.CITY) == 2, "레벨 합이어야 한다"
    assert p.units.num(UnitType.CITY) == 1, "실제 개수는 그대로 하나다"


def test_a_building_under_construction_counts_as_one():
    """건설 중인 것은 레벨이 아니라 1 로 센다(`if isUnderConstruction() total++`)."""
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    p = st.players[0]
    p.gold = 100_000_000
    u = st.build(0, UnitType.CITY, st.gmap.ref(0, 0))
    assert u.under_construction
    assert p.units.owned(UnitType.CITY) == 1


def test_upgrade_refuses_non_upgradable():
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    p = st.players[0]
    p.gold = 10_000_000
    u = st.build(0, UnitType.DEFENSE_POST, st.gmap.ref(0, 0))
    for _ in range(UNIT_INFO[UnitType.DEFENSE_POST].construction_ticks):
        st.tick()
    assert not st.upgrade(0, u), "방어초소는 업그레이드 대상이 아니다"


# --- 방어초소 ---------------------------------------------------------------

def test_defense_post_makes_tiles_much_harder():
    """사거리 30 안이면 방어 ×5, 속도 ×3 — 원본에서 가장 큰 단일 수정자다.

    막지 않았으면: 초소를 지어도 아무 일도 안 일어난다."""
    from domynion.core.attack import attack_logic
    gm = GameMap.from_rows(["." * 80] * 40)
    atk = PlayerState(pid=0, name="A", is_bot=False, troops=100_000.0)
    dfn = PlayerState(pid=1, name="D", is_bot=False, troops=100_000.0)
    t = gm.ref(40, 20)
    bare = attack_logic(gm, t, 20_000.0, atk, dfn, 500, 500, defense_post=False)
    guarded = attack_logic(gm, t, 20_000.0, atk, dfn, 500, 500, defense_post=True)
    assert guarded.attacker_loss > bare.attacker_loss * 4
    assert guarded.tiles_used == pytest.approx(bare.tiles_used * C.DEFENSE_POST_SPEED_BONUS)


def test_defense_post_index_covers_only_within_range():
    gm = GameMap.from_rows(["." * 120] * 80)
    idx = DefensePostIndex(gm.size)
    centre = gm.ref(60, 40)
    idx.rebuild(gm, [(centre, 1)])
    assert idx.covers(gm, centre, 1)
    assert idx.covers(gm, gm.ref(60 + C.DEFENSE_POST_RANGE - 1, 40), 1)
    assert not idx.covers(gm, gm.ref(60 + C.DEFENSE_POST_RANGE, 40), 1)
    assert not idx.covers(gm, centre, 0), "남의 초소는 나를 지켜 주지 않는다"


def test_absorbed_player_hands_over_buildings():
    """`conquerPlayer` — 흡수하면 건물도 넘어간다.

    막지 않았으면: 도시가 사라져 정복자의 병력 상한이 오히려 안 오른다."""
    st = state(["." * 40] * 20, {0: (0, 0), 1: (39, 19)})
    loser = st.players[1]
    loser.units.units.append(Unit(UnitType.CITY, 1, tile=st.gmap.ref(39, 19), level=2))
    loser.units.record_constructed(UnitType.CITY)
    st._counts = {0: 1, 1: 1}
    st._maybe_absorb(0, 1)
    assert st.players[0].units.city_levels() == 2
    assert loser.units.units == []
