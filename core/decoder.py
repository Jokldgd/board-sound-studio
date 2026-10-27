"""音频解码、重采样与内存缓存。

解码链（按顺序尝试，前一个不行才退到下一个）：

1. **soundfile**（libsndfile）：wav / flac / ogg 稳定支持，较新的
   libsndfile 也原生支持 mp3。零外部依赖，优先使用。
2. **miniaudio**：内置 dr_libs 解码器，mp3/flac/wav/ogg 全覆盖，
   纯本地 wheel，不需要用户装 ffmpeg。
3. **pydub**（需要系统里有 ffmpeg）：最后兜底，覆盖极端格式。

所有解码结果统一成 ``float32``、双声道、目标采样率的 numpy 数组，
形状 ``(frames, 2)``，取值大致在 [-1, 1]。

重采样用线性插值：无外部依赖、音效场景足够，代价是极高频略有损失。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

try:  # 主解码器
    import soundfile as sf
except Exception:  # pragma: no cover - 环境缺库时退化
    sf = None  # type: ignore[assignment]

try:  # 第二解码器（可选依赖）
    import miniaudio
except Exception:  # pragma: no cover
    miniaudio = None  # type: ignore[assignment]


class DecodeError(Exception):
    """解码失败，消息直接展示给用户。"""


# ---------------------------------------------------------------- 基础工具
def to_stereo(data: np.ndarray) -> np.ndarray:
    """把任意声道数的数组规范成 (frames, 2) 的 float32。"""
    if data.ndim == 1:
        data = data[:, None]
    if data.size == 0 or data.shape[0] == 0:
        raise DecodeError("音频内容为空")
    channels = data.shape[1]
    if channels == 1:
        data = np.repeat(data, 2, axis=1)
    elif channels > 2:
        data = data[:, :2]
    if data.dtype != np.float32:
        data = data.astype(np.float32)
    return np.ascontiguousarray(data)


def resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """线性插值重采样，(frames, 2) -> (new_frames, 2)。"""
    if src_rate == dst_rate or src_rate <= 0 or dst_rate <= 0:
        return data
    src_frames = data.shape[0]
    dst_frames = max(1, int(round(src_frames * dst_rate / src_rate)))
    if src_frames < 2:
        return np.repeat(data[:1], dst_frames, axis=0)

    src_x = np.arange(src_frames, dtype=np.float64)
    dst_x = np.linspace(0.0, src_frames - 1, dst_frames, dtype=np.float64)
    out = np.empty((dst_frames, data.shape[1]), dtype=np.float32)
    for channel in range(data.shape[1]):
        out[:, channel] = np.interp(dst_x, src_x, data[:, channel]).astype(np.float32)
    return out


# ---------------------------------------------------------------- 各解码后端
def _decode_soundfile(path: Path, target_rate: int) -> np.ndarray:
    if sf is None:
        raise DecodeError("soundfile 未安装")
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return resample(to_stereo(data), int(rate), target_rate)


def _decode_miniaudio(path: Path, target_rate: int) -> np.ndarray:
    if miniaudio is None:
        raise DecodeError("miniaudio 未安装")
    # 直接让 miniaudio 输出目标采样率的 float32 双声道，省一次重采样
    decoded = miniaudio.decode_file(
        str(path),
        output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=2,
        sample_rate=target_rate,
    )
    samples = np.asarray(decoded.samples, dtype=np.float32)
    if samples.size == 0:
        raise DecodeError("音频内容为空")
    return to_stereo(samples.reshape(-1, 2))


def _decode_pydub(path: Path, target_rate: int) -> np.ndarray:
    try:
        from pydub import AudioSegment
    except Exception as exc:  # pragma: no cover
        raise DecodeError("pydub 未安装") from exc
    try:
        segment = AudioSegment.from_file(str(path))
    except Exception as exc:
        raise DecodeError(f"pydub 无法解析（可能缺 ffmpeg）：{exc}") from exc
    segment = segment.set_channels(2).set_frame_rate(target_rate)
    raw = np.frombuffer(segment.raw_data, dtype=np.int16).astype(np.float32) / 32768.0
    return to_stereo(raw.reshape(-1, 2))


_BACKENDS = (
    ("soundfile", _decode_soundfile),
    ("miniaudio", _decode_miniaudio),
    ("pydub", _decode_pydub),
)


def decode(path: str | Path, target_rate: int) -> np.ndarray:
    """把任意受支持格式解码成目标采样率的立体声 float32 数组。

    失败时抛 DecodeError，消息里带上每个后端的具体原因，方便用户排查。
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise DecodeError(f"文件不存在：{file_path}")

    problems: list[str] = []
    for name, backend in _BACKENDS:
        try:
            data = backend(file_path, target_rate)
            if data.shape[0] == 0:
                raise DecodeError("音频内容为空")
            return data
        except DecodeError as exc:
            problems.append(f"{name}: {exc}")
        except Exception as exc:  # 后端内部异常一律视为该后端不可用
            problems.append(f"{name}: {exc}")

    detail = "；".join(problems)
    raise DecodeError(
        f"无法解码 {file_path.name}。尝试过的解码器：[{detail}]。"
        "如果是 mp3，请确认已安装 miniaudio（pip install miniaudio）"
        "或系统内可用的 ffmpeg。"
    )


