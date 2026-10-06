; Instalador do servidor de Windows (Inno Setup 6): python packaging/windows/installer.py, que passa
; a versão, as pastas e o instalador do ViGEmBus (endereço, nome e SHA-256, os do server/win_doctor.py).
; Faz o que o pspstream --setup faz, com caixinhas: a regra do firewall e o driver ViGEmBus (baixado
; na hora, com o hash conferido; não vai dentro do instalador). A desinstalação tira a regra do firewall;
; o ViGEmBus fica (outros programas usam), e sai pelo "Adicionar ou remover programas".

#ifndef AppVersion
  #define AppVersion "0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\PSPStream"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif
#ifndef ViGEmBusURL
  #error Pass /DViGEmBusURL, /DViGEmBusFile, /DViGEmBusSHA256 and /DViGEmBusVersion (installer.py does)
#endif
#define Port "5123"

[Setup]
AppId={{6F2C1B7E-4A3D-4E58-9C1A-2B7D5E8F3A61}
AppName=PSPStream
AppVersion={#AppVersion}
AppVerName=PSPStream {#AppVersion}
AppPublisher=PSPStream
AppPublisherURL=https://github.com/k7vinilstorage/PSP-Stream
AppSupportURL=https://github.com/k7vinilstorage/PSP-Stream/wiki/Windows
AppUpdatesURL=https://github.com/k7vinilstorage/PSP-Stream/releases
VersionInfoProductTextVersion={#AppVersion}
DefaultDirName={autopf}\PSPStream
DefaultGroupName=PSPStream
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=PSPStream-Setup-x64
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
; administrador: a regra do firewall e o driver precisam
PrivilegesRequired=admin
WizardStyle=modern
; inglês por padrão, como o resto do PSPStream; o português fica na janela de idioma do início
LanguageDetectionMethod=none
ShowLanguageDialog=yes
UninstallDisplayIcon={app}\pspstream.exe
UninstallDisplayName=PSPStream
CloseApplications=yes
SetupLogging=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[CustomMessages]
en.GroupSetup=Prepare this PC:
pt.GroupSetup=Preparar este PC:
en.TaskFirewall=Allow the PSP through the Windows firewall (port {#Port} UDP and TCP, private networks)
pt.TaskFirewall=Liberar o PSP no firewall do Windows (porta {#Port} UDP e TCP, redes privadas)
en.TaskViGEmBus=Install the ViGEmBus driver {#ViGEmBusVersion}: the virtual Xbox controller (xbox profiles), downloaded from github.com/nefarius/ViGEmBus
pt.TaskViGEmBus=Instalar o driver ViGEmBus {#ViGEmBusVersion}: o controle de Xbox virtual (perfis xbox), baixado de github.com/nefarius/ViGEmBus
en.StatusFirewall=Allowing PSPStream through the firewall...
pt.StatusFirewall=Liberando o PSPStream no firewall...
en.StatusViGEmBus=Installing the ViGEmBus driver...
pt.StatusViGEmBus=Instalando o driver ViGEmBus...
en.DownloadFailed=The ViGEmBus driver could not be downloaded:%n%n%1%n%nPSPStream is installed without it (keyboard and mouse work). To add the Xbox controller later, run "pspstream --setup".
pt.DownloadFailed=Não foi possível baixar o driver ViGEmBus:%n%n%1%n%nO PSPStream é instalado sem ele (teclado e mouse funcionam). Para o controle de Xbox, rode "pspstream --setup" depois.
en.SettingsName=PSPStream settings
pt.SettingsName=Configurações do PSPStream
en.AppComment=Streams this PC's screen and audio to the PSP
pt.AppComment=Transmite a tela e o som deste PC para o PSP

[Tasks]
Name: "firewall"; Description: "{cm:TaskFirewall}"; GroupDescription: "{cm:GroupSetup}"
Name: "vigembus"; Description: "{cm:TaskViGEmBus}"; GroupDescription: "{cm:GroupSetup}"; Check: not ViGEmBusInstalled
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
; a interface web: o atalho de "Configurações" abre no navegador
Filename: "{app}\settings.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://localhost:5124"

[Icons]
Name: "{group}\PSPStream"; Filename: "{app}\pspstream.exe"; WorkingDir: "{app}"; Comment: "{cm:AppComment}"
Name: "{group}\{cm:SettingsName}"; Filename: "{app}\settings.url"
Name: "{group}\{cm:UninstallProgram,PSPStream}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PSPStream"; Filename: "{app}\pspstream.exe"; WorkingDir: "{app}"; Comment: "{cm:AppComment}"; Tasks: desktopicon

[Run]
; as mesmas regras do pspstream --setup (win_doctor.firewall_commands); a antiga sai antes, numa reinstalação
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PSPStream"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PSPStream"" dir=in action=allow protocol=UDP localport={#Port} profile=private,domain"; Flags: runhidden; StatusMsg: "{cm:StatusFirewall}"; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PSPStream"" dir=in action=allow protocol=TCP localport={#Port} profile=private,domain"; Flags: runhidden; StatusMsg: "{cm:StatusFirewall}"; Tasks: firewall
Filename: "{tmp}\{#ViGEmBusFile}"; Parameters: "/quiet /norestart"; StatusMsg: "{cm:StatusViGEmBus}"; Tasks: vigembus; Check: ViGEmBusDownloaded
Filename: "{app}\pspstream.exe"; Description: "{cm:LaunchProgram,PSPStream}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PSPStream"""; Flags: runhidden; RunOnceId: "DeleteFirewallRule"

[UninstallDelete]
Type: files; Name: "{app}\settings.url"

[Code]
var
  DownloadPage: TDownloadWizardPage;

function ViGEmBusInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\ViGEmBus');
end;

function ViGEmBusDownloaded: Boolean;
begin
  Result := FileExists(ExpandConstant('{tmp}\{#ViGEmBusFile}'));
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
end;

{ O instalador do ViGEmBus é baixado depois da página "Pronto para instalar", com o SHA-256 conferido.
  Se o download falhar, o PSPStream instala mesmo assim (sem o controle de Xbox). }
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and WizardIsTaskSelected('vigembus') then
  begin
    DownloadPage.Clear;
    DownloadPage.Add('{#ViGEmBusURL}', '{#ViGEmBusFile}', '{#ViGEmBusSHA256}');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        if not DownloadPage.AbortedByUser then
          SuppressibleMsgBox(FmtMessage(CustomMessage('DownloadFailed'), [GetExceptionMessage]),
            mbError, MB_OK, IDOK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;
