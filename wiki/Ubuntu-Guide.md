This applies to Ubuntu 22.04 and 24.04 (and newer), and to systems that come
from Ubuntu or Debian: Debian 12+, Linux Mint 21+, Pop!_OS, Zorin,
elementary OS.

What was tested on Ubuntu (without a screen: containers and CI):
- the `.deb` package installed by `apt` on clean 22.04 and 24.04, with a
  stream to the test PSP in JPEG and in H.264 with P frames, and the web
  interface;
- `--check` and the automated tests (also with the Python 3.10 of 22.04);
- the EBOOT built with Ubuntu's pspdev and running in the emulator.

Real screen capture, audio and controls were tested on a PSP-3000 with
Fedora; on Ubuntu, they are the same components (GStreamer, PipeWire,
uinput).

## Summary

The easiest way is the package: download `pspstream_*.deb` from
[Releases](https://github.com/k7vinilstorage/PSP-Stream/releases) and install it with apt (it pulls the dependencies):

```sh
sudo apt install ./pspstream_*.deb
pspstream --setup      # KMS capture permission (optional) and firewall, if needed
pspstream              # and on the PSP: "Find the PC on the network"
```

The ready EBOOT is there too (`PSPStream-EBOOT.zip`). The package was
installed and tested on a clean Ubuntu 22.04: apt installed the
dependencies and `libopenh264`, and the server streamed to the test PSP in
JPEG and H.264 with P frames.

From source:

```sh
sudo apt install git python3
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
python3 server/pspstream.py --setup      # installs and configures what is missing, asking before each step
python3 server/pspstream.py              # and on the PSP: "Find the PC on the network"
```

The settings live in the browser, at **http://localhost:5124**.

`--setup` shows each command before running it (packages, controls
permission, firewall, optional KMS capture) and asks. At the end, it checks
everything again. To only check, without changing anything: `--check`.

The server speaks English; `--lang pt` switches it to Portuguese
([Language](Server-Options#language)).

## 1. Packages

If you prefer to install by hand:

```sh
sudo apt install git python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-pipewire gstreamer1.0-gl libopenh264-dev python3-evdev pulseaudio-utils
```

| package | what for |
|---|---|
| `python3-gi`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0` | the server talks to GStreamer through PyGObject |
| `gstreamer1.0-plugins-base` | downscale to 480x272, color conversion |
| `gstreamer1.0-plugins-good` | `pulsesrc` (audio), `jpegenc` (JPEG), `ximagesrc` (X11) |
| `gstreamer1.0-plugins-bad` | `adpcmenc` (audio) |
| `gstreamer1.0-pipewire` | capture through the Wayland portal (the default) |
| `gstreamer1.0-gl` | KMS capture (downscale on the GPU) |
| `libopenh264-dev` | pulls `libopenh264` (`-7` on 24.04): H.264 with P frames. Without it, the server sends JPEG, ~10x more bytes |
| `python3-evdev` | controls (virtual keyboard, mouse and Xbox controller) |
| `pulseaudio-utils` | `pactl`: finds what goes out to the speakers and lists the audio sources |

**Use the system Python** (`/usr/bin/python3`). `python3-gi` only exists
for it: in a venv, conda or pyenv, `import gi` fails. If you need a venv,
create it with `python3 -m venv --system-site-packages`.

`libopenh264-dev` exists on 22.04 (pulls `libopenh264-6`, 2.2.0) and on
24.04 (`libopenh264-7`, 2.4.1).

**Without `libopenh264-dev` in apt** (older versions): use Cisco's own
library. The file name of each version is at
<https://github.com/cisco/openh264/releases>; for example:

```sh
mkdir -p ~/.local/lib && cd ~/.local/lib
curl -LO http://ciscobinary.openh264.org/libopenh264-2.4.1-linux64.7.so.bz2
bunzip2 libopenh264-2.4.1-linux64.7.so.bz2
```

The server looks in `~/.local/lib`, in `lib/` inside the project, or at the
path in the `PSPSTREAM_OPENH264` variable.

## 2. Check

```sh
python3 server/pspstream.py --check
```

It lists what is ready (`ok`), what limits something (`warn`) and what
keeps the server from starting (`missing`), and ends with the `apt` command
for what is missing. Example from an Ubuntu 24.04 with everything installed
except the controls:

```
GStreamer
  ok      GStreamer 1.24.2 and PyGObject
  ok      pipewiresrc: capture through the portal (the default on Wayland)
  ...
Video
  ok      libopenh264 2.4.1: H.264 with P frames (the default)
Controls
  warn    no write permission on /dev/uinput (the server streams without the controls)
```

## 3. Controls (uinput)

The server creates a virtual keyboard, mouse or Xbox controller through
`/dev/uinput`, and your user must be able to write to it. `--setup` does
this; by hand:

```sh
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
printf '%s\n' 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/60-pspstream-uinput.rules
sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger
```

The rule only gives access to whoever sits at the PC (active session),
with no new group and no reboot.

## 4. Screen capture

- **Ubuntu with Wayland** (the default since 22.04, with GNOME): the
  capture goes through the portal. The first time, GNOME asks which monitor
  or window to stream. With ScreenCast portal v4 or newer, the choice is
  saved; `--check` says the version.
- **"Ubuntu on Xorg"** (picked in the gear on the login screen): use
  `--source x11`.
- **KMS capture**: straight from the graphics card, like Sunshine's. It is
  worth it when the server line shows the `source` below 60 fps: from
  GNOME 50 on (`gnome-shell --version`), the portal stays at ~40 fps. It
  needs a helper with permission to read the screen:

  ```sh
  sudo apt install gcc make pkg-config libdrm-dev libcap2-bin
  make -C tools/kms && make -C tools/kms cap    # redo "cap" after each make
  python3 server/pspstream.py --source kms
  ```

  The mouse cursor does not show in the KMS capture. With the proprietary
  NVIDIA driver, the KMS capture was not tested.

## 5. Audio

It works with PipeWire (the default since 22.10) and with PulseAudio
(22.04): the server records what goes out to the speakers. For the audio to
go only to the PSP, without playing on the PC:

```sh
pactl load-module module-null-sink sink_name=psp
```

Pick "Null Output" as the output in Ubuntu's sound settings, and run the
server with `--audio-device psp.monitor` (or pick the source in the web
interface).

## 6. Firewall

`ufw` ships off on Ubuntu Desktop. If you turned it on (`sudo ufw status`
says "active"), open the port:

```sh
sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp
```

If the PSP still does not find the PC, check that the router does not
isolate the devices on the network (guest network, "AP isolation"). In that
case, "Find the PC" does not work, and neither does the IP typed on the PSP.

## 7. Use

```sh
python3 server/pspstream.py                         # portal, "game" profile (keyboard and mouse)
python3 server/pspstream.py --source kms --profile xbox
```

Then, on the PSP, open PSPStream and pick **Find the PC on the network**.
The rest is the same on any distribution: [Using the PSP](Using-the-PSP)
(shortcuts, overlay) and [Web Interface](Web-Interface).

## 8. Starting with the session (optional)

A systemd user service starts the server when you log into the graphical
session. Run the server once by hand first, to pick the screen in the
portal (the choice is saved). It was not tested on this machine, which has
no graphical session.

```sh
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/pspstream.service <<EOF
[Unit]
Description=PSPStream (PC screen on the PSP)
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
journalctl --user -u pspstream -f      # the log
```

(Run it inside the PSPStream folder: `$PWD` becomes its path.)

## 9. Building the EBOOT on Ubuntu (optional)

This is how the EBOOT was built in the tests:

```sh
sudo apt install build-essential cmake pkgconf libreadline8 libusb-0.1-4 libgpgme11 libarchive-tools fakeroot
curl -LO https://github.com/pspdev/pspdev/releases/latest/download/pspdev-ubuntu-latest-x86_64.tar.gz
tar xzf pspdev-ubuntu-latest-x86_64.tar.gz -C ~
cd psp && make dist      # dist/PSP/GAME/PSPStream/{EBOOT.PBP,server.txt}
```

Copy `dist/PSP` to the root of the memory stick and follow
[Installation](Installation#psp) (the PSP Wi-Fi and WLAN Power Save).

## 10. Common problems on Ubuntu

| symptom | what to do |
|---|---|
| `No module named 'gi'` | the Python is not the system one (venv, conda, pyenv): use `/usr/bin/python3`, or a venv with `--system-site-packages` |
| `no element "pipewiresrc"` | `sudo apt install gstreamer1.0-pipewire` |
| "codec: JPEG (no openh264...)" | `sudo apt install libopenh264-dev`, or Cisco's library (section 1) |
| "ScreenCast portal unavailable" | in an Xorg session, use `--source x11`; on Wayland, `sudo apt install xdg-desktop-portal xdg-desktop-portal-gnome` (KDE: `-kde`; Sway: `-wlr`) and log out and back in |
| the portal asks for the screen every time | ScreenCast portal older than v4 (`--check` says): use `--source kms`, or answer the dialog |
| "controls disabled" | section 3; check with `--check` |
| no audio, "GStreamer's pulsesrc and adpcmenc are missing" | `sudo apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-bad` |
| the PSP does not find the PC | section 6 (firewall and router isolation); the PC and the PSP must be on the same network |
| "PC firewall open?" on the PSP screen | the PSP gets no answer: is the server running? `--check` on the PC, and section 6 |
