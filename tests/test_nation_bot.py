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


def test_a_sunk_transport_triggers_retaliation():
    """내 수송선이 **격침당하면** 보복한다.

    ⚠ 무역선보다 관계가 크게 깎인다(−15 대 −7.5) — 병력을 실은 배라서다."""
    from domynion.core.naval import TransportShip
    st = _sea_state()
    _with_port(st)
    st.players[0].gold = 100_000_000
    b = bot(difficulty="impossible", seed=1)
    for i in range(20):
        boat = TransportShip(owner=0, target=1, troops=100.0,
                             path=[st.gmap.ref(100 + i, 50)],
                             dst=st.gmap.ref(100 + i, 50))
        st.boats.append(boat)
        b._track_transport_ships(st)          # 추적 시작
        boat.active, boat.sunk_by = False, 1   # 적 전함이 격침시켰다
        st.boats.remove(boat)
        b._track_transport_ships(st)          # 여기서 보복 판단이 돈다
        if st.warships:
            break
    assert st.warships, "20척을 잃었는데 보복을 한 번도 안 했다"


def test_an_arrived_transport_does_not_trigger_retaliation():
    """대조군 — 도착·퇴각으로 빠진 배에는 보복하지 않는다.

    ⚠ 목록에서 빠지는 이유가 셋(도착·퇴각·격침)인데 빠졌다는 것만으로는
    구별되지 않는다. `sunk_by` 가 없으면 보복도 없다."""
    from domynion.core.naval import TransportShip
    st = _sea_state()
    _with_port(st)
    st.players[0].gold = 100_000_000
    b = bot(difficulty="impossible", seed=1)
    for i in range(30):
        boat = TransportShip(owner=0, target=1, troops=100.0,
                             path=[st.gmap.ref(100 + i, 50)],
                             dst=st.gmap.ref(100 + i, 50))
        st.boats.append(boat)
        b._track_transport_ships(st)
        boat.active = False                    # 도착했다 — sunk_by 는 없다
        st.boats.remove(boat)
        b._track_transport_ships(st)
    assert not st.warships, "도착한 배에 보복했다"


def test_engine_marks_who_sank_a_transport():
    """**배선** 검사 — 엔진이 격침시킬 때 `sunk_by` 를 실제로 남기는가.

    ⚠ 봇만 단위 테스트하면 엔진이 표시를 안 남겨도 통과한다."""
    from domynion.core.naval import TransportShip, Warship
    st = _sea_state()
    boat = TransportShip(owner=1, target=0, troops=100.0,
                         path=[st.gmap.ref(100, 50)], dst=st.gmap.ref(100, 50))
    st.boats.append(boat)
    st.warships.append(Warship(owner=0, tile=st.gmap.ref(101, 50)))
    st.tick_count += 1
    st._advance_warships()
    assert boat not in st.boats, "격침이 안 됐다 — 재료가 잘못됐다"
    assert boat.active is False and boat.sunk_by == 0, \
        "엔진이 격침 표시를 안 남겼다"


def test_sinking_a_transport_hurts_more_than_capturing_a_trade_ship():
    """수송선 격침은 −15, 무역선 나포는 −7.5.

    ⚠ 값을 두 자리에 따로 쓰면 한쪽을 다른 쪽으로 바꿔도 "보복했다"는 그대로다.
    두 경로의 **관계 감소량을 직접 비교**해야 잡힌다."""
    from domynion.core.naval import TradeShip, TransportShip

    def drop(kind: str) -> float:
        st = _sea_state()
        _with_port(st)
        st.players[0].gold = 100_000_000
        b = bot(difficulty="impossible", seed=1)
        # ⚠ `of()` 는 **구간(enum)** 이다 — −15 든 −7.5 든 한 칸 내려가면 같아진다.
        # 원값을 주는 `value()` 로 재야 두 값의 차이가 보인다.
        before = st.players[0].relations.value(1)
        for i in range(40):
            if kind == "boat":
                x = TransportShip(owner=0, target=1, troops=100.0,
                                  path=[st.gmap.ref(100 + i, 50)],
                                  dst=st.gmap.ref(100 + i, 50))
                st.boats.append(x)
                b._track_transport_ships(st)
                x.active, x.sunk_by = False, 1
                st.boats.remove(x)
                b._track_transport_ships(st)
            else:
                x = TradeShip(owner=0, src_port=st.gmap.ref(19, 5),
                              dst_port=st.gmap.ref(19, 9), dst_owner=1,
                              path=[st.gmap.ref(100 + i, 50)])
                st.trade_ships.append(x)
                b._track_trade_ships(st)
                x.captured_by = 1
                b._track_trade_ships(st)
                st.trade_ships.remove(x)
            if st.warships:
                break
        assert st.warships, f"{kind}: 보복을 안 했다 — 재료가 잘못됐다"
        return before - st.players[0].relations.value(1)

    assert drop("boat") > drop("trade"), "수송선 격침이 더 아프지 않다"


