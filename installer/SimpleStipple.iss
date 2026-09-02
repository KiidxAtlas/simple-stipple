; Inno Setup definition for the per-user Simple Stipple installer.
; The release build passes the project version as /DAppVersion=<version>.
#ifndef AppVersion
  #error AppVersion must be supplied by scripts/build_installer.py
#endif

#define AppName "Simple Stipple"
#define AppExeName "SimpleStipple.exe"
#define AppIdValue "{{8C70BFD0-0F48-4B44-9DE3-BF78EA5EE4D2}}"

[Setup]
AppId={#AppIdValue}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=KiidxAtlas
AppPublisherURL=https://github.com/KiidxAtlas/simple-stipple
AppSupportURL=https://github.com/KiidxAtlas/simple-stipple/issues
AppUpdatesURL=https://github.com/KiidxAtlas/simple-stipple/releases
DefaultDirName={localappdata}\Programs\Simple Stipple
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SimpleStipple-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Uninstallable=yes
CloseApplications=yes
RestartApplications=no
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist\SimpleStipple\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall
