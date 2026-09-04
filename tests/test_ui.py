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
    assert fb.terrain_rgb.shape == (st.gmap.height, st.gmap.width, 3)
    assert fb.owner_rgba().shape == (st.gmap.height, st.gmap.width, 4)
    assert fb.terrain_rgb.dtype == np.uint8


def test_terrain_layer_is_baked_once_and_owner_layer_is_per_frame():
    """지형은 판 내내 **같은 배열**이다. 매 프레임 다시 만들면 원본 크기 지도에서
    17fps 가 된다(실측) — 오버레이만 만들면 58fps."""
    st = state()
    fb = FrameBuilder(st.gmap)
    first = fb.terrain_rgb
    st.gmap.owner[st.gmap.ref(12, 12)] = 1
    fb.owner_rgba()
    assert fb.terrain_rgb is first, "소유가 바뀌었는데 지형을 다시 구웠다"


def test_frame_buffers_are_contiguous():
    """QImage 는 버퍼를 **복사하지 않는다.** 연속이 아니면 화면이 깨진다."""
    fb = FrameBuilder(state().gmap)
    assert fb.terrain_rgb.flags["C_CONTIGUOUS"]
    assert fb.owner_rgba().flags["C_CONTIGUOUS"]


def test_owned_tiles_take_the_player_colour():
    st = state()
    spot = (12, 12)                      # `state()` 가 안 건드리는 중립 칸
    fb = FrameBuilder(st.gmap)
    assert int(st.gmap.owner[st.gmap.ref(*spot)]) == -1
    assert fb.owner_rgba()[spot[1], spot[0], 3] == 0, "중립은 투명해야 한다"
    st.gmap.owner[st.gmap.ref(*spot)] = 1
    px = fb.owner_rgba()[spot[1], spot[0]]
    assert px[3] > 0, "소유가 바뀌었는데 투명하다"
    assert tuple(px[:3]) == P.player_color(1)


def test_neutral_is_fully_transparent_so_terrain_shows_through():
    """중립을 불투명하게 칠하면 지형이 안 보인다 — 지도가 통째로 한 색이 된다."""
    st = state()
    fb = FrameBuilder(st.gmap)
    ov = fb.owner_rgba()
    neutral = st.gmap.owner.reshape(st.gmap.height, st.gmap.width) < 0
    assert (ov[..., 3][neutral] == 0).all()


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
    pid, cx, cy, rw, rh = anchors[0]
    # ⚠ **폭이 아니라 "이름이 앉을 사각형"이다**(§5.97). 예전에는 경계상자 폭
    # (40)을 그대로 썼는데, 그 값은 영토가 아니라 **영토를 감싼 상자**의 크기라
    # 초승달 모양에서는 실제로 쓸 수 있는 자리보다 훨씬 크게 나온다.
    assert rw > 0 and rh > 0
    assert rw <= 40, f"자리가 영토 상자보다 넓다: {rw}"


def test_the_name_lands_inside_the_territory_not_at_its_centre_of_mass():
    """⚠ **무게중심은 영토 밖에 떨어질 수 있다**(§5.97). 초승달 모양이나 해협
    양쪽에 걸친 나라는 이름이 바다나 남의 땅 위에 뜬다. 원본은 그래서 가장 큰
    내접 사각형을 찾아 거기에 놓는다.

    막지 않았으면: 대부분의 나라는 볼록해서 무게중심도 안쪽에 떨어진다 —
    **오목한 재료가 없으면 아무것도 안 재는 테스트**가 된다. 그래서 ㄷ 모양으로
    만들고, 무게중심이 실제로 밖인지 먼저 단언한다."""
    gm = GameMap.from_rows(["." * 60] * 30)
    ps = {0: PlayerState(pid=0, name="P0", kind="nation", start=gm.ref(2, 2))}
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1}
    st._posts = DefensePostIndex(gm.size)

    # ㄷ 모양 — 가운데가 비어 무게중심이 그 구멍에 떨어진다.
    for y in range(4, 24):
        for x in (4, 5, 6, 7):
            gm.owner[gm.ref(x, y)] = 0
        for x in range(4, 24):
            if y in (4, 5, 22, 23):
                gm.owner[gm.ref(x, y)] = 0

    ys, xs = np.nonzero(gm.owner.reshape(30, 60) == 0)
    mx, my = xs.mean(), ys.mean()
    assert gm.owner[gm.ref(int(round(mx)), int(round(my)))] != 0,         "재료 확인: 무게중심이 영토 안이면 이 테스트는 아무것도 안 잰다"

    fb = FrameBuilder(gm)
    (_, cx, cy, rw, rh), = fb.label_anchors(st.alive)
    assert gm.owner[gm.ref(int(cx), int(cy))] == 0,         f"이름이 영토 밖({cx:.0f}, {cy:.0f})에 앉았다"


