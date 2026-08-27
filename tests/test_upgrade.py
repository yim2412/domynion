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
from domynion.ui.actions import attack_items, build_items     # noqa: E402


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
    assert st.upgrade(0, u) == 0


def test_you_cannot_upgrade_someone_elses_building():
    st = state()
    u = a_city(st, x=50, y=10, pid=1)
    assert st.upgrade(0, u) == 0, "남의 건물을 내 골드로 올렸다"


def test_you_cannot_upgrade_a_defense_post():
    st = state()
    u = st.build(0, UnitType.DEFENSE_POST, st.gmap.ref(10, 10))
    assert u is not None
    while u.under_construction:
        st.tick()
    assert st.upgrade(0, u) == 0


def test_you_cannot_upgrade_without_the_gold():
    st = state()
    u = a_city(st)
    st.players[0].gold = 249_999
    assert st.upgrade(0, u) == 0


def test_you_cannot_upgrade_during_the_spawn_phase():
    st = state()
    u = a_city(st)
    st.spawn_phase = True
    assert st.upgrade(0, u) == 0


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
    """골드가 한 레벨치뿐이면 항목이 **바로** 올린다(하위 메뉴 없이).

    원본도 `maxAmount <= 1` 이면 하위 메뉴를 만들지 않고 클릭이 ×1 로 떨어진다."""
    st = state()
    u = a_city(st, x=10, y=10)
    st.players[0].gold = st.players[0].units.cost(UnitType.CITY)
    item = city_item(st, st.gmap.ref(11, 11))
    assert item.submenu is None, "한 레벨뿐인데 하위 메뉴를 열었다"
    item.action()
    assert u.level == 2


def test_the_menu_opens_a_bulk_submenu_when_you_can_afford_more():
    """여러 레벨을 살 수 있으면 **하위 메뉴**가 열린다 — 바로 올리지 않는다.

    ⚠ 이게 동작 변화다. 예전에는 클릭이 곧 1레벨이었다."""
    st = state()
    a_city(st, x=10, y=10)
    item = city_item(st, st.gmap.ref(11, 11))
    assert item.submenu is not None
    assert item.action is None, "하위 메뉴가 있는데 클릭이 바로 올려 버린다"
    assert "최대 ×" in item.hint


def test_the_bulk_slots_are_always_in_the_same_place():
    """원본은 네 칸을 늘 같은 자리에 둔다 — [1, 5, 10, 최대]. 이유는 muscle memory.

    **살 수 없는 칸도 숨기지 않는다.** 회색으로 남아야 "왜 못 하지"가 보인다."""
    st = state()
    a_city(st, x=10, y=10)
    st.players[0].gold = st.players[0].units.bulk_cost(UnitType.CITY, 2)
    sub = city_item(st, st.gmap.ref(11, 11)).submenu()
    assert [i.label for i in sub] == ["×1", "×5", "×10", "×2"], [i.label for i in sub]
    assert [i.enabled for i in sub] == [True, False, False, True]


def test_the_bulk_slots_stay_four_even_when_the_max_duplicates_a_step():
    """`최대` 가 5나 10과 같아도 **칸을 지우지 않는다** — 원본이 그렇다.

    `const slots = [1, ...steps, maxAmount]` 에 중복 제거가 없다. 칸 수가 줄면
    자리가 밀려서 "늘 같은 자리"가 깨지는데, 그게 이 배치의 유일한 목적이다."""
    st = state()
    a_city(st, x=10, y=10)
    st.players[0].gold = st.players[0].units.bulk_cost(UnitType.CITY, 5)
    sub = city_item(st, st.gmap.ref(11, 11)).submenu()
    assert [i.label for i in sub] == ["×1", "×5", "×10", "×5"], [i.label for i in sub]


def test_a_bulk_slot_upgrades_that_many_levels():
    """×5 칸이 실제로 5레벨을 올리고 누적값만큼 결제하는가."""
    st = state()
    u = a_city(st, x=10, y=10)
    p = st.players[0]
    want = p.units.bulk_cost(UnitType.CITY, 5)
    slot = next(i for i in city_item(st, st.gmap.ref(11, 11)).submenu()
                if i.label == "×5")
    before = p.gold
    slot.action()
    assert u.level == 6
    assert before - p.gold == want


def test_bulk_charges_the_escalating_total_not_a_flat_multiple():
    """**`cost × 수량` 이 아니다.** 레벨이 오를 때마다 다음 값이 오른다.

    원본 주석: "upgrade costs escalate per level, so a bulk total is NOT
    cost * amount". 선형으로 매기면 3레벨을 2.3배 싸게 파는 셈이 된다."""
    st = state()
    u = a_city(st, x=10, y=10)
    p = st.players[0]
    flat = p.units.cost(UnitType.CITY) * 3
    want = p.units.bulk_cost(UnitType.CITY, 3)
    assert want > flat, (want, flat)

    before = p.gold
    assert st.upgrade(0, u, 3) == 3
    assert before - p.gold == want
    assert u.level == 4


def test_bulk_stops_when_the_gold_runs_out():
    """중간에 골드가 떨어지면 **거기까지만 오르고 멈춘다** — 원본 실행부 그대로.

    막지 않았으면: 값을 미리 합산해 한 번에 빼는 구현은 골드를 음수로 만들거나
    아무것도 안 올린다. 둘 다 원본과 다르다."""
    st = state()
    u = a_city(st, x=10, y=10)
    p = st.players[0]
    p.gold = p.units.bulk_cost(UnitType.CITY, 2)
    assert st.upgrade(0, u, 10) == 2, "요청한 만큼 다 올라 버렸다"
    assert u.level == 3
    assert p.gold == 0


