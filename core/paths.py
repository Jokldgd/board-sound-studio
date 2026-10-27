"""项目内路径（Soundboard Studio）。

* 源码运行：基准目录 = 项目根（``soundboard_studio/``）
* PyInstaller 冻结：基准目录 = exe 所在目录（配置/audio/temp 都跟着 exe 走，
  不能放 ``_MEIPASS``，那是每次启动解压、退出即删的临时目录）
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "Soundboard Studio"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _ensure(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    keep = directory / ".gitkeep"
    if not keep.exists():
        try:
            keep.touch()
        except OSError:
            pass
    return directory


def audio_dir() -> Path:
    """音效文件目录（导入与录音都落在这里）。"""
    return _ensure(base_dir() / "audio")


def temp_dir() -> Path:
    """录音临时产物。"""
    return _ensure(base_dir() / "temp")


def config_path() -> Path:
    return base_dir() / "config.json"


def log_path() -> Path:
    return base_dir() / "studio.log"


def asset(*parts: str) -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    root = Path(meipass) if meipass else base_dir()
    return root.joinpath(*parts)


def resolve_relative(rel_path: str | Path) -> Path:
    """配置里存相对路径（``audio/xxx.wav``），还原成绝对路径。"""
    path = Path(rel_path)
    if path.is_absolute():
        return path
    return base_dir() / path


def to_relative(abs_path: str | Path) -> str:
    """绝对路径 -> 相对 base_dir 的正斜杠路径，便于 JSON 阅读。"""
    path = Path(abs_path).resolve()
    try:
        return path.relative_to(base_dir().resolve()).as_posix()
    except ValueError:
        return path.as_posix()
