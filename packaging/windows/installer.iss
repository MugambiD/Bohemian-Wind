; Bohemian Wind for Windows: the setup program. Compiled by build.py (python packaging\windows\build.py installer),
; which passes the version, the bundle folder and the output folder. Inno Setup 6.
;
; Installs for the current user, without administrator rights, into %LOCALAPPDATA%\Programs\Bohemian Wind.
; Nothing is written outside that folder except the Start-menu entry, the optional desktop icon and the entry in
; "Installed apps". Projects and the settings of the application (the folder .bohemian-wind of the user) are never touched.

#ifndef AppVersion
  #error AppVersion is not given: this file is compiled by build.py
#endif
#ifndef NumericVersion
  #define NumericVersion "0.0.0.0"
#endif
#define AppName "Bohemian Wind"
#define AppPublisher "Bohemian Renewables"
#define AppURL "https://github.com/MugambiD/bohemian-wind."
#define Start "-m bohemian_wind desktop"

[Setup]
AppId={{6C0E7B5B-9B7E-4C55-A1B7-4E0F5C6F2D31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion={#NumericVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} setup
DefaultDirName={autopf}\Bohemian Wind
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=BohemianWind-{#AppVersion}-Setup
SetupIconFile={#Repo}\icon.ico
UninstallDisplayIcon={app}\icon.ico
UninstallDisplayName={#AppName} {#AppVersion}
WizardStyle=modern
LicenseFile={#Bundle}\licenses\Bohemian-Wind-LICENSE.txt
InfoBeforeFile={#SourcePath}\before-install.txt
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
; The file times are kept to the second: Python's compiled files (made while the build server ran the tests in the
; bundle) stay valid, so the first start does not have to compile them again.
TimeStampRounding=0
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; An installation over an older one: the old Python and packages go first, so that no file of a package that is
; no longer used stays behind.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\licenses"

[Files]
Source: "{#Bundle}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "{#Start}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Comment: "From met mast data to a permitting pack"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "{#Start}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Comment: "From met mast data to a permitting pack"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "{#Start}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; A copy that is running is asked to stop first; its files could not be removed otherwise.
Filename: "{app}\python\pythonw.exe"; Parameters: "{#Start} --quit"; WorkingDir: "{app}"; RunOnceId: "StopBohemianWind"; Flags: runhidden waituntilterminated

[UninstallDelete]
; Python writes compiled files beside its sources as it runs: they are not on the list of what was installed.
Type: filesandordirs; Name: "{app}\python"

[Code]
// An installation over a running copy: ask it to stop first, so that its files can be replaced.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Pythonw: String;
begin
  Result := '';
  Pythonw := ExpandConstant('{app}\python\pythonw.exe');
  if FileExists(Pythonw) then
    Exec(Pythonw, '{#Start} --quit', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
