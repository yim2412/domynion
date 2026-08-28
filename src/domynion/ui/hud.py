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
from .rates import gold_pip, rate_rising, troop_rate

# 순위표에 몇 줄을 보여줄 것인가. 원본 기본 구성이 472명이라 전부 쓰면 화면을 덮는다.
# 마지막 한 줄은 상위권 밖일 때 **내 자리**로 쓴다.
SCOREBOARD_ROWS = 12

# 증가율 색 — 원본의 `text-green-400` / `text-orange-400` 그대로다.
# 오르는 중이면 초록, 꺾이면 주황. **색이 곧 신호라 회색으로 두면 안 된다.**
RATE_UP = "#4ade80"
RATE_DOWN = "#fb923c"
GOLD_PIP = "#4ade80"

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
        # ⚠ **줄 수를 플레이어 수로 잡으면 안 된다.** 원본 기본 구성이 472명이라
        # 순위표가 화면 왼쪽 전체를 덮는다. 상위 몇 명 + 내 줄만 보여준다.
        for _ in range(SCOREBOARD_ROWS):
            lbl = QLabel()
            box.addWidget(lbl)
            self._rows.append(lbl)
        self.me: int | None = None

    def refresh(self) -> None:
        st = self.state
        bar = ""
        if st.clock.cfg.enabled:
            need = st.clock.bar_tiles(st.elapsed, st.gmap.land_count)
            bar = f"   기준선 {need / max(1, st.gmap.land_count) * 100:.1f}%"
        self.clock_label.setText(f"{int(st.elapsed) // 60:02d}:{int(st.elapsed) % 60:02d}{bar}")

        order = sorted(st.players.values(), key=lambda p: -st.tiles(p.pid))
        # 상위권 밖이면 **내 줄을 맨 아래에 끼워 넣는다.** 안 그러면 판이 커진 뒤
        # 내가 몇 등인지 화면 어디에도 안 나온다.
        shown = order[:len(self._rows)]
        if self.me is not None and self.me in st.players:
            if all(p.pid != self.me for p in shown):
                shown = shown[:-1] + [st.players[self.me]]
        # 남는 줄을 비우는 코드는 두지 않는다 — `st.players` 는 판 중에 줄지 않고
        # QLabel 초기값이 이미 빈 문자열이라, 돌연변이로도 안 잡히는 죽은 줄이 된다.
        for lbl, p in zip(self._rows, shown):
            rank = order.index(p) + 1
            share = st.share(p.pid) * 100
            doomed = p.pid in st.clock.marked_at
            mark = " ☠" if doomed else ""
            colour = _swatch(p.pid)
            dim = "" if p.alive else "opacity: 0.4;"
            mine = " style=\"font-weight:bold\"" if p.pid == self.me else ""
            lbl.setText(
                f'<span{mine}><span style="opacity:.5">{rank}.</span> '
                f'<span style="color:{colour}">■</span> {p.name}{mark} '
                f'&nbsp;{share:4.1f}%&nbsp; <span style="opacity:.7">'
                f'{p.troops:,.0f}</span></span>')
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
        # 방향은 **직전 tick 의 증가율**과 견줘야 나온다(원본 `_lastTroopIncreaseRate`).
        self._last_rate = troop_rate(state.players[pid],
                                     state.tiles(pid)) if pid in state.players else 0.0

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        self.troops_label = QLabel()
        self.troops_label.setMinimumWidth(330)
        row.addWidget(self.troops_label)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(C.ATTACK_RATIO_MIN * 100), 100)
        self.slider.setValue(int(C.ATTACK_RATIO_HUMAN * 100))
        self.slider.setFixedWidth(240)
        self.slider.valueChanged.connect(self._on_slider)
        row.addWidget(self.slider)

        self.ratio_label = QLabel()
        self.ratio_label.setMinimumWidth(90)
        row.addWidget(self.ratio_label)
        row.addStretch(1)

        self.gold_label = QLabel()
        # `+N` 이 떴다 사라질 때 골드 숫자가 좌우로 흔들리지 않게 폭을 고정한다.
        self.gold_label.setMinimumWidth(190)
        row.addWidget(self.gold_label)
        self._on_slider(self.slider.value())

    def _on_slider(self, value: int) -> None:
        self.ratio_changed.emit(value / 100)
        self._label_ratio(value)

    def _label_ratio(self, value: int) -> None:
        """% 만 보여주면 그게 몇 명인지 모른다 — 원본도 실제 병력 수를 같이 쓴다."""
        p = self.state.players.get(self.pid)
        n = p.troops * value / 100 if p else 0.0
        self.ratio_label.setText(f"공격 {value}% <b>{n:,.0f}</b>")

    def nudge_ratio(self, delta: float) -> None:
        """T/Y 로 10%p 씩. 1% 에서 올릴 때는 11% 가 아니라 10% 로 붙인다.

        원본 주석 그대로 — 최저값에서 한 칸 올리면 눈금과 어긋나기 때문이다."""
        cur = self.slider.value() / 100
        new = cur + delta
        # 상·하한은 슬라이더 범위가 이미 잡는다(`setValue` 가 클램프한다).
        # 여기서 또 막으면 돌연변이로도 안 잡히는 죽은 코드가 된다.
        if abs(new - 0.11) < 1e-9 and abs(cur - C.ATTACK_RATIO_MIN) < 1e-9:
            new = 0.10
        self.slider.setValue(round(new * 100))

    def refresh(self) -> None:
        p = self.state.players.get(self.pid)
        if p is None:
            return
        # 탈락하면 통째로 숨긴다(원본 `ControlPanel.tick` 의 `setVisibile(false)`).
        # ⚠ 숨기지 않으면 **죽은 사람에게 증가율이 계속 뜬다** — 병력 0에 최저
        # 증가량이 붙어 `+100/s` 가 찍히는데, 관전 중에 그건 거짓 신호다.
        self.setVisible(p.alive)
        if not p.alive:
            return
        tiles = self.state.tiles(self.pid)
        cap = p.max_troops(tiles)
        pct = p.troops / cap * 100 if cap else 0
        rate = troop_rate(p, tiles)
        colour = RATE_UP if rate_rising(rate, self._last_rate) else RATE_DOWN
        self._last_rate = rate
        self.troops_label.setText(
            f"병력 <b>{p.troops:,.0f}</b> / {cap:,.0f} ({pct:.0f}%) "
            f'<span style="color:{colour}">+{rate:,.0f}/s</span>')
        pip = gold_pip(self.state, self.pid)
        gain = ("" if pip is None else
                f' <span style="color:{GOLD_PIP}">+{pip:,.0f}</span>')
        self.gold_label.setText(f"골드 <b>{p.gold:,}</b>{gain}")
        self._label_ratio(self.slider.value())


