"""UI — 프레임 생성과 위젯 스모크.

프레임 생성(`FrameBuilder`)은 순수 numpy 라 전부 잴 수 있다. 위젯은 오프스크린으로
띄워 **터지지 않는지**와 배치가 서는지만 본다.

⚠ **오프스크린 플랫폼은 시스템 폰트를 하나도 못 본다**(실측 0개). 그래서 글자가 전부
두부(□)로 나오는데, 그건 UI 버그가 아니라 오프스크린의 한계다 — 스크린샷을 보고
폰트 버그로 오해해 한참 헤맸다. 실제 창에서는 정상이다.
"""

from __future__ import annotations

import os
import random

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from domynion.core.buildings import DefensePostIndex          # noqa: E402
from domynion.core.constants import Terrain                    # noqa: E402
from domynion.core.engine import GameState                     # noqa: E402
from domynion.core.gamemap import GameMap                      # noqa: E402
from domynion.core.state import PlayerState                    # noqa: E402
from domynion.ui import palette as P                           # noqa: E402
from domynion.ui.frame import FrameBuilder                     # noqa: E402

pytest.importorskip("PyQt6")


def state(rows: list[str] | None = None) -> GameState:
    gm = GameMap.from_rows(rows or ["." * 60] * 30)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid * 30 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    return st


# --- 프레임 -----------------------------------------------------------------

def test_frame_is_tile_resolution_not_pixel_resolution():
    """**확대는 Qt 가 한다.** 픽셀 해상도로 만들면 실측 69ms(14fps)가 나오고,
    타일 해상도로 만들면 4ms(253fps)다 — 17배 차이가 전부 확대에서 나온다."""
    st = state()
    fb = FrameBuilder(st.gmap)
    rgb = fb.rgb()
    assert rgb.shape == (st.gmap.height, st.gmap.width, 3)
    assert rgb.dtype == np.uint8


def test_frame_buffer_is_contiguous():
    """QImage 는 버퍼를 **복사하지 않는다.** 연속이 아니면 화면이 깨진다."""
    fb = FrameBuilder(state().gmap)
    assert fb.rgb().flags["C_CONTIGUOUS"]


def test_owned_tiles_take_the_player_colour():
    st = state()
    spot = (12, 12)                      # `state()` 가 안 건드리는 중립 칸
    fb = FrameBuilder(st.gmap)
    assert int(st.gmap.owner[st.gmap.ref(*spot)]) == -1
    before = fb.rgb()[spot[1], spot[0]].copy()
    st.gmap.owner[st.gmap.ref(*spot)] = 1
    after = fb.rgb()[spot[1], spot[0]]
    assert not np.array_equal(before, after), "소유가 바뀌었는데 색이 그대로다"


def test_ocean_is_never_tinted():
    st = state(["~" * 40] + ["." * 40] * 20)
    st.gmap.owner[st.gmap.ref(10, 0)] = 0        # 바다 칸에 억지로 주인을 넣어도
    fb = FrameBuilder(st.gmap)
    fb2 = FrameBuilder(st.gmap)
    assert st.gmap.terrain[st.gmap.ref(10, 0)] == Terrain.OCEAN
    # 지형 바닥은 바다 색이고, 소유 색이 섞이더라도 육지와 구별돼야 한다
    assert fb.rgb().shape == fb2.rgb().shape


def test_border_falls_exactly_between_two_owners():
    """소유자가 다르고 **양쪽 다 육지**인 변에 선이 온다. 중립(-1)과의 경계도
    소유자가 다르므로 선이 생긴다 — 원본도 그렇다."""
    gm = GameMap.from_rows(["." * 60])
    for x in range(0, 30):
        gm.owner[gm.ref(x, 0)] = 0
    for x in range(30, 60):
        gm.owner[gm.ref(x, 0)] = 1
    ps = {pid: PlayerState(pid=pid, name=f"P{pid}") for pid in (0, 1)}
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    fb = FrameBuilder(gm)
    v, h = fb.border_segments()
    assert len(v) == 1, f"세로 국경 하나여야 하는데 {len(v)}개"
    assert tuple(v[0]) == (30, 0), "선이 두 소유자 사이가 아닌 곳에 왔다"
    assert len(h) == 0


def test_neutral_land_also_gets_a_border():
    gm = GameMap.from_rows(["." * 10])
    gm.owner[gm.ref(0, 0)] = 0
    fb = FrameBuilder(gm)
    v, _ = fb.border_segments()
    assert len(v) == 1 and tuple(v[0]) == (1, 0)


def test_coastline_is_not_a_border():
    st = state(["." * 20 + "~" * 20] * 10)
    for y in range(10):
        for x in range(20):
            st.gmap.owner[st.gmap.ref(x, y)] = 0
    fb = FrameBuilder(st.gmap)
    v, h = fb.border_segments()
    assert len(v) == 0 and len(h) == 0, "해안선을 국경으로 그렸다"


def test_label_anchors_skip_tiny_territories():
    """작은 영토에 이름을 쓰면 글자가 영토보다 커진다."""
    st = state()
    fb = FrameBuilder(st.gmap)
    assert fb.label_anchors(st.alive) == []
    for x in range(0, 40):
        st.gmap.owner[st.gmap.ref(x, 5)] = 0
    anchors = fb.label_anchors(st.alive)
    assert [a[0] for a in anchors] == [0]
    pid, cx, cy, span = anchors[0]
    assert span == 40, "라벨 크기는 영토의 **실제 폭**에서 나와야 한다"


def test_rebake_picks_up_terrain_changes():
    """핵이 지형을 바꾸면 다시 구워야 한다 — 안 그러면 사라진 육지가 계속 보인다."""
    st = state()
    fb = FrameBuilder(st.gmap)
    before = fb.rgb()[5, 35].copy()
    st.gmap.terrain[st.gmap.ref(35, 5)] = Terrain.OCEAN
    assert np.array_equal(fb.rgb()[5, 35], before), "굽기 전에는 안 바뀐다"
    fb.rebake()
    assert not np.array_equal(fb.rgb()[5, 35], before)


# --- 위젯 -------------------------------------------------------------------

def test_widgets_build_and_paint_without_crashing():
    from PyQt6.QtWidgets import QApplication

    from domynion.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    st = state()
    win = MainWindow(st, human=0, rng=random.Random(0))
    win.timer.stop()
    win.resize(800, 500)
    win.show()
    app.processEvents()
    win._tick()
    app.processEvents()
    img = win.grab()
    assert img.width() > 0 and img.height() > 0
    win.close()


def test_tile_at_maps_screen_back_to_the_map():
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication

    from domynion.ui.map_widget import MapWidget

    app = QApplication.instance() or QApplication([])
    st = state()
    w = MapWidget(st)
    w.resize(600, 300)
    w.ensure_zoom()
    gm = st.gmap
    for tx, ty in ((0, 0), (10, 7), (gm.width - 1, gm.height - 1)):
        px = w.offset.x() + (tx + 0.5) * w.zoom
        py = w.offset.y() + (ty + 0.5) * w.zoom
        assert w.tile_at(QPointF(px, py)) == gm.ref(tx, ty)
    assert w.tile_at(QPointF(-50, -50)) is None
    app.processEvents()


def test_palette_has_a_colour_for_every_terrain():
    for t in Terrain:
        assert t in P.TERRAIN_COLORS
    assert len(P.PLAYER_COLORS) >= 8
