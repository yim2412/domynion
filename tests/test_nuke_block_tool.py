"""`tools/nuke_block.py` 가 **`쏠 칸 없음`을 제대로 가르는가**(§5.133).

측정 도구가 틀리면 결론이 통째로 틀린다 — 그리고 도구는 **관대해지는 방향으로**
틀린다. 그래서 갈래마다 **실제 `_pick_tile_scored`** 를 돌려 None 이 나오는 판을
만들고, 도구의 감싸기(`make_probes`)가 그 판을 어느 갈래로 넣는지 잰다.
"""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

import nuke_block                                    # noqa: E402
from domynion.ai.nukes import NationNukeBehavior     # noqa: E402
from domynion.core.gamemap import GameMap            # noqa: E402
from domynion.core.nukes import NUKE_MAGNITUDES      # noqa: E402
from domynion.core.units import UnitType             # noqa: E402
from test_nuke_ai import behavior, fill, state, unit  # noqa: E402

ATOM_R = NUKE_MAGNITUDES[UnitType.ATOM_BOMB][1]


def pick(monkeypatch, st, b, silo):
    """도구와 **같은 감싸기**를 끼우고 칸을 고른다 → (칸, 갈래, 걸린 것)."""
    probe = {"clean": 0, "scores": []}
    blockers: Counter = Counter()
    clean, score = nuke_block.make_probes(probe, blockers)
    monkeypatch.setattr(NationNukeBehavior, "_blast_is_clean", clean)
    monkeypatch.setattr(NationNukeBehavior, "tile_score", score)
    tile = b._pick_tile(st, st.players[0], st.players[1], [silo],
                        UnitType.ATOM_BOMB)
    return tile, nuke_block._why_no_tile(probe), blockers


def base():
    st = state()
    silo = unit(st, 0, UnitType.MISSILE_SILO, 20, 200)
    return st, silo


def test_a_territory_too_small_for_the_blast_is_no_clean_tile(monkeypatch):
    """반경보다 좁은 영토 — 테두리가 전부 남의 땅(여기서는 무주지)이다."""
    st, silo = base()
    fill(st, 1, 300, 195, 310, 205)              # 10×10 < 반경 30
    tile, why, blockers = pick(monkeypatch, st, behavior(), silo)
    assert tile is None
    assert why == "칸: 깨끗한 칸 0"
    assert blockers and set(blockers) == {"무주지(땅)"}, blockers


def test_a_sam_near_every_candidate_is_the_sam_branch(monkeypatch):
    """medium 은 SAM 50칸 안이면 점수 **정확히 -1** — 칸은 깨끗한데 못 고른다."""
    st, silo = base()
    fill(st, 1, 260, 160, 341, 241)              # 깨끗한 칸이 전부 SAM 50칸 안
    unit(st, 1, UnitType.CITY, 300, 200)
    unit(st, 1, UnitType.SAM_LAUNCHER, 300, 200)
    b = behavior()
    tile, why, _ = pick(monkeypatch, st, b, silo)
    # 막지 않았으면 — 같은 판에서 SAM 만 빼면 칸이 나온다
    st.players[1].units.units = [u for u in st.players[1].units.units
                                 if u.utype is not UnitType.SAM_LAUNCHER]
    assert pick(monkeypatch, st, behavior(), silo)[0] is not None
    assert tile is None
    assert why == "칸: SAM 50칸"


def test_a_recent_hit_everywhere_is_the_recent_branch(monkeypatch):
    """최근 때린 자리 감점은 **-1 아래**다 — SAM 갈래와 섞이면 안 된다."""
    st, silo = base()
    fill(st, 1, 260, 160, 341, 241)              # 81×81 — 깨끗한 칸은 가운데 근처뿐
    unit(st, 1, UnitType.CITY, 300, 200)         # 가운데를 후보에 넣는다
    b = behavior()
    # 막지 않았으면 — 감점이 없으면 칸이 나온다
    assert pick(monkeypatch, st, behavior(), silo)[0] is not None
    b._recent.append((st.tick_count, st.gmap.ref(300, 200), UnitType.HYDROGEN_BOMB))
    tile, why, _ = pick(monkeypatch, st, b, silo)
    assert tile is None
    assert why == "칸: 최근 표적"


def test_a_choosable_tile_is_not_a_tile_failure(monkeypatch):
    st, silo = base()
    fill(st, 1, 200, 100, 400, 300)
    tile, why, _ = pick(monkeypatch, st, behavior(), silo)
    assert tile is not None
    assert why == "칸: 발사 실패"    # 칸이 있는데 안 쐈다면 남는 것은 발사뿐


@pytest.mark.parametrize("v, picked", [(-1.0, False), (-0.999, True)])
def test_the_split_rests_on_minus_one_being_rejected(monkeypatch, v, picked):
    """갈래 판정이 기대는 문장 — *"점수가 정확히 -1 이면 고르지 않는다"*.

    원본 `bestValue = -1` + `value > bestValue` 다. 이게 `>=` 로 바뀌면 SAM 칸도
    골라져 `SAM 50칸` 갈래는 **존재하지 않는 것을 세게** 된다."""
    st, silo = base()
    fill(st, 1, 200, 100, 400, 300)
    monkeypatch.setattr(NationNukeBehavior, "tile_score",
                        lambda *a, **k: v)
    tile = behavior()._pick_tile(st, st.players[0], st.players[1], [silo],
                                 UnitType.ATOM_BOMB)
    assert (tile is not None) is picked


def test_first_blocker_names_what_is_on_the_ring():
    """테두리의 **첫 위반** 칸이 무엇인가 — 나라 · 무주지 · 바다를 가른다."""
    r = 4
    rows = ["." * 20 for _ in range(20)]
    rows[10 - r] = "~" * 20                      # 위 변 전체가 바다
    gm = GameMap.from_rows(rows)
    for y in range(20):
        for x in range(20):
            if rows[y][x] == ".":
                gm.owner[gm.ref(x, y)] = 1
    st = SimpleNamespace(gmap=gm)
    c = gm.ref(10, 10)
    assert nuke_block._first_blocker(st, c, r, 1) == "바다"

    gm.owner[:] = 1
    gm.owner[gm.ref(10 + r, 10)] = 2
    assert nuke_block._first_blocker(st, c, r, 1) == "다른 나라"

    gm.owner[gm.ref(10 + r, 10)] = -1
    assert nuke_block._first_blocker(st, c, r, 1) == "무주지(땅)"

    gm.owner[gm.ref(10 + r, 10)] = 1
    assert nuke_block._first_blocker(st, c, r, 1) == "?"
