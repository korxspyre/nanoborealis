; SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
; Copyright (C) 2026 korxspyre
; Windows installer for the NanoBorealis app (Inno Setup 6). CI passes the version and the
; PyInstaller output folder:
;   iscc /DAppVersion=0.2.0 /DSourceDir=..\dist\NanoBorealis client\packaging\windows.iss
; Installs for the current user only, so it needs no administrator rights. The app asks for
; them itself, only when it writes an install stick.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\NanoBorealis"
#endif

[Setup]
AppId={{7D7B2E0C-4E5A-4C55-9C1E-6B0F0A6C2B51}
AppName=NanoBorealis
AppVersion={#AppVersion}
AppVerName=NanoBorealis {#AppVersion}
AppPublisher=NanoBorealis contributors
AppCopyright=Copyright (C) 2026 korxspyre. PolyForm Noncommercial 1.0.0.
AppPublisherURL=https://github.com/korxspyre/nanoborealis
AppSupportURL=https://github.com/korxspyre/nanoborealis/issues
DefaultDirName={localappdata}\Programs\NanoBorealis
DefaultGroupName=NanoBorealis
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=NanoBorealis-Setup-{#AppVersion}
SetupIconFile=..\src\assets\icon.ico
UninstallDisplayIcon={app}\NanoBorealis.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; An update closes the running app first (the app starts the installer, then quits).
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; The license NanoBorealis is under (PolyForm-Noncommercial-1.0.0), its NOTICE, the text of the other licenses in it,
; and the licenses of the packages it bundles: the release workflow gathers them in assets\licenses.
Source: "..\src\assets\licenses\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\NanoBorealis"; Filename: "{app}\NanoBorealis.exe"
Name: "{userdesktop}\NanoBorealis"; Filename: "{app}\NanoBorealis.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\NanoBorealis.exe"; Description: "Open NanoBorealis"; Flags: nowait postinstall skipifsilent
; A silent install is the app updating itself: reopen it afterwards.
Filename: "{app}\NanoBorealis.exe"; Flags: nowait runasoriginaluser; Check: WizardSilent
