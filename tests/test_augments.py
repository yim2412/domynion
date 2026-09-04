"""증강 드래프트 — **원본에 없는 우리 계층**(`docs/design.md` §3).

⚠ 이 파일이 생기기 전까지 `core/augments.py` 는 **import 조차 안 됐다.**
`C.AUGMENT_CHOICES` 등 상수가 `constants.py` 에 없었는데, 아무도 그 모듈을
안 불러서 문법 검사도 스위트도 통과한 채 남아 있었다(2026-09-04 발견).
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.augments import (AUGMENTS, AUGMENTS_BY_KEY, Modifiers,
                                    describe, level_mult, offer, value_at)
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState


def state(human: int | None = 0) -> GameState:
    gm = GameMap.from_rows(["." * 40] * 20)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid * 20 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", start=t,
                              kind="human" if pid == human else "nation")
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.human = human
    if human is not None:
        st.augment_next_tick = C.AUGMENT_FIRST_TICK
    return st


# --- 카드와 계수 ---------------------------------------------------------------

def test_every_card_lands_on_a_declared_field():
    """`FIELDS` 에 없는 축을 쓰면 `Modifiers.get` 이 0 을 돌려줘 **조용히 죽는다.**"""
    from domynion.core.augments import FIELDS
    for a in AUGMENTS:
        assert a.field in FIELDS, f"{a.key} 의 축 {a.field} 이 FIELDS 에 없다"


def test_levels_multiply_the_base_value():
    assert level_mult(1) == 1.0
    a = AUGMENTS_BY_KEY["fertile"]
    assert value_at(a, 1) == pytest.approx(a.per_level)
    assert value_at(a, 3) == pytest.approx(a.per_level * C.AUGMENT_LEVEL_MULT[2])
    # 범위를 벗어난 레벨도 죽지 않고 끝값으로 눌린다.
    assert value_at(a, 99) == value_at(a, C.AUGMENT_MAX_LEVEL)


def test_same_axis_cards_add_not_multiply():
    """⚠ **더한다.** 곱하면 카드가 쌓일수록 체감이 급격해져 후반이 독주가 된다."""
    m = Modifiers.from_augments({"fertile": 1})
    two = Modifiers.from_augments({"fertile": 2})
    assert two.get("troops_cap_pct") > m.get("troops_cap_pct")
    # 두 장이 같은 축이면 합이다 — 0.18 + 0.18×1.7 이지 곱이 아니다.
    assert two.get("troops_cap_pct") == pytest.approx(
        value_at(AUGMENTS_BY_KEY["fertile"], 2))


def test_a_discount_stack_can_never_make_conquest_free():
    """⚠ **막지 않았으면 무엇이 일어났을 것인가** — 할인을 겹쳐 배율이 0 이나
    음수가 되면 **공짜로 무한 확장**이 된다. 실제로 넘길 수 있는 조합이 있다."""
    m = Modifiers({"cost_vs_player_pct": -5.0})
    assert m.mult("cost_vs_player_pct") == 0.2
    assert m.mult("cost_vs_player_pct") > 0


def test_an_unknown_card_in_a_save_does_not_kill_the_game():
    m = Modifiers.from_augments({"없는카드": 2, "fertile": 1})
    assert m.get("troops_cap_pct") > 0


def test_the_description_shows_the_value_for_that_level():
    a = AUGMENTS_BY_KEY["fertile"]
    assert describe(a, 1) != describe(a, 3)


# --- 드래프트 후보 -------------------------------------------------------------

def test_a_maxed_card_is_not_offered():
    """고를 수 없는 카드가 자리를 차지하면 선택지가 실질 2장이 된다."""
    owned = {a.key: C.AUGMENT_MAX_LEVEL for a in AUGMENTS[:8]}
    got = offer(random.Random(0), owned, count=3)
    assert len(got) == 2                       # 남은 둘
    assert all(g.key not in owned for g in got)


def test_offering_stops_when_everything_is_maxed():
    owned = {a.key: C.AUGMENT_MAX_LEVEL for a in AUGMENTS}
    assert offer(random.Random(0), owned) == []


# --- 정지 흐름 ----------------------------------------------------------------

def _run(st, ticks):
    for _ in range(ticks):
        st.tick()


def test_the_draft_opens_at_the_first_tick_and_stops_the_game():
    st = state()
    _run(st, C.AUGMENT_FIRST_TICK - 1)
    assert st.augment_offer == []
    st.tick()
    assert len(st.augment_offer) == C.AUGMENT_CHOICES
    # **판이 멈춘다** — tick 은 흐르지만 아무 일도 안 일어난다.
    # ⚠ `tick_count` 만 보면 **어느 쪽이든 늘어난다**(정지 중에도 시계는 간다).
    # 판이 실제로 안 도는지는 **판이 하는 일**로 재야 한다 — 병력이 안 자라고
    # 관계가 안 삭는다. 처음엔 `tick_count` 만 봤다가 "판을 안 멈춘다" 변이가
    # 그대로 통과했다.
    st.players[0].troops = 100.0
    st.players[1].troops = 100.0
    before = st.tick_count
    _run(st, 5)
    assert st.tick_count == before + 5 and st.augment_offer
    assert st.players[1].troops == 100.0, "정지 중인데 병력이 자랐다"


def test_choosing_resumes_the_game_and_schedules_the_next_stop():
    st = state()
    _run(st, C.AUGMENT_FIRST_TICK)
    key = st.augment_offer[0].key
    assert st.choose_augment(key)
    assert st.augment_offer == []
    assert st.players[0].augments[key] == 1
    assert st.augment_next_tick == st.tick_count + C.AUGMENT_PERIOD_TICKS
    assert st.augments_taken == 1


def test_a_card_that_was_not_offered_cannot_be_taken():
    st = state()
    _run(st, C.AUGMENT_FIRST_TICK)
    shown = {a.key for a in st.augment_offer}
    hidden = next(a.key for a in AUGMENTS if a.key not in shown)
    assert not st.choose_augment(hidden)
    assert st.players[0].augments == {}


def test_the_limit_picks_for_you_so_a_headless_run_never_stalls():
    """⚠ **막지 않았으면 무엇이 일어났을 것인가** — 첫 정지에서 판이 영영 선다.
    스폰 페이즈와 같은 구조다: 원본도 안 고른 사람을 기다려 주지 않는다."""
    st = state()
    _run(st, C.AUGMENT_FIRST_TICK)
    assert st.augment_offer
    _run(st, C.AUGMENT_PICK_LIMIT_TICKS)
    assert st.augment_offer == [], "상한을 넘겼는데 아직 열려 있다"
    assert st.augments_taken == 1, "자동으로 골라 주지 않았다"


def test_choosing_the_same_card_raises_its_level():
    st = state()
    st.players[0].augments["fertile"] = 1
    st.augment_offer = [AUGMENTS_BY_KEY["fertile"]]
    st.augment_opened_at = st.tick_count
    st.choose_augment("fertile")
    assert st.players[0].augments["fertile"] == 2


def test_a_headless_game_never_opens_a_draft():
    """사람이 없으면 고를 사람도 없다 — §5.111 기준선이 그대로 유효한 이유다.

    ⚠ **관문이 둘이다**(`human is None` · `augment_next_tick < 0`). 둘 다 재려면
    예약 시각을 **일부러 넣어** 앞의 관문만 남긴다 — 그냥 돌리면 뒤의 관문이
    막아 줘서 앞의 것을 지워도 통과한다(실제로 그 변이가 살아남았다)."""
    st = state(human=None)
    _run(st, C.AUGMENT_FIRST_TICK + 5)
    assert st.augment_offer == [] and st.augments_taken == 0
    # 예약이 있어도 사람이 없으면 안 연다.
    st.augment_next_tick = st.tick_count
    st.tick()
    assert st.augment_offer == [], "사람이 없는데 드래프트가 열렸다"


def test_a_dead_player_stops_getting_drafts():
    """⚠ **`tick()` 으로 재면 안 된다.** 둘뿐인 판에서 사람을 죽이면 그 자리에서
    승리 판정이 나 `over` 가 되고, `tick()` 이 첫 줄에서 돌아선다 — 그러면
    "드래프트가 안 열린 이유"가 죽음이 아니라 **판의 끝**이 된다.
    판정을 직접 부른다(재료가 규칙을 가리는 그 자리다)."""
    st = state()
    st.players[0].alive = False
    st.tick_count = C.AUGMENT_FIRST_TICK
    assert st._augment_tick() is False
    assert st.augment_offer == []
    assert st.augment_next_tick == -1, "죽었는데 다음 정지가 예약돼 있다"


def test_the_multiplier_is_exactly_one_without_augments():
    """⚠ **원본 공식이 그대로 남아야 한다.** 1.0 이 아니면 증강을 안 고른
    판(= 헤드리스 기준선)이 원본과 달라진다."""
    p = PlayerState(pid=0, name="P0")
    assert p.mult("troops_cap_pct") == 1.0
    p.augments["fertile"] = 1
    assert p.mult("troops_cap_pct") > 1.0


def test_the_modifier_cache_is_dropped_when_a_card_is_taken():
    """⚠ 캐시를 안 버리면 **두 장째부터 아무 일도 안 한다.**

    첫 장은 캐시를 안 버려도 통한다 — `mult` 가 `augments` 가 비었을 때
    바로 1.0 을 돌려주므로 그때는 **캐시 자체가 없다.** 그래서 첫 장으로만
    재면 "캐시를 안 버린다" 변이가 그대로 통과한다(실제로 살아남았다).
    **한 장을 고르고 값을 읽어 캐시를 만든 뒤** 두 장째를 골라야 잡힌다."""
    st = state()
    p = st.players[0]
    st.augment_offer = [AUGMENTS_BY_KEY["fertile"]]
    st.augment_opened_at = st.tick_count
    st.choose_augment("fertile")
    first = p.mult("troops_cap_pct")            # ← 여기서 캐시가 만들어진다
    assert first > 1.0 and p.mods is not None

    st.augment_offer = [AUGMENTS_BY_KEY["fertile"]]
    st.augment_opened_at = st.tick_count
    st.choose_augment("fertile")                # Lv2
    assert p.mult("troops_cap_pct") > first, "캐시를 안 버려 Lv2 가 안 먹었다"
