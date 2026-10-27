"""音频读写与轻量处理：写 16bit WAV、裁剪、归一化、可选导出 mp3。

约定：
* 内存里统一用 numpy ``float32``、形状 ``(frames, channels)``，取值 [-1, 1]。
* 落盘统一 44100Hz / PCM_16（WAV），与音效板完全一致，避免播放端重采样。
* 所有"能提前发现"的问题都抛 ``AudioError``/``ExportError``，消息直接给用户看。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .constants import BIT_DEPTH, SAMPLE_RATE


class AudioError(Exception):
    """音频处理失败，消息面向用户。"""


class ExportError(AudioError):
    """导出（例如 mp3）失败。"""


# ---------------------------------------------------------------- 基础
def to_float32(data: np.ndarray) -> np.ndarray:
    """规范成 float32、二维、C 连续。"""
    array = np.asarray(data, dtype=np.float32)
    if array.ndim == 1:
        array = array[:, None]
    if array.size == 0 or array.shape[0] == 0:
        raise AudioError("音频内容为空")
    return np.ascontiguousarray(array)


def duration_seconds(data: np.ndarray, samplerate: int = SAMPLE_RATE) -> float:
    frames = int(np.asarray(data).shape[0])
    return frames / float(samplerate) if samplerate > 0 else 0.0


def peak_dbfs(data: np.ndarray) -> float:
    """峰值电平（dBFS）。全静音返回 -inf。"""
    array = np.asarray(data, dtype=np.float32)
    if array.size == 0:
        return float("-inf")
    peak = float(np.max(np.abs(array)))
    if peak <= 1e-12:
        return float("-inf")
    return 20.0 * float(np.log10(peak))


def rms_dbfs(data: np.ndarray) -> float:
    array = np.asarray(data, dtype=np.float32)
    if array.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(np.square(array))))
    if rms <= 1e-12:
        return float("-inf")
    return 20.0 * float(np.log10(rms))


# ---------------------------------------------------------------- 采样率
def resample_to(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """线性插值重采样（(frames, ch) -> (new_frames, ch)）。

    什么时候会用到：不少麦克风/WASAPI 设备只接受它自己的混音采样率
    （常见 48000），这时录音按 48000 采集、保存前统一换成 44100，
    保证音效板拿到的永远是 44100Hz 的文件。
    线性插值对语音/音效足够；不引入 scipy/soxr 这类重依赖。
    """
    array = to_float32(data)
    if src_rate == dst_rate or src_rate <= 0 or dst_rate <= 0:
        return array
    src_frames = array.shape[0]
    dst_frames = max(1, int(round(src_frames * dst_rate / src_rate)))
    if src_frames < 2:
        return np.repeat(array[:1], dst_frames, axis=0)

    src_x = np.arange(src_frames, dtype=np.float64)
    dst_x = np.linspace(0.0, src_frames - 1, dst_frames, dtype=np.float64)
    out = np.empty((dst_frames, array.shape[1]), dtype=np.float32)
    for channel in range(array.shape[1]):
        out[:, channel] = np.interp(dst_x, src_x, array[:, channel]).astype(np.float32)
    return out


# ---------------------------------------------------------------- 写盘
def write_wav(
    path: str | Path,
    data: np.ndarray,
    samplerate: int = SAMPLE_RATE,
    subtype: str = "PCM_16",
) -> Path:
    """原子写入 WAV：先写同目录临时文件，成功后再替换，避免半截文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    array = to_float32(data)
    # 先夹到 [-1, 1]，否则 float->int16 会环绕成噪音
    array = np.clip(array, -1.0, 1.0)

    tmp_path = target.with_name(target.name + ".tmp")
    try:
        sf.write(str(tmp_path), array, samplerate, subtype=subtype, format="WAV")
        os.replace(tmp_path, target)
    except Exception as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise AudioError(f"写入 WAV 失败：{exc}") from exc
    return target


def load_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """读回 WAV（试听用）。"""
    try:
        data, samplerate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:
        raise AudioError(f"读取音频失败：{exc}") from exc
    return to_float32(data), int(samplerate)


