"""전투 — openfront `attackLogic()` 공식을 그대로 옮겼는가.

여기 테스트는 **원본 공식을 테스트 안에서 다시 계산해 대조한다.** 값을 하드코딩하면
상수를 바꿔도 통과해서, 배선이 끊긴 것을 못 잡는다.

v0.1 의 `test_blocked_tile_goes_back_to_front_of_queue` 는 **폐기됐다.** 그건
"막힌 칸을 큐 앞에 되돌린다" 는 우리 규칙을 못 박은 것인데, 원본은 정반대로
우선순위 힙에서 **싼 칸부터** 먹는다. 아래 `test_cheap_terrain_is_taken_first` 가
그 자리를 대신한다.
"""

from __future__ import annotations

import math
import random

import pytest

from domynion.core import constants as C
from domynion.core.attack import (Attack, attack_logic, sigmoid,
                                  tiles_per_tick, within)
from domynion.core.constants import Terrain
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState


def human(pid=0, troops=25_000.0) -> PlayerState:
    return PlayerState(pid=pid, name=f"P{pid}", is_bot=False, troops=troops)


def bot(pid=1, troops=10_000.0) -> PlayerState:
    return PlayerState(pid=pid, name=f"P{pid}", is_bot=True, troops=troops)


# --- 중립 분기 --------------------------------------------------------------

def test_neutral_loss_is_mag_over_five_for_humans():
    """`attackLogic()` 중립 분기: 손실은 지형 mag / 5, 수비 손실은 0."""
    gm = GameMap.from_rows([".nA"])
    for x, terrain in ((0, Terrain.PLAINS), (1, Terrain.HIGHLAND), (2, Terrain.MOUNTAIN)):
        r = attack_logic(gm, x, 25_000.0, human(), None, 0, 1)
        assert r.attacker_loss == C.TERRAIN_MAG[terrain] / C.NEUTRAL_LOSS_DIV_HUMAN
        assert r.defender_loss == 0.0


def test_bots_pay_half_against_neutral():
    """봇은 mag/10 — 사람의 절반이다. 이걸 빼면 봇이 확장을 못 한다."""
    gm = GameMap.from_rows(["."])
    assert (attack_logic(gm, 0, 10_000.0, bot(), None, 0, 1).attacker_loss * 2
            == attack_logic(gm, 0, 10_000.0, human(), None, 0, 1).attacker_loss)


def test_neutral_budget_shrinks_as_troops_grow():
    """중립 예산 소모 = within(2000 × max(10,speed) / 병력, 5, 100).

    **병력이 많을수록 한 칸이 예산을 덜 먹는다** = 더 넓게 번진다. 부호를 뒤집으면
    큰 나라일수록 느려져 판이 안 끝난다."""
    gm = GameMap.from_rows(["."])
    small = attack_logic(gm, 0, 1_000.0, human(), None, 0, 1).tiles_used
    big = attack_logic(gm, 0, 100_000.0, human(), None, 0, 1).tiles_used
    assert big < small
    assert small == pytest.approx(within(
        C.NEUTRAL_TILES_USED_NUM * max(10.0, C.TERRAIN_SPEED[Terrain.PLAINS]) / 1_000.0,
        *C.NEUTRAL_TILES_USED_CLAMP))


# --- 플레이어 분기 ----------------------------------------------------------

def test_player_loss_matches_original_formula():
    """0.6 × A + 0.4 × B 를 테스트 안에서 다시 계산해 대조한다."""
    gm = GameMap.from_rows(["."])
    atk, dfn = human(troops=20_000.0), human(pid=1, troops=40_000.0)
    d_tiles, a_tiles = 500, 800
    attack_troops = 4_000.0

    mag = C.TERRAIN_MAG[Terrain.PLAINS]
    sig = 1.0 - sigmoid(d_tiles, C.DEFENSE_DEBUFF_DECAY_RATE, C.DEFENSE_DEBUFF_MIDPOINT)
    large = C.DEFENDER_DEBUFF_FLOOR + C.DEFENDER_DEBUFF_SPAN * sig
    d_loss = dfn.troops / d_tiles
    a = within(dfn.troops / attack_troops, *C.ATTACKER_LOSS_A_CLAMP) * mag \
        * C.ATTACKER_LOSS_A_MULT * large
    b = C.ATTACKER_LOSS_B_MULT * d_loss * (mag / C.ATTACKER_LOSS_B_MAG_DIV)
    want = C.ATTACKER_LOSS_A_WEIGHT * a + C.ATTACKER_LOSS_B_WEIGHT * b

    r = attack_logic(gm, 0, attack_troops, atk, dfn, d_tiles, a_tiles)
    assert r.attacker_loss == pytest.approx(want)
    assert r.defender_loss == pytest.approx(d_loss)


