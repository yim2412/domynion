"""지도 위에 얹는 겹그림 — 원본 `client/render/frame/derive/` 의 나머지 둘.

⚠ **이식 누락 아흔넷·아흔다섯.** §5.68 에서 이름 옆 깃발(`status.py`)만 옮겼는데,
원본 `frame/derive/` 에는 규칙이 든 파일이 **넷**이다:

| 원본 | 우리 |
|---|---|
| `PlayerStatus.ts` | `status.py` (§5.68) |
| `RelationMatrix.ts` | 관계는 `diplomacy` 가 직접 답한다 — 행렬을 미리 만들 이유가 없다 |
| `NukeTelegraphs.ts` | **없었다** → 아래 `nuke_telegraphs` |
| `AttackRings.ts` | **없었다** → 아래 `attack_rings` |

가장 나쁜 것이 핵 예고다. 우리는 날아가는 핵을 **점 하나**로만 그렸다 —
*어디에 떨어지는지도, 반경이 얼마인지도, 누가 쐈는지도* 화면에 없었다.
그런데 그 셋이 전부 **사람이 지금 무엇을 해야 하는가**를 정하는 재료다:
내 땅이면 병력을 빼고, 동맹 것이면 안 막고, 적 것이면 SAM 사거리를 본다.
§5.62 가 적어 둔 *"행동도 되고 규칙도 도는데 결과가 안 보인다"* 의 네 번째 얼굴이다.

**그리기와 분리한다** — `status.py` 와 같은 이유로 이 파일에 QPainter 는 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from ..core.nukes import NUKE_MAGNITUDES


class Relation(IntEnum):
    """예고 원의 색을 가르는 셋 — 원본 `TELEGRAPH_SELF/FRIENDLY/ENEMY`.

    ⚠ **보는 사람이 없으면 전부 적이다.** 원본 리플레이·관전 경로가 그렇게 둔다
    (`localPlayerID <= 0 → TELEGRAPH_ENEMY`). 헤드리스에서도 같은 값이 나와야
    Qt 없이 잴 수 있다."""

    SELF = 0
    FRIENDLY = 1
    ENEMY = 2


@dataclass(frozen=True)
class Telegraph:
    """핵 한 발의 낙하 예고. 좌표는 **표적 칸**이지 지금 나는 자리가 아니다."""

    x: int
    y: int
    inner: int
    outer: int
    relation: Relation


@dataclass(frozen=True)
class AttackRing:
    """내 수송선이 상륙하려는 자리."""

    x: int
    y: int


def _relation(owner: int, me: int | None, diplomacy) -> Relation:
    if me is None:
        return Relation.ENEMY
    if owner == me:
        return Relation.SELF
    if diplomacy.allied(me, owner):
        return Relation.FRIENDLY
    return Relation.ENEMY


def nuke_telegraphs(st, me: int | None = None) -> list[Telegraph]:
    """비행 중인 핵의 낙하 예고 원들 — 원본 `extractNukeTelegraphs`.

    빠지는 것이 셋이고 셋 다 이유가 있다:

    - **`wait_ticks > 0` 인 핵은 안 보인다.** 겹쳐 산 핵은 발사가 뒤로 밀리는데
      (`NukeExecution.waitTicks`), 아직 안 나간 핵의 표적을 미리 띄우면 사람이
      실제보다 이르게 반응한다. 원본이 같은 조건으로 거른다.
    - **MIRV 본체는 안 보인다.** `NUKE_MAGNITUDES` 에 MIRV 가 없어서다 — 본체는
      터지지 않고 갈라진다. 반경이 없으니 그릴 원도 없다.
    - 표적이 없는 핵도 없다(우리 모델은 `dst` 가 항상 있다).
    """
    w = st.gmap.width
    out: list[Telegraph] = []
    for n in st.nukes:
        if n.wait_ticks > 0:
            continue
        mag = NUKE_MAGNITUDES.get(n.utype)
        if mag is None:
            continue
        inner, outer = mag
        out.append(Telegraph(n.dst % w, n.dst // w, inner, outer,
                             _relation(n.owner, me, st.diplomacy)))
    return out


def attack_rings(st, me: int | None = None) -> list[AttackRing]:
    """**내** 수송선이 향하는 상륙 지점들 — 원본 `extractAttackRings`.

    ⚠ **남의 배는 안 그린다.** 원본이 `u.ownerID !== owner` 로 자른다 — 400나라의
    배를 전부 그리면 지도가 고리로 덮인다. 보는 사람이 없으면 아무것도 없다.

    ⚠ **퇴각 중인 배도 안 그린다.** 되돌아가는 배의 원래 표적을 계속 띄우면
    사람이 아직 그리로 간다고 읽는다."""
    if me is None:
        return []
    w = st.gmap.width
    out: list[AttackRing] = []
    for b in st.boats:
        if b.owner != me or not b.active or b.retreating:
            continue
        out.append(AttackRing(b.dst % w, b.dst // w))
    return out
