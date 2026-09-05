# Domynion

**openfront.io 를 완전 복제한 뒤, 그 위에 증강형 테크트리를 얹는다.**

원본의 공식과 상수를 그대로 옮기는 것이 1단계다(개념 재현이 아니다). 유닛 16종 ·
골드 · 동맹/배신자 · 보트 · 핵/MIRV 까지 옮긴 뒤에 증강을 설계한다.

- **이식 계획과 원본 공식 · 재개 지점: [`docs/openfront-port.md`](docs/openfront-port.md)** ← 여기부터
- **증강 계층의 설계**(원본에 없는 우리 층): [`docs/design.md`](docs/design.md)

자원은 병력 하나. 영토가 병력을 낳고 병력이 영토를 넓힌다. 타일을 클릭하면 그 칸이
아니라 **그 칸의 소유자 전체**로 공격 부대가 번지고, 병력이 떨어지는 지점에서 멈춘다.
일정 시간마다 전원이 멈춰 증강 3장 중 하나를 고른다 — 그것이 빌드가 된다.


## 상태

**플레이할 수 있다.** 실제 openfront 지도 위에서 영토 확장 · 골드 · 건물 ·
동맹/배신 · 상륙 · 무역 · 전함 · 철도 · 핵/낙진 · 둠스데이 클락 · 원본 봇이
함께 돌고, PyQt6 UI 로 직접 조작한다. 테스트 **1,219개**.

| | |
|---|---|
| ✅ | 규칙 이식(P1~P6) · 원본 봇 · 원본 스폰 · **UI** · 이식 누락 **123개** 메움 |
| ✅ | **증강형 테크트리** — 카드 10종 · 드래프트 창 · 보유 표시. 켜면 생존판 영토가 **2.9배**(A/B 24판) |
| ⏳ | Overtime(교착 방지)을 켰다 — 기준선을 다시 재는 중 |

**원본과 같은지는 실행해서 대조한다** — `tools/oracle.mts` 가 원본 TypeScript 를
직접 실행해 기준값을 뽑고, `tests/test_fidelity.py` 가 우리 값과 맞춰 본다.
현재 **185/185 일치**(전투 공식 30케이스 + 업그레이드 비용 곡선 포함, 2026-08-22 실행).

⚠ **값 대조만으로는 부족하다.** 2026-08-27 에 이식 누락 **열일곱**을 더 찾았는데
(누계 마흔셋), 그동안 이 대조는 **내내 185/185 초록불이었다.** 열넷이 *"원본
파일을 줄 수로 세워 우리 것과 나란히 놓기"* 에서 나왔다 — 값 대조는 `f(x)` 가
맞는지만 본다. 우리에게 그 자리가 **아예 없으면** 대조할 `f` 가 없어 조용히
지나간다. 방법은 `docs/openfront-port.md` §5.51 · 하루 요약은 §5.59.

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

# 기준선을 뜬다 (핵 발사·MIRV·생존·골드). ⚠ --ticks 를 명시한다 (기본 9,000 은
# 더 이상 기준선이 아니다). --jobs 를 생략하면 CPU·RAM 을 재서 여유 10% 를 남긴다
python tools/balance.py --seeds 1 2 3 --ticks 45000

# 증강 켜고/끄고 A/B — 생존율과 짝 판정을 낸다
python tools/augment_ab.py --seeds 11 22 33 --focus troops

# 골드가 어디로 가는지 센다 · 판을 프로파일한다
python tools/gold_flow.py --ticks 9000 --size map
python tools/profile_game.py --ticks 1200 --size map

# 테스트
python -m pytest tests -q

# 변이 하네스 — 테스트가 규칙을 진짜로 재는지 확인한다.
# ⚠ 도는 동안 pytest 도 실측 스크립트도 새로 띄우지 않는다 (소스를 제자리에서 고친다)
python tools/mutate.py --spec <변이명세.json> --timeout 180
# ⚠ 실행마다 docs/mutation-log.tsv 에 한 줄 남는다 (누적 변이 수를 손으로 세다 두 번 틀렸다)

