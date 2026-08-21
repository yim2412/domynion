"""Tribe — 원본 `TribeExecution.ts`. **Nation 과 다른 AI 다.**

이식 누락이었다. 우리는 사람 아닌 모두에게 `NationBot` 을 붙이고 있었는데, 원본은
둘을 나눈다. 싱글플레이 기본 구성이 **72개 나라 + 봇 400개**라, 지도를 채우는 것은
사실 이쪽이다.

성격이 정반대다:

| | Nation | Tribe(봇) |
|---|---|---|
| 동맹 요청 | 관계·배신자를 따진다 | **전부 받는다** |
| 건물 | 짓는다 | **하나씩 지운다** |
| 표적 | 약한 이웃부터 | **배신자 우선**, 아니면 무작위 |
| 이모지 | 사람에게 말을 건다 | 아무 말도 안 한다 |

봇이 동맹을 다 받아 주는 것이 이 게임의 초반 구조다 — 사람은 주변 봇을 우방으로
묶어 두고 나라와 싸운다. 봇도 관계를 따지게 만들면 그게 사라진다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..core.engine import GameState
from ..core.units import STRUCTURES

# `TribeExecution` 생성자 그대로. Nation 보다 자주 판단한다(40~80 vs 45~100).
ATTACK_RATE = (40, 80)
TRIGGER_RATIO = (50, 60)        # 병력이 상한의 이만큼 차야 움직인다
RESERVE_RATIO = (30, 40)        # 사람·나라를 칠 때 남겨 둘 몫
EXPAND_RATIO = (10, 20)         # 중립으로 나갈 때 남겨 둘 몫

# 배신자를 칠 확률. **동맹이어도 친다** — 다만 덜 자주(1/6 vs 1/3).
TRAITOR_ODDS_FRIENDLY = 6
TRAITOR_ODDS_ENEMY = 3

# 나라·사람은 절반 확률로 건너뛴다. 봇끼리 먼저 부딪히게 하는 장치다.
SKIP_BIG_TARGET_ODDS = 2

# 건물 하나 지우는 간격(`deleteUnitCooldown`). 봇은 건물을 안 쓴다.
DELETE_COOLDOWN_TICKS = 10


@dataclass
class TribeBot:
    """봇 하나. `NationBot` 과 같은 `tick(st)` 규약을 쓴다."""

    pid: int
    rng: random.Random
    attack_rate: int = 0
    attack_tick: int = 0
    trigger_ratio: float = 0.0
    reserve_ratio: float = 0.0
    expand_ratio: float = 0.0
    _first_attack_done: bool = False
    _last_delete: int = field(default=-999)
    # 중립이 남아 있는 동안은 중립만 본다. 한 번 없어지면 다시 안 찾는다 —
    # 원본 `neighborsTerraNullius` 도 false 로 굳는다.
    _neighbours_neutral: bool = True

    def __post_init__(self) -> None:
        self.attack_rate = self.rng.randint(*ATTACK_RATE)
        self.attack_tick = self.rng.randrange(self.attack_rate)
        self.trigger_ratio = self.rng.randint(*TRIGGER_RATIO) / 100
        self.reserve_ratio = self.rng.randint(*RESERVE_RATIO) / 100
        self.expand_ratio = self.rng.randint(*EXPAND_RATIO) / 100

    # --- 진입점 -----------------------------------------------------------

    def tick(self, st: GameState) -> None:
        p = st.players.get(self.pid)
        if p is None or not p.alive or st.over or st.spawn_phase:
            return
        if st.tick_count % self.attack_rate != self.attack_tick:
            return

        if not self._first_attack_done:
            # 원본은 **첫 판단에서 중립을 한 번 친다.** 이게 없으면 봇이 병력
            # 문턱을 넘을 때까지 가만히 있어 초반 지도가 안 채워진다.
            self._first_attack_done = True
            self._attack(st, None)
            return

        self._accept_everything(st)
        self._delete_a_structure(st)
        self._maybe_attack(st)

    # --- 외교 -------------------------------------------------------------

    def _accept_everything(self, st: GameState) -> None:
        """**들어온 동맹 요청을 전부 받는다.** 관계도 배신자도 안 본다.

        사람이 주변 봇을 우방으로 묶어 두고 나라와 싸우는 것이 초반 구조다."""
        for requestor, recipients in list(st.diplomacy.pending.items()):
            if self.pid in recipients and requestor in st.players:
                st.accept_alliance(self.pid, requestor)

    # --- 건설(의 반대) ----------------------------------------------------

    def _delete_a_structure(self, st: GameState) -> None:
        """가진 건물을 하나씩 지운다. 봇은 건물을 안 쓴다.

        정복으로 넘어온 건물이 봇 손에 쌓이면, 그 땅을 되찾기 전까지 아무도
        못 쓰는 채로 남는다 — 원본이 지우는 이유다."""
        if st.tick_count - self._last_delete < DELETE_COOLDOWN_TICKS:
            return
        p = st.players[self.pid]
        for u in p.units.units:
            if u.utype in STRUCTURES:
                p.units.units.remove(u)
                self._last_delete = st.tick_count
                return

    # --- 공격 -------------------------------------------------------------

    def _maybe_attack(self, st: GameState) -> None:
        traitor = self._nearby_traitor(st)
        if traitor is not None:
            friendly = st.diplomacy.is_friendly(self.pid, traitor)
            odds = TRAITOR_ODDS_FRIENDLY if friendly else TRAITOR_ODDS_ENEMY
            if self.rng.randrange(odds) == 0:
                if friendly:
                    # **동맹이어도 깨고 친다.** 배신자를 감싸 주지 않는다.
                    st.break_alliance(self.pid, traitor)
                if self._attack(st, traitor):
                    return

        if self._neighbours_neutral:
            if None in st.border_targets(self.pid):
                if self._attack(st, None):
                    return
            else:
                self._neighbours_neutral = False

        self._attack_random(st)

    def _attack_random(self, st: GameState) -> None:
        if not self._has_trigger_troops(st):
            return
        targets = [o for o in st.border_targets(self.pid)
                   if o is not None and o in st.players
                   and st.players[o].alive
                   and not st.diplomacy.is_friendly(self.pid, o)]
        self.rng.shuffle(targets)
        for o in targets:
            # 나라·사람은 절반 확률로 건너뛴다 — 봇끼리 먼저 부딪히게 하는 장치다.
            if st.players[o].kind != "bot" \
                    and self.rng.randrange(SKIP_BIG_TARGET_ODDS) == 0:
                continue
            if self._attack(st, o):
                return

    def _nearby_traitor(self, st: GameState) -> int | None:
        found = [o for o in st.border_targets(self.pid)
                 if o is not None and o in st.players
                 and st.players[o].alive and st.is_traitor(o)]
        return self.rng.choice(found) if found else None

    def _has_trigger_troops(self, st: GameState) -> bool:
        p = st.players[self.pid]
        cap = p.max_troops(st.tiles(self.pid))
        return cap > 0 and p.troops / cap >= self.trigger_ratio

    def _attack(self, st: GameState, target: int | None) -> bool:
        """남길 몫이 표적에 따라 다르다 — 중립이면 거의 다 쏟는다."""
        p = st.players[self.pid]
        keep = self.expand_ratio if target is None else self.reserve_ratio
        send = p.troops * (1.0 - keep)
        if send <= 0 or p.troops <= 0:
            return False
        saved = p.attack_ratio
        p.attack_ratio = min(1.0, send / p.troops)
        try:
            return st.launch_attack(self.pid, target) is not None
        finally:
            p.attack_ratio = saved
