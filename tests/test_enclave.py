"""둘러싸인 영토 흡수 — 원본 `PlayerExecution.removeClusters`.

⚠ **이식 누락 서른여덟.** 우리에겐 이 규칙이 통째로 없었다. 남의 영토 안에 갇힌
조각이 **영원히 남는다** — 갇힌 조각은 국경이 한 쪽뿐이라 공격 부대가 거의 안
가므로, 실제로는 지도에 점처럼 박힌 채 끝까지 살아 있게 된다.
"""

from __future__ import annotations

import random

from domynion.core import constants as C
from domynion.core import enclave
from domynion.core.buildings import DefensePostIndex
from domynion.core.constants import Terrain
from domynion.core.engine import GameState
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.state import PlayerState


def state(w: int = 40, h: int = 40, players: int = 3) -> GameState:
    """⚠ **가장자리를 바다로 두지 않는다.** 지도 끝은 그 자체로 "나갈 길"이라
    (`isOnEdgeOfMap`) 가장자리에 닿는 영토는 절대 안 갇힌다. 전부 육지로 둔다."""
    gm = GameMap.from_rows(["." * w] * h)
    ps = {}
    for pid in range(players):
        ps[pid] = PlayerState(pid=pid, name=f"P{pid}", kind="nation",
                              start=gm.ref(pid, 0))
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {pid: 0 for pid in range(players)}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS * 2
    return st


def fill(st, pid, x0, y0, x1, y1):
    """⚠ **엔진처럼 `_tile_changed` 도 찍는다.**

    안 찍으면 "영토가 안 바뀐 나라는 건너뛴다" 관문에 걸려 검사가 안 돈다 —
    이 파일이 문서화한 그 함정에 테스트 헬퍼 자신이 먼저 걸렸다."""
    n = 0
    touched = {pid}
    for y in range(y0, y1):
        for x in range(x0, x1):
            t = st.gmap.ref(x, y)
            old = int(st.gmap.owner[t])
            if old >= 0:
                st._counts[old] -= 1
                touched.add(old)
            st.gmap.owner[t] = pid
            n += 1
    st._counts[pid] = st._counts.get(pid, 0) + n
    for who in touched:
        st._tile_changed[who] = st.tick_count


def pocket(st):
    """1번이 0번의 땅 한가운데에 **완전히 갇힌** 상태를 만든다."""
    fill(st, 0, 5, 5, 35, 35)                    # 0번의 넓은 땅
    fill(st, 1, 18, 18, 22, 22)                  # 그 안에 1번의 조각
    return st


def run_until_check(st, ticks: int = 60):
    for _ in range(ticks):
        st.tick()


# --- 기본 ---------------------------------------------------------------------

def test_a_pocket_inside_one_enemy_is_absorbed():
    """한 상대에게 완전히 둘러싸인 조각은 **그 상대에게 넘어간다.**

    막지 않았으면: 그 조각이 판 끝까지 남는다. 국경이 한 쪽뿐이라 공격 부대가
    거의 안 가기 때문이다."""
    st = pocket(state())
    assert st.tiles(1) == 16
    run_until_check(st)
    assert st.tiles(1) == 0, "갇힌 조각이 안 넘어갔다"
    assert st.tiles(0) >= 900


def test_a_pocket_touching_water_is_not_absorbed():
    """⚠ **바다에 닿으면 안 갇힌 것이다.** 나갈 길이 있다."""
    st = state()
    fill(st, 0, 5, 5, 35, 35)
    fill(st, 1, 18, 18, 22, 22)
    # 조각 옆 한 칸을 바다로 만든다
    st.gmap.terrain[st.gmap.ref(22, 20)] = Terrain.OCEAN
    st.gmap.owner[st.gmap.ref(22, 20)] = -1
    st._counts[0] -= 1
    st.gmap.invalidate_terrain_caches()
    run_until_check(st)
    assert st.tiles(1) == 16, "바다에 닿았는데 흡수됐다"


def test_a_pocket_touching_neutral_land_is_not_absorbed():
    """**주인 없는 이웃이 하나라도 있으면** 안 갇힌 것이다."""
    st = state()
    fill(st, 0, 5, 5, 35, 35)
    fill(st, 1, 18, 18, 22, 22)
    t = st.gmap.ref(22, 20)                      # 조각에 붙은 칸을 중립으로
    st.gmap.owner[t] = -1
    st._counts[0] -= 1
    run_until_check(st)
    assert st.tiles(1) == 16, "중립에 닿았는데 흡수됐다"


