"""공격 부대 — 비용·정지·프론티어 순서."""

from __future__ import annotations

import pytest

from domynion.core import constants as C
from collections import deque

from domynion.core.attack import Attack
from domynion.core.constants import Terrain
from domynion.core.state import PlayerState

from conftest import make_map


def _atk(**kw) -> PlayerState:
    return PlayerState(pid=0, name="A", **kw)


def test_neutral_cost_is_base_times_terrain(plains5):
    plains5[(0, 0)].owner = 0
    a = Attack.launch(plains5, 0, None, troops=1000.0)
    assert a is not None
    cost = a.tile_cost(plains5, (1, 0), _atk(), def_factor=1.0)
    assert cost == C.CONQUER_COST_BASE * C.TERRAIN_DEFENSE[Terrain.PLAINS]


def test_mountain_costs_more_than_plains(plains5):
    gm = make_map([".A...", "....."])
    gm[(0, 0)].owner = 0
    a = Attack.launch(gm, 0, None, troops=1000.0)
    plains = a.tile_cost(gm, (0, 1), _atk(), 1.0)
    mountain = a.tile_cost(gm, (1, 0), _atk(), 1.0)
    assert mountain > plains
    assert mountain / plains == pytest.approx(
        C.TERRAIN_DEFENSE[Terrain.MOUNTAINS] / C.TERRAIN_DEFENSE[Terrain.PLAINS])


def test_expansion_stops_when_troops_run_out(plains5):
    """부대는 병력이 떨어지는 지점에서 저절로 멈춘다 — 이게 확장의 유일한 제동이다."""
    plains5[(0, 0)].owner = 0
    per_tile = C.CONQUER_COST_BASE * C.TERRAIN_DEFENSE[Terrain.PLAINS]
    a = Attack.launch(plains5, 0, None, troops=per_tile * 5 + 0.1)
    atk = _atk()
    taken = 0
    for _ in range(200):
        taken += len(a.step(plains5, C.TICK_DT, atk, 1.0))
        if a.finished:
            break
    assert taken == 5, f"{per_tile:.2f}씩 5칸만 살 수 있는데 {taken}칸을 먹었다"


def test_blocked_tile_goes_back_to_front_of_queue():
    """감당 못 하는 칸을 만나면 **그 자리에서 멈춘다.**

    큐 뒤로 보내면 부대가 산을 피해 평야만 골라 먹어 지형 방어가 무의미해진다.
    보호를 뜯어냈을 때 무엇이 일어나는가: 아래 평야(0,1)가 대신 먹힌다."""
    gm = make_map([".A", ".."])       # 오른쪽은 산, 아래는 평야
    gm[(0, 0)].owner = 0
    mountain_cost = C.CONQUER_COST_BASE * C.TERRAIN_DEFENSE[Terrain.MOUNTAINS]
    plains_cost = C.CONQUER_COST_BASE * C.TERRAIN_DEFENSE[Terrain.PLAINS]
    assert plains_cost < mountain_cost, "이 테스트는 산이 더 비쌀 때만 의미가 있다"

    # 산이 큐 맨 앞에 오도록 부대를 직접 만든다 (launch 는 순서를 보장하지 않는다)
    a = Attack(attacker=0, target=None, troops=mountain_cost - 0.01,
               frontier=deque([(1, 0), (0, 1)]), seen={(1, 0), (0, 1)})
    # dt 는 1초로 준다. TICK_DT(0.05) 로는 이번 tick 예산이 0칸이라 루프가 아예
    # 돌지 않고, 그러면 이 테스트는 아무것도 재지 않는다 (실제로 그랬다).
    taken = a.step(gm, 1.0, _atk(), 1.0)
    assert a.budget(_atk(), 1.0) >= 1, "예산이 0이면 아래 단언은 공짜로 통과한다"

    assert taken == [], "산에서 막혔는데 평야를 대신 먹었다 — 큐 뒤로 밀렸다는 뜻"
    assert a.frontier[0] == (1, 0), "막힌 칸이 큐 앞에 되돌아가지 않았다"


def test_defense_factor_raises_cost(plains5):
    """방어측이 병력을 두껍게 채워 둘수록 비싸진다."""
    plains5[(0, 0)].owner = 0
    plains5[(1, 0)].owner = 1
    a = Attack.launch(plains5, 0, 1, troops=1000.0)
    assert a is not None
    bare = a.tile_cost(plains5, (1, 0), _atk(), def_factor=1.0)
    dug_in = a.tile_cost(plains5, (1, 0), _atk(), def_factor=1.0 + C.DEFENDER_FILL_MULT)
    assert dug_in == bare * (1.0 + C.DEFENDER_FILL_MULT)


def test_carry_lets_slow_army_eventually_advance(plains5):
    """초당 6칸을 20Hz 로 쪼개면 tick 당 0.3칸이다. 버리면 영원히 한 칸도 못 먹는다."""
    plains5[(0, 0)].owner = 0
    a = Attack.launch(plains5, 0, None, troops=1000.0)
    atk = _atk()
    first = [len(a.step(plains5, C.TICK_DT, atk, 1.0)) for _ in range(10)]
    assert sum(first) > 0, "소수 누적이 없으면 느린 부대가 한 칸도 못 먹는다"


def test_augment_discount_lowers_cost(plains5):
    """증강은 새 규칙이 아니라 계수다 — 같은 공식에 배율만 곱해져야 한다."""
    plains5[(0, 0)].owner = 0
    a = Attack.launch(plains5, 0, None, troops=1000.0)
    plain = a.tile_cost(plains5, (1, 0), _atk(), 1.0)
    discounted = a.tile_cost(plains5, (1, 0), _atk(augments={"settlers": 1}), 1.0)
    assert discounted < plain
    assert discounted == plain * _atk(augments={"settlers": 1}).cost_mult(
        Terrain.PLAINS, vs_player=False)
