# PSPStream

[![build](https://github.com/k7vinilstorage/PSP-Stream/actions/workflows/build.yml/badge.svg)](https://github.com/k7vinilstorage/PSP-Stream/actions/workflows/build.yml)

**English** · [Português](README.pt-BR.md)

Streams the PC screen and audio to a PSP over Wi-Fi and sends the PSP
buttons back to the PC, as keyboard and mouse or as an Xbox controller. The
video goes as H.264 with P frames, decoded by the PSP hardware, and the
audio as IMA ADPCM. Inspired by
[RNDS-Stream](https://github.com/gavff64/RNDS-Stream), which does the same
for the Nintendo DSi.

```
 PC (Linux, Wayland)                                     PSP (homebrew)
 ┌───────────────────────────────────────┐   Wi-Fi     ┌──────────────────────────────┐
 │ KMS capture (60 fps) or portal        │   802.11b   │ network thread: UDP chunks,  │
 │ GPU: downscale to 480x272             │ ──H.264──>  │   NACK, in-order queue       │
 │ openh264: P frame when asked          │             │ Media Engine: H.264 decode   │
 │ quality adapted to the bandwidth      │ <─request─  │   straight into VRAM         │
 │ uinput: keyboard/mouse or virtual Xbox│  + buttons  │ settings screen              │
 └───────────────────────────────────────┘             └──────────────────────────────┘
```

The full documentation is in the **[wiki](https://github.com/k7vinilstorage/PSP-Stream/wiki)**.

## Features

- **Close to 60 smooth fps on the PSP-3000** with H.264 with P frames,
  decoded by the PSP Media Engine. With a still screen, each frame is ~100
  bytes.
- **60 fps capture** through KMS (straight from the graphics card) or the
  Wayland portal (GNOME, KDE).
- **PC audio** on the PSP: IMA ADPCM at 44.1 kHz stereo, ~46 KB/s.
- **Controls**: a virtual Xbox 360 controller, as in Sunshine, or keyboard
  and mouse.
- **UDP with loss recovery** and quality adapted to the Wi-Fi throughput.
- **Settings screen on the PSP**, with "Find the PC on the network".
- **Web interface** on the PC to change the settings with the PSP connected.
- **Wolf (Games on Whales)**: the Wolf lobby on the PSP, with audio and
  controls, in a container next to it.
- **Windows server (experimental)**: a zip with `pspstream.exe`, screen
  capture through Desktop Duplication, audio, keyboard/mouse and the Xbox
  controller (with the ViGEmBus driver).
- **English by default, Portuguese as an option**: server, web interface,
  installer and the PSP screens.

## How it works

The PSP **asks** for each frame, and the server answers with the most
recent one, encoded on the spot (RNDS-Stream's "pull" model). That way no
queue ever builds up on the network, and latency stays close to one frame.

1. The server captures the screen, downscales it to 480x272 on the GPU and
   encodes it as H.264 (or JPEG, for old EBOOTs) when the request arrives.
2. The frame goes in UDP chunks. A lost chunk is asked for again (NACK),
   and the quality adjusts to the Wi-Fi throughput.
3. The PSP decodes on the Media Engine, straight into video memory, and
   already asks for the next one while it decodes the current one.
4. The buttons go along with each request and become keyboard, mouse or an
   Xbox controller on the PC (uinput).
5. The audio goes separately, in 20 ms packets, and does not depend on the
   video.

The details are on the
[Protocol](https://github.com/k7vinilstorage/PSP-Stream/wiki/Protocol) and
[Design Decisions](https://github.com/k7vinilstorage/PSP-Stream/wiki/Design-Decisions)
pages.

## Quick start

You need:
- a PSP with custom firmware;
- a Linux PC (Fedora, Ubuntu, Debian, Arch, openSUSE), or Windows 10/11
  (experimental, [Windows](https://github.com/k7vinilstorage/PSP-Stream/wiki/Windows));
- a router with 2.4 GHz Wi-Fi in b/g/n mixed mode, because the PSP only
  speaks 802.11b.

### 1. On the PSP

1. Download
   [`PSPStream-EBOOT.zip`](https://github.com/k7vinilstorage/PSP-Stream/releases/latest/download/PSPStream-EBOOT.zip)
   and copy the `PSP` folder to the root of the memory stick.
2. In the XMB, create the connection to the router and, in **Power Save
   Settings**, leave **WLAN Power Save** off.

### 2. On the PC

With the packages from
[Releases](https://github.com/k7vinilstorage/PSP-Stream/releases)
(`sudo apt install ./pspstream_*.deb` or `sudo dnf install ./pspstream-*.rpm`),
or from source, on any distribution:

```sh
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
python3 server/pspstream.py --setup      # installs what is missing, asking first
python3 server/pspstream.py --source kms --profile xbox
```

With the package, the command is `pspstream`. `--setup` takes care of the
packages, the controls permission, the firewall (port 5123 UDP and TCP) and
the KMS capture. To only check, use `--check`.

### 3. Connect

Open PSPStream on the PSP, pick **Find the PC on the network** (X) and
press **START**. The next times, it connects by itself. The PC settings
are at **http://localhost:5124**.

### On Windows (experimental)

Download `PSPStream-Setup-x64.exe` from
[Releases](https://github.com/k7vinilstorage/PSP-Stream/releases) and run it:
the wizard installs PSPStream, allows it through the firewall and, if you
want the Xbox controller, installs the ViGEmBus driver. Then open
**PSPStream** from the Start menu. A portable zip is there too. Details on
the [Windows](https://github.com/k7vinilstorage/PSP-Stream/wiki/Windows) page.

### With Wolf (Games on Whales)

On a server with Docker, the installer starts Wolf and PSPStream:

```sh
curl -fsSLO https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/install.sh
sudo bash install.sh
```

To put PSPStream next to an existing Wolf, or to deploy through Portainer,
see the [Wolf](https://github.com/k7vinilstorage/PSP-Stream/wiki/Wolf)
page.

## Language

Everything is in English by default. For Portuguese:

| where | how |
|---|---|
| server and web interface | `--lang pt`, `PSPSTREAM_LANG=pt` or **Language** in the web interface |
| PSP | `lang=pt` in `server.txt` or **Language / Idioma** on the settings screen |
| Docker installer | `sudo bash install.sh --lang pt` |

More in [Server Options](https://github.com/k7vinilstorage/PSP-Stream/wiki/Server-Options#language).

## PSP shortcuts

Hold **SELECT + START** and press:

| button | does |
|---|---|
| triangle | overlay on/off (FPS, KB per frame, timings) |
| up | audio on/off |
| R | opens the settings screen |
| L | switches the transport TCP/UDP |

The other shortcuts and the overlay are in
[Using the PSP](https://github.com/k7vinilstorage/PSP-Stream/wiki/Using-the-PSP).
The Xbox controller buttons are in
[Controls](https://github.com/k7vinilstorage/PSP-Stream/wiki/Controls).

## Documentation

| page | what it has |
|---|---|
| [Installation](https://github.com/k7vinilstorage/PSP-Stream/wiki/Installation) | requirements, packages per distribution, firewall, uinput, KMS capture, building the EBOOT |
| [Using the PSP](https://github.com/k7vinilstorage/PSP-Stream/wiki/Using-the-PSP) | settings screen, shortcuts, overlay, `server.txt` |
| [Controls](https://github.com/k7vinilstorage/PSP-Stream/wiki/Controls) | Xbox controller, keyboard and mouse, your own profiles |
| [Web Interface](https://github.com/k7vinilstorage/PSP-Stream/wiki/Web-Interface) | the settings in the browser, on the network with a password |
| [Wolf](https://github.com/k7vinilstorage/PSP-Stream/wiki/Wolf) | Docker, installer, Portainer, Start and Coop lobbies |
| [Server Options](https://github.com/k7vinilstorage/PSP-Stream/wiki/Server-Options) | the command line, the language and the stats line |
| [Troubleshooting](https://github.com/k7vinilstorage/PSP-Stream/wiki/Troubleshooting) | symptom and what to do |
| [Performance](https://github.com/k7vinilstorage/PSP-Stream/wiki/Performance) | FPS, latency and bandwidth measured on the PSP-3000 |
| [Windows](https://github.com/k7vinilstorage/PSP-Stream/wiki/Windows) | the server on Windows (experimental) |
| [Development](https://github.com/k7vinilstorage/PSP-Stream/wiki/Development) | builds, releases, tests, translations, code layout |

All the pages, including the protocol, the measurements and the
limitations, are in the [wiki](https://github.com/k7vinilstorage/PSP-Stream/wiki).
They are generated from the [`wiki/`](wiki) folder of this repository. The
version history is in [CHANGELOG.md](CHANGELOG.md).

## Credits

- [RNDS-Stream](https://github.com/gavff64/RNDS-Stream): the idea of the
  pull model and of the server + homebrew.
- [pspdev](https://github.com/pspdev): toolchain and PSPSDK.
- [openh264](https://www.openh264.org/) (Cisco), libjpeg-turbo, GStreamer,
  PPSSPP (emulator tests).
- PMP Mod/PMPlayer: the raw H.264 decode path on the PSP
  (`sceMpegBasePESpacketCopy`).
- [Sunshine](https://github.com/LizardByte/Sunshine): reference for the KMS
  capture and the virtual controller.
- [Wolf](https://github.com/games-on-whales/wolf) (Games on Whales): the API
  and the lobbies `--source wolf` uses.
- [Moonlight](https://moonlight-stream.org/): the controller packet format
  Wolf receives.

## License

[MIT](LICENSE), © 2026 João Torezan.
