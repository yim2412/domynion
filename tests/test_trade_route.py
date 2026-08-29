"""무역선의 항해 — 이식 누락 여든둘·여든셋 (§5.81).

`TradeShipExecution.ts`(218줄)는 **이 문서가 한 번도 언급하지 않은 파일**이었다.
§5.35(스폰 횟수)와 §5.36(나포)이 그 주변을 다뤘지만 항해 자체는 안 봤다.

| # | 원본 | 우리 |
|---|---|---|
| **여든둘** | 골드는 **지나온 칸 수**(`tilesTraveled`)로 매긴다 | 계획된 경로 길이(`len(path)`) |
| **여든셋** | **목적지 항구가 사라지면 배를 지운다**(`!dstPort.isActive()`) | 주인만 살아 있으면 계속 갔다 |

여든둘은 **나포에서 드러난다.** 나포하면 경로가 해적 항구로 새로 깔리는데,
`len(path)` 로 재면 그 짧은 거리만 값을 쳐 준다 — 반대편 대륙까지 갔다가 잡힌
배와 항구 앞에서 잡힌 배가 **같은 값**이 된다. 원본의 `tilesTraveled` 는 배에
붙은 값이라 목적지가 바뀌어도 0으로 안 돌아간다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.naval import TradeShip, Warship, trade_gold
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state() -> GameState:
    gm = GameMap.from_rows(["." + "~" * 60 + "."] * 12)
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
    st.gmap.owner[u.tile] = pid
    st.players[pid].units.units.append(u)
    return u


def ship(st: GameState, owner: int = 1, dst_owner: int = 2,
         length: int = 30) -> TradeShip:
    """오른쪽에서 왼쪽으로 가는 배 하나. 목적지에는 진짜 항구가 있다."""
    port(st, dst_owner, 1, 5)
    t = TradeShip(owner=owner, src_port=st.gmap.ref(50, 5),
                  dst_port=st.gmap.ref(1, 5), dst_owner=dst_owner,
                  path=[st.gmap.ref(x, 5) for x in range(50, 50 - length, -1)])
    st.trade_ships.append(t)
    return t


# --- 여든둘 · 지나온 칸 수 ---------------------------------------------------

def test_the_counter_grows_with_every_step():
    t = ship(st := state())
    for i in range(5):
        t.advance()
        assert t.tiles_travelled == i + 1


def test_gold_uses_the_distance_actually_sailed():
    """도착까지 그대로 가면 계획 길이와 같다 — 대조군이다."""
    st = state()
    t = ship(st, length=4)
    for _ in range(10):
        st.tick_count += 1
        st._advance_trade()
        if t not in st.trade_ships:
            break
    assert st.players[2].gold == pytest.approx(trade_gold(3), rel=0.01)


def test_a_captured_ship_still_pays_for_the_whole_journey():
    """⚠ **여든둘의 본체.** 나포하면 경로가 새로 깔리는데, 그때 `len(path)` 로
    재면 해적 항구까지의 짧은 거리만 값을 쳐 준다.

    막지 않았으면: 멀리까지 갔다가 잡힌 배와 항구 앞에서 잡힌 배가 같은 값이다.
    해적질이 "멀리 나간 배를 노린다"는 성격을 잃는다."""
    st = state()
    port(st, 0, 25, 5)                       # 해적의 항구 — 배가 가는 쪽 바로 앞
    t = ship(st, length=40)
    for _ in range(20):                      # 20칸을 실제로 지나온다
        t.advance()
    assert t.tiles_travelled == 20
    far = t.tiles_travelled
    assert st._capture_trade_ship(t, 0) is True
    assert t.tiles_travelled == far, "나포가 지나온 거리를 지웠다"
    assert len(t.path) < far, "재료: 새 경로가 지나온 거리보다 짧아야 잰다"


def test_the_pirate_is_paid_for_the_long_haul():
    st = state()
    port(st, 0, 25, 5)
    t = ship(st, length=40)
    for _ in range(20):
        t.advance()
    st._capture_trade_ship(t, 0)
    for _ in range(60):
        st.tick_count += 1
        st._advance_trade()
        if t not in st.trade_ships:
            break
    assert t not in st.trade_ships, "재료: 도착했어야 한다"
    assert st.players[0].gold >= trade_gold(20), \
        "해적이 짧은 마지막 구간만큼만 벌었다"


# --- 여든셋 · 목적지 항구가 사라지면 -----------------------------------------

def test_a_ship_is_dropped_when_its_destination_port_is_gone():
    """막지 않았으면: 부서진 항구로 배가 계속 가서 **도착해 골드를 준다.**

    항구는 정복으로 넘어가거나 핵에 부서지거나 스스로 철거된다."""
    st = state()
    t = ship(st)
    st.tick_count += 1
    st._advance_trade()
    assert t in st.trade_ships, "재료: 항구가 있으면 계속 가야 한다"
    st.players[2].units.units.clear()         # 목적지 항구가 사라졌다
    st.tick_count += 1
    st._advance_trade()
    assert t not in st.trade_ships, "항구가 없는데 계속 갔다"


def test_a_port_under_construction_does_not_keep_a_ship_alive():
    """짓는 중인 항구는 `ports` 목록에 안 든다 — 원본 `isActive()` 와 같은 뜻이다."""
    st = state()
    t = ship(st)
    st.players[2].units.units[0].ticks_left = 50    # 아직 짓는 중이다
    st.tick_count += 1
    st._advance_trade()
    assert t not in st.trade_ships


def test_the_gold_is_not_paid_when_the_port_vanished():
    st = state()
    t = ship(st, length=3)
    st.players[2].units.units.clear()
    before = st.players[2].gold
    st.tick_count += 1
    st._advance_trade()
    assert st.players[2].gold == before, "항구가 없는데 골드를 줬다"


def test_other_ships_are_unaffected():
    """한 척이 지워진다고 나머지가 같이 사라지면 안 된다."""
    st = state()
    a = ship(st, dst_owner=2)
    port(st, 1, 40, 5)
    b = TradeShip(owner=2, src_port=st.gmap.ref(1, 5),
                  dst_port=st.gmap.ref(40, 5), dst_owner=1,
                  path=[st.gmap.ref(x, 5) for x in range(2, 40)])
    st.trade_ships.append(b)
    st.players[2].units.units.clear()          # a 의 목적지가 사라졌다
    st.tick_count += 1
    st._advance_trade()
    assert a not in st.trade_ships and b in st.trade_ships
