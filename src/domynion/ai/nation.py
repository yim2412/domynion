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
from ..core import emoji
from ..core.relations import Relation
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

# 건설 가중치. **"가장 비싼 것"으로 고르면 안 된다** — 방어초소는 25만에서 상한이라
# 무한 흡수구가 되어, 한 판에 41개를 짓고 도시(50만~100만)·사일로(100만)에 영영
# 못 간다. 실측으로 그렇게 됐고 철도·핵이 판에서 아예 안 나왔다.
# 원본 `NationStructureBehavior` 는 종류별 목적이 따로 있는데, 그 판단을 다 옮기기
# 전까지는 가중 무작위로 종류가 골고루 나오게 한다.
BUILD_WEIGHT = {
    UnitType.CITY: 5.0,          # 병력 상한을 직접 올린다
    UnitType.FACTORY: 3.0,       # 기차 — 육지 수입
    UnitType.MISSILE_SILO: 3.0,  # 핵을 쏘려면 있어야 한다
    UnitType.PORT: 2.0,          # 무역선 — 바다 수입
    UnitType.SAM_LAUNCHER: 1.5,
    UnitType.DEFENSE_POST: 0.6,
}

# ⚠ 아래는 **원본 값이 아니라 우리가 정한 것**이다.
# 비용이 상한에 걸리는 종류(초소 25만·SAM 300만)는 그 뒤로 값이 안 오르므로,
# 개수를 안 막으면 골드를 무한히 빨아들여 도시(100만)·사일로(100만)에 못 간다.
# 실측: 초소 72개를 짓고 사일로가 0개였다. 영토 1,500칸당 하나로 묶는다.
# 원본 `NationStructureBehavior` 를 옮기면 이 표는 사라진다.
STRUCTURE_CAP_PER_TILES = {
    UnitType.DEFENSE_POST: 1_500,
    UnitType.SAM_LAUNCHER: 4_000,
    UnitType.PORT: 3_000,
}


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
        self._embargoes(st)
        self._maybe_attack(st)

    # --- 금수 -------------------------------------------------------------

    def _embargoes(self, st: GameState) -> None:
        """`NationExecution` — 적대적이면 금수를 걸고, 중립으로 돌아오면 푼다.

        **거는 문턱과 푸는 문턱이 다르다**(적대에서 걸고 중립에서 푼다). 같으면
        관계가 문턱 근처에서 떨릴 때 금수가 매 tick 켜졌다 꺼진다.

        hard 이상은 중립이 돼도 안 풀고, impossible 은 우호가 돼도 안 푼다 —
        어려울수록 한 번 틀어지면 되돌리기 어렵다."""
        d = st.diplomacy
        for other in st.border_targets(self.pid):
            if other is None or other not in st.players:
                continue
            rel = st.relation_of(self.pid, other)
            on = d.embargoed(self.pid, other)
            hostile = rel <= Relation.HOSTILE
            if hostile and not on and not d.same_team(self.pid, other):
                d.start_embargo(self.pid, other)
            elif rel >= Relation.FRIENDLY and on and self.difficulty != "impossible":
                d.stop_embargo(self.pid, other)
            elif rel >= Relation.NEUTRAL and on:
                if self.difficulty not in ("hard", "impossible"):
                    d.stop_embargo(self.pid, other)

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

        # 동맹의 부탁이 먼저다 — 중립 확장보다 우선한다. 안 그러면 부탁이
        # 10초 안에 만료돼 아무도 도와주지 않는다.
        if self._assist_allies(st):
            return

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

    def _assist_allies(self, st: GameState) -> bool:
        """`assistAllies` — 동맹이 찍은 표적을 대신 친다.

        **이게 동맹의 실질적 효용이다.** 없으면 동맹은 "서로 안 친다"는 소극적
        약속일 뿐이고 함께 싸우는 수단이 없다.

        거절할 때도 말을 남긴다 — 왜 안 도와주는지 모르면 사람은 동맹을 관리할
        수 없다. 사이가 덜 좋으면 🥱, 표적이 나면 🥺, 표적이 내 동맹이면 🕊️.
        """
        for ally in st.diplomacy.allies_of(self.pid):
            asked = st.targets_of(ally)
            if not asked:
                continue
            if st.relation_of(self.pid, ally) < Relation.FRIENDLY:
                st.ai_emoji(self.pid, ally, emoji.ASSIST_RELATION_TOO_LOW)
                continue
            for foe in asked:
                if foe == self.pid:
                    st.ai_emoji(self.pid, ally, emoji.ASSIST_TARGET_ME)
                    continue
                if st.diplomacy.is_friendly(self.pid, foe):
                    st.ai_emoji(self.pid, ally, emoji.ASSIST_TARGET_ALLY)
                    continue
                if not self._send_attack(st, foe):
                    continue
                # 도와준 대가로 부탁한 쪽을 조금 낮춰 본다 — 계속 부려먹으면
                # 결국 사이가 나빠져 더는 안 도와준다.
                st.relate(self.pid, ally, C.REL_ASSIST_COST)
                st.ai_emoji(self.pid, ally, emoji.ASSIST_ACCEPT)
                return True
        return False

    def _attack_best(self, st: GameState, enemies: list) -> None:
        """가장 약한 적부터 시도한다. `sendAttack` 이 여유를 보고 알아서 거른다."""
        for foe in enemies:
            if self._send_attack(st, foe.pid):
                return

    def _should_attack(self, st: GameState, target: int | None) -> bool:
        """`shouldAttack` — **낮은 난이도는 사람을 봐준다.**

        중립·나라·봇은 언제나 친다. 사람만 난이도별로 걸러진다:
        easy 는 네 번 중 세 번을 그냥 넘기고, medium 은 네 번 중 한 번을 넘긴다.
        hard 이상은 봐주지 않는다.

        **배신자는 난이도와 무관하게 친다** — 낙인이 붙은 동안은 사람도 예외가
        아니다. 이게 없으면 easy 에서 배신에 아무 대가가 없다.
        """
        if target is None:
            return True
        foe = st.players.get(target)
        if foe is None or foe.kind != "human" or st.is_traitor(target):
            return True
        if self.difficulty == "easy":
            return self.rng.randrange(4) == 0
        if self.difficulty == "medium":
            return self.rng.randrange(4) != 0
        return True

    def _send_attack(self, st: GameState, target: int | None) -> bool:
        if not self._should_attack(st, target):
            return False
        troops = self._attack_troops(st, target)
        if troops is None:
            return False
        if target is not None:
            # 관계가 나쁘지 않은데 친다 = 내가 먼저 시작한 것(😈).
            # 이미 사이가 나쁘면 보복으로 본다(😡). `maybeSendAttackEmoji` 그대로.
            if st.relation_of(self.pid, target) >= Relation.NEUTRAL:
                if self.rng.randrange(2) == 0:
                    st.ai_emoji(self.pid, target, emoji.AGGRESSIVE_ATTACK)
            elif self.rng.randrange(4) == 0:
                st.ai_emoji(self.pid, target, emoji.ATTACK)
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
        """국경을 맞댄 적에게 동맹을 건다. 들어온 요청도 여기서 받는다.

        **수락 여부는 관계가 정한다.** 동전 던지기로 받으면 방금 나를 핵으로 친
        상대와도 절반 확률로 손을 잡아, 사람이 외교를 관리할 이유가 사라진다
        (`NationAllianceBehavior` : 중립 미만이면 거절, 우호면 거의 수락)."""
        d = st.diplomacy
        for requestor, recipients in list(d.pending.items()):
            if self.pid in recipients and requestor in st.players:
                if self._accepts_alliance(st, requestor):
                    st.accept_alliance(self.pid, requestor)
                else:
                    d.reject(self.pid, requestor)
        for foe in enemies:
            # 적대적인 상대에게는 먼저 손을 내밀지 않는다
            if st.relation_of(self.pid, foe.pid) <= Relation.HOSTILE:
                continue
            if self.rng.randrange(4) == 0:
                st.request_alliance(self.pid, foe.pid)

    def _accepts_alliance(self, st: GameState, requestor: int) -> bool:
        """받을 것인가. 관계보다 **배신자 낙인이 먼저**다.

        원본은 배신자를 90% 거절한다(`nextInt(0, 100) >= 10`). 관계만 보면 방금
        남을 배신한 자가 관계가 중립이라는 이유로 받아들여진다 — 그러면 배신에
        비용이 없어져 동맹 자체가 의미를 잃는다."""
        if st.is_traitor(requestor) and self.rng.randrange(100) >= 10:
            st.ai_emoji(self.pid, requestor, emoji.CONFUSED)
            return False
        rel = st.relation_of(self.pid, requestor)
        if rel < Relation.NEUTRAL:
            st.ai_emoji(self.pid, requestor, emoji.CONFUSED)
            return False                       # 사이가 나쁘면 무조건 거절
        if rel >= Relation.FRIENDLY:
            ok = self.rng.randrange(3) != 0    # 우호면 대체로 받는다
        else:
            ok = self.rng.random() < 0.5       # 중립은 여전히 반반
        if ok:
            st.ai_emoji(self.pid, requestor, emoji.HANDSHAKE)
        return ok

    # --- 건설 -------------------------------------------------------------

    def _structures(self, st: GameState) -> None:
        """`NationStructureBehavior` — 도시를 우선하되 골드가 놀지 않게 한다.

        원본은 표적 근처에 방어초소를, 항구를 해안에 짓는 등 자리까지 고르지만
        여기서는 자리 고르기를 `find_spot` 에 맡긴다."""
        p = st.players[self.pid]
        refs = st.gmap.owned_refs(self.pid)
        if not len(refs):
            return
        tiles = st.tiles(self.pid)
        affordable = [
            u for u in BUILD_WEIGHT
            if p.gold >= p.units.cost(u)
            and (u not in STRUCTURE_CAP_PER_TILES
                 or p.units.owned(u) < max(1, tiles // STRUCTURE_CAP_PER_TILES[u]))
        ]
        if affordable:
            weights = [BUILD_WEIGHT[u] for u in affordable]
            for utype in self.rng.choices(affordable, weights=weights, k=3):
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
                # 핵에도 같은 봐주기가 걸린다(`NationNukeBehavior` 가
                # `shouldAttack` 을 먼저 본다). 없으면 easy 에서 사람을 안 치면서
                # 핵만 떨구는 이상한 AI 가 된다.
                if not self._should_attack(st, biggest.pid):
                    return
                tiles = st.gmap.owned_refs(biggest.pid)
                if len(tiles):
                    st.launch_nuke(self.pid, utype,
                                   int(self.rng.choice(tiles.tolist())))
                return


def attach(st: GameState, rng: random.Random,
           difficulty: str = "medium") -> list:
    """AI 를 붙인다. **나라와 봇은 다른 AI 다.**

    원본은 `NationExecution` 과 `TribeExecution` 을 따로 돌린다 — 봇은 동맹을 다
    받아 주고 건물을 지운다. 전부 Nation 으로 돌리면 그 성격이 사라진다.
    """
    from .tribe import TribeBot          # 순환 import 를 피한다

    st.difficulty = difficulty
    out: list = []
    for p in st.players.values():
        if p.kind == "human":
            continue
        p.difficulty = difficulty
        if p.kind == "bot":
            out.append(TribeBot(pid=p.pid, rng=rng))
        else:
            out.append(NationBot(pid=p.pid, rng=rng, difficulty=difficulty))
    return out