def test_max_bulk_upgrade_is_capped_at_fifty():
    """`MAX_UPGRADE_AMOUNT` = 50. 골드가 아무리 많아도 한 번에 50레벨까지다."""
    st = state()
    u = a_city(st, x=10, y=10)
    st.players[0].gold = 10 ** 12
    assert C.MAX_UPGRADE_AMOUNT == 50
    assert st.max_bulk_upgrade(0, u) == 50


def test_max_bulk_upgrade_is_zero_when_you_cannot_upgrade_at_all():
    st = state()
    u = a_city(st, x=10, y=10)
    st.players[0].gold = 0
    assert st.max_bulk_upgrade(0, u) == 0


def test_the_count_beside_the_name_is_the_level_sum():
    """원본 `count()` = `totalUnitLevels`. 개수로 보이면 Lv3 한 채가 '1' 이 된다."""
    st = state()
    u = a_city(st, x=10, y=10)
    st.upgrade(0, u)
    assert city_item(st, st.gmap.ref(11, 11)).label.startswith("도시·2")


# --- 핵 대량 구매 (§5.49) ----------------------------------------------------

def a_silo(st: GameState, x=10, y=10, pid=0, level=1):
    u = st.build(pid, UnitType.MISSILE_SILO, st.gmap.ref(x, y))
    assert u is not None
    while u.under_construction:
        st.tick()
    for _ in range(level - 1):
        st.upgrade(pid, u, 1)
    # 발사관을 전부 비운다 — 방금 지은 사일로는 재장전 중일 수 있다
    for _ in range(C.SILO_COOLDOWN_TICKS + 2):
        st.tick()
    return u


def nuke_item(st, tile, name="원폭"):
    return next(i for i in attack_items(st, 0, tile, noop) if i.label == name)


def test_only_atom_bombs_can_be_bought_in_bulk():
    """원본은 겹쳐 사는 것을 **원자탄에만** 연다(`isStackableNuke`)."""
    st = state()
    a_silo(st, level=5)
    tile = st.gmap.ref(60, 20)                    # 상대 땅
    assert nuke_item(st, tile, "원폭").submenu is not None
    for name in ("수폭", "MIRV"):
        assert nuke_item(st, tile, name).submenu is None, f"{name} 이 겹쳐 산다"


def test_nuke_bulk_slots_use_2_and_5():
    """핵의 고정 단계는 **2·5** 다(건물은 5·10). 자리는 늘 넷이다."""
    st = state()
    a_silo(st, level=5)
    st.players[0].gold = st.players[0].units.bulk_cost(UnitType.ATOM_BOMB, 3)
    sub = nuke_item(st, st.gmap.ref(60, 20)).submenu()
    assert [i.label for i in sub] == ["×1", "×2", "×5", "×3"], [i.label for i in sub]
    assert [i.enabled for i in sub] == [True, True, False, True]


def test_ready_tubes_cap_the_bulk_amount():
    """상한은 골드만이 아니다 — **발사관 수**가 같이 자른다(§5.34).

    막지 않았으면: 골드만 있으면 Lv1 사일로 하나로 50발을 한 번에 지른다."""
    st = state()
    silo = a_silo(st, level=1)                    # 관 한 개
    st.players[0].gold = 100_000_000              # 골드는 넘친다
    assert st.max_bulk_nuke(0, UnitType.ATOM_BOMB) == 1
    assert nuke_item(st, st.gmap.ref(60, 20)).submenu is None,         "관이 하나인데 하위 메뉴를 열었다"

    # 대조군 — 레벨을 올리면 관이 늘어 그만큼 열린다.
    # ⚠ 새로 생긴 관은 **재장전부터 시작한다**(§5.34). 게다가 사일로는 tick 당
    # 맨 앞 관 하나만 비우므로(원본이 `if`) 네 관을 비우는 데 네 tick 이 더 든다 —
    # `+2` 로 뒀다가 4 를 받고 규칙이 틀린 줄 알았다.
    st.upgrade(0, silo, 4)
    for _ in range(C.SILO_COOLDOWN_TICKS + 6):
        st.tick()
    assert st.max_bulk_nuke(0, UnitType.ATOM_BOMB) == 5


def test_a_bulk_slot_launches_that_many_nukes():
    """×2 칸이 실제로 두 발을 쏘고 두 발치를 결제한다."""
    st = state()
    a_silo(st, level=5)
    p = st.players[0]
    p.gold = p.units.bulk_cost(UnitType.ATOM_BOMB, 2)
    tile = st.gmap.ref(60, 20)
    slot = next(i for i in nuke_item(st, tile).submenu() if i.label == "×2")
    slot.action()
    assert len(st.nukes) == 2, [n.utype for n in st.nukes]
    assert p.gold == 0


def test_a_bulk_launch_stops_when_the_tubes_run_out():
    """요청보다 적게 나가면 **그 사실을 말해 준다.** 조용히 삼키면 안 된다."""
    st = state()
    a_silo(st, level=2)
    p = st.players[0]
    p.gold = 100_000_000
    said = []
    tile = st.gmap.ref(60, 20)
    item = next(i for i in attack_items(st, 0, tile, said.append)
                if i.label == "원폭")
    # 관이 둘뿐인데 ×5 를 누른다 — 하위 메뉴는 골드 기준으로 열려 있다
    slot = next(i for i in item.submenu() if i.label == "×5")
    slot.action()
    assert len(st.nukes) == 2
    assert said and "5발 중 2발" in said[-1], said
