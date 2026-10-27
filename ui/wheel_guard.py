"""全局交互护栏：滚轮不误改下拉框 / 数字框。

Windows 上鼠标滚轮划过未聚焦的 `QComboBox` 会直接改选项——在设置里滚动页面时
非常容易误改（比如把"路由模式"滚成了别的）。这里统一拦掉：
* **未聚焦**时滚轮不改变值，并把滚轮事件转发给最近的可滚动祖先（页面照常滚）；
* 只有点了下拉、它拿到焦点之后，滚轮才允许切换选项（和浏览器一致）。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox


class WheelGuard(QObject):
    """装在 QApplication 上的事件过滤器。"""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel:
            return False
        if not isinstance(obj, (QComboBox, QAbstractSpinBox)):
            return False
        if obj.hasFocus():
            return False
        # 未聚焦：吞掉这次滚轮，改交给最近的可滚动容器，页面滚动不受影响
        scroller = obj.parentWidget()
        while scroller is not None and not isinstance(scroller, QAbstractScrollArea):
            scroller = scroller.parentWidget()
        if scroller is not None:
            QApplication.sendEvent(scroller.viewport(), event)
        return True


def install(app: QApplication) -> WheelGuard:
    """安装护栏并返回实例（调用方需持有引用，否则会被回收）。"""
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
