"""§5.48 기준선을 **재현 가능하게** 다시 재는 도구.

    python tools/balance.py --seeds 1 2 3 4 --jobs 4

§5.48 의 표(핵 발사 · 생존 · 골드 최고/중앙 · 벽시계)는 그때그때 만든 스크립트로
뽑았고 남지 않았다. 규칙을 건드릴 때마다 같은 표를 다시 떠야 하므로 도구로 굳힌다.

기본값이 곧 그 기준선이다 — `map`(2000×1000) · 나라 72 + 봇 400 · medium ·
9,000 tick. **판당 20분**이라 seed 는 병렬로 돌린다(§5.48: 병렬 3판이 단독 대비
1.2배). 한 판으로는 아무것도 판정하지 않는다(§5.46).
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

# 파이프로 넘어갈 때의 버퍼링을 끈다(`tools/gold_flow.py` 의 주석 참조).
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

from _budget import report as budget_report          # noqa: E402
from _budget import safe_jobs                        # noqa: E402
from domynion.ai import nation                       # noqa: E402
from domynion.core.engine import GameState           # noqa: E402
from domynion.core.units import UnitType             # noqa: E402

NATIONS = 72
BOTS = 400
TICKS = 9_000


def run(seed: int, size: str, difficulty: str, ticks: int,
        nations: int, bots: int, clock: str | None = None,
        progress: int = 0) -> dict:
    t0 = time.perf_counter()
    c0 = time.process_time()
    rng = random.Random(seed)
    st = GameState.new(nations, rng, map_name="world", human=-1,
                       size=size, bots=bots)
    # ⚠ **적재와 루프를 가른다.** §5.91 이 "벽시계 9시간 12분" 과 "판당
    # 3,419~3,782초" 를 나란히 적어 두고 **8배 어긋난 채로** 남겼다. 어느
    # 구간이 그 차이를 냈는지 재는 자리가 없었기 때문이다 — 도구가 낸 전체는
    # 63분인데 시계는 9시간이었다. 이제 셋을 따로 찍는다:
    #   load  = 지도 적재(워커 셋이 동시에 2000×1000 을 읽는다)
    #   loop  = 게임 루프
    #   cpu   = 이 프로세스의 CPU 시간. **멈춤과 진행을 가르는 유일한 신호**
    #           다(§5.92). 벽시계만 크고 cpu 가 안 늘면 그건 계산이 아니라
    #           대기(스왑·경합)다.
    load = time.perf_counter() - t0
    t_loop = time.perf_counter()
    # ⚠ `clock` 을 주면 **원본의 종료 규칙**으로 돈다. 안 주면 우리가 넣은
    # 안전장치(`MATCH_SECONDS` = 900초 = 9,000 tick)가 판을 자른다 — 원본에
    # 없는 조건이므로, 그 길이를 "판의 길이"로 착각하면 안 된다(§5.55).
    if clock:
        st.clock.cfg.enabled = True
        st.clock.cfg.speed = clock
    ai = nation.attach(st, rng, difficulty=difficulty)
    # ⚠ **진행을 찍는다.** 이 도구는 세 판이 다 끝나야 표를 내므로, 그전에는
    # 얼마나 남았는지 알 방법이 없었다 — 실제로 3시간 넘게 "0/3 seed" 만 보며
    # 기다린 적이 있다(2026-08-31). 추측으로 보고하지 않으려면 실제 출력이
    # 있어야 한다(공통 규칙 §1). 워커가 다른 프로세스라 stderr 로 낸다.
    while not st.over and st.tick_count < ticks:
        st.tick()
        for b in ai:
            b.tick(st)
        if progress and st.tick_count % progress == 0:
            print(f"[seed {seed}] {st.tick_count}/{ticks} tick  "
                  f"{time.perf_counter() - t0:.0f}초"
                  f"(cpu {time.process_time() - c0:.0f})  "
                  f"생존 {len(list(st.alive))}",
                  file=sys.stderr, flush=True)

    golds = sorted(int(p.gold) for p in st.alive) or [0]
    launched = sum(p.units.constructed(UnitType.ATOM_BOMB)
                   + p.units.constructed(UnitType.HYDROGEN_BOMB)
                   for p in st.players.values())
    silos = sum(len(list(p.units.of(UnitType.MISSILE_SILO)))
                for p in st.players.values())
    return {
        "seed": seed,
        "nukes": launched,
        "mirvs": st.mirvs_launched,
        "alive": len(list(st.alive)),
        "silos": silos,
        "gold_max": golds[-1],
        "gold_median": int(statistics.median(golds)),
        "ticks": st.tick_count,
        "victory": st.victory.value if st.victory else "미종료",
        "wall": round(time.perf_counter() - t0, 1),
        "load": round(load, 1),
        "loop": round(time.perf_counter() - t_loop, 1),
        "cpu": round(time.process_time() - c0, 1),
    }


def _worker(a: tuple) -> dict:
    return run(*a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="밸런스 기준선 측정(§5.48)")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--size", default="map")
    ap.add_argument("--difficulty", default="medium")
    ap.add_argument("--clock", choices=["slow", "normal", "fast", "veryfast"],
                    default=None,
                    help="⚠ 주면 **원본의 종료 규칙**으로 돈다(둠스데이 클락). "
                         "안 주면 우리가 넣은 안전장치가 900초(9,000 tick)에 "
                         "판을 자른다 — 원본에 없는 조건이다")
    ap.add_argument("--ticks", type=int, default=TICKS)
    ap.add_argument("--nations", type=int, default=NATIONS)
    ap.add_argument("--bots", type=int, default=BOTS)
    ap.add_argument("--jobs", type=int, default=0, metavar="N",
                    help="0 이면 CPU·RAM 을 재서 여유 10%% 를 남기고 정한다")
    ap.add_argument("--out", type=Path, default=None, help="JSON 으로도 남긴다")
    ap.add_argument("--progress", type=int, default=1000, metavar="N",
                    help="N tick 마다 진행을 stderr 로 찍는다 (0이면 끈다). "
                         "판이 한 시간을 넘으므로 기본으로 켜 둔다")
    a = ap.parse_args(argv)

    jobs = [(s, a.size, a.difficulty, a.ticks, a.nations, a.bots, a.clock,
             a.progress) for s in a.seeds]
    # ⚠ **시계 시각을 찍는다.** §5.91 의 "9시간 12분" 은 사람이 시계를 보고
    # 적은 것이고 도구가 낸 "전체 3,783초"(63분)와 8배 어긋난다. 어느 쪽이
    # 맞는지 가르려면 도구가 **자기 시작·끝 시각을 스스로** 남겨야 한다.
    # 판 하나가 2000×1000 지도 + 나라 72 + 봇 400 이라 `augment_ab.py` 보다
    # 무겁다. ⚠ 1.5GB 는 **추정치이지 실측이 아니다** — 상한 쪽으로만 쓴다.
    workers = safe_jobs(want=a.jobs or len(jobs), per_job_gb=1.5)
    print(f"시작 {time.strftime('%H:%M:%S')} · {budget_report(workers, 1.5)}",
          file=sys.stderr, flush=True)
    t0 = time.perf_counter()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(_worker, jobs))
    else:
        rows = [_worker(j) for j in jobs]
    rows.sort(key=lambda r: r["seed"])

    total = time.perf_counter() - t0
    head = ("| seed | 핵 발사 | MIRV | 생존 | 사일로 | 골드 최고 | 골드 중앙 | tick | 종료 | 벽시계 |")
    print(f"{a.size} · 나라 {a.nations} + 봇 {a.bots} · {a.difficulty} · "
          f"{a.ticks} tick · 전체 {total:.0f}초 "
          f"({time.strftime('%H:%M:%S')} 종료)")
    print(head)
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['seed']} | {r['nukes']} | {r['mirvs']} | {r['alive']} | "
              f"{r['silos']} | {r['gold_max']:,} | {r['gold_median']:,} | "
              f"{r['ticks']} | {r['victory']} | {r['wall']:.0f}초 |")

    def med(k):
        return statistics.median([r[k] for r in rows])
    print(f"| **중앙** | **{med('nukes')}** | {med('mirvs')} | **{med('alive')}** | "
          f"{med('silos')} | {med('gold_max'):,.0f} | {med('gold_median'):,.0f} | | | "
          f"전체 {total:.0f}초 |")

    # 시간이 어디로 갔나 — 적재 · 루프 · CPU 를 따로 본다(§7.2 의 모순).
    print()
    print("| seed | 적재 | 루프 | 벽시계 | CPU | CPU/벽시계 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        ratio = r["cpu"] / r["wall"] if r["wall"] else 0.0
        print(f"| {r['seed']} | {r['load']:.0f}초 | {r['loop']:.0f}초 | "
              f"{r['wall']:.0f}초 | {r['cpu']:.0f}초 | {ratio:.0%} |")
    print(f"| **전체** | | | **{total:.0f}초** | | |")
    print()
    print("> `CPU/벽시계` 가 1 에 가까우면 계산 중이고, 낮으면 대기(경합·스왑)다.")

    if a.out:
        a.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
