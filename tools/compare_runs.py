"""두 판을 **tick 별로** 맞춰 어디서 갈라지는지 찾는다.

    python tools/compare_runs.py A.log B.log

`balance.py --progress N` 이 stderr 로 흘린 진행줄을 읽어, 두 실행의 **공통
지점**(seed × tick)을 전부 대조한다. 최종표만 보면 *"달랐다"* 까지밖에 모르는데
이 도구는 **몇 tick 에서 갈렸는지**를 준다 — 원인을 좁히는 데 그 차이가 크다.

⚠ **이 프로젝트는 결정론이 깨진 적이 있다**(§5.129). AI 가 배를 `id()` 로
알아봐서, 죽은 배의 주소를 물려받은 새 배가 무시됐다. 어떤 주소가 재사용되는지는
**프로세스의 할당 이력**에 달려 있어, **계측을 얹는 것만으로도 판이 갈렸다.**
그래서 결정론 검증은 *"같은 명령 두 번"* 으로는 약하다 — **할당 패턴을 일부러
다르게** 해야 한다:

    python tools/balance.py ... --progress 1000 > A.log 2>&1
    python tools/balance.py ... --progress 500  > B.log 2>&1
    python tools/compare_runs.py A.log B.log

`--progress` 만 다르면 규칙은 같고 임시 객체 수만 달라진다. 그러고도 공통
지점이 전부 같으면 판이 할당 이력에 안 기댄다는 뜻이다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LINE = re.compile(r"\[seed (\d+)\] (\d+)/\d+ tick.*?생존 (\d+)(?:\s+핵 (\d+))?")


def load(path: Path) -> dict[tuple[int, int], tuple[int, int | None]]:
    out: dict[tuple[int, int], tuple[int, int | None]] = {}
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(ln.strip())
        if m:
            out[(int(m[1]), int(m[2]))] = (
                int(m[3]), int(m[4]) if m[4] is not None else None)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="두 판을 tick 별로 대조 (§5.129)")
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--show", type=int, default=8, metavar="N",
                    help="불일치를 몇 개까지 보여줄 것인가")
    args = ap.parse_args(argv)

    a, b = load(args.a), load(args.b)
    common = sorted(set(a) & set(b))
    if not common:
        # ⚠ **공통 지점 0 을 "일치"로 읽으면 안 된다.** `--progress` 가 서로
        # 배수가 아니면 겹치는 tick 이 하나도 없고, 그때 이 도구는 아무것도
        # 재지 않은 채 초록불을 낸다.
        print("[FAIL] 공통 지점이 없다 — `--progress` 가 서로 배수인지 본다")
        return 2

    # ⚠ **없는 값과 다른 값을 섞지 않는다.** 옛 로그에는 누적 핵 칸이 없어
    # `None` 인데, 그걸 그대로 견주면 **모든 지점이 불일치**로 나온다(2026-09-06
    # 자기 검증에서 70/70 이 빨갛게 떴다 — 실제로 다른 것은 한 seed 뿐이었다).
    # 양쪽에 **있는 칸만** 대조한다.
    def differs(x, y) -> bool:
        if x[0] != y[0]:
            return True
        return x[1] is not None and y[1] is not None and x[1] != y[1]

    bad = [(k, a[k], b[k]) for k in common if differs(a[k], b[k])]
    seeds = sorted({s for s, _ in common})
    print(f"공통 지점 {len(common)}개 (seed {seeds}) · 불일치 {len(bad)}개")
    for k, x, y in bad[:args.show]:
        def cell(v):
            return f"생존 {v[0]}" + ("" if v[1] is None else f", 핵 {v[1]}")
        print(f"  seed {k[0]} tick {k[1]:,}: A({cell(x)}) vs B({cell(y)})")
    if len(bad) > args.show:
        print(f"  … 그리고 {len(bad) - args.show}개 더")
    if bad:
        first = min(bad, key=lambda r: (r[0][0], r[0][1]))
        print(f"\n[FAIL] **처음 갈린 곳: seed {first[0][0]} · tick {first[0][1]:,}**")
        return 1
    print("\n[OK] 공통 지점이 전부 같다 — 이 구간에서는 결정론이 지켜졌다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
