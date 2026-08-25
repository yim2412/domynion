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
    # ⚠ 세 명이다. 무역선은 주인과 목적지 주인이 **달라야** 항로가 성립하므로
    # (`canTrade`), 둘만 두면 나포 시험용 배가 자기 자신에게 가는 배가 된다.
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
    """⚠ 전함 주인에게 항구가 없으면 무역선은 **후보에도 안 든다**
    (`hasReachablePort`) — 나포는 끌고 갈 곳이 있어야 성립하기 때문이다.
    무역선 표적 테스트는 이걸 안 붙이면 우선순위를 재지 않고 통과한다."""
    u = Unit(UnitType.PORT, pid, tile=st.gmap.ref(x, y))
    st.gmap.owner[u.tile] = pid
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(UnitType.PORT)
    return u


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
    port(st, 0, 0, 0)                    # 없으면 무역선이 후보에 안 든다
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
    port(st, 0, 0, 0)                    # 없으면 무역선이 후보에 안 든다
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


# --- 나포 (이식 누락 스물) ---------------------------------------------------
#
# 전에는 전함이 무역선을 **격침**시켰다 — 골드가 아무에게도 안 가고 증발했다.
# 원본은 쫓아가 **나포**하고, 도착하면 **나포한 쪽이 전액**을 번다.

def _trade_setup(st: GameState, *, dst_owner: int = 2, far: int = 40) -> TradeShip:
    t = TradeShip(owner=1, src_port=st.gmap.ref(far, 5), dst_port=st.gmap.ref(1, 5),
                  dst_owner=dst_owner,
                  path=[st.gmap.ref(x, 5) for x in range(29, far)])
    st.trade_ships.append(t)
    return t


def test_warship_captures_a_trade_ship_instead_of_sinking_it():
    """나포다. 격침이 아니다.

    ⚠ 격침으로 두면 골드가 증발한다 — 수입 경로 하나가 통째로 사라진다."""
    st = state()
    port(st, 0, 0, 0)
    st.warships.append(Warship(owner=0, tile=st.gmap.ref(30, 5)))
    t = _trade_setup(st)
    for _ in range(20):                  # 추격은 tick 당 2칸이다
        st.tick_count += 1
        st._advance_warships()
    assert t in st.trade_ships, "배가 사라졌다 — 격침시켰다"
    assert t.captured_by == 0, "나포 표시가 안 붙었다"
    assert t.dst_owner == 0 and t.dst_port == st.gmap.ref(0, 0), \
        "나포하면 해적의 항구로 뱃머리를 돌린다"


def test_captured_trade_ship_pays_only_the_pirate():
    """`wasCaptured` 분기 — 원래 주인은 **한 푼도 못 받는다**.

    ⚠ 대조군이 필요하다: 나포 안 된 배는 양쪽이 함께 번다. 그걸 같이 재지 않으면
    "골드가 들어왔다"만 보고 분기를 지워도 통과한다."""
    st = state()
    port(st, 0, 0, 0)
    t = _trade_setup(st, far=32)
    t.captured_by, t.dst_owner, t.dst_port = 0, 0, st.gmap.ref(0, 0)
    t.path = [st.gmap.ref(31, 5), st.gmap.ref(30, 5)]
    g0, g1 = st.players[0].gold, st.players[1].gold
    st.tick_count += 1
    st._advance_trade()
    assert st.players[0].gold > g0, "해적이 못 벌었다"
    assert st.players[1].gold == g1, "뺏긴 쪽이 벌었다"


def test_uncaptured_trade_ship_still_pays_both():
    """대조군 — 나포가 아니면 예전대로 양쪽이 함께 번다."""
    st = state()
    p1 = st.players[1]
    t = TradeShip(owner=0, src_port=st.gmap.ref(31, 5), dst_port=st.gmap.ref(30, 5),
                  dst_owner=1, path=[st.gmap.ref(31, 5), st.gmap.ref(30, 5)])
    st.trade_ships.append(t)
    g0, g1 = st.players[0].gold, p1.gold
    st.tick_count += 1
    st._advance_trade()
    assert st.players[0].gold > g0 and p1.gold > g1, "한쪽만 벌었다"


