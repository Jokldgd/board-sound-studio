"""路径管理（Soundboard Studio）。

数据与程序**彻底分离**（1.2.0 起）：

* 程序本体 / 依赖 / 资源：exe 所在目录（PyInstaller onedir）、源码运行时是项目根
* **用户数据**：``%APPDATA%\\Soundboard Studio\\``
    - ``audio\\``        录音、导入的音效（含同名 .json sidecar）
    - ``config.json``   配置
    - ``studio.log``    日志
    - ``temp\\``        录音临时产物

这样卸载程序（删 Program Files）永远不会碰到用户数据；卸载器再单独询问
"是否删除用户数据"。

老版本把 ``audio/`` 与 ``config.json`` 放在程序目录里，这里提供
:func:`migrate_legacy_data` 做一次性平滑迁移（**只复制、不删除**）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Soundboard Studio"

#: 数据目录环境变量（测试/便携模式用；设置后不再用 %APPDATA%）
DATA_DIR_ENV = "SOUNDBOARD_DATA_DIR"

#: 迁移完成标记（避免每次启动都重复复制）
MIGRATION_MARKER = ".migrated-from-install-dir"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    """程序目录（只读语义：不放用户数据）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _probe_writable(directory: Path) -> bool:
    """目录能否真的写入（mkdir 成功不代表能写，杀软/受控文件夹访问会拦）。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _ensure(directory: Path) -> Path:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return directory
    return directory


#: data_dir() 的缓存：探测一次，避免每次调用都写测试文件
_DATA_DIR_CACHE: Path | None = None


def _data_candidates() -> list[Path]:
    """按优先级列出数据目录候选：APPDATA → LOCALAPPDATA → 程序目录 → TEMP。"""
    candidates: list[Path] = []
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.append(Path(appdata) / APP_NAME)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / APP_NAME)
    candidates.append(base_dir() / "data")
    temp = (os.environ.get("TEMP") or os.environ.get("TMP") or "").strip()
    if temp:
        candidates.append(Path(temp) / "SoundboardStudio-data")
    else:
        candidates.append(Path.home() / f".{APP_NAME.replace(' ', '-').lower()}")
    # 去重（保持顺序）
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def default_data_dir() -> Path:
    """首选数据目录（不做可写探测），用于提示"已降级"。"""
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / f".{APP_NAME.replace(' ', '-').lower()}"


def data_dir() -> Path:
    """用户数据根目录；APPDATA 不可写时自动降级（安全软件拦截的场景）。

    真实踩坑：`%APPDATA%\\Soundboard Studio\\temp` 被安全软件挡住写入，
    录音一结束就抛 WinError 5，整个程序弹“未处理的问题”。现在先探测
    “能写”，再决定用哪个目录。
    """
    global _DATA_DIR_CACHE
    if _DATA_DIR_CACHE is not None:
        return _DATA_DIR_CACHE
    for candidate in _data_candidates():
        if _probe_writable(candidate):
            _DATA_DIR_CACHE = candidate
            return candidate
    # 全都不行：保留首选路径（只读使用，写入处会提示用户）
    _DATA_DIR_CACHE = _data_candidates()[0]
    return _DATA_DIR_CACHE


def reset_data_dir_cache() -> None:
    """测试用：清掉 data_dir 缓存。"""
    global _DATA_DIR_CACHE
    _DATA_DIR_CACHE = None


def audio_dir() -> Path:
    """音效目录（录音与导入都落这里）。"""
    return _ensure(data_dir() / "audio")


def temp_dir() -> Path:
    """录音临时产物；APPDATA 的 temp 不可写时退到 LOCALAPPDATA / 系统 TEMP。"""
    primary = data_dir() / "temp"
    if _probe_writable(primary):
        return primary
    for candidate in _data_candidates():
        alt = candidate / "temp"
        if alt.resolve() == primary.resolve():
            continue
        if _probe_writable(alt):
            return alt
    return primary


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "studio.log"


def asset(*parts: str) -> Path:
    """只读资源（图标/SVG）：始终跟着程序目录/_MEIPASS。"""
    meipass = getattr(sys, "_MEIPASS", None)
    root = Path(meipass) if meipass else base_dir()
    return root.joinpath(*parts)


def resolve_relative(rel_path: str | Path) -> Path:
    """配置里存的是相对数据目录的路径（``audio/xxx.wav``）。"""
    path = Path(rel_path)
    if path.is_absolute():
        return path
    return data_dir() / path


def to_relative(abs_path: str | Path) -> str:
    """绝对路径 -> 相对数据目录的正斜杠路径。"""
    path = Path(abs_path).resolve()
    try:
        return path.relative_to(data_dir().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# --------------------------------------------------------------------------- 迁移
def legacy_data_candidates() -> list[Path]:
    """老版本可能放用户数据的位置（按优先级）。"""
    candidates = [base_dir()]
    if is_frozen():
        candidates.append(Path(sys.executable).resolve().parent)
    return candidates


def migrate_legacy_data() -> dict[str, int | bool]:
    """把老版本放在程序目录里的数据搬到 ``%APPDATA%``（幂等，每次启动都检查）。

    设计要点（踩过的坑）：
    * **按文件粒度**迁移，不再"有 config 就整体跳过"。1.2.0 早期版本只要
      ``%APPDATA%\\...\\config.json`` 存在就直接返回，结果用户把录音放回程序目录后
      永远迁不过去 —— 表现就是"audio 里有文件，程序列表却是空的"。
    * config.json 只在目标缺失时复制（避免覆盖用户新配置）；
      audio 按**文件名**补齐（已存在的不动，只复制缺的）。
    * **只复制、不删除**旧文件（可回滚旧版本）。
    """
    target = data_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"migrated": False, "files": 0, "source": ""}

    audio_target = target / "audio"
    try:
        audio_target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"migrated": False, "files": 0, "source": ""}

    copied = 0
    source_used = ""
    audio_suffixes = {".wav", ".mp3", ".flac", ".ogg", ".json"}

    sources = [s for s in legacy_data_candidates() if s.resolve() != target.resolve()]

    # 1) 配置：只在目标没有时复制
    if not (target / "config.json").is_file():
        for source in sources:
            legacy_config = source / "config.json"
            if not legacy_config.is_file():
                continue
            try:
                shutil.copy2(legacy_config, target / "config.json")
            except OSError:
                continue
            copied += 1
            source_used = str(source)
            break

    # 2) 音频：按文件名补齐（幂等；每次启动都跑，能救"放回去却加载不出来"）
    try:
        existing = {p.name for p in audio_target.iterdir() if p.is_file()}
    except OSError:
        existing = set()

    for source in sources:
        legacy_audio = source / "audio"
        if not legacy_audio.is_dir():
            continue
        try:
            items = list(legacy_audio.iterdir())
        except OSError:
            continue
        for item in items:
            if not item.is_file() or item.suffix.lower() not in audio_suffixes:
                continue
            if item.name in existing:
                continue
            try:
                shutil.copy2(item, audio_target / item.name)
            except OSError:
                continue
            existing.add(item.name)
            copied += 1
            source_used = source_used or str(source)

    if copied:
        try:
            (target / MIGRATION_MARKER).write_text(
                f"migrated from: {source_used}\nfiles: {copied}\n", encoding="utf-8"
            )
        except OSError:
            pass
        return {"migrated": True, "files": copied, "source": source_used}

    return {"migrated": False, "files": 0, "source": ""}
