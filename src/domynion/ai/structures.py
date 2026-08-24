"""Nation 봇의 건설·업그레이드 — 원본 `NationStructureBehavior` 이식.

**이 파일이 생기기 전 우리 AI 는 업그레이드를 아예 하지 않았다.** 종류를 가중
무작위(`BUILD_WEIGHT`)로 고르고 개수를 영토 대비 상한(`STRUCTURE_CAP_PER_TILES`)으로
막고 있었는데, 원본은 둘 다 쓰지 않는다. 원본의 판단은 이렇다:

1. **도시 수가 모든 것의 기준이다.** 항구·공장·SAM·사일로는 전부 "도시 몇 채당
   몇 개"로 목표치가 정해지고, 그 목표에 못 미칠 때만 짓는다. 목표를 다 채우면
   남은 돈은 도시로 간다 — 그래서 도시가 먼저 늘고 나머지가 따라 온다.
2. **건물 밀도가 문턱을 넘으면 새로 짓는 대신 올린다**(`UPGRADE_DENSITY_THRESHOLD`).
   못 올렸으면 **새로 짓지도 않는다** — SAM 처럼 건설이 느린 것이 줄줄이 서는 것을
   막는 장치다.
3. **체감 비용**(`_perceived_cost`). 핵 살 돈이 모이기 전까지는 이미 가진 수만큼
   다음 건물이 비싸 보이게 해서, 건물에 돈을 다 쓰고 핵을 영영 못 사는 것을 막는다.
4. **방어초소는 건설 순서에 없다.** 오직 "육상 공격을 받는 중"일 때만 전선 근처에
   짓는다(`_defense_post`). 그래서 초소가 골드를 무한히 빨아들이는 일이 원본에는
   구조적으로 없다 — 우리가 상한 표로 막고 있던 문제가 여기서 사라진다.

값은 전부 원본 `src/core/execution/nation/NationStructureBehavior.ts` 를 열어
확인한 것이다(§5.30 의 클론 명령).

## 일부러 안 옮긴 것 — 이름만 적어 두면 다음 세션이 "확인했다"고 오해한다

| 원본 | 왜 안 옮겼나 |
|---|---|
| `hasHighStartingGold` · `HIGH_GOLD_STRUCTURE_COOLDOWN_TICKS` | 우리 엔진에 **시작 골드 개념이 없다**. 항상 거짓인 분기가 되어 죽은 코드만 남는다 |
| `isOnStructureCooldown` | 위 분기가 유일한 발동 조건이다 |
| `isInPostSaveUpBlockedPhase` · `TEAM_POST_SAVE_UP_PHASE_TICKS` | 팀 모드가 없다 |
| `MIN_PORT_WATER_COMPONENT_SIZE`(3000) | **우리 지형에는 호수가 없다.** `Terrain` 은 OCEAN/PLAINS/HIGHLAND/MOUNTAIN/IMPASSABLE 뿐이고 `is_shore` 가 OCEAN 이웃만 본다. 원본도 *"Ocean is always considered shared"* 로 바다는 크기 검사를 건너뛰므로, 우리 쪽에서는 이 상수가 걸릴 경로 자체가 없다 |
| `isUnitDisabled` 분기 전체 | 종류를 끄는 설정이 없다 |
| `citiesDisabled` · `TILES_PER_CITY_EQUIVALENT`(2000) | 위와 같다. 도시는 항상 켜져 있다 |
| 종류별 자리 값 함수 5개 + 철도 연결성 점수 | **아직 안 옮겼다(보류).** 600줄이고 철도 클러스터까지 얽힌다. 지금은 `find_spot` 이 가까운 빈자리를 고른다 |
| `sampleTilesNearFront` 의 3단 폴백 | 간략화했다. 전선 타일을 그대로 `find_spot` 에 넘긴다 |

⚠ 위 표의 마지막 둘은 **보류지 완료가 아니다.** `docs/openfront-port.md` §7 참조.
"""

from __future__ import annotations

import math
import random

import numpy as np

