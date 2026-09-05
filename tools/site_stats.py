#!/usr/bin/env python
"""docs/index.html 의 통계 다섯 개를 실측해서 갱신한다.

손으로 적어 두면 썩는다 — 2026-09-05 에 다섯 중 셋이 틀어져 있었다
(커밋 263→264 · 변이 309→근거 없음 · 문서 10,747→어느 조합으로도 안 나옴).
페이지가 내건 "수치는 전부 실측" 을 구호가 아니라 장치로 만드는 것이 목적이다.

    python tools/site_stats.py            # 재서 보여만 준다
    python tools/site_stats.py --write    # index.html 을 갱신한다
    python tools/site_stats.py --check    # 어긋나면 exit 1 (커밋 전 확인용)

갱신 대상은 `data-stat="<키>"` 가 붙은 요소의 내용뿐이다. 마커가 없으면
아무것도 안 건드리고 그 사실을 말한다 — 정규식이 엉뚱한 자리를 고치는 것보다 낫다.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "index.html"
PORT_DOC = ROOT / "docs" / "implementation-log.md"
MUT_LOG = ROOT / "docs" / "mutation-log.tsv"

# 변이 하네스가 기록을 남기기 전(2026-09-05)까지의 누적. 이 값의 근거는
# implementation-log.md §7.1 한 줄뿐이라 소급할 수 없다 — 그래서 상수로 박고,
# 이후 실행분만 mutation-log.tsv 에서 더한다.
MUT_BASELINE_DEFAULT = 278


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 실패: {(done.stderr or '').strip()}")
    return (done.stdout or "").strip()


def commits() -> int:
    return int(_git("rev-list", "--count", "HEAD"))


def doc_lines() -> int:
    """추적 중인 .md 의 줄 수 합. of/ · node_modules · 캐시가 자동으로 빠진다."""
    total = 0
    for rel in _git("ls-files", "*.md").splitlines():
        p = ROOT / rel
        if p.is_file():
            total += len(p.read_text(encoding="utf-8", errors="replace").splitlines())
    return total


def ported_gaps() -> int:
    """메운 이식 누락. 출처는 implementation-log.md §7.1 한 곳뿐이다."""
    text = PORT_DOC.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\|\s*메운 이식 누락\s*\|\s*\*\*(\d+)개", text)
    if not m:
        raise SystemExit("§7.1 에서 '메운 이식 누락' 줄을 못 찾았다 — 표를 고쳤나?")
    return int(m.group(1))


def mutations() -> tuple[int, str]:
    """누적 변이 수와 그 출처. 기록이 없으면 기준선(문서값)만 돌려준다."""
    if not MUT_LOG.exists():
        return MUT_BASELINE_DEFAULT, "기준선만 (mutation-log.tsv 없음)"
    extra = 0
    runs = 0
    for line in MUT_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        try:
            extra += int(cols[1])   # 열: 시각 \t 변이수 \t 잡힘 \t 살아남음 \t 무효 \t 명세
            runs += 1
        except (IndexError, ValueError):
            continue                # 깨진 줄 하나가 나머지를 날리지 않는다
    return MUT_BASELINE_DEFAULT + extra, f"기준선 {MUT_BASELINE_DEFAULT} + 기록 {runs}회 {extra}개"


def tests() -> int:
    done = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (done.stdout or "") + (done.stderr or "")
    m = re.search(r"(\d+) passed", out)
    if not m:
        raise SystemExit("pytest 요약에서 'N passed' 를 못 찾았다:\n" + out[-800:])
    if done.returncode != 0:
        raise SystemExit(f"테스트가 통과하지 않았다 (exit {done.returncode}) — "
                         "통과하지 않은 수를 페이지에 적지 않는다")
    return int(m.group(1))


def measure(run_tests: bool) -> dict[str, str]:
    n_mut, mut_src = mutations()
    vals = {
        "commits": f"{commits():,}",
        "ported-gaps": f"{ported_gaps():,}",
        "mutations": f"{n_mut:,}",
        "doc-lines": f"{doc_lines():,}",
        "measured-at": time.strftime("%Y-%m-%d %H:%M"),
    }
    vals["_mut_src"] = mut_src
    if run_tests:
        vals["tests"] = f"{tests():,}"
    return vals


def current(page: str) -> dict[str, list[str]]:
    """키 하나가 여러 곳에 박혀 있을 수 있다(본문에서 다시 언급하는 값).

    dict[key] = 값 하나로 접으면 **한 곳만 어긋났을 때 그게 보이지 않는다.**
    그래서 나온 순서대로 전부 모은다.
    """
    found: dict[str, list[str]] = {}
    for m in re.finditer(r'data-stat="([\w-]+)"[^>]*>(.*?)<', page, re.S):
        found.setdefault(m.group(1), []).append(m.group(2).strip())
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="docs/index.html 통계 실측·갱신")
    ap.add_argument("--write", action="store_true", help="index.html 을 갱신한다")
    ap.add_argument("--check", action="store_true", help="어긋나면 exit 1")
    ap.add_argument("--no-tests", action="store_true",
                    help="pytest 를 돌리지 않는다 (테스트 수는 건드리지 않음)")
    args = ap.parse_args()

    vals = measure(run_tests=not args.no_tests)
    mut_src = vals.pop("_mut_src")
    page = PAGE.read_text(encoding="utf-8", errors="replace")
    have = current(page)

    if not have:
        print("[!] index.html 에 data-stat 마커가 하나도 없다. 갱신할 자리를 모른다.")
        print("    <dd data-stat=\"commits\">…</dd> 형태로 표시해 두면 여기가 채운다.")
        return 2

    print(f"{'키':<14}{'페이지':>20}{'실측':>20}")
    bad = 0
    for key, val in vals.items():
        seen = have.get(key)
        if not seen:
            bad += 1
            print(f"{key:<14}{'(마커 없음)':>20}{val:>20}  ← 붙일 자리가 없다")
            continue
        for was in seen:
            ok = was == val
            # 측정 시각은 늘 다르다. "값이 낡았나" 를 묻는 --check 의 대상이 아니다.
            bad += 0 if (ok or key == "measured-at") else 1
            n = f" (x{len(seen)})" if len(seen) > 1 else ""
            print(f"{key:<14}{was:>20}{val:>20}  {'' if ok else '← 다르다'}{n}")
    print(f"\n변이 출처: {mut_src}")
    if "tests" not in vals:
        print("테스트: 안 쟀다 (--no-tests)")

    if args.write:
        for key, val in vals.items():
            # count 를 걸지 않는다 — 같은 값이 본문에서 다시 언급되는 자리가 있다
            page = re.sub(rf'(data-stat="{key}"[^>]*>)(.*?)(<)',
                          lambda m: m.group(1) + val + m.group(3), page, flags=re.S)
        PAGE.write_text(page, encoding="utf-8")
        print(f"\n갱신했다 → {PAGE.relative_to(ROOT)}")
        return 0

    if args.check and bad:
        print(f"\n[FAIL] {bad}개가 어긋난다. `--write` 로 갱신한다.")
        return 1
    print("\n[OK] 전부 일치한다." if not bad else "\n(재기만 했다. 갱신하려면 --write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
