"""변이(돌연변이) 하네스 — 소스를 일부러 깨뜨려 테스트가 잡는지 잰다.

`통과는 증거가 아니다`. 아무것도 검증하지 않는 테스트도 통과하므로, 규칙을
지키는 줄을 **일부러 지우거나 뒤집어** 스위트가 빨개지는지 확인한다.

그동안 이 하네스는 세션마다 손으로 다시 썼고, 그때마다 같은 함정에 빠졌다.
그 셋을 코드로 굳혀 둔다:

1. **시간 제한**(§5.83). A* 방문 검사를 지우는 변이가 힙을 무한히 키워 하위
   프로세스가 3.85GB 를 먹었고, "맞은 핵을 안 지운다" 변이는 한 판을 18분 넘게
   끌었다. `--timeout` 을 넘기면 **잡힌 것으로 센다** — 무한 루프는 테스트가 못
   잡은 것이 아니라 잡은 것이다.
2. **강제 종료해도 소스를 되돌린다**(§5.83). `finally` 는 `kill` 을 못 막지만
   Ctrl-C(`KeyboardInterrupt`) 와 `SIGTERM` 은 막을 수 있다. 그래도 못 되돌린
   경우를 위해 **끝날 때 `git diff --stat` 을 찍는다** — 깨끗한지 눈으로 본다.
3. **바이너리로 읽고 쓴다**(함정 표 1번). `read_text`/`write_text` 왕복이 CRLF
   파일의 줄을 두 배로 늘려 288줄 파일이 576줄이 된 채 커밋된 적이 있다.

그리고 **무동작 변이**를 네 번 만났다(§5.42 · §5.47 · §5.55 · 그 밖) — 패턴이
안 맞아 아예 안 들어갔거나, 들어갔어도 동작이 같았다. 패턴이 정확히 한 번
맞지 않으면 `INVALID` 로 갈라 센다. **`SURVIVED` 와 `INVALID` 는 뜻이 다르다** —
전자는 테스트 구멍이고 후자는 변이 자체가 틀린 것이다.

사용법:

    python tools/mutate.py --spec mutations.json
    python tools/mutate.py --spec mutations.json --timeout 180 -k rail

변이 명세(JSON) 는 목록이고 항목마다:

    {"name": "역 사거리 하한을 없앤다",
     "file": "src/domynion/core/rail.py",
     "old":  "C.TRAIN_STATION_MIN_RANGE <= d <= C.TRAIN_STATION_MAX_RANGE",
     "new":  "d <= C.TRAIN_STATION_MAX_RANGE",
     "tests": "tests/test_rail.py"}          # 없으면 --tests 기본값

⚠ **하네스가 도는 동안 `pytest` 도 실측 스크립트도 새로 띄우지 않는다**(§ 함정 표).
소스가 제자리에서 변이된 상태라 다른 프로세스가 그걸 읽는다. 실제로 세 번 당했다.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CAUGHT, SURVIVED, INVALID, TIMEOUT = "CAUGHT", "SURVIVED", "INVALID", "TIMEOUT"


@dataclass
class Mutation:
    name: str
    file: str
    old: str
    new: str
    tests: str | None = None


def load_spec(path: Path) -> list[Mutation]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Mutation(**m) for m in raw]


def _read(p: Path) -> bytes:
    # ⚠ 반드시 바이너리. 텍스트 왕복은 CRLF 파일의 줄 수를 두 배로 만든다.
    return p.read_bytes()


def _write(p: Path, data: bytes) -> None:
    p.write_bytes(data)


def apply(m: Mutation) -> tuple[Path, bytes] | None:
    """변이를 넣는다. 패턴이 **정확히 한 번** 맞지 않으면 None(=INVALID)."""
    p = ROOT / m.file
    original = _read(p)
    old, new = m.old.encode("utf-8"), m.new.encode("utf-8")
    if original.count(old) != 1:
        return None
    _write(p, original.replace(old, new))
    return p, original


def run_tests(tests: str, k: str | None, timeout: float) -> tuple[str, float]:
    """스위트를 돌린다. 반환은 (판정, 걸린 초).

    타임아웃은 `CAUGHT` 로 센다 — 무한 루프를 만드는 변이는 잡힌 것이다."""
    cmd = [sys.executable, "-m", "pytest", *tests.split(), "-q", "-x"]
    if k:
        cmd += ["-k", k]
    env = dict(os.environ)
    # stale `.pyc` 에 속은 적이 있다(§ 함정 표). 크기가 같고 간격이 짧으면
    # 파이썬이 소스 변경을 못 알아챈다 — 변이 테스트가 정확히 그 조건이다.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.monotonic()
    try:
        done = subprocess.run(
            cmd, cwd=ROOT, env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return TIMEOUT, time.monotonic() - t0
    return (SURVIVED if done.returncode == 0 else CAUGHT), time.monotonic() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description="변이 하네스")
    ap.add_argument("--spec", required=True, type=Path, help="변이 명세 JSON")
    ap.add_argument("--tests", default="tests", help="기본 테스트 대상")
    ap.add_argument("-k", default=None, help="pytest -k 표현식")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="한 변이당 초 (기본 180). 넘으면 잡힌 것으로 센다")
    args = ap.parse_args()

    muts = load_spec(args.spec)
    results: list[tuple[Mutation, str, float]] = []
    restore: tuple[Path, bytes] | None = None

    def _restore() -> None:
        nonlocal restore
        if restore is not None:
            _write(*restore)
            restore = None

    def _on_signal(signum, frame):    # SIGTERM 도 되돌린다. kill -9 는 못 막는다
        _restore()
        print(f"\n[!] 신호 {signum} — 소스를 되돌렸다", file=sys.stderr)
        sys.exit(130)

    signal.signal(signal.SIGTERM, _on_signal)

    try:
        for i, m in enumerate(muts, 1):
            print(f"[{i}/{len(muts)}] {m.name} … ", end="", flush=True)
            applied = apply(m)
            if applied is None:
                results.append((m, INVALID, 0.0))
                print(f"{INVALID} (패턴이 정확히 한 번 맞지 않는다)")
                continue
            restore = applied
            try:
                verdict, secs = run_tests(m.tests or args.tests, args.k,
                                          args.timeout)
            finally:
                _restore()
            results.append((m, verdict, secs))
            print(f"{verdict} ({secs:.0f}s)")
    except KeyboardInterrupt:
        _restore()
        print("\n[!] 중단 — 소스를 되돌렸다", file=sys.stderr)
        return 130
    finally:
        _restore()

    print("\n== 결과 ==")
    for m, verdict, secs in results:
        mark = {CAUGHT: "OK", TIMEOUT: "OK", SURVIVED: "FAIL",
                INVALID: "??"}[verdict]
        print(f"[{mark}] {verdict:9s} {secs:5.0f}s  {m.name}")

    n_bad = sum(1 for _, v, _ in results if v == SURVIVED)
    n_inv = sum(1 for _, v, _ in results if v == INVALID)
    n_to = sum(1 for _, v, _ in results if v == TIMEOUT)
    print(f"\n잡힘 {len(results) - n_bad - n_inv}건"
          f"(그중 타임아웃 {n_to}) · 살아남음 {n_bad}건 · 무효 {n_inv}건")

    # 되돌리기가 실제로 됐는지 눈으로 본다. 하네스가 남긴 변이로 하루를 날린 적이 있다.
    diff = subprocess.run(["git", "diff", "--stat"], cwd=ROOT,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    print("\n== git diff --stat (비어 있어야 정상) ==")
    print(diff.stdout.strip() or "(깨끗하다)")

    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
