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
        self._flash_left = 0

        self.map = MapWidget(state)
        self.setCentralWidget(self.map)
        self.map.mouseReleaseEvent = self._map_release      # 좌클릭 = 공격

        self.scoreboard = Scoreboard(state, self.map)
        self.scoreboard.move(12, 12)
        self.scoreboard.adjustSize()

        self.controls = ControlBar(state, human, self.map)
        self.controls.ratio_changed.connect(self._set_ratio)

        # 커서가 얹힌 대상 정보. 무엇을 치는지 모르면 클릭이 도박이 된다.
        self.inspect = QLabel("", self.map)
        self.inspect.setStyleSheet(
            "color:#e8e8ec; background: rgba(16,20,28,210); padding: 6px 10px;"
            "border-radius: 6px;")
        self.inspect.hide()

        self.banner = QLabel("", self.map)
        self.banner.setStyleSheet(
            "color:#fff; background: rgba(16,20,28,220); padding: 10px 18px;"
            "border-radius: 8px; font-size: 20px; font-weight: bold;")
        self.banner.hide()

        QShortcut(QKeySequence("Space"), self, self.toggle_pause)
        QShortcut(QKeySequence("Escape"), self, self.close)
        QShortcut(QKeySequence("H"), self, self.toggle_help)

        self.help = QLabel(
            "<b>조작</b><br>"
            "좌클릭 — 그 칸의 <b>소유자 전체</b>를 공격<br>"
            "우클릭 드래그 · WASD/화살표 — 이동<br>"
            "휠 · +/− — 확대 &nbsp;·&nbsp; F — 화면에 맞추기<br>"
            "Space — 일시정지 &nbsp;·&nbsp; H — 이 도움말 &nbsp;·&nbsp; Esc — 종료",
            self.map)
        self.help.setStyleSheet(
            "color:#e8e8ec; background: rgba(16,20,28,225); padding: 12px 16px;"
            "border-radius: 8px;")
        self.help.adjustSize()
        self.help.show()

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
        self.help.adjustSize()
        self.help.move(self.map.width() - self.help.width() - 12, 12)

    # --- 루프 -------------------------------------------------------------

    def toggle_help(self) -> None:
        self.help.setVisible(not self.help.isVisible())
        if self.help.isVisible():
            self.help.raise_()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self._show_banner("일시정지" if self.paused else "")

    def _tick(self) -> None:
        if self._flash_left > 0:
            self._flash_left -= 1
            if self._flash_left == 0 and not self.state.over and not self.paused:
                self.banner.hide()
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
        self._refresh_inspect()

    def _refresh_inspect(self) -> None:
        """커서가 얹힌 나라의 병력·영토를 보여 준다.

        원본에서 공격 판단의 핵심이 **상대 병력 대비 내 병력**이다
        (`within(수비병력/공격병력, 0.6, 2)`). 그 값을 못 보면 판단이 불가능하다."""
        st = self.state
        pid = self.map.hovered_owner
        if pid is None or pid not in st.players:
            self.inspect.hide()
            return
        p = st.players[pid]
        mine = st.players.get(self.human)
        send = mine.attack_troops() if mine else 0.0
        ratio = (p.troops / send) if send > 0 else float("inf")
        hint = ("유리" if ratio < 0.6 else "불리" if ratio > 2 else "팽팽")
        self.inspect.setText(
            f"<b>{p.name}</b>  영토 {st.share(pid) * 100:.1f}%  "
            f"병력 {p.troops:,.0f}<br>"
            f"<span style='opacity:.75'>내가 보낼 병력 {send:,.0f} · "
            f"상대/내 = {ratio:.2f} ({hint})</span>")
        self.inspect.adjustSize()
        self.inspect.move(12, self.scoreboard.y() + self.scoreboard.height() + 8)
        self.inspect.show()
        self.inspect.raise_()

    def _announce(self) -> None:
        st = self.state
        who = st.players[st.winner].name if st.winner is not None else "무승부"
        kind = st.victory.value if st.victory else ""
        self._show_banner(f"{kind} — {who}")

    def _flash(self, text: str, ticks: int = 20) -> None:
        """잠깐 뜨는 안내. 클릭이 아무 일도 안 했을 때 이유를 알려 준다."""
        self._show_banner(text)
        self._flash_left = ticks

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
        self.map.ping(tile)              # 눌렸다는 표시를 먼저 남긴다
        if self.state.launch_attack(self.human, target) is None:
            # 육지로 못 닿으면 배로 간다
            if self.state.send_boat(self.human, tile) is None:
                self._flash("닿지 않는다 — 국경이나 해안 쪽을 노려 보세요")
