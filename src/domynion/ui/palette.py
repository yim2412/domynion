"""색 — 지형과 소유자.

원칙은 하나다: **영토가 덩어리로 읽혀야 한다.** 그래서 격자선을 그리지 않고,
소유자가 다른 변에만 굵은 경계를 넣어 그 선이 화면의 유일한 선이 되게 한다.

지형은 색으로만 구분한다. 나무·산 문양을 또렷하게 그리면 칸마다 같은 그림이 반복돼
지운 격자가 되살아난다.
"""

from __future__ import annotations

from ..core.constants import Terrain

RGB = tuple[int, int, int]

# 육지는 채도를 낮게 깐다. 소유자 색을 위에 섞을 자리를 남겨 둬야 하기 때문이다.
# 지형끼리는 **명도**로 갈라 둔다. 소유자 색을 위에 섞으면 색상(hue)은 거의 지워지고
# 명도만 남기 때문이다 — 색상으로만 구분하면 영토 안에서 지형이 안 보인다.
TERRAIN_COLORS: dict[Terrain, RGB] = {
    Terrain.OCEAN:      (26, 48, 70),
    Terrain.PLAINS:     (168, 176, 124),
    Terrain.HIGHLAND:   (150, 130, 94),
    Terrain.MOUNTAIN:   (178, 178, 182),
    Terrain.IMPASSABLE: (52, 52, 56),
}

# 나라 색 — **원본 `default-theme.json` 의 세 벌 그대로**(§5.95).
#
# ⚠ **여기 색이 여덟 개였다.** v0.1 이 4~6명짜리라 그때는 맞았는데, §5.51 에서
# 판을 **나라 72 + 봇 400** 으로 올리면서 이 파일만 그대로 남았다. 472명이 도는
# 지도에서 색이 여덟이면 **평균 59명이 같은 색**이라, 맞닿은 두 나라가 한 덩어리로
# 보이고 국경선만이 유일한 단서가 된다.
#
# ⚠ **원본은 색으로 종류를 나눈다.** 봇은 채도를 뺀 회색빛, 나라는 중간, 사람은
# 선명하다(tailwind 계열). 그래서 지도만 봐도 *누가 사람인지*를 안다 — 우리는
# 셋을 같은 통에서 뽑고 있었다.
#
# 16진 문자열로 두는 것은 원본과 눈으로 대조하기 위해서다. 값을 옮길 때 소스를
# 여는 규칙(§6)이 실제로 지켜지려면 형태가 같아야 한다.
NATION_HEX: tuple[str, ...] = (
    "#d2d264", "#b4d278", "#aabe64", "#50c878", "#82c882", "#8cb48c",
    "#a0bea0", "#a0b48c", "#64a050", "#648c6e", "#64b4a0", "#82b4aa",
    "#aabeb4", "#648296", "#78a0c8", "#8c96b4", "#64d2d2", "#8cb4dc",
    "#82aabe", "#64b4e6", "#5082be", "#7878be", "#966ebe", "#a078a0",
    "#aa8cbe", "#b482b4", "#be8c96", "#b464e6", "#b4a0b4", "#aa96aa",
    "#968296", "#e6b4b4", "#d2a0c8", "#e682b4", "#d264a0", "#be6482",
    "#dc7878", "#c8826e", "#e68c8c", "#e66464", "#e69664", "#d28c50",
    "#e6b450", "#c8a06e", "#be9682", "#beb4a0", "#b4aa8c", "#c8c88c",
    "#beaa64",
)

BOT_HEX: tuple[str, ...] = (
    "#96a08c", "#a0a096", "#aaaa8c", "#aaaa78", "#96a078", "#96aa82",
    "#96aa96", "#82aa82", "#8ca08c", "#789664", "#788c78", "#64aa82",
    "#78a096", "#82a096", "#78aaaa", "#78a0be", "#8296aa", "#8296a0",
    "#8c96a0", "#8ca0aa", "#96a0a0", "#6478a0", "#78828c", "#8282a0",
    "#8c828c", "#8c78a0", "#968296", "#968ca0", "#a082a0", "#aa96aa",
    "#a078be", "#a07882", "#aa788c", "#aa8278", "#aa8282", "#b48c8c",
    "#be82a0", "#be7878", "#be8c78", "#bea064", "#aa8c64", "#a08c82",
    "#aa9682", "#a09678", "#a0968c", "#a08c96", "#a096a0", "#968c96",
    "#b4a0a0",
)

