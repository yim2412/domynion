"""`ai/nukes.py` — 원본 `NationNukeBehavior` 대조.

**이 파일이 생긴 이유는 우리 핵 AI 가 열 줄짜리 축소판이었다는 것이다.**
영토가 가장 큰 적을 골라 그 나라 **아무 칸에나** 쐈다 — 내 땅이 반경에 들어도,
건물이 하나도 없어도, 방금 때린 자리여도 그대로 쐈다.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nukes import (ATOM_COST_GROWTH, FFA_CROWN_THRESHOLD,
                               HYDRO_COST_GROWTH, NUKE_RECENT_MAX_AGE,
                               NUKE_TILE_VALUE, NationNukeBehavior)
from domynion.core import constants as C
from domynion.core.attack import Attack
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import NUKE_MAGNITUDES, Fallout
from domynion.core.relations import Relation
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitStore, UnitType


# --- 도구 -------------------------------------------------------------------

def state(players: int = 3, w: int = 600, h: int = 400) -> GameState:
    """⚠ 지도를 크게 잡는다. 수폭 반경이 **100** 이라 300×200 에서는 반경 검사용
    영토(반경의 4배)가 지도를 벗어난다 — §5.37 의 "상수가 지도보다 크면 그 규칙은
    검사되지 않는다"와 같은 자리다."""
    gm = GameMap.from_rows(["." * w] * h)
    ps = {}
    for pid in range(players):
        t = gm.ref(pid, 0)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in range(players)}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def behavior(pid: int = 0, difficulty: str = "medium", seed: int = 0):
    store = UnitStore()
    return NationNukeBehavior(pid, random.Random(seed), difficulty,
                              atom_cost=store.cost(UnitType.ATOM_BOMB),
                              hydro_cost=store.cost(UnitType.HYDROGEN_BOMB))


def fill(st: GameState, pid: int, x0: int, y0: int, x1: int, y1: int) -> None:
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            st.gmap.owner[st.gmap.ref(x, y)] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n


def unit(st: GameState, pid: int, utype: UnitType, x: int, y: int,
         level: int = 1) -> Unit:
    u = Unit(utype, pid, tile=st.gmap.ref(x, y), level=level)
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(utype)
    return u


def always_attack(st, pid) -> bool:
    return True


# --- 표적 나라 ---------------------------------------------------------------

def test_incoming_attack_is_the_first_priority():
    """들어오는 공격이 최우선이다(원본 주석: *"Most important!"*)."""
    st = state()
    fill(st, 1, 100, 100, 110, 110)
    fill(st, 2, 200, 100, 400, 300)            # 2번이 훨씬 크다
    st.attacks.append(Attack(attacker=1, target=0, troops=500.0))
    b = behavior()
    assert b.find_target(st).pid == 1, "더 큰 나라를 골랐다"


def test_a_much_weaker_hated_player_is_skipped():
    """미운 상대라도 **훨씬 약하면** 핵을 안 쓴다.

    원본 주석: *"we don't need nukes to deal with them"*."""
    st = state()
    fill(st, 0, 0, 0, 100, 100)                # 나는 크다
    fill(st, 1, 200, 0, 205, 5)                # 1번은 아주 작다
    st.players[0].relations.update(1, -200)    # 아주 밉다
    b = behavior()
    t = b.find_target(st)
    assert t is None or t.pid != 1, "훨씬 약한 상대에게 핵을 겨눴다"


def test_a_comparable_hated_player_is_targeted():
    """대조군 — 힘이 비슷하면 미운 상대를 겨눈다."""
    st = state()
    fill(st, 0, 0, 0, 100, 100)
    fill(st, 1, 200, 0, 300, 100)              # 비슷한 크기
    st.players[0].relations.update(1, -200)
    b = behavior()
    assert b.find_target(st).pid == 1, "비슷한 상대를 안 겨눴다"


