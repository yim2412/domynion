"""정복 골드 이전 — `GameImpl.conquerPlayer` 의 골드 부분.

**우리에게 통째로 없던 규칙이다.** 건물은 넘기고 있었는데 골드는 아무 데도 안 갔다.
472명이 도는 판은 수백 명이 탈락하므로, 그들이 모은 골드가 판에서 조용히 증발하고
있었다는 뜻이다.

원본 규칙 셋:

1. 정복자는 패자의 골드를 받는다 — 봇·나라는 **전액**, 사람은 **절반**
   (`conquerGoldAmount`).
2. 패자에게서는 **언제나 전액**이 빠진다(`removeGold(gold)`). 그래서 사람을 정복하면
   나머지 절반은 어디로도 가지 않고 **사라진다.**
3. **한 번도 공격을 보낸 적 없는 사람**은 이전 자체를 건너뛴다.

⚠ 이 파일의 테스트는 일부러 깨뜨려서 실패하는지 확인했다(2026-08-24).
변이 목록은 파일 끝 주석에 있다.
"""

from __future__ import annotations

import random

import pytest

from domynion.core import constants as C
from domynion.core.engine import GameState
from domynion.core.events import EventKind


def make_state(players: int = 2) -> GameState:
    """`_maybe_absorb` 만 부르면 되므로 작은 지도로 충분하다."""
    from domynion.core.buildings import DefensePostIndex
    from domynion.core.gamemap import GameMap
    from domynion.core.nukes import Fallout
    from domynion.core.state import PlayerState

    gmap = GameMap.from_rows(["." * 40 + "~" * 10 for _ in range(40)])
    st = GameState.__new__(GameState)
    st.__init__(gmap=gmap, players={}, rng=random.Random(0))
    for pid in range(players):
        st.players[pid] = PlayerState(pid=pid, name=f"p{pid}", kind="nation",
                                      start=0)
    st._posts = DefensePostIndex(gmap.size)
    st.fallout = Fallout(gmap.size)
    st._counts = {}
    return st


def setup_conquest(loser_kind: str, loser_gold: int, winner_gold: int = 0,
                   loser_attacks: int = 1, loser_tiles: int = 5) -> GameState:
    """0번이 1번을 흡수하기 직전 상태. `loser_tiles` 는 흡수 문턱 아래여야 한다."""
    assert loser_tiles < C.CONQUER_PLAYER_TILES
    st = make_state()
    st.players[1].kind = loser_kind
    st.players[1].is_bot = loser_kind == "bot"
    st.players[1].gold = loser_gold
    st.players[1].attacks_sent = loser_attacks
    st.players[0].gold = winner_gold
    # 패자에게 흡수 문턱 아래의 영토를 준다
    tiles = [t for t in range(st.gmap.size) if st.gmap.passable(t)][:loser_tiles]
    for t in tiles:
        st.gmap.owner[t] = 1
    st._counts[1] = len(tiles)
    st._counts[0] = 1000
    return st


# --- 이전량 -----------------------------------------------------------------

@pytest.mark.parametrize("kind,gold,want_taken", [
    ("nation", 1_000_000, 1_000_000),   # 나라는 전액
    ("bot", 700_000, 700_000),          # 봇도 전액
    ("human", 1_000_000, 500_000),      # 사람만 절반
    ("human", 999_999, 499_999),        # 내림 (BigInt 나눗셈)
])
def test_conqueror_takes_the_right_share(kind, gold, want_taken):
    st = setup_conquest(kind, gold)
    st._maybe_absorb(0, 1)
    assert st.players[0].gold == want_taken
    assert not st.players[1].alive


def test_the_loser_always_loses_everything():
    """**정복자가 받는 양과 패자가 잃는 양이 다르다.**

    사람을 정복하면 절반만 넘어오고 나머지 절반은 **사라진다.** 패자에게 남겨
    두는 것으로 옮기면(`loser.gold -= taken`) 죽은 사람 지갑에 50만이 남는데,
    그 골드는 다시 셀 수 있는 자리가 없어 조용한 불일치가 된다."""
    st = setup_conquest("human", 1_000_000)
    st._maybe_absorb(0, 1)
    assert st.players[0].gold == 500_000
    assert st.players[1].gold == 0, "패자에게 골드가 남았다"


def test_gold_is_added_not_replaced():
    """이미 가진 골드에 **더한다.** 대입으로 옮기면 정복자가 오히려 가난해진다."""
    st = setup_conquest("nation", 300_000, winner_gold=800_000)
    st._maybe_absorb(0, 1)
    assert st.players[0].gold == 1_100_000


# --- 공격한 적 없는 사람 예외 ------------------------------------------------

