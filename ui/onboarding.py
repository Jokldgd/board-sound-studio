"""首次启动向导 + 常驻帮助。

* :class:`FirstRunWizard` —— 3 步：检查虚拟声卡 → 语音软件里选 CABLE Output →
  本地如何听到声音（程序内开关 / Windows「侦听」）。
* :class:`HelpDialog` —— 常驻排错指引：没声音、有回音、对方听不到，各给步骤；
  底部三个动作：打开 Windows 声音设置 / 播放测试音 / 打开设备中心。
"""

from __future__ import annotations

import sys
import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import device_enum

from . import theme
from .device_center import open_sound_settings

CABLE_URL = "https://vb-audio.com/Cable/"


class _SheetDialog(QDialog):
    """和设置/设备中心一致的"卡片式"无边框对话框。"""

    def __init__(self, title: str, size: tuple[int, int],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(*size)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        self.sheet = QFrame(self)
        self.sheet.setObjectName("SheetDialog")
        outer.addWidget(self.sheet)
        self.layout = QVBoxLayout(self.sheet)
        self.layout.setContentsMargins(theme.SPACE["xl"], theme.SPACE["lg"],
                                       theme.SPACE["xl"], theme.SPACE["lg"])
        self.layout.setSpacing(theme.SPACE["md"])

    def _title(self, text: str, subtitle: str = "") -> None:
        row = QHBoxLayout()
        label = QLabel(text, self.sheet)
        label.setObjectName("DialogTitle")
        row.addWidget(label)
        row.addStretch(1)
        if subtitle:
            hint = QLabel(subtitle, self.sheet)
            hint.setObjectName("HintLabel")
            row.addWidget(hint)
        self.layout.addLayout(row)

    def _text(self, text: str, object_name: str = "BodyText") -> QLabel:
        label = QLabel(text, self.sheet)
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label


class FirstRunWizard(_SheetDialog):
    """首次启动三步向导。"""

    finished = Signal()

    def __init__(self, rules: dict, parent: QWidget | None = None) -> None:
        super().__init__("欢迎使用 Soundboard Studio", (760, 520), parent)
        self._rules = rules
        self._index = 0

        self._title("欢迎使用 Soundboard Studio", "3 步完成设置")
        self.stack = QStackedWidget(self.sheet)
        self.stack.addWidget(self._page_cable())
        self.stack.addWidget(self._page_voice_app())
        self.stack.addWidget(self._page_listen())
        self.layout.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        self.step_label = QLabel("", self.sheet)
        self.step_label.setObjectName("HintLabel")
        nav.addWidget(self.step_label)
        nav.addStretch(1)
        self.back_button = QPushButton("上一步", self.sheet)
        self.back_button.setObjectName("SecondaryButton")
        self.back_button.setFixedHeight(32)
        self.back_button.clicked.connect(self._go_back)
        nav.addWidget(self.back_button)
        self.next_button = QPushButton("下一步", self.sheet)
        self.next_button.setObjectName("PrimaryButton")
        self.next_button.setFixedHeight(32)
        self.next_button.clicked.connect(self._go_next)
        nav.addWidget(self.next_button)
        skip = QPushButton("跳过", self.sheet)
        skip.setObjectName("SecondaryButton")
        skip.setFixedHeight(32)
        skip.clicked.connect(self._finish)
        nav.addWidget(skip)
        self.layout.addLayout(nav)

        self._refresh()

    # ------------------------------------------------------------------ 页面
    def _page_cable(self) -> QWidget:
        page = QWidget(self.sheet)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._text(
            "语音软件需要一个「虚拟麦克风」才能听到音效。本程序用免费驱动 VB-CABLE：\n"
            "安装后系统里会多出一个播放设备 CABLE Input 和一个录音设备 CABLE Output。",
            "BodyText",
        ))
        self.cable_status = QLabel("", page)
        self.cable_status.setObjectName("BodyText")
        self.cable_status.setWordWrap(True)
        layout.addWidget(self.cable_status)

        row = QHBoxLayout()
        detect = QPushButton("重新检测", page)
        detect.setObjectName("SecondaryButton")
        detect.setFixedHeight(30)
        detect.clicked.connect(self._refresh_cable)
        row.addWidget(detect)
        download = QPushButton("打开 VB-CABLE 下载页", page)
        download.setObjectName("SecondaryButton")
        download.setFixedHeight(30)
        download.clicked.connect(lambda: webbrowser.open(CABLE_URL))
        row.addWidget(download)
        sound = QPushButton("打开 Windows 声音设置", page)
        sound.setObjectName("SecondaryButton")
        sound.setFixedHeight(30)
        sound.clicked.connect(open_sound_settings)
        row.addWidget(sound)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _page_voice_app(self) -> QWidget:
        page = QWidget(self.sheet)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._text(
            "第 2 步：把语音软件的「麦克风」改成 CABLE Output。", "SectionLabel"))
        layout.addWidget(self._text(
            "OOPZ：设置 → 语音 → 输入设备 → 选择「CABLE Output (VB-Audio Virtual Cable)」\n\n"
            "Discord：用户设置 → 语音和视频 → 输入设备 → 同上\n\n"
            "游戏内语音：音频设置 → 麦克风/输入 → 同上\n\n"
            "改完之后，在这台电脑上说话 + 点音效，另一端应该能同时听到两者。",
            "BodyText",
        ))
        layout.addWidget(self._text(
            "提示：本程序自己不要选 CABLE Output 当麦克风（那会形成回环），"
            "本程序的麦克风在「设备中心」里选你的**物理麦克风**。", "HintLabel"))
        layout.addStretch(1)
        return page

    def _page_listen(self) -> QWidget:
        page = QWidget(self.sheet)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._text("第 3 步：本地怎么听到音效（可选）", "SectionLabel"))
        layout.addWidget(self._text(
            "方式 A（推荐，不会有自己的回声）：顶栏的混音按钮 → 勾选"
            "「本地也播放音效（不含麦克风）」。\n\n"
            "方式 B（系统级）：Windows 设置 → 系统 → 声音 → 更多声音设置 →"
            " 录制 → CABLE Output → 属性 → 侦听 → 勾选「侦听此设备」并选你的耳机。\n"
            "注意：方式 B 会把混音（含你的麦克风）一起放出来，可能听到自己说话。",
            "BodyText",
        ))
        row = QHBoxLayout()
        sound = QPushButton("打开 Windows 声音设置", page)
        sound.setObjectName("SecondaryButton")
        sound.setFixedHeight(30)
        sound.clicked.connect(open_sound_settings)
        row.addWidget(sound)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------ 导航
    def _refresh_cable(self) -> None:
        cable = device_enum.detect_vb_cable(self._rules, show_all=True)
        if cable.complete:
            self.cable_status.setText("已就绪：" + cable.summary())
        elif cable.installed:
            self.cable_status.setText(
                "注意：" + cable.summary() + "\n端点不完整：请运行安装目录里的"
                "「修复虚拟声卡.cmd」，或重启电脑再试。"
            )
        else:
            self.cable_status.setText(
                "未检测到 VB-CABLE。\n"
                "可以点下面的按钮下载安装（免费），装完需要重启电脑；"
                "也可以先跳过，主界面顶栏会一直提示。"
            )

    def _refresh(self) -> None:
        self.stack.setCurrentIndex(self._index)
        self.step_label.setText(f"第 {self._index + 1} / 3 步")
        self.back_button.setEnabled(self._index > 0)
        self.next_button.setText("完成" if self._index == 2 else "下一步")
        if self._index == 0:
            self._refresh_cable()

    def _go_back(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._refresh()

    def _go_next(self) -> None:
        if self._index >= 2:
            self._finish()
            return
        self._index += 1
        self._refresh()

    def _finish(self) -> None:
        self.finished.emit()
        self.accept()


class HelpDialog(_SheetDialog):
    """常驻帮助：排错 + 快捷动作。"""

    test_tone_requested = Signal()
    device_center_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("帮助与指引", (760, 560), parent)
        self._title("帮助与指引", "按症状找原因")

        self.layout.addWidget(self._section("对方听不到音效", [
            "1. 语音软件的输入设备是否选了 CABLE Output（不是你的物理麦克风）？",
            "2. 顶栏路由是否为「虚拟声卡（推荐）」？旁边是否橙色警告？",
            "3. 点左下「播放测试音」，对方能听到 → 说明路由正常。",
        ]))
        self.layout.addWidget(self._section("本地听不到声音", [
            "1. 顶栏的混音按钮 → 勾选「本地也播放音效（不含麦克风）」（推荐）。",
            "2. 或 Windows 声音设置 → 录制 → CABLE Output → 属性 → 侦听此设备。",
            "3. 检查顶栏音量不是 0%，且没有点静音。",
        ]))
        self.layout.addWidget(self._section("听到了自己的回声", [
            "1. 关掉 Windows 里 CABLE Output 的「侦听此设备」。",
            "2. 用程序内的「本地也播放音效」代替它（只放音效，不含麦克风）。",
            "3. 语音软件不要把自己的麦克风再监听一遍。",
        ]))
        self.layout.addWidget(self._section("音效有电音/爆音", [
            "1. 同时播放太多音效会触发软限幅，降低音效音量。",
            "2. 把 CABLE Input 与 CABLE Output 的采样率都设为 48000Hz。",
            "3. 关掉「互斥模式」试试，避免频繁截断。",
        ]))
        self.layout.addStretch(1)

        row = QHBoxLayout()
        sound = QPushButton("打开 Windows 声音设置", self.sheet)
        sound.setObjectName("SecondaryButton")
        sound.setFixedHeight(32)
        sound.clicked.connect(open_sound_settings)
        row.addWidget(sound)
        tone = QPushButton("播放测试音", self.sheet)
        tone.setObjectName("SecondaryButton")
        tone.setFixedHeight(32)
        tone.clicked.connect(self.test_tone_requested.emit)
        row.addWidget(tone)
        center = QPushButton("打开设备中心", self.sheet)
        center.setObjectName("SecondaryButton")
        center.setFixedHeight(32)
        center.clicked.connect(self.device_center_requested.emit)
        row.addWidget(center)
        row.addStretch(1)
        close = QPushButton("关闭", self.sheet)
        close.setObjectName("PrimaryButton")
        close.setFixedHeight(32)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.layout.addLayout(row)

    def _section(self, title: str, lines: list[str]) -> QFrame:
        box = QFrame(self.sheet)
        box.setObjectName("Card")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        label = QLabel(title, box)
        label.setObjectName("SectionLabel")
        layout.addWidget(label)
        body = QLabel("\n".join(lines), box)
        body.setObjectName("BodyText")
        body.setWordWrap(True)
        layout.addWidget(body)
        return box
