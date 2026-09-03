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
from domynion.ui import palette as P
from domynion.ui.overlays import (BorderRelation, Relation, attack_labels,
                                  attack_rings, border_relation,
                                  nuke_telegraphs)


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


# --- 국경 색 — 관계와 방어 (§5.93) --------------------------------------------

def test_an_embargo_outranks_an_alliance_on_the_border():
    """원본은 이웃을 훑다 금수를 만나면 그 자리에서 `break` 한다 — 우호는 계속
    훑는다. 둘 다인 관계에서 먼저 알아야 하는 것은 무역이 끊겼다는 쪽이다.

    막지 않았으면: 우호를 먼저 보면 동맹이면서 금수인 국경이 초록으로 뜬다."""
    st = state()
    st.diplomacy.form(0, 1, st.tick_count)
    assert border_relation(0, 1, st.diplomacy) is BorderRelation.FRIENDLY
    st.diplomacy.start_embargo(0, 1)
    assert border_relation(0, 1, st.diplomacy) is BorderRelation.EMBARGO


def test_the_embargo_shows_from_either_side():
    """⚠ **원본과 갈리는 자리다.** 원본은 칸 주인 쪽만 보고 양쪽이 각자 자기
    국경을 그리므로 A 만 금수를 걸면 A 쪽 선만 빨갛다. 우리는 두 칸 사이에 선을
    하나만 긋기 때문에 한쪽만 보면 방향에 따라 신호가 통째로 사라진다.

    막지 않았으면: 상대가 나에게 건 금수가 내 국경에 안 나타난다."""
    st = state()
    st.diplomacy.start_embargo(1, 0)
    assert border_relation(0, 1, st.diplomacy) is BorderRelation.EMBARGO


def test_plain_neighbours_stay_neutral():
    """대조군이 없으면 위 둘은 "항상 무언가를 돌려준다"도 통과시킨다."""
    st = state()
    assert border_relation(0, 1, st.diplomacy) is BorderRelation.NEUTRAL


def test_unowned_land_is_never_read_as_defended():
    """⚠ `_cover` 의 0 이 "아무도 안 덮음"이고 `-1 + 1` 도 0 이다. 안 자르면
    **중립 땅이 전부 방어된 것으로** 읽혀 국경 절반이 체커보드가 된다.

    막지 않았으면: 초소가 하나도 없는 판에서도 중립과 맞닿은 국경이 교차한다."""
    import numpy as np
    st = state()
    posts = st._posts
    tiles = np.array([st.gmap.ref(50, 1), st.gmap.ref(51, 1)])
    assert not posts.covers_many(tiles, np.array([-1, -1])).any()


