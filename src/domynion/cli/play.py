"""헤드리스 시뮬레이션 — 밸런스를 눈이 아니라 수치로 본다.

    python -m domynion.cli.play --games 40 --map world
    python -m domynion.cli.play --games 240 --map world --jobs 8

**판당 10~20초다.** v0.1(0.5초)보다 30배 느린데, 지도가 1,600칸에서 37,575칸으로
커졌기 때문이다. 그래서 판을 프로세스로 나눈다 — 판끼리 완전히 독립이라 그대로 쪼개진다.

판당 노이즈가 크므로 **40판은 방향, 채택 판단은 240판**으로 본다.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from ..ai import nation, simple_ai
from ..core import constants as C
from ..core.engine import GameState


@dataclass
class MatchResult:
    seed: int
    winner: int | None
    victory: str
    seconds: float
    top_share: float
    alive_at_end: int
    wall: float


def run_match(seed: int, players: int = 4, map_name: str = "world",
              clock: str | None = None, max_seconds: float | None = None,
              ai: str = "nation", difficulty: str = "medium") -> MatchResult:
    """`clock` 을 주면 **원본의 종료 규칙**(둠스데이 클락)으로 돈다 — 시간 제한도
    지배 승리도 없이 마지막 생존자가 남을 때까지 간다."""
    t0 = time.perf_counter()
    rng = random.Random(seed)
    st = GameState.new(players, rng, map_name=map_name, human=-1)
    if clock:
        st.clock.cfg.enabled = True
        st.clock.cfg.speed = clock
    if ai == "nation":
        for p in st.players.values():
            p.kind = "nation"
            p.is_bot = False
        bots = nation.attach(st, rng, difficulty)
        step = lambda: [b.tick(st) for b in bots]
    else:
        bots = simple_ai.attach(st, rng)
        step = lambda: [b.update(st, C.TICK_DT) for b in bots]

    cap = max_seconds if max_seconds is not None else float("inf")
    while not st.over and st.elapsed < cap:
        st.tick()
        step()

    return MatchResult(
        seed=seed,
        winner=st.winner,
        victory=st.victory.value if st.victory else "무승부",
        seconds=st.elapsed,
        top_share=st.share(st.winner) if st.winner is not None else 0.0,
        alive_at_end=len(st.alive),
        wall=time.perf_counter() - t0,
    )


def _worker(args: tuple) -> MatchResult:
    return run_match(*args)


def summarize(results: list[MatchResult], wall: float, map_name: str) -> str:
    n = len(results)
    lines = [f"{map_name} · {n}판 / 벽시계 {wall:.0f}초 "
             f"(판당 실행 중앙 {statistics.median([r.wall for r in results]):.1f}초)", ""]

    for k, v in Counter(r.victory for r in results).most_common():
        lines.append(f"  {k:<10} {v:>4}판  {v / n * 100:>5.1f}%")

    secs = sorted(r.seconds for r in results)
    lines += [
        "",
        f"  판 길이   중앙 {statistics.median(secs):.0f}초  "
        f"최단 {secs[0]:.0f}  최장 {secs[-1]:.0f}",
        f"  승자 점유 중앙 {statistics.median([r.top_share for r in results]) * 100:.1f}%",
        f"  종료 시 생존 중앙 {statistics.median([r.alive_at_end for r in results]):.0f}명",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Domynion 헤드리스 밸런스 측정")
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--players", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--map", dest="map_name", default="world")
    ap.add_argument("--jobs", type=int, default=1, help="동시에 돌릴 프로세스 수")
    ap.add_argument("--clock", choices=["slow", "normal", "fast", "veryfast"],
                    help="둠스데이 클락을 켠다 (원본 종료 규칙)")
    ap.add_argument("--max-seconds", type=float,
                    help="측정용 상한. 클락을 켜면 판이 길어질 수 있다")
    ap.add_argument("--ai", choices=["nation", "simple"], default="nation",
                    help="nation = 원본 봇 이식, simple = v0.1 자체 AI")
    ap.add_argument("--difficulty", choices=list(C.DIFFICULTIES), default="medium")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    jobs = [(args.seed + i, args.players, args.map_name, args.clock, args.max_seconds,
             args.ai, args.difficulty) for i in range(args.games)]
    t0 = time.perf_counter()
    results: list[MatchResult] = []

    def note(r: MatchResult) -> None:
        results.append(r)
        if args.verbose:
            print(f"  seed {r.seed:>5}  {r.victory:<10} {r.seconds:>5.0f}초  "
                  f"P{r.winner} {r.top_share * 100:>4.1f}%  ({r.wall:.0f}초)", flush=True)

    if args.jobs > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for r in pool.map(_worker, jobs):
                note(r)
    else:
        for j in jobs:
            note(_worker(j))

    print(summarize(results, time.perf_counter() - t0, args.map_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
