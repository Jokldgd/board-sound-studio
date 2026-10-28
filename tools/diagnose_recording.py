"""录音诊断：不需要打开主界面，直接录 3 秒并保存，输出一份可发送的报告。

用法（在项目根目录）：
    python tools\\diagnose_recording.py
    python tools\\diagnose_recording.py --seconds 5 --mode system
    python tools\\diagnose_recording.py --out D:\\诊断结果.txt

会把结果同时打印到屏幕、写入 ``诊断结果.txt``（默认在项目根），
并把录音存到数据目录 audio\\诊断_YYYYmmdd_HHMMSS.wav 方便试听。
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT: list[str] = []


def say(text: str = "") -> None:
    print(text)
    REPORT.append(text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--mode", default="", help="mic / system / app，默认用配置里的")
    parser.add_argument("--device", default="", help="设备名（默认用配置里的）")
    parser.add_argument("--out", default=str(ROOT / "诊断结果.txt"))
    args = parser.parse_args()

    say(f"时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    say(f"系统: {platform.platform()}  Python {platform.python_version()}")
    say(f"程序目录: {ROOT}")

    from core import paths

    say(f"数据目录: {paths.data_dir()}")
    say(f"音效目录: {paths.audio_dir()}")
    say(f"日志文件: {paths.log_path()}（存在={paths.log_path().is_file()}）")

    from core.config import ConfigStore
    from core import constants, device_enum

    store = ConfigStore()
    try:
        config = store.load()
    except Exception as exc:  # noqa: BLE001
        say(f"读取配置失败: {exc}")
        config = store.config
    say(f"配置: mode={config.record_mode} mic={config.mic_device!r} "
        f"system={config.system_device!r} app_pid={config.app_pid!r} "
        f"save_dir={config.save_dir!r} format={config.output_format}")

    rules = device_enum.merge_rules(None)
    say("")
    say("== 输入设备（sounddevice）==")
    try:
        import sounddevice as sd

        for index, info in enumerate(sd.query_devices()):
            if info.get("max_input_channels", 0) > 0:
                say(f"  [{index}] {info['name']} | in={info['max_input_channels']} "
                    f"rate={int(info['default_samplerate'])}")
    except Exception as exc:  # noqa: BLE001
        say(f"  sounddevice 枚举失败: {exc}")

    say("")
    say("== 录音捕获设备（pyaudiowpatch）==")
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                say(f"  [{i}] {info['name']} | in={info['maxInputChannels']} "
                    f"rate={int(info['defaultSampleRate'])}")
        pa.terminate()
    except Exception as exc:  # noqa: BLE001
        say(f"  pyaudiowpatch 枚举失败: {exc}")

    say("")
    say("== 实际录音 ==")
    from core.recorder import RecorderEngine
    from core.constants import MODE_MIC

    mode = args.mode or (config.record_mode if config.record_mode in
                         (constants.MODE_MIC, constants.MODE_SYSTEM, constants.MODE_APP)
                         else MODE_MIC)
    target = None
    try:
        kind = "loopback" if mode == constants.MODE_SYSTEM else "input"
        choices = device_enum.list_devices(kind, rules)
        say(f"候选 {kind} 设备: {len(choices)}")
        for choice in choices[:8]:
            say(f"  - {choice.name} (backend={choice.backend})")
        wanted = args.device or (
            config.system_device if mode == constants.MODE_SYSTEM else config.mic_device)
        target = next((c for c in choices if c.name == wanted), None) if wanted else None
        if target is None:
            target = next((c for c in choices if c.is_default), choices[0] if choices else None)
        if target is None:
            say("没有可用设备，无法录音")
            return 1
        say(f"选用: {target.name} (backend={target.backend} rate={target.samplerate})")
    except Exception:  # noqa: BLE001
        say("选择设备失败:\n" + traceback.format_exc())
        return 1

    engine = RecorderEngine()
    if not engine.start(mode, target):
        say(f"引擎启动失败: {engine.last_error}")
        return 1
    say(f"开始录音 {args.seconds:.1f}s ...")
    time.sleep(args.seconds)
    result = engine.stop()
    if result is None:
        say("没有录到数据（engine.stop() 返回 None）")
        return 1
    data, rate = result
    peak = float(abs(data).max()) if data.size else 0.0
    rms = float((data ** 2).mean() ** 0.5) if data.size else 0.0
    say(f"采集成功: {data.size} samples @ {rate}Hz, 时长 {data.size / rate:.2f}s, "
        f"peak={peak:.3f}, rms={rms:.4f}")

    from core import audio_io

    out = paths.audio_dir() / f"诊断_{datetime.now():%Y%m%d_%H%M%S}.wav"
    audio_io.write_wav(out, data, rate)
    say(f"已写入: {out}（{out.stat().st_size / 1024:.0f} KB）")

    log = paths.log_path()
    if log.is_file():
        say("")
        say("== 最近日志（30 行）==")
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-30:]:
                say("  " + line)
        except OSError as exc:
            say(f"读取日志失败: {exc}")
    else:
        say("")
        say("== 没有 studio.log ==（程序可能从未成功写日志，或日志被清理）")

    report_path = Path(args.out)
    try:
        report_path.write_text("\n".join(REPORT), encoding="utf-8")
        print(f"\n报告已写入: {report_path}")
    except OSError as exc:
        print(f"写报告失败: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
