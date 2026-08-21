"""규칙 기반 AI — 언제 / 누구를 / 얼마로 치는가.

핵심은 **반응 주기**다. 매 tick 판단하게 두면 20Hz 로 공격을 쏟아 내 부대가 잘게
쪼개지고, 사람은 절대 흉내 낼 수 없는 손놀림이 된다. 사람과 비슷한 간격으로만
결정하게 묶는다.

`core` 를 읽기만 하고 고치지 않는다 — 상태 변경은 전부 `GameState` 의 행동 메서드를
거친다. 그래야 AI 가 규칙 밖의 일을 할 수 없다.
"""

from __future__ import annotations

import random

from ..core.augments import Augment
from ..core.engine import GameState
from ..core.state import PlayerState

# --- AI 튜닝 (게임 밸런스가 아니라 '행동'이라 constants.py 와 분리한다) ---------

REACT_SEC = 1.0            # 이 간격으로만 판단한다

# 아래 셋은 2026-08-21 에 240판으로 갈아 끼웠다. 옛 값(0.55 / 1.6 / 1)에서는 판의
# 51% 가 시간 종료로 끝났다 — AI 가 중립만 먹다가 판이 안 끝났다.
#
# **셋이 같이 움직여야 효과가 난다.** 100판 스윕에서 bias 만 1.6→0.4 로 내리거나
# conc 만 2로 올리면 시간 종료가 53.0% 로 **한 자릿수도 안 움직였고**, 둘을 같이
# 바꿨을 때 비로소 떨어졌다. 하나만 되돌리면 옛 상태로 돌아간다.
LAUNCH_FILL = 0.35         # 병력이 상한의 이만큼 차면 공격한다
NEUTRAL_BIAS = 0.4         # 중립 선호. 1.0 이상이면 사람을 사실상 한 번도 안 친다
MAX_CONCURRENT = 2         # 동시에 굴리는 부대 수. 늘리면 병력이 잘게 쪼개진다

# 증강 선호 가중치. 뽑히는 카드가 무작위라 '선호'는 순위가 아니라 저울이다.
AUGMENT_WEIGHT: dict[str, float] = {
    "fertile": 1.2,
    "conscript": 1.3,
    "elite": 1.0,
    "settlers": 1.2,
    "forced_march": 1.0,
    "ramparts": 0.9,
    "mountaineers": 0.7,
    "rangers": 0.7,
    "scorched": 0.8,
    "seafaring": 0.6,
}


class SimpleAI:
    """플레이어 한 명을 조종한다. 상태를 갖는 이유는 반응 주기 하나 때문이다."""

    def __init__(self, pid: int, rng: random.Random):
        self.pid = pid
        self.rng = rng
        self._cooldown = rng.uniform(0.0, REACT_SEC)   # 전원이 같은 tick 에 움직이지 않게

    # --- 공격 -------------------------------------------------------------

    def update(self, st: GameState, dt: float) -> None:
        self._cooldown -= dt
        if self._cooldown > 0.0:
            return
        self._cooldown += REACT_SEC

        p = st.players[self.pid]
        if not p.alive or st.paused or st.over:
            return
        if sum(1 for a in st.attacks if a.attacker == self.pid) >= MAX_CONCURRENT:
            return
        if p.fill_ratio(st.tiles(self.pid)) < LAUNCH_FILL:
            return

        target = self.choose_target(st)
        if target is not False:
            st.launch_attack(self.pid, target)

    def choose_target(self, st: GameState) -> "int | None | bool":
        """칠 상대. 닿는 곳이 없으면 False 를 돌려준다 (None 은 '중립'이라 못 쓴다)."""
        p = st.players[self.pid]
        reachable = st.gmap.border_targets(self.pid, p.naval_range)
        best, best_score = False, -1.0
        for owner in reachable:
            if owner == self.pid:
                continue
            if owner is None:
                score = NEUTRAL_BIAS
            else:
                d = st.players.get(owner)
                if d is None or not d.alive:
                    continue
                # 약한 쪽을 먼저 친다. 병력이 두꺼운 쪽은 칸값 자체가 비싸다.
                #
                # ⚠ 이 줄은 지금 **사실상 죽어 있다.** 후반에는 충전율이 영토 규모와
                # 무관하게 93% 확률로 1.0 에 박혀서(2026-08-21 실측) 모든 상대의
                # 점수가 0.333 으로 같아진다. 충전율 포화를 규칙 쪽에서 풀기 전까지는
                # 여기를 손봐도 소용이 없다.
                score = 1.0 / (1.0 + d.fill_ratio(st.tiles(owner)) * 2.0)
            if score > best_score:
                best, best_score = owner, score
        return best

    # --- 증강 -------------------------------------------------------------

    def pick(self, player: PlayerState, offers: list[Augment]) -> str:
        """가중 무작위. 항상 최선을 고르면 모든 AI 가 같은 빌드로 수렴해 판이 똑같아진다."""
        weights = [AUGMENT_WEIGHT.get(a.key, 1.0) for a in offers]
        return self.rng.choices(offers, weights=weights, k=1)[0].key


def attach(st: GameState, rng: random.Random,
           pids: list[int] | None = None) -> "list[SimpleAI]":
    """AI 들을 만들어 붙인다. 증강 선택기도 여기서 배선한다."""
    targets = pids if pids is not None else [p.pid for p in st.players.values() if p.is_ai]
    bots = [SimpleAI(pid, rng) for pid in targets]
    by_pid = {b.pid: b for b in bots}

    def pick(player: PlayerState, offers: list[Augment]) -> str:
        bot = by_pid.get(player.pid)
        return bot.pick(player, offers) if bot else rng.choice(offers).key

    st.ai_pick = pick
    return bots
