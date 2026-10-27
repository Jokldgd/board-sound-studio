"""全局常量（音效板 + 录音共用）。

单独放一个模块，避免 ``config → clip_library → audio_io → config`` 这种循环导入：
所有底层模块只依赖这里，谁也不依赖 config。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 音频格式
#: 播放与录音统一格式：音效板落盘固定 44100Hz / 16bit，音效板直读 24
SAMPLE_RATE = 44100
BIT_DEPTH = 16

#: 单条音量上限，对应界面 200%
MAX_VOLUME = 2.0

#: 支持的导入格式
SUPPORTED_EXTS = {".mp3", ".wav", ".ogg", ".flac"}

# ---------------------------------------------------------------- 导出格式
FORMAT_WAV = "wav"
FORMAT_MP3 = "mp3"
FORMATS = (FORMAT_WAV, FORMAT_MP3)

# ---------------------------------------------------------------- 播放模式
CLIP_TOGGLE = "toggle"   # 按一下播完
CLIP_HOLD = "hold"       # 按住播放、松开停止
CLIP_MODES = (CLIP_TOGGLE, CLIP_HOLD)
CLIP_MODE_TITLES = {CLIP_TOGGLE: "按一下", CLIP_HOLD: "按住"}

# ---------------------------------------------------------------- 音效来源
SOURCE_IMPORT = "import"   # 手动导入
SOURCE_RECORD = "record"   # 录音生成
SOURCE_TITLES = {SOURCE_IMPORT: "导入", SOURCE_RECORD: "录音"}

# ---------------------------------------------------------------- 录音模式
MODE_MIC = "mic"
MODE_SYSTEM = "system"
MODE_APP = "app"
MODES = (MODE_MIC, MODE_SYSTEM, MODE_APP)
MODE_TITLES = {MODE_MIC: "🎤 麦克风", MODE_SYSTEM: "🔊 系统声音", MODE_APP: "🎯 单个应用"}

# ---------------------------------------------------------------- 其它
CONFIG_VERSION = 1
DEFAULT_RECORD_HOTKEY = "f9"
#: "最近使用"最多记几个设备
RECENT_LIMIT = 3
#: 录音文件名前缀（内部联动约定）
REC_PREFIX = "rec_"