def test_a_pocket_on_the_map_edge_is_not_absorbed():
    """지도 가장자리는 그 자체로 나갈 길이다(`isOnEdgeOfMap`)."""
    st = state()
    fill(st, 0, 0, 0, 40, 40)
    fill(st, 1, 0, 18, 4, 22)                    # 왼쪽 끝에 붙은 조각
    run_until_check(st)
    assert st.tiles(1) == 16, "가장자리에 닿았는데 흡수됐다"


# --- 누가 가져가나 -------------------------------------------------------------

def test_two_different_enemies_do_not_absorb_the_largest_cluster():
    """가장 큰 덩어리는 **적이 정확히 하나**여야 한다(원본 규칙).

    둘이 나눠 둘러싸고 있으면 아무도 못 가져간다."""
    st = state()
    fill(st, 0, 5, 5, 35, 20)                    # 위쪽 절반은 0번
    fill(st, 2, 5, 20, 35, 35)                   # 아래쪽 절반은 2번
    fill(st, 1, 18, 18, 22, 22)                  # 경계에 걸친 조각
    run_until_check(st)
    assert st.tiles(1) == 16, "적이 둘인데 흡수됐다"


def test_an_ally_does_not_absorb_the_pocket():
    """동맹은 안 가져간다 — 원본도 `!surroundedBy.isFriendly(player)` 를 본다."""
    st = pocket(state())
    st.request_alliance(0, 1)
    st.accept_alliance(1, 0)
    run_until_check(st)
    assert st.tiles(1) == 16, "동맹이 가져갔다"


# --- 두 번째 검사 (isEnclosed) --------------------------------------------------

def test_a_hole_in_a_wide_empire_does_not_hand_over_the_empire():
    """⚠ **국경 덩어리 검사만으로는 부족하다.**

    원본 주석: 그 검사는 국경 타일만 보는데 실제로 넘어가는 것은 **덩어리가 얹힌
    영토 전체**다. 넓은 제국 한가운데 뚫린 구멍(적의 고립지, 핵 분화구)을 감싼
    덩어리가 검사를 통과할 수 있다 — `isEnclosed` 가 그걸 막는다.

    여기서는 0번이 지도 끝까지 뻗어 있으므로, 안쪽 구멍을 감싼 덩어리가 무슨
    답을 내든 **영토는 안 넘어가야 한다.**"""
    st = state()
    fill(st, 0, 0, 0, 40, 40)                    # 0번이 지도 전체
    fill(st, 1, 18, 18, 22, 22)                  # 한가운데 1번의 구멍
    before = st.tiles(0)
    run_until_check(st)
    assert st.tiles(0) >= before - 16, "제국이 통째로 넘어갔다"


def test_is_enclosed_walks_through_neutral_land():
    """**분화구는 출구가 아니다.** 주인 없는 땅은 타고 걸어가되 바다·지도 끝만
    출구다(원본 주석 그대로)."""
    st = state()
    fill(st, 0, 5, 5, 35, 35)
    fill(st, 1, 18, 18, 22, 22)
    st.gmap.owner[st.gmap.ref(10, 10)] = -1      # 0번 땅 안쪽의 분화구
    st._counts[0] -= 1
    assert enclave.is_enclosed(st.gmap, 1, st.gmap.ref(19, 19)),         "분화구를 출구로 쳤다"

    # ⚠ 0번은 **안 갇혔다.** 자기 땅 밖은 전부 중립인데 중립은 타고 걸어갈 수
    # 있으므로 지도 끝까지 간다. 처음에 반대로 기대했다가 틀렸다 —
    # `is_enclosed` 는 "남의 땅에 막혔는가"를 보는 것이지 "내 땅이 끝나는가"를
    # 보는 것이 아니다.
    assert not enclave.is_enclosed(st.gmap, 0, st.gmap.ref(6, 6))


# --- 덩어리 묶기 ---------------------------------------------------------------

def test_clusters_join_diagonally():
    """국경 덩어리는 **대각선으로도 이어진다**(원본 `forEachNeighborWithDiag`).

    4방향으로만 묶으면 대각으로 이어진 국경이 두 덩어리가 돼 판정이 달라진다."""
    gm = GameMap.from_rows(["." * 10] * 10)
    border = {gm.ref(2, 2), gm.ref(3, 3)}        # 대각으로만 붙어 있다
    assert len(enclave.clusters(gm, border)) == 1

    far = {gm.ref(2, 2), gm.ref(5, 5)}
    assert len(enclave.clusters(gm, far)) == 2


