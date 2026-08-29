"""섬나라의 중립 확장 — 이식 누락 여든하나 (§5.80).

`sendAttack(TerraNullius)` 는 **두 갈래다**(`AiAttackBehavior:760~779`):

```ts
if (this.hasLandBorderWithTerraNullius()) return this.sendLandAttack(target);
else return this.sendBoatAttackToNearbyTerraNullius();
```

우리는 앞쪽만 옮겼다. 그래서 **걸어서 빈 땅에 못 닿는 나라는 중립 확장을 아예
못 했다** — 사다리 맨 앞의 중립 확장도, `nuked` 도 조용히 False 를 돌려주고
그 나라는 이웃을 칠 여유가 생길 때까지 한 칸도 안 늘었다.

원본이 정하는 것 셋:

1. 해안 타일을 **열 칸에 하나씩** 훑는다(전수가 아니다 — 해안이 수천 칸이다)
2. 네 방향으로 **바로 옆이 물**이고 **5칸 앞이 빈 육지**여야 한다
3. **낙진이 앉은 땅은 제외**한다

같은 자리에서 `_boat_attack`(§5.76 의 `hated`·`island` 가 쓴다)에도 상한과
20% 규칙을 붙였다 — 원본 `sendBoatAttack` 도 `calculateAttackTroops` 를 거친다.
§5.77 은 `attackWithRandomBoat` 쪽만 고쳤었다.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import (NEUTRAL_BOAT_REACH, NEUTRAL_BOAT_SHORE_STRIDE,
                                NationBot)
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.events import EventKind
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState


def island(gap: int = NEUTRAL_BOAT_REACH, empty: bool = True,
           neighbour_land: bool = False) -> GameState:
    """P0 은 **사방이 바다인 섬**이다. `gap` 칸 건너에 빈 땅이 있다.

    0행: P0 의 섬(x 0~19)
    1~(gap-1)행: 바다
    gap 행: 빈 육지 — 배로만 닿는다
    """
    # ⚠ 섬은 **딱 20칸이다.** 0행 나머지를 육지로 두면 그게 빈 땅이라
    # "걸어서 닿는 중립"이 생겨 배 경로를 안 탄다 — 재료가 규칙을 가린다.
    rows = ["." * 40 if neighbour_land else "." * 20 + "~" * 20]         + ["~" * 40] * (gap - 1)
    rows.append(("." if empty else "~") * 40)
    rows += ["~" * 40] * 4
    gm = GameMap.from_rows(rows)
    ps = {}
    tiles = [gm.ref(x, 0) for x in range(20)]
    ps[0] = PlayerState(pid=0, name="P0", kind="nation", start=tiles[0])
    for t in tiles:
        gm.owner[t] = 0
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: len(tiles)}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    ps[0].troops = 100_000.0
    return st


def bot(pid: int = 0, difficulty: str = "medium", seed: int = 1) -> NationBot:
    return NationBot(pid=pid, rng=random.Random(seed), difficulty=difficulty)


# --- 여든하나 ---------------------------------------------------------------

def test_an_island_nation_can_still_expand():
    """⚠ 막지 않았으면: 걸어서 빈 땅에 못 닿는 나라는 **한 칸도 안 는다.**

    사다리의 맨 앞(중립 확장)과 `nuked` 가 둘 다 조용히 False 가 되고, 그
    나라는 이웃을 칠 여유가 생길 때까지 제자리다."""
    st = island()
    assert not bot()._has_land_border_with_neutral(st), "재료: 육지로 안 닿아야 한다"
    assert bot()._send_attack(st, None) is True
    assert st.boats and st.boats[0].target is None, "빈 땅으로 배가 안 갔다"


def test_a_land_border_still_goes_by_land():
    """걸어서 닿으면 배를 안 띄운다 — 3척 제한을 헛되이 쓰지 않는다."""
    # ⚠ 지도를 만든 **뒤에** 지형을 바꾸면 안 된다 — `passable_mask` 가 캐시라
    # 바뀐 것이 안 보인다(§5.50 에서 캐시를 붙인 자리다). 처음부터 육지로 만든다.
    st = island(neighbour_land=True)
    assert bot()._has_land_border_with_neutral(st)
    assert bot()._send_attack(st, None) is True
    assert not st.boats, "육지로 닿는데 배를 띄웠다"
    assert st.attacks and st.attacks[0].target is None


def test_nothing_happens_when_there_is_no_land_within_reach():
    """5칸 안에 빈 땅이 없으면 아무 일도 없다 — 바다만 있는 방향은 건너뛴다."""
    st = island(gap=NEUTRAL_BOAT_REACH + 2)
    assert bot()._send_attack(st, None) is False
    assert not st.boats


def test_nuked_land_is_not_a_boat_destination():
    """낙진 땅은 제외한다 — 평소 확장과 같은 이유(§5.76)."""
    st = island()
    st.fallout.add([st.gmap.ref(x, NEUTRAL_BOAT_REACH) for x in range(40)])
    assert bot()._send_attack(st, None) is False
    assert not st.boats


def test_the_boat_carries_a_fifth_of_the_troops():
    st = island()
    before = st.players[0].troops
    assert bot()._send_attack(st, None)
    assert st.boats[0].troops == pytest.approx(before * C.BOAT_ATTACK_RATIO)


def test_the_boat_cap_applies_here_too():
    st = island()
    b = bot()
    for _ in range(C.BOAT_MAX_NUMBER):
        b._boat_to_nearby_neutral(st)
    n = len(st.boats)
    assert n == C.BOAT_MAX_NUMBER, f"재료: 세 척이 떠 있어야 한다 ({n})"
    assert b._boat_to_nearby_neutral(st) is False
    # ⚠ 엔진의 `send_boat` 에도 같은 상한이 있어 **배 수로는 못 잰다.**
    # 관찰되는 차이는 소식이다 — 엔진 쪽에 걸리면 "배가 다 나가 있다"가 뜬다(§5.67).
    assert not [e for e in st.log.items if e.kind is EventKind.ATTACK_FAILED],         "AI 가 엔진 쪽 상한에 걸려 사람에게 소식을 냈다"


def test_owned_land_across_the_water_is_not_a_destination():
    """빈 땅만이다 — 남의 땅에 "중립 확장"으로 배를 보내면 표적이 없는 상륙이 된다.

    막지 않았으면: `target=None` 인 배가 남의 영토에 내린다."""
    st = island()
    for x in range(40):                      # 건너편 땅에 주인을 준다
        st.gmap.owner[st.gmap.ref(x, NEUTRAL_BOAT_REACH)] = 1
    st.players[1] = PlayerState(pid=1, name="P1", kind="nation",
                                start=st.gmap.ref(0, NEUTRAL_BOAT_REACH))
    st._counts[1] = 40
    assert bot()._boat_to_nearby_neutral(st) is False
    assert not st.boats


def test_the_next_tile_must_be_water():
    """**바로 옆이 물인 방향만** 본다. 육지로 이어지는 방향은 배가 갈 길이 아니다.

    막지 않았으면: 걸어갈 수 있는 방향으로 배를 띄운다 — 원본이 방향마다
    `isWater(bx+dx, by+dy)` 를 먼저 보는 이유다."""
    rows = ["." * 40, "~" * 40, "~" * 40, "~" * 40, "~" * 40, "~" * 40, "~" * 40]
    gm = GameMap.from_rows(rows)
    ps = {0: PlayerState(pid=0, name="P0", kind="nation", start=gm.ref(0, 0))}
    # ⚠ P0 을 **5칸보다 좁게** 둔다. 넓으면 오른쪽 5칸 앞이 여전히 내 땅이라
    # 물 검사를 지워도 결과가 같아진다 — 변이가 그래서 한 번 살아남았다.
    for x in range(5):
        gm.owner[gm.ref(x, 0)] = 0
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 5}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    ps[0].troops = 100_000.0
    # 아래쪽(물)로는 5칸 앞이 바다라 후보가 없고, 오른쪽은 **옆 칸이 육지**다.
    assert bot()._boat_to_nearby_neutral(st) is False
    assert not st.boats, "걸어갈 수 있는 방향으로 배를 띄웠다"


def test_the_shore_is_sampled_every_tenth_tile():
    """전수로 훑으면 해안이 수천 칸인 판에서 이 함수가 비싸진다."""
    assert NEUTRAL_BOAT_SHORE_STRIDE == 10
    assert NEUTRAL_BOAT_REACH == 5


# --- 같은 자리 · `_boat_attack` 에도 제동 -----------------------------------

def two_islands() -> GameState:
    """P0 과 P1 이 바다를 사이에 두고 있다. P0 에게는 **육지 이웃 P2** 도 있다."""
    rows = ["." * 40, "~" * 40, "." * 40, "~" * 40, "~" * 40]
    gm = GameMap.from_rows(rows)
    layout = {0: [(0, x) for x in range(20)],
              2: [(0, x) for x in range(20, 40)],
              1: [(2, x) for x in range(40)]}
    ps = {}
    for pid, cells in layout.items():
        tiles = [gm.ref(x, y) for y, x in cells]
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation",
                              start=tiles[0])
        for t in tiles:
            gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: len(c) for pid, c in layout.items()}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    for p in ps.values():
        p.troops = 100_000.0
    ps[2].troops = 1_000.0
    return st


def test_a_boat_attack_on_a_far_player_respects_the_cap():
    """⚠ §5.77 은 `attackWithRandomBoat` 만 고쳤다. `hated`·`island` 가 쓰는
    `sendBoatAttack` 에도 같은 제동이 있다.

    막지 않았으면: hard 나라가 상한을 **관계표 경로로** 우회한다."""
    st = two_islands()
    st.players[2].troops = 10_000_000.0        # 육지 이웃이 상한을 0 으로 만든다
    b = bot(difficulty="hard")
    assert b._send_cap(st) < C.ATTACK_MIN_TROOPS, "재료: 상한이 0 이어야 한다"
    assert b._boat_attack(st, 1) is False
    assert not st.boats


def test_a_boat_attack_goes_when_the_cap_allows():
    st = two_islands()
    b = bot(difficulty="hard")
    assert b._boat_attack(st, 1) is True
    assert st.boats and st.boats[0].target == 1


def test_a_boat_attack_carries_the_capped_amount():
    st = two_islands()
    st.players[2].troops = 110_000.0
    st.players[1].troops = 50_000.0    # 20% 규칙(1만)이 상한(1.75만)보다 낮아야 잰다
    b = bot(difficulty="hard")
    cap = b._send_cap(st)
    assert 0 < cap < st.players[0].troops * C.BOAT_ATTACK_RATIO, "재료"
    assert b._boat_attack(st, 1) is True
    assert st.boats[0].troops == pytest.approx(cap)
