"""设置对话框：录音 / 快捷键 / 设备名清洗（无系统标题栏，圆角 + 轻阴影）。"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import device_enum
from core.constants import FORMAT_MP3, FORMAT_WAV, MODE_APP, MODE_MIC, MODE_SYSTEM
from core.hotkey_manager import display_hotkey

from . import icons, theme
from .widgets import HotkeyBadge

MARGIN = 16
CHIPS_PER_ROW = 3


def _allow_shrink(label: QLabel) -> None:
    """自动换行的中文标签：别用"最长一行"当最小宽度，否则会把对话框撑宽。"""
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)


class KeywordTags(QWidget):
    """关键词标签输入：回车添加，点标签上的 × 删除。"""

    def __init__(self, keywords: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keywords: list[str] = list(dict.fromkeys(keywords or []))
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme.SPACE["xs"])

        self._chips_host = QWidget(self)
        self._chips = QGridLayout(self._chips_host)
        self._chips.setContentsMargins(0, 0, 0, 0)
        self._chips.setHorizontalSpacing(theme.SPACE["xs"])
        self._chips.setVerticalSpacing(theme.SPACE["xs"])
        self._layout.addWidget(self._chips_host)

        self.entry = QLineEdit(self)
        self.entry.setFixedHeight(28)
        self.entry.setPlaceholderText("输入关键词后按回车添加，例如 C-Media")
        self.entry.returnPressed.connect(self._add_from_entry)
        self._layout.addWidget(self.entry)

        self._rebuild()

    # ------------------------------------------------------------------ API
    def keywords(self) -> list[str]:
        return list(self._keywords)

    def setKeywords(self, keywords: list[str]) -> None:  # noqa: N802
        self._keywords = list(dict.fromkeys(keywords or []))
        self._rebuild()

    def add(self, keyword: str) -> None:
        keyword = keyword.strip()
        if not keyword or keyword in self._keywords:
            return
        self._keywords.append(keyword)
        self._rebuild()

    def remove(self, keyword: str) -> None:
        if keyword in self._keywords:
            self._keywords.remove(keyword)
            self._rebuild()

    # ------------------------------------------------------------------ 内部
    def _add_from_entry(self) -> None:
        self.add(self.entry.text())
        self.entry.clear()

    def _rebuild(self) -> None:
        while self._chips.count():
            item = self._chips.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not self._keywords:
            hint = QLabel("还没有自定义关键词（只用内置规则）", self._chips_host)
            hint.setObjectName("HintLabel")
            self._chips.addWidget(hint, 0, 0, 1, CHIPS_PER_ROW)
            return

        for index, keyword in enumerate(self._keywords):
            chip = QPushButton(keyword, self._chips_host)
            chip.setObjectName("SecondaryButton")
            chip.setFixedHeight(24)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setIcon(icons.icon("x", 10, theme.color("textMuted")))
            chip.setToolTip("点击移除")
            chip.clicked.connect(lambda _checked=False, k=keyword: self.remove(k))
            self._chips.addWidget(chip, index // CHIPS_PER_ROW, index % CHIPS_PER_ROW)


class SettingsDialog(QDialog):
    """全部设置；``values()`` 返回可直接写回配置的字典。"""

    route_probe_requested = Signal()
    open_sound_settings_requested = Signal()
    #: 1.2.0：路由/设备统一到「设备中心」，设置里只留入口
    device_center_requested = Signal()

    def __init__(
        self,
        config,
        capability: tuple[bool, str] | None = None,
        suspend_hotkeys: Callable[[bool], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle("设置")
        self.config = config
        self._suspend_hotkeys = suspend_hotkeys or (lambda _flag: None)
        self._record_hotkey = config.record_hotkey
        self._drag_origin: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)

        self.sheet = QFrame(self)
        self.sheet.setObjectName("SheetDialog")
        shadow = QGraphicsDropShadowEffect(self.sheet)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.sheet.setGraphicsEffect(shadow)
        outer.addWidget(self.sheet)

        layout = QVBoxLayout(self.sheet)
        layout.setContentsMargins(theme.SPACE["xl"], theme.SPACE["lg"],
                                  theme.SPACE["xl"], theme.SPACE["lg"])
        layout.setSpacing(theme.SPACE["lg"])

        title = QLabel("设置", self.sheet)
        title.setObjectName("DialogTitle")
        layout.addWidget(title)

        # 分组放进滚动区：屏幕小的时候不会把按钮挤出可视范围
        self.scroll = QScrollArea(self.sheet)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("background: transparent;")
        content = QWidget(self.scroll)
        content.setObjectName("Canvas")
        groups = QVBoxLayout(content)
        groups.setContentsMargins(0, 0, 0, 0)
        groups.setSpacing(theme.SPACE["xl"])
        groups.addWidget(self._build_route_group())
        groups.addWidget(self._build_record_group())
        groups.addWidget(self._build_hotkey_group())
        groups.addWidget(self._build_device_group(capability))
        groups.addStretch(1)
        self.scroll.setWidget(content)
        self.scroll.setMinimumHeight(320)
        layout.addWidget(self.scroll, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE["sm"])
        buttons.addStretch(1)
        self.cancel_button = QPushButton("取消", self.sheet)
        self.cancel_button.setObjectName("SecondaryButton")
        self.cancel_button.setFixedHeight(32)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.save_button = QPushButton("保存", self.sheet)
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.setFixedHeight(32)
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.setFixedWidth(520)
        self._load()
        self._fit_to_screen()

    # ================================================================== 分组
    def _fit_to_screen(self) -> None:
        """限制在屏幕可用区域内（不会顶出屏幕），并居中到父窗口。"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_height = max(360, available.height() - 80)
        self.adjustSize()
        height = min(max(self.sizeHint().height(), 360), max_height)
        self.resize(self.width(), height)

        parent = self.parentWidget()
        anchor = parent.window().frameGeometry().center() if parent else available.center()
        x = anchor.x() - self.width() // 2
        y = anchor.y() - self.height() // 2
        x = max(available.left() + 8, min(x, available.right() - self.width() - 8))
        y = max(available.top() + 8, min(y, available.bottom() - self.height() - 8))
        self.move(x, y)

    def _group(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        box = QWidget(self.sheet)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE["md"])
        caption = QLabel(title, box)
        caption.setObjectName("SectionTitle")
        layout.addWidget(caption)
        return box, layout

    def _row(self, label: str, widget: QWidget, parent: QWidget) -> QWidget:
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE["md"])
        caption = QLabel(label, row)
        caption.setObjectName("Caption")
        caption.setFixedWidth(88)
        layout.addWidget(caption)
        layout.addWidget(widget, 1)
        return row

    def _build_route_group(self) -> QWidget:
        """音频路由：模式（与顶栏同步）+ VB-CABLE 状态 + 本地监听引导。"""
        from core.audio_engine import ROUTE_LOCAL, ROUTE_VIRTUAL

        box, layout = self._group("音频路由")

        self.route_combo = QComboBox(box)
        self.route_combo.setFixedHeight(32)
        # 选项文字较长：只按"最小可见宽度"要空间，别用它把对话框撑宽
        self.route_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.route_combo.setMinimumContentsLength(10)
        self.route_combo.setMinimumWidth(0)
        self.route_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # 双输出模式已弃置（会和本地麦克风打架），这里只保留两种真正可用的
        for mode, text in (
            (ROUTE_VIRTUAL, "虚拟声卡（推荐）"),
            (ROUTE_LOCAL, "仅本地扬声器（不发送到语音软件）"),
        ):
            self.route_combo.addItem(text, mode)
        layout.addWidget(self._row("路由模式", self.route_combo, box))
        # 1.2.0：主界面和设置都不再直接改路由/设备，统一进「设备中心」
        self.route_combo.setVisible(False)
        center_row = QWidget(box)
        center_layout = QHBoxLayout(center_row)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(theme.SPACE["sm"])
        center_note = QLabel("播放路由、麦克风、录音源请在「设备中心」里修改", center_row)
        center_note.setObjectName("HintLabel")
        center_note.setWordWrap(True)
        _allow_shrink(center_note)
        center_layout.addWidget(center_note, 1)
        self.device_center_button = QPushButton("打开设备中心…", center_row)
        self.device_center_button.setObjectName("SecondaryButton")
        self.device_center_button.setFixedHeight(30)
        self.device_center_button.clicked.connect(self.device_center_requested.emit)
        center_layout.addWidget(self.device_center_button)
        layout.addWidget(center_row)

        self.cable_label = QLabel("", box)
        self.cable_label.setObjectName("Caption")
        self.cable_label.setWordWrap(True)
        _allow_shrink(self.cable_label)
        layout.addWidget(self.cable_label)

        self.cable_hint = QLabel("", box)
        self.cable_hint.setObjectName("HintLabel")
        self.cable_hint.setWordWrap(True)
        _allow_shrink(self.cable_hint)
        layout.addWidget(self.cable_hint)

        action_row = QWidget(box)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(theme.SPACE["sm"])
        self.open_sound_button = QPushButton("打开声音设置", action_row)
        self.open_sound_button.setObjectName("SecondaryButton")
        self.open_sound_button.setFixedHeight(32)
        self.open_sound_button.clicked.connect(self.open_sound_settings_requested.emit)
        action_layout.addWidget(self.open_sound_button)
        self.reprobe_button = QPushButton("重新检测设备", action_row)
        self.reprobe_button.setObjectName("SecondaryButton")
        self.reprobe_button.setFixedHeight(32)
        self.reprobe_button.clicked.connect(self.route_probe_requested.emit)
        action_layout.addWidget(self.reprobe_button)
        action_layout.addStretch(1)
        layout.addWidget(action_row)

        self.steps_toggle = QToolButton(box)
        self.steps_toggle.setObjectName("LinkButton")
        self.steps_toggle.setText("本地监听怎么开（4 步）")
        self.steps_toggle.setCheckable(True)
        self.steps_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.steps_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.steps_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.steps_toggle.clicked.connect(self._toggle_steps)
        layout.addWidget(self.steps_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.steps_label = QLabel(
            "① Win+R → 输入 control mmsys.cpl 回车（或点上面的「打开声音设置」）\n"
            "② 切到「录制」→ 找到 CABLE Output → 双击打开属性\n"
            "③ 在「侦听」选项卡勾选「侦听此设备」，"
            "「通过此设备播放」选你的耳机/扬声器 → 应用 → 确定\n"
            "④ 语音软件的输入设备选「CABLE Output (VB-Audio Virtual Cable)」，"
            "不要选物理麦克风",
            box,
        )
        self.steps_label.setObjectName("HintLabel")
        self.steps_label.setWordWrap(True)
        _allow_shrink(self.steps_label)
        self.steps_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.steps_label.setVisible(False)
        layout.addWidget(self.steps_label)

        note = QLabel(
            "只把音效送入虚拟声卡；麦克风混音（边说话边放音效）请交给语音软件或 VoiceMeeter，"
            "本程序不代劳。",
            box,
        )
        note.setObjectName("HintLabel")
        note.setWordWrap(True)
        _allow_shrink(note)
        layout.addWidget(note)
        return box

    def _toggle_steps(self, checked: bool) -> None:
        self.steps_label.setVisible(bool(checked))
        self.steps_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

    def refresh_cable_status(self, summary: str | None = None,
                             playback_ok: bool | None = None,
                             capture_ok: bool | None = None) -> None:
        """刷新 VB-CABLE 检测结果（由主窗口喂真实检测值）。"""
        from core import device_enum as de

        if playback_ok is None or capture_ok is None:
            status = de.detect_vb_cable(
                de.merge_rules(self.config.device_name_rules), show_all=True
            )
            playback_ok = status.playback is not None
            capture_ok = status.capture is not None
            summary = summary or status.summary()
        mark = lambda ok: "已检测到" if ok else "未检测到"  # noqa: E731
        self.cable_label.setText(
            f"播放端 CABLE Input：{mark(playback_ok)}　"
            f"录音端 CABLE Output：{mark(capture_ok)}"
        )
        self.cable_hint.setText(summary or "")

    def _build_record_group(self) -> QWidget:
        box, layout = self._group("录音")

        # 「默认模式」已移除：模式在右栏分段里选，设置里再放一个容易互相打架
        self.mic_combo = QComboBox(box)
        self.mic_combo.setFixedHeight(32)
        layout.addWidget(self._row("默认输入设备", self.mic_combo, box))

        self.loop_combo = QComboBox(box)
        self.loop_combo.setFixedHeight(32)
        layout.addWidget(self._row("默认输出设备", self.loop_combo, box))

        path_row = QWidget(box)
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(theme.SPACE["sm"])
        self.save_dir_edit = QLineEdit(path_row)
        self.save_dir_edit.setFixedHeight(32)
        self.save_dir_edit.setPlaceholderText("留空 = 程序目录下的 audio/")
        path_layout.addWidget(self.save_dir_edit, 1)
        self.browse_button = QPushButton("选择…", path_row)
        self.browse_button.setObjectName("SecondaryButton")
        self.browse_button.setFixedHeight(32)
        self.browse_button.clicked.connect(self._choose_dir)
        path_layout.addWidget(self.browse_button)
        layout.addWidget(self._row("保存路径", path_row, box))

        self.format_combo = QComboBox(box)
        self.format_combo.setFixedHeight(32)
        self.format_combo.addItem("WAV（推荐）", FORMAT_WAV)
        self.format_combo.addItem("MP3（需 ffmpeg）", FORMAT_MP3)
        layout.addWidget(self._row("另存为格式", self.format_combo, box))

        self.sidecar_check = QCheckBox("保存时写同名 .json 元数据", box)
        layout.addWidget(self.sidecar_check)

        self.normalize_check = QCheckBox("录音默认开启音量归一化", box)
        layout.addWidget(self.normalize_check)
        return box

    def _build_hotkey_group(self) -> QWidget:
        box, layout = self._group("快捷键")
        row = QWidget(box)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(theme.SPACE["md"])
        caption = QLabel("开始/停止录音", row)
        caption.setObjectName("Caption")
        caption.setFixedWidth(88)
        row_layout.addWidget(caption)
        self.hotkey_badge = HotkeyBadge(self._record_hotkey, row)
        self.hotkey_badge.binding_requested.connect(self._begin_capture)
        self.hotkey_badge.binding_changed.connect(self._on_hotkey_changed)
        self.hotkey_badge.binding_cancelled.connect(self._end_capture)
        row_layout.addWidget(self.hotkey_badge)
        row_layout.addStretch(1)
        layout.addWidget(row)

        hint = QLabel("点击徽章直接按键即可改；Esc 取消，Backspace 清除", box)
        hint.setObjectName("HintLabel")
        layout.addWidget(hint)
        return box

    def _build_device_group(self, capability: tuple[bool, str] | None) -> QWidget:
        box, layout = self._group("设备名清洗")

        hint = QLabel("这些关键词会被当作驱动名去掉，让设备列表更简洁", box)
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        rules = self.config.device_name_rules or {}
        self.tags = KeywordTags(list(rules.get("driver_keywords") or []), box)
        layout.addWidget(self.tags)

        self.show_all_check = QCheckBox("列表里显示全部设备（含禁用 / 旧接口重复项）", box)
        layout.addWidget(self.show_all_check)

        self.inferred_check = QCheckBox("应用列表显示「推断」候选（UWP / 权限受限时有用）", box)
        layout.addWidget(self.inferred_check)

        status = QLabel("", box)
        status.setObjectName("HintLabel")
        status.setWordWrap(True)
        if capability is not None:
            ok, reason = capability
            status.setText(
                "单个应用模式：可用" if ok else f"单个应用模式：不可用 —— {reason}"
            )
        layout.addWidget(status)
        return box

    # ================================================================== 数据
    def _fill_devices(self, combo: QComboBox, kind: str, current: str) -> None:
        rules = device_enum.merge_rules(self.config.device_name_rules)
        devices = device_enum.list_devices(kind, rules)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("跟随系统默认", "")
        for choice in devices:
            combo.addItem(choice.short_name, choice.key)
        index = combo.findData(current or "")
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _load(self) -> None:
        config = self.config
        self._fill_devices(self.mic_combo, "input", config.mic_device)
        self._fill_devices(self.loop_combo, "loopback", config.system_device)
        self.save_dir_edit.setText(config.save_dir or "")
        fmt_index = self.format_combo.findData(config.output_format)
        self.format_combo.setCurrentIndex(max(0, fmt_index))
        self.sidecar_check.setChecked(bool(config.write_sidecar))
        self.normalize_check.setChecked(bool(config.normalize))
        self.show_all_check.setChecked(bool(config.show_all_devices))
        self.inferred_check.setChecked(bool(config.app_show_inferred))
        self.hotkey_badge.setHotkey(self._record_hotkey)
        self.hotkey_badge.setState("bound" if self._record_hotkey else "unset")

        route_index = self.route_combo.findData(config.route_mode())
        self.route_combo.setCurrentIndex(max(0, route_index))
        self.refresh_cable_status()

    def _choose_dir(self) -> None:
        current = self.save_dir_edit.text().strip()
        chosen = QFileDialog.getExistingDirectory(self, "选择音效保存目录", current or "")
        if chosen:
            self.save_dir_edit.setText(chosen)

    # ------------------------------------------------------------------ 热键
    def _begin_capture(self) -> None:
        self._suspend_hotkeys(True)
        self.hotkey_badge.startCapture()

    def _end_capture(self) -> None:
        self._suspend_hotkeys(False)
        self.hotkey_badge.setState("bound" if self._record_hotkey else "unset")

    def _on_hotkey_changed(self, hotkey: str) -> None:
        self._record_hotkey = hotkey
        self._suspend_hotkeys(False)
        self.hotkey_badge.setState("bound" if hotkey else "unset")

    # ------------------------------------------------------------------ 结果
    def values(self) -> dict:
        rules = dict(self.config.device_name_rules or {})
        rules["driver_keywords"] = self.tags.keywords()
        # 路由：这里只改模式，设备仍在顶栏选（避免两处编辑同一个字段）
        route = dict(self.config.output_route or {})
        route["mode"] = self.route_combo.currentData() or "virtual"
        route.setdefault("primary_device_name", "")
        route.setdefault("monitor_device_name", "")
        return {
            # 模式由右栏分段决定，设置不再改它（保持当前值不变）
            "record_mode": self.config.record_mode,
            "mic_device": self.mic_combo.currentData() or "",
            "system_device": self.loop_combo.currentData() or "",
            "save_dir": self.save_dir_edit.text().strip(),
            "output_format": self.format_combo.currentData() or FORMAT_WAV,
            "write_sidecar": self.sidecar_check.isChecked(),
            "normalize": self.normalize_check.isChecked(),
            "show_all_devices": self.show_all_check.isChecked(),
            "app_show_inferred": self.inferred_check.isChecked(),
            "record_hotkey": self._record_hotkey,
            "device_name_rules": rules,
            "output_route": route,
        }

    def accept(self) -> None:  # noqa: D102
        self._suspend_hotkeys(False)
        super().accept()

    def reject(self) -> None:  # noqa: D102
        self._suspend_hotkeys(False)
        super().reject()

    # ------------------------------------------------------------------ 拖动
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < MARGIN + 40:
            handle = self.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove") and handle.startSystemMove():
                event.accept()
                return
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_origin = None
        super().mouseReleaseEvent(event)


def apply_values(config, values: dict) -> None:  # noqa: ANN001
    """把对话框结果写回配置（字段一一对应）。"""
    for field_name, value in values.items():
        if hasattr(config, field_name):
            setattr(config, field_name, value)
