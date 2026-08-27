"""AI 가 먼저 거는 잡담 — 원본 `NationEmojiBehavior.maybeSendCasualEmoji`.

⚠ **이식 누락 서른둘.** 우리 AI 는 **사건에 대한 대답**만 했다(공격할 때 · 동맹
요청을 받았을 때 · 표적 부탁을 거절할 때). 원본은 그 위에 **먼저 거는 말** 여덟
가지가 있고, 그게 없으면 AI 는 내가 무언가 했을 때만 반응하는 자판기가 된다.

§5.25 가 이미 배운 대로 **이모지는 장식이 아니다** — 관계를 움직이고, 사람이
AI 를 읽는 유일한 창이다. 여덟 가지가 하는 일도 그것이다: 지금 누가 위태로운지,
누가 1등인지, 누가 배신자인지, 내 공격이 우스운 규모인지를 **말로** 알려 준다.

원본의 확률이 곧 성격이다. `chance(n)` = 1/n 이고, 이 함수는 나라마다 매 tick
불린다:

| 언제 | 확률 | 누구에게 |
|---|---|---|
| 들어오는 공격이 내 병력의 3배 이상 | 1/16 | 전체 |
| 사람이 내 병력의 10% 미만으로 찔렀다 | 1/8 | 그 사람 |
| 판이 끝났다 | 1회 | 승자(1등 나라만) |
| 내가 1등이다 | 1/300 | 전체 |
| 사람 동맹이 있다 | 1/250 | 그 동맹 |
| 사람 배신자가 있다 | 1/40 | 그 배신자 |
| 6,000 tick 이후, 땅 1% 미만인 사람 | 1/10000 | 그 사람 |
| 첫 600 tick, 이웃한 사람 | 1/250 | 그 이웃 |

**전체(`AllPlayers`) 방송은 30초 제한을 안 받는다**(`shouldSendEmoji` 가 맨 앞에서
true 를 돌려준다). 개인에게 거는 말만 제한을 받는다.
"""

from __future__ import annotations

import random

from ..core import emoji

# `chance(n)` — 확률의 역수. 원본 상수 그대로다.
CHANCE_OVERWHELMED = 16
CHANCE_SMALL_ATTACK = 8
CHANCE_BRAG = 300
CHANCE_CHARM = 250
CHANCE_ANNOY_TRAITOR = 40
CHANCE_FIND_RAT = 10_000
CHANCE_GREET = 250

# 들어오는 병력이 내 병력의 이 배수 이상이면 "휩쓸리는 중"이다.
OVERWHELMED_RATIO = 3.0

# 사람의 공격이 내 병력의 이 비율 미만이면 우스운 규모다.
SMALL_ATTACK_RATIO = 0.1

# 쥐(🐀)는 **초반 10분을 넘긴 뒤**에만 찾는다 — 원본 주석: 초반엔 다들 작다.
FIND_RAT_AFTER_TICK = 6_000
FIND_RAT_LAND_SHARE = 0.01

# 인사는 첫 1분에만.
GREET_BEFORE_TICK = 600


