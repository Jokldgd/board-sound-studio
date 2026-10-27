"""电平条：8px 圆角轨道 + 绿→橙→红横向渐变填充，平滑跟随（不回弹）。"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import theme

HEIGHT = 8


class LevelMeter(QWidget):
    """录音电平表。``setLevel(peak, rms)`` 传入 0..1 的实测值。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current = 0.0
        self._target = 0.0
        self._active = False
        self._phase = 0.0
        self.setFixedHeight(HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ------------------------------------------------------------------ 数据
    def setLevel(self, peak: float, rms: float = 0.0) -> None:  # noqa: N802
        """peak / rms 均为 0..1；用两者的加权让视觉更接近"响度"。"""
        target = max(float(peak), float(rms) * 1.6)
        self._target = max(0.0, min(1.0, target))

    def set_level(self, peak: float, rms: float = 0.0) -> None:
        self.setLevel(peak, rms)

    def setActive(self, active: bool) -> None:  # noqa: N802
        self._active = bool(active)
        if not self._active:
            self._target = 0.0
        self.update()

    def reset(self) -> None:
        self._target = 0.0
        self._current = 0.0
        self.update()

    def level(self) -> float:
        return self._current

    # ------------------------------------------------------------------ 平滑
    def _tick(self) -> None:
        # 上升快、下降稍慢，但不做回弹
        coefficient = 0.45 if self._target > self._current else 0.22
        self._current += (self._target - self._current) * coefficient
        if abs(self._target - self._current) < 0.002:
            self._current = self._target
        if self._active:
            self._phase = (self._phase + 0.09) % (2 * math.pi)
        self.update()

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        width = self.width()
        rect = QRectF(0, 0, width, HEIGHT)
        radius = HEIGHT / 2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.color("canvas")))
        painter.drawRoundedRect(rect, radius, radius)

        level = self._current
        if level <= 0.001:
            if self._active:
                # 录音中但静音：极淡的呼吸感（不抢视觉）
                alpha = 0.10 + 0.06 * (1 + math.sin(self._phase)) / 2
                glow = QColor(theme.color("accent"))
                glow.setAlphaF(alpha)
                painter.setBrush(glow)
                painter.drawRoundedRect(QRectF(0, 0, width * 0.06, HEIGHT), radius, radius)
            return

        gradient = QLinearGradient(0, 0, width, 0)
        gradient.setColorAt(0.0, QColor(theme.color("success")))
        gradient.setColorAt(0.62, QColor(theme.color("warning")))
        gradient.setColorAt(1.0, QColor(theme.color("record")))
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(0, 0, max(HEIGHT, width * level), HEIGHT), radius, radius)
