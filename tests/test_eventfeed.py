"""소식창 — **덜 중요한 것이 중요한 것을 밀어내지 못한다** (§5.104).

원본 `EventsDisplay` 는 이벤트를 두 층으로 나눈다. 티어 1(핵 접근 · 공격받음 ·
동맹 성립/거절/파기/연장 · 정복 · 이모지 · 기부 받음 · 핵 폭발 · 상륙 접근)은
상한 안에서 전부 남고, **나머지는 마지막 넷만** 남는다. 8초가 지나면 사라진다.

우리는 `recent(count=7)` 로 **최근 일곱 개**를 그냥 띄우고 있었다. 무역선이
판당 수천 척 오가는 판에서 그것이 무슨 뜻인지가 이 파일의 첫 테스트다.
"""

from __future__ import annotations

from domynion.core.events import (FEED_EXPIRY_TICKS, FEED_TIER2_KEEP,
                                  Event, EventKind, EventLog)


def _log(*events: Event) -> EventLog:
    log = EventLog()
    for e in events:
        log.add(e)
    return log


def _ev(kind: EventKind, tick: int, who: int | None = 0) -> Event:
    return Event(kind=kind, tick=tick, who=who)


def test_a_flood_of_minor_events_cannot_bury_an_alliance_being_broken():
    """⚠ **막지 않았으면 무엇이 일어났을 것인가** — 무역선 스무 건이 들어오면
    `recent` 는 일곱 자리를 전부 그것으로 채운다. 동맹이 깨진 것을 못 본다."""
    broken = _ev(EventKind.ALLIANCE_BROKEN, 1)
    log = _log(broken, *[_ev(EventKind.TRADE_SHIP_CAPTURED, 2 + i)
                         for i in range(20)])
    # 막지 않았을 때: 최근 일곱 개는 전부 무역선이다.
    assert all(e.kind is EventKind.TRADE_SHIP_CAPTURED
               for e in log.recent(who=0, count=7))
    feed = log.feed(0, tick=22)
    assert broken in feed
    minor = [e for e in feed if e.kind is EventKind.TRADE_SHIP_CAPTURED]
    assert len(minor) == FEED_TIER2_KEEP        # 넷까지만


def test_tier_one_events_are_all_kept():
    """핵이 여러 발 날아오면 **전부** 보여야 한다 — 그게 티어 1 의 뜻이다."""
    log = _log(*[_ev(EventKind.NUKE_INBOUND, i) for i in range(10)])
    assert len(log.feed(0, tick=10)) == 10


def test_events_older_than_eight_seconds_are_gone():
    """`recent` 는 판이 조용하면 몇 분 전 소식을 그대로 띄운다."""
    old = _ev(EventKind.CONQUERED_PLAYER, 0)
    log = _log(old)
    assert log.feed(0, tick=FEED_EXPIRY_TICKS - 1) == [old]
    assert log.feed(0, tick=FEED_EXPIRY_TICKS) == []
    assert log.recent(who=0) == [old]           # 만료는 `feed` 에만 있다


def test_the_newest_event_comes_first():
    a, b = _ev(EventKind.CONQUERED_PLAYER, 1), _ev(EventKind.CONQUERED_PLAYER, 2)
    assert _log(a, b).feed(0, tick=3) == [b, a]


def test_events_for_someone_else_are_not_shown():
    mine = _ev(EventKind.ATTACK_REQUEST, 1, who=0)
    theirs = _ev(EventKind.ATTACK_REQUEST, 1, who=1)
    everyone = _ev(EventKind.DOOMSDAY_MARKED, 1, who=None)
    feed = _log(mine, theirs, everyone).feed(0, tick=2)
    assert mine in feed and everyone in feed and theirs not in feed


def test_the_cap_is_applied_before_the_tiers_not_after():
    """⚠ **순서가 규칙이다.** 티어를 먼저 나누면 티어 2 가 30 상한을 다 먹고
    나서 넷으로 잘려, 오래된 티어 1 이 상한 밖으로 밀린 것을 못 본다.

    여기서는 무역선 40건 뒤에 핵 하나다. 원본 순서(만료 → 30 → 티어)라면
    핵은 30 안에 들어오지 못해 **안 보이는 것이 맞다.**"""
    nuke = _ev(EventKind.NUKE_INBOUND, 1)
    log = _log(nuke, *[_ev(EventKind.TRADE_SHIP_CAPTURED, 2 + i)
                       for i in range(40)])
    assert nuke not in log.feed(0, tick=42)


def test_the_doomsday_warning_is_tier_one_even_though_the_original_has_no_such_event():
    """⚠ **이건 우리 판단이라 더 단언해야 한다.** 원본은 둠스데이를 별도 UI 로
    띄우므로 이벤트 종류 자체가 없다 — 대응물이 없으니 대조로는 안 잡히고,
    티어 1 에서 빼도 **변이가 살아남았다**(2026-09-04, 23번째).

    놓치면 판이 끝나는 것이라 `URGENT` 과 같은 자리에 둔다."""
    warn = _ev(EventKind.DOOMSDAY_MARKED, 1, who=None)
    log = _log(warn, *[_ev(EventKind.TRADE_SHIP_CAPTURED, 2 + i)
                       for i in range(20)])
    assert warn in log.feed(0, tick=22)
