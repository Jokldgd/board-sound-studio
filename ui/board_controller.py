"""左栏控制器：把面板上的操作翻译成"库 + 引擎 + 热键"的动作。

只负责左栏；录音相关逻辑在 ``ui/recorder_panel.py``。
对外三个信号：``message`` / ``error`` / ``library_changed``（主窗口据此存盘+注册热键）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from core.audio_engine import AudioEngine
from core.clip_library import Clip, ClipLibrary
from core.hotkey_manager import HotkeyManager

from .soundboard_panel import SoundboardPanel


class BoardController(QObject):
    """音效板（左栏）的交互控制器。"""

    message = Signal(str)
    error = Signal(str)
    library_changed = Signal()

    def __init__(
        self,
        engine: AudioEngine,
        library: ClipLibrary,
        config,
        panel: SoundboardPanel,
        hotkeys: HotkeyManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.library = library
        self.config = config
        self.panel = panel
        self.hotkeys = hotkeys
        self._states: dict = {}
        self._bound_badge = None
        self._connect()

    # ------------------------------------------------------------------ 接线
    def _connect(self) -> None:
        panel = self.panel
        panel.import_requested.connect(self.import_dialog)
        panel.refresh_requested.connect(self.refresh_directory)
        panel.files_dropped.connect(self.import_paths)
        panel.preview_requested.connect(self.toggle_preview)
        panel.hotkey_bind_requested.connect(self.begin_bind)
        panel.hotkey_changed.connect(self.set_hotkey)
        panel.rename_committed.connect(self.rename_clip)
        panel.delete_requested.connect(self.delete_clip)
        panel.volume_changed.connect(self.set_volume)

    # ------------------------------------------------------------------ 播放
    def toggle_preview(self, clip_id: str) -> None:
        clip = self.library.get(clip_id)
        if clip is None:
            return
        if self.engine.is_playing(clip_id):
            self.engine.release(clip_id)
            return
        self.play(clip)

    def play(self, clip: Clip) -> bool:
        if not clip.exists():
            self.error.emit(f"找不到文件：{clip.file}")
            return False
        if not self.engine.running:
            self.error.emit("音频引擎未运行：请检查顶栏输出设备")
            return False
        return self.engine.trigger(clip.id, clip.path(), clip.volume)

    # ------------------------------------------------------------------ 导入/刷新
    def import_dialog(self) -> None:
        files, _filter = QFileDialog.getOpenFileNames(
            self.panel, "导入音效", str(Path.home()),
            "音频文件 (*.mp3 *.wav *.ogg *.flac);;所有文件 (*)",
        )
        if files:
            self.import_paths(files)

    def import_paths(self, sources: list[str]) -> None:
        self.panel.set_busy(True)
        try:
            added, errors = self.library.import_files(sources)
        finally:
            self.panel.set_busy(False)

        if added:
            for clip in added:
                self.panel.insert_clip(clip)
            self.library_changed.emit()
            self.message.emit(f"已导入 {len(added)} 个音效")
        if errors:
            head = "；".join(errors[:3])
            more = f"（共 {len(errors)} 个问题）" if len(errors) > 3 else ""
            self.error.emit(f"{head}{more}")

    def refresh_directory(self) -> None:
        self.panel.set_busy(True)
        try:
            added, updated = self.library.scan_directory()
        finally:
            self.panel.set_busy(False)

        self.panel.set_clips(self.library.clips)
        self.apply_hotkey_states(self._states)
        self.library_changed.emit()
        if added or updated:
            self.message.emit(f"刷新完成：新增 {len(added)} 个，更新 {updated} 个")
        else:
            self.message.emit("刷新完成：目录里没有新文件")

    # ------------------------------------------------------------------ 编辑
    def rename_clip(self, clip_id: str, name: str) -> None:
        # 走 library.rename：它会把新名字同步写进同名 .json sidecar，
        # 否则重启时 scan_directory 会用旧 sidecar 把名字覆盖回去
        if not self.library.rename(clip_id, name):
            return
        clip = self.library.get(clip_id)
        if clip is not None:
            self.panel.update_row(clip)
        self.library_changed.emit()

    def delete_clip(self, clip_id: str) -> None:
        clip = self.library.get(clip_id)
        if clip is None:
            return
        answer = QMessageBox.question(
            self.panel, "删除音效",
            f"确定删除「{clip.name}」吗？\n\naudio/ 里的音频文件也会一起删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.engine.release(clip_id)
        self.engine.cache.invalidate(clip.path())
        if self.library.remove(clip_id, delete_file=True):
            self.panel.remove_row(clip_id)
            self.library_changed.emit()
            self.message.emit(f"已删除「{clip.name}」")

    def set_volume(self, clip_id: str, value: float) -> None:
        # library.set_volume 会同步 sidecar，避免重启后音量被旧值覆盖
        if self.library.set_volume(clip_id, value):
            self.library_changed.emit()

    # ------------------------------------------------------------------ 快捷键
    def begin_bind(self, clip_id: str) -> None:
        """行内绑定：先挂起全局热键，再让徽章抢键盘。

        必须保证**任何**结束捕获的路径都会解除挂起（按下按键 / Esc / 失焦 /
        超时看门狗），否则全局热键会一直处于挂起状态（历史 bug）。
        """
        row = self.panel.row(clip_id)
        if row is None:
            return
        badge = row.hotkey_badge
        if self._bound_badge is not None and self._bound_badge is not badge:
            try:
                self._bound_badge.binding_cancelled.disconnect(self._end_bind)
            except (RuntimeError, TypeError):
                pass
        self._bound_badge = badge
        badge.binding_cancelled.connect(self._end_bind)
        self.hotkeys.set_suspended(True)
        badge.startCapture()
        self.message.emit("按下要绑定的按键，Esc 取消")
        # 看门狗：15 秒还没结束就强制解除挂起（防止任何异常路径把热键锁死）
        QTimer.singleShot(15000, lambda b=badge: self._watchdog_end(b))

    def _end_bind(self) -> None:
        if self._bound_badge is not None:
            try:
                self._bound_badge.binding_cancelled.disconnect(self._end_bind)
            except (RuntimeError, TypeError):
                pass
            self._bound_badge = None
        self.hotkeys.set_suspended(False)

    def _watchdog_end(self, badge) -> None:  # noqa: ANN001
        if badge.isCapturing():
            badge.stopCapture()
            self.message.emit("快捷键绑定已超时取消，全局热键已恢复")
        self.hotkeys.set_suspended(False)

    def set_hotkey(self, clip_id: str, hotkey: str) -> None:
        self.hotkeys.set_suspended(False)
        clip = self.library.get(clip_id)
        if clip is None:
            return
        hotkey = (hotkey or "").strip().lower()
        if hotkey == clip.hotkey:
            return
        clip.hotkey = hotkey
        self.hotkeys.reset_press_state(clip_id)
        row = self.panel.row(clip_id)
        if row is not None:
            row.set_hotkey(hotkey)
        self.library_changed.emit()
        self.message.emit("快捷键已更新" if hotkey else "快捷键已清除")

    # ------------------------------------------------------------------ 显示同步
    def refresh_board(self) -> None:
        self.panel.set_clips(self.library.clips)
        self.apply_hotkey_states(self._states)

    def apply_hotkey_states(self, states: dict) -> None:
        """状态刷到每一行（热键取自 Clip，不取行上的缓存）。"""
        self._states = states
        for clip in self.library.clips:
            row = self.panel.row(clip.id)
            if row is None:
                continue
            row.set_hotkey(clip.hotkey or "")
            row.set_hotkey_state(states.get(clip.id))

    def set_playing(self, playing_ids: set[str]) -> None:
        self.panel.set_playing(playing_ids)

    def conflicts(self, states: dict) -> int:
        return sum(1 for state in states.values() if state.is_problem)
