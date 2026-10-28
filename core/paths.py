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


def _appdata_root() -> Path:
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / APP_NAME
    # 非 Windows / 极端环境兜底
    return Path.home() / f".{APP_NAME.replace(' ', '-').lower()}"


def _ensure(directory: Path) -> Path:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return directory
    return directory


def data_dir() -> Path:
    """用户数据根目录（``%APPDATA%\\Soundboard Studio``）。"""
    return _ensure(_appdata_root())


def audio_dir() -> Path:
    """音效目录（录音与导入都落这里）。"""
    return _ensure(data_dir() / "audio")


def temp_dir() -> Path:
    return _ensure(data_dir() / "temp")


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
    """把老版本放在程序目录里的数据搬到 ``%APPDATA%``。

    * **只复制、不删除**旧文件（万一用户想回滚旧版本，原目录仍可用）；
    * 新位置已有 ``config.json`` 或迁移标记时直接跳过；
    * 返回 ``{"migrated": bool, "files": n, "source": str}``，供日志记录。
    """
    target = data_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"migrated": False, "files": 0, "source": ""}
    marker = target / MIGRATION_MARKER
    if (target / "config.json").is_file() or marker.is_file():
        return {"migrated": False, "files": 0, "source": ""}

    for source in legacy_data_candidates():
        if source.resolve() == target.resolve():
            continue
        legacy_config = source / "config.json"
        legacy_audio = source / "audio"
        has_audio = legacy_audio.is_dir() and any(
            p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".flac", ".ogg", ".json")
            for p in legacy_audio.iterdir()
        )
        if not legacy_config.is_file() and not has_audio:
            continue

        copied = 0
        try:
            if legacy_config.is_file():
                shutil.copy2(legacy_config, target / "config.json")
                copied += 1
            if has_audio:
                dest_audio = target / "audio"
                dest_audio.mkdir(parents=True, exist_ok=True)
                for item in legacy_audio.iterdir():
                    if not item.is_file():
                        continue
                    shutil.copy2(item, dest_audio / item.name)
                    copied += 1
            marker.write_text(
                f"migrated from: {source}\nfiles: {copied}\n", encoding="utf-8"
            )
        except OSError:
            # 迁移失败不阻断启动：用户仍可手动把文件拷到 %APPDATA%
            return {"migrated": False, "files": copied, "source": str(source)}
        return {"migrated": True, "files": copied, "source": str(source)}

    return {"migrated": False, "files": 0, "source": ""}
