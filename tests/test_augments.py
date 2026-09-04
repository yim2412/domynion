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


def test_no_real_card_combination_can_reach_the_axis_floor():
    """⚠ **위 테스트의 재료는 카드가 만들 수 없는 값이다.**

    `Modifiers({"cost_vs_player_pct": -5.0})` 는 손으로 넣은 값이고, 실제
    카드로는 그 축에 **한 장밖에** 안 실린다(할인 카드는 축마다 정확히 하나).
    그래서 하한(0.2)은 **한 번도 걸리지 않는다** — 보호 장치는 있지만 잠들어
    있다. 이 사실을 못 박아 두지 않으면 다음 세션이 *"하한이 막고 있다"* 로
    읽는다(2026-09-04 까지 `docs/design.md` 가 실제로 그렇게 적고 있었다).
    """
    per_axis: dict[str, float] = {}
    for aug in AUGMENTS:
        if aug.per_level >= 0:
            continue
        per_axis[aug.field] = per_axis.get(aug.field, 0.0) + value_at(
            aug, C.AUGMENT_MAX_LEVEL)
    assert per_axis, "할인 카드가 하나도 없다 — 이 테스트가 아무것도 안 잰다"
    for field, total in per_axis.items():
        m = Modifiers({field: total})
        assert m.mult(field) > 0.2, (
            f"{field} 이 하한에 닿았다 — 카드 구성이 바뀌었으면 "
            f"`docs/design.md` §3 의 '할인 중첩' 을 다시 재야 한다")


def test_cost_axes_multiply_so_the_per_axis_floor_does_not_bound_the_product():
    """⚠ **하한은 축마다 걸리는데 `attack.py` 는 곱을 쓴다.**

    `cost = mult(cost_vs_player_pct) * terrain_cost * mult(defense_pct)` 라
    각 축이 0.2 위여도 **곱은 0.2 아래로 내려간다**(실측 0.179).

    막지 않았으면 무엇이 일어났을 것인가 — 여기서 지켜야 하는 성질은 *"0.2
    아래로 안 간다"* 가 **아니라** *"0 이하로는 안 간다"* 다. 곱이 양수인 한
    공짜 정복은 없다. 축 간 곱셈은 `docs/design.md` 가 *검토할 선택지 2* 로
    적어 둔 것인데 **이미 그렇게 돌고 있다.**
    """
    m = Modifiers.from_augments({"elite": C.AUGMENT_MAX_LEVEL,
                                 "mountaineers": C.AUGMENT_MAX_LEVEL})
    a = m.mult("cost_vs_player_pct")
    b = m.mult("cost_highland_pct")
    assert a > 0.2 and b > 0.2          # 축마다는 하한 위인데
    assert a * b < 0.2                  # 곱은 아래다 — 하한이 곱을 못 막는다
    assert a * b > 0                    # 지켜지는 성질은 이것뿐이다

    # 중립 경로가 더 싸다. 두 경로를 다 재지 않으면 한쪽만 고쳐도 통과한다.
    n = Modifiers.from_augments({"settlers": C.AUGMENT_MAX_LEVEL,
                                 "mountaineers": C.AUGMENT_MAX_LEVEL})
    assert 0 < n.mult("cost_vs_neutral_pct") * n.mult("cost_highland_pct") < 0.2


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


# --- 계수가 실제 공식에 닿는가 (§ 2단계) ---------------------------------------
#
# ⚠ **축마다 "증강 없이 원본 그대로"와 "있으면 움직인다"를 둘 다 단언한다.**
# 앞의 것이 없으면 헤드리스 기준선(§5.111)이 조용히 달라져도 안 잡힌다.

def _human(**aug):
    p = PlayerState(pid=0, name="P0", kind="human")
    p.augments.update(aug)
    return p


def test_every_declared_field_is_read_by_some_formula():
    """⚠ **선언만 하고 안 읽는 축이 있으면 그 카드는 조용히 아무 일도 안 한다.**
    옛 `naval_range`·`cost_woodland_pct` 가 정확히 그 상태였다(openfront 이식
    뒤 대응물이 사라졌는데 카드는 남아 있었다).

    이 테스트는 **소스에서 축 이름을 찾는다** — 값으로 재는 것은 축마다 따로
    아래에 있고, 여기서는 *배선이 어디에도 없는 축*을 잡는다."""
    import pathlib
    from domynion.core.augments import FIELDS
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "domynion"
    body = "\n".join(f.read_text(encoding="utf-8")
                     for f in src.rglob("*.py") if f.name != "augments.py")
    for field in FIELDS:
        assert f'"{field}"' in body, f"{field} 을 읽는 공식이 없다"


def test_troop_cap_and_growth_move_with_their_cards():
    plain, fertile = _human(), _human(fertile=1)
    assert fertile.max_troops(100) > plain.max_troops(100)
    plain.troops = fertile.troops = 1_000.0
    grow, conscript = _human(), _human(conscript=1)
    grow.troops = conscript.troops = 1_000.0
    assert conscript.troop_increase(100) > grow.troop_increase(100)


