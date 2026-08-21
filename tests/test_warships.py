"""전함 · 포탄 · MIRV 분열.

전함의 핵심은 **표적 우선순위**다: 수송선 → 적 전함 → 무역선. 수송선이 첫째인 이유는
그게 상륙을 막는 유일한 수단이기 때문이다. 순서를 바꾸면 바다가 무역 사냥터가 되고
상륙이 무제한이 된다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.naval import TradeShip, TransportShip, Warship, shell_damage
from domynion.core.nukes import Fallout, Nuke
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state() -> GameState:
    gm = GameMap.from_rows(["." + "~" * 60 + "."] * 30)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(0 if pid == 0 else 61, pid)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


# --- 포탄 -------------------------------------------------------------------

def test_shell_damage_is_rolled_not_fixed():
    """`(굴림−1)×25 + 200`, 굴림 1~5 → 200~300.

    고정 250 으로 두면 체력 1000 인 전함이 정확히 4발에 죽어 교전이 전부 같아진다."""
    rolls = {shell_damage(random.Random(s)) for s in range(60)}
    assert rolls <= {200, 225, 250, 275, 300}
    assert len(rolls) > 1, "굴림이 안 흔들린다"


def test_veterancy_makes_shells_hurt_more():
    """격침 1회당 +20%."""
    plain = shell_damage(random.Random(3), veterancy=0)
    vet = shell_damage(random.Random(3), veterancy=2)
    assert vet == (plain * 140) // 100 or vet > plain


def test_warship_survives_four_shells_at_least():
    hp = C.WARSHIP_MAX_HEALTH
    assert hp // 300 >= 3 and hp // 200 == 5, "체력 1000 에 200~300 피해 = 4~5발"


# --- 표적 우선순위 -----------------------------------------------------------

def test_transport_ship_is_shot_before_a_trade_ship():
    """수송선이 최우선이다 — 상륙을 막는 유일한 수단이다."""
    st = state()
    w = Warship(owner=0, tile=st.gmap.ref(30, 5))
    st.warships.append(w)
    boat = TransportShip(owner=1, target=0, troops=1000.0,
                         path=[st.gmap.ref(31, 5)], dst=st.gmap.ref(31, 5))
    trade = TradeShip(owner=1, src_port=0, dst_port=1, dst_owner=1,
                      path=[st.gmap.ref(29, 5)])
    st.boats.append(boat)
    st.trade_ships.append(trade)
    st._advance_warships()
    assert boat not in st.boats, "수송선을 안 쳤다"
    assert trade in st.trade_ships, "무역선을 먼저 쳤다"


def test_enemy_warship_is_shot_before_a_trade_ship():
    st = state()
    mine = Warship(owner=0, tile=st.gmap.ref(30, 5))
    foe = Warship(owner=1, tile=st.gmap.ref(32, 5))
    st.warships += [mine, foe]
    st.trade_ships.append(TradeShip(owner=1, src_port=0, dst_port=1, dst_owner=1,
                                    path=[st.gmap.ref(29, 5)]))
    st._advance_warships()
    assert foe.health < C.WARSHIP_MAX_HEALTH, "적 전함을 안 쳤다"
    assert st.trade_ships, "무역선을 먼저 쳤다"


def test_allies_are_not_shot():
    st = state()
    st.diplomacy.form(0, 1, tick=0)
    mine = Warship(owner=0, tile=st.gmap.ref(30, 5))
    friend = Warship(owner=1, tile=st.gmap.ref(31, 5))
    st.warships += [mine, friend]
    st._advance_warships()
    assert friend.health == C.WARSHIP_MAX_HEALTH


def test_targets_outside_range_are_ignored():
    # 사거리 130 을 넘기려면 지도가 그보다 커야 한다. 61×30 지도에서는 대각선이
    # 68 밖에 안 돼 "사거리 밖"을 만들 수 없다 — 아무것도 재지 않는 테스트가 된다.
    gm = GameMap.from_rows(["." + "~" * 298 + "."] * 40)
    ps = {pid: PlayerState(pid=pid, name=f"P{pid}", is_bot=False) for pid in (0, 1)}
    for pid in (0, 1):
        gm.owner[gm.ref(0 if pid == 0 else 299, pid)] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)

    w = Warship(owner=0, tile=st.gmap.ref(1, 1))
    far = Warship(owner=1, tile=st.gmap.ref(290, 35))
    st.warships += [w, far]
    assert st._dist_sq(w.tile, far.tile) > C.WARSHIP_TARGETTING_RANGE ** 2
    st._advance_warships()
    assert far.health == C.WARSHIP_MAX_HEALTH


def test_sinking_grants_veterancy_and_removes_the_wreck():
    st = state()
    mine = Warship(owner=0, tile=st.gmap.ref(30, 5))
    foe = Warship(owner=1, tile=st.gmap.ref(31, 5), health=1)
    st.warships += [mine, foe]
    st._advance_warships()
    assert foe not in st.warships
    assert mine.veterancy == 1


def test_reload_cooldown_limits_fire_rate():
    st = state()
    mine = Warship(owner=0, tile=st.gmap.ref(30, 5))
    foe = Warship(owner=1, tile=st.gmap.ref(31, 5))
    st.warships += [mine, foe]
    st._advance_warships()
    after_first = foe.health
    st._advance_warships()
    assert foe.health == after_first, "재장전 없이 연사했다"
    assert mine.cooldown == C.WARSHIP_SHELL_ATTACK_RATE - 1


# --- 수리 -------------------------------------------------------------------

def test_ports_heal_warships_but_not_for_a_doomed_side():
    """클락에 표시된 쪽은 수리를 못 한다 — 그래야 클락의 유출이 배를 실제로 가라앉힌다."""
    st = state()
    port = Unit(UnitType.PORT, 0, tile=st.gmap.ref(0, 0))
    st.players[0].units.units.append(port)
    w = Warship(owner=0, tile=st.gmap.ref(2, 0), health=500)
    st.warships.append(w)
    st._advance_warships()
    assert w.health == 500 + C.WARSHIP_PASSIVE_HEALING

    st.clock.marked_at[0] = 0.0
    before = w.health
    st._advance_warships()
    assert w.health == before, "표시된 쪽이 수리를 했다"


def test_build_warship_needs_ocean_and_gold():
    st = state()
    p = st.players[0]
    assert st.build_warship(0, st.gmap.ref(5, 5)) is None, "골드가 없다"
    p.gold = 1_000_000
    assert st.build_warship(0, st.gmap.ref(0, 0)) is None, "육지에는 못 띄운다"
    assert st.build_warship(0, st.gmap.ref(5, 5)) is not None
    assert p.gold == 1_000_000 - 250_000


# --- MIRV -------------------------------------------------------------------

def test_mirv_splits_into_many_warheads_scaled_to_the_map():
    """원본은 350발이다. 우리 지도는 원본의 1/16 면적이라 그대로 쓰면 지도가
    통째로 날아간다 — 면적 비로 줄여 같은 *비중*이 되게 한다."""
    st = state()
    n = Nuke(owner=0, utype=UnitType.MIRV, src=st.gmap.ref(1, 1),
             dst=st.gmap.ref(30, 15))
    before = st.fallout._count
    st._split_mirv(n)
    assert st.fallout._count > before, "탄두가 하나도 안 터졌다"
    scaled = max(1, int(C.MIRV_WARHEAD_COUNT * st.gmap.land_count / 2_000_000))
    assert scaled < C.MIRV_WARHEAD_COUNT
