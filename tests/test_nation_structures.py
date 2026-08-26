"""`ai/structures.py` — 원본 `NationStructureBehavior` 대조.

**이 파일이 생긴 이유는 AI 가 업그레이드를 아예 안 하고 있었다는 것이다.**
그래서 여기서 가장 중요한 것은 비율 표가 아니라 `_maybe_spawn` 의 밀도 분기다 —
"빽빽하면 새로 짓지 말고 올린다", 그리고 "못 올렸으면 짓지도 않는다".

⚠ 이 파일의 테스트는 **일부러 깨뜨려서** 실패하는지 확인했다(2026-08-24).
변이 목록은 파일 끝 주석에 있다.
"""

from __future__ import annotations

import math
import random

import pytest

from domynion.ai.structures import (
    CITY_PERCEIVED_COST_INCREASE_PER_OWNED,
    DEFENSE_POST_RATIO_PER_POST,
    FACTORY_COASTAL_RATIO_MULTIPLIER,
    FIRST_MISSILE_SILO_RATIO,
    MAX_MISSILE_SILOS,
    RANDOM_UPGRADE_CHANCE,
    SAM_RATIO_BY_DIFFICULTY,
    STRUCTURE_RATIOS,
    UNDER_ATTACK_THREAT_RATIO,
    UPGRADE_DENSITY_THRESHOLD,
    NationStructureBehavior,
    border_spacing,
)
from domynion.core.attack import Attack
from domynion.core.engine import GameState
from domynion.core.units import Unit, UnitType


# --- 도구 -------------------------------------------------------------------

def make_state(rows: list[str] | None = None, players: int = 2) -> GameState:
    """작은 손지도로 상태를 만든다. 넓은 바다를 둬서 해안이 실제로 생기게 한다."""
    from domynion.core.gamemap import GameMap

    if rows is None:
        # 200×200. 왼쪽 160칸이 육지(32,000칸), 오른쪽이 바다.
        #
        # ⚠ **작게 만들면 안 된다.** 20×12(육지 120칸)로 두었더니 두 가지가 조용히
        # 어긋났다: (1) 나라 2명/육지 120칸이 `HIGH_NATION_DENSITY_THRESHOLD`
        # (1/7500)를 넘어 첫 건물이 항상 항구가 됐고, (2) 영토가 10×10 이라
        # 모든 칸이 첫 건물에서 `structure_min_dist`(15) 안이라 두 번째 건물을
        # 지을 자리가 아예 없었다. 둘 다 **지도가 만든 결과**지 규칙이 아니다.
        rows = ["." * 160 + "~" * 40 for _ in range(200)]
    gmap = GameMap.from_rows(rows)
    rng = random.Random(0)
    st = GameState.__new__(GameState)
    # `GameState.new` 는 스폰까지 하므로, 지도만 갈아 끼운 빈 상태를 직접 만든다.
    st.__init__(gmap=gmap, players={}, rng=rng)
    from domynion.core.buildings import DefensePostIndex
    from domynion.core.nukes import Fallout
    from domynion.core.state import PlayerState

    for pid in range(players):
        st.players[pid] = PlayerState(pid=pid, name=f"p{pid}", kind="nation",
                                      start=0)
    st._posts = DefensePostIndex(gmap.size)
    st.fallout = Fallout(gmap.size)
    st._counts = {}
    return st


def give_land(st: GameState, pid: int, tiles: list[int]) -> None:
    for t in tiles:
        st.gmap.owner[t] = pid
    st._counts[pid] = st._counts.get(pid, 0) + len(tiles)


def land_of(st: GameState, count: int, start: int = 0) -> list[int]:
    """앞에서부터 통행 가능한 칸 `count` 개."""
    out = []
    t = start
    while len(out) < count and t < st.gmap.size:
        if st.gmap.passable(t):
            out.append(t)
        t += 1
    return out


def behavior(pid: int = 0, difficulty: str = "medium",
             seed: int = 0) -> NationStructureBehavior:
    return NationStructureBehavior(pid, random.Random(seed), difficulty)


def add_unit(st: GameState, pid: int, utype: UnitType, tile: int,
             level: int = 1) -> Unit:
    u = Unit(utype=utype, owner=pid, tile=tile, level=level)
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(utype)
    return u


# --- 비율 -------------------------------------------------------------------

