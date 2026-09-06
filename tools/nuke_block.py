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
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domynion.ai import nation                       # noqa: E402
from domynion.ai.nukes import NationNukeBehavior   # noqa: E402
from domynion.core.engine import GameState           # noqa: E402
from domynion.core.units import UnitType             # noqa: E402

REASONS = (
    "죽었다",
    "사일로 없음",
    "빈 발사관 없음",       # ready_missiles <= 0 — 재장전(90 tick) 중
    "표적 없음",            # find_target
    "표적이 봇/공격 안 함",
    "탄종 없음",            # _pick_type — 골드가 여기서 걸린다
    "쏠 칸 없음",
    "**발사**",
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="핵이 왜 안 나가는가 (§7.2)")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--ticks", type=int, default=24_000)
    ap.add_argument("--nations", type=int, default=72)
    ap.add_argument("--bots", type=int, default=400)
    ap.add_argument("--size", default="map")
    ap.add_argument("--difficulty", default="medium")
    ap.add_argument("--bucket", type=int, default=1000, metavar="N",
                    help="이 tick 수마다 한 줄. 기준선 진행줄과 같은 눈금으로 둔다")
    ap.add_argument("--progress", type=int, default=1000, metavar="N")
    a = ap.parse_args(argv)

    # (버킷, 사유) → 횟수
    tally: Counter[tuple[int, str]] = Counter()

    orig = NationNukeBehavior.maybe_send

    def counted(self, st, should_attack) -> bool:
        bucket = st.tick_count // a.bucket * a.bucket
        p = st.players.get(self.pid)
        if p is None or not p.alive:
            tally[(bucket, "죽었다")] += 1
            return False
        silos = [u for u in p.units.of(UnitType.MISSILE_SILO)
                 if not u.under_construction]
        if not silos:
            tally[(bucket, "사일로 없음")] += 1
            return orig(self, st, should_attack)
        if st.ready_missiles(self.pid) <= 0:
            tally[(bucket, "빈 발사관 없음")] += 1
            return orig(self, st, should_attack)
        target = self.find_target(st)
        if target is None:
            tally[(bucket, "표적 없음")] += 1
            return orig(self, st, should_attack)
        if target.is_bot or not should_attack(st, target.pid):
            tally[(bucket, "표적이 봇/공격 안 함")] += 1
            return orig(self, st, should_attack)
        if self._pick_type(st, p) is None:
            tally[(bucket, "탄종 없음")] += 1
            return orig(self, st, should_attack)
        # 여기까지 왔으면 칸 고르기와 발사만 남았다 — 결과로 가른다.
        fired = orig(self, st, should_attack)
        tally[(bucket, "**발사**" if fired else "쏠 칸 없음")] += 1
        return fired

    NationNukeBehavior.maybe_send = counted                      # type: ignore[assignment]

    t0 = time.perf_counter()
    print(f"시작 {time.strftime('%H:%M:%S')} · seed {a.seed} · {a.size} · "
          f"{a.difficulty}", file=sys.stderr, flush=True)
    rng = random.Random(a.seed)
    st = GameState.new(a.nations, rng, map_name="world", human=-1,
                       size=a.size, bots=a.bots)
    ai = nation.attach(st, rng, difficulty=a.difficulty)

    while not st.over and st.tick_count < a.ticks:
        st.tick()
        for b in ai:
            b.tick(st)
        if a.progress and st.tick_count % a.progress == 0:
            fired = sum(n for (bk, r), n in tally.items() if r == "**발사**")
            print(f"  {st.tick_count}/{a.ticks} "
                  f"{time.perf_counter() - t0:.0f}초  발사 {fired}",
                  file=sys.stderr, flush=True)

    print(f"seed {a.seed} · {st.tick_count} tick · "
          f"{time.perf_counter() - t0:.0f}초 ({time.strftime('%H:%M:%S')} 종료)")
    print()
    used = [r for r in REASONS if any(k[1] == r for k in tally)]
    print("| tick | " + " | ".join(used) + " |")
    print("|---" * (len(used) + 1) + "|")
    for bucket in range(0, st.tick_count + 1, a.bucket):
        row = [tally.get((bucket, r), 0) for r in used]
        if not any(row):
            continue
        print(f"| {bucket:,} | " + " | ".join(f"{v:,}" for v in row) + " |")
    print()
    print("| 사유 | 합계 |")
    print("|---|---|")
    for r in used:
        print(f"| {r} | {sum(n for k, n in tally.items() if k[1] == r):,} |")
    print()
    print("> ⚠ **횟수는 나라마다 매 tick 세진다.** 한 나라가 오래 막히면 그 사유가"
          " 크게 나온다 — **비율이 아니라 어느 사유가 그 구간을 채우는지**를 본다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
