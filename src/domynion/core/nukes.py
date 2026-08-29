"""핵 · 낙진 · SAM · 전함 — openfront 의 요격/파괴 계통.

핵은 **두 개의 반경**을 갖는다. `inner` 안은 무조건 날아가고, `inner`~`outer` 사이는
방향마다 다른 문턱(원본은 각도별로 `inner²~outer²` 사이를 무작위로 뽑아 보간한다)이
걸려 폭발 자국이 원이 아니라 울퉁불퉁해진다. 그 모양이 게임의 인상을 만든다.

병력 손실은 **칸마다 반복 적용**되고 남은 타일 수로 나뉜다:

    nukeDeathFactor = 5 × 병력 / max(1, 남은타일수)      (MIRV 탄두는 다른 식)

그래서 좁은 나라가 훨씬 크게 다친다. 진행 중인 공격 부대와 수송선도 같이 깎인다.

폭심의 **육지는 바다가 된다**(`queueWaterConversion`). 지형이 바뀌므로 바다 경로
캐시를 반드시 비워야 한다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from . import constants as C
from .constants import Terrain
from .gamemap import GameMap, TileRef
from .units import UnitType


NUKE_MAGNITUDES: dict[UnitType, tuple[int, int]] = {
    UnitType.ATOM_BOMB: (12, 30),
    UnitType.HYDROGEN_BOMB: (80, 100),
    UnitType.MIRV_WARHEAD: (12, 18),
}

NUKE_SPEED: dict[UnitType, int] = {
    UnitType.ATOM_BOMB: 10,
    UnitType.HYDROGEN_BOMB: 10,
    UnitType.MIRV: 15,
    UnitType.MIRV_WARHEAD: 22,
}

_NUM_SAMPLES = 64          # 폭발 가장자리를 흔드는 각도 표본 수


def sam_range(level: int) -> float:
    """`samRange(level)` = 150 − 480/(level+5). Lv1 = 70, 위로 갈수록 150 에 수렴."""
    return C.MAX_SAM_RANGE - 480.0 / (level + 5)


def dynamic_sam_range(unit, now: int) -> float:
    """`dynamicSamRange` — **업그레이드 중에는 사거리가 서서히 는다**(§5.82).

    ⚠ 우리는 레벨이 오른 그 tick 부터 새 사거리를 썼다. 원본은 옛 사거리에서
    새 사거리로 `samUpgradeDuration`(쿨다운의 절반, 45 tick)에 걸쳐 선형으로
    올린다. 즉시 적용하면 **업그레이드가 즉발 방공망 확장**이 된다 — 날아오는
    핵을 보고 올려서 그 자리에서 막을 수 있다.

    올리는 그 tick 에 `upgrade_started`(시각)와 `upgrade_from`(옛 사거리)을
    적어 두면 여기서 그 둘로 잰다."""
    started = getattr(unit, "upgrade_started", None)
    if started is None:
        return sam_range(unit.level)
    target = sam_range(unit.level)
    elapsed = now - started
    if elapsed >= C.SAM_UPGRADE_DURATION_TICKS:
        return target
    start = unit.upgrade_from
    return start + (target - start) * elapsed / C.SAM_UPGRADE_DURATION_TICKS


def blast_counts(gmap: GameMap, dst: TileRef, utype: UnitType) -> dict[int, float]:
    """`computeNukeBlastCounts` — 반경 안 타일을 **주인별로 가중치로** 센다.

    내부 반경 1점 · 내부~외부 0.5점. 이 합이 문턱(100)을 넘는 나라가 화를 낸다
    (`listNukeBreakAlliance`). **무작위가 없다** — `blast_tiles` 는 가장자리를
    각도별로 흔들지만 이쪽은 원본도 원 하나로 셈한다.
    """
    inner, outer = NUKE_MAGNITUDES[utype]
    inner2, outer2 = inner * inner, outer * outer
    w, h = gmap.width, gmap.height
    cx, cy = dst % w, dst // w
    out: dict[int, float] = {}
    for py in range(max(0, cy - outer), min(h - 1, cy + outer) + 1):
        for px in range(max(0, cx - outer), min(w - 1, cx + outer) + 1):
            dx, dy = px - cx, py - cy
            d2 = dx * dx + dy * dy
            if d2 > outer2:
                continue
            owner = int(gmap.owner[py * w + px])
            if owner < 0:
                continue
            out[owner] = out.get(owner, 0.0) + (
                C.NUKE_BLAST_WEIGHT_INNER if d2 <= inner2
                else C.NUKE_BLAST_WEIGHT_OUTER)
    return out


def blast_tiles(gmap: GameMap, dst: TileRef, utype: UnitType,
                rng: random.Random) -> list[TileRef]:
    """폭발이 닿는 칸들. `inner` 안은 전부, 바깥은 방향마다 문턱이 다르다."""
    inner, outer = NUKE_MAGNITUDES[utype]
    inner2, outer2 = inner * inner, outer * outer
    radii = [rng.uniform(inner2, outer2) for _ in range(_NUM_SAMPLES)]

    w, h = gmap.width, gmap.height
    cx, cy = dst % w, dst // w
    out: list[TileRef] = []
    for py in range(max(0, cy - outer), min(h - 1, cy + outer) + 1):
        for px in range(max(0, cx - outer), min(w - 1, cx + outer) + 1):
            dx, dy = px - cx, py - cy
            d2 = dx * dx + dy * dy
            if d2 > outer2:
                continue
            if d2 > inner2:
                angle = math.atan2(dy, dx) + math.pi
                t = angle / (2 * math.pi) * _NUM_SAMPLES
                i0 = int(t) % _NUM_SAMPLES
                i1 = (i0 + 1) % _NUM_SAMPLES
                frac = t - math.floor(t)
                if d2 > radii[i0] * (1 - frac) + radii[i1] * frac:
                    continue
            tile = py * w + px
            if gmap.terrain[tile] == Terrain.IMPASSABLE:
                continue
            out.append(tile)
    return out


def death_factor(utype: UnitType, troops: float, tiles_left: int,
                 max_troops: float) -> float:
    """`nukeDeathFactor` — 칸마다 한 번씩 적용된다.

    일반 핵은 `5 × 병력 / 남은타일수` 라, **영토가 좁을수록 한 칸의 피해가 크다.**
    MIRV 탄두만 다른 식으로, 상한 대비 초과 병력을 지수로 눌러 잡는다."""
    if utype is not UnitType.MIRV_WARHEAD:
        return 5.0 * troops / max(1, tiles_left)
    target = C.MIRV_TARGET_TROOP_RATIO * max_troops
    excess = max(0.0, troops - target)
    if max_troops <= 0:
        return 0.0
    return C.MIRV_DEATH_SCALE * (
        1.0 - math.exp(-C.MIRV_DEATH_STEEPNESS * excess / max_troops))


# SAM 이 노릴 수 있는 종류. **MIRV 본체는 없다** — 원본 `SAMLauncherExecution` 의
# `nearbyUnits(..., [AtomBomb, HydrogenBomb, MIRVWarhead])` 에 MIRV 가 빠져 있다.
# 본체를 막을 수 있으면 탄두 여러 발이 한 방에 사라져 MIRV 가 의미를 잃는다.
SAM_TARGETABLE_TYPES = frozenset({
    UnitType.ATOM_BOMB, UnitType.HYDROGEN_BOMB, UnitType.MIRV_WARHEAD,
})


def is_targetable(gmap: GameMap, src: TileRef, dst: TileRef,
                  here: TileRef) -> bool:
    """`NukeExecution.isTargetable` — **발사점 150 안 또는 표적 150 안**일 때만
    요격 대상이다. 그 사이의 중간 비행 구간은 SAM 이 손댈 수 없다.

    ⚠ 이식 누락 스물일곱. `NUKE_TARGETABLE_RANGE = 150` 이 상수 파일에 적혀만
    있고 **아무도 읽지 않았다**(상륙 퇴각 25% 와 같은 자리다). 그동안 우리 SAM 은
    지나가는 모든 핵을 경로 어디서든 떨궜다 — 장거리 핵이 남의 SAM 옆을 스치기만
    해도 사라졌다는 뜻이다."""
    w = gmap.width
    r2 = C.NUKE_TARGETABLE_RANGE * C.NUKE_TARGETABLE_RANGE

    def d2(a: TileRef, b: TileRef) -> int:
        return (a % w - b % w) ** 2 + (a // w - b // w) ** 2

    return d2(here, dst) < r2 or d2(here, src) < r2


@dataclass
class Nuke:
    """비행 중인 핵. `speed` 칸씩 직선으로 날아간다."""

    owner: int
    utype: UnitType
    src: TileRef
    dst: TileRef
    progress: float = 0.0
    # `NukeExecution.waitTicks` — **겹쳐 산 핵은 한 발씩 밀려 나간다.** 같은
    # 사일로에서 한 tick 에 여러 발을 쏘면 원본은 발사 시각을 하나씩 뒤로 민다
    # (원본 주석: *"delay each missile so launches from the same silo trail each
    # other instead of overlapping"*). 미는 동안에도 핵은 발사점에 떠 있으므로
    # SAM 의 표적이 될 수 있다.
    wait_ticks: int = 0


    def tile(self, gmap: GameMap) -> TileRef:
        w = gmap.width
        sx, sy = self.src % w, self.src // w
        dx, dy = self.dst % w, self.dst // w
        total = math.hypot(dx - sx, dy - sy)
        if total <= 0:
            return self.dst
        f = min(1.0, self.progress / total)
        return int(round(sy + (dy - sy) * f)) * w + int(round(sx + (dx - sx) * f))

    def advance(self) -> None:
        self.progress += NUKE_SPEED[self.utype]

    def arrived(self, gmap: GameMap) -> bool:
        w = gmap.width
        sx, sy = self.src % w, self.src // w
        dx, dy = self.dst % w, self.dst // w
        return self.progress >= math.hypot(dx - sx, dy - sy)


class Fallout:
    """낙진 — 방어를 크게 올린다(`falloutDefenseModifier` = 5 − 비율 × 2).

    비율은 **지도 전체의 낙진 비율**이라, 핵이 많이 터진 판일수록 낙진 한 칸의
    방어 효과가 오히려 줄어든다."""

    __slots__ = ("mask", "_count")

    def __init__(self, size: int):
        self.mask = np.zeros(size, dtype=bool)
        self._count = 0

    def add(self, tiles: list[TileRef]) -> None:
        if not tiles:
            return
        idx = np.asarray(tiles, dtype=np.int64)
        self.mask[idx] = True
        self._count = int(self.mask.sum())

    def clear(self, tile: TileRef) -> None:
        if self.mask[tile]:
            self.mask[tile] = False
            self._count -= 1

    def ratio(self, land_count: int) -> float:
        return self._count / land_count if land_count else 0.0

    def modifier(self, land_count: int) -> float:
        return C.FALLOUT_DEFENSE_BASE - self.ratio(land_count) * C.FALLOUT_DEFENSE_SLOPE

    def at(self, tile: TileRef) -> bool:
        return bool(self.mask[tile])
