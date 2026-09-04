"""플레이어 상태 — openfront 의 병력 공식 그대로.

원본 `Config.ts :: maxTroops() / troopIncreaseRate()`.

**성장은 초당이 아니라 tick 당이다.** `troopIncreaseRate()` 가 매 tick 불려서 그 값을
그대로 더한다. 초당으로 바꿔 두면 tick 길이를 바꿀 때 조용히 어긋난다.

영토 수는 여기 두지 않는다. 소유는 지도 배열이 갖고 있고 엔진이 증분으로 세어 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as C
from .gamemap import TileRef
from .relations import Relations
from .units import UnitStore


@dataclass
class PlayerState:
    pid: int
    name: str
    is_bot: bool = False
    # "bot" · "nation" · "human". 원본은 셋을 구분하고 배율이 전부 다르다 —
    # 봇은 난이도와 무관하게 늘 약하고, Nation 만 난이도를 탄다.
    kind: str = "human"
    difficulty: str = "medium"
    troops: float = 0.0
    gold: int = 0                      # P2 에서 쓴다
    start: TileRef | None = None
    alive: bool = True

    units: UnitStore = field(default_factory=UnitStore)

    # 사람이 슬라이더로 조절하는 값. 기본은 원본의 attackAmount().
    attack_ratio: float | None = None

    # 내가 남들을 어떻게 보는가. **한 방향**이다 — 상대가 나를 보는 눈은 상대에게 있다.
    relations: Relations = field(default_factory=Relations)

    # 쿨다운 기록. **초기값이 −1 이다**(`PlayerImpl.lastDeleteUnitTick = -1`) —
    # 0 으로 두면 판 시작 직후에 한 번 공짜로 쓸 수 있어 원본과 어긋난다.
    last_delete_unit_tick: int = -1
    last_embargo_all_tick: int = -1

    # 이 플레이어가 공격을 **보낸** 횟수. 원본은 통계
    # (`stats().attacks[ATTACK_INDEX_SENT]`)에서 읽는데, 우리는 통계 계층이 없어
    # 여기 센다. 쓰이는 곳은 하나뿐이다 — **한 번도 안 친 사람은 정복당해도
    # 골드를 뺏기지 않는다**(`GameImpl.conquerPlayer` 의 `skipGoldTransfer`).
    # 시작 골드를 켠 판에서 가만히 있는 사람을 털어 가는 것을 막는 장치다.
    attacks_sent: int = 0

    # 고른 증강. key -> 레벨(1~3). **사람만 채운다**(`docs/design.md` 개정).
    augments: dict[str, int] = field(default_factory=dict)
    # 합산된 계수 캐시. `None` 이면 다음에 쓸 때 만든다 — 증강을 고를 때마다
    # 버린다. ⚠ **매 tick 다시 만들면 안 된다**: 병력 성장·정복 비용이 tick 마다
    # 이걸 읽으므로, 캐시가 없으면 카드 열 장을 매 tick 다시 더하게 된다.
    mods: object | None = None

    @property
    def city_levels(self) -> int:
        return self.units.city_levels()

    def __post_init__(self) -> None:
        if self.is_bot and self.kind == "human":
            self.kind = "bot"          # 옛 호출부 호환: is_bot 만 주면 봇으로 본다
        self.is_bot = self.kind == "bot"
        if self.troops <= 0.0:
            self.troops = {
                "bot": C.START_TROOPS_BOT,
                "nation": C.NATION_START_TROOPS.get(self.difficulty,
                                                    C.START_TROOPS_HUMAN),
            }.get(self.kind, C.START_TROOPS_HUMAN)
        if self.attack_ratio is None:
            self.attack_ratio = (C.ATTACK_RATIO_BOT if self.is_bot
                                 else C.ATTACK_RATIO_HUMAN)

    # --- 병력 -------------------------------------------------------------

    def mult(self, field: str) -> float:
        """증강 배율. 증강이 없으면 **정확히 1.0** 이라 원본 공식이 그대로 남는다.

        ⚠ **원본 공식에 손대지 않고 결과에 곱한다.** 계수를 공식 안쪽에 끼워
        넣으면 openfront 규칙과 우리 계층이 섞여, 나중에 원본과 대조할 때 어느
        쪽이 틀렸는지 못 가른다(§ 문서의 이식 원칙).
        """
        if not self.augments:
            return 1.0          # 사람이 아니거나 아직 안 골랐다 — 흔한 경우다
        if self.mods is None:
            from .augments import Modifiers
            self.mods = Modifiers.from_augments(self.augments)
        return self.mods.mult(field)

    def max_troops(self, tile_count: int) -> float:
        """`2 × (타일^0.6 × 1000 + 50000) + Σ도시레벨 × 250000`, 봇은 ÷3.

        상수항 50000 이 크다는 점을 기억할 것 — 작은 지도에서는 이게 지배해서
        영토를 넓혀도 상한이 거의 안 오른다(계획서 4.5절)."""
        base = C.MAX_TROOPS_MULT * (
            tile_count ** C.MAX_TROOPS_TILE_EXP * C.MAX_TROOPS_TILE_MULT
            + C.MAX_TROOPS_BASE
        ) + self.units.city_levels() * C.CITY_TROOP_INCREASE
        if self.kind == "bot":
            return base / C.BOT_MAX_TROOPS_DIV
        if self.kind == "nation":
            return base * C.NATION_MAX_TROOPS_MULT.get(self.difficulty, 1.0)
        return base

    def troop_increase(self, tile_count: int) -> float:
        """이번 **tick** 에 늘어날 병력. 상한을 넘지 않게 잘라서 돌려준다.

        증가량이 상한이 아니라 **현재 병력**에 붙는다(`병력^0.73`). 그래서 병력이
        적을 때는 회복이 느리고, 많을수록 빨라지다가 상한 근처에서 다시 눌린다."""
        cap = self.max_troops(tile_count)
        add = C.TROOP_GROWTH_FLAT + self.troops ** C.TROOP_GROWTH_EXP / C.TROOP_GROWTH_DIV
        add *= 1.0 - self.troops / cap if cap > 0 else 0.0
        if self.kind == "bot":
            add *= C.BOT_GROWTH_MULT
        elif self.kind == "nation":
            add *= C.NATION_GROWTH_MULT.get(self.difficulty, 1.0)
        return min(self.troops + add, cap) - self.troops

    def attack_troops(self) -> float:
        return self.troops * self.attack_ratio
