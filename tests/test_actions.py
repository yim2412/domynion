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
    st.players[0].gold = 4_000
    by_label(diplomacy_items(st, 0, 1, noop), "금수").action()
    assert st.diplomacy.embargoed(0, 1)
    assert by_label(diplomacy_items(st, 0, 1, noop), "금수 해제") is not None

    by_label(diplomacy_items(st, 0, 1, noop), "골드 주기").action()
    assert st.players[1].gold == 1_000


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
