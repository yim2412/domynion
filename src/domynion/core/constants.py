"""밸런스 수치 단일 출처 — **openfront 원본 값 그대로.**

이 파일의 숫자는 하나도 우리가 정한 것이 아니다. 전부 OpenFront 소스에서 옮겼고,
각 값 옆에 **원본 위치**를 적어 뒀다. 값을 고치고 싶으면 먼저 물어야 한다:
"원본이 이 값인가, 아니면 우리가 튜닝하는 것인가." 후자라면 그 자리에 그렇게 적는다.

원본: github.com/openfrontio/OpenFrontIO (main, 2026-08-21)
이식 계획과 공식 전문: docs/openfront-port.md

주의 — **지도 크기가 이 수치들을 지배한다.** 공식이 육지 수만~수백만 타일에 맞춰져
있어서 작은 지도에 넣으면 다른 게임이 된다. 자세한 건 계획서 4.5절.
"""

from __future__ import annotations

from enum import IntEnum

# --- 시간 -----------------------------------------------------------------
# ServerEnv.turnIntervalMs() = 100
TICK_MS = 100
TICK_HZ = 10
TICK_DT = 1.0 / TICK_HZ


# --- 지형 -----------------------------------------------------------------
#
# GameMap.ts :: terrainType() — 지도 바이트의 magnitude 로 갈린다.
#   magnitude < 10 → Plains,  < 20 → Highland,  그 외 → Mountain,  31 → Impassable
#
# 값을 IntEnum 으로 두는 이유: 타일 13만 개를 uint8 배열에 담으므로 지형도 정수여야
# 한다. Enum 객체를 타일마다 들고 있을 수 없다.

class Terrain(IntEnum):
    OCEAN = 0
    PLAINS = 1
    HIGHLAND = 2
    MOUNTAIN = 3
    IMPASSABLE = 4


# 지도 바이트 해석 (resources/maps/ATTRIBUTION.md)
LAND_BIT = 1 << 7
SHORELINE_BIT = 1 << 6
OCEAN_BIT = 1 << 5
MAGNITUDE_MASK = 0x1F
IMPASSABLE_MAGNITUDE = 31
HIGHLAND_MAGNITUDE = 10       # 이 값 이상이면 Highland
MOUNTAIN_MAGNITUDE = 20       # 이 값 이상이면 Mountain

# Config.ts :: attackLogic() — 지형이 가진 숫자는 **둘**이다.
#   mag   = 공격측이 이 칸에서 잃는 병력의 크기
#   speed = 이 칸이 tick 예산에서 깎아 먹는 양
TERRAIN_MAG: dict[Terrain, float] = {
    Terrain.PLAINS:   80.0,
    Terrain.HIGHLAND: 100.0,
    Terrain.MOUNTAIN: 120.0,
}
TERRAIN_SPEED: dict[Terrain, float] = {
    Terrain.PLAINS:   16.5,
    Terrain.HIGHLAND: 20.0,
    Terrain.MOUNTAIN: 25.0,
}

# AttackExecution.ts :: addNeighbors() — 우선순위 힙에 쓰는 별도의 지형 가중치.
# 위 mag 와 **다른 값**이다. 같은 이름에 속지 말 것.
PRIORITY_MAG: dict[Terrain, float] = {
    Terrain.PLAINS:   1.0,
    Terrain.HIGHLAND: 1.5,
    Terrain.MOUNTAIN: 2.0,
}
PRIORITY_NOISE_MAX = 8        # rand(0, 7)
PRIORITY_BASE = 10            # (rand + 10)
PRIORITY_NEIGHBOR_WEIGHT = 0.5    # (1 − 내이웃수 × 0.5 + mag/2)


# --- 병력 -----------------------------------------------------------------
#
# Config.ts :: maxTroops() / troopIncreaseRate()
#
#   maxTroops     = 2 × (타일^0.6 × 1000 + 50000) + Σ(도시 레벨) × 250000
#   troopIncrease = (10 + 병력^0.73 / 4) × (1 − 병력/상한)
#
# 상한이 **준선형**(^0.6)이고 상수항이 크다는 점이 중요하다. 작은 지도에서는 상수항이
# 지배해 영토 확장의 의미가 사라진다.

MAX_TROOPS_MULT = 2.0
MAX_TROOPS_TILE_EXP = 0.6
MAX_TROOPS_TILE_MULT = 1000.0
MAX_TROOPS_BASE = 50_000.0

TROOP_GROWTH_FLAT = 10.0
TROOP_GROWTH_EXP = 0.73
TROOP_GROWTH_DIV = 4.0

CITY_TROOP_INCREASE = 250_000.0    # Config.ts :: cityTroopIncrease()