HUMAN_HEX: tuple[str, ...] = (
    "#a3e635", "#84cc16", "#10b981", "#34d399", "#2dd4bf", "#4ade80",
    "#6ee7b7", "#86efac", "#97ffbb", "#baffc9", "#e6fad2", "#22c55e",
    "#43be54", "#52b788", "#30b2b4", "#e6fffa", "#dcf0fa", "#e9d5ff",
    "#ccccff", "#dcdcff", "#cae1ff", "#93c5fd", "#7dd3fc", "#63cafd",
    "#38bdf8", "#60a5fa", "#3b82f6", "#4f46e5", "#7c3aed", "#9333ea",
    "#b388ff", "#a78bfa", "#d946ef", "#a855f7", "#be5cfb", "#c084fc",
    "#f0abfc", "#f472b6", "#ec4899", "#dc2626", "#ef4444", "#eb4b4b",
    "#f56565", "#f87171", "#fb7185", "#fda4af", "#fca5a5", "#ffcce5",
    "#fad7e1", "#fbebf5", "#f0f0c8", "#fafad2", "#fff0c8", "#ffdfba",
    "#fcd34d", "#fbbf24", "#eab308", "#ca8a04", "#f59e0b", "#fb923c",
    "#f97316", "#ea580c", "#854d0e",
)

