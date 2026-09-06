"""결정론을 지키는 규칙 — **주소(`id()`)를 신원으로 쓰지 않는다** (§5.129).

2026-09-06 에 AI 가 배를 `id()` 로 알아보고 있었다. 배는 계속 죽고 새로 생기는데
죽은 배의 주소는 곧바로 재사용되므로, 새 배가 죽은 배의 표식을 물려받아 **AI 가
조용히 무시**했다. 게다가 어떤 주소가 재사용되는지는 **프로세스의 할당 이력**에
달려 있어, 같은 seed 로 돌린 45,000 tick 판이 seed 3 에서만 갈렸다 —
**계측을 얹는 것만으로도** 판이 달라졌다(진행줄에 누적 핵을 찍은 것이 전부였다).

이 프로젝트의 검증(골든 회귀 · A/B · 기준선 대조)이 전부 *"같은 seed 면 같은 판"*
위에 얹혀 있으므로, 그 성질은 테스트로 잠가 둔다.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "domynion"

# `id()` 를 쓰는 자리와 **왜 안전한가**. 근거는 전부 하나다 —
# *"그 집합/비교에 들어가는 객체가 **전부 동시에 살아 있다**"*.
# 살아 있는 객체끼리는 주소가 겹칠 수 없으므로 재사용 함정에 안 걸린다.
#
# ⚠ **새 자리가 생기면 이 표에 판정을 적어야 통과한다.** 판정 없이 늘어나는 것을
# 막는 것이 이 테스트의 전부다 — §5.129 는 정확히 그렇게 들어왔다.
JUDGED: dict[str, str] = {
    # --- core/engine.py -----------------------------------------------------
    "core/engine.py: if id(n) in shot:":
        "`shot` 은 같은 tick 의 `_sams_pick_targets()` 결과다. 그 안의 핵도, "
        "여기서 도는 핵도 전부 `self.nukes` 가 붙들고 있어 동시에 살아 있다.",
    "core/engine.py: if id(n) in taken or n.owner == p.pid:":
        "`taken` 은 이 호출 안에서만 산다. 후보는 전부 `self.nukes` 안이다.",
    "core/engine.py: taken.add(id(best))":
        "위와 같은 집합. 한 호출을 못 넘는다.",
    "core/engine.py: return id(n) in self._sams_pick_targets()":
        "그 자리에서 만든 집합과 즉시 견준다. ⚠ 다만 이 함수는 **테스트만 "
        "부르는데 부수효과가 있다**(SAM 을 실제로 발사시킨다) — §7.3 백로그.",
    "core/engine.py: p.units.units = [u for u in p.units.units if id(u) not in keep]":
        "`keep` 은 바로 윗줄에서 `gone` 으로 만든다. `gone` 리스트가 그 유닛들을 "
        "붙들고 있어 필터링이 끝날 때까지 아무도 안 죽는다.",
    # --- ai/nukes.py --------------------------------------------------------
    "ai/nukes.py: covering_ids = {id(u) for u in covering}":
        "`covering` 리스트가 살아 있는 동안만 쓴다. 계획 함수라 이 사이에 "
        "유닛을 만들거나 지우지 않는다.",
    "ai/nukes.py: if excluded is not None and id(u) in excluded:":
        "위 `covering_ids` 를 받는 자리. 같은 호출 안이다.",
}


def _uses_of_id() -> dict[str, str]:
    """`src/domynion` 에서 `id(...)` 를 **실제로 호출하는** 자리를 모은다.

    ⚠ `grep` 이 아니라 `ast` 로 찾는다 — 주석과 문자열 안의 `id(` 가 섞이면
    표가 오탐으로 불어나고, 그러면 아무도 안 읽는다."""
    found: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "id"):
                rel = path.relative_to(SRC).as_posix()
                found[f"{rel}: {lines[node.lineno - 1].strip()}"] = rel
    return found


def test_every_use_of_id_is_judged():
    """`id()` 를 쓰는 자리마다 *"왜 안전한가"* 가 적혀 있어야 한다.

    ⚠ **판정이 없는 새 자리가 곧 §5.129 다.** 그때도 코드는 멀쩡해 보였고
    테스트도 전부 초록이었다 — 드물게·늦게·크게만 나타났기 때문이다."""
    found = set(_uses_of_id())
    judged = set(JUDGED)

    new = found - judged
    assert not new, (
        "판정 없는 `id()` 사용이 생겼다. 각각에 대해 물을 것: "
        "**이 집합이 한 호출보다 오래 사는가? 그 사이에 객체가 죽을 수 있는가?** "
        "죽을 수 있으면 주소 대신 안정된 번호를 쓴다(`naval.py :: uid`).\n"
        + "\n".join(sorted(new)))

    gone = judged - found
    assert not gone, (
        "표에는 있는데 코드에 없다 — 지웠으면 표에서도 지운다:\n"
        + "\n".join(sorted(gone)))


def test_the_judgements_rest_on_objects_being_alive_at_the_same_time():
    """위 판정 전부가 **한 문장**에 기대고 있다: *살아 있는 객체끼리는 주소가
    겹칠 수 없다.* 그 문장과, 그 **반대쪽**(죽으면 곧 재사용된다)을 같이 잰다.

    ⚠ 반대쪽을 안 재면 이 테스트는 *"주소는 유일하다"* 만 말하게 되고, 그건
    §5.129 를 못 잡는다 — 거기서 문제였던 것은 유일성이 아니라 **재사용**이다."""
    from domynion.core.naval import TransportShip

    def boat(**kw):
        return TransportShip(owner=0, target=None, troops=1.0, path=[0], dst=0,
                             **kw)

    alive = [boat() for _ in range(50)]
    assert len({id(b) for b in alive}) == 50, "살아 있는 객체가 주소를 공유했다"

    # 반대쪽 — 죽은 자리는 다시 쓰인다. (할당기가 정하므로 *언젠가는* 걸린다.
    # 50번 안에 한 번도 안 나오면 그게 오히려 이상한 것이다.)
    seen: set[int] = set()
    reused = False
    for _ in range(50):
        b = boat()
        if id(b) in seen:
            reused = True
        seen.add(id(b))
        del b
    assert reused, (
        "죽은 객체의 주소가 한 번도 재사용되지 않았다 — 이 테스트가 재려던 "
        "위험이 이 파이썬에서는 다르게 나타난다는 뜻이다. §5.129 를 다시 읽을 것")


def test_ships_carry_a_number_not_an_address():
    """§5.129 의 수정이 서 있는지. **배는 번호로 알아본다.**"""
    from domynion.core.naval import TradeShip, TransportShip

    a = TransportShip(owner=0, target=1, troops=1.0, path=[0], dst=0)
    b = TransportShip(owner=0, target=1, troops=1.0, path=[0], dst=0)
    t = TradeShip(owner=0, src_port=0, dst_port=1, dst_owner=1, path=[0])
    assert len({a.uid, b.uid, t.uid}) == 3
    assert all(x.uid for x in (a, b, t)), "0 은 신원이 아니다"
