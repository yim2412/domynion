"""병렬 작업이 기계를 다 먹지 않게 — **여유를 10% 남긴다.**

사용자 요청(2026-09-04): *"CPU·RAM·글카를 쓰는 작업은 여유 점유율 10퍼센트는
남기도록 측정해서"*. 그날 `augment_ab.py` 를 `--jobs 12`(코어 12개 전부)로 돌려
CPU 사용률이 **72.7%** 까지 올라가 있었다.

**추측하지 않고 잰다.** 코어 수만 보고 나누면 다른 프로그램이 이미 쓰고 있는 몫이
안 보인다 — `psutil` 로 지금 사용률과 남은 RAM 을 실제로 읽어 그 자리에서 정한다.

    from _budget import safe_jobs
    jobs = safe_jobs(want=24, per_job_gb=0.6)

⚠ **GPU 는 여기서 안 잰다.** 이 프로젝트에 GPU 를 쓰는 경로가 없다(순수 파이썬
시뮬레이션). 생기면 `nvidia-smi --query-gpu=memory.free` 를 같은 자리에 붙인다 —
따로 만들면 여유 비율이 두 곳으로 갈라진다.
"""

from __future__ import annotations

import os

# 남겨 둘 여유. 한 곳에만 둔다 — 도구마다 다른 값을 쓰면 "왜 이 도구만 무거운가"를
# 다시 재야 한다.
HEADROOM = 0.10


def _psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def safe_jobs(want: int = 0, per_job_gb: float = 0.6,
              sample_sec: float = 1.0) -> int:
    """지금 이 기계에서 띄워도 되는 워커 수. 최소 1.

    want 는 "많아야 이만큼"(작업 개수)이다. 0 이면 상한을 안 둔다.
    per_job_gb 는 워커 하나가 쓰는 RAM 추정치 — 이것도 상한을 만든다.
    """
    cores = os.cpu_count() or 1
    cap = int(cores * (1.0 - HEADROOM))          # 코어 기준 상한
    ps = _psutil()
    if ps is not None:
        # 이미 남이 쓰고 있는 몫을 뺀다. 사용률은 한 번 재면 튀므로 구간으로 잰다.
        idle = max(0.0, (100.0 - ps.cpu_percent(interval=sample_sec)) / 100.0)
        cap = min(cap, int(cores * max(0.0, idle - HEADROOM)))
        avail_gb = ps.virtual_memory().available / 2 ** 30
        if per_job_gb > 0:
            cap = min(cap, int(avail_gb * (1.0 - HEADROOM) / per_job_gb))
    if want > 0:
        cap = min(cap, want)
    return max(1, cap)


def report(jobs: int, per_job_gb: float = 0.6) -> str:
    """왜 그 수가 나왔는지 한 줄. **판단 근거를 안 보여 주면 추측과 구별이 안 된다.**"""
    cores = os.cpu_count() or 1
    ps = _psutil()
    if ps is None:
        return (f"병렬 {jobs} (코어 {cores}, 여유 {HEADROOM:.0%} 확보. "
                f"psutil 없음 — 실측 없이 코어 수만 봤다)")
    m = ps.virtual_memory()
    return (f"병렬 {jobs} / 코어 {cores} · CPU 사용 {ps.cpu_percent():.0f}% · "
            f"RAM 여유 {m.available / 2 ** 30:.1f}GB "
            f"(워커당 {per_job_gb}GB 가정, 여유 {HEADROOM:.0%} 확보)")
