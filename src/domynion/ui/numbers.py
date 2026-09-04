"""숫자 표시 — 원본 `client/Utils.ts :: renderNumber / renderTroops`.

⚠ **이식 누락 백스물여덟.** 우리는 병력을 `f"{troops:,.0f}"` 로 **날것 그대로**
찍고 있었다. 원본은 **10으로 나눠서** 보여 준다(`renderTroops`) — 그래서 우리
화면의 모든 병력 숫자가 **원본의 10배**였다. 시작 병력 25,000 을 원본은 `2.5K`
라고 쓴다.

`renderTroops` 는 원본에서 병력을 보여 주는 **거의 모든 곳**에 쓰인다:
`ControlPanel`(병력바·증가율) · `PlayerPanel` · `PlayerInfoOverlay` ·
`AttacksDisplay` · `EventsDisplay` · `SendResourceModal` · `DoomsdayClockPanel` ·
지도 이름 옆 숫자(`name-pass`). 한 자리만 고치면 화면 안에서 축이 갈린다.

⚠ **골드는 나누지 않는다** — `renderNumber(gold)` 다. 둘을 같은 함수로 합치면
골드가 1/10 이 된다.

⚠ **자릿수 규칙이 구간마다 다르다.** 10K 미만은 소수 둘, 10K~100K 는 소수 하나,
100K 이상은 정수다. "적당히 K 로 줄이기"가 아니라 **구간표**다 — 눈금이 일정해야
숫자가 커져도 폭이 안 흔들린다(원본이 `tabular-nums` 를 쓰는 이유와 같다).
"""

from __future__ import annotations

import math

# (문턱, 나누는 수, 소수 자릿수, 접미사). 원본 `renderNumber` 의 분기 그대로다.
# ⚠ **내림이 먼저다**(원본이 `Math.floor` 를 나누기 안쪽에 쓴다). 반올림으로
# 바꾸면 9,999 가 `10.00K` 가 되어 다음 구간의 표기와 섞인다.
_STEPS: tuple[tuple[float, float, int, str], ...] = (
    (10_000_000_000, 100_000_000, 1, "B"),
    (1_000_000_000,   10_000_000, 2, "B"),
    (10_000_000,         100_000, 1, "M"),
    (1_000_000,           10_000, 2, "M"),
    (100_000,              1_000, 0, "K"),
    (10_000,                 100, 1, "K"),
    (1_000,                   10, 2, "K"),
)


def render_number(num: float, fixed_points: int | None = None) -> str:
    """`renderNumber` — 음수는 0 으로 눌린다(원본 `Math.max(num, 0)`)."""
    num = max(0.0, float(num))
    for threshold, div, digits, suffix in _STEPS:
        if num >= threshold:
            value = math.floor(num / div) / (10 ** (digits if digits else 0))
            if digits == 0:
                return f"{int(math.floor(num / div))}{suffix}"
            return f"{value:.{fixed_points if fixed_points is not None else digits}f}{suffix}"
    return str(int(math.floor(num)))


def render_troops(troops: float, fixed_points: int | None = None) -> str:
    """`renderTroops` — **10으로 나눈다.** 그것이 원본이 보여 주는 축이다."""
    return render_number(troops / 10, fixed_points)