def test_the_whole_territory_moves_not_just_the_border():
    """넘어가는 것은 **국경 덩어리가 아니라 그 땅 전체**다."""
    st = pocket(state())
    inner = st.gmap.ref(19, 19)                  # 안쪽 칸(국경이 아니다)
    assert int(st.gmap.owner[inner]) == 1
    run_until_check(st)
    assert int(st.gmap.owner[inner]) == 0, "안쪽 칸이 안 넘어갔다"


def test_a_secondary_pocket_touching_neutral_land_is_not_absorbed():
    """⚠ **작은 덩어리 경로**로 중립 이웃 검사를 잰다.

    가장 큰 덩어리에는 "적이 정확히 하나" 검사가 따로 있어서, 중립 이웃 검사를
    지워도 그쪽이 대신 걸린다(변이 U2 가 그렇게 살아남았다). 작은 덩어리에는
    그 검사가 없으므로 여기서만 실제로 문다.

    재료: 1번이 **두 조각**을 갖는다 — 지도 끝에 붙은 큰 땅(안 갇힘)과 0번
    안에 갇힌 작은 조각. 작은 조각이 중립 칸에 닿아 있다."""
    st = state()
    fill(st, 0, 5, 5, 35, 35)
    fill(st, 1, 0, 0, 40, 4)                     # 지도 위쪽 끝 — 큰 덩어리
    fill(st, 1, 18, 18, 22, 22)                  # 0번 안의 작은 조각
    t = st.gmap.ref(22, 20)                      # 그 조각에 붙은 중립 칸
    st.gmap.owner[t] = -1
    st._counts[0] -= 1
    before = st.tiles(1)
    run_until_check(st)
    assert st.tiles(1) == before, "중립에 닿은 작은 조각이 흡수됐다"


# --- 언제 도는가 ---------------------------------------------------------------

def test_the_check_skips_players_whose_territory_did_not_change():
    """⚠ **영토가 안 바뀐 나라는 건너뛴다**(원본 `lastTileChange >= lastCalc`).

    이걸 빼면 판 시간의 절반이 여기로 간다 — 실측으로 188ms/tick 이 나왔고
    10Hz 예산(100ms)을 넘겼다. 대부분의 나라는 대부분의 20 tick 동안 국경이
    그대로다.

    ⚠ **조용히 깨지는 자리다.** 영토를 바꾸는 곳 중 하나라도 시각 찍기를
    빠뜨리면 그 나라는 영영 검사에서 빠진다 — 규칙이 아니라 성능 코드처럼
    보이지만 결과는 규칙이 안 도는 것과 같다."""
    st = state()
    fill(st, 0, 5, 5, 35, 35)
    fill(st, 1, 0, 0, 40, 4)                     # 1번의 본진(지도 끝 — 안 갇힘)
    fill(st, 1, 18, 18, 22, 22)                  # 갇힌 조각
    before = st.tiles(1)
    run_until_check(st)
    assert st.tiles(1) == before - 16, "재료가 흡수를 안 만든다"

    # ⚠ 1번이 **살아 있어야** 이어서 잴 수 있다. 조각만 있으면 첫 흡수로
    # 탈락해 `alive` 에서 빠지고, 그러면 "안 도는 이유"가 관문이 아니라
    # 죽음이 된다(처음에 그렇게 만들었다가 헛짚었다).
    assert st.players[1].alive

    # 조각을 다시 만들면 영토가 바뀌었으므로 또 검사가 돈다
    fill(st, 1, 18, 18, 22, 22)
    run_until_check(st)
    assert st.tiles(1) == before - 16, "영토가 바뀌었는데 검사가 안 돌았다"


def test_every_ownership_change_stamps_the_clock():
    """영토를 바꾸는 경로가 **전부** 시각을 찍는가.

    한 곳이라도 빠지면 그 나라가 검사에서 조용히 빠진다."""
    st = pocket(state())
    st.tick()
    st._tile_changed.clear()                     # 지우고 시작한다

    # 공격으로 한 칸이 넘어가는 경우 — **양쪽 다** 찍혀야 한다
    st._conquer_tile(0, st.gmap.ref(18, 18), 1)
    assert st._tile_changed.get(0) == st.tick_count, "뺏은 쪽이 안 찍혔다"
    assert st._tile_changed.get(1) == st.tick_count, "뺏긴 쪽이 안 찍혔다"

    # 썩음으로 중립이 되는 경우
    st._tile_changed.clear()
    st._rot_step(0, st.elapsed)
    # (썩는 조건이 아니면 아무 일도 안 하므로 여기서는 호출만 확인한다)

    # 소멸(`_wipe`)
    st._tile_changed.clear()
    st._wipe(1)
    assert st._tile_changed.get(1) == st.tick_count, "소멸이 안 찍혔다"
