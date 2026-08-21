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
