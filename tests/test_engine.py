"""게임 루프 — 증분 카운트, 증강 정지, 승리 판정.

여기서 가장 중요한 건 **증분 카운트가 실제 지도와 어긋나지 않는가**다. 이건 예외를
던지지 않고 값만 조용히 틀어지는 종류의 버그라, 안 재면 판이 다 끝날 때까지 모른다.
"""

from __future__ import annotations

import random

from domynion.core import constants as C
from domynion.core.engine import GameState, Victory
from domynion.core.state import PlayerState

from conftest import make_map


def make_state(rows: list[str], owners: dict[int, tuple[int, int]],
               seed: int = 1) -> GameState:
    gm = make_map(rows)
    players = {}
    for pid, pos in owners.items():
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_ai=True, start=pos)
        gm[pos].owner = pid
    st = GameState(gmap=gm, players=players, rng=random.Random(seed))
    st._counts = {pid: 1 for pid in players}
    st._land_total = len(gm.land_tiles())
    return st


def scan_counts(st: GameState) -> dict[int, int]:
    """지도를 전수 순회해 센다. 런타임에는 절대 이렇게 세지 않는다 — 대조용이다."""
    out = {pid: 0 for pid in st.players}
    for t in st.gmap.all_tiles():
        if t.owner is not None:
            out[t.owner] = out.get(t.owner, 0) + 1
    return out


# --- 증분 카운트 -----------------------------------------------------------

def test_counts_match_full_scan_after_expansion():
    st = make_state(["." * 12] * 8, {0: (0, 0), 1: (11, 7)})
    st.players[0].troops = 400.0
    st.launch_attack(0, None)
    for _ in range(400):
        st.tick()
        assert st._counts == scan_counts(st), f"{st.elapsed:.1f}초에 카운트가 어긋났다"


def test_counts_match_full_scan_when_taking_from_a_player():
    """중립이 아니라 **사람 땅**을 뺏을 때가 어긋나기 쉽다 — 양쪽을 동시에 고쳐야 한다."""
    st = make_state(["." * 10] * 4, {0: (0, 0), 1: (9, 0)})
    st.players[1].troops = 300.0
    st.launch_attack(1, None)                # P1 이 먼저 중립을 넓게 먹는다
    for _ in range(200):
        st.tick()
    st.players[0].troops = 600.0
    st.launch_attack(0, 1)                   # 그 땅을 P0 이 친다
    for _ in range(400):
        st.tick()
        assert st._counts == scan_counts(st)
    assert st._counts[0] > 1, "P0 이 한 칸도 못 뺏었으면 이 테스트는 아무것도 안 쟀다"


# --- 병력 -----------------------------------------------------------------

def test_growth_is_wired_to_constants(monkeypatch):
    """배선 검증은 **기본값이 아닌 값**으로 잰다. 상수를 바꿨는데 결과가 그대로면
    엔진이 상수를 안 읽고 있다는 뜻이다."""
    st = make_state(["." * 6] * 4, {0: (0, 0)})
    st.players[0].troops = 10.0
    before = st.players[0].troops
    st.tick(1.0)
    normal = st.players[0].troops - before

    monkeypatch.setattr(C, "TROOPS_GROWTH_RATE", C.TROOPS_GROWTH_RATE * 4)
    st2 = make_state(["." * 6] * 4, {0: (0, 0)})
    st2.players[0].troops = 10.0
    st2.tick(1.0)
    boosted = st2.players[0].troops - 10.0
    assert boosted > normal * 1.5, f"성장률을 4배로 올렸는데 {normal:.2f}→{boosted:.2f}"


