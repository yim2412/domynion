"""지도 위젯 — QImage 를 확대해 그리고, 그 위에 국경선과 이름을 얹는다.

세 층으로 나눈다:

1. **지형 + 소유자** — 타일 해상도 QImage. 확대는 Qt(하드웨어)가 한다
2. **국경선** — `QPainter.drawLines` 로 한 번에. 소유자가 다른 변에만 긋는다
3. **이름** — 영토 덩어리 폭에 맞춘 크기로

설계 5절의 함정을 지킨다:
- 격자선을 그리지 않는다. **소유자가 다른 변만** 선을 갖는다
- 확대는 `SmoothPixmapTransform` 을 끄고 한다. 켜면 국경이 뭉개져 덩어리 경계가 흐려진다
- `WA_StyledBackground` 를 켜야 QWidget 서브클래스의 배경이 칠해진다
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
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


MIN_ZOOM = 0.2      # 원본 크기 지도(2000×1000)를 화면에 담으려면 0.5 로는 모자란다
MAX_ZOOM = 24.0


class MapWidget(QWidget):
    """지도를 그린다. 마우스 휠로 확대, 드래그로 이동."""

    def __init__(self, state: GameState, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # 키 입력을 받으려면 필요하다
        self.state = state
        self.frames = FrameBuilder(state.gmap)
        self.zoom = 0.0            # 0 이면 첫 그리기에서 화면에 맞춘다
        self.offset = QPointF(0, 0)
        self._drag_from: QPointF | None = None
        # 지형은 한 번만 굽는다 — 판 내내 같은 QImage 다(핵이 터지면 다시 굽는다).
        self._terrain_img: QImage | None = None
        self._overlay_img: QImage | None = None
        self._overlay_buf: np.ndarray | None = None
        self.hovered_tile: int | None = None
        self.hovered_owner: int | None = None
        # 공격이 나갔다는 표시. 클릭했는데 아무 일도 안 일어나면 눌렸는지조차 모른다.
        self._pings: list[tuple[int, int, int]] = []      # (x, y, 남은 프레임)
        self._bake_terrain()

    # --- 좌표 -------------------------------------------------------------

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
        gm = self.state.gmap
        x = int((pos.x() - self.offset.x()) / self.zoom)
        y = int((pos.y() - self.offset.y()) / self.zoom)
        if 0 <= x < gm.width and 0 <= y < gm.height:
            return gm.ref(x, y)
        return None

    # --- 그리기 -----------------------------------------------------------

    def _bake_terrain(self) -> None:
        """지형 층을 굽는다. **핵이 지형을 바꿨을 때만** 다시 부른다."""
        buf = self.frames.terrain_rgb
        h, w, _ = buf.shape
        # QImage 는 버퍼를 복사하지 않으므로 `copy()` 로 자기 것을 갖게 한다 —
        # 지형은 한 번만 만드니 복사 비용이 문제되지 않는다.
        self._terrain_img = QImage(buf.data, w, h, w * 3,
                                   QImage.Format.Format_RGB888).copy()

    def rebake(self) -> None:
        self.frames.rebake()
        self._bake_terrain()

    def refresh(self) -> None:
        """상태가 바뀌었을 때 부른다. **소유자 층만** 새로 만든다."""
        self._overlay_buf = self.frames.owner_rgba()
        h, w, _ = self._overlay_buf.shape
        # 이쪽은 매 프레임이라 복사하지 않는다. 대신 `self._overlay_buf` 를 살려
        # 둬야 한다 — 지역변수로만 두면 다음 프레임에 회수돼 화면이 깨진다.
        self._overlay_img = QImage(self._overlay_buf.data, w, h, w * 4,
                                   QImage.Format.Format_RGBA8888)
        self.update()

    def visible_tiles(self) -> tuple[int, int, int, int]:
        """화면에 보이는 타일 범위 `(x0, y0, x1, y1)`. 국경 계산을 여기로 묶는다 —
        원본 크기 지도(200만 칸)에서 전체를 훑으면 18ms 가 든다."""
        gm = self.state.gmap
        z = max(self.zoom, 1e-6)
        x0 = int((0 - self.offset.x()) / z) - 1
        y0 = int((0 - self.offset.y()) / z) - 1
        x1 = int((self.width() - self.offset.x()) / z) + 2
        y1 = int((self.height() - self.offset.y()) / z) + 2
        return (max(0, x0), max(0, y0), min(gm.width, x1), min(gm.height, y1))

    def paintEvent(self, _event) -> None:
        self.ensure_zoom()
        if self._overlay_img is None:
            self.refresh()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(*P.TERRAIN_COLORS[Terrain.OCEAN]))
        # 확대에 부드러움을 쓰지 않는다 — 켜면 국경이 뭉개져 덩어리 경계가 흐려진다
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        gm = self.state.gmap
        target = QRectF(self.offset.x(), self.offset.y(),
                        gm.width * self.zoom, gm.height * self.zoom)
        p.drawImage(target, self._terrain_img)
        p.drawImage(target, self._overlay_img)      # 알파 합성은 Qt 가 한다

        self._draw_borders(p)
        self._draw_hover(p)
        self._draw_pings(p)
        self._draw_labels(p)
        p.end()

    def _draw_borders(self, p: QPainter) -> None:
        vx, hx = self.frames.border_segments(self.visible_tiles())
        if not len(vx) and not len(hx):
            return
        # 선 개수가 너무 많으면(줌 아웃) 그리지 않는다. 그 배율에서는 어차피
        # 픽셀보다 촘촘해 색 경계로 이미 보인다.
        if len(vx) + len(hx) > 40_000:
            return
        width = max(1.0, self.zoom / 4)
        p.setPen(QPen(QColor(*P.BORDER_COLOR), width))
        z, ox, oy = self.zoom, self.offset.x(), self.offset.y()
        lines = []
        for x, y in vx:                       # 세로변: 타일 왼쪽 변
            px = ox + x * z
            lines.append(QLineF(px, oy + y * z, px, oy + (y + 1) * z))
        for x, y in hx:                       # 가로변: 타일 위쪽 변
            py = oy + y * z
            lines.append(QLineF(ox + x * z, py, ox + (x + 1) * z, py))
        p.drawLines(lines)

    def ping(self, tile: int) -> None:
        """클릭한 자리에 잠깐 표시를 남긴다. 없으면 눌렸는지조차 알 수 없다."""
        gm = self.state.gmap
        self._pings.append((tile % gm.width, tile // gm.width, 12))

    def _draw_pings(self, p: QPainter) -> None:
        if not self._pings:
            return
        z, ox, oy = self.zoom, self.offset.x(), self.offset.y()
        keep = []
        for x, y, life in self._pings:
            r = (14 - life) * max(2.0, z)
            fade = int(220 * life / 12)
            p.setPen(QPen(QColor(255, 255, 255, fade), max(1.5, z / 3)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(ox + (x + 0.5) * z, oy + (y + 0.5) * z), r, r)
            if life > 1:
                keep.append((x, y, life - 1))
        self._pings = keep

    def _draw_hover(self, p: QPainter) -> None:
        """커서가 얹힌 나라의 국경을 밝게 덧그린다 — **무엇을 치게 되는지**가 보여야 한다.

        openfront 에서 클릭은 그 칸이 아니라 소유자 전체를 겨눈다. 대상이 안 보이면
        오조작이 잦다."""
        if self.hovered_owner is None:
            return
        vx, hx = self.frames.border_segments(self.visible_tiles())
        if not len(vx) and not len(hx):
            return
        gm = self.state.gmap
        owner = gm.owner.reshape(gm.height, gm.width)
        z, ox, oy = self.zoom, self.offset.x(), self.offset.y()
        col = QColor(*P.player_color(self.hovered_owner))
        col = QColor(min(255, col.red() + 70), min(255, col.green() + 70),
                     min(255, col.blue() + 70))
        p.setPen(QPen(col, max(1.5, self.zoom / 2.5)))
        lines = []
        me = self.hovered_owner
        for x, y in vx:
            if owner[y, x] == me or owner[y, x - 1] == me:
                px = ox + x * z
                lines.append(QLineF(px, oy + y * z, px, oy + (y + 1) * z))
        for x, y in hx:
            if owner[y, x] == me or owner[y - 1, x] == me:
                py = oy + y * z
                lines.append(QLineF(ox + x * z, py, ox + (x + 1) * z, py))
        if lines:
            p.drawLines(lines)

    def _draw_labels(self, p: QPainter) -> None:
        anchors = self.frames.label_anchors(self.state.alive)
        if not anchors:
            return
        z, ox, oy = self.zoom, self.offset.x(), self.offset.y()
        for pid, cx, cy, span in anchors:
            player = self.state.players[pid]
            text = f"{player.name} {self.state.share(pid) * 100:.0f}%"
            # 폰트는 **영토가 실제로 차지한 폭**에서 뽑는다. 타일 비례로 잡으면
            # 큰 나라 이름이 화면을 덮고, 고정하면 작은 나라 위에서 넘친다.
            size = int(min(48, max(9, span * z / max(len(text), 3) * 0.9)))
            font = ui_font(size)
            p.setFont(font)
            fm = QFontMetrics(font)
            w = fm.horizontalAdvance(text)
            x = ox + cx * z - w / 2
            y = oy + cy * z + fm.height() / 4
            p.setPen(QPen(QColor(*P.LABEL_SHADOW)))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p.drawText(int(x + dx), int(y + dy), text)
            p.setPen(QPen(QColor(*P.LABEL_COLOR)))
            p.drawText(int(x), int(y), text)

    # --- 입력 -------------------------------------------------------------

    def wheelEvent(self, e: QWheelEvent) -> None:
        self.zoom_at(e.position(), 1.15 ** (e.angleDelta().y() / 120))

    def keyPressEvent(self, e) -> None:
        """화살표/WASD 로 이동, +/- 로 확대. 마우스만으로 다루면 손이 바쁘다."""
        step = 80
        moves = {
            Qt.Key.Key_Left: (step, 0), Qt.Key.Key_A: (step, 0),
            Qt.Key.Key_Right: (-step, 0), Qt.Key.Key_D: (-step, 0),
            Qt.Key.Key_Up: (0, step), Qt.Key.Key_W: (0, step),
            Qt.Key.Key_Down: (0, -step), Qt.Key.Key_S: (0, -step),
        }
        if e.key() in moves:
            dx, dy = moves[e.key()]
            self.offset += QPointF(dx, dy)
            self.update()
            return
        if e.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(QPointF(self.width() / 2, self.height() / 2), 1.25)
            return
        if e.key() in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_at(QPointF(self.width() / 2, self.height() / 2), 0.8)
            return
        if e.key() == Qt.Key.Key_F:
            self.zoom = 0.0                 # 화면에 맞춘다
            self.ensure_zoom()
            self.update()
            return
        super().keyPressEvent(e)

    def zoom_at(self, pos: QPointF, factor: float) -> None:
        """`pos` 아래 지점을 고정한 채 확대한다 — 그래야 보던 곳을 잃지 않는다."""
        self.ensure_zoom()
        before = ((pos.x() - self.offset.x()) / self.zoom,
                  (pos.y() - self.offset.y()) / self.zoom)
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
        self.offset = QPointF(pos.x() - before[0] * self.zoom,
                              pos.y() - before[1] * self.zoom)
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self._drag_from = e.position()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            d = e.position() - self._drag_from
            self.offset += d
            self._drag_from = e.position()
            self.update()
        else:
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
