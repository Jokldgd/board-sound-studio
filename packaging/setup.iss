; ============================================================================
; Soundboard Studio —— Inno Setup 安装脚本（1.1.0，傻瓜式：自动装并校验 VB-CABLE）
;
; 要求：Inno Setup 6.5+
; 编译：ISCC.exe packaging\setup.iss
;  或：powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1
;
; 与 1.0.0 的差别：
;   * VB-CABLE 不再依赖 [Run] 的静默调用：改为 [Code] 里执行 + **轮询校验端点**；
;   * 同时检测 CABLE Input（播放）与 CABLE Output（录音），只装一半也算"未就绪"；
;   * 装完端点没出现 → 明确提示"需要重启"，可一键重启；
;   * 静默安装无效时自动再跑一次**带界面**的安装器兜底；
;   * 安装目录里保留一份 VB-CABLE 安装器 + 一键修复脚本，测试人员随时可重跑。
; ============================================================================

#define AppName        "Soundboard Studio"
#define AppVersion     "1.1.0"
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
DisableDirPage=no
DisableProgramGroupPage=yes
LicenseFile=LICENSE.txt
OutputDir=..\installer
OutputBaseFilename=SoundboardStudio-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
DirExistsWarning=auto
UsePreviousAppDir=yes
AllowNoIcons=yes

[Languages]
#if FileExists(SourcePath + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "Languages\ChineseSimplified.isl"
#else
#pragma message "警告：缺少 Languages\ChineseSimplified.isl，仅英文界面"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart";   Description: "开机自动启动 {#AppName}"; GroupDescription: "附加任务:"
Name: "vbcable";     Description: "安装 VB-CABLE 虚拟声卡（推荐；装完可能需要重启）"; GroupDescription: "虚拟声卡:"

[Files]
; 主程序：PyInstaller onedir 产物；**不打包用户数据**（config.json / studio.log / audio\ 里的录音）
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
  Excludes: "config.json,studio.log,studio.log.*,audio\*"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
; 保留一份驱动安装器在安装目录，方便测试人员随时"修复/重装"
Source: "{#CableSetup}"; DestDir: "{app}\redist"; Flags: ignoreversion
; 一键修复脚本（开始菜单里也有入口）
Source: "extra\repair-cable.cmd"; DestDir: "{app}"; DestName: "修复虚拟声卡.cmd"; Flags: ignoreversion

[Dirs]
Name: "{app}";       Permissions: users-modify
Name: "{app}\audio"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}";                Filename: "{app}\{#AppExeName}"
Name: "{group}\安装/修复 VB-CABLE 虚拟声卡"; Filename: "{app}\修复虚拟声卡.cmd"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}";            Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";          Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "SoundboardStudio"; \
  ValueData: """{app}\{#AppExeName}"""; \
  Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; \
  Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[Code]
const
  MMDEVICES_RENDER  = 'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render';
  MMDEVICES_CAPTURE = 'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture';
  // 设备"友好名称"属性键
  DEVICE_FRIENDLY_NAME = '{a45c254e-df1c-4efd-8020-67d146a850e0},2';
  CABLE_RETRY_SECONDS  = 60;

var
  CableStateBefore: Integer;    // 0 无 / 1 只有一个端点 / 2 两端齐全
  CableInstalledNow: Boolean;
  CableNoticeShown: Boolean;
  CableStatus: String;

{ 统计端点：返回命中个数（CABLE Input / CABLE Output 各算一个）
  注意：友好名称在 <设备GUID>\Properties 子键下，值名才是属性 GUID。
  少了这一层 \Properties 会永远读不到 —— 1.0.0 的"已装检测"就是这么失效的。 }
function CountCableEndpoint(const RootKey: String; const Needle: String): Integer;
var
  Names: TArrayOfString;
  I: Integer;
  Value: String;
  Key: String;
begin
  Result := 0;
  if not RegGetSubkeyNames(HKLM, RootKey, Names) then
    Exit;
  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    Key := RootKey + '\' + Names[I] + '\Properties';
    if RegQueryStringValue(HKLM, Key, DEVICE_FRIENDLY_NAME, Value) then
      if Pos(Needle, Uppercase(Value)) > 0 then
      begin
        Result := 1;
        Exit;
      end;
  end;
end;

{ 0 = 都没装；1 = 只有一半；2 = 播放+录音都在 }
function GetCableState(): Integer;
begin
  Result := CountCableEndpoint(MMDEVICES_RENDER, 'CABLE INPUT')
          + CountCableEndpoint(MMDEVICES_CAPTURE, 'CABLE OUTPUT');
end;

