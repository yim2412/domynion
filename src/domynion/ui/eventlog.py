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
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from ..core import constants as C
from ..core.engine import GameState
from ..core.events import Category, Event, EventKind
from .numbers import render_number, render_troops
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
        return f"{who} 가 공격 (병력 {render_troops(e.amount)})"
    if k is EventKind.NAVAL_INVASION_INBOUND:
        return f"{who} 의 상륙 부대 (병력 {render_troops(e.amount)})"
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
    if k is EventKind.GOLD_FROM_CONQUEST:
        return f"{who} 에게서 {int(e.amount):,} 골드 노획"
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
    if k is EventKind.NUKES_CANCELLED_SENT:
        return f"{who} 와 동맹 — 그쪽으로 가던 핵 {int(e.amount)}발이 사라졌다"
    if k is EventKind.NUKES_CANCELLED_RECEIVED:
        return f"{who} 와 동맹 — 나에게 오던 핵 {int(e.amount)}발이 사라졌다"
    if k is EventKind.RENEW_ALLIANCE:
        return f"{who} 가 동맹 연장을 원한다 — 그 땅을 클릭해 동의"
    if k is EventKind.ATTACK_FAILED:
        # ⚠ 여기만 `who` 가 상대가 아니다(§5.67). 내 배가 다 나가 있는 것이므로
        # 상대가 없다 — `other` 없이 오는 유일한 소식이라 표 순서를 탄다.
        return f"배가 다 나가 있다 (최대 {int(e.amount)}척) — 돌아와야 또 띄운다"
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
        # **내 배신 디버프가 언제 끝나는가**(원본 `renderBetrayalDebuffTimer`).
        # 깃발(🗡)은 *지금 배신자다* 만 말한다 — 방어가 절반인 동안 **언제까지인지**
        # 를 모르면 반격 시점을 못 잡는다. `traitor_remaining` 은 이미 있었는데
        # **읽는 곳이 0** 이었다.
        self.debuff = QLabel("")
        self.debuff.setStyleSheet("color: #e0c060;")
        self.debuff.hide()
        box.addWidget(self.debuff)
        self._rows = []
        for _ in range(rows):
            lbl = QLabel("")
            box.addWidget(lbl)
            self._rows.append(lbl)

    def refresh(self) -> None:
        st = self.state
        # ⚠ `recent` 가 아니라 `feed` 다 — 만료·티어가 거기 있다(§5.104).
        # 요격·격침된 위협의 경고는 그 자리에서 지운다(`unitGone`) — **`feed` 에
        # 넣지 않는 이유**는 `EventLog` 가 판을 안 보기 때문이다(core 는 이벤트를
        # 쌓기만 한다). 자리를 넉넉히 받아 거른 뒤 줄 수만큼 자른다.
        left = st.diplomacy.traitor_remaining(self.me, st.tick_count)
        self.debuff.setVisible(left > 0)
        if left > 0:
            self.debuff.setText(f"🗡 배신 페널티 {left * C.TICK_DT:.0f}초 남음 "
                                f"— 방어가 절반이다")
        rows = len(self._rows)
        events = [e for e in st.log.feed(self.me, st.tick_count)
                  if st.threat_still_inbound(e)][:rows]
        for lbl, e in zip(self._rows, events):
            secs = int((st.tick_count - e.tick) * 0.1)
            colour = CATEGORY_COLOUR[e.category]
            lbl.setText(f'<span style="color:{colour}">●</span> '
                        f'{describe(st, e, self.me)} '
                        f'<span style="opacity:.45">{secs}초 전</span>')
        for lbl in self._rows[len(events):]:
            lbl.setText("")
        # ⚠ 디버프만 있고 소식이 없어도 패널은 떠 있어야 한다 — 그 줄이
        # 사라지면 페널티가 언제 끝나는지가 다시 화면에서 없어진다.
        self.setVisible(bool(events) or left > 0)


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
        # 각 줄 = 설명 + 버튼 하나. **버튼의 뜻이 줄마다 다르다** — 내 공격이면
        # 퇴각(✕), 들어오는 공격이면 **맞받아치기**(⚔). 원본도 같은 자리
        # (`ml-auto`)에 두 버튼을 갈아 끼운다(`AttacksDisplay`). 전에는 여기
        # 주석이 *"버튼은 내 공격에만 뜬다"* 였는데, 그건 퇴각만 옮겼을 때의
        # 이야기고 원본에는 그 자리에 **다른** 버튼이 있었다(§5.100 후보 둘).
        self._rows: list[tuple[QLabel, QPushButton]] = []
        for _ in range(6):
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(6)
            lbl = QLabel("")
            btn = QPushButton("✕")
            btn.setFixedSize(18, 16)
            btn.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,20); border: none;"
                " border-radius: 3px; color: #e08a7a; font-size: 10px; }"
                "QPushButton:hover { background: rgba(224,138,122,90); }")
            btn.hide()
            line.addWidget(lbl)
            line.addStretch(1)
            line.addWidget(btn)
            box.addLayout(line)
            self._rows.append((lbl, btn))

    def refresh(self) -> None:
        st = self.state
        # 한 줄 = (화살표, 상대, 병력, 색, 버튼 글자, 명령, 툴팁).
        # **명령을 콜러블로 들고 다닌다** — 퇴각만 해도 육상 공격과 상륙 부대가
        # 서로 다른 엔진 함수를 부르고, 여기에 맞받아치기까지 붙는다. 줄마다
        # 종류를 다시 따지면 이 아래가 분기투성이가 된다.
        me = st.players.get(self.me)
        lines = []
        for a in st.attacks:
            if a.attacker == self.me:
                foe = st.players.get(a.target) if a.target is not None else None
                mark = " (물러나는 중)" if a.retreating else ""
                lost = a.troops * C.RETREAT_MALUS if a.target is not None else 0.0
                lines.append(("→", (foe.name if foe else "중립") + mark, a.troops,
                              "#8fd6f0", "✕",
                              None if a.retreating
                              else (lambda x=a: st.order_retreat(self.me, x)),
                              f"퇴각 — {lost:,.0f} 손실" if lost
                              else "퇴각 — 손실 없음"))
            elif a.target == self.me:
                foe = st.players.get(a.attacker)
                # ⚠ **봇의 공격은 안 띄운다**(원본 `AttacksDisplay` 가
                # `t !== PlayerType.Bot` 으로 자른다). 이 패널은 여섯 줄뿐인데
                # 판에 봇이 400이라, 봇 줄이 자리를 채우면 **사람이 반응해야 하는
                # 나라의 공격이 목록 밖으로 밀린다.** 봇은 늘 국경을 긁고 있어서
                # 알림 가치가 낮고, 그 사실은 지도의 전선 숫자(§5.99)가 이미
                # 보여 준다.
                if foe is not None and foe.is_bot:
                    continue
                # **맞받아치기**(`handleRetaliate`). 보낼 병력은
                # `min(들어온 공격, 내 비율 × 내 병력)` 이다 — ⚠ **`min` 이
                # 규칙이다.** 비율만 쓰면 봇이 스무 명으로 긁는 줄에서도 슬라이더
                # 대로 전군이 나간다. 들어온 크기로 한 번 깎아야 "받아친다"가 된다.
                counter = min(a.troops, me.attack_troops()) if me else 0.0
                lines.append(("←", foe.name if foe else "?",
                              a.troops, "#e08a7a", "⚔",
                              None if (a.retreating or foe is None
                                       or counter < C.ATTACK_MIN_TROOPS)
                              else (lambda t=a.attacker, n=counter:
                                    st.launch_attack_troops(self.me, t, n)),
                              f"맞받아치기 — {counter:,.0f} 보낸다"))
        for b in st.boats:
            if b.owner == self.me:
                mark = " (돌아오는 중)" if b.retreating else ""
                blost = b.troops * C.BOAT_RETREAT_MALUS_PCT
                lines.append(("⛵→", "상륙 중" + mark, b.troops, "#8fd6f0", "✕",
                              None if b.retreating
                              else (lambda x=b: st.order_boat_retreat(self.me, x)),
                              f"퇴각 — {blost:,.0f} 손실" if blost
                              else "퇴각 — 손실 없음"))
            elif b.target == self.me:
                foe = st.players.get(b.owner)
                # ⚠ **오는 배에는 맞받아치기가 없다.** 원본이 그 버튼을
                # `incomingAttacks` 에만 단다 — 배는 아직 내 땅에 안 닿았고,
                # 주인의 본토는 바다 건너라 육상 공격이 성립하지 않는다.
                lines.append(("⛵←", foe.name if foe else "?",
                              b.troops, "#e08a7a", "", None, ""))

        lines = lines[:len(self._rows)]
        for (lbl, btn), (arrow, who, troops, colour, mark, cmd, tip) in zip(
                self._rows, lines):
            lbl.setText(f'<span style="color:{colour}">{arrow}</span> {who} '
                        f'<span style="opacity:.75">{render_troops(troops)}</span>')
            btn.setVisible(cmd is not None)
            if cmd is not None:
                btn.setText(mark)
                btn.setToolTip(tip)
                try:
                    btn.clicked.disconnect()
                except TypeError:
                    pass          # 연결이 없으면 그냥 넘어간다
                btn.clicked.connect(lambda _=False, f=cmd: f())
        for lbl, btn in self._rows[len(lines):]:
            lbl.setText("")
            btn.hide()
        self.setVisible(bool(lines))