def test_ratios_match_original_values():
    """원본 `getStructureRatios` 의 값 그대로.

    ⚠ 이름만 옮기고 값을 안 본 것이 이 프로젝트에서 이미 30배 틀린 적이 있다
    (철거 쿨다운). 그래서 값을 못 박는다."""
    assert STRUCTURE_RATIOS[UnitType.PORT] == (0.75, 1.0)
    assert STRUCTURE_RATIOS[UnitType.FACTORY] == (0.75, 1.0)
    assert STRUCTURE_RATIOS[UnitType.MISSILE_SILO] == (0.2, 1.0)
    assert STRUCTURE_RATIOS[UnitType.SAM_LAUNCHER][1] == 0.3
    assert SAM_RATIO_BY_DIFFICULTY == {
        "easy": 0.15, "medium": 0.2, "hard": 0.25, "impossible": 0.3}
    assert FACTORY_COASTAL_RATIO_MULTIPLIER == 0.33
    assert FIRST_MISSILE_SILO_RATIO == 0.4
    assert MAX_MISSILE_SILOS == 3
    assert UPGRADE_DENSITY_THRESHOLD == 1 / 1500
    assert CITY_PERCEIVED_COST_INCREASE_PER_OWNED == 1.0


@pytest.mark.parametrize("cities,want_ports", [(0, 0), (1, 0), (2, 1), (4, 3), (8, 6)])
def test_port_target_is_three_quarters_of_cities(cities, want_ports):
    """항구 목표 = floor(도시 × 0.75). 도시 4채면 3개, 1채면 **0개**다.

    도시 1채에서 0 이라는 것이 중요하다 — 원본이 도시를 우선하는 방식이 바로
    이 내림이다. 비율만 보고 `round` 로 옮기면 첫 도시에서 바로 항구가 선다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    for i in range(cities):
        add_unit(st, 0, UnitType.CITY, i)

    built = 0
    while b._should_build(st, UnitType.PORT, cities, has_coastal=True):
        add_unit(st, 0, UnitType.PORT, 500 + built)
        built += 1
        assert built <= 20, "무한 루프 — 목표치가 안 걸린다"
    assert built == want_ports


def test_factory_ratio_is_cut_by_a_third_on_the_coast():
    """해안이 있으면 공장 비율이 0.75 → 0.2475 로 줄어든다.

    도시 4채: 해안이 없으면 3개(floor(4×0.75)), 있으면 0개(floor(4×0.2475)=0).
    **바다가 있으면 항구가 낫다**는 원본 판단이다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    for i in range(4):
        add_unit(st, 0, UnitType.CITY, i)

    assert b._should_build(st, UnitType.FACTORY, 4, has_coastal=False) is True
    assert b._should_build(st, UnitType.FACTORY, 4, has_coastal=True) is False
    # 내륙이면 3채까지 간다
    n = 0
    while b._should_build(st, UnitType.FACTORY, 4, has_coastal=False):
        add_unit(st, 0, UnitType.FACTORY, 600 + n)
        n += 1
        assert n <= 20
    assert n == 3


def test_first_missile_silo_uses_the_higher_ratio():
    """첫 사일로만 0.4, 그다음부터 0.2.

    도시 3채: 첫 채는 floor(3×0.4)=1 로 통과하고, 한 채 지은 뒤에는
    floor(3×0.2)=0 이라 막힌다. 이 분기가 없으면 도시 5채까지 핵이 없다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    assert b._should_build(st, UnitType.MISSILE_SILO, 3, False) is True
    add_unit(st, 0, UnitType.MISSILE_SILO, 700)
    assert b._should_build(st, UnitType.MISSILE_SILO, 3, False) is False
    # 도시가 10채면 floor(10×0.2)=2 라 두 채까지
    assert b._should_build(st, UnitType.MISSILE_SILO, 10, False) is True


def test_missile_silos_are_hard_capped_at_three():
    """도시가 아무리 많아도 사일로는 3기까지. 상한을 **레벨 합**으로 센다.

    ⚠ **레벨 1짜리 3기로 재면 안 된다.** 그러면 레벨 합과 개수가 둘 다 3 이라
    `owned()` 를 `num()` 으로 바꿔도 테스트가 통과한다(실제로 그 변이가 살아남았다).
    한 기를 레벨 3 으로 두면 레벨 합 3 · 개수 1 로 갈라져 비교가 생긴다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    silo = add_unit(st, 0, UnitType.MISSILE_SILO, 700)
    silo.level = MAX_MISSILE_SILOS
    assert st.players[0].units.owned(UnitType.MISSILE_SILO) == 3
    assert st.players[0].units.num(UnitType.MISSILE_SILO) == 1
    assert b._should_build(st, UnitType.MISSILE_SILO, 1000, False) is False

    # 대조군 — 레벨 2 면 아직 상한이 아니다
    silo.level = 2
    assert b._should_build(st, UnitType.MISSILE_SILO, 1000, False) is True


