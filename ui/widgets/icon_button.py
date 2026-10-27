"""图标按钮：28×28 命中区，悬停/按下/禁用/焦点四态，支持行内 loading。

为什么不用 QToolButton + QSS：规格要求「focus 时 2px 强调色描边」，
QSS 没有 outline，只能自绘；顺带把 loading 旋转也收进同一个控件。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget

from .. import icons, theme


class IconButton(QAbstractButton):
    """线性图标按钮（继承 ``clicked`` 信号，用法同普通按钮）。"""

    def __init__(
        self,
        icon_name: str,
        icon_size: int = theme.SIZE["iconInline"],
        button_size: int = theme.SIZE["btnIcon"],
        variant: str = "ghost",
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._icon_size = int(icon_size)
        self._variant = variant
        self._fixed_color: str | None = None
        self._hover = False
        self._loading = False
        self._angle = 0
        self._spin = QTimer(self)
        self._spin.setInterval(40)
        self._spin.timeout.connect(self._on_spin)

        self.setFixedSize(QSize(button_size, button_size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        if tooltip:
            self.setToolTip(tooltip)

    # ------------------------------------------------------------------ 配置
    def setIconName(self, name: str, color: str | None = None) -> None:
        self._icon_name = name
        self._fixed_color = color
        self.update()

    def setIconColor(self, color: str | None) -> None:
        self._fixed_color = color
        self.update()

    def setVariant(self, variant: str) -> None:
        self._variant = variant
        self.update()

    def setLoading(self, loading: bool) -> None:
        """行内 loading：换成旋转的 refresh 图标并暂时吞掉点击。"""
        loading = bool(loading)
        if loading == self._loading:
            return
        self._loading = loading
        if loading:
            self._spin.start()
        else:
            self._spin.stop()
        self.update()

    def is_loading(self) -> bool:
        return self._loading

    # ------------------------------------------------------------------ 颜色
    def _icon_color(self) -> str:
        if self._fixed_color:
            return self._fixed_color
        if not self.isEnabled():
            return theme.color("textFaint")
        if self._variant == "danger":
            return theme.color("record") if (self._hover or self.isDown()) else theme.color("textFaint")
        if self._variant == "accent":
            return theme.color("accent")
        if self._hover or self.isDown():
            return theme.color("text")
        return theme.color("textMuted")

    def _background(self) -> QColor:
        if not self.isEnabled():
            return QColor(Qt.GlobalColor.transparent)
        if self.isDown():
            return QColor(theme.color("pressed"))
        if self._hover:
            if self._variant == "danger":
                return QColor(theme.color("recordSoft"))
            return QColor(theme.color("hover"))
        return QColor(Qt.GlobalColor.transparent)

    # ------------------------------------------------------------------ 事件
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._loading:
            event.ignore()
            return
        super().mousePressEvent(event)

    def _on_spin(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        background = self._background()
        if background.alpha() > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, theme.RADIUS["sm"], theme.RADIUS["sm"])

        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.color("accent")), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                rect.adjusted(1, 1, -1, -1), theme.RADIUS["xs"], theme.RADIUS["xs"]
            )

        name = "refresh-cw" if self._loading else self._icon_name
        pixmap = icons.pixmap(name, self._icon_size, self._icon_color())
        if pixmap.isNull():
            return
        target = QRectF(
            (self.width() - self._icon_size) / 2,
            (self.height() - self._icon_size) / 2,
            self._icon_size,
            self._icon_size,
        )
        if self._loading:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            center = target.center()
            painter.translate(center)
            painter.rotate(self._angle)
            painter.translate(-center)
        painter.drawPixmap(target.toRect(), pixmap)
