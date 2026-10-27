"""设计系统：颜色 / 间距 / 圆角 / 字体 / 尺寸 / 动效 + 全局 QSS。

**唯一颜色来源**。任何控件、任何 QSS 都不许写字面色值——
QSS 里全部用 ``{accent}`` 这类占位符，由 :func:`stylesheet` 统一注入；
自绘控件用 :func:`color` 取同一个 token。

深色模式已预留（``PALETTE[key]['dark']``），本次只启用浅色。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# ---------------------------------------------------------------------------
# 颜色（唯一可交互色 = accent；红色只给录音；绿色只给播放中/成功；橙色只给冲突/警告）
# ---------------------------------------------------------------------------
PALETTE: dict[str, dict[str, str]] = {
    "canvas": {"light": "#F7F8FA", "dark": "#111214"},          # 背景-画布
    "card": {"light": "#FFFFFF", "dark": "#1A1C1F"},            # 背景-卡片
    "hover": {"light": "#F1F2F4", "dark": "#23262A"},           # 背景-悬停
    "pressed": {"light": "#E8EAED", "dark": "#2B2F34"},         # 背景-按下
    "border": {"light": "#E5E7EB", "dark": "#2E3238"},          # 边框-常规
    "borderStrong": {"light": "#D1D5DB", "dark": "#3C4148"},    # 边框-强调
    "text": {"light": "#1F2328", "dark": "#E8EAED"},            # 文字-主
    "textMuted": {"light": "#6B7280", "dark": "#9CA3AF"},       # 文字-次
    "textFaint": {"light": "#9CA3AF", "dark": "#6B7280"},       # 文字-弱
    "accent": {"light": "#3B82F6", "dark": "#60A5FA"},          # 强调-主
    "accentHover": {"light": "#2563EB", "dark": "#3B82F6"},     # 强调-悬停
    "record": {"light": "#EF4444", "dark": "#F87171"},          # 录音-红
    "success": {"light": "#10B981", "dark": "#34D399"},         # 成功-绿
    "warning": {"light": "#F59E0B", "dark": "#FBBF24"},         # 警告-橙
    # 派生色（由上面推导，避免在控件里散落 rgba 字面量）
    "accentSoft": {"light": "#EFF6FF", "dark": "#1B2735"},      # 强调色 8% ≈ 淡底
    "recordSoft": {"light": "#FEF2F2", "dark": "#33191B"},
    "successSoft": {"light": "#ECFDF5", "dark": "#102A22"},
    "warningSoft": {"light": "#FFFBEB", "dark": "#2E2413"},
    "overlay": {"light": "rgba(59, 130, 246, 0.08)", "dark": "rgba(96, 165, 250, 0.12)"},
    "shadow": {"light": "rgba(0, 0, 0, 0.06)", "dark": "rgba(0, 0, 0, 0.35)"},
    "transparent": {"light": "transparent", "dark": "transparent"},
}

MODE = "light"

# ---------------------------------------------------------------------------
# 间距（基准 8；只用 4 / 8 / 12 / 16 / 24）
# ---------------------------------------------------------------------------
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}

# ---------------------------------------------------------------------------
# 圆角
# ---------------------------------------------------------------------------
RADIUS = {"xs": 4, "sm": 6, "btn": 8, "card": 10, "pill": 999}

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------
FAMILY_UI = '"Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif'
FAMILY_NUM = '"Inter", "Segoe UI", "Microsoft YaHei UI", sans-serif'
FAMILY_MONO = '"Cascadia Mono", "Consolas", monospace'

TYPE = {
    "title": ("15px", 600),
    "body": ("13px", 400),
    "caption": ("12px", 400),
    "badge": ("11px", 500),
    "timer": ("22px", 600),
    "volume": ("11px", 500),
}

# ---------------------------------------------------------------------------
# 尺寸
# ---------------------------------------------------------------------------
SIZE = {
    "titleBar": 40,
    "topBar": 48,
    "bottomBar": 32,
    "sidePanel": 340,
    "toolbar": 44,
    "row": 56,
    "iconInline": 16,
    "iconBar": 20,
    "btnIcon": 28,
    "window": (1100, 680),
    "windowMin": (960, 620),
    "cardPadding": 16,
    "moduleGap": 24,
    "rowGap": 12,
}

# ---------------------------------------------------------------------------
# 动效（毫秒 / 缓动）
# ---------------------------------------------------------------------------
ANIM = {
    "hover": 120,
    "panel": 200,
    "pulse": 1200,
    "rowIn": 150,
    "spin": 900,
    "easing": "OutCubic",
}


def color(key: str, mode: str | None = None) -> str:
    """取颜色 token（缺失时回退文字色，避免 None 传到画笔里）。"""
    entry = PALETTE.get(key)
    if entry is None:
        return PALETTE["text"][mode or MODE]
    return entry.get(mode or MODE) or entry["light"]


def colors(mode: str | None = None) -> dict[str, str]:
    """把整份调色板展开成 ``{key: "#RRGGBB"}``，供 QSS 模板注入。"""
    active = mode or MODE
    return {key: values.get(active) or values["light"] for key, values in PALETTE.items()}


def palette(mode: str | None = None) -> QPalette:
    """给 QApplication 的浅色调色板。

    QSS 管不到的地方（下拉弹层、右键菜单、tooltip 这类独立顶层窗口）会用系统
    palette；Windows 深色主题下默认是深底浅字，和我们的浅色界面撞在一起就会出现
    "下拉背景太暗、字看不清"。这里显式给一份浅色调色板兜底。
    """
    c = colors(mode)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["canvas"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["card"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["hover"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["card"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["card"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accentSoft"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["textFaint"]))
    return pal


def stylesheet(mode: str | None = None) -> str:
    """生成全局 QSS。颜色全部来自 token，控件代码里不出现字面色值。"""
    c = colors(mode)
    ui = FAMILY_UI
    num = FAMILY_NUM
    mono = FAMILY_MONO
    title_size, title_weight = TYPE["title"]
    body_size, body_weight = TYPE["body"]
    caption_size, _ = TYPE["caption"]
    badge_size, badge_weight = TYPE["badge"]
    timer_size, timer_weight = TYPE["timer"]
    volume_size, volume_weight = TYPE["volume"]

    return f"""
    /* ================= 基础 ================= */
    QWidget {{
        color: {c['text']};
        font-family: {ui};
        font-size: {body_size};
        font-weight: {body_weight};
    }}
    QMainWindow, QDialog, QWidget#Canvas {{
        background: {c['canvas']};
    }}
    QToolTip {{
        background: {c['text']};
        color: {c['card']};
        border: none;
        padding: 5px 8px;
        border-radius: {RADIUS['xs']}px;
        font-size: {caption_size};
    }}

    /* ================= 窗口外壳 ================= */
    QWidget#Shell {{
        background: {c['canvas']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['card']}px;
    }}
    QWidget#ShellMaximized {{
        background: {c['canvas']};
        border: none;
        border-radius: 0px;
    }}

    /* ================= 标题栏 ================= */
    QWidget#TitleBar {{
        background: {c['card']};
        border-top-left-radius: {RADIUS['card']}px;
        border-top-right-radius: {RADIUS['card']}px;
    }}
    QLabel#AppTitle {{
        font-size: {title_size};
        font-weight: {title_weight};
        color: {c['text']};
        background: transparent;
    }}
    QLabel#AppDot {{
        background: {c['accent']};
        border-radius: 8px;
    }}

    /* ================= 顶栏 ================= */
    QWidget#TopBar {{
        background: {c['card']};
    }}
    QLabel#FieldLabel {{
        font-size: {caption_size};
        color: {c['textMuted']};
        background: transparent;
    }}
    QLabel#MasterValue {{
        font-size: {volume_size};
        font-weight: {volume_weight};
        font-family: {num};
        color: {c['textMuted']};
        background: transparent;
    }}

    /* ================= 分割线 ================= */
    QFrame#HSep {{
        background: {c['border']};
        border: none;
        max-height: 1px;
        min-height: 1px;
    }}
    QFrame#VSep {{
        background: {c['border']};
        border: none;
        max-width: 1px;
        min-width: 1px;
    }}

    /* ================= 面板 ================= */
    QWidget#SoundboardPanel {{
        background: {c['canvas']};
    }}
    QWidget#RecorderPanel {{
        background: {c['card']};
        border-left: none;
    }}
    QWidget#Toolbar {{
        background: {c['canvas']};
    }}
    QLabel#PanelTitle {{
        font-size: {body_size};
        font-weight: 600;
        color: {c['text']};
        background: transparent;
    }}
    QLabel#CountBadge {{
        font-size: {badge_size};
        font-weight: {badge_weight};
        color: {c['textMuted']};
        background: {c['hover']};
        border-radius: {RADIUS['xs']}px;
        padding: 1px 6px;
        min-width: 14px;
    }}

    /* ================= 卡片 ================= */
    QFrame#Card, QFrame#ResultCard {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['card']}px;
    }}
    QLabel#SectionTitle {{
        font-size: {caption_size};
        color: {c['textMuted']};
        background: transparent;
    }}
    QLabel#HintLabel {{
        font-size: {badge_size};
        color: {c['textFaint']};
        background: transparent;
    }}
    QLabel#Caption {{
        font-size: {caption_size};
        color: {c['textMuted']};
        background: transparent;
    }}
    QLabel#ErrorLabel {{
        font-size: {badge_size};
        color: {c['record']};
        background: transparent;
    }}

    /* ================= 按钮 ================= */
    QPushButton {{
        background: {c['card']};
        border: 1px solid {c['borderStrong']};
        border-radius: {RADIUS['btn']}px;
        padding: 0px 12px;
        min-height: 28px;
        color: {c['text']};
        font-size: {body_size};
    }}
    QPushButton:hover {{
        background: {c['hover']};
    }}
    QPushButton:pressed {{
        background: {c['pressed']};
    }}
    QPushButton:disabled {{
        color: {c['textFaint']};
        background: {c['card']};
        border-color: {c['border']};
    }}
    QPushButton#PrimaryButton {{
        background: {c['accent']};
        border: 1px solid {c['accent']};
        color: #FFFFFF;
        font-weight: 600;
        border-radius: {RADIUS['sm']}px;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {c['accentHover']};
        border-color: {c['accentHover']};
    }}
    QPushButton#PrimaryButton:pressed {{
        background: {c['accent']};
        border-color: {c['accent']};
    }}
    QPushButton#PrimaryButton:disabled {{
        background: {c['accentSoft']};
        border-color: {c['accentSoft']};
        color: {c['textFaint']};
    }}
    QPushButton#SecondaryButton {{
        background: {c['card']};
        border: 1px solid {c['borderStrong']};
        border-radius: {RADIUS['sm']}px;
        color: {c['text']};
    }}
    QPushButton#SecondaryButton:hover {{
        background: {c['hover']};
    }}
    QPushButton#LinkButton {{
        background: transparent;
        border: none;
        color: {c['accent']};
        padding: 0px;
        min-height: 18px;
        font-size: {caption_size};
    }}
    QPushButton#LinkButton:hover {{
        color: {c['accentHover']};
    }}

    /* ================= 输入类 ================= */
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['sm']}px;
        padding: 0px 8px;
        min-height: 28px;
        color: {c['text']};
        selection-background-color: {c['accent']};
        selection-color: #FFFFFF;
    }}
    QComboBox:hover, QLineEdit:hover {{
        border-color: {c['borderStrong']};
    }}
    QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus {{
        border-color: {c['accent']};
    }}
    QComboBox:disabled, QLineEdit:disabled {{
        color: {c['textFaint']};
        background: {c['canvas']};
        border-color: {c['border']};
    }}
    QComboBox#FlatCombo {{
        border-color: transparent;
        background: transparent;
    }}
    QComboBox#FlatCombo:hover {{
        border-color: {c['border']};
        background: {c['card']};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}
    QComboBox QAbstractItemView {{
        background: {c['card']};
        color: {c['text']};
        border: 1px solid {c['borderStrong']};
        border-radius: {RADIUS['sm']}px;
        padding: 4px;
        outline: none;
        selection-background-color: {c['hover']};
        selection-color: {c['text']};
    }}
    /* 弹层条目：显式给字色与行高，否则 Windows 深色主题下会出现深底深字 */
    QComboBox QAbstractItemView::item {{
        min-height: 26px;
        padding: 2px 8px;
        color: {c['text']};
        background: transparent;
    }}
    QComboBox QAbstractItemView::item:selected {{
        background: {c['hover']};
        color: {c['text']};
    }}
    QComboBox QAbstractItemView::item:disabled {{
        color: {c['textFaint']};
    }}
    QLineEdit#ErrorField {{
        border-color: {c['record']};
    }}

    /* ================= 复选框 ================= */
    QCheckBox {{
        spacing: 8px;
        color: {c['text']};
        font-size: {body_size};
        background: transparent;
    }}
    QCheckBox::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid {c['borderStrong']};
        border-radius: {RADIUS['xs']}px;
        background: {c['card']};
    }}
    QCheckBox::indicator:hover {{
        border-color: {c['accent']};
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent']};
        border-color: {c['accent']};
    }}
    QCheckBox:disabled {{
        color: {c['textFaint']};
    }}

    /* ================= 列表 ================= */
    QListWidget#ClipList {{
        background: transparent;
        border: none;
        outline: none;
        padding: 0px;
    }}
    QListWidget#ClipList::item {{
        background: transparent;
        border: none;
        margin: 0px;
    }}
    QListWidget#ClipList::item:selected {{
        background: transparent;
    }}
    /* 行 widget 必须是**不透明**底色：透明会让重建时的旧像素留在视口里透出来，
       表现为"文字/色点重影，鼠标一悬停就正常"（悬停时这里被重涂成 hover 色）。 */
    QWidget#SoundRow {{
        background: {c['canvas']};
        border: none;
        border-radius: {RADIUS['btn']}px;
    }}
    /* 注意：SoundRow 是 QWidget 子类，必须 setAttribute(WA_StyledBackground, True)，
       下面两条背景才会真正绘制（否则属性变了但颜色不生效）。 */
    QWidget#SoundRow[hovered="true"] {{
        background: {c['hover']};
    }}
    QWidget#SoundRow[playing="true"] {{
        background: {c['hover']};
    }}
    QLabel#ClipName {{
        font-size: {body_size};
        color: {c['text']};
        background: transparent;
    }}
    /* 行内改名输入框：与行高对齐，聚焦才显强调边框，避免和 QLabel 切换时尺寸跳动 */
    QLineEdit#RowNameEdit {{
        background: {c['card']};
        border: 1px solid {c['accent']};
        border-radius: {RADIUS['xs']}px;
        padding: 0px 6px;
        font-size: {body_size};
        color: {c['text']};
        selection-background-color: {c['accent']};
        selection-color: #FFFFFF;
    }}
    QLabel#VolumeValue {{
        font-size: {volume_size};
        font-weight: {volume_weight};
        font-family: {num};
        color: {c['textMuted']};
        background: transparent;
    }}
    QLabel#SourceDot {{
        border-radius: 4px;
        background: {c['textFaint']};
    }}
    QLabel#SourceDot[source="record"] {{
        background: {c['record']};
    }}
    QLabel#SourceDot[source="import"] {{
        background: {c['textFaint']};
    }}

    /* ================= 空状态 / 拖拽层 ================= */
    QLabel#EmptyTitle {{
        font-size: {body_size};
        color: {c['textMuted']};
        background: transparent;
    }}
    QLabel#EmptyText {{
        font-size: {caption_size};
        color: {c['textFaint']};
        background: transparent;
    }}
    QWidget#DropOverlay {{
        background: {c['overlay']};
        border: 2px dashed {c['accent']};
        border-radius: {RADIUS['card']}px;
    }}
    QLabel#DropText {{
        font-size: {body_size};
        font-weight: 600;
        color: {c['accent']};
        background: transparent;
    }}

    /* ================= 底栏 ================= */
    QWidget#StatusBar {{
        background: {c['card']};
        border-bottom-left-radius: {RADIUS['card']}px;
        border-bottom-right-radius: {RADIUS['card']}px;
    }}
    QLabel#StatusText, QLabel#NowPlaying {{
        font-size: {badge_size};
        color: {c['textMuted']};
        background: transparent;
    }}
    QLabel#NowPlayingAccent {{
        font-size: {badge_size};
        color: {c['success']};
        background: transparent;
    }}
    QLabel#ModeText {{
        font-size: {badge_size};
        color: {c['textMuted']};
        background: transparent;
    }}
    QLabel#StatusDot {{
        border-radius: 3px;
        background: {c['success']};
    }}
    QLabel#StatusDot[state="idle"] {{
        background: {c['textFaint']};
    }}
    QLabel#StatusDot[state="recording"] {{
        background: {c['record']};
    }}
    QLabel#StatusDot[state="warning"] {{
        background: {c['warning']};
    }}

    /* ================= 计时器 ================= */
    QLabel#TimerLabel {{
        font-size: {timer_size};
        font-weight: {timer_weight};
        font-family: {num};
        color: {c['text']};
        background: transparent;
    }}
    QLabel#TimerLabel[state="idle"] {{
        color: {c['textFaint']};
    }}
    QLabel#TimerLabel[state="recording"] {{
        color: {c['text']};
    }}

    /* ================= 滚动条 ================= */
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['borderStrong']};
        border-radius: 3px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c['textFaint']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}

    /* ================= 混音浮层（Popup）================= */
    /* Popup 是独立顶层窗口，必须自己给不透明底，否则会像早先的对话框一样全透明 */
    QFrame#MixerPopover {{
        background: {c['card']};
        border: 1px solid {c['borderStrong']};
        border-radius: {RADIUS['card']}px;
    }}

    /* ================= 裁剪窗口 ================= */
    QWidget#WaveformArea {{
        background: {c['canvas']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['sm']}px;
    }}

    /* ================= 对话框 ================= */
    /* 注意：objectName 挂在 QFrame 上（外层 QDialog 半透明以露出投影留白），
       所以这里用 #SheetDialog 而不是 QDialog#SheetDialog —— 否则选择器不匹配，
       对话框会变成全透明（曾经就是这个坑）。 */
    #SheetDialog {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['card']}px;
    }}
    QLabel#DialogTitle {{
        font-size: {title_size};
        font-weight: {title_weight};
        color: {c['text']};
        background: transparent;
    }}
    QMenu {{
        background: {c['card']};
        border: 1px solid {c['borderStrong']};
        border-radius: {RADIUS['sm']}px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 16px;
        border-radius: {RADIUS['xs']}px;
        color: {c['text']};
    }}
    QMenu::item:selected {{
        background: {c['hover']};
    }}
    QMenu::separator {{
        height: 1px;
        background: {c['border']};
        margin: 4px 8px;
    }}
    """


def font_px(key: str) -> tuple[int, int]:
    """把 ``TYPE`` 里的 "15px" 转成 (15, weight)，方便自绘控件用。"""
    text, weight = TYPE[key]
    return int(text.replace("px", "")), int(weight)
