"""「压缩成包」对话框：勾选若干音效 → 选择输出 zip。

包内结构（由 ``ClipLibrary.export_archive`` 生成）::

    manifest.json          # 名称/音量/快捷键/模式，导入时还原
    audio/rec_xxx.wav
    audio/rec_xxx.json     # 同名 sidecar
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme


class ArchiveDialog(QDialog):
    """选择要打包的音效。"""

    def __init__(self, clips: list, parent: QWidget | None = None,
                 default_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("压缩音效")
        self.setModal(True)
        self.setMinimumSize(520, 460)
        self._clips = list(clips)
        self._default_path = default_path or (Path.home() / "soundboard-pack.zip")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE["lg"], theme.SPACE["lg"],
                                  theme.SPACE["lg"], theme.SPACE["lg"])
        layout.setSpacing(theme.SPACE["md"])

        title = QLabel("选择要压缩的音效", self)
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        hint = QLabel(
            "压缩包包含音频、同名 .json（名称/音量）和 manifest.json；"
            "以后可直接把它拖回本程序导入。",
            self,
        )
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for clip in self._clips:
            item = QListWidgetItem(f"{clip.name}    ({clip.path().name})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, clip.id)
            self.list_widget.addItem(item)
        if self._clips:
            self.list_widget.setCurrentRow(0)
        layout.addWidget(self.list_widget, 1)

        tools = QHBoxLayout()
        all_btn = QPushButton("全选", self)
        all_btn.setObjectName("SecondaryButton")
        all_btn.clicked.connect(lambda: self._set_all(True))
        tools.addWidget(all_btn)
        none_btn = QPushButton("全不选", self)
        none_btn.setObjectName("SecondaryButton")
        none_btn.clicked.connect(lambda: self._set_all(False))
        tools.addWidget(none_btn)
        tools.addStretch(1)
        layout.addLayout(tools)

        row = QHBoxLayout()
        self.path_label = QLabel(str(self._default_path), self)
        self.path_label.setObjectName("Caption")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.path_label, 1)
        browse = QPushButton("另存为…", self)
        browse.setObjectName("SecondaryButton")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消", self)
        cancel.setObjectName("SecondaryButton")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.ok_button = QPushButton("压缩", self)
        self.ok_button.setObjectName("PrimaryButton")
        self.ok_button.clicked.connect(self._accept)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ 交互
    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.list_widget.count()):
            self.list_widget.item(index).setCheckState(state)

    def _browse(self) -> None:
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "保存压缩包", str(self._default_path), "音效压缩包 (*.zip)"
        )
        if chosen:
            if not chosen.lower().endswith(".zip"):
                chosen += ".zip"
            self._default_path = Path(chosen)
            self.path_label.setText(chosen)

    def _accept(self) -> None:
        if not self.selected_clips():
            return
        self.accept()

    # ------------------------------------------------------------------ 结果
    def selected_ids(self) -> list[str]:
        result: list[str] = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return result

    def selected_clips(self) -> list:
        wanted = set(self.selected_ids())
        return [clip for clip in self._clips if clip.id in wanted]

    def output_path(self) -> Path:
        return self._default_path