# 통을 다 쓰면 넘어가는 예비 통. 나라가 72명인데 나라 통은 49개다.
FALLBACK_HEX: tuple[str, ...] = (
    "#230000", "#2d0000", "#370000", "#410000", "#4b0000", "#550000",
    "#5f0000", "#690000", "#730000", "#7d0000", "#870000", "#910000",
    "#9b0000", "#a50000", "#af0000", "#b90000", "#c30005", "#cd000a",
    "#d7000f", "#e10014", "#eb0019", "#f5001e", "#ff0023", "#ff0a2d",
    "#ff1437", "#ff1e41", "#ff284b", "#ff3255", "#ff3c5f", "#ff4669",
    "#ff5073", "#ff5a7d", "#ff6487", "#ff6e91", "#ff789b", "#ff82a5",
    "#ff8caf", "#ff96b9", "#ffa0c3", "#ffaacd", "#ffb4d7", "#ffbee1",
    "#ffc8eb", "#002d00", "#003700", "#004100", "#004b00", "#005500",
    "#005f00", "#006900", "#007300", "#007d00", "#008700", "#009100",
    "#009b00", "#00a500", "#00af00", "#00b900", "#00c305", "#00cd0a",
    "#00d70f", "#00e114", "#00eb19", "#00f51e", "#00ff23", "#0aff2d",
    "#14ff37", "#1eff41", "#28ff4b", "#32ff55", "#3cff5f", "#46ff69",
    "#50ff73", "#5aff7d", "#64ff87", "#6eff91", "#78ff9b", "#82ffa5",
    "#8cffaf", "#96ffb9", "#a0ffc3", "#aaffcd", "#b4ffd7", "#beffe1",
    "#c8ffeb", "#000023", "#00002d", "#000037", "#000041", "#00004b",
    "#000055", "#00005f", "#000069", "#000073", "#00007d", "#000087",
    "#000091", "#00009b", "#0000a5", "#0000af", "#0000b9", "#0500c3",
    "#0a00cd", "#0f00d7", "#1400e1", "#1900eb", "#1e00f5", "#2300ff",
    "#2d0aff", "#3714ff", "#411eff", "#4b28ff", "#5532ff", "#5f3cff",
    "#6946ff", "#7350ff", "#7d5aff", "#8764ff", "#916eff", "#9b78ff",
    "#a582ff", "#af8cff", "#b996ff", "#c3a0ff", "#cdaaff", "#d7b4ff",
    "#e1beff", "#ebc8ff", "#230023", "#2d002d", "#370037", "#410041",
    "#4b004b", "#550055", "#5f005f", "#690069", "#730073", "#7d007d",
    "#870087", "#910091", "#9b009b", "#a500a5", "#af00af", "#b900b9",
    "#c305c3", "#cd0acd", "#d70fd7", "#e114e1", "#eb19eb", "#f51ef5",
    "#ff23ff", "#ff2dff", "#ff37ff", "#ff41ff", "#ff4bff", "#ff55ff",
    "#ff5fff", "#ff69ff", "#ff73ff", "#ff7dff", "#ff87ff", "#ff91ff",
    "#ff9bff", "#ffa5ff", "#ffafff", "#ffb9ff", "#ffc3ff", "#ffcdff",
    "#ffd7ff", "#002323", "#002d2d", "#003737", "#004141", "#004b4b",
    "#005555", "#005f5f", "#006969", "#007373", "#007d7d", "#008787",
    "#009191", "#009b9b", "#00a5a5", "#00afaf", "#00b9b9", "#05c3c3",
    "#0acdcd", "#0fd7d7", "#14e1e1", "#19ebeb", "#1ef5f5", "#23ffff",
    "#2dffff", "#37ffff", "#41ffff", "#4bffff", "#55ffff", "#5fffff",
    "#69ffff", "#73ffff", "#7dffff", "#87ffff", "#91ffff", "#9bffff",
    "#a5ffff", "#afffff", "#b9ffff", "#c3ffff", "#cdffff", "#d7ffff",
    "#232300", "#2d2d00", "#373700", "#414100", "#4b4b00", "#555500",
    "#5f5f00", "#696900", "#737300", "#7d7d00", "#878700", "#919100",
    "#9b9b00", "#a5a500", "#afaf00", "#b9b900", "#c3c305", "#cdcd0a",
    "#d7d70f", "#e1e114", "#ebeb19", "#f5f51e", "#ffff23", "#ffff2d",
    "#ffff37", "#ffff41", "#ffff4b", "#ffff55", "#ffff5f", "#ffff69",
    "#ffff73", "#ffff7d", "#ffff87", "#ffff91", "#ffff9b", "#ffffa5",
    "#ffffaf", "#ffffb9", "#ffffc3", "#ffffcd", "#ffffd7", "#d7ffc8",
    "#e1ffaf", "#f0faa0", "#f5f5af", "#96c8ff", "#a0d7ff", "#aae1ff",
    "#b4ebfa", "#bef5f0", "#d2fff5", "#dcffff", "#e6faff", "#f0f0ff",
    "#fae6ff", "#aabeff", "#b4b4ff", "#c8aaff", "#be8cc3", "#c391c8",
    "#c896cd", "#cd9bd2", "#d2a0d7", "#d7a5dc", "#dcaae1", "#e1afe6",
    "#e6b4eb", "#ebb9f0", "#f0bef5", "#f5c3fa", "#fac8ff", "#ffcdff",
    "#ffd2ff", "#ffd2fa", "#ffcdf5", "#ffd7f5", "#dca0ff", "#eb96ff",
    "#f5a0f0", "#ffaae1", "#ffb9d7", "#ffc3eb", "#ffc8dc", "#ffd2e6",
    "#ffdceb", "#ffdcfa", "#ffe1ff", "#ffe6f5", "#ffebeb", "#ffd7c3",
    "#ffe1b4", "#ffe6be", "#ffebc8", "#fff5d2", "#fff0dc",
)


def _hex(c: str) -> RGB:
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


NATION_COLORS: list[RGB] = [_hex(c) for c in NATION_HEX]
BOT_COLORS: list[RGB] = [_hex(c) for c in BOT_HEX]
HUMAN_COLORS: list[RGB] = [_hex(c) for c in HUMAN_HEX]
FALLBACK_COLORS: list[RGB] = [_hex(c) for c in FALLBACK_HEX]

_POOLS: dict[str, list[RGB]] = {
    "nation": NATION_COLORS,
    "bot": BOT_COLORS,
    "human": HUMAN_COLORS,
}

