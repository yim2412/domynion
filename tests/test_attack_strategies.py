"""나라 AI 의 공격 전략 사다리 — 이식 누락 예순다섯~일흔 (§5.76).

원본 `AiAttackBehavior.getAttackStrategies()` 는 **열세 개의 전략을 난이도별로 다른
순서로** 늘어놓고 위에서부터 하나가 성공할 때까지 내려간다. 우리에게는 그 자리에
*"가장 약한 적부터"* 한 줄(`_attack_best`)만 있었다.

| # | 원본 | 우리 |
|---|---|---|
| **예순다섯** | 난이도별 전략 순서 열셋 | `weakest` 하나 |
| **예순여섯** | `reserve_ratio` 관문 · `trigger_ratio` 는 1/10 로 뚫린다 | reserve 관문 없음 · trigger 가 엉뚱한 자리 |
| **예순일곱** | 봇은 **동시에 여러 개**(난이도별 1·1~2·3·100), 건물 가진 봇 먼저 | 한 번에 하나 |
| **예순여덟** | 중립 확장은 **낙진 없는 땅만**, 낙진 땅은 `nuked` 자리에서만 | 구분 없음 |
| **예순아홉** | `troopSendCap` 은 봇·동맹 이웃을 위협으로 안 센다 · 반격 하한 | 전부 셌다 |
| **일흔** | FFA 문턱들(`weakest` 는 나보다 약할 때만 등) | 없었다 |

⚠ `afk`(접속 끊김)와 `donate`(팀전 전용)는 **우리 판에 개념이 없어 항상 False** 지만
사다리에 자리를 남긴다 — 지우면 그 아래 전략들의 순서가 밀린다.
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import (ATTACK_STRATEGIES, BOT_PARALLELISM, NationBot)
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState
from domynion.core.units import Unit, UnitType


def state(n: int = 3, kinds=None, far=(), water: bool = False) -> GameState:
    """P0 은 **모두와 국경을 맞댄다.**

    ⚠ 처음에는 나라들을 한 줄로 나란히 뒀는데, 그러면 P0 은 P1 하고만 닿는다 —
    "봇 넷을 동시에 친다"도 "가장 크게 때린 쪽을 되받는다"도 **공격 자체가 성립하지
    않아** 전부 False 로 통과했다. 재료가 규칙을 가리는 자리다(§7).

    P0 은 0행 전체, 나머지는 1행을 열 칸씩 나눠 가져 세로로 맞닿는다.
    `far` 에 넣은 pid 는 8행에 둬 **일부러 안 닿게** 한다."""
    if water:
        # 0행 P0 · 1행 이웃(x<20) · 그 밖은 바다 · 8행은 바다 건너 땅.
        rows = ["." * 120,
                "." * 20 + "~" * 100] + ["~" * 120] * 6 + ["." * 120] \
            + ["~" * 120] * 11
    else:
        rows = ["." * 120] * 20
    gm = GameMap.from_rows(rows)
    ps = {}
    for pid in range(n):
        kind = (kinds or {}).get(pid, "nation")
        if pid == 0:
            tiles = [gm.ref(x, 0) for x in range(120)]
        elif pid in far:
            tiles = [gm.ref(x, 8) for x in range((pid - 1) * 10,
                                                 (pid - 1) * 10 + 10)]
        else:
            tiles = [gm.ref(x, 1) for x in range((pid - 1) * 10,
                                                 (pid - 1) * 10 + 10)]
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind=kind, start=tiles[0])
        for t in tiles:
            gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: (120 if pid == 0 else 10) for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    # 스폰 면역이 지난 뒤로 맞춘다 — 면역 중이면 사람 공격자는 아예 못 친다.
    # (재료로 반격을 만들려면 상대가 실제로 나를 칠 수 있어야 한다.)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    for p in ps.values():
        p.troops = 50_000.0
    return st


def bot(pid: int = 0, difficulty: str = "medium", seed: int = 1) -> NationBot:
    return NationBot(pid=pid, rng=random.Random(seed), difficulty=difficulty)


def rich(st: GameState, pid: int) -> None:
    """비율 관문을 넉넉히 넘겨 둔다 — 사다리 자체를 재고 싶을 때."""
    p = st.players[pid]
    p.troops = p.max_troops(st.tiles(pid)) * 0.95


# --- 예순다섯 · 사다리가 있다 ------------------------------------------------

def test_every_difficulty_has_its_own_order():
    """막지 않았으면: 난이도가 반응 주기와 사람 봐주기 말고는 아무 데도 안 남는다."""
    orders = {d: tuple(v) for d, v in ATTACK_STRATEGIES.items()}
    assert len(set(orders.values())) == 4, "네 난이도의 순서가 서로 다르지 않다"
    assert orders["easy"][0] == "nuked"
    assert orders["impossible"][0] == "retaliate", "impossible 은 반격이 최우선이다"
    assert "victim" not in orders["easy"] and "victim" not in orders["medium"], \
        "easy·medium 은 먹잇감 올라타기를 모른다"


def test_the_ladder_stops_at_the_first_success():
    """하나가 성공하면 그 아래는 안 본다 — 한 tick 에 여러 번 치면 안 된다."""
    st = state()
    rich(st, 0)
    b = bot()
    calls = []
    for name in ATTACK_STRATEGIES["medium"]:
        def make(n):
            def f(self, st_, fr, en):
                calls.append(n)
                return n == "betray"
            return f
        b._STRATEGIES = dict(b._STRATEGIES)
        b._STRATEGIES[name] = make(name)
    b._attack_best_target(st, [], [st.players[1]])
    assert calls[-1] == "betray", "성공한 뒤에도 계속 내려갔다"


def test_every_named_strategy_exists():
    for order in ATTACK_STRATEGIES.values():
        for name in order:
            assert name in NationBot._STRATEGIES, name


# --- 예순여섯 · 두 관문 ------------------------------------------------------

class AlwaysGo(random.Random):
    """`randrange(10)` 이 항상 0 — **trigger 관문의 1/10 을 통과시킨다.**

    ⚠ 이게 없으면 `reserve` 관문을 지워도 그 아래 `trigger` 관문이 9/10 을
    막아 테스트가 통과한다. 변이 하나가 그렇게 살아남았다."""

    def randrange(self, *a, **kw):
        return 0


def test_the_reserve_gate_blocks_everything():
    """`reserve_ratio` 아래면 **아무것도 안 한다** — trigger 의 1/10 조차 못 뚫는다.

    막지 않았으면: 모아 두는 구간이 사라져 나라가 늘 빈손으로 싸운다."""
    st = state()
    st.players[1].troops = 1_000.0        # 재료: 나보다 약해야 `weakest` 가 문다
    b = bot()
    b.rng = AlwaysGo()
    p = st.players[0]
    p.troops = p.max_troops(st.tiles(0)) * (b.reserve_ratio - 0.05)
    b._attack_best_target(st, [], [st.players[1]])
    assert not st.attacks, "1/10 이 열려 있어도 reserve 아래면 안 나가야 한다"

    p.troops = p.max_troops(st.tiles(0)) * (b.reserve_ratio + 0.02)
    b._attack_best_target(st, [], [st.players[1]])
    assert st.attacks, "재료: reserve 를 넘기면 나가야 한다"


def test_the_reserve_gate_also_holds_back_nuked_land():
    """⚠ **관문을 지우는 변이가 한 번 살아남았다.** 사람을 칠 때는 남겨 두는 양이
    `reserve_ratio` 자체라, 관문이 없어도 병력 계산이 음수가 되어 결과가 같았다.

    드러나는 자리는 **중립을 노리는 전략**이다 — 거기는 `expand_ratio`(10~20%)만
    남기므로 reserve 아래에서도 병력이 남는다. 관문이 없으면 `nuked` 가 그 상태로
    나간다."""
    st = state(n=2)
    st.fallout.add([st.gmap.ref(x, 1) for x in range(10, 120)]
                   + [st.gmap.ref(x, 2) for x in range(120)])
    b = bot()
    b.rng = AlwaysGo()
    p = st.players[0]
    p.troops = p.max_troops(st.tiles(0)) * (b.reserve_ratio - 0.05)
    assert p.troops > p.max_troops(st.tiles(0)) * b.expand_ratio,         "재료: expand 위 · reserve 아래여야 잰다"
    b._attack_best_target(st, [], [])
    assert not st.attacks, "reserve 아래인데 낙진 땅으로 나갔다"


def weak_bots(st: GameState, pids) -> None:
    """봇 병력을 작게 둔다.

    ⚠ **재료다.** 봇에게 보내는 양은 상대 병력의 네 배(`calculateBotAttackTroops`)라,
    봇이 나만큼 크면 여유가 모자라 한 대도 못 나간다 — 그러면 "병렬로 친다"가
    안 재진다. 실제 판의 봇은 나라보다 훨씬 작다."""
    for pid in pids:
        st.players[pid].troops = 2_000.0


def test_a_bot_with_structures_is_attacked_before_the_gates():
    """⚠ 원본 주석 그대로 — 시작 골드가 많은 판에서 나라는 도시를 짓느라 확장이
    느려지고 **봇이 건물을 훔쳐 지워 버린다.** 그래서 비율 검사보다 앞이다.

    막지 않았으면: 여유가 없는 동안 건물을 되찾을 방법이 없다."""
    st = state(kinds={1: "bot"})
    weak_bots(st, [1])
    st.players[1].units.units.append(Unit(UnitType.CITY, 1, tile=st.gmap.ref(12, 1)))
    b = bot()
    p = st.players[0]
    p.troops = p.max_troops(st.tiles(0)) * (b.reserve_ratio - 0.05)
    b._attack_best_target(st, [], [st.players[1]])
    assert st.attacks, "건물 가진 봇을 관문 앞에서 안 쳤다"


# --- 예순일곱 · 봇은 동시에 여러 개 -----------------------------------------

def test_bots_are_attacked_in_parallel_by_difficulty():
    """막지 않았으면: 봇이 400개인 판을 한 번에 하나씩 민다."""
    seen = {}
    for difficulty in ("easy", "hard", "impossible"):
        st = state(n=5, kinds={1: "bot", 2: "bot", 3: "bot", 4: "bot"})
        weak_bots(st, [1, 2, 3, 4])
        rich(st, 0)
        b = bot(difficulty=difficulty)
        b._attack_bots(st, [st.players[i] for i in (1, 2, 3, 4)])
        seen[difficulty] = len(st.attacks)
    assert seen["easy"] == 1
    assert seen["hard"] == 3
    assert seen["impossible"] == 4, "impossible 은 닿는 대로 전부 친다"
    assert BOT_PARALLELISM["impossible"] == 100


def test_bots_with_structures_go_first():
    """훔쳐 간 건물을 되찾는 것이 급하다 — 밀도보다 앞선다."""
    st = state(n=4, kinds={1: "bot", 2: "bot", 3: "bot"})
    weak_bots(st, [1, 2, 3])
    rich(st, 0)
    st.players[3].units.units.append(Unit(UnitType.PORT, 3, tile=st.gmap.ref(22, 1)))
    st.players[1].troops = 1.0                      # 밀도로는 1번이 가장 만만하다
    b = bot(difficulty="easy")                      # 하나만 고른다
    b._attack_bots(st, [st.players[i] for i in (1, 2, 3)])
    assert [a.target for a in st.attacks] == [3], "밀도가 건물보다 앞섰다"


def test_parallel_bot_attacks_do_not_spend_the_same_troops_twice():
    """⚠ `- botAttackTroopsSent`. 없으면 병렬 공격 셋이 각자 "남은 전부"를 계산한다.

    막지 않았으면: 병력 5만인 나라가 한 tick 에 12만을 보낸다."""
    st = state(n=4, kinds={1: "bot", 2: "bot", 3: "bot"})
    weak_bots(st, [1, 2, 3])
    rich(st, 0)
    before = st.players[0].troops
    b = bot(difficulty="hard")
    b._attack_bots(st, [st.players[i] for i in (1, 2, 3)])
    sent = sum(a.troops for a in st.attacks)
    assert len(st.attacks) == 3
    assert sent <= before, f"있는 병력({before:,.0f})보다 많이 보냈다({sent:,.0f})"


def test_bot_attacks_that_send_nothing_do_not_stop_the_ladder():
    """원본 주석대로 — 한 대도 못 보냈으면 사다리를 계속 내려가야 한다."""
    st = state(kinds={1: "bot"})
    st.players[0].troops = 0.0
    b = bot()
    assert b._attack_bots(st, [st.players[1]]) is False


# --- 예순여덟 · 낙진 -------------------------------------------------------

def test_plain_expansion_ignores_nuked_land():
    """막지 않았으면: 방어가 크게 붙은 낙진 땅으로 평소처럼 밀고 들어간다."""
    st = state(n=1)
    st.fallout.add([st.gmap.ref(x, 1) for x in range(120)])
    clean, nuked = st.neutral_borders(0)
    assert nuked is True
    assert clean is False, "낙진뿐인데 깨끗한 중립이 있다고 한다"


def test_plain_expansion_is_wired_to_the_fallout_check():
    """로직이 아니라 **배선**을 잰다.

    낙진뿐인 국경 + `reserve` 아래 병력 → 평소 확장 경로가 낙진을 구분하면
    아무 일도 안 일어나고, `None in reachable` 로 뭉뚱그리면 **관문보다 앞선
    자리에서** 공격이 나간다."""
    st = state(n=2)
    st.fallout.add([st.gmap.ref(x, 1) for x in range(10, 120)]
                   + [st.gmap.ref(x, 2) for x in range(120)])
    b = bot()
    b.rng = AlwaysGo()
    p = st.players[0]
    p.troops = p.max_troops(st.tiles(0)) * (b.reserve_ratio - 0.05)
    b._maybe_attack(st)
    assert not any(a.target is None for a in st.attacks), \
        "낙진 땅으로 평소처럼 확장했다"


def test_the_nuked_strategy_does_nothing_without_fallout():
    """막지 않았으면: `nuked` 가 그냥 "중립 확장 한 번 더"가 된다 —
    낙진이 없어도 사다리에서 매번 성공해 그 아래 전략이 영영 안 돈다."""
    st = state(n=2)
    rich(st, 0)
    assert bot()._s_nuked(st, [], []) is False


def test_the_nuked_strategy_takes_it_anyway():
    """낙진 땅은 사다리의 `nuked` 자리에서만 노린다."""
    st = state(n=1)
    rich(st, 0)
    st.fallout.add([st.gmap.ref(x, 1) for x in range(120)])
    b = bot()
    assert b._s_nuked(st, [], []) is True
    assert st.attacks and st.attacks[0].target is None


def test_nuked_is_placed_by_difficulty():
    """easy 는 맨 위, impossible 은 아래쪽이다 — 순서가 성격이다."""
    assert ATTACK_STRATEGIES["easy"].index("nuked") == 0
    assert (ATTACK_STRATEGIES["impossible"].index("nuked")
            > ATTACK_STRATEGIES["impossible"].index("retaliate"))


# --- 예순아홉 · 상한 --------------------------------------------------------

def test_bot_neighbours_do_not_eat_the_send_cap():
    """⚠ 판에 봇이 400개다. 봇을 위협으로 세면 hard 나라는 **아무도 못 친다.**"""
    st = state(n=2, kinds={1: "bot"})
    rich(st, 0)
    st.players[1].troops = 10_000_000.0
    b = bot(difficulty="hard")
    assert b._send_cap(st) == float("inf"), "봇 하나가 상한을 다 먹었다"


def test_allies_do_not_eat_the_send_cap():
    st = state(n=2)
    rich(st, 0)
    st.players[1].troops = 10_000_000.0
    st.diplomacy.form(0, 1, 0)
    b = bot(difficulty="hard")
    assert b._send_cap(st) == float("inf"), "동맹을 위협으로 셌다"


def test_a_nation_under_attack_may_spend_the_incoming_troops():
    """원본 주석: *"Nations under attack may retaliate freely."*

    막지 않았으면: 센 이웃에게 얻어맞는 나라가 상한 때문에 반격을 못 한다."""
    st = state()
    rich(st, 0)
    st.players[1].troops = 10_000_000.0
    b = bot(difficulty="hard")
    capped = b._send_cap(st)
    st.players[1].attack_ratio = 1.0
    st.launch_attack(1, 0)
    assert st.attacks, "재료: 공격이 안 걸렸다"
    # ⚠ **친 쪽의 병력을 되돌려 놓는다.** 안 그러면 공격에 쓴 만큼 이웃이 약해져
    # 상한이 저절로 올라간다 — 반격 하한을 지워도 값이 커져 변이가 살아남았다.
    st.players[1].troops = 10_000_000.0
    incoming = sum(a.troops for a in st.attacks)
    assert b._send_cap(st) == pytest.approx(incoming), "반격 하한이 없다"
    assert incoming > capped, "재료: 하한이 원래 상한보다 커야 잰다"


def test_too_weak_attacks_are_allowed_while_under_attack():
    st = state()
    rich(st, 0)
    st.players[1].troops = 10_000_000.0
    b = bot(difficulty="hard")
    assert b._attack_troops(st, 1) is None, "재료: 평소엔 걸러져야 한다"
    st.players[1].attack_ratio = 1.0
    st.launch_attack(1, 0)
    st.players[1].troops = 10_000_000.0        # 위와 같은 이유로 되돌린다
    assert b._attack_troops(st, 1) is not None, "맞고 있는데도 반격을 걸렀다"


# --- 일흔 · FFA 문턱 --------------------------------------------------------

def test_weakest_is_skipped_when_they_are_stronger_than_us():
    """⚠ 이게 없어서 우리 나라들은 자기보다 센 이웃에게 계속 들이받았다."""
    st = state()
    rich(st, 0)
    st.players[1].troops = st.players[0].troops * 2
    b = bot()
    assert b._s_weakest(st, [], [st.players[1]]) is False
    st.players[1].troops = st.players[0].troops * 0.5
    assert b._s_weakest(st, [], [st.players[1]]) is True


def test_a_traitor_who_is_much_stronger_is_left_alone():
    st = state()
    rich(st, 0)
    st.diplomacy.form(0, 1, 0)
    st.break_alliance(1, 0)                      # 1 이 배신자가 된다
    assert st.is_traitor(1)
    st.players[1].troops = st.players[0].troops * 2
    b = bot()
    assert b._s_traitor(st, [], [st.players[1]]) is False
    st.players[1].troops = st.players[0].troops * 0.9
    assert b._s_traitor(st, [], [st.players[1]]) is True


def test_victims_are_those_taking_heavy_incoming_fire():
    """들어오는 공격이 그 나라 병력의 50% 를 넘으면 올라탄다."""
    st = state(n=3)
    rich(st, 0)
    b = bot(difficulty="hard")
    assert b._s_victim(st, [], [st.players[1]]) is False, "재료: 아직 안 맞고 있다"
    st.players[2].troops = 200_000.0
    st.players[2].attack_ratio = 1.0
    st.launch_attack(2, 1)
    assert b._s_victim(st, [], [st.players[1]]) is True


def test_very_weak_means_under_fifteen_percent_of_their_cap():
    """원본 주석이 대놓고 말한다 — MIRV 맞은 나라를 주우라는 것이다."""
    st = state()
    rich(st, 0)
    foe = st.players[1]
    foe.troops = foe.max_troops(st.tiles(1)) * 0.20
    b = bot(difficulty="impossible")
    assert b._s_very_weak(st, [], [foe]) is False
    foe.troops = foe.max_troops(st.tiles(1)) * 0.10
    assert b._s_very_weak(st, [], [foe]) is True


def test_hated_looks_past_the_border():
    """`allRelationsSorted` 는 관계표 전체를 본다 — 국경 이웃만이 아니다."""
    st = state(n=3, far=(2,), water=True)
    rich(st, 0)
    st.players[0].relations.update(2, -100.0)     # 바다 건너 2 를 미워한다
    b = bot()
    assert b._s_hated(st, [], []) is True
    assert any(a.target == 2 for a in st.attacks) or st.boats


def test_only_hostile_relations_count_as_hated():
    """⚠ 문턱은 −50 이다(`relationFromValue`). 거기 못 미치면 `hated` 가 아니다.

    막지 않았으면: 관계표에 이름이 있는 **아무나** 친다 — 조금 서먹한 이웃이
    사다리 위쪽에서 표적이 되어 사실상 `weakest` 가 사라진다."""
    st = state(n=3)
    rich(st, 0)
    st.players[0].relations.update(1, -40.0)      # 불신이지 적대가 아니다
    st.players[0].relations.update(2, -10.0)
    b = bot()
    assert b._s_hated(st, [], []) is False
    st.players[0].relations.update(1, -30.0)      # 합 −70 — 이제 적대다
    assert b._s_hated(st, [], []) is True


def test_a_hated_giant_is_left_alone_in_ffa():
    st = state(n=3, far=(2,), water=True)
    rich(st, 0)
    st.players[0].relations.update(2, -100.0)
    st.players[2].troops = st.players[0].troops * 4
    b = bot()
    assert b._s_hated(st, [], []) is False


# --- 자리를 비워 둔 둘 ------------------------------------------------------

def test_afk_and_donate_are_no_ops_but_keep_their_slots():
    """우리 판에 없는 개념이라 항상 False 다. **지우면 순서가 밀린다.**"""
    st = state()
    b = bot()
    assert b._s_afk(st, [], [st.players[1]]) is False
    assert b._s_donate(st, [], [st.players[1]]) is False
    assert "afk" in ATTACK_STRATEGIES["medium"]
    assert "donate" in ATTACK_STRATEGIES["hard"]


# --- 반격 -------------------------------------------------------------------

def test_retaliation_picks_the_biggest_incoming_attack():
    st = state(n=3)
    rich(st, 0)
    for pid, troops in ((1, 20_000.0), (2, 90_000.0)):
        st.players[pid].troops = troops
        st.players[pid].attack_ratio = 1.0
        st.launch_attack(pid, 0)
    b = bot()
    assert b._biggest_incoming_attacker(st) == 2, "작은 쪽을 되받았다"


def test_bot_attacks_are_ignored_when_deciding_whom_to_retaliate_against():
    """봇에게 되받아 봐야 판이 안 바뀐다 — 내가 봇이 아니면 봇 공격은 무시한다."""
    st = state(n=3, kinds={2: "bot"})
    rich(st, 0)
    for pid, troops in ((1, 20_000.0), (2, 90_000.0)):
        st.players[pid].troops = troops
        st.players[pid].attack_ratio = 1.0
        st.launch_attack(pid, 0)
    assert bot()._biggest_incoming_attacker(st) == 1


def test_retaliation_ignores_the_difficulty_mercy():
    """`force=True` — 맞고 있는데 난이도 때문에 못 받아치면 샌드백이 된다."""
    st = state(kinds={1: "human"})
    rich(st, 0)
    st.players[1].troops = 30_000.0
    st.players[1].attack_ratio = 1.0
    st.launch_attack(1, 0)
    b = bot(difficulty="easy")
    b.rng = random.Random(0)
    assert b._s_retaliate(st, [], [st.players[1]]) is True
