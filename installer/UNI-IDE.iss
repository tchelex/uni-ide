; ============================================================================
;  UNI-IDE.iss — установщик UNI IDE (ESP32 / UniBase) для Windows.
;  Собирается компилятором Inno Setup 6 (ISCC.exe).
;
;  Обычно вызывается из build.py, который передаёт пути и версию:
;     ISCC /DAppVersion=1.0.0 /DSourceDir=<...\dist\UNI-IDE> /DRepoDir=<репозиторий> UNI-IDE.iss
;
;  Можно собрать и вручную (из этой папки), тогда берутся значения по умолчанию:
;     ISCC UNI-IDE.iss
;
;  Тип установки: ДЛЯ ПОЛЬЗОВАТЕЛЯ, без прав администратора
;  (ставится в %LOCALAPPDATA%\Programs\UNI IDE).
; ============================================================================

#define MyAppName "UNI IDE"
#define MyAppExeName "UNI-IDE.exe"
#define MyAppPublisher "UniBase"

; Версия (переопределяется из build.py через /DAppVersion=...)
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

; Папка с готовым бандлом PyInstaller (--onedir). По умолчанию — относительно .iss.
#ifndef SourceDir
  #define SourceDir "..\dist\UNI-IDE"
#endif

; Корень репозитория (для иконки и драйвера CH341SER.EXE).
#ifndef RepoDir
  #define RepoDir ".."
#endif

[Setup]
; Уникальный идентификатор приложения — НЕ менять между версиями (нужен для обновления/удаления).
AppId={{7C2A9E14-3F8B-4D5A-9E1C-6B0D2F4A8C73}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppVerName={#MyAppName} {#AppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#AppVersion}

; --- Установка для пользователя, без админа ---
PrivilegesRequired=lowest
DefaultDirName={autopf}\UNI IDE
DefaultGroupName=UNI IDE
DisableProgramGroupPage=yes
DisableDirPage=auto

; --- Выходной установщик ---
OutputDir=..\installer-out
OutputBaseFilename=UNI-IDE-Setup-{#AppVersion}
SetupIconFile={#RepoDir}\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; --- Сжатие (внутри сотни МБ тулчейна ESP32) ---
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; Бандл 64-битный (arduino-cli Windows_64bit + 64-битный Python)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; Весь бандл (UNI-IDE.exe, _internal, index.html, vendor, arduino-cli, arduino-data, ...)
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Драйвер CH340 — ставится опционально (галочка на последней странице).
Source: "{#RepoDir}\CH341SER.EXE"; DestDir: "{app}\drivers"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
; Галочка на финальной странице (по умолчанию ВЫКЛ): установка драйвера CH340.
; CH341SER.EXE сам запросит права администратора при установке драйвера.
Filename: "{app}\drivers\CH341SER.EXE"; Description: "Установить драйвер CH340 (нужен, чтобы ПК увидел плату ESP32)"; Flags: postinstall skipifsilent unchecked
; Галочка «Запустить UNI IDE» (по умолчанию ВКЛ).
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Регенерируемые папки сборки/кэша и лог — чистим при удалении.
; ВАЖНО: папка проектов Uni_Sketches и настройки НЕ удаляются (работы учеников сохраняются).
Type: filesandordirs; Name: "{app}\build-tmp"
Type: filesandordirs; Name: "{app}\build-cache"
Type: filesandordirs; Name: "{app}\arduino-downloads"
Type: files; Name: "{app}\uni-ide-log.txt"
