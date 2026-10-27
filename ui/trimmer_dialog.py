"""录音裁剪窗口：波形可视化 + 拖拽选区（纯 QPainter，无第三方依赖）。

结构：
    PeakWorker        —— 后台把音频降采样成 min/max 峰值数组（长音频不卡 UI）
    WaveformCanvas    —— 只画波形 + 选区 + 手柄，处理鼠标拖拽
    TrimmerDialog     —— 800×400 模态窗口：试听 / 重置 / 确认 / 取消

交互约定（参考 Audacity / 网易云）：
* 左/右手柄拖拽改起点/终点；选区内部拖拽整体平移；
* 悬停手柄光标变 SizeHorCursor，悬停选区变 SizeAllCursor；
* 键盘 ←/→ 微调端点 10ms，Shift+←/→ 100ms，Home/End 拉到两端。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

from PySide6.QtCore import QLineF, QPoint, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

MIN_SELECTION = 0.05      # 秒
HANDLE_WIDTH = 10
HANDLE_HIT = 8
PEAK_BUCKETS = 1600


# ---------------------------------------------------------------- 峰值
def compute_peaks(samples: np.ndarray, buckets: int = PEAK_BUCKETS) -> tuple[np.ndarray, np.ndarray]:
    """把单声道音频压成 buckets 个 (min, max)。O(n)，无 Python 循环。"""
    mono = samples if samples.ndim == 1 else samples.mean(axis=1)
    count = int(buckets)
    total = mono.size
    if total == 0 or count <= 0:
        return np.zeros(count, np.float32), np.zeros(count, np.float32)
    usable = (total // count) * count
    if usable == 0:
        low = np.full(count, float(mono.min()), np.float32)
        high = np.full(count, float(mono.max()), np.float32)
        return low, high
    block = mono[:usable].reshape(count, -1)
    return block.min(axis=1).astype(np.float32), block.max(axis=1).astype(np.float32)


class PeakWorker(QThread):
    """后台算峰值（10 分钟音频也不阻塞 UI）。"""

    ready = Signal(object, object)
    failed = Signal(str)

    def __init__(self, samples: np.ndarray, buckets: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples = samples
        self._buckets = buckets

    def run(self) -> None:  # noqa: D102
        try:
            low, high = compute_peaks(self._samples, self._buckets)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.ready.emit(low, high)


# ---------------------------------------------------------------- 画布
class WaveformCanvas(QWidget):
    """波形 + 选区。所有几何都在这里算，外部只关心秒数。"""

    selection_changed = Signal(float, float)

    def __init__(self, duration: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WaveformArea")
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._duration = max(0.01, float(duration))
        self._start = 0.0
        self._end = self._duration
        self._low = np.zeros(0, np.float32)
        self._high = np.zeros(0, np.float32)
        self._drag: str | None = None
        self._drag_anchor = 0.0
        self._drag_origin = (self._start, self._end)
        self._hover: str | None = None
        self._preview: float | None = None
        self._col_cache: tuple[int, np.ndarray, np.ndarray] | None = None

    # ------------------------------------------------------------------ 数据
    def set_peaks(self, low: np.ndarray, high: np.ndarray) -> None:
        self._low = np.asarray(low, np.float32)
        self._high = np.asarray(high, np.float32)
        self._col_cache = None
        self.update()

    def has_peaks(self) -> bool:
        return self._low.size > 0

    def duration(self) -> float:
        return self._duration

    def selection(self) -> tuple[float, float]:
        return self._start, self._end

    def set_selection(self, start: float, end: float, notify: bool = False) -> None:
        start = max(0.0, min(float(start), self._duration - MIN_SELECTION))
        end = max(start + MIN_SELECTION, min(float(end), self._duration))
        self._start, self._end = start, end
        self.update()
        if notify:
            self.selection_changed.emit(start, end)

    def reset(self) -> None:
        self.set_selection(0.0, self._duration, notify=True)

    def set_preview(self, position: float | None) -> None:
        self._preview = position
        self.update()

    # ------------------------------------------------------------------ 几何
    def _area(self) -> QRectF:
        return QRectF(self.rect()).adjusted(HANDLE_WIDTH, 10, -HANDLE_WIDTH, -10)

    def _x_of(self, seconds: float) -> float:
        area = self._area()
        return area.left() + area.width() * (seconds / self._duration)

    def _seconds_of(self, x: float) -> float:
        area = self._area()
        if area.width() <= 0:
            return 0.0
        ratio = (x - area.left()) / area.width()
        return max(0.0, min(1.0, ratio)) * self._duration

    def _columns(self) -> tuple[np.ndarray, np.ndarray]:
        """按当前像素宽度聚合峰值（缓存，宽度不变不重算）。"""
        width = max(1, int(self._area().width()))
        if self._col_cache is not None and self._col_cache[0] == width:
            return self._col_cache[1], self._col_cache[2]
        if self._low.size == 0:
            zeros = np.zeros(width, np.float32)
            self._col_cache = (width, zeros, zeros)
            return zeros, zeros
        buckets = self._low.size
        index = (np.arange(width) * buckets // width).astype(np.int32)
        index = np.clip(index, 0, buckets - 1)
        low = self._low[index]
        high = self._high[index]
        self._col_cache = (width, low, high)
        return low, high

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(theme.color("card")))

        area = self._area()
        painter.fillRect(area, QColor(theme.color("canvas")))

        # 中线
        mid = area.center().y()
        painter.setPen(QPen(QColor(theme.color("border")), 1))
        painter.drawLine(QPointF(area.left(), mid), QPointF(area.right(), mid))

        if not self.has_peaks():
            painter.setPen(QPen(QColor(theme.color("textFaint"))))
            font = QFont()
            font.setPixelSize(12)
            painter.setFont(font)
            painter.drawText(area, int(Qt.AlignmentFlag.AlignCenter), "正在分析波形…")
            painter.end()
            return

        low, high = self._columns()
        width = low.size
        half = area.height() / 2.0
        wave_color = QColor(theme.color("accent"))
        wave_color.setAlphaF(0.55)
        pen = QPen(wave_color, 1.0)
        painter.setPen(pen)
        lines = []
        left = area.left()
        for index in range(width):
            x = left + index + 0.5
            top = mid - max(-1.0, min(1.0, float(high[index]))) * half
            bottom = mid - max(-1.0, min(1.0, float(low[index]))) * half
            if abs(bottom - top) < 1.0:
                top, bottom = mid - 0.5, mid + 0.5
            lines.append(QLineF(x, top, x, bottom))
        painter.drawLines(lines)

        # 选区外遮罩
        start_x = self._x_of(self._start)
        end_x = self._x_of(self._end)
        mask = QColor(0, 0, 0, 72)
        painter.fillRect(QRectF(area.left(), area.top(), max(0.0, start_x - area.left()),
                                area.height()), mask)
        painter.fillRect(QRectF(end_x, area.top(), max(0.0, area.right() - end_x),
                                area.height()), mask)

        # 选区边框
        accent = QColor(theme.color("accent"))
        painter.setPen(QPen(accent, 2))
        painter.drawLine(QPointF(start_x, area.top()), QPointF(start_x, area.bottom()))
        painter.drawLine(QPointF(end_x, area.top()), QPointF(end_x, area.bottom()))
        painter.setPen(QPen(accent, 1))
        painter.drawLine(QPointF(start_x, area.top()), QPointF(end_x, area.top()))
        painter.drawLine(QPointF(start_x, area.bottom()), QPointF(end_x, area.bottom()))

        # 手柄（两端圆角竖条）
        for x in (start_x, end_x):
            handle = QRectF(x - HANDLE_WIDTH / 2, area.top() + 2,
                            HANDLE_WIDTH, area.height() - 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(handle, 3, 3)
            painter.setBrush(QColor("#FFFFFF"))
            for offset in (-2, 2):
                painter.drawRect(QRectF(x + offset - 0.5, area.center().y() - 6, 1, 12))

        # 试听进度线
        if self._preview is not None:
            px = self._x_of(self._preview)
            painter.setPen(QPen(QColor(theme.color("success")), 1.5))
            painter.drawLine(QPointF(px, area.top()), QPointF(px, area.bottom()))
        painter.end()

    # ------------------------------------------------------------------ 命中
    def _hit(self, x: float) -> str | None:
        start_x = self._x_of(self._start)
        end_x = self._x_of(self._end)
        if abs(x - start_x) <= HANDLE_HIT:
            return "left"
        if abs(x - end_x) <= HANDLE_HIT:
            return "right"
        if start_x < x < end_x:
            return "move"
        return None

    # ------------------------------------------------------------------ 鼠标
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        hit = self._hit(x)
        self._drag = hit or "left"
        if hit == "move":
            self._drag_anchor = self._seconds_of(x)
            self._drag_origin = (self._start, self._end)
        else:
            # 点在遮罩/空白处：把最近的手柄挪过来
            seconds = self._seconds_of(x)
            if hit is None:
                if abs(x - self._x_of(self._start)) <= abs(x - self._x_of(self._end)):
                    self._drag = "left"
                    self.set_selection(min(seconds, self._end - MIN_SELECTION), self._end, notify=True)
                else:
                    self._drag = "right"
                    self.set_selection(self._start, max(seconds, self._start + MIN_SELECTION), notify=True)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        x = event.position().x()
        if self._drag is None:
            hit = self._hit(x)
            self._hover = hit
            if hit in ("left", "right"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        seconds = self._seconds_of(x)
        if self._drag == "left":
            self.set_selection(min(seconds, self._end - MIN_SELECTION), self._end, notify=True)
        elif self._drag == "right":
            self.set_selection(self._start, max(seconds, self._start + MIN_SELECTION), notify=True)
        else:
            span = self._drag_origin[1] - self._drag_origin[0]
            delta = seconds - self._drag_anchor
            start = max(0.0, min(self._drag_origin[0] + delta, self._duration - span))
            self.set_selection(start, start + span, notify=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag = None
        self.mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        step = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.01
        start, end = self._start, self._end
        key = event.key()
        if key == Qt.Key.Key_Left:
            self.set_selection(start - step, end, notify=True)
        elif key == Qt.Key.Key_Right:
            self.set_selection(start + step, end, notify=True)
        elif key == Qt.Key.Key_Home:
            self.set_selection(0.0, end, notify=True)
        elif key == Qt.Key.Key_End:
            self.set_selection(start, self._duration, notify=True)
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------- 时间格式
def format_clock(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds % 60:05.2f}"


class TrimTimeBar(QWidget):
    """底部三个时间读数（等宽数字，不跳动）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE["xl"])
        self.start_label = self._make(layout, "开始", "00:00.00")
        self.end_label = self._make(layout, "结束", "00:00.00")
        self.span_label = self._make(layout, "保留", "00:00.00")

    def _make(self, layout: QHBoxLayout, caption: str, value: str) -> QLabel:
        box = QWidget(self)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE["xs"])
        label = QLabel(caption, box)
        label.setObjectName("FieldLabel")
        row.addWidget(label)
        number = QLabel(value, box)
        number.setObjectName("TimerLabel")
        font = QFont()
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Medium)
        number.setFont(font)
        row.addWidget(number)
        layout.addWidget(box)
        return number

    def set_values(self, start: float, end: float) -> None:
        self.start_label.setText(format_clock(start))
        self.end_label.setText(format_clock(end))
        self.span_label.setText(format_clock(max(0.0, end - start)))


