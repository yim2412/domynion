"""지도 겹그림 — 원본 `derive/NukeTelegraphs.ts` · `AttackRings.ts` (§5.92).

§5.68(`test_status.py`)과 같은 자리다: **규칙은 도는데 화면에 안 보였다.**
핵이 어디에 떨어지는지도, 반경이 얼마인지도, 누가 쐈는지도 없었다.

여기 있는 것도 순수 계산이라 Qt 없이 잰다.
"""

from __future__ import annotations

import os
import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.naval import TransportShip
from domynion.core.nukes import NUKE_MAGNITUDES, Nuke
from domynion.core.state import PlayerState
from domynion.core.units import UnitType
from domynion.ui.overlays import Relation, attack_rings, nuke_telegraphs


def state(n: int = 3) -> GameState:
    gm = GameMap.from_rows(["." * 60] * 6)
    players = {}
    for pid in range(n):
        for x in range(pid * 6, pid * 6 + 6):
            for y in range(0, 3):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 6, 0))
        p.kind = "nation"
        p.troops = 300_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 18 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def _nuke(st, owner: int, dst: int, utype=UnitType.ATOM_BOMB, wait: int = 0):
    n = Nuke(owner=owner, utype=utype, src=st.players[owner].start, dst=dst,
             wait_ticks=wait)
    st.nukes.append(n)
    return n


# --- 핵 낙하 예고 -------------------------------------------------------------

def test_the_circle_sits_on_the_target_not_on_the_missile():
    """⚠ **이게 이 파일의 요점이다.** 우리는 날아가는 핵을 점 하나로만 그렸다 —
    점은 *지금 어디 있나*를 말하고 예고 원은 *어디에 떨어지나*를 말한다.

    막지 않았으면: 좌표를 `n.tile(gmap)`(현재 위치)에서 뽑아도 원이 하나 나오므로
    테스트가 통과한다. 그래서 **비행 중간 상태로 재고 둘이 다름을 단언한다.**"""
    st = state()
    dst = st.gmap.ref(50, 1)
    n = _nuke(st, owner=0, dst=dst)
    for _ in range(3):
        n.advance()
    assert n.tile(st.gmap) != dst, "재료 확인: 핵이 아직 표적에 안 닿았다"

    (t,) = nuke_telegraphs(st, me=0)
    assert (t.x, t.y) == (50, 1)


def test_the_two_radii_are_the_blast_radii_not_a_drawing_constant():
    """반경은 `NUKE_MAGNITUDES` 에서 온다 — 수소탄과 원자탄이 다르게 보여야 한다.

    막지 않았으면: 고정 반경으로 그려도 원은 나오고, 사람은 수소탄 100칸을
    원자탄 30칸으로 읽고 그 안에 병력을 둔다."""
    st = state()
    _nuke(st, owner=0, dst=st.gmap.ref(50, 1), utype=UnitType.ATOM_BOMB)
    _nuke(st, owner=0, dst=st.gmap.ref(52, 1), utype=UnitType.HYDROGEN_BOMB)
    got = {(t.inner, t.outer) for t in nuke_telegraphs(st, me=0)}
    assert got == {NUKE_MAGNITUDES[UnitType.ATOM_BOMB],
                   NUKE_MAGNITUDES[UnitType.HYDROGEN_BOMB]}
    assert len(got) == 2, "둘이 같으면 이 테스트는 아무것도 안 잰다"


def test_a_waiting_nuke_has_not_launched_so_it_is_not_telegraphed():
    """겹쳐 산 핵은 발사가 한 발씩 밀린다(`wait_ticks`). 아직 안 나간 핵의
    표적을 띄우면 사람이 실제보다 이르게 반응한다 — 원본도 같은 조건으로 거른다.

    막지 않았으면: 사일로 하나에서 세 발을 사면 원이 **동시에 셋** 뜬다."""
    st = state()
    _nuke(st, owner=0, dst=st.gmap.ref(50, 1), wait=0)
    _nuke(st, owner=0, dst=st.gmap.ref(51, 1), wait=7)
    (t,) = nuke_telegraphs(st, me=0)
    assert t.x == 50


def test_a_mirv_in_flight_has_no_circle_because_it_does_not_detonate():
    """MIRV 본체는 `NUKE_MAGNITUDES` 에 없다 — 터지지 않고 갈라지기 때문이다.

    막지 않았으면: 반경 없는 유닛에 기본값을 씌워 **없는 폭발을 예고한다.**"""
    st = state()
    _nuke(st, owner=0, dst=st.gmap.ref(50, 1), utype=UnitType.MIRV)
    assert nuke_telegraphs(st, me=0) == []


def test_the_color_is_who_fired_it_self_ally_enemy():
    """⚠ 셋을 한 색으로 그리면 원이 여럿 뜰 때 **무엇을 피해야 하는지** 모른다.

    막지 않았으면: 관계를 안 보고 전부 적색으로 칠해도 원 개수는 같다."""
    st = state()
    st.diplomacy.form(0, 1, st.tick_count)
    _nuke(st, owner=0, dst=st.gmap.ref(50, 1))
    _nuke(st, owner=1, dst=st.gmap.ref(51, 1))
    _nuke(st, owner=2, dst=st.gmap.ref(52, 1))
    by_x = {t.x: t.relation for t in nuke_telegraphs(st, me=0)}
    assert by_x == {50: Relation.SELF, 51: Relation.FRIENDLY, 52: Relation.ENEMY}


