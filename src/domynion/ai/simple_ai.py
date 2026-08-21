"""규칙 기반 AI — 언제 / 누구를 / 얼마로 치는가.

핵심은 **반응 주기**다. 매 tick 판단하게 두면 20Hz 로 공격을 쏟아 내 부대가 잘게
쪼개지고, 사람은 절대 흉내 낼 수 없는 손놀림이 된다. 사람과 비슷한 간격으로만
결정하게 묶는다.

`core` 를 읽기만 하고 고치지 않는다 — 상태 변경은 전부 `GameState` 의 행동 메서드를
거친다. 그래야 AI 가 규칙 밖의 일을 할 수 없다.
"""

from __future__ import annotations

import random

from ..core.engine import GameState
from ..core.units import UnitType

# --- AI 튜닝 (게임 밸런스가 아니라 '행동'이라 constants.py 와 분리한다) ---------

# ⚠ 아래 값들은 **v0.1 코어에서 튜닝한 것이라 지금은 근거가 없다.** 코어 공식이
# 통째로 바뀌었으므로 그때 잰 수치(시간 종료 51.2%→41.7% 등)는 아무것도 말해 주지
# 않는다. P1b 가 끝난 뒤 원본 봇(`NationExecution` 등)을 보고 다시 짠다.
REACT_SEC = 1.0            # 이 간격으로만 판단한다
LAUNCH_FILL = 0.35         # 병력이 상한의 이만큼 차면 공격한다
NEUTRAL_BIAS = 0.4         # 중립 선호
MAX_CONCURRENT = 2         # 동시에 굴리는 부대 수

# 건설 우선순위. 도시가 첫째인 이유는 병력 상한을 직접 올리기 때문이다(레벨당 25만).
# 원본 봇(`NationExecution`)의 판단은 P6 에서 옮긴다 — 지금은 골드가 놀지 않게만 한다.
BUILD_ORDER = (UnitType.CITY, UnitType.PORT, UnitType.DEFENSE_POST, UnitType.FACTORY)
BUILD_EVERY_SEC = 5.0

# 외교. 원본 봇(`NationExecution`)의 판단은 P6 에서 옮긴다 — 지금은 동맹 규칙이
# 판에서 실제로 작동하는지 볼 만큼만 한다.
ALLY_CHANCE = 0.15         # 반응마다 이 확률로 이웃에게 동맹을 건다
ACCEPT_CHANCE = 0.6        # 들어온 요청을 받을 확률
BETRAY_TILE_RATIO = 0.5    # 동맹이 나보다 이만큼 작아지면 배신을 고려한다
BETRAY_CHANCE = 0.05

# 상륙. 육지로 닿는 곳이 없을 때만 본다 — 배는 병력의 1/5 을 통째로 걸어서 비싸다.
BOAT_CHANCE = 0.25
BOAT_SEARCH = 600          # 상륙 후보를 이만큼만 훑는다(지도 전체는 13만 칸이다)

