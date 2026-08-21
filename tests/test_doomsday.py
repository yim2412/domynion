"""P6 — 둠스데이 클락.

원본의 **진짜 종료 규칙**이다. openfront 에는 시간 제한도 지배 승리도 없고, 요구
점유율이 파도처럼 올라 그 아래로 떨어진 쪽이 말라 죽는다.

여기 수치는 전부 원본 주석에 근거가 적혀 있는 것들이라 그대로 대조한다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.doomsday import (LEVELS, LEVELS_TEAM, SCHEDULES, DoomsdayClock,
                                    required_basis_points, required_tiles)
from domynion.core.engine import GameState, Victory
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState


def state(players: int = 3) -> GameState:
    gm = GameMap.from_rows(["." * 100] * 10)
    ps = {}
    for pid in range(players):
        t = gm.ref(pid * 20, 0)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    return st


# --- 요구 점유율 곡선 --------------------------------------------------------

def test_first_ten_minutes_are_free():
    """클락은 초반 솎아내기가 아니라 **교착 해결기**다. 600초까지 0% 다.

    막지 않았으면: 시작하자마자 전원이 바 아래라 초반 싸움이 무의미해진다."""
    assert required_basis_points(0) == 0
    assert required_basis_points(599) == 0
    assert required_basis_points(600) == 0
    assert required_basis_points(601) > 0


def test_bar_climbs_smoothly_never_jumps():
    """파도마다 선형으로 오르고 잠깐 쉰다. 계단으로 뛰면 그 순간 여럿이 동시에 죽는다."""
    prev = 0
    for t in range(600, 3000, 7):
        bp = required_basis_points(t)
        assert bp >= prev, "요구치가 내려갔다"
        assert bp - prev <= 60, f"{t}초에 {prev}→{bp} 로 뛰었다"
        prev = bp


def test_bar_holds_flat_during_pauses():
    s = SCHEDULES["normal"]
    end_of_first_ramp = s.grace_seconds + s.ramp_seconds[0]
    assert required_basis_points(end_of_first_ramp) == LEVELS[0]
    mid_pause = end_of_first_ramp + s.pause_seconds[0] // 2
    assert required_basis_points(mid_pause) == LEVELS[0], "쉬는 동안 올랐다"


def test_ceiling_is_thirty_five_percent():
    """천장 35% 는 85판 실측(2위 점유율이 21.6% 를 넘은 적 없음)에서 왔다.
    더 올리면 선두마저 죽는다."""
    assert LEVELS[-1] == 3500
    assert required_basis_points(100_000) == 3500
    assert required_basis_points(2_100) == 3500, "normal 은 35:00 에 천장"


def test_team_ladder_is_raised_but_same_ceiling():
    assert LEVELS_TEAM[0] > LEVELS[0]
    assert LEVELS_TEAM[-1] == LEVELS[-1]
    assert required_basis_points(700, team_game=True) > required_basis_points(700)


def test_speeds_only_change_the_pace():
    """빠를수록 같은 사다리를 빨리 오른다. 목표치 자체는 같다."""
    for name in ("slow", "normal", "fast", "veryfast"):
        assert SCHEDULES[name].levels == LEVELS
    t = 900
    assert (required_basis_points(t, "veryfast") >= required_basis_points(t, "fast")
            >= required_basis_points(t, "normal") >= required_basis_points(t, "slow"))


def test_required_tiles_scales_with_the_map():
    assert required_tiles(100_000, 10_000) == 3_500
    assert required_tiles(0, 10_000) == 0


# --- 표시·유출·소멸 ---------------------------------------------------------

def test_climbing_back_over_the_bar_clears_the_mark():
    """원본 주석: "the drain stops the moment it climbs back"."""
    c = DoomsdayClock()
    c.cfg.enabled = True
    c.update(1200, {0: 1}, land_count=10_000)
    assert 0 in c.marked_at
    c.update(1210, {0: 9_999}, land_count=10_000)
    assert 0 not in c.marked_at, "바 위로 올라왔는데 표시가 남았다"


def test_warning_window_delays_the_drain():
    c = DoomsdayClock()
    c.cfg.enabled = True
    c.update(1000, {0: 0}, land_count=10_000)
    assert c.drain_fraction(0, 1000 + c.cfg.warn_seconds - 1) == 0.0, "경고 중엔 안 샌다"
    assert c.drain_fraction(0, 1000 + c.cfg.warn_seconds + 1) > 0.0


def test_drain_ramps_from_two_to_five_percent():
    c = DoomsdayClock()
    c.cfg.enabled = True
    c.update(1000, {0: 0}, land_count=10_000)
    start = c.drain_fraction(0, 1000 + c.cfg.warn_seconds)
    full = c.drain_fraction(0, 1000 + c.cfg.warn_seconds + c.cfg.drain_ramp_seconds)
    assert start == pytest.approx(c.cfg.drain_start_percent / 100)
    assert full == pytest.approx(c.cfg.drain_max_percent / 100)


def test_troop_floor_decays_leaving_one_comeback_window():
    """바닥이 40% 에서 5% 로 내려간다 — 반격 기회를 한 번 주되 영구히 살리지 않는다."""
    c = DoomsdayClock()
    c.cfg.enabled = True
    c.update(1000, {0: 0}, land_count=10_000)
    early = c.troop_floor_fraction(0, 1000 + c.cfg.warn_seconds)
    late = c.troop_floor_fraction(0, 1000 + c.cfg.warn_seconds + c.cfg.floor_decay_seconds)
    assert early == pytest.approx(0.40)
    assert late == pytest.approx(0.05)


def test_rot_is_a_deadline_not_a_rate():
    """`rotDeathSeconds` 는 마감 시각이다. 유출만으로는 절대 안 죽으므로 이게 없으면
    바 아래에 있어도 판이 안 끝난다."""
    c = DoomsdayClock()
    c.cfg.enabled = True
    c.update(1000, {0: 0}, land_count=10_000)
    deadline = 1000 + c.cfg.warn_seconds + c.cfg.rot_death_seconds
    assert not c.is_dead(0, deadline - 1)
    assert c.is_dead(0, deadline)


def test_disabled_clock_does_nothing():
    """원본 기본값도 꺼져 있다."""
    c = DoomsdayClock()
    assert not c.cfg.enabled
    c.update(5000, {0: 0}, land_count=10_000)
    assert c.marked_at == {}


# --- 엔진 배선 --------------------------------------------------------------

def test_clock_drains_troops_and_finally_wipes():
    st = state(players=2)
    st.clock.cfg.enabled = True
    st._counts = {0: 900, 1: 1}
    for x in range(1, 901):
        st.gmap.owner[x] = 0
    st.gmap.owner[st.gmap.ref(0, 0)] = 0
    st._counts = {0: 901, 1: 1}
    st.players[1].troops = 100_000.0

    st.tick_count = int(1200 / C.TICK_DT)       # 유예를 지난 시점
    st.tick()
    assert 1 in st.clock.marked_at

    before = st.players[1].troops
    st.tick_count += int(60 / C.TICK_DT)        # 경고를 넘겨 유출 구간으로
    st.tick()
    assert st.players[1].troops < before, "병력이 안 샌다"

    st.tick_count += int(200 / C.TICK_DT)       # 마감을 넘긴다
    st.tick()
    assert not st.players[1].alive
    assert st.tiles(1) == 0
    assert st.verify_counts()


def test_with_the_clock_on_only_conquest_ends_the_game():
    """클락이 켜지면 시간 제한·지배 승리를 쓰지 않는다 — 원본에 없는 규칙이다."""
    st = state(players=2)
    st.clock.cfg.enabled = True
    st._counts = {0: 999, 1: 1}
    st.tick_count = int(C.MATCH_SECONDS / C.TICK_DT) + 10
    st.tick()
    assert st.victory is not Victory.TIMEOUT
    assert st.victory is not Victory.DOMINATION
