"""配置：播放 + 录音 + 音效列表，全部存在一个 config.json。

* 原子写（临时文件 + os.replace）+ 300ms 防抖：拖滑条不会写坏文件；
* 读取时逐字段校验，坏值回退默认，不让一个错字导致程序起不来；
* 音效列表由 ``ClipLibrary`` 管，这里只负责**序列化时把它写进去**
  （``bind_library``），避免两处各存一份导致数据打架；
* 设备名清洗/分组规则（``device_name_rules``）也在这里，用户可扩展、不硬编码。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .clip_library import Clip
from .constants import (
    CONFIG_VERSION,
    DEFAULT_RECORD_HOTKEY,
    FORMAT_WAV,
    FORMATS,
    MODE_MIC,
    MODES,
    RECENT_LIMIT,
)


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


@dataclass
class StudioConfig:
    """全部持久化设置。"""

    version: int = CONFIG_VERSION

    # ---------------- 播放 ----------------
    #: 旧字段：单一输出设备（模式 C 的回退值，保留以兼容旧配置/旧版本回滚）
    output_device: str = ""            # 输出设备完整原名（key）
    #: 新字段：输出路由 {"mode": "virtual|dual|local", "primary_device_name": ..., "monitor_device_name": ...}
    #: 默认按"虚拟声卡"——没装 VB-CABLE 时引擎会回退到系统默认设备并显示橙色警告
    output_route: dict[str, str] = field(
        default_factory=lambda: {
            "mode": "virtual",
            "primary_device_name": "CABLE Input (VB-Audio Virtual Cable)",
            "monitor_device_name": "",
        }
    )
    master_volume: float = 1.0         # 0.0 ~ 1.0
    exclusive_mode: bool = False       # 互斥模式
    #: 内部混音（麦克风 + 音效板 → 一路输出；麦克风不会回放到本地）
    #: local_playback = 本地也放音效（只含音效，不含麦克风）
    #: {"enabled": false, "mic_device_name": "", "mic_gain": 1.0, "sfx_gain": 1.0, "local_playback": false}
    mixer: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": False,
            "mic_device_name": "",
            "mic_gain": 1.0,
            "sfx_gain": 1.0,
            "local_playback": False,
        }
    )

    # ---------------- 录音 ----------------
    record_mode: str = MODE_MIC
    mic_device: str = ""
    system_device: str = ""
    app_pid: int | None = None
    recent_mic: list[str] = field(default_factory=list)
    recent_system: list[str] = field(default_factory=list)
    record_hotkey: str = DEFAULT_RECORD_HOTKEY
    output_format: str = FORMAT_WAV     # wav / mp3（mp3 需 pydub + ffmpeg）
    #: 音效保存目录；空 = 默认 audio/（相对项目根，也支持绝对路径）
    save_dir: str = ""
    normalize: bool = False
    write_sidecar: bool = True
    app_show_inferred: bool = True
    show_all_devices: bool = False
    device_name_rules: dict[str, Any] = field(default_factory=dict)

    # ---------------- 音效列表 ----------------
    clips: list[Clip] = field(default_factory=list)

    # ---- 最近设备 ----
    def remember_device(self, mode: str, key: str) -> None:
        if not key:
            return
        bucket = self.recent_mic if mode == "mic" else self.recent_system
        if key in bucket:
            bucket.remove(key)
        bucket.insert(0, key)
        del bucket[RECENT_LIMIT:]

    def recent_for(self, mode: str) -> list[str]:
        return list(self.recent_mic if mode == "mic" else self.recent_system)

    # ---- 路由 ----
    def route(self):  # noqa: ANN201
        """当前路由（OutputRoute 实例；延迟导入避免 config 依赖音频栈）。"""
        from .audio_engine import OutputRoute

        return OutputRoute.from_dict(self.output_route)

    def apply_route(self, route) -> None:  # noqa: ANN001
        """写回路由配置（传 OutputRoute 实例）。"""
        self.output_route = route.to_dict()

    def route_mode(self) -> str:
        return str((self.output_route or {}).get("mode") or "virtual")

    # ---- 混音 ----
    def mixer_settings(self):  # noqa: ANN201
        """当前混音设置（MixerSettings 实例；延迟导入）。"""
        from .audio_engine import MixerSettings

        return MixerSettings.from_dict(self.mixer)

    def apply_mixer(self, settings) -> None:  # noqa: ANN001
        self.mixer = settings.to_dict()

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "output_device": self.output_device,
            "output_route": dict(self.output_route or {}),
            "mixer": dict(self.mixer or {}),
            "master_volume": round(self.master_volume, 3),
            "exclusive_mode": self.exclusive_mode,
            "record_mode": self.record_mode,
            "mic_device": self.mic_device,
            "system_device": self.system_device,
            "app_pid": self.app_pid,
            "recent_mic": list(self.recent_mic),
            "recent_system": list(self.recent_system),
            "record_hotkey": self.record_hotkey,
            "output_format": self.output_format,
            "save_dir": self.save_dir,
            "normalize": self.normalize,
            "write_sidecar": self.write_sidecar,
            "app_show_inferred": self.app_show_inferred,
            "show_all_devices": self.show_all_devices,
            "device_name_rules": self.device_name_rules,
            "clips": [clip.to_dict() for clip in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StudioConfig":
        fmt = str(data.get("output_format", FORMAT_WAV)).lower()
        if fmt not in FORMATS:
            fmt = FORMAT_WAV
        mode = str(data.get("record_mode") or MODE_MIC).lower()
        if mode not in MODES:
            mode = MODE_MIC

        try:
            master = clamp(float(data.get("master_volume", 1.0)), 0.0, 1.0)
        except (TypeError, ValueError):
            master = 1.0

        def _str_list(key: str) -> list[str]:
            raw = data.get(key)
            if not isinstance(raw, list):
                return []
            return [str(item) for item in raw if str(item).strip()][:RECENT_LIMIT]

        app_pid = data.get("app_pid")
        try:
            pid_value = int(app_pid) if app_pid not in (None, "") else None
        except (TypeError, ValueError):
            pid_value = None

        rules = data.get("device_name_rules")
        clips_raw = data.get("clips")
        clips: list[Clip] = []
        seen: set[str] = set()
        for item in clips_raw if isinstance(clips_raw, list) else []:
            if not isinstance(item, dict):
                continue
            clip = Clip.from_dict(item)
            while clip.id in seen:
                clip.id = f"{clip.id}_x"
            seen.add(clip.id)
            clips.append(clip)

        route_raw = data.get("output_route")
        route: dict[str, str] = {}
        if isinstance(route_raw, dict):
            mode_value = str(route_raw.get("mode") or "").strip().lower()
            # 双输出模式已弃置：旧配置里的 dual 一律按「虚拟声卡」处理
            if mode_value not in ("virtual", "local"):
                mode_value = "virtual"
            route = {
                "mode": mode_value,
                "primary_device_name": str(route_raw.get("primary_device_name") or "").strip(),
                "monitor_device_name": str(route_raw.get("monitor_device_name") or "").strip(),
            }

        mixer_raw = data.get("mixer")
        mixer: dict[str, Any] = {}
        if isinstance(mixer_raw, dict):
            def _gain(value, default=1.0) -> float:  # noqa: ANN001
                try:
                    return max(0.0, min(2.0, float(value)))
                except (TypeError, ValueError):
                    return default

            mixer = {
                "enabled": bool(mixer_raw.get("enabled", False)),
                "mic_device_name": str(mixer_raw.get("mic_device_name") or "").strip(),
                "mic_gain": _gain(mixer_raw.get("mic_gain", 1.0)),
                "sfx_gain": _gain(mixer_raw.get("sfx_gain", 1.0)),
                "local_playback": bool(mixer_raw.get("local_playback", False)),
            }

        return cls(
            version=int(data.get("version", CONFIG_VERSION) or CONFIG_VERSION),
            output_device=str(data.get("output_device") or "").strip(),
            output_route=route,
            mixer=mixer,
            master_volume=master,
            exclusive_mode=bool(data.get("exclusive_mode", False)),
            record_mode=mode,
            mic_device=str(data.get("mic_device") or "").strip(),
            system_device=str(data.get("system_device") or "").strip(),
            app_pid=pid_value,
            recent_mic=_str_list("recent_mic"),
            recent_system=_str_list("recent_system"),
            record_hotkey=str(data.get("record_hotkey") or DEFAULT_RECORD_HOTKEY).strip().lower(),
            output_format=fmt,
            save_dir=str(data.get("save_dir") or "").strip(),
            normalize=bool(data.get("normalize", False)),
            write_sidecar=bool(data.get("write_sidecar", True)),
            app_show_inferred=bool(data.get("app_show_inferred", True)),
            show_all_devices=bool(data.get("show_all_devices", False)),
            device_name_rules=rules if isinstance(rules, dict) else {},
            clips=clips,
        )


class ConfigStore:
    """配置加载与原子保存。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.config_path()
        self.config = StudioConfig()
        self.warnings: list[str] = []
        self._library = None                 # ClipLibrary（由界面绑定）
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._dirty = False

    # ------------------------------------------------------------ 绑定音效库
    def bind_library(self, library) -> None:  # noqa: ANN001
        """把 ClipLibrary 交给 Store：保存时从它取音效列表（唯一真相）。"""
        self._library = library

    # ------------------------------------------------------------ 加载
    def load(self) -> StudioConfig:
        with self._lock:
            self.warnings.clear()
            if not self.path.exists():
                self.config = StudioConfig()
                self.save(immediate=True)
                return self.config

            try:
                raw = self.path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("配置根节点不是对象")
                self.config = StudioConfig.from_dict(data)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                backup = self.path.with_suffix(f".broken-{int(time.time())}.json")
                try:
                    shutil.copy2(self.path, backup)
                except OSError:
                    backup = None  # type: ignore[assignment]
                self.warnings.append(
                    f"配置文件无法解析（{exc}），已重置为默认配置"
                    + (f"，原文件备份为 {backup.name}" if backup else "")
                )
                self.config = StudioConfig()
                self.save(immediate=True)
            return self.config

    # ------------------------------------------------------------ 保存
    def save(self, immediate: bool = False) -> None:
        with self._lock:
            self._dirty = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if immediate:
                self._write()
                return
            timer = threading.Timer(0.3, self._write_if_dirty)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _write_if_dirty(self) -> None:
        with self._lock:
            self._timer = None
            if self._dirty:
                self._write()

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._dirty:
                self._write()

    def _write(self) -> None:
        self._dirty = False
        payload = self.config.to_dict()
        if self._library is not None:
            # 音效列表以 ClipLibrary 为准（界面可能刚改过名称/热键/音量）
            payload["clips"] = self._library.to_list()
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            self.warnings.append(f"配置保存失败：{exc}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
