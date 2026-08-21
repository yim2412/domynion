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
from ..core.relations import RELATION_COLOUR, RELATION_LABEL
from .actions import EMOJI_OPEN, root_items
from .emojitable import EmojiTable
from .endmodal import EndModal
from .eventlog import AlertBanner, AttacksPanel, EventList
from .hud import ControlBar, ImmunityBar, Scoreboard
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

        # 면역은 규칙에만 넣으면 안 된다 — 왜 공격이 안 되는지 항상 보여야 한다.
        self.immunity = ImmunityBar(state, human, self.map)

        # 이모지는 장식이 아니다 — 🖕 하나가 상대 관계를 −100 움직인다.
        self.emoji = EmojiTable(state, human, self.map)
        self.emoji.picked.connect(self._send_emoji)

        # 탈락하면 판이 안 끝나도 그 자리에서 뜬다 — 없을 때는 내가 죽은 줄도 몰랐다.
        self.end = EndModal(state, human, self.map)

        # 커서가 얹힌 대상 정보. 무엇을 치는지 모르면 클릭이 도박이 된다.
        self.inspect = QLabel("", self.map)
        self.inspect.setStyleSheet(
            "color:#e8e8ec; background: rgba(16,20,28,210); padding: 6px 10px;"
            "border-radius: 6px;")
        self.inspect.hide()

        # 이벤트는 셋으로 나눈다 — 지나간 일(로그) · 지금 상태(전투) · 놓치면 안
        # 되는 것(경보). 하나로 몰면 급한 것이 흘러가 버린다.
        self.events = EventList(state, human, parent=self.map)
        self.attacks = AttacksPanel(state, human, parent=self.map)
        self.alert = AlertBanner(state, human, parent=self.map)

        self.banner = QLabel("", self.map)
        self.banner.setStyleSheet(
            "color:#fff; background: rgba(16,20,28,220); padding: 10px 18px;"
            "border-radius: 8px; font-size: 20px; font-weight: bold;")
        self.banner.hide()

        QShortcut(QKeySequence("Space"), self, self.toggle_pause)
        QShortcut(QKeySequence("Escape"), self, self.close)
        QShortcut(QKeySequence("H"), self, self.toggle_help)
        # 원본 기본 키바인딩: T 내림 / Y 올림, 10%p 씩(`attackRatioIncrement`)
        QShortcut(QKeySequence("T"), self,
                  lambda: self.controls.nudge_ratio(-C.ATTACK_RATIO_STEP))
        QShortcut(QKeySequence("Y"), self,
                  lambda: self.controls.nudge_ratio(+C.ATTACK_RATIO_STEP))

        self.help = QLabel(
            "<b>조작</b><br>"
            "<b>좌클릭 — 메뉴</b> (공격 · 건설 · 상륙 · 외교)<br>"
            "&nbsp;&nbsp;가운데 = 뒤로 · 바깥 = 닫기 · 회색 항목엔 이유가 붙는다<br>"
            "우클릭 드래그 · WASD/화살표 — 이동 (가로로 계속 순환한다)<br>"
            "휠 · +/− — 확대 &nbsp;·&nbsp; F — 화면에 맞추기<br>"
            "T/Y — 공격 비율 ∓10%p &nbsp;·&nbsp; Space — 일시정지 &nbsp;·&nbsp; H — 이 도움말 &nbsp;·&nbsp; Esc — 종료",
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
        self.immunity.setGeometry(0, 0, self.map.width(), ImmunityBar.HEIGHT)
        self.banner.adjustSize()
        self.banner.move((self.map.width() - self.banner.width()) // 2,
                         self.map.height() // 2 - 40)
        self.help.adjustSize()
        self.help.move(self.map.width() - self.help.width() - 12, 12)
        self._place_side_panels()

    def _place_side_panels(self) -> None:
        """오른쪽 아래에 전투 → 소식 순으로 쌓는다. 위쪽은 도움말이 쓴다."""
        margin = 12
        bottom = self.map.height() - self.controls.height() - margin * 2
        for panel in (self.attacks, self.events):
            if not panel.isVisible():
                continue
            panel.adjustSize()
            panel.setFixedWidth(330)
            panel.adjustSize()
            bottom -= panel.height() + 8
            panel.move(self.map.width() - panel.width() - margin, bottom)
            panel.raise_()
        if self.alert.isVisible():
            self.alert.move((self.map.width() - self.alert.width()) // 2, 16)
            self.alert.raise_()

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
        self.immunity.refresh()
        self.emoji.refresh()
        self.end.check()
        self._refresh_inspect()
        self.events.refresh()
        self.attacks.refresh()
        self.alert.refresh()
        self._place_side_panels()

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
        # **상대가 나를 어떻게 보는가**. 내가 상대를 보는 눈이 아니다 —
        # 동맹 요청이 받아들여질지 정하는 것은 상대 쪽 값이다.
        rel = st.relation_of(pid, self.human)
        self.inspect.setText(
            f"<b>{p.name}</b>  영토 {st.share(pid) * 100:.1f}%  "
            f"병력 {p.troops:,.0f}  "
            f"<span style='color:{RELATION_COLOUR[rel]}'>"
            f"{RELATION_LABEL[rel]}</span><br>"
            f"<span style='opacity:.75'>내가 보낼 병력 {send:,.0f} · "
            f"상대/내 = {ratio:.2f} ({hint})</span>")
        self.inspect.adjustSize()
        self.inspect.move(12, self.scoreboard.y() + self.scoreboard.height() + 8)
        self.inspect.show()
        self.inspect.raise_()

    def _open_emoji(self, target: int) -> None:
        self.emoji.open_for(target)
        self.emoji.move((self.map.width() - self.emoji.width()) // 2,
                        (self.map.height() - self.emoji.height()) // 2)

    def _send_emoji(self, ch: str) -> None:
        t = self.emoji.target
        if t is None:
            return
        if self.state.send_emoji(self.human, t, ch):
            self._show_banner(f"{ch}  →  {self.state.players[t].name}")
            self._flash_left = 15
        else:
            self._flash("아직 보낼 수 없다 (쿨다운)")

    def _announce(self) -> None:
        st = self.state
        who = st.players[st.winner].name if st.winner is not None else "무승부"
        kind = st.victory.value if st.victory else ""
        self._show_banner(f"{kind} — {who}")

    def _flash(self, text: str, ticks: int = 20) -> None:
        # 메뉴는 위젯을 모른다 — 이모지 판을 열라는 신호만 흘려보낸다.
        if text.startswith(EMOJI_OPEN):
            self._open_emoji(int(text[len(EMOJI_OPEN):]))
            return
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
        """좌클릭 = **방사형 메뉴**를 연다. 원본과 같은 조작이다(MainRadialMenu).

        처음엔 좌클릭을 즉시 공격으로 뒀는데, 그러면 사람이 할 수 있는 게 공격
        하나뿐이라 골드를 쓸 수도 외교를 할 수도 없었다."""
        if e.button() == Qt.MouseButton.RightButton:
            MapWidget.mouseReleaseEvent(self.map, e)
            return

        if self.map.menu is not None:
            if self.map.menu.activate(e.position()):
                self.map.close_menu()
            else:
                self.map.update()
            return

        tile = self.map.tile_at(e.position())
        if tile is None or self.state.over:
            return
        if not self.state.gmap.passable(tile):
            self._flash("바다다 — 육지를 찍어야 한다")
            return
        self.map.ping(tile)
        self.map.open_menu(e.position(), tile,
                           root_items(self.state, self.human, tile, self._flash))
