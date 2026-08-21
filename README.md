# Domynion

**실시간** 영토 확장 전략 게임. openfront.io 의 연속 확장에 **증강 드래프트**를 얹었다.

자원은 병력 하나. 영토가 병력을 낳고 병력이 영토를 넓힌다. 타일을 클릭하면 그 칸이
아니라 **그 칸의 소유자 전체**로 공격 부대가 번지고, 병력이 떨어지는 지점에서 멈춘다.
일정 시간마다 전원이 멈춰 증강 3장 중 하나를 고른다 — 그것이 빌드가 된다.

- 설계와 재개 지점: [`docs/design.md`](docs/design.md)

## 상태

**규칙이 돌아간다. 화면이 없다.** 판을 처음부터 끝까지 시뮬레이션할 수 있고, 밸런스를
수치로 잴 수 있다. 사람이 앉아서 할 수는 아직 없다.

| | |
|---|---|
| ✅ | `core/` 전부 · `ai/simple_ai.py` · `cli/play.py` · `tests/` |
| ⬜ | `ui/` (PyQt6) |

**밸런스는 아직 나쁘다.** 240판 기준선에서 절반(51%)이 시간 종료로 끝난다 —
서로 못 뚫고 중립만 먹는 모양이다. 자세한 수치와 다음 손댈 곳은
[`docs/design.md`](docs/design.md) 6·7절에 있다.

## 설치

```bash
pip install -e ".[dev,ui]"
```

## 실행

```bash
# 헤드리스로 판을 돌려 밸런스를 잰다 (240판 약 75초)
python -m domynion.cli.play --games 40 --players 4
python -m domynion.cli.play --games 240 --players 4 --seed 1000

# 테스트
python -m pytest tests -q
```

40판은 방향을 보는 용도이고, **채택 판단은 240판**으로 본다. 판당 노이즈가 크다.

## 지도만 보기

```bash
# 대륙 생성 결과를 ASCII 로 본다
python -c "
import random
from domynion.core.gamemap import GameMap
from domynion.core.constants import Terrain
CH = {Terrain.WATER:'~', Terrain.PLAINS:'.', Terrain.FOREST:'f',
      Terrain.HILLS:'n', Terrain.MOUNTAINS:'A'}
m = GameMap.generate(4, random.Random(7))
for y in range(0, m.height, 2):
    print(''.join(CH[m[(x,y)].terrain] for x in range(m.width)))
print(f'육지 {len(m.land_tiles())}/{m.width*m.height}')"
```

## 구조

```
src/domynion/
  core/          순수 로직 계층 — UI·네트워크 의존 없음
    constants.py   밸런스 수치 단일 출처
    gamemap.py     대륙 생성(노이즈 높이맵), 타일, 인접, 시작 배치
    augments.py    증강 카드·레벨·계수 합산·드래프트
    state.py       플레이어 상태 + 증강이 반영된 파생 수치
    attack.py      공격 부대와 연속 확장 (프론티어 BFS 큐)
    engine.py      tick(dt), 증강 정지, 탈락, 승리 판정
  ai/
    simple_ai.py   규칙 기반 AI — 반응 주기로 묶여 있다
  ui/            (예정) PyQt6 — 전체화면 지도 + 오버레이 HUD
  cli/
    play.py        헤드리스 시뮬레이션 (밸런스 측정)
```

계층 규칙: `core` 는 아무것도 import 하지 않는다. UI 와 AI 는 `core` 위에 나란히
얹으며 서로를 참조하지 않는다.

## 핵심 규칙 요약

```
자원은 병력 하나.  타일이 가진 숫자는 방어 계수 하나.

  병력을 떼어 국경에 붙이면 부대가 번지며 타일을 사들이고, 떨어지면 멈춘다.
  한 칸 비용은 지형과 방어측이 병력을 얼마나 채워 뒀는가로 정해진다.
  증강은 그 수치에 곱해지는 계수일 뿐, 새 규칙을 만들지 않는다(항해술만 예외).
```
