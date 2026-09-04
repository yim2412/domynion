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
import heapq
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
    # ⚠ 이 검사와 아래 상자는 **결과를 안 바꾼다**(순수 성능 가드다):
    # 길이 있으면 두 칸은 같은 바다 성분이고, 상자는 A* 가 어차피 안 가는
    # 자리를 자를 뿐이다. 지우는 변이가 살아남는 것이 정상이니 파지 말 것 —
    # 없으면 닿을 수 없는 목적지에서 바다 전체를 훑는다(판당 15초 → 91초, §5.8).
    if not (_touching_components(gmap, src) & _touching_components(gmap, dst)):
        return None

    w = gmap.width
    sx, sy = src % w, src // w
    dx_, dy_ = dst % w, dst // w
    margin = int(abs(sx - dx_) + abs(sy - dy_)) * slack + 8
    x0, x1 = min(sx, dx_) - margin, max(sx, dx_) + margin
    y0, y1 = min(sy, dy_) - margin, max(sy, dy_) + margin

    # ⚠ **A* 다. BFS 가 아니다.** 상자만으로는 부족했다 — `margin` 이 거리에
    # 비례해서(×2.5) 400칸짜리 항로면 상자가 사실상 지도 전체가 되고, BFS 가
    # 바다를 통째로 훑는다. 프로파일에서 한 번에 **0.3초**, 판 전체의 **63%** 였다.
    #
    # 휴리스틱은 맨해튼 거리다. 4방향 이동에 대해 **절대 실제 거리를 넘지 않으므로**
    # (허용적) A* 가 돌려주는 경로도 BFS 와 같이 최단이다. 다만 같은 길이의 경로가
    # 여럿일 때 **어느 것을 고르는지는 달라질 수 있다** — 항로가 조금 달라 보여도
    # 길이는 같다.
    def h(t: TileRef) -> int:
        return abs(t % w - dx_) + abs(t // w - dy_)

    prev: dict[TileRef, TileRef] = {src: src}
    g: dict[TileRef, int] = {src: 0}
    heap: list[tuple[int, int, TileRef]] = [(h(src), 0, src)]
    while heap:
        _f, gc, cur = heapq.heappop(heap)
        if gc > g.get(cur, 1 << 30):
            continue                     # 더 짧은 길로 이미 지나간 칸
        for n in gmap.neighbors(cur):
            if n == dst:
                prev[n] = cur
                path = [n]
                while path[-1] != src:
                    path.append(prev[path[-1]])
                path.reverse()
                return path[1:]
            if n in g:
                continue                 # 4방향 균일 비용이라 다시 볼 일이 없다
            if gmap.terrain[n] != Terrain.OCEAN:
                continue
            nx, ny = n % w, n // w
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            g[n] = gc + 1
            prev[n] = cur
            heapq.heappush(heap, (gc + 1 + h(n), gc + 1, n))
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


def landing_tile(gmap: GameMap, attacker: int, clicked: TileRef,
                 max_dist: int = C.LANDING_SEARCH_RANGE) -> TileRef | None:
    """상륙 **지점**을 정한다 — 원본 `closestReachableShore`.

    ⚠ **클릭한 칸이 곧 상륙 지점이 아니다.** 원본은 클릭한 칸에서 맨해튼 거리
    50 안을 훑어 이런 칸을 고른다:

    1. **육지이면서 해안**이고
    2. **클릭한 칸과 주인이 같고**(엉뚱한 나라에 상륙하지 않는다)
    3. 그 칸이 접한 바다가 **내 해안에서 물로 닿는 곳**이어야 한다

    셋째가 규칙이다. 원본 주석이 직접 적어 뒀다 — *"a shore facing a
    disconnected inland lake is never chosen"*. 내륙 호수를 낀 해안은
    거기까지 배가 못 가므로 애초에 고르지 않는다.

    전에는 **클릭한 칸을 그대로 목적지로 썼다.** 그래서 사람은 해안 한 칸을
    정확히 눌러야 했고, 조금만 안쪽을 누르면 *"배를 못 보낸다"* 만 떴다.
    원본은 그 자리에서 가장 가까운 해안으로 옮겨 준다.

    맨해튼 거리가 같은 후보가 여럿이면 **칸 번호가 작은 것**을 고른다. 원본은
    LIFO 순회 순서에 달렸는데 그건 재현할 이유가 없는 세부다 — 우리는 대신
    **결정론적이고 설명 가능한** 기준을 쓴다."""
    h, w = gmap.height, gmap.width
    cx, cy = clicked % w, clicked // w
    target = int(gmap.owner[clicked])

    x0, x1 = max(0, cx - max_dist), min(w - 1, cx + max_dist)
    y0, y1 = max(0, cy - max_dist), min(h - 1, cy + max_dist)
    # ⚠ **상자 안에서만 센다.** 해안 마스크를 지도 전체에 만들면 원본 크기에서
    # 200만 칸짜리 배열을 네 방향으로 미는 일이 **호출마다** 벌어진다
    # (`passable_mask` 가 판 시간의 28% 였던 것과 같은 함정, §5.50). 이웃을
    # 보려면 한 칸 여유가 필요해 상자를 1칸 넓혀 자른다.
    mx0, mx1 = max(0, x0 - 1), min(w - 1, x1 + 1)
    my0, my1 = max(0, y0 - 1), min(h - 1, y1 + 1)
    sub = gmap.terrain.reshape(h, w)[my0:my1 + 1, mx0:mx1 + 1]
    land = (sub >= Terrain.PLAINS) & (sub <= Terrain.MOUNTAIN)
    ocean = (sub == Terrain.OCEAN)
    touch = np.zeros(sub.shape, dtype=bool)
    touch[:, :-1] |= ocean[:, 1:]
    touch[:, 1:] |= ocean[:, :-1]
    touch[:-1, :] |= ocean[1:, :]
    touch[1:, :] |= ocean[:-1, :]

    iy, ix = y0 - my0, x0 - mx0                  # 여유분을 뺀 진짜 상자
    ny, nx = y1 - y0 + 1, x1 - x0 + 1
    owner = gmap.owner.reshape(h, w)[y0:y1 + 1, x0:x1 + 1]
    # ⚠ `touch` 는 **결과를 안 바꾼다**(순수 성능 가드다). 아래에서 후보마다
    # 다시 묻는 `_touching_components(t) & reach` 가 이미 "바다에 접했는가"를
    # 포함하기 때문이다 — 지우는 변이가 살아남는 것이 정상이니 파지 말 것.
    # 없으면 상자 안 **모든** 육지가 후보가 되어(반경 50이면 5,000칸) 칸마다
    # 성분을 묻게 된다. 실제로 변이 하네스가 이걸 잡아 확인했다.
    box = (slice(iy, iy + ny), slice(ix, ix + nx))
    ok = (owner == target) & land[box] & touch[box]
    ys, xs = np.nonzero(ok)
    if not len(ys):
        return None
    dist = np.abs(xs + x0 - cx) + np.abs(ys + y0 - cy)
    keep = dist <= max_dist
    ys, xs, dist = ys[keep], xs[keep], dist[keep]
    if not len(ys):
        return None

    # 내 해안에서 물로 닿는 바다 성분들. 여기에 접하지 않는 해안은 못 간다.
    reach: set[int] = set()
    for t in shoreline_tiles(gmap, attacker):
        reach |= _touching_components(gmap, int(t))
    if not reach:
        return None

    order = np.lexsort((((ys + y0) * w + (xs + x0)), dist))   # 거리 → 칸 번호
    for i in order:
        t = int((ys[i] + y0) * w + (xs[i] + x0))
        if _touching_components(gmap, t) & reach:
            return t
    return None


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
    _since_move: int = 0            # `lastMove` — 마지막으로 움직인 뒤 몇 tick
    retreating: bool = False
    # 퇴각 경로를 이미 새로 깔았는가. 원본은 `retreatDst ??=` 로 **한 번만** 정한다 —
    # 매 tick 다시 정하면 배가 해안을 따라 움직일 때마다 목적지가 흔들려 제자리걸음한다.
    replanned: bool = False
    done: bool = False

    # --- 격침 표시 (`wasDestroyedByEnemy` / `destroyer`) ---------------------
    #
    # 배가 목록에서 빠지는 이유는 셋이다: 도착 · 퇴각 완료 · 격침. 봇이 보복할지
    # 정하려면 셋을 구분해야 한다 — **도착에 보복하면 안 된다.** 원본은 유닛에
    # 플래그를 남기고 봇이 참조를 들고 있다가 나중에 본다. 같은 구조다.
    active: bool = True
    sunk_by: int | None = None

    @property
    def tile(self) -> TileRef:
        return self.path[min(self.step_i, len(self.path) - 1)]

    @property
    def arrived(self) -> bool:
        return self.step_i >= len(self.path) - 1

    def advance(self) -> None:
        """`ticksPerMove` tick 마다 한 칸. 원본도 우리도 1 이라 매 tick 이다.

        ⚠ 값이 1 이라 **배선이 끊겨 있어도 결과가 같았다** — 상수는 있는데
        읽는 곳이 0 이었고 여기 주석만 이름을 적어 뒀다. 값이 바뀌는 날
        한 곳만 고치면 되게 둔다."""
        self._since_move += 1
        if self._since_move < C.BOAT_TICKS_PER_MOVE:
            return
        self._since_move = 0
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
    # ⚠ **격침 횟수가 아니라 레벨이다**(0~3, `warshipMaxVeterancy`). 우리는
    # 오래 격침 한 번마다 +1 을 했고 상한도 없었다 — 수송선을 열 척 잡은 배가
    # 레벨 10 이 되어 포탄 피해가 3배였다(§5.75).
    veterancy: int = 0
    # 수송선 격침과 무역선 나포가 **같은 정수 미터**에 쌓인다(`veterancyProgress`).
    # 한 레벨 = 10 × 25 = 250점, 수송선 한 척 25점 · 무역선 한 척 10점이라
    # **수송선 10척 또는 무역선 25척 또는 그 섞임**이 정확히 한 레벨이 된다.
    # 넘친 점수는 다음 레벨로 이월된다 — 전함 격침만 이 미터를 0으로 지운다.
    veterancy_progress: int = 0
    # 지금 향하는 순찰 지점(`targetTile`). 닿으면 비우고 새로 뽑는다.
    #
    # ⚠ 이식 누락 스물둘. 이게 없어서 전함이 **태어난 자리에 붙박여 있었다** —
    # `patrol_origin` 은 필드로만 있고 아무도 배를 옮기지 않았다. 격침에서는
    # 안 드러난다(사거리 안이면 그 자리에서 쏘면 된다). 나포를 붙이니 드러났다.
    patrol_target: TileRef | None = None

    # --- 수리 후퇴 (`retreating` / `docked`) --------------------------------
    #
    # ⚠ 이식 누락 스물셋. 우리 전함은 다치면 그 자리에서 계속 싸웠다 — 원본은
    # 체력이 75% 아래로 떨어지면 항구로 돌아가 정박해 수리한다.
    retreat_port: TileRef | None = None   # None 이면 순찰 중이다
    docked: bool = False
    # 정박 회복은 소수로 나뉘므로(레벨×5 를 배들이 나눠 갖는다) 나머지를 들고
    # 간다. 안 그러면 세 척이 정박했을 때 5/3 = 1.67 이 매 tick 1 로 잘려
    # 회복량이 조용히 20% 줄어든다.
    heal_remainder: float = 0.0

    def __post_init__(self) -> None:
        if self.patrol_origin is None:
            self.patrol_origin = self.tile

    @property
    def sunk(self) -> bool:
        return self.health <= 0

    @property
    def max_health(self) -> int:
        """`maxHealthWithVeterancy` — 레벨당 기본 최대 체력의 20% 가 붙는다.

        ⚠ **정수 내림이다**(원본이 `Math.floor`). 회복·후퇴 문턱·클락 유출이 전부
        이 값을 기준으로 하므로, 여기서 부동소수를 쓰면 그 셋이 함께 어긋난다."""
        if self.veterancy <= 0:
            return C.WARSHIP_MAX_HEALTH
        return C.WARSHIP_MAX_HEALTH + (
            C.WARSHIP_MAX_HEALTH * self.veterancy
            * C.WARSHIP_VETERANCY_HEALTH_BONUS) // 100

    def _level_up(self) -> None:
        """올라도 **즉시 회복되지는 않는다**(원본 주석 그대로) — 높아진 상한을
        향해 평소대로 수리할 뿐이다."""
        if self.veterancy < C.WARSHIP_MAX_VETERANCY:
            self.veterancy += 1

    def record_kill(self, target: str) -> None:
        """`UnitImpl.recordKill` — **전함을 잡으면 즉시 한 레벨**이고, 쌓아 둔
        진행도는 지워진다. 수송선은 진행도만 준다.

        ⚠ 무역선은 여기 오지 않는다 — 격침이 아니라 나포다(§5.36)."""
        if target == "warship":
            self.veterancy_progress = 0
            self._level_up()
        elif target == "transport":
            self._add_progress(C.WARSHIP_VETERANCY_TRADE_CAPTURES)

    def record_trade_capture(self) -> None:
        self._add_progress(C.WARSHIP_VETERANCY_TRANSPORT_KILLS)

    def _add_progress(self, points: int) -> None:
        if self.veterancy >= C.WARSHIP_MAX_VETERANCY:
            return
        per_level = (C.WARSHIP_VETERANCY_TRANSPORT_KILLS
                     * C.WARSHIP_VETERANCY_TRADE_CAPTURES)
        self.veterancy_progress += points
        while (self.veterancy_progress >= per_level
               and self.veterancy < C.WARSHIP_MAX_VETERANCY):
            self.veterancy_progress -= per_level
            self._level_up()


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
    # ⚠ **골드는 "지나온 칸 수"로 매긴다**(`tilesTraveled`), 계획된 경로 길이가
    # 아니다(§5.81). 나포되면 경로가 통째로 새로 깔리는데, 그때 `len(path)` 로
    # 재면 **해적 항구까지의 짧은 거리**만 값을 쳐 준다 — 반대편 대륙까지 갔다가
    # 잡힌 배와 항구 앞에서 잡힌 배가 같은 값이 된다.
    tiles_travelled: int = 0
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
            self.tiles_travelled += 1


def trade_gold(dist: float) -> int:
    """`tradeShipGold(dist)` — 거리 300 아래는 시그모이드가 눌러 크게 손해다.

        75000 / (1 + e^(−0.03 × (거리 − 300))) + 50 × 거리
    """
    return int(75_000 / (1 + math.exp(-0.03 * (dist - C.TRADE_SHORT_RANGE_DEBUFF)))
               + 50 * dist)


def trade_spawn_rate(rejections: int, num_ships: int) -> int:
    """무역선이 뜰 확률은 `1 / 이 값`. 배가 많을수록 잘 안 뜨고, 계속 안 뜨면 보정된다."""
    decay = math.log(2) / C.TRADE_SPAWN_DECAY_HALFLIFE
    base = 1.0 - 1.0 / (1.0 + math.exp(
        -decay * (num_ships - C.TRADE_SPAWN_SIGMOID_MID)))
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
