"""이름 옆에 붙는 상태 깃발 — 원본 `client/render/frame/derive/PlayerStatus.ts`.

⚠ **이식 누락 마흔일곱.** 우리는 지도에 **이름만** 그렸다. 규칙은 전부 도는데
(배신자 · 클락 · 핵 · 동맹 · 표적 · 금수) **화면에서 구분할 수가 없었다.**

가장 나쁜 것이 `nuke_targets_me` 다 — 핵이 **나를** 향해 오는지 아닌지가 지도에
안 보였다. §5.23 에서 소식창에 `NUKE_INBOUND` 를 넣었지만 **로그는 흘러가고 지도는
남는다.** 어느 나라가 지금 나를 노리는지는 지도에서 봐야 하는 정보다.

**그리기와 분리한다.** 원본도 `derive/` 에 순수 함수로 두고 그리기는 따로다 —
그래야 Qt 없이 잴 수 있다. 이 파일에는 QPainter 가 한 줄도 없다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import constants as C
from ..core.units import UnitType


# 핵으로 치는 것들. **MIRV 탄두도 센다**(원본 `NUKE_ACTIVE_TYPES` 가
# `NUKE_TYPES` 에 `UT_MIRV_WARHEAD` 를 더한 집합이다) — 탄두가 갈라진 뒤가
# 오히려 더 위험한데 그때 표시가 꺼지면 안 된다.
# ⚠ `MIRV_WARHEAD` 를 넣어 두지만 **우리 모델에서는 안 걸린다** — 우리는 MIRV 가
# 도착한 그 tick 에 탄두를 바로 터뜨려서(`_detonate`) 날아다니는 탄두가 없다.
# 원본은 탄두도 유닛이라 잠깐 뜬다. 나중에 탄두를 유닛으로 바꾸면 여기가 이미
# 맞아 있게 두는 것이다.
NUKE_ACTIVE_TYPES = (UnitType.ATOM_BOMB, UnitType.HYDROGEN_BOMB,
                     UnitType.MIRV, UnitType.MIRV_WARHEAD)


@dataclass
class Status:
    """한 나라의 깃발들. **아무 깃발도 없으면 아예 만들지 않는다**(원본과 같다) —
    400개 나라마다 빈 상자를 만들면 그리기 전에 이미 샌다."""

    crown: bool = False
    traitor: bool = False
    traitor_remaining: int = 0
    in_clock: bool = False
    clock_draining: bool = False
    clock_warn_progress: float = 0.0
    nuke_active: bool = False
    nuke_targets_me: bool = False
    alliance: bool = False
    alliance_req: bool = False
    alliance_fraction: float = 0.0
    target: bool = False
    embargo: bool = False


def player_status(st, me: int | None = None) -> dict[int, Status]:
    """살아 있는 나라별 깃발. `me` 가 없으면 **상대적인 깃발은 전부 꺼진다**
    (원본의 리플레이 경로와 같다).

    ⚠ **핵은 유닛을 한 번만 훑는다.** 나라마다 유닛을 훑으면 400×수천이 된다 —
    원본이 주석으로 남긴 그대로다(`avoids the O(players × units) scan`).
    """
    out: dict[int, Status] = {}

    # 왕관 — 살아 있는 나라 중 **땅이 가장 많은** 쪽. 병력도 골드도 아니다.
    crown_pid, most = -1, 0
    for pid, p in st.players.items():
        if not p.alive:
            continue
        n = st.tiles(pid)
        if n > most:
            crown_pid, most = pid, n

    nuke_active: set[int] = set()
    nuke_at_me: set[int] = set()
    for n in st.nukes:
        if n.utype not in NUKE_ACTIVE_TYPES:
            continue
        nuke_active.add(n.owner)
        if me is not None and int(st.gmap.owner[n.dst]) == me:
            nuke_at_me.add(n.owner)

    d = st.diplomacy
    tick = st.tick_count
    elapsed = tick / C.TICK_HZ
    warn = st.clock.cfg.warn_seconds
    my_targets = set(st.targets_of(me)) if me is not None else set()

    for pid, p in st.players.items():
        if not p.alive:
            continue
        s = Status()
        s.crown = pid == crown_pid
        s.traitor = d.is_traitor(pid, tick)
        s.traitor_remaining = d.traitor_remaining(pid, tick) if s.traitor else 0
        since = st.clock.marked_at.get(pid)
        s.in_clock = since is not None
        if since is not None:
            under = elapsed - since
            # 경고 구간을 지나면 실제로 병력이 샌다 — 원본은 그때 해골을
            # **깜빡이지 않고 고정**한다(위험과 실제 유출을 눈으로 가른다).
            s.clock_draining = under >= warn
            s.clock_warn_progress = min(1.0, max(0.0, under / warn)) if warn else 0.0
        s.nuke_active = pid in nuke_active
        s.nuke_targets_me = pid in nuke_at_me

        if me is not None and pid != me:
            s.alliance = d.allied(me, pid)
            s.alliance_req = me in d.pending.get(pid, set())
            s.target = pid in my_targets
            # **금수는 양방향이다** — 어느 쪽이 걸었든 표시한다.
            s.embargo = d.embargoed(me, pid) or d.embargoed(pid, me)
            if s.alliance:
                al = d.alliance_between(me, pid)
                if al is not None:
                    left = max(0, al.expires_at - tick)
                    s.alliance_fraction = min(
                        1.0, left / max(1, C.ALLIANCE_DURATION_TICKS))

        if (s.crown or s.traitor or s.in_clock or s.nuke_active
                or s.nuke_targets_me or s.alliance or s.alliance_req
                or s.target or s.embargo):
            out[pid] = s
    return out


# 깃발 → 지도에 찍는 글자. **순서가 곧 우선순위다** — 이름 옆 자리가 좁아
# 여럿이 붙으면 앞에서부터 잘린다. 나를 겨눈 핵이 맨 앞인 이유다.
MARKERS: tuple[tuple[str, str], ...] = (
    ("nuke_targets_me", "☢"),
    ("clock_draining", "💀"),
    ("nuke_active", "☣"),
    ("traitor", "🗡"),
    ("crown", "👑"),
    ("alliance", "🤝"),
    ("alliance_req", "✉"),
    ("target", "🎯"),
    ("embargo", "⛔"),
)

MAX_MARKERS = 3


def markers(s: Status) -> str:
    """이름 옆에 붙일 글자들. **최대 셋**이다.

    ⚠ 전부 붙이면 이름보다 깃발이 길어져 지도가 읽히지 않는다. 원본은 아이콘을
    그려 겹치지만 우리는 글자라 폭이 그대로 든다 — 그래서 여기서 자른다."""
    got = [ch for name, ch in MARKERS if getattr(s, name)]
    return "".join(got[:MAX_MARKERS])
