"""건물을 **어디에** 짓는가 — `validStructureSpawnTiles` (§5.86).

클릭한 칸이 곧 건설 자리가 아니다. 원본은 그 칸에서 반경 안을 훑어 가장
가까운 유효한 칸에 짓는다. 규칙 둘이 여기 걸린다:

1. **반경은 15 다**(`searchRadius`). 그보다 멀면 짓지 않는다
2. 최소 거리(15)는 **주인을 안 가린다** — 남의 건물도 자리를 막는다
"""

from __future__ import annotations

import random

from domynion.core import constants as C
from domynion.core.buildings import (all_structure_tiles, can_place_structure,
                                     find_spot, structure_tiles)
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def make_state(width: int = 120, height: int = 60) -> GameState:
    gm = GameMap.from_rows(["." * width] * height)
    players = {}
    for pid in (0, 1):
        t = gm.ref(pid * 60 + 10, 10)
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation",
                                   start=t)
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 0, 1: 0}
    return st


def claim(st: GameState, pid: int, x0: int, x1: int, y0: int, y1: int) -> None:
    n = 0
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            st.gmap.owner[st.gmap.ref(x, y)] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n


def give(st: GameState, pid: int, utype: UnitType, x: int, y: int) -> Unit:
    u = Unit(utype, pid, tile=st.gmap.ref(x, y))
    st.players[pid].units.units.append(u)
    return u


# --- 반경 15 ---------------------------------------------------------------

def test_the_search_radius_is_fifteen() -> None:
    """클릭한 칸에서 **15칸 안**에만 대신 지을 자리를 찾는다.

    막지 않았으면(반경이 40이면): 사람이 남의 땅이나 바다를 눌러도 15~40칸
    떨어진 내 땅 어딘가에 건물이 선다 — **어디를 눌렀는지가 무의미해진다.**
    아래 둘째 단언이 옛 반경 40에서는 통과했다."""
    st = make_state()
    claim(st, 0, 0, 20, 0, 20)              # 내 땅은 x ≤ 20
    st.players[0].gold = 10_000_000

    near = st.gmap.ref(30, 10)              # 내 땅에서 10칸 밖
    assert st.can_build(0, UnitType.CITY, near) is not None, \
        "반경 안이면 옮겨서라도 지어야 한다"

    far = st.gmap.ref(50, 10)               # 30칸 밖 — 옛 반경 40이면 지어졌다
    assert st.can_build(0, UnitType.CITY, far) is None, \
        "15칸을 넘는데도 자리를 찾았다"
    assert C.STRUCTURE_SEARCH_RADIUS == 15


def test_the_radius_is_not_the_min_distance_by_accident() -> None:
    """둘 다 15 지만 **다른 값이다.** 한쪽만 바꿔도 다른 쪽이 안 따라가야 한다."""
    st = make_state()
    claim(st, 0, 0, 40, 0, 40)
    st.players[0].gold = 10_000_000
    got = find_spot(st.gmap, 0, st.gmap.ref(20, 20), [], search=3)
    assert got == st.gmap.ref(20, 20), "빈 땅이면 클릭한 칸 그대로다"


# --- 남의 건물도 막는다 ----------------------------------------------------

def test_an_enemy_structure_blocks_the_spot() -> None:
    """최소 거리는 **주인을 안 가린다**(`nearbyUnits` 의 predicate 가 없다).

    막지 않았으면: 국경에 적 도시가 있어도 그 옆 5칸에 내 도시를 붙여 지을 수
    있다. 아래 첫 단언이 그 상태를 못 박는다 — 내 건물만 보면 자리가 있다."""
    st = make_state()
    claim(st, 0, 0, 30, 0, 30)
    claim(st, 1, 31, 60, 0, 30)
    st.players[0].gold = 10_000_000
    enemy = give(st, 1, UnitType.CITY, 32, 10)

    near = st.gmap.ref(30, 10)              # 적 도시에서 2칸
    mine_only = find_spot(st.gmap, 0, near, structure_tiles(st.players[0].units))
    assert mine_only == near, "내 건물만 보면 클릭한 칸이 그대로 유효하다"

    got = st.can_build(0, UnitType.CITY, near)
    assert got != near, "적 도시 옆 2칸에 그대로 지었다"
    if got is not None:
        d2 = (got % st.gmap.width - 32) ** 2 + (got // st.gmap.width - 10) ** 2
        assert d2 >= C.STRUCTURE_MIN_DIST ** 2, "적 건물과 15칸을 안 벌렸다"
    assert enemy.active


def test_all_structure_tiles_gathers_every_player() -> None:
    st = make_state()
    give(st, 0, UnitType.CITY, 5, 5)
    give(st, 1, UnitType.PORT, 70, 5)
    tiles = set(all_structure_tiles(st.alive))
    assert tiles == {st.gmap.ref(5, 5), st.gmap.ref(70, 5)}


def test_can_place_still_refuses_someone_elses_land() -> None:
    """자리를 넓게 보게 됐다고 **남의 땅에 짓게 되면 안 된다.**"""
    st = make_state()
    claim(st, 0, 0, 30, 0, 30)
    claim(st, 1, 31, 60, 0, 30)
    assert not can_place_structure(st.gmap, st.gmap.ref(35, 10), 0, [])
    assert can_place_structure(st.gmap, st.gmap.ref(25, 10), 0, [])
