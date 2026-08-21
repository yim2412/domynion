"""공격 부대 — 연속 확장.

타일을 클릭하면 **그 칸이 아니라 그 칸의 소유자 전체**가 대상이 된다. 병력의 일부를
떼어 국경에 붙이면 부대가 프론티어를 따라 번지며 타일을 하나씩 사들이고, 병력이
떨어지는 지점에서 저절로 멈춘다. 한 칸씩 수동으로 편입하는 게 아니다.

프론티어는 BFS 큐다. 그래서 확장이 국경에 접한 곳에서 바깥으로 자라고, 먼 곳이
갑자기 뚫리지 않는다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import constants as C
from .gamemap import Coord, GameMap
from .state import PlayerState


def reach(gmap: GameMap, pos: Coord, naval_range: int) -> list[Coord]:
    """이 칸에서 부대가 다음으로 번질 수 있는 칸들.

    항해술이 없으면 육지 인접 4방향뿐이다. 있으면 그만큼 바다 건너까지 닿는다 —
    그게 이 증강이 하는 일 전부이고, 계수가 아닌 유일한 증강인 이유다."""
    if naval_range <= 0:
        return gmap.neighbors(pos)
    return gmap.within(pos, naval_range + 1)


@dataclass
class Attack:
    """진행 중인 하나의 공격.

    `target` 은 타일이 아니라 **소유자**다. None 이면 중립 지대를 먹는 중이다."""

    attacker: int
    target: int | None
    troops: float
    naval_range: int = 0
    frontier: deque[Coord] = field(default_factory=deque)
    seen: set[Coord] = field(default_factory=set)

    # 확장 속도가 초당 6.3칸이면 20Hz tick 하나에 0.315칸이다. 버리면 느린 부대가
    # 영원히 한 칸도 못 먹는다 — 소수를 누적해서 1을 넘을 때 한 칸 먹는다.
    _carry: float = 0.0

    # 직전 step 에서 이 부대가 쓴 병력. 방어측 손실이 여기 비례하므로 engine 이 읽는다.
    last_spent: float = 0.0

    @classmethod
    def launch(cls, gmap: GameMap, attacker: int, target: int | None,
               troops: float, naval_range: int = 0) -> "Attack | None":
        """국경에서 target 소유 타일에 붙는다. 붙을 곳이 없으면 None."""
        if troops < C.MIN_ATTACK_TROOPS:
            return None
        seeds: dict[Coord, None] = {}
        for tile in gmap.owned_by(attacker):
            for n in reach(gmap, tile.pos, naval_range):
                t = gmap[n]
                if t.owner == target and t.passable:
                    seeds[n] = None
        if not seeds:
            return None
        return cls(attacker=attacker, target=target, troops=troops,
                   naval_range=naval_range,
                   frontier=deque(seeds), seen=set(seeds))

    # --- 진행 -------------------------------------------------------------

    def tile_cost(self, gmap: GameMap, pos: Coord,
                  atk: PlayerState, def_factor: float) -> float:
        """이 한 칸을 사들이는 데 드는 병력."""
        tile = gmap[pos]
        vs_player = self.target is not None
        return (C.CONQUER_COST_BASE
                * tile.defense
                * def_factor
                * atk.cost_mult(tile.terrain, vs_player))

    def budget(self, atk: PlayerState, dt: float) -> int:
        """이번 tick 에 시도할 칸 수. 병력이 많을수록 넓게 번진다."""
        per_sec = min(
            C.EXPAND_TILES_PER_SEC_MAX,
            C.EXPAND_TILES_PER_SEC_BASE + self.troops * C.EXPAND_TILES_PER_SEC_PER_TROOP,
        ) * atk.expand_speed_mult()
        self._carry += per_sec * dt
        n = int(self._carry)
        self._carry -= n
        return n

    def step(self, gmap: GameMap, dt: float,
             atk: PlayerState, def_factor: float) -> list[Coord]:
        """이번 tick 에 정복한 칸들을 돌려준다. 빈 리스트여도 부대는 살아 있을 수 있다."""
        taken: list[Coord] = []
        self.last_spent = 0.0
        for _ in range(self.budget(atk, dt)):
            if not self.frontier or self.troops < C.ATTACK_ABANDON_TROOPS:
                break
            pos = self.frontier.popleft()
            tile = gmap[pos]
            # 큐에 들어간 뒤 상황이 바뀌었을 수 있다 — 다른 부대가 먼저 먹었거나,
            # 대상이 그 사이 그 칸을 잃었거나.
            if tile.owner != self.target or not tile.passable:
                continue
            cost = self.tile_cost(gmap, pos, atk, def_factor)
            if cost > self.troops:
                # 감당 못 하는 칸은 **큐 앞에 되돌린다.** 뒤로 보내면 부대가 산을
                # 피해 평야만 골라 먹으며 지형 방어가 무의미해진다.
                self.frontier.appendleft(pos)
                break
            self.troops -= cost
            self.last_spent += cost
            tile.owner = self.attacker
            taken.append(pos)
            for n in reach(gmap, pos, self.naval_range):
                t = gmap[n]
                if n not in self.seen and t.owner == self.target and t.passable:
                    self.seen.add(n)
                    self.frontier.append(n)
        return taken

    def defender_loss(self, atk: PlayerState) -> float:
        """직전 step 에서 방어측이 함께 잃은 병력. 공격측이 쏟은 만큼 상대도 깎인다."""
        return self.last_spent * C.DEFENDER_LOSS_RATIO * atk.defender_loss_mult()

    @property
    def finished(self) -> bool:
        return not self.frontier or self.troops < C.ATTACK_ABANDON_TROOPS
