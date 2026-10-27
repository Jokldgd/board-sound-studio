"""模式 3：只录某一个应用的声音（Windows 进程 loopback）。

这个后端要做三件"容错"的事，缺一不可：

1. **能力门**：启动前先问 ``app_loopback.quick_probe()``，不支持/激活失败
   直接把原因交给界面，让模式 3 显示为不可用，而不是录出一条静音骗人；
2. **进程存活监控**：录制中目标应用退出 → 立刻停录并上报，
   已录到的部分仍然保留（上层照常可以保存）；
3. **无音频检测**：目标应用在播放静音/被系统静音/DRM 保护时，
   loopback 会"激活成功但没有数据"。这里统计"多久没收到非静音数据"，
   上报一次提示（不强行停录，避免误杀静音片段）。
"""

from __future__ import annotations

import threading
import time

import numpy as np

from . import app_loopback, app_enum
from .capture_base import CaptureBackend

#: 多久没有非静音数据就提示一次（秒）
SILENCE_HINT_SECONDS = 2.5
#: 进程存活检查间隔（秒）
PROCESS_CHECK_INTERVAL = 0.5


class AppCapture(CaptureBackend):
    """对某个 PID 做进程级 loopback。"""

    mode = "app"

    def __init__(self, on_block, on_error, on_finished=None) -> None:  # noqa: ANN001
        super().__init__(on_block, on_error)
        self._on_finished = on_finished
        self.pid: int | None = None
        self.process_name = ""
        self._capture: app_loopback.ProcessLoopbackCapture | None = None
        self._watcher: threading.Thread | None = None
        self._last_loud = time.time()
        self._silence_hinted = False
        self._target_exited = False

    # ------------------------------------------------------------------ 启动
    def start(self, target=None) -> bool:  # noqa: ANN001
        """target: core.app_enum.AppChoice 或 int(PID)。"""
        pid = None
        if isinstance(target, int):
            pid = target
        elif target is not None:
            pid = int(getattr(target, "pid", 0) or 0)
            self.process_name = str(getattr(target, "display_name", "") or "")
        if not pid:
            self._fail("没有选择要录制的应用")
            return False
        if not app_enum.process_alive(pid):
            self._fail(f"所选应用已经退出（PID {pid}），请刷新应用列表")
            return False

        self.pid = pid
        self._last_loud = time.time()
        self._silence_hinted = False
        self._target_exited = False
        label = self.process_name or f"PID {pid}"
        self.target_label = f"{label} (PID {pid})"

        self._capture = app_loopback.ProcessLoopbackCapture(
            pid,
            on_block=self._handle_block,
            on_error=self._handle_error,
        )
        started = self._capture.start(timeout=3.0)
        if not started or self._capture.error:
            self._fail(self._capture.error or "进程 loopback 启动失败")
            self._capture.stop()
            self._capture = None
            return False

        self.samplerate = int(self._capture.samplerate)
        self.channels = int(self._capture.channels)
        self._stop.clear()
        self._watcher = threading.Thread(
            target=self._watch, name="capture-app-watch", daemon=True
        )
        self._watcher.start()
        return True

    # ------------------------------------------------------------------ 回调
    def _handle_block(self, block: np.ndarray) -> None:
        if self._stop.is_set():
            return
        try:
            peak = float(np.max(np.abs(block))) if block.size else 0.0
        except Exception:
            peak = 0.0
        if peak > 1e-4:
            self._last_loud = time.time()
        self._emit(block)

    def _handle_error(self, message: str) -> None:
        if self._stop.is_set():
            return
        self._fail(message)
        self._notify_finished("error")

    def _notify_finished(self, reason: str) -> None:
        if self._on_finished is not None:
            try:
                self._on_finished(reason)
            except Exception:
                pass

    # ------------------------------------------------------------------ 监控
    def _watch(self) -> None:
        """每 0.5s 检查：目标进程还在不在 + 有没有一直静音。"""
        while not self._stop.wait(PROCESS_CHECK_INTERVAL):
            pid = self.pid
            if pid and not app_enum.process_alive(pid):
                self._target_exited = True
                self._fail(f"目标应用已退出（{self.target_label}），录音已停止")
                self._notify_finished("exited")
                return
            if (
                not self._silence_hinted
                and time.time() - self._last_loud > SILENCE_HINT_SECONDS
            ):
                self._silence_hinted = True
                # 只提示，不停录：可能只是这段没声音
                self._fail(
                    "已经超过 "
                    f"{SILENCE_HINT_SECONDS:.0f} 秒没有录到该应用的声音。"
                    "可能它当前没在播放、被静音，或受 DRM 保护无法采集。"
                )

    # ------------------------------------------------------------------ 状态
    @property
    def target_exited(self) -> bool:
        return self._target_exited

    def stop(self) -> None:
        self._stop.set()
        capture = self._capture
        self._capture = None
        if capture is not None:
            capture.stop()
        watcher = self._watcher
        if watcher is not None:
            watcher.join(timeout=1.0)
            self._watcher = None
        super().stop()


# ------------------------------------------------------------------ 能力检测
#: 缓存探测结果，避免每次切模式都跑一遍 COM 激活
_probe_cache: dict[str, object] = {}


def probe_capability(force: bool = False) -> tuple[bool, str]:
    """检测"进程 loopback 是否真的能用"。

    分两层：
    1. 系统层面（build 号 + 导出函数）——不支持就没必要试；
    2. 真激活一次（目标=自己）——有些系统 build 够但激活会被策略/驱动挡掉，
       这时也要禁用模式 3，并把 HRESULT 告诉用户。
    """
    if not force and "result" in _probe_cache:
        return _probe_cache["result"]  # type: ignore[return-value]

    ok, reason = app_loopback.is_supported()
    if not ok:
        _probe_cache["result"] = (False, reason)
        return False, reason

    activated, activate_reason = app_loopback.quick_probe()
    if not activated:
        message = (
            f"当前系统不支持或实现不可用：{activate_reason}。"
            "可以点「重新检测」再试，或改用模式 1/2。"
        )
        _probe_cache["result"] = (False, message)
        return False, message

    _probe_cache["result"] = (True, "可用")
    return True, "可用"


def clear_probe_cache() -> None:
    _probe_cache.clear()
