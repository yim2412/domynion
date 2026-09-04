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
REL_ASSIST_COST = -20.0            # 동맹의 부탁을 들어준 대가(AiAttackBehavior)

# 표적 지정 — Config.ts :: targetDuration / targetCooldown
#
# 동맹에게 "저놈을 쳐 달라"고 찍는 것이다. 이게 없으면 동맹은 서로 안 친다는
# 소극적 약속일 뿐이고, **함께 싸우는 수단이 없다.**
# 퇴각 — RetreatExecution.ts
#
# 명령하고 2초 뒤에 실제로 물러난다. 즉시 물리면 되돌릴 수 없는 클릭 한 번으로
# 부대가 증발한다.
RETREAT_DELAY_TICKS = 20
# **사람을 치던 부대만** 25% 를 잃는다. 중립 확장은 공짜로 무를 수 있다 —
# 안 그러면 잘못 찍은 확장을 취소하는 데 병력을 버려야 한다.
RETREAT_MALUS = 0.25

TARGET_DURATION_TICKS = 10 * 10    # 표적으로 남는 시간
TARGET_COOLDOWN_TICKS = 15 * 10    # 다음 표적을 찍기까지

# 건물 철거 — DeleteUnitExecution.ts / Config.ts :: deleteUnitCooldown, deletionMarkDuration
#
# 명령하면 바로 사라지지 않는다. 30초 동안 **철거 예정**으로 표시된 채 계속 돌아가다가
# 그 뒤에 사라진다. 원본에 취소는 없다 — 무를 시간을 주는 게 아니라, 남이 보고
# 대응할 시간을 주는 것이다. **골드는 한 푼도 안 돌아온다**(`delete()` 에 환불이 없다).
#
# ⚠ 계획서에 "쿨다운 10 tick" 이라고 적혀 있었는데 **틀렸다.** 원본은 둘 다 300 tick 이다.
# 초기값이 −1 이라 판 시작 후 30초가 지나야 첫 철거가 된다(`lastDeleteUnitTick = -1`).
DELETE_UNIT_COOLDOWN_TICKS = 30 * 10
DELETION_MARK_DURATION_TICKS = 30 * 10

# 전체 금수 — EmbargoAllExecution.ts / Config.ts :: embargoAllCooldown()
#
# 봇은 건너뛴다. 봇과의 무역은 원래 관계를 안 타므로 끊어 봐야 나만 손해다.
EMBARGO_ALL_COOLDOWN_TICKS = 10 * 10
# 핵이 동맹을 깨는 문턱(`nukeAllianceBreakThreshold`). 반경 안 타일을 **가중치로**
# 센 합이다 — 내부 반경은 1점, 내부~외부는 0.5점(`computeNukeBlastCounts`).
# ⚠ 100은 "내부 100칸" 또는 "외부 200칸"이라는 뜻이라, 작은 지도에서는 타일만으로는
# 절대 안 넘는다. 그래서 **건물 경로가 따로 있다**(반경 안에 건물이 있으면 무조건).
NUKE_ALLIANCE_BREAK_THRESHOLD = 100.0
NUKE_BLAST_WEIGHT_INNER = 1.0
NUKE_BLAST_WEIGHT_OUTER = 0.5

# `allianceRequestDuration()` — 요청은 20초 뒤 자동 거절된다.
ALLIANCE_REQUEST_DURATION_TICKS = 20 * 10
# `allianceRequestCooldown()` — **같은 상대에게 30초 안에 다시 못 건다.**
# 만료(20초)보다 길다. 그래서 거절당하거나 만료된 뒤에도 10초를 더 기다려야 한다.
ALLIANCE_REQUEST_COOLDOWN_TICKS = 30 * 10