def _border_pens(w) -> set[tuple[int, int, int]]:
    """`_draw_borders` 가 실제로 쓴 펜 색들. 그룹을 나눠 그리므로 **색의 가짓수**가
    곧 "관계를 봤는가"다."""
    from PyQt6.QtGui import QImage, QPainter
    img = QImage(400, 300, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    seen = set()
    real = p.setPen

    def spy(pen):
        c = pen.color()
        seen.add((c.red(), c.green(), c.blue()))
        return real(pen)

    p.setPen = spy
    w._draw_borders(p, w.offset.x())
    p.end()
    return seen


def _map(st, qapp):
    from domynion.ui.map_widget import MapWidget
    w = MapWidget(st)
    w.resize(400, 300)
    w.zoom = 4.0
    w.me = 0
    w.refresh()
    return w


def test_the_map_paints_allied_and_embargoed_borders_differently(qapp):
    """⚠ **배선이다.** `border_relation` 이 맞아도 `_draw_borders` 가 한 색으로만
    그으면 화면은 그대로다 — 이 프로젝트에서 네 번 나온 그 모양이다.

    막지 않았으면: 지도만 봐서는 누가 동맹이고 누가 금수인지 알 수 없다."""
    plain = _border_pens(_map(state(), qapp))
    assert plain == {P.BORDER_COLOR_NEUTRAL}, f"관계가 없는데 색이 여럿이다: {plain}"

    st = state()
    st.diplomacy.form(0, 1, st.tick_count)
    st.diplomacy.start_embargo(1, 2)
    tinted = _border_pens(_map(st, qapp))
    assert P.BORDER_COLOR_FRIENDLY in tinted, "동맹 국경이 안 물들었다"
    assert P.BORDER_COLOR_EMBARGO in tinted, "금수 국경이 안 물들었다"


def test_a_defended_border_alternates_and_an_undefended_one_does_not(qapp):
    """방어된 국경은 `(x+y)` 홀짝으로 두 색이 교차한다(원본 체커보드).

    막지 않았으면: 초소를 지어도 국경이 그대로라 **어디가 덮여 있는지** 모른다."""
    from domynion.core.units import Unit, UnitType

    st = state()
    before = _border_pens(_map(st, qapp))
    assert before == {P.BORDER_COLOR_NEUTRAL}, "재료 확인: 아직 초소가 없다"

    post = st.gmap.ref(11, 1)                      # 0번과 1번의 경계 근처
    st.players[0].units.units.append(
        Unit(utype=UnitType.DEFENSE_POST, tile=post, owner=0))
    st._rebuild_posts()
    after = _border_pens(_map(st, qapp))

    light, dark = P.defended_pair(P.BORDER_COLOR_NEUTRAL)
    # ⚠ **두 단계가 다 기본색과 달라야 한다.** 어두운 쪽을 기본색 그대로 두면
    # 방어된 국경의 절반이 평범한 국경과 똑같아져, 패리티를 고정해도 이 단언이
    # 통과한다 — 실제로 그 변이가 살아남아서 알았다.
    assert light != P.BORDER_COLOR_NEUTRAL and dark != P.BORDER_COLOR_NEUTRAL
    assert {light, dark} <= after, f"체커보드가 안 나온다: {after}"


# --- 전선 위 병력 숫자 (백넷) -------------------------------------------------
#
# 원본 `AttackingTroopsController` + `AttackImpl.clusteredPositions`.
# 이게 없으면 **공격이 얼마나 큰지 화면 어디에도 안 나온다** — 색칠이 번지는
# 것만 보인다. 자리를 정하는 것이 규칙이라 여기서 함께 잰다.

def _launch(st, attacker: int, target: int | None):
    atk = st.launch_attack(attacker, target)
    assert atk is not None
    return atk


def test_only_attacks_i_am_part_of_get_a_number():
    """472명이 도는 판에서 전부 그리면 지도가 숫자로 덮인다."""
    st = state()
    _launch(st, 1, 2)                       # 남의 싸움
    assert attack_labels(st, me=0) == []
    _launch(st, 0, 1)
    assert len(attack_labels(st, me=0)) >= 1


def test_incoming_and_outgoing_are_told_apart():
    """색이 방향을 말한다 — 숫자만 있으면 내 부대인지 적 부대인지 모른다."""
    st = state()
    _launch(st, 0, 1)
    _launch(st, 2, 1)                       # 나(1)를 향해 오는 공격
    labs = attack_labels(st, me=1)
    assert {l.incoming for l in labs} == {True}
    assert all(l.incoming for l in attack_labels(st, me=1))
    assert not any(l.incoming for l in attack_labels(st, me=0))


def test_the_number_is_the_troops_actually_in_the_attack():
    st = state()
    atk = _launch(st, 0, 1)
    lab = attack_labels(st, me=0)[0]
    assert lab.troops == atk.troops


def test_the_spot_is_on_the_front_line():
    """무게중심이 아니라 **실제 국경 칸**이어야 한다 — 초승달 전선에서 갈린다."""
    st = state()
    atk = _launch(st, 0, 1)
    w = st.gmap.width
    border = {t for _, t in atk.heap}
    for lab in attack_labels(st, me=0):
        assert lab.y * w + lab.x in border


def test_a_split_front_gets_two_numbers():
    """섬·길목에서 전선이 갈리면 자리도 갈린다. 하나만 쓰면 엉뚱한 곳에 앉는다."""
    gm = GameMap.from_rows(["." * 20] * 80)
    players = {}
    for pid in (0, 1):
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(0, 0))
        players[pid].kind = "nation"
        players[pid].troops = 300_000.0
    # 위·아래로 멀리 떨어진 두 전선. 사이는 아무도 없는 땅이라 국경이 안 이어진다.
    # ⚠ **각 35칸으로 둔다** — 30 미만이면 둘 다 작은 조각이라 큰 것 하나만
    # 남는 것이 정상이고, 그러면 "갈린다"가 아니라 "버린다"를 재게 된다.
    # (여기서 한 번 헛짚었다. 재료가 규칙을 가린 열여섯째 사례다.)
    for y in list(range(0, 35)) + list(range(45, 80)):
        for x in range(0, 10):
            gm.owner[gm.ref(x, y)] = 0
        for x in range(10, 20):
            gm.owner[gm.ref(x, y)] = 1
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 700, 1: 700}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    atk = _launch(st, 0, 1)
    assert len({t for _, t in atk.heap}) == 70           # 35 + 35
    spots = atk.clustered_positions(gm)
    assert len(spots) == 2
    ys = sorted(t // gm.width for t in spots)
    assert ys[0] < 35 and ys[1] >= 45


def test_a_small_second_cluster_is_dropped():
    """`minSize`(30) 미만은 버린다 — 잘게 부서진 국경에서 숫자가 지도를 덮는다."""
    gm = GameMap.from_rows(["." * 20] * 80)
    players = {}
    for pid in (0, 1):
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(0, 0))
        players[pid].kind = "nation"
        players[pid].troops = 300_000.0
    for y in range(0, 40):                    # 큰 전선 40칸
        gm.owner[gm.ref(9, y)] = 0
        gm.owner[gm.ref(10, y)] = 1
    for y in range(70, 75):                   # 작은 전선 5칸
        gm.owner[gm.ref(9, y)] = 0
        gm.owner[gm.ref(10, y)] = 1
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 45, 1: 45}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    atk = _launch(st, 0, 1)
    spots = atk.clustered_positions(gm)
    assert len(spots) == 1
    assert spots[0] // gm.width < 40