# --- 관계도 ------------------------------------------------------------------
# PlayerImpl.ts :: relationFromValue / updateRelation / decayRelations
RELATION_MAX = 100.0               # within(-100, 100)
RELATION_HOSTILE_BELOW = -50.0
RELATION_FRIENDLY_AT = 50.0
RELATION_DECAY_PER_TICK = 0.05     # 원한도 호감도 시간이 지나면 잊힌다

# 무엇이 관계를 얼마나 움직이는가 — 각 Execution 에 흩어져 있던 값을 모았다.
REL_ALLIANCE_ACCEPTED = 100.0      # AllianceRequestExecution (양쪽 다)
REL_ALLIANCE_BROKEN = -100.0       # BreakAllianceExecution (당한 쪽 → 깬 쪽)
REL_ALLIANCE_BROKEN_NEIGHBOUR = -40.0   # 이웃들도 배신을 본다
REL_TARGETED = -40.0               # TargetPlayerExecution
REL_NUKED = -100.0                 # NukeExecution
REL_MIRV = -100.0                  # MIRVExecution (양방향)
REL_TROOP_DONATION = 50.0          # DonateTroopExecution
REL_EMBARGO = -20.0                # NationExecution (걸면 −20, 풀면 +20)
REL_ATTACKED_ALLY = -20.0          # AiAttackBehavior — 동맹을 친 벌
REL_WARSHIP_SANK_TRADE = -7.5      # NationWarshipBehavior
REL_WARSHIP_SANK_OTHER = -15.0

# 공격당하면 얼마나 나빠지는가 — 난이도가 높을수록 더 오래 기억한다.
REL_ATTACKED: dict[str, float] = {
    "easy": -60.0, "medium": -70.0, "hard": -80.0, "impossible": -100.0,
}

# 이모지 — NationEmojiBehavior.ts / PlayerImpl.canSendEmoji
#
# 🖕 하나가 −100 이다. 사람이 AI 관계를 바꾸는 수단 중 **유일하게 공짜**다.
REL_EMOJI_INSULT = -100.0          # 🖕
REL_EMOJI_CLOWN = -10.0            # 🤡
# ⚠ 좋은 이모지는 **easy 에서만** 통한다. 아니면 쿨다운마다 눌러 관계를 산다.
REL_EMOJI_PEACEFUL = 15.0          # 🕊️🏳️❤️🥰👏

EMOJI_COOLDOWN_TICKS = 5 * 10      # emojiMessageCooldown() — 같은 상대에게 5초
EMOJI_AI_INTERVAL_TICKS = 300      # shouldSendEmoji — AI 가 먼저 거는 말은 30초에 한 번

# 골드 기부 한 덩어리 크기(`getGoldChunkSize`). 덩어리당 관계 +5.
GOLD_CHUNK_SIZE: dict[str, float] = {
    "easy": 2_500.0, "medium": 5_000.0, "hard": 12_500.0, "impossible": 25_000.0,
}

# `numSpawnPhaseTurns()` — 기부 덩어리 크기 스케일링의 분모에 들어간다.
SPAWN_PHASE_TURNS = 100

# 스폰 면역 — Config.ts :: spawnImmunityDuration() / PlayerImpl :: isImmune()
#
# **사람 공격자만 면역을 존중한다**(원본 주석: "Only human attackers respect PVP
# immunity"). 봇·Nation 은 면역 중인 상대도 친다. 이 비대칭을 빼면 초반 규칙이 달라진다.
SPAWN_IMMUNITY_TICKS = 5 * 10

# Config.ts :: startManpower()
START_TROOPS_HUMAN = 25_000.0
START_TROOPS_BOT = 10_000.0

# Config.ts :: attackAmount() — 사람은 UI 슬라이더로 조절, 이건 기본값
ATTACK_RATIO_HUMAN = 1 / 5

# 공격 비율 슬라이더 — ControlPanel.ts / UserSettings.ts
#
# 1% 밑으로는 못 내린다. 0% 면 아무 일도 안 일어나는데 왜 안 되는지 알 수 없다.
ATTACK_RATIO_MIN = 0.01
ATTACK_RATIO_STEP = 0.10           # attackRatioIncrement 기본 10%p — 키 T/Y
ATTACK_RATIO_BOT = 1 / 20
BOAT_ATTACK_RATIO = 1 / 5          # boatAttackAmount()

# 봇 배율 (maxTroops / troopIncreaseRate)
BOT_MAX_TROOPS_DIV = 3.0
BOT_GROWTH_MULT = 0.5

