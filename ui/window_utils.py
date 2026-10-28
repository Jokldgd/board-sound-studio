"""窗口/对话框的小工具：把模态框居中对齐到父窗口。

为什么不用 `self.move(500, 500)`：
* 主窗口在不同分辨率/DPI 下大小不同，写死坐标会跑到屏幕角落；
* 用父窗口的 `frameGeometry()` 计算中心点，4K/1080p、100%/200% 都能居中。
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QWidget


def center_on_parent(dialog: QDialog, parent: QWidget | None = None) -> None:
    """把 ``dialog`` 居中到 ``parent``（默认取 dialog.parentWidget()）。"""
    target = parent if parent is not None else dialog.parentWidget()
    if target is None:
        return
    dialog.adjustSize()
    geometry = target.frameGeometry()
    if geometry.isNull():
        return
    frame = dialog.frameGeometry()
    x = geometry.center().x() - frame.width() // 2
    y = geometry.center().y() - frame.height() // 2
    # 先夹在父窗口矩形内（4K/多屏/高缩放都适用）；
    # 只有父窗口缺失时才退到屏幕可用区域。
    x = max(geometry.left(), min(x, max(geometry.left(),
                                           geometry.right() - frame.width())))
    y = max(geometry.top(), min(y, max(geometry.top(),
                                          geometry.bottom() - frame.height())))
    dialog.move(x, y)


def exec_centered(dialog: QDialog, parent: QWidget | None = None) -> int:
    """居中后 exec()，返回 QDialog 的结果码。"""
    center_on_parent(dialog, parent)
    return dialog.exec()
