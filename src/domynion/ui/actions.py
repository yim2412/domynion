"""방사형 메뉴의 내용 — 사람이 할 수 있는 일 전부.

원본 `RadialMenuElements.ts` 의 구성을 그대로 따른다:

    root
      ├ 공격   → 그 칸의 소유자 전체 · 핵(원폭/수폭/MIRV)
      ├ 건설   → 도시·항구·공장·방어초소·사일로·SAM·전함
      ├ 상륙   → 배로 그 칸에
      └ 외교   → 동맹 요청/수락/파기 · 금수 · 골드/병력 기부

**못 하는 항목도 지운 게 아니라 회색으로 남긴다.** "왜 안 되지"를 알려면 항목이
보이면서 이유가 붙어야 한다 — 원본도 `disabled` 로 그렇게 한다.
"""

from __future__ import annotations

from ..core import constants as C
from ..core.engine import GameState
from ..core.relations import (RELATION_COLOUR, RELATION_LABEL,
                              Relation)
from ..core.gamemap import TileRef
from ..core.units import UNIT_INFO, UnitType
from .radial import Item

# 건설 메뉴에 올릴 것들. 원본 `BuildMenu.ts` 의 표와 같은 목록이다
# (거기 있는 원폭·수폭·MIRV 는 우리 쪽에서 공격 메뉴로 뺐다 — 목표를 찍는 행동이라
#  건물과 성격이 다르다).
BUILDABLE = (UnitType.CITY, UnitType.PORT, UnitType.FACTORY,
             UnitType.DEFENSE_POST, UnitType.MISSILE_SILO, UnitType.SAM_LAUNCHER)

NUKES = (UnitType.ATOM_BOMB, UnitType.HYDROGEN_BOMB, UnitType.MIRV)

# 클릭한 칸에서 이만큼 안에 있는 내 건물을 "그 건물을 찍었다"로 본다.
# 원본은 건물 아이콘을 직접 누르지만 우리 조작은 칸 단위다. **건물끼리 최소 15칸
# 떨어져 있으므로**(`find_spot`) 반경 7 안에는 많아야 하나뿐이라 헷갈릴 일이 없다.
STRUCTURE_CLICK_RADIUS = 7

NAMES = {
    UnitType.CITY: "도시", UnitType.PORT: "항구", UnitType.FACTORY: "공장",
    UnitType.DEFENSE_POST: "방어초소", UnitType.MISSILE_SILO: "사일로",
    UnitType.SAM_LAUNCHER: "SAM", UnitType.WARSHIP: "전함",
    UnitType.ATOM_BOMB: "원폭", UnitType.HYDROGEN_BOMB: "수폭", UnitType.MIRV: "MIRV",
}

COL_ATTACK = (150, 58, 52)
COL_BUILD = (58, 96, 128)
COL_BOAT = (52, 104, 96)
COL_DIPLO = (104, 84, 132)
COL_PLAIN = (70, 78, 96)


# 원본 `SendResourceModal.PRESETS`. 슬라이더는 안 옮겼다 — 라디얼은 칸이라
# 연속 값을 담을 자리가 없고, 프리셋 다섯이 그 역할을 이미 한다.
DONATE_PRESETS = (10, 25, 50, 75, 100)


def _gold(n: int) -> str:
    return f"{n:,}"


def structure_at(st: GameState, me: int, tile: TileRef):
    """클릭한 칸 근처의 내 건물 하나. 없으면 None."""
    p = st.players.get(me)
    if p is None:
        return None
    x, y = st.gmap.xy(tile)
    best, best_d = None, STRUCTURE_CLICK_RADIUS ** 2 + 1
    for u in p.units.units:
        if u.utype not in BUILDABLE or not u.active:
            continue
        ux, uy = st.gmap.xy(u.tile)
        d = (ux - x) ** 2 + (uy - y) ** 2
        if d < best_d:
            best, best_d = u, d
    return best


# 메뉴는 위젯을 모른다. 이모지 판을 열라는 신호만 `notify` 로 흘려보내고,
# 창 쪽에서 이 접두어를 보고 판을 연다. **접두어는 여기 한 곳에만 둔다.**
EMOJI_OPEN = "@@emoji:"


def root_items(st: GameState, me: int, tile: TileRef,
               notify) -> list[Item]:
    """타일 하나에 대해 지금 할 수 있는 일들."""
    gm = st.gmap
    owner = int(gm.owner[tile])
    target = None if owner < 0 else owner
    mine = st.players[me]
    is_mine = target == me

    return [
        # ⚠ **서브메뉴에 타일 조건을 걸지 않는다.** 원본에서 `canAttack(tile)` 이
        # 잠그는 것은 중앙 버튼(치기) 하나이고, 핵은 `nukeSpawn` 이라는 **다른
        # 규칙**을 쓴다(`centerButtonElement` vs `BuildableAttacks`). 여기는 핵과
        # 전함 부르기까지 담고 있어서, 국경을 안 맞댔다고 잠그면 **국경 밖으로는
        # 핵도 못 쏜다.** 그래서 상대 조건(`canAttackPlayer`)만 여기서 보고,
        # 타일 조건은 아래 "치기" 항목이 본다.
        Item("공격", submenu=lambda: attack_items(st, me, tile, notify),
             enabled=not is_mine and st.can_attack(me, target),
             hint=_attack_hint(st, me, tile, target),
             colour=COL_ATTACK),
        Item("건설", submenu=lambda: build_items(st, me, tile, notify),
             enabled=True, hint=f"골드 {_gold(mine.gold)}", colour=COL_BUILD),
        Item("상륙", action=lambda: _boat(st, me, tile, notify),
             enabled=st.can_send_boat(me, tile),
             hint=_boat_hint(st, me, tile, target),
             colour=COL_BOAT),
        Item("외교", submenu=lambda: diplomacy_items(st, me, target, notify),
             enabled=target is not None and target != me,
             hint="중립 지대다" if target is None else
                  "내 땅이다" if is_mine else f"P{target} 와의 관계",
             colour=COL_DIPLO),
    ]


