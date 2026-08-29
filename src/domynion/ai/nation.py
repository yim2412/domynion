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
from ..core.constants import Terrain
from ..core.engine import GameState
from ..core.gamemap import TileRef
from ..core import emoji
from ..core.relations import Relation
from ..core.naval import shoreline_tiles
from ..core.units import STRUCTURES, UnitStore, UnitType
from .nukes import NationNukeBehavior
from .alliance import NationAllianceBehavior
from .chatter import NationChatter
from .mirv import NationMIRVBehavior
from .structures import NationStructureBehavior

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

# `getAttackStrategies()` — **난이도는 문턱이 아니라 순서로 들어간다.**
#
# ⚠ 이식 누락 예순다섯. 우리는 이 자리에 *"가장 약한 적부터"* 한 줄만 두고 있었다
# (`_attack_best`). 원본은 열세 개의 전략을 난이도별로 **다른 순서**로 늘어놓고
# 위에서부터 하나가 성공할 때까지 내려간다. 원본 주석: *"Easy nations get the
# dumbest order, impossible nations get the smartest order."*
#
# 순서가 곧 성격이다 — impossible 은 `retaliate` 가 맨 위(맞으면 바로 되받는다)고
# easy 는 `nuked`(핵 맞은 빈 땅 줍기)가 맨 위다. 이걸 한 줄로 접으면 **난이도가
# 반응 주기와 사람 봐주기 말고는 아무 데도 안 남는다.**
ATTACK_STRATEGIES: dict[str, tuple[str, ...]] = {
    "easy": ("nuked", "bots", "retaliate", "assist", "betray", "hated",
             "weakest"),
    "medium": ("bots", "nuked", "retaliate", "assist", "betray", "hated",
               "afk", "traitor", "weakest", "island", "donate"),
    "hard": ("bots", "retaliate", "assist", "betray", "nuked", "traitor",
             "afk", "hated", "very_weak", "victim", "weakest", "island",
             "donate"),
    "impossible": ("retaliate", "bots", "very_weak", "assist", "traitor",
                   "afk", "betray", "victim", "nuked", "hated", "weakest",
                   "island", "donate"),
}

# `getBotAttackMaxParallelism()` — 한 번에 몇 개의 봇을 동시에 칠 것인가.
# medium 만 1/2 확률로 1 또는 2 다.
BOT_PARALLELISM: dict[str, int] = {"easy": 1, "hard": 3, "impossible": 100}

# FFA 판단에 쓰는 배수들. **팀전이면 전부 꺼진다** — 원본 주석: 팀전에서는
# 동료가 병력을 보내 주므로 나보다 센 상대에게 덤벼도 된다.
FFA_TRAITOR_MARGIN = 1.2      # 배신자가 나보다 1.2배 넘게 세면 안 친다
FFA_VICTIM_MARGIN = 1.2       # 얻어맞는 중인 상대도 같은 문턱
FFA_VERY_WEAK_MARGIN = 1.2
FFA_HATED_MARGIN = 3.0        # 미운 상대가 3배 넘게 세면 참는다
VICTIM_INCOMING_RATIO = 0.5   # 자기 병력의 50% 넘게 얻어맞는 중이면 "먹잇감"
VERY_WEAK_RATIO = 0.15        # 상한의 15% 미만이면 "아주 약하다"
ISLAND_SECOND_CHANCE = 3      # 1/3 확률로 두 번째로 가까운 섬을 고른다

# `calculateBotAttackTroops` — 봇에게는 상대 병력의 네 배만 보낸다. 여유가
# 두 배도 안 되면 아예 안 친다(easy 는 예외 — 있는 대로 쏟는다).
BOT_ATTACK_MULTIPLE = 4
BOT_ATTACK_MIN_MULTIPLE = 2

