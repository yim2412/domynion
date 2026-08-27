"""동맹 판단 — 원본 `NationAllianceBehavior.getAllianceDecision`.

⚠ **이식 누락 서른셋.** 우리 것은 관문 셋짜리 축소판이었다:

    배신자면 90% 거절 → 관계가 중립 미만이면 거절 → 우호면 2/3, 아니면 **동전 던지기**

원본은 관문이 **여덟**이고, 그 동전 던지기 자리에 실제 판단 넷이 들어 있다:

1. **혼란**(easy 10% · medium 5% · hard 2.5% · impossible 0%) — 낮은 난이도는
   가끔 아무렇게나 답한다. 원본 주석: *"Just like dumb humans do"*
2. **배신자 90% 거절** (우리에게 있던 것)
3. **동맹을 너무 많이 가진 상대는 거절**(hard 이상). 원본 주석이 이유를 적어
   뒀다 — *"to make sure there are enough non-friendly players in the game to
   stop the crown with nukes"*. 즉 **핵 균형을 위한 장치**다.
4. **상대가 위협이면 오히려 받는다.** 두려워서 손을 잡는 것이다. easy 는 아무도
   위협으로 안 보고, impossible 은 세 가지 지표로 본다.
5. 관계가 중립 미만이면 거절 (우리에게 있던 것)
6. **우호면 받는다** — 난이도가 높을수록 덜 받는다(hard 17% · impossible 33% 거절)
7. **이미 동맹이 충분하면 거절.** hard 이상은 *"이웃 전부와 동맹하지 않는다"* 는
   별도 규칙이 앞선다
8. **초반이면 받는다** — 난이도별로 창과 확률이 다르다
9. 마지막은 **비슷하게 강한가** — 병력(나가 있는 것 포함)이나 타일로 본다

난이도가 문턱이 아니라 **어느 관문을 보는가와 얼마나 까다로운가**로 들어가는 것이
이 파일의 성격이다(§5.51 의 배신과 같다).
"""

from __future__ import annotations

import random

from ..core import emoji
from ..core.relations import Relation

# 혼란 확률의 역수. `chance(n)` = 1/n. impossible 은 혼란이 없다.
CONFUSED_ODDS = {"easy": 10, "medium": 20, "hard": 40, "impossible": 0}

# 상대가 가진 동맹이 (봇을 뺀) 전체 인원의 이 비율 이상이면 거절한다(hard 이상).
TOO_MANY_ALLIANCES_SHARE = {"hard": 0.5, "impossible": 0.25}

# 초반이라 그냥 받아 주는 창(tick)과 **거절 확률**(%).
EARLYGAME = {
    "easy": (3000, 10),
    "medium": (1800, 30),
    "hard": (1800, 50),
    "impossible": (600, 70),
}

# 우호일 때 그래도 거절하는 확률(%). 어려울수록 까다롭다.
FRIENDLY_REJECT_PCT = {"easy": 0, "medium": 0, "hard": 17, "impossible": 33}

# "동맹이 이미 충분하다"의 문턱 — `randint(a, b)` 범위다.
ENOUGH_ALLIANCES = {"medium": (4, 6), "hard": (3, 5), "impossible": (2, 4)}

# 비슷하게 강한가 — 병력·타일 문턱을 이 백분율 범위에서 뽑는다.
SIMILAR_TROOP_PCT = {"easy": (60, 70), "medium": (70, 80),
                     "hard": (75, 85), "impossible": (80, 90)}
SIMILAR_TILE_PCT = {"easy": (70, 80), "medium": (80, 90),
                    "hard": (85, 95), "impossible": (90, 100)}

# 타일로 비슷하다고 볼 때도 병력이 내 절반은 돼야 한다.
SIMILAR_TILE_TROOP_FLOOR = 0.5

# 위협 판정의 배수들.
THREAT_MEDIUM_TROOPS = 2.5
THREAT_HARD_MAX_TROOPS = 2.0
THREAT_IMPOSSIBLE_TROOPS = 1.5
THREAT_IMPOSSIBLE_MAX_TROOPS = 1.5
THREAT_IMPOSSIBLE_TILES = 1.5


