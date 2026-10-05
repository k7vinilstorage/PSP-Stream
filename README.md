# PSPStream

Transmite a tela do PC para um PSP pelo Wi-Fi e manda os botões do PSP de
volta ao PC, como teclado e mouse ou como um controle de Xbox. O vídeo vai em
H.264 com frames P, decodificado pelo hardware do PSP. Inspirado no
[RNDS-Stream](https://github.com/gavff64/RNDS-Stream), que faz o mesmo para o
Nintendo DSi.

```
 PC (Linux, Wayland)                                     PSP (homebrew)
 ┌───────────────────────────────────────┐   Wi-Fi     ┌──────────────────────────────┐
 │ captura KMS (60 fps) ou portal        │   802.11b   │ thread de rede: pedaços UDP, │
 │ GPU: reduz para 480x272               │ ──H.264──>  │   NACK, fila em ordem        │
 │ openh264: frame P na hora do pedido   │             │ Media Engine: decode H.264   │
 │ qualidade adaptativa à banda          │ <─pedido──  │   direto na VRAM             │
 │ uinput: teclado/mouse ou Xbox virtual │  + botões   │ tela de configuração         │
 └───────────────────────────────────────┘             └──────────────────────────────┘
```

**Modelo "pull"** (a ideia central do RNDS-Stream): o PSP pede um frame e o
servidor responde com o mais recente, codificado na hora. Nunca se forma
fila na rede, e a latência fica perto de um frame. Detalhes em
[docs/PROTOCOL.md](docs/PROTOCOL.md).

## Recursos

- **H.264 com frames P** decodificado pelo Media Engine do PSP: com a câmera
  andando num jogo, cada frame tem ~5-15% dos bytes de um quadro completo, e
  com a tela parada, ~100 bytes. Também há H.264 só com quadros completos e
  MJPEG, para EBOOTs antigos.
- **Captura a 60 fps** no GNOME 50 pela KMS (direto da placa de vídeo), ou
  pelo portal do Wayland (GNOME, KDE).
- **UDP com recuperação de perdas** (NACK, pedido repetido, último pedaço de
  cada frame P em dobro, IDR só quando precisa), **qualidade adaptativa** à
  vazão do Wi-Fi e **pedido antecipado**, para não ficar tempo morto entre
  frames.
- **Controles**: teclado e mouse virtuais ou um **controle de Xbox 360
  virtual**, como no Sunshine.
- **Tela de configuração no PSP**: IP (ou "Procurar o PC na rede"), perfil de
  Wi-Fi, transporte e opções, gravados no `server.txt`.
- **Overlay** com FPS, KB por frame, tempos de decode e rede; estatísticas
  completas no log do servidor.

## Requisitos

| | testado | deve funcionar |
|---|---|---|
| PSP | PSP-3000, firmware 6.61 com ARK-4 | qualquer PSP com firmware customizado que rode homebrew |
| PC | Fedora 44, GNOME 50 (Wayland), Intel Gen12 | Linux com PipeWire e o portal ScreenCast; KMS precisa de libdrm |
| rede | roteador em modo misto b/g/n, WPA2 | o PSP só fala 802.11b em 2,4 GHz |

## Instalação

### 1. PC (Fedora)

```sh
sudo dnf install python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good \
                 pipewire-gstreamer python3-evdev gstreamer1-plugin-openh264
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
```

- `gstreamer1-plugin-openh264` vem do repositório `fedora-cisco-openh264`,
  já ativo no Fedora Workstation, e traz a `libopenh264`, que o servidor
  chama direto.
- `python3-evdev`: os controles (uinput).

**Firewall.** O Fedora bloqueia conexões de entrada por padrão:

```sh
sudo firewall-cmd --permanent --add-port=5123/tcp --add-port=5123/udp && sudo firewall-cmd --reload
```

**Controles (uinput).** O servidor cria teclado, mouse ou controle virtuais
pelo `/dev/uinput`, e seu usuário precisa poder escrever nele (a mesma regra
do RNDS-Stream):

```sh
test -w /dev/uinput && echo "Pronto" || echo "Precisa configurar"
# se precisar:
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
printf '%s\n' 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/60-pspstream-uinput.rules
sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger
```

Sem o uinput, o servidor avisa e transmite sem os controles.

**Captura KMS (recomendada no GNOME 50).** Pelo portal, o GNOME 50 entrega
no máximo ~40 fps (um limitador do próprio GNOME). A captura KMS lê a imagem
que a placa de vídeo está mostrando, como a do Sunshine, e chega a 60 fps.
Ela usa um auxiliar pequeno com permissão de administrador (`CAP_SYS_ADMIN`):

```sh
sudo dnf install gcc libdrm-devel
make -C tools/kms          # compila tools/kms/pspstream-kms
make -C tools/kms cap      # sudo setcap cap_sys_admin+ep (refaça depois de cada make)
```

Só o auxiliar tem a permissão, e ele faz uma coisa só: exporta o buffer da
tela como DMA-BUF. A redução para 480x272 roda no servidor, sem privilégio,
no OpenGL. O cursor do mouse não aparece (fica num plano separado da placa),
e a captura é do monitor inteiro (`--kms-monitor 1` escolhe o segundo).

### 2. PSP

Compile o EBOOT com o [pspdev](https://pspdev.github.io/installation/fedora.html):

```sh
sudo dnf -y install @development-tools cmake bsdtar libusb-compat-0.1 gpgme2 fakeroot xz
curl -LO https://github.com/pspdev/pspdev/releases/latest/download/pspdev-fedora-latest.tar.gz
tar xzf pspdev-fedora-latest.tar.gz -C ~
cd psp && make dist      # dist/PSP/GAME/PSPStream/{EBOOT.PBP,server.txt}
```

Os Makefiles acham o pspdev em `~/pspdev` ou `/usr/local/pspdev` sem nenhum
`export`; em outro lugar, use `make PSPDEV=/caminho`.

1. Copie a pasta `dist/PSP` para a raiz do memory stick
   (`ms0:/PSP/GAME/PSPStream/EBOOT.PBP`).
2. No XMB, em **Ajustes > Ajustes de rede > Modo infraestrutura**, crie a
   conexão com o roteador.
3. Em **Ajustes > Ajustes de economia de energia > Economia de energia
   WLAN**, escolha **Desligado**. Ligada, ela desliga o rádio entre beacons e
   a latência cresce muito (o app avisa).
4. Ligue a chave WLAN, na lateral do PSP.

## Primeiro uso

No PC:

```sh
python3 server/pspstream.py --source kms --profile xbox
```

No PSP, abra o PSPStream. Na primeira vez não há IP configurado, e a tela de
configuração espera: escolha **Procurar o PC na rede** (X) e depois aperte
**START** (salva e conecta). Nas próximas vezes, ela conecta sozinha em 3 s.

Sem `--source kms`, a captura é pelo portal: na primeira vez o GNOME/KDE
pergunta qual monitor ou janela transmitir, e a escolha fica salva
(`--forget` pergunta de novo).

## No PSP

### Tela de configuração

Aparece ao abrir (conecta sozinha em 3 s se o IP já existe; qualquer botão
para a contagem), com **SELECT + START + R** durante o stream e com
**START** quando o Wi-Fi ou o PC não respondem.

- **Cima/Baixo** escolhe o item, **Esq/Dir** muda o valor.
- **IP do PC** e **Porta**: X entra na edição dígito a dígito (Esq/Dir
  escolhe o dígito, Cima/Baixo muda, X termina).
- **Perfil de Wi-Fi**: mostra o nome salvo no XMB.
- **Procurar o PC na rede**: liga o Wi-Fi e manda um ping em broadcast; o
  servidor responde e o IP dele entra no lugar.
- Transporte, H.264, frames P, decoder, vsync, overlay, controles, prefetch
  (auto/sim/não) e os ajustes de rede: as mesmas opções do `server.txt`
  (abaixo).
- **START** grava o `server.txt` e conecta; **O** conecta sem gravar. Ao
  gravar, os comentários do arquivo antigo somem.

### Atalhos durante o stream

Segure **SELECT + START** e aperte:

| botão | faz |
|---|---|
| triângulo | liga/desliga o overlay |
| quadrado | decoder do JPEG hardware/software |
| círculo | vsync |
| X | prefetch: inverte o que está valendo (com `prefetch=auto`, desligado nos frames P) |
| L | troca o transporte TCP/UDP (reconecta) |
| R | abre a tela de configuração |

Enquanto o atalho estiver segurado, nada é enviado ao PC. O SELECT apertado
sozinho antes do START chega ao PC.

### Overlay

```
 41.3 fps   1.2 KB  52 KB/s
dec 10.6 ms (h264p) rede 9.8 ms udp drop 0
perdidos 0 nack 1 repet 0 idr 0 ping 7.1 ms (min 5.2, ini 6.3 sel)
pede o proximo depois de exibir (sem prefetch, auto)
```

A última linha diz quando o próximo frame é pedido: depois de exibir o atual
(sem prefetch, o padrão com frames P) ou quando faltam tantos KB do atual
(com prefetch: JPEG e H.264 só com quadros completos).

`h264p` = H.264 com frames P (`h264`: só quadros completos; `hw`/`sw`:
JPEG). `idr` conta os quadros completos pedidos depois de uma perda, e
`repet`, os pedidos repetidos por falta de resposta (pedido ou frame inteiro
perdido, ou tela parada). `nack` conta os pedidos de pedaços que faltavam.

### `server.txt`

Tudo pode ser mudado na tela de configuração. À mão:

```
192.168.1.100        # IP do PC (opcional :porta, padrão 5123)
wifi_profile=1       # perfil de rede do XMB
transport=udp        # udp (padrão) | tcp
h264=1               # aceita H.264
h264p=1              # aceita H.264 com frames P
decoder=auto         # JPEG: auto (hardware com reserva em software) | hw | sw
vsync=1              # 1 = sem rasgo na imagem (+0 a 16 ms); 0 = troca imediata
overlay=1            # FPS, KB/frame, tempos
input=1              # controles do PSP -> PC
prefetch=auto        # auto (padrão) = sim, menos com frames P; 1 = sempre; 0 = nunca (ver abaixo)
early_kb=auto        # UDP: pede o próximo frame quando faltar isso do atual (auto = ida e volta x vazão; 0 = no fim)
rxwait=auto          # UDP: auto | select | poll
rcvbuf=64            # buffer de recepção do socket (KB)
bench=0              # 1 = mede o decode JPEG hw x sw no próprio PSP ao conectar
menu_wait=3          # s com a tela de configuração aberta antes de conectar sozinho (0 = direto)
```

**Prefetch** é pedir o próximo frame antes de decodificar o atual, para rede
e decode trabalharem juntos. No JPEG e no H.264 só com quadros completos ele
rende 1,2-1,7x de FPS (medido no PSP-3000). Com frames P, o stream ficou
**liso com o prefetch desligado** (Hollow Knight, PSP-3000): o próximo frame
só é pedido depois de exibir o atual, um por vez, num ritmo regular. Por isso
o padrão `auto` desliga o prefetch só com frames P. Na simulação, isso troca
~60 fps com tropeços por ~30 fps constantes; quem preferir os 60 fps usa
`prefetch=1` (ou SELECT + START + X durante o stream).

## Controles

### Controle de Xbox (`--profile xbox`)

Como no Sunshine, o PC ganha um **controle de Xbox 360 virtual**, com o
mesmo fabricante, modelo, botões e eixos do driver `xpad`. Jogos nativos e
do Proton (SDL), o Steam e o navegador o reconhecem sem configuração. Não
tem vibração: o PSP não tem motor.

O PSP tem menos controles que um Xbox. O resto vem de uma camada:
**segurando SELECT**, os outros botões mudam de função, e **um toque rápido
no SELECT sozinho** vale BACK (View).

| PSP | `xbox` | segurando SELECT |
|---|---|---|
| X / círculo / quadrado / triângulo | A / B / X / Y | L3 / R3 / BACK / Guide |
| direcional | direcional | analógico direito |
| L / R | LT / RT (gatilho inteiro) | LB / RB |
| START | Start | (SELECT + START é o menu do PSP) |
| analógico | analógico esquerdo | analógico esquerdo |

- `xbox-camera`, para jogos 3D: X/círculo/quadrado/triângulo viram o
  **analógico direito** (câmera) e o direcional vira A/B/X/Y (baixo = A,
  direita = B, esquerda = X, cima = Y). Segurando SELECT, o direcional volta
  a ser direcional.
- `xbox-ombros`: L/R = LB/RB e SELECT + L/R = LT/RT.

### Teclado e mouse

Perfil `jogo` (padrão):

| PSP | PC |
|---|---|
| direcional | W A S D |
| X / círculo / quadrado / triângulo | espaço / Ctrl / R / E |
| R / L | clique esquerdo / direito |
| START / SELECT | Esc / Tab |
| analógico | mouse |

Perfil `desktop`: direcional = setas, X/círculo = cliques, SELECT = Alt+Tab.
Perfil `setas`: para emuladores e jogos antigos.

Os perfis ficam em `server/keymap.json` (as chaves `_ajuda` explicam o
formato), com zona morta, curva e velocidade ajustáveis. Se o PSP sumir com
algo apertado, tudo é solto em 0,5 s (`--input-timeout`).

## Opções do servidor

| opção | o que faz |
|---|---|
| `--source kms` | direto da placa de vídeo: 60 fps, sem cursor (recomendado no GNOME 50) |
| `--source portal` | portal do Wayland (padrão); `--window` captura uma janela |
| `--source test` / `static --image arq.png` | padrão animado / imagem fixa (testes) |
| `--source x11` / `gst --gst-src "..."` | sessão X11 / pipeline GStreamer próprio |
| `--profile xbox` | controles: `jogo` (padrão), `desktop`, `setas`, `xbox`, `xbox-camera`, `xbox-ombros` |
| `--codec auto` | `h264p` (padrão, se houver openh264), `h264` (só quadros completos) ou `jpeg` |
| `--h264-encoder auto` | libopenh264 direto, com o GStreamer de reserva (padrão); `gstreamer` força o caminho antigo |
| `--fixed-quality -q 70` | qualidade fixa em vez de adaptativa |
| `--target-fps 20`, `--q-min 25 --q-max 90` | alvo e limites da qualidade adaptativa |
| `--fps 60` | taxa de captura |
| `--scale bilinear2` | filtro de redução (padrão; `lanczos` deixa o texto um pouco mais nítido) |
| `--input-dry-run` | só mostrar no log o que seria injetado |
| `--dscp ef` | marca os pacotes para a fila de voz do Wi-Fi (WMM); `0` desliga |
| `--p-redundancy-ms 4` | frames P: o último pedaço de cada frame vai de novo depois disso, e a perda dele não trava o stream; `0` desliga |
| `--bench 30,50,70,90` | varre qualidades com o PSP conectado e grava uma tabela |
| `-v` | log detalhado |

A cada 2 s, o servidor mostra uma linha de estatística:

```
41.3 fps (fonte 59.8) | 1.2 KB/frame | Wi-Fi 52 KB/s | latência 29.1 ms (p95 41.0) ~ captura 1.0 + idade 3.4 + rede 11.8 + psp 12.9 | ...
```

"Latência" vai da captura no PC até o frame aparecer no PSP, medida só com
o relógio do servidor (ver [PROTOCOL.md](docs/PROTOCOL.md)).

Quando houve, a linha termina com os **engasgos**: 50 ms ou mais entre dois
frames, com a causa provável:

```
... | engasgos 3 (pior 74 ms: 2 perda, 1 pedido atrasado)
```

| causa | o que é |
|---|---|
| `perda` | um pedaço ou frame se perdeu no Wi-Fi e foi reenviado |
| `IDR` | o PSP perdeu a corrente de frames P e pediu um quadro completo |
| `pedido atrasado` | o pedido do PSP demorou a chegar: Wi-Fi lento naquele instante, ou o PSP ocupado |
| `captura` | o PC demorou a ter frame novo (o jogo ou a captura engasgou no PC) |

## Desempenho medido

PSP-3000 e Fedora 44 com Wi-Fi 802.11b; detalhes e o histórico em
[docs/MEASUREMENTS.md](docs/MEASUREMENTS.md).

| | FPS | latência média | KB/frame |
|---|---|---|---|
| MJPEG (v0.4), imagem fixa q50 / q90 | 20 / 11 | 46 / 90 ms | 13-35 |
| H.264 só quadros completos (v0.8), imagem fixa q50 / q90 | 69 / 61 | 21 / 26 ms | 2,4 / 5,4 |
| H.264, Minecraft pelo portal (fonte ~38 fps) | 35-37 | 30-38 ms | 5-9 |
| H.264 + captura KMS, cenas leves / jogo | 43-56 / 40-42 | 32-38 / 55-66 ms | 4 / 8-9 |
| H.264 com frames P (v0.9), Hollow Knight + KMS (relato) | quase 60, com engasgos de vez em quando | não medida | 1,2-2,5 (70-150 KB/s, contra 400-450 KB/s do H.264 só quadros completos) |
| H.264 com frames P (v1.0, sem prefetch), Hollow Knight + KMS (relato) | **liso** ("excelente") | não medida | |

- Decode no PSP: JPEG 7,9 ms (hardware); H.264 só quadros completos 3,7 ms;
  frame P + 2 cópias 10,6 ms.
- No PC, o pacote P sai 1,8-2,7 ms depois do pedido (openh264 direto).
- Nas cenas de jogo, a rede é o limite: o 802.11b do PSP entrega 380-460
  KB/s na prática. Os frames P existem para isso.
- O decode dos frames P (10,6 ms) cabe nos 16,7 ms de um frame a 60 fps e
  roda em paralelo com a chegada do próximo. Os engasgos do h264p vinham das
  perdas no Wi-Fi (cada P precisa do anterior, então uma perda parava o
  stream até o reenvio); a v1.0 manda o último pedaço em dobro e repete o
  pedido, e a linha do servidor mostra o que sobrou e por quê. Com o
  prefetch desligado (padrão com frames P), o stream ficou liso no PSP-3000.

## Solução de problemas

| sintoma | o que fazer |
|---|---|
| "Sem resposta do PC" / "Procurar" não acha | o servidor está rodando? Libere 5123/udp e 5123/tcp no firewall. PC e PSP na mesma rede |
| latência alta, FPS oscilando | desligue a Economia de energia WLAN do PSP; deixe o PC no 5 GHz ou no cabo (o servidor avisa se ele divide o canal de 2,4 GHz com o PSP); roteador em modo misto b/g/n |
| engasgos de vez em quando (h264p) | confira se o prefetch está desligado (última linha do overlay: "depois de exibir"; `prefetch=auto` ou `0`). Depois, veja os engasgos e a causa na linha do servidor (acima). `perda`/`pedido atrasado`: Wi-Fi (distância, canal de 2,4 GHz cheio, micro-ondas, Bluetooth); `captura`: o PC; `IDR` frequente: perdas seguidas. `--codec h264` aguenta perdas melhor (cada quadro é independente), com 2-3x mais banda |
| h264p liso, mas quer mais FPS | `prefetch=1`: ~60 fps em vez de ~30, com mais chance de tropeço quando o Wi-Fi oscila |
| captura em ~38-40 fps no GNOME 50 | use `--source kms` |
| KMS: "sem permissão para ler a tela" | `make -C tools/kms cap` de novo (depois de cada `make`); partições montadas com `nosuid` ignoram a permissão |
| controles não chegam | o log diz "controles desativados": configure o `/dev/uinput` (acima) |
| o servidor avisa "EBOOT antigo" ou "não aceita frames P" | atualize o EBOOT (v1.0) |
| imagem torta ou com cores erradas (JPEG) | `decoder=sw` |
| algo estranho no H.264 do PC | `--h264-encoder gstreamer` usa o caminho antigo; mande o log |

## Para desenvolvedores

### Testes

```sh
python3 -m unittest discover tests                 # servidor: protocolo, encoders, controles, captura
python3 server/pspstream.py --source test &
python3 tools/fake_client.py --transport udp --h264p --seconds 10 --kbps 450 --decode-ms 11
```

`tools/fake_client.py` imita as threads do PSP (fila em ordem, NACK, IDR,
pedido antecipado e repetido, `--prefetch auto|on|off` como no `server.txt`)
e confere que nenhum frame P é decodificado sem o anterior; `--kbps`,
`--rtt-ms`, `--loss`, `--loss-up`, `--loss-burst-ms` (rajadas de
interferência) e `--decode-ms` simulam o Wi-Fi e o PSP, e o resumo conta os
engasgos. `FAKE_TRACE=1` mostra cada pedido, pedaço, perda e NACK. Os
números dele são simulados.

**PPSSPPHeadless** (compile o PPSSPP com `cmake -DHEADLESS=ON`; o H.264
precisa de `tools/ppsspp-pmp-fix.patch`):

```sh
PPSSPP_HEADLESS=/caminho/PPSSPPHeadless tools/emu_test.sh tela.png --source static
PPSSPP_HEADLESS=... python3 tools/emu_input_test.py   # controles; precisa de: pip install websocket-client
PPSSPP_HEADLESS=... python3 tools/emu_menu_test.py    # tela de configuração: procurar o PC, salvar, conectar
```

No emulador, a imagem e a lógica valem, mas os **tempos não**: o relógio
emulado pula o tempo ocioso (com frames P, corre ~30x o real), e a banda e as
perdas do 802.11b não são simuladas.

### Teste do decoder H.264 no hardware (`psp/probe`)

EBOOT separado (`PSP/GAME/PSPStreamH264/`) que decodifica clipes embutidos
pelo Media Engine e grava `resultado_h264.txt` antes de cada passo (se o PSP
travar, a próxima rodada pula o passo e roda os outros). Foi assim que se
mediu o decoder (segura 2 frames, ~3,5-4 ms por chamada) e que se achou por
que a primeira versão dos frames P desligava o PSP: um IDR no meio de uma
sequência de P sem `sceMpegAvcDecodeStop` antes. Os clipes vêm de
`tools/h264_probe_clips.py`.

### Estrutura

```
psp/                   cliente (C, pspdev)
  src/main.c           ciclo de vida, decode + exibição, overlay, atalhos, controles
  src/stream.c         thread de rede, slots, modelo pull, fila em ordem dos frames P
  src/decode.c         sceJpeg (hw), libjpeg-turbo (sw) e H.264 (sceMpegAvcDecode)
  src/menu.c           tela de configuração (IP, Wi-Fi, opções, procurar o PC)
  src/net.c            módulos de rede, Wi-Fi (apctl), TCP/UDP, broadcast
  src/display.c        framebuffer 8888, triple buffering, texto
  src/config.c         server.txt (ler e gravar)
  src/protocol.h       formato das mensagens (espelhado em server/protocol.py)
  probe/               teste do decoder H.264 no hardware
server/                servidor (Python 3)
  pspstream.py         sessões, linha de comando, benchmark
  gst_source.py        pipeline GStreamer (captura -> 480x272 -> JPEG/H.264/I420)
  kms.py, portal.py    captura KMS e pelo portal ScreenCast
  h264.py, openh264.py encoders H.264 (libopenh264 direto por ctypes; GStreamer de reserva)
  transports.py        TCP e UDP (pedaços, NACK, reenvio)
  adaptive.py          qualidade adaptativa
  inject.py, gamepad.py, keymap.json   controles (uinput)
  stats.py, sources.py, jpeginfo.py, protocol.py, netcheck.py
tools/                 fake_client.py, emu_*.py/sh, h264_probe_clips.py, kms/ (auxiliar KMS)
docs/                  PROTOCOL.md, MEASUREMENTS.md
tests/                 testes do servidor
```

### Decisões técnicas

- **Servidor em Python, o trabalho pesado em C.** Captura, redução,
  conversão de cor e codificação são GStreamer, openh264 e OpenGL (C/C++,
  GPU); o auxiliar KMS é C. O Python só costura: lê pedidos, chama o
  encoder e corta o frame em pacotes. Medido a 60 fps: as threads de Python
  usam 3-6% de um núcleo, e do pedido do PSP ao 1º pacote (JPEG/H.264 já
  pronto) são 0,43-0,48 ms no localhost, o mesmo de um ping. Reescrever em C
  ou Rust economizaria uns 20-30 MB de RAM e nada perceptível de latência.
- **openh264 chamado direto.** Pelo GStreamer (appsrc -> openh264enc ->
  appsink), cada um dos 3 AUs do pacote P passava por duas filas e duas
  threads, e o QP só mudava refazendo o encoder (um IDR). Pela libopenh264
  direto (ctypes): pedido -> 1º pacote de 2,8 para 1,8 ms, e a qualidade
  muda sem IDR. O fluxo é o mesmo, byte a byte; se a biblioteca faltar ou o
  layout dela não bater, volta para o GStreamer sozinho.
- **Frames P com 2 cópias.** O decoder do PSP só solta o frame N depois do
  N+2, e o `sceMpegAvcDecodeStop`, que solta na hora, zera as referências.
  Cada pacote leva o frame e 2 cópias (P sem mudança, ~20-80 bytes): o PSP
  faz 3 chamadas e mostra o frame real, sem atraso. O Stop só entra antes de
  um IDR, e é obrigatório ali (sem ele, o PSP desliga).
- **UDP (padrão) e TCP, os dois no modelo pull.** No PSP-3000, o Wi-Fi perde
  1-3% dos pacotes; no TCP, cada perda com um frame em trânsito vira um
  timeout de retransmissão, e o vídeo **e os controles** travavam por
  centenas de ms. No UDP, um pedaço perdido volta pelo NACK, e um frame
  inteiro perdido, pelo pedido repetido.
- **Pedido antecipado na medida.** O PSP pede o próximo frame quando o que
  falta do atual leva uma ida e volta para chegar (ping do início x vazão,
  ~2-3 KB no PSP-3000). Valores fixos maiores, que pareciam bons na
  simulação, criavam fila no roteador no PSP real.
- **Frames P sem prefetch (`prefetch=auto`).** Com frames P, o próximo frame
  só é pedido depois de exibir o atual: um frame por vez, sem fila e num
  ritmo regular. Foi o que deixou o stream liso no PSP-3000; com prefetch o
  FPS é maior, mas qualquer oscilação do Wi-Fi aparece na tela.
- **Perdas nos frames P.** Cada P precisa do anterior, então perder um
  pacote para o stream até o reenvio. O servidor manda o último pedaço de
  cada frame P de novo 6 ms depois (perder o último só era notado pelo
  silêncio), e o PSP repete o pedido de frame novo depois de 6 ms, com o
  número do frame para o servidor reconhecer a cópia.
- **GStreamer em vez de ffmpeg** para a captura: o portal entrega PipeWire,
  que o GStreamer lê nativamente, e tudo roda dentro do processo.
- **Escrita direta no framebuffer** (stride 512), sem sceGu: os decoders
  escrevem na VRAM, sem cópias.
- **Prioridades de thread no PSP**: o decode fica abaixo da pilha TCP/IP,
  senão a rede para durante o decode.
- **`-lpspnet_inet` e `-lpsputility` fora do `LIBS`**: o psp-gcc já os
  acrescenta; listados duas vezes, os stubs se dividem e o carregador lê
  NIDs errados.

## Limitações conhecidas

- Um PSP por vez. Sem autenticação nem criptografia: use só na rede local.
- Sem áudio.
- 480x272 fixo para H.264; resoluções menores (só JPEG) aparecem
  centralizadas, sem ampliação.
- A captura KMS não mostra o cursor do mouse.
- O decoder do PSP segura 2 frames, então os frames P custam 3 decodes (10,6
  ms) em vez de 1.

## Créditos

- [RNDS-Stream](https://github.com/gavff64/RNDS-Stream): a ideia do modelo
  pull e do servidor + homebrew.
- [pspdev](https://github.com/pspdev): toolchain e PSPSDK.
- [openh264](https://www.openh264.org/) (Cisco), libjpeg-turbo, GStreamer,
  PPSSPP (testes no emulador).
- PMP Mod/PMPlayer: o caminho de decode H.264 cru no PSP (`sceMpegBasePESpacketCopy`).
- [Sunshine](https://github.com/LizardByte/Sunshine): referência para a
  captura KMS e o controle virtual.

Histórico de versões em [CHANGELOG.md](CHANGELOG.md).
