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
from .buildings import DefensePostIndex, euclid_sq, find_spot, structure_tiles
from .diplomacy import Diplomacy
from .doomsday import DoomsdayClock
from .events import Event, EventKind, EventLog
from .gamemap import DEFAULT_SIZE, GameMap, TileRef
from .naval import (TradeShip, TransportShip, Warship, best_spawn, shell_damage,
                    _touching_components, manhattan, port_check_due,
                    trade_gold, trade_spawn_rate,
                    trading_ports, water_path)
from .nukes import (Fallout, Nuke, NUKE_MAGNITUDES, SAM_TARGETABLE_TYPES,
                    blast_tiles, death_factor, is_targetable, sam_range)
from .rail import RailNetwork, Train, train_gold, train_spawn_rate
from .spawn import pick_spawn, place_at, spawn_tiles
from . import emoji as emoji_mod
from .emoji import Emojis
from .emoji import relation_delta as emoji_relation_delta
from .emoji import reply_to as emoji_reply_to
from .relations import Relation, gold_donation_relation
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
    # 항구 쌍은 계속 반복된다. 바다 지형은 안 바뀌므로 경로를 그대로 재사용한다.
    # ⚠ P5 에서 핵이 육지를 바다로 만들면 **여기를 비워야 한다.**
    _path_cache: dict = field(default_factory=dict)
    nukes: list[Nuke] = field(default_factory=list)
    mirvs_launched: int = 0        # 판 전체. MIRV 값이 이 수에 따라 오른다
    fallout: Fallout | None = None
    clock: DoomsdayClock = field(default_factory=DoomsdayClock)
    rail: RailNetwork = field(default_factory=RailNetwork)
    log: EventLog = field(default_factory=EventLog)
    emojis: Emojis = field(default_factory=Emojis)
    # 누가 누구를 표적으로 찍었나: (찍은 사람, 찍힌 사람) → 찍은 tick.
    # 동맹에게 보내는 부탁이라 **시간이 지나면 사라진다** — 안 그러면 판 내내
    # 옛 부탁이 남아 AI 가 엉뚱한 상대를 계속 친다.
    targets: dict[tuple[int, int], int] = field(default_factory=dict)
    trains: list[Train] = field(default_factory=list)

    # 관계 변화량이 난이도를 탄다(`AttackExecution` 의 relationChange).
    difficulty: str = "medium"

    # 스폰 페이즈 — 사람이 시작 위치를 고르는 동안 **판 전체가 멈춘다**.
    # 원본은 `activeDuringSpawnPhase()` 가 false 인 Execution 을 전부 건너뛴다:
    # AI 도, 공격도, 성장도 안 돈다. None 이면 이미 끝난 것이다.
    spawn_phase: bool = False

    # 금수 벌점을 이미 매겼는지(`embargoMalusApplied`). 매 tick 깎으면 안 된다.
    _embargo_malus: dict[int, set[int]] = field(default_factory=dict)

    _counts: dict[int, int] = field(default_factory=dict)
    _posts: DefensePostIndex | None = None

    # --- 설정 -------------------------------------------------------------

    @classmethod
    def new(cls, player_count: int, rng: random.Random,
            map_name: str = "world", human: int = 0,
            size: str = DEFAULT_SIZE, bots: int = 0) -> "GameState":
        """`human` 은 사람이 잡는 pid. 헤드리스는 -1 을 줘서 전원 봇으로 만든다.

        `size` 는 지도 해상도(`map16x`/`map4x`/`map`). **밸런스에 직접 영향을 준다** —
        원본 공식이 전체 크기 기준이라 작은 지도에서는 상수항이 지배한다."""
        gmap = GameMap.load(map_name, size=size)
        # 원본 `SpawnExecution` — 시작 영토는 1칸이 아니라 **반경 4의 원**이다.
        # 1칸으로 시작하면 상한 공식(타일^0.6)의 바닥에서 출발해 초반이 지나치게
        # 느리고, 첫 공격 한 번에 탈락할 수 있다.
        #
        # `player_count` 는 **나라 수**다. 봇은 `bots` 로 따로 센다 — 원본 싱글의
        # 기본 구성이 72개 나라 + 봇 400개라, 지도를 채우는 것은 사실 봇 쪽이다.
        players: dict[int, PlayerState] = {}
        counts: dict[int, int] = {}
        pid = 0

        # 1) 나라는 manifest 좌표에 앉힌다. 아메리카가 아메리카에 있어야 한다.
        for name, want in gmap.nations[:player_count]:
            got = place_at(gmap, pid, want, rng)
            if got is None:
                continue
            centre, tiles = got
            players[pid] = PlayerState(pid=pid, name=name, kind="nation",
                                       start=centre)
            counts[pid] = len(tiles)
            pid += 1

        # 2) 좌표가 모자라면(나라가 적은 지도) 무작위로 채운다.
        while pid < player_count:
            got = pick_spawn(gmap, rng, [p.start for p in players.values()])
            if got is None:
                break
            centre, tiles = got
            for t in tiles:
                gmap.owner[t] = pid
            players[pid] = PlayerState(pid=pid, name=f"나라 {pid}",
                                       kind="nation", start=centre)
            counts[pid] = len(tiles)
            pid += 1

        # 3) 봇으로 빈 곳을 메운다. 자리를 못 찾으면 **거기서 멈춘다** —
        #    작은 지도에서 400개를 다 넣으려다 배치가 몇 분씩 걸린다.
        for _ in range(bots):
            got = pick_spawn(gmap, rng, [])
            if got is None:
                break
            centre, tiles = got
            for t in tiles:
                gmap.owner[t] = pid
            players[pid] = PlayerState(pid=pid, name=f"부족 {pid}", kind="bot",
                                       start=centre)
            counts[pid] = len(tiles)
            pid += 1

        # 4) 사람은 마지막에. 앞에서 만든 나라 하나를 사람으로 바꾼다.
        if human in players:
            players[human].kind = "human"
            players[human].is_bot = False

        st = cls(gmap=gmap, players=players, rng=rng)
        st._counts = counts
        st._posts = DefensePostIndex(gmap.size)
        st.fallout = Fallout(gmap.size)
        # 사람이 없으면(헤드리스) 고를 사람도 없다 — 그냥 시작한다.
        st.spawn_phase = human in players
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

    def is_immune(self, pid: int) -> bool:
        """스폰 면역 중인가. 판 시작 직후 잠깐이다."""
        p = self.players.get(pid)
        if p is None or p.kind == "bot":
            return False
        return self.tick_count < C.SPAWN_IMMUNITY_TICKS

    def can_attack(self, pid: int, target: int | None) -> bool:
        """`canAttackPlayer` — **사람 공격자만 면역을 존중한다.**

        봇·Nation 은 면역 중인 상대도 친다(원본 주석: "Only human attackers respect
        PVP immunity"). 이 비대칭을 빼면 초반이 원본과 달라진다."""
        if target is None:
            return True
        if self.diplomacy.is_friendly(pid, target):
            return False
        me = self.players.get(pid)
        if me is not None and me.kind == "human":
            return not self.is_immune(target)
        return True

    def launch_attack(self, pid: int, target: int | None) -> Attack | None:
        p = self.players.get(pid)
        if p is None or not p.alive or self.over:
            return None
        if not self.can_attack(pid, target):
            return None                 # 친한 상대·면역 중인 상대는 못 친다
        troops = p.attack_troops()
        if troops < C.ATTACK_MIN_TROOPS:
            return None
        atk = Attack.launch(self.gmap, pid, target, troops, self.rng, self.tick_count)
        if atk is None:
            return None
        p.troops -= troops
        p.attacks_sent += 1
        self.attacks.append(atk)
        if target is not None:
            self.emit(EventKind.ATTACK_REQUEST, who=target, other=pid, amount=troops)
            # 맞은 쪽만 나빠진다(`AttackExecution` 도 target 쪽만 갱신한다).
            self.relate(target, pid, C.REL_ATTACKED.get(self.difficulty, -70.0))
            if self.diplomacy.is_friendly(pid, target):
                self.relate(pid, target, C.REL_ATTACKED_ALLY)
        return atk

    # --- 이벤트 -----------------------------------------------------------

    def relate(self, who: int, about: int, delta: float) -> None:
        """`who` 가 `about` 을 보는 눈을 바꾼다. **한 방향이다.**

        양쪽을 다 바꿔야 하는 것은 동맹 성사·MIRV 둘뿐이고, 나머지는 당한 쪽만
        나빠진다 — 친 쪽은 자기가 뭘 했는지 신경 쓰지 않는다."""
        p = self.players.get(who)
        if p is not None and who != about:
            p.relations.update(about, delta)

    def relation_of(self, who: int, about: int) -> Relation:
        p = self.players.get(who)
        return Relation.NEUTRAL if p is None else p.relations.of(about)

    def emit(self, kind: EventKind, who: int | None = None, other: int | None = None,
             tile: TileRef | None = None, amount: float = 0.0, text: str = "") -> None:
        """무슨 일이 일어났는지 남긴다. `who` 는 **이걸 봐야 하는 사람**이다."""
        self.log.add(Event(kind=kind, tick=self.tick_count, who=who, other=other,
                           tile=tile, amount=amount, text=text))

    # --- 외교 -------------------------------------------------------------

    def request_alliance(self, pid: int, other: int) -> bool:
        ok = self.diplomacy.request(pid, other)
        if ok:
            self.emit(EventKind.ALLIANCE_REQUEST, who=other, other=pid)
        return ok

    def accept_alliance(self, pid: int, requestor: int) -> bool:
        ok = self.diplomacy.accept(pid, requestor, self.tick_count) is not None
        if ok:
            self.emit(EventKind.ALLIANCE_ACCEPTED, who=requestor, other=pid)
            self.emit(EventKind.ALLIANCE_ACCEPTED, who=pid, other=requestor)
            self.relate(requestor, pid, C.REL_ALLIANCE_ACCEPTED)
            self.relate(pid, requestor, C.REL_ALLIANCE_ACCEPTED)
        return ok

    def reject_alliance(self, pid: int, requestor: int) -> None:
        self.diplomacy.reject(pid, requestor)
        self.emit(EventKind.ALLIANCE_REJECTED, who=requestor, other=pid)

    def break_alliance(self, pid: int, other: int) -> bool:
        """동맹 파기. 상대가 이미 배신자가 아니면 **내가** 배신자가 된다."""
        ok = self.diplomacy.break_alliance(pid, other, self.tick_count)
        if ok:
            self.emit(EventKind.ALLIANCE_BROKEN, who=other, other=pid)
            self.relate(other, pid, C.REL_ALLIANCE_BROKEN)
            # 이웃도 배신을 본다. 배신자가 조용히 다음 상대를 찾지 못하게 하는 장치다.
            #
            # ⚠ 원본 필터는 "피해자 제외"가 아니라 **피해자와 같은 팀이 아닌 이웃**
            # (`!n.isOnSameTeam(recipient)`)이다. 피해자 본인이 이웃이면 −100 위에
            # −40 이 더 얹힌다. clamp 는 update 마다 걸리므로 −140 이 아니라
            # (동맹으로 얻은 +100) −100 −40 = −40 이 된다.
            for n in self.border_targets(pid):
                if n is not None and not self.diplomacy.same_team(n, other):
                    self.relate(n, pid, C.REL_ALLIANCE_BROKEN_NEIGHBOUR)
        return ok

    def is_traitor(self, pid: int) -> bool:
        return self.diplomacy.is_traitor(pid, self.tick_count)

    def embargo_all_targets(self, pid: int) -> list[int]:
        """전체 금수가 실제로 걸리는 상대들 — 원본 `EmbargoAllExecution` 의 필터.

        **봇은 뺀다.** 봇과의 무역은 관계를 타지 않아 끊어 봐야 내 무역선만 줄고,
        지도에 봇이 400개라 넣으면 사실상 무역 자체를 끄는 버튼이 된다."""
        return [q.pid for q in self.alive
                if q.pid != pid and not q.is_bot]

    def can_embargo_all(self, pid: int) -> bool:
        """`canEmbargoAll()` — 쿨다운 10초 + **걸 상대가 하나라도 있어야** 한다."""
        p = self.players.get(pid)
        if p is None or not p.alive or self.over:
            return False
        if self.tick_count - p.last_embargo_all_tick < C.EMBARGO_ALL_COOLDOWN_TICKS:
            return False
        return bool(self.embargo_all_targets(pid))

    def embargo_all(self, pid: int, start: bool = True) -> int:
        """봇을 뺀 모두에게 금수를 걸거나(`start`) 푼다. 바꾼 수를 돌려준다.

        원본은 이미 걸린 상대를 다시 걸지 않는다 — 다시 걸면 관계 −20 이 두 번
        붙어서 버튼 한 번이 관계를 −40 만큼 태운다."""
        if not self.can_embargo_all(pid):
            return 0
        me, dip = self.players[pid], self.diplomacy
        changed = 0
        for other in self.embargo_all_targets(pid):
            if start and not dip.embargoed(pid, other):
                dip.start_embargo(pid, other)
                changed += 1
            elif not start and dip.embargoed(pid, other):
                dip.stop_embargo(pid, other)
                changed += 1
        me.last_embargo_all_tick = self.tick_count
        return changed

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
        if not self.can_attack(pid, target):
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
        if target is not None:
            self.emit(EventKind.NAVAL_INVASION_INBOUND, who=target, other=pid,
                      tile=dst, amount=troops)
        return boat

    def order_boat_retreat(self, pid: int, boat: TransportShip) -> bool:
        """떠 있는 상륙 부대를 돌린다 — 원본 `BoatRetreatExecution`.

        육상 퇴각과 달리 **지연이 없다**(`RetreatExecution.cancelDelay` 에 해당하는
        것이 없다). 대신 되돌아오는 길 자체가 시간이고, 도착하면 25% 를 잃는다."""
        if boat.owner != pid or boat.retreating or boat not in self.boats:
            return False
        boat.retreating = True
        return True

    def _replan_retreat(self, b: TransportShip) -> bool:
        """지금 있는 자리에서 가장 가까운 내 해안으로 뱃머리를 돌린다.

        원본 `retreatDst ??= bestTransportShipSpawn(boat.tile())` — **퇴각을 시작한
        위치** 기준으로 한 번만 정한다. 돌아갈 해안이 없으면(그 사이 영토를 다 잃는
        따위) 원본은 배를 지우고 병력을 **손실 없이** 돌려준다."""
        dst = best_spawn(self.gmap, b.owner, b.tile)
        path = None if dst is None else self._water_path(b.tile, dst)
        if dst is None or path is None:
            return False
        b.dst, b.path, b.step_i, b.replanned = dst, path, 0, True
        return True

    def _advance_boats(self) -> None:
        still: list[TransportShip] = []
        for b in self.boats:
            p = self.players.get(b.owner)
            if p is None or not p.alive:
                continue
            if b.retreating and not b.replanned and not self._replan_retreat(b):
                p.troops += b.troops       # 돌아갈 해안이 없다 — 손실 없이 환원
                continue
            # 상륙 지점이 그 사이 친해졌으면 되돌아온다 — 육상 공격과 같은 규칙이다.
            if (not b.retreating and b.target is not None
                    and self.diplomacy.is_friendly(b.owner, b.target)):
                p.troops += b.troops
                continue
            b.advance()
            if not b.arrived:
                still.append(b)
                continue

            dst = b.dst
            owner_now = int(self.gmap.owner[dst])
            if owner_now == b.owner:
                # **내 땅에 닿으면 25% 를 잃는다**(`malusForRetreat = 25`). 퇴각만이
                # 아니라 목적지가 그 사이 내 땅이 된 경우에도 원본은 같은 값을 뗀다 —
                # 배에 태운 병력은 공짜로 돌아오지 않는다.
                lost = b.troops * C.BOAT_RETREAT_MALUS_PCT
                p.troops += b.troops - lost
                if lost:
                    self.emit(EventKind.ATTACK_CANCELLED, who=b.owner, amount=lost)
                continue
            self._conquer_tile(b.owner, dst, owner_now)
            # 상륙에 성공하면 **그 자리에서 육상 공격이 시작된다**(원본도 여기서
            # AttackExecution 을 새로 만든다). 배가 육지를 계속 먹는 게 아니다.
            p.attacks_sent += 1
            atk = Attack.launch(self.gmap, b.owner, b.target, b.troops,
                                self.rng, self.tick_count, source_tile=dst)
            if atk is None:
                p.troops += b.troops
            else:
                self.attacks.append(atk)
        for b in self.boats:
            if b not in still:
                b.active = False      # 도착했거나 퇴각이 끝났다(격침이 아니다)
        self.boats = still

    def _conquer_tile(self, pid: int, tile: TileRef, previous: int) -> None:
        self.gmap.owner[tile] = pid
        self._counts[pid] = self._counts.get(pid, 0) + 1
        if previous >= 0:
            self._counts[previous] = max(0, self._counts.get(previous, 0) - 1)

    def _advance_trade(self) -> None:
        """항구마다 따로 스폰을 굴린다. 도착하면 **양쪽 항구 주인이 함께** 번다.

        ⚠ 이식 누락 열아홉 — 전에는 이걸 **판 전체에서 매 tick 한 번** 굴렸다.
        원본은 `PortExecution` 이 항구마다 붙어 10 tick 마다 **레벨 횟수만큼**
        굴리고, 거절 카운터도 항구마다 따로 쌓는다. 자세한 것은 `naval.trading_ports`.
        """
        ports = [(u.tile, p.pid, u.level, u) for p in self.alive
                 for u in p.units.of(UnitType.PORT) if not u.under_construction]
        if len(ports) >= 2:
            n_ships = len(self.trade_ships)
            for tile, pid, level, unit in ports:
                if not port_check_due(unit.check_offset, self.tick_count):
                    continue
                # `shouldSpawnTradeShip()` — **레벨만큼** 굴린다. 실패할 때마다
                # 그 항구의 pity 가 올라가고, 성공하면 그 항구만 0으로 돌아간다.
                for _ in range(level):
                    rate = trade_spawn_rate(unit.spawn_rejections, n_ships)
                    if self.rng.randrange(max(1, rate)) == 0:
                        unit.spawn_rejections = 0
                        if self._spawn_trade_ship(tile, pid, ports):
                            n_ships += 1
                        break
                    unit.spawn_rejections += 1

        still: list[TradeShip] = []
        for t in self.trade_ships:
            src_p = self.players.get(t.owner)
            dst_p = self.players.get(t.dst_owner)
            if src_p is None or not src_p.alive or dst_p is None or not dst_p.alive:
                continue
            # 나포된 배는 금수와 무관하다 — 해적이 자기 항구로 끌고 가는 것이라
            # 원래 두 나라의 관계가 항로를 끊지 않는다.
            if t.captured_by is None and not self._can_trade(t.owner, t.dst_owner):
                continue                       # 금수 중이면 항로가 끊긴다
            t.advance()
            # 해안선 물 칸을 밟으면 20 tick 동안 나포당하지 않는다.
            if self.gmap.is_ocean(t.tile) and self.gmap.is_shoreline(t.tile):
                t.last_safe_tick = self.tick_count
            if not t.arrived:
                still.append(t)
                continue
            gold = trade_gold(len(t.path))
            if t.captured_by is not None:
                # `wasCaptured` — **나포한 쪽이 전액**을 번다. 원래 주인은 0이다.
                pirate = self.players.get(t.captured_by)
                if pirate is not None and pirate.alive:
                    pirate.gold += gold
                    self.emit(EventKind.GOLD_FROM_CAPTURED_SHIP,
                              who=pirate.pid, other=t.owner,
                              tile=t.dst_port, text="나포한 무역선")
            else:
                src_p.gold += gold
                dst_p.gold += gold
        self.trade_ships = still

    def _capture_trade_ship(self, t: TradeShip, pid: int) -> bool:
        """`captureUnit()` + `TradeShipExecution` 의 나포 뒤 항로 갱신.

        나포하면 배는 **해적의 가장 가까운 도달 가능 항구**로 뱃머리를 돌린다.
        갈 수 있는 항구가 없으면 원본은 배를 지운다 — 골드도 사라진다."""
        pirate = self.players.get(pid)
        if pirate is None or not pirate.alive:
            return False
        here = t.tile
        best: TileRef | None = None
        best_d = None
        for u in pirate.units.of(UnitType.PORT):
            if u.under_construction or u.marked_for_deletion:
                continue
            d = manhattan(self.gmap, here, u.tile)
            if best_d is None or d < best_d:
                best, best_d = u.tile, d
        if best is None:
            return False
        path = self._water_path(here, best)
        if path is None:
            return False
        t.captured_by = pid
        t.dst_port, t.dst_owner = best, pid
        t.path, t.step_i = path, 0
        return True

    def _water_path(self, src: TileRef, dst: TileRef) -> "list[TileRef] | None":
        # ⚠ 캐시도 **결과를 안 바꾼다**(성능 전용). 지형이 안 변하므로 같은 쌍은
        # 늘 같은 경로다. 지우는 변이가 살아남는 것이 정상이다.
        key = (src, dst)
        if key not in self._path_cache:
            self._path_cache[key] = water_path(self.gmap, src, dst)
        return self._path_cache[key]

    def _can_trade(self, a: int, b: int) -> bool:
        return (a != b
                and not self.diplomacy.embargoed(a, b)
                and not self.diplomacy.embargoed(b, a))

    def _spawn_trade_ship(self, src: TileRef, owner: int,
                          ports: list[tuple[TileRef, int, int, Unit]]) -> bool:
        """`tradingPorts()` 로 만든 **확률 목록**에서 목적지를 하나 고른다.

        균등 무작위가 아니다 — 레벨·거리·동맹이 가중치로 붙는다. 균등으로 두면
        레벨이 아무 일도 안 하고, 시그모이드가 크게 깎는 300 미만 왕복이 오히려
        자주 뽑힌다."""
        # `canTrade()` — 금수는 **양방향**이다. 어느 한쪽이 걸어도 항로가 끊긴다.
        cands = [(t, pid, lvl) for t, pid, lvl, _ in ports
                 if pid != owner and self._can_trade(owner, pid)]
        if not cands:
            return False
        friendly = {pid for _, pid, _ in cands
                    if self.diplomacy.is_friendly(owner, pid)}
        weighted = trading_ports(self.gmap, src, cands, friendly)
        if not weighted:
            return False
        dst, dst_owner = weighted[self.rng.randrange(len(weighted))]
        path = self._water_path(src, dst)
        if path is None:
            return False
        self.trade_ships.append(TradeShip(owner=owner, src_port=src,
                                          dst_port=dst, dst_owner=dst_owner,
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
            health_before = w.health
            self._heal_warship(w, p)
            if w.cooldown > 0:
                w.cooldown -= 1
                alive_ships.append(w)
                continue

            # ⚠ 후퇴 판정은 **회복 전 체력**으로 한다. 회복 뒤 값으로 보면 항구
            # 옆에서 tick 당 1씩 차오르는 배가 문턱을 오르내리며 후퇴를 껐다 켰다
            # 한다(원본이 `healthBeforeHealing` 을 따로 넘기는 이유다).
            if self._handle_retreat(w, p, health_before):
                alive_ships.append(w)
                continue

            target = self._pick_naval_target(w, r2)
            if isinstance(target, TradeShip):
                # `huntDownTradeShip` — 무역선은 **쏘는 게 아니라 쫓아가 잡는다.**
                # 포격 쿨다운을 쓰지 않는다(원본도 이 분기에서 안 쓴다).
                self._hunt_trade_ship(w, target)
            else:
                if target is not None:
                    w.cooldown = C.WARSHIP_SHELL_ATTACK_RATE
                    self._fire_shell(w, target)
                # ⚠ 원본은 **쏘고 나서도 순찰한다**(`shootTarget(); patrol();`).
                # 무역선 추격만 순찰을 건너뛴다 — 그쪽은 이미 목표로 움직인다.
                self._patrol(w)
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
        # 무역선은 관문이 더 있다(`findBestTarget` 의 `includeTradeShips` 분기).
        # 나포는 격침과 달리 **끌고 갈 곳이 있어야** 성립하기 때문이다.
        if self._has_reachable_port(w):
            patrol_r2 = C.WARSHIP_PATROL_RANGE ** 2
            for t in self.trade_ships:
                if not hostile(t.owner):
                    continue
                if t.safe_from_pirates(self.tick_count):
                    continue          # 해안선을 막 밟았다 — 20 tick 은 못 건드린다
                # 목적지가 나거나 내 동맹이면 안 건드린다. 어차피 내가 벌 배다.
                if t.dst_owner == w.owner or self.diplomacy.is_friendly(w.owner,
                                                                       t.dst_owner):
                    continue
                if self._dist_sq(w.patrol_origin, t.tile) > patrol_r2:
                    continue          # 순찰 구역 밖까지 쫓아가지는 않는다
                if self._dist_sq(w.tile, t.tile) <= r2:
                    return t
        return None

    def _handle_retreat(self, w: Warship, p: PlayerState, health_before: int) -> bool:
        """`shouldStartRepairRetreat` + `handleRepairRetreat`.

        체력이 최대의 75% 아래로 떨어지면 가장 가까운 항구로 돌아가 정박한다.
        정박 중에는 항구 레벨 × 5 를 그 항구의 배들이 나눠 갖는다 — 레벨이 곧
        수리 능력이고, 한 항구에 몰리면 각자 느려진다.

        True 를 돌려주면 이 tick 은 후퇴가 가져간다(교전도 순찰도 안 한다).
        ⚠ 다만 원본은 후퇴 중에도 **수송선·전함이 붙으면 쏜다**(`findRetreatAggroTarget`).
        """
        if w.retreat_port is None:
            # 클락에 표시된 쪽은 애초에 수리가 안 된다 — 돌아가 봐야 헛걸음이다.
            if w.owner in self.clock.marked_at:
                return False
            threshold = (C.WARSHIP_MAX_HEALTH * C.WARSHIP_RETREAT_HEALTH_PERCENT) // 100
            if health_before >= threshold:
                return False
            w.retreat_port = self._nearest_port_tile(w, p)
        elif not any(u.tile == w.retreat_port and not u.under_construction
                     for u in p.units.of(UnitType.PORT)):
            w.retreat_port = self._nearest_port_tile(w, p)   # 항구가 사라졌다
            w.docked = False

        # ⚠ None 검사는 **한 곳에만** 둔다. 위 두 갈래에 각각 두면 한쪽을 지워도
        # 다른 쪽이 가려 줘서 변이가 살아남는다(실제로 살아남았다).
        # 갈 곳이 없으면 후퇴하지 않는다 — 순찰을 멈추면 그냥 표적이 된다.
        if w.retreat_port is None:
            self._cancel_retreat(w)
            return False

        # 후퇴 중에도 붙는 적은 쏜다(무역선은 제외 — 그건 추격이라 후퇴와 겹친다)
        aggro = self._pick_retreat_aggro(w, C.WARSHIP_TARGETTING_RANGE ** 2)
        if aggro is not None:
            w.cooldown = C.WARSHIP_SHELL_ATTACK_RATE
            self._fire_shell(w, aggro)

        if self._dist_sq(w.tile, w.retreat_port) <= C.WARSHIP_DOCKING_RANGE ** 2:
            port = next(u for u in p.units.of(UnitType.PORT)
                        if u.tile == w.retreat_port)
            docked_here = [o for o in self.warships
                           if o is not w and o.docked and not o.sunk
                           and o.retreat_port == w.retreat_port]
            if w.docked or len(docked_here) < port.level:
                w.docked = True
                self._apply_docked_healing(w, port, len(docked_here) + 1)
            elif w.health >= C.WARSHIP_MAX_HEALTH:
                self._cancel_retreat(w)
                return False
            # 자리가 없으면 항구 옆에서 기다린다(수동 회복은 계속 받는다)
        else:
            step = self._step_toward(w.tile, w.retreat_port)
            if step is None:
                self._cancel_retreat(w)
                return False
            w.tile = step

        if w.health >= C.WARSHIP_MAX_HEALTH:
            self._cancel_retreat(w)
        return True

    def _cancel_retreat(self, w: Warship) -> None:
        w.retreat_port, w.docked, w.heal_remainder = None, False, 0.0

    def _nearest_port_tile(self, w: Warship, p: PlayerState) -> "TileRef | None":
        comp = _touching_components(self.gmap, w.tile)
        best, best_d = None, None
        for u in p.units.of(UnitType.PORT):
            if u.under_construction or u.marked_for_deletion:
                continue
            if comp and not (comp & _touching_components(self.gmap, u.tile)):
                continue          # 수로가 안 이어져 있으면 갈 수 없다
            d = self._dist_sq(w.tile, u.tile)
            if best_d is None or d < best_d:
                best, best_d = u.tile, d
        return best

    def _pick_retreat_aggro(self, w: Warship, r2: int):
        """`findRetreatAggroTarget` — 후퇴 중에는 **무역선을 안 쫓는다.**
        나포는 항구 반대 방향으로 끌려갈 수 있어 후퇴와 겹친다."""
        def hostile(pid: int) -> bool:
            return pid != w.owner and not self.diplomacy.is_friendly(w.owner, pid)
        for b in self.boats:
            if hostile(b.owner) and self._dist_sq(w.tile, b.tile) <= r2:
                return b
        for o in self.warships:
            if (o is not w and not o.sunk and hostile(o.owner)
                    and self._dist_sq(w.tile, o.tile) <= r2):
                return o
        return None

    def _apply_docked_healing(self, w: Warship, port: Unit, n_docked: int) -> None:
        """`applyActiveDockedHealing` — 레벨 × 5 를 정박한 배들이 **나눠 갖는다**.

        ⚠ 나머지를 들고 가야 한다. 세 척이면 5/3 = 1.67 인데 매 tick 1 로 자르면
        회복량이 조용히 20% 줄어든다(원본이 `activeHealingRemainder` 를 두는 이유)."""
        pool = port.level * C.WARSHIP_PORT_HEALING_PER_LEVEL
        if pool <= 0 or n_docked <= 0:
            return
        w.heal_remainder += pool / n_docked
        gain = int(w.heal_remainder)
        if gain <= 0:
            return
        w.heal_remainder -= gain
        w.health = min(C.WARSHIP_MAX_HEALTH, w.health + gain)

    def _patrol(self, w: Warship) -> None:
        """`patrol()` — 순찰 지점을 하나 잡고 그쪽으로 한 칸 간다. 닿으면 새로 뽑는다.

        ⚠ 이식 누락 스물둘. 이게 없어서 전함이 태어난 자리에 붙박여 있었다.
        붙박이면 순찰 반경(100)이 사거리(130)보다 작다는 규칙이 아무 의미가 없고,
        바다가 통째로 비어 있어도 아무도 그리로 가지 않는다."""
        if w.patrol_target is None:
            w.patrol_target = self._random_patrol_tile(w)
            if w.patrol_target is None:
                return
        # 닿았거나(더 가까운 이웃이 없다) 길이 막혔으면 목표를 비운다.
        # ⚠ 도착 검사를 따로 두지 않는다 — `_step_toward` 가 목표 칸에서 None 을
        # 돌려주므로 같은 조건이고, 두 벌로 두면 한쪽을 지워도 다른 쪽이 가려 준다.
        step = self._step_toward(w.tile, w.patrol_target)
        if step is None:
            w.patrol_target = None
            return
        w.tile = step

    def _random_patrol_tile(self, w: Warship) -> "TileRef | None":
        """`randomTile()` — 순찰 기점 주변 **반경의 절반** 안에서 바다 칸 하나.

        해안선은 피한다(원본 `allowShoreline=false` 가 기본). 500번 실패할 때마다
        반경을 1.5배로 넓히고, 세 번까지 넓힌다 — 작은 만에 갇힌 배가 영원히
        후보를 못 찾는 것을 막는 장치다. 그래도 못 찾으면 해안선을 허용해 한 번 더.
        """
        gm = self.gmap
        comp = _touching_components(gm, w.tile) or None
        origin = w.patrol_origin if w.patrol_origin is not None else w.tile
        ox, oy = origin % gm.width, origin // gm.width
        for allow_shore in (False, True):
            rng_r = C.WARSHIP_PATROL_RANGE
            attempts = expands = 0
            while expands < C.PATROL_MAX_EXPANDS:
                half = max(1, rng_r // 2)
                x = ox + self.rng.randint(-half, half)
                y = oy + self.rng.randint(-half, half)
                if not (0 <= x < gm.width and 0 <= y < gm.height):
                    continue
                tile = gm.ref(x, y)
                bad = (gm.terrain[tile] != Terrain.OCEAN
                       or (not allow_shore and gm.is_shoreline(tile))
                       or (comp is not None
                           and not (comp & _touching_components(gm, tile))))
                if bad:
                    attempts += 1
                    if attempts == C.PATROL_ATTEMPTS_BEFORE_EXPAND:
                        expands += 1
                        attempts = 0
                        rng_r += rng_r // 2
                    continue
                return tile
        return None

    def _hunt_trade_ship(self, w: Warship, t: TradeShip) -> None:
        """`huntDownTradeShip` — tick 당 **2칸** 다가가고, 맨해튼 5 안이면 나포한다.

        ⚠ 2칸인 것이 규칙이다. 무역선도 1칸/tick 이라 같은 속도면 영원히 못
        따라잡는다 — 추격 자체가 성립하지 않는다."""
        for _ in range(C.PIRACY_HUNT_STEPS):
            if manhattan(self.gmap, w.tile, t.tile) <= C.PIRACY_CAPTURE_RANGE:
                if self._capture_trade_ship(t, w.owner):
                    w.veterancy += 1
                    self.emit(EventKind.TRADE_SHIP_CAPTURED, who=t.owner,
                              other=w.owner, tile=t.tile, text="무역선")
                elif t in self.trade_ships:
                    self.trade_ships.remove(t)   # 끌고 갈 항구가 없으면 원본도 지운다
                return
            step = self._step_toward(w.tile, t.tile)
            if step is None:
                return
            w.tile = step

    def _step_toward(self, src: TileRef, dst: TileRef) -> "TileRef | None":
        """`bestNeighborToward` — 바다 이웃 중 목표에 가장 가까운 칸.

        경로 탐색이 아니라 탐욕이다. 원본도 근접(20 이하)에서는 그렇게 한다 —
        축소 지도 경로가 대각으로 튀어 수렴이 안 되기 때문이다."""
        best, best_d = None, manhattan(self.gmap, src, dst)
        for n in self.gmap.neighbors(src):
            if self.gmap.terrain[n] != Terrain.OCEAN:
                continue
            d = manhattan(self.gmap, n, dst)
            if d < best_d:
                best, best_d = n, d
        return best

    def _has_reachable_port(self, w: Warship) -> bool:
        """`hasReachablePort` — 나포해도 끌고 갈 항구가 없으면 아예 안 노린다."""
        p = self.players.get(w.owner)
        if p is None:
            return False
        return any(not u.under_construction and not u.marked_for_deletion
                   for u in p.units.of(UnitType.PORT))

    def _fire_shell(self, w: Warship, target) -> None:
        dmg = shell_damage(self.rng, w.veterancy)
        if isinstance(target, Warship):
            target.health -= dmg
            if target.sunk:
                w.veterancy += 1
                self.emit(EventKind.UNIT_DESTROYED, who=target.owner, other=w.owner,
                          tile=target.tile, text="전함")
        elif isinstance(target, TransportShip):
            # 수송선은 체력이 없다 — 원본은 포탄 한 방에 격침시킨다
            if target in self.boats:
                # ⚠ 지우기 전에 **누가 격침시켰는지** 남긴다. 봇이 나중에 이걸
                # 보고 보복을 정한다(도착·퇴각과 구분해야 한다).
                target.active = False
                target.sunk_by = w.owner
                self.boats.remove(target)
                w.veterancy += 1
                self.emit(EventKind.UNIT_DESTROYED, who=target.owner, other=w.owner,
                          tile=target.tile, text="수송선")
        # ⚠ 무역선은 여기 안 온다 — **격침이 아니라 나포**라
        # `_hunt_trade_ship` 이 따로 처리한다(이식 누락 스물).

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
        # `PlayerImpl.nukeSpawn` — **재장전 중인 사일로는 쓸 수 없다.**
        # 이게 없으면 사일로 한 기로 골드가 되는 한 무한 연사가 된다.
        silos = [u for u in p.units.of(UnitType.MISSILE_SILO)
                 if not u.under_construction and not u.in_cooldown]
        if not silos:
            return None
        cost = self.nuke_cost(pid, utype)
        if p.gold < cost:
            return None
        p.gold -= cost
        p.units.record_constructed(utype)
        if utype is UnitType.MIRV:
            self.mirvs_launched += 1
        silo = min(silos, key=lambda u: self._dist_sq(u.tile, dst))
        silo.fire(self.tick_count)          # `silo.launch()` — 관 하나가 막힌다
        src = silo.tile
        n = Nuke(owner=pid, utype=utype, src=src, dst=dst)
        self.nukes.append(n)
        kind = {UnitType.HYDROGEN_BOMB: EventKind.HYDROGEN_BOMB_INBOUND,
                UnitType.MIRV: EventKind.MIRV_INBOUND}.get(
                    utype, EventKind.NUKE_INBOUND)
        victim = int(self.gmap.owner[dst])
        self.emit(kind, who=victim if victim >= 0 else None, other=pid, tile=dst)
        if victim >= 0 and victim != pid:
            if utype is UnitType.MIRV:
                # MIRV 만 **양방향**이다 — 쏜 쪽도 상대를 적으로 확정한다.
                self.relate(victim, pid, C.REL_MIRV)
                self.relate(pid, victim, C.REL_MIRV)
            else:
                self.relate(victim, pid, C.REL_NUKED)
            self.ai_emoji(pid, victim, emoji_mod.NUKE)
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

    def _reload_missiles(self) -> None:
        """`MissileSiloExecution` · `SAMLauncherExecution` 의 재장전 부분.

        ⚠ **둘의 처리 횟수가 다르다.** 사일로는 tick 당 맨 앞 관 **하나만**
        비우고(원본이 `if`), SAM 은 끝난 것을 **전부** 비운다(원본이 `while`).
        사일로를 `while` 로 바꾸면 한 tick 에 관이 여러 개 열려 연사 간격이 줄어든다 —
        같은 상수(90 tick)를 쓴다고 같은 코드로 합치면 안 되는 자리다."""
        now = self.tick_count
        for p in self.alive:
            for silo in p.units.of(UnitType.MISSILE_SILO):
                if not silo.under_construction:
                    silo.reload_front(now, C.SILO_COOLDOWN_TICKS)
            for sam in p.units.of(UnitType.SAM_LAUNCHER):
                if not sam.under_construction:
                    sam.reload_ready(now, C.SAM_COOLDOWN_TICKS)

    def ready_missiles(self, pid: int) -> int:
        """`readyMissileCount()` — 지금 쏠 수 있는 미사일 수(사일로 관의 합).

        핵을 한 번에 여러 발 사는 상한이 이 값이다."""
        p = self.players.get(pid)
        if p is None:
            return 0
        return sum(u.ready_tubes for u in p.units.of(UnitType.MISSILE_SILO))

    def _sam_intercepts(self, n: Nuke) -> bool:
        """SAM 은 **자기 것이 아닌** 핵만 요격한다. 사거리는 레벨에 따라 70~150.

        ⚠ 관문이 둘 앞에 붙는다(§5.49). **MIRV 본체는 아예 못 노리고**, 노릴 수
        있는 종류라도 **발사점이나 표적에서 150 안**에 있어야 한다. 둘 다 없어서
        우리 SAM 은 지나가는 모든 핵을 경로 어디서든 떨구고 있었다."""
        if n.utype not in SAM_TARGETABLE_TYPES:
            return False
        here = n.tile(self.gmap)
        if not is_targetable(self.gmap, n.src, n.dst, here):
            return False
        for p in self.alive:
            if p.pid == n.owner or self.diplomacy.is_friendly(p.pid, n.owner):
                continue
            for sam in p.units.of(UnitType.SAM_LAUNCHER):
                # **관이 전부 차 있으면 못 쏜다**(`SAMLauncherExecution` 이
                # `isInCooldown()` 이면 그 tick 을 통째로 건너뛴다). 이게 없어서
                # SAM 한 기가 판의 모든 핵을 영원히 100% 막고 있었다.
                if sam.under_construction or sam.in_cooldown:
                    continue
                r = sam_range(sam.level)
                if self._dist_sq(sam.tile, here) <= r * r:
                    sam.fire(self.tick_count)
                    self.emit(EventKind.SAM_HIT, who=p.pid, other=n.owner, tile=here)
                    self.emit(EventKind.SAM_MISS, who=n.owner, other=p.pid, tile=here)
                    return True
        return False

    def _detonate(self, n: Nuke) -> None:
        tiles = blast_tiles(self.gmap, n.dst, n.utype, self.rng)
        if not tiles:
            return
        self.emit(EventKind.NUKE_DETONATED, other=n.owner, tile=n.dst,
                  amount=len(tiles))
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
        for b in self.boats:
            if self._dist_sq(n.dst, b.tile) < outer2:
                # 핵에 날아간 것도 격침이다. **누가 쐈는지**를 남긴다 —
                # 원본도 `delete(true, destroyer)` 로 같은 자리에 기록한다.
                b.active, b.sunk_by = False, n.owner
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

    def _apply_embargo_relations(self) -> None:
        """금수는 **걸려 있는 동안 계속** 깎는 것이 아니라 한 번만 깎는다.

        매 tick 깎으면 몇 초 만에 −100 에 박혀 풀어도 회복이 안 된다. 원본은
        적용 여부를 따로 기억해 두고(`embargoMalusApplied`) 상태가 바뀔 때만
        움직인다. 푸는 것도 같은 크기로 되돌린다."""
        # ⚠ 전에는 생존자 **전 쌍**을 돌았다(472명이면 tick 당 222,784쌍).
        # 프로파일에서 `embargoed` 가 1,500 tick 에 **9,878만 번** 불려 판 전체의
        # 18% 를 먹고 있었다. 금수는 몇 건 안 되므로, **걸린 것만** 돌면 된다.
        # `embargoes` 는 "내가 막은 대상"이라 역방향을 여기서 만든다.
        alive = {p.pid for p in self.alive}
        against: dict[int, set[int]] = {}
        for by, targets in self.diplomacy.embargoes.items():
            if by not in alive:
                continue
            for t in targets:
                if t in alive:
                    against.setdefault(t, set()).add(by)

        # 새로 걸린 것과 풀린 것만 움직인다. 둘의 합집합만 보면 되므로
        # 살아 있는 사람 수가 아니라 **금수 건수**에 비례한다.
        for pid in alive | set(self._embargo_malus):
            if pid not in alive:
                continue
            applied = self._embargo_malus.setdefault(pid, set())
            now = against.get(pid, frozenset())
            for other in now - applied:
                self.relate(pid, other, C.REL_EMBARGO)
                applied.add(other)
            for other in applied - now:
                self.relate(pid, other, -C.REL_EMBARGO)
                applied.discard(other)

    # --- 스폰 -------------------------------------------------------------

    def end_spawn_phase(self) -> None:
        """페이즈를 끝내고 시계를 0 으로 돌린다.

        ⚠ **면역이 여기서부터 세어진다.** 안 그러면 고르는 데 쓴 시간이 면역에서
        깎여, 늦게 고른 사람일수록 보호를 덜 받는다(원본은 `spawnPhaseTurns +
        immunityDuration` 으로 더해서 같은 문제를 피한다).
        """
        if not self.spawn_phase:
            return
        self.spawn_phase = False
        self.tick_count = 0

    def choose_spawn(self, pid: int, centre: TileRef) -> bool:
        """시작 위치를 고른다. **페이즈 동안은 몇 번이든 옮길 수 있다.**

        원본 `SpawnExecution` 은 옮길 때 이전 칸을 전부 반납한다(`relinquish`).
        반납을 빼면 옮긴 자리마다 영토가 쌓여 고르는 것만으로 이길 수 있다.
        """
        if not self.spawn_phase:
            return False
        p = self.players.get(pid)
        if p is None:
            return False
        tiles = spawn_tiles(self.gmap, centre, require_all_valid=False)
        # 남의 땅·바다를 뺀 나머지가 너무 적으면 시작점으로 못 쓴다.
        if not tiles or len(tiles) < C.SPAWN_MIN_TILES:
            return False

        gm = self.gmap
        old = gm.owned_refs(pid)
        if len(old):
            gm.owner[old] = -1
        for t in tiles:
            gm.owner[t] = pid
        self._counts[pid] = len(tiles)
        p.start = centre
        return True

    # --- 퇴각 -------------------------------------------------------------

    def order_retreat(self, pid: int, attack: Attack) -> bool:
        """진행 중인 공격을 물린다. **2초 뒤에 실제로 물러난다.**

        원본은 명령과 실행을 나눈다(`RetreatExecution` 의 `cancelDelay = 20`).
        즉시 물리면 되돌릴 수 없는 클릭 한 번으로 부대가 증발한다.
        """
        if attack.attacker != pid or attack not in self.attacks:
            return False
        if attack.retreat_ordered_at is not None:
            return False
        attack.retreat_ordered_at = self.tick_count
        return True

    def my_attacks(self, pid: int) -> list[Attack]:
        return [a for a in self.attacks if a.attacker == pid]

    # --- 표적 -------------------------------------------------------------

    def can_target(self, pid: int, other: int) -> bool:
        """`canTarget` — 친한 상대는 못 찍고, 15초에 한 번만 찍을 수 있다."""
        if pid == other or other not in self.players:
            return False
        if self.diplomacy.is_friendly(pid, other):
            return False
        for (a, _b), tick in self.targets.items():
            if a == pid and self.tick_count - tick < C.TARGET_COOLDOWN_TICKS:
                return False
        return True

    def target_player(self, pid: int, other: int) -> bool:
        """동맹에게 "저놈을 쳐 달라"고 찍는다. 찍힌 쪽은 나를 −40 으로 본다."""
        if not self.can_target(pid, other):
            return False
        self.targets[(pid, other)] = self.tick_count
        self.relate(other, pid, C.REL_TARGETED)
        return True

    def targets_of(self, pid: int) -> list[int]:
        """`targets()` — 아직 살아 있는 부탁만. 10초가 지나면 잊는다."""
        return [b for (a, b), tick in self.targets.items()
                if a == pid
                and self.tick_count - tick < C.TARGET_DURATION_TICKS
                and b in self.players and self.players[b].alive]

    def _expire_targets(self) -> None:
        cut = self.tick_count - C.TARGET_DURATION_TICKS
        # 쿨다운이 지속시간보다 길어서(15초 vs 10초) **바로 지우면 안 된다** —
        # `can_target` 이 쿨다운을 재는 데 같은 기록을 쓴다.
        keep = self.tick_count - C.TARGET_COOLDOWN_TICKS
        if cut <= 0:
            return
        self.targets = {k: t for k, t in self.targets.items() if t > keep}

    # --- 이모지 -----------------------------------------------------------

    def send_emoji(self, pid: int, to: int, emoji: str) -> bool:
        """이모지 하나를 보낸다. 원본에서 이건 장식이 아니라 **관계 조작 수단**이다.

        🖕 하나가 −100 이다 — 사람이 AI 의 눈을 바꾸는 방법 중 유일하게 공짜다
        (공격은 병력을, 기부는 골드를 쓴다). 그래서 5초 쿨다운이 붙어 있다.
        """
        me, them = self.players.get(pid), self.players.get(to)
        if me is None or them is None or self.over:
            return False
        if not me.alive or not them.alive:
            return False
        if not self.emojis.can_send(pid, to, self.tick_count):
            return False
        self.emojis.record(pid, to, self.tick_count)
        self.emit(EventKind.CHAT, who=to, other=pid, text=emoji)

        # 받는 쪽이 AI 일 때만 관계가 움직이고 답이 온다. 사람끼리는 그냥 말이다.
        if them.kind != "nation":
            return True
        delta = emoji_relation_delta(emoji, self.difficulty)
        if delta:
            self.relate(to, pid, delta)
        reply = emoji_reply_to(emoji, self.rng, self.relation_of(pid, to))
        if reply is not None and self.emojis.can_send(to, pid, self.tick_count):
            self.emojis.record(to, pid, self.tick_count)
            self.emit(EventKind.CHAT, who=pid, other=to, text=reply)
        return True

    def ai_emoji(self, pid: int, to: int, pool: tuple[str, ...]) -> bool:
        """AI 가 **먼저** 말을 건다.

        `shouldSendEmoji` 의 두 조건을 그대로 지킨다: 봇은 안 보내고, **받는 쪽이
        사람이 아니면 안 보낸다.** AI 끼리 주고받지 않는다는 뜻이라, 화면에 뜨는
        이모지는 전부 나에게 온 말이 된다.
        """
        me, them = self.players.get(pid), self.players.get(to)
        if me is None or them is None or self.over:
            return False
        if me.kind == "bot" or them.kind != "human":
            return False
        if not me.alive or not them.alive:
            return False
        if not self.emojis.ai_may_speak(pid, to, self.tick_count):
            return False
        if not self.emojis.can_send(pid, to, self.tick_count):
            return False
        self.emojis.record(pid, to, self.tick_count)
        self.emit(EventKind.CHAT, who=to, other=pid, text=self.rng.choice(pool))
        return True

    # --- 기부 -------------------------------------------------------------

    def donate_gold(self, pid: int, to: int, gold: int) -> bool:
        a, b = self.players.get(pid), self.players.get(to)
        if a is None or b is None or pid == to or gold <= 0 or a.gold < gold:
            return False
        a.gold -= gold
        b.gold += gold
        self.emit(EventKind.DONATION_SENT, who=pid, other=to, amount=gold)
        self.emit(EventKind.DONATION_RECEIVED, who=to, other=pid, amount=gold)
        # 액수에 비례한다. 덩어리 크기가 시간에 따라 커져서 후반에 관계를 살 수 없다.
        bump = gold_donation_relation(gold, self.tick_count, self.difficulty)
        self.relate(to, pid, bump)
        # **적으면 적다고 말한다.** 답이 없으면 준 사람은 통했는지 알 수 없다.
        them = self.players.get(to)
        if them is not None and them.kind == "nation":
            pool = (emoji_mod.LOVE if bump >= 50 else
                    emoji_mod.DONATION_OK if bump > 0 else
                    emoji_mod.DONATION_TOO_SMALL)
            self.ai_emoji(to, pid, pool)
        return True

    def donate_troops(self, pid: int, to: int, troops: float) -> bool:
        a, b = self.players.get(pid), self.players.get(to)
        if a is None or b is None or pid == to or troops <= 0 or a.troops < troops:
            return False
        a.troops -= troops
        b.troops += troops
        self.emit(EventKind.DONATION_SENT, who=pid, other=to, amount=troops)
        self.emit(EventKind.DONATION_RECEIVED, who=to, other=pid, amount=troops)
        self.relate(to, pid, C.REL_TROOP_DONATION)   # 병력은 액수와 무관하게 +50
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
                    ticks_left=UNIT_INFO[utype].construction_ticks,
                    # `checkOffset = mg.ticks() % 10` — 항구마다 굴리는 tick 을
                    # 어긋나게 둔다. 안 두면 유통량이 10 tick 주기로 뭉친다.
                    check_offset=self.tick_count % C.TRADE_SPAWN_CHECK_PERIOD)
        p.units.units.append(unit)
        # 원본은 `buildUnit()` 안에서, **건설이 끝나기 전에** 완공 카운터를 올린다
        # (PlayerImpl.ts 주석: "already accounts for in-progress builds").
        # 완공 시점으로 미루면 짓는 동안 같은 건물을 원본보다 싸게 연달아 지을 수 있다.
        p.units.record_constructed(utype)
        if not unit.under_construction:
            self._activate(p, unit)
        return unit

    def find_upgrade(self, pid: int, utype: UnitType, near: TileRef) -> Unit | None:
        """`findUnitToUpgrade` — 찍은 칸에서 `structureMinDist`(15) 안의 내 같은 건물.

        **원본의 건설 버튼은 건설/업그레이드 통합 버튼이다.** 같은 종류가 이미 가까이
        있으면 그 버튼이 업그레이드가 되고(`canUpgrade` 가 `canBuild` 보다 우선한다),
        없을 때만 새로 짓는다. 우리는 이 자리에서 "지을 자리가 없다"고 거절하고 있었다 —
        그래서 사람은 도시를 두 채째부터 **아예 못 늘렸다.**"""
        p = self.players.get(pid)
        if p is None or not UNIT_INFO[utype].upgradable:
            return None
        min_sq = C.STRUCTURE_MIN_DIST ** 2
        best, best_d = None, min_sq
        for u in p.units.of(utype):
            d = euclid_sq(self.gmap, u.tile, near)
            if d < best_d and self.can_upgrade(pid, u):
                best, best_d = u, d
        return best

    def can_upgrade(self, pid: int, unit: Unit) -> bool:
        """`canUpgradeUnit()` = 종류가 업그레이드 대상 + 골드 + `isUnitValidToUpgrade`."""
        p = self.players.get(pid)
        if p is None or not p.alive or self.over or self.spawn_phase:
            return False
        if not UNIT_INFO[unit.utype].upgradable:
            return False
        if unit.owner != pid or not unit.active:
            return False
        if unit.under_construction or unit.marked_for_deletion:
            return False
        return p.gold >= p.units.cost(unit.utype)

    def upgrade(self, pid: int, unit: Unit, amount: int = 1) -> int:
        """`UpgradeStructureExecution` — **실제로 오른 레벨 수**를 돌려준다.

        `upgradeUnit()` 은 지금 상태로 값을 매기고 레벨과 완공수를 함께 올린다.
        **레벨이 오르면 `unitsOwned` 도 오른다**(레벨 합이다). 그래서 다음 값이
        250,000 → 500,000 → 1,000,000 으로 뛴다 — 원본을 실행해 대조한 값이다.

        `amount` 는 원본 실행부 그대로 **매 단계 다시 검사하며 반복**한다:

            for (let i = 0; i < this.amount; i++) {
              if (!this.player.canUpgradeUnit(this.structure)) break;
              this.player.upgradeUnit(this.structure);
            }

        즉 값을 미리 합산해 한 번에 빼지 않는다. 골드가 중간에 떨어지면 **거기까지만
        오르고 멈춘다** — 그래서 돌려주는 값이 요청한 수보다 작을 수 있다.
        `units.bulk_cost()` 는 이 결과를 **미리 보여주기 위한 것**이지 결제 경로가 아니다.

        ⚠ 반환이 bool 이 아니라 int 다. 0 이 "하나도 못 올렸다"이고, 예전처럼
        `if st.upgrade(...)` 로 써도 뜻이 같다."""
        done = 0
        p = self.players.get(pid)
        for _ in range(max(0, amount)):
            if not self.can_upgrade(pid, unit):
                break
            p.gold -= p.units.cost(unit.utype)
            unit.level += 1
            # `UnitImpl.increaseLevel` — 사일로·SAM 은 **새 관이 재장전부터
            # 시작한다.** 올리자마자 한 발 더 쏘게 두면 업그레이드가 즉발 화력이
            # 되어 버린다.
            if unit.utype in (UnitType.MISSILE_SILO, UnitType.SAM_LAUNCHER):
                unit.fire(self.tick_count)
            p.units.record_constructed(unit.utype)
            done += 1
        return done

    def max_bulk_upgrade(self, pid: int, unit: Unit) -> int:
        """`maxBulkAmount` — 지금 골드로 살 수 있는 최대 레벨 수(상한 50).

        원본은 미리 만들어 둔 누적표(`upgradeCosts`)를 넘어서면 **선형 가격으로
        조용히 떨어지므로** 표 길이에서 멈춘다. 우리는 `bulk_cost` 가 언제나
        누적으로 계산하지만, 상한은 같은 이유로 `MAX_UPGRADE_AMOUNT` 에 둔다."""
        if not self.can_upgrade(pid, unit):
            return 0
        p = self.players[pid]
        best = 0
        for n in range(1, C.MAX_UPGRADE_AMOUNT + 1):
            if p.units.bulk_cost(unit.utype, n) > p.gold:
                break
            best = n
        return best

    # --- 철거 -------------------------------------------------------------

    def can_delete_unit(self, pid: int, unit: Unit | None = None) -> bool:
        """`DeleteUnitExecution.init()` 의 관문들 + `canDeleteUnit()` 쿨다운.

        원본은 이 검사를 서버에서 다시 하며 실패를 `SECURITY:` 로 찍는다 — 클라이언트
        버튼을 회색으로 만드는 것과 **같은 조건을 두 번** 두고 있다는 뜻이다."""
        p = self.players.get(pid)
        if p is None or not p.alive or self.over or self.spawn_phase:
            return False
        if self.tick_count - p.last_delete_unit_tick < C.DELETE_UNIT_COOLDOWN_TICKS:
            return False
        if unit is None:
            return True
        if unit.owner != pid or not unit.active or unit.marked_for_deletion:
            return False
        # 내 땅 위의, 육지에 있는 것만. 배는 여기로 못 지운다(원본도 막는다).
        return (int(self.gmap.owner[unit.tile]) == pid
                and self.gmap.passable(unit.tile))

    def delete_unit(self, pid: int, unit: Unit) -> bool:
        """철거를 **예약한다.** 30초 뒤에 실제로 사라진다 — 골드 환불은 없다."""
        if not self.can_delete_unit(pid, unit):
            return False
        self.players[pid].last_delete_unit_tick = self.tick_count
        unit.mark_for_deletion(self.tick_count)
        return True

    def _advance_deletions(self) -> None:
        """예약 시간이 지난 것을 실제로 지운다.

        **표시 기간 동안 건물은 그대로 동작한다** — 원본은 방어초소 사거리도, 도시
        레벨도 건드리지 않고 `_deletionAt` 만 세워 둔다. 여기서 조기에 효과를 끄면
        30초 동안 원본과 다른 판이 된다."""
        removed = False
        for p in self.alive:
            gone = [u for u in p.units.units if u.overdue_deletion(self.tick_count)]
            if not gone:
                continue
            for u in gone:
                u.active = False
                self.emit(EventKind.UNIT_DELETED, who=p.pid, tile=u.tile,
                          text=u.utype.value)
            keep = set(map(id, gone))
            p.units.units = [u for u in p.units.units if id(u) not in keep]
            removed = True
        if removed:
            self._rebuild_posts()

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
        if self.spawn_phase:
            # 시간만 흐르고 아무 일도 일어나지 않는다. 상한(`numSpawnPhaseTurns`)을
            # 넘기면 안 고른 사람도 그냥 시작한다 — 원본도 기다려 주지 않는다.
            if self.tick_count >= C.SPAWN_PHASE_TURNS:
                self.end_spawn_phase()
            return
        for gone in self.diplomacy.expire_due(self.tick_count):
            self.emit(EventKind.ALLIANCE_EXPIRED, who=gone.a, other=gone.b)
            self.emit(EventKind.ALLIANCE_EXPIRED, who=gone.b, other=gone.a)
        self._decay_relations()
        self._expire_targets()
        self._apply_embargo_relations()
        self._grow()
        self._advance_construction()
        self._advance_deletions()
        self._reload_missiles()
        self._advance_nukes()
        self._advance_warships()
        self._advance_boats()
        self._advance_trade()
        self._advance_rail()
        self._advance_attacks()
        self._tick_clock()
        self._check_end()

    def _decay_relations(self) -> None:
        """`PlayerExecution.tick` 이 매 tick 부르는 것. 원한은 잊힌다."""
        for p in self.alive:
            p.relations.decay()

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
            if a.retreating:
                # 물러나는 중에는 진격하지 않는다. 시간이 차면 실제로 물린다.
                if (self.tick_count - a.retreat_ordered_at
                        >= C.RETREAT_DELAY_TICKS):
                    a.retreated = True
                else:
                    still.append(a)
                    continue
            elif (a.target is not None
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
                    # 사람을 치던 부대만 25% 를 잃는다(`malusForRetreat`).
                    # 중립 확장은 공짜로 무를 수 있다 — 안 그러면 잘못 찍은
                    # 확장을 취소하는 데 병력을 버려야 한다.
                    lost = (a.troops * C.RETREAT_MALUS
                            if a.target is not None else 0.0)
                    atk.troops += a.troops - lost
                    if lost:
                        self.emit(EventKind.ATTACK_CANCELLED, who=a.attacker,
                                  other=a.target, amount=lost)
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
        self.emit(EventKind.CONQUERED_PLAYER, other=attacker, amount=target)
        if d.units.units:
            self.emit(EventKind.CAPTURED_ENEMY_UNIT, who=attacker, other=target,
                      amount=len(d.units.units))
        # `conquerPlayer` — 건물도 정복자에게 넘어간다. 버리면 도시가 사라져
        # 병력 상한이 갑자기 떨어진다.
        winner = self.players[attacker]
        for u in d.units.units:
            u.owner = attacker
            u.deletion_at = None       # `setOwner()` → `clearPendingDeletion()`
            winner.units.units.append(u)
            winner.units.record_constructed(u.utype)
        d.units.units = []
        self._transfer_conquest_gold(winner, d)
        self.diplomacy.drop_player(target)
        self._rebuild_posts()

    def _transfer_conquest_gold(self, winner: PlayerState,
                                loser: PlayerState) -> None:
        """`conquerPlayer` 의 골드 이전. **우리에게 통째로 없던 규칙이다.**

        원본은 정복자가 패자의 골드를 가져간다 — 봇·나라는 **전액**, 사람은 **절반**
        (`conquerGoldAmount`). 패자에게서는 언제나 **전액**이 빠지므로, 사람을 정복하면
        나머지 절반은 **어디로도 가지 않고 사라진다.** 그 비대칭이 원본이고, 사람을
        터는 것이 나라를 터는 것보다 남는 게 적다는 뜻이다.

        ⚠ 예외 하나: **한 번도 공격을 보낸 적 없는 사람**은 이전 자체를 건너뛴다.
        시작 골드를 켠 판에서 가만히 있는 사람을 털어 가는 것을 막는 장치다
        (원본 주석: "Don't transfer gold when the conquered player didn't play").
        봇·나라에는 이 예외가 걸리지 않는다.

        이게 없어서 판에서 골드가 조용히 증발하고 있었다 — 472명이 도는 판은 수백 명이
        탈락하는데, 그들이 모은 골드가 아무에게도 가지 않았다."""
        if loser.kind == "human" and loser.attacks_sent == 0:
            return
        taken = (loser.gold // 2 if loser.kind == "human" else loser.gold)
        winner.gold += taken
        loser.gold = 0                 # `removeGold(gold)` — 언제나 전액이 빠진다
        if taken:
            self.emit(EventKind.GOLD_FROM_CONQUEST, who=winner.pid,
                      other=loser.pid, amount=taken)

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
