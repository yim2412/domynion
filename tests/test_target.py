"""표적 지정 — 동맹에게 "저놈을 쳐 달라"고 부탁하는 기능.

이식 누락이었다. 상수(`REL_TARGETED`)만 있고 기능이 없었다.

**이게 동맹의 실질적 효용이다.** 없으면 동맹은 "서로 안 친다"는 소극적 약속일
뿐이고, 함께 싸우는 수단이 하나도 없다.

출처: `TargetPlayerExecution.ts` · `PlayerImpl.canTarget/target/targets` ·
`AiAttackBehavior.assistAllies`
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import NationBot
from domynion.core import constants as C
from domynion.core import emoji
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.events import EventKind
from domynion.core.gamemap import GameMap
from domynion.core.relations import Relation
from domynion.core.state import PlayerState
from domynion.ui.status import markers, player_status


def state(n: int = 3, kinds: dict[int, str] | None = None) -> GameState:
    gm = GameMap.from_rows(["." * 60] * 6)
    kinds = kinds or {}
    players = {}
    for pid in range(n):
        for x in range(pid * 6, pid * 6 + 6):
            for y in range(0, 3):
                gm.owner[gm.ref(x, y)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 6, 0))
        p.kind = kinds.get(pid, "nation")
        p.troops = 300_000.0
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 18 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def ally(st: GameState, a: int, b: int) -> None:
    """⚠ `diplomacy.form()` 을 직접 부르면 **관계가 안 올라간다.**

    관계 +100 은 `accept_alliance` 에 붙어 있어서, form 만 쓰면 "동맹인데 사이는
    중립"이라는 실제로는 안 나오는 상태가 된다 — 지원 테스트가 통째로 빗나간다."""
    st.request_alliance(a, b)
    assert st.accept_alliance(b, a)


def chats(st: GameState) -> list[str]:
    return [e.text for e in st.log.items if e.kind is EventKind.CHAT]


# --- 찍기 -------------------------------------------------------------------

def test_targeting_marks_them_and_costs_relation():
    st = state()
    assert st.target_player(0, 1)
    assert st.targets_of(0) == [1]
    assert st.players[1].relations.value(0) == pytest.approx(C.REL_TARGETED)


def test_you_cannot_target_a_friend():
    """동맹을 찍으면 동맹에게 동맹을 치라고 하는 셈이 된다."""
    st = state()
    st.diplomacy.form(0, 1, tick=0)
    assert st.can_target(0, 1) is False
    assert st.target_player(0, 1) is False


def test_you_cannot_target_yourself():
    st = state()
    assert st.can_target(0, 0) is False


def test_one_target_every_fifteen_seconds():
    """막지 않았으면: 모두를 한꺼번에 찍어 동맹을 전방위로 부려먹는다."""
    st = state(4)
    assert st.target_player(0, 1)
    assert st.target_player(0, 2) is False, "쿨다운이 돌아야 한다"
    st.tick_count += C.TARGET_COOLDOWN_TICKS
    assert st.target_player(0, 2)


def test_cooldown_matches_the_original():
    assert C.TARGET_COOLDOWN_TICKS * C.TICK_DT == 15.0
    assert C.TARGET_DURATION_TICKS * C.TICK_DT == 10.0


# --- 만료 -------------------------------------------------------------------

def test_a_request_is_forgotten_after_ten_seconds():
    """막지 않았으면: 옛 부탁이 판 내내 남아 AI 가 엉뚱한 상대를 계속 친다."""
    st = state()
    st.target_player(0, 1)
    st.tick_count += C.TARGET_DURATION_TICKS
    assert st.targets_of(0) == []


def test_the_record_outlives_the_request_so_the_cooldown_still_works():
    """지속(10초)보다 쿨다운(15초)이 길다 — 만료됐다고 기록을 지우면
    12초째에 또 찍을 수 있게 된다."""
    st = state(4)
    st.target_player(0, 1)
    st.tick_count += C.TARGET_DURATION_TICKS + 20     # 12초
    st.tick()
    assert st.targets_of(0) == [], "부탁은 만료됐고"
    assert st.can_target(0, 2) is False, "쿨다운은 아직 남아 있어야 한다"


def test_a_dead_target_drops_out():
    st = state()
    st.target_player(0, 1)
    st.players[1].alive = False
    assert st.targets_of(0) == []


# --- 동맹이 답한다 ----------------------------------------------------------

def bot_for(st: GameState, pid: int) -> NationBot:
    b = NationBot(pid=pid, rng=random.Random(0), difficulty="hard")
    st.difficulty = "hard"          # hard 는 사람도 봐주지 않는다
    return b


def test_a_friendly_ally_attacks_the_target():
    """이 이식의 요점이다. 없으면 동맹으로 할 수 있는 일이 없다."""
    st = state()
    ally(st, 0, 1)                           # 동맹이 되면 관계 +100 = 우호
    st.target_player(0, 2)
    assert bot_for(st, 1)._assist_allies(st) is True
    assert any(a.attacker == 1 and a.target == 2 for a in st.attacks)


