"""MIRV 판단 — 원본 `NationMIRVBehavior`.

⚠ **이식 누락 서른.** 이 파일이 통째로 없었다. AI 는 MIRV 값을 위해 **저축은
하는데**(`structures.py` 의 `getSaveUpTarget` 이 "MIRV 한 발 + 수폭 한 발"을
목표로 잡는다) **정작 사는 행동이 없었다.** 그래서 MIRV 는 판에서 한 발도 안
나갔고, 그 골드는 갈 곳 없이 쌓였다(§5.40 이 "지출의 85%가 전함"이라고 센 그 옆자리다).

MIRV 는 아무 때나 쏘는 무기가 아니다. 원본은 **세 가지 상황**에서만 쏜다:

1. **반격** — 나를 겨눈 MIRV 가 날아오는 중이면 그 상대에게(가장 큰 쪽부터)
2. **승리 저지** — 누군가 땅의 40~75%(난이도별)를 넘게 가지면
3. **폭주 저지** — 1등의 도시가 2등의 1.15~2배(난이도별)를 넘어서면

난이도는 **문턱이 아니라 반응 속도**로 들어간다. impossible 은 40% 에서 이미
반응하고 easy 는 75% 까지 기다린다. 망설임 확률도 같은 축이다(easy 1/2 · impossible 1/16).
"""

from __future__ import annotations

import random

from ..core.units import UnitType

# 30초. **나라들이 같은 상대에게 몰리는 것을 막는 장치**이므로 인스턴스가 아니라
# 클래스에 둔다 — 원본도 `private static recentMirvTargets` 다. 이게 없으면
# 골드가 많은 판에서 열 나라가 같은 tick 에 같은 상대를 MIRV 로 덮는다.
MIRV_COOLDOWN_TICKS = 300

# `chance(n)` = 1/n 로 **망설인다.** 낮을수록 자주 망설인다.
MIRV_HESITATION_ODDS = {"easy": 2, "medium": 4, "hard": 8, "impossible": 16}

# 이 점유율을 넘으면 승리 저지에 나선다(FFA — 팀 모드는 우리에게 없다).
MIRV_VICTORY_DENIAL_SHARE = {"easy": 0.75, "medium": 0.65,
                             "hard": 0.55, "impossible": 0.4}

# 폭주 저지 — 1등 도시가 2등의 이 배수를 넘으면.
MIRV_STEAMROLL_GAP = {"easy": 2.0, "medium": 1.5, "hard": 1.25, "impossible": 1.15}

# 그리고 1등이 최소 이만큼은 갖고 있어야 한다(초반에 3대 1로 터지는 것을 막는다).
MIRV_STEAMROLL_MIN_CITIES = {"easy": 20, "medium": 10, "hard": 10, "impossible": 8}


