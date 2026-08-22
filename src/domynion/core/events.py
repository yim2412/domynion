"""이벤트 — 무슨 일이 일어났는지.

**규칙만 있고 알림이 없으면 판이 안 읽힌다.** 누가 나를 치는지, 핵이 날아오는지,
동맹 요청이 왔는지 모른 채 지도만 보고 있으면 반응할 수가 없다.

원본 `Game.ts :: MessageType` 을 그대로 옮겼다(22종). 분류(`MessageCategory`)도
같다 — 공격 · 핵 · 동맹 · 무역 · 채팅. 원본 `EventsDisplay` 가 그 분류로 거른다.

`core` 는 이벤트를 **쌓기만** 한다. 어떻게 보여줄지는 UI 몫이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventKind(Enum):
    ATTACK_FAILED = "공격 실패"
    ATTACK_CANCELLED = "공격 취소"
    ATTACK_REQUEST = "공격받음"
    CONQUERED_PLAYER = "정복"
    MIRV_INBOUND = "MIRV 접근"
    NUKE_INBOUND = "핵 접근"
    NUKE_DETONATED = "핵 폭발"
    HYDROGEN_BOMB_INBOUND = "수폭 접근"
    NAVAL_INVASION_INBOUND = "상륙 접근"
    SAM_MISS = "SAM 실패"
    SAM_HIT = "SAM 요격"
    CAPTURED_ENEMY_UNIT = "유닛 노획"
    UNIT_DESTROYED = "유닛 파괴"
    UNIT_DELETED = "유닛 철거"      # `unit_voluntarily_deleted` — 내가 스스로 지운 것
    ALLIANCE_ACCEPTED = "동맹 성립"
    ALLIANCE_REJECTED = "동맹 거절"
    ALLIANCE_REQUEST = "동맹 요청"
    ALLIANCE_BROKEN = "동맹 파기"
    ALLIANCE_EXPIRED = "동맹 만료"
    DONATION_SENT = "기부 보냄"
    DONATION_RECEIVED = "기부 받음"
    RENEW_ALLIANCE = "동맹 연장"
    CHAT = "이모지"
    DOOMSDAY_MARKED = "둠스데이 경고"     # 원본에는 없다 — 클락이 별도 UI 라서


class Category(Enum):
    ATTACK = "공격"
    NUKE = "핵"
    ALLIANCE = "동맹"
    TRADE = "무역"
    SYSTEM = "판"
    CHAT = "말"


CATEGORY: dict[EventKind, Category] = {
    EventKind.ATTACK_FAILED: Category.ATTACK,
    EventKind.ATTACK_CANCELLED: Category.ATTACK,
    EventKind.ATTACK_REQUEST: Category.ATTACK,
    EventKind.CONQUERED_PLAYER: Category.ATTACK,
    EventKind.NAVAL_INVASION_INBOUND: Category.ATTACK,
    EventKind.UNIT_DESTROYED: Category.ATTACK,
    EventKind.UNIT_DELETED: Category.SYSTEM,
    EventKind.CAPTURED_ENEMY_UNIT: Category.ATTACK,
    EventKind.MIRV_INBOUND: Category.NUKE,
    EventKind.NUKE_INBOUND: Category.NUKE,
    EventKind.HYDROGEN_BOMB_INBOUND: Category.NUKE,
    EventKind.NUKE_DETONATED: Category.NUKE,
    EventKind.SAM_MISS: Category.NUKE,
    EventKind.SAM_HIT: Category.NUKE,
    EventKind.ALLIANCE_ACCEPTED: Category.ALLIANCE,
    EventKind.ALLIANCE_REJECTED: Category.ALLIANCE,
    EventKind.ALLIANCE_REQUEST: Category.ALLIANCE,
    EventKind.ALLIANCE_BROKEN: Category.ALLIANCE,
    EventKind.ALLIANCE_EXPIRED: Category.ALLIANCE,
    EventKind.RENEW_ALLIANCE: Category.ALLIANCE,
    EventKind.DONATION_SENT: Category.TRADE,
    EventKind.DONATION_RECEIVED: Category.TRADE,
    EventKind.DOOMSDAY_MARKED: Category.SYSTEM,
    EventKind.CHAT: Category.CHAT,
}

# 이것들은 **놓치면 안 되는 것**이라 화면 가운데에 크게 띄운다.
URGENT = frozenset({
    EventKind.NUKE_INBOUND, EventKind.HYDROGEN_BOMB_INBOUND,
    EventKind.MIRV_INBOUND, EventKind.NAVAL_INVASION_INBOUND,
    EventKind.DOOMSDAY_MARKED,
})


@dataclass(frozen=True)
class Event:
    kind: EventKind
    tick: int
    who: int | None = None           # 이 이벤트를 봐야 하는 사람 (None = 모두)
    other: int | None = None         # 상대
    tile: int | None = None          # 지도에서 어디
    amount: float = 0.0
    text: str = ""

    @property
    def category(self) -> Category:
        return CATEGORY[self.kind]

    @property
    def urgent(self) -> bool:
        return self.kind in URGENT


@dataclass
class EventLog:
    """최근 이벤트를 들고 있는다.

    **무한히 쌓지 않는다.** 한 판에 수천 개가 나오는데(무역선만 판당 1,000척) 전부
    들고 있으면 메모리와 그리기 양쪽이 샌다."""

    limit: int = 400
    items: list[Event] = field(default_factory=list)

    def add(self, event: Event) -> None:
        self.items.append(event)
        if len(self.items) > self.limit:
            del self.items[:len(self.items) - self.limit]

    def recent(self, who: int | None = None, count: int = 8,
               category: Category | None = None) -> list[Event]:
        """최근 것부터. `who` 를 주면 그 사람이 봐야 하는 것만."""
        out = []
        for e in reversed(self.items):
            if who is not None and e.who is not None and e.who != who:
                continue
            if category is not None and e.category is not category:
                continue
            out.append(e)
            if len(out) >= count:
                break
        return out

    def urgent_for(self, who: int, since_tick: int) -> list[Event]:
        return [e for e in self.items
                if e.urgent and e.tick >= since_tick and e.who in (None, who)]