# --- 왜 안 되는지 -----------------------------------------------------------
#
# ⚠ **갈래마다 문구가 따로 있어야 한다.** `can_attack_tile` 은 참/거짓 하나만
# 돌려주는데, 막힌 이유가 다섯이다(내 땅 · 동맹 · 면역 · 바다 · 국경을 안 맞댐).
# 이유를 안 붙이면 회색 항목만 남아 "왜 안 되지"에 답하지 못한다 — 이 파일
# 머리말이 항목을 지우지 않고 회색으로 남기는 이유가 그것이다.

def _why_not_attackable(st: GameState, me: int, tile: TileRef,
                        target: int | None) -> str | None:
    """공격이 막힌 이유. 막히지 않았으면 None."""
    gm = st.gmap
    if target == me:
        return "내 땅이다"
    if target is not None and st.diplomacy.is_friendly(me, target):
        return "동맹이다 — 먼저 파기해야 한다"
    if target is not None and st.is_immune(target):
        return "아직 스폰 면역 중이다"
    if not gm.passable(tile):
        return "바다이거나 넘을 수 없는 땅이다"
    if target is not None:
        if not st.shares_border_with(me, target):
            return "국경을 맞대지 않았다 — 배로 상륙해야 한다"
    elif not st.neutral_reaches_me(me, tile):
        return "내 땅에서 이어지지 않은 중립이다 — 배로 상륙해야 한다"
    return None


def _attack_hint(st: GameState, me: int, tile: TileRef,
                 target: int | None) -> str:
    why = _why_not_attackable(st, me, tile, target)
    if why is not None:
        return why
    return f"보낼 병력 {st.players[me].attack_troops():,.0f}"


def _boat_hint(st: GameState, me: int, tile: TileRef,
               target: int | None) -> str:
    """상륙은 국경을 안 봐도 되지만 **배·해안**이 필요하다(`canBuildTransportShip`)."""
    gm = st.gmap
    mine = st.players[me]
    if target == me:
        return "내 땅이다"
    if target is not None and st.diplomacy.is_friendly(me, target):
        return "동맹이다 — 먼저 파기해야 한다"
    if target is not None and st.is_immune(target):
        return "아직 스폰 면역 중이다"
    if not gm.passable(tile):
        return "바다이거나 넘을 수 없는 땅이다"
    afloat = sum(1 for b in st.boats if b.owner == me)
    if afloat >= C.BOAT_MAX_NUMBER:
        return f"배가 다 나가 있다 ({afloat}/{C.BOAT_MAX_NUMBER}척)"
    if not st.can_send_boat(me, tile):
        # 상한도 아니고 상대도 아니면 남는 것은 지리다 — 상륙 지점이 없거나
        # 내 해안에서 물길로 닿지 않는다.
        return "물길로 닿는 해안이 없다"
    return f"배로 병력 {mine.troops * C.BOAT_ATTACK_RATIO:,.0f} 을 보낸다"


# --- 공격 -------------------------------------------------------------------

def attack_items(st: GameState, me: int, tile: TileRef, notify) -> list[Item]:
    owner = int(st.gmap.owner[tile])
    target = None if owner < 0 else owner
    who = "중립" if target is None else f"P{target}"
    mine = st.players[me]
    items = [
        Item(f"{who} 치기", action=lambda: _attack(st, me, target, notify),
             enabled=st.can_attack_tile(me, tile),
             hint=_attack_hint(st, me, tile, target), colour=COL_ATTACK),
    ]
    # ⚠ **루트가 아니라 여기다.** 루트는 원본의 넷(공격·건설·상륙·외교)으로
    # 고정이고, 원본은 전함을 라디얼이 아니라 **직접 선택해 클릭**한다
    # (`WarshipSelectionController`). 우리는 그 조작 계층이 없어 공격 메뉴에 둔다.
    my_ships = [w for w in st.warships if w.owner == me]
    items.append(Item(
        "전함 부르기", action=lambda: _patrol(st, me, tile, notify),
        enabled=bool(my_ships),
        hint=("전함이 없다" if not my_ships else
              f"가장 가까운 전함({len(my_ships)}척 중)을 이 자리로 보낸다 · "
              "수리 후퇴는 취소된다"),
        colour=COL_BOAT))

    silos = [u for u in mine.units.of(UnitType.MISSILE_SILO)
             if not u.under_construction]
    ready = st.ready_missiles(me)
    for ut in NUKES:
        cost = st.nuke_cost(me, ut)
        ok = bool(silos) and mine.gold >= cost and ready > 0
        # **원자탄만 겹쳐 산다**(원본 `isStackableNuke`). 수폭·MIRV 는 한 발씩이다 —
        # SAM 하나를 뚫는 표준 수가 ×2 라 원본 주석이 그 자리를 설명해 뒀다.
        top = st.max_bulk_nuke(me, ut) if ut is UnitType.ATOM_BOMB else 1
        # ⚠ **지금 날고 있는 내 핵의 수**(원본 `count()` = `totalUnitLevels`).
        # 원본은 `buildTable` 의 **모든** 항목에 이 칩을 띄우는데 우리는 건물에만
        # 있었다. 핵은 `UnitType` 이라 발사된 것도 유닛으로 세어진다 — 겹쳐 사면
        # 대기 중인 것까지 포함이라(§5.60), **또 살지 말지의 재료**다.
        flying = sum(1 for n in st.nukes if n.owner == me and n.utype is ut)
        items.append(Item(
            f"{NAMES[ut]}·{flying}" if flying else NAMES[ut],
            action=(None if top > 1 else
                    (lambda u=ut: _nuke(st, me, u, tile, notify))),
            submenu=((lambda u=ut: _nuke_amounts(st, me, u, tile, notify))
                     if top > 1 else None),
            enabled=ok,
            hint=("사일로가 없다" if not silos else
                  "발사관이 전부 재장전 중이다" if ready <= 0 else
                  f"골드 {_gold(cost)} 필요 (보유 {_gold(mine.gold)})"
                  if mine.gold < cost else
                  f"골드 {_gold(cost)}" + (f" · 최대 ×{top}" if top > 1 else "")),
            colour=COL_ATTACK))
    return items


