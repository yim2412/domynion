"""플레이어 상태와 증강이 반영된 파생 수치.

액션·AI·UI 는 원시 상수를 직접 읽지 않고 **여기 계산 결과만** 참조한다. 그래야
증강 계수가 한 군데서만 적용되고, "이 상황에서 증강이 먹었나"를 의심할 일이 없다.

영토 수는 여기 두지 않는다. 소유는 타일이 갖고 있고(`Tile.owner`), 그것을 복제해
두면 반드시 어긋난다 — 확장이 초당 수십 칸씩 일어나므로 동기화 지점이 너무 많다.
대신 `GameState` 가 증분으로 세어 넘겨 준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as C
from .augments import Modifiers
from .constants import Terrain
from .gamemap import Coord


@dataclass
class PlayerState:
    pid: int
    name: str
    is_ai: bool = False
    troops: float = C.TROOPS_START
    attack_ratio: float = C.DEFAULT_ATTACK_RATIO   # 공격에 투입할 병력 비율
    start: Coord | None = None
    alive: bool = True

    augments: dict[str, int] = field(default_factory=dict)   # key -> level
    pending_picks: int = 0        # 아직 고르지 않은 증강 수

    # --- 증강 계수 --------------------------------------------------------

    @property
    def mods(self) -> Modifiers:
        return Modifiers.from_augments(self.augments)

    @property
    def naval_range(self) -> int:
        return int(self.mods.get("naval_range"))

    # --- 병력 -------------------------------------------------------------

    def max_troops(self, tile_count: int) -> float:
        """병력 상한. 영토가 곧 인구 부양력이다."""
        base = C.TROOPS_CAP_BASE + tile_count * C.TROOPS_CAP_PER_TILE
        return base * self.mods.mult("troops_cap_pct")

    def growth_per_sec(self, tile_count: int) -> float:
        """로지스틱 성장 — 상한에 가까울수록 느려진다. 그래서 큰 나라라고 해서
        무한히 병력이 불지 않고, 쓰지 않고 쌓아 두는 전략에 천장이 생긴다."""
        cap = self.max_troops(tile_count)
        if self.troops >= cap:
            return 0.0
        headroom = 1.0 - self.troops / cap
        raw = max(C.TROOPS_GROWTH_FLOOR, cap * C.TROOPS_GROWTH_RATE * headroom)
        return raw * self.mods.mult("troops_growth_pct")

    def fill_ratio(self, tile_count: int) -> float:
        """병력이 상한의 몇 %인가(0~1). 방어 비용에 실리는 값이라 '얼마나 두껍게
        지키는가'가 된다.

        타일당 병력(절대값)을 쓰면 안 된다 — 그 값은 상한까지 커져 비용이 십수 배로
        뛰고, 서로를 아무도 못 뚫어 판이 교착된다."""
        cap = self.max_troops(tile_count)
        return min(1.0, self.troops / cap) if cap > 0 else 0.0

    def attack_troops(self) -> float:
        return self.troops * self.attack_ratio

    # --- 정복 계수 --------------------------------------------------------

    def cost_mult(self, terrain: Terrain, vs_player: bool) -> float:
        """이 플레이어가 이 지형을 칠 때의 비용 배율.

        지형 특화와 대상별 할인은 **더해서** 한 번에 적용한다. 각각 곱하면 두 장을
        겹쳤을 때 배율이 0 에 붙어 공짜 확장이 된다."""
        m = self.mods
        pct = m.get("cost_vs_player_pct" if vs_player else "cost_vs_neutral_pct")
        if terrain in C.HIGHLAND:
            pct += m.get("cost_highland_pct")
        elif terrain in C.WOODLAND:
            pct += m.get("cost_woodland_pct")
        return max(0.2, 1.0 + pct)

    def expand_speed_mult(self) -> float:
        return self.mods.mult("expand_speed_pct")

    def defense_mult(self) -> float:
        """남이 내 땅을 먹을 때 붙는 배율."""
        return self.mods.mult("defense_pct")

    def defender_loss_mult(self) -> float:
        """내가 뺏을 때 상대가 잃는 병력에 붙는 배율."""
        return self.mods.mult("defender_loss_pct")
