; Инсталлятор propgen (Inno Setup 6). Собирается tools\build\build.py, который передаёт пути и версию:
;   iscc /DAppVersion=1.2.0 /DAppVersionNum=1.2.0.0 /DAppSrc=..\build\app /DRedistDir=..\build\redist /DOutputDir=..\dist propgen.iss
; Установка на пользователя (без прав администратора) в %LOCALAPPDATA%\Programs\propgen = {app}:
; код из репозитория + python\ (python-build-standalone с зависимостями) + installed.json.
; Данные пользователя (%LOCALAPPDATA%\propgen) инсталлятор не трогает ни при обновлении, ни при удалении.
; Обновление из программы: propgen-setup-X.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS —
; после него инсталлятор сам запускает start.cmd. /NOSTART (или /NOSTART=1) — не запускать (так ставит CI).

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef AppVersionNum
  #define AppVersionNum "0.0.0.0"
#endif
#ifndef AppSrc
  #define AppSrc "..\build\app"
#endif
#ifndef RedistDir
  #define RedistDir "..\build\redist"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
; AppId не менять никогда: по нему обновление находит прежнюю установку, а «Приложения» — деинсталлятор
AppId={{6E1B7C3A-4F2D-4B8E-9A51-2C7D8E0F3B64}
AppName=propgen
AppVersion={#AppVersion}
AppVerName=propgen {#AppVersion}
AppPublisher=creatoff
AppPublisherURL=https://github.com/creatoff/propgen
AppSupportURL=https://github.com/creatoff/propgen/issues
AppUpdatesURL=https://github.com/creatoff/propgen/releases
VersionInfoVersion={#AppVersionNum}
VersionInfoProductName=propgen
; на пользователя: обновление из программы идёт без UAC (запрос — только у vc_redist, если он нужен)
PrivilegesRequired=lowest
; путь фиксирован (контракт с программой и её обновлением); /DIR= в командной строке всё равно работает (CI)
DefaultDirName={localappdata}\Programs\propgen
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=propgen-setup-{#AppVersion}
SetupIconFile=propgen.ico
UninstallDisplayIcon={app}\propgen.ico
UninstallDisplayName=propgen
WizardStyle=modern
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage
; python\ с numpy/scipy/pymeshlab — сотни мегабайт: сплошное сжатие ужимает их примерно втрое
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
; занятые файлы закрывает Restart Manager, но сервер мы гасим сами (PrepareToInstall) и запускаем заново через [Run] —
; повторный перезапуск силами Restart Manager дал бы второй сервер
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ru.VCInstalling=Установка Microsoft Visual C++ Redistributable…
ru.VCNotInstalled=Не удалось установить Microsoft Visual C++ Redistributable (%1).%n%npropgen установится, но обработка 3D (pymeshlab) может падать. Поставьте его вручную:%nhttps://aka.ms/vs/17/release/vc_redist.x64.exe
ru.VCFailed=Установщик Microsoft Visual C++ Redistributable завершился с кодом %1.%n%npropgen установится, но обработка 3D (pymeshlab) может падать. Поставьте его вручную:%nhttps://aka.ms/vs/17/release/vc_redist.x64.exe
en.VCInstalling=Installing Microsoft Visual C++ Redistributable…
en.VCNotInstalled=Microsoft Visual C++ Redistributable was not installed (%1).%n%npropgen will be installed, but 3D processing (pymeshlab) may crash. Install it manually:%nhttps://aka.ms/vs/17/release/vc_redist.x64.exe
en.VCFailed=Microsoft Visual C++ Redistributable setup exited with code %1.%n%npropgen will be installed, but 3D processing (pymeshlab) may crash. Install it manually:%nhttps://aka.ms/vs/17/release/vc_redist.x64.exe

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; обновление = чистая замена кода: устаревшие модули не должны остаться и импортироваться.
; python\ — целиком: зависимости могли смениться, остатки старых пакетов ломают импорт.
; Не трогаем: web\hdri\*_4k.hdr (панорамы, скачанные пользователем) и всё вне {app}.
Type: filesandordirs; Name: "{app}\python"
Type: files; Name: "{app}\*.py"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\tools\*.py"
Type: filesandordirs; Name: "{app}\tools\__pycache__"
Type: files; Name: "{app}\web\*.js"
Type: files; Name: "{app}\web\*.css"
Type: files; Name: "{app}\web\*.html"
Type: files; Name: "{app}\examples\*"

[Files]
Source: "{#AppSrc}\*"; DestDir: "{app}"; Excludes: "\work,\input,\config.json,\.openai_key"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "propgen.ico"; DestDir: "{app}"; Flags: ignoreversion
; ставится из [Code] только если в системе старее 14.40 — в {app} не копируется
Source: "{#RedistDir}\vc_redist.x64.exe"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\propgen"; Filename: "{app}\start.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\propgen.ico"
Name: "{autodesktop}\propgen"; Filename: "{app}\start.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\propgen.ico"; Tasks: desktopicon

[Run]
; обычная установка — галочка на последней странице; тихая (обновление из программы) — запуск сразу, без ожидания
Filename: "{app}\start.cmd"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,propgen}"; Flags: postinstall nowait skipifsilent shellexec; Check: ManualStart
Filename: "{app}\start.cmd"; WorkingDir: "{app}"; Flags: nowait shellexec; Check: AutoStart

[UninstallDelete]
; то, что появилось после установки: кэш байткода, панорамы 4k. Данные в %LOCALAPPDATA%\propgen остаются.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\tools\__pycache__"
Type: files; Name: "{app}\web\hdri\*_4k.hdr"

[Code]
const
  VCKey = 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';
  VCMinMinor = 40;  { 14.40: на более старом msvcp140 pymeshlab падает 0xC0000005 }

{ /NOSTART, /NOSTART=1 — не запускать программу после установки (CI, ручные проверки) }
function NoStart: Boolean;
var
  i: Integer;
  p: String;
begin
  Result := ExpandConstant('{param:NOSTART|0}') <> '0';
  for i := 1 to ParamCount do
  begin
    p := Uppercase(ParamStr(i));
    if p = '/NOSTART' then
      Result := True;
  end;
end;

function ManualStart: Boolean;
begin
  Result := not NoStart;
end;

function AutoStart: Boolean;
begin
  Result := WizardSilent and not NoStart;
end;

function VCRegistryOk(RootKey: Integer): Boolean;
var
  major, minor: Cardinal;
begin
  Result := RegQueryDWordValue(RootKey, VCKey, 'Major', major) and RegQueryDWordValue(RootKey, VCKey, 'Minor', minor)
    and ((major > 14) or ((major = 14) and (minor >= VCMinMinor)));
end;

{ Свежий ли VC++ Runtime: запись в реестре И сама msvcp140.dll в System32 (бывает, что одно новее другого) }
function VCRuntimeOk: Boolean;
var
  ms, ls: Cardinal;
begin
  Result := VCRegistryOk(HKLM64) or VCRegistryOk(HKLM32);
  if Result then
  begin
    if GetVersionNumbers(ExpandConstant('{sys}\msvcp140.dll'), ms, ls) then
      Result := ((ms shr 16) > 14) or (((ms shr 16) = 14) and ((ms and $FFFF) >= VCMinMinor))
    else
      Result := False;
  end;
end;

procedure InstallVCRedist;
var
  rc: Integer;
  f: String;
begin
  if VCRuntimeOk then
  begin
    Log('VC++ Runtime >= 14.' + IntToStr(VCMinMinor) + ' уже есть');
    Exit;
  end;
  WizardForm.StatusLabel.Caption := CustomMessage('VCInstalling');
  ExtractTemporaryFile('vc_redist.x64.exe');
  f := ExpandConstant('{tmp}\vc_redist.x64.exe');
  { у vc_redist свой запрос UAC (ставится на всю машину); отказ — не повод срывать установку propgen }
  if not ShellExec('runas', f, '/install /quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, rc) then
  begin
    Log('vc_redist не запущен: ' + SysErrorMessage(rc));
    SuppressibleMsgBox(FmtMessage(CustomMessage('VCNotInstalled'), [SysErrorMessage(rc)]), mbError, MB_OK, IDOK);
  end
  else
  begin
    Log('vc_redist завершился с кодом ' + IntToStr(rc));
    { 0 — готово, 1638 — уже стоит новее, 3010/1641 — нужна (или начата) перезагрузка: DLL уже на месте }
    if (rc <> 0) and (rc <> 1638) and (rc <> 3010) and (rc <> 1641) then
      SuppressibleMsgBox(FmtMessage(CustomMessage('VCFailed'), [IntToStr(rc)]), mbError, MB_OK, IDOK);
  end;
end;

{ Гасим запущенный propgen из этой папки: python.exe из папки программы (python\) держит свои DLL (иначе их не заменить
  и не удалить) и порт сервера, а окно start.cmd после гибели сервера повисло бы на pause.
  Сервер данные пишет атомарно (через .tmp → replace), так что жёсткое завершение их не портит. }
procedure StopRunningApp;
var
  d, cmd: String;
  rc: Integer;
begin
  d := AddBackslash(ExpandConstant('{app}'));
  StringChangeEx(d, '''', '''''', True);  { апостроф в пути (имя пользователя) — экранируем для PowerShell }
  cmd := '$d=''' + d + '''; ' +
    '$p = @(Get-CimInstance Win32_Process | Where-Object { ' +
    '($_.ExecutablePath -and $_.ExecutablePath.StartsWith($d + ''python\'', [StringComparison]::OrdinalIgnoreCase)) -or ' +
    '($_.CommandLine -and $_.CommandLine.IndexOf($d + ''start.cmd'', [StringComparison]::OrdinalIgnoreCase) -ge 0) }); ' +
    '$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; ' +
    '$p | ForEach-Object { Wait-Process -Id $_.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }; ' +
    'Write-Output (''stopped '' + $p.Count)';
  if Exec('powershell.exe', '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + cmd + '"', '',
          SW_HIDE, ewWaitUntilTerminated, rc) then
    Log('остановка запущенного propgen: код ' + IntToStr(rc))
  else
    Log('остановка запущенного propgen не удалась: ' + SysErrorMessage(rc));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    InstallVCRedist;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopRunningApp;
end;
