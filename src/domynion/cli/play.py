"""헤드리스 시뮬레이션 — 밸런스를 눈이 아니라 수치로 본다.

UI 없이 판을 끝까지 돌려 통계를 낸다. 화면으로 보면 "빠른 것 같다"까지밖에 못 가고,
그 감으로 상수를 고치면 다음 번에 무엇이 좋아졌는지 말할 수 없다.

    python -m domynion.cli.play --games 40 --players 4

판당 노이즈가 크다. **40판은 방향을 보는 용도이고, 채택 판단은 240판**으로 본다.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass

from ..ai import simple_ai
from ..core import constants as C
from ..core.augments import AUGMENTS_BY_KEY
from ..core.engine import GameState, Victory


@dataclass
class MatchResult:
    seed: int
    winner: int | None
    victory: Victory | None
    seconds: float
    top_share: float
    augments: dict[int, dict[str, int]]
    min_cost_mult: float          # 판 전체에서 관측된 최저 비용 배율 (할인 중첩 감시)
    stops: int                    # 실제로 일어난 증강 정지 횟수 (설계값은 7회)


def run_match(seed: int, players: int, dt: float = C.TICK_DT) -> MatchResult:
    rng = random.Random(seed)
    st = GameState.new(players, rng)
    for p in st.players.values():
        p.is_ai = True                      # 헤드리스에는 사람이 없다
    bots = simple_ai.attach(st, rng)

    min_cost = 1.0
    stops = 0
    next_at = st.next_augment_at
    ticks = 0
    sample_every = max(1, int(2.0 / dt))   # 2초마다. 매 tick 재면 측정이 판보다 비싸다
    while not st.over:
        st.tick(dt)
        if not st.paused:
            for b in bots:
                b.update(st, dt)
        if st.next_augment_at != next_at:
            stops += 1                     # 정지는 같은 tick 에 풀리므로 예약 시각으로 센다
            next_at = st.next_augment_at
        ticks += 1
        if ticks % sample_every == 0:
            # 하한에 걸리는 조합이 실제로 나오는지 본다 (설계 3절 미해결 항목).
            min_cost = min(min_cost, _observed_min_cost(st))

    return MatchResult(
        seed=seed,
        winner=st.winner,
        victory=st.victory,
        seconds=st.elapsed,
        top_share=st.share(st.winner) if st.winner is not None else 0.0,
        augments={p.pid: dict(p.augments) for p in st.players.values()},
        min_cost_mult=min_cost,
        stops=stops,
    )


def _observed_min_cost(st: GameState) -> float:
    """지금 이 순간 누군가가 가진 최저 비용 배율. 0.2 하한에 닿으면 할인이 남아돈다."""
    out = 1.0
    for p in st.players.values():
        for terrain in C.TERRAIN_DEFENSE:
            if terrain is C.Terrain.WATER:
                continue
            for vs in (True, False):
                out = min(out, p.cost_mult(terrain, vs))
    return out


def summarize(results: list[MatchResult], wall: float) -> str:
    n = len(results)
    lines = [f"{n}판 / 실행 {wall:.1f}초 ({wall / n:.2f}초per판)", ""]

    kinds = Counter(r.victory.value if r.victory else "무승부" for r in results)
    for k, v in kinds.most_common():
        lines.append(f"  {k:<10} {v:>4}판  {v / n * 100:>5.1f}%")

    secs = sorted(r.seconds for r in results)
    lines += [
        "",
        f"  판 길이   중앙 {statistics.median(secs):.0f}초  "
        f"최단 {secs[0]:.0f}  최장 {secs[-1]:.0f}",
        f"  승자 점유 중앙 {statistics.median([r.top_share for r in results]) * 100:.1f}%",
        f"  증강 정지  중앙 {statistics.median([r.stops for r in results]):.0f}회 "
        f"(설계 7회)  최소 {min(r.stops for r in results)}",
    ]

    # 증강별 채택률과 승자 채택률. 둘의 차이가 그 카드의 실제 강함이다.
    taken: Counter[str] = Counter()
    won: Counter[str] = Counter()
    for r in results:
        for pid, augs in r.augments.items():
            for key, lv in augs.items():
                taken[key] += lv
                if pid == r.winner:
                    won[key] += lv
    if taken:
        lines += ["", "  증강            선택Lv   승자Lv   승자비중"]
        for key, cnt in taken.most_common():
            name = AUGMENTS_BY_KEY[key].name
            w = won.get(key, 0)
            lines.append(f"  {name:<12} {cnt:>6} {w:>8} {w / cnt * 100:>9.1f}%")

    floored = sum(1 for r in results if r.min_cost_mult <= 0.2 + 1e-9)
    lines += [
        "",
        f"  비용 하한(0.2) 도달  {floored}/{n}판  "
        f"관측 최저 배율 {min(r.min_cost_mult for r in results):.3f}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Domynion 헤드리스 밸런스 측정")
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--players", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--verbose", action="store_true", help="판마다 한 줄씩 찍는다")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    results = []
    for i in range(args.games):
        r = run_match(args.seed + i, args.players)
        results.append(r)
        if args.verbose:
            kind = r.victory.value if r.victory else "무승부"
            print(f"  seed {r.seed:>5}  {kind:<10} {r.seconds:>6.0f}초  "
                  f"승자 P{r.winner}  점유 {r.top_share * 100:.0f}%", flush=True)
    print(summarize(results, time.perf_counter() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