def test_leftover_troops_return_home():
    """부대가 멈추면 남은 병력은 사라지지 않고 본국으로 돌아온다."""
    # 두 명을 둔다 — 혼자면 첫 tick 에 정복 승리로 판이 끝나 부대가 진행하지 않는다
    st = make_state(["...."], {0: (0, 0), 1: (3, 0)})
    st.players[0].troops = 1000.0
    st.launch_attack(0, None)
    assert st.players[0].troops < 1000.0, "출정한 병력이 본국에서 빠지지 않았다"
    for _ in range(100):
        st.tick()
        if not st.attacks:
            break
    assert not st.attacks
    # 중립 두 칸만 먹고 멈추므로 거의 전부 돌아와야 한다
    assert st._counts[0] == 3
    assert st.players[0].troops > 900.0


# --- 증강 정지 -------------------------------------------------------------

def test_pause_fires_at_first_augment_time():
    st = make_state(["." * 8] * 6, {0: (0, 0), 1: (7, 5)})
    while st.elapsed < C.AUGMENT_FIRST_SEC - C.TICK_DT:
        st.tick()
    assert all(not p.augments for p in st.players.values()), "정지 전에 증강이 붙었다"
    st.tick()
    # AI 는 즉시 고르므로 같은 tick 에 정지가 풀린다 — 결과로 확인한다
    assert all(sum(p.augments.values()) == 1 for p in st.players.values())
    assert not st.paused, "AI 만 있는 판에서 정지가 풀리지 않았다"


def test_human_blocks_until_pick_then_timeout_resolves():
    st = make_state(["." * 8] * 6, {0: (0, 0), 1: (7, 5)})
    st.players[0].is_ai = False
    while not st.paused and st.elapsed < C.AUGMENT_FIRST_SEC + 1.0:
        st.tick()
    assert st.paused, "사람이 안 골랐는데 판이 계속 흘렀다"

    frozen = st.elapsed
    for _ in range(10):
        st.tick()
    assert st.elapsed == frozen, "정지 중에 시계가 흘렀다"

    for _ in range(int(C.AUGMENT_PICK_TIMEOUT / C.TICK_DT) + 2):
        st.tick()
    assert not st.paused, "시간이 다했는데도 판이 멈춰 있다 — 한 명이 판 전체를 잠근다"
    assert sum(st.players[0].augments.values()) == 1, "자동 선택이 안 됐다"


def test_max_level_card_leaves_the_pool():
    st = make_state(["." * 8] * 6, {0: (0, 0)})
    p = st.players[0]
    p.augments["settlers"] = C.AUGMENT_MAX_LEVEL
    st.ai_pick = lambda _p, offers: offers[0].key
    for _ in range(int(C.MATCH_SECONDS / C.TICK_DT)):
        st.tick()
        if st.over:
            break
    assert p.augments["settlers"] == C.AUGMENT_MAX_LEVEL, "최대 레벨을 넘겼다"


# --- 승리 -----------------------------------------------------------------

def test_domination_ends_the_match():
    st = make_state(["." * 10] * 4, {0: (0, 0), 1: (9, 3)})
    st.players[0].troops = 5000.0
    st.launch_attack(0, None)
    for _ in range(2000):
        st.tick()
        if st.over:
            break
    assert st.over and st.winner == 0
    assert st.victory in (Victory.DOMINATION, Victory.CONQUEST)


def test_timeout_gives_it_to_the_biggest():
    st = make_state(["." * 10] * 4, {0: (0, 0), 1: (9, 3)})
    st.players[0].troops = 60.0
    st.launch_attack(0, None)
    st.elapsed = C.MATCH_SECONDS - C.TICK_DT
    st.tick()
    st.tick()
    assert st.over and st.victory is Victory.TIMEOUT
    assert st.winner == 0, "영토가 더 넓은 쪽이 이겨야 한다"


def test_eliminated_player_stops_growing():
    st = make_state(["." * 6] * 4, {0: (0, 0), 1: (5, 3)})
    st.gmap[(5, 3)].owner = 0
    st._counts = {0: 2, 1: 0}
    st.tick()
    assert not st.players[1].alive
    assert st.players[1].troops == 0.0
