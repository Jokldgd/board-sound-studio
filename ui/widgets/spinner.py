"""加载指示：16px 旋转的 refresh 图标（用于导入/保存等异步操作的行内提示）。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QTransform
from PySide6.QtWidgets import QWidget

from .. import icons, theme


class Spinner(QWidget):
    """旋转图标；``start()`` / ``stop()`` 控制。"""

    def __init__(self, size: int = theme.SIZE["iconInline"], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = int(size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(self._size, self._size)

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def isRunning(self) -> bool:  # noqa: N802
        return self._timer.isActive()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pixmap = icons.pixmap("refresh-cw", self._size, theme.color("textMuted"))
        if pixmap.isNull():
            return
        center = QRectF(0, 0, self._size, self._size).center()
        painter.translate(center)
        painter.rotate(self._angle)
        painter.translate(-center)
        painter.drawPixmap(0, 0, self._size, self._size, pixmap)
