"""퇴각 — 사람이 진행 중인 공격을 물린다.

동맹이 맺어졌을 때의 **자동** 퇴각만 있었고, 사람이 명령하는 퇴각이 없었다.
잘못 찍은 공격을 되돌릴 방법이 없으면 클릭 한 번이 곧 손실이 된다.

원본이 정하는 두 가지:

1. **명령과 실행이 2초 떨어져 있다**(`RetreatExecution` 의 `cancelDelay = 20`).
   즉시 물리면 되돌릴 수 없는 클릭 한 번으로 부대가 증발한다.
2. **사람을 치던 부대만 25% 를 잃는다**(`malusForRetreat`). 중립 확장은 공짜로
   무를 수 있다.

출처: `RetreatExecution.ts` · `AttackExecution.retreat()`
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


def state() -> GameState:
    gm = GameMap.from_rows(["." * 60] * 6)
    players = {}
    for pid in (0, 1):
        for x in range(pid * 20, pid * 20 + 20):
            for y in range(0, 3):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 20, 0))
        p.kind = "human" if pid == 0 else "nation"
        # ⚠ **상한 아래로 둔다.** 넘겨 두면 `_grow` 가 매 tick 큰 폭으로 깎아
        # 돌아온 병력이 그대로 사라져, 반환을 아예 안 해도 테스트가 통과한다.
        p.troops = 60_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 60, 1: 60}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def launch(st: GameState, target: int | None):
    a = st.launch_attack(0, target)
    assert a is not None
    return a


# --- 명령 -------------------------------------------------------------------

def test_ordering_a_retreat_does_not_finish_it_immediately():
    """막지 않았으면: 되돌릴 수 없는 클릭 한 번으로 부대가 증발한다."""
    st = state()
    a = launch(st, 1)
    assert st.order_retreat(0, a)
    assert a.retreating and not a.retreated
    assert a in st.attacks


def test_a_retreating_force_stops_advancing():
    """물러나는 중에 계속 진격하면 명령이 무의미하다."""
    st = state()
    a = launch(st, 1)
    st.tick()
    before = st.tiles(0)
    st.order_retreat(0, a)
    st.tick()
    assert st.tiles(0) == before, "명령 뒤에는 한 칸도 더 먹으면 안 된다"


def test_the_retreat_completes_after_two_seconds():
    st = state()
    a = launch(st, 1)
    st.order_retreat(0, a)
    for _ in range(C.RETREAT_DELAY_TICKS - 1):
        st.tick()
    assert a in st.attacks, "아직은 남아 있어야 한다"
    st.tick()
    assert a not in st.attacks
    assert a.retreated


def test_delay_matches_the_original():
    assert C.RETREAT_DELAY_TICKS * C.TICK_DT == 2.0
    assert C.RETREAT_MALUS == 0.25


def test_you_cannot_order_someone_elses_retreat():
    st = state()
    a = launch(st, 1)
    assert st.order_retreat(1, a) is False


def test_ordering_twice_changes_nothing():
    """두 번째 명령이 시계를 되감으면 영영 안 물러난다."""
    st = state()
    a = launch(st, 1)
    st.order_retreat(0, a)
    at = a.retreat_ordered_at
    st.tick()
    assert st.order_retreat(0, a) is False
    assert a.retreat_ordered_at == at


def test_my_attacks_lists_only_mine():
    st = state()
    a = launch(st, 1)
    b = st.launch_attack(1, 0)
    assert b is not None
    assert st.my_attacks(0) == [a]


# --- 병력 -------------------------------------------------------------------

def after_retreat(target: int | None) -> tuple[float, float, float]:
    """(퇴각한 판의 병력, 아무것도 안 한 판의 병력, 보낸 병력).

    ⚠ **차이를 그냥 재면 안 된다.** 병력은 매 tick 상한을 향해 움직이고, 상한을
    넘으면 오히려 줄어든다 — 그 변화가 퇴각 손실보다 커서 측정을 삼킨다.
    같은 tick 수를 돌린 대조군과 비교해야 손실만 남는다.
    """
    st = state()
    a = launch(st, target)
    sent = a.troops
    st.order_retreat(0, a)
    for _ in range(C.RETREAT_DELAY_TICKS):
        st.tick()

    base = state()                      # 공격을 아예 안 한 같은 판
    for _ in range(C.RETREAT_DELAY_TICKS):
        base.tick()
    return st.players[0].troops, base.players[0].troops, sent


def test_retreating_from_a_player_costs_a_quarter():
    got, base, sent = after_retreat(1)
    lost = base - got
    assert lost > 0, "손실이 없으면 무르는 데 값이 없다"
    assert lost == pytest.approx(sent * C.RETREAT_MALUS, rel=0.1)


def test_retreating_from_neutral_is_free():
    """잘못 찍은 확장을 취소하는 데 병력을 버려야 하면 안 된다."""
    got, base, sent = after_retreat(None)
    assert got == pytest.approx(base, rel=0.02)


def test_the_loss_is_reported():
    """병력이 조용히 사라지면 무엇 때문에 줄었는지 알 수 없다."""
    st = state()
    a = launch(st, 1)
    st.order_retreat(0, a)
    for _ in range(C.RETREAT_DELAY_TICKS):
        st.tick()
    said = [e for e in st.log.items if e.kind is EventKind.ATTACK_CANCELLED]
    assert said and said[0].who == 0 and said[0].amount > 0


def test_a_free_retreat_says_nothing():
    st = state()
    a = launch(st, None)
    st.order_retreat(0, a)
    for _ in range(C.RETREAT_DELAY_TICKS):
        st.tick()
    assert not [e for e in st.log.items
                if e.kind is EventKind.ATTACK_CANCELLED]


# --- 자동 퇴각과 섞이지 않는가 ----------------------------------------------

def test_an_alliance_still_retreats_the_force_without_a_delay():
    """동맹이 맺어지면 **즉시** 물러나야 한다 — 2초를 더 두들기면 안 된다."""
    st = state()
    a = launch(st, 1)
    st.diplomacy.form(0, 1, tick=st.tick_count)
    st.tick()
    assert a.retreated and a not in st.attacks
