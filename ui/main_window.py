"""主窗口：无边框外壳 + 三段布局 + 信号路由（只做粘合）。

业务都在别处：
* 左栏交互 → ``ui/board_controller.py``
* 顶栏设备/音量 → ``ui/top_bar.py``
* 录音全流程 → ``ui/recorder_panel.py``
* 引擎/库/热键 → ``core/``
"""

from __future__ import annotations

import logging
import subprocess
import threading

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core import capture_app, device_enum
from core.audio_engine import (
    ROUTE_DUAL,
    ROUTE_LOCAL,
    ROUTE_VIRTUAL,
    AudioEngine,
    OutputRoute,
    RouteStatus,
)
from core.clip_library import Clip, ClipLibrary
from core.config import ConfigStore
from core.constants import MODE_TITLES
from core.hotkey_manager import RECORD_ID, Binding, HotkeyManager
from core.recorder import RecorderEngine

from . import theme
from .board_controller import BoardController
from .bridge import Bridge
from .icon import app_icon
from .recorder_panel import RecorderPanel
from .settings_dialog import SettingsDialog, apply_values
from .soundboard_panel import SoundboardPanel
from .status_bar import STATE_OK, STATE_RECORDING, STATE_WARNING, StatusBar
from .text import strip_emoji

logger = logging.getLogger("studio.ui")

RESIZE_MARGIN = 6
TRANSIENT_MS = 3000