# Nation(난이도가 붙는 AI 국가) 배율 — Config.ts :: maxTroops/troopIncreaseRate/startManpower
# 플레이어 종류가 셋이다: Human · Nation · Bot. 봇은 난이도와 무관하게 항상 약하고,
# Nation 만 난이도를 탄다.
DIFFICULTIES = ("easy", "medium", "hard", "impossible")
NATION_MAX_TROOPS_MULT = {"easy": 0.5, "medium": 0.75, "hard": 1.0, "impossible": 1.25}
NATION_GROWTH_MULT = {"easy": 0.9, "medium": 0.95, "hard": 1.0, "impossible": 1.05}
NATION_START_TROOPS = {"easy": 12_500.0, "medium": 18_750.0,
                       "hard": 25_000.0, "impossible": 25_000.0}


# --- 전투 -----------------------------------------------------------------
#
# Config.ts :: attackLogic(). 공식 전문은 계획서 1.2/1.3 절.

DEFENSE_DEBUFF_MIDPOINT = 150_000.0          # 타일 수
DEFENSE_DEBUFF_DECAY_RATE = 0.6931471805599453 / 50_000.0   # Math.LN2 / 50000

LARGE_PLAYER_TILES = 100_000.0               # 이 이상부터 대국 페널티
LARGE_ATTACK_BONUS_EXP = 0.7                 # sqrt(100000/타일)^0.7
LARGE_SPEED_BONUS_EXP = 0.6                  # (100000/타일)^0.6

DEFENDER_DEBUFF_FLOOR = 0.7                  # 0.7 + 0.3 × defenseSig
DEFENDER_DEBUFF_SPAN = 0.3

# 공격측 손실 = 0.6 × A + 0.4 × B
ATTACKER_LOSS_A_WEIGHT = 0.6
ATTACKER_LOSS_B_WEIGHT = 0.4
ATTACKER_LOSS_A_MULT = 0.8                   # A 안의 × 0.8
ATTACKER_LOSS_A_CLAMP = (0.6, 2.0)           # within(수비병력/공격병력, 0.6, 2)
ATTACKER_LOSS_B_MULT = 1.3                   # B = 1.3 × (수비병력/수비타일) × mag/100
ATTACKER_LOSS_B_MAG_DIV = 100.0

TILES_USED_CLAMP = (0.2, 1.5)                # within(수비병력/(5×공격병력), 0.2, 1.5)
TILES_USED_TROOP_MULT = 5.0

# 중립(TerraNullius) 분기 — 완전히 다른 공식이다
NEUTRAL_LOSS_DIV_HUMAN = 5.0                 # mag / 5
NEUTRAL_LOSS_DIV_BOT = 10.0                  # mag / 10
NEUTRAL_TILES_USED_NUM = 2000.0              # within(2000 × max(10, speed) / 병력, 5, 100)
NEUTRAL_TILES_USED_SPEED_FLOOR = 10.0
NEUTRAL_TILES_USED_CLAMP = (5.0, 100.0)

# attackTilesPerTick() — 한 tick 의 예산
BUDGET_VS_PLAYER_CLAMP = (0.01, 0.5)         # within(5×공격/수비 × 2, 0.01, 0.5)
BUDGET_VS_PLAYER_MULT = 2.0
BUDGET_VS_PLAYER_BORDER_MULT = 3.0
BUDGET_VS_NEUTRAL_BORDER_MULT = 2.0
BUDGET_BORDER_NOISE_MAX = 6                  # borderSize() + rand(0, 5)

ATTACK_MIN_TROOPS = 1.0                      # 이보다 적으면 부대가 소멸(퇴각 없음)
CONQUER_PLAYER_TILES = 100                   # 수비자가 이 미만이면 통째로 흡수

# 외교 (P3) — Config.ts :: allianceDuration() / traitor*()
#
# 동맹 5분. 호스트가 1~15분으로 바꿀 수 있고 0 이면 동맹이 아예 없다.
ALLIANCE_DURATION_TICKS = 300 * 10
TEMPORARY_EMBARGO_TICKS = 300 * 10

# 동맹을 깬 쪽에 30초 동안 붙는 낙인. 방어가 절반이 되고 상대 확장이 빨라진다.
TRAITOR_DEFENSE_DEBUFF = 0.5
TRAITOR_SPEED_DEBUFF = 0.8
TRAITOR_DURATION_TICKS = 30 * 10

# 방어초소
DEFENSE_POST_RANGE = 30
DEFENSE_POST_DEFENSE_BONUS = 5.0
DEFENSE_POST_SPEED_BONUS = 3.0

# 낙진 — falloutDefenseModifier(비율) = 5 − 비율 × 2.
# 비율은 **지도 전체의 낙진 비율**이라, 핵이 많이 터질수록 한 칸의 효과가 줄어든다.
FALLOUT_DEFENSE_BASE = 5.0
FALLOUT_DEFENSE_SLOPE = 2.0

