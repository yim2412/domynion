"""각 증강 축이 **한 판에서 몇 번 곱해지는가**를 센다.

    python tools/axis_hits.py --seed 11 --ticks 12000

⚠ **계수 값이 아니라 발동 횟수를 잰다.** 아무리 센 계수도 안 걸리면 꽝이다 —
§5.114 가 카드 둘(항해술·삼림 순찰대)을 갈아끼운 이유가 *"갈 곳 없는 축을 남기면
카드가 조용히 아무 일도 안 하고, 그 사실이 화면 어디에도 안 나온다"* 였다.
그때는 **축이 코드에 있는지**만 봤는데, 있어도 **거의 안 걸릴 수 있다.**

⚠ **사람 자리가 반드시 있어야 한다.** 증강은 사람만 받으므로 `human=None` 으로
재면 `max_troops` 가 봇·나라 분기에서 **먼저 return** 해 `troops_cap_pct` 줄에
도달조차 안 한다 — 2026-09-04 첫 실행에서 그 축이 **0회**로 나왔고, 코드 버그로
읽힐 뻔했다. `augment_ab.py` 와 **같은 재료**(사람 자리에 `NationBot`)를 쓴다.
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
from domynion.ai.nation import NationBot             # noqa: E402
from domynion.core.augments import FIELDS            # noqa: E402
from domynion.core.engine import GameState           # noqa: E402
from domynion.core.state import PlayerState          # noqa: E402

# 한 장씩 들려 두는 카드. 축을 넓게 덮되 **네 장을 넘기지 않는다** — 12,000 tick
# 판에서 드래프트로 받을 수 있는 수와 같게 둬야 실제 판과 비교가 된다(§5.116).
SEED_CARDS = {"fertile": 1, "conscript": 1, "traders": 1, "landing": 1}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증강 축별 발동 횟수 (§5.121)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--ticks", type=int, default=12_000)
    ap.add_argument("--nations", type=int, default=12)
    ap.add_argument("--bots", type=int, default=30)
    ap.add_argument("--size", default="map4x")
    ap.add_argument("--progress", type=int, default=2000, metavar="N")
    a = ap.parse_args(argv)

    hits: Counter[str] = Counter()
    orig = PlayerState.mult

    def counting(self, field):
        hits[field] += 1
        return orig(self, field)

    PlayerState.mult = counting                      # type: ignore[assignment]

    t0 = time.perf_counter()
    print(f"시작 {time.strftime('%H:%M:%S')}", file=sys.stderr, flush=True)
    rng = random.Random(a.seed)
    st = GameState.new(a.nations, rng, map_name="world", human=0,
                       size=a.size, bots=a.bots)
    st.spawn_phase = False
    ai = nation.attach(st, rng, difficulty="medium")
    st.players[0].difficulty = "medium"
    ai.append(NationBot(pid=0, rng=rng, difficulty="medium"))
    st.players[0].augments = dict(SEED_CARDS)
    st.players[0].mods = None

    while not st.over and st.tick_count < a.ticks:
        st.tick()
        if st.augment_offer:
            st.choose_augment(st.augment_offer[0].key)
            continue
        for b in ai:
            b.tick(st)
        if a.progress and st.tick_count % a.progress == 0:
            print(f"  {st.tick_count}/{a.ticks} "
                  f"{time.perf_counter() - t0:.0f}초",
                  file=sys.stderr, flush=True)

    print(f"seed {a.seed} · {st.tick_count} tick · "
          f"{time.perf_counter() - t0:.0f}초 "
          f"({time.strftime('%H:%M:%S')} 종료)")
    print("| 축 | 발동 횟수 |")
    print("|---|---|")
    for f, n in hits.most_common():
        print(f"| {f} | {n:,} |")
    zero = [f for f in FIELDS if f not in hits]
    print()
    print("**한 번도 안 걸린 축:**", ", ".join(zero) if zero else "없다")
    print()
    print("> ⚠ **횟수만으로 카드의 세기를 판정하면 안 된다.** 한 번에 큰 값이 "
          "걸리는 축(`trade_gold_pct` — 무역선 도착)은 횟수가 적어도 값어치가 "
          "크다. 이 표가 답하는 것은 *\"그 축이 **걸리기는 하는가**\"* 다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
