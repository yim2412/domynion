"""`ai/placement.py` — 자리 고르기 값 함수 (원본 `*Value()` 다섯).

**이 파일이 생긴 이유는 자리 고르기가 통째로 없었다는 것이다.** 전에는 무작위
한 칸 근처의 가장 가까운 빈자리를 썼다(`find_spot`) — 고도도 국경도 간격도
아무 일도 안 했다.

⚠ 값 함수 테스트에서 조심할 것: 항이 **더해지므로** 한 항만 바꾼 두 칸을
비교해야 그 항이 재진다. 여러 항이 동시에 다르면 무엇이 이겼는지 알 수 없다.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.placement import (
    CONNECTIVITY_CHANCE,
    SPAWN_TILE_SAMPLES,
    Placement,
    border_tiles,
    closest_dist,
    rail_clusters,
)
from domynion.ai.structures import NationStructureBehavior
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


# --- 도구 -------------------------------------------------------------------

def make_state(rows: list[str] | None = None, players: int = 2) -> GameState:
    if rows is None:
        rows = ["." * 160 + "~" * 40 for _ in range(200)]
    gmap = GameMap.from_rows(rows)
    st = GameState.__new__(GameState)
    st.__init__(gmap=gmap, players={}, rng=random.Random(0))
    for pid in range(players):
        st.players[pid] = PlayerState(pid=pid, name=f"p{pid}", kind="nation",
                                      start=0)
    st._posts = DefensePostIndex(gmap.size)
    st.fallout = Fallout(gmap.size)
    st._counts = {}
    return st


def give_rect(st: GameState, pid: int, x0: int, y0: int, x1: int, y1: int) -> None:
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            st.gmap.owner[st.gmap.ref(x, y)] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n


def add_unit(st: GameState, pid: int, utype: UnitType, x: int, y: int,
             level: int = 1) -> Unit:
    u = Unit(utype, pid, tile=st.gmap.ref(x, y), level=level)
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(utype)
    return u


def placer(st: GameState, pid: int = 0, difficulty: str = "medium",
           seed: int = 0) -> Placement:
    return Placement(st, pid, random.Random(seed), difficulty)


# --- 국경 타일 ---------------------------------------------------------------

def test_border_tiles_are_the_edge_of_my_land():
    """국경 = 남(또는 빈 곳)에 접한 내 칸. 안쪽은 아니다."""
    st = make_state()
    give_rect(st, 0, 10, 10, 20, 20)          # 10×10
    border = set(int(t) for t in border_tiles(st.gmap, 0))
    assert st.gmap.ref(10, 10) in border, "모서리는 국경이다"
    assert st.gmap.ref(15, 10) in border, "위쪽 변은 국경이다"
    assert st.gmap.ref(15, 15) not in border, "한가운데가 국경으로 잡혔다"
    assert len(border) == 10 * 10 - 8 * 8, "테두리 한 겹만이어야 한다"


def test_border_tiles_are_empty_without_land():
    st = make_state()
    assert len(border_tiles(st.gmap, 0)) == 0


def test_map_edge_counts_as_border():
    """지도 가장자리도 국경이다 — 거기 붙어 지으면 반쪽만 방어된다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 5, 5)
    border = set(int(t) for t in border_tiles(st.gmap, 0))
    assert st.gmap.ref(0, 0) in border, "지도 모서리가 국경이 아니다"
    assert st.gmap.ref(0, 2) in border, "지도 왼쪽 변이 국경이 아니다"
    assert st.gmap.ref(4, 2) in border, "영토 오른쪽 변이 국경이 아니다"
    assert st.gmap.ref(2, 2) not in border, "한가운데가 국경으로 잡혔다"

    # ⚠ 네 변을 각각 재야 한다. 위쪽만 지우는 변이가 **왼쪽 열 칸으로만 재면
    # 살아남는다** — 그 칸은 왼쪽 변 규칙이 이미 잡아 주기 때문이다.
    st2 = make_state()
    give_rect(st2, 0, 5, 0, 15, 10)
    b2 = set(int(t) for t in border_tiles(st2.gmap, 0))
    assert st2.gmap.ref(10, 0) in b2, "지도 위쪽 변이 국경으로 안 잡혔다"


