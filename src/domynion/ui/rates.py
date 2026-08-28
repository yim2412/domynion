"""HUD 의 증가율 — 원본 `client/hud/layers/ControlPanel.ts`.

⚠ **이식 누락 마흔여덟.** 우리 HUD 는 병력과 골드의 **현재값만** 그렸다. 원본은
그 옆에 `+N/s` 와 `+N` 을 같이 쓴다.

증가율이 없으면 **건물을 지은 효과를 화면에서 확인할 수 없다.** 도시를 올려도
"상한이 얼마 올랐나"만 보이고 "지금 얼마나 빨리 차나"는 안 보인다 — 그게 도시와
공장 중 무엇을 지을지 고르는 판단의 재료다.

`ui/status.py`(§5.68)와 같은 방침이다: **여기에 Qt 가 한 줄도 없다.** 그려야
보이는 것이 아니라 계산으로 재는 것이라, 값은 Qt 없이 잰다.
"""

from __future__ import annotations

from ..core import constants as C

# `+N` 이 떠 있는 시간. 원본 `ControlPanel.addGoldGain` 의 `setTimeout(..., 2000)`.
GOLD_PIP_TICKS = int(2.0 * C.TICK_HZ)


def troop_rate(p, tile_count: int) -> float:
    """**초당** 병력 증가량. 원본 `config.troopIncreaseRate(player) * 10`.

    ⚠ `troop_increase()` 는 **tick 당**이다(초당이 아니다). 10을 안 곱하면 화면에
    실제의 1/10 이 뜨는데, 그래도 그럴듯해 보여서 눈으로는 안 잡힌다."""
    return p.troop_increase(tile_count) * C.TICK_HZ


def rate_rising(current: float, previous: float) -> bool:
    """증가율이 오르는 중인가. 원본은 `>=` 다 — **같으면 오르는 것으로 친다.**

    부동소수 증가율이 두 tick 연속 정확히 같은 일은 거의 없지만, 상한에 붙어
    증가율이 0에 고정되면 `>` 는 계속 거짓이 된다. 그때 색이 경고로 굳으면
    "줄고 있다"는 잘못된 신호가 된다."""
    return current >= previous


def gold_pip(st, pid: int) -> float | None:
    """방금 덩어리로 들어온 골드(무역·철도·정복·기부). 없으면 `None`.

    원본은 `BonusEvent`/`ConquestEvent`/`DonateEvent` 를 받아 2초간 띄우고
    **합치지 않는다**(last-wins). 엔진 쪽 기록은 `GameState.note_gold_gain`.

    ⚠ 매 tick 들어오는 인구 수입은 여기 안 온다. 원본도 그쪽은 `+N` 을 안 띄운다 —
    띄우면 매 tick 깜빡여서 **정말 큰 건이 들어왔을 때 묻힌다.**"""
    rec = st.gold_gains.get(pid)
    if rec is None:
        return None
    tick, amount = rec
    if st.tick_count - tick >= GOLD_PIP_TICKS:
        return None
    return amount