class NationAllianceBehavior:
    """한 나라의 동맹 판단. `NationBot` 이 하나씩 들고 있다."""

    __slots__ = ("pid", "rng", "difficulty")

    def __init__(self, pid: int, rng: random.Random, difficulty: str):
        self.pid, self.rng, self.difficulty = pid, rng, difficulty

    # --- 진입점 -----------------------------------------------------------

    def decide(self, st, other_pid: int, is_response: bool) -> bool:
        """`getAllianceDecision` — 받을 것인가(또는 걸 것인가).

        `is_response` 는 **내가 답하는 쪽인가**다. 원본은 이모지를 보낼지 말지에만
        쓰지만, 그것도 규칙이다 — 거절할 때 아무 말이 없으면 사람은 왜 거절당했는지
        모른다(§5.25)."""
        me = st.players.get(self.pid)
        other = st.players.get(other_pid)
        if me is None or other is None:
            return False

        # 1) 혼란 — 낮은 난이도는 가끔 아무렇게나 답한다
        if self._confused():
            return self.rng.randrange(2) == 0

        # 2) 배신자는 거의 항상 거절
        if st.is_traitor(other_pid) and self.rng.randrange(100) >= 10:
            if is_response and self.rng.randrange(3) == 0:
                st.ai_emoji(self.pid, other_pid, emoji.CONFUSED)
            return False

        # 3) 동맹을 너무 많이 가진 상대 (hard 이상)
        if self._has_too_many_alliances(st, other_pid):
            return False

        # 4) **위협이면 오히려 받는다** — 두려워서 손을 잡는다
        if self._is_threat(st, me, other):
            if not is_response and self.rng.randrange(6) == 0:
                st.ai_emoji(self.pid, other_pid, emoji.SCARED_OF_THREAT)
            if is_response and self.rng.randrange(6) == 0:
                st.ai_emoji(self.pid, other_pid, emoji.LOVE)
            return True

        # 5) 사이가 나쁘면 거절
        if st.relation_of(self.pid, other_pid) < Relation.NEUTRAL:
            if is_response and self.rng.randrange(3) == 0:
                st.ai_emoji(self.pid, other_pid, emoji.CONFUSED)
            return False

        # 6) 우호면 받는다 (어려울수록 덜)
        if self._is_friendly_enough(st, other_pid):
            if self.rng.randrange(3) == 0:
                st.ai_emoji(self.pid, other_pid, emoji.HANDSHAKE)
            return True

        # 7) 이미 동맹이 충분하면 거절
        if self._enough_alliances(st, other_pid):
            return False

        # 8) 초반이면 받는다
        if self._earlygame(st):
            return True

        # 9) 마지막 — 비슷하게 강한가
        return self._similarly_strong(st, me, other)

    # --- 관문들 -----------------------------------------------------------

    def _confused(self) -> bool:
        """원본 주석: *"Easy (dumb) nations sometimes get confused and
        accept/reject randomly (Just like dumb humans do)"*."""
        odds = CONFUSED_ODDS[self.difficulty]
        return odds > 0 and self.rng.randrange(odds) == 0

    def _has_too_many_alliances(self, st, other_pid: int) -> bool:
        """**핵 균형을 위한 장치다.** 원본 주석: *"to make sure there are enough
        non-friendly players in the game to stop the crown with nukes"*.

        동맹이 온 지도로 번지면 아무도 왕관을 못 친다."""
        share = TOO_MANY_ALLIANCES_SHARE.get(self.difficulty)
        if share is None:
            return False
        total = sum(1 for q in st.alive if not q.is_bot)
        theirs = len(st.diplomacy.allies_of(other_pid))
        return theirs >= total * share

    def _is_threat(self, st, me, other) -> bool:
        """**위협이면 받는다**(거절이 아니다). 두려워서 손을 잡는 것이다.

        easy 는 아무도 위협으로 안 본다 — 원본 주석: *"we are very dumb"*.
        impossible 은 병력·상한·타일 셋 중 하나만 걸려도 위협으로 본다."""
        d = self.difficulty
        if d == "easy":
            return False
        if d == "medium":
            return other.troops > me.troops * THREAT_MEDIUM_TROOPS
        my_cap = me.max_troops(max(1, st.tiles(self.pid)))
        their_cap = other.max_troops(max(1, st.tiles(other.pid)))
        if d == "hard":
            return (other.troops > me.troops
                    and their_cap > my_cap * THREAT_HARD_MAX_TROOPS)
        more_troops = other.troops > me.troops * THREAT_IMPOSSIBLE_TROOPS
        more_cap = (other.troops > me.troops
                    and their_cap > my_cap * THREAT_IMPOSSIBLE_MAX_TROOPS)
        more_tiles = (other.troops > me.troops
                      and st.tiles(other.pid)
                      > st.tiles(self.pid) * THREAT_IMPOSSIBLE_TILES)
        return more_troops or more_cap or more_tiles

    def _is_friendly_enough(self, st, other_pid: int) -> bool:
        """⚠ 원본은 **`=== Relation.Friendly`** 다(이상이 아니다). 우리 관계
        등급에는 우호 위가 없으므로 `>=` 와 같은 뜻이지만, 등급이 늘면 갈린다."""
        if st.relation_of(self.pid, other_pid) < Relation.FRIENDLY:
            return False
        reject = FRIENDLY_REJECT_PCT[self.difficulty]
        return self.rng.randrange(100) >= reject

    def _enough_alliances(self, st, other_pid: int) -> bool:
        """easy 는 이 관문이 없다. hard 이상은 **이웃 전부와 동맹하지 않는다**는
        규칙이 앞선다 — 이웃이 둘 이상이면 하나는 적으로 남긴다."""
        d = self.difficulty
        if d == "easy":
            return False
        mine = len(st.diplomacy.allies_of(self.pid))
        if d == "medium":
            lo, hi = ENOUGH_ALLIANCES["medium"]
            return mine >= self.rng.randint(lo, hi)

        nearby = [pid for pid in st.border_targets(self.pid)
                  if pid is not None and pid in st.players
                  and not st.players[pid].is_bot]
        if len(nearby) >= 2 and other_pid in nearby:
            friends = sum(1 for pid in nearby
                          if st.diplomacy.is_friendly(self.pid, pid))
            return len(nearby) <= friends + 1
        lo, hi = ENOUGH_ALLIANCES[d]
        return mine >= self.rng.randint(lo, hi)

    def _earlygame(self, st) -> bool:
        """초반에는 그냥 받아 준다. **창과 확률이 난이도마다 다르다** —
        easy 는 5분 동안 90%, impossible 은 1분 동안 30%."""
        window, reject = EARLYGAME[self.difficulty]
        from ..core import constants as C
        if st.tick_count >= window + C.SPAWN_PHASE_TURNS:
            return False
        return self.rng.randrange(100) >= reject

    def _similarly_strong(self, st, me, other) -> bool:
        """마지막 관문 — **나가 있는 병력까지 더해서** 견준다.

        문턱을 난이도별 범위에서 **무작위로 뽑는다**(원본 주석이 float 대신 int 를
        쓰는 이유까지 적어 뒀다 — 부동소수는 desync 를 낼 수 있다).

        타일로 비슷하다고 볼 때도 병력이 내 절반은 돼야 한다 — 땅만 넓고 병력이
        없는 상대와 손잡아 봐야 도움이 안 된다."""
        troop_lo, troop_hi = SIMILAR_TROOP_PCT[self.difficulty]
        tile_lo, tile_hi = SIMILAR_TILE_PCT[self.difficulty]
        my_out = sum(a.troops for a in st.attacks if a.attacker == self.pid)
        their_out = sum(a.troops for a in st.attacks if a.attacker == other.pid)
        mine = me.troops + my_out
        theirs = other.troops + their_out
        troop_gate = mine * (self.rng.randint(troop_lo, troop_hi) / 100)
        tile_gate = st.tiles(self.pid) * (self.rng.randint(tile_lo, tile_hi) / 100)
        if theirs > troop_gate:
            return True
        return (st.tiles(other.pid) > tile_gate
                and theirs > mine * SIMILAR_TILE_TROOP_FLOOR)
