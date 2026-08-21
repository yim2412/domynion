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


@dataclass
class PlayerState:
    pid: int
    name: str
    is_bot: bool = False
    troops: float = 0.0
    gold: int = 0                      # P2 에서 쓴다
    start: TileRef | None = None
    alive: bool = True

    # 도시 레벨 합. P2 에서 실제 유닛으로 바뀐다 — 지금은 상한 공식이 이 값을 읽는
    # 자리만 열어 둔다(원본 공식에 들어 있으므로 빼면 공식이 달라진다).
    city_levels: int = 0

    # 사람이 슬라이더로 조절하는 값. 기본은 원본의 attackAmount().
    attack_ratio: float | None = None

    augments: dict[str, int] = field(default_factory=dict)   # P7 까지 미사용

    def __post_init__(self) -> None:
        if self.troops <= 0.0:
            self.troops = (C.START_TROOPS_BOT if self.is_bot
                           else C.START_TROOPS_HUMAN)
        if self.attack_ratio is None:
            self.attack_ratio = (C.ATTACK_RATIO_BOT if self.is_bot
                                 else C.ATTACK_RATIO_HUMAN)

    # --- 병력 -------------------------------------------------------------

    def max_troops(self, tile_count: int) -> float:
        """`2 × (타일^0.6 × 1000 + 50000) + Σ도시레벨 × 250000`, 봇은 ÷3.

        상수항 50000 이 크다는 점을 기억할 것 — 작은 지도에서는 이게 지배해서
        영토를 넓혀도 상한이 거의 안 오른다(계획서 4.5절)."""
        base = C.MAX_TROOPS_MULT * (
            tile_count ** C.MAX_TROOPS_TILE_EXP * C.MAX_TROOPS_TILE_MULT
            + C.MAX_TROOPS_BASE
        ) + self.city_levels * C.CITY_TROOP_INCREASE
        return base / C.BOT_MAX_TROOPS_DIV if self.is_bot else base

    def troop_increase(self, tile_count: int) -> float:
        """이번 **tick** 에 늘어날 병력. 상한을 넘지 않게 잘라서 돌려준다.

        증가량이 상한이 아니라 **현재 병력**에 붙는다(`병력^0.73`). 그래서 병력이
        적을 때는 회복이 느리고, 많을수록 빨라지다가 상한 근처에서 다시 눌린다."""
        cap = self.max_troops(tile_count)
        add = C.TROOP_GROWTH_FLAT + self.troops ** C.TROOP_GROWTH_EXP / C.TROOP_GROWTH_DIV
        add *= 1.0 - self.troops / cap if cap > 0 else 0.0
        if self.is_bot:
            add *= C.BOT_GROWTH_MULT
        return min(self.troops + add, cap) - self.troops

    def attack_troops(self) -> float:
        return self.troops * self.attack_ratio
