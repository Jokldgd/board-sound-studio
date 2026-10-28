"""顶栏：输出路由（虚拟声卡 / 双输出 / 仅本地）+ 总音量 + 互斥 + 设置。

路由语义化是这里的关键：
* 模式 A 只显示"输出 → CABLE Input"（本地靠 Windows「侦听此设备」），不给用户选设备；
* 模式 B 才出现两个下拉（虚拟声卡 + 本地监听），并标注"实验性"；
* 模式 C 是普通单设备选择（不发送给语音软件）。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import device_enum
from core.audio_engine import (
    ROUTE_DUAL,
    ROUTE_LOCAL,
    ROUTE_VIRTUAL,
    AudioEngine,
    MixerSettings,
    OutputRoute,
    RouteStatus,
)

from . import icons, theme
from .text import strip_emoji
from .widgets import IconButton, ThinSlider, Toggle

ROLE_KEY = Qt.ItemDataRole.UserRole
NARROW_WIDTH = 1080

ROUTE_LABELS = {
    ROUTE_VIRTUAL: "虚拟声卡（推荐）",
    ROUTE_LOCAL: "仅本地扬声器（不发送到语音软件）",
}


class MixerPopover(QFrame):
    """顶栏"混音"浮层：麦克风设备 + 两个音量 + 耳返开关。

    为什么做成浮层而不是顶栏第二行：顶栏固定 48px、横向已满载
    （路由 210 + 状态区 + 两个图标 + 音量 96 + 互斥 + 设置），
    再塞三个控件会把最小宽度顶到 1200+。
    """

    changed = Signal(object)          # MixerSettings

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MixerPopover")
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setFixedWidth(330)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("内部混音", self)
        title.setObjectName("PanelTitle")
        layout.addWidget(title)

        hint = QLabel("麦克风 + 音效 → 同一条输出，OOPZ 里能同时听到", self)
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        enable_row = QHBoxLayout()
        enable_row.setSpacing(8)
        self.enable_toggle = Toggle(False, self)
        enable_row.addWidget(self.enable_toggle)
        enable_label = QLabel("启用混音", self)
        enable_label.setObjectName("Caption")
        enable_row.addWidget(enable_label)
        enable_row.addStretch(1)
        layout.addLayout(enable_row)

        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        mic_caption = QLabel("麦克风", self)
        mic_caption.setObjectName("FieldLabel")
        mic_caption.setFixedWidth(58)
        mic_row.addWidget(mic_caption)
        self.mic_combo = QComboBox(self)
        self.mic_combo.setFixedHeight(28)
        self.mic_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.mic_combo.setMinimumContentsLength(10)
        mic_row.addWidget(self.mic_combo, 1)
        layout.addLayout(mic_row)

        self.mic_slider, self.mic_value = self._volume_row(layout, "麦克风音量", 100)
        self.sfx_slider, self.sfx_value = self._volume_row(layout, "音效音量", 100)

        local_row = QHBoxLayout()
        local_row.setSpacing(8)
        self.local_toggle = Toggle(False, self)
        local_row.addWidget(self.local_toggle)
        local_label = QLabel("本地也播放音效（不含麦克风）", self)
        local_label.setObjectName("Caption")
        local_row.addWidget(local_label)
        local_row.addStretch(1)
        layout.addLayout(local_row)

        self.local_hint = QLabel(
            "开启后本地能听到音效、但听不到自己说话；不需要再开系统的「侦听此设备」",
            self,
        )
        self.local_hint.setObjectName("HintLabel")
        self.local_hint.setWordWrap(True)
        layout.addWidget(self.local_hint)

        note = QLabel("麦克风只会混进输出（虚拟声卡），不会在本地播放", self)
        note.setObjectName("HintLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        # 事件
        self.enable_toggle.toggled.connect(self._emit)
        self.local_toggle.toggled.connect(self._emit)
        self.mic_combo.currentIndexChanged.connect(lambda _i: self._emit())
        self.mic_slider.valueChanged.connect(self._on_slider)
        self.sfx_slider.valueChanged.connect(self._on_slider)

    # ------------------------------------------------------------------ 构造
    def _volume_row(self, layout: QVBoxLayout, caption: str, percent: int):
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(caption, self)
        label.setObjectName("FieldLabel")
        label.setFixedWidth(58)
        row.addWidget(label)
        slider = ThinSlider(parent=self)
        slider.setRange(0, 200)
        slider.setValue(percent)
        slider.setDimmed(False)
        row.addWidget(slider, 1)
        value = QLabel(f"{percent}%", self)
        value.setObjectName("VolumeValue")
        value.setFixedWidth(38)
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(value)
        layout.addLayout(row)
        return slider, value

    # ------------------------------------------------------------------ 状态
    def settings(self) -> MixerSettings:
        return MixerSettings(
            enabled=self.enable_toggle.isChecked(),
            mic_name=str(self.mic_combo.currentData() or ""),
            mic_gain=self.mic_slider.value() / 100.0,
            sfx_gain=self.sfx_slider.value() / 100.0,
            local_playback=self.local_toggle.isChecked(),
        )

    def bind(self, settings: MixerSettings, rules: dict, route_mode: str) -> None:
        """用配置初始化（不触发 changed）。"""
        for widget in (self.enable_toggle, self.local_toggle,
                       self.mic_slider, self.sfx_slider):
            widget.blockSignals(True)
        self._reload_mics(rules, settings.mic_name)
        self.enable_toggle.setChecked(bool(settings.enabled))
        self.local_toggle.setChecked(bool(settings.local_playback))
        self.mic_slider.setValue(int(round(max(0.0, min(2.0, settings.mic_gain)) * 100)))
        self.sfx_slider.setValue(int(round(max(0.0, min(2.0, settings.sfx_gain)) * 100)))
        self.mic_value.setText(f"{self.mic_slider.value()}%")
        self.sfx_value.setText(f"{self.sfx_slider.value()}%")
        for widget in (self.enable_toggle, self.local_toggle,
                       self.mic_slider, self.sfx_slider):
            widget.blockSignals(False)
        self.apply_route_mode(route_mode)

    def _reload_mics(self, rules: dict, wanted: str) -> None:
        devices = device_enum.list_mixer_inputs(rules)
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        for choice in devices:
            self.mic_combo.addItem(strip_emoji(choice.short_name), choice.name)
            self.mic_combo.setItemData(
                self.mic_combo.count() - 1, choice.tooltip(), Qt.ItemDataRole.ToolTipRole
            )
        index = self.mic_combo.findData(wanted) if wanted else -1
        if index < 0:
            fallback = device_enum.default_mixer_input(rules)
            index = self.mic_combo.findData(fallback.name) if fallback else 0
        self.mic_combo.setCurrentIndex(max(0, index))
        self.mic_combo.blockSignals(False)

    def apply_route_mode(self, route_mode: str) -> None:
        """路由变了只需要刷新提示（不再有耳返开关）。"""
        self._route_mode = route_mode

    # ------------------------------------------------------------------ 事件
    def _on_slider(self, _value: int) -> None:
        self.mic_value.setText(f"{self.mic_slider.value()}%")
        self.sfx_value.setText(f"{self.sfx_slider.value()}%")
        self._emit()

    def _emit(self, *_args) -> None:
        self.changed.emit(self.settings())


class TopBar(QWidget):
    """顶栏控件；只发信号，不碰持久化。"""

    changed = Signal()                  # 音量/互斥变化（主窗口负责存盘）
    route_changed = Signal(object)      # OutputRoute
    test_route_requested = Signal()
    open_sound_settings_requested = Signal()
    message = Signal(str)
    error = Signal(str)
    settings_requested = Signal()
    mixer_changed = Signal(object)      # MixerSettings
    device_center_requested = Signal()
    help_requested = Signal()

    def __init__(
        self,
        engine: AudioEngine,
        config,
        rules_provider: Callable[[], dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(theme.SIZE["topBar"])
        self.engine = engine
        self.config = config
        self._rules_provider = rules_provider
        self._devices: list[device_enum.DeviceChoice] = []
        self._status: RouteStatus | None = None
        self._narrow: bool | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.SPACE["lg"], 0, theme.SPACE["lg"], 0)
        layout.setSpacing(theme.SPACE["md"])

        # ---------------- 路由模式（1.2.0：主界面只读，改设备去「设备中心」） ----------------
        self.route_combo = QComboBox(self)
        self.route_combo.setObjectName("FlatCombo")
        self.route_combo.setFixedWidth(210)
        self.route_combo.setFixedHeight(28)
        for mode in (ROUTE_VIRTUAL, ROUTE_LOCAL):
            self.route_combo.addItem(ROUTE_LABELS[mode], mode)
        self.route_combo.currentIndexChanged.connect(self._on_route_mode_changed)
        self.route_combo.setVisible(False)      # 只读：真正修改在设备中心

        # ---------------- 设备 / 状态区 ----------------
        self.route_stack = QStackedWidget(self)
        self.route_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.route_stack.setFixedHeight(28)
        self.route_stack.addWidget(self._build_virtual_page())
        self.route_stack.addWidget(self._build_dual_page())
        self.route_stack.addWidget(self._build_local_page())
        self.route_stack.setVisible(False)      # 由只读摘要代替

        # 只读摘要 + 设备中心 + 帮助
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(theme.SPACE["xs"])

        self.route_summary = QLabel("输出：检测中…", self)
        self.route_summary.setObjectName("Caption")
        self.route_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        summary_row.addWidget(self.route_summary)

        self.device_button = IconButton(
            "sliders-horizontal", 16, 28, "ghost", "设备中心：路由 / 麦克风 / 录音源", self
        )
        self.device_button.clicked.connect(self.device_center_requested.emit)
        summary_row.addWidget(self.device_button)

        self.help_button = IconButton("help-circle", 16, 28, "ghost", "帮助与指引", self)
        self.help_button.clicked.connect(self.help_requested.emit)
        summary_row.addWidget(self.help_button)
        layout.addLayout(summary_row, 1)

        # ---------------- 两个动作按钮 ----------------
        self.test_button = IconButton(
            "audio-lines", 16, 28, "ghost", "播放 2 秒测试音（判断两端是否都能听到）", self
        )
        self.test_button.clicked.connect(self.test_route_requested.emit)
        layout.addWidget(self.test_button)

        self.open_settings_button = IconButton(
            "external-link", 16, 28, "ghost", "打开系统声音设置（control mmsys.cpl）", self
        )
        self.open_settings_button.clicked.connect(self.open_sound_settings_requested.emit)
        layout.addWidget(self.open_settings_button)

        layout.addStretch(0)

        # ---------------- 音量 ----------------
        self.volume_text = QLabel("音量", self)
        self.volume_text.setObjectName("FieldLabel")
        layout.addWidget(self.volume_text)

        self.volume_icon = QLabel(self)
        self.volume_icon.setPixmap(
            icons.pixmap("volume-2", theme.SIZE["iconBar"], theme.color("textMuted"))
        )
        self.volume_icon.setFixedSize(theme.SIZE["iconBar"], theme.SIZE["iconBar"])
        layout.addWidget(self.volume_icon)

        self.master_slider = ThinSlider(parent=self)
        self.master_slider.setRange(0, 100)
        self.master_slider.setFixedWidth(96)
        self.master_slider.setDimmed(False)
        self.master_slider.setToolTip("总音量")
        self.master_slider.valueChanged.connect(self._on_volume_changed)
        layout.addWidget(self.master_slider)

        self.master_value = QLabel("100%", self)
        self.master_value.setObjectName("MasterValue")
        self.master_value.setFixedWidth(36)
        self.master_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.master_value)

        # ---------------- 互斥 ----------------
        self.exclusive_label = QLabel("互斥模式", self)
        self.exclusive_label.setObjectName("FieldLabel")
        layout.addWidget(self.exclusive_label)

        self.exclusive_toggle = Toggle(bool(config.exclusive_mode), self)
        self.exclusive_toggle.setToolTip("开启后，新音效会打断正在播放的上一个")
        self.exclusive_toggle.toggled.connect(self._on_exclusive_toggled)
        layout.addWidget(self.exclusive_toggle)

        layout.addSpacing(theme.SPACE["xs"])

        # ---------------- 内部混音（浮层入口）----------------
        self.mixer_button = IconButton(
            "mic", theme.SIZE["iconBar"], theme.SIZE["btnIcon"], "ghost",
            "内部混音：麦克风 + 音效一起送出去", self
        )
        self.mixer_button.clicked.connect(self._toggle_mixer_popover)
        layout.addWidget(self.mixer_button)

        self.settings_button = IconButton(
            "settings", theme.SIZE["iconBar"], theme.SIZE["btnIcon"], "ghost", "设置", self
        )
        self.settings_button.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_button)

        self._sync_widgets()
        self.reload_devices()
        self._build_mixer_popover()

    # ================================================================== 混音浮层
    def _build_mixer_popover(self) -> None:
        self.mixer_popover = MixerPopover(self)
        self.mixer_popover.changed.connect(self._on_mixer_changed)
        self._mixer = MixerSettings()

    def _toggle_mixer_popover(self) -> None:
        if self.mixer_popover.isVisible():
            self.mixer_popover.hide()
            return
        self.mixer_popover.bind(
            self._mixer, self._rules_provider(),
            self.route_combo.currentData() or ROUTE_VIRTUAL,
        )
        point = self.mixer_button.mapToGlobal(QPoint(0, self.mixer_button.height() + 6))
        point.setX(point.x() - self.mixer_popover.width() + self.mixer_button.width())
        self.mixer_popover.move(point)
        self.mixer_popover.show()

    def bind_mixer(self, settings: MixerSettings) -> None:
        self._mixer = settings
        self._refresh_mixer_button()

    def mixer_settings(self) -> MixerSettings:
        return self._mixer

    def _refresh_mixer_button(self) -> None:
        enabled = bool(self._mixer.enabled and self._mixer.mic_name)
        self.mixer_button.setIconColor(
            theme.color("accent") if enabled else None
        )
        self.mixer_button.setToolTip(
            ("混音已开启：" + strip_emoji(self._mixer.mic_name)) if enabled
            else "内部混音：麦克风 + 音效一起送出去"
        )

    def _on_mixer_changed(self, settings: MixerSettings) -> None:
        self._mixer = settings
        self._refresh_mixer_button()
        self.mixer_changed.emit(settings)

    # ================================================================== 三个页面
    def _build_virtual_page(self) -> QWidget:
        page = QWidget(self)
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE["sm"])

        self.virtual_dot = QLabel(page)
        self.virtual_dot.setObjectName("StatusDot")
        self.virtual_dot.setFixedSize(6, 6)
        row.addWidget(self.virtual_dot)

        self.virtual_text = QLabel("输出 → CABLE Input", page)
        self.virtual_text.setObjectName("Caption")
        row.addWidget(self.virtual_text)

        self.virtual_badge = QLabel(page)
        self.virtual_badge.setFixedSize(14, 14)
        row.addWidget(self.virtual_badge)
        row.addStretch(1)
        return page

    def _build_dual_page(self) -> QWidget:
        page = QWidget(self)
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE["sm"])

        caption = QLabel("虚拟声卡", page)
        caption.setObjectName("FieldLabel")
        row.addWidget(caption)
        self.primary_combo = self._make_device_combo(page)
        row.addWidget(self.primary_combo)

        caption2 = QLabel("本地", page)
        caption2.setObjectName("FieldLabel")
        row.addWidget(caption2)
        self.monitor_combo = self._make_device_combo(page)
        row.addWidget(self.monitor_combo)

        self.dual_badge = QLabel(page)
        self.dual_badge.setFixedSize(14, 14)
        self.dual_badge.setPixmap(
            icons.pixmap("alert-triangle", 14, theme.color("warning"))
        )
        self.dual_badge.setToolTip("实验性：两路可能存在 35–50ms 延迟差异")
        row.addWidget(self.dual_badge)
        return page

    def _build_local_page(self) -> QWidget:
        page = QWidget(self)
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE["sm"])

        self.local_dot = QLabel(page)
        self.local_dot.setObjectName("StatusDot")
        self.local_dot.setFixedSize(6, 6)
        row.addWidget(self.local_dot)

        caption = QLabel("输出设备", page)
        caption.setObjectName("FieldLabel")
        row.addWidget(caption)

        self.local_combo = self._make_device_combo(page, width=190)
        row.addWidget(self.local_combo)
        row.addStretch(1)
        return page

    def _make_device_combo(self, parent: QWidget, width: int = 140) -> QComboBox:
        combo = QComboBox(parent)
        combo.setFixedWidth(width)
        combo.setFixedHeight(28)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(8)
        combo.currentIndexChanged.connect(self._on_device_changed)
        return combo

    # ================================================================== 设备列表
    def reload_devices(self) -> None:
        rules = self._rules_provider()
        self._devices = device_enum.list_devices("output", rules, show_all=False)
        if not self._devices:
            self._devices = device_enum.list_devices("output", rules, show_all=True)

        for combo in (self.primary_combo, self.monitor_combo, self.local_combo):
            combo.blockSignals(True)
            combo.clear()
            for choice in self._devices:
                combo.addItem(strip_emoji(choice.label()), choice.name)
                combo.setItemData(combo.count() - 1, choice.tooltip(), Qt.ItemDataRole.ToolTipRole)
            combo.blockSignals(False)

        cable_status = device_enum.detect_vb_cable(rules, show_all=True)
        self._cable = cable_status
        cable = cable_status.playback
        physical = device_enum.physical_outputs(rules)
        monitor_default = next((c for c in physical if c.is_default), physical[0] if physical else None)
        local_default = device_enum.default_output(rules)

        self.primary_combo.blockSignals(True)
        index = self.primary_combo.findData(cable.name) if cable else -1
        self.primary_combo.setCurrentIndex(max(0, index))
        self.primary_combo.blockSignals(False)

        self.monitor_combo.blockSignals(True)
        index = self.monitor_combo.findData(monitor_default.name) if monitor_default else -1
        self.monitor_combo.setCurrentIndex(max(0, index))
        self.monitor_combo.blockSignals(False)

        self.local_combo.blockSignals(True)
        index = self.local_combo.findData(local_default.name) if local_default else -1
        self.local_combo.setCurrentIndex(max(0, index))
        self.local_combo.blockSignals(False)

        if not self._devices:
            self.error.emit("没有可用的输出设备（虚拟声卡装了吗？）")

    # ================================================================== 路由
    def current_route(self) -> OutputRoute:
        mode = self.route_combo.currentData() or ROUTE_VIRTUAL
        if mode == ROUTE_LOCAL:
            return OutputRoute(mode=mode, primary_name=str(self.local_combo.currentData() or ""))
        # 虚拟声卡：**永远指向 CABLE Input**，不能拿上一次的状态（否则在「仅本地」里
        # 换过设备后，切回虚拟声卡会把扬声器当成虚拟声卡，导致"虚拟声卡处失效"）
        return OutputRoute(mode=mode, primary_name=self._cable_primary())

    def _cable_primary(self) -> str:
        """虚拟声卡模式的输出设备名（空 = 交给引擎按关键词自己找）。"""
        cable = getattr(self, "_cable", None)
        if cable is not None and cable.playback is not None:
            return cable.playback.name
        status = getattr(self, "_status", None)
        cable_name = getattr(status, "primary_name", "") if status is not None else ""
        if cable_name and device_enum.is_virtual_name(cable_name):
            return cable_name
        return ""

    def bind_route(self, route: OutputRoute) -> None:
        """用配置初始化路由下拉（不触发 route_changed）。"""
        self.set_route_status(None)
        index = self.route_combo.findData(route.mode)
        self.route_combo.blockSignals(True)
        self.route_combo.setCurrentIndex(max(0, index))
        self.route_combo.blockSignals(False)
        self._switch_page(route.mode)

        if route.primary_name:
            for combo in (self.primary_combo, self.local_combo):
                position = combo.findData(route.primary_name)
                if position >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(position)
                    combo.blockSignals(False)
        if route.monitor_name:
            position = self.monitor_combo.findData(route.monitor_name)
            if position >= 0:
                self.monitor_combo.blockSignals(True)
                self.monitor_combo.setCurrentIndex(position)
                self.monitor_combo.blockSignals(False)

    def _switch_page(self, mode: str) -> None:
        # 双输出已弃置：只剩「虚拟声卡」(0) 与「仅本地」(2) 两页
        self.route_stack.setCurrentIndex(0 if mode == ROUTE_VIRTUAL else 2)
        self.route_stack.currentWidget().setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def _on_route_mode_changed(self, _index: int) -> None:
        mode = self.route_combo.currentData() or ROUTE_VIRTUAL
        self._switch_page(mode)
        if mode == ROUTE_LOCAL:
            # 切到「仅本地」时别停在虚拟声卡上（那样本地根本听不到）
            current = str(self.local_combo.currentData() or "")
            if not current or device_enum.is_virtual_name(current):
                fallback = device_enum.default_output(self._rules_provider())
                if fallback is not None:
                    position = self.local_combo.findData(fallback.name)
                    if position >= 0:
                        self.local_combo.blockSignals(True)
                        self.local_combo.setCurrentIndex(position)
                        self.local_combo.blockSignals(False)
        self.message.emit(
            {
                ROUTE_VIRTUAL: "已选「虚拟声卡」：远端靠语音软件采集 CABLE Output",
                ROUTE_LOCAL: "已选「仅本地」：只有你能听到，不会发送给语音软件",
            }.get(mode, "")
        )
        self.mixer_popover.apply_route_mode(mode)
        self.route_changed.emit(self.current_route())

    def _on_device_changed(self, _index: int) -> None:
        mode = self.route_combo.currentData() or ROUTE_VIRTUAL
        if mode == ROUTE_VIRTUAL:
            return
        self.route_changed.emit(self.current_route())

    # ================================================================== 状态
    def set_route_status(self, status: RouteStatus | None) -> None:
        """把引擎的路由状态刷到徽章/文案（status=None 表示"未知"）。"""
        self._status = status
        if status is None:
            self.virtual_dot.setProperty("state", "idle")
            self.virtual_text.setText("输出 → CABLE Input")
            self.virtual_badge.clear()
            self.virtual_badge.setToolTip("")
            self.local_dot.setProperty("state", "idle")
            self._repolish(self.virtual_dot)
            self._repolish(self.local_dot)
            return

        cable_ok = status.cable_playback
        virtual_dot_state = "ok" if (cable_ok and status.primary_ok) else "warning"
        self.virtual_dot.setProperty("state", virtual_dot_state)
        self._repolish(self.virtual_dot)

        if cable_ok and status.effective_mode in (ROUTE_VIRTUAL, ROUTE_DUAL):
            self.virtual_text.setText(f"输出 → {status.primary_label or 'CABLE Input'}")
        elif not cable_ok:
            self.virtual_text.setText("未检测到 VB-CABLE")
        else:
            self.virtual_text.setText(f"输出 → {status.primary_label or '默认设备'}")

        mismatch = bool(getattr(self, "_cable", None) and self._cable.rate_mismatch)
        if cable_ok and status.primary_ok and not status.degraded and not mismatch:
            self.virtual_badge.setPixmap(icons.pixmap("check", 14, theme.color("success")))
            tooltip = "已检测到虚拟声卡；本地声音请用系统「侦听此设备」"
        else:
            self.virtual_badge.setPixmap(
                icons.pixmap("alert-triangle", 14, theme.color("warning"))
            )
            tooltip = status.degrade_reason or status.primary_error or "未检测到 VB-CABLE，请先安装"
            if cable_ok and not status.cable_capture:
                tooltip += "；未检测到 CABLE Output（语音软件可能选不到输入）"
            if mismatch:
                cable = self._cable
                tooltip = (
                    f"CABLE Input {cable.playback.samplerate}Hz ≠ "
                    f"CABLE Output {cable.capture.samplerate}Hz：VB-CABLE 跨采样率不重采样，"
                    "音效会变调/断续。请在「声音设置」把两端默认格式统一成 48000Hz"
                )
        self.virtual_badge.setToolTip(tooltip)
        self.virtual_text.setToolTip(tooltip)

        self.local_dot.setProperty("state", "ok" if status.primary_ok else "warning")
        self._repolish(self.local_dot)
        self.local_combo.setToolTip(
            f"当前：{status.primary_label} · {status.samplerate}Hz" if status.primary_ok
            else (status.primary_error or "未打开")
        )

        # 1.2.0：顶栏只显示一行状态摘要（改设备去设备中心）
        if status.effective_mode == ROUTE_LOCAL:
            summary = f"输出：本地扬声器 · {status.primary_label or '默认设备'}"
        elif not cable_ok:
            summary = "输出：未检测到 VB-CABLE（点右侧设备中心查看）"
        else:
            summary = f"输出：{status.primary_label or 'CABLE Input'}"
        if status.degraded:
            summary += "（已降级）"
        if mismatch:
            summary += " ⚠ 采样率不一致"
        self.route_summary.setText(summary)
        self.route_summary.setToolTip(tooltip)
        self.route_summary.setProperty("state", "warning" if (status.degraded or mismatch
                                                             or not cable_ok) else "ok")
        self._repolish(self.route_summary)

        if status.effective_mode == ROUTE_DUAL:
            self.dual_badge.setPixmap(icons.pixmap("check", 14, theme.color("success")))
            self.dual_badge.setToolTip(
                f"双输出已生效：{status.primary_label} + {status.monitor_label}"
                f"（额外延迟约 {status.latency_ms:.0f}ms）"
            )
        else:
            self.dual_badge.setPixmap(
                icons.pixmap("alert-triangle", 14, theme.color("warning"))
            )
            self.dual_badge.setToolTip(
                status.degrade_reason or "双输出未生效；两路可能有 35–50ms 延迟差"
            )

    # ================================================================== 其它
    def _on_volume_changed(self, value: int) -> None:
        self.master_value.setText(f"{value}%")
        self.config.master_volume = round(value / 100.0, 3)
        self.engine.set_master_volume(self.config.master_volume)
        self.changed.emit()

    def _on_exclusive_toggled(self, checked: bool) -> None:
        self.config.exclusive_mode = bool(checked)
        self.engine.set_exclusive(bool(checked))
        self.message.emit("互斥模式已开启" if checked else "互斥模式已关闭")
        self.changed.emit()

    def _sync_widgets(self) -> None:
        self.master_slider.blockSignals(True)
        self.master_slider.setValue(int(round(self.config.master_volume * 100)))
        self.master_value.setText(f"{self.master_slider.value()}%")
        self.master_slider.blockSignals(False)
        self.exclusive_toggle.setChecked(bool(self.config.exclusive_mode))

    def sync_from_config(self) -> None:
        """设置对话框改完后回填（不触发业务回调）。"""
        self._sync_widgets()
        self.reload_devices()
        self.bind_route(self.config.route())
        self.bind_mixer(self.config.mixer_settings())

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    # 窄窗口：隐掉「音量 / 互斥模式」文字，只留控件（tooltip 仍在）
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        narrow = self.width() < NARROW_WIDTH
        if narrow != self._narrow:
            self._narrow = narrow
            self.volume_text.setVisible(not narrow)
            self.exclusive_label.setVisible(not narrow)
            self.volume_icon.setVisible(True)
