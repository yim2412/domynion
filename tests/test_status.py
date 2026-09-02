"""이름 옆 상태 깃발 — 원본 `derive/PlayerStatus.ts` (§5.68).

⚠ **규칙이 도는 것과 그것이 화면에 보이는 것은 다른 문제다.** 배신자·클락·핵·
동맹·표적·금수가 전부 돌고 있었는데 지도에서는 구분할 수가 없었다.

여기 있는 것은 **순수 계산**이라 Qt 없이 잰다. 원본도 `derive/` 에 따로 뒀다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Nuke
from domynion.core.state import PlayerState
from domynion.core.units import UnitType
from domynion.ui.status import MAX_MARKERS, Status, markers, player_status


def state(n: int = 3) -> GameState:
    gm = GameMap.from_rows(["." * 60] * 6)
    players = {}
    for pid in range(n):
        for x in range(pid * 6, pid * 6 + 6):
            for y in range(0, 3):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 6, 0))
        p.kind = "nation"
        p.troops = 300_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 18 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


# --- 절대 깃발 (누가 보든 같다) ----------------------------------------------

def test_the_crown_goes_to_the_most_land_not_the_most_troops():
    """⚠ **땅이다.** 병력도 골드도 아니다 — 원본은 `tilesOwned` 만 본다.

    막지 않았으면: 병력 부자가 왕관을 쓰고, 실제로 이기고 있는 쪽이 안 보인다."""
    st = state()
    st._counts = {0: 18, 1: 40, 2: 18}
    st.players[0].troops = 9_000_000.0        # 병력은 압도적이지만 땅이 적다
    s = player_status(st)
    assert s[1].crown
    assert 0 not in s, "왕관이 없으면 상자도 없다 — 병력은 깃발이 아니다"


def test_a_dead_player_gets_no_flags_at_all():
    st = state()
    st.players[1].alive = False
    assert 1 not in player_status(st)


def test_only_players_with_something_to_show_get_an_entry():
    """⚠ 깃발이 하나도 없으면 **상자를 만들지 않는다**(원본과 같다).

    막지 않았으면: 400개 나라마다 빈 상자가 생겨 그리기 전에 이미 샌다."""
    st = state()
    s = player_status(st)
    assert set(s) == {0}, "왕관 하나뿐이어야 한다"


def test_the_traitor_flag_carries_its_remaining_time():
    st = state()
    st.diplomacy.traitor_since[1] = st.tick_count
    s = player_status(st)
    assert s[1].traitor and s[1].traitor_remaining > 0

    st.tick_count += C.TRAITOR_DURATION_TICKS
    s2 = player_status(st)
    assert 1 not in s2 or not s2[1].traitor


# --- 클락 --------------------------------------------------------------------

def test_the_skull_only_stops_blinking_once_it_actually_drains():
    """⚠ **위험과 실제 유출을 눈으로 갈라야 한다.** 원본은 경고 구간 동안 해골을
    깜빡이고(`warnProgress` 가 1 에 가까울수록 빨리), 지나면 고정한다.

    막지 않았으면: 바 아래로 내려간 순간과 실제로 병력이 새기 시작한 순간이
    화면에서 같아 보인다 — 되돌릴 수 있는 구간인지 알 수가 없다."""
    st = state()
    warn = st.clock.cfg.warn_seconds
    st.clock.marked_at[1] = st.tick_count / C.TICK_HZ

    s = player_status(st)
    assert s[1].in_clock and not s[1].clock_draining
    assert s[1].clock_warn_progress == pytest.approx(0.0)

    st.tick_count += int(warn * C.TICK_HZ / 2)
    assert player_status(st)[1].clock_warn_progress == pytest.approx(0.5, abs=0.05)

    st.tick_count += int(warn * C.TICK_HZ)
    s3 = player_status(st)
    assert s3[1].clock_draining and s3[1].clock_warn_progress == pytest.approx(1.0)


def test_the_red_skull_means_land_is_already_gone_not_that_troops_are_leaking():
    """⚠ 해골이 둘이다(§5.92). 유출(💀)은 견디면 멈추지만 썩음(☠)은 **이미 땅이
    사라지는 중**이라 되돌릴 수 없다 — 원본도 색을 갈라 놓는다.

    막지 않았으면: 유출 깃발 하나로 뭉뚱그려도 해골은 뜨고, 사람은 아직 반격할
    수 있는 상태와 이미 늦은 상태를 구분 못 한다."""
    st = state()
    warn = st.clock.cfg.warn_seconds
    st.clock.marked_at[1] = (st.tick_count / C.TICK_HZ) - warn - 1

    s = player_status(st)[1]
    assert s.clock_draining and not s.clock_decaying, "아직 한 칸도 안 썩었다"

    st.clock.mark_rotted(1, st.tick_count)
    assert player_status(st)[1].clock_decaying


def test_the_rotting_skull_outranks_the_draining_one_for_a_marker_slot():
    """자리가 셋뿐이라 순서가 곧 무엇을 버릴지다.

    막지 않았으면: 둘 다 켜졌을 때 💀 만 보이고 ☠ 가 잘린다."""
    got = markers(Status(crown=True, alliance=True, embargo=True,
                         clock_draining=True, clock_decaying=True))
    assert got[0] == "☠" and "💀" in got


# --- 핵 ----------------------------------------------------------------------

def _nuke(st, owner: int, dst_owner: int) -> None:
    dst = next(int(t) for t in range(st.gmap.size)
               if int(st.gmap.owner[t]) == dst_owner)
    st.nukes.append(Nuke(owner=owner, utype=UnitType.ATOM_BOMB,
                         src=st.players[owner].start, dst=dst))


def test_a_nuke_aimed_at_me_is_a_different_flag_from_any_nuke():
    """⚠ **이게 이 절에서 가장 값진 깃발이다.** 핵이 날아다니는 것과 그것이
    **나를** 겨눈 것은 완전히 다른 정보다.

    막지 않았으면: 소식창의 `NUKE_INBOUND` 한 줄이 전부다 — 로그는 흘러가고
    지도는 남는데, 어느 나라가 지금 나를 노리는지는 지도에서 봐야 한다."""
    st = state()
    _nuke(st, owner=1, dst_owner=2)

    mine = player_status(st, me=0)
    assert mine[1].nuke_active and not mine[1].nuke_targets_me

    victim = player_status(st, me=2)
    assert victim[1].nuke_active and victim[1].nuke_targets_me


def test_without_a_local_player_the_relative_flags_are_all_off():
    """관전·리플레이 경로. 원본도 `localPlayerSmallID` 가 없으면 그렇다."""
    st = state()
    _nuke(st, owner=1, dst_owner=2)
    st.diplomacy.form(0, 1, st.tick_count)
    s = player_status(st)
    assert s[1].nuke_active, "절대 깃발은 그대로 켜진다"
    assert not s[1].alliance and not s[1].nuke_targets_me


# --- 상대적 깃발 --------------------------------------------------------------

def test_an_embargo_shows_from_either_side():
    """⚠ **금수는 양방향이다.** 내가 걸었든 상대가 걸었든 무역은 똑같이 끊긴다.

    막지 않았으면: 상대가 나에게 건 금수가 안 보여, 무역선이 왜 안 오는지
    화면 어디에도 답이 없다."""
    st = state()
    st.diplomacy.embargoes.setdefault(1, set()).add(0)   # 상대가 나에게 걸었다
    assert player_status(st, me=0)[1].embargo


def test_the_alliance_flag_carries_how_much_time_is_left():
    st = state()
    al = st.diplomacy.form(0, 1, st.tick_count)
    s = player_status(st, me=0)
    assert s[1].alliance and s[1].alliance_fraction == pytest.approx(1.0)

    st.tick_count = al.expires_at - C.ALLIANCE_DURATION_TICKS // 4
    assert player_status(st, me=0)[1].alliance_fraction == pytest.approx(0.25)


def test_an_incoming_request_is_not_the_same_as_an_alliance():
    st = state()
    st.diplomacy.pending.setdefault(1, set()).add(0)     # 상대가 나에게 요청
    s = player_status(st, me=0)
    assert s[1].alliance_req and not s[1].alliance


def test_i_never_get_relative_flags_about_myself():
    st = state()
    st.diplomacy.form(0, 1, st.tick_count)
    assert not player_status(st, me=0)[0].alliance


# --- 글자로 줄이기 -------------------------------------------------------------

def test_markers_are_capped_so_the_name_stays_readable():
    """⚠ 원본은 아이콘을 그려 겹치지만 **우리는 글자라 폭이 그대로 든다.**
    전부 붙이면 이름보다 깃발이 길어져 지도가 안 읽힌다."""
    s = Status(crown=True, traitor=True, nuke_active=True, nuke_targets_me=True,
               alliance=True, target=True, embargo=True)
    out = markers(s)
    assert len(out) == MAX_MARKERS


def test_the_nuke_aimed_at_me_wins_the_first_slot():
    """자리가 셋뿐이라 **순서가 곧 우선순위**다. 나를 겨눈 핵이 맨 앞이다."""
    s = Status(crown=True, alliance=True, target=True, embargo=True,
               nuke_targets_me=True)
    assert markers(s)[0] == "☢"


def test_nothing_shown_for_an_empty_status():
    assert markers(Status()) == ""
