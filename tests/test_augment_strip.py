"""보유 증강 표시 — `AugmentStrip`.

⚠ **드래프트 창만으로는 고른 것이 어디에도 안 남는다.** 창이 닫히는 순간
*"내가 무엇을 골랐는지 · 다음이 언제인지"* 를 화면에서 알 방법이 사라진다.
계수는 전부 뒤에서만 곱해지기 때문이다.

여기서 재는 것은 셋이다:
① 머리글이 **세 상태를 구분하는가**(다음까지 / 고르는 중 / 다 모았다)
② 죽으면 숨고, **가진 것이 없어도 안 숨는가**
③ 레벨이 오르면 **표시 수치도 따라 오르는가**(Lv1 수치를 굳혀 두면 체감이 없다)
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication                        # noqa: E402

from domynion.core import constants as C                        # noqa: E402
from domynion.core.augments import AUGMENTS_BY_KEY, describe    # noqa: E402
from domynion.core.buildings import DefensePostIndex            # noqa: E402
from domynion.core.engine import GameState                      # noqa: E402
from domynion.core.gamemap import GameMap                       # noqa: E402
from domynion.core.state import PlayerState                     # noqa: E402
from domynion.ui.augment_dialog import AugmentStrip             # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def state() -> GameState:
    gm = GameMap.from_rows(["." * 40] * 20)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid * 20 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", start=t,
                              kind="human" if pid == 0 else "nation")
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.human = 0
    st.augment_next_tick = C.AUGMENT_FIRST_TICK
    return st


# ⚠ **참조를 잡아 둔다.** 부모 없는 QWidget 을 `strip(st).body.text()` 처럼
# 바로 흘려 쓰면 파이썬이 위젯을 먼저 회수해 Qt 쪽 객체가 사라지고
# `RuntimeError: wrapped C/C++ object of type QLabel has been deleted` 가 난다.
# 코드 버그가 아니라 **테스트 재료** 문제라, 여기서 한 번만 막는다.
_ALIVE: list[AugmentStrip] = []


def strip(st: GameState) -> AugmentStrip:
    w = AugmentStrip(st, 0)
    _ALIVE.append(w)
    w.refresh()
    return w


# --- 머리글: 세 상태 ------------------------------------------------------------

def test_counts_down_to_the_next_stop(qapp):
    st = state()
    w = strip(st)
    first = w.head.text()
    assert "다음" in first
    st.tick_count = C.AUGMENT_FIRST_TICK // 2
    w.refresh()
    # ⚠ **"시간이 들어 있다"로 재면 안 된다** — 고정 문자열도 통과한다.
    # 시계가 실제로 줄어드는지를 본다.
    assert w.head.text() != first


def test_the_countdown_shows_the_configured_first_stop(qapp):
    """상수를 바꿨는데 화면이 안 따라가면 조용히 어긋난다.

    ⚠ 테스트 안에서 초를 손으로 만들지 않는다 — 그러면 배선이 아니라
    산수를 재게 된다."""
    st = state()
    sec = int(C.AUGMENT_FIRST_TICK * C.TICK_DT)
    assert strip(st).head.text().endswith(f"{sec // 60}:{sec % 60:02d}")


def test_says_it_is_open_while_the_draft_is_up(qapp):
    st = state()
    st.tick_count = C.AUGMENT_FIRST_TICK
    st.tick()
    assert st.augment_offer, "드래프트가 안 열렸다 — 재료가 틀렸다"
    assert "고르는 중" in strip(st).head.text()


def test_says_it_is_done_when_nothing_will_open_again(qapp):
    st = state()
    st.augment_next_tick = -1
    assert "다 모았다" in strip(st).head.text()


# --- 보이고 숨기기 --------------------------------------------------------------

def test_stays_visible_before_the_first_card(qapp):
    """⚠ **판 초반이야말로 보여야 하는 구간이다** — 숨기면 증강이 있는 판인지
    조차 모른다."""
    st = state()
    w = strip(st)
    assert w.isVisible()
    assert not st.players[0].augments


def test_hides_when_i_am_dead(qapp):
    st = state()
    w = strip(st)
    assert w.isVisible()
    st.players[0].alive = False
    w.refresh()
    assert not w.isVisible()


# --- 본문 ---------------------------------------------------------------------

def test_lists_what_i_own_with_its_level(qapp):
    st = state()
    st.players[0].augments = {"fertile": 2}
    body = strip(st).body.text()
    assert AUGMENTS_BY_KEY["fertile"].name in body
    assert "Lv2" in body


def test_the_number_follows_the_level(qapp):
    """Lv1 수치를 굳혀 두면 Lv3 을 올려도 체감이 없다 — 드래프트 창과 같은 규칙."""
    aug = AUGMENTS_BY_KEY["fertile"]
    st = state()
    st.players[0].augments = {"fertile": 1}
    one = strip(st).body.text()
    st.players[0].augments = {"fertile": 3}
    three = strip(st).body.text()
    assert describe(aug, 1) in one
    assert describe(aug, 3) in three
    assert describe(aug, 1) != describe(aug, 3)


def test_orders_by_level_so_the_rows_do_not_jump(qapp):
    """매 tick 자리가 흔들리면 읽을 수 없다. 레벨 내림차순 · 같으면 이름 순."""
    st = state()
    st.players[0].augments = {"fertile": 1, "conscript": 3, "elite": 1}
    body = strip(st).body.text()
    # 이름 순은 **한글 기준**이다 — 비옥한 땅 < 정예 병단 (키 순서와 다르다).
    pos = [body.index(AUGMENTS_BY_KEY[k].name)
           for k in ("conscript", "fertile", "elite")]
    assert pos == sorted(pos), "Lv3 이 맨 위, 나머지는 이름 순이어야 한다"
    # 막지 않았으면 무엇이 일어났을 것인가 — **고른 순서(dict 순서)** 로 그리면
    # 징집령이 가운데 온다. 그 배치와 구별되는지 같이 단언한다.
    assert pos != [body.index(AUGMENTS_BY_KEY[k].name)
                   for k in ("fertile", "conscript", "elite")]


def test_a_card_from_an_old_save_does_not_kill_the_panel(qapp):
    """엔진과 같은 방어다 — 모르는 키 하나로 화면이 통째로 죽으면 안 된다."""
    st = state()
    st.players[0].augments = {"없는카드": 1, "fertile": 1}
    body = strip(st).body.text()
    assert AUGMENTS_BY_KEY["fertile"].name in body
