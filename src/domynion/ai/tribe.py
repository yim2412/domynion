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

from ..core import constants as C
from ..core.engine import GameState
from ..core.units import STRUCTURES

# 봇에게 보내는 양 — `calculateBotAttackTroops`. `ai/nation.py` 와 같은 값이다.
BOT_ATTACK_MULTIPLE = 4

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

# 건물 하나 지우는 간격은 `C.DELETE_UNIT_COOLDOWN_TICKS`(30초)다. 여기 따로
# 두지 않는다 — 전에는 이 파일에만 있는 **10 tick** 이 쓰이고 있었다(§5.78).


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

        사람이 주변 봇을 우방으로 묶어 두고 나라와 싸우는 것이 초반 구조다.

        ⚠ **이식 누락 일흔아홉 — 연장도 전부 받는다.** 원본
        `acceptAllAllianceRequests` 는 두 부분이고 우리는 앞부분만 옮겼다.
        뒷부분이 없으면 봇과 맺은 동맹은 **5분 뒤 반드시 만료된다** — 사람이
        연장을 눌러도 상대가 동의하지 않아 §5.65 의 양쪽 동의가 성립하지 않는다.
        봇을 우방으로 묶어 두는 구조가 5분짜리가 된다."""
        for requestor, recipients in list(st.diplomacy.pending.items()):
            if self.pid in recipients and requestor in st.players:
                st.accept_alliance(self.pid, requestor)
        for al in st.diplomacy.alliances:
            if al.involves(self.pid) and al.only_one_agreed_to_extend:
                st.extend_alliance(self.pid, al.other(self.pid))

    # --- 건설(의 반대) ----------------------------------------------------

    def _delete_a_structure(self, st: GameState) -> None:
        """가진 건물을 하나씩 지운다. 봇은 건물을 안 쓴다.

        정복으로 넘어온 건물이 봇 손에 쌓이면, 그 땅을 되찾기 전까지 아무도
        못 쓰는 채로 남는다 — 원본이 지우는 이유다.

        ⚠ **이식 누락 여든.** 우리 봇은 `units.remove()` 로 **그 자리에서**
        지웠다. 원본은 `DeleteUnitExecution` 을 예약하므로 (1) 30초 쿨다운을
        받고 (2) 30초 뒤에 사라지며 (3) 그동안 건물이 계속 동작한다(§5.29).
        엔진에 그 경로가 이미 있는데 봇만 우회하고 있었다 — 그래서 봇 손의
        건물이 **원본보다 30배 빨리** 사라졌다."""
        p = st.players[self.pid]
        # ⚠ 이 쿨다운 검사는 **변이로 안 잡힌다. 정상이다** — 아래
        # `st.delete_unit()` 이 같은 검사를 다시 한다(`can_delete_unit(pid, unit)`).
        # 원본도 둘 다 둔다(`TribeExecution.canDeleteUnit` + `DeleteUnitExecution.init`).
        # 여기 있는 이유는 유닛 목록을 훑기 전에 빠지기 위해서다. 파지 말 것.
        if not st.can_delete_unit(self.pid):
            return
        for u in p.units.units:
            if u.utype in STRUCTURES and not u.marked_for_deletion:
                if st.delete_unit(self.pid, u):
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
        """`attackRandomTarget` — 문턱을 넘었으면 **반격부터** 본다.

        ⚠ **이식 누락 일흔여덟.** 우리 봇은 얻어맞는 중에도 무작위 이웃을 골랐다.
        원본은 `findIncomingAttackPlayer` 로 **가장 크게 때리는 쪽**을 먼저 되받는다
        (봇은 `shouldAttack` 을 어차피 통과하므로 `force` 는 의미가 없다).
        봇이 400개인 판에서 이건 "맞으면 맞받는다"는 기본 반응이 통째로 없던 것이다."""
        if not self._has_trigger_troops(st):
            return
        attacker = self._biggest_incoming_attacker(st)
        if attacker is not None and self._attack(st, attacker):
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

    def _biggest_incoming_attacker(self, st: GameState) -> "int | None":
        """`findIncomingAttackPlayer` — 나에게 들어오는 공격 중 가장 큰 것의 주인.

        ⚠ **봇은 봇의 공격도 센다.** 원본이 거르는 조건이
        `player.type() !== Bot` 이라, 내가 봇이면 그 필터가 아예 안 걸린다."""
        best, best_troops = None, 0.0
        for a in st.attacks:
            if a.target != self.pid or a.attacker is None:
                continue
            # ⚠ 이 줄도 **변이로 안 잡힌다. 정상이다** — 친한 상대는
            # `launch_attack` 의 `can_attack` 이 어차피 막는다. 원본이 여기서
            # 거르는 이유는 *"가장 큰 공격"* 을 고를 때 동맹의 공격이 1등을
            # 차지해 **반격 자체가 무산되는 것**을 막기 위해서다(고르고 나서
            # 실패하면 그 tick 은 반격을 안 한 것이 된다).
            if st.diplomacy.is_friendly(self.pid, a.attacker):
                continue
            if a.attacker not in st.players:
                continue
            if a.troops > best_troops:
                best, best_troops = a.attacker, a.troops
        return best

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
        """남길 몫이 표적에 따라 다르다 — 중립이면 거의 다 쏟는다.

        ⚠ **이식 누락 일흔일곱 — 무엇의 비율인가가 달랐다.** 우리는 *지금 가진
        병력*의 비율을 남겼는데(`troops × (1−keep)`), 원본은
        `calculateAttackTroops` 로 **상한(`maxTroops`)의 비율**을 남긴다
        (`troops − maxTroops × ratio`).

        차이가 크다. 병력이 상한의 40% 인 봇이 사람을 칠 때, 우리 식은 60% 를
        보내고(남는 것은 상한의 16%), 원본은 **한 명도 안 보낸다**(40% − 35% 가
        `reserve` 아래라 음수). 봇이 늘 여유 없이 찔러 대던 이유가 이것이다.

        ⚠ 원본은 봇도 나라와 **같은** `AiAttackBehavior.sendAttack` 을 쓴다.
        표적이 봇이면 4배 규칙까지 그대로 걸린다."""
        p = st.players[self.pid]
        cap = p.max_troops(st.tiles(self.pid))
        if cap <= 0 or p.troops <= 0:
            return False
        keep = self.expand_ratio if target is None else self.reserve_ratio
        send = p.troops - cap * keep
        foe = st.players.get(target) if target is not None else None
        if foe is not None and foe.is_bot:
            # `calculateBotAttackTroops` — 봇에게는 상대 병력의 네 배만.
            send = min(send, foe.troops * BOT_ATTACK_MULTIPLE)
        # ⚠ 이 문턱도 **변이로 안 잡힌다. 정상이다** — `ATTACK_MIN_TROOPS` 가
        # 1.0 이라 `send <= 0` 과 갈리는 구간이 (0, 1) 뿐이고, 그 값은
        # `launch_attack` 이 같은 상수로 다시 거른다.
        if send < C.ATTACK_MIN_TROOPS:
            return False
        return st.launch_attack_troops(self.pid, target, send) is not None
