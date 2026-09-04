"""Overtime — 교착 방지 (`OVERTIME_DEFAULTS` / `percentageTilesOwnedToWin`).

§5.115 에서 seed 2 가 22,000 에서도 25,000 에서도 **미종료**였다. 둘이 남아
서로를 못 이기는 판이라 **상한을 늘려서는 안 풀린다.** 원본은 시작 시각을
넘기면 승리 문턱(80%)을 분당 2%p 씩 내리고, **바닥이 없다** — 언젠가 반드시
누군가 넘는다.

⚠ 원본은 기본이 꺼짐이고 로비에서만 켠다. **우리는 싱글이라 로비가 없어
켤지 말지가 곧 판 규칙이다** — 켠 상태를 여기서 못 박는다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState, Victory, domination_percent
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState

MIN = 60


# --- 문턱 곡선 -----------------------------------------------------------------

def test_before_overtime_the_bar_is_the_original_80():
    assert domination_percent(0) == C.DOMINATION_TILE_PERCENT
    assert domination_percent(C.OVERTIME_START_MINUTES * MIN) == \
        C.DOMINATION_TILE_PERCENT


def test_the_bar_falls_after_the_start_time():
    start = C.OVERTIME_START_MINUTES * MIN
    one_min_later = domination_percent(start + MIN)
    assert one_min_later == C.DOMINATION_TILE_PERCENT - \
        C.OVERTIME_DROP_PCT_PER_MINUTE
    # 계속 내려간다 — 한 계단만 재면 상수 하나로도 통과한다.
    assert domination_percent(start + 10 * MIN) < one_min_later


def test_the_bar_reaches_zero_so_no_game_can_stall_forever():
    """⚠ **바닥이 없는 것이 교착을 푸는 유일한 보장이다.**

    하한을 두면 §5.115 의 seed 2 처럼 둘이 남은 판이 그 하한 아래에서 영원히
    버틴다. 0 에 닿는 시각을 계산이 아니라 **훑어서** 확인한다."""
    span = C.DOMINATION_TILE_PERCENT / C.OVERTIME_DROP_PCT_PER_MINUTE
    zero_at = (C.OVERTIME_START_MINUTES + span) * MIN
    assert domination_percent(zero_at) == 0
    assert domination_percent(zero_at * 10) == 0        # 그 뒤로도 0 이다
    # 0 에 닿기 **전**에는 0 이 아니다 — 안 그러면 위 단언이 공짜로 통과한다.
    assert domination_percent(zero_at - MIN) > 0


def test_the_bar_never_goes_negative():
    """음수 문턱이면 `tiles/usable >= 문턱` 이 **죽은 사람에게도** 참이 된다."""
    assert domination_percent(10_000 * MIN) >= 0


def test_whole_percentage_points_only():
    """원본이 초와 %p 를 둘 다 `Math.floor` 로 자른다 — 화면의 정수와 판정이
    쓰는 값이 같아야 한다. 30초가 지나도 아직 안 내려간다(2%p/분 = 60초에 한 칸).

    ⚠ 막지 않았으면 무엇이 일어났을 것인가 — 실수로 재면 30초에 1%p 가 되어
    화면(정수)과 판정이 어긋난다."""
    start = C.OVERTIME_START_MINUTES * MIN
    step = MIN // C.OVERTIME_DROP_PCT_PER_MINUTE      # 한 칸에 걸리는 초
    assert domination_percent(start + step - 1) == C.DOMINATION_TILE_PERCENT
    assert domination_percent(start + step) == C.DOMINATION_TILE_PERCENT - 1
    assert isinstance(domination_percent(start + 5 * MIN), int)


def test_it_is_on_because_we_have_no_lobby():
    """원본 기본은 꺼짐이다. **우리가 켠 것**이고, 그게 곧 판 규칙이다."""
    assert C.OVERTIME_ENABLED is True


# --- 실제 판에서 -----------------------------------------------------------------

def stalemate(tiles_each: int = 20) -> GameState:
    """둘이 남아 서로를 못 이기는 판. 어느 쪽도 80% 를 못 넘는다."""
    gm = GameMap.from_rows(["." * 40] * 5)
    ps = {}
    for pid in (0, 1):
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation",
                              start=gm.ref(pid * 20 + 1, 1))
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._posts = DefensePostIndex(gm.size)
    half = gm.land_count // 2
    st._counts = {0: half, 1: gm.land_count - half}
    return st


def test_a_two_way_split_does_not_win_before_overtime():
    st = stalemate()
    st.tick_count = int(C.OVERTIME_START_MINUTES * MIN * C.TICK_HZ)
    st._check_end()
    assert not st.over, "50% 로는 80% 문턱을 못 넘는다"


def test_the_same_split_wins_once_the_bar_has_fallen_below_it():
    """**같은 판인데 시간만 지나서** 끝난다 — 그것이 Overtime 의 전부다."""
    st = stalemate()
    # 문턱이 50% 아래로 내려가는 시각까지 민다.
    minutes = C.OVERTIME_START_MINUTES
    while domination_percent(minutes * MIN) >= 50:
        minutes += 1
    st.tick_count = int(minutes * MIN * C.TICK_HZ)
    st._check_end()
    assert st.over
    assert st.victory is Victory.DOMINATION
    assert st.winner in (0, 1)


def test_the_bar_the_engine_uses_is_the_one_the_function_returns():
    """⚠ **배선을 기본값이 아닌 값으로 잰다.** `ratio == 0.80` 로 재면 Overtime
    이 통째로 끊겨도 판 초반에는 참이다."""
    st = stalemate()
    st.tick_count = int((C.OVERTIME_START_MINUTES + 5) * MIN * C.TICK_HZ)
    assert st.domination_ratio() < C.DOMINATION_TILE_RATIO
    assert st.domination_ratio() == \
        pytest.approx(domination_percent(st.elapsed) / 100.0)
