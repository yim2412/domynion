"""건물 철거 — 사람이 자기 건물을 지운다.

원본 `DeleteUnitExecution.ts` · `Config.ts :: deleteUnitCooldown / deletionMarkDuration`.

이게 없으면 **한 번 지은 것을 영영 되돌릴 수 없다.** 방어초소를 잘못 놓아 도시 자리를
막았거나, 상한에 걸린 건물이 골드를 빨아먹고 있을 때 손쓸 방법이 사라진다.

원본이 정하는 네 가지 — 우리가 놓치기 쉬운 순서대로:

1. **즉시 사라지지 않는다.** 30초 동안 "철거 예정"으로 표시된 채 **그대로 동작하다가**
   사라진다. 조기에 효과를 끄면 그 30초 동안 원본과 다른 판이 된다.
2. **골드가 안 돌아온다.** `delete()` 어디에도 환불이 없다.
3. **30초에 하나씩만.** 쿨다운 기록의 초기값이 −1 이라 판 시작 직후에도 못 쓴다.
4. **노획하면 예약이 풀린다**(`setOwner()` → `clearPendingDeletion()`).
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.events import EventKind
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state() -> GameState:
    gm = GameMap.from_rows(["." * 60] * 6)
    players = {}
    for pid in (0, 1):
        for x in range(pid * 30, pid * 30 + 30):
            for y in range(0, 3):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 30, 0))
        p.kind = "human" if pid == 0 else "nation"
        p.troops = 60_000.0
        p.gold = 10_000_000
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 90, 1: 90}
    st._posts = DefensePostIndex(gm.size)
    # 쿨다운 초기값이 −1 이라 판 시작 직후에는 못 지운다. 그 규칙 자체는 아래에서
    # 따로 재고, 나머지 테스트는 쿨다운이 지난 시점에서 시작한다.
    st.tick_count = C.DELETE_UNIT_COOLDOWN_TICKS
    return st


def a_city(st: GameState, pid: int = 0) -> Unit:
    u = st.build(pid, UnitType.CITY, st.gmap.ref(pid * 30 + 5, 1))
    assert u is not None
    while u.under_construction:
        st.tick()
    return u


# --- 예약 -------------------------------------------------------------------

def test_deleting_marks_but_does_not_remove():
    """막지 않았으면: 클릭 한 번에 건물이 증발해 무를 시간도, 남이 볼 시간도 없다."""
    st = state()
    u = a_city(st)
    assert st.delete_unit(0, u)
    assert u.marked_for_deletion
    assert u in st.players[0].units.units, "예약만 해야 하는데 바로 사라졌다"
    assert u.active


def test_a_marked_building_still_works_until_it_goes():
    """표시 기간 동안 효과를 끄면 30초짜리 유령 상태가 생긴다."""
    st = state()
    u = a_city(st)
    before = st.players[0].units.city_levels()
    st.delete_unit(0, u)
    for _ in range(C.DELETION_MARK_DURATION_TICKS):
        st.tick()
        assert st.players[0].units.city_levels() == before, "일찍 효과가 꺼졌다"


def test_it_disappears_after_the_mark_duration():
    st = state()
    u = a_city(st)
    st.delete_unit(0, u)
    for _ in range(C.DELETION_MARK_DURATION_TICKS):
        st.tick()
    assert u in st.players[0].units.units, "아직은 남아 있어야 한다"
    st.tick()
    assert u not in st.players[0].units.units
    assert not u.active
    assert st.players[0].units.owned(UnitType.CITY) == 0


def test_the_durations_match_the_original():
    assert C.DELETE_UNIT_COOLDOWN_TICKS == 30 * 10
    assert C.DELETION_MARK_DURATION_TICKS == 30 * 10


def test_deleting_logs_it():
    st = state()
    u = a_city(st)
    st.delete_unit(0, u)
    for _ in range(C.DELETION_MARK_DURATION_TICKS + 1):
        st.tick()
    assert any(e.kind is EventKind.UNIT_DELETED for e in st.log.items)


# --- 값 ---------------------------------------------------------------------

def test_no_gold_comes_back():
    """환불을 넣으면 비싼 건물을 짓고 지우기를 반복해 비용 곡선을 되감을 수 있다."""
    st = state()
    u = a_city(st)
    gold = st.players[0].gold
    st.delete_unit(0, u)
    for _ in range(C.DELETION_MARK_DURATION_TICKS + 1):
        st.tick()
    assert st.players[0].gold == gold + C.GOLD_PER_TICK_HUMAN * (
        C.DELETION_MARK_DURATION_TICKS + 1), "철거로 골드가 들어왔다"


def test_deleting_does_bring_the_price_back_down():
    """⚠ 반직관적이지만 **원본이 그렇다.**

    비용은 `min(unitsOwned, unitsConstructed)` 이고(`Config.costWrapper`), 철거는
    `unitsOwned` 만 줄인다. 그래서 도시를 지웠다 다시 지으면 값이 되돌아온다 —
    골드가 안 돌아오므로 이득은 아니지만, "값은 절대 안 내려간다"고 지레짐작해
    `constructed` 쪽을 보게 고치면 원본과 어긋난다."""
    st = state()
    u = a_city(st)
    assert st.players[0].units.cost(UnitType.CITY) == 250_000
    st.delete_unit(0, u)
    for _ in range(C.DELETION_MARK_DURATION_TICKS + 1):
        st.tick()
    assert st.players[0].units.cost(UnitType.CITY) == 125_000


# --- 관문 -------------------------------------------------------------------

def test_you_cannot_delete_at_the_very_start():
    """쿨다운 기록이 −1 이라 판 시작 직후에는 못 쓴다."""
    st = state()
    st.tick_count = 0
    u = a_city(st)
    st.tick_count = 0            # 건설을 기다리며 흐른 시간을 되돌린다
    assert st.delete_unit(0, u) is False


def test_only_one_deletion_per_cooldown():
    st = state()
    a = a_city(st)
    b = st.build(0, UnitType.CITY, st.gmap.ref(25, 1))   # 건물끼리 15칸
    assert b is not None
    assert st.delete_unit(0, a)
    assert st.delete_unit(0, b) is False, "쿨다운을 안 보고 둘 다 지웠다"
    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS
    assert st.delete_unit(0, b)


def test_you_cannot_delete_someone_elses_building():
    st = state()
    u = a_city(st, pid=1)
    assert st.delete_unit(0, u) is False


def test_you_cannot_delete_twice():
    st = state()
    u = a_city(st)
    assert st.delete_unit(0, u)
    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS
    assert st.delete_unit(0, u) is False, "두 번째 명령이 시계를 되감았다"


def test_you_cannot_delete_during_the_spawn_phase():
    st = state()
    u = a_city(st)
    st.spawn_phase = True
    assert st.delete_unit(0, u) is False


def test_you_cannot_delete_a_building_on_lost_ground():
    """`SECURITY: unit is not on player's territory` — 뺏긴 땅의 건물은 남의 것이다."""
    st = state()
    u = a_city(st)
    st.gmap.owner[u.tile] = 1
    assert st.delete_unit(0, u) is False


# --- 다른 규칙과의 맞물림 ---------------------------------------------------

def test_a_marked_building_cannot_be_upgraded():
    """지울 것에 돈을 더 넣게 두면 골드를 태우는 함정이 된다."""
    st = state()
    u = a_city(st)
    st.delete_unit(0, u)
    assert st.upgrade(0, u) == 0


def test_capturing_clears_the_pending_deletion():
    """예약을 안 지우면 노획한 건물이 내 손에서 30초 뒤에 사라진다."""
    st = state()
    u = a_city(st)
    st.delete_unit(0, u)
    st.players[1].troops = 0.0
    st._counts[1] = 1
    st._maybe_absorb(0, 1)          # P1 이 P0 에게 흡수된다 (건물은 반대 방향)
    # 방향을 맞춰 다시: P0 의 건물을 P1 이 가져가는 경우
    st2 = state()
    v = a_city(st2)
    st2.delete_unit(0, v)
    st2._counts[0] = 1
    st2._maybe_absorb(1, 0)
    assert v.owner == 1
    assert not v.marked_for_deletion, "노획한 건물이 예약을 물고 왔다"


def test_defense_posts_stop_defending_only_after_they_go():
    """방어초소는 사라진 뒤에야 사거리가 걷혀야 한다."""
    st = state()
    tile = st.gmap.ref(5, 1)
    u = st.build(0, UnitType.DEFENSE_POST, tile)
    assert u is not None
    while u.under_construction:
        st.tick()
    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS
    assert st._posts.covers(st.gmap, u.tile, 0)
    st.delete_unit(0, u)
    assert st._posts.covers(st.gmap, u.tile, 0), "예약만 했는데 사거리가 걷혔다"
    for _ in range(C.DELETION_MARK_DURATION_TICKS + 1):
        st.tick()
    assert not st._posts.covers(st.gmap, u.tile, 0)