from ..core import constants as C
from ..core.buildings import euclid_sq
from ..core.naval import shoreline_tiles
from ..core.nukes import NUKE_MAGNITUDES, sam_range
from ..core.units import STRUCTURES, UNIT_INFO, Unit, UnitType

# --- 원본 상수 --------------------------------------------------------------

# `SAM_RATIO_BY_DIFFICULTY` — 도시 한 채당 SAM 몇 기
SAM_RATIO_BY_DIFFICULTY: dict[str, float] = {
    "easy": 0.15,
    "medium": 0.2,
    "hard": 0.25,
    "impossible": 0.3,
}

# `getStructureRatios` — (도시당 비율, 보유 하나당 체감 비용 증가율).
# SAM 의 비율만 난이도를 타므로 `None` 을 두고 `_ratio_for` 가 채운다.
STRUCTURE_RATIOS: dict[UnitType, tuple[float | None, float]] = {
    UnitType.PORT: (0.75, 1.0),
    UnitType.FACTORY: (0.75, 1.0),
    UnitType.SAM_LAUNCHER: (None, 0.3),
    UnitType.MISSILE_SILO: (0.2, 1.0),
}

# 비율 표에 없는 종류의 체감 비용 증가율 (`config?.perceivedCostIncreasePerOwned ?? 0.1`)
DEFAULT_COST_INCREASE_PER_OWNED = 0.1

# 도시는 비율 표에 없다 — 항상 마지막에 지어지고, 증가율만 따로 정해져 있다
CITY_PERCEIVED_COST_INCREASE_PER_OWNED = 1.0

# 해안이 있으면 공장 비율을 이만큼으로 줄인다 — 바다 수입(항구)이 더 낫기 때문
FACTORY_COASTAL_RATIO_MULTIPLIER = 0.33

MAX_MISSILE_SILOS = 3

# **첫 사일로만** 비율을 높게 쓴다. 0.2 로는 도시 5채를 지어야 첫 핵이 나오는데,
# 그러면 판이 절반 지나도록 아무도 핵을 안 쏜다.
FIRST_MISSILE_SILO_RATIO = 0.4

# 영토 대비 건물이 이보다 빽빽하면 **새로 짓지 말고 올린다**. 레벨은 세지 않는다
# (원본 주석: "ignoring levels for structures").
UPGRADE_DENSITY_THRESHOLD = 1 / 1500

# 지도가 이보다 빽빽하면 첫 건물을 도시가 아니라 항구(내륙이면 공장)로 —
# 수입을 일찍 시작하려는 것이다. 원본 주석은 200명 이상 사설방을 겨냥한다고 적었다.
#
# 실측(2026-08-24, 나라 72): map=0.000111(안 걸림) · map4x=0.000456(걸림) ·
# map16x=0.001916(걸림). **우리 기본 크기(map)에서는 안 걸린다** — 작은 지도에서만
# 성격이 바뀐다.
HIGH_NATION_DENSITY_THRESHOLD = 1 / 7500

# 들어오는 육상 공격 병력이 내 병력의 이 비율 미만이면 초소를 짓지 않는다
UNDER_ATTACK_THREAT_RATIO = 0.35

# hard 이상: 위협 비율 이만큼마다 초소를 하나씩 더 허용한다
# (0.4 → 0~40% 에 1기, 40~80% 에 2기, …)
DEFENSE_POST_RATIO_PER_POST = 0.4

# `findBestStructureToUpgrade` — 점수를 안 보고 그냥 아무거나 고를 확률(%).
# easy 가 70 이라는 것이 핵심이다: 쉬운 AI 는 SAM 밖의 건물도 태연히 올린다.
RANDOM_UPGRADE_CHANCE: dict[str, int] = {
    "easy": 70, "medium": 40, "hard": 25, "impossible": 10,
}

# SAM 이 덮는 건물에 붙는 점수 (`score += 10`, 레벨 2 이상은 레벨당 +7.5)
SAM_PROTECTION_SCORE = 10.0
SAM_LEVEL_BONUS = 7.5

