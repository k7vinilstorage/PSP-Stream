The PSPStream server also runs on **Windows 10 and 11** (64-bit). It is the
same server as on Linux (protocol, H.264 with P frames, adaptive quality, the
web interface, English and Portuguese), with the screen, the audio and the
controls done the Windows way. The PSP side does not change: the same EBOOT
and the same `server.txt`.

**Status: experimental.** It is built and tested by CI on every push (the
packaged `pspstream.exe` streams to the fake PSP, with audio and the web
interface), but it has not been tested on a real PC with a PSP yet. Reports
are welcome.

## Install

1. Download **`PSPStream-Setup-x64.exe`** from
   [Releases](https://github.com/k7vinilstorage/PSP-Stream/releases)
   (the `nightly` pre-release has the latest build) and run it. The
   wizard is in English or Portuguese, following Windows.
2. Leave the boxes on the "Prepare this PC" page checked:
   - **allow the PSP through the firewall**: port 5123, UDP and TCP, on
     private networks;
   - **install the ViGEmBus driver**: the virtual Xbox controller for the
     `xbox` profiles. The wizard downloads the official installer (version
     1.22.0, its SHA-256 checked) and installs it. The box does not show if
     the driver is already there.
3. At the end, **Launch PSPStream**. A console window shows the log, and
   the Start menu gets **PSPStream** and **PSPStream settings** (the web
   interface, **http://localhost:5124**).
4. On the PSP: **Find the PC on the network**, or the PC's IP in
   `server.txt` ([Using the PSP](Using-the-PSP)).

The network must be **Private** (Settings > Network & Internet > the
network): on a Public network Windows blocks the PSP even with the rule.
Uninstalling (Settings > Apps) removes PSPStream and its firewall rule;
ViGEmBus stays, because other programs use it, and has its own entry there.

For Portuguese: **Language** in the web interface, or `pspstream --lang pt`
([Language](Server-Options#language)).

### Without installing (zip)

`PSPStream-Windows-x64.zip` is the same program, portable: unzip it
anywhere and prepare the PC once from a terminal in that folder:

```bat
pspstream --setup
pspstream
```

`--setup` asks before each step: the firewall rule (the Windows UAC prompt
appears), Cisco's openh264 if none was found, and the ViGEmBus driver if it
is missing. `pspstream --check` shows what is ready, in both cases.

## What works

| part | on Windows |
|---|---|
| screen | `--source screen` (default): the monitor through Desktop Duplication (DXGI), `--monitor 1` for the second one. The scaling to 480x272 runs on the GPU (`d3d11convert`); if that does not start, on the CPU |
| codecs | H.264 with P frames (default), H.264 IDR only and JPEG, the same as on Linux |
| audio | what plays on the speakers (WASAPI loopback), or `--audio-device test` |
| controls | keyboard and mouse profiles (`game`, `desktop`, `arrows`) through SendInput; the virtual Xbox 360 controller (`xbox`, `xbox-camera`, `xbox-shoulders`) through the ViGEmBus driver |
| web interface | the same page, with the Windows options |
| `--source test`, `static`, `gst` | test pattern, still image, your own GStreamer elements |

**Not yet:**

- capturing a single window;
- a tray icon and starting with Windows.

## Xbox controller (ViGEmBus)

With `--profile xbox` (or `xbox-camera`, `xbox-shoulders`), the PSP shows up
on the PC as a **wired Xbox 360 controller**, which games read through
XInput, with the same profiles and the same SELECT layer as on Linux
([Controls](Controls)). Windows has no way to create a controller without a
driver, so this needs **ViGEmBus**, the same driver Sunshine and DS4Windows
use:

1. Install ViGEmBus once: the box in the installer, or `pspstream --setup`
   with the zip. Both download the official installer (version 1.22.0, its
   SHA-256 checked) and run it as administrator. By hand: its
   [releases page](https://github.com/nefarius/ViGEmBus/releases/latest).
2. `pspstream --check` tests it end to end: it plugs in a controller for a
   moment, presses A on it and reads it back through XInput, as a game
   would, then unplugs it. It shows `ok  ViGEmBus: virtual Xbox 360
   controller works` under Controls.
3. `pspstream --profile xbox`, or **Profile** in the web interface.

The `vigem\ViGEmClient.dll` next to `pspstream.exe` talks to the driver; CI
builds it from ViGEmClient's source (MIT). Without the driver, the `xbox`
profiles are refused with a message in the log, and keyboard and mouse
keep working. The controller disappears when the server stops. ViGEmBus is
no longer developed by its author (2023), but it works on Windows 10 and
11 and is what the other streaming programs still use. It does not install
on Windows Server.

## How it works

On Linux, the server talks to GStreamer inside its own process
(PyGObject). On Windows, PyGObject only exists for MSYS2's Python, so the
capture runs in GStreamer's `gst-launch-1.0`, as a child process, and the
server reads the result over local TCP:

```
d3d11screencapturesrc ! d3d11convert (GPU, 480x272) ! d3d11download ! I420 ! tcpclientsink  ──>  server
wasapi2src loopback=true ! audioresample ! adpcmenc ! tcpclientsink                          ──>  server
```

From there it is the same server: the I420 frames go to the openh264
encoder (P frames encoded only when the PSP asks), and the audio blocks go
to the UDP session. The child process dies with the server (a Windows Job
Object). The reasons for each choice are in
[Windows Server Plan](Windows-Server-Plan) and the code is in
[`server/gst_pipe.py`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/gst_pipe.py).

## The H.264 library (openh264)

The server looks for the openh264 DLL in this order:

1. `PSPSTREAM_OPENH264` (the path of a DLL);
2. next to `pspstream.exe` (or in its `lib` folder);
3. `%LOCALAPPDATA%\PSPStream\lib`, where `pspstream --setup` puts Cisco's;
4. the bundled GStreamer's `bin` folder (`openh264-7.dll`, from the official
   GStreamer runtime).

Cisco's H.264 patent license covers the binary downloaded from Cisco, which
is why PSPStream does not ship it and `--setup` downloads it on your PC.
Without any openh264, the server sends JPEG (~10x more bytes per frame).

## Using your own GStreamer

The zip bundles only the GStreamer plugins the server uses. To use a full
installation instead (the MSVC 64-bit runtime from
[gstreamer.freedesktop.org](https://gstreamer.freedesktop.org/download/)),
delete the `gstreamer` folder next to `pspstream.exe`: the server then finds
the official installation (`GSTREAMER_1_0_ROOT_MSVC_X86_64`). Or point
`PSPSTREAM_GSTREAMER` at any GStreamer root (the folder with `bin\`), such
as the one `python packaging\windows\gstreamer.py C:\gst` makes from the
official wheels on PyPI (what the zip is built from).

## Troubleshooting

| symptom | what to do |
|---|---|
| the PSP does not find the PC | `pspstream --check`: the firewall rule (`--setup` creates it) and the network profile, which must be **Private** (Settings > Network & Internet > the network > Private). A Public network blocks the "Find the PC" broadcast |
| "could not start the capture" with `d3d11` errors | the log says what failed on the GPU path and on the CPU one. Over Remote Desktop, Desktop Duplication does not see the screen: run it on the PC's own session |
| black image in a full-screen game | some older games in exclusive full screen do not show in Desktop Duplication: use borderless window mode |
| the controls do nothing in one program | Windows does not let a normal program send input to one running as administrator: run `pspstream.exe` as administrator too. Some anti-cheats ignore injected input |
| no audio | `--check` lists `wasapi2src` and `adpcmenc`; with nothing playing, the loopback sends nothing (the PSP plays silence) |
| "the ViGEmBus driver is not installed" | `pspstream --setup` installs it (accept the Windows UAC prompt), or install it from its [releases page](https://github.com/nefarius/ViGEmBus/releases/latest) as administrator; then `pspstream --check` |
| "the test controller failed" in `--check` | the driver is there but Windows did not show the controller to XInput: restart the PC (a driver update can wait for a restart) and run `pspstream --check` again; reinstall ViGEmBus with `pspstream --setup` if it persists |
| the game does not see the Xbox controller | the game must read XInput (most PC games do); a game that only reads DirectInput sees it too, but with the triggers on one axis, as with a real Xbox 360 controller. `joy.cpl` (Win+R) shows whether the controller is there and its buttons |
| antivirus warning | the exe is not signed (PyInstaller builds are sometimes flagged). The zip is built by GitHub Actions from this repository |
