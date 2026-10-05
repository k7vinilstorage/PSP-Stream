# PSPStream no Ubuntu

Vale para o Ubuntu 22.04 e 24.04 (e mais novos), e para quem vem do Ubuntu
ou do Debian: Debian 12+, Linux Mint 21+, Pop!_OS, Zorin, elementary OS.

O que foi testado num Ubuntu 24.04: os pacotes (pelo próprio `apt`), o
`--check`, os testes automáticos (também com o Python 3.10 do 22.04), o
servidor com a fonte de teste, o EBOOT compilado com o pspdev do Ubuntu e
rodando no emulador. A captura da tela de verdade, o som e os controles
foram testados num PSP-3000 com o Fedora; no Ubuntu, são os mesmos
componentes (GStreamer, PipeWire, uinput).

## Resumo

```sh
sudo apt install git python3
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
python3 server/pspstream.py --setup      # instala e configura o que falta, perguntando antes de cada passo
python3 server/pspstream.py              # e no PSP: "Procurar o PC na rede"
```

As configurações ficam no navegador, em **http://localhost:5124**.

O `--setup` mostra cada comando antes de rodar (pacotes, permissão dos
controles, firewall, captura KMS opcional) e pergunta. No fim, ele confere
tudo de novo. Para só conferir, sem mudar nada: `--check`.

## 1. Pacotes

Se preferir instalar à mão:

```sh
sudo apt install git python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-pipewire gstreamer1.0-gl libopenh264-dev python3-evdev pulseaudio-utils
```

| pacote | para quê |
|---|---|
| `python3-gi`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0` | o servidor fala com o GStreamer pelo PyGObject |
| `gstreamer1.0-plugins-base` | redução para 480x272, conversão de cor |
| `gstreamer1.0-plugins-good` | `pulsesrc` (som), `jpegenc` (JPEG), `ximagesrc` (X11) |
| `gstreamer1.0-plugins-bad` | `adpcmenc` (som) |
| `gstreamer1.0-pipewire` | captura pelo portal do Wayland (o padrão) |
| `gstreamer1.0-gl` | captura KMS (redução na GPU) |
| `libopenh264-dev` | puxa a `libopenh264` (a `-7` no 24.04): H.264 com frames P. Sem ela, o servidor manda JPEG, ~10x mais bytes |
| `python3-evdev` | controles (teclado, mouse e controle de Xbox virtuais) |
| `pulseaudio-utils` | `pactl`: acha o que sai nas caixas e lista as fontes de som |

**Use o Python do sistema** (`/usr/bin/python3`). O `python3-gi` só existe
para ele: num venv, conda ou pyenv, o `import gi` falha. Se precisar de um
venv, crie com `python3 -m venv --system-site-packages`.

**Sem `libopenh264-dev` no apt** (versões antigas): use a biblioteca do
próprio Cisco. O nome do arquivo de cada versão está em
<https://github.com/cisco/openh264/releases>; por exemplo:

```sh
mkdir -p ~/.local/lib && cd ~/.local/lib
curl -LO http://ciscobinary.openh264.org/libopenh264-2.4.1-linux64.7.so.bz2
bunzip2 libopenh264-2.4.1-linux64.7.so.bz2
```

O servidor procura em `~/.local/lib`, em `lib/` dentro do projeto, ou no
caminho da variável `PSPSTREAM_OPENH264`.

## 2. Conferir

```sh
python3 server/pspstream.py --check
```

Ele lista o que está pronto (`ok`), o que limita alguma coisa (`aviso`) e o
que impede o servidor de abrir (`falta`), e termina com o comando do `apt`
para o que falta. Exemplo de um Ubuntu 24.04 com tudo instalado, menos os
controles:

```
GStreamer
  ok     GStreamer 1.24.2 e PyGObject
  ok     pipewiresrc: captura pelo portal (o padrão no Wayland)
  ...
Vídeo
  ok     libopenh264 2.4.1: H.264 com frames P (o padrão)
Controles
  aviso  sem permissão de escrita no /dev/uinput (o servidor transmite sem os controles)
