"""胶囊分段控件：三段模式切换（麦克风 / 系统声音 / 单个应用），自绘。

容器：背景-画布 + 胶囊圆角 + 2px 内边距；选中段：强调色填充 + 白字；
未选中：透明 + 文字-次，悬停浅灰；不可用：文字-弱 + tooltip 说明原因。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import icons, theme

SEGMENT_HEIGHT = 32
CONTAINER_PADDING = 2
ICON_SIZE = 16
GAP = 6


class Segmented(QWidget):
    """``items`` 为 ``[{"id", "text", "icon"}]``；``changed(id)`` 是唯一信号。"""

    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[dict] = []
        self._current = ""
        self._enabled: dict[str, bool] = {}
        self._hover_index = -1
        self._pressed_index = -1
        self.setFixedHeight(SEGMENT_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ 配置
    def setItems(self, items: list[dict]) -> None:  # noqa: N802
        self._items = [dict(item) for item in items]
        for item in self._items:
            self._enabled.setdefault(item["id"], True)
        if self._current not in {i["id"] for i in self._items}:
            self._current = self._items[0]["id"] if self._items else ""
        self.update()

    def setCurrent(self, item_id: str, notify: bool = False) -> None:  # noqa: N802
        if item_id not in {i["id"] for i in self._items}:
            return
        if item_id == self._current:
            return
        self._current = item_id
        self.update()
        if notify:
            self.changed.emit(item_id)

    def current(self) -> str:
        return self._current

    def setItemEnabled(self, item_id: str, enabled: bool, tooltip: str = "") -> None:  # noqa: N802
        self._enabled[item_id] = bool(enabled)
        for index, item in enumerate(self._items):
            if item["id"] == item_id:
                item["tooltip"] = tooltip
                break
        self.update()

    def setAllEnabled(self, enabled: bool) -> None:  # noqa: N802
        for item in self._items:
            self._enabled[item["id"]] = bool(enabled)
        self.update()

    def isItemEnabled(self, item_id: str) -> bool:  # noqa: N802
        return bool(self._enabled.get(item_id, True))

    # ------------------------------------------------------------------ 几何
    def _segment_rect(self, index: int) -> QRectF:
        if not self._items:
            return QRectF()
        width = (self.width() - CONTAINER_PADDING * 2) / len(self._items)
        return QRectF(
            CONTAINER_PADDING + width * index,
            CONTAINER_PADDING,
            width,
            self.height() - CONTAINER_PADDING * 2,
        )

    def _index_at(self, x: float) -> int:
        for index in range(len(self._items)):
            if self._segment_rect(index).contains(x, self.height() / 2):
                return index
        return -1

    # ------------------------------------------------------------------ 事件
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        index = self._index_at(event.position().x())
        if index < 0:
            return
        item = self._items[index]
        if not self.isItemEnabled(item["id"]):
            self.setToolTip(item.get("tooltip") or "当前不可用")
            return
        self._pressed_index = index
        self.setCurrent(item["id"], notify=True)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pressed_index = -1
        self.update()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._index_at(event.position().x())
        if index != self._hover_index:
            self._hover_index = index
            item = self._items[index] if 0 <= index < len(self._items) else None
            self.setToolTip((item or {}).get("tooltip", ""))
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._items:
            return
        index = next(
            (i for i, item in enumerate(self._items) if item["id"] == self._current), 0
        )
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if event.key() == Qt.Key.Key_Left else 1
            for offset in range(1, len(self._items) + 1):
                candidate = (index + step * offset) % len(self._items)
                item = self._items[candidate]
                if self.isItemEnabled(item["id"]):
                    self.setCurrent(item["id"], notify=True)
                    break
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        radius = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.color("canvas")))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), radius, radius)

        font_px, weight = theme.font_px("caption")
        font = QFont()
        font.setPixelSize(font_px)
        font.setWeight(QFont.Weight(weight))

        for index, item in enumerate(self._items):
            rect = self._segment_rect(index)
            selected = item["id"] == self._current
            enabled = self.isItemEnabled(item["id"])

            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.color("accent")))
                inner = rect.adjusted(0, 0, 0, 0)
                painter.drawRoundedRect(inner, inner.height() / 2, inner.height() / 2)
                text_color = "#FFFFFF"
                icon_color = "#FFFFFF"
            else:
                if enabled and index == self._hover_index:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(theme.color("hover")))
                    painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
                text_color = theme.color("textMuted") if enabled else theme.color("textFaint")
                icon_color = text_color

            painter.setFont(font)
            painter.setPen(QPen(QColor(text_color)))
            text = item["text"]
            icon_name = item.get("icon", "")
            text_width = painter.fontMetrics().horizontalAdvance(text)
            total = text_width + (ICON_SIZE + GAP if icon_name else 0)
            start_x = rect.center().x() - total / 2

            if icon_name:
                pixmap = icons.pixmap(icon_name, ICON_SIZE, icon_color)
                if not pixmap.isNull():
                    painter.drawPixmap(
                        int(start_x),
                        int(rect.center().y() - ICON_SIZE / 2),
                        ICON_SIZE,
                        ICON_SIZE,
                        pixmap,
                    )
                start_x += ICON_SIZE + GAP

            text_rect = QRectF(start_x, rect.top(), text_width + 2, rect.height())
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                text,
            )

        if self.hasFocus() and self._current:
            index = next(
                (i for i, item in enumerate(self._items) if item["id"] == self._current), -1
            )
            if index >= 0:
                rect = self._segment_rect(index).adjusted(1, 1, -1, -1)
                painter.setPen(QPen(QColor(theme.color("text")), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
