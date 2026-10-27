"""测试用的"声音发生器"（不是产品代码，只给自检/调试用）。

四种用法：
    python tools/test_player.py tone 440 0.25 5           # 纯正弦音（验证单频点）
    python tools/test_player.py bubble 0.3 5              # 合成"咕咕嘎嘎"（默认柔和）
    python tools/test_player.py bubble 0.3 5 crisp        # 旧版偏硬的音色（对比用）
    python tools/test_player.py wav some.wav 5            # 循环播放任意 wav
    python tools/test_player.py save out.wav 3 soft       # 只生成 wav 不播放

为什么还要"咕咕嘎嘎"：纯正弦音只能验证某一个频点，语音是宽带信号，
更能证明 loopback 把整段声音都搬过来了。

合成原理（共振峰合成，纯 numpy，不依赖任何 TTS 引擎）：
* 音节 = 轻轻的送气起音（不是爆破）+ 元音；
* 元音用一组谐波，谐波幅度按三个共振峰（F1/F2/F3）的洛伦兹包络加权：
  /u/（咕）≈ F1 320 / F2 800，/a/（嘎）≈ F1 730 / F2 1090；
* 整体走一遍短核低通，把高频"棱角"磨掉。

两个预设（`soft` 更柔、`crisp` 偏硬，后者保留旧参数便于对比）：
    soft  : 基频 165Hz、起音 40ms / 收尾 90ms、谐波 1/k^0.62 衰减、峰值 0.32
    crisp : 基频 180Hz、起音 15ms / 收尾 30ms、谐波 1/k^0.35 衰减、峰值 0.60
"""

from __future__ import annotations

import math
import sys
import time

import numpy as np

RATE = 48000
BLOCK = 1024

#: 元音共振峰（F1, F2, F3）与相对增益（F3 压低一点，听起来更"暗"、更柔）
VOWELS = {
    "u": ((320.0, 800.0, 2200.0), (1.0, 0.45, 0.16)),   # 咕
    "a": ((700.0, 1060.0, 2300.0), (1.0, 0.52, 0.18)),  # 嘎
}

#: 音色预设
PRESETS: dict[str, dict] = {
    # 柔和：低音高、慢起落、高频更少、极轻的送气、整体更安静
    "soft": {
        "f0": 165.0,
        "vibrato_depth": 0.009,      # 0.9% 基频颤动，几乎听不出"抖"
        "vibrato_rate": 4.2,
        "rolloff": 0.62,             # 谐波 1/k^0.62（越大越暗）
        "attack": 0.040,
        "release": 0.090,
        "breath": 0.030,             # 起音前的送气：很低、且被压到 1kHz 以下
        "breath_taps": 15,           # 送气噪声的低通强度（越大越闷，避免"嘶"）
        "glide": 0.04,               # 起音时基频从 -4% 滑到目标
        "gap": 0.060,
        "normalize": 0.32,
        "smooth_taps": 3,            # 3 点二项式核，连续两次 → 温和低通
        "syllables": ((0.24, "u"), (0.22, "u"), (0.27, "a"), (0.28, "a")),
    },
    # 旧版偏硬：保留下来做 A/B 对比
    "crisp": {
        "f0": 180.0,
        "vibrato_depth": 0.030,
        "vibrato_rate": 5.5,
        "rolloff": 0.35,
        "attack": 0.015,
        "release": 0.030,
        "breath": 0.0,
        "breath_taps": 0,
        "glide": 0.0,
        "gap": 0.040,
        "normalize": 0.60,
        "smooth_taps": 0,
        "syllables": ((0.19, "u"), (0.17, "u"), (0.22, "a"), (0.24, "a")),
    },
}


