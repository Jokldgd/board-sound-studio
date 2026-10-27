"""录音按钮：64px 圆钮，空闲红点 / 录音中白方块 + 外圈脉动（透明度 1↔0.55）。"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import theme

DIAMETER = 64


class RecordButton(QWidget):
    """圆形录音按钮。点击只发 ``clicked``，状态由外部 ``setRecording`` 决定。"""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._recording = False
        self._hover = False
        self._pulse = 1.0
        self.setFixedSize(DIAMETER, DIAMETER)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip("开始录音（F9）")

        self._animation = QPropertyAnimation(self, b"pulse", self)
        self._animation.setDuration(theme.ANIM["pulse"])
        self._animation.setStartValue(1.0)
        self._animation.setEndValue(0.55)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.setLoopCount(-1)

    # ------------------------------------------------------------------ 状态
    def isRecording(self) -> bool:  # noqa: N802
        return self._recording

    def setRecording(self, recording: bool) -> None:  # noqa: N802
        recording = bool(recording)
        if recording == self._recording:
            return
        self._recording = recording
        if recording:
            self.setToolTip("停止录音（F9）")
            self._animation.start()
        else:
            self.setToolTip("开始录音（F9）")
            self._animation.stop()
            self._pulse = 1.0
        self.update()

    # ------------------------------------------------------------------ 脉动属性
    def _get_pulse(self) -> float:
        return self._pulse

    def _set_pulse(self, value: float) -> None:
        self._pulse = float(value)
        self.update()

    pulse = Property(float, _get_pulse, _set_pulse)

    # ------------------------------------------------------------------ 事件
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

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

        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        if self._recording:
            # 外圈脉动：只改透明度，不外扩
            ring = QColor(theme.color("record"))
            ring.setAlphaF(0.65 * self._pulse)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(1, 1, -1, -1))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.color("card")))
            painter.drawEllipse(rect)

            size = 20.0
            square = QRectF(
                (self.width() - size) / 2, (self.height() - size) / 2, size, size
            )
            painter.setBrush(QColor(theme.color("record")))
            painter.drawRoundedRect(square, 4, 4)
        else:
            painter.setPen(QPen(QColor(theme.color("border")), 1))
            painter.setBrush(
                QColor(theme.color("hover") if self._hover else theme.color("card"))
            )
            painter.drawEllipse(rect)

            size = 24.0
            dot = QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.color("record")))
            painter.drawEllipse(dot)

        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.color("accent")), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(-1, -1, 1, 1))