REL_NUKED = -100.0                 # NukeExecution
REL_MIRV = -100.0                  # MIRVExecution (양방향)
REL_TROOP_DONATION = 50.0          # DonateTroopExecution
# 기부 버튼 한 번에 나가는 몫. **원본은 골드도 병력도 1/3 이다**
# (`defaultDonationAmount` = `troops()/3`, `DonateGoldExecution` 의 `gold()/3n`).
# 우리는 1/4 였다(§5.90). 라디얼 메뉴가 액수를 안 넘기므로 이 기본값이 곧
# 사람이 보내는 액수다.
DONATION_DIVISOR = 3
REL_EMBARGO = -20.0                # NationExecution (걸면 −20, 풀면 +20)
REL_ATTACKED_ALLY = -20.0          # AiAttackBehavior — 동맹을 친 벌
REL_WARSHIP_SANK_TRADE = -7.5      # NationWarshipBehavior
REL_WARSHIP_SANK_OTHER = -15.0

# --- AI 의 전함 판단 (`NationWarshipBehavior`) --------------------------------
#
# ⚠ 이식 누락 스물다섯. 우리는 골드가 되면 무조건 지었다. 실측에서 판 전체
# 지출의 **85%**(535,000,000 / 2,140척)가 전함으로 갔다. 원본은 두 겹으로 막는다:
# 판단 tick 마다 50% 확률이고, **전함이 한 척도 없을 때만** 새로 짓는다.
# 그 뒤로는 보복(`maybeRetaliateWithWarship`)으로만 늘어나고 그것도 10척까지다.
WARSHIP_SPAWN_CHANCE = 50            # `random.chance(50)`
WARSHIP_SPAWN_RADIUS = 250           # 항구에서 이 반경 안 바다에 띄운다
WARSHIP_RETALIATION_CAP = 10         # 보복으로 늘릴 수 있는 상한
# 보복 확률 — easy 는 아예 안 한다
WARSHIP_RETALIATION_CHANCE = {"easy": 0, "medium": 15, "hard": 50, "impossible": 80}
# 이미 순찰 중인 배를 다시 보낼 때 보는 거리(`maybeMoveWarship`)
WARSHIP_REASSIGN_RANGE = 130

# 바다 독점 견제 (`counterWarshipInfestation`) — hard 이상만 한다.
# 한 나라가 바다를 전함으로 덮어 무역·상륙을 통째로 막는 것을 푸는 장치다.
WARSHIP_INFESTATION_GAME_MIN = 10    # 판 전체 전함이 이보다 많아야 본다
WARSHIP_INFESTATION_ENEMY_MIN = 10   # 한 적이 이보다 많이 가졌으면 표적
WARSHIP_INFESTATION_RICH_TOP = 3     # 부자 상위 몇 명만 나선다

# 들어오는 상륙선 선제 대응 (`trackIncomingTransportsAndRetaliate`)
#
# 목표까지 이만큼도 안 남았으면 손쓸 수 없다고 보고 넘긴다. 지금 배를 띄워도
# 상륙이 먼저 끝난다.
INCOMING_BOAT_TOO_CLOSE = 20
# 목표 근처에 이 거리 안으로 내 전함(또는 그 순찰 기점)이 있으면 이미 덮인 것으로 본다
INCOMING_BOAT_COVERED_RANGE = 90
# 대응 전함을 목표에서 이 반경 안 바다에 띄운다
INCOMING_BOAT_SPAWN_RADIUS = 30

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
# `emojiMessageDuration()` — 보낸 이모지가 **지도에 떠 있는** 시간(§5.96).
# 쿨다운(5초)과 우연히 같은 값이지만 **다른 것**이다: 쿨다운은 다시 보낼 수 있게
# 되는 때고, 이건 화면에서 사라지는 때다. 원본도 둘을 따로 둔다.
EMOJI_MESSAGE_DURATION_TICKS = 5 * 10

# 골드 기부 한 덩어리 크기(`getGoldChunkSize`). 덩어리당 관계 +5.
GOLD_CHUNK_SIZE: dict[str, float] = {
    "easy": 2_500.0, "medium": 5_000.0, "hard": 12_500.0, "impossible": 25_000.0,
}

