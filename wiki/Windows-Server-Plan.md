Status: **a plan, nothing implemented.** The EBOOT, the protocol and
`server.txt` do not change: the PSP does not know which system the PC runs.

Target: Windows 10 22H2 and Windows 11, x64. Parity with Linux: capture at
60 fps, H.264 with P frames, audio, keyboard and mouse, Xbox controller, and
the web interface as the app's settings screen (on Windows it plays the
role a windowed program would, without Qt or Tk).

## What already works as it is

Most of the server does not depend on Linux:

| module | what it is |
|---|---|
| `protocol.py`, `transports.py` | messages, UDP chunks, NACK, resend, credit window |
| `pspstream.py` (Session, Server) | sessions, pull model, switching the capture with the PSP connected |
| `stats.py`, `adaptive.py`, `sources.py` | stats, adaptive quality, frame sources |
| `settings.py`, `control.py`, `web.py`, `web/` | settings, applying them with the server running, web interface (standard library only; `settings.default_path()` already uses `%APPDATA%\PSPStream`) |
| `i18n.py`, `lang_pt.py` | English and Portuguese messages (plain Python) |
| `openh264.py` | libopenh264 through ctypes. It only uses `c_int`/`c_longlong` (no `c_long`, which is 32 bits on Windows), so the structures match; the DLL name is missing in `LIB_NAMES` |
| `gst_source.RateLimiter` | fixed FPS grid; works with any clock, including the frame arrival time |
| `inject.py`, `gamepad.py` (the logic) | profiles, stuck key, mouse at 125 Hz, Xbox layer. Only the device (`_UInput`) is Linux, behind a small interface (`key`, `move`, `close`) |
| `fake_client.py` and the protocol, UDP, settings and web tests | run on Windows without changes (to be confirmed in CI) |

## What is Linux-only, and the Windows equivalent

| part | Linux today | Windows |
|---|---|---|
| screen | portal (PipeWire), KMS (helper with DRM), `ximagesrc` | Desktop Duplication (DXGI) through GStreamer's `d3d11screencapturesrc` (monitor, cursor); a window through Windows Graphics Capture (`capture-api=wgc`, GStreamer 1.22+, to be confirmed in W0) |
| downscale to 480x272 | `videoscale` on the CPU, or OpenGL with `--dmabuf` | on the GPU: `d3d11convert` (scale and color) and `d3d11download` in I420. Only the small image comes to the CPU, like the KMS path |
| H.264 encode | libopenh264 (`.so`) | the same, Cisco's `openh264-2.4.1-win64.dll` (see Risks: license) |
| JPEG (old EBOOT) | `jpegenc` | Pillow (the pipeline delivers RGB; the quality changes every frame, as with `jpegenc`) |
| audio | `pulsesrc` of the output monitor | `wasapi2src loopback=true` (what goes out to the speakers). Since GStreamer 1.24, the audio of a single program can be taken (process loopback), to be confirmed |
| IMA ADPCM | `adpcmenc` | the same element |
| keyboard and mouse | uinput | `SendInput` (user32, through ctypes), with scancodes (DirectInput games ignore virtual codes) |
| Xbox controller | uinput with the `xpad` IDs | ViGEmBus driver + `vgamepad` (Python, brings ViGEmClient) |
| DSCP (Wi-Fi voice queue) | `IP_TOS` | Windows ignores `IP_TOS` without a policy: qWAVE (`QOSAddSocketToFlow`, voice type) or a per-program QoS Policy |
| PC Wi-Fi check | `ip`, `iw`, `/sys` (`netcheck.py`) | `netsh wlan show interfaces` (band and channel) |
| firewall | firewalld | inbound UDP and TCP 5123 rules (the installer creates them with `netsh advfirewall`); the network must be "Private" for the "Find the PC" broadcast to get through |
| audio sources | `pactl list short sources` | `gst-device-monitor-1.0 Audio/Source` or the WASAPI enumeration |

## The main decision: GStreamer in the process or apart

