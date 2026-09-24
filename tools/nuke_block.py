"""핵이 **왜 안 나가는가** — 관문마다 몇 번 막혔는지 센다.

    python tools/nuke_block.py --seed 3 --ticks 24000 --progress 1000

새 기준선(§5.129)에서 누적 핵이 구간마다 **0 으로 길게 멈춘다**(seed 1 은
22k~27k 다섯 구간, seed 3 은 8k~11k 네 구간). 그러다 한꺼번에 +18 처럼 터진다.
*"못 쏘는 것"* 과 *"모았다 쏘는 것"* 은 다른 규칙이라 갈라야 한다.

⚠ **난이도를 확인하고 읽을 것.** SAM 을 물량으로 뚫는 일제 사격
(`_destroy_enemy_sam`)은 **`impossible` 전용**이다. 기준선은 `medium` 이라 그
경로가 아예 안 돈다 — 곡선 모양만 보고 *"일제 사격이라 그렇다"* 고 읽으면
틀린다(2026-09-06 에 실제로 그렇게 읽었다가 코드에서 잡았다).

세는 자리는 `NationNukeBehavior.maybe_send` 의 관문들이다. **로직을 다시 쓰지 않는다** —
헬퍼의 **실제 반환값**을 보고 센다. 다시 쓰면 세는 것과 도는 것이 갈라진다.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _budget import be_nice                        # noqa: E402
from _budget import report as budget_report        # noqa: E402
from _budget import safe_jobs                      # noqa: E402

from domynion.ai import nation                       # noqa: E402
from domynion.ai.nukes import NationNukeBehavior   # noqa: E402
from domynion.core.engine import GameState           # noqa: E402
from domynion.core.units import UnitType             # noqa: E402
from domynion.core.constants import LAND_BIT as C_LAND_BIT   # noqa: E402

REASONS = (
    "죽었다",
    "사일로 없음",
    "빈 발사관 없음",       # ready_missiles <= 0 — 재장전(90 tick) 중
    "표적 없음",            # find_target
    "표적이 봇/공격 안 함",
    "탄종 없음",            # _pick_type — 골드가 여기서 걸린다
    # ↓ `쏠 칸 없음` 을 가른 것(§5.133). 앞 관문을 다 지나고도 안 쏜 경우다.
    "칸: 깨끗한 칸 0",        # _blast_is_clean 이 후보 전부를 거절
    "칸: 궤적 회피",          # 깨끗한데 점수까지 못 갔다(hard 이상의 SAM 궤적)
    "칸: SAM 50칸",           # 점수가 전부 -1 — medium 의 SAM 근접 거절
    "칸: 최근 표적",          # 점수가 -1 아래 — 최근 때린 자리 감점
    "칸: 발사 실패",          # 칸은 골랐는데 launch_nuke 가 None
    "**발사**",
)

# 깨끗한 칸 0 일 때 **무엇이** 테두리에 걸렸나 — 칸 하나당 첫 위반 소유자
BLOCKERS = ("다른 나라", "무주지(땅)", "바다")


def run(seed: int, ticks: int, nations: int, bots: int, size: str,
        difficulty: str, bucket: int, progress: int) -> dict:
    """판 하나를 돌리고 (버킷, 사유) → 횟수를 돌려준다.

    ⚠ **워커 프로세스에서 돈다.** 관문 세기는 `NationNukeBehavior.maybe_send`
    를 갈아 끼우는 것이라 프로세스마다 따로 걸어야 한다 — 부모에서 한 번 걸고
    자식이 물려받기를 기대하면 Windows(spawn)에서는 안 걸린다."""
    tally: Counter[tuple[int, str]] = Counter()
    blockers: Counter[str] = Counter()
    orig = NationNukeBehavior.maybe_send
    orig_clean = NationNukeBehavior._blast_is_clean
    orig_score = NationNukeBehavior.tile_score
    probe = {"clean": 0, "scores": []}
    clean, score = make_probes(probe, blockers)

    def counted(self, st, should_attack) -> bool:
        b = st.tick_count // bucket * bucket
        p = st.players.get(self.pid)
        if p is None or not p.alive:
            tally[(b, "죽었다")] += 1
            return False
        silos = [u for u in p.units.of(UnitType.MISSILE_SILO)
                 if not u.under_construction]
        if not silos:
            tally[(b, "사일로 없음")] += 1
            return orig(self, st, should_attack)
        if st.ready_missiles(self.pid) <= 0:
            tally[(b, "빈 발사관 없음")] += 1
            return orig(self, st, should_attack)
        target = self.find_target(st)
        if target is None:
            tally[(b, "표적 없음")] += 1
            return orig(self, st, should_attack)
        if target.is_bot or not should_attack(st, target.pid):
            tally[(b, "표적이 봇/공격 안 함")] += 1
            return orig(self, st, should_attack)
        if self._pick_type(st, p) is None:
            tally[(b, "탄종 없음")] += 1
            return orig(self, st, should_attack)
        probe["clean"], probe["scores"] = 0, []
        fired = orig(self, st, should_attack)
        tally[(b, "**발사**" if fired else _why_no_tile(probe))] += 1
        return fired

    NationNukeBehavior.maybe_send = counted      # type: ignore[assignment]
    NationNukeBehavior._blast_is_clean = clean   # type: ignore[assignment]
    NationNukeBehavior.tile_score = score        # type: ignore[assignment]
    try:
        t0 = time.perf_counter()
        rng = random.Random(seed)
        st = GameState.new(nations, rng, map_name="world", human=-1,
                           size=size, bots=bots)
        ai = nation.attach(st, rng, difficulty=difficulty)
        while not st.over and st.tick_count < ticks:
            st.tick()
            for b in ai:
                b.tick(st)
            if progress and st.tick_count % progress == 0:
                fired = sum(n for (bk, r), n in tally.items() if r == "**발사**")
                print(f"[seed {seed}] {st.tick_count}/{ticks} "
                      f"{time.perf_counter() - t0:.0f}초  발사 {fired}",
                      file=sys.stderr, flush=True)
        return {"seed": seed, "ticks": st.tick_count,
                "wall": round(time.perf_counter() - t0, 1),
                "tally": {f"{k[0]}|{k[1]}": v for k, v in tally.items()},
                "blockers": dict(blockers)}
    finally:
        NationNukeBehavior.maybe_send = orig     # type: ignore[assignment]
        NationNukeBehavior._blast_is_clean = orig_clean   # type: ignore[assignment]
        NationNukeBehavior.tile_score = orig_score        # type: ignore[assignment]


def make_probes(probe: dict, blockers: Counter):
    """`_blast_is_clean`·`tile_score` 를 감싸 **한 번의 칸 고르기에서 본 것**을 남긴다.

    **난수를 안 먹는 헬퍼만** 감싼다 — 후보를 다시 뽑으면 rng 를 먹어 판이
    갈린다(§5.116). 테스트도 이 함수를 그대로 쓴다(세는 것과 도는 것이 한 벌)."""
    orig_clean = NationNukeBehavior._blast_is_clean
    orig_score = NationNukeBehavior.tile_score

    def clean(self, st, tile, radius, target_pid) -> bool:
        ok = orig_clean(self, st, tile, radius, target_pid)
        if ok:
            probe["clean"] += 1
        else:
            blockers[_first_blocker(st, tile, radius, target_pid)] += 1
        return ok

    def score(self, st, tile, silos, structures, utype) -> float:
        v = orig_score(self, st, tile, silos, structures, utype)
        probe["scores"].append(v)
        return v

    return clean, score


def _why_no_tile(probe: dict) -> str:
    """앞 관문을 다 지나고 안 쐈을 때 — 어느 갈래였나.

    `_pick_tile_scored` 는 `v > -1.0` 인 칸만 고른다(원본 `bestValue = -1`).
    medium 의 SAM 근접은 **정확히 -1** 을, 최근 표적 감점은 **-1 아래**를 낸다."""
    if probe["clean"] == 0:
        return "칸: 깨끗한 칸 0"
    if not probe["scores"]:
        return "칸: 궤적 회피"
    best = max(probe["scores"])
    if best > -1.0:
        return "칸: 발사 실패"
    return "칸: SAM 50칸" if best == -1.0 else "칸: 최근 표적"


def _first_blocker(st, tile, radius, target_pid) -> str:
    """`_blast_is_clean` 과 **같은 순서로** 테두리를 돌아 첫 위반 칸의 정체를 댄다.

    판정은 원본 함수가 이미 했다 — 여기서는 *무엇이* 걸렸는지만 본다."""
    gm = st.gmap
    w, h = gm.width, gm.height
    cx, cy = tile % w, tile // w
    for r in (radius, radius // 2):
        if r <= 0:
            continue
        x0, x1, y0, y1 = cx - r, cx + r, cy - r, cy + r
        ring = [(x, y) for x in range(x0, x1 + 1) for y in (y0, y1)]
        ring += [(x, y) for y in range(y0 + 1, y1) for x in (x0, x1)]
        for x, y in ring:
            if not (0 <= x < w and 0 <= y < h):
                continue
            owner = int(gm.owner[y * w + x])
            if owner == target_pid:
                continue
            if owner >= 0:
                return "다른 나라"
            return "바다" if not bool(gm.raw[y * w + x] & C_LAND_BIT) else "무주지(땅)"
    return "?"


def _worker(a: tuple) -> dict:
    return run(*a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="핵이 왜 안 나가는가 (§7.2)")
    ap.add_argument("--seeds", type=int, nargs="+", default=[3])
    ap.add_argument("--ticks", type=int, default=24_000)
    ap.add_argument("--nations", type=int, default=72)
    ap.add_argument("--bots", type=int, default=400)
    ap.add_argument("--size", default="map")
    ap.add_argument("--difficulty", default="medium")
    ap.add_argument("--bucket", type=int, default=1000, metavar="N",
                    help="이 tick 수마다 한 줄. 기준선 진행줄과 같은 눈금으로 둔다")
    # ⚠ **한 판은 코어 하나로만 돈다**(tick 이 순차라 쪼갤 수 없다). 12코어 중
    # 하나만 쓰며 87분을 보낸 적이 있어(2026-09-06) seed 를 병렬로 돌리게 했다 —
    # 같은 벽시계에 판 수만 는다.
    ap.add_argument("--jobs", type=int, default=0, metavar="N",
                    help="0 이면 CPU·RAM 을 재서 여유 10%% 를 남기고 정한다")
    # ⚠ **긴 측정이 기계를 독차지하지 않게.** `safe_jobs` 는 시작 때 한 번만
    # 재므로 도중에 사용자가 기계를 쓰기 시작하면 못 비켜 준다 — 우선순위를
    # 내려 두면 스케줄러가 매 순간 조절한다. **결과는 안 변한다**(결정론,
    # §5.129) — 벽시계만 늘어난다.
    ap.add_argument("--nice", action=argparse.BooleanOptionalAction, default=True,
                    help="낮은 우선순위로 돈다 (기본 켬)")
    ap.add_argument("--progress", type=int, default=1000, metavar="N")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.nice:
        print(be_nice(), file=sys.stderr, flush=True)

    jobs = [(s, a.ticks, a.nations, a.bots, a.size, a.difficulty,
             a.bucket, a.progress) for s in a.seeds]
    workers = safe_jobs(want=a.jobs or len(jobs))
    print(f"시작 {time.strftime('%H:%M:%S')} · {len(jobs)}판 · "
          f"{a.size} · {a.difficulty} · {budget_report(workers)}",
          file=sys.stderr, flush=True)
    t0 = time.perf_counter()
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(_worker, jobs))
    else:
        rows = [_worker(j) for j in jobs]

    print(f"{a.size} · 나라 {a.nations} + 봇 {a.bots} · {a.difficulty} · "
          f"{a.ticks} tick · 전체 {time.perf_counter() - t0:.0f}초 "
          f"({time.strftime('%H:%M:%S')} 종료)")
    print(f"seed: {' '.join(str(s) for s in a.seeds)}")
    print()

    for r in rows:
        tally: Counter[tuple[int, str]] = Counter()
        for k, v in r["tally"].items():
            b, reason = k.split("|", 1)
            tally[(int(b), reason)] = v
        used = [x for x in REASONS if any(k[1] == x for k in tally)]
        print(f"#### seed {r['seed']} · {r['ticks']:,} tick · {r['wall']:.0f}초")
        print()
        print("| tick | " + " | ".join(used) + " |")
        print("|---" * (len(used) + 1) + "|")
        for b in range(0, r["ticks"] + 1, a.bucket):
            row = [tally.get((b, x), 0) for x in used]
            if any(row):
                print(f"| {b:,} | " + " | ".join(f"{v:,}" for v in row) + " |")
        print()
        print("| 사유 | 합계 |")
        print("|---|---|")
        for x in used:
            print(f"| {x} | {sum(n for k, n in tally.items() if k[1] == x):,} |")
        print()
        bl = r.get("blockers") or {}
        if bl:
            print("깨끗하지 않은 후보 칸 — 테두리에 **처음** 걸린 것:")
            print()
            print("| 걸린 것 | 후보 칸 수 |")
            print("|---|---|")
            for x in (*BLOCKERS, "?"):
                if bl.get(x):
                    print(f"| {x} | {bl[x]:,} |")
            print()

    print("> ⚠ **횟수는 나라마다 매 tick 세진다.** 한 나라가 오래 막히면 그 사유가"
          " 크게 나온다 — **비율이 아니라 어느 사유가 그 구간을 채우는지**를 본다.")
    if a.out:
        a.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