# 병력 기부로 관계가 오르려면 **얼마나 줘야 하는가**(`getMinTroopsForRelationUpdate`).
# 받는 쪽 **병력 상한**을 이 두 값으로 나눈 사이에서 무작위로 뽑는다 —
# 원본 주석: *"1% 만 보내 좋은 관계를 사는 것을 막는다."* 난이도가 높을수록 더 내야 한다.
# ⚠ 문턱이 **무작위**인 것이 규칙이다. 고정이면 그 값 바로 위만 계속 보내면 된다.
TROOP_DONATION_MIN_DIV: dict[str, tuple[int, int]] = {
    "easy": (13, 11), "medium": (11, 9), "hard": (9, 7), "impossible": (7, 5),
}

# `numSpawnPhaseTurns()` — 기부 덩어리 크기 스케일링의 분모에 들어간다.
SPAWN_PHASE_TURNS = 100

# 시작 원(반경 4)에서 이만큼은 쓸 수 있어야 시작점으로 인정한다. **우리 값이다** —
# 원본은 사람이 고를 때 `requireAllValid=false` 로 걸러만 주는데, 그러면 반도 끝
# 세 칸을 골라도 시작이 돼 첫 공격에 사라진다.
SPAWN_MIN_TILES = 20

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
# 중립 땅을 칠 수 있는지 볼 때, 클릭한 칸에서 **이어진 무주지 덩어리**를 이만큼
# 안까지만 따라간다(`PlayerImpl.canAttack` 의 `manhattanDistFN(tile, 200)`).
# 내 국경에 닿지 않는 먼 대륙의 중립을 눌러도 공격이 켜지면 안 된다.
NEUTRAL_ATTACK_REACH = 200
CONQUER_PLAYER_TILES = 100                   # 수비자가 이 미만이면 통째로 흡수
# 전선 숫자를 어디에 띄울지 — `AttackImpl.clusterBorderTiles(30, 2)`.
# 조각이 이보다 작으면 버린다(가장 큰 하나는 예외). 둘까지만 띄운다 —
# 잘게 부서진 국경에서 숫자가 지도를 덮지 않게.
ATTACK_CLUSTER_MIN_SIZE = 30
ATTACK_CLUSTER_MAX = 2

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
# ⚠ 한 번 "읽는 곳이 0" 으로 보고 지웠다가 되돌렸다(2026-09-04). `src/` 만
# 세었기 때문이다 — `tools/verify_port.py` 의 **원본 값 대조표**와
# `test_nukes.py` 가 읽는다. `sam_range(1)` = 150 − 480/6 = 70 임을 재는 자리라
# 중복이 아니라 **대조점**이다. 읽는 곳을 셀 때 tests·tools 를 빼면 오탐이 난다.
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
# 클릭한 칸이 막혔을 때 **얼마나 멀리까지 대신 지을 자리를 찾는가**
# (`validStructureSpawnTiles` 의 `searchRadius`). 최소 거리와 같은 값이다 —
# 원본이 둘 다 15 를 쓴다. 전에 우리는 40 이었다(§5.86).
STRUCTURE_SEARCH_RADIUS = 15
# ⚠ **항구만 다르다**(`radiusPortSpawn`). 원본 `portSpawn` 은 위 반경으로 거른
# 자리들 중에서, 다시 **맨해튼 20** 안의 것만 보고 **맨해튼 거리 순**으로 고른다.
# 유클리드 순으로 고르면 대각선 쪽 후보가 먼저 뽑혀 자리가 달라진다(§5.89).
PORT_SPAWN_RADIUS = 20

# Game.ts :: MAX_UPGRADE_AMOUNT — 한 번에 올릴 수 있는 최대 레벨 수.
# 우리는 오래 1레벨씩만 올릴 수 있었다. 도시 값이 25만 → 50만 → 100만으로 뛰므로
# 후반에는 한 번에 여러 레벨을 사는 것이 실제 조작이다.
MAX_UPGRADE_AMOUNT = 50

