"""동맹 · 배신자 · 팀 · 금수 — openfront 의 외교 규칙.

핵심은 **`is_friendly` 하나로 수렴한다**는 것이다. 동맹이든 같은 팀이든 공격이
막히고, 공격 중에 친해지면 그 부대는 퇴각한다(`AttackExecution` 이 매 tick 확인).

배신은 값이 비싸다. 동맹을 깨면 **30초 동안 방어 ×0.5, 속도 페널티 ×0.8** 이 붙는다.
다만 상대가 이미 배신자면 낙인이 안 찍힌다 — 배신자를 버리는 것은 배신이 아니다.

원본: `AllianceImpl.ts`, `GameImpl.ts :: breakAlliance()`,
      `PlayerImpl.ts :: markTraitor() / isFriendly()`, `Config.ts :: allianceDuration()`
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as C


@dataclass
class Alliance:
    """두 사람 사이의 동맹 하나. 만료 시각을 tick 으로 들고 있다."""

    a: int
    b: int
    created_at: int
    expires_at: int
    _extend_a: bool = False
    _extend_b: bool = False

    def other(self, pid: int) -> int:
        return self.b if pid == self.a else self.a

    def involves(self, pid: int) -> bool:
        return pid in (self.a, self.b)

    def request_extension(self, pid: int) -> None:
        if pid == self.a:
            self._extend_a = True
        elif pid == self.b:
            self._extend_b = True

    @property
    def both_agreed_to_extend(self) -> bool:
        return self._extend_a and self._extend_b

    @property
    def only_one_agreed_to_extend(self) -> bool:
        """`onlyOneAgreedToExtend` — 한쪽만 동의한 상태. 봇은 이걸 보고 따라 동의한다."""
        return self._extend_a != self._extend_b


@dataclass
class Embargo:
    """금수 한 건. `PlayerImpl.embargoes` 의 `Embargo` 그대로다.

    ⚠ **임시와 수동은 다른 것이다.** 임시는 *공격이 자동으로 건 것*이라 5분 뒤
    스스로 풀리고 동맹이 맺어지면 즉시 풀린다. 수동(사람이 건 것 · 전체 금수 ·
    AI 가 관계를 보고 건 것)은 판 끝까지 남는다."""

    created_at: int
    temporary: bool


@dataclass
class Diplomacy:
    """판 전체의 외교 상태. `GameState` 가 하나 들고 있다."""

    alliances: list[Alliance] = field(default_factory=list)
    teams: dict[int, int | None] = field(default_factory=dict)
    traitor_since: dict[int, int] = field(default_factory=dict)     # pid -> tick
    betrayals: dict[int, int] = field(default_factory=dict)
    # 건 사람 -> {막힌 사람: 금수}. **값이 필요하다** — 임시(공격이 자동으로 건 것)와
    # 수동을 구분해야 만료와 자동 해제가 갈린다(§5.74).
    embargoes: dict[int, dict[int, Embargo]] = field(default_factory=dict)
    # 요청자 -> {받는 이: 건 tick}. **시각을 들고 있어야 만료를 잰다**(§5.73).
    # `x in pending[a]` 는 dict 에서도 그대로 도므로 읽는 쪽은 안 바뀐다.
    pending: dict[int, dict[int, int]] = field(default_factory=dict)

    # --- 조회 -------------------------------------------------------------

    def allied(self, a: int, b: int) -> bool:
        return any(al.involves(a) and al.involves(b) for al in self.alliances)

    def allies_of(self, pid: int) -> list[int]:
        """이 사람과 동맹인 상대들. **팀은 포함하지 않는다** — 원본 `allies()` 도
        동맹만 본다(팀은 `isFriendly` 쪽에서 따로 걸린다)."""
        return [al.other(pid) for al in self.alliances if al.involves(pid)]

    def same_team(self, a: int, b: int) -> bool:
        if a == b:
            return False
        ta, tb = self.teams.get(a), self.teams.get(b)
        return ta is not None and ta == tb

    def is_friendly(self, a: int, b: int) -> bool:
        """공격이 막히는 관계. 동맹이거나 같은 팀."""
        return a == b or self.allied(a, b) or self.same_team(a, b)

    def alliance_between(self, a: int, b: int) -> Alliance | None:
        for al in self.alliances:
            if al.involves(a) and al.involves(b):
                return al
        return None

    def is_traitor(self, pid: int, tick: int) -> bool:
        since = self.traitor_since.get(pid)
        return since is not None and tick - since < C.TRAITOR_DURATION_TICKS

    def traitor_remaining(self, pid: int, tick: int) -> int:
        since = self.traitor_since.get(pid)
        if since is None:
            return 0
        return max(0, C.TRAITOR_DURATION_TICKS - (tick - since))

    def embargoed(self, by: int, target: int) -> bool:
        return target in self.embargoes.get(by, ())

    # --- 행동 -------------------------------------------------------------

    def request(self, requestor: int, recipient: int, tick: int = 0) -> bool:
        """동맹 요청. 이미 친하거나 요청이 걸려 있으면 안 된다."""
        if self.is_friendly(requestor, recipient):
            return False
        if recipient in self.pending.get(requestor, {}):
            return False
        self.pending.setdefault(requestor, {})[recipient] = tick
        return True

    def expire_requests(self, tick: int) -> list[tuple[int, int]]:
        """`allianceRequestDuration` — **20초가 지난 요청은 자동 거절된다.**

        ⚠ 이식 누락 쉰다섯. 만료가 없어서 `pending` 이 판 끝까지 남았다. §5.68 의
        ✉(요청 중) 깃발이 한 번 켜지면 안 꺼졌고, AI 는 판 내내 같은 요청을
        다시 판단했다."""
        gone: list[tuple[int, int]] = []
        for requestor, targets in list(self.pending.items()):
            for recipient, at in list(targets.items()):
                if tick - at >= C.ALLIANCE_REQUEST_DURATION_TICKS:
                    del targets[recipient]
                    gone.append((requestor, recipient))
            if not targets:
                self.pending.pop(requestor, None)
        return gone

    def accept(self, recipient: int, requestor: int, tick: int) -> Alliance | None:
        if recipient not in self.pending.get(requestor, {}):
            return None
        del self.pending[requestor][recipient]
        return self.form(requestor, recipient, tick)

    def reject(self, recipient: int, requestor: int) -> None:
        self.pending.get(requestor, {}).pop(recipient, None)

    def form(self, a: int, b: int, tick: int) -> Alliance:
        al = Alliance(a=a, b=b, created_at=tick,
                      expires_at=tick + C.ALLIANCE_DURATION_TICKS)
        self.alliances.append(al)
        return al

    def break_alliance(self, breaker: int, other: int, tick: int) -> bool:
        """동맹 파기. **상대가 이미 배신자가 아닐 때만** 낙인이 찍힌다."""
        al = self.alliance_between(breaker, other)
        if al is None:
            return False
        if not self.is_traitor(other, tick):
            self.traitor_since[breaker] = tick
            self.betrayals[breaker] = self.betrayals.get(breaker, 0) + 1
        self.alliances.remove(al)
        return True

    def expire_due(self, tick: int) -> list[Alliance]:
        """만료된 동맹을 걷어낸다. 양쪽이 연장에 동의했으면 기간을 늘린다."""
        gone: list[Alliance] = []
        for al in list(self.alliances):
            if al.expires_at > tick:
                continue
            if al.both_agreed_to_extend:
                al.expires_at = tick + C.ALLIANCE_DURATION_TICKS
                al._extend_a = al._extend_b = False
                continue
            self.alliances.remove(al)
            gone.append(al)
        return gone

    def drop_player(self, pid: int) -> None:
        """탈락하면 그 사람이 낀 동맹과 요청이 전부 사라진다."""
        self.alliances = [al for al in self.alliances if not al.involves(pid)]
        self.pending.pop(pid, None)
        for s in self.pending.values():
            s.pop(pid, None)
        self.embargoes.pop(pid, None)
        for e in self.embargoes.values():
            e.pop(pid, None)

    def start_embargo(self, by: int, target: int, tick: int = 0, *,
                      temporary: bool = False) -> None:
        """`PlayerImpl.addEmbargo(other, isTemporary)`.

        ⚠ **이미 걸린 수동 금수는 덮어쓰지 않는다.** 원본이 그 자리에서 바로
        돌아선다(`if (embargo !== undefined && !embargo.isTemporary) return`).
        없으면 사람이 걸어 둔 금수를 공격 한 번이 임시로 바꿔 **5분 뒤 저절로
        풀린다** — 푼 적이 없는데 풀린다."""
        cur = self.embargoes.get(by, {}).get(target)
        if cur is not None and not cur.temporary:
            return
        self.embargoes.setdefault(by, {})[target] = Embargo(created_at=tick,
                                                            temporary=temporary)

    def stop_embargo(self, by: int, target: int) -> None:
        self.embargoes.get(by, {}).pop(target, None)

    def end_temporary_embargo(self, by: int, target: int) -> None:
        """`PlayerImpl.endTemporaryEmbargo` — **자동으로 걸린 것만** 푼다.

        원본 주석: *"Automatically remove embargoes only if they were
        automatically created."* 동맹을 맺었다고 상대가 손수 건 금수까지
        풀어 주지는 않는다."""
        cur = self.embargoes.get(by, {}).get(target)
        if cur is not None and not cur.temporary:
            return
        self.stop_embargo(by, target)

    def expire_embargoes(self, tick: int) -> list[tuple[int, int]]:
        """`PlayerExecution.tick` 의 임시 금수 만료(`temporaryEmbargoDuration`).

        ⚠ 원본은 **초과**(`>`)로 잰다 — 정확히 3,000 tick 되는 순간에는 아직
        살아 있다. 동맹 요청 만료(`>=`, §5.73)와 부호가 다르므로 옮길 때
        섞으면 안 된다."""
        gone: list[tuple[int, int]] = []
        for by, targets in list(self.embargoes.items()):
            for target, e in list(targets.items()):
                if e.temporary and tick - e.created_at > C.TEMPORARY_EMBARGO_TICKS:
                    del targets[target]
                    gone.append((by, target))
            if not targets:
                self.embargoes.pop(by, None)
        return gone