def _nuke_amounts(st: GameState, me: int, ut: UnitType, tile: TileRef,
                  notify) -> list[Item]:
    """핵 대량 구매 하위 메뉴 — 원본 `RadialMenuElements` 의 `NUKE_BULK_STEPS`.

    건물 쪽(`_upgrade_amounts`)과 **같은 네 칸 배치**다: `[1, 2, 5, 최대]`.
    단계 숫자만 다르다(건물은 5·10). 살 수 없는 칸도 회색으로 남기고, 중복도
    지우지 않는다 — 자리가 밀리면 "늘 같은 자리"라는 목적 자체가 깨진다."""
    mine = st.players[me]
    top = st.max_bulk_nuke(me, ut)
    slots = [1, *C.NUKE_BULK_STEPS, top]
    out: list[Item] = []
    for n in slots:
        price = mine.units.bulk_cost(ut, n)
        ok = n <= top
        out.append(Item(
            f"×{n}",
            action=(lambda a=n: _nuke(st, me, ut, tile, notify, a)),
            enabled=ok,
            hint=(f"{n}발 · 골드 {_gold(price)}" if ok else
                  f"골드 {_gold(price)} · 발사관 {n}개 필요 "
                  f"(지금 {st.ready_missiles(me)}개)"),
            colour=COL_ATTACK))
    return out


def _attack(st: GameState, me: int, target, notify) -> None:
    if st.launch_attack(me, target) is None:
        notify("닿지 않는다 — 국경이 맞닿아야 한다")
    else:
        notify(f"공격 개시 → {'중립' if target is None else f'P{target}'}")


def _nuke(st: GameState, me: int, ut: UnitType, tile: TileRef, notify,
          amount: int = 1) -> None:
    """`amount` 발을 같은 칸으로 쏜다. **요청한 수보다 적게 나갈 수 있다** —
    골드나 발사관이 중간에 떨어지면 거기서 멈춘다(`_upgrade` 와 같은 규칙).
    몇 발이 실제로 나갔는지를 그대로 알려 준다."""
    sent = 0
    for _ in range(amount):
        if st.launch_nuke(me, ut, tile) is None:
            break
        sent += 1
    if sent == 0:
        notify(f"{NAMES[ut]} 발사 실패 — 사일로와 골드를 확인하세요")
    elif sent < amount:
        notify(f"{NAMES[ut]} {amount}발 중 {sent}발만 나갔다 — 골드·발사관 부족")
    elif amount > 1:
        notify(f"{NAMES[ut]} {sent}발 발사")
    else:
        notify(f"{NAMES[ut]} 발사")


# --- 건설 -------------------------------------------------------------------

