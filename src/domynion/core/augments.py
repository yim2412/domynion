"""증강 카드와 계수 합산.

**증강은 새 규칙을 만들지 않는다. 기존 수치에 곱해지는 계수일 뿐이다.**
이 원칙을 지켜야 카드가 늘어도 판정이 하나로 유지된다. 카드마다 예외 규칙을 두면
"이 상황에서 뭐가 먼저 적용되는가"를 아무도 모르게 된다.

**예외는 없다. 열 장 전부 계수다.**

⚠ 원래 예외가 하나 있었다(항해술 `naval_range` — 바다를 건널 칸 수). openfront
이식 뒤에 **대응물이 사라졌다**: 원본 배는 물길만 이어지면 어디든 간다(사거리가
아니라 경로 탐색이다). 같은 이유로 삼림 순찰대(`cost_woodland_pct`)도 갈 곳이
없어졌다 — openfront 지형은 **평지·구릉·산악** 셋뿐이고 숲이 없다.
둘 다 openfront 에 실재하는 축으로 갈아끼웠다(2026-09-04):

| 옛 카드 | 새 카드 | 축 |
|---|---|---|
| 항해술(바다 1칸) | **상륙전** | `boat_loss_pct` — 퇴각할 때 잃는 25%가 줄어든다 |
| 삼림 순찰대(숲 −32%) | **교역로** | `trade_gold_pct` — 무역선이 도착해 버는 골드가 는다 |

**갈 곳 없는 축을 남겨 두면 카드가 조용히 아무 일도 안 한다** — 3장 중 하나가
꽝이 되고, 그 사실이 화면 어디에도 안 나온다.

드래프트 방식이다. 정지마다 무작위 3장을 받아 하나를 고르고, 같은 카드를 다시
고르면 레벨이 오른다. 그래서 빌드는 미리 짜는 것이 아니라 **뽑힌 것들 사이에서
방향을 잡아 가는 것**이다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import constants as C


@dataclass(frozen=True)
class Augment:
    key: str
    name: str
    desc: str          # Lv1 기준 설명. UI 는 레벨에 맞춰 수치를 다시 계산해 보여 준다
    field: str         # Modifiers 의 어느 축에 실리는가
    per_level: float   # Lv1 값. 레벨 배율이 여기 곱해진다


# 계수 축. 여기 없는 이름을 쓰면 `Modifiers.get` 이 0 을 돌려주므로 조용히 죽는다 —
# 새 축을 만들 때는 반드시 이 목록에 먼저 추가할 것.
FIELDS = (
    "troops_cap_pct",        # 병력 상한 +%
    "troops_growth_pct",     # 병력 성장률 +%
    "cost_vs_player_pct",    # 적 영토 정복 비용 +% (음수가 유리)
    "cost_vs_neutral_pct",   # 중립 정복 비용 +%
    "cost_highland_pct",     # 구릉·산악 정복 비용 +%
    "trade_gold_pct",        # 무역선 도착 골드 +%
    "expand_speed_pct",      # 확장 속도 +%
    "defense_pct",           # 내 영토 방어 +% (남이 나를 먹을 때 비싸진다)
    "defender_loss_pct",     # 내가 뺏을 때 상대가 추가로 잃는 병력 +%
    "boat_loss_pct",         # 상륙 부대가 퇴각할 때 잃는 몫 +% (음수가 유리)
)

AUGMENTS: list[Augment] = [
    Augment("fertile", "비옥한 땅", "병력 상한 +18%",
            "troops_cap_pct", 0.18),
    Augment("conscript", "징집령", "병력 성장 +22%",
            "troops_growth_pct", 0.22),
    Augment("elite", "정예 병단", "적 영토 정복 비용 -14%",
            "cost_vs_player_pct", -0.14),
    Augment("settlers", "개척단", "중립 정복 비용 -18%",
            "cost_vs_neutral_pct", -0.18),
    Augment("forced_march", "강행군", "확장 속도 +30%",
            "expand_speed_pct", 0.30),
    Augment("ramparts", "견고한 방벽", "내 영토 방어 +22%",
            "defense_pct", 0.22),
    Augment("mountaineers", "산악병", "구릉·산악 정복 비용 -32%",
            "cost_highland_pct", -0.32),
    Augment("traders", "교역로", "무역선 도착 골드 +25%",
            "trade_gold_pct", 0.25),
    Augment("scorched", "초토화", "정복할 때 상대 병력 추가 손실 +35%",
            "defender_loss_pct", 0.35),
    Augment("landing", "상륙전", "상륙 퇴각 손실 -30%",
            "boat_loss_pct", -0.30),
]

AUGMENTS_BY_KEY = {a.key: a for a in AUGMENTS}


def level_mult(level: int) -> float:
    """레벨 배율. Lv1 이 1.0 이고 위로 갈수록 커지되 선형보다 완만하다."""
    idx = max(1, min(C.AUGMENT_MAX_LEVEL, level)) - 1
    return C.AUGMENT_LEVEL_MULT[idx]


def value_at(aug: Augment, level: int) -> float:
    """이 증강을 레벨 N 까지 올렸을 때의 실제 값."""
    return aug.per_level * level_mult(level)


def describe(aug: Augment, level: int) -> str:
    """레벨을 반영한 설명. 카드에 Lv1 수치만 적어 두면 Lv3 을 골라도 체감이 없다."""
    v = value_at(aug, level)
    pct = abs(v) * 100
    sign = "-" if v < 0 else "+"
    return aug.desc.rsplit(" ", 1)[0] + f" {sign}{pct:.0f}%"


class Modifiers:
    """한 플레이어가 가진 증강 전부를 합산한 계수 묶음.

    같은 축에 여러 카드가 실리면 **더한다**(곱하지 않는다). 곱하면 카드가 쌓일수록
    체감이 급격히 커져 후반이 한 명의 독주가 된다."""

    __slots__ = ("_v",)

    def __init__(self, values: dict[str, float] | None = None):
        self._v = values or {}

    @classmethod
    def from_augments(cls, owned: dict[str, int]) -> "Modifiers":
        values: dict[str, float] = {}
        for key, level in owned.items():
            aug = AUGMENTS_BY_KEY.get(key)
            if aug is None or level <= 0:
                continue          # 저장본에 없는 카드가 섞여도 판이 죽지 않게
            values[aug.field] = values.get(aug.field, 0.0) + value_at(aug, level)
        return cls(values)

    def get(self, field: str) -> float:
        return self._v.get(field, 0.0)

    def mult(self, field: str, floor: float = 0.2) -> float:
        """`1 + 계수` 를 배율로 돌려준다. 비용 축에 쓰면 음수 계수가 할인이 된다.

        floor 가 있는 이유: 할인 증강을 겹쳐 쌓으면 배율이 0 이나 음수가 되어
        **공짜로 무한 확장**이 된다. 실제로 그 지점을 넘길 수 있는 조합이 있다."""
        return max(floor, 1.0 + self.get(field))


def offer(rng: random.Random, owned: dict[str, int],
          count: int = C.AUGMENT_CHOICES) -> list[Augment]:
    """이번 정지에 보여 줄 카드들.

    이미 최대 레벨인 카드는 후보에서 뺀다. 뽑아 봐야 고를 수 없는 카드가 자리를
    차지하면 선택지가 실질 2장으로 줄어든다."""
    pool = [a for a in AUGMENTS if owned.get(a.key, 0) < C.AUGMENT_MAX_LEVEL]
    if not pool:
        return []
    return rng.sample(pool, k=min(count, len(pool)))
