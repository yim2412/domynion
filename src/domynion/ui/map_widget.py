"""지도 위젯 — 가로로 순환하는 지도, LOD, 부드러운 카메라.

세 가지가 얽혀 있다.

**가로 순환.** 오른쪽으로 계속 가면 왼쪽이 나온다. 세계 지도라 그게 자연스럽다.
⚠ **화면만 순환한다. 게임 규칙은 순환하지 않는다** — 원본 `neighbors4` 가 x 경계를
넘지 않으므로 지도 왼쪽 끝과 오른쪽 끝은 실제로 안 이어져 있다. 그 사이는 태평양이라
어차피 육지가 맞닿지 않아 눈에 띄지 않는다. 여기를 규칙까지 순환시키면 이식이 깨진다.

**LOD.** 줌이 1 미만이면(타일이 픽셀보다 작으면) 오버레이를 `stride` 칸마다 뽑는다.
어차피 화면에서 줄어들어 보이는데 원본 해상도로 만들 이유가 없다 —
2000×1000 에서 17.0ms → 4.3ms(실측).

**부드러운 카메라.** 게임은 10Hz(원본 tick)인데 카메라를 거기 얹으면 뚝뚝 끊긴다.
카메라와 화면 갱신은 **별도 타이머(60Hz)** 로 돌리고, 눌린 키에서 목표 속도를 만들어
현재 속도를 그쪽으로 당긴다(가속·감속).

설계 5절 함정: 격자선 없음 · 확대에 `SmoothPixmapTransform` 끄기 ·
`WA_StyledBackground` · 라벨 크기는 영토 덩어리의 실제 폭.
"""

from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QPen,
                         QWheelEvent)
from PyQt6.QtWidgets import QWidget

from ..core.constants import Terrain
from ..core.engine import GameState
from . import palette as P
from .frame import FrameBuilder


def ui_font(size: int, bold: bool = True) -> QFont:
    """한글이 되는 폰트. 지정하지 않으면 글자가 전부 두부(□)가 된다."""
    font = QFont()
    font.setFamilies(list(P.UI_FONT_FAMILIES))
    font.setPointSize(size)
    font.setBold(bold)
    return font


MIN_ZOOM = 0.15     # 원본 크기 지도(2000×1000)를 화면에 담으려면 이만큼 내려가야 한다
MAX_ZOOM = 24.0

CAMERA_HZ = 60
PAN_SPEED = 900.0        # 초당 픽셀
PAN_ACCEL = 12.0         # 목표 속도로 당기는 세기 (클수록 즉각적)
MAX_BORDER_LINES = 40_000


