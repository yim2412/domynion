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
from domynion.core.constants import Terrain
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


# --- 순찰 이동 (이식 누락 스물둘) --------------------------------------------
#
# 전에는 전함이 태어난 자리에 붙박여 있었다. patrol_origin 이 필드로만 있고
# 아무도 배를 옮기지 않았다. 붙박이면 순찰 반경(100)이 사거리(130)보다 작다는
# 규칙 자체가 아무 의미가 없다.

def test_a_warship_with_no_target_patrols():
    """표적이 없으면 순찰 지점을 잡고 그쪽으로 움직인다."""
    st = state()
    w = Warship(owner=0, tile=st.gmap.ref(30, 5))
    st.warships.append(w)
    start = w.tile
    moved = set()
    for _ in range(40):
        st.tick_count += 1
        st._advance_warships()
        moved.add(w.tile)
    assert w.tile != start, "전함이 한 칸도 안 움직였다"
    assert len(moved) > 3, f"제자리를 맴돈다 ({len(moved)}칸)"


def _ocean(w: int = 400, h: int = 240) -> GameMap:
    """넓은 바다. ⚠ 기본 지도(62×30)는 **순찰 반경(100)보다 작다** — 그 위에서는
    반경·기점·수로 규칙을 지워도 결과가 같아 변이가 전부 살아남는다(실제로 다섯
    개가 그렇게 살아남았다)."""
    return GameMap.from_rows(["." + "~" * (w - 2) + "."] * h)