# ---------------------------------------------------------------- 裁剪
def trim(
    data: np.ndarray,
    start_seconds: float,
    end_seconds: float | None = None,
    samplerate: int = SAMPLE_RATE,
) -> np.ndarray:
    """掐头去尾：保留 [start, end) 区间（秒）。end 为 None 表示到结尾。"""
    array = to_float32(data)
    total = array.shape[0]
    total_seconds = total / float(samplerate)

    start = max(0.0, float(start_seconds or 0.0))
    end = total_seconds if end_seconds is None else float(end_seconds)
    end = min(total_seconds, end)

    if start >= end:
        raise AudioError(
            f"裁剪区间无效：起点 {start:.2f}s 必须小于终点 {end:.2f}s（总长 {total_seconds:.2f}s）"
        )
    start_frame = int(round(start * samplerate))
    end_frame = int(round(end * samplerate))
    # 至少保留 10ms，避免用户把区间设得极小后得到空文件
    if end_frame - start_frame < int(0.01 * samplerate):
        raise AudioError("裁剪后的音频太短（不足 10 毫秒），请调整起止点")
    return np.ascontiguousarray(array[start_frame:end_frame])


# ---------------------------------------------------------------- 归一化
def normalize_peak(data: np.ndarray, target_dbfs: float = -1.0) -> tuple[np.ndarray, float]:
    """把峰值拉到 target_dbfs（默认 -1dBFS，留一点余量防削波）。

    返回 (处理后的数据, 实际增益倍数)。全静音时原样返回、增益 1.0。
    只放大/缩小整体，不改动态，所以不会引入失真。
    """
    array = to_float32(data)
    peak = float(np.max(np.abs(array)))
    if peak <= 1e-9:
        return array, 1.0
    target_linear = float(10.0 ** (target_dbfs / 20.0))
    gain = target_linear / peak
    if abs(gain - 1.0) < 1e-3:
        return array, 1.0
    return np.ascontiguousarray(np.clip(array * gain, -1.0, 1.0)), gain


def clamp_peak(data: np.ndarray, ceiling: float = 0.999) -> np.ndarray:
    """超过 ceiling 的样本做软削顶（只在用户手动调大增益后用得上）。"""
    array = np.asarray(data, dtype=np.float32)
    return np.clip(array, -ceiling, ceiling).astype(np.float32, copy=False)


# ---------------------------------------------------------------- mp3
def mp3_available() -> tuple[bool, str]:
    """检查 mp3 导出是否可用，返回 (可用, 不可用原因)。"""
    try:
        import pydub  # noqa: F401
    except Exception:
        return False, "未安装 pydub（pip install pydub）"
    try:
        from pydub.utils import which

        if which("ffmpeg") is None:
            return False, "找不到 ffmpeg（请安装并加入 PATH）"
    except Exception as exc:  # pragma: no cover
        return False, f"检测 ffmpeg 失败：{exc}"
    return True, ""


def export_mp3(
    data: np.ndarray,
    path: str | Path,
    samplerate: int = SAMPLE_RATE,
    bitrate: str = "192k",
) -> Path:
    """导出 mp3：先落一个临时 wav，再用 pydub/ffmpeg 转码。"""
    ok, reason = mp3_available()
    if not ok:
        raise ExportError(f"无法导出 MP3：{reason}。可以先把输出格式改回 WAV。")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="sb_rec_"))
    tmp_wav = tmp_dir / "export.wav"
    try:
        write_wav(tmp_wav, data, samplerate)
        from pydub import AudioSegment

        segment = AudioSegment.from_wav(str(tmp_wav))
        segment.export(str(target), format="mp3", bitrate=bitrate)
    except ExportError:
        raise
    except Exception as exc:
        raise ExportError(f"导出 MP3 失败：{exc}") from exc
    finally:
        try:
            tmp_wav.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass
    return target


def save_audio(
    path: str | Path,
    data: np.ndarray,
    fmt: str = "wav",
    samplerate: int = SAMPLE_RATE,
) -> Path:
    """按格式保存：wav 直接写，mp3 走 pydub。"""
    target = Path(path)
    array = to_float32(data)

    if fmt == "mp3":
        return export_mp3(array, target, samplerate)
    # 统一成 .wav 扩展名，避免用户手填了别的后缀
    if target.suffix.lower() != ".wav":
        target = target.with_suffix(".wav")
    return write_wav(target, array, samplerate)


def file_info(path: str | Path) -> str:
    """给界面用的一行信息：时长 / 采样率 / 声道。"""
    try:
        info = sf.info(str(path))
        seconds = info.frames / float(info.samplerate or SAMPLE_RATE)
        return (
            f"{seconds:.2f}s · {info.samplerate} Hz · "
            f"{info.channels} ch · {BIT_DEPTH} bit"
        )
    except Exception:
        return ""