class MapWidget(QWidget):
    """지도를 그린다. 가로로 순환하고, 휠·키로 이동/확대한다."""

    def __init__(self, state: GameState, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.state = state
        self.frames = FrameBuilder(state.gmap)

        self.zoom = 0.0            # 0 이면 첫 그리기에서 화면에 맞춘다
        self.offset = QPointF(0, 0)
        self._drag_from: QPointF | None = None

        self._terrain_img: QImage | None = None
        self._overlay_img: QImage | None = None
        self._overlay_buf: np.ndarray | None = None
        self._lod_stride = 1
        self._crop_y0 = 0                 # 오버레이가 담고 있는 첫 줄(타일 좌표)
        self._borders: tuple[np.ndarray, np.ndarray] | None = None
        # 라벨 위치는 **게임 tick 마다만** 바뀐다. paint 마다(그것도 순환 사본마다)
        # 다시 계산하면 200만 칸을 플레이어 수만큼 훑는다 — 실측으로 그게 병목이었다.
        self._labels: list[tuple[int, float, float, float]] = []
        self._label_age = 0

        self.hovered_tile: int | None = None
        self.hovered_owner: int | None = None
        self._pings: list[tuple[int, int, float]] = []

        # 카메라 — 게임 tick 과 분리한다. 10Hz 에 얹으면 이동이 뚝뚝 끊긴다.
        self._keys: set[int] = set()
        self._vel = QPointF(0, 0)
        self._camera = QTimer(self)
        self._camera.timeout.connect(self._step_camera)
        self._camera.start(1000 // CAMERA_HZ)

        self._bake_terrain()

    # --- 좌표 -------------------------------------------------------------

    @property
    def world_w(self) -> float:
        """지도 한 바퀴의 픽셀 폭. 순환 계산의 기준이다."""
        return self.state.gmap.width * self.zoom

    def _fit_zoom(self) -> float:
        gm = self.state.gmap
        return min(self.width() / gm.width, self.height() / gm.height)

    def ensure_zoom(self) -> None:
        if self.zoom <= 0.0:
            self.zoom = self._fit_zoom()
            gm = self.state.gmap
            self.offset = QPointF((self.width() - gm.width * self.zoom) / 2,
                                  (self.height() - gm.height * self.zoom) / 2)

    def tile_at(self, pos: QPointF) -> int | None:
        """화면 좌표 → 타일. **x 는 순환**하므로 지도 폭으로 나머지를 취한다."""
        gm = self.state.gmap
        if self.zoom <= 0:
            return None
        x = int(math.floor((pos.x() - self.offset.x()) / self.zoom)) % gm.width
        y = int(math.floor((pos.y() - self.offset.y()) / self.zoom))
        if 0 <= y < gm.height:
            return gm.ref(x, y)
        return None

    def visible_tiles(self) -> tuple[int, int, int, int]:
        """보이는 타일 범위. 순환 때문에 x 는 지도 전체가 되기 쉬우므로,
        가로로 한 바퀴 이상 보이면 그냥 전체로 잡는다."""
        gm = self.state.gmap
        z = max(self.zoom, 1e-6)
        y0 = int((0 - self.offset.y()) / z) - 1
        y1 = int((self.height() - self.offset.y()) / z) + 2
        return (0, max(0, y0), gm.width, min(gm.height, y1))

    # --- 층 만들기 --------------------------------------------------------

    def _wanted_stride(self) -> int:
        """줌이 작을수록 성기게 뽑는다. 1 이상이면 원본 해상도가 필요하다."""
        if self.zoom >= 0.9:
            return 1
        return max(1, min(4, int(round(1.0 / max(self.zoom, 1e-6)))))

    def _bake_terrain(self) -> None:
        """지형 층을 굽는다. 지형이 바뀌거나 LOD 가 달라질 때만 부른다."""
        buf = self.frames.terrain_lod(self._lod_stride)
        h, w, _ = buf.shape
        self._terrain_img = QImage(buf.data, w, h, w * 3,
                                   QImage.Format.Format_RGB888).copy()

    def rebake(self) -> None:
        self.frames.rebake()
        self._bake_terrain()

    def refresh(self) -> None:
        """게임 상태가 바뀌었을 때. **소유자 층과 국경만** 새로 만든다."""
        self.ensure_zoom()
        stride = self._wanted_stride()
        if stride != self._lod_stride:
            self._lod_stride = stride
            self._bake_terrain()
        # 보이는 줄만 만든다. 확대했을 때 전체를 만들면 2000×1000 에서 6fps 다.
        _, vy0, _, vy1 = self.visible_tiles()
        self._crop_y0 = (vy0 // stride) * stride       # stride 격자에 맞춘다
        self._overlay_buf = self.frames.owner_rgba(stride, self._crop_y0, vy1)
        h, w, _ = self._overlay_buf.shape
        # 매 프레임이라 복사하지 않는다. 대신 `self._overlay_buf` 를 살려 둬야 한다 —
        # QImage 는 버퍼를 참조만 하므로, 지역변수로 두면 화면이 깨진다.
        self._overlay_img = QImage(self._overlay_buf.data, w, h, w * 4,
                                   QImage.Format.Format_RGBA8888)
        self._borders = self.frames.border_segments(self.visible_tiles())
        # 라벨은 전체 지도를 플레이어 수만큼 훑는다(원본 크기에서 12~14ms 실측).
        # 나라 중심은 1초에 한 번만 다시 잡아도 눈에 띄지 않는다.
        self._label_age -= 1
        if self._label_age <= 0 or not self._labels:
            self._labels = self.frames.label_anchors(self.state.alive)
            self._label_age = 10
        self.update()

    # --- 그리기 -----------------------------------------------------------

    def _tiles_x(self) -> list[float]:
        """지도를 그릴 x 위치들. 화면을 덮을 만큼 좌우로 반복한다."""
        ww = self.world_w
        if ww <= 0:
            return [self.offset.x()]
        start = self.offset.x() - math.ceil(self.offset.x() / ww) * ww
        out, x = [], start
        while x < self.width() and len(out) < 8:
            out.append(x)
            x += ww
        return out or [start]

    def paintEvent(self, _event) -> None:
        self.ensure_zoom()
        if self._overlay_img is None:
            self.refresh()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(*P.TERRAIN_COLORS[Terrain.OCEAN]))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        gm = self.state.gmap
        # 오버레이는 잘려 있으므로 **자른 위치에 맞춰** 그려야 한다.
        ov_h = self._overlay_buf.shape[0] * self._lod_stride if self._overlay_buf is not None else gm.height
        for x in self._tiles_x():
            target = QRectF(x, self.offset.y(),
                            gm.width * self.zoom, gm.height * self.zoom)
            p.drawImage(target, self._terrain_img)
            p.drawImage(QRectF(x, self.offset.y() + self._crop_y0 * self.zoom,
                               gm.width * self.zoom, ov_h * self.zoom),
                        self._overlay_img)           # 알파 합성은 Qt 가 한다
            self._draw_borders(p, x)
            self._draw_hover(p, x)
            self._draw_labels(p, x)
        self._draw_pings(p)
        p.end()

    def _draw_borders(self, p: QPainter, ox: float) -> None:
        if self._borders is None:
            return
        vx, hx = self._borders
        n = len(vx) + len(hx)
        if n == 0 or n > MAX_BORDER_LINES:
            # 너무 촘촘하면 그리지 않는다. 그 배율에서는 색 경계로 이미 보인다.
            return
        p.setPen(QPen(QColor(*P.BORDER_COLOR), max(1.0, self.zoom / 4)))
        z, oy = self.zoom, self.offset.y()
        lines = [QLineF(ox + x * z, oy + y * z, ox + x * z, oy + (y + 1) * z)
                 for x, y in vx]
        lines += [QLineF(ox + x * z, oy + y * z, ox + (x + 1) * z, oy + y * z)
                  for x, y in hx]
        p.drawLines(lines)

    def _draw_hover(self, p: QPainter, ox: float) -> None:
        """커서가 얹힌 나라의 국경을 밝게 덧그린다 — **무엇을 치게 되는지** 보여야 한다.

        openfront 에서 클릭은 그 칸이 아니라 소유자 전체를 겨눈다. 대상이 안 보이면
        오조작이 잦다."""
        if self.hovered_owner is None or self._borders is None:
            return
        vx, hx = self._borders
        n = len(vx) + len(hx)
        if n == 0 or n > MAX_BORDER_LINES:
            return
        gm = self.state.gmap
        owner = gm.owner.reshape(gm.height, gm.width)
        z, oy = self.zoom, self.offset.y()
        c = QColor(*P.player_color(self.hovered_owner))
        p.setPen(QPen(QColor(min(255, c.red() + 70), min(255, c.green() + 70),
                             min(255, c.blue() + 70)), max(1.5, self.zoom / 2.5)))
        me = self.hovered_owner
        lines = [QLineF(ox + x * z, oy + y * z, ox + x * z, oy + (y + 1) * z)
                 for x, y in vx if owner[y, x] == me or owner[y, x - 1] == me]
        lines += [QLineF(ox + x * z, oy + y * z, ox + (x + 1) * z, oy + y * z)
                  for x, y in hx if owner[y, x] == me or owner[y - 1, x] == me]
        if lines:
            p.drawLines(lines)

    def _draw_labels(self, p: QPainter, ox: float) -> None:
        anchors = self._labels
        if not anchors:
            return
        z, oy = self.zoom, self.offset.y()
        for pid, cx, cy, span in anchors:
            x = ox + cx * z
            if not (-300 < x < self.width() + 300):
                continue                      # 순환 사본 중 화면 밖은 건너뛴다
            player = self.state.players[pid]
            text = f"{player.name} {self.state.share(pid) * 100:.0f}%"
            # 폰트는 **영토가 실제로 차지한 폭**에서 뽑는다. 타일 비례로 잡으면
            # 큰 나라 이름이 화면을 덮고, 고정하면 작은 나라 위에서 넘친다.
            size = int(min(48, max(9, span * z / max(len(text), 3) * 0.9)))
            font = ui_font(size)
            p.setFont(font)
            fm = QFontMetrics(font)
            px = x - fm.horizontalAdvance(text) / 2
            py = oy + cy * z + fm.height() / 4
            p.setPen(QPen(QColor(*P.LABEL_SHADOW)))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p.drawText(int(px + dx), int(py + dy), text)
            p.setPen(QPen(QColor(*P.LABEL_COLOR)))
            p.drawText(int(px), int(py), text)

    def ping(self, tile: int) -> None:
        """클릭한 자리에 잠깐 표시를 남긴다. 없으면 눌렸는지조차 알 수 없다."""
        gm = self.state.gmap
        self._pings.append((tile % gm.width, tile // gm.width, 1.0))

    def _draw_pings(self, p: QPainter) -> None:
        if not self._pings:
            return
        z, oy = self.zoom, self.offset.y()
        p.setBrush(Qt.BrushStyle.NoBrush)
        xs = self._tiles_x()
        for x, y, life in self._pings:
            r = (1.0 - life) * 26 + 4
            p.setPen(QPen(QColor(255, 255, 255, int(220 * max(0.0, life))),
                          max(1.5, z / 3)))
            for ox in xs:
                cx = ox + (x + 0.5) * z
                if -50 < cx < self.width() + 50:
                    p.drawEllipse(QPointF(cx, oy + (y + 0.5) * z), r, r)

    # --- 카메라 -----------------------------------------------------------

    def _step_camera(self) -> None:
        """60Hz. 눌린 키에서 목표 속도를 만들고 현재 속도를 그쪽으로 당긴다.

        키를 누른 순간 최고 속도로 튀지 않고, 뗀 순간 멈추지도 않는다 — 그 사이가
        '부드럽다'는 느낌을 만든다."""
        dt = 1.0 / CAMERA_HZ
        tx = ty = 0.0
        if self._keys & {Qt.Key.Key_Left.value, Qt.Key.Key_A.value}:
            tx += 1
        if self._keys & {Qt.Key.Key_Right.value, Qt.Key.Key_D.value}:
            tx -= 1
        if self._keys & {Qt.Key.Key_Up.value, Qt.Key.Key_W.value}:
            ty += 1
        if self._keys & {Qt.Key.Key_Down.value, Qt.Key.Key_S.value}:
            ty -= 1
        if tx and ty:                       # 대각선이 빨라지지 않게
            tx *= 0.7071
            ty *= 0.7071

        k = min(1.0, PAN_ACCEL * dt)
        self._vel = QPointF(self._vel.x() + (tx * PAN_SPEED - self._vel.x()) * k,
                            self._vel.y() + (ty * PAN_SPEED - self._vel.y()) * k)

        moved = abs(self._vel.x()) > 0.5 or abs(self._vel.y()) > 0.5
        if moved:
            self.offset += QPointF(self._vel.x() * dt, self._vel.y() * dt)
            self._clamp_vertical()
        if self._pings:
            self._pings = [(x, y, life - dt * 1.6)
                           for x, y, life in self._pings if life > dt * 1.6]
            moved = True
        if moved:
            self.update()

    def _clamp_vertical(self) -> None:
        """세로는 순환하지 않는다 — 지도 위아래로 넘어가면 빈 화면만 남는다."""
        gm = self.state.gmap
        h = gm.height * self.zoom
        lo = min(0.0, self.height() - h)
        hi = max(0.0, self.height() - h)
        y = self.offset.y()
        self.offset = QPointF(self.offset.x(), max(lo, min(hi, y)))

    def zoom_at(self, pos: QPointF, factor: float) -> None:
        """`pos` 아래 지점을 고정한 채 확대한다 — 그래야 보던 곳을 잃지 않는다."""
        self.ensure_zoom()
        before = ((pos.x() - self.offset.x()) / self.zoom,
                  (pos.y() - self.offset.y()) / self.zoom)
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
        self.offset = QPointF(pos.x() - before[0] * self.zoom,
                              pos.y() - before[1] * self.zoom)
        self._clamp_vertical()
        self.refresh()          # 줌이 바뀌면 LOD 와 국경 범위가 달라진다

    # --- 입력 -------------------------------------------------------------

    def keyPressEvent(self, e) -> None:
        if e.isAutoRepeat():
            return
        key = e.key()
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(QPointF(self.width() / 2, self.height() / 2), 1.25)
            return
        if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_at(QPointF(self.width() / 2, self.height() / 2), 0.8)
            return
        if key == Qt.Key.Key_F:
            self.zoom = 0.0
            self.ensure_zoom()
            self.refresh()
            return
        self._keys.add(key)

    def keyReleaseEvent(self, e) -> None:
        if not e.isAutoRepeat():
            self._keys.discard(e.key())

    def focusOutEvent(self, e) -> None:
        self._keys.clear()          # 창을 벗어나면 키가 눌린 채로 남지 않게
        super().focusOutEvent(e)

    def wheelEvent(self, e: QWheelEvent) -> None:
        self.zoom_at(e.position(), 1.15 ** (e.angleDelta().y() / 120))

    def mousePressEvent(self, e) -> None:
        self.setFocus()
        if e.button() == Qt.MouseButton.RightButton:
            self._drag_from = e.position()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            d = e.position() - self._drag_from
            self.offset += d
            self._drag_from = e.position()
            self._clamp_vertical()
            self.update()
            return
        t = self.tile_at(e.position())
        if t != self.hovered_tile:
            self.hovered_tile = t
            gm = self.state.gmap
            o = int(gm.owner[t]) if t is not None and gm.passable(t) else -2
            self.hovered_owner = None if o < 0 else o
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self._drag_from = None
