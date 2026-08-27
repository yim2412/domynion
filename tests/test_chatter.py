"""AI 가 먼저 거는 잡담 — 원본 `NationEmojiBehavior.maybeSendCasualEmoji`.

⚠ **이식 누락 서른둘.** 우리 AI 는 사건에 대한 대답만 했다. 먼저 거는 말 여덟
가지가 없으면 AI 는 내가 뭘 했을 때만 반응하는 자판기가 된다.

확률이 낮아(1/16 ~ 1/10000) 그대로 두면 테스트가 흔들린다. **주사위를 고정해서
판정만 잰다** — `_chance` 를 참/거짓으로 못 박고, 조건 쪽만 움직인다.
"""

from __future__ import annotations

import random

from domynion.ai.chatter import (FIND_RAT_AFTER_TICK, FIND_RAT_LAND_SHARE,
                                 GREET_BEFORE_TICK, NationChatter,
                                 OVERWHELMED_RATIO, SMALL_ATTACK_RATIO)
from domynion.core import constants as C
from domynion.core import emoji
from domynion.core.attack import Attack
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.events import EventKind
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState


def state(w: int = 60, h: int = 30) -> GameState:
    """0 = 나라(말하는 쪽) · 1 = 사람 · 2 = 나라."""
    gm = GameMap.from_rows(["." * w] * h)
    kinds = {0: "nation", 1: "human", 2: "nation"}
    ps = {}
    for pid, kind in kinds.items():
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind=kind,
                              start=gm.ref(pid * 20 + 1, 1))
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 0 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.EMOJI_AI_INTERVAL_TICKS * 5
    return st


def fill(st, pid, x0, y0, x1, y1):
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            st.gmap.owner[st.gmap.ref(x, y)] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n


class FixedDice(NationChatter):
    """주사위를 고정한 잡담기 — **판정만 잰다.**

    ⚠ `NationChatter` 는 `__slots__` 라 인스턴스에 `_chance` 를 붙일 수 없다.
    서브클래스로 덮어쓴다(슬롯도 비워 둔다)."""

    __slots__ = ()
    always = True

    def _chance(self, n: int) -> bool:
        return type(self).always


def chatter(pid: int = 0, always: bool = True) -> NationChatter:
    cls = type("Dice", (FixedDice,), {"__slots__": (), "always": always})
    return cls(pid, random.Random(0))


def chats(st) -> list:
    return [e for e in st.log.items if e.kind is EventKind.CHAT]


# --- 위태로움 ---------------------------------------------------------------

def test_being_overwhelmed_is_broadcast():
    """들어오는 병력이 내 병력의 3배 이상이면 **전체에 대고** 비명을 지른다."""
    st = state()
    st.players[0].troops = 1_000.0
    ch = chatter()
    st.attacks.append(Attack(attacker=2, target=0,
                             troops=1_000.0 * OVERWHELMED_RATIO + 1))
    ch._overwhelmed(st, st.players[0])
    said = chats(st)
    assert said and said[-1].text in emoji.OVERWHELMED
    assert said[-1].who == 1, "사람에게 안 갔다"


def test_a_survivable_attack_says_nothing():
    """대조군 — 3배에 못 미치면 조용하다. 늘 비명을 지르면 뜻이 없다."""
    st = state()
    st.players[0].troops = 1_000.0
    st.attacks.append(Attack(attacker=2, target=0,
                             troops=1_000.0 * OVERWHELMED_RATIO - 1))
    chatter()._overwhelmed(st, st.players[0])
    assert not chats(st)


def test_a_tiny_human_attack_is_mocked():
    """사람이 내 병력의 10% 도 안 되는 병력으로 찌르면 비웃는다."""
    st = state()
    st.players[0].troops = 10_000.0
    st.attacks.append(Attack(attacker=1, target=0,
                             troops=10_000.0 * SMALL_ATTACK_RATIO - 1))
    chatter()._small_attack(st, st.players[0])
    said = chats(st)
    assert said and said[-1].text in (emoji.CONFUSED + emoji.BORED)


def test_a_tiny_attack_from_another_nation_is_ignored():
    """⚠ **사람의 공격만 본다.** AI 끼리의 작은 공격에는 아무 말도 안 한다."""
    st = state()
    st.players[0].troops = 10_000.0
    st.attacks.append(Attack(attacker=2, target=0, troops=1.0))
    chatter()._small_attack(st, st.players[0])
    assert not chats(st)


# --- 판 전체 ----------------------------------------------------------------

def test_only_the_largest_nation_congratulates():
    """판이 끝나면 **1등 나라만** 축하한다. 전원이 보내면 화면이 도배된다."""
    st = state()
    fill(st, 2, 0, 0, 40, 20)                # 2번이 1등 나라
    fill(st, 0, 40, 0, 50, 10)
    st.over, st.winner = True, 1
    assert chatter(pid=0)._congratulate(st, st.players[0]) is None
    assert not chats(st), "1등이 아닌데 축하했다"

    ch2 = chatter(pid=2)
    ch2._congratulate(st, st.players[2])
    said = chats(st)
    assert said and said[-1].text in emoji.CONGRATULATE and said[-1].who == 1


def test_congratulations_are_sent_only_once():
    """한 번뿐이다 — 원본 `gameOver` 플래그와 같은 자리."""
    st = state()
    fill(st, 0, 0, 0, 40, 20)
    st.over, st.winner = True, 1
    ch = chatter()
    ch._congratulate(st, st.players[0])
    n = len(chats(st))
    assert n == 1
    st.emojis.sent_at.clear()                # 쿨다운이 아니라 플래그로 막는지 본다
    st.emojis.ai_spoke_at.clear()
    ch._congratulate(st, st.players[0])
    assert len(chats(st)) == n, "두 번 축하했다"


