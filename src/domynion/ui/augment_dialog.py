"""증강 드래프트 창 — **원본에 없는 우리 계층**(`docs/design.md` §3).

정지마다 카드 3장이 뜨고 하나를 고른다. 같은 카드를 다시 고르면 레벨이 오른다.

⚠ **남은 시간 막대가 규칙의 일부다.** 판은 최대 10초만 멈춘다
(`AUGMENT_PICK_LIMIT_TICKS`) — 넘기면 엔진이 무작위로 골라 주고 재개한다.
그 사실이 화면에 없으면 사람은 *"고를 때까지 기다려 준다"* 고 믿고 있다가
엉뚱한 카드를 받는다. 스폰 면역 바(`ImmunityBar`)와 같은 이유다.

⚠ **이미 가진 카드는 현재 레벨과 다음 레벨을 같이 보여 준다.** 안 그러면
*"이미 있는 카드를 또 고르는 게 손해인가"* 를 화면에서 알 수 없다 — 드래프트의
핵심 판단이 그것이다(`docs/design.md`: *"뽑힌 것들 사이에서 방향을 잡아 간다"*).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from ..core import constants as C
from ..core.augments import describe
from ..core.engine import GameState

_STYLE = """
QWidget#augpanel { background: rgba(14, 17, 24, 246); border-radius: 10px; }
QLabel { color: #e8e8ec; }
QLabel#title { font-size: 22px; font-weight: bold; }
QLabel#sub { color: #b9b9c4; }
QPushButton#card {
    background: rgba(255,255,255,18); border: 1px solid rgba(255,255,255,40);
    border-radius: 8px; padding: 14px 12px; color: #e8e8ec; text-align: left;
}
QPushButton#card:hover { background: rgba(120,180,255,60); }
"""

# 남은 시간 막대. 스폰 면역 바와 같은 색 규칙을 쓴다.
_BAR_BG = "rgba(255,255,255,26)"
_BAR_FG = "#e0c060"


class TimeBar(QWidget):
    """남은 시간. `ratio` 가 1 → 0 으로 줄어든다."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.ratio = 1.0
        self.setFixedHeight(4)

    def paintEvent(self, _e) -> None:
        from PyQt6.QtGui import QColor, QPainter
        q = QPainter(self)
        q.fillRect(0, 0, self.width(), self.height(), QColor(255, 255, 255, 26))
        q.fillRect(0, 0, int(self.width() * max(0.0, self.ratio)),
                   self.height(), QColor(224, 192, 96))


class AugmentDialog(QWidget):
    """열려 있는 드래프트 하나. `refresh()` 가 열고 닫는다."""

    def __init__(self, state: GameState, me: int,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("augpanel")
        self.setStyleSheet(_STYLE)
        self.state = state
        self.me = me

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 20, 24, 18)
        box.setSpacing(10)
        self.title = QLabel("증강 선택", objectName="title")
        self.sub = QLabel(objectName="sub")
        box.addWidget(self.title)
        box.addWidget(self.sub)

        self.bar = TimeBar()
        box.addWidget(self.bar)

        self.row = QHBoxLayout()
        self.row.setSpacing(10)
        box.addLayout(self.row)
        # 카드 버튼은 **최대 장수만큼 미리 만든다.** 매번 만들고 지우면 이전
        # 버튼의 `clicked` 연결이 남아 엉뚱한 카드가 눌린다.
        self._cards: list[QPushButton] = []
        for _ in range(C.AUGMENT_CHOICES):
            b = QPushButton(objectName="card")
            b.setMinimumWidth(190)
            self.row.addWidget(b)
            self._cards.append(b)
        self.hide()

    def refresh(self) -> None:
        st = self.state
        if not st.augment_offer:
            self.hide()
            return
        left = max(0, C.AUGMENT_PICK_LIMIT_TICKS
                   - (st.tick_count - st.augment_opened_at))
        self.bar.ratio = left / C.AUGMENT_PICK_LIMIT_TICKS
        self.bar.update()
        self.sub.setText(
            f"{left * C.TICK_DT:.0f}초 안에 고른다 — "
            f"넘기면 무작위로 정해진다")
        owned = st.players[self.me].augments if self.me in st.players else {}
        for i, btn in enumerate(self._cards):
            if i >= len(st.augment_offer):
                btn.hide()
                continue
            aug = st.augment_offer[i]
            have = owned.get(aug.key, 0)
            nxt = have + 1
            # 이미 가진 카드는 **지금 → 다음**을 같이 쓴다. 이게 없으면
            # "또 고르는 게 손해인가"를 화면에서 알 수 없다.
            if have:
                text = (f"<b>{aug.name}</b> Lv{have} → Lv{nxt}<br>"
                        f"<span style='color:#b9b9c4'>{describe(aug, have)}"
                        f" → {describe(aug, nxt)}</span>")
            else:
                text = (f"<b>{aug.name}</b><br>"
                        f"<span style='color:#b9b9c4'>{describe(aug, 1)}</span>")
            # QPushButton 은 리치 텍스트를 안 쓰므로 라벨을 얹지 않고
            # 평문으로 옮긴다 — 굵기 대신 줄바꿈으로 나눈다.
            btn.setText(_plain(text))
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass
            btn.clicked.connect(
                lambda _=False, k=aug.key: self.state.choose_augment(k))
            btn.show()
        self._present()

    def _present(self) -> None:
        self.adjustSize()
        par = self.parentWidget()
        if par is not None:
            self.move((par.width() - self.width()) // 2,
                      (par.height() - self.height()) // 2)
        self.show()
        self.raise_()


def _plain(html: str) -> str:
    """아주 작은 태그 제거기. 카드 문구에만 쓰므로 파서를 들이지 않는다.

    ⚠ **`<br>` 을 먼저 개행으로 바꾼다.** 태그를 먼저 지우면 `<br>` 도 함께
    사라져 카드가 한 줄로 뭉친다(처음에 그렇게 썼다)."""
    text, skip, out = html.replace("<br>", "\n"), False, []
    for ch in text:
        if ch == "<":
            skip = True
        elif ch == ">":
            skip = False
        elif not skip:
            out.append(ch)
    return "".join(out)
