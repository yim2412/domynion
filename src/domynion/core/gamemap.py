"""맵 생성과 타일 정의.

인접 판정은 `neighbors()` 하나만 사용한다. 헥스 전환 시 이 함수만 교체하면 된다.

지형은 타일마다 독립 추첨하지 않는다. 그렇게 만들면 바다가 한 칸씩 흩어지고 산이
점점이 박혀, 격자선을 지워도 화면이 "타일 게임"으로 읽힌다. 대신 노이즈 높이맵을
만들고 해수면으로 잘라 대륙을 얻는다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .constants import TerrainSpec

from .constants import (
    BASE_TILES_PER_PLAYER,
    EDGE_FALLOFF,
    FOREST_RATIO,
    HILL_RATIO,
    MAP_ASPECT,
    MAX_LAND_RATIO,
    MIN_LAND_RATIO,
    MIN_TILES_PER_START,
    MOUNTAIN_RATIO,
    NOISE_OCTAVES,
    NOISE_SCALE,
    SEA_LEVEL,
    Terrain,
    spec_for,
)

Coord = tuple[int, int]


@dataclass
class Tile:
    pos: Coord
    terrain: Terrain
    owner: int | None = None          # 플레이어 id, None = 중립

    @property
    def spec(self) -> "TerrainSpec":
        return spec_for(self.terrain)

    @property
    def passable(self) -> bool:
        return self.spec.passable

    @property
    def defense(self) -> float:
        """이 타일을 먹는 데 드는 병력의 지형 배율."""
        return self.spec.defense


class GameMap:
    def __init__(self, width: int, height: int, tiles: dict[Coord, Tile]):
        self.width = width
        self.height = height
        self.tiles = tiles

    # --- 생성 -------------------------------------------------------------

    @staticmethod
    def dims_for(player_count: int) -> tuple[int, int]:
        """가로×세로. 총 칸 수는 유지하고 화면 비율로 편다."""
        total = BASE_TILES_PER_PLAYER * player_count
        h = math.ceil(math.sqrt(total / MAP_ASPECT))
        return math.ceil(h * MAP_ASPECT), h

    @classmethod
    def generate(cls, player_count: int, rng: random.Random) -> "GameMap":
        width, height = cls.dims_for(player_count)
        salt = rng.randrange(1 << 30)

        # 해수면을 조금씩 낮춰 가며 육지 비율을 맞춘다. 노이즈만 믿으면 판마다
        # "거의 다 바다"인 맵이 나와 시작조차 못 하는 경우가 생긴다.
        sea = SEA_LEVEL
        heights = {
            (x, y): _height_at(x, y, width, height, salt)
            for y in range(height) for x in range(width)
        }
        for _ in range(24):
            land = sum(1 for h in heights.values() if h >= sea)
            ratio = land / (width * height)
            if ratio < MIN_LAND_RATIO:
                sea -= 0.03
            elif ratio > MAX_LAND_RATIO:
                sea += 0.03
            else:
                break

        # 고지대·숲은 백분위로 가른다. 절대 임계값을 쓰면 fBm 값이 중앙에 몰려
        # 있어 산악이 한 칸도 안 나오는 판이 생긴다.
        land_pos = [pos for pos, h in heights.items() if h >= sea]
        moisture = {
            pos: _fbm(pos[0] * NOISE_SCALE * 1.7, pos[1] * NOISE_SCALE * 1.7,
                      salt ^ 0x5EED, octaves=3)
            for pos in land_pos
        }
        mtn_cut = _percentile([heights[p] for p in land_pos], 1.0 - MOUNTAIN_RATIO)
        hill_cut = _percentile([heights[p] for p in land_pos],
                               1.0 - MOUNTAIN_RATIO - HILL_RATIO)
        lowland = [p for p in land_pos if heights[p] < hill_cut]
        forest_cut = _percentile([moisture[p] for p in lowland], 1.0 - FOREST_RATIO)

        tiles: dict[Coord, Tile] = {}
        for y in range(height):
            for x in range(width):
                pos = (x, y)
                h = heights[pos]
                if h < sea:
                    terrain = Terrain.WATER
                elif h >= mtn_cut:
                    terrain = Terrain.MOUNTAINS
                elif h >= hill_cut:
                    terrain = Terrain.HILLS
                elif moisture[pos] >= forest_cut:
                    terrain = Terrain.FOREST
                else:
                    terrain = Terrain.PLAINS
                tiles[pos] = Tile(pos=pos, terrain=terrain)
        return cls(width, height, tiles)

    # --- 조회 -------------------------------------------------------------

    def __getitem__(self, pos: Coord) -> Tile:
        return self.tiles[pos]

    def in_bounds(self, pos: Coord) -> bool:
        x, y = pos
        return 0 <= x < self.width and 0 <= y < self.height

    def neighbors(self, pos: Coord) -> list[Coord]:
        """4방향 인접(대각선 제외). 헥스 전환 시 여기만 교체."""
        x, y = pos
        cand = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        return [c for c in cand if self.in_bounds(c)]

    def within(self, pos: Coord, radius: int) -> list[Coord]:
        """맨해튼 거리 radius 이내. 항해술이 바다 건너를 볼 때 쓴다."""
        x, y = pos
        out = []
        for dy in range(-radius, radius + 1):
            span = radius - abs(dy)
            for dx in range(-span, span + 1):
                c = (x + dx, y + dy)
                if c != pos and self.in_bounds(c):
                    out.append(c)
        return out

    def all_tiles(self):
        return self.tiles.values()

    def owned_by(self, player_id: int) -> list[Tile]:
        return [t for t in self.tiles.values() if t.owner == player_id]

    def land_tiles(self) -> list[Tile]:
        return [t for t in self.tiles.values() if t.passable]

    def frontier(self, player_id: int, naval_range: int = 0) -> list[Tile]:
        """내 영토에서 닿을 수 있는, 내 소유가 아닌 통행 가능 타일.

        naval_range 가 0 이면 육지로 붙어 있는 곳만이다. 항해술이 있으면 그만큼
        바다 건너까지 닿는다 — 그게 이 증강이 하는 일 전부다."""
        seen: dict[Coord, Tile] = {}
        reach = max(1, naval_range + 1)
        for tile in self.owned_by(player_id):
            cand = (self.neighbors(tile.pos) if naval_range <= 0
                    else self.within(tile.pos, reach))
            for n in cand:
                t = self.tiles[n]
                if t.owner != player_id and t.passable:
                    seen[n] = t
        return list(seen.values())

    def border_targets(self, player_id: int, naval_range: int = 0) -> set[int | None]:
        """닿을 수 있는 상대들. None 은 중립 지대를 뜻한다."""
        return {t.owner for t in self.frontier(player_id, naval_range)}

    # --- 대륙 -------------------------------------------------------------

    def landmasses(self) -> list[list[Coord]]:
        """연결된 육지 덩어리들. 큰 것부터 정렬해 돌려준다."""
        unseen = {t.pos for t in self.tiles.values() if t.passable}
        out: list[list[Coord]] = []
        while unseen:
            start = unseen.pop()
            blob = [start]
            stack = [start]
            while stack:
                cur = stack.pop()
                for n in self.neighbors(cur):
                    if n in unseen:
                        unseen.discard(n)
                        blob.append(n)
                        stack.append(n)
            out.append(blob)
        out.sort(key=len, reverse=True)
        return out

    def place_starts(self, player_count: int, rng: random.Random) -> list[Coord]:
        """시작 위치. 수도 타일 같은 특별한 칸은 두지 않는다 — 한 칸에서 시작해
        번져 나갈 뿐이다.

        전원을 **같은 대륙**에 놓는다. 한 명이라도 작은 섬에서 시작하면 그 판은
        시작하자마자 끝난 것이나 마찬가지다 — 확장할 곳이 없어 병력 상한이 낮게
        묶이고, 항해술을 뽑기 전까지는 바다를 건널 수도 없다."""
        masses = self.landmasses()
        if not masses:  # pragma: no cover - 해수면 보정이 실패한 극단적 경우
            raise RuntimeError("육지가 없는 맵이 생성되었다")

        pool = list(masses[0])
        for extra in masses[1:]:
            if len(pool) >= player_count * MIN_TILES_PER_START:
                break
            pool.extend(extra)

        min_dist = min(self.width, self.height) / 2.0
        picked: list[Coord] = []
        for _ in range(400):
            picked = []
            shuffled = pool[:]
            rng.shuffle(shuffled)
            for pos in shuffled:
                if all(_manhattan(pos, q) >= min_dist for q in picked):
                    picked.append(pos)
                if len(picked) == player_count:
                    break
            if len(picked) == player_count:
                break
            min_dist *= 0.92      # 좁은 대륙이면 조건을 점점 완화한다
        if len(picked) < player_count:  # pragma: no cover
            picked = pool[:player_count]

        for pid, pos in enumerate(picked):
            self.tiles[pos].owner = pid
        return picked


# --- 노이즈 ---------------------------------------------------------------


def _hash2(x: int, y: int, salt: int) -> float:
    h = (x * 73856093) ^ (y * 19349663) ^ (salt * 83492791)
    h = (h ^ (h >> 13)) & 0x7FFFFFFF
    h = (h * 1274126177) & 0x7FFFFFFF
    return (h % 65521) / 65521.0


def _value_noise(fx: float, fy: float, salt: int) -> float:
    x0, y0 = math.floor(fx), math.floor(fy)
    tx, ty = fx - x0, fy - y0
    sx = tx * tx * (3 - 2 * tx)
    sy = ty * ty * (3 - 2 * ty)
    x0, y0 = int(x0), int(y0)
    v00 = _hash2(x0, y0, salt)
    v10 = _hash2(x0 + 1, y0, salt)
    v01 = _hash2(x0, y0 + 1, salt)
    v11 = _hash2(x0 + 1, y0 + 1, salt)
    return (v00 * (1 - sx) + v10 * sx) * (1 - sy) + (v01 * (1 - sx) + v11 * sx) * sy


def _fbm(fx: float, fy: float, salt: int, octaves: int = NOISE_OCTAVES) -> float:
    """옥타브를 겹쳐 큰 지형과 잔 지형을 함께 만든다."""
    total = amp = 0.0
    amp, freq, norm = 1.0, 1.0, 0.0
    for _ in range(octaves):
        total += _value_noise(fx * freq, fy * freq, salt) * amp
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def _height_at(x: int, y: int, width: int, height: int, salt: int) -> float:
    """높이 = 노이즈 − 가장자리 감쇠. 감쇠가 없으면 대륙이 맵 밖으로 잘려 나가
    직선 해안선이 생기고, 그 순간 절차 생성이라는 티가 난다."""
    h = _fbm(x * NOISE_SCALE, y * NOISE_SCALE, salt)
    nx = (x / (width - 1)) * 2 - 1 if width > 1 else 0.0
    ny = (y / (height - 1)) * 2 - 1 if height > 1 else 0.0
    dist = math.sqrt(nx * nx + ny * ny) / math.sqrt(2)
    return h - (dist ** 2.1) * EDGE_FALLOFF


def _percentile(values: list[float], q: float) -> float:
    """values 의 q 분위값. 빈 리스트면 아무도 넘지 못할 값을 돌려준다."""
    if not values:
        return float("inf")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
