Estado: **plano, nada implementado.** O EBOOT, o protocolo e o `server.txt`
não mudam: o PSP não sabe qual é o sistema do PC.

Alvo: Windows 10 22H2 e Windows 11, x64. Paridade com o Linux: captura a 60
fps, H.264 com frames P, som, teclado e mouse, controle de Xbox, e a
interface web como a tela de configurações do app (no Windows ela faz o
papel que um programa com janela faria, sem Qt nem Tk).

## O que já serve como está

A maior parte do servidor não depende do Linux:

| módulo | o que é |
|---|---|
| `protocol.py`, `transports.py` | mensagens, pedaços UDP, NACK, reenvio, janela de crédito |
| `pspstream.py` (Session, Server) | sessões, modelo pull, troca de captura com o PSP conectado |
| `stats.py`, `adaptive.py`, `sources.py` | estatísticas, qualidade adaptativa, fontes de frame |
| `settings.py`, `control.py`, `web.py`, `web/` | configurações, aplicar com o servidor rodando, interface web (só biblioteca padrão; `settings.default_path()` já usa `%APPDATA%\PSPStream`) |
| `openh264.py` | libopenh264 por ctypes. Só usa `c_int`/`c_longlong` (sem `c_long`, que tem 32 bits no Windows), então as estruturas batem; falta o nome da DLL em `LIB_NAMES` |
| `gst_source.RateLimiter` | grade fixa de FPS; serve para qualquer relógio, inclusive a hora de chegada do frame |
| `inject.py`, `gamepad.py` (a lógica) | perfis, tecla presa, mouse a 125 Hz, camada do Xbox. O que é Linux é só o dispositivo (`_UInput`), atrás de uma interface pequena (`key`, `move`, `close`) |
| `fake_client.py` e os testes de protocolo, UDP, configurações e web | rodam no Windows sem mudança (a confirmar no CI) |

## O que é do Linux, e o equivalente no Windows

| parte | Linux hoje | Windows |
|---|---|---|
| tela | portal (PipeWire), KMS (auxiliar com DRM), `ximagesrc` | Desktop Duplication (DXGI) pelo `d3d11screencapturesrc` do GStreamer (monitor, cursor); uma janela pela Windows Graphics Capture (`capture-api=wgc`, GStreamer 1.22+, a confirmar no W0) |
| redução para 480x272 | `videoscale` na CPU, ou OpenGL com `--dmabuf` | na GPU: `d3d11convert` (escala e cor) e `d3d11download` em I420. Só a imagem pequena vem para a CPU, como o caminho KMS |
| encode H.264 | libopenh264 (`.so`) | a mesma, `openh264-2.4.1-win64.dll` do Cisco (ver Riscos: licença) |
| JPEG (EBOOT antigo) | `jpegenc` | Pillow (o pipeline entrega RGB; a qualidade muda a cada frame, como no `jpegenc`) |
| som | `pulsesrc` do monitor da saída | `wasapi2src loopback=true` (o que sai nas caixas). Desde o GStreamer 1.24, dá para pegar o som de um programa só (process loopback), a confirmar |
| IMA ADPCM | `adpcmenc` | o mesmo elemento |
| teclado e mouse | uinput | `SendInput` (user32, por ctypes), com scancodes (jogos com DirectInput ignoram códigos virtuais) |
| controle de Xbox | uinput com os IDs do `xpad` | driver ViGEmBus + `vgamepad` (Python, traz o ViGEmClient) |
| DSCP (fila de voz do Wi-Fi) | `IP_TOS` | o Windows ignora `IP_TOS` sem política: qWAVE (`QOSAddSocketToFlow`, tipo voz) ou uma Política de QoS por programa |
| checagem do Wi-Fi do PC | `ip`, `iw`, `/sys` (`netcheck.py`) | `netsh wlan show interfaces` (banda e canal) |
| firewall | firewalld | regras de entrada UDP e TCP 5123 (o instalador cria com `netsh advfirewall`); a rede tem de estar como "Privada" para o broadcast do "Procurar o PC" passar |
| fontes de som | `pactl list short sources` | `gst-device-monitor-1.0 Audio/Source` ou a enumeração do WASAPI |

## A decisão principal: GStreamer dentro do processo ou à parte

**A. Dentro do processo, como no Linux (PyGObject).** O `gst_source.py` e o
`audio.py` servem quase iguais. Mas o caminho que o PyGObject documenta
para o Windows é o Python e o GStreamer do MSYS2. Isso pesa no instalador e
foge do Python oficial.

