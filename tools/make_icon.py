"""生成录音工具的图标 assets/icon.ico 与 icon.png（打包用）。

用法（在 recorder 目录下）：
    python tools/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.icon import pixmap, save_ico  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 (QPainter 需要实例)
    assets = ROOT / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    ico = assets / "icon.ico"
    save_ico(ico)
    print(f"已生成 {ico}（{ico.stat().st_size} 字节）")

    png = assets / "icon.png"
    pixmap(256).save(str(png), "PNG")
    print(f"已生成 {png}（{png.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
