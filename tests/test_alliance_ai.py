"""동맹 판단 — 원본 `NationAllianceBehavior.getAllianceDecision`.

⚠ **이식 누락 서른셋.** 우리 것은 관문 셋짜리였고 나머지 자리를 **동전 던지기**로
때우고 있었다. 원본은 관문이 여덟이고, 그 동전 자리에 실제 판단 넷이 있다.

확률이 얽혀 있어 그대로 두면 흔들린다. **관문 함수를 하나씩 직접 부르고**,
전체 판단은 seed 를 여러 개 돌려 분포로 잰다.
"""

from __future__ import annotations

import random

from domynion.ai.alliance import (CONFUSED_ODDS, EARLYGAME,
                                  ENOUGH_ALLIANCES, FRIENDLY_REJECT_PCT,
                                  NationAllianceBehavior,
                                  TOO_MANY_ALLIANCES_SHARE)
from domynion.core import constants as C
from domynion.core.attack import Attack
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.relations import Relation
from domynion.core.state import PlayerState


def state(players: int = 4, w: int = 120, h: int = 60) -> GameState:
    gm = GameMap.from_rows(["." * w] * h)
    ps = {}
    for pid in range(players):
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation",
                              start=gm.ref(pid * 10 + 1, 1))
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    # 초반 창을 벗어난 시각에서 시작한다 — 안 그러면 8)번이 늘 먼저 걸려
    # 뒤쪽 관문을 하나도 못 잰다.
    st.tick_count = C.SPAWN_PHASE_TURNS + 10_000
    return st


def fill(st, pid, x0, y0, x1, y1):
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            st.gmap.owner[st.gmap.ref(x, y)] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n


def ab(pid=0, difficulty="medium", seed=0):
    return NationAllianceBehavior(pid, random.Random(seed), difficulty)


def allied(st, a, b):
    st.request_alliance(a, b)
    st.accept_alliance(b, a)


# --- 1) 혼란 ----------------------------------------------------------------

def test_confusion_only_affects_the_easier_difficulties():
    """easy 10% · medium 5% · hard 2.5% · **impossible 은 없다.**

    원본 주석: *"Just like dumb humans do"* — 낮은 난이도의 성격이지 버그가 아니다."""
    assert CONFUSED_ODDS["impossible"] == 0
    assert CONFUSED_ODDS["easy"] < CONFUSED_ODDS["medium"] < CONFUSED_ODDS["hard"]
    for diff in ("easy", "medium", "hard"):
        hits = sum(ab(difficulty=diff, seed=s)._confused() for s in range(200))
        assert hits > 0, f"{diff} 가 한 번도 혼란에 안 빠졌다"
    assert not any(ab(difficulty="impossible", seed=s)._confused()
                   for s in range(200))


# --- 3) 동맹이 너무 많은 상대 -------------------------------------------------

def test_a_player_with_many_alliances_is_rejected_on_hard():
    """⚠ **핵 균형 장치다.** 원본 주석: *"to make sure there are enough
    non-friendly players in the game to stop the crown with nukes"*.

    easy·medium 은 이 관문이 없다."""
    st = state(players=4)
    allied(st, 1, 2)
    allied(st, 1, 3)                             # 1번이 넷 중 둘과 동맹(0.5)
    assert TOO_MANY_ALLIANCES_SHARE["hard"] == 0.5
    assert ab(difficulty="hard")._has_too_many_alliances(st, 1)
    assert ab(difficulty="impossible")._has_too_many_alliances(st, 1)
    for diff in ("easy", "medium"):
        assert not ab(difficulty=diff)._has_too_many_alliances(st, 1), diff


def test_bots_do_not_count_toward_the_alliance_share():
    """분모가 **봇을 뺀 인원**이다. 봇을 세면 400명짜리 판에서 이 관문이 죽는다."""
    st = state(players=4)
    for pid in (2, 3):
        st.players[pid].kind = "bot"
        st.players[pid].is_bot = True
    allied(st, 1, 2)                             # 봇과의 동맹 하나
    # 사람/나라는 둘(0·1)이므로 문턱은 1건이다 — 봇을 셌다면 2건이 필요했다
    assert ab(difficulty="hard")._has_too_many_alliances(st, 1)


# --- 4) 위협이면 오히려 받는다 ------------------------------------------------