def test_the_ffa_crown_is_targeted_when_far_ahead():
    """1등이 나보다 난이도별 문턱만큼 앞서 있으면 왕관을 친다."""
    st = state()
    total = st.gmap.land_count
    # medium 문턱은 30%
    want = int(total * 0.5)
    side = int(want ** 0.5)
    fill(st, 1, 0, 0, side, side)
    b = behavior(difficulty="medium")
    assert b.find_target(st).pid == 1, "크게 앞선 1등을 안 쳤다"


def test_the_crown_is_left_alone_when_close():
    """대조군 — 차이가 문턱 아래면 안 친다."""
    st = state()
    total = st.gmap.land_count
    side = int((total * 0.1) ** 0.5)
    fill(st, 1, 0, 0, side, side)              # 10% 정도
    b = behavior(difficulty="medium")
    assert FFA_CROWN_THRESHOLD["medium"] == 0.3
    t = b.find_target(st)
    assert t is None or t.pid != 1, "차이가 작은데 왕관을 쳤다"


def test_crown_share_ignores_fallout_land():
    """점유율의 분모는 **낙진이 없는 땅**이다.

    ⚠ 낙진 땅을 세면 판이 망가질수록 점유율이 낮게 나와 아무도 왕관을 안 친다.
    낙진을 크게 깔아 같은 영토가 문턱을 넘는지 본다."""
    def targets_crown(with_fallout: bool) -> bool:
        st = state()
        side = int((st.gmap.land_count * 0.2) ** 0.5)
        fill(st, 1, 0, 0, side, side)          # 20% — medium 문턱(30%) 아래
        if with_fallout:
            # 남은 땅의 절반쯤을 낙진으로 덮는다 → 분모가 줄어 점유율이 오른다
            # ⚠ 절반만 덮어서는 문턱을 못 넘었다(20% → 25.8%, 문턱 30%).
            # 1번 영토 **아래 전부**를 덮어 분모를 확실히 줄인다.
            st.fallout.add(list(range(side * st.gmap.width, st.gmap.size)))
        b = behavior(difficulty="medium")
        t = b.find_target(st)
        return t is not None and t.pid == 1
    assert not targets_crown(False), "문턱 아래인데 쳤다 — 재료가 잘못됐다"
    assert targets_crown(True), "낙진을 분모에서 안 뺐다"


# --- 종류 --------------------------------------------------------------------

def test_a_hydro_nation_skips_atom_bombs():
    """수폭 나라(1/3)는 원자탄을 안 쓴다 — 심한 공격을 받는 중이 아니면.

    ⚠ 이게 없으면 모든 AI 가 똑같이 싼 원자탄부터 쏜다."""
    st = state()
    p = st.players[0]
    store = UnitStore()
    p.gold = store.cost(UnitType.ATOM_BOMB)    # 원자탄만 살 수 있다
    hydro = behavior(seed=0)
    hydro.is_hydro_nation = True
    plain = behavior(seed=0)
    plain.is_hydro_nation = False
    assert plain._pick_type(st, p) is UnitType.ATOM_BOMB, "대조군이 깨졌다"
    assert hydro._pick_type(st, p) is None, "수폭 나라가 원자탄을 골랐다"


def test_a_hydro_nation_uses_atom_bombs_under_heavy_attack():
    """심한 공격을 받으면 수폭 나라도 원자탄을 쓴다."""
    st = state()
    p = st.players[0]
    p.troops = 100.0
    p.gold = UnitStore().cost(UnitType.ATOM_BOMB)
    st.attacks.append(Attack(attacker=1, target=0, troops=1000.0))
    b = behavior()
    b.is_hydro_nation = True
    assert b._pick_type(st, p) is UnitType.ATOM_BOMB