def test_sam_ratio_follows_difficulty():
    """impossible 은 easy 의 두 배로 SAM 을 세운다. **배선을 재는 테스트다.**

    ⚠ 기본값(medium)이 아닌 값으로 잰다 — easy 와 impossible 을 비교하면
    `self.difficulty` 를 안 읽고 상수를 박아도 반드시 실패한다."""
    st = make_state()
    give_land(st, 0, land_of(st, 100))
    cities = 10
    got = {}
    for diff in ("easy", "medium", "hard", "impossible"):
        b = behavior(difficulty=diff)
        n = 0
        while b._should_build(st, UnitType.SAM_LAUNCHER, cities, False):
            add_unit(st, 0, UnitType.SAM_LAUNCHER, 800 + n)
            n += 1
            assert n <= 30
        got[diff] = n
        st.players[0].units.units = [
            u for u in st.players[0].units.units
            if u.utype is not UnitType.SAM_LAUNCHER]
        st.players[0].units._constructed.pop(UnitType.SAM_LAUNCHER, None)
    assert got == {"easy": 1, "medium": 2, "hard": 2, "impossible": 3}


def test_cities_are_never_gated_by_a_ratio():
    """도시는 비율 표에 없다 — `_should_build` 는 항상 False 를 돌려준다.

    도시가 어떻게 지어지는지는 `_place` 의 **마지막 줄**이다. 여기에 도시를
    끼워 넣으면 도시가 다른 건물과 경쟁하게 되고, 원본의 "도시 우선"이 깨진다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    assert b._should_build(st, UnitType.CITY, 100, True) is False
    assert b._should_build(st, UnitType.DEFENSE_POST, 100, True) is False


# --- 체감 비용 --------------------------------------------------------------

def test_perceived_cost_grows_with_what_you_already_own():
    """`getPerceivedCost` — 실제 값은 그대로, **보이는 값**만 오른다.

    도시 증가율이 1.0 이므로 레벨 합 n 이면 (1+n)배다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    p = st.players[0]
    p.gold = 0                       # 저축 목표에 한참 못 미친다 → 배수가 걸린다

    real = p.units.cost(UnitType.CITY)
    assert b._perceived_cost(st, UnitType.CITY) == real     # 0채 → 1배

    add_unit(st, 0, UnitType.CITY, 0)
    real1 = p.units.cost(UnitType.CITY)
    assert b._perceived_cost(st, UnitType.CITY) == math.ceil(real1 * 2)

    u = add_unit(st, 0, UnitType.CITY, 40)
    u.level = 3                      # 레벨 합 = 1 + 3 = 4
    real2 = p.units.cost(UnitType.CITY)
    assert b._perceived_cost(st, UnitType.CITY) == math.ceil(real2 * 5)


def test_perceived_cost_stops_inflating_once_the_nuke_fund_is_full():
    """**저축 목표를 넘기면 배수가 사라진다.**

    이게 없으면 부자 나라가 건물을 영영 못 짓는다 — 가진 게 많을수록 더
    비싸 보이는데 배수가 끊기지 않으니 골드가 무한정 쌓인다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    p = st.players[0]
    add_unit(st, 0, UnitType.CITY, 0)

    p.gold = 0
    assert b._perceived_cost(st, UnitType.CITY) > p.units.cost(UnitType.CITY)

    p.gold = b._save_up_target(st)
    assert b._perceived_cost(st, UnitType.CITY) == p.units.cost(UnitType.CITY)


def test_save_up_target_is_a_mirv_plus_a_hydrogen_bomb():
    st = make_state()
    b = behavior()
    p = st.players[0]
    want = (p.units.cost(UnitType.MIRV, extra=st.mirvs_launched)
            + p.units.cost(UnitType.HYDROGEN_BOMB))
    assert b._save_up_target(st) == want
    assert want > 0


def test_mirv_cost_in_the_target_follows_the_global_launch_count():
    """MIRV 값은 **판 전체 발사 수**로 오른다. 저축 목표도 같이 올라야 한다.

    보유량으로 세면 아무도 MIRV 를 안 가졌으므로 목표가 영원히 고정된다."""
    st = make_state()
    b = behavior()
    before = b._save_up_target(st)
    st.mirvs_launched = 2
    assert b._save_up_target(st) > before


# --- 밀도와 업그레이드 (이 파일의 핵심) --------------------------------------

def test_density_counts_buildings_not_levels():
    """`getTotalStructureDensity` 는 **개수**로 센다.

    레벨 합으로 세면 올릴수록 밀도가 올라가 스스로를 막는다 — 한 채를 2레벨로
    올린 것과 두 채를 지은 것이 같은 밀도가 되어 버린다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 300))
    u = add_unit(st, 0, UnitType.CITY, 0)
    d1 = b._density(st)
    u.level = 5
    assert b._density(st) == d1, "레벨을 세고 있다"
    add_unit(st, 0, UnitType.CITY, 40)
    assert b._density(st) == pytest.approx(2 / 300)


