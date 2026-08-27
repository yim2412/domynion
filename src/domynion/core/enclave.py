"""둘러싸인 영토 흡수 — 원본 `PlayerExecution.removeClusters`.

⚠ **이식 누락 서른여덟.** 우리에겐 이 규칙이 통째로 없었다. 남의 영토 안에 갇힌
조각이 **영원히 남는다** — 공격으로 지워야 하는데, 갇힌 조각은 국경이 한 쪽뿐이라
공격 부대가 거의 안 간다. 실제로는 지도에 점처럼 박힌 채 끝까지 살아 있게 된다.

원본은 국경 타일을 **덩어리로 묶어**(8방향) 각 덩어리가 갇혔는지 본다:

1. 바다에 닿거나 지도 가장자리에 닿으면 **안 갇힌 것**이다(나갈 길이 있다).
2. 이웃에 **주인 없는 칸**이 있으면 안 갇힌 것이다.
3. 가장 큰 덩어리는 **적이 정확히 하나**여야 하고, 나머지 덩어리는 적이 있기만
   하면 된다.
4. 적 이웃들의 경계 상자가 덩어리의 경계 상자를 **감싸야** 한다(`inscribed`).

그리고 실제로 넘기기 전에 한 번 더 본다(`isEnclosed`): 그 자리에서 내 땅과
**주인 없는 땅**을 타고 걸어 나갔을 때 바다나 지도 끝에 닿을 수 있으면 안 넘긴다.
원본 주석이 이 두 번째 검사가 필요한 이유를 적어 뒀다 — 국경 덩어리 검사는
"국경 타일만" 봤는데 실제로 넘어가는 것은 **그 덩어리가 얹힌 영토 전체**라,
넓은 제국 한가운데 뚫린 구멍(적의 고립지, 핵 분화구)을 감싼 덩어리가 검사를
통과할 수 있기 때문이다.

**분화구는 출구가 아니다** — 주인 없는 땅은 타고 걸어가되, 바다와 지도 끝만 출구다.
"""

from __future__ import annotations

from .constants import Terrain
from .gamemap import GameMap, TileRef


def _neighbors8(gm: GameMap, t: TileRef):
    """8방향. 국경 덩어리는 대각선으로도 이어진다(원본 `forEachNeighborWithDiag`)."""
    w, h = gm.width, gm.height
    x, y = t % w, t // w
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                yield ny * w + nx


def _on_edge(gm: GameMap, t: TileRef) -> bool:
    w, h = gm.width, gm.height
    x, y = t % w, t // w
    return x == 0 or y == 0 or x == w - 1 or y == h - 1


def _touches_water(gm: GameMap, t: TileRef) -> bool:
    return any(gm.terrain[n] == Terrain.OCEAN for n in gm.neighbors(t))


def border_tiles(gm: GameMap, pid: int, owned) -> set[TileRef]:
    """내 칸 중 **남과 맞닿은** 것들. 덩어리를 묶는 재료다.

    ⚠ **numpy 로 편다.** 파이썬 루프로 내 타일마다 이웃을 보면 이 함수 하나가
    판 시간의 26% 를 먹었다(실측: 12,890번 호출에 29초). §5.50 의
    `border_targets` 와 정확히 같은 자리다 — 인덱스 산술을 배열로 한 번에 한다."""
    import numpy as np
    refs = np.asarray(owned, dtype=np.int64)
    if refs.size == 0:
        return set()
    w, size = gm.width, gm.size
    owner = gm.owner
    x = refs % w
    mine = np.zeros(refs.size, dtype=bool)
    # 네 방향 중 하나라도 남의 칸이면 국경이다. 지도 밖은 **남**으로 친다 —
    # 가장자리 칸은 어차피 `_on_edge` 에서 걸러진다.
    left = x > 0
    mine[~left] = True
    mine[left] |= owner[refs[left] - 1] != pid
    right = x < w - 1
    mine[~right] = True
    mine[right] |= owner[refs[right] + 1] != pid
    up = refs >= w
    mine[~up] = True
    mine[up] |= owner[refs[up] - w] != pid
    down = refs < size - w
    mine[~down] = True
    mine[down] |= owner[refs[down] + w] != pid
    return {int(t) for t in refs[mine]}


def clusters(gm: GameMap, border: set[TileRef]) -> list[list[TileRef]]:
    """국경 타일을 **8방향으로** 이어 붙인 덩어리들."""
    seen: set[TileRef] = set()
    out: list[list[TileRef]] = []
    for start in border:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        group = []
        while stack:
            t = stack.pop()
            group.append(t)
            for n in _neighbors8(gm, t):
                if n in border and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(group)
    return out