def build_items(st: GameState, me: int, tile: TileRef, notify) -> list[Item]:
    """**건설/업그레이드 통합 버튼**이다 — 원본 `BuildMenu.sendBuildOrUpgrade()`.

    같은 종류가 15칸 안에 이미 있으면 그 항목은 업그레이드가 되고, 없을 때만 새로
    짓는다(원본도 `canUpgrade` 를 `canBuild` 보다 먼저 본다). 예전에는 이 자리에서
    "지을 자리가 없다"고 거절해서, 사람은 도시를 두 채째부터 아예 못 늘렸다.

    옆의 숫자는 개수가 아니라 **레벨 합**이다(원본 `count()` = `totalUnitLevels`).
    도시 하나를 Lv3 으로 올린 것과 Lv1 세 채가 병력 상한에 같은 값을 낸다."""
    mine = st.players[me]
    items = []
    for ut in BUILDABLE:
        cost = mine.units.cost(ut)
        have = mine.units.owned(ut)
        up = st.find_upgrade(me, ut, tile)
        spot = None if up is not None else st.can_build(me, ut, tile)
        label = f"{NAMES[ut]}·{have}" if have else NAMES[ut]
        if up is not None:
            # 여러 레벨을 살 수 있을 때만 하위 메뉴를 연다. 한 레벨뿐이면 메뉴를
            # 한 번 더 여는 것이 손해다 — 원본도 `maxAmount <= 1` 이면 하위 메뉴를
            # 만들지 않고 클릭이 바로 ×1 로 떨어지게 둔다.
            top = st.max_bulk_upgrade(me, up)
            items.append(Item(
                f"{label} ▲Lv{up.level + 1}",
                action=(None if top > 1 else
                        (lambda x=up, u=ut: _upgrade(st, me, x, u, notify))),
                submenu=((lambda x=up, u=ut: _upgrade_amounts(st, me, x, u, notify))
                         if top > 1 else None),
                enabled=True,
                hint=(f"가까운 {NAMES[ut]}(Lv{up.level}) 를 올린다 · "
                      f"골드 {_gold(cost)}"
                      + (f" · 최대 ×{top}" if top > 1 else "")),
                colour=COL_BUILD))
            continue
        items.append(Item(
            label,
            action=(lambda u=ut: _build(st, me, u, tile, notify)),
            enabled=spot is not None,
            hint=(f"골드 {_gold(cost)} 필요 (보유 {_gold(mine.gold)})"
                  if mine.gold < cost else
                  "지을 자리가 없다 — 내 땅에서, 건물끼리 15칸 떨어져야 한다"
                  if spot is None else
                  f"골드 {_gold(cost)} · 건설 "
                  f"{UNIT_INFO[ut].construction_ticks * C.TICK_DT:.0f}초"),
            colour=COL_BUILD))
    # 전함은 건물이 아니라 바다에 띄운다
    cost = mine.units.cost(UnitType.WARSHIP)
    ports = [u for u in mine.units.of(UnitType.PORT) if not u.under_construction]
    # 전함도 원본 `buildTable` 항목이라 같은 칩이 붙는다(위 주석 참조).
    fleet = sum(1 for w in st.warships if w.owner == me)
    items.append(Item(
        f"전함·{fleet}" if fleet else "전함",
        action=lambda: _warship(st, me, notify),
        enabled=bool(ports) and mine.gold >= cost,
        hint=("항구가 필요하다" if not ports else
              f"골드 {_gold(cost)} 필요 (보유 {_gold(mine.gold)})"
              if mine.gold < cost else f"골드 {_gold(cost)} · 항구 옆에 뜬다"),
        colour=COL_BUILD))
    items.append(_delete_item(st, me, tile, notify))
    return items


def _delete_item(st: GameState, me: int, tile: TileRef, notify) -> Item:
    """철거. **골드는 안 돌아온다** — 그 사실을 힌트에 적어 둔다.

    잘못 놓은 방어초소가 도시 자리를 막고 있어도 되돌릴 방법이 없던 자리다."""
    unit = structure_at(st, me, tile)
    secs = C.DELETION_MARK_DURATION_TICKS * C.TICK_DT
    if unit is None:
        return Item("철거", enabled=False,
                    hint="이 근처에 내 건물이 없다 — 건물 위를 찍어야 한다",
                    colour=COL_PLAIN)
    name = NAMES.get(unit.utype, unit.utype.value)
    if unit.marked_for_deletion:
        left = max(0, unit.deletion_at - st.tick_count) * C.TICK_DT
        return Item(f"철거 · {name}", enabled=False,
                    hint=f"이미 철거 예정이다 — {left:.0f}초 뒤에 사라진다. 취소는 없다",
                    colour=COL_PLAIN)
    ok = st.can_delete_unit(me, unit)
    return Item(
        f"철거 · {name}", action=lambda: _delete(st, me, unit, notify),
        enabled=ok,
        hint=(f"{secs:.0f}초 뒤에 사라진다 · 골드는 안 돌아온다" if ok else
              f"철거는 {C.DELETE_UNIT_COOLDOWN_TICKS * C.TICK_DT:.0f}초에 하나씩만"),
        colour=COL_ATTACK)


def _delete(st: GameState, me: int, unit, notify) -> None:
    name = NAMES.get(unit.utype, unit.utype.value)
    secs = C.DELETION_MARK_DURATION_TICKS * C.TICK_DT
    if st.delete_unit(me, unit):
        notify(f"{name} 철거 예정 — {secs:.0f}초 뒤에 사라진다")
    else:
        notify("지금은 철거할 수 없다")


def _upgrade(st: GameState, me: int, unit, ut: UnitType, notify,
             amount: int = 1) -> None:
    """`amount` 만큼 올린다. **요청한 수보다 적게 오를 수 있다.**

    엔진이 매 단계 다시 검사하므로 골드가 중간에 떨어지면 거기까지만 오른다
    (원본 실행부 그대로). 그래서 몇 레벨이 실제로 올랐는지를 그대로 알려준다 —
    "5레벨 눌렀는데 3레벨만 올랐다"를 사람이 알 수 없으면 안 된다."""
    want = amount
    got = st.upgrade(me, unit, amount)
    nxt = _gold(st.players[me].units.cost(ut))
    if got == 0:
        notify(f"{NAMES[ut]} 업그레이드 실패 — 골드를 확인하세요")
    elif got < want:
        notify(f"{NAMES[ut]} Lv{unit.level} — 골드가 모자라 {want}레벨 중 "
               f"{got}레벨만 올렸다 · 다음 값 {nxt}")
    else:
        notify(f"{NAMES[ut]} Lv{unit.level} (+{got}) — 다음 값 {nxt}")


