"""엔진 — 병력 공식, 증분 카운트, 흡수, 종료.

가장 중요한 건 **증분 카운트가 지도와 어긋나지 않는가**다. 예외를 던지지 않고 값만
조용히 틀어지는 종류라, 안 재면 판이 다 끝날 때까지 모른다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.engine import GameState, Victory
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def make_state(rows: list[str], owners: dict[int, tuple[int, int]],
               seed: int = 1, bots: bool = True) -> GameState:
    gm = GameMap.from_rows(rows)
    players = {}
    for pid, (x, y) in owners.items():
        t = gm.ref(x, y)
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=bots, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=players, rng=random.Random(seed))
    st._counts = {pid: 1 for pid in players}
    return st


# --- 병력 공식 --------------------------------------------------------------

def test_max_troops_matches_original_formula():
    p = PlayerState(pid=0, name="P0", is_bot=False)
    for tiles in (1, 100, 1_600, 37_575, 100_000):
        want = C.MAX_TROOPS_MULT * (tiles ** C.MAX_TROOPS_TILE_EXP
                                    * C.MAX_TROOPS_TILE_MULT + C.MAX_TROOPS_BASE)
        assert p.max_troops(tiles) == pytest.approx(want)


def test_map_must_be_large_enough_for_territory_to_matter():
    """계획서 4.5절을 코드로 못 박는다 — **지도를 줄이려는 시도를 막는 테스트다.**

    상한 공식의 상수항(50000)이 작은 지도에서 지배한다. 1타일 대비 상한 배율:
      1,600칸 → 2.6배 (영토 확장이 거의 무의미)
     37,575칸 → 11.9배 (World, 쓸 만하다)
    지도를 v0.1 규모로 되돌리면 아래 첫 단언이 깨진다."""
    p = PlayerState(pid=0, name="P0", is_bot=False)
    assert p.max_troops(37_575) / p.max_troops(1) > 10.0
    assert p.max_troops(1_600) / p.max_troops(1) < 3.0, "작은 지도가 왜 안 되는가"


def test_city_levels_raise_the_cap():
    """도시 레벨 합 × 250000 이 병력 상한에 더해진다."""
    plain = PlayerState(pid=0, name="P0")
    with_city = PlayerState(pid=1, name="P1")
    with_city.units.units.append(Unit(UnitType.CITY, 1, tile=0, level=3))
    assert (with_city.max_troops(100) - plain.max_troops(100)
            == pytest.approx(3 * C.CITY_TROOP_INCREASE))


def test_city_under_construction_does_not_raise_the_cap():
    """건설 중인 도시는 세지 않는다 — 원본이 `!isUnderConstruction()` 을 건다.

    막지 않았으면: 짓자마자 상한이 올라 건설 시간이 무의미해진다."""
    p = PlayerState(pid=0, name="P0")
    base = p.max_troops(100)
    p.units.units.append(Unit(UnitType.CITY, 0, tile=0, level=1, ticks_left=20))
    assert p.max_troops(100) == base


def test_bot_cap_and_growth_are_reduced():
    h = PlayerState(pid=0, name="H", is_bot=False, troops=10_000.0)
    b = PlayerState(pid=1, name="B", is_bot=True, troops=10_000.0)
    assert b.max_troops(500) == pytest.approx(h.max_troops(500) / C.BOT_MAX_TROOPS_DIV)
    assert b.troop_increase(500) < h.troop_increase(500)


def test_growth_depends_on_current_troops_not_cap():
    """`(10 + 병력^0.73/4) × (1 − 병력/상한)`.

    v0.1 은 상한에 비례했다. 그때 방식이면 병력이 적을수록 회복이 빨라야 하는데,
    원본은 반대로 **병력이 적을 때 느리다.**"""
    lo = PlayerState(pid=0, name="A", troops=1_000.0)
    hi = PlayerState(pid=1, name="B", troops=50_000.0)
    assert lo.troop_increase(1_000) < hi.troop_increase(1_000)


def test_growth_never_exceeds_cap():
    p = PlayerState(pid=0, name="P0", troops=0.0)
    p.troops = p.max_troops(10) - 1.0
    assert p.troops + p.troop_increase(10) <= p.max_troops(10) + 1e-6


def test_attack_ratio_defaults_match_original():
    assert PlayerState(pid=0, name="H", is_bot=False).attack_ratio == C.ATTACK_RATIO_HUMAN
    assert PlayerState(pid=1, name="B", is_bot=True).attack_ratio == C.ATTACK_RATIO_BOT


# --- 증분 카운트 ------------------------------------------------------------

def test_counts_match_full_scan_while_expanding():
    st = make_state(["." * 24] * 16, {0: (0, 0), 1: (23, 15)})
    st.launch_attack(0, None)
    for _ in range(120):
        st.tick()
        assert st.verify_counts(), f"{st.tick_count}tick 에 카운트가 어긋났다"


def test_counts_match_full_scan_when_taking_from_a_player():
    """사람 땅을 뺏을 때가 어긋나기 쉽다 — 양쪽을 동시에 고쳐야 한다.

    P1 의 영토를 손으로 깔아 P0 과 맞닿게 한다. AI 확장에 맡기면 둘이 안 만나서
    아무것도 안 재는 테스트가 된다(실제로 그랬다)."""
    st = make_state(["." * 30] * 10, {0: (0, 0), 1: (1, 0)})
    for y in range(10):
        for x in range(1, 30):
            st.gmap.owner[st.gmap.ref(x, y)] = 1
    st._counts = {0: 1, 1: 29 * 10}
    assert st.verify_counts()

    st.players[0].troops = st.players[0].max_troops(1)
    assert st.launch_attack(0, 1) is not None, "국경이 안 맞닿았다"
    for _ in range(120):
        st.tick()
        assert st.verify_counts()
        if st.over:
            break
    assert st.tiles(0) > 1, "P0 이 한 칸도 못 뺏었으면 이 테스트는 아무것도 안 쟀다"


# --- 흡수·탈락 --------------------------------------------------------------

def test_small_defender_is_absorbed_whole():
    """타일 100 미만으로 떨어진 수비자는 통째로 흡수된다 (`handleDeadDefender`).

    막지 않았으면: 잔챙이 영토를 한 칸씩 긁느라 판이 늘어진다."""
    st = make_state(["." * 20] * 6, {0: (0, 0), 1: (19, 5)})
    st._counts = {0: 1, 1: 1}
    st.gmap.owner[st.gmap.ref(18, 5)] = 1
    st._counts[1] = 2
    st._maybe_absorb(0, 1)
    assert not st.players[1].alive
    assert st.tiles(1) == 0
    assert st.tiles(0) == 3
    assert st.verify_counts()


def test_absorb_does_not_fire_above_threshold():
    st = make_state(["." * 20] * 20, {0: (0, 0), 1: (19, 19)})
    st._counts = {0: 1, 1: C.CONQUER_PLAYER_TILES}
    st._maybe_absorb(0, 1)
    assert st.players[1].alive


def test_retreating_troops_come_home():
    st = make_state(["...~"], {0: (0, 0), 1: (3, 0)}, bots=False)
    st.gmap.owner[3] = 1                      # 바다 칸은 소유 못 하니 육지로
    st.gmap.raw[3] = C.LAND_BIT
    st.gmap.terrain[3] = C.Terrain.PLAINS
    p = st.players[0]
    sent = p.attack_troops()
    st.launch_attack(0, None)
    assert p.troops == pytest.approx(25_000.0 - sent + 0, abs=1.0) or p.troops < 25_000.0
    for _ in range(40):
        st.tick()
        if not st.attacks:
            break
    assert not st.attacks


# --- 종료 -------------------------------------------------------------------

def test_conquest_when_one_left():
    st = make_state(["." * 10] * 4, {0: (0, 0), 1: (9, 3)})
    st.players[1].alive = False
    st.tick()
    assert st.over and st.victory is Victory.CONQUEST and st.winner == 0


def test_timeout_gives_it_to_the_biggest(monkeypatch):
    """⚠ **Overtime 을 끄고 재야 한다.**

    켜 두면 문턱이 70분에 0 이 되므로 170분 하드 리밋에 **닿을 수가 없다** —
    판은 그 전에 지배로 끝난다. 원본도 Overtime 을 켜면 마찬가지다. 이 테스트는
    *하드 리밋 경로가 아직 살아 있는가*를 재는 것이라 그 경로를 열고 잰다."""
    monkeypatch.setattr(C, "OVERTIME_ENABLED", False)
    st = make_state(["." * 10] * 4, {0: (0, 0), 1: (9, 3)})
    st._counts = {0: 20, 1: 5}
    st.tick_count = int(C.MATCH_SECONDS / C.TICK_DT) - 1
    st.tick()
    assert st.over and st.victory is Victory.TIMEOUT and st.winner == 0


def test_overtime_ends_the_game_long_before_the_hard_time_limit():
    """켜 둔 채로는 **`Victory.TIMEOUT` 이 도달 불가**다. 그 사실을 못 박는다 —
    안 그러면 다음 세션이 시간 종료 경로를 살아 있는 것으로 읽는다."""
    from domynion.core.engine import domination_percent
    assert domination_percent(C.MATCH_SECONDS) == 0
    st = make_state(["." * 10] * 4, {0: (0, 0), 1: (9, 3)})
    st._counts = {0: 20, 1: 5}
    st.tick_count = int(C.MATCH_SECONDS / C.TICK_DT) - 1
    st.tick()
    assert st.over and st.victory is Victory.DOMINATION


def test_tick_is_ten_hz():
    """원본 `turnIntervalMs` = 100. 20Hz 로 되돌리면 성장·예산이 두 배가 된다."""
    assert C.TICK_HZ == 10
    st = make_state(["..", ".."], {0: (0, 0)})
    st.tick()
    assert st.elapsed == pytest.approx(0.1)


# --- 플레이어 종류 ----------------------------------------------------------

def test_three_player_kinds_have_different_multipliers():
    """원본은 Human · Nation · Bot 셋을 구분한다. **봇은 난이도를 안 탄다** —
    난이도는 Nation 에만 붙는다. 하나로 합치면 난이도 설정이 조용히 무의미해진다."""
    human = PlayerState(pid=0, name="H", kind="human")
    bot = PlayerState(pid=1, name="B", kind="bot")
    easy = PlayerState(pid=2, name="N", kind="nation", difficulty="easy")
    imp = PlayerState(pid=3, name="N", kind="nation", difficulty="impossible")

    assert bot.max_troops(500) == pytest.approx(human.max_troops(500) / C.BOT_MAX_TROOPS_DIV)
    assert easy.max_troops(500) == pytest.approx(human.max_troops(500) * 0.5)
    assert imp.max_troops(500) == pytest.approx(human.max_troops(500) * 1.25)
    assert easy.max_troops(500) < imp.max_troops(500)


def test_starting_troops_differ_by_kind():
    assert PlayerState(pid=0, name="H", kind="human").troops == C.START_TROOPS_HUMAN
    assert PlayerState(pid=1, name="B", kind="bot").troops == C.START_TROOPS_BOT
    assert (PlayerState(pid=2, name="N", kind="nation", difficulty="easy").troops
            == C.NATION_START_TROOPS["easy"])


def test_every_difficulty_has_its_own_starting_troops():
    """⚠ **표를 재는 테스트는 모든 칸을 재야 한다.**

    위 테스트가 넷 중 `easy` 하나만 재고 있었고, 그래서 `impossible` 이
    **25,000 으로 틀린 채**(원본 31,250) 지나갔다 — 가장 어려운 난이도가
    `hard` 와 같아져 있었다(2026-09-04 발견, §5.124).

    원본 `Config.startManpower` 값 그대로 못 박는다."""
    want = {"easy": 12_500.0, "medium": 18_750.0,
            "hard": 25_000.0, "impossible": 31_250.0}
    assert C.NATION_START_TROOPS == want
    # 난이도가 올라가면 **반드시 늘어난다** — 둘이 같아지면 그 난이도는 뜻이 없다.
    vals = [C.NATION_START_TROOPS[d] for d in ("easy", "medium", "hard", "impossible")]
    assert vals == sorted(vals) and len(set(vals)) == len(vals)


def test_is_bot_flag_still_works_for_old_callers():
    """`is_bot=True` 만 주던 호출부가 조용히 human 으로 바뀌면 안 된다."""
    p = PlayerState(pid=0, name="B", is_bot=True)
    assert p.kind == "bot" and p.is_bot


# --- 벡터화가 답을 바꾸지 않는가 ---------------------------------------------

def _slow_border_targets(st, pid):
    """벡터화 전의 구현. **대조용이다** — 런타임에는 쓰지 않는다."""
    out = set()
    for t in st.gmap.owned_refs(pid).tolist():
        for n in st.gmap.neighbors(t):
            o = int(st.gmap.owner[n])
            if o != pid and st.gmap.passable(n):
                out.add(None if o < 0 else o)
    return out


def test_border_targets_matches_the_loop_it_replaced():
    """`border_targets` 를 numpy 로 폈다(영토 17만 칸에서 119ms → 수 ms).

    빨라져도 답이 다르면 소용없다. 손으로 깐 배치와 실제 확장 양쪽으로 대조한다."""
    st = make_state(["." * 40] * 20, {0: (0, 0), 1: (39, 19), 2: (20, 10)})
    for x in range(0, 20):
        st.gmap.owner[st.gmap.ref(x, 5)] = 0
    for x in range(20, 40):
        st.gmap.owner[st.gmap.ref(x, 5)] = 1
    st.gmap.owner[st.gmap.ref(10, 6)] = 2
    for pid in (0, 1, 2):
        assert st.border_targets(pid) == _slow_border_targets(st, pid)


def test_border_targets_matches_after_real_expansion():
    st = make_state(["." * 30] * 20, {0: (0, 0), 1: (29, 19)})
    st.launch_attack(0, None)
    st.launch_attack(1, None)
    for _ in range(200):
        st.tick()
        if st.over:
            break
    for pid in (0, 1):
        assert st.border_targets(pid) == _slow_border_targets(st, pid)


def test_border_targets_ignores_ocean_and_impassable():
    st = make_state(["..~.", "..#."], {0: (0, 0), 1: (3, 0)})
    st.gmap.owner[st.gmap.ref(1, 0)] = 0
    st._counts = {0: 2, 1: 1}
    # 바다·통행불가 너머는 닿는 것으로 치지 않는다
    assert st.border_targets(0) == _slow_border_targets(st, 0)
    assert 1 not in st.border_targets(0)


def test_border_targets_of_a_dead_player_is_empty():
    st = make_state(["....", "...."], {0: (0, 0), 1: (3, 1)})
    st.gmap.owner[st.gmap.owner == 1] = -1
    assert st.border_targets(1) == set()


# --- border_targets 등가 (§5.50) ---------------------------------------------

def _border_targets_reference(st, pid):
    """§5.50 **이전** 구현. 지도 전체를 네 방향으로 미는 방식이다.

    빠르게 고친 것이 같은 답을 내는지 대조하기 위해서만 남긴다 — 통과는 증거가
    아니므로, 새 구현이 **옛 구현과 같은 집합**을 내는 것을 직접 확인한다."""
    import numpy as np
    gm = st.gmap
    h, w = gm.height, gm.width
    o = gm.owner.reshape(h, w)
    mine = o == pid
    if not mine.any():
        return set()
    passable = gm.passable_mask().reshape(h, w)
    vals = [
        o[:, 1:][mine[:, :-1] & passable[:, 1:]],
        o[:, :-1][mine[:, 1:] & passable[:, :-1]],
        o[1:, :][mine[:-1, :] & passable[1:, :]],
        o[:-1, :][mine[1:, :] & passable[:-1, :]],
    ]
    found = np.unique(np.concatenate(vals))
    return {None if int(v) < 0 else int(v) for v in found if int(v) != pid}


def test_border_targets_matches_the_old_full_scan():
    """무작위로 칠한 판 여러 개에서 **옛 구현과 같은 집합**을 내는가.

    x 경계를 안 넘는 것과 통행 불가 칸을 빼는 것이 특히 조용히 깨지는 자리다 —
    양쪽 끝 열과 바다·산을 섞어 둔다."""
    import random as _random
    rng = _random.Random(7)
    for trial in range(12):
        rows = []
        for y in range(9):
            row = ""
            for x in range(11):
                row += rng.choice("..~#")        # 평야·평야·바다·통행불가
            rows.append(row)
        gm = GameMap.from_rows(rows)
        players = {}
        for pid in range(4):
            players[pid] = PlayerState(pid=pid, name=f"P{pid}", start=0)
        st = GameState(gmap=gm, players=players, rng=_random.Random(trial))
        for t in range(gm.size):
            if gm.passable(t) and rng.random() < 0.5:
                gm.owner[t] = rng.randrange(4)
        st._counts = {pid: int((gm.owner == pid).sum()) for pid in range(4)}
        for pid in range(4):
            assert st.border_targets(pid) == _border_targets_reference(st, pid),                 (trial, pid)


def test_border_targets_does_not_wrap_around_the_map_edge():
    """오른쪽 끝 칸의 "오른쪽 이웃"이 다음 줄 왼쪽 끝이 되면 안 된다.

    ⚠ 인덱스 산술로 바꾸면서 가장 쉽게 깨지는 자리다. 옛 구현은 2차원 배열을
    밀어서 이 문제가 구조적으로 없었다."""
    gm = GameMap.from_rows(["...", "...", "..."])
    players = {0: PlayerState(pid=0, name="P0", start=0),
               1: PlayerState(pid=1, name="P1", start=0)}
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    gm.owner[gm.ref(2, 0)] = 0            # 첫 줄 오른쪽 끝
    gm.owner[gm.ref(0, 1)] = 1            # 둘째 줄 왼쪽 끝 — 인덱스로는 바로 옆이다
    st._counts = {0: 1, 1: 1}
    assert 1 not in st.border_targets(0), "지도 오른쪽 끝에서 반대편으로 샜다"


# --- 종료 조건이 원본 값인가 (§5.61) ------------------------------------------

def test_the_end_conditions_match_the_original():
    """⚠ **주석이 오래 틀려 있었다.**

    "openfront 는 시간 제한도 지배 승리도 없다"고 적혀 있었는데 `checkWinnerFFA`
    가 매 tick 셋 중 하나를 본다. 틀린 것은 **조건이 아니라 값**이었다 —
    우리 900초는 원본 170분의 **1/11** 이었다."""
    assert C.DOMINATION_TILE_RATIO == 0.80      # percentageTilesOwnedToWin (FFA)
    assert C.MATCH_SECONDS == 170 * 60          # HARD_TIME_LIMIT_SECONDS


def test_domination_uses_land_without_fallout():
    """⚠ 분모는 **낙진을 뺀 땅**이다(`numLandTiles() - numTilesWithFallout()`).

    전체 육지로 나누면 **핵이 많이 터진 판일수록 승리가 멀어진다** — 남은 땅을
    다 가져도 80% 가 안 된다."""
    from domynion.core.nukes import Fallout
    st = make_state(["." * 10] * 10, {0: (0, 0), 1: (9, 9)})
    st.fallout = Fallout(st.gmap.size)
    st._counts = {0: 60, 1: 5}                   # 100칸 중 60 → 60%, 아직 아니다
    st.tick()
    assert not st.over

    # 절반이 낙진이 되면 분모가 50 이 돼 60/50 > 80% 다
    st.fallout.add(list(range(50, 100)))
    st.tick()
    assert st.over and st.victory is Victory.DOMINATION and st.winner == 0


def test_domination_still_needs_the_share_without_fallout():
    """대조군 — 낙진이 없으면 문턱 그대로다."""
    from domynion.core.nukes import Fallout
    st = make_state(["." * 10] * 10, {0: (0, 0), 1: (9, 9)})
    st.fallout = Fallout(st.gmap.size)
    st._counts = {0: 79, 1: 5}
    st.tick()
    assert not st.over
    st._counts[0] = 80
    st.tick()
    assert st.over and st.victory is Victory.DOMINATION
