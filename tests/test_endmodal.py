"""판이 끝났을 때 · 내가 죽었을 때 — 원본 `WinModal.ts`.

지금까지는 배너 한 줄뿐이라 **사람이 죽은 줄도 몰랐다.** 가만히 두면 2분여 만에
탈락하는데 화면에 아무 변화가 없었다.
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication                          # noqa: E402

from domynion.core import constants as C                          # noqa: E402
from domynion.core.buildings import DefensePostIndex              # noqa: E402
from domynion.core.engine import GameState, Victory               # noqa: E402
from domynion.core.gamemap import GameMap                         # noqa: E402
from domynion.core.state import PlayerState                       # noqa: E402
from domynion.ui.endmodal import COLUMNS, EndModal                 # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def state(n: int = 3) -> GameState:
    gm = GameMap.from_rows(["." * 40] * 4)
    players = {}
    for pid in range(n):
        for x in range(pid * 5, pid * 5 + 5):
            gm.owner[gm.ref(x, 0)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 5, 0))
        p.kind = "human" if pid == 0 else "nation"
        p.troops = 50_000.0
        p.gold = 1_000
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0))
    st._counts = {pid: 5 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def kill(st: GameState, pid: int) -> None:
    st.players[pid].alive = False
    st._counts[pid] = 0


# --- 언제 뜨는가 ------------------------------------------------------------

def test_nothing_shows_while_the_game_is_normal(qapp):
    m = EndModal(state(), 0)
    assert m.check() is False
    assert not m.isVisible()


def test_death_shows_before_the_game_is_over(qapp):
    """이게 이 화면의 핵심이다. 끝날 때까지 기다리면 언제 죽었는지 알 수 없다."""
    st = state()
    m = EndModal(st, 0)
    kill(st, 0)
    assert m.check() is True
    assert m.isVisible()
    assert "탈락" in m.title.text()
    assert not st.over, "판은 아직 안 끝났는데도 떴다"


def test_closing_it_lets_you_keep_watching(qapp):
    """막지 않았으면: 닫아도 다음 프레임에 다시 떠서 관전이 불가능하다."""
    st = state()
    m = EndModal(st, 0)
    kill(st, 0)
    m.check()
    m.hide()
    for _ in range(10):
        assert m.check() is False
    assert not m.isVisible()


def test_the_end_screen_still_comes_after_you_died(qapp):
    """탈락 화면을 봤다고 종료 화면까지 건너뛰면 안 된다."""
    st = state()
    m = EndModal(st, 0)
    kill(st, 0)
    m.check()
    m.hide()
    st.over = True
    st.winner = 1
    st.victory = Victory.CONQUEST
    assert m.check() is True
    assert "패배" in m.title.text()


def test_the_end_screen_shows_once_too(qapp):
    st = state()
    m = EndModal(st, 0)
    st.over = True
    st.winner = 0
    st.victory = Victory.CONQUEST
    assert m.check() is True
    m.hide()
    assert m.check() is False


def test_winning_says_so(qapp):
    st = state()
    m = EndModal(st, 0)
    st.over = True
    st.winner = 0
    st.victory = Victory.CONQUEST
    m.check()
    assert m.title.text() == "승리"


def test_a_draw_is_not_a_defeat(qapp):
    st = state()
    m = EndModal(st, 0)
    st.over = True
    st.winner = None
    m.check()
    assert "무승부" in m.title.text()


# --- 표 ---------------------------------------------------------------------

def test_every_player_gets_a_row_ordered_by_territory(qapp):
    st = state()
    for x in range(20, 35):                 # P2 를 가장 크게 만든다
        st.gmap.owner[st.gmap.ref(x, 1)] = 2
    st._counts[2] = 20
    m = EndModal(st, 0)
    st.over = True
    st.winner = 2
    m.check()
    # 헤더 1줄 + 나라 수만큼
    assert m.grid.rowCount() == len(st.players) + 1
    first = m.grid.itemAtPosition(1, 1).widget().text()
    assert first.startswith("P2"), "영토가 가장 큰 나라가 맨 위여야 한다"


def test_dead_players_are_marked(qapp):
    st = state()
    m = EndModal(st, 0)
    kill(st, 1)
    st.over = True
    st.winner = 0
    m.check()
    names = [m.grid.itemAtPosition(r, 1).widget().text()
             for r in range(1, len(st.players) + 1)]
    assert any("P1" in n and "✝" in n for n in names)


def test_betrayals_are_counted(qapp):
    """배신은 판이 끝난 뒤 되짚어 보는 값이다 — 안 세면 낙인이 흔적을 안 남긴다."""
    st = state()
    st.diplomacy.form(0, 1, tick=0)
    st.break_alliance(0, 1)
    m = EndModal(st, 0)
    st.over = True
    st.winner = 0
    m.check()
    col = [k for _, k in COLUMNS].index("betrayals")
    mine = m.grid.itemAtPosition(1, col).widget().text()
    rows = [m.grid.itemAtPosition(r, col).widget().text()
            for r in range(1, len(st.players) + 1)]
    assert "1" in rows, f"배신 1회가 표에 없다: {rows} (내 줄 {mine})"
