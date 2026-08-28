"""HUD 증가율 — 원본 `ControlPanel.ts` (§5.69).

⚠ **규칙이 도는 것과 그것이 화면에 보이는 것은 다른 문제다.** 병력 증가도 골드
유입도 엔진에서는 내내 돌고 있었는데, 화면에는 **현재값만** 있었다. 도시를 올려도
"지금 얼마나 빨리 차나"가 안 보이면 도시와 공장 중 무엇을 지을지 고를 수가 없다.

`ui/rates.py` 는 순수 계산이라 Qt 없이 잰다(`ui/status.py` 와 같은 방침).
"""

from __future__ import annotations

import random

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState
from domynion.ui.rates import GOLD_PIP_TICKS, gold_pip, rate_rising, troop_rate


def state(tiles: int = 8) -> GameState:
    gm = GameMap.from_rows(["." * 40] * 4)
    players = {}
    for pid in (0, 1):
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 20, 0))
        p.kind = "human"
        p.troops = 40_000.0
        p.gold = 1_000
        players[pid] = p
        for x in range(pid * 20, pid * 20 + tiles):
            gm.owner[gm.ref(x, 0)] = pid
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: tiles, 1: tiles}
    st._posts = DefensePostIndex(gm.size)
    return st


# --- 병력 증가율 ------------------------------------------------------------

def test_the_rate_is_per_second_not_per_tick():
    """⚠ `troop_increase()` 는 **tick 당**이다. 원본은 여기에 10을 곱해 초당으로 쓴다.

    막지 않았으면: 화면에 실제의 1/10 이 뜬다. 그래도 그럴듯한 숫자라 눈으로는
    영영 안 잡힌다 — 그래서 **1초를 실제로 굴려** 대조한다."""
    st = state()
    p = st.players[0]
    shown = troop_rate(p, st.tiles(0))

    before = p.troops
    for _ in range(C.TICK_HZ):              # 1초를 실제로 굴린다
        p.troops += p.troop_increase(st.tiles(0))
    actual = p.troops - before

    assert abs(shown - actual) / actual < 0.02, (
        f"화면 {shown:.1f}/s 인데 1초 동안 실제로는 {actual:.1f} 늘었다")


def test_the_rate_reads_my_actual_land_not_a_fixed_number():
    """상한이 땅에서 나오므로(`max_troops`) 땅이 넓으면 같은 병력도 더 빨리 찬다.

    막지 않았으면: 타일 수를 상수로 넘겨도 통과한다 — 배선이 끊긴 채로."""
    narrow, wide = state(tiles=2), state(tiles=30)
    assert (troop_rate(wide.players[0], wide.tiles(0))
            > troop_rate(narrow.players[0], narrow.tiles(0)))


def test_a_full_army_grows_at_zero():
    st = state()
    p = st.players[0]
    p.troops = p.max_troops(st.tiles(0))
    assert troop_rate(p, st.tiles(0)) == 0.0


def test_a_flat_rate_still_counts_as_rising():
    """원본이 `>=` 다. `>` 로 두면 상한에 붙어 증가율이 0으로 굳었을 때 색이
    영영 경고로 남아 **"줄고 있다"는 잘못된 신호**가 된다."""
    assert rate_rising(5.0, 5.0)
    assert rate_rising(6.0, 5.0)
    assert not rate_rising(4.0, 5.0)


# --- 골드 `+N` --------------------------------------------------------------

def test_nothing_shows_when_no_lump_has_arrived():
    assert gold_pip(state(), 0) is None


def test_the_pip_expires_after_two_seconds():
    st = state()
    st.tick_count = 100
    st.note_gold_gain(0, 35_000)
    assert gold_pip(st, 0) == 35_000
    st.tick_count = 100 + GOLD_PIP_TICKS - 1
    assert gold_pip(st, 0) == 35_000, "2초 안에는 계속 보여야 한다"
    st.tick_count = 100 + GOLD_PIP_TICKS
    assert gold_pip(st, 0) is None
    assert GOLD_PIP_TICKS == 20, "원본 setTimeout 2000ms · 10Hz"


def test_two_lumps_in_one_tick_do_not_add_up():
    """원본 주석 그대로 — *"Last-wins"*. 합치면 화면에 **일어나지 않은 액수**가 뜬다."""
    st = state()
    st.note_gold_gain(0, 10_000)
    st.note_gold_gain(0, 3_000)
    assert gold_pip(st, 0) == 3_000


def test_a_zero_gain_never_takes_over_the_pip():
    """원본도 `ev.gold > 0` 을 본다. 0을 기록하면 방금 뜬 진짜 액수가 **0으로 덮인다.**"""
    st = state()
    st.note_gold_gain(0, 10_000)
    st.note_gold_gain(0, 0)
    assert gold_pip(st, 0) == 10_000


def test_the_pip_belongs_to_one_player_only():
    st = state()
    st.note_gold_gain(1, 500)
    assert gold_pip(st, 0) is None
    assert gold_pip(st, 1) == 500


# --- 엔진 배선 — 덩어리로 들어오는 골드 넷 -----------------------------------

def test_a_donation_shows_up_on_the_receivers_hud():
    st = state()
    st.players[0].gold = 50_000
    st.diplomacy.form(0, 1, st.tick_count)          # 동맹에게만 기부할 수 있다
    assert st.donate_gold(0, 1, 20_000)
    assert gold_pip(st, 1) == 20_000
    assert gold_pip(st, 0) is None, "준 쪽은 는 것이 아니다"


def test_conquest_loot_shows_up():
    st = state()
    loser = st.players[1]
    loser.gold = 80_000
    loser.kind = "bot"                  # 사람은 절반만·공격 이력 조건이 따로 있다
    st._transfer_conquest_gold(st.players[0], loser)
    assert gold_pip(st, 0) == 80_000
