# Domynion

**openfront.io 를 완전 복제한 뒤, 그 위에 증강형 테크트리를 얹는다.**

원본의 공식과 상수를 그대로 옮기는 것이 1단계다(개념 재현이 아니다). 유닛 16종 ·
골드 · 동맹/배신자 · 보트 · 핵/MIRV 까지 옮긴 뒤에 증강을 설계한다.

- **이식 계획과 원본 공식: [`docs/openfront-port.md`](docs/openfront-port.md)** ← 여기부터
- v0.1 자체 설계(폐기된 방향): [`docs/design.md`](docs/design.md)

자원은 병력 하나. 영토가 병력을 낳고 병력이 영토를 넓힌다. 타일을 클릭하면 그 칸이
아니라 **그 칸의 소유자 전체**로 공격 부대가 번지고, 병력이 떨어지는 지점에서 멈춘다.
일정 시간마다 전원이 멈춰 증강 3장 중 하나를 고른다 — 그것이 빌드가 된다.


## 상태

**P1 완료 — 영토 코어가 원본 공식으로 돈다.** 실제 openfront 지도(World 37,575칸 ~
Europe 134,751칸) 위에서 판이 끝까지 돌아간다. 테스트 42개.

| | |
|---|---|
| ✅ | P1 지도·병력·전투·우선순위 힙·10Hz tick |
| ⬜ | P2 골드·건물 · P3 동맹 · P4 보트/전함 · P5 핵 · P6 스폰/난이도 · **P7 증강** |

진행 상황과 원본 공식은 [`docs/openfront-port.md`](docs/openfront-port.md).

## 설치

```bash
pip install -e ".[dev,ui]"
```

## 실행

```bash
# 헤드리스로 판을 돌려 밸런스를 잰다 (판당 약 16초 — 지도가 커서 느리다)
python -m domynion.cli.play --games 40 --map world --jobs 8
python -m domynion.cli.play --games 240 --map world --jobs 8

# 판을 그림으로 찍는다 (창 없이)
python -m domynion.cli.shot --seed 1 --map world --at 120 600 --tile 3 --out shots/

# 테스트
python -m pytest tests -q

# 변이 테스트는 바이트코드 캐시를 꺼야 한다 (계획서 5.5절 참고)
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests -q
```

지도는 `world` `asia` `europe` `africa` 넷을 담아 뒀다 — 출처와 라이선스는
[`resources/maps/ATTRIBUTION.md`](resources/maps/ATTRIBUTION.md).

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
