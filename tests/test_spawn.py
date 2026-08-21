"""시작 배치 — `SpawnExecution` + `getSpawnTiles`.

**시작 영토는 1칸이 아니라 반경 4의 원(49칸)이다.** 1칸으로 시작하면 병력 상한
공식(`타일^0.6`)의 바닥에서 출발해 초반이 지나치게 느리고, 첫 공격 한 번에 탈락할
수 있다.
"""

from __future__ import annotations

import itertools
import random

import pytest

from domynion.core.gamemap import GameMap, available_maps
from domynion.core.spawn import (MIN_DISTANCE_BETWEEN_PLAYERS, RELAX_MIN_DIST_AT,
                                 SPAWN_RADIUS, is_border_tile, pick_spawn,
                                 place_players, spawn_tiles)

MAPS = available_maps()


def big_map(w: int = 200, h: int = 120) -> GameMap:
    return GameMap.from_rows(["." * w] * h)


# --- 시작 영토 모양 ----------------------------------------------------------

def test_spawn_area_is_a_disc_of_radius_four():
    gm = big_map()
    centre = gm.ref(100, 60)
    tiles = spawn_tiles(gm, centre)
    assert tiles is not None
    assert len(tiles) == 49, f"반경 4 원은 49칸인데 {len(tiles)}칸"
    w = gm.width
    for t in tiles:
        dx, dy = t % w - 100, t // w - 60
        assert dx * dx + dy * dy <= SPAWN_RADIUS ** 2


def test_partial_disc_is_refused_whole_not_trimmed():
    """하나라도 못 쓰는 칸이 있으면 **통째로 무른다.** 걸러서 주면 해안에 반쯤
    걸친 시작점이 생겨 시작 영토가 나라마다 달라진다."""
    gm = GameMap.from_rows(["~" * 20] + ["." * 20] * 19)
    near_water = gm.ref(10, 2)
    assert spawn_tiles(gm, near_water, require_all_valid=True) is None
    loose = spawn_tiles(gm, near_water, require_all_valid=False)
    assert loose and len(loose) < 49, "느슨한 모드는 걸러서 준다"


def test_occupied_tiles_invalidate_a_spawn():
    gm = big_map()
    gm.owner[gm.ref(101, 60)] = 7
    assert spawn_tiles(gm, gm.ref(100, 60)) is None


def test_impassable_invalidates_a_spawn():
    gm = big_map()
    from domynion.core import constants as C
    from domynion.core.constants import Terrain
    t = gm.ref(102, 60)
    gm.raw[t] = C.LAND_BIT | C.IMPASSABLE_MAGNITUDE
    gm.terrain[t] = Terrain.IMPASSABLE
    assert spawn_tiles(gm, gm.ref(100, 60)) is None


def test_map_edge_is_not_a_valid_centre():
    """가장자리에서 시작하면 확장 방향이 반쪽이라 불리하다 — 원본이 `isBorder` 로 거른다."""
    gm = big_map()
    assert is_border_tile(gm, gm.ref(0, 50))
    assert is_border_tile(gm, gm.ref(50, 0))
    assert is_border_tile(gm, gm.ref(gm.width - 1, 50))
    assert not is_border_tile(gm, gm.ref(50, 50))


# --- 거리 -------------------------------------------------------------------

def test_players_keep_minimum_distance():
    """`minDistanceBetweenPlayers()` = 30 (맨해튼).

    막지 않았으면: 둘이 붙어서 시작해 한쪽이 초반에 지워진다."""
    gm = big_map(300, 200)
    spawns = place_players(gm, 4, random.Random(0))
    w = gm.width
    xy = [(c % w, c // w) for c, _ in spawns]
    for a, b in itertools.combinations(xy, 2):
        d = abs(a[0] - b[0]) + abs(a[1] - b[1])
        assert d >= MIN_DISTANCE_BETWEEN_PLAYERS, f"거리 {d}"


def test_distance_rule_relaxes_on_a_cramped_map():
    """750번을 넘기면 거리 조건을 푼다 — 좁은 지도에서 영영 못 뽑는 것을 막는다.

    막지 않았으면: 작은 지도에서 배치가 실패해 판이 아예 시작되지 않는다."""
    gm = big_map(40, 40)          # 거리 30 을 4명이 지킬 수 없는 크기
    spawns = place_players(gm, 4, random.Random(1))
    assert len(spawns) == 4
    assert RELAX_MIN_DIST_AT < 1_000


def test_spawns_do_not_overlap():
    gm = big_map(300, 200)
    spawns = place_players(gm, 5, random.Random(3))
    seen: set[int] = set()
    for _, tiles in spawns:
        assert not (seen & set(tiles)), "시작 영토가 겹쳤다"
        seen |= set(tiles)
    assert len(seen) == 5 * 49


def test_owner_array_is_written():
    gm = big_map()
    spawns = place_players(gm, 2, random.Random(4))
    for pid, (_, tiles) in enumerate(spawns):
        assert all(int(gm.owner[t]) == pid for t in tiles)


def test_pick_spawn_gives_up_rather_than_hanging():
    """육지가 없으면 None 을 돌려준다 — 무한 루프가 아니라."""
    gm = GameMap.from_rows(["~" * 30] * 30)
    assert pick_spawn(gm, random.Random(0), []) is None


# --- 실제 지도 --------------------------------------------------------------

@pytest.mark.skipif(not MAPS, reason="지도 리소스가 없다")
def test_real_map_spawns_are_full_discs():
    gm = GameMap.load("world")
    spawns = place_players(gm, 4, random.Random(1))
    assert all(len(tiles) == 49 for _, tiles in spawns)
    w = gm.width
    xy = [(c % w, c // w) for c, _ in spawns]
    for a, b in itertools.combinations(xy, 2):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) >= MIN_DISTANCE_BETWEEN_PLAYERS