def test_perceived_cost_grows_with_each_launch():
    """쏠수록 비싸 **보인다** — 원자탄 50%, 수폭 25%.

    ⚠ 원자탄이 더 가파른 것이 규칙이다. 같은 값으로 두면 후반에도 원자탄이
    계속 매력적이라 MIRV 를 위한 저축이 안 일어난다."""
    st = state()
    st.players[0].gold = 0                     # 실비용으로 돌아가는 조건을 피한다
    b = behavior()
    a0 = b.perceived_cost(st, UnitType.ATOM_BOMB)
    h0 = b.perceived_cost(st, UnitType.HYDROGEN_BOMB)
    b._record(0, 0, UnitType.ATOM_BOMB)
    b._record(0, 0, UnitType.HYDROGEN_BOMB)
    a1 = b.perceived_cost(st, UnitType.ATOM_BOMB)
    h1 = b.perceived_cost(st, UnitType.HYDROGEN_BOMB)
    assert a1 == pytest.approx(a0 * ATOM_COST_GROWTH)
    assert h1 == pytest.approx(h0 * HYDRO_COST_GROWTH)
    assert a1 / a0 > h1 / h0, "원자탄이 더 가파르지 않다"


def test_perceived_cost_drops_to_real_when_rich():
    """MIRV + 수폭을 이미 살 수 있으면 체감 비용을 안 쓴다."""
    st = state()
    p = st.players[0]
    b = behavior()
    b._record(0, 0, UnitType.ATOM_BOMB)        # 체감 비용을 올려 둔다
    p.gold = 0
    inflated = b.perceived_cost(st, UnitType.ATOM_BOMB)
    p.gold = (p.units.cost(UnitType.MIRV)
              + p.units.cost(UnitType.HYDROGEN_BOMB) + 1)
    assert b.perceived_cost(st, UnitType.ATOM_BOMB) < inflated


# --- 타일 --------------------------------------------------------------------

def test_the_blast_must_not_touch_my_own_land():
    """반경 안이 **전부 표적의 땅**이어야 쏜다(`isValidNukeTile`).

    ⚠ 축소판은 무작위 칸을 골라 내 땅·동맹 땅을 같이 날렸다."""
    st = state()
    outer = NUKE_MAGNITUDES[UnitType.ATOM_BOMB][1]
    # ⚠ "안쪽" 칸은 **네 변 모두에서** 반경보다 멀어야 한다. 반경의 2배 지점은
    # 영토 폭이 반경의 3배면 오른쪽 변에서 반경만큼밖에 안 떨어져 있다.
    lo, hi = 100, 100 + outer * 3
    fill(st, 1, lo, lo, hi, hi)
    b = behavior()
    mid = (lo + hi) // 2
    deep = st.gmap.ref(mid, mid)                # 네 변에서 반경보다 멀다
    edge = st.gmap.ref(lo, lo)                  # 모서리
    assert b._blast_is_clean(st, deep, outer, 1), "안쪽인데 막혔다"
    assert not b._blast_is_clean(st, edge, outer, 1), "모서리인데 통과했다"


def test_hard_allows_empty_land_in_the_blast():
    """hard 이상은 **빈 땅**은 허용한다(작은 섬을 치기 위해).

    easy·medium 은 예외가 없다 — 원본 주석: *"nuke away from the border"*."""
    st = state()
    outer = NUKE_MAGNITUDES[UnitType.ATOM_BOMB][1]
    fill(st, 1, 100, 100, 110, 110)            # 작은 땅. 주변은 빈 땅이다
    tile = st.gmap.ref(105, 105)
    assert not behavior(difficulty="medium")._blast_is_clean(st, tile, outer, 1)
    assert behavior(difficulty="hard")._blast_is_clean(st, tile, outer, 1)


def test_tile_score_counts_structures_by_type_and_level():
    """건물 값 = 종류값 × 레벨. **사일로가 가장 값지다.**"""
    st = state()
    b = behavior(difficulty="easy")            # easy 는 SAM 을 안 본다
    silo = unit(st, 1, UnitType.MISSILE_SILO, 100, 100)
    city = unit(st, 1, UnitType.CITY, 200, 100)
    tile_s, tile_c = silo.tile, city.tile
    s = b.tile_score(st, tile_s, [], [silo], UnitType.ATOM_BOMB)
    c = b.tile_score(st, tile_c, [], [city], UnitType.ATOM_BOMB)
    assert s == NUKE_TILE_VALUE[UnitType.MISSILE_SILO]
    assert c == NUKE_TILE_VALUE[UnitType.CITY]
    assert s > c, "사일로가 도시보다 값지지 않다"

    lv3 = unit(st, 1, UnitType.CITY, 250, 100, level=3)
    assert b.tile_score(st, lv3.tile, [], [lv3], UnitType.ATOM_BOMB) == \
        NUKE_TILE_VALUE[UnitType.CITY] * 3, "레벨을 안 곱했다"