def test_rebake_picks_up_terrain_changes():
    """핵이 지형을 바꾸면 다시 구워야 한다 — 안 그러면 사라진 육지가 계속 보인다."""
    st = state()
    fb = FrameBuilder(st.gmap)
    before = fb.terrain_rgb[5, 35].copy()
    st.gmap.terrain[st.gmap.ref(35, 5)] = Terrain.OCEAN
    assert np.array_equal(fb.terrain_rgb[5, 35], before), "굽기 전에는 안 바뀐다"
    fb.rebake()
    assert not np.array_equal(fb.terrain_rgb[5, 35], before)


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


def test_every_nation_on_the_default_map_gets_its_own_colour():
    """⚠ **이 테스트가 `len(PLAYER_COLORS) >= 8` 이었다**(§5.95). v0.1 이 4~6명일
    때는 맞았는데, §5.51 에서 나라 72 + 봇 400 으로 올린 뒤에도 그대로 남아
    **색 여덟 개를 통과시키고 있었다.** 통과는 증거가 아니다 — 문턱이 규모를
    안 따라갔다.

    막지 않았으면: 472명이 도는 지도에서 평균 59명이 같은 색이라, 맞닿은 두
    나라가 한 덩어리로 보인다.

    ⚠ **개수가 아니라 겹침을 잰다.** `len(...) >= 72` 로 두면 나라 통이 49개인
    원본을 거짓으로 떨어뜨린다 — 원본도 모자라면 예비 통으로 넘어간다."""
    nations = 72                      # world manifest 의 나라 수 (`ui/app.py`)
    got = {P.player_color(pid, "nation") for pid in range(nations)}
    assert len(got) == nations, f"{nations}명 중 색이 {len(got)}가지뿐이다"


def test_the_three_kinds_draw_from_different_pools():
    """원본은 색으로 종류를 나눈다 — 봇은 채도를 뺀 회색빛, 사람은 선명하다.
    지도만 봐도 *누가 사람인지* 알 수 있어야 한다.

    막지 않았으면: 셋을 한 통에서 뽑아도 색은 나오므로 눈에 안 띈다."""
    got = {P.player_color(0, k) for k in ("nation", "bot", "human")}
    assert len(got) == 3, f"종류가 달라도 같은 색이다: {got}"

    def sat(c):
        return max(c) - min(c)

    assert sat(P.player_color(0, "bot")) < sat(P.player_color(0, "human")),         "봇이 사람보다 선명하다 — 통이 뒤바뀌었다"


# --- 순환 · LOD · 카메라 ------------------------------------------------------