def test_the_largest_cluster_survives_even_when_it_is_tiny():
    """⚠ 작다고 전부 버리면 **시작 직후 숫자가 통째로 사라진다.**"""
    st = state()
    atk = _launch(st, 0, 1)
    assert len({t for _, t in atk.heap}) < C.ATTACK_CLUSTER_MIN_SIZE
    assert len(atk.clustered_positions(st.gmap)) == 1


def test_diagonal_tiles_stay_one_front():
    """대각으로만 이어진 칸을 다른 전선으로 세면 한 덩어리가 부서진다."""
    gm = GameMap.from_rows(["." * 20] * 20)
    players = {}
    for pid in (0, 1):
        players[pid] = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(0, 0))
        players[pid].kind = "nation"
        players[pid].troops = 300_000.0
    # 계단 모양 전선 — 4방향으로는 끊겨 보이고 8방향으로는 하나다.
    for i in range(8):
        gm.owner[gm.ref(i, i)] = 0
        gm.owner[gm.ref(i + 1, i)] = 1
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 8, 1: 8}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    atk = _launch(st, 0, 1)
    assert len(atk.clustered_positions(gm)) == 1


def test_a_boat_attack_with_no_border_yet_uses_the_landing_tile():
    """국경이 아직 없으면(배가 막 내렸으면) 내린 칸에 띄운다."""
    st = state()
    atk = _launch(st, 0, 1)
    atk.heap.clear()
    atk.source_tile = st.gmap.ref(30, 1)
    assert atk.clustered_positions(st.gmap) == [st.gmap.ref(30, 1)]
    atk.source_tile = None
    assert atk.clustered_positions(st.gmap) == []