def test_the_crown_brags():
    st = state()
    fill(st, 0, 0, 0, 40, 20)                # 내가 1등
    fill(st, 2, 40, 0, 50, 10)
    chatter()._brag(st, st.players[0])
    said = chats(st)
    assert said and said[-1].text in emoji.BRAG

    # 대조군 — 2등은 자랑하지 않는다
    st2 = state()
    fill(st2, 2, 0, 0, 40, 20)
    fill(st2, 0, 40, 0, 50, 10)
    chatter()._brag(st2, st2.players[0])
    assert not chats(st2)


# --- 사람에게 ---------------------------------------------------------------

def test_only_human_allies_are_charmed():
    """AI 동맹에게는 애정 표현을 하지 않는다 — 사람만 본다."""
    st = state()
    st.request_alliance(0, 2)
    st.accept_alliance(2, 0)
    chatter()._charm_allies(st, st.players[0])
    assert not chats(st), "AI 동맹에게 말을 걸었다"

    st.request_alliance(0, 1)
    st.accept_alliance(1, 0)
    chatter()._charm_allies(st, st.players[0])
    said = chats(st)
    assert said and said[-1].text in (emoji.LOVE + emoji.CHARM_ALLIES)


def test_human_traitors_are_called_clowns():
    st = state()
    st.diplomacy.traitor_since[1] = st.tick_count
    assert st.is_traitor(1)
    chatter()._annoy_traitors(st, st.players[0])
    said = chats(st)
    assert said and said[-1].text in emoji.CLOWN_POOL


def test_friendly_traitors_are_spared():
    """대조군 — 친한 사이면 놀리지 않는다."""
    st = state()
    st.diplomacy.traitor_since[1] = st.tick_count
    st.request_alliance(0, 1)
    st.accept_alliance(1, 0)
    chatter()._annoy_traitors(st, st.players[0])
    assert not chats(st)


def test_the_rat_hunt_waits_out_the_early_game():
    """⚠ **초반 10분(6,000 tick)은 안 본다** — 그때는 다들 작아서 전부 쥐가 된다."""
    st = state()
    fill(st, 1, 0, 0, 3, 3)                  # 사람이 아주 작다
    fill(st, 0, 10, 0, 50, 20)
    st.tick_count = FIND_RAT_AFTER_TICK - 1
    chatter()._find_rat(st, st.players[0])
    assert not chats(st), "초반인데 쥐를 찾았다"

    st.tick_count = FIND_RAT_AFTER_TICK
    assert st.tiles(1) < st.gmap.land_count * FIND_RAT_LAND_SHARE
    chatter()._find_rat(st, st.players[0])
    said = chats(st)
    assert said and said[-1].text in emoji.RAT


def test_greeting_only_happens_in_the_first_minute():
    st = state()
    fill(st, 0, 0, 0, 10, 10)
    fill(st, 1, 10, 0, 20, 10)               # 국경을 맞댄 사람
    st.tick_count = GREET_BEFORE_TICK + 1
    chatter()._greet(st, st.players[0])
    assert not chats(st), "1분이 지났는데 인사했다"

    st.tick_count = GREET_BEFORE_TICK
    chatter()._greet(st, st.players[0])
    said = chats(st)
    assert said and said[-1].text in emoji.GREET


def test_greeting_needs_an_actual_neighbour():
    """대조군 — 국경이 안 닿으면 인사하지 않는다."""
    st = state()
    fill(st, 0, 0, 0, 10, 10)
    fill(st, 1, 40, 0, 50, 10)               # 멀리 떨어져 있다
    st.tick_count = GREET_BEFORE_TICK
    chatter()._greet(st, st.players[0])
    assert not chats(st)


# --- 방송의 성격 ------------------------------------------------------------

def test_broadcasts_ignore_the_thirty_second_limit():
    """⚠ **전체 방송은 30초 제한을 안 받는다**(원본 `shouldSendEmoji` 가
    `AllPlayers` 면 맨 앞에서 true 를 돌려준다).

    막지 않았으면: 비명이 제한에 걸려 안 나가고, 사람은 어디가 무너지는지
    영영 알 수 없다."""
    st = state()
    ch = chatter()
    assert st.ai_broadcast(0, emoji.BRAG) is True
    assert st.ai_broadcast(0, emoji.BRAG) is True, "방송이 제한에 걸렸다"
    assert len(chats(st)) == 2

    # 대조군 — 개인에게 거는 말은 제한을 받는다
    st.log.items.clear()
    assert st.ai_emoji(0, 1, emoji.BRAG) is True
    assert st.ai_emoji(0, 1, emoji.BRAG) is False, "개인 말이 제한을 안 받는다"


def test_bots_never_chatter():
    """봇(부족)은 아예 말을 안 한다 — `shouldSendEmoji` 의 첫 관문이다."""
    st = state()
    st.players[0].kind = "bot"
    st.players[0].is_bot = True
    assert st.ai_broadcast(0, emoji.BRAG) is False
    assert not chats(st)


def test_the_whole_chain_runs_without_a_human():
    """헤드리스(사람 없음)에서도 죽지 않는다 — 밸런스 판이 이 경로를 매번 탄다."""
    st = state()
    for pid in st.players:
        st.players[pid].kind = "nation"
    fill(st, 0, 0, 0, 20, 20)
    st.tick_count = FIND_RAT_AFTER_TICK
    ch = chatter()
    ch.tick(st)
    assert not chats(st)