def test_with_no_local_player_everything_reads_as_enemy():
    """관전·리플레이 경로. 원본 `localPlayerID <= 0 → TELEGRAPH_ENEMY`."""
    st = state()
    _nuke(st, owner=0, dst=st.gmap.ref(50, 1))
    assert [t.relation for t in nuke_telegraphs(st)] == [Relation.ENEMY]


# --- 상륙 고리 ---------------------------------------------------------------

def _boat(st, owner: int, dst: int, retreating: bool = False,
          active: bool = True) -> TransportShip:
    b = TransportShip(owner=owner, target=None, troops=100.0,
                      path=[st.players[owner].start, dst], dst=dst,
                      retreating=retreating, active=active)
    st.boats.append(b)
    return b


def test_only_my_own_boats_get_a_ring():
    """⚠ 400나라의 배를 다 그리면 지도가 고리로 덮인다 — 원본이 소유자로 자른다.

    막지 않았으면: 필터를 지워도 내 배의 고리는 그대로 나오므로 눈에 안 띈다.
    그래서 **남의 배를 더 많이 두고** 잰다."""
    st = state()
    _boat(st, owner=0, dst=st.gmap.ref(40, 1))
    _boat(st, owner=1, dst=st.gmap.ref(41, 1))
    _boat(st, owner=2, dst=st.gmap.ref(42, 1))
    got = attack_rings(st, me=0)
    assert [(r.x, r.y) for r in got] == [(40, 1)]


def test_a_retreating_boat_is_not_still_heading_to_its_old_target():
    """되돌아가는 배의 원래 표적을 계속 띄우면 사람이 아직 그리로 간다고 읽는다.

    막지 않았으면: 퇴각 판정이 빠져도 고리는 나오고, 그 자리에 방어를 몰아 둔다."""
    st = state()
    _boat(st, owner=0, dst=st.gmap.ref(40, 1), retreating=True)
    assert attack_rings(st, me=0) == []


def test_a_sunk_boat_leaves_no_ring():
    st = state()
    _boat(st, owner=0, dst=st.gmap.ref(40, 1), active=False)
    assert attack_rings(st, me=0) == []


def test_without_a_local_player_there_are_no_rings_at_all():
    st = state()
    _boat(st, owner=0, dst=st.gmap.ref(40, 1))
    assert attack_rings(st) == []


# --- 배선: 순수 함수가 맞아도 그리기가 끊기면 화면은 그대로다 ------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_widget_actually_draws_the_circles(qapp):
    """⚠ **위 열은 전부 `overlays.py` 안에서만 돈다.** `paintEvent` 가
    `_draw_telegraphs` 를 안 불러도 전부 통과한다 — 그러면 화면은 예전 그대로다.

    막지 않았으면: §5.62 가 이름 붙인 *"규칙은 도는데 결과가 안 보인다"* 를
    이번엔 **우리가 고치면서** 다시 만든다.

    픽셀을 세는 대신 `drawEllipse` 호출을 가로챈다 — 색과 알파까지 재려면
    렌더 결과를 봐야 하지만, 여기서 잴 것은 *그리기로 결정했는가* 다."""
    from PyQt6.QtGui import QImage, QPainter
    from domynion.ui.map_widget import MapWidget

    st = state()
    _nuke(st, owner=0, dst=st.gmap.ref(30, 1))

    w = MapWidget(st)
    w.resize(400, 300)
    w.zoom = 4.0
    w.me = 0
    w.refresh()

    img = QImage(400, 300, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    calls = []
    real = p.drawEllipse
    p.drawEllipse = lambda *a: (calls.append(a), real(*a))[1]
    w._draw_telegraphs(p, w.offset.x())
    p.end()

    # 예고 하나당 원 둘(안쪽·바깥쪽)이다.
    assert len(calls) == 2, f"원이 {len(calls)}개다"


def test_paint_event_actually_reaches_the_overlay(qapp):
    """⚠ **위 테스트도 `_draw_telegraphs` 를 손으로 부른다.** `paintEvent` 에서
    그 줄을 지워도 통과한다 — 그러면 화면에는 아무것도 안 나온다.

    막지 않았으면: 순수 함수도 맞고 그리기 함수도 맞는데 **아무도 안 부른다.**
    이 프로젝트가 죽은 코드를 찾은 게 네 번인데, 전부 이 모양이었다."""
    from PyQt6.QtGui import QImage
    from domynion.ui.map_widget import MapWidget

    st = state()
    _nuke(st, owner=0, dst=st.gmap.ref(30, 1))
    w = MapWidget(st)
    w.resize(400, 300)
    w.zoom = 4.0
    w.me = 0
    w.refresh()

    seen = []
    real = w._draw_telegraphs
    w._draw_telegraphs = lambda *a: (seen.append(a), real(*a))[1]
    w.render(QImage(400, 300, QImage.Format.Format_ARGB32))
    assert seen, "paintEvent 이 예고 원을 그리지 않는다"


def test_nothing_is_drawn_when_nothing_is_in_flight(qapp):
    """대조군이 없으면 위 테스트는 "항상 둘을 그린다"도 통과시킨다."""
    from PyQt6.QtGui import QImage, QPainter
    from domynion.ui.map_widget import MapWidget

    w = MapWidget(state())
    w.resize(400, 300)
    w.zoom = 4.0
    w.me = 0
    w.refresh()

    img = QImage(400, 300, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    calls = []
    real = p.drawEllipse
    p.drawEllipse = lambda *a: (calls.append(a), real(*a))[1]
    w._draw_telegraphs(p, w.offset.x())
    p.end()
    assert calls == []
