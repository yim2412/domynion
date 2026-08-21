"""HUD — 공격 비율 조절과 면역 바.

비율 슬라이더는 이미 있었지만 **키로 못 움직였고 몇 명이 가는지 안 보였다.**
원본은 T/Y 로 10%p 씩 움직이고 % 옆에 실제 병력 수를 같이 쓴다
(`ControlPanel.ts` / `UserSettings.ts :: attackRatioIncrement` = 10).
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication                          # noqa: E402

from domynion.core import constants as C                          # noqa: E402
from domynion.core.buildings import DefensePostIndex              # noqa: E402
from domynion.core.engine import GameState                        # noqa: E402
from domynion.core.gamemap import GameMap                         # noqa: E402
from domynion.core.state import PlayerState                       # noqa: E402
from domynion.ui.hud import (SCOREBOARD_ROWS, ControlBar,          # noqa: E402
                             ImmunityBar, Scoreboard)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def state(tick: int = 0) -> GameState:
    gm = GameMap.from_rows(["." * 20] * 4)
    p = PlayerState(pid=0, name="P0", start=gm.ref(0, 0))
    p.kind = "human"
    p.troops = 40_000.0
    gm.owner[gm.ref(0, 0)] = 0
    st = GameState(gmap=gm, players={0: p}, rng=random.Random(0))
    st._counts = {0: 1}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = tick
    return st


# --- 공격 비율 --------------------------------------------------------------

def test_slider_range_and_default_match_the_original(qapp):
    c = ControlBar(state(), 0)
    assert (c.slider.minimum(), c.slider.maximum()) == (1, 100)
    assert c.slider.value() == 20              # UserSettings :: attackRatio 기본 0.2


def test_ratio_reaches_the_engine_at_a_non_default_value(qapp):
    """**기본값으로 재면 배선이 끊겨도 통과한다.** 65% 로 움직여서 잰다."""
    st = state()
    c = ControlBar(st, 0)
    c.ratio_changed.connect(lambda r: setattr(st.players[0], "attack_ratio", r))
    c.slider.setValue(65)
    assert st.players[0].attack_ratio == pytest.approx(0.65)


def test_ten_point_steps(qapp):
    c = ControlBar(state(), 0)
    c.slider.setValue(30)
    c.nudge_ratio(+C.ATTACK_RATIO_STEP)
    assert c.slider.value() == 40
    c.nudge_ratio(-C.ATTACK_RATIO_STEP)
    assert c.slider.value() == 30


def test_one_percent_snaps_to_ten_not_eleven(qapp):
    """원본 주석 그대로 — 최저값에서 올리면 눈금과 어긋나 11% 가 된다."""
    c = ControlBar(state(), 0)
    c.slider.setValue(1)
    c.nudge_ratio(+C.ATTACK_RATIO_STEP)
    assert c.slider.value() == 10


def test_ratio_is_clamped_to_one_percent_and_a_hundred(qapp):
    """0% 를 허용하면 공격이 아무 일도 안 하는데 이유를 알 수 없다."""
    c = ControlBar(state(), 0)
    c.slider.setValue(5)
    c.nudge_ratio(-C.ATTACK_RATIO_STEP)
    assert c.slider.value() == 1
    c.slider.setValue(95)
    c.nudge_ratio(+C.ATTACK_RATIO_STEP)
    assert c.slider.value() == 100


def test_label_shows_how_many_troops_actually_go(qapp):
    """% 만 보여주면 그게 몇 명인지 모른다."""
    c = ControlBar(state(), 0)         # 병력 40,000
    c.slider.setValue(25)
    assert "25%" in c.ratio_label.text()
    assert "10,000" in c.ratio_label.text()


# --- 면역 바 ----------------------------------------------------------------

def test_immunity_bar_shows_then_hides(qapp):
    st = state(tick=0)
    bar = ImmunityBar(st, 0)
    bar.refresh()
    assert bar.isVisible() and bar.ratio == pytest.approx(1.0)

    st.tick_count = C.SPAWN_IMMUNITY_TICKS // 2
    bar.refresh()
    assert bar.ratio == pytest.approx(0.5), "남은 시간에 비례해 줄어야 한다"

    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    bar.refresh()
    assert not bar.isVisible()


def test_bots_never_see_an_immunity_bar(qapp):
    """봇은 `isImmune()` 자체가 false — 바가 뜨면 규칙과 화면이 어긋난다."""
    st = state(tick=0)
    st.players[0].kind = "bot"
    bar = ImmunityBar(st, 0)
    bar.refresh()
    assert not bar.isVisible()


# --- 원본 규모(472명)에서도 읽히는가 ----------------------------------------

def big_state(n: int) -> GameState:
    gm = GameMap.from_rows(["." * (n * 3 + 10)] * 4)
    players = {}
    for pid in range(n):
        t = gm.ref(pid * 3, 0)
        gm.owner[t] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=t)
        p.kind = "human" if pid == 0 else "bot"
        p.troops = 1000.0 * (pid + 1)
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: pid + 1 for pid in players}     # P0 가 꼴찌
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def test_scoreboard_does_not_grow_with_the_player_count(qapp):
    """원본 기본 구성이 472명이다. 다 나열하면 화면 왼쪽 전체를 덮는다."""
    small = Scoreboard(big_state(4))
    big = Scoreboard(big_state(200))
    assert len(small._rows) == len(big._rows) == SCOREBOARD_ROWS


def test_my_row_is_shown_even_when_i_am_last(qapp):
    """막지 않았으면: 판이 커진 뒤 내가 몇 등인지 화면 어디에도 안 나온다."""
    st = big_state(200)
    sb = Scoreboard(st)
    sb.me = 0                      # P0 는 영토가 가장 작다
    sb.refresh()
    texts = [r.text() for r in sb._rows]
    assert any(">P0<" in t or "P0 " in t for t in texts), texts


def test_the_rank_shown_is_the_real_rank(qapp):
    """꼴찌인데 12등으로 적히면 거짓말이 된다."""
    st = big_state(200)
    sb = Scoreboard(st)
    sb.me = 0
    sb.refresh()
    mine = next(t for t in sb._rows if "P0" in t.text()).text() \
        if False else [t.text() for t in sb._rows if "P0 " in t.text()][0]
    assert "200." in mine, mine


def test_a_small_game_leaves_the_extra_rows_empty(qapp):
    """4명짜리 판에서 12줄이 다 채워지면 없는 나라가 보인다."""
    st = big_state(4)
    sb = Scoreboard(st)
    sb.me = 0
    sb.refresh()
    filled = [r.text() for r in sb._rows if r.text()]
    assert len(filled) == 4
