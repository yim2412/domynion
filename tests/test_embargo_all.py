"""전체 금수 — 한 번에 전부 끊는다.

원본 `EmbargoAllExecution.ts` · `Config.ts :: embargoAllCooldown()`.

개별 금수는 이미 있었다. 이게 없으면 나라 72개 판에서 "무역을 끊겠다"는 결정을
71번 클릭해야 한다 — 규칙이 아니라 조작의 문제지만, 원본이 버튼을 둔 이유가 그것이다.

원본이 정하는 세 가지:

1. **봇은 건너뛴다**(`p.type() === PlayerType.Bot` 이면 continue). 지도에 봇이
   400개라 넣으면 사실상 무역 자체를 끄는 버튼이 된다.
2. **이미 걸린 상대는 다시 안 건다.** 다시 걸면 관계 −20 이 겹쳐 붙는다.
3. **10초 쿨다운**, 그리고 걸 상대가 하나도 없으면 아예 못 누른다(`canEmbargoAll`).
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState


def state(kinds=("human", "nation", "nation", "bot")) -> GameState:
    gm = GameMap.from_rows(["." * 40] * 4)
    players = {}
    for pid, kind in enumerate(kinds):
        for x in range(pid * 10, pid * 10 + 10):
            gm.owner[gm.ref(x, 0)] = pid
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", kind=kind,
                                   start=gm.ref(pid * 10, 0))
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 10 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.EMBARGO_ALL_COOLDOWN_TICKS
    return st


# --- 걸기 -------------------------------------------------------------------

def test_it_embargoes_everyone_at_once():
    st = state()
    assert st.embargo_all(0) == 2
    assert st.diplomacy.embargoed(0, 1)
    assert st.diplomacy.embargoed(0, 2)


def test_bots_are_left_alone():
    """막지 않았으면: 봇 400개까지 끊겨 내 무역선이 사실상 전멸한다."""
    st = state()
    st.embargo_all(0)
    assert not st.diplomacy.embargoed(0, 3), "봇에게도 걸었다"


def test_it_does_not_embargo_myself():
    st = state()
    st.embargo_all(0)
    assert not st.diplomacy.embargoed(0, 0)


def test_it_is_one_way():
    """금수는 내가 거는 것이다. 상대가 나를 막은 것으로 만들면 안 된다."""
    st = state()
    st.embargo_all(0)
    assert not st.diplomacy.embargoed(1, 0)


# --- 풀기 -------------------------------------------------------------------

def test_it_can_lift_them_all():
    st = state()
    st.embargo_all(0)
    st.tick_count += C.EMBARGO_ALL_COOLDOWN_TICKS
    assert st.embargo_all(0, start=False) == 2
    assert not st.diplomacy.embargoed(0, 1)


def test_re_embargoing_changes_nothing():
    """이미 걸린 것을 다시 걸면 관계 −20 이 두 번 붙는다.

    ⚠ 관계는 매 tick 0 쪽으로 감쇠한다. 절대값을 못 박으면 감쇠까지 같이 재게 되므로
    **한 번만 누른 판을 대조군으로 두고** 같은 tick 수를 흘려 비교한다."""
    control, again = state(), state()
    for st in (control, again):
        st.embargo_all(0)
        st.tick()
    again.tick_count += C.EMBARGO_ALL_COOLDOWN_TICKS
    control.tick_count += C.EMBARGO_ALL_COOLDOWN_TICKS
    assert again.embargo_all(0) == 0, "이미 걸린 상대를 또 걸었다"
    for st in (control, again):
        st.tick()
    assert (again.players[1].relations.value(0)
            == pytest.approx(control.players[1].relations.value(0)))


# --- 관문 -------------------------------------------------------------------

def test_the_cooldown_matches_the_original():
    assert C.EMBARGO_ALL_COOLDOWN_TICKS == 10 * 10


def test_the_cooldown_blocks_the_second_press():
    st = state()
    assert st.embargo_all(0) == 2
    st.tick_count += C.EMBARGO_ALL_COOLDOWN_TICKS - 1
    assert st.can_embargo_all(0) is False
    assert st.embargo_all(0, start=False) == 0, "쿨다운 중에 풀렸다"
    st.tick_count += 1
    assert st.can_embargo_all(0)


def test_you_cannot_press_it_at_the_very_start():
    """쿨다운 기록의 초기값이 −1 이라 첫 10초는 못 쓴다."""
    st = state()
    st.tick_count = 0
    assert st.can_embargo_all(0) is False


def test_you_cannot_press_it_when_only_bots_are_left():
    """`canEmbargoAll` 의 두 번째 관문 — 걸 상대가 하나도 없으면 못 누른다."""
    st = state(kinds=("human", "bot", "bot"))
    assert st.can_embargo_all(0) is False
    assert st.embargo_all(0) == 0


def test_the_dead_are_not_embargoed():
    st = state()
    st.players[1].alive = False
    assert st.embargo_all(0) == 1
    assert not st.diplomacy.embargoed(0, 1)
