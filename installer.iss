; Inno Setup script for Bootleg Catalog Manager.
; Build: scripts\build-installer.bat (requires Inno Setup 6 on PATH).
; Packages the existing PyInstaller output as a single Setup .exe, so run
; scripts\build.bat first. The entire dist\bootleg-catalog-manager\ folder
; is packaged (the app needs its _internal\ directory).
;
; Uninstall note: user data (settings.json, layouts/) lives under
; %LOCALAPPDATA%\BootlegCatalogManager and is intentionally NOT removed by
; the uninstaller — that is the user's data. The uninstaller only removes
; what this script installed under {app}.

#define MyAppName "Bootleg Catalog Manager"
#define MyAppVersion "1.1.0"      ; read from pyproject.toml at build time
#define MyAppPublisher "fca"
#define MyAppURL "https://github.com/fca-a/bootleg-catalog-manager"
#define MyAppExeName "bootleg-catalog-manager.exe"
#define MyAppSourceDir "dist\bootleg-catalog-manager"
#define MyAppIcon "resources\icons\bcm-icon.ico"

[Setup]
; AppId is fixed forever: changing this GUID would register a second
; entry in Add/Remove Programs instead of upgrading the existing one.
AppId={{2e4d3282-ee67-4e14-ab54-0bb484f2b6e3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; {autopf} resolves per install mode: %LOCALAPPDATA%\Programs (per-user)
; or C:\Program Files (per-machine).
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=installer-output
OutputBaseFilename=BootlegCatalogManagerSetup-{#MyAppVersion}
; The .ico must exist at {#MyAppIcon} when compiling; iscc fails otherwise.
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-user by default: no UAC prompt, files go to
; %LOCALAPPDATA%\Programs\Bootleg Catalog Manager.
PrivilegesRequired=lowest
; On the "Select Destination Location" page the user may choose
; "Install for all users"; that override triggers a UAC prompt and
; installs to C:\Program Files\Bootleg Catalog Manager instead.
PrivilegesRequiredOverridesAllowed=dialog
; Install in Program Files on 64-bit Windows and Program Files (x86) on
; 32-bit Windows (x64compatible also matches ARM64 hosts).
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Whole folder, including _internal\ — the app will not run without it.
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