# Game.ts :: STRUCTURE_BULK_STEPS — 대량 메뉴의 고정 중간 단계.
# 원본은 **네 칸을 늘 같은 자리에** 둔다: [1, 5, 10, 지금 살 수 있는 최대].
# 자리를 고정하는 이유가 주석에 적혀 있다 — "muscle memory". 살 수 없는 칸도
# 숨기지 않고 회색으로 남긴다.
STRUCTURE_BULK_STEPS: tuple[int, ...] = (5, 10)
# 핵은 더 작은 묶음이다. 원본 주석: *"x2 is the standard play against a single SAM"*.
NUKE_BULK_STEPS: tuple[int, ...] = (2, 5)

# NationAllianceBehavior.maybeBetray — AI 가 동맹을 깨는 네 가지 이유의 문턱.
# 난이도는 문턱이 아니라 **어느 이유를 보는가**로 들어간다(§5.51).
BETRAY_WEAK_TROOP_RATIO = 0.2        # 상한의 20% 미만이면 "거의 죽었다"
BETRAY_STRONGER_MULTIPLE = 10.0      # easy·medium — 내가 열 배면 깬다
BETRAY_TRAITOR_MARGIN = 1.2          # 배신자가 나보다 1.2배 넘게 강하면 참는다
BETRAY_LONE_NEIGHBOUR_MULTIPLE = 3.0 # 이웃이 하나뿐이고 내가 세 배면 깬다

# Config.ts :: SAM_CONSTRUCTION_TICKS
SAM_CONSTRUCTION_TICKS = 30 * 10

# Config.ts :: SiloCooldown() / SAMCooldown() — 둘 다 90 tick(9초)이다.
#
# **발사관은 레벨 수만큼이다.** 사일로/SAM 을 Lv3 으로 올리면 관이 셋이고, 쏜 관만
# 재장전에 들어간다(`missileTimerQueue`). 관이 전부 차면 그 기체는 쿨다운 상태다
# (`isInCooldown` = 큐 길이 == 레벨).
#
# ⚠ 우리는 이게 통째로 없었다. 사일로 한 기로 무한 연사가 됐고, **SAM 한 기가 판의
# 모든 핵을 영원히 100% 막았다.** 값을 따로 두는 이유는 원본이 따로 두기 때문이고,
# 지금 같은 값이라고 합치면 한쪽만 바뀔 때 조용히 어긋난다.
SILO_COOLDOWN_TICKS = 90
SAM_COOLDOWN_TICKS = 90
# `samUpgradeDuration()` = SAM 쿨다운의 절반. **업그레이드는 즉시가 아니다** —
# 사거리가 옛 값에서 새 값으로 이 시간에 걸쳐 선형으로 는다(§5.82).
SAM_UPGRADE_DURATION_TICKS = SAM_COOLDOWN_TICKS // 2
# `computeTargetScore` — SAM 이 한 tick 에 여러 핵을 볼 때 **무엇을 막을지**.
#
#   점수 = 수폭 보너스 + 거리 보너스 + 급한 정도
#
# 원본 주석이 두 값의 관계를 못 박아 뒀다: *"70,000 offset balances the distance
# bonus between Hydro at 100 and Atom at 30"* — **수폭이 70칸 더 멀어도 먼저**다.
SAM_SCORE_HYDROGEN_BONUS = 70_001
SAM_SCORE_DISTANCE_BASE = 200_000
SAM_SCORE_DISTANCE_PER_TILE = 1_000
SAM_SCORE_URGENCY_BASE = 10_000
SAM_SCORE_URGENCY_PER_TICK = 100


# --- 해상 (P4) ------------------------------------------------------------
#
# Config.ts :: boatMaxNumber() / warship*() / tradeShip*()

BOAT_MAX_NUMBER = 3            # 동시에 띄울 수 있는 수송선 수
# 클릭한 칸에서 상륙 지점을 찾는 반경(`closestReachableShore` 의 `maxDist`).
# 안쪽을 눌러도 이 안의 가장 가까운 해안으로 옮겨 준다.
LANDING_SEARCH_RANGE = 50
BOAT_TICKS_PER_MOVE = 1        # TransportShipExecution.ticksPerMove
# 퇴각해 돌아온 병력의 손실 비율. 원본 `TransportShipExecution.ts` 의
# `const malusForRetreat = 25` — 배를 돌리면 태운 병력의 25% 가 사라진다.
# ⚠ 여기 0.0 이 박혀 있었다. 퇴각 기능 자체가 없어서 아무도 안 읽던 값이다.
BOAT_RETREAT_MALUS_PCT = 0.25

