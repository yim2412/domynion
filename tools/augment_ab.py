"""증강이 판을 얼마나 바꾸는가 — **같은 seed 로 켜고/끄고** 비교한다.

    python tools/augment_ab.py --seeds 11 13 21 --jobs 3

⚠ **진행을 찍는다.** §5.91 이 *"`--progress` 없이 돌리지 말 것"* 을 적어 뒀는데,
2026-09-04 에 같은 형태(판이 끝나야 한 줄)의 일회성 스크립트를 또 만들어
첫 실행이 10분을 넘겼다. 도구로 굳히면서 그 자리부터 고친다.

사람 자리에 `NationBot` 을 붙여 **조작하는 사람의 하한**을 흉내 낸다. 방치된
사람은 53~98초에 죽어(§5.114) 카드를 한 장도 못 받으므로 비교가 안 된다.

카드는 **한 방향으로만** 고른다(`--focus`). 무작위로 고르면 seed 마다 다른
빌드가 나와 "증강이 판을 바꿨나"와 "어느 카드가 좋나"가 섞인다.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

from domynion.ai import nation                       # noqa: E402
from domynion.ai.nation import NationBot             # noqa: E402
from domynion.core import constants as C             # noqa: E402
from domynion.core.engine import GameState           # noqa: E402

# 축 이름 → 그 축을 쓰는 카드를 우선한다. `docs/design.md` 의 빌드 방향이다.
FOCUS = {
    "troops": ("troops_cap_pct", "troops_growth_pct"),
    "cost": ("cost_vs_player_pct", "cost_vs_neutral_pct", "cost_highland_pct"),
    "defense": ("defense_pct", "defender_loss_pct"),
    "economy": ("trade_gold_pct",),
}


def run(seed: int, augments: bool, focus: str, ticks: int, nations: int,
        bots: int, size: str, progress: int) -> dict:
    t0 = time.perf_counter()
    rng = random.Random(seed)
    st = GameState.new(nations, rng, map_name="world", human=0,
                       size=size, bots=bots)
    # 시작 위치 고르기는 건너뛴다 — 비교에 필요한 것은 그 뒤다.
    st.spawn_phase = False
    ai = nation.attach(st, rng, difficulty="medium")
    st.players[0].difficulty = "medium"
    ai.append(NationBot(pid=0, rng=rng, difficulty="medium"))
    if not augments:
        st.augment_next_tick = -1
    want = FOCUS[focus]
    while not st.over and st.tick_count < ticks:
        st.tick()
        if st.augment_offer:
            pick = max(st.augment_offer, key=lambda a: a.field in want)
            st.choose_augment(pick.key)
            continue
        for b in ai:
            b.tick(st)
        if progress and st.tick_count % progress == 0:
            print(f"[{seed} {'on ' if augments else 'off'}] "
                  f"{st.tick_count}/{ticks} tick  "
                  f"{time.perf_counter() - t0:.0f}초  "
                  f"영토 {st.tiles(0)}  카드 {st.augments_taken}",
                  file=sys.stderr, flush=True)
    p = st.players[0]
    return {
        "seed": seed, "on": augments,
        "ticks": st.tick_count, "alive": p.alive,
        "tiles": st.tiles(0), "troops": int(p.troops), "gold": int(p.gold),
        "picks": st.augments_taken,
        "wall": round(time.perf_counter() - t0, 1),
    }


def _worker(a: tuple) -> dict:
    return run(*a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증강 A/B (§5.114)")
    ap.add_argument("--seeds", type=int, nargs="+", default=[11, 13, 21])
    ap.add_argument("--focus", choices=sorted(FOCUS), default="troops")
    ap.add_argument("--ticks", type=int, default=12_000)
    ap.add_argument("--nations", type=int, default=12)
    ap.add_argument("--bots", type=int, default=30)
    ap.add_argument("--size", default="map4x")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--progress", type=int, default=2000, metavar="N")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)

    jobs = [(s, on, a.focus, a.ticks, a.nations, a.bots, a.size, a.progress)
            for s in a.seeds for on in (False, True)]
    print(f"시작 {time.strftime('%H:%M:%S')} · {len(jobs)}판",
          file=sys.stderr, flush=True)
    t0 = time.perf_counter()
    if a.jobs > 1:
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            rows = list(ex.map(_worker, jobs))
    else:
        rows = [_worker(j) for j in jobs]

    total = time.perf_counter() - t0
    print(f"{a.size} · 나라 {a.nations} + 봇 {a.bots} · {a.focus} 빌드 · "
          f"{a.ticks} tick · 전체 {total:.0f}초 "
          f"({time.strftime('%H:%M:%S')} 종료)")
    print("| seed | 증강 | 카드 | tick | 생존 | 영토 | 병력 | 골드 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["seed"], r["on"])):
        print(f"| {r['seed']} | {'켜고' if r['on'] else '끄고'} | {r['picks']} | "
              f"{r['ticks']} | {'○' if r['alive'] else '×'} | {r['tiles']:,} | "
              f"{r['troops']:,} | {r['gold']:,} |")

    def med(on: bool, key: str) -> float:
        return statistics.median([r[key] for r in rows if r["on"] is on])

    print(f"| **중앙** | 끄고 | | {med(False,'ticks'):.0f} | | "
          f"{med(False,'tiles'):,.0f} | {med(False,'troops'):,.0f} | "
          f"{med(False,'gold'):,.0f} |")
    print(f"| **중앙** | 켜고 | | {med(True,'ticks'):.0f} | | "
          f"{med(True,'tiles'):,.0f} | {med(True,'troops'):,.0f} | "
          f"{med(True,'gold'):,.0f} |")
    print()
    print("> ⚠ **판이 끝난 tick 이 다르면 영토·병력을 그대로 견주면 안 된다** — "
          "오래 산 쪽이 당연히 크다. 생존(○/×)을 먼저 본다.")
    if a.out:
        a.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
