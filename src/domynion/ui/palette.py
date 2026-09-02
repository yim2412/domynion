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

# 8명까지. 인접한 두 나라가 헷갈리지 않게 색상환에서 떨어뜨렸고, 명도는 비슷하게
# 맞춰 어느 한 나라만 눈에 띄지 않도록 했다.
PLAYER_COLORS: list[RGB] = [
    (214, 78, 66),     # 붉은
    (74, 132, 206),    # 파랑
    (226, 176, 66),    # 노랑
    (110, 178, 106),   # 초록
    (170, 106, 196),   # 보라
    (226, 132, 72),    # 주황
    (94, 190, 190),    # 청록
    (222, 130, 168),   # 분홍
]

OWNER_BLEND = 0.66      # 소유자 색을 이만큼 섞는다. 1.0 이면 지형이 안 보인다
BORDER_COLOR: RGB = (18, 18, 22)
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

TEXTURE_AMP = 0.16      # 지형 질감의 세기. 크면 타일 경계가 도로 보인다
TEXTURE_PERIOD = 24     # 타일 몇 개마다 한 주기인가. **1 이면 체크무늬가 된다**
                        # 지도가 커져서(500x250) v0.1 의 6 보다 크게 잡아야 한다


def player_color(pid: int) -> RGB:
    return PLAYER_COLORS[pid % len(PLAYER_COLORS)]
