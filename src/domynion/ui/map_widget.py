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


MIN_ZOOM = 0.5
MAX_ZOOM = 12.0


class MapWidget(QWidget):
    """지도를 그린다. 마우스 휠로 확대, 드래그로 이동."""

    def __init__(self, state: GameState, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.state = state
        self.frames = FrameBuilder(state.gmap)
        self.zoom = 0.0            # 0 이면 첫 그리기에서 화면에 맞춘다
        self.offset = QPointF(0, 0)
        self._drag_from: QPointF | None = None
        self._image: QImage | None = None
        self._buf: np.ndarray | None = None
        self.hovered_tile: int | None = None

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

    def refresh(self) -> None:
        """상태가 바뀌었을 때 부른다. 픽셀 버퍼를 새로 만들고 다시 그린다."""
        self._buf = self.frames.rgb()
        h, w, _ = self._buf.shape
        # QImage 는 버퍼를 **복사하지 않는다.** `self._buf` 를 살려 둬야 한다 —
        # 지역변수로만 두면 다음 프레임에 회수돼 화면이 깨진다.
        self._image = QImage(self._buf.data, w, h, w * 3, QImage.Format.Format_RGB888)
        self.update()

    def paintEvent(self, _event) -> None:
        self.ensure_zoom()
        if self._image is None:
            self.refresh()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(*P.TERRAIN_COLORS[Terrain.OCEAN]))
        # 확대에 부드러움을 쓰지 않는다 — 켜면 국경이 뭉개져 덩어리 경계가 흐려진다
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        gm = self.state.gmap
        target = QRectF(self.offset.x(), self.offset.y(),
                        gm.width * self.zoom, gm.height * self.zoom)
        p.drawImage(target, self._image)

        self._draw_borders(p)
        self._draw_labels(p)
        p.end()

    def _draw_borders(self, p: QPainter) -> None:
        vx, hx = self.frames.border_segments()
        if not len(vx) and not len(hx):
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
        """커서 아래 지점을 고정한 채 확대한다 — 그래야 보던 곳을 잃지 않는다."""
        self.ensure_zoom()
        pos = e.position()
        before = ((pos.x() - self.offset.x()) / self.zoom,
                  (pos.y() - self.offset.y()) / self.zoom)
        factor = 1.15 ** (e.angleDelta().y() / 120)
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
            self.hovered_tile = self.tile_at(e.position())

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self._drag_from = None