def _smooth_taps(signal: np.ndarray, taps: int, passes: int = 2) -> np.ndarray:
    """用短二项式核做温和低通（连续几次），磨掉高频毛刺。

    taps=0 表示不处理；taps=3 → [1,2,1]/4。
    """
    if taps <= 0:
        return signal
    if taps == 3:
        kernel = np.array([1.0, 2.0, 1.0]) / 4.0
    elif taps == 5:
        kernel = np.array([1.0, 4.0, 6.0, 4.0, 1.0]) / 16.0
    else:
        kernel = np.hanning(taps + 2)[1:-1]
        kernel = kernel / kernel.sum()
    result = signal
    for _ in range(passes):
        result = np.convolve(result, kernel, mode="same")
    return result


def _soft_envelope(count: int, attack: float, release: float) -> np.ndarray:
    """升余弦包络：起落都是平滑的 S 形，不会有"咔"的瞬态。"""
    envelope = np.ones(count, dtype=np.float64)
    attack_samples = min(count, max(1, int(attack * RATE)))
    release_samples = min(count - attack_samples, max(1, int(release * RATE))) if count > 1 else 0
    if attack_samples:
        ramp = np.linspace(0.0, math.pi, attack_samples)
        envelope[:attack_samples] = 0.5 - 0.5 * np.cos(ramp)
    if release_samples:
        ramp = np.linspace(0.0, math.pi, release_samples)
        envelope[-release_samples:] = 0.5 + 0.5 * np.cos(ramp)
    return envelope


def _vowel(vowel: str, duration: float, preset: dict) -> np.ndarray:
    """合成一个元音音节（柔和版：低一路基频 + 平滑起落 + 高频少）。"""
    count = max(1, int(duration * RATE))
    t = np.arange(count, dtype=np.float64) / RATE
    f0_base = float(preset["f0"])

    # 基频：轻微颤动 + 起音时从低处滑上来（更"人一点"，也少了硬起音）
    f0 = f0_base * (1.0 + preset["vibrato_depth"] * np.sin(
        2 * math.pi * preset["vibrato_rate"] * t))
    glide = float(preset.get("glide", 0.0))
    if glide > 0:
        glide_samples = min(count, max(1, int(glide * RATE * 6)))
        ramp = np.linspace(1.0 - glide, 1.0, glide_samples)
        f0[:glide_samples] *= ramp
    phase = np.cumsum(2 * math.pi * f0 / RATE)

    formants, gains = VOWELS[vowel]
    signal = np.zeros(count, dtype=np.float64)
    harmonic = 1
    while harmonic * f0_base < 4200.0 and harmonic <= 32:
        freq = harmonic * f0_base
        amplitude = 0.0
        for formant, gain in zip(formants, gains):
            bandwidth = 90.0 + 8.0 * (formant / 500.0)
            amplitude += gain / (1.0 + ((freq - formant) / bandwidth) ** 2)
        # 谐波衰减更快 → 高频更少 → 更柔
        amplitude /= max(1.0, harmonic ** float(preset["rolloff"]))
        signal += amplitude * np.sin(harmonic * phase)
        harmonic += 1

    return signal * _soft_envelope(count, float(preset["attack"]), float(preset["release"]))


def _breath(duration: float, preset: dict) -> np.ndarray:
    """起音前的轻声送气（替代原来的爆破音，几乎没有"冲击感"）。

    关键是**必须压得很低通**：白噪声直接加进来会变"嘶"声，反而更刺耳，
    所以这里用较大核多次卷积（等效 2kHz 以下），听起来才是"呼"的一下。
    """
    level = float(preset.get("breath", 0.0))
    if level <= 0:
        return np.zeros(0)
    count = max(1, int(duration * RATE))
    noise = np.random.default_rng(11).normal(0.0, 1.0, count)
    filtered = _smooth_taps(noise, int(preset.get("breath_taps", 5)), passes=4)
    envelope = np.sin(np.linspace(0.0, math.pi, count)) ** 1.6
    peak = float(np.max(np.abs(filtered))) or 1.0
    return filtered / peak * level * envelope