```

## 3. Controles (uinput)

O servidor cria teclado, mouse ou controle de Xbox virtuais pelo
`/dev/uinput`, e o seu usuário precisa poder escrever nele. O `--setup` faz
isto; à mão:

```sh
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
printf '%s\n' 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/60-pspstream-uinput.rules
sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger
```

A regra dá acesso só a quem está sentado no PC (sessão ativa), sem grupo
novo nem reiniciar.

## 4. Captura da tela

- **Ubuntu com Wayland** (o padrão desde o 22.04, com GNOME): a captura é
  pelo portal. Na primeira vez, o GNOME pergunta qual monitor ou janela
  transmitir. Com o portal ScreenCast v4 ou mais novo, a escolha fica salva;
  o `--check` diz a versão.
- **"Ubuntu no Xorg"** (escolhido na engrenagem da tela de login): use
  `--source x11`.
- **Captura KMS**: direto da placa de vídeo, como a do Sunshine. Vale a pena
  quando a linha do servidor mostra a `fonte` abaixo de 60 fps: a partir do
  GNOME 50 (`gnome-shell --version`), o portal fica em ~40 fps. Precisa de um
  auxiliar com permissão para ler a tela:

  ```sh
  sudo apt install gcc make pkg-config libdrm-dev libcap2-bin
  make -C tools/kms && make -C tools/kms cap    # refaça o "cap" depois de cada make
  python3 server/pspstream.py --source kms
  ```

  O cursor do mouse não aparece na captura KMS. Com o driver proprietário
  da NVIDIA, a captura KMS não foi testada.

## 5. Som

Funciona com o PipeWire (padrão desde o 22.10) e com o PulseAudio (o 22.04):
o servidor grava o que sai nas caixas. Para o som ir só para o PSP, sem
tocar no PC:

```sh
pactl load-module module-null-sink sink_name=psp
```

Escolha "Null Output" como saída nas configurações de som do Ubuntu, e
rode o servidor com `--audio-device psp.monitor` (ou escolha a fonte na
interface web).

## 6. Firewall

O `ufw` vem desligado no Ubuntu Desktop. Se você ligou (`sudo ufw status`
diz "active"), libere a porta:

```sh
sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp
```

Se o PSP não achar o PC mesmo assim, veja se o roteador não isola os
aparelhos da rede (rede de convidados, "AP isolation"). Nesse caso, o
"Procurar o PC" não funciona, mas o IP digitado no PSP também não.

## 7. Usar

```sh
python3 server/pspstream.py                         # portal, perfil "jogo" (teclado e mouse)
python3 server/pspstream.py --source kms --profile xbox
```

Depois, no PSP, abra o PSPStream e escolha **Procurar o PC na rede**. O
resto é igual ao README: atalhos, overlay, interface web.

## 8. Iniciar junto com a sessão (opcional)

Um serviço de usuário do systemd sobe o servidor quando você entra na
sessão gráfica. Rode o servidor uma vez à mão antes, para escolher a tela
no portal (a escolha fica salva). Não foi testado nesta máquina, que não
tem sessão gráfica.

```sh
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/pspstream.service <<EOF
[Unit]
Description=PSPStream (tela do PC no PSP)
PartOf=graphical-session.target
After=graphical-session.target pipewire.service

[Service]
ExecStart=/usr/bin/python3 $PWD/server/pspstream.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF
systemctl --user daemon-reload && systemctl --user enable --now pspstream
journalctl --user -u pspstream -f      # o log
```

(Rode dentro da pasta do PSPStream: o `$PWD` vira o caminho dela.)

## 9. Compilar o EBOOT no Ubuntu (opcional)

Foi assim que o EBOOT foi compilado nos testes:

```sh
sudo apt install build-essential cmake pkgconf libreadline8 libusb-0.1-4 libgpgme11 libarchive-tools fakeroot
curl -LO https://github.com/pspdev/pspdev/releases/latest/download/pspdev-ubuntu-latest-x86_64.tar.gz
tar xzf pspdev-ubuntu-latest-x86_64.tar.gz -C ~
cd psp && make dist      # dist/PSP/GAME/PSPStream/{EBOOT.PBP,server.txt}
```

Copie `dist/PSP` para a raiz do memory stick e siga o README (Wi-Fi e
economia de energia WLAN do PSP).

## 10. Problemas comuns no Ubuntu

| sintoma | o que fazer |
|---|---|
| `No module named 'gi'` | o Python não é o do sistema (venv, conda, pyenv): use `/usr/bin/python3`, ou um venv com `--system-site-packages` |
| `no element "pipewiresrc"` | `sudo apt install gstreamer1.0-pipewire` |
| "codec: JPEG (sem o openh264...)" | `sudo apt install libopenh264-dev`, ou a biblioteca do Cisco (seção 1) |
| "portal ScreenCast indisponível" | numa sessão Xorg, use `--source x11`; no Wayland, `sudo apt install xdg-desktop-portal xdg-desktop-portal-gnome` (KDE: `-kde`; Sway: `-wlr`) e saia e entre de novo na sessão |
| o portal pergunta a tela toda vez | portal ScreenCast anterior ao v4 (o `--check` diz): use `--source kms`, ou responda o diálogo |
| "controles desativados" | seção 3; confira com `--check` |
| sem som, "faltam o pulsesrc e o adpcmenc" | `sudo apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-bad` |
| o PSP não acha o PC | seção 6 (firewall e isolamento do roteador); o PC e o PSP têm de estar na mesma rede |
| "Firewall do PC liberado?" na tela do PSP | o PSP não recebe resposta: servidor rodando? `--check` no PC, e a seção 6 |
