"""右栏：录音面板（模式 / 目标 / 电平 / 录音按钮 / 录制后操作卡片）。

面板自己持有录音引擎（录音是它的领域），保存交给注入的 ``ClipLibrary``，
保存成功后只发 ``clip_saved``，由主窗口负责插进左栏、写配置、注册热键。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import audio_io, clip_library, paths
from core.clip_library import Clip, ClipError, ClipLibrary
from core.constants import (
    FORMAT_MP3,
    MODE_APP,
    MODE_MIC,
    MODE_SYSTEM,
    SAMPLE_RATE,
)
from core.recorder import RecorderEngine

from . import icons, theme
from .widgets import IconButton, LevelMeter, RecordButton, Segmented, Spinner

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_READY = "ready"

TARGET_PLACEHOLDER = {
    MODE_MIC: "选择输入设备",
    MODE_SYSTEM: "选择输出设备",
    MODE_APP: "选择应用",
}


def _format_clock(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{int(seconds % 60):02d}"


class RecorderPanel(QWidget):
    """右栏录音面板（固定 340px）。"""

    clip_saved = Signal(object)
    status_changed = Signal(str)
    error = Signal(str)
    recording_changed = Signal(bool)
    mode_changed = Signal(str)
    target_changed = Signal()
    refresh_requested = Signal()
    #: 1.2.0：主界面只读，改录音源去「设备中心」
    device_center_requested = Signal()

    def __init__(
        self,
        engine: RecorderEngine,
        library: ClipLibrary,
        config,
        on_config_changed=None,  # noqa: ANN001
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RecorderPanel")
        self.setFixedWidth(theme.SIZE["sidePanel"])
        self.engine = engine
        self.library = library
        self.config = config
        self._on_config_changed = on_config_changed or (lambda: None)

        self.state = STATE_IDLE
        self._data: np.ndarray | None = None
        self._rate = SAMPLE_RATE
        self._choices: list = []
        self._playing = False
        self._trim: tuple[float, float] | None = None
        self._app_ok = True
        self._app_reason = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget(scroll)
        content.setObjectName("Canvas")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, theme.SPACE["lg"], 20, theme.SPACE["lg"])
        layout.setSpacing(0)
        scroll.setWidget(content)

        # ---- 区块 1：模式分段 ----
        self.segmented = Segmented(content)
        self.segmented.setItems([
            {"id": MODE_MIC, "text": "麦克风", "icon": "mic"},
            {"id": MODE_SYSTEM, "text": "系统声音", "icon": "speaker"},
            {"id": MODE_APP, "text": "单个应用", "icon": "target"},
        ])
        # 能力探测是异步的：先禁用「单个应用」，探测通过再放开，
        # 否则探测返回前点进去会走到不支持的分支
        self.segmented.setItemEnabled(MODE_APP, False, "正在检测是否可用…")
        self.segmented.changed.connect(self._on_mode_changed)
        layout.addWidget(self.segmented)

        # ---- 区块 2：目标选择 ----
        layout.addSpacing(theme.SPACE["lg"])
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(theme.SPACE["sm"])

        self.target_combo = QComboBox(content)
        self.target_combo.setFixedHeight(32)
        self.target_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.target_combo.setMinimumContentsLength(12)
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        target_row.addWidget(self.target_combo, 1)

        self.refresh_target_button = IconButton(
            "refresh-cw", 16, 32, "ghost", "刷新目标列表", content
        )
        self.refresh_target_button.clicked.connect(self.refresh_requested.emit)
        target_row.addWidget(self.refresh_target_button)
        layout.addLayout(target_row)

        self.target_hint = QLabel("", content)
        self.target_hint.setObjectName("HintLabel")
        self.target_hint.setWordWrap(True)
        layout.addWidget(self.target_hint)

        # ---- 1.2.0：主界面只读摘要，改设备统一去「设备中心」 ----
        for widget in (self.segmented, self.target_combo,
                       self.refresh_target_button, self.target_hint):
            widget.setVisible(False)

        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(theme.SPACE["sm"])
        self.source_summary = QLabel("录音源：—", content)
        self.source_summary.setObjectName("Caption")
        self.source_summary.setWordWrap(False)
        source_row.addWidget(self.source_summary, 1)
        self.source_button = QPushButton("修改", content)
        self.source_button.setObjectName("SecondaryButton")
        self.source_button.setFixedHeight(28)
        self.source_button.clicked.connect(self.device_center_requested.emit)
        source_row.addWidget(self.source_button)
        layout.insertWidget(0, self._wrap_row(source_row, content))

        # ---- 区块 3：电平条 ----
        layout.addSpacing(theme.SPACE["xl"])
        level_row = QHBoxLayout()
        level_row.setContentsMargins(0, 0, 0, 0)
        level_row.setSpacing(theme.SPACE["sm"])
        self.level_icon = QLabel(content)
        self.level_icon.setPixmap(icons.pixmap("volume-2", 12, theme.color("textFaint")))
        self.level_icon.setFixedSize(12, 12)
        level_row.addWidget(self.level_icon)
        self.level_meter = LevelMeter(content)
        level_row.addWidget(self.level_meter, 1)
        layout.addLayout(level_row)

        # ---- 区块 4：录音按钮 + 计时 ----
        layout.addSpacing(theme.SPACE["xl"])
        button_column = QVBoxLayout()
        button_column.setContentsMargins(0, 0, 0, 0)
        button_column.setSpacing(theme.SPACE["sm"])
        self.record_button = RecordButton(content)
        self.record_button.clicked.connect(self.toggle_recording)
        button_column.addWidget(self.record_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.timer_label = QLabel("00:00", content)
        self.timer_label.setObjectName("TimerLabel")
        self.timer_label.setProperty("state", "idle")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button_column.addWidget(self.timer_label)
        layout.addLayout(button_column)

        # ---- 区块 5：录制后操作卡片 ----
        layout.addSpacing(theme.SPACE["xl"])
        self.result_card = QFrame(content)
        self.result_card.setObjectName("ResultCard")
        card = QVBoxLayout(self.result_card)
        card.setContentsMargins(theme.SIZE["cardPadding"], theme.SIZE["cardPadding"],
                                theme.SIZE["cardPadding"], theme.SIZE["cardPadding"])
        card.setSpacing(theme.SPACE["md"])

        name_row = QVBoxLayout()
        name_row.setSpacing(theme.SPACE["xs"])
        self.name_caption = QLabel("名称", self.result_card)
        self.name_caption.setObjectName("SectionTitle")
        name_row.addWidget(self.name_caption)
        self.name_edit = QLineEdit(self.result_card)
        self.name_edit.setFixedHeight(32)
        self.name_edit.setPlaceholderText("给这段录音起个名字")
        name_row.addWidget(self.name_edit)
        card.addLayout(name_row)

        actions = QHBoxLayout()
        actions.setSpacing(theme.SPACE["sm"])
        self.preview_button = QPushButton("试听", self.result_card)
        self.preview_button.setObjectName("SecondaryButton")
        self.preview_button.setFixedHeight(32)
        self.preview_button.setIcon(icons.icon("play", 14, theme.color("text")))
        self.preview_button.clicked.connect(self.toggle_preview)
        actions.addWidget(self.preview_button, 1)

        self.trim_button = QPushButton("裁剪", self.result_card)
        self.trim_button.setObjectName("SecondaryButton")
        self.trim_button.setFixedHeight(32)
        self.trim_button.setToolTip("设置起止时间（掐头去尾）")
        self.trim_button.clicked.connect(self.open_trim)
        actions.addWidget(self.trim_button, 1)
        card.addLayout(actions)

        self.trim_hint = QLabel("", self.result_card)
        self.trim_hint.setObjectName("HintLabel")
        card.addWidget(self.trim_hint)

        save_row = QHBoxLayout()
        save_row.setSpacing(theme.SPACE["sm"])
        self.save_button = QPushButton("保存到音效板", self.result_card)
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.setFixedHeight(36)
        self.save_button.clicked.connect(self.save_to_library)
        save_row.addWidget(self.save_button, 1)

        self.save_as_button = QPushButton("另存为…", self.result_card)
        self.save_as_button.setObjectName("SecondaryButton")
        self.save_as_button.setFixedHeight(36)
        self.save_as_button.clicked.connect(self.save_as)
        save_row.addWidget(self.save_as_button, 1)
        card.addLayout(save_row)

        hint_row = QHBoxLayout()
        hint_row.setSpacing(theme.SPACE["xs"])
        self.spinner = Spinner(16, self.result_card)
        self.spinner.hide()
        hint_row.addWidget(self.spinner)
        self.result_hint = QLabel("", self.result_card)
        self.result_hint.setObjectName("HintLabel")
        self.result_hint.setWordWrap(True)
        self.result_hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        hint_row.addWidget(self.result_hint, 1)
        card.addLayout(hint_row)

        self.result_card.setVisible(False)
        layout.addWidget(self.result_card)
        layout.addStretch(1)

        # 50ms：计时/电平/引擎事件/目标退出自动收尾
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ------------------------------------------------------------------ 配置
    @staticmethod
    def _wrap_row(layout: QHBoxLayout, parent: QWidget) -> QWidget:
        """把 QHBoxLayout 包成一个 QWidget，方便 insertWidget 控制位置。"""
        holder = QWidget(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(layout)
        return holder

    def update_source_summary(self) -> None:
        """主界面只读摘要：录音源 + 当前目标。"""
        mode = self.current_mode()
        label = {MODE_MIC: "麦克风", MODE_SYSTEM: "系统声音", MODE_APP: "单个应用"}.get(mode, mode)
        target = self.current_target_label() or "未选择"
        self.source_summary.setText(f"录音源：{label} · {target}")
        self.source_summary.setToolTip(
            f"当前录制：{label} / {target}。点右侧「修改」打开设备中心。"
        )

    def bind_config(self, config, rules: dict) -> None:  # noqa: ANN001
        self.config = config
        self._rules = rules
        mode = config.record_mode if config.record_mode in (MODE_MIC, MODE_SYSTEM, MODE_APP) else MODE_MIC
        if mode == MODE_APP and not self._app_ok:
            # 配置里存的是「单个应用」，但本机不支持/还没探测完 → 先显示麦克风
            mode = MODE_MIC
        self.segmented.setCurrent(mode)
        if hasattr(self, "_rules"):
            self.reload_targets()
        self.update_source_summary()

    def set_capability(self, ok: bool, reason: str) -> None:
        self._app_ok = bool(ok)
        self._app_reason = reason or ""
        self.segmented.setItemEnabled(
            MODE_APP, self._app_ok,
            "只录所选应用的声音" if self._app_ok else f"不可用：{self._app_reason}",
        )
        if not self._app_ok and self.current_mode() == MODE_APP:
            self.segmented.setCurrent(MODE_MIC, notify=True)

    # ------------------------------------------------------------------ 目标
    def current_mode(self) -> str:
        return self.segmented.current() or MODE_MIC

    def current_choice(self):
        index = self.target_combo.currentIndex()
        if index < 0 or index >= len(self._choices):
            return None
        return self._choices[index]

    def current_target_label(self) -> str:
        choice = self.current_choice()
        if choice is None:
            return ""
        return getattr(choice, "short_name", "") or getattr(choice, "display_name", "")

    def reload_targets(self) -> None:
        from core import app_enum, device_enum

        mode = self.current_mode()
        rules = getattr(self, "_rules", None) or device_enum.merge_rules(None)
        if mode == MODE_APP:
            self._choices = app_enum.list_audio_apps(
                include_inferred=bool(getattr(self.config, "app_show_inferred", True))
            )
        else:
            kind = "loopback" if mode == MODE_SYSTEM else "input"
            self._choices = device_enum.list_devices(
                kind, rules, show_all=bool(getattr(self.config, "show_all_devices", False))
            )

        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        if not self._choices:
            self.target_combo.addItem(TARGET_PLACEHOLDER[mode] + "（无可用）")
            self.target_combo.setCurrentIndex(0)
        else:
            for choice in self._choices:
                if mode == MODE_APP:
                    # AppChoice 没有 .name/.short_name，只有 display_name
                    text = f"{getattr(choice, 'display_name', '应用')}  ·  PID {choice.pid}"
                else:
                    text = choice.short_name
                self.target_combo.addItem(text)
            wanted = self._wanted_key(mode)
            index = self._index_of(wanted)
            if index < 0:
                default = next((c for c in self._choices if getattr(c, "is_default", False)), None)
                index = self._index_of(self._key_of(default)) if default else 0
            self.target_combo.setCurrentIndex(max(0, index))
        self.target_combo.blockSignals(False)

        if mode == MODE_SYSTEM:
            self.target_hint.setText("会录入该设备上所有应用的声音")
        elif mode == MODE_APP:
            self.target_hint.setText("只录所选应用；目标退出会自动停止录音")
        else:
            self.target_hint.setText("选择要录制的输入设备")
        self.target_combo.setEnabled(bool(self._choices))

    def _wanted_key(self, mode: str) -> str:
        if mode == MODE_MIC:
            return str(getattr(self.config, "mic_device", "") or "")
        if mode == MODE_SYSTEM:
            return str(getattr(self.config, "system_device", "") or "")
        pid = getattr(self.config, "app_pid", None)
        return str(pid) if pid else ""

    @staticmethod
    def _key_of(choice) -> str:  # noqa: ANN001
        if choice is None:
            return ""
        return str(getattr(choice, "key", getattr(choice, "pid", "")))

    def _index_of(self, key: str) -> int:
        if not key:
            return -1
        for index, choice in enumerate(self._choices):
            if self._key_of(choice) == key:
                return index
        return -1

    def key(self) -> str:
        return self._key_of(self.current_choice())

    def refresh_targets(self) -> None:
        self.reload_targets()

    # ------------------------------------------------------------------ 事件
    def _on_mode_changed(self, mode: str) -> None:
        if self.engine.is_recording:
            self.segmented.setCurrent(self.config.record_mode or MODE_MIC)
            self.error.emit("录音中不能切换模式")
            return
        self.config.record_mode = mode
        self._on_config_changed()
        self.reload_targets()
        self.update_source_summary()
        self.mode_changed.emit(mode)

    def _on_target_changed(self, _index: int) -> None:
        self.update_source_summary()
        if not self._choices:
            return
        self.target_changed.emit()

    # ------------------------------------------------------------------ 录音
    def toggle_recording(self) -> None:
        if self.engine.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        target = self.current_choice()
        if target is None:
            self.error.emit(TARGET_PLACEHOLDER[self.current_mode()])
            return
        if not self.engine.start(self.current_mode(), target):
            self.error.emit(self.engine.last_error or "无法开始录音")
            self._enter_idle()
            return
        self._enter_recording()

    def stop_recording(self) -> None:
        auto_reason = self.engine.auto_stop_reason
        self.engine.auto_stop_reason = None
        result = self.engine.stop()
        if result is None:
            self.error.emit("没有录到任何数据（设备可能没在采集）")
            self._enter_idle()
            self.recording_changed.emit(False)
            return

        data, rate = result
        duration = data.shape[0] / float(rate)
        if duration < 0.15:
            self.error.emit(f"录音太短（{duration:.2f}s），已丢弃")
            self._enter_idle()
            self.recording_changed.emit(False)
            return

        self._data = data
        self._rate = rate
        self._trim = None
        self.name_edit.setText(clip_library.friendly_name())
        self.trim_hint.setText(f"全长 {duration:.2f}s")
        self.result_hint.setText("")
        self._save_temp_copy()
        self._enter_ready()

        if auto_reason == "exited":
            self.error.emit(f"目标应用已退出，已录 {duration:.2f}s 仍可保存")
        else:
            self.status_changed.emit(f"已录制 {duration:.2f}s")

    def _save_temp_copy(self) -> None:
        if self._data is None:
            return
        try:
            audio_io.write_wav(paths.temp_dir() / "rec_last.wav", self._data, self._rate)
        except audio_io.AudioError as exc:
            self.error.emit(str(exc))

    # ------------------------------------------------------------------ 状态机
    def _enter_idle(self) -> None:
        self.state = STATE_IDLE
        self.record_button.setRecording(False)
        self.timer_label.setText("00:00")
        self.timer_label.setProperty("state", "idle")
        _repolish(self.timer_label)
        self.level_meter.setActive(False)
        self.level_meter.reset()
        self.segmented.setAllEnabled(True)
        self.target_combo.setEnabled(bool(self._choices))
        self.refresh_target_button.setEnabled(True)

    def _enter_recording(self) -> None:
        self.state = STATE_RECORDING
        self.record_button.setRecording(True)
        self.timer_label.setProperty("state", "recording")
        _repolish(self.timer_label)
        self.level_meter.setActive(True)
        self.segmented.setAllEnabled(False)
        self.target_combo.setEnabled(False)
        self.refresh_target_button.setEnabled(False)
        self.result_card.setVisible(False)
        self._stop_preview()
        self.recording_changed.emit(True)
        self.status_changed.emit("录音中")

    def _enter_ready(self) -> None:
        self.state = STATE_READY
        self.record_button.setRecording(False)
        self.timer_label.setProperty("state", "recording")
        _repolish(self.timer_label)
        self.level_meter.setActive(False)
        self.segmented.setAllEnabled(True)
        self.target_combo.setEnabled(bool(self._choices))
        self.refresh_target_button.setEnabled(True)
        self._show_card()
        self.recording_changed.emit(False)

    def _show_card(self) -> None:
        """200ms 淡入 + 上移 8px。"""
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self.result_card.setVisible(True)
        effect = QGraphicsOpacityEffect(self.result_card)
        self.result_card.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self.result_card)
        fade.setDuration(theme.ANIM["panel"])
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.start()
        self._fade_animation = fade

    # ------------------------------------------------------------------ 试听
    def toggle_preview(self) -> None:
        if self._playing:
            self._stop_preview()
            return
        try:
            prepared = self._prepared_data()
        except audio_io.AudioError as exc:
            self.error.emit(str(exc))
            return
        if sd is None:
            self.error.emit("未安装 sounddevice，无法试听")
            return
        try:
            sd.stop()
            sd.play(prepared, self._rate)
        except Exception as exc:
            self.error.emit(f"试听失败：{exc}")
            return
        self._playing = True
        self.preview_button.setText("停止")
        self.preview_button.setIcon(icons.icon("square", 14, theme.color("text")))

    def _stop_preview(self) -> None:
        if sd is not None:
            try:
                sd.stop()
            except Exception:
                pass
        self._playing = False
        self.preview_button.setText("试听")
        self.preview_button.setIcon(icons.icon("play", 14, theme.color("text")))

    def _preview_finished(self) -> bool:
        if not self._playing:
            return True
        try:
            stream = sd.get_stream()
            return stream is None or not stream.active
        except Exception:
            return True

    # ------------------------------------------------------------------ 裁剪
    def open_trim(self) -> None:
        """打开波形裁剪窗口：确认后**直接替换内存里的录音数据**。"""
        if self._data is None:
            return
        from .trimmer_dialog import TrimmerDialog

        from .window_utils import exec_centered

        dialog = TrimmerDialog(
            self._data, self._rate,
            title=f"裁剪「{self.name_edit.text().strip() or clip_library.friendly_name()}」",
            parent=self,
        )
        if exec_centered(dialog, self.window()) != QDialog.DialogCode.Accepted:
            return

        trimmed = dialog.trimmed()
        if trimmed.size < int(self._rate * 0.05):
            self.error.emit("裁剪后太短（<0.05s），已忽略")
            return
        before = self._duration()
        self._data = trimmed.reshape(-1, 1) if self._data.ndim > 1 else trimmed
        self._trim = None                      # 已物理裁剪，不再叠加时间窗
        self.trim_button.setText("裁剪")
        self.trim_hint.setText(
            f"已裁剪：{before:.2f}s → {self._duration():.2f}s"
        )
        self._update_preview_state()
        self.status_changed.emit(f"已裁剪到 {self._duration():.2f}s，记得「保存到音效板」")

    def _duration(self) -> float:
        if self._data is None:
            return 0.0
        return self._data.shape[0] / float(self._rate)

    def _prepared_data(self) -> np.ndarray:
        if self._data is None:
            raise audio_io.AudioError("还没有录音")
        array = self._data
        if self._trim is not None:
            array = audio_io.trim(array, self._trim[0], self._trim[1], self._rate)
        if getattr(self.config, "normalize", False):
            array, _gain = audio_io.normalize_peak(array)
        return array

    # ------------------------------------------------------------------ 保存
    def _clip_name(self) -> str:
        return self.name_edit.text().strip() or clip_library.friendly_name()

    def save_to_library(self) -> None:
        try:
            prepared = self._prepared_data()
        except audio_io.AudioError as exc:
            self.error.emit(str(exc))
            return
        self._set_saving(True)
        try:
            clip = self.library.save_recorded(
                prepared,
                name=self._clip_name(),
                samplerate=self._rate,
                write_sidecar=bool(getattr(self.config, "write_sidecar", True)),
                volume=1.0,
            )
        except ClipError as exc:
            self.error.emit(str(exc))
            self._set_saving(False)
            return
        self._set_saving(False)
        self.result_hint.setText(f"已加入音效板：{clip.path().name}")
        self.clip_saved.emit(clip)

    def save_as(self) -> None:
        try:
            prepared = self._prepared_data()
        except audio_io.AudioError as exc:
            self.error.emit(str(exc))
            return

        fmt = getattr(self.config, "output_format", "wav")
        suffix = ".mp3" if fmt == FORMAT_MP3 else ".wav"
        default_path = Path.home() / f"{self._clip_name()}{suffix}"
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "另存录音", str(default_path),
            "WAV 音频 (*.wav);;MP3 音频 (*.mp3);;所有文件 (*)",
        )
        if not chosen:
            return
        target = Path(chosen)
        if target.suffix.lower() == ".mp3":
            fmt = FORMAT_MP3
        elif target.suffix.lower() == ".wav":
            fmt = "wav"

        self._set_saving(True)
        try:
            saved = audio_io.save_audio(target, prepared, fmt, self._rate)
        except audio_io.AudioError as exc:
            self._set_saving(False)
            self.error.emit(str(exc))
            return
        self._set_saving(False)

        if getattr(self.config, "write_sidecar", True):
            clip_library.write_sidecar_file(saved, self._clip_name(), 1.0)
        self.result_hint.setText(f"已另存为：{saved.name}")
        self.status_changed.emit(f"已另存为：{saved.name}")

    def _set_saving(self, saving: bool) -> None:
        self.save_button.setEnabled(not saving)
        self.save_as_button.setEnabled(not saving)
        self.preview_button.setEnabled(not saving)
        self.trim_button.setEnabled(not saving)
        self.spinner.setVisible(saving)
        if saving:
            self.spinner.start()
            self.result_hint.setText("正在保存…")
        else:
            self.spinner.stop()

    def _update_preview_state(self) -> None:
        """裁剪/试听/保存按钮是否有可操作的数据。"""
        has_data = self._data is not None and self._data.size > 0
        for widget in (self.preview_button, self.trim_button,
                       self.save_button, self.save_as_button):
            widget.setEnabled(has_data)
        if has_data:
            self.timer_label.setText(f"{self._duration():.1f}s")

    # ------------------------------------------------------------------ 定时器
    def _tick(self) -> None:
        for message in self.engine.drain_events():
            self.error.emit(message)

        if self.engine.is_recording:
            self.timer_label.setText(_format_clock(self.engine.duration))
            self.level_meter.setLevel(self.engine.peak, self.engine.rms)
            return

        if self.state == STATE_RECORDING:
            self.stop_recording()
            return

        if self._playing and self._preview_finished():
            self._stop_preview()

    def shutdown(self) -> None:
        """退出清理：停定时器/试听，并无条件释放录音采集流（防止设备被占）。"""
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            self._stop_preview()
        except Exception:
            pass
        try:
            self.engine.cancel()          # 无条件调用（内部幂等），确保采集线程退出
        except Exception:
            pass


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
