"""统一全局热键：**N 条音效键 + 1 条录音键**，统一分配与冲突检测。

两条后端（和单独的音效板/录音工具一样，但这里要同时服务两类按键）：

1. **Win32 RegisterHotKey** —— 系统级注册，前台/后台都生效；
   注册失败会返回 1409（已被其他程序占用），这是**真实被占用**的证据；
   缺点：只收按下，收不到松开。
2. **keyboard 低级钩子** —— 能拿到 key-up，用于"按住播放、松开停止"，
   也兜住 RegisterHotKey 注册失败的组合（否则那个键就废了）。

分配规则（这是合并后最容易出错的地方，规则写死在这里）：
* **录音键优先**：它和某条音效键重复时，录音键拿走这个组合，音效键标"重复"；
* 一击模式音效 → 先 Win32，失败退钩子并标"可能冲突"；
* 按住模式音效 → 必须钩子（需要 key-up）；
* 同一条组合被多条音效占用 → 只有第一条生效，其余标 duplicate。

本模块不依赖 Qt：回调都在后台线程触发，界面层负责切回主线程。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

try:
    import keyboard as _keyboard
except Exception:  # pragma: no cover
    _keyboard = None  # type: ignore[assignment]

#: 录音热键在绑定表里用的保留 id
RECORD_ID = "__record__"

# ------------------------------------------------------------------ 按键表
MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")

MODIFIERS: dict[str, tuple[int, str]] = {
    "ctrl": (0x0002, "Ctrl"),
    "alt": (0x0001, "Alt"),
    "shift": (0x0004, "Shift"),
    "win": (0x0008, "Win"),
}

#: 主键名 -> (虚拟键码 VK, 显示名, keyboard 库可能使用的名字候选)
KEYS: dict[str, tuple[int, str, tuple[str, ...]]] = {
    **{chr(c): (0x41 + (c - 97), chr(c).upper(), (chr(c),)) for c in range(97, 123)},
    **{str(d): (0x30 + d, str(d), (str(d),)) for d in range(10)},
    **{
        f"numpad {d}": (0x60 + d, f"小键盘 {d}", (f"num {d}", f"numpad {d}", f"num{d}"))
        for d in range(10)
    },
    **{f"f{n}": (0x6F + n, f"F{n}", (f"f{n}",)) for n in range(1, 25)},
    "space": (0x20, "空格", ("space",)),
    "tab": (0x09, "Tab", ("tab",)),
    "enter": (0x0D, "回车", ("enter",)),
    "backspace": (0x08, "退格", ("backspace",)),
    "insert": (0x2D, "Insert", ("insert",)),
    "delete": (0x2E, "Delete", ("delete",)),
    "home": (0x24, "Home", ("home",)),
    "end": (0x23, "End", ("end",)),
    "page up": (0x21, "PageUp", ("page up",)),
    "page down": (0x22, "PageDown", ("page down",)),
    "up": (0x26, "↑", ("up",)),
    "down": (0x28, "↓", ("down",)),
    "left": (0x25, "←", ("left",)),
    "right": (0x27, "→", ("right",)),
    "esc": (0x1B, "Esc", ("esc",)),
    "caps lock": (0x14, "CapsLock", ("caps lock",)),
    "print screen": (0x2C, "PrintScreen", ("print screen",)),
    "pause": (0x13, "Pause", ("pause",)),
    "scroll lock": (0x91, "ScrollLock", ("scroll lock",)),
    "numpad multiply": (0x6A, "小键盘 *", ("multiply", "num multiply")),
    "numpad add": (0x6B, "小键盘 +", ("add", "num add")),
    "numpad subtract": (0x6D, "小键盘 -", ("subtract", "num subtract")),
    "numpad decimal": (0x6E, "小键盘 .", ("decimal", "num decimal")),
    "numpad divide": (0x6F, "小键盘 /", ("divide", "num divide")),
    "`": (0xC0, "`", ("`",)),
    "-": (0xBD, "-", ("-",)),
    "=": (0xBB, "=", ("=",)),
    "[": (0xDB, "[", ("[",)),
    "]": (0xDD, "]", ("]",)),
    "\\": (0xDC, "\\", ("\\",)),
    ";": (0xBA, ";", (";",)),
    "'": (0xDE, "'", ("'",)),
    ",": (0xBC, ",", (",",)),
    ".": (0xBE, ".", (".",)),
    "/": (0xBF, "/", ("/",)),
}

VK_TO_KEY: dict[int, str] = {vk: name for name, (vk, _l, _c) in KEYS.items()}
VK_MODIFIERS: dict[int, str] = {
    0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x12: "alt", 0xA4: "alt", 0xA5: "alt",
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x5B: "win", 0x5C: "win",
}

WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
MOD_NOREPEAT = 0x4000


class InvalidHotkey(ValueError):
    """快捷键字符串无法解析。"""


# ------------------------------------------------------------------ 解析/显示
def parse_hotkey(text: str) -> frozenset[str]:
    if not text or not str(text).strip():
        raise InvalidHotkey("快捷键为空")
    parts = [part.strip().lower() for part in str(text).split("+")]
    if any(not part for part in parts):
        raise InvalidHotkey("快捷键格式错误（存在空片段）")
    *maybe_mods, main = parts
    for part in maybe_mods:
        if part not in MODIFIERS:
            raise InvalidHotkey(f"不支持的修饰键：{part}")
    if main in MODIFIERS:
        raise InvalidHotkey("只有修饰键不算有效快捷键")
    if main not in KEYS:
        raise InvalidHotkey(f"不支持的按键：{main}")
    if len(set(parts)) != len(parts):
        raise InvalidHotkey("快捷键里有重复按键")
    return frozenset(parts)


def format_hotkey(keys: Iterable[str]) -> str:
    keys = {key.lower() for key in keys}
    mods = [mod for mod in MODIFIER_ORDER if mod in keys]
    mains = sorted(key for key in keys if key not in MODIFIERS)
    return "+".join(mods + mains)


def display_hotkey(text: str) -> str:
    if not text:
        return ""
    try:
        keys = parse_hotkey(text)
    except InvalidHotkey:
        return text
    mods = [MODIFIERS[mod][1] for mod in MODIFIER_ORDER if mod in keys]
    mains = [KEYS[key][1] for key in sorted(key for key in keys if key not in MODIFIERS)]
    return " + ".join(mods + mains)


def hotkey_to_win32(text: str) -> tuple[int, int]:
    keys = parse_hotkey(text)
    mods = 0
    vk = None
    for key in keys:
        if key in MODIFIERS:
            mods |= MODIFIERS[key][0]
        else:
            vk = KEYS[key][0]
    if vk is None:
        raise InvalidHotkey("缺少主键")
    return mods, vk


def key_name_from_vk(vk: int) -> str | None:
    return VK_TO_KEY.get(int(vk))


def modifier_from_vk(vk: int) -> str | None:
    return VK_MODIFIERS.get(int(vk))


# ------------------------------------------------------------------ 数据结构
@dataclass
class Binding:
    """一条热键绑定。id 是 Clip.id，或 RECORD_ID（录音键）。"""

    id: str
    hotkey: str
    mode: str = "toggle"          # toggle / hold / record
    keys: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_record(self) -> bool:
        return self.id == RECORD_ID

    @property
    def needs_keyup(self) -> bool:
        return self.mode == "hold"


@dataclass
class HotkeyState:
    """注册结果，界面据此标色。"""

    status: str = "not_set"   # not_set/ok/duplicate/occupied/invalid/error
    message: str = ""
    backend: str = ""         # win32 / hook

    @property
    def is_problem(self) -> bool:
        return self.status in ("duplicate", "occupied", "invalid", "error")


# ------------------------------------------------------------------ Win32 后端
class _Win32Backend:
    """独立线程里 RegisterHotKey + 自跑消息循环（因此不需要 Qt 的 native filter）。"""

    def __init__(self, on_press: Callable[[str], None], on_log: Callable[[str], None]) -> None:
        self._on_press = on_press
        self._log = on_log
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.restype = ctypes.c_bool
        self._user32.UnregisterHotKey.restype = ctypes.c_bool
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._id_to_key: dict[int, str] = {}
        self._next_id = 1
        self._results: dict[str, HotkeyState] = {}
        self._sync = threading.Event()
        self._lock = threading.RLock()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="studio-win32-hotkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        try:
            self._commands.put_nowait(("quit",))
        except Exception:
            pass
        self._thread.join(timeout=1.0)
        self._thread = None

    def register(self, bindings: list[Binding], timeout: float = 2.0) -> dict[str, HotkeyState]:
        with self._lock:
            self._results = {}
        self._sync.clear()
        self._commands.put(("set", bindings))
        if not self._sync.wait(timeout):
            self._log("热键注册超时（可能被安全软件拦截）")
            return {}
        with self._lock:
            return dict(self._results)

    def _run(self) -> None:
        message = ctypes.wintypes.MSG()
        while not self._stop.is_set():
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                command = None
            if command is not None:
                self._handle(command)

            got_any = False
            while self._user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                got_any = True
                if message.message == WM_HOTKEY:
                    key_id = self._id_to_key.get(int(message.wParam))
                    if key_id:
                        try:
                            self._on_press(key_id)
                        except Exception:
                            pass
            if not got_any:
                time.sleep(0.004)
        self._unregister_all()

    def _handle(self, command: tuple) -> None:
        if command[0] == "quit":
            return
        if command[0] != "set":
            return
        bindings: list[Binding] = command[1]
        results: dict[str, HotkeyState] = {}
        self._unregister_all()

        for binding in bindings:
            if not binding.hotkey:
                continue
            try:
                mods, vk = hotkey_to_win32(binding.hotkey)
            except InvalidHotkey as exc:
                results[binding.id] = HotkeyState("invalid", str(exc))
                continue
            hotkey_id = self._next_id
            self._next_id += 1
            ctypes.set_last_error(0)
            ok = self._user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk)
            if ok:
                self._id_to_key[hotkey_id] = binding.id
                results[binding.id] = HotkeyState("ok", "", "win32")
            else:
                error = ctypes.get_last_error()
                if error == ERROR_HOTKEY_ALREADY_REGISTERED:
                    results[binding.id] = HotkeyState("occupied", "该快捷键已被其他程序占用")
                else:
                    results[binding.id] = HotkeyState("error", f"注册失败（错误码 {error}）")
        with self._lock:
            self._results = results
        self._sync.set()

    def _unregister_all(self) -> None:
        for hotkey_id in list(self._id_to_key):
            try:
                self._user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
        self._id_to_key.clear()


# ------------------------------------------------------------------ 钩子后端
class _HookBackend:
    """低级键盘钩子：拿 key-up（按住模式）+ 兜住 Win32 注册失败的组合。"""

    def __init__(
        self,
        on_press: Callable[[str], None],
        on_release: Callable[[str], None],
        on_log: Callable[[str], None],
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._log = on_log
        self._lock = threading.RLock()
        self._bindings: list[Binding] = []
        self._pressed: set[str] = set()
        self._active: set[str] = set()
        self._running = False
        self._suspended = False
        self._name_map: dict[str, str] = {}
        self._build_name_map()

    def _build_name_map(self) -> None:
        if _keyboard is None:
            return
        for canonical, (_vk, _label, candidates) in KEYS.items():
            for candidate in candidates:
                try:
                    _keyboard.key_to_scan_codes(candidate)
                except Exception:
                    continue
                if candidate in self._name_map:
                    break
                self._name_map[candidate] = canonical
                break
        for modifier in MODIFIERS:
            self._name_map["windows" if modifier == "win" else modifier] = modifier

    def start(self) -> bool:
        if _keyboard is None:
            self._log("keyboard 库不可用，全局热键不可用")
            return False
        if self._running:
            return True
        try:
            _keyboard.hook(self._on_event, suppress=False)
        except Exception as exc:
            self._log(f"安装键盘钩子失败：{exc}")
            return False
        self._running = True
        return True

    def stop(self) -> None:
        if _keyboard is None or not self._running:
            return
        try:
            _keyboard.unhook_all()
        except Exception:
            pass
        self._running = False

    def set_bindings(self, bindings: list[Binding]) -> None:
        with self._lock:
            self._bindings = [b for b in bindings if b.keys]
            self._pressed.clear()
            self._active.clear()

    def set_suspended(self, suspended: bool) -> None:
        self._suspended = suspended
        if suspended:
            with self._lock:
                self._pressed.clear()
                self._active.clear()

    def _on_event(self, event) -> None:  # noqa: ANN001
        if self._suspended or _keyboard is None:
            return
        canonical = self._name_map.get(event.name)
        if canonical is None:
            return
        is_down = event.event_type == "down"
        pressed_fire: list[str] = []
        released_fire: list[str] = []

        with self._lock:
            if is_down:
                if canonical in self._pressed:
                    return  # 长按重复事件，忽略
                self._pressed.add(canonical)
            else:
                self._pressed.discard(canonical)

            for binding in self._bindings:
                satisfied = binding.keys.issubset(self._pressed)
                if satisfied and binding.id not in self._active:
                    self._active.add(binding.id)
                    pressed_fire.append(binding.id)
                elif not satisfied and binding.id in self._active:
                    self._active.discard(binding.id)
                    if binding.needs_keyup:
                        released_fire.append(binding.id)

        for key_id in pressed_fire:
            try:
                self._on_press(key_id)
            except Exception:
                pass
        for key_id in released_fire:
            try:
                self._on_release(key_id)
            except Exception:
                pass



# ------------------------------------------------------------------ 底层钩子后端
USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10

VK_MODIFIER_NAMES = {
    0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x12: "alt", 0xA4: "alt", 0xA5: "alt",
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x5B: "win", 0x5C: "win",
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.wintypes.DWORD),
        ("scanCode", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
)

# 64 位下必须显式声明函数签名：否则 ctypes 按 32 位 int 处理句柄，
# GetModuleHandleW 的 HMODULE 会被截断，SetWindowsHookEx 报 126（找不到模块）。
KERNEL32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
KERNEL32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
KERNEL32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD

USER32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, ctypes.wintypes.HINSTANCE, ctypes.wintypes.DWORD
]
USER32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
USER32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
USER32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL
USER32.CallNextHookEx.argtypes = [
    ctypes.wintypes.HHOOK, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
]
USER32.CallNextHookEx.restype = ctypes.c_ssize_t
USER32.GetMessageW.argtypes = [
    ctypes.POINTER(ctypes.wintypes.MSG), ctypes.wintypes.HWND,
    ctypes.wintypes.UINT, ctypes.wintypes.UINT,
]
USER32.GetMessageW.restype = ctypes.wintypes.BOOL
USER32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD, ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
]
USER32.PostThreadMessageW.restype = ctypes.wintypes.BOOL


class LowLevelKeyHook:
    """自建全局键盘钩子：后台也能收到按下/松开。

    为什么不用 RegisterHotKey：它依赖注册线程的消息泵，出问题时表现为"前台能用、
    后台没反应"；底层钩子由系统回调，跟窗口焦点无关。
    """

    def __init__(self, on_press, on_release, on_log=None) -> None:  # noqa: ANN001
        self._on_press = on_press
        self._on_release = on_release
        self._log = on_log or (lambda message: None)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._proc = None                 # 必须留引用，否则会被回收导致崩溃
        self._lock = threading.RLock()
        self._running = threading.Event()
        self._install_error = ""
        # 绑定：vk -> list[(binding_id, mods frozenset, needs_keyup)]
        self._by_vk: dict[int, list[tuple[str, frozenset[str], bool]]] = {}
        self._held_vks: set[int] = set()
        self._down_main: set[int] = set()
        self._active: dict[int, list[str]] = {}
        self._suspended = False

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        if self._running.is_set():
            return True
        self._thread = threading.Thread(
            target=self._run, name="studio-kbd-hook", daemon=True
        )
        self._thread.start()
        # 等钩子装好（最多 2 秒），失败要能立刻知道
        for _ in range(200):
            if self._running.is_set() or self._install_error:
                break
            time.sleep(0.01)
        if self._install_error:
            self._log(f"底层键盘钩子安装失败：{self._install_error}")
            return False
        return self._running.is_set()

    def stop(self) -> None:
        if self._thread_id:
            try:
                USER32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None
        self._running.clear()
        self._held_vks.clear()
        self._down_main.clear()
        self._active.clear()

    # ------------------------------------------------------------------ 绑定
    def set_bindings(self, bindings) -> None:  # noqa: ANN001
        table: dict[int, list[tuple[str, frozenset[str], bool]]] = {}
        for binding in bindings:
            keys = binding.keys
            if not keys:
                continue
            mods = frozenset(key for key in keys if key in VK_MODIFIER_NAMES.values())
            mains = [key for key in keys if key not in VK_MODIFIER_NAMES.values()]
            if not mains:
                continue
            vk = KEYS[mains[0]][0]
            table.setdefault(vk, []).append((binding.id, mods, binding.needs_keyup))
        with self._lock:
            self._by_vk = table
            self._active.clear()
            self._down_main.clear()

    def set_suspended(self, suspended: bool) -> None:
        self._suspended = bool(suspended)
        if suspended:
            with self._lock:
                self._held_vks.clear()
                self._down_main.clear()
                self._active.clear()

    # ------------------------------------------------------------------ 线程
    def _run(self) -> None:
        self._thread_id = int(KERNEL32.GetCurrentThreadId())
        self._proc = HOOKPROC(self._callback)
        module = KERNEL32.GetModuleHandleW(None)
        self._hook = USER32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, module, 0)
        if not self._hook:
            self._install_error = f"SetWindowsHookEx 失败（错误码 {ctypes.get_last_error()}）"
            return
        self._running.set()
        message = ctypes.wintypes.MSG()
        # 标准消息泵：钩子回调靠它派发（GetMessage 会阻塞，回调在系统线程里执行）
        while USER32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            USER32.TranslateMessage(ctypes.byref(message))
            USER32.DispatchMessageW(ctypes.byref(message))
        try:
            USER32.UnhookWindowsHookEx(self._hook)
        except Exception:
            pass
        self._hook = None
        self._running.clear()

    def _callback(self, n_code, w_param, l_param):  # noqa: ANN001
        try:
            if n_code == HC_ACTION and not self._suspended:
                data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(data.vkCode)
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    self._on_down(vk)
                elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                    self._on_up(vk)
        except Exception:  # noqa: BLE001 # pragma: no cover
            pass
        return USER32.CallNextHookEx(0, n_code, w_param, l_param)

    # ------------------------------------------------------------------ 匹配
    def _mods(self) -> frozenset[str]:
        return frozenset(
            VK_MODIFIER_NAMES[vk] for vk in self._held_vks if vk in VK_MODIFIER_NAMES
        )

    def _on_down(self, vk: int) -> None:
        with self._lock:
            self._held_vks.add(vk)
            if vk in self._down_main:
                return                      # 自动重复
            entries = self._by_vk.get(vk)
            if not entries:
                return
            current = self._mods()
            for binding_id, mods, needs_keyup in entries:
                if mods != current:
                    continue
                self._down_main.add(vk)
                if needs_keyup:
                    self._active.setdefault(vk, []).append(binding_id)
                try:
                    self._on_press(binding_id)
                except Exception:
                    pass
                break

    def _on_up(self, vk: int) -> None:
        with self._lock:
            self._held_vks.discard(vk)
            self._down_main.discard(vk)
            for binding_id in self._active.pop(vk, []):
                try:
                    self._on_release(binding_id)
                except Exception:
                    pass

# ------------------------------------------------------------------ 门面
class HotkeyManager:
    """统一管理音效键与录音键。"""

    def __init__(
        self,
        on_press: Callable[[str], None],
        on_release: Callable[[str], None],
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._on_log = on_log or (lambda message: None)
        self._lock = threading.RLock()
        self._states: dict[str, HotkeyState] = {}
        #: 记录每条绑定走的是哪条通道（hold 状态机用）
        self._hook_ids: set[str] = set()
        self._hold_ids: set[str] = set()
        self._pressed: dict[str, threading.Event] = {}
        self._win32 = _Win32Backend(self._guarded_press, self._on_log)
        self._hook = _HookBackend(self._guarded_press, self._guarded_release, self._on_log)
        #: 自建底层钩子（首选）：后台必收，不依赖 RegisterHotKey 的消息泵
        self._llhook = LowLevelKeyHook(
            self._guarded_press, self._guarded_release, self._on_log
        )
        self._hook_active = False
        #: 同一条绑定的去重时间戳（钩子 + RegisterHotKey 会各报一次）
        self._last_press: dict[str, float] = {}
        self.started = False

    # ---- 生命周期 ----
    def start(self) -> None:
        if self.started:
            return
        # 钩子优先；RegisterHotKey 线程也启动，作为**第二条保险**
        # （两条通道可能同时报同一次按键，靠 _guarded_press 的 150ms 去重）
        self._hook_active = self._llhook.start()
        self._win32.start()
        if self._hook_active:
            self._on_log("全局热键：自建底层钩子 + RegisterHotKey 双通道")
        else:
            self._on_log("全局热键：底层钩子不可用，退回 RegisterHotKey + keyboard 钩子")
            self._hook.start()
        self.started = True

    def stop(self) -> None:
        self._llhook.stop()
        self._hook.stop()
        self._win32.stop()
        self.started = False

    def set_suspended(self, suspended: bool) -> None:
        self._llhook.set_suspended(suspended)
        self._hook.set_suspended(suspended)

    # ---- 应用绑定 ----
    def apply(self, bindings: list[Binding]) -> dict[str, HotkeyState]:
        """重新注册全部热键，返回 {id: HotkeyState}。"""
        # 重新应用绑定意味着绑定流程已经结束：顺手解除挂起，
        # 避免历史"挂起未恢复"的状态把全局热键锁死。
        self._llhook.set_suspended(False)
        self._hook.set_suspended(False)
        with self._lock:
            states: dict[str, HotkeyState] = {}
            self._pressed = {b.id: threading.Event() for b in bindings if b.hotkey}
            self._hold_ids = {b.id for b in bindings if b.needs_keyup}

            # 1) 解析 + 录音键优先占位
            parsed: list[Binding] = []
            for binding in bindings:
                if not binding.hotkey:
                    states[binding.id] = HotkeyState("not_set", "未设置快捷键")
                    continue
                try:
                    binding.keys = parse_hotkey(binding.hotkey)
                except InvalidHotkey as exc:
                    states[binding.id] = HotkeyState("invalid", str(exc))
                    continue
                parsed.append(binding)

            parsed.sort(key=lambda b: 0 if b.is_record else 1)

            record_seen: str | None = None
            used: dict[str, str] = {}
            win32_candidates: list[Binding] = []
            hook_candidates: list[Binding] = []

            for binding in parsed:
                if binding.hotkey in used:
                    if binding.is_record:
                        # 理论上不会发生（录音键已优先），保险起见
                        states[binding.id] = HotkeyState("duplicate", "与音效快捷键重复")
                    else:
                        states[binding.id] = HotkeyState(
                            "duplicate",
                            "与录音快捷键重复" if used[binding.hotkey] == RECORD_ID
                            else "与其他音效的快捷键重复",
                        )
                    continue
                used[binding.hotkey] = binding.id
                if binding.is_record:
                    record_seen = binding.id
                if binding.needs_keyup:
                    hook_candidates.append(binding)   # 按住模式必须钩子
                else:
                    win32_candidates.append(binding)

            # 2) 自建底层钩子可用：所有绑定都交给它（前后台一致、支持 key-up）
            if self._hook_active:
                self._llhook.set_bindings(parsed)
                self._hook_ids = {b.id for b in parsed}
                # 同时再给"带修饰键的一击模式"注册一份 RegisterHotKey：双通道，
                # 谁先到都能响（重复触发由 150ms 去重吃掉）。
                # 纯按键（F9、字母）**不注册**——RegisterHotKey 会把该键从其他程序
                # 手里抢走（比如语音软件的按键说话），钩子已经足够。
                extra = [
                    b for b in win32_candidates
                    if any(key in MODIFIERS for key in getattr(b, "keys", frozenset()))
                ]
                if extra:
                    try:
                        self._win32.register(extra)
                    except Exception:
                        pass
                for binding in parsed:
                    states.setdefault(binding.id, HotkeyState("ok", "", "hook"))
                self._hook.set_bindings([])          # 不让旧的 keyboard 钩子重复触发
                self._states = states
                return dict(states)

            # 2b) 退化路径：Win32 批量注册；失败的退钩子
            win32_states = self._win32.register(win32_candidates) if win32_candidates else {}
            wrapped = 0
            for binding in win32_candidates:
                state = win32_states.get(binding.id, HotkeyState("error", "注册无响应"))
                if state.status == "ok":
                    states[binding.id] = state
                else:
                    hook_candidates.append(binding)
                    message = state.message
                    if state.status == "occupied":
                        message = "该快捷键被其他程序占用（已改用键盘钩子，可能同时触发）"
                    states[binding.id] = HotkeyState(state.status, message, "hook")
                    wrapped += 1

            # 3) 钩子接手剩下的
            hook_only = [b for b in hook_candidates if b.id not in _ok_ids(states)]
            self._hook_ids = {b.id for b in hook_only}
            self._hook.set_bindings(hook_only)
            for binding in hook_only:
                states.setdefault(binding.id, HotkeyState("ok", "", "hook"))

            self._states = states
            return dict(states)

    def states(self) -> dict[str, HotkeyState]:
        with self._lock:
            return dict(self._states)

    def state_of(self, key_id: str) -> HotkeyState:
        with self._lock:
            return self._states.get(key_id, HotkeyState())

    def reset_press_state(self, key_id: str) -> None:
        with self._lock:
            event = self._pressed.get(key_id)
            if event is not None:
                event.clear()

    # ---- 回调包装 ----
    #: 双通道（钩子 + RegisterHotKey）去重窗口
    DEDUPE_SECONDS = 0.15

    def _guarded_press(self, key_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            event = self._pressed.setdefault(key_id, threading.Event())
            # 同一条绑定在"按住中"时忽略重复触发
            if event.is_set():
                return
            # 两条通道可能同时报同一次按键：窗口内只认第一次
            last = self._last_press.get(key_id, 0.0)
            if now - last < self.DEDUPE_SECONDS:
                return
            self._last_press[key_id] = now
            if key_id in self._hold_ids:
                event.set()
        try:
            self._on_press(key_id)
        except Exception:
            pass

    def _guarded_release(self, key_id: str) -> None:
        with self._lock:
            event = self._pressed.get(key_id)
            if event is not None:
                event.clear()
            is_hold = key_id in self._hold_ids
        if is_hold:
            try:
                self._on_release(key_id)
            except Exception:
                pass


def _ok_ids(states: dict[str, HotkeyState]) -> set[str]:
    """已经由 Win32 成功注册的绑定 id。"""
    return {
        key_id for key_id, state in states.items()
        if state.status == "ok" and state.backend == "win32"
    }