def test_upgrades_instead_of_building_when_dense():
    """**이 프로젝트의 이식 누락이 정확히 여기였다.**

    영토 100칸에 건물 1채 = 1/100 로 문턱(1/1500)을 훌쩍 넘는다. 이때
    `_maybe_spawn` 은 새로 짓지 않고 **레벨을 올린다.**"""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    p = st.players[0]
    city = add_unit(st, 0, UnitType.CITY, 0)
    p.gold = 10_000_000

    before_count = p.units.num(UnitType.CITY)
    assert b._density(st) > UPGRADE_DENSITY_THRESHOLD
    assert b._maybe_spawn(st, UnitType.CITY, coastal=[]) is True
    assert city.level == 2, "올리지 않았다"
    assert p.units.num(UnitType.CITY) == before_count, "새로 지었다"


def test_builds_normally_when_sparse():
    """**막지 않았으면 무엇이 일어났을 것인가** — 대조군.

    영토를 넓혀 밀도를 문턱 아래로 떨어뜨리면 같은 상황에서 **새로 짓는다.**
    이게 없으면 위 테스트는 "업그레이드 경로가 늘 켜져 있다"도 통과시킨다."""
    st = make_state([("." * 60 + "~" * 20) for _ in range(60)])
    b = behavior()
    give_land(st, 0, land_of(st, 3000))
    p = st.players[0]
    city = add_unit(st, 0, UnitType.CITY, 0)
    p.gold = 10_000_000

    assert b._density(st) < UPGRADE_DENSITY_THRESHOLD
    before = p.units.num(UnitType.CITY)
    assert b._maybe_spawn(st, UnitType.CITY, coastal=[]) is True
    assert city.level == 1, "올렸다 — 문턱 아래인데"
    assert p.units.num(UnitType.CITY) == before + 1


def test_dense_and_cannot_upgrade_means_build_nothing():
    """빽빽한데 못 올리면 **새로 짓지도 않는다.**

    건설 중인 건물은 올릴 수 없다(`can_upgrade`). 이 경우 원본은 기다린다 —
    이 분기를 빼면 SAM 처럼 건설이 긴 것이 줄줄이 서서 골드를 다 먹는다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    p = st.players[0]
    u = add_unit(st, 0, UnitType.CITY, 0)
    u.ticks_left = 100               # 건설 중 → 올릴 수 없다
    assert u.under_construction
    p.gold = 10_000_000

    assert b._maybe_spawn(st, UnitType.CITY, coastal=[]) is False
    assert p.units.num(UnitType.CITY) == 1


def test_first_of_a_type_is_built_even_when_dense():
    """빽빽해도 **그 종류가 하나도 없으면** 첫 채는 짓는다.

    원본 주석: 작은 섬에 갇혀 밀도가 늘 높은 나라는 이게 없으면 아무것도 못 짓는다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    p = st.players[0]
    add_unit(st, 0, UnitType.CITY, 0)     # 밀도를 올려 두되 공장은 0채
    p.gold = 10_000_000

    assert b._density(st) > UPGRADE_DENSITY_THRESHOLD
    assert b._maybe_spawn(st, UnitType.FACTORY, coastal=[]) is True
    assert p.units.num(UnitType.FACTORY) == 1


def _sam_pick_rate(with_sam: bool, seeds: int = 300) -> float:
    """SAM 이 덮는 도시 1채 + 안 덮는 도시 5채. `near` 가 뽑히는 비율.

    ⚠ **후보를 둘만 두면 안 된다.** 원본은 절반의 확률로 2·3위를 고르므로
    (`chance(2)`), 후보가 둘이면 점수를 아무리 잘 매겨도 결과가 50:50 이다 —
    처음에 그렇게 짜서 31:29 를 받고 "점수가 안 먹는다"고 오해할 뻔했다."""
    st = make_state()
    give_land(st, 0, land_of(st, 2000))
    st.players[0].gold = 10_000_000

    if with_sam:
        add_unit(st, 0, UnitType.SAM_LAUNCHER, 0)          # (0,0), 사거리 70
    near = add_unit(st, 0, UnitType.CITY, 20)              # (20,0)  — 덮인다
    far = [add_unit(st, 0, UnitType.CITY, 100 * 200 + x)   # (x,100) — 안 덮인다
           for x in (0, 20, 40, 60, 80)]

    hits = 0
    for seed in range(seeds):
        b = behavior(difficulty="impossible", seed=seed)
        # `near` 를 **마지막에** 넘긴다. 동점이면 정렬 키의 순서 항목이 앞선
        # 후보를 살리므로, 첫 자리에 두면 대조군이 부풀어 오른다(실측 23.7%).
        if b._best_to_upgrade(st, [*far, near]) is near:
            hits += 1
    return hits / seeds


