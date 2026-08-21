"""이모지 판 — 원본 `EmojiTable.ts`.

방사형 메뉴에 넣을 수 없다(60개다). 원본도 별도 격자로 띄운다.

**이건 장식이 아니라 조작 수단이다.** 🖕 하나가 상대 관계를 −100 움직인다 —
사람이 AI 의 눈을 바꾸는 방법 중 유일하게 공짜다. 그래서 어떤 이모지가 무엇을
하는지 눌러 보기 전에 알 수 있어야 한다(`setToolTip`).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QGridLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from ..core import constants as C
from ..core.emoji import CLOWN, EMOJI_TABLE, INSULT, PEACEFUL
from ..core.engine import GameState

_STYLE = """
QWidget#emojipanel { background: rgba(16, 20, 28, 235); border-radius: 8px; }
QLabel { color: #e8e8ec; }
QPushButton {
    background: rgba(255,255,255,18); border: none; border-radius: 5px;
    font-size: 19px; padding: 2px;
}
QPushButton:hover { background: rgba(255,255,255,55); }
QPushButton:disabled { color: rgba(255,255,255,60); }
"""


def effect_hint(emoji: str, difficulty: str) -> str:
    """이 이모지가 관계를 어떻게 하는지 사람 말로.

    `relation_delta` 를 그대로 부르지 않고 따로 쓴다 — 값이 0 인 이유가
    "효과가 없는 이모지"인지 "난이도 때문에 안 통하는 것"인지 구분해야 한다.
    """
    if emoji == INSULT:
        return f"관계 {C.REL_EMOJI_INSULT:+.0f} — 돌이키기 어렵다"
    if emoji == CLOWN:
        return f"관계 {C.REL_EMOJI_CLOWN:+.0f}"
    if emoji in PEACEFUL:
        if difficulty == "easy":
            return f"관계 {C.REL_EMOJI_PEACEFUL:+.0f}"
        return "이 난이도에서는 관계가 움직이지 않는다"
    return "관계에 영향 없음"


class EmojiTable(QWidget):
    """격자 하나. 고르면 `picked` 로 이모지를 넘기고 스스로 닫힌다."""

    picked = pyqtSignal(str)

    def __init__(self, state: GameState, me: int,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("emojipanel")
        self.setStyleSheet(_STYLE)
        self.state = state
        self.me = me
        self.target: int | None = None

        box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 10)
        self.title = QLabel()
        box.addWidget(self.title)

        grid = QGridLayout()
        grid.setSpacing(4)
        self._buttons: list[QPushButton] = []
        for r, row in enumerate(EMOJI_TABLE):
            for c, ch in enumerate(row):
                b = QPushButton(ch)
                b.setFixedSize(38, 34)
                b.setToolTip(f"{ch}  {effect_hint(ch, state.difficulty)}")
                b.clicked.connect(lambda _=False, e=ch: self._pick(e))
                grid.addWidget(b, r, c)
                self._buttons.append(b)
        box.addLayout(grid)
        self.hide()

    def open_for(self, target: int) -> None:
        them = self.state.players.get(target)
        if them is None:
            return
        self.target = target
        self.title.setText(f"<b>{them.name}</b> 에게 한마디")
        self.adjustSize()
        self.show()
        self.raise_()

    def refresh(self) -> None:
        """쿨다운 중이면 못 누른다. **왜 안 눌리는지 제목에 쓴다.**"""
        if not self.isVisible() or self.target is None:
            return
        st = self.state
        left = 0
        last = st.emojis.sent_at.get((self.me, self.target))
        if last is not None:
            left = max(0, C.EMOJI_COOLDOWN_TICKS - (st.tick_count - last))
        ready = left == 0
        for b in self._buttons:
            b.setEnabled(ready)
        them = st.players.get(self.target)
        name = them.name if them else "?"
        self.title.setText(
            f"<b>{name}</b> 에게 한마디"
            if ready else f"<b>{name}</b> — {left * C.TICK_DT:.1f}초 뒤에 가능")

    def _pick(self, e: str) -> None:
        if self.target is not None:
            self.picked.emit(e)
        self.hide()