# 예전 이름. 종류를 모르는 자리(그림 파일 렌더러 등)가 쓰던 통이다.
PLAYER_COLORS: list[RGB] = NATION_COLORS

OWNER_BLEND = 0.66      # 소유자 색을 이만큼 섞는다. 1.0 이면 지형이 안 보인다
BORDER_COLOR: RGB = (18, 18, 22)

# 국경 색은 **이웃과의 관계로 갈린다**(§5.93 · 원본 `PlayerView.computeColors`).
# 원본이 기본 국경색을 목표색 쪽으로 35% 섞는다 — 색을 갈아 치우지 않고 *물들인다*.
# 그래야 나라 색을 여전히 알아볼 수 있다.
BORDER_TINT_RATIO = 0.35
FRIENDLY_TINT_TARGET: RGB = (0, 255, 0)
EMBARGO_TINT_TARGET: RGB = (255, 0, 0)


def _mix(base: RGB, target: RGB, ratio: float) -> RGB:
    return tuple(round(b * (1 - ratio) + t * ratio)      # type: ignore[return-value]
                 for b, t in zip(base, target))


BORDER_COLOR_NEUTRAL: RGB = BORDER_COLOR
BORDER_COLOR_FRIENDLY: RGB = _mix(BORDER_COLOR, FRIENDLY_TINT_TARGET, BORDER_TINT_RATIO)
BORDER_COLOR_EMBARGO: RGB = _mix(BORDER_COLOR, EMBARGO_TINT_TARGET, BORDER_TINT_RATIO)

# 방어된 국경은 **체커보드로 교차**한다(`defendedBorderColors` + `(x+y)` 패리티).
#
# ⚠ **원본은 두 단계를 다 어둡게 뺀다**(darken 0.2 / 0.4). 우리 기본 국경색이
# 거의 검정(18,18,22)이라 그대로 옮기면 둘 다 검정으로 뭉개진다 — 그래서 방향만
# 뒤집어 **밝은 쪽으로** 두 단계를 뺐다.
#
# ⚠ **두 단계가 다 기본색과 달라야 한다.** 처음에 어두운 쪽을 기본색 그대로 뒀는데,
# 그러면 방어된 국경의 절반이 평범한 국경과 똑같아져 **교차가 신호가 아니라
# 얼룩으로 읽힌다.** 변이(패리티 고정)가 살아남아서 알았다 — 원본이 굳이 두 값을
# 다 옮기는 이유가 여기 있었다.
DEFENDED_LIGHTEN_LIGHT = 0.45
DEFENDED_LIGHTEN_DARK = 0.20


def defended_pair(base: RGB) -> tuple[RGB, RGB]:
    """방어된 국경의 (밝은 칸, 어두운 칸). **둘 다 기본색과 다르다.**"""
    return (_mix(base, (255, 255, 255), DEFENDED_LIGHTEN_LIGHT),
            _mix(base, (255, 255, 255), DEFENDED_LIGHTEN_DARK))


# 관계 → 색. 순서가 `BorderRelation` 값 그대로다.
BORDER_RELATION_COLORS: tuple[RGB, RGB, RGB] = (
    BORDER_COLOR_NEUTRAL, BORDER_COLOR_FRIENDLY, BORDER_COLOR_EMBARGO,
)

COAST_COLOR: RGB = (20, 38, 56)
LABEL_COLOR: RGB = (255, 255, 255)
LABEL_SHADOW: RGB = (0, 0, 0)

# 한글이 되는 폰트를 **명시한다.** Qt 기본 폰트에 한글 글리프가 없으면 글자가 전부
# 두부(□)가 된다 — 오프스크린 렌더에서 실제로 그랬다.
UI_FONT_FAMILIES = ("Malgun Gothic", "맑은 고딕", "Noto Sans KR",
                    "Segoe UI", "Arial")

# 유닛 표시. 건물은 **모양으로** 구분한다 — 색은 이미 소유자를 뜻하므로
# 색으로 종류까지 나타내면 둘 다 안 읽힌다.
UNIT_GLYPH = {
    "City": "◉",
    "Port": "⚓",
    "Factory": "▦",
    "Defense Post": "◈",
    "Missile Silo": "▲",
    "SAM Launcher": "◮",
}
UNIT_OUTLINE: RGB = (12, 12, 16)
UNIT_FILL: RGB = (250, 250, 252)
UNIT_MIN_ZOOM = 0.45      # 이보다 작으면 아이콘이 점이 돼 오히려 지저분하다