function CableStateText(State: Integer): String;
begin
  case State of
    2: Result := '虚拟声卡已就绪（CABLE Input / CABLE Output 都在）';
    1: Result := '虚拟声卡只装了一半（缺少 CABLE Input 或 CABLE Output）';
  else
    Result := '尚未检测到虚拟声卡（CABLE Input / CABLE Output 都不存在）';
  end;
end;

procedure UncheckTaskByHint(const Hint: String);
var
  I: Integer;
begin
  for I := 0 to WizardForm.TasksList.Items.Count - 1 do
    if Pos(Uppercase(Hint), Uppercase(WizardForm.TasksList.Items[I])) > 0 then
    begin
      WizardForm.TasksList.Checked[I] := False;
      Exit;
    end;
end;

{ 跑一次驱动安装器；返回进程退出码（-1 = 启动失败） }
function RunCableInstaller(Silent: Boolean): Integer;
var
  Params: String;
  Code: Integer;
begin
  if Silent then
    Params := '-i -h'
  else
    Params := '-i';
  if not Exec(ExpandConstant('{app}\redist\{#CableInstaller}'), Params, '',
              SW_SHOW, ewWaitUntilTerminated, Code) then
    Result := -1
  else
    Result := Code;
end;

{ 装完轮询端点（驱动枚举有时要几秒） }
function WaitForCable(Seconds: Integer): Integer;
var
  I: Integer;
begin
  Result := GetCableState();
  for I := 1 to Seconds do
  begin
    if Result = 2 then
      Exit;
    Sleep(1000);
    Result := GetCableState();
  end;
end;

procedure InitializeWizard();
begin
  CableStateBefore := GetCableState();
  CableStatus := CableStateText(CableStateBefore);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then
  begin
    if CableStateBefore = 2 then
    begin
      UncheckTaskByHint('VB-CABLE');
      if not CableNoticeShown then
      begin
        CableNoticeShown := True;
        MsgBox(CableStatus + #13#10 + '无需重复安装，已自动取消勾选。', mbInformation, MB_OK);
      end;
    end;
  end
  else if CurPageID = wpFinished then
  begin
    if CableInstalledNow and (GetCableState() = 2) then
      MsgBox('虚拟声卡已安装成功（CABLE Input / CABLE Output 已就绪）。', mbInformation, MB_OK)
    else if CableInstalledNow then
      MsgBox('驱动已安装，但端点还没出现：这是正常现象，通常重启一次就好。' + #13#10 +
             '如果重启后仍无效，请运行安装目录里的「修复虚拟声卡.cmd」。',
             mbInformation, MB_OK);
  end;
end;

{ 安装阶段（复制完文件之后）处理虚拟声卡 }
procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  State: Integer;
begin
  if (CurStep <> ssPostInstall) or (not WizardIsTaskSelected('vbcable')) then
    Exit;
  if CableStateBefore = 2 then
    Exit;

  WizardForm.StatusLabel.Caption := '正在安装 VB-CABLE 虚拟声卡（约 30–60 秒）…';
  Code := RunCableInstaller(True);
  if Code < 0 then
  begin
    MsgBox('无法启动 VB-CABLE 安装程序。可以稍后运行安装目录里的「修复虚拟声卡.cmd」。',
           mbError, MB_OK);
    Exit;
  end;

  CableInstalledNow := True;
  State := WaitForCable(20);

  { 静默安装没生效（版本差异/被杀软拦）→ 再跑一次带界面的，让用户能手动完成 }
  if State < 2 then
  begin
    if MsgBox('静默安装没有生效（驱动退出码 ' + IntToStr(Code) + '）。' + #13#10 +
              '是否现在打开 VB-CABLE 安装界面手动完成？',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      RunCableInstaller(False);
      State := WaitForCable(CABLE_RETRY_SECONDS);
    end;
  end;

  if State < 2 then
  begin
    if MsgBox('虚拟声卡还没有就绪（' + CableStateText(State) + '）。' + #13#10 + #13#10 +
              '绝大多数情况重启一次就会生效。现在就重启电脑吗？' + #13#10 +
              '（选"否"的话，请稍后手动重启，或运行安装目录里的「修复虚拟声卡.cmd」）',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      if MsgBox('确定现在重启？未保存的工作会丢失。', mbConfirmation, MB_YESNO) = IDYES then
        ShellExec('', ExpandConstant('{sys}\shutdown.exe'),
                  '/r /t 15 /c "Soundboard Studio: 安装虚拟声卡后需要重启"',
                  '', SW_SHOW, ewNoWait, Code);
    end;
  end;
end;
