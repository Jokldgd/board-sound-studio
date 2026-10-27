"""拖拽覆盖层：2px 强调色虚线边框 + 8% 强调色底 + 居中提示。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class DropOverlay(QWidget):
    """拖拽时覆盖在列表区上方；由容器 ``setVisible`` 控制显隐。"""

    def __init__(self, text: str = "松开以导入", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        self.label = QLabel(text, self)
        self.label.setObjectName("DropText")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

    def setText(self, text: str) -> None:  # noqa: N802
        self.label.setText(text)
