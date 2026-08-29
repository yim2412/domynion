"""동맹 요청 쿨다운 · SAM 업그레이드 사거리 — 이식 누락 여든넷·여든다섯 (§5.82).

찾은 방법이 새롭다: **원본 `Config.ts` 의 메서드 105개를 뽑아 우리 코드·문서에
한 번도 안 나오는 이름을 셌다.** 50개가 나왔는데 대부분은 우리가 한국어·snake
이름으로 옮겨 둔 것이었고(`warshipTargettingRange` → `WARSHIP_TARGETTING_RANGE`),
**진짜 없는 것 셋**이 그 안에 섞여 있었다.

| # | 원본 | 우리 |
|---|---|---|
| **여든넷** | 같은 상대에게 **30초 요청 쿨다운**(`allianceRequestCooldown`) | 없었다 — 거절당한 그 tick 에 다시 걸 수 있었다 |
| **여든다섯** | SAM 업그레이드는 사거리가 **서서히** 는다(`dynamicSamRange`) | 올린 그 tick 부터 새 사거리 |
| (발견 기록) | 철로 경로가 155칸을 넘으면 **안 깔린다**(`railroadMaxSize`) | 상수만 있고 읽는 곳이 0 |

쿨다운은 만료(20초, §5.73)보다 **길다.** 그래서 요청이 만료된 뒤에도 10초를 더
기다려야 한다 — 없으면 AI 가 매 판단마다 같은 상대에게 다시 걸고, §5.68 의
✉ 깃발이 꺼졌다 켜졌다를 반복한다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.diplomacy import Diplomacy
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout, dynamic_sam_range, sam_range
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(size: int = 80) -> GameState:
    gm = GameMap.from_rows(["." * size] * size)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid * 20 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


# --- 여든넷 · 요청 쿨다운 ----------------------------------------------------

def test_the_cooldown_is_longer_than_the_expiry():
    """⚠ **이 순서가 규칙이다.** 만료 20초 · 쿨다운 30초 — 요청이 저절로
    사라진 뒤에도 10초를 더 기다린다."""
    assert C.ALLIANCE_REQUEST_COOLDOWN_TICKS > C.ALLIANCE_REQUEST_DURATION_TICKS


def test_a_second_request_is_refused_inside_the_window():
    """막지 않았으면: 거절당한 그 tick 에 다시 건다. AI 는 판단할 때마다 걸고,
    받는 쪽 화면의 ✉ 깃발이 꺼졌다 켜졌다를 반복한다."""
    d = Diplomacy()
    assert d.request(0, 1, tick=0)
    d.reject(1, 0)
    assert not d.request(0, 1, tick=C.ALLIANCE_REQUEST_COOLDOWN_TICKS - 1)
    assert d.request(0, 1, tick=C.ALLIANCE_REQUEST_COOLDOWN_TICKS)


def test_an_expired_request_still_waits_out_the_cooldown():
    """만료(20초) 뒤 10초가 더 남는다 — 두 값이 다른 이유다."""
    d = Diplomacy()
    d.request(0, 1, tick=0)
    gone = d.expire_requests(C.ALLIANCE_REQUEST_DURATION_TICKS)
    assert gone == [(0, 1)], "재료: 만료됐어야 한다"
    assert not d.request(0, 1, tick=C.ALLIANCE_REQUEST_DURATION_TICKS), \
        "만료되자마자 다시 걸었다"


def test_the_cooldown_is_per_recipient():
    """상대마다 따로 잰다 — 한 사람에게 걸었다고 판 전체가 막히면 안 된다."""
    d = Diplomacy()
    d.request(0, 1, tick=0)
    assert d.request(0, 2, tick=0), "다른 상대에게도 막혔다"


def test_the_cooldown_does_not_block_the_other_direction():
    """내가 건 것이 **상대가 나에게** 거는 것을 막지는 않는다."""
    d = Diplomacy()
    d.request(0, 1, tick=0)
    assert d.can_request(1, 0, tick=0)


def test_a_dead_player_is_forgotten():
    d = Diplomacy()
    d.request(0, 1, tick=0)
    d.drop_player(1)
    assert d.can_request(0, 1, tick=0), "죽은 상대의 쿨다운이 남았다"


def test_the_engine_path_records_the_cooldown():
    """로직이 아니라 **배선**을 잰다."""
    st = state()
    st.tick_count = 1000
    assert st.request_alliance(0, 1) is True
    st.reject_alliance(1, 0)
    assert st.request_alliance(0, 1) is False, "엔진 경로가 쿨다운을 안 탄다"


# --- 여든다섯 · SAM 업그레이드 사거리 ----------------------------------------

def sam(st: GameState, pid: int, x: int, y: int, level: int = 1) -> Unit:
    u = Unit(UnitType.SAM_LAUNCHER, pid, tile=st.gmap.ref(x, y), level=level)
    st.players[pid].units.units.append(u)
    return u


def test_a_fresh_sam_uses_its_plain_range():
    u = Unit(UnitType.SAM_LAUNCHER, 0, tile=0, level=2)
    assert dynamic_sam_range(u, 0) == sam_range(2)


def test_the_range_grows_over_the_upgrade_duration():
    """⚠ 막지 않았으면 **업그레이드가 즉발 방공망 확장**이 된다 — 날아오는 핵을
    보고 올려서 그 자리에서 막을 수 있다."""
    u = Unit(UnitType.SAM_LAUNCHER, 0, tile=0, level=2)
    u.upgrade_from = sam_range(1)
    u.upgrade_started = 100
    half = C.SAM_UPGRADE_DURATION_TICKS // 2
    mid = dynamic_sam_range(u, 100 + half)
    assert sam_range(1) < mid < sam_range(2), f"중간값이 아니다 ({mid})"
    assert mid == pytest.approx(
        sam_range(1) + (sam_range(2) - sam_range(1)) * half
        / C.SAM_UPGRADE_DURATION_TICKS)


def test_the_range_settles_at_the_new_level():
    u = Unit(UnitType.SAM_LAUNCHER, 0, tile=0, level=2)
    u.upgrade_from = sam_range(1)
    u.upgrade_started = 0
    assert dynamic_sam_range(u, C.SAM_UPGRADE_DURATION_TICKS) == sam_range(2)
    assert dynamic_sam_range(u, C.SAM_UPGRADE_DURATION_TICKS * 3) == sam_range(2)


def test_the_duration_is_half_the_cooldown():
    assert C.SAM_UPGRADE_DURATION_TICKS == C.SAM_COOLDOWN_TICKS // 2


def test_upgrading_records_the_start_and_the_old_range():
    """배선 — 엔진이 올릴 때 두 값을 적어야 한다."""
    st = state()
    st.tick_count = 500
    u = sam(st, 0, 5, 5)
    st.players[0].gold = 10_000_000_000
    assert st.upgrade(0, u) == 1
    assert u.upgrade_started == 500
    assert u.upgrade_from == pytest.approx(sam_range(1))


def test_two_upgrades_in_a_row_continue_from_the_middle():
    """⚠ 연달아 올리면 **그 순간의 사거리**에서 이어진다 — 옛 레벨 값으로
    되돌아가면 두 번째 업그레이드가 사거리를 잠깐 줄인다."""
    st = state()
    st.tick_count = 500
    u = sam(st, 0, 5, 5)
    st.players[0].gold = 10_000_000_000
    st.upgrade(0, u)
    st.tick_count += C.SAM_UPGRADE_DURATION_TICKS // 2
    mid = dynamic_sam_range(u, st.tick_count)
    st.upgrade(0, u)
    assert u.upgrade_from == pytest.approx(mid)
    assert dynamic_sam_range(u, st.tick_count) == pytest.approx(mid), \
        "두 번째 업그레이드가 사거리를 되돌렸다"


def test_interception_uses_the_growing_range():
    """배선 — 요격 판정이 `dynamic_sam_range` 를 써야 한다.

    막지 않았으면: 올린 그 tick 부터 넓어진 사거리로 막는다."""
    # ⚠ 지도가 **사거리보다 넓어야 한다.** 80칸 지도에서 94칸 떨어진 칸을
    # 만들면 `ref` 가 감겨서 바로 옆 칸이 된다 — 그러면 아무것도 안 잰다.
    st = state(size=160)
    st.tick_count = 500
    u = sam(st, 0, 5, 5)
    st.players[0].gold = 10_000_000_000
    st.upgrade(0, u)                      # Lv2 — 다 오르면 사거리가 넓어진다
    st.upgrade(0, u)
    st.upgrade(0, u)                      # Lv4
    u.missile_queue.clear()               # 재장전은 이 시험의 관심사가 아니다
    assert dynamic_sam_range(u, st.tick_count) < sam_range(u.level), \
        "재료: 아직 다 안 올랐어야 한다"
    far = sam_range(1) + (sam_range(4) - sam_range(1)) * 0.9
    tile = st.gmap.ref(5 + int(far), 5)
    from domynion.core.nukes import Nuke
    n = Nuke(owner=1, utype=UnitType.ATOM_BOMB, src=tile, dst=tile)
    assert st._sam_intercepts(n) is False, "아직 안 넓어진 사거리로 막았다"