def test_an_arrived_boat_is_not_marked_as_sunk():
    """**배선** 검사 — 도착·퇴각으로 빠진 배에는 `sunk_by` 가 없어야 한다.

    ⚠ 봇만 재면 엔진이 도착한 배까지 격침으로 표시해도 통과한다."""
    from domynion.core.naval import TransportShip
    st = _sea_state()
    boat = TransportShip(owner=0, target=1, troops=100.0,
                         path=[st.gmap.ref(100, 50), st.gmap.ref(101, 50)],
                         dst=st.gmap.ref(101, 50))
    st.boats.append(boat)
    for _ in range(6):
        st.tick_count += 1
        st._advance_boats()
        if boat not in st.boats:
            break
    assert boat not in st.boats, "배가 도착을 안 했다 — 재료가 잘못됐다"
    assert boat.active is False, "빠졌는데 active 가 그대로다"
    assert boat.sunk_by is None, "도착한 배를 격침으로 표시했다"


def test_a_nuke_marks_who_sank_the_boat():
    """**배선** 검사 — 핵에 날아간 배도 누가 쐈는지 남긴다."""
    from domynion.core.naval import TransportShip
    from domynion.core.nukes import Nuke
    from domynion.core.units import UnitType
    st = _sea_state()
    where = st.gmap.ref(100, 50)
    boat = TransportShip(owner=0, target=1, troops=100.0, path=[where], dst=where)
    st.boats.append(boat)
    n = Nuke(owner=1, utype=UnitType.ATOM_BOMB, src=st.gmap.ref(0, 0), dst=where)
    st._detonate(n)
    assert boat not in st.boats, "핵에 안 날아갔다 — 재료가 잘못됐다"
    assert boat.sunk_by == 1, "핵으로 격침시킨 사람을 안 남겼다"


# --- 바다 독점 견제 (`counterWarshipInfestation`) ------------------------------

def _infest(st, enemy: int, n: int):
    """적 전함을 n 척 깔아 독점 상태를 만든다."""
    from domynion.core.naval import Warship
    for i in range(n):
        st.warships.append(Warship(owner=enemy, tile=st.gmap.ref(100 + i, 60)))


def _rich_setup(difficulty: str = "hard", enemy_ships: int = 12,
                my_gold: int = 100_000_000, enemy_gold: int = 0):
    st = _sea_state()
    _with_port(st)
    st.players[0].gold = my_gold
    st.players[1].gold = enemy_gold
    _infest(st, 1, enemy_ships)
    return st, bot(difficulty=difficulty, seed=1)


def test_a_rich_hard_nation_counters_an_infestation():
    """적이 전함으로 바다를 덮으면 그 옆에 내 전함을 띄운다."""
    st, b = _rich_setup()
    b._counter_infestation(st)
    assert any(w.owner == 0 for w in st.warships), "견제를 안 했다"


def test_only_hard_and_above_counter():
    """easy·medium 은 안 한다 — 원본 주석: *"Only the smart nations"*.

    대조군은 hard 다."""
    for d in ("easy", "medium"):
        st, b = _rich_setup(difficulty=d)
        b._counter_infestation(st)
        assert not any(w.owner == 0 for w in st.warships), f"{d} 가 견제했다"
    st, b = _rich_setup(difficulty="hard")
    b._counter_infestation(st)
    assert any(w.owner == 0 for w in st.warships), "hard 가 안 한다 — 대조군이 깨졌다"


