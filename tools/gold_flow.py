"""골드가 어디서 들어와 어디로 나가는지 **센다** — §5.40 의 방법.

    python tools/gold_flow.py --ticks 9000 --size map

§5.49 에서 MIRV 가 한 발도 안 나가는 것이 드러났다. 값(25M)이 판의 골드
최고(15.5M)보다 크기 때문이다. **값을 만지기 전에 왜 15M 에서 멈추는지 갈라야
한다** — 수입이 모자란 것인가, 지출이 다 먹는 것인가.

§5.35 가 남긴 교훈이 이 도구의 근거다: *"공식이 맞는데 결과가 이상하면 유통량을
센다."* 대조는 `f(x)` 가 맞는지만 본다. `f` 가 몇 번 불리는지는 대조가 보지 않는
자리다.

지출 경로는 다섯 개뿐이다(`grep "gold -=" core/engine.py`):
`build_warship` · `launch_nuke` · `donate_gold` · `build` · `upgrade`.
전부 감싸서 종류별로 센다.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domynion.ai import nation                       # noqa: E402
from domynion.core.engine import GameState           # noqa: E402
from domynion.core.units import UnitType             # noqa: E402


def instrument(st: GameState) -> tuple[Counter, Counter]:
    """지출·수입을 종류별로 센다. **원래 함수를 감싼다** — 안을 고치면 그
    자체가 규칙 변경이 되고, 측정이 대상을 바꾼다."""
    spent: Counter = Counter()
    income: Counter = Counter()

    def wrap(name: str, fn, cost_of):
        def inner(*a, **k):
            before = _total_gold(st)
            out = fn(*a, **k)
            after = _total_gold(st)
            if after < before:
                spent[name] += before - after
            return out
        return inner

    def _total_gold(state: GameState) -> int:
        return sum(int(p.gold) for p in state.players.values())

    st.build_warship = wrap("전함", st.build_warship, None)      # type: ignore[method-assign]
    st.launch_nuke = wrap("핵", st.launch_nuke, None)            # type: ignore[method-assign]
    st.donate_gold = wrap("기부", st.donate_gold, None)          # type: ignore[method-assign]
    st.build = wrap("건설", st.build, None)                      # type: ignore[method-assign]
    st.upgrade = wrap("업그레이드", st.upgrade, None)            # type: ignore[method-assign]
    return spent, income


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="골드 유통량 계수(§5.40 의 방법)")
    ap.add_argument("--ticks", type=int, default=9_000)
    ap.add_argument("--size", default="map")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--nations", type=int, default=72)
    ap.add_argument("--bots", type=int, default=400)
    ap.add_argument("--difficulty", default="medium")
    ap.add_argument("--every", type=int, default=1_000,
                    help="이 간격으로 골드 곡선을 찍는다")
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    rng = random.Random(a.seed)
    st = GameState.new(a.nations, rng, map_name="world", human=-1,
                       size=a.size, bots=a.bots)
    ai = nation.attach(st, rng, difficulty=a.difficulty)
    spent, _ = instrument(st)

    print(f"{a.size} · 나라 {a.nations} + 봇 {a.bots} · {a.difficulty} · "
          f"{a.ticks} tick · seed {a.seed}")
    print("\n골드 곡선 — **최고**가 MIRV 값(25,000,000)에 닿는지가 관심사다\n")
    print("| tick | 생존 | 골드 최고 | 골드 중앙 | 골드 총합 | 사일로 |")
    print("|---|---|---|---|---|---|")

    while not st.over and st.tick_count < a.ticks:
        st.tick()
        for b in ai:
            b.tick(st)
        if st.tick_count % a.every == 0:
            golds = sorted(int(p.gold) for p in st.alive) or [0]
            silos = sum(len(list(p.units.of(UnitType.MISSILE_SILO)))
                        for p in st.players.values())
            print(f"| {st.tick_count} | {len(list(st.alive))} | "
                  f"{golds[-1]:,} | {int(statistics.median(golds)):,} | "
                  f"{sum(golds):,} | {silos} |")

    total = sum(spent.values())
    print(f"\n지출 — 전체 {total:,} · 벽시계 {time.perf_counter() - t0:.0f}초\n")
    print("| 어디로 | 골드 | 비중 |")
    print("|---|---|---|")
    for name, v in spent.most_common():
        print(f"| {name} | {v:,} | {v / max(1, total) * 100:.1f}% |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
