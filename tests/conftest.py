"""테스트용 인공 지도.

생성된 지도로 규칙을 재면 노이즈 때문에 무엇을 재고 있는지 알 수 없다. 지형을 손으로
깔아 둔 작은 지도에서 재고, 생성기 자체는 따로 검증한다.
"""

from __future__ import annotations

import pytest

from domynion.core.constants import Terrain
from domynion.core.gamemap import GameMap, Tile


def make_map(rows: list[str]) -> GameMap:
    """문자 그림으로 지도를 만든다. `~`바다 `.`평야 `f`숲 `n`구릉 `A`산악."""
    ch = {"~": Terrain.WATER, ".": Terrain.PLAINS, "f": Terrain.FOREST,
          "n": Terrain.HILLS, "A": Terrain.MOUNTAINS}
    tiles = {}
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            tiles[(x, y)] = Tile(pos=(x, y), terrain=ch[c])
    return GameMap(width=len(rows[0]), height=len(rows), tiles=tiles)


@pytest.fixture
def plains5() -> GameMap:
    """5×5 전부 평야. 지형 변수를 없앤 대조군이다."""
    return make_map(["." * 5] * 5)
