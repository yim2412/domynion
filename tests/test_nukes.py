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
from domynion.core.nukes import (NUKE_MAGNITUDES, NUKE_SPEED, Fallout, Nuke,
                                 blast_tiles, death_factor, sam_range)
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


def test_detonation_turns_land_to_water_and_clears_the_path_cache():
    """폭심의 육지는 바다가 된다. **바다 경로 캐시를 반드시 비워야 한다** —
    안 그러면 P4 의 캐시가 사라진 육지를 계속 육지로 안다."""
    st = state()
    st.players[0].gold = 10_000_000
    give_silo(st, 0, st.gmap.ref(5, 5))
    st._path_cache[(1, 2)] = [1, 2]
    dst = st.gmap.ref(40, 40)
    before_land = st.gmap.land_count
    n = st.launch_nuke(0, UnitType.ATOM_BOMB, dst)
    for _ in range(60):
        st.tick()
        if n not in st.nukes:
            break
    assert st.gmap.terrain[dst] == Terrain.OCEAN
    assert st.gmap.land_count < before_land
    assert st._path_cache == {}, "지형이 바뀌었는데 경로 캐시가 남아 있다"
    assert st.fallout.at(dst)


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
    assert st.gmap.terrain[st.gmap.ref(40, 40)] != Terrain.OCEAN, "요격됐어야 한다"

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
    assert st2.gmap.terrain[st2.gmap.ref(40, 40)] == Terrain.OCEAN, "자기 SAM 이 막았다"


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