def test_a_threatening_partner_is_accepted_not_rejected():
    """**두려워서 손을 잡는다.** 이걸 거절로 옮기면 약자가 강자에게 늘 혼자 맞선다."""
    st = state()
    fill(st, 0, 0, 0, 10, 10)
    fill(st, 1, 20, 0, 30, 10)
    st.players[0].troops = 1_000.0
    st.players[1].troops = 1_000.0 * 3           # medium 문턱 2.5배 초과
    assert ab(difficulty="medium")._is_threat(st, st.players[0], st.players[1])
    # easy 는 아무도 위협으로 안 본다 — 원본 주석: "we are very dumb"
    assert not ab(difficulty="easy")._is_threat(st, st.players[0], st.players[1])

    # 관계가 나빠도(5번 관문 앞) 위협이면 받는다
    st.players[0].relations.update(1, -200)
    got = [ab(difficulty="medium", seed=s).decide(st, 1, is_response=True)
           for s in range(12)]
    assert sum(got) >= 10, f"위협적인 상대를 {sum(got)}/12 만 받았다"


def test_impossible_sees_three_kinds_of_threat():
    """impossible 은 병력·상한·타일 셋 중 하나만 걸려도 위협으로 본다."""
    st = state()
    fill(st, 0, 0, 0, 10, 10)
    fill(st, 1, 20, 0, 40, 20)                   # 타일이 훨씬 많다
    st.players[0].troops = 1_000.0
    st.players[1].troops = 1_100.0               # 병력은 1.5배가 안 된다
    b = ab(difficulty="impossible")
    assert b._is_threat(st, st.players[0], st.players[1]), "타일 지표를 안 봤다"
    # medium 은 병력만 보므로 이 상대를 위협으로 안 본다
    assert not ab(difficulty="medium")._is_threat(st, st.players[0],
                                                  st.players[1])


# --- 6) 우호 ----------------------------------------------------------------

def test_higher_difficulties_are_pickier_even_with_friends():
    """우호여도 hard 는 17%, impossible 은 33% 거절한다."""
    assert FRIENDLY_REJECT_PCT["easy"] == FRIENDLY_REJECT_PCT["medium"] == 0
    assert FRIENDLY_REJECT_PCT["hard"] < FRIENDLY_REJECT_PCT["impossible"]
    st = state()
    st.players[0].relations.update(1, 80)
    assert st.relation_of(0, 1) >= Relation.FRIENDLY
    for diff in ("easy", "medium"):
        assert all(ab(difficulty=diff, seed=s)._is_friendly_enough(st, 1)
                   for s in range(20)), diff
    got = [ab(difficulty="impossible", seed=s)._is_friendly_enough(st, 1)
           for s in range(40)]
    assert 0 < sum(got) < 40, f"impossible 이 {sum(got)}/40 — 확률이 안 걸렸다"


# --- 7) 이미 충분한 동맹 -----------------------------------------------------

def test_hard_keeps_one_neighbour_as_an_enemy():
    """hard 이상은 **이웃 전부와 동맹하지 않는다.** 이웃이 둘 이상이면 하나는
    적으로 남긴다 — 그래야 판에 전선이 남는다."""
    st = state(players=4)
    fill(st, 0, 0, 0, 20, 20)
    fill(st, 1, 20, 0, 40, 20)                   # 이웃 1
    fill(st, 2, 40, 0, 60, 20)                   # 이웃 아님(0 과 안 닿는다)
    fill(st, 3, 0, 20, 20, 40)                   # 이웃 2
    near = {p for p in st.border_targets(0) if p is not None}
    assert near == {1, 3}, near

    b = ab(difficulty="hard")
    assert not b._enough_alliances(st, 1), "동맹이 하나도 없는데 충분하다고 한다"
    allied(st, 0, 3)                             # 이웃 둘 중 하나와 이미 동맹
    assert b._enough_alliances(st, 1), "이웃 전부와 동맹하려 한다"


def test_easy_never_thinks_it_has_enough_alliances():
    st = state(players=4)
    for pid in (1, 2, 3):
        allied(st, 0, pid)
    assert not ab(difficulty="easy")._enough_alliances(st, 1)
    assert "easy" not in ENOUGH_ALLIANCES


# --- 8) 초반 ----------------------------------------------------------------

def test_the_early_game_window_closes():
    """초반에는 그냥 받아 준다 — **창과 확률이 난이도마다 다르다.**"""
    st = state()
    st.tick_count = C.SPAWN_PHASE_TURNS + 100    # 모든 난이도의 창 안
    for diff in ("easy", "medium", "hard", "impossible"):
        got = [ab(difficulty=diff, seed=s)._earlygame(st) for s in range(40)]
        assert sum(got) > 0, f"{diff} 가 초반에 한 번도 안 받았다"

    st.tick_count = C.SPAWN_PHASE_TURNS + 4_000  # 모든 창 밖
    for diff in ("easy", "medium", "hard", "impossible"):
        assert not any(ab(difficulty=diff, seed=s)._earlygame(st)
                       for s in range(40)), diff


def test_the_early_window_is_shortest_on_impossible():
    assert (EARLYGAME["impossible"][0] < EARLYGAME["medium"][0]
            <= EARLYGAME["hard"][0] < EARLYGAME["easy"][0])
    assert (EARLYGAME["impossible"][1] > EARLYGAME["hard"][1]
            > EARLYGAME["medium"][1] > EARLYGAME["easy"][1])