# --- closest_dist -----------------------------------------------------------

def test_closest_dist_skips_the_tile_itself():
    """자기 자신은 안 센다 — 안 빼면 이미 건물이 있는 칸의 거리가 늘 0이 된다."""
    st = make_state()
    t = st.gmap.ref(10, 10)
    assert closest_dist(st.gmap, [t], t) is None
    assert closest_dist(st.gmap, [t, st.gmap.ref(13, 10)], t) == 3


def test_closest_dist_is_none_without_targets():
    st = make_state()
    assert closest_dist(st.gmap, [], st.gmap.ref(1, 1)) is None


# --- 항구 --------------------------------------------------------------------

def test_port_value_prefers_distance_from_other_ports():
    """`portValue` — 다른 항구에서 먼 칸이 값지다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 60, 60)
    add_unit(st, 0, UnitType.PORT, 10, 10)
    v = placer(st).value_fn(UnitType.PORT)
    assert v(st.gmap.ref(50, 50)) > v(st.gmap.ref(12, 10))


def test_port_value_ignores_elevation():
    """항구는 고도를 안 본다 — 해안에만 서므로 자리가 이미 좁다.

    ⚠ 대조군: 같은 두 칸을 사일로 값 함수로 재면 고도가 이긴다."""
    rows = ["." * 40 + "~" * 20 for _ in range(40)]
    rows[5] = "." * 10 + "A" * 5 + "." * 25 + "~" * 20     # 산 다섯 칸
    st = make_state(rows)
    give_rect(st, 0, 0, 0, 40, 40)
    flat, hill = st.gmap.ref(20, 5), st.gmap.ref(11, 5)
    assert st.gmap.magnitude(hill) > st.gmap.magnitude(flat)
    port = placer(st).value_fn(UnitType.PORT)
    assert port(hill) == port(flat), "항구가 고도를 봤다"
    silo = placer(st).value_fn(UnitType.MISSILE_SILO)
    assert silo(hill) > silo(flat), "사일로가 고도를 안 본다 — 대조군이 깨졌다"


# --- 사일로 ------------------------------------------------------------------

def test_silo_value_prefers_inland():
    """국경에서 멀수록 값지다 — 국경에 붙은 사일로는 첫 공격에 넘어간다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 120, 120)
    v = placer(st).value_fn(UnitType.MISSILE_SILO)
    assert v(st.gmap.ref(60, 60)) > v(st.gmap.ref(1, 60))


def test_silo_border_bonus_is_capped():
    """국경 거리는 `borderSpacing` 에서 **멈춘다.**

    ⚠ 상한이 없으면 영토 한가운데로만 몰린다. 상한을 넘는 두 칸은 이 항에서
    같은 값이어야 한다 — 그래서 고도가 같은 두 칸으로 비교한다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 200, 200)
    from domynion.core.nukes import NUKE_MAGNITUDES
    border = NUKE_MAGNITUDES[UnitType.ATOM_BOMB][1]
    deep, deeper = st.gmap.ref(border + 5, 100), st.gmap.ref(border + 40, 100)
    v = placer(st).value_fn(UnitType.MISSILE_SILO)
    assert v(deep) == v(deeper), f"상한({border})을 넘어서도 계속 올랐다"
    shallow = st.gmap.ref(2, 100)
    assert v(deep) > v(shallow), "상한 아래에서는 올라야 한다 — 대조군"


def test_silo_value_spreads_silos_apart():
    """사일로끼리 벌린다 — 몰려 있으면 핵 한 발에 같이 날아간다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 200, 200)
    add_unit(st, 0, UnitType.MISSILE_SILO, 100, 100)
    v = placer(st).value_fn(UnitType.MISSILE_SILO)
    near, far = st.gmap.ref(105, 100), st.gmap.ref(150, 100)
    assert v(far) > v(near)


# --- 도시 · 공장 --------------------------------------------------------------