def test_tile_score_prefers_tiles_near_a_silo():
    """사일로에서 멀수록 깎인다 — 다만 건물 값의 20% 는 남는다."""
    st = state()
    b = behavior(difficulty="easy")
    city = unit(st, 1, UnitType.CITY, 100, 100)
    silo_near = unit(st, 0, UnitType.MISSILE_SILO, 105, 100)
    silo_far = unit(st, 0, UnitType.MISSILE_SILO, 590, 390)
    near = b.tile_score(st, city.tile, [silo_near], [city], UnitType.ATOM_BOMB)
    far = b.tile_score(st, city.tile, [silo_far], [city], UnitType.ATOM_BOMB)
    assert near > far, "거리를 안 본다"
    assert far >= NUKE_TILE_VALUE[UnitType.CITY] * 0.2, "20% 바닥이 없다"


def test_recently_hit_tiles_are_avoided():
    """방금 때린 자리는 사실상 금지다.

    ⚠ 없으면 첫 표적에 계속 붓는다. 그리고 **오래되면 잊는다**(600 tick)."""
    st = state()
    b = behavior(difficulty="easy")
    city = unit(st, 1, UnitType.CITY, 100, 100)
    base = b.tile_score(st, city.tile, [], [city], UnitType.ATOM_BOMB)
    b._record(0, city.tile, UnitType.ATOM_BOMB)
    after = b.tile_score(st, city.tile, [], [city], UnitType.ATOM_BOMB)
    assert after < base - 500_000, "최근 표적을 안 깎았다"
    b._forget_old(NUKE_RECENT_MAX_AGE + 1)
    assert b.tile_score(st, city.tile, [], [city],
                        UnitType.ATOM_BOMB) == base, "오래된 것을 안 잊었다"


def test_medium_refuses_tiles_near_a_sam():
    """medium 은 SAM 이 50 안에 있으면 아예 안 쏜다.

    대조군은 easy 다 — SAM 을 아예 안 본다."""
    st = state()
    city = unit(st, 1, UnitType.CITY, 100, 100)
    sam = unit(st, 1, UnitType.SAM_LAUNCHER, 110, 100)
    units = [city, sam]
    assert behavior(difficulty="medium").tile_score(
        st, city.tile, [], units, UnitType.ATOM_BOMB) == -1.0
    assert behavior(difficulty="easy").tile_score(
        st, city.tile, [], units, UnitType.ATOM_BOMB) > 0


# --- 통합 --------------------------------------------------------------------

def _ready(st, pid: int = 0, gold: int = 100_000_000):
    """사일로를 세우고 골드를 채운다."""
    u = unit(st, pid, UnitType.MISSILE_SILO, 5, 5)
    u.ticks_left = 0
    st.players[pid].gold = gold
    return u


def test_tribes_are_never_nuked():
    """**부족(봇)은 안 친다** — 원본 주석: *"Don't nuke tribes"*."""
    st = state()
    _ready(st)
    st.players[1].is_bot = True
    st.players[1].kind = "bot"
    fill(st, 1, 100, 100, 500, 390)            # 아주 크다 — 표적이 될 만하다
    st.players[0].relations.update(1, -200)
    b = behavior()
    assert not b.maybe_send(st, always_attack), "부족에게 핵을 쐈다"
    assert not st.nukes, "핵이 날아갔다"


