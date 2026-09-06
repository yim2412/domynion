"""증강 축이 **한 판에서 얼마를 벌어 줬는가** — 횟수가 아니라 값어치를 센다.

    python tools/axis_worth.py --seed 11 --ticks 12000

`tools/axis_hits.py` 가 답하는 것은 *"그 축이 걸리기는 하는가"* 뿐이다. 그 도구가
스스로 경고를 달아 뒀다 — **한 번에 큰 값이 걸리는 축은 횟수가 적어도 값어치가
크다**(`trade_gold_pct` 는 213회인데 무역선 한 척의 골드가 크다). 이 도구는
그 다음 질문에 답한다.

**A/B 로는 이 질문에 답할 수 없다.** 이유가 둘이다:

1. `augment_ab.py --focus` 는 축을 **고립시키지 못한다.** `offer()` 가 10장 중
   무작위 3장을 주므로, 원하는 카드가 안 나오면 다른 카드를 집는다. 즉 *"naval
   빌드"* 는 **naval 을 우선하는 무작위 빌드**다.
2. 카드가 판을 조금이라도 바꾸면 그 뒤 모든 것이 갈린다(나비 효과). 같은 seed 를
   켜고/끄고 돌려 차이를 봐도, **작은 축일수록 그 차이가 노이즈에 묻힌다.**

그래서 판을 **한 번만** 돌리고 회계로 답한다. 배율 `m` 을 알고 실제로 뗀 값
`lost` 를 알면 *"카드가 없었을 때의 값"* 은 역산된다:

    lost_없음 = lost / m          saved = lost_없음 - lost = lost × (1 - m) / m

⚠ **축마다 회계 지점이 다르다.** 지금은 `boat_loss_pct` 하나만 잰다 —
`engine.py :: _advance_boats` 가 뗀 값을 `ATTACK_CANCELLED` 로 흘려 주기
때문에 붙일 자리가 있다. 다른 축을 재려면 **그 축의 값이 흘러나오는 자리를
먼저 찾아야 한다.** 공통 훅은 없다(`mult()` 는 배율만 알지 무엇에 곱해지는지
모른다).

⚠ **사람 자리가 반드시 있어야 한다.** 증강은 사람만 받는다 — `human=None` 으로
재면 축이 0회로 나온다(`axis_hits.py` 머리말, 2026-09-04 실측).
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domynion.ai import nation                       # noqa: E402
from domynion.ai.nation import NationBot             # noqa: E402
from domynion.core import constants as C             # noqa: E402
from domynion.core.augments import (                 # noqa: E402
    AUGMENTS_BY_KEY, value_at,
)
from domynion.core.engine import GameState           # noqa: E402
from domynion.core.events import EventKind           # noqa: E402
from domynion.core.state import PlayerState          # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증강 축의 값어치 (§5.121 다음 질문)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--ticks", type=int, default=12_000)
    ap.add_argument("--nations", type=int, default=12)
    ap.add_argument("--bots", type=int, default=30)
    ap.add_argument("--size", default="map4x")
    ap.add_argument("--level", type=int, default=C.AUGMENT_MAX_LEVEL,
                    help="상륙전 카드의 레벨. 기본은 최대 — **가장 유리한 조건**에서도"
                         " 값어치가 없으면 카드가 꽝이라는 뜻이다")
    ap.add_argument("--progress", type=int, default=2000, metavar="N")
    a = ap.parse_args(argv)

    # 회계 — 배 퇴각으로 뗀 값과, 그 사이에 생산한 병력.
    in_boats = [False]
    retreats: list[float] = []
    standing: list[float] = []
    produced = [0.0]

    orig_adv = GameState._advance_boats
    orig_emit = GameState.emit
    orig_inc = PlayerState.troop_increase

    def advance_boats(self):
        in_boats[0] = True
        try:
            return orig_adv(self)
        finally:
            in_boats[0] = False

    def emit(self, kind, who=None, other=None, tile=None, amount=0.0, text=""):
        # ⚠ `ATTACK_CANCELLED` 는 **육상 공격 취소**에서도 나온다(engine :2511).
        # 배에서 온 것만 세려고 `_advance_boats` 안인지를 깃발로 가른다.
        if kind is EventKind.ATTACK_CANCELLED and who == 0 and in_boats[0]:
            # ⚠ **그 순간 서 있는 병력을 같이 남긴다.** 판 전체 생산량으로만 나누면
            # 드물게 큰 덩어리가 작은 비율로 보인다 — 결정에 쓰이는 것은 *"그때
            # 내 병력의 몇 %를 건졌나"* 다. 분모 하나로 결론이 뒤집히는 자리다.
            retreats.append(amount)
            standing.append(float(self.players[0].troops))
        return orig_emit(self, kind, who, other, tile, amount, text)

    def troop_increase(self, tiles):
        v = orig_inc(self, tiles)
        if self.pid == 0:
            produced[0] += v
        return v

    GameState._advance_boats = advance_boats         # type: ignore[assignment]
    GameState.emit = emit                            # type: ignore[assignment]
    PlayerState.troop_increase = troop_increase      # type: ignore[assignment]

    t0 = time.perf_counter()
    print(f"시작 {time.strftime('%H:%M:%S')}", file=sys.stderr, flush=True)
    rng = random.Random(a.seed)
    st = GameState.new(a.nations, rng, map_name="world", human=0,
                       size=a.size, bots=a.bots)
    st.spawn_phase = False
    ai = nation.attach(st, rng, difficulty="medium")
    st.players[0].difficulty = "medium"
    ai.append(NationBot(pid=0, rng=rng, difficulty="medium"))
    st.players[0].augments = {"landing": a.level}
    st.players[0].mods = None

    while not st.over and st.tick_count < a.ticks:
        st.tick()
        if st.augment_offer:
            # 드래프트는 받되 **상륙전만** 굳힌다. 다른 카드를 집으면 그 카드가
            # 판을 키워 회계의 분모(생산 병력)가 축과 무관하게 달라진다.
            pick = next((x for x in st.augment_offer if x.key == "landing"), None)
            st.choose_augment(pick.key if pick else st.augment_offer[0].key)
            continue
        for b in ai:
            b.tick(st)
        if a.progress and st.tick_count % a.progress == 0:
            print(f"  {st.tick_count}/{a.ticks} "
                  f"{time.perf_counter() - t0:.0f}초  퇴각 {len(retreats)}건",
                  file=sys.stderr, flush=True)

    m = st.players[0].mult("boat_loss_pct")
    lost = sum(retreats)
    saved = lost * (1.0 - m) / m if m else 0.0

    print(f"seed {a.seed} · {st.tick_count} tick · "
          f"{time.perf_counter() - t0:.0f}초 "
          f"({time.strftime('%H:%M:%S')} 종료) · 생존 {st.players[0].alive}")
    print()
    print(f"상륙전 Lv{a.level} · 계수 {value_at(AUGMENTS_BY_KEY['landing'], a.level):+.3f}"
          f" · 배율 {m:.3f}")
    print()
    print("| 항목 | 값 |")
    print("|---|---|")
    print(f"| 퇴각 정산 건수 | {len(retreats):,} |")
    print(f"| 실제로 뗀 병력(카드 있음) | {lost:,.0f} |")
    print(f"| 카드가 없었으면 뗐을 병력 | {lost / m if m else 0:,.0f} |")
    print(f"| **카드가 살려 낸 병력** | **{saved:,.0f}** |")
    print(f"| 판에서 생산한 병력 | {produced[0]:,.0f} |")
    share = saved / produced[0] * 100 if produced[0] else 0.0
    print(f"| **일생 생산 대비** | **{share:.4f}%** |")
    print()
    if retreats:
        print("| # | 뗀 병력 | 살린 병력 | 그때 서 있던 병력 | 서 있던 것의 % |")
        print("|---|---|---|---|---|")
        for i, (lo, st_troops) in enumerate(zip(retreats, standing), 1):
            sv = lo * (1.0 - m) / m if m else 0.0
            pct = sv / st_troops * 100 if st_troops else 0.0
            print(f"| {i} | {lo:,.0f} | {sv:,.0f} | {st_troops:,.0f} | **{pct:.1f}%** |")
        best = max((lo * (1.0 - m) / m) / s2 * 100
                   for lo, s2 in zip(retreats, standing) if s2)
        print()
        print(f"**한 건이 서 있던 병력의 최대 {best:.1f}% 를 건졌다.**")
    print()
    # ⚠ **표본 없음과 값어치 없음을 가른다.** 짧은 판에서 0건이 나오면 그것은
    # *"이 축이 꽝"* 이 아니라 *"이 판이 짧아서 못 잰다"* 다. 첫 스모크(1,200 tick)가
    # 0건인 채로 "꽝이다"를 찍었고, 그대로 뒀으면 표본 부족을 결론으로 읽었을 것이다.
    if not retreats:
        print(f"> **판정 불가 — 표본 0.** {st.tick_count:,} tick 동안 이 축이 한"
              " 번도 안 걸렸다. 판을 길게 돌려 다시 잰다(§5.121 은 8,258 tick 에서"
              " 9회였다). 0건 자체는 *걸리는 빈도가 낮다*는 뜻이지 *값어치가 없다*는"
              " 증거가 아니다.")
    elif share < 0.1:
        print("> **꽝이다.** 판에서 생산한 병력의 0.1% 도 못 벌었다. 계수를 키워도"
              " 곱해질 자리가 없으므로 **카드를 갈아야 한다.**")
    else:
        print(f"> 값어치가 있다 — 생산 병력의 {share:.2f}% 다. 카드를 유지한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
