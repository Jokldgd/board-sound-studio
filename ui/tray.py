"""系统托盘：显示窗口 / 开始录音 / 停止录音 / 退出（原生菜单）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import icon as app_icon_module


def tray_available() -> bool:
    return QSystemTrayIcon.isSystemTrayAvailable()


class TrayIcon(QSystemTrayIcon):
    """托盘图标 + 四项菜单。"""

    toggle_requested = Signal()
    start_record_requested = Signal()
    stop_record_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(app_icon_module.app_icon(), parent)
        self.setToolTip("Soundboard Studio")
        self._recording = False

        menu = QMenu()
        self.toggle_action = menu.addAction("显示窗口", self.toggle_requested.emit)
        menu.addSeparator()
        self.start_action = menu.addAction("开始录音", self.start_record_requested.emit)
        self.stop_action = menu.addAction("停止录音", self.stop_record_requested.emit)
        menu.addSeparator()
        menu.addAction("退出", self.quit_requested.emit)
        self.setContextMenu(menu)
        self._menu = menu
        self._apply_state()

        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------------ 状态
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self.toggle_requested.emit()

    def _apply_state(self) -> None:
        self.start_action.setEnabled(not self._recording)
        self.stop_action.setEnabled(self._recording)
        self.start_action.setVisible(not self._recording)
        self.stop_action.setVisible(self._recording)

    def set_recording(self, recording: bool) -> None:
        recording = bool(recording)
        if recording == self._recording:
            return
        self._recording = recording
        self.setIcon(app_icon_module.app_icon(recording))
        self.setToolTip("Soundboard Studio · 录音中" if recording else "Soundboard Studio")
        self._apply_state()

    def set_window_visible(self, visible: bool) -> None:
        self.toggle_action.setText("隐藏窗口" if visible else "显示窗口")

    def notify(self, title: str, message: str) -> None:
        try:
            self.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)
        except Exception:
            pass


class TrayController:
    """托盘 + 窗口显隐 + 关闭到托盘的收口（主窗口只留三行调用）。"""

    def __init__(self, window, on_start_record, on_stop_record, on_quit) -> None:  # noqa: ANN001
        self.window = window
        self.tray: TrayIcon | None = None
        self._hint_shown = False
        self._on_start_record = on_start_record
        self._on_stop_record = on_stop_record
        self._on_quit = on_quit
        if not tray_available():
            return
        self.tray = TrayIcon(window)
        self.tray.toggle_requested.connect(self.toggle_window)
        self.tray.start_record_requested.connect(self._on_start_record)
        self.tray.stop_record_requested.connect(self._on_stop_record)
        self.tray.quit_requested.connect(self._on_quit)
        self.tray.set_window_visible(True)
        self.tray.show()

    # ------------------------------------------------------------------ 显隐
    def toggle_window(self) -> None:
        window = self.window
        if window.isVisible() and not window.isMinimized():
            window.hide()
        else:
            window.show()
            window.setWindowState(
                window.windowState() & ~Qt.WindowState.WindowMinimized
                | Qt.WindowState.WindowActive
            )
            window.raise_()
            window.activateWindow()
        self.set_window_visible(window.isVisible())

    def set_window_visible(self, visible: bool) -> None:
        if self.tray is not None:
            self.tray.set_window_visible(visible)

    # ------------------------------------------------------------------ 其它
    def notify_once(self, title: str, message: str) -> None:
        if self.tray is None or self._hint_shown:
            return
        self._hint_shown = True
        self.tray.notify(title, message)

    def set_recording(self, recording: bool) -> None:
        if self.tray is not None:
            self.tray.set_recording(recording)

    def shutdown(self) -> None:
        if self.tray is not None:
            self.tray.hide()
            self.tray = None
