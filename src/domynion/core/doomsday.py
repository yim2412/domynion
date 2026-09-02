"""둠스데이 클락 — 원본의 진짜 종료 규칙.

openfront 에는 **시간 제한도 지배 승리도 없다.** 대신 요구 점유율이 파도처럼 오르고,
그 아래로 떨어진 쪽이 병력을 흘리다가 영토가 썩어 사라진다. 배틀로얄의 자기장이다.

핵심 성질 셋 (원본 주석에 근거가 적혀 있다):

1. **처음 10분은 0% 다.** 클락은 초반 솎아내기가 아니라 **교착 해결기**다.
   그동안의 탈락은 순수하게 싸움으로 난다.
2. 이후 7개 파도로 2/4/7/11/17/25/35% 까지 **선형으로 오르고 잠깐 쉰다.** 뛰지 않는다.
3. 천장 35% 는 85판 토너먼트에서 2위 점유율이 21.6% 를 넘은 적이 없다는 실측에서 왔다.
   더 올리면 선두마저 죽기 시작한다.

바 아래로 떨어지면: 경고 30초 → 병력 유출(2%→5%) → 영토 썩음 → 150초 뒤 소멸.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# 원본 `PlayerImpl.DECAY_CUE_GRACE_TICKS`. 클락 설정(`DOOMSDAY_CLOCK_DEFAULTS`)이
# 아니라 화면 신호라 원본도 따로 두고 있다 — 여기 상단에 둔다.
DECAY_CUE_GRACE_TICKS = 30

# 파도 목표치 (basis point, 100 = 1%)
LEVELS = (200, 400, 700, 1100, 1700, 2500, 3500)
# 팀전은 같은 사다리를 높여 오른다 — 한쪽이 살아 있으면 그 편이 사는 구조라
# 같은 압력에서 더 천천히 줄어들기 때문이다.
LEVELS_TEAM = (300, 600, 1000, 1500, 2100, 2800, 3500)


@dataclass(frozen=True)
class Schedule:
    grace_seconds: int
    ramp_seconds: tuple[int, ...]
    pause_seconds: tuple[int, ...]
    levels: tuple[int, ...] = LEVELS


SCHEDULES: dict[str, Schedule] = {
    "slow":     Schedule(600, (240,) * 7, (70,) * 6 + (0,)),
    "normal":   Schedule(600, (168,) * 7, (54,) * 6 + (0,)),
    "fast":     Schedule(600, (102,) * 7, (31,) * 6 + (0,)),
    "veryfast": Schedule(600, (36,) * 7, (8,) * 6 + (0,)),
}


def required_basis_points(elapsed: float, speed: str = "normal",
                          team_game: bool = False) -> int:
    """`elapsed` 초 시점에 한 **진영**이 쥐고 있어야 하는 점유율(bp).

    유예 동안 0, 이후 파도마다 선형으로 오르고 잠시 유지한다. 원본이 정수 내림으로
    계산하는 이유는 모든 클라이언트가 같은 값을 봐야 하기 때문이다 — 우리는 단일
    프로세스라 필요 없지만, 값이 어긋나면 대조가 안 되므로 그대로 둔다."""
    s = SCHEDULES.get(speed, SCHEDULES["normal"])
    levels = LEVELS_TEAM if team_game else s.levels
    if elapsed <= s.grace_seconds:
        return 0
    t = elapsed - s.grace_seconds
    prev = 0
    for i, target in enumerate(levels):
        ramp = s.ramp_seconds[i]
        if t < ramp:
            return prev + int((target - prev) * t // ramp)
        t -= ramp
        if t < s.pause_seconds[i]:
            return target
        t -= s.pause_seconds[i]
        prev = target
    return levels[-1]


def required_tiles(elapsed: float, land_count: int, speed: str = "normal",
                   team_game: bool = False) -> int:
    bp = required_basis_points(elapsed, speed, team_game)
    return int(land_count * bp // 10_000)


@dataclass
class DoomsdayDefaults:
    """원본 `DOOMSDAY_CLOCK_DEFAULTS` 그대로."""
    enabled: bool = False           # 원본 기본값도 꺼져 있다
    speed: str = "normal"
    warn_seconds: int = 30          # 경고(깜빡임) 뒤에 유출이 시작된다
    drain_start_percent: float = 2.0
    drain_max_percent: float = 5.0
    drain_ramp_seconds: int = 90
    drain_floor_percent: float = 5.0
    floor_start_percent: float = 40.0
    floor_decay_seconds: int = 90
    rot_death_seconds: int = 150    # **비율이 아니라 마감 시각**이다
    rot_grain_seconds: int = 10
    rot_speckle_percent: float = 15.0
    # 전함은 **같은 경사를 훨씬 높은 천장까지** 오른다. 그리고 곡선이 볼록해서
    # (지수 8) 초반에는 완만하다가 끝에서 치솟는다 — 원본 주석: 처음 표시됐을 때
    # 잡힌 배는 병력만큼 버티지만, 경사를 다 오른 쪽은 **2초 만에** 배를 잃는다.
    warship_drain_start_percent: float = 1.0
    warship_drain_max_percent: float = 50.0
    warship_drain_curve_exponent: int = 8


@dataclass
class DoomsdayClock:
    """바 아래로 떨어진 쪽을 표시하고 말려 죽인다."""

    cfg: DoomsdayDefaults = field(default_factory=DoomsdayDefaults)
    marked_at: dict[int, float] = field(default_factory=dict)   # pid -> 표시된 시각(초)
    # pid -> **마지막으로 칸이 실제로 썩은 tick**(`PlayerImpl.rottedAtTick`).
    # 표시가 풀리면 같이 지운다(원본 `unmarkDoomsdayClock` 이 −1 로 되돌린다).
    rotted_at: dict[int, int] = field(default_factory=dict)

    def bar_tiles(self, elapsed: float, land_count: int,
                  team_game: bool = False) -> int:
        return required_tiles(elapsed, land_count, self.cfg.speed, team_game)

    def update(self, elapsed: float, tiles_of: dict[int, int],
               land_count: int, team_game: bool = False) -> None:
        """바 아래면 표시하고, 다시 올라오면 표시를 지운다.

        원본 주석: "the drain stops the moment it climbs back" — 되돌아오면 회복된다.

        ⚠ **1등은 절대 표시되지 않는다**(§5.56). 원본 주석이 이유를 적어 뒀다:
        *"the leader always keeps its army: the game can never freeze with every
        remaining side crippled at the floor, and the final wave squeezes out
        everyone but the leader -> a single winner."* 즉 클락은 **도전자들을
        선두 쪽으로 몰아 정리하는 장치**이지 모두를 깎는 장치가 아니다.

        ⚠ `land_count` 는 **낙진을 뺀 땅**이어야 한다. 썩음이 낙진을 만들므로
        판이 진행될수록 분모가 줄고 바가 상대적으로 높아진다."""
        if not self.cfg.enabled:
            return
        # 남은 편이 하나뿐이면 아무도 표시하지 않는다(원본: 승자가 정해졌거나
        # 임박한 상태 — `sides.length < 2` 면 전부 지운다).
        # ⚠ **변이로 안 잡힌다. 정상이다** — 혼자 남으면 그가 곧 선두라 아래
        # 면제가 이미 같은 일을 한다. 원본에 있는 분기라 남긴다.
        if len(tiles_of) < 2:
            self.marked_at.clear()
            return
        leader = max(tiles_of, key=lambda pid: tiles_of[pid])
        bar = self.bar_tiles(elapsed, land_count, team_game)
        for pid, n in tiles_of.items():
            if pid != leader and n < bar:
                self.marked_at.setdefault(pid, elapsed)
            else:
                self.marked_at.pop(pid, None)
                self.rotted_at.pop(pid, None)

    def drain_fraction(self, pid: int, elapsed: float) -> float:
        """이번 초에 잃는 병력의 비율. 경고 시간 동안은 0 이다.

        ⚠ **이 비율은 `max_troops` 에 곱한다. 현재 병력이 아니다**(§5.56).
        원본 주석이 이유를 못 박아 뒀다 — *"as a percentage of MAX capacity
        (not current), so it outpaces income from the first second."*

        현재 병력에 곱하면 줄어들수록 유출도 줄어 **수입과 균형을 이루고 멈춘다.**
        실측: 상한 102,000 짜리가 62,139 에서 멈춰 바닥(5,100)에 영영 안 닿았다.
        그래서 옛 코드에는 "표시 뒤 150초면 무조건 소멸"이라는 임시 장치가
        필요했던 것이다 — 그게 없으면 아무도 안 죽었다."""
        since = self.marked_at.get(pid)
        if since is None:
            return 0.0
        t = elapsed - since - self.cfg.warn_seconds
        if t < 0:
            return 0.0
        c = self.cfg
        f = min(1.0, t / c.drain_ramp_seconds)
        pct = c.drain_start_percent + (c.drain_max_percent - c.drain_start_percent) * f
        return pct / 100.0

    def troop_floor_fraction(self, pid: int, elapsed: float) -> float:
        """유출이 멈추는 바닥. 40% 에서 시작해 90초에 걸쳐 5% 로 내려간다 —
        **한 번의 반격 기회**를 남기되 영구히 살려 두지는 않는다."""
        since = self.marked_at.get(pid)
        if since is None:
            return 1.0
        c = self.cfg
        t = max(0.0, elapsed - since - c.warn_seconds)
        f = min(1.0, t / c.floor_decay_seconds)
        pct = c.floor_start_percent + (c.drain_floor_percent - c.floor_start_percent) * f
        return pct / 100.0

    def warship_drain_fraction(self, pid: int, elapsed: float) -> float:
        """전함이 이번 초에 잃는 **최대 체력의 비율**(§5.56).

        병력과 같은 경사를 타지만 천장이 50% 로 훨씬 높고, **볼록 곡선**(지수 8)
        이라 초반에는 거의 안 닳다가 끝에서 치솟는다. 바닥은 병력과 같은
        `drain_floor_percent` 다 — **가라앉히는 것이 아니라 두들겨 놓는 것**이다."""
        since = self.marked_at.get(pid)
        if since is None:
            return 0.0
        c = self.cfg
        t = elapsed - since - c.warn_seconds
        if t < 0:
            return 0.0
        span = c.warship_drain_max_percent - c.warship_drain_start_percent
        r = c.drain_ramp_seconds
        if r > 0 and t < r:
            # (t/r)^지수 — 볼록하게 오른다
            pct = c.warship_drain_start_percent + span * (t / r) ** c.warship_drain_curve_exponent
        else:
            pct = c.warship_drain_max_percent
        return pct / 100.0

    # --- 영토 썩음 (§5.56) ------------------------------------------------

    def rot_quota(self, tiles_left: int, seconds_under: float) -> int:
        """`doomsdayClockRotQuota` — **초당 먹을 칸 수** = ⌈남은칸 / 남은초⌉.

        스스로 보정된다: 늦게 시작해도, 영토가 크든 작든 `rot_death_seconds`
        마감을 지킨다. 원본 주석이 그 성질을 요점으로 적어 뒀다."""
        c = self.cfg
        if tiles_left <= 0 or c.rot_death_seconds <= 0:
            return 0
        seconds_left = max(1.0, c.rot_death_seconds - seconds_under)
        return math.ceil(tiles_left / seconds_left)

    def rot_specks(self, held: int, ticks_since: int) -> int:
        """썩기 시작한 **처음 10초**는 쿼터 대신 알갱이로 뿌린다.

        원본 주석: 쿼터의 몫만 뿌리면 웬만한 나라에서는 초당 구멍 한두 개뿐이라
        아무 일도 안 일어나 보인다. 앞질러 뿌리고 쿼터가 그만큼 흡수한다."""
        c = self.cfg
        if ticks_since >= c.rot_grain_seconds * 10:
            return 0
        return max(1, math.ceil(held * c.rot_speckle_percent / 100
                                / c.rot_grain_seconds))

    def rotting(self, pid: int, elapsed: float, troops: float,
                max_troops: float) -> bool:
        """지금 썩는 중인가. **마감이 지나서가 아니라 바닥에 닿아서** 시작한다.

        ⚠ 원본 조건이 둘이다: 바닥이 다 내려갔을 것(`floor_decay_seconds`)과
        **병력이 그 바닥 이하일 것.** 즉 반격 창에서 병력을 지켜 낸 나라는
        아직 안 썩는다. 우리는 이걸 "표시된 뒤 150초면 무조건 소멸"로 뭉뚱그려
        놨었다(§5.56)."""
        since = self.marked_at.get(pid)
        c = self.cfg
        if since is None or c.rot_death_seconds <= 0:
            return False
        past_warn = elapsed - since - c.warn_seconds
        if past_warn < c.floor_decay_seconds:
            return False
        floor = self.troop_floor_fraction(pid, elapsed) * max_troops
        return troops <= floor

    def mark_rotted(self, pid: int, tick: int) -> None:
        """**이식 누락 아흔여섯**(§5.92). 칸이 **실제로 하나 썩은** 그 tick 을 찍는다 — 원본 `Player.markRotted`,
        `DoomsdayClockExecution.consume` 이 칸을 놓아 줄 때마다 부른다."""
        self.rotted_at[pid] = tick

    def is_decaying(self, pid: int, tick: int) -> bool:
        """지금 영토가 썩고 있는가 — 화면의 **빨간 고정 해골**.

        ⚠ **`rotting()` 을 다시 계산하지 않는다.** 원본이 주석으로 이유를 못 박아
        뒀다 — 병력 대 바닥 비교는 *knife-edge* 라(유출이 바닥에 정확히 닿고,
        썩음이 상한을 줄이므로 바닥 자체가 움직인다) 화면에서 **깜빡인다.**
        그래서 원본은 판정을 그리는 쪽에서 다시 하지 않고, 썩힌 쪽이 찍어 둔
        시각을 본다. 우리도 같은 이유로 `_rot_step` 이 찍는다.

        찍힌 뒤 `DECAY_CUE_GRACE_TICKS` 안이면 참이다 — 썩음은 **초에 한 번**만
        도는데(`_rot_step`) 화면은 10Hz 라, 유예가 없으면 열 프레임 중 하나만
        빨갛다."""
        if pid not in self.marked_at:
            return False
        at = self.rotted_at.get(pid)
        return at is not None and tick - at <= DECAY_CUE_GRACE_TICKS

    def is_dead(self, pid: int, elapsed: float) -> bool:
        """`rotDeathSeconds` 는 **마감 시각**이다 — 표시된 뒤 이만큼 지나면 무엇을
        쥐고 있든 영토가 사라진다. 유출만으로는 절대 안 죽기 때문에 필요하다."""
        since = self.marked_at.get(pid)
        if since is None or self.cfg.rot_death_seconds <= 0:
            return False
        return elapsed - since >= self.cfg.warn_seconds + self.cfg.rot_death_seconds
