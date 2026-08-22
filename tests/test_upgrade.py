"""건물 업그레이드 — 원본은 **건설 버튼이 곧 업그레이드 버튼**이다.

원본 `PlayerImpl.findUnitToUpgrade` / `BuildMenu.sendBuildOrUpgrade` /
`UpgradeStructureExecution`.

엔진에 `upgrade()` 는 있었는데 **사람이 부를 길이 없었다.** 그리고 길을 내려고
원본을 열어 보니, 원본에는 "업그레이드 버튼"이라는 게 따로 없었다 — 건설 메뉴의
같은 항목이, 같은 종류가 `structureMinDist`(15) 안에 이미 있으면 업그레이드가 된다.
우리는 그 자리에서 "지을 자리가 없다"고 거절하고 있었으므로, **사람은 도시를 두
채째부터 아예 못 늘렸다.**

`canUpgrade` 가 `canBuild` 보다 우선한다(`if (buildableUnit.canUpgrade !== false)`).
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from domynion.core import constants as C                      # noqa: E402
from domynion.core.buildings import DefensePostIndex          # noqa: E402
from domynion.core.engine import GameState                    # noqa: E402
from domynion.core.gamemap import GameMap                     # noqa: E402
from domynion.core.state import PlayerState                   # noqa: E402
from domynion.core.units import UNIT_INFO, UnitType           # noqa: E402
from domynion.ui.actions import build_items                   # noqa: E402


def state() -> GameState:
    gm = GameMap.from_rows(["." * 80] * 40)
    players = {}
    for pid in (0, 1):
        for x in range(pid * 40, pid * 40 + 40):
            for y in range(40):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 40, 0),
                        kind="human" if pid == 0 else "nation")
        p.gold = 100_000_000
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 1600, 1: 1600}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def a_city(st: GameState, x=10, y=10, pid=0):
    u = st.build(pid, UnitType.CITY, st.gmap.ref(x, y))
    assert u is not None
    while u.under_construction:
        st.tick()
    return u


def noop(_msg):
    pass


def city_item(st, tile):
    return next(i for i in build_items(st, 0, tile, noop)
                if i.label.startswith("도시"))


# --- 값 ---------------------------------------------------------------------

def test_the_price_curve_matches_the_original():
    """원본 `Config.unitInfo(City).cost` 를 실제로 실행해 받은 값이다(2026-08-22).

    막지 않았으면: 도시 하나를 250,000 씩 무한히 올려 병력 상한을 공짜로 늘린다."""
    st = state()
    u = a_city(st)
    p = st.players[0]
    prices = []
    for _ in range(4):
        before = p.gold
        assert st.upgrade(0, u)
        prices.append(before - p.gold)
    assert prices == [250_000, 500_000, 1_000_000, 1_000_000]


def test_upgrading_raises_the_troop_cap():
    """레벨을 올리는 이유가 이것이다 — 안 오르면 업그레이드가 무의미하다."""
    st = state()
    u = a_city(st)
    p = st.players[0]
    before = p.max_troops(st.tiles(0))
    st.upgrade(0, u)
    assert p.max_troops(st.tiles(0)) == before + C.CITY_TROOP_INCREASE


def test_a_level_three_city_equals_three_level_one_cities():
    """`unitsOwned` 가 레벨 합이라는 것의 관찰 가능한 결과."""
    st = state()
    u = a_city(st)
    st.upgrade(0, u)
    st.upgrade(0, u)
    assert st.players[0].units.owned(UnitType.CITY) == 3
    assert st.players[0].units.city_levels() == 3


# --- 관문 -------------------------------------------------------------------

def test_you_cannot_upgrade_a_building_under_construction():
    st = state()
    u = st.build(0, UnitType.CITY, st.gmap.ref(10, 10))
    assert u is not None and u.under_construction
    assert st.upgrade(0, u) is False


def test_you_cannot_upgrade_someone_elses_building():
    st = state()
    u = a_city(st, x=50, y=10, pid=1)
    assert st.upgrade(0, u) is False, "남의 건물을 내 골드로 올렸다"


def test_you_cannot_upgrade_a_defense_post():
    st = state()
    u = st.build(0, UnitType.DEFENSE_POST, st.gmap.ref(10, 10))
    assert u is not None
    while u.under_construction:
        st.tick()
    assert st.upgrade(0, u) is False


def test_you_cannot_upgrade_without_the_gold():
    st = state()
    u = a_city(st)
    st.players[0].gold = 249_999
    assert st.upgrade(0, u) is False


def test_you_cannot_upgrade_during_the_spawn_phase():
    st = state()
    u = a_city(st)
    st.spawn_phase = True
    assert st.upgrade(0, u) is False


# --- 찾기 -------------------------------------------------------------------

def test_it_finds_the_city_within_the_min_distance():
    st = state()
    u = a_city(st, x=10, y=10)
    near = st.gmap.ref(10 + C.STRUCTURE_MIN_DIST - 1, 10)
    assert st.find_upgrade(0, UnitType.CITY, near) is u


def test_it_ignores_a_city_beyond_the_min_distance():
    """15칸 밖이면 업그레이드가 아니라 **새로 짓는 것**이 원본의 행동이다."""
    st = state()
    a_city(st, x=10, y=10)
    far = st.gmap.ref(10 + C.STRUCTURE_MIN_DIST, 10)
    assert st.find_upgrade(0, UnitType.CITY, far) is None


def test_it_picks_the_closest_one():
    """`findClosestBy(nearbyUnits(...), distSquared)` — 가장 가까운 것 하나다.

    ⚠ **두 도시가 모두 사거리 안에 있어야 이 규칙을 잰다.** 처음엔 (10,10) 과
    (30,10) 에 짓고 (31,10) 을 찍었는데, 앞의 것이 21칸이라 애초에 후보가 아니었다 —
    "가장 가까운 게 아니라 아무거나 고른다"는 변이가 그대로 통과했다. 건물 최소
    거리가 15 라 **정확히 15 떨어뜨려** 둘 다 후보가 되게 만든다."""
    st = state()
    a = a_city(st, x=10, y=10)
    b = a_city(st, x=10 + C.STRUCTURE_MIN_DIST, y=10)
    assert a.tile == st.gmap.ref(10, 10) and b.tile == st.gmap.ref(25, 10)
    probe = st.gmap.ref(14, 10)          # a 까지 4칸, b 까지 11칸 — 둘 다 후보다
    assert st.find_upgrade(0, UnitType.CITY, probe) is a
    probe = st.gmap.ref(21, 10)          # 이번엔 b 가 더 가깝다
    assert st.find_upgrade(0, UnitType.CITY, probe) is b


def test_it_ignores_a_building_marked_for_deletion():
    """지울 것에 돈을 더 넣지 않는다(`isUnitValidToUpgrade`)."""
    st = state()
    u = a_city(st)
    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS
    assert st.delete_unit(0, u)
    assert st.find_upgrade(0, UnitType.CITY, st.gmap.ref(10, 10)) is None


# --- 메뉴 -------------------------------------------------------------------

def test_the_build_item_becomes_an_upgrade():
    """막지 않았으면: 두 채째부터 "지을 자리가 없다"만 나와 늘릴 방법이 없다."""
    st = state()
    a_city(st, x=10, y=10)
    item = city_item(st, st.gmap.ref(11, 11))
    assert item.enabled, "가까이 찍었더니 회색이 됐다 — 원본은 업그레이드가 된다"
    assert "▲Lv2" in item.label


def test_far_away_it_is_still_a_build():
    st = state()
    a_city(st, x=10, y=10)
    item = city_item(st, st.gmap.ref(40 - 1, 30))
    assert item.enabled and "▲" not in item.label


def test_the_menu_item_actually_upgrades():
    st = state()
    u = a_city(st, x=10, y=10)
    city_item(st, st.gmap.ref(11, 11)).action()
    assert u.level == 2


def test_the_count_beside_the_name_is_the_level_sum():
    """원본 `count()` = `totalUnitLevels`. 개수로 보이면 Lv3 한 채가 '1' 이 된다."""
    st = state()
    u = a_city(st, x=10, y=10)
    st.upgrade(0, u)
    assert city_item(st, st.gmap.ref(11, 11)).label.startswith("도시·2")