def test_defender_loses_troops_per_tile():
    """수비측은 **타일당 병력**을 잃는다. 영토가 좁을수록 한 칸이 비싸게 팔린다."""
    gm = GameMap.from_rows(["."])
    dfn = human(pid=1, troops=100_000.0)
    wide = attack_logic(gm, 0, 5_000.0, human(), dfn, 10_000, 100).defender_loss
    narrow = attack_logic(gm, 0, 5_000.0, human(), dfn, 100, 100).defender_loss
    assert narrow == pytest.approx(wide * 100)


def test_attacking_a_bot_is_cheaper_for_humans():
    """Human/Nation → Bot 이면 mag × 0.7. 봇끼리는 안 붙는다."""
    gm = GameMap.from_rows(["."])
    d_bot = bot(pid=1, troops=40_000.0)
    d_human = human(pid=1, troops=40_000.0)
    vs_bot = attack_logic(gm, 0, 5_000.0, human(), d_bot, 500, 500).attacker_loss
    vs_human = attack_logic(gm, 0, 5_000.0, human(), d_human, 500, 500).attacker_loss
    assert vs_bot < vs_human
    # 봇이 공격자면 할인이 없다
    same = attack_logic(gm, 0, 5_000.0, bot(pid=2), d_bot, 500, 500).attacker_loss
    assert same > vs_bot


def test_large_defender_debuff_only_bites_at_scale():
    """`DEFENSE_DEBUFF_MIDPOINT` 은 15만 타일이다 — 우리 지도에서는 거의 안 움직인다.

    **디버프 계수만 따로 잰다.** 처음에는 `attacker_loss` 로 재려 했는데, 그 값에는
    타일당 수비 손실(B 항)이 같이 들어 있어서 디버프가 아니라 영토 크기를 재고 있었다.
    보호 장치를 재는 테스트는 그 장치만 봐야 한다."""
    def debuff(tiles: float) -> float:
        sig = 1.0 - sigmoid(tiles, C.DEFENSE_DEBUFF_DECAY_RATE,
                            C.DEFENSE_DEBUFF_MIDPOINT)
        return C.DEFENDER_DEBUFF_FLOOR + C.DEFENDER_DEBUFF_SPAN * sig

    assert debuff(400_000) < debuff(1_000), "대국 수비자는 방어 디버프를 받는다"
    span = (debuff(1_000) - debuff(37_575)) / debuff(1_000)
    assert span < 0.05, f"우리 지도 범위(1~37,575)에서는 거의 상수여야 하는데 {span:.1%}"


# --- tick 예산 --------------------------------------------------------------

def test_budget_scales_with_border_size():
    """국경이 넓을수록 한 tick 에 더 많이 번진다 — v0.1 에 없던 축이다."""
    assert tiles_per_tick(5_000.0, None, 100) == 100 * C.BUDGET_VS_NEUTRAL_BORDER_MULT
    dfn = human(pid=1, troops=20_000.0)
    assert (tiles_per_tick(5_000.0, dfn, 200)
            == pytest.approx(tiles_per_tick(5_000.0, dfn, 100) * 2))


def test_budget_against_player_is_clamped():
    dfn = human(pid=1, troops=1.0)
    hi = tiles_per_tick(10_000_000.0, dfn, 10) / (10 * C.BUDGET_VS_PLAYER_BORDER_MULT)
    assert hi == pytest.approx(C.BUDGET_VS_PLAYER_CLAMP[1])
    dfn2 = human(pid=1, troops=10_000_000.0)
    lo = tiles_per_tick(1.0, dfn2, 10) / (10 * C.BUDGET_VS_PLAYER_BORDER_MULT)
    assert lo == pytest.approx(C.BUDGET_VS_PLAYER_CLAMP[0])


# --- 우선순위 힙 ------------------------------------------------------------

