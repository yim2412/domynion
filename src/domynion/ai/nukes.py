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
from ..core.nukes import (NUKE_MAGNITUDES, NUKE_SPEED, Nuke, is_targetable,
                          sam_range)
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

# 높은 밀도 표적 — 레벨 합 / 타일 수가 이 값을 넘으면 impossible 의 **최고 부자**
# 나라가 선제적으로 친다. 부자 하나로 제한하는 이유는 원본 주석에 있다:
# *"prevents every impossible nation from piling onto the same compact player"*.
HIGH_DENSITY_NUKE_THRESHOLD = 1 / 75
MIN_LEVEL_SUM_FOR_HIGH_DENSITY = 5

# impossible + FFA 에서 1등이 **낙진 없는 땅의 절반**을 넘게 가지면 바로 왕관을 친다.
# `FFA_CROWN_THRESHOLD`(격차 기준)와 다른 관문이다 — 이쪽은 절대 점유율이다.
IMPOSSIBLE_CROWN_SHARE = 0.5

# 높은 밀도 표적을 실제로 고를 확률 1/2(`chance(2)`).
HIGH_DENSITY_CHANCE = 2

# `maybeDestroyEnemySam` — impossible 이 물량으로 SAM 을 뚫을 때.
# 레벨 N 짜리 SAM 은 N 발을 막고 재장전에 들어가므로 **N+1 발**이 필요하다.
# 날아가는 동안 상대가 SAM 을 더 지을 수 있으니 5발마다 한 발을 더 얹는다.
MAX_NATION_SILO_UPGRADE_LEVEL = 5
SAM_OVERWHELM_EXTRA_PER = 5

# FFA 왕관을 노리는 문턱 — 내 점유율보다 이만큼 앞서 있으면 친다.
FFA_CROWN_THRESHOLD = {"easy": 0.4, "medium": 0.3, "hard": 0.2, "impossible": 0.1}


# `randTerritoryTile` — 경계 상자 안에서 이만큼 던져 본다. 넘으면 포기한다.
RAND_TILE_TRIES = 100

# 영토가 이보다 작으면 기각 표본이 잘 안 맞으므로 소유 타일에서 곧장 하나 고른다.
RAND_TILE_SMALL_TERRITORY = 100


