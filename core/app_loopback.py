"""Windows **进程级 loopback**（只录某个应用的声音）的 ctypes 封装。

官方能力叫 *Application Loopback*：把一个音频设备当作"虚拟采集端"，但只把
指定进程（及其子进程）的音频渲染结果送进来。Python 没有现成 pip 包，这里用
ctypes 直接调 COM / Win32。

──────────────────────── 关键 API ────────────────────────
导出函数（Mmdevapi.dll，Windows 8+）：
    HRESULT ActivateAudioInterfaceAsync(
        LPCWSTR deviceInterfacePath,      // 渲染端点的接口 ID（IMMDevice::GetId）
        REFIID riid,                      // 固定 IID_IAudioClient
        PROPVARIANT* activationParams,    // 里装 AUDIOCLIENT_ACTIVATION_PARAMS
        IActivateAudioInterfaceCompletionHandler* handler,  // 我们自己实现的回调
        IActivateAudioInterfaceAsyncOperation** operation);

COM 接口（vtable 索引从 IUnknown 的 3 个方法之后开始计数）：
    IMMDeviceEnumerator  {A95664D2-9614-4F35-A746-DE8DB63617E6}
        [4] GetDefaultAudioEndpoint(EDataFlow, ERole, IMMDevice**)
    IMMDevice            {D666063F-1587-4E43-81F1-B948E807363F}
        [5] GetId(LPWSTR*)
    IActivateAudioInterfaceAsyncOperation {72A22D78-CDE4-431D-B8CC-843A71199B6D}
        [3] GetActivateResult(HRESULT*, IUnknown**)
    IActivateAudioInterfaceCompletionHandler {41D949AB-9862-444A-80F6-C261334DA5EB}
        [3] ActivateCompleted(IActivateAudioInterfaceAsyncOperation*)  ← 由我们实现
    IAudioClient         {1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}
        [8]  GetMixFormat(WAVEFORMATEX**)
        [10] Start()   [11] Stop()   [14] GetService(REFIID, void**)
    IAudioCaptureClient  {C8ADBD64-E71E-48A0-A4DE-185C395CD317}
        [3] GetBuffer(...)  [4] ReleaseBuffer(n)  [5] GetNextPacketSize(UINT32*)

调用的 12 步顺序见 ``ProcessLoopbackCapture._run`` 里的注释。

为什么用轮询而不是事件回调：
    进程 loopback **不支持事件驱动**（文档明确写了不能配 AUDCLNT_STREAMFLAGS_EVENTCALLBACK），
    所以用 AUDCLNT_STREAMFLAGS_LOOPBACK + 每 10ms 调 GetNextPacketSize 拉数据。

系统要求：Windows 10 20H1（build 19041）及以上。低版本直接不支持，
``is_supported()`` 会返回 False，界面据此禁用模式 3。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
import uuid
from typing import Callable

import numpy as np

# ------------------------------------------------------------------ GUID
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, text: str) -> "GUID":
        u = uuid.UUID(text)
        guid = cls()
        guid.Data1 = u.time_low
        guid.Data2 = u.time_mid
        guid.Data3 = u.time_hi_version
        # 后 8 字节按大端顺序拆（与内存布局一致）
        bytes_be = u.bytes[8:]
        for index, byte in enumerate(bytes_be):
            guid.Data4[index] = byte
        return guid

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GUID):
            return NotImplemented
        return ctypes.string_at(ctypes.byref(self), ctypes.sizeof(GUID)) == ctypes.string_at(
            ctypes.byref(other), ctypes.sizeof(GUID)
        )


IID_IAudioClient = GUID.from_string("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = GUID.from_string("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
IID_IMMDeviceEnumerator = GUID.from_string("A95664D2-9614-4F35-A746-DE8DB63617E6")
CLSID_MMDeviceEnumerator = GUID.from_string("BCDE0395-E52F-467C-8E3D-C4579291692E")

# 常量
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x00000002
VT_BLOB = 65
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
S_OK = 0
E_NOINTERFACE = ctypes.c_long(0x80004002).value
REFERENCE_TIME = ctypes.c_longlong
REFTIMES_PER_SEC = 10_000_000
#: 缓冲区时长：20ms（足够稳，延迟也可接受）
BUFFER_DURATION_HNS = int(0.02 * REFTIMES_PER_SEC)


# ------------------------------------------------------------------ 结构体
class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_ushort),
        ("nChannels", ctypes.c_ushort),
        ("nSamplesPerSec", ctypes.c_ulong),
        ("nAvgBytesPerSec", ctypes.c_ulong),
        ("nBlockAlign", ctypes.c_ushort),
        ("wBitsPerSample", ctypes.c_ushort),
        ("cbSize", ctypes.c_ushort),
    ]


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    """告诉系统"要哪个进程的声音、包含还是排除它的子进程"。"""

    _fields_ = [
        ("TargetProcessId", wt.DWORD),
        ("ProcessLoopbackMode", ctypes.c_int),
    ]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    """ActivateAudioInterfaceAsync 的激活参数（打包进 PROPVARIANT 的 BLOB）。"""

    class _Union(ctypes.Union):
        _fields_ = [("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]

    _anonymous_ = ("u",)
    _fields_ = [
        ("ActivationType", ctypes.c_int),   # 1 = PROCESS_LOOPBACK
        ("u", _Union),
    ]


class _PropVariantBlob(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("pBlobData", ctypes.c_void_p)]


class _PropVariantUnion(ctypes.Union):
    #: VT_BLOB 分支 + 16 字节对齐垫片（PROPVARIANT 联合体在 x64 上是 16 字节）
    _fields_ = [("blob", _PropVariantBlob), ("pad", ctypes.c_byte * 16)]


class PROPVARIANT(ctypes.Structure):
    """只用到 VT_BLOB 分支；x64 下总长 24 字节，BLOB 在偏移 8。"""

    _anonymous_ = ("u",)
    _fields_ = [
        ("vt", ctypes.c_ushort),          # VT_BLOB = 65
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("u", _PropVariantUnion),
    ]


# ------------------------------------------------------------------ HRESULT
def hresult_ok(hr: int) -> bool:
    return hr >= 0


def hresult_text(hr: int) -> str:
    """把 HRESULT 变成能读懂的一行（含常见错误码解释）。"""
    code = ctypes.c_ulong(hr & 0xFFFFFFFF).value
    try:
        message = ctypes.FormatError(code).strip()
    except Exception:
        message = ""
    known = {
        0x80070005: "拒绝访问（权限不足）",
        0x80070057: "参数无效（PID 可能已退出）",
        0x88890008: "音频格式不受支持",
        0x8889000A: "设备被独占",
        0x88890017: "音频客户端初始化失败",
        0x80004005: "未指定的失败（系统/驱动不支持进程 loopback）",
        0x80040154: "找不到所需的 COM 组件",
    }
    label = known.get(code)
    if label:
        return f"0x{code:08X} {label}" + (f"（{message}）" if message else "")
    return f"0x{code:08X}" + (f"（{message}）" if message else "")


class AppLoopbackError(Exception):
    """进程 loopback 失败；``hresult`` 供界面显示原因。"""

    def __init__(self, message: str, hresult: int | None = None) -> None:
        super().__init__(message)
        self.hresult = hresult


# ------------------------------------------------------------------ 支持检测
def is_supported() -> tuple[bool, str]:
    """当前系统是否支持进程 loopback（不看具体进程）。"""
    version = getattr(ctypes.windll.ntdll, "RtlGetVersion", None)  # noqa: F841
    try:
        build = ctypes.c_ulong(0)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # GetVersionExW 在 Win8.1+ 会被"撒谎"，所以用 RtlGetVersion 取真实版本
        class OSVERSIONINFOW(ctypes.Structure):
            _fields_ = [
                ("dwOSVersionInfoSize", ctypes.c_ulong),
                ("dwMajorVersion", ctypes.c_ulong),
                ("dwMinorVersion", ctypes.c_ulong),
                ("dwBuildNumber", ctypes.c_ulong),
                ("dwPlatformId", ctypes.c_ulong),
                ("szCSDVersion", ctypes.c_wchar * 128),
            ]

        info = OSVERSIONINFOW()
        info.dwOSVersionInfoSize = ctypes.sizeof(OSVERSIONINFOW)
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.RtlGetVersion(ctypes.byref(info))
        build.value = info.dwBuildNumber
    except Exception:
        return False, "无法获取 Windows 版本号"

    if info.dwBuildNumber < 19041:
        return False, (
            f"需要 Windows 10 20H1（build 19041）及以上，当前 build {info.dwBuildNumber}，"
            "模式 3 不可用"
        )
    try:
        mmdevapi = ctypes.WinDLL("Mmdevapi")
        getattr(mmdevapi, "ActivateAudioInterfaceAsync")
    except Exception:
        return False, "系统缺少 ActivateAudioInterfaceAsync（Mmdevapi.dll），模式 3 不可用"
    return True, ""


# ------------------------------------------------------------------ COM 调用小工具
SUCCEEDED = staticmethod(lambda hr: hr >= 0)  # 便于阅读


def _vtbl_func(ptr: int, index: int, restype, *argtypes):
    """从 COM 对象指针取出 vtable 第 index 个方法，返回可调用原型。"""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    address = vtable[index]
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(address)


def _com_call(ptr: int, index: int, restype, argtypes: tuple, args: tuple):
    return _vtbl_func(ptr, index, restype, *argtypes)(ptr, *args)


def _com_release(ptr: int | None) -> None:
    """调用 vtable[2] === IUnknown::Release。"""
    if not ptr:
        return
    try:
        release = _vtbl_func(ptr, 2, ctypes.c_ulong)
        release(ptr)
    except Exception:
        pass


# ------------------------------------------------------------------ 完成回调对象
_QI = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                         ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
_ADDREF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_ACTIVATE_COMPLETED = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)


class _HandlerVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", _QI),
        ("AddRef", _ADDREF),
        ("Release", _RELEASE),
        ("ActivateCompleted", _ACTIVATE_COMPLETED),
    ]


class _HandlerObject(ctypes.Structure):
    """内存布局 = COM 对象的布局：首字段是指向 vtable 的指针。"""

    _fields_ = [
        ("lpVtbl", ctypes.POINTER(_HandlerVtbl)),
        ("ref_count", ctypes.c_ulong),
        ("py_self", ctypes.py_object),   # 从回调里找回 Python 对象
    ]


#: 防止 vtable / 对象在异步操作完成前被回收
_alive_handlers: list = []


class _CompletionHandler:
    """实现 IActivateAudioInterfaceCompletionHandler 的最小对象。

    调用方把 ``self.ptr``（一个 _HandlerObject*）传给
    ActivateAudioInterfaceAsync；系统会在工作线程上回调 ActivateCompleted。
    """

    def __init__(self) -> None:
        self.done = threading.Event()
        self.hresult = ctypes.c_long(0)
        self.client_ptr: int | None = None
        self.debug: dict[str, object] = {}

        self._vtbl = _HandlerVtbl(
            _QI(self._query_interface),
            _ADDREF(self._add_ref),
            _RELEASE(self._release),
            _ACTIVATE_COMPLETED(self._activate_completed),
        )
        self._obj = _HandlerObject()
        self._obj.lpVtbl = ctypes.pointer(self._vtbl)
        self._obj.ref_count = 1
        self._obj.py_self = self
        self.ptr = ctypes.cast(ctypes.byref(self._obj), ctypes.c_void_p)

    # ---- IUnknown ----
    def _query_interface(self, this, riid, out):
        try:
            requested = riid.contents
            if requested == IID_IMMDeviceEnumerator or requested.Data1 == 0:  # IUnknown
                out[0] = this
                self._obj.ref_count += 1
                return S_OK
            # 我们也只声明支持 IActivateAudioInterfaceCompletionHandler：
            # 直接无条件返回 S_OK 更省事——系统只会按这个接口用它。
            out[0] = this
            self._obj.ref_count += 1
            return S_OK
        except Exception:
            return E_NOINTERFACE

    def _add_ref(self, this):
        self._obj.ref_count += 1
        return self._obj.ref_count

    def _release(self, this):
        if self._obj.ref_count > 0:
            self._obj.ref_count -= 1
        # 不真的释放内存（交给 GC），但按 COM 约定返回剩余引用计数
        return max(1, self._obj.ref_count)

    # ---- IActivateAudioInterfaceCompletionHandler ----
    def _activate_completed(self, this, operation_ptr):
        """系统回调：取出激活结果（HRESULT + IAudioClient），唤醒等待线程。"""
        try:
            if not operation_ptr:
                self.hresult.value = ctypes.c_long(0x80004005).value
                self.done.set()
                return S_OK
            activate_result = ctypes.c_long(0)
            interface = ctypes.c_void_p()
            hr = _com_call(
                operation_ptr, 3, ctypes.c_long,
                (ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_void_p)),
                (ctypes.byref(activate_result), ctypes.byref(interface)),
            )
            self.debug["get_activate_result_hr"] = hr
            self.debug["activate_result"] = activate_result.value
            self.debug["interface"] = interface.value
            if hresult_ok(hr) and hresult_ok(activate_result.value):
                self.hresult.value = 0
                self.client_ptr = interface.value
            elif not hresult_ok(activate_result.value):
                # 内层 HRESULT 才是真正的失败原因（外层 S_OK 只代表调用完成）
                self.hresult.value = activate_result.value
                self.client_ptr = None
            else:
                self.hresult.value = hr
                self.client_ptr = None
        except Exception as exc:  # pragma: no cover
            self.hresult.value = ctypes.c_long(0x80004005).value
            self.client_ptr = None
            setattr(self, "_exception", exc)
        finally:
            self.done.set()
        return S_OK


# ------------------------------------------------------------------ 采集器
class ProcessLoopbackCapture:
    """对一个 PID 做进程级 loopback 采集。

    用法::

        cap = ProcessLoopbackCapture(pid, on_block, on_error)
        cap.start()          # 非阻塞，内部起线程
        ...
        cap.stop()           # 停止并回收

    ``on_block`` 收到 ``np.float32`` 的 ``(frames, channels)`` 数组；
    ``on_error`` 收到字符串（进程退出、设备错误等）。
    """

    def __init__(
        self,
        pid: int,
        on_block: Callable[[np.ndarray], None],
        on_error: Callable[[str], None] | None = None,
        exclude_tree: bool = False,
        poll_interval: float = 0.01,
    ) -> None:
        self.pid = int(pid)
        self._on_block = on_block
        self._on_error = on_error or (lambda message: None)
        self._mode = (
            PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE if exclude_tree
            else PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        )
        self._poll = max(0.002, float(poll_interval))

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.samplerate = 48000
        self.channels = 2
        self.error: str | None = None
        self.debug: dict[str, object] = {}
        self.started = threading.Event()

    # ---- 生命周期 ----
    def start(self, timeout: float = 3.0) -> bool:
        """启动采集线程并等待它真正开始工作。"""
        if self._thread is not None:
            return True
        self._stop.clear()
        self.error = None
        self.started.clear()
        self._thread = threading.Thread(target=self._run, name="proc-loopback", daemon=True)
        self._thread.start()
        # 等"真的开始出数据"或失败；超时不算失败（可能应用暂时没声音）
        self.started.wait(timeout)
        return self.error is None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _fail(self, message: str, hresult: int | None = None) -> None:
        self.error = message
        try:
            self._on_error(message)
        except Exception:
            pass
        self.started.set()

    # ---- 线程主体：12 步 ----
    def _run(self) -> None:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        mmdevapi = ctypes.WinDLL("Mmdevapi")
        enumerator = None
        device = None
        client = None
        capture = None
        handler = None
        device_id_ptr = ctypes.c_wchar_p()
        mix_format = None

        # 1) 本线程初始化 COM（MTA）。所有 COM 调用都留在这个线程里。
        hr = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED = 0
        com_initialized = hresult_ok(hr) or hr == 1  # S_FALSE 也算成功
        try:
            # 2) 建设备枚举器
            enumerator = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_MMDeviceEnumerator), None, 1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enumerator),
            )
            if not hresult_ok(hr) or not enumerator.value:
                self._fail(f"创建音频设备枚举器失败：{hresult_text(hr)}", hr)
                return

            # 3) 取默认渲染端点（eRender=0, eConsole=0）→ 拿它的接口 ID 字符串
            device = ctypes.c_void_p()
            hr = _com_call(
                enumerator.value, 4, ctypes.c_long,
                (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                (0, 0, ctypes.byref(device)),
            )
            if not hresult_ok(hr) or not device.value:
                self._fail(f"获取默认播放设备失败：{hresult_text(hr)}", hr)
                return

            hr = _com_call(
                device.value, 5, ctypes.c_long,
                (ctypes.POINTER(ctypes.c_wchar_p),),
                (ctypes.byref(device_id_ptr),),
            )
            if not hresult_ok(hr) or not device_id_ptr.value:
                self._fail(f"获取设备 ID 失败：{hresult_text(hr)}", hr)
                return
            self.debug["device_id"] = device_id_ptr.value
            self.debug["com_init_hr"] = hr

            # 4) 填激活参数：目标 PID + 包含子树
            params = AUDIOCLIENT_ACTIVATION_PARAMS()
            params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
            params.ProcessLoopbackParams.TargetProcessId = self.pid
            params.ProcessLoopbackParams.ProcessLoopbackMode = self._mode

            # 5) 包成 PROPVARIANT(VT_BLOB)
            prop = PROPVARIANT()
            prop.vt = VT_BLOB
            prop.blob.cbSize = ctypes.sizeof(params)
            prop.blob.pBlobData = ctypes.cast(ctypes.byref(params), ctypes.c_void_p)

            # 6-7) 建回调对象并调 ActivateAudioInterfaceAsync
            handler = _CompletionHandler()
            _alive_handlers.append(handler)

            activate = mmdevapi.ActivateAudioInterfaceAsync
            activate.restype = ctypes.c_long
            activate.argtypes = [
                ctypes.c_wchar_p,
                ctypes.POINTER(GUID),
                ctypes.POINTER(PROPVARIANT),
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            operation = ctypes.c_void_p()
            hr = activate(
                device_id_ptr.value,
                ctypes.byref(IID_IAudioClient),
                ctypes.byref(prop),
                handler.ptr,
                ctypes.byref(operation),
            )
            if not hresult_ok(hr):
                self._fail(f"ActivateAudioInterfaceAsync 调用失败：{hresult_text(hr)}", hr)
                return

            # 8) 等系统回调（工作线程上完成），取出结果
            if not handler.done.wait(3.0):
                self._fail("等待进程 loopback 激活超时（3 秒）", None)
                return
            self.debug.update(handler.debug)
            if not hresult_ok(handler.hresult.value) or not handler.client_ptr:
                code = handler.hresult.value
                self._fail(f"激活进程 loopback 失败：{hresult_text(code)}", code)
                return
            client = ctypes.c_void_p(handler.client_ptr)

            # 9) 取混音格式 → 初始化（共享模式 + LOOPBACK 标志，20ms 缓冲）
            format_ptr = ctypes.POINTER(WAVEFORMATEX)()
            hr = _com_call(
                client.value, 8, ctypes.c_long,
                (ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX)),),
                (ctypes.byref(format_ptr),),
            )
            if not hresult_ok(hr) or not format_ptr:
                self._fail(f"获取混音格式失败：{hresult_text(hr)}", hr)
                return
            mix_format = format_ptr.contents
            self.samplerate = int(mix_format.nSamplesPerSec) or 48000
            self.channels = int(mix_format.nChannels) or 2

            hr = _com_call(
                client.value, 3, ctypes.c_long,
                (ctypes.c_int, wt.DWORD, REFERENCE_TIME, REFERENCE_TIME,
                 ctypes.POINTER(WAVEFORMATEX), ctypes.c_void_p),
                (AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK,
                 REFERENCE_TIME(BUFFER_DURATION_HNS), REFERENCE_TIME(0),
                 format_ptr, None),
            )
            if not hresult_ok(hr):
                self._fail(f"初始化 loopback 音频客户端失败：{hresult_text(hr)}", hr)
                return

            # 10) 取采集客户端并启动
            capture = ctypes.c_void_p()
            hr = _com_call(
                client.value, 14, ctypes.c_long,
                (ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)),
                (ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(capture)),
            )
            if not hresult_ok(hr) or not capture.value:
                self._fail(f"获取采集接口失败：{hresult_text(hr)}", hr)
                return

            hr = _com_call(client.value, 10, ctypes.c_long, (), ())
            if not hresult_ok(hr):
                self._fail(f"启动音频客户端失败：{hresult_text(hr)}", hr)
                return

            self.started.set()

            # 11) 采集循环（轮询）
            self._pump(capture.value, mix_format)

            # 12) 收尾
            _com_call(client.value, 11, ctypes.c_long, (), ())  # Stop()
        except Exception as exc:  # pragma: no cover
            self._fail(f"进程 loopback 异常：{exc}")
        finally:
            for ptr in (capture, client, device, enumerator):
                if ptr is not None:
                    _com_release(ptr.value if isinstance(ptr, ctypes.c_void_p) else ptr)
            try:
                if device_id_ptr.value:
                    ctypes.windll.ole32.CoTaskMemFree(device_id_ptr)
            except Exception:
                pass
            if com_initialized:
                try:
                    ole32.CoUninitialize()
                except Exception:
                    pass
            try:
                if handler is not None and handler in _alive_handlers:
                    _alive_handlers.remove(handler)
            except Exception:
                pass
            self.started.set()

    # ---- 拉数据 ----
    def _pump(self, capture_ptr: int, fmt: WAVEFORMATEX) -> None:
        get_next = _vtbl_func(
            capture_ptr, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_ulong)
        )
        get_buffer = _vtbl_func(
            capture_ptr, 3, ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
        )
        release_buffer = _vtbl_func(
            capture_ptr, 4, ctypes.c_long, ctypes.c_ulong
        )

        bytes_per_sample = max(1, int(fmt.wBitsPerSample) // 8)
        channels = max(1, int(fmt.nChannels))
        is_float = fmt.wFormatTag in (WAVE_FORMAT_IEEE_FLOAT, WAVE_FORMAT_EXTENSIBLE) and \
            bytes_per_sample == 4

        idle_rounds = 0
        while not self._stop.is_set():
            packet = ctypes.c_ulong(0)
            hr = get_next(capture_ptr, ctypes.byref(packet))
            if not hresult_ok(hr):
                self._fail(f"读取缓冲长度失败：{hresult_text(hr)}", hr)
                return
            while packet.value > 0 and not self._stop.is_set():
                data_ptr = ctypes.c_void_p()
                frames = ctypes.c_ulong(0)
                flags = ctypes.c_ulong(0)
                device_pos = ctypes.c_ulonglong(0)
                qpc_pos = ctypes.c_ulonglong(0)
                hr = get_buffer(
                    capture_ptr, ctypes.byref(data_ptr), ctypes.byref(frames),
                    ctypes.byref(flags), ctypes.byref(device_pos), ctypes.byref(qpc_pos),
                )
                if not hresult_ok(hr):
                    self._fail(f"读取音频数据失败：{hresult_text(hr)}", hr)
                    return
                try:
                    if frames.value:
                        block = self._convert(
                            data_ptr, frames.value, channels, bytes_per_sample,
                            is_float, bool(flags.value & AUDCLNT_BUFFERFLAGS_SILENT),
                        )
                        if block is not None and block.size:
                            try:
                                self._on_block(block)
                            except Exception:
                                pass
                finally:
                    release_buffer(capture_ptr, frames.value)
                hr = get_next(capture_ptr, ctypes.byref(packet))
                if not hresult_ok(hr):
                    return
            idle_rounds += 1
            time.sleep(self._poll)

    @staticmethod
    def _convert(
        data_ptr: ctypes.c_void_p,
        frames: int,
        channels: int,
        bytes_per_sample: int,
        is_float: bool,
        silent: bool,
    ) -> np.ndarray | None:
        """把采集到的原始缓冲转成 float32、最多双声道。"""
        if silent or not data_ptr.value:
            return np.zeros((frames, min(2, channels)), dtype=np.float32)
        if is_float and bytes_per_sample == 4:
            count = frames * channels
            raw = np.ctypeslib.as_array(
                ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_float)), shape=(count,)
            )
            data = raw.reshape(frames, channels).astype(np.float32, copy=True)
        elif bytes_per_sample == 2:
            count = frames * channels
            raw = np.ctypeslib.as_array(
                ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_int16)), shape=(count,)
            )
            data = (raw.reshape(frames, channels).astype(np.float32) / 32768.0)
        elif bytes_per_sample == 4:
            count = frames * channels
            raw = np.ctypeslib.as_array(
                ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_int32)), shape=(count,)
            )
            data = (raw.reshape(frames, channels).astype(np.float32) / 2147483648.0)
        else:  # 不认识的位深：当静音处理，避免放噪音出去
            return np.zeros((frames, min(2, channels)), dtype=np.float32)

        if channels > 2:
            data = data[:, :2]
        elif channels == 1:
            data = np.repeat(data, 2, axis=1)
        return np.ascontiguousarray(data, dtype=np.float32)


def probe(pid: int | None = None, duration: float = 0.6) -> tuple[bool, str]:
    """快速自检：尝试对某个进程（默认自己）开一次 loopback，然后立刻停。

    返回 (是否可用, 原因)。只用于诊断，不参与录音流程。
    """
    ok, reason = is_supported()
    if not ok:
        return False, reason

    target = int(pid) if pid else ctypes.windll.kernel32.GetCurrentProcessId()
    blocks = {"count": 0, "frames": 0}

    def on_block(block: np.ndarray) -> None:
        blocks["count"] += 1
        blocks["frames"] += int(block.shape[0])

    capture = ProcessLoopbackCapture(target, on_block)
    started = capture.start(timeout=3.0)
    if not started or capture.error:
        return False, capture.error or "激活失败（原因未知）"
    time.sleep(duration)
    capture.stop()
    return True, (
        f"可用：PID {target}，{capture.samplerate} Hz / {capture.channels} ch，"
        f"收到 {blocks['count']} 个数据块 / {blocks['frames']} 帧"
    )


def quick_probe(target_pid: int | None = None) -> tuple[bool, str]:
    """只验证"能不能激活进程 loopback"，拿到就直接停（几十毫秒级）。

    界面启动时用它决定模式 3 是否可用：有些系统版本够、导出函数也在，
    但激活会被音频策略/驱动挡掉，只有真试一次才知道。
    """
    ok, reason = is_supported()
    if not ok:
        return False, reason

    pid = int(target_pid) if target_pid else int(ctypes.windll.kernel32.GetCurrentProcessId())
    capture = ProcessLoopbackCapture(pid, lambda block: None)
    started = capture.start(timeout=2.5)
    error = capture.error
    capture.stop()
    if started and not error:
        return True, ""
    return False, error or "激活未成功（原因未知）"