class NationMIRVBehavior:
    """한 나라의 MIRV 판단. `NationBot` 이 하나씩 들고 있다."""

    # pid → 마지막으로 MIRV 를 맞은 tick. **모든 나라가 공유한다.**
    recent_targets: dict[int, int] = {}

    __slots__ = ("pid", "rng", "difficulty")

    def __init__(self, pid: int, rng: random.Random, difficulty: str):
        self.pid, self.rng, self.difficulty = pid, rng, difficulty

    def consider(self, st) -> bool:
        """`considerMIRV` — 쐈으면 True.

        ⚠ 순서가 원본과 같아야 한다. 원본 `NationExecution` 은 **건물보다 먼저**
        MIRV 를 본다 — 골드를 건물에 써 버린 뒤에 보면 영원히 못 산다."""
        p = st.players.get(self.pid)
        if p is None or not p.alive:
            return False
        if not [u for u in p.units.of(UnitType.MISSILE_SILO)
                if not u.under_construction]:
            return False
        if p.gold < st.nuke_cost(self.pid, UnitType.MIRV):
            return False
        # ⚠ 망설임은 **비용 검사 뒤**다. 앞에 두면 값을 못 치르는 tick 마다
        # 주사위를 굴려 버려 확률의 뜻이 달라진다.
        if self.rng.randrange(MIRV_HESITATION_ODDS[self.difficulty]) == 0:
            return False

        for pick in (self._counter_target, self._victory_denial_target,
                     self._steamroll_target):
            q = pick(st)
            if q is not None and not self._recently_mirved(st, q.pid):
                return self._send(st, q)
        return False

    # --- 표적 -------------------------------------------------------------

    def _valid_targets(self, st) -> list:
        """**부족(봇)은 안 친다.** 핵과 같은 규칙이다 — 봇에게 MIRV 를 쓰면
        나라끼리의 판이 안 돈다."""
        return [q for q in st.alive
                if q.pid != self.pid and not q.is_bot
                and not st.diplomacy.same_team(self.pid, q.pid)]

    def _counter_target(self, st):
        """`selectCounterMirvTarget` — **나를 겨눈 MIRV** 를 쏜 상대. 큰 쪽부터."""
        inbound = {n.owner for n in st.nukes
                   if n.utype is UnitType.MIRV
                   and int(st.gmap.owner[n.dst]) == self.pid}
        cands = [q for q in self._valid_targets(st) if q.pid in inbound]
        if not cands:
            return None
        return max(cands, key=lambda q: st.tiles(q.pid))

    def _victory_denial_target(self, st):
        """`selectVictoryDenialTarget` — 땅을 난이도별 문턱만큼 가진 상대.

        ⚠ 분모가 **지도 전체 육지**다(`numLandTiles`). 핵의 왕관 판정과 달리
        낙진을 빼지 않는다 — 원본이 그렇다."""
        total = st.gmap.land_count
        if total <= 0:
            return None
        floor = MIRV_VICTORY_DENIAL_SHARE[self.difficulty]
        best, best_share = None, 0.0
        for q in self._valid_targets(st):
            share = st.tiles(q.pid) / total
            if share >= floor and share > best_share:
                best, best_share = q, share
        return best

    def _steamroll_target(self, st):
        """`selectSteamrollStopTarget` — 1등의 도시가 2등을 크게 앞지르면.

        도시 수는 **레벨 합**이다(`unitCount` 도 레벨을 더한다 — §5.30)."""
        ranked = sorted(st.alive, key=lambda q: q.units.owned(UnitType.CITY),
                        reverse=True)
        if len(ranked) < 2:
            return None
        top = ranked[0]
        top_cities = top.units.owned(UnitType.CITY)
        if top_cities <= MIRV_STEAMROLL_MIN_CITIES[self.difficulty]:
            return None
        second = ranked[1].units.owned(UnitType.CITY)
        if top_cities < second * MIRV_STEAMROLL_GAP[self.difficulty]:
            return None
        return top if top in self._valid_targets(st) else None

    # --- 발사 -------------------------------------------------------------

    def _recently_mirved(self, st, pid: int) -> bool:
        last = NationMIRVBehavior.recent_targets.get(pid)
        return last is not None and st.tick_count - last < MIRV_COOLDOWN_TICKS

    def _send(self, st, target) -> bool:
        """`maybeSendMIRV` — 표적 영토의 **중심**을 친다. 무작위 칸이 아니다."""
        tile = territory_center(st, target.pid)
        if tile is None:
            return False
        if st.launch_nuke(self.pid, UnitType.MIRV, tile) is None:
            return False
        NationMIRVBehavior.recent_targets[target.pid] = st.tick_count
        return True


def territory_center(st, pid: int):
    """`calculateTerritoryCenter` — 영토 경계 상자의 중심.

    원본은 **국경 타일**로 상자를 잡는다. 우리는 국경 목록이 따로 없어 소유 타일을
    쓰는데 **같은 상자가 나온다** — 영토의 x·y 극값은 언제나 국경 타일이다.
    중심이 남의 땅이면(오목한 영토) 가장 가까운 내 타일로 물러서는 것도 같다:
    중심이 안 가진 칸이면 거기서 가장 가까운 소유 타일은 반드시 국경 타일이다."""
    tiles = st.gmap.owned_refs(pid)
    if not len(tiles):
        return None
    w = st.gmap.width
    xs, ys = tiles % w, tiles // w
    cx = int((int(xs.min()) + int(xs.max())) // 2)
    cy = int((int(ys.min()) + int(ys.max())) // 2)
    centre = cy * w + cx
    if int(st.gmap.owner[centre]) == pid:
        return int(centre)
    d2 = (xs - cx) ** 2 + (ys - cy) ** 2
    return int(tiles[int(d2.argmin())])