# 개발 노트 페이지(docs/index.html)의 통계 다섯 개를 실측해 갱신한다
python tools/site_stats.py               # 재서 보여만 준다
python tools/site_stats.py --write       # 갱신 (pytest 를 같이 돌린다)
python tools/site_stats.py --check --no-tests   # 어긋나면 exit 1 · 커밋 전 확인용
```

조작: **좌클릭 = 메뉴**(공격 · 건설 · 상륙 · 외교) · 우클릭 드래그/WASD = 이동 ·
휠 또는 `+`/`-` = 확대 · `F` = 화면 맞추기 · `T`/`Y` = 공격 비율 ∓10%p ·
`E` = 전체 금수 걸기/풀기 · Space = 일시정지 · `H` = 도움말.

진행 중인 공격과 상륙 부대는 오른쪽 **전투 패널**에 뜨고, 옆의 `✕` 로 물릴 수 있다
(육상 25% · 상륙 25% 손실, 중립 확장은 공짜). 건물은 **건설 메뉴 끝의 `철거`** 로
지운다 — 30초 뒤에 사라지고 **골드는 안 돌아온다.**

건설 메뉴는 **건설/업그레이드 통합**이다(원본과 같다). 같은 종류가 15칸 안에 이미
있으면 그 항목이 `▲Lv2` 로 바뀌어 그 건물을 올린다. 값은 올릴수록 뛴다 —
도시는 250,000 → 500,000 → 1,000,000(상한). 이름 옆 숫자는 개수가 아니라 **레벨 합**이다.

지도는 **가로로 순환한다** — 오른쪽으로 계속 가면 왼쪽이 나온다(화면만; 게임 규칙은
원본대로 x 경계를 안 넘는다). 키 이동은 60Hz 로 가속·감속한다.

메뉴 가운데는 뒤로, 바깥은 닫기다. **못 하는 항목은 회색으로 남고 이유가 붙는다**
(`사일로가 없다` · `골드 125,000 필요` · `⚠ 배신자가 된다`).

커서를 얹으면 그 나라의 병력과 **상대/내가 보낼 병력 비**가 뜬다 — 원본 공식이
`within(수비병력/공격병력, 0.6, 2)` 라 그 값이 판단의 전부다.

`--clock` 을 주면 둠스데이 클락이 **더해진다.** ⚠ 예전에 여기 *"시간 제한도 지배
승리도 없이"* 라고 적혀 있었는데 **틀렸다**(§5.61) — 원본은 둘 다 돌고, 클락은
교착을 푸는 장치이지 승리 판정을 대신하는 것이 아니다.

판을 끝내는 것은 **정복 · 지배 · Overtime** 셋이다. Overtime 이 켜져 있어
**30분부터 지배 문턱(80%)이 분당 2%p 내려가 70분에 0** 이 된다 — 안 끝나는 판이
없다. 170분 하드 리밋은 그래서 **도달 불가**다(§5.118).

지도는 `world` `asia` `europe` `africa` 넷 × 세 해상도 — 출처와 라이선스는
[`resources/maps/ATTRIBUTION.md`](resources/maps/ATTRIBUTION.md).

## 구조

```
src/domynion/
  core/          순수 로직 계층 — UI·네트워크 의존 없음
    constants.py   밸런스 수치 단일 출처
    gamemap.py     지도 로드, 타일·인접·바다 연결성분
    spawn.py       시작 위치 고르기 (반경 4의 원)
    state.py       플레이어 상태 + 파생 수치
    attack.py      공격 부대와 연속 확장 (우선순위 힙)
    units.py       유닛 종류·비용 곡선·보유량(레벨 합)
    buildings.py   건물 배치 규칙 · 방어초소 커버리지 색인
    naval.py       수송선 · 무역선 · 전함
    rail.py        공장 · 역 · 기차
    nukes.py       원자탄 · 수소탄 · MIRV · 낙진 · SAM
    diplomacy.py   동맹 · 금수 · 팀
    relations.py   관계 수치와 감쇠
    emoji.py       이모지와 관계 변화량
    doomsday.py    둠스데이 클락 (원본의 종료 규칙)
    events.py      소식·경보 로그
    rot.py         점진적 썩음
    enclave.py     둘러싸인 영토 흡수
    augments.py    증강 카드 · 레벨 · 계수 합산(Modifiers) · 드래프트
    engine.py      tick 루프, 건설·업그레이드·철거, 탈락·승리 판정
  ai/
    nation.py      Nation 봇 — 공격 · 외교 · 상륙 · 핵 (`NationExecution`)
    structures.py  Nation 봇의 건설·업그레이드 (`NationStructureBehavior`)
    nukes.py       핵 판단 · mirv.py  MIRV 판단
    placement.py   자리 고르기 값 함수 · alliance.py  동맹 판단·연장
    chatter.py     AI 가 먼저 거는 말
    tribe.py       부족(봇) — 동맹을 다 받고 건물을 지운다
    simple_ai.py   v0.1 규칙 기반 AI (대조용으로 남겨 둔다)
  ui/
    frame.py       타일 해상도 프레임 생성 · 국경 변 · 이름 자리(내접 사각형)
    map_widget.py  지도 + 국경선(관계·방어로 색이 갈린다) + 이름
    hud.py         순위표 · 병력바 · 공격 슬라이더 · 증가율 · 클락의 다음 파도
    status.py      이름 옆 상태 깃발 (`derive/PlayerStatus.ts`) — Qt 없는 순수 계산
    overlays.py    핵 낙하 예고 원 · 상륙 고리 · 국경 관계 · 파도 문구
                   (`derive/NukeTelegraphs.ts` · `AttackRings.ts` ·
                   `PlayerView.borderColor` · `DoomsdayClockPanel`) — 같은 방침
    rates.py       병력 `+N/s` · 골드 `+N` (`ControlPanel.ts`) — 같은 방침
    augment_dialog.py 증강 드래프트 창 + **보유 증강 스트립**(다음 정지까지 남은 시간)
    radial.py      방사형 메뉴 · actions.py  클릭 → 명령
    eventlog.py    소식·전투·경보 패널 · endmodal.py  결과 화면
    emojitable.py  이모지 고르기
    main_window.py 조립 + 실시간 타이머
    app.py         진입점 (--shot 스크린샷 모드)
    render.py      PIL 렌더러 (창 없이 그림 파일을 뽑는 용도)
    palette.py     색
  cli/
    play.py        헤드리스 시뮬레이션 (밸런스 측정)
    shot.py        스크린샷 모드
tools/
    balance.py     기준선 · augment_ab.py  증강 A/B
    gold_flow.py   골드 흐름 · profile_game.py  프로파일
    mutate.py      변이 하네스 · verify_port.py  이식 대조 · oracle.mts  값 대조
    _budget.py     병렬 작업이 CPU·RAM 여유 10% 를 남기게 한다
```

계층 규칙: `core` 는 아무것도 import 하지 않는다. UI 와 AI 는 `core` 위에 나란히
얹으며 서로를 참조하지 않는다.

## 핵심 규칙 요약

```
자원은 병력 하나.  타일이 가진 숫자는 방어 계수 하나.

  병력을 떼어 국경에 붙이면 부대가 번지며 타일을 사들이고, 떨어지면 멈춘다.
  한 칸 비용은 지형과 방어측이 병력을 얼마나 채워 뒀는가로 정해진다.
  증강은 그 수치에 곱해지는 계수일 뿐, 새 규칙을 만들지 않는다 — **예외는 없다.**
```
