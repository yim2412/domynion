"""영토 썩음 — 원본 `DoomsdayClockExecution.rot` / `speckle` / `spread`.

⚠ **이식 누락 서른넷.** 우리는 둠스데이 마감(150초)에 영토를 **한 번에** 지웠다.
원본은 매초 `⌈남은칸/남은초⌉` 씩 **먹어 들어가서** 마감에 0이 되게 한다.

차이가 셋이나 된다:

1. **점진적이다.** 썩는 나라는 150초에 걸쳐 줄어들고, 그동안 상한·수입이 같이
   줄어든다. 한 번에 지우면 마지막 순간까지 멀쩡하다가 사라진다.
2. **썩은 칸은 낙진(황무지)이 된다.** 원본 주석이 이유를 적어 뒀다 —
   *"Wasteland, not a prize: plain relinquish left neutral land the biggest
   neighbour absorbed for free — rot was feeding the one side it never presses."*
   우리 즉시 소멸은 그 땅을 **이웃에게 공짜로** 줬다.
3. **모양이 있다.** 안쪽부터 구멍이 뚫리고(speckle) 그 구멍이 **끝에서부터**
   번진다(spread). 원본이 이 둘을 나눈 이유도 주석에 있다: 균등 선택은 둥근
   덩어리(Eden 성장)를 만들고, 끝을 우선하면 손가락처럼 갈라진다.

노이즈 둘은 **성격이 반대**이고 원본이 그걸 못 박아 뒀다:

- `speckle` 은 **해싱하지 않은** 격자다. x·y 에 선형이라 값이 고르게 퍼지고,
  가장 낮은 것부터 고르면 점들이 서로 안 붙는다(실측 0% 대 백색잡음 32%).
- `front` 는 **반드시 해싱한다.** 격자는 줄무늬라 그걸로 전선을 정렬하면
  줄을 따라 걸어 80×21 짜리 실이 된다.
"""

from __future__ import annotations

from .gamemap import GameMap, TileRef

ROT_NOISE_SCALE = 1 << 16

_R2_X = 3242174889
_R2_Y = 2447445413
_GOLDEN = 0x9E3779B9
_MASK32 = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    """`Math.imul` — **32비트 곱**이다. 파이썬 정수는 무한이라 잘라야 같은 값이 된다."""
    return (a * b) & _MASK32


def speckle_noise(x: int, y: int, salt: int) -> int:
    """`rotSpeckleNoise` — 고르게 퍼지는 값. **일부러 해싱하지 않는다.**"""
    h = (_imul(x, _R2_X) + _imul(y, _R2_Y) + _imul(salt, _GOLDEN)) & _MASK32
    return (h >> 16) % ROT_NOISE_SCALE


def front_noise(tile: int, salt: int) -> int:
    """`rotFrontNoise` — 구조 없는 값. **반드시 해싱한다**(위 주석 참조)."""
    h = (_imul(tile, 0x27D4EB2D) ^ _imul(salt, _GOLDEN)) & _MASK32
    h ^= h >> 15
    h = _imul(h, 0x2545F491)
    h ^= h >> 13
    return (h >> 16) % ROT_NOISE_SCALE


class RotState:
    """한 나라의 썩음 진행. 회복하면 통째로 버린다(원본도 그렇다)."""

    __slots__ = ("since_tick", "held", "front")

    def __init__(self, since_tick: int, held: int):
        self.since_tick = since_tick
        self.held = held                      # 시작 시점 영토 — 알갱이 밀도의 기준
        self.front: dict[TileRef, int] = {}   # 타일 → 썩은 이웃 수


def _lowest_n(items, key, count: int) -> list:
    """가장 낮은 `count` 개. 원본 `LowestN` 과 같은 자리다."""
    if count <= 0:
        return []
    return [t for t, _k in sorted(((t, key(t)) for t in items),
                                  key=lambda p: p[1])[:count]]


def rot_tiles(gmap: GameMap, pid: int, owned: list[TileRef],
              border: set[TileRef], state: RotState, budget: int,
              specks: int) -> list[TileRef]:
    """이번 초에 썩을 칸들을 고른다. **고르기만 한다** — 실제로 뺏는 것은 엔진이다.

    순서가 규칙이다: 알갱이 → 번짐 → (전선이 마르면) 다시 알갱이. 마지막 가지가
    없으면 섬에 갇힌 덩어리에서 진행이 멈춘다(원본 주석: *"blobs walled in on
    an island"*)."""
    if budget <= 0 or not owned:
        return []
    held = set(owned)
    # 회복 없이 이어지는 전선만 남긴다 — 뺏긴 칸은 버린다
    state.front = {t: n for t, n in state.front.items() if t in held}

    picked: list[TileRef] = []

    def consume(tile: TileRef) -> None:
        picked.append(tile)
        held.discard(tile)
        state.front.pop(tile, None)
        for n in gmap.neighbors(tile):
            if n in held:
                state.front[n] = state.front.get(n, 0) + 1

    def do_speckle(count: int) -> None:
        """**안쪽을 먼저** 뚫는다 — 가장자리부터 갉으면 그냥 국경이 밀리는 것과
        같아 보인다."""
        if count <= 0:
            return
        inside = [t for t in held if t not in border]
        edge = [t for t in held if t in border]
        w = gmap.width
        key = lambda t: speckle_noise(t % w, t // w, pid)   # noqa: E731
        chosen = _lowest_n(inside, key, count)
        if len(chosen) < count:
            chosen += _lowest_n(edge, key, count - len(chosen))
        for t in chosen[:count]:
            consume(t)

    def do_spread(count: int) -> int:
        """구멍을 **끝에서부터** 키운다. 썩은 이웃이 하나뿐인 칸이 셋인 칸보다 먼저다.

        원본 주석: 균등 선택은 둥근 덩어리를 만들고(Eden 성장), 끝을 우선하면
        손가락처럼 갈라진다. 노이즈는 동점을 깨서 깔끔한 마름모가 되는 것을 막는다."""
        if count <= 0 or not state.front:
            return 0
        items = list(state.front.items())
        ranked = sorted(items,
                        key=lambda kv: kv[1] * ROT_NOISE_SCALE
                        + front_noise(kv[0], pid))
        taken = 0
        for tile, _n in ranked:
            if taken == count:
                break
            if tile in held:
                consume(tile)
                taken += 1
        return taken

    n = min(specks, budget)
    before = len(picked)
    do_speckle(n)
    budget -= len(picked) - before

    budget -= do_spread(budget)

    if budget > 0:                            # 전선이 말랐다 — 다시 뚫는다
        do_speckle(budget)
    return picked