# 핵·SAM (P5) — Config.ts :: nukeMagnitudes/nukeSpeed/samRange/nukeDeathFactor
NUKE_TARGETABLE_RANGE = 150
DEFAULT_SAM_RANGE = 70
MAX_SAM_RANGE = 150
SAM_MISSILE_SPEED = 12

# Config.ts :: waterNukes() — **기본값 false**.
# 꺼져 있으면 폭심은 육지로 남고 **낙진만** 생긴다. 켜면 반대로 바다가 되고
# 낙진은 지워진다(`setWater` 가 지운다). 둘 다 하면 안 된다 —
# 실제로 둘 다 했다가 한 판에 낙진이 지도의 90% 를 덮었다.
WATER_NUKES = False

# MIRV 탄두만 다른 피해식을 쓴다
MIRV_TARGET_TROOP_RATIO = 0.03
MIRV_DEATH_SCALE = 500.0
MIRV_DEATH_STEEPNESS = 2.0

# 공격자 Human/Nation 이 Bot 을 칠 때
ATTACK_VS_BOT_MAG_MULT = 0.7


# --- 골드·건물 (P2) -------------------------------------------------------
#
# Config.ts :: goldAdditionRate() — tick 당이다(초당이 아니다).
GOLD_PER_TICK_HUMAN = 100
GOLD_PER_TICK_BOT = 50

# Config.ts :: structureMinDist() — 건물끼리 이만큼 떨어져야 한다(유클리드).
STRUCTURE_MIN_DIST = 15

# Config.ts :: SAM_CONSTRUCTION_TICKS
SAM_CONSTRUCTION_TICKS = 30 * 10


# --- 해상 (P4) ------------------------------------------------------------
#
# Config.ts :: boatMaxNumber() / warship*() / tradeShip*()

BOAT_MAX_NUMBER = 3            # 동시에 띄울 수 있는 수송선 수
BOAT_TICKS_PER_MOVE = 1        # TransportShipExecution.ticksPerMove
BOAT_RETREAT_MALUS_PCT = 0.0   # 퇴각해 돌아온 병력의 손실 비율(원본 malusForRetreat)

WARSHIP_PATROL_RANGE = 100
WARSHIP_TARGETTING_RANGE = 130
WARSHIP_SHELL_ATTACK_RATE = 20  # 이 tick 마다 한 발
WARSHIP_DOCKING_RANGE = 5
WARSHIP_PASSIVE_HEALING = 1          # 항구 근처면 tick 당 이만큼 회복
WARSHIP_PASSIVE_HEALING_RANGE = 150
WARSHIP_VETERANCY_SHELL_BONUS = 20   # 격침 1회당 포탄 피해 +20%
WARSHIP_MAX_HEALTH = 1000
# 포탄 피해 = 250/250 × ((굴림−1)×25 + 200), 굴림은 1~5.
# 그래서 한 발이 200~300 이고, 체력 1000 인 전함은 4~5발을 견딘다.
SHELL_DAMAGE = 250
SHELL_LIFETIME = 50
SHELL_ROLL_MIN = 1
SHELL_ROLL_MAX = 5
SHELL_ROLL_STEP = 25
SHELL_ROLL_BASE = 200

# MIRV — MIRVExecution.warheadCount
MIRV_WARHEAD_COUNT = 350

TRADE_SHORT_RANGE_DEBUFF = 300  # 이 거리 아래는 시그모이드가 눌러 크게 손해다
TRADE_SPAWN_SIGMOID_MID = 400

# 철도 (P4 마무리) — Config.ts :: trainStation*/trainGold/trainSpawnRate
#
# 역은 따로 짓는 게 아니라 **건물에 붙는다.** 사거리 15~110 안의 역끼리 이어진다 —
# 너무 가까우면 골드 찍어내기가 되고, 너무 멀면 노선이 지도를 가로지른다.
TRAIN_STATION_MIN_RANGE = 15
TRAIN_STATION_MAX_RANGE = 110
RAILROAD_MAX_SIZE = TRAIN_STATION_MAX_RANGE * 1.4142
TRAIN_SPEED = 4.0                    # 우리 값 — 원본은 경로 그래프를 따라간다

# 기부 — Config.ts :: donateCooldown()
DONATE_COOLDOWN_TICKS = 10 * 10


# --- 판 ------------------------------------------------------------------
#
# ⚠ 아래 셋은 **원본 값이 아니다.** openfront 는 시간 제한도 지배 승리도 없고,
# 마지막까지 살아남는 것으로 끝난다. 헤드리스 측정을 끝내려면 종료 조건이 필요해서
# 우리가 둔 것이다. P6 에서 원본의 종료 규칙(둠스데이 클락)으로 교체한다.

MATCH_SECONDS = 900.0
DOMINATION_TILE_RATIO = 0.80
MIN_PLAYERS = 2
MAX_PLAYERS = 8
