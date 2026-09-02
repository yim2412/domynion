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
from domynion.ui.frame import FrameBuilder                        # noqa: E402
from domynion.ui.map_widget import LABEL_MIN_PX, MapWidget        # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# 나라 하나가 차지하는 줄 수. **1이면 안 된다** — §5.97 에서 이름을 "가장 큰
# 내접 사각형"에 놓게 바꾼 뒤로, 높이 1짜리 띠는 폰트 크기가 1/3 타일이 되어
# 어느 배율에서도 안 그려진다. 원본도 그렇게 자른다(`height / 3`).
# 그때 이 파일의 테스트 넷이 한꺼번에 깨졌는데 **코드가 아니라 재료가 문제였다**
# (함정 표 8번: 테스트 지도를 작게 만들면 규칙이 아니라 지도가 답을 낸다).
#
# ⚠ 10 으로도 모자랐다. 폰트가 `높이/3` 이라 배율 3배에서 10px 이 되는데
# `LABEL_MIN_PX` 가 11 이다 — **문턱 바로 아래**라 전부 잘렸다. 20 이면
# 배율 3배에서 20px, 0.05배에서 0.33px 으로 양쪽에 여유가 있다.
BAND = 20


def crowded(n: int) -> GameState:
    """작은 나라를 잔뜩 만든다 — 각자 `BAND` 줄씩."""
    gm = GameMap.from_rows(["." * 200] * (n * BAND + 2))
    players = {}
    for pid in range(n):
        for y in range(pid * BAND, pid * BAND + BAND):
            for x in range(0, 200):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"나라{pid}", start=gm.ref(0, pid * BAND))
        p.kind = "bot"
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 200 * BAND for pid in players}
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


def test_a_bay_inside_the_country_still_counts_as_room_for_the_name(qapp):
    """⚠ **얕은 바다와 해안도 자리로 친다**(원본 `isShore || (isOcean &&
    magnitude < 10)`). 만을 품은 나라는 자기 땅만으로 재면 자리가 띠 두께밖에
    안 나와, 이름이 못 읽게 작아진다.

    ⚠ **재료를 두 번 틀렸다.** 처음엔 "해안 나라의 이름이 바다 쪽으로 걸친다"로
    잡았는데, 격자는 **경계상자 안**에서만 뽑히므로(원본도 그렇다) 영토 밖의
    바다는 애초에 안 들어온다. 규칙이 실제로 일하는 곳은 **영토가 감싼 물**이다.

    막지 않았으면: 육지만 있는 지도에서는 이 규칙이 무동작이라 변이가 살아남는다."""
    rows = []
    for y in range(30):
        row = "".join("~" if (8 <= y <= 21 and 8 <= x <= 21) else "."
                      for x in range(30))
        rows.append(row)
    gm = GameMap.from_rows(rows)
    ps = {0: PlayerState(pid=0, name="만국", kind="nation", start=gm.ref(0, 0))}
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._posts = DefensePostIndex(gm.size)

    # 만을 두르는 육지 전부를 가진다 — 경계상자가 만을 품는다.
    own = gm.owner.reshape(30, 30)
    for y in range(30):
        for x in range(30):
            if not (8 <= y <= 21 and 8 <= x <= 21):
                own[y, x] = 0
    st._counts = {0: int((own == 0).sum())}

    (_, _, _, rw, rh), = FrameBuilder(gm).label_anchors(st.alive)
    # 만을 안 세면 자리는 바깥 띠(두께 8)에 갇힌다.
    assert rw > 14 and rh > 14, f"만을 자리로 안 쳤다: {rw}x{rh}"


def test_the_font_is_capped_by_the_height_of_the_spot_not_only_its_width(qapp):
    """⚠ 원본 `calculateFontSize` 가 **폭 제약과 높이 제약 중 작은 쪽**을 쓴다.
    폭만 보면 납작한 나라 위에서 글자가 위아래로 넘쳐 이웃을 덮는다.

    막지 않았으면: 넓고 납작한 재료에서만 갈린다 — `crowded` 가 200x20 이라
    폭 제약(약 44타일)이 높이 제약(약 6.7타일)보다 훨씬 크다."""
    st = crowded(3)
    w = widget(st, 3.0, qapp)
    sizes = _font_sizes(w)
    assert sizes, "이름이 하나도 안 그려졌다"
    # 높이 제약 = BAND/3 타일. 폭 제약은 그보다 훨씬 크다.
    assert max(sizes) <= BAND / 3 * w.zoom + 1, \
        f"폰트가 자리 높이를 넘었다: {max(sizes)}px"


def _font_sizes(w) -> list[int]:
    from PyQt6.QtGui import QImage, QPainter
    seen: list[int] = []
    img = QImage(400, 300, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    real_font, real_text = p.setFont, p.drawText
    cur = {"px": 0}

    def spy_font(f):
        cur["px"] = f.pointSize() if f.pointSize() > 0 else f.pixelSize()
        return real_font(f)

    def spy_text(*args):
        if args and isinstance(args[-1], str):
            seen.append(cur["px"])
        return real_text(*args)

    p.setFont, p.drawText = spy_font, spy_text
    w._draw_labels(p, w.offset.x())
    p.end()
    return seen