def _enemy_box(gm: GameMap, pid: int, cluster: list[TileRef]):
    """덩어리에 닿은 적 칸들의 경계 상자와 적 목록.

    **주인 없는 이웃이 하나라도 있으면 갇힌 게 아니다** — 그쪽으로 나갈 수 있다.

    ⚠ 중립(-1)을 **적 목록에 넣지 않는다.** 넣으면 "적이 정확히 하나" 검사가
    대신 걸려 위 조기 탈출이 무동작이 된다 — 변이가 안 잡히는 것이 그 신호였다.
    -1 은 플레이어 번호가 아니므로 애초에 섞이면 안 된다."""
    w = gm.width
    enemies: set[int] = set()
    x0 = y0 = 1 << 30
    x1 = y1 = -1
    for t in cluster:
        # ⚠ 이 검사는 **변이로 안 잡힌다. 정상이다** — 지우면 `is_enclosed` 가
        # 같은 자리를 막는다(바다와 지도 끝이 거기서도 출구다). 원본도 두 곳에
        # 다 두므로 남긴다. 여기서 먼저 걸러야 비싼 채우기를 안 돈다.
        if _on_edge(gm, t) or _touches_water(gm, t):
            return None, None
        for n in gm.neighbors(t):
            o = int(gm.owner[n])
            if o < 0:
                return None, None            # 주인 없는 이웃 = 열린 길
            if o == pid:
                continue
            enemies.add(o)
            nx, ny = n % w, n // w
            x0, y0 = min(x0, nx), min(y0, ny)
            x1, y1 = max(x1, nx), max(y1, ny)
    if not enemies:
        return None, None
    return enemies, (x0, y0, x1, y1)


def _cluster_box(gm: GameMap, cluster: list[TileRef]):
    w = gm.width
    xs = [t % w for t in cluster]
    ys = [t // w for t in cluster]
    return min(xs), min(ys), max(xs), max(ys)


def _inscribed(outer, inner) -> bool:
    """`inscribed` — 바깥 상자가 안쪽 상자를 감싸는가."""
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def surrounded_by(gm: GameMap, pid: int, cluster: list[TileRef],
                  single_enemy: bool) -> set[int] | None:
    """이 덩어리를 가둔 **상대들**. 안 갇혔으면 `None`.

    `single_enemy` 는 **가장 큰 덩어리에만** 참이다 — 원본이 큰 덩어리에는
    "적이 정확히 하나"를 요구하고(`surroundedBySamePlayer`) 나머지에는 요구하지
    않는다(`isSurrounded`). 여럿이 둘러싼 경우 누가 가져가는지는
    `capturing_player` 가 정한다."""
    enemies, ebox = _enemy_box(gm, pid, cluster)
    if enemies is None:
        return None
    if single_enemy and len(enemies) != 1:
        return None
    if not _inscribed(ebox, _cluster_box(gm, cluster)):
        return None
    return enemies


def capturing_player(gm: GameMap, pid: int, cluster: list[TileRef],
                     attacks) -> int | None:
    """`getCapturingPlayer` — 여럿이 둘러쌌을 때 **누가 가져가나.**

    1. 나를 치고 있는 상대 중 **병력이 가장 많은 쪽**이 가져간다.
    2. 진행 중인 공격이 없으면 **국경을 가장 많이 맞댄 쪽**(최빈값)이 가져간다.

    ⚠ 이걸 안 옮기고 "적이 여럿이면 포기"로 뭉갰었다. 그러면 가장 큰 덩어리가
    아닌 조각은 영원히 안 없어진다 — 둘 이상에게 둘러싸이는 것이 오히려 흔하다."""
    counts: dict[int, int] = {}
    for t in cluster:
        for n in gm.neighbors(t):
            o = int(gm.owner[n])
            if o < 0 or o == pid:
                continue
            counts[o] = counts.get(o, 0) + 1
    if not counts:
        return None
    best_attacker, best_troops = None, 0.0
    for a in attacks:
        if a.target != pid or a.attacker not in counts:
            continue
        if a.troops > best_troops:
            best_attacker, best_troops = a.attacker, a.troops
    if best_attacker is not None:
        return best_attacker
    return max(counts, key=lambda k: counts[k])


def is_enclosed(gm: GameMap, pid: int, start: TileRef) -> bool:
    """그 자리에서 걸어 나가 **바다나 지도 끝**에 닿을 수 있는가(닿으면 False).

    ⚠ **주인 없는 땅은 타고 걸어간다.** 내 땅 한가운데의 분화구는 구멍이지
    출구가 아니다 — 원본 주석 그대로다. 열린 물과 지도 끝만 출구다."""
    seen = {start}
    stack = [start]
    while stack:
        t = stack.pop()
        if _on_edge(gm, t):
            return False
        for n in gm.neighbors(t):
            if n in seen:
                continue
            o = int(gm.owner[n])
            if o >= 0 and o != pid:
                continue                      # 남의 땅 = 벽
            # ⚠ 위와 같은 이유로 **이쪽도 변이로 안 잡힌다.** 둘 다 원본에 있다.
            if o < 0 and gm.terrain[n] == Terrain.OCEAN:
                return False                  # 열린 물 = 출구
            seen.add(n)
            stack.append(n)
    return True


def territory_from(gm: GameMap, pid: int, start: TileRef) -> list[TileRef]:
    """`start` 에서 내 칸만 타고 닿는 영토 전체. 넘어가는 것은 이것이다."""
    seen = {start}
    stack = [start]
    out = []
    while stack:
        t = stack.pop()
        out.append(t)
        for n in gm.neighbors(t):
            if n not in seen and int(gm.owner[n]) == pid:
                seen.add(n)
                stack.append(n)
    return out
