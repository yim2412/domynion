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
from domynion.ui.hud import (RATE_DOWN, RATE_UP, SCOREBOARD_ROWS,   # noqa: E402
                             ControlBar, ImmunityBar, Scoreboard)
from domynion.ui.rates import troop_rate                          # noqa: E402


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


# --- 증가율 (§5.69) ---------------------------------------------------------

def test_the_bar_shows_how_fast_the_army_fills(qapp):
    """막지 않았으면: 도시를 지어도 **상한만** 보이고 속도는 안 보인다."""
    st = state()
    c = ControlBar(st, 0)
    c.refresh()
    rate = troop_rate(st.players[0], st.tiles(0))
    assert rate > 0
    assert f"+{rate:,.0f}/s" in c.troops_label.text()


def test_the_colour_turns_when_the_rate_falls(qapp):
    """색이 곧 신호다 — 원본도 초록/주황으로 방향을 쓴다.

    막지 않았으면: 증가율이 꺾인 것을 숫자를 기억하고 있어야만 알 수 있다."""
    st = state()
    c = ControlBar(st, 0)
    c.refresh()
    assert RATE_UP in c.troops_label.text()
    st.players[0].troops = st.players[0].max_troops(st.tiles(0)) * 0.999
    c.refresh()                              # 상한에 붙어 증가율이 떨어진다
    assert RATE_DOWN in c.troops_label.text()

    # ⚠ **직전 tick 과 견줘야 한다.** 처음 값과 견주면 여기서 주황으로 남는다 —
    # 바닥을 친 뒤 회복하는 중인데 화면은 계속 "꺾이는 중"이라고 말하게 된다.
    st.players[0].troops = st.players[0].max_troops(st.tiles(0)) * 0.5
    c.refresh()
    assert RATE_UP in c.troops_label.text()


def test_a_lump_of_gold_pops_up_and_then_goes_away(qapp):
    st = state()
    c = ControlBar(st, 0)
    st.note_gold_gain(0, 35_000)
    c.refresh()
    assert "+35,000" in c.gold_label.text()
    st.tick_count += 20                      # 2초
    c.refresh()
    assert "+35,000" not in c.gold_label.text()
    assert f"{st.players[0].gold:,}" in c.gold_label.text(), "골드 자체는 남아야 한다"


# --- 클락의 다음 파도가 화면에 뜨는가 (§5.94) ---------------------------------

def test_the_clock_line_shows_the_next_wave_not_just_the_current_bar(qapp):
    """⚠ **배선이다.** `wave_state` 가 맞아도 HUD 가 안 쓰면 화면은 그대로다.

    막지 않았으면: 사람은 기준선만 보고, 다음 파도가 몇 %로 언제 오는지는
    표시된 뒤에야 알게 된다."""
    from domynion.core.doomsday import SCHEDULES

    st = state()
    st.clock.cfg.enabled = True
    st.tick_count = int((SCHEDULES["normal"].grace_seconds + 10) * C.TICK_HZ)

    sb = Scoreboard(st)
    sb.refresh()
    text = sb.clock_label.text()
    assert "기준선" in text, "기준선이 사라졌다"
    assert "오르는 중" in text, f"다음 파도가 안 보인다: {text!r}"


def test_the_clock_line_stays_short_when_the_clock_is_off(qapp):
    """클락이 꺼진 판에서는 시계만 나온다 — 대조군이 없으면 위 테스트는
    "항상 붙인다"도 통과시킨다."""
    st = state()
    st.clock.cfg.enabled = False
    sb = Scoreboard(st)
    sb.refresh()
    assert "기준선" not in sb.clock_label.text()


# --- 전투 패널: 봇의 공격은 목록에 안 띄운다 (§5.100 후보 하나) ----------------
#
# 원본 `AttacksDisplay` 가 `t !== PlayerType.Bot` 으로 자른다. 우리 패널은
# **여섯 줄뿐인데 판에 봇이 400** 이라, 봇 줄이 자리를 채우면 사람이 반응해야
# 하는 나라의 공격이 목록 밖으로 밀린다.

def _panel_state():
    from domynion.core.attack import Attack
    gm = GameMap.from_rows(["." * 40] * 10)
    players = {}
    for pid, kind in ((0, "human"), (1, "nation"), (2, "bot")):
        # ⚠ `kind` 는 **생성자에 넘긴다.** 만든 뒤에 대입하면 `is_bot` 이
        # 갱신되지 않아(생성자에서 계산된다) 봇이 봇으로 안 보인다.
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 10, 0),
                        kind=kind)
        p.troops = 50_000.0
        players[pid] = p
    for y in range(10):
        for x in range(0, 10):
            gm.owner[gm.ref(x, y)] = 0
        for x in range(10, 20):
            gm.owner[gm.ref(x, y)] = 1
        for x in range(20, 30):
            gm.owner[gm.ref(x, y)] = 2
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 100, 1: 100, 2: 100}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    # ⚠ 봇과 나라가 **둘 다** 내 국경에 닿아야 한다. 봇으로 한 열을 통째로
    # 덮으면 나라가 내 땅에서 끊겨 공격 자체가 안 뜬다(그러면 필터가 아니라
    # 지도를 재게 된다 — 재료가 규칙을 가리는 그 자리다).
    for y in range(0, 5):
        gm.owner[gm.ref(10, y)] = 2          # 위쪽 절반은 봇이 내 옆에
    for y in range(5, 10):
        gm.owner[gm.ref(10, y)] = 1          # 아래쪽 절반은 나라가 내 옆에
    a_bot = Attack.launch(gm, 2, 0, 5_000.0, random.Random(0))
    a_nation = Attack.launch(gm, 1, 0, 7_000.0, random.Random(0))
    assert a_bot is not None and a_nation is not None
    st.attacks += [a_bot, a_nation]
    return st


def _rows_text(panel):
    return [lbl.text() for lbl, _ in panel._rows if lbl.text()]


def test_bot_attacks_do_not_take_up_the_panel(qapp):
    from domynion.ui.eventlog import AttacksPanel
    st = _panel_state()
    panel = AttacksPanel(st, me=0)
    panel.refresh()
    rows = _rows_text(panel)
    assert len(rows) == 1
    assert "P1" in rows[0]                      # 나라의 공격만 남는다
    assert "P2" not in rows[0]                  # 봇은 없다


def test_a_nation_attack_is_never_dropped(qapp):
    """⚠ **막지 않았으면 무엇이 일어났을 것인가** — 봇이 여섯 줄을 채운다."""
    from domynion.core.attack import Attack
    st = _panel_state()
    gm = st.gmap
    for extra in range(6):                      # 봇 공격을 여섯 개 더
        a = Attack.launch(gm, 2, 0, 100.0 + extra, random.Random(extra))
        assert a is not None
        st.attacks.append(a)
    from domynion.ui.eventlog import AttacksPanel
    panel = AttacksPanel(st, me=0)
    panel.refresh()
    rows = _rows_text(panel)
    assert len(rows) == 1 and "P1" in rows[0]
    # 필터가 없으면 봇 일곱 줄이 여섯 자리를 전부 채워 P1 이 사라진다.
    assert sum(1 for a in st.attacks if a.attacker == 2) == 7