WARSHIP_PATROL_RANGE = 100
# 무역선이 해안선 물 칸을 밟으면 이 tick 동안 나포 대상에서 빠진다
# (`safeFromPiratesCooldownMax`). 항구 앞에서 잡히지 않게 하는 장치다.
SAFE_FROM_PIRATES_TICKS = 20
# 나포 사거리 — 원본 `huntDownTradeShip` 은 맨해튼 5 안이면 그 자리에서 잡는다
PIRACY_CAPTURE_RANGE = 5
# `huntDownTradeShip` 은 tick 당 루프를 **2번** 돈다 — 무역선과 같은 속도(1칸/tick)
# 로는 영원히 못 따라잡기 때문이다. 추격이 성립하려면 더 빨라야 한다.
PIRACY_HUNT_STEPS = 2
# 순찰 목표를 뽑을 때 몇 번 실패하면 반경을 1.5배로 넓히는지, 그리고 몇 번까지
# 넓히는지 (`maxAttemptBeforeExpand` · `expandCount < 3`). 작은 만에 갇힌 배가
# 영원히 후보를 못 찾는 것을 막는 장치다.
PATROL_ATTEMPTS_BEFORE_EXPAND = 500
PATROL_MAX_EXPANDS = 3

# 수리 후퇴 (`WarshipExecution` 의 retreating/docked 상태)
#
# 체력이 최대의 이 비율 아래로 떨어지면 항구로 돌아간다. 항구가 없으면 안 간다 —
# 갈 곳이 없는데 순찰을 멈추면 그냥 표적이 된다.
WARSHIP_RETREAT_HEALTH_PERCENT = 75
WARSHIP_DOCKING_RANGE = 5            # 이 안에 들어오면 정박이다
# 정박 회복은 **항구 레벨 × 이 값**을 그 항구에 정박한 배들이 나눠 갖는다.
# 그래서 레벨이 곧 수리 능력이고, 한 항구에 몰리면 각자 느려진다.
WARSHIP_PORT_HEALING_PER_LEVEL = 5
# 후퇴 중에 **더 가까운 항구로 갈아타는** 문턱(`warshipPortSwitchThreshold`).
# 새 항구가 지금 목적지보다 이 비율보다 더 가까울 때만 바꾼다 — 1.0 으로 두면
# 배가 두 항구 사이에서 매 tick 목적지를 바꿔 제자리걸음한다.
WARSHIP_PORT_SWITCH_THRESHOLD = 0.75
WARSHIP_TARGETTING_RANGE = 130
WARSHIP_SHELL_ATTACK_RATE = 20  # 이 tick 마다 한 발
WARSHIP_PASSIVE_HEALING = 1          # 항구 근처면 tick 당 이만큼 회복
WARSHIP_PASSIVE_HEALING_RANGE = 150
# --- 전함 베테랑 (`Config.ts` "Warship veterancy" · `UnitImpl.recordKill`) ---
#
# ⚠ 우리는 오래 **격침 횟수를 그대로 레벨로 썼다**(§5.75). 원본은 넷을 나눠 둔다:
# 레벨 상한 · 레벨당 최대 체력 · 수송선 몇 척이 1레벨 · 무역선 몇 척이 1레벨.
WARSHIP_MAX_VETERANCY = 3            # `warshipMaxVeterancy`
WARSHIP_VETERANCY_SHELL_BONUS = 20   # 레벨당 포탄 피해 +20%
WARSHIP_VETERANCY_HEALTH_BONUS = 20  # 레벨당 최대 체력 +20%(즉시 회복은 아니다)
WARSHIP_VETERANCY_TRANSPORT_KILLS = 10   # 수송선 10척 = 1레벨
WARSHIP_VETERANCY_TRADE_CAPTURES = 25    # 무역선 25척 = 1레벨
WARSHIP_MAX_HEALTH = 1000
# 포탄 피해 = 250/250 × ((굴림−1)×25 + 200), 굴림은 1~5.
# 그래서 한 발이 200~300 이고, 체력 1000 인 전함은 4~5발을 견딘다.
SHELL_DAMAGE = 250
SHELL_LIFETIME = 50
SHELL_ROLL_MIN = 1
SHELL_ROLL_MAX = 5
SHELL_ROLL_STEP = 25
SHELL_ROLL_BASE = 200

