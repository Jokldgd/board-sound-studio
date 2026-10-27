"""模式 1：麦克风 / 虚拟声卡输入。

首选 **pyaudiowpatch**（和模式 2 同一套后端，行为一致）；
装不上或打不开时自动退到 **sounddevice**。

⚠ 线程约定（踩过坑）：**PyAudio 的创建、开流、读取、关闭、terminate 必须全部在
同一个线程里完成**。之前在主线程创建 PyAudio、又在采集线程里 terminate，
会直接触发访问违例（0xC0000005）把进程打崩。所以这里 start() 只负责起线程 +
等"开好了"的信号，真正的 PortAudio 生命周期都在采集线程内。

采样率策略：先试 44100，失败退设备默认采样率（WASAPI 麦克风多为 48000）；
上层 ``RecorderEngine.stop()`` 会把非 44100 统一重采样。
"""

from __future__ import annotations

import threading

import numpy as np

from .capture_base import CaptureBackend

try:
    import pyaudiowpatch as _pyaudio
except Exception:  # pragma: no cover
    _pyaudio = None  # type: ignore[assignment]

try:
    import sounddevice as _sd
except Exception:  # pragma: no cover
    _sd = None  # type: ignore[assignment]

#: 每块约 1024 帧（≈23ms@44.1k）
BLOCK_FRAMES = 1024
#: 等待"流已打开"的最长时间
OPEN_TIMEOUT = 4.0


class MicCapture(CaptureBackend):
    """从指定输入设备采集。"""

    mode = "mic"

    def __init__(self, on_block, on_error, device=None) -> None:  # noqa: ANN001
        super().__init__(on_block, on_error)
        self.device = device          # core.device_enum.DeviceChoice | None
        self.backend = ""
        self._opened = threading.Event()

    # ------------------------------------------------------------------ 启动
    def start(self, target=None) -> bool:  # noqa: ANN001
        self.error = None
        self._opened = threading.Event()
        if _pyaudio is not None:
            self._start_thread(self._run_pyaudio)
            self._opened.wait(OPEN_TIMEOUT)
            if self.error is None and self.backend == "pyaudiowpatch":
                return True
            # 打不开就退 sounddevice，别让模式 1 直接不可用
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=1.5)
                self._thread = None

        if _sd is not None:
            return self._start_sounddevice()
        self._fail("没有可用的录音后端（请安装 pyaudiowpatch 或 sounddevice）")
        return False

    def _formats(self) -> list[tuple[int, int]]:
        device_rate = int(getattr(self.device, "samplerate", 0) or 44100)
        max_channels = int(getattr(self.device, "channels", 1) or 1)
        channels = [1, 2] if max_channels >= 2 else [1]
        formats: list[tuple[int, int]] = []
        for rate in (44100, device_rate):
            if rate <= 0:
                continue
            for ch in channels:
                if (rate, ch) not in formats:
                    formats.append((rate, ch))
        return formats

    # ---- pyaudiowpatch：全部操作都在本线程 ----
    def _run_pyaudio(self) -> None:
        import pyaudiowpatch as pyaudio

        audio = None
        stream = None
        try:
            audio = pyaudio.PyAudio()
            index = self.device.index if self.device is not None else None
            last_error = ""
            rate = channels = 0
            for candidate_rate, candidate_channels in self._formats():
                try:
                    stream = audio.open(
                        format=pyaudio.paFloat32,
                        channels=candidate_channels,
                        rate=candidate_rate,
                        input=True,
                        input_device_index=index,
                        frames_per_buffer=BLOCK_FRAMES,
                    )
                except Exception as exc:
                    last_error = str(exc)
                    continue
                rate, channels = candidate_rate, candidate_channels
                break

            if stream is None:
                self.error = last_error
                self._opened.set()
                self._fail(f"打不开麦克风：{last_error or '未知原因'}")
                return

            self.samplerate = rate
            self.channels = channels
            self.backend = "pyaudiowpatch"
            self.target_label = (
                self.device.short_name if self.device is not None else "系统默认麦克风"
            )
            self._opened.set()

            while not self._stop.is_set():
                try:
                    raw = stream.read(BLOCK_FRAMES, exception_on_overflow=False)
                except Exception as exc:
                    if not self._stop.is_set():
                        self._fail(f"读取麦克风数据失败：{exc}")
                    break
                if not raw:
                    continue
                data = np.frombuffer(raw, dtype=np.float32)
                data = data.reshape(-1, channels) if channels > 1 else data.reshape(-1, 1)
                self._emit(np.ascontiguousarray(data))
        except Exception as exc:  # pragma: no cover
            self._fail(f"麦克风采集异常：{exc}")
            self._opened.set()
        finally:
            # 关流 + terminate 都留在这个线程，绝不跨线程释放
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            try:
                if audio is not None:
                    audio.terminate()
            except Exception:
                pass
            self._opened.set()

    # ---- sounddevice 回退 ----
    def _start_sounddevice(self) -> bool:
        index = self.device.index if self.device is not None else None
        last_error = ""
        channels_ = max(1, int(getattr(self.device, "channels", 1) or 1))
        for rate, channels in self._formats():
            try:
                stream = _sd.InputStream(
                    samplerate=rate,
                    blocksize=BLOCK_FRAMES,
                    device=index,
                    channels=channels,
                    dtype="float32",
                    callback=self._sd_callback,
                )
                stream.start()
            except Exception as exc:
                last_error = str(exc)
                continue
            self.samplerate = rate
            self.channels = channels
            self.backend = "sounddevice"
            self.target_label = (
                self.device.short_name if self.device is not None else "系统默认麦克风"
            )
            self._sd_stream = stream
            return True
        self._fail(f"打不开麦克风：{last_error or '未知原因'}")
        return False

    def _sd_callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if self._stop.is_set():
            return
        try:
            self._emit(np.array(indata, dtype=np.float32, copy=True))
        except Exception:
            pass

    # ------------------------------------------------------------------ 停止
    def stop(self) -> None:
        self._stop.set()
        stream = getattr(self, "_sd_stream", None)
        if stream is not None:
            self._sd_stream = None
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        # pyaudiowpatch 那条路径由采集线程自己关流，这里只等它退出
        super().stop()
