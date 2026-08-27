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

        # 배신은 **중립 확장 뒤, 최선 표적 앞**이다(원본 `AiAttackBehavior` 의
        # 판단 사슬 순서). 앞으로 당기면 빈 땅이 남았는데도 동맹을 깨고, 뒤로
        # 미루면 배신할 만한 상대를 그냥 이웃으로 두고 지나간다.
        friends = [q for q in others
                   if st.diplomacy.allied(self.pid, q.pid)]
        if self._maybe_betray_and_attack(st, friends, enemies):
            return

        self._attack_best(st, enemies)

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
