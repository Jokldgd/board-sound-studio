"""空状态：48px 线性图标 + 主/副文案 + 次按钮。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .. import icons, theme


class EmptyState(QWidget):
    """列表为空时显示；副按钮发 ``action_requested``。"""

    action_requested = Signal()

    def __init__(
        self,
        icon_name: str = "audio-lines",
        title: str = "还没有音效",
        subtitle: str = "拖入文件或点击导入",
        action_text: str = "导入文件",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE["sm"])
        layout.addStretch(1)

        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setPixmap(icons.pixmap(icon_name, 48, theme.color("textFaint")))
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("EmptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setObjectName("EmptyText")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.subtitle_label)

        layout.addSpacing(theme.SPACE["xs"])
        self.action_button = QPushButton(action_text, self)
        self.action_button.setObjectName("SecondaryButton")
        self.action_button.setFixedHeight(28)
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_button.clicked.connect(self.action_requested.emit)
        layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(1)

    # ------------------------------------------------------------------ 配置
    def setTexts(self, title: str, subtitle: str) -> None:  # noqa: N802
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)

    def setIcon(self, name: str) -> None:  # noqa: N802
        self.icon_label.setPixmap(icons.pixmap(name, 48, theme.color("textFaint")))