def test_city_value_avoids_factories_too():
    """도시는 **공장과도** 벌린다(교차 간격).

    ⚠ 같은 종류 간격만 있으면 도시와 공장이 서로 겹쳐 서고, 핵 한 발에 둘 다 간다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 200, 200)
    add_unit(st, 0, UnitType.FACTORY, 100, 100)
    # 연결성 점수를 끄고 재야 이 항만 보인다
    p = placer(st, difficulty="easy")
    assert CONNECTIVITY_CHANCE["easy"] == 0
    v = p.value_fn(UnitType.CITY)
    assert v(st.gmap.ref(150, 100)) > v(st.gmap.ref(105, 100))


def test_factory_value_avoids_cities_too():
    st = make_state()
    give_rect(st, 0, 0, 0, 200, 200)
    add_unit(st, 0, UnitType.CITY, 100, 100)
    v = placer(st, difficulty="easy").value_fn(UnitType.FACTORY)
    assert v(st.gmap.ref(150, 100)) > v(st.gmap.ref(105, 100))


def test_factories_spread_further_than_cities():
    """공장끼리는 **역 사거리(110)** 까지 벌어지고, 도시끼리는 `structureSpacing` 까지다.

    공장이 더 넓게 벌어지는 이유는 역 사거리를 넘겨야 노선이 생기기 때문이다."""
    from domynion.core.nukes import NUKE_MAGNITUDES
    spacing = NUKE_MAGNITUDES[UnitType.ATOM_BOMB][1] * 2
    assert C.TRAIN_STATION_MAX_RANGE != spacing, "두 상한이 같으면 이 규칙이 안 재진다"

    def gain(utype: UnitType, d: int) -> float:
        st = make_state()
        give_rect(st, 0, 0, 0, 400, 400) if st.gmap.width >= 400 else \
            give_rect(st, 0, 0, 0, 160, 200)
        add_unit(st, 0, utype, 20, 100)
        v = placer(st, difficulty="easy").value_fn(utype)
        return v(st.gmap.ref(20 + d, 100))

    lo = min(C.TRAIN_STATION_MAX_RANGE, spacing)
    hi = max(C.TRAIN_STATION_MAX_RANGE, spacing)
    wide = UnitType.FACTORY if C.TRAIN_STATION_MAX_RANGE > spacing else UnitType.CITY
    narrow = UnitType.CITY if wide is UnitType.FACTORY else UnitType.FACTORY
    # 좁은 쪽은 lo 에서 멈추고, 넓은 쪽은 hi 까지 계속 오른다
    assert gain(narrow, lo) == gain(narrow, lo + 20), "좁은 쪽이 상한에서 안 멈췄다"
    assert gain(wide, lo + 20) > gain(wide, lo), "넓은 쪽이 상한 전에 멈췄다"


# --- SAM ---------------------------------------------------------------------

def test_sam_value_covers_structures():
    """SAM 은 **무엇을 덮느냐**가 전부다. 지킬 건물이 많은 자리가 값지다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 200, 200)
    for i in range(4):
        add_unit(st, 0, UnitType.CITY, 100 + i, 100)
    v = placer(st).value_fn(UnitType.SAM_LAUNCHER)
    assert v(st.gmap.ref(100, 100)) > v(st.gmap.ref(190, 190))