class MainWindow(QMainWindow):
    """Soundboard Studio 主窗口（无边框、可拖拽缩放）。"""

    def __init__(self, store: ConfigStore) -> None:
        super().__init__()
        self.store = store
        self.config = store.config
        self.library = ClipLibrary(self.config.clips)
        store.bind_library(self.library)
        self.library.configure_audio_dir(self.config.save_dir)

        self.setWindowTitle("Soundboard Studio")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(*theme.SIZE["window"])
        self.setMinimumSize(1040, 620)

        self._quitting = False
        self._shutdown_done = False
        self._app_capable: bool | None = None
        self._app_capable_reason = ""
        self._transient_timer = QTimer(self)
        self._transient_timer.setSingleShot(True)
        self._transient_timer.timeout.connect(self._restore_status)
        self._resize_edges: Qt.Edge | None = None

        # ---- 引擎 / 热键 ----
        self.bridge = Bridge(self)
        self.bridge.hotkey_pressed.connect(self._on_hotkey_press)
        self.bridge.hotkey_released.connect(self._on_hotkey_release)
        self.bridge.capability.connect(self._on_capability)

        self.engine = AudioEngine()
        self.engine.configure_rules(self._rules(), self.config.show_all_devices)
        self.engine.set_master_volume(self.config.master_volume)
        self.engine.set_exclusive(self.config.exclusive_mode)
        self._ensure_route()
        # 启动时就要应用混音设置：否则"本地也播放音效"这类开关在重启后只是
        # 界面上勾着、引擎完全不知道（历史 bug）
        mixer = self.config.mixer_settings()
        self.engine.set_mixer(mixer, restart=False)
        self._route_status = self.engine.set_route(self.config.route())
        self.engine.set_local_playback(mixer.local_playback)
        self._route_signature: tuple = ()

        self.recorder_engine = RecorderEngine()
        self.hotkeys = HotkeyManager(
            on_press=self.bridge.hotkey_pressed.emit,
            on_release=self.bridge.hotkey_released.emit,
        )

        self._build_ui()
        self._load_library()
        self._setup_tray()
        self.hotkeys.start()
        logger.info("全局热键通道：%s",
                    "自建底层钩子 + RegisterHotKey 双通道（后台可用）"
                    if self.hotkeys._hook_active
                    else "RegisterHotKey + keyboard 钩子（降级）")
        self._apply_hotkeys()
        self._probe_capability()

        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._warn_cable_health()
        # 启动后自检列表行几何：真机上若出现"行没被布局"，这里会自愈并写日志
        QTimer.singleShot(350, self._audit_clip_rows)

    def _audit_clip_rows(self) -> None:
        try:
            mismatched = self.board_panel.audit_rows()
        except Exception as exc:  # noqa: BLE001
            logger.warning("列表行几何自检失败：%s", exc)
            return
        if not mismatched:
            return
        logger.warning("列表行未正确布局（%d 行），已强制重排：%s", len(mismatched), mismatched[:3])
        self.board_panel._force_list_layout()
        remaining = self.board_panel.audit_rows()
        if remaining:
            logger.error("强制重排后仍有 %d 行异常：%s", len(remaining), remaining[:3])

    # ================================================================== 装配
    def _rules(self) -> dict:
        return device_enum.merge_rules(self.config.device_name_rules)

    # ================================================================== 输出路由
    def _ensure_route(self) -> None:
        """首次运行/旧配置迁移：没有 output_route 时按"有没有 CABLE"给一个默认值。"""
        if self.config.output_route:
            return
        cable = device_enum.find_cable_input(self._rules(), show_all=True)
        physical = device_enum.physical_outputs(self._rules())
        default = device_enum.default_output(self._rules())
        if cable is not None:
            monitor = next((c for c in physical if c.is_default), physical[0] if physical else None)
            route = OutputRoute(
                mode=ROUTE_VIRTUAL,
                primary_name=cable.name,
                monitor_name=monitor.name if monitor else "",
            )
        else:
            route = OutputRoute(
                mode=ROUTE_LOCAL,
                primary_name=default.name if default else self.config.output_device,
            )
        self.config.apply_route(route)
        self._save_config()

    def _on_route_changed(self, route: OutputRoute) -> None:
        self.config.apply_route(route)
        mixer = self.config.mixer_settings()
        self.engine.set_mic_gain(mixer.mic_gain)
        self.engine.set_sfx_gain(mixer.sfx_gain)
        status = self.engine.set_route(route)
        self._route_status = status
        self._route_signature = self._signature_of(status)
        self.top_bar.set_route_status(status)
        self.top_bar.mixer_popover.apply_route_mode(
            self.top_bar.route_combo.currentData() or ROUTE_VIRTUAL
        )
        if status.primary_name:
            self.config.output_device = status.primary_name
        self._save_config()
        if status.degrade_reason:
            self.show_error(status.degrade_reason)
        elif status.effective_mode == ROUTE_DUAL:
            self.show_message(
                f"双输出已开启：{status.primary_label} + {status.monitor_label}"
                f"（额外延迟约 {status.latency_ms:.0f}ms）"
            )
        else:
            self.show_message(self.engine.describe())

    def _on_mixer_changed(self, settings) -> None:  # noqa: ANN001
        """混音设置变化：写配置 → 重建流（开/关麦克风）→ 刷新状态。

        麦克风只进输出（虚拟声卡），不写本地扬声器 —— 耳返功能已移除。
        """
        self.config.apply_mixer(settings)
        self.engine.set_mic_gain(settings.mic_gain)
        self.engine.set_sfx_gain(settings.sfx_gain)
        status = self.engine.set_mixer(settings)
        local_ok = self.engine.set_local_playback(settings.local_playback)
        self._route_status = status
        self._route_signature = self._signature_of(status)
        self.top_bar.set_route_status(status)
        self._save_config()
        if settings.local_playback and not local_ok:
            self.show_error("本地音效监听打不开（没有可用扬声器？）")
        elif status.degrade_reason:
            self.show_error(status.degrade_reason)
        elif not settings.enabled:
            self.show_message("混音已关闭：只播放音效")
        elif status.primary_ok:
            mic = strip_emoji(settings.mic_name)
            suffix = "；本地只放音效（不含麦克风）" if settings.local_playback else ""
            self.show_message(f"混音已开启：麦克风「{mic}」+ 音效 一起输出{suffix}")

    @staticmethod
    def _signature_of(status: RouteStatus) -> tuple:
        return (
            status.mode,
            status.effective_mode,
            status.primary_ok,
            status.monitor_ok,
            status.degraded_from,
            status.primary_label,
            status.monitor_label,
            status.samplerate,
        )

    def _refresh_route_status(self) -> None:
        status = self.engine.route_status()
        signature = self._signature_of(status)
        if signature != self._route_signature:
            self._route_signature = signature
            self._route_status = status
            self.top_bar.set_route_status(status)

    def _on_test_route(self) -> None:
        if self.engine.play_test_tone(2.0):
            self.show_message(
                "测试音已播放。本地听到 + 语音软件另一端听到 = 配置正确。"
            )
        else:
            self.show_error("测试音没播出去：请先检查输出路由（虚拟声卡装了吗）")

    def _on_open_sound_settings(self) -> None:
        try:
            subprocess.Popen("control mmsys.cpl", shell=True)
            self.show_message(
                "已打开系统声音设置：录制 → CABLE Output → 属性 → 侦听 → 勾选「侦听此设备」"
            )
        except Exception as exc:  # noqa: BLE001
            self.show_error(f"打开声音设置失败：{exc}")

    def _reprobe_vb_cable(self) -> None:
        """设置里点「重新检测设备」：重扫 CABLE 两端并刷新顶栏徽章。"""
        self.engine.configure_rules(self._rules(), self.config.show_all_devices)
        self.top_bar.reload_devices()
        status = self.engine.set_route(self.config.route())
        self._route_status = status
        self._route_signature = self._signature_of(status)
        self.top_bar.set_route_status(status)
        cable = device_enum.detect_vb_cable(self._rules(), show_all=True)
        self.show_message(cable.summary())

    def _warn_cable_health(self) -> None:
        """启动自检：虚拟声卡两端采样率不一致时，明确告诉用户会变调。"""
        try:
            cable = device_enum.detect_vb_cable(self._rules(), show_all=True)
        except Exception:  # noqa: BLE001
            return
        if cable.installed and cable.rate_mismatch:
            self.show_error(cable.summary())

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("Shell")
        self._root = root
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        from .widgets import TitleBar

        self.title_bar = TitleBar("Soundboard Studio", root)
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.maximize_requested.connect(self._toggle_maximize)
        self.title_bar.close_requested.connect(self.close)
        outer.addWidget(self.title_bar)

        from .top_bar import TopBar

        self.top_bar = TopBar(self.engine, self.config, self._rules, root)
        self.top_bar.changed.connect(self._save_config)
        self.top_bar.message.connect(self.show_message)
        self.top_bar.error.connect(self.show_error)
        self.top_bar.settings_requested.connect(self._open_settings)
        self.top_bar.route_changed.connect(self._on_route_changed)
        self.top_bar.test_route_requested.connect(self._on_test_route)
        self.top_bar.open_sound_settings_requested.connect(self._on_open_sound_settings)
        self.top_bar.mixer_changed.connect(self._on_mixer_changed)
        self.top_bar.bind_route(self.config.route())
        self.top_bar.set_route_status(self._route_status)
        self.top_bar.bind_mixer(self.config.mixer_settings())
        outer.addWidget(self.top_bar)
        outer.addWidget(self._separator("HSep"))

        body = QSplitter(Qt.Orientation.Horizontal, root)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(1)
        body.setStyleSheet("QSplitter::handle { background: transparent; }")

        left = QWidget(body)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.board_panel = SoundboardPanel(left)
        left_layout.addWidget(self.board_panel)

        self.recorder_panel = RecorderPanel(
            self.recorder_engine, self.library, self.config,
            on_config_changed=self._save_config, parent=body,
        )
        body.addWidget(left)
        body.addWidget(self.recorder_panel)
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 0)
        body.setSizes([theme.SIZE["window"][0] - theme.SIZE["sidePanel"],
                       theme.SIZE["sidePanel"]])
        outer.addWidget(body, 1)
        outer.addWidget(self._separator("HSep"))

        self.status_bar = StatusBar(root)
        outer.addWidget(self.status_bar)
        self.setCentralWidget(root)

        # ---- 控制器 ----
        self.board = BoardController(
            self.engine, self.library, self.config,
            self.board_panel, self.hotkeys, self,
        )
        self.board.message.connect(self.show_message)
        self.board.error.connect(self.show_error)
        self.board.library_changed.connect(self._on_library_changed)

        recorder = self.recorder_panel
        recorder.mode_changed.connect(self._on_record_mode_changed)
        recorder.target_changed.connect(self._on_target_changed)
        recorder.refresh_requested.connect(self._on_refresh_targets)
        recorder.clip_saved.connect(self._on_clip_saved)
        recorder.status_changed.connect(self.show_message)
        recorder.error.connect(self.show_error)
        recorder.recording_changed.connect(self._on_recording_changed)
        recorder.bind_config(self.config, self._rules())

    @staticmethod
    def _separator(name: str) -> QFrame:
        line = QFrame()
        line.setObjectName(name)
        line.setFixedHeight(1)
        return line

    # ================================================================== 数据/热键
    def _load_library(self) -> None:
        added, updated = self.library.scan_directory()
        self.board.refresh_board()
        if added:
            self.show_message(f"从目录收进 {len(added)} 个音效")
        if updated:
            self.show_message(f"{updated} 个音效的名称/音量按 .json 更新")

    def _apply_hotkeys(self) -> None:
        bindings = [Binding(RECORD_ID, self.config.record_hotkey or "", "record")]
        bindings += [
            Binding(clip.id, clip.hotkey, clip.mode)
            for clip in self.library.clips if clip.hotkey
        ]
        states = self.hotkeys.apply(bindings)
        self.board.apply_hotkey_states(states)
        # 把每条绑定的结果写进日志：排查"切窗口就失灵"时看这里最快
        for key_id, state in states.items():
            if state.status == "ok":
                continue
            logger.info("热键 %s：%s（%s）%s", key_id, state.status,
                        state.backend, state.message)
        problems = self.board.conflicts(states)
        if problems:
            self.show_error(f"{problems} 条快捷键冲突或被占用，见行内徽章")

    def _on_library_changed(self) -> None:
        self._save_config()
        self._apply_hotkeys()

    def _on_clip_saved(self, clip: Clip) -> None:
        self.board_panel.insert_clip(clip)
        self._apply_hotkeys()
        self._save_config()
        self.show_message(f"已加入音效板：{clip.name}")

    def _on_hotkey_press(self, key_id: str) -> None:
        logger.info("热键触发：%s", key_id)
        if key_id == RECORD_ID:
            self.recorder_panel.toggle_recording()
            return
        clip = self.library.get(key_id)
        if clip is not None:
            self.board.play(clip)

    def _on_hotkey_release(self, key_id: str) -> None:
        if key_id != RECORD_ID:
            self.engine.release(key_id)

    # ================================================================== 录音面板事件
    def _on_record_mode_changed(self, mode: str) -> None:
        self.config.record_mode = mode
        self._save_config()
        self._update_status()

    def _on_target_changed(self) -> None:
        mode = self.recorder_panel.current_mode()
        key = self.recorder_panel.key()
        if mode == "mic":
            self.config.mic_device = key
            self.config.remember_device("mic", key)
        elif mode == "system":
            self.config.system_device = key
            self.config.remember_device("system", key)
        else:
            self.config.app_pid = int(key) if key.isdigit() else None
        self._save_config()

        hint = self.engine.conflicts_with(key if mode != "app" else None)
        if hint:
            self.show_message(hint)

    def _on_refresh_targets(self) -> None:
        self.recorder_panel.refresh_targets()
        self.show_message("已刷新目标列表")

    def _on_recording_changed(self, recording: bool) -> None:
        self.tray.set_recording(recording)
        self._update_status()

    # ================================================================== 探测/设置
    def _probe_capability(self) -> None:
        def worker() -> None:
            ok, reason = capture_app.probe_capability()
            self.bridge.capability.emit(ok, reason)

        threading.Thread(target=worker, name="probe-app-loopback", daemon=True).start()

    def _on_capability(self, ok: bool, reason: str) -> None:
        self._app_capable = ok
        self._app_capable_reason = reason
        self.recorder_panel.set_capability(ok, reason)
        if not ok:
            self.show_error(f"单个应用模式不可用：{reason}")

    def _open_settings(self) -> None:
        capability = (None if self._app_capable is None
                      else (self._app_capable, self._app_capable_reason))
        dialog = SettingsDialog(
            self.config, capability, self.hotkeys.set_suspended, parent=self
        )
        dialog.route_probe_requested.connect(self._reprobe_vb_cable)
        dialog.open_sound_settings_requested.connect(self._on_open_sound_settings)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        apply_values(self.config, values)
        self.library.configure_audio_dir(self.config.save_dir)
        self.engine.configure_rules(self._rules(), self.config.show_all_devices)
        self.top_bar.sync_from_config()
        # 设置里可能改了路由 → 重新开流并刷新徽章
        self._route_status = self.engine.set_route(self.config.route())
        self.top_bar.set_route_status(self._route_status)
        self.recorder_panel.bind_config(self.config, self._rules())
        self._apply_hotkeys()
        self._save_config()
        if self._route_status.degrade_reason:
            self.show_error(self._route_status.degrade_reason)
        else:
            self.show_message("设置已保存")

    # ================================================================== 状态
    def _tick(self) -> None:
        self.engine.tick()
        for message in self.engine.drain_events():
            self.show_error(message)
        self._refresh_route_status()
        playing = set(self.engine.active_ids())
        self.board.set_playing(playing)
        names = [clip.name for clip_id in playing
                 if (clip := self.library.get(clip_id)) is not None]
        label = "正在播放：" + "、".join(names[:3]) + ("…" if len(names) > 3 else "")
        self.status_bar.set_playing(label, playing=bool(names))
        self._update_status()

    def _update_status(self, force: bool = False) -> None:
        if self._transient_timer.isActive() and not force:
            return
        if self.recorder_engine.is_recording:
            self.status_bar.set_status("录音中", STATE_RECORDING)
        else:
            self.status_bar.set_status(
                f"运行中 · {len(self.library.clips)} 个音效", STATE_OK
            )
        mode = self.recorder_panel.current_mode()
        target = self.recorder_panel.current_target_label()
        self.status_bar.set_mode(
            strip_emoji(MODE_TITLES.get(mode, mode)) + (f" · {target}" if target else ""),
            {"mic": "mic", "system": "speaker", "app": "target"}.get(mode, "mic"),
        )

    def show_message(self, text: str) -> None:
        if not text:
            return
        self.status_bar.set_status(text, STATE_OK, tooltip=text)
        self._transient_timer.start(TRANSIENT_MS)

    def show_error(self, text: str) -> None:
        if not text:
            return
        self.status_bar.set_status(text, STATE_WARNING, tooltip=text)
        self._transient_timer.start(TRANSIENT_MS)

    def _restore_status(self) -> None:
        self._transient_timer.stop()
        self._update_status(force=True)

    def _save_config(self) -> None:
        self.store.save()

    # ================================================================== 窗口行为
    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange:
            return
        maximized = self.isMaximized()
        self._root.setObjectName("ShellMaximized" if maximized else "Shell")
        style = self._root.style()
        style.unpolish(self._root)
        style.polish(self._root)
        self.title_bar.set_maximized(maximized)
        if self.tray is not None and maximized is False and self.isMinimized():
            QTimer.singleShot(0, self.hide)
            self.tray.notify_once("Soundboard Studio 仍在后台", "全局快捷键有效；录音不会中断")
            self.tray.set_window_visible(False)

    def _edges_at(self, position: QPoint) -> Qt.Edge | None:
        """命中窗口边缘（8 方向）。注意 ``Qt.Edge`` 是 Flag 枚举，不能 ``int()``。"""
        if self.isMaximized():
            return None
        rect = self.rect()
        margin = RESIZE_MARGIN
        flags: list[Qt.Edge] = []
        if position.x() <= margin:
            flags.append(Qt.Edge.LeftEdge)
        if position.x() >= rect.width() - margin:
            flags.append(Qt.Edge.RightEdge)
        if position.y() <= margin:
            flags.append(Qt.Edge.TopEdge)
        if position.y() >= rect.height() - margin:
            flags.append(Qt.Edge.BottomEdge)
        if not flags:
            return None
        edges = flags[0]
        for flag in flags[1:]:
            edges |= flag
        return edges

    @staticmethod
    def _cursor_for(edges: Qt.Edge | None) -> Qt.CursorShape:
        if edges is None:
            return Qt.CursorShape.ArrowCursor
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeVerCursor

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            edges = self._edges_at(event.position().toPoint())
            self.setCursor(QCursor(self._cursor_for(edges)))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.position().toPoint())
            if edges is not None:
                handle = self.windowHandle()
                if handle is not None and hasattr(handle, "startSystemResize"):
                    if handle.startSystemResize(edges):
                        event.accept()
                        return
        super().mousePressEvent(event)

    # ================================================================== 托盘/退出
    def _setup_tray(self) -> None:
        from .tray import TrayController

        self.tray = TrayController(
            self,
            self.recorder_panel.start_recording,
            self.recorder_panel.stop_recording,
            self._quit,
        )

    def _quit(self) -> None:
        """真正退出：先标记，再关窗，最后显式 quit 事件循环。

        只 close() 不够 —— 窗口隐藏到托盘过的话，Qt 不会因为"最后一个窗口关闭"
        而退出事件循环，进程就留在任务管理器里（历史 bug）。
        """
        self._quitting = True
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        """点窗口的 ✕ = **真正退出**（不再隐藏到托盘）。

        之前"✕ 只是最小化到托盘"会让任务管理器里留下进程，用户以为没退出；
        现在 ✕ 一律释放音频流、注销热键、隐藏托盘并结束事件循环。
        """
        self._quitting = True
        self.shutdown()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def shutdown(self) -> None:
        """幂等清理：每一步独立 try，任何一步失败都不影响后面的释放。

        顺序很重要：先停录音/播放（释放设备），再存盘，再注销热键与托盘。
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        for timer in (self._timer, self._transient_timer):
            try:
                timer.stop()
            except Exception:
                pass
        for step in (
            self.recorder_panel.shutdown,     # 停录音、释放采集流
            self.engine.close,                # 停播放/混音、释放输出流
            self.store.flush,                 # 落盘配置
            self.hotkeys.stop,                # 注销全局热键 + unhook
            self.tray.shutdown,               # 隐藏托盘图标
        ):
            try:
                step()
            except Exception as exc:  # noqa: BLE001
                logger.warning("退出清理步骤失败（%s）：%s", getattr(step, "__name__", step), exc)
