"""방사형 메뉴와 그 안의 행동들 — **사람이 할 수 있는 일 전부**.

이게 없을 때 사람이 할 수 있는 건 클릭 공격 하나뿐이었다. 골드는 쌓이는데 쓸 방법이
없고 외교도 못 했다 — 그래서 "게임이 아니라 시뮬레이션 같다"가 됐다.

원본은 타일을 클릭하면 방사형 메뉴가 뜬다(`MainRadialMenu.ts`).

**못 하는 항목은 지우지 않고 회색으로 남긴다.** "왜 안 되지"를 알려면 항목이 보이면서
이유가 붙어야 한다 — 아래 테스트들이 그 이유(`hint`)까지 확인한다.
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF                                # noqa: E402

from domynion.core import constants as C            # noqa: E402
from domynion.core.buildings import DefensePostIndex            # noqa: E402
from domynion.core.engine import GameState                      # noqa: E402
from domynion.core.gamemap import GameMap                       # noqa: E402
from domynion.core.nukes import Fallout                         # noqa: E402
from domynion.core.state import PlayerState                     # noqa: E402
from domynion.core.units import Unit, UnitType                  # noqa: E402
from domynion.ui.actions import (BUILDABLE, attack_items,       # noqa: E402
                                 build_items, diplomacy_items, root_items)
from domynion.ui.radial import (RADIUS_INNER, RADIUS_OUTER,     # noqa: E402
                                Item, RadialMenu)


def state(rows: list[str] | None = None) -> GameState:
    gm = GameMap.from_rows(rows or ["." * 60] * 40)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid * 30 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}",
                              kind="human" if pid == 0 else "nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    # 스폰 면역(5초)을 지난 시점에서 시작한다 — 사람은 그전에
    # 사람을 못 친다(원본 `canAttackPlayer`).
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def labels(items):
    return [i.label for i in items]


def by_label(items, label):
    return next(i for i in items if i.label == label)


def noop(_msg):
    return None


# --- 루트 -------------------------------------------------------------------

def test_root_offers_the_four_things_a_player_can_do():
    """원본 루트: 공격 · 건설 · 보트 · 정보(외교). 하나라도 빠지면 그만큼 못 논다."""
    st = state()
    items = root_items(st, 0, st.gmap.ref(40, 20), noop)
    assert labels(items) == ["공격", "건설", "상륙", "외교"]


def test_cannot_attack_own_land_and_it_says_why():
    st = state()
    items = root_items(st, 0, st.gmap.ref(5, 5), noop)     # 내 시작 칸
    atk = by_label(items, "공격")
    assert not atk.enabled
    assert "내 땅" in atk.hint


def test_cannot_attack_an_ally_and_it_says_why():
    st = state()
    st.diplomacy.form(0, 1, tick=0)
    items = root_items(st, 0, st.gmap.ref(35, 5), noop)     # P1 시작 칸
    atk = by_label(items, "공격")
    assert not atk.enabled
    assert "동맹" in atk.hint


def test_diplomacy_is_disabled_on_neutral_ground():
    st = state()
    d = by_label(root_items(st, 0, st.gmap.ref(40, 20), noop), "외교")
    assert not d.enabled and "중립" in d.hint


# --- 건설 -------------------------------------------------------------------

def test_build_menu_lists_every_buildable_plus_warship_and_delete():
    """건물 6종 + 전함 + 철거. 철거가 건설 메뉴에 있는 이유는 짓는 것과 지우는 것이
    같은 결정의 앞뒤이기 때문이다 — 자리를 잘못 잡았을 때 여는 곳이 여기다."""
    st = state()
    items = build_items(st, 0, st.gmap.ref(5, 5), noop)
    assert len(items) == len(BUILDABLE) + 2
    assert "전함" in labels(items)
    assert any(l.startswith("철거") for l in labels(items))


def test_build_shows_cost_and_refuses_without_gold():
    st = state()
    st.players[0].gold = 0
    city = by_label(build_items(st, 0, st.gmap.ref(5, 5), noop), "도시")
    assert not city.enabled
    assert "골드" in city.hint and "125,000" in city.hint


def test_build_actually_places_the_building():
    st = state()
    st.players[0].gold = 1_000_000
    msgs = []
    by_label(build_items(st, 0, st.gmap.ref(5, 5), msgs.append), "도시").action()
    assert st.players[0].units.owned(UnitType.CITY) == 1
    assert st.players[0].gold == 1_000_000 - 125_000
    assert msgs and "도시" in msgs[0]


def test_port_needs_a_shore():
    st = state(["." * 20] * 10)                 # 바다가 없는 지도
    st.players[0].gold = 1_000_000
    port = by_label(build_items(st, 0, st.gmap.ref(5, 5), noop), "항구")
    assert not port.enabled and "자리" in port.hint


def test_warship_needs_a_port():
    st = state()
    st.players[0].gold = 1_000_000
    ship = by_label(build_items(st, 0, st.gmap.ref(5, 5), noop), "전함")
    assert not ship.enabled and "항구" in ship.hint


# --- 핵 ---------------------------------------------------------------------

def test_nukes_appear_in_the_attack_menu_and_need_a_silo():
    st = state()
    st.players[0].gold = 100_000_000
    items = attack_items(st, 0, st.gmap.ref(35, 5), noop)
    for name in ("원폭", "수폭", "MIRV"):
        assert name in labels(items)
    bomb = by_label(items, "원폭")
    assert not bomb.enabled and "사일로" in bomb.hint

    st.players[0].units.units.append(
        Unit(UnitType.MISSILE_SILO, 0, tile=st.gmap.ref(5, 5)))
    assert by_label(attack_items(st, 0, st.gmap.ref(35, 5), noop), "원폭").enabled


# --- 외교 -------------------------------------------------------------------

def test_alliance_request_then_accept():
    st = state()
    msgs = []
    by_label(diplomacy_items(st, 0, 1, msgs.append), "동맹 요청").action()
    assert 1 in st.diplomacy.pending.get(0, set())
    accept = by_label(diplomacy_items(st, 1, 0, msgs.append), "동맹 수락")
    assert accept.enabled
    accept.action()
    assert st.diplomacy.allied(0, 1)


def test_breaking_an_alliance_warns_about_the_traitor_mark():
    """배신에 값이 있다는 것을 **누르기 전에** 알려야 한다."""
    st = state()
    st.diplomacy.form(0, 1, tick=0)
    brk = by_label(diplomacy_items(st, 0, 1, noop), "동맹 파기")
    assert brk.enabled and "배신자" in brk.hint
    brk.action()
    assert not st.diplomacy.allied(0, 1)
    assert st.is_traitor(0)


def test_embargo_toggles_and_donations_move_resources():
    st = state()
    st.players[0].gold = 3_000
    by_label(diplomacy_items(st, 0, 1, noop), "금수").action()
    assert st.diplomacy.embargoed(0, 1)
    assert by_label(diplomacy_items(st, 0, 1, noop), "금수 해제") is not None

    # 기부 버튼은 **친한 사이가 아니면 잠긴다**(§5.63). 원본도 `canDonateGold` 를
    # 클라이언트로 내려보내 잠근다 — 눌러도 안 되는 버튼을 열어 두지 않는다.
    gold = by_label(diplomacy_items(st, 0, 1, noop), "골드 주기")
    assert not gold.enabled
    gold.action()
    assert st.players[1].gold == 0, "잠긴 버튼은 눌러도 안 나간다"

    st.diplomacy.form(0, 1, st.tick_count)
    gold = by_label(diplomacy_items(st, 0, 1, noop), "골드 주기")
    assert gold.enabled
    gold.action()
    # ⚠ **기대값을 상수로 만들면 안 된다.** `3_000 // C.DONATION_DIVISOR` 로 쓰면
    # 상수를 4 로 되돌려도 양쪽이 같이 움직여 통과한다 — 실제로 변이가 살아남았다.
    # 한 번에 **1/3** 이 나간다(원본 `DonateGoldExecution` 의 `gold()/3n`, §5.90).
    assert st.players[1].gold == 1_000, "3,000 의 1/3 이 아니다"
    assert C.DONATION_DIVISOR == 3

    # 같은 상대에게 연달아는 안 된다 — 쿨다운 10초.
    assert not by_label(diplomacy_items(st, 0, 1, noop), "병력 주기").enabled


# --- 메뉴 자체 --------------------------------------------------------------

def test_slice_hit_testing():
    items = [Item(str(i)) for i in range(4)]
    m = RadialMenu(centre=QPointF(100, 100), items=items, tile=0)
    r = (RADIUS_INNER + RADIUS_OUTER) / 2
    assert m.slice_at(QPointF(100, 100 - r)) == 0        # 12시 = 첫 칸
    assert m.slice_at(QPointF(100 + r, 100)) == 1        # 3시
    assert m.slice_at(QPointF(100, 100 + r)) == 2        # 6시
    assert m.slice_at(QPointF(100 - r, 100)) == 3        # 9시
    assert m.slice_at(QPointF(100, 100)) == -1           # 가운데
    assert m.slice_at(QPointF(100, 100 - RADIUS_OUTER - 20)) == -1   # 바깥


def test_disabled_items_do_nothing_and_keep_the_menu_open():
    fired = []
    m = RadialMenu(centre=QPointF(0, 0),
                   items=[Item("x", action=lambda: fired.append(1), enabled=False)],
                   tile=0)
    r = (RADIUS_INNER + RADIUS_OUTER) / 2
    assert m.activate(QPointF(0, -r)) is False, "닫히면 안 된다"
    assert not fired


def test_submenu_and_back():
    leaf = [Item("잎")]
    m = RadialMenu(centre=QPointF(0, 0),
                   items=[Item("가지", submenu=lambda: leaf)], tile=0)
    r = (RADIUS_INNER + RADIUS_OUTER) / 2
    assert m.activate(QPointF(0, -r)) is False
    assert labels(m.items) == ["잎"]
    assert m.activate(QPointF(0, 0)) is False, "가운데 = 뒤로"
    assert labels(m.items) == ["가지"]
    assert m.activate(QPointF(0, 0)) is True, "최상위에서 가운데 = 닫기"


def test_action_closes_the_menu():
    fired = []
    m = RadialMenu(centre=QPointF(0, 0),
                   items=[Item("x", action=lambda: fired.append(1))], tile=0)
    r = (RADIUS_INNER + RADIUS_OUTER) / 2
    assert m.activate(QPointF(0, -r)) is True
    assert fired == [1]


# --- 철거 -------------------------------------------------------------------

def _delete_row(items):
    return next(i for i in items if i.label.startswith("철거"))


def _a_city(st, x=5, y=5):
    st.players[0].gold = 10_000_000
    st.tick_count = C.DELETE_UNIT_COOLDOWN_TICKS
    u = st.build(0, UnitType.CITY, st.gmap.ref(x, y))
    assert u is not None
    while u.under_construction:
        st.tick()
    return u


def test_delete_is_greyed_out_where_there_is_no_building():
    """회색으로 두고 이유를 붙인다 — 지우면 "왜 없지"가 된다."""
    st = state()
    item = _delete_row(build_items(st, 0, st.gmap.ref(50, 30), noop))
    assert not item.enabled and "건물이 없다" in item.hint


def test_delete_finds_the_building_you_clicked_near():
    st = state()
    u = _a_city(st)
    item = _delete_row(build_items(st, 0, st.gmap.ref(7, 6), noop))
    assert item.enabled and "도시" in item.label


def test_a_far_away_building_is_not_picked_up():
    """반경 밖의 건물을 집으면 엉뚱한 것을 지운다."""
    st = state()
    _a_city(st, x=5, y=5)
    item = _delete_row(build_items(st, 0, st.gmap.ref(40, 30), noop))
    assert not item.enabled


def test_delete_hint_says_the_gold_is_gone():
    """환불이 있다고 착각하면 되돌릴 수 없는 결정을 가볍게 내린다."""
    st = state()
    _a_city(st)
    item = _delete_row(build_items(st, 0, st.gmap.ref(5, 5), noop))
    assert "골드는 안 돌아온다" in item.hint


def test_delete_actually_marks_it():
    st = state()
    u = _a_city(st)
    _delete_row(build_items(st, 0, st.gmap.ref(5, 5), noop)).action()
    assert u.marked_for_deletion


def test_an_already_marked_building_shows_the_countdown():
    st = state()
    u = _a_city(st)
    st.delete_unit(0, u)
    item = _delete_row(build_items(st, 0, st.gmap.ref(5, 5), noop))
    assert not item.enabled and "이미 철거 예정" in item.hint


def test_cooldown_shows_up_as_a_reason():
    st = state()
    # 건물 둘을 15칸 떨어뜨려 지으려면 땅이 그만큼 있어야 한다.
    for x in range(0, 30):
        for y in range(0, 30):
            st.gmap.owner[st.gmap.ref(x, y)] = 0
    st._counts[0] = 900
    u = _a_city(st)
    st.delete_unit(0, u)
    v = st.build(0, UnitType.CITY, st.gmap.ref(25, 25))
    assert v is not None
    item = _delete_row(build_items(st, 0, st.gmap.ref(25, 25), noop))
    assert not item.enabled and "초에 하나씩만" in item.hint


# --- 공격 가능 판정이 타일을 본다 (백셋) --------------------------------------
#
# `can_attack`(= `canAttackPlayer`) 는 **상대만** 본다. 원본 `canAttack(tile)` 은
# 그 위에 셋을 더 얹는다: 바다·통행불가 · 남의 땅이면 국경 · 중립이면 이어진
# 무주지가 내 땅에 닿아야. 규칙(`Attack.launch`)은 이미 맞았고 **화면만** 틀렸다 —
# 바다 칸을 눌러도 "치기"가 켜져 있고 힌트는 "보낼 병력 N" 이라고 말했다.

def _sea_map() -> GameState:
    """왼쪽 대륙(내 땅) · 바다 · 오른쪽 대륙(P1). **국경을 안 맞댄 배치다.**

    ⚠ 폭을 40으로 두는 이유가 있다 — `state()` 가 시작 칸을 (5,5)·(35,5)에
    박는다. 좁은 지도를 쓰면 그 칸이 **바다 위나 지도 밖**에 떨어지고(`ref` 가
    조용히 감긴다) 재려던 것과 다른 것을 재게 된다.
    양쪽 대륙을 바다에 닿게 둬야 "국경이 없다"를 재지 "해안이 없다"를 안 잰다."""
    st = state(["." * 15 + "~" * 10 + "." * 15] * 10)
    gm = st.gmap
    for y in range(10):
        for x in range(15):
            gm.owner[gm.ref(x, y)] = 0
        for x in range(25, 40):
            gm.owner[gm.ref(x, y)] = 1
    # 양쪽 대륙에 **내륙 중립**을 한 칸씩 남긴다. 대륙 가장자리를 비우면 그쪽이
    # 바다에 못 닿아 배가 아예 안 뜬다 — 재려는 것은 국경이지 해안이 아니다.
    gm.owner[gm.ref(7, 9)] = -1             # 내 대륙의 중립
    gm.owner[gm.ref(32, 9)] = -1            # 건너편 대륙의 중립
    st._counts = {0: 149, 1: 149}
    return st


def test_cannot_attack_the_sea_and_it_says_why():
    """⚠ 이게 백셋의 본체다. 바다에는 국경도 확장도 없다."""
    st = _sea_map()
    sea = st.gmap.ref(20, 5)
    assert not st.can_attack_tile(0, sea)
    hit = by_label(attack_items(st, 0, sea, noop), "중립 치기")
    assert not hit.enabled
    assert "바다" in hit.hint


def test_cannot_attack_impassable_land():
    st = state(["....#....."] * 10)
    st.gmap.owner[st.gmap.ref(0, 0)] = 0
    assert not st.can_attack_tile(0, st.gmap.ref(4, 5))


def test_cannot_attack_a_player_across_the_water():
    """국경을 안 맞대면 못 친다 — 배로 가야 한다. 그 문구까지 확인한다."""
    st = _sea_map()
    far = st.gmap.ref(35, 5)
    assert not st.can_attack_tile(0, far)
    hit = by_label(attack_items(st, 0, far, noop), "P1 치기")
    assert not hit.enabled
    assert "국경" in hit.hint and "상륙" in hit.hint


def test_can_attack_a_player_we_share_a_border_with():
    st = state()
    gm = st.gmap
    for y in range(5, 8):
        gm.owner[gm.ref(10, y)] = 0
        gm.owner[gm.ref(11, y)] = 1
    st._counts = {0: 4, 1: 4}
    assert st.shares_border_with(0, 1)
    assert st.can_attack_tile(0, gm.ref(11, 6))
    hit = by_label(attack_items(st, 0, gm.ref(11, 6), noop), "P1 치기")
    assert hit.enabled and "보낼 병력" in hit.hint


def test_neutral_must_be_connected_to_my_land():
    """중립은 국경이 아니라 **이어진 덩어리**로 판정한다 — 그게 확장의 규칙이다."""
    st = _sea_map()
    gm = st.gmap
    mine_side = gm.ref(7, 9)            # 내 땅 옆의 중립 (같은 대륙)
    other_side = gm.ref(32, 9)          # 바다 건너 중립
    assert st.can_attack_tile(0, mine_side)
    assert not st.can_attack_tile(0, other_side)
    hit = by_label(attack_items(st, 0, other_side, noop), "중립 치기")
    assert not hit.enabled and "이어지지 않은" in hit.hint


def test_neutral_beyond_the_reach_is_out_even_when_connected():
    """`manhattanDistFN(tile, 200)` — 이어져 있어도 200칸 밖은 안 본다."""
    st = state(["." * 260] * 8)
    st.gmap.owner[st.gmap.ref(0, 1)] = 0
    st._counts = {0: 1, 1: 0}
    assert st.can_attack_tile(0, st.gmap.ref(150, 1))
    assert not st.can_attack_tile(0, st.gmap.ref(259, 1))


def test_the_rule_matches_what_launch_actually_does():
    """⚠ **화면과 규칙이 같은 답을 해야 한다.** 어긋난 것이 이 누락의 정체였다.

    ⚠ 중립 칸은 여기서 못 잰다 — `launch_attack` 은 **소유자**를 받으므로, 판
    어딘가에 붙을 중립이 하나라도 있으면 성공한다. 화면이 칸 단위인데 규칙이
    소유자 단위라는 것, 그 간극 자체가 이 누락이 생긴 이유다."""
    st = _sea_map()
    far = st.gmap.ref(35, 5)
    assert not st.can_attack_tile(0, far)
    assert st.launch_attack(0, 1) is None


# --- 상륙은 다른 규칙이다 (`canBuildTransportShip`) ---------------------------

def test_boat_can_reach_across_the_water_even_without_a_border():
    """배는 국경을 맞댈 필요가 없다 — **그게 배의 존재 이유다.**"""
    st = _sea_map()
    far = st.gmap.ref(35, 5)
    assert not st.can_attack_tile(0, far)      # 육상으로는 못 친다
    assert st.can_send_boat(0, far)            # 배로는 간다
    boat = by_label(root_items(st, 0, far, noop), "상륙")
    assert boat.enabled and "배로 병력" in boat.hint


def test_boat_says_when_every_ship_is_out():
    """조용한 실패 중 가장 헷갈리는 자리다 — 3척이 다 나가 있으면 클릭이 사라진다."""
    st = _sea_map()
    far = st.gmap.ref(35, 5)
    for _ in range(C.BOAT_MAX_NUMBER):
        assert st.send_boat(0, far) is not None
    boat = by_label(root_items(st, 0, far, noop), "상륙")
    assert not boat.enabled
    assert f"{C.BOAT_MAX_NUMBER}척" in boat.hint


def test_boat_refuses_the_open_sea():
    st = _sea_map()
    boat = by_label(root_items(st, 0, st.gmap.ref(20, 5), noop), "상륙")
    assert not boat.enabled and "바다" in boat.hint


# --- 외교 패널 대조: 원본 `PlayerPanel` 이 보여 주는 것 (§5.103) ---------------
#
# 관계 알약 · 상대의 동맹 목록 · 배신 횟수 · 무역 상태 넷. 앞의 셋은 **판단의
# 재료**라 없으면 사람이 눈으로 셀 방법이 없고, 관계는 **거짓 재료**였다.

def _kinds(st, pid, kind):
    """`kind` 는 생성자에서 `is_bot` 을 계산한다 — 만든 뒤 대입하면 안 먹는다."""
    old = st.players[pid]
    st.players[pid] = PlayerState(pid=pid, name=old.name, start=old.start,
                                  kind=kind)
    return st.players[pid]


def _relation_row(st, me=0, target=1):
    return next(i for i in diplomacy_items(st, me, target, noop)
                if i.label.startswith("관계"))


def test_relation_is_shown_for_a_nation():
    """나라에게만 관계가 뜻이 있다 — 그때는 값이 그대로 보여야 한다."""
    st = state()
    row = _relation_row(st)
    assert row.label != "관계 · —"
    assert "상대가 나를 보는 눈" in row.hint


def test_a_bot_never_shows_a_relation_because_it_ignores_one():
    """⚠ **막지 않았으면 무엇이 일어났을 것인가** — 봇에게 "우호라 동맹 요청을
    대체로 받아 준다"고 적히는데, `tribe.py` 는 관계를 **아예 안 본다.**
    화면이 없는 규칙을 있다고 말하는 자리였다."""
    st = state()
    _kinds(st, 1, "bot")
    row = _relation_row(st)
    assert row.label == "관계 · —"
    assert "전부 받는다" in row.hint
    assert "대체로 받아 준다" not in row.hint


def test_a_traitor_never_shows_a_relation_because_it_is_overridden():
    """배신자는 관계가 우호여도 90% 거절한다 — 관계를 띄우면 거짓말이 된다."""
    st = state()
    st.diplomacy.traitor_since[1] = st.tick_count
    row = _relation_row(st)
    assert row.label == "관계 · —"
    assert "90%" in row.hint


def test_an_ally_never_shows_a_relation_because_it_is_moot():
    st = state()
    st.diplomacy.form(0, 1, st.tick_count)
    row = _relation_row(st)
    assert row.label == "관계 · —"
    assert "이미 동맹" in row.hint


def test_the_panel_lists_who_the_target_is_allied_with_and_for_how_long():
    """칠 상대를 고를 때 **누가 끼어드는지**가 먼저다."""
    st = state()
    st.players[2] = PlayerState(pid=2, name="P2", start=st.gmap.ref(50, 5),
                                kind="nation")
    st.diplomacy.form(1, 2, st.tick_count)
    row = next(i for i in diplomacy_items(st, 0, 1, noop)
               if i.label.startswith("동맹국"))
    assert row.label == "동맹국 1"
    assert "P2" in row.hint


def test_an_alliance_about_to_expire_is_marked():
    """30~60초 남은 동맹은 계산에서 빼도 된다 — 원본은 색, 우리는 ⚠."""
    st = state()
    st.players[2] = PlayerState(pid=2, name="P2", start=st.gmap.ref(50, 5),
                                kind="nation")
    al = st.diplomacy.form(1, 2, st.tick_count)
    al.expires_at = st.tick_count + int(20 / C.TICK_DT)     # 20초 남았다
    row = next(i for i in diplomacy_items(st, 0, 1, noop)
               if i.label.startswith("동맹국"))
    assert "⚠" in row.hint
    # 막지 않았으면: 5분짜리 동맹과 20초짜리가 화면에서 똑같아 보인다.
    al.expires_at = st.tick_count + C.ALLIANCE_DURATION_TICKS
    row2 = next(i for i in diplomacy_items(st, 0, 1, noop)
                if i.label.startswith("동맹국"))
    assert "⚠" not in row2.hint


def test_no_alliances_says_so_instead_of_showing_an_empty_row():
    st = state()
    row = next(i for i in diplomacy_items(st, 0, 1, noop)
               if i.label.startswith("동맹국"))
    assert row.label == "동맹국 0" and "끼어들 상대가 없다" in row.hint


def test_betrayal_count_is_visible_during_the_game_not_only_at_the_end():
    st = state()
    st.diplomacy.form(0, 1, st.tick_count)
    st.diplomacy.break_alliance(1, 0, st.tick_count)
    row = next(i for i in diplomacy_items(st, 0, 1, noop)
               if i.label.startswith("배신 "))
    assert row.label == "배신 1회"
    assert "연장이 위험" in row.hint


def test_an_embargo_the_other_side_placed_is_shown_because_i_cannot_lift_it():
    """상대가 건 금수는 내 버튼으로 못 푼다 — 그래서 **알림**이어야 한다.
    이게 없으면 무역선이 왜 안 오는지가 화면 어디에도 없다."""
    st = state()
    row = by_label(diplomacy_items(st, 0, 1, noop), "금수")
    assert "그쪽도" not in row.hint
    st.diplomacy.start_embargo(1, 0, st.tick_count)
    row = by_label(diplomacy_items(st, 0, 1, noop), "금수")
    assert "그쪽도 나를 막고 있다" in row.hint
