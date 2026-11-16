The PSPStream server runs on **macOS** (Apple Silicon and Intel). It uses the same protocol, H.264 with P frames, adaptive quality, web interface, USB cable transport, and English/Portuguese texts as Linux and Windows. The PSP side does not change: use the same EBOOT and `server.txt`.

**Status:** native `ScreenCaptureKit` capture and speaker audio loopback (`pspstream-sck` on macOS 13+), AVFoundation / CoreAudio fallback (`avfvideosrc`, `osxaudiosrc`), H.264 (`libopenh264`), Quartz keyboard/mouse controls, virtual Xbox 360 controller for CrossOver (`libSDL2-2.0.0.dylib`) and composite USB HID gamepad, USB (`libusb`), standalone builds, and `--check`/`--setup` are implemented (see [macOS Server Plan](macOS-Server-Plan)).

## Step-by-step installation (Homebrew)

The recommended way to install every dependency on macOS is [Homebrew](https://brew.sh).

### 1. Install Homebrew (if you do not have it)

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

On Apple Silicon Macs, follow the instructions printed at the end of the installer so `brew` is on your `PATH` (usually under `/opt/homebrew/bin`).

### 2. Install the required packages

```sh
brew install python gstreamer openh264 pillow libusb
```

And install `pyusb` (needed only if you plan to stream over the USB cable with `transport=usb`):

```sh
python3 -m pip install --break-system-packages pyusb
```

If you prefer an isolated environment instead of `--break-system-packages`, use a virtual environment (`python3 -m venv .venv && source .venv/bin/activate && pip install pillow pyusb`).

What each package provides:

| package | what it is for |
|---|---|
| `gstreamer` | screen capture (`avfvideosrc`), audio (`osxaudiosrc`, `adpcmenc`), and scaling (`videoscale`) |
| `openh264` | H.264 with P frames (`libopenh264.dylib`, ~10x smaller frames than JPEG) |
| `pillow` | JPEG encoding (`--codec jpeg` or older EBOOTs) and `--source static` |
| `libusb` + `pyusb` | optional: streaming over the USB cable (`transport=usb` on the PSP; works without `root` or `udev` rules on macOS) |

If you do not want to install `openh264` via Homebrew, `python3 server/pspstream.py --setup` can also download Cisco's `libopenh264` library into `~/Library/Application Support/PSPStream/lib`.

### 3. Audio loopback: capturing what plays on your Mac (optional)

On **macOS 13 (Ventura) or newer**, PSPStream includes a native `ScreenCaptureKit` helper ([`tools/sck/pspstream-sck.m`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/tools/sck/pspstream-sck.m)) that captures both 60 fps screen video and **what plays on your Mac's speakers directly**, without installing a virtual audio driver:

```sh
make -C tools/sck
```

(`python3 server/pspstream.py --setup` also offers to build it automatically if Xcode Command Line Tools are installed, and release bundles include `pspstream-sck` already compiled.)

On **macOS 12** or when falling back to GStreamer's `osxaudiosrc` without `pspstream-sck`, macOS does not provide a built-in loopback source so `osxaudiosrc` records from your default input (microphone). In that case, install **BlackHole 2ch** with Homebrew:

1. Install **BlackHole 2ch** with Homebrew:

   ```sh
   brew install --cask blackhole-2ch
   ```

2. Open **Audio MIDI Setup** (*Configuração de Áudio e MIDI*, via Spotlight `Cmd + Space`):
   - Click **+** in the bottom-left corner → **Create Multi-Output Device** (*Criar Dispositivo de Saída Múltipla*).
   - Check both your speakers/headphones **and** **BlackHole 2ch** (turn on Drift Correction for BlackHole 2ch).
3. In **System Settings > Sound**:
   - Set **Output** to the **Multi-Output Device** (so you still hear audio on the Mac while streaming, or choose **BlackHole 2ch** directly if you only want audio on the PSP).
   - Set **Input** to **BlackHole 2ch** (so `--audio-device monitor`, the default, captures it).

Alternatively, keep your normal microphone as the default input and pass BlackHole's numeric CoreAudio device index via `--audio-device <ID>`.

### 4. Clone the repository and run `--setup` / `--check`

```sh
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
make -C tools/sck
make -C tools/crossover
python3 server/pspstream.py --setup
python3 server/pspstream.py --check
```

For Portuguese output, add `--lang pt`. If you download a standalone macOS release archive instead of cloning the repository, `pspstream-sck` and `libSDL2-2.0.0.dylib` are already bundled next to `pspstream` and you only need `./pspstream --setup`.

`--check` verifies GStreamer (`avfvideosrc`, `osxaudiosrc`, `adpcmenc`), `libopenh264`, Pillow, the `ScreenCaptureKit` helper, macOS **Screen Recording** and **Accessibility** permissions, CrossOver virtual Xbox 360 controller shim status, the macOS firewall, and `pyusb`/`libusb`.

### 5. Grant macOS Privacy & Security permissions

macOS requires two permissions for the terminal app you run `pspstream.py` from (Terminal.app, iTerm2, Warp, VS Code, etc.):

1. **Screen Recording** (*Gravação de Tela*):
   - **System Settings > Privacy & Security > Screen Recording** → enable your terminal app.
   - Without it, macOS only sends the desktop wallpaper or a black screen.
2. **Accessibility** (*Acessibilidade*):
   - **System Settings > Privacy & Security > Accessibility** → enable your terminal app.
   - Without it, macOS silently ignores keyboard and mouse events from the PSP.

`python3 server/pspstream.py --setup` can trigger both system permission prompts for you. After enabling a permission, **restart your terminal app** if macOS asks or if `--check` still shows it as off.

## Running the server

Over Wi-Fi (keyboard and mouse controls, default profile `game`):

```sh
python3 server/pspstream.py --source screen --profile game
```

As a virtual Xbox 360 controller (`--profile xbox`, `xbox-camera`, or `xbox-shoulders` for CrossOver games over Wi-Fi/USB, or system-wide over the USB cable):

```sh
python3 server/pspstream.py --source screen --profile xbox
```

Over the USB cable (`transport=usb` on the PSP, with `pyusb` and `libusb` installed):

- Plug the PSP into the Mac with a USB data cable and select **USB** in PSPStream's settings screen on the PSP.
- No extra driver or `udev` rule is needed on macOS. (Note: if your Mac only has USB-C ports, use a USB-C to Mini-B cable or USB-C adapter.)

The web interface is at **http://localhost:5124** ([Web Interface](Web-Interface)), and saved settings live in `~/Library/Application Support/PSPStream/server.json`.

To force GStreamer directly instead of `ScreenCaptureKit` (for example with BlackHole 2ch or if `ScreenCaptureKit` has issues on a display), pass `--source avf` for video and/or `--audio-device osx` (or a CoreAudio device index) for audio. To build a standalone bundle or run the linter locally with Homebrew: `brew install pyinstaller ruff`, then `python3 packaging/macos/build.py` or `ruff check server tools tests`.

## What works and limitations

| part | on macOS |
|---|---|
| screen | `--source screen` (default): native `ScreenCaptureKit` helper (`pspstream-sck`, macOS 13+) with automatic fallback to AVFoundation (`avfvideosrc`), `--source avf` to force GStreamer `avfvideosrc` directly, `--monitor 1` for a secondary display, `--no-cursor` to hide the cursor |
| codecs | H.264 with P frames (`h264p`, default via `libopenh264`), H.264 IDR only (`h264`), and JPEG (`jpeg` via Pillow) |
| audio | `--audio-device monitor` uses native `ScreenCaptureKit` speaker audio loopback on macOS 13+ when `pspstream-sck` is present (no driver needed), or falls back to `osxaudiosrc` + `adpcmenc`; pass `--audio-device osx` to force GStreamer `osxaudiosrc` (BlackHole 2ch / default input), `--audio-device test`, or a CoreAudio device index |
| controls | keyboard and mouse profiles (`game`, `desktop`, `arrows`) via Quartz Event Services (`CGEventPost`) |
| Xbox controller (`--profile xbox`) | works in **CrossOver** bottles over both Wi-Fi and USB via the SDL2 proxy ([`tools/crossover/pspstream-sdl2.c`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/tools/crossover/pspstream-sdl2.c), installed by `pspstream --setup`), and **system-wide** across macOS when the PSP is plugged in over the USB cable (`transport=usb`, composite USB HID gamepad in [`psp/usbdrv/main.c`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/psp/usbdrv/main.c)) |
| USB transport | works via `pyusb` + `libusb` (`brew install libusb && pip3 install pyusb`) |
| `--source test`, `static`, `gst` | test pattern, still image, custom GStreamer pipeline |

## Troubleshooting

| symptom | what to do |
|---|---|
| only the wallpaper or a black screen appears | grant **Screen Recording** to your terminal app in **System Settings > Privacy & Security > Screen Recording**, restart the terminal, and verify with `python3 server/pspstream.py --check` |
| PSP buttons do nothing on the Mac | grant **Accessibility** to your terminal app in **System Settings > Privacy & Security > Accessibility**; run `--check` to verify |
| `Codec: JPEG` instead of H.264 with P frames | install `openh264` (`brew install openh264`) or run `python3 server/pspstream.py --setup` |
| no game audio (or microphone audio plays on PSP) | build the `ScreenCaptureKit` helper on macOS 13+ (`make -C tools/sck` or `--setup`), or install `blackhole-2ch` (`brew install --cask blackhole-2ch`) and configure a Multi-Output Device as described in [step 3](#3-audio-loopback-capturing-what-plays-on-your-mac-optional) |
| PSP cannot find the Mac on the network | check **System Settings > Network > Firewall**: allow incoming connections for Python, or temporarily test with the firewall off; also make sure the Mac and PSP are on the same router/subnet |
| `--profile xbox` fails | run `python3 server/pspstream.py --setup` to install the CrossOver SDL2 proxy (`libSDL2-2.0.0.dylib`), or install `libusb` + `pyusb` and plug the PSP over USB (`transport=usb`); otherwise use `game`, `desktop`, or `arrows` |
| `USB transport off: pyusb is not installed` | run `brew install libusb && python3 -m pip install --break-system-packages pyusb` |
