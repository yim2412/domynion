# Domynion

**실시간** 영토 확장 전략 게임. openfront.io 의 연속 확장에 **증강 드래프트**를 얹었다.

자원은 병력 하나. 영토가 병력을 낳고 병력이 영토를 넓힌다. 타일을 클릭하면 그 칸이
아니라 **그 칸의 소유자 전체**로 공격 부대가 번지고, 병력이 떨어지는 지점에서 멈춘다.
일정 시간마다 전원이 멈춰 증강 3장 중 하나를 고른다 — 그것이 빌드가 된다.

- 설계와 재개 지점: [`docs/design.md`](docs/design.md)

## 상태

**설계 단계.** 코어의 절반이 서 있고 나머지는 아직 없다.

| | |
|---|---|
| ✅ | `core/constants.py` `gamemap.py` `augments.py` `state.py` |
| ⬜ | `core/attack.py` `engine.py` · `ai/` · `ui/` · `cli/` · `tests/` |

아직 **플레이할 수 없다.** 실행 방법은 게임 루프(`engine.py`)가 서면 여기에 적는다.

## 설치

```bash
pip install -e ".[dev,ui]"
```

## 지금 확인할 수 있는 것

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
    attack.py      (예정) 공격 부대와 연속 확장
    engine.py      (예정) tick(state, dt), 증강 정지, 승리 판정
  ai/            (예정) 규칙 기반 AI
  ui/            (예정) PyQt6 — 전체화면 지도 + 오버레이 HUD
  cli/           (예정) 헤드리스 시뮬레이션 (밸런스 측정)
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
