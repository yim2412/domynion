"""원본 봇 이식 — `NationExecution` + `AiAttackBehavior`.

핵심은 **세 비율의 비대칭**이다. 중립을 먹을 때는 `expand_ratio`(10~20%)만 남기고
거의 전부 쏟는데, 사람을 칠 때는 `reserve_ratio`(30~40%)를 남긴다. 이 차이가 원본
봇의 성격을 만든다 — 빈 땅은 게걸스럽게, 사람은 여유가 있을 때만.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import (ATTACK_RATE, MIN_ATTACK_RATIO, RETAIN_FRACTION,
                                NationBot, attach)
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState


def state(rows: list[str] | None = None) -> GameState:
    gm = GameMap.from_rows(rows or ["." * 60] * 30)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(0 if pid == 0 else 59, pid)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def bot(pid: int = 0, difficulty: str = "medium", seed: int = 1) -> NationBot:
    return NationBot(pid=pid, rng=random.Random(seed), difficulty=difficulty)


# --- 비율 -------------------------------------------------------------------

def test_ratios_land_in_the_original_ranges():
    """`trigger` 50~60%, `reserve` 30~40%, `expand` 10~20%."""
    for seed in range(30):
        b = bot(seed=seed)
        assert 0.50 <= b.trigger_ratio <= 0.60
        assert 0.30 <= b.reserve_ratio <= 0.40
        assert 0.10 <= b.expand_ratio <= 0.20
        assert b.expand_ratio < b.reserve_ratio < b.trigger_ratio


def test_reaction_rate_depends_on_difficulty():
    """`getAttackRate()` — easy 는 6.5~10초, impossible 은 3~5초에 한 번만 판단한다.

    매 tick 판단하게 두면 사람이 흉내 낼 수 없는 손놀림이 된다."""
    for name, (lo, hi) in ATTACK_RATE.items():
        rates = {bot(difficulty=name, seed=s).attack_rate for s in range(40)}
        assert min(rates) >= lo and max(rates) <= hi
    assert ATTACK_RATE["easy"][0] > ATTACK_RATE["impossible"][1], \
        "쉬울수록 느리게 반응해야 한다"


# --- 병력 배분 --------------------------------------------------------------

def test_expansion_keeps_far_less_than_a_player_attack():
    """중립은 `expand_ratio`, 사람은 `reserve_ratio` 를 남긴다 — 이게 비대칭의 핵심.

    막지 않았으면(둘을 같게 두면): 빈 땅 확장이 굼떠져 봇이 초반에 자라지 못한다."""
    st = state()
    b = bot()
    p = st.players[0]
    p.troops = p.max_troops(1) * 0.9

    to_neutral = b._attack_troops(st, None)
    to_player = b._attack_troops(st, 1)
    assert to_neutral is not None and to_player is not None
    assert to_neutral > to_player
    cap = p.max_troops(1)
    assert to_neutral == pytest.approx(p.troops - cap * b.expand_ratio)
    assert to_player == pytest.approx(p.troops - cap * b.reserve_ratio)


def test_below_trigger_ratio_it_does_not_attack_at_all():
    """`trigger_ratio` 아래면 **공격을 고려조차 하지 않는다.**"""
    st = state()
    b = bot()
    p = st.players[0]
    p.troops = p.max_troops(1) * (b.trigger_ratio - 0.05)
    assert b._attack_troops(st, None) is None
    p.troops = p.max_troops(1) * (b.trigger_ratio + 0.05)
    assert b._attack_troops(st, None) is not None


def test_hard_bots_refuse_attacks_that_are_too_weak():
    """hard 이상은 상대 병력의 20% 미만이면 안 친다 — 병력만 버리는 짓이다.

    easy/medium 에는 이 제한이 없다(원본도 그렇다)."""
    st = state()
    st.players[1].troops = 10_000_000.0
    hard = bot(difficulty="hard")
    p = st.players[0]
    p.troops = p.max_troops(1) * 0.9
    assert hard._attack_troops(st, 1) is None, "약한 공격을 걸렀어야 한다"

    easy = bot(difficulty="easy")
    assert easy._attack_troops(st, 1) is not None, "easy 에는 제한이 없다"


def test_send_cap_only_applies_to_hard_and_above():
    st = state()
    assert bot(difficulty="medium")._send_cap(st) == float("inf")
    assert bot(difficulty="easy")._send_cap(st) == float("inf")
    assert "hard" in RETAIN_FRACTION and "impossible" in RETAIN_FRACTION
    assert RETAIN_FRACTION["impossible"] > RETAIN_FRACTION["hard"]


def test_bot_owning_structures_is_attacked_with_expand_ratio():
    """구조물을 가진 봇은 **평소 여유를 기다리지 않고** 친다 — 원본 주석: 뺏긴
    건물을 되찾아야 하는데 봇은 그걸 지워 버리기 때문이다."""
    from domynion.core.units import Unit, UnitType
    st = state()
    foe = st.players[1]
    foe.kind = "bot"
    foe.is_bot = True
    foe.units.units.append(Unit(UnitType.CITY, 1, tile=st.gmap.ref(59, 1)))
    b = bot()
    p = st.players[0]
    p.troops = p.max_troops(1) * 0.9
    cap = p.max_troops(1)
    assert b._attack_troops(st, 1) == pytest.approx(p.troops - cap * b.expand_ratio)


# --- 반응 주기 --------------------------------------------------------------

def test_it_only_decides_on_its_own_tick():
    st = state()
    b = bot()
    calls = []
    b._maybe_attack = lambda s: calls.append(s.tick_count)
    b._structures = lambda s: None
    for _ in range(b.attack_rate * 3):
        st.tick_count += 1
        b.tick(st)
    assert len(calls) == 3, f"{b.attack_rate}tick 마다 한 번이어야 하는데 {len(calls)}회"


def test_dead_or_finished_games_are_skipped():
    st = state()
    b = bot()
    called = []
    b._maybe_attack = lambda s: called.append(1)
    st.players[0].alive = False
    st.tick_count = b.attack_tick
    b.tick(st)
    assert not called


# --- 통합 -------------------------------------------------------------------

def test_bots_actually_expand_on_a_real_map():
    st = state(["." * 120] * 60)
    bots = attach(st, random.Random(2), "medium")
    assert len(bots) == 2
    for _ in range(600):
        st.tick()
        for b in bots:
            b.tick(st)
        if st.over:
            break
    assert st.tiles(0) > 1 or st.tiles(1) > 1, "봇이 한 칸도 못 넓혔다"
    assert st.verify_counts()


def test_attach_skips_human_players():
    st = state()
    st.players[0].kind = "human"
    bots = attach(st, random.Random(0))
    assert [b.pid for b in bots] == [1]


# --- 전함 판단 (이식 누락 스물다섯) -------------------------------------------
#
# 전에는 골드가 되면 무조건 지었다. 실측에서 판 전체 지출의 85%
# (535,000,000 / 2,140척)가 전함으로 갔고, 그래서 아무도 사일로를 못 샀다.

def _sea_state():
    """바다가 넓은 지도 — 전함을 띄울 자리가 실제로 있어야 한다."""
    from domynion.core.units import Unit, UnitType
    gm = GameMap.from_rows(["." * 20 + "~" * 280] * 200)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid, 0)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: 1, 1: 1}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    return st


def _with_port(st, pid: int = 0, x: int = 19, y: int = 5):
    from domynion.core.units import Unit, UnitType
    u = Unit(UnitType.PORT, pid, tile=st.gmap.ref(x, y))
    st.gmap.owner[u.tile] = pid
    st.players[pid].units.units.append(u)
    st.players[pid].units.record_constructed(UnitType.PORT)
    return u


def test_a_nation_builds_only_one_warship():
    """`ships.length === 0` — **한 척도 없을 때만** 새로 짓는다.

    ⚠ 이게 없으면 골드가 있는 한 무한히 짓는다. 실측 2,140척."""
    from domynion.core.units import UnitType
    st = _sea_state()
    _with_port(st)
    p = st.players[0]
    p.gold = 10_000_000
    b = bot(seed=5)
    built = 0
    for _ in range(300):
        st.tick_count += 1
        if b._maybe_spawn_warship(st, p):
            built += 1
    assert built == 1, f"{built}척을 지었다 — 한 척 뒤로는 안 지어야 한다"


def test_warship_spawn_is_a_coin_flip():
    """판단 tick 마다 50% 확률이다(`random.chance(50)`).

    ⚠ 확률만 재는 게 아니라 **확률이 실제로 걸리는지**를 잰다 — 첫 시도에서
    바로 짓지 않는 경우가 있어야 한다."""
    from domynion.core.units import UnitType
    firsts = []
    for seed in range(40):
        st = _sea_state()
        _with_port(st)
        p = st.players[0]
        p.gold = 10_000_000
        b = bot(seed=seed)
        for i in range(20):
            st.tick_count += 1
            if b._maybe_spawn_warship(st, p):
                firsts.append(i)
                break
    assert firsts, "아무도 못 지었다"
    assert min(firsts) == 0 and max(firsts) > 0, \
        f"확률이 안 걸린다(첫 성공 tick 분포 {sorted(set(firsts))})"


def test_no_warship_without_a_port():
    st = _sea_state()
    p = st.players[0]
    p.gold = 10_000_000
    b = bot(seed=5)
    assert not any(b._maybe_spawn_warship(st, p) for _ in range(50))


def test_warship_spawns_away_from_the_port():
    """항구 **옆**이 아니라 반경 250 안 아무 바다다.

    ⚠ 항구 옆에 몰아 두면 순찰 구역이 겹친다(§5.37). 그 자리가 곧 순찰 기점이다."""
    st = _sea_state()
    port = _with_port(st)
    p = st.players[0]
    p.gold = 10_000_000
    spots = set()
    for seed in range(30):
        b = bot(seed=seed)
        t = b._warship_spawn_tile(st, port.tile, C.WARSHIP_SPAWN_RADIUS)
        if t is not None:
            spots.add(t)
    assert len(spots) > 5, f"자리가 {len(spots)}곳뿐 — 항구 옆에 몰려 있다"
    w = st.gmap.width
    far = max(abs(t % w - port.tile % w) + abs(t // w - port.tile // w)
              for t in spots)
    assert far > 10, f"가장 먼 자리도 {far}칸 — 반경을 안 쓴다"


def test_retaliation_is_capped_at_ten():
    """보복은 10척까지다. 넘으면 짓지 않고 있던 배를 옮긴다."""
    from domynion.core.naval import Warship
    st = _sea_state()
    _with_port(st)
    p = st.players[0]
    p.gold = 100_000_000
    for i in range(C.WARSHIP_RETALIATION_CAP):
        st.warships.append(Warship(owner=0, tile=st.gmap.ref(100 + i, 50)))
    b = bot(difficulty="impossible", seed=1)
    before = len(st.warships)
    for _ in range(50):
        b._retaliate(st, st.gmap.ref(200, 100), 1, C.REL_WARSHIP_SANK_TRADE)
    assert len(st.warships) == before, f"상한을 넘겨 {len(st.warships)}척이 됐다"


def test_retaliation_builds_below_the_cap():
    """대조군 — 상한 아래면 실제로 짓는다."""
    st = _sea_state()
    _with_port(st)
    p = st.players[0]
    p.gold = 100_000_000
    b = bot(difficulty="impossible", seed=1)
    for _ in range(50):
        b._retaliate(st, st.gmap.ref(200, 100), 1, C.REL_WARSHIP_SANK_TRADE)
        if st.warships:
            break
    assert st.warships, "상한 아래인데 한 척도 안 지었다"


def test_easy_never_retaliates():
    """easy 는 보복하지 않는다 — 대조군이 impossible 이다."""
    def built(difficulty: str) -> int:
        st = _sea_state()
        _with_port(st)
        st.players[0].gold = 100_000_000
        b = bot(difficulty=difficulty, seed=2)
        for _ in range(60):
            b._retaliate(st, st.gmap.ref(200, 100), 1, C.REL_WARSHIP_SANK_TRADE)
        return len(st.warships)
    assert built("easy") == 0, "easy 가 보복했다"
    assert built("impossible") > 0, "impossible 이 보복 안 한다 — 대조군이 깨졌다"


def test_retaliation_hits_the_relation():
    """보복하면 그 상대를 보는 눈이 나빠진다 — **한 방향만**이다."""
    st = _sea_state()
    _with_port(st)
    st.players[0].gold = 100_000_000
    b = bot(difficulty="impossible", seed=1)
    before01 = st.players[0].relations.of(1)
    before10 = st.players[1].relations.of(0)
    for _ in range(50):
        b._retaliate(st, st.gmap.ref(200, 100), 1, C.REL_WARSHIP_SANK_TRADE)
        if st.warships:
            break
    assert st.players[0].relations.of(1) < before01, "관계가 안 나빠졌다"
    assert st.players[1].relations.of(0) == before10, "양방향으로 바꿨다"


def test_capture_triggers_retaliation():
    """내 무역선이 **나포당하면** 보복한다. 도착·격침은 아니다.

    ⚠ 나포 한 건당 보복 시도는 **한 번**이다(원본도 그 배를 추적에서 뺀다).
    impossible 도 80% 라 한 건만 보면 놓칠 수 있어, 여러 건을 흘려 보낸다."""
    from domynion.core.naval import TradeShip
    st = _sea_state()
    _with_port(st)
    st.players[0].gold = 100_000_000
    b = bot(difficulty="impossible", seed=1)
    for i in range(20):
        t = TradeShip(owner=0, src_port=st.gmap.ref(19, 5),
                      dst_port=st.gmap.ref(19, 9), dst_owner=1,
                      path=[st.gmap.ref(100 + i, 50)])
        st.trade_ships.append(t)
        b._track_trade_ships(st)               # 추적 시작
        t.captured_by = 1                       # 나포당했다
        b._track_trade_ships(st)               # 여기서 보복 판단이 돈다
        st.trade_ships.remove(t)
        if st.warships:
            break
    assert st.warships, "20건을 나포당했는데 보복을 한 번도 안 했다"


def test_arrival_does_not_trigger_retaliation():
    """대조군 — 그냥 사라진 배(도착)에는 보복하지 않는다."""
    from domynion.core.naval import TradeShip
    st = _sea_state()
    _with_port(st)
    st.players[0].gold = 100_000_000
    b = bot(difficulty="impossible", seed=1)
    t = TradeShip(owner=0, src_port=st.gmap.ref(19, 5), dst_port=st.gmap.ref(19, 9),
                  dst_owner=1, path=[st.gmap.ref(100, 50)])
    st.trade_ships.append(t)
    b._track_trade_ships(st)
    st.trade_ships.clear()                     # 도착해서 목록에서 빠졌다
    for _ in range(50):
        b._track_trade_ships(st)
    assert not st.warships, "도착에 보복했다"

    # ⚠ 목록에서 빠지는 것만으로는 안 잡힌다 — 빠진 배는 추적에서 지워지고
    # 보복 판단에 닿지도 않는다. **살아 있고 아직 내 것인** 배를 계속 두고
    # 보복이 안 나가는지를 봐야 "나포당했을 때만"이 재진다.
    for i in range(30):
        t2 = TradeShip(owner=0, src_port=st.gmap.ref(19, 5),
                       dst_port=st.gmap.ref(19, 9), dst_owner=1,
                       path=[st.gmap.ref(100 + i, 50)])
        st.trade_ships.append(t2)
        b._track_trade_ships(st)
        b._track_trade_ships(st)               # 주인이 안 바뀐 채로 한 번 더
    assert not st.warships, "나포당하지도 않았는데 보복했다"


def test_warship_spawn_tile_is_always_ocean():
    """`warshipSpawnTile` 계약 — **반드시 바다다.**

    ⚠ 엔진 경로로는 안 잡힌다: `build_warship` 이 육지를 다시 거르므로 육지를
    돌려줘도 그냥 짓기에 실패할 뿐이고, 다음 판단 tick 에 또 시도해 결국 짓는다.
    그래서 함수를 직접 부르고, **육지가 후보에 실제로 많이 섞이는** 지도로 잰다."""
    gm = GameMap.from_rows(["." * 150 + "~" * 150] * 60)
    ps = {}
    for pid in (0, 1):
        t = gm.ref(pid, 0)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
        gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts, st._posts = {0: 1, 1: 1}, DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    port_tile = gm.ref(149, 30)                # 육지 끝 — 반경의 절반이 육지다
    from domynion.core.constants import Terrain
    found = 0
    for seed in range(60):
        b = bot(seed=seed)
        t = b._warship_spawn_tile(st, port_tile, 20)
        if t is None:
            continue
        found += 1
        assert gm.terrain[t] == Terrain.OCEAN, "육지를 골랐다"
    assert found > 30, f"{found}번만 자리를 찾았다 — 재료가 약하다"


def test_a_ship_already_travelling_is_not_recalled():
    """`maybeMoveWarship` — 기점에서 **130 넘게** 떨어진 배는 다시 안 부른다.

    ⚠ 부르면 가던 길을 버리고 되돌아와 아무 데도 못 간다.
    대조군으로 가까이 있는 배는 불려야 한다."""
    from domynion.core.naval import Warship
    st = _sea_state()
    b = bot(seed=1)
    origin = st.gmap.ref(100, 50)
    far = Warship(owner=0, tile=st.gmap.ref(280, 190), patrol_origin=origin)
    w2 = st.gmap.width
    d = (abs(far.tile % w2 - origin % w2) + abs(far.tile // w2 - origin // w2))
    assert d > C.WARSHIP_REASSIGN_RANGE, f"떨어진 거리가 {d} — 재료가 약하다"
    target = st.gmap.ref(150, 60)
    b._move_warship(st, [far], target)
    assert far.patrol_origin == origin, "이동 중인 배를 다시 불렀다"

    near = Warship(owner=0, tile=st.gmap.ref(101, 50), patrol_origin=origin)
    b._move_warship(st, [near], target)
    assert near.patrol_origin == target, "가까운 배를 안 불렀다 — 대조군이 깨졌다"
