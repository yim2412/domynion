"""Nation 봇 — openfront `NationExecution` + `AiAttackBehavior` 이식.

v0.1 의 `simple_ai` 와 근본이 다르다. 그쪽은 "충전율이 넘으면 친다"였는데, 원본은
**세 개의 비율**로 판단한다:

| 비율 | 뜻 | 값 |
|---|---|---|
| `trigger_ratio` | 이만큼 차야 **공격을 고려**한다 | rand(50,60)% |
| `reserve_ratio` | 사람을 칠 때 **남겨 둘** 병력 | rand(30,40)% |
| `expand_ratio` | 중립을 먹을 때 남겨 둘 병력 | rand(10,20)% |

**중립 확장은 남겨 두는 양이 훨씬 적다**(10~20% vs 30~40%). 그래서 빈 땅은 거의 전부
쏟아붓고, 사람은 여유가 있을 때만 친다. 이 비대칭이 원본 봇의 성격을 만든다.

반응 주기도 난이도별로 다르다 — easy 는 6.5~10초, impossible 은 3~5초에 한 번만
판단한다(`getAttackRate`). 매 tick 판단하면 사람이 흉내 낼 수 없는 손놀림이 된다.

판단 순서 (`maybeAttack`):
1. 국경에 **낙진 없는 중립**이 있으면 그쪽을 먼저 친다. 성공하면 거기서 끝
2. 적이 없으면 1/5 확률로 상륙, 있으면 1/10 확률로 상륙(하고 끝) 또는 동맹 요청
3. 남으면 가장 좋은 표적을 고른다 — **병력이 적은 쪽부터**
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..core import constants as C
from ..core.engine import GameState
from ..core.naval import shoreline_tiles
from ..core.units import STRUCTURES, UnitType

# `getAttackRate()` — 난이도별 반응 주기(tick). 10Hz 이므로 65 tick = 6.5초.
ATTACK_RATE: dict[str, tuple[int, int]] = {
    "easy": (65, 100),
    "medium": (55, 70),
    "hard": (45, 60),
    "impossible": (30, 50),
}

# `troopSendCap()` — hard 이상은 이웃 병력 대비 이만큼을 남겨 둔다
RETAIN_FRACTION: dict[str, float] = {"hard": 0.75, "impossible": 0.9}

# `isAttackTooWeak` — hard 이상은 상대 병력의 20% 미만이면 아예 안 친다
MIN_ATTACK_RATIO = 0.2

BOAT_CHANCE_NO_ENEMY = 5      # `random.chance(5)` = 1/5
BOAT_CHANCE_WITH_ENEMY = 10   # `random.chance(10)` = 1/10


@dataclass
class NationBot:
    """플레이어 한 명을 원본 규칙으로 조종한다."""

    pid: int
    rng: random.Random
    difficulty: str = "medium"

    trigger_ratio: float = 0.0
    reserve_ratio: float = 0.0
    expand_ratio: float = 0.0
    attack_rate: int = 0
    attack_tick: int = 0
    _bot_troops_sent: float = 0.0
    _build_tick: int = field(default=0)

    def __post_init__(self) -> None:
        self.trigger_ratio = self.rng.randint(50, 60) / 100
        self.reserve_ratio = self.rng.randint(30, 40) / 100
        self.expand_ratio = self.rng.randint(10, 20) / 100
        lo, hi = ATTACK_RATE.get(self.difficulty, ATTACK_RATE["medium"])
        self.attack_rate = self.rng.randint(lo, hi)
        self.attack_tick = self.rng.randrange(self.attack_rate)
        self._build_tick = self.rng.randrange(self.attack_rate)

    # --- 진입점 -----------------------------------------------------------

    def tick(self, st: GameState) -> None:
        """`NationExecution.tick` — **반응 주기에 걸린 tick 에만** 판단한다."""
        p = st.players.get(self.pid)
        if p is None or not p.alive or st.over:
            return
        if st.tick_count % self.attack_rate == self._build_tick:
            self._structures(st)
        if st.tick_count % self.attack_rate != self.attack_tick:
            return
        self._maybe_attack(st)

    # --- 공격 -------------------------------------------------------------

    def _maybe_attack(self, st: GameState) -> None:
        p = st.players[self.pid]
        reachable = st.border_targets(self.pid)

        has_neutral = None in reachable
        others = [st.players[o] for o in reachable
                  if o is not None and o in st.players and st.players[o].alive]
        # **병력이 적은 쪽부터.** 원본이 오름차순으로 정렬해 그 순서로 고른다.
        others.sort(key=lambda q: q.troops)
        enemies = [q for q in others if not st.diplomacy.is_friendly(self.pid, q.pid)]

        if has_neutral and self._send_attack(st, None):
            return

        if not enemies:
            if self.rng.randrange(BOAT_CHANCE_NO_ENEMY) == 0:
                self._boat(st, enemies)
        else:
            if self.rng.randrange(BOAT_CHANCE_WITH_ENEMY) == 0:
                self._boat(st, enemies)
                return
            self._alliance_requests(st, enemies)

        self._attack_best(st, enemies)

    def _attack_best(self, st: GameState, enemies: list) -> None:
        """가장 약한 적부터 시도한다. `sendAttack` 이 여유를 보고 알아서 거른다."""
        for foe in enemies:
            if self._send_attack(st, foe.pid):
                return

    def _send_attack(self, st: GameState, target: int | None) -> bool:
        troops = self._attack_troops(st, target)
        if troops is None:
            return False
        p = st.players[self.pid]
        saved = p.attack_ratio
        p.attack_ratio = min(1.0, troops / p.troops) if p.troops > 0 else 0.0
        try:
            return st.launch_attack(self.pid, target) is not None
        finally:
            p.attack_ratio = saved

    def _attack_troops(self, st: GameState, target: int | None) -> float | None:
        """`calculateAttackTroops` — **남겨 둘 양**이 표적에 따라 다르다.

        중립이면 `expand_ratio`(10~20%)만 남기고 거의 전부 쏟는다. 사람이면
        `reserve_ratio`(30~40%)를 남긴다. 이 비대칭이 봇의 성격이다."""
        p = st.players[self.pid]
        cap = p.max_troops(st.tiles(self.pid))
        if cap <= 0:
            return None
        if p.troops / cap < self.trigger_ratio:
            return None          # 아직 여유가 없다 — 공격 자체를 고려하지 않는다

        foe = st.players.get(target) if target is not None else None
        bot_with_structures = (
            foe is not None and foe.kind == "bot"
            and any(u.utype in STRUCTURES for u in foe.units.units))
        ratio = self.expand_ratio if (foe is None or bot_with_structures) \
            else self.reserve_ratio
        keep = cap * ratio
        troops = p.troops - keep

        if foe is not None:
            troops = min(troops, self._send_cap(st))
        if troops < C.ATTACK_MIN_TROOPS:
            return None
        # hard 이상은 상대 병력의 20% 미만으로는 안 친다 — 병력만 버리는 짓이다
        if foe is not None and self.difficulty in RETAIN_FRACTION \
                and troops < foe.troops * MIN_ATTACK_RATIO:
            return None
        return troops

    def _send_cap(self, st: GameState) -> float:
        """`troopSendCap()` — hard 이상은 가장 센 이웃 대비 일정 비율을 남겨 둔다.
        easy/medium 은 상한이 없다."""
        frac = RETAIN_FRACTION.get(self.difficulty)
        if frac is None:
            return float("inf")
        p = st.players[self.pid]
        strongest = 0.0
        for o in st.border_targets(self.pid):
            if o is None or o not in st.players:
                continue
            strongest = max(strongest, st.players[o].troops)
        return max(0.0, p.troops - strongest * frac)

    # --- 상륙 -------------------------------------------------------------

    def _boat(self, st: GameState, enemies: list) -> None:
        """`attackWithRandomBoat` — 해안에서 무작위로 고른 목표에 상륙.

        빈 땅·봇 땅을 먼저 찾고, 없으면 사람 땅을 본다."""
        shore = shoreline_tiles(st.gmap, self.pid)
        if not len(shore):
            return
        src = int(self.rng.choice(shore.tolist()))
        for high_interest in (True, False):
            dst = self._boat_target(st, src, high_interest)
            if dst is not None:
                st.send_boat(self.pid, dst)
                return

    def _boat_target(self, st: GameState, src: int, high_interest: bool):
        gm = st.gmap
        sx, sy = gm.xy(src)
        for _ in range(20):
            r = self.rng.randint(4, 80)
            ang = self.rng.random() * 6.283185
            x = int(sx + r * __import__("math").cos(ang))
            y = int(sy + r * __import__("math").sin(ang))
            if not (0 <= x < gm.width and 0 <= y < gm.height):
                continue
            t = gm.ref(x, y)
            if not gm.passable(t):
                continue
            owner = int(gm.owner[t])
            if owner == self.pid:
                continue
            if owner >= 0 and st.diplomacy.is_friendly(self.pid, owner):
                continue
            interesting = owner < 0 or st.players[owner].kind == "bot"
            if high_interest and not interesting:
                continue
            return t
        return None

    # --- 외교 -------------------------------------------------------------

    def _alliance_requests(self, st: GameState, enemies: list) -> None:
        """국경을 맞댄 적에게 동맹을 건다. 들어온 요청도 여기서 받는다."""
        d = st.diplomacy
        for requestor, recipients in list(d.pending.items()):
            if self.pid in recipients and requestor in st.players:
                if self.rng.random() < 0.5:
                    st.accept_alliance(self.pid, requestor)
                else:
                    d.reject(self.pid, requestor)
        for foe in enemies:
            if self.rng.randrange(4) == 0:
                st.request_alliance(self.pid, foe.pid)

    # --- 건설 -------------------------------------------------------------

    def _structures(self, st: GameState) -> None:
        """`NationStructureBehavior` — 도시를 우선하되 골드가 놀지 않게 한다.

        원본은 표적 근처에 방어초소를, 항구를 해안에 짓는 등 자리까지 고르지만
        여기서는 자리 고르기를 `find_spot` 에 맡긴다."""
        p = st.players[self.pid]
        refs = st.gmap.owned_refs(self.pid)
        if not len(refs):
            return
        order = (UnitType.CITY, UnitType.PORT, UnitType.MISSILE_SILO,
                 UnitType.DEFENSE_POST, UnitType.SAM_LAUNCHER, UnitType.FACTORY)
        affordable = [(p.units.cost(u), u) for u in order
                      if p.gold >= p.units.cost(u)]
        if affordable:
            for _, utype in sorted(affordable, key=lambda pair: pair[0], reverse=True):
                near = int(self.rng.choice(refs.tolist()))
                if st.build(self.pid, utype, near) is not None:
                    return
        if p.gold >= p.units.cost(UnitType.WARSHIP) and p.units.of(UnitType.PORT):
            port = self.rng.choice(p.units.of(UnitType.PORT))
            for n in st.gmap.neighbors(port.tile):
                if st.build_warship(self.pid, n) is not None:
                    return
        # 사일로가 있으면 가장 큰 적을 노린다 (`NationNukeBehavior` 의 축소판)
        if p.units.of(UnitType.MISSILE_SILO):
            for utype in (UnitType.HYDROGEN_BOMB, UnitType.ATOM_BOMB):
                if p.gold < p.units.cost(utype):
                    continue
                foes = [q for q in st.alive if q.pid != self.pid
                        and not st.diplomacy.is_friendly(self.pid, q.pid)]
                if not foes:
                    return
                biggest = max(foes, key=lambda q: st.tiles(q.pid))
                tiles = st.gmap.owned_refs(biggest.pid)
                if len(tiles):
                    st.launch_nuke(self.pid, utype,
                                   int(self.rng.choice(tiles.tolist())))
                return


def attach(st: GameState, rng: random.Random,
           difficulty: str = "medium") -> list[NationBot]:
    """모든 AI 플레이어에 Nation 봇을 붙인다."""
    bots = []
    for p in st.players.values():
        if p.kind == "human":
            continue
        p.difficulty = difficulty
        bots.append(NationBot(pid=p.pid, rng=rng, difficulty=difficulty))
    return bots
