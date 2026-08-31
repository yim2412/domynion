"""땅을 잃으면 건물도 잃는다 — 원본 `PlayerExecution.tick` 앞부분.

⚠ **이식 누락 서른일곱.** 우리는 칸 주인만 바꾸고 건물은 그대로 뒀다. 그래서
영토를 통째로 뺏겨도 **도시가 원래 주인 것으로 남아 병력 상한과 수입을 계속
냈다.** §5.56 의 썩음이 만든 낙진 위 건물도 같은 상태로 남았다 — 원본 주석이
*"Anything built on it is deleted by PlayerExecution"* 이라며 그 처리를 이 자리로
미뤄 두고 있었는데, 우리에겐 그 자리가 없었다.

규칙이 종류마다 다르다:

- 칸이 **중립**이 되면(낙진·썩음) 건물은 **사라진다.**
- 칸이 **남의 것**이 되면 그 사람이 **가져간다.**
- 단 **방어초소만 부서진다** — 뺏은 쪽이 남의 방어선을 그대로 쓰면 국경이 영영
  안 밀린다.
"""

from __future__ import annotations

import random

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state() -> GameState:
    gm = GameMap.from_rows(["." * 60] * 30)
    ps = {}
    for pid in (0, 1):
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation",
                              start=gm.ref(pid * 30, 0))
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 0, 1: 0}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS * 2
    for pid in (0, 1):
        for y in range(30):
            for x in range(pid * 30, pid * 30 + 30):
                gm.owner[gm.ref(x, y)] = pid
                st._counts[pid] += 1
    return st


def put(st, pid, utype, x, y, level=1):
    u = Unit(utype, pid, tile=st.gmap.ref(x, y), level=level)
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(utype)
    return u


def take(st, taker, x, y):
    """그 칸을 `taker` 가 가져간다(-1 이면 중립이 된다)."""
    t = st.gmap.ref(x, y)
    old = int(st.gmap.owner[t])
    st.gmap.owner[t] = taker
    if old >= 0:
        st._counts[old] -= 1
    if taker >= 0:
        st._counts[taker] = st._counts.get(taker, 0) + 1


# --- 남의 것이 되면 넘어간다 ---------------------------------------------------

def test_a_captured_city_changes_hands():
    """땅을 뺏으면 **도시도 뺏는다.**

    막지 않았으면: 영토를 통째로 잃은 나라가 도시 값(병력 상한 +250,000/레벨)과
    수입을 그대로 유지한다."""
    st = state()
    u = put(st, 0, UnitType.CITY, 10, 10, level=3)
    assert st.players[0].units.owned(UnitType.CITY) == 3

    take(st, 1, 10, 10)
    st.tick()

    assert u.owner == 1 and u.active
    assert st.players[0].units.owned(UnitType.CITY) == 0
    assert st.players[1].units.owned(UnitType.CITY) == 3, "레벨까지 넘어가야 한다"


def test_ports_and_silos_change_hands_too():
    """도시만이 아니다 — 항구·사일로·공장·SAM 전부 넘어간다."""
    st = state()
    kinds = (UnitType.PORT, UnitType.MISSILE_SILO, UnitType.FACTORY,
             UnitType.SAM_LAUNCHER)
    for i, k in enumerate(kinds):
        put(st, 0, k, 10 + i * 2, 10)
        take(st, 1, 10 + i * 2, 10)
    st.tick()
    for k in kinds:
        assert st.players[1].units.owned(k) == 1, k
        assert st.players[0].units.owned(k) == 0, k


# --- 방어초소만 부서진다 -------------------------------------------------------

def test_a_captured_defense_post_is_destroyed_not_taken():
    """⚠ **방어초소만 예외다.** 뺏은 쪽이 남의 방어선을 그대로 쓰면 국경이 영영
    안 밀린다."""
    st = state()
    u = put(st, 0, UnitType.DEFENSE_POST, 10, 10)
    take(st, 1, 10, 10)
    st.tick()
    assert not u.active, "초소가 넘어갔다"
    assert st.players[1].units.owned(UnitType.DEFENSE_POST) == 0
    assert st.players[0].units.owned(UnitType.DEFENSE_POST) == 0


