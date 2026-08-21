"""게임 루프 — tick 하나가 100ms (원본 `turnIntervalMs` = 100).

한 tick 순서: **성장 → 공격 진행 → 흡수/탈락 → 종료 판정.**

영토 수는 `_counts` 로 증분 유지한다. 13만 타일을 매 tick 세면 판당 수 초가 날아간다
(전수 `np.bincount` 는 0.42ms, 9000 tick 이면 3.8초). 테스트만 전수로 대조한다.

⚠ 종료 조건(시간 제한·지배)은 **원본에 없다.** openfront 는 마지막 생존자로 끝난다.
헤드리스 측정을 끝내려고 우리가 둔 것이고, P6 에서 둠스데이 클락으로 교체한다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from . import constants as C
from .attack import Attack
from .buildings import DefensePostIndex, find_spot, structure_tiles
from .gamemap import GameMap, TileRef
from .state import PlayerState
from .units import UNIT_INFO, STRUCTURES, Unit, UnitType


class Victory(Enum):
    CONQUEST = "정복"
    DOMINATION = "지배"
    TIMEOUT = "시간 종료"


@dataclass
class GameState:
    gmap: GameMap
    players: dict[int, PlayerState]
    rng: random.Random

    tick_count: int = 0
    attacks: list[Attack] = field(default_factory=list)

    over: bool = False
    winner: int | None = None
    victory: Victory | None = None

    _counts: dict[int, int] = field(default_factory=dict)
    _posts: DefensePostIndex | None = None

    # --- 설정 -------------------------------------------------------------

    @classmethod
    def new(cls, player_count: int, rng: random.Random,
            map_name: str = "world", human: int = 0) -> "GameState":
        """`human` 은 사람이 잡는 pid. 헤드리스는 -1 을 줘서 전원 봇으로 만든다."""
        gmap = GameMap.load(map_name)
        starts = gmap.place_starts(player_count, rng)
        players = {}
        for pid, tile in enumerate(starts):
            players[pid] = PlayerState(pid=pid, name=f"P{pid}",
                                       is_bot=(pid != human), start=tile)
            gmap.owner[tile] = pid
        st = cls(gmap=gmap, players=players, rng=rng)
        st._counts = {pid: 1 for pid in players}
        st._posts = DefensePostIndex(gmap.size)
        return st

    # --- 조회 -------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return self.tick_count * C.TICK_DT

    def tiles(self, pid: int) -> int:
        return self._counts.get(pid, 0)

    def share(self, pid: int) -> float:
        return self.tiles(pid) / self.gmap.land_count if self.gmap.land_count else 0.0

    @property
    def alive(self) -> list[PlayerState]:
        return [p for p in self.players.values() if p.alive]

    # --- 행동 -------------------------------------------------------------

    def launch_attack(self, pid: int, target: int | None) -> Attack | None:
        p = self.players.get(pid)
        if p is None or not p.alive or self.over:
            return None
        troops = p.attack_troops()
        if troops < C.ATTACK_MIN_TROOPS:
            return None
        atk = Attack.launch(self.gmap, pid, target, troops, self.rng, self.tick_count)
        if atk is None:
            return None
        p.troops -= troops
        self.attacks.append(atk)
        return atk

    # --- 건설 -------------------------------------------------------------

    def can_build(self, pid: int, utype: UnitType, near: TileRef) -> TileRef | None:
        """지을 수 있으면 실제로 지을 칸을, 아니면 None.

        원본 `canBuild()` = 골드가 되는가(`canBuildUnitType`) + 자리가 있는가
        (`canSpawnUnitType`). 둘을 한 번에 본다."""
        p = self.players.get(pid)
        if p is None or not p.alive:
            return None
        if p.gold < p.units.cost(utype):
            return None
        if utype in STRUCTURES:
            return find_spot(self.gmap, pid, near, structure_tiles(p.units))
        return near if self.gmap.passable(near) else None

    def build(self, pid: int, utype: UnitType, near: TileRef) -> Unit | None:
        tile = self.can_build(pid, utype, near)
        if tile is None:
            return None
        p = self.players[pid]
        p.gold -= p.units.cost(utype)
        unit = Unit(utype=utype, owner=pid, tile=tile,
                    ticks_left=UNIT_INFO[utype].construction_ticks)
        p.units.units.append(unit)
        # 원본은 `buildUnit()` 안에서, **건설이 끝나기 전에** 완공 카운터를 올린다
        # (PlayerImpl.ts 주석: "already accounts for in-progress builds").
        # 완공 시점으로 미루면 짓는 동안 같은 건물을 원본보다 싸게 연달아 지을 수 있다.
        p.units.record_constructed(utype)
        if not unit.under_construction:
            self._activate(p, unit)
        return unit

    def upgrade(self, pid: int, unit: Unit) -> bool:
        """`upgradeUnit()` — 같은 비용 함수를 다시 낸다. 레벨이 오르면 완공수가 하나
        늘어 **다음 업그레이드가 더 비싸진다.**"""
        p = self.players.get(pid)
        if p is None or not UNIT_INFO[unit.utype].upgradable or unit.under_construction:
            return False
        cost = p.units.cost(unit.utype)
        if p.gold < cost:
            return False
        p.gold -= cost
        unit.level += 1
        p.units.record_constructed(unit.utype)
        return True

    def _activate(self, p: PlayerState, unit: Unit) -> None:
        """건설이 끝났을 때. 완공 카운터는 이미 `build()` 에서 올렸다.

        방어초소는 **완공된 것만** 효과가 있다 — 원본이 `nearbyUnits(...)` 를
        `includeUnderConstruction` 없이 부르고, 그 기본값이 false 다."""
        if unit.utype is UnitType.DEFENSE_POST:
            self._rebuild_posts()

    def _rebuild_posts(self) -> None:
        posts = [(u.tile, u.owner) for p in self.players.values()
                 for u in p.units.of(UnitType.DEFENSE_POST)
                 if not u.under_construction]
        if self._posts is None:
            self._posts = DefensePostIndex(self.gmap.size)
        self._posts.rebuild(self.gmap, posts)

    def _advance_construction(self) -> None:
        for p in self.alive:
            for u in p.units.units:
                if u.under_construction:
                    u.ticks_left -= 1
                    if not u.under_construction:
                        self._activate(p, u)

    # --- tick -------------------------------------------------------------

    def tick(self) -> None:
        if self.over:
            return
        self.tick_count += 1
        self._grow()
        self._advance_construction()
        self._advance_attacks()
        self._check_end()

    def _grow(self) -> None:
        for p in self.alive:
            p.troops += p.troop_increase(self.tiles(p.pid))
            p.gold += C.GOLD_PER_TICK_BOT if p.is_bot else C.GOLD_PER_TICK_HUMAN

    def _advance_attacks(self) -> None:
        still: list[Attack] = []
        for a in self.attacks:
            atk = self.players.get(a.attacker)
            if atk is None or not atk.alive:
                continue
            defender = self.players.get(a.target) if a.target is not None else None
            if defender is not None and not defender.alive:
                a.retreated = True
            else:
                taken = a.step(self.gmap, atk, defender,
                               self.tiles(a.target) if a.target is not None else 0,
                               self.tiles(a.attacker), self.rng, self.tick_count,
                               defense_posts=self._posts)
                if taken:
                    self._counts[a.attacker] = self._counts.get(a.attacker, 0) + len(taken)
                    if a.target is not None:
                        self._counts[a.target] = max(
                            0, self._counts.get(a.target, 0) - len(taken))
                        self._maybe_absorb(a.attacker, a.target)

            if a.finished:
                if a.retreated:
                    atk.troops += a.troops      # 퇴각한 병력은 돌아온다
            else:
                still.append(a)
        self.attacks = still

    def _maybe_absorb(self, attacker: int, target: int) -> None:
        """`handleDeadDefender()` — 타일 100 미만으로 떨어진 수비자는 통째로 흡수된다.

        원본이 이걸 두는 이유는 잔챙이 영토를 한 칸씩 긁어내느라 판이 늘어지는 것을
        막기 위해서다. 우리 지도(3.7만~13만 칸)에서도 같은 비율로 작동한다."""
        d = self.players.get(target)
        if d is None or not d.alive or self._counts.get(target, 0) >= C.CONQUER_PLAYER_TILES:
            return
        refs = self.gmap.owned_refs(target)
        if len(refs):
            self.gmap.owner[refs] = attacker
            self._counts[attacker] = self._counts.get(attacker, 0) + len(refs)
        self._counts[target] = 0
        d.alive = False
        d.troops = 0.0
        # `conquerPlayer` — 건물도 정복자에게 넘어간다. 버리면 도시가 사라져
        # 병력 상한이 갑자기 떨어진다.
        winner = self.players[attacker]
        for u in d.units.units:
            u.owner = attacker
            winner.units.units.append(u)
            winner.units.record_constructed(u.utype)
        d.units.units = []
        self._rebuild_posts()

    def _check_end(self) -> None:
        for p in self.alive:
            if self.tiles(p.pid) <= 0 and not any(a.attacker == p.pid for a in self.attacks):
                p.alive = False
                p.troops = 0.0

        alive = self.alive
        if len(alive) <= 1:
            self._finish(alive[0].pid if alive else None, Victory.CONQUEST)
            return
        top = max(alive, key=lambda p: self.tiles(p.pid))
        if self.share(top.pid) >= C.DOMINATION_TILE_RATIO:
            self._finish(top.pid, Victory.DOMINATION)
            return
        if self.elapsed >= C.MATCH_SECONDS:
            self._finish(top.pid, Victory.TIMEOUT)

    def _finish(self, pid: int | None, how: Victory) -> None:
        self.over = True
        self.winner = pid
        self.victory = how

    # --- 검증 -------------------------------------------------------------

    def verify_counts(self) -> bool:
        """증분 카운트가 지도와 맞는가. 테스트 전용 — 런타임에 부르지 말 것."""
        scan = self.gmap.tile_counts(max(self.players) + 1)
        return all(int(scan[pid]) == self._counts.get(pid, 0) for pid in self.players)

    def border_targets(self, pid: int) -> set[int | None]:
        """닿을 수 있는 상대들. AI 가 쓴다. None 은 중립."""
        out: set[int | None] = set()
        for t in self.gmap.owned_refs(pid).tolist():
            for n in self.gmap.neighbors(t):
                o = int(self.gmap.owner[n])
                if o != pid and self.gmap.passable(n):
                    out.add(None if o < 0 else o)
        return out
