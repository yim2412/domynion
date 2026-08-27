"""나라 AI 의 배신 — 원본 `NationAllianceBehavior.maybeBetray`.

⚠ **이식 누락 서른하나.** 배신의 *대가*는 정성껏 옮겨 놓고(배신자는 동맹 요청의
90% 를 거절당하고 `TRAITOR_DEFENSE_DEBUFF` · `TRAITOR_SPEED_DEBUFF` 가 걸린다)
나라 AI 가 배신하는 *행동* 자체가 없었다. 그래서 그 상태에 들어가는 나라가 한
명도 없었다 — 봇이 배신자를 칠 때와 사람이 직접 깰 때만 쓰이던 규칙이다.

난이도가 **문턱이 아니라 어느 이유를 보는가**로 들어가는 것이 이 파일의 성격이다.
"""

from __future__ import annotations

import random

from domynion.ai.nation import NationBot
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState


def state(w: int = 60, h: int = 30) -> GameState:
    gm = GameMap.from_rows(["." * w] * h)
    ps = {}
    for pid in (0, 1, 2):
        t = gm.ref(pid * 20 + 1, 1)
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation", start=t)
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 0 for pid in ps}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS * 2
    return st


def fill(st, pid, x0, y0, x1, y1):
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            st.gmap.owner[st.gmap.ref(x, y)] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n


def bot(pid: int = 0, difficulty: str = "hard") -> NationBot:
    return NationBot(pid, random.Random(0), difficulty=difficulty)


def allied(st, a: int, b: int) -> None:
    st.request_alliance(a, b)
    st.accept_alliance(b, a)
    assert st.diplomacy.allied(a, b)


def pair(difficulty="hard", my_troops=1000.0, their_troops=1000.0):
    """국경을 맞댄 동맹 둘. 이웃은 그 하나뿐이 **아니게** 셋째를 붙여 둔다 —
    안 그러면 "이웃이 하나뿐" 조건이 늘 함께 걸려 이유를 못 가른다."""
    st = state()
    fill(st, 0, 0, 0, 20, 20)
    fill(st, 1, 20, 0, 40, 20)
    fill(st, 2, 40, 0, 60, 20)
    st.players[0].troops = my_troops
    st.players[1].troops = their_troops
    st.players[2].troops = my_troops
    allied(st, 0, 1)
    return st, bot(0, difficulty)


# --- 이유 1: 거의 죽은 동맹 ---------------------------------------------------

def test_a_nearly_dead_ally_is_betrayed():
    """병력이 상한의 20% 미만이고 나보다 약하면 친다.

    원본 주석이 무엇을 노리는지 적어 뒀다 — *"For example MIRVed ones"*."""
    st, b = pair(difficulty="hard", my_troops=100_000.0)
    other = st.players[1]
    cap = other.max_troops(max(1, st.tiles(1)))
    other.troops = cap * (C.BETRAY_WEAK_TROOP_RATIO - 0.05)
    assert b._betrays(st, st.players[0], other, bordering=2)

    # 대조군 — 문턱 위면 참는다
    other.troops = cap * (C.BETRAY_WEAK_TROOP_RATIO + 0.05)
    assert not b._betrays(st, st.players[0], other, bordering=2)


def test_outgoing_attacks_count_toward_the_ally_strength():
    """**나가 있는 병력도 함께 센다.** 안 그러면 총공세를 나간 동맹이 매번
    "거의 죽었다"로 보여 등을 찔린다."""
    from domynion.core.attack import Attack
    st, b = pair(difficulty="hard", my_troops=100_000.0)
    other = st.players[1]
    cap = other.max_troops(max(1, st.tiles(1)))
    other.troops = cap * 0.05
    assert b._betrays(st, st.players[0], other, bordering=2), "재료가 조건을 안 만든다"

    st.attacks.append(Attack(attacker=1, target=2, troops=cap * 0.5))
    assert not b._betrays(st, st.players[0], other, bordering=2), \
        "나가 있는 병력을 안 셌다"


def test_easy_and_medium_do_not_use_the_weak_ally_rule():
    """easy·medium 은 이 이유를 아예 안 본다 — 대신 열 배 규칙만 본다."""
    for diff in ("easy", "medium"):
        st, b = pair(difficulty=diff, my_troops=100_000.0)
        other = st.players[1]
        cap = other.max_troops(max(1, st.tiles(1)))
        # 상한의 5% 지만, 열 배에는 못 미치게 둔다
        other.troops = max(cap * 0.05, 100_000.0 / 5)
        assert not b._betrays(st, st.players[0], other, bordering=2), diff


# --- 이유 2: 열 배 --------------------------------------------------------

def test_easy_side_betrays_when_ten_times_stronger():
    """easy·medium 은 **열 배**면 깬다. 원본이 이 조건이 엉성함을 인정한다."""
    st, b = pair(difficulty="medium", my_troops=10_000.0, their_troops=1_000.0)
    assert b._betrays(st, st.players[0], st.players[1], bordering=2)

    st.players[1].troops = 1_001.0            # 열 배에 아주 조금 못 미친다
    assert not b._betrays(st, st.players[0], st.players[1], bordering=2)