class NationChatter:
    """한 나라의 잡담. `NationBot` 이 하나씩 들고 있다."""

    __slots__ = ("pid", "rng", "_congratulated")

    def __init__(self, pid: int, rng: random.Random):
        self.pid, self.rng = pid, rng
        self._congratulated = False        # 원본 `gameOver` 플래그와 같은 자리

    def _chance(self, n: int) -> bool:
        return self.rng.randrange(n) == 0

    def tick(self, st) -> None:
        """`maybeSendCasualEmoji` — 여덟 가지를 **원본 순서대로** 본다.

        순서가 뜻을 갖는다: 위태로움과 조롱이 앞이고 자랑·인사가 뒤다. 한 tick 에
        여러 개가 나갈 수 있지만 30초 제한과 쿨다운이 실제로는 걸러 낸다."""
        me = st.players.get(self.pid)
        if me is None or not me.alive:
            return
        self._overwhelmed(st, me)
        self._small_attack(st, me)
        self._congratulate(st, me)
        self._brag(st, me)
        self._charm_allies(st, me)
        self._annoy_traitors(st, me)
        self._find_rat(st, me)
        self._greet(st, me)

    # --- 위태로움 ---------------------------------------------------------

    def _overwhelmed(self, st, me) -> None:
        """들어오는 병력이 내 병력의 3배 이상이면 **전체에 대고** 비명을 지른다.

        방송이라 30초 제한을 안 받는다 — 사람이 "저기가 무너지는 중"임을 아는
        유일한 통로다."""
        if not self._chance(CHANCE_OVERWHELMED):
            return
        incoming = sum(a.troops for a in st.attacks if a.target == self.pid)
        if incoming <= 0:
            return
        if incoming >= me.troops * OVERWHELMED_RATIO:
            self._broadcast(st, emoji.OVERWHELMED)

    def _small_attack(self, st, me) -> None:
        """사람이 내 병력의 10% 도 안 되는 병력으로 찌르면 비웃는다.

        ⚠ **사람의 공격만 본다.** AI 끼리의 작은 공격에는 아무 말도 안 한다.

        이 검사는 **변이로 안 잡힌다. 정상이다** — `ai_emoji` 가 사람이 아닌
        상대를 이미 거절하므로 지워도 결과가 같다. 원본도 양쪽에 다 두므로
        남긴다(주사위 소비가 달라지는 것만이 유일한 차이다)."""
        if not self._chance(CHANCE_SMALL_ATTACK):
            return
        if me.troops <= 0:
            return
        for a in st.attacks:
            if a.target != self.pid:
                continue
            other = st.players.get(a.attacker)
            if other is None or other.kind != "human":
                continue
            if a.troops < me.troops * SMALL_ATTACK_RATIO:
                pool = (emoji.CONFUSED if self._chance(2) else emoji.BORED)
                st.ai_emoji(self.pid, other.pid, pool)

    # --- 판 전체 ----------------------------------------------------------

    def _congratulate(self, st, me) -> None:
        """판이 끝나면 **1등 나라만** 승자에게 축하를 보낸다. 한 번뿐이다.

        전원이 보내면 화면이 축하로 도배된다 — 원본이 1등으로 제한한 이유다."""
        if self._congratulated or not st.over or st.winner is None:
            return
        self._congratulated = True
        nations = [q for q in st.players.values()
                   if q.kind == "nation" and q.alive]
        if not nations:
            return
        largest = max(nations, key=lambda q: st.tiles(q.pid))
        if largest.pid != self.pid:
            return
        st.ai_emoji(self.pid, st.winner, emoji.CONGRATULATE,
                    after_game_over=True)

    def _brag(self, st, me) -> None:
        """내가 1등이면 아주 가끔(1/300) 자랑한다. 전체 방송이다."""
        if not self._chance(CHANCE_BRAG):
            return
        alive = list(st.alive)
        if not alive:
            return
        if max(alive, key=lambda q: st.tiles(q.pid)).pid != self.pid:
            return
        self._broadcast(st, emoji.BRAG)

    # --- 사람에게 ---------------------------------------------------------

    def _charm_allies(self, st, me) -> None:
        """**사람 동맹**에게 가끔 애정 표현을 한다. AI 동맹에게는 안 한다.

        위와 같은 이유로 이 필터도 **변이로 안 잡힌다. 정상이다.**"""
        if not self._chance(CHANCE_CHARM):
            return
        allies = [pid for pid in st.diplomacy.allies_of(self.pid)
                  if pid in st.players and st.players[pid].kind == "human"]
        if not allies:
            return
        pool = emoji.LOVE if self._chance(3) else emoji.CHARM_ALLIES
        st.ai_emoji(self.pid, self.rng.choice(allies), pool)

    def _annoy_traitors(self, st, me) -> None:
        """**사람 배신자**를 광대(🤡)라고 부른다 — 친하지 않을 때만."""
        if not self._chance(CHANCE_ANNOY_TRAITOR):
            return
        traitors = [q.pid for q in st.alive
                    if q.kind == "human" and st.is_traitor(q.pid)
                    and not st.diplomacy.is_friendly(self.pid, q.pid)]
        if not traitors:
            return
        st.ai_emoji(self.pid, self.rng.choice(traitors), emoji.CLOWN_POOL)

    def _find_rat(self, st, me) -> None:
        """구석에 숨어 안 크는 사람을 쥐(🐀)라고 부른다.

        ⚠ **초반 10분(6,000 tick)은 안 본다.** 원본 주석이 이유를 적어 뒀다 —
        그때는 다들 작아서 전부 쥐가 된다."""
        if st.tick_count < FIND_RAT_AFTER_TICK:
            return
        if not self._chance(CHANCE_FIND_RAT):
            return
        floor = st.gmap.land_count * FIND_RAT_LAND_SHARE
        small = [q.pid for q in st.alive
                 if q.kind == "human" and 0 < st.tiles(q.pid) < floor]
        if not small:
            return
        st.ai_emoji(self.pid, self.rng.choice(small), emoji.RAT)

    def _greet(self, st, me) -> None:
        """첫 1분에만, 이웃한 사람에게 손을 흔든다."""
        if st.tick_count > GREET_BEFORE_TICK:
            return
        if not self._chance(CHANCE_GREET):
            return
        near = [pid for pid in st.border_targets(self.pid)
                if pid is not None and pid in st.players
                and st.players[pid].kind == "human"]
        if not near:
            return
        st.ai_emoji(self.pid, self.rng.choice(near), emoji.GREET)

    # --- 방송 -------------------------------------------------------------

    def _broadcast(self, st, pool: tuple[str, ...]) -> None:
        """전체에 대고 하는 말. **30초 제한을 안 받는다**(원본 `shouldSendEmoji`
        가 `AllPlayers` 면 맨 앞에서 true 를 돌려준다). 사람이 여럿이어도 한 번의
        판단으로 모두에게 간다."""
        st.ai_broadcast(self.pid, pool)
