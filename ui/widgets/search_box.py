"""搜索框：左侧放大镜图标 + 无边框输入（仅在列表条目 > 15 时显示）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from .. import icons, theme

WIDTH = 180
HEIGHT = 28


class SearchBox(QWidget):
    """``text_changed(str)`` 实时发出（已去空白）。"""

    text_changed = Signal(str)

    def __init__(self, placeholder: str = "搜索音效", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(WIDTH, HEIGHT)
        self.setObjectName("SearchBox")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(14, 14)
        self.icon_label.setPixmap(icons.pixmap("search", 14, theme.color("textFaint")))
        layout.addWidget(self.icon_label)

        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFrame(False)
        self.edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; padding: 0px; }"
        )
        self.edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.edit, 1)

        self.setStyleSheet(
            f"QWidget#SearchBox {{ background: {theme.color('card')};"
            f" border: 1px solid {theme.color('border')};"
            f" border-radius: {theme.RADIUS['sm']}px; }}"
        )

    # ------------------------------------------------------------------ API
    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:  # noqa: N802
        self.edit.setText(text)

    def clear(self) -> None:
        self.edit.clear()

    def focus(self) -> None:
        self.edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _on_text_changed(self, text: str) -> None:
        self.text_changed.emit(text.strip())
