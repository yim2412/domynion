"""메인 창 — 지도 + HUD 조립, 실시간 타이머.

**시뮬레이션과 화면을 같은 주기로 돌리지 않는다.** 게임은 10Hz(원본 `turnIntervalMs`
= 100ms)이고 화면은 그보다 자주 그릴 이유가 없으므로 같은 타이머에 얹되, 한 tick 이
늦어도 따라잡지 않는다 — 따라잡으면 렉이 났을 때 화면이 튄다.

AI 는 `nation.NationBot` 이 돌린다. 사람은 `human` 으로 잡은 pid 하나뿐이고,
지도를 클릭하면 그 칸의 **소유자 전체**를 공격한다(원본과 같다).
"""

from __future__ import annotations

import random

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from ..ai import nation
from ..core import constants as C
from ..core.engine import GameState
from .hud import ControlBar, Scoreboard
from .map_widget import MapWidget


class MainWindow(QMainWindow):
    def __init__(self, state: GameState, human: int, rng: random.Random,
                 difficulty: str = "medium"):
        super().__init__()
        self.setWindowTitle("Domynion")
        self.state = state
        self.human = human
        self.bots = nation.attach(state, rng, difficulty)
        self.paused = False

        self.map = MapWidget(state)
        self.setCentralWidget(self.map)
        self.map.mouseReleaseEvent = self._map_release      # 좌클릭 = 공격

        self.scoreboard = Scoreboard(state, self.map)
        self.scoreboard.move(12, 12)
        self.scoreboard.adjustSize()

        self.controls = ControlBar(state, human, self.map)
        self.controls.ratio_changed.connect(self._set_ratio)

        self.banner = QLabel("", self.map)
        self.banner.setStyleSheet(
            "color:#fff; background: rgba(16,20,28,220); padding: 10px 18px;"
            "border-radius: 8px; font-size: 20px; font-weight: bold;")
        self.banner.hide()

        QShortcut(QKeySequence("Space"), self, self.toggle_pause)
        QShortcut(QKeySequence("Escape"), self, self.close)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(C.TICK_MS)
        self.resize(1280, 760)

    # --- 배치 -------------------------------------------------------------

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.controls.adjustSize()
        self.controls.setFixedWidth(self.map.width() - 24)
        self.controls.move(12, self.map.height() - self.controls.height() - 12)
        self.banner.adjustSize()
        self.banner.move((self.map.width() - self.banner.width()) // 2,
                         self.map.height() // 2 - 40)

    # --- 루프 -------------------------------------------------------------

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self._show_banner("일시정지" if self.paused else "")

    def _tick(self) -> None:
        if not self.paused and not self.state.over:
            self.state.tick()
            for b in self.bots:
                b.tick(self.state)
            if self.state.over:
                self._announce()
        self.map.refresh()
        self.scoreboard.refresh()
        self.scoreboard.adjustSize()
        self.controls.refresh()

    def _announce(self) -> None:
        st = self.state
        who = st.players[st.winner].name if st.winner is not None else "무승부"
        kind = st.victory.value if st.victory else ""
        self._show_banner(f"{kind} — {who}")

    def _show_banner(self, text: str) -> None:
        if not text:
            self.banner.hide()
            return
        self.banner.setText(text)
        self.banner.adjustSize()
        self.banner.move((self.map.width() - self.banner.width()) // 2,
                         self.map.height() // 2 - 40)
        self.banner.show()
        self.banner.raise_()

    # --- 입력 -------------------------------------------------------------

    def _set_ratio(self, ratio: float) -> None:
        p = self.state.players.get(self.human)
        if p is not None:
            p.attack_ratio = ratio

    def _map_release(self, e) -> None:
        """좌클릭 = 그 칸의 **소유자 전체**를 공격. 원본과 같다.

        바다를 클릭하면 상륙이 아니라 아무 일도 안 한다 — 상륙은 목표 육지를
        직접 찍어야 한다(그쪽이 오조작이 적다)."""
        if e.button() == Qt.MouseButton.RightButton:
            MapWidget.mouseReleaseEvent(self.map, e)
            return
        tile = self.map.tile_at(e.position())
        if tile is None or self.state.over:
            return
        gm = self.state.gmap
        if not gm.passable(tile):
            return
        owner = int(gm.owner[tile])
        target = None if owner < 0 else owner
        if target == self.human:
            return
        if self.state.launch_attack(self.human, target) is None:
            # 육지로 못 닿으면 배로 간다
            self.state.send_boat(self.human, tile)
