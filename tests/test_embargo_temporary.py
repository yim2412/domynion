"""임시 금수 · 동맹이 맺어질 때 풀리는 것들 — 이식 누락 쉰일곱~예순 (§5.74).

`TEMPORARY_EMBARGO_TICKS = 300 * 10` 은 `constants.py` 에 **적혀만 있었다.**
`grep` 해 보니 읽는 곳이 한 군데도 없었다 — §5.49(핵 요격 창)·§5.12(상륙 퇴각
25%)와 같은 모양이다. 이름만 적힌 상수는 아직 확인 안 한 항목이다.

원본이 정하는 네 가지:

1. **공격을 받으면 맞은 쪽이 친 쪽에게 금수를 건다**(`AttackExecution:102`,
   `addEmbargo(owner, true)`). 자동으로 걸리는 것이라 *임시*다.
2. **임시 금수는 5분 뒤 스스로 풀린다**(`PlayerExecution:100~108`). 수동은 안 풀린다.
3. **맞요청으로 동맹이 성립하면 임시 금수가 양쪽 다 풀린다**
   (`AllianceRequestExecution:51~54`). 손수 건 금수는 그대로 남는다.
4. **같은 자리에서 서로에게 날아가던 핵이 취소된다**
   (`cancelNukesBetweenAlliedPlayers`). §5.72(핵이 동맹을 깬다)의 반대 방향이다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.events import EventKind
from domynion.core.gamemap import GameMap
from domynion.core.nukes import NUKE_MAGNITUDES, Fallout
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(size: int = 80, players: int = 3, bots=()) -> GameState:
    gm = GameMap.from_rows(["." * size] * size)
    ps = {}
    for pid in range(players):
        t = gm.ref(pid * 20 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=pid in bots, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    # 스폰 면역이 지난 뒤로 맞춰 둔다 — 면역 중이면 사람은 아예 못 친다(§5.24).
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def own_square(st: GameState, pid: int, cx: int, cy: int, r: int) -> None:
    w = st.gmap.width
    n = 0
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            if 0 <= x < w and 0 <= y < st.gmap.height:
                st.gmap.owner[st.gmap.ref(x, y)] = pid
                n += 1
    st._counts[pid] = n


def give_silo(st: GameState, pid: int, tile: int) -> Unit:
    u = Unit(UnitType.MISSILE_SILO, pid, tile=tile)
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(UnitType.MISSILE_SILO)
    return u


def attack(st: GameState, pid: int, target: int) -> None:
    """국경을 맞대게 해 두고 실제로 친다 — `launch_attack` 경로를 그대로 탄다.

    가짜로 `start_embargo` 를 부르면 **배선을 안 재게 된다.** 공격 경로가
    금수를 거는지 보려면 실제로 쳐야 한다."""
    for x in range(20, 40):                       # 두 나라를 맞붙여 둔다
        st.gmap.owner[st.gmap.ref(x, 60)] = pid
    for x in range(40, 60):
        st.gmap.owner[st.gmap.ref(x, 60)] = target
    st._counts[pid] = st._counts[target] = 20
    st.players[pid].troops = 100_000
    assert st.launch_attack(pid, target) is not None, "공격 자체가 안 나갔다"


# --- 1. 공격이 임시 금수를 건다 ----------------------------------------------

def test_being_attacked_embargoes_the_attacker():
    """⚠ 이식 누락 — 맞은 쪽이 무역을 끊지 않고 있었다.

    막지 않았으면: 나를 친 상대와 판 내내 무역을 계속한다. 원본에서 전쟁은
    무역을 끊지만 우리에게는 공격과 무역이 아무 상관이 없었다."""
    st = state()
    attack(st, 0, 1)
    assert st.diplomacy.embargoed(1, 0), "맞은 쪽이 금수를 안 걸었다"
    assert not st.diplomacy.embargoed(0, 1), "친 쪽이 거는 것이 아니다"


def test_the_attack_embargo_is_temporary():
    st = state()
    attack(st, 0, 1)
    assert st.diplomacy.embargoes[1][0].temporary, "수동으로 걸렸다 — 안 풀린다"


def test_bots_neither_embargo_nor_get_embargoed():
    """봇은 무역을 안 하므로 금수가 의미가 없다(원본 주석 그대로).

    막지 않았으면: 봇 400개가 있는 판에서 공격 한 번마다 금수가 쌓인다."""
    st = state(bots=(1,))
    attack(st, 0, 1)
    assert not st.diplomacy.embargoed(1, 0), "봇이 금수를 걸었다"
    st2 = state(bots=(0,))
    attack(st2, 0, 1)
    assert not st2.diplomacy.embargoed(1, 0), "봇에게 금수를 걸었다"


def test_a_bot_attack_does_not_reject_alliance_requests_either():
    """같은 `if` 안에 있는 것이라 함께 움직인다 — 봇이 끼면 거절도 안 한다."""
    st = state(bots=(1,))
    st.request_alliance(1, 0)
    assert 0 in st.diplomacy.pending.get(1, {})
    attack(st, 0, 1)
    assert 0 in st.diplomacy.pending.get(1, {}), "봇 공격이 요청을 거절했다"


# --- 2. 만료 ----------------------------------------------------------------

def test_temporary_embargo_expires_after_five_minutes():
    st = state()
    attack(st, 0, 1)
    at = st.diplomacy.embargoes[1][0].created_at
    st.diplomacy.expire_embargoes(at + C.TEMPORARY_EMBARGO_TICKS)
    assert st.diplomacy.embargoed(1, 0), "정확히 3,000 tick 에는 아직 살아 있다"
    st.diplomacy.expire_embargoes(at + C.TEMPORARY_EMBARGO_TICKS + 1)
    assert not st.diplomacy.embargoed(1, 0), "5분이 지나도 안 풀렸다"


def test_manual_embargo_never_expires():
    """막지 않았으면: 사람이 손수 끊은 무역이 5분 뒤 저절로 되살아난다."""
    st = state()
    st.diplomacy.start_embargo(0, 1, tick=0)
    st.diplomacy.expire_embargoes(C.TEMPORARY_EMBARGO_TICKS * 10)
    assert st.diplomacy.embargoed(0, 1), "수동 금수가 만료됐다"


def test_expiry_is_wired_into_the_tick():
    """로직이 아니라 **배선**을 잰다. 문턱을 테스트가 만들면 배선은 안 보인다."""
    st = state()
    attack(st, 0, 1)
    st.tick_count += C.TEMPORARY_EMBARGO_TICKS + 1
    st._expire_embargoes()
    assert not st.diplomacy.embargoed(1, 0), "tick 이 만료를 안 부른다"


def test_an_attack_does_not_downgrade_a_manual_embargo():
    """⚠ 원본이 `addEmbargo` 첫 줄에서 바로 돌아서는 이유다.

    막지 않았으면: 사람이 걸어 둔 금수를 공격 한 번이 임시로 바꿔 5분 뒤
    **푼 적이 없는데 풀린다.**"""
    st = state()
    st.diplomacy.start_embargo(1, 0, tick=0)          # 1 이 손수 걸어 뒀다
    attack(st, 0, 1)
    assert not st.diplomacy.embargoes[1][0].temporary, "수동이 임시로 덮였다"
    st.diplomacy.expire_embargoes(st.tick_count + C.TEMPORARY_EMBARGO_TICKS + 1)
    assert st.diplomacy.embargoed(1, 0), "덮인 뒤 만료됐다"


def test_a_second_attack_refreshes_the_temporary_embargo():
    """임시 위에는 덮인다 — 계속 맞는 동안 금수가 안 풀려야 한다."""
    st = state()
    attack(st, 0, 1)
    first = st.diplomacy.embargoes[1][0].created_at
    st.tick_count += 2_000
    attack(st, 0, 1)
    assert st.diplomacy.embargoes[1][0].created_at == first + 2_000
    st.diplomacy.expire_embargoes(first + C.TEMPORARY_EMBARGO_TICKS + 1)
    assert st.diplomacy.embargoed(1, 0), "두 번째 공격이 시각을 안 갱신했다"


# --- 3. 동맹이 맺어지면 임시 금수가 풀린다 -----------------------------------

def test_mutual_request_ends_temporary_embargoes_on_both_sides():
    st = state()
    attack(st, 0, 1)
    attack(st, 1, 0)
    assert st.diplomacy.embargoed(1, 0) and st.diplomacy.embargoed(0, 1)
    st.request_alliance(0, 1)
    st.request_alliance(1, 0)                          # 맞요청 — 그 자리에서 성립
    assert st.diplomacy.allied(0, 1)
    assert not st.diplomacy.embargoed(1, 0), "한쪽이 안 풀렸다"
    assert not st.diplomacy.embargoed(0, 1), "다른 쪽이 안 풀렸다"


def test_a_manual_embargo_survives_the_new_alliance():
    """원본 주석: *"only if they were automatically created."*

    막지 않았으면: 동맹을 맺었다고 상대가 손수 끊은 무역까지 되살아난다."""
    st = state()
    st.diplomacy.start_embargo(1, 0, tick=0)
    st.request_alliance(0, 1)
    st.request_alliance(1, 0)
    assert st.diplomacy.allied(0, 1)
    assert st.diplomacy.embargoed(1, 0), "수동 금수가 풀렸다"


def test_a_plain_accept_does_not_touch_embargoes():
    """⚠ **원본이 맞요청 분기에만 둔 것이다.** `AllianceRequestReplyExecution`
    에는 이 처리가 없다. "동맹이 맺어지면 언제나" 로 옮기면 원본에 없는 규칙이 된다."""
    st = state()
    attack(st, 0, 1)
    st.request_alliance(0, 1)
    st.accept_alliance(1, 0)
    assert st.diplomacy.allied(0, 1)
    assert st.diplomacy.embargoed(1, 0), "평범한 수락이 금수를 풀었다"


# --- 4. 동맹이 맺어지면 날아가던 핵이 취소된다 -------------------------------

def test_mutual_request_cancels_nukes_in_flight_both_ways():
    """⚠ §5.72 의 반대 방향. 한쪽만 있으면 *쏜 뒤 동맹을 맺어 취소하는 길*만
    막히고 그 반대는 열려 있는 비대칭이 된다."""
    st = state()
    own_square(st, 1, 40, 40, 20)
    own_square(st, 0, 5, 5, 20)
    for pid, dst in ((0, st.gmap.ref(40, 40)), (1, st.gmap.ref(5, 5))):
        give_silo(st, pid, st.players[pid].start)
        st.players[pid].gold = 10_000_000
        assert st.launch_nuke(pid, UnitType.ATOM_BOMB, dst) is not None
    assert len(st.nukes) == 2
    st.request_alliance(0, 1)
    st.request_alliance(1, 0)
    assert st.diplomacy.allied(0, 1)
    assert st.nukes == [], "동맹을 맺었는데 핵이 계속 날아간다"


def test_cancelling_nukes_tells_both_players():
    """조용히 사라지면 왜 핵이 없어졌는지 알 수 없다 — §5.67 과 같은 자리다."""
    st = state()
    own_square(st, 1, 40, 40, 20)
    give_silo(st, 0, st.players[0].start)
    st.players[0].gold = 10_000_000
    st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(40, 40))
    st.request_alliance(0, 1)
    st.request_alliance(1, 0)
    kinds = {(e.kind, e.who) for e in st.log.items}
    assert (EventKind.NUKES_CANCELLED_SENT, 0) in kinds, "쏜 쪽이 모른다"
    assert (EventKind.NUKES_CANCELLED_RECEIVED, 1) in kinds, "맞을 뻔한 쪽이 모른다"


def test_nukes_aimed_at_a_third_party_survive():
    """막지 않았으면: 둘이 동맹을 맺을 때 판의 모든 핵이 사라진다."""
    st = state()
    own_square(st, 2, 40, 40, 20)
    give_silo(st, 0, st.players[0].start)
    st.players[0].gold = 10_000_000
    st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(40, 40))
    st.request_alliance(0, 1)
    st.request_alliance(1, 0)
    assert len(st.nukes) == 1, "제3자에게 가던 핵이 사라졌다"


def test_a_nuke_just_outside_the_border_is_cancelled_too():
    """⚠ 일반 핵은 **칸 주인이 아니라** 가중 타일·건물 판정을 쓴다
    (`wouldNukeBreakAlliance`). 칸 주인만 보면 *터지면 동맹이 깨질* 핵이 남는다."""
    st = state()
    own_square(st, 1, 40, 40, 20)
    edge = st.gmap.ref(40, 19)                    # 1 의 땅 바로 바깥 — 주인은 없다
    assert int(st.gmap.owner[edge]) != 1
    give_silo(st, 0, st.players[0].start)
    st.players[0].gold = 10_000_000
    st.launch_nuke(0, UnitType.ATOM_BOMB, edge)
    st.request_alliance(0, 1)
    st.request_alliance(1, 0)
    assert st.nukes == [], "국경 밖을 노린 핵이 남았다"


def test_many_warheads_are_reported_as_one():
    """막지 않았으면: MIRV 한 발이 갈라진 350발이 "핵 350발이 사라졌다"로 나온다."""
    st = state()
    own_square(st, 1, 40, 40, 20)
    from domynion.core.nukes import Nuke
    for _ in range(5):
        st.nukes.append(Nuke(owner=0, utype=UnitType.MIRV_WARHEAD,
                             src=st.players[0].start, dst=st.gmap.ref(40, 40)))
    st.request_alliance(0, 1)
    st.request_alliance(1, 0)
    assert st.nukes == []
    sent = [e for e in st.log.items if e.kind is EventKind.NUKES_CANCELLED_SENT]
    assert len(sent) == 1 and sent[0].amount == 1, "탄두를 발 수 그대로 셌다"


def test_the_blast_radius_is_what_decides_not_the_target_tile():
    """반경 밖은 그대로 날아간다 — 취소가 판 전체로 번지지 않는지 확인한다."""
    st = state()
    own_square(st, 1, 70, 70, 5)
    _, outer = NUKE_MAGNITUDES[UnitType.ATOM_BOMB]
    far = st.gmap.ref(10, 70)
    assert (70 - 10) > outer
    give_silo(st, 0, st.players[0].start)
    st.players[0].gold = 10_000_000
    st.launch_nuke(0, UnitType.ATOM_BOMB, far)
    st.request_alliance(0, 1)
    st.request_alliance(1, 0)
    assert len(st.nukes) == 1, "반경 밖 핵이 취소됐다"
