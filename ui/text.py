"""文本小工具：把 core 里的"带 emoji 的展示名"处理成纯文字。

为什么要这一层：``core/constants.MODE_TITLES``、``device_enum`` 的分组标题里
带 ``🎤``/``🔊`` 这类 emoji（那是给旧版本用的）。新界面规定"不用 emoji 当图标"，
但 core 本次不改，所以在**显示前**统一剥掉，图标改由 SVG 提供。
"""

from __future__ import annotations

import unicodedata

#: 这些区段里的字符按"图形符号"处理（emoji / 装饰符号 / 变体选择符）
_GRAPHIC_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0xFE00, 0xFE0F),   # 变体选择符
    (0x200D, 0x200D),   # 零宽连接符
    (0x20E3, 0x20E3),   # 组合键帽
)


def _is_graphic(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _GRAPHIC_RANGES)


def strip_emoji(text: str) -> str:
    """去掉 emoji 与装饰符号，并把多余空格压成一个（用于标题/分组名）。"""
    if not text:
        return ""
    cleaned = "".join("" if _is_graphic(ch) else ch for ch in text)
    return " ".join(cleaned.split())


def strip_emoji_keep_space(text: str) -> str:
    """同 :func:`strip_emoji`，但保留原有空格（用于行内文案）。"""
    if not text:
        return ""
    cleaned = "".join("" if _is_graphic(ch) else ch for ch in text)
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned.strip()


def wide_len(text: str) -> int:
    """估算显示宽度（全角按 2 计），用于自绘控件里的居中对齐。"""
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total