def rand_territory_tiles(st, pid: int, tiles, count: int, rng) -> list:
    """`randTerritoryTileArray` — 표적 영토에서 무작위 칸 `count` 개.

    ⚠ **소유 타일에서 균등하게 뽑는 것이 아니다.** 원본은 영토의 **경계 상자
    안에서** (x, y) 를 던져 내 땅이면 채택하고, 100번 실패하면 **포기한다.**
    영토가 100칸 이하일 때만 소유 타일에서 곧장 하나 고른다.

    차이가 어디서 나오나: 영토가 성기거나 길쭉하면(상자는 큰데 실제 땅은 적은
    모양) 원본은 요청한 수보다 **적게** 돌려준다. 우리 옛 구현은 늘 `count` 개를
    채웠으므로 그런 나라를 칠 때 후보가 더 많았다 — 깨끗한 자리를 찾을 확률이
    원본보다 높았다는 뜻이다.

    중복도 원본대로 그냥 둔다. 부르는 쪽이 `set` 으로 받으므로 결과는 같고,
    **난수 소비 횟수**가 원본과 맞는다."""
    w, h = st.gmap.width, st.gmap.height
    xs, ys = tiles % w, tiles // w
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    owner = st.gmap.owner
    out = []
    for _ in range(count):
        got = None
        for _try in range(RAND_TILE_TRIES):
            x = rng.randint(x0, x1)
            y = rng.randint(y0, y1)
            if not (0 <= x < w and 0 <= y < h):
                continue                      # 원본 주석: "should never happen"
            t = y * w + x
            if int(owner[t]) == pid:
                got = t
                break
        if got is None and 0 < len(tiles) <= RAND_TILE_SMALL_TERRITORY:
            got = int(rng.choice(tiles))
        if got is not None:
            out.append(int(got))
    return out


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

        tile, value = self._pick_tile_scored(st, p, target, silos, utype)
        # ⚠ impossible 은 **점수가 0 이하인 칸에는 안 쏜다.** 대신 SAM 을 물량으로
        # 뚫는 쪽으로 간다(`maybeDestroyEnemySam`). 다른 난이도는 값이 -1 이어도
        # 그냥 쏜다 — 원본이 `bestValue > 0 || difficulty !== Impossible` 이다.
        if tile is None or (self.difficulty == "impossible" and value <= 0):
            if self.difficulty == "impossible":
                return self._destroy_enemy_sam(st, p, target)
            return False
        if st.launch_nuke(self.pid, utype, tile) is None:
            return False
        self._record(st.tick_count, tile, utype)
        return True

    # --- SAM 을 물량으로 뚫기 (impossible) --------------------------------

    def _destroy_enemy_sam(self, st, p, target) -> bool:
        """`maybeDestroyEnemySam` — 쏠 만한 자리가 없으면 **SAM 부터 없앤다.**

        레벨 N 짜리 SAM 은 N 발을 막고 재장전에 들어간다. 그 칸을 사거리에 넣는
        적 SAM 들의 **레벨 합 + 1** 발을 재장전 안에 몰아 넣으면 마지막 한 발이
        들어간다. 그래서 이 함수의 어려움은 발사가 아니라 **도착 시각 맞추기**다.

        ⚠ 계획은 엔진의 발사 순서를 그대로 흉내 내야 한다. `launch_nuke` 가
        표적에서 가까운 사일로부터 고르므로 같은 순서로 줄을 세운다(원본은
        맨해튼 거리, 우리 엔진은 유클리드다 — **엔진 쪽에 맞춘다.** 계획과 실제가
        어긋나면 몇 발이 엉뚱한 사일로에서 나가 일제 사격이 흩어진다).

        막히는 궤적의 사일로도 엔진은 그대로 고른다. 그 발은 요격돼 사라지지만
        **관과 골드는 쓴다** — 그래서 계획에서 빼지 않고 "낭비되는 발"로 센다."""
        if any(n.owner == self.pid and n.utype is UnitType.ATOM_BOMB
               for n in st.nukes):
            return False                      # 이미 날아가는 원자탄이 있다
        atom_cost = p.units.cost(UnitType.ATOM_BOMB)
        enemy_sams = [u for u in target.units.of(UnitType.SAM_LAUNCHER)
                      if not u.under_construction]
        if not enemy_sams:
            return False
        silos = [u for u in p.units.of(UnitType.MISSILE_SILO)
                 if not u.under_construction]
        if not silos:
            return False

        all_sams = self._enemy_sams(st)
        speed = NUKE_SPEED[UnitType.ATOM_BOMB]
        max_spread = C.SAM_COOLDOWN_TICKS // 2
        failed = None
        needs_more_silos = False

        for target_sam in sorted(enemy_sams, key=lambda u: u.level):
            tt = target_sam.tile
            covering = self._sams_covering(st, tt)
            covering_ids = {id(u) for u in covering}
            bombs_needed = sum(u.level for u in covering) + 1
            total_bombs = bombs_needed + bombs_needed // SAM_OVERWHELM_EXTRA_PER

            # 엔진이 고를 순서대로 줄을 세운다
            plan = []
            for silo in sorted(silos, key=lambda u: self._d2(st, u.tile, tt)):
                slots = silo.ready_tubes
                if slots <= 0:
                    continue
                blocked = self._trajectory_interceptable(
                    st, silo.tile, tt, UnitType.ATOM_BOMB, all_sams,
                    excluded=covering_ids)
                flight = max(1, math.ceil(
                    math.sqrt(self._d2(st, silo.tile, tt)) / speed))
                plan.extend([(flight, blocked)] * slots)

            free = [(i, f) for i, (f, blocked) in enumerate(plan) if not blocked]
            if len(free) < total_bombs:
                failed = failed or (tt, covering_ids, total_bombs)
                needs_more_silos = True
                continue

            # 도착 시각이 `max_spread` 안에 들어오는 창을 가장 크게 잡는다
            by_flight = sorted(free, key=lambda b: b[1])
            best_start, best_count = 0, 0
            for s in range(len(by_flight)):
                e = s
                while (e < len(by_flight)
                       and by_flight[e][1] - by_flight[s][1] <= max_spread):
                    e += 1
                if e - s > best_count:
                    best_start, best_count = s, e - s
            if best_count < total_bombs:
                failed = failed or (tt, covering_ids, total_bombs)
                needs_more_silos = True
                continue

            window = sorted(by_flight[best_start:best_start + best_count])
            chosen = window[:total_bombs]
            chosen_idx = {i for i, _f in chosen}
            fire_count = chosen[-1][0] + 1
            first_flight = min(f for _i, f in chosen)
            stagger = max(1, max_spread // total_bombs)

            if p.gold < atom_cost * fire_count:
                continue                      # 골드가 모자라면 다음 SAM 을 본다

            k = 0
            sent = 0
            for i in range(fire_count):
                if i in chosen_idx:
                    wait = max(0, first_flight + k * stagger - plan[i][0])
                    k += 1
                else:
                    wait = 0                  # 낭비되는 발 — 바로 쏜다
                if st.launch_nuke(self.pid, UnitType.ATOM_BOMB, tt,
                                  wait_ticks=wait) is None:
                    break
                # ⚠ **발마다** 기록한다. 원본도 `sendNuke` 를 발마다 부르므로
                # 체감 비용이 그만큼 오른다 — 한 번만 올리면 일제 사격이 공짜에
                # 가까워져 다음 판단이 계속 이쪽으로 쏠린다.
                self._record(st.tick_count, tt, UnitType.ATOM_BOMB)
                sent += 1
            return sent > 0

        if needs_more_silos and failed is not None:
            self._upgrade_helpful_silo(st, p, failed)
        return False

    def _upgrade_helpful_silo(self, st, p, failed) -> bool:
        """`maybeUpgradeHelpfulSilo` — **그 계획에 실제로 보탬이 되는** 사일로만 올린다.

        조건 셋: 실패한 표적으로 가는 궤적이 (뚫으려는 SAM 말고) 다른 SAM 에
        안 막힐 것 · 레벨이 상한 미만일 것 · 최대 레벨까지 올려도 발 수가 모자라면
        **아예 안 올릴 것**(그건 낭비다). 그중에서 **내 SAM 이 가장 잘 지켜 주는**
        것을 고른다 — 사일로가 반격에 먼저 죽으면 계획 자체가 사라진다."""
        tt, covering_ids, total_bombs = failed
        silos = [u for u in p.units.of(UnitType.MISSILE_SILO) if u.active]
        if not silos:
            return False
        all_sams = self._enemy_sams(st)
        free = [u for u in silos
                if not self._trajectory_interceptable(
                    st, u.tile, tt, UnitType.ATOM_BOMB, all_sams,
                    excluded=covering_ids)]
        if not free:
            return False
        if len(free) * MAX_NATION_SILO_UPGRADE_LEVEL < total_bombs:
            return False                      # 최대까지 올려도 모자란다
        my_sams = [u for u in p.units.of(UnitType.SAM_LAUNCHER)]
        best, best_cover = None, -1
        for silo in free:
            if silo.level >= MAX_NATION_SILO_UPGRADE_LEVEL:
                continue
            if not st.can_upgrade(self.pid, silo):
                continue
            cover = sum(s.level for s in my_sams
                        if self._d2(st, silo.tile, s.tile)
                        <= sam_range(s.level) ** 2)
            if cover > best_cover:
                best, best_cover = silo, cover
        if best is None:
            return False
        return st.upgrade(self.pid, best, 1) > 0

    # --- 표적 나라 --------------------------------------------------------

    def find_target(self, st):
        """`findBestNukeTarget` — 순서가 곧 우선순위다.

        0. (hard 이상) **둘만 남았으면** 그 상대
        1. **들어오는 공격**(원본 주석: *"Most important!"*)
        2. (impossible) **내가 최고 부자면** 건물 밀도가 높은 상대 — 확률 1/2
        3. (impossible) 1등이 땅의 절반을 넘게 가졌으면 왕관
        4. 동맹이 지목한 표적 — 관계가 우호 이상일 때만
        5. 가장 미워하는 상대. 단 **나보다 훨씬 약하면 건너뛴다** —
           원본 주석: *"we don't need nukes to deal with them"*
        6. FFA 왕관 — 나보다 난이도별 문턱만큼 앞서 있으면

        ⚠ 0·2·3 은 §5.49 에서 채웠다. 그전까지 hard·impossible 이 medium 과
        **같은 표적을 골랐다** — 난이도가 핵 표적 선택에는 거의 안 걸려 있었다.
        """
        me = st.players.get(self.pid)
        if me is None:
            return None
        alive = list(st.alive)

        # 0) 둘만 남았으면 고민할 것이 없다
        if self.difficulty in ("hard", "impossible") and len(alive) == 2:
            for q in alive:
                if q.pid != self.pid:
                    return q

        # 1) 들어오는 공격
        for a in st.attacks:
            if a.target == self.pid and a.attacker != self.pid:
                q = st.players.get(a.attacker)
                if q is not None and q.alive and not st.diplomacy.is_friendly(
                        self.pid, q.pid):
                    return q

        # 2) impossible — 최고 부자만 밀도 높은 상대를 선제적으로 친다
        if (self.difficulty == "impossible"
                and self._is_richest_nation(st)
                and self.rng.randrange(HIGH_DENSITY_CHANCE) == 0):
            dense = self._high_density_target(st)
            if dense is not None:
                return dense

        # 3) impossible — 1등이 땅의 절반을 넘게 가졌으면 왕관
        if self.difficulty == "impossible" and alive:
            usable = st.gmap.land_count - int(st.fallout.mask.sum())
            crown = max(alive, key=lambda q: st.tiles(q.pid))
            if (usable > 0 and crown.pid != self.pid
                    and not st.diplomacy.is_friendly(self.pid, crown.pid)
                    and st.tiles(crown.pid) / usable > IMPOSSIBLE_CROWN_SHARE):
                return crown

        # 4) 동맹의 표적
        for ally_pid in st.diplomacy.allies_of(self.pid):
            if st.relation_of(self.pid, ally_pid) < Relation.FRIENDLY:
                continue
            for tpid in st.targets_of(ally_pid):
                if tpid == self.pid or st.diplomacy.is_friendly(self.pid, tpid):
                    continue
                q = st.players.get(tpid)
                if q is not None and q.alive:
                    return q

        # 5) 가장 미워하는 상대 — 약한 상대는 건너뛴다
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

        # 6) FFA 왕관
        return self._ffa_crown(st)

    def _is_richest_nation(self, st) -> bool:
        """`isRichestNation` — **나라끼리만** 견준다. 봇·사람은 안 센다.

        이 관문이 있어야 impossible 나라 전부가 같은 밀집 상대에게 몰리지 않는다."""
        me = st.players.get(self.pid)
        if me is None:
            return False
        for q in st.alive:
            if q.pid == self.pid or q.kind != "nation":
                continue
            if q.gold > me.gold:
                return False
        return True

    def _high_density_target(self, st):
        """`findHighDensityTarget` — **레벨 합 / 타일 수**가 가장 높은 상대.

        개수가 아니라 레벨 합이다(§5.30 의 `unitsOwned` 와 같은 자리). 건물이
        너무 적으면 밀도가 아무리 높아도 건너뛴다 — 타일 몇 칸에 도시 하나짜리
        갓 태어난 나라가 1등이 되는 것을 막는 관문이다."""
        best, best_density = None, HIGH_DENSITY_NUKE_THRESHOLD
        for q in st.alive:
            if q.pid == self.pid or q.is_bot:
                continue
            if st.diplomacy.is_friendly(self.pid, q.pid):
                continue
            tiles = st.tiles(q.pid)
            if tiles <= 0:
                continue
            level_sum = sum(u.level for u in q.units.units
                            if u.active and u.utype in STRUCTURES)
            if level_sum < MIN_LEVEL_SUM_FOR_HIGH_DENSITY:
                continue
            density = level_sum / tiles
            if density > best_density:
                best, best_density = q, density
        return best

    def _ffa_crown(self, st):
        """`findFFACrownTarget` — 1등이 나보다 문턱만큼 앞서 있으면 친다.

        ⚠ 분모가 **낙진이 없는 땅**이다. 낙진으로 못 쓰게 된 땅을 세면 판이
        망가질수록 점유율이 낮게 나와 아무도 왕관을 안 친다."""
        alive = list(st.alive)
        if len(alive) <= 1:
            return None
        alive.sort(key=lambda q: st.tiles(q.pid), reverse=True)
        first = alive[0]
        # impossible 에서 **내가 1등이면 2등을 친다.** 이게 없으면 앞서 나간
        # impossible 나라는 이 경로에서 핵을 아예 안 쏘고 굳는다.
        if (self.difficulty == "impossible" and first.pid == self.pid
                and len(alive) >= 2):
            second = alive[1]
            if not st.diplomacy.is_friendly(self.pid, second.pid):
                return second
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
        """가장 좋은 칸만 돌려주는 얇은 겉면. 점수까지 필요하면 아래를 쓴다."""
        return self._pick_tile_scored(st, p, target, silos, utype)[0]

    def _pick_tile_scored(self, st, p, target, silos, utype):
        """후보를 모아 `nukeTileScore` 로 가장 좋은 칸을 고른다.

        후보 = 무작위 영토 칸 + **상대 건물이 선 칸 전부**. 건물 칸을 빼면
        알짜를 영영 못 맞힌다 — 무작위로 건물 위를 찍을 확률은 거의 0이다."""
        tiles = st.gmap.owned_refs(target.pid)
        if not len(tiles):
            return None, -1.0
        n = (NUKE_RANDOM_TILES_IMPOSSIBLE if self.difficulty == "impossible"
             else NUKE_RANDOM_TILES)
        cands = set(rand_territory_tiles(st, target.pid, tiles, n, self.rng))
        structures = [u for u in target.units.units
                      if u.active and u.utype in STRUCTURES]
        cands |= {u.tile for u in structures}

        self._forget_old(st.tick_count)
        outer = NUKE_MAGNITUDES[utype][1]
        # hard 이상은 **떨어질 궤적을 피한다.** 우리 엔진이 실제로 쓰는 발사
        # 사일로(`launch_nuke` 와 같은 규칙)를 미리 골라 둔다.
        ready = [u for u in silos if not u.in_cooldown]
        dodge = self.difficulty in ("hard", "impossible") and bool(ready)
        enemy_sams = self._enemy_sams(st) if dodge else []

        best, best_v = None, -1.0
        for t in cands:
            if not self._blast_is_clean(st, t, outer, target.pid):
                continue
            if dodge and enemy_sams:
                src = min(ready, key=lambda u: self._d2(st, u.tile, t)).tile
                if self._trajectory_interceptable(st, src, t, utype, enemy_sams):
                    continue
            v = self.tile_score(st, t, silos, structures, utype)
            if v > best_v:
                best, best_v = t, v
        return best, best_v

    def _d2(self, st, a: TileRef, b: TileRef) -> int:
        w = st.gmap.width
        return (a % w - b % w) ** 2 + (a // w - b // w) ** 2

    def _enemy_sams(self, st) -> list:
        """궤적 검사에 쓸 (유닛, 사거리²) 목록. 후보 칸마다 다시 모으면 안 된다."""
        out = []
        for q in st.alive:
            if q.pid == self.pid or st.diplomacy.is_friendly(self.pid, q.pid):
                continue
            for u in q.units.of(UnitType.SAM_LAUNCHER):
                if u.under_construction:
                    continue
                r = sam_range(u.level)
                out.append((u, r * r))
        return out

    def _sams_covering(self, st, tile: TileRef) -> list:
        """`findEnemySamsCoveringTile` — 이 칸을 사거리에 넣는 적 SAM 전부.

        일제 사격으로 뚫어야 할 요격 용량이 **이들 레벨의 합**이다."""
        return [u for u, r2 in self._enemy_sams(st)
                if self._d2(st, u.tile, tile) <= r2]

    def _trajectory_interceptable(self, st, src: TileRef, dst: TileRef,
                                  utype, enemy_sams: list,
                                  excluded: set | None = None) -> bool:
        """`isTrajectoryInterceptableBySam` — 이 궤적이 SAM 에 걸리는가.

        원본은 포물선 경로를 뽑아 훑지만 **우리 핵은 직선으로 난다**(`Nuke.tile`).
        그래서 같은 `Nuke` 를 실제로 한 번 날려 보며 잰다 — 예측이 엔진의 실제
        비행과 어긋날 수가 없다. 원본이 하는 `defaultNukeTargetableRange` 구간
        건너뛰기는 `is_targetable` 이 그대로 맡는다(§5.49 앞 절).

        ⚠ SAM 의 재장전 상태는 **안 본다.** 원본도 안 본다 — 도착할 때쯤이면
        관이 열려 있을 수 있으므로 지금 비어 있다고 안심하면 안 된다."""
        gm = st.gmap
        n = Nuke(owner=self.pid, utype=utype, src=src, dst=dst)
        steps = 0
        limit = int(math.dist((src % gm.width, src // gm.width),
                              (dst % gm.width, dst // gm.width))
                    / NUKE_SPEED[utype]) + 2
        while steps < limit:
            steps += 1
            n.advance()
            here = n.tile(gm)
            if is_targetable(gm, src, dst, here):
                for u, r2 in enemy_sams:
                    # 일부러 물량으로 뚫으려는 SAM 은 장애물로 세지 않는다
                    if excluded is not None and id(u) in excluded:
                        continue
                    if self._d2(st, u.tile, here) <= r2:
                        return True
            if n.arrived(gm):
                break
        return False

    def _blast_is_clean(self, st, tile: TileRef, radius: int, target_pid: int) -> bool:
        """`isValidNukeTile` × `boundingBoxTiles` — 반경이 남의 땅에 안 닿아야 한다.

        ⚠ **원본은 정사각형 두 개의 "테두리"만 본다**(`boundingBoxTiles`):
        반경짜리 하나와 반경/2 짜리 하나. 안쪽은 안 본다.

        ```ts
        boundingBoxTiles(game, tile, range)
          .concat(boundingBoxTiles(game, tile, Math.floor(range / 2)))
        ```

        전에는 원 안을 격자로 통째로 훑었다. **훨씬 엄격해서 거의 아무 데도 못
        쐈다** — 실측에서 관문 계수가 `깨끗한 자리 없음` 278회 대 `쏠 수 있었다`
        1회였고, 9,000 tick 판에서 핵이 0~4발밖에 안 나갔다(seed 1·2·3).

        테두리만 보는 것이 허술해 보이지만 의도된 것이다. 원본 주석이 안쪽 상자를
        두는 이유를 적어 뒀다 — *"in case there is a piece of unwanted territory
        inside the outer radius that we miss"*. 즉 **완벽한 검사가 아니라 값싼
        표본**이고, 그래서 핵이 실제로 나간다.
        """
        gm = st.gmap
        w, h = gm.width, gm.height
        cx, cy = tile % w, tile // w
        loose = self.difficulty in ("hard", "impossible")

        def ok(x: int, y: int) -> bool:
            if not (0 <= x < w and 0 <= y < h):
                return True                  # 지도 밖은 안 본다
            owner = int(gm.owner[y * w + x])
            if owner == target_pid:
                return True
            return loose and owner < 0       # hard 이상만 빈 땅을 허용한다

        for r in (radius, radius // 2):
            if r <= 0:
                continue
            x0, x1, y0, y1 = cx - r, cx + r, cy - r, cy + r
            for x in range(x0, x1 + 1):      # 위·아래 변
                if not ok(x, y0) or not ok(x, y1):
                    return False
            for y in range(y0 + 1, y1):      # 좌·우 변(모서리 제외)
                if not ok(x0, y) or not ok(x1, y):
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
