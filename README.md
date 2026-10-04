# PSPStream

Transmite a tela do PC para um PSP (homebrew) em MJPEG pelo Wi-Fi e manda os
botões do PSP de volta ao PC como teclado e mouse. Inspirado no
[RNDS-Stream](https://github.com/gavff64/RNDS-Stream), que faz o mesmo para o
Nintendo DSi.

```
 PC (Fedora, Wayland)                                   PSP (ARK-4)
 ┌──────────────────────────────────────┐   Wi-Fi     ┌─────────────────────────────┐
 │ portal ScreenCast -> PipeWire        │   802.11b   │ thread de rede  (prio 0x24) │
 │ GStreamer: escala 480x272 -> jpegenc │ ──JPEG───>  │ 3 slots JPEG alinhados a 64 │
 │ guarda só o frame mais novo          │             │ decode sceJpeg / libjpeg-   │
 │ qualidade adaptativa à banda         │ <──pedido── │   turbo direto na VRAM      │
 │ uinput: teclado + mouse virtuais     │  + botões   │ triple buffer, flip no vsync│
 └──────────────────────────────────────┘             │ thread de controles (60 Hz) │
                                                      └─────────────────────────────┘
```

**Modelo "pull"** (a ideia central do RNDS-Stream), sobre TCP ou UDP. O PSP pede um
frame e o servidor responde com o mais recente. Só existe um frame em
trânsito, então nunca se forma fila na rede: o frameskip é automático e a
latência fica perto de um frame. Detalhes em [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Estado: o que foi testado e o que falta validar no seu hardware

Tudo foi desenvolvido sem acesso a um PSP. Testes feitos:

| parte | onde foi testado | resultado |
|---|---|---|
| toolchain pspdev, build do EBOOT | container Ubuntu 24.04, psp-gcc 15.2 | ok, sem warnings |
| Wi-Fi (apctl), TCP, protocolo | PPSSPPHeadless | ok |
| decode libjpeg-turbo e sceJpeg, cores e stride | PPSSPPHeadless (screenshots comparados) | ok |
| stream contínuo, overlay, reconexão | PPSSPPHeadless | ok |
| controles PSP -> PC (X, direcional, analógico -> mouse) | PPSSPPHeadless + depurador WebSocket, injetor em modo dry-run | ok (TCP e UDP) |
| transporte UDP (pedaços, NACK, BYE) | PPSSPPHeadless + teste com 5% de perda simulada + **PSP real** | ok; no PSP: 14-27 fps, p95 55-150 ms |
| PSPStream no PSP-3000 + Fedora 44 (TCP) | **seu hardware** | funciona; decode hw 7,9 ms, sw 34 ms |
| captura GStreamer, escala, jpegenc, qualidade adaptativa | PC + cliente falso | ok |
| `pipewiresrc` com fd + nó (o que o portal entrega) | PipeWire de teste no container | ok, 59 fps em 1080p |
| diálogo do portal ScreenCast | sway headless + xdg-desktop-portal-wlr | **parcial**: as chamadas D-Bus chegam ao portal, mas o backend wlr não captura sem GPU |

**Precisa ser validado no PSP-3000 / Fedora 44** (marcado com ⚠ ao longo
do texto):

- ⚠ Conexão Wi-Fi real (perfil do XMB, WPA2) e vazão real do 802.11b.
- ⚠ `sceJpeg` no hardware: se aceita escrever direto na VRAM, se o stride é
  512 e quanto tempo leva. Se a escrita na VRAM falhar, há fallback
  automático para um buffer em RAM; se a imagem sair torta, use `decoder=sw`.
- ⚠ Velocidade do libjpeg-turbo no Allegrex de 333 MHz.
- ⚠ Diálogo do portal do GNOME/KDE e o token que evita o diálogo nas próximas
  vezes.
- ⚠ uinput no Fedora (permissão de `/dev/uinput`).
- ⚠ Todos os números de FPS e latência do PSP. Os números atuais são medidos
  no PC ou simulados ([docs/MEASUREMENTS.md](docs/MEASUREMENTS.md)).

## 1. PC: instalação (Fedora 44)

```sh
sudo dnf install python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good \
                 pipewire-gstreamer python3-evdev
```

- `python3-gobject`: GStreamer e D-Bus no Python.
- `gstreamer1-plugins-good`: `jpegenc`.
- `pipewire-gstreamer`: `pipewiresrc`, captura no Wayland.
- `python3-evdev`: uinput, para os controles.

O xdg-desktop-portal já vem no GNOME e no KDE.

**Firewall.** O Fedora bloqueia conexões de entrada por padrão:

```sh
sudo firewall-cmd --add-port=5123/tcp --add-port=5123/udp          # até reiniciar
sudo firewall-cmd --permanent --add-port=5123/tcp --add-port=5123/udp && sudo firewall-cmd --reload   # permanente
```

**Controles (uinput).** O servidor cria um teclado e um mouse virtuais.
Para isso, seu usuário precisa poder escrever em `/dev/uinput` (mesma regra
do RNDS-Stream):

```sh
test -w /dev/uinput && echo "Pronto" || echo "Precisa configurar"
# se precisar:
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
printf '%s\n' 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/60-pspstream-uinput.rules
sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger
```

Sem acesso ao uinput, o servidor avisa e continua transmitindo, só sem os
controles.

## 2. PSP: build do cliente

### Toolchain pspdev (Fedora), conforme o [guia oficial](https://pspdev.github.io/installation/fedora.html)

```sh
sudo dnf -y install @development-tools cmake bsdtar libusb-compat-0.1 gpgme2 fakeroot xz
curl -LO https://github.com/pspdev/pspdev/releases/latest/download/pspdev-fedora-latest.tar.gz
tar xzf pspdev-fedora-latest.tar.gz -C ~
echo 'export PSPDEV="$HOME/pspdev"; export PATH="$PATH:$PSPDEV/bin"' >> ~/.bashrc
source ~/.bashrc && psp-config --pspdev-path
```

No zsh, ponha a mesma linha no `~/.zshrc`, porque o zsh não lê o `~/.bashrc`.
Mesmo sem o `export`, os Makefiles acham o pspdev em `~/pspdev` ou
`/usr/local/pspdev` (`psp/pspdev.mk`). Em outro lugar, use
`make PSPDEV=/caminho/do/pspdev`.

### Compilar

```sh
cd psp
make          # gera EBOOT.PBP
make dist     # monta dist/PSP/GAME/PSPStream/{EBOOT.PBP,server.txt}
```

### Instalar no PSP-3000 com ARK-4

1. Copie a pasta `dist/PSP` para a raiz do memory stick. O resultado deve ser
   `ms0:/PSP/GAME/PSPStream/EBOOT.PBP`.
2. Edite `PSP/GAME/PSPStream/server.txt`: na primeira linha, coloque o IP do
   PC, que o servidor mostra ao iniciar.
3. No XMB, em **Ajustes > Ajustes de rede > Modo infraestrutura**, crie a
   conexão com o seu roteador. Ela é o perfil 1 se for a primeira.
4. Recomendado: em **Ajustes > Ajustes de economia de energia > Economia de
   energia WLAN**, escolha **Desligado**. Com ela ligada, o PSP desliga o
   rádio entre beacons e a latência cresce muito. O app avisa na tela.
5. Ligue a chave WLAN, na lateral esquerda do PSP-3000.

**Roteador.** O PSP só fala 802.11b em 2,4 GHz. Deixe o roteador em modo
misto (b/g/n) e use WPA2-PSK (AES) ou WPA-PSK. Se possível, ligue o PC ao
roteador por cabo: com o PC também no Wi-Fi, a banda disponível cai pela
metade.

### Opções do `server.txt`

```
192.168.1.100        # IP do PC (opcional :porta, padrão 5123)
wifi_profile=1       # perfil de rede do XMB
decoder=auto         # auto (hardware com fallback) | hw | sw
vsync=1              # 1 = sem rasgo na imagem (+0 a 16 ms); 0 = troca imediata
prefetch=1           # 1 = rede e decode em paralelo
overlay=1            # FPS, KB/frame, KB/s, decode, rede, descartes
input=1              # controles do PSP -> PC
transport=udp        # udp (padrão) | tcp; SELECT+START+L troca com o stream rodando
early_kb=auto        # UDP: pede o próximo frame quando faltar isso do atual (auto = ida e volta x vazão; 0 = no fim)
rxwait=auto          # UDP: auto | select | poll (auto mede os dois ao conectar e usa o mais rápido)
h264=1               # 1 = aceita H.264 (servidor com --codec h264)
rcvbuf=64            # buffer de recepção do socket (KB)
bench=0              # 1 = mede o decode hw x sw no próprio PSP ao conectar
```

## 3. Uso

```sh
python3 server/pspstream.py
```

Na primeira vez, o GNOME/KDE abre um diálogo para escolher o monitor ou a
janela. A escolha fica salva em `~/.config/pspstream/portal_token`; use
`--forget` para escolher de novo. Depois, abra o PSPStream no PSP.

### H.264 (padrão desde a v0.5)

```sh
sudo dnf install gstreamer1-plugin-openh264   # repositório fedora-cisco-openh264, já ativo no Fedora Workstation
python3 server/pspstream.py                   # --codec auto: H.264 se o openh264enc existir, senão JPEG
python3 server/pspstream.py --codec jpeg      # força o MJPEG
```

Todo frame vai como IDR (quadro completo), decodificado pelo hardware do PSP
(~3,7 ms por frame medidos no PSP-3000) e sem frames de atraso. Medido no
PSP-3000 (v0.8, imagem estática): 69 fps com 21 ms de latência média em
q50, e 61 fps com 26 ms em q90. No Minecraft, 35-37 fps com 30-38 ms. Com JPEG eram 20 fps / 46 ms e 11 fps / 90 ms. Na mesma
qualidade, os frames têm ~33-45% dos bytes do JPEG (medido no PC). Cada
frame continua independente: uma perda estraga só aquele frame. A qualidade
adaptativa e o `-q` continuam na escala do JPEG (q50 do H.264 ≈ q50 do JPEG
em SSIM). Só em 480x272. O overlay mostra `h264` no lugar de `hw`/`sw`.
Para o PSP recusar H.264, use `h264=0` no `server.txt`.

| opção | o que faz |
|---|---|
| `--source portal` | tela no Wayland (padrão) |
| `--source test` | padrão animado com relógio (testes sem captura de tela) |
| `--source static --image arq.png` | uma imagem fixa (benchmark reproduzível) |
| `--source x11` / `--source gst --gst-src "..."` | sessão X11 / pipeline GStreamer próprio |
| `--window` | portal: capturar uma janela em vez do monitor |
| `--dmabuf` | portal, experimental: a tela fica na memória da GPU e é reduzida no OpenGL; só 480x272 vem para a CPU. Se não funcionar, volta sozinho para o modo normal |
| `--target-fps 20` | qualidade adaptativa: FPS que a banda precisa sustentar (padrão 20) |
| `--fixed-quality -q 70` | qualidade fixa em vez de adaptativa |
| `--q-min 25 --q-max 90` | limites da qualidade adaptativa |
| `--size 480x272` | resolução enviada (menor = menos banda; o PSP centraliza) |
| `--scale bilinear2` | filtro de redução (padrão; `lanczos` deixa o texto um pouco mais nítido) |
| `--fps 60` | taxa de captura (acima do FPS do PSP, reduz a idade do frame) |
| `--profile jogo` | mapa de controles: `jogo`, `desktop`, `setas` (ver `server/keymap.json`) |
| `--input-dry-run` | só mostrar no log as teclas que seriam injetadas |
| `--input-timeout 0.5` | solta tudo se o PSP sumir por 0,5 s com tecla segurada (evita tecla presa) |
| `--udp-pace KB/s` | UDP: limitar a taxa de envio dos pedaços (padrão: sem limite; teste 450 se a perda crescer com frames grandes) |
| `--no-hdr-cache` | UDP: mandar o cabeçalho JPEG (~620 bytes) em todo frame. O padrão manda só quando a qualidade muda; a opção existe para comparar |
| `--codec auto` | H.264 só com quadros completos, decodificado pelo hardware do PSP, se o openh264enc existir (padrão); `jpeg` força o MJPEG |
| `--dscp ef` | marca os pacotes para a fila de voz do Wi-Fi (WMM) na placa do PC e no roteador; `0` desliga |
| `--bench 30,50,70,90` | varre qualidades com o PSP conectado e salva uma tabela |

A cada 2 s, o servidor mostra uma linha de estatística:

```
28.2 fps | 13.2 KB/frame | Wi-Fi 375 KB/s | latência 46.7 ms (p95 47.5) ~ captura 9.0 + idade 7.5 + rede 35.2 + psp 11.3 | decode 11.0 ms | espera por frame novo 0.2 ms | q 51
```

"Latência" vai da captura no PC até o frame aparecer no PSP. Ela é medida só
com o relógio do servidor, sem sincronizar relógios (ver
[PROTOCOL.md](docs/PROTOCOL.md)).

### Qualidade x latência: como foi ajustado

Com 802.11b, a rede domina a latência: cada KB a menos por frame economiza
~2,5 ms a 400 KB/s. Por isso a qualidade é **adaptativa** por padrão. O
servidor mede a vazão real do Wi-Fi pelos relatórios do PSP e escolhe a
**maior qualidade cuja transferência cabe em 1/20 s**. Se o decode do PSP
for mais lento que isso, usa o tempo do decode como limite, porque aí uma
qualidade maior sai de graça.

O alvo de 20 fps veio das medições no PSP-3000. Cada frame custa ~25 ms
fixos no 802.11b (disputa do meio, ACKs, o pedido), além do tempo
proporcional ao tamanho. Até q30 leva ~46 ms, então 30 fps é inalcançável,
e com esse alvo o controlador derrubava a qualidade para o mínimo sem ganhar
nada. Com 20, ele para em ~q55: ~20 fps e ~48 ms de latência. Para mais
qualidade, use `--target-fps 15`.

O filtro de redução também conta. O bilinear comum serrilha o texto ao
reduzir 1080p para 480x272. O `bilinear2`, padrão aqui, deixa o texto legível
e gera frames 27% menores (ver [MEASUREMENTS.md](docs/MEASUREMENTS.md)).

### Controles

Perfil `jogo` (padrão):

| PSP | PC |
|---|---|
| direcional | W A S D |
| X / círculo / quadrado / triângulo | espaço / Ctrl / R / E |
| R / L | clique esquerdo / direito |
| START / SELECT | Esc / Tab |
| analógico | mouse |

Perfil `desktop`: direcional = setas, X/círculo = cliques, SELECT = Alt+Tab.
Perfil `setas`: para emuladores e jogos antigos. Para criar o seu, edite
`server/keymap.json`.

**Atalhos no PSP** (segure **SELECT + START** e aperte): triângulo =
overlay, quadrado = decoder hw/sw, círculo = vsync, X = prefetch, L =
transporte TCP/UDP (reconecta). Enquanto o
atalho estiver segurado, nada é enviado ao PC. O SELECT apertado sozinho
antes do START chega ao PC.

## 4. Medições e benchmark

Os números medidos e o roteiro para medir no PSP estão em
[docs/MEASUREMENTS.md](docs/MEASUREMENTS.md). Resumo do que você roda com o
PSP:

```sh
# decode hw x sw no PSP: bench=1 no server.txt e
python3 server/pspstream.py --source static --fixed-quality -q 70
# FPS / KB / vazão / latência por qualidade (tabela em bench_*.md):
python3 server/pspstream.py --source static --image captura_do_jogo.png --bench 30,50,70,90
```

### Teste do decoder H.264 de hardware (experimental)

`psp/probe` é um EBOOT separado que decodifica clipes H.264 curtos (embutidos
nele) pelo decoder de hardware do PSP. Ele mede o que só o hardware responde
antes de trocar o MJPEG por H.264: se o decoder funciona, quanto tempo leva
por frame e se segura frames (o que somaria latência).

```sh
cd psp/probe && make
# copie EBOOT.PBP para ms0:/PSP/GAME/PSPStreamH264/ e rode pelo XMB
```

Leva alguns segundos. O resultado aparece na tela e fica em
`PSP/GAME/PSPStreamH264/resultado_h264.txt` (gravado a cada passo: se o PSP
travar, o arquivo mostra até onde foi). Os clipes vêm de
`tools/h264_probe_clips.py` (ffmpeg com libx264).

## 5. Testar sem o PSP

```sh
python3 -m unittest discover tests                 # protocolo, injetor, controlador adaptativo
python3 server/pspstream.py --source test &         # servidor
python3 tools/fake_client.py --seconds 10 --kbps 400 --decode-ms 11   # "PSP" simulado
```

`fake_client.py` imita as duas threads do PSP. `--kbps` simula a vazão do
Wi-Fi e `--decode-ms` o tempo de decode. Os números dele são simulados.

### No emulador PPSSPP

**PPSSPP desktop.** Ative *Configurações > Rede > Ativar rede/WLAN*. Coloque
o `EBOOT.PBP` e um `server.txt` com `127.0.0.1` na pasta
`PSP/GAME/PSPStream` do memory stick do PPSSPP e rode o servidor no mesmo PC.

**PPSSPPHeadless** (testes automáticos; compile o PPSSPP com
`cmake -DHEADLESS=ON`):

```sh
PPSSPP_HEADLESS=/caminho/PPSSPPHeadless tools/emu_test.sh tela.png --source static
PPSSPP_HEADLESS=... EXIT_AFTER=120 tools/emu_test.sh tela.png --source test
PPSSPP_HEADLESS=... python3 tools/emu_input_test.py   # controles; precisa de: pip install websocket-client
```

O que funciona e o que não funciona no emulador:

| recurso | PPSSPP |
|---|---|
| conexão Wi-Fi (apctl) | simulada, conecta sempre |
| TCP (sceNetInet) | sockets do PC. Socket bloqueante devolve `EAGAIN`; o cliente trata como "tente de novo", o que não muda nada no PSP real |
| sceJpeg | emulado em software, saída correta. O tempo (~10,8 ms) é um valor fixo do emulador, não medido |
| libjpeg-turbo | funciona; o tempo emulado não é o do Allegrex |
| tempos no headless | **inválidos**: o relógio emulado pula o tempo ocioso |
| banda e perdas do 802.11b | não simuladas |
| H.264 pelo `sceMpegAvcDecode` (caminho "PMP", `psp/probe`) | decodifica com FFmpeg; precisa de `tools/ppsspp-pmp-fix.patch` (sem ele o PPSSPP aborta no 2º frame). Não mostra se o PSP real segura frames |

## Estrutura

```
psp/                   cliente (C, pspdev)
  Makefile             make / make dist
  server.txt.example
  src/main.c           ciclo de vida, decode + exibição, overlay, atalhos, controles
  src/stream.c         thread de rede, slots, modelo pull
  src/net.c            módulos de rede, Wi-Fi (apctl), TCP
  src/decode.c         sceJpeg (hw) e libjpeg-turbo (sw)
  src/display.c        framebuffer 8888, triple buffering, texto
  src/config.c         server.txt
  src/protocol.h
server/                servidor (Python 3)
  pspstream.py         sessões TCP, linha de comando, benchmark
  gst_source.py        pipeline GStreamer (captura -> 480x272 -> JPEG)
  portal.py            xdg-desktop-portal ScreenCast (Wayland)
  adaptive.py          qualidade adaptativa
  inject.py            uinput (teclado/mouse)
  keymap.json          perfis de controles
  stats.py, sources.py, jpeginfo.py, protocol.py
tools/                 fake_client.py, emu_test.sh, emu_input_test.py, bench_sizes.py, make_testcard.sh
docs/                  PROTOCOL.md, MEASUREMENTS.md
tests/                 testes do servidor
```

## Decisões técnicas

- **GStreamer em vez de ffmpeg.** No Wayland, a única captura de tela robusta
  é o portal ScreenCast, que entrega um stream PipeWire. O GStreamer lê
  PipeWire nativamente (`pipewiresrc`). O `x11grab` do ffmpeg não captura
  janelas Wayland, e o `kmsgrab` exige root. Rodando dentro do processo
  (PyGObject), o `appsink` entrega um JPEG por vez, sem procurar marcadores,
  e a qualidade do `jpegenc` muda em tempo real.
- **UDP (padrão) e TCP, os dois no modelo pull.** O pull resolve a fila, que
  é o principal problema do TCP em vídeo. No PSP-3000 medido, o Wi-Fi perde
  1-3% dos pacotes. Com um frame em trânsito, cada perda vira um timeout de
  retransmissão no TCP: o vídeo **e os controles** travam por centenas de ms
  a segundos (era a causa da tecla presa). No UDP, um pedaço perdido é pedido
  de volta (NACK). Resultado medido: UDP 14-27 fps e p95 de 55-150 ms,
  contra TCP 0,5-14 fps e p95 de até 1 s ([MEASUREMENTS.md](docs/MEASUREMENTS.md)).
- **Pedido antecipado, na medida (v0.8).** O PSP pede o próximo frame
  quando o que falta do atual leva uma ida e volta para chegar: ping do
  início / intervalo médio entre pedaços x 1400 bytes, ~2-3 KB no PSP-3000.
  Assim o próximo frame começa a chegar logo depois do último pedaço do
  atual, sem tempo morto e sem fila. A primeira tentativa usava 6-14 KB
  fixos. Na simulação deu +62% de FPS, mas no PSP real o FPS não subiu e a
  latência piorou 10-30 ms, porque o frame seguinte ia inteiro para a fila
  do roteador. A versão automática deu +12-33% de FPS na simulação, sem
  subir a latência; ainda falta medir no PSP. Um frame com perda não
  antecipa, para o reenvio não ficar atrás do frame seguinte. O overlay
  mostra o valor em uso, e o bench mostra o "tempo morto entre frames".
  `early_kb=0` volta ao comportamento antigo.
- **Cabeçalho JPEG enviado uma vez (UDP).** As tabelas no início de cada
  JPEG (623 bytes no `jpegenc`) só mudam com a qualidade. O PSP guarda as
  duas últimas e diz ao servidor qual tem; o servidor manda só os dados
  comprimidos. São 6% do frame em q30 e 4,5% em q50 (medido no PC).
- **Ida e volta pura no início do stream.** O PSP manda 16 pings pequenos
  antes de pedir frames, metade esperando com `select()` e metade
  consultando o socket a cada 0,5 ms. O overlay e o log do servidor mostram
  os dois tempos, e `rxwait=auto` fica com a espera mais rápida. Isso separa
  a parte fixa da rede (pacote pequeno, rede parada) do resto.
- **Escrita direta no framebuffer em vez de sceGu.** Os dois decoders
  escrevem direto na VRAM (stride 512), sem cópias. O sceGu só valeria a pena
  para ampliar um stream menor (240x136, por exemplo) com filtro, o que ainda
  não foi implementado.
- **Prioridades de thread.** No PSP, quem tem prioridade maior sempre roda
  primeiro. O decode fica abaixo das threads da pilha TCP/IP, senão a rede
  para durante o decode e o prefetch não adianta.
- **Ordem dos libs no Makefile.** `-lpspnet_inet` e `-lpsputility` não podem
  aparecer no `LIBS`, porque o psp-gcc já os acrescenta. Listados duas vezes,
  os stubs se dividem e o carregador lê NIDs errados. Isso apareceu no
  primeiro teste no emulador.

## Limitações conhecidas

- Um PSP por vez. Nenhuma segurança: use só na rede local, como o RNDS-Stream.
- O PSP entrando em modo de espera durante o stream não foi tratado.
- Sem áudio.
- Resoluções menores que 480x272 aparecem centralizadas, sem ampliação.
