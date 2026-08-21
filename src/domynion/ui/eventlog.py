"""이벤트 표시 — 원본 `EventsDisplay` · `AlertFrame` · `AttacksDisplay`.

셋을 나눈 이유가 있다:

- **로그**(`EventsDisplay`) — 지나간 일. 놓쳐도 되지만 흐름을 읽는 데 쓴다
- **경보**(`AlertFrame`) — 놓치면 안 되는 것. 핵이 날아오는 중 같은 것
- **공격 현황**(`AttacksDisplay`) — 지금 진행 중인 것. 로그가 아니라 **상태**다

로그에 다 몰아넣으면 급한 것이 흘러가 버리고, 진행 중인 것은 시작할 때 한 줄 뜨고
끝나서 지금 어떤지 알 수 없다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..core.engine import GameState
from ..core.events import Category, Event, EventKind
from . import palette as P

_PANEL = """
QWidget#panel { background: rgba(16, 20, 28, 200); border-radius: 6px; }
QLabel { color: #e8e8ec; }
"""

CATEGORY_COLOUR = {
    Category.ATTACK: "#e08a7a",
    Category.NUKE: "#ff8a6a",
    Category.ALLIANCE: "#b9a3e0",
    Category.TRADE: "#8fd6f0",
    Category.SYSTEM: "#d8d8dd",
    Category.CHAT: "#f0d68f",
}


def describe(st: GameState, e: Event, me: int) -> str:
    """이벤트 한 줄. **누가 무엇을 했는지**가 앞에 와야 훑어 읽힌다."""
    other = st.players.get(e.other) if e.other is not None else None
    who = other.name if other else "?"
    k = e.kind
    if k is EventKind.ATTACK_REQUEST:
        return f"{who} 가 공격 (병력 {e.amount:,.0f})"
    if k is EventKind.NAVAL_INVASION_INBOUND:
        return f"{who} 의 상륙 부대 (병력 {e.amount:,.0f})"
    if k in (EventKind.NUKE_INBOUND, EventKind.HYDROGEN_BOMB_INBOUND,
             EventKind.MIRV_INBOUND):
        return f"{who} 가 {k.value}"
    if k is EventKind.NUKE_DETONATED:
        return f"{who} 의 핵 폭발 — {int(e.amount):,}칸"
    if k is EventKind.SAM_HIT:
        return f"SAM 이 {who} 의 핵을 요격"
    if k is EventKind.SAM_MISS:
        return f"{who} 의 SAM 에 요격당함"
    if k is EventKind.CHAT:
        return f"{who} : {e.text}"
    if k is EventKind.CONQUERED_PLAYER:
        gone = st.players.get(int(e.amount))
        return f"{who} 가 {gone.name if gone else '?'} 를 정복"
    if k is EventKind.CAPTURED_ENEMY_UNIT:
        return f"{who} 의 건물 {int(e.amount)}개를 노획"
    if k is EventKind.UNIT_DESTROYED:
        return f"{who} 가 내 {e.text} 격침"
    if k is EventKind.ALLIANCE_REQUEST:
        return f"{who} 가 동맹 요청 — 그 땅을 클릭해 수락"
    if k is EventKind.ALLIANCE_ACCEPTED:
        return f"{who} 와 동맹"
    if k is EventKind.ALLIANCE_REJECTED:
        return f"{who} 가 동맹 거절"
    if k is EventKind.ALLIANCE_BROKEN:
        return f"{who} 가 동맹 파기 — 배신"
    if k is EventKind.ALLIANCE_EXPIRED:
        return f"{who} 와의 동맹 만료"
    if k is EventKind.DONATION_SENT:
        return f"{who} 에게 보냄 ({e.amount:,.0f})"
    if k is EventKind.DONATION_RECEIVED:
        return f"{who} 에게서 받음 ({e.amount:,.0f})"
    if k is EventKind.DOOMSDAY_MARKED:
        return "둠스데이 — 기준선 아래다. 영토를 넓히지 않으면 사라진다"
    return k.value


class EventList(QWidget):
    """최근 이벤트 몇 줄. 지나간 일이라 작고 흐리게 둔다."""

    def __init__(self, state: GameState, me: int, rows: int = 7,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("panel")
        self.setStyleSheet(_PANEL)
        self.state = state
        self.me = me
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 7, 10, 7)
        box.setSpacing(1)
        self.title = QLabel("소식")
        self.title.setStyleSheet("font-weight: bold; opacity: .8;")
        box.addWidget(self.title)
        self._rows = []
        for _ in range(rows):
            lbl = QLabel("")
            box.addWidget(lbl)
            self._rows.append(lbl)

    def refresh(self) -> None:
        st = self.state
        events = st.log.recent(who=self.me, count=len(self._rows))
        for lbl, e in zip(self._rows, events):
            secs = int((st.tick_count - e.tick) * 0.1)
            colour = CATEGORY_COLOUR[e.category]
            lbl.setText(f'<span style="color:{colour}">●</span> '
                        f'{describe(st, e, self.me)} '
                        f'<span style="opacity:.45">{secs}초 전</span>')
        for lbl in self._rows[len(events):]:
            lbl.setText("")
        self.setVisible(bool(events))


class AttacksPanel(QWidget):
    """진행 중인 공격 — **로그가 아니라 상태다.**

    시작할 때 한 줄 뜨고 마는 게 아니라, 지금 몇 명이 나를 치고 있고 병력이 얼마나
    남았는지가 계속 보여야 대응할 수 있다."""

    def __init__(self, state: GameState, me: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("panel")
        self.setStyleSheet(_PANEL)
        self.state = state
        self.me = me
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 7, 10, 7)
        box.setSpacing(1)
        self.title = QLabel("전투")
        self.title.setStyleSheet("font-weight: bold; opacity: .8;")
        box.addWidget(self.title)
        self._rows = []
        for _ in range(6):
            lbl = QLabel("")
            box.addWidget(lbl)
            self._rows.append(lbl)

    def refresh(self) -> None:
        st = self.state
        lines = []
        for a in st.attacks:
            if a.attacker == self.me:
                foe = st.players.get(a.target) if a.target is not None else None
                lines.append(("→", foe.name if foe else "중립", a.troops, "#8fd6f0"))
            elif a.target == self.me:
                foe = st.players.get(a.attacker)
                lines.append(("←", foe.name if foe else "?", a.troops, "#e08a7a"))
        for b in st.boats:
            if b.owner == self.me:
                lines.append(("⛵→", "상륙 중", b.troops, "#8fd6f0"))
            elif b.target == self.me:
                foe = st.players.get(b.owner)
                lines.append(("⛵←", foe.name if foe else "?", b.troops, "#e08a7a"))

        lines = lines[:len(self._rows)]
        for lbl, (arrow, who, troops, colour) in zip(self._rows, lines):
            lbl.setText(f'<span style="color:{colour}">{arrow}</span> {who} '
                        f'<span style="opacity:.75">{troops:,.0f}</span>')
        for lbl in self._rows[len(lines):]:
            lbl.setText("")
        self.setVisible(bool(lines))


class AlertBanner(QLabel):
    """놓치면 안 되는 것 — 핵·상륙·둠스데이. 화면 가운데 위에 크게."""

    def __init__(self, state: GameState, me: int, parent: QWidget | None = None):
        super().__init__("", parent)
        self.state = state
        self.me = me
        self._until = -1
        self.setStyleSheet(
            "color:#fff; background: rgba(150, 40, 34, 235); padding: 9px 18px;"
            "border-radius: 8px; font-size: 17px; font-weight: bold;")
        self.hide()

    def refresh(self) -> None:
        st = self.state
        fresh = [e for e in st.log.urgent_for(self.me, st.tick_count - 3)]
        if fresh:
            e = fresh[-1]
            self.setText("⚠ " + describe(st, e, self.me))
            self.adjustSize()
            self._until = st.tick_count + 40        # 4초
            self.show()
            self.raise_()
        elif st.tick_count > self._until:
            self.hide()
