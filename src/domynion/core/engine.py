"""게임 루프 — tick 하나가 100ms (원본 `turnIntervalMs` = 100).

한 tick 순서: **성장 → 공격 진행 → 흡수/탈락 → 종료 판정.**

영토 수는 `_counts` 로 증분 유지한다. 13만 타일을 매 tick 세면 판당 수 초가 날아간다
(전수 `np.bincount` 는 0.42ms, 9000 tick 이면 3.8초). 테스트만 전수로 대조한다.

⚠ 종료 조건(시간 제한·지배)은 **원본에도 있다**(`checkWinnerFFA`). 오래 "우리가
넣은 것"이라 적혀 있었으나 §5.61 에서 정정했다 — 값이 11배 짧았을 뿐이다.
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
from .buildings import (DefensePostIndex, all_structure_tiles, euclid_sq,
                        find_spot, structure_tiles)
from .diplomacy import Diplomacy
from .doomsday import DoomsdayClock
from .events import Event, EventKind, EventLog
from .gamemap import DEFAULT_SIZE, GameMap, TileRef
from .naval import (TradeShip, TransportShip, Warship, best_spawn, shell_damage,
                    _touching_components, landing_tile, manhattan,
                    port_check_due, trade_gold, trade_spawn_rate,
                    trading_ports, water_path)
from . import enclave
from .rot import RotState, rot_tiles
from .nukes import (Fallout, Nuke, NUKE_MAGNITUDES, SAM_TARGETABLE_TYPES,
                    blast_counts, blast_tiles, death_factor,
                    dynamic_sam_range, is_targetable, sam_range,
                    sam_target_score)
from .rail import RailNetwork, Train, train_gold, train_spawn_rate
from .spawn import pick_spawn, place_at, spawn_tiles
from . import emoji as emoji_mod
from .emoji import Emojis
from .emoji import relation_delta as emoji_relation_delta
from .emoji import reply_to as emoji_reply_to
from .relations import Relation, gold_donation_relation, troop_donation_min
from .state import PlayerState
from .units import UNIT_INFO, STRUCTURES, Unit, UnitType


class Victory(Enum):
    CONQUEST = "정복"          # 원본의 유일한 승리 조건
    # ⚠ 아래 두 **이름**은 우리 것이다 — 원본은 승리 종류를 구분하지 않고
    # `setWinner` 하나로 끝낸다. 그러나 **조건 자체는 원본에 있다**(§5.61):
    # `percentageTilesOwnedToWin` = 80% · `HARD_TIME_LIMIT_SECONDS` = 170분.
    DOMINATION = "지배"        # percentageTilesOwnedToWin
    TIMEOUT = "시간 종료"      # HARD_TIME_LIMIT_SECONDS


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
    # 둠스데이 썩음 진행(pid → RotState). 회복하면 버린다.
    _rot: dict = field(default_factory=dict)
    # pid → 영토가 마지막으로 바뀐 tick(`lastTileChange`). 둘러싸임 검사가
    # **바뀐 나라만** 보게 하는 데 쓴다 — 안 그러면 판 시간의 절반을 먹는다.
    _tile_changed: dict = field(default_factory=dict)
    # 역 타일 → 마지막 발차 tick(`ticksCooldown`). 한 역이 연달아 못 내게 한다.
    _station_fired: dict = field(default_factory=dict)
    # (준 사람, 받는 사람) → 마지막 기부 tick(`donateCooldown`).
    _donated_at: dict = field(default_factory=dict)
    _enclave_checked: dict = field(default_factory=dict)
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

    # pid → (tick, 액수). HUD 의 `+N` 알림용으로 **마지막 하나만** 들고 있다.
    # 원본 `addGold(gold, tile)` 이 흘리는 `BonusEvent` + `ConquestEvent` +
    # `DonateEvent` 가 여기 모인다(`ControlPanel.tick`).
    gold_gains: dict[int, tuple[int, float]] = field(default_factory=dict)

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
        if not self._merge_attack(atk):
            return None                 # 맞공격에 통째로 상쇄됐다
        if target is not None:
            self.emit(EventKind.ATTACK_REQUEST, who=target, other=pid, amount=troops)
            # ⚠ **치는 순간 그쪽이 보낸 동맹 요청은 거절되고, 맞은 쪽이 나에게
            # 임시 금수를 건다**(`AttackExecution:95~104`). 요청 거절이 없으면
            # 때려 놓고 그 요청을 그대로 받아 동맹이 된다 — 공격이 관계에 −70 을
            # 주는 것과 앞뒤가 안 맞는다.
            #
            # ⚠ 둘 다 **봇이 끼면 안 한다.** 전에는 거절만 옮기면서 이 조건을
            # 상륙 쪽에만 달아 두고 "원본이 여기서만 본다"고 적었는데, 원본은
            # 육상 공격에도 같은 `if` 를 두고 있다(봇은 무역을 안 하므로 금수가
            # 의미가 없다는 원본 주석이 그 자리에 있다).
            if not p.is_bot and not self.players[target].is_bot:
                self.diplomacy.reject(pid, target)
                self.diplomacy.start_embargo(target, pid, self.tick_count,
                                             temporary=True)
            # 맞은 쪽만 나빠진다(`AttackExecution` 도 target 쪽만 갱신한다).
            self.relate(target, pid, C.REL_ATTACKED.get(self.difficulty, -70.0))
            if self.diplomacy.is_friendly(pid, target):
                self.relate(pid, target, C.REL_ATTACKED_ALLY)
        return atk

    def _merge_attack(self, atk: Attack) -> bool:
        """새 공격을 기존 공격들과 정리한다(`AttackExecution.init` 의 두 루프).

        **1) 맞공격은 상쇄된다.** 상대가 나를 치고 있으면 병력을 서로 깎는다 —
        큰 쪽만 남고 작은 쪽은 사라진다. 없으면 두 부대가 서로를 통과해 지나가,
        *A가 B의 땅을 먹는 동시에 B가 A의 땅을 먹는* 그림이 된다.

        **2) 같은 상대를 치는 내 공격은 하나로 합쳐진다.** 없으면 공격 버튼을
        연타해 부대를 여러 개로 쪼갤 수 있고, 쪼갠 쪽이 유리하다 — 확장은
        국경 길이를 따라가므로 부대 수가 많을수록 전선이 넓어진다.

        ⚠ **상륙은 합치지 않는다**(`sourceTile !== null`). 상륙 부대는 배가 내린
        칸에서 시작하므로 육상 부대에 합치면 그 자리를 잃는다.

        돌려주는 값은 *이 공격이 살아남았는가* 다."""
        # ⚠ **퇴각 중인 공격도 그대로 센다.** 원본에 그 예외가 없다 — 물러나는
        # 부대에 새 부대를 합치면 그대로 되돌아온다. 처음에 `retreated` 를
        # 거르는 줄을 넣었다가 지웠다: 퇴각이 끝난 공격은 **같은 tick 에 목록에서
        # 빠지므로** 여기서 볼 수가 없다(죽은 코드였다).
        for other in list(self.attacks):
            if other is atk:
                continue
            # (1) 상대가 나를 치는 중인가
            if other.attacker == atk.target and other.target == atk.attacker:
                if other.troops > atk.troops:
                    other.troops -= atk.troops
                    self.attacks.remove(atk)
                    return False
                atk.troops -= other.troops
                self.attacks.remove(other)
                continue
            # (2) 내가 같은 상대를 이미 치는 중인가
            if (atk.source_tile is None and other.attacker == atk.attacker
                    and other.target == atk.target):
                atk.troops += other.troops
                self.attacks.remove(other)
        return True

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

    def note_gold_gain(self, pid: int, amount: float) -> None:
        """골드가 **덩어리로** 들어온 것을 기록한다(무역·철도·정복·기부).

        원본은 `addGold` 안에서 `BonusEvent` 를 흘리고 HUD 가 그걸 받아 2초간
        `+N` 을 띄운다. 매 tick 들어오는 인구 수입(`GOLD_PER_TICK_*`)은 이 경로가
        아니다 — 원본도 `PlayerExecution` 에서 tile 없이 `addGold` 를 부른다.

        ⚠ **합치지 않고 마지막 것으로 덮는다**(원본 주석 *"Last-wins"*). 한 tick 에
        여러 건이 와도 가장 최근 액수만 보여준다."""
        if amount > 0:
            self.gold_gains[pid] = (self.tick_count, amount)

    # --- 외교 -------------------------------------------------------------

    def extend_alliance(self, pid: int, other: int) -> bool:
        """`AllianceExtensionExecution` — 연장 요청. 성사되면 True.

        ⚠ **이식 누락 마흔다섯.** 규칙은 §5.53 에서 붙였는데 두 군데가 어긋나 있었다:

        1. **원본은 양쪽이 동의한 그 순간 갱신한다**(`extend()` 는
           `expiresAt = 지금 + 기간`). 우리는 만료될 때까지 미뤘다. 미루면 남은
           시간이 **덤으로 붙는다** — 100초 남기고 동의하면 원본은 300초가 되는데
           우리는 400초가 됐다. 일찍 동의할수록 이득이라 방향까지 반대다.
        2. 한쪽만 동의했을 때 **상대에게 알리지 않았다**(`RENEW_ALLIANCE`). 이걸
           안 보내면 연장은 **양쪽이 우연히 같은 생각을 했을 때만** 성사된다 —
           사람은 상대가 원하는 줄 모르고, AI 는 요청을 볼 수가 없다.

        ⚠ 알림은 **아무 → 한쪽** 전이일 때만 보낸다(`!wasOnlyOneAgreed`). 안 그러면
        같은 사람이 여러 번 눌러 소식창을 도배한다.
        """
        al = self.diplomacy.alliance_between(pid, other)
        if al is None or not self.players[pid].alive:
            return False
        was_one = al._extend_a != al._extend_b
        al.request_extension(pid)
        if al.both_agreed_to_extend:
            al.expires_at = self.tick_count + C.ALLIANCE_DURATION_TICKS
            al._extend_a = al._extend_b = False
            for who, name in ((pid, other), (other, pid)):
                self.emit(EventKind.ALLIANCE_ACCEPTED, who=who, other=name)
            return True
        if not was_one:
            self.emit(EventKind.RENEW_ALLIANCE, who=other, other=pid)
        return False

    def request_alliance(self, pid: int, other: int) -> bool:
        """`AllianceRequestExecution.init`.

        ⚠ **맞요청은 그 자리에서 성립한다**(이식 누락 쉰다섯). 상대가 이미
        나에게 요청해 뒀으면 새 요청을 만드는 대신 **그 요청을 수락한다** —
        원본 주석 그대로다(*"then accept it instead of creating a new one"*).

        없으면 서로 손을 내민 두 나라가 **각자 대기 상태로 남아** 아무도
        수락하지 않은 동맹이 된다. 사람이 먼저 손을 내밀었는데 AI 도 같은
        생각이었을 때가 정확히 그 자리다."""
        if pid in self.diplomacy.pending.get(other, set()):
            ok = self.accept_alliance(pid, other)
            if ok:
                # ⚠ **이 셋은 맞요청 분기에만 있다.** 원본 `AllianceRequestExecution`
                # 이 여기서만 부르고, 평범한 수락(`AllianceRequestReplyExecution`)
                # 에서는 아무것도 안 한다. "동맹이 맺어지면 언제나" 로 옮기면
                # 원본에 없는 규칙이 된다 — 확인하고 이 자리에 둔다.
                self.diplomacy.end_temporary_embargo(pid, other)
                self.diplomacy.end_temporary_embargo(other, pid)
                self._cancel_nukes_between(pid, other)
            return ok
        ok = self.diplomacy.request(pid, other, self.tick_count)
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
                  target: int | None = "auto",
                  troops: float | None = None) -> TransportShip | None:
        """상륙 부대를 띄운다. 병력 `troops/5`, 동시에 최대 3척(`boatMaxNumber`)."""
        p = self.players.get(pid)
        if p is None or not p.alive or self.over:
            return None
        if sum(1 for b in self.boats if b.owner == pid) >= C.BOAT_MAX_NUMBER:
            # ⚠ **왜 알리는가**(`TransportShipExecution.ts:70~82`). 배가 다 나가
            # 있으면 클릭이 **아무 일도 안 일어난 것처럼** 보인다 — 병력도 안 줄고
            # 배도 안 뜬다. 사람은 "지도를 잘못 눌렀나"를 의심하지 3척 제한을
            # 떠올리지 못한다. 조용한 실패 중 가장 헷갈리는 자리다.
            self.emit(EventKind.ATTACK_FAILED, who=pid,
                      amount=C.BOAT_MAX_NUMBER)
            return None
        if not self.gmap.passable(dst) or int(self.gmap.owner[dst]) == pid:
            return None
        if target == "auto":
            o = int(self.gmap.owner[dst])
            target = None if o < 0 else o
        if not self.can_attack(pid, target):
            return None

        # ⚠ **상륙 지점을 먼저 옮긴다**(원본 `TransportShipExecution.init` 이
        # `targetTransportTile` → `canBuild(..., dst)` 순서다). 클릭한 칸이 곧
        # 상륙 지점이 아니다 — 안쪽을 눌러도 50칸 안의 가장 가까운 해안으로
        # 간다. 출발지도 **옮긴 뒤의** 목적지를 기준으로 골라야 한다.
        moved = landing_tile(self.gmap, pid, dst)
        if moved is None:
            return None
        dst = moved

        src = best_spawn(self.gmap, pid, dst)
        if src is None:
            return None
        path = self._water_path(src, dst)
        if path is None:
            return None
        # 원본 `TransportShipExecution` 은 **보낼 병력을 인자로 받는다** —
        # AI 가 상한(`troopSendCap`)을 걸어 줄여 보낼 수 있다(§5.77).
        troops = p.troops * C.BOAT_ATTACK_RATIO if troops is None else troops
        troops = min(troops, p.troops)
        if troops < C.ATTACK_MIN_TROOPS:
            return None
        p.troops -= troops
        boat = TransportShip(owner=pid, target=target, troops=troops,
                             path=path, dst=dst)
        self.boats.append(boat)
        if target is not None:
            # ⚠ 상륙도 **그쪽이 보낸 동맹 요청을 거절한다**
            # (`TransportShipExecution.rejectIncomingAllianceRequests`), 봇이
            # 끼면 안 한다 — 육상 공격과 같은 조건이다.
            #
            # ⚠ 다만 **임시 금수는 여기 없다.** 원본 `TransportShipExecution` 은
            # 같은 `if` 안에서 거절만 하고 `addEmbargo` 를 부르지 않는다. 둘이
            # 같아 보인다고 묶으면 상륙만으로 금수가 걸린다.
            if not p.is_bot and not self.players[target].is_bot:
                self.diplomacy.reject(pid, target)
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
        self._tile_changed[pid] = self.tick_count
        if previous >= 0:
            self._counts[previous] = max(0, self._counts.get(previous, 0) - 1)
            self._tile_changed[previous] = self.tick_count

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

        # ⚠ **목적지 항구가 아직 있는지 본다**(`!this._dstPort.isActive()`).
        # 없으면 원본은 배를 지운다 — 이식 누락 여든셋. 우리는 주인이 살아 있기만
        # 하면 계속 갔고, **항구가 부서졌는데도 도착해서 골드를 줬다.**
        # (항구는 정복으로 넘어가거나 핵에 부서지거나 스스로 철거된다.)
        live_ports = {tile for tile, _pid, _lvl, _u in ports}
        still: list[TradeShip] = []
        for t in self.trade_ships:
            src_p = self.players.get(t.owner)
            dst_p = self.players.get(t.dst_owner)
            if src_p is None or not src_p.alive or dst_p is None or not dst_p.alive:
                continue
            if t.dst_port not in live_ports:
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
            gold = trade_gold(t.tiles_travelled)
            if t.captured_by is not None:
                # `wasCaptured` — **나포한 쪽이 전액**을 번다. 원래 주인은 0이다.
                pirate = self.players.get(t.captured_by)
                if pirate is not None and pirate.alive:
                    pirate.gold += gold
                    self.note_gold_gain(pirate.pid, gold)
                    self.emit(EventKind.GOLD_FROM_CAPTURED_SHIP,
                              who=pirate.pid, other=t.owner,
                              tile=t.dst_port, text="나포한 무역선")
            else:
                src_p.gold += gold
                dst_p.gold += gold
                self.note_gold_gain(src_p.pid, gold)
                self.note_gold_gain(dst_p.pid, gold)
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
        # ⚠ `tiles_travelled` 는 **안 건드린다.** 새 경로를 깔되 지나온 거리는
        # 그대로 이어 센다 — 원본 `tilesTraveled` 가 배 하나에 붙어 있는 값이라
        # 목적지가 바뀌어도 0으로 안 돌아간다(§5.81).
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
            # ⚠ **베테랑 보정된** 최대 체력의 비율이다(원본 주석: *"so a tougher
            # veteran ship retreats at the same relative health as a fresh one"*).
            # 기본 1000 으로 재면 레벨 3 짜리(1600)는 체력 750 까지 버티는데,
            # 그건 상대 비율로 47% 다 — 베테랑일수록 더 늦게 돌아간다.
            threshold = (w.max_health * C.WARSHIP_RETREAT_HEALTH_PERCENT) // 100
            if health_before >= threshold:
                return False
            w.retreat_port = self._nearest_port_tile(w, p)
        else:
            # `refreshRetreatPortTile` — **매 tick 다시 본다**(§5.87). 전에는
            # 항구가 사라졌을 때만 골랐다. 그래서 꽉 찬 항구 앞에서 영원히
            # 기다리는 배가 생겼고, 훨씬 가까운 항구가 새로 서도 안 갔다.
            port = next((u for u in p.units.of(UnitType.PORT)
                         if u.tile == w.retreat_port
                         and not u.under_construction), None)
            if port is None:
                w.retreat_port = self._nearest_port_tile(w, p)   # 항구가 사라졌다
                w.docked = False
            elif not w.docked and self._port_full_of_healing(port, exclude=w):
                alt = self._nearest_port_tile(w, p, free_only=True)
                if alt is not None:
                    w.retreat_port = alt
            elif not w.docked:
                # ⚠ **문턱이 없으면 두 항구 사이에서 제자리걸음한다.** 새 항구가
                # 지금 목적지보다 `0.75` 배보다 더 가까울 때만 바꾼다.
                got = self._nearest_port(w, p, free_only=True)
                if got is not None:
                    tile, d = got
                    cur = self._dist_sq(w.tile, w.retreat_port)
                    closer = d < cur * C.WARSHIP_PORT_SWITCH_THRESHOLD
                    if tile != w.retreat_port and closer:
                        w.retreat_port = tile

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
            docked_here = self._docked_at(port, exclude=w)
            if w.docked or docked_here < port.level:
                w.docked = True
                self._apply_docked_healing(w, port, docked_here + 1)
            elif w.health >= w.max_health:
                self._cancel_retreat(w)
                return False
            # 자리가 없으면 항구 옆에서 기다린다(수동 회복은 계속 받는다)
        else:
            step = self._step_toward(w.tile, w.retreat_port)
            if step is None:
                self._cancel_retreat(w)
                return False
            w.tile = step

        if w.health >= w.max_health:
            self._cancel_retreat(w)
        return True

    def _cancel_retreat(self, w: Warship) -> None:
        w.retreat_port, w.docked, w.heal_remainder = None, False, 0.0

    def _nearest_port_tile(self, w: Warship, p: PlayerState,
                           free_only: bool = False) -> "TileRef | None":
        """가장 가까운 내 항구. `free_only` 면 **정박 자리가 남은 곳만** 본다
        (원본 `nearestAvailablePortTile`)."""
        return (self._nearest_port(w, p, free_only) or (None, None))[0]

    def _nearest_port(self, w: Warship, p: PlayerState,
                      free_only: bool = False):
        """(칸, 거리제곱) 또는 None. 거리를 쓰는 쪽이 있어 따로 둔다."""
        comp = _touching_components(self.gmap, w.tile)
        best, best_d = None, None
        for u in p.units.of(UnitType.PORT):
            if u.under_construction or u.marked_for_deletion:
                continue
            if comp and not (comp & _touching_components(self.gmap, u.tile)):
                continue          # 수로가 안 이어져 있으면 갈 수 없다
            if free_only and self._port_full_of_healing(u, exclude=w):
                continue
            d = self._dist_sq(w.tile, u.tile)
            if best_d is None or d < best_d:
                best, best_d = u.tile, d
        return None if best is None else (best, best_d)

    def _docked_at(self, port, exclude: "Warship | None" = None) -> int:
        """그 항구에 정박해 수리 중인 배 수(`dockedShipsAtPort`)."""
        return sum(1 for o in self.warships
                   if o is not exclude and not o.sunk and o.owner == port.owner
                   and o.docked and o.retreat_port == port.tile)

    def _port_full_of_healing(self, port, exclude: "Warship | None" = None) -> bool:
        """`isPortFullOfHealing` — 정박한 배가 **항구 레벨만큼** 있으면 꽉 찼다."""
        return self._docked_at(port, exclude) >= port.level

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
        w.health = min(w.max_health, w.health + gain)

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
                    w.record_trade_capture()
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
                w.record_kill("warship")
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
                w.record_kill("transport")
                self.emit(EventKind.UNIT_DESTROYED, who=target.owner, other=w.owner,
                          tile=target.tile, text="수송선")
        # ⚠ 무역선은 여기 안 온다 — **격침이 아니라 나포**라
        # `_hunt_trade_ship` 이 따로 처리한다(이식 누락 스물).

    def _heal_warship(self, w: Warship, p: PlayerState) -> None:
        """항구 사거리 안이면 tick 당 1 회복. **클락에 표시된 쪽은 회복 못 한다** —
        그래야 클락의 유출이 실제로 배를 가라앉힌다(원본 주석 그대로)."""
        if w.owner in self.clock.marked_at:
            return
        if w.health >= w.max_health:
            return
        r2 = C.WARSHIP_PASSIVE_HEALING_RANGE ** 2
        for port in p.units.of(UnitType.PORT):
            if self._dist_sq(w.tile, port.tile) <= r2:
                w.health = min(w.max_health,
                               w.health + C.WARSHIP_PASSIVE_HEALING)
                return

    # --- 핵 ---------------------------------------------------------------

    def launch_nuke(self, pid: int, utype: UnitType, dst: TileRef,
                    wait_ticks: int = 0) -> Nuke | None:
        """미사일 사일로에서 쏜다. 사일로가 없으면 못 쏜다.

        `wait_ticks` 는 **부르는 쪽이 더 미는 시간**이다(원본 `NukeExecution` 의
        생성자 인자). 사일로 큐 때문에 자동으로 밀리는 양에 **더해진다** — 원본도
        `this.waitTicks += ...` 로 더한다. 도착 시각을 맞추는 일제 사격
        (`maybeDestroyEnemySam`)이 이 인자를 쓴다."""
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
        # ⚠ **관을 막기 전에** 대기 tick 을 계산한다. 같은 사일로에서 한 tick 에
        # 여러 발을 쏘면(대량 구매) 원본은 발사를 하나씩 뒤로 민다. 큐 전체를
        # 봐야 한다 — 원본 주석: *"even if nukes have waitticks, the silo queue
        # will be filled with the same tick"*.
        wait = 0
        last_dep = 0
        for launch_tick in silo.missile_queue:
            last_dep = max(launch_tick + 1, last_dep + 1)
        if last_dep > self.tick_count:
            wait = last_dep - self.tick_count
        silo.fire(self.tick_count)          # `silo.launch()` — 관 하나가 막힌다
        src = silo.tile
        n = Nuke(owner=pid, utype=utype, src=src, dst=dst,
                 wait_ticks=wait + max(0, wait_ticks))
        self.nukes.append(n)
        kind = {UnitType.HYDROGEN_BOMB: EventKind.HYDROGEN_BOMB_INBOUND,
                UnitType.MIRV: EventKind.MIRV_INBOUND}.get(
                    utype, EventKind.NUKE_INBOUND)
        victim = int(self.gmap.owner[dst])
        self.emit(kind, who=victim if victim >= 0 else None, other=pid, tile=dst)
        if utype is UnitType.MIRV:
            # MIRV 만 **양방향**이다 — 쏜 쪽도 상대를 적으로 확정한다.
            if victim >= 0 and victim != pid:
                self.relate(victim, pid, C.REL_MIRV)
                self.relate(pid, victim, C.REL_MIRV)
        else:
            self._nuke_angers(pid, utype, dst)
        if victim >= 0 and victim != pid:
            self.ai_emoji(pid, victim, emoji_mod.NUKE)
        return n

    def _nuke_angered(self, pid: int, utype: UnitType, dst: TileRef) -> list[int]:
        """`listNukeBreakAlliance` — 이 핵에 **화를 낼 나라들**.

        ⚠ **표적 칸의 주인 한 명이 아니다.** 반경에 든 나라를 가중치로 세어
        (내부 1 · 외부 0.5) 합이 문턱을 넘으면 포함되고, **반경 안에 건물이
        있으면 타일 수와 무관하게** 포함된다. 원본 주석이 두 경로를 못 박아 뒀다:
        *"exceeds tile threshold OR has a structure in blast radius."*"""
        out: list[int] = []
        for owner, weight in blast_counts(self.gmap, dst, utype).items():
            if owner != pid and weight > C.NUKE_ALLIANCE_BREAK_THRESHOLD:
                out.append(owner)
        _, outer = NUKE_MAGNITUDES[utype]
        w = self.gmap.width
        cx, cy = dst % w, dst // w
        for p in self.alive:
            if p.pid == pid or p.pid in out:
                continue
            for u in p.units.units:
                if not u.active or u.utype not in STRUCTURES:
                    continue
                dx, dy = u.tile % w - cx, u.tile // w - cy
                if dx * dx + dy * dy <= outer * outer:
                    out.append(p.pid)
                    break
        return out

    def _nuke_angers(self, pid: int, utype: UnitType, dst: TileRef) -> None:
        """`NukeExecution.maybeBreakAlliances` — 발사하는 그 tick 에 값을 치른다.

        ⚠ **이식 누락 쉰넷.** 우리는 표적 칸 주인의 관계만 −100 으로 깎았다.
        그래서 **동맹에게 핵을 쏘고도 동맹이 유지됐고** 배신자 낙인(§5.68)도
        안 찍혔다 — 핵으로 뒤통수를 치는 쪽이 아무 대가도 안 치렀다.

        ⚠ MIRV **탄두**는 여기 안 온다(원본 `MIRVWarhead` 예외). 갈라진 탄두마다
        동맹이 깨지면 MIRV 한 발로 판의 모든 동맹이 사라진다."""
        for other in self._nuke_angered(pid, utype, dst):
            # 순서가 있다: **들어온 요청부터 거절한다.** 원본 주석 —
            # 미사일이 나는 동안 요청을 수락해 파기를 피하는 구멍을 막는 것이다.
            self.reject_alliance(pid, other)
            self.reject_alliance(other, pid)
            self.break_alliance(pid, other)
            self.relate(other, pid, C.REL_NUKED)

    def _cancel_nukes_between(self, a: int, b: int) -> None:
        """`cancelNukesBetweenAlliedPlayers` — 동맹이 맺어지면 **서로에게 날아가던
        핵이 사라진다.**

        ⚠ §5.72(핵이 동맹을 깬다)의 **반대 방향**이고, 짝으로 있어야 말이 된다.
        한쪽만 있으면 *쏜 뒤 동맹을 맺어 취소하는 길*만 막히고 그 반대는 열려 있다.

        ⚠ **종류마다 "그쪽으로 가는 핵"의 판정이 다르다.** 원본이 세 갈래로 나눈다:
        MIRV 본체와 탄두는 **표적 칸의 주인**으로 보고, 일반 핵은 §5.72 와 같은
        가중 타일·건물 판정(`wouldNukeBreakAlliance`)을 쓴다. 일반 핵까지 칸 주인만
        보면, 동맹의 국경 바로 밖을 노린 핵 — *터지면 동맹이 깨질* 핵 — 이 남는다."""
        for launcher, other in ((a, b), (b, a)):
            count = 0
            warheads = False
            for n in list(self.nukes):
                if n.owner != launcher:
                    continue
                if n.utype in (UnitType.MIRV, UnitType.MIRV_WARHEAD):
                    if int(self.gmap.owner[n.dst]) != other:
                        continue
                elif other not in self._nuke_angered(launcher, n.utype, n.dst):
                    continue
                self.nukes.remove(n)
                # ⚠ **탄두는 몇 발이 사라져도 소식에는 1로 센다**(원본이 발사자
                # 집합으로 모은다). 갈라진 350발을 그대로 세면 "핵 350발이
                # 사라졌다"가 되는데, 사람이 산 것은 MIRV 한 발이다.
                if n.utype is UnitType.MIRV_WARHEAD:
                    warheads = True
                else:
                    count += 1
            count += 1 if warheads else 0
            if count:
                self.emit(EventKind.NUKES_CANCELLED_SENT, who=launcher,
                          other=other, amount=count)
                self.emit(EventKind.NUKES_CANCELLED_RECEIVED, who=other,
                          other=launcher, amount=count)

    def _split_mirv(self, n: Nuke) -> None:
        """MIRV 는 스스로 터지지 않고 **탄두 여러 개로 갈라진다**(원본 350발 고정).

        ⚠ 이 줄은 **`map4x` 시절 값이었다**(§5.57). "우리 지도는 원본의 1/16
        면적이라 350발이면 지도가 통째로 날아간다"고 적고 면적 비로 줄였는데,
        §5.47 에서 기본 해상도를 원본 크기로 올릴 때 이 줄을 안 봤다. 그래서
        **원본과 같은 크기에서도 114발**만 쓰고 있었다(실측).

        게다가 분모(2,000,000)는 지도의 **총 칸 수**인데 분자에는 **육지 수**를
        넣고 있었다 — 원본 크기에서도 651,569/2,000,000 = 0.33 이 곱해진다.
        즉 두 번 틀렸다.

        면적 비 자체는 남긴다. 작은 지도(`map16x` **20발** · `map4x` **85발**)에서는
        여전히 필요하고, **원본 크기에서 350발이 되는 것**이 맞는 기준선이다.
        (6·27 은 분모가 틀렸을 때의 값이다 — 고친 뒤 실측으로 갈아 끼웠다.)"""
        count = max(1, round(C.MIRV_WARHEAD_COUNT
                             * self.gmap.land_count / C.FULL_MAP_LAND))
        for tile in self._mirv_targets(n.dst, count):
            self._detonate(Nuke(owner=n.owner, utype=UnitType.MIRV_WARHEAD,
                                src=n.dst, dst=tile))

    def _mirv_targets(self, base: TileRef, count: int) -> list[TileRef]:
        """`tryGenerateTarget` — 탄두가 떨어질 자리들.

        ⚠ **표적의 땅에만 떨어진다.** 우리는 상자 안 아무 칸에나 뿌리고 있었다 —
        바다에도, 내 땅에도, 중립에도. 그리고 **최소 간격**(맨해튼 55)이 있어야
        한다. 안 그러면 한 덩어리에 몰려 터져 350발이 한 발과 다를 바 없어진다.

        자리를 못 찾으면(100번 시도) **그 탄두는 그냥 없다.** 원본도 발 수를
        채우려 하지 않는다 — 좁은 나라에 쏘면 그만큼 적게 떨어진다."""
        gm = self.gmap
        w, h = gm.width, gm.height
        owner = int(gm.owner[base])
        bx, by = base % w, base // w
        rng_ = C.MIRV_TARGET_RANGE
        r2 = rng_ * rng_
        spread = C.MIRV_MIN_SPREAD
        taken: list[tuple[int, int]] = []
        out: list[TileRef] = []
        # ⚠ 시도 예산은 **전체**다(자리마다가 아니다). 원본은 던져서 되면 담고,
        # 예산이 다 떨어지거나 발 수를 채우면 멈춘다 — 좁은 나라에 쏘면 예산만
        # 태우고 적게 떨어진다.
        for _attempt in range(C.MIRV_TARGET_ATTEMPTS):
            if len(out) >= count:
                break
            x = round(self.rng.uniform(-rng_, rng_) + bx)
            y = round(self.rng.uniform(-rng_, rng_) + by)
            if not (0 <= x < w and 0 <= y < h):
                continue
            # ⚠ **변이로 안 잡힌다. 정상이다** — 반경 1,500 이 우리 지도보다
            # 크다(원본 크기 2000×1000 도 중심에서 모서리까지 1,118). 지도가
            # 3,000칸보다 넓어져야 무는 검사다. 원본에 있으므로 남긴다.
            if (x - bx) ** 2 + (y - by) ** 2 > r2:
                continue
            t = y * w + x
            if not gm.passable(t):
                continue
            if int(gm.owner[t]) != owner:
                continue
            if any(abs(x - tx) + abs(y - ty) < spread for tx, ty in taken):
                continue
            taken.append((x, y))
            out.append(t)
        # 원본은 표적에서 **먼 것부터** 정렬한다(`finalizeDestinations`) — 탄두가
        # 바깥부터 떨어져 안쪽이 마지막에 터진다.
        out.sort(key=lambda t: abs(t % w - bx) + abs(t // w - by), reverse=True)
        return out

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
        # ⚠ **먼저 움직이고, 그다음 SAM 이 고른다**(§5.83). 전에는 핵 하나를
        # 옮길 때마다 그 자리에서 요격을 물어봐서, 한 SAM 이 볼 수 있는 핵이
        # 여럿일 때 **목록 순서대로** 막았다 — 수폭과 원자탄이 같이 오면 먼저
        # 만들어진 쪽이 막혔다. 원본은 SAM 마다 표적을 모아 **점수로 고른다.**
        for n in self.nukes:
            if n.wait_ticks > 0:
                # 대기 중인 핵은 **발사점에 떠 있다.** 움직이지 않지만 요격은 된다
                # (원본도 대기 분기에서 이동만 건너뛰고 유닛은 살아 있다).
                n.wait_ticks -= 1
                continue
            n.advance()
        shot = self._sams_pick_targets()
        for n in self.nukes:
            if id(n) in shot:
                continue
            if n.wait_ticks > 0:
                still.append(n)
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

    def _sams_pick_targets(self) -> "set[int]":
        """SAM 마다 **점수가 가장 높은 표적 하나**를 쏜다(`sortTargets`).

        ⚠ 한 핵을 두 SAM 이 겹쳐 쏘지 않는다(`targetedBySAM`). 원본도 이미
        노려진 유닛을 후보에서 뺀다 — 안 그러면 핵 한 발에 방공망 전체가 소모된다.

        돌려주는 것은 **맞은 핵들의 `id()`** 다. 리스트에서 지우는 것은 부르는
        쪽이 한다 — 여기서 지우면 순회 중인 목록을 건드리게 된다."""
        taken: set[int] = set()
        for p in self.alive:
            for sam in p.units.of(UnitType.SAM_LAUNCHER):
                if sam.under_construction or sam.in_cooldown:
                    continue
                r = dynamic_sam_range(sam, self.tick_count)
                r2 = r * r
                best, best_score = None, None
                for n in self.nukes:
                    if id(n) in taken or n.owner == p.pid:
                        continue
                    if n.utype not in SAM_TARGETABLE_TYPES:
                        continue
                    if self.diplomacy.is_friendly(p.pid, n.owner):
                        continue
                    here = n.tile(self.gmap)
                    if self._dist_sq(sam.tile, here) > r2:
                        continue
                    if not is_targetable(self.gmap, n.src, n.dst, here):
                        continue
                    score = sam_target_score(self.gmap, sam.tile, n)
                    if best_score is None or score > best_score:
                        best, best_score = n, score
                if best is None:
                    continue
                taken.add(id(best))
                sam.fire(self.tick_count)
                here = best.tile(self.gmap)
                self.emit(EventKind.SAM_HIT, who=p.pid, other=best.owner, tile=here)
                self.emit(EventKind.SAM_MISS, who=best.owner, other=p.pid, tile=here)
        return taken

    def _sam_intercepts(self, n: Nuke) -> bool:
        """이 핵 **하나**가 지금 요격되는가.

        ⚠ 판정은 `_sams_pick_targets` 가 한다 — SAM 마다 표적을 모아 점수로
        고르기 때문에(§5.83) 핵 하나만 보고 답할 수 없다. 이 함수는 그 결과를
        한 발에 대해 물어보는 **얇은 껍데기**다.

        ⚠ **부작용이 있다** — 맞으면 SAM 의 관이 실제로 소모되고 소식이 나간다.
        판정만 하는 함수가 아니다."""
        return id(n) in self._sams_pick_targets()

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
            # 지형이 바뀌었다 — 바다 성분·통행 마스크·경로 캐시를 버린다
            # (P4 의 전제가 깨진다). 무효화 목록은 `GameMap` 안에 모아 뒀다.
            gm.invalidate_terrain_caches()
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
        self.rail.rebuild(self.gmap, self.alive)
        # ⚠ **역마다, 그 역의 레벨만큼 굴린다**(§5.60, 이식 누락 마흔셋).
        # 원본 `TrainStationExecution.shouldSpawnTrain` 이 그렇다. 전에는 나라
        # 단위로 한 번만 굴려서, 역이 열 곳이어도 기차는 한 대였다.
        #
        # ⚠ **공장 역만 기차를 낸다**(`spawnTrains` 가 공장에만 true). 도시·항구
        # 역은 지나가는 정거장이지 출발지가 아니다.
        for p in self.alive:
            factories = p.units.owned(UnitType.FACTORY)
            if not factories:
                continue
            rate = max(1, train_spawn_rate(factories))
            for st_ in self.rail.stations:
                if st_.owner != p.pid or st_.unit.utype is not UnitType.FACTORY:
                    continue
                last = self._station_fired.get(st_.tile, -10_000)
                if self.tick_count - last < C.TRAIN_STATION_COOLDOWN_TICKS:
                    continue
                if not any(self.rng.randrange(rate) == 0
                           for _ in range(max(1, st_.unit.level))):
                    continue
                t = self.rail.dispatch(self.gmap, self.diplomacy, p.pid,
                                       self.rng, src=st_)
                if t is not None:
                    self._station_fired[st_.tile] = self.tick_count
                    self.trains.append(t)

        # 여정 중에 역이 부서졌는지 볼 때 쓴다(원본 `stations[1].isActive()`).
        live = {st_.tile: st_.owner for st_ in self.rail.stations}
        still: list[Train] = []
        for t in self.trains:
            owner = self.players.get(t.owner)
            if owner is None or not owner.alive:
                continue
            t.advance()
            while t.leg_done(self.gmap):
                stop = t.stops[0]
                # ⚠ **닿기 전에** 확인한다(원본 `canTradeWithDestination` 이
                # `getNextTile` 의 맨 앞에 있다). 역이 사라졌거나 금수면 여정이
                # 거기서 끝나고, 그 역에서는 아무도 벌지 않는다.
                if live.get(stop.tile) != stop.owner or not self._train_may_stop(
                        t.owner, stop.owner):
                    t.stops.clear()
                    break
                self._train_stop(t, stop)
                t.begin_next_leg(self.gmap)
            if t.stops:
                still.append(t)
        self.trains = still

    def _train_may_stop(self, train_owner: int, station_owner: int) -> bool:
        """`TrainStation.tradeAvailable` — **내 역은 언제나 선다.** 남의 역은
        금수가 없어야 한다."""
        return (train_owner == station_owner
                or self._can_trade(train_owner, station_owner))

    def _train_stop(self, t: Train, stop) -> None:
        """`TradeStationStopHandler.onStop` — **정거장마다** 돈이 오간다.

        ⚠ **역 주인도 같은 액수를 받는다**(자기 역이 아닐 때). 이게 §5.60 의
        "남의 역에 닿으면 2.5배"의 뒷면이다 — 원본에서는 **남이 내 역에 들르는
        것도 수입**이라 철도를 깐 나라끼리 서로 이득이다. 우리는 기차 주인에게만
        줘서 그 유인이 절반이었다(§5.70, 이식 누락 쉰).

        ⚠ 공장 역은 **안 판다**(`FactoryStopHandler` 가 빈 함수다). 방문 수도
        공장에서는 안 오른다 — 그래서 공장만 잔뜩 이은 노선으로 페널티를 피하며
        벌 수는 없다."""
        if not stop.trade:
            return
        # 값은 **오르기 전의** 방문 수로 매긴다 — 원본도 `onStop` 을 부른 **뒤에**
        # `_tradeStopsVisited++` 한다. 첫 정거장이 페널티 없이 만액인 이유다.
        rel = self.rail.relation(self.diplomacy, t.owner, stop.owner)
        gold = train_gold(rel, t.cities_visited)
        t.cities_visited += 1
        host = self.players.get(stop.owner)
        if stop.owner != t.owner and host is not None and host.alive:
            host.gold += gold
            self.note_gold_gain(host.pid, gold)
        owner = self.players[t.owner]
        owner.gold += gold
        self.note_gold_gain(owner.pid, gold)

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

    def transitive_targets_of(self, pid: int) -> list[int]:
        """`transitiveTargets()` — **내 표적 + 동맹들의 표적**.

        표적 지정은 동맹에게 보내는 부탁이라(§5.27), 찍은 쪽에게만 보이면 절반만
        도는 규칙이다. **동맹이 찍은 것이 내 화면에도 떠야 같이 친다.**

        ⚠ 전이는 **한 단계**다(원본과 같다) — 동맹의 동맹까지 따라가지 않는다."""
        out = list(self.targets_of(pid))
        for ally in self.diplomacy.allies_of(pid):
            for t in self.targets_of(ally):
                if t not in out:
                    out.append(t)
        return out

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

    def ai_emoji(self, pid: int, to: int, pool: tuple[str, ...],
                 after_game_over: bool = False) -> bool:
        """AI 가 **먼저** 말을 건다.

        `shouldSendEmoji` 의 두 조건을 그대로 지킨다: 봇은 안 보내고, **받는 쪽이
        사람이 아니면 안 보낸다.** AI 끼리 주고받지 않는다는 뜻이라, 화면에 뜨는
        이모지는 전부 나에게 온 말이 된다.

        ⚠ **축하만 판이 끝난 뒤에도 나간다**(`congratulateWinner`). 원본은 승자가
        정해진 뒤에 보내므로 `over` 를 무조건 막으면 그 말이 영영 안 나온다.
        나머지 잡담은 여기서 멈추는 것이 맞다.
        """
        me, them = self.players.get(pid), self.players.get(to)
        if me is None or them is None:
            return False
        if self.over and not after_game_over:
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

    def ai_broadcast(self, pid: int, pool: tuple[str, ...]) -> bool:
        """AI 가 **전체에 대고** 하는 말(`sendEmoji(AllPlayers, ...)`).

        ⚠ **30초 제한(`ai_may_speak`)을 안 받는다.** 원본 `shouldSendEmoji` 가
        받는 쪽이 `AllPlayers` 면 맨 앞에서 true 를 돌려주기 때문이다. 개인에게
        거는 말만 제한을 받는다 — 비명(`EMOJI_OVERWHELMED`)이 제한에 걸려 안
        나가면 사람은 어디가 무너지는지 영영 알 수 없다.

        받는 사람이 없으면(헤드리스) 아무 일도 안 한다."""
        me = self.players.get(pid)
        if me is None or not me.alive or self.over:
            return False
        if me.kind == "bot":
            return False
        text = self.rng.choice(pool)
        sent = False
        for q in self.alive:
            if q.kind != "human" or q.pid == pid:
                continue
            self.emit(EventKind.CHAT, who=q.pid, other=pid, text=text)
            sent = True
        return sent

    # --- 기부 -------------------------------------------------------------

    def can_donate(self, pid: int, to: int) -> bool:
        """`canDonateGold` / `canDonateTroops` — **친한 사이에게만, 10초에 한 번.**

        ⚠ 이식 누락 마흔넷(§5.63). 우리는 골드/병력만 확인하고 있었다. 그래서
        **적에게도 줄 수 있었고 같은 tick 에 여러 번 줄 수 있었다.**

        둘 다 중요하다:

        - **친한 사이 제한**이 없으면 적에게 돈을 뿌려 관계를 살 수 있다. 기부는
          관계를 올리는 수단인데(§P3), 그 관계가 **이미 친해야** 쓸 수 있는 것이
          원본의 구조다.
        - **쿨다운**이 없으면 덩어리 크기 규칙(§P3)이 무의미해진다 — 한 번에
          `GOLD_CHUNK_SIZE` 만큼만 관계가 오르는데, 같은 tick 에 100번 나눠
          보내면 100번 오른다.
        """
        a, b = self.players.get(pid), self.players.get(to)
        if a is None or b is None or pid == to:
            return False
        if not a.alive or not b.alive:
            return False
        if not self.diplomacy.is_friendly(pid, to):
            return False
        last = self._donated_at.get((pid, to))
        return last is None or self.tick_count - last >= C.DONATE_COOLDOWN_TICKS

    def donate_gold(self, pid: int, to: int, gold: int) -> bool:
        a, b = self.players.get(pid), self.players.get(to)
        if a is None or b is None or pid == to or gold <= 0 or a.gold < gold:
            return False
        if not self.can_donate(pid, to):
            return False
        self._donated_at[(pid, to)] = self.tick_count
        a.gold -= gold
        b.gold += gold
        self.note_gold_gain(b.pid, gold)
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
        """`DonateTroopsExecution`.

        ⚠ **이식 누락 쉰셋.** 우리는 액수와 무관하게 +50 을 줬고(주석에 그렇게
        적어 두기까지 했다), 받는 쪽 **상한을 넘겨서도** 밀어 넣을 수 있었다.
        원본은 둘 다 막는다:

        - **넘치는 만큼은 애초에 안 간다** — `min(troops, 상한 − 현재)`. 상한에
          붙은 상대에게 보내면 아무 일도 안 일어난다(관계도 안 오른다).
        - **문턱을 넘어야 관계가 오른다**(`getMinTroopsForRelationUpdate`).
          원본 주석: *"1% 만 보내 좋은 관계를 사는 것을 막는다."* 골드 쪽의
          덩어리 규칙(§P3)과 같은 목적인데, 병력에는 그게 통째로 없었다."""
        a, b = self.players.get(pid), self.players.get(to)
        if a is None or b is None or pid == to or troops <= 0 or a.troops < troops:
            return False
        if not self.can_donate(pid, to):
            return False
        # 받는 쪽 여유분까지만 간다. 여유가 없으면 **보내지 못한다**(원본은
        # `init` 에서 `active = false` 로 실행 자체를 접는다).
        room = b.max_troops(self.tiles(to)) - b.troops
        troops = min(troops, room)
        if troops <= 0:
            return False
        self._donated_at[(pid, to)] = self.tick_count
        a.troops -= troops
        b.troops += troops
        self.emit(EventKind.DONATION_SENT, who=pid, other=to, amount=troops)
        self.emit(EventKind.DONATION_RECEIVED, who=to, other=pid, amount=troops)
        enough = troops >= troop_donation_min(b.max_troops(self.tiles(to)),
                                              self.difficulty, self.rng)
        if enough:
            self.relate(to, pid, C.REL_TROOP_DONATION)
        # **적으면 적다고 말한다** — 골드 쪽(§5.64)과 같다. 답이 없으면 준 사람은
        # 문턱을 넘었는지 알 방법이 없다.
        if b.kind == "nation":
            self.ai_emoji(to, pid,
                          emoji_mod.LOVE if enough else emoji_mod.DONATION_TOO_SMALL)
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
            # ⚠ **남의 건물도 자리를 막는다**(§5.86). 원본 `nearbyUnits` 가
            # 주인을 안 가린다 — 국경 근처는 내 땅이어도 적 도시가 15칸 안일
            # 수 있고, 전에는 거기에 붙여 지을 수 있었다.
            return find_spot(self.gmap, pid, near,
                             all_structure_tiles(self.alive), utype=utype)
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
            # ⚠ **레벨을 올리기 전에** 지금 사거리를 읽는다(§5.82). 올린 뒤에
            # 읽으면 이미 새 레벨 값이라 "서서히 는다"가 그 자리에서 끝난다.
            prev_range = (dynamic_sam_range(unit, self.tick_count)
                          if unit.utype is UnitType.SAM_LAUNCHER else 0.0)
            unit.level += 1
            # `UnitImpl.increaseLevel` — 사일로·SAM 은 **새 관이 재장전부터
            # 시작한다.** 올리자마자 한 발 더 쏘게 두면 업그레이드가 즉발 화력이
            # 되어 버린다.
            if unit.utype in (UnitType.MISSILE_SILO, UnitType.SAM_LAUNCHER):
                unit.fire(self.tick_count)
            if unit.utype is UnitType.SAM_LAUNCHER:
                # 사거리는 **서서히** 오른다(§5.82). 올리기 직전의 사거리에서
                # 시작한다 — 연달아 올리면 그 중간값에서 이어진다.
                unit.upgrade_from = prev_range
                unit.upgrade_started = self.tick_count
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

    def max_bulk_nuke(self, pid: int, utype: UnitType) -> int:
        """`maxBulkAmount` + **발사관 상한**. 한 번에 살 수 있는 핵 수다.

        원본은 겹쳐 사는 것을 **원자탄에만** 연다(`isStackableNuke`) — 수폭·MIRV
        는 한 발씩이다. 상한이 둘인 것이 핵심이다: 골드로 살 수 있는 수와
        `readyMissileCount()`(§5.34 의 발사관) 중 **작은 쪽**."""
        p = self.players.get(pid)
        if p is None:
            return 0
        best = 0
        for n in range(1, C.MAX_UPGRADE_AMOUNT + 1):
            if p.units.bulk_cost(utype, n) > p.gold:
                break
            best = n
        return min(best, self.ready_missiles(pid))

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
        self._expire_alliance_requests()
        self._expire_embargoes()
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
        # 땅이 넘어간 뒤에 정리한다 — 공격·핵·썩음이 전부 끝난 자리를 본다
        self._reassign_lost_structures()
        self._absorb_enclaves()
        self._tick_clock()
        self._check_end()

    def _expire_alliance_requests(self) -> None:
        """20초가 지난 동맹 요청을 거절 처리한다(§5.73). 거절 소식도 그대로 나간다 —
        원본도 `req.reject()` 를 부르므로 받는 쪽이 결과를 본다."""
        for requestor, recipient in self.diplomacy.expire_requests(self.tick_count):
            self.emit(EventKind.ALLIANCE_REJECTED, who=requestor, other=recipient)

    def _expire_embargoes(self) -> None:
        """공격이 자동으로 건 금수는 5분 뒤 스스로 풀린다(`PlayerExecution`).

        수동 금수는 여기서 절대 안 풀린다 — 판단은 `expire_embargoes` 안에 있다."""
        self.diplomacy.expire_embargoes(self.tick_count)

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
            self._tile_changed[attacker] = self.tick_count
            self._tile_changed[target] = self.tick_count
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
        self.note_gold_gain(winner.pid, taken)
        loser.gold = 0                 # `removeGold(gold)` — 언제나 전액이 빠진다
        if taken:
            self.emit(EventKind.GOLD_FROM_CONQUEST, who=winner.pid,
                      other=loser.pid, amount=taken)

    def _reassign_lost_structures(self) -> None:
        """`PlayerExecution.tick` 앞부분 — **땅을 잃으면 건물도 잃는다.**

        ⚠ 이식 누락 서른일곱. 우리는 칸 주인만 바꾸고 건물은 그대로 뒀다. 그래서
        영토를 통째로 뺏겨도 **도시가 원래 주인 것으로 남아 병력 상한과 수입을
        계속 냈다**(실측으로 확인). §5.56 의 썩음이 만든 낙진 위 건물도 마찬가지다 —
        원본 주석이 *"Anything built on it is deleted by PlayerExecution"* 이라고
        그 처리를 여기로 미뤄 두고 있었다.

        규칙이 종류마다 다르다:

        - 칸이 **중립**이 되면(낙진·썩음) 건물은 **사라진다.**
        - 칸이 **남의 것**이 되면 그 사람이 **가져간다.**
        - 단 **방어초소만 부서진다** — 뺏은 쪽이 남의 방어선을 그대로 쓰면
          국경이 영영 안 밀린다.
        """
        for p in list(self.alive):
            lost = [u for u in p.units.units
                    if u.active and u.utype in STRUCTURES
                    and int(self.gmap.owner[u.tile]) != p.pid]
            for u in lost:
                owner = int(self.gmap.owner[u.tile])
                new_owner = self.players.get(owner) if owner >= 0 else None
                if new_owner is None or not new_owner.alive:
                    u.active = False          # 중립이 된 땅 — 부서진다
                    continue
                if u.utype is UnitType.DEFENSE_POST:
                    u.active = False          # 방어선은 안 넘겨준다
                    continue
                p.units.units.remove(u)
                u.owner = new_owner.pid
                new_owner.units.units.append(u)
                new_owner.units.record_constructed(u.utype)

    def _absorb_enclaves(self) -> None:
        """`removeClusters` — **둘러싸인 영토는 흡수된다.**

        ⚠ 이식 누락 서른여덟. 우리에겐 이 규칙이 통째로 없어서, 남의 영토 안에
        갇힌 조각이 **영원히 남았다.** 갇힌 조각은 국경이 한 쪽뿐이라 공격 부대가
        거의 안 가므로 실제로는 지도에 점처럼 박힌 채 끝까지 살아 있는다.

        ⚠ **20 tick 에 한 번만 돈다**(원본 `ticksPerClusterCalc = 20`). 국경
        타일을 전부 묶는 계산이라 매 tick 돌리면 비싸다. 나라마다 시작 tick 을
        어긋나게 해 한 tick 에 몰리지 않게 한다(원본도 pid 해시로 흩는다)."""
        gm = self.gmap
        for p in list(self.alive):
            if (self.tick_count + p.pid) % C.ENCLAVE_CHECK_TICKS != 0:
                continue
            # ⚠ **영토가 안 바뀐 나라는 건너뛴다**(원본 `lastTileChange >=
            # lastCalc`). 이걸 빼면 판 시간의 절반이 여기로 간다(실측:
            # 138ms/tick 중 대부분). 대부분의 나라는 대부분의 20 tick 동안
            # 국경이 그대로다.
            # ⚠ 이 관문 자체는 **변이로 안 잡힌다. 정상이다** — 지워도 결과가
            # 같고 느려질 뿐이다(순수 성능). 다만 **시각을 찍는 쪽**을 빠뜨리면
            # 규칙이 조용히 안 돌므로 그쪽은 테스트로 못 박아 뒀다.
            last = self._enclave_checked.get(p.pid, -1)
            if self._tile_changed.get(p.pid, 0) < last:
                continue
            self._enclave_checked[p.pid] = self.tick_count
            owned = [int(t) for t in gm.owned_refs(p.pid)]
            if not owned:
                continue
            border = enclave.border_tiles(gm, p.pid, owned)
            if not border:
                continue
            groups = enclave.clusters(gm, border)
            if not groups:
                continue
            biggest = max(range(len(groups)), key=lambda i: len(groups[i]))
            for i, group in enumerate(groups):
                # 가장 큰 덩어리는 **적이 정확히 하나**여야 한다(원본이 그렇다)
                enemies = enclave.surrounded_by(gm, p.pid, group,
                                                single_enemy=(i == biggest))
                if enemies is None:
                    continue
                captor = enclave.capturing_player(gm, p.pid, group, self.attacks)
                if captor is None:
                    continue
                if not self.diplomacy.is_friendly(p.pid, captor):
                    self._absorb_cluster(p.pid, captor, group)
                    break                     # 영토가 통째로 넘어갔을 수 있다

    def _absorb_cluster(self, pid: int, captor: int, group: list) -> None:
        """덩어리가 얹힌 **영토 전체**를 넘긴다.

        ⚠ 넘기기 전에 `is_enclosed` 로 한 번 더 본다. 국경 덩어리 검사는 국경
        타일만 봤는데 실제로 넘어가는 것은 그 덩어리가 얹힌 땅 전체라, 넓은
        제국 한가운데 뚫린 구멍을 감싼 덩어리가 검사를 통과할 수 있다(원본 주석)."""
        gm = self.gmap
        start = group[0]
        if int(gm.owner[start]) != pid:
            return
        if not enclave.is_enclosed(gm, pid, start):
            return
        tiles = enclave.territory_from(gm, pid, start)
        if not tiles:
            return
        taker = self.players.get(captor)
        if taker is None or not taker.alive:
            return
        for t in tiles:
            gm.owner[t] = captor
        self._counts[pid] = max(0, self._counts.get(pid, 0) - len(tiles))
        self._counts[captor] = self._counts.get(captor, 0) + len(tiles)
        self._tile_changed[pid] = self.tick_count
        self._tile_changed[captor] = self.tick_count
        # 영토가 통째로 넘어갔으면 정복 경로(골드 이전 · 건물 이전)를 태운다.
        # `_maybe_absorb` 가 남은 타일 수를 보고 판단한다.
        self._maybe_absorb(captor, pid)

    def _tick_clock(self) -> None:
        """둠스데이 클락 — 원본의 진짜 종료 규칙. 기본은 꺼져 있다(원본도 그렇다)."""
        if not self.clock.cfg.enabled:
            return
        elapsed = self.elapsed
        team_game = any(t is not None for t in self.diplomacy.teams.values())
        # ⚠ **봇은 클락에 안 걸린다**(§5.56). 원본이 `players().filter(p =>
        # p.type() !== PlayerType.Bot)` 로 후보를 추린다. 봇 400 을 같이 넣으면
        # 요구 점유율(전체의 2~35%)에 못 미치는 봇이 전부 표시돼 판이 클락으로
        # 정리돼 버린다 — 원본에서 클락은 **나라들의 교착 해결기**다.
        contenders = [p for p in self.alive if not p.is_bot]
        # ⚠ 바의 분모는 **낙진을 뺀 땅**이다(원본: `numLandTiles() -
        # numTilesWithFallout()`). 썩음이 낙진을 만들므로 판이 갈수록 분모가
        # 줄고 바가 상대적으로 높아진다 — 전부 세면 후반에 클락이 헐거워진다.
        # `fallout` 이 없는 상태(옛 테스트가 만드는 최소 상태)도 견딘다
        burnt = int(self.fallout.mask.sum()) if self.fallout is not None else 0
        usable = self.gmap.land_count - burnt
        self.clock.update(elapsed, {p.pid: self.tiles(p.pid) for p in contenders},
                          max(1, usable), team_game)
        for p in contenders:
            if not p.alive:
                continue
            frac = self.clock.drain_fraction(p.pid, elapsed)
            cap = p.max_troops(self.tiles(p.pid))
            if frac > 0.0:
                floor = self.clock.troop_floor_fraction(p.pid, elapsed) * cap
                # ⚠ **상한에 곱한다. 현재 병력이 아니다**(§5.56). 현재 병력에
                # 곱하면 줄어들수록 유출이 줄어 수입과 균형을 이루고 멈춘다 —
                # 실측으로 62,139 에서 멎어 바닥(5,100)에 영영 안 닿았다.
                chunk = cap * frac * C.TICK_DT
                p.troops = max(floor, p.troops - chunk)
            # ⚠ **썩음은 마감이 지나서가 아니라 바닥에 닿아서 시작한다**(§5.56).
            # 반격 창에서 병력을 지켜 낸 나라는 아직 안 썩는다.
            # 함대도 같은 경사로 닳는다 — **가라앉히지 않고 바닥까지 두들긴다.**
            # 회복 억제(`_heal_warship`)만 옮겨 놓고 정작 깎는 쪽이 없었다(§5.56).
            wfrac = self.clock.warship_drain_fraction(p.pid, elapsed)
            if wfrac > 0.0:
                # ⚠ 바닥도 피해량도 **배마다 다르다** — 원본이 `ws.maxHealth()`
                # 를 두 번 다 쓴다. 기본 1000 으로 통일하면 베테랑 배는 상대적으로
                # 덜 닳고, 바닥이 자기 최대 체력의 훨씬 아래가 된다(§5.75).
                for w in self.warships:
                    if w.owner != p.pid:
                        continue
                    ship_floor = (w.max_health
                                  * self.clock.cfg.drain_floor_percent / 100.0)
                    if w.health > ship_floor:
                        dmg = w.max_health * wfrac * C.TICK_DT
                        w.health = max(ship_floor, w.health - dmg)

            if self.clock.rotting(p.pid, elapsed, p.troops, cap):
                self._rot_step(p.pid, elapsed)
            elif p.pid in self._rot:
                del self._rot[p.pid]          # 회복하면 진행이 통째로 사라진다

    def _rot_step(self, pid: int, elapsed: float) -> None:
        """이번 초에 먹을 만큼 영토를 썩힌다 — 원본 `DoomsdayClockExecution.rot`.

        **초에 한 번**만 돈다(원본이 `secondsUnder` 로 세고 쿼터도 초당이다).
        tick 마다 돌리면 10배 빨리 먹어 마감이 15초가 된다."""
        if self.tick_count % C.TICK_HZ != 0:
            return
        owned = [int(t) for t in self.gmap.owned_refs(pid)]
        if not owned:
            self._wipe(pid)
            return
        since = self.clock.marked_at.get(pid, elapsed)
        seconds_under = elapsed - since
        state = self._rot.get(pid)
        if state is None:
            state = RotState(self.tick_count, len(owned))
            self._rot[pid] = state
        quota = self.clock.rot_quota(len(owned), seconds_under)
        specks = self.clock.rot_specks(state.held,
                                       self.tick_count - state.since_tick)
        budget = min(len(owned), max(quota, specks))
        border = self._border_tiles(pid, owned)
        eaten = rot_tiles(self.gmap, pid, owned, border, state, budget, specks)
        for t in eaten:
            self.gmap.owner[t] = -1
            self._counts[pid] = max(0, self._counts.get(pid, 0) - 1)
        if eaten:
            self._tile_changed[pid] = self.tick_count
        # ⚠ **썩은 칸은 낙진이다.** 원본 주석: *"Wasteland, not a prize"* —
        # 그냥 중립으로 두면 가장 큰 이웃이 공짜로 먹는다.
        if eaten:
            self.fallout.add(eaten)
        if self._counts.get(pid, 0) <= 0:
            self._wipe(pid)

    def _border_tiles(self, pid: int, owned: list[int]) -> set[int]:
        """내 칸 중 **남과 맞닿은** 것들. 안쪽부터 뚫기 위해 필요하다."""
        gm = self.gmap
        out = set()
        for t in owned:
            for n in gm.neighbors(t):
                if int(gm.owner[n]) != pid:
                    out.add(t)
                    break
        return out

    def _wipe(self, pid: int) -> None:
        """영토가 통째로 썩어 사라진다 — 아무도 가져가지 않고 중립이 된다."""
        refs = self.gmap.owned_refs(pid)
        if len(refs):
            self.gmap.owner[refs] = -1
        self._counts[pid] = 0
        self._tile_changed[pid] = self.tick_count
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
        # ⚠ **원본은 둘 다 돈다**(§5.61). `GameRunner` 가 `WinCheckExecution` 을
        # 항상 등록하고, 클락은 켜졌을 때 **추가로** 등록한다 — 클락은 교착을
        # 푸는 장치이지 승리 판정을 대신하는 것이 아니다. 우리는 클락이 켜지면
        # 이쪽을 껐었다. 이제 둘 다 본다.
        top = max(alive, key=lambda p: self.tiles(p.pid))
        # ⚠ 분모는 **낙진을 뺀 땅**이다(원본 `numLandTiles() -
        # numTilesWithFallout()`). `share()` 는 전체 육지로 나누므로 여기서
        # 쓰면 안 된다 — 핵이 많이 터진 판일수록 승리가 멀어진다(§5.61).
        burnt = int(self.fallout.mask.sum()) if self.fallout is not None else 0
        usable = max(1, self.gmap.land_count - burnt)
        if self.tiles(top.pid) / usable >= C.DOMINATION_TILE_RATIO:
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

    def neutral_borders(self, pid: int) -> tuple[bool, bool]:
        """내 국경에 닿은 중립 땅이 **깨끗한가 · 낙진인가**를 따로 돌려준다.

        ⚠ 원본 AI 는 이 둘을 다르게 다룬다(`AiAttackBehavior`). 평소의 중립 확장은
        *낙진이 없는* 중립만 노리고(`borderHasNonNukedTerraNullius`), 낙진 땅은
        난이도별 전략 목록의 `nuked` 자리에서만 친다. 우리는 `border_targets` 의
        `None` 하나로 뭉뚱그려 **핵이 터진 자리로도 그냥 밀고 들어갔다** —
        낙진은 방어가 크게 붙으므로(§핵) 그쪽으로 확장하는 것은 손해다.

        `border_targets` 와 같은 numpy 이웃 계산을 쓴다."""
        gm = self.gmap
        cand = self._border_neighbours(pid)
        if cand is None:
            return False, False
        neutral = cand[(gm.owner[cand] < 0) & gm.passable_mask()[cand]]
        if not len(neutral):
            return False, False
        if self.fallout is None:      # 최소 상태로 만든 옛 테스트 (2279줄과 같은 이유)
            return True, False
        dirty = self.fallout.mask[neutral]
        return bool((~dirty).any()), bool(dirty.any())

    def _border_neighbours(self, pid: int) -> "np.ndarray | None":
        gm = self.gmap
        w, size = gm.width, gm.size
        refs = gm.owned_refs(pid)
        if not len(refs):
            return None
        x = refs % w
        return np.concatenate((
            refs[x > 0] - 1,
            refs[x < w - 1] + 1,
            refs[refs >= w] - w,
            refs[refs < size - w] + w,
        ))

    def border_targets(self, pid: int) -> set[int | None]:
        """닿을 수 있는 상대들. AI 가 쓴다. None 은 중립.

        **numpy 로 편다.** 파이썬 루프로 내 타일마다 이웃을 보면 영토가 17만 칸일 때
        한 번에 119ms 가 든다(실측, cProfile) — 원본 크기 지도에서 이 함수 하나가
        시뮬레이션 전체보다 6배 비쌌다. 배열을 네 방향으로 밀어 한 번에 본다."""
        gm = self.gmap
        w, size = gm.width, gm.size
        refs = gm.owned_refs(pid)
        if not len(refs):
            return set()
        # ⚠ **내 타일 수에 비례한다.** 전에는 지도 전체(200만 칸)를 네 방향으로
        # 밀어 열 번쯤 훑었다 — 실측(§5.50)에서 이 함수 하나가 판 시간의 33%,
        # 호출당 2.3ms 였다. 나라가 472명이면 1인당 영토는 평균 1,380칸이라
        # 이웃을 직접 세는 쪽이 자릿수로 싸다. `owned_refs` 의 전수 훑기 한 번만
        # 남는다.
        #
        # 파이썬 루프로 돌아가는 것이 **아니다.** 그건 17만 칸에서 119ms 였다.
        # 인덱스 산술을 numpy 로 한 번에 한다.
        x = refs % w
        cand = np.concatenate((
            refs[x > 0] - 1,               # 왼쪽
            refs[x < w - 1] + 1,           # 오른쪽 (x 경계를 안 넘는다)
            refs[refs >= w] - w,           # 위
            refs[refs < size - w] + w,     # 아래
        ))
        found = np.unique(gm.owner[cand][gm.passable_mask()[cand]])
        return {None if int(v) < 0 else int(v) for v in found if int(v) != pid}