def test_map_wraps_horizontally_on_screen_only():
    """오른쪽으로 계속 가면 왼쪽이 나온다.

    ⚠ **화면만 순환한다.** 게임 규칙은 x 경계를 안 넘는다(원본 `neighbors4`).
    여기를 규칙까지 순환시키면 이식이 깨진다 — 아래 두 번째 단언이 그것을 지킨다."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication

    from domynion.ui.map_widget import MapWidget

    app = QApplication.instance() or QApplication([])
    st = state()
    w = MapWidget(st)
    w.resize(600, 300)
    w.ensure_zoom()
    gm = st.gmap

    # 지도 폭만큼 오른쪽으로 간 자리는 같은 타일을 가리킨다
    left = w.tile_at(QPointF(w.offset.x() + 0.5 * w.zoom, w.offset.y() + 0.5 * w.zoom))
    wrapped = w.tile_at(QPointF(w.offset.x() + w.world_w + 0.5 * w.zoom,
                                w.offset.y() + 0.5 * w.zoom))
    assert left == wrapped == gm.ref(0, 0)

    # 규칙은 순환하지 않는다
    right_edge = gm.ref(gm.width - 1, 0)
    assert gm.ref(0, 0) not in gm.neighbors(right_edge), "게임 규칙이 순환해 버렸다"
    app.processEvents()


def test_wrapped_copies_cover_the_widget():
    from PyQt6.QtWidgets import QApplication

    from domynion.ui.map_widget import MapWidget

    app = QApplication.instance() or QApplication([])
    w = MapWidget(state())
    w.resize(800, 300)
    w.ensure_zoom()
    w.zoom = w.width() / w.state.gmap.width / 3      # 화면에 세 바퀴가 들어가게
    xs = w._tiles_x()
    assert len(xs) >= 3, f"화면을 못 덮는다: {xs}"
    assert xs[0] <= 0 < xs[0] + w.world_w, "첫 사본이 화면 왼쪽을 못 덮는다"
    assert xs[-1] < w.width() <= xs[-1] + w.world_w, "마지막 사본이 오른쪽을 못 덮는다"
    app.processEvents()


def test_lod_gets_coarser_as_you_zoom_out():
    """줌 아웃하면 성기게 뽑는다 — 2000×1000 에서 17.0ms → 4.3ms(실측).

    막지 않았으면: 원본 크기 지도가 17fps 로 떨어진다."""
    from PyQt6.QtWidgets import QApplication

    from domynion.ui.map_widget import MapWidget

    app = QApplication.instance() or QApplication([])
    w = MapWidget(state())
    w.zoom = 2.0
    assert w._wanted_stride() == 1, "확대했는데 성기게 뽑는다"
    w.zoom = 0.5
    assert w._wanted_stride() == 2
    w.zoom = 0.8
    assert w._wanted_stride() == 2, "화면 픽셀보다 촘촘히 뽑아 봐야 버려진다"
    w.zoom = 0.05
    assert w._wanted_stride() <= 6, "무한정 성겨지면 안 된다"
    app.processEvents()


def test_owner_layer_shrinks_with_stride():
    st = state()
    fb = FrameBuilder(st.gmap)
    full = fb.owner_rgba(1).shape
    half = fb.owner_rgba(2).shape
    assert half[0] * 2 >= full[0] and half[1] * 2 >= full[1]
    assert half[0] < full[0] and half[1] < full[1]


def test_camera_accelerates_and_decelerates():
    """키를 누른 순간 최고 속도로 튀지 않고, 뗀 순간 멈추지도 않는다."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from domynion.ui.map_widget import PAN_SPEED, MapWidget

    app = QApplication.instance() or QApplication([])
    w = MapWidget(state())
    w.resize(600, 300)
    w.ensure_zoom()

    w._keys.add(Qt.Key.Key_D.value)
    w._step_camera()
    first = abs(w._vel.x())
    assert 0 < first < PAN_SPEED, "한 프레임 만에 최고 속도가 됐다"
    for _ in range(60):
        w._step_camera()
    assert abs(w._vel.x()) > first, "가속이 안 된다"

    w._keys.clear()
    w._step_camera()
    assert abs(w._vel.x()) > 0, "키를 떼자마자 멈췄다"
    for _ in range(120):
        w._step_camera()
    assert abs(w._vel.x()) < 1.0, "영원히 미끄러진다"
    app.processEvents()


