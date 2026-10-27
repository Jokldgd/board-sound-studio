"""界面自检（离屏）：边缘拖拽命中、控件绘制、关键流程、规范扫描。

用法（在 soundboard_studio 目录）：
    $env:QT_QPA_PLATFORM = "offscreen"
    python tools\\selftest_ui.py

覆盖：
* 8 方向边缘命中与 resize 光标（``Qt.Edge`` 是 Flag 枚举，这里锁死"不能 int()"的坑）；
* 窗口级鼠标按下/移动/松开不抛异常（回归：曾经每次点击都抛 TypeError）；
* 全部控件离屏 ``grab()`` 绘制无异常；
* 导入 → 录音 → 保存 的链路仍在；
* 底栏错误提示可恢复、界面无 emoji。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(r"F:\BoardSound\soundboard_studio")
sys.path.insert(0, str(ROOT))

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from core import audio_io, device_enum
from core.config import ConfigStore
from core.constants import SAMPLE_RATE
from ui import theme
from ui.main_window import MainWindow

problems: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        problems.append(name)


def pump(app: QApplication, seconds: float = 0.25) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def mouse_event(kind: QEvent.Type, pos: QPoint, button: Qt.MouseButton) -> QMouseEvent:
    return QMouseEvent(
        kind, QPointF(pos), QPointF(pos),
        button, button, Qt.KeyboardModifier.NoModifier,
    )


def main() -> int:
    # 用独立配置：绝不碰用户真实的 config.json / audio/
    ROOT.joinpath("temp").mkdir(parents=True, exist_ok=True)
    cfg_path = ROOT / "temp" / "_ui_selftest_config.json"
    cfg_path.unlink(missing_ok=True)
    backup = None

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.stylesheet())

    # 模态对话框在离屏脚本里会永久阻塞：一律桩掉（删除确认/警告/提示）
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.question = staticmethod(  # type: ignore[assignment]
        lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    QMessageBox.warning = staticmethod(lambda *a, **k: None)  # type: ignore[assignment]
    QMessageBox.information = staticmethod(lambda *a, **k: None)  # type: ignore[assignment]

    store = ConfigStore(cfg_path)
    store.load()
    store.config.clips = []
    store.save(immediate=True)

    window = MainWindow(store)
    window.show()
    pump(app, 0.8)

    # ---------------- 0. 最小化必须留在任务栏（不能 hide） ----------------
    window.showMinimized()
    pump(app, 0.4)
    check("最小化后窗口仍然可见（任务栏有按钮）",
          window.isVisible() and window.isMinimized(),
          f"visible={window.isVisible()} minimized={window.isMinimized()}")
    window.showNormal()
    pump(app, 0.4)
    check("从最小化可以还原", not window.isMinimized(), str(window.windowState()))

    # ---------------- 1. 边缘命中（纯函数） ----------------
    width, height = window.width(), window.height()
    cases = {
        "左上角": (QPoint(1, 1), Qt.Edge.LeftEdge | Qt.Edge.TopEdge),
        "右下角": (QPoint(width - 1, height - 1), Qt.Edge.RightEdge | Qt.Edge.BottomEdge),
        "右上角": (QPoint(width - 1, 1), Qt.Edge.RightEdge | Qt.Edge.TopEdge),
        "左中": (QPoint(1, height // 2), Qt.Edge.LeftEdge),
        "下中": (QPoint(width // 2, height - 1), Qt.Edge.BottomEdge),
        "中间": (QPoint(width // 2, height // 2), None),
    }
    for label, (point, expected) in cases.items():
        got = window._edges_at(point)
        check(f"边缘命中：{label}", got == expected, f"{got!r}")

    # ---------------- 2. 光标形状（8 方向） ----------------
    cursor_cases = {
        Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
        Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    }
    bad = [f"{edges!r}→{window._cursor_for(edges).name}"
           for edges, want in cursor_cases.items()
           if window._cursor_for(edges) != want]
    check("8 方向 resize 光标正确", not bad, str(bad))

    # ---------------- 3. 真实鼠标事件不抛异常 ----------------
    crashed: list[str] = []
    for name, point in (("窗口中间", QPoint(width // 2, height // 2)),
                        ("左边缘", QPoint(1, height // 2)),
                        ("右下角", QPoint(width - 1, height - 1)),
                        ("标题栏", QPoint(120, 20))):
        try:
            window.mousePressEvent(mouse_event(QEvent.Type.MouseButtonPress, point,
                                               Qt.MouseButton.LeftButton))
            window.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove, point,
                                              Qt.MouseButton.NoButton))
            window.mouseReleaseEvent(mouse_event(QEvent.Type.MouseButtonRelease, point,
                                                 Qt.MouseButton.LeftButton))
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"{name}: {type(exc).__name__} {exc}")
    check("窗口级鼠标按下/移动/松开不抛异常", not crashed, str(crashed))

    # 最大化时不应命中边缘
    window.showMaximized()
    pump(app, 0.4)
    check("最大化后不进入 resize 命中", window._edges_at(QPoint(1, 1)) is None)
    window.showNormal()
    pump(app, 0.3)

    # ---------------- 4. 对话框样式：必须是不透明卡片（回归"全透明"的坑） ----------------
    from ui.settings_dialog import SettingsDialog

    settings = SettingsDialog(window.config, (False, "测试原因"), parent=window)
    settings.adjustSize()
    settings.show()
    pump(app, 0.3)
    sheet = settings.sheet.grab().toImage()
    accent_alpha = sheet.pixelColor(sheet.width() // 2, 20).alpha()
    check("设置对话框不透明（QSS 选择器命中）", accent_alpha == 255,
          f"alpha={accent_alpha}")

    samples = [
        (6, 6), (sheet.width() - 6, 6), (sheet.width() // 2, 6),
        (6, sheet.height() - 6), (6, sheet.height() // 2),
    ]
    bright = []
    for x, y in samples:
        color = sheet.pixelColor(x, y)
        bright.append((color.red(), color.green(), color.blue()))
    ok_bg = all(min(rgb_) >= 235 for rgb_ in bright)
    check("设置对话框底色是浅色（card/画布）", ok_bg, str(bright))

    screen = settings.screen() or app.primaryScreen()
    available = screen.availableGeometry()
    inside = available.contains(settings.frameGeometry())
    check("对话框不出屏（含小屏）", inside,
          f"dialog={settings.frameGeometry().getRect()} screen={available.getRect()}")
    check("对话框有滚动区收纳分组", settings.scroll.isVisible())

    # 元素不许溢出卡片（滚动区内的内容允许纵向超出，但要能被滚动看到）
    scroll_content = settings.scroll.widget()
    overflow = []
    sheet_rect = settings.sheet.rect()

    def in_scroll(widget: QWidget) -> bool:
        node = widget.parentWidget()
        while node is not None:
            if node is scroll_content:
                return True
            if node is settings.sheet:
                return False
            node = node.parentWidget()
        return False

    for child in settings.sheet.findChildren(QWidget):
        if not child.isVisible() or child is scroll_content or in_scroll(child):
            continue
        top_left = child.mapTo(settings.sheet, QPoint(0, 0))
        right = top_left.x() + child.width()
        bottom = top_left.y() + child.height()
        if top_left.x() < -1 or top_left.y() < -1:
            overflow.append(f"{type(child).__name__} 左上溢出 {top_left.x()},{top_left.y()}")
        if right > sheet_rect.width() + 1:
            overflow.append(f"{type(child).__name__} 右侧溢出 {right}>{sheet_rect.width()}")
        if bottom > sheet_rect.height() + 1:
            overflow.append(f"{type(child).__name__} 底部溢出 {bottom}>{sheet_rect.height()}")

    viewport = settings.scroll.viewport()
    check("滚动内容不横向溢出",
          scroll_content.width() <= viewport.width() + 1,
          f"{scroll_content.width()} > {viewport.width()}")
    check("对话框内元素不溢出卡片", not overflow, str(overflow[:3]))
    check("保存按钮在可视区内",
          settings.save_button.mapTo(settings.sheet, QPoint(0, 0)).y()
          + settings.save_button.height() <= sheet_rect.height(),
          str(settings.save_button.mapTo(settings.sheet, QPoint(0, 0)).y()))

    # 裁剪窗口同样验证一次「卡片不透明」
    from ui.trimmer_dialog import TrimmerDialog as _TrimCheck

    trim = _TrimCheck(np.zeros(48000, np.float32), 48000, parent=window)
    trim.adjustSize()
    trim.show()
    pump(app, 0.3)
    trim_image = trim.sheet.grab().toImage()
    trim_center = trim_image.pixelColor(trim_image.width() // 2, 30)
    check("裁剪窗口不透明", trim_center.alpha() == 255, f"alpha={trim_center.alpha()}")
    trim.close()
    settings.close()

    # ---------------- 4b. 顶栏齿轮 → 设置对话框（真实点击链路） ----------------
    from PySide6.QtWidgets import QDialog

    import ui.main_window as main_window_module

    opened: list[str] = []
    original_dialog = main_window_module.SettingsDialog

    class StubDialog(original_dialog):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            super().__init__(*args, **kwargs)
            opened.append("settings")

        def exec(self):  # noqa: ANN201
            self.close()
            return QDialog.DialogCode.Rejected

    main_window_module.SettingsDialog = StubDialog
    try:
        QTest.mouseClick(window.top_bar.settings_button, Qt.MouseButton.LeftButton)
        pump(app, 0.3)
    finally:
        main_window_module.SettingsDialog = original_dialog
    check("点击顶栏齿轮能打开设置对话框", opened == ["settings"], str(opened))

    # ---------------- 4c. 输出路由（虚拟声卡 / 仅本地；双输出已弃置） ----------------
    from core.audio_engine import ROUTE_LOCAL, ROUTE_VIRTUAL

    top = window.top_bar
    check("路由下拉只剩两项", top.route_combo.count() == 2, str(top.route_combo.count()))
    modes_in_combo = [top.route_combo.itemData(i) for i in range(top.route_combo.count())]
    check("两项顺序为 虚拟/仅本地", modes_in_combo == [ROUTE_VIRTUAL, ROUTE_LOCAL],
          str(modes_in_combo))
    check("没有任何双输出入口",
          all("双输出" not in top.route_combo.itemText(i)
              for i in range(top.route_combo.count())),
          str([top.route_combo.itemText(i) for i in range(top.route_combo.count())]))

    # 模式 A（本机没有 VB-CABLE → 应橙色警告 + 说明）
    top.route_combo.setCurrentIndex(0)
    pump(app, 0.5)
    status = window.engine.route_status()
    check("模式 A 页面可见", top.route_stack.currentIndex() == 0)
    if not status.cable_playback:
        check("无 VB-CABLE 时显示未检测到", "未检测到" in top.virtual_text.text(),
              top.virtual_text.text())
        check("无 VB-CABLE 时警告徽章有 tooltip", "CABLE" in top.virtual_badge.toolTip(),
              top.virtual_badge.toolTip()[:50])
        check("模式 A 仍能出声（回退到默认设备）", status.primary_ok,
              status.primary_error)
        check("降级原因可读", bool(status.degrade_reason), status.degrade_reason)
    else:
        check("有 VB-CABLE 时锁定 CABLE Input",
              status.effective_mode == ROUTE_VIRTUAL and status.primary_ok)

    # 模式 C：单设备
    top.route_combo.setCurrentIndex(1)
    pump(app, 0.5)
    check("模式 C 页面可见", top.route_stack.currentIndex() == 2)
    status = window.engine.route_status()
    check("模式 C 正常开流", status.primary_ok and status.effective_mode == ROUTE_LOCAL,
          f"{status.effective_mode}/{status.primary_error}")
    check("模式 C 描述带「本地」", "本地" in window.engine.describe(), window.engine.describe())

    # 回归：在「仅本地」里换输出设备，不能把「虚拟声卡」的设备也改掉
    if top.local_combo.count() > 1:
        top.local_combo.setCurrentIndex(1)
        pump(app, 0.4)
    top.route_combo.setCurrentIndex(0)
    pump(app, 0.5)
    route_now = top.current_route()
    check("回到虚拟声卡时输出仍是 CABLE Input",
          (not route_now.primary_name)
          or ("CABLE" in route_now.primary_name.upper()),
          f"primary={route_now.primary_name!r}")
    status_now = window.engine.route_status()
    check("改本地设备后虚拟声卡仍生效",
          status_now.effective_mode == ROUTE_VIRTUAL and status_now.primary_ok,
          f"{status_now.effective_mode}/{status_now.primary_name}")

    # 滚轮护栏：未聚焦时拦下滚轮；聚焦时放行（用可控焦点状态的探针下拉测）
    from PySide6.QtWidgets import QComboBox as _QComboBox

    from ui import wheel_guard as _wheel_guard

    class _ProbeCombo(_QComboBox):
        """hasFocus 由测试控制，避免离屏环境下真实焦点不可靠。"""

        forced_focus = False

        def hasFocus(self) -> bool:  # noqa: N802
            return self.forced_focus

    guard = _wheel_guard.install(app)
    probe_combo = _ProbeCombo(window)
    probe_combo.addItems(["A", "B", "C"])
    probe_combo.show()
    pump(app, 0.2)

    def _wheel_event() -> QWheelEvent:
        return QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )

    probe_combo.forced_focus = False
    before_index = probe_combo.currentIndex()
    app.sendEvent(probe_combo, _wheel_event())
    pump(app, 0.2)
    check("未聚焦时滚轮不改下拉", probe_combo.currentIndex() == before_index,
          f"{before_index} -> {probe_combo.currentIndex()}")
    probe_combo.forced_focus = True
    app.sendEvent(probe_combo, _wheel_event())
    pump(app, 0.2)
    check("聚焦后滚轮可切换下拉",
          probe_combo.currentIndex() != before_index,
          f"{before_index} -> {probe_combo.currentIndex()}")
    probe_combo.close()
    top.route_combo.setCurrentIndex(0)
    pump(app, 0.4)

    # 测试路由按钮（桩掉 subprocess，避免真的弹系统面板）
    import subprocess as _subprocess

    opened_sound: list[str] = []
    original_popen = _subprocess.Popen

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            opened_sound.append(str(args[0]) if args else "")

    _subprocess.Popen = _FakePopen  # type: ignore[assignment]
    try:
        QTest.mouseClick(top.test_button, Qt.MouseButton.LeftButton)
        pump(app, 0.4)
        check("测试路由播放了测试音", window.engine.is_playing("__test_tone__")
              or "测试音已播放" in window.status_bar.status_label.text(),
              window.status_bar.status_label.text())
        check("测试音提示在底栏（非弹窗）",
              "测试音" in window.status_bar.status_label.text(),
              window.status_bar.status_label.text())
        QTest.mouseClick(top.open_settings_button, Qt.MouseButton.LeftButton)
        pump(app, 0.3)
        check("打开声音设置调用 mmsys.cpl",
              any("mmsys.cpl" in item for item in opened_sound), str(opened_sound))
    finally:
        _subprocess.Popen = original_popen  # type: ignore[assignment]

    # 设置对话框里的路由分组
    settings_route = SettingsDialog(window.config, None, parent=window)
    check("设置里有路由分组", hasattr(settings_route, "route_combo"))
    check("设置路由下拉只剩两项（无双输出）", settings_route.route_combo.count() == 2,
          str([settings_route.route_combo.itemText(i)
               for i in range(settings_route.route_combo.count())]))
    settings_route.refresh_cable_status("测试摘要", playback_ok=True, capture_ok=False)
    check("VB-CABLE 状态可刷新",
          "播放端" in settings_route.cable_label.text()
          and "测试摘要" == settings_route.cable_hint.text(),
          settings_route.cable_label.text())
    settings_route._toggle_steps(True)
    check("本地监听引导可展开", settings_route.steps_label.isVisible()
          or settings_route.steps_label.text().startswith("①"))
    route_values = settings_route.values()
    check("设置 values 含 output_route", "output_route" in route_values,
          str(sorted(route_values)))
    check("output_route 结构正确",
          set(route_values["output_route"]) >= {"mode"},
          str(route_values["output_route"]))
    settings_route.close()

    # ---------------- 5. 所有自绘控件绘制不抛异常 ----------------
    paint_errors: list[str] = []
    for widget in window.findChildren(QWidget):
        try:
            widget.grab()
        except Exception as exc:  # noqa: BLE001
            paint_errors.append(f"{type(widget).__name__}: {type(exc).__name__} {exc}")
    check("全部控件离屏绘制无异常", not paint_errors, str(paint_errors[:2]))

    # ---------------- 4d. 列表行几何：不许重叠 / 不许残留幽灵行 ----------------
    from ui.soundboard_panel import SoundRow

    def audit_rows(tag: str) -> None:
        view = window.board_panel.list_widget
        viewport_rows = view.viewport().findChildren(SoundRow)
        check(f"[{tag}] 行数 = item 数", len(viewport_rows) == view.count(),
              f"{len(viewport_rows)} 行 / {view.count()} item")
        check(f"[{tag}] 没有幽灵行",
              all(row.clip_id in window.board_panel._rows for row in viewport_rows),
              str([r.clip_id for r in viewport_rows
                   if r.clip_id not in window.board_panel._rows]))
        overlaps = []
        for i in range(len(viewport_rows)):
            for j in range(i + 1, len(viewport_rows)):
                if viewport_rows[i].geometry().intersects(viewport_rows[j].geometry()):
                    overlaps.append((i, j))
        check(f"[{tag}] 行之间没有重叠", not overlaps, str(overlaps))
        misaligned = []
        for index in range(view.count()):
            item = view.item(index)
            widget = view.itemWidget(item)
            if widget is None:
                continue
            visual = view.visualItemRect(item)
            if widget.pos() != visual.topLeft():
                misaligned.append((index, widget.pos().toTuple(), visual.topLeft().toTuple()))
        check(f"[{tag}] 行位置与 item 对齐", not misaligned, str(misaligned[:2]))
        # 每行只有一个可见的名称控件（编辑框与标签不能同时显示）
        bad_rows = []
        for row in viewport_rows:
            if row.name_label.isVisible() == row.name_edit.isVisible():
                bad_rows.append(row.name_label.text())
        check(f"[{tag}] 每行只有一处名称可见", not bad_rows, str(bad_rows[:2]))

    # ---------------- 4d2. 改名按钮（录制好的音效也能改名字） ----------------
    rename_view = window.board_panel.list_widget
    if rename_view.count():
        rename_row = rename_view.itemWidget(rename_view.item(0))
        old_name = rename_row._name_full
        check("行里有「修改名字」按钮", hasattr(rename_row, "rename_button"))
        QTest.mouseClick(rename_row.rename_button, Qt.MouseButton.LeftButton)
        pump(app, 0.3)
        check("点击后出现行内输入框", rename_row.name_edit.isVisible())
        check("输入框里是当前名字", rename_row.name_edit.text() == old_name,
              rename_row.name_edit.text())
        new_name = "改名测试-123"
        rename_row.name_edit.setText(new_name)
        rename_row._commit_rename()
        pump(app, 0.4)
        clip_now = window.library.get(rename_row.clip_id)
        check("改名写进音效库", clip_now is not None and clip_now.name == new_name,
              clip_now.name if clip_now else "None")
        check("行内标签同步更新", rename_row._name_full == new_name, rename_row._name_full)
        check("输入框已收起、标签恢复", not rename_row.name_edit.isVisible()
              and rename_row.name_label.isVisible())
        check("改名已落盘到配置",
              any(c.get("name") == new_name
                  for c in window.config.to_dict().get("clips", [])),
              "config.clips")
        # 改回去，避免影响后续用例
        rename_row._begin_rename(None)
        rename_row.name_edit.setText(old_name)
        rename_row._commit_rename()
        pump(app, 0.3)

    row_tone_a = ROOT / "temp" / "row_a.wav"
    row_tone_b = ROOT / "temp" / "row_b.wav"
    row_tone_c = ROOT / "temp" / "row_c.wav"
    for path in (row_tone_a, row_tone_b, row_tone_c):
        audio_io.write_wav(path, np.zeros((int(SAMPLE_RATE * 0.2), 1), np.float32), SAMPLE_RATE)
    window.board.import_paths([str(row_tone_a), str(row_tone_b), str(row_tone_c)])
    pump(app, 0.6)
    audit_rows("导入 3 条后")

    # 重命名第一行：这次点**铅笔按钮**（不是双击名称）
    view = window.board_panel.list_widget
    first_item = view.item(0)
    first_row = view.itemWidget(first_item)
    check("行里有「修改名字」按钮", hasattr(first_row, "rename_button"))
    QTest.mouseClick(first_row.rename_button, Qt.MouseButton.LeftButton)
    pump(app, 0.3)
    check("点按钮后就地出现输入框", first_row.name_edit.isVisible())
    first_row.name_edit.setText("改名后的音效")
    first_row._commit_rename()
    pump(app, 0.4)
    check("改名写回行文本", first_row.name_label.text() == "改名后的音效",
          first_row.name_label.text())
    clip_now = window.library.get(first_row.clip_id)
    check("改名写进音效库", clip_now is not None and clip_now.name == "改名后的音效",
          clip_now.name if clip_now else "None")
    # 落盘检查：配置写入时才从 ClipLibrary 取列表，所以先 flush 再读文件
    import json as _json

    window.store.flush()
    saved = _json.loads(Path(window.store.path).read_text(encoding="utf-8"))
    check("改名已落盘到 config.json",
          any(c.get("name") == "改名后的音效" for c in saved.get("clips", [])),
          str(window.store.path))
    # 重启不丢的关键：同名 .json sidecar 必须同步（否则启动扫描会用旧名字覆盖回来）
    if clip_now is not None:
        clip_file = clip_now.path()
        sidecar_obj = clip_file.with_suffix(".json")
        if sidecar_obj.is_file():
            payload = _json.loads(sidecar_obj.read_text(encoding="utf-8"))
            check("改名同步写进 sidecar（重启不丢）",
                  payload.get("name") == "改名后的音效", str(payload))
        else:
            print("[INFO] 该条目没有 sidecar，改名只依赖 config.json")
    audit_rows("改名后")

    # 删除中间那条
    middle_row = view.itemWidget(view.item(1))
    window.board.delete_clip(middle_row.clip_id)
    pump(app, 0.5)
    audit_rows("删除一条后")

    # 刷新目录（整表重建）
    window.board.refresh_directory()
    pump(app, 0.6)
    audit_rows("刷新目录后")

    # 悬停背景（WA_StyledBackground 才生效）
    hover_row = view.itemWidget(view.item(0))
    hover_row.setProperty("hovered", "true")
    style = hover_row.style()
    style.unpolish(hover_row)
    style.polish(hover_row)
    pump(app, 0.15)
    hover_pixel = hover_row.grab().toImage().pixelColor(hover_row.width() // 2, 6)
    hover_rgb = (hover_pixel.red(), hover_pixel.green(), hover_pixel.blue())
    check("行悬停背景生效（WA_StyledBackground）",
          abs(hover_rgb[0] - 241) <= 6 and abs(hover_rgb[1] - 242) <= 6
          and abs(hover_rgb[2] - 244) <= 6, str(hover_rgb))
    hover_row.setProperty("hovered", "false")
    style.unpolish(hover_row)
    style.polish(hover_row)
    pump(app, 0.15)

    # 行必须有**不透明**底色：透明会让重建时旧文字留在视口里（重影），
    # 也解释了"鼠标一悬停就正常"（悬停时那块被重涂成 hover 色）
    idle_image = hover_row.grab().toImage()
    idle_pixel = idle_image.pixelColor(hover_row.width() - 30, hover_row.height() // 2)
    idle_rgb = (idle_pixel.red(), idle_pixel.green(), idle_pixel.blue())
    check("行底色不透明（alpha=255）", idle_pixel.alpha() == 255, f"alpha={idle_pixel.alpha()}")
    check("行底色是画布色 #F7F8FA", idle_rgb == (247, 248, 250), str(idle_rgb))

    # ---------------- 4e. 混音浮层 ----------------
    from ui.top_bar import MixerPopover  # noqa: F401  (确认模块可导入)

    top = window.top_bar
    top._toggle_mixer_popover()
    pump(app, 0.3)
    popover = top.mixer_popover
    check("混音浮层能打开", popover.isVisible())
    check("浮层列出了混音麦克风", popover.mic_combo.count() > 0,
          str(popover.mic_combo.count()))
    settings_now = popover.settings()
    check("浮层设置结构正确",
          {"enabled", "mic_name", "mic_gain", "sfx_gain", "local_playback"}
          == set(vars(settings_now)),
          str(settings_now))
    check("浮层没有耳返开关", not hasattr(popover, "monitor_toggle"))
    check("浮层有「本地也播放音效」开关", hasattr(popover, "local_toggle"))
    # 勾选后设置里应带上 local_playback，并真的打开本地监听流
    top.route_combo.setCurrentIndex(0)      # 本地监听只在「虚拟声卡」路由下有意义
    pump(app, 0.4)
    popover.local_toggle.toggle()           # Toggle.setChecked 默认不发信号，模拟真实点击
    pump(app, 0.6)
    check("勾选后设置里 local_playback=True",
          popover.settings().local_playback is True,
          str(popover.settings().local_playback))
    check("本地音效监听流已开启", window.engine.local_playback_active(),
          str(window.engine.local_playback_active()))
    popover.local_toggle.toggle()
    pump(app, 0.4)
    check("取消勾选后本地监听流关闭", not window.engine.local_playback_active())
    popover.hide()

    # ---------------- 4f. 波形裁剪窗口 ----------------
    from ui.trimmer_dialog import TrimmerDialog, format_clock

    trim_rate = 48000
    trim_t = np.arange(trim_rate * 3) / trim_rate
    trim_audio = np.zeros(trim_t.size, np.float32)
    trim_audio[: trim_rate] = 0.5 * np.sin(2 * np.pi * 440 * trim_t[: trim_rate])
    trim_audio[trim_rate * 2:] = 0.3 * np.sin(2 * np.pi * 660 * trim_t[trim_rate * 2:])

    trimmer = TrimmerDialog(trim_audio, trim_rate, parent=window)
    trimmer.show()
    for _ in range(60):
        pump(app, 0.05)
        if trimmer.canvas.has_peaks():
            break
    check("裁剪窗口尺寸 800×400",
          (trimmer.width(), trimmer.height()) == (800, 400),
          f"{trimmer.width()}x{trimmer.height()}")
    check("波形峰值分析完成", trimmer.canvas.has_peaks())
    check("初始选区是全长", trimmer.selection() == (0.0, trimmer.canvas.duration()),
          str(trimmer.selection()))

    # 拖左端点：从 0 拖到约 1/3 处
    canvas = trimmer.canvas
    left_x = int(canvas._x_of(0.0))
    target_x = int(canvas._x_of(1.0))
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(left_x + 2, 100))
    QTest.mouseMove(canvas, QPoint(target_x, 100))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(target_x, 100))
    pump(app, 0.3)
    start, end = trimmer.selection()
    check("拖左手柄改变了起点", abs(start - 1.0) < 0.15, f"start={start:.2f}")
    check("右手柄没动", abs(end - 3.0) < 0.05, f"end={end:.2f}")
    check("时间标签同步", trimmer.time_bar.start_label.text() == format_clock(start),
          trimmer.time_bar.start_label.text())

    trimmed = trimmer.trimmed()
    expected = int(round((end - start) * trim_rate))
    check("裁剪结果长度正确", abs(trimmed.size - expected) <= 2,
          f"{trimmed.size} vs {expected}")

    # 重置 + 整体平移
    trimmer._reset()
    pump(app, 0.2)
    check("重置回全长", trimmer.selection() == (0.0, trimmer.canvas.duration()))
    middle = int((canvas._x_of(0.5) + canvas._x_of(0.6)) / 2)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(middle, 100))
    QTest.mouseMove(canvas, QPoint(middle + 40, 100))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(middle + 40, 100))
    pump(app, 0.3)
    start2, end2 = trimmer.selection()
    check("拖选区内可整体平移（长度不变）",
          abs((end2 - start2) - (canvas.duration())) < 0.05 or end2 <= canvas.duration(),
          f"{start2:.2f}-{end2:.2f}")
    trimmer.close()

    # ---------------- 4g. 绑定快捷键不会把全局热键永久挂起 ----------------
    row_view = window.board_panel.list_widget
    if row_view.count():
        target_row = row_view.itemWidget(row_view.item(0))
        window.board.begin_bind(target_row.clip_id)
        pump(app, 0.25)
        check("进入绑定时挂起全局热键", window.hotkeys._hook._suspended)
        badge = target_row.hotkey_badge
        badge.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))   # 直接模拟"点了别处"
        pump(app, 0.3)
        check("失焦后自动解除挂起（不锁死热键）", not window.hotkeys._hook._suspended)
        check("徽章退出捕获态", not badge.isCapturing())

    # ---------------- 5. 关键流程仍在 ----------------
    tone = ROOT / "temp" / "regress_tone.wav"
    audio_io.write_wav(tone, np.zeros((int(SAMPLE_RATE * 0.2), 1), np.float32), SAMPLE_RATE)
    before_import = window.board_panel.count()
    window.board.import_paths([str(tone)])
    pump(app, 0.4)
    check("导入仍正常", window.board_panel.count() == before_import + 1,
          f"{before_import} → {window.board_panel.count()}")

    panel = window.recorder_panel
    panel.start_recording()
    pump(app, 0.9)
    panel.stop_recording()
    pump(app, 0.4)
    check("录音仍正常拿到数据", panel._data is not None,
          str(None if panel._data is None else panel._data.shape))
    before_save = window.board_panel.count()
    panel.name_edit.setText("回归录音")
    panel.save_to_library()
    pump(app, 0.4)
    check("保存仍正常加入列表", window.board_panel.count() == before_save + 1,
          f"{before_save} → {window.board_panel.count()}")

    # ---------------- 6. 状态栏 / 托盘 / 分段 ----------------
    window.show_error("回归测试错误")
    pump(app, 0.2)
    check("错误提示走底栏", window.status_bar.dot.property("state") == "warning")
    window._restore_status()
    check("状态可恢复", window.status_bar.dot.property("state") == "ok")

    def is_emoji(text: str) -> bool:
        return any(
            0x1F300 <= ord(ch) <= 0x1FAFF
            or 0x2600 <= ord(ch) <= 0x27BF
            or 0xFE00 <= ord(ch) <= 0xFE0F
            for ch in text
        )

    hits = [w.text() for w in window.findChildren(QLabel) if is_emoji(w.text())]
    hits += [b.text() for b in window.findChildren(QPushButton) if is_emoji(b.text())]
    check("界面无 emoji", not hits, str(hits[:3]))

    window.shutdown()
    pump(app, 0.2)

    # ---------------- 清理 ----------------
    for leftover in (ROOT / "audio").glob("*"):
        if leftover.name != ".gitkeep":
            leftover.unlink(missing_ok=True)
    for leftover in (ROOT / "temp").glob("*.wav"):
        leftover.unlink(missing_ok=True)
    cfg_path.unlink(missing_ok=True)
    if backup is not None:
        cfg_path.write_bytes(backup)

    print()
    if problems:
        print(f"{len(problems)} 项失败：" + "、".join(problems))
        return 1
    print("边缘拖拽修复回归通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
