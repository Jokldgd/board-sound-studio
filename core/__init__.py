"""Soundboard Studio 的核心逻辑层：配置、音效库、统一音频引擎、录音、热键。

这一层不依赖 PySide6（除 Qt 信号外一律用回调/队列），可脱离界面用脚本测试
（见 tools/selftest.py）。
"""
