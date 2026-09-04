"""건설 미리보기 사거리 원 — 원본 `BuildPreviewController` + `RangeCirclePass`.

원본은 ghost preview(677줄)로 **놓기 전에** 반경을 보여 준다. 우리에게 그 계층이
통째로 없었다. 677줄을 그대로 옮기는 대신 **규칙이 든 부분만** 가져왔다 —
*"고르는 항목의 사거리를 지도에 그린다."*

**왜 규칙인가:** 사거리를 모르고 놓으면 골드를 버린다. 특히 **공장은 역
사거리(110) 안에 있어야 철도가 이어진다** — 그 사실이 화면에 없으면 왜 안
이어지는지 알 방법이 없다.
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from domynion.core import constants as C                      # noqa: E402
from domynion.core.nukes import NUKE_MAGNITUDES, sam_range    # noqa: E402
from domynion.core.units import UnitType                      # noqa: E402
from domynion.ui.overlays import preview_radius               # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# --- 반경 값 ---------------------------------------------------------------

def test_the_four_types_that_show_a_circle():
    """원본 `switch (u.type)` 넷 그대로. 다섯 번째를 넣으면 원본과 어긋난다."""
    assert preview_radius(UnitType.SAM_LAUNCHER) == sam_range(1)
    assert preview_radius(UnitType.DEFENSE_POST) == C.DEFENSE_POST_RANGE
    assert preview_radius(UnitType.FACTORY) == C.TRAIN_STATION_MAX_RANGE
    assert preview_radius(UnitType.ATOM_BOMB) == NUKE_MAGNITUDES[
        UnitType.ATOM_BOMB][1]


def test_types_without_a_range_show_nothing():
    """도시·항구·사일로는 사거리 개념이 없다 — 원을 그리면 거짓 정보다."""
    for ut in (UnitType.CITY, UnitType.PORT, UnitType.MISSILE_SILO):
        assert preview_radius(ut) == 0.0


def test_nukes_use_the_outer_radius_not_the_inner():
    """⚠ 안쪽(전멸) 반경을 그리면 **피해 범위를 과소평가한다.**

    막지 않았으면 무엇이 일어났을 것인가 — 원자탄은 12 대 30 이라 **2.5배** 다."""
    inner, outer = NUKE_MAGNITUDES[UnitType.ATOM_BOMB]
    assert inner != outer, "두 반경이 같아져 이 테스트가 아무것도 안 가른다"
    assert preview_radius(UnitType.ATOM_BOMB) == outer


def test_sam_range_follows_the_level_being_previewed():
    """⚠ **업그레이드는 다음 레벨 반경**을 보여 준다. 지금 레벨을 그리면
    "올리면 얼마나 넓어지나"를 알 수 없다."""
    one = preview_radius(UnitType.SAM_LAUNCHER, 1)
    three = preview_radius(UnitType.SAM_LAUNCHER, 3)
    assert three > one
    assert three == sam_range(3)


# --- 메뉴에 실리는가 -------------------------------------------------------

def _state_with_land():
    from domynion.core.buildings import DefensePostIndex
    from domynion.core.engine import GameState
    from domynion.core.gamemap import GameMap
    from domynion.core.state import PlayerState
    gm = GameMap.from_rows(["." * 60] * 40)
    ps = {0: PlayerState(pid=0, name="P0", kind="human", start=gm.ref(30, 20))}
    for t in range(gm.size):
        gm.owner[t] = 0
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: gm.size}
    st._posts = DefensePostIndex(gm.size)
    ps[0].gold = 10_000_000
    return st, gm


def test_build_items_carry_the_preview():
    """⚠ **계산이 맞아도 메뉴에 안 실리면 화면에 안 뜬다.**"""
    from domynion.ui.actions import build_items
    st, gm = _state_with_land()
    items = build_items(st, 0, gm.ref(30, 20), lambda *_: None)
    with_preview = [i for i in items if i.preview is not None]
    assert with_preview, "사거리 원을 가진 항목이 하나도 없다 — 배선이 끊겼다"
    for i in with_preview:
        _tile, radius = i.preview
        assert radius > 0


def test_items_without_a_range_carry_no_preview():
    from domynion.ui.actions import build_items
    st, gm = _state_with_land()
    items = build_items(st, 0, gm.ref(30, 20), lambda *_: None)
    # 도시는 사거리가 없다 — 라벨로 찾는다(이름 표는 actions 가 들고 있다).
    from domynion.ui.actions import NAMES
    city = [i for i in items if i.label.startswith(NAMES[UnitType.CITY])]
    assert city, "도시 항목이 없다 — 재료가 틀렸다"
    assert all(i.preview is None for i in city)


def test_an_upgrade_item_previews_the_next_level_range():
    """⚠ **재료에 올릴 SAM 이 있어야 이 경로가 돈다.**

    처음에 빈 판으로 재서 *"업그레이드가 지금 레벨 반경을 쓴다"* 변이가
    **살아남았다** — `find_upgrade` 가 늘 None 이라 업그레이드 가지가 한 번도
    안 돌았다. 오늘만 재료 문제가 넷째다."""
    from domynion.core.units import Unit
    from domynion.ui.actions import build_items
    st, gm = _state_with_land()
    tile = gm.ref(30, 20)
    sam = Unit(utype=UnitType.SAM_LAUNCHER, owner=0, tile=tile, level=1)
    sam.ticks_left = 0
    st.players[0].units.units.append(sam)

    items = build_items(st, 0, tile, lambda *_: None)
    up = [i for i in items if "▲Lv2" in i.label]
    assert up, "업그레이드 항목이 없다 — 재료가 틀렸다(`find_upgrade` 가 못 찾았다)"
    _t, radius = up[0].preview
    assert radius == sam_range(2), "올린 뒤 반경(Lv2)을 보여 줘야 한다"
    assert radius != sam_range(1)


# --- 실제로 그려지는가 -----------------------------------------------------

def test_the_circle_is_actually_painted(qapp):
    """⚠ 값도 맞고 메뉴에도 실렸는데 **`paintEvent` 가 안 부르면** 안 보인다."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QImage, QPainter

    from domynion.ui import palette as P
    from domynion.ui.map_widget import MapWidget
    from domynion.ui.radial import Item

    st, gm = _state_with_land()
    w = MapWidget(st)
    w.resize(400, 300)
    w.zoom = 4.0
    w.offset.setX(0.0)
    w.offset.setY(0.0)

    def count() -> int:
        img = QImage(w.size(), QImage.Format.Format_RGB32)
        p = QPainter(img)
        w.render(p)
        p.end()
        return sum(1 for x in range(img.width()) for y in range(img.height())
                   if QImage.pixelColor(img, x, y).getRgb()[:3] == P.PREVIEW_RANGE)

    centre = gm.ref(30, 20)
    plain = Item("사거리 없음", preview=None)
    ranged = Item("사거리 있음", preview=(centre, 10.0))

    w.open_menu(QPointF(200.0, 150.0), centre, [plain, ranged])
    w.menu.hovered = 0
    assert count() == 0, "사거리 없는 항목인데 원이 그려졌다"

    w.menu.hovered = 1
    assert count() > 0, "`_draw_preview_range` 가 배선되지 않았다"