def test_a_warship_without_a_port_does_not_hunt():
    """`hasReachablePort` — 끌고 갈 항구가 없으면 아예 안 노린다.

    대조군은 위 `test_warship_captures_...` 다(항구가 있으면 잡는다)."""
    st = state()
    st.warships.append(Warship(owner=0, tile=st.gmap.ref(30, 5)))
    t = _trade_setup(st)
    where = t.tile
    for _ in range(20):
        st.tick_count += 1
        st._advance_warships()
    assert t.captured_by is None, "항구도 없는데 나포했다"
    # ⚠ `captured_by` 만 보면 안 된다. 노리기는 했는데 나포에 실패해 배가
    # **삭제되는** 경로가 있어서, 그 경우에도 `captured_by` 는 None 이다.
    assert t in st.trade_ships, "노렸다가 실패해 배를 지웠다"
    assert t.dst_owner == 2 and t.tile == where, "배가 손을 탔다"


def test_shoreline_makes_a_trade_ship_safe_from_pirates():
    """해안선 물 칸을 밟으면 20 tick 동안 못 건드린다(`isSafeFromPirates`).

    대조군: 21 tick 이 지나면 다시 잡힌다 — 그게 없으면 보호를 뜯어내도 결과가 같다."""
    st = state()
    port(st, 0, 0, 0)
    st.warships.append(Warship(owner=0, tile=st.gmap.ref(30, 5)))
    t = _trade_setup(st)
    # ⚠ `last_safe_tick` 을 직접 세팅하면 **엔진의 갱신 코드를 안 탄다** —
    # 갱신을 지우는 변이가 살아남는다. 해안선 칸을 실제로 밟게 해서 엔진이
    # 스스로 찍게 둔다.
    shore = next(x for x in (st.gmap.ref(i, 1) for i in range(1, 60))
                 if st.gmap.is_ocean(x) and st.gmap.is_shoreline(x))
    # 길이 5 — 한 번 나아가도 **도착하지 않게** 둔다. 도착하면 배가 항로에서
    # 빠져 이어지는 나포 시험이 통째로 무의미해진다.
    t.path = [shore] * 5
    st.tick_count += 1
    st._advance_trade()
    assert t.last_safe_tick == st.tick_count, "엔진이 해안선을 안 찍었다"
    t.path = [st.gmap.ref(x, 5) for x in range(29, 40)]
    t.step_i = 0
    for _ in range(C.SAFE_FROM_PIRATES_TICKS - 1):
        st.tick_count += 1
        st._advance_warships()
    assert t.captured_by is None, "막 해안을 밟은 배를 잡았다"
    # 대조군 — 보호가 풀리면 잡힌다. 없으면 보호를 뜯어내도 결과가 같다
    for _ in range(20):
        st.tick_count += 1
        st._advance_warships()
    assert t.captured_by == 0, "보호가 풀렸는데도 못 잡는다"


def test_pirates_leave_ships_bound_for_friends_alone():
    """목적지가 나거나 내 동맹이면 안 건드린다 — 어차피 내가 벌 배다."""
    st = state()
    port(st, 0, 0, 0)
    st.warships.append(Warship(owner=0, tile=st.gmap.ref(30, 5)))
    mine = _trade_setup(st, dst_owner=0)
    for _ in range(20):
        st.tick_count += 1
        st._advance_warships()
    assert mine.captured_by is None, "내 항구로 오는 배를 잡았다"


def test_pirates_do_not_chase_beyond_the_patrol_range():
    """`warshipPatrolRange`(100) — 사거리(130) 안이어도 순찰 구역 밖은 안 쫓는다.

    ⚠ 기본 지도(가로 62)로는 100 을 넘길 수 없어 이 규칙을 지워도 통과한다.
    넓은 지도를 따로 만든다. 대조군은 같은 배를 순찰 구역 안에 둔 경우다."""
    def run(trade_x: int) -> "int | None":
        gm = GameMap.from_rows(["." + "~" * 298 + "."] * 12)
        ps = {}
        for pid in (0, 1):
            t = gm.ref(0 if pid == 0 else 299, pid)
            ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
            gm.owner[t] = pid
        st = GameState(gmap=gm, players=ps, rng=random.Random(0))
        st._counts, st._posts = {0: 1, 1: 1}, DefensePostIndex(gm.size)
        st.fallout = Fallout(gm.size)
        port(st, 0, 0, 0)
        w = Warship(owner=0, tile=gm.ref(150, 5))
        w.patrol_origin = gm.ref(30, 5)          # 순찰 기점에서 멀리 흘러왔다
        st.warships.append(w)
        t = TradeShip(owner=1, src_port=gm.ref(280, 5), dst_port=gm.ref(1, 5),
                      dst_owner=1, path=[gm.ref(trade_x, 5)])
        st.trade_ships.append(t)
        # 추격은 tick 당 2칸이다 — 한 번 부르고 판정하면 대조군이 거짓으로 깨진다
        for _ in range(60):
            st.tick_count += 1
            st._advance_warships()
        return t.captured_by
    assert run(100) == 0, "순찰 반경(100) 안인데 안 잡았다 — 대조군이 깨졌다"
    assert run(160) is None, "순찰 반경 밖(기점에서 130)까지 쫓아갔다"


