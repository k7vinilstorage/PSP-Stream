# PSPStream

[![build](https://github.com/k7vinilstorage/PSP-Stream/actions/workflows/build.yml/badge.svg)](https://github.com/k7vinilstorage/PSP-Stream/actions/workflows/build.yml)

Transmite a tela e o som do PC para um PSP pelo Wi-Fi e manda os botões do
PSP de volta ao PC, como teclado e mouse ou como um controle de Xbox. O vídeo
vai em H.264 com frames P, decodificado pelo hardware do PSP, e o som em IMA
ADPCM. Inspirado no
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
- **Som do PC** (o que sai nas caixas, pelo PipeWire): IMA ADPCM a 44,1 kHz
  estéreo, ~46 KB/s, em pacotes de 20 ms, com buffer adaptativo no PSP. Liga
  e desliga pelo PSP (tela de configuração ou SELECT + START + cima).
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
- **Wolf (Games on Whales)**, experimental: transmite o lobby do Wolf para o
  PSP, com som e controles, num container ao lado dele
  ([seção Wolf](#wolf-games-on-whales)).
- **Interface web** no PC (http://localhost:5124): as configurações gerais
  (captura, codec, qualidade, som, controles, rede) mudam com o PSP
  conectado, e ficam gravadas. Mostra também o estado do stream e o log.

## Requisitos

| | testado | deve funcionar |
|---|---|---|
| PSP | PSP-3000, firmware 6.61 com ARK-4 | qualquer PSP com firmware customizado que rode homebrew |
| PC | Fedora 44, GNOME 50 (Wayland), Intel Gen12; instalação e servidor também num Ubuntu 24.04 | qualquer Linux com Python 3.10+, GStreamer 1.20+, PipeWire ou PulseAudio e o portal ScreenCast (ou X11); KMS precisa de libdrm |
| rede | roteador em modo misto b/g/n, WPA2 | o PSP só fala 802.11b em 2,4 GHz |

## Instalação

### Downloads prontos

Em [Releases](https://github.com/k7vinilstorage/PSP-Stream/releases), sem compilar nada:

| arquivo | o que é |
|---|---|
| `PSPStream-EBOOT.zip` | o app do PSP: copie a pasta `PSP` para a raiz do memory stick |
| `pspstream_*.deb` | servidor para Ubuntu 22.04+ e Debian 12+: `sudo apt install ./pspstream_*.deb` |
| `pspstream-*.rpm` | servidor para o Fedora: `sudo dnf install ./pspstream-*.rpm` |

A release mais recente é a versão estável
([EBOOT direto](https://github.com/k7vinilstorage/PSP-Stream/releases/latest/download/PSPStream-EBOOT.zip)). A pré-release
**nightly** é refeita a cada mudança na `main`
([EBOOT da nightly](https://github.com/k7vinilstorage/PSP-Stream/releases/download/nightly/PSPStream-EBOOT.zip)).

Com o pacote, o servidor vira o comando `pspstream` (`pspstream --check`,
`pspstream --source kms`...), e o pacote já traz:
- a permissão do `/dev/uinput` para os controles, para quem está sentado no PC;
- o atalho no menu de aplicativos;
- o serviço `systemctl --user enable --now pspstream`, para iniciar com a sessão;
- a regra de firewall: `sudo ufw allow PSPStream` ou
  `sudo firewall-cmd --permanent --add-service=pspstream && sudo firewall-cmd --reload`.

A captura KMS vem compilada, mas sem a permissão de ler a tela, que só o
administrador dá: o `pspstream --setup` oferece o comando
(`sudo setcap cap_sys_admin+ep /usr/libexec/pspstream/pspstream-kms`).

### 1. PC (pelo código)

O jeito mais simples, em qualquer distribuição:

```sh
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
python3 server/pspstream.py --setup
```

O `--setup` descobre a distribuição (Ubuntu/Debian e derivadas, Fedora,
Arch, openSUSE), mostra cada comando (pacotes, permissão dos controles,
firewall, captura KMS opcional) e pergunta antes de rodar. Para só
conferir, sem mudar nada: `python3 server/pspstream.py --check`.

À mão:

| distribuição | comando |
|---|---|
| **Ubuntu, Debian, Mint, Pop!_OS** ([guia completo](docs/UBUNTU.md)) | `sudo apt install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-pipewire gstreamer1.0-gl libopenh264-dev python3-evdev pulseaudio-utils` |
| **Fedora** | `sudo dnf install python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugins-bad-free pipewire-gstreamer python3-evdev gstreamer1-plugin-openh264` |
| **Arch, Manjaro, EndeavourOS** (não testado) | `sudo pacman -S --needed python-gobject gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugin-pipewire openh264 python-evdev libpulse` |
| **openSUSE** (não testado) | `sudo zypper install python3-gobject typelib-1_0-Gst-1_0 typelib-1_0-GstVideo-1_0 typelib-1_0-GstAllocators-1_0 gstreamer-plugins-base gstreamer-plugins-good gstreamer-plugins-bad gstreamer-plugin-pipewire libopenh264-7 python3-evdev pulseaudio-utils` |

- A `libopenh264` (H.264 com frames P) vem do pacote da distribuição. No
  Fedora, `gstreamer1-plugin-openh264` vem do repositório
  `fedora-cisco-openh264`, já ativo no Fedora Workstation, e traz a
  biblioteca junto. Sem pacote, a do Cisco serve
  ([docs/UBUNTU.md](docs/UBUNTU.md#1-pacotes)); sem nenhuma, o servidor
  manda JPEG.
- `evdev`: os controles (uinput).
- `pulsesrc` (plugins good) e `adpcmenc` (plugins bad): o som. Sem eles, o
  servidor avisa e roda sem som.
- Use o Python do sistema: o PyGObject do pacote não aparece num venv,
  conda ou pyenv (ou crie o venv com `--system-site-packages`).

**Firewall.** O Fedora (firewalld) bloqueia conexões de entrada por padrão;
o Ubuntu (ufw) vem com o firewall desligado. O `--check` diz qual está
ativo e o comando:

```sh
sudo firewall-cmd --permanent --add-port=5123/tcp --add-port=5123/udp && sudo firewall-cmd --reload   # firewalld
sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp                                                      # ufw
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
sudo apt install gcc make pkg-config libdrm-dev libcap2-bin   # Ubuntu/Debian
sudo dnf install gcc make libdrm-devel libcap                 # Fedora
make -C tools/kms          # compila tools/kms/pspstream-kms
make -C tools/kms cap      # sudo setcap cap_sys_admin+ep (refaça depois de cada make)
```

Só o auxiliar tem a permissão, e ele faz uma coisa só: exporta o buffer da
tela como DMA-BUF. A redução para 480x272 roda no servidor, sem privilégio,
no OpenGL. O cursor do mouse não aparece (fica num plano separado da placa),
e a captura é do monitor inteiro (`--kms-monitor 1` escolhe o segundo).

### 2. PSP

O `PSPStream-EBOOT.zip` das [Releases](https://github.com/k7vinilstorage/PSP-Stream/releases) é o EBOOT pronto. Para compilar,
use o o [pspdev](https://pspdev.github.io/installation/fedora.html)
(Fedora abaixo; Ubuntu em [docs/UBUNTU.md](docs/UBUNTU.md#9-compilar-o-eboot-no-ubuntu-opcional)):

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

As configurações também mudam pelo navegador, em **http://localhost:5124**
(ver [Interface web](#interface-web)).

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
- Transporte, H.264, frames P, decoder, vsync, overlay, controles, som,
  prefetch (auto/sim/não) e os ajustes de rede: as mesmas opções do
  `server.txt` (abaixo).
- **START** grava o `server.txt` e conecta; **O** conecta sem gravar. Ao
  gravar, os comentários do arquivo antigo somem.

### Atalhos durante o stream

Segure **SELECT + START** e aperte:

| botão | faz |
|---|---|
| triângulo | liga/desliga o overlay |
| quadrado | decoder do JPEG hardware/software |
| círculo | vsync |
| cima | liga/desliga o som (o PC para de mandar quando desliga) |
| X | prefetch: auto -> sim -> não (ver `server.txt` abaixo) |
| L | troca o transporte TCP/UDP (reconecta) |
| R | abre a tela de configuração |

Enquanto o atalho estiver segurado, nada é enviado ao PC. O SELECT apertado
sozinho antes do START chega ao PC.

### Overlay

```
 41.3 fps   1.2 KB  52 KB/s
dec 10.6 ms (h264p) rede 9.8 ms udp drop 0
perdidos 0 nack 1 repet 0 idr 0 ping 7.1 ms (min 5.2, ini 6.3 sel)
pede ate 2 frames a frente quando o decode comeca (auto)
```

A 4ª linha diz quando o próximo frame é pedido: quando o decode começa,
autorizando até 2 à frente (frames P com `prefetch=auto`; o frame sai do PC
na hora da captura), quando faltam tantos KB do atual (pedido
antecipado: JPEG e H.264 só com quadros completos, ou `prefetch=1`) ou
depois de exibir (`prefetch=0`). A 5ª é o som:

```
som 44.1 kHz buf 38 ms (alvo 40) perdidos 0 vazio 0 pulos 0
```

`buf` é o som recebido esperando para tocar, e `alvo`, quanto o PSP tenta
manter (começa em 40 ms; sobe 10 ms a cada vez que o buffer esvazia,
`vazio`, e desce 5 ms a cada 10 s sem faltar, entre 30 e 120 ms). `perdidos`
são pacotes que não chegaram (viram 20 ms de silêncio) e `pulos`, som
descartado porque acumulou demais (o atraso não cresce).

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
audio=1              # som do PC (só pelo UDP); 0 = o PC nem manda
prefetch=auto        # auto (padrão) | 1 | 0 (ver abaixo)
early_kb=auto        # UDP: pede o próximo frame quando faltar isso do atual (auto = ida e volta x vazão; 0 = no fim)
rxwait=auto          # UDP: auto | select | poll
rcvbuf=64            # buffer de recepção do socket (KB)
bench=0              # 1 = mede o decode JPEG hw x sw no próprio PSP ao conectar
menu_wait=3          # s com a tela de configuração aberta antes de conectar sozinho (0 = direto)
```

**Prefetch** é pedir o próximo frame antes de terminar o atual, para rede e
decode trabalharem juntos:

| `prefetch=` | JPEG e H.264 só com quadros completos | frames P |
|---|---|---|
| `auto` (padrão) | pede antes do fim do frame que chega (1,2-1,7x de FPS, medido no PSP-3000) | pede quando o decode pega o atual: **~60 fps lisos** no PSP-3000 (Hollow Knight). No UDP, autoriza até 2 frames à frente (v1.1): o frame sai na hora da captura |
| `1` | igual | também pede antes do fim do frame que chega (pedido antecipado) |
| `0` | pede depois de exibir o atual | pede depois de exibir o atual: ~45 fps no mesmo teste |

SELECT + START + X troca entre os três durante o stream, e o overlay mostra
qual está valendo. Até a v1.0, o `0` dava 45 ou 60 fps conforme a história
(um sinal velho fazia ele pedir quando o decode começava), e o `1` com frames
P engasgava por um erro na contagem dos pedidos; os dois foram corrigidos
na v1.1.

A janela de 2 frames (v1.1): com só o seguinte autorizado, o pedido tinha de
ir e o frame ser codificado antes da captura seguinte (16,7 ms a 60 fps);
com o Wi-Fi oscilando, o servidor perdia capturas e o PSP-3000 ficava em
52-55 fps com a fonte a 60. Com 2 à frente, o pedido já está esperando no PC
(simulação: 53-55 → 59,5-60 fps, mesma latência). Na fila do PSP fica no
máximo um frame pronto a mais, e só se a rede entregar dois de uma vez.

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

## Interface web

Com o servidor rodando, abra **http://localhost:5124** no PC. A página mostra
o estado (PSP conectado, FPS no PSP e da captura, latência, Wi-Fi,
engasgos, qualidade), as configurações gerais e o log.

| grupo | o que muda |
|---|---|
| Captura | fonte (portal, kms, x11, test, static, wolf), monitor do KMS, janela e cursor do portal, alvo e conversão do Wolf, limite de FPS, filtro de redução, esticar |
| Vídeo | codec, qualidade adaptativa ou fixa, alvo e limites da adaptativa |
| Som | ligado, fonte (o que sai nas caixas, tom de teste ou uma fonte do PipeWire), taxa, mono |
| Controles | ligados, perfil, velocidade do mouse |
| Rede | prioridade no Wi-Fi (DSCP), cópia do último pedaço, porta |

Cada campo diz quando a mudança vale:

- **na hora**: qualidade, limite de FPS, DSCP;
- **refaz a captura**, com o PSP continuando conectado: fonte, codec,
  filtro. A captura nova sobe antes de a velha parar; se não subir (ex.: KMS
  sem o auxiliar), a velha continua e a página mostra o motivo. Com frames P,
  o primeiro frame da captura nova é um IDR;
- **refaz o som** ou **os controles** (as teclas seguradas são soltas);
- **na próxima conexão do PSP** (cópia do último pedaço) ou **ao reiniciar o
  servidor** (porta: mude também o `server.txt` do PSP).

As mudanças ficam em `~/.config/pspstream/server.json` (só o que difere do
padrão; `--config` escolhe outro arquivo). Na partida, a ordem é padrão <
arquivo < linha de comando: uma opção dada na linha de comando vale mais que
o arquivo, e a página marca esses campos com "linha de comando". Se a
captura gravada no arquivo não subir, o servidor usa a da linha de comando e
avisa no log, e a página continua acessível para trocar.

Por padrão, a página só abre no próprio PC (127.0.0.1). `--web
0.0.0.0:5124` abre para a rede local (do celular, por exemplo); com a
variável `PSPSTREAM_WEB_PASSWORD`, o navegador pede uma senha (qualquer
usuário), e sem ela qualquer um na rede muda as configurações. Na rede
local, a senha vai em HTTP, sem criptografia. `--no-web` desliga.

A página recusa pedidos vindos de outros sites e endereços que não conhece
(localhost, o nome do PC e IPs; outros nomes, como um do DNS do
roteador, com `--web-allow-host` ou `PSPSTREAM_WEB_HOSTS`; ver
`server/web.py`), e nada nela recebe caminho de arquivo nem pipeline do
GStreamer (`--source gst` e `--image` só pela linha de comando).

## Wolf (Games on Whales)

O PSPStream também mostra no PSP o que roda num lobby do
[Wolf](https://github.com/games-on-whales/wolf) (jogos em containers,
servidos para o Moonlight): a imagem, o som e os botões do PSP como um
controle de Xbox no jogo, sem mudar nada no Wolf. Roda num container ao
lado dele (`--source wolf`) e fala com a API do Wolf.

Instalação do zero (Wolf + PSPStream), num servidor com Docker:

```sh
curl -fsSLO https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/install.sh
sudo bash install.sh
```

Ou à mão com os compose de [`docker/`](docker) (Intel/AMD, NVIDIA, ou só o
PSPStream ao lado de um Wolf que já roda), pelo Portainer a partir deste
repositório, e com a imagem pronta `ghcr.io/k7vinilstorage/pspstream`.

- **Passo a passo** (instalador, compose, Portainer, Wolf existente,
  primeiro uso, interface web na rede,
  configurações, problemas): [docs/WOLF.md](docs/WOLF.md).
- **Como funciona por dentro** (a sessão no Wolf, os pipelines, o som, os
  pacotes de controle, a reconexão, a segurança e o que foi conferido no
  código do Wolf): [docs/WOLF-INTERNALS.md](docs/WOLF-INTERNALS.md).

Em resumo: o PSPStream cria uma sessão própria no Wolf pela API e manda
nela pipelines que escutam o lobby, reduzem a imagem para 480x272 na GPU e
entregam a imagem e o som por TCP local; os controles entram no lobby como
um controle de Xbox virtual. Num lobby **Coop** do Wolf UI, o PSP joga
junto; num lobby **Start** (de um jogador), ele assiste enquanto o Moonlight
estiver dentro e assume quando o Moonlight sai. Um PSPStream por Wolf. O PSP
precisa estar na mesma rede local do servidor (porta 5123 UDP e TCP).

## Opções do servidor

| opção | o que faz |
|---|---|
| `--source kms` | direto da placa de vídeo: 60 fps, sem cursor (recomendado no GNOME 50) |
| `--source portal` | portal do Wayland (padrão); `--window` captura uma janela |
| `--source test` / `static --image arq.png` | padrão animado / imagem fixa (testes) |
| `--source x11` / `gst --gst-src "..."` | sessão X11 / pipeline GStreamer próprio |
| `--source wolf` | o que roda no Wolf, pela API dele (opções `--wolf-*` em [docs/WOLF.md](docs/WOLF.md)) |
| `--profile xbox` | controles: `jogo` (padrão), `desktop`, `setas`, `xbox`, `xbox-camera`, `xbox-ombros` |
| `--codec auto` | `h264p` (padrão, se houver openh264), `h264` (só quadros completos) ou `jpeg` |
| `--h264-encoder auto` | libopenh264 direto, com o GStreamer de reserva (padrão); `gstreamer` força o caminho antigo |
| `--fixed-quality -q 70` | qualidade fixa em vez de adaptativa |
| `--target-fps 20`, `--q-min 25 --q-max 90` | alvo e limites da qualidade adaptativa |
| `--fps 60` | taxa máxima de captura. De uma tela de 60 Hz, 30 é um frame sim, um não (uniforme); 40 é 2 de cada 3 (intervalos de 17 e 33 ms: a média dá 40, mas o movimento é menos uniforme que a 30 ou 60) |
| `--scale bilinear2` | filtro de redução (padrão; `lanczos` deixa o texto um pouco mais nítido) |
| `--input-dry-run` | só mostrar no log o que seria injetado |
| `--dscp ef` | marca os pacotes para a fila de voz do Wi-Fi (WMM); `0` desliga |
| `--p-redundancy-ms 6` | frames P: o último pedaço de cada frame vai de novo depois disso, e a perda dele não trava o stream; `0` desliga |
| `--no-audio` | sem som (o PSP também desliga pelo `audio=0` ou SELECT + START + cima) |
| `--audio-device NOME` | fonte do som: `monitor` (padrão, o que sai nas caixas), `test` (tom de 440 Hz) ou uma fonte do `pactl list short sources` |
| `--audio-rate 44100`, `--audio-mono` | taxa (22050, 32000, 44100 ou 48000 Hz; 44100 é a do PSP) e mono: ~46 KB/s a 44,1 kHz estéreo, ~34 a 32 kHz, metade em mono |
| `--bench 30,50,70,90` | varre qualidades com o PSP conectado e grava uma tabela |
| `--web 127.0.0.1:5124`, `--no-web` | endereço da [interface web](#interface-web) (`0.0.0.0:5124` = rede local, sem senha) ou nenhuma |
| `--config ARQUIVO` | configurações gravadas pela interface web (padrão `~/.config/pspstream/server.json`) |
| `--check`, `--setup` | confere as dependências e diz o comando da sua distribuição; `--setup` também instala e configura, perguntando antes de cada passo |
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
| H.264 com frames P (v1.0), Hollow Knight + KMS (relato), pedido quando o decode começa | **perto de 60, liso** ("excelente") | não medida | |
| o mesmo, pedido depois de exibir (`prefetch=0` de verdade) | ~45 | não medida | |

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
  próximo frame pedido quando o decode começa (`prefetch=auto`), o stream
  fica liso e perto de 60 fps no PSP-3000.

## Solução de problemas

| sintoma | o que fazer |
|---|---|
| "Sem resposta do PC" / "Procurar" não acha | o servidor está rodando? Libere 5123/udp e 5123/tcp no firewall (`--check` diz o comando). PC e PSP na mesma rede, sem isolamento de clientes no roteador |
| algo falta ou não abre | `python3 server/pspstream.py --check`: lista o que falta e o comando para a sua distribuição |
| latência alta, FPS oscilando | desligue a Economia de energia WLAN do PSP; deixe o PC no 5 GHz ou no cabo (o servidor avisa se ele divide o canal de 2,4 GHz com o PSP); roteador em modo misto b/g/n |
| engasgos de vez em quando (h264p) | confira o prefetch (4ª linha do overlay; o padrão `auto` diz "ate 2 frames a frente quando o decode comeca"). Depois, veja os engasgos e a causa na linha do servidor (acima). `perda`/`pedido atrasado`: Wi-Fi (distância, canal de 2,4 GHz cheio, micro-ondas, Bluetooth); `captura`: o PC; `IDR` frequente: perdas seguidas. `--codec h264` aguenta perdas melhor (cada quadro é independente), com 2-3x mais banda |
| h264p liso, mas com FPS bem abaixo de 60 | com `prefetch=0`, cada frame espera o anterior ser exibido (~45 fps): use `auto`. Veja a `fonte` na linha do servidor: abaixo de 60, é a captura (`--source kms`). Até a v1.1, `auto` ficava em 52-55 com o Wi-Fi oscilando (o pedido chegava depois da captura seguinte) e o limite de `--fps` cortava frames de uma fonte com horários tremidos: atualize servidor e EBOOT |
| `--fps 40` não dá 40 | até a v1.1, o limite de `--fps` cortava frames com os horários da captura tremendo (~38) e o PSP pulava capturas (~35): atualize. 40 de uma tela de 60 Hz alterna 17 e 33 ms; para um movimento uniforme, `--fps 30` |
| som some depois de mexer na configuração ("som: erro no canal de audio") | EBOOT 1.1 antes da correção: o canal de som não era solto com som na fila. Atualize o EBOOT |
| captura em ~38-40 fps no GNOME 50 | use `--source kms` |
| KMS: "sem permissão para ler a tela" | `make -C tools/kms cap` de novo (depois de cada `make`); partições montadas com `nosuid` ignoram a permissão |
| controles não chegam | o log diz "controles desativados": configure o `/dev/uinput` (acima) |
| sem som | o log do servidor diz `som: ...` ao iniciar: sem `pulsesrc`/`adpcmenc`, instale `gstreamer1-plugins-good` e `gstreamer1-plugins-bad-free`. No PSP, a 5ª linha do overlay: "desligado" = SELECT + START + cima; "esperando o PC" = o servidor não está mandando. Só pelo UDP |
| som picotando | `vazio` subindo no overlay: Wi-Fi oscilando (o buffer aumenta sozinho até 120 ms). `--audio-rate 32000`, `22050` ou `--audio-mono` aliviam a rede |
| zumbido no som (EBOOT 1.1 antes da correção) | era o buffer de saída reaproveitado enquanto tocava; atualize o EBOOT |
| o PC continua tocando o som | o servidor grava o que sai nas caixas. Para o som ir só para o PSP: `pactl load-module module-null-sink sink_name=psp`, escolha "Null Output" como saída nas configurações de som e rode o servidor com `--audio-device psp.monitor` |
| o servidor avisa "EBOOT antigo" ou "não aceita frames P" | atualize o EBOOT (v1.0) |
| imagem torta ou com cores erradas (JPEG) | `decoder=sw` |
| algo estranho no H.264 do PC | `--h264-encoder gstreamer` usa o caminho antigo; mande o log |

## Para desenvolvedores

### Builds e releases (GitHub Actions)

`.github/workflows/build.yml`, a cada push e pull request:
- compila o EBOOT com o pspdev do Ubuntu;
- roda os testes e o `ruff`;
- gera o `.deb` (num Ubuntu 22.04) e o `.rpm` (num contêiner Fedora 43), e
  instala cada um para testar (`packaging/smoke-test.sh`: `--check`, stream
  com o PSP falso, interface web).

Os arquivos ficam nos "artifacts" da execução. Um push na `main` refaz a
pré-release `nightly`. O job "imagem Docker" compila a imagem do servidor,
a testa contra o Wolf falso e confere os compose e o instalador
(`packaging/docker-test.sh`, `compose-check.sh`); num push na `main`, publica
`ghcr.io/k7vinilstorage/pspstream:nightly`, e numa tag, `:1.2` e
`:latest`. Na primeira publicação, o pacote do ghcr.io nasce privado: em
GitHub → perfil → *Packages* → `pspstream` → *Package settings*, mude a
visibilidade para *Public*, para baixar sem login. Uma versão estável sai
de uma tag:

```sh
# no CHANGELOG.md, "## 1.2 (em desenvolvimento)" vira "## 1.2"; VERSION em
# server/pspstream.py e PSPSTREAM_VERSION em psp/src/version.h
git tag v1.2 && git push origin v1.2
```

A release usa a seção da versão no `CHANGELOG.md` como notas.

Para gerar os pacotes à mão: `packaging/build-deb.sh` (precisa de
`dpkg-deb`, `gcc`, `make`, `pkg-config` e `libdrm-dev`) e
`packaging/build-rpm.sh` (`rpm-build`, `gcc`, `make`, `libdrm-devel`). A
estrutura instalada é a mesma nos dois (`packaging/install-tree.sh`).

### Testes

```sh
python3 -m unittest discover tests                 # servidor: protocolo, encoders, controles, captura
python3 server/pspstream.py --source test &
python3 tools/fake_client.py --transport udp --h264p --seconds 10 --kbps 450 --decode-ms 11
```

`tools/fake_client.py` imita as threads do PSP (fila em ordem, NACK, IDR,
pedido antecipado e repetido, `--prefetch auto|on|off` como no `server.txt`,
janela de 2 frames; `--no-window` tira a janela) e confere que nenhum frame
P é decodificado sem o anterior; `--kbps`, `--rtt-ms`, `--rtt-jitter-ms`
(ida e volta oscilando, como no Wi-Fi), `--loss`, `--loss-up`,
`--loss-burst-ms` (rajadas de interferência) e `--decode-ms` simulam o Wi-Fi
e o PSP, e o resumo conta os engasgos. `FAKE_TRACE=1` mostra cada pedido, pedaço, perda e NACK. Os
números dele são simulados.

**PPSSPPHeadless** (compile o PPSSPP com `cmake -DHEADLESS=ON`; o H.264
precisa de `tools/ppsspp-pmp-fix.patch`):

```sh
PPSSPP_HEADLESS=/caminho/PPSSPPHeadless tools/emu_test.sh tela.png --source static
PPSSPP_HEADLESS=... python3 tools/emu_input_test.py   # controles; precisa de: pip install websocket-client
PPSSPP_HEADLESS=... python3 tools/emu_menu_test.py    # tela de configuração: procurar o PC, salvar, conectar
PPSSPP_HEADLESS=... python3 tools/emu_audio_test.py   # som: o PSP pede, desliga e liga pelo atalho
```

**Wolf**: `tests/test_wolf.py` roda contra `tests/fake_wolf.py`, que imita
a API num socket Unix (campos obrigatórios, `fmt::format` do pipeline, id
igual para as sessões sem cliente), o ping e os pipelines da sessão, com
`videotestsrc` e `audiotestsrc` no lugar do `interpipesrc`.
`packaging/docker-test.sh` faz o mesmo com a imagem Docker. Os bytes dos
pacotes de controle são conferidos contra as structs do Wolf.

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
  src/audio.c, ima.c   som: anel com buffer adaptativo, sceAudioSRC, decoder IMA ADPCM
  src/net.c            módulos de rede, Wi-Fi (apctl), TCP/UDP, broadcast
  src/display.c        framebuffer 8888, triple buffering, texto
  src/config.c         server.txt (ler e gravar)
  src/protocol.h       formato das mensagens (espelhado em server/protocol.py)
  probe/               teste do decoder H.264 no hardware
server/                servidor (Python 3)
  pspstream.py         sessões, linha de comando, benchmark
  capture.py           monta captura, som e controles (na partida e pela interface web)
  distro.py, doctor.py distribuição e pacotes; --check e --setup
  paths.py             arquivos no repositório ou instalados pelo pacote
  settings.py          configurações gerais: esquema, server.json, validação
  control.py           aplica as configurações com o servidor rodando
  web.py, web/         interface web (http.server da biblioteca padrão; HTML, CSS e JS sem dependências)
  gst_source.py        pipeline GStreamer (captura -> 480x272 -> JPEG/H.264/I420)
  kms.py, portal.py    captura KMS e pelo portal ScreenCast
  audio.py             som: captura (pulsesrc), IMA ADPCM (adpcmenc), pacotes
  h264.py, openh264.py encoders H.264 (libopenh264 direto por ctypes; GStreamer de reserva)
  transports.py        TCP e UDP (pedaços, NACK, reenvio)
  adaptive.py          qualidade adaptativa
  inject.py, gamepad.py, keymap.json   controles (uinput)
  wolf_api.py          Wolf: cliente da API (HTTP no socket Unix, só biblioteca padrão)
  wolf_source.py       Wolf: sessão, pipelines de vídeo e som, alvo, lobby, reconexão
  wolf_input.py        Wolf: controle de Xbox em pacotes do Moonlight (sessions/input)
  stats.py, sources.py, jpeginfo.py, protocol.py, netcheck.py
Dockerfile, docker/    imagem do servidor; compose (Wolf + PSPStream, NVIDIA, só o PSPStream), .env, install.sh
packaging/             .deb e .rpm (install-tree.sh, build-deb.sh, pspstream.spec, build-rpm.sh);
                       docker-test.sh (a imagem contra o Wolf falso), compose-check.sh
.github/workflows/     build do EBOOT, testes, pacotes e releases
tools/                 fake_client.py, emu_*.py/sh, h264_probe_clips.py, kms/ (auxiliar KMS)
docs/                  PROTOCOL.md, MEASUREMENTS.md, UBUNTU.md e WOLF.md (guias), WOLF-INTERNALS.md, WINDOWS.md (plano do servidor de Windows)
tests/                 testes do servidor e da interface web; fake_wolf.py imita o Wolf
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
- **Frames P pedidos quando o decode começa (`prefetch=auto`).** O próximo
  chega enquanto o atual decodifica, sem nunca ter dois frames na fila e sem
  pedir no meio de um frame chegando. ~60 fps lisos no PSP-3000. Pedir só
  depois de exibir dava ~45 fps. No UDP, o pedido autoriza até 2 frames à
  frente (v1.1): o servidor guarda o crédito e manda cada frame na hora da
  captura, sem esperar a ida e volta daquele pedido.
- **Perdas nos frames P.** Cada P precisa do anterior, então perder um
  pacote para o stream até o reenvio. O servidor manda o último pedaço de
  cada frame P de novo 6 ms depois (perder o último só era notado pelo
  silêncio). Sem a janela, o PSP repete o pedido de frame novo depois de 6
  ms, com o número do frame para o servidor reconhecer a cópia; com ela, o
  pedido seguinte cobre um perdido, e um frame que some inteiro é notado
  quando o seguinte chega (o PSP pede o reenvio na hora).
- **`--fps` numa grade fixa.** O limite conta a vez de cada frame a partir
  da vez anterior, não do frame que chegou: um frame atrasado pela captura
  não empurra os seguintes, e a taxa sai exata (o limite anterior cortava
  frames com os horários tremendo 2-3 ms).
- **Som em IMA ADPCM, empurrado.** 4 bits por amostra (~46 KB/s a 44,1 kHz
  estéreo, a taxa do PSP: o PSP não reamostra), codificado em C pelo
  `adpcmenc` (~2% de um núcleo no PC), e
  decodificado no CPU do PSP com somas e deslocamentos. MP3 ou ATRAC
  pesariam menos na rede, mas somariam 50-100 ms de atraso e disputariam o
  Media Engine com o H.264. O som não depende do pedido de vídeo (uma
  travada no vídeo não corta o som), e vai em pacotes de 20 ms: com 10 ms,
  seriam 100 pacotes por segundo disputando o ar do 802.11b com o vídeo.
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
  A interface web também não tem senha (por padrão, só abre no próprio PC).
- Servidor só para Linux por enquanto. O plano do servidor de Windows está
  em [docs/WINDOWS.md](docs/WINDOWS.md).
- Sem microfone (o som vai só do PC para o PSP).
- O som só vai pelo UDP (o padrão).
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
