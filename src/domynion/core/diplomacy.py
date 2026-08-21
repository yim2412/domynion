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


@dataclass
class Diplomacy:
    """판 전체의 외교 상태. `GameState` 가 하나 들고 있다."""

    alliances: list[Alliance] = field(default_factory=list)
    teams: dict[int, int | None] = field(default_factory=dict)
    traitor_since: dict[int, int] = field(default_factory=dict)     # pid -> tick
    betrayals: dict[int, int] = field(default_factory=dict)
    embargoes: dict[int, set[int]] = field(default_factory=dict)    # pid -> 대상들
    pending: dict[int, set[int]] = field(default_factory=dict)      # 요청자 -> 받는 이들

    # --- 조회 -------------------------------------------------------------

    def allied(self, a: int, b: int) -> bool:
        return any(al.involves(a) and al.involves(b) for al in self.alliances)

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

    def request(self, requestor: int, recipient: int) -> bool:
        """동맹 요청. 이미 친하거나 요청이 걸려 있으면 안 된다."""
        if self.is_friendly(requestor, recipient):
            return False
        if recipient in self.pending.get(requestor, set()):
            return False
        self.pending.setdefault(requestor, set()).add(recipient)
        return True

    def accept(self, recipient: int, requestor: int, tick: int) -> Alliance | None:
        if recipient not in self.pending.get(requestor, set()):
            return None
        self.pending[requestor].discard(recipient)
        return self.form(requestor, recipient, tick)

    def reject(self, recipient: int, requestor: int) -> None:
        self.pending.get(requestor, set()).discard(recipient)

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
            s.discard(pid)
        self.embargoes.pop(pid, None)
        for s in self.embargoes.values():
            s.discard(pid)

    def start_embargo(self, by: int, target: int) -> None:
        self.embargoes.setdefault(by, set()).add(target)

    def stop_embargo(self, by: int, target: int) -> None:
        self.embargoes.get(by, set()).discard(target)
