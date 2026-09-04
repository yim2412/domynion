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

from .units import UnitType


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
    # 원본 `received_gold_from_conquest` — 정복으로 받은 골드.
    # `CONQUERED_PLAYER` 를 재사용하면 안 된다. 그쪽 `amount` 는 정복당한
    # 사람의 pid 라서, 골드를 넣으면 소식창이 엉뚱한 이름을 찍는다.
    GOLD_FROM_CONQUEST = "정복 전리품"
    # 원본 `received_gold_from_captured_ship` — 나포한 무역선이 도착해 받은 골드.
    # `CAPTURED_ENEMY_UNIT` 과 나눠 두는 이유는 `GOLD_FROM_CONQUEST` 와 같다 —
    # 이쪽 `amount` 는 골드고 저쪽은 유닛이라, 합치면 소식창이 엉뚱한 걸 찍는다.
    GOLD_FROM_CAPTURED_SHIP = "나포 전리품"
    TRADE_SHIP_CAPTURED = "무역선 나포"     # `trade_ship_captured` — 뺏긴 쪽에게
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
    # `alliance_nukes_destroyed_outgoing` / `_incoming` — 동맹이 성립하면서
    # 서로에게 날아가던 핵이 사라졌다. **양쪽 다 봐야 한다** — 쏜 쪽은 돈이
    # 사라진 것이고, 맞을 뻔한 쪽은 살아난 것이다.
    NUKES_CANCELLED_SENT = "핵 취소(내 것)"
    NUKES_CANCELLED_RECEIVED = "핵 취소(상대 것)"
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
    EventKind.GOLD_FROM_CONQUEST: Category.ATTACK,
    EventKind.GOLD_FROM_CAPTURED_SHIP: Category.TRADE,
    EventKind.TRADE_SHIP_CAPTURED: Category.TRADE,
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
    EventKind.NUKES_CANCELLED_SENT: Category.ALLIANCE,
    EventKind.NUKES_CANCELLED_RECEIVED: Category.ALLIANCE,
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


# 원본 `EventsDisplay` 의 `TIER_1_TYPES` 열셋 그대로. **덜 중요한 것이 중요한
# 것을 밀어내지 못하게** 하는 장치다 — 티어 2 는 마지막 넷만 남고, 티어 1 은
# 상한(`FEED_MAX`) 안에서 전부 남는다. §5.101(봇 공격이 전투 패널 자리를
# 차지했다)과 **같은 형태의 규칙**이다: 판 규모가 커지면 자리 다툼이 생긴다.
TIER_1 = frozenset({
    EventKind.NUKE_INBOUND, EventKind.HYDROGEN_BOMB_INBOUND,
    EventKind.MIRV_INBOUND, EventKind.NUKE_DETONATED,
    EventKind.NAVAL_INVASION_INBOUND, EventKind.ATTACK_REQUEST,
    EventKind.ALLIANCE_ACCEPTED, EventKind.ALLIANCE_REJECTED,
    EventKind.ALLIANCE_BROKEN, EventKind.RENEW_ALLIANCE,
    EventKind.CONQUERED_PLAYER, EventKind.CHAT,
    EventKind.DONATION_RECEIVED,
    # ⚠ **원본에 없다.** 원본은 둠스데이를 별도 UI 로 띄우므로 이벤트 종류가
    # 아예 없다(`DOOMSDAY_MARKED` 주석 참조). 놓치면 판이 끝나는 것이라
    # 티어 1 에 둔다 — `URGENT` 과 같은 판단이다.
    EventKind.DOOMSDAY_MARKED,
})

# *날아오는 중* 경고와 그 유닛 종류. 원본 `EventsDisplay` 가 `unitGone` 으로
# 지우는 넷 중 셋이다(상륙은 유닛 종류가 아니라 배 목록에서 본다).
# ⚠ **여기서 만든다** — `GameState.threat_still_inbound` 가 유일한 사용처지만,
# 종류와 이벤트의 짝은 이벤트 쪽 지식이다.
INBOUND_NUKE = {
    EventKind.NUKE_INBOUND: UnitType.ATOM_BOMB,
    EventKind.HYDROGEN_BOMB_INBOUND: UnitType.HYDROGEN_BOMB,
    EventKind.MIRV_INBOUND: UnitType.MIRV,
}

# 소식창 규칙 셋. **한 곳에만 둔다** — 원본도 `EventsDisplay` 한 파일 안에 있다.
FEED_EXPIRY_TICKS = 80      # 8초. 지난 것은 사라진다
FEED_MAX = 30               # 만료 뒤에도 이만큼까지만 들고 있는다
FEED_TIER2_KEEP = 4         # 덜 중요한 것은 마지막 넷


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

    def feed(self, who: int, tick: int, count: int = FEED_MAX) -> list[Event]:
        """소식창에 띄울 것 — **최신이 앞**이다.

        `recent` 와 다른 점이 셋이고 셋 다 원본 `EventsDisplay` 의 규칙이다:

        1. **8초(`FEED_EXPIRY_TICKS`)가 지나면 사라진다.** `recent` 는 최근 N개를
           무조건 띄우므로, 판이 조용하면 몇 분 전 소식이 그대로 남아 있었다.
        2. **덜 중요한 것은 마지막 넷까지만**(`FEED_TIER2_KEEP`). 무역선·유닛
           파괴가 판당 수천 건인데, 그것이 동맹 파기·정복·핵 접근을 밀어냈다.
        3. 순서는 원본대로 **만료 → 30 상한 → 티어 나누기** 다. 티어를 먼저
           나누면 티어 2 가 상한을 다 먹고 나서 넷으로 잘려 결과가 달라진다.
        """
        fresh = [e for e in self.items
                 if e.who in (None, who)
                 and tick - e.tick < FEED_EXPIRY_TICKS][-FEED_MAX:]
        t1 = [e for e in fresh if e.kind in TIER_1]
        t2 = [e for e in fresh if e.kind not in TIER_1][-FEED_TIER2_KEEP:]
        out = sorted(t1 + t2, key=lambda e: e.tick)
        return list(reversed(out))[:count]

    def urgent_for(self, who: int, since_tick: int) -> list[Event]:
        return [e for e in self.items
                if e.urgent and e.tick >= since_tick and e.who in (None, who)]
