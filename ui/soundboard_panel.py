"""左栏：音效列表面板（工具栏 / 列表行 / 空状态 / 拖拽覆盖层）。

面板只做两件事：把 ``Clip`` 显示成一行、把用户操作变成信号发出去；
改配置、播放、删除等动作由 ``ui/board_controller.py`` 处理。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.clip_library import Clip
from core.constants import SOURCE_RECORD, SOURCE_TITLES
from core.hotkey_manager import HotkeyState

from . import icons, theme
from .widgets import DropOverlay, EmptyState, HotkeyBadge, IconButton, ThinSlider
from .widgets.hotkey_badge import (
    STATE_BOUND,
    STATE_CONFLICT,
    STATE_FAILED,
    STATE_UNSET,
)

SEARCH_THRESHOLD = 15


class SoundRow(QWidget):
    """一条音效：色点 | 名称 | 热键徽章 | 试听 | 音量 | 删除（高 56）。"""

    preview_toggled = Signal(str)
    hotkey_bind_requested = Signal(str)
    hotkey_changed = Signal(str, str)
    rename_committed = Signal(str, str)
    delete_requested = Signal(str)
    volume_changed = Signal(str, float)

    def __init__(self, clip: Clip, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SoundRow")
        self.clip_id = clip.id
        self._playing = False
        self._hover = False
        self._name = clip.name
        self._hotkey = (clip.hotkey or "").strip().lower()
        self._volume_hint = clip.volume_percent()
        self.setFixedHeight(theme.SIZE["row"])
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # QWidget 子类要显式开启样式背景，否则 QSS 里的 hover/playing 背景不会画
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("hovered", "false")
        self.setProperty("playing", "false")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(theme.SPACE["md"])

        # 来源色点：录音红 / 导入灰
        self.source_dot = QLabel(self)
        self.source_dot.setObjectName("SourceDot")
        self.source_dot.setFixedSize(8, 8)
        self.source_dot.setProperty(
            "source", "record" if clip.source == SOURCE_RECORD else "import"
        )
        self.source_dot.setToolTip(SOURCE_TITLES.get(clip.source, ""))
        layout.addWidget(self.source_dot)

        # 名称（双击进入行内编辑）
        self.name_label = QLabel(clip.name, self)
        self.name_label.setObjectName("ClipName")
        self.name_label.setWordWrap(False)
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name_label.mouseDoubleClickEvent = self._begin_rename  # type: ignore[method-assign]
        layout.addWidget(self.name_label, 1)

        self.name_edit = QLineEdit(self)
        self.name_edit.setObjectName("RowNameEdit")
        self.name_edit.setFixedHeight(24)
        self.name_edit.setVisible(False)
        self.name_edit.editingFinished.connect(self._commit_rename)
        layout.addWidget(self.name_edit, 1)
        self._name_full = clip.name

        self.hotkey_badge = HotkeyBadge(self._hotkey, self)
        self.hotkey_badge.binding_requested.connect(
            lambda: self.hotkey_bind_requested.emit(self.clip_id)
        )
        self.hotkey_badge.binding_changed.connect(
            lambda value: self.hotkey_changed.emit(self.clip_id, value)
        )
        layout.addWidget(self.hotkey_badge)

        self.preview_button = IconButton("play", 16, 28, "ghost", "试听", self)
        self.preview_button.clicked.connect(lambda: self.preview_toggled.emit(self.clip_id))
        layout.addWidget(self.preview_button)

        volume_box = QHBoxLayout()
        volume_box.setContentsMargins(0, 0, 0, 0)
        volume_box.setSpacing(theme.SPACE["xs"])
        self.volume_slider = ThinSlider(parent=self)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setFixedWidth(64)
        self.volume_slider.setDimmed(True)
        self.volume_slider.setToolTip("单条音量 0% ~ 200%")
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        volume_box.addWidget(self.volume_slider)
        self.volume_label = QLabel(f"{clip.volume_percent()}%", self)
        self.volume_label.setObjectName("VolumeValue")
        self.volume_label.setFixedWidth(34)
        self.volume_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        volume_box.addWidget(self.volume_label)
        layout.addLayout(volume_box)

        # 改名按钮（和双击名称等价，给"不知道能双击"的人一个明确入口）
        self.rename_button = IconButton("pencil", 16, 28, "ghost", "修改名字", self)
        self.rename_button.clicked.connect(lambda: self._begin_rename(None))
        layout.addWidget(self.rename_button)

        self.delete_button = IconButton("trash-2", 16, 28, "danger", "删除", self)
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.clip_id))
        layout.addWidget(self.delete_button)

        self.update_clip(clip)

    # ------------------------------------------------------------------ 数据
    def update_clip(self, clip: Clip) -> None:
        self._name = clip.name
        self._name_full = clip.name
        self._apply_name_text()
        self.name_label.setToolTip(clip.name)
        self.source_dot.setProperty(
            "source", "record" if clip.source == SOURCE_RECORD else "import"
        )
        self.source_dot.setToolTip(SOURCE_TITLES.get(clip.source, ""))
        _repolish(self.source_dot)

        self._volume_hint = clip.volume_percent()
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(self._volume_hint)
        self.volume_slider.blockSignals(False)
        self.volume_label.setText(f"{self._volume_hint}%")

        self.set_hotkey(clip.hotkey or "")

    def _apply_name_text(self) -> None:
        """按当前宽度做省略号裁剪（QLabel 不会自动 elide，长了会顶到隔壁控件）。"""
        metrics = self.name_label.fontMetrics()
        available = max(0, self.name_label.width() - 2)
        if available <= 0:
            self.name_label.setText(self._name_full)
            return
        self.name_label.setText(
            metrics.elidedText(self._name_full, Qt.TextElideMode.ElideRight, available)
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_name_text()

    def set_hotkey(self, hotkey: str) -> None:
        self._hotkey = (hotkey or "").strip().lower()
        self.hotkey_badge.setHotkey(self._hotkey)
        if not self._hotkey:
            self.hotkey_badge.setState(STATE_UNSET)
        else:
            self.hotkey_badge.setState(STATE_BOUND)

    def set_hotkey_state(self, state: HotkeyState | None) -> None:
        """把热键注册结果映射成徽章状态（冲突红框+橙字，失败橙字）。"""
        if state is None:
            self.hotkey_badge.setState(STATE_BOUND if self._hotkey else STATE_UNSET)
            self.hotkey_badge.setMessage("")
            return

        message = state.message or ""
        if state.status in ("duplicate", "occupied"):
            self.hotkey_badge.setState(STATE_CONFLICT)
        elif state.status in ("error", "invalid"):
            self.hotkey_badge.setState(STATE_FAILED)
        elif state.status == "ok" and state.backend == "hook":
            self.hotkey_badge.setState(STATE_BOUND)
            message = message or "已通过键盘钩子接管（组合可能被其他程序占用）"
        else:
            self.hotkey_badge.setState(STATE_BOUND if self._hotkey else STATE_UNSET)
        self.hotkey_badge.setMessage(message)

    def set_playing(self, playing: bool) -> None:
        if playing == self._playing:
            return
        self._playing = bool(playing)
        self.setProperty("playing", "true" if self._playing else "false")
        _repolish(self)
        self.preview_button.setIconName("square" if self._playing else "play")
        self.preview_button.setIconColor(
            theme.color("success") if self._playing else None
        )

    def is_playing(self) -> bool:
        return self._playing

    def filter_text(self) -> str:
        return self._name.lower()

    # ------------------------------------------------------------------ 行内改名
    def _begin_rename(self, event: QMouseEvent | None = None) -> None:
        """进入行内改名（双击名称、点铅笔按钮都走这里）。"""
        self.name_label.setVisible(False)
        self.name_edit.setVisible(True)
        self.name_edit.setText(self._name_full)
        self.name_edit.setFocus(Qt.FocusReason.MouseFocusReason
                                if event is not None else Qt.FocusReason.ShortcutFocusReason)
        self.name_edit.selectAll()
        if event is not None:
            event.accept()

    def _commit_rename(self) -> None:
        if not self.name_edit.isVisible():
            return
        text = self.name_edit.text().strip()
        self.name_edit.setVisible(False)
        self.name_label.setVisible(True)
        if not text or text == self._name_full:
            self._apply_name_text()
            return
        self._name = text
        self._name_full = text
        self._apply_name_text()
        self.name_label.setToolTip(text)
        self.rename_committed.emit(self.clip_id, text)

    # ------------------------------------------------------------------ 事件
    def _on_volume_changed(self, value: int) -> None:
        self.volume_label.setText(f"{value}%")
        self.volume_changed.emit(self.clip_id, value / 100.0)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.setProperty("hovered", "true")
        _repolish(self)
        self.volume_slider.setDimmed(False)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.setProperty("hovered", "false")
        _repolish(self)
        self.volume_slider.setDimmed(True)
        super().leaveEvent(event)

    def _base_background(self) -> QColor:
        """行的不透明底色，与 QSS 的 `[hovered]` / `[playing]` 规则保持同一来源。

        必须**每次整块填满**：行是 QWidget，若背景透明，重建列表时上一帧的
        文字/色点像素会留在视口里透出来，表现为"重影，鼠标一悬停就正常"。
        颜色按 QSS 用的状态属性取（不要只看 Python 的 _hover，避免两条路径不一致）。
        """
        if (str(self.property("playing")) == "true"
                or str(self.property("hovered")) == "true"):
            return QColor(theme.color("hover"))
        return QColor(theme.color("canvas"))

    def paintEvent(self, event) -> None:  # noqa: N802
        # 1) 先用不透明底色铺满（不依赖 QSS 是否已 polish，避免首帧残影）
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._base_background())
        painter.end()
        # 2) 让 QSS 背景/子控件正常绘制
        super().paintEvent(event)
        # 3) 播放中的左侧强调竖条
        if not self._playing:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.color("accent")))
        painter.drawRoundedRect(QRectF(0, 10, 3, self.height() - 20), 1.5, 1.5)


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class SoundboardPanel(QWidget):
    """左栏容器。"""

    import_requested = Signal()
    refresh_requested = Signal()
    files_dropped = Signal(list)
    preview_requested = Signal(str)
    hotkey_bind_requested = Signal(str)
    hotkey_changed = Signal(str, str)
    rename_committed = Signal(str, str)
    delete_requested = Signal(str)
    volume_changed = Signal(str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SoundboardPanel")
        self.setAcceptDrops(True)
        self._rows: dict[str, SoundRow] = {}
        self._clips: list[Clip] = []
        self._filter = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 工具栏 44px ----
        toolbar = QWidget(self)
        toolbar.setObjectName("Toolbar")
        toolbar.setFixedHeight(theme.SIZE["toolbar"])
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(theme.SPACE["lg"], 0, theme.SPACE["lg"], 0)
        bar.setSpacing(theme.SPACE["sm"])

        self.title_label = QLabel("音效", toolbar)
        self.title_label.setObjectName("PanelTitle")
        bar.addWidget(self.title_label)

        self.count_badge = QLabel("0", toolbar)
        self.count_badge.setObjectName("CountBadge")
        self.count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.count_badge)
        bar.addStretch(1)

        self.search_box = self._build_search(toolbar)
        self.search_box.setVisible(False)
        bar.addWidget(self.search_box)

        self.import_button = QPushButton("导入", toolbar)
        self.import_button.setObjectName("PrimaryButton")
        self.import_button.setFixedHeight(28)
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.setIcon(icons.icon("plus", 14, "#FFFFFF"))
        self.import_button.setToolTip("导入音频文件（也可直接拖进来）")
        self.import_button.clicked.connect(self.import_requested.emit)
        bar.addWidget(self.import_button)

        self.refresh_button = IconButton("refresh-cw", 16, 28, "ghost", "刷新目录", toolbar)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        bar.addWidget(self.refresh_button)
        outer.addWidget(toolbar)

        # ---- 列表区 ----
        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("ClipList")
        self.list_widget.setFrameShape(QListWidget.Shape.NoFrame)
        self.list_widget.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setSpacing(4)
        # 行高固定 56：告诉视图可以按统一尺寸算布局，减少几何计算误差
        self.list_widget.setUniformItemSizes(True)
        # 视口自己也要不透明地铺底，避免"透明行 + 透明视口"叠加出残影
        self.list_widget.viewport().setAutoFillBackground(True)
        palette = self.list_widget.viewport().palette()
        palette.setColor(self.list_widget.viewport().backgroundRole(),
                         QColor(theme.color("canvas")))
        self.list_widget.viewport().setPalette(palette)
        self.list_widget.setStyleSheet(
            f"QListWidget#ClipList {{ padding: {theme.SPACE['md']}px {theme.SPACE['lg']}px; }}"
            f"QListWidget#ClipList::item {{ height: {theme.SIZE['row']}px; }}"
        )
        outer.addWidget(self.list_widget, 1)

        self.empty_state = EmptyState(
            "audio-lines", "还没有音效", "拖入文件或点击导入", "导入文件", self
        )
        self.empty_state.action_requested.connect(self.import_requested.emit)
        outer.addWidget(self.empty_state, 1)

        self.drop_overlay = DropOverlay(parent=self)
        self.drop_overlay.hide()

    # ------------------------------------------------------------------ 工具栏
    def _build_search(self, parent: QWidget):
        from .widgets import SearchBox

        box = SearchBox(parent=parent)
        box.text_changed.connect(self._on_search)
        return box

    def _on_search(self, text: str) -> None:
        self._filter = text.lower()
        self._apply_filter()

    # ------------------------------------------------------------------ 列表
    #: 行 widget 的生命周期**只由这里管**：无论是整表重建、局部插入还是删除，
    #: 都必须走 _teardown_item()，不能只调 takeItem()/clear() 了事。
    #: （踩过的坑：takeItem 不会删 widget，残留的 widget 会继续按旧几何绘制，
    #:   行上移后就跟新行叠在一起，表现为"两个名字重影"。）
    def _force_list_layout(self) -> None:
        """显式让视图重排 item widget。

        为什么必须手动来：item widget 的几何由 QListWidget 内部在
        ``doItemsLayout()`` 时设置。窗口在 show() 之前就构建行、或真机有 DPI
        缩放时，这次重排可能不发生 —— 行 widget 会停在默认几何（约 100×30、
        全叠在左上角），表现就是"多行文字重影、色点和名字挤在一起"。
        离屏（offscreen）环境恰好会重排，所以这个坑只在真机复现。
        """
        view = self.list_widget
        for name in ("doItemsLayout", "scheduleDelayedItemsLayout"):
            method = getattr(view, name, None)
            if method is None:
                continue
            try:
                method()
            except (TypeError, RuntimeError):
                continue
        view.viewport().update()

    def audit_rows(self) -> list[tuple[int, tuple, tuple]]:
        """自检：返回"位置与 item 矩形不一致"的行（空 = 正常），供启动日志用。"""
        view = self.list_widget
        mismatched: list[tuple[int, tuple, tuple]] = []
        for index in range(view.count()):
            item = view.item(index)
            widget = view.itemWidget(item)
            if widget is None:
                continue
            visual = view.visualItemRect(item)
            if widget.pos() != visual.topLeft() or widget.size().height() < 40:
                mismatched.append((index, widget.pos().toTuple(), visual.topLeft().toTuple()))
        return mismatched

    def _teardown_item(self, index: int) -> None:
        item = self.list_widget.item(index)
        if item is None:
            return
        row = self.list_widget.itemWidget(item)
        if row is None:
            return
        # 记下它占过的区域：拆掉之后要**精确失效**这块像素，否则旧文字会留在视口里
        rect = row.geometry()
        self._stop_row_animation(row)
        row.setGraphicsEffect(None)
        row.hide()
        self.list_widget.removeItemWidget(item)
        row.setParent(None)
        row.deleteLater()
        if rect.isValid() and not rect.isEmpty():
            self.list_widget.viewport().update(rect)

    def _stop_row_animation(self, row: QWidget) -> None:
        for name in ("_enter_fade", "_enter_rise"):
            animation = getattr(row, name, None)
            if animation is not None:
                try:
                    animation.stop()
                except RuntimeError:
                    pass
                setattr(row, name, None)

    def _clear_rows(self) -> None:
        for index in range(self.list_widget.count() - 1, -1, -1):
            self._teardown_item(index)
        self.list_widget.clear()
        self._rows.clear()
        # 整块视口失效一次：清空后不留任何上一帧像素
        self.list_widget.viewport().update()

    def set_clips(self, clips: list[Clip]) -> None:
        """整表重建：销毁旧行 → 按数据重新生成（唯一允许的刷新方式）。"""
        self._clips = list(clips)
        try:
            self._clear_rows()
            for clip in self._clips:
                self._append_row(clip)
        finally:
            self._update_counts()
            self._force_list_layout()
        self._apply_filter()

    def _append_row(self, clip: Clip) -> None:
        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(QSize(0, theme.SIZE["row"]))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setText(clip.name)          # 供无障碍/查找使用（渲染由行 widget 负责）
        item.setData(Qt.ItemDataRole.UserRole, clip.id)
        row = SoundRow(clip, self.list_widget)   # 每行都是独立实例
        row.preview_toggled.connect(self.preview_requested)
        row.hotkey_bind_requested.connect(self.hotkey_bind_requested)
        row.hotkey_changed.connect(self.hotkey_changed)
        row.rename_committed.connect(self.rename_committed)
        row.delete_requested.connect(self.delete_requested)
        row.volume_changed.connect(self.volume_changed)
        self.list_widget.setItemWidget(item, row)
        self._rows[clip.id] = row

    def insert_clip(self, clip: Clip) -> None:
        """追加一条（淡入 + 内部上移 4px）。

        注意：**绝不动 row.pos()** —— 行几何归 QListWidget 的视图管，
        给 pos 做动画会把动画的结束值写成真实位置（历史 bug：所有行叠在 (0,0)）。
        入场位移改成动行内布局的上边距。
        """
        existing = self._item_index_of(clip.id)
        if existing >= 0:                 # 幂等：同一条重复插不进来
            self._teardown_item(existing)
            self.list_widget.takeItem(existing)
        if not any(c.id == clip.id for c in self._clips):
            self._clips.append(clip)
        self._append_row(clip)
        self._apply_filter()
        self._update_counts()

        row = self._rows.get(clip.id)
        if row is None:
            return
        layout = row.layout()
        if layout is not None:
            left, _t, right, _b = layout.getContentsMargins()
            rise = QVariantAnimation(row)
            rise.setDuration(theme.ANIM["rowIn"])
            rise.setStartValue(float(theme.SPACE["xs"]))
            rise.setEndValue(0.0)
            rise.setEasingCurve(QEasingCurve.Type.OutCubic)
            rise.valueChanged.connect(
                lambda value, l=layout, a=left, r=right: l.setContentsMargins(
                    a, int(value), r, 0
                )
            )
            rise.start()
            row._enter_rise = rise  # type: ignore[attr-defined]

        effect = QGraphicsOpacityEffect(row)
        effect.setOpacity(0.0)
        row.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", row)
        fade.setDuration(theme.ANIM["rowIn"])
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(lambda r=row: self._finish_enter(r))
        fade.start()
        row._enter_fade = fade  # type: ignore[attr-defined]

    def _finish_enter(self, row: QWidget) -> None:
        """入场动画结束：把图形效果摘掉，避免缓存 pixmap 造成陈旧像素。"""
        try:
            row.setGraphicsEffect(None)
        except RuntimeError:
            return
        layout = row.layout()
        if layout is not None:
            left, _t, right, _b = layout.getContentsMargins()
            layout.setContentsMargins(left, 0, right, 0)
        row._enter_fade = None  # type: ignore[attr-defined]

    def _item_index_of(self, clip_id: str) -> int:
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item is None:
                continue
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == clip_id:
                return index
            widget = self.list_widget.itemWidget(item)
            if widget is not None and getattr(widget, "clip_id", "") == clip_id:
                return index
        return -1

    def update_row(self, clip: Clip) -> None:
        row = self._rows.get(clip.id)
        if row is not None:
            row.update_clip(clip)

    def remove_row(self, clip_id: str) -> None:
        index = self._item_index_of(clip_id)
        if index >= 0:
            self._teardown_item(index)
            self.list_widget.takeItem(index)
        self._rows.pop(clip_id, None)
        self._clips = [c for c in self._clips if c.id != clip_id]
        self._update_counts()
        self._force_list_layout()

    def row(self, clip_id: str) -> SoundRow | None:
        return self._rows.get(clip_id)

    def count(self) -> int:
        return len(self._rows)

    def visible_count(self) -> int:
        """可见行数：按 item 的 rowHidden 统计（不能看 row.isHidden()，那不会收掉占位）。"""
        return sum(
            1
            for index in range(self.list_widget.count())
            if not self.list_widget.isRowHidden(index)
        )

    # ------------------------------------------------------------------ 状态同步
    def set_playing(self, playing_ids: set[str]) -> None:
        for clip_id, row in self._rows.items():
            row.set_playing(clip_id in playing_ids)

    def set_hotkey_states(self, states: dict) -> None:
        for clip_id, row in self._rows.items():
            row.set_hotkey_state(states.get(clip_id))

    def set_busy(self, busy: bool) -> None:
        """导入/刷新时的行内 loading。"""
        self.import_button.setEnabled(not busy)
        self.refresh_button.setLoading(busy)

    # ------------------------------------------------------------------ 过滤/计数
    def _apply_filter(self) -> None:
        """搜索过滤：用 setRowHidden（行会真的收起来），不要 row.setVisible。"""
        if not self._filter:
            for index in range(self.list_widget.count()):
                if self.list_widget.isRowHidden(index):
                    self.list_widget.setRowHidden(index, False)
        else:
            for index in range(self.list_widget.count()):
                item = self.list_widget.item(index)
                row = self.list_widget.itemWidget(item)
                text = row.filter_text() if row is not None else item.text().lower()
                self.list_widget.setRowHidden(index, self._filter not in text)
        self._force_list_layout()
        self._update_counts()

    def _update_counts(self) -> None:
        total = len(self._clips)
        self.count_badge.setText(str(total))
        has_clips = total > 0
        self.list_widget.setVisible(has_clips)
        self.empty_state.setVisible(not has_clips)
        self.search_box.setVisible(total > SEARCH_THRESHOLD)
        if total <= SEARCH_THRESHOLD and self.search_box.text():
            self.search_box.clear()

        if has_clips and self.visible_count() == 0 and self._filter:
            self.empty_state.setTexts("没有匹配的音效", f"试试别的关键词：{self._filter}")
            self.empty_state.setVisible(True)
            self.list_widget.setVisible(False)
        elif has_clips:
            self.empty_state.setTexts("还没有音效", "拖入文件或点击导入")
            self.empty_state.setVisible(False)
            self.list_widget.setVisible(True)

    # ------------------------------------------------------------------ 拖拽
    def _accept_urls(self, mime) -> bool:  # noqa: ANN001
        from core.constants import SUPPORTED_EXTS

        if not mime.hasUrls():
            return False
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir() or path.suffix.lower() in SUPPORTED_EXTS:
                return True
        return False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._accept_urls(event.mimeData()):
            self.drop_overlay.setGeometry(self.list_area_rect().toRect())
            self.drop_overlay.show()
            self.drop_overlay.raise_()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.drop_overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.drop_overlay.hide()
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()

    def list_area_rect(self) -> QRectF:  # noqa: ANN201
        widget = self.list_widget if self.list_widget.isVisible() else self.empty_state
        top = widget.mapTo(self, QPoint(0, 0))
        return QRectF(
            top.x() + theme.SPACE["sm"],
            top.y(),
            widget.width() - theme.SPACE["sm"] * 2,
            widget.height(),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.drop_overlay.isVisible():
            self.drop_overlay.setGeometry(self.list_area_rect().toRect())
        # 尺寸/DPI 变化后 item widget 的行高与位置也要重排
        self._force_list_layout()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 关键：行可能在窗口 show() 之前就建好了，显示后再强制排一次
        self._force_list_layout()
