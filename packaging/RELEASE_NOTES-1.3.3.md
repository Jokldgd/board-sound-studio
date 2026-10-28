# Soundboard Studio 1.3.3

Windows 音效板 + 录音工具：音效送进虚拟声卡（语音软件当麦克风采集），程序内混音麦克风，
右侧录麦克风 / 系统声音 / 单个应用，录完可拖波形裁剪。

## 安装

1. 下载 `SoundboardStudio-Setup-1.3.3.exe`
2. 运行安装包（如遇「错误 5 / 无法创建临时目录」，请改用 Release 里的 `安装.cmd` 启动）
3. 附加任务保持「安装 VB-CABLE 虚拟声卡」勾选；装完提示重启就重启一次
4. 语音软件的麦克风选择 `CABLE Output (VB-Audio Virtual Cable)`

## 1.3.3 更新内容

- **数据与程序同级**：录音/音效在安装目录 `audio\`，配置 `config.json`、临时文件 `temp\`、日志 `studio.log`
  - 安装器给 `audio\`、`temp\` 加 `users-modify`，普通用户可写
  - 升级安装排除 `audio\`、`temp\`、`config.json`、`studio.log`，不会覆盖用户数据
  - 卸载时询问「是否保留录音/音效/配置」，默认保留
- **更新流程**：更新器先把 `audio\` + `config.json` 压成 zip 备份到
  `%LOCALAPPDATA%\SoundboardStudio-backup\`，复制新程序后再解压还原
- **音效打包**：音效列表新增「压缩」按钮，勾选后生成 zip（含 `manifest.json` + 音频 + 同名 `.json`）
- **导入支持 zip**：文件对话框与拖拽都支持压缩包，名称/音量/模式随包还原
- 日志多路径回退（APPDATA → LOCALAPPDATA → TEMP），录音开始/结束/保存都有日志
- 高 DPI：`PassThrough` 缩放策略、pt 字体、900×600 最小窗口、340–400 侧边栏、对话框按父窗口居中
- 安装包内置完整 VB-CABLE 驱动包（inf/sys/cat），修复旧版 "Missing 'inf' file"

## 数据位置

```
安装目录\
├─ SoundboardStudio.exe
├─ audio\        录音与导入的音效
├─ temp\         未保存的临时录音 rec_last.wav
├─ config.json   配置（音效列表/名称/音量/快捷键）
└─ studio.log    日志
```

## 卸载

卸载时选择「是」保留 `audio\` 与 `config.json`（推荐）；选「否」并二次确认才会删除。

## 已知说明

- 未做代码签名，SmartScreen/杀软可能提示“未知发布者”，请选择“仍要运行”或加白名单；
- Windows「受控文件夹访问」若拦截写入，程序会自动降级到 `%LOCALAPPDATA%` 并弹窗说明；
- VB-CABLE 为 VB-Audio 免费驱动，版权归其作者所有。

SHA-256: `D1416B3D5BD0889C81B3D52F6143B7DDE88FA3F061AEA16FDFED63232870E170`