def _upgrade_amounts(st: GameState, me: int, unit, ut: UnitType,
                     notify) -> list[Item]:
    """대량 업그레이드 하위 메뉴 — 원본 `RadialMenuElements` 그대로.

    **네 칸을 늘 같은 자리에 둔다**: [1, 5, 10, 지금 살 수 있는 최대].
    원본 주석이 이유를 적어 뒀다 — "muscle memory". 살 수 없는 칸도 숨기지 않고
    회색으로 남긴다(이 파일 `Item` 의 규칙과도 같다).

    ⚠ 값은 `cost × 수량` 이 **아니다.** 한 레벨 올릴 때마다 다음 값이 오르므로
    누적으로 계산한다(`units.bulk_cost`)."""
    mine = st.players[me]
    top = st.max_bulk_upgrade(me, unit)
    # ⚠ **중복을 지우지 않는다.** `top` 이 5나 10과 같으면 같은 수가 두 번 뜨는데,
    # 원본이 그렇다(`const slots = [1, ...steps, maxAmount]`). 칸 수가 줄면 자리가
    # 밀려서 "늘 같은 자리" 자체가 깨진다 — 그게 이 배치의 유일한 목적이다.
    slots = [1, *C.STRUCTURE_BULK_STEPS, top]
    out: list[Item] = []
    for n in slots:
        price = mine.units.bulk_cost(ut, n)
        ok = n <= top
        out.append(Item(
            f"×{n}",
            action=(lambda a=n: _upgrade(st, me, unit, ut, notify, a)),
            enabled=ok,
            hint=(f"Lv{unit.level} → Lv{unit.level + n} · 골드 {_gold(price)}"
                  if ok else
                  f"골드 {_gold(price)} 필요 (보유 {_gold(mine.gold)})"),
            colour=COL_BUILD))
    return out


def _build(st: GameState, me: int, ut: UnitType, tile: TileRef, notify) -> None:
    if st.build(me, ut, tile) is None:
        notify(f"{NAMES[ut]} 건설 실패 — 골드나 자리를 확인하세요")
    else:
        notify(f"{NAMES[ut]} 건설 시작")


def _warship(st: GameState, me: int, notify) -> None:
    mine = st.players[me]
    for port in mine.units.of(UnitType.PORT):
        if port.under_construction:
            continue
        for n in st.gmap.neighbors(port.tile):
            if st.build_warship(me, n) is not None:
                notify("전함 건조")
                return
    notify("전함을 띄울 바다가 없다 — 항구 옆이 막혀 있다")


def _patrol(st: GameState, me: int, tile: TileRef, notify) -> None:
    """`handleManualPatrolOverride` — 사람이 전함의 순찰 지점을 찍는다.

    ⚠ 이식 누락 마흔. 전함은 §5.37~5.43 에서 스스로 움직이게 됐는데 **사람이
    조종할 수단이 없었다.** 원본은 순찰 지점을 찍을 수 있고, 찍는 순간
    **수리 후퇴가 취소된다** — 급할 때 다친 배도 불러올 수 있어야 한다."""
    mine = [w for w in st.warships if w.owner == me]
    if not mine:
        notify("전함이 없다")
        return
    w = min(mine, key=lambda x: st._dist_sq(x.tile, tile))
    w.patrol_origin = tile
    w.patrol_target = None
    if w.retreat_port is not None:
        w.retreat_port = None
        w.docked = False
        notify("전함을 부른다 — 수리 후퇴를 취소했다")
    else:
        notify("전함 순찰 지점을 옮겼다")


def _boat(st: GameState, me: int, tile: TileRef, notify) -> None:
    if st.send_boat(me, tile) is None:
        notify("배를 못 보낸다 — 해안이 있어야 하고 최대 3척이다")
    else:
        notify("상륙 부대 출발")


# --- 외교 -------------------------------------------------------------------

def _target(st: GameState, me: int, target: int, notify) -> None:
    them = st.players[target].name
    if st.target_player(me, target):
        notify(f"{them} 를 표적으로 찍었다 — 우호적인 동맹이 대신 쳐 줄 수 있다")
    else:
        notify("지금은 찍을 수 없다")

def _allies_hint(st: GameState, target: int) -> str:
    """상대의 동맹을 **잔여 시간이 짧은 순**으로. 원본은 30초 이하 빨강,
    60초 이하 노랑으로 칠한다 — 곧 풀리는 동맹은 계산에서 빼도 되기 때문이다.
    라디얼은 한 줄 힌트뿐이라 색 대신 ⚠ 로 표시한다."""
    d = st.diplomacy
    rows = []
    for al in d.alliances:
        if not al.involves(target):
            continue
        other = al.other(target)
        left = max(0, al.expires_at - st.tick_count) * C.TICK_DT
        name = st.players[other].name if other in st.players else f"P{other}"
        rows.append((left, f"{'⚠ ' if left <= 60 else ''}{name} {left:.0f}초"))
    if not rows:
        return "동맹이 없다 — 쳐도 끼어들 상대가 없다"
    rows.sort()
    return " · ".join(t for _, t in rows[:5])