def test_cheap_terrain_is_taken_first():
    """**v0.1 과 정반대다.** 원본은 싼 지형을 먼저 먹는다.

    막지 않았으면 무엇이 일어나는가: 우선순위를 뒤집으면 산이 먼저 먹힌다."""
    # 시작 칸 오른쪽은 산, 아래는 평야. 둘 다 같은 tick 에 큐에 들어간다.
    gm = GameMap.from_rows([".A", ".."])
    gm.owner[0] = 0
    rng = random.Random(0)
    a = Attack.launch(gm, 0, None, 25_000.0, rng, tick=0)
    assert a is not None
    order = [t for _, t in sorted(a.heap)]
    assert order[0] == gm.ref(0, 1), "평야(아래)가 산(오른쪽)보다 먼저 나와야 한다"


def test_more_owned_neighbors_raises_priority():
    """내 영토에 많이 접한 칸이 먼저 먹힌다 — 영토가 덩어리로 자란다."""
    gm = GameMap.from_rows(["...", "...", "..."])
    gm.owner[gm.ref(0, 0)] = 0
    gm.owner[gm.ref(1, 0)] = 0
    gm.owner[gm.ref(0, 1)] = 0
    rng = random.Random(0)
    a = Attack.launch(gm, 0, None, 25_000.0, rng, tick=0)
    pri = dict((t, p) for p, t in a.heap)
    corner = gm.ref(1, 1)      # 내 칸 둘에 접함
    edge = gm.ref(2, 0)        # 내 칸 하나에 접함
    assert pri[corner] < pri[edge]


# --- 진행 -------------------------------------------------------------------

def test_expansion_conquers_and_drains_troops():
    gm = GameMap.from_rows(["." * 12] * 12)
    gm.owner[0] = 0
    atk = human()
    rng = random.Random(1)
    a = Attack.launch(gm, 0, None, 5_000.0, rng, 0)
    before = a.troops
    taken = a.step(gm, atk, None, 0, 1, rng, 1)
    assert taken, "한 칸도 안 먹었다"
    assert a.troops < before
    assert all(gm.owner[t] == 0 for t in taken)


def test_army_dies_without_retreating_when_troops_run_out():
    """병력이 1 미만이면 **소멸**한다 — 퇴각이 아니라서 병력이 안 돌아온다."""
    gm = GameMap.from_rows(["." * 20] * 20)
    gm.owner[0] = 0
    rng = random.Random(2)
    a = Attack.launch(gm, 0, None, 30.0, rng, 0)
    for tick in range(1, 60):
        a.step(gm, human(), None, 0, 1, rng, tick)
        if a.finished:
            break
    assert a.finished
    assert not a.retreated, "소멸이어야 한다 — 퇴각이면 병력이 본국에 돌아간다"
    assert a.troops < C.ATTACK_MIN_TROOPS


def test_army_retreats_when_nothing_left_to_take():
    """먹을 것이 없으면 퇴각한다 — 남은 병력은 엔진이 돌려준다."""
    gm = GameMap.from_rows(["..~"])
    gm.owner[0] = 0
    rng = random.Random(3)
    a = Attack.launch(gm, 0, None, 25_000.0, rng, 0)
    for tick in range(1, 10):
        a.step(gm, human(), None, 0, 1, rng, tick)
        if a.finished:
            break
    assert a.retreated and a.troops > 0.0


def test_stale_tile_is_dropped_not_requeued():
    """큐에 들어간 뒤 남이 먼저 먹었으면 **버린다.** 되돌리면 같은 칸에서 맴돈다."""
    gm = GameMap.from_rows(["...."])
    gm.owner[0] = 0
    rng = random.Random(4)
    a = Attack.launch(gm, 0, None, 25_000.0, rng, 0)
    gm.owner[1] = 9                      # 다른 사람이 먼저 먹었다
    taken = a.step(gm, human(), None, 0, 1, rng, 1)
    assert 1 not in taken
    assert 1 not in [t for _, t in a.heap]


def test_impassable_is_never_conquered():
    # 시작 칸 바로 옆이 통행불가면 공격 자체가 안 붙으므로, 평야 한 칸을 사이에 둔다
    gm = GameMap.from_rows(["..#."])
    gm.owner[0] = 0
    rng = random.Random(5)
    a = Attack.launch(gm, 0, None, 25_000.0, rng, 0)
    for tick in range(1, 10):
        a.step(gm, human(), None, 0, 1, rng, tick)
        if a.finished:
            break
    assert gm.owner[2] == -1, "통행 불가 칸을 먹었다"
    assert gm.owner[1] == 0, "그 앞의 평야는 먹었어야 한다"
