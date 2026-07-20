; Inno Setup 脚本：把 PyInstaller 产物打包为安装程序
; 用法：先运行 build_exe.bat，再编译本脚本
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss

#define AppName "imgReEditor"
#define AppVersion "1.0.0"
#define AppExe "imgReEditor.exe"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=imgReEditor
DefaultDirName={autopf}\imgReEditor
DefaultGroupName={#AppName}
OutputDir=installer
OutputBaseFilename=imgReEditor_Setup_{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "dist\imgReEditor\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Run]
Filename: "{app}\{#AppExe}"; Description: "运行 {#AppName}"; Flags: nowait postinstall skipifsilent
