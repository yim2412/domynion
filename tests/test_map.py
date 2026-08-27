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
from domynion.core.gamemap import (SIZES, GameMap, available_maps,
                                   available_sizes)

MAPS = available_maps()


@pytest.mark.skipif(not MAPS, reason="지도 리소스가 없다")
@pytest.mark.parametrize("name", MAPS)
@pytest.mark.parametrize("size", SIZES)
def test_land_count_matches_manifest(name, size):
    """manifest 의 육지 수와 파일이 어긋나면 파싱이 틀린 것이다.

    **세 해상도를 전부 잰다.** 크기를 고르는 코드가 조용히 엉뚱한 파일을 읽으면
    육지 수가 안 맞는다."""
    if size not in available_sizes(name):
        pytest.skip(f"{name}/{size} 없음")
    gm = GameMap.load(name, size=size)
    meta = json.loads(
        (Path("resources/maps") / name / "manifest.json")
        .read_text(encoding="utf-8"))[size]
    land = int(((gm.raw & C.LAND_BIT) != 0).sum())
    assert land == meta["num_land_tiles"]
    assert gm.width * gm.height == gm.raw.size
    assert gm.width == meta["width"] and gm.height == meta["height"]


@pytest.mark.skipif(not MAPS, reason="지도 리소스가 없다")
def test_sizes_scale_by_four():
    """`map16x` → `map4x` → `map` 이 각각 변당 2배(면적 4배)다."""
    small = GameMap.load("world", size="map16x")
    mid = GameMap.load("world", size="map4x")
    full = GameMap.load("world", size="map")
    assert mid.width == small.width * 2 and mid.height == small.height * 2
    assert full.width == mid.width * 2 and full.height == mid.height * 2
    assert small.land_count < mid.land_count < full.land_count


@pytest.mark.skipif(not MAPS, reason="지도 리소스가 없다")
def test_unknown_size_fails_loudly():
    """없는 크기를 조용히 기본값으로 떨어뜨리면 어떤 지도를 재고 있는지 모르게 된다."""
    with pytest.raises(FileNotFoundError):
        GameMap.load("world", size="map64x")


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


# --- 통행 마스크 캐시 (§5.50) -------------------------------------------------

def test_the_passable_mask_is_cached():
    """같은 배열을 돌려준다. **매번 새로 만들면 판의 28% 를 여기서 쓴다**(실측).

    막지 않았으면: 원본 크기 지도에서 1,200 tick 에 11,138번, 매번 200만 칸
    불린 배열을 새로 만든다."""
    gm = GameMap.from_rows(["..." , "...", "..."])
    first = gm.passable_mask()
    assert gm.passable_mask() is first, "매번 새 배열을 만든다"


def test_terrain_changes_drop_every_derived_cache():
    """지형이 바뀌면 **파생 캐시를 전부** 버린다 — 마스크도, 바다 성분도, 접촉 성분도.

    ⚠ `_touch_cc` 는 그동안 안 버려지고 있었다(주석이 경고만 하고 있었다).
    셋을 한 함수에 모은 이유가 그것이다."""
    gm = GameMap.from_rows(["...", "...", "..."])
    before = gm.passable_mask()
    gm.ocean_components()
    gm._touch_cc[0] = frozenset({1})
    assert gm._ocean_cc is not None

    gm.invalidate_terrain_caches()
    assert gm._ocean_cc is None
    assert gm._touch_cc == {}
    assert gm.passable_mask() is not before, "마스크가 그대로 남았다"


def test_the_mask_follows_the_terrain_after_invalidation():
    """캐시가 **옛 지형을 들고 있으면 안 된다.** 값까지 확인한다."""
    gm = GameMap.from_rows(["...", "...", "..."])
    assert int(gm.passable_mask().sum()) == 9
    gm.terrain[4] = Terrain.OCEAN
    gm.invalidate_terrain_caches()
    assert int(gm.passable_mask().sum()) == 8
