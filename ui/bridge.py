"""跨线程信号桥：后台线程（热键/探测）→ Qt 主线程。

只做转发，不含任何逻辑：热键线程 emit，主线程槽函数接收，
Qt 的自动排队（QueuedConnection）保证槽在 UI 线程执行。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    """后台线程 → 主线程。"""

    hotkey_pressed = Signal(str)        # clip_id 或 hotkey_manager.RECORD_ID
    hotkey_released = Signal(str)
    hotkey_log = Signal(str)
    capability = Signal(bool, str)      # 模式 3 探测结果 (可用, 原因)
