"""스폰 페이즈 — 사람이 시작 위치를 고르는 동안 판 전체가 멈춘다.

이식 누락이었다. 원본은 `activeDuringSpawnPhase()` 가 false 인 Execution 을 전부
건너뛴다 — AI 도, 공격도, 성장도 안 돈다. 우리는 자동 배정 후 바로 시작했다.

싱글플레이는 **고르는 순간 페이즈가 끝난다**(`SpawnExecution` 의 마지막 분기).

출처: `GameImpl.inSpawnPhase` · `SpawnExecution.ts` · `Config.numSpawnPhaseTurns`
"""

from __future__ import annotations

import random

import pytest

from domynion.ai import nation
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState


def state(n: int = 2) -> GameState:
    """넉넉한 빈 지도. 반경 4 원이 여러 자리에 들어가야 재배치를 잴 수 있다."""
    gm = GameMap.from_rows(["." * 80] * 40)
    players = {}
    for pid in range(n):
        p = PlayerState(pid=pid, name=f"P{pid}", start=None)
        p.kind = "human" if pid == 0 else "nation"
        p.troops = 50_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 0 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.spawn_phase = True
    return st


# --- 페이즈 동안 판이 멈춘다 ------------------------------------------------

def test_nothing_happens_while_choosing():
    """막지 않았으면: 고르는 동안 AI 가 먼저 크고 사람은 뒤처진 채 시작한다."""
    st = state()
    st.gmap.owner[st.gmap.ref(60, 20)] = 1
    st._counts[1] = 1
    before = st.players[1].troops
    for _ in range(30):
        st.tick()
    assert st.players[1].troops == before, "병력이 자라면 안 된다"
    assert st.tiles(1) == 1, "AI 가 확장하면 안 된다"


def test_time_still_passes_so_the_phase_can_end():
    st = state()
    st.tick()
    assert st.tick_count == 1


def test_the_phase_ends_on_its_own_if_nobody_picks():
    """원본도 기다려 주지 않는다 — `numSpawnPhaseTurns` 가 상한이다."""
    st = state()
    for _ in range(C.SPAWN_PHASE_TURNS):
        st.tick()
    assert not st.spawn_phase


def test_attacks_do_not_start_during_the_phase():
    st = state()
    st.gmap.owner[st.gmap.ref(10, 10)] = 0
    st.gmap.owner[st.gmap.ref(11, 10)] = 1
    st._counts = {0: 1, 1: 1}
    a = st.launch_attack(0, 1)
    st.tick()
    assert a is None or a.tiles_taken == 0


# --- 고르기 -----------------------------------------------------------------

def test_picking_gives_you_the_radius_four_circle():
    st = state()
    assert st.choose_spawn(0, st.gmap.ref(20, 20))
    # 반경 4 원은 49칸이다(dx²+dy² ≤ 16 인 격자점)
    assert st.tiles(0) == 49
    assert st.players[0].start == st.gmap.ref(20, 20)


def test_you_can_move_before_you_commit():
    """페이즈 동안은 몇 번이든 옮길 수 있다(원본도 그렇다)."""
    st = state()
    st.choose_spawn(0, st.gmap.ref(20, 20))
    first = set(map(int, st.gmap.owned_refs(0)))
    assert st.choose_spawn(0, st.gmap.ref(50, 20))
    second = set(map(int, st.gmap.owned_refs(0)))
    assert first.isdisjoint(second), "옛 자리를 반납해야 한다"
    assert st.tiles(0) == 49, "옮긴 자리마다 쌓이면 안 된다"


def test_moving_actually_releases_the_old_tiles():
    """막지 않았으면: 고르는 것만으로 영토가 쌓여 그걸로 이길 수 있다."""
    st = state()
    st.choose_spawn(0, st.gmap.ref(20, 20))
    old = st.gmap.ref(20, 20)
    st.choose_spawn(0, st.gmap.ref(50, 20))
    assert int(st.gmap.owner[old]) == -1


def test_you_cannot_pick_a_spot_with_nothing_usable():
    st = state()
    for x in range(0, 80):
        for y in range(0, 40):
            st.gmap.owner[st.gmap.ref(x, y)] = 1
    assert st.choose_spawn(0, st.gmap.ref(40, 20)) is False


def test_a_sliver_is_refused_even_though_it_is_not_empty():
    """**0 칸이 아니라 "너무 적은" 것을 막아야 한다.**

    막지 않았으면: 반도 끝 몇 칸을 골라도 시작이 돼 첫 공격에 사라진다.
    아래 첫 단언이 "빈 자리가 아니다"를 먼저 확인한다."""
    from domynion.core.spawn import spawn_tiles
    st = state()
    for x in range(0, 80):                 # 아래 두 줄만 남기고 전부 남의 땅
        for y in range(0, 38):
            st.gmap.owner[st.gmap.ref(x, y)] = 1
    edge = st.gmap.ref(40, 39)
    got = spawn_tiles(st.gmap, edge, require_all_valid=False)
    assert 0 < len(got) < C.SPAWN_MIN_TILES, f"조각이 아니다: {len(got)}칸"
    assert st.choose_spawn(0, edge) is False


def test_picking_is_refused_once_the_phase_is_over():
    st = state()
    st.end_spawn_phase()
    assert st.choose_spawn(0, st.gmap.ref(20, 20)) is False


# --- 페이즈가 끝난 뒤 -------------------------------------------------------

def test_the_clock_restarts_so_immunity_is_not_eaten():
    """막지 않았으면: 늦게 고른 사람일수록 스폰 면역을 덜 받는다."""
    st = state()
    for _ in range(40):
        st.tick()
    st.choose_spawn(0, st.gmap.ref(20, 20))
    st.end_spawn_phase()
    assert st.tick_count == 0
    assert st.is_immune(0), "고르자마자 면역이 시작돼야 한다"


def test_the_game_runs_after_the_phase():
    st = state()
    st.choose_spawn(0, st.gmap.ref(20, 20))
    st.choose_spawn(1, st.gmap.ref(60, 20))
    st.end_spawn_phase()
    before = st.players[0].troops
    st.tick()
    assert st.players[0].troops != before, "병력이 다시 자라야 한다"


def test_ai_starts_expanding_only_after_the_phase():
    st = state()
    st.choose_spawn(0, st.gmap.ref(20, 20))
    st.choose_spawn(1, st.gmap.ref(60, 20))
    bots = nation.attach(st, random.Random(0), "medium")
    st.end_spawn_phase()
    for _ in range(200):
        st.tick()
        for b in bots:
            b.tick(st)
    assert st.tiles(1) > 49, "페이즈가 끝났으면 AI 가 커져야 한다"


# --- 새 판 -----------------------------------------------------------------

def test_a_headless_game_skips_the_phase():
    """고를 사람이 없으면 기다릴 이유가 없다 — 헤드리스 측정이 멈춘다."""
    st = GameState.new(3, random.Random(0), map_name="world",
                       human=-1, size="map16x")
    assert not st.spawn_phase


def test_a_game_with_a_human_starts_in_the_phase():
    st = GameState.new(3, random.Random(0), map_name="world",
                       human=0, size="map16x")
    assert st.spawn_phase