class SimpleAI:
    """플레이어 한 명을 조종한다. 상태를 갖는 이유는 반응 주기 하나 때문이다."""

    def __init__(self, pid: int, rng: random.Random):
        self.pid = pid
        self.rng = rng
        self._cooldown = rng.uniform(0.0, REACT_SEC)   # 전원이 같은 tick 에 움직이지 않게
        self._build_cd = rng.uniform(0.0, BUILD_EVERY_SEC)

    # --- 공격 -------------------------------------------------------------

    def update(self, st: GameState, dt: float) -> None:
        p = st.players[self.pid]
        if not p.alive or st.over:
            return
        self._maybe_build(st, dt)

        self._cooldown -= dt
        if self._cooldown > 0.0:
            return
        self._cooldown += REACT_SEC
        if sum(1 for a in st.attacks if a.attacker == self.pid) >= MAX_CONCURRENT:
            return
        if p.troops / p.max_troops(st.tiles(self.pid)) < LAUNCH_FILL:
            return

        # `border_targets` 는 내 영토를 전부 훑는다(13만 칸 지도에서 비싸다).
        # 외교와 목표 선택이 각자 부르면 판당 실행 시간이 17→29초가 된다 — 한 번만 부른다.
        reachable = st.border_targets(self.pid)
        self._diplomacy(st, reachable)

        target = self.choose_target(st, reachable)
        if target is not False:
            st.launch_attack(self.pid, target)
        elif self.rng.random() < BOAT_CHANCE:
            self._maybe_boat(st)          # 육지로 갈 곳이 없으면 바다를 본다

    def _maybe_boat(self, st: GameState) -> None:
        """바다 건너 상륙. 내 해안 근처에서 남의 땅이나 빈 땅을 찾는다."""
        import numpy as np

        from ..core.naval import shoreline_tiles

        shore = shoreline_tiles(st.gmap, self.pid)
        if not len(shore):
            return
        origin = int(self.rng.choice(shore.tolist()))
        gm = st.gmap
        ox, oy = gm.xy(origin)
        for _ in range(12):
            r = self.rng.randint(4, 60)
            ang = self.rng.random() * 6.283185
            x = int(ox + r * np.cos(ang))
            y = int(oy + r * np.sin(ang))
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
            if st.send_boat(self.pid, t) is not None:
                return

    def _diplomacy(self, st: GameState, reachable: "set[int | None]") -> None:
        """들어온 요청을 받고, 이웃에게 걸고, 약해진 동맹은 버린다."""
        d = st.diplomacy
        for requestor, recipients in list(d.pending.items()):
            if self.pid in recipients and requestor in st.players:
                if self.rng.random() < ACCEPT_CHANCE:
                    st.accept_alliance(self.pid, requestor)
                else:
                    d.reject(self.pid, requestor)

        neighbours = [o for o in reachable if o is not None]
        for other in neighbours:
            if d.is_friendly(self.pid, other):
                # 동맹이 크게 약해졌으면 등을 돌린다. 배신 낙인(30초)이 값이므로
                # 자주 하지는 않는다.
                mine, theirs = st.tiles(self.pid), st.tiles(other)
                if (theirs < mine * BETRAY_TILE_RATIO
                        and self.rng.random() < BETRAY_CHANCE):
                    st.break_alliance(self.pid, other)
            elif self.rng.random() < ALLY_CHANCE:
                st.request_alliance(self.pid, other)

    def _maybe_build(self, st: GameState, dt: float) -> None:
        """골드가 쌓이는 대로 짓는다. 건설은 공격 판단보다 드물게 본다 —
        건물 자리 탐색이 비싸서 매 초 돌리면 판당 실행 시간이 눈에 띄게 늘어난다."""
        self._build_cd -= dt
        if self._build_cd > 0.0:
            return
        self._build_cd += BUILD_EVERY_SEC
        p = st.players[self.pid]
        for utype in BUILD_ORDER:
            if p.gold >= p.units.cost(utype):
                near = int(self.rng.choice(st.gmap.owned_refs(self.pid).tolist()))
                if st.build(self.pid, utype, near) is not None:
                    return

    def choose_target(self, st: GameState,
                      reachable: "set[int | None] | None" = None) -> "int | None | bool":
        """칠 상대. 닿는 곳이 없으면 False 를 돌려준다 (None 은 '중립'이라 못 쓴다)."""
        if reachable is None:
            reachable = st.border_targets(self.pid)
        best, best_score = False, -1.0
        for owner in reachable:
            if owner == self.pid:
                continue
            if owner is not None and st.diplomacy.is_friendly(self.pid, owner):
                continue                # 친한 상대는 후보가 아니다
            if owner is None:
                score = NEUTRAL_BIAS
            else:
                d = st.players.get(owner)
                if d is None or not d.alive:
                    continue
                # 약한 쪽을 먼저 친다. 원본 공식에서 수비 병력이 두꺼울수록 공격측
                # 손실(`within(수비/공격, 0.6, 2)`)이 그대로 커지기 때문이다.
                fill = d.troops / d.max_troops(st.tiles(owner))
                score = 1.0 / (1.0 + fill * 2.0)
            if score > best_score:
                best, best_score = owner, score
        return best


def attach(st: GameState, rng: random.Random,
           pids: list[int] | None = None) -> "list[SimpleAI]":
    """AI 들을 만들어 붙인다. 증강 선택 배선은 P7 까지 떼어 뒀다."""
    targets = pids if pids is not None else [p.pid for p in st.players.values()]
    return [SimpleAI(pid, rng) for pid in targets]