def test_a_poor_nation_does_not_counter():
    """부자 상위 3 밖이면 안 한다 — 마지막 골드를 여기 쓰면 안 된다.

    ⚠ 골드가 아예 없어서 못 하는 것과 구분해야 한다. **골드는 충분한데
    남들이 더 부자인** 상황으로 잰다."""
    st = _sea_state()
    _with_port(st)
    _infest(st, 1, 12)
    st.players[0].gold = 1_000_000            # 전함(250,000)은 충분히 산다
    st.players[1].gold = 9_000_000
    # 상위 3을 채울 부자 셋을 더 둔다
    for pid in (2, 3, 4):
        st.players[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation",
                                      start=st.gmap.ref(pid, 0))
        st.gmap.owner[st.gmap.ref(pid, 0)] = pid
        st._counts[pid] = 1
        st.players[pid].gold = 8_000_000
    b = bot(difficulty="hard", seed=1)
    assert st.players[0].gold >= st.players[0].units.cost(
        __import__("domynion.core.units", fromlist=["UnitType"]).UnitType.WARSHIP)
    b._counter_infestation(st)
    assert not any(w.owner == 0 for w in st.warships), "가난한데 견제했다"


def test_a_thin_sea_is_not_an_infestation():
    """판 전체 전함이 10 이하면 독점이라 하지 않는다.

    대조군은 12척이다."""
    st, b = _rich_setup(enemy_ships=8)
    b._counter_infestation(st)
    assert not any(w.owner == 0 for w in st.warships), "8척인데 견제했다"


def test_an_enemy_with_few_ships_is_not_a_target():
    """한 적이 10척을 넘어야 표적이다. 여럿이 나눠 가진 것은 독점이 아니다."""
    st = _sea_state()
    _with_port(st)
    st.players[0].gold = 100_000_000
    for pid in (1, 2):
        if pid not in st.players:
            st.players[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation",
                                          start=st.gmap.ref(pid, 0))
            st.gmap.owner[st.gmap.ref(pid, 0)] = pid
            st._counts[pid] = 1
        _infest(st, pid, 7)                    # 합쳐 14척, 각자는 7척
    b = bot(difficulty="hard", seed=1)
    b._counter_infestation(st)
    assert not any(w.owner == 0 for w in st.warships), \
        "아무도 10척을 안 넘었는데 견제했다"


def test_allies_are_not_countered():
    """동맹의 함대는 독점으로 보지 않는다."""
    st, b = _rich_setup()
    st.diplomacy.form(0, 1, tick=0)
    b._counter_infestation(st)
    assert not any(w.owner == 0 for w in st.warships), "동맹을 견제했다"


def test_countering_respects_my_own_cap():
    """내가 이미 10척이면 안 한다 — 견제한다고 내가 독점하면 안 된다."""
    from domynion.core.naval import Warship
    st, b = _rich_setup()
    for i in range(C.WARSHIP_RETALIATION_CAP):
        st.warships.append(Warship(owner=0, tile=st.gmap.ref(200 + i, 60)))
    before = sum(1 for w in st.warships if w.owner == 0)
    b._counter_infestation(st)
    assert sum(1 for w in st.warships if w.owner == 0) == before, "상한을 넘겼다"


def test_tick_actually_runs_the_infestation_counter():
    """**배선** 검사 — `tick()` 이 견제를 실제로 부르는가.

    ⚠ 앞의 테스트들은 `_counter_infestation` 을 직접 부른다. 그러면 `tick()`
    에서 호출을 지워도 전부 통과한다(로직과 배선을 따로 재야 한다)."""
    st, b = _rich_setup()
    for _ in range(max(4, b.attack_rate + 2)):
        st.tick_count += 1
        b.tick(st)
        if any(w.owner == 0 for w in st.warships):
            break
    assert any(w.owner == 0 for w in st.warships), "tick 이 견제를 안 부른다"


def test_rich_check_ignores_humans():
    """`isRichPlayer` 는 **사람을 세지 않는다.**

    ⚠ 사람을 세면 사람이 부자일 때 봇들의 순위가 밀려 견제가 통째로 멈춘다.
    골드가 많은 사람 셋을 두고도 봇이 견제하는지를 본다."""
    st, b = _rich_setup()
    for pid in (5, 6, 7):
        st.players[pid] = PlayerState(pid=pid, name=f"H{pid}", kind="human",
                                      start=st.gmap.ref(pid, 0))
        st.gmap.owner[st.gmap.ref(pid, 0)] = pid
        st._counts[pid] = 1
        st.players[pid].gold = 900_000_000     # 봇보다 훨씬 부자다
    b._counter_infestation(st)
    assert any(w.owner == 0 for w in st.warships), "사람을 세는 바람에 견제를 멈췄다"