# 동점을 흩는 잡음 — `random.nextInt(0, 5)` 는 **5 를 포함하지 않는다**(0~4)
UPGRADE_SCORE_JITTER = 5

# 전선 근처에서 자리를 몇 개까지 시도하는가 (`sampleTilesNearFront(front, 25, …)`)
DEFENSE_POST_SAMPLES = 25

# 원본 건설 순서. **도시는 여기 없다** — 이 넷을 다 채운 뒤에 도시를 짓는다.
# **방어초소도 여기 없다** — 초소는 공격받는 중에만 지어진다.
BUILD_ORDER: tuple[UnitType, ...] = (
    UnitType.PORT, UnitType.FACTORY, UnitType.SAM_LAUNCHER, UnitType.MISSILE_SILO,
)


def border_spacing() -> int:
    """`spacingConstants().borderSpacing` = 원자탄의 바깥 반경.

    초소를 전선에서 얼마나 떨어뜨릴지, 초소끼리 얼마나 벌릴지가 전부 이 값에서
    나온다. **핵 반경이 지도 규모에 맞춰져 있으므로 여기도 자동으로 따라온다** —
    §5.10 에서 한 번 데인 자리라 상수를 새로 두지 않고 그 값을 읽는다."""
    return NUKE_MAGNITUDES[UnitType.ATOM_BOMB][1]