**B. GStreamer num processo à parte (`gst-launch-1.0`), Python lendo do
pipe.** Usa o GStreamer oficial (instalador MSVC) e o Python oficial, e o
PyInstaller empacota sem surpresa. Os pipes carregam tamanhos fixos, então
não precisam de enquadramento:
- vídeo: I420 480x272 = 195.840 bytes por frame (60 fps = 11 MB/s, nada
  para um pipe);
- som: blocos IMA ADPCM de tamanho fixo (`adpcmenc blockalign=N`).

O que se perde com B:
- o pts do GStreamer: o limite de FPS usa a hora de chegada, e o
  `RateLimiter` aceita qualquer relógio;
- a medida "captura" das estatísticas: vira uma estimativa;
- mudar a captura é reiniciar o processo (estimativa: 0,2-0,5 s). O `control.py` já
  troca a captura com o PSP conectado, então ele não cai;
- os erros chegam pelo código de saída e pelo stderr, em vez do bus.

**Recomendação: B na primeira versão**, atrás da mesma interface
`FrameSource`, para A continuar possível. O W0 confirma.

Pipelines previstos (a validar no W0):

```
d3d11screencapturesrc monitor-index=0 show-cursor=true
  ! queue leaky=downstream max-size-buffers=1
  ! d3d11convert add-borders=true ! video/x-raw(memory:D3D11Memory),width=480,height=272
  ! d3d11download ! video/x-raw,format=I420 ! fdsink

wasapi2src loopback=true low-latency=true ! audioconvert ! audioresample
  ! audio/x-raw,format=S16LE,rate=44100,channels=2 ! adpcmenc layout=dvi blockalign=888 ! fdsink
```

## Como fica no código

- `capture.py` já é o ponto de montagem (captura, som, controles, na
  partida e pela interface web). Ele passa a escolher o módulo pelo
  `sys.platform`: `capture_linux` (o que existe hoje) ou `capture_windows`.
- Novos módulos:
  - `win_capture.py`: `PipeSource(FrameSource)`, com o processo do
    GStreamer, a thread que lê o pipe, o `RateLimiter` pela hora de chegada
    e o `publish`;
  - `win_audio.py`: `PipeAudioCapture`, com a mesma interface do
    `AudioCapture` (ouvintes, `seq`, `rate`, `channels`, `stop`);
  - `win_input.py`: o `SendInput` atrás de `key`/`move` do `inject.py`,
    com a tabela de `KEY_*` (nomes do evdev no `keymap.json`) para
    scancodes, mais a flag de tecla estendida (setas, Ctrl e Alt da
    direita...);
  - `win_gamepad.py`: o ViGEm atrás do `GamepadInjector`; os eventos do
    perfil `xbox` viram o relatório XUSB do `vgamepad`;
  - `win_system.py`: `timeBeginPeriod(1)`, a economia de energia
    (EcoQoS) desligada para o processo, o DSCP pelo qWAVE e a checagem do
    Wi-Fi pelo `netsh`.
- `settings.py`: escolhas por sistema. A fonte no Windows fica "tela"
  (DXGI), "janela" (WGC), test e static; "Monitor" vira uma configuração
  só para os dois sistemas; os campos que só existem no Linux (janela do
  portal, KMS) somem da página no Windows.
- `keymap.json` continua com os nomes do evdev: os perfis valem igual nos
  dois sistemas.
- `packaging/windows/`: spec do PyInstaller, script do Inno Setup, regras
  do firewall.

## Fases

Cada fase termina com algo que roda.

**W0, viabilidade (1-2 dias, um PC com Windows 11 e um com Windows 10).**
- O pipeline de vídeo acima dá 60 fps? Quanto de CPU?
- O `openh264.py` codifica com a DLL do Cisco, e o resultado bate byte a
  byte com o Linux na mesma versão?
- O `wasapi2src` em loopback funciona?
- `SendInput` num jogo (teclado e mouse), e o `vgamepad` aparece como
  controle de Xbox no `joy.cpl`?
- Decidir A ou B.

Pronto quando um script mede FPS e CPU de cada parte e o resultado está
neste documento.

**W1, vídeo (3-5 dias).** `PipeSource`, montagem no `capture.py`, fontes
tela/test/static, h264p e h264, TCP e UDP, interface web,
`timeBeginPeriod`. Pronto quando o PSP-3000 mostra a área de trabalho a 60
fps e o `fake_client` passa no Windows.

