"""해상 — 수송선 · 전함 · 포탄 · 무역선.

**바다는 육지 확장과 규칙이 다르다.** 육지는 프론티어가 번지지만 배는 경로를 따라
한 칸씩 움직이고, 도착해서야 상륙 지점을 정복한 뒤 그 자리에서 육상 공격이 시작된다.
그래서 배는 `Attack` 이 아니라 별도의 진행체다.

원본:
- `TransportShipExecution.ts` — 수송선. tick 당 1칸, 최대 3척, 병력 `troops/5`
- `WarshipExecution` / `ShellExecution` — 전함은 순찰하고 사거리 안의 배를 포격한다
- `TradeShipExecution` — 항구 사이를 오가며 골드를 번다

경로는 A* 대신 **바다만 지나는 BFS 최단 경로**로 낸다. 원본도 결국 바다 그래프 위의
최단 경로이고, 우리 지도(3.7만~13만 칸)에서는 BFS 로 충분하다.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from . import constants as C
from .constants import Terrain
from .gamemap import GameMap, TileRef


def _touching_components(gmap: GameMap, t: TileRef) -> frozenset[int]:
    """칸이 접한 바다 연결성분. **캐시한다** — 무역선 목적지를 고를 때 항구마다
    후보 전부에 대해 부르므로(120곳이면 판당 수만 번) 매번 다시 재면 비싸다."""
    hit = gmap._touch_cc.get(t)
    if hit is None:
        cc = gmap.ocean_components()
        hit = frozenset(int(cc[x]) for x in gmap.neighbors(t) if cc[x] >= 0)
        gmap._touch_cc[t] = hit
    return hit


def water_path(gmap: GameMap, src: TileRef, dst: TileRef,
               slack: float = 2.5) -> list[TileRef] | None:
    """`src` 에서 `dst` 까지 **바다를 지나는** 최단 경로. 끝 칸(상륙 지점)만 육지다.

    BFS 라 경로가 최단임이 보장된다. 다만 그냥 두면 **닿을 수 없는 목적지에서
    바다 전체를 훑는다** — 실측으로 판당 15초가 91초가 됐다. 두 겹으로 막는다:

    1. 두 칸이 접한 바다 연결성분이 겹치지 않으면 **즉시 기각**한다(O(1))
    2. 탐색을 두 칸을 감싸는 상자 안으로 묶는다. `slack` 이 그 여유다 —
       최단 경로가 상자를 크게 벗어나는 지형이면 못 찾고 None 이 되는데,
       배가 그렇게까지 돌아가야 하는 목적지는 애초에 고를 만한 곳이 아니다.
    """
    if src == dst:
        return [dst]
    if not (_touching_components(gmap, src) & _touching_components(gmap, dst)):
        return None

    w = gmap.width
    sx, sy = src % w, src // w
    dx_, dy_ = dst % w, dst // w
    margin = int(abs(sx - dx_) + abs(sy - dy_)) * slack + 8
    x0, x1 = min(sx, dx_) - margin, max(sx, dx_) + margin
    y0, y1 = min(sy, dy_) - margin, max(sy, dy_) + margin

    prev: dict[TileRef, TileRef] = {src: src}
    q = deque([src])
    while q:
        cur = q.popleft()
        for n in gmap.neighbors(cur):
            if n in prev:
                continue
            if n == dst:
                prev[n] = cur
                path = [n]
                while path[-1] != src:
                    path.append(prev[path[-1]])
                path.reverse()
                return path[1:]
            if gmap.terrain[n] != Terrain.OCEAN:
                continue
            nx, ny = n % w, n // w
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            prev[n] = cur
            q.append(n)
    return None


def shoreline_tiles(gmap: GameMap, pid: int) -> np.ndarray:
    """내 영토 중 바다에 접한 칸들. 배가 여기서 출발한다.

    **numpy 로 편다.** 칸마다 `is_shore` 를 부르면 영토가 17만 칸일 때 한 번에
    589ms 가 든다(실측, cProfile). 바다 마스크를 네 방향으로 밀어 한 번에 본다."""
    h, w = gmap.height, gmap.width
    mine = (gmap.owner.reshape(h, w) == pid)
    if not mine.any():
        return np.empty(0, dtype=np.int64)
    ocean = (gmap.terrain.reshape(h, w) == Terrain.OCEAN)
    touch = np.zeros((h, w), dtype=bool)
    touch[:, :-1] |= ocean[:, 1:]
    touch[:, 1:] |= ocean[:, :-1]
    touch[:-1, :] |= ocean[1:, :]
    touch[1:, :] |= ocean[:-1, :]
    return np.flatnonzero((mine & touch).ravel()).astype(np.int64)


def best_spawn(gmap: GameMap, pid: int, toward: TileRef) -> TileRef | None:
    """`toward` 에 가장 가까운 내 해안 칸. 원본 `bestTransportShipSpawn` 자리다."""
    shore = shoreline_tiles(gmap, pid)
    if not len(shore):
        return None
    tx, ty = gmap.xy(toward)
    xs = shore % gmap.width
    ys = shore // gmap.width
    return int(shore[np.argmin((xs - tx) ** 2 + (ys - ty) ** 2)])


@dataclass
class TransportShip:
    """상륙 부대. 도착하면 상륙 지점을 정복하고 그 자리에서 육상 공격이 시작된다."""

    owner: int
    target: int | None
    troops: float
    path: list[TileRef]
    dst: TileRef
    step_i: int = 0
    retreating: bool = False
    # 퇴각 경로를 이미 새로 깔았는가. 원본은 `retreatDst ??=` 로 **한 번만** 정한다 —
    # 매 tick 다시 정하면 배가 해안을 따라 움직일 때마다 목적지가 흔들려 제자리걸음한다.
    replanned: bool = False
    done: bool = False

    @property
    def tile(self) -> TileRef:
        return self.path[min(self.step_i, len(self.path) - 1)]

    @property
    def arrived(self) -> bool:
        return self.step_i >= len(self.path) - 1

    def advance(self) -> None:
        """tick 당 한 칸(`ticksPerMove = 1`)."""
        if not self.arrived:
            self.step_i += 1


def shell_damage(rng: random.Random, veterancy: int = 0) -> int:
    """`ShellExecution` — 굴림 1~5 로 200~300. 격침 경험이 있으면 더 아프다.

        피해 = (250/250) × ((굴림 − 1) × 25 + 200) × (100 + 격침수 × 20)/100

    체력 1000 인 전함이 4~5발을 견딘다는 뜻이다. 고정 250 으로 두면 정확히 4발이
    되어 교전이 전부 같은 모양이 된다."""
    roll = rng.randint(C.SHELL_ROLL_MIN, C.SHELL_ROLL_MAX)
    mult = (roll - 1) * C.SHELL_ROLL_STEP + C.SHELL_ROLL_BASE
    if veterancy:
        mult = (mult * (100 + veterancy * C.WARSHIP_VETERANCY_SHELL_BONUS)) // 100
    return round(C.SHELL_DAMAGE / 250 * mult)


@dataclass
class Warship:
    """순찰하며 사거리 안의 적 배를 포격한다.

    **표적 우선순위가 정해져 있다**(원본 `WarshipExecution`):
    수송선 → 적 전함 → 무역선. 수송선을 먼저 치는 이유는 그게 상륙을 막는 유일한
    수단이기 때문이다."""

    owner: int
    tile: TileRef
    health: int = C.WARSHIP_MAX_HEALTH
    patrol_origin: TileRef | None = None
    cooldown: int = 0
    veterancy: int = 0            # 격침 횟수. 포탄 피해에 실린다
    # 지금 향하는 순찰 지점(`targetTile`). 닿으면 비우고 새로 뽑는다.
    #
    # ⚠ 이식 누락 스물둘. 이게 없어서 전함이 **태어난 자리에 붙박여 있었다** —
    # `patrol_origin` 은 필드로만 있고 아무도 배를 옮기지 않았다. 격침에서는
    # 안 드러난다(사거리 안이면 그 자리에서 쏘면 된다). 나포를 붙이니 드러났다.
    patrol_target: TileRef | None = None

    def __post_init__(self) -> None:
        if self.patrol_origin is None:
            self.patrol_origin = self.tile

    @property
    def sunk(self) -> bool:
        return self.health <= 0


@dataclass
class TradeShip:
    """항구 사이를 오가며 골드를 번다. 도착하면 **양쪽 항구 주인이 함께** 받는다."""

    owner: int
    src_port: TileRef
    dst_port: TileRef
    dst_owner: int
    path: list[TileRef]
    step_i: int = 0
    done: bool = False

    # --- 나포 (`wasCaptured`) ---------------------------------------------
    #
    # ⚠ 이식 누락 스물. 전에는 전함이 무역선을 **포탄으로 격침**시켰다 —
    # 골드가 아무에게도 안 가고 증발했다. 원본은 쫓아가 **나포**하고, 도착하면
    # **나포한 쪽이 전액을 번다**(원래 주인은 한 푼도 못 받는다). 원본 통계에
    # `piracyGold` 가 별도 항목으로 있을 만큼 독립된 수입 경로다.
    captured_by: int | None = None
    # 해안선 물 칸을 밟은 마지막 tick. 그 뒤 20 tick 동안 나포당하지 않는다
    # (`_lastSetSafeFromPirates`). 항구 앞에서 잡히지 않게 하는 장치다.
    last_safe_tick: int = -10_000

    @property
    def tile(self) -> TileRef:
        return self.path[min(self.step_i, len(self.path) - 1)]

    @property
    def arrived(self) -> bool:
        return self.step_i >= len(self.path) - 1

    def safe_from_pirates(self, tick: int) -> bool:
        """`isSafeFromPirates()` — 해안선을 밟은 지 20 tick 이 안 지났으면 안전."""
        return tick - self.last_safe_tick < C.SAFE_FROM_PIRATES_TICKS

    def advance(self) -> None:
        if not self.arrived:
            self.step_i += 1


def trade_gold(dist: float) -> int:
    """`tradeShipGold(dist)` — 거리 300 아래는 시그모이드가 눌러 크게 손해다.

        75000 / (1 + e^(−0.03 × (거리 − 300))) + 50 × 거리
    """
    return int(75_000 / (1 + math.exp(-0.03 * (dist - C.TRADE_SHORT_RANGE_DEBUFF)))
               + 50 * dist)


def trade_spawn_rate(rejections: int, num_ships: int) -> int:
    """무역선이 뜰 확률은 `1 / 이 값`. 배가 많을수록 잘 안 뜨고, 계속 안 뜨면 보정된다."""
    decay = math.log(2) / 50
    base = 1.0 - 1.0 / (1.0 + math.exp(-decay * (num_ships - 400)))
    pity = 1.0 / (rejections + 1)
    return int(100 * pity / base) if base > 0 else 1 << 30


# --- 무역선 스폰 — 항구마다 따로 돈다 (`PortExecution`) ----------------------
#
# ⚠ 이식 누락 열아홉. 우리는 이걸 **판 전체에서 매 tick 한 번** 굴리고 있었다.
# 원본은 `PortExecution` 이 항구마다 붙어 10 tick 마다, **레벨 횟수만큼** 굴리고,
# 거절 카운터(pity)도 항구마다 따로 쌓인다. 실측 결과가 그대로 갈렸다 —
# 원본 크기 9,000 tick 에서 무역선 도착이 22회였다(기차는 577회).
#
# 판 하나로 두면 세 가지가 동시에 깨진다:
#   1. 항구가 46곳이든 2곳이든 유통량이 같다 (항구를 지을 이유가 없어진다)
#   2. 레벨이 아무 일도 안 한다 (`unitsOwned` 때와 같은 종류의 누락)
#   3. 아무 항구나 한 번 성공하면 **모든 항구의 pity 가 0으로 리셋된다**

def port_check_due(check_offset: int, tick: int) -> bool:
    """`(ticks + checkOffset) % 10 !== 0` — 항구마다 다른 tick 에 굴린다.

    한꺼번에 굴리면 유통량이 10 tick 주기로 뭉친다. 원본은 항구가 생긴 tick 을
    그대로 오프셋으로 쓴다(`checkOffset = mg.ticks() % 10`)."""
    return (tick + check_offset) % C.TRADE_SPAWN_CHECK_PERIOD == 0


def proximity_bonus_count(total_ports: int) -> int:
    """`within(totalPorts / 3, 4, totalPorts)` — 근접 보너스를 받는 후보 수."""
    return int(min(max(total_ports / C.TRADE_PROXIMITY_BONUS_DIVISOR,
                       C.TRADE_PROXIMITY_BONUS_MIN), total_ports))


def manhattan(gmap: GameMap, a: TileRef, b: TileRef) -> int:
    w = gmap.width
    return abs(a % w - b % w) + abs(a // w - b // w)


def trading_ports(gmap: GameMap, src: TileRef,
                  candidates: list[tuple[TileRef, int, int]],
                  friendly: "set[int]") -> list[tuple[TileRef, int]]:
    """`tradingPorts()` — **확률 목록**이다. 같은 항구가 여러 번 들어가면 그만큼 잘 뽑힌다.

    `candidates` 는 이미 금수·자기 자신을 걸러 낸 (타일, 주인, 레벨) 목록.
    반환은 (타일, 주인) 을 가중치만큼 반복한 것 — 호출부가 균등하게 하나 고르면 된다.

    가중치 셋이 곱이 아니라 **합**으로 붙는다(원본이 `push` 를 반복한다):
      · 기본 레벨만큼
      · 거리순 상위 1/3 안이고 300 이상이면 레벨만큼 더
      · 동맹이고 300 이상이면 레벨만큼 더
    300 미만(`tradeShipShortRangeDebuff`)이 보너스에서 빠지는 것이 핵심이다 —
    `trade_gold` 시그모이드가 그 구간을 크게 깎으므로 가까운 항구끼리 왕복하는
    것이 이득이 되면 안 된다.
    """
    src_comp = _touching_components(gmap, src)
    reachable = [(t, owner, lvl) for t, owner, lvl in candidates
                 if src_comp & _touching_components(gmap, t)]
    reachable.sort(key=lambda c: manhattan(gmap, src, c[0]))

    bonus_n = proximity_bonus_count(len(reachable))
    out: list[tuple[TileRef, int]] = []
    for i, (tile, owner, lvl) in enumerate(reachable):
        entry = [(tile, owner)] * lvl
        out += entry
        too_close = manhattan(gmap, src, tile) < C.TRADE_SHORT_RANGE_DEBUFF
        if not too_close and i < bonus_n:
            out += entry
        if not too_close and owner in friendly:
            out += entry
    return out
