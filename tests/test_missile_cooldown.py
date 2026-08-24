"""사일로·SAM 재장전 — `missileTimerQueue` · `SiloCooldown` · `SAMCooldown`.

**둘 다 우리에게 통째로 없던 규칙이다.**

- 사일로 한 기로 골드가 되는 한 **무한 연사**가 됐다.
- SAM 한 기가 사거리 안의 핵을 **영원히 100%** 막았다.

원본 규칙:

1. **발사관 수 = 레벨.** Lv3 사일로는 관이 셋이다(`readyMissileCount` =
   `level - missileTimerQueue.length`).
2. 쏘면 그 관이 큐에 들어가고 **90 tick** 뒤에 열린다(`SiloCooldown` /
   `SAMCooldown`, 둘 다 90).
3. 관이 전부 차면 그 기체는 쿨다운이다(`isInCooldown` = `length === level`).
4. **레벨을 올리면 새 관도 재장전부터 시작한다**(`increaseLevel` 이 큐에 push).

⚠ 이 파일의 테스트는 일부러 깨뜨려서 실패하는지 확인했다(2026-08-24).
변이 목록은 파일 끝 주석에 있다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState
from domynion.core.units import UnitType


def state() -> GameState:
    """0번과 1번이 지도를 반씩 갖는다. 서로 사거리 밖이 되도록 넓게 둔다."""
    gm = GameMap.from_rows(["." * 400] * 40)
    players = {}
    for pid in (0, 1):
        for x in range(pid * 200, pid * 200 + 200):
            for y in range(40):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 200, 0),
                        kind="nation")
        p.gold = 10 ** 12
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 8000, 1: 8000}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def a_silo(st: GameState, pid: int = 0, x: int = 10, y: int = 10, level: int = 1):
    u = st.build(pid, UnitType.MISSILE_SILO, st.gmap.ref(x, y))
    assert u is not None
    while u.under_construction:
        st.tick()
    for _ in range(level - 1):
        assert st.upgrade(pid, u) == 1
    # 업그레이드가 새 관을 재장전에 넣으므로 비워 두고 시작한다
    u.missile_queue.clear()
    return u


def a_sam(st: GameState, pid: int, x: int, y: int, level: int = 1):
    u = st.build(pid, UnitType.SAM_LAUNCHER, st.gmap.ref(x, y))
    assert u is not None
    while u.under_construction:
        st.tick()
    for _ in range(level - 1):
        assert st.upgrade(pid, u) == 1
    u.missile_queue.clear()
    return u


def enemy_tile(st: GameState) -> int:
    return st.gmap.ref(250, 20)


# --- 상수 -------------------------------------------------------------------

def test_the_cooldowns_match_the_original():
    """`SiloCooldown()` = `SAMCooldown()` = 90. 값을 못 박는다 —
    이 프로젝트는 "이름만 옮기고 값을 안 본" 탓에 이미 30배 틀린 적이 있다."""
    assert C.SILO_COOLDOWN_TICKS == 90
    assert C.SAM_COOLDOWN_TICKS == 90


# --- 사일로 -----------------------------------------------------------------

def test_a_silo_cannot_fire_twice_in_a_row():
    """**막지 않았으면 무엇이 일어났을 것인가**: 골드가 되는 한 무한 연사.

    Lv1 사일로는 관이 하나다. 한 발 쏘면 90 tick 동안 못 쏜다."""
    st = state()
    a_silo(st)
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is not None
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is None, \
        "같은 tick 에 두 발이 나갔다"


def test_the_silo_reopens_after_exactly_the_cooldown():
    """89 tick 뒤에는 아직 안 되고, 90 tick 뒤에는 된다."""
    st = state()
    a_silo(st)
    st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st))
    for _ in range(C.SILO_COOLDOWN_TICKS - 1):
        st.tick()
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is None, \
        "쿨다운이 한 tick 일찍 풀렸다"
    st.tick()
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is not None


def test_tubes_equal_level():
    """Lv3 사일로는 **연달아 세 발**이 나간다. 그다음이 막힌다.

    관 수를 레벨이 아니라 1로 고정하면 여기서 걸린다 — 레벨을 올리는 이유가
    사라지므로 조용히 밸런스가 바뀐다."""
    st = state()
    silo = a_silo(st, level=3)
    assert silo.level == 3
    assert silo.ready_tubes == 3
    for i in range(3):
        assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is not None, i
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is None
    assert silo.in_cooldown


def test_a_silo_only_reloads_one_tube_per_tick():
    """⚠ **사일로는 tick 당 관 하나만 연다**(원본 `MissileSiloExecution` 이 `if`).

    Lv3 사일로가 세 발을 같은 tick 에 쐈으면, 90 tick 뒤에 관 하나만 열린다.
    `while` 로 옮기면 셋이 한꺼번에 열려 연사 간격이 사라진다."""
    st = state()
    silo = a_silo(st, level=3)
    for _ in range(3):
        st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st))
    assert len(silo.missile_queue) == 3
    for _ in range(C.SILO_COOLDOWN_TICKS):
        st.tick()
    assert len(silo.missile_queue) == 2, "한 tick 에 관이 여러 개 열렸다"
    st.tick()
    assert len(silo.missile_queue) == 1
    st.tick()
    assert len(silo.missile_queue) == 0


def test_a_second_silo_gives_another_shot():
    """사일로가 둘이면 두 발이 나간다 — 쿨다운은 **기체별**이다."""
    st = state()
    a_silo(st, x=10, y=10)
    a_silo(st, x=60, y=10)
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is not None
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is not None
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is None


def test_a_silo_under_construction_cannot_fire():
    st = state()
    u = st.build(0, UnitType.MISSILE_SILO, st.gmap.ref(10, 10))
    assert u.under_construction
    assert u.ready_tubes == 0
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is None


def test_no_gold_is_spent_when_every_silo_is_reloading():
    """막힌 발사에서 골드가 빠지면 안 된다 — 검사가 결제보다 앞이어야 한다."""
    st = state()
    a_silo(st)
    st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st))
    before = st.players[0].gold
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is None
    assert st.players[0].gold == before


# --- 업그레이드가 관을 여는 것이 아니다 ---------------------------------------

def test_upgrading_does_not_hand_you_an_instant_shot():
    """**레벨을 올려도 새 관은 재장전부터 시작한다**(`increaseLevel` 이 push).

    막지 않았으면: 쿨다운에 걸릴 때마다 업그레이드를 눌러 즉발로 한 발 더 쏜다."""
    st = state()
    silo = a_silo(st, level=1)
    st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st))
    assert silo.in_cooldown
    assert st.upgrade(0, silo) == 1
    assert silo.level == 2
    assert silo.in_cooldown, "업그레이드가 즉발 한 발을 줬다"
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st)) is None


def test_a_fresh_silo_upgrade_also_starts_reloading():
    """한 발도 안 쏜 사일로를 올려도 새 관은 재장전부터다 — 대조군.

    이게 없으면 위 테스트는 "쏜 뒤에만 push 한다"는 구현도 통과시킨다."""
    st = state()
    u = st.build(0, UnitType.MISSILE_SILO, st.gmap.ref(10, 10))
    while u.under_construction:
        st.tick()
    assert u.missile_queue == []
    st.upgrade(0, u)
    assert len(u.missile_queue) == 1
    assert u.ready_tubes == 1, "Lv2 인데 쏠 수 있는 관이 1개가 아니다"


def test_only_silos_and_sams_use_tubes():
    """도시를 올릴 때 관 큐를 건드리면 안 된다."""
    st = state()
    u = st.build(0, UnitType.CITY, st.gmap.ref(10, 10))
    while u.under_construction:
        st.tick()
    st.upgrade(0, u)
    assert u.missile_queue == []


# --- SAM --------------------------------------------------------------------

def _sam_hits(st: GameState) -> int:
    from domynion.core.events import EventKind
    return sum(1 for e in st.log.items if e.kind is EventKind.SAM_HIT)


def test_a_sam_cannot_intercept_forever():
    """**막지 않았으면 무엇이 일어났을 것인가**: SAM 한 기가 모든 핵을 영원히 막는다.

    Lv1 SAM 은 관이 하나다. 첫 발은 요격하지만, 재장전이 끝나기 전에 온 **두 번째
    발은 통과해야 한다.**

    ⚠ **한 발만 쏘면 이 테스트는 아무것도 안 잰다.** 처음에 그렇게 짰다가
    "쿨다운 검사를 지운" 변이가 살아남았다 — 한 발만 보면 검사가 있으나 없으나
    결과가 같다. 이 프로젝트에서 재료가 문제였던 네 번째다."""
    st = state()
    a_silo(st, pid=1, x=250, y=10, level=3)
    sam = a_sam(st, pid=0, x=100, y=20, level=1)
    target = st.gmap.ref(100, 20)          # SAM 바로 위 (사거리 안)

    first = st.launch_nuke(1, UnitType.ATOM_BOMB, target)
    assert first is not None
    for _ in range(300):
        st.tick()
        if first not in st.nukes:
            break
    assert first not in st.nukes, "첫 발이 아직 날고 있다 — 검사가 무의미하다"
    assert _sam_hits(st) == 1, "첫 발을 요격하지 못했다"
    assert len(sam.missile_queue) == 1, "요격했는데 관이 안 막혔다"

    # 재장전이 끝나기 전에 두 번째 발
    assert sam.in_cooldown, "SAM 이 이미 재장전을 마쳤다 — 검사가 무의미하다"
    second = st.launch_nuke(1, UnitType.ATOM_BOMB, target)
    assert second is not None
    for _ in range(300):
        st.tick()
        if second not in st.nukes:
            break
        if not sam.missile_queue:
            break                          # 재장전이 끝나 버리면 검사가 무의미하다
    assert _sam_hits(st) == 1, "재장전 중인 SAM 이 두 번째 발을 또 요격했다"


def test_the_sam_intercepts_again_once_reloaded():
    """**대조군.** 재장전이 끝나면 다시 요격한다.

    이게 없으면 위 테스트는 "SAM 이 아예 요격을 안 한다"는 구현도 통과시킨다."""
    st = state()
    a_silo(st, pid=1, x=250, y=10, level=3)
    sam = a_sam(st, pid=0, x=100, y=20, level=1)
    target = st.gmap.ref(100, 20)

    st.launch_nuke(1, UnitType.ATOM_BOMB, target)
    for _ in range(300):
        st.tick()
        if _sam_hits(st) == 1:
            break
    assert _sam_hits(st) == 1
    while sam.missile_queue:               # 재장전이 끝날 때까지 기다린다
        st.tick()
    st.launch_nuke(1, UnitType.ATOM_BOMB, target)
    for _ in range(300):
        st.tick()
        if _sam_hits(st) == 2:
            break
    assert _sam_hits(st) == 2, "재장전이 끝났는데 요격을 못 했다"


def test_a_sam_reloads_every_ready_tube_at_once():
    """⚠ **SAM 은 끝난 관을 전부 비운다**(원본이 `while`). 사일로와 다르다.

    같은 상수(90)를 쓴다고 사일로와 같은 코드로 합치면 이 차이가 사라진다."""
    st = state()
    sam = a_sam(st, pid=0, x=100, y=20, level=3)
    now = st.tick_count
    for _ in range(3):
        sam.fire(now)
    assert len(sam.missile_queue) == 3
    for _ in range(C.SAM_COOLDOWN_TICKS):
        st.tick()
    assert sam.missile_queue == [], "SAM 이 tick 당 하나씩만 열었다"


def test_sam_tubes_equal_level():
    st = state()
    sam = a_sam(st, pid=0, x=100, y=20, level=2)
    assert sam.ready_tubes == 2
    sam.fire(st.tick_count)
    assert sam.ready_tubes == 1 and not sam.in_cooldown
    sam.fire(st.tick_count)
    assert sam.in_cooldown


# --- `ready_missiles` --------------------------------------------------------

def test_ready_missiles_sums_open_tubes():
    """`readyMissileCount()` — 핵 대량 구매의 상한이 된다."""
    st = state()
    a_silo(st, x=10, y=10, level=2)
    a_silo(st, x=60, y=10, level=1)
    assert st.ready_missiles(0) == 3
    st.launch_nuke(0, UnitType.ATOM_BOMB, enemy_tile(st))
    assert st.ready_missiles(0) == 2


def test_ready_missiles_ignores_silos_under_construction():
    st = state()
    st.build(0, UnitType.MISSILE_SILO, st.gmap.ref(10, 10))
    assert st.ready_missiles(0) == 0


# ---------------------------------------------------------------------------
# 확인한 변이 (2026-08-24) — 전부 잡혔다
#
# 1. `launch_nuke` 의 `not u.in_cooldown` 제거
#      → test_a_silo_cannot_fire_twice_in_a_row
# 2. `silo.fire(...)` 호출 제거
#      → test_a_silo_cannot_fire_twice_in_a_row
# 3. `in_cooldown` 의 `== self.level` → `>= 1`
#      → test_tubes_equal_level
# 4. `reload_front` 를 `reload_ready` 로 (사일로가 tick 당 전부 열림)
#      → test_a_silo_only_reloads_one_tube_per_tick
# 5. SAM 의 `reload_ready` 를 `reload_front` 로
#      → test_a_sam_reloads_every_ready_tube_at_once
# 6. `_sam_intercepts` 의 `or sam.in_cooldown` 제거
#      → test_a_sam_cannot_intercept_forever
#      ⚠ 처음엔 살아남았다. 핵을 **한 발만** 쏘고 있어서 검사가 있으나 없으나
#        결과가 같았다 — 재료 문제였다(이 프로젝트에서 네 번째).
# 7. `sam.fire(...)` 호출 제거
#      → test_a_sam_cannot_intercept_forever
# 8. 업그레이드의 `unit.fire(...)` 제거
#      → test_upgrading_does_not_hand_you_an_instant_shot
# 9. 쿨다운 `>= cooldown` → `> cooldown`
#      → test_the_silo_reopens_after_exactly_the_cooldown
# 10. `ready_tubes` 가 건설 중을 안 봄
#      → test_ready_missiles_ignores_silos_under_construction
# ---------------------------------------------------------------------------