def test_helping_costs_the_helper_some_goodwill():
    """계속 부려먹으면 사이가 나빠져 결국 안 도와준다."""
    st = state()
    ally(st, 0, 1)
    before = st.players[1].relations.value(0)
    st.target_player(0, 2)
    bot_for(st, 1)._assist_allies(st)
    assert st.players[1].relations.value(0) == pytest.approx(
        before + C.REL_ASSIST_COST)


def test_a_lukewarm_ally_refuses_and_says_why():
    """거절에도 말이 있어야 사람이 동맹을 관리할 수 있다."""
    st = state(kinds={1: "nation", 0: "human"})
    ally(st, 0, 1)
    st.players[1].relations.update(0, -100)      # 동맹이지만 사이는 중립
    assert st.relation_of(1, 0) < Relation.FRIENDLY
    st.target_player(0, 2)
    assert bot_for(st, 1)._assist_allies(st) is False
    assert chats(st) and chats(st)[-1] in emoji.ASSIST_RELATION_TOO_LOW


def test_being_asked_to_attack_yourself_gets_a_sad_face():
    st = state(kinds={0: "human"})
    ally(st, 0, 1)
    # 동맹은 못 찍으므로 직접 기록한다 — 동맹을 맺기 **전에** 찍어 둔 부탁이
    # 아직 살아 있는 상황이다.
    st.targets[(0, 1)] = st.tick_count
    assert bot_for(st, 1)._assist_allies(st) is False
    assert chats(st) and chats(st)[-1] in emoji.ASSIST_TARGET_ME


def test_being_asked_to_attack_another_ally_is_refused():
    """동맹을 치라는 부탁은 안 듣는다 — 들으면 동맹망이 한 번에 무너진다."""
    st = state(4, kinds={0: "human"})
    ally(st, 0, 1)
    st.target_player(0, 2)                   # 찍고 나서
    ally(st, 1, 2)                           # P1 이 P2 와도 동맹을 맺는다
    assert bot_for(st, 1)._assist_allies(st) is False
    assert chats(st) and chats(st)[-1] in emoji.ASSIST_TARGET_ALLY


def test_nobody_helps_when_there_is_no_request():
    st = state()
    ally(st, 0, 1)
    assert bot_for(st, 1)._assist_allies(st) is False


def test_assist_sits_inside_the_difficulty_ladder():
    """⚠ **이 테스트는 우리 발명품을 재고 있었다**(§5.76).

    전에는 `_maybe_attack` 의 소스를 읽어 `_assist_allies` 가 중립 확장보다
    **앞에** 오는지 봤다. 그건 우리가 넣은 순서였다 — 원본은 `assistAllies` 를
    `getAttackStrategies` 의 한 항목으로 두고, 중립 확장은 그보다 **앞에**서
    따로 한다(`maybeAttack`).

    이제 재는 것은 *순서의 사실*이다: 사다리에 있고, 네 난이도 모두에서
    `betray` 보다 앞이다(동맹을 돕는 것이 동맹을 깨는 것보다 먼저다)."""
    from domynion.ai.nation import ATTACK_STRATEGIES

    for difficulty, order in ATTACK_STRATEGIES.items():
        assert "assist" in order, difficulty
        assert order.index("assist") < order.index("betray"), difficulty


# --- 화면에 보이나 (§5.69) ---------------------------------------------------

def test_my_allys_target_shows_on_my_screen_too():
    """⚠ **이식 누락 마흔아홉.** 규칙은 §5.27 에서 옮겼는데 **찍은 쪽만** 알았다.

    막지 않았으면: 동맹이 "저놈을 치자"고 찍어도 내 지도에는 아무 표시가 없다.
    부탁이 오간 것을 사람이 알 방법이 없으니 표적 지정이 절반만 도는 규칙이 된다."""
    st = state(4)
    ally(st, 0, 1)
    assert st.target_player(1, 2)
    assert st.transitive_targets_of(0) == [2], "동맹이 찍은 표적이 내게 안 보인다"
    assert markers(player_status(st, me=0)[2]) == "🎯"


def test_a_stranger_s_target_stays_invisible():
    """대조군 — 동맹이 아닌 나라가 찍은 것은 내 화면에 안 뜬다."""
    st = state(4)
    assert st.target_player(1, 2)
    assert st.transitive_targets_of(0) == []
    assert 2 not in player_status(st, me=0)


def test_the_same_target_is_not_listed_twice():
    """나와 동맹이 같은 상대를 찍었을 때. 원본도 `new Set` 으로 한 번만 센다."""
    st = state(4)
    ally(st, 0, 1)
    assert st.target_player(0, 2)
    assert st.target_player(1, 2)
    assert st.transitive_targets_of(0) == [2]


def test_a_targets_request_expires_for_the_ally_too():
    """10초가 지나면 동맹 화면에서도 사라진다 — `targets_of` 를 거치므로 자동이다."""
    st = state(4)
    ally(st, 0, 1)
    assert st.target_player(1, 2)
    st.tick_count += C.TARGET_DURATION_TICKS
    assert st.transitive_targets_of(0) == []
