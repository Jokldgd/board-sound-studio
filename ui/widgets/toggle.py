"""开关（Toggle）：36×20 胶囊，开启为强调色。"""

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

WIDTH = 36
HEIGHT = 20
KNOB = 14


class Toggle(QWidget):
    """胶囊开关；``toggled(bool)`` 是唯一信号。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._position = 1.0 if checked else 0.0
        self._hover = False
        self.setFixedSize(WIDTH, HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(theme.ANIM["hover"] * 2)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ------------------------------------------------------------------ 状态
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, notify: bool = False) -> None:
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._animate(1.0 if checked else 0.0)
        if notify:
            self.toggled.emit(checked)
        self.update()

    def toggle(self) -> None:
        self.setChecked(not self._checked, notify=True)

    # ------------------------------------------------------------------ 动画属性
    def _get_position(self) -> float:
        return self._position

    def _set_position(self, value: float) -> None:
        self._position = float(value)
        self.update()

    position = Property(float, _get_position, _set_position)

    def _animate(self, target: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(target)
        self._animation.start()

    # ------------------------------------------------------------------ 事件
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggle()
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

        track = QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1)
        if self._checked:
            color = QColor(theme.color("accentHover") if self._hover else theme.color("accent"))
        elif self._hover:
            color = QColor(theme.color("pressed"))
        else:
            color = QColor(theme.color("pressed"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(track, HEIGHT / 2, HEIGHT / 2)

        left = 3.0
        travel = WIDTH - KNOB - 6.0
        knob = QRectF(left + travel * self._position, (HEIGHT - KNOB) / 2, KNOB, KNOB)
        painter.setBrush(QColor(theme.color("card")))
        painter.drawEllipse(knob)

        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.color("accent")), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(1, 1, WIDTH - 2, HEIGHT - 2), (HEIGHT - 2) / 2, (HEIGHT - 2) / 2
            )
