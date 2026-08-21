"""이모지 — AI 가 사람에게 말을 거는 유일한 통로.

이식 누락이다. 원본에서 이건 장식이 아니라 **규칙**이다:

- 사람이 🖕 를 보내면 상대의 관계가 **−100** 움직인다. 사람이 AI 관계를 바꾸는
  수단 중 유일하게 공짜다(공격은 병력을, 기부는 골드를 쓴다).
- AI 는 **사람에게만** 보낸다(`shouldSendEmoji` : 받는 쪽이 Human 이 아니면 false).
  AI 끼리는 주고받지 않는다 — 그래서 화면에 뜨는 이모지는 전부 나에게 온 말이다.
- 봇(Bot)은 아예 안 보낸다. Nation 만 보낸다.

출처: `NationEmojiBehavior.ts` · `PlayerImpl.canSendEmoji` · `Util.ts :: emojiTable`
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import constants as C
from .relations import Relation

# 사람이 고를 수 있는 판(`emojiTable`). 12행 × 5열 그대로 옮긴다 —
# 줄 수가 바뀌면 UI 격자도 같이 바뀌므로 여기가 유일한 출처다.
EMOJI_TABLE: tuple[tuple[str, ...], ...] = (
    ("😀", "😊", "🥰", "😇", "😎"),
    ("😞", "🥺", "😭", "😱", "😡"),
    ("😈", "🤡", "🥱", "🫡", "🖕"),
    ("👋", "👏", "✋", "🙏", "💪"),
    ("👍", "👎", "🫴", "🤌", "🤦"),
    ("🤝", "🆘", "🕊️", "🏳️", "⏳"),
    ("🔥", "💥", "💀", "☢️", "⚠️"),
    ("↖️", "⬆️", "↗️", "👑", "🥇"),
    ("⬅️", "🎯", "➡️", "🥈", "🥉"),
    ("↙️", "⬇️", "↘️", "❤️", "💔"),
    ("💰", "⚓", "⛵", "🏡", "🛡️"),
    ("🏭", "🚂", "❓", "🐔", "🐀"),
)

# AI 가 쓰는 묶음들. 상황마다 다른 통을 쓴다.
LOVE = ("❤️", "😊", "🥰")
CONFUSED = ("❓", "🤡")
GOT_INSULTED = ("🖕", "😡", "🤡", "😞", "😭")
AGGRESSIVE_ATTACK = ("😈",)
ATTACK = ("😡",)
NUKE = ("☢️", "💥")
HANDSHAKE = ("🤝",)
DONATION_OK = ("👍",)
DONATION_TOO_SMALL = ("❓", "🥱")
OVERWHELMED = ("💀", "🆘", "😱", "🥺", "😭", "😞", "🫡", "👋")

# 사람이 보낸 이모지에 관계가 얼마나 움직이는가.
INSULT = "🖕"
CLOWN = "🤡"
PEACEFUL = ("🕊️", "🏳️", "❤️", "🥰", "👏")


def relation_delta(emoji: str, difficulty: str) -> float:
    """사람이 보낸 이모지 하나가 상대의 눈을 얼마나 바꾸는가.

    ⚠ **좋은 이모지는 easy 에서만 통한다.** medium 이상에서 +15 를 주면 쿨다운마다
    ❤️ 를 눌러 관계를 공짜로 살 수 있다 — 원본이 난이도로 막아 둔 이유다.
    """
    if emoji == INSULT:
        return C.REL_EMOJI_INSULT
    if emoji == CLOWN:
        return C.REL_EMOJI_CLOWN
    if emoji in PEACEFUL and difficulty == "easy":
        return C.REL_EMOJI_PEACEFUL
    return 0.0


def reply_to(emoji: str, rng: random.Random,
             sender_relation: Relation) -> str | None:
    """AI 가 뭐라고 되받는가. 답할 말이 없으면 None.

    좋은 이모지에 대한 답이 **보내는 쪽을 어떻게 보느냐**로 갈린다 — 사이가 나쁘면
    사랑이 아니라 물음표가 돌아온다.
    """
    if emoji == INSULT:
        return rng.choice(GOT_INSULTED)
    if emoji == CLOWN:
        return rng.choice(CONFUSED)
    if emoji in PEACEFUL:
        pool = LOVE if sender_relation >= Relation.NEUTRAL else CONFUSED
        return rng.choice(pool)
    return None


@dataclass
class Emojis:
    """누가 누구에게 언제 보냈는지. 쿨다운 두 개를 따로 센다.

    - `canSendEmoji` 5초 — 같은 상대에게 도배하지 못하게
    - `shouldSendEmoji` 30초 — **AI 가 먼저 말을 거는** 주기. 이게 없으면
      AI 가 매 판단마다 말을 걸어 화면이 이모지로 덮인다
    """

    sent_at: dict[tuple[int, int], int] = field(default_factory=dict)
    ai_spoke_at: dict[tuple[int, int], int] = field(default_factory=dict)

    def can_send(self, sender: int, to: int, tick: int) -> bool:
        if sender == to:
            return False
        last = self.sent_at.get((sender, to))
        return last is None or tick - last >= C.EMOJI_COOLDOWN_TICKS

    def record(self, sender: int, to: int, tick: int) -> None:
        self.sent_at[(sender, to)] = tick

    def ai_may_speak(self, sender: int, to: int, tick: int) -> bool:
        """AI 가 먼저 말을 걸어도 되는가. **되면 그 자리에서 시간을 찍는다.**

        원본 `shouldSendEmoji` 도 확인과 기록을 같이 한다 — 나눠 두면 호출부가
        기록을 잊어 30초 제한이 조용히 사라진다.
        """
        last = self.ai_spoke_at.get((sender, to), -C.EMOJI_AI_INTERVAL_TICKS)
        if tick - last <= C.EMOJI_AI_INTERVAL_TICKS:
            return False
        self.ai_spoke_at[(sender, to)] = tick
        return True