def test_hunt_moves_two_tiles_per_tick():
    """`huntDownTradeShip` 은 tick 당 루프를 **2번** 돈다.

    ⚠ 추격 tick 을 넉넉히 주면 1칸이든 2칸이든 결국 따라잡아 변이가 살아남는다.
    거리를 정확히 재고 **딱 맞는 tick 수**만 준다 — 1칸이면 절반밖에 못 간다."""
    st = state()
    port(st, 0, 0, 0)
    w = Warship(owner=0, tile=st.gmap.ref(10, 5))
    st.warships.append(w)
    t = TradeShip(owner=1, src_port=st.gmap.ref(59, 5), dst_port=st.gmap.ref(1, 5),
                  dst_owner=1, path=[st.gmap.ref(41, 5)])   # 거리 31
    st.trade_ships.append(t)
    ticks = (31 - C.PIRACY_CAPTURE_RANGE + 1) // C.PIRACY_HUNT_STEPS + 1
    for _ in range(ticks):
        st.tick_count += 1
        st._advance_warships()
    assert t.captured_by == 0, (
        f"{ticks} tick 에 못 잡았다 — tick 당 {C.PIRACY_HUNT_STEPS}칸이 아니다 "
        f"(전함 {w.tile % st.gmap.width}, 배 {t.tile % st.gmap.width})")


def test_capture_happens_at_range_five_not_on_contact():
    """맨해튼 **5** 안이면 잡는다 — 같은 칸까지 갈 필요가 없다.

    ⚠ 탐욕 이동은 결국 같은 칸까지 가므로, tick 을 넉넉히 주면 사거리를 0 으로
    바꿔도 통과한다. 전함을 정확히 5칸 옆에 두고 **한 tick** 만 준다."""
    st = state()
    port(st, 0, 0, 0)
    w = Warship(owner=0, tile=st.gmap.ref(30, 5))
    st.warships.append(w)
    t = TradeShip(owner=1, src_port=st.gmap.ref(59, 5), dst_port=st.gmap.ref(1, 5),
                  dst_owner=1, path=[st.gmap.ref(35, 5)])   # 정확히 5
    st.trade_ships.append(t)
    st.tick_count += 1
    st._advance_warships()
    assert t.captured_by == 0, "거리 5 에서 안 잡았다"
    assert w.tile == st.gmap.ref(30, 5), "잡으려고 움직였다 — 5 안이면 그 자리에서다"


def test_capture_fails_when_the_pirate_has_no_port():
    """`_capture_trade_ship` 자체의 계약 — 끌고 갈 항구가 없으면 **실패**한다.

    ⚠ 엔진 경로로는 이 분기에 못 닿는다(`hasReachablePort` 가 먼저 막는다) —
    그래서 나포 함수를 직접 부른다. 없으면 이 안전장치를 뜯어내도 스위트가
    전부 통과한다."""
    st = state()
    t = _trade_setup(st)
    before = (t.dst_port, t.dst_owner, t.path)
    assert st._capture_trade_ship(t, 0) is False, "항구도 없는데 나포에 성공했다"
    assert t.captured_by is None
    assert (t.dst_port, t.dst_owner, t.path) == before, "실패했는데 항로를 건드렸다"
    # 대조군 — 항구가 있으면 성공한다
    port(st, 0, 0, 0)
    assert st._capture_trade_ship(t, 0) is True
