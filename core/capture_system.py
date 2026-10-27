"""模式 2：系统声音（某个输出设备上所有应用的混音）。

首选 **pyaudiowpatch** 的 loopback 设备（本质是该扬声器的"回环输入"）；
pyaudiowpatch 不可用时退到 **sounddevice + WasapiSettings(loopback=True)**。

⚠ 与模式 1 同样的线程约定：PyAudio 的创建/开流/读取/关闭/terminate
全部在采集线程内完成，避免跨线程释放导致进程崩溃。

注意：本模式会录进**该输出设备上所有应用**的声音，不做隔离；
只要一个应用的声音请用模式 3。
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

BLOCK_FRAMES = 1024
OPEN_TIMEOUT = 4.0


class SystemCapture(CaptureBackend):
    """采集某个输出设备当前的混音。"""

    mode = "system"

    def __init__(self, on_block, on_error, device=None) -> None:  # noqa: ANN001
        super().__init__(on_block, on_error)
        self.device = device          # DeviceChoice（is_loopback=True）
        self.backend = ""
        self._opened = threading.Event()

    # ------------------------------------------------------------------ 启动
    def start(self, target=None) -> bool:  # noqa: ANN001
        if self.device is None:
            self._fail("没有选择输出设备")
            return False

        self.error = None
        self._opened = threading.Event()
        if _pyaudio is not None and getattr(self.device, "is_loopback", False):
            self._start_thread(self._run_pyaudio)
            self._opened.wait(OPEN_TIMEOUT)
            if self.error is None and self.backend == "pyaudiowpatch":
                return True
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=1.5)
                self._thread = None

        if _sd is not None:
            return self._start_sounddevice()
        self._fail("当前无法录系统声音：pyaudiowpatch 与 sounddevice 都不可用")
        return False

    def _formats(self) -> list[tuple[int, int]]:
        rate = int(getattr(self.device, "samplerate", 0) or 48000)
        channels = max(1, int(getattr(self.device, "channels", 2) or 2))
        formats: list[tuple[int, int]] = []
        for candidate in (44100, rate):
            if candidate > 0 and (candidate, channels) not in formats:
                formats.append((candidate, channels))
        return formats

    # ---- pyaudiowpatch loopback ----
    def _run_pyaudio(self) -> None:
        import pyaudiowpatch as pyaudio

        audio = None
        stream = None
        try:
            audio = pyaudio.PyAudio()
            index = int(self.device.index)
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
                self._fail(f"打不开该设备的 loopback：{last_error or '未知原因'}")
                return

            self.samplerate = rate
            self.channels = channels
            self.backend = "pyaudiowpatch"
            self.target_label = self.device.short_name
            self._opened.set()

            while not self._stop.is_set():
                try:
                    raw = stream.read(BLOCK_FRAMES, exception_on_overflow=False)
                except Exception as exc:
                    if not self._stop.is_set():
                        self._fail(f"读取系统声音失败：{exc}")
                    break
                if not raw:
                    continue
                data = np.frombuffer(raw, dtype=np.float32)
                data = data.reshape(-1, channels) if channels > 1 else data.reshape(-1, 1)
                self._emit(np.ascontiguousarray(data))
        except Exception as exc:  # pragma: no cover
            self._fail(f"系统声音采集异常：{exc}")
            self._opened.set()
        finally:
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
        try:
            extra = _sd.WasapiSettings(loopback=True, auto_convert=True)
        except Exception:
            try:
                extra = _sd.WasapiSettings(loopback=True)
            except Exception as exc:
                self._fail(f"当前 PortAudio 不支持 loopback：{exc}")
                return False

        channels = max(1, int(getattr(self.device, "channels", 2) or 2))
        last_error = ""
        for rate, _ch in self._formats():
            try:
                stream = _sd.InputStream(
                    samplerate=rate,
                    blocksize=BLOCK_FRAMES,
                    device=int(self.device.index),
                    channels=channels,
                    dtype="float32",
                    callback=self._sd_callback,
                    extra_settings=extra,
                )
                stream.start()
            except Exception as exc:
                last_error = str(exc)
                continue
            self.samplerate = rate
            self.channels = channels
            self.backend = "sounddevice-loopback"
            self.target_label = self.device.short_name
            self._sd_stream = stream
            return True

        self._fail(f"无法录该设备的系统声音：{last_error or '未知原因'}")
        return False

    def _sd_callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if self._stop.is_set():
            return
        try:
            self._emit(np.array(indata, dtype=np.float32, copy=True))
        except Exception:
            pass

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
        super().stop()