# MIRV — MIRVExecution.warheadCount. **원본은 350발 고정이다.**
MIRV_WARHEAD_COUNT = 350

# 원본 크기 지도(`map`)의 육지 수. 작은 지도에서 규모를 맞출 때 **분모**로 쓴다.
# ⚠ 총 칸 수(2,000,000)가 아니라 **육지 수**다 — 분자도 육지 수이므로 여기가
# 총 칸 수면 원본 크기에서도 0.33 이 곱해진다(§5.57 에서 그렇게 틀려 있었다).
FULL_MAP_LAND = 651_569

# MIRV 탄두가 떨어지는 자리 — MIRVExecution.ts :: range / minimumSpread
#
# ⚠ 탄두는 **표적의 땅에만** 떨어진다. 반경 안 아무 데나가 아니다. 그리고 이미
# 정한 자리와 맨해튼 55 이상 떨어져야 한다 — 안 그러면 한 덩어리에 몰려 터져
# 350발이 한 발과 다를 바 없어진다. 자리를 못 찾으면(100번 시도) **그 탄두는
# 그냥 없다**(원본도 발 수를 채우려 하지 않는다).
MIRV_TARGET_RANGE = 1500
MIRV_MIN_SPREAD = 55
# ⚠ 시도 예산은 **전체 1,500번**이다. 세는 자리를 두 번 틀렸다:
#
#   - 원본은 분리 **20~11 tick 동안 매 tick 100번**을 굴려 둔다(`stagedTargets`)
#     — 한 번이 아니라 열 번이라 1,000번이다.
#   - 그 뒤 10 tick 전에 `finalizeDestinations(500)` 으로 더 채운다.
#
# 그리고 이건 **탄두마다가 아니라 전체** 예산이다. 던진 점 대부분이 버려지기
# 때문에(반경 1500 짜리 정사각형 안에 던지는데 지도는 2000×1000 이라 ⅔ 가
# 지도 밖) 350발은 **상한이지 목표가 아니다** — 원본에서도 큰 나라에 수십 발이
# 떨어진다.
MIRV_TARGET_ATTEMPTS = 1000 + 500

# 둘러싸인 영토 흡수 — PlayerExecution.ts :: ticksPerClusterCalc
#
# 국경 타일을 전부 덩어리로 묶는 계산이라 비싸다. 원본도 20 tick 에 한 번만 돌고,
# 나라마다 시작 tick 을 해시로 흩어 한 tick 에 몰리지 않게 한다.
ENCLAVE_CHECK_TICKS = 20
# ⚠ **작은 나라는 주기를 기다리지 않는다** — 원본 조건이 `또는` 이다
# (`ticks - lastCalc > 20 || numTilesOwned < 100`). 땅이 적으면 국경 타일도
# 적어 계산이 싸고, 작은 나라에게 갇힌 조각은 곧 죽음이라 늦게 반영하면
# **최대 2초 더 살아 있는다.**
ENCLAVE_ALWAYS_BELOW_TILES = 100

TRADE_SHORT_RANGE_DEBUFF = 300  # 이 거리 아래는 시그모이드가 눌러 크게 손해다
# `tradeShipSpawnRate` 의 시그모이드 — 배가 이 수를 넘어가면 스폰율이 0 으로
# 수렴한다. ⚠ **둘 다 읽는 곳이 0 이었다**(값은 `trade_spawn_rate` 안에
# 하드코딩돼 있었다). 매직 넘버가 로직 안에 박혀 있으면 그 자체로 규칙 위반이다.
TRADE_SPAWN_SIGMOID_MID = 400
TRADE_SPAWN_DECAY_HALFLIFE = 50     # `Math.LN2 / 50`

