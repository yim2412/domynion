"""공격 — openfront 의 `attackLogic()` 과 `AttackExecution` 그대로.

v0.1 과 **정반대로 바뀐 것 두 가지**를 먼저 적어 둔다. 되돌리지 말 것:

1. **프론티어는 FIFO 가 아니라 우선순위 힙이다.** 싼 지형과 내 영토에 많이 접한 칸을
   먼저 먹는다. v0.1 은 "막힌 칸을 큐 앞에 되돌려" 산을 못 피하게 했는데, 원본은
   반대로 **비켜 갈 수 있게** 만들어 뒀다.
2. **예산과 병력이 다른 축이다.** tick 마다 `attackTilesPerTick()` 으로 예산을 받고,
   칸마다 `tilesPerTickUsed` 만큼 예산을 쓰고 `attackerTroopLoss` 만큼 병력을 쓴다.
   v0.1 은 병력 하나로 둘 다 했다.

원본: `Config.ts :: attackLogic() / attackTilesPerTick()`,
      `AttackExecution.ts :: tick() / addNeighbors()`
"""

from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field

from . import constants as C
from .constants import Terrain
from .gamemap import GameMap, TileRef
from .state import PlayerState


def within(value: float, lo: float, hi: float) -> float:
    """원본 `Util.ts :: within`. 이름을 그대로 둬서 공식을 눈으로 대조할 수 있게."""
    return min(max(value, lo), hi)


def sigmoid(value: float, decay_rate: float, midpoint: float) -> float:
    return 1.0 / (1.0 + math.exp(-decay_rate * (value - midpoint)))


@dataclass
class AttackResult:
    attacker_loss: float
    defender_loss: float
    tiles_used: float


def attack_logic(gmap: GameMap, tile: TileRef, attack_troops: float,
                 attacker: PlayerState, defender: PlayerState | None,
                 defender_tiles: int, attacker_tiles: int,
                 defense_post: bool = False, defender_traitor: bool = False,
                 same_team_disconnected: bool = False) -> AttackResult:
    """한 칸을 먹을 때의 손실과 예산 소모. `defender is None` 이면 중립이다.

    중립과 플레이어는 **완전히 다른 분기**다. 하나로 합치지 말 것 — 원본이 그렇게
    나눠 놓았고, 중립 쪽은 수비 병력이라는 개념 자체가 없다."""
    terrain = gmap.terrain_at(tile)
    mag = C.TERRAIN_MAG[terrain]
    speed = C.TERRAIN_SPEED[terrain]

    # 방어초소는 **수비자 것일 때만** 걸린다. 사거리 30 안에 하나라도 있으면
    # 방어 ×5, 속도 ×3 — 원본에서 가장 큰 단일 수정자다.
    if defense_post:
        mag *= C.DEFENSE_POST_DEFENSE_BONUS
        speed *= C.DEFENSE_POST_SPEED_BONUS

    # 낙진은 P5 에서 이 자리에 들어간다.

    if defender is None:
        div = C.NEUTRAL_LOSS_DIV_BOT if attacker.is_bot else C.NEUTRAL_LOSS_DIV_HUMAN
        return AttackResult(
            attacker_loss=mag / div,
            defender_loss=0.0,
            tiles_used=within(
                C.NEUTRAL_TILES_USED_NUM * max(C.NEUTRAL_TILES_USED_SPEED_FLOOR, speed)
                / max(attack_troops, 1e-9),
                *C.NEUTRAL_TILES_USED_CLAMP),
        )

    if attacker.is_bot is False and defender.is_bot:
        mag *= C.ATTACK_VS_BOT_MAG_MULT
    if same_team_disconnected:
        mag = 0.0        # 같은 팀의 연결 끊긴 수비자는 공짜로 넘어온다

    defense_sig = 1.0 - sigmoid(defender_tiles,
                                C.DEFENSE_DEBUFF_DECAY_RATE,
                                C.DEFENSE_DEBUFF_MIDPOINT)
    large_defender = C.DEFENDER_DEBUFF_FLOOR + C.DEFENDER_DEBUFF_SPAN * defense_sig

    large_attack_bonus = 1.0
    large_speed_bonus = 1.0
    if attacker_tiles > C.LARGE_PLAYER_TILES:
        ratio = C.LARGE_PLAYER_TILES / attacker_tiles
        large_attack_bonus = math.sqrt(ratio) ** C.LARGE_ATTACK_BONUS_EXP
        large_speed_bonus = ratio ** C.LARGE_SPEED_BONUS_EXP

    # 수비측은 **타일당 병력**을 잃는다. v0.1 은 이 값을 *비용*에 써서 교착을 만들었지만,
    # 원본은 *손실*에만 쓴다. 같은 수식이 다른 자리에 있는 것이라 헷갈리지 말 것.
    defender_loss = defender.troops / max(defender_tiles, 1)
    traitor_mod = C.TRAITOR_DEFENSE_DEBUFF if defender_traitor else 1.0

    a = (within(defender.troops / max(attack_troops, 1e-9), *C.ATTACKER_LOSS_A_CLAMP)
         * mag * C.ATTACKER_LOSS_A_MULT * large_defender * large_attack_bonus
         * traitor_mod)
    b = (C.ATTACKER_LOSS_B_MULT * defender_loss
         * (mag / C.ATTACKER_LOSS_B_MAG_DIV) * traitor_mod)

    return AttackResult(
        attacker_loss=C.ATTACKER_LOSS_A_WEIGHT * a + C.ATTACKER_LOSS_B_WEIGHT * b,
        defender_loss=defender_loss,
        tiles_used=within(
            defender.troops / (C.TILES_USED_TROOP_MULT * max(attack_troops, 1e-9)),
            *C.TILES_USED_CLAMP) * speed * large_defender * large_speed_bonus
        * (C.TRAITOR_SPEED_DEBUFF if defender_traitor else 1.0),
    )


