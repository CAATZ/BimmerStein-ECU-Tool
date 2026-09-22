#ifndef AppVersion
  #define AppVersion "0.1.0b17"
#endif
#ifndef AppDisplayVersion
  #define AppDisplayVersion "0.1.0 Beta 17"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "0.1.0.17"
#endif
#ifndef SourceDir
  #define SourceDir "..\release\BimmerStein-ECU-Tool-0.1.0b17-Windows-x64"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif
#ifndef Architecture
  #define Architecture "x64"
#endif
#define SetupAppName "BimmerStein ECU Tool"
; Keep the existing public application's identity and saved install location.
#define SetupAppId "{{2FB57080-7F5F-4C36-B723-55377DC11A55}"
#define SetupInstallDirName "BimmerStein ECU Tool"
#define InstallerDescription "BimmerStein ECU Tool Installer"

[Setup]
AppId={#SetupAppId}
AppName={#SetupAppName}
AppVersion={#AppVersion}
AppVerName={#SetupAppName} {#AppDisplayVersion}
AppPublisher=CAATZ
AppPublisherURL=https://github.com/CAATZ/BimmerStein-ECU-Tool
AppSupportURL=https://github.com/CAATZ/BimmerStein-ECU-Tool/issues
AppUpdatesURL=https://github.com/CAATZ/BimmerStein-ECU-Tool/releases
VersionInfoVersion={#AppNumericVersion}
VersionInfoCompany=CAATZ
VersionInfoDescription={#InstallerDescription}
VersionInfoProductName={#SetupAppName}
VersionInfoProductVersion={#AppNumericVersion}
DefaultDirName={localappdata}\Programs\{#SetupInstallDirName}
DefaultGroupName={#SetupAppName}
PrivilegesRequired=lowest
MinVersion=10.0
#if Architecture == "x64"
ArchitecturesAllowed=x64compatible
#else
ArchitecturesAllowed=x86compatible
#endif
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=BimmerStein-ECU-Tool-{#AppVersion}-Windows-{#Architecture}-Setup
SetupIconFile=..\assets\bimmerstein_ecu_tool.ico
UninstallDisplayIcon={app}\BimmerStein ECU Tool.ico
LicenseFile={#SourceDir}\LICENSE.txt
InfoBeforeFile={#SourceDir}\RELEASE_NOTES.md
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\bimmerstein_ecu_tool.ico"; DestDir: "{app}"; DestName: "BimmerStein ECU Tool.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\{#SetupAppName}"; Filename: "{app}\BimmerStein ECU Tool.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BimmerStein ECU Tool.ico"
Name: "{autodesktop}\{#SetupAppName}"; Filename: "{app}\BimmerStein ECU Tool.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BimmerStein ECU Tool.ico"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}\plugins\__pycache__"

[Run]
Filename: "{app}\BimmerStein ECU Tool.exe"; Description: "Launch {#SetupAppName}"; Flags: nowait postinstall skipifsilent
