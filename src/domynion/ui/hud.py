"""HUD — 순위표 · 병력바 · 공격 슬라이더.

지도 위에 겹쳐 뜨는 오버레이다. **`WA_StyledBackground` 를 켜야** QWidget 서브클래스에
스타일시트 배경이 칠해진다 — 안 켜면 배경이 투명해서 지도 위 글자가 안 읽힌다
(설계 5절의 함정).

슬라이더가 정하는 것은 원본의 `attackAmount` 다. 기본 20%(`병력/5`).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget)

from ..core import constants as C
from ..core.engine import GameState
from . import palette as P

_PANEL = """
QWidget#panel { background: rgba(16, 20, 28, 200); border-radius: 6px; }
QLabel { color: #e8e8ec; }
"""


def _swatch(pid: int) -> str:
    r, g, b = P.player_color(pid)
    return f"#{r:02x}{g:02x}{b:02x}"


class Scoreboard(QWidget):
    """순위표 — 영토 점유율 순으로. 탈락한 쪽은 흐리게."""

    def __init__(self, state: GameState, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("panel")
        self.setStyleSheet(_PANEL)
        self.state = state
        self._rows: list[QLabel] = []
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(2)
        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("font-weight: bold;")
        box.addWidget(self.clock_label)
        for _ in state.players:
            lbl = QLabel()
            box.addWidget(lbl)
            self._rows.append(lbl)

    def refresh(self) -> None:
        st = self.state
        bar = ""
        if st.clock.cfg.enabled:
            need = st.clock.bar_tiles(st.elapsed, st.gmap.land_count)
            bar = f"   기준선 {need / max(1, st.gmap.land_count) * 100:.1f}%"
        self.clock_label.setText(f"{int(st.elapsed) // 60:02d}:{int(st.elapsed) % 60:02d}{bar}")

        order = sorted(st.players.values(), key=lambda p: -st.tiles(p.pid))
        for lbl, p in zip(self._rows, order):
            share = st.share(p.pid) * 100
            doomed = p.pid in st.clock.marked_at
            mark = " ☠" if doomed else ""
            colour = _swatch(p.pid)
            dim = "" if p.alive else "opacity: 0.4;"
            lbl.setText(
                f'<span style="color:{colour}">■</span> {p.name}{mark} '
                f'&nbsp;{share:4.1f}%&nbsp; <span style="opacity:.7">'
                f'{p.troops:,.0f}</span>')
            lbl.setStyleSheet(dim)


class ControlBar(QWidget):
    """하단 바 — 내 병력, 공격 비율 슬라이더."""

    ratio_changed = pyqtSignal(float)

    def __init__(self, state: GameState, pid: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("panel")
        self.setStyleSheet(_PANEL)
        self.state = state
        self.pid = pid

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        self.troops_label = QLabel()
        self.troops_label.setMinimumWidth(220)
        row.addWidget(self.troops_label)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 100)
        self.slider.setValue(int(C.ATTACK_RATIO_HUMAN * 100))
        self.slider.setFixedWidth(240)
        self.slider.valueChanged.connect(self._on_slider)
        row.addWidget(self.slider)

        self.ratio_label = QLabel()
        self.ratio_label.setMinimumWidth(90)
        row.addWidget(self.ratio_label)
        row.addStretch(1)

        self.gold_label = QLabel()
        row.addWidget(self.gold_label)
        self._on_slider(self.slider.value())

    def _on_slider(self, value: int) -> None:
        self.ratio_label.setText(f"공격 {value}%")
        self.ratio_changed.emit(value / 100)

    def refresh(self) -> None:
        p = self.state.players.get(self.pid)
        if p is None:
            return
        cap = p.max_troops(self.state.tiles(self.pid))
        pct = p.troops / cap * 100 if cap else 0
        self.troops_label.setText(
            f"병력 <b>{p.troops:,.0f}</b> / {cap:,.0f} ({pct:.0f}%)")
        self.gold_label.setText(f"골드 <b>{p.gold:,}</b>")
