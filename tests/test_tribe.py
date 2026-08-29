"""Tribe(봇) — Nation 과 성격이 정반대인 AI.

이식 누락이었다. 사람 아닌 모두에게 `NationBot` 을 붙이고 있었는데, 원본은
`NationExecution` 과 `TribeExecution` 을 따로 돌린다. 싱글플레이 기본 구성이
**72개 나라 + 봇 400개**라 지도를 채우는 것은 사실 봇 쪽이다.

봇이 동맹을 다 받아 주는 것이 이 게임의 초반 구조다 — 사람은 주변 봇을 우방으로
묶어 두고 나라와 싸운다. 봇도 관계를 따지게 만들면 그게 사라진다.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai import nation
from domynion.ai.nation import NationBot
from domynion.ai.tribe import TribeBot
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(kinds: dict[int, str]) -> GameState:
    gm = GameMap.from_rows(["." * 60] * 8)
    players = {}
    for pid, kind in kinds.items():
        for x in range(pid * 6, pid * 6 + 6):
            for y in range(0, 3):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 6, 0))
        p.kind = kind
        p.is_bot = kind == "bot"
        p.troops = 100_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 18 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def bot(pid: int = 1, seed: int = 0) -> TribeBot:
    return TribeBot(pid=pid, rng=random.Random(seed))


# --- 나라와 봇은 다른 AI 다 -------------------------------------------------

def test_attach_gives_bots_and_nations_different_brains():
    st = state({0: "human", 1: "nation", 2: "bot"})
    ai = {b.pid: type(b).__name__ for b in nation.attach(st, random.Random(0))}
    assert ai == {1: "NationBot", 2: "TribeBot"}


def test_the_human_gets_no_ai():
    st = state({0: "human", 1: "bot"})
    assert all(b.pid != 0 for b in nation.attach(st, random.Random(0)))


# --- 동맹: 무조건 받는다 ----------------------------------------------------

def test_a_bot_accepts_every_alliance_request():
    """관계도 배신자도 안 본다 — 이게 봇과 나라의 가장 큰 차이다."""
    st = state({0: "human", 1: "bot"})
    st.players[1].relations.update(0, -100)      # 최악의 관계인데도
    st.request_alliance(0, 1)
    bot(1)._accept_everything(st)
    assert st.diplomacy.allied(0, 1)


def test_a_bot_even_allies_with_a_traitor():
    """나라는 배신자를 90% 거절한다. 봇은 그 규칙이 없다."""
    st = state({0: "human", 1: "bot", 2: "nation"})
    st.diplomacy.form(0, 2, tick=0)
    st.diplomacy.break_alliance(0, 2, tick=st.tick_count)
    assert st.is_traitor(0)
    st.request_alliance(0, 1)
    bot(1)._accept_everything(st)
    assert st.diplomacy.allied(0, 1)


def test_a_nation_would_have_refused_the_same_request():
    """비교 기준 — 이게 없으면 위 테스트가 무엇과 다른지 알 수 없다."""
    st = state({0: "human", 1: "nation"})
    st.players[1].relations.update(0, -100)
    assert NationBot(pid=1, rng=random.Random(0),
                     difficulty="medium")._accepts_alliance(st, 0) is False


# --- 건물: 지운다 -----------------------------------------------------------

def test_a_bot_deletes_its_structures_one_at_a_time():
    """정복으로 넘어온 건물이 봇 손에 쌓이면 아무도 못 쓰는 채로 남는다.

    ⚠ **이 테스트는 우리 발명품을 재고 있었다**(§5.78). 봇이 `units.remove()` 로
    그 자리에서 지우고 쿨다운도 이 파일에만 있는 10 tick 이었다. 원본은
    `DeleteUnitExecution` 을 예약하므로 사람이 누른 것과 **같은 경로**를 탄다 —
    30초 쿨다운 · 30초 뒤 실제 삭제 · 그동안 건물은 계속 동작(§5.29).

    막지 않았으면: 봇 손의 건물이 원본보다 30배 빨리 사라진다."""
    st = state({0: "human", 1: "bot"})
    p = st.players[1]
    for u in (UnitType.CITY, UnitType.PORT, UnitType.FACTORY):
        p.units.units.append(Unit(u, 1, tile=st.gmap.ref(7, 1)))
    b = bot(1)
    # 판 시작 직후에는 아무도 못 지운다 — 쿨다운이 마지막 철거 시각(-1)부터
    # 재기 때문이다. 원본도 같다. 시계를 그만큼 넘겨 둔다.
    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS

    b._delete_a_structure(st)
    marked = [u for u in p.units.units if u.marked_for_deletion]
    assert len(marked) == 1, "한 번에 하나씩 예약한다"
    assert len(p.units.units) == 3, "예약만 했는데 그 자리에서 사라졌다"

    b._delete_a_structure(st)
    assert len([u for u in p.units.units if u.marked_for_deletion]) == 1, \
        "쿨다운 중에는 안 지운다"

    st.tick_count += C.DELETE_UNIT_COOLDOWN_TICKS
    b._delete_a_structure(st)
    assert len([u for u in p.units.units if u.marked_for_deletion]) == 2

    st.tick_count += C.DELETION_MARK_DURATION_TICKS + 1
    st._advance_deletions()
    assert len(p.units.units) == 1, "예약이 지났는데 안 사라졌다"


def test_deleting_does_nothing_when_there_is_nothing_to_delete():
    st = state({0: "human", 1: "bot"})
    bot(1)._delete_a_structure(st)          # 터지지 않으면 된다
    assert st.players[1].units.units == []


# --- 공격: 배신자 우선 ------------------------------------------------------

def test_a_bot_hunts_traitors_nearby():
    st = state({0: "human", 1: "bot", 2: "nation"})
    st.diplomacy.form(0, 2, tick=0)
    st.diplomacy.break_alliance(0, 2, tick=st.tick_count)
    assert bot(1)._nearby_traitor(st) == 0


def test_only_neighbours_count_as_traitors_to_hunt():
    """멀리 있는 배신자까지 쫓아가면 봇이 자기 자리를 비운다."""
    st = state({0: "human", 1: "bot", 2: "nation", 3: "nation"})
    st.diplomacy.form(3, 2, tick=0)
    st.diplomacy.break_alliance(3, 2, tick=st.tick_count)
    assert st.is_traitor(3)
    assert bot(1)._nearby_traitor(st) is None, "P3 은 P1 과 안 붙어 있다"


def test_a_bot_breaks_an_alliance_to_punish_a_traitor():
    """**동맹이어도 깨고 친다.** 배신자를 감싸 주지 않는다."""
    st = state({0: "human", 1: "bot"})
    st.diplomacy.form(0, 1, tick=0)
    st.players[1].troops = 500_000.0
    st.diplomacy.break_alliance(0, 1, tick=st.tick_count)
    st.diplomacy.form(0, 1, tick=st.tick_count)     # 다시 동맹, P0 은 배신자
    assert st.is_traitor(0)

    broke = False
    for seed in range(60):
        s2 = state({0: "human", 1: "bot"})
        s2.diplomacy.form(0, 1, tick=0)
        s2.diplomacy.break_alliance(0, 1, tick=s2.tick_count)
        s2.diplomacy.form(0, 1, tick=s2.tick_count)
        s2.players[1].troops = 500_000.0
        b = bot(1, seed)
        b._first_attack_done = True
        b._maybe_attack(s2)
        if not s2.diplomacy.allied(0, 1):
            broke = True
            break
    assert broke, "60번 시도해도 동맹을 안 깼다"


def test_a_bot_holds_troops_until_the_trigger_ratio():
    """막지 않았으면: 봇이 병력 한 줌으로 계속 찔러 아무것도 못 뺏는다."""
    st = state({0: "human", 1: "bot"})
    b = bot(1)
    st.players[1].troops = 1.0
    assert b._has_trigger_troops(st) is False
    st.players[1].troops = st.players[1].max_troops(st.tiles(1))
    assert b._has_trigger_troops(st) is True


def test_the_first_decision_always_expands_into_neutral():
    """막지 않았으면: 봇이 병력 문턱을 넘을 때까지 가만히 있어 지도가 안 찬다."""
    st = state({0: "human", 1: "bot"})
    b = bot(1)
    # 문턱(상한의 50~60%)에는 못 미치되 최소 공격 병력은 넘는 값이라야,
    # 거부된 이유가 문턱인지 병력 부족인지 헷갈리지 않는다.
    # ⚠ `expand_ratio`(10~20%) **위**여야 한다 — 그 아래면 보낼 병력이 음수라
    # 문턱과 무관하게 안 나간다(§5.78 에서 공격 병력 공식을 원본으로 고쳤다).
    cap = st.players[1].max_troops(st.tiles(1))
    st.players[1].troops = max(C.ATTACK_MIN_TROOPS * 4, cap * 0.35)
    assert not b._has_trigger_troops(st), "문턱은 못 넘은 상태여야 한다"
    assert st.players[1].troops > cap * b.expand_ratio, "재료: 보낼 병력이 있어야 한다"

    st.tick_count = b.attack_tick
    b.tick(st)
    assert b._first_attack_done
    assert st.attacks, "첫 판단에서는 문턱을 무시하고 나가야 한다"


def test_bots_do_nothing_during_the_spawn_phase():
    st = state({0: "human", 1: "bot"})
    st.spawn_phase = True
    b = bot(1)
    st.tick_count = b.attack_tick
    b.tick(st)
    assert not st.attacks


# --- 판 구성 ----------------------------------------------------------------

def test_a_new_game_places_nations_by_name_and_fills_with_bots():
    st = GameState.new(6, random.Random(0), map_name="world",
                       human=0, size="map16x", bots=10)
    kinds = [p.kind for p in st.players.values()]
    assert kinds.count("human") == 1
    assert kinds.count("nation") == 5
    assert kinds.count("bot") == 10
    names = [p.name for p in st.players.values() if p.kind != "bot"]
    assert "United States" in names or "Canada" in names, names


def test_nations_land_near_their_real_coordinates():
    """아메리카가 아메리카에 있어야 한다 — 좌표 해상도를 잘못 나누면 어긋난다.

    ⚠ 기대값을 `gm.nations` 에서 가져오면 **검사 대상과 같은 변환을 거친 값**이라
    자기 자신과 비교하게 된다. 해상도를 안 나누는 돌연변이가 그대로 통과했다.
    manifest 원본 좌표에서 직접 계산한다."""
    import json
    import pathlib as _pl

    raw = json.loads(
        (_pl.Path("resources/maps/world/manifest.json")).read_text(
            encoding="utf-8"))
    sw, sh = raw["nation_coord_space"]
    want = {n["name"]: n["coordinates"] for n in raw["nations"]}

    st = GameState.new(3, random.Random(0), map_name="world",
                       human=-1, size="map4x", bots=0)
    gm = st.gmap
    checked = 0
    for p in st.players.values():
        if p.name not in want:
            continue
        cx, cy = want[p.name]
        wx, wy = cx * gm.width / sw, cy * gm.height / sh
        gx, gy = p.start % gm.width, p.start // gm.width
        assert abs(wx - gx) + abs(wy - gy) <= 60,             f"{p.name}: manifest {wx:.0f},{wy:.0f} 인데 {gx},{gy} 에 앉았다"
        checked += 1
    assert checked >= 3, "이름이 하나도 안 맞으면 아무것도 안 잰 것이다"


def test_bots_are_optional():
    st = GameState.new(3, random.Random(0), map_name="world",
                       human=0, size="map16x", bots=0)
    assert not any(p.kind == "bot" for p in st.players.values())
