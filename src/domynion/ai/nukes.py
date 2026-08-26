"""핵 판단 — 원본 `NationNukeBehavior`.

⚠ **이식 누락 스물여섯.** 우리 것은 `nation.py` 안의 열 줄짜리 축소판이었다:

    영토가 가장 큰 적을 골라 → 그 나라 아무 칸에나 → 무작위로 한 발

원본은 세 단계다:

1. **표적 나라를 고른다**(`findBestNukeTarget`) — 들어오는 공격 · 동맹의 표적 ·
   가장 미워하는 상대 · FFA 왕관. **봇(부족)은 절대 안 친다.**
2. **후보 칸을 모은다** — 무작위 10칸 **+ 상대 건물 타일 전부**.
3. **점수로 고른다**(`nukeTileScore`) — 반경 안 건물 값의 합에서 사일로까지의
   거리를 빼고, 최근에 때린 자리를 크게 깎는다.

축소판이 실제로 낸 차이 셋:

- **무작위 칸은 내 땅·동맹 땅을 같이 날린다.** 원본은 반경 안이 전부 표적의
  땅이어야 쏜다(`isValidNukeTile`) — easy·medium 에서는 예외가 없다.
- **건물을 안 본다.** 사일로(50,000×레벨)와 도시(25,000×레벨)가 있는 자리와
  빈 들판이 같은 값이 된다.
- **같은 자리를 계속 때린다.** 최근 표적 감점이 없으면 첫 표적에 계속 붓는다.
"""

from __future__ import annotations

import math
import random

from ..core import constants as C
from ..core.gamemap import TileRef
from ..core.nukes import NUKE_MAGNITUDES, sam_range
from ..core.relations import Relation
from ..core.units import STRUCTURES, UnitType

# `nukeTileScore` 의 건물 값. 사일로가 가장 값진 것이 핵심이다 — 상대의 핵을
# 먼저 없애는 것이 무엇보다 낫다.
NUKE_TILE_VALUE = {
    UnitType.MISSILE_SILO: 50_000,
    UnitType.CITY: 25_000,
    UnitType.PORT: 15_000,
    UnitType.FACTORY: 15_000,
    UnitType.DEFENSE_POST: 5_000,
}

# 사일로에서 멀수록 깎는다. 다만 건물 값의 20% 는 남긴다 — 안 남기면 멀리 있는
# 알짜 표적이 가까운 빈 들판보다 못해진다.
NUKE_DISTANCE_PENALTY = 30
NUKE_MIN_VALUE_FRACTION = 0.2

# 최근에 때린 자리(안쪽 반경 안)는 이만큼 깎는다. 사실상 금지다.
NUKE_RECENT_PENALTY = 1_000_000
NUKE_RECENT_MAX_AGE = 600            # tick. 원본 주석: 1분

# medium 은 이 거리 안에 SAM 이 있으면 아예 안 쏜다(`hasSam → -1`).
NUKE_SAM_AVOID_RANGE = 50

# 후보로 뽑는 무작위 타일 수. impossible 만 더 많이 본다.
NUKE_RANDOM_TILES = 10
NUKE_RANDOM_TILES_IMPOSSIBLE = 30

# 발사할 때마다 체감 비용이 오른다 — MIRV 를 위해 모으는 것을 흉내 낸다.
# ⚠ 원자탄이 더 가파르다(50% 대 25%). 후반에 원자탄을 덜 매력적으로 만든다.
ATOM_COST_GROWTH = 1.5
HYDRO_COST_GROWTH = 1.25

# 수폭 나라(`isHydroNation`)일 확률 1/3. 이 나라는 심한 공격을 받는 중이 아니면
# 원자탄을 안 쓴다 — AI 마다 성격이 갈리게 하는 장치다.
HYDRO_NATION_CHANCE = 3

# FFA 왕관을 노리는 문턱 — 내 점유율보다 이만큼 앞서 있으면 친다.
FFA_CROWN_THRESHOLD = {"easy": 0.4, "medium": 0.3, "hard": 0.2, "impossible": 0.1}


