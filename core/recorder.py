"""统一录音引擎（门面）：按模式分派到对应采集后端。

三模式：
    mic     🎤 麦克风          → capture_mic.MicCapture
    system  🔊 系统声音（全部）  → capture_system.SystemCapture
    app     🎯 单个应用        → capture_app.AppCapture（进程 loopback）

门面负责三件事，后端只负责"吐数据"：
1. **汇聚**：把各后端推来的块攒起来，同时统计时长/峰值/RMS（给电平条用）；
2. **格式归一**：采集率不是 44100（WASAPI 常见 48000）时，停止时统一重采样；
3. **状态与错误**：录音中/停止、错误进队列由界面轮询，自动停止的原因单独标记。

线程模型：后端在自己的线程里推块；本类用一把锁保护块列表；
界面只读 ``peak``/``rms``/``duration`` 这些属性，不碰后端对象。
"""

from __future__ import annotations

import queue
import threading

import numpy as np

from . import audio_io
from .capture_app import AppCapture
from .capture_base import CaptureBackend
from .capture_mic import MicCapture
from .capture_system import SystemCapture
from .constants import MODE_APP, MODE_MIC, MODE_SYSTEM, SAMPLE_RATE

MODES = (MODE_MIC, MODE_SYSTEM, MODE_APP)


class RecorderEngine:
    """对外只暴露 start/stop 和几个只读状态。"""

    def __init__(self, samplerate: int = SAMPLE_RATE) -> None:
        self.samplerate = int(samplerate)
        self.events: "queue.Queue[str]" = queue.Queue()
        self.last_error: str | None = None
        #: 需要界面自动停止的原因（"exited" = 目标应用退出）
        self.auto_stop_reason: str | None = None

        self._lock = threading.RLock()
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self._peak = 0.0
        self._rms = 0.0
        self._backend: CaptureBackend | None = None
        self._mode: str = MODE_MIC
        self._capture_rate = int(samplerate)
        self._channels = 1
        self._target_label = ""

    # ------------------------------------------------------------ 只读状态
    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._backend is not None

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def capture_rate(self) -> int:
        with self._lock:
            return self._capture_rate

    @property
    def channels(self) -> int:
        with self._lock:
            return self._channels

    @property
    def target_label(self) -> str:
        with self._lock:
            return self._target_label

    @property
    def duration(self) -> float:
        with self._lock:
            rate = self._capture_rate or self.samplerate
            return self._samples / float(rate) if rate else 0.0

    @property
    def peak(self) -> float:
        with self._lock:
            return self._peak

    @property
    def rms(self) -> float:
        with self._lock:
            return self._rms

    def backend_name(self) -> str:
        backend = self._backend
        return getattr(backend, "backend", "") if backend is not None else ""

    # ------------------------------------------------------------ 错误/事件
    def _notify(self, message: str) -> None:
        self.last_error = message
        try:
            self.events.put_nowait(message)
        except queue.Full:  # pragma: no cover
            pass

    def drain_events(self) -> list[str]:
        messages: list[str] = []
        while True:
            try:
                messages.append(self.events.get_nowait())
            except queue.Empty:
                break
        return messages

    def _on_block(self, block: np.ndarray) -> None:
        if block is None:
            return
        try:
            array = np.asarray(block, dtype=np.float32)
            if array.ndim == 1:
                array = array[:, None]
            if array.size == 0:
                return
            peak = float(np.max(np.abs(array)))
            rms = float(np.sqrt(np.mean(np.square(array))))
            copy = np.array(array, dtype=np.float32, copy=True)
        except Exception:
            return
        with self._lock:
            self._chunks.append(copy)
            self._samples += int(copy.shape[0])
            self._peak = min(1.0, peak)
            self._rms = min(1.0, rms)

    def _on_error(self, message: str) -> None:
        self._notify(message)

    def _on_capture_finished(self, reason: str) -> None:
        """后端自己结束（目标退出等）：让界面看到并自动走停止流程。"""
        self.auto_stop_reason = reason

    # ------------------------------------------------------------ 启动
    def start(self, mode: str, target=None) -> bool:  # noqa: ANN001
        if mode not in MODES:
            self._notify(f"未知的录音模式：{mode}")
            return False
        with self._lock:
            if self._backend is not None:
                self._notify("已经在录音了，请先停止")
                return False
            self._chunks = []
            self._samples = 0
            self._peak = 0.0
            self._rms = 0.0
            self._mode = mode
            self.auto_stop_reason = None

        if mode == MODE_MIC:
            backend: CaptureBackend = MicCapture(self._on_block, self._on_error, device=target)
        elif mode == MODE_SYSTEM:
            backend = SystemCapture(self._on_block, self._on_error, device=target)
        else:
            backend = AppCapture(
                self._on_block,
                self._on_error,
                on_finished=self._on_capture_finished,
            )

        started = backend.start(target)
        if not started:
            self._notify(backend.error or "启动录音失败")
            try:
                backend.stop()
            except Exception:
                pass
            return False

        with self._lock:
            self._backend = backend
            self._capture_rate = int(backend.samplerate or self.samplerate)
            self._channels = int(backend.channels or 1)
            self._target_label = backend.target_label or ""
        return True

    # ------------------------------------------------------------ 停止
    def stop(self) -> tuple[np.ndarray, int] | None:
        """停止并返回 (data, 44100)；没数据返回 None。"""
        with self._lock:
            backend = self._backend
            self._backend = None
        if backend is not None:
            try:
                backend.stop()
            except Exception:
                pass

        with self._lock:
            chunks = self._chunks
            self._chunks = []
            self._peak = 0.0
            self._rms = 0.0
            rate = self._capture_rate
            if not chunks:
                return None
            data = np.concatenate(chunks, axis=0)

        if data.ndim == 1:
            data = data[:, None]
        if data.shape[0] == 0:
            return None

        # 采集率不是 44100（WASAPI 多为 48000）→ 统一转换，保证音效板拿到的格式一致
        if rate and rate != self.samplerate:
            data = audio_io.resample_to(data, int(rate), self.samplerate)
        return np.ascontiguousarray(data, dtype=np.float32), self.samplerate

    def cancel(self) -> None:
        """放弃当前录音（丢弃数据）。"""
        self.stop()
        with self._lock:
            self._chunks = []
            self._samples = 0

    def describe(self) -> str:
        """状态栏/日志用的一行描述。"""
        with self._lock:
            if self._backend is None:
                return "未录音"
            rate = self._capture_rate
        text = f"{self.mode} · {self.target_label}"
        if rate != self.samplerate:
            text += f" · {rate}Hz→{self.samplerate}Hz"
        return text
