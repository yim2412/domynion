"""`ai/mirv.py` — 원본 `NationMIRVBehavior` 대조.

**이 파일이 생긴 이유는 우리 AI 가 MIRV 를 한 발도 안 쐈다는 것이다.** 값을 위해
저축하는 코드(`getSaveUpTarget`)는 있었는데 **사는 코드가 통째로 없었다.**
"""

from __future__ import annotations

import random

from domynion.ai.mirv import (MIRV_COOLDOWN_TICKS, MIRV_HESITATION_ODDS,
                              MIRV_STEAMROLL_GAP, MIRV_STEAMROLL_MIN_CITIES,
                              MIRV_VICTORY_DENIAL_SHARE, NationMIRVBehavior,
                              territory_center)
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout, Nuke
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(players: int = 3, w: int = 400, h: int = 200) -> GameState:
    gm = GameMap.from_rows(["." * w] * h)
    ps = {}
    for pid in range(players):
        t = gm.ref(pid, 0)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in range(players)}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = MIRV_COOLDOWN_TICKS * 4      # 쿨다운을 이미 지난 시각에서 시작
    return st


def only(st, pid, x0, y0, x1, y1):
    """`fill` 에 더해 **시작 타일을 지운다.**

    ⚠ `state()` 가 pid 마다 `(pid, 0)` 에 한 칸을 심는다. 경계 상자를 재는
    테스트에서 그 한 칸이 상자를 지도 왼쪽 위까지 늘려 중심이 엉뚱한 곳으로
    간다 — 처음에 그대로 두고 "코드가 틀렸다"고 볼 뻔했다."""
    st.gmap.owner[st.gmap.ref(pid, 0)] = -1
    st._counts[pid] = st._counts.get(pid, 0) - 1
    fill(st, pid, x0, y0, x1, y1)


def fill(st, pid, x0, y0, x1, y1):
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            st.gmap.owner[st.gmap.ref(x, y)] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n


def unit(st, pid, utype, x, y, level=1):
    u = Unit(utype, pid, tile=st.gmap.ref(x, y), level=level)
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(utype)
    return u


def behavior(pid=0, difficulty="impossible", seed=0):
    NationMIRVBehavior.recent_targets.clear()     # 클래스 공유 상태를 격리한다
    return NationMIRVBehavior(pid, random.Random(seed), difficulty)


def ready(st, pid=0, level=5):
    """골드와 사일로를 넉넉히 준다."""
    unit(st, pid, UnitType.MISSILE_SILO, 5, 5, level=level)
    st.players[pid].gold = 10_000_000_000


# --- 관문 -------------------------------------------------------------------

def test_no_mirv_without_a_silo_or_gold():
    st = state()
    fill(st, 1, 0, 0, 380, 190)                  # 1번이 지도의 90% 를 가졌다
    b = behavior()
    assert b.consider(st) is False, "사일로도 골드도 없는데 쐈다"

    unit(st, 0, UnitType.MISSILE_SILO, 5, 195, level=5)
    st.players[0].gold = 1
    assert b.consider(st) is False, "골드가 없는데 쐈다"


def test_hesitation_is_rolled_after_the_cost_check():
    """망설임 확률은 **값을 치를 수 있을 때만** 굴린다.

    앞에 두면 못 사는 tick 마다 주사위를 버려 확률의 뜻이 달라진다."""
    assert MIRV_HESITATION_ODDS["easy"] < MIRV_HESITATION_ODDS["impossible"]
    st = state()
    fill(st, 1, 0, 0, 380, 190)
    ready(st)
    fired = sum(behavior(difficulty="easy", seed=s).consider(st)
                for s in range(40))
    # easy 는 1/2 로 망설인다 — 40번이 전부 같은 답이면 확률이 안 걸린 것이다
    assert 0 < fired < 40, fired


# --- 승리 저지 ---------------------------------------------------------------

def test_victory_denial_fires_at_the_runaway_leader():
    st = state()
    fill(st, 1, 0, 0, 380, 190)                  # 90%
    ready(st)
    b = behavior(difficulty="impossible", seed=1)
    assert b._victory_denial_target(st).pid == 1
    assert b.consider(st) is True
    assert len(st.nukes) == 1 and st.nukes[0].utype is UnitType.MIRV


def test_the_threshold_moves_with_difficulty():
    """impossible 은 40% 에서 이미 반응하고 easy 는 75% 까지 기다린다."""
    assert (MIRV_VICTORY_DENIAL_SHARE["impossible"]
            < MIRV_VICTORY_DENIAL_SHARE["hard"]
            < MIRV_VICTORY_DENIAL_SHARE["medium"]
            < MIRV_VICTORY_DENIAL_SHARE["easy"])
    st = state()
    land = st.gmap.land_count
    fill(st, 1, 0, 0, 400, 90)                   # 45% — impossible 만 반응한다
    share = st.tiles(1) / land
    assert 0.4 <= share < 0.55, share
    assert behavior(difficulty="impossible")._victory_denial_target(st).pid == 1
    for diff in ("easy", "medium", "hard"):
        assert behavior(difficulty=diff)._victory_denial_target(st) is None, diff


def test_bots_are_never_mirved():
    """부족(봇)은 안 친다 — 핵과 같은 규칙이다."""
    st = state()
    st.players[1].kind = "bot"
    st.players[1].is_bot = True
    fill(st, 1, 0, 0, 380, 190)
    ready(st)
    b = behavior()
    assert b._victory_denial_target(st) is None
    assert b.consider(st) is False


# --- 반격 --------------------------------------------------------------------

