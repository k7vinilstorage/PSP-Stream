O que é preciso, os downloads prontos e a instalação pelo código. No Ubuntu
há um [guia passo a passo](Guia-do-Ubuntu). Para usar com o Wolf (Games on
Whales), num servidor com Docker, veja a página [Wolf](Wolf).

## Requisitos

| | testado | deve funcionar |
|---|---|---|
| PSP | PSP-3000, firmware 6.61 com ARK-4 | qualquer PSP com firmware customizado que rode homebrew |
| PC | Fedora 44, GNOME 50 (Wayland), Intel Gen12; instalação e servidor também num Ubuntu 24.04 | qualquer Linux com Python 3.10+, GStreamer 1.20+, PipeWire ou PulseAudio e o portal ScreenCast (ou X11); KMS precisa de libdrm |
| rede | roteador em modo misto b/g/n, WPA2 | o PSP só fala 802.11b em 2,4 GHz |

## Downloads prontos

Em [Releases](https://github.com/k7vinilstorage/PSP-Stream/releases), sem compilar nada:

| arquivo | o que é |
|---|---|
| `PSPStream-EBOOT.zip` | o app do PSP: copie a pasta `PSP` para a raiz do memory stick |
| `pspstream_*.deb` | servidor para Ubuntu 22.04+ e Debian 12+: `sudo apt install ./pspstream_*.deb` |
| `pspstream-*.rpm` | servidor para o Fedora: `sudo dnf install ./pspstream-*.rpm` |

A release mais recente é a versão estável
([EBOOT direto](https://github.com/k7vinilstorage/PSP-Stream/releases/latest/download/PSPStream-EBOOT.zip)).
A pré-release **nightly** é refeita a cada mudança na `main`
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
(`sudo setcap cap_sys_admin+ep /usr/libexec/pspstream/pspstream-kms`). Dada
uma vez, ela continua depois das atualizações do pacote (os pacotes mais
antigos a perdiam a cada atualização: nesse caso, rode o comando de novo).

## PC, pelo código

O jeito mais simples, em qualquer distribuição:

```sh
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
python3 server/pspstream.py --setup
```

O `--setup` descobre a distribuição (Ubuntu/Debian e derivadas, Fedora,
Arch, openSUSE), mostra cada comando (pacotes, permissão dos controles,
firewall, captura KMS opcional) e pergunta antes de rodar. Para só
conferir, sem mudar nada: `python3 server/pspstream.py --check`.

### Pacotes à mão

| distribuição | comando |
|---|---|
| **Ubuntu, Debian, Mint, Pop!_OS** ([guia completo](Guia-do-Ubuntu)) | `sudo apt install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-pipewire gstreamer1.0-gl libopenh264-dev python3-evdev pulseaudio-utils` |
| **Fedora** | `sudo dnf install python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugins-bad-free pipewire-gstreamer python3-evdev gstreamer1-plugin-openh264` |
| **Arch, Manjaro, EndeavourOS** (não testado) | `sudo pacman -S --needed python-gobject gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugin-pipewire openh264 python-evdev libpulse` |
| **openSUSE** (não testado) | `sudo zypper install python3-gobject typelib-1_0-Gst-1_0 typelib-1_0-GstVideo-1_0 typelib-1_0-GstAllocators-1_0 gstreamer-plugins-base gstreamer-plugins-good gstreamer-plugins-bad gstreamer-plugin-pipewire libopenh264-7 python3-evdev pulseaudio-utils` |

- A `libopenh264` (H.264 com frames P) vem do pacote da distribuição. No
  Fedora, `gstreamer1-plugin-openh264` vem do repositório
  `fedora-cisco-openh264`, já ativo no Fedora Workstation, e traz a
  biblioteca junto. Sem pacote, a do Cisco serve
  ([Guia do Ubuntu](Guia-do-Ubuntu#1-pacotes)); sem nenhuma, o servidor
  manda JPEG.
- `evdev`: os controles (uinput).
- `pulsesrc` (plugins good) e `adpcmenc` (plugins bad): o som. Sem eles, o
  servidor avisa e roda sem som.
- Use o Python do sistema: o PyGObject do pacote não aparece num venv,
  conda ou pyenv (ou crie o venv com `--system-site-packages`).

### Firewall

O Fedora (firewalld) bloqueia conexões de entrada por padrão; o Ubuntu (ufw)
vem com o firewall desligado. O `--check` diz qual está ativo e o comando:

```sh
sudo firewall-cmd --permanent --add-port=5123/tcp --add-port=5123/udp && sudo firewall-cmd --reload   # firewalld
sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp                                                      # ufw
```

### Controles (uinput)

O servidor cria teclado, mouse ou controle virtuais pelo `/dev/uinput`, e
seu usuário precisa poder escrever nele (a mesma regra do RNDS-Stream):

```sh
test -w /dev/uinput && echo "Pronto" || echo "Precisa configurar"
# se precisar:
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
printf '%s\n' 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/60-pspstream-uinput.rules
sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger
```

Sem o uinput, o servidor avisa e transmite sem os controles. Os botões de
cada perfil estão em [Controles](Controles).

### Captura KMS (recomendada no GNOME 50)

Pelo portal, o GNOME 50 entrega no máximo ~40 fps (um limitador do próprio
GNOME). A captura KMS lê a imagem que a placa de vídeo está mostrando, como
a do Sunshine, e chega a 60 fps. Ela usa um auxiliar pequeno com permissão
de administrador (`CAP_SYS_ADMIN`):

```sh
sudo apt install gcc make pkg-config libdrm-dev libcap2-bin   # Ubuntu/Debian
sudo dnf install gcc make libdrm-devel libcap                 # Fedora
make -C tools/kms          # compila tools/kms/pspstream-kms
make -C tools/kms cap      # sudo setcap cap_sys_admin+ep (refaça depois de cada make)
```

Cada `make` troca o arquivo e apaga a permissão: rode o `make ... cap` de
novo. Só o auxiliar tem a permissão, e ele faz uma coisa só: exporta o
buffer da tela como DMA-BUF. A redução para 480x272 roda no servidor, sem
privilégio, no OpenGL. O cursor do mouse não aparece (fica num plano separado da placa),
e a captura é do monitor inteiro (`--kms-monitor 1` escolhe o segundo).

## PSP

O `PSPStream-EBOOT.zip` das
[Releases](https://github.com/k7vinilstorage/PSP-Stream/releases) é o EBOOT
pronto. Para compilar, use o [pspdev](https://pspdev.github.io/installation/fedora.html)
(Fedora abaixo; Ubuntu no [Guia do Ubuntu](Guia-do-Ubuntu#9-compilar-o-eboot-no-ubuntu-opcional)):

```sh
sudo dnf -y install @development-tools cmake bsdtar libusb-compat-0.1 gpgme2 fakeroot xz
curl -LO https://github.com/pspdev/pspdev/releases/latest/download/pspdev-fedora-latest.tar.gz
tar xzf pspdev-fedora-latest.tar.gz -C ~
cd psp && make dist      # dist/PSP/GAME/PSPStream/{EBOOT.PBP,server.txt}
```

Os Makefiles acham o pspdev em `~/pspdev` ou `/usr/local/pspdev` sem nenhum
`export`; em outro lugar, use `make PSPDEV=/caminho`.

No PSP:

1. Copie a pasta `PSP` (do zip, ou `dist/PSP`) para a raiz do memory stick
   (`ms0:/PSP/GAME/PSPStream/EBOOT.PBP`).
2. No XMB, em **Ajustes > Ajustes de rede > Modo infraestrutura**, crie a
   conexão com o roteador.
3. Em **Ajustes > Ajustes de economia de energia > Economia de energia
   WLAN**, escolha **Desligado**. Ligada, ela desliga o rádio entre beacons e
   a latência cresce muito (o app avisa).
4. Ligue a chave WLAN, na lateral do PSP.

Depois: [Uso no PSP](Uso-no-PSP).
