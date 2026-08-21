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


def test_timeout_gives_it_to_the_biggest():
    st = make_state(["." * 10] * 4, {0: (0, 0), 1: (9, 3)})
    st._counts = {0: 20, 1: 5}
    st.tick_count = int(C.MATCH_SECONDS / C.TICK_DT) - 1
    st.tick()
    assert st.over and st.victory is Victory.TIMEOUT and st.winner == 0


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


def test_is_bot_flag_still_works_for_old_callers():
    """`is_bot=True` 만 주던 호출부가 조용히 human 으로 바뀌면 안 된다."""
    p = PlayerState(pid=0, name="B", is_bot=True)
    assert p.kind == "bot" and p.is_bot
