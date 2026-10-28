"""统一「设备中心」：把散落的播放/麦克风/录音源选择收到一个对话框。

设计原则
--------
* 主界面只显示**状态摘要**（输出: CABLE Input | 录音: 系统声音），
  改设备一律进这里，避免"三个地方各有一个下拉、选错就哑"。
* 三个区块与数据一一对应：
    ① 播放与混音路由  → ``config.output_route``（虚拟声卡锁定 CABLE Input）
    ② 麦克风输入      → ``config.mixer``（sounddevice 域的设备名）
    ③ 录音捕获源      → ``config.record_mode / mic_device / system_device / app_pid``
* 对话框只负责收集值，真正应用交给 MainWindow（保持"UI 只发信号"的既有结构）。
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import app_enum, device_enum
from core.audio_engine import ROUTE_LOCAL, ROUTE_VIRTUAL, MixerSettings, OutputRoute
from core.constants import MODE_APP, MODE_MIC, MODE_SYSTEM

from . import theme

MODE_LABELS = {MODE_MIC: "麦克风", MODE_SYSTEM: "系统声音", MODE_APP: "单个应用"}


def open_sound_settings() -> None:
    """打开 Windows「声音」控制面板（播放/录制设备）。"""
    try:
        if sys.platform == "win32":
            subprocess.Popen("control mmsys.cpl", shell=True)
    except Exception:  # noqa: BLE001
        pass


class DeviceCenterDialog(QDialog):
    """模态的设备中心。"""

    def __init__(self, config, rules: dict, capability: tuple[bool, str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._rules = rules
        self._app_ok, self._app_reason = capability or (False, "尚未检测")
        self._choices: list = []          # 当前录音目标候选

        self.setWindowTitle("设备中心")
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(720, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        self.sheet = QFrame(self)
        self.sheet.setObjectName("SheetDialog")
        outer.addWidget(self.sheet)

        layout = QVBoxLayout(self.sheet)
        layout.setContentsMargins(theme.SPACE["xl"], theme.SPACE["lg"],
                                  theme.SPACE["xl"], theme.SPACE["lg"])
        layout.setSpacing(theme.SPACE["md"])

        header = QHBoxLayout()
        title = QLabel("设备中心", self.sheet)
        title.setObjectName("DialogTitle")
        header.addWidget(title)
        header.addStretch(1)
        hint = QLabel("所有音频设备都在这里改；主界面只显示状态", self.sheet)
        hint.setObjectName("HintLabel")
        header.addWidget(hint)
        layout.addLayout(header)

        scroll = QScrollArea(self.sheet)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(scroll)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(theme.SPACE["md"])
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        body_layout.addWidget(self._build_route_group())
        body_layout.addWidget(self._build_mic_group())
        body_layout.addWidget(self._build_record_group())
        body_layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消", self.sheet)
        cancel.setObjectName("SecondaryButton")
        cancel.setFixedHeight(32)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("保存", self.sheet)
        ok.setObjectName("PrimaryButton")
        ok.setFixedHeight(32)
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        self._load()

    # ------------------------------------------------------------------ 区块
    def _group(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        box = QFrame(self.sheet)
        box.setObjectName("Card")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)
        label = QLabel(title, box)
        label.setObjectName("SectionLabel")
        layout.addWidget(label)
        return box, layout

    def _row(self, caption: str, widget: QWidget) -> QWidget:
        row = QWidget(self.sheet)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE["sm"])
        label = QLabel(caption, row)
        label.setObjectName("FieldLabel")
        label.setFixedWidth(96)
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return row

    def _build_route_group(self) -> QFrame:
        box, layout = self._group("① 播放与混音路由")
        self.route_combo = QComboBox(box)
        self.route_combo.setFixedHeight(30)
        self.route_combo.addItem("虚拟声卡（CABLE Input，推荐）", ROUTE_VIRTUAL)
        self.route_combo.addItem("仅本地扬声器（不发给语音软件）", ROUTE_LOCAL)
        layout.addWidget(self._row("输出到", self.route_combo))

        self.route_status = QLabel("", box)
        self.route_status.setObjectName("HintLabel")
        self.route_status.setWordWrap(True)
        layout.addWidget(self.route_status)

        actions = QHBoxLayout()
        actions.setSpacing(theme.SPACE["sm"])
        detect = QPushButton("重新检测设备", box)
        detect.setObjectName("SecondaryButton")
        detect.setFixedHeight(30)
        detect.clicked.connect(self._refresh_route_status)
        actions.addWidget(detect)
        sound = QPushButton("打开 Windows 声音设置", box)
        sound.setObjectName("SecondaryButton")
        sound.setFixedHeight(30)
        sound.clicked.connect(open_sound_settings)
        actions.addWidget(sound)
        actions.addStretch(1)
        layout.addLayout(actions)
        return box

    def _build_mic_group(self) -> QFrame:
        box, layout = self._group("② 麦克风输入（和音效一起混进虚拟声卡）")
        self.mixer_check = QCheckBox("启用内部混音（说话与音效同时送出去）", box)
        layout.addWidget(self.mixer_check)
        self.mic_combo = QComboBox(box)
        self.mic_combo.setFixedHeight(30)
        layout.addWidget(self._row("麦克风", self.mic_combo))
        note = QLabel(
            "麦克风只混进虚拟声卡，不会在本地扬声器播放（避免听到自己的回声）。",
            box,
        )
        note.setObjectName("HintLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        return box

    def _build_record_group(self) -> QFrame:
        box, layout = self._group("③ 录音捕获源")
        self.mode_combo = QComboBox(box)
        self.mode_combo.setFixedHeight(30)
        for mode, text in (
            (MODE_MIC, "麦克风"),
            (MODE_SYSTEM, "系统声音（回环）"),
            (MODE_APP, "单个应用（仅它的声音）"),
        ):
            self.mode_combo.addItem(text, mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self._row("录什么", self.mode_combo))

        self.target_combo = QComboBox(box)
        self.target_combo.setFixedHeight(30)
        layout.addWidget(self._row("设备", self.target_combo))

        self.app_combo = QComboBox(box)
        self.app_combo.setFixedHeight(30)
        self.app_row = self._row("应用", self.app_combo)
        self.app_row.setVisible(False)
        layout.addWidget(self.app_row)

        self.mode_hint = QLabel("", box)
        self.mode_hint.setObjectName("HintLabel")
        self.mode_hint.setWordWrap(True)
        layout.addWidget(self.mode_hint)
        return box

    # ------------------------------------------------------------------ 数据
    def _load(self) -> None:
        route = self.config.route()
        index = self.route_combo.findData(route.mode)
        self.route_combo.setCurrentIndex(max(0, index))
        self._refresh_route_status()

        mixer = self.config.mixer_settings()
        self.mixer_check.setChecked(mixer.enabled)
        self._reload_mics(mixer.mic_name)

        mode = self.config.record_mode
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(mode)))
        self.mode_combo.blockSignals(False)
        self._reload_targets(mode)
        self._reload_apps(mixer)

    def _refresh_route_status(self) -> None:
        cable = device_enum.detect_vb_cable(self._rules, show_all=True)
        parts = [cable.summary()]
        if cable.rate_mismatch:
            parts.append("注意：播放端与录音端采样率不一致，建议都设为 48000Hz")
        if not cable.complete:
            parts.append("未装 VB-CABLE 时会自动回退到系统默认扬声器（语音软件收不到）")
        self.route_status.setText("；".join(parts))

    def _reload_mics(self, wanted: str) -> None:
        self.mic_combo.clear()
        for choice in device_enum.list_mixer_inputs(self._rules):
            self.mic_combo.addItem(choice.short_name, choice.name)
        index = self.mic_combo.findData(wanted) if wanted else -1
        if index < 0:
            fallback = device_enum.default_mixer_input(self._rules)
            index = self.mic_combo.findData(fallback.name) if fallback else 0
        self.mic_combo.setCurrentIndex(max(0, index))

    def _reload_targets(self, mode: str) -> None:
        self.target_combo.clear()
        self._choices = []
        if mode == MODE_APP:
            self.target_combo.addItem("见下方「应用」列表", "")
            self.target_combo.setEnabled(False)
            return
        self.target_combo.setEnabled(True)
        kind = "input" if mode == MODE_MIC else "loopback"
        self._choices = device_enum.list_devices(kind, self._rules)
        for choice in self._choices:
            self.target_combo.addItem(choice.short_name, choice.name)
        wanted = self.config.mic_device if mode == MODE_MIC else self.config.system_device
        index = self.target_combo.findData(wanted) if wanted else -1
        if index < 0 and self._choices:
            default = next((c for c in self._choices if c.is_default), self._choices[0])
            index = self.target_combo.findData(default.name)
        self.target_combo.setCurrentIndex(max(0, index))

    def _reload_apps(self, mixer: MixerSettings) -> None:  # noqa: ARG002
        self.app_combo.clear()
        if not self._app_ok:
            self.app_combo.addItem(f"不可用：{self._app_reason}", None)
            self.app_combo.setEnabled(False)
            return
        apps = app_enum.list_audio_apps()
        for app in apps:
            self.app_combo.addItem(app.display_name, app.pid)
        if not apps:
            self.app_combo.addItem("（当前没有正在出声的应用）", None)

    def _on_mode_changed(self, _index: int) -> None:
        mode = self.mode_combo.currentData() or MODE_MIC
        self._reload_targets(mode)
        self.app_row.setVisible(mode == MODE_APP)
        if mode == MODE_APP:
            self.mode_hint.setText(
                "只录所选应用的声音。若列表里没有目标应用，先让它播放一段声音再打开这里；"
                "本机不支持时仍可用「系统声音」。"
            )
        elif mode == MODE_SYSTEM:
            self.mode_hint.setText("录整台电脑正在播放的声音（WASAPI 回环），适合录游戏/浏览器。")
        else:
            self.mode_hint.setText("录所选麦克风/声卡输入。语音软件里选 CABLE Output 时也能录到混音结果。")

    # ------------------------------------------------------------------ 结果
    def values(self) -> dict:
        mode = self.mode_combo.currentData() or MODE_MIC
        mixer = MixerSettings(
            enabled=self.mixer_check.isChecked(),
            mic_name=str(self.mic_combo.currentData() or ""),
            mic_gain=self.config.mixer_settings().mic_gain,
            sfx_gain=self.config.mixer_settings().sfx_gain,
            local_playback=self.config.mixer_settings().local_playback,
        )
        result = {
            "route": OutputRoute(mode=str(self.route_combo.currentData() or ROUTE_VIRTUAL)),
            "mixer": mixer,
            "record_mode": mode,
            "mic_device": "",
            "system_device": "",
            "app_pid": None,
        }
        if mode == MODE_APP:
            result["app_pid"] = self.app_combo.currentData()
        else:
            name = str(self.target_combo.currentData() or "")
            if mode == MODE_MIC:
                result["mic_device"] = name
            else:
                result["system_device"] = name
        return result
