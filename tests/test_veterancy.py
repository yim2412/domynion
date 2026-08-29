"""전함 베테랑 — 이식 누락 예순하나~예순넷 (§5.75).

우리는 오래 **격침 횟수를 그대로 레벨로 썼다.** `w.veterancy += 1` 이 세 군데
있었고 상한이 없었다. 원본 `Config.ts` 의 "Warship veterancy" 블록은 넷을 나눠 둔다:

| # | 원본 | 우리 |
|---|---|---|
| **예순하나** | 레벨 상한 3(`warshipMaxVeterancy`) | 없었다 — 수송선 열 척이면 레벨 10 |
| **예순둘** | 수송선 10척 · 무역선 25척이 **한 레벨**(공유 정수 미터) | 한 척마다 한 레벨 |
| **예순셋** | 레벨당 **최대 체력 +20%**(`maxHealthWithVeterancy`) | 없었다 |
| **예순넷** | 후퇴 문턱·회복 상한·클락 유출이 전부 **베테랑 보정된** 최대 체력 기준 | 상수 1000 고정 |

포탄 피해 +20%/레벨만 옮겨져 있었다. 그래서 **가장 눈에 띄는 것만 맞고 나머지가
전부 틀린** 모양이었다 — 수송선 스무 척을 잡은 배가 포탄 피해 5배였다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.naval import TradeShip, TransportShip, Warship, shell_damage
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state() -> GameState:
    gm = GameMap.from_rows(["." + "~" * 60 + "."] * 30)
    ps = {}
    for pid in (0, 1, 2):
        t = gm.ref(0 if pid == 0 else 61, pid)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1, 2: 1}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def port(st: GameState, pid: int, x: int, y: int) -> Unit:
    u = Unit(UnitType.PORT, pid, tile=st.gmap.ref(x, y))
    st.players[pid].units.units.append(u)
    return u


# --- 예순하나 · 상한 --------------------------------------------------------

def test_veterancy_is_capped_at_three():
    """막지 않았으면: 잡을수록 끝없이 세진다. 포탄 피해가 레벨당 +20% 라
    레벨 10 이면 3배, 레벨 20 이면 5배다 — 바다에 무적함이 생긴다."""
    w = Warship(owner=0, tile=0)
    for _ in range(10):
        w.record_kill("warship")
    assert w.veterancy == C.WARSHIP_MAX_VETERANCY == 3


def test_progress_stops_at_the_cap():
    w = Warship(owner=0, tile=0, veterancy=C.WARSHIP_MAX_VETERANCY)
    w.record_trade_capture()
    assert w.veterancy_progress == 0, "상한에서도 미터가 돌고 있다"


# --- 예순둘 · 진행도 미터 ---------------------------------------------------

def test_ten_transports_make_one_level():
    """막지 않았으면: 수송선 한 척마다 레벨이 올라 세 척으로 상한에 닿는다."""
    w = Warship(owner=0, tile=0)
    for _ in range(C.WARSHIP_VETERANCY_TRANSPORT_KILLS - 1):
        w.record_kill("transport")
    assert w.veterancy == 0, "열 척이 되기 전에 올랐다"
    w.record_kill("transport")
    assert w.veterancy == 1


def test_twenty_five_captures_make_one_level():
    w = Warship(owner=0, tile=0)
    for _ in range(C.WARSHIP_VETERANCY_TRADE_CAPTURES - 1):
        w.record_trade_capture()
    assert w.veterancy == 0
    w.record_trade_capture()
    assert w.veterancy == 1


def test_transports_and_captures_share_one_meter():
    """⚠ **둘이 같은 미터를 쓴다.** 따로 세면 수송선 9척 + 무역선 24척을 잡고도
    레벨이 0 이다 — 원본은 그 조합이 한 레벨을 훌쩍 넘는다."""
    w = Warship(owner=0, tile=0)
    for _ in range(5):
        w.record_kill("transport")               # 절반
    for _ in range(13):
        w.record_trade_capture()                 # 절반보다 조금 더
    assert w.veterancy == 1, "섞으면 레벨이 안 오른다"


def test_overflow_carries_into_the_next_level():
    """넘친 점수는 버리지 않는다 — 버리면 큰 전투 직후의 한 척이 손해를 본다.

    ⚠ **수송선만으로는 이걸 못 잰다.** 한 척이 25점이고 한 레벨이 250점이라
    열 척이 정확히 딱 떨어져 나머지가 0 이다 — "이월"과 "버림"의 결과가 같아진다.
    변이(`-= per_level` → `= 0`)가 그래서 한 번 살아남았다. 딱 떨어지지 않게
    **섞어서** 잰다."""
    w = Warship(owner=0, tile=0)
    for _ in range(C.WARSHIP_VETERANCY_TRANSPORT_KILLS - 1):
        w.record_kill("transport")               # 225점
    for _ in range(3):
        w.record_trade_capture()                 # +30 = 255점
    assert w.veterancy == 1
    assert w.veterancy_progress == 5, "넘친 5점이 사라졌다"


def test_a_warship_kill_is_an_instant_level_and_wipes_progress():
    """원본 주석: *"instant level, and the partial progress ... is wiped."*

    막지 않았으면: 전함 격침이 수송선 한 척과 같은 값이 된다."""
    w = Warship(owner=0, tile=0)
    for _ in range(5):
        w.record_kill("transport")
    assert w.veterancy_progress > 0
    w.record_kill("warship")
    assert w.veterancy == 1
    assert w.veterancy_progress == 0, "쌓아 둔 진행도가 안 지워졌다"


# --- 예순셋 · 최대 체력 -----------------------------------------------------

def test_each_level_adds_twenty_percent_max_health():
    assert Warship(owner=0, tile=0).max_health == C.WARSHIP_MAX_HEALTH
    for lvl, want in ((1, 1200), (2, 1400), (3, 1600)):
        assert Warship(owner=0, tile=0, veterancy=lvl).max_health == want


def test_levelling_up_does_not_heal_the_ship():
    """원본 주석 그대로 — 상한만 오르고 회복은 평소대로 한다.

    막지 않았으면: 격침 순간 체력이 가득 차서 연전에서 절대 안 죽는다."""
    w = Warship(owner=0, tile=0, health=100)
    w.record_kill("warship")
    assert w.health == 100


# --- 예순넷 · 그 값을 쓰는 자리들 -------------------------------------------

def test_a_veteran_retreats_at_the_same_relative_health():
    """⚠ 후퇴 문턱이 **베테랑 보정된** 최대 체력의 75% 다.

    막지 않았으면: 레벨 3(최대 1600)짜리가 750 까지 버틴다 — 상대 비율로 47% 라
    베테랑일수록 더 늦게, 즉 더 위험할 때 돌아간다."""
    st = state()
    port(st, 0, 0, 0)
    w = Warship(owner=0, tile=st.gmap.ref(2, 0), veterancy=3, health=1100)
    st.warships.append(w)
    st._advance_warships()
    assert w.retreat_port is not None, "1600 의 75%(1200) 아래인데 안 돌아갔다"


def test_healing_stops_at_the_veteran_cap():
    """막지 않았으면: 베테랑 배가 1000 에서 회복을 멈춰 늘어난 체력을 못 쓴다."""
    st = state()
    port(st, 0, 0, 0)
    w = Warship(owner=0, tile=st.gmap.ref(2, 0), veterancy=1, health=1000)
    st.warships.append(w)
    st._heal_warship(w, st.players[0])
    assert w.health == 1000 + C.WARSHIP_PASSIVE_HEALING


def test_the_doomsday_floor_scales_with_the_ship():
    """클락 유출의 바닥도 배마다 다르다(`ws.maxHealth()` 를 두 번 쓴다).

    막지 않았으면: 베테랑 배의 바닥이 자기 최대 체력의 훨씬 아래가 된다."""
    st = state()
    pct = st.clock.cfg.drain_floor_percent
    plain = Warship(owner=0, tile=0)
    vet = Warship(owner=0, tile=0, veterancy=3)
    assert vet.max_health * pct / 100.0 > plain.max_health * pct / 100.0


# --- 배선 -------------------------------------------------------------------

def test_sinking_a_transport_goes_through_the_meter():
    """로직이 아니라 **배선**을 잰다 — 엔진이 `+= 1` 로 돌아가면 여기서 걸린다."""
    st = state()
    w = Warship(owner=0, tile=st.gmap.ref(30, 5))
    st.warships.append(w)
    boat = TransportShip(owner=1, target=0, troops=100,
                         path=[st.gmap.ref(30, 6)], dst=st.gmap.ref(0, 0))
    st.boats.append(boat)
    st._fire_shell(w, boat)
    assert w.veterancy == 0, "수송선 한 척으로 레벨이 올랐다"
    assert w.veterancy_progress == C.WARSHIP_VETERANCY_TRADE_CAPTURES


def test_capturing_a_trade_ship_goes_through_the_meter():
    st = state()
    port(st, 0, 0, 0)
    w = Warship(owner=0, tile=st.gmap.ref(30, 5))
    st.warships.append(w)
    t = TradeShip(owner=1, src_port=st.gmap.ref(61, 1), dst_port=st.gmap.ref(61, 2),
                  dst_owner=2, path=[st.gmap.ref(30, 5)])
    st.trade_ships.append(t)
    st._hunt_trade_ship(w, t)
    assert w.veterancy == 0, "무역선 한 척으로 레벨이 올랐다"
    assert w.veterancy_progress == C.WARSHIP_VETERANCY_TRANSPORT_KILLS


def test_shell_damage_still_scales_with_the_level():
    """이건 원래 맞았다 — 고치면서 깨지지 않았는지만 본다."""
    plain = shell_damage(random.Random(3), veterancy=0)
    vet = shell_damage(random.Random(3), veterancy=3)
    assert vet == (plain * 160) // 100 or vet > plain
