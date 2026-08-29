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
    # 스폰 면역(5초)을 지난 시점에서 시작한다 — 사람은 그전에
    # 사람을 못 친다(원본 `canAttackPlayer`).
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
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
    # ⚠ **거절당했다고 바로 다시 걸 수는 없다**(§5.82) — 같은 상대에게 30초
    # 쿨다운이다. 전에는 이 줄이 `tick=0` 으로 다시 걸고 있었다.
    assert not d.request(0, 1, tick=0), "쿨다운을 무시했다"
    assert d.request(0, 1, tick=C.ALLIANCE_REQUEST_COOLDOWN_TICKS)
    assert d.accept(1, 0, tick=C.ALLIANCE_REQUEST_COOLDOWN_TICKS) is not None
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


# --- 공격이 동맹 요청을 거절한다 (§5.58) ---------------------------------------

def _bordering(st):
    """0번과 1번이 **국경을 맞대게** 한다.

    ⚠ 이 파일의 `state()` 는 한 칸씩만 준다. 그대로면 `launch_attack` 이
    "닿지 않는다"로 실패해 **거절 규칙까지 가지도 못한다.**"""
    for y in range(0, 10):
        for x in range(0, 10):
            st.gmap.owner[st.gmap.ref(x, y)] = 0
    for y in range(0, 10):
        for x in range(10, 20):
            st.gmap.owner[st.gmap.ref(x, y)] = 1
    st._counts[0] = 100
    st._counts[1] = 100
    st.players[0].troops = 100_000.0
    return st


def test_attacking_rejects_their_pending_alliance_request():
    """⚠ **치는 순간 그쪽이 보낸 동맹 요청은 거절된다**
    (`rejectIncomingAllianceRequests`).

    막지 않았으면: 때려 놓고 그 요청을 그대로 받아 동맹이 된다. 공격이 관계에
    −70 을 주는 것과 앞뒤가 안 맞는다."""
    st = _bordering(state())
    st.request_alliance(1, 0)                    # 1번이 나에게 동맹을 청했다
    assert 0 in st.diplomacy.pending.get(1, set())

    st.launch_attack(0, 1)                       # 내가 1번을 친다
    assert 0 not in st.diplomacy.pending.get(1, set()),         "때려 놓고 그 요청이 그대로 남아 있다"


def test_attacking_leaves_other_peoples_requests_alone():
    """대조군 — **그 상대의 요청만** 거절한다. 남의 요청은 그대로다."""
    st = _bordering(state())
    st.request_alliance(1, 0)
    st.request_alliance(2, 0)
    st.launch_attack(0, 1)
    assert 0 not in st.diplomacy.pending.get(1, set())
    assert 0 in st.diplomacy.pending.get(2, set()), "엉뚱한 요청까지 지웠다"


def test_attacking_neutral_land_rejects_nothing():
    """중립을 칠 때는 거절할 상대가 없다 — 그냥 아무 일도 안 일어난다."""
    st = state()
    st.request_alliance(1, 0)
    st.launch_attack(0, None)
    assert 0 in st.diplomacy.pending.get(1, set())


def _across_water():
    """바다를 사이에 둔 두 나라. ⚠ 이 파일의 지도는 **전부 육지**라
    `send_boat` 이 바닷길을 못 찾아 그냥 실패한다 — 상륙 규칙까지 가지도 못한다."""
    gm = GameMap.from_rows(["." * 8 + "~" * 8 + "." * 8] * 20)
    players = {}
    for pid in range(3):
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False,
                                   start=gm.ref(pid, 0))
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 0 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS * 2
    for y in range(20):
        for x in range(0, 8):
            gm.owner[gm.ref(x, y)] = 0
            st._counts[0] += 1
        for x in range(16, 24):
            gm.owner[gm.ref(x, y)] = 1
            st._counts[1] += 1
    st.players[0].troops = 100_000.0
    return st


def test_a_naval_invasion_also_rejects_the_request():
    """상륙도 같은 일을 한다 — 배를 띄우는 것도 공격이다."""
    st = _across_water()
    st.request_alliance(1, 0)
    assert st.send_boat(0, st.gmap.ref(16, 5)) is not None
    assert 0 not in st.diplomacy.pending.get(1, set()),         "배를 띄워 놓고 그 요청이 그대로 남아 있다"


