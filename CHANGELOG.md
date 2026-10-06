# Changelog

The EBOOT and server versions move together. The protocol has its own
version (`PSC5` = v5) and only changes when the message format changes; an
EBOOT with another protocol version is refused with a warning in the log.

## 1.1 (in development)

- **Windows server (experimental).** `PSPStream-Windows-x64.zip`
  (Windows 10/11, 64-bit), built by CI with PyInstaller and the GStreamer
  plugins it needs (GStreamer 1.28, from the project's official wheels on
  PyPI, pinned by hash): unzip and run `pspstream.exe`. The capture runs in
  GStreamer's `gst-launch-1.0` as a child process (no PyGObject on
  Windows) and reaches the server over local TCP: the screen through
  Desktop Duplication (`--source screen`, `--monitor`), scaled on the GPU
  with a CPU fallback, and the audio through WASAPI loopback. Keyboard and
  mouse through SendInput, and the virtual Xbox 360 controller (the `xbox`
  profiles) through the ViGEmBus driver, which the user installs once
  (`--setup` opens its page); `vigem\ViGEmClient.dll` is built by CI from
  ViGEmClient's source.
  `--check` and `--setup` for Windows (GStreamer, openh264, the firewall
  rule, the network profile, Cisco's openh264 DLL, ViGEmBus). The protocol, P
  frames, adaptive quality, web interface and translations are the same
  as on Linux. CI tests the packaged exe against the fake PSP; it was not
  tested on a real PC with a PSP yet.
- The receiver of the pipe capture ACKs every TCP segment: without it,
  Nagle and the delayed ACK held frames for up to 150 ms (measured on
  localhost).
- `h264.py`, `audio.py` and the FPS limiter import without PyGObject;
  JPEG and still images also work through Pillow.
- [PC Client Plan](https://github.com/k7vinilstorage/PSP-Stream/wiki/PC-Client-Plan):
  a receiver for Linux and Windows PCs (a plan).
- **English by default, Portuguese as an option.** The server (log,
  `--help`, `--check`, errors), the web interface, the PSP screens
  (settings, overlay, messages), the KMS helper, the Docker installer and
  the package metadata are in English. Portuguese is one switch away:
  `--lang pt`, `PSPSTREAM_LANG=pt` or **Language** in the web interface
  (applies right away and is saved) for the server; `lang=pt` in
  `server.txt` or the new **Language / Idioma** item on the settings screen
  for the PSP; `install.sh --lang pt` for the installer. `--lang` on the
  command line wins over the saved setting, which wins over
  `PSPSTREAM_LANG`.
- The documentation is in English: the README (with a Portuguese version,
  `README.pt-BR.md`), this changelog and the wiki, whose pages got English
  names (Installation, Using-the-PSP, Controls, Server-Options...). Code
  comments stay in Portuguese.
- Keymap profiles renamed to English: `game` (was `jogo`, still the
  default), `arrows` (was `setas`) and `xbox-shoulders` (was `xbox-ombros`).
  The old names still work, on the command line, in `PSPSTREAM_PROFILE` and
  in saved settings.
- `tests/test_i18n.py` checks that every server and web interface message
  has a Portuguese translation with the same fields, and that no Portuguese
  text is left outside the translation calls.
- **PC audio on the PSP.** The server captures what goes out to the
  speakers (monitor of the PipeWire/PulseAudio default output, `pulsesrc`),
  encodes it as IMA ADPCM (`adpcmenc`, 4 bits per sample) and pushes a UDP
  packet every 20 ms: 44.1 kHz stereo (the PSP rate), ~46 KB/s, ~2% of a
  core on the PC. The PSP decodes it on the CPU (additions and shifts,
  without the H.264 Media Engine) and plays it through `sceAudioSRC`, with a
  buffer that adjusts itself between 30 and 120 ms. A lost packet becomes
  20 ms of silence; video and audio do not depend on each other.
- **On and off from the PSP:** `audio=1/0` in `server.txt`, the "PC audio"
  item on the settings screen and SELECT + START + up during the stream.
  When off, the PSP stops asking for audio (`wflags & 0x10`) and the PC
  stops sending it.
- **A "bee" buzz in the audio (first test on the PSP-3000):** the audio
  thread reused its single output buffer while the hardware was still
  playing it (the blocking output returns when the chunk enters the queue,
  and the DMA reads it later), spoiling the end of each 8 ms chunk: ~125 Hz.
  Now there are two alternating buffers, and each chunk leaves the cache
  first. It did not show on PPSSPP (it copies at call time).
- Audio at **44.1 kHz** by default (it was 32 kHz): it is the PSP hardware
  rate, so it does not resample; on a game song, 0.3-1.8 dB more fidelity
  and highs up to 22 kHz, for ~12 KB/s more.
- The overlay shows the audio (buffer, target, lost, empty, skips), and the
  server line, the audio KB/s.
- **P frames requested when decoding starts (`prefetch=auto`).** The report
  "turning prefetch on and off takes it from ~45 to ~60 fps" (Hollow Knight)
  found two bugs:
  - `prefetch=0` waited for the decode on a binary semaphore that could keep
    a stale signal (turning prefetch on/off, or two frames published at
    once). With the signal, the next one went out when decoding started
    (~60 fps); without it, after showing (~45 fps). Now the state of the
    frames decides, and `0` is always "after showing".
  - With prefetch and P frames, the decode thread released the "deferred
    request" before counting the request it made; the network thread (which
    checks every 1 ms) could ask for the same frame again, and the phantom
    request stalled the next one until the RTO. That was the prefetch hitch
    with P frames.
  The default `auto` is now the good mode, on purpose: with P frames, the
  next one is requested when decode takes the current one, without an early
  request in the middle of the frame. `1` adds the early request (in the
  simulation, ~59 fps against ~55 for `auto` before the window, below).
  SELECT + START + cross cycles auto, yes and no.
- **P frames without skipping captures: a 2-frame window.** Report: 52-55
  fps instead of 60, and ~35 with `--fps 40`, with the Wi-Fi well below its
  limit. The request for N+1 went out when decode took N and had to reach
  the PC before the next capture (16.7 ms at 60 fps); with the Wi-Fi
  swinging, the server missed captures. Now, with `prefetch=auto` over UDP,
  the request allows up to N+2 (FRAME + NACK "up to frame F"; the server
  keeps the credit, at most 2 ahead) and the frame leaves at capture time.
  In the simulation with a swinging round trip: 53-55 → 59.4-60 fps, same
  latency. In this mode the request is no longer repeated after 6 ms (the
  next one covers a lost one): one packet less per frame on the way up.
- A whole P frame lost with the window: the next one arrives before the
  repeated request; the PSP notices the gap in the numbering and asks for
  the resend right away, instead of an IDR.
- **Exact `--fps`.** The limit counted the next frame's turn from the frame
  that arrived, with 25% tolerance: with the capture timestamps jittering
  2-3 ms, a 60 Hz screen gave 55-59 fps with `--fps 60` (the default) and
  38-39 with `--fps 40` (75 Hz with `--fps 60`: ~56; 60 Hz with `--fps 50`:
  46-48). Now it is a fixed grid: the requested rate, and a late frame does
  not push the following ones.
- `--fps 40` from a 60 Hz screen is 2 out of every 3 frames: gaps of 17 and
  33 ms. For even motion, `--fps 30` or 60 (wiki, Server Options).
- **Audio that did not come back after the settings screen** ("error" until
  restarting the app): the PSP only releases the audio channel with an
  empty queue, the error was ignored and the channel stayed stuck; the next
  stream could not reserve it. Now it waits for the queue to drain before
  releasing, and tries again when reserving. Reproduced and fixed on
  PPSSPP: `tools/emu_audio_test.py` checks the PSP log, turns the audio off
  and on and comes back from the settings screen.
- `fake_client`: `--rtt-jitter-ms` (swinging round trip, exponential),
  `--no-window` (without the window) and "whole P lost".
- **Web interface for the general settings** (http://localhost:5124):
  capture, codec, quality, audio, controls and network change with the PSP
  connected. The new capture comes up before the old one stops and joins
  the same session (the frame numbering goes on, and the first P frame is
  an IDR); if it does not come up, the old one goes on. Stream state and
  log on the page. The changes are saved in
  `~/.config/pspstream/server.json`; the command line wins over the file.
  On the PC itself only by default (`--web`, `--no-web`), with protection
  against requests from other sites. Python standard library only.
- Server reorganized for that: `capture.py` (assembling capture, audio and
  controls), `settings.py`, `control.py`, `web.py`. The emulator tools run
  the server with their own `--config` and `--no-web`.
- **Any Linux, with an Ubuntu guide**
  ([Ubuntu Guide](https://github.com/k7vinilstorage/PSP-Stream/wiki/Ubuntu-Guide)).
  `--check` checks everything the server uses (Python, PyGObject, each
  GStreamer element, libopenh264, portal, uinput, audio, KMS helper,
  firewall, port) and ends with the command for the detected distribution
  (apt, dnf, pacman or zypper). `--setup` runs those steps, showing each
  command and asking first. The error messages ("X is missing") also give
  the distribution's command, instead of a hard-coded `dnf`.
- Compatibility with older versions: `videoscale`'s `n-threads` and
  `pipewiresrc`'s `always-copy` only go in if they exist (GStreamer 1.20 on
  Ubuntu 22.04); tests also on Python 3.10 and 3.13.
- `libopenh264` is also looked for in `~/.local/lib`, in the project's
  `lib/` and in `PSPSTREAM_OPENH264`: Cisco's works where the distribution
  has no package.
- On the PSP, "No answer from the PC" suggests `--check` instead of the
  firewalld command.
- **Ready EBOOT and packages on GitHub** (`.github/workflows/build.yml`): on
  every push, CI builds the EBOOT, runs the tests and produces the `.deb`
  (Ubuntu 22.04+, Debian 12+) and the `.rpm` (Fedora), installing each one
  to test it. A push to `main` rebuilds the `nightly` pre-release; a `v*`
  tag publishes the stable release. The package brings the `pspstream`
  command, the `/dev/uinput` permission, the menu shortcut, the systemd user
  service and the firewall rule (ufw/firewalld); the KMS capture permission
  is left to `pspstream --setup`.
- MIT license (`LICENSE`).
- Server: `--no-audio`, `--audio-device` (`monitor`, `test` or a PipeWire
  source), `--audio-rate`, `--audio-mono`.
- UDP only (the default).
- `fake_client --audio`, `tools/emu_audio_test.py`, and the PSP decoder
  (`psp/src/ima.c`) tested on the PC against the reference.
- The README said `--p-redundancy-ms 4`; the default is 6.
- **Wolf (Games on Whales), video** (`--source wolf`, experimental):
  PSPStream creates a session in Wolf through the API (Unix socket,
  `--wolf-socket`) and Wolf runs a PSPStream pipeline, which listens to the
  lobby (`interpipesrc`), downscales to 480x272 I420 and sends it over TCP
  on 127.0.0.1; from there on it is the same as the other sources (JPEG,
  h264, h264p). The target is the only open lobby, or `--wolf-target` (id
  or name of the lobby, or session id); the GPU conversion is
  `--wolf-video-convert` (`nvidia`, `va`, `cpu` or `auto`, which tries in
  that order). If the lobby closes, the session is stopped and the source
  waits for it to come back, without bringing the server down. Ping on
  Wolf's port 48100 (`--wolf-rtp-port`, `WOLF_VIDEO_PING_PORT`). Tested only
  against a fake Wolf (`tests/fake_wolf.py`), not on a real Wolf.
- **Wolf, audio:** the target's audio comes the same way as the video (the
  session's audio pipeline listens to `<lobby>_audio`, converts it to S16LE
  at the PSP rate and sends it over TCP) and becomes the usual IMA ADPCM.
  With `--source wolf`, the default `--audio-device monitor` already uses
  Wolf's audio (or `--audio-device wolf`); the packet numbering goes on
  when the Wolf session is rebuilt, and changing the rate or mono rebuilds
  the session. Picked over mounting Wolf's PulseAudio in the container:
  nothing more to share, and it reconnects together with the video.
- **Wolf, controls:** with `--source wolf`, the PSP buttons go to the game
  as a virtual Xbox controller created by Wolf: the same `keymap.json`
  profiles (`xbox`, `xbox-camera`, `xbox-shoulders`; a keyboard profile
  becomes `xbox`), in Moonlight `CONTROLLER_ARRIVAL` and `CONTROLLER_MULTI`
  packets sent through `sessions/input`. The PSPStream session joins the
  lobby by itself (and again, if the Wolf UI shortcut START + up + RB takes
  it out); a full lobby or one with a PIN (`--wolf-pin`) stays view-only,
  trying again. A target that is a standalone Moonlight session is
  view-only. `--input-timeout` releases everything, and on exit the
  controller is turned off in Wolf. The bytes were checked against Wolf's
  structs and the example from its tests.
- **Wolf, Docker:** `Dockerfile` (Ubuntu 24.04, only the server, GStreamer
  and `libopenh264-7` from universe; runs as a regular user) and
  `docker/compose.yml` with the `pspstream` service next to `wolf` and the
  two lines that change in the Wolf service (`WOLF_SOCKET_PATH` and the
  `/var/run/wolf` volume). The Wolf API socket belongs to root only, so the
  compose file uses uid 0 without any capability, without gaining
  privileges and with a read-only file system (the regular user with
  `setfacl` was also tested). CI builds the image and tests it against the
  fake Wolf (`packaging/docker-test.sh`). "Wolf" page in the wiki; the web
  interface shows a "Wolf" line with what is being mirrored.
- **Wolf, fresh install:** `docker/install.sh` installs Wolf and PSPStream
  on a server with Docker (detects the GPU, prepares the system as the Wolf
  documentation asks: the `uinput`/`uhid` modules and Wolf's udev rules;
  opens the ports in ufw/firewalld; writes a `.env`; starts everything),
  showing each command and asking first. Full compose files in
  `docker/compose.yml` (Intel/AMD) and `compose.nvidia.yml`,
  `pspstream.yml` for those who already have Wolf, `build.yml` to build,
  and `.env.example` with the settings; CI checks that the PSPStream
  service is the same in all three and that the installer produces a valid
  `.env`.
- **Ready image on ghcr.io:** CI publishes
  `ghcr.io/k7vinilstorage/pspstream` (`nightly` on every push to `main`,
  the version and `latest` on every tag), after testing it against the fake
  Wolf.
- **Web interface on the network, with a password:**
  `PSPSTREAM_WEB_PASSWORD` (HTTP basic authentication, on the page and the
  API) and `--web-allow-host` / `PSPSTREAM_WEB_HOSTS` for other server names
  (like one from the router's DNS). The default is still the PC itself
  only.
- Environment variable defaults for Docker (the web interface can still
  change them): `PSPSTREAM_WEB`, `PSPSTREAM_WOLF_TARGET`,
  `PSPSTREAM_VIDEO_CONVERT`, `PSPSTREAM_WOLF_PIN`, `PSPSTREAM_PROFILE`.
- Wolf documentation: the wiki's "Wolf" page (install, first use, Wolf UI's
  Start and Coop lobbies and the controller order, web interface, settings,
  troubleshooting) and "Wolf Internals" (how it works inside and what was
  checked in the Wolf code).
- **Documentation in the wiki.** The README got short: features, how it
  works, quick start, shortcuts and credits. The rest (installation, using
  the PSP, controls, web interface, Wolf, options, troubleshooting,
  performance, protocol, measurements, development) moved to the
  [project wiki](https://github.com/k7vinilstorage/PSP-Stream/wiki),
  generated from the `wiki/` folder by the `wiki.yml` workflow on every push
  to `main`; `docs/` went away. `tests/test_docs.py` checks the links of the
  README and the wiki. The `.deb` and `.rpm` packages carry the README, the
  CHANGELOG and the license.
- The server shuts down properly on SIGTERM (`docker stop`, `systemctl
  stop`), as on Ctrl+C.
- **KMS capture, "no permission to read the screen":** the message (in the
  log and in the web interface) says which helper and the right command
  (the package's `setcap` or the repository's `make -C tools/kms cap`), and
  warns when the permission is there but the kernel ignores it (`nosuid`
  partition, or the server running with `no_new_privs`, as in a Flatpak
  terminal). The `.deb` and `.rpm` packages keep the permission across
  updates: before, each update replaced the helper and it was gone.
  `--check` reads the permission without needing `getcap`.

## 1.0

First release.

- **openh264 called directly** (`server/openh264.py`, ctypes), without
  GStreamer in the encode path: from the PSP request to the 1st packet of
  the P frame, 2.8 -> 1.8 ms (median; p95 4.2 -> 2.7 ms). Adaptive quality
  changes the QP without an IDR (before, each change rebuilt the encoder and
  waited up to 3 s). The stream is the same as openh264enc's, byte for
  byte; if the library is missing or its layout does not match, the server
  falls back to GStreamer by itself. `--h264-encoder auto|openh264|gstreamer`.
- `--codec auto` now picks **H.264 with P frames**, validated on the
  PSP-3000 in gameplay. An EBOOT older than v0.9 gets every frame as IDR.
- **Fewer hitches with P frames.** Each P depends on the previous one, so a
  lost packet stopped the stream until the resend: 30-60 ms per loss (the
  decode, 10.6 ms, fits in the 16.7 ms of a frame at 60 fps and was not the
  limit). Now:
  - the server sends the **last chunk of each P frame again** 6 ms later
    (`--p-redundancy-ms`): losing the last chunk was the slow case, only
    noticed by the silence, and a small P is a single chunk;
  - the PSP **repeats the new frame request** after 6 ms, with the frame
    number (the server recognizes the copy and does not send an extra
    frame): a request lost on the way up waited for the RTO, >= 30 ms;
  - the PSP no longer repeats a request it has not even made yet (the next
    frame deferred to the decode thread): it was an extra request, and it
    inflated the overlay's `rep`;
  - **no periodic IDR** (it was every 30 s): the openh264 counter wrap
    passed in probe v4.1.
  In the simulation with 1-2% loss, the hitches (>= 50 ms between frames)
  dropped from 4-12 to 0-3 every 16 s; the last-chunk copy costs ~40 KB/s.
- **`prefetch=auto` (default):** no prefetch with P frames, prefetch for the
  rest. With P frames and prefetch off, the next frame is only requested
  after showing the current one: one at a time, at a steady pace, and that
  is what made Hollow Knight smooth and close to 60 fps on the PSP-3000.
  With JPEG and H.264 with full frames only, prefetch stays (1.2-1.7x the
  FPS, measured). `prefetch=1` or `0` forces it; SELECT + START + cross
  flips what is in effect; the settings screen has auto/yes/no; the overlay
  says when the next one is requested.
- The server stats line shows the **hitches with the likely cause** (loss,
  IDR, late request, capture) and the IDRs.
- `fake_client`: loss bursts (`--loss-burst-ms`), hitch counting,
  `--prefetch auto|on|off`, `FAKE_TRACE=1`; fixed the counting of the
  request made by the "decode", which created false hitches in the
  simulation.
- `--version`; documentation reorganized for first-time installers; this
  changelog.

## 0.9

- **H.264 with P frames** (`--codec h264p`): the server encodes the frame
  when it is requested, and each packet carries the frame and 2 copies (the
  PSP decoder only releases frame N after N+2). On the PSP, an in-order
  queue, complete frames waiting for the resend of an older one, an IDR
  requested (`PS_REQ_IDR`) when the chain breaks, and the next frame
  requested only when decode takes the current one.
- A small frame lost whole comes back through the repeated request with a
  NACK of the expected frame, without needing an IDR.
- `sceMpegAvcDecodeStop` before every IDR that arrives with P frames in the
  decoder: without it, the PSP **powered off** (found by the `psp/probe`
  v4.1 probe). An IDR every 30 s.
- **Virtual Xbox 360 controller** (`--profile xbox`, `xbox-camera`,
  `xbox-shoulders`), with a layer on SELECT.
- **Settings screen on the PSP** (IP, port, Wi-Fi profile, options; writes
  `server.txt`) and **Find the PC on the network** (broadcast ping). It
  opens with SELECT + START + R during the stream.
- `scePowerTick`: automatic standby no longer suspends the PSP in the middle
  of the stream.
- The SELECT + START + L shortcut no longer repeats after reconnecting.

## 0.8

- Automatic early request (round trip x throughput, ~2-3 KB on the
  PSP-3000), protocol v5 (`early_b`, `idle_t`).
- Capture: without `videorate`, which cut the portal from 60 to ~38 fps.
- **KMS capture** (`--source kms`, `tools/kms` helper with
  `CAP_SYS_ADMIN`): 58-60 fps on GNOME 50, whose portal stays at ~40 fps.
- `--dmabuf` (experimental): screen downscaling in OpenGL.
- The server says whether the PC shares the 2.4 GHz channel with the PSP.

## 0.7

- An unanswered request is repeated after a measured round trip (it was a
  fixed 200 ms): the H.264 FPS doubled on the PSP-3000.

## 0.6

- Ping during the stream (protocol v4); minimum and median of the 1st chunk
  in the benchmark.

## 0.5

- **H.264 through the PSP hardware decoder** (`--codec h264`, every frame
  IDR + Stop): ~3.7 ms of decode and ~40% of the JPEG bytes at the same
  quality. Default for `--codec auto`.
- `psp/probe`: H.264 decoder test on the hardware (v1-v3).

## 0.1 - 0.4

- MJPEG stream with the pull model (RNDS-Stream), sceJpeg/libjpeg-turbo
  decode straight into VRAM, overlay, quality adapted to the bandwidth.
- PSP controls as keyboard and mouse (uinput), with stuck-key protection.
- UDP transport (default) with NACK; protocol v3: JPEG header sent once,
  ping at the start, DSCP.
- Quality benchmark with the PSP connected, fake client and tests on
  PPSSPP.