def test_vertical_does_not_wrap():
    """세로로 넘어가면 빈 화면만 남는다 — 가로만 순환한다."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QPointF

    from domynion.ui.map_widget import MapWidget

    app = QApplication.instance() or QApplication([])
    w = MapWidget(state())
    w.resize(600, 300)
    w.ensure_zoom()
    w.offset = QPointF(w.offset.x(), 99_999)
    w._clamp_vertical()
    assert w.offset.y() <= max(0.0, 300 - w.state.gmap.height * w.zoom) + 1e-6
    app.processEvents()


# --- 유닛 표시 ---------------------------------------------------------------

def test_every_structure_has_a_glyph():
    """건물은 **모양으로** 종류를 구분한다 — 색은 이미 소유자를 뜻한다.

    글리프가 빠진 종류는 지도에서 통째로 안 보인다. 그게 "시뮬레이션 같다"의 원인이었다."""
    from domynion.core.units import STRUCTURES
    for ut in STRUCTURES:
        assert ut.value in P.UNIT_GLYPH, f"{ut.value} 이 지도에 안 그려진다"


def test_units_are_drawn_without_crashing():
    from PyQt6.QtWidgets import QApplication

    from domynion.core.units import Unit, UnitType
    from domynion.ui.map_widget import MapWidget

    app = QApplication.instance() or QApplication([])
    st = state()
    p0 = st.players[0]
    for i, ut in enumerate((UnitType.CITY, UnitType.PORT, UnitType.FACTORY,
                            UnitType.DEFENSE_POST, UnitType.MISSILE_SILO,
                            UnitType.SAM_LAUNCHER)):
        p0.units.units.append(Unit(ut, 0, tile=st.gmap.ref(5 + i * 3, 5), level=i + 1))
    w = MapWidget(st)
    w.resize(600, 300)
    w.zoom = 3.0                      # UNIT_MIN_ZOOM 위여야 건물이 그려진다
    w.refresh()
    img = w.grab()
    assert img.width() > 0
    app.processEvents()


def test_the_overlay_colours_a_bot_differently_from_a_nation_on_the_same_id():
    """⚠ **배선이다**(§5.95). `player_color` 가 종류를 봐도 프레임의 표가 안 보면
    화면은 그대로다 — 표는 `owner + 1` 로만 찾으므로 종류가 안 들어간다.

    막지 않았으면: 지도에서 봇과 나라가 같은 색으로 나와, 누가 사람인지도
    누가 나라인지도 알 수 없다."""
    st = state()
    spot = (12, 12)
    st.gmap.owner[st.gmap.ref(*spot)] = 1

    as_nation = FrameBuilder(st.gmap, kinds={1: "nation"})
    as_bot = FrameBuilder(st.gmap, kinds={1: "bot"})
    a = tuple(as_nation.owner_rgba()[spot[1], spot[0]][:3])
    b = tuple(as_bot.owner_rgba()[spot[1], spot[0]][:3])
    assert a == P.player_color(1, "nation")
    assert b == P.player_color(1, "bot")
    assert a != b, "종류가 다른데 같은 색이다"


def test_a_high_player_id_does_not_fall_off_the_colour_table():
    """⚠ 예전 코드가 `len(PLAYER_COLORS) < 64` 일 때만 감쌌다 — **조건이
    거꾸로**라, 색을 64개 넘게 늘리면 감싸기가 꺼져 pid 가 표를 넘는 순간
    IndexError 였다(§5.95).

    막지 않았으면: 색을 늘린 그 순간 원본 크기 판이 첫 프레임에서 죽는다."""
    st = state()
    spot = (12, 12)
    st.gmap.owner[st.gmap.ref(*spot)] = 470       # 나라 72 + 봇 400 짜리 판
    fb = FrameBuilder(st.gmap)
    px = fb.owner_rgba()[spot[1], spot[0]]
    assert px[3] > 0 and tuple(px[:3]) == P.player_color(470)


def test_the_map_widget_hands_the_frame_builder_the_player_kinds():
    """⚠ **배선의 마지막 한 칸**(§5.95). 팔레트도 맞고 프레임 표도 종류를 받는데,
    지도 위젯이 그걸 안 넘기면 판 전체가 나라 색으로만 그려진다.

    막지 않았으면: 위 두 테스트가 다 통과하면서도 실제 화면에서는 봇 400명이
    나라 색을 쓴다. 변이가 살아남아서 알았다."""
    from PyQt6.QtWidgets import QApplication

    from domynion.ui.map_widget import MapWidget

    # ⚠ **참조를 잡아 둔다.** 버리면 QApplication 이 바로 수거돼 위젯을 만드는
    # 순간 프로세스가 조용히 죽는다 — 요약 줄도 안 나온다.
    app = QApplication.instance() or QApplication([])
    st = state()
    st.players[1].kind = "bot"
    w = MapWidget(st)
    spot = (12, 12)
    st.gmap.owner[st.gmap.ref(*spot)] = 1
    px = w.frames.owner_rgba()[spot[1], spot[0]]
    assert tuple(px[:3]) == P.player_color(1, "bot"), \
        "지도가 봇을 나라 색으로 그린다"


def test_the_biggest_rectangle_wins_not_the_first_one_found():
    """⚠ **재료가 필요하다.** 앞쪽에 작은 사각형, 뒤쪽에 큰 것을 두지 않으면
    "첫 번째를 고른다"는 변이가 같은 답을 낸다 — 실제로 살아남았다.

    막지 않았으면: 이름이 영토 안에는 앉지만 **가장 좁은 자리**에 앉아,
    글자가 실제로 쓸 수 있는 것보다 훨씬 작아진다."""
    from domynion.ui.frame import _largest_rectangle

    grid = np.zeros((6, 10), dtype=bool)
    grid[0:2, 0:2] = True                 # 먼저 나오는 작은 것 (2x2 = 4)
    grid[3:6, 4:10] = True                # 나중에 나오는 큰 것 (3x6 = 18)
    x, y, w, h = _largest_rectangle(grid)
    assert (w, h) == (6, 3), f"큰 사각형을 못 찾았다: {(x, y, w, h)}"
    assert (x, y) == (4, 3)


def test_the_grid_gets_coarser_as_the_territory_grows():
    """⚠ 원본의 스케일 사다리(<25→1 … 500 이상→32). 큰 나라를 1칸 격자로 뽑으면
    비용이 폭발한다 — 그래서 성기게 뽑고, **자리 크기가 그 배수로 나온다.**

    막지 않았으면: 항상 1칸 간격으로 뽑아도 이름은 영토 안에 앉으므로 눈에 안
    띈다 — 실제로 그 변이가 살아남았다. 배수를 단언해야 잡힌다."""
    from domynion.ui.frame import _name_scale

    assert [_name_scale(v) for v in (0, 24, 25, 49, 50, 99, 100, 249, 250,
                                     499, 500, 9999)] == \
        [1, 1, 2, 2, 4, 4, 8, 8, 16, 16, 32, 32]

    gm = GameMap.from_rows(["." * 700] * 700)
    ps = {0: PlayerState(pid=0, name="P0", kind="nation", start=gm.ref(0, 0))}
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1}
    st._posts = DefensePostIndex(gm.size)
    gm.owner.reshape(700, 700)[50:650, 50:650] = 0      # 짧은 변 600 → 배율 32

    (_, _, _, rw, rh), = FrameBuilder(gm).label_anchors(st.alive)
    assert rw % 32 == 0 and rh % 32 == 0, \
        f"성긴 격자를 안 썼다 — 자리가 32의 배수가 아니다 ({rw}, {rh})"


# --- 정보 오버레이 — 커서가 얹힌 나라 (§5.106) --------------------------------
#
# 원본 `PlayerInfoOverlay`(654줄) 대조. 우리는 이름·영토·병력·관계만 띄웠다.
# ⚠ **관계를 누구에게나 띄우는 버그가 여기에도 있었다** — §5.103 에서 외교
# 메뉴의 같은 자리를 고치면서 이 자리를 놓쳤다.

def _inspect_window(kind: str = "nation"):
    import random as _r

    from PyQt6.QtWidgets import QApplication

    from domynion.core.state import PlayerState
    from domynion.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    st = state()
    old = st.players[1]
    st.players[1] = PlayerState(pid=1, name="P1", start=old.start, kind=kind)
    st.players[1].troops = 5_000.0
    st.players[1].gold = 1_234_567.0
    win = MainWindow(st, human=0, rng=_r.Random(0))
    win.timer.stop()
    win.map.hovered_owner = 1
    win._refresh_inspect()
    return app, st, win


def test_the_overlay_says_what_kind_of_player_this_is():
    """봇인지 나라인지로 **외교가 통째로 달라진다** — 봇은 동맹을 전부 받는다."""
    for kind, word in (("bot", "봇"), ("nation", "나라"), ("human", "사람")):
        _app, _st, win = _inspect_window(kind)
        assert word in win.inspect.text()
        win.close()


def test_a_bot_gets_no_relation_because_it_ignores_one():
    """⚠ §5.103 과 **같은 거짓 재료**다. 여기서도 봇에게 관계를 띄우고 있었다."""
    from domynion.core.relations import RELATION_LABEL
    _app, _st, win = _inspect_window("bot")
    text = win.inspect.text()
    assert not any(lbl in text for lbl in RELATION_LABEL.values())
    win.close()
    _app, _st, win = _inspect_window("nation")
    assert any(lbl in win.inspect.text() for lbl in RELATION_LABEL.values())
    win.close()


def test_the_overlay_shows_gold_and_the_troop_cap():
    """골드는 **핵을 살 수 있는가**, 상한은 **더 불어날 여지가 있는가** 다.
    절대값만으로는 5,000 이 많은지 적은지 알 수 없다."""
    _app, st, win = _inspect_window()
    text = win.inspect.text()
    assert "1,234,567" in text
    cap = st.players[1].max_troops(st.tiles(1))
    assert f"{cap:,.0f}" in text
    win.close()


def test_troops_out_on_attack_are_shown_only_when_there_are_any():
    """**집에 얼마가 남았는가** 가 방어 판단의 재료다. 없을 때 0 을 띄우면
    줄만 길어진다(원본도 그때는 흐리게 죽인다)."""
    from domynion.core.attack import Attack
    _app, st, win = _inspect_window()
    assert "나가 있음" not in win.inspect.text()
    # ⚠ **국경을 맞대게 만든다.** 기본 판은 두 나라가 한 칸씩만 갖고 있어
    # `Attack.launch` 가 붙을 칸을 못 찾고 `None` 을 돌려준다 — 그러면 이
    # 테스트가 skip 으로 넘어가 **아무것도 재지 않는다.**
    gm = st.gmap
    for y in range(30):
        for x in range(0, 20):
            gm.owner[gm.ref(x, y)] = 0
        for x in range(20, 40):
            gm.owner[gm.ref(x, y)] = 1
    st._counts = {0: 600, 1: 600}
    a = Attack.launch(gm, 1, 0, 700.0, random.Random(0))
    assert a is not None
    st.attacks.append(a)
    win._refresh_inspect()
    assert "나가 있음" in win.inspect.text()
    win.close()


def test_a_traitor_and_an_ally_show_their_remaining_time():
    _app, st, win = _inspect_window()
    assert "🗡" not in win.inspect.text() and "🤝" not in win.inspect.text()
    st.diplomacy.traitor_since[1] = st.tick_count
    st.diplomacy.form(0, 1, st.tick_count)
    win._refresh_inspect()
    text = win.inspect.text()
    assert "🗡" in text and "🤝" in text
    win.close()
