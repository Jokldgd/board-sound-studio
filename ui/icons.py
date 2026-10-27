"""线性图标：加载 ``resources/icons/*.svg`` 并按需染色。

为什么不用 emoji / 位图：emoji 在不同系统字体会变形，位图无法换色；
SVG 源文件统一写 ``stroke="currentColor"``，这里把 ``currentColor`` 替换成目标
颜色再交给 ``QSvgRenderer`` 渲染，得到任意尺寸、任意颜色的清晰图标。

用法::

    from ui import icons
    icons.icon("trash-2", 16, theme.color("textFaint"))
    icons.pixmap("mic", 16)            # 默认正文色
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme


def _resource_root() -> Path:
    """图标目录：源码运行用仓库路径，PyInstaller 打包后用解包目录。"""
    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            return Path(bundle) / "resources" / "icons"
    return Path(__file__).resolve().parent.parent / "resources" / "icons"


ICON_DIR = _resource_root()


class IconLibrary:
    """SVG 图标仓库 + 染色缓存。"""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory else ICON_DIR
        self._raw: dict[str, bytes] = {}
        self._cache: dict[tuple[str, int, str, int], QPixmap] = {}

    # ------------------------------------------------------------------ 读取
    def names(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(path.stem for path in self.directory.glob("*.svg"))

    def has(self, name: str) -> bool:
        return (self.directory / f"{name}.svg").is_file()

    def _source(self, name: str) -> bytes | None:
        if name not in self._raw:
            path = self.directory / f"{name}.svg"
            if not path.is_file():
                return None
            self._raw[name] = path.read_bytes()
        return self._raw[name]

    # ------------------------------------------------------------------ 渲染
    def pixmap(
        self,
        name: str,
        size: int = 16,
        color: str | None = None,
        dpr: int | None = None,
    ) -> QPixmap:
        """渲染成 QPixmap（透明底）。缺失图标返回空 pixmap（调用方自行降级）。"""
        tint = (color or theme.color("text")).lower()
        ratio = int(dpr or 2)
        key = (name, int(size), tint, ratio)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        source = self._source(name)
        if source is None:
            empty = QPixmap(0, 0)
            self._cache[key] = empty
            return empty

        data = source.replace(b"currentColor", tint.encode("ascii"))
        renderer = QSvgRenderer(QByteArray(data))
        canvas = QPixmap(int(size * ratio), int(size * ratio))
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        canvas.setDevicePixelRatio(ratio)
        self._cache[key] = canvas
        return canvas

    def icon(self, name: str, size: int = 16, color: str | None = None) -> QIcon:
        icon = QIcon()
        for scale in (1, 2):
            pixmap = self.pixmap(name, size, color, dpr=scale)
            if not pixmap.isNull():
                icon.addPixmap(pixmap)
        return icon

    def clear(self) -> None:
        self._cache.clear()
        self._raw.clear()


ICONS = IconLibrary()


def icon(name: str, size: int = 16, color: str | None = None) -> QIcon:
    return ICONS.icon(name, size, color)


def pixmap(name: str, size: int = 16, color: str | None = None) -> QPixmap:
    return ICONS.pixmap(name, size, color)


def names() -> list[str]:
    return ICONS.names()
