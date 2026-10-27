"""枚举"当前正在出声的应用"（模式 3 的目标列表）。

做法：
1. **pycaw** ``AudioUtilities.GetAllSessions()`` 拿所有音频会话；
2. 只保留 ``State == Active``（正在播放）且有 ``ProcessId`` 的会话；
3. **psutil** 补齐进程名 / exe 路径（图标由界面用 QFileIconProvider 取，零额外依赖）；
4. 按 PID 去重（Chrome 这类多进程应用会有多个会话），按首次枚举顺序排序
   ——pycaw 的返回顺序基本等于系统里的活跃顺序，比"猜 last_activity"更可靠。

兜底：若某些进程拿不到会话（UWP、权限受限），可用 psutil 把"看起来像播放器"
的进程作为**推断**候选追加在末尾，界面上明确标注"推断"，避免用户误选。

本模块不依赖 Qt，可单独测试。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

try:
    from pycaw.pycaw import AudioUtilities
except Exception:  # pragma: no cover
    AudioUtilities = None  # type: ignore[assignment]

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

#: 音频会话处于"正在播放"状态时的枚举值（AUDIO_SESSION_STATE_ACTIVE）
SESSION_ACTIVE = 1

#: 推断用的默认关键词（用户可在 config 里追加）
DEFAULT_INFERRED_KEYWORDS = [
    "chrome", "msedge", "firefox", "brave", "opera",
    "spotify", "vlc", "potplayer", "mpv", "foobar", "aimp",
    "wmplayer", "qqmusic", "cloudmusic", "netease", "kugou", "kuwo",
    "discord", "oopz", "teamspeak", "steam", "obs", "bilibili",
]


@dataclass(frozen=True)
class AppChoice:
    """一个可以被单独录音的应用。"""

    pid: int
    process_name: str        # 进程名（chrome.exe）
    display_name: str        # 应用名（Chrome）
    exe: str | None          # 可执行文件路径（取图标用）
    is_inferred: bool = False  # True = 没有精确会话，只是推断
    sessions: int = 1        # 该 PID 的会话数（去重前）

    @property
    def key(self) -> str:
        """配置里持久化的标识（用 PID；进程重启后失效，属预期）。"""
        return str(self.pid)

    def label(self) -> str:
        mark = "  (推断)" if self.is_inferred else ""
        return f"{self.display_name} (PID {self.pid}){mark}"

    def tooltip(self) -> str:
        lines = [
            f"进程：{self.process_name}",
            f"PID：{self.pid}",
            f"路径：{self.exe or '（无法获取，可能权限受限）'}",
        ]
        if self.is_inferred:
            lines.append("没有检测到精确音频会话，仅按进程名推断（可能录不到声音）")
        elif self.sessions > 1:
            lines.append(f"该进程有 {self.sessions} 个音频会话，已合并为一条")
        return "\n".join(lines)


def _pretty_name(exe_name: str) -> str:
    """chrome.exe -> Chrome；msedgewebview2.exe -> Msedgewebview2。"""
    stem = os.path.splitext(exe_name)[0]
    if not stem:
        return exe_name
    return stem[:1].upper() + stem[1:]


@lru_cache(maxsize=512)
def _process_name(pid: int) -> str | None:
    if psutil is None:
        return None
    try:
        return psutil.Process(pid).name()
    except Exception:
        return None


@lru_cache(maxsize=512)
def _process_exe(pid: int) -> str | None:
    if psutil is None:
        return None
    try:
        return psutil.Process(pid).exe()
    except Exception:
        return None


def clear_process_cache() -> None:
    """刷新列表前清缓存，避免打进同一个 PID 的旧信息。"""
    _process_name.cache_clear()
    _process_exe.cache_clear()


def is_supported() -> tuple[bool, str]:
    """当前环境能不能枚举音频会话。"""
    if AudioUtilities is None:
        return False, "未安装 pycaw（pip install pycaw），无法识别单个应用"
    try:
        sessions = AudioUtilities.GetAllSessions()
        _ = len(sessions)
    except Exception as exc:
        return False, f"枚举音频会话失败：{exc}"
    return True, ""


def _active_session_pids() -> dict[int, int]:
    """{pid: 会话数}，只算 Active 且拿得到 PID 的会话。保持首次出现顺序。"""
    result: dict[int, int] = {}
    if AudioUtilities is None:
        return result
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception:
        return result

    for session in sessions:
        try:
            state = session.State
        except Exception:
            continue
        if state != SESSION_ACTIVE:
            continue
        try:
            pid = int(session.ProcessId or 0)
        except Exception:
            pid = 0
        if pid <= 0:
            continue
        result[pid] = result.get(pid, 0) + 1
    return result


def list_audio_apps(
    include_inferred: bool = True,
    inferred_keywords: Iterable[str] | None = None,
    exclude_pids: Iterable[int] = (),
) -> list[AppChoice]:
    """列出正在播放声音的应用（按 PID 去重，活跃顺序在前）。"""
    excludes = {int(pid) for pid in exclude_pids}
    pids = _active_session_pids()

    app_list: list[AppChoice] = []
    for pid, count in pids.items():
        if pid in excludes:
            continue
        process_name = _process_name(pid) or f"pid-{pid}"
        exe = _process_exe(pid)
        app_list.append(AppChoice(
            pid=pid,
            process_name=process_name,
            display_name=_pretty_name(process_name),
            exe=exe,
            is_inferred=False,
            sessions=count,
        ))

    if include_inferred and psutil is not None:
        keywords = [str(k).lower() for k in (inferred_keywords or DEFAULT_INFERRED_KEYWORDS) if k]
        known = set(pids) | excludes
        for candidate in _infer_candidates(keywords, known):
            app_list.append(candidate)

    return app_list


def _infer_candidates(keywords: list[str], known_pids: set[int]) -> list[AppChoice]:
    """按进程名关键词找"可能是播放器但没被会话命中"的进程（标记为推断）。

    同一个进程名只留一条（Chrome/Edge 会有十几个子进程，全列出来只会干扰），
    并且最多 5 条。
    """
    results: list[AppChoice] = []
    seen_names: set[str] = set()
    if psutil is None:
        return results
    try:
        processes = list(psutil.process_iter(["pid", "name", "exe"]))
    except Exception:
        return results

    for proc in processes:
        try:
            pid = int(proc.info.get("pid") or 0)
            name = str(proc.info.get("name") or "")
        except Exception:
            continue
        if pid <= 0 or pid in known_pids or not name:
            continue
        lowered = name.lower()
        if lowered in seen_names:
            continue
        if not any(keyword in lowered for keyword in keywords):
            continue
        seen_names.add(lowered)
        results.append(AppChoice(
            pid=pid,
            process_name=name,
            display_name=_pretty_name(name),
            exe=proc.info.get("exe"),
            is_inferred=True,
            sessions=0,
        ))
        if len(results) >= 5:
            break
    return results


def find_app(apps: list[AppChoice], pid: int | str | None) -> AppChoice | None:
    """按 PID 找回上次选择的应用（进程还在就能命中）。"""
    if pid in (None, ""):
        return None
    try:
        target = int(pid)
    except (TypeError, ValueError):
        return None
    for app in apps:
        if app.pid == target:
            return app
    return None


def process_alive(pid: int) -> bool:
    """目标进程是否还活着（录音过程中用来判断要不要自动停）。"""
    if psutil is None:
        return True
    try:
        return psutil.pid_exists(int(pid))
    except Exception:
        return True
