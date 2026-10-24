; Inno Setup script for PYFIX 360.
;
; Build the onedir bundle first (packaging\build.ps1), then compile this with:
;
;     ISCC.exe /DMyDistDir=<absolute path to dist\PYFIX360-full> installer.iss
;
; The build script passes MyDistDir automatically. If you compile from the IDE,
; it falls back to ..\dist\PYFIX360-full.
;
; Design choices:
;   * per-user install by default (PrivilegesRequired=lowest) so there is no
;     UAC prompt and no admin rights needed — appropriate for a dev tool
;   * the data directory is created under %LOCALAPPDATA%\PYFIX360 and is NOT
;     deleted on uninstall unless the user asks, because it holds the audit
;     history and snapshots
;   * an uninstall-time prompt offers to remove that data explicitly

#define MyAppName "PYFIX 360"
#define MyAppPublisher "PYFIX 360 Team"
#define MyAppExeName "PYFIX360.exe"
#define MyAppCliName "PYFIX360-cli.exe"
; The file name must not contain a space: the release pipeline puts it in a URL.
#define MyAppFileBase "PYFIX360"

; The release pipeline passes the version it built: ISCC /DMyAppVersion=0.1.0.142
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#ifndef MyDistDir
  #define MyDistDir "..\dist\PYFIX360"
#endif

[Setup]
AppId={{8E31B2A4-7C41-4C0E-9F2B-5D6A9E13C7B0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=Setup_{#MyAppFileBase}_v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=pyfix360.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\README.md
; Keep the install tidy: we ship a self-contained folder, nothing else.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
      GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "clitools"; Description: "Add the command-line tool to my &PATH"; \
      GroupDescription: "Integration:"; Flags: unchecked

[Files]
; The whole onedir bundle. recursesubdirs keeps Qt plugins and the Python
; runtime intact; ignoreversion because these are build artefacts.
Source: "{#MyDistDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} command line"; Filename: "{cmd}"; \
    Parameters: "/k ""{app}\{#MyAppCliName}"""; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Install]
; An update replaces the installed folder while the application may still be
; running. The Restart Manager closes it for us, and starts the new version
; again when the install is done — that is what makes the self-update work.
CloseApplications=yes
RestartApplications=yes

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only touch things the installer itself created inside {app}. User data lives
; under %LOCALAPPDATA% and is handled by the prompt below.
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Code]
// The data directory holds the database, audit history, snapshots and reports.
// Removing it is destructive and irreversible, so we ask explicitly and default
// to keeping it.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  Response: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\PYFIX360');
    if DirExists(DataDir) then
    begin
      Response := MsgBox(
        'Keep your PYFIX 360 data?' + #13#10 + #13#10 +
        'The data folder contains your imported projects, findings, audit ' +
        'history and repair snapshots:' + #13#10 + DataDir + #13#10 + #13#10 +
        'Choose No to delete it permanently. This cannot be undone.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON1);
      if Response = IDNO then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;

// Fail early with a clear message if the bundle was not built first.
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not DirExists(ExpandConstant('{#MyDistDir}')) then
  begin
    MsgBox('Build output not found at:' + #13#10 + '{#MyDistDir}' + #13#10 + #13#10 +
           'Run packaging\build.ps1 first, then compile this installer.',
           mbError, MB_OK);
    Result := False;
  end;
end;
