#define AppName "CoopInput"
#define AppVersion "12.9.0"
#define AppPublisher "CoopInput Contributors"

[Setup]
AppId={{67DE6AD0-9A7F-4A9E-8EBA-C4172C69A7D2}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\CoopInput
DefaultGroupName=CoopInput
UninstallDisplayIcon={app}\CoopInput.exe
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=CoopInput-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\CoopInput.ico
LicenseFile=LICENSE
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\CoopInput\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "DRIVER_SETUP.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "vendor\Interception\PROVENANCE.md"; DestDir: "{app}\licenses\Interception"; Flags: ignoreversion skipifsourcedoesntexist
Source: "vendor\Interception\licenses\LGPL-3.0.txt"; DestDir: "{app}\licenses\Interception"; Flags: ignoreversion skipifsourcedoesntexist
Source: "INSTALL_INTERCEPTION_LIBRARY.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "INSTALL_INTERCEPTION_LIBRARY.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\CoopInput"; Filename: "{app}\CoopInput.exe"
Name: "{group}\Set up Interception library"; Filename: "{app}\INSTALL_INTERCEPTION_LIBRARY.cmd"
Name: "{group}\Uninstall CoopInput"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CoopInput"; Filename: "{app}\CoopInput.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\INSTALL_INTERCEPTION_LIBRARY.cmd"; Description: "I have the official interception.dll; help me install it"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
; Do not delete personal settings: %LOCALAPPDATA%\CoopInput remains for the user.
