"""게임 루프 — tick 하나가 100ms (원본 `turnIntervalMs` = 100).

한 tick 순서: **성장 → 공격 진행 → 흡수/탈락 → 종료 판정.**

영토 수는 `_counts` 로 증분 유지한다. 13만 타일을 매 tick 세면 판당 수 초가 날아간다
(전수 `np.bincount` 는 0.42ms, 9000 tick 이면 3.8초). 테스트만 전수로 대조한다.

⚠ 종료 조건(시간 제한·지배)은 **원본에 없다.** openfront 는 마지막 생존자로 끝난다.
헤드리스 측정을 끝내려고 우리가 둔 것이고, P6 에서 둠스데이 클락으로 교체한다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from . import constants as C
from .constants import Terrain
from .attack import Attack
from .buildings import DefensePostIndex, find_spot, structure_tiles
from .diplomacy import Diplomacy
from .doomsday import DoomsdayClock
from .gamemap import DEFAULT_SIZE, GameMap, TileRef
from .naval import (TradeShip, TransportShip, Warship, best_spawn, shell_damage,
                    trade_gold, trade_spawn_rate, water_path)
from .nukes import Fallout, Nuke, NUKE_MAGNITUDES, blast_tiles, death_factor, sam_range
from .rail import RailNetwork, Train, train_gold, train_spawn_rate
from .spawn import place_players
from .state import PlayerState
from .units import UNIT_INFO, STRUCTURES, Unit, UnitType


class Victory(Enum):
    CONQUEST = "정복"          # 원본의 유일한 승리 조건
    DOMINATION = "지배"        # ⚠ 우리가 넣은 것 (원본에 없다)
    TIMEOUT = "시간 종료"      # ⚠ 우리가 넣은 것 (원본에 없다)


@dataclass
class GameState:
    gmap: GameMap
    players: dict[int, PlayerState]
    rng: random.Random

    tick_count: int = 0
    attacks: list[Attack] = field(default_factory=list)

    over: bool = False
    winner: int | None = None
    victory: Victory | None = None

    diplomacy: Diplomacy = field(default_factory=Diplomacy)
    boats: list[TransportShip] = field(default_factory=list)
    warships: list[Warship] = field(default_factory=list)
    trade_ships: list[TradeShip] = field(default_factory=list)
    _trade_rejections: int = 0
    # 항구 쌍은 계속 반복된다. 바다 지형은 안 바뀌므로 경로를 그대로 재사용한다.
    # ⚠ P5 에서 핵이 육지를 바다로 만들면 **여기를 비워야 한다.**
    _path_cache: dict = field(default_factory=dict)
    nukes: list[Nuke] = field(default_factory=list)
    mirvs_launched: int = 0        # 판 전체. MIRV 값이 이 수에 따라 오른다
    fallout: Fallout | None = None
    clock: DoomsdayClock = field(default_factory=DoomsdayClock)
    rail: RailNetwork = field(default_factory=RailNetwork)
    trains: list[Train] = field(default_factory=list)

    _counts: dict[int, int] = field(default_factory=dict)
    _posts: DefensePostIndex | None = None

    # --- 설정 -------------------------------------------------------------

    @classmethod
    def new(cls, player_count: int, rng: random.Random,
            map_name: str = "world", human: int = 0,
            size: str = DEFAULT_SIZE) -> "GameState":
        """`human` 은 사람이 잡는 pid. 헤드리스는 -1 을 줘서 전원 봇으로 만든다.

        `size` 는 지도 해상도(`map16x`/`map4x`/`map`). **밸런스에 직접 영향을 준다** —
        원본 공식이 전체 크기 기준이라 작은 지도에서는 상수항이 지배한다."""
        gmap = GameMap.load(map_name, size=size)
        # 원본 `SpawnExecution` — 시작 영토는 1칸이 아니라 **반경 4의 원**이다.
        # 1칸으로 시작하면 상한 공식(타일^0.6)의 바닥에서 출발해 초반이 지나치게
        # 느리고, 첫 공격 한 번에 탈락할 수 있다.
        spawns = place_players(gmap, player_count, rng)
        players = {}
        for pid, (centre, tiles) in enumerate(spawns):
            players[pid] = PlayerState(pid=pid, name=f"P{pid}",
                                       is_bot=(pid != human), start=centre)
        st = cls(gmap=gmap, players=players, rng=rng)
        st._counts = {pid: len(tiles) for pid, (_, tiles) in enumerate(spawns)}
        st._posts = DefensePostIndex(gmap.size)
        st.fallout = Fallout(gmap.size)
        return st

    # --- 조회 -------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return self.tick_count * C.TICK_DT

    def tiles(self, pid: int) -> int:
        return self._counts.get(pid, 0)

    def share(self, pid: int) -> float:
        return self.tiles(pid) / self.gmap.land_count if self.gmap.land_count else 0.0

    @property
    def alive(self) -> list[PlayerState]:
        return [p for p in self.players.values() if p.alive]

    # --- 행동 -------------------------------------------------------------

    def launch_attack(self, pid: int, target: int | None) -> Attack | None:
        p = self.players.get(pid)
        if p is None or not p.alive or self.over:
            return None
        if target is not None and self.diplomacy.is_friendly(pid, target):
            return None                 # 친한 상대는 못 친다 — 먼저 동맹을 깨야 한다
        troops = p.attack_troops()
        if troops < C.ATTACK_MIN_TROOPS:
            return None
        atk = Attack.launch(self.gmap, pid, target, troops, self.rng, self.tick_count)
        if atk is None:
            return None
        p.troops -= troops
        self.attacks.append(atk)
        return atk

    # --- 외교 -------------------------------------------------------------

    def request_alliance(self, pid: int, other: int) -> bool:
        return self.diplomacy.request(pid, other)

    def accept_alliance(self, pid: int, requestor: int) -> bool:
        return self.diplomacy.accept(pid, requestor, self.tick_count) is not None

    def break_alliance(self, pid: int, other: int) -> bool:
        """동맹 파기. 상대가 이미 배신자가 아니면 **내가** 배신자가 된다."""
        return self.diplomacy.break_alliance(pid, other, self.tick_count)

    def is_traitor(self, pid: int) -> bool:
        return self.diplomacy.is_traitor(pid, self.tick_count)

    # --- 해상 -------------------------------------------------------------

    def send_boat(self, pid: int, dst: TileRef,
                  target: int | None = "auto") -> TransportShip | None:
        """상륙 부대를 띄운다. 병력 `troops/5`, 동시에 최대 3척(`boatMaxNumber`)."""
        p = self.players.get(pid)
        if p is None or not p.alive or self.over:
            return None
        if sum(1 for b in self.boats if b.owner == pid) >= C.BOAT_MAX_NUMBER:
            return None
        if not self.gmap.passable(dst) or int(self.gmap.owner[dst]) == pid:
            return None
        if target == "auto":
            o = int(self.gmap.owner[dst])
            target = None if o < 0 else o
        if target is not None and self.diplomacy.is_friendly(pid, target):
            return None

        src = best_spawn(self.gmap, pid, dst)
        if src is None:
            return None
        path = self._water_path(src, dst)
        if path is None:
            return None
        troops = min(p.troops * C.BOAT_ATTACK_RATIO, p.troops)
        if troops < C.ATTACK_MIN_TROOPS:
            return None
        p.troops -= troops
        boat = TransportShip(owner=pid, target=target, troops=troops,
                             path=path, dst=dst)
        self.boats.append(boat)
        return boat

    def _advance_boats(self) -> None:
        still: list[TransportShip] = []
        for b in self.boats:
            p = self.players.get(b.owner)
            if p is None or not p.alive:
                continue
            # 상륙 지점이 그 사이 친해졌으면 되돌아온다 — 육상 공격과 같은 규칙이다.
            if b.target is not None and self.diplomacy.is_friendly(b.owner, b.target):
                p.troops += b.troops
                continue
            b.advance()
            if not b.arrived:
                still.append(b)
                continue

            dst = b.dst
            owner_now = int(self.gmap.owner[dst])
            if owner_now == b.owner:
                p.troops += b.troops          # 이미 내 땅이면 그냥 돌아온다
                continue
            self._conquer_tile(b.owner, dst, owner_now)
            # 상륙에 성공하면 **그 자리에서 육상 공격이 시작된다**(원본도 여기서
            # AttackExecution 을 새로 만든다). 배가 육지를 계속 먹는 게 아니다.
            atk = Attack.launch(self.gmap, b.owner, b.target, b.troops,
                                self.rng, self.tick_count)
            if atk is None:
                p.troops += b.troops
            else:
                self.attacks.append(atk)
        self.boats = still

    def _conquer_tile(self, pid: int, tile: TileRef, previous: int) -> None:
        self.gmap.owner[tile] = pid
        self._counts[pid] = self._counts.get(pid, 0) + 1
        if previous >= 0:
            self._counts[previous] = max(0, self._counts.get(previous, 0) - 1)

    def _advance_trade(self) -> None:
        """항구가 둘 이상이면 무역선이 뜬다. 도착하면 **양쪽 항구 주인이 함께** 번다."""
        ports = [(u.tile, p.pid) for p in self.alive
                 for u in p.units.of(UnitType.PORT) if not u.under_construction]
        if len(ports) >= 2:
            rate = trade_spawn_rate(self._trade_rejections, len(self.trade_ships))
            if self.rng.randrange(max(1, rate)) == 0:
                if self._spawn_trade_ship(ports):
                    self._trade_rejections = 0
                else:
                    self._trade_rejections += 1
            else:
                self._trade_rejections += 1

        still: list[TradeShip] = []
        for t in self.trade_ships:
            src_p = self.players.get(t.owner)
            dst_p = self.players.get(t.dst_owner)
            if src_p is None or not src_p.alive or dst_p is None or not dst_p.alive:
                continue
            if not self._can_trade(t.owner, t.dst_owner):
                continue                       # 금수 중이면 항로가 끊긴다
            t.advance()
            if not t.arrived:
                still.append(t)
                continue
            gold = trade_gold(len(t.path))
            src_p.gold += gold
            dst_p.gold += gold
        self.trade_ships = still

    def _water_path(self, src: TileRef, dst: TileRef) -> "list[TileRef] | None":
        key = (src, dst)
        if key not in self._path_cache:
            self._path_cache[key] = water_path(self.gmap, src, dst)
        return self._path_cache[key]

    def _can_trade(self, a: int, b: int) -> bool:
        return (a != b
                and not self.diplomacy.embargoed(a, b)
                and not self.diplomacy.embargoed(b, a))

    def _spawn_trade_ship(self, ports: list[tuple[TileRef, int]]) -> bool:
        src, dst = self.rng.sample(ports, 2)
        # `canTrade()` — 금수는 **양방향**이다. 어느 한쪽이 걸어도 항로가 끊긴다.
        if src[1] == dst[1] or not self._can_trade(src[1], dst[1]):
            return False
        path = self._water_path(src[0], dst[0])
        if path is None:
            return False
        self.trade_ships.append(TradeShip(owner=src[1], src_port=src[0],
                                          dst_port=dst[0], dst_owner=dst[1],
                                          path=path))
        return True

    # --- 전함 -------------------------------------------------------------

    def build_warship(self, pid: int, tile: TileRef) -> Warship | None:
        """항구 근처 바다에 띄운다. 골드로 사는 유닛이라 비용 계산은 건물과 같다."""
        p = self.players.get(pid)
        if p is None or not p.alive:
            return None
        if self.gmap.terrain[tile] != Terrain.OCEAN:
            return None
        cost = p.units.cost(UnitType.WARSHIP)
        if p.gold < cost:
            return None
        p.gold -= cost
        p.units.record_constructed(UnitType.WARSHIP)
        w = Warship(owner=pid, tile=tile)
        self.warships.append(w)
        return w

    def _advance_warships(self) -> None:
        """표적 우선순위: **수송선 → 적 전함 → 무역선**(원본 `WarshipExecution`).

        수송선이 첫째인 이유는 그게 상륙을 막는 유일한 수단이기 때문이다."""
        r2 = C.WARSHIP_TARGETTING_RANGE ** 2
        alive_ships: list[Warship] = []
        for w in self.warships:
            p = self.players.get(w.owner)
            if p is None or not p.alive or w.sunk:
                continue
            self._heal_warship(w, p)
            if w.cooldown > 0:
                w.cooldown -= 1
                alive_ships.append(w)
                continue

            target = self._pick_naval_target(w, r2)
            if target is not None:
                w.cooldown = C.WARSHIP_SHELL_ATTACK_RATE
                self._fire_shell(w, target)
            alive_ships.append(w)
        self.warships = [w for w in alive_ships if not w.sunk]

    def _pick_naval_target(self, w: Warship, r2: int):
        def hostile(pid: int) -> bool:
            return pid != w.owner and not self.diplomacy.is_friendly(w.owner, pid)

        for b in self.boats:
            if hostile(b.owner) and self._dist_sq(w.tile, b.tile) <= r2:
                return b
        for o in self.warships:
            if o is not w and not o.sunk and hostile(o.owner)                     and self._dist_sq(w.tile, o.tile) <= r2:
                return o
        for t in self.trade_ships:
            if hostile(t.owner) and self._dist_sq(w.tile, t.tile) <= r2:
                return t
        return None

    def _fire_shell(self, w: Warship, target) -> None:
        dmg = shell_damage(self.rng, w.veterancy)
        if isinstance(target, Warship):
            target.health -= dmg
            if target.sunk:
                w.veterancy += 1
        elif isinstance(target, TransportShip):
            # 수송선은 체력이 없다 — 원본은 포탄 한 방에 격침시킨다
            if target in self.boats:
                self.boats.remove(target)
                w.veterancy += 1
        elif isinstance(target, TradeShip):
            if target in self.trade_ships:
                self.trade_ships.remove(target)

    def _heal_warship(self, w: Warship, p: PlayerState) -> None:
        """항구 사거리 안이면 tick 당 1 회복. **클락에 표시된 쪽은 회복 못 한다** —
        그래야 클락의 유출이 실제로 배를 가라앉힌다(원본 주석 그대로)."""
        if w.owner in self.clock.marked_at:
            return
        if w.health >= C.WARSHIP_MAX_HEALTH:
            return
        r2 = C.WARSHIP_PASSIVE_HEALING_RANGE ** 2
        for port in p.units.of(UnitType.PORT):
            if self._dist_sq(w.tile, port.tile) <= r2:
                w.health = min(C.WARSHIP_MAX_HEALTH,
                               w.health + C.WARSHIP_PASSIVE_HEALING)
                return

    # --- 핵 ---------------------------------------------------------------

    def launch_nuke(self, pid: int, utype: UnitType, dst: TileRef) -> Nuke | None:
        """미사일 사일로에서 쏜다. 사일로가 없으면 못 쏜다."""
        p = self.players.get(pid)
        if p is None or not p.alive or self.over:
            return None
        if utype not in NUKE_MAGNITUDES and utype is not UnitType.MIRV:
            return None
        silos = [u for u in p.units.of(UnitType.MISSILE_SILO)
                 if not u.under_construction]
        if not silos:
            return None
        cost = self.nuke_cost(pid, utype)
        if p.gold < cost:
            return None
        p.gold -= cost
        p.units.record_constructed(utype)
        if utype is UnitType.MIRV:
            self.mirvs_launched += 1
        src = min(silos, key=lambda u: self._dist_sq(u.tile, dst)).tile
        n = Nuke(owner=pid, utype=utype, src=src, dst=dst)
        self.nukes.append(n)
        return n

    def _split_mirv(self, n: Nuke) -> None:
        """MIRV 는 스스로 터지지 않고 **탄두 여러 개로 갈라진다**(원본 350발).

        우리 지도는 원본의 1/16 면적이라 350발이면 지도가 통째로 날아간다.
        `MIRV_WARHEAD_COUNT` 를 면적 비로 줄여 같은 *비중*이 되게 한다."""
        count = max(1, int(C.MIRV_WARHEAD_COUNT * self.gmap.land_count / 2_000_000))
        w = self.gmap.width
        cx, cy = n.dst % w, n.dst // w
        spread = NUKE_MAGNITUDES[UnitType.MIRV_WARHEAD][1] * 3
        for _ in range(count):
            x = min(self.gmap.width - 1, max(0, cx + self.rng.randint(-spread, spread)))
            y = min(self.gmap.height - 1, max(0, cy + self.rng.randint(-spread, spread)))
            self._detonate(Nuke(owner=n.owner, utype=UnitType.MIRV_WARHEAD,
                                src=n.dst, dst=y * w + x))

    def nuke_cost(self, pid: int, utype: UnitType) -> int:
        """핵 값. MIRV 만 **판 전체 발사 수**를 쓴다(원본 `numMirvsLaunched`)."""
        p = self.players[pid]
        if utype is UnitType.MIRV:
            return p.units.cost(UnitType.MIRV, extra=self.mirvs_launched)
        return p.units.cost(utype)

    def _dist_sq(self, a: TileRef, b: TileRef) -> int:
        w = self.gmap.width
        return (a % w - b % w) ** 2 + (a // w - b // w) ** 2

    def _advance_nukes(self) -> None:
        still: list[Nuke] = []
        for n in self.nukes:
            n.advance()
            if self._sam_intercepts(n):
                continue
            if n.arrived(self.gmap):
                if n.utype is UnitType.MIRV:
                    self._split_mirv(n)
                else:
                    self._detonate(n)
            else:
                still.append(n)
        self.nukes = still

    def _sam_intercepts(self, n: Nuke) -> bool:
        """SAM 은 **자기 것이 아닌** 핵만 요격한다. 사거리는 레벨에 따라 70~150."""
        here = n.tile(self.gmap)
        for p in self.alive:
            if p.pid == n.owner or self.diplomacy.is_friendly(p.pid, n.owner):
                continue
            for sam in p.units.of(UnitType.SAM_LAUNCHER):
                if sam.under_construction:
                    continue
                r = sam_range(sam.level)
                if self._dist_sq(sam.tile, here) <= r * r:
                    return True
        return False

    def _detonate(self, n: Nuke) -> None:
        tiles = blast_tiles(self.gmap, n.dst, n.utype, self.rng)
        if not tiles:
            return
        gm = self.gmap

        # 1) 소유자별로 몇 칸이 날아갔는지 센다 — 병력 손실이 이 수만큼 반복 적용된다
        per_player: dict[int, int] = {}
        for t in tiles:
            o = int(gm.owner[t])
            if o >= 0:
                per_player[o] = per_player.get(o, 0) + 1

        # 2) 소유를 지우고, **바다로 바꾸거나 낙진을 남기거나 둘 중 하나만** 한다.
        #    `waterNukes()` 기본값이 false 라 보통은 낙진 쪽이다. 둘 다 하면
        #    낙진이 지도를 덮어 버린다(실측: 한 판에 90.3%).
        for t in tiles:
            o = int(gm.owner[t])
            if o >= 0:
                gm.owner[t] = -1
                self._counts[o] = max(0, self._counts.get(o, 0) - 1)
        if C.WATER_NUKES:
            for t in tiles:
                if gm.terrain[t] != Terrain.OCEAN:
                    gm.terrain[t] = Terrain.OCEAN
                    gm.raw[t] = C.OCEAN_BIT
                    gm.land_count -= 1
                self.fallout.clear(t)
            # 지형이 바뀌었다 — 바다 성분과 경로 캐시를 버린다(P4 의 전제가 깨진다)
            gm._ocean_cc = None
            self._path_cache.clear()
        else:
            self.fallout.add([t for t in tiles if gm.terrain[t] != Terrain.OCEAN])

        # 3) 병력 손실 — 칸마다, 남은 타일 수로 나뉜다
        for pid, hit in per_player.items():
            p = self.players.get(pid)
            if p is None:
                continue
            before = self.tiles(pid) + hit
            cap = p.max_troops(max(1, self.tiles(pid)))
            mine = [a for a in self.attacks if a.attacker == pid]
            boats = [b for b in self.boats if b.owner == pid]
            for i in range(hit):
                left = before - i
                p.troops = max(0.0, p.troops - death_factor(n.utype, p.troops, left, cap))
                for a in mine:
                    a.troops = max(0.0, a.troops
                                   - death_factor(n.utype, a.troops, left, cap))
                for b in boats:
                    b.troops = max(0.0, b.troops
                                   - death_factor(n.utype, b.troops, left, cap))

        # 4) 폭심 반경 안의 건물·배가 사라진다
        outer2 = NUKE_MAGNITUDES[n.utype][1] ** 2
        for p in self.players.values():
            p.units.units = [u for u in p.units.units
                             if self._dist_sq(n.dst, u.tile) >= outer2]
        self.boats = [b for b in self.boats
                      if self._dist_sq(n.dst, b.tile) >= outer2]
        self._rebuild_posts()

    # --- 철도 -------------------------------------------------------------

    def _advance_rail(self) -> None:
        """무역선이 바다로 벌듯 기차는 육지로 번다.

        **남의 역에 닿는 것이 자기 역보다 2.5배 벌린다**(동맹 35,000 vs 자기 10,000).
        그래서 철도를 깔면 이웃과 사이가 좋을 이유가 생긴다."""
        self.rail.rebuild(self.alive)
        for p in self.alive:
            factories = p.units.owned(UnitType.FACTORY)
            if not factories:
                continue
            if self.rng.randrange(max(1, train_spawn_rate(factories))) != 0:
                continue
            t = self.rail.dispatch(self.gmap, self.diplomacy, p.pid, self.rng)
            if t is not None:
                self.trains.append(t)

        still: list[Train] = []
        for t in self.trains:
            owner = self.players.get(t.owner)
            if owner is None or not owner.alive:
                continue
            t.advance()
            if not t.arrived(self.gmap):
                still.append(t)
                continue
            owner.gold += train_gold(t.rel, t.cities_visited)
        self.trains = still

    # --- 기부 -------------------------------------------------------------

    def donate_gold(self, pid: int, to: int, gold: int) -> bool:
        a, b = self.players.get(pid), self.players.get(to)
        if a is None or b is None or pid == to or gold <= 0 or a.gold < gold:
            return False
        a.gold -= gold
        b.gold += gold
        return True

    def donate_troops(self, pid: int, to: int, troops: float) -> bool:
        a, b = self.players.get(pid), self.players.get(to)
        if a is None or b is None or pid == to or troops <= 0 or a.troops < troops:
            return False
        a.troops -= troops
        b.troops += troops
        return True

    # --- 건설 -------------------------------------------------------------

    def can_build(self, pid: int, utype: UnitType, near: TileRef) -> TileRef | None:
        """지을 수 있으면 실제로 지을 칸을, 아니면 None.

        원본 `canBuild()` = 골드가 되는가(`canBuildUnitType`) + 자리가 있는가
        (`canSpawnUnitType`). 둘을 한 번에 본다."""
        p = self.players.get(pid)
        if p is None or not p.alive:
            return None
        if p.gold < p.units.cost(utype):
            return None
        if utype in STRUCTURES:
            return find_spot(self.gmap, pid, near, structure_tiles(p.units),
                             utype=utype)
        return near if self.gmap.passable(near) else None

    def build(self, pid: int, utype: UnitType, near: TileRef) -> Unit | None:
        tile = self.can_build(pid, utype, near)
        if tile is None:
            return None
        p = self.players[pid]
        p.gold -= p.units.cost(utype)
        unit = Unit(utype=utype, owner=pid, tile=tile,
                    ticks_left=UNIT_INFO[utype].construction_ticks)
        p.units.units.append(unit)
        # 원본은 `buildUnit()` 안에서, **건설이 끝나기 전에** 완공 카운터를 올린다
        # (PlayerImpl.ts 주석: "already accounts for in-progress builds").
        # 완공 시점으로 미루면 짓는 동안 같은 건물을 원본보다 싸게 연달아 지을 수 있다.
        p.units.record_constructed(utype)
        if not unit.under_construction:
            self._activate(p, unit)
        return unit

    def upgrade(self, pid: int, unit: Unit) -> bool:
        """`upgradeUnit()` — 같은 비용 함수를 다시 낸다. 레벨이 오르면 완공수가 하나
        늘어 **다음 업그레이드가 더 비싸진다.**"""
        p = self.players.get(pid)
        if p is None or not UNIT_INFO[unit.utype].upgradable or unit.under_construction:
            return False
        cost = p.units.cost(unit.utype)
        if p.gold < cost:
            return False
        p.gold -= cost
        unit.level += 1
        p.units.record_constructed(unit.utype)
        return True

    def _activate(self, p: PlayerState, unit: Unit) -> None:
        """건설이 끝났을 때. 완공 카운터는 이미 `build()` 에서 올렸다.

        방어초소는 **완공된 것만** 효과가 있다 — 원본이 `nearbyUnits(...)` 를
        `includeUnderConstruction` 없이 부르고, 그 기본값이 false 다."""
        if unit.utype is UnitType.DEFENSE_POST:
            self._rebuild_posts()

    def _rebuild_posts(self) -> None:
        posts = [(u.tile, u.owner) for p in self.players.values()
                 for u in p.units.of(UnitType.DEFENSE_POST)
                 if not u.under_construction]
        if self._posts is None:
            self._posts = DefensePostIndex(self.gmap.size)
        self._posts.rebuild(self.gmap, posts)

    def _advance_construction(self) -> None:
        for p in self.alive:
            for u in p.units.units:
                if u.under_construction:
                    u.ticks_left -= 1
                    if not u.under_construction:
                        self._activate(p, u)

    # --- tick -------------------------------------------------------------

    def tick(self) -> None:
        if self.over:
            return
        self.tick_count += 1
        self.diplomacy.expire_due(self.tick_count)
        self._grow()
        self._advance_construction()
        self._advance_nukes()
        self._advance_warships()
        self._advance_boats()
        self._advance_trade()
        self._advance_rail()
        self._advance_attacks()
        self._tick_clock()
        self._check_end()

    def _grow(self) -> None:
        for p in self.alive:
            p.troops += p.troop_increase(self.tiles(p.pid))
            p.gold += C.GOLD_PER_TICK_BOT if p.is_bot else C.GOLD_PER_TICK_HUMAN

    def _advance_attacks(self) -> None:
        still: list[Attack] = []
        for a in self.attacks:
            atk = self.players.get(a.attacker)
            if atk is None or not atk.alive:
                continue
            defender = self.players.get(a.target) if a.target is not None else None
            # 공격이 시작된 뒤에 동맹이 맺어질 수 있다. 원본은 매 tick 확인해서
            # 그런 부대를 **퇴각**시킨다 — 안 그러면 동맹 중에 계속 두들겨 맞는다.
            if (a.target is not None
                    and self.diplomacy.is_friendly(a.attacker, a.target)):
                a.retreated = True
            elif defender is not None and not defender.alive:
                a.retreated = True
            else:
                taken = a.step(self.gmap, atk, defender,
                               self.tiles(a.target) if a.target is not None else 0,
                               self.tiles(a.attacker), self.rng, self.tick_count,
                               defense_posts=self._posts,
                               fallout=self.fallout,
                               land_count=self.gmap.land_count,
                               defender_traitor=(
                                   a.target is not None
                                   and self.diplomacy.is_traitor(a.target, self.tick_count)))
                if taken:
                    self._counts[a.attacker] = self._counts.get(a.attacker, 0) + len(taken)
                    if a.target is not None:
                        self._counts[a.target] = max(
                            0, self._counts.get(a.target, 0) - len(taken))
                        self._maybe_absorb(a.attacker, a.target)

            if a.finished:
                if a.retreated:
                    atk.troops += a.troops      # 퇴각한 병력은 돌아온다
            else:
                still.append(a)
        self.attacks = still

    def _maybe_absorb(self, attacker: int, target: int) -> None:
        """`handleDeadDefender()` — 타일 100 미만으로 떨어진 수비자는 통째로 흡수된다.

        원본이 이걸 두는 이유는 잔챙이 영토를 한 칸씩 긁어내느라 판이 늘어지는 것을
        막기 위해서다. 우리 지도(3.7만~13만 칸)에서도 같은 비율로 작동한다."""
        d = self.players.get(target)
        if d is None or not d.alive or self._counts.get(target, 0) >= C.CONQUER_PLAYER_TILES:
            return
        refs = self.gmap.owned_refs(target)
        if len(refs):
            self.gmap.owner[refs] = attacker
            self._counts[attacker] = self._counts.get(attacker, 0) + len(refs)
        self._counts[target] = 0
        d.alive = False
        d.troops = 0.0
        # `conquerPlayer` — 건물도 정복자에게 넘어간다. 버리면 도시가 사라져
        # 병력 상한이 갑자기 떨어진다.
        winner = self.players[attacker]
        for u in d.units.units:
            u.owner = attacker
            winner.units.units.append(u)
            winner.units.record_constructed(u.utype)
        d.units.units = []
        self.diplomacy.drop_player(target)
        self._rebuild_posts()

    def _tick_clock(self) -> None:
        """둠스데이 클락 — 원본의 진짜 종료 규칙. 기본은 꺼져 있다(원본도 그렇다)."""
        if not self.clock.cfg.enabled:
            return
        elapsed = self.elapsed
        team_game = any(t is not None for t in self.diplomacy.teams.values())
        self.clock.update(elapsed, {p.pid: self.tiles(p.pid) for p in self.alive},
                          self.gmap.land_count, team_game)
        for p in list(self.alive):
            if self.clock.is_dead(p.pid, elapsed):
                self._wipe(p.pid)
                continue
            frac = self.clock.drain_fraction(p.pid, elapsed)
            if frac <= 0.0:
                continue
            floor = self.clock.troop_floor_fraction(p.pid, elapsed) *                 p.max_troops(self.tiles(p.pid))
            p.troops = max(floor, p.troops * (1.0 - frac * C.TICK_DT))

    def _wipe(self, pid: int) -> None:
        """영토가 통째로 썩어 사라진다 — 아무도 가져가지 않고 중립이 된다."""
        refs = self.gmap.owned_refs(pid)
        if len(refs):
            self.gmap.owner[refs] = -1
        self._counts[pid] = 0
        p = self.players[pid]
        p.alive = False
        p.troops = 0.0
        p.units.units = []
        self.diplomacy.drop_player(pid)
        self._rebuild_posts()

    def _check_end(self) -> None:
        for p in self.alive:
            if self.tiles(p.pid) <= 0 and not any(a.attacker == p.pid for a in self.attacks):
                p.alive = False
                p.troops = 0.0
                self.diplomacy.drop_player(p.pid)

        alive = self.alive
        if len(alive) <= 1:
            self._finish(alive[0].pid if alive else None, Victory.CONQUEST)
            return
        # ⚠ 아래 둘은 **원본에 없다.** 클락이 켜져 있으면 원본대로 마지막 생존자만
        # 남을 때까지 간다. 헤드리스 측정을 끝내려고 둔 안전장치일 뿐이다.
        if self.clock.cfg.enabled:
            return
        top = max(alive, key=lambda p: self.tiles(p.pid))
        if self.share(top.pid) >= C.DOMINATION_TILE_RATIO:
            self._finish(top.pid, Victory.DOMINATION)
            return
        if self.elapsed >= C.MATCH_SECONDS:
            self._finish(top.pid, Victory.TIMEOUT)

    def _finish(self, pid: int | None, how: Victory) -> None:
        self.over = True
        self.winner = pid
        self.victory = how

    # --- 검증 -------------------------------------------------------------

    def verify_counts(self) -> bool:
        """증분 카운트가 지도와 맞는가. 테스트 전용 — 런타임에 부르지 말 것."""
        scan = self.gmap.tile_counts(max(self.players) + 1)
        return all(int(scan[pid]) == self._counts.get(pid, 0) for pid in self.players)

    def border_targets(self, pid: int) -> set[int | None]:
        """닿을 수 있는 상대들. AI 가 쓴다. None 은 중립.

        **numpy 로 편다.** 파이썬 루프로 내 타일마다 이웃을 보면 영토가 17만 칸일 때
        한 번에 119ms 가 든다(실측, cProfile) — 원본 크기 지도에서 이 함수 하나가
        시뮬레이션 전체보다 6배 비쌌다. 배열을 네 방향으로 밀어 한 번에 본다."""
        gm = self.gmap
        h, w = gm.height, gm.width
        o = gm.owner.reshape(h, w)
        mine = o == pid
        if not mine.any():
            return set()
        passable = gm.passable_mask().reshape(h, w)
        vals = []
        # 내 칸의 오른쪽/왼쪽/아래/위 이웃 중 통행 가능한 것들의 소유자
        vals.append(o[:, 1:][mine[:, :-1] & passable[:, 1:]])
        vals.append(o[:, :-1][mine[:, 1:] & passable[:, :-1]])
        vals.append(o[1:, :][mine[:-1, :] & passable[1:, :]])
        vals.append(o[:-1, :][mine[1:, :] & passable[:-1, :]])
        found = np.unique(np.concatenate(vals)) if vals else np.empty(0, dtype=np.int16)
        return {None if int(v) < 0 else int(v) for v in found if int(v) != pid}