def diplomacy_items(st: GameState, me: int, target, notify) -> list[Item]:
    if target is None or target == me:
        return []
    d = st.diplomacy
    mine = st.players[me]
    allied = d.allied(me, target)
    incoming = me in d.pending.get(target, set())
    outgoing = target in d.pending.get(me, set())
    foe = st.players[target]
    # 동맹 요청이 받아들여질지는 **상대가 나를 보는 값**이 정한다.
    rel = st.relation_of(target, me)
    # ⚠ **관계 값은 나라에게만 뜻이 있다.** 원본 `PlayerPanel` 이 관계 알약을
    # 세 조건에서 아예 숨긴다 — 나라가 아니거나(`type() !== Nation`), 배신자거나,
    # 이미 동맹이면. 우리는 **누구에게나 띄우고 있었고 힌트가 거짓이었다**:
    # 봇에게 "우호라 동맹 요청을 대체로 받아 준다"고 적어 두는데 `tribe.py` 는
    # 관계도 배신자도 안 보고 **전부 받는다.** 배신자는 반대로, 관계가 우호여도
    # 90% 거절한다.
    #
    # 우리는 숨기는 대신 **왜 뜻이 없는지**를 적는다 — 이 메뉴의 원칙이
    # *"못 하는 것도 보이면서 이유가 붙는다"* 이기 때문이다(`radial.Item`).
    rel_useless = ("이미 동맹이다 — 관계는 요청을 받을지 정할 때만 쓴다" if allied
                   else "배신자라 관계와 무관하게 90% 거절한다"
                   if d.is_traitor(target, st.tick_count)
                   else "봇은 관계를 안 본다 — 동맹 요청을 전부 받는다"
                   if foe.is_bot
                   else "사람이라 직접 판단한다" if foe.kind == "human"
                   else "")
    # 기부는 **친한 사이에게만, 10초에 한 번**(§5.63). 원본도 이 값을 클라이언트로
    # 내려보내 버튼을 잠근다(`GameRunner` → `canDonateGold`/`canDonateTroops`).
    can_donate = st.can_donate(me, target)
    _no_donate = ("동맹·같은 팀에게만 줄 수 있다" if not d.is_friendly(me, target)
                  else "아직 쿨다운이다 (10초)")

    items = [
        Item("동맹 수락", action=lambda: _accept(st, me, target, notify),
             enabled=incoming,
             hint="들어온 요청이 없다" if not incoming else
                  f"P{target} 의 요청을 받는다 ({C.ALLIANCE_DURATION_TICKS * C.TICK_DT / 60:.0f}분)",
             colour=COL_DIPLO),
        Item("동맹 거절", action=lambda: _reject(st, me, target, notify),
             enabled=incoming, hint='들어온 요청이 없다' if not incoming else
                  f'P{target} 의 요청을 물린다', colour=COL_PLAIN),
        Item(f"관계 · {RELATION_LABEL[rel]}" if not rel_useless
             else "관계 · —", enabled=False,
             hint=(rel_useless if rel_useless else
                   "상대가 나를 보는 눈이다. "
                   + ("적대라 동맹 요청을 받지 않는다" if rel < Relation.NEUTRAL
                      else "우호라 동맹 요청을 대체로 받아 준다"
                      if rel >= Relation.FRIENDLY else "중립 — 반반이다")),
             colour=(COL_PLAIN if rel_useless else RELATION_COLOUR[rel])),
        # 상대가 **누구와** 동맹이고 **얼마나 남았는지**. 칠 상대를 고를 때
        # 먼저 보는 것이다 — 동맹이 붙어 있으면 그쪽이 개입한다. 원본
        # `PlayerPanel` 은 이름마다 잔여 시간을 달고 30초/60초에 색을 바꾼다.
        Item(f"동맹국 {len(d.allies_of(target))}", enabled=False,
             hint=_allies_hint(st, target), colour=COL_DIPLO),
        # `other.betrayals()`. 몇 번 뒤통수를 쳤는지가 동맹을 맺을지의 재료다.
        # 우리는 **종료 화면에만** 있었다 — 판 중에 정작 필요한 자리에 없었다.
        Item(f"배신 {d.betrayals.get(target, 0)}회", enabled=False,
             hint=("한 번도 안 깼다" if not d.betrayals.get(target)
                   else "동맹을 먼저 깬 횟수다. 잦으면 연장이 위험하다"),
             colour=COL_ATTACK if d.betrayals.get(target) else COL_PLAIN),
        Item("동맹 요청", action=lambda: _request(st, me, target, notify),
             enabled=not allied and not outgoing,
             hint="이미 동맹이다" if allied else
                  "이미 요청했다" if outgoing else "동맹을 제안한다",
             colour=COL_DIPLO),
        Item("동맹 연장", action=lambda: _extend(st, me, target, notify),
             enabled=allied,
             hint=("동맹이 아니다" if not allied else
                   _extend_hint(st, me, target)),
             colour=COL_DIPLO),
        Item("동맹 파기", action=lambda: _break(st, me, target, notify),
             enabled=allied,
             hint="동맹이 아니다" if not allied else
                  f"⚠ 배신자가 된다 — {C.TRAITOR_DURATION_TICKS * C.TICK_DT:.0f}초 동안 방어 절반",
             colour=COL_ATTACK),
        Item("표적 지정", action=lambda: _target(st, me, target, notify),
             enabled=st.can_target(me, target),
             hint=("동맹에게 이 나라를 쳐 달라고 부탁한다 "
                   f"(관계 {C.REL_TARGETED:+.0f})"
                   if st.can_target(me, target)
                   else "동맹은 못 찍는다" if st.diplomacy.is_friendly(me, target)
                   else f"{C.TARGET_COOLDOWN_TICKS * C.TICK_DT:.0f}초에 한 번만"),
             colour=COL_ATTACK),
        Item("한마디", action=lambda: notify(f"{EMOJI_OPEN}{target}"),
             enabled=st.emojis.can_send(me, target, st.tick_count),
             hint=("이모지를 보낸다. 🖕 하나가 관계를 크게 흔든다"
                   if st.emojis.can_send(me, target, st.tick_count)
                   else "아직 쿨다운이다 (5초)"),
             colour=COL_DIPLO),
        # ⚠ 원본 패널은 **무역 상태를 따로 한 줄로** 띄운다
        # (`other.hasEmbargoAgainst(my)`). 우리는 *내가* 건 것만 보여 주고 있어서,
        # 무역선이 왜 안 오는지가 화면 어디에도 없었다 — 상대가 건 금수는
        # 내가 풀 수 없으므로 **버튼이 아니라 알림**이다.
        Item("금수" if not d.embargoed(me, target) else "금수 해제",
             action=lambda: _embargo(st, me, target, notify),
             hint=("무역선 항로를 끊는다 (양쪽 다 손해다)"
                   + ("  ⚠ 그쪽도 나를 막고 있다"
                      if d.embargoed(target, me) else "")),
             colour=COL_DIPLO),
        # ⚠ **양을 고른다**(원본 `SendResourceModal` 의 `PRESETS`). 우리는
        # `1/DONATION_DIVISOR` 고정이었다 — 그 값은 **AI 가 얼마를 주는가**의
        # 규칙이지 사람이 얼마를 줄 수 있는가가 아니다. 관계는 액수에 비례하므로
        # (`gold_donation_relation`), 고정이면 **관계를 사는 속도가 하나뿐**이다.
        Item("골드 주기", submenu=lambda: _donate_amounts(
                 st, me, target, notify, gold=True),
             enabled=can_donate and mine.gold > 0,
             hint=("얼마를 줄지 고른다" if can_donate else _no_donate),
             colour=COL_PLAIN),
        Item("병력 주기", submenu=lambda: _donate_amounts(
                 st, me, target, notify, gold=False),
             enabled=can_donate and mine.troops > 1,
             hint=("얼마를 줄지 고른다" if can_donate else _no_donate),
             colour=COL_PLAIN),
    ]
    return items


