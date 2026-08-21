"""`shouldAttack` — 낮은 난이도는 사람을 봐준다.

이식 누락이었다. 우리 AI 는 사람을 나라와 똑같이 취급해서, 방치된 사람이
빨리 사라졌다. 원본은 난이도로 사람 공격 자체를 걸러낸다.

⚠ **원본 싱글 기본 난이도는 easy 다**(`DEFAULT_OPTIONS.selectedDifficulty`).
우리 기본이 medium 이었으니 두 겹으로 어긋나 있었다.

출처: `AiAttackBehavior.ts :: shouldAttack` · `NationNukeBehavior` 가 같이 본다.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import NationBot
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState


def state(target_kind: str = "human") -> GameState:
    gm = GameMap.from_rows(["." * 40] * 4)
    players = {}
    for pid, kind in ((0, target_kind), (1, "nation")):
        for x in range(pid * 5, pid * 5 + 5):
            gm.owner[gm.ref(x, 0)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 5, 0))
        p.kind = kind
        p.is_bot = kind == "bot"
        p.troops = 100_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 5, 1: 5}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def rate(difficulty: str, target_kind: str = "human",
         traitor: bool = False, n: int = 400) -> float:
    """400번 물어봐서 몇 번 치겠다고 하는가."""
    yes = 0
    for seed in range(n):
        st = state(target_kind)
        if traitor:
            st.diplomacy.form(0, 1, tick=0)
            st.diplomacy.break_alliance(0, 1, tick=st.tick_count)
            assert st.is_traitor(0)
        b = NationBot(pid=1, rng=random.Random(seed), difficulty=difficulty)
        yes += b._should_attack(st, 0)
    return yes / n


# --- 난이도별 비율 ----------------------------------------------------------

def test_easy_lets_a_human_off_three_times_out_of_four():
    """원본: `nextInt(0, 4) !== 0` 이면 거절 — 1/4 만 친다."""
    assert rate("easy") == pytest.approx(0.25, abs=0.06)


def test_medium_lets_a_human_off_one_time_in_four():
    """원본: `chance(4)` 면 거절 — `chance(n)` 은 1/n 이므로 3/4 을 친다."""
    assert rate("medium") == pytest.approx(0.75, abs=0.06)


def test_hard_and_impossible_never_hold_back():
    assert rate("hard") == 1.0
    assert rate("impossible") == 1.0


def test_the_difficulties_are_actually_ordered():
    """easy < medium < hard — 뒤집히면 난이도가 거꾸로 도는데도 각 테스트는 통과한다."""
    assert rate("easy") < rate("medium") < rate("hard")


# --- 누구를 봐주는가 --------------------------------------------------------

def test_only_humans_get_the_discount():
    """나라·봇은 easy 에서도 그냥 친다 — 안 그러면 AI 끼리 판이 멈춘다."""
    assert rate("easy", target_kind="nation") == 1.0
    assert rate("easy", target_kind="bot") == 1.0


def test_neutral_land_is_always_fair_game():
    st = state()
    b = NationBot(pid=1, rng=random.Random(0), difficulty="easy")
    assert all(b._should_attack(st, None) for _ in range(20))


def test_a_traitor_gets_no_mercy_even_on_easy():
    """막지 않았으면: easy 에서 배신에 아무 대가가 없다."""
    assert rate("easy", traitor=True) == 1.0


# --- 실제 공격 경로에 걸리는가 ----------------------------------------------

def test_the_check_actually_blocks_the_attack():
    """`_should_attack` 만 있고 `_send_attack` 이 안 보면 아무 효과가 없다."""
    blocked = 0
    for seed in range(60):
        st = state()
        st.players[1].troops = 500_000.0
        b = NationBot(pid=1, rng=random.Random(seed), difficulty="easy")
        b._send_attack(st, 0)
        blocked += not st.attacks
    assert blocked > 30, f"easy 인데 {60 - blocked}/60 이나 쳤다"


def test_the_same_check_gates_nukes():
    """막지 않았으면: easy 에서 사람을 안 치면서 핵만 떨구는 AI 가 된다."""
    import inspect

    from domynion.ai import nation as mod
    src = inspect.getsource(mod.NationBot._structures)
    assert "_should_attack" in src, "핵 경로가 봐주기를 안 본다"


# --- 기본 난이도 ------------------------------------------------------------

def test_the_default_difficulty_matches_the_original():
    """원본 싱글 기본이 easy 다. medium 이면 사람이 세 배 자주 맞는다."""
    from domynion.ui.app import DEFAULT_DIFFICULTY
    assert DEFAULT_DIFFICULTY == "easy"