class ImmunityBar(QWidget):
    """화면 맨 위 7px 진행바 — 스폰 면역이 얼마나 남았는지.

    원본 `ImmunityTimer.ts` 도 같은 자리·같은 두께다. 면역을 규칙에만 넣고 화면에
    안 보이면, 공격이 왜 거부되는지 알 방법이 없다 — 방사형 메뉴의 회색 항목은
    타일을 눌러야 보이지만 이건 항상 보인다.
    """

    HEIGHT = 7

    def __init__(self, state: GameState, pid: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.state = state
        self.pid = pid
        self.ratio = 0.0
        self.setFixedHeight(self.HEIGHT)

    def refresh(self) -> None:
        left = C.SPAWN_IMMUNITY_TICKS - self.state.tick_count
        if left <= 0 or not self.state.is_immune(self.pid):
            self.hide()
            return
        self.ratio = left / C.SPAWN_IMMUNITY_TICKS
        self.show()
        self.update()

    def paintEvent(self, _e) -> None:
        from PyQt6.QtGui import QColor, QPainter
        q = QPainter(self)
        q.fillRect(self.rect(), QColor(16, 20, 28, 160))
        q.fillRect(0, 0, int(self.width() * self.ratio), self.height(),
                   QColor(96, 200, 255))
        q.end()
