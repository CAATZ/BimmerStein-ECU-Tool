#ifndef AppVersion
  #define AppVersion "0.1.0b1"
#endif
#ifndef AppDisplayVersion
  #define AppDisplayVersion "0.1.0 Beta 1"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "0.1.0.1"
#endif
#ifndef SourceDir
  #define SourceDir "..\release\BimmerStein-ECU-Tool-0.1.0b1-Windows-x64"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

[Setup]
AppId={{2FB57080-7F5F-4C36-B723-55377DC11A55}
AppName=BimmerStein ECU Tool
AppVersion={#AppVersion}
AppVerName=BimmerStein ECU Tool {#AppDisplayVersion}
AppPublisher=CAATZ
AppPublisherURL=https://github.com/CAATZ/BimmerStein-ECU-Tool
AppSupportURL=https://github.com/CAATZ/BimmerStein-ECU-Tool/issues
AppUpdatesURL=https://github.com/CAATZ/BimmerStein-ECU-Tool/releases
VersionInfoVersion={#AppNumericVersion}
VersionInfoCompany=CAATZ
VersionInfoDescription=BimmerStein ECU Tool Beta Installer
VersionInfoProductName=BimmerStein ECU Tool
VersionInfoProductVersion={#AppNumericVersion}
DefaultDirName={localappdata}\Programs\BimmerStein ECU Tool
DefaultGroupName=BimmerStein ECU Tool
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=BimmerStein-ECU-Tool-{#AppVersion}-Windows-x64-Setup
SetupIconFile=..\assets\bimmerstein_ecu_tool.ico
UninstallDisplayIcon={app}\BimmerStein-ECU-Tool-{#AppVersion}.ico
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
Source: "..\assets\bimmerstein_ecu_tool.ico"; DestDir: "{app}"; DestName: "BimmerStein-ECU-Tool-{#AppVersion}.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\BimmerStein ECU Tool"; Filename: "{app}\BimmerStein ECU Tool.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BimmerStein-ECU-Tool-{#AppVersion}.ico"
Name: "{autodesktop}\BimmerStein ECU Tool"; Filename: "{app}\BimmerStein ECU Tool.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BimmerStein-ECU-Tool-{#AppVersion}.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\BimmerStein ECU Tool.exe"; Description: "Launch BimmerStein ECU Tool"; Flags: nowait postinstall skipifsilent
