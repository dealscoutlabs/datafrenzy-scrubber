; Inno Setup script: builds DataFrenzyScrubber-Setup-<version>.exe
;   iscc /DAppVersion=0.1.0 packaging\windows\installer.iss
; Expects the PyInstaller output in dist\DataFrenzyScrubber (with _internal\tesseract added).

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "Data Frenzy Scrubber"
#define RuleName "Data Frenzy Scrubber - block internet"

[Setup]
AppId={{6F4C2A8E-1D3B-4C7A-9E2F-DF0A5C1B7E31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Data Frenzy
AppPublisherURL=https://www.datafrenzy.ai
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=Output
OutputBaseFilename=DataFrenzyScrubber-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; admin rights are needed to add the Windows Firewall rules below
PrivilegesRequired=admin
WizardStyle=modern
UninstallDisplayName={#AppName}

[Files]
Source: "..\..\dist\DataFrenzyScrubber\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\DataFrenzyScrubber.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DataFrenzyScrubber.exe"

[Run]
; The no-internet guarantee: block all outbound traffic for the app and its OCR engine.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#RuleName}"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#RuleName}"" dir=out action=block enable=yes program=""{app}\DataFrenzyScrubber.exe"""; Flags: runhidden; StatusMsg: "Blocking internet access for the scrubber..."
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#RuleName}"" dir=out action=block enable=yes program=""{app}\_internal\tesseract\tesseract.exe"""; Flags: runhidden
Filename: "{app}\DataFrenzyScrubber.exe"; Description: "Open {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#RuleName}"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"
