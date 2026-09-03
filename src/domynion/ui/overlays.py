"""지도 위에 얹는 겹그림 — 원본 `client/render/frame/derive/` 의 나머지 둘.

⚠ **이식 누락 아흔넷·아흔다섯.** §5.68 에서 이름 옆 깃발(`status.py`)만 옮겼는데,
원본 `frame/derive/` 에는 규칙이 든 파일이 **넷**이다:

| 원본 | 우리 |
|---|---|
| `PlayerStatus.ts` | `status.py` (§5.68) |
| `RelationMatrix.ts` | 1024x1024 버퍼는 GPU 에 넘길 값이라 필요 없다. **분류는 필요하다** → `border_relation` |
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


@dataclass(frozen=True)
class AttackLabel:
    """진행 중인 공격 하나의 **전선 위 병력 숫자**."""

    x: int
    y: int
    troops: float
    incoming: bool          # 나를 향해 오는 공격인가 (색이 갈린다)


def attack_labels(st, me: int | None = None) -> list[AttackLabel]:
    """원본 `AttackingTroopsController` + `GameRunner.attackClusteredPositions`.

    ⚠ **내가 낀 공격만**이다(`outgoingAttacks + incomingAttacks`). 472명이 도는
    판에서 전부 그리면 지도가 숫자로 덮인다 — `attack_rings` 가 남의 배를
    안 그리는 것과 같은 이유다.

    자리는 `Attack.clustered_positions` 가 정한다. 전선이 섬이나 좁은 길목에서
    갈라지면 대표 칸이 둘이 되고 숫자도 둘 뜬다 — **한 자리만 쓰면 숫자가
    엉뚱한 전선 위에 앉는다**는 것이 원본이 그 함수를 둔 이유다.

    ⚠ 퇴각 중인 공격도 그린다. 부대는 아직 거기 있고, 오히려 **얼마가 물러나는
    중인지**가 사람이 알고 싶은 값이다(`attack_rings` 의 배와 다르다 — 배는
    표적을 가리키는 고리라 되돌아가는 배의 표적이 남으면 거짓말이 된다)."""
    if me is None:
        return []
    w = st.gmap.width
    out: list[AttackLabel] = []
    for atk in st.attacks:
        incoming = atk.target == me
        if not incoming and atk.attacker != me:
            continue
        for t in atk.clustered_positions(st.gmap):
            out.append(AttackLabel(t % w, t // w, atk.troops, incoming))
    return out


# --- 국경 색 — 원본 `PlayerView.borderColor` / `borderRelationFlags` ----------


class BorderRelation(IntEnum):
    """국경 한 변이 무엇 사이인가 — 원본 `RelationMatrix.ts` 의 셋 그대로.

    **금수가 우호를 이긴다.** 원본이 이웃을 훑다 금수를 만나면 그 자리에서
    `break` 한다(우호는 계속 훑는다) — 둘 다 해당하는 관계에서 사람이 먼저 알아야
    하는 것은 무역이 끊겼다는 쪽이기 때문이다."""

    NEUTRAL = 0
    FRIENDLY = 1
    EMBARGO = 2


def border_relation(a: int, b: int, diplomacy) -> BorderRelation:
    """두 나라 사이 국경의 관계.

    ⚠ **금수를 양방향으로 본다.** 원본은 칸 주인 쪽에서만 보고(`this.hasEmbargo`)
    양쪽이 각자 자기 국경을 그리므로, A 만 금수를 걸면 A 쪽 선만 빨갛다. 우리는
    두 칸 사이에 **선을 하나만** 긋기 때문에 한쪽만 보면 방향에 따라 신호가
    사라진다. `status.py` 의 금수 깃발과 같은 이유로 양방향으로 합친다."""
    if diplomacy.embargoed(a, b) or diplomacy.embargoed(b, a):
        return BorderRelation.EMBARGO
    if diplomacy.allied(a, b):
        return BorderRelation.FRIENDLY
    return BorderRelation.NEUTRAL


# --- 클락의 다음 파도 — 원본 `DoomsdayClockPanel` 의 한 줄 -------------------


def wave_text(w) -> str:
    """다음 파도를 한 줄로. 원본 `zoneDetail` 의 세 갈래 그대로다.

    ⚠ **셋을 한 문구로 뭉치면 안 된다.** *오르는 중*과 *쉬는 중*은 사람이 할 일이
    다르다 — 오르는 중이면 지금 잃는 중이고, 쉬는 중이면 다음 파도까지가 남은
    시간이다. 마지막 단계는 더 안 오르므로 카운트다운 자체가 거짓말이 된다."""
    if w.done:
        return f"최종 {w.target_percent:.0f}%"
    if w.growing:
        return f"{w.target_percent:.0f}% 로 오르는 중 {_hms(w.seconds_to_target)}"
    return f"다음 {w.target_percent:.0f}% 까지 {_hms(w.seconds_to_next_growth)}"


def _hms(seconds: float) -> str:
    t = max(0, int(seconds))
    return f"{t // 60}:{t % 60:02d}"