# --- 9) 비슷하게 강한가 -------------------------------------------------------

def test_outgoing_troops_count_on_both_sides():
    """**나가 있는 병력까지 더해서** 견준다.

    막지 않았으면: 총공세를 나간 상대가 "약하다"로 보여 동맹을 거절당한다."""
    st = state()
    fill(st, 0, 0, 0, 20, 20)
    fill(st, 1, 20, 0, 40, 20)
    st.players[0].troops = 10_000.0
    st.players[1].troops = 1_000.0               # 그대로는 한참 약하다
    b = ab(difficulty="medium", seed=3)
    assert not b._similarly_strong(st, st.players[0], st.players[1])

    st.attacks.append(Attack(attacker=1, target=2, troops=9_000.0))
    assert b._similarly_strong(st, st.players[0], st.players[1]), \
        "나가 있는 병력을 안 셌다"


def test_land_alone_is_not_enough():
    """땅만 넓고 병력이 내 절반도 안 되면 비슷하다고 보지 않는다."""
    st = state()
    fill(st, 0, 0, 0, 10, 10)
    fill(st, 1, 20, 0, 60, 40)                   # 땅은 훨씬 넓다
    st.players[0].troops = 10_000.0
    st.players[1].troops = 100.0                 # 병력은 1%
    for seed in range(12):
        assert not ab(difficulty="medium", seed=seed)._similarly_strong(
            st, st.players[0], st.players[1]), seed


# --- 순서 -------------------------------------------------------------------

def test_traitors_are_rejected_before_anything_else_helps_them():
    """배신자는 우호여도, 초반이어도 거의 항상 거절된다 — 2)번이 앞이다."""
    st = state()
    st.tick_count = C.SPAWN_PHASE_TURNS + 100    # 초반 창 안
    st.players[0].relations.update(1, 80)        # 우호
    st.diplomacy.traitor_since[1] = st.tick_count
    got = [ab(difficulty="impossible", seed=s).decide(st, 1, is_response=True)
           for s in range(40)]
    assert sum(got) <= 8, f"배신자를 {sum(got)}/40 이나 받았다"


# --- 연장 (§5.53 의 나머지 절반) ----------------------------------------------

def test_the_ai_answers_an_extension_request():
    """⚠ **이식 누락 서른셋의 나머지 절반.** `request_extension` 과
    `both_agreed_to_extend` 는 `diplomacy.py` 에 **있었는데 아무도 안 불렀다** —
    사람 쪽 버튼도, AI 쪽 동의도 없어서 모든 동맹이 예외 없이 만료됐다."""
    from domynion.ai.nation import NationBot
    st = state()
    st.players[1].kind = "human"
    st.players[0].relations.update(1, 80)        # 우호 → 받아 준다
    allied(st, 0, 1)
    al = st.diplomacy.alliances[0]
    assert not al.both_agreed_to_extend

    bot = NationBot(pid=0, rng=random.Random(0), difficulty="medium")
    bot._alliance_extensions(st)
    assert not al.both_agreed_to_extend, "아무도 요청 안 했는데 동의했다"

    al.request_extension(1)                      # 사람이 연장을 요청했다
    bot._alliance_extensions(st)
    assert al.both_agreed_to_extend, "요청에 답하지 않았다"


def test_an_extension_is_refused_for_a_traitor():
    """연장도 **같은 판단**을 쓴다 — 배신자에게는 거의 안 해 준다."""
    from domynion.ai.nation import NationBot
    refused = 0
    for seed in range(20):
        st = state()
        st.players[1].kind = "human"
        st.players[0].relations.update(1, 80)
        allied(st, 0, 1)
        st.diplomacy.traitor_since[1] = st.tick_count
        al = st.diplomacy.alliances[0]
        al.request_extension(1)
        NationBot(pid=0, rng=random.Random(seed),
                  difficulty="hard")._alliance_extensions(st)
        if not al.both_agreed_to_extend:
            refused += 1
    assert refused >= 15, f"배신자 연장을 {20 - refused}/20 이나 받아 줬다"


def test_an_expired_alliance_survives_when_both_agreed():
    """양쪽이 동의하면 만료 대신 **기간이 늘어난다.** 규칙은 있었지만 아무도
    그 상태를 만들지 못했다 — 이제 AI 가 만든다."""
    st = state()
    allied(st, 0, 1)
    al = st.diplomacy.alliances[0]
    al.request_extension(0)
    al.request_extension(1)
    st.tick_count = al.expires_at + 1
    gone = st.diplomacy.expire_due(st.tick_count)
    assert gone == [], "양쪽이 동의했는데 만료됐다"
    assert al in st.diplomacy.alliances
    assert al.expires_at > st.tick_count
