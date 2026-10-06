The PSPStream server also runs on **Windows 10 and 11** (64-bit). It is the
same server as on Linux (protocol, H.264 with P frames, adaptive quality, the
web interface, English and Portuguese), with the screen, the audio and the
controls done the Windows way. The PSP side does not change: the same EBOOT
and the same `server.txt`.

**Status: experimental.** It is built and tested by CI on every push (the
packaged `pspstream.exe` streams to the fake PSP, with audio and the web
interface), but it has not been tested on a real PC with a PSP yet. Reports
are welcome.

## Download and run

1. Download **`PSPStream-Windows-x64.zip`** from
   [Releases](https://github.com/k7vinilstorage/PSP-Stream/releases)
   (the `nightly` pre-release has the latest build).
2. Unzip it anywhere (for example `C:\PSPStream`). Nothing is installed:
   the folder has `pspstream.exe`, Python and the parts of GStreamer it
   uses.
3. Open a terminal in the folder and prepare the machine once:

   ```bat
   pspstream --check
   pspstream --setup
   ```

   `--setup` asks before each step: it creates the firewall rule for port
   5123 (UDP and TCP, the Windows UAC prompt appears) and, if no openh264
   library was found, downloads Cisco's.
4. Run the server:

   ```bat
   pspstream
   ```

   Or double-click `pspstream.exe`: a console window shows the log. The
   settings are at **http://localhost:5124**.
5. On the PSP: **Find the PC on the network**, or the PC's IP in
   `server.txt` ([Using the PSP](Using-the-PSP)).

The first time the server opens the port, Windows may ask whether to allow
it on the network: allow it on **private** networks.

For Portuguese: `pspstream --lang pt`, or **Language** in the web interface
([Language](Server-Options#language)).

## What works

| part | on Windows |
|---|---|
| screen | `--source screen` (default): the monitor through Desktop Duplication (DXGI), `--monitor 1` for the second one. The scaling to 480x272 runs on the GPU (`d3d11convert`); if that does not start, on the CPU |
| codecs | H.264 with P frames (default), H.264 IDR only and JPEG, the same as on Linux |
| audio | what plays on the speakers (WASAPI loopback), or `--audio-device test` |
| controls | keyboard and mouse profiles (`game`, `desktop`, `arrows`) through SendInput |
| web interface | the same page, with the Windows options |
| `--source test`, `static`, `gst` | test pattern, still image, your own GStreamer elements |

**Not yet:**

- the virtual Xbox controller (`xbox` profiles): it needs the ViGEmBus
  driver, planned for later;
- capturing a single window;
- a tray icon and starting with Windows.

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
| antivirus warning | the exe is not signed (PyInstaller builds are sometimes flagged). The zip is built by GitHub Actions from this repository |
