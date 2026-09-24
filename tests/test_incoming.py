"""`ai/incoming.py` — 나라·봇·핵 AI 가 **같이 쓰는** *"가장 큰 들어오는 공격"*.

사본이 셋이었고 핵 AI 의 것만 틀렸다(§5.132). 한 곳으로 모은 뒤 규칙마다 여기서
잰다 — 호출하는 쪽 테스트만으로는 **친한 쪽 거르기**와 **남을 향한 공격 거르기**
를 아무도 안 쟀다(변이 둘이 살아남았다).
"""

from __future__ import annotations

from domynion.ai.incoming import biggest_incoming_attacker
from domynion.core.attack import Attack
from test_nuke_ai import state


def test_an_attack_on_someone_else_is_not_incoming():
    st = state(players=4)
    st.attacks.append(Attack(attacker=1, target=2, troops=9_000.0))
    assert biggest_incoming_attacker(st, 0) is None
    # 막지 않았으면 — 같은 공격이 나를 향하면 잡힌다
    st.attacks[0].target = 0
    assert biggest_incoming_attacker(st, 0) == 1


def test_an_ally_attack_does_not_win_the_biggest_slot():
    """동맹이 된 **같은 tick 안에는** 그 부대가 아직 목록에 있다(다음 공격 tick 에
    물러난다). 그 부대가 1등을 차지하면 반격 표적이 동맹이 된다."""
    st = state(players=4)
    st.attacks.append(Attack(attacker=1, target=0, troops=9_000.0))   # 동맹 — 더 크다
    st.attacks.append(Attack(attacker=2, target=0, troops=100.0))
    assert biggest_incoming_attacker(st, 0) == 1      # 막지 않았으면 — 동맹이 1등
    st.diplomacy.request(1, 0, st.tick_count)
    assert st.diplomacy.accept(0, 1, st.tick_count) is not None
    assert biggest_incoming_attacker(st, 0) == 2, "동맹의 공격을 반격 표적으로 골랐다"