class AlertBanner(QLabel):
    """놓치면 안 되는 것 — 핵·상륙·둠스데이, 그리고 **큰 육상 공격과 배신**.

    뒤의 둘이 원본 `AlertFrame` 이다(§5.109). 우리에게는 통째로 없었다 —
    `URGENT` 다섯 종에 육상 공격이 없어서, 봇 400이 국경을 긁는 판에서
    **나라의 큰 공격이 아무 경고도 없이** 들어왔다.

    ⚠ **띄우지 않는 조건 넷이 규칙의 본체다.** 원본이 그 넷을 안 두면 화면이
    계속 번쩍여 아무도 안 보게 된다 — 경고를 *더* 띄우는 것이 아니라
    **덜 띄우는 것**이 이 파일의 규칙이다.
    """

    def __init__(self, state: GameState, me: int, parent: QWidget | None = None):
        super().__init__("", parent)
        self.state = state
        self.me = me
        self._until = -1
        # 원본 `seenAttackIds` · `lastAlertTick` · `outgoingAttackTicks`.
        #
        # ⚠ **`id()` 를 키로 쓰면 안 된다.** 처음에 `set[int]` 에 `id(a)` 를
        # 담았는데, 파이썬은 해제된 객체의 주소를 **재사용한다** — 앞 공격이
        # 사라진 자리에 새 공격이 앉으면 "이미 본 것"이 돼 경고가 통째로
        # 사라진다. 단독 실행에서는 재현이 안 되고 **전체 스위트에서만**
        # 나왔다(쿨다운 변이가 그때만 살아남아 잡혔다).
        # 객체를 그대로 들고 있으면 참조가 살아 있어 주소가 재사용되지 않는다.
        # `Attack` 은 `@dataclass` 라 unhashable 이므로 리스트다 — 나에게
        # 들어오는 공격 수라 짧다.
        self._seen: list = []
        self._last_alert = -1
        self._i_attacked: dict[int, int] = {}
        self.setStyleSheet(
            "color:#fff; background: rgba(150, 40, 34, 235); padding: 9px 18px;"
            "border-radius: 8px; font-size: 17px; font-weight: bold;")
        self.hide()

    def refresh(self) -> None:
        st = self.state
        fresh = [e for e in st.log.urgent_for(self.me, st.tick_count - 3)]
        text = "⚠ " + describe(st, fresh[-1], self.me) if fresh else None
        if text is None:
            text = self._land_alert()
        if text is not None:
            self.setText(text)
            self.adjustSize()
            self._until = st.tick_count + 40        # 4초
            self._last_alert = st.tick_count
            self.show()
            self.raise_()
        elif st.tick_count > self._until:
            self.hide()

    # --- 원본 `AlertFrame` -------------------------------------------------

    def _land_alert(self) -> str | None:
        """육상 공격·배신 경고. 띄울 것이 없으면 None."""
        st, me = self.state, self.me
        mine = st.players.get(me)
        if mine is None or not mine.alive:
            self._seen.clear()
            self._i_attacked.clear()
            self._last_alert = -1
            return None
        self._track_my_attacks()
        # **배신은 필터를 안 탄다**(`onBrokeAllianceUpdate` 는 쿨다운도 안 본다).
        # 동맹이 깨진 것은 드물고, 방어가 절반이 되는 쪽은 상대다.
        for e in st.log.recent(who=me, count=8):
            if (e.kind is EventKind.ALLIANCE_BROKEN
                    and e.tick >= st.tick_count - 3 and e.other is not None):
                foe = st.players.get(e.other)
                return f"⚠ {foe.name if foe else '?'} 가 동맹을 깼다"

        cooling = (self._last_alert >= 0
                   and st.tick_count - self._last_alert < C.ALERT_COOLDOWN_TICKS)
        floor = mine.troops / C.ALERT_MIN_TROOPS_DIVISOR
        out: str | None = None
        for a in st.attacks:
            if (a.target != me or a.retreating
                    or any(a is seen for seen in self._seen)):
                continue
            self._seen.append(a)        # **본 것은 다시 안 띄운다**(띄웠든 아니든)
            foe = st.players.get(a.attacker)
            if foe is None or foe.is_bot:
                continue                # 봇은 늘 긁는다
            hit = self._i_attacked.get(a.attacker)
            if hit is not None and (st.tick_count - hit
                                    < C.ALERT_RETALIATION_WINDOW_TICKS):
                continue                # **내가 먼저 쳤다** — 반격은 놀랄 일이 아니다
            if a.troops < floor:
                continue                # 내 병력의 1/5 미만
            if not cooling and out is None:
                out = f"⚠ {foe.name} 의 공격 — {render_troops(a.troops)}"
        # 목록에서 빠진 공격은 잊는다(원본도 `activeAttackIds` 로 정리한다).
        self._seen = [s for s in self._seen
                      if any(s is a for a in st.attacks)]
        return out

    def _track_my_attacks(self) -> None:
        """내가 누구를 언제 쳤는가 — 반격 판정의 재료(`trackOutgoingAttacks`).

        ⚠ **시각을 덮어쓰지 않는다.** 창이 살아 있는 동안 같은 상대를 또 치면
        원본은 시각을 **그대로 둔다** — 계속 치는 것으로 창을 무한히 늘릴 수
        없게 하려는 것이다."""
        st, now = self.state, self.state.tick_count
        for a in st.attacks:
            if a.attacker != self.me or a.target is None or a.retreating:
                continue
            was = self._i_attacked.get(a.target)
            if was is None or now - was >= C.ALERT_RETALIATION_WINDOW_TICKS:
                self._i_attacked[a.target] = now
        for pid, t in list(self._i_attacked.items()):
            if now - t > C.ALERT_RETALIATION_WINDOW_TICKS:
                del self._i_attacked[pid]
