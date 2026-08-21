"""스폰 면역 — 판 시작 직후 잠깐 사람이 사람을 못 친다.

**이식 누락이었다.** 없을 때 사람(P0)이 아무것도 안 하고 있으면 155초 만에 탈락했다.
원본은 시작 5초 동안 봐준다(`Config.ts :: spawnImmunityDuration`).

함정은 규칙이 **비대칭**이라는 것이다 — 원본 주석 그대로 "Only human attackers
respect PVP immunity". 봇·Nation 은 면역 중인 상대도 친다. 대칭으로 만들면
초반 5초 동안 판이 통째로 멈춘다.
"""

from __future__ import annotations

import random

from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState


def state(kinds: dict[int, str], tick: int = 0) -> GameState:
    """가로 한 줄에 서로 맞닿게 세운다 — 공격이 실제로 성립해야 한다."""
    gm = GameMap.from_rows(["." * 40] * 4)
    players = {}
    for pid, kind in kinds.items():
        for x in range(pid * 4, pid * 4 + 4):
            gm.owner[gm.ref(x, 0)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 4, 0))
        p.kind = kind
        p.is_bot = kind == "bot"
        p.troops = 100_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 4 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = tick
    return st


def test_five_seconds_at_ten_hertz():
    assert C.SPAWN_IMMUNITY_TICKS * C.TICK_DT == 5.0


def test_human_cannot_attack_during_immunity():
    st = state({0: "human", 1: "human"})
    assert st.launch_attack(0, 1) is None


def naval_state(tick: int) -> GameState:
    """바다를 사이에 둔 두 사람. 양쪽 소유 칸이 바다에 닿아야 배가 뜬다."""
    gm = GameMap.from_rows(["." + "~" * 8 + "."] * 3)
    players = {}
    for pid, x in ((0, 0), (1, 9)):
        t = gm.ref(x, 1)
        p = PlayerState(pid=pid, name=f"P{pid}", start=t)
        p.kind = "human"
        p.troops = 100_000.0
        players[pid] = p
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = tick
    return st


def test_immunity_blocks_landings_too():
    """공격만 막고 상륙을 안 막으면 면역이 뚫린다.

    막지 않았으면: 아래 첫 단언이 보여주듯 같은 상륙이 그냥 성공한다."""
    ok = naval_state(C.SPAWN_IMMUNITY_TICKS)
    assert ok.send_boat(0, ok.gmap.ref(9, 1)) is not None, "면역 밖에서는 뜬다"

    st = naval_state(0)
    assert st.send_boat(0, st.gmap.ref(9, 1)) is None


def test_human_can_attack_once_immunity_expires():
    """막지 않았으면: 면역이 안 풀려 사람이 영영 사람을 못 친다."""
    st = state({0: "human", 1: "human"}, tick=C.SPAWN_IMMUNITY_TICKS)
    assert st.launch_attack(0, 1) is not None


def test_bots_and_nations_ignore_immunity():
    """비대칭이 이 규칙의 핵심이다.

    막지 않았으면: 초반 5초 동안 아무도 아무도 못 쳐서 판이 멈춘다."""
    for kind in ("bot", "nation"):
        st = state({0: kind, 1: "human"})
        assert st.launch_attack(0, 1) is not None, f"{kind} 은 면역을 무시한다"


def test_bots_are_never_immune_even_as_targets():
    """봇은 `isImmune()` 자체가 false — 사람이 봇은 처음부터 칠 수 있다."""
    st = state({0: "human", 1: "bot"})
    assert st.is_immune(1) is False
    assert st.launch_attack(0, 1) is not None


def test_immunity_does_not_block_neutral_expansion():
    """중립 확장까지 막으면 시작 5초 동안 할 게 없다."""
    st = state({0: "human"})
    assert st.launch_attack(0, None) is not None


def test_alliance_still_blocks_after_immunity_expires():
    """면역을 넣으면서 동맹 검사가 밀려나면 안 된다."""
    st = state({0: "human", 1: "human"}, tick=C.SPAWN_IMMUNITY_TICKS)
    st.diplomacy.form(0, 1, tick=st.tick_count)
    assert st.launch_attack(0, 1) is None
