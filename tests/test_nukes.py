"""P5 — 핵 · 낙진 · SAM.

핵의 특징은 **칸마다 병력 손실이 반복 적용된다**는 것이다. 한 번에 계산하면 값이
완전히 달라진다 — 남은 타일 수가 매 칸 줄어들면서 나눗셈의 분모가 작아지기 때문이다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.attack import attack_logic
from domynion.core.buildings import DefensePostIndex
from domynion.core.constants import Terrain
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import (NUKE_MAGNITUDES, NUKE_SPEED, SAM_TARGETABLE_TYPES,
                                 Fallout, Nuke, blast_tiles, death_factor,
                                 is_targetable, sam_range)
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(size: int = 80, players: int = 2) -> GameState:
    gm = GameMap.from_rows(["." * size] * size)
    ps = {}
    for pid in range(players):
        t = gm.ref(pid * 20 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def give_silo(st: GameState, pid: int, tile: int) -> Unit:
    u = Unit(UnitType.MISSILE_SILO, pid, tile=tile)
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(UnitType.MISSILE_SILO)
    return u


# --- 수치 -------------------------------------------------------------------

def test_nuke_magnitudes_match_original():
    assert NUKE_MAGNITUDES[UnitType.ATOM_BOMB] == (12, 30)
    assert NUKE_MAGNITUDES[UnitType.HYDROGEN_BOMB] == (80, 100)
    assert NUKE_MAGNITUDES[UnitType.MIRV_WARHEAD] == (12, 18)
    assert NUKE_SPEED[UnitType.ATOM_BOMB] == 10
    assert NUKE_SPEED[UnitType.MIRV] == 15
    assert NUKE_SPEED[UnitType.MIRV_WARHEAD] == 22


def test_sam_range_grows_toward_the_cap():
    """`150 − 480/(레벨+5)` — Lv1 은 70, 위로 갈수록 150 에 수렴한다."""
    assert sam_range(1) == pytest.approx(C.DEFAULT_SAM_RANGE)
    assert sam_range(1) < sam_range(2) < sam_range(5)
    assert sam_range(100) < C.MAX_SAM_RANGE


def test_death_factor_hurts_small_countries_more():
    """`5 × 병력 / 남은타일수` — 분모가 영토라 좁을수록 한 칸이 아프다."""
    wide = death_factor(UnitType.ATOM_BOMB, 100_000.0, 10_000, 200_000.0)
    narrow = death_factor(UnitType.ATOM_BOMB, 100_000.0, 100, 200_000.0)
    assert narrow == pytest.approx(wide * 100)
    assert wide == pytest.approx(5 * 100_000.0 / 10_000)


def test_mirv_warhead_uses_a_different_curve():
    """MIRV 탄두는 상한 대비 **초과 병력**만 노린다 — 3% 아래면 피해가 0 이다."""
    cap = 1_000_000.0
    tiny = death_factor(UnitType.MIRV_WARHEAD, cap * 0.02, 500, cap)
    big = death_factor(UnitType.MIRV_WARHEAD, cap * 0.9, 500, cap)
    assert tiny == pytest.approx(0.0)
    assert 0 < big <= C.MIRV_DEATH_SCALE


# --- 폭발 -------------------------------------------------------------------

def test_blast_covers_inner_radius_completely():
    gm = GameMap.from_rows(["." * 80] * 80)
    centre = gm.ref(40, 40)
    tiles = set(blast_tiles(gm, centre, UnitType.ATOM_BOMB, random.Random(1)))
    inner, outer = NUKE_MAGNITUDES[UnitType.ATOM_BOMB]
    for dx in range(-inner, inner + 1):
        for dy in range(-inner, inner + 1):
            if dx * dx + dy * dy <= inner * inner:
                assert gm.ref(40 + dx, 40 + dy) in tiles, "inner 안이 안 날아갔다"
    assert all(
        (t % 80 - 40) ** 2 + (t // 80 - 40) ** 2 <= outer * outer for t in tiles
    ), "outer 밖까지 날아갔다"


def test_blast_edge_is_ragged_not_a_circle():
    """가장자리는 방향마다 문턱이 달라 울퉁불퉁하다. 원이면 원본과 인상이 다르다."""
    gm = GameMap.from_rows(["." * 80] * 80)
    tiles = blast_tiles(gm, gm.ref(40, 40), UnitType.ATOM_BOMB, random.Random(7))
    inner, outer = NUKE_MAGNITUDES[UnitType.ATOM_BOMB]
    ring = [t for t in tiles
            if (t % 80 - 40) ** 2 + (t // 80 - 40) ** 2 > inner * inner]
    dists = {round(((t % 80 - 40) ** 2 + (t // 80 - 40) ** 2) ** 0.5) for t in ring}
    assert len(dists) > 3, "가장자리 거리가 한 값이면 완전한 원이다"


def test_impassable_survives_the_blast():
    gm = GameMap.from_rows(["." * 40] * 40)
    gm.raw[gm.ref(20, 20)] = C.LAND_BIT | C.IMPASSABLE_MAGNITUDE
    gm.terrain[gm.ref(20, 20)] = Terrain.IMPASSABLE
    tiles = blast_tiles(gm, gm.ref(20, 20), UnitType.ATOM_BOMB, random.Random(1))
    assert gm.ref(20, 20) not in tiles


# --- 엔진 배선 --------------------------------------------------------------

def test_launch_requires_a_silo_and_gold():
    st = state()
    p = st.players[0]
    p.gold = 10_000_000
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(40, 40)) is None, "사일로가 없다"
    give_silo(st, 0, st.gmap.ref(5, 5))
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(40, 40)) is not None
    p.gold = 0
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(40, 40)) is None


def _detonate_at(st, dst):
    st.players[0].gold = 10_000_000
    give_silo(st, 0, st.gmap.ref(5, 5))
    n = st.launch_nuke(0, UnitType.ATOM_BOMB, dst)
    for _ in range(60):
        st.tick()
        if n not in st.nukes:
            break
    return n


def test_default_nukes_leave_fallout_not_water(monkeypatch):
    """`waterNukes()` 기본값은 **false** — 폭심은 육지로 남고 낙진만 생긴다.

    막지 않았으면(둘 다 하면): 낙진이 지도를 덮는다. 실측으로 한 판에 90.3% 였다."""
    monkeypatch.setattr(C, "WATER_NUKES", False)
    st = state()
    dst = st.gmap.ref(40, 40)
    before_land = st.gmap.land_count
    _detonate_at(st, dst)
    assert st.gmap.terrain[dst] != Terrain.OCEAN, "바다가 되면 안 된다"
    assert st.gmap.land_count == before_land
    assert st.fallout.at(dst)


def test_water_nukes_convert_terrain_and_clear_both_fallout_and_path_cache(monkeypatch):
    """`waterNukes` 를 켜면 반대다 — 바다가 되고 낙진은 지워진다(`setWater` 가 지운다).

    지형이 바뀌므로 **P4 의 바다 경로 캐시를 반드시 비워야 한다.**"""
    monkeypatch.setattr(C, "WATER_NUKES", True)
    st = state()
    st._path_cache[(1, 2)] = [1, 2]
    dst = st.gmap.ref(40, 40)
    before_land = st.gmap.land_count
    _detonate_at(st, dst)
    assert st.gmap.terrain[dst] == Terrain.OCEAN
    assert st.gmap.land_count < before_land
    assert not st.fallout.at(dst), "바다 칸에는 낙진이 남지 않는다"
    assert st._path_cache == {}, "지형이 바뀌었는데 경로 캐시가 남아 있다"


def test_nuke_kills_troops_and_takes_tiles():
    st = state()
    victim = st.players[1]
    for x in range(30, 50):
        for y in range(30, 50):
            st.gmap.owner[st.gmap.ref(x, y)] = 1
    # 20×20 블록 400칸 + state() 가 깔아 둔 시작 칸 1개 = 401
    st._counts = {0: 1, 1: 401}
    assert st.verify_counts(), "출발점부터 카운트가 어긋나면 아래는 아무 의미가 없다"
    victim.troops = 500_000.0
    st.players[0].gold = 10_000_000
    give_silo(st, 0, st.gmap.ref(5, 5))
    n = st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(40, 40))
    for _ in range(80):
        st.tick()
        if n not in st.nukes:
            break
    assert st.tiles(1) < 401, "타일을 안 뺏겼다"
    assert victim.troops < 500_000.0, "병력이 안 죽었다"
    assert st.verify_counts()


def test_sam_intercepts_enemy_nukes_only():
    st = state()
    st.players[0].gold = 10_000_000
    give_silo(st, 0, st.gmap.ref(5, 5))
    sam = Unit(UnitType.SAM_LAUNCHER, 1, tile=st.gmap.ref(40, 40), level=1)
    st.players[1].units.units.append(sam)
    st.players[1].units.record_constructed(UnitType.SAM_LAUNCHER)

    n = st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(40, 40))
    for _ in range(80):
        st.tick()
        if n not in st.nukes:
            break
    # 폭발 여부는 **낙진**으로 판정한다. 기본값(waterNukes=false)에서는 지형이
    # 안 바뀌므로 지형으로 재면 항상 통과하는 빈 테스트가 된다.
    assert not st.fallout.at(st.gmap.ref(40, 40)), "요격됐어야 한다"

    # 같은 SAM 이 주인의 핵은 안 막는다
    st2 = state()
    st2.players[0].gold = 10_000_000
    give_silo(st2, 0, st2.gmap.ref(5, 5))
    own_sam = Unit(UnitType.SAM_LAUNCHER, 0, tile=st2.gmap.ref(40, 40), level=1)
    st2.players[0].units.units.append(own_sam)
    n2 = st2.launch_nuke(0, UnitType.ATOM_BOMB, st2.gmap.ref(40, 40))
    for _ in range(80):
        st2.tick()
        if n2 not in st2.nukes:
            break
    assert st2.fallout.at(st2.gmap.ref(40, 40)), "자기 SAM 이 막았다"


def test_buildings_inside_the_blast_are_destroyed():
    st = state()
    st.players[0].gold = 10_000_000
    give_silo(st, 0, st.gmap.ref(5, 5))
    doomed = Unit(UnitType.CITY, 1, tile=st.gmap.ref(40, 40))
    st.players[1].units.units.append(doomed)
    n = st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(40, 40))
    for _ in range(80):
        st.tick()
        if n not in st.nukes:
            break
    assert doomed not in st.players[1].units.units


# --- 낙진 -------------------------------------------------------------------

def test_fallout_raises_defence():
    """`5 − 낙진비율 × 2` 가 mag·speed 에 곱해진다. 폭심을 지나기가 비싸진다."""
    gm = GameMap.from_rows(["." * 10])
    atk = PlayerState(pid=0, name="A", is_bot=False, troops=50_000.0)
    dfn = PlayerState(pid=1, name="D", is_bot=False, troops=50_000.0)
    clean = attack_logic(gm, 0, 10_000.0, atk, dfn, 500, 500)
    dirty = attack_logic(gm, 0, 10_000.0, atk, dfn, 500, 500, fallout_mod=5.0)
    assert dirty.attacker_loss > clean.attacker_loss * 4
    assert dirty.tiles_used == pytest.approx(clean.tiles_used * 5.0)


def test_fallout_modifier_weakens_as_the_map_gets_dirtier():
    """낙진이 많을수록 한 칸의 방어 효과가 줄어든다(5 → 3)."""
    f = Fallout(1000)
    f.add(list(range(10)))
    assert f.modifier(1000) == pytest.approx(5.0 - 0.01 * 2)
    f.add(list(range(1000)))
    assert f.modifier(1000) == pytest.approx(3.0)


# --- 요격 창 (§5.49 · 이식 누락 스물일곱) -------------------------------------

def wide_state(width: int = 600, height: int = 40, players: int = 2) -> GameState:
    """가로로 긴 지도. **요격 창(150)을 재려면 비행거리가 300 을 넘어야 한다** —
    80×80 짜리 손지도에서는 모든 칸이 발사점이나 표적에서 150 안이라 이 규칙이
    무동작이 된다(CLAUDE.md 8번 함정)."""
    gm = GameMap.from_rows(["." * width] * height)
    ps = {}
    for pid in range(players):
        t = gm.ref(pid * 10 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def test_midflight_nuke_is_untargetable():
    """발사점 150 · 표적 150 **밖**을 나는 동안에는 SAM 이 못 건드린다."""
    st = wide_state()
    st.players[0].gold = 10_000_000
    src, dst = st.gmap.ref(20, 20), st.gmap.ref(520, 20)
    give_silo(st, 0, src)
    sam_tile = st.gmap.ref(270, 20)      # 양쪽에서 250 씩 — 중간 구간
    sam = Unit(UnitType.SAM_LAUNCHER, 1, tile=sam_tile, level=1)
    st.players[1].units.units.append(sam)
    st.players[1].units.record_constructed(UnitType.SAM_LAUNCHER)

    # 막지 않았으면 무엇이 일어났을 것인가 — SAM 사거리 안을 실제로 지나간다
    r = sam_range(1)
    n0 = Nuke(owner=0, utype=UnitType.ATOM_BOMB, src=src, dst=dst)
    passes_in_range = False
    for _ in range(200):
        n0.advance()
        here = n0.tile(st.gmap)
        if st._dist_sq(sam_tile, here) <= r * r:
            passes_in_range = True
            assert not is_targetable(st.gmap, src, dst, here), \
                "이 칸은 요격 창 밖이어야 한다"
        if n0.arrived(st.gmap):
            break
    assert passes_in_range, "SAM 사거리를 아예 안 지나가면 아무것도 안 재는 테스트다"

    n = st.launch_nuke(0, UnitType.ATOM_BOMB, dst)
    assert n is not None
    for _ in range(200):
        st.tick()
        if n not in st.nukes:
            break
    assert st.fallout.at(dst), "중간 구간에서 요격됐다 — 요격 창이 안 걸렸다"


def test_sam_near_the_target_still_intercepts():
    """요격 창은 **표적 근처**에서 열린다. SAM 을 무력화한 것이 아니다."""
    st = wide_state()
    st.players[0].gold = 10_000_000
    src, dst = st.gmap.ref(20, 20), st.gmap.ref(520, 20)
    give_silo(st, 0, src)
    sam = Unit(UnitType.SAM_LAUNCHER, 1, tile=st.gmap.ref(490, 20), level=1)
    st.players[1].units.units.append(sam)
    st.players[1].units.record_constructed(UnitType.SAM_LAUNCHER)

    n = st.launch_nuke(0, UnitType.ATOM_BOMB, dst)
    for _ in range(200):
        st.tick()
        if n not in st.nukes:
            break
    assert not st.fallout.at(dst), "표적 옆 SAM 이 못 막았다"


def test_mirv_carrier_cannot_be_intercepted():
    """`SAMLauncherExecution` 의 표적 목록에 **MIRV 본체가 없다.**

    본체를 막을 수 있으면 탄두 여러 발이 한 방에 사라져 MIRV 가 의미를 잃는다."""
    assert UnitType.MIRV not in SAM_TARGETABLE_TYPES
    assert UnitType.MIRV_WARHEAD in SAM_TARGETABLE_TYPES

    st = wide_state()
    st.players[0].gold = 500_000_000
    src, dst = st.gmap.ref(20, 20), st.gmap.ref(120, 20)
    give_silo(st, 0, src)
    # 표적 바로 위 SAM — 창은 열려 있다. 막히는 이유가 있다면 종류뿐이다.
    sam = Unit(UnitType.SAM_LAUNCHER, 1, tile=dst, level=5)
    st.players[1].units.units.append(sam)
    st.players[1].units.record_constructed(UnitType.SAM_LAUNCHER)

    n = st.launch_nuke(0, UnitType.MIRV, dst)
    assert n is not None
    for _ in range(60):
        st.tick()
        if n not in st.nukes:
            break
    else:
        raise AssertionError("MIRV 가 아직 날고 있다 — 테스트 tick 이 모자라다")
    # ⚠ 우리 탄두는 갈라지는 자리에서 **즉시 터진다**(`_split_mirv`) — 원본처럼
    # 날아가지 않는다. 그래서 판정은 낙진으로 한다.
    assert bool(st.fallout.mask.any()), "MIRV 본체가 요격돼 갈라지지 못했다"


def test_sam_near_the_launch_site_also_intercepts():
    """요격 창은 **양쪽**에 열린다 — 표적뿐 아니라 발사점 150 안에서도.

    ⚠ 이 배치가 없으면 `d2(here, src) < r2` 를 지워도 아무 테스트가 안 깨진다
    (실제로 안 깨졌다). 표적 쪽만 재는 재료로는 절반만 검사하는 셈이다."""
    st = wide_state()
    st.players[0].gold = 10_000_000
    src, dst = st.gmap.ref(20, 20), st.gmap.ref(520, 20)
    give_silo(st, 0, src)
    # 발사점에서 60 — 창이 열려 있고, 표적에서는 440 이라 표적 쪽 창은 닫혀 있다
    sam_tile = st.gmap.ref(80, 20)
    sam = Unit(UnitType.SAM_LAUNCHER, 1, tile=sam_tile, level=1)
    st.players[1].units.units.append(sam)
    st.players[1].units.record_constructed(UnitType.SAM_LAUNCHER)
    assert st._dist_sq(sam_tile, dst) > C.NUKE_TARGETABLE_RANGE ** 2, \
        "표적 쪽 창까지 열려 있으면 발사점 쪽을 안 재는 테스트가 된다"

    n = st.launch_nuke(0, UnitType.ATOM_BOMB, dst)
    for _ in range(200):
        st.tick()
        if n not in st.nukes:
            break
    assert not st.fallout.at(dst), "발사점 옆 SAM 이 못 막았다"
