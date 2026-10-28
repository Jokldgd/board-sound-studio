"""音效列表：数据模型 + 文件操作 + 持久化。

设计要点：
* ``Clip`` 是纯数据（id/名称/文件/热键/音量/播放模式/来源），不持有播放或录音对象，
  这样 UI 与引擎都能安全地按 id 引用它；
* **目录即真相**：启动时扫描 ``audio/``，把没登记的音频补进来（来源按文件前缀判断），
  有同名 ``.json`` 就读它的 name/volume 覆盖默认值 —— 所以手工往目录里丢文件也能用；
* 录音保存走 ``save_recorded()``：写 ``rec_YYYYMMDD_HHMMSS.wav``（+ 可选 sidecar），
  并把新 ``Clip`` 直接返回，界面**不需要重新扫描目录**；
* 删除时只删 ``audio/`` 目录内的文件（防止配置被手改后误删别处）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from . import audio_io, paths

_LOGGER = logging.getLogger("studio.core")
from .constants import (
    CLIP_MODES,
    CLIP_TOGGLE,
    MAX_VOLUME,
    REC_PREFIX,
    SAMPLE_RATE,
    SOURCE_IMPORT,
    SOURCE_RECORD,
    SUPPORTED_EXTS,
)

#: Windows 文件名非法字符
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ClipError(Exception):
    """导入/保存失败，消息面向用户。"""


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def safe_filename(name: str, fallback: str = "audio") -> str:
    cleaned = _ILLEGAL.sub("_", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or fallback


@dataclass
class Clip:
    """一条音效。"""

    id: str
    name: str
    file: str                       # 相对 base_dir 的路径：audio/xxx.wav
    hotkey: str = ""
    volume: float = 1.0             # 0.0 ~ 2.0
    mode: str = CLIP_TOGGLE         # toggle / hold
    source: str = SOURCE_IMPORT     # import / record
    created_at: str = ""            # ISO 时间，仅用于排序展示

    # ---- 派生信息 ----
    def path(self) -> Path:
        return paths.resolve_relative(self.file)

    def exists(self) -> bool:
        try:
            return self.path().is_file()
        except OSError:
            return False

    def volume_percent(self) -> int:
        return int(round(self.volume * 100))

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "file": self.file,
            "hotkey": self.hotkey,
            "volume": round(self.volume, 3),
            "mode": self.mode,
            "source": self.source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Clip":
        raw_volume = data.get("volume", 1.0)
        try:
            volume = clamp(float(raw_volume), 0.0, MAX_VOLUME)
        except (TypeError, ValueError):
            volume = 1.0
        mode = str(data.get("mode") or CLIP_TOGGLE)
        if mode not in CLIP_MODES:
            mode = CLIP_TOGGLE
        source = str(data.get("source") or SOURCE_IMPORT)
        if source not in (SOURCE_IMPORT, SOURCE_RECORD):
            source = SOURCE_IMPORT
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            name=str(data.get("name") or "未命名"),
            file=str(data.get("file") or ""),
            hotkey=str(data.get("hotkey") or "").strip().lower(),
            volume=volume,
            mode=mode,
            source=source,
            created_at=str(data.get("created_at") or ""),
        )


# ---------------------------------------------------------------- 目录约定
def record_filename(moment: datetime | None = None) -> str:
    """录音文件主干：rec_YYYYMMDD_HHMMSS.wav"""
    moment = moment or datetime.now()
    return f"{REC_PREFIX}{moment.strftime('%Y%m%d_%H%M%S')}.wav"


def friendly_name(moment: datetime | None = None) -> str:
    """录音默认显示名：录音 14-32-05"""
    moment = moment or datetime.now()
    return f"录音 {moment.strftime('%H-%M-%S')}"


def sidecar_path(media: Path) -> Path:
    """同名 .json（录音元数据）。"""
    return media.with_suffix(".json")


#: 音效目录覆盖（来自 config.save_dir）；None = 用默认 audio/
_audio_root_override: Path | None = None


def audio_root() -> Path:
    """当前生效的音效目录：配置指定的 或 默认 ``audio/``。"""
    if _audio_root_override is not None:
        return _audio_root_override
    return paths.audio_dir()


def write_sidecar_file(media: str | Path, name: str, volume: float = 1.0) -> Path | None:
    """给任意音频文件写同名 .json（"另存为"复用）。失败返回 None。"""
    target = sidecar_path(Path(media))
    payload = {"name": str(name), "volume": round(clamp(float(volume), 0.0, MAX_VOLUME), 3)}
    tmp = target.with_name(target.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return target


class ClipLibrary:
    """音效列表的全部数据操作。"""

    def __init__(self, clips: Iterable[Clip] | None = None) -> None:
        self.clips: list[Clip] = list(clips or [])

    # ------------------------------------------------------------ 音效目录
    def configure_audio_dir(self, raw: str | None) -> Path:
        """设置本库使用的音效目录（来自 config.save_dir）。

        ``raw`` 为空 → 用默认的 ``%APPDATA%\\Soundboard Studio\\audio``；
        绝对路径原样使用。**目录不存在时回退到默认目录**（并记日志）：
        老配置里可能残留旧安装路径，如果直接用它，界面上就会"audio 里有文件
        但列表是空的"。
        """
        global _audio_root_override
        if raw and str(raw).strip():
            text = str(raw).strip()
            path = Path(text)
            candidate = path if path.is_absolute() else paths.resolve_relative(text)
            if candidate.is_dir():
                _audio_root_override = candidate
            else:
                _LOGGER.warning("配置的音效目录不存在，回退到默认目录：%s", candidate)
                _audio_root_override = None
        else:
            _audio_root_override = None
        root = audio_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return root

    def audio_dir(self) -> Path:
        """当前生效的音效目录。"""
        return audio_root()

    # ------------------------------------------------------------ 查询
    def get(self, clip_id: str) -> Clip | None:
        for clip in self.clips:
            if clip.id == clip_id:
                return clip
        return None

    def by_file(self, path: Path) -> Clip | None:
        try:
            target = path.resolve()
        except OSError:
            return None
        for clip in self.clips:
            try:
                if clip.path().resolve() == target:
                    return clip
            except OSError:
                continue
        return None

    def hotkey_map(self) -> dict[str, Clip]:
        """{热键: Clip}，顺序即"谁先抢到"。"""
        result: dict[str, Clip] = {}
        for clip in self.clips:
            if clip.hotkey and clip.hotkey not in result:
                result[clip.hotkey] = clip
        return result

    def duplicate_hotkeys(self) -> dict[str, list[str]]:
        """程序内部重复占用的热键 -> 名称列表。"""
        buckets: dict[str, list[str]] = {}
        for clip in self.clips:
            if clip.hotkey:
                buckets.setdefault(clip.hotkey, []).append(clip.name)
        return {key: names for key, names in buckets.items() if len(names) > 1}

    def missing_files(self) -> list[Clip]:
        return [clip for clip in self.clips if not clip.exists()]

    # ------------------------------------------------------------ 增删改
    def new_id(self) -> str:
        existing = {clip.id for clip in self.clips}
        while True:
            candidate = uuid.uuid4().hex[:8]
            if candidate not in existing:
                return candidate

    def add(self, clip: Clip) -> Clip:
        self.clips.append(clip)
        return clip

    def rename(self, clip_id: str, name: str) -> bool:
        """改名字，并**同步写回同名 .json sidecar**。

        为什么必须写 sidecar：启动时 ``scan_directory()`` 会用 sidecar 覆盖条目名称
        （设计上允许用户手改 json）。以前只改了内存 + config.json，重启就被旧
        sidecar 覆盖回去，表现为"关闭后改名消失"。
        """
        clip = self.get(clip_id)
        if clip is None:
            return False
        new_name = (name or "").strip()
        if not new_name or new_name == clip.name:
            return False
        clip.name = new_name
        self._sync_sidecar(clip)
        return True

    def set_volume(self, clip_id: str, volume: float) -> bool:
        """改单条音量，同样同步 sidecar（否则重启会被旧值覆盖）。"""
        clip = self.get(clip_id)
        if clip is None:
            return False
        value = clamp(float(volume), 0.0, MAX_VOLUME)
        if abs(value - clip.volume) <= 1e-6:
            return False
        clip.volume = value
        self._sync_sidecar(clip)
        return True

    def _sync_sidecar(self, clip: Clip) -> None:
        """把当前名称/音量写进音频文件旁的 .json。

        只更新**已经存在**的 sidecar：用户关掉了"写 sidecar"时不该凭空造文件；
        没有 sidecar 的情况下，启动扫描不会覆盖名称，config.json 就够了。
        """
        try:
            path = clip.path()
            if not path.is_file():
                return
            if not sidecar_path(path).is_file():
                return
            write_sidecar_file(path, clip.name, clip.volume)
        except OSError:
            pass

    def remove(self, clip_id: str, delete_file: bool = True) -> bool:
        clip = self.get(clip_id)
        if clip is None:
            return False
        self.clips.remove(clip)
        if delete_file:
            self._unlink_audio_file(clip)
        return True

    def move(self, clip_id: str, delta: int) -> bool:
        for index, clip in enumerate(self.clips):
            if clip.id != clip_id:
                continue
            target = int(clamp(index + delta, 0, len(self.clips) - 1))
            if target == index:
                return False
            self.clips.insert(target, self.clips.pop(index))
            return True
        return False

    def _unlink_audio_file(self, clip: Clip) -> None:
        """只删 audio/ 目录内的文件 + 同名 sidecar。"""
        try:
            target = clip.path().resolve()
            root = audio_root().resolve()
        except OSError:
            return
        if target.parent != root:
            return
        for path in (target, sidecar_path(target)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------ 导入
    def import_files(self, sources: Iterable[str | Path]) -> tuple[list[Clip], list[str]]:
        """把外部音频复制进 audio/ 并建 Clip。返回 (新增, 错误信息)。"""
        added: list[Clip] = []
        errors: list[str] = []
        root_dir = audio_root().resolve()

        for raw in sources:
            source = Path(raw)
            if source.is_dir():
                candidates = sorted(
                    p for p in source.iterdir()
                    if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
                )
                if not candidates:
                    errors.append(f"{source.name}：文件夹里没有支持的音频")
                    continue
                for candidate in candidates:
                    try:
                        added.append(self._import_one(candidate, root_dir))
                    except ClipError as exc:
                        errors.append(f"{Path(candidate).name}：{exc}")
                continue
            try:
                added.append(self._import_one(source, root_dir))
            except ClipError as exc:
                errors.append(f"{source.name}：{exc}")
        return added, errors

    def _import_one(self, source: Path, audio_root: Path) -> Clip:
        if not source.is_file():
            raise ClipError("文件不存在")
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_EXTS:
            raise ClipError(f"不支持的格式 {suffix or '(无扩展名)'}")

        try:
            inside = source.resolve().parent == audio_root
        except OSError:
            inside = False

        if inside:
            dest = source
            existing = self.by_file(dest)
            if existing is not None:
                raise ClipError("这个文件已经在列表里了")
        else:
            # 同名 + 同大小视为同一个文件重复导入（避免无声无息多出一份副本）
            try:
                size = source.stat().st_size
            except OSError:
                size = -1
            for clip in self.clips:
                try:
                    path = clip.path()
                    if path.name.rsplit(".", 1)[0] == source.stem and path.stat().st_size == size:
                        raise ClipError("这个文件已经在列表里了（同名同大小）")
                except OSError:
                    continue

            stem = safe_filename(source.stem)
            dest = audio_root / f"{stem}{suffix}"
            index = 2
            while dest.exists():
                dest = audio_root / f"{stem} ({index}){suffix}"
                index += 1
            try:
                shutil.copy2(source, dest)
            except OSError as exc:
                raise ClipError(f"复制失败：{exc}") from exc

        clip = Clip(
            id=self.new_id(),
            name=source.stem.strip() or dest.stem,
            file=paths.to_relative(dest),
            source=SOURCE_IMPORT,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        return self.add(clip)

    # ------------------------------------------------------------ 录音保存
    def save_recorded(
        self,
        data: np.ndarray,
        name: str,
        samplerate: int = SAMPLE_RATE,
        write_sidecar: bool = True,
        volume: float = 1.0,
        moment: datetime | None = None,
    ) -> Clip:
        """把录音落盘并加入列表（一步完成"保存到音效板"）。

        文件名严格 ``rec_YYYYMMDD_HHMMSS.wav``；同一秒撞车加 ``_1``。
        """
        root_dir = audio_root()
        stem = Path(record_filename(moment)).stem
        target = root_dir / f"{stem}.wav"
        index = 1
        while target.exists() or self.by_file(target) is not None:
            target = root_dir / f"{stem}_{index}.wav"
            index += 1
            if index > 999:
                raise ClipError("同名文件过多，无法生成新文件名")

        try:
            audio_io.write_wav(target, data, samplerate)
        except audio_io.AudioError as exc:
            raise ClipError(str(exc)) from exc

        display_name = (name or "").strip() or friendly_name(moment)
        if write_sidecar:
            self._write_sidecar(target, display_name, volume)

        clip = Clip(
            id=self.new_id(),
            name=display_name,
            file=paths.to_relative(target),
            volume=clamp(float(volume), 0.0, MAX_VOLUME),
            source=SOURCE_RECORD,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        return self.add(clip)

    def _write_sidecar(self, media: Path, name: str, volume: float) -> Path | None:
        """写同名 .json（音效板/本程序下次扫描时会读它）。"""
        target = sidecar_path(media)
        payload = {"name": name, "volume": round(clamp(volume, 0.0, MAX_VOLUME), 3)}
        tmp = target.with_name(target.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return target

    # ------------------------------------------------------------ 扫描目录
    def scan_directory(self) -> tuple[list[Clip], int]:
        """扫描 audio/：把未登记的文件补进列表（名字/音量取 sidecar）。

        已登记的条目**保持 config 里的名称与音量不变**（config 是唯一真相），
        返回值第二个元素现在恒为 0，仅为兼容旧调用方保留。
        """
        added: list[Clip] = []
        updated = 0
        root_dir = audio_root()
        try:
            files = sorted(
                p for p in root_dir.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
            )
        except OSError:
            return added, updated

        for path in files:
            clip = self.by_file(path)
            meta = self._read_sidecar(path)

            if clip is None:
                is_recording = path.stem.startswith(REC_PREFIX)
                clip = Clip(
                    id=self.new_id(),
                    name=str(meta.get("name") or path.stem),
                    file=paths.to_relative(path),
                    source=SOURCE_RECORD if is_recording else SOURCE_IMPORT,
                    created_at=datetime.now().isoformat(timespec="seconds"),
                )
                if "volume" in meta:
                    try:
                        clip.volume = clamp(float(meta["volume"]), 0.0, MAX_VOLUME)
                    except (TypeError, ValueError):
                        pass
                self.add(clip)
                added.append(clip)
                continue

            # 已登记条目：**以 config.json 为准**，sidecar 不再覆盖。
            #
            # 历史坑：以前这里拿 sidecar 覆盖名称/音量，而界面改名只写了 config，
            # 于是重启就被旧 sidecar 打回原形（"关闭后修改消失"）。现在改名/音量
            # 会同步 sidecar（见 rename/set_volume），config 又是唯一真相，
            # 两边一致；即使 sidecar 是旧的（比如从归档恢复的配置），也听 config 的。
            continue

        return added, updated

    @staticmethod
    def _read_sidecar(path: Path) -> dict[str, Any]:
        sidecar = sidecar_path(path)
        if not sidecar.is_file():
            return {}
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------ 序列化
    def to_list(self) -> list[dict[str, Any]]:
        return [clip.to_dict() for clip in self.clips]

    @classmethod
    def from_list(cls, items: Iterable[dict[str, Any]]) -> "ClipLibrary":
        clips: list[Clip] = []
        seen: set[str] = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            clip = Clip.from_dict(item)
            while clip.id in seen:
                clip.id = uuid.uuid4().hex[:8]
            seen.add(clip.id)
            clips.append(clip)
        return cls(clips)
