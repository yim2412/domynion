"""지도 — 원본 `.bin` 파싱과 배열 표현.

여기서 틀리면 그 위 모든 측정이 조용히 무의미해진다. 특히 **magnitude 임계값**은
예외를 던지지 않고 지형 분포만 바꾸므로, 안 재면 끝까지 모른다.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pytest

from domynion.core import constants as C
from domynion.core.constants import Terrain
from domynion.core.gamemap import GameMap, available_maps

MAPS = available_maps()


@pytest.mark.skipif(not MAPS, reason="지도 리소스가 없다")
@pytest.mark.parametrize("name", MAPS)
def test_land_count_matches_manifest(name):
    """manifest 의 육지 수와 파일이 어긋나면 파싱이 틀린 것이다."""
    gm = GameMap.load(name)
    meta = json.loads(
        (Path(gm.__module__ and "resources/maps") / name / "manifest.json")
        .read_text(encoding="utf-8"))["map16x"]
    land = int(((gm.raw & C.LAND_BIT) != 0).sum())
    assert land == meta["num_land_tiles"]
    assert gm.width * gm.height == gm.raw.size


@pytest.mark.skipif(not MAPS, reason="지도 리소스가 없다")
def test_terrain_thresholds_match_original():
    """GameMap.ts :: terrainType() — magnitude <10 평야, <20 구릉, 그 외 산악.

    임계값을 하나 틀리면 지형 분포만 바뀌고 아무 예외도 안 난다."""
    gm = GameMap.load(MAPS[0])
    mag = gm.raw & C.MAGNITUDE_MASK
    land = (gm.raw & C.LAND_BIT) != 0
    assert (gm.terrain[land & (mag < 10)] == Terrain.PLAINS).all()
    assert (gm.terrain[land & (mag >= 10) & (mag < 20)] == Terrain.HIGHLAND).all()
    assert (gm.terrain[land & (mag >= 20) & (mag < 31)] == Terrain.MOUNTAIN).all()
    assert (gm.terrain[~land] == Terrain.OCEAN).all()


def test_from_rows_builds_expected_terrain():
    gm = GameMap.from_rows(["~.nA", "..#."])
    assert gm.width == 4 and gm.height == 2
    assert gm.terrain_at(0) is Terrain.OCEAN
    assert gm.terrain_at(1) is Terrain.PLAINS
    assert gm.terrain_at(2) is Terrain.HIGHLAND
    assert gm.terrain_at(3) is Terrain.MOUNTAIN
    assert gm.terrain_at(gm.ref(2, 1)) is Terrain.IMPASSABLE
    assert not gm.passable(0) and not gm.passable(gm.ref(2, 1))
    assert gm.land_count == 6      # 육지 7칸 중 통행불가 1칸을 뺀 수


def test_neighbors_do_not_wrap_across_rows():
    """평탄 배열에서 가장 흔한 버그다 — x=0 의 왼쪽 이웃이 윗줄 끝으로 샌다.

    막지 않았으면 무엇이 일어나는가: (0,1) 의 이웃에 (3,0) 이 들어간다."""
    gm = GameMap.from_rows(["....", "....", "...."])
    left_edge = gm.ref(0, 1)
    assert gm.ref(3, 0) not in gm.neighbors(left_edge)
    assert set(gm.neighbors(left_edge)) == {gm.ref(1, 1), gm.ref(0, 0), gm.ref(0, 2)}

    right_edge = gm.ref(3, 1)
    assert gm.ref(0, 2) not in gm.neighbors(right_edge)
    assert set(gm.neighbors(right_edge)) == {gm.ref(2, 1), gm.ref(3, 0), gm.ref(3, 2)}

    corner = gm.ref(0, 0)
    assert set(gm.neighbors(corner)) == {gm.ref(1, 0), gm.ref(0, 1)}


def test_ref_and_xy_round_trip():
    gm = GameMap.from_rows(["." * 7] * 5)
    for t in range(gm.size):
        assert gm.ref(*gm.xy(t)) == t


def test_starts_are_on_passable_land_and_spread_out():
    gm = GameMap.from_rows(["~~~~~~~~", "~......~", "~......~", "~~~~~~~~"])
    starts = gm.place_starts(3, random.Random(0))
    assert len(set(starts)) == 3
    assert all(gm.passable(t) for t in starts)


def test_tile_counts_is_a_full_scan():
    gm = GameMap.from_rows(["...", "..."])
    gm.owner[0] = 0
    gm.owner[1] = 0
    gm.owner[4] = 1
    counts = gm.tile_counts(2)
    assert list(counts) == [2, 1]
    assert list(gm.owned_refs(0)) == [0, 1]


@pytest.mark.skipif(not MAPS, reason="지도 리소스가 없다")
def test_loaded_map_arrays_are_writable():
    """`np.frombuffer` 는 **읽기 전용** 배열을 준다. `.copy()` 를 빼면 핵이 지형을
    바꾸는 순간 실전에서만 죽는다 — `from_rows` 로 만든 테스트 지도는 쓰기 가능해서
    이 버그를 못 잡았다(실제로 P5 를 다 짜고 실전에서야 터졌다)."""
    gm = GameMap.load(MAPS[0])
    gm.raw[0] = C.OCEAN_BIT
    gm.terrain[0] = Terrain.OCEAN
    gm.owner[0] = 3
    assert gm.raw.flags.writeable and gm.terrain.flags.writeable


@pytest.mark.skipif(not MAPS, reason="지도 리소스가 없다")
def test_owner_starts_neutral():
    gm = GameMap.load(MAPS[0])
    assert (gm.owner == -1).all()
    assert gm.owner.dtype == np.int16      # 8명이면 int8 도 되지만 여유를 둔다
