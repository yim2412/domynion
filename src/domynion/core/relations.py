"""관계도 — 누가 누구를 어떻게 보는가.

**통째로 빠져 있던 이식 누락이다.** UI 가 아니라 규칙이다: 원본 AI 는 관계 값으로
누구를 칠지·동맹 요청을 받을지 정한다. 이게 없으면 외교가 있어도 AI 쪽 반응이
전부 무작위라 "동맹을 맺어 둘 이유"가 사라진다.

값은 **한 방향**이다. 내가 너를 어떻게 보는지와 네가 나를 어떻게 보는지가 다르다 —
공격당한 쪽만 나빠지는 것이 이 시스템의 핵심이다(`AttackExecution` 은 피해자
쪽만 갱신한다).

출처: `PlayerImpl.ts :: relation / updateRelation / decayRelations`
"""

from __future__ import annotations

from enum import IntEnum

from . import constants as C


class Relation(IntEnum):
    """`Game.ts :: Relation`. 정렬에 쓰이므로 값 순서가 의미를 갖는다."""

    HOSTILE = 0
    DISTRUSTFUL = 1
    NEUTRAL = 2
    FRIENDLY = 3


def relation_from_value(v: float) -> Relation:
    """`relationFromValue` — 문턱 −50 / 0 / 50."""
    if v < C.RELATION_HOSTILE_BELOW:
        return Relation.HOSTILE
    if v < 0:
        return Relation.DISTRUSTFUL
    if v < C.RELATION_FRIENDLY_AT:
        return Relation.NEUTRAL
    return Relation.FRIENDLY


class Relations:
    """플레이어 한 명이 남들을 보는 눈. `PlayerImpl.relations` 와 같은 자리."""

    __slots__ = ("_v",)

    def __init__(self) -> None:
        self._v: dict[int, float] = {}

    def value(self, other: int) -> float:
        return self._v.get(other, 0.0)

    def of(self, other: int) -> Relation:
        return relation_from_value(self.value(other))

    def update(self, other: int, delta: float) -> None:
        """`updateRelation` — ±100 으로 잘린다."""
        v = self._v.get(other, 0.0) + delta
        self._v[other] = min(C.RELATION_MAX, max(-C.RELATION_MAX, v))

    def decay(self) -> None:
        """`decayRelations` — 매 tick 0.05 씩 0 으로 되돌아간다.

        **원한도 호감도 시간이 지나면 잊힌다.** 이게 없으면 초반에 한 번 얻어맞은
        상대와 판이 끝날 때까지 화해할 수 없다. 0 근처(±0.1)에서는 부호가
        진동하지 않게 딱 0 으로 붙인다.
        """
        d = C.RELATION_DECAY_PER_TICK
        for pid, v in self._v.items():
            v += d if v < 0 else (-d if v > 0 else 0.0)
            self._v[pid] = 0.0 if abs(v) < d * 2 else v

    def sorted_by_relation(self, alive: set[int]) -> list[tuple[int, Relation]]:
        """`allRelationsSorted` — 적대적인 쪽부터. 죽은 상대는 뺀다."""
        rows = [(pid, v) for pid, v in self._v.items() if pid in alive]
        rows.sort(key=lambda r: r[1])
        return [(pid, relation_from_value(v)) for pid, v in rows]

    def forget(self, other: int) -> None:
        self._v.pop(other, None)


def gold_donation_relation(gold: float, tick: int, difficulty: str) -> float:
    """`DonateGoldExecution.calculateRelationUpdate`.

    덩어리 하나당 5점, 최대 100점. **덩어리 크기가 시간에 따라 커진다** — 안 그러면
    후반에 남아도는 골드로 관계를 살 수 있다.
    """
    chunk = C.GOLD_CHUNK_SIZE[difficulty]
    chunk = round(chunk + chunk * (tick / (3000 + C.SPAWN_PHASE_TURNS)))
    if chunk <= 0:
        return 0.0
    return min(100.0, int(gold // chunk) * 5.0)
