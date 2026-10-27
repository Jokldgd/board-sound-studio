"""无边框标题栏：40px，左侧应用圆点 + 标题，中间拖动区，右侧三个窗口按钮。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .. import theme
from .icon_button import IconButton


class TitleBar(QWidget):
    """拖动 / 双击最大化交给主窗口处理，这里只发信号。"""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, title: str = "Soundboard Studio", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(theme.SIZE["titleBar"])
        self._drag_origin: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(8)

        self.dot = QLabel(self)
        self.dot.setObjectName("AppDot")
        self.dot.setFixedSize(16, 16)
        layout.addWidget(self.dot)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("AppTitle")
        layout.addWidget(self.title_label)

        layout.addStretch(1)

        self.minimize_button = IconButton("minus", 16, 28, "ghost", "最小化", self)
        self.minimize_button.clicked.connect(self.minimize_requested.emit)
        layout.addWidget(self.minimize_button)

        self.maximize_button = IconButton("maximize-2", 16, 28, "ghost", "最大化 / 还原", self)
        self.maximize_button.clicked.connect(self.maximize_requested.emit)
        layout.addWidget(self.maximize_button)

        self.close_button = IconButton("x", 16, 28, "danger", "关闭", self)
        self.close_button.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.close_button)

    # ------------------------------------------------------------------ 拖动
    def set_maximized(self, maximized: bool) -> None:
        self.maximize_button.setIconName("maximize-2" if not maximized else "square")
        self.maximize_button.setToolTip("还原" if maximized else "最大化")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            handle = window.windowHandle() if window is not None else None
            if handle is not None and hasattr(handle, "startSystemMove") and handle.startSystemMove():
                event.accept()
                return
            self._drag_origin = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            window = self.window()
            if window is not None and not window.isMaximized():
                window.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
