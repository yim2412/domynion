"""시작 배치 — openfront `SpawnExecution` + `getSpawnTiles`.

**시작 영토는 1칸이 아니라 반경 4의 원이다.** 우리가 임시로 쓰던 "육지 한 칸"과는
다르다 — 1칸으로 시작하면 병력 상한 공식(`타일^0.6`)의 바닥에서 출발해 초반이
지나치게 느리고, 첫 공격 한 번에 탈락할 수 있다.

규칙:
- 후보는 **육지·주인 없음·지도 경계 아님**
- 다른 사람 시작점과 맨해튼 거리 **30 이상**(`minDistanceBetweenPlayers`)
- 반경 4 원 안이 **전부** 유효해야 한다. 하나라도 바다·통행불가·남의 땅이면 다시 뽑는다
- 1,000번까지 시도하고, **750번을 넘기면 거리 조건을 푼다**(`RELAX_MIN_DIST_AT`) —
  좁은 지도에서 영영 못 뽑는 것을 막는 안전장치다
"""

from __future__ import annotations

import random

import numpy as np

from . import constants as C
from .gamemap import GameMap, TileRef

MAX_SPAWN_TRIES = 1_000
RELAX_MIN_DIST_AT = 750
SPAWN_RADIUS = 4
MIN_DISTANCE_BETWEEN_PLAYERS = 30


def spawn_tiles(gmap: GameMap, centre: TileRef,
                require_all_valid: bool = True) -> list[TileRef] | None:
    """`getSpawnTiles` — 중심에서 유클리드 반경 4 안의 칸들.

    `require_all_valid` 면 하나라도 못 쓰는 칸이 있을 때 **None** 을 돌려준다.
    걸러서 주는 게 아니라 통째로 무르는 것이 원본이다 — 해안에 반쯤 걸친 시작점을
    막기 위해서다."""
    w, h = gmap.width, gmap.height
    cx, cy = centre % w, centre // w
    r2 = SPAWN_RADIUS * SPAWN_RADIUS
    out: list[TileRef] = []
    for dy in range(-SPAWN_RADIUS, SPAWN_RADIUS + 1):
        y = cy + dy
        if not 0 <= y < h:
            # 느슨한 모드는 **건너뛴다.** 여기서 반환하면 위쪽 줄이 잘렸을 때
            # 아래쪽을 아예 안 보고 빈 목록을 준다.
            if require_all_valid:
                return None
            continue
        for dx in range(-SPAWN_RADIUS, SPAWN_RADIUS + 1):
            if dx * dx + dy * dy > r2:
                continue
            x = cx + dx
            if not 0 <= x < w:
                if require_all_valid:
                    return None
                continue
            t = y * w + x
            bad = int(gmap.owner[t]) >= 0 or not gmap.passable(t)
            if bad:
                if require_all_valid:
                    return None
                continue
            out.append(t)
    return out


def is_border_tile(gmap: GameMap, t: TileRef) -> bool:
    """지도 가장자리. 원본이 `isBorder` 로 거른다 — 가장자리에서 시작하면 확장
    방향이 반쪽이라 불리하다."""
    x, y = t % gmap.width, t // gmap.width
    return x == 0 or y == 0 or x == gmap.width - 1 or y == gmap.height - 1


def pick_spawn(gmap: GameMap, rng: random.Random,
               taken: list[TileRef]) -> tuple[TileRef, list[TileRef]] | None:
    """시작점 하나를 고른다. 못 고르면 None."""
    land = np.flatnonzero(gmap.passable_mask())
    if not len(land):
        return None
    w = gmap.width
    for tries in range(1, MAX_SPAWN_TRIES + 1):
        centre = int(rng.choice(land))
        if int(gmap.owner[centre]) >= 0 or is_border_tile(gmap, centre):
            continue
        if tries <= RELAX_MIN_DIST_AT and taken:
            cx, cy = centre % w, centre // w
            too_close = any(
                abs(cx - t % w) + abs(cy - t // w) < MIN_DISTANCE_BETWEEN_PLAYERS
                for t in taken)
            if too_close:
                continue
        tiles = spawn_tiles(gmap, centre, require_all_valid=True)
        if tiles:
            return centre, tiles
    return None


def place_players(gmap: GameMap, count: int,
                  rng: random.Random) -> list[tuple[TileRef, list[TileRef]]]:
    """전원을 배치한다. 각자 중심과 반경 4 원을 돌려준다."""
    out: list[tuple[TileRef, list[TileRef]]] = []
    taken: list[TileRef] = []
    for pid in range(count):
        got = pick_spawn(gmap, rng, taken)
        if got is None:
            raise ValueError(f"{pid}번째 시작점을 못 찾았다 — 지도가 너무 좁다")
        centre, tiles = got
        for t in tiles:
            gmap.owner[t] = pid
        taken.append(centre)
        out.append((centre, tiles))
    return out
