"""底栏：状态点 + 运行状态 / 正在播放 / 当前录音模式（32px）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from . import icons, theme

STATE_IDLE = "idle"
STATE_OK = "ok"
STATE_RECORDING = "recording"
STATE_WARNING = "warning"


class StatusBar(QWidget):
    """三段式底栏；只做显示。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(theme.SIZE["bottomBar"])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.SPACE["lg"], 0, theme.SPACE["lg"], 0)
        layout.setSpacing(theme.SPACE["sm"])

        self.dot = QLabel(self)
        self.dot.setObjectName("StatusDot")
        self.dot.setFixedSize(6, 6)
        self.dot.setProperty("state", STATE_IDLE)
        layout.addWidget(self.dot)

        self.status_label = QLabel("就绪", self)
        self.status_label.setObjectName("StatusText")
        layout.addWidget(self.status_label)

        layout.addStretch(1)

        self.playing_label = QLabel("—", self)
        self.playing_label.setObjectName("NowPlaying")
        layout.addWidget(self.playing_label)

        layout.addStretch(1)

        self.mode_icon = QLabel(self)
        self.mode_icon.setFixedSize(12, 12)
        layout.addWidget(self.mode_icon)

        self.mode_label = QLabel("麦克风", self)
        self.mode_label.setObjectName("ModeText")
        layout.addWidget(self.mode_label)

    # ------------------------------------------------------------------ 更新
    def set_status(self, text: str, state: str = STATE_OK, tooltip: str = "") -> None:
        self.status_label.setText(text)
        self.status_label.setToolTip(tooltip or text)
        if self.dot.property("state") != state:
            self.dot.setProperty("state", state)
            self._repolish(self.dot)

    def set_playing(self, text: str, playing: bool = False) -> None:
        self.playing_label.setText(text if playing else "—")
        self.playing_label.setObjectName("NowPlayingAccent" if playing else "NowPlaying")
        self._repolish(self.playing_label)

    def set_mode(self, text: str, icon_name: str = "mic") -> None:
        self.mode_label.setText(text)
        self.mode_icon.setPixmap(
            icons.pixmap(icon_name, 12, theme.color("textMuted"))
        )

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
