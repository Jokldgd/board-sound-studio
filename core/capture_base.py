"""采集后端基类：三种录音模式共用的最小接口。

约定（三个子类都必须遵守）：
* ``on_block(np.float32 (frames, channels))`` 推送音频块，回调在采集线程里执行，
  必须快、不能抛异常、不能碰 Qt；
* ``on_error(message)`` 报告致命错误（设备被占用、激活失败、目标退出等）；
* ``samplerate`` / ``channels`` 是**实际采集**的格式，可能是 48000；
  换算成 44100 由上层 ``RecorderEngine`` 统一做；
* ``stop()`` 必须幂等且能在 2 秒内返回。
"""

from __future__ import annotations

import threading
from typing import Callable


class CaptureBackend:
    """所有采集后端的公共外壳。"""

    mode: str = "base"

    def __init__(
        self,
        on_block: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._on_block = on_block
        self._on_error = on_error
        self.samplerate = 44100
        self.channels = 1
        self.error: str | None = None
        self.target_label = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # ---- 生命周期（子类实现 _open/_read/_close 或自行管理线程） ----
    def start(self, target=None) -> bool:  # noqa: ANN001
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- 给子类用的工具 ----
    def _fail(self, message: str) -> None:
        self.error = message
        try:
            self._on_error(message)
        except Exception:
            pass

    def _emit(self, block) -> None:  # noqa: ANN001
        if block is None or getattr(block, "size", 0) == 0:
            return
        try:
            self._on_block(block)
        except Exception:
            pass

    def _start_thread(self, target) -> None:  # noqa: ANN001
        self._stop.clear()
        self._thread = threading.Thread(target=target, name=f"capture-{self.mode}", daemon=True)
        self._thread.start()


def describe(mode: str) -> str:
    """给界面用的模式中文名。"""
    return {
        "mic": "🎤 麦克风",
        "system": "🔊 系统声音",
        "app": "🎯 单个应用",
    }.get(mode, mode)
