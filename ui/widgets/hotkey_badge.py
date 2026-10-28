"""热键徽章：未绑定 / 已绑定 / 绑定中 / 冲突 / 注册失败 五态，行内捕获按键。

捕获流程（不弹对话框）：
    点击徽章 → 外部先 ``hotkeys.set_suspended(True)`` → ``startCapture()``
    → 显示「按下按键…」并抢键盘 → 收到组合键发 ``binding_changed``
    → ESC 发 ``binding_cancelled``，Backspace/Delete 发 ``binding_changed("")``
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from core.hotkey_manager import (
    InvalidHotkey,
    display_hotkey,
    format_hotkey,
    key_name_from_vk,
    modifier_from_vk,
    parse_hotkey,
)

from .. import theme

HEIGHT = 22

_QT_FALLBACK: dict[int, str] = {}
for _i in range(26):
    _QT_FALLBACK[int(Qt.Key.Key_A) + _i] = chr(ord("a") + _i)
for _i in range(10):
    _QT_FALLBACK[int(Qt.Key.Key_0) + _i] = str(_i)
for _i in range(24):
    _QT_FALLBACK[int(Qt.Key.Key_F1) + _i] = f"f{_i + 1}"

_MODIFIER_KEYS = {
    Qt.Key.Key_Control: "ctrl",
    Qt.Key.Key_Alt: "alt",
    Qt.Key.Key_Shift: "shift",
    Qt.Key.Key_Meta: "win",
}

STATE_UNSET = "unset"
STATE_BOUND = "bound"
STATE_CAPTURING = "capturing"
STATE_CONFLICT = "conflict"
STATE_FAILED = "failed"


class HotkeyBadge(QWidget):
    """可点击的热键徽章。"""

    binding_requested = Signal()
    binding_changed = Signal(str)
    binding_cancelled = Signal()

    def __init__(self, hotkey: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hotkey = (hotkey or "").strip().lower()
        self._state = STATE_BOUND if self._hotkey else STATE_UNSET
        self._message = ""
        self._hover = False
        self.setFixedHeight(HEIGHT)
        self.setMinimumWidth(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._update_size()

    # ------------------------------------------------------------------ 配置
    def hotkey(self) -> str:
        return self._hotkey

    def setHotkey(self, hotkey: str) -> None:  # noqa: N802
        self._hotkey = (hotkey or "").strip().lower()
        if self._state not in (STATE_CONFLICT, STATE_FAILED):
            self._state = STATE_BOUND if self._hotkey else STATE_UNSET
        self._update_size()
        self.update()

    def setState(self, state: str) -> None:  # noqa: N802
        self._state = state
        self._update_size()
        self.update()

    def state(self) -> str:
        return self._state

    def setMessage(self, message: str) -> None:  # noqa: N802
        self._message = message or ""
        tips = [f"当前：{display_hotkey(self._hotkey)}"] if self._hotkey else ["点击绑定快捷键"]
        if self._message:
            tips.append(self._message)
        self.setToolTip("\n".join(tips))

    # ------------------------------------------------------------------ 文本/尺寸
    def _text(self) -> str:
        if self._state == STATE_CAPTURING:
            return "按下按键…"
        if not self._hotkey:
            return "未绑定"
        return display_hotkey(self._hotkey)

    def _font(self) -> QFont:
        size, weight = theme.font_pt("badge")
        font = QFont()
        font.setPointSizeF(size)
        font.setWeight(QFont.Weight(weight))
        return font

    def _update_size(self) -> None:
        painter_font = self._font()
        from PySide6.QtGui import QFontMetrics

        width = QFontMetrics(painter_font).horizontalAdvance(self._text()) + 12
        if self._state == STATE_CAPTURING:
            width = max(width, QFontMetrics(painter_font).horizontalAdvance("按下按键…") + 12)
        self.setFixedWidth(max(64, min(width, 168)))

    # ------------------------------------------------------------------ 捕获
    def startCapture(self) -> None:
        self._state = STATE_CAPTURING
        self._update_size()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabKeyboard()
        self.update()

    def stopCapture(self) -> None:
        self.releaseKeyboard()
        self._state = STATE_BOUND if self._hotkey else STATE_UNSET
        self._update_size()
        self.update()

    def isCapturing(self) -> bool:  # noqa: N802
        return self._state == STATE_CAPTURING

    # ------------------------------------------------------------------ 事件
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.isCapturing():
                self.binding_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self.isCapturing():
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.stopCapture()
            self.binding_cancelled.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self._hotkey = ""
            self.stopCapture()
            self.binding_changed.emit("")
            event.accept()
            return
        if event.isAutoRepeat():
            event.accept()
            return

        vk = int(event.nativeVirtualKey() or 0)
        modifier = modifier_from_vk(vk) if vk else None
        if modifier is not None:
            mods = self._modifiers(event)
            mods.add(modifier)
            self._hotkey = format_hotkey(mods)
            self._update_size()
            self.update()
            event.accept()
            return

        name = key_name_from_vk(vk) or _QT_FALLBACK.get(int(key))
        if (name is None and event.modifiers() & Qt.KeyboardModifier.KeypadModifier
                and int(Qt.Key.Key_0) <= int(key) <= int(Qt.Key.Key_9)):
            name = f"numpad {chr(int(key))}"
        if not name:
            event.accept()
            return

        keys = self._modifiers(event)
        keys.add(name)
        try:
            normalized = format_hotkey(keys)
            parse_hotkey(normalized)
        except InvalidHotkey:
            event.accept()
            return

        self._hotkey = normalized
        self.stopCapture()
        self.binding_changed.emit(normalized)
        event.accept()

    def _modifiers(self, event) -> set[str]:
        mods: set[str] = set()
        for flag, name in (
            (Qt.KeyboardModifier.ControlModifier, "ctrl"),
            (Qt.KeyboardModifier.AltModifier, "alt"),
            (Qt.KeyboardModifier.ShiftModifier, "shift"),
            (Qt.KeyboardModifier.MetaModifier, "win"),
        ):
            if event.modifiers() & flag:
                mods.add(name)
        return mods

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        was_capturing = self.isCapturing()
        if was_capturing:
            self.releaseKeyboard()
            self._state = STATE_BOUND if self._hotkey else STATE_UNSET
        self.update()
        super().focusOutEvent(event)
        # 关键：失焦也算「结束捕获」。以前这里不发信号，导致 HotkeyManager
        # 一直停在 suspended=True，全局热键从此全部失灵（切窗口点一下徽章就中招）。
        if was_capturing:
            self.binding_cancelled.emit()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # ------------------------------------------------------------------ 绘制
    def _palette(self) -> tuple[QColor, QColor, QColor]:
        """(背景, 边框, 文字)。"""
        card = QColor(theme.color("card"))
        if self._state == STATE_CAPTURING:
            return QColor(theme.color("accentSoft")), QColor(theme.color("accent")), QColor(
                theme.color("accent")
            )
        if self._state == STATE_CONFLICT:
            return QColor(theme.color("recordSoft")), QColor(theme.color("record")), QColor(
                theme.color("warning")
            )
        if self._state == STATE_FAILED:
            return QColor(theme.color("warningSoft")), QColor(theme.color("warning")), QColor(
                theme.color("warning")
            )
        if self._state == STATE_BOUND:
            background = card if not self._hover else QColor(theme.color("hover"))
            return background, QColor(theme.color("borderStrong")), QColor(theme.color("textMuted"))
        # 未绑定
        background = QColor(theme.color("hover")) if not self._hover else QColor(
            theme.color("pressed")
        )
        return background, QColor(theme.color("border")), QColor(theme.color("textFaint"))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        background, border, text_color = self._palette()

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, theme.RADIUS["xs"], theme.RADIUS["xs"])

        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.color("accent")), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), theme.RADIUS["xs"],
                                    theme.RADIUS["xs"])

        painter.setFont(self._font())
        painter.setPen(QPen(text_color))
        painter.drawText(
            rect.adjusted(6, 0, -6, 0),
            int(Qt.AlignmentFlag.AlignCenter),
            self._text(),
        )