**A. In the process, as on Linux (PyGObject).** `gst_source.py` and
`audio.py` work almost unchanged. But the path PyGObject documents for
Windows is MSYS2's Python and GStreamer. That weighs on the installer and
strays from the official Python.

**B. GStreamer in a separate process (`gst-launch-1.0`), Python reading
from the pipe.** It uses the official GStreamer (MSVC installer) and the
official Python, and PyInstaller packages it without surprises. The pipes
carry fixed sizes, so they need no framing:
- video: I420 480x272 = 195,840 bytes per frame (60 fps = 11 MB/s, nothing
  for a pipe);
- audio: fixed-size IMA ADPCM blocks (`adpcmenc blockalign=N`).

What B loses:
- the GStreamer pts: the FPS limit uses the arrival time, and
  `RateLimiter` accepts any clock;
- the "capture" measurement in the stats: it becomes an estimate;
- changing the capture means restarting the process (estimate: 0.2-0.5 s).
  `control.py` already switches the capture with the PSP connected, so it
  does not drop;
- errors come through the exit code and stderr, instead of the bus.

**Recommendation: B in the first version**, behind the same `FrameSource`
interface, so A stays possible. W0 confirms it.

Planned pipelines (to be validated in W0):

```
d3d11screencapturesrc monitor-index=0 show-cursor=true
  ! queue leaky=downstream max-size-buffers=1
  ! d3d11convert add-borders=true ! video/x-raw(memory:D3D11Memory),width=480,height=272
  ! d3d11download ! video/x-raw,format=I420 ! fdsink

wasapi2src loopback=true low-latency=true ! audioconvert ! audioresample
  ! audio/x-raw,format=S16LE,rate=44100,channels=2 ! adpcmenc layout=dvi blockalign=888 ! fdsink
```

## How it looks in the code

- `capture.py` is already the assembly point (capture, audio, controls, at
  startup and from the web interface). It starts picking the module by
  `sys.platform`: `capture_linux` (what exists today) or
  `capture_windows`.
- New modules:
  - `win_capture.py`: `PipeSource(FrameSource)`, with the GStreamer
    process, the thread that reads the pipe, the `RateLimiter` by arrival
    time and `publish`;
  - `win_audio.py`: `PipeAudioCapture`, with the same interface as
    `AudioCapture` (listeners, `seq`, `rate`, `channels`, `stop`);
  - `win_input.py`: `SendInput` behind `inject.py`'s `key`/`move`, with
    the table from `KEY_*` (evdev names in `keymap.json`) to scancodes,
    plus the extended key flag (arrows, right Ctrl and Alt...);
  - `win_gamepad.py`: ViGEm behind `GamepadInjector`; the events of the
    `xbox` profile become `vgamepad`'s XUSB report;
  - `win_system.py`: `timeBeginPeriod(1)`, power throttling (EcoQoS)
    turned off for the process, DSCP through qWAVE and the Wi-Fi check
    through `netsh`.
- `settings.py`: per-system choices. The source on Windows becomes "screen"
  (DXGI), "window" (WGC), test and static; "Monitor" becomes a single
  setting for both systems; the Linux-only fields (portal window, KMS)
  disappear from the page on Windows.
- `keymap.json` keeps the evdev names: the profiles work the same on both
  systems.
- `packaging/windows/`: PyInstaller spec, Inno Setup script, firewall
  rules.

## Phases

Each phase ends with something that runs.

**W0, feasibility (1-2 days, one PC with Windows 11 and one with Windows 10).**
- Does the video pipeline above give 60 fps? How much CPU?
- Does `openh264.py` encode with Cisco's DLL, and does the result match
  Linux byte for byte on the same version?
- Does `wasapi2src` in loopback work?
- `SendInput` in a game (keyboard and mouse), and does `vgamepad` show up
  as an Xbox controller in `joy.cpl`?
- Decide A or B.

Done when a script measures FPS and CPU of each part and the result is in
this document.

