"""核心层自检（不开界面）：配置/音效库/设备/播放混音/录音/热键。

用法（在 soundboard_studio 目录）：
    python tools/selftest.py
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from core import audio_io, clip_library, device_enum  # noqa: E402
from core.clip_library import Clip, ClipLibrary  # noqa: E402
from core.config import ConfigStore, StudioConfig  # noqa: E402
from core.constants import (  # noqa: E402
    MODE_APP,
    MODE_MIC,
    MODE_SYSTEM,
    SAMPLE_RATE,
    SOURCE_IMPORT,
    SOURCE_RECORD,
)

WORK = ROOT / ".selftest"
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(name)


def sine(seconds: float = 1.0, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)[:, None]


# ---------------------------------------------------------------- 配置
def test_config() -> None:
    cfg_path = WORK / "config.json"
    store = ConfigStore(cfg_path)
    store.load()
    check("首次运行生成 config.json", cfg_path.is_file())

    store.config.master_volume = 0.66
    store.config.record_hotkey = "ctrl+alt+r"
    store.config.record_mode = MODE_SYSTEM
    store.config.remember_device(MODE_MIC, "mic-A")
    store.config.remember_device(MODE_MIC, "mic-B")
    store.config.remember_device(MODE_MIC, "mic-C")
    store.config.remember_device(MODE_MIC, "mic-D")
    store.save(immediate=True)

    again = ConfigStore(cfg_path)
    again.load()
    check("配置持久化", abs(again.config.master_volume - 0.66) < 1e-6
          and again.config.record_hotkey == "ctrl+alt+r"
          and again.config.record_mode == MODE_SYSTEM)
    check("最近设备最多 3 个且最新在前",
          again.config.recent_mic == ["mic-D", "mic-C", "mic-B"], str(again.config.recent_mic))

    cfg_path.write_text("{ broken", encoding="utf-8")
    broken = ConfigStore(cfg_path)
    broken.load()
    check("坏配置自愈并留备份", bool(broken.warnings), broken.warnings[0][:40] if broken.warnings else "")


# ---------------------------------------------------------------- 音效库
def test_library() -> None:
    audio_root = WORK / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)

    lib = ClipLibrary()
    # 造一个外部文件导入
    ext = WORK / "外来音效.wav"
    audio_io.write_wav(ext, sine(0.5), SAMPLE_RATE)
    added, errors = lib.import_files([ext])
    check("导入文件复制进 audio/", len(added) == 1 and not errors,
          f"{len(added)} 个, 错误 {errors}")
    check("导入后文件在 audio/ 里", added and added[0].path().parent.resolve() == audio_root.resolve())
    check("来源标签是「导入」", added and added[0].source == SOURCE_IMPORT)

    # 重复导入同一文件应报"已在列表"
    again, errors2 = lib.import_files([ext])
    check("重复导入被拒绝", not again and bool(errors2), errors2[0] if errors2 else "")

    # 录音保存
    clip = lib.save_recorded(sine(1.2), "录音测试", SAMPLE_RATE, write_sidecar=True)
    check("录音落盘且命名合规",
          clip.path().name.startswith("rec_") and clip.path().suffix == ".wav"
          and len(clip.path().stem) == len("rec_YYYYMMDD_HHMMSS"),
          clip.path().name)
    check("来源标签是「录音」", clip.source == SOURCE_RECORD)
    sidecar = clip.path().with_suffix(".json")
    check("sidecar 已写出", sidecar.is_file())
    if sidecar.is_file():
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        check("sidecar 内容正确", payload.get("name") == "录音测试", str(payload))

    # 保存后读音
    back, rate = audio_io.load_wav(clip.path())
    check("录音时长正确", abs(back.shape[0] / rate - 1.2) < 0.05,
          f"{back.shape[0] / rate:.2f}s")

    # 已登记条目：config 是唯一真相，手改 sidecar 不会再覆盖它
    sidecar.write_text(json.dumps({"name": "改过的名字", "volume": 1.5}, ensure_ascii=False),
                       encoding="utf-8")
    updated = lib.scan_directory()
    check("扫描不覆盖已登记条目的名称（config 优先）",
          clip.name == "录音测试" and abs(clip.volume - 1.0) < 1e-6,
          f"{clip.name}/{clip.volume}")
    check("扫描未产生重复条目", len(updated[0]) == 0, str(len(updated[0])))

    # 未登记的新文件：名字/音量从 sidecar 读
    fresh = audio_root / "rec_fresh.wav"
    audio_io.write_wav(fresh, sine(0.3), SAMPLE_RATE)
    fresh.with_suffix(".json").write_text(
        json.dumps({"name": "侧车起名", "volume": 1.2}, ensure_ascii=False), encoding="utf-8"
    )
    added3, _ = lib.scan_directory()
    fresh_clip = next((c for c in lib.clips if c.file.endswith("rec_fresh.wav")), None)
    check("新文件按 sidecar 命名", fresh_clip is not None and fresh_clip.name == "侧车起名",
          fresh_clip.name if fresh_clip else "None")

    # 关键回归：界面改名/改音量后**重启不丢**（以前只改内存+config，
    # 重启时 scan_directory 会用旧 sidecar 把名字覆盖回去）
    check("library.rename 返回成功", lib.rename(clip.id, "界面改的名字"))
    check("改名同步写进 sidecar",
          json.loads(sidecar.read_text(encoding="utf-8")).get("name") == "界面改的名字",
          str(json.loads(sidecar.read_text(encoding="utf-8"))))
    lib.set_volume(clip.id, 0.8)
    check("音量同步写进 sidecar",
          abs(float(json.loads(sidecar.read_text(encoding="utf-8")).get("volume", 0)) - 0.8)
          < 1e-6,
          str(json.loads(sidecar.read_text(encoding="utf-8"))))
    # 模拟重启：从 config 里的记录重建（名字/音量都带着），再扫描目录
    rebuilt = ClipLibrary([Clip(id=clip.id, name=clip.name, file=clip.file, volume=0.8)])
    rebuilt.scan_directory()
    restored = rebuilt.get(clip.id)
    check("重启后名字保持（config 优先）",
          restored is not None and restored.name == "界面改的名字",
          restored.name if restored else "None")
    check("重启后音量保持",
          restored is not None and abs(restored.volume - 0.8) < 1e-6,
          str(restored.volume if restored else None))

    # 没有 sidecar 的条目：改名后不应凭空造 sidecar，但 config 里的名字仍然生效
    plain = lib.save_recorded(sine(0.4), "无侧车", SAMPLE_RATE, write_sidecar=False)
    plain_sidecar = plain.path().with_suffix(".json")
    if plain_sidecar.exists():
        plain_sidecar.unlink()
    lib.rename(plain.id, "无侧车改名")
    check("无 sidecar 时改名不造文件", not plain_sidecar.exists())

    # 孤儿文件（直接丢进目录）能被扫到
    orphan = audio_root / "手动丢进来的.wav"
    audio_io.write_wav(orphan, sine(0.3), SAMPLE_RATE)
    lib2 = ClipLibrary()
    added2, _ = lib2.scan_directory()
    check("启动扫描能收进未登记文件", len(added2) == len(lib.clips) + 1,
          f"扫到 {len(added2)} 个")

    # 持久化往返
    payload = lib2.to_list()
    lib3 = ClipLibrary.from_list(payload)
    check("音效库序列化往返", len(lib3.clips) == len(lib2.clips)
          and lib3.clips[0].to_dict() == lib2.clips[0].to_dict())

    # 删除：文件一并删除
    target = lib3.clips[0]
    path = target.path()
    lib3.remove(target.id, delete_file=True)
    check("删除时同时删文件", not path.exists())

    # 热键冲突检测
    lib4 = ClipLibrary([
        Clip(id="a", name="A", file="audio/a.wav", hotkey="f1"),
        Clip(id="b", name="B", file="audio/b.wav", hotkey="f1"),
        Clip(id="c", name="C", file="audio/c.wav", hotkey="f2"),
    ])
    dup = lib4.duplicate_hotkeys()
    check("检测到程序内热键重复", "f1" in dup and len(dup["f1"]) == 2, str(dup))
    check("热键映射只保留第一条", lib4.hotkey_map()["f1"].id == "a")


# ---------------------------------------------------------------- 设备
def test_devices() -> None:
    rules = device_enum.merge_rules(None)
    outputs = device_enum.list_devices("output", rules, show_all=False)
    check("能枚举输出设备（播放用）", bool(outputs), f"{len(outputs)} 个")
    for choice in outputs[:5]:
        print(f"        · [{choice.group}] {choice.short_name} ({choice.hostapi}) idx={choice.index}")
    check("输出设备做了名称清洗",
          all("Realtek" not in c.short_name and "C-Media" not in c.short_name for c in outputs),
          " / ".join(c.short_name for c in outputs[:3]))
    check("输出设备有分组", any(c.group for c in outputs))
    groups = device_enum.group_choices(outputs, "output", recent_keys=[])
    check("输出设备分组标题非空", bool(groups), str([title for title, _ in groups]))

    inputs = device_enum.list_devices("input", rules, show_all=False)
    check("能枚举输入设备", isinstance(inputs, list), f"{len(inputs)} 个")
    all_inputs = device_enum.list_devices("input", rules, show_all=True)
    check("显示全部时设备更多", len(all_inputs) >= len(inputs), f"{len(inputs)}→{len(all_inputs)}")

    loops = device_enum.list_devices("loopback", rules, show_all=False)
    check("能枚举 loopback 设备", isinstance(loops, list), f"{len(loops)} 个")

    # 名字↔索引：同名的 loopback 与 output 应是两套索引，但 key 命名规则一致
    if loops and outputs:
        check("loopback 设备带 loopback 标记", loops[0].is_loopback)
        check("输出设备不带 loopback 标记", not outputs[0].is_loopback)


# ---------------------------------------------------------------- 播放引擎
def test_engine() -> None:
    from core.audio_engine import AudioEngine

    engine = AudioEngine()
    engine.configure_rules(device_enum.merge_rules(None))
    devices = engine.refresh_devices()
    check("引擎拿到设备列表", bool(devices), f"{len(devices)} 个")

    opened = engine.open(None)
    check("打开默认输出设备", opened, engine.last_error or engine.describe())
    if not opened:
        return

    temp_wav = WORK / "engine_tone.wav"
    audio_io.write_wav(temp_wav, sine(0.4), SAMPLE_RATE)

    # 混音核心：离线验证（不依赖声卡回调）
    check("触发播放", engine.trigger("clip-1", temp_wav, 1.0))
    buffer = np.zeros((512, 2), dtype=np.float32)
    engine._mix_into(buffer, 512)
    check("混音输出有声音", float(np.max(np.abs(buffer))) > 0.0, f"peak={engine.last_peak:.3f}")
    check("正在播放", engine.is_playing("clip-1"))

    engine.trigger("clip-2", temp_wav, 0.5)
    buffer2 = np.zeros((512, 2), dtype=np.float32)
    engine._mix_into(buffer2, 512)
    check("多音效可同时混音", set(engine.active_ids()) >= {"clip-1", "clip-2"},
          str(engine.active_ids()))

    engine.set_exclusive(True)
    engine.trigger("clip-3", temp_wav, 1.0)
    for _ in range(20):
        scratch = np.zeros((512, 2), dtype=np.float32)
        engine._mix_into(scratch, 512)
    check("互斥模式打断前一个", engine.active_ids() == ["clip-3"], str(engine.active_ids()))

    engine.set_exclusive(False)
    engine.trigger("clip-4", temp_wav, 1.0)
    engine.release("clip-4")
    for _ in range(20):
        scratch = np.zeros((512, 2), dtype=np.float32)
        engine._mix_into(scratch, 512)
    check("松开后停止播放", not engine.is_playing("clip-4"))

    # 设备冲突提示
    message = engine.conflicts_with(engine.device_key)
    check("播放/录音同设备时给出提示", bool(message), message)
    check("不同设备时不提示", engine.conflicts_with("完全不同的设备") == "")

    engine.close()
    check("关闭引擎", not engine.running)


# ---------------------------------------------------------------- 输出路由
def test_routes() -> None:
    """虚拟声卡 / 双输出 / 仅本地：环形缓冲、按名解析、降级路径。"""
    from core.audio_engine import (
        ROUTE_DUAL,
        ROUTE_LOCAL,
        ROUTE_VIRTUAL,
        AudioEngine,
        OutputRoute,
        RingBuffer,
    )

    # 环形缓冲
    ring = RingBuffer(1024)
    block = np.full((512, 2), 0.5, dtype=np.float32)
    ring.write(block)
    ring.write(block)
    check("环形缓冲按容量存满", ring.frames_available() == 1024)
    out = np.zeros((512, 2), dtype=np.float32)
    check("环形缓冲读取正确", ring.read(512, out) == 0 and float(out[0, 0]) == 0.5)
    ring.clear()
    out.fill(1.0)
    missing = ring.read(256, out)
    check("空缓冲补静音并报欠载",
          missing == 256 and float(out[:256].max()) == 0.0, f"missing={missing}")
    ring2 = RingBuffer(512)
    ring2.write(np.ones((512, 2), dtype=np.float32))
    check("写满时丢弃最旧帧", ring2.write(np.full((256, 2), 2.0, dtype=np.float32)) == 256)

    # 虚拟声卡检测
    rules = device_enum.merge_rules(None)
    cable = device_enum.detect_vb_cable(rules)
    print(f"[INFO] VB-CABLE 检测：{cable.summary()}")
    check("检测函数能给出结论", isinstance(cable.installed, bool))
    check("按名解析设备可用",
          device_enum.resolve_by_name("扬声器", "output", rules) is not None
          or device_enum.resolve_by_name("Speakers", "output", rules) is not None)
    check("不存在的设备名解析为 None",
          device_enum.resolve_by_name("根本不存在的设备", "output", rules) is None)

    engine = AudioEngine()
    engine.configure_rules(rules)

    status = engine.set_route(OutputRoute(mode=ROUTE_LOCAL))
    check("模式 C 开流成功", status.primary_ok, status.primary_error)
    check("模式 C 描述含「本地」", "本地" in engine.describe(), engine.describe())
    check("模式 C 可播测试音", engine.play_test_tone(0.2) and engine.running)

    status = engine.set_route(OutputRoute(mode=ROUTE_VIRTUAL))
    if cable.installed:
        check("有 VB-CABLE 时模式 A 用虚拟声卡",
              status.effective_mode == ROUTE_VIRTUAL and status.primary_ok)
    else:
        check("无 VB-CABLE 时模式 A 降级但仍有声",
              status.effective_mode == ROUTE_LOCAL and status.primary_ok,
              f"{status.effective_mode}/{status.primary_ok}")
        check("无 VB-CABLE 时给出降级原因", "CABLE" in status.degrade_reason,
              status.degrade_reason)
        check("降级标记写进描述", "已降级" in engine.describe(), engine.describe())

    # 双输出模式已弃置：旧配置/旧调用一律按「虚拟声卡」处理
    status = engine.set_route(
        OutputRoute(mode=ROUTE_DUAL, primary_name="", monitor_name="")
    )
    check("旧 dual 路由被强制为虚拟声卡",
          status.mode == ROUTE_VIRTUAL and status.effective_mode == ROUTE_VIRTUAL,
          f"{status.mode}/{status.effective_mode}")

    engine.close()
    check("路由关闭后不再运行", not engine.running and not engine.route_status().primary_ok)


# ---------------------------------------------------------------- 混音
def test_mixer() -> None:
    """麦克风 + 音效的内部混音：双工流、增益、削波检测、降级。"""
    from core.audio_engine import (
        ROUTE_VIRTUAL,
        AudioEngine,
        MixerSettings,
        OutputRoute,
    )

    rules = device_enum.merge_rules(None)
    mic = device_enum.default_mixer_input(rules)
    check("能枚举到混音用麦克风（sounddevice 域）", mic is not None,
          mic.name if mic else "无")
    if mic is None:
        return
    check("混音麦克风索引与录音域解耦",
          isinstance(mic.index, int) and mic.index >= 0, str(mic.index))

    engine = AudioEngine()
    engine.configure_rules(rules)
    settings = MixerSettings(enabled=True, mic_name=mic.name, mic_gain=1.0, sfx_gain=0.9)
    status = engine.set_mixer(settings)
    check("混音开启后开流成功", status.primary_ok and engine.running,
          f"{status.effective_mode}/{status.primary_error}")
    check("优先使用双工流（同一 callback）", not engine._mixer_split,
          f"split={engine._mixer_split}")
    check("采样率一致", engine.samplerate in (44100, 48000), str(engine.samplerate))

    time.sleep(0.4)
    level = engine.mic_level()
    check("能读到麦克风电平", level >= 0.0, f"{level:.4f}")

    # 削波检测：用独立引擎（避免真实回调把计数清零，造成竞态）
    clip_engine = AudioEngine()
    block = np.ones((1024, 2), dtype=np.float32)
    for _ in range(20):
        clip_engine._track_levels(block, 1024)
    clipping, peak = clip_engine.clip_status()
    check("持续削波能被检测", clipping, f"clipping={clipping} peak={peak}")
    events = clip_engine.drain_events()
    check("削波给出「音量过大」提示", any("音量过大" in e for e in events), str(events[:2]))
    clip_engine.close()

    # 耳返已移除：引擎里不存在任何"麦克风 → 本地设备"的流字段
    check("没有任何麦克风耳返流", not hasattr(engine, "_mic_monitor_stream")
          and not hasattr(engine, "_monitor_stream"))

    # 本地音效监听：只放音效、不含麦克风（这条是"本地听到音效但听不到自己"的关键）
    from core.audio_engine import RingBuffer, Voice

    engine._master = 1.0
    engine._sfx_ring = RingBuffer(8192)
    engine._voices.append(Voice("mix-probe", np.full((1024, 2), 0.25, np.float32), 1.0, 0))
    program = np.zeros((1024, 2), np.float32)
    mic_probe = np.full((1024, 2), 0.5, np.float32)
    engine._mix_program(program, 1024, mic_probe)
    local = np.zeros((1024, 2), np.float32)
    engine._sfx_ring.read(1024, local)
    check("主输出 = 音效 + 麦克风", abs(float(program.mean()) - 0.75) < 1e-3,
          f"{float(program.mean()):.3f}")
    check("本地监听只有音效（不含麦克风）", abs(float(local.mean()) - 0.25) < 1e-3,
          f"{float(local.mean()):.3f}")
    engine._sfx_ring = None
    engine._voices.clear()

    # 本地监听的开关：虚拟声卡下可开、关掉后流释放
    engine.set_route(OutputRoute(mode=ROUTE_VIRTUAL))
    opened = engine.set_local_playback(True)
    check("本地音效监听能打开", opened and engine.local_playback_active(),
          f"opened={opened}")
    engine.set_local_playback(False)
    check("关闭后本地监听流释放", not engine.local_playback_active()
          and engine._sfx_monitor_stream is None)

    # 软限幅：不得直接削成方波，1.0 以内保持不变
    probe = np.array([[0.5, -0.5], [1.5, -2.5], [0.9, 0.2]], dtype=np.float32)
    engine._limit(probe, 3)
    check("软限幅压回 ±1 以内", float(np.max(np.abs(probe))) <= 1.0,
          str(np.round(probe, 3).tolist()))
    check("软限幅不改动 1.0 以内的样本", abs(float(probe[0, 0]) - 0.5) < 1e-6,
          str(float(probe[0, 0])))

    # 测试音：0.5s / 440Hz / 0.3 / 50ms 淡入淡出
    engine.play_test_tone()
    tone = engine._voices[-1]
    samples = tone.data[:, 0]
    seconds = samples.size / float(engine.samplerate)
    check("测试音时长 0.5s", abs(seconds - 0.5) < 0.02, f"{seconds:.2f}s")
    check("测试音峰值 0.3（不满幅）", abs(float(np.max(np.abs(samples))) - 0.3) < 0.02,
          f"{float(np.max(np.abs(samples))):.3f}")
    check("测试音首样本为 0（已淡入）", abs(float(samples[0])) < 1e-3,
          f"{float(samples[0]):.4f}")
    check("测试音末样本为 0（已淡出）", abs(float(samples[-1])) < 1e-3,
          f"{float(samples[-1]):.4f}")

    # 释放：stop()/shutdown() 幂等，流置 None
    engine.stop()
    check("stop() 后输出流为 None", engine._stream is None and engine._mic_input_stream is None)
    engine.shutdown()
    check("shutdown() 幂等且不再运行", not engine.running)

    # 换一个不存在的麦克风 → 应降级为仅音效而不是崩
    bad = engine.set_mixer(MixerSettings(enabled=True, mic_name="不存在的麦克风"))
    check("麦克风不存在时降级为仅音效", bad.primary_ok and bool(bad.degrade_reason),
          f"{bad.primary_ok}/{bad.degrade_reason}")

    engine.close()
    check("关闭后混音状态复位", not engine.running and not engine._mixer_split)


# ---------------------------------------------------------------- 裁剪峰值
def test_peaks() -> None:
    from ui.trimmer_dialog import compute_peaks, format_clock

    rate = 48000
    t = np.arange(int(rate * 3)) / rate
    wave = np.zeros_like(t)
    wave[:rate] = 0.5 * np.sin(2 * np.pi * 440 * t[:rate])
    wave[2 * rate:] = 0.2 * np.sin(2 * np.pi * 880 * t[2 * rate:])

    low, high = compute_peaks(wave, 800)
    check("峰值数组长度正确", low.shape == (800,) and high.shape == (800,), str(low.shape))
    check("有声段峰值明显", float(high[:260].max()) > 0.4, f"{float(high[:260].max()):.2f}")
    check("静音段峰值归零", float(high[300:500].max()) == 0.0,
          f"{float(high[300:500].max()):.3f}")

    started = time.perf_counter()
    compute_peaks(np.random.rand(rate * 600).astype(np.float32) - 0.5, 1600)
    elapsed = (time.perf_counter() - started) * 1000
    check("10 分钟音频峰值计算 < 1500ms", elapsed < 1500, f"{elapsed:.0f}ms")

    check("时间格式 mm:ss.cc", format_clock(1.234) == "00:01.23", format_clock(1.234))

def test_recorder() -> None:
    from core import capture_app
    from core.recorder import RecorderEngine

    rules = device_enum.merge_rules(None)
    engine = RecorderEngine()

    inputs = device_enum.list_devices("input", rules, show_all=False)
    target = next((c for c in inputs if c.is_default), inputs[0] if inputs else None)
    if target is None:
        print("[INFO] 没有输入设备，跳过模式 1")
    else:
        started = engine.start(MODE_MIC, target)
        check("模式 1 开始录音", started, engine.last_error or target.short_name)
        if started:
            time.sleep(1.2)
            result = engine.stop()
            check("模式 1 拿到数据", result is not None)
            if result is not None:
                data, rate = result
                check("录音统一 44100Hz", rate == SAMPLE_RATE, str(rate))
                check("录音时长合理", 0.8 < data.shape[0] / rate < 1.8,
                      f"{data.shape[0] / rate:.2f}s")
                print(f"        · 峰值 {audio_io.peak_dbfs(data):.1f} dBFS, "
                      f"采集率 {engine.capture_rate}")

    loops = device_enum.list_devices("loopback", rules, show_all=False)
    if loops:
        engine2 = RecorderEngine()

        def _is_real_speaker(choice) -> bool:  # noqa: ANN001
            name = choice.name.lower()
            return any(hint in name for hint in ("c-media", "realtek", "扬声器", "speaker"))

        loop_target = next((c for c in loops if _is_real_speaker(c)), None)
        if loop_target is None:
            loop_target = next((c for c in loops if c.is_default), loops[0])
        # 先放一段测试音到**同一个扬声器**，保证 loopback 有真实信号
        # （设备空闲时 WASAPI loopback 可能一帧都不给）
        from core.audio_engine import ROUTE_LOCAL, AudioEngine, OutputRoute

        source_name = loop_target.name.replace(" [Loopback]", "").replace("[Loopback]", "").strip()
        tone_engine = AudioEngine()
        tone_engine.configure_rules(rules)
        tone_engine.set_route(OutputRoute(mode=ROUTE_LOCAL, primary_name=source_name))
        started2 = engine2.start(MODE_SYSTEM, loop_target)
        check("模式 2 能开流", started2, engine2.last_error or loop_target.short_name)
        if started2:
            time.sleep(0.2)
            tone_engine.play_test_tone()
            time.sleep(0.8)
            result2 = engine2.stop()
            check("模式 2 拿到数据", result2 is not None,
                  str(None if result2 is None else result2[0].shape))
        tone_engine.close()

    capable, reason = capture_app.probe_capability(force=True)
    print(f"[INFO] 模式 3（进程 loopback）：{'可用' if capable else '不可用 —— ' + reason}")
    if capable:
        engine3 = RecorderEngine()
        apps = __import__("core.app_enum", fromlist=["app_enum"]).list_audio_apps(
            include_inferred=False
        )
        if apps:
            started3 = engine3.start(MODE_APP, apps[0])
            check("模式 3 能开始录单个应用", started3, engine3.last_error or apps[0].label())
            if started3:
                time.sleep(1.0)
                check("模式 3 停止拿到数据", engine3.stop() is not None)
    else:
        import os

        engine4 = RecorderEngine()
        check("模式 3 不可用时明确失败（不静默）", engine4.start(MODE_APP, os.getpid()) is False,
              str(engine4.last_error)[:80])


# ---------------------------------------------------------------- 热键
def test_hotkeys() -> None:
    import os

    from core.hotkey_manager import (
        RECORD_ID,
        Binding,
        HotkeyManager,
        display_hotkey,
        format_hotkey,
        parse_hotkey,
    )

    for text in ("ctrl+alt+1", "f9", "numpad 3", "ctrl+shift+f12"):
        keys = parse_hotkey(text)
        check(f"热键解析往返 {text}", parse_hotkey(format_hotkey(keys)) == keys)
    check("热键显示格式", display_hotkey("ctrl+alt+1") == "Ctrl + Alt + 1",
          display_hotkey("ctrl+alt+1"))

    presses: list[str] = []
    releases: list[str] = []
    manager = HotkeyManager(presses.append, releases.append, lambda _m: None)
    manager.start()

    states = manager.apply([
        Binding(RECORD_ID, "f9", "record"),
        Binding("clip-a", "ctrl+shift+f11", "toggle"),
        Binding("clip-b", "ctrl+shift+f12", "hold"),
        Binding("clip-c", "ctrl+shift+f11", "toggle"),   # 与 clip-a 重复
        Binding("clip-d", "f9", "toggle"),               # 与录音键重复
    ])
    # F9 可能被正在运行的本程序实例占用：那属于环境状态，不算失败
    record_state = states[RECORD_ID]
    check("录音键注册成功（被占用时降级为钩子也算正常）",
          record_state.status in ("ok", "occupied"),
          f"{record_state.status}/{record_state.backend}")
    if record_state.status == "occupied":
        print("[INFO] F9 已被其他程序占用（可能本程序正在运行），改用键盘钩子")
    check("音效键注册成功", states["clip-a"].status == "ok")
    check("按住模式走钩子", states["clip-b"].backend == "hook", states["clip-b"].backend)
    check("音效键互相重复被标记", states["clip-c"].status == "duplicate",
          states["clip-c"].status)
    check("与录音键冲突被标记",
          states["clip-d"].status == "duplicate"
          and "录音" in states["clip-d"].message,
          states["clip-d"].message)

    # 自建底层钩子（首选通道）：装得上就应该是它接管，且匹配/挂起都正确
    check("自建底层键盘钩子可用（后台热键的前提）", manager._hook_active,
          "llhook active" if manager._hook_active else "fallback")
    if manager._hook_active:
        presses.clear()
        releases.clear()
        hook = manager._llhook
        # 模拟 ctrl+shift+f11（clip-a）—— 直接喂 VK，等价于系统回调
        for vk in (0x11, 0x10, 0x7A):
            hook._on_down(vk)
        for vk in (0x7A, 0x10, 0x11):
            hook._on_up(vk)
        check("底层钩子：按下触发音效键", "clip-a" in presses, str(presses))
        # 按住模式：ctrl+shift+f12 → key-up 必须产生 release
        presses.clear()
        releases.clear()
        for vk in (0x11, 0x10, 0x7B):
            hook._on_down(vk)
        check("底层钩子：按住模式按下触发", "clip-b" in presses, str(presses))
        for vk in (0x7B, 0x10, 0x11):
            hook._on_up(vk)
        check("底层钩子：松开触发 release", "clip-b" in releases, str(releases))
        # 修饰键不匹配时不应触发
        presses.clear()
        for vk in (0x11, 0x7A):
            hook._on_down(vk)
        hook._on_up(0x7A)
        hook._on_up(0x11)
        check("底层钩子：修饰键不匹配不触发", not presses, str(presses))

    # 模拟 WM_HOTKEY 投递（真实按键在当前沙箱被系统过滤，这里走系统边界）
    import ctypes

    win32_ids = list(manager._win32._id_to_key)
    if win32_ids:
        time.sleep(0.2)          # 让双通道去重窗口过期
        presses.clear()
        hotkey_id = win32_ids[0]
        ctypes.windll.user32.PostThreadMessageW(
            manager._win32._thread.ident, 0x0312, hotkey_id, 0
        )
        deadline = time.time() + 1.0
        while time.time() < deadline and not presses:
            time.sleep(0.01)
        check("Win32 消息派发到回调", bool(presses), str(presses))
    else:
        print("[INFO] 本次没有走 Win32 注册的热键，跳过消息派发测试")

    # 钩子通道：喂合成事件验证按住/松开（只在降级到 keyboard 钩子时才需要）
    if not manager._hook_active:
        presses.clear()
        releases.clear()
        manager._hook._on_event(__import__("types").SimpleNamespace(name="ctrl", event_type="down"))
        manager._hook._on_event(__import__("types").SimpleNamespace(name="shift", event_type="down"))
        manager._hook._on_event(__import__("types").SimpleNamespace(name="f12", event_type="down"))
        check("钩子按下触发", "clip-b" in presses, str(presses))
        manager._hook._on_event(__import__("types").SimpleNamespace(name="f12", event_type="up"))
        check("松开触发 release（按住模式）", "clip-b" in releases, str(releases))
    else:
        print("[INFO] 使用自建底层钩子，跳过 keyboard 库钩子测试")

    manager.stop()
    check("停止热键管理器", not manager.started)


def test_migration() -> None:
    """1.2.0：老版本把数据放在程序目录，首次启动应复制到数据目录。"""
    import shutil as _shutil

    from core import paths

    legacy_audio = WORK / "audio"
    legacy_audio.mkdir(parents=True, exist_ok=True)
    legacy_wav = legacy_audio / "rec_legacy.wav"
    audio_io.write_wav(legacy_wav, sine(0.2), SAMPLE_RATE)
    (WORK / "config.json").write_text('{"version": 1}', encoding="utf-8")

    data = WORK / "data"
    _shutil.rmtree(data, ignore_errors=True)

    first = paths.migrate_legacy_data()
    check("首次启动迁移旧数据", bool(first["migrated"]) and first["files"] >= 1,
          str(first))
    check("录音被复制到数据目录",
          (data / "audio" / "rec_legacy.wav").is_file())
    check("配置被复制到数据目录", (data / "config.json").is_file())
    check("旧文件保留（可回滚）", legacy_wav.is_file())

    second = paths.migrate_legacy_data()
    check("迁移只执行一次", not bool(second["migrated"]), str(second))


# ---------------------------------------------------------------- 主流程
def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "audio").mkdir(exist_ok=True)
    print(f"项目根目录：{ROOT}")
    try:
        # 让 clip_library/paths 落到 .selftest 下，避免污染真实 audio/
        import core.paths as paths_module

        paths_module.base_dir = lambda: WORK  # type: ignore[assignment]
        paths_module.audio_dir = lambda: (WORK / "audio")  # type: ignore[assignment]
        paths_module.config_path = lambda: WORK / "config.json"  # type: ignore[assignment]
        paths_module.temp_dir = lambda: (WORK / "temp")  # type: ignore[assignment]
        paths_module.resolve_relative = (  # type: ignore[assignment]
            lambda rel: Path(rel) if Path(rel).is_absolute() else WORK / Path(rel)
        )
        paths_module.to_relative = lambda abs_path: Path(abs_path).as_posix()  # type: ignore[assignment]
        # 1.2.0：用户数据目录（迁移测试用；也保证不写真实 %APPDATA%）
        paths_module.data_dir = lambda: (WORK / "data")  # type: ignore[assignment]
        import os as _os
        _os.environ["SOUNDBOARD_DATA_DIR"] = str(WORK / "data")

        test_config()
        test_library()
        test_devices()
        test_engine()
        test_routes()
        test_mixer()
        test_peaks()
        test_recorder()
        test_hotkeys()
        test_migration()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：" + "、".join(FAILED))
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