def test_growth_still_never_exceeds_the_cap():
    """⚠ **자르기가 마지막이어야 한다.** 증강을 자른 뒤에 곱하면 상한을 넘는다."""
    p = _human(conscript=3, fertile=0)
    cap = p.max_troops(50)
    p.troops = cap - 1.0
    assert p.troops + p.troop_increase(50) <= cap + 1e-6


def test_attack_costs_move_with_their_cards():
    from domynion.core.attack import attack_logic
    gm = GameMap.from_rows(["." * 20] * 10)
    tile = gm.ref(5, 5)
    foe = PlayerState(pid=1, name="P1", kind="nation")
    foe.troops = 10_000.0

    def loss(att, defender):
        return attack_logic(gm, tile, 5_000.0, att, defender,
                            defender_tiles=100, attacker_tiles=100).attacker_loss

    plain = _human()
    assert loss(_human(elite=1), foe) < loss(plain, foe)          # 적 영토 −14%
    assert loss(_human(settlers=1), None) < loss(plain, None)     # 중립 −18%
    # `견고한 방벽` 은 **수비자의** 카드다 — 공격자 손실을 올린다.
    tough = PlayerState(pid=1, name="P1", kind="human")
    tough.troops = 10_000.0
    tough.augments["ramparts"] = 1
    assert loss(plain, tough) > loss(plain, foe)


def test_expand_speed_and_defender_loss_move():
    from domynion.core.attack import attack_logic
    gm = GameMap.from_rows(["." * 20] * 10)
    tile = gm.ref(5, 5)
    foe = PlayerState(pid=1, name="P1", kind="nation")
    foe.troops = 10_000.0

    def res(att):
        return attack_logic(gm, tile, 5_000.0, att, foe,
                            defender_tiles=100, attacker_tiles=100)

    plain = res(_human())
    assert res(_human(forced_march=1)).tiles_used > plain.tiles_used
    assert res(_human(scorched=1)).defender_loss > plain.defender_loss


def test_the_terrain_card_only_helps_on_highland_and_mountain():
    """⚠ **평지에서도 깎이면 그건 그냥 '정복 비용 −32%' 다** — 카드가 둘로
    갈리는 이유가 사라진다."""
    from domynion.core.constants import Terrain
    from domynion.core.attack import attack_logic
    rows = ["." * 20] * 10
    gm = GameMap.from_rows(rows)
    foe = PlayerState(pid=1, name="P1", kind="nation")
    foe.troops = 10_000.0
    plains = gm.ref(5, 5)
    assert gm.terrain_at(plains) is Terrain.PLAINS

    def loss(att, tile):
        return attack_logic(gm, tile, 5_000.0, att, foe,
                            defender_tiles=100, attacker_tiles=100).attacker_loss

    assert loss(_human(mountaineers=1), plains) == pytest.approx(
        loss(_human(), plains)), "평지인데 산악병이 먹혔다"


def test_no_augments_means_the_original_formula_is_untouched():
    """§5.111 기준선이 계속 유효한 근거 — 사람 없는 판은 한 글자도 안 달라진다."""
    from domynion.core.attack import attack_logic
    gm = GameMap.from_rows(["." * 20] * 10)
    tile = gm.ref(5, 5)
    nat = PlayerState(pid=0, name="P0", kind="nation")
    foe = PlayerState(pid=1, name="P1", kind="nation")
    foe.troops = 10_000.0
    for field in ("cost_vs_player_pct", "cost_vs_neutral_pct",
                  "cost_highland_pct", "expand_speed_pct",
                  "defense_pct", "defender_loss_pct",
                  "trade_gold_pct", "boat_loss_pct",
                  "troops_cap_pct", "troops_growth_pct"):
        assert nat.mult(field) == 1.0, f"{field} 배율이 1.0 이 아니다"
    r = attack_logic(gm, tile, 5_000.0, nat, foe,
                     defender_tiles=100, attacker_tiles=100)
    assert r.attacker_loss > 0 and r.tiles_used > 0


