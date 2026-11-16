Status: **phases M0–M5 are implemented** (how to use it: [macOS](macOS)). This page records the architecture and implementation plan for running the PSPStream server on **macOS** (`darwin`, Apple Silicon `arm64` and Intel `x86_64`).

The EBOOT, the protocol and `server.txt` do not change: the PSP does not know which system the PC runs.

| plan | implemented |
|---|---|
| GStreamer subprocess capture (`gst_pipe.py`) with `avfvideosrc capture-screen=true` | yes (`server/gst_pipe.py`, measured at 59.3 fps on Apple Silicon) |
| `libopenh264.dylib` via `ctypes` (Homebrew or Cisco download) | yes (`server/openh264.py`, `server/mac_doctor.py`) |
| JPEG fallback with Pillow | yes (`server/imaging.py`) |
| `osxaudiosrc` + `adpcmenc` (with BlackHole 2ch for speaker loopback) | yes (`server/gst_pipe.py`) |
| Quartz Event Services (`CGEventPost` via `ctypes`) for keyboard and mouse | yes (`server/mac_input.py`) |
| USB transport (`pyusb` + `libusb` via macOS `IOKit`) | yes (`server/usb_link.py`, no `udev` rule or `root` required) |
| `--check` and `--setup` for macOS (`Homebrew`, `openh264`, TCC permissions, firewall, USB) | yes (`server/mac_doctor.py`) |
| Wi-Fi band check (`CoreWLAN` / `networksetup`) | yes (`server/netcheck.py`) |
| CI on `macos-latest` and standalone bundle | yes ([`.github/workflows/build.yml`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/.github/workflows/build.yml), [`packaging/macos/`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/packaging/macos)) |
| Native `ScreenCaptureKit` helper (macOS 13+, zero-driver speaker loopback + 60 fps `I420`) | yes ([`tools/sck/pspstream-sck.m`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/tools/sck/pspstream-sck.m)) |

## What already works as it is

Most of the server does not depend on Linux or Windows:

| module | what it is |
|---|---|
| `protocol.py`, `transports.py` | messages, UDP chunks, NACK, resend, credit window, DSCP (`IP_TOS`) |
| `pspstream.py` (`Session`, `Server`) | sessions, pull model, switching the capture with the PSP connected |
| `stats.py`, `adaptive.py`, `sources.py` | stats, adaptive quality, frame sources |
| `settings.py`, `control.py`, `web.py`, `web/` | settings, applying them live, web interface (standard library only; config saved in `~/Library/Application Support/PSPStream/server.json`) |
| `i18n.py`, `lang_pt.py` | English and Portuguese messages |
| `openh264.py` | `libopenh264` through `ctypes`, checking `.dylib` names and Homebrew / `Application Support` / GStreamer paths |
| `usb_link.py` | finds and opens the PSP (`054C:02D2`, vendor class `0xFF`) via `pyusb` and `libusb` on macOS `IOKit` without `root` or kernel driver changes |

## What is OS-specific, and the macOS equivalent

