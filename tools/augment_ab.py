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
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

from _budget import report as budget_report          # noqa: E402
from _budget import safe_jobs                        # noqa: E402
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
    # ⚠ **꽝인지 재기 위한 축이다.** §5.121 실측에서 `boat_loss_pct` 는 한 판에
    # 9번밖에 안 걸렸다(정예 병단의 1/38,000). 카드를 갈아끼울지 판단하려면
    # "이 축만 밀었을 때 판이 달라지는가"를 재야 한다 — 안 달라지면 꽝이다.
    "naval": ("boat_loss_pct",),
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
    # 0 = 재서 정한다(여유 10%). 숫자를 주면 그 값을 쓰되 상한은 그대로 건다 —
    # 이 규칙을 우회할 수 있게 두면 급할 때 반드시 우회한다.
    ap.add_argument("--jobs", type=int, default=0, metavar="N",
                    help="0 이면 CPU·RAM 을 재서 여유 10%% 를 남기고 정한다")
    ap.add_argument("--progress", type=int, default=2000, metavar="N")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)

    jobs = [(s, on, a.focus, a.ticks, a.nations, a.bots, a.size, a.progress)
            for s in a.seeds for on in (False, True)]
    workers = safe_jobs(want=a.jobs or len(jobs))
    print(f"시작 {time.strftime('%H:%M:%S')} · {len(jobs)}판 · "
          f"{budget_report(workers)}", file=sys.stderr, flush=True)
    t0 = time.perf_counter()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
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

    # ⚠ 죽은 판의 0 을 섞은 중앙값은 **아무 말도 못 한다.** 2026-09-04 첫 실행에서
    # 6판 중 4판이 죽어 켜고·끄고 중앙값이 나란히 0 이 나왔다. 생존을 먼저 세고,
    # 크기는 **살아남은 판끼리만** 견준다.
    def alive_rate(on: bool) -> tuple[int, int]:
        g = [r for r in rows if r["on"] is on]
        return sum(1 for r in g if r["alive"]), len(g)

    def med_alive(on: bool, key: str) -> float | None:
        v = [r[key] for r in rows if r["on"] is on and r["alive"]]
        return statistics.median(v) if v else None

    def cell(x: float | None) -> str:
        return "—" if x is None else f"{x:,.0f}"

    for on in (False, True):
        k, n = alive_rate(on)
        print(f"| **생존판 중앙** | {'켜고' if on else '끄고'} | | | "
              f"**{k}/{n}** | {cell(med_alive(on,'tiles'))} | "
              f"{cell(med_alive(on,'troops'))} | {cell(med_alive(on,'gold'))} |")
    print()

    # 짝 판정. 같은 seed 를 양쪽으로 돌렸으므로 **넷 중 하나**가 된다.
    # "켜고만 생존" 이 "끄고만 생존" 보다 많아야 증강이 유리하다는 뜻이다.
    tally = {"켜고만 생존": 0, "끄고만 생존": 0, "둘 다 생존": 0, "둘 다 죽음": 0}
    for s in a.seeds:
        off = next(r for r in rows if r["seed"] == s and not r["on"])
        on_ = next(r for r in rows if r["seed"] == s and r["on"])
        if on_["alive"] and off["alive"]:
            tally["둘 다 생존"] += 1
        elif on_["alive"]:
            tally["켜고만 생존"] += 1
        elif off["alive"]:
            tally["끄고만 생존"] += 1
        else:
            tally["둘 다 죽음"] += 1
    print("| 짝 결과 | 판 수 |")
    print("|---|---|")
    for k, v in tally.items():
        print(f"| {k} | {v} |")
    print()
    print("> ⚠ **판이 끝난 tick 이 다르면 영토·병력을 그대로 견주면 안 된다** — "
          "오래 산 쪽이 당연히 크다. 생존(○/×)을 먼저 본다.")
    print("> ⚠ **둘 다 죽음이 많으면 표가 증강이 아니라 봇의 사망률을 재고 있다.** "
          "그 경우 seed 를 늘리기 전에 재료(사람 자리 봇)를 먼저 본다.")
    if a.out:
        a.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