def test_upgrade_prefers_structures_covered_by_a_sam():
    """`findBestStructureToUpgrade` — SAM 사거리 안의 건물을 먼저 올린다.

    핵 한 발에 날아갈 자리에 레벨을 쌓지 않겠다는 판단이다.

    **막지 않았으면 무엇이 일어났을 것인가를 먼저 잰다**: SAM 이 없으면 여섯 채가
    전부 동점이라 `near` 는 1/6(≈17%) 근처에서 뽑힌다. SAM 이 있으면 1위가 되어
    "절반은 1위" 분기에 걸려 ≈50% 로 올라간다. 대조군이 없으면 점수 계산을
    통째로 지워도 이 테스트가 통과한다."""
    without = _sam_pick_rate(with_sam=False)
    with_sam = _sam_pick_rate(with_sam=True)
    assert without < 0.30, f"대조군이 이미 높다 — 검사가 무의미하다: {without}"
    assert with_sam > 0.40, with_sam
    assert with_sam > without * 2, (with_sam, without)


def test_random_upgrade_chance_follows_difficulty():
    """easy 70% · medium 40% · hard 25% · impossible 10%.

    **easy 가 가장 무작위**라는 것이 핵심이다 — 쉬운 AI 는 SAM 밖 건물도 태연히
    올린다. 값을 뒤집어 옮기기 쉬운 자리라 못 박는다."""
    assert RANDOM_UPGRADE_CHANCE == {
        "easy": 70, "medium": 40, "hard": 25, "impossible": 10}
    assert (RANDOM_UPGRADE_CHANCE["easy"] > RANDOM_UPGRADE_CHANCE["medium"]
            > RANDOM_UPGRADE_CHANCE["hard"] > RANDOM_UPGRADE_CHANCE["impossible"])


