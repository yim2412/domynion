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
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from . import constants as C
from .constants import Terrain
from .gamemap import GameMap, TileRef


def _touching_components(gmap: GameMap, t: TileRef) -> set[int]:
    cc = gmap.ocean_components()
    return {int(cc[n]) for n in gmap.neighbors(t) if cc[n] >= 0}


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
    """내 영토 중 바다에 접한 칸들. 배가 여기서 출발한다."""
    mine = gmap.owned_refs(pid)
    if not len(mine):
        return mine
    keep = [t for t in mine.tolist() if gmap.is_shore(t)]
    return np.array(keep, dtype=np.int64)


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


@dataclass
class Warship:
    """순찰하며 사거리 안의 적 배를 포격한다. 포탄은 250 피해."""

    owner: int
    tile: TileRef
    health: int = 1000
    patrol_origin: TileRef | None = None
    cooldown: int = 0

    def __post_init__(self) -> None:
        if self.patrol_origin is None:
            self.patrol_origin = self.tile


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

    @property
    def tile(self) -> TileRef:
        return self.path[min(self.step_i, len(self.path) - 1)]

    @property
    def arrived(self) -> bool:
        return self.step_i >= len(self.path) - 1

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
