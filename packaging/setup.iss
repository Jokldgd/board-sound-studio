; ============================================================================
; Soundboard Studio —— Inno Setup 安装脚本
;
; 要求：Inno Setup 6.5+（语言文件 Languages\ChineseSimplified.isl 来自 6.5 分支）
; 编译：ISCC.exe packaging\setup.iss
;  或：powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1
;
; 产物：installer\SoundboardStudio-Setup-<版本>.exe
; ============================================================================

#define AppName        "Soundboard Studio"
#define AppVersion     "1.0.0"
#define AppPublisher   "Jokldgd"
#define AppURL         "https://github.com/Jokldgd/board-sound-studio"
#define AppExeName     "SoundboardStudio.exe"
#define SourceDir      "..\dist\SoundboardStudio"
#define CableSetup     "redist\VBCABLE_Setup_x64.exe"
#define CableInstaller "VBCABLE_Setup_x64.exe"

[Setup]
AppId={{8E3F1C62-5B4A-4D2E-9A17-6C0D5E8B7F31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} 安装程序
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; 允许用户自定义安装路径（这是默认行为；这里显式写出来，方便一眼确认）
DisableDirPage=no
DisableProgramGroupPage=yes
; 许可协议页
LicenseFile=LICENSE.txt
; 输出
OutputDir=..\installer
OutputBaseFilename=SoundboardStudio-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 需要管理员：写 Program Files + 安装音频驱动
PrivilegesRequired=admin
; 只支持 64 位（Python/PySide6 打包产物是 x64）
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 安装时关闭正在运行的旧实例
CloseApplications=yes
RestartApplications=no
DirExistsWarning=auto
UsePreviousAppDir=yes
AllowNoIcons=yes

[Languages]
; 中文语言文件随仓库提供（packaging\Languages\ChineseSimplified.isl）
#if FileExists(SourcePath + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "Languages\ChineseSimplified.isl"
#else
#pragma message "警告：缺少 Languages\ChineseSimplified.isl，将只提供英文界面"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart";   Description: "开机自动启动 {#AppName}"; GroupDescription: "附加任务:"
Name: "vbcable";     Description: "安装 VB-CABLE 虚拟声卡（推荐，未安装时）"; GroupDescription: "虚拟声卡:"

[Files]
; 主程序：PyInstaller onedir 产物（exe + _internal 依赖）
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; VB-CABLE 安装器：复制到临时目录，装完自动删除（不污染安装目录）
Source: "{#CableSetup}"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Dirs]
; 程序要写 config.json / studio.log / audio\ 里的录音，给普通用户写权限
Name: "{app}";        Permissions: users-modify
Name: "{app}\audio";  Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}";          Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}";      Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";    Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; 开机自启（当前用户；卸载时自动清理）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "SoundboardStudio"; \
  ValueData: """{app}\{#AppExeName}"""; \
  Flags: uninsdeletevalue; Tasks: autostart

[Run]
; VB-CABLE：官方安装器支持 -i（安装） -h（隐藏界面）实现静默安装；
; 需要管理员权限，本安装程序已是 admin，子进程会继承。
Filename: "{tmp}\{#CableInstaller}"; Parameters: "-i -h"; \
  StatusMsg: "正在安装 VB-CABLE 虚拟声卡（约 30–60 秒，期间屏幕可能闪烁）…"; \
  Flags: waituntilterminated; Check: NeedCableInstall

; 完成页：立即运行
Filename: "{app}\{#AppExeName}"; \
  Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[Code]
const
  MMDEVICES_RENDER = 'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render';
  // 设备"友好名称"属性键（Windows 音频端点标准属性）
  DEVICE_FRIENDLY_NAME = '{a45c254e-df1c-4efd-8020-67d146a850e0},2';

var
  CableDetected: Boolean;
  CableNoticeShown: Boolean;

{ 检测系统里是否已有 "CABLE Input" 播放端点 }
function HasCableInput(): Boolean;
var
  Names: TArrayOfString;
  I: Integer;
  Value: String;
begin
  Result := False;
  if not RegGetSubkeyNames(HKLM, MMDEVICES_RENDER, Names) then
    Exit;
  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    if RegQueryStringValue(HKLM, MMDEVICES_RENDER + '\' + Names[I],
                           DEVICE_FRIENDLY_NAME, Value) then
    begin
      if Pos('CABLE INPUT', Uppercase(Value)) > 0 then
      begin
        Result := True;
        Exit;
      end;
    end;
  end;
  { 兜底：VB-CABLE 的卸载登记项 }
  if RegKeyExists(HKLM,
       'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\VB-Audio Virtual Cable') then
    Result := True;
end;

{ 在"附加任务"页把包含 Hint 的勾选项取消勾选（保持可手动再勾） }
procedure UncheckTaskByHint(const Hint: String);
var
  I: Integer;
begin
  for I := 0 to WizardForm.TasksList.Items.Count - 1 do
  begin
    if Pos(Uppercase(Hint), Uppercase(WizardForm.TasksList.Items[I])) > 0 then
    begin
      WizardForm.TasksList.Checked[I] := False;
      Exit;
    end;
  end;
end;

procedure InitializeWizard();
begin
  CableDetected := HasCableInput();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then
  begin
    if CableDetected then
    begin
      UncheckTaskByHint('VB-CABLE');
      if not CableNoticeShown then
      begin
        CableNoticeShown := True;
        MsgBox('已检测到虚拟声卡（CABLE Input），无需重复安装，已自动取消勾选。'
               + #13#10 + '如果检测不准确，可以手动重新勾选后再继续。',
               mbInformation, MB_OK);
      end;
    end;
  end
  else if CurPageID = wpFinished then
  begin
    if WizardIsTaskSelected('vbcable') and not CableDetected then
      MsgBox('VB-CABLE 已安装。建议重启电脑后再启动 {#AppName}，'
             + '重启后系统里才会出现 CABLE Input / CABLE Output 两个端点。',
             mbInformation, MB_OK);
  end;
end;

{ [Run] 的 Check：已装过就不再跑驱动安装器 }
function NeedCableInstall(): Boolean;
begin
  Result := WizardIsTaskSelected('vbcable') and not CableDetected;
end;
