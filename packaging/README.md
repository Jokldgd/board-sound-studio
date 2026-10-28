# 打包与安装程序（PyInstaller + Inno Setup）

把 Soundboard Studio 打成 **绿色 exe 目录**，再封装成**标准 Windows 安装向导**
（欢迎 → 许可协议 → 选择路径 → 附加任务 → 安装中 → 完成）。

## 目录约定

```
soundboard_studio/
├─ packaging/
│  ├─ SoundboardStudio.spec        # PyInstaller 配置（无控制台 + 图标 + 音频依赖）
│  ├─ setup.iss                    # Inno Setup 安装脚本
│  ├─ LICENSE.txt                  # 许可协议页内容（可按需替换）
│  ├─ Languages/
│  │  └─ ChineseSimplified.isl     # 简体中文语言包（Inno 6.5 官方分支）
│  └─ redist/
│     └─ VBCABLE_Setup_x64.exe     # 不提交到 git，由脚本下载/校验后放入
├─ dist/SoundboardStudio/          # PyInstaller 产物（exe + _internal）
├─ installer/                      # 最终 Setup.exe 输出目录
└─ tools/
   ├─ build_exe.ps1                # 只出绿色版
   └─ build_installer.ps1          # 绿色版 + VB-CABLE + Inno 一条龙
```

## 一次性准备

1. **Inno Setup 6.5+**（中文语言文件来自 6.5 分支）
   ```powershell
   winget install --id JRSoftware.InnoSetup -e
   # 或 https://jrsoftware.org/isdl.php
   ```
2. **VB-CABLE 安装包**（可选：脚本会自动下载并校验签名）
   - 官方：<https://vb-audio.com/Cable/> → `VBCABLE_Driver_Pack43.zip`
   - 或手动把 zip 交给脚本：`-CableZip D:\VBCABLE_Driver_Pack43.zip`
   - 脚本会用 Authenticode 校验发布者为 `VB-Audio / Vincent Burel`，不符直接拒绝打包。

## 一条命令出安装包

```powershell
cd F:\BoardSound\soundboard_studio
powershell -ExecutionPolicy Bypass -File .\tools\build_installer.ps1
```

脚本做三件事：

1. `PyInstaller packaging\SoundboardStudio.spec` → `dist\SoundboardStudio\`
   （构建前把 `config.json` / `audio\` / `studio.log` 备份，构建后还原，不会删用户录音）；
2. 准备 `packaging\redist\VBCABLE_Setup_x64.exe`（下载/解压/验签）；
3. `ISCC.exe packaging\setup.iss` → `installer\SoundboardStudio-Setup-1.0.0.exe`。

只要绿色版（不编译安装包）：`.\tools\build_exe.ps1`。

## 安装向导包含什么

| 页面 | 内容 |
|---|---|
| 欢迎 | 标准 Inno 欢迎页（可加 `InfoBeforeFile` 放说明） |
| 许可协议 | `packaging\LICENSE.txt`（MIT，可换成你自己的协议） |
| 选择目录 | 默认 `C:\Program Files\Soundboard Studio`，**可自定义** |
| 附加任务 | ☐ 桌面快捷方式 · ☐ 开机自启（`HKCU\...\Run`，卸载自动清理）· ☑ **安装 VB-CABLE**（已装则自动取消并提示） |
| 安装中 | 进度条 + 状态文字（"正在安装 VB-CABLE 虚拟声卡…"） |
| 完成 | ☑ 立即运行 Soundboard Studio |

## VB-CABLE 相关的实现细节

### 检测是否已安装（Pascal）

读 Windows 音频端点注册表，找名称含 `CABLE Input` 的播放设备：

```pascal
MMDEVICES_RENDER = 'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render';
DEVICE_FRIENDLY_NAME = '{a45c254e-df1c-4efd-8020-67d146a850e0},2';
```

枚举子键 → 读友好名称 → `Pos('CABLE INPUT', Uppercase(Value)) > 0`；
兜底再查 VB-CABLE 的卸载登记项。命中后：

* 在「附加任务」页**自动取消勾选** VB-CABLE；
* 弹一次提示"已检测到虚拟声卡，无需重复安装"；
* `[Run]` 的 `Check: NeedCableInstall` 也会再拦一次，避免重复跑驱动安装器。

### 静默安装参数

```ini
Filename: "{tmp}\VBCABLE_Setup_x64.exe"; Parameters: "-i -h"
```

* `-i` = install，`-h` = 隐藏界面（官方安装器支持；若你的版本仍弹窗，删掉 `-h` 即可）；
* 安装器已复制到 `{tmp}`，`deleteafterinstall` 装完自动删；
* 本安装程序以管理员运行（`PrivilegesRequired=admin`），驱动安装会继承权限；
* 驱动装完**建议重启**，完成页会提示（端点要重启后才出现在系统里）。

## 数据与权限

* 程序把 `config.json`、`studio.log`、`audio\` 写在**安装目录**里；
  安装到 `Program Files` 后普通用户默认不可写，所以脚本给安装目录加了
  `Permissions: users-modify`（`[Dirs]` 段）。
* 卸载**不会删除** `audio\` 里的录音和 `config.json`（Inno 只删自己装的文件；
  目录非空时会保留）。
* 想改成"每用户安装（无需管理员）"：把 `PrivilegesRequired=admin` 改成 `lowest`，
  `DefaultDirName` 改成 `{localappdata}\Programs\Soundboard Studio` 即可。

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 装完没声音 | `sounddevice` 的 PortAudio DLL 没被打进去。确认 spec 里有 `collect_dynamic_libs("sounddevice")`，且 `dist\SoundboardStudio\_internal\_sounddevice_data\` 存在 |
| 录音模式 2/3 打不开 | `pyaudiowpatch` 没 `collect_all`；检查 `_internal\pyaudiowpatch\` 和其中的 PortAudio DLL |
| 任务栏图标是空白 | 确认 `assets\icon.ico` 含多尺寸（16–256），并且程序启动时设置了 AppUserModelID（已内置） |
| 杀软报毒 | PyInstaller 单文件/UPX 高发。本项目用 **onedir + 不开 UPX**；仍报毒就给 exe 做代码签名（见下） |
| 驱动安装被拦 | 必须管理员；企业环境可能禁装驱动，可让用户手动装 VB-CABLE |
| 编译报 `Unknown message name` | 中文 isl 与 Inno 版本不匹配：用 **6.5+**，或删掉 `[Languages]` 里的中文行回退英文 |

## 代码签名（可选，减少 SmartScreen 警告）

```powershell
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 `
  dist\SoundboardStudio\SoundboardStudio.exe
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 `
  installer\SoundboardStudio-Setup-1.0.0.exe
```

没有证书时，用户首次运行会看到 SmartScreen「更多信息 → 仍要运行」，属正常现象。

## 版本号

改两处即可：`packaging/setup.iss` 的 `#define AppVersion`，以及
`packaging/SoundboardStudio.spec`（如需写进 exe 版本信息，在 `EXE(...)` 里加
`version=` 指向一个 version 资源文件）。
