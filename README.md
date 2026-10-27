# Soundboard Studio

> **Windows 音效板 + 录音工具，单窗口、纯本地。**
> 音效直接送进虚拟声卡（语音软件把它当麦克风采集），可以把物理麦克风和音效在程序内混音；
> 右侧能录麦克风 / 系统声音 / 单个应用，录完拖动波形就能裁。
>
> A local-first Windows soundboard and recorder built with Python + PySide6:
> route sound effects into a virtual cable, mix your microphone in-app, trim recordings
> on a waveform, and trigger everything with global hotkeys.

<!-- 徽章区（按需替换 owner/repo） -->
![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-3B82F6)
![python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![ui](https://img.shields.io/badge/UI-PySide6%20(Qt6)-41CD52)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

## 这个项目是什么

**音效板 + 录音工具合并成一个桌面程序**：左边管理/播放音效（输出到虚拟声卡，
供语音软件当麦克风采集），右边直接录音（麦克风 / 系统声音 / **单个应用**），
录完一键进左侧列表，无需重新扫描、无需第二个程序。

纯本地运行：不联网、不建数据库、不做云同步。界面按 Linear / Raycast 那类工具软件的
克制风格重做过：无边框自定义标题栏、单色强调、线性图标、细边框、无冗余装饰。

### 亮点

| 能力 | 说明 |
|---|---|
| 🔊 **虚拟声卡输出** | 音效写进 `CABLE Input`，OOPZ / Discord / 游戏把它当麦克风采集 |
| 🎙 **内部混音** | 麦克风 + 音效在**同一个回调**里混音后统一输出（`sd.Stream` 双工，无时钟漂移）；麦克风**永远不会**播回本地 |
| 🎧 **本地只放音效** | 开着混音也能本地听音效、却听不到自己说话（纯音效监听，不用开 Windows「侦听此设备」） |
| ✂️ **波形裁剪** | 800×400 窗口，拖手柄选区 / 拖中间平移，试听只在你本地播放，纯 `QPainter` 实现 |
| ⌨️ **全局热键** | 自建 `WH_KEYBOARD_LL` 钩子 + `RegisterHotKey` 双通道（150ms 去重），**后台/全屏也生效**；支持按住播放 |
| 🎚 **软限幅** | `x / max(1, |x|)`，多音效叠加不炸音；1024 采样块 + 10ms 淡出，不爆不卡 |
| 🧹 **干净退出** | 点 ✕ 即释放音频流、注销热键、结束进程，不留后台残留、不占设备 |

### 快速开始

```powershell
# 1) 安装依赖（Python 3.11+）
pip install -r requirements.txt

# 2) 装 VB-CABLE（虚拟声卡），管理员 PowerShell：
powershell -ExecutionPolicy Bypass -File .\tools\install_vbcable.ps1

# 3) 启动
python main.py
```

打包成免安装 exe：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\build_exe.ps1
# 产物：dist\SoundboardStudio\SoundboardStudio.exe（会保留 audio\ 与 config.json）
```

### 目录结构

```
main.py               入口：QApplication、主题、全局清理
core/                 引擎层（不依赖 Qt）
  audio_engine.py       播放 + 双工混音 + 软限幅 + 路由（虚拟声卡/仅本地）
  device_enum.py        设备枚举（sounddevice / pyaudiowpatch 两套域）
  recorder.py           三种录音模式
  capture_*.py          麦克风 / 系统回环 / 单应用回环
  clip_library.py       音效库（导入、录音落盘、sidecar、改名/音量持久化）
  hotkey_manager.py     全局热键（自建底层钩子 + RegisterHotKey 双通道）
  config.py             配置读写（音效列表以 ClipLibrary 为唯一真相）
ui/                   界面层（PySide6）
  main_window.py        单窗口 + 托盘 + 退出清理
  top_bar.py            路由 / 混音浮层 / 音量 / 互斥
  soundboard_panel.py   音效列表（自绘行、行内改名、行内绑定热键）
  recorder_panel.py     录音面板（模式分段、电平、保存）
  trimmer_dialog.py     波形裁剪窗口
  settings_dialog.py    设置
  theme.py              设计系统 token + 全局 QSS
  widgets/              自绘控件（图标按钮、滑块、开关、电平条…）
resources/icons/        21 个手写 SVG 线性图标（运行时染色）
tools/                  自检脚本、打包脚本、VB-CABLE 安装/验证脚本
```

### 技术栈

Python 3.11+ · PySide6 (Qt 6) · sounddevice（PortAudio 播放/双工）·
pyaudiowpatch（WASAPI 回环采集）· numpy · soundfile · keyboard · psutil · pycaw · PyInstaller

### 验证

```powershell
python tools\selftest.py       # 核心：混音、路由、热键、录音、裁剪峰值、配置
python tools\selftest_ui.py    # 界面（离屏）：布局、透明度、改名、热键、浮层
```

### 已知限制

* 只支持 Windows；「单个应用」模式需要 Windows 10 2004+（本机不可用时界面会禁用并说明）。
* 全局热键受 UIPI 限制：**如果目标程序以管理员身份运行，本程序也要以管理员身份运行**。
* 虚拟声卡需要单装 VB-CABLE；没装时自动回退到默认输出（本地能听、远端收不到）。

---

## 零、界面与设计系统（一句话版）

* 视觉：浅色、细边框、无阴影（卡片/弹窗才有一层极轻阴影）、单一强调色 `#3B82F6`；
  红只用于录音，绿只用于"播放中/成功"，橙只用于"热键冲突/警告"。
* 图标：`resources/icons/*.svg`（Lucide 风格，stroke 1.5），运行时按需染色，
  **界面上没有任何 emoji**。
* 控件：全部自绘（图标按钮 / 细滑块 / 开关 / 电平条 / 录音圆钮 / 胶囊分段 /
  热键徽章 / 无边框标题栏…），带 hover 与键盘焦点描边。
* 尺寸：标题栏 40 · 顶栏 48 · 底栏 32 · 右栏 340 · 行高 56；默认 1100×680，最小 1040×620。
* 细节（颜色 token 表、主题怎么切、控件怎么复用）在 **[ui/README.md](ui/README.md)**。

---

## 一、安装与运行

```powershell
cd F:\BoardSound\soundboard_studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

首次启动生成 `config.json`、`audio/`、`temp/`；日志写 `studio.log`。

| 包 | 用途 |
|---|---|
| PySide6 | 界面 |
| sounddevice | **播放**：常驻输出流 + 回调内混音 |
| soundfile | 播放解码 + 录音落盘（44100Hz/16bit） |
| **pyaudiowpatch** | **录音**：模式 1 麦克风、模式 2 系统声音 loopback |
| **pycaw** | 模式 3 的音频会话枚举（自动带 comtypes） |
| **psutil** | 进程名/路径/存活检测 + 应用图标定位 |
| keyboard | 全局热键（音效键 + 录音键） |
| numpy | 混音、裁剪、归一化、重采样 |
| pydub（可选） | 只有"另存为 MP3"需要，还要系统有 ffmpeg |

> **pyaudiowpatch 与 pyaudio 不冲突**：模块名不同（`import pyaudiowpatch`），
> 自带 PortAudio DLL，`pip install pyaudiowpatch` 即用。某个 Python 版本若没有预编译
> 轮子，装不上也不致命：录音会自动退到 `sounddevice`
> （模式 1 正常、模式 2 用 `WasapiSettings(loopback=True)`、模式 3 不受影响）。

---

## 二、让本地和远端同时听到（输出路由）

程序的顶栏有一个**路由模式**下拉，它决定了音效往哪里送：

| 路由模式 | 写到哪里 | 本地怎么听 | 远端怎么听 | 适用 |
|---|---|---|---|---|
| **虚拟声卡（推荐）** | `CABLE Input` | Windows「侦听此设备」 | 语音软件采集 `CABLE Output` | 日常开黑/直播 |
| **仅本地扬声器** | 你的扬声器 | 直接听 | **收不到** | 只自己试听 |

> **双输出模式已弃置**：它会在本地多播一路（含麦克风）导致听到自己的回音。
> 旧配置里的 `dual` 会自动按「虚拟声卡」处理。

### 方案 A：虚拟声卡 + 系统侦听（推荐）

**第 1 步：装 VB-CABLE**

1. <https://vb-audio.com/Cable/> 下载 `VBCABLE_Driver_Pack43.zip`（免费版）。
2. 解压 → 右键 **`VBCABLE_Setup_x64.exe` → 以管理员身份运行 → Install Driver**。
3. 建议重启。之后系统里会多出两块端点：
   - 播放端：`CABLE Input (VB-Audio Virtual Cable)`
   - 录音端：`CABLE Output (VB-Audio Virtual Cable)`

> **也可以一条命令装完**（脚本会下载官方包、校验 VB-Audio 数字签名、静默安装、
> 重启音频服务，最后用本项目的设备枚举确认端点真的出现）：
>
> ```powershell
> # 在「以管理员身份运行」的 PowerShell 里执行
> cd F:\BoardSound\soundboard_studio
> powershell -ExecutionPolicy Bypass -File .\tools\install_vbcable.ps1
> ```
>
> * 已经手动下好包：把 zip 放到 `%TEMP%\VBCABLE_install\`，加 `-SkipDownload`；
> * 卸载：同样命令加 `-Uninstall`；
> * 签名不是 VB-Audio 时脚本会**拒绝安装**（防中间人替换）。

**第 2 步：本程序选「虚拟声卡（推荐）」**

顶栏路由下拉 → **虚拟声卡（推荐，本地通过系统监听）**。
这时右侧会显示 `输出 → CABLE Input`，检测到设备时是**绿色对勾**，
没装 VB-CABLE 时是**橙色警告**（程序会临时回退到系统默认设备，让你至少能听到，
但远端收不到；tooltip 里写明原因）。

**第 3 步：打开 Windows 的「侦听此设备」**（这是本地能听到的关键）

1. 按 `Win+R`，输入 `control mmsys.cpl` 回车
   （或点顶栏最右边的「打开声音设置」图标按钮，程序会用同一命令打开）。
2. 切到 **「录制」** 选项卡 → 找到 **`CABLE Output`** → 双击它。
3. 切到 **「侦听」** 选项卡 → 勾选 **「侦听此设备」**。
4. **「通过此设备播放」** 选你的**耳机/扬声器**（别选 `CABLE Input`，会自激啸叫）。
5. 应用 → 确定。

**第 4 步：语音软件把输入设备选成 `CABLE Output`**

| 软件 | 位置 | 选什么 |
|---|---|---|
| Discord | 用户设置 → 语音和视频 → 输入设备 | `CABLE Output (VB-Audio Virtual Cable)` |
| YY | 设置 → 语音 → 麦克风 | 同上 |
| QQ | 设置 → 音视频通话 → 麦克风 | 同上 |
| TeamSpeak | 工具 → 选项 → 采集 | 同上 |
| OOPZ | 设置 → 语音 → 输入设备 | 同上 |

同时关掉语音软件的**降噪 / 回声消除 / 自动增益**（Discord 的 Krisp 尤其会把音效当噪音削掉）。

**第 5 步：点顶栏「测试路由」图标**

播放 2 秒测试音，底栏提示：

> 测试音已播放。本地听到 + 语音软件另一端听到 = 配置正确。

* 本地听到、对面听不到 → 语音软件的输入设备没选 `CABLE Output`；
* 对面听到、本地听不到 → 没做第 3 步的「侦听此设备」；
* 两边都没听到 → 路由下拉没选「虚拟声卡」，或 VB-CABLE 没装好（看橙色警告的 tooltip）。

### 采样率

把 `CABLE Input` 与 `CABLE Output` 的高级属性都设成 **48000Hz**（或与语音软件一致），
可减少杂音与重采样。本程序输出/落盘是 44100Hz，设备给 48000 时会自动转换。

### 程序不做什么（职责边界）

本程序**只负责把音效送进虚拟声卡**；麦克风的混入由**本程序的内部混音**负责（见下节），
不需要 VoiceMeeter。语音软件那边仍然只选 `CABLE Output`，不需要额外设置。

### 边说话边放音效（内部混音，新）

顶栏右侧的 **🎙 麦克风图标** → 弹出「内部混音」浮层：

| 控件 | 作用 |
|---|---|
| 启用混音 | 打开后：**麦克风 + 音效**在程序内部混合，一起送进 `CABLE Input` |
| 麦克风 | 用哪块麦克风（列表来自 sounddevice，和录音器的列表是两套，互不影响） |
| 麦克风音量 | 0–200%，独立于音效 |
| 音效音量 | 0–200%（等于顶栏「音量」） |
| 本地也播放音效（不含麦克风） | **推荐打开**：本地能听到音效，但听不到自己说话；不用再开 Windows 的「侦听此设备」 |

> **麦克风永远不会在本地播放**：数据只有一条路径 `麦克风 → 混音 → 输出（虚拟声卡）`。
> 「本地也播放音效」这一路取的是**加麦克风之前**的那份数据（纯音效），所以
> 戴着耳机只会听到音效、不会听到自己的回音。

技术细节：

* 用 `sd.Stream` **双工流**：同一个 callback 里读麦克风 + 混音效 + 写虚拟声卡，
  输入输出同采样率/同块大小，**没有时钟漂移**；
* 双工打不开时自动降级为"两条流 + 环形缓冲"，底栏会提示可能有轻微延迟；
* 混音后做**软限幅** `x / max(1, |x|)`（不是直接 `clip` 截断，避免叠加时变成刺耳的方波）；
  连续约 0.23 秒触顶会在底栏提示 **「音量过大：混音已限幅」**，建议降增益；
* 块大小 1024 采样（≈21ms@48k），给 CPU 留足余量；互斥切歌用 10ms 淡出，不会"啪"；
* 麦克风被拔/被独占 → 自动回退"仅音效"，不会静音也不会崩。

> 典型用法：OOPZ 输入设备选 `CABLE Output`；本程序开混音、麦克风选你的物理麦。
> 这样对面同时听到你说话和音效，而你本地什么都不用改。

---

## 三、三种录音模式

| 模式 | 录什么 | 目标列表 | 适用场景 |
|---|---|---|---|
| 🎤 **麦克风** | 输入设备（物理麦克风 / 虚拟声卡输入 / 立体声混音） | 输入设备（分组） | 录人声、录"已经混好的"虚拟声卡 |
| 🔊 **系统声音** | **某个输出设备上所有应用**的混音 | loopback 输出设备 | 录游戏/视频/网页整体声音 |
| 🎯 **单个应用** | **只录某一个进程**的声音，不录其他声音 | 正在播放声音的应用 | 只录某个游戏/播放器/浏览器标签的音频 |

* 模式 2 会**明确提示**"会录入该设备上所有应用的声音"（右上提示行）。
* 模式 3 由 Windows **进程级 loopback** 实现，天然不会录入麦克风或其他应用的声音。
  它要求 **Windows 10 20H1（build 19041）及以上**；低版本或系统拒绝时，
  🎯 按钮会置灰、tooltip 与设置区写明原因，模式 1/2 照常可用。
* 录音中**不能切换模式/目标**（控件禁用），避免中途换源产生异常样本。
* 三种模式的输出统一是 **44100Hz / 16bit**；设备只给 48000 时自动转换。

录音完成后（右栏内）：试听 → 命名 → **✂ 裁剪（波形窗口）** → 保存到音效板。

裁剪窗口是一个 800×400 的独立窗口：

* 波形按像素列降采样绘制（100 万采样的录音也能在几十毫秒内画出来，长音频在后台线程算峰值）；
* 左右两侧暗色遮罩 = 会被裁掉的部分，中间高亮选区 = 保留；
* 拖两端手柄改起止（光标变 ↔），拖选区内部整体平移（光标变 ✥）；
* 底部实时显示 `开始 / 结束 / 保留`（`mm:ss.cc`，等宽数字不跳动）；
* 「试听当前选区」只在你本地播放，**不会**送进虚拟声卡；
* 「确认裁剪」直接替换内存里的录音数据，之后点「保存到音效板」才落盘。

---

## 四、音效板（左栏）

* **导入**：工具栏 `导入` 按钮（可多选）、空状态里的「导入文件」按钮、
  把文件/文件夹**拖进窗口**（拖拽时列表区会出现虚线提示层），或手工放进音效目录
  后点 `⟳ 刷新目录`。导入会把文件**复制**进音效目录。
* **每行**（高 56）：来源色点（录音红 / 导入灰）· 名称（双击行内改名）·
  快捷键徽章（点击即按键绑定）· 试听 · 细音量滑块（0–200%）·
  **改名按钮（铅笔图标）** · 删除。
  点铅笔或双击名称都能就地改名，回车确认、失焦也算确认；改完立刻写回 `config.json`。
* **搜索**：条目超过 15 时，工具栏右侧自动出现搜索框（按名称过滤；清空即恢复）。
* **播放**：多音效可同时混音；顶栏 **互斥模式** 开关打开后新音效打断上一个。
  正在播放的行左侧有 3px 强调色竖条、试听按钮变绿色停止图标。
* 删除会同时删掉音效目录里的文件（`rec_*` 还会删同名 `.json`），删除前有确认框。

### 全局热键

* 支持单键（`F1`~`F24`、小键盘数字）与组合键（`Ctrl+Alt+1`）。
* **音效键与录音键统一分配**，冲突规则：
  1. 音效键与音效键重复 → 只有第一条生效，其余徽章**红框 + 橙字**并给出原因；
  2. 音效键与**录音键**（默认 `F9`）重复 → **录音键优先**，音效键徽章红框 + 橙字
     "与录音快捷键重复"；
  3. 组合被**其他程序**占用 → 红框 + 橙字"被其他程序占用"，并自动改用键盘钩子接收
     （仍能用，但对方程序可能同时响应；tooltip 会注明"走钩子"）；
  4. 按压式绑定（需要 key-up）的音效走键盘钩子。
* 绑定是**行内**的：点徽章 → 显示「按下按键…」→ 直接按组合键即可；
  `Esc` 取消，`Backspace / Delete` 清除。绑定期间全局热键会临时挂起，避免自己抢自己。

**后台也能用（双通道）**：全局热键由**程序自己安装的底层键盘钩子**
（`WH_KEYBOARD_LL`）负责，它跟窗口焦点无关；同时给"带修饰键的组合"再注册一份
`RegisterHotKey` 作为第二保险，两条通道 150ms 内去重，不会重复触发。
纯按键（`F9`、字母）只走钩子——避免 `RegisterHotKey` 把按键从语音软件手里抢走。

**管理员权限（重要）**：底层钩子受 Windows UIPI 限制——**如果目标窗口（OOPZ、游戏、
Discord）是以管理员身份运行的，本程序也必须以管理员身份运行**，否则钩子收不到它的按键，
表现就是"前台能用、后台没反应"。少数反作弊游戏会直接屏蔽底层钩子，属游戏侧限制。

> 排查入口（`studio.log`）：
> * 启动时一行 `全局热键通道：自建底层钩子 + RegisterHotKey 双通道（后台可用）`
>   —— 说明钩子装上了；
> * 每次触发一行 `热键触发：<clip-id>` —— 说明**系统确实把按键交给了程序**。
>
> 若在后台按键后日志里**没有** `热键触发`，就是权限/UIPI 或被其他软件拦截；
> 若有这行但没声音，则是输出路由问题（看底栏提示）。

### 键盘操作

| 快捷键 | 功能 |
|---|---|
| `F9` | 开始 / 停止录音（在设置里可改为其他键） |
| `Esc` | 取消正在进行的快捷键绑定 |
| `Backspace / Delete` | 绑定态下清除该条快捷键 |
| `Tab` / `Shift+Tab` | 在可交互控件间移动焦点（焦点有 2px 强调色描边） |
| `Space` / `Enter` | 激活当前聚焦的按钮 / 开关 / 分段项 |
| `双击名称` | 行内改名（回车确认，失焦也算确认） |
| `双击标题栏` | 最大化 / 还原 |

> **鼠标滚轮不会误改下拉框**：只有点了下拉、它拿到焦点之后，滚轮才会切换选项；
> 其它时候滚轮照常滚动页面。下拉弹层使用浅色底 + 深色字，避免看不清。

### 托盘与退出

* **`—` 最小化 = 缩到任务栏**（保留任务栏按钮，点一下就能还原，录音不中断）。
  不会再把窗口藏进托盘——以前那样会让任务栏里"找不到程序"。
* **点窗口的 ✕ = 真正退出**：释放音频流、注销全局热键、隐藏托盘图标、结束进程
  （任务管理器里不会残留，再次启动也不会遇到"设备被占用"）。
* 托盘图标提供 **显示窗口 · 开始录音 · 停止录音 · 退出**；想彻底收进托盘时用托盘菜单的
  「隐藏窗口」（录音与全局快捷键继续工作）。
* 进程设置了显式 **AppUserModelID**（`Jokldgd.SoundboardStudio`），任务栏 / 开始菜单
  会把它当成同一个应用，图标不会显示成空白块，固定到任务栏也不会分裂成两个条目。

---

## 五、设备列表怎么读

* **分组**：`🔊 虚拟声卡` / `🎤 麦克风` / `🔊 物理扬声器` / `🖥️ 其他`，
  最近用过的 3 个设备置顶（`★ 最近使用`）。
* **名称清洗**：`Microphone (C-Media(R) Audio)` → `Microphone`；
  `扬声器 (Steam Streaming Speakers) [Loopback]` → `扬声器 (Steam Streaming Speakers)`。
  完整原名 + 驱动接口 + 声道/采样率在 **tooltip**。
  规则在 **设置 → 设备名清洗** 里用关键词标签维护（写入 `config.json → device_name_rules`，
  不硬编码；标签可增删，回车添加、点标签移除）。
* **显示全部设备**：默认只显示可用的 **WASAPI** 设备，一台设备一行（MME/DirectSound 的
  重复条目、`声音映射器`、`主声音捕获驱动程序` 这类旧别名都隐藏了）。
  需要排查老声卡/旧接口条目时，去 **设置 → 设备名清洗** 勾上"显示全部设备"。
* **应用列表（模式 3）**：只列 `Active` 会话，按 PID 去重，显示 `Chrome · PID 1234`；
  同名多进程各占一行；空列表提示"当前没有正在播放声音的应用，点击刷新"。
  拿不到精确会话的 UWP/权限受限应用可按进程名**推断**列出（可在设置里关掉）。

> 设备分组标题里的 emoji（core 内部标记）在显示前会被剥掉，界面统一用 SVG 图标。

---

## 六、配置与数据

```
config.json   播放设备/总音量/互斥、录音模式与目标、最近设备、快捷键、
              保存目录（save_dir）、设备清洗规则、音效列表（clips）
audio/        默认音效目录（可在设置里改成任意目录）
temp/         录音中间产物（rec_last.wav 兜底）
studio.log    运行日志（排查问题先看它）
resources/    SVG 图标（ui/icons.py 运行时染色加载）
```

录音落盘约定：

```
audio/rec_20261026_215651.wav     ← 44100Hz / 16bit / PCM
audio/rec_20261026_215651.json    ← {"name": "你起的名字", "volume": 1.0}
```

* 文件名严格 `rec_YYYYMMDD_HHMMSS.wav`，同一秒撞车加 `_1`，绝不覆盖。
* **保存目录可改**：设置 → 录音 → 保存路径（留空 = 程序目录下的 `audio/`）。
  改完后新录音/导入都会落到新目录，旧目录里的条目仍可正常播放（路径按文件记录）。
* 启动扫描音效目录时：没有登记的音频自动收进列表（`rec_` 开头记为"录音"来源），
  已登记的条目若同名 `.json` 存在，用其中的 `name`/`volume` 覆盖。
* 配置是**原子写 + 300ms 防抖**；损坏会备份成 `config.broken-<时间戳>.json` 并重置。

---

## 七、打包成 exe

```powershell
cd F:\BoardSound\soundboard_studio
pip install pyinstaller

# 1) 生成图标
python tools\make_icon.py

# 2) 打包
pyinstaller --noconfirm --clean --windowed --name "SoundboardStudio" ^
  --icon "assets\icon.ico" ^
  --add-data "assets;assets" ^
  --add-data "resources;resources" ^
  --collect-all pyaudiowpatch ^
  --collect-all pycaw ^
  --collect-all comtypes ^
  --collect-binaries sounddevice ^
  --collect-binaries soundfile ^
  --collect-data soundfile ^
  --hidden-import psutil ^
  main.py
```

说明：

* **`--collect-all pyaudiowpatch` 必加**：它自带 PortAudio DLL，不收集的话
  打包后一开录音就报"找不到 portaudio"。
* `pycaw`/`comtypes` 运行时动态生成 COM 接口，必须 `--collect-all`，否则模式 3
  的应用列表是空的。
* 产物 `dist\SoundboardStudio\SoundboardStudio.exe`；`config.json` / `audio/` /
  `temp/` / `studio.log` 都在 exe 同目录，整个文件夹可直接拷给别人。
* **诊断模式**（不弹窗，把三种模式所需能力全跑一遍写日志）：

  ```powershell
  $env:STUDIO_PROBE = "1"
  .\dist\SoundboardStudio\SoundboardStudio.exe
  Get-Content .\studio.log -Tail 20
  ```

  输出：录音后端、播放设备、模式 1 输入、模式 2 loopback、模式 3 会话枚举与
  进程 loopback 是否可用（含失败原因）、音效目录/条目数。
  排查"🎯 为什么是灰的"时把这段日志发出来即可。
* **进程 loopback 深度诊断**（区分"代码问题"还是"系统拒绝"，换机排查用这个）：

  ```powershell
  python tools\diag_process_loopback.py          # 目标 = 自己
  python tools\diag_process_loopback.py 12345    # 目标 = 指定 PID
  ```

  它依次验证：系统版本/导出函数 → **经典激活**（`IMMDevice::Activate`，验证
  COM 封装与音频栈）→ **异步 Default 激活** → **异步进程 loopback 激活** → 实采 1 秒，
  最后给出判定。若"经典激活成功、异步激活失败"，就是系统侧拒绝了
  `ActivateAudioInterfaceAsync`，与本项目无关。
* 验证 exe 能启动（窗口程序无控制台）：
  ```powershell
  $env:STUDIO_EXIT_AFTER_MS = "3000"; .\dist\SoundboardStudio\SoundboardStudio.exe
  ```
  退出码 0 即正常。

---

## 八、常见问题

**Q1：选了 CABLE Input 后自己听不到？**
正常，声音进了虚拟声卡。要自己听：系统 → 录制 → `CABLE Output` → 侦听 → 耳机。

**Q2：对面听不到音效？**
按顺序查：本程序输出设备是否 `CABLE Input` → 语音软件麦克风是否 `CABLE Output` →
各处音量 → 语音软件降噪/回声消除是否把音效滤掉（关掉试）。

**Q3：🎯 单个应用按钮是灰的？**
本机进程 loopback 不可用。设置区会写原因：
- `需要 Windows 10 20H1（build 19041）及以上` → 升级系统，或改用模式 1/2；
- `激活进程 loopback 失败：0x8xxxxxxx` → 系统/驱动拒绝（点「重新检测」可重试）。
模式 1/2 不受影响；想只录一个应用可以退而求其次：让该应用输出到虚拟声卡，
再用模式 1 录那块虚拟声卡。

**Q4：模式 3 录出来是静音？**
1. 该应用其实没出声/被静音（界面会提示"超过 2 秒没有录到声音"）；
2. **DRM 保护内容**（Netflix、部分商店应用）系统不允许采集；
3. **UWP/商店应用**音频由 `audiodg` 代理，部分抓不到；
4. 目标用**独占模式**输出。碰到 2/3/4 请改用模式 2 或模式 1 + 虚拟声卡。

**Q5：模式 2 列表空 / 打不开？**
`pyaudiowpatch` 不可用时会自动退到 sounddevice 的 loopback；两者都失败会提示
"当前无法录系统声音"，这时用模式 1 录虚拟声卡（VB-CABLE / 立体声混音）。

**Q6：热键不生效或冲突？**
先看那一行按钮颜色：红色=冲突/被占用（tooltip 有原因），黄色=走键盘钩子。
被占用时换一个组合；游戏里无效请以管理员身份运行本程序。

**Q7：录音/播放会不会互相抢设备？**
不会。播放走 sounddevice、录音走 pyaudiowpatch（两套 PortAudio，都用 WASAPI
**共享模式**），可以同时占用同一块声卡。若某设备被其他程序**独占**，开流会失败并提示。
注意：录音目标与播放设备相同时，界面会提示"会把本程序播放的声音也录进去"。

**Q8：导入提示"这个文件已经在列表里了"？**
同名且同大小的文件视为重复导入；确实要两份就改个名字再导入。

---

## 九、已知限制

1. 不做多轨、不做变声/混响等滤镜；裁剪是拖拽选区的波形裁剪（掐头去尾），不是波形编辑。
2. 模式 3 依赖 Windows 10 20H1+；UWP、DRM、独占模式应用可能录不到。
3. 模式 2 不做隔离，录的是整个输出设备的混音。
4. 全局热键受 UIPI 限制（对被提权程序无效）；部分反作弊游戏会屏蔽。
5. 热键总数受 Windows `RegisterHotKey` 限制（实际远超日常需求）。
6. 采样率转换用线性插值（无额外依赖），语音/音效场景足够。
7. 只支持「虚拟声卡」与「仅本地」两种路由（双输出因本地回音问题已移除）。
8. 内部混音会把麦克风混进**虚拟声卡**输出；麦克风永远不会播到本地扬声器。
   要"边说话边放音效"，打开顶栏 🎙 混音即可，不需要 VoiceMeeter。
9. 虚拟声卡模式依赖 Windows「侦听此设备」；不装 VB-CABLE 时会自动回退到系统
   默认输出（本地能听到，但远端收不到），顶栏会显示橙色警告。

---

## 十、目录结构

```
soundboard_studio/
├── main.py                 入口（日志/样式/异常兜底/STUDIO_PROBE 诊断）
├── core/                   —— 业务逻辑，本次 UI 重构未改动（仅新增 save_dir 字段）
│   ├── constants.py        全局常量（采样率、模式、来源…）
│   ├── paths.py            项目内路径
│   ├── config.py           配置（播放+录音+保存目录+音效列表，统一存 config.json）
│   ├── clip_library.py     音效数据模型 + 导入/扫描/落盘 + sidecar json
│   ├── audio_engine.py     统一音频引擎：设备真源 + 播放混音 + 输出路由（单路/双路）
│   ├── recorder.py         录音门面（按模式分派 + 汇聚 + 重采样）
│   ├── capture_mic.py      模式 1
│   ├── capture_system.py   模式 2（pyaudiowpatch loopback）
│   ├── capture_app.py      模式 3（含能力探测/进程存活监控）
│   ├── app_loopback.py     进程 loopback 的 ctypes 封装
│   ├── device_enum.py      设备枚举 + 分组 + 名称清洗 + 显示全部
│   ├── app_enum.py         音频会话/进程枚举
│   ├── hotkey_manager.py   统一热键（音效键 + 录音键 + 冲突检测）
│   ├── decoder.py          播放解码链 + LRU 缓存
│   └── audio_io.py         读写/裁剪/归一化/重采样/可选 MP3
├── ui/                     —— 本次重构的部分（详见 ui/README.md）
│   ├── theme.py            设计系统：颜色/间距/圆角/字体/尺寸/动效 + 全局 QSS
│   ├── icons.py            SVG 图标加载与染色（含打包路径处理）
│   ├── icon.py             应用图标（结构化绘制 + 录音态变体 + .ico 导出）
│   ├── text.py             文案处理（剥掉 core 文案里的 emoji）
│   ├── bridge.py           后台线程 → 主线程信号桥
│   ├── main_window.py      无边框外壳装配（只做粘合）
│   ├── top_bar.py          顶栏（路由模式 / 设备状态 / 测试音 / 总音量 / 互斥 / 设置）
│   ├── status_bar.py       底栏（状态点 / 正在播放 / 当前模式）
│   ├── soundboard_panel.py 左栏（工具栏 / 列表行 / 空状态 / 拖拽层）
│   ├── recorder_panel.py   右栏（分段模式 / 目标 / 电平 / 录音钮 / 操作卡片）
│   ├── board_controller.py 左栏交互控制器（导入/改名/删除/热键/试听）
│   ├── settings_dialog.py  设置（录音 / 快捷键 / 设备名清洗三组 + 标签输入）
│   ├── tray.py             托盘（显示窗口 / 开始录音 / 停止录音 / 退出）
│   ├── trimmer_dialog.py   波形裁剪窗口（PeakWorker / WaveformCanvas / TrimmerDialog）
│   └── widgets/            自绘控件：icon_button、thin_slider、toggle、spinner、
│                           level_meter、record_button、segmented、hotkey_badge、
│                           title_bar、empty_state、drop_overlay、search_box、trim_dialog
├── resources/icons/        16 个 Lucide 风格 SVG（stroke 1.5 / currentColor）
├── tools/
│   ├── selftest.py         核心自检（不开界面）
│   ├── make_icon.py        生成 assets/icon.ico
│   ├── diag_process_loopback.py  进程 loopback 深度诊断
│   └── test_player.py      测试用声音发生器（正弦 / "咕咕嘎嘎" / 任意 wav）
├── audio/  temp/  config.json  requirements.txt  README.md
```

### 自检

```powershell
python tools\selftest.py                 # 核心层（不开界面）
$env:QT_QPA_PLATFORM = "offscreen"
python tools\selftest_ui.py              # 界面层（离屏）
```

`selftest.py` 覆盖：配置读写与坏配置自愈、音效库（导入/扫描/sidecar/删除/热键冲突）、
设备枚举（播放/输入/loopback 的分组与清洗）、播放引擎（混音/互斥/松开停止/
设备冲突提示）、录音（模式 1 真录、模式 2 开流、模式 3 能力探测与明确报错）、
统一热键（录音键优先、重复标记、钩子按住/松开）。只用默认设备录很短，
不会往音效目录里塞东西。

`selftest_ui.py` 覆盖：无边框窗口的 8 方向边缘命中与 resize 光标、窗口级鼠标事件
（回归"每次点击都抛 `TypeError`"的坑）、全部控件离屏绘制无异常、导入→录音→保存链路、
底栏错误提示可恢复、界面无 emoji。它不需要真实显示器，CI 里也能跑。

测试音源（调试用）：

```powershell
python tools\test_player.py tone 440 0.25 5      # 纯正弦
python tools\test_player.py bubble 0.4 5         # "咕咕嘎嘎"（默认 soft 柔和音色）
python tools\test_player.py bubble 0.4 5 crisp   # 旧版偏硬音色（A/B 对比用）
python tools\test_player.py wav some.wav 5       # 循环任意 wav
python tools\test_player.py save out.wav 3 soft  # 只生成 wav 不播放
```

`soft` 与 `crisp` 的差别（都在 `tools/test_player.py` 的 `PRESETS` 里，可自己调）：

| 参数 | soft（默认） | crisp（旧版） | 含义 |
|---|---|---|---|
| `f0` | 165 Hz | 180 Hz | 基频，低一点更沉、更柔 |
| `attack` / `release` | 40 / 90 ms | 15 / 30 ms | 起落时长，越长越不生硬 |
| `rolloff` | 1/k^0.62 | 1/k^0.35 | 谐波衰减，越大高频越少 |
| `breath` + `breath_taps` | 0.03 / 15 抽头低通 | 无 | 起音送气（被压到 1kHz 以下，不会变"嘶"声） |
| `vibrato_depth` | 0.9% | 3% | 基频颤动，越小越稳 |
| `gap` | 60 ms | 40 ms | 音节间隔，越长越从容 |

想要更"闷"一点：把 `rolloff` 调到 0.7~0.8；想更"亮"：降到 0.4。
