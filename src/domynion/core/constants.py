"""밸런스 수치 단일 출처(Single Source of Truth).

설계 원칙:
- 자원은 **병력 하나**. 영토가 병력을 낳고, 병력이 영토를 넓힌다.
- 확장은 **연속적**이다. 공격 부대가 국경을 따라 병력을 소모하며 계속 번지고,
  병력이 떨어지는 지점에서 저절로 멈춘다. 한 칸씩 편입되는 것이 아니다.
- 타일이 가진 숫자는 **방어 계수 하나**. 지형은 정복 비용에만 관여한다.
- **증강은 새 규칙을 만들지 않고 위 수치에 곱해지는 계수다.** 이 원칙이 무너지면
  증강 카드 한 장마다 예외 규칙이 하나씩 늘어나 판정이 아무도 모르는 것이 된다.
  (예외는 항해술 하나뿐이다 — 아래 주석 참고.)

튜닝은 이 파일만 고쳐서 되어야 한다. 매직 넘버가 로직 안에 박히면 규칙 위반이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# --- 맵 -------------------------------------------------------------------

# 확장이 부드러워 보이려면 타일이 잘아야 한다. 한 칸이 크면 아무리 자주 갱신해도
# "뚝뚝 끊겨 편입되는" 것으로 보인다.
BASE_TILES_PER_PLAYER = 400

# 지도는 정사각형이 아니라 화면 비율을 따른다. 정사각 맵을 와이드 모니터에 띄우면
# 좌우가 통째로 남아, 지도가 화면을 꽉 채우는 인상이 나지 않는다.
MAP_ASPECT = 1.6              # 가로/세로

MIN_PLAYERS = 2
MAX_PLAYERS = 8


class Terrain(Enum):
    PLAINS = "평야"
    FOREST = "숲"
    HILLS = "구릉"
    MOUNTAINS = "산악"
    WATER = "바다"


# 지형이 가진 유일한 숫자. 이 타일 한 칸을 먹는 데 드는 병력의 배율이다.
TERRAIN_DEFENSE: dict[Terrain, float] = {
    Terrain.PLAINS:    1.00,
    Terrain.FOREST:    1.20,
    Terrain.HILLS:     1.45,
    Terrain.MOUNTAINS: 1.90,
    Terrain.WATER:     0.00,   # 통행 불가 — 비용을 매길 일이 없다
}

# 지형 특화 증강이 걸리는 묶음. 개별 지형마다 증강을 두면 카드가 너무 잘게 쪼개져
# 뽑아도 체감이 없다.
HIGHLAND = (Terrain.HILLS, Terrain.MOUNTAINS)
WOODLAND = (Terrain.FOREST,)


@dataclass(frozen=True)
class TerrainSpec:
    defense: float
    passable: bool


def spec_for(terrain: Terrain) -> TerrainSpec:
    return TerrainSpec(
        defense=TERRAIN_DEFENSE[terrain],
        passable=terrain is not Terrain.WATER,
    )


# --- 대륙 생성 ------------------------------------------------------------
#
# 지형을 타일마다 독립 추첨하면 바다가 한 칸씩 흩어지고 산이 점점이 박힌다.
# 그러면 아무리 격자선을 지워도 화면이 "타일 게임"으로 읽힌다. 높이맵을 노이즈로
# 만들고 해수면으로 잘라야 같은 격자여도 대륙으로 보인다.

SEA_LEVEL = 0.42          # 이 높이 미만은 바다

# 고지대 임계값은 **절대 높이가 아니라 육지 높이의 백분위**로 잡는다. 절대값으로
# 두면 fBm 값이 중앙(0.5)에 몰려 있어 아무도 임계를 넘지 못한다.
MOUNTAIN_RATIO = 0.10     # 육지 중 산악 (높이 상위 10%)
HILL_RATIO = 0.20         # 그다음 20% 는 구릉
FOREST_RATIO = 0.36       # 남은 저지대 중 숲 (습도 상위)

NOISE_SCALE = 0.11        # 낮을수록 대륙이 커진다
NOISE_OCTAVES = 4
EDGE_FALLOFF = 0.55       # 맵 가장자리를 바다로 미는 정도 (섬 대륙을 만든다)

MIN_LAND_RATIO = 0.45
MAX_LAND_RATIO = 0.75

# 시작 대륙이 인원 대비 이만큼은 되어야 한다. 섬에 갇힌 채 시작하면 확장할 곳이
# 없어 병력 상한이 낮게 묶이고, 그 판은 시작과 동시에 끝난 것이나 다름없다.
MIN_TILES_PER_START = 40

# --- 시간 -----------------------------------------------------------------

TICK_HZ = 20              # 확장이 부드럽게 보이려면 10Hz 로는 모자란다
TICK_DT = 1.0 / TICK_HZ

MATCH_SECONDS = 900.0     # 15분. 시간이 다하면 영토가 가장 넓은 쪽이 이긴다

# --- 병력 -----------------------------------------------------------------
#
# 인구 곡선. 상한은 영토에 비례하고 성장은 상한에 로지스틱으로 수렴한다 —
# 그래서 작은 나라는 빨리 차고 큰 나라는 오래 채운다.

TROOPS_START = 60.0
TROOPS_CAP_BASE = 250.0
TROOPS_CAP_PER_TILE = 22.0
TROOPS_GROWTH_RATE = 0.085     # 초당. 상한 대비 비율로 작동한다
TROOPS_GROWTH_FLOOR = 1.2      # 영토가 거의 없어도 최소한 이만큼은 초당 회복

DEFAULT_ATTACK_RATIO = 0.25    # 공격 슬라이더 초기값
MIN_ATTACK_TROOPS = 8.0        # 이보다 적게는 공격을 시작할 수 없다

# --- 정복 -----------------------------------------------------------------
#
# 한 칸 비용 = CONQUER_COST_BASE × 지형 방어 × (적이면 방어 충전율 가산) × 증강 계수.
#
# 충전율은 상대 병력이 상한의 몇 %인가다(0~1). 타일당 병력(절대값)을 쓰면 안 된다 —
# 그 값은 상한(타일당 22)까지 커져 비용이 13배까지 뛰고, 그러면 서로를 아무도 못
# 뚫어 판이 교착된다.

CONQUER_COST_BASE = 2.6
DEFENDER_FILL_MULT = 2.2       # 병력을 가득 채운 상대는 최대 3.2배 비싸다
DEFENDER_LOSS_RATIO = 0.42     # 타일을 잃을 때 방어측이 함께 잃는 병력 비율

# 확장 속도 — 초당 몇 칸까지 번지는가. 병력이 많을수록 넓게 번진다.
EXPAND_TILES_PER_SEC_BASE = 6.0
EXPAND_TILES_PER_SEC_PER_TROOP = 0.055
EXPAND_TILES_PER_SEC_MAX = 90.0

ATTACK_ABANDON_TROOPS = 1.5    # 부대가 이보다 줄면 남은 병력을 본국으로 돌려보낸다

# --- 증강 정지 ------------------------------------------------------------
#
# 일정 시간마다 **전원이 멈춰서** 증강을 고른다. 실시간이 끊김 없이 흐르기만 하면
# 오히려 단조로워진다 — 정지가 판을 챕터로 쪼갠다.

AUGMENT_FIRST_SEC = 75.0       # 첫 정지. 초반 확장 레이스가 한 번 끝난 뒤
AUGMENT_INTERVAL_SEC = 130.0   # 이후 간격. 75/205/335/465/595/725/855 = 한 판에 7회
AUGMENT_CHOICES = 3            # 뽑아 주는 카드 수
AUGMENT_MAX_LEVEL = 3
AUGMENT_LEVEL_MULT = [1.0, 1.7, 2.3]   # Lv1/Lv2/Lv3 계수 배율

# 미선택 시 자동 선택까지의 시간. 온라인에서 한 명이 판 전체를 멈추는 것을 막는다 —
# 나중에 붙이면 규칙이 둘이 된다.
AUGMENT_PICK_TIMEOUT = 25.0

# --- 승리 -----------------------------------------------------------------

DOMINATION_TILE_RATIO = 0.80   # 육지의 이 비율을 쥐면 즉시 승리

# --- 점수 -----------------------------------------------------------------
# 영토가 곧 점수다. 별도의 점수 공식을 두지 않는다.

SCORE_PER_TILE = 1
