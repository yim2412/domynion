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

**플레이할 수 있다.** 실제 openfront 지도 위에서 영토 확장 · 골드 · 건물 ·
동맹/배신 · 상륙 · 무역 · 전함 · 철도 · 핵/낙진 · 둠스데이 클락 · 원본 봇이
함께 돌고, PyQt6 UI 로 직접 조작한다. 테스트 179개.

| | |
|---|---|
| ✅ | 규칙 이식(P1~P6) · 원본 봇 · 원본 스폰 · **UI** |
| ⬜ | **증강형 테크트리**(원본에 없는 우리 층) |

**원본과 같은지는 실행해서 대조한다** — `tools/oracle.mts` 가 원본 TypeScript 를
직접 실행해 기준값을 뽑고, `tests/test_fidelity.py` 가 우리 값과 맞춰 본다.
현재 **181/181 일치**(전투 공식 30케이스 포함).

진행 상황과 원본 공식은 [`docs/openfront-port.md`](docs/openfront-port.md).

## 설치

```bash
pip install -e ".[dev,ui]"
```

## 실행

```bash
# 플레이 (기본 해상도 map4x = 1000×500, 육지 15.8만)
python -m domynion.ui.app --map world --players 4 --difficulty hard

# 해상도: map16x(1/16) · map4x(기본, 1000×500) · map(원본 크기 2000×1000, 육지 65만)
python -m domynion.ui.app --map world --size map

# 창 없이 한 장 찍는다 (600초 시점)
python -m domynion.ui.app --shot shot.png --at 600 --map world

# 헤드리스로 판을 돌려 밸런스를 잰다
python -m domynion.cli.play --games 40 --map world --jobs 8
python -m domynion.cli.play --games 20 --map world --clock normal --difficulty hard

# 테스트
python -m pytest tests -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests -q   # 변이 테스트는 캐시를 끈다
```

조작: **좌클릭 = 메뉴**(공격 · 건설 · 상륙 · 외교) · 우클릭 드래그/WASD = 이동 ·
휠 또는 `+`/`-` = 확대 · `F` = 화면 맞추기 · Space = 일시정지 · `H` = 도움말.

지도는 **가로로 순환한다** — 오른쪽으로 계속 가면 왼쪽이 나온다(화면만; 게임 규칙은
원본대로 x 경계를 안 넘는다). 키 이동은 60Hz 로 가속·감속한다.

메뉴 가운데는 뒤로, 바깥은 닫기다. **못 하는 항목은 회색으로 남고 이유가 붙는다**
(`사일로가 없다` · `골드 125,000 필요` · `⚠ 배신자가 된다`).

커서를 얹으면 그 나라의 병력과 **상대/내가 보낼 병력 비**가 뜬다 — 원본 공식이
`within(수비병력/공격병력, 0.6, 2)` 라 그 값이 판단의 전부다.

`--clock` 을 주면 **원본의 종료 규칙**(둠스데이 클락)으로 돈다 — 시간 제한도 지배
승리도 없이 마지막 생존자가 남을 때까지 간다.

지도는 `world` `asia` `europe` `africa` 넷 × 세 해상도 — 출처와 라이선스는
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
  ui/
    frame.py       타일 해상도 프레임 생성 (확대는 Qt 가 한다)
    map_widget.py  지도 + 국경선 + 이름
    hud.py         순위표 · 병력바 · 공격 슬라이더
    main_window.py 조립 + 실시간 타이머
    app.py         진입점 (--shot 스크린샷 모드)
    render.py      PIL 렌더러 (창 없이 그림 파일을 뽑는 용도)
    palette.py     색
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
