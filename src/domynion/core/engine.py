"""게임 루프 — `GameState.tick(dt)`.

한 tick 의 순서는 **성장 → 공격 진행 → 증강 정지 → 탈락 → 승리 판정**이다.
순서를 바꾸면 판정이 달라진다: 탈락을 승리보다 뒤에 두면 마지막 한 명이 남은 tick 에
정복 승리가 한 tick 늦게 잡히고, 공격을 성장보다 먼저 두면 방금 자란 병력이 같은
tick 에 곧바로 소모된다.

영토 수는 `_counts` 로 **증분 유지**한다. 전수 순회하면 1600칸 × 인원 × 20Hz 가 되어
헤드리스 측정이 두 배 넘게 느려진다(실측 9.8초 → 4.2초).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from . import constants as C
from .attack import Attack
from .augments import Augment, offer
from .gamemap import GameMap
from .state import PlayerState


class Victory(Enum):
    CONQUEST = "정복"        # 마지막 생존자
    DOMINATION = "지배"      # 육지 80%
    TIMEOUT = "시간 종료"    # 15분 시점 최대 영토


AiPick = Callable[[PlayerState, list[Augment]], str]


def _random_pick(rng: random.Random) -> AiPick:
    """AI 배선이 붙기 전의 기본 선택기. `core` 가 `ai` 를 import 하지 않기 위한 것이다."""
    def pick(_player: PlayerState, offers: list[Augment]) -> str:
        return rng.choice(offers).key
    return pick


@dataclass
class GameState:
    gmap: GameMap
    players: dict[int, PlayerState]
    rng: random.Random

    elapsed: float = 0.0
    attacks: list[Attack] = field(default_factory=list)

    # 증강 정지
    paused: bool = False
    pause_timer: float = 0.0
    next_augment_at: float = C.AUGMENT_FIRST_SEC
    offers: dict[int, list[Augment]] = field(default_factory=dict)
    ai_pick: AiPick | None = None

    over: bool = False
    winner: int | None = None
    victory: Victory | None = None

    _counts: dict[int, int] = field(default_factory=dict)
    _land_total: int = 0

    # --- 설정 -------------------------------------------------------------

    @classmethod
    def new(cls, player_count: int, rng: random.Random,
            names: list[str] | None = None) -> "GameState":
        gmap = GameMap.generate(player_count, rng)
        starts = gmap.place_starts(player_count, rng)
        players = {}
        for pid, pos in enumerate(starts):
            nm = names[pid] if names and pid < len(names) else f"P{pid}"
            players[pid] = PlayerState(pid=pid, name=nm, is_ai=(pid != 0), start=pos)
            gmap[pos].owner = pid
        st = cls(gmap=gmap, players=players, rng=rng)
        st._counts = {pid: 1 for pid in players}
        st._land_total = len(gmap.land_tiles())
        return st

    # --- 조회 -------------------------------------------------------------

    def tiles(self, pid: int) -> int:
        return self._counts.get(pid, 0)

    def share(self, pid: int) -> float:
        return self.tiles(pid) / self._land_total if self._land_total else 0.0

    @property
    def alive(self) -> list[PlayerState]:
        return [p for p in self.players.values() if p.alive]

    def defense_factor(self, owner: int | None) -> float:
        """중립은 1.0 — 지켜 주는 사람이 없다."""
        if owner is None:
            return 1.0
        p = self.players.get(owner)
        return p.defense_factor(self.tiles(owner)) if p else 1.0

    # --- 행동 -------------------------------------------------------------

    def launch_attack(self, pid: int, target: int | None) -> Attack | None:
        """병력의 일부를 떼어 target 소유 영토 전체에 붙인다."""
        p = self.players.get(pid)
        if p is None or not p.alive or self.paused or self.over:
            return None
        troops = p.attack_troops()
        atk = Attack.launch(self.gmap, pid, target, troops, p.naval_range)
        if atk is None:
            return None
        p.troops -= troops
        self.attacks.append(atk)
        return atk

    def choose_augment(self, pid: int, key: str) -> bool:
        """정지 중 증강을 고른다. 전원이 고르면 시계가 다시 흐른다."""
        p = self.players.get(pid)
        if not self.paused or p is None or p.pending_picks <= 0:
            return False
        if key not in {a.key for a in self.offers.get(pid, [])}:
            return False
        p.augments[key] = min(C.AUGMENT_MAX_LEVEL, p.augments.get(key, 0) + 1)
        p.pending_picks -= 1
        self.offers.pop(pid, None)
        self._resume_if_ready()
        return True

    # --- tick -------------------------------------------------------------

    def tick(self, dt: float = C.TICK_DT) -> None:
        if self.over:
            return
        if self.paused:
            self._tick_paused(dt)
            return

        self.elapsed += dt
        self._grow(dt)
        self._advance_attacks(dt)
        self._maybe_pause()
        self._check_eliminated()
        self._check_victory()

    def _grow(self, dt: float) -> None:
        for p in self.alive:
            p.troops += p.growth_per_sec(self.tiles(p.pid)) * dt

    def _advance_attacks(self, dt: float) -> None:
        still: list[Attack] = []
        for a in self.attacks:
            atk = self.players.get(a.attacker)
            if atk is None or not atk.alive:
                continue                      # 공격자가 이미 탈락했으면 부대도 사라진다
            taken = a.step(self.gmap, dt, atk, self.defense_factor(a.target))
            if taken:
                self._counts[a.attacker] = self._counts.get(a.attacker, 0) + len(taken)
                if a.target is not None:
                    self._counts[a.target] = max(0, self._counts.get(a.target, 0) - len(taken))
                    d = self.players.get(a.target)
                    if d is not None:
                        d.troops = max(0.0, d.troops - a.defender_loss(atk))
            if a.finished:
                atk.troops += a.troops        # 남은 병력은 본국으로 돌아온다
            else:
                still.append(a)
        self.attacks = still

    def _maybe_pause(self) -> None:
        if self.elapsed < self.next_augment_at:
            return
        self.next_augment_at += C.AUGMENT_INTERVAL_SEC
        self.paused = True
        self.pause_timer = 0.0
        self.offers = {}
        for p in self.alive:
            cards = offer(self.rng, p.augments)
            if not cards:
                continue                      # 전부 최대 레벨이면 고를 것이 없다
            self.offers[p.pid] = cards
            p.pending_picks += 1
        self._auto_pick_ai()
        self._resume_if_ready()

    def _auto_pick_ai(self) -> None:
        """AI 는 즉시 고른다. 정지는 사람을 기다리는 시간이지 AI 를 기다리는 시간이 아니다."""
        pick = self.ai_pick or _random_pick(self.rng)
        for p in list(self.alive):
            if p.is_ai and p.pending_picks > 0 and p.pid in self.offers:
                self.choose_augment(p.pid, pick(p, self.offers[p.pid]))

    def _tick_paused(self, dt: float) -> None:
        """정지 중에는 시계가 멈춘다 — 고민하는 동안 남이 자라면 정지가 벌이 된다."""
        self.pause_timer += dt
        if self.pause_timer < C.AUGMENT_PICK_TIMEOUT:
            return
        # 시간이 다하면 대신 골라 준다. 한 명이 판 전체를 무기한 멈출 수는 없다.
        pick = self.ai_pick or _random_pick(self.rng)
        for p in list(self.alive):
            while p.pending_picks > 0 and p.pid in self.offers:
                if not self.choose_augment(p.pid, pick(p, self.offers[p.pid])):
                    break
        self._resume_if_ready(force=True)

    def _resume_if_ready(self, force: bool = False) -> None:
        if force or all(p.pending_picks <= 0 for p in self.alive):
            self.paused = False
            self.pause_timer = 0.0
            self.offers = {}
            for p in self.players.values():
                p.pending_picks = 0

    def _check_eliminated(self) -> None:
        for p in self.alive:
            if self.tiles(p.pid) <= 0 and not any(a.attacker == p.pid for a in self.attacks):
                p.alive = False
                p.troops = 0.0

    def _check_victory(self) -> None:
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
