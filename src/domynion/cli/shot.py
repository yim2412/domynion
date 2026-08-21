"""판을 그림으로 찍는다 — 창 없이.

    python -m domynion.cli.shot --seed 1000 --at 0 120 300 600 --out shots/

헤드리스로 240판을 돌리다 "이 판은 왜 이렇게 끝났나" 를 볼 때, 창을 띄우는 것보다
프레임을 꺼내 보는 쪽이 빠르다. UI 가 서기 전까지는 이게 유일한 눈이다.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from ..ai import simple_ai
from ..core import constants as C
from ..core.engine import GameState
from ..ui.render import render


def capture(seed: int, players: int, at: list[float], out: Path,
            tile: int, prefix: str = "", map_name: str = "world") -> list[Path]:
    rng = random.Random(seed)
    st = GameState.new(players, rng, map_name=map_name, human=-1)
    bots = simple_ai.attach(st, rng)

    out.mkdir(parents=True, exist_ok=True)
    todo = sorted(at)
    saved: list[Path] = []

    def shoot(when: float) -> None:
        labels = {p.pid: f"{p.name} {st.share(p.pid) * 100:.0f}%"
                  for p in st.alive if st.tiles(p.pid) >= 30}
        top = max(st.players.values(), key=lambda p: st.tiles(p.pid))
        title = (f"{map_name} · seed {seed} · {when:.0f}초 · "
                 f"1위 {top.name} {st.share(top.pid) * 100:.1f}% · "
                 f"병력 {top.troops:,.0f}")
        img = render(st.gmap, scale=tile, seed=seed, labels=labels, title=title)
        path = out / f"{prefix}s{seed}_t{int(when):04d}.png"
        img.save(path)
        saved.append(path)

    while todo and not st.over:
        if st.elapsed >= todo[0]:
            shoot(todo.pop(0))
            continue
        st.tick()
        for b in bots:
            b.update(st, C.TICK_DT)
    if todo:                      # 판이 먼저 끝났으면 마지막 상태를 한 장 남긴다
        shoot(st.elapsed)
    return saved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Domynion 판 스크린샷")
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--players", type=int, default=4)
    ap.add_argument("--at", type=float, nargs="+", default=[0, 120, 300, 600],
                    help="이 시각(초)들에 한 장씩 찍는다")
    ap.add_argument("--tile", type=int, default=2, help="타일 한 변의 픽셀")
    ap.add_argument("--map", dest="map_name", default="world")
    ap.add_argument("--out", type=Path, default=Path("shots"))
    args = ap.parse_args(argv)

    for p in capture(args.seed, args.players, args.at, args.out, args.tile,
                     map_name=args.map_name):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
