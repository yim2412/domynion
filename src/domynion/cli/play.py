"""헤드리스 시뮬레이션 — 밸런스를 눈이 아니라 수치로 본다.

UI 없이 판을 끝까지 돌려 통계를 낸다. 화면으로 보면 "빠른 것 같다"까지밖에 못 가고,
그 감으로 상수를 고치면 다음 번에 무엇이 좋아졌는지 말할 수 없다.

    python -m domynion.cli.play --games 40 --players 4

판당 노이즈가 크다. **40판은 방향을 보는 용도이고, 채택 판단은 240판**으로 본다.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass

from ..ai import simple_ai
from ..core import constants as C
from ..core.augments import AUGMENTS_BY_KEY
from ..core.engine import GameState, Victory


@dataclass
class MatchResult:
    seed: int
    winner: int | None
    victory: Victory | None
    seconds: float
    top_share: float
    augments: dict[int, dict[str, int]]
    fill_saturated: float         # 후반 표본 중 충전율이 1.0 에 박힌 비율
    min_cost_mult: float          # 판 전체에서 관측된 최저 비용 배율 (할인 중첩 감시)
    stops: int                    # 실제로 일어난 증강 정지 횟수 (설계값은 7회)


def apply_ai_overrides(**kw: float | int | None) -> None:
    """AI 튜닝 상수를 덮어쓴다. 교착의 원인이 규칙인지 AI 인지 가르려면 AI 쪽을
    코드 수정 없이 흔들어 볼 수 있어야 한다.

    `simple_ai` 의 메서드들이 이 값을 **모듈 전역으로 조회**하기 때문에 여기서
    setattr 하면 그대로 먹는다. 이름 import 로 바꾸면 조용히 안 먹으니 주의."""
    for name, value in kw.items():
        if value is None:
            continue
        attr = name.upper()
        if not hasattr(simple_ai, attr):
            raise SystemExit(f"simple_ai 에 {attr} 가 없다 — 이름이 바뀌었는지 확인할 것")
        setattr(simple_ai, attr, value)


def apply_rule_overrides(**kw: float | None) -> None:
    """밸런스 상수를 덮어쓴다. AI 쪽(`apply_ai_overrides`)과 같은 이유다 — 채택 전에
    코드를 고치면 무엇이 기준선이었는지 알 수 없게 된다.

    `state.py` 가 `C.<이름>` 으로 **모듈 경유** 조회하기 때문에 setattr 이 먹는다.
    이름 import 로 바꾸는 순간 조용히 안 먹으니 주의."""
    for name, value in kw.items():
        if value is None:
            continue
        attr = name.upper()
        if not hasattr(C, attr):
            raise SystemExit(f"constants 에 {attr} 가 없다 — 이름이 바뀌었는지 확인할 것")
        setattr(C, attr, value)


def run_match(seed: int, players: int, dt: float = C.TICK_DT) -> MatchResult:
    rng = random.Random(seed)
    st = GameState.new(players, rng)
    for p in st.players.values():
        p.is_ai = True                      # 헤드리스에는 사람이 없다
    bots = simple_ai.attach(st, rng)

    min_cost = 1.0
    sat = samples = 0
    stops = 0
    next_at = st.next_augment_at
    ticks = 0
    sample_every = max(1, int(2.0 / dt))   # 2초마다. 매 tick 재면 측정이 판보다 비싸다
    while not st.over:
        st.tick(dt)
        if not st.paused:
            for b in bots:
                b.update(st, dt)
        if st.next_augment_at != next_at:
            stops += 1                     # 정지는 같은 tick 에 풀리므로 예약 시각으로 센다
            next_at = st.next_augment_at
        ticks += 1
        if ticks % sample_every == 0:
            # 하한에 걸리는 조합이 실제로 나오는지 본다 (설계 3절 미해결 항목).
            min_cost = min(min_cost, _observed_min_cost(st))
            if st.elapsed > 300.0:
                for p in st.alive:
                    samples += 1
                    sat += p.fill_ratio(st.tiles(p.pid)) > 0.99

    return MatchResult(
        seed=seed,
        winner=st.winner,
        victory=st.victory,
        seconds=st.elapsed,
        top_share=st.share(st.winner) if st.winner is not None else 0.0,
        augments={p.pid: dict(p.augments) for p in st.players.values()},
        fill_saturated=(sat / samples if samples else 0.0),
        min_cost_mult=min_cost,
        stops=stops,
    )


def _observed_min_cost(st: GameState) -> float:
    """지금 이 순간 누군가가 가진 최저 비용 배율. 0.2 하한에 닿으면 할인이 남아돈다."""
    out = 1.0
    for p in st.players.values():
        for terrain in C.TERRAIN_DEFENSE:
            if terrain is C.Terrain.WATER:
                continue
            for vs in (True, False):
                out = min(out, p.cost_mult(terrain, vs))
    return out


def summarize(results: list[MatchResult], wall: float) -> str:
    n = len(results)
    lines = [f"{n}판 / 실행 {wall:.1f}초 ({wall / n:.2f}초per판)", ""]

    kinds = Counter(r.victory.value if r.victory else "무승부" for r in results)
    for k, v in kinds.most_common():
        lines.append(f"  {k:<10} {v:>4}판  {v / n * 100:>5.1f}%")

    secs = sorted(r.seconds for r in results)
    lines += [
        "",
        f"  판 길이   중앙 {statistics.median(secs):.0f}초  "
        f"최단 {secs[0]:.0f}  최장 {secs[-1]:.0f}",
        f"  승자 점유 중앙 {statistics.median([r.top_share for r in results]) * 100:.1f}%",
        f"  증강 정지  중앙 {statistics.median([r.stops for r in results]):.0f}회 "
        f"(설계 7회)  최소 {min(r.stops for r in results)}",
    ]

    # 증강별 채택률과 승자 채택률. 둘의 차이가 그 카드의 실제 강함이다.
    taken: Counter[str] = Counter()
    won: Counter[str] = Counter()
    for r in results:
        for pid, augs in r.augments.items():
            for key, lv in augs.items():
                taken[key] += lv
                if pid == r.winner:
                    won[key] += lv
    if taken:
        lines += ["", "  증강            선택Lv   승자Lv   승자비중"]
        for key, cnt in taken.most_common():
            name = AUGMENTS_BY_KEY[key].name
            w = won.get(key, 0)
            lines.append(f"  {name:<12} {cnt:>6} {w:>8} {w / cnt * 100:>9.1f}%")

    floored = sum(1 for r in results if r.min_cost_mult <= 0.2 + 1e-9)
    lines += [
        "",
        f"  비용 하한(0.2) 도달  {floored}/{n}판  "
        f"관측 최저 배율 {min(r.min_cost_mult for r in results):.3f}",
    ]
    return "\n".join(lines)


def brief(results: list[MatchResult], args) -> str:
    """스윕용 한 줄. 교착 지표는 **시간 종료 비율**이다 — 판이 안 끝난다는 뜻이다."""
    n = len(results)
    timeout = sum(1 for r in results if r.victory is Victory.TIMEOUT) / n
    secs = statistics.median([r.seconds for r in results])
    floored = sum(1 for r in results if r.min_cost_mult <= 0.2 + 1e-9) / n

    taken: Counter[str] = Counter()
    won: Counter[str] = Counter()
    for r in results:
        for pid, augs in r.augments.items():
            for key, lv in augs.items():
                taken[key] += lv
                if pid == r.winner:
                    won[key] += lv

    def rate(key: str) -> float:
        return won.get(key, 0) / taken[key] * 100 if taken.get(key) else 0.0

    sat = statistics.median([r.fill_saturated for r in results])
    return (f"growth={C.TROOPS_GROWTH_RATE:<6} atk={C.DEFAULT_ATTACK_RATIO:<5} | "
            f"충전포화 {sat * 100:>5.1f}%  "
            f"시간종료 {timeout * 100:>5.1f}%  중앙 {secs:>4.0f}초  "
            f"개척단 {rate('settlers'):>4.1f}%  강행군 {rate('forced_march'):>4.1f}%  "
            f"하한 {floored * 100:>4.1f}%  n={n}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Domynion 헤드리스 밸런스 측정")
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--players", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--verbose", action="store_true", help="판마다 한 줄씩 찍는다")
    ap.add_argument("--brief", action="store_true", help="스윕용 한 줄 요약")
    ap.add_argument("--neutral-bias", type=float, help="AI: 중립 선호 (기본 1.6)")
    ap.add_argument("--max-concurrent", type=int, help="AI: 동시 부대 수 (기본 1)")
    ap.add_argument("--launch-fill", type=float, help="AI: 출정 충전율 (기본 0.35)")
    ap.add_argument("--growth-rate", type=float, help="규칙: 병력 성장률 (기본 0.085)")
    ap.add_argument("--attack-ratio", type=float, help="규칙: 공격 투입 비율 (기본 0.25)")
    args = ap.parse_args(argv)

    apply_rule_overrides(troops_growth_rate=args.growth_rate,
                         default_attack_ratio=args.attack_ratio)
    apply_ai_overrides(neutral_bias=args.neutral_bias,
                       max_concurrent=args.max_concurrent,
                       launch_fill=args.launch_fill)

    t0 = time.perf_counter()
    results = []
    for i in range(args.games):
        r = run_match(args.seed + i, args.players)
        results.append(r)
        if args.verbose:
            kind = r.victory.value if r.victory else "무승부"
            print(f"  seed {r.seed:>5}  {kind:<10} {r.seconds:>6.0f}초  "
                  f"승자 P{r.winner}  점유 {r.top_share * 100:.0f}%", flush=True)
    wall = time.perf_counter() - t0
    print(brief(results, args) if args.brief else summarize(results, wall))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
