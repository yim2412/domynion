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
from domynion.core.nukes import Fallout
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

    # ⚠ §5.56 부터 **마감에 한 번에 지우지 않는다.** 병력이 바닥까지 내려가야
    # 썩기 시작하고, 그 뒤 `rot_death_seconds` 에 걸쳐 칸을 먹는다. 그래서
    # 시간을 건너뛰고 tick 한 번으로는 못 잰다 — 실제로 돌려야 한다.
    for _ in range(int(600 / C.TICK_DT)):
        st.tick()
        if not st.players[1].alive:
            break
    assert not st.players[1].alive, "마감이 지났는데 안 죽었다"
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


# --- 점진적 썩음 (§5.56) ------------------------------------------------------

def doomed(tiles: int = 40):
    """바 아래로 떨어진 나라 하나를 만든다. 0번이 크고 1번이 썩는다.

    ⚠ 재료를 두 번 틀렸다. (a) 지도가 **100×10 = 1,000칸**이라 정사각형으로
    잡으면 벗어난다. (b) 200칸(20%)은 **바보다 높아 표시가 안 된다** — 1200초
    시점의 요구치가 7% 다. 40칸(4%)으로 낮춰야 이 규칙을 잰다."""
    st = state(players=2)
    st.clock.cfg.enabled = True
    # ⚠ 이 파일의 `state()` 는 낙진을 안 만든다. 썩은 칸이 낙진이 되는 규칙을
    # 재려면 있어야 한다.
    st.fallout = Fallout(st.gmap.size)
    w = st.gmap.width
    n0 = 0
    for y in range(5, 10):                      # 아래 절반은 0번
        for x in range(0, w):
            st.gmap.owner[y * w + x] = 0
            n0 += 1
    n1 = 0
    for i in range(tiles):                      # 위쪽에서 tiles 칸만
        st.gmap.owner[i] = 1
        n1 += 1
    st._counts = {0: n0, 1: n1}
    st.players[1].troops = 10.0                 # 이미 바닥이다
    st.tick_count = int(1200 / C.TICK_DT)
    st.tick()
    assert 1 in st.clock.marked_at, "표시가 안 됐다"
    return st


def test_rot_eats_territory_gradually_not_all_at_once():
    """⚠ **마감에 한 번에 지우지 않는다.** 매초 ⌈남은칸/남은초⌉ 씩 먹는다.

    막지 않았으면: 썩는 나라가 마지막 순간까지 멀쩡하다가 사라진다 — 그동안
    상한도 수입도 안 줄고, 이웃은 아무것도 못 가져간다."""
    st = doomed(40)
    start = st.tiles(1)
    seen = []
    for _ in range(int(200 / C.TICK_DT)):
        st.tick()
        seen.append(st.tiles(1))
        if not st.players[1].alive:
            break
    assert not st.players[1].alive, "마감 안에 안 죽었다"
    # **중간 단계가 있어야 한다** — 전부 → 0 으로 뛰면 점진적이 아니다
    middles = [n for n in seen if 0 < n < start]
    assert len(middles) > 10, f"중간 단계가 {len(middles)}개뿐이다"
    assert seen == sorted(seen, reverse=True), "영토가 늘었다"


def test_rotted_tiles_become_fallout_not_free_land():
    """⚠ 썩은 칸은 **낙진(황무지)** 이다. 원본 주석: *"Wasteland, not a prize:
    plain relinquish left neutral land the biggest neighbour absorbed for free —
    rot was feeding the one side it never presses."*"""
    st = doomed(40)
    # ⚠ 썩음은 표시 즉시가 아니라 **경고 30초 + 바닥 감쇠 90초 = 120초** 뒤다.
    # 60초만 기다렸다가 "안 썩는다"고 볼 뻔했다.
    for _ in range(int(300 / C.TICK_DT)):
        st.tick()
        if st.tiles(1) < 40:
            break
    assert st.tiles(1) < 40, "아직 안 썩었다"
    assert int(st.fallout.mask.sum()) > 0, "썩은 칸이 낙진이 안 됐다"