def test_a_nuke_actually_launches_at_a_valid_tile():
    """통합 — 표적이 있고 골드가 되면 실제로 쏘고, **깨끗한 자리**에 떨어진다."""
    st = state()
    _ready(st)
    outer = NUKE_MAGNITUDES[UnitType.HYDROGEN_BOMB][1]
    fill(st, 1, 100, 20, 100 + outer * 3, 20 + outer * 3)
    unit(st, 1, UnitType.CITY, 100 + outer * 2, 20 + outer * 2)
    st.players[0].relations.update(1, -200)
    b = behavior(difficulty="easy")
    assert b.maybe_send(st, always_attack), "안 쐈다"
    assert st.nukes, "핵이 안 생겼다"
    dst = st.nukes[0].dst
    assert b._blast_is_clean(st, dst, outer, 1), "지저분한 자리에 쐈다"


def test_no_nuke_without_a_ready_tube():
    """관이 없으면(재장전 중) 안 쏜다 — §5.34 의 발사관 규칙."""
    st = state()
    u = _ready(st)
    u.missile_queue.append(st.tick_count)      # 하나뿐인 관이 재장전 중
    fill(st, 1, 100, 100, 400, 390)
    st.players[0].relations.update(1, -200)
    assert not behavior().maybe_send(st, always_attack)


def test_should_attack_gates_nukes_too():
    """`shouldAttack` 이 막으면 핵도 안 쏜다(§5.27 의 사람 봐주기)."""
    st = state()
    _ready(st)
    outer = NUKE_MAGNITUDES[UnitType.HYDROGEN_BOMB][1]
    fill(st, 1, 100, 20, 100 + outer * 3, 20 + outer * 3)
    st.players[0].relations.update(1, -200)
    b = behavior(difficulty="easy")
    assert not b.maybe_send(st, lambda s, pid: False), "shouldAttack 을 무시했다"


def test_no_launch_when_no_clean_tile_exists():
    """깨끗한 자리가 하나도 없으면 **안 쏜다.**

    ⚠ 앞의 통합 테스트로는 안 잡힌다 — 영토가 넓어 가운데가 늘 깨끗하고,
    거기 있는 건물이 어차피 최고점이라 반경 검사를 지워도 같은 칸이 나온다.
    영토를 반경보다 좁게 만들어 **검사가 유일한 관문**이 되게 한다."""
    st = state()
    _ready(st)
    outer = NUKE_MAGNITUDES[UnitType.HYDROGEN_BOMB][1]
    # 반경(100)보다 좁은 땅 — 어느 칸을 찍어도 반경이 밖으로 샌다
    fill(st, 1, 200, 200, 200 + outer // 2, 200 + outer // 2)
    unit(st, 1, UnitType.CITY, 200 + outer // 4, 200 + outer // 4)
    st.players[0].relations.update(1, -200)
    b = behavior(difficulty="easy")
    assert b.find_target(st) is not None, "표적이 없다 — 재료가 잘못됐다"
    assert not b.maybe_send(st, always_attack), "깨끗한 자리가 없는데 쐈다"
    assert not st.nukes


def test_structure_tiles_are_always_candidates():
    """상대 **건물이 선 칸**은 무작위 후보와 별개로 반드시 후보에 든다.

    ⚠ 무작위 10칸만 보면 넓은 영토에서 건물 위를 찍을 확률이 거의 0이다.
    영토를 크게 잡고 건물을 한 채만 둬서, 뽑힌 칸이 그 건물 자리인지 본다."""
    st = state()
    _ready(st)
    outer = NUKE_MAGNITUDES[UnitType.HYDROGEN_BOMB][1]
    lo = 50                                     # 지도 높이(400)를 안 넘게
    hi = lo + outer * 3
    fill(st, 1, lo, lo, hi, hi)
    mid = (lo + hi) // 2
    city = unit(st, 1, UnitType.CITY, mid, mid, level=9)
    st.players[0].relations.update(1, -200)
    b = behavior(difficulty="easy", seed=3)
    assert b.maybe_send(st, always_attack), "안 쐈다 — 재료가 잘못됐다"
    assert st.nukes[0].dst == city.tile, \
        "건물 자리를 안 찍었다 — 건물 타일이 후보에 없다"
