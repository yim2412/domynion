"""판 하나를 `cProfile` 로 재는 도구 — **고치기 전에 먼저 돌린다.**

    python tools/profile_game.py --ticks 1200 --size map

§7 "남은 성능 자리"가 `water_path` 48% 를 1등으로 적어 두고 **전부 추측이라고**
못 박아 뒀다. 그 추측을 매번 손으로 재현하지 않으려고 도구로 굳힌다.

⚠ 이 값은 **그 기계에서 무엇이 같이 돌고 있었는가**에 좌우된다(§5.32 · 함정 10).
다른 측정을 띄워 둔 채로 돌리지 않는다.
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domynion.ai import nation                       # noqa: E402
from domynion.core.engine import GameState           # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="판 프로파일")
    ap.add_argument("--ticks", type=int, default=1200)
    ap.add_argument("--size", default="map")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--nations", type=int, default=72)
    ap.add_argument("--bots", type=int, default=400)
    ap.add_argument("--difficulty", default="medium")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)

    rng = random.Random(a.seed)
    st = GameState.new(a.nations, rng, map_name="world", human=-1,
                       size=a.size, bots=a.bots)
    ai = nation.attach(st, rng, difficulty=a.difficulty)

    def run() -> None:
        for _ in range(a.ticks):
            if st.over:
                break
            st.tick()
            for b in ai:
                b.tick(st)

    t0 = time.perf_counter()
    pr = cProfile.Profile()
    pr.enable()
    run()
    pr.disable()
    wall = time.perf_counter() - t0

    print(f"{a.size} · 나라 {a.nations} + 봇 {a.bots} · {a.difficulty} · "
          f"{st.tick_count} tick · {wall:.1f}초 "
          f"({wall / max(1, st.tick_count) * 1000:.1f}ms/tick, 프로파일러 포함)")
    stats = pstats.Stats(pr)
    stats.sort_stats("cumulative").print_stats(a.top)
    print("\n=== tottime 순 ===")
    stats.sort_stats("tottime").print_stats(a.top)
    if a.out:
        stats.dump_stats(str(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