# ---------------------------------------------------------------- 对话框
@dataclass
class TrimResult:
    start: float
    end: float
    samples: np.ndarray


class TrimmerDialog(QDialog):
    """波形裁剪窗口（800×400，模态）。"""

    def __init__(self, audio: np.ndarray, samplerate: int,
                 title: str = "裁剪录音", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle(title)

        self._audio = np.asarray(audio, np.float32)
        if self._audio.ndim > 1:
            self._audio = self._audio.mean(axis=1)
        self._rate = int(samplerate)
        self._duration = max(0.01, self._audio.size / self._rate)
        self._play_stream = None
        self._worker: PeakWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        self.sheet = QFrame(self)
        self.sheet.setObjectName("SheetDialog")
        outer.addWidget(self.sheet)

        layout = QVBoxLayout(self.sheet)
        layout.setContentsMargins(theme.SPACE["xl"], theme.SPACE["lg"],
                                  theme.SPACE["xl"], theme.SPACE["lg"])
        layout.setSpacing(theme.SPACE["md"])

        header = QHBoxLayout()
        title_label = QLabel(title, self.sheet)
        title_label.setObjectName("DialogTitle")
        header.addWidget(title_label)
        header.addStretch(1)
        self.hint = QLabel("拖动两端手柄选择保留区间；拖中间可整体平移", self.sheet)
        self.hint.setObjectName("HintLabel")
        header.addWidget(self.hint)
        layout.addLayout(header)

        self.canvas = WaveformCanvas(self._duration, self.sheet)
        self.canvas.selection_changed.connect(self._on_selection)
        layout.addWidget(self.canvas, 1)

        self.time_bar = TrimTimeBar(self.sheet)
        layout.addWidget(self.time_bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE["sm"])
        self.preview_button = QPushButton("试听当前选区", self.sheet)
        self.preview_button.setObjectName("SecondaryButton")
        self.preview_button.setFixedHeight(32)
        self.preview_button.clicked.connect(self.toggle_preview)
        buttons.addWidget(self.preview_button)

        self.reset_button = QPushButton("重置", self.sheet)
        self.reset_button.setObjectName("SecondaryButton")
        self.reset_button.setFixedHeight(32)
        self.reset_button.clicked.connect(self._reset)
        buttons.addWidget(self.reset_button)

        buttons.addStretch(1)

        self.cancel_button = QPushButton("取消", self.sheet)
        self.cancel_button.setObjectName("SecondaryButton")
        self.cancel_button.setFixedHeight(32)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)

        self.confirm_button = QPushButton("确认裁剪", self.sheet)
        self.confirm_button.setObjectName("PrimaryButton")
        self.confirm_button.setFixedHeight(32)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

        self.setFixedSize(800, 400)
        self._on_selection(0.0, self._duration)
        self._start_analysis()

    # ------------------------------------------------------------------ 分析
    def _start_analysis(self) -> None:
        self._worker = PeakWorker(self._audio, PEAK_BUCKETS, self)
        self._worker.ready.connect(self.canvas.set_peaks)
        self._worker.failed.connect(self._on_analysis_failed)
        self._worker.start()

    def _on_analysis_failed(self, message: str) -> None:
        self.hint.setText(f"波形分析失败：{message}")

    # ------------------------------------------------------------------ 选区
    def _on_selection(self, start: float, end: float) -> None:
        self.time_bar.set_values(start, end)

    def _reset(self) -> None:
        self.canvas.reset()

    # ------------------------------------------------------------------ 试听
    def toggle_preview(self) -> None:
        if self._play_stream is not None:
            self._stop_preview()
            return
        if sd is None:
            self.hint.setText("未安装 sounddevice，无法试听")
            return
        start, end = self.canvas.selection()
        begin = int(start * self._rate)
        finish = min(self._audio.size, int(end * self._rate))
        chunk = self._audio[begin:finish]
        if chunk.size == 0:
            return
        block = chunk.astype(np.float32)
        state = {"position": 0}

        def callback(outdata, frames, time_info, status):  # noqa: ANN001
            position = state["position"]
            take = min(frames, block.size - position)
            if take <= 0:
                outdata.fill(0.0)
                raise sd.CallbackStop
            outdata[:take, 0] = block[position:position + take]
            if outdata.shape[1] > 1:
                outdata[:take, 1] = block[position:position + take]
            if take < frames:
                outdata[take:].fill(0.0)
            state["position"] = position + take
            self.canvas.set_preview(start + state["position"] / self._rate)

        try:
            stream = sd.OutputStream(samplerate=self._rate, blocksize=512, channels=2,
                                     dtype="float32", callback=callback)
            stream.start()
        except Exception as exc:  # noqa: BLE001
            self.hint.setText(f"试听失败：{exc}")
            return
        self._play_stream = stream
        self.preview_button.setText("停止试听")

    def _stop_preview(self) -> None:
        stream, self._play_stream = self._play_stream, None
        self.preview_button.setText("试听当前选区")
        self.canvas.set_preview(None)
        if stream is not None:
            for action in (stream.stop, stream.close):
                try:
                    action()
                except Exception:
                    pass

    # ------------------------------------------------------------------ 结果
    def selection(self) -> tuple[float, float]:
        return self.canvas.selection()

    def trimmed(self) -> np.ndarray:
        start, end = self.canvas.selection()
        begin = max(0, int(round(start * self._rate)))
        finish = min(self._audio.size, int(round(end * self._rate)))
        if finish <= begin:
            return self._audio.copy()
        return self._audio[begin:finish].astype(np.float32)

    def accept(self) -> None:  # noqa: D102
        self._stop_preview()
        super().accept()

    def reject(self) -> None:  # noqa: D102
        self._stop_preview()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_preview()
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(500)
        super().closeEvent(event)