class NationNukeBehavior:
    """한 나라의 핵 판단. `NationBot` 이 하나씩 들고 있다."""

    __slots__ = ("pid", "rng", "difficulty", "is_hydro_nation",
                 "_atom_cost", "_hydro_cost", "_recent")

    def __init__(self, pid: int, rng: random.Random, difficulty: str,
                 atom_cost: int, hydro_cost: int):
        self.pid, self.rng, self.difficulty = pid, rng, difficulty
        self.is_hydro_nation = rng.randrange(HYDRO_NATION_CHANCE) == 0
        self._atom_cost = float(atom_cost)
        self._hydro_cost = float(hydro_cost)
        # (tick, 타일, 종류) — 최근에 때린 자리
        self._recent: list = []

    # --- 진입점 -----------------------------------------------------------

    def maybe_send(self, st, should_attack) -> bool:
        """`maybeSendNuke` — 쐈으면 True.

        `should_attack` 은 `NationBot._should_attack` 이다. 원본도 `shouldAttack`
        을 먼저 보므로, 사람을 봐주는 난이도에서는 핵도 같이 봐준다(§5.27)."""
        p = st.players.get(self.pid)
        if p is None or not p.alive:
            return False
        silos = [u for u in p.units.of(UnitType.MISSILE_SILO)
                 if not u.under_construction]
        if not silos or st.ready_missiles(self.pid) <= 0:
            return False

        target = self.find_target(st)
        if target is None:
            return False
        # ⚠ **부족(봇)은 안 친다.** 원본 주석: *"Don't nuke tribes"*.
        # 봇은 어차피 약하고 수가 많아, 핵을 여기 쓰면 나라끼리의 판이 안 돈다.
        if target.is_bot or not should_attack(st, target.pid):
            return False

        utype = self._pick_type(st, p)
        if utype is None:
            return False

        tile = self._pick_tile(st, p, target, silos, utype)
        if tile is None:
            return False
        if st.launch_nuke(self.pid, utype, tile) is None:
            return False
        self._record(st.tick_count, tile, utype)
        return True

    # --- 표적 나라 --------------------------------------------------------

    def find_target(self, st):
        """`findBestNukeTarget` — 순서가 곧 우선순위다.

        1. **들어오는 공격**(원본 주석: *"Most important!"*)
        2. 동맹이 지목한 표적 — 관계가 우호 이상일 때만
        3. 가장 미워하는 상대. 단 **나보다 훨씬 약하면 건너뛴다** —
           원본 주석: *"we don't need nukes to deal with them"*
        4. FFA 왕관 — 나보다 난이도별 문턱만큼 앞서 있으면
        """
        me = st.players.get(self.pid)
        if me is None:
            return None

        # 1) 들어오는 공격
        for a in st.attacks:
            if a.target == self.pid and a.attacker != self.pid:
                q = st.players.get(a.attacker)
                if q is not None and q.alive and not st.diplomacy.is_friendly(
                        self.pid, q.pid):
                    return q

        # 2) 동맹의 표적
        for ally_pid in st.diplomacy.allies_of(self.pid):
            if st.relation_of(self.pid, ally_pid) < Relation.FRIENDLY:
                continue
            for tpid in st.targets_of(ally_pid):
                if tpid == self.pid or st.diplomacy.is_friendly(self.pid, tpid):
                    continue
                q = st.players.get(tpid)
                if q is not None and q.alive:
                    return q

        # 3) 가장 미워하는 상대 — 약한 상대는 건너뛴다
        my_cap = me.max_troops(max(1, st.tiles(self.pid)))
        for other, rel in me.relations.sorted_by_relation(
                {q.pid for q in st.alive}):
            if rel > Relation.HOSTILE:
                continue
            if st.diplomacy.is_friendly(self.pid, other):
                continue
            q0 = st.players.get(other)
            if q0 is None:
                continue
            if my_cap >= q0.max_troops(max(1, st.tiles(other))) * 2:
                continue
            if q0.alive:
                return q0

        # 4) FFA 왕관
        return self._ffa_crown(st)

    def _ffa_crown(self, st):
        """`findFFACrownTarget` — 1등이 나보다 문턱만큼 앞서 있으면 친다.

        ⚠ 분모가 **낙진이 없는 땅**이다. 낙진으로 못 쓰게 된 땅을 세면 판이
        망가질수록 점유율이 낮게 나와 아무도 왕관을 안 친다."""
        alive = list(st.alive)
        if len(alive) <= 1:
            return None
        alive.sort(key=lambda q: st.tiles(q.pid), reverse=True)
        first = alive[0]
        if first.pid == self.pid or st.diplomacy.is_friendly(self.pid, first.pid):
            return None
        # `numLandTiles() - numTilesWithFallout()` — 낙진으로 못 쓰게 된 땅은 뺀다
        usable = st.gmap.land_count - int(st.fallout.mask.sum())
        if usable <= 0:
            return None
        gap = (st.tiles(first.pid) - st.tiles(self.pid)) / usable
        return first if gap > FFA_CROWN_THRESHOLD[self.difficulty] else None

    # --- 종류 -------------------------------------------------------------

    def _pick_type(self, st, p):
        """수폭이 되면 수폭, 아니면 원자탄. **체감 비용**으로 본다.

        ⚠ 수폭 나라(1/3)는 원자탄을 건너뛴다 — 심한 공격을 받는 중이 아니면.
        이게 없으면 모든 AI 가 똑같이 싼 원자탄부터 쏜다."""
        if p.gold >= self.perceived_cost(st, UnitType.HYDROGEN_BOMB):
            return UnitType.HYDROGEN_BOMB
        if ((not self.is_hydro_nation or self._under_heavy_attack(st, p))
                and p.gold >= self.perceived_cost(st, UnitType.ATOM_BOMB)):
            return UnitType.ATOM_BOMB
        return None

    def perceived_cost(self, st, utype) -> float:
        """`getPerceivedNukeCost` — 쏠수록 비싸 **보이게** 한다(MIRV 저축 흉내).

        실비용으로 돌아가는 조건 셋: 둘만 남았다 · MIRV + 수폭을 이미 살 수
        있다 · (hard 이상) 심한 공격을 받는 중이다 — 곧 죽을 판이면 아끼는 것이
        의미가 없다."""
        p = st.players[self.pid]
        real = p.units.cost(utype)
        if len(list(st.alive)) == 2:
            return real
        if p.gold > (p.units.cost(UnitType.MIRV)
                     + p.units.cost(UnitType.HYDROGEN_BOMB)):
            return real
        if (self.difficulty in ("hard", "impossible")
                and self._under_heavy_attack(st, p)):
            return real
        return (self._atom_cost if utype is UnitType.ATOM_BOMB
                else self._hydro_cost)

    def _under_heavy_attack(self, st, p) -> bool:
        """`isUnderHeavyAttack` — 들어오는 병력이 내 병력 이상이면."""
        incoming = sum(a.troops for a in st.attacks if a.target == self.pid)
        return incoming >= p.troops

    # --- 타일 -------------------------------------------------------------

    def _pick_tile(self, st, p, target, silos, utype):
        """후보를 모아 `nukeTileScore` 로 가장 좋은 칸을 고른다.

        후보 = 무작위 영토 칸 + **상대 건물이 선 칸 전부**. 건물 칸을 빼면
        알짜를 영영 못 맞힌다 — 무작위로 건물 위를 찍을 확률은 거의 0이다."""
        tiles = st.gmap.owned_refs(target.pid)
        if not len(tiles):
            return None
        n = (NUKE_RANDOM_TILES_IMPOSSIBLE if self.difficulty == "impossible"
             else NUKE_RANDOM_TILES)
        n = min(n, len(tiles))
        idx = self.rng.sample(range(len(tiles)), n)
        cands = {int(tiles[i]) for i in idx}
        structures = [u for u in target.units.units
                      if u.active and u.utype in STRUCTURES]
        cands |= {u.tile for u in structures}

        self._forget_old(st.tick_count)
        outer = NUKE_MAGNITUDES[utype][1]
        best, best_v = None, -1.0
        for t in cands:
            if not self._blast_is_clean(st, t, outer, target.pid):
                continue
            v = self.tile_score(st, t, silos, structures, utype)
            if v > best_v:
                best, best_v = t, v
        return best

    def _blast_is_clean(self, st, tile: TileRef, radius: int, target_pid: int) -> bool:
        """`isValidNukeTile` — 반경 안이 **전부 표적의 땅**이어야 한다.

        ⚠ 이게 없으면 내 땅·동맹 땅을 같이 날린다. easy·medium 은 예외가 없고
        (원본 주석: *"nuke away from the border"*), hard 이상만 빈 땅을 허용한다.

        원본은 반경과 반경/2 두 겹의 상자를 훑는다 — 바깥 반경 안쪽에 낀 남의
        땅 조각을 놓치지 않으려는 것이다. 우리는 상자 하나를 격자로 훑는다."""
        gmap = st.gmap
        w, h = gmap.width, gmap.height
        cx, cy = tile % w, tile // w
        r2 = radius * radius
        loose = self.difficulty in ("hard", "impossible")
        # 반경 전체를 칸마다 보면 비싸다. 상자를 성글게 훑되 반경/2 격자를 겹쳐
        # 원본의 두 겹과 같은 촘촘함을 낸다.
        step = max(1, radius // 6)
        for dy in range(-radius, radius + 1, step):
            y = cy + dy
            if not (0 <= y < h):
                continue
            for dx in range(-radius, radius + 1, step):
                if dx * dx + dy * dy > r2:
                    continue
                x = cx + dx
                if not (0 <= x < w):
                    continue
                t = y * w + x
                owner = int(gmap.owner[t])
                if owner == target_pid:
                    continue
                if loose and owner < 0:
                    continue          # hard 이상은 빈 땅을 허용한다
                return False
        return True

    def tile_score(self, st, tile: TileRef, silos, structures, utype) -> float:
        """`nukeTileScore` — 반경 안 건물 값 − 사일로 거리 − 최근 표적 감점."""
        gmap = st.gmap
        outer = NUKE_MAGNITUDES[utype][1]
        out2 = outer * outer
        w = gmap.width

        def d2(a, b):
            dx, dy = a % w - b % w, a // w - b // w
            return dx * dx + dy * dy

        value = 0.0
        for u in structures:
            if d2(tile, u.tile) > out2:
                continue
            value += NUKE_TILE_VALUE.get(u.utype, 0) * u.level

        # medium 은 SAM 이 가까우면 아예 안 쏜다. easy 는 SAM 을 아예 안 본다.
        if self.difficulty == "medium":
            sam2 = NUKE_SAM_AVOID_RANGE ** 2
            if any(u.utype is UnitType.SAM_LAUNCHER and d2(tile, u.tile) <= sam2
                   for u in structures):
                return -1.0

        # impossible + 수폭이면 **사거리 밖에서 때릴 수 있는 SAM** 은 오히려 값지다
        if self.difficulty == "impossible" and utype is UnitType.HYDROGEN_BOMB:
            for u in structures:
                if u.utype is not UnitType.SAM_LAUNCHER or u.level >= 5:
                    continue
                if d2(tile, u.tile) > out2:
                    continue
                r = sam_range(u.level)
                if d2(tile, u.tile) > r * r:
                    value += 100_000 * u.level

        # 사일로에서 멀수록 깎되, 건물 값의 20% 는 남긴다
        if silos:
            near = min(math.sqrt(d2(tile, u.tile)) for u in silos)
            value = max(value * NUKE_MIN_VALUE_FRACTION,
                        value - near * NUKE_DISTANCE_PENALTY)

        # 최근에 때린 자리는 사실상 금지
        for _tick, rtile, rtype in self._recent:
            inner = NUKE_MAGNITUDES[rtype][0]
            if d2(tile, rtile) <= inner * inner:
                value -= NUKE_RECENT_PENALTY
        return value

    # --- 발사 기록 --------------------------------------------------------

    def _record(self, tick: int, tile: TileRef, utype) -> None:
        self._recent.append((tick, tile, utype))
        if utype is UnitType.ATOM_BOMB:
            self._atom_cost *= ATOM_COST_GROWTH
        else:
            self._hydro_cost *= HYDRO_COST_GROWTH

    def _forget_old(self, tick: int) -> None:
        cut = tick - NUKE_RECENT_MAX_AGE
        while self._recent and self._recent[0][0] < cut:
            self._recent.pop(0)