def test_an_inbound_mirv_is_answered_first():
    """나를 겨눈 MIRV 가 있으면 그것이 최우선이다 — 승리 저지보다 앞선다."""
    st = state()
    fill(st, 0, 0, 100, 100, 200)
    fill(st, 1, 200, 0, 380, 190)                # 1번이 크다(승리 저지 후보)
    fill(st, 2, 0, 0, 40, 40)
    ready(st)
    b = behavior(difficulty="impossible", seed=1)
    assert b._victory_denial_target(st).pid == 1, "재료가 승리 저지를 안 만든다"

    # 2번이 내 땅을 겨눈 MIRV 를 띄웠다
    st.nukes.append(Nuke(owner=2, utype=UnitType.MIRV,
                         src=st.gmap.ref(10, 10), dst=st.gmap.ref(50, 150)))
    assert b._counter_target(st).pid == 2
    assert b.consider(st) is True
    assert st.nukes[-1].owner == 0
    assert int(st.gmap.owner[st.nukes[-1].dst]) == 2, "2번 땅을 안 겨눴다"


def test_a_mirv_aimed_elsewhere_is_not_my_business():
    """대조군 — 남을 겨눈 MIRV 에는 반격하지 않는다."""
    st = state()
    fill(st, 0, 0, 100, 100, 200)
    fill(st, 2, 300, 0, 380, 100)
    st.nukes.append(Nuke(owner=2, utype=UnitType.MIRV,
                         src=st.gmap.ref(310, 10), dst=st.gmap.ref(350, 50)))
    assert behavior()._counter_target(st) is None


# --- 폭주 저지 ---------------------------------------------------------------

def test_steamroll_needs_both_a_gap_and_a_floor():
    """도시가 2등의 배수를 넘어야 하고, **최소 개수**도 넘어야 한다."""
    st = state()
    fill(st, 1, 0, 0, 100, 100)
    fill(st, 2, 200, 0, 300, 100)
    b = behavior(difficulty="impossible")        # 최소 8, 배수 1.15
    unit(st, 1, UnitType.CITY, 10, 10, level=6)  # 6 — 최소 미달
    unit(st, 2, UnitType.CITY, 210, 10, level=1)
    assert b._steamroll_target(st) is None, "최소 개수를 안 봤다"

    st.players[1].units.units[0].level = 9       # 9 > 8 이고 9 >= 1×1.15
    assert b._steamroll_target(st).pid == 1

    st.players[2].units.units[0].level = 9       # 격차가 사라진다
    assert b._steamroll_target(st) is None, "격차를 안 봤다"


def test_steamroll_thresholds_move_with_difficulty():
    assert MIRV_STEAMROLL_GAP["impossible"] < MIRV_STEAMROLL_GAP["easy"]
    assert (MIRV_STEAMROLL_MIN_CITIES["impossible"]
            < MIRV_STEAMROLL_MIN_CITIES["easy"])
    st = state()
    fill(st, 1, 0, 0, 100, 100)
    fill(st, 2, 200, 0, 300, 100)
    unit(st, 1, UnitType.CITY, 10, 10, level=12)
    unit(st, 2, UnitType.CITY, 210, 10, level=9)   # 12 / 9 = 1.33
    assert behavior(difficulty="impossible")._steamroll_target(st).pid == 1
    assert behavior(difficulty="hard")._steamroll_target(st).pid == 1   # 1.25
    assert behavior(difficulty="medium")._steamroll_target(st) is None  # 1.5
    assert behavior(difficulty="easy")._steamroll_target(st) is None    # 2.0


# --- 쿨다운 ------------------------------------------------------------------

def test_nations_do_not_pile_onto_the_same_target():
    """MIRV 를 맞은 상대는 30초 동안 다시 겨누지 않는다 — **나라끼리 공유한다.**

    막지 않았으면: 골드가 많은 판에서 여러 나라가 같은 tick 에 같은 상대를 덮는다."""
    st = state()
    fill(st, 1, 0, 0, 380, 190)
    ready(st, 0)
    ready(st, 2)
    NationMIRVBehavior.recent_targets.clear()
    a = NationMIRVBehavior(0, random.Random(1), "impossible")
    b = NationMIRVBehavior(2, random.Random(1), "impossible")
    assert a.consider(st) is True
    assert b.consider(st) is False, "두 번째 나라가 같은 상대를 또 덮었다"

    st.tick_count += MIRV_COOLDOWN_TICKS         # 쿨다운이 지나면 다시 열린다
    assert b.consider(st) is True


# --- 표적 지점 ---------------------------------------------------------------

def test_the_mirv_hits_the_territory_centre():
    """무작위 칸이 아니라 **경계 상자의 중심**을 친다."""
    st = state()
    only(st, 1, 100, 40, 200, 140)                # 100~199 × 40~139
    # ⚠ 중심은 `floor((min+max)/2)` 라 (149, 89) 다. (150, 90) 을 기대했다가
    # 틀렸다 — `fill` 의 끝이 배타적이라는 것을 두 번 세지 않았다.
    assert territory_center(st, 1) == st.gmap.ref(149, 89)


def test_a_concave_territory_falls_back_to_the_nearest_owned_tile():
    """중심이 내 땅이 아니면 **가장 가까운 내 타일**로 물러선다.

    ⚠ 이게 없으면 도넛 모양 영토에 쏜 MIRV 가 남의 땅 한가운데서 갈라진다."""
    st = state()
    only(st, 1, 100, 40, 200, 60)                # 위쪽 띠
    fill(st, 1, 100, 120, 200, 140)              # 아래쪽 띠 — 중심은 빈 땅
    centre = territory_center(st, 1)
    assert int(st.gmap.owner[centre]) == 1, "남의 땅/빈 땅을 겨눴다"
    assert centre != st.gmap.ref(149, 89), "중심이 빈 땅인데 그대로 겨눴다"
