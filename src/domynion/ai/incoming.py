"""`AiAttackBehavior.findIncomingAttackPlayer` — 나에게 들어오는 공격 중 **가장 큰
것**의 주인.

⚠ **한 곳에만 둔다.** 이 규칙이 나라 AI · 봇 AI · 핵 AI 세 자리에 따로 적혀
있었고, 핵 AI 의 사본만 **처음 만난 공격을 봇이든 아니든** 돌려주고 있었다
(§5.132). 원본은 셋 다 이 함수 하나를 부른다.
"""

from __future__ import annotations


def biggest_incoming_attacker(st, pid: int) -> "int | None":
    """친한 쪽은 빼고, **내가 봇이 아니면 봇의 공격은 무시한다.**

    봇이 봇의 공격을 세는 것은 원본의 필터가 `player.type() !== Bot` 일 때만
    걸리기 때문이다 — 내가 봇이면 그 필터가 아예 안 걸린다.

    친한 상대를 거르는 이유: *"가장 큰 공격"* 을 고를 때 동맹의 공격이 1등을
    차지하면 **반격 자체가 무산된다**(고르고 나서 실패하면 그 tick 은 반격을
    안 한 것이 된다)."""
    me = st.players.get(pid)
    if me is None:
        return None
    best, best_troops = None, 0.0
    for a in st.attacks:
        if a.target != pid or a.attacker is None:
            continue
        if st.diplomacy.is_friendly(pid, a.attacker):
            continue
        other = st.players.get(a.attacker)
        if other is None:
            continue
        if not me.is_bot and other.is_bot:
            continue
        if a.troops > best_troops:
            best, best_troops = a.attacker, a.troops
    return best