def test_the_trade_card_pays_each_side_separately():
    """⚠ **받는 쪽마다 따로 곱한다.** 무역선 하나가 양쪽 항구 주인에게 전액을
    주므로(§5.35), 한 번만 곱하면 증강이 없는 쪽에도 보너스가 가거나 있는 쪽이
    못 받는다.

    ⚠ **엔진을 실제로 돌려야 한다.** 처음엔 `trade_gold()` 를 직접 부르고
    `mult` 를 손으로 곱해 쟀는데, 그건 **테스트가 배선을 흉내 낸 것**이라
    "한 번만 곱한다" 변이가 그대로 통과했다."""
    from domynion.core.naval import TradeShip, trade_gold
    st = state()
    src_p, dst_p = st.players[0], st.players[1]
    src_p.augments["traders"] = 1               # 사람 쪽만 증강이 있다
    # ⚠ **목적지 항구가 실제로 있어야 골드가 간다**(이식 누락 여든셋 —
    # 항구가 부서졌는데 도착해서 골드를 주던 자리). 없으면 이 테스트는
    # 0 을 받고 "증강이 안 먹었다"로 잘못 읽힌다.
    from domynion.core.units import Unit, UnitType
    gm = st.gmap
    for pid, (x, y) in ((0, (5, 5)), (1, (25, 5))):
        u = Unit(UnitType.PORT, pid, tile=gm.ref(x, y))
        gm.owner[u.tile] = pid
        st.players[pid].units.units.append(u)
    t = TradeShip(owner=0, src_port=gm.ref(5, 5), dst_port=gm.ref(25, 5),
                  dst_owner=1, path=[gm.ref(5, 5), gm.ref(25, 5)])
    t.step_i = 1
    t.tiles_travelled = 500
    st.trade_ships.append(t)
    before_src, before_dst = src_p.gold, dst_p.gold
    st._advance_trade()
    got_src, got_dst = src_p.gold - before_src, dst_p.gold - before_dst
    assert got_dst == trade_gold(500), "증강 없는 쪽이 원본 값이 아니다"
    assert got_src > got_dst, "증강이 있는 쪽이 더 못 받았다"


def test_the_landing_card_cuts_the_boat_retreat_malus():
    p = _human(landing=1)
    plain = PlayerState(pid=1, name="P1", kind="nation")
    troops = 1_000.0
    lost_aug = troops * C.BOAT_RETREAT_MALUS_PCT * p.mult("boat_loss_pct")
    lost_plain = troops * C.BOAT_RETREAT_MALUS_PCT * plain.mult("boat_loss_pct")
    assert lost_aug < lost_plain
    assert lost_plain == pytest.approx(troops * C.BOAT_RETREAT_MALUS_PCT)


def test_the_replaced_cards_are_gone():
    """옛 `naval_range`·`cost_woodland_pct` 는 openfront 에 대응물이 없다 —
    남겨 두면 3장 중 하나가 **꽝**이 되고 그 사실이 화면에 안 나온다."""
    from domynion.core.augments import FIELDS
    assert "naval_range" not in FIELDS and "cost_woodland_pct" not in FIELDS
    assert "seafaring" not in AUGMENTS_BY_KEY and "rangers" not in AUGMENTS_BY_KEY
    assert len(AUGMENTS) == 10


def test_the_draft_never_touches_the_game_rng():
    """⚠ **A/B 가 성립하려면 이게 참이어야 한다.** 카드를 뽑을 때마다
    `rng.sample` 이 판의 난수를 한 번 더 쓰면 그 뒤 모든 무작위가 어긋나
    같은 seed 여도 **완전히 다른 판**이 된다.

    2026-09-04 실측: 그 상태로 A/B 를 돌렸더니 증강을 켠 쪽 영토가
    46,594 → 20,983 으로 **줄었다.** 증강이 나쁜 것이 아니라 다른 판이었다."""
    st = state()
    _run(st, C.AUGMENT_FIRST_TICK)
    assert st.augment_offer
    before = st.rng.getstate()
    st.choose_augment(st.augment_offer[0].key)
    _run(st, 3)
    # 드래프트를 열고 고르는 사이 판 rng 가 안 움직였는지 — tick 이 다른 일로
    # rng 를 쓰므로, **드래프트 경로만** 따로 본다.
    st2 = state()
    _run(st2, C.AUGMENT_FIRST_TICK)
    mark = st2.rng.getstate()
    st2._augment_tick()                 # 이미 열려 있다 — 아무것도 안 뽑는다
    st2.choose_augment(st2.augment_offer[0].key)
    st2.augment_next_tick = st2.tick_count
    st2._augment_tick()                 # 여기서 새로 뽑는다
    assert st2.augment_offer, "재료가 뽑기를 안 만든다"
    assert st2.rng.getstate() == mark, "드래프트가 판 rng 를 썼다"


def test_the_draft_rng_exists_whether_augments_are_on_or_off():
    """⚠ **켜든 끄든 같은 횟수만큼 판 rng 를 소비해야 한다.** 조건부로 만들면
    그 한 번이 A/B 를 어긋나게 한다 — 고치려던 버그를 그 자리에 다시 만든다."""
    import random as _r
    from domynion.core.engine import GameState as GS
    a = GS.new(4, _r.Random(5), map_name="world", human=0, size="map16x", bots=4)
    b = GS.new(4, _r.Random(5), map_name="world", human=-1, size="map16x", bots=4)
    assert a._aug_rng is not None and b._aug_rng is not None
    assert a.rng.getstate() == b.rng.getstate(), "판 rng 소비가 다르다"