# 유닛 진행바 (`overlays.unit_bar` · 원본 `BarPass`). **종류마다 색이 달라야 한다** —
# 셋이 같은 자리에 뜨는데 색이 같으면 "차고 있다"와 "사라지고 있다"를 못 가른다.
BAR_BG: RGB = (12, 12, 16)
BAR_COLOR = {
    "deletion":     (255, 96, 96),      # 거꾸로 줄어든다 — 빨강
    "construction": (150, 230, 255),    # 무역선과 같은 청록 계열(건설 중)
    "reload":       (255, 220, 140),    # 전함과 같은 호박색(무장 준비)
}
BAR_HEIGHT = 3            # 픽셀. 줌과 무관하게 얇게 — 아이콘을 덮으면 안 된다

BOAT_COLOR: RGB = (240, 240, 245)
WARSHIP_COLOR: RGB = (255, 220, 140)
TRADE_COLOR: RGB = (150, 230, 255)
NUKE_COLOR: RGB = (255, 120, 90)
FALLOUT_COLOR: RGB = (190, 255, 120)

# 핵 낙하 예고 원(§5.92) — **쏜 쪽과의 관계로 색이 갈린다.** 원본 색 그대로다
# (self=초록 · ally=노랑 · enemy=빨강). 내 핵과 적 핵을 같은 색으로 그리면
# 화면에 원이 여럿 뜰 때 무엇을 피해야 하는지 알 수 없다.
TELEGRAPH_COLORS: tuple[RGB, RGB, RGB] = (
    (80, 230, 110),     # SELF
    (240, 220, 90),     # FRIENDLY
    (255, 80, 80),      # ENEMY
)
# 내 수송선의 상륙 표적 고리.
ATTACK_RING_COLOR: RGB = (255, 255, 255)
# 전선 위 병력 숫자. 원본 `AttackingTroopsController` 의 두 색 그대로 —
# 나가는 공격은 Aquarius(#3fa9f5), 들어오는 공격은 red-400(#f87171).
# **색이 방향을 말한다** — 숫자만 있으면 내 부대인지 적 부대인지 알 수 없다.
ATTACK_LABEL_OUT: RGB = (0x3f, 0xa9, 0xf5)
ATTACK_LABEL_IN: RGB = (0xf8, 0x71, 0x71)
ATTACK_LABEL_MIN_PX = 9        # 이보다 작아지면 안 그린다(라벨과 같은 규칙)

TEXTURE_AMP = 0.16      # 지형 질감의 세기. 크면 타일 경계가 도로 보인다
TEXTURE_PERIOD = 24     # 타일 몇 개마다 한 주기인가. **1 이면 체크무늬가 된다**
                        # 지도가 커져서(500x250) v0.1 의 6 보다 크게 잡아야 한다


def player_color(pid: int, kind: str = "nation") -> RGB:
    """`pid` 의 색. **종류마다 통이 다르다**(원본 `theme.territoryColor`).

    통을 다 쓰면 **예비 통**으로 넘어간다(원본 `ColorAllocator` 도 그렇다).
    나라 통이 49개인데 나라가 72명이라 이게 없으면 스물셋이 겹친다.

    ⚠ **원본과 배정 방식이 다르다.** 원본은 요청 순서대로 주면서 *이미 나간 색과
    가장 멀리 떨어진 것*을 고른다(Lab 거리). 우리는 pid 로 바로 찾는다 — 단일
    프로세스라 요청 순서라는 개념이 없고, pid 로 찾으면 같은 판을 다시 열어도
    색이 같기 때문이다. **결과 색은 다르지만 성질(겹치지 않는다)은 같다.**"""
    pool = _POOLS.get(kind, NATION_COLORS)
    if pid < len(pool):
        return pool[pid]
    return FALLBACK_COLORS[(pid - len(pool)) % len(FALLBACK_COLORS)]
