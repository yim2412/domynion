"""유닛 진행바 — 원본 `client/render/gl/passes/BarPass.ts`.

⚠ **§7.4 가 `client/render/` 를 "나머지는 WebGL" 로 넘긴 자리인데 이 파일은
CPU 에서 값을 계산한다.** 파일 머리말이 그렇게 적어 뒀다.

우리에게 없던 것 셋:
① **철거 예정이 화면에 아예 없었다** — 명령하고 30초 동안 계속 돌아가는데
   눌렀는지조차 알 수 없었다(`DELETION_MARK_DURATION_TICKS`).
② 건설은 **흐리게**(alpha)만 표시해 *얼마나 남았는지*가 없었다.
③ 재장전은 표시가 없었다. 값(`ready_missiles`)은 이미 있었는데 그리기만 없었다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from domynion.core import constants as C
from domynion.core.units import UNIT_INFO, Unit, UnitType
from domynion.ui.overlays import (BAR_KIND_CONSTRUCTION, BAR_KIND_DELETION,
                                  BAR_KIND_RELOAD, missile_readiness, unit_bar)


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def silo(level: int = 1, ticks_left: int = 0) -> Unit:
    u = Unit(utype=UnitType.MISSILE_SILO, owner=0, tile=0, level=level)
    u.ticks_left = ticks_left
    return u


# --- 재장전 ---------------------------------------------------------------

def test_a_full_silo_shows_no_bar():
    """⚠ **꽉 찼으면 안 그린다.** 늘 그리면 사일로마다 꽉 찬 막대가 붙어
    신호가 아니라 장식이 된다 — 원본도 `readiness < 1` 일 때만 값을 낸다."""
    assert missile_readiness(silo(), now=100) == 1.0
    assert unit_bar(silo(), now=100) is None


def test_a_just_fired_tube_starts_the_bar_near_empty():
    u = silo()
    u.fire(now=100)
    assert missile_readiness(u, now=100) == pytest.approx(0.0)
    kind, ratio = unit_bar(u, now=100)
    assert kind == BAR_KIND_RELOAD
    assert ratio == pytest.approx(0.0)


def test_the_bar_fills_continuously_not_in_steps():
    """⚠ **"준비된 관 수 / 레벨" 이 아니다.** 재장전 중인 관도 *진행한 만큼*
    더한다 — 그래야 "곧 쏠 수 있는가"를 알 수 있다.

    막지 않았으면 무엇이 일어났을 것인가: 계단으로 그리면 Lv1 사일로가 쿨다운
    내내 **0 에 붙어 있다가** 갑자기 1 이 된다."""
    u = silo()
    u.fire(now=0)
    half = C.SILO_COOLDOWN_TICKS // 2
    assert missile_readiness(u, now=half) == pytest.approx(0.5, abs=0.01)
    # 계단이면 여기가 0.0 이다.
    assert missile_readiness(u, now=half) > 0.0


def test_a_level_three_silo_counts_ready_tubes_and_partial_progress():
    """Lv3 에서 관 하나가 반쯤 찼으면 `2/3 + 0.5/3` 이다."""
    u = silo(level=3)
    u.fire(now=0)
    half = C.SILO_COOLDOWN_TICKS // 2
    assert missile_readiness(u, now=half) == pytest.approx(2 / 3 + 0.5 / 3,
                                                           abs=0.01)


def test_sam_reads_its_own_cooldown_constant(monkeypatch):
    """⚠ **값으로는 구별할 수 없는 배선이다.** 원본도 `SAMCooldown()` 과
    `SiloCooldown()` 이 **둘 다 90** 이라, 그냥 재면 어느 상수를 읽었는지 알 수
    없다. `engine.py` 도 같은 자리에 *"같은 상수를 쓴다고 같은 코드로 합치면 안
    되는 자리"* 라고 적어 뒀다.

    → **하나를 일부러 다른 값으로 바꿔서** 잰다. 그러지 않으면 SAM 이 사일로
    상수를 읽어도 테스트가 통과한다."""
    assert C.SAM_COOLDOWN_TICKS == C.SILO_COOLDOWN_TICKS, (
        "두 상수가 갈라졌다 — 이제 값으로 구별되므로 이 테스트를 단순하게 고칠 수 있다")
    monkeypatch.setattr(C, "SAM_COOLDOWN_TICKS", C.SILO_COOLDOWN_TICKS * 2)
    sam = Unit(utype=UnitType.SAM_LAUNCHER, owner=0, tile=0, level=1)
    sam.fire(now=0)
    at = C.SILO_COOLDOWN_TICKS                  # 사일로 기준으로는 꽉 찬 시각
    assert missile_readiness(sam, now=at) == pytest.approx(0.5, abs=0.01)
    # 사일로 상수를 읽었다면 여기가 1.0 이다.
    assert missile_readiness(sam, now=at) < 1.0


# --- 우선순위 -------------------------------------------------------------

def test_deletion_wins_over_everything_and_counts_down():
    """⚠ **거꾸로 줄어드는 막대다.** 다른 것과 섞이면 "차고 있다"와
    "사라지고 있다"를 구별할 수 없다."""
    u = silo()
    u.fire(now=0)                       # 재장전도 걸어 둔다
    u.deletion_at = 300 + C.DELETION_MARK_DURATION_TICKS
    kind, full = unit_bar(u, now=300)
    assert kind == BAR_KIND_DELETION
    assert full == pytest.approx(1.0)
    _, later = unit_bar(u, now=300 + C.DELETION_MARK_DURATION_TICKS // 2)
    assert later < full, "철거 막대는 줄어들어야 한다"


def test_deletion_wins_even_while_still_under_construction():
    """⚠ **이 상태는 실제로 만들 수 있다.** `can_delete_unit` 이 건설 중인지
    보지 않으므로, 짓는 중에 철거를 눌러 둘이 겹칠 수 있다.

    처음에 이 경우를 안 만들어서 *"철거보다 건설을 먼저 본다"* 변이가
    **살아남았다.** 원본 `unitBarProgress` 는 철거를 **무조건 먼저** 본다 —
    짓는 중인 것이 사라지는 중이면, 사람이 봐야 하는 것은 **사라진다는 사실**이다."""
    u = silo(ticks_left=UNIT_INFO[UnitType.MISSILE_SILO].construction_ticks // 2)
    u.deletion_at = 100 + C.DELETION_MARK_DURATION_TICKS
    kind, ratio = unit_bar(u, now=100)
    assert kind == BAR_KIND_DELETION
    assert ratio == pytest.approx(1.0)


def test_construction_wins_over_reload():
    u = silo(ticks_left=UNIT_INFO[UnitType.MISSILE_SILO].construction_ticks)
    u.fire(now=0)
    kind, ratio = unit_bar(u, now=0)
    assert kind == BAR_KIND_CONSTRUCTION
    assert ratio == pytest.approx(0.0)


def test_construction_progress_follows_ticks_left():
    total = UNIT_INFO[UnitType.MISSILE_SILO].construction_ticks
    assert total > 0, "건설 기간이 0 이면 이 테스트가 아무것도 안 잰다"
    u = silo(ticks_left=total // 2)
    kind, ratio = unit_bar(u, now=0)
    assert kind == BAR_KIND_CONSTRUCTION
    assert ratio == pytest.approx(0.5, abs=0.02)


def test_a_plain_building_has_no_bar():
    """발사관이 없는 건물은 다 지어지면 막대가 없다 — 아이콘만 남는다."""
    city = Unit(utype=UnitType.CITY, owner=0, tile=0, level=1)
    city.ticks_left = 0
    assert unit_bar(city, now=500) is None


# --- 실제로 그려지는가 (배선) ---------------------------------------------------
#
# ⚠ **계산이 맞아도 안 그려질 수 있다.** 위 테스트는 전부 순수 함수만 잰다.
# `map_widget` 이 `unit_bar` 를 부르지 않으면 전부 초록인 채 화면에는 아무것도
# 안 뜬다 — §5.114 의 *"드래프트가 한 번도 안 열렸다"* 와 같은 자리다.

def test_the_bar_is_actually_painted_on_the_map(qapp):
    """막대 색 픽셀이 실제로 찍히는지 본다.

    ⚠ **막지 않았으면 무엇이 일어났을 것인가** — 같은 판을 막대 없는 상태로도
    한 번 그려서 **그때는 그 색이 없다**는 것을 먼저 단언한다. 안 그러면 지도
    어딘가에 우연히 같은 색이 있어도 통과한다."""
    import random

    from PyQt6.QtGui import QImage, QPainter

    from domynion.core.engine import GameState
    from domynion.core.gamemap import GameMap
    from domynion.core.state import PlayerState
    from domynion.ui import palette as P
    from domynion.ui.map_widget import MapWidget

    gm = GameMap.from_rows(["." * 40] * 20)
    ps = {0: PlayerState(pid=0, name="P0", kind="human", start=gm.ref(10, 10))}
    for t in range(gm.size):
        gm.owner[t] = 0
    st = GameState(gmap=gm, players=ps, rng=random.Random(0))
    st._counts = {0: gm.size}
    st.tick_count = 500

    u = Unit(utype=UnitType.MISSILE_SILO, owner=0, tile=gm.ref(10, 10), level=1)
    u.ticks_left = 0
    ps[0].units.units.append(u)

    w = MapWidget(st)
    w.resize(400, 300)
    w.zoom = 8.0                      # UNIT_MIN_ZOOM(0.45) 보다 훨씬 크게
    w.offset.setX(0.0)
    w.offset.setY(0.0)

    want = P.BAR_COLOR["reload"]

    def bar_pixels() -> int:
        """막대 색 픽셀 수. **존재가 아니라 개수를 센다** — 존재만 보면
        *"폭이 비율을 안 탄다"* 변이가 살아남는다(실제로 살아남았다)."""
        img = QImage(w.size(), QImage.Format.Format_RGB32)
        p = QPainter(img)
        w.render(p)
        p.end()
        return sum(1 for x in range(img.width()) for y in range(img.height())
                   if QImage.pixelColor(img, x, y).getRgb()[:3] == want)

    assert bar_pixels() == 0, "막대가 없는데도 그 색이 화면에 있다 — 색을 바꿔야 한다"

    # ⚠ **막 쏜 순간(ratio 0)으로 재면 안 된다.** 채울 폭이 0 이라 배경만 그려지고,
    # 배선이 멀쩡해도 실패한다(처음에 그렇게 써서 한 번 헛짚었다).
    cd = C.SILO_COOLDOWN_TICKS
    u.missile_queue = [500 - cd // 4]              # 약 25% 찼다
    quarter = bar_pixels()
    assert quarter > 0, "`unit_bar` 가 배선되지 않았다 — 계산만 맞고 안 그려진다"

    u.missile_queue = [500 - cd * 3 // 4]          # 약 75% 찼다
    three_quarters = bar_pixels()
    assert three_quarters > quarter, (
        "폭이 비율을 안 탄다 — 25% 와 75% 가 같은 크기로 그려진다")