def make_bubble(seconds: float, preset: str = "soft") -> np.ndarray:
    """生成一段"咕咕嘎嘎"，返回 float32 单声道。preset = soft / crisp。"""
    params = PRESETS.get(preset, PRESETS["soft"])
    syllables = params["syllables"]
    gap = np.zeros(int(params["gap"] * RATE), dtype=np.float64)

    pieces: list[np.ndarray] = []
    total = 0
    index = 0
    while total / RATE < seconds:
        duration, vowel = syllables[index % len(syllables)]
        breath = _breath(duration * 0.30, params)
        voice = _vowel(vowel, duration, params)
        syllable = np.concatenate([breath, voice]) if breath.size else voice
        pieces.append(syllable)
        pieces.append(gap)
        total += syllable.size + gap.size
        index += 1

    data = np.concatenate(pieces) if pieces else np.zeros(0)
    data = _smooth_taps(data, int(params.get("smooth_taps", 0)))

    # 整段再补 20ms 淡入淡出，播放起止都不会"啪"
    fade = min(int(0.02 * RATE), data.size // 2)
    if fade > 0:
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, fade))
        data[:fade] *= ramp
        data[-fade:] *= ramp[::-1]

    peak = float(np.max(np.abs(data))) or 1.0
    data = (data / peak * float(params["normalize"])).astype(np.float32)
    limit = int(seconds * RATE)
    return data[:limit] if data.size > limit else data


class _LoopPlayer:
    """把一个 numpy 缓冲循环播放到指定时长。"""

    def __init__(self, buffer: np.ndarray, volume: float) -> None:
        self.buffer = np.ascontiguousarray(buffer, dtype=np.float32) * volume
        self.position = 0

    def callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        chunks = []
        remaining = frames
        while remaining > 0:
            available = self.buffer.size - self.position
            if available <= 0:
                self.position = 0
                available = self.buffer.size
            take = min(available, remaining)
            chunks.append(self.buffer[self.position:self.position + take])
            self.position += take
            remaining -= take
        mono = np.concatenate(chunks) if chunks else np.zeros(frames, np.float32)
        outdata[:, 0] = mono
        if outdata.shape[1] > 1:
            outdata[:, 1] = mono


def _play(mono: np.ndarray, volume: float, seconds: float) -> int:
    import os

    import sounddevice as sd

    print(f"READY {os.getpid()}", flush=True)
    player = _LoopPlayer(mono, volume)
    stream = sd.OutputStream(samplerate=RATE, blocksize=BLOCK, channels=2,
                             dtype="float32", callback=player.callback)
    stream.start()
    try:
        time.sleep(seconds)
    finally:
        stream.stop()
        stream.close()
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    mode = sys.argv[1]

    if mode == "tone":
        freq = float(sys.argv[2]) if len(sys.argv) > 2 else 440.0
        volume = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
        seconds = float(sys.argv[4]) if len(sys.argv) > 4 else 5.0
        t = np.arange(int(seconds * RATE), dtype=np.float32) / RATE
        mono = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        return _play(mono, volume, seconds)

    if mode == "bubble":
        volume = float(sys.argv[2]) if len(sys.argv) > 2 else 0.35
        seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
        preset = sys.argv[4] if len(sys.argv) > 4 else "soft"
        return _play(make_bubble(min(seconds, 8.0), preset), volume, seconds)

    if mode == "wav":
        import soundfile as sf

        path = sys.argv[2]
        volume = float(sys.argv[3]) if len(sys.argv) > 3 else 0.3
        seconds = float(sys.argv[4]) if len(sys.argv) > 4 else 5.0
        data, rate = sf.read(path, dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        if rate != RATE:
            ratio = RATE / rate
            mono = np.interp(np.arange(int(mono.size * ratio)) / ratio,
                             np.arange(mono.size), mono).astype(np.float32)
        return _play(mono, volume, seconds)

    if mode == "save":
        import soundfile as sf

        out = sys.argv[2]
        seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
        preset = sys.argv[4] if len(sys.argv) > 4 else "soft"
        sf.write(out, make_bubble(seconds, preset), RATE, subtype="PCM_16")
        print(f"已生成 {out}（preset={preset}）")
        return 0

    print(f"未知模式：{mode}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
