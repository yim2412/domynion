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
    friendly = target is not None and st.diplomacy.is_friendly(me, target)

    return [
        Item("공격", submenu=lambda: attack_items(st, me, tile, notify),
             enabled=not is_mine and st.can_attack(me, target),
             hint=("내 땅이다" if is_mine else
                   "동맹이다 — 먼저 파기해야 한다" if friendly else
                   "아직 스폰 면역 중이다" if target is not None
                   and st.is_immune(target) else
                   f"보낼 병력 {mine.attack_troops():,.0f}"),
             colour=COL_ATTACK),
        Item("건설", submenu=lambda: build_items(st, me, tile, notify),
             enabled=True, hint=f"골드 {_gold(mine.gold)}", colour=COL_BUILD),
        Item("상륙", action=lambda: _boat(st, me, tile, notify),
             enabled=not is_mine and st.can_attack(me, target) and gm.passable(tile),
             hint=f"배로 병력 {mine.troops * C.BOAT_ATTACK_RATIO:,.0f} 을 보낸다",
             colour=COL_BOAT),
        Item("외교", submenu=lambda: diplomacy_items(st, me, target, notify),
             enabled=target is not None and target != me,
             hint="중립 지대다" if target is None else
                  "내 땅이다" if is_mine else f"P{target} 와의 관계",
             colour=COL_DIPLO),
    ]


# --- 공격 -------------------------------------------------------------------

def attack_items(st: GameState, me: int, tile: TileRef, notify) -> list[Item]:
    owner = int(st.gmap.owner[tile])
    target = None if owner < 0 else owner
    who = "중립" if target is None else f"P{target}"
    mine = st.players[me]
    items = [
        Item(f"{who} 치기", action=lambda: _attack(st, me, target, notify),
             hint=f"보낼 병력 {mine.attack_troops():,.0f}", colour=COL_ATTACK),
    ]
    silos = [u for u in mine.units.of(UnitType.MISSILE_SILO)
             if not u.under_construction]
    ready = st.ready_missiles(me)
    for ut in NUKES:
        cost = st.nuke_cost(me, ut)
        ok = bool(silos) and mine.gold >= cost and ready > 0
        # **원자탄만 겹쳐 산다**(원본 `isStackableNuke`). 수폭·MIRV 는 한 발씩이다 —
        # SAM 하나를 뚫는 표준 수가 ×2 라 원본 주석이 그 자리를 설명해 뒀다.
        top = st.max_bulk_nuke(me, ut) if ut is UnitType.ATOM_BOMB else 1
        items.append(Item(
            NAMES[ut],
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
    items.append(Item(
        "전함", action=lambda: _warship(st, me, notify),
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

def diplomacy_items(st: GameState, me: int, target, notify) -> list[Item]:
    if target is None or target == me:
        return []
    d = st.diplomacy
    mine = st.players[me]
    allied = d.allied(me, target)
    incoming = me in d.pending.get(target, set())
    outgoing = target in d.pending.get(me, set())
    # 동맹 요청이 받아들여질지는 **상대가 나를 보는 값**이 정한다.
    rel = st.relation_of(target, me)

    items = [
        Item("동맹 수락", action=lambda: _accept(st, me, target, notify),
             enabled=incoming,
             hint="들어온 요청이 없다" if not incoming else
                  f"P{target} 의 요청을 받는다 ({C.ALLIANCE_DURATION_TICKS * C.TICK_DT / 60:.0f}분)",
             colour=COL_DIPLO),
        Item("동맹 거절", action=lambda: _reject(st, me, target, notify),
             enabled=incoming, hint='들어온 요청이 없다' if not incoming else
                  f'P{target} 의 요청을 물린다', colour=COL_PLAIN),
        Item(f"관계 · {RELATION_LABEL[rel]}", enabled=False,
             hint=("상대가 나를 보는 눈이다. "
                   + ("적대라 동맹 요청을 받지 않는다" if rel < Relation.NEUTRAL
                      else "우호라 동맹 요청을 대체로 받아 준다"
                      if rel >= Relation.FRIENDLY else "중립 — 반반이다")),
             colour=RELATION_COLOUR[rel]),
        Item("동맹 요청", action=lambda: _request(st, me, target, notify),
             enabled=not allied and not outgoing,
             hint="이미 동맹이다" if allied else
                  "이미 요청했다" if outgoing else "동맹을 제안한다",
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
        Item("금수" if not d.embargoed(me, target) else "금수 해제",
             action=lambda: _embargo(st, me, target, notify),
             hint="무역선 항로를 끊는다 (양쪽 다 손해다)", colour=COL_DIPLO),
        Item("골드 주기", action=lambda: _donate_gold(st, me, target, notify),
             enabled=mine.gold > 0,
             hint=f"가진 골드의 1/4 ({_gold(mine.gold // 4)}) 을 보낸다",
             colour=COL_PLAIN),
        Item("병력 주기", action=lambda: _donate_troops(st, me, target, notify),
             enabled=mine.troops > 1,
             hint=f"병력의 1/4 ({mine.troops / 4:,.0f}) 을 보낸다",
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


def _donate_gold(st: GameState, me: int, target: int, notify) -> None:
    amount = st.players[me].gold // 4
    notify(f"P{target} 에게 골드 {_gold(amount)}" if st.donate_gold(me, target, amount)
           else "보낼 골드가 없다")


def _donate_troops(st: GameState, me: int, target: int, notify) -> None:
    amount = st.players[me].troops / 4
    notify(f"P{target} 에게 병력 {amount:,.0f}"
           if st.donate_troops(me, target, amount) else "보낼 병력이 없다")