# 건설 판단은 `structures.NationStructureBehavior` 로 옮겼다(2026-08-24).
#
# 여기 있던 `BUILD_WEIGHT`(가중 무작위)와 `STRUCTURE_CAP_PER_TILES`(영토 대비 개수
# 상한)는 **둘 다 원본에 없는 우리 발명품**이었다. 원본은 도시 수 대비 비율로
# 종류를 정하고, 밀도가 높으면 새로 짓는 대신 올린다. 초소는 건설 순서에 아예 없고
# 공격받는 중에만 지어진다 — 초소가 골드를 빨아들이던 문제가 거기서 사라지므로
# 상한 표도 함께 필요 없어졌다.


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
    # 건물 판단은 통째로 여기 들어 있다. `__post_init__` 에서 만든다.
    structures: NationStructureBehavior | None = None
    # 핵 판단도 통째로 분리돼 있다. `__post_init__` 에서 만든다.
    nukes: "NationNukeBehavior | None" = None
    # 내가 띄운 무역선들(`trackedTradeShips`). 나포당하면 보복한다.
    _tracked_trade: set = field(default_factory=set)
    # 내가 띄운 수송선들(`trackedTransportShips`). 격침당하면 보복한다.
    _tracked_boats: list = field(default_factory=list)
    # 이미 대응한 상륙선(`dealtWithTransportShip`). 한 척에 한 번만 대응한다.
    _dealt_boats: set = field(default_factory=set)

    def __post_init__(self) -> None:
        self.trigger_ratio = self.rng.randint(50, 60) / 100
        self.reserve_ratio = self.rng.randint(30, 40) / 100
        self.expand_ratio = self.rng.randint(10, 20) / 100
        lo, hi = ATTACK_RATE.get(self.difficulty, ATTACK_RATE["medium"])
        self.attack_rate = self.rng.randint(lo, hi)
        self.attack_tick = self.rng.randrange(self.attack_rate)
        self._build_tick = self.rng.randrange(self.attack_rate)
        self.structures = NationStructureBehavior(self.pid, self.rng, self.difficulty)
        self.mirv = NationMIRVBehavior(self.pid, self.rng, self.difficulty)
        self.chatter = NationChatter(self.pid, self.rng)
        self.alliance = NationAllianceBehavior(self.pid, self.rng,
                                              self.difficulty)
        # ⚠ 체감 비용의 출발점은 **실비용**이다. 보유량에 따라 오르는 건물 비용과
        # 달리 핵은 "쏜 횟수"로만 오르므로, 여기서 한 번 잡아 두고 발사마다 곱한다.
        store = UnitStore()
        self.nukes = NationNukeBehavior(
            self.pid, self.rng, self.difficulty,
            atom_cost=store.cost(UnitType.ATOM_BOMB),
            hydro_cost=store.cost(UnitType.HYDROGEN_BOMB))

    # --- 진입점 -----------------------------------------------------------

    def tick(self, st: GameState) -> None:
        """`NationExecution.tick` — **반응 주기에 걸린 tick 에만** 판단한다."""
        p = st.players.get(self.pid)
        if p is None or not p.alive or st.over:
            return
        # ⚠ 추적은 **매 tick** 이다. 판단 주기에만 보면 그 사이에 나포됐다가
        # 도착까지 끝난 배를 놓친다(원본도 `trackShipsAndRetaliate` 를 매 tick 부른다).
        self._track_trade_ships(st)
        self._track_transport_ships(st)
        self._counter_infestation(st)
        self._intercept_incoming(st)
        if st.tick_count % self.attack_rate == self._build_tick:
            self._structures(st)
        if st.tick_count % self.attack_rate != self.attack_tick:
            return
        # 잡담이 판단 사슬의 **맨 앞**이다(원본 `NationExecution.tick` 순서:
        # `maybeSendCasualEmoji` → `updateRelationsFromEmbargos` → 동맹 → MIRV → …).
        # 확률(1/16 ~ 1/10000)이 **판단 tick 기준**이라 여기서 불러야 원본과 같은
        # 빈도가 된다 — 매 tick 부르면 반응 주기(수십 tick)만큼 수다스러워진다.
        self.chatter.tick(st)
        self._embargoes(st)
        # 연장 요청에 답하는 것은 **동맹 요청을 받는 것과 같은 판단**이다
        # (원본도 `getAllianceDecision(human, true)` 를 그대로 쓴다).
        self._alliance_extensions(st)
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

        others = [st.players[o] for o in reachable
                  if o is not None and o in st.players and st.players[o].alive]
        # **병력이 적은 쪽부터.** 원본이 오름차순으로 정렬해 그 순서로 고른다.
        others.sort(key=lambda q: q.troops)
        enemies = [q for q in others if not st.diplomacy.is_friendly(self.pid, q.pid)]
        friends = [q for q in others if st.diplomacy.is_friendly(self.pid, q.pid)]

        # ⚠ **낙진 없는 중립만 여기서 친다**(`borderHasNonNukedTerraNullius`).
        # 전에는 중립이면 낙진이든 아니든 밀고 들어갔다 — 낙진은 방어를 크게
        # 올리므로 그쪽 확장은 손해고, 원본은 낙진 땅을 난이도 순서의 `nuked`
        # 자리에서만 노린다. 이 파일 맨 위 주석은 처음부터 "낙진 없는 중립"이라고
        # 적혀 있었다. **적어만 두고 코드는 안 그랬다.**
        clean_tn, _ = st.neutral_borders(self.pid)
        if clean_tn and self._send_attack(st, None):
            return

        if not enemies:
            if self.rng.randrange(BOAT_CHANCE_NO_ENEMY) == 0:
                self._boat(st, enemies)
        else:
            if self.rng.randrange(BOAT_CHANCE_WITH_ENEMY) == 0:
                self._boat(st, enemies)
                return
            self._alliance_requests(st, enemies)

        self._attack_best_target(st, friends, enemies)

    # --- 표적 고르기 (`attackBestTarget` + `getAttackStrategies`) ----------

    def _attack_best_target(self, st: GameState, friends: list,
                            enemies: list) -> None:
        """`attackBestTarget` — 두 개의 관문을 지나 **난이도별 전략 순서**로 간다.

        ⚠ 관문 둘이 우리에게 잘못 놓여 있었다. `trigger_ratio` 검사가
        `_attack_troops` 안에 있어서 **표적을 고르는 일 전체가 아니라 병력 계산만**
        막고 있었고, `reserve_ratio` 관문은 아예 없었다.

        원본 순서:

        1. **구조물을 가진 봇 이웃이 있으면 비율 검사보다 먼저 친다.**
           원본 주석대로 — 시작 골드가 많은 판에서 나라는 도시를 잔뜩 지어
           확장이 느려지고, 그 사이 봇이 건물을 훔쳐 **지워 버린다.**
        2. `reserve_ratio` 에 못 미치면 **아무것도 안 한다**(모아 둔다).
        3. `trigger_ratio` 에 못 미치면 10번 중 9번은 안 한다(1/10 은 그냥 간다).
        """
        if self._has_bot_neighbour_with_structures(st, enemies):
            if self._attack_bots(st, enemies):
                return
        p = st.players[self.pid]
        cap = p.max_troops(st.tiles(self.pid))
        if cap <= 0:
            return
        ratio = p.troops / cap
        if ratio < self.reserve_ratio:
            return
        if ratio < self.trigger_ratio and self.rng.randrange(10) != 0:
            return

        for name in ATTACK_STRATEGIES.get(self.difficulty,
                                          ATTACK_STRATEGIES["medium"]):
            if self._STRATEGIES[name](self, st, friends, enemies):
                return

    # --- 전략 열셋 --------------------------------------------------------
    #
    # 전부 **성공하면 True** 를 돌려주고, 사다리는 거기서 멈춘다.

    def _s_retaliate(self, st, friends, enemies) -> bool:
        """`retaliate` — 나를 가장 크게 치고 있는 쪽을 되받는다.

        ⚠ `force=True` 다 — `shouldAttack` 의 사람 봐주기를 건너뛴다. 맞고 있는데
        난이도 때문에 반격을 못 하면 easy 나라는 그냥 샌드백이 된다."""
        who = self._biggest_incoming_attacker(st)
        return who is not None and self._send_attack(st, who, force=True)

    def _s_bots(self, st, friends, enemies) -> bool:
        return self._attack_bots(st, enemies)

    def _s_assist(self, st, friends, enemies) -> bool:
        return self._assist_allies(st)

    def _s_traitor(self, st, friends, enemies) -> bool:
        """배신자를 친다 — **나보다 크게 세지 않을 때만**(FFA)."""
        me = st.players[self.pid]
        for foe in enemies:                      # 이미 병력 오름차순이다
            if st.is_traitor(foe.pid) and (
                    not self._is_ffa(st)
                    or foe.troops < me.troops * FFA_TRAITOR_MARGIN):
                return self._send_attack(st, foe.pid)
        return False

    def _s_afk(self, st, friends, enemies) -> bool:
        """`afk` — 접속이 끊긴 사람을 노린다.

        ⚠ **우리에게는 이 개념이 없다.** 헤드리스 판에도 UI 판에도 "연결이 끊긴
        플레이어"가 없다(`isDisconnected`). 자리를 비워 두는 것은 **사다리의 순서를
        보존하기 위해서다** — 지우면 medium 의 `traitor` 가 한 칸 올라간다.
        멀티플레이가 생기면 여기에 붙인다."""
        return False

    def _s_betray(self, st, friends, enemies) -> bool:
        return self._maybe_betray_and_attack(st, friends, enemies)

    def _s_nuked(self, st, friends, enemies) -> bool:
        """국경에 **낙진이 앉은 빈 땅**이 있으면 그리로 확장한다.

        핵이 터진 자리는 주인이 없어졌으므로 주울 수 있다. 다만 방어가 붙어 있어
        평소 확장보다 비싸고, 그래서 난이도마다 순서가 다르다."""
        _, nuked = st.neutral_borders(self.pid)
        return nuked and self._send_attack(st, None)

    def _s_victim(self, st, friends, enemies) -> bool:
        """남에게 크게 얻어맞는 중인 상대에 **올라탄다**(들어오는 공격이 그 나라
        병력의 50% 초과)."""
        me = st.players[self.pid]
        for foe in enemies:
            if self._is_ffa(st) and foe.troops > me.troops * FFA_VICTIM_MARGIN:
                continue
            incoming = sum(a.troops for a in st.attacks if a.target == foe.pid)
            if incoming > foe.troops * VICTIM_INCOMING_RATIO:
                return self._send_attack(st, foe.pid)
        return False

    def _s_hated(self, st, friends, enemies) -> bool:
        """관계가 **적대**인 상대를 미운 순서대로 친다(`allRelationsSorted`).

        ⚠ 국경 이웃만 보는 것이 아니다 — 관계표 전체를 본다. 그래서 이 전략은
        상륙까지 간다(`sendAttack` 이 국경이 없으면 배를 띄운다)."""
        me = st.players[self.pid]
        alive = {q.pid for q in st.alive}
        for pid, rel in me.relations.sorted_by_relation(alive):
            if rel is not Relation.HOSTILE:
                continue
            if pid == self.pid or st.diplomacy.is_friendly(self.pid, pid):
                continue
            other = st.players.get(pid)
            if other is None:
                continue
            if self._is_ffa(st) and other.troops > me.troops * FFA_HATED_MARGIN:
                continue
            return self._send_attack(st, pid)
        return False

    def _s_very_weak(self, st, friends, enemies) -> bool:
        """상한의 15% 미만으로 쪼그라든 상대. 원본 주석이 대놓고 말한다 —
        **MIRV 맞은 나라를 주우라는 것이다.**"""
        me = st.players[self.pid]
        for foe in enemies:
            cap = foe.max_troops(max(1, st.tiles(foe.pid)))
            if foe.troops >= cap * VERY_WEAK_RATIO:
                continue
            if self._is_ffa(st) and foe.troops >= me.troops * FFA_VERY_WEAK_MARGIN:
                continue
            return self._send_attack(st, foe.pid)
        return False

    def _s_weakest(self, st, friends, enemies) -> bool:
        """가장 약한 이웃. ⚠ **FFA 에서는 나보다 약할 때만 친다** — 이 조건이
        없어서 우리 나라들은 자기보다 센 이웃에게도 계속 들이받고 있었다."""
        if not enemies:
            return False
        me, foe = st.players[self.pid], enemies[0]
        if self._is_ffa(st) and foe.troops >= me.troops:
            return False
        return self._send_attack(st, foe.pid)

    def _s_island(self, st, friends, enemies) -> bool:
        """국경에 적이 하나도 없을 때만 — 바다 건너 가장 가까운 적을 노린다."""
        if enemies:
            return False
        return self._island_boat(st)

    def _s_donate(self, st, friends, enemies) -> bool:
        """`donateTroops` — **팀전 전용이다.** FFA 판에서는 원본도 첫 줄에서
        돌아선다(`gameMode !== Team`). 우리 판은 전부 FFA 라 항상 False 지만,
        `afk` 와 같은 이유로 사다리에 자리를 남겨 둔다."""
        return False

    # 이름 → 메서드. **메서드 정의 뒤에 와야 한다**(클래스 본문은 위에서
    # 아래로 실행된다). 위에 두면 `NameError` 로 import 자체가 죽는다.
    _STRATEGIES = {
        "retaliate": _s_retaliate, "bots": _s_bots, "assist": _s_assist,
        "traitor": _s_traitor, "afk": _s_afk, "betray": _s_betray,
        "nuked": _s_nuked, "victim": _s_victim, "hated": _s_hated,
        "very_weak": _s_very_weak, "weakest": _s_weakest,
        "island": _s_island, "donate": _s_donate,
    }

    # --- 전략이 쓰는 것들 -------------------------------------------------

    def _is_ffa(self, st: GameState) -> bool:
        """팀이 하나도 없으면 FFA 다. 우리 판은 지금 전부 FFA 다."""
        return not any(t is not None for t in st.diplomacy.teams.values())

    def _biggest_incoming_attacker(self, st: GameState) -> "int | None":
        """`findIncomingAttackPlayer` — 나에게 들어오는 공격 중 **가장 큰 것**의
        주인. 친한 쪽은 빼고, **내가 봇이 아니면 봇의 공격은 무시한다**(봇에게
        되받아 봐야 판이 안 바뀐다)."""
        me = st.players[self.pid]
        best, best_troops = None, 0.0
        for a in st.attacks:
            if a.target != self.pid or a.attacker is None:
                continue
            if st.diplomacy.is_friendly(self.pid, a.attacker):
                continue
            other = st.players.get(a.attacker)
            if other is None:
                continue
            if not me.is_bot and other.is_bot:
                continue
            if a.troops > best_troops:
                best, best_troops = a.attacker, a.troops
        return best

    def _has_bot_neighbour_with_structures(self, st: GameState,
                                           enemies: list) -> bool:
        return any(q.kind == "bot"
                   and any(u.utype in STRUCTURES for u in q.units.units)
                   for q in enemies)

    def _attack_bots(self, st: GameState, enemies: list) -> bool:
        """`attackBots` — **봇은 여러 개를 동시에 친다.**

        ⚠ 이게 없어서 우리 나라들은 봇을 한 번에 하나씩만 밀었다. 원본은
        난이도만큼 병렬로 밀고(impossible 은 사실상 전부), **건물을 가진 봇을
        먼저** 고른다 — 훔쳐 간 건물을 되찾는 것이 급하기 때문이다. 그다음이
        밀도(병력/타일)가 낮은 순이다.

        `_bot_troops_sent` 가 실제로 늘어야 True 다. 원본 주석대로 — 한 대도 못
        보냈으면 사다리를 계속 내려가야 한다."""
        bots = [q for q in enemies if q.kind == "bot"]
        if not bots:
            return False
        self._bot_troops_sent = 0.0
        bots.sort(key=lambda q: (
            not any(u.utype in STRUCTURES for u in q.units.units),
            q.troops / max(1, st.tiles(q.pid))))
        limit = BOT_PARALLELISM.get(self.difficulty)
        if limit is None:                     # medium 은 1/2 로 1 또는 2
            limit = 1 if self.rng.randrange(2) == 0 else 2
        for bot in bots[:limit]:
            self._send_attack(st, bot.pid)
        return self._bot_troops_sent > 0

    def _island_boat(self, st: GameState) -> bool:
        """`findNearestIslandEnemy` — 국경에 적이 없을 때 바다 건너를 노린다.

        원본은 두 후보까지 모아 두고 **1/3 확률로 두 번째**를 고른다. 하나만
        고르면 모든 나라가 같은 이웃 섬으로 몰린다."""
        me = st.players[self.pid]
        if sum(1 for b in st.boats if b.owner == self.pid) >= C.BOAT_MAX_NUMBER:
            return False
        shore = shoreline_tiles(st.gmap, self.pid)
        if not len(shore):
            return False
        cands = []
        for q in st.alive:
            if q.pid == self.pid or st.diplomacy.is_friendly(self.pid, q.pid):
                continue
            if self._is_ffa(st) and q.troops >= me.troops:
                continue
            cands.append(q)
        if not cands:
            return False
        cands.sort(key=lambda q: self._center_distance(st, q))
        pick = cands[0]
        if len(cands) >= 2 and self.rng.randrange(ISLAND_SECOND_CHANCE) == 0:
            pick = cands[1]
        return self._send_attack(st, pick.pid)

    def _center_distance(self, st: GameState, other) -> int:
        gm = st.gmap
        ax, ay = gm.xy(st.players[self.pid].start)
        bx, by = gm.xy(other.start)
        return abs(ax - bx) + abs(ay - by)

    def _maybe_betray_and_attack(self, st: GameState, friends: list,
                                 enemies: list) -> bool:
        """`maybeBetrayAndAttack` → `maybeBetray`.

        ⚠ **이식 누락 서른하나.** 배신의 *대가*는 정성껏 옮겨 놓고(배신자는 동맹
        요청의 90% 를 거절당하고 방어와 속도가 깎인다) 나라 AI 가 배신하는 *행동*
        자체가 없었다. 그래서 그 상태에 들어가는 나라가 한 명도 없었다 — 봇이
        배신자를 칠 때(`tribe.py`)와 사람이 직접 깰 때만 쓰이던 규칙이다.

        원본은 네 가지 이유로 깬다. **난이도가 문턱이 아니라 이유의 개수로
        들어간다** — easy 는 아래 둘째 하나만 본다:

        1. (medium 초과) **거의 죽은 동맹**을 친다 — 병력 + 진행 중인 공격이
           상한의 20% 미만이고 나보다 약하면. 원본 주석: *"For example MIRVed ones"*
        2. (easy·medium) 내 병력이 **열 배** 이상이면. 원본 주석이 이 조건이
           엉성하다는 것을 인정한다 — *"isn't really smart. It makes nations
           vulnerable, but that's intended."* easy 는 **사람은 안 친다.**
        3. (easy 제외) 상대가 **배신자**이고 나보다 1.2배 이상 강하지 않으면
        4. (easy 제외) 이웃이 **그 하나뿐**이고 내가 세 배 이상 강하면
        """
        # ⚠ 이 조기 탈출은 **변이로 안 잡힌다. 정상이다** — 지워도 아래 루프가
        # 빈 목록을 돌 뿐이라 관찰 가능한 차이가 없다(원본에도 같은 검사가 있고
        # 같은 이유로 무동작이다). 성능용이니 파지 말 것.
        if not friends:
            return False
        bordering = len(friends) + len(enemies)
        me = st.players[self.pid]
        for friend in friends:
            if not self._betrays(st, me, friend, bordering):
                continue
            if st.break_alliance(self.pid, friend.pid):
                if self._send_attack(st, friend.pid):
                    return True
        return False

    def _betrays(self, st: GameState, me, other, bordering: int) -> bool:
        """`maybeBetray` 의 판정만. 깨는 것은 부르는 쪽이 한다."""
        easy_side = self.difficulty in ("easy", "medium")
        if not easy_side:
            outgoing = sum(a.troops for a in st.attacks if a.attacker == other.pid)
            cap = other.max_troops(max(1, st.tiles(other.pid)))
            if (other.troops + outgoing < cap * C.BETRAY_WEAK_TROOP_RATIO
                    and other.troops < me.troops):
                return True
        if easy_side:
            # easy 는 사람을 봐준다 — §5.27 의 `shouldAttack` 과 같은 성격이다
            if not (self.difficulty == "easy" and other.kind == "human"):
                if me.troops >= other.troops * C.BETRAY_STRONGER_MULTIPLE:
                    return True
        if self.difficulty != "easy":
            if (st.is_traitor(other.pid)
                    and other.troops < me.troops * C.BETRAY_TRAITOR_MARGIN):
                return True
            if (bordering == 1
                    and other.troops * C.BETRAY_LONE_NEIGHBOUR_MULTIPLE < me.troops):
                return True
        return False

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

    def _send_attack(self, st: GameState, target: int | None,
                     force: bool = False) -> bool:
        """`sendAttack(target, force = false)`.

        ⚠ `force` 는 **`shouldAttack` 만 건너뛴다.** 병력 계산·상한은 그대로
        거친다 — 반격이라고 무제한으로 쏟아붓는 것이 아니다."""
        if not force and not self._should_attack(st, target):
            return False
        # ⚠ **`sendAttack` 은 육상과 상륙으로 갈린다**(`sharesBorderWith` 로).
        # 우리는 육상만 옮겨 놓아서, 국경을 안 맞댄 상대를 고르는 전략들
        # (`hated` · `island`)이 **아무 일도 못 하고 False 만 돌려주고 있었다.**
        if target is not None and target not in st.border_targets(self.pid):
            return self._boat_attack(st, target)
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
        # ⚠ `trigger_ratio` 관문은 **여기 있으면 안 된다.** 원본은
        # `attackBestTarget` 초입에서 한 번 보고, 통과하면 표적별 계산은 비율을
        # 다시 안 본다. 여기 두면 사다리의 모든 전략이 매번 같은 검사를 다시
        # 받으면서도 "봇 먼저 치기"(비율 검사보다 앞서는 자리)까지 막힌다.
        foe = st.players.get(target) if target is not None else None
        bot_with_structures = (
            foe is not None and foe.kind == "bot"
            and any(u.utype in STRUCTURES for u in foe.units.units))
        ratio = self.expand_ratio if (foe is None or bot_with_structures) \
            else self.reserve_ratio
        keep = cap * ratio
        troops = p.troops - keep
        # ⚠ **봇에게는 "남은 전부"를 쏟지 않는다**(`calculateBotAttackTroops`).
        # 상대 병력의 **네 배**만 보내고, 그마저 여유를 넘으면 — 남은 것이
        # 상대의 두 배도 안 되면 — **아예 안 친다.**
        #
        # 이게 없으면 봇 하나에 가진 병력을 전부 털어 넣어 **병렬 공격이 성립하지
        # 않는다**(둘째 봇부터 보낼 병력이 없다). 봇이 400개인 판에서 이 차이는
        # "한 tick 에 봇 셋"과 "봇 하나"의 차이다.
        #
        # ⚠ 원본의 `- botAttackTroopsSent` 는 **여기서 빼지 않는다.** 원본은
        # 공격을 Execution 으로 예약만 하고 병력은 다음 tick 에 빠지므로 루프
        # 안에서 `troops()` 가 안 줄어 그 누적치가 필요하다. 우리 `launch_attack`
        # 은 **그 자리에서** 병력을 깎으므로 빼면 두 번 빠진다.
        if foe is not None and foe.is_bot and not p.is_bot:
            troops = self._bot_attack_troops(foe, troops)

        if foe is not None:
            troops = min(troops, self._send_cap(st))
        if troops < C.ATTACK_MIN_TROOPS:
            return None
        # hard 이상은 상대 병력의 20% 미만으로는 안 친다 — 병력만 버리는 짓이다.
        # ⚠ **맞고 있는 중이면 면제다**(원본 주석: *"Nations under attack may
        # retaliate freely"*). 없으면 강한 상대에게 얻어맞는 나라가 반격조차 못 한다.
        if (foe is not None and self.difficulty in RETAIN_FRACTION
                and not self._under_attack(st)
                and troops < foe.troops * MIN_ATTACK_RATIO):
            return None
        if foe is not None and foe.is_bot and not p.is_bot:
            self._bot_troops_sent += troops
        return troops

    def _bot_attack_troops(self, foe, max_troops: float) -> float:
        """`calculateBotAttackTroops` — easy 만 있는 대로 쏟는다."""
        if self.difficulty == "easy":
            return max_troops
        troops = foe.troops * BOT_ATTACK_MULTIPLE
        if troops > max_troops:
            if max_troops < foe.troops * BOT_ATTACK_MIN_MULTIPLE:
                return 0.0
            troops = max_troops
        return troops

    def _under_attack(self, st: GameState) -> bool:
        return any(a.target == self.pid for a in st.attacks)

    def _boat_attack(self, st: GameState, target: int) -> bool:
        """`sendBoatAttack` — 국경이 없으면 **상대 해안에 배를 붙인다.**

        원본은 `closestTwoTiles(내 해안, 상대 해안)` 으로 가장 가까운 쌍을 고른다.
        우리도 같은 것을 하되, 뒤 계산(어느 항구에서 뜰지 · 물길이 있는지)은
        `send_boat` 이 이미 한다 — 물길이 없으면 거기서 None 이 돌아온다."""
        gm = st.gmap
        mine = shoreline_tiles(gm, self.pid)
        theirs = shoreline_tiles(gm, target)
        if not len(mine) or not len(theirs):
            return False
        mx, my = gm.xy(int(mine[0]))
        best, best_d = None, None
        for t in theirs.tolist():
            tx, ty = gm.xy(int(t))
            d = abs(tx - mx) + abs(ty - my)
            if best_d is None or d < best_d:
                best, best_d = int(t), d
        if best is None:
            return False
        return st.send_boat(self.pid, best, target) is not None

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
            other = st.players[o]
            # ⚠ **봇과 친한 쪽은 위협이 아니다**(원본이 둘 다 거른다). 안 거르면
            # 봇 이웃 하나 때문에 상한이 바닥나 나라가 아무도 못 친다 — 판에
            # 봇이 400개라 사실상 항상 걸린다.
            if other.is_bot or st.diplomacy.is_friendly(self.pid, o):
                continue
            strongest = max(strongest, other.troops)
        cap = float("inf") if strongest == 0.0 else max(0.0, p.troops - strongest * frac)
        # 맞고 있으면 **들어오는 병력만큼은** 무조건 쓸 수 있다.
        incoming = sum(a.troops for a in st.attacks if a.target == self.pid)
        return max(cap, incoming) if incoming > 0 else cap

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
        """`getAllianceDecision(other, isResponse=true)` — `ai/alliance.py` 로 옮겼다.

        ⚠ 전에는 관문 셋짜리 축소판이었다(배신자 90% · 관계 · **동전 던지기**).
        그 동전 던지기 자리에 원본은 판단 넷을 갖고 있다 — 위협이면 오히려 받고,
        동맹이 너무 많은 상대는 거절하고, 초반이면 그냥 받고, 마지막엔 비슷하게
        강한지를 본다(§5.53)."""
        return self.alliance.decide(st, requestor, is_response=True)

    def _alliance_extensions(self, st: GameState) -> None:
        """`handleAllianceExtensionRequests` — **사람이 연장을 요청한 동맹**에만
        답한다.

        ⚠ 이식 누락 서른셋의 나머지 절반. `Alliance.request_extension` 과
        `both_agreed_to_extend` 는 `diplomacy.py` 에 **있었는데 아무도 안 불렀다.**
        사람 쪽 버튼도, AI 쪽 동의도 없어서 모든 동맹이 예외 없이 만료됐다."""
        for al in st.diplomacy.alliances:
            if not al.involves(self.pid):
                continue
            # 한쪽만 동의한 상태 = 상대가 요청해 둔 상태다. 둘 다면 이미 끝났고,
            # 아무도 안 했으면 답할 것이 없다.
            if al.both_agreed_to_extend:
                continue
            other = al.other(self.pid)
            mine = al._extend_a if self.pid == al.a else al._extend_b
            theirs = al._extend_b if self.pid == al.a else al._extend_a
            if mine or not theirs:
                continue
            if self.alliance.decide(st, other, is_response=True):
                # ⚠ 엔진 경유다(§5.65) — 여기서 직접 부르면 **즉시 갱신**과 양쪽에
                # 가는 성사 소식을 건너뛴다. 사람은 AI 가 동의한 줄 모르게 된다.
                st.extend_alliance(self.pid, other)

    # --- 전함 -------------------------------------------------------------

    def _track_trade_ships(self, st: GameState) -> None:
        """`trackTradeShipsAndRetaliate` — 내 무역선이 **나포당하면** 보복한다.

        원본은 배가 목록에서 사라졌는지가 아니라 **주인이 바뀌었는지**를 본다.
        격침·도착과 나포를 구분해야 하기 때문이다 — 도착에 보복하면 안 된다."""
        alive = {id(t): t for t in st.trade_ships}
        for key in list(self._tracked_trade):
            t = alive.get(key)
            if t is None:
                self._tracked_trade.discard(key)
                continue
            if t.captured_by is not None and t.captured_by != self.pid:
                self._tracked_trade.discard(key)
                self._retaliate(st, t.tile, t.captured_by,
                                C.REL_WARSHIP_SANK_TRADE)
        for t in st.trade_ships:
            if t.owner == self.pid and t.captured_by is None:
                self._tracked_trade.add(id(t))

    def _intercept_incoming(self, st: GameState) -> None:
        """`trackIncomingTransportsAndRetaliate` — 내 땅을 노리는 상륙선을 **미리** 친다.

        보복(`_retaliate`)이 당한 **뒤**라면 이쪽은 당하기 **전**이다. 관문 셋이
        이 기능을 쓸모 있게 만든다:

        | 관문 | 왜 |
        |---|---|
        | 목표까지 20 이상 남았다 | 코앞이면 배를 띄워도 상륙이 먼저 끝난다 |
        | 목표 90 안에 내 전함(또는 그 순찰 기점)이 없다 | 이미 덮은 자리에 또 띄우면 낭비다 |
        | 상대가 동맹이 아니다 | |

        ⚠ **한 척에 한 번만** 대응한다(`dealtWithTransportShip`). 안 그러면
        같은 배가 다가오는 내내 매 tick 전함을 뽑아 §5.40 의 낭비가 되돌아온다.
        그리고 한 tick 에 **한 척만** 처리한다(원본의 `break`).
        """
        p = st.players.get(self.pid)
        if p is None or not p.alive:
            return
        gmap = st.gmap
        w2 = gmap.width

        def man(a, b):
            return abs(a % w2 - b % w2) + abs(a // w2 - b // w2)

        live = {id(b) for b in st.boats}
        self._dealt_boats &= live          # 사라진 배는 잊는다
        mine = [x for x in st.warships if x.owner == self.pid and not x.sunk]

        for b in st.boats:
            # ⚠ `b.owner == self.pid` 는 **관찰 가능한 차이가 없다**(원본도 같다):
            # 아래 `is_friendly(pid, pid)` 가 항상 참이라 어차피 걸러진다.
            # 남겨 두는 이유는 내 배마다 거리·소유 검사를 도는 것을 아끼기
            # 위해서다(원본의 `smallID() !== this.player.smallID()` 와 같다).
            if b.owner == self.pid or b.retreating:
                continue
            if int(gmap.owner[b.dst]) != self.pid:
                continue                    # 내 땅을 노리는 배가 아니다
            if id(b) in self._dealt_boats:
                continue
            if man(b.tile, b.dst) < C.INCOMING_BOAT_TOO_CLOSE:
                self._dealt_boats.add(id(b))
                continue
            if st.diplomacy.is_friendly(self.pid, b.owner):
                continue
            covered = any(
                man(x.tile, b.dst) < C.INCOMING_BOAT_COVERED_RANGE
                or (x.patrol_origin is not None
                    and man(x.patrol_origin, b.dst) < C.INCOMING_BOAT_COVERED_RANGE)
                for x in mine)
            if covered:
                self._dealt_boats.add(id(b))
                continue
            tile = self._warship_spawn_tile(st, b.dst,
                                            C.INCOMING_BOAT_SPAWN_RADIUS)
            if tile is not None:
                self._retaliate(st, tile, b.owner, C.REL_WARSHIP_SANK_OTHER)
            self._dealt_boats.add(id(b))
            return                          # 원본의 `break` — tick 당 한 척

    def _counter_infestation(self, st: GameState) -> None:
        """`counterWarshipInfestation` — 바다를 전함으로 덮은 상대를 견제한다.

        한 나라가 전함으로 바다를 독점하면 남의 무역선·수송선이 통째로 막힌다.
        그걸 푸는 장치다 — **적의 전함 옆에 내 전함을 띄운다.**

        관문이 다섯이고, 다 있어야 한다:

        | 관문 | 왜 |
        |---|---|
        | hard 이상 | 원본 주석: *"Only the smart nations can do this"* |
        | 판 전체 전함 > 10 | 바다가 실제로 붐벼야 독점이라 할 수 있다 |
        | 내 전함 < 10 | 견제한다고 내가 독점하면 안 된다 |
        | 항구가 있다 · 골드가 된다 | |
        | **내가 부자 상위 3** | 원본 주석: *"We don't want poor nations to use their precious gold on this"* |

        ⚠ 마지막이 핵심이다. 이게 없으면 가난한 나라가 마지막 골드를 여기 쓰고
        아무것도 못 짓는다 — §5.40 에서 잡은 것과 같은 종류의 낭비다.
        """
        if self.difficulty not in ("hard", "impossible"):
            return
        p = st.players.get(self.pid)
        if p is None or not p.alive:
            return
        alive_ships = [w for w in st.warships if not w.sunk]
        # ⚠ 이 줄은 **관찰 가능한 차이가 없는 이른 탈출**이다(원본도 같다):
        # 아래에서 적 하나가 10척을 넘어야 표적이 되므로, 판 전체가 10 이하면
        # 애초에 표적이 나올 수 없다. 원본이 둔 이유는 성능이다 —
        # 붐비지 않는 판에서 매 tick 전 함대를 세지 않으려는 것. 파지 말 것.
        if len(alive_ships) <= C.WARSHIP_INFESTATION_GAME_MIN:
            return
        mine = [w for w in alive_ships if w.owner == self.pid]
        if len(mine) >= C.WARSHIP_RETALIATION_CAP:
            return
        if not p.units.of(UnitType.PORT):
            return
        if p.gold < p.units.cost(UnitType.WARSHIP):
            return
        if not self._is_rich(st):
            return

        counts: dict[int, list] = {}
        for w in alive_ships:
            if w.owner == self.pid or st.diplomacy.is_friendly(self.pid, w.owner):
                continue
            counts.setdefault(w.owner, []).append(w)
        for owner, ships in counts.items():
            if len(ships) > C.WARSHIP_INFESTATION_ENEMY_MIN:
                tile = self.rng.choice(ships).tile
                # 적 전함 **옆에** 띄운다. 못 지으면 있던 배를 그리로 보낸다.
                if st.build_warship(self.pid, tile) is None:
                    self._move_warship(st, mine, tile)
                return

    def _is_rich(self, st: GameState) -> bool:
        """`isRichPlayer` — 골드 상위 3 안에 드는가. **사람은 세지 않는다.**"""
        golds = sorted((q.gold for q in st.alive if q.kind != "human"),
                       reverse=True)
        if not golds:
            return False
        cut = golds[min(C.WARSHIP_INFESTATION_RICH_TOP, len(golds)) - 1]
        p = st.players.get(self.pid)
        return p is not None and p.gold >= cut

    def _track_transport_ships(self, st: GameState) -> None:
        """`trackTransportShipsAndRetaliate` — 내 수송선이 **격침당하면** 보복한다.

        ⚠ 도착·퇴각과 구분해야 한다. 목록에서 빠졌다는 것만으로는 셋이 구별되지
        않으므로 배에 남긴 `sunk_by` 를 본다. 참조를 들고 있어야 목록에서 빠진
        뒤에도 볼 수 있다 — 원본이 `Set` 에 담아 두는 것과 같은 이유다.

        무역선보다 관계가 더 크게 깎인다(−15 대 −7.5). 병력을 실은 배라서다."""
        keep = []
        for b in self._tracked_boats:
            if b.active:
                keep.append(b)
                continue
            if b.sunk_by is not None:
                self._retaliate(st, b.tile, b.sunk_by, C.REL_WARSHIP_SANK_OTHER)
        self._tracked_boats = keep
        for b in st.boats:
            if b.owner == self.pid and b not in self._tracked_boats:
                self._tracked_boats.append(b)

    def _retaliate(self, st: GameState, tile: TileRef, enemy: int,
                   rel_hit: float) -> None:
        """`maybeRetaliateWithWarship` — 당한 자리로 전함을 낸다.

        ⚠ **상한 10척.** 넘으면 새로 짓지 않고 있던 배의 순찰 기점을 그리로 옮긴다
        (`maybeMoveWarship`). 이게 원본 해군이 커지지 않는 두 번째 장치다.
        확률은 난이도를 탄다 — easy 는 아예 보복하지 않는다."""
        if enemy == self.pid:
            return
        p = st.players.get(self.pid)
        if p is None or not p.alive:
            return
        mine = [w for w in st.warships if w.owner == self.pid and not w.sunk]
        if len(mine) >= C.WARSHIP_RETALIATION_CAP:
            self._move_warship(st, mine, tile)
            return
        chance = C.WARSHIP_RETALIATION_CHANCE[self.difficulty]
        if self.rng.randrange(100) >= chance:
            return
        if st.build_warship(self.pid, tile) is None:
            self._move_warship(st, mine, tile)
            return
        # ⚠ `relate` 는 **한 방향**이다 — 당한 쪽만 나빠진다.
        st.relate(self.pid, enemy, rel_hit)

    def _move_warship(self, st: GameState, mine, tile: TileRef) -> None:
        """`maybeMoveWarship` — 순찰 기점을 옮긴다.

        ⚠ **이미 이동 중인 배는 안 부른다**(기점에서 130 넘게 떨어진 배).
        부르면 가던 길을 버리고 되돌아와 아무 데도 못 간다."""
        if st.gmap.terrain[tile] != Terrain.OCEAN:
            return
        w2 = st.gmap.width
        idle = [w for w in mine
                if w.patrol_origin is not None
                and (abs(w.tile % w2 - w.patrol_origin % w2)
                     + abs(w.tile // w2 - w.patrol_origin // w2))
                < C.WARSHIP_REASSIGN_RANGE]
        if not idle:
            return
        best = min(idle, key=lambda w: (abs(w.tile % w2 - tile % w2)
                                        + abs(w.tile // w2 - tile // w2)))
        best.patrol_origin = tile
        best.patrol_target = None


    def _maybe_spawn_warship(self, st: GameState, p) -> bool:
        """`maybeSpawnWarship` — **한 척도 없을 때만** 짓는다.

        ⚠ 이식 누락 스물다섯. 전에는 골드가 되면 무조건 지었다. 실측에서 판 전체
        지출의 **85%**(535,000,000 / 2,140척)가 전함으로 갔고, 그래서 아무도
        사일로(1,000,000)를 못 샀다. 원본은 두 겹으로 막는다:

          1. 판단 tick 마다 **50% 확률**
          2. **전함이 한 척도 없을 때만** 새로 짓는다

        그 뒤로 전함이 느는 길은 보복(`_retaliate`)뿐이고 그것도 10척까지다.
        원본의 해군이 작은 이유가 여기 있다 — 전함은 상비군이 아니라 **대응 수단**이다.
        """
        if self.rng.randrange(100) >= C.WARSHIP_SPAWN_CHANCE:
            return False
        ports = p.units.of(UnitType.PORT)
        if not ports:
            return False
        if any(not w.sunk and w.owner == self.pid for w in st.warships):
            return False
        if p.gold <= p.units.cost(UnitType.WARSHIP):
            return False
        tile = self._warship_spawn_tile(st, self.rng.choice(ports).tile,
                                        C.WARSHIP_SPAWN_RADIUS)
        if tile is None:
            return False
        return st.build_warship(self.pid, tile) is not None

    def _warship_spawn_tile(self, st: GameState, near: TileRef,
                            radius: int) -> "TileRef | None":
        """`warshipSpawnTile` — 항구 반경 안 아무 바다 칸. 50번 던져 본다.

        ⚠ 항구 **옆**이 아니다. 반경 250 이면 배가 처음부터 흩어져 뜨고, 그 자리가
        곧 순찰 기점이 된다(§5.37). 항구 옆에 몰아 두면 순찰 구역이 겹친다."""
        gmap = st.gmap
        cx, cy = near % gmap.width, near // gmap.width
        for _ in range(50):
            x = self.rng.randint(cx - radius, cx + radius)
            y = self.rng.randint(cy - radius, cy + radius)
            if not (0 <= x < gmap.width and 0 <= y < gmap.height):
                continue
            tile = gmap.ref(x, y)
            if gmap.terrain[tile] != Terrain.OCEAN:
                continue
            return tile
        return None

    # --- 건설 -------------------------------------------------------------

    def _structures(self, st: GameState) -> None:
        """건물은 `NationStructureBehavior` 가, 전함·핵은 여기가 맡는다.

        원본도 `NationStructureBehavior` · `NationWarshipBehavior` ·
        `NationNukeBehavior` 로 나뉘어 있다. 건물을 짓거나 올렸으면 이번 판단은
        거기서 끝이다 — 골드를 이미 썼으므로 전함·핵까지 이어 가면 안 된다."""
        p = st.players[self.pid]
        if not len(st.gmap.owned_refs(self.pid)):
            return
        # ⚠ **MIRV 가 건물보다 먼저다**(원본 `NationExecution` 의 호출 순서).
        # 건물에 골드를 써 버린 뒤에 보면 MIRV 는 영원히 못 산다 — 그래서
        # `getSaveUpTarget` 이 MIRV 값을 목표로 잡아도 아무 일이 안 일어났다.
        if self.mirv.consider(st):
            return
        if self.structures.handle(st):
            return
        if self._maybe_spawn_warship(st, p):
            return
        # 핵은 `NationNukeBehavior` 가 맡는다(§5.44). 전에는 여기 열 줄짜리
        # 축소판이 있었다 — 영토가 가장 큰 적의 **아무 칸에나** 쐈다.
        self.nukes.maybe_send(st, self._should_attack)


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