# --- 들어오는 상륙선 선제 대응 -------------------------------------------------
#
# 보복이 당한 뒤라면 이쪽은 당하기 전이다.

def _incoming(st, enemy: int, at_x: int, dst_x: int):
    """적 상륙선 한 척을 내 땅으로 보낸다."""
    from domynion.core.naval import TransportShip
    dst = st.gmap.ref(dst_x, 5)
    st.gmap.owner[dst] = 0                     # 목표는 내 땅이어야 한다
    b = TransportShip(owner=enemy, target=0, troops=1000.0,
                      path=[st.gmap.ref(at_x, 5)], dst=dst)
    st.boats.append(b)
    return b


def _intercept_state(difficulty: str = "impossible"):
    st = _sea_state()
    _with_port(st)
    st.players[0].gold = 100_000_000
    return st, bot(difficulty=difficulty, seed=1)


def test_an_incoming_boat_draws_a_warship():
    """멀리서 오는 상륙선을 보면 미리 전함을 낸다."""
    st, b = _intercept_state()
    _incoming(st, 1, at_x=200, dst_x=10)       # 190칸 남았다
    for _ in range(30):
        b._intercept_incoming(st)
        if st.warships:
            break
        b._dealt_boats.clear()                 # 확률(80%)을 여러 번 굴린다
    assert any(w.owner == 0 for w in st.warships), "다가오는데 아무것도 안 했다"


def test_a_boat_about_to_land_is_ignored():
    """목표까지 20 안이면 손쓸 수 없다고 보고 넘긴다.

    ⚠ 대조군은 멀리 있는 배다(위 테스트). 그게 없으면 "안 했다"가
    기능이 통째로 죽어도 참이다."""
    st, b = _intercept_state()
    _incoming(st, 1, at_x=15, dst_x=10)        # 5칸 남았다
    for _ in range(30):
        b._intercept_incoming(st)
        b._dealt_boats.clear()
    assert not any(w.owner == 0 for w in st.warships), "코앞인데 배를 띄웠다"


def test_a_covered_target_is_ignored():
    """목표 90 안에 내 전함이 이미 있으면 또 띄우지 않는다."""
    from domynion.core.naval import Warship
    st, b = _intercept_state()
    dst_x = 100
    st.warships.append(Warship(owner=0, tile=st.gmap.ref(dst_x + 10, 5)))
    _incoming(st, 1, at_x=250, dst_x=dst_x)
    before = len(st.warships)
    for _ in range(30):
        b._intercept_incoming(st)
        b._dealt_boats.clear()
    assert len(st.warships) == before, "이미 덮인 자리에 또 띄웠다"


def test_a_patrol_origin_alone_counts_as_cover():
    """배는 멀리 있어도 **순찰 기점**이 목표 90 안이면 덮인 것으로 본다.

    ⚠ 배 자체를 가까이 두면 위치 검사가 먼저 잡아 기점 검사가 안 재진다."""
    from domynion.core.naval import Warship
    st, b = _intercept_state()
    dst_x = 100
    # ⚠ x 축으로 250 을 떼려다 지도 폭(300)을 넘겨 **줄바꿈되어 오히려
    # 가까워졌다.** 위치 검사가 먼저 잡아 기점 검사가 안 재졌다. y 축으로 뗀다.
    w = Warship(owner=0, tile=st.gmap.ref(dst_x, 180))       # 배는 멀다(175)
    w.patrol_origin = st.gmap.ref(dst_x + 10, 5)             # 기점은 가깝다(10)
    st.warships.append(w)
    _incoming(st, 1, at_x=280, dst_x=dst_x)
    before = len(st.warships)
    for _ in range(30):
        b._intercept_incoming(st)
        b._dealt_boats.clear()
    assert len(st.warships) == before, "기점이 가까운데 또 띄웠다"


def test_a_far_warship_does_not_count_as_cover():
    """대조군 — 90 밖의 전함은 덮은 것으로 치지 않는다."""
    from domynion.core.naval import Warship
    st, b = _intercept_state()
    dst_x = 100
    far = Warship(owner=0, tile=st.gmap.ref(dst_x, 180))     # 같은 이유로 y 축
    far.patrol_origin = far.tile
    st.warships.append(far)
    _incoming(st, 1, at_x=250, dst_x=dst_x)
    before = len(st.warships)
    for _ in range(30):
        b._intercept_incoming(st)
        if len(st.warships) > before:
            break
        b._dealt_boats.clear()
    assert len(st.warships) > before, "멀리 있는 배를 덮은 것으로 쳤다"


