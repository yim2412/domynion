"""AI 의 상륙 표적 고르기 — 이식 누락 일흔셋~일흔여섯 (§5.77).

`attackWithRandomBoat` + `findRandomBoatTarget`. §5.76 에서 같은 파일의 공격
사다리를 옮기면서 **상륙 쪽 300줄이 아직 남아 있었다.**

| # | 원본 | 우리 |
|---|---|---|
| **일흔셋** | **국경을 맞댄 적에게는 배를 안 보낸다**(*"that usually looks stupid"*) | 보냈다 |
| **일흔넷** | FFA 에서 **나보다 센 상대**에게는 안 보낸다 | 보냈다 |
| **일흔다섯** | 상륙 병력에도 `troopSendCap` · "20% 미만이면 안 친다" | 상륙만 무제한이었다 |
| **일흔여섯** | 사방 **150칸 상자를 500번** 찍는다 | 반경 4~80 을 **20번** |

일흔다섯이 특히 앞뒤가 안 맞던 자리다 — **육상으로는 못 치는 상대에게 배로는
계속 들이받았다.**
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import (BOAT_TARGET_RANGE, BOAT_TARGET_TRIES, NationBot)
from domynion.core import constants as C
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.events import EventKind
from domynion.core.gamemap import GameMap
from domynion.core.naval import TransportShip
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState


def state(kinds=None) -> GameState:
    """P0 은 **육지 이웃 하나(P3)와 바다 건너 둘(P1·P2)** 을 갖는다.

    ⚠ 재료가 세 가지를 동시에 만족해야 이 파일이 재려는 것을 잰다:
    (a) `troopSendCap` 이 유한하려면 P0 에게 **육지 이웃**이 있어야 하고,
    (b) 상륙 표적이 있으려면 **바다로 닿는 상대**가 있어야 하며,
    (c) 그 둘이 **다른 나라**여야 한다.

    0행: P0(x<50) · P3(x>=50) — 육지로 맞닿는다
    1행: 바다(x<50) · **P0 의 땅**(x>=50) — P3 을 감싸 물에서 떼어 놓는다
    2행: 바다
    3행: P1(x<30) · P2(x>=30) — 바다 건너 섬 둘
    4행부터: 바다

    ⚠ **빈 땅을 한 칸도 남기지 않는다.** 남기면 AI 가 그쪽을 먼저 고르는데
    (high-interest), 중립은 상한도 20% 규칙도 안 받으므로 그 둘을 재는 테스트가
    통째로 헛돈다 — 실제로 그렇게 세 번 통과했다.

    ⚠ **지도가 높아야 한다.** 표적 탐색은 사방 150칸 상자를 500번 찍는데, 4줄짜리
    지도에서는 뽑은 좌표의 1%만 지도 안에 들어와 500번을 다 써도 후보를 못 찾는다.
    작은 재료가 규칙을 가리는 또 한 가지 방식이다."""
    rows = ["." * 60,
            "~" * 50 + "." * 10,
            "~" * 60,
            "." * 60] + ["~" * 60] * 56
    gm = GameMap.from_rows(rows)
    layout = {
        0: [(0, x) for x in range(50)] + [(1, x) for x in range(50, 60)],
        3: [(0, x) for x in range(50, 60)],
        1: [(3, x) for x in range(30)],
        2: [(3, x) for x in range(30, 60)],
    }
    ps = {}
    for pid, cells in layout.items():
        kind = (kinds or {}).get(pid, "nation")
        tiles = [gm.ref(x, y) for y, x in cells]
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind=kind, start=tiles[0])
        for t in tiles:
            gm.owner[t] = pid
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: len(cells) for pid, cells in layout.items()}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    for p in ps.values():
        p.troops = 100_000.0
    ps[3].troops = 1_000.0            # 기본은 약하게 — 상한을 안 건드린다
    return st


def bot(pid: int = 0, difficulty: str = "medium", seed: int = 3) -> NationBot:
    return NationBot(pid=pid, rng=random.Random(seed), difficulty=difficulty)


def targets(st: GameState, b: NationBot, high_interest: bool = False,
            enemies=(), tries: int = 40) -> set:
    """여러 번 뽑아 **어느 나라들이 후보로 나오는지** 모은다."""
    out = set()
    for seed in range(tries):
        b.rng = random.Random(seed)
        t = b._boat_target(st, st.gmap.ref(0, 0), high_interest, list(enemies))
        if t is not None:
            out.add(int(st.gmap.owner[t]))
    return out


# --- 일흔셋 · 국경 이웃에게는 안 보낸다 --------------------------------------

def test_bordering_enemies_are_not_boat_targets():
    """원본 주석 그대로 — *"that usually looks stupid."*

    막지 않았으면: 걸어서 갈 수 있는 상대에게 배를 돌린다. 상륙은 3척 제한이
    있으므로 그만큼 진짜 필요한 자리에 못 쓴다."""
    st = state()
    b = bot()
    assert 1 in targets(st, b), "재료: 평소엔 P1 이 후보다"
    assert 1 not in targets(st, b, enemies=[st.players[1]]), "국경 이웃에게 배를 보냈다"


def test_other_players_stay_reachable_when_one_is_excluded():
    st = state()
    b = bot()
    assert 2 in targets(st, b, enemies=[st.players[1]]), "엉뚱한 상대까지 막았다"


# --- 일흔넷 · FFA 에서 강자는 피한다 -----------------------------------------

def test_stronger_players_are_skipped_in_ffa():
    """막지 않았으면: 병력 1/5 를 실은 배가 나보다 센 상대에게 계속 녹는다."""
    st = state()
    b = bot()
    assert 1 in targets(st, b), "재료: 평소엔 후보다"
    st.players[1].troops = st.players[0].troops * 2
    assert 1 not in targets(st, b), "나보다 센 상대에게 배를 보냈다"


def test_equal_strength_is_still_a_target():
    """문턱은 **초과**다 — 같은 병력이면 간다."""
    st = state()
    st.players[1].troops = st.players[0].troops
    assert 1 in targets(st, bot())


# --- 일흔다섯 · 상륙에도 두 제동 ---------------------------------------------

def sends(difficulty: str, land_neighbour: float, islanders: float,
          seeds: int = 20) -> int:
    """같은 재료로 여러 seed 를 돌려 **몇 번 배가 나갔는지** 센다.

    ⚠ 한 seed 로 `not st.boats` 를 재면 안 된다. 표적 탐색이 무작위 표본이라
    **표적을 못 찾아서** 안 나간 것과 **규칙이 막아서** 안 나간 것이 구분되지
    않는다. 난이도만 바꿔 같은 seed 묶음을 돌려 대조한다."""
    n = 0
    for seed in range(seeds):
        st = state()
        st.players[3].troops = land_neighbour
        st.players[1].troops = islanders
        st.players[2].troops = islanders
        b = bot(difficulty=difficulty, seed=seed)
        b._boat(st, [])
        n += bool(st.boats)
    return n


def test_the_send_cap_also_limits_boats():
    """⚠ 육상으로는 못 치는 상대에게 배로는 계속 들이받고 있었다.

    막지 않았으면: hard 나라가 상한을 상륙으로 우회한다. **강한 쪽은 육지
    이웃(P3)** 이다 — 상한은 국경 이웃으로 정해지므로 바다 건너 상대는
    아무리 세도 상한을 안 만든다."""
    st = state()
    st.players[3].troops = 10_000_000.0
    assert bot(difficulty="hard")._send_cap(st) < C.ATTACK_MIN_TROOPS, \
        "재료: 상한이 0 이어야 한다"
    assert sends("medium", 10_000_000.0, 1_000.0) > 0, "재료: 표적은 있다"
    assert sends("hard", 10_000_000.0, 1_000.0) == 0, "상한을 무시하고 배가 나갔다"


def test_boats_still_go_when_the_cap_allows():
    assert sends("hard", 1_000.0, 1_000.0) > 0, "상한이 넉넉한데 안 나갔다"


def test_a_boat_carries_the_capped_amount():
    """상한이 걸리면 **줄여서** 보낸다 — 원본이 `min(troops/5, cap)` 이다."""
    seen = 0
    for seed in range(20):
        st = state()
        st.players[3].troops = 110_000.0       # 상한이 1/5 보다 조금 작아진다
        st.players[1].troops = 1_000.0
        st.players[2].troops = 1_000.0
        b = bot(difficulty="hard", seed=seed)
        cap = b._send_cap(st)
        assert 0 < cap < st.players[0].troops * C.BOAT_ATTACK_RATIO, "재료"
        b._boat(st, [])
        if st.boats:
            seen += 1
            assert st.boats[0].troops == pytest.approx(cap), \
                "상한만큼 줄여 보내지 않았다"
    assert seen > 0, "재료: 한 번도 안 나가면 아무것도 안 잰 것이다"


def test_too_weak_boat_attacks_are_refused_on_hard():
    """육상과 같은 20% 규칙이다.

    ⚠ **FFA 에서 이 규칙은 상한이 걸렸을 때만 관찰된다.** 보내는 양이 내 병력의
    1/5 이고 문턱이 상대 병력의 1/5 이라, 상한이 없으면 "상대가 나보다 세다"와
    같은 말이 된다 — 그 조건은 일흔넷(FFA 강자 제외)이 이미 걸러 낸다.
    그래서 상한으로 병력을 줄여 놓고 잰다.

    상한 = 100,000 − 128,000×0.75 = 4,000 · 문턱 = 90,000×0.2 = 18,000."""
    assert sends("medium", 128_000.0, 90_000.0) > 0, "재료: 표적은 있다"
    assert sends("hard", 128_000.0, 90_000.0) == 0, \
        "상한에 눌려 20% 도 안 되는 배가 나갔다"


def test_easy_and_medium_have_no_such_limit():
    """두 제동은 hard 이상만이다 — 위 두 테스트의 medium 쪽이 그것을 보인다."""
    assert sends("easy", 10_000_000.0, 1_000.0) > 0
    assert sends("medium", 128_000.0, 90_000.0) > 0


# --- 일흔여섯 · 탐색 범위 ---------------------------------------------------

def test_the_search_box_matches_the_original():
    """⚠ 우리는 반경 4~80 을 20번 찍고 있었다. 원본 크기 지도(§5.47)에서 그
    범위는 이웃 하나를 겨우 덮는다 — 섬 건너편은 영영 안 뽑힌다."""
    assert BOAT_TARGET_RANGE == 150
    assert BOAT_TARGET_TRIES == 500


def test_unreachable_players_are_skipped():
    """물길이 없으면 후보가 아니다(`canBuildTransportShip`).

    P3 은 육지로 붙어 있고 바다에 닿지 않는다 — 배로는 갈 수 없다."""
    st = state()
    assert 3 not in targets(st, bot()), "물길 없는 상대가 후보로 나왔다"


# --- high-interest ----------------------------------------------------------

def test_unowned_and_bot_land_is_looked_at_first():
    """원본은 **빈 땅·봇 땅을 먼저** 찾고, 없을 때만 사람 땅을 본다
    (원본 주석: *"Mainly relevant for earlygame"*).

    막지 않았으면: 초반에 빈 섬을 두고 남의 나라에 배를 들이받는다."""
    st = state(kinds={2: "bot"})
    b = bot()
    only = targets(st, b, high_interest=True, tries=40)
    assert only, "재료: 봇 섬이 후보로 나와야 한다"
    assert only == {2}, f"봇·빈 땅만 나와야 하는데 {only}"
    assert 1 in targets(st, b, high_interest=False, tries=40), \
        "재료: 사람 땅은 두 번째 단계에서 나온다"


def test_high_interest_is_tried_first():
    """`_boat` 는 두 단계를 **순서대로** 돈다 — 빈 땅·봇 땅 먼저, 그다음 사람.

    ⚠ 여기서는 **호출 순서를 직접 잰다.** 실전 표본으로 재려 했더니 이 작은
    지도에서는 ±150 상자 500번이 작은 섬을 거의 못 찍어(약 5%) 치우침이
    안 보였다 — 재료로는 못 재는 자리다."""
    st = state(kinds={2: "bot"})
    b = bot()
    order = []
    real = b._boat_target

    def spy(st_, src, high_interest, enemies=()):
        order.append(high_interest)
        return real(st_, src, high_interest, enemies)

    b._boat_target = spy
    b._boat(st, [])
    assert order and order[0] is True, f"1단계를 건너뛰었다: {order}"


# --- 배선 -------------------------------------------------------------------

def test_the_boat_cap_is_checked_before_searching():
    """배가 이미 다 나가 있으면 **표적을 찾지도 않는다**(원본이 첫 줄에서 본다).

    ⚠ 엔진의 `send_boat` 에도 같은 상한이 있어 배 수는 어차피 안 늘어난다.
    관찰되는 차이는 **소식**이다 — 엔진 쪽 검사에 걸리면 "배가 다 나가 있다"
    (`ATTACK_FAILED`)가 사람에게 뜬다(§5.67). AI 의 결정 때문에 사람 화면에
    경보가 뜨면 안 된다."""
    st = state()
    # ⚠ 배를 **미리 채워 둔다.** `_boat` 를 여러 번 부르는 것으로는 못 잰다 —
    # 이 작은 지도에서는 표적 탐색이 자주 빈손이라 상한에 닿지도 못한다.
    for _ in range(C.BOAT_MAX_NUMBER):
        st.boats.append(TransportShip(owner=0, target=None, troops=1.0,
                                      path=[st.gmap.ref(0, 2)],
                                      dst=st.gmap.ref(0, 2)))
    b = bot()
    # 표적 탐색은 무작위라 빈손일 때가 많다 — 그러면 엔진까지 가지도 않아
    # 아무것도 안 재게 된다. 표적을 고정해 **상한 검사만** 남긴다.
    b._boat_target = lambda st_, src, hi, en=(): st_.gmap.ref(5, 3)
    b._boat(st, [])
    assert len(st.boats) == C.BOAT_MAX_NUMBER
    assert not [e for e in st.log.items if e.kind is EventKind.ATTACK_FAILED], \
        "AI 가 엔진 쪽 상한에 걸려 소식을 냈다"
