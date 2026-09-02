"""판이 끝났을 때 — 원본 `WinModal.ts` + `GameStatsModal`.

지금까지는 배너 한 줄뿐이라 **내가 죽은 줄도 몰랐다.** 사람이 아무것도 안 하고
있으면 2분여 만에 탈락하는데 화면에 아무 변화가 없었다.

원본이 하는 두 가지를 그대로 옮긴다:

1. **탈락하면 판이 안 끝나도 그 자리에서 뜬다**(`hasShownDeathModal`). 끝날 때까지
   기다리면 내가 언제 왜 죽었는지 알 수 없다.
2. 닫으면 **관전이 이어진다.** 원본도 죽은 뒤 판을 계속 볼 수 있다.

통계 열은 `StatsConstants.ts :: COLUMN_IDS` 에서 우리에게 있는 것만 골랐다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from ..core.engine import GameState
from ..core.units import UnitType
from . import palette as P

_STYLE = """
QWidget#endpanel { background: rgba(14, 17, 24, 244); border-radius: 10px; }
QLabel { color: #e8e8ec; }
QLabel#title { font-size: 26px; font-weight: bold; }
QLabel#sub { color: #b9b9c4; }
QLabel#head { color: #8a8a96; font-size: 11px; }
QPushButton {
    background: rgba(255,255,255,22); border: none; border-radius: 6px;
    padding: 7px 20px; color: #e8e8ec;
}
QPushButton:hover { background: rgba(255,255,255,50); }
"""

# `COLUMN_IDS` 중 우리가 실제로 셀 수 있는 것만. 순서가 화면 순서다.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("", "rank"), ("나라", "player"), ("영토", "tiles"), ("병력", "troops"),
    ("골드", "gold"), ("도시", "cities"), ("항구", "ports"),
    ("사일로", "silos"), ("동맹", "allies"), ("배신", "betrayals"),
)

# 표에 몇 줄을 쓸 것인가. 마지막 한 줄은 상위권 밖일 때 **내 자리**로 쓴다.
TABLE_ROWS = 15

_UNIT_OF = {"cities": UnitType.CITY, "ports": UnitType.PORT,
            "silos": UnitType.MISSILE_SILO}


def _cell(st: GameState, pid: int, key: str, rank: int) -> str:
    p = st.players[pid]
    if key == "rank":
        return f"{rank}."
    if key == "player":
        return p.name + ("" if p.alive else "  ✝")
    if key == "tiles":
        return f"{st.tiles(pid):,} ({st.share(pid) * 100:.1f}%)"
    if key == "troops":
        return f"{p.troops:,.0f}"
    if key == "gold":
        return f"{p.gold:,}"
    if key == "allies":
        return str(sum(1 for q in st.players if q != pid
                       and st.diplomacy.allied(pid, q)))
    if key == "betrayals":
        return str(st.diplomacy.betrayals.get(pid, 0))
    u = _UNIT_OF.get(key)
    return str(p.units.owned(u)) if u else ""


class EndModal(QWidget):
    """승리·패배·탈락 화면. `closed` 를 누르면 관전으로 돌아간다."""

    def __init__(self, state: GameState, me: int,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("endpanel")
        self.setStyleSheet(_STYLE)
        self.state = state
        self.me = me
        self._shown_death = False      # 탈락 화면은 판마다 한 번만
        self._shown_end = False        # 종료 화면도 마찬가지

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 20, 24, 18)
        box.setSpacing(10)
        self.title = QLabel(objectName="title")
        self.sub = QLabel(objectName="sub")
        box.addWidget(self.title)
        box.addWidget(self.sub)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(4)
        box.addLayout(self.grid)

        row = QHBoxLayout()
        row.addStretch(1)
        self.button = QPushButton("계속 보기")
        self.button.clicked.connect(self.hide)
        row.addWidget(self.button)
        box.addLayout(row)
        self.hide()

    # --- 언제 뜨는가 ------------------------------------------------------

    def check(self) -> bool:
        """떠야 하면 띄운다. **탈락은 판이 끝나기 전에도 뜬다.**

        ⚠ 두 화면 모두 **판마다 한 번만** 뜬다. 조건만 보고 매 tick 띄우면 닫아도
        다음 프레임에 다시 떠서 관전이 불가능해진다.
        """
        st = self.state
        me = st.players.get(self.me)
        if st.over and not self._shown_end:
            self._shown_end = True
            self._fill_for_end()
            return True
        if (not st.over and me is not None and not me.alive
                and not self._shown_death):
            self._shown_death = True
            self._fill_for_death()
            return True
        return False

    def _fill_for_end(self) -> None:
        st = self.state
        won = st.winner == self.me
        if st.winner is None:
            self.title.setText("무승부")
        elif won:
            self.title.setText("승리")
        else:
            self.title.setText(f"패배 — {st.players[st.winner].name} 의 승리")
        kind = st.victory.value if st.victory else ""
        self.sub.setText(f"{kind} · {int(st.elapsed) // 60}분 "
                         f"{int(st.elapsed) % 60}초")
        self.button.setText("닫기")
        self._fill_table()
        self._present()

    def _fill_for_death(self) -> None:
        st = self.state
        self.title.setText("탈락")
        # **왜 죽었는지가 아니라 언제·어디까지 갔는지**를 보여준다. 死因은 소식
        # 로그에 이미 있다 — 여기서 되풀이하면 표가 읽히지 않는다.
        self.sub.setText(f"{int(st.elapsed) // 60}분 {int(st.elapsed) % 60}초 "
                         f"· 아직 {len(st.alive)}나라가 남아 있다")
        self.button.setText("계속 보기")
        self._fill_table()
        self._present()

    # --- 표 ---------------------------------------------------------------

    def _fill_table(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0).widget().deleteLater()
        st = self.state
        order = sorted(st.players, key=lambda pid: -st.tiles(pid))
        # ⚠ **전원을 그리면 안 된다.** 원본 기본 구성이 472명이라 표가 화면을 덮는다.
        # 상위권 + 내 줄만 남긴다 — 내가 몇 등이었는지가 이 화면의 요점이다.
        shown = order[:TABLE_ROWS]
        if self.me in st.players and self.me not in shown:
            shown = shown[:-1] + [self.me]

        for c, (head, _) in enumerate(COLUMNS):
            self.grid.addWidget(QLabel(head, objectName="head"), 0, c)
        for r, pid in enumerate(shown, start=1):
            for c, (_, key) in enumerate(COLUMNS):
                lbl = QLabel(_cell(st, pid, key, order.index(pid) + 1))
                if pid == self.me:
                    lbl.setStyleSheet("font-weight: bold;")
                elif not st.players[pid].alive:
                    lbl.setStyleSheet("color: #71717c;")
                if key == "player":
                    r_, g_, b_ = P.player_color(pid, st.players[pid].kind)
                    lbl.setStyleSheet(
                        lbl.styleSheet()
                        + f"color: rgb({r_},{g_},{b_});")
                self.grid.addWidget(lbl, r, c)

    def _present(self) -> None:
        self.adjustSize()
        par = self.parentWidget()
        if par is not None:
            self.move((par.width() - self.width()) // 2,
                      (par.height() - self.height()) // 2)
        self.show()
        self.raise_()