def test_recovering_throws_away_the_rot_progress():
    """바 위로 돌아오면 진행이 **통째로** 사라진다(원본도 `rotState.delete`)."""
    st = doomed(40)
    for _ in range(int(300 / C.TICK_DT)):       # 경고 30 + 바닥 감쇠 90 을 넘긴다
        st.tick()
        if 1 in st._rot:
            break
    assert 1 in st._rot, "썩기 시작하지 않았다"

    st.clock.marked_at.pop(1, None)             # 바 위로 회복
    st.players[1].troops = 1_000_000.0
    st.tick()
    assert 1 not in st._rot, "진행이 남아 있다"


def test_rot_starts_at_the_floor_not_at_the_deadline():
    """썩음은 **바닥에 닿아서** 시작한다. 병력을 지켜 낸 나라는 아직 안 썩는다.

    ⚠ 이게 없으면 반격 창(`floor_decay_seconds`)이 의미를 잃는다."""
    st = doomed(40)
    p = st.players[1]
    cap = p.max_troops(st.tiles(1))
    p.troops = cap                              # 바닥보다 한참 위
    el = st.elapsed + st.clock.cfg.warn_seconds + st.clock.cfg.floor_decay_seconds + 1
    assert not st.clock.rotting(1, el, p.troops, cap), "바닥 위인데 썩는다"
    p.troops = 0.0
    assert st.clock.rotting(1, el, p.troops, cap), "바닥인데 안 썩는다"

    # ⚠ **반격 창 안에서는 병력이 0 이어도 아직 안 썩는다.** 이 대조군이 없으면
    # `past_warn < floor_decay_seconds` 검사를 지워도 아무도 안 깨진다(변이 N3).
    early = st.elapsed + st.clock.cfg.warn_seconds + 1
    assert not st.clock.rotting(1, early, 0.0, cap),         "바닥 감쇠가 끝나기 전에 썩기 시작했다 — 반격 창이 사라진다"


def test_rot_takes_the_whole_deadline_not_a_tenth_of_it():
    """썩는 속도는 **초당** 쿼터다. tick 당으로 돌리면 10배 빨라져 마감이
    150초가 아니라 15초가 된다.

    ⚠ 변이 N5 가 이 재료 없이는 안 잡혔다 — "점진적인가"만 보면 10배 빨라도
    여전히 점진적이기 때문이다. **걸린 시간**을 재야 한다."""
    st = doomed(40)
    started = None
    for i in range(int(400 / C.TICK_DT)):
        st.tick()
        if started is None and st.tiles(1) < 40:
            started = st.elapsed
        if not st.players[1].alive:
            break
    assert started is not None and not st.players[1].alive
    took = st.elapsed - started
    # 40칸을 초당 ⌈남은칸/남은초⌉ 로 먹으면 마감(150초)에 맞춰 끝난다.
    # tick 당으로 돌면 4초쯤에 끝난다.
    assert took > 20.0, f"{took:.0f}초 만에 다 먹었다 — 초당이 아니라 tick 당이다"


def test_the_drain_uses_max_troops_not_current():
    """⚠ **상한에 곱한다. 현재 병력이 아니다.**

    현재 병력에 곱하면 줄어들수록 유출이 줄어 **수입과 균형을 이루고 멈춘다** —
    실측으로 상한 102,000 짜리가 62,139 에서 멎어 바닥(5,100)에 영영 안 닿았다.

    ⚠ 한 tick 의 감소량으로 재려다 실패했다. **병력 성장이 같이 들어와** 값이
    음수로 나온다(측정이 대상을 못 고른 것이다). 관찰 가능한 것은 하나뿐이다 —
    **실제로 바닥에 닿는가.**"""
    st = doomed(40)
    p = st.players[1]
    p.troops = p.max_troops(st.tiles(1))
    reached = False
    for _ in range(int(400 / C.TICK_DT)):
        st.tick()
        if not p.alive:
            reached = True                      # 썩어 사라졌다 = 바닥을 지났다
            break
        floor = (st.clock.troop_floor_fraction(1, st.elapsed)
                 * p.max_troops(max(1, st.tiles(1))))
        if p.troops <= floor * 1.01:
            reached = True
            break
    assert reached, ("바닥에 영영 안 닿는다 — 현재 병력에 곱하면 유출이 수입과 "
                     "균형을 이뤄 멈춘다(실측 62,139 대 바닥 5,100)")
