"""방사형 메뉴 — 원본의 조작 방식.

openfront 는 타일을 클릭하면 **원형 메뉴**가 뜬다(`MainRadialMenu.ts`).
루트에 공격 · 건설 · 보트 · 정보(외교)가 있고 각각 하위 메뉴로 들어간다.

우리가 처음 만든 "좌클릭 = 즉시 공격"은 원본과 다른 조작이었다. 그 방식으로는
골드를 쓸 수도, 외교를 할 수도 없어서 **사람이 할 수 있는 게 공격 하나뿐**이었다.

원본의 항목 구성(`RadialMenuElements.ts`):

    root
      ├ 공격   → 대상별 공격, 핵(원폭/수폭/MIRV)
      ├ 건설   → 도시·항구·공장·방어초소·사일로·SAM·전함
      ├ 보트   → 상륙
      └ 정보   → 동맹 요청/연장/파기 · 표적 · 금수 · 골드/병력 기부 · 이모지

여기서는 그 구조를 그대로 둔다. **채팅(`QuickChat`)만 없다** — 정해진 문구를
보내는 것이고 관계를 움직이지 않는다(`QuickChatExecution` 은 `displayChat` 만
한다). 순수 커뮤니케이션이라 싱글에서 할 일이 없다.

⚠ 이 줄에 *"이모지도 뺐다"* 고 적혀 있었는데 **낡아 있었다** — 이모지는
`한마디` 항목으로 있고 🖕 하나가 관계를 −100 움직인다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen

from . import palette as P

RADIUS_INNER = 42.0
RADIUS_OUTER = 118.0
LABEL_RADIUS = 80.0


@dataclass
class Item:
    """메뉴 한 칸.

    `enabled` 가 False 면 회색으로 뜨고 눌리지 않는다 — **숨기지 않는 것이 중요하다.**
    "왜 이걸 못 하지"를 알려면 항목이 보이면서 이유가 붙어야 한다."""

    label: str
    action: Callable[[], None] | None = None
    submenu: Callable[[], list["Item"]] | None = None
    enabled: bool = True
    hint: str = ""                     # 왜 못 하는지, 또는 비용
    colour: tuple[int, int, int] = (70, 78, 96)
    # 커서를 얹었을 때 지도에 그릴 사거리 원 `(타일, 반경)`. 원본 ghost preview 의
    # `rangeRadius` 자리다 — **놓기 전에 범위를 봐야** 골드를 안 버린다.
    preview: tuple[int, float] | None = None


@dataclass
class RadialMenu:
    """열려 있는 메뉴 하나. 위젯이 아니라 **그리기 상태**다 —
    지도 위젯이 자기 paintEvent 안에서 그린다."""

    centre: QPointF
    items: list[Item]
    tile: int
    stack: list[list[Item]] = field(default_factory=list)
    hovered: int = -1

    # --- 조회 -------------------------------------------------------------

    def slice_at(self, pos: QPointF) -> int:
        """커서가 몇 번 칸 위인가. 밖이거나 가운데면 -1."""
        d = pos - self.centre
        r = math.hypot(d.x(), d.y())
        if not (RADIUS_INNER <= r <= RADIUS_OUTER) or not self.items:
            return -1
        ang = math.degrees(math.atan2(d.y(), d.x())) + 90.0     # 위쪽이 0도
        ang %= 360.0
        return int(ang / (360.0 / len(self.items)))

    def hit_centre(self, pos: QPointF) -> bool:
        d = pos - self.centre
        return math.hypot(d.x(), d.y()) < RADIUS_INNER

    # --- 조작 -------------------------------------------------------------

    def hover(self, pos: QPointF) -> bool:
        i = self.slice_at(pos)
        if i != self.hovered:
            self.hovered = i
            return True
        return False

    def activate(self, pos: QPointF) -> bool:
        """클릭 처리. 메뉴를 닫아야 하면 True."""
        if self.hit_centre(pos):
            return not self.go_back()      # 가운데 = 뒤로, 최상위면 닫기
        i = self.slice_at(pos)
        if i < 0 or i >= len(self.items):
            return True                    # 바깥 클릭 = 닫기
        item = self.items[i]
        if not item.enabled:
            return False
        if item.submenu is not None:
            self.stack.append(self.items)
            self.items = item.submenu()
            self.hovered = -1
            return False
        if item.action is not None:
            item.action()
        return True

    def go_back(self) -> bool:
        if not self.stack:
            return False
        self.items = self.stack.pop()
        self.hovered = -1
        return True

    # --- 그리기 -----------------------------------------------------------

    def draw(self, p: QPainter, font_maker) -> None:
        if not self.items:
            return
        n = len(self.items)
        span = 360.0 / n
        rect_o = QRectF(self.centre.x() - RADIUS_OUTER, self.centre.y() - RADIUS_OUTER,
                        RADIUS_OUTER * 2, RADIUS_OUTER * 2)
        rect_i = QRectF(self.centre.x() - RADIUS_INNER, self.centre.y() - RADIUS_INNER,
                        RADIUS_INNER * 2, RADIUS_INNER * 2)

        p.setPen(QPen(QColor(20, 22, 28, 230), 1.5))
        for i, item in enumerate(self.items):
            base = QColor(*item.colour)
            if not item.enabled:
                base = QColor(52, 54, 60)
            if i == self.hovered and item.enabled:
                base = base.lighter(150)
            base.setAlpha(238)
            p.setBrush(base)
            # Qt 의 각도는 1/16도 단위, 3시 방향이 0, 반시계가 양수.
            # 우리는 12시에서 시계 방향으로 배치한다.
            start = int((90 - (i + 1) * span) * 16)
            p.drawPie(rect_o, start, int(span * 16))

        p.setBrush(QColor(24, 27, 34, 245))
        p.setPen(QPen(QColor(120, 128, 145), 1.5))
        p.drawEllipse(rect_i)

        # 라벨
        for i, item in enumerate(self.items):
            mid = math.radians(-90 + (i + 0.5) * span)
            x = self.centre.x() + math.cos(mid) * LABEL_RADIUS
            y = self.centre.y() + math.sin(mid) * LABEL_RADIUS
            font = font_maker(10, i == self.hovered)
            p.setFont(font)
            fm = QFontMetrics(font)
            colour = QColor(240, 242, 248) if item.enabled else QColor(140, 143, 150)
            p.setPen(QPen(QColor(10, 10, 14)))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p.drawText(int(x - fm.horizontalAdvance(item.label) / 2 + dx),
                           int(y + fm.height() / 4 + dy), item.label)
            p.setPen(QPen(colour))
            p.drawText(int(x - fm.horizontalAdvance(item.label) / 2),
                       int(y + fm.height() / 4), item.label)

        # 가운데: 뒤로 가기 또는 닫기
        font = font_maker(9, False)
        p.setFont(font)
        fm = QFontMetrics(font)
        mid_text = "뒤로" if self.stack else "닫기"
        p.setPen(QPen(QColor(215, 220, 232)))
        p.drawText(int(self.centre.x() - fm.horizontalAdvance(mid_text) / 2),
                   int(self.centre.y() + fm.height() / 4), mid_text)

        # 커서가 얹힌 항목의 설명 — 왜 못 하는지, 얼마인지
        if 0 <= self.hovered < n and self.items[self.hovered].hint:
            hint = self.items[self.hovered].hint
            font = font_maker(10, False)
            p.setFont(font)
            fm = QFontMetrics(font)
            w = fm.horizontalAdvance(hint) + 16
            box = QRectF(self.centre.x() - w / 2,
                         self.centre.y() + RADIUS_OUTER + 8, w, fm.height() + 10)
            p.setBrush(QColor(16, 20, 28, 235))
            p.setPen(QPen(QColor(90, 96, 112)))
            p.drawRoundedRect(box, 5, 5)
            p.setPen(QPen(QColor(232, 234, 240)))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, hint)
