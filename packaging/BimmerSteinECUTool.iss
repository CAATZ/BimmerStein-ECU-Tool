#ifndef AppVersion
  #define AppVersion "0.1.0b12"
#endif
#ifndef AppDisplayVersion
  #define AppDisplayVersion "0.1.0 Beta 12"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "0.1.0.12"
#endif
#ifndef SourceDir
  #define SourceDir "..\release\BimmerStein-ECU-Tool-0.1.0b12-Windows-x64"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif
#ifndef PackageSuffix
  #define PackageSuffix ""
#endif
#define SetupAppName "BimmerStein ECU Tool"
#ifdef NuitkaBuild
  #define SetupAppId "{{53C48669-F8A0-4C31-A2C1-E4AF447F71CA}"
  #define SetupInstallDirName "BimmerStein ECU Tool-N"
#else
  #define SetupAppId "{{2FB57080-7F5F-4C36-B723-55377DC11A55}"
  #define SetupInstallDirName "BimmerStein ECU Tool"
#endif
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
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=BimmerStein-ECU-Tool-{#AppVersion}-Windows-x64{#PackageSuffix}-Setup
SetupIconFile=..\assets\bimmerstein_ecu_tool.ico
UninstallDisplayIcon={app}\BimmerStein-ECU-Tool-{#AppVersion}{#PackageSuffix}.ico
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
Source: "..\assets\bimmerstein_ecu_tool.ico"; DestDir: "{app}"; DestName: "BimmerStein-ECU-Tool-{#AppVersion}{#PackageSuffix}.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\{#SetupAppName}"; Filename: "{app}\BimmerStein ECU Tool.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BimmerStein-ECU-Tool-{#AppVersion}{#PackageSuffix}.ico"
Name: "{autodesktop}\{#SetupAppName}"; Filename: "{app}\BimmerStein ECU Tool.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BimmerStein-ECU-Tool-{#AppVersion}{#PackageSuffix}.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\BimmerStein ECU Tool.exe"; Description: "Launch {#SetupAppName}"; Flags: nowait postinstall skipifsilent