**W2, som (2 dias).** `PipeAudioCapture` e a lista de dispositivos. Pronto
quando o som toca no PSP e sobrevive a trocar a taxa pela interface web.

**W3, controles (3-4 dias).** `SendInput` e ViGEm, com os perfis atuais.
Pronto quando os testes da tabela passam (cada `KEY_*` do `keymap.json` tem
scancode) e o controle funciona num jogo.

**W4, instalador (3-5 dias).**
- PyInstaller (pasta, não um .exe único, que abre devagar) com o servidor e
  a página.
- GStreamer: só os plugins usados (coreelements, d3d11, videoconvertscale,
  wasapi2, audioconvert, audioresample, adpcmenc), ~60-80 MB, ou o
  instalador oficial como pré-requisito.
- A DLL do openh264 baixada do Cisco na primeira vez, com SHA-256 conferido.
- Inno Setup: regras do firewall, menu Iniciar, "iniciar com o Windows"
  (opção na interface web), link para o ViGEmBus.
- Ícone na bandeja (`pystray`): "Abrir configurações", que abre a interface
  web no navegador, e "Sair".
- Log em `%LOCALAPPDATA%\PSPStream`.

Pronto quando, num Windows limpo (máquina virtual), o PSP conecta sem abrir
um terminal.

**W5, acabamento (2-3 dias).** DSCP pelo qWAVE, aviso do Wi-Fi do PC no
mesmo canal de 2,4 GHz, página Windows na wiki, CI no `windows-latest`,
assinatura do executável (opcional; sem ela o SmartScreen avisa).

Total: ~3-4 semanas de uma pessoa.

## Riscos

| risco | efeito | o que fazer |
|---|---|---|
| PyGObject no Windows | instalador pesado, Python do MSYS2 | arquitetura B |
| Desktop Duplication: conteúdo protegido, notebook com duas GPUs, HDR | tela preta, falha ao abrir, cores erradas (HDR entrega FP16) | testar no W0; WGC como alternativa; avisar no log |
| jogo em tela cheia exclusiva (DirectX 9 antigo) | pode sair preto | modo janela sem bordas |
| entrada injetada | o Windows bloqueia `SendInput` em janelas de administrador (UIPI); anticheats ignoram entrada injetada | rodar como administrador só se precisar; o controle pelo ViGEm costuma passar, porque é um dispositivo de driver |
| ViGEmBus foi arquivado (2023) | funciona hoje no 10 e no 11, sem correções futuras | teclado e mouse ficam como a base; o controle é opcional |
| relógio de 15,6 ms | a cópia do último pedaço (6 ms) e as esperas com tempo saem atrasadas | `timeBeginPeriod(1)`; o Python 3.11+ já usa timer de alta resolução no `sleep`; medir no W1 |
| economia de energia do Windows 11 (EcoQoS) | o processo em segundo plano é desacelerado | desligar para o processo (`SetProcessInformation`) |
| DSCP ignorado | sem a fila de voz no Wi-Fi do PC | qWAVE; com o PC no cabo, quase não importa |
| rede "Pública" | o "Procurar o PC" do PSP não acha | o instalador e a interface web avisam |
| licença do openh264 | a cobertura de patentes do Cisco vale para a DLL baixada do Cisco | baixar na primeira vez, como o Firefox; nunca distribuir junto |
| antivírus e SmartScreen | falso positivo em executáveis do PyInstaller, aviso sem assinatura | assinar o executável; publicar o hash |

## Testes

- CI com Ubuntu e `windows-latest`, Python 3.12: protocolo, transportes,
  configurações, web, controlador e o `fake_client` de ponta a ponta com a
  fonte static (nenhum precisa de GPU). Os testes que pedem `gi`, uinput ou
  KMS são pulados no Windows (`skipUnless`).
- Só no Windows: a tabela do `SendInput` (todo `KEY_*` dos perfis mapeado,
  em dry-run) e o `PipeSource` com `videotestsrc`. Precisa do GStreamer no
  runner (~300 MB): rodar só no CI noturno.
- No hardware, para cada versão: Windows 10 e 11; GPU Intel, AMD e NVIDIA;
  um notebook com duas GPUs; um jogo em janela sem bordas e um em tela
  cheia; FPS, latência e engasgos na linha do servidor, como na
  [tabela de desempenho](Desempenho).
