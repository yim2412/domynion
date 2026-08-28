"""관계도 — 통째로 빠져 있던 이식 누락.

UI 가 아니라 규칙이다. 원본 AI 는 관계 값으로 누구를 칠지·동맹 요청을 받을지
정한다. 이게 없으면 AI 가 방금 나를 핵으로 친 상대와도 절반 확률로 손을 잡아
사람이 외교를 관리할 이유가 사라진다.

출처: `PlayerImpl.ts :: relation / updateRelation / decayRelations` + 각 Execution.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import NationBot
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.relations import (Relation, Relations,
                                     gold_donation_relation,
                                     relation_from_value)
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(n: int = 3, difficulty: str = "medium") -> GameState:
    gm = GameMap.from_rows(["." * 60] * 6)
    players = {}
    for pid in range(n):
        for x in range(pid * 6, pid * 6 + 6):
            gm.owner[gm.ref(x, 0)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 6, 0))
        p.kind = "nation"
        p.troops = 100_000.0
        p.gold = 200_000_000   # MIRV 가 25e6 부터다
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0),
                   difficulty=difficulty)
    st._counts = {pid: 6 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


# --- 값 → 등급 --------------------------------------------------------------

def test_thresholds_match_the_original():
    assert relation_from_value(-51) is Relation.HOSTILE
    assert relation_from_value(-50) is Relation.DISTRUSTFUL, "−50 은 아직 불신이다"
    assert relation_from_value(-1) is Relation.DISTRUSTFUL
    assert relation_from_value(0) is Relation.NEUTRAL
    assert relation_from_value(49) is Relation.NEUTRAL
    assert relation_from_value(50) is Relation.FRIENDLY


def test_relation_order_matters_for_comparisons():
    """AI 가 `>= NEUTRAL` 처럼 비교한다 — 순서가 뒤집히면 조건이 통째로 반대가 된다."""
    assert (Relation.HOSTILE < Relation.DISTRUSTFUL
            < Relation.NEUTRAL < Relation.FRIENDLY)


def test_value_is_clamped_to_a_hundred():
    r = Relations()
    for _ in range(5):
        r.update(1, 100)
    assert r.value(1) == C.RELATION_MAX
    for _ in range(10):
        r.update(1, -100)
    assert r.value(1) == -C.RELATION_MAX


def test_relations_are_one_directional():
    """맞은 쪽만 나빠진다. 양방향이면 친 쪽도 겁을 먹어 공세가 멈춘다."""
    st = state()
    st.launch_attack(0, 1)
    assert st.relation_of(1, 0) is Relation.HOSTILE
    assert st.relation_of(0, 1) is Relation.NEUTRAL, "친 쪽은 그대로다"


# --- 감쇠 -------------------------------------------------------------------

def test_grudges_fade():
    """막지 않았으면: 초반에 한 번 맞은 상대와 판이 끝날 때까지 화해할 수 없다."""
    r = Relations()
    r.update(1, -100)
    for _ in range(900):         # 90초 × 0.05 = 45점 회복
        r.decay()
    assert r.value(1) == pytest.approx(-55.0)
    assert r.of(1) is Relation.HOSTILE, "아직 적대다"
    for _ in range(200):
        r.decay()
    assert r.of(1) is Relation.DISTRUSTFUL, "적대 → 불신으로 풀렸다"


def test_decay_settles_exactly_on_zero():
    """0 근처에서 부호가 진동하면 관계가 영영 0 이 안 된다."""
    r = Relations()
    r.update(1, 0.07)
    r.decay()
    assert r.value(1) == 0.0


def test_decay_runs_every_tick_in_the_engine():
    st = state()
    st.players[1].relations.update(0, -100)
    before = st.players[1].relations.value(0)
    st.tick()
    assert st.players[1].relations.value(0) > before


# --- 무엇이 관계를 움직이는가 -----------------------------------------------

def test_attack_penalty_scales_with_difficulty():
    """`AttackExecution` — 어려울수록 더 오래 기억한다."""
    for diff, expect in (("easy", -60.0), ("medium", -70.0),
                         ("hard", -80.0), ("impossible", -100.0)):
        st = state(difficulty=diff)
        st.launch_attack(0, 1)
        assert st.players[1].relations.value(0) == pytest.approx(expect)


def test_alliance_lifts_both_sides_and_breaking_it_is_seen_by_neighbours():
    # P1 이 가운데라 P0·P2 둘 다와 국경을 맞댄다 — 이웃 규칙을 재려면 이 배치여야 한다.
    st = state()
    st.request_alliance(1, 0)
    st.accept_alliance(0, 1)
    assert st.relation_of(0, 1) is Relation.FRIENDLY
    assert st.relation_of(1, 0) is Relation.FRIENDLY, "동맹은 양방향이다"

    st.break_alliance(1, 0)
    # 피해자는 −100 을 받고, **이웃이기도 해서 −40 이 더 얹힌다.**
    # 원본 필터는 "피해자 제외"가 아니라 "피해자와 같은 팀이 아닌 이웃"이다.
    assert st.players[0].relations.value(1) == pytest.approx(-40.0)   # +100−100−40
    # P2 는 동맹 당사자가 아닌데도 배신을 봤다
    assert st.players[2].relations.value(1) == pytest.approx(
        C.REL_ALLIANCE_BROKEN_NEIGHBOUR)


def test_nuking_someone_only_hurts_the_victims_view():
    st = state()
    st.players[0].units.units.append(
        Unit(UnitType.MISSILE_SILO, 0, tile=st.gmap.ref(0, 0)))
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(7, 0)) is not None
    assert st.players[1].relations.value(0) == pytest.approx(C.REL_NUKED)
    assert st.players[0].relations.value(1) == pytest.approx(0.0)


def test_mirv_is_the_only_two_way_hostility():
    """MIRV 는 쏜 쪽도 상대를 적으로 확정한다 — 되돌릴 수 없는 선언이다."""
    st = state()
    st.players[0].units.units.append(
        Unit(UnitType.MISSILE_SILO, 0, tile=st.gmap.ref(0, 0)))
    assert st.launch_nuke(0, UnitType.MIRV, st.gmap.ref(7, 0)) is not None
    assert st.players[1].relations.value(0) == pytest.approx(C.REL_MIRV)
    assert st.players[0].relations.value(1) == pytest.approx(C.REL_MIRV)


def test_a_token_troop_donation_buys_nothing():
    """⚠ **이식 누락 쉰셋.** 액수와 무관하게 +50 을 주고 있었다.

    원본 주석 그대로: *"1% 만 보내 좋은 관계를 사는 것을 막는다."* 문턱은 받는 쪽
    **상한**의 1/13~1/5 사이에서 난이도별로 **무작위**로 뽑는다.

    막지 않았으면: 병력 한 줌으로 관계 +50 을 사고, 골드 쪽의 덩어리 규칙(§P3)만
    남아 **싼 쪽으로 몰린다.**"""
    st = state()
    st.diplomacy.form(0, 1, st.tick_count)        # 기부는 친한 사이만 (§5.63)
    cap = st.players[1].max_troops(st.tiles(1))
    # ⚠ 받는 쪽 병력을 **낮게** 둔다. 문턱은 상한에서 나오는데(`maxTroops`), 현재
    # 병력에서 뽑아도 여유가 크면 결과가 같아 **배선이 끊긴 채로 통과한다.**
    st.players[1].troops = cap * 0.02
    st.donate_troops(0, 1, cap / 100)             # 1% — 원본이 막으려던 바로 그것
    assert st.players[1].relations.value(0) == 0
    assert st.players[1].troops > cap * 0.02, "관계는 몰라도 병력은 갔어야 한다"


def test_a_real_troop_donation_still_pays_fifty():
    st = state()
    st.diplomacy.form(0, 1, st.tick_count)
    cap = st.players[1].max_troops(st.tiles(1))
    st.players[1].troops = cap * 0.2
    st.players[0].troops = cap                    # 보낼 만큼은 있어야 한다
    st.donate_troops(0, 1, cap / 4)               # 1/5 위 — 어느 난이도든 문턱을 넘는다
    assert st.players[1].relations.value(0) == pytest.approx(C.REL_TROOP_DONATION)


def test_troops_never_go_over_the_recipients_cap():
    """`min(troops, 상한 − 현재)`. 상한에 붙은 상대에게는 **아예 못 보낸다.**

    막지 않았으면: 동맹끼리 병력을 돌려 상한을 넘긴 군대를 만들 수 있다."""
    st = state()
    st.diplomacy.form(0, 1, st.tick_count)
    cap = st.players[1].max_troops(st.tiles(1))
    st.players[1].troops = cap
    before = st.players[0].troops
    assert st.donate_troops(0, 1, 5_000) is False
    assert st.players[0].troops == before, "보내지도 못했는데 병력이 줄었다"
    assert st.players[1].troops == cap

    st.players[1].troops = cap - 1_000
    assert st.donate_troops(0, 1, 5_000)
    assert st.players[1].troops == pytest.approx(cap), "여유분만큼만 간다"
    assert st.players[0].troops == pytest.approx(before - 1_000)


def test_gold_donation_scales_with_the_amount():

    st2 = state()
    st2.diplomacy.form(0, 1, st2.tick_count)
    st2.donate_gold(0, 1, 5_000)          # medium 덩어리 하나 = 5,000 → +5
    small = st2.players[1].relations.value(0)
    st3 = state()
    st3.diplomacy.form(0, 1, st3.tick_count)
    st3.donate_gold(0, 1, 50_000)
    assert st3.players[1].relations.value(0) > small


def test_gold_chunks_grow_over_time():
    """막지 않았으면: 후반에 남아도는 골드로 관계를 살 수 있다."""
    early = gold_donation_relation(100_000, tick=0, difficulty="medium")
    late = gold_donation_relation(100_000, tick=30_000, difficulty="medium")
    assert late < early


def test_gold_relation_is_capped():
    assert gold_donation_relation(10**9, tick=0, difficulty="medium") == 100.0


# --- 금수 -------------------------------------------------------------------

def test_embargo_penalty_is_applied_once_not_every_tick():
    """매 tick 깎으면 몇 초 만에 −100 에 박혀 풀어도 회복이 안 된다."""
    st = state()
    st.diplomacy.start_embargo(1, 0)
    st.tick()
    once = st.players[0].relations.value(1)
    assert once == pytest.approx(C.REL_EMBARGO)
    for _ in range(20):
        st.tick()
    # 감쇠 때문에 0 쪽으로 조금 올라올 뿐, 더 깎이지 않는다
    assert st.players[0].relations.value(1) > once


def test_lifting_an_embargo_gives_the_points_back():
    st = state()
    st.diplomacy.start_embargo(1, 0)
    st.tick()
    st.diplomacy.stop_embargo(1, 0)
    st.tick()
    assert st.players[0].relations.value(1) == pytest.approx(0.0, abs=0.2)


# --- AI 가 실제로 쓰는가 ----------------------------------------------------

def test_ai_refuses_an_alliance_from_someone_it_hates():
    """이 이식의 목적이다. 관계를 안 보면 핵으로 친 상대와도 반반 확률로 손잡는다."""
    st = state()
    bot = NationBot(pid=1, rng=random.Random(0), difficulty="medium")
    st.players[1].relations.update(0, -60)      # 적대
    assert bot._accepts_alliance(st, 0) is False


def test_ai_usually_accepts_from_a_friend():
    st = state()
    st.players[1].relations.update(0, 80)       # 우호
    got = sum(NationBot(pid=1, rng=random.Random(i), difficulty="medium")
              ._accepts_alliance(st, 0) for i in range(60))
    assert got > 30, "우호면 중립(반반)보다 확실히 자주 받아야 한다"


def test_ai_embargoes_hostiles_and_lifts_when_neutral_again():
    st = state()
    bot = NationBot(pid=1, rng=random.Random(0), difficulty="medium")
    st.players[1].relations.update(0, -80)
    bot._embargoes(st)
    assert st.diplomacy.embargoed(1, 0)

    st.players[1].relations.update(0, +100)     # 중립으로 회복
    bot._embargoes(st)
    assert not st.diplomacy.embargoed(1, 0)


def test_hard_ai_never_lifts_at_neutral():
    """어려울수록 한 번 틀어지면 되돌리기 어렵다."""
    st = state(difficulty="hard")
    bot = NationBot(pid=1, rng=random.Random(0), difficulty="hard")
    st.players[1].relations.update(0, -80)
    bot._embargoes(st)
    st.players[1].relations.update(0, +90)      # 중립
    bot._embargoes(st)
    assert st.diplomacy.embargoed(1, 0), "hard 는 중립으로는 안 푼다"
    st.players[1].relations.update(0, +60)      # 우호
    bot._embargoes(st)
    assert not st.diplomacy.embargoed(1, 0), "우호면 푼다"


# --- 화면에 보이는가 --------------------------------------------------------

def test_menu_shows_the_relation_that_actually_decides():
    """**상대가 나를 보는 값**을 보여야 한다. 내가 상대를 보는 값이 아니다 —
    동맹 요청이 받아들여질지 정하는 것은 상대 쪽이다."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from domynion.core.relations import RELATION_LABEL
    from domynion.ui.actions import diplomacy_items
    QApplication.instance() or QApplication([])

    st = state()
    st.players[1].relations.update(0, -80)      # 상대(P1)가 나(P0)를 적대
    st.players[0].relations.update(1, +80)      # 나는 상대를 우호로 봄
    labels = [i.label for i in diplomacy_items(st, 0, 1, lambda _m: None)]
    assert f"관계 · {RELATION_LABEL[Relation.HOSTILE]}" in labels
    assert f"관계 · {RELATION_LABEL[Relation.FRIENDLY]}" not in labels