# 항구는 **10 tick 마다** 스폰을 굴린다(`PortExecution.tick` 의 `% 10`). 판 전체가
# 아니라 항구마다다 — 이걸 판 하나로 두면 항구가 몇이든 유통량이 같아진다.
TRADE_SPAWN_CHECK_PERIOD = 10
# 근접 보너스를 받는 후보 수 = `within(전체/3, 4, 전체)`.
TRADE_PROXIMITY_BONUS_DIVISOR = 3
TRADE_PROXIMITY_BONUS_MIN = 4

# 철도 (P4 마무리) — Config.ts :: trainStation*/trainGold/trainSpawnRate
#
# 역은 따로 짓는 게 아니라 **건물에 붙는다.** 사거리 15~110 안의 역끼리 이어진다 —
# 너무 가까우면 골드 찍어내기가 되고, 너무 멀면 노선이 지도를 가로지른다.
TRAIN_STATION_MIN_RANGE = 15
TRAIN_STATION_MAX_RANGE = 110
RAILROAD_MAX_SIZE = TRAIN_STATION_MAX_RANGE * 1.4142
TRAIN_SPEED = 4.0                    # 우리 값 — 원본은 경로 그래프를 따라간다

# 한 여정에 거치는 역 수의 상한. **우리 값이다** — 원본은 A* 가 찾은 경로 길이가
# 그대로 되고 따로 상한이 없다(§5.60 의 범위 결정). 무한 순회를 막으려고 둔다.
TRAIN_MAX_HOPS = 6

# 역 사이 최소 쿨다운(`ticksCooldown`). 한 역이 연달아 내지 못하게 한다.
TRAIN_STATION_COOLDOWN_TICKS = 10

# 기부 — Config.ts :: donateCooldown()
DONATE_COOLDOWN_TICKS = 10 * 10


# --- 판 ------------------------------------------------------------------
#
# 종료 조건 — WinCheckExecution.ts :: checkWinnerFFA
#
# ⚠ **주석이 오래 틀려 있었다**(§5.61). "openfront 는 시간 제한도 지배 승리도
# 없다"고 적혀 있었는데 **둘 다 있다.** `checkWinnerFFA` 가 매 tick 셋 중 하나를
# 본다: 점유율 초과 · 방 설정의 타이머 · **하드 시간 제한**.
#
#   percentageTilesOwnedToWin() = 80  (FFA. 분모는 **낙진을 뺀 땅**)
#   HARD_TIME_LIMIT_SECONDS     = 170 * 60 = 10,200초
#
# 틀린 것은 조건이 아니라 **값**이었다. 우리 900초는 원본의 **1/11** 이라,
# §5.55 에서 "MIRV 가 안 나간다"고 본 것이 사실은 그 자리였다.
MATCH_SECONDS = 10_200.0        # 170분 — 원본 `HARD_TIME_LIMIT_SECONDS`
DOMINATION_TILE_RATIO = 0.80    # 원본 `percentageTilesOwnedToWin` (FFA)
# 경고 테두리(`AlertFrame`). **큰 판에서 경고가 의미를 잃지 않게 하는 장치다** —
# 봇 400이 국경을 긁는 판에서 들어오는 공격마다 화면이 번쩍이면 아무도 안 본다.
ALERT_COOLDOWN_TICKS = 150          # 15초. 최근에 경고했으면 또 안 띄운다
ALERT_RETALIATION_WINDOW_TICKS = 150  # 15초. 내가 먼저 친 상대의 공격은 반격이다
ALERT_MIN_TROOPS_DIVISOR = 5        # 내 병력의 1/5 미만이면 안 띄운다

MIN_PLAYERS = 2
MAX_PLAYERS = 8