| part | Linux | Windows | macOS |
|---|---|---|---|
| screen | portal (PipeWire), KMS, `ximagesrc` | Desktop Duplication (`d3d11screencapturesrc`) | `ScreenCaptureKit` ([`tools/sck/pspstream-sck.m`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/tools/sck/pspstream-sck.m)) on macOS 13+ with automatic fallback to AVFoundation (`avfvideosrc capture-screen=true ...`) in [`server/gst_pipe.py`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/gst_pipe.py) |
| downscale to 480x272 | `videoscale` (CPU) or OpenGL (`--dmabuf`) | `d3d11convert` (GPU) or `videoscale` (CPU) | `ScreenCaptureKit` GPU scaling (`vImage` / Metal `I420` conversion) or GStreamer `videoscale` (`bilinear2` on CPU) |
| H.264 encode | `libopenh264.so` | `openh264-*.dll` | `libopenh264.dylib` (Homebrew `openh264` or Cisco's release downloaded by `--setup`) |
| JPEG (old EBOOT) | `jpegenc` | Pillow | Pillow (`server/imaging.py`) |
| audio | `pulsesrc` of output monitor | `wasapi2src loopback=true` | `ScreenCaptureKit` system audio loopback (`pspstream-sck audio`, WAV IMA ADPCM without extra drivers on macOS 13+), or `osxaudiosrc` + `adpcmenc` fallback (BlackHole 2ch or CoreAudio device index) |
| keyboard and mouse | `/dev/uinput` (`evdev`) | `SendInput` (`user32`) | Quartz Event Services (`CGEventCreateKeyboardEvent`, `CGEventCreateMouseEvent`, `CGEventPost` in [`server/mac_input.py`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/mac_input.py)) |
| Xbox controller | `/dev/uinput` (`xpad`) | ViGEmBus + `ViGEmClient.dll` | Not natively supported without a signed DriverKit HID extension; use keyboard/mouse profiles (`game`, `desktop`, `arrows`) |
| permissions | `/dev/uinput`, `CAP_SYS_ADMIN` | UAC (Firewall, ViGEmBus) | macOS TCC: **Screen Recording** (`CGPreflightScreenCaptureAccess`) and **Accessibility** (`AXIsProcessTrusted`) |
| PC Wi-Fi check | `ip`, `iw`, `/sys` | `netsh wlan` | `route -n get`, `networksetup -listallhardwareports`, and `CoreWLAN` (`CWWiFiClient`) |

## Key decisions

1. **Subprocess capture (`gst_pipe.py`) with Homebrew / official GStreamer.** On macOS, requiring `PyGObject` and `gi` inside a system Python environment is fragile (`DYLD` paths, GTK/GLib introspection mismatches). Running `gst-launch-1.0` in a child process lets PSPStream work directly with `brew install gstreamer` or the official `.pkg` from `gstreamer.freedesktop.org`, setting `DYLD_FALLBACK_LIBRARY_PATH` in `_from_root()` so `gst-plugin-scanner` finds GLib cleanly.
2. **Pure `ctypes` for Quartz controls and TCC checks.** [`server/mac_input.py`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/mac_input.py) talks directly to `CoreGraphics.framework`, `CoreFoundation.framework`, and `ApplicationServices.framework` with no `pyobjc` or `pynput` dependency, mapping every `KEY_*` and `BTN_*` in [`server/keymap.json`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/keymap.json) to `CGKeyCode` and mouse events (including `Dragged` events while buttons are held).
3. **Native `ScreenCaptureKit` helper on macOS 13+ with GStreamer fallback.** [`tools/sck/pspstream-sck.m`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/tools/sck/pspstream-sck.m) captures 60 fps `I420` video frames and system speaker audio loopback directly through macOS `ScreenCaptureKit` + `Accelerate` + `AudioToolbox` over localhost TCP, removing the BlackHole requirement for `--audio-device monitor`. On macOS 12 or when the helper is not built, `server/gst_pipe.py` falls back automatically to `avfvideosrc` and `osxaudiosrc` (with BlackHole 2ch).

## Phases

- **M0, feasibility (done):** validated `avfvideosrc capture-screen=true` at 59.3 fps in 480x272 I420, `CGEvent` keyboard/mouse creation, `CoreWLAN` channel lookup (~10 ms), and TCC permission checks.
- **M1, video, settings, and USB (done):** enabled `sys.platform == "darwin"` in `openh264.py`, `gst_pipe.py`, `capture.py`, `settings.py`, `pspstream.py`, and `netcheck.py`.
- **M2, controls (done):** implemented `server/mac_input.py` and wired it into `server/inject.py`.
- **M3, diagnostics, setup, and translations (done):** implemented `server/mac_doctor.py` (`--check`, `--setup`, Cisco `openh264` `.dylib` download for `arm64`/`x86_64`), updated `server/lang_pt.py`, added `tests/test_macos.py`, and documented Homebrew setup on [macOS](macOS).
- **M4, CI and packaging (done):** added the `macos` job on `macos-latest` in [`.github/workflows/build.yml`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/.github/workflows/build.yml) and PyInstaller bundle / smoke scripts in [`packaging/macos/`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/packaging/macos).
- **M5, native ScreenCaptureKit helper (done):** implemented [`tools/sck/pspstream-sck.m`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/tools/sck/pspstream-sck.m) for macOS 13+, delivering 60 fps `I420` display capture and zero-driver speaker audio loopback (IMA ADPCM) over localhost TCP.

## Risks and limitations

| risk | effect | mitigation |
|---|---|---|
| Screen Recording TCC permission off | macOS only captures the desktop wallpaper or a black screen | `--check` detects `CGPreflightScreenCaptureAccess()`; `--setup` triggers the macOS permission prompt |
| Accessibility TCC permission off | `CGEventPost` events are silently dropped by macOS | `--check` detects `AXIsProcessTrusted()`; `--setup` triggers the prompt and `mac_input.py` logs a warning |
| No virtual Xbox driver on macOS | `xbox*` profiles require a signed DriverKit HID extension | `capture.open_injector()` reports a clear message; use `game`, `desktop`, or `arrows` |
| macOS 12 or unbuilt `pspstream-sck` | `osxaudiosrc` records the microphone instead of speaker audio unless configured | On macOS 13+, build `pspstream-sck` (`make -C tools/sck` or `pspstream --setup`) for zero-driver loopback; otherwise follow BlackHole 2ch setup on [macOS](macOS) |
