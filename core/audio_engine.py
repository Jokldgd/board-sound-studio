"""统一音频引擎：**设备唯一真源** + 播放混音。

为什么合并后要单独做"设备真源"：

* 播放走 ``sounddevice``（PortAudio A），录音走 ``pyaudiowpatch``（PortAudio B），
  **两套实例的设备索引完全不通用**——拿 A 的索引去开 B 的设备会静默开到别的设备。
  所以这里规定：设备一律用**完整原名**做 key，索引由 ``device_enum`` 按用途给出。
* 播放流常驻一个（多音效在回调里混音），切设备只重开一个流；
  录音流是间歇的、由 ``RecorderEngine`` 自己开关，两者互不重启。
* 只使用 **WASAPI 共享模式**，不开独占：这样播放流与录音流、语音软件
  可以同时占用同一块声卡（这是"两套 PortAudio 共存"的前提）。

线程模型：
* 混音在 PortAudio 回调线程，绝不调用 Qt、绝不做磁盘 IO；
* 触发/停止来自 UI 或热键线程，用一把锁保护 voices；
* 错误塞进 ``events`` 队列，界面定时器轮询取走。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import device_enum
from .constants import MAX_VOLUME, SAMPLE_RATE
from .decoder import AudioCache, DecodeError

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

#: 路由模式
ROUTE_VIRTUAL = "virtual"   # 只写虚拟声卡；本机靠 Windows「侦听此设备」
ROUTE_DUAL = "dual"         # 虚拟声卡 + 本地监听（两条独立流，实验性）
ROUTE_LOCAL = "local"       # 只写本地扬声器（不发送给语音软件）

ROUTE_MODES = (ROUTE_VIRTUAL, ROUTE_DUAL, ROUTE_LOCAL)


@dataclass(frozen=True)
class OutputRoute:
    """路由配置：只存**设备名**（索引每次按名现解析，换机/改名都不失效）。"""

    mode: str = ROUTE_VIRTUAL
    primary_name: str = ""       # 模式 A/C 的单设备，或模式 B 的虚拟声卡
    monitor_name: str = ""       # 模式 B 的本地监听设备

    def to_dict(self) -> dict:
        return {
            "mode": self.mode if self.mode in ROUTE_MODES else ROUTE_VIRTUAL,
            "primary_device_name": self.primary_name,
            "monitor_device_name": self.monitor_name,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "OutputRoute":
        data = data or {}
        mode = str(data.get("mode") or ROUTE_VIRTUAL)
        if mode not in ROUTE_MODES:
            mode = ROUTE_VIRTUAL
        return cls(
            mode=mode,
            primary_name=str(data.get("primary_device_name") or ""),
            monitor_name=str(data.get("monitor_device_name") or ""),
        )


@dataclass(frozen=True)
class MixerSettings:
    """内部混音设置：麦克风 + 音效板 → 一路输出。"""

    enabled: bool = False
    mic_name: str = ""          # sounddevice 域的设备名（与录音器的 pyaudiowpatch 名不同域）
    mic_gain: float = 1.0       # 0.0 ~ 2.0
    sfx_gain: float = 1.0       # 0.0 ~ 2.0（= 顶栏"音量"）
    #: 本地也播放**音效**（只含音效、绝不含麦克风）——这样不用开系统侦听也不会听到自己
    local_playback: bool = False

    def to_dict(self) -> dict:
        return {
            "enabled": bool(self.enabled),
            "mic_device_name": self.mic_name,
            "mic_gain": round(max(0.0, min(2.0, self.mic_gain)), 3),
            "sfx_gain": round(max(0.0, min(2.0, self.sfx_gain)), 3),
            "local_playback": bool(self.local_playback),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "MixerSettings":
        data = data or {}
        def clamp(value, default=1.0) -> float:  # noqa: ANN001
            try:
                number = float(value)
            except (TypeError, ValueError):
                return default
            return max(0.0, min(2.0, number))

        return cls(
            enabled=bool(data.get("enabled", False)),
            mic_name=str(data.get("mic_device_name") or ""),
            mic_gain=clamp(data.get("mic_gain", 1.0)),
            sfx_gain=clamp(data.get("sfx_gain", 1.0)),
            local_playback=bool(data.get("local_playback", False)),
        )


@dataclass
class RouteStatus:
    """路由的运行时状态（给顶栏徽章 / 设置分组用）。"""

    mode: str = ROUTE_VIRTUAL
    effective_mode: str = ROUTE_VIRTUAL
    degraded_from: str = ""
    degrade_reason: str = ""
    primary_ok: bool = False
    primary_name: str = ""
    primary_label: str = ""
    primary_error: str = ""
    monitor_ok: bool = False
    monitor_name: str = ""
    monitor_label: str = ""
    monitor_error: str = ""
    samplerate: int = SAMPLE_RATE
    cable_playback: bool = False       # 本机是否有 CABLE Input
    cable_capture: bool = False        # 本机是否有 CABLE Output
    latency_ms: float = 0.0            # 监听路额外延迟（B 模式）
    underrun_frames: int = 0
    overflow_frames: int = 0

    @property
    def degraded(self) -> bool:
        return bool(self.degraded_from)

    def is_virtual_route(self) -> bool:
        return self.effective_mode in (ROUTE_VIRTUAL, ROUTE_DUAL)

    def summary(self) -> str:
        mode = self.effective_mode
        if mode == ROUTE_DUAL:
            return (f"双输出 → {self.primary_label or '虚拟声卡'}"
                    f" + {self.monitor_label or '本地设备'}")
        if mode == ROUTE_VIRTUAL:
            return f"虚拟声卡 → {self.primary_label or 'CABLE Input'}"
        return f"本地 → {self.primary_label or '默认设备'}"


class RingBuffer:
    """单生产者/单消费者环形缓冲（主路回调写，监听路回调读）。

    * 写满时丢弃**最旧**数据（保证监听路的延迟不会无限增长）；
    * 读空时补静音并返回欠载帧数（由调用方累计统计）。
    """

    def __init__(self, capacity_frames: int, channels: int = 2) -> None:
        self._capacity = max(int(capacity_frames), 64)
        self._channels = channels
        self._data = np.zeros((self._capacity, channels), dtype=np.float32)
        self._read = 0
        self._write = 0
        self._fill = 0
        self._lock = threading.Lock()
        self.dropped_frames = 0

    # ------------------------------------------------------------------ 属性
    @property
    def capacity(self) -> int:
        return self._capacity

    def frames_available(self) -> int:
        with self._lock:
            return self._fill

    def clear(self) -> None:
        with self._lock:
            self._read = self._write = self._fill = 0
            self._data.fill(0.0)

    # ------------------------------------------------------------------ 读写
    def write(self, block: np.ndarray) -> int:
        """写入一块；返回因写满而丢弃的**旧帧数**（0 = 正常）。"""
        frames = int(block.shape[0])
        if frames <= 0:
            return 0
        with self._lock:
            dropped = 0
            if frames >= self._capacity:
                block = block[-self._capacity:]
                frames = self._capacity
            overflow = self._fill + frames - self._capacity
            if overflow > 0:
                self._read = (self._read + overflow) % self._capacity
                self._fill -= overflow
                dropped = overflow
                self.dropped_frames += overflow
            first = min(frames, self._capacity - self._write)
            self._data[self._write:self._write + first] = block[:first]
            rest = frames - first
            if rest:
                self._data[:rest] = block[first:]
            self._write = (self._write + frames) % self._capacity
            self._fill += frames
            return dropped

    def read(self, frames: int, out: np.ndarray) -> int:
        """读一块进 out；返回欠载帧数（缺的部分已被填 0）。"""
        frames = int(min(frames, out.shape[0]))
        if frames <= 0:
            return 0
        with self._lock:
            available = min(self._fill, frames)
            first = min(available, self._capacity - self._read)
            if available:
                out[:first] = self._data[self._read:self._read + first]
                rest = available - first
                if rest:
                    out[first:first + rest] = self._data[:rest]
                self._read = (self._read + available) % self._capacity
                self._fill -= available
            missing = frames - available
            if missing:
                out[available:frames] = 0.0
            return missing


class Voice:
    """一路正在播放的声音（含淡入淡出包络）。"""

    __slots__ = (
        "sound_id", "data", "gain", "pos",
        "attack_left", "attack_total", "release_left", "release_total", "done",
    )

    def __init__(self, sound_id: str, data: np.ndarray, gain: float, fade_samples: int) -> None:
        self.sound_id = sound_id
        self.data = data
        self.gain = float(gain)
        self.pos = 0
        self.attack_total = max(1, fade_samples)
        self.attack_left = self.attack_total   # 淡入剩余采样（防爆音）
        self.release_total = max(1, fade_samples)
        self.release_left: int | None = None   # None = 未进入淡出
        self.done = False

    @property
    def level(self) -> float:
        """当前包络 0~1：淡入与淡出两条斜坡取较小值。"""
        value = 1.0
        if self.attack_left > 0:
            value = 1.0 - self.attack_left / self.attack_total
        if self.release_left is not None:
            value = min(value, self.release_left / self.release_total)
        return value if value > 0.0 else 0.0

    def begin_release(self, fade_samples: int | None = None) -> None:
        if self.release_left is None:
            self.release_total = max(1, fade_samples or self.release_total)
            self.release_left = self.release_total


class AudioEngine:
    """设备管理与播放混音。"""

    def __init__(
        self,
        cache: AudioCache | None = None,
        blocksize: int = 1024,
        fade_ms: float = 10.0,
        max_layers_per_sound: int = 3,
    ) -> None:
        self.cache = cache or AudioCache()
        self.blocksize = blocksize
        self.fade_ms = fade_ms
        self.max_layers_per_sound = max_layers_per_sound

        self.events: "queue.Queue[str]" = queue.Queue()
        self.samplerate = SAMPLE_RATE
        self.device_key: str | None = None        # 当前输出设备（完整原名）
        self.device_label = ""                    # 当前输出设备（短名）
        self.running = False
        self.last_error: str | None = None
        self.last_peak = 0.0
        #: 设备缓存（DeviceChoice 列表，kind="output"）
        self.devices: list[device_enum.DeviceChoice] = []
        self._rules: dict = device_enum.merge_rules(None)
        self._show_all = False

        self._lock = threading.RLock()
        self._voices: list[Voice] = []
        self._master = 1.0
        self._exclusive = False
        self._stream = None
        self._scratch = np.zeros((blocksize, 2), dtype=np.float32)
        self._limit_scratch = np.empty((blocksize, 2), dtype=np.float32)

        # ---- 路由（虚拟声卡 / 双输出 / 仅本地）----
        self.route = OutputRoute()
        self._status = RouteStatus()
        self._under_frames = 0
        self._over_frames = 0
        self._diag_last = time.monotonic()
        self._pending_reopen: tuple[float, int, str] | None = None   # (deadline, attempts, reason)
        self._status_errors = 0

        # ---- 内部混音（麦克风 + 音效板）----
        # 麦克风数据只有一条出路：混音 → 主输出（虚拟声卡）。绝不写本地扬声器。
        self._mixer = MixerSettings()
        self._mic_gain = 1.0
        self._mic_input_stream = None                 # T2 降级：独立麦克风输入流
        self._in_ring: RingBuffer | None = None       # T2 降级：麦克风 → 输出回调
        self._mixer_split = False                     # True = 用两条流（非双工）
        self._mic_scratch = np.zeros(blocksize, dtype=np.float32)
        self._mic_pair = np.zeros((blocksize, 2), dtype=np.float32)
        self._mic_peak = 0.0
        self._clip_blocks = 0
        self._clip_notified_at = 0.0
        # 双工回调里要临时用的混音缓冲（预分配，回调内零分配）
        self._duplex_scratch = np.zeros((blocksize, 2), dtype=np.float32)
        # ---- 本地音效监听（只含音效，绝不含麦克风）----
        self._sfx_ring: RingBuffer | None = None
        self._sfx_monitor_stream = None
        self._sfx_copy = np.zeros((blocksize, 2), dtype=np.float32)
        #: 当前主输出是不是虚拟声卡（决定要不要本地补一路音效）
        self._output_is_virtual = False

    # ------------------------------------------------------------ 混音配置
    def mixer(self) -> MixerSettings:
        return self._mixer

    def set_mixer(self, settings: MixerSettings, *, restart: bool = True) -> RouteStatus:
        """开启/关闭混音或换麦克风（内部重开流，返回新的路由状态）。"""
        self._mixer = settings
        self._mic_gain = max(0.0, min(2.0, float(settings.mic_gain)))
        self._master = max(0.0, min(1.0, float(settings.sfx_gain)))
        if not restart:
            return self._status
        status = self.set_route(self.route)
        self._sync_local_playback()
        return status

    # ------------------------------------------------------------ 本地音效监听
    def set_local_playback(self, enabled: bool) -> bool:
        """本地补一路**只含音效**的输出（不含麦克风，所以不会听到自己）。

        返回最终是否在跑。
        """
        from dataclasses import replace

        self._mixer = replace(self._mixer, local_playback=bool(enabled))
        return self._sync_local_playback()

    def local_playback_active(self) -> bool:
        return self._sfx_monitor_stream is not None

    def _sync_local_playback(self) -> bool:
        """按当前状态开/关本地音效路；只有输出到虚拟声卡时才需要（仅本地模式本身就在放）。"""
        if self._mixer.local_playback and self.running and not self._output_is_virtual:
            self._notify("当前是「仅本地」模式，音效本来就在本地播放，无需再开这一路")
        want = bool(self._mixer.local_playback and self.running and self._output_is_virtual)
        if not want:
            self._close_local_playback()
            return False
        if self._sfx_monitor_stream is not None:
            return True
        return self._open_local_playback()

    def _local_device_candidates(self) -> list:  # noqa: ANN201
        """本地监听候选设备：默认输出优先，其次所有物理输出（逐个试，避免某块被独占就放弃）。"""
        candidates: list = []
        default = device_enum.default_output(self._rules)
        if default is not None and not device_enum.is_virtual_name(default.name):
            candidates.append(default)
        for choice in device_enum.physical_outputs(self._rules):
            if device_enum.is_virtual_name(choice.name):
                continue
            if default is not None and choice.name == default.name:
                continue
            candidates.append(choice)
        return candidates

    def _pick_local_device(self):  # noqa: ANN201
        candidates = self._local_device_candidates()
        return candidates[0] if candidates else None

    def _open_local_playback(self) -> bool:
        if sd is None:
            return False
        candidates = self._local_device_candidates()
        if not candidates:
            self._notify("没有可用的本地扬声器，本地音效监听未开启")
            return False
        ring = RingBuffer(capacity_frames=self.blocksize * 8)
        self._sfx_ring = ring
        last_error = ""
        opened = None
        for choice in candidates:
            rates: list[int] = []
            for rate in (self.samplerate, choice.samplerate, 48000, 44100):
                if rate and int(rate) not in rates:
                    rates.append(int(rate))
            for rate in rates:
                stream = None
                try:
                    stream = sd.OutputStream(
                        samplerate=rate,
                        blocksize=self.blocksize,
                        device=choice.index,
                        channels=2,
                        dtype="float32",
                        callback=self._local_playback_callback,
                    )
                    stream.start()
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
                    continue
                opened = (choice, stream)
                break
            if opened is not None:
                break
        if opened is None:
            self._sfx_ring = None
            self._notify(f"本地音效监听打不开（没有可用扬声器）：{last_error}")
            return False
        choice, stream = opened
        self._sfx_monitor_stream = stream
        self._notify(f"本地音效监听已开启：{choice.short_name}（只放音效，不含麦克风）")
        return True

    def _close_local_playback(self) -> None:
        stream, self._sfx_monitor_stream = self._sfx_monitor_stream, None
        self._sfx_ring = None
        if stream is not None:
            for action in (stream.stop, stream.close):
                try:
                    action()
                except Exception:
                    pass

    def _local_playback_callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        """本地路：只搬运音效（不含麦克风的 program），欠载补静音。"""
        try:
            ring = self._sfx_ring
            if ring is None:
                outdata.fill(0.0)
                return
            missing = ring.read(frames, outdata)
            if missing:
                outdata[frames - missing:] = 0.0
        except Exception:  # noqa: BLE001 # pragma: no cover
            outdata.fill(0.0)

    def _feed_local_playback(self, sfx: np.ndarray, frames: int) -> None:
        """把**纯音效**写进本地监听环（必须在加麦克风之前调用）。"""
        ring = self._sfx_ring
        if ring is None or frames <= 0:
            return
        if self._sfx_copy.shape[0] < frames:
            self._sfx_copy = np.zeros((frames, 2), dtype=np.float32)
        copy = self._sfx_copy[:frames]
        np.copyto(copy, sfx[:frames])
        ring.write(copy)

    def set_mic_gain(self, value: float) -> None:
        """只改标量，不重开流（回调里读它）。"""
        self._mic_gain = max(0.0, min(2.0, float(value)))

    def set_sfx_gain(self, value: float) -> None:
        self._master = max(0.0, min(1.0, float(value)))

    def mic_level(self) -> float:
        return self._mic_peak

    def clip_status(self) -> tuple[bool, float]:
        """(是否持续削波, 最近峰值)。"""
        return self._clip_blocks >= 20, self.last_peak

    @property
    def mixer_active(self) -> bool:
        return bool(self._mixer.enabled and self._mixer.mic_name)

    def _track_levels(self, block: np.ndarray, frames: int) -> None:
        """削波统计（只比大小，不分配）。"""
        if not frames:
            return
        peak = float(np.max(np.abs(block)))
        self.last_peak = peak
        if peak >= 0.999:
            self._clip_blocks += 1
            if self._clip_blocks == 20:
                now = time.monotonic()
                if now - self._clip_notified_at > 5.0:
                    self._clip_notified_at = now
                    self._notify("音量过大：混音已限幅，建议降低麦克风或音效音量")
        else:
            self._clip_blocks = 0

    # ------------------------------------------------------------ 设备真源
    def configure_rules(self, rules: dict, show_all: bool = False) -> None:
        """设置清洗/分组规则（来自 config），并让设备缓存失效。"""
        self._rules = rules
        self._show_all = bool(show_all)

    def refresh_devices(self) -> list[device_enum.DeviceChoice]:
        """重新枚举输出设备（播放固定用 sounddevice 的索引）。"""
        self.devices = device_enum.list_devices("output", self._rules, show_all=self._show_all)
        return self.devices

    def default_device(self) -> device_enum.DeviceChoice | None:
        for choice in self.devices:
            if choice.is_default:
                return choice
        return self.devices[0] if self.devices else None

    def resolve(self, key: str | None) -> device_enum.DeviceChoice | None:
        """按完整原名找设备（支持模糊匹配，设备改名也不至于失效）。"""
        return device_enum.find_choice(self.devices, key)

    # ------------------------------------------------------------ 流生命周期
    def available(self) -> bool:
        return sd is not None

    def open(self, device_key: str | None = None) -> bool:
        """兼容旧调用：等价于切到「仅本地扬声器」路由。"""
        return self.set_route(
            OutputRoute(mode=ROUTE_LOCAL, primary_name=str(device_key or ""))
        ).primary_ok

    # ------------------------------------------------------------ 路由
    def route_status(self) -> RouteStatus:
        self._sync_counters()
        return self._status

    def set_route(self, route: OutputRoute, *, restart: bool = True) -> RouteStatus:
        """设置路由并开流。**失败会按 A → C 的优先次序降级，绝不抛异常。**

        返回的 ``RouteStatus`` 里 ``effective_mode`` 是真正生效的模式，
        ``degraded_from`` 非空表示发生过降级（UI 据此显示橙色警告）。
        """
        if sd is None:
            self._notify("未安装 sounddevice，无法播放音频（pip install sounddevice）")
            return self._status

        self.close()
        # 双输出模式已弃置：旧配置里的 dual 一律按「虚拟声卡」处理
        mode = route.mode if route.mode in (ROUTE_VIRTUAL, ROUTE_LOCAL) else ROUTE_VIRTUAL
        self.route = OutputRoute(mode, route.primary_name, route.monitor_name)
        if restart:
            self.refresh_devices()

        cable = device_enum.detect_vb_cable(self._rules, show_all=True)
        status = RouteStatus(
            mode=mode,
            effective_mode=mode,
            cable_playback=cable.playback is not None,
            cable_capture=cable.capture is not None,
        )
        self._status = status

        if mode == ROUTE_VIRTUAL:
            primary = self._resolve_named(route.primary_name) or cable.playback
            if primary is None:
                # 没装虚拟声卡：回退到系统默认喇叭（能听到，但远端收不到）
                fallback = device_enum.default_output(self._rules) or self.default_device()
                status.degraded_from = ROUTE_VIRTUAL
                status.degrade_reason = "未检测到 VB-CABLE，已回退到系统默认设备"
                self._notify(
                    "未检测到 VB-CABLE：已回退到系统默认设备（远端暂时听不到），"
                    "装好后重新选择「虚拟声卡」即可"
                )
                return self._apply_single(fallback, ROUTE_LOCAL, status)
            return self._apply_single(primary, ROUTE_VIRTUAL, status)

        primary = self._resolve_named(route.primary_name) or device_enum.default_output(
            self._rules
        ) or self.default_device()
        return self._apply_single(primary, ROUTE_LOCAL, status)

    # ---- 路由内部：解析 ----
    def _resolve_named(self, name: str | None) -> device_enum.DeviceChoice | None:
        if not name:
            return None
        if not self.devices:
            self.refresh_devices()
        found = device_enum.find_choice(self.devices, name)
        if found is not None:
            return found
        return device_enum.resolve_by_name(name, "output", self._rules, show_all=True)

    def _default_physical(self) -> device_enum.DeviceChoice | None:
        physical = device_enum.physical_outputs(self._rules)
        for choice in physical:
            if choice.is_default:
                return choice
        return physical[0] if physical else None

    # ---- 路由内部：开流 ----
    def _open_stream(self, choice, callback, rate_hint: int | None = None):  # noqa: ANN001
        """按采样率候选开一条流。返回 (stream, rate, error)。"""
        rates: list[int] = []
        for rate in (rate_hint, choice.samplerate, 48000, 44100):
            if rate and int(rate) not in rates:
                rates.append(int(rate))
        last_error = ""
        for rate in rates:
            stream = None
            try:
                stream = sd.OutputStream(
                    samplerate=rate,
                    blocksize=self.blocksize,
                    device=choice.index,
                    channels=2,
                    dtype="float32",
                    callback=callback,
                )
                stream.start()
                return stream, rate, ""
            except Exception as exc:
                last_error = str(exc)
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
        return None, 0, last_error

    def _install_primary(self, stream, rate: int, choice) -> None:  # noqa: ANN001
        self._stream = stream
        self.samplerate = rate
        self.device_key = choice.key
        self.device_label = choice.short_name
        self.running = True
        self._status_errors = 0
        self._pending_reopen = None
        self._output_is_virtual = device_enum.is_virtual_name(choice.name)
        self.cache.drop_rate(rate)

    def _apply_single(self, choice, effective_mode: str, status: RouteStatus) -> RouteStatus:  # noqa: ANN001
        status.effective_mode = effective_mode
        if choice is None:
            status.primary_ok = False
            status.primary_error = "没有可用的输出设备"
            status.primary_label = "未找到设备"
            self._notify("系统里没有可用的音频输出设备")
            return status

        status.primary_label = choice.short_name

        # 混音开启：只有输出到**虚拟声卡**时才采集麦克风。
        # 输出到物理扬声器（仅本地模式）时绝不采集/混入麦克风，
        # 否则就是"本地能听到自己说话"（回音）——麦克风永远不进本地设备。
        mic = None
        if self._mixer.enabled:
            target_is_cable = (
                effective_mode == ROUTE_VIRTUAL
                and device_enum.is_virtual_name(choice.name)
            )
            if not target_is_cable:
                self._notify("当前输出不是虚拟声卡：麦克风不会送回本地扬声器（只放音效）")
            else:
                mic = device_enum.resolve_mixer_mic(self._mixer.mic_name, self._rules)
                if mic is None and self._mixer.mic_name:
                    status.degraded_from = effective_mode
                    status.degrade_reason = f"找不到麦克风「{self._mixer.mic_name}」"
                    self._notify(f"找不到麦克风「{self._mixer.mic_name}」，已回退为仅音效")
                elif mic is not None:
                    if self._open_duplex(choice, mic, status):
                        return status
                    if self._open_mixer_split(choice, mic, status):
                        return status
                    status.degraded_from = effective_mode
                    status.degrade_reason = "麦克风开流失败，已回退为仅音效"
                    self._notify("麦克风开流失败（可能被其他程序独占），已回退为仅音效")

        stream, rate, error = self._open_stream(choice, self._primary_callback)
        if stream is None:
            status.primary_ok = False
            status.primary_error = error or "开流失败"
            self._notify(f"无法打开输出设备「{choice.short_name}」：{error}")
            self.running = False
            return status

        self._install_primary(stream, rate, choice)
        status.primary_ok = True
        status.samplerate = rate
        status.primary_name = choice.key
        self._sync_local_playback()
        return status

    # ---- 混音：双工（首选）----
    def _open_duplex(self, output, mic, status: RouteStatus) -> bool:  # noqa: ANN001
        if sd is None:
            return False
        rates: list[int] = []
        for rate in (output.samplerate, 48000, 44100):
            if rate and int(rate) not in rates:
                rates.append(int(rate))
        last_error = ""
        for rate in rates:
            for in_channels in (1, 2):
                stream = None
                try:
                    stream = sd.Stream(
                        samplerate=rate,
                        blocksize=self.blocksize,
                        device=(mic.index, output.index),
                        channels=(in_channels, 2),
                        dtype="float32",
                        callback=self._duplex_callback,
                    )
                    stream.start()
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
                    continue
                self._stream = stream
                self.samplerate = rate
                self.device_key = output.key
                self.device_label = output.short_name
                self.running = True
                self._status_errors = 0
                self._pending_reopen = None
                self._mixer_split = False
                self._output_is_virtual = device_enum.is_virtual_name(output.name)
                self.cache.drop_rate(rate)
                status.primary_ok = True
                status.primary_name = output.key
                status.samplerate = rate
                self._sync_local_playback()
                return True
        if last_error:
            self._notify(f"双工流打不开，改用两条流：{last_error}")
        return False

    # ---- 混音：两条流（降级）----
    def _open_mixer_split(self, output, mic, status: RouteStatus) -> bool:  # noqa: ANN001
        if sd is None:
            return False
        rates: list[int] = []
        for rate in (output.samplerate, 48000, 44100):
            if rate and int(rate) not in rates:
                rates.append(int(rate))
        last_error = ""
        for rate in rates:
            out_stream = in_stream = None
            try:
                mic_ring = RingBuffer(capacity_frames=self.blocksize * 8)
                in_stream = sd.InputStream(
                    samplerate=rate, blocksize=self.blocksize, device=mic.index,
                    channels=1, dtype="float32", callback=self._mixer_input_callback,
                )
                self._in_ring = mic_ring
                out_stream = sd.OutputStream(
                    samplerate=rate, blocksize=self.blocksize, device=output.index,
                    channels=2, dtype="float32", callback=self._mixer_output_callback,
                )
                in_stream.start()
                out_stream.start()
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                for stream in (in_stream, out_stream):
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
                self._in_ring = None
                continue

            self._mic_input_stream = in_stream
            self._stream = out_stream
            self.samplerate = rate
            self.device_key = output.key
            self.device_label = output.short_name
            self.running = True
            self._status_errors = 0
            self._pending_reopen = None
            self._mixer_split = True
            self._output_is_virtual = device_enum.is_virtual_name(output.name)
            self.cache.drop_rate(rate)
            status.primary_ok = True
            status.primary_name = output.key
            status.samplerate = rate
            self._sync_local_playback()
            self._notify("混音在使用两条独立流（可能有轻微延迟差）")
            return True
        if last_error:
            self._notify(f"两条流方案也失败：{last_error}")
        return False


    # ---- 路由内部：清理 / 统计 ----

    def _sync_counters(self) -> None:
        self._status.underrun_frames = self._under_frames
        self._status.overflow_frames = self._over_frames

    def tick(self) -> None:
        """由 UI 定时器调用（约 8Hz）：处理失败重开（1s 退避）与统计汇总。"""
        now = time.monotonic()
        pending = self._pending_reopen
        if pending is not None:
            deadline, attempts, reason = pending
            if now >= deadline:
                self._pending_reopen = None
                status = self.set_route(self.route)
                if not status.primary_ok:
                    if attempts + 1 < 2:
                        self._pending_reopen = (now + 1.0, attempts + 1, reason)
                    else:
                        self._notify(f"输出设备恢复失败：{reason}")

        if now - self._diag_last >= 5.0:
            self._diag_last = now
            self._sync_counters()
            if self._under_frames or self._over_frames:
                # 只写日志级别的统计，不逐回调打扰 UI
                under, over = self._under_frames, self._over_frames
                self._under_frames = self._over_frames = 0
                self._notify(
                    f"双输出同步统计：欠载 {under} 帧 / 丢弃 {over} 帧（"
                    f"{'轻微' if under + over < self.samplerate // 4 else '明显'}不同步）"
                )

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            self._voices.clear()
        self.running = False
        self._output_is_virtual = False
        self._close_local_playback()          # 本地音效监听（只含音效）
        # 关掉降级方案里的独立麦克风输入流
        mic_stream, self._mic_input_stream = self._mic_input_stream, None
        self._in_ring = None
        self._mixer_split = False
        self._mic_peak = 0.0
        for extra in (mic_stream,):
            if extra is None:
                continue
            for action in (extra.stop, extra.close):
                try:
                    action()
                except Exception:
                    pass
        if stream is not None:
            for action in (stream.stop, stream.close):
                try:
                    action()
                except Exception:
                    pass
        self._status.primary_ok = False
        self._status.monitor_ok = False
        self._pending_reopen = None
        self._status_errors = 0

    #: 语义化别名（退出清理时调用，和 close 等价、幂等）
    def stop(self) -> None:
        self.close()

    def shutdown(self) -> None:
        self.close()

    # ------------------------------------------------------------ 播放控制
    def set_master_volume(self, value: float) -> None:
        with self._lock:
            self._master = max(0.0, min(1.0, float(value)))

    def set_exclusive(self, enabled: bool) -> None:
        with self._lock:
            self._exclusive = bool(enabled)

    @property
    def exclusive(self) -> bool:
        return self._exclusive

    def trigger(self, clip_id: str, path: str | Path, gain: float = 1.0) -> bool:
        """播放一条音效。返回 False 表示没播出去（原因进 events）。"""
        if not self.running:
            self._notify("音频引擎未运行：请检查顶栏输出设备")
            return False
        try:
            data = self.cache.get(path, self.samplerate)
        except DecodeError as exc:
            self._notify(str(exc))
            return False
        except Exception as exc:
            self._notify(f"解码失败：{exc}")
            return False

        fade = self._fade_samples
        gain = max(0.0, min(MAX_VOLUME, float(gain)))
        with self._lock:
            if self._exclusive:
                for voice in self._voices:
                    voice.begin_release(fade)
            else:
                same = [v for v in self._voices if v.sound_id == clip_id]
                if len(same) >= self.max_layers_per_sound:
                    same[0].begin_release(fade)
            self._voices.append(Voice(clip_id, data, gain, fade))
        return True

    def release(self, clip_id: str) -> None:
        """松开按键：淡出停止（"按住播放"用）。"""
        fade = self._fade_samples
        with self._lock:
            for voice in self._voices:
                if voice.sound_id == clip_id:
                    voice.begin_release(fade)

    def stop_all(self) -> None:
        fade = self._fade_samples
        with self._lock:
            for voice in self._voices:
                voice.begin_release(fade)

    def active_ids(self) -> list[str]:
        with self._lock:
            result: list[str] = []
            for voice in self._voices:
                if voice.done or voice.level <= 0.001:
                    continue
                if voice.sound_id not in result:
                    result.append(voice.sound_id)
            return result

    def is_playing(self, clip_id: str) -> bool:
        with self._lock:
            return any(v.sound_id == clip_id and not v.done for v in self._voices)

    @property
    def _fade_samples(self) -> int:
        return max(1, int(self.samplerate * self.fade_ms / 1000.0))

    # ------------------------------------------------------------ 混音核心
    def _primary_callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        """主路 PortAudio 回调：混音**只在这里算一次**，再复制给监听路。

        异常必须吞掉，否则 PortAudio 会直接关掉这条流。
        """
        try:
            if status:
                self._status_errors += 1
                if self._status_errors == 3 and self._pending_reopen is None:
                    self._pending_reopen = (time.monotonic() + 1.0, 0, str(status))
                    self._notify(f"输出流状态异常（{status}），1 秒后尝试重开")
            else:
                self._status_errors = 0

            if frames > self._scratch.shape[0]:
                self._scratch = np.zeros((frames, 2), dtype=np.float32)
            buffer = self._scratch[:frames]
            self._mix_into(buffer, frames)
            self._limit(buffer, frames)          # 纯音效路径也要软限幅
            self._feed_local_playback(buffer, frames)   # 本地只放音效
            outdata[:] = buffer
            self._track_levels(buffer, frames)

        except Exception as exc:  # pragma: no cover
            outdata.fill(0)
            self._notify(f"混音异常：{exc}")

    #: 兼容旧名字（早期版本叫 ``_callback``）
    _callback = _primary_callback

    # ------------------------------------------------------------ 混音回调
    def _mix_program(self, buffer: np.ndarray, frames: int,
                     mic_pair: np.ndarray | None) -> None:  # noqa: ANN001
        """音频线程里的混音点：音效(+麦克风) → **软限幅** → buffer。零分配。

        麦克风数据只在这里参与相加，最终只写 buffer（→ 主输出/虚拟声卡），
        没有任何分支把它写到本地扬声器。本地音效监听取的是**加麦克风之前**的
        那一份（``_feed_local_playback``），所以本地永远听不到自己说话。
        """
        self._mix_into(buffer, frames)          # 音效（已乘音效总音量，未限幅）
        self._feed_local_playback(buffer, frames)   # ← 必须在加麦克风之前
        if mic_pair is not None:
            np.add(buffer, mic_pair, out=buffer)
        self._limit(buffer, frames)             # 唯一一次限幅，软限幅
        self._track_levels(buffer, frames)

    def _duplex_callback(self, indata, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        """双工回调：读麦克风 + 混音效 + 写虚拟声卡，全部在同一个时钟里。"""
        try:
            if status:
                self._status_errors += 1
                if self._status_errors == 3 and self._pending_reopen is None:
                    self._pending_reopen = (time.monotonic() + 1.0, 0, str(status))
                    self._notify(f"混音流状态异常（{status}），1 秒后尝试重开")
            else:
                self._status_errors = 0

            mic_pair = None
            mic_mono = None
            if indata is not None and indata.size:
                if frames > self._mic_scratch.shape[0]:
                    self._mic_scratch = np.zeros(frames, dtype=np.float32)
                    self._mic_pair = np.zeros((frames, 2), dtype=np.float32)
                mic_mono = self._mic_scratch[:frames]
                source = indata[:, 0] if indata.shape[1] == 1 else indata.mean(axis=1)
                np.multiply(source, self._mic_gain, out=mic_mono)
                self._mic_peak = float(np.max(np.abs(mic_mono)))
                mic_pair = self._mic_pair[:frames]
                mic_pair[:, 0] = mic_mono
                mic_pair[:, 1] = mic_mono

            if frames > self._duplex_scratch.shape[0]:
                self._duplex_scratch = np.zeros((frames, 2), dtype=np.float32)
            buffer = self._duplex_scratch[:frames]
            self._mix_program(buffer, frames, mic_pair)
            outdata[:] = buffer

            # 注意：麦克风数据**只**进 program（→ 虚拟声卡），这里没有任何
            # 写本地扬声器的分支（耳返功能已彻底移除）。
        except Exception as exc:  # pragma: no cover
            outdata.fill(0)
            self._notify(f"混音异常：{exc}")

    def _mixer_input_callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        """降级方案：独立麦克风输入流 → 环形缓冲（不在这里混音）。"""
        try:
            ring = self._in_ring
            if ring is None or not frames:
                return
            if frames > self._mic_scratch.shape[0]:
                self._mic_scratch = np.zeros(frames, dtype=np.float32)
                self._mic_pair = np.zeros((frames, 2), dtype=np.float32)
            mic_mono = self._mic_scratch[:frames]
            source = indata[:, 0] if indata.shape[1] == 1 else indata.mean(axis=1)
            np.multiply(source, self._mic_gain, out=mic_mono)
            self._mic_peak = float(np.max(np.abs(mic_mono)))
            pair = self._mic_pair[:frames]
            pair[:, 0] = mic_mono
            pair[:, 1] = mic_mono
            ring.write(pair)
        except Exception as exc:  # pragma: no cover
            self._notify(f"麦克风输入异常：{exc}")

    def _mixer_output_callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        """降级方案：从环形缓冲取麦克风 + 混音效 → 输出。"""
        try:
            if status:
                self._status_errors += 1
            if frames > self._duplex_scratch.shape[0]:
                self._duplex_scratch = np.zeros((frames, 2), dtype=np.float32)
            buffer = self._duplex_scratch[:frames]
            mic_pair = None
            ring = self._in_ring
            if ring is not None:
                # 用预分配缓冲，回调里不再 np.zeros（需求要求零分配）
                if frames > self._mic_pair.shape[0]:
                    self._mic_pair = np.zeros((frames, 2), dtype=np.float32)
                    self._mic_scratch = np.zeros(frames, dtype=np.float32)
                mic_pair = self._mic_pair[:frames]
                mic_pair.fill(0.0)
                missing = ring.read(frames, mic_pair)
                if missing:
                    self._under_frames += missing
            self._mix_program(buffer, frames, mic_pair)
            outdata[:] = buffer

        except Exception as exc:  # pragma: no cover
            outdata.fill(0)
            self._notify(f"混音异常：{exc}")


    def _mix_into(self, out: np.ndarray, frames: int) -> None:
        """把所有音效混进 out（**不在这里限幅**：限幅留给混音出口统一做）。"""
        out[:frames] = 0.0
        finished: list[Voice] = []
        with self._lock:
            master = self._master
            for voice in self._voices:
                self._render(voice, out, frames)
                if voice.done:
                    finished.append(voice)
            for voice in finished:
                try:
                    self._voices.remove(voice)
                except ValueError:
                    pass

        if master != 1.0:
            out[:frames] *= master
        self.last_peak = float(np.max(np.abs(out[:frames]))) if frames else 0.0

    def _limit(self, block: np.ndarray, frames: int) -> None:
        """软限幅（peak limiter）：``x / max(1, |x|)``。

        相比 ``np.clip`` 直接截断成方波（"电音"来源），这个算法只在超过 1.0 时
        按比例压回来，波形保持连续；1.0 以内完全不动。全程用预分配缓冲，
        回调里 **零分配**。
        """
        if frames <= 0:
            return
        view = block[:frames]
        if view.shape[0] > self._limit_scratch.shape[0]:
            self._limit_scratch = np.empty((view.shape[0], view.shape[1]), dtype=np.float32)
        head = self._limit_scratch[:view.shape[0]]
        if head.shape != view.shape:            # 声道数变化时补一块（罕见）
            head = np.empty_like(view)
        np.abs(view, out=head)
        np.maximum(head, 1.0, out=head)
        np.divide(view, head, out=view)

    def _render(self, voice: Voice, out: np.ndarray, frames: int) -> None:
        data = voice.data
        total = data.shape[0]
        written = 0
        while written < frames:
            if voice.release_left is not None and voice.release_left <= 0:
                voice.done = True
                return
            if voice.pos >= total:
                voice.done = True
                return

            span = min(frames - written, total - voice.pos)
            if voice.attack_left > 0:
                span = min(span, voice.attack_left)
            if voice.release_left is not None:
                span = min(span, voice.release_left)
            span = int(span)
            if span <= 0:
                break

            start_level = voice.level
            if voice.attack_left > 0:
                voice.attack_left -= span
            if voice.release_left is not None:
                voice.release_left -= span
            end_level = voice.level

            segment = data[voice.pos:voice.pos + span]
            voice.pos += span

            if start_level != end_level:
                envelope = np.linspace(start_level, end_level, span,
                                       endpoint=False, dtype=np.float32)
                out[written:written + span] += segment * (voice.gain * envelope)[:, None]
            elif start_level != 1.0:
                out[written:written + span] += segment * np.float32(voice.gain * start_level)
            else:
                out[written:written + span] += segment * np.float32(voice.gain)
            written += span

    # ------------------------------------------------------------ 状态/错误
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

    def describe(self) -> str:
        if not self.running:
            reason = self._status.degrade_reason or self._status.primary_error
            return reason or "播放未运行"
        text = self._status.summary()
        if self._status.degraded:
            text += "（已降级）"
        return f"{text} · {self.samplerate}Hz"

    # ------------------------------------------------------------ 路由自检
    TEST_TONE_SECONDS = 0.5
    TEST_TONE_FREQ = 440.0
    TEST_TONE_GAIN = 0.3
    TEST_TONE_FADE = 0.05          # 50ms 线性淡入/淡出，消掉"啪"的截断声

    def play_test_tone(self, seconds: float | None = None,
                       freq: float | None = None) -> bool:
        """播放一段柔和测试音（0.5s / 440Hz / 音量 0.3 / 50ms 淡入淡出）。

        走**当前路由**：混音开着时会和麦克风一起送出去，正好验证整条链路。
        """
        if not self.running:
            self._notify("播放未运行，无法测试路由（请先选好输出）")
            return False
        rate = max(1, int(self.samplerate))
        length = float(seconds if seconds is not None else self.TEST_TONE_SECONDS)
        tone = float(freq if freq is not None else self.TEST_TONE_FREQ)
        count = max(1, int(rate * max(0.1, length)))

        t = np.arange(count, dtype=np.float32) / float(rate)
        wave = (self.TEST_TONE_GAIN * np.sin(2.0 * np.pi * tone * t)).astype(np.float32)

        fade = min(int(rate * self.TEST_TONE_FADE), count // 2)
        if fade > 0:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            wave[:fade] *= ramp            # 线性淡入
            wave[-fade:] *= ramp[::-1]     # 线性淡出

        data = np.repeat(wave[:, None], 2, axis=1).astype(np.float32)
        with self._lock:
            if self._exclusive:
                for voice in self._voices:
                    voice.begin_release(self._fade_samples)
            self._voices.append(Voice("__test_tone__", data, 1.0, self._fade_samples))
        return True

    # ------------------------------------------------------------ 冲突校验
    def conflicts_with(self, record_device_key: str | None) -> str:
        """录音目标与播放设备的关系说明（空串 = 无冲突）。

        不是错误，只是提示：录自己正在播的声音往往会重复/回授。
        """
        if not record_device_key or not self.device_key:
            return ""
        if record_device_key == self.device_key:
            if self._status.effective_mode == ROUTE_DUAL:
                return "录音目标与播放设备相同：会把本程序播放的声音也录进去（双输出）"
            return "录音目标与播放设备相同：会把本程序播放的声音也录进去"
        return ""
