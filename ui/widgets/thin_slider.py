"""细滑块：4px 轨道 + 12px 圆滑块（自绘），支持"默认半透明、悬停恢复"。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSlider, QWidget

from .. import theme


class ThinSlider(QSlider):
    """行内音量 / 总音量用的细滑块。

    * ``setDimmed(True)``：默认 45% 透明度（规格：行内滑块默认 0.45，行 hover 恢复 1.0）；
    * 键盘可操作（继承 QSlider），焦点态画 2px 强调描边。
    """

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self._dimmed = True
        self._hover = False
        self.setFixedHeight(16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet("background: transparent;")

    # ------------------------------------------------------------------ 配置
    def setDimmed(self, dimmed: bool) -> None:
        dimmed = bool(dimmed)
        if dimmed != self._dimmed:
            self._dimmed = dimmed
            self.update()

    def is_dimmed(self) -> bool:
        return self._dimmed

    # ------------------------------------------------------------------ 事件
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        opacity = 1.0 if (self._hover or not self._dimmed or self.hasFocus()) else 0.45
        track_height = 4.0
        handle_radius = 6.0
        margin = handle_radius
        width = max(1.0, self.width() - margin * 2)
        top = (self.height() - track_height) / 2

        track = QRectF(margin, top, width, track_height)
        track_color = QColor(theme.color("pressed"))
        track_color.setAlphaF(0.9 * opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, track_height / 2, track_height / 2)

        span = max(1, self.maximum() - self.minimum())
        ratio = (self.value() - self.minimum()) / span
        filled = QRectF(margin, top, max(track_height, width * ratio), track_height)
        fill_color = QColor(theme.color("accent"))
        fill_color.setAlphaF(opacity)
        painter.setBrush(fill_color)
        painter.drawRoundedRect(filled, track_height / 2, track_height / 2)

        center_x = margin + width * ratio
        handle = QRectF(
            center_x - handle_radius,
            self.height() / 2 - handle_radius,
            handle_radius * 2,
            handle_radius * 2,
        )
        painter.setPen(QPen(QColor(theme.color("borderStrong")), 1))
        painter.setBrush(QColor(theme.color("card")))
        if self._hover or self.isSliderDown():
            painter.setPen(QPen(QColor(theme.color("accent")), 1.5))
        painter.drawEllipse(handle)

        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.color("accent")), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
                theme.RADIUS["xs"],
                theme.RADIUS["xs"],
            )
