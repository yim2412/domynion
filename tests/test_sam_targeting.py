"""SAM 이 무엇을 막을지 고른다 — 이식 누락 여든여섯 (§5.83).

§5.82 가 남긴 표(*"SAM 계통 전체 — 요격 창만 옮겼다"*)를 다시 읽다
`SAMLauncherExecution.computeTargetScore` 를 봤다. **여러 핵이 한 tick 에 한 SAM
사거리에 들 때 무엇을 막을지가 규칙이었다.**

우리는 `self.nukes` 목록 **순서대로** 막았다 — 수폭과 원자탄이 같이 오면
**먼저 만들어진 쪽**이 막혔다. 원본은 셋을 더해 점수를 매긴다:

| 항목 | 값 | 뜻 |
|---|---|---|
| 수폭 보너스 | +70,001 | **수폭은 70칸 더 멀어도 먼저** (원본 주석이 이 값의 뜻을 적어 뒀다) |
| 거리 | 200,000 − 칸당 1,000 | **표적 칸** 기준이다. 지나가는 핵이 아니라 내 근처에 떨어질 것 |
| 급한 정도 | 10,000 − tick당 100 | 원본 주석: *"only a very minor tiebreaker"* |

그리고 **한 핵을 두 SAM 이 겹쳐 쏘지 않는다**(`targetedBySAM`) — 없으면 핵 한 발에
방공망 전체가 소모된다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout, Nuke, sam_target_score
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(size: int = 200) -> GameState:
    gm = GameMap.from_rows(["." * size] * size)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid * 20 + 5, 5)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", is_bot=False, start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 1 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def sam(st: GameState, pid: int, x: int, y: int, level: int = 1) -> Unit:
    u = Unit(UnitType.SAM_LAUNCHER, pid, tile=st.gmap.ref(x, y), level=level)
    st.players[pid].units.units.append(u)
    return u


def nuke(st: GameState, owner: int, utype: UnitType, dx: int, dy: int = 0,
         src=None) -> Nuke:
    """SAM(50,50) 기준 상대 좌표로 표적을 잡는다. 발사점은 표적 바로 옆이라
    `is_targetable`(발사점·표적 150 안)을 늘 통과한다."""
    dst = st.gmap.ref(50 + dx, 50 + dy)
    n = Nuke(owner=owner, utype=utype, src=src if src is not None else dst,
             dst=dst)
    st.nukes.append(n)
    return n


# --- 점수 그 자체 ------------------------------------------------------------

def test_a_hydrogen_bomb_outranks_an_atom_bomb_by_seventy_tiles():
    """⚠ **이 값의 뜻이 원본 주석에 적혀 있다** —
    *"70,000 offset balances the distance bonus between Hydro at 100 and Atom at 30."*

    막지 않았으면: 도시 한복판에 떨어질 수폭을 두고 멀리 가는 원자탄을 막는다."""
    st = state()
    here = st.gmap.ref(50, 50)
    hydro = nuke(st, 1, UnitType.HYDROGEN_BOMB, 100)
    atom = nuke(st, 1, UnitType.ATOM_BOMB, 30)
    assert sam_target_score(st.gmap, here, hydro) > \
        sam_target_score(st.gmap, here, atom), "70칸 차이를 못 넘었다"
    # 71칸 더 멀면 뒤집힌다 — 보너스가 무한대가 아니다
    far = nuke(st, 1, UnitType.HYDROGEN_BOMB, 102)
    assert sam_target_score(st.gmap, here, far) < \
        sam_target_score(st.gmap, here, atom)


def test_closer_targets_score_higher():
    st = state()
    here = st.gmap.ref(50, 50)
    near = nuke(st, 1, UnitType.ATOM_BOMB, 10)
    far = nuke(st, 1, UnitType.ATOM_BOMB, 60)
    assert sam_target_score(st.gmap, here, near) > \
        sam_target_score(st.gmap, here, far)


def test_the_distance_is_measured_to_the_target_not_the_nuke():
    """⚠ **표적 칸 기준이다.** SAM 은 지나가는 핵이 아니라 **자기 근처에 떨어질
    것**을 먼저 막는다."""
    st = state()
    here = st.gmap.ref(50, 50)
    # 둘 다 지금은 SAM 코앞에 있지만 표적이 다르다
    passing = Nuke(owner=1, utype=UnitType.ATOM_BOMB,
                   src=st.gmap.ref(50, 50), dst=st.gmap.ref(150, 50))
    incoming = Nuke(owner=1, utype=UnitType.ATOM_BOMB,
                    src=st.gmap.ref(50, 50), dst=st.gmap.ref(52, 50))
    assert sam_target_score(st.gmap, here, incoming) > \
        sam_target_score(st.gmap, here, passing)


def test_urgency_is_only_a_tiebreaker():
    """급한 정도는 최대 10,000 — 거리 1칸(1,000)의 열 배지만 수폭 보너스의 1/7 이다."""
    assert C.SAM_SCORE_URGENCY_BASE < C.SAM_SCORE_HYDROGEN_BONUS
    assert C.SAM_SCORE_URGENCY_BASE == 10 * C.SAM_SCORE_DISTANCE_PER_TILE


# --- 실제 요격에서 ----------------------------------------------------------

def ready_sam(st: GameState) -> Unit:
    u = sam(st, 0, 50, 50, level=9)          # 사거리를 넉넉히
    u.missile_queue.clear()
    return u


def test_the_sam_shoots_the_hydrogen_bomb_first():
    """막지 않았으면: 목록 순서대로 막는다 — 먼저 **만들어진** 쪽이다."""
    st = state()
    ready_sam(st)
    atom = nuke(st, 1, UnitType.ATOM_BOMB, 20)       # 먼저 목록에 든다
    hydro = nuke(st, 1, UnitType.HYDROGEN_BOMB, 30)
    shot = st._sams_pick_targets()
    assert id(hydro) in shot, "원자탄을 먼저 막았다"
    assert id(atom) not in shot


def test_one_sam_shoots_only_one_nuke_per_tick():
    """관이 하나면 한 발이다 — 나머지는 지나간다."""
    st = state()
    ready_sam(st)
    nuke(st, 1, UnitType.ATOM_BOMB, 20)
    nuke(st, 1, UnitType.ATOM_BOMB, 25)
    assert len(st._sams_pick_targets()) == 1


def test_two_sams_do_not_stack_on_the_same_nuke():
    """⚠ `targetedBySAM` — 겹쳐 쏘면 **핵 한 발에 방공망 전체가 소모된다.**

    막지 않았으면: 두 SAM 이 같은 핵을 쏘고 나머지 핵이 그대로 떨어진다."""
    st = state()
    a, b = ready_sam(st), sam(st, 0, 51, 50, level=9)
    b.missile_queue.clear()
    n1 = nuke(st, 1, UnitType.ATOM_BOMB, 20)
    n2 = nuke(st, 1, UnitType.ATOM_BOMB, 25)
    shot = st._sams_pick_targets()
    assert shot == {id(n1), id(n2)}, "두 SAM 이 한 발에 겹쳤다"


def test_a_friendly_nuke_is_never_targeted():
    st = state()
    ready_sam(st)
    st.diplomacy.form(0, 1, 0)
    n = nuke(st, 1, UnitType.ATOM_BOMB, 20)
    assert st._sams_pick_targets() == set()
    assert n in st.nukes


def test_my_own_nuke_is_never_targeted():
    st = state()
    ready_sam(st)
    nuke(st, 0, UnitType.ATOM_BOMB, 20)
    assert st._sams_pick_targets() == set()


def test_a_mirv_carrier_is_still_immune():
    """§5.11 의 규칙이 그대로여야 한다 — 본체는 SAM 이 못 노린다."""
    st = state()
    ready_sam(st)
    nuke(st, 1, UnitType.MIRV, 20)
    assert st._sams_pick_targets() == set()


def test_the_tick_loop_removes_what_was_shot():
    """배선 — `_advance_nukes` 가 맞은 핵을 목록에서 빼야 한다."""
    st = state()
    ready_sam(st)
    n = nuke(st, 1, UnitType.ATOM_BOMB, 20, src=st.gmap.ref(50, 50))
    st._advance_nukes()
    assert n not in st.nukes


def test_a_waiting_nuke_can_still_be_shot():
    """겹쳐 산 핵은 발사점에 떠 있는 동안에도 요격된다(§5.49)."""
    st = state()
    ready_sam(st)
    n = nuke(st, 1, UnitType.ATOM_BOMB, 20)
    n.wait_ticks = 5
    st._advance_nukes()
    assert n not in st.nukes


def test_a_nuke_out_of_range_is_left_alone():
    st = state()
    u = sam(st, 0, 50, 50, level=1)          # 사거리 70
    u.missile_queue.clear()
    n = Nuke(owner=1, utype=UnitType.ATOM_BOMB,
             src=st.gmap.ref(160, 50), dst=st.gmap.ref(160, 50))
    st.nukes.append(n)
    assert st._sams_pick_targets() == set()


def test_ticks_left_counts_the_wait():
    """점수의 급한 정도는 **대기 중인 핵도** 센다 — 아직 발사점에 떠 있다."""
    st = state()
    n = Nuke(owner=1, utype=UnitType.ATOM_BOMB,
             src=st.gmap.ref(50, 50), dst=st.gmap.ref(60, 50))
    plain = n.ticks_left(st.gmap)
    n.wait_ticks = 7
    assert n.ticks_left(st.gmap) == pytest.approx(plain + 7)