def _request(st: GameState, me: int, target: int, notify) -> None:
    notify(f"P{target} 에게 동맹 요청" if st.request_alliance(me, target)
           else "요청할 수 없다")


def _reject(st: GameState, me: int, target: int, notify) -> None:
    st.reject_alliance(me, target)
    notify(f'P{target} 의 동맹 요청을 거절')


def _accept(st: GameState, me: int, target: int, notify) -> None:
    notify(f"P{target} 와 동맹" if st.accept_alliance(me, target)
           else "받을 요청이 없다")


def _break(st: GameState, me: int, target: int, notify) -> None:
    if st.break_alliance(me, target):
        notify(f"P{target} 와의 동맹 파기 — 배신자가 됐다")
    else:
        notify("동맹이 아니다")


def _embargo(st: GameState, me: int, target: int, notify) -> None:
    d = st.diplomacy
    if d.embargoed(me, target):
        d.stop_embargo(me, target)
        notify(f"P{target} 금수 해제")
    else:
        d.start_embargo(me, target)
        notify(f"P{target} 에 금수 조치")


def _alliance_with(st: GameState, me: int, target: int):
    for al in st.diplomacy.alliances:
        if al.involves(me) and al.other(me) == target:
            return al
    return None


def _extend_hint(st: GameState, me: int, target: int) -> str:
    """남은 시간과 **누가 동의했는지**를 보여준다.

    원본은 이 정보를 `PlayerPanel` 의 카운트다운으로 준다. 우리는 패널이 없으므로
    힌트 한 줄에 넣는다 — 없으면 사람은 동맹이 언제 끝나는지 알 방법이 없다."""
    al = _alliance_with(st, me, target)
    if al is None:
        return "동맹이 아니다"
    left = max(0, al.expires_at - st.tick_count) * C.TICK_DT
    mine = al._extend_a if me == al.a else al._extend_b
    theirs = al._extend_b if me == al.a else al._extend_a
    if al.both_agreed_to_extend:
        return f"양쪽이 동의했다 — 만료 때 연장된다 ({left:.0f}초 남음)"
    if mine:
        return f"이미 요청했다 — 상대의 답을 기다린다 ({left:.0f}초 남음)"
    if theirs:
        return f"상대가 먼저 요청했다 — 누르면 성사된다 ({left:.0f}초 남음)"
    return f"{left:.0f}초 남음 · 연장을 요청한다"