def tiles_per_tick(attack_troops: float, defender: PlayerState | None,
                   border_size: int) -> float:
    """이번 tick 의 예산. **국경이 넓을수록 많이 번진다** — v0.1 에 없던 축이다."""
    if defender is None:
        return border_size * C.BUDGET_VS_NEUTRAL_BORDER_MULT
    return (within((C.TILES_USED_TROOP_MULT * attack_troops
                    / max(defender.troops, 1e-9)) * C.BUDGET_VS_PLAYER_MULT,
                   *C.BUDGET_VS_PLAYER_CLAMP)
            * border_size * C.BUDGET_VS_PLAYER_BORDER_MULT)


@dataclass
class Attack:
    """진행 중인 공격 하나. `target` 은 타일이 아니라 **소유자**다 (None = 중립)."""

    attacker: int
    target: int | None
    troops: float
    heap: list[tuple[float, TileRef]] = field(default_factory=list)
    seen: set[TileRef] = field(default_factory=set)
    retreated: bool = False

    @classmethod
    def launch(cls, gmap: GameMap, attacker: int, target: int | None,
               troops: float, rng: random.Random, tick: int = 0) -> "Attack | None":
        """내 국경에서 target 소유 타일에 붙는다. 붙을 곳이 없으면 None."""
        atk = cls(attacker=attacker, target=target, troops=troops)
        want = -1 if target is None else target
        mine = gmap.owned_refs(attacker)
        for t in mine.tolist():
            for n in gmap.neighbors(t):
                if gmap.owner[n] == want and gmap.passable(n) and n not in atk.seen:
                    atk._push(gmap, n, rng, tick)
        return atk if atk.heap else None

    # --- 힙 ---------------------------------------------------------------

    def _push(self, gmap: GameMap, tile: TileRef,
              rng: random.Random, tick: int) -> None:
        """`AttackExecution.addNeighbors()` 의 우선순위 공식 그대로.

            priority = (rand(0,7) + 10) × (1 − 내이웃수 × 0.5 + mag/2) + 현재tick

        낮은 값을 먼저 꺼낸다. `+ tick` 이 있어서 나중에 들어온 칸은 뒤로 밀린다 —
        완전한 최단경로가 아니라 FIFO 성질을 일부 남긴 형태다."""
        self.seen.add(tile)
        owned_by_me = sum(1 for n in gmap.neighbors(tile)
                          if gmap.owner[n] == self.attacker)
        terrain = gmap.terrain_at(tile)
        mag = C.PRIORITY_MAG.get(terrain, 0.0)
        priority = ((rng.randrange(C.PRIORITY_NOISE_MAX) + C.PRIORITY_BASE)
                    * (1.0 - owned_by_me * C.PRIORITY_NEIGHBOR_WEIGHT + mag / 2.0)
                    + tick)
        heapq.heappush(self.heap, (priority, tile))

    @property
    def border_size(self) -> int:
        return len(self.heap)

    @property
    def finished(self) -> bool:
        return self.retreated or not self.heap or self.troops < C.ATTACK_MIN_TROOPS

    # --- 진행 -------------------------------------------------------------

    def step(self, gmap: GameMap, attacker: PlayerState,
             defender: PlayerState | None, defender_tiles: int,
             attacker_tiles: int, rng: random.Random,
             tick: int, defense_posts: "object | None" = None,
             defender_traitor: bool = False) -> list[TileRef]:
        """`AttackExecution.tick()`. 이번 tick 에 정복한 칸들을 돌려준다."""
        want = -1 if self.target is None else self.target
        budget = tiles_per_tick(
            self.troops, defender,
            self.border_size + rng.randrange(C.BUDGET_BORDER_NOISE_MAX))
        taken: list[TileRef] = []

        while budget > 0:
            if self.troops < C.ATTACK_MIN_TROOPS:
                self.troops = 0.0        # 소멸 — 퇴각이 아니라서 병력이 안 돌아온다
                self.heap.clear()
                return taken
            if not self.heap:
                self.retreated = True    # 퇴각 — 남은 병력은 엔진이 본국에 돌려준다
                return taken

            _, tile = heapq.heappop(self.heap)

            # 큐에 들어간 뒤 상황이 바뀌었을 수 있다. 원본과 같이 **재큐하지 않고
            # 버린다** — 되돌리면 부대가 같은 칸에서 영원히 맴돈다.
            if gmap.owner[tile] != want or not gmap.passable(tile):
                continue
            if not any(gmap.owner[n] == self.attacker for n in gmap.neighbors(tile)):
                continue                 # 내 영토에 더 이상 안 접한다

            for n in gmap.neighbors(tile):
                if n not in self.seen and gmap.owner[n] == want and gmap.passable(n):
                    self._push(gmap, n, rng, tick)

            guarded = (defense_posts is not None
                       and defender is not None
                       and defense_posts.covers(gmap, tile, defender.pid))
            r = attack_logic(gmap, tile, self.troops, attacker, defender,
                             defender_tiles, attacker_tiles, defense_post=guarded,
                             defender_traitor=defender_traitor)
            budget -= r.tiles_used
            self.troops -= r.attacker_loss
            if defender is not None:
                defender.troops = max(0.0, defender.troops - r.defender_loss)
            gmap.owner[tile] = self.attacker
            taken.append(tile)

        return taken
