"""P3 — 동맹 · 배신자 · 팀 · 금수.

배신 규칙에 함정이 하나 있다: **상대가 이미 배신자면 낙인이 안 찍힌다.** 배신자를
버리는 것은 배신이 아니라는 뜻인데, 이걸 빼면 배신자를 끊는 쪽이 같이 벌을 받는다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.attack import attack_logic
from domynion.core.buildings import DefensePostIndex
from domynion.core.diplomacy import Diplomacy
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState


def state(n: int = 3) -> GameState:
    gm = GameMap.from_rows(["." * 40] * 20)
    players = {}
    for pid in range(n):
        t = gm.ref(pid * 10, 0)
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 1 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    return st


# --- 동맹 -------------------------------------------------------------------

def test_alliance_lasts_five_minutes():
    """`allianceDuration()` = 300 × 10 tick = 5분 (10Hz)."""
    d = Diplomacy()
    al = d.form(0, 1, tick=0)
    assert al.expires_at == C.ALLIANCE_DURATION_TICKS
    assert C.ALLIANCE_DURATION_TICKS * C.TICK_DT == pytest.approx(300.0)
    assert d.allied(0, 1) and d.allied(1, 0)
    d.expire_due(C.ALLIANCE_DURATION_TICKS - 1)
    assert d.allied(0, 1)
    d.expire_due(C.ALLIANCE_DURATION_TICKS)
    assert not d.allied(0, 1)


def test_both_sides_must_agree_to_extend():
    d = Diplomacy()
    al = d.form(0, 1, tick=0)
    al.request_extension(0)
    d.expire_due(C.ALLIANCE_DURATION_TICKS)
    assert not d.allied(0, 1), "한쪽만 원해도 연장되면 안 된다"

    al2 = d.form(2, 3, tick=0)
    al2.request_extension(2)
    al2.request_extension(3)
    d.expire_due(C.ALLIANCE_DURATION_TICKS)
    assert d.allied(2, 3)
    assert al2.expires_at == C.ALLIANCE_DURATION_TICKS * 2


def test_request_accept_reject_flow():
    d = Diplomacy()
    assert d.request(0, 1)
    assert not d.request(0, 1), "같은 요청을 두 번 걸 수 없다"
    d.reject(1, 0)
    assert d.accept(1, 0, tick=5) is None, "거절된 요청은 수락할 수 없다"
    assert d.request(0, 1)
    assert d.accept(1, 0, tick=5) is not None
    assert d.allied(0, 1)


def test_cannot_request_when_already_friendly():
    d = Diplomacy()
    d.form(0, 1, tick=0)
    assert not d.request(0, 1)


# --- 팀 ---------------------------------------------------------------------

def test_same_team_is_friendly_without_an_alliance():
    d = Diplomacy(teams={0: 1, 1: 1, 2: 2})
    assert d.is_friendly(0, 1)
    assert not d.is_friendly(0, 2)
    assert not d.same_team(0, 0), "자기 자신은 '같은 팀'이 아니다(원본도 false)"


def test_no_team_means_no_team_bond():
    d = Diplomacy(teams={0: None, 1: None})
    assert not d.same_team(0, 1)


# --- 배신자 -----------------------------------------------------------------

def test_breaking_an_alliance_marks_the_breaker():
    d = Diplomacy()
    d.form(0, 1, tick=0)
    assert d.break_alliance(0, 1, tick=100)
    assert d.is_traitor(0, 100)
    assert not d.is_traitor(1, 100), "배신당한 쪽은 낙인이 없다"
    assert not d.allied(0, 1)


def test_traitor_mark_expires_after_thirty_seconds():
    d = Diplomacy()
    d.form(0, 1, tick=0)
    d.break_alliance(0, 1, tick=100)
    assert d.is_traitor(0, 100 + C.TRAITOR_DURATION_TICKS - 1)
    assert not d.is_traitor(0, 100 + C.TRAITOR_DURATION_TICKS)
    assert C.TRAITOR_DURATION_TICKS * C.TICK_DT == pytest.approx(30.0)


def test_dropping_an_existing_traitor_is_not_betrayal():
    """상대가 이미 배신자면 낙인이 안 찍힌다.

    막지 않았으면: 배신자와의 동맹을 끊는 쪽도 똑같이 벌을 받는다."""
    d = Diplomacy()
    d.form(0, 1, tick=0)
    d.break_alliance(0, 1, tick=10)          # P0 이 배신자가 된다
    d.form(1, 0, tick=20)                    # 다시 동맹
    assert d.break_alliance(1, 0, tick=30)   # P1 이 배신자 P0 을 버린다
    assert not d.is_traitor(1, 30), "배신자를 버린 것은 배신이 아니다"


def test_traitor_defends_at_half_strength():
    """`traitorDefenseDebuff` = 0.5, `traitorSpeedDebuff` = 0.8.

    막지 않았으면: 배신에 아무 비용이 없어 동맹이 의미를 잃는다."""
    gm = GameMap.from_rows(["." * 10])
    atk = PlayerState(pid=0, name="A", is_bot=False, troops=50_000.0)
    dfn = PlayerState(pid=1, name="D", is_bot=False, troops=50_000.0)
    loyal = attack_logic(gm, 0, 10_000.0, atk, dfn, 500, 500)
    traitor = attack_logic(gm, 0, 10_000.0, atk, dfn, 500, 500, defender_traitor=True)
    assert traitor.attacker_loss == pytest.approx(loyal.attacker_loss * 0.5)
    assert traitor.tiles_used == pytest.approx(loyal.tiles_used * C.TRAITOR_SPEED_DEBUFF)


# --- 엔진 배선 --------------------------------------------------------------

def test_cannot_attack_an_ally():
    st = state()
    st.diplomacy.form(0, 1, tick=0)
    st.gmap.owner[st.gmap.ref(1, 0)] = 1
    st._counts[1] = 2
    st.players[0].troops = 100_000.0
    assert st.launch_attack(0, 1) is None
    assert st.break_alliance(0, 1)
    assert st.launch_attack(0, 1) is not None, "깨고 나면 칠 수 있어야 한다"
    assert st.is_traitor(0)


def test_attack_retreats_if_an_alliance_forms_mid_attack():
    """원본은 매 tick 확인해서 친해진 상대를 치던 부대를 퇴각시킨다.

    막지 않았으면: 동맹을 맺어도 이미 출발한 부대가 계속 두들긴다."""
    st = state()
    for x in range(1, 20):
        st.gmap.owner[st.gmap.ref(x, 0)] = 1
    st._counts = {0: 1, 1: 19, 2: 1}
    st.players[0].troops = 200_000.0
    a = st.launch_attack(0, 1)
    assert a is not None
    st.tick()
    assert not a.retreated

    st.diplomacy.form(0, 1, tick=st.tick_count)
    st.tick()
    assert a.retreated and a not in st.attacks


def test_eliminated_player_loses_all_alliances():
    st = state()
    st.diplomacy.form(0, 1, tick=0)
    st._counts[1] = 0
    st.tick()
    assert not st.players[1].alive
    assert not st.diplomacy.allied(0, 1)


# --- 금수 -------------------------------------------------------------------

def test_embargo_is_one_directional():
    d = Diplomacy()
    d.start_embargo(0, 1)
    assert d.embargoed(0, 1)
    assert not d.embargoed(1, 0)
    d.stop_embargo(0, 1)
    assert not d.embargoed(0, 1)