def test_a_naval_invasion_involving_a_bot_leaves_the_request():
    """⚠ **조건이 육상 공격과 다르다.** 원본은 상륙에서만
    `targetPlayer.type() !== Bot && attacker.type() !== Bot` 를 본다 —
    봇이 끼면 요청을 안 건드린다.

    옮길 때 "같은 규칙이니 같겠지"로 뭉갤 자리다."""
    st = _across_water()
    st.players[1].kind = "bot"
    st.players[1].is_bot = True
    st.request_alliance(1, 0)
    assert st.send_boat(0, st.gmap.ref(16, 5)) is not None
    assert 0 in st.diplomacy.pending.get(1, set()), "봇인데도 거절했다"


# --- 맞요청 (§5.73) -----------------------------------------------------------

def test_two_players_reaching_out_at_once_become_allies_immediately():
    """⚠ **이식 누락 쉰다섯.** 원본은 상대가 이미 요청해 뒀으면 새 요청을 만들지
    않고 **그 요청을 수락한다**(*"accept it instead of creating a new one"*).

    막지 않았으면: 서로 손을 내민 두 나라가 **각자 대기 상태로 남아** 아무도
    수락하지 않은 동맹이 된다. 사람이 먼저 내밀었는데 AI 도 같은 생각이었을 때가
    정확히 그 자리다."""
    st = state()
    assert st.request_alliance(1, 0)              # 1 이 먼저 내밀었다
    assert not st.diplomacy.allied(0, 1)
    assert st.request_alliance(0, 1)              # 0 도 같은 생각이었다
    assert st.diplomacy.allied(0, 1), "둘 다 대기만 하고 동맹이 안 됐다"
    assert not st.diplomacy.pending.get(1), "맞요청이 성립했는데 요청이 남아 있다"
    # 수락 경로를 그대로 타므로 관계도 오른다(+100 양쪽)
    assert st.players[0].relations.value(1) == pytest.approx(C.REL_ALLIANCE_ACCEPTED)
    assert st.players[1].relations.value(0) == pytest.approx(C.REL_ALLIANCE_ACCEPTED)


def test_a_one_way_request_still_just_waits():
    """대조군 — 한쪽만 내밀면 그대로 대기다. 아무거나 성립시키면 안 된다."""
    st = state()
    assert st.request_alliance(1, 0)
    assert not st.diplomacy.allied(0, 1)
    assert 0 in st.diplomacy.pending.get(1, {})


def test_an_unanswered_request_expires_after_twenty_seconds():
    """⚠ **이식 누락 쉰다섯.** 만료가 없어 `pending` 이 판 끝까지 남았다.

    막지 않았으면: §5.68 의 ✉(요청 중) 깃발이 한 번 켜지면 안 꺼지고, AI 는
    판 내내 같은 요청을 다시 판단한다. 원본은 20초(`allianceRequestDuration`)다."""
    st = state()
    assert st.request_alliance(1, 0)
    t0 = st.tick_count
    while st.tick_count - t0 < C.ALLIANCE_REQUEST_DURATION_TICKS - 1:
        st.tick()
    assert 0 in st.diplomacy.pending.get(1, {}), "20초 전에 사라졌다"
    st.tick()
    assert not st.diplomacy.pending.get(1), "20초가 지났는데 요청이 남아 있다"
    assert C.ALLIANCE_REQUEST_DURATION_TICKS == 200


def test_expiring_tells_the_one_who_asked():
    """거절 소식이 나가야 한다 — 원본도 `req.reject()` 를 부른다.

    막지 않았으면: 요청이 조용히 사라져 **왜 답이 없는지** 알 수 없다(§5.67)."""
    from domynion.core.events import EventKind
    st = state()
    st.request_alliance(1, 0)
    for _ in range(C.ALLIANCE_REQUEST_DURATION_TICKS + 1):
        st.tick()
    kinds = [e.kind for e in st.log.items if e.who == 1]
    assert EventKind.ALLIANCE_REJECTED in kinds