def test_upgrade_skips_what_cannot_be_upgraded():
    """돈이 모자라면 후보에서 빠지고, 후보가 없으면 None."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 100))
    p = st.players[0]
    city = add_unit(st, 0, UnitType.CITY, 0)
    p.gold = 0
    assert b._best_to_upgrade(st, [city]) is None
    assert b._maybe_upgrade(st, [city]) is False


def test_best_to_upgrade_is_deterministic_for_a_seed():
    """같은 seed 는 같은 답. `Unit` 은 비교가 안 되므로 정렬 키에 순서를 넣었다 —
    그게 빠지면 동점에서 판이 갈린다."""
    st = make_state([("." * 80) for _ in range(80)])
    give_land(st, 0, land_of(st, 2000))
    st.players[0].gold = 10_000_000
    units = [add_unit(st, 0, UnitType.CITY, i * 40) for i in range(5)]
    first = [behavior(seed=s)._best_to_upgrade(st, units) for s in range(10)]
    second = [behavior(seed=s)._best_to_upgrade(st, units) for s in range(10)]
    assert first == second


# --- 건설 순서 --------------------------------------------------------------

def test_cities_come_first_when_nothing_else_qualifies():
    """도시가 0채면 모든 비율 목표가 0 이다 → `_place` 는 도시를 짓는다."""
    st = make_state()
    b = behavior()
    give_land(st, 0, land_of(st, 3000))
    p = st.players[0]
    p.gold = 10_000_000
    assert b._place(st) is True
    assert p.units.num(UnitType.CITY) == 1


def test_ports_are_skipped_without_a_coast():
    """해안이 없으면 항구를 시도조차 안 한다.

    내륙 나라가 항구를 짓겠다고 `find_spot` 을 헛돌리면 그 tick 의 건설이
    통째로 날아간다."""
    st = make_state([("." * 40) for _ in range(40)])   # 바다가 없다
    b = behavior()
    give_land(st, 0, land_of(st, 1200))
    p = st.players[0]
    p.gold = 10_000_000
    for i in range(4):
        add_unit(st, 0, UnitType.CITY, i * 45)
    for _ in range(6):
        b._place(st)
    assert p.units.num(UnitType.PORT) == 0


def test_defense_posts_are_never_in_the_build_order():
    """**초소는 정상 건설로 지어지지 않는다.**

    공격이 없는 상태로 계속 돌려도 초소가 0채여야 한다. 여기서 초소가 나오면
    예전처럼 초소가 골드를 빨아들이는 상태로 되돌아간 것이다."""
    st = make_state([("." * 60 + "~" * 20) for _ in range(60)])
    b = behavior()
    give_land(st, 0, land_of(st, 3000))
    p = st.players[0]
    p.gold = 100_000_000
    for _ in range(40):
        p.gold = 100_000_000
        b.handle(st)
    assert p.units.num(UnitType.DEFENSE_POST) == 0
    assert p.units.num(UnitType.CITY) > 0, "아무것도 안 지었다 — 검사가 무의미하다"


def test_placements_counter_gates_the_defense_post_path():
    """초소는 **첫 건물이 될 수 없다**(`placementsCount > 0`)."""
    st = make_state()
    b = behavior(difficulty="hard")
    assert b.placements == 0
    give_land(st, 0, land_of(st, 3000))
    st.players[0].gold = 10_000_000
    b.handle(st)
    assert b.placements == 1


# --- 방어초소 ---------------------------------------------------------------

def setup_under_attack(st: GameState, threat: float, source_tile=None) -> None:
    """0번을 1번이 친다. `threat` 은 내 병력 대비 들어오는 병력의 비율."""
    st.players[0].troops = 1000.0
    atk = Attack(attacker=1, target=0, troops=1000.0 * threat,
                 source_tile=source_tile)
    st.attacks.append(atk)


def test_no_defense_post_below_the_threat_threshold():
    """들어오는 병력이 내 병력의 35% 미만이면 초소를 안 짓는다."""
    st = make_state()
    b = behavior(difficulty="hard")
    b.placements = 1
    give_land(st, 0, land_of(st, 100))
    st.players[0].gold = 10_000_000
    setup_under_attack(st, UNDER_ATTACK_THREAT_RATIO - 0.01)
    assert b._defense_post_needed(st) is False
    assert b._defense_post(st) is False


def test_defense_post_needed_above_the_threshold():
    st = make_state()
    b = behavior(difficulty="hard")
    give_land(st, 0, land_of(st, 100))
    setup_under_attack(st, UNDER_ATTACK_THREAT_RATIO + 0.01)
    assert b._defense_post_needed(st) is True


def test_boat_attacks_do_not_count_toward_the_threat():
    """**상륙은 세지 않는다** (`sourceTile() !== null`).

    초소는 국경을 넘어오는 것을 늦추는 물건이라 배로 뒤를 잡힌 상황에는
    소용이 없다. `source_tile` 이 없어 우리는 오래 이 둘을 구분 못 했다."""
    st = make_state()
    b = behavior(difficulty="hard")
    give_land(st, 0, land_of(st, 100))
    setup_under_attack(st, 5.0, source_tile=7)     # 압도적이지만 상륙이다
    assert b._threat_ratio(st) == 0.0
    assert b._defense_post_needed(st) is False


def test_easy_never_builds_defense_posts():
    st = make_state()
    b = behavior(difficulty="easy")
    b.placements = 1
    give_land(st, 0, land_of(st, 100))
    st.players[0].gold = 10_000_000
    setup_under_attack(st, 3.0)
    assert b._defense_post_needed(st) is False
    assert b._defense_post(st) is False


def test_hard_allows_more_posts_as_the_threat_grows():
    """hard 이상은 위협 비율 0.4 마다 한 기씩 더 허용한다.

    비율 0.5 → ceil(0.5/0.4) = 2기, 비율 1.2 → 3기."""
    assert DEFENSE_POST_RATIO_PER_POST == 0.4
    assert math.ceil(0.5 / DEFENSE_POST_RATIO_PER_POST) == 2
    assert math.ceil(1.2 / DEFENSE_POST_RATIO_PER_POST) == 3


def test_medium_is_capped_at_one_post():
    """medium 은 위협이 아무리 커도 1기까지다."""
    st = make_state([("." * 60 + "~" * 20) for _ in range(60)])
    give_land(st, 0, land_of(st, 2000))
    give_land(st, 1, land_of(st, 200, start=2600))
    st.players[0].gold = 100_000_000
    setup_under_attack(st, 5.0)

    built = 0
    for seed in range(60):
        b = behavior(difficulty="medium", seed=seed)
        b.placements = 1
        st.players[0].gold = 100_000_000
        if b._defense_post(st):
            built += 1
    # 한 기가 서고 나면 그 뒤로는 전선 근처 개수 제한에 막힌다
    assert st.players[0].units.num(UnitType.DEFENSE_POST) == 1, built


def test_threat_blocks_other_construction():
    """위협 문턱을 넘은 상태에서는 초소를 못 지었어도 **다른 건물도 안 짓는다.**

    돈이 없어 초소를 못 세운 나라가 그 tick 에 도시를 올리면 안 된다."""
    st = make_state()
    b = behavior(difficulty="hard")
    b.placements = 1
    give_land(st, 0, land_of(st, 100))
    st.players[0].gold = 0                 # 초소를 살 수 없다
    setup_under_attack(st, 2.0)
    assert b.handle(st) is False
    st.players[0].gold = 10_000_000
    # 돈이 생겨도 초소 자리가 먼저다 — 도시가 늘지 않는다
    before = st.players[0].units.num(UnitType.CITY)
    b.handle(st)
    assert st.players[0].units.num(UnitType.CITY) == before


def test_border_spacing_reads_the_atom_bomb_radius():
    """초소 간격은 상수가 아니라 **원자탄 바깥 반경**에서 나온다.

    §5.10 에서 핵 반경이 지도 규모에 맞춰졌는데, 여기에 숫자를 따로 박으면
    지도 크기를 바꿀 때 조용히 어긋난다."""
    from domynion.core.nukes import NUKE_MAGNITUDES
    assert border_spacing() == NUKE_MAGNITUDES[UnitType.ATOM_BOMB][1]


# --- 지도 밀도 --------------------------------------------------------------

def test_high_density_ignores_tribes():
    """`isHighNationDensity` 는 **나라만** 센다.

    봇 400 을 같이 세면 어느 지도에서나 문턱을 넘어 분기가 늘 켜진 채가 된다 —
    그러면 모든 나라의 첫 건물이 항구가 되고 도시 우선이 사라진다."""
    from domynion.core.state import PlayerState

    st = make_state([("." * 200 + "~" * 50) for _ in range(200)])
    b = behavior()
    b._land_tiles = None
    assert b._high_nation_density(st) is False       # 나라 2명
    for pid in range(2, 2000):
        st.players[pid] = PlayerState(pid=pid, name=f"b{pid}", kind="bot", start=0)
    b._land_tiles = None
    assert b._high_nation_density(st) is False, "봇을 세고 있다"


# ---------------------------------------------------------------------------
# 확인한 변이 (2026-08-24) — 전부 잡혔다
#
# 1. `_should_build` 의 `math.floor` → `round`
#      → test_port_target_is_three_quarters_of_cities (도시 1채에서 항구가 선다)
# 2. `FACTORY_COASTAL_RATIO_MULTIPLIER` 를 안 곱함
#      → test_factory_ratio_is_cut_by_a_third_on_the_coast
# 3. `FIRST_MISSILE_SILO_RATIO` 분기 제거
#      → test_first_missile_silo_uses_the_higher_ratio
# 4. `_should_build` 가 `num()`(개수)을 읽게 바꿈 — `owned()`(레벨 합) 대신
#      → test_missile_silos_are_hard_capped_at_three
# 5. `SAM_RATIO_BY_DIFFICULTY[self.difficulty]` → `[...]["medium"]` 고정
#      → test_sam_ratio_follows_difficulty
# 6. `_density` 가 `owned()`(레벨 합)를 세게 바꿈
#      → test_density_counts_buildings_not_levels, test_upgrades_instead_of_building
# 7. `_maybe_spawn` 의 밀도 분기 통째로 제거
#      → test_upgrades_instead_of_building_when_dense
# 8. 밀도 분기에서 `if structures: return False` 제거
#      → test_dense_and_cannot_upgrade_means_build_nothing
# 9. `_perceived_cost` 의 저축 목표 조기 반환 제거
#      → test_perceived_cost_stops_inflating_once_the_nuke_fund_is_full
# 10. `_save_up_target` 에서 MIRV 의 `extra=` 제거
#      → test_mirv_cost_in_the_target_follows_the_global_launch_count
# 11. `_land_attacks` 의 `source_tile is None` 제거
#      → test_boat_attacks_do_not_count_toward_the_threat
# 12. `handle` 의 `if self._defense_post_needed(st): return False` 제거
#      → test_threat_blocks_other_construction
# 13. `_high_nation_density` 가 `len(st.players)` 를 세게 바꿈
#      → test_high_density_ignores_tribes
# 14. `STRUCTURE_RATIOS` 에 `DEFENSE_POST` 항목 추가
#      → test_cities_are_never_gated_by_a_ratio,
#        test_defense_posts_are_never_in_the_build_order
#
# ⚠ **잡히지 않는 게 정상인 변이 하나.** `BUILD_ORDER` 에 `DEFENSE_POST` 를 넣어도
# 아무 일도 일어나지 않는다 — 초소는 `STRUCTURE_RATIOS` 에 없어 `_should_build` 가
# 항상 False 를 돌려주기 때문이다. 즉 **진짜 관문은 순서 목록이 아니라 비율 표**다.
# 다음 세션이 "테스트가 못 잡는다"고 여기를 파지 않도록 적어 둔다.
# 15. `_best_to_upgrade` 의 SAM 점수 제거
#      → test_upgrade_prefers_structures_covered_by_a_sam
# ---------------------------------------------------------------------------


# --- 초소 자리 (`sampleTilesNearFront`) --------------------------------------
#
# §5.31 에서 간략화해 뒀던 자리다. 전에는 전선 타일을 그대로 find_spot 에 넘겨
# 가장 가까운 빈자리를 썼다 — 초소가 국경에 딱 붙고 여러 기가 한 곳에 몰린다.

def _wide_state():
    """넓은 영토 — 국경 깊이 띠(borderSpacing×0.75~1.5)가 실제로 존재해야 한다."""
    st = make_state()
    give_land(st, 0, land_of(st, 0))          # 아래에서 직접 채운다
    return st


def _fill(st, pid: int, x0: int, y0: int, x1: int, y1: int) -> None:
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            st.gmap.owner[st.gmap.ref(x, y)] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n


def test_defense_posts_sit_in_the_border_depth_band():
    """초소는 국경에서 `borderSpacing × 0.75 ~ 1.5` 안에 선다.

    ⚠ 너무 앞이면 첫 공격에 넘어가고, 너무 뒤면 전선을 못 덮는다.
    전에는 전선 타일 근처의 가장 가까운 빈자리라 **국경에 딱 붙었다.**"""
    from domynion.ai.placement import border_tiles, closest_dist
    b = NationStructureBehavior(0, random.Random(0), "hard")
    st = make_state()
    span = border_spacing() * 4
    _fill(st, 0, 0, 0, span, span)
    front = [st.gmap.ref(0, y) for y in range(0, span, 5)]   # 왼쪽 국경이 전선
    tiles = b._sample_near_front(st, front, 25)
    assert tiles, "한 칸도 못 뽑았다"
    bts = border_tiles(st.gmap, 0)
    lo = math.ceil(border_spacing() * 0.75)
    hi = math.ceil(border_spacing() * 1.5)
    for t in tiles:
        d = closest_dist(st.gmap, bts, t)
        assert d is not None and lo <= d <= hi, f"국경에서 {d} — 띠({lo}~{hi}) 밖이다"


def test_all_sampled_tiles_are_mine():
    """남의 땅은 안 뽑는다."""
    b = NationStructureBehavior(0, random.Random(0), "hard")
    st = make_state()
    span = border_spacing() * 4
    _fill(st, 0, 0, 0, span, span)
    _fill(st, 1, span, 0, span * 2, span)     # 옆에 남의 땅
    front = [st.gmap.ref(span - 1, y) for y in range(0, span, 5)]
    for t in b._sample_near_front(st, front, 25):
        assert int(st.gmap.owner[t]) == 0, "남의 땅을 뽑았다"


def test_posts_spread_away_from_existing_ones():
    """이미 초소가 있으면 그 초소에서 `borderSpacing × 1.5` 밖인 전선만 쓴다.

    ⚠ 처음에 `<=` 로 "몰리지 않았다"만 쟀는데 그건 **항상 참**이었다. 뽑힌 칸이
    기존 초소에서 얼마나 떨어졌는지를 **직접 재고**, 초소가 없는 경우를 대조군으로
    둔다."""
    from domynion.ai.placement import euclid_sq
    span = border_spacing() * 4

    def min_dist_to(x0: int, y0: int, with_post: bool) -> float:
        b = NationStructureBehavior(0, random.Random(1), "hard")
        st = make_state()
        _fill(st, 0, 0, 0, span, span)
        # 전선을 왼쪽 국경 전체에 두고, 기존 초소를 그 한쪽 끝 근처에 놓는다
        front = [st.gmap.ref(0, y) for y in range(0, span, 4)]
        post = st.gmap.ref(x0, y0)
        if with_post:
            st.players[0].units.units.append(
                Unit(UnitType.DEFENSE_POST, 0, tile=post))
        tiles = b._sample_near_front(st, front, 25)
        assert tiles, "한 칸도 못 뽑았다 — 재료가 잘못됐다"
        return min(euclid_sq(st.gmap, t, post) for t in tiles) ** 0.5

    px, py = border_spacing(), 5
    with_post = min_dist_to(px, py, True)
    without = min_dist_to(px, py, False)
    assert with_post > without,         f"초소가 있어도 같은 자리에 뽑는다 ({without:.0f} -> {with_post:.0f})"


def test_a_thin_territory_falls_back():
    """영토가 얇아 띠가 안 나오면 깊이 조건을 풀고 다시 뽑는다.

    ⚠ 폴백이 없으면 좁은 나라는 초소를 **한 기도** 못 짓는다."""
    b = NationStructureBehavior(0, random.Random(0), "hard")
    st = make_state()
    _fill(st, 0, 0, 0, 200, 3)                # 세 칸 두께 — 띠가 존재할 수 없다
    front = [st.gmap.ref(x, 1) for x in range(0, 200, 5)]
    tiles = b._sample_near_front(st, front, 25)
    assert tiles, "폴백이 없어 한 칸도 못 뽑았다"
    for t in tiles:
        assert int(st.gmap.owner[t]) == 0, "폴백에서도 남의 땅은 안 된다"


def test_no_front_means_no_tiles():
    b = NationStructureBehavior(0, random.Random(0), "hard")
    st = make_state()
    _fill(st, 0, 0, 0, 100, 100)
    assert b._sample_near_front(st, [], 25) == []