def _sea_state(gm: GameMap) -> GameState:
    ps = {}
    for pid in (0, 1, 2):
        t = gm.ref(0, pid)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts, st._posts = {0: 1, 1: 1, 2: 1}, DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def test_patrol_stays_within_range_of_the_origin():
    """순찰 지점은 기점 반경의 **절반** 안에서 뽑는다(`warshipPatrolRange / 2`).

    ⚠ 대조군이 필요하다: "안 벗어났다"만 보면 배가 안 움직여도 참이다. 그리고
    지도가 반경보다 작으면 절반이든 전체든 결과가 같다 — 넓은 바다에서 잰다."""
    gm = _ocean()
    st = _sea_state(gm)
    origin = gm.ref(200, 120)
    w = Warship(owner=0, tile=origin)
    st.warships.append(w)
    half = C.WARSHIP_PATROL_RANGE // 2
    seen = set()
    for _ in range(600):                 # 반경 끝까지 갈 시간을 준다
        st.tick_count += 1
        st._advance_warships()
        seen.add(w.tile)
        dx = abs(w.tile % gm.width - origin % gm.width)
        dy = abs(w.tile // gm.width - origin // gm.width)
        assert dx <= half and dy <= half, f"기점에서 {dx},{dy} 나갔다 (한계 {half})"
    assert len(seen) > 50, f"거의 안 움직였다 ({len(seen)}칸) — 대조군이 깨졌다"


def test_patrol_recenters_on_the_origin_not_the_current_tile():
    """중심은 **순찰 기점**이지 지금 위치가 아니다.

    ⚠ 현재 위치를 중심으로 두면 배가 무작위 보행으로 **표류한다** — 순찰 구역이라는
    개념 자체가 사라진다. 600 tick 을 돌려 기점 근처에 머무는지 본다."""
    gm = _ocean()
    st = _sea_state(gm)
    origin = gm.ref(200, 120)
    w = Warship(owner=0, tile=origin)
    st.warships.append(w)
    far = 0
    for _ in range(600):
        st.tick_count += 1
        st._advance_warships()
        dx = abs(w.tile % gm.width - origin % gm.width)
        dy = abs(w.tile // gm.width - origin // gm.width)
        far = max(far, dx + dy)
    assert far <= C.WARSHIP_PATROL_RANGE, f"기점에서 {far} 까지 표류했다"
    assert far > 10, f"안 움직였다({far}) — 대조군이 깨졌다"


def test_patrol_target_is_cleared_on_arrival():
    """목표에 닿으면 비운다. 안 비우면 그 자리에 굳는다.

    ⚠ 위치만 보면 안 잡힌다 — 굳어도 "범위 안"이고 "바다 위"다.
    **닿은 뒤에도 계속 움직이는지**를 봐야 한다."""
    gm = _ocean()
    st = _sea_state(gm)
    w = Warship(owner=0, tile=gm.ref(200, 120))
    st.warships.append(w)
    for _ in range(400):                 # 목표를 여러 번 갈아탈 만큼
        st.tick_count += 1
        st._advance_warships()
    late = set()
    for _ in range(200):
        st.tick_count += 1
        st._advance_warships()
        late.add(w.tile)
    assert len(late) > 20, f"400 tick 뒤에 굳었다 ({len(late)}칸만 밟았다)"


def test_patrol_never_picks_a_tile_across_land():
    """수로가 안 이어진 칸은 순찰 지점이 되지 않는다(`hasWaterComponent`).

    ⚠ 이어지지 않은 바다가 있는 지도라야 이 규칙이 재진다 — 가운데를 육지로 막는다."""
    w_, h_ = 400, 240
    rows = []
    for y in range(h_):
        row = ["."] + ["~"] * (w_ - 2) + ["."]
        row[w_ // 2] = "#"               # 통행불가 벽이 바다를 둘로 가른다
        rows.append("".join(row))
    gm = GameMap.from_rows(rows)
    st = _sea_state(gm)
    # ⚠ 벽에서 멀면 반경(절반 50) 안에 벽 너머가 안 들어와 규칙이 안 재진다.
    # 벽 바로 옆에 둬야 후보에 건너편이 실제로 뽑힌다(재료 문제로 한 번 놓쳤다).
    ship = Warship(owner=0, tile=gm.ref(190, 120))
    st.warships.append(ship)
    left = gm.width // 2
    for _ in range(600):
        st.tick_count += 1
        st._advance_warships()
        assert ship.tile % gm.width < left, "벽 너머로 넘어갔다"
        if ship.patrol_target is not None:
            assert ship.patrol_target % gm.width < left,                 "이어지지 않은 바다를 순찰 지점으로 골랐다"


def test_a_warship_shooting_still_patrols():
    """원본은 **쏘고 나서도 순찰한다**(`shootTarget(); patrol();`).

    ⚠ 포격 분기에서 순찰을 빼면 교전 중인 배가 굳는다. 무역선 추격만 예외다
    (그쪽은 이미 목표를 향해 움직인다)."""
    st = state()
    mine = Warship(owner=0, tile=st.gmap.ref(30, 5))
    foe = Warship(owner=1, tile=st.gmap.ref(32, 5))
    st.warships += [mine, foe]
    start = mine.tile
    for _ in range(30):
        st.tick_count += 1
        st._advance_warships()
        if foe.sunk:
            break
    assert mine.tile != start, "적을 쏘는 동안 제자리에 굳었다"


def test_hunting_a_trade_ship_skips_patrol():
    """추격 중에는 순찰하지 않는다 — 목표 쪽으로만 간다."""
    st = state()
    port(st, 0, 0, 0)
    w = Warship(owner=0, tile=st.gmap.ref(30, 5))
    st.warships.append(w)
    t = TradeShip(owner=1, src_port=st.gmap.ref(59, 5), dst_port=st.gmap.ref(1, 5),
                  dst_owner=2, path=[st.gmap.ref(45, 5)])
    st.trade_ships.append(t)
    st.tick_count += 1
    st._advance_warships()
    # 정확히 2칸, 목표 쪽으로만 (순찰이 끼면 방향이 흐트러진다)
    assert w.tile == st.gmap.ref(32, 5), f"추격이 순찰에 흔들렸다 ({w.tile % st.gmap.width})"
    assert w.patrol_target is None, "추격 중에 순찰 목표를 잡았다"


def test_patrol_never_picks_land_or_shoreline():
    """순찰 지점은 바다여야 하고, 해안선은 피한다(원본 `allowShoreline=false`).

    ⚠ 좁은 지도에서는 이 규칙을 지워도 안 잡힌다 — 탐욕 이동이 육지 이웃을
    애초에 안 고르므로 **목표가 육지여도 배는 바다에 남는다.** 그러면
    "육지에 안 올라갔다"가 늘 참이다. 목표 자체를 함께 봐야 한다."""
    w_, h_ = 400, 240
    rows = []
    for y in range(h_):
        row = ["."] + ["~"] * (w_ - 2) + ["."]
        if 100 <= y < 140:                    # 가운데 섬 — 육지 후보가 실제로 뽑힌다
            for x in range(180, 220):
                row[x] = "."
        rows.append("".join(row))
    gm = GameMap.from_rows(rows)
    st = _sea_state(gm)
    ship = Warship(owner=0, tile=gm.ref(160, 120))
    st.warships.append(ship)
    for _ in range(600):
        st.tick_count += 1
        st._advance_warships()
        assert gm.terrain[ship.tile] == Terrain.OCEAN, "육지로 올라갔다"
        if ship.patrol_target is not None:
            assert gm.terrain[ship.patrol_target] == Terrain.OCEAN, \
                "육지를 순찰 지점으로 골랐다"
            assert not gm.is_shoreline(ship.patrol_target), \
                "해안선을 순찰 지점으로 골랐다"


def test_patrol_tile_is_never_land_even_in_the_shoreline_fallback():
    """`randomTile()` 계약 — **해안선을 허용하는 폴백에서도 육지는 안 고른다.**

    ⚠ 보통 경로로는 이 규칙이 안 재진다. 안쪽 육지는 수로 검사가 먼저 걷어내고
    (바다 이웃이 없어 성분이 비어 있다), 물가 육지는 해안선 검사가 걷어낸다 —
    **다른 두 검사가 이 검사를 가려 준다.** 해안선을 허용하는 폴백에서는 물가
    육지가 성분 검사를 통과하므로, 그때 지형 검사만이 유일한 관문이다.

    작은 못은 칸이 전부 해안선이라 1차 통과가 반드시 실패하고 폴백으로 간다."""
    w_, h_ = 40, 24
    rows = []
    for y in range(h_):
        row = ["."] * w_
        if 10 <= y < 12:
            for x in range(18, 20):
                row[x] = "~"             # 2×2 못 — 모든 칸이 물가라 전부 해안선이다
                                         # (3×3 이상이면 가운데가 해안선이 아니다)
        rows.append("".join(row))
    gm = GameMap.from_rows(rows)
    st = _sea_state(gm)
    pond = [gm.ref(x, y) for y in range(10, 12) for x in range(18, 20)]
    assert all(gm.is_shoreline(t) for t in pond), "못이 전부 해안선이어야 폴백을 탄다"
    ship = Warship(owner=0, tile=pond[0])
    st.warships.append(ship)
    for _ in range(40):
        t = st._random_patrol_tile(ship)
        assert t is not None, "폴백에서도 못 찾았다"
        assert gm.terrain[t] == Terrain.OCEAN, "폴백에서 육지를 골랐다"
