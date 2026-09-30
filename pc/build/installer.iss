; Установщик «Скачивателя» (Inno Setup 6). Собирается из pc/build/build.py.
; Ставится без прав администратора — в папку пользователя; данные — в %APPDATA%\Скачиватель.

#ifndef Version
  #define Version "1.0.0"
#endif

[Setup]
AppId={{6E8F2A4B-7C1D-4E5A-9B3F-5A2C8D1E0F47}
AppName=Скачиватель
AppVersion={#Version}
AppVerName=Скачиватель {#Version}
AppPublisher=Скачиватель
AppPublisherURL=https://github.com/Jeff999Z/skachivatel
DefaultDirName={localappdata}\Programs\Скачиватель
DefaultGroupName=Скачиватель
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Skachivatel-Setup-{#Version}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Skachivatel.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktop"; Description: "Значок на рабочем столе"; GroupDescription: "Дополнительно:"
Name: "autostart"; Description: "Запускать вместе с Windows (бот всегда на связи)"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "dist\Skachivatel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Скачиватель"; Filename: "{app}\Skachivatel.exe"
Name: "{group}\Удалить Скачиватель"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Скачиватель"; Filename: "{app}\Skachivatel.exe"; Tasks: desktop
Name: "{userstartup}\Скачиватель"; Filename: "{app}\Skachivatel.exe"; Parameters: "--hidden"; Tasks: autostart

[Run]
Filename: "{app}\Skachivatel.exe"; Description: "Запустить Скачиватель"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /im Skachivatel.exe /f"; Flags: runhidden; RunOnceId: "stop"

[Code]
// Настройки и история в %APPDATA%\Скачиватель при удалении НЕ стираются —
// переустановка ничего не теряет. Удалить вручную можно по адресу %APPDATA%\Скачиватель.