def test_an_allied_landing_is_not_intercepted():
    """동맹의 상륙선은 안 친다."""
    st, b = _intercept_state()
    st.diplomacy.form(0, 1, tick=0)
    _incoming(st, 1, at_x=200, dst_x=10)
    for _ in range(30):
        b._intercept_incoming(st)
        b._dealt_boats.clear()
    assert not any(w.owner == 0 for w in st.warships), "동맹을 쳤다"


def test_a_boat_headed_elsewhere_is_ignored():
    """남의 땅을 노리는 배는 내 일이 아니다."""
    from domynion.core.naval import TransportShip
    st, b = _intercept_state()
    dst = st.gmap.ref(10, 5)
    st.gmap.owner[dst] = 1                     # 목표가 내 땅이 아니다
    st.boats.append(TransportShip(owner=2, target=1, troops=1000.0,
                                  path=[st.gmap.ref(200, 5)], dst=dst))
    for _ in range(30):
        b._intercept_incoming(st)
        b._dealt_boats.clear()
    assert not any(w.owner == 0 for w in st.warships), "남의 싸움에 끼어들었다"


def test_each_boat_is_handled_once():
    """**한 척에 한 번만** 대응한다(`dealtWithTransportShip`).

    ⚠ "전함이 한 척뿐"으로는 안 잡힌다 — 띄운 배가 목표를 덮어 버려 **커버
    검사가 두 번째 시도를 대신 막는다.** 대응 판단 자체가 몇 번 불렸는지를
    직접 세야 한다. 이게 없으면 확률이 빗나갈 때마다 매 tick 다시 시도해
    §5.40 의 낭비가 되돌아온다."""
    st, b = _intercept_state()
    _incoming(st, 1, at_x=200, dst_x=10)
    calls = []
    b._retaliate = lambda *a, **k: calls.append(1)   # 짓지 않고 세기만 한다
    for _ in range(200):
        b._intercept_incoming(st)
    assert len(calls) == 1, f"같은 배에 {len(calls)}번 대응했다"


def test_only_one_boat_per_tick():
    """한 tick 에 **한 척만** 처리한다(원본의 `break`).

    ⚠ 여러 척이 동시에 오면 한 tick 에 함대가 통째로 나간다."""
    st, b = _intercept_state()
    for i in range(5):
        _incoming(st, 1, at_x=200 + i, dst_x=10 + i)
    calls = []
    b._retaliate = lambda *a, **k: calls.append(1)
    b._intercept_incoming(st)
    assert len(calls) == 1, f"한 tick 에 {len(calls)}척을 처리했다"


def test_a_retreating_boat_is_ignored():
    """퇴각 중인 배는 더 이상 위협이 아니다."""
    st, b = _intercept_state()
    boat = _incoming(st, 1, at_x=200, dst_x=10)
    boat.retreating = True
    calls = []
    b._retaliate = lambda *a, **k: calls.append(1)
    for _ in range(50):
        b._intercept_incoming(st)
        b._dealt_boats.clear()
    assert not calls, "퇴각하는 배에 대응했다"


def test_my_own_boat_is_ignored():
    """내 상륙선에는 대응하지 않는다."""
    from domynion.core.naval import TransportShip
    st, b = _intercept_state()
    dst = st.gmap.ref(10, 5)
    st.gmap.owner[dst] = 0
    st.boats.append(TransportShip(owner=0, target=1, troops=1000.0,
                                  path=[st.gmap.ref(200, 5)], dst=dst))
    calls = []
    b._retaliate = lambda *a, **k: calls.append(1)
    for _ in range(50):
        b._intercept_incoming(st)
        b._dealt_boats.clear()
    assert not calls, "내 배에 대응했다"


def test_tick_actually_runs_the_interceptor():
    """**배선** 검사 — `tick()` 이 선제 대응을 실제로 부르는가."""
    st, b = _intercept_state()
    _incoming(st, 1, at_x=200, dst_x=10)
    for _ in range(max(6, b.attack_rate + 2)):
        st.tick_count += 1
        b.tick(st)
        if any(w.owner == 0 for w in st.warships):
            break
        b._dealt_boats.clear()
    assert any(w.owner == 0 for w in st.warships), "tick 이 선제 대응을 안 부른다"
