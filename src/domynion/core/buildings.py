"""건물 배치와 방어초소 조회.

**방어초소 조회가 성능의 급소다.** 원본은 `nearbyUnits(tile, range, DefensePost)` 를
칸마다 부른다. 우리 지도는 3.7만~13만 칸이고 확장은 tick 당 수백 칸이라, 초소마다
거리를 재면 그 자리에서 루프가 터진다. 그래서 **초소가 덮는 칸을 미리 칠해 둔 배열**을
들고 다니고, 초소가 생기거나 사라질 때만 다시 칠한다.

원본 규칙:
- `structureMinDist()` = 15 — 건물끼리 유클리드 거리로 이만큼 떨어져야 한다
- `defensePostRange()` = 30 — 이 안이면 방어 ×5, 속도 ×3
- 건물은 **자기 영토 위**에만 짓는다
"""

from __future__ import annotations

import numpy as np

from . import constants as C
from .gamemap import GameMap, TileRef
from .units import STRUCTURES, UnitType


class DefensePostIndex:
    """초소가 덮는 칸을 소유자별로 칠해 둔 표.

    `covers(gmap, tile, pid)` 가 O(1) 이어야 한다 — 확장 루프 가장 안쪽에서 불린다."""

    __slots__ = ("_cover", "_dirty")

    def __init__(self, size: int):
        # 값은 소유자 pid + 1 (0 = 아무도 안 덮음). 여러 명이 겹치면 마지막이 이긴다 —
        # 원본도 "수비자 것이 하나라도 있으면" 이라 겹침을 따지지 않는다.
        self._cover = np.zeros(size, dtype=np.int16)
        self._dirty = True

    def rebuild(self, gmap: GameMap, posts: list[tuple[TileRef, int]]) -> None:
        self._cover[:] = 0
        if not posts:
            self._dirty = False
            return
        w = gmap.width
        r = C.DEFENSE_POST_RANGE
        for tile, pid in posts:
            cx, cy = tile % w, tile // w
            x0, x1 = max(0, cx - r), min(gmap.width, cx + r + 1)
            y0, y1 = max(0, cy - r), min(gmap.height, cy + r + 1)
            ys = np.arange(y0, y1)[:, None]
            xs = np.arange(x0, x1)[None, :]
            mask = (xs - cx) ** 2 + (ys - cy) ** 2 < r * r
            block = self._cover.reshape(gmap.height, gmap.width)[y0:y1, x0:x1]
            block[mask] = pid + 1
        self._dirty = False

    def covers(self, gmap: GameMap, tile: TileRef, pid: int) -> bool:
        return bool(self._cover[tile] == pid + 1)


def euclid_sq(gmap: GameMap, a: TileRef, b: TileRef) -> int:
    ax, ay = a % gmap.width, a // gmap.width
    bx, by = b % gmap.width, b // gmap.width
    return (ax - bx) ** 2 + (ay - by) ** 2


def can_place_structure(gmap: GameMap, tile: TileRef, pid: int,
                        existing: list[TileRef],
                        utype: "UnitType | None" = None) -> bool:
    """내 영토 위이고, **아무 건물에서든** `structureMinDist` 이상 떨어져 있어야 한다.

    ⚠ `existing` 에는 **남의 건물도 들어간다**(§5.86). 원본
    `validStructureSpawnTiles` 가 `nearbyUnits(..., predicate=undefined)` 로
    주인을 안 가리고 훑는다. 국경 근처는 내 땅이어도 적 도시가 15칸 안일 수
    있고, 전에는 거기에 붙여 지을 수 있었다.

    **항구만 예외로 해안이어야 한다**(`portSpawn` 이 `isShore` 로 거른다).
    이걸 빼면 내륙에 항구가 서고, 그 항구에서 배가 못 떠서 무역선이 한 척도 안 뜬다."""
    if not gmap.passable(tile) or int(gmap.owner[tile]) != pid:
        return False
    if utype is UnitType.PORT and not gmap.is_shore(tile):
        return False
    min_sq = C.STRUCTURE_MIN_DIST ** 2
    return all(euclid_sq(gmap, tile, t) >= min_sq for t in existing)


def structure_tiles(player_units) -> list[TileRef]:
    """최소 거리 판정에 걸리는 건물들의 타일. 건설 중인 것도 자리를 차지한다.

    ⚠ 한 플레이어 것만 돌려준다. **최소 거리는 주인을 안 가리므로**(§5.86)
    엔진은 `all_structure_tiles` 를 쓴다 — 이 함수는 한 명만 볼 때 쓴다."""
    return [u.tile for u in player_units.units
            if u.active and u.utype in STRUCTURES]


def all_structure_tiles(players) -> list[TileRef]:
    """**모든** 플레이어의 건물 타일. 원본 `nearbyUnits` 가 주인을 안 가린다."""
    out: list[TileRef] = []
    for p in players:
        out.extend(u.tile for u in p.units.units
                   if u.active and u.utype in STRUCTURES)
    return out


def find_spot(gmap: GameMap, pid: int, near: TileRef,
              existing: list[TileRef],
              search: int = C.STRUCTURE_SEARCH_RADIUS,
              utype: "UnitType | None" = None) -> TileRef | None:
    """`near` 근처에서 지을 수 있는 칸을 찾는다 — 가까운 곳부터.

    원본 `validStructureSpawnTiles()` 는 BFS 로 내 영토를 훑어 거리순으로 정렬한 뒤
    첫 칸을 쓴다. 여기서는 같은 결과를 사각 탐색으로 낸다(내 영토는 연결돼 있고
    반경이 작아서 차이가 없다).

    ⚠ **반경은 15 다**(`searchRadius`, §5.86). 전에는 40 이었다 — 클릭한 자리가
    막혔을 때 원본이 포기하는 거리에서도 우리는 계속 찾아 지었다. 40이면 사람이
    바다를 눌러도 내륙 어딘가에 건물이 서서, 어디를 눌렀는지와 무관해진다.

    ⚠ **항구는 자를 다르게 쓴다**(`portSpawn`, §5.89). 위 반경으로 거른 자리들
    중에서 다시 **맨해튼 20** 안만 보고 **맨해튼 거리 순**으로 고른다. 그래서
    같은 후보 집합이어도 뽑히는 칸이 다르다 — 유클리드는 대각선을 가깝게 보고
    맨해튼은 멀게 본다."""
    w, h = gmap.width, gmap.height
    cx, cy = near % w, near // w
    is_port = utype is not None and utype is UnitType.PORT
    best, best_d = None, None
    for dy in range(-search, search + 1):
        y = cy + dy
        if not 0 <= y < h:
            continue
        for dx in range(-search, search + 1):
            x = cx + dx
            if not 0 <= x < w:
                continue
            t = y * w + x
            # ⚠ **원이지 정사각형이 아니다**(원본은 `euclideanDistSquared < r²`).
            # 사각으로 두면 모서리(15,15)까지 후보가 되는데 그건 21칸이다.
            if dx * dx + dy * dy > search * search:
                continue
            if is_port:
                d = abs(dx) + abs(dy)
                if d > C.PORT_SPAWN_RADIUS:
                    continue
            else:
                d = dx * dx + dy * dy
            if best_d is not None and d >= best_d:
                continue
            if can_place_structure(gmap, t, pid, existing, utype):
                best, best_d = t, d
    return best
