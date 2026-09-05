#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#ifndef MySourceDir
  #define MySourceDir "..\dist\BlindPilot"
#endif

#ifndef MyOutputDir
  #define MyOutputDir "..\artifacts"
#endif

#define MyAppName "BlindPilot"
#define MyAppPublisher "serrebidev"
#define MyAppURL "https://github.com/serrebidev/BlindPilot"
#define MyAppExeName "BlindPilot.exe"

[Setup]
AppId={{E182A4E0-0D4C-478C-9FC0-7C1EC064949E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
; The installer's own icon, so the downloaded setup.exe shows the app
; rather than Inno Setup's stock picture. Relative to this file, like LicenseFile.
SetupIconFile=..\packaging\BlindPilot.ico
OutputDir={#MyOutputDir}
OutputBaseFilename=BlindPilot-Setup-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; force, not yes. Anything Restart Manager finds here is holding a file we are
; about to overwrite and has already declined to close, so close it. This is
; what the in-app updater has always passed on the command line.
; /NOFORCECLOSEAPPLICATIONS restores the polite prompt.
CloseApplications=force
RestartApplications=no
SetupLogging=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
; Checked by default: reaching the app from the desktop is one keystroke,
; and UsePreviousTasks keeps this choice across silent in-app updates.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
