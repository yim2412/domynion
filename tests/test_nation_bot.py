"""원본 봇 이식 — `NationExecution` + `AiAttackBehavior`.

핵심은 **세 비율의 비대칭**이다. 중립을 먹을 때는 `expand_ratio`(10~20%)만 남기고
거의 전부 쏟는데, 사람을 칠 때는 `reserve_ratio`(30~40%)를 남긴다. 이 차이가 원본
봇의 성격을 만든다 — 빈 땅은 게걸스럽게, 사람은 여유가 있을 때만.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import (ATTACK_RATE, MIN_ATTACK_RATIO, RETAIN_FRACTION,
                                NationBot, attach)
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState


def state(rows: list[str] | None = None) -> GameState:
    gm = GameMap.from_rows(rows or ["." * 60] * 30)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(0 if pid == 0 else 59, pid)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def bot(pid: int = 0, difficulty: str = "medium", seed: int = 1) -> NationBot:
    return NationBot(pid=pid, rng=random.Random(seed), difficulty=difficulty)


# --- 비율 -------------------------------------------------------------------

def test_ratios_land_in_the_original_ranges():
    """`trigger` 50~60%, `reserve` 30~40%, `expand` 10~20%."""
    for seed in range(30):
        b = bot(seed=seed)
        assert 0.50 <= b.trigger_ratio <= 0.60
        assert 0.30 <= b.reserve_ratio <= 0.40
        assert 0.10 <= b.expand_ratio <= 0.20
        assert b.expand_ratio < b.reserve_ratio < b.trigger_ratio


def test_reaction_rate_depends_on_difficulty():
    """`getAttackRate()` — easy 는 6.5~10초, impossible 은 3~5초에 한 번만 판단한다.

    매 tick 판단하게 두면 사람이 흉내 낼 수 없는 손놀림이 된다."""
    for name, (lo, hi) in ATTACK_RATE.items():
        rates = {bot(difficulty=name, seed=s).attack_rate for s in range(40)}
        assert min(rates) >= lo and max(rates) <= hi
    assert ATTACK_RATE["easy"][0] > ATTACK_RATE["impossible"][1], \
        "쉬울수록 느리게 반응해야 한다"


# --- 병력 배분 --------------------------------------------------------------

def test_expansion_keeps_far_less_than_a_player_attack():
    """중립은 `expand_ratio`, 사람은 `reserve_ratio` 를 남긴다 — 이게 비대칭의 핵심.

    막지 않았으면(둘을 같게 두면): 빈 땅 확장이 굼떠져 봇이 초반에 자라지 못한다."""
    st = state()
    b = bot()
    p = st.players[0]
    p.troops = p.max_troops(1) * 0.9

    to_neutral = b._attack_troops(st, None)
    to_player = b._attack_troops(st, 1)
    assert to_neutral is not None and to_player is not None
    assert to_neutral > to_player
    cap = p.max_troops(1)
    assert to_neutral == pytest.approx(p.troops - cap * b.expand_ratio)
    assert to_player == pytest.approx(p.troops - cap * b.reserve_ratio)


def test_below_trigger_ratio_it_does_not_attack_at_all():
    """`trigger_ratio` 아래면 **공격을 고려조차 하지 않는다.**"""
    st = state()
    b = bot()
    p = st.players[0]
    p.troops = p.max_troops(1) * (b.trigger_ratio - 0.05)
    assert b._attack_troops(st, None) is None
    p.troops = p.max_troops(1) * (b.trigger_ratio + 0.05)
    assert b._attack_troops(st, None) is not None


def test_hard_bots_refuse_attacks_that_are_too_weak():
    """hard 이상은 상대 병력의 20% 미만이면 안 친다 — 병력만 버리는 짓이다.

    easy/medium 에는 이 제한이 없다(원본도 그렇다)."""
    st = state()
    st.players[1].troops = 10_000_000.0
    hard = bot(difficulty="hard")
    p = st.players[0]
    p.troops = p.max_troops(1) * 0.9
    assert hard._attack_troops(st, 1) is None, "약한 공격을 걸렀어야 한다"

    easy = bot(difficulty="easy")
    assert easy._attack_troops(st, 1) is not None, "easy 에는 제한이 없다"


def test_send_cap_only_applies_to_hard_and_above():
    st = state()
    assert bot(difficulty="medium")._send_cap(st) == float("inf")
    assert bot(difficulty="easy")._send_cap(st) == float("inf")
    assert "hard" in RETAIN_FRACTION and "impossible" in RETAIN_FRACTION
    assert RETAIN_FRACTION["impossible"] > RETAIN_FRACTION["hard"]


def test_bot_owning_structures_is_attacked_with_expand_ratio():
    """구조물을 가진 봇은 **평소 여유를 기다리지 않고** 친다 — 원본 주석: 뺏긴
    건물을 되찾아야 하는데 봇은 그걸 지워 버리기 때문이다."""
    from domynion.core.units import Unit, UnitType
    st = state()
    foe = st.players[1]
    foe.kind = "bot"
    foe.is_bot = True
    foe.units.units.append(Unit(UnitType.CITY, 1, tile=st.gmap.ref(59, 1)))
    b = bot()
    p = st.players[0]
    p.troops = p.max_troops(1) * 0.9
    cap = p.max_troops(1)
    assert b._attack_troops(st, 1) == pytest.approx(p.troops - cap * b.expand_ratio)


# --- 반응 주기 --------------------------------------------------------------

def test_it_only_decides_on_its_own_tick():
    st = state()
    b = bot()
    calls = []
    b._maybe_attack = lambda s: calls.append(s.tick_count)
    b._structures = lambda s: None
    for _ in range(b.attack_rate * 3):
        st.tick_count += 1
        b.tick(st)
    assert len(calls) == 3, f"{b.attack_rate}tick 마다 한 번이어야 하는데 {len(calls)}회"


def test_dead_or_finished_games_are_skipped():
    st = state()
    b = bot()
    called = []
    b._maybe_attack = lambda s: called.append(1)
    st.players[0].alive = False
    st.tick_count = b.attack_tick
    b.tick(st)
    assert not called


# --- 통합 -------------------------------------------------------------------

def test_bots_actually_expand_on_a_real_map():
    st = state(["." * 120] * 60)
    bots = attach(st, random.Random(2), "medium")
    assert len(bots) == 2
    for _ in range(600):
        st.tick()
        for b in bots:
            b.tick(st)
        if st.over:
            break
    assert st.tiles(0) > 1 or st.tiles(1) > 1, "봇이 한 칸도 못 넓혔다"
    assert st.verify_counts()


def test_attach_skips_human_players():
    st = state()
    st.players[0].kind = "human"
    bots = attach(st, random.Random(0))
    assert [b.pid for b in bots] == [1]