def test_a_human_who_never_attacked_is_not_robbed():
    """**한 번도 안 친 사람은 털리지 않는다.**

    시작 골드를 켠 판에서 가만히 있는 사람을 털어 가는 것을 막는 장치다."""
    st = setup_conquest("human", 1_000_000, loser_attacks=0)
    st._maybe_absorb(0, 1)
    assert st.players[0].gold == 0
    assert st.players[1].gold == 1_000_000, "이전을 건너뛰지 않았다"
    assert not st.players[1].alive, "탈락 자체는 일어나야 한다"


@pytest.mark.parametrize("kind", ["nation", "bot"])
def test_the_exception_does_not_cover_bots_or_nations(kind):
    """**대조군.** 예외는 사람에게만 걸린다.

    이게 없으면 `loser.attacks_sent == 0` 만 보는 구현도 위 테스트를 통과한다 —
    그러면 판의 거의 모든 봇이(대부분 공격을 못 해 보고 죽는다) 골드를 안 뺏긴다."""
    st = setup_conquest(kind, 400_000, loser_attacks=0)
    st._maybe_absorb(0, 1)
    assert st.players[0].gold == 400_000


# --- `attacks_sent` 배선 -----------------------------------------------------

def test_sending_an_attack_counts():
    """`launch_attack()` 이 실제로 세는가. 안 세면 위 예외가 **모든 사람**에게 걸린다."""
    from domynion.core.state import PlayerState

    st = make_state()
    st.players[0].kind = "human"
    st.players[0].is_bot = False
    tiles = [t for t in range(st.gmap.size) if st.gmap.passable(t)]
    for t in tiles[:200]:
        st.gmap.owner[t] = 0
    st._counts[0] = 200
    st.players[0].troops = 100_000.0
    assert st.players[0].attacks_sent == 0
    assert st.launch_attack(0, None) is not None, "중립 공격이 안 나갔다 — 검사가 무의미하다"
    assert st.players[0].attacks_sent == 1


def test_a_failed_attack_does_not_count():
    """붙을 곳이 없어 공격이 안 나가면 세지 않는다."""
    st = make_state()
    st.players[0].troops = 100_000.0
    st._counts[0] = 0                      # 영토가 없다 → `Attack.launch` 가 None
    assert st.launch_attack(0, None) is None
    assert st.players[0].attacks_sent == 0


# --- 이벤트 -----------------------------------------------------------------

def test_a_separate_event_kind_is_used():
    """`CONQUERED_PLAYER` 를 재사용하면 안 된다.

    그쪽 `amount` 는 **정복당한 사람의 pid** 라서(소식창이 그렇게 읽는다) 골드를
    넣으면 엉뚱한 이름이 찍힌다. 처음에 그렇게 짰다가 화면이 깨질 뻔했다."""
    st = setup_conquest("nation", 250_000)
    st._maybe_absorb(0, 1)
    kinds = [e.kind for e in st.log.items]
    assert EventKind.GOLD_FROM_CONQUEST in kinds
    got = next(e for e in st.log.items if e.kind is EventKind.GOLD_FROM_CONQUEST)
    assert got.amount == 250_000
    assert got.who == 0 and got.other == 1


def test_no_event_when_nothing_was_taken():
    """0 골드를 노획했다는 줄이 소식창에 뜨면 안 된다."""
    st = setup_conquest("nation", 0)
    st._maybe_absorb(0, 1)
    assert not any(e.kind is EventKind.GOLD_FROM_CONQUEST for e in st.log.items)


def test_the_event_renders_without_crashing():
    """소식창 한 줄이 실제로 만들어지는가. 렌더러를 안 붙이면 여기서 걸린다."""
    from domynion.ui.eventlog import describe

    st = setup_conquest("nation", 250_000)
    st._maybe_absorb(0, 1)
    got = next(e for e in st.log.items if e.kind is EventKind.GOLD_FROM_CONQUEST)
    line = describe(st, got, me=0)
    assert "250,000" in line and "p1" in line, line


# ---------------------------------------------------------------------------
# 확인한 변이 (2026-08-24) — 전부 잡혔다
#
# 1. `loser.gold // 2 if human` → 언제나 전액
#      → test_conqueror_takes_the_right_share[human-*]
# 2. `winner.gold += taken` → `winner.gold = taken`
#      → test_gold_is_added_not_replaced
# 3. `loser.gold = 0` → `loser.gold -= taken`
#      → test_the_loser_always_loses_everything
# 4. 예외의 `loser.kind == "human"` 조건 제거
#      → test_the_exception_does_not_cover_bots_or_nations
# 5. 예외 통째로 제거
#      → test_a_human_who_never_attacked_is_not_robbed
# 6. `p.attacks_sent += 1` 제거
#      → test_sending_an_attack_counts
# 7. `attacks_sent += 1` 을 `Attack.launch` 실패 검사보다 앞으로 이동
#      → test_a_failed_attack_does_not_count
# 8. `_transfer_conquest_gold` 호출 자체를 제거
#      → 위 대부분
# ---------------------------------------------------------------------------
