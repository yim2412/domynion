"""진입점.

    python -m domynion.ui.app --map world --players 4
    python -m domynion.ui.app --shot out.png --at 300      # 창 없이 한 장 찍는다

`--shot` 이 있는 이유: **창을 띄우지 않고 UI 를 검증하기 위해서다.** 오프스크린
플랫폼으로 실제 위젯을 그려 파일로 뽑으므로, 위젯 배치·국경선·라벨이 실제로 어떻게
나오는지 눈으로 확인할 수 있다. 창을 띄워야만 볼 수 있으면 자동 검증이 불가능하다.
"""

from __future__ import annotations

import argparse
import os
import random
import sys

from ..core import constants as C
from ..core.engine import GameState
from ..core.gamemap import DEFAULT_SIZE, SIZES

# 원본 싱글플레이 기본 구성(`SinglePlayerModal :: DEFAULT_OPTIONS`).
# 지도가 빽빽한 것이 이 게임의 기본 상태다 — 몇 명만 두면 전혀 다른 게임이 된다.
DEFAULT_BOTS = 400
DEFAULT_NATIONS = 72        # world manifest 의 나라 수
DEFAULT_DIFFICULTY = "easy"


def build_state(seed: int, players: int, map_name: str, human: int,
                clock: str | None, size: str,
                bots: int = 0) -> tuple[GameState, random.Random]:
    rng = random.Random(seed)
    # ⚠ 여기서 `kind` 를 덮어쓰면 안 된다. `new()` 가 나라·봇·사람을 이미 나눠
    # 놓는데, 전부 nation 으로 밀면 봇의 성격(동맹 다 받기·건물 지우기)이 사라진다.
    st = GameState.new(players, rng, map_name=map_name, human=human,
                       size=size, bots=bots)
    if clock:
        st.clock.cfg.enabled = True
        st.clock.cfg.speed = clock
    return st, rng


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Domynion")
    ap.add_argument("--map", dest="map_name", default="world")
    ap.add_argument("--size", choices=list(SIZES), default=DEFAULT_SIZE,
                    help="지도 해상도. map16x=1/16 · map4x=1/4 · map=원본. "
                         "클수록 원본 밸런스에 가깝지만 무겁다")
    ap.add_argument("--players", type=int, default=DEFAULT_NATIONS,
                    help="나라 수 (지도 manifest 의 실제 국가부터 채운다)")
    ap.add_argument("--bots", type=int, default=DEFAULT_BOTS,
                    help="부족(봇) 수. 원본 싱글 기본이 400 이다")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--human", type=int, default=0, help="사람이 잡을 pid")
    # 원본 싱글 기본이 easy 다(`DEFAULT_OPTIONS.selectedDifficulty`).
    # 난이도는 AI 의 공격 주기뿐 아니라 **사람을 얼마나 봐주는지**도 정한다.
    ap.add_argument("--difficulty", choices=list(C.DIFFICULTIES),
                    default=DEFAULT_DIFFICULTY)
    ap.add_argument("--clock", choices=["slow", "normal", "fast", "veryfast"])
    ap.add_argument("--shot", help="PNG 한 장을 찍고 끝낸다")
    ap.add_argument("--offscreen", action="store_true",
                    help="화면이 없는 환경에서 --shot 을 쓸 때. ⚠ 오프스크린 플랫폼은 "
                         "시스템 폰트를 못 본다(실측: 0개) — 글자가 전부 두부가 된다")
    ap.add_argument("--at", type=float, default=0.0,
                    help="--shot 과 함께: 이 시각(초)까지 돌린 뒤 찍는다")
    args = ap.parse_args(argv)

    if args.offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtWidgets import QApplication      # 플랫폼 지정 뒤에 import 한다

    from .main_window import MainWindow

    st, rng = build_state(args.seed, args.players, args.map_name,
                          args.human, args.clock, args.size, args.bots)
    app = QApplication(sys.argv[:1])
    # 전역 폰트를 먼저 정한다 — 위젯이 만들어진 뒤에 바꾸면 이미 잰 크기가 안 맞는다
    from PyQt6.QtGui import QFont

    from . import palette as P
    base = QFont()
    base.setFamilies(list(P.UI_FONT_FAMILIES))
    base.setPointSize(10)
    app.setFont(base)

    # 폰트가 하나도 없으면 글자가 전부 두부(□)로 나온다. 조용히 넘어가면 스크린샷을
    # 보고 UI 버그로 오해하게 된다 — 실제로 그렇게 한 번 헤맸다.
    from PyQt6.QtGui import QFontDatabase
    if not QFontDatabase.families():
        print("⚠ 폰트를 하나도 못 찾았다(오프스크린 플랫폼). 글자가 두부로 나온다.",
              file=sys.stderr)

    win = MainWindow(st, args.human, rng, args.difficulty)

    if args.shot:
        win.timer.stop()
        win.resize(1600, 900)
        win.show()
        app.processEvents()
        while st.elapsed < args.at and not st.over:
            st.tick()
            for b in win.bots:
                b.tick(st)
        win._tick()
        app.processEvents()
        win.grab().save(args.shot)
        print(f"{args.shot}  ({st.elapsed:.0f}초, "
              f"타일 {[st.tiles(p.pid) for p in st.players.values()]})")
        return 0

    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
