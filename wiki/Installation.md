What you need, the ready-made downloads and installing from source. On
Ubuntu there is a [step-by-step guide](Ubuntu-Guide). To use it with Wolf
(Games on Whales), on a server with Docker, see the [Wolf](Wolf) page. For
the server on Windows, see [Windows](Windows).

## Requirements

| | tested | should work |
|---|---|---|
| PSP | PSP-3000, firmware 6.61 with ARK-4 | any PSP with custom firmware that runs homebrew |
| PC | Fedora 44, GNOME 50 (Wayland), Intel Gen12; install and server also on Ubuntu 24.04 | any Linux with Python 3.10+, GStreamer 1.20+, PipeWire or PulseAudio and the ScreenCast portal (or X11); KMS needs libdrm |
| network | router in b/g/n mixed mode, WPA2 | the PSP only speaks 802.11b on 2.4 GHz |

## Ready-made downloads

In [Releases](https://github.com/k7vinilstorage/PSP-Stream/releases), with nothing to build:

| file | what it is |
|---|---|
| `PSPStream-EBOOT.zip` | the PSP app: copy the `PSP` folder to the root of the memory stick |
| `pspstream_*.deb` | server for Ubuntu 22.04+ and Debian 12+: `sudo apt install ./pspstream_*.deb` |
| `pspstream-*.rpm` | server for Fedora: `sudo dnf install ./pspstream-*.rpm` |
| `PSPStream-Windows-x64.zip` | server for Windows 10/11, experimental: unzip and run `pspstream.exe` ([Windows](Windows)) |

The most recent release is the stable version
([EBOOT directly](https://github.com/k7vinilstorage/PSP-Stream/releases/latest/download/PSPStream-EBOOT.zip)).
The **nightly** pre-release is rebuilt on every change to `main`
([nightly EBOOT](https://github.com/k7vinilstorage/PSP-Stream/releases/download/nightly/PSPStream-EBOOT.zip)).

With the package, the server becomes the `pspstream` command
(`pspstream --check`, `pspstream --source kms`...), and the package already
brings:
- the `/dev/uinput` permission for the controls, for whoever sits at the PC;
- the shortcut in the applications menu;
- the `systemctl --user enable --now pspstream` service, to start with the
  session;
- the firewall rule: `sudo ufw allow PSPStream` or
  `sudo firewall-cmd --permanent --add-service=pspstream && sudo firewall-cmd --reload`.

The KMS capture comes built, but without the permission to read the screen,
which only the administrator can give: `pspstream --setup` offers the
command (`sudo setcap cap_sys_admin+ep /usr/libexec/pspstream/pspstream-kms`).
Given once, it stays after package updates (older packages lost it on every
update: in that case, run the command again).

The server speaks English; for Portuguese, `pspstream --lang pt` or
**Language** in the web interface ([Language](Server-Options#language)).

## PC, from source

The simplest way, on any distribution:

```sh
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
python3 server/pspstream.py --setup
```

`--setup` finds out the distribution (Ubuntu/Debian and derivatives,
Fedora, Arch, openSUSE), shows each command (packages, controls permission,
firewall, optional KMS capture) and asks before running it. To only check,
without changing anything: `python3 server/pspstream.py --check`.

### Packages by hand

| distribution | command |
|---|---|
| **Ubuntu, Debian, Mint, Pop!_OS** ([full guide](Ubuntu-Guide)) | `sudo apt install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-pipewire gstreamer1.0-gl libopenh264-dev python3-evdev pulseaudio-utils` |
| **Fedora** | `sudo dnf install python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugins-bad-free pipewire-gstreamer python3-evdev gstreamer1-plugin-openh264` |
| **Arch, Manjaro, EndeavourOS** (not tested) | `sudo pacman -S --needed python-gobject gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugin-pipewire openh264 python-evdev libpulse` |
| **openSUSE** (not tested) | `sudo zypper install python3-gobject typelib-1_0-Gst-1_0 typelib-1_0-GstVideo-1_0 typelib-1_0-GstAllocators-1_0 gstreamer-plugins-base gstreamer-plugins-good gstreamer-plugins-bad gstreamer-plugin-pipewire libopenh264-7 python3-evdev pulseaudio-utils` |

- `libopenh264` (H.264 with P frames) comes from the distribution package.
  On Fedora, `gstreamer1-plugin-openh264` comes from the
  `fedora-cisco-openh264` repository, already enabled on Fedora Workstation,
  and brings the library with it. Without a package, Cisco's works
  ([Ubuntu Guide](Ubuntu-Guide#1-packages)); with none, the server sends
  JPEG.
- `evdev`: the controls (uinput).
- `pulsesrc` (good plugins) and `adpcmenc` (bad plugins): the audio. Without
  them, the server warns and runs without audio.
- Use the system Python: the packaged PyGObject does not show up in a venv,
  conda or pyenv (or create the venv with `--system-site-packages`).

### Firewall

Fedora (firewalld) blocks incoming connections by default; Ubuntu (ufw)
ships with the firewall off. `--check` says which one is active and the
command:

```sh
sudo firewall-cmd --permanent --add-port=5123/tcp --add-port=5123/udp && sudo firewall-cmd --reload   # firewalld
sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp                                                      # ufw
```

### Controls (uinput)

The server creates a virtual keyboard, mouse or controller through
`/dev/uinput`, and your user must be able to write to it (the same rule as
RNDS-Stream):

```sh
test -w /dev/uinput && echo "Ready" || echo "Needs setup"
# if needed:
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
printf '%s\n' 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/60-pspstream-uinput.rules
sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger
```

Without uinput, the server warns and streams without the controls. The
buttons of each profile are in [Controls](Controls).

### KMS capture (recommended on GNOME 50)

Through the portal, GNOME 50 delivers at most ~40 fps (a limiter in GNOME
itself). The KMS capture reads the image the graphics card is showing, like
Sunshine's, and reaches 60 fps. It uses a small helper with administrator
permission (`CAP_SYS_ADMIN`):

```sh
sudo apt install gcc make pkg-config libdrm-dev libcap2-bin   # Ubuntu/Debian
sudo dnf install gcc make libdrm-devel libcap                 # Fedora
make -C tools/kms          # builds tools/kms/pspstream-kms
make -C tools/kms cap      # sudo setcap cap_sys_admin+ep (redo it after each make)
```

Each `make` replaces the file and wipes the permission: run `make ... cap`
again. Only the helper has the permission, and it does one thing only: it
exports the screen buffer as a DMA-BUF. The downscale to 480x272 runs in
the server, without privileges, in OpenGL. The mouse cursor does not show
(it lives on a separate plane of the card), and the capture is of the whole
monitor (`--kms-monitor 1` picks the second one).

## PSP

The `PSPStream-EBOOT.zip` from
[Releases](https://github.com/k7vinilstorage/PSP-Stream/releases) is the
ready EBOOT. To build it, use [pspdev](https://pspdev.github.io/installation/fedora.html)
(Fedora below; Ubuntu in the [Ubuntu Guide](Ubuntu-Guide#9-building-the-eboot-on-ubuntu-optional)):

```sh
sudo dnf -y install @development-tools cmake bsdtar libusb-compat-0.1 gpgme2 fakeroot xz
curl -LO https://github.com/pspdev/pspdev/releases/latest/download/pspdev-fedora-latest.tar.gz
tar xzf pspdev-fedora-latest.tar.gz -C ~
cd psp && make dist      # dist/PSP/GAME/PSPStream/{EBOOT.PBP,server.txt}
```

The Makefiles find pspdev in `~/pspdev` or `/usr/local/pspdev` without any
`export`; elsewhere, use `make PSPDEV=/path`.

On the PSP:

1. Copy the `PSP` folder (from the zip, or `dist/PSP`) to the root of the
   memory stick (`ms0:/PSP/GAME/PSPStream/EBOOT.PBP`).
2. In the XMB, under **Settings > Network Settings > Infrastructure Mode**,
   create the connection to the router.
3. Under **Settings > Power Save Settings > WLAN Power Save**, pick **Off**.
   When on, it turns the radio off between beacons and latency grows a lot
   (the app warns).
4. Turn on the WLAN switch, on the side of the PSP.

The PSP screens are in English; for Portuguese, `lang=pt` in `server.txt`
or **Language / Idioma** on the settings screen.

Next: [Using the PSP](Using-the-PSP).
