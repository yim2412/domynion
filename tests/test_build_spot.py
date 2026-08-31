"""건물을 **어디에** 짓는가 — `validStructureSpawnTiles` (§5.86).

클릭한 칸이 곧 건설 자리가 아니다. 원본은 그 칸에서 반경 안을 훑어 가장
가까운 유효한 칸에 짓는다. 규칙 둘이 여기 걸린다:

1. **반경은 15 다**(`searchRadius`). 그보다 멀면 짓지 않는다
2. 최소 거리(15)는 **주인을 안 가린다** — 남의 건물도 자리를 막는다
"""

from __future__ import annotations

import random

from domynion.core import constants as C
from domynion.core.constants import Terrain
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


# --- 항구만 자를 다르게 쓴다 (§5.89) ---------------------------------------

def test_a_port_is_measured_in_manhattan_not_euclid() -> None:
    """원본 `portSpawn` 은 **맨해튼 거리 순**으로 고른다(`radiusPortSpawn` = 20).

    막지 않았으면(유클리드로 재면): 대각선 쪽 후보가 더 가깝게 보여 먼저 뽑힌다.
    아래 대조군이 그것을 못 박는다 — 유클리드로는 대각선 쪽이 이긴다."""
    rows = ["~" + "." * 39 for _ in range(40)]
    gm = GameMap.from_rows(rows)
    players = {0: PlayerState(pid=0, name="P0", kind="nation", start=gm.ref(1, 20))}
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 0}
    n = 0
    for y in range(40):
        st.gmap.owner[st.gmap.ref(1, y)] = 0            # 해안 한 줄만 내 땅이다
        n += 1
    st._counts[0] = n
    st.players[0].gold = 10_000_000

    near = st.gmap.ref(9, 20)                            # 해안에서 8칸 안쪽
    got = st.can_build(0, UnitType.PORT, near)
    assert got is not None, "항구를 지을 자리가 있어야 한다"
    assert got == st.gmap.ref(1, 20), "가장 가까운 해안이 아니다"
    assert C.PORT_SPAWN_RADIUS == 20


def test_a_port_beyond_the_manhattan_radius_is_refused() -> None:
    """맨해튼 20 을 넘으면 항구는 안 선다 — 다른 건물은 유클리드 15 로 잰다.

    ⚠ **둘이 갈리는 자리를 정확히 짚어야 뜻이 있다.** 오프셋 (11,10) 은
    유클리드 14.87(≤15, 통과)인데 맨해튼 21(>20, 탈락)이다. (10,10) 은
    유클리드 14.14 · 맨해튼 20 으로 둘 다 통과한다 — 그 둘을 나란히 잰다."""
    rows = ["." * 40 for _ in range(40)]
    rows[15] = rows[15][:17] + "~" + rows[15][18:]     # 바다 한 칸
    gm = GameMap.from_rows(rows)
    players = {0: PlayerState(pid=0, name="P0", kind="nation", start=gm.ref(5, 5))}
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    n = 0
    for y in range(40):
        for x in range(40):
            if gm.terrain[gm.ref(x, y)] != Terrain.OCEAN:
                st.gmap.owner[st.gmap.ref(x, y)] = 0
                n += 1
    st._counts = {0: n}
    st.players[0].gold = 10_000_000

    # ⚠ 바다 한 칸에는 **해안이 넷** 붙는다(상하좌우). 특정 칸을 기대하지 말고
    # "그 넷 중 하나인가"로 잰다.
    shores = {st.gmap.ref(16, 15), st.gmap.ref(18, 15),
              st.gmap.ref(17, 14), st.gmap.ref(17, 16)}
    assert all(st.gmap.is_shore(t) for t in shores), "재료: 넷 다 해안이어야 한다"

    ok = st.can_build(0, UnitType.PORT, st.gmap.ref(6, 5))
    assert ok in shores, "맨해튼 20 안인데 항구가 안 섰다"

    # (5,5) 에서 네 해안까지 맨해튼은 21·21·23·23 — 전부 20 을 넘는다
    far = st.gmap.ref(5, 5)
    for t in shores:
        d = abs(t % st.gmap.width - 5) + abs(t // st.gmap.width - 5)
        assert d > C.PORT_SPAWN_RADIUS, f"재료: {d} 가 20 이하다"
    assert st.can_build(0, UnitType.PORT, far) is None, "맨해튼 21 인데 지어졌다"
    # 대조군: 같은 자리에 도시는 선다(항구만 맨해튼 자를 쓴다)
    assert st.can_build(0, UnitType.CITY, far) is not None


def test_the_search_area_is_a_circle_not_a_square() -> None:
    """반경 15 는 **원**이다(`euclideanDistSquared < r²`).

    막지 않았으면(사각으로 두면): 모서리 (15,15) 까지 후보가 되는데 그건
    실제로 21칸이다 — 반경을 15 로 맞춰 놓고 21칸을 허용하는 셈이다.
    아래 대조군이 자리를 못 박는다: (11,11) 은 사각 안이고 원 밖이다."""
    st = make_state(width=80, height=80)
    # 내 땅은 (30,30) 한 칸뿐 — 거기서만 지을 수 있다
    st.gmap.owner[st.gmap.ref(30, 30)] = 0
    st._counts[0] = 1
    st.players[0].gold = 10_000_000

    inside = st.gmap.ref(30 + 10, 30 + 10)      # 유클리드 14.14 ≤ 15
    assert st.can_build(0, UnitType.CITY, inside) == st.gmap.ref(30, 30)

    corner = st.gmap.ref(30 + 11, 30 + 11)      # 유클리드 15.56 > 15, 사각 안
    assert max(11, 11) <= C.STRUCTURE_SEARCH_RADIUS, "재료: 사각 안이어야 한다"
    assert st.can_build(0, UnitType.CITY, corner) is None, "모서리까지 지었다"