def _extend(st: GameState, me: int, target: int, notify) -> None:
    """`AllianceExtensionExecution` — **양쪽이 동의해야** 연장된다.

    ⚠ 이 경로가 통째로 없었다(§5.53). 규칙(`request_extension` ·
    `both_agreed_to_extend`)은 `diplomacy.py` 에 있었는데 부르는 곳이 사람 쪽에도
    AI 쪽에도 없어서 **모든 동맹이 예외 없이 만료됐다.**"""
    if _alliance_with(st, me, target) is None:
        notify("동맹이 아니다")
        return
    # ⚠ 규칙은 **엔진 한 자리**에 있다(§5.65). 여기서 `request_extension` 을 직접
    # 부르면 즉시 갱신도, 상대에게 가는 알림도 건너뛴다.
    if st.extend_alliance(me, target):
        notify(f"P{target} 와 동맹 연장 — 기간이 지금부터 다시 간다")
    else:
        notify(f"P{target} 에게 연장을 요청했다 — 상대가 동의해야 성사된다")


def _donate_amounts(st: GameState, me: int, target: int, notify, *,
                    gold: bool) -> list[Item]:
    """기부 양 하위 메뉴 — 원본 `SendResourceModal.PRESETS` = 10·25·50·75·100%.

    `_nuke_amounts` · `_upgrade_amounts` 와 **같은 자리 배치**다: 살 수 없는
    칸도 회색으로 남긴다. 자리가 밀리면 "늘 같은 자리"가 깨진다.

    ⚠ **병력은 상대의 남은 자리까지만 간다**(`getCapacityLeft` = `maxTroops −
    troops`). 넘치는 만큼은 **애초에 안 가고 관계도 안 오른다**(§5.71) —
    상한에 붙은 동맹에게 100%를 눌러도 아무 일이 없다. 그것을 누르기 전에
    보여 준다. 골드는 상한이 없다.
    """
    mine = st.players[me]
    them = st.players.get(target)
    total = mine.gold if gold else mine.troops
    room = (None if gold or them is None
            else max(0.0, them.max_troops(st.tiles(target)) - them.troops))
    # ⚠ **원본에는 경로가 둘이다.** 라디얼은 액수를 안 넘겨 `Config` 의 기본값
    # 1/3 이 나가고(`handleDonateGold(recipient)` → `null`), 패널의 모달은 고른
    # 양을 넘긴다. §5.90 은 라디얼만 보고 *"기본값이 곧 사람이 보내는 액수"* 라고
    # 적었는데 **모달을 못 봤다.** 우리 라디얼이 유일한 경로이므로 **둘을 한
    # 메뉴에 담는다** — 첫 칸이 원본 라디얼, 나머지가 모달의 프리셋이다.
    out: list[Item] = [Item(
        f"기본 1/{C.DONATION_DIVISOR}",
        action=(lambda: (_donate_gold(st, me, target, notify) if gold
                         else _donate_troops(st, me, target, notify))),
        enabled=total > 0,
        hint=(f"원본 라디얼과 같다 — "
              + (f"골드 {_gold(int(total // C.DONATION_DIVISOR))}"
                 if gold else f"병력 {total / C.DONATION_DIVISOR:,.0f}")
              if total > 0 else "보낼 것이 없다"),
        colour=COL_PLAIN)]
    for pct in DONATE_PRESETS:
        amount = total * pct / 100
        goes = amount if room is None else min(amount, room)
        ok = goes >= (1 if gold else 1.0)
        label = f"{pct}%"
        if gold:
            hint = f"골드 {_gold(int(amount))} 을 보낸다"
        elif room is not None and goes < amount:
            hint = (f"병력 {amount:,.0f} 중 {goes:,.0f} 만 간다 — "
                    f"상대의 남은 자리가 그만큼이다")
        else:
            hint = f"병력 {amount:,.0f} 을 보낸다"
        out.append(Item(
            label,
            action=(lambda a=amount: (_donate_gold(st, me, target, notify, a)
                                      if gold else
                                      _donate_troops(st, me, target, notify, a))),
            enabled=ok,
            hint=hint if ok else ("보낼 것이 없다" if total <= 0
                                  else "상대가 상한이라 못 받는다"),
            colour=COL_PLAIN))
    return out


def _donate_gold(st: GameState, me: int, target: int, notify,
                 amount: float | None = None) -> None:
    """⚠ `amount=None` 은 원본 `DonateGoldExecution` 의
    `this.gold ??= this.sender.gold() / 3n` — **양을 안 줬을 때의 기본값**이고,
    원본 **라디얼**이 그 경로다(`handleDonateGold(recipient)` → `null`).
    우리 메뉴의 첫 칸(*기본 1/3*)이 여기로 온다."""
    if amount is None:
        amount = st.players[me].gold // C.DONATION_DIVISOR
    amount = int(amount)
    notify(f"P{target} 에게 골드 {_gold(amount)}" if st.donate_gold(me, target, amount)
           else "보낼 골드가 없다")


def _donate_troops(st: GameState, me: int, target: int, notify,
                   amount: float | None = None) -> None:
    if amount is None:
        amount = st.players[me].troops / C.DONATION_DIVISOR
    # ⚠ 실패 이유가 둘이다(§5.71) — 내 병력이 없거나, **상대가 상한이라 못 받거나**.
    # 앞의 것만 말하면 상한에 붙은 동맹에게 보내려다 "내 병력이 없다"는 틀린 답을 본다.
    notify(f"P{target} 에게 병력 {amount:,.0f}"
           if st.donate_troops(me, target, amount)
           else "병력을 보낼 수 없다 — 내 병력이 없거나 상대가 상한이다")