class NationStructureBehavior:
    """플레이어 한 명의 건설·업그레이드 판단. `NationBot` 이 하나씩 들고 있다."""

    __slots__ = ("pid", "rng", "difficulty", "placements", "_land_tiles")

    def __init__(self, pid: int, rng: random.Random, difficulty: str = "medium"):
        self.pid = pid
        self.rng = rng
        self.difficulty = difficulty
        # `placementsCount` — 초소는 **첫 건물이 될 수 없다**. 이 카운터가 그것만 한다.
        self.placements = 0
        self._land_tiles: int | None = None

    # --- 진입점 -----------------------------------------------------------

    def handle(self, st) -> bool:
        """`handleStructures` — 지었거나 올렸으면 True.

        초소는 **정상 경로 바깥**이다: `placements` 를 올리지 않고, 첫 건물이 될 수
        없다. 그리고 위협 문턱을 넘은 상태에서는 초소를 못 지었더라도(돈이 없거나
        자리가 없거나) **다른 건물도 짓지 않는다** — 공격받는 중에 도시를 올리는
        AI 가 되지 않게 하는 장치다."""
        if self.placements > 0:
            if self._defense_post(st):
                return True
            if self._defense_post_needed(st):
                return False
        built = self._place(st)
        if built:
            self.placements += 1
        return built

    # --- 건설 순서 ---------------------------------------------------------

    def _place(self, st) -> bool:
        """`doHandleStructures` — 순서대로 하나만 처리하고 즉시 끝낸다."""
        p = st.players[self.pid]
        # ⚠ `owned()` 는 **레벨 합**이다(개수가 아니다). 원본 `unitsOwned` 가 그렇고,
        # 모든 비율이 이 값 위에서 돈다 — 도시를 올리면 목표치도 같이 오른다.
        city_count = p.units.owned(UnitType.CITY)
        coastal = shoreline_tiles(st.gmap, self.pid)
        has_coastal = bool(len(coastal))

        # 빽빽한 지도에서는 첫 건물이 도시가 아니라 항구다
        if city_count == 0 and self._high_nation_density(st):
            first = UnitType.PORT if has_coastal else UnitType.FACTORY
            if self._maybe_spawn(st, first, coastal):
                return True

        for utype in BUILD_ORDER:
            if utype is UnitType.PORT and not has_coastal:
                continue
            if self._should_build(st, utype, city_count, has_coastal):
                if self._maybe_spawn(st, utype, coastal):
                    return True

        return self._maybe_spawn(st, UnitType.CITY, coastal)

    def _high_nation_density(self, st) -> bool:
        """`isHighNationDensity` — 나라 수 / 육지 칸.

        ⚠ **부족(봇)은 세지 않는다.** 원본 `game.nations()` 가 나라만 돌려준다 —
        봇 400 을 같이 세면 어느 지도에서나 문턱을 넘어 분기가 늘 켜진 채가 된다."""
        if self._land_tiles is None:
            self._land_tiles = int(st.gmap.passable_mask().sum())
        if self._land_tiles <= 0:
            return False
        nations = sum(1 for q in st.players.values() if q.kind == "nation")
        return nations / self._land_tiles > HIGH_NATION_DENSITY_THRESHOLD

    def _ratio_for(self, utype: UnitType) -> float | None:
        cfg = STRUCTURE_RATIOS.get(utype)
        if cfg is None:
            return None
        ratio = cfg[0]
        if ratio is None:                      # SAM 만 난이도를 탄다
            ratio = SAM_RATIO_BY_DIFFICULTY[self.difficulty]
        return ratio

    def _should_build(self, st, utype: UnitType,
                      city_count: int, has_coastal: bool) -> bool:
        """`shouldBuildStructure` — 보유량이 목표치에 못 미치면 True.

        도시는 이 표에 없으므로 항상 False 다. 도시는 `_place` 의 마지막 줄에서
        **목표치 없이** 지어진다 — 그게 원본이 도시를 우선하는 방식이다."""
        ratio = self._ratio_for(utype)
        if ratio is None:
            return False
        if utype is UnitType.FACTORY and has_coastal:
            ratio *= FACTORY_COASTAL_RATIO_MULTIPLIER

        owned = st.players[self.pid].units.owned(utype)
        if utype is UnitType.MISSILE_SILO:
            if owned >= MAX_MISSILE_SILOS:
                return False
            if owned == 0:
                ratio = FIRST_MISSILE_SILO_RATIO

        return owned < math.floor(city_count * ratio)

    # --- 짓기 / 올리기 -----------------------------------------------------

    def _maybe_spawn(self, st, utype: UnitType, coastal) -> bool:
        """`maybeSpawnStructure` — **여기가 업그레이드로 갈리는 자리다.**"""
        p = st.players[self.pid]
        if p.gold < self._perceived_cost(st, utype):
            return False

        structures = p.units.of(utype)
        if (self._density(st) > UPGRADE_DENSITY_THRESHOLD
                and UNIT_INFO[utype].upgradable):
            if self._maybe_upgrade(st, structures):
                return True
            # 빽빽한데 못 올렸다면(전부 건설 중이거나 돈이 모자라거나) **새로 짓지도
            # 않는다.** 그냥 기다린다 — SAM 처럼 건설이 긴 것이 줄줄이 서지 않게.
            if structures:
                return False
            # 단 이 종류가 하나도 없으면 첫 채는 짓는다. 원본 주석: 작은 섬에
            # 갇혀 밀도가 늘 높은 나라는 이게 없으면 아무것도 못 짓는다.

        near = self._spawn_near(st, utype, coastal)
        if near is None:
            return False
        return st.build(self.pid, utype, near) is not None

    def _spawn_near(self, st, utype: UnitType, coastal):
        """자리 탐색의 **출발점**만 고른다. 실제 칸은 `find_spot` 이 정한다.

        ⚠ 항구만 해안에서 출발해야 한다. 내륙 칸에서 출발하면 `find_spot` 의 탐색
        반경(40) 안에 해안이 없어 조용히 실패하고, **무역선이 한 척도 안 뜬다.**
        원본이 항구만 `randCoastalTileArray` 를 쓰는 것과 같은 이유다."""
        if utype is UnitType.PORT:
            refs = coastal
        else:
            refs = st.gmap.owned_refs(self.pid)
        if not len(refs):
            return None
        return int(refs[self.rng.randrange(len(refs))])

    def _cost(self, st, utype: UnitType) -> int:
        p = st.players[self.pid]
        if utype is UnitType.MIRV:
            # MIRV 만 보유량이 아니라 **판 전체 발사 수**로 값이 오른다
            return p.units.cost(utype, extra=st.mirvs_launched)
        return p.units.cost(utype)

    def _perceived_cost(self, st, utype: UnitType) -> int:
        """`getPerceivedCost` — 핵 살 돈이 모이기 전까지 건물이 비싸 **보이게** 한다.

        실제 값은 안 바뀐다. 목표치(`_save_up_target`)를 이미 넘겼으면 그대로다."""
        real = self._cost(st, utype)
        target = self._save_up_target(st)
        p = st.players[self.pid]
        if target == 0 or p.gold >= target:
            return real
        if utype is UnitType.CITY:
            inc = CITY_PERCEIVED_COST_INCREASE_PER_OWNED
        else:
            cfg = STRUCTURE_RATIOS.get(utype)
            inc = cfg[1] if cfg is not None else DEFAULT_COST_INCREASE_PER_OWNED
        return math.ceil(real * (1.0 + inc * p.units.owned(utype)))

    def _save_up_target(self, st) -> int:
        """`getSaveUpTarget` — MIRV 한 발 + 수소탄 한 발.

        우리는 종류를 끄는 설정이 없어 원본의 분기 사슬(MIRV→수소→원자→SAM)이
        늘 첫 가지로 간다. 나머지 가지는 옮기지 않았다."""
        return (self._cost(st, UnitType.MIRV)
                + self._cost(st, UnitType.HYDROGEN_BOMB))

    def _density(self, st) -> float:
        """`getTotalStructureDensity` — 건물 **개수** / 내 영토 칸.

        ⚠ 레벨은 안 센다. 여기서 `owned()`(레벨 합)를 쓰면 올릴수록 밀도가 올라가
        스스로를 막는다 — 원본이 일부러 개수로 세는 자리다."""
        tiles = st.tiles(self.pid)
        if tiles <= 0:
            return 0.0
        p = st.players[self.pid]
        n = sum(1 for u in p.units.units if u.active and u.utype in STRUCTURES)
        return n / tiles

    def _maybe_upgrade(self, st, structures: list[Unit]) -> bool:
        if self._density(st) <= UPGRADE_DENSITY_THRESHOLD:
            return False
        if not structures:
            return False
        best = self._best_to_upgrade(st, structures)
        if best is None:
            return False
        return st.upgrade(self.pid, best)

    def _best_to_upgrade(self, st, structures: list[Unit]) -> Unit | None:
        """`findBestStructureToUpgrade` — **SAM 이 덮는 건물을 먼저 올린다.**

        핵 한 발로 날아갈 자리에 레벨을 쌓지 않겠다는 판단이다. 난이도가 낮을수록
        그 판단을 건너뛰고 아무거나 고른다(easy 는 70%)."""
        upgradable = [s for s in structures if st.can_upgrade(self.pid, s)]
        if not upgradable:
            return None

        chance = RANDOM_UPGRADE_CHANCE[self.difficulty]
        if self.rng.randrange(100) < chance:
            return upgradable[self.rng.randrange(len(upgradable))]

        p = st.players[self.pid]
        sams = p.units.of(UnitType.SAM_LAUNCHER)
        scored: list[tuple[float, int, Unit]] = []
        for i, s in enumerate(upgradable):
            score = 0.0
            for sam in sams:
                r = sam_range(sam.level)
                if euclid_sq(st.gmap, s.tile, sam.tile) <= r * r:
                    score += SAM_PROTECTION_SCORE
                    if sam.level > 1:
                        score += (sam.level - 1) * SAM_LEVEL_BONUS
            score += self.rng.randrange(UPGRADE_SCORE_JITTER)
            # 두 번째 자리는 순서를 고정하기 위한 것이다 — `Unit` 은 비교가 안 되고,
            # 동점일 때 정렬이 흔들리면 같은 seed 가 다른 판을 만든다.
            scored.append((score, i, s))

        scored.sort(key=lambda t: (-t[0], t[1]))

        # 절반은 2·3위를 고른다 — 1위만 계속 올리면 한 채만 탑이 된다
        if len(scored) >= 2 and self.rng.randrange(2) == 0:
            idx = self.rng.randrange(1, 3) if len(scored) >= 3 else 1
            return scored[idx][2]
        return scored[0][2]

    # --- 방어초소 ---------------------------------------------------------

    def _land_attacks(self, st) -> list:
        """나를 향해 **육지로** 오는 공격들.

        ⚠ 상륙 공격은 제외한다(`sourceTile() !== null`). 초소는 국경을 넘어오는
        것을 늦추는 물건이라 배로 뒤를 잡힌 상황에는 소용이 없고, 원본도 세지
        않는다. 우리 `Attack` 에 `source_tile` 이 없어 오래 구분이 안 됐다."""
        return [a for a in st.attacks
                if a.target == self.pid and a.source_tile is None
                and not a.retreated]

    def _threat_ratio(self, st) -> float:
        p = st.players[self.pid]
        if p.troops <= 0:
            return 0.0
        atks = self._land_attacks(st)
        if not atks:
            return 0.0
        return sum(a.troops for a in atks) / p.troops

    def _defense_post_needed(self, st) -> bool:
        """`defensePostNeeded` — easy 는 초소를 아예 안 짓는다."""
        if self.difficulty == "easy":
            return False
        return self._threat_ratio(st) >= UNDER_ATTACK_THREAT_RATIO

    def _defense_post(self, st) -> bool:
        """`tryBuildDefensePost` — 전선 근처에 한 기.

        medium 은 부를 때마다 1/2 확률로만 시도하고 총 1기까지. hard 이상은
        위협 비율에 비례해 여러 기를 허용한다."""
        if self.difficulty == "easy":
            return False
        if self.difficulty == "medium" and self.rng.randrange(2) != 0:
            return False

        ratio = self._threat_ratio(st)
        if ratio < UNDER_ATTACK_THREAT_RATIO:
            return False

        if self.difficulty == "medium":
            allowed = 1
        else:
            allowed = math.ceil(ratio / DEFENSE_POST_RATIO_PER_POST)

        front = self._front_tiles(st)
        if front is None or not len(front):
            return False
        if self._posts_near_front(st, front, allowed) >= allowed:
            return False

        p = st.players[self.pid]
        if p.gold < p.units.cost(UnitType.DEFENSE_POST):
            return False

        n = min(DEFENSE_POST_SAMPLES, len(front))
        for _ in range(n):
            near = int(front[self.rng.randrange(len(front))])
            if st.build(self.pid, UnitType.DEFENSE_POST, near) is not None:
                return True
        return False

    def _front_tiles(self, st):
        """`getAttackFrontTiles` — 공격자 영토에 맞닿은 내 국경 칸.

        **numpy 로 편다.** 칸마다 이웃을 보면 영토 17만 칸에서 이 함수 하나가
        시뮬레이션보다 비싸진다 — `border_targets` 가 같은 이유로 그렇게 돼 있다."""
        attackers = {a.attacker for a in self._land_attacks(st)}
        if not attackers:
            return None
        gm = st.gmap
        h, w = gm.height, gm.width
        o = gm.owner.reshape(h, w)
        mine = o == self.pid
        if not mine.any():
            return None
        theirs = np.isin(o, list(attackers))
        front = np.zeros((h, w), dtype=bool)
        front[:, :-1] |= mine[:, :-1] & theirs[:, 1:]
        front[:, 1:] |= mine[:, 1:] & theirs[:, :-1]
        front[:-1, :] |= mine[:-1, :] & theirs[1:, :]
        front[1:, :] |= mine[1:, :] & theirs[:-1, :]
        return np.flatnonzero(front.ravel())

    def _posts_near_front(self, st, front, cap: int) -> int:
        """전선 근처에 이미 선 초소 수. `cap` 에 닿으면 즉시 멈춘다."""
        rng_sq = (border_spacing() * 1.5) ** 2
        count = 0
        for dp in st.players[self.pid].units.of(UnitType.DEFENSE_POST):
            for t in front:
                if euclid_sq(st.gmap, dp.tile, int(t)) <= rng_sq:
                    count += 1
                    if count >= cap:
                        return count
                    break
        return count