def test_easy_spares_humans():
    """easy 는 **사람은 안 친다**(§5.27 의 `shouldAttack` 과 같은 성격이다)."""
    st, b = pair(difficulty="easy", my_troops=10_000.0, their_troops=1.0)
    st.players[1].kind = "human"
    assert not b._betrays(st, st.players[0], st.players[1], bordering=2)

    # 대조군 — medium 은 사람도 친다
    st2, b2 = pair(difficulty="medium", my_troops=10_000.0, their_troops=1.0)
    st2.players[1].kind = "human"
    assert b2._betrays(st2, st2.players[0], st2.players[1], bordering=2)


# --- 이유 3: 배신자 -------------------------------------------------------

def test_a_traitor_ally_is_betrayed_back():
    """상대가 배신자면 깬다 — 단 **나보다 1.2배 넘게 강하면** 참는다."""
    st, b = pair(difficulty="hard", my_troops=1_000.0, their_troops=1_100.0)
    assert not b._betrays(st, st.players[0], st.players[1], bordering=2), \
        "배신자가 아닌데 이미 깬다 — 다른 이유가 걸린 재료다"

    st.diplomacy.traitor_since[1] = st.tick_count
    assert st.is_traitor(1)
    assert b._betrays(st, st.players[0], st.players[1], bordering=2)

    st.players[1].troops = 1_000.0 * C.BETRAY_TRAITOR_MARGIN + 1
    assert not b._betrays(st, st.players[0], st.players[1], bordering=2), \
        "나보다 훨씬 강한 배신자에게 덤볐다"


# --- 이유 4: 이웃이 하나뿐 --------------------------------------------------

def test_a_lone_neighbour_is_betrayed_when_much_weaker():
    """이웃이 그 하나뿐이면 세 배만 돼도 깬다 — 갈 곳이 거기밖에 없다."""
    # ⚠ 병력을 **상한 대비로** 잡는다. 절대값(300)으로 뒀더니 그게 상한의 20%
    # 아래라 이유 1이 함께 걸려, 무엇 때문에 깼는지 못 가르는 재료가 됐다.
    st, b = pair(difficulty="hard")
    other = st.players[1]
    cap = other.max_troops(max(1, st.tiles(1)))
    other.troops = cap * 0.5                  # 이유 1의 문턱(20%) 위
    st.players[0].troops = other.troops * 3.5
    assert not b._betrays(st, st.players[0], other, bordering=2), \
        "이웃이 둘인데 깬다 — 이 이유를 안 재는 재료다"
    assert b._betrays(st, st.players[0], other, bordering=1)

    st.players[0].troops = other.troops * 2.5  # 세 배에 못 미친다
    assert not b._betrays(st, st.players[0], other, bordering=1)


def test_easy_never_betrays_except_by_the_ten_times_rule():
    """easy 는 배신자도, 외톨이 이웃도 안 본다."""
    st, b = pair(difficulty="easy")
    other = st.players[1]
    cap = other.max_troops(max(1, st.tiles(1)))
    other.troops = cap * 0.5                  # 이유 1·2 는 안 걸리는 자리
    st.players[0].troops = other.troops * 3.5
    st.diplomacy.traitor_since[1] = st.tick_count
    assert not b._betrays(st, st.players[0], other, bordering=1)


# --- 실제로 깨지는가 --------------------------------------------------------

def test_betraying_actually_breaks_the_alliance_and_attacks():
    """판정만으로는 아무 일도 안 일어난다 — **깨고, 치고, 배신자가 된다.**

    막지 않았으면: 판정이 참이어도 동맹이 그대로 남아 공격이 튕긴다."""
    st, b = pair(difficulty="hard", my_troops=100_000.0)
    other = st.players[1]
    other.troops = other.max_troops(max(1, st.tiles(1))) * 0.05
    assert st.diplomacy.allied(0, 1)

    did = b._maybe_betray_and_attack(st, [other], [st.players[2]])
    assert did is True
    assert not st.diplomacy.allied(0, 1), "동맹이 그대로다"
    assert st.is_traitor(0), "배신자 낙인이 안 찍혔다"
    assert any(a.attacker == 0 and a.target == 1 for a in st.attacks), \
        "깨기만 하고 안 쳤다"


def test_no_betrayal_without_an_alliance():
    """동맹이 아니면 아무 일도 없다 — 여기서 걸러야 공격 사슬이 안 꼬인다."""
    st, b = pair(difficulty="hard", my_troops=100_000.0)
    st.break_alliance(0, 1)
    st.diplomacy.traitor_since.clear()
    other = st.players[1]
    other.troops = other.max_troops(max(1, st.tiles(1))) * 0.05
    assert b._maybe_betray_and_attack(st, [], [other]) is False
    assert not st.attacks