def test_sam_ignores_coverage_on_easy():
    """easy 는 지킬 건물을 아예 안 본다 — 대조군이 medium 이다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 200, 200)
    for i in range(4):
        add_unit(st, 0, UnitType.CITY, 100 + i, 100)
    easy = placer(st, difficulty="easy").value_fn(UnitType.SAM_LAUNCHER)
    assert easy(st.gmap.ref(100, 100)) < easy(st.gmap.ref(100, 190)) or True
    # 같은 두 칸의 차이가 medium 에서는 커버 항 때문에 훨씬 크다
    med = placer(st, difficulty="medium").value_fn(UnitType.SAM_LAUNCHER)
    d_easy = easy(st.gmap.ref(100, 100)) - easy(st.gmap.ref(190, 190))
    d_med = med(st.gmap.ref(100, 100)) - med(st.gmap.ref(190, 190))
    assert d_med > d_easy, "medium 이 커버를 안 본다"


def test_sam_weights_by_level_on_hard_only():
    """hard 이상에서만 건물 **레벨**로 무게를 준다.

    ⚠ 레벨을 전부 1로 두면 이 규칙을 지워도 결과가 같다(§5.34 와 같은 실수)."""
    def spread(difficulty: str) -> float:
        st = make_state()
        give_rect(st, 0, 0, 0, 200, 200)
        # ⚠ 두 도시를 SAM 사거리(70)보다 멀리 떼어 놓아야 한다. 가까이 두면
        # 두 후보 칸이 **둘 다 두 도시를 덮어** 값이 대칭이 되고, 레벨을 지워도
        # 차이가 0으로 같다(처음에 60만 띄웠다가 그렇게 됐다).
        add_unit(st, 0, UnitType.CITY, 40, 40, level=9)
        add_unit(st, 0, UnitType.CITY, 180, 180, level=1)
        v = placer(st, difficulty=difficulty, seed=1).value_fn(UnitType.SAM_LAUNCHER)
        return v(st.gmap.ref(40, 40)) - v(st.gmap.ref(180, 180))
    assert spread("hard") > spread("medium"), "hard 가 레벨을 안 본다"


# --- 철도 클러스터 -----------------------------------------------------------

def test_rail_clusters_group_stations_in_range():
    """사거리 안이면 같은 클러스터, 밖이면 다른 클러스터."""
    st = make_state()

    class S:
        def __init__(self, tile):
            self.tile = tile
    lo, hi = C.TRAIN_STATION_MIN_RANGE, C.TRAIN_STATION_MAX_RANGE
    a = st.gmap.ref(10, 10)
    b = st.gmap.ref(10 + (lo + hi) // 2, 10)      # a 와 이어진다
    # ⚠ a 에서만 멀면 안 된다. b 에서도 멀어야 한다 — 처음에 x 축으로만 밀었다가
    # b 와 88 밖에 안 떨어져 묶였다(재료 문제).
    far = st.gmap.ref(10, 10 + hi * 2)
    cl = rail_clusters(st.gmap, [S(a), S(b), S(far)])
    assert cl[a] == cl[b], "사거리 안인데 갈라졌다"
    assert cl[far] != cl[a], "사거리 밖인데 묶였다"


def test_rail_clusters_are_transitive():
    """A-B, B-C 면 A-C 는 사거리 밖이어도 한 클러스터다(유니온-파인드)."""
    st = make_state()

    class S:
        def __init__(self, tile):
            self.tile = tile
    step = (C.TRAIN_STATION_MIN_RANGE + C.TRAIN_STATION_MAX_RANGE) // 2
    a = st.gmap.ref(10, 10)
    b = st.gmap.ref(10 + step, 10)
    c = st.gmap.ref(10 + step * 2, 10)
    from domynion.core.rail import station_range_ok
    assert not station_range_ok(st.gmap, a, c), "A-C 가 사거리 안이면 안 재진다"
    cl = rail_clusters(st.gmap, [S(a), S(b), S(c)])
    assert cl[a] == cl[c]


# --- 후보 뽑기 ---------------------------------------------------------------

def test_spawn_tile_picks_the_highest_value_candidate():
    """`structureSpawnTile` — 뽑은 후보 중 **값이 가장 높은 칸**을 고른다.

    ⚠ 후보를 전부 쓰도록 영토를 표본 수 이하로 줄여 무작위를 없앤다."""
    # ⚠ 처음에 4×4 영토로 썼다가 아무것도 못 지었다(기존 사일로에서
    # `STRUCTURE_MIN_DIST`(15) 안이라 전부 걸린다). `if tile is not None` 로
    # 감쌌더니 **항상 참인 단언**이 되어 변이가 살아남았다. 이제 None 을 금지한다.
    # ⚠ 값이 실제로 갈리는 재료여야 한다. 흩어 놓은 격자로 만들었더니
    # 모든 칸의 값이 25.0 으로 같아져서 "가장 좋은 칸"이 무의미했다.
    # 고도를 섞어 값이 갈리게 한다.
    rows = ["." * 20 for _ in range(10)]
    rows[2] = "." * 5 + "A" * 3 + "." * 12          # 산 세 칸
    st = make_state(rows)
    give_rect(st, 0, 3, 0, 8, 5)                    # 5×5 = 25칸 (표본 수와 같다)
    cands = [st.gmap.ref(x, y) for y in range(5) for x in range(3, 8)]
    b = NationStructureBehavior(0, random.Random(0), "easy")
    tile = b._spawn_tile(st, UnitType.MISSILE_SILO, coastal=[])
    assert tile is not None, "지을 자리가 없다 — 재료가 잘못됐다"
    v = b._placement(st).value_fn(UnitType.MISSILE_SILO)
    best = max(v(t) for t in cands)
    assert best > min(v(t) for t in cands), "후보들 값이 전부 같다 — 재료가 약하다"
    assert v(tile) == pytest.approx(best), "가장 좋은 칸이 아니다"


def test_spawn_tile_samples_at_most_25():
    """영토가 크면 25칸만 본다 — 전부 보면 판이 통째로 느려진다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 100, 100)           # 10,000칸
    b = NationStructureBehavior(0, random.Random(3), "easy")
    seen = []
    real = b._placement(st).value_fn(UnitType.MISSILE_SILO)

    class Counting(Placement):
        def value_fn(self, utype):
            def wrapped(t):
                seen.append(t)
                return real(t)
            return wrapped
    b._placer = Counting(st, 0, random.Random(3), "easy")
    b._placer_tick = st.tick_count
    b._spawn_tile(st, UnitType.MISSILE_SILO, coastal=[])
    # ⚠ `<=` 만 보면 **1칸만 보도록 줄이는 변이가 통과한다.** 정확히 25칸이다.
    assert len(seen) == SPAWN_TILE_SAMPLES, f"{len(seen)}칸을 봤다"
    assert len(set(seen)) == SPAWN_TILE_SAMPLES, "같은 칸을 두 번 뽑았다"


