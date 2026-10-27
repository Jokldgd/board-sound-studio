"""应用图标：运行时用 QPainter 画（线性喇叭 + 录音红点），并支持导出 .ico。

不走图片资源：图标要跟着主题色走（强调色底 + 白色线条），
且托盘需要"空闲 / 录音中"两个变体。
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme

_DESIGN = 64.0


@lru_cache(maxsize=32)
def pixmap(size: int = 64, recording: bool = False) -> QPixmap:
    """强调色圆角方块 + 白色喇叭线条；录音中右下角加红点。"""
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / _DESIGN, size / _DESIGN)

    background = QPainterPath()
    background.addRoundedRect(QRectF(3, 3, 58, 58), 15, 15)
    painter.fillPath(background, QColor(theme.color("accent")))

    white = QColor("#FFFFFF")
    body = QPainterPath()
    body.moveTo(13, 26)
    body.lineTo(21, 26)
    body.lineTo(31, 17)
    body.lineTo(31, 47)
    body.lineTo(21, 38)
    body.lineTo(13, 38)
    body.closeSubpath()
    painter.setPen(QPen(white, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(body)

    painter.setPen(QPen(white, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(QRectF(24, 22, 12, 20), -55 * 16, 110 * 16)
    painter.drawArc(QRectF(19, 17, 22, 30), -55 * 16, 110 * 16)

    if recording:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.color("record")))
        painter.drawEllipse(QRectF(40, 40, 18, 18))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.color("border")))
        painter.drawEllipse(QRectF(40, 40, 18, 18))

    painter.end()
    return result


def app_icon(recording: bool = False) -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(pixmap(size, recording))
    return icon


def save_ico(path: str | Path, sizes: tuple[int, ...] | None = None) -> bool:
    """导出 .ico（PNG 负载手工拼 ICONDIR，Qt 自己不写 ico）。"""
    sizes = sizes or (16, 24, 32, 48, 64, 128, 256)
    pngs: list[tuple[int, bytes]] = []
    for size in sizes:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap(size).save(buffer, "PNG"):
            return False
        buffer.close()
        pngs.append((size, bytes(buffer.data())))

    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = len(header) + 16 * len(pngs)
    entries = b""
    payload = b""
    for size, data in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(header + entries + payload)
    return True
