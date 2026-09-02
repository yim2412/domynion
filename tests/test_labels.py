"""지도 위 나라 이름 — 언제 그리고 언제 버리는가.

원본 기본 구성이 **472명**이라, 작은 이름을 하한으로 끌어올려 그리면 지도가 글자로
덮여 아무것도 안 읽힌다. 원본은 화면상 크기가 `cullThreshold` 미만이면 **버린다**
(`name.vert.glsl` — `screenSize < uCullThreshold && !isHighlighted` 면 정점을 죽인다).
커서가 얹힌 나라만 예외다.
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage, QPainter                          # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from domynion.core.buildings import DefensePostIndex              # noqa: E402
from domynion.core.engine import GameState                        # noqa: E402
from domynion.core.gamemap import GameMap                         # noqa: E402
from domynion.core.state import PlayerState                       # noqa: E402
from domynion.ui.map_widget import LABEL_MIN_PX, MapWidget        # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def crowded(n: int) -> GameState:
    """작은 나라를 잔뜩 만든다 — 각자 한 줄씩."""
    gm = GameMap.from_rows(["." * 200] * (n + 2))
    players = {}
    for pid in range(n):
        for x in range(0, 200):
            gm.owner[gm.ref(x, pid)] = pid
        p = PlayerState(pid=pid, name=f"나라{pid}", start=gm.ref(0, pid))
        p.kind = "bot"
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 200 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    return st


def drawn_labels(w: MapWidget) -> int:
    """실제로 `_draw_labels` 가 몇 개를 그렸는지 센다.

    픽셀을 세는 대신 `drawText` 호출을 가로챈다 — 겹쳐 그린 그림자까지 세지 않고
    **그리기로 결정한 나라 수**만 잡아야 컷이 도는지 알 수 있다."""
    seen: list[str] = []
    img = QImage(400, 300, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    real = p.drawText

    def spy(*args):
        if args and isinstance(args[-1], str):
            seen.append(args[-1])
        return real(*args)

    p.drawText = spy
    w._draw_labels(p, w.offset.x())
    p.end()
    return len(set(seen))


def widget(st: GameState, zoom: float, qapp) -> MapWidget:
    w = MapWidget(st)
    w.resize(400, 300)
    w.zoom = zoom
    w.refresh()
    return w


def test_tiny_names_are_dropped_not_shrunk(qapp):
    """막지 않았으면: 400개 부족 이름이 전부 최소 크기로 그려져 지도를 덮는다."""
    st = crowded(40)
    small = drawn_labels(widget(st, 0.05, qapp))
    big = drawn_labels(widget(st, 3.0, qapp))
    assert small < big, f"축소해도 그리는 수가 안 줄었다 ({small} vs {big})"
    assert small == 0, f"이 배율에서는 하나도 안 보여야 한다 ({small}개)"


def test_names_come_back_when_you_zoom_in(qapp):
    st = crowded(6)
    assert drawn_labels(widget(st, 4.0, qapp)) > 0


def test_the_hovered_country_is_named_even_when_tiny(qapp):
    """무엇을 치는지는 배율과 무관하게 보여야 한다 — 원본도 이 예외를 둔다."""
    st = crowded(40)
    w = widget(st, 0.05, qapp)
    assert drawn_labels(w) == 0
    w.hovered_owner = 3
    assert drawn_labels(w) == 1


def test_the_cut_is_a_readable_size(qapp):
    """컷이 너무 낮으면 읽지도 못할 글자를 그리는 것과 같다."""
    assert LABEL_MIN_PX >= 9


def test_what_someone_said_to_me_rides_along_with_their_name(qapp):
    """⚠ **배선이다**(§5.96). `visible_to` 가 맞아도 라벨이 안 붙이면 화면은
    그대로다 — 소식창 한 줄이 흘러가고 끝난다.

    막지 않았으면: AI 가 던진 🖕 하나가 관계를 −100 움직이는데, 지도에서는
    누가 그랬는지 알 수 없다."""
    st = crowded(6)
    st.players[0].kind = "human"
    st.players[1].kind = "nation"
    w = widget(st, 4.0, qapp)
    w.me = 0

    st.emojis.outgoing.append((1, 0, "🖕", st.tick_count))
    # ⚠ 라벨·깃발·이모지는 **1초에 한 번**만 다시 잰다(`_label_age`). 그냥
    # `refresh()` 를 또 부르면 주기가 안 돌아 옛 값이 남는다.
    w._label_age = 0
    w.refresh()
    texts = _label_texts(w)
    assert any("🖕" in t for t in texts), f"상대가 한 말이 지도에 안 뜬다: {texts}"
    assert not any("🖕" in t and "나라0" in t for t in texts),         "받은 사람 이름 옆에 붙었다 — **말한 쪽**에 붙어야 한다"


def test_the_flags_keep_their_slots_when_someone_talks(qapp):
    """⚠ 깃발은 자리가 셋뿐이다(`MAX_MARKERS`). 이모지가 그 자리를 놓고 다투면
    **지속되는 상태 표시가 잠깐 뜨는 말에 밀려난다.**

    막지 않았으면: 왕관을 쓴 나라가 말을 거는 순간 왕관이 사라진다."""
    st = crowded(6)
    st.players[0].kind = "human"
    w = widget(st, 4.0, qapp)
    w.me = 0
    w._label_age = 0
    w.refresh()
    with_crown = [t for t in _label_texts(w) if "👑" in t]
    assert with_crown, "재료 확인: 왕관이 안 떴다"

    crowned = st.players[max(st.players, key=lambda p: st.tiles(p))].pid
    st.emojis.outgoing.append((crowned, 0, "🖕", st.tick_count))
    w._label_age = 0
    w.refresh()
    assert any("👑" in t and "🖕" in t for t in _label_texts(w)), \
        "말을 거는 순간 깃발이 밀려났다"


def _label_texts(w) -> list[str]:
    from PyQt6.QtGui import QImage, QPainter
    seen: list[str] = []
    img = QImage(400, 300, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    real = p.drawText

    def spy(*args):
        if args and isinstance(args[-1], str):
            seen.append(args[-1])
        return real(*args)

    p.drawText = spy
    w._draw_labels(p, w.offset.x())
    p.end()
    return seen
