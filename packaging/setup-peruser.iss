; ============================================================================
; Soundboard Studio —— 免管理员（per-user）安装脚本
;
; 用途：当"以管理员运行安装包"报
;      "安装程序无法创建目录 ...\Temp\is-XXXX.tmp，错误 5：拒绝访问"
;      （Windows 11 新版 Administrator Protection 的典型症状）
; 时，用这个脚本打出的安装包：装到 %LOCALAPPDATA%\Programs，全程不需要 UAC。
;
; 编译：ISCC.exe packaging\setup-peruser.iss
; 产物：installer\SoundboardStudio-Setup-1.0.0-user.exe
;
; 差异（相对 setup.iss）：
;   * PrivilegesRequired=lowest，DefaultDirName={localappdata}\Programs\...
;   * 不需要给安装目录加 users-modify（目录本来就归用户）
;   * VB-CABLE 驱动仍需管理员：勾选后用 ShellExec('runas') 单独提权安装
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
AppId={{2C7B9E14-8D6A-4F3B-9E52-7A1C4D6B8F22}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableDirPage=no
DisableProgramGroupPage=yes
LicenseFile=LICENSE.txt
OutputDir=..\installer
OutputBaseFilename=SoundboardStudio-Setup-{#AppVersion}-user
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
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
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart";   Description: "开机自动启动 {#AppName}"; GroupDescription: "附加任务:"
Name: "vbcable";     Description: "安装 VB-CABLE 虚拟声卡（需要管理员，稍后单独弹 UAC）"; GroupDescription: "虚拟声卡:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#CableSetup}"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#AppName}";          Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}";      Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";    Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

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
  MMDEVICES_RENDER = 'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render';
  // 设备"友好名称"属性键
  DEVICE_FRIENDLY_NAME = '{a45c254e-df1c-4efd-8020-67d146a850e0},2';

var
  CableDetected: Boolean;
  CableNoticeShown: Boolean;

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
    if RegQueryStringValue(HKLM, MMDEVICES_RENDER + '\' + Names[I] + '\Properties',
                           DEVICE_FRIENDLY_NAME, Value) then
      if Pos('CABLE INPUT', Uppercase(Value)) > 0 then
      begin
        Result := True;
        Exit;
      end;
  if RegKeyExists(HKLM,
       'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\VB-Audio Virtual Cable') then
    Result := True;
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

procedure InitializeWizard();
begin
  CableDetected := HasCableInput();
end;

{ VB-CABLE 需要管理员：用 runas 单独提权执行（本安装程序自己是普通权限） }
procedure InstallCableElevated();
var
  ResultCode: Integer;
begin
  if not ShellExec('runas', ExpandConstant('{tmp}\{#CableInstaller}'), '-i -h', '',
                   SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('无法启动 VB-CABLE 安装程序（可能取消了 UAC）。' + #13#10 +
           '可以稍后手动以管理员身份运行安装包里的 VBCABLE_Setup_x64.exe。',
           mbError, MB_OK);
    Exit;
  end;
  if ResultCode <> 0 then
    MsgBox('VB-CABLE 安装器返回代码 ' + IntToStr(ResultCode) + '，可能没有装上。',
           mbInformation, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('vbcable') and not CableDetected then
    InstallCableElevated();
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
        MsgBox('已检测到虚拟声卡（CABLE Input），无需重复安装，已自动取消勾选。',
               mbInformation, MB_OK);
      end;
    end;
  end
  else if CurPageID = wpFinished then
    if WizardIsTaskSelected('vbcable') and not CableDetected then
      MsgBox('如果刚安装 VB-CABLE，建议重启后再启动 {#AppName}。', mbInformation, MB_OK);
end;
