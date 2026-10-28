# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：Soundboard Studio（onedir + 无控制台 + 图标）。

用法（在项目根目录执行）：
    python -m PyInstaller packaging/SoundboardStudio.spec --noconfirm \
        --distpath dist --workpath build

产物：dist/SoundboardStudio/SoundboardStudio.exe（+ _internal 依赖目录）

关键点（为什么这么写）：
* sounddevice 自带 PortAudio DLL（_sounddevice_data/）：必须 collect_dynamic_libs，
  否则打包后 open() 报 "PortAudio library not found" —— 表现就是"没声音"。
* soundfile 的 libsndfile*.dll 在 _soundfile_data/：数据 + 动态库都要收，
  否则读写 wav 直接抛异常。
* pyaudiowpatch 自带一份 PortAudio（WASAPI loopback），必须 collect_all，
  否则录音模式 2/3 打不开流。
* pycaw / comtypes：音频会话枚举（"单个应用"模式）需要它们的元数据与 COM 类型。
* PySide6 交给 PyInstaller 官方 hook：Qt 插件（platforms / styles /
  imageformats / svg）会自动带上。**不要** --collect-all PySide6，
  否则体积从 ~150MB 膨胀到 500MB+。
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

#: packaging/ 的上一级 = 项目根（SPECPATH 由 PyInstaller 注入）
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821

datas = [
    (os.path.join(ROOT, "assets"), "assets"),        # 应用图标（ico/png）
    (os.path.join(ROOT, "resources"), "resources"),  # SVG 线性图标（运行时染色）
]
binaries = []
hiddenimports = ["psutil", "sounddevice", "soundfile"]

for package in ("pyaudiowpatch", "pycaw", "comtypes"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# 注意：sounddevice / soundfile 是**模块**（不是 package），
# collect_dynamic_libs() 对它们只会打印 “not a package” 警告并跳过。
# 它们的 DLL 由 PyInstaller 自带的 contrib hook（hook-sounddevice.py /
# hook-soundfile.py）负责收集；构建后应能看到：
#   _internal\_sounddevice_data\portaudio-binaries\libportaudio64bit.dll
#   _internal\_soundfile_data\libsndfile_x64.dll
# 若某天 hook 失效，再显式把这两个目录加进 datas。

# 安装目录里自带一个空 audio/（运行时 paths.audio_dir() 还会再 ensure 一次）
keep = os.path.join(ROOT, "audio", ".gitkeep")
if os.path.exists(keep):
    datas.append((keep, "audio"))

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "scipy", "pandas", "IPython",
        "pytest", "setuptools", "pip",
        "comtypes.test",          # 测试包，会拖进几十个无用模块
    ],
    noarchive=False,
    optimize=0,
)

# 只保留 Windows x64 的 PortAudio：sounddevice 的 hook 会把 macOS / ARM / 32 位
# 的 DLL 一起收进来（多出约 2MB，还会让杀软多扫几个文件）。
a.binaries = [
    entry for entry in a.binaries
    if not any(mark in entry[0].lower() for mark in ("arm64", "32bit", ".dylib"))
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SoundboardStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX 容易被杀软误报，关掉
    console=False,             # ★ 无控制台窗口
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "icon.ico"),  # ★ 应用图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SoundboardStudio",
)