# --- 중립이 되면 사라진다 -------------------------------------------------------

def test_a_structure_on_neutral_land_is_destroyed():
    """칸이 **중립**이 되면(낙진·썩음) 건물은 사라진다. 아무도 안 가져간다."""
    st = state()
    u = put(st, 0, UnitType.CITY, 10, 10, level=2)
    take(st, -1, 10, 10)
    st.tick()
    assert not u.active
    assert st.players[0].units.owned(UnitType.CITY) == 0
    assert st.players[1].units.owned(UnitType.CITY) == 0


def test_rotted_land_takes_its_buildings_with_it():
    """§5.56 의 썩음이 만든 낙진 위 건물도 같이 사라진다.

    원본 주석이 그 처리를 이 자리로 미뤄 뒀다 — *"Anything built on it is
    deleted by PlayerExecution, which already removes structures standing on
    unowned land."*"""
    st = state()
    u = put(st, 0, UnitType.CITY, 5, 5)
    # 썩음과 같은 결과를 만든다: 칸이 중립이 되고 낙진이 깔린다
    take(st, -1, 5, 5)
    st.fallout.add([st.gmap.ref(5, 5)])
    st.tick()
    assert not u.active, "낙진 위 건물이 살아남았다"


# --- 안 건드려야 하는 것 -------------------------------------------------------

def test_structures_on_my_own_land_are_untouched():
    """대조군 — 내 땅 위 건물은 매 tick 그대로 있어야 한다.

    이 검사가 매 tick 도므로, 조건이 조금만 틀려도 **판이 시작하자마자 모든
    건물이 사라진다.**"""
    st = state()
    kinds = (UnitType.CITY, UnitType.PORT, UnitType.DEFENSE_POST,
             UnitType.MISSILE_SILO)
    units = [put(st, 0, k, 10 + i * 2, 10) for i, k in enumerate(kinds)]
    for _ in range(30):
        st.tick()
    assert all(u.active and u.owner == 0 for u in units)


def test_a_structure_under_construction_is_not_spared():
    """건설 중이어도 땅을 잃으면 같이 잃는다 — 원본은 종류만 보고 상태는 안 본다."""
    st = state()
    u = put(st, 0, UnitType.CITY, 10, 10)
    u.ticks_left = 100
    assert u.under_construction
    take(st, 1, 10, 10)
    st.tick()
    assert u.owner == 1


def test_a_taken_building_finishes_under_its_new_owner():
    """뺏긴 건물은 **새 주인 밑에서 완공된다**(원본 `ConstructionExecution.tick`
    의 `this.player = this.structure.owner()`).

    막지 않았으면: 짓던 사람이 완공을 가져가, 남의 땅 위에 내 도시가 선다."""
    st = state()
    u = put(st, 0, UnitType.CITY, 10, 10)
    u.ticks_left = 3
    take(st, 1, 10, 10)
    for _ in range(6):
        st.tick()
    assert not u.under_construction, "완공이 안 됐다 — 대조군이 깨졌다"
    assert u.owner == 1
    assert u in st.players[1].units.units
    assert u not in st.players[0].units.units


def test_the_new_owner_pays_more_for_the_next_city():
    """⚠ **완공 집계(`record_constructed`)도 같이 넘어가야 한다.**

    값은 `min(보유 레벨 합, 완공 수)` 로 매겨진다(§P2). 집계를 안 넘기면 뺏은
    쪽의 다음 도시 값이 **안 오른다** — 남의 도시를 뺏어 공짜로 상한을 늘리고
    값도 그대로인 셈이다.

    변이 T3(집계 누락)가 소유권만 보는 테스트로는 안 잡혔다."""
    st = state()
    base = st.players[1].units.cost(UnitType.CITY)
    for i in range(3):                           # 도시 셋을 뺏는다
        put(st, 0, UnitType.CITY, 10 + i * 2, 10)
        take(st, 1, 10 + i * 2, 10)
    st.tick()
    assert st.players[1].units.owned(UnitType.CITY) == 3
    assert st.players[1].units.cost(UnitType.CITY) > base,         "뺏은 도시가 값에 안 반영됐다 — 완공 집계를 안 넘겼다"
