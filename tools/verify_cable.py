"""验证 VB-CABLE 路由是否真的通了（程序 → 虚拟声卡 → 语音软件那条线）。

用法：
    python tools/verify_cable.py

判断标准：
  * 主路过不去 / 端点不在        → 先装 VB-CABLE 或看顶栏橙色警告
  * 两端采样率不一致（会变调）    → 按提示把两端统一成 48000Hz
  * 主频与测试音一致、峰值不为 0  → 这一侧就通了，剩下的只是系统「侦听」和语音软件输入设备
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from core import device_enum
from core.audio_engine import ROUTE_DUAL, ROUTE_VIRTUAL, AudioEngine, OutputRoute
from core.constants import MODE_MIC
from core.recorder import RecorderEngine

TEST_FREQ = 880.0
problems: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        problems.append(name)


def dominant(data: np.ndarray, rate: int) -> float:
    mono = data.mean(axis=1) if data.ndim > 1 else data
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(mono.size)))
    freqs = np.fft.rfftfreq(mono.size, 1.0 / rate)
    band = (freqs > 150) & (freqs < 4000)
    if not band.any():
        return 0.0
    return float(freqs[band][int(np.argmax(spectrum[band]))])


def main() -> int:
    rules = device_enum.merge_rules(None)
    cable = device_enum.detect_vb_cable(rules, show_all=True)
    print(f"虚拟声卡：{cable.summary()}")
    if not cable.complete:
        print("\n先装 VB-CABLE：https://vb-audio.com/Cable/")
        print("或运行：powershell -ExecutionPolicy Bypass -File .\\tools\\install_vbcable.ps1（管理员）")
        return 2

    print(f"  播放端：{cable.playback.name}  {cable.playback.samplerate}Hz  {cable.playback.hostapi}")
    print(f"  录音端：{cable.capture.name}  {cable.capture.samplerate}Hz  {cable.capture.hostapi}")
    if cable.rate_mismatch:
        print("\n[注意] 两端采样率不一致 —— VB-CABLE 跨采样率不重采样，音效会变调/断续。")
        print("       修法：Win+R → control mmsys.cpl")
        print(f"       · 播放 → CABLE Input → 属性 → 高级 → 默认格式选 48000Hz")
        print(f"       · 录制 → CABLE Output → 属性 → 高级 → 默认格式选 48000Hz")
        print("       两处统一后再跑本脚本。\n")

    engine = AudioEngine()
    engine.configure_rules(rules)
    status = engine.set_route(OutputRoute(mode=ROUTE_VIRTUAL))
    check("路由生效在虚拟声卡", status.effective_mode == ROUTE_VIRTUAL and status.primary_ok,
          f"{status.effective_mode} / {status.primary_label}")
    if not status.primary_ok:
        engine.close()
        return 1
    print(f"  引擎：{engine.describe()}")

    recorder = RecorderEngine()
    started = recorder.start(MODE_MIC, cable.capture)
    check("能从 CABLE Output 开录音（模拟语音软件采集）", started, recorder.last_error or "")
    if not started:
        engine.close()
        return 1

    time.sleep(0.35)
    engine.play_test_tone(1.5, TEST_FREQ)
    time.sleep(1.6)
    result = recorder.stop()
    engine.close()
    check("录音拿到数据", result is not None)
    if result is None:
        return 1

    data, rate = result
    peak = float(np.max(np.abs(data)))
    freq = dominant(data, rate)
    print(f"  录到：{data.shape[0] / rate:.2f}s @ {rate}Hz，峰值 {peak:.4f}，主频 {freq:.0f}Hz")
    check("虚拟声卡那条线有声音", peak > 0.01, f"peak={peak:.4f}")
    if peak > 0.01:
        check(f"音高正确（测试音 {TEST_FREQ:.0f}Hz）", abs(freq - TEST_FREQ) < 60,
              f"{freq:.0f}Hz")

    # 双输出（附带确认本地那一路也能同时开）
    physical = device_enum.physical_outputs(rules)
    local = next((c for c in physical if c.is_default), physical[0] if physical else None)
    if local is not None:
        engine2 = AudioEngine()
        engine2.configure_rules(rules)
        dual = engine2.set_route(
            OutputRoute(mode=ROUTE_DUAL, primary_name=cable.playback.name,
                        monitor_name=local.name)
        )
        check("双输出两路能同时开", dual.primary_ok and dual.monitor_ok,
              dual.monitor_error or f"延迟 {dual.latency_ms}ms")
        engine2.close()

    print()
    if problems:
        print(f"{len(problems)} 项未通过：" + "、".join(problems))
        print("如果只是「音高正确」没过，几乎都是两端采样率不一致，按上面的修法统一即可。")
        return 1
    print("虚拟声卡这一侧已通：剩下的只是系统「侦听此设备」和语音软件输入设备选择。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
