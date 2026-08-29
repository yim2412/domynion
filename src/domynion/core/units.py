"""유닛 — 종류·비용·건설. openfront `UnitType` / `unitInfo()` 그대로.

**비용이 지수로 오른다.** `min(1e6, 2^n × 125000)` 같은 꼴이라 도시 4채째부터 상한에
걸린다. `n` 은 이미 **지어 놓은** 같은 종류의 수인데, 원본은 정확히
`min(보유수, 완공수)` 를 쓴다 — 건설 중인 것을 세면 짓는 도중에 값이 오른다.

Port 와 Factory 는 **비용을 공유한다**(`costWrapper(fn, Port, Factory)`). 둘을 섞어
지어도 다음 값이 같이 오른다. 따로 세면 원본보다 싸진다.

업그레이드는 같은 비용 함수를 다시 내는 것이다 — 레벨을 올리면 완공수가 하나 늘어
다음 업그레이드가 더 비싸진다(`upgradeUnit()`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from . import constants as C
from .gamemap import TileRef


class UnitType(Enum):
    TRANSPORT_SHIP = "Transport"
    WARSHIP = "Warship"
    SHELL = "Shell"
    SAM_MISSILE = "SAMMissile"
    PORT = "Port"
    ATOM_BOMB = "Atom Bomb"
    HYDROGEN_BOMB = "Hydrogen Bomb"
    TRADE_SHIP = "Trade Ship"
    MISSILE_SILO = "Missile Silo"
    DEFENSE_POST = "Defense Post"
    SAM_LAUNCHER = "SAM Launcher"
    CITY = "City"
    MIRV = "MIRV"
    MIRV_WARHEAD = "MIRV Warhead"
    TRAIN = "Train"
    FACTORY = "Factory"


# 건물 — 지도 위에 자리를 차지하고 최소 거리 규칙이 걸리는 것들
STRUCTURES = (UnitType.CITY, UnitType.PORT, UnitType.FACTORY,
              UnitType.DEFENSE_POST, UnitType.MISSILE_SILO,
              UnitType.SAM_LAUNCHER)


@dataclass(frozen=True)
class UnitInfo:
    """`cost_fn(n)` 의 n 은 이미 완공한 같은 종류의 수다."""
    cost_fn: object                     # Callable[[int], int]
    construction_ticks: int = 0
    upgradable: bool = False
    max_health: int | None = None
    shares_cost_with: tuple[UnitType, ...] = ()


def _capped_doubling(cap: int, base: int):
    return lambda n: min(cap, 2 ** n * base)


def _capped_linear(cap: int, step: int):
    return lambda n: min(cap, (n + 1) * step)


UNIT_INFO: dict[UnitType, UnitInfo] = {
    UnitType.CITY: UnitInfo(
        _capped_doubling(1_000_000, 125_000), construction_ticks=2 * 10, upgradable=True),
    UnitType.PORT: UnitInfo(
        _capped_doubling(1_000_000, 125_000), construction_ticks=5 * 10, upgradable=True,
        shares_cost_with=(UnitType.FACTORY,)),
    UnitType.FACTORY: UnitInfo(
        _capped_doubling(1_000_000, 125_000), construction_ticks=2 * 10, upgradable=True,
        shares_cost_with=(UnitType.PORT,)),
    UnitType.DEFENSE_POST: UnitInfo(
        _capped_linear(250_000, 50_000), construction_ticks=5 * 10),
    UnitType.MISSILE_SILO: UnitInfo(
        lambda n: 1_000_000, construction_ticks=10 * 10, upgradable=True),
    UnitType.SAM_LAUNCHER: UnitInfo(
        _capped_linear(3_000_000, 1_500_000),
        construction_ticks=C.SAM_CONSTRUCTION_TICKS, upgradable=True),
    UnitType.WARSHIP: UnitInfo(
        _capped_linear(1_000_000, 250_000), max_health=1000),
    UnitType.ATOM_BOMB: UnitInfo(lambda n: 750_000),
    UnitType.HYDROGEN_BOMB: UnitInfo(lambda n: 5_000_000),
    UnitType.TRANSPORT_SHIP: UnitInfo(lambda n: 0),
    UnitType.SHELL: UnitInfo(lambda n: 0),
    UnitType.SAM_MISSILE: UnitInfo(lambda n: 0),
    UnitType.TRADE_SHIP: UnitInfo(lambda n: 0),
    # MIRV 는 **판 전체의 발사 수**에 따라 값이 오른다(`25e6 + 발사수 × 15e6`).
    # 그 수는 플레이어가 아니라 게임이 들고 있으므로, `extra` 로 받아 계산한다 —
    # `GameState.launch_nuke` 가 전역 카운터를 넘긴다.
    UnitType.MIRV: UnitInfo(lambda n: 25_000_000 + n * 15_000_000),
    UnitType.MIRV_WARHEAD: UnitInfo(lambda n: 0),
    UnitType.TRAIN: UnitInfo(lambda n: 0),
}


@dataclass
class Unit:
    utype: UnitType
    owner: int
    tile: TileRef
    level: int = 1
    # SAM 업그레이드가 시작된 tick 과 그때의 사거리(`samLauncherState`).
    # 사거리가 새 값으로 **서서히** 오르는 데 쓴다(§5.82 · `dynamic_sam_range`).
    upgrade_started: int | None = None
    upgrade_from: float = 0.0
    health: int | None = None
    ticks_left: int = 0                 # 건설이 끝나기까지
    active: bool = True
    # 철거가 예약된 tick. `UnitImpl._deletionAt` 그대로 — None 이면 예약이 없다.
    # 소유자가 바뀌면 원본은 이걸 지운다(`setOwner` → `clearPendingDeletion`).
    deletion_at: int | None = None

    # 발사한 tick 들 (`UnitImpl._missileTimerQueue`). 사일로와 SAM 만 쓴다.
    #
    # **발사관 수 = 레벨이다.** Lv3 사일로는 관이 셋이고, 쏜 관만 재장전에 들어간다.
    # 큐 길이가 레벨과 같아지면 그 기체는 쿨다운 상태다(`in_cooldown`).
    #
    # ⚠ 이게 없어서 사일로 한 기로 무한 연사가 됐고, **SAM 한 기가 판의 모든 핵을
    # 영원히 막았다.** 리스트인 이유는 관마다 재장전이 따로 돌기 때문이다 — 숫자
    # 하나(마지막 발사 tick)로 두면 Lv3 이 Lv1 과 똑같이 동작한다.
    missile_queue: list[int] = field(default_factory=list)

    # --- 항구 전용 (`PortExecution`) ---------------------------------------
    #
    # ⚠ 이식 누락 열아홉. 둘 다 **항구마다** 있어야 한다 — 판 전체에 하나로 두면
    # 아무 항구나 성공했을 때 모두의 pity 가 리셋되고, 항구를 더 지어도 유통량이
    # 늘지 않는다. 원본은 `PortExecution` 인스턴스가 항구마다 하나씩 붙어 있다.
    spawn_rejections: int = 0            # `tradeShipSpawnRejections`
    check_offset: int = 0                # `checkOffset = mg.ticks() % 10`

    def __post_init__(self) -> None:
        if self.health is None:
            self.health = UNIT_INFO[self.utype].max_health

    # --- 발사관 (사일로 · SAM) --------------------------------------------

    @property
    def ready_tubes(self) -> int:
        """지금 쏠 수 있는 관 수 (`level - missileTimerQueue.length`).

        건설 중이면 0이다 — 원본 `readyMissileCount` 가 그렇게 센다."""
        if self.under_construction:
            return 0
        return max(0, self.level - len(self.missile_queue))

    @property
    def in_cooldown(self) -> bool:
        """`isInCooldown()` — 관이 전부 찼는가. **`>=` 가 아니라 `==` 다.**

        원본이 `length === level` 로 쓴다. 레벨이 내려가면 큐도 같이 줄이므로
        (`decreaseLevel` 이 `pop()` 한다) 큐가 레벨을 넘는 상태가 없다."""
        return len(self.missile_queue) == self.level

    def fire(self, now: int) -> None:
        """`launch()` — 관 하나를 재장전에 넣는다."""
        self.missile_queue.append(now)

    def reload_ready(self, now: int, cooldown: int) -> int:
        """재장전이 끝난 관을 비운다. 비운 개수를 돌려준다.

        ⚠ **사일로와 SAM 의 처리 횟수가 다르다.** 원본 `MissileSiloExecution` 은
        tick 당 맨 앞 하나만 보고(`if`), `SAMLauncherExecution` 은 끝난 것을 전부
        비운다(`while`). 사일로를 `while` 로 바꾸면 한 tick 에 관이 여러 개 열려
        연사 간격이 줄어든다. 호출부가 정하도록 `limit` 을 받는다."""
        done = 0
        while self.missile_queue and now - self.missile_queue[0] >= cooldown:
            self.missile_queue.pop(0)
            done += 1
        return done

    def reload_front(self, now: int, cooldown: int) -> bool:
        """맨 앞 관 하나만 본다 — 사일로용(`MissileSiloExecution` 은 `if` 다)."""
        if self.missile_queue and now - self.missile_queue[0] >= cooldown:
            self.missile_queue.pop(0)
            return True
        return False

    @property
    def under_construction(self) -> bool:
        return self.ticks_left > 0

    @property
    def marked_for_deletion(self) -> bool:
        return self.deletion_at is not None

    def mark_for_deletion(self, now: int) -> None:
        if self.active:
            self.deletion_at = now + C.DELETION_MARK_DURATION_TICKS

    def overdue_deletion(self, now: int) -> bool:
        """`isOverdueDeletion()` — **`>` 다.** `>=` 로 두면 한 tick 일찍 사라진다."""
        return self.active and self.deletion_at is not None and now - self.deletion_at > 0


class UnitStore:
    """한 플레이어가 가진 유닛들.

    비용 계산이 `min(보유수, 완공수)` 를 쓰기 때문에 **둘을 따로 센다.** 하나로
    합치면 건설 중에 값이 올라 원본보다 비싸진다."""

    __slots__ = ("units", "_constructed")

    def __init__(self) -> None:
        self.units: list[Unit] = []
        self._constructed: dict[UnitType, int] = {}

    def owned(self, utype: UnitType) -> int:
        """`unitsOwned()` — **개수가 아니라 레벨 합이다.**

        원본:

            if (unit.isUnderConstruction()) total++;
            else                            total += unit.level();

        ⚠ 우리는 이걸 오래 **개수**로 세고 있었다. 그래서 도시를 올릴수록 비싸져야
        하는데 값이 250,000 에 붙박여 있었다(원본 실측: 250,000 → 500,000 →
        1,000,000). 아무도 업그레이드를 안 해서 안 드러났던 것뿐이다.

        원본 `unitCount()` 도 사실상 같은 값이다 — 건설 중인 유닛은 레벨이 1 이라
        두 함수가 갈리지 않는다. 그래서 하나로 둔다.

        ⚠ **`1 if under_construction` 분기는 변이로 잡히지 않는다. 정상이다.**
        건설 중인 유닛은 레벨이 반드시 1 이다(`can_upgrade` 가 건설 중을 막고, 레벨을
        바꾸는 곳은 `upgrade()` 뿐이다). 그래서 `1` 과 `u.level` 이 늘 같은 값이라
        **관찰 가능한 차이가 없다.** 원본 표현을 그대로 두려고 남긴 것이니, 다음
        세션이 "테스트가 못 잡는다"고 여기를 파지 않도록 적어 둔다."""
        return sum(1 if u.under_construction else u.level
                   for u in self.units if u.utype is utype and u.active)

    def num(self, utype: UnitType) -> int:
        """**실제 개수.** 원본에는 없는 값이다 — 우리 AI 의 건물 개수 상한만 쓴다.
        레벨을 올렸다고 "한 채 더 지었다"로 세면 상한이 조용히 낮아진다."""
        return sum(1 for u in self.units if u.utype is utype and u.active)

    def constructed(self, utype: UnitType) -> int:
        return self._constructed.get(utype, 0)

    def record_constructed(self, utype: UnitType) -> None:
        self._constructed[utype] = self._constructed.get(utype, 0) + 1

    def of(self, utype: UnitType) -> list[Unit]:
        return [u for u in self.units if u.utype is utype and u.active]

    def city_levels(self) -> int:
        """완공된 도시의 레벨 합. 병력 상한 공식이 이 값을 읽는다."""
        return sum(u.level for u in self.units
                   if u.utype is UnitType.CITY and u.active and not u.under_construction)

    def bulk_cost(self, utype: UnitType, amount: int) -> int:
        """`amount` 레벨을 연달아 올릴 때의 **누적** 값. 원본 `upgradeCosts[amount-1]`.

        ⚠ **`cost × amount` 가 아니다.** 한 번 올릴 때마다 레벨 합(`unitsOwned`)이
        같이 오르므로 다음 값이 더 비싸다. 원본 주석이 못 박아 뒀다:
        *"upgrade costs escalate per level, so a bulk total is NOT cost * amount"*.

        원본은 `cost(mg, this, n)` 으로 "이미 n채 더 가진 것처럼" 값을 매긴다 —
        우리 `cost(utype, extra=n)` 의 `extra` 가 정확히 그 자리다.

        도시 예(1채 보유, 상한 100만): 1레벨 25만 · 2레벨 누적 75만 ·
        3레벨 누적 175만. 선형으로 계산하면 3레벨이 75만이라 **2.3배 싸게 판다.**"""
        return sum(self.cost(utype, extra=n) for n in range(amount))

    def cost(self, utype: UnitType, extra: int = 0) -> int:
        """`costWrapper` — 비용을 공유하는 종류는 합쳐 센다.

        **MIRV 만 예외다.** 원본이 플레이어의 보유량이 아니라 `game.stats()
        .numMirvsLaunched()`(판 전체 발사 수)를 쓰므로, 그 수를 `extra` 로 받는다."""
        info = UNIT_INFO[utype]
        if utype is UnitType.MIRV:
            return int(info.cost_fn(extra))
        n = extra
        for t in (utype, *info.shares_cost_with):
            n += min(self.owned(t), self.constructed(t))
        return int(info.cost_fn(n))