**W1, video (3-5 days).** `PipeSource`, assembly in `capture.py`,
screen/test/static sources, h264p and h264, TCP and UDP, web interface,
`timeBeginPeriod`. Done when the PSP-3000 shows the desktop at 60 fps and
`fake_client` passes on Windows.

**W2, audio (2 days).** `PipeAudioCapture` and the device list. Done when
the audio plays on the PSP and survives changing the rate from the web
interface.

**W3, controls (3-4 days).** `SendInput` and ViGEm, with the current
profiles. Done when the table tests pass (every `KEY_*` in `keymap.json`
has a scancode) and the controller works in a game.

**W4, installer (3-5 days).**
- PyInstaller (a folder, not a single .exe, which opens slowly) with the
  server and the page.
- GStreamer: only the plugins used (coreelements, d3d11,
  videoconvertscale, wasapi2, audioconvert, audioresample, adpcmenc),
  ~60-80 MB, or the official installer as a prerequisite.
- The openh264 DLL downloaded from Cisco the first time, with its SHA-256
  checked.
- Inno Setup: firewall rules, Start menu, "start with Windows" (an option
  in the web interface), a link to ViGEmBus.
- Tray icon (`pystray`): "Open settings", which opens the web interface in
  the browser, and "Quit".
- Log in `%LOCALAPPDATA%\PSPStream`.

Done when, on a clean Windows (virtual machine), the PSP connects without
opening a terminal.

**W5, polish (2-3 days).** DSCP through qWAVE, a warning for the PC Wi-Fi on
the same 2.4 GHz channel, a Windows page in the wiki, CI on
`windows-latest`, signing the executable (optional; without it SmartScreen
warns).

Total: ~3-4 weeks for one person.

## Risks

| risk | effect | what to do |
|---|---|---|
| PyGObject on Windows | heavy installer, MSYS2's Python | architecture B |
| Desktop Duplication: protected content, laptop with two GPUs, HDR | black screen, failure to open, wrong colors (HDR delivers FP16) | test in W0; WGC as an alternative; warn in the log |
| game in exclusive full screen (old DirectX 9) | may come out black | borderless window mode |
| injected input | Windows blocks `SendInput` into administrator windows (UIPI); anti-cheats ignore injected input | run as administrator only if needed; the ViGEm controller usually gets through, because it is a driver device |
| ViGEmBus was archived (2023) | works today on 10 and 11, with no future fixes | keyboard and mouse stay as the base; the controller is optional |
| 15.6 ms clock | the last-chunk copy (6 ms) and the timed waits come out late | `timeBeginPeriod(1)`; Python 3.11+ already uses a high-resolution timer in `sleep`; measure in W1 |
| Windows 11 power saving (EcoQoS) | the background process is slowed down | turn it off for the process (`SetProcessInformation`) |
| DSCP ignored | no voice queue on the PC's Wi-Fi | qWAVE; with the PC on a cable, it hardly matters |
| "Public" network | the PSP's "Find the PC" does not find it | the installer and the web interface warn |
| openh264 license | Cisco's patent coverage applies to the DLL downloaded from Cisco | download it the first time, like Firefox; never ship it bundled |
| antivirus and SmartScreen | false positive on PyInstaller executables, warning without a signature | sign the executable; publish the hash |

## Tests

- CI with Ubuntu and `windows-latest`, Python 3.12: protocol, transports,
  settings, web, controller and the end-to-end `fake_client` with the
  static source (none needs a GPU). The tests that need `gi`, uinput or
  KMS are skipped on Windows (`skipUnless`).
- Windows only: the `SendInput` table (every `KEY_*` of the profiles
  mapped, in dry-run) and `PipeSource` with `videotestsrc`. It needs
  GStreamer on the runner (~300 MB): run it only in the nightly CI.
- On the hardware, for each version: Windows 10 and 11; Intel, AMD and
  NVIDIA GPUs; a laptop with two GPUs; a game in a borderless window and
  one in full screen; FPS, latency and hitches in the server line, as in
  the [performance table](Performance).
