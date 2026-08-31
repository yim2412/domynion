"""봇(Tribe)의 나머지 규칙 — 이식 누락 일흔일곱~여든 (§5.78).

§5.76~§5.77 이 `AiAttackBehavior` 를 통째로 옮기고 나니, **같은 클래스를 쓰는
봇 쪽**(`TribeExecution`)이 우리에게는 따로 구현돼 있다는 것이 드러났다.
129줄을 우리 177줄과 줄 세워 보니 넷이 달랐다.

| # | 원본 | 우리 |
|---|---|---|
| **일흔일곱** | 보낼 병력 = `현재 − 상한 × 비율` (나라와 **같은 함수**) | `현재 × (1 − 비율)` |
| **일흔여덟** | `attackRandomTarget` 은 **반격부터** 본다 | 무작위 이웃부터 |
| **일흔아홉** | 동맹 **연장 요청도 전부 받아 준다** | 새 요청만 받았다 |
| **여든** | 건물 철거는 `DeleteUnitExecution` — 30초 쿨다운 · 30초 뒤 삭제 | 즉시 삭제 · 10 tick 쿨다운 |

일흔일곱이 가장 크다. 병력이 상한의 40% 인 봇이 사람을 칠 때, 우리 식은 60% 를
보내고 원본은 **한 명도 안 보낸다**(40% − 35% 가 음수).
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.tribe import BOT_ATTACK_MULTIPLE, TribeBot
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(kinds: dict[int, str]) -> GameState:
    gm = GameMap.from_rows(["." * 60] * 8)
    players = {}
    for pid, kind in kinds.items():
        for x in range(pid * 6, pid * 6 + 6):
            for y in range(0, 3):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 6, 0))
        p.kind = kind
        p.is_bot = kind == "bot"
        p.troops = 100_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 18 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def bot(pid: int = 1, seed: int = 0) -> TribeBot:
    return TribeBot(pid=pid, rng=random.Random(seed))


# --- 일흔일곱 · 공격 병력 공식 ----------------------------------------------

def test_the_amount_kept_is_a_share_of_the_cap_not_of_what_i_have():
    """⚠ **무엇의 비율인가가 달랐다.**

    막지 않았으면: 여유가 없는 봇도 늘 병력의 60~90% 를 쏟아 낸다. 원본은
    상한 기준이라 **여유가 없으면 아예 안 나간다.**"""
    st = state({0: "nation", 1: "bot"})
    b = bot(1)
    p = st.players[1]
    cap = p.max_troops(st.tiles(1))
    p.troops = cap * (b.reserve_ratio - 0.05)
    assert b._attack(st, 0) is False, "상한 대비 여유가 없는데 나갔다"
    p.troops = cap * (b.reserve_ratio + 0.3)
    assert b._attack(st, 0) is True
    sent = st.attacks[-1].troops
    assert sent == pytest.approx(cap * 0.3, rel=0.02), \
        "보낸 양이 `현재 − 상한×비율` 이 아니다"


def test_neutral_expansion_keeps_less_than_a_player_attack():
    """중립은 `expand_ratio`(10~20%)만 남긴다 — 이 비대칭이 봇의 성격이다."""
    st = state({0: "nation", 1: "bot"})
    b = bot(1)
    p = st.players[1]
    cap = p.max_troops(st.tiles(1))
    p.troops = cap * 0.9
    assert b._attack(st, None)
    to_neutral = st.attacks[-1].troops
    p.troops = cap * 0.9
    assert b._attack(st, 0)
    to_player = st.attacks[-1].troops
    assert to_neutral > to_player, "중립에 더 많이 쏟지 않는다"


def test_bots_get_at_most_four_times_their_troops():
    """봇끼리도 `calculateBotAttackTroops` 를 탄다."""
    st = state({0: "bot", 1: "bot"})
    b = bot(1)
    p = st.players[1]
    p.troops = p.max_troops(st.tiles(1)) * 0.9
    st.players[0].troops = 1_000.0
    assert b._attack(st, 0)
    assert st.attacks[-1].troops == pytest.approx(1_000.0 * BOT_ATTACK_MULTIPLE)


# --- 일흔여덟 · 반격이 먼저다 -----------------------------------------------

def test_a_bot_hits_back_at_its_biggest_attacker():
    """막지 않았으면: 얻어맞는 중에도 무작위 이웃을 고른다.

    봇이 400개인 판에서 "맞으면 맞받는다"가 통째로 없던 것이다."""
    st = state({0: "nation", 1: "bot", 2: "nation"})
    b = bot(1)
    p = st.players[1]
    p.troops = p.max_troops(st.tiles(1)) * 0.9
    for pid, troops in ((0, 20_000.0), (2, 90_000.0)):
        st.players[pid].troops = troops
        st.players[pid].attack_ratio = 1.0
        st.launch_attack(pid, 1)
    assert len(st.attacks) == 2, "재료: 둘 다 나를 치고 있어야 한다"
    assert b._biggest_incoming_attacker(st) == 2, "작은 쪽을 골랐다"

    # ⚠ **반격이 목록에 남는 것으로 재면 안 된다**(§5.88). 맞공격은 서로
    # 상쇄되므로, 봇의 반격은 2번이 보낸 공격에 흡수돼 사라진다 — 원본도
    # 그렇다. 대신 **2번의 공격이 줄었는가**로 잰다. 그게 곧 "2번을 쳤다"이다.
    def incoming(pid: int) -> float:
        return sum(a.troops for a in st.attacks if a.attacker == pid)
    before_2, before_0 = incoming(2), incoming(0)
    b._attack_random(st)
    assert incoming(2) < before_2, "가장 크게 때리는 쪽을 안 되받았다"
    assert incoming(0) == before_0, "엉뚱한 쪽을 쳤다 — 대조군이 깨졌다"


def test_a_bot_counts_bot_attackers_too():
    """⚠ 원본이 봇 공격을 거르는 조건은 *"내가 봇이 아니면"* 이다 —
    봇 자신에게는 그 필터가 안 걸린다."""
    st = state({0: "bot", 1: "bot"})
    st.players[0].troops = 50_000.0
    st.players[0].attack_ratio = 1.0
    st.launch_attack(0, 1)
    assert bot(1)._biggest_incoming_attacker(st) == 0


def test_allied_attackers_are_not_hit_back():
    st = state({0: "nation", 1: "bot"})
    st.diplomacy.form(0, 1, st.tick_count)
    st.players[0].troops = 50_000.0
    st.players[0].attack_ratio = 1.0
    st.launch_attack(0, 1)
    assert bot(1)._biggest_incoming_attacker(st) is None


# --- 일흔아홉 · 연장도 받아 준다 --------------------------------------------

def test_a_bot_agrees_to_extend_an_alliance():
    """⚠ 없으면 **봇과의 동맹은 5분 뒤 반드시 만료된다.** 사람이 연장을 눌러도
    상대가 동의하지 않아 §5.65 의 양쪽 동의가 성립하지 않는다.

    사람이 주변 봇을 우방으로 묶어 두는 구조가 5분짜리가 된다."""
    st = state({0: "human", 1: "bot"})
    al = st.diplomacy.form(0, 1, st.tick_count)
    st.extend_alliance(0, 1)                       # 사람만 동의한 상태
    assert al.only_one_agreed_to_extend
    bot(1)._accept_everything(st)
    assert not al.only_one_agreed_to_extend, "봇이 연장에 동의하지 않았다"
    assert al.expires_at == st.tick_count + C.ALLIANCE_DURATION_TICKS


def test_a_bot_does_not_agree_when_nobody_asked():
    """아무도 안 눌렀으면 그냥 둔다 — 봇이 먼저 연장을 요청하지는 않는다."""
    st = state({0: "human", 1: "bot"})
    al = st.diplomacy.form(0, 1, st.tick_count)
    bot(1)._accept_everything(st)
    assert not al.only_one_agreed_to_extend and not al.both_agreed_to_extend


def test_a_bot_still_accepts_new_requests():
    """앞부분은 그대로여야 한다."""
    st = state({0: "human", 1: "bot"})
    st.request_alliance(0, 1)
    bot(1)._accept_everything(st)
    assert st.diplomacy.allied(0, 1)


# --- 여든 · 철거는 사람과 같은 경로 -----------------------------------------

def test_deletion_goes_through_the_engine_path():
    """막지 않았으면: 봇 손의 건물이 원본보다 30배 빨리 사라지고, 사라지는
    순간까지 동작해야 하는 30초가 없어진다(§5.29)."""
    st = state({0: "human", 1: "bot"})
    p = st.players[1]
    p.units.units.append(Unit(UnitType.CITY, 1, tile=st.gmap.ref(7, 1)))
    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS
    b = bot(1)
    b._delete_a_structure(st)
    assert len(p.units.units) == 1, "그 자리에서 지웠다"
    assert p.units.units[0].marked_for_deletion
    st.tick_count += C.DELETION_MARK_DURATION_TICKS + 1
    st._advance_deletions()
    assert p.units.units == []


def test_the_cooldown_is_the_shared_constant():
    """⚠ 이 파일에만 있던 10 tick 이 30초(300 tick)와 30배 달랐다.

    상수는 한 곳에만 둔다 — `tribe.py` 안에 따로 두면 원본 값이 바뀔 때
    여기만 남는다."""
    import domynion.ai.tribe as tribe
    assert not hasattr(tribe, "DELETE_COOLDOWN_TICKS")
    assert C.DELETE_UNIT_COOLDOWN_TICKS == 300


def test_a_bot_does_not_re_mark_the_same_structure():
    st = state({0: "human", 1: "bot"})
    p = st.players[1]
    for _ in range(2):
        p.units.units.append(Unit(UnitType.CITY, 1, tile=st.gmap.ref(7, 1)))
    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS
    b = bot(1)
    b._delete_a_structure(st)
    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS
    b._delete_a_structure(st)
    assert all(u.marked_for_deletion for u in p.units.units), \
        "같은 건물을 두 번 예약했다"