def probe_duration(path: str | Path) -> float | None:
    """尽量廉价地拿到音频时长（秒），拿不到返回 None。"""
    file_path = Path(path)
    if sf is not None:
        try:
            info = sf.info(str(file_path))
            if info.samplerate:
                return float(info.frames) / float(info.samplerate)
        except Exception:
            pass
    if miniaudio is not None:
        try:
            info = miniaudio.get_file_info(str(file_path))
            if info.sample_rate:
                return float(info.num_frames) / float(info.sample_rate)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------- 缓存
class AudioCache:
    """按 (文件, 采样率) 缓存解码结果，用字节预算做 LRU 淘汰。

    为什么不全部常驻内存：100 条 10 秒、48kHz、立体声 float32
    约 384MB，全留着会吃爆内存。
    """

    def __init__(self, budget_bytes: int = 256 * 1024 * 1024) -> None:
        self.budget_bytes = budget_bytes
        self._items: "OrderedDict[tuple[str, int], np.ndarray]" = OrderedDict()
        self._lock = threading.RLock()
        self._bytes = 0

    # -- 查询
    def peek(self, path: str | Path, rate: int) -> np.ndarray | None:
        key = (str(path), int(rate))
        with self._lock:
            data = self._items.get(key)
            if data is not None:
                self._items.move_to_end(key)  # 标记为最近使用
            return data

    def get(self, path: str | Path, rate: int) -> np.ndarray:
        """取缓存，未命中则解码后存入。"""
        cached = self.peek(path, rate)
        if cached is not None:
            return cached
        data = decode(path, rate)  # 解码放在锁外，避免阻塞其他线程
        with self._lock:
            key = (str(path), int(rate))
            if key in self._items:
                return self._items[key]
            self._items[key] = data
            self._bytes += data.nbytes
            self._evict()
            return data

    def preload(self, path: str | Path, rate: int) -> bool:
        """预加载（后台线程用），失败静默。"""
        try:
            self.get(path, rate)
            return True
        except DecodeError:
            return False

    # -- 维护
    def drop_rate(self, keep_rate: int) -> None:
        """设备采样率变了，丢掉别的采样率的缓存省内存。"""
        with self._lock:
            for key in [k for k in self._items if k[1] != int(keep_rate)]:
                self._evict_key(key)

    def invalidate(self, path: str | Path) -> None:
        """文件被替换/删除时清掉相关缓存。"""
        with self._lock:
            target = str(path)
            for key in [k for k in self._items if k[0] == target]:
                self._evict_key(key)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def _evict_key(self, key: tuple[str, int]) -> None:
        data = self._items.pop(key, None)
        if data is not None:
            self._bytes -= data.nbytes

    def _evict(self) -> None:
        while self._bytes > self.budget_bytes and len(self._items) > 1:
            _, data = self._items.popitem(last=False)  # 淘汰最久未使用
            self._bytes -= data.nbytes

    @property
    def usage_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._items)
