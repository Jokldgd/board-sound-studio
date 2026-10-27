"""进程 loopback 深度诊断（换机排查时直接跑这个）。

它按顺序验证四件事，能明确区分"我们的代码有问题"还是"系统拒绝"：

    1. 系统层面：Windows build 是否 >= 19041、Mmdevapi 是否导出 ActivateAudioInterfaceAsync
    2. 经典激活路径：IMMDevice::Activate → IAudioClient → GetMixFormat
       （这条走通 = COM 封装、设备枚举、音频栈都正常）
    3. 异步 Default 激活：ActivateAudioInterfaceAsync（不传激活参数）→ HRESULT
    4. 异步进程 loopback 激活：带 AUDIOCLIENT_ACTIVATION_PARAMS 目标 PID → HRESULT
       成功的话再真录 1 秒，报告收到多少帧

判定：
    * 2 通 + 3/4 都失败 → 系统侧拒绝异步激活（不是本项目的问题）
    * 2 失败           → 本机音频栈/设备有问题，先查声卡与 Audio 服务
    * 4 成功           → 模式 3 可用（真机上就应该能录）

用法：
    python tools\\diag_process_loopback.py            # 目标 = 自己
    python tools\\diag_process_loopback.py 12345      # 目标 = 指定 PID
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import app_loopback as al  # noqa: E402

SEP = "=" * 78


def _default_render_endpoint() -> tuple[str, int]:
    """返回 (设备接口 ID, 设备索引)。用经典的 IMMDeviceEnumerator 路径。"""
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitializeEx(None, 0)

    enumerator = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(al.CLSID_MMDeviceEnumerator), None, 1,
        ctypes.byref(al.IID_IMMDeviceEnumerator), ctypes.byref(enumerator),
    )
    if hr != 0:
        raise RuntimeError(f"CoCreateInstance 失败：{al.hresult_text(hr)}")

    device = ctypes.c_void_p()
    hr = al._com_call(enumerator.value, 4, ctypes.c_long,
                      (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                      (0, 0, ctypes.byref(device)))
    if hr != 0 or not device.value:
        raise RuntimeError(f"GetDefaultAudioEndpoint 失败：{al.hresult_text(hr)}")

    device_id = ctypes.c_wchar_p()
    hr = al._com_call(device.value, 5, ctypes.c_long,
                      (ctypes.POINTER(ctypes.c_wchar_p),), (ctypes.byref(device_id),))
    if hr != 0:
        raise RuntimeError(f"GetId 失败：{al.hresult_text(hr)}")

    # 顺便取一下 "默认设备索引"（仅用于打印，帮助确认选对了设备）
    index = -1
    try:
        import sounddevice as sd

        index = int(sd.query_devices(kind="output")["index"])
    except Exception:
        pass

    al._com_release(device.value)
    al._com_release(enumerator.value)
    return (device_id.value or ""), index


def _classic_activate(device_id: str) -> tuple[int, str]:
    """IMMDevice::Activate(IID_IAudioClient) —— 经典路径，验证录音栈本身。"""
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    enumerator = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(al.CLSID_MMDeviceEnumerator), None, 1,
        ctypes.byref(al.IID_IMMDeviceEnumerator), ctypes.byref(enumerator),
    )
    if hr != 0:
        return hr, "创建枚举器失败"

    device = ctypes.c_void_p()
    hr = al._com_call(enumerator.value, 4, ctypes.c_long,
                      (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                      (0, 0, ctypes.byref(device)))
    if hr != 0 or not device.value:
        al._com_release(enumerator.value)
        return hr, "取默认渲染端点失败"

    client = ctypes.c_void_p()
    hr = al._com_call(
        device.value, 3, ctypes.c_long,
        (ctypes.POINTER(al.GUID), ctypes.c_ulong, ctypes.c_void_p,
         ctypes.POINTER(ctypes.c_void_p)),
        (ctypes.byref(al.IID_IAudioClient), 0x17, None, ctypes.byref(client)),
    )
    detail = ""
    if hr == 0 and client.value:
        fmt = ctypes.POINTER(al.WAVEFORMATEX)()
        hr2 = al._com_call(client.value, 8, ctypes.c_long,
                           (ctypes.POINTER(ctypes.POINTER(al.WAVEFORMATEX)),),
                           (ctypes.byref(fmt),))
        if hr2 == 0 and fmt:
            detail = (f"{fmt.contents.nSamplesPerSec}Hz / {fmt.contents.nChannels}ch / "
                      f"{fmt.contents.wBitsPerSample}bit / tag={fmt.contents.wFormatTag}")
        al._com_release(client.value)
    elif not detail:
        detail = "拿不到 IAudioClient"

    al._com_release(device.value)
    al._com_release(enumerator.value)
    return hr, detail


def _async_activate(device_id: str, pid: int | None) -> tuple[int, str]:
    """ActivateAudioInterfaceAsync：pid=None 表示 Default 激活。"""
    mmdevapi = ctypes.WinDLL("Mmdevapi")
    activate = mmdevapi.ActivateAudioInterfaceAsync
    activate.restype = ctypes.c_long
    activate.argtypes = [
        ctypes.c_wchar_p, ctypes.POINTER(al.GUID), ctypes.POINTER(al.PROPVARIANT),
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]

    handler = al._CompletionHandler()
    al._alive_handlers.append(handler)
    operation = ctypes.c_void_p()
    params = None
    prop = None

    if pid is None:
        hr = activate(device_id, ctypes.byref(al.IID_IAudioClient), None,
                      handler.ptr, ctypes.byref(operation))
    else:
        params = al.AUDIOCLIENT_ACTIVATION_PARAMS()
        params.ActivationType = al.AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        params.ProcessLoopbackParams.TargetProcessId = int(pid)
        params.ProcessLoopbackParams.ProcessLoopbackMode = (
            al.PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        )
        prop = al.PROPVARIANT()
        prop.vt = al.VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(params)
        prop.blob.pBlobData = ctypes.cast(ctypes.byref(params), ctypes.c_void_p)
        hr = activate(device_id, ctypes.byref(al.IID_IAudioClient), ctypes.byref(prop),
                      handler.ptr, ctypes.byref(operation))

    if hr != 0:
        return hr, "调用本身就失败"
    if not handler.done.wait(3.0):
        return 0x800705B4, "等待完成回调超时（3 秒）"
    if handler.client_ptr:
        al._com_release(handler.client_ptr)
        return 0, "成功拿到 IAudioClient"
    return handler.hresult.value, "回调返回失败"


def main() -> int:
    target_pid = int(sys.argv[1]) if len(sys.argv) > 1 else ctypes.windll.kernel32.GetCurrentProcessId()
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    emit(SEP)
    emit("进程 loopback 深度诊断")
    emit(SEP)
    emit(f"Python           : {sys.version.split()[0]}")
    emit(f"Windows build    : {sys.getwindowsversion().build}"
         f"（进程 loopback 需要 >= 19041）")
    emit(f"目标 PID         : {target_pid}")

    # 1) 系统层面
    emit(f"\n[1] 系统能力：{al.is_supported()}")
    try:
        device_id, device_index = _default_render_endpoint()
        emit(f"    默认渲染端点：{device_id}")
        if device_index >= 0:
            emit(f"    sounddevice 索引：{device_index}")
    except Exception as exc:
        emit(f"    ❌ 取默认端点失败：{exc}")
        print("\n".join(lines))
        return 1

    # 2) 经典激活
    classic_hr, classic_detail = _classic_activate(device_id)
    emit(f"\n[2] 经典激活 IMMDevice::Activate(IID_IAudioClient)：")
    emit(f"    HRESULT : {'S_OK' if classic_hr == 0 else al.hresult_text(classic_hr)}")
    emit(f"    混音格式: {classic_detail or '（未取到）'}")

    # 3) 异步 Default 激活
    default_hr, default_detail = _async_activate(device_id, None)
    emit(f"\n[3] 异步激活 ActivateAudioInterfaceAsync（Default，无参数）：")
    emit(f"    HRESULT : {'S_OK' if default_hr == 0 else al.hresult_text(default_hr)}")
    emit(f"    说明    : {default_detail}")

    # 4) 异步进程 loopback 激活
    loop_hr, loop_detail = _async_activate(device_id, target_pid)
    emit(f"\n[4] 异步进程 loopback 激活（TargetProcessId={target_pid}）：")
    emit(f"    HRESULT : {'S_OK' if loop_hr == 0 else al.hresult_text(loop_hr)}")
    emit(f"    说明    : {loop_detail}")

    # 5) 实采 1 秒（仅当第 4 步成功）
    if loop_hr == 0:
        blocks = {"count": 0, "frames": 0}

        def on_block(block) -> None:  # noqa: ANN001
            blocks["count"] += 1
            blocks["frames"] += int(block.shape[0])

        capture = al.ProcessLoopbackCapture(target_pid, on_block)
        started = capture.start(timeout=3.0)
        time.sleep(1.0)
        capture.stop()
        emit(f"\n[5] 实采 1 秒：started={started} error={capture.error}")
        emit(f"    收到 {blocks['count']} 个数据块 / {blocks['frames']} 帧"
             f" @ {capture.samplerate}Hz/{capture.channels}ch")
        verdict = "模式 3 可用 ✅" if started and not capture.error else "激活成功但采集有问题 ⚠"
    else:
        verdict = ""

    # 结论
    emit("\n" + SEP)
    emit("结论")
    emit(SEP)
    if classic_hr == 0 and default_hr != 0 and loop_hr != 0:
        emit("经典激活正常、异步激活失败 → **系统侧拒绝了 ActivateAudioInterfaceAsync**，")
        emit("不是本项目的封装问题：代码在支持的系统上会自动启用模式 3。")
        emit("可以尝试：更新声卡驱动 / 确认 Windows 版本 ≥ 19041 / 关注后续系统更新。")
        emit("在此期间请用模式 1（麦克风/虚拟声卡）或模式 2（系统声音）。")
    elif classic_hr != 0:
        emit("经典激活就失败了 → 本机音频栈/设备异常：先检查声卡驱动、")
        emit("Windows Audio 服务（Audiosrv）是否运行、默认播放设备是否正常。")
    elif default_hr == 0 and loop_hr != 0:
        emit("异步 Default 激活成功但进程 loopback 失败 → 该 PID 可能没有音频会话、")
        emit("已退出，或受 DRM/独占模式限制。换一个正在播放声音的应用再试。")
    else:
        emit(verdict or "按上面各步骤的 HRESULT 定位。")

    print(SEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
