"""상륙 퇴각 — 떠 있는 배를 돌린다.

원본 `BoatRetreatExecution.ts` · `TransportShipExecution.ts`.

육상 퇴각은 이미 있었지만 **배는 한 번 띄우면 끝**이었다. 상륙은 병력의 절반 가까이를
한 번에 태우는 행동이라, 그 사이 판이 바뀌어도(동맹이 생기거나 목표가 이미 죽거나)
되돌릴 방법이 없다는 게 육상보다 훨씬 아프다.

원본이 정하는 세 가지:

1. **지연이 없다.** 육상 퇴각의 `cancelDelay = 20` 에 해당하는 것이 없다 —
   돌아오는 뱃길 자체가 시간이다.
2. **목적지는 한 번만 정한다**(`retreatDst ??= bestTransportShipSpawn(boat.tile())`).
   매 tick 다시 정하면 배가 움직일 때마다 목표가 흔들린다.
3. **내 땅에 닿으면 25% 를 잃는다**(`const malusForRetreat = 25`). 그리고 이건
   퇴각 전용이 아니다 — 목적지가 그 사이 내 땅이 된 배도 같은 값을 뗀다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState

# 가운데 줄만 바닷길이고 위아래는 육지다. 왼쪽 끝 두 칸이 P0, 오른쪽 끝이 P1.
#
# ⚠ **위아래 육지가 있어야 하는 이유가 있다.** 처음엔 가운데를 통째로 바다로 뒀는데,
# 그러면 P0 의 해안이 한 칸뿐이라 "퇴각 목적지를 매 tick 다시 고른다"는 변이가
# 결과를 안 바꿔 테스트를 통과했다(무동작 변이). 항로 도중에 **더 가까운 해안이
# 생길 수 있어야** 그 규칙을 잴 수 있다.
ROWS = [
    "." * 20,
    "..~~~~~~~~~~~~~~~~..",
    "." * 20,
]


def state() -> GameState:
    gm = GameMap.from_rows(ROWS)
    players = {}
    for pid, xs in ((0, (0, 1)), (1, (18, 19))):
        for x in xs:
            for y in range(3):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(xs[0], 1))
        p.kind = "human" if pid == 0 else "nation"
        p.troops = 10_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 6, 1: 6}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def launch(st: GameState):
    b = st.send_boat(0, st.gmap.ref(18, 1))
    assert b is not None
    return b


def sail(st: GameState, steps: int = 200) -> None:
    """배만 움직인다. `tick()` 을 쓰면 성장이 병력을 덮어 반환량을 못 잰다."""
    for _ in range(steps):
        if not st.boats:
            return
        st._advance_boats()


# --- 대조군 -----------------------------------------------------------------

def test_without_a_retreat_the_boat_lands():
    """막지 않았으면 무엇이 일어났는가 — 이게 없으면 아래 테스트가 공허하다."""
    st = state()
    launch(st)
    sail(st)
    assert int(st.gmap.owner[st.gmap.ref(18, 1)]) == 0, "상륙 자체가 안 됐다"


# --- 명령 -------------------------------------------------------------------

def test_retreating_turns_the_boat_around():
    st = state()
    b = launch(st)
    st._advance_boats()
    away = b.tile
    assert st.order_boat_retreat(0, b)
    st._advance_boats()
    assert b.retreating and b.replanned
    assert int(st.gmap.owner[b.dst]) == 0, "내 땅이 아닌 곳으로 물러난다"
    assert b.tile != away


def test_a_retreating_boat_never_lands():
    st = state()
    b = launch(st)
    st._advance_boats()
    st.order_boat_retreat(0, b)
    sail(st)
    assert int(st.gmap.owner[st.gmap.ref(18, 1)]) == 1, "물러났는데 상륙했다"


def test_you_cannot_retreat_someone_elses_boat():
    st = state()
    b = launch(st)
    assert st.order_boat_retreat(1, b) is False


def test_ordering_twice_changes_nothing():
    st = state()
    b = launch(st)
    assert st.order_boat_retreat(0, b)
    st._advance_boats()
    dst = b.dst
    assert st.order_boat_retreat(0, b) is False
    st._advance_boats()
    assert b.dst == dst, "두 번째 명령이 목적지를 다시 골랐다"


def test_the_destination_is_fixed_once():
    """`retreatDst ??=` — **퇴각을 시작한 위치** 기준으로 한 번만 고른다.

    돌아가는 도중에 더 가까운 해안이 생겨도 뱃머리를 다시 돌리지 않는다. 매 tick
    다시 고르게 두면 육상 전선이 움직일 때마다 배가 따라 흔들린다."""
    st = state()
    b = launch(st)
    for _ in range(8):
        st._advance_boats()
    st.order_boat_retreat(0, b)
    st._advance_boats()
    dst = b.dst
    # 배 바로 옆 해안을 P0 가 새로 얻는다 — 다시 고르면 여기로 붙는다.
    nearer = st.gmap.ref(10, 0)
    st.gmap.owner[nearer] = 0
    assert nearer != dst
    for _ in range(3):
        st._advance_boats()
        assert b.dst == dst, "돌아가는 도중에 목적지를 다시 골랐다"


# --- 병력 -------------------------------------------------------------------

def test_retreating_troops_come_back_minus_the_malus():
    st = state()
    before = st.players[0].troops
    b = launch(st)
    sent = b.troops
    assert st.players[0].troops == pytest.approx(before - sent)
    st._advance_boats()
    st.order_boat_retreat(0, b)
    sail(st)
    assert not st.boats, "배가 안 돌아왔다"
    back = st.players[0].troops - (before - sent)
    assert back == pytest.approx(sent * (1 - C.BOAT_RETREAT_MALUS_PCT))
    assert back < sent, "퇴각이 공짜다"


def test_the_malus_matches_the_original():
    assert C.BOAT_RETREAT_MALUS_PCT == 0.25


def test_arriving_at_own_territory_also_costs_the_malus():
    """퇴각 명령이 없어도, 목적지가 그 사이 내 땅이 되면 같은 값을 뗀다."""
    st = state()
    before = st.players[0].troops
    b = launch(st)
    sent = b.troops
    st.gmap.owner[st.gmap.ref(18, 1)] = 0          # 육상 부대가 먼저 먹었다
    sail(st)
    back = st.players[0].troops - (before - sent)
    assert back == pytest.approx(sent * (1 - C.BOAT_RETREAT_MALUS_PCT))


def test_a_boat_with_nowhere_to_go_returns_everyone():
    """돌아갈 해안이 없으면 원본은 **손실 없이** 병력을 돌려준다."""
    st = state()
    before = st.players[0].troops
    b = launch(st)
    sent = b.troops
    st._advance_boats()
    st.gmap.owner[st.gmap.owner == 0] = -1          # 영토를 통째로 잃었다
    st.order_boat_retreat(0, b)
    st._advance_boats()
    assert not st.boats
    back = st.players[0].troops - (before - sent)
    assert back == pytest.approx(sent), "돌아갈 곳이 없는데 병력까지 뗐다"
