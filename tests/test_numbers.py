"""숫자 표시 — 원본 `client/Utils.ts :: renderNumber / renderTroops`.

⚠ **구간마다 자릿수가 다른 표라 경계에서 틀리기 쉽다.** 그래서 값을 손으로
적지 않고 **원본을 실제로 실행해** 뽑은 것과 대조했다(54건 불일치 0, §5.124).
여기서는 그 결과 중 **경계값**을 못 박는다.
"""

from __future__ import annotations

import pytest

from domynion.core import constants as C
from domynion.ui.numbers import render_number, render_troops


@pytest.mark.parametrize("n,want", [
    (0, "0"), (5, "5"), (999, "999"),
    (1_000, "1.00K"), (1_234, "1.23K"), (9_999, "9.99K"),
    (10_000, "10.0K"), (12_345, "12.3K"), (99_999, "99.9K"),
    (100_000, "100K"), (123_456, "123K"), (999_999, "999K"),
    (1_000_000, "1.00M"), (9_999_999, "9.99M"),
    (10_000_000, "10.0M"), (12_345_678, "12.3M"),
    (1_000_000_000, "1.00B"), (10_000_000_000, "10.0B"),
])
def test_render_number_matches_the_original(n, want):
    assert render_number(n) == want


def test_flooring_not_rounding_at_the_boundary():
    """⚠ **내림이 먼저다.** 반올림이면 9,999 가 `10.00K` 가 되어 다음 구간의
    표기(`10.0K`)와 섞여 자릿수가 흔들린다."""
    assert render_number(9_999) == "9.99K"
    assert render_number(10_000) == "10.0K"
    assert render_number(99_999) == "99.9K"
    assert render_number(100_000) == "100K"


def test_negative_is_clamped_to_zero():
    """원본 `Math.max(num, 0)`. 음수를 그대로 쓰면 `-1.23K` 가 화면에 뜬다."""
    assert render_number(-5) == "0"


def test_troops_are_divided_by_ten():
    """⚠ **이것이 §5.124 의 전부다.** 우리는 날것을 찍어 **원본의 10배**로
    보여 주고 있었다 — 시작 병력 25,000 을 원본은 `2.5K` 라고 쓴다."""
    assert render_troops(25_000) == "2.50K"
    assert render_troops(C.START_TROOPS_HUMAN) == render_number(
        C.START_TROOPS_HUMAN / 10)
    # 막지 않았으면 무엇이 일어났을 것인가 — 안 나누면 `25.0K` 다.
    assert render_troops(25_000) != render_number(25_000)


def test_gold_is_not_divided():
    """⚠ 둘을 한 함수로 합치면 **골드가 1/10** 이 된다."""
    assert render_number(1_234_567) == "1.23M"
    assert render_number(1_234_567) != render_troops(1_234_567)