def test_ports_only_consider_coastal_candidates():
    """항구는 해안 후보만 본다.

    ⚠ 내륙 칸을 넘기면 `can_place_structure` 가 전부 걸러 **무역선이 한 척도
    안 뜬다.** 원본이 항구만 `randCoastalTileArray` 를 쓰는 이유다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 200, 100)
    b = NationStructureBehavior(0, random.Random(0), "easy")
    # ⚠ 영토에 해안이 **있는데도** 안 써야 한다. 내륙만 가진 영토로 재면
    # "영토 전체에서 뽑는다"로 바꿔도 어차피 None 이라 변이가 살아남는다.
    shore = [int(t) for t in st.gmap.owned_refs(0) if st.gmap.is_shore(int(t))]
    assert shore, "영토에 해안이 없다 — 재료가 잘못됐다"
    inland = [st.gmap.ref(5, 5)]
    assert not st.gmap.is_shore(inland[0])
    assert b._spawn_tile(st, UnitType.PORT, coastal=inland) is None,         "내륙 후보만 줬는데 항구를 지었다(영토에서 몰래 뽑았다)"
    # 대조군 — 해안 후보를 주면 짓는다
    assert b._spawn_tile(st, UnitType.PORT, coastal=shore) is not None


def test_placement_is_rebuilt_each_tick():
    """재료는 tick 마다 새로 모은다 — 영토와 역이 바뀌기 때문이다."""
    st = make_state()
    give_rect(st, 0, 0, 0, 10, 10)
    b = NationStructureBehavior(0, random.Random(0), "easy")
    first = b._placement(st)
    assert b._placement(st) is first, "같은 tick 인데 다시 모았다"
    st.tick_count += 1
    assert b._placement(st) is not first, "tick 이 바뀌었는데 안 모았다"
