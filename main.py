"""Soundboard Studio —— 程序入口。

职责只有"装环境"：日志、样式、图标、异常兜底、启动主窗口。
另带两个环境变量开关（打包成窗口程序后没有控制台也能用）：

    STUDIO_PROBE=1          只跑一遍能力诊断写进 studio.log，然后退出
    STUDIO_EXIT_AFTER_MS=N  启动 N 毫秒后自动退出（用于自动验证 exe）
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QtMsgType, QTimer, qInstallMessageHandler
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from core import paths
from core.config import ConfigStore
from ui import theme, wheel_guard
from ui.icon import app_icon
from ui.main_window import MainWindow

#: 生命周期与 QApplication 同长，必须留引用
_wheel_guard = None

logger = logging.getLogger("studio")


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    try:
        handler = RotatingFileHandler(
            paths.log_path(), maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(handler)


def _qt_message_handler(mode, context, message) -> None:  # noqa: ANN001
    level = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }.get(mode, logging.INFO)
    logger.log(level, "Qt: %s", message)


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb) -> None:  # noqa: ANN001
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logger.error("未捕获异常：\n%s", text)
        try:
            QMessageBox.critical(
                None, "Soundboard Studio 出错了",
                f"程序遇到一个未处理的问题：\n\n{exc}\n\n"
                f"详细信息已写入：{paths.log_path()}",
            )
        except Exception:
            pass

    sys.excepthook = hook


def run_diagnostics(store: ConfigStore) -> None:
    """三模式 + 设备 + 热键所需能力的完整诊断（写日志，不弹窗）。"""
    from core import app_enum, capture_app, device_enum
    from core.audio_engine import AudioEngine

    logger.info("=== 诊断开始 ===")
    logger.info("Python %s / build %s / 基准目录 %s",
                sys.version.split()[0], sys.getwindowsversion().build, paths.base_dir())

    ok, name = device_enum.backend_available()
    logger.info("录音后端：%s（可用=%s）", name, ok)

    rules = device_enum.merge_rules(store.config.device_name_rules)
    outputs = device_enum.list_devices("output", rules)
    logger.info("播放输出设备 %d 个：%s", len(outputs),
                " | ".join(f"{c.short_name}[{c.hostapi}]" for c in outputs[:8]))
    inputs = device_enum.list_devices("input", rules)
    logger.info("模式 1 输入设备 %d 个：%s", len(inputs),
                " | ".join(c.short_name for c in inputs[:8]))
    loops = device_enum.list_devices("loopback", rules)
    logger.info("模式 2 loopback 设备 %d 个（后端 %s）", len(loops),
                device_enum.loopback_backend())

    session_ok, session_reason = app_enum.is_supported()
    apps = app_enum.list_audio_apps(include_inferred=False) if session_ok else []
    logger.info("模式 3 会话枚举：可用=%s %s；正在播放 %d 个：%s", session_ok, session_reason,
                len(apps), " | ".join(a.label() for a in apps[:8]))

    capable, reason = capture_app.probe_capability(force=True)
    logger.info("模式 3 进程 loopback：可用=%s%s", capable,
                f"（{reason}）" if reason and reason != "可用" else "")

    # ---- 输出路由（本地 + 远端同时可听的关键） ----
    cable = device_enum.detect_vb_cable(rules, show_all=True)
    logger.info("VB-CABLE：%s", cable.summary())
    route = store.config.route()
    logger.info("输出路由配置：mode=%s primary=%r monitor=%r",
                route.mode, route.primary_name, route.monitor_name)
    route_engine = AudioEngine()
    route_engine.configure_rules(rules, store.config.show_all_devices)
    status = route_engine.set_route(route)
    logger.info("路由实际生效：mode=%s effective=%s degraded=%r 主路=%r(%s) 监听=%r(%s) "
                "延迟=%.1fms 采样率=%dHz",
                status.mode, status.effective_mode, status.degrade_reason or "",
                status.primary_label, "ok" if status.primary_ok else status.primary_error,
                status.monitor_label, "ok" if status.monitor_ok else "未启用",
                status.latency_ms, status.samplerate)
    if status.degrade_reason:
        logger.warning("路由降级原因：%s", status.degrade_reason)
    route_engine.close()

    logger.info("音效目录：%s（%d 个文件）", paths.audio_dir(),
                len(list(paths.audio_dir().glob("*.wav"))) + len(list(paths.audio_dir().glob("*.mp3"))))
    logger.info("音效列表：%d 条", len(store.config.clips))
    logger.info("=== 诊断结束 ===")


def _set_app_user_model_id() -> None:
    """给进程一个显式 AppUserModelID。

    Windows 靠它把窗口/任务栏/开始菜单归到同一个应用；不设的话，无边框窗口在
    任务栏可能显示成"空白/幽灵"图标，固定到任务栏也会分裂成两个条目。
    """
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Jokldgd.SoundboardStudio"
        )
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    setup_logging()
    logger.info("启动，基准目录 %s", paths.base_dir())

    _set_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName("SoundboardStudio")
    app.setApplicationDisplayName("Soundboard Studio")
    app.setOrganizationName("Soundboard")
    app.setQuitOnLastWindowClosed(True)

    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setWindowIcon(app_icon())
    app.setStyleSheet(theme.stylesheet())
    app.setPalette(theme.palette())
    # 滚轮护栏：未聚焦的下拉/数字框不会被滚轮误改（见 ui/wheel_guard.py）
    global _wheel_guard
    _wheel_guard = wheel_guard.install(app)

    qInstallMessageHandler(_qt_message_handler)
    _install_excepthook()

    store = ConfigStore()
    store.load()
    for warning in store.warnings:
        logger.warning("配置提示：%s", warning)

    if os.environ.get("STUDIO_PROBE", "").strip() not in ("", "0", "false"):
        run_diagnostics(store)
        return 0

    window = MainWindow(store)
    window.show()
    app.aboutToQuit.connect(window.shutdown)

    exit_after = os.environ.get("STUDIO_EXIT_AFTER_MS", "").strip()
    if exit_after.isdigit():
        logger.info("调试开关：%s ms 后自动退出", exit_after)
        QTimer.singleShot(int(exit_after), app.quit)

    # 调试开关：模拟用户点窗口的 ✕（验证"关闭即退出、无残留进程"）
    close_after = os.environ.get("STUDIO_CLOSE_AFTER_MS", "").strip()
    if close_after.isdigit():
        logger.info("调试开关：%s ms 后模拟点击关闭", close_after)
        QTimer.singleShot(int(close_after), window.close)

    code = app.exec()
    logger.info("退出，返回码 %s", code)

    # 兜底：某些平台/库会留下非守护线程（如热键钩子），导致 exec 返回后进程不退。
    # 再跑一次幂等清理，并把还活着的线程写进日志，最后强制结束进程。
    try:
        window.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("退出清理异常：%s", exc)

    alive = [t for t in threading.enumerate() if t is not threading.main_thread()]
    if alive:
        logger.warning(
            "退出时仍有 %d 个线程：%s",
            len(alive), ", ".join(f"{t.name}(daemon={t.daemon})" for t in alive[:6]),
        )
    # 只剩守护线程时可以直接返回（Python 会自行结束它们）；
    # 若还有非守护线程（会阻止进程退出），用 os._exit 强制结束，保证不留进程。
    if any(not t.daemon for t in alive):
        logger.warning("存在非守护线程，强制结束进程")
        os._exit(int(code or 0))
    return code


if __name__ == "__main__":
    sys.exit(main())
