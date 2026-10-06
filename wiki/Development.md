How the project is built, tested and published, and where everything is in
the code. The design choices are in [Design Decisions](Design-Decisions)
and the message format, in [Protocol](Protocol).

## Builds and releases (GitHub Actions)

[`.github/workflows/build.yml`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/.github/workflows/build.yml),
on every push and pull request:
- builds the EBOOT with Ubuntu's pspdev;
- runs the tests and `ruff`;
- builds the `.deb` (on Ubuntu 22.04) and the `.rpm` (in a Fedora 43
  container), and installs each one to test it (`packaging/smoke-test.sh`:
  `--check`, a stream with the fake PSP, the web interface).

The files stay in the run's "artifacts". A push to `main` rebuilds the
`nightly` pre-release. The "Docker image" job builds the server image,
tests it against the fake Wolf and checks the compose files and the
installer (`packaging/docker-test.sh`, `compose-check.sh`); on a push to
`main`, it publishes `ghcr.io/k7vinilstorage/pspstream:nightly`, and on a
tag, `:1.2` and `:latest`. On the first publish, the ghcr.io package is
born private: in GitHub → profile → *Packages* → `pspstream` → *Package
settings*, change the visibility to *Public*, to pull without logging in.

A stable version comes from a tag:

```sh
# in CHANGELOG.md, "## 1.2 (in development)" becomes "## 1.2"; VERSION in
# server/pspstream.py and PSPSTREAM_VERSION in psp/src/version.h
git tag v1.2 && git push origin v1.2
```

The release uses the version's section of `CHANGELOG.md` as its notes.

The **Windows** job (`windows-2022`) downloads the official GStreamer
runtime (MSVC 64-bit) as the project's wheels on PyPI, pinned with their
hashes in `packaging/windows/gstreamer-wheels.txt`, and merges them into one
folder with `packaging/windows/gstreamer.py` (nothing installed). It runs
`tests/test_windows.py` against it, builds the zip with
`packaging/windows/build.py` and tests the packaged `pspstream.exe` with
`packaging/windows/smoke.py` (`--version`, `--check`, the Portuguese help
and a stream to the fake PSP with audio and the web interface, in H.264 with
P frames and in JPEG). `packaging/windows/vigem.py` builds
`ViGEmClient.dll` (the virtual Xbox controller) from ViGEmClient's source at
a pinned commit, with the runner's Visual Studio, and the Windows tests load
it. A last, informational step tries the real screen capture on the runner,
and the job downloads the ViGEmBus installer the way `--setup` does (pinned
version and SHA-256, the signature checked). The driver itself cannot be
installed there: its installer refuses Windows Server, which is what
GitHub's Windows runners run. The controller end to end (the driver, the
DLL, XInput) is tested on a Windows 10/11 PC by `pspstream --check`, and by
`tests/test_windows.py` (`ViGEmBusTest`) where the driver is installed. The
zip goes to the run's artifacts, the `nightly` pre-release and the
releases.

`build.py` runs PyInstaller (`packaging/windows/pspstream.spec`, a folder,
not a single exe) and copies only the GStreamer plugins the server uses
(`PLUGINS` in the script) plus the DLLs they import, found by reading each
file's import table with `pefile`. By hand, on Windows:

```bat
pip install pyinstaller pillow pefile
python packaging\windows\gstreamer.py C:\gst
python packaging\windows\vigem.py C:\vigem      # ViGEmClient.dll from the source (needs Visual Studio with C++)
python packaging\windows\build.py --gstreamer C:\gst --vigem C:\vigem
```

`--gstreamer` also takes an installed runtime
(`C:\Program Files\gstreamer\1.0\msvc_x86_64`). To move to a new GStreamer,
change the version and the hashes in `gstreamer-wheels.txt` (`pip hash`, or
the files' page on PyPI).

To build the packages by hand: `packaging/build-deb.sh` (needs `dpkg-deb`,
`gcc`, `make`, `pkg-config` and `libdrm-dev`) and `packaging/build-rpm.sh`
(`rpm-build`, `gcc`, `make`, `libdrm-devel`). The installed layout is the
same in both (`packaging/install-tree.sh`).

## This wiki

The pages live in the
[`wiki/`](https://github.com/k7vinilstorage/PSP-Stream/tree/main/wiki)
folder of the repository, and the
[`wiki.yml`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/.github/workflows/wiki.yml)
workflow publishes them here on every push to `main` that changes the
folder. Edit them there, in a pull request: anything changed directly in
the wiki is replaced on the next publish.

- The file name is the page name (`Using-the-PSP.md` becomes "Using the
  PSP").
- Between pages, the link is the name, without `.md`: `[Controls](Controls)`
  or `[x](Wolf#10-troubleshooting)`. For repository files, the full address
  (`https://github.com/k7vinilstorage/PSP-Stream/blob/main/...`).
- `tests/test_docs.py` checks the links of the READMEs and the wiki (pages,
  headings and files) and that every page is on the Home and in the
  sidebar (`_Sidebar.md`).
- The wiki is in English only; the README also has a Portuguese version
  (`README.pt-BR.md`).

The first time, the wiki must exist: in *Settings* → *Features*, check
*Wikis*, open the *Wiki* tab and create any page (it gets replaced). Then
run the workflow in *Actions* → *wiki* → *Run workflow*.

## Translations

English is the default everywhere, and Portuguese is the option. Code
comments and docstrings are in Portuguese.

- **Server** (`server/i18n.py`): user-facing text goes through `tr("...")`
  (translated right away) or `N_("...")` (marked now, translated when
  shown, for tables and settings). The English text is the key; the
  Portuguese one lives in `server/lang_pt.py` (`PT = {english: portuguese}`).
  `--lang`, `PSPSTREAM_LANG` or the web interface setting pick the language.
- **Web interface** (`server/web/app.js`, `index.html`): `t("...")`,
  `N_("...")` and the `data-i18n` attributes, with the same catalog served
  by `/api/i18n`.
- **PSP** (`psp/src/lang.h`): `T("english", "portuguese")`, ASCII only (the
  font has no accents). `lang=pt` in `server.txt` or the "Language /
  Idioma" item picks it. The printf format checks cover both texts.
- **KMS helper** (`tools/kms/pspstream-kms.c`): the same `T()`, with
  `--lang pt` passed by the server.
- `tests/test_i18n.py` fails when a message has no translation, when a
  translation has different `{fields}` or `%` placeholders, when a
  translation is no longer used, or when Portuguese text is left in the
  server outside `tr()`/`N_()`.

## Tests

```sh
python3 -m unittest discover tests                 # server: protocol, encoders, controls, capture, Windows path, translations, documentation links
python3 server/pspstream.py --source test &
python3 tools/fake_client.py --transport udp --h264p --seconds 10 --kbps 450 --decode-ms 11
```

`tools/fake_client.py` mimics the PSP threads (in-order queue, NACK, IDR,
early and repeated request, `--prefetch auto|on|off` as in `server.txt`,
2-frame window; `--no-window` removes the window) and checks that no P
frame is decoded without the previous one; `--kbps`, `--rtt-ms`,
`--rtt-jitter-ms` (swinging round trip, as on Wi-Fi), `--loss`,
`--loss-up`, `--loss-burst-ms` (interference bursts) and `--decode-ms`
simulate the Wi-Fi and the PSP, and the summary counts the hitches.
`FAKE_TRACE=1` shows each request, chunk, loss and NACK. Its numbers are
simulated.

**PPSSPPHeadless** (build PPSSPP with `cmake -DHEADLESS=ON`; H.264 needs
`tools/ppsspp-pmp-fix.patch`):

```sh
PPSSPP_HEADLESS=/path/PPSSPPHeadless tools/emu_test.sh screen.png --source static
PPSSPP_HEADLESS=... python3 tools/emu_input_test.py   # controls; needs: pip install websocket-client
PPSSPP_HEADLESS=... python3 tools/emu_menu_test.py    # settings screen: find the PC, save, connect
PPSSPP_HEADLESS=... python3 tools/emu_audio_test.py   # audio: the PSP asks, turns it off and on with the shortcut
```

In the emulator, the image and the logic hold, but the **timings do not**:
the emulated clock skips idle time (with P frames, it runs ~30x real
time), and the 802.11b bandwidth and losses are not simulated.

**Wolf**: `tests/test_wolf.py` runs against `tests/fake_wolf.py`, which
mimics the API on a Unix socket (required fields, the pipeline's
`fmt::format`, the same id for sessions without a client), the ping and
the session pipelines, with `videotestsrc` and `audiotestsrc` in place of
`interpipesrc`. `packaging/docker-test.sh` does the same with the Docker
image. The bytes of the controller packets are checked against Wolf's
structs. Details in [Wolf Internals](Wolf-Internals).

## H.264 decoder test on the hardware (`psp/probe`)

A separate EBOOT (`PSP/GAME/PSPStreamH264/`) that decodes built-in clips on
the Media Engine and writes `result_h264.txt` before each step (if the PSP
freezes, the next run skips that step and runs the others). That is how
the decoder was measured (holds 2 frames, ~3.5-4 ms per call) and how the
reason the first version of P frames powered the PSP off was found: an IDR
in the middle of a sequence of P frames without `sceMpegAvcDecodeStop`
before it. The clips come from `tools/h264_probe_clips.py`. The results are
in [Measurements](Measurements).

## Layout

```
psp/                   client (C, pspdev)
  src/main.c           life cycle, decode + display, overlay, shortcuts, controls
  src/stream.c         network thread, slots, pull model, in-order queue of P frames
  src/decode.c         sceJpeg (hw), libjpeg-turbo (sw) and H.264 (sceMpegAvcDecode)
  src/menu.c           settings screen (IP, Wi-Fi, options, find the PC, language)
  src/audio.c, ima.c   audio: ring with an adaptive buffer, sceAudioSRC, IMA ADPCM decoder
  src/net.c            network modules, Wi-Fi (apctl), TCP/UDP, broadcast
  src/display.c        8888 framebuffer, triple buffering, text
  src/config.c         server.txt (read and write)
  src/lang.h           T(en, pt): English or Portuguese texts
  src/protocol.h       message format (mirrored in server/protocol.py)
  probe/               H.264 decoder test on the hardware
server/                server (Python 3)
  pspstream.py         sessions, command line, benchmark
  capture.py           builds capture, audio and controls (at startup and from the web interface)
  distro.py, doctor.py distribution and packages; --check and --setup
  i18n.py, lang_pt.py  language: tr()/N_() and the Portuguese catalog
  gst_pipe.py          capture through gst-launch-1.0 in a child process (the Windows server; PSPSTREAM_CAPTURE=pipe on Linux)
  imaging.py           JPEG and still images through Pillow (no GStreamer in the process)
  win_input.py, win_gamepad.py, win_doctor.py   Windows: SendInput keyboard and mouse; Xbox controller (ViGEmBus); --check and --setup
  framerate.py         FPS limiter and capture rate meter (no GStreamer needed)
  paths.py             files in the repository or installed by the package
  settings.py          general settings: schema, server.json, validation
  control.py           applies the settings with the server running
  web.py, web/         web interface (standard library http.server; HTML, CSS and JS without dependencies)
  gst_source.py        GStreamer pipeline (capture -> 480x272 -> JPEG/H.264/I420)
  kms.py, portal.py    KMS capture and ScreenCast portal capture
  audio.py             audio: capture (pulsesrc), IMA ADPCM (adpcmenc), packets
  h264.py, openh264.py H.264 encoders (libopenh264 directly through ctypes; GStreamer as a fallback)
  transports.py        TCP and UDP (chunks, NACK, resend)
  adaptive.py          adaptive quality
  inject.py, gamepad.py, keymap.json   controls (uinput)
  wolf_api.py          Wolf: API client (HTTP over the Unix socket, standard library only)
  wolf_source.py       Wolf: session, video and audio pipelines, target, lobby, reconnection
  wolf_input.py        Wolf: Xbox controller in Moonlight packets (sessions/input)
  stats.py, sources.py, jpeginfo.py, protocol.py, netcheck.py
Dockerfile, docker/    server image; compose (Wolf + PSPStream, NVIDIA, PSPStream only), .env, install.sh
packaging/             .deb and .rpm (install-tree.sh, build-deb.sh, pspstream.spec, build-rpm.sh);
                       docker-test.sh (the image against the fake Wolf), compose-check.sh, publish-wiki.sh;
                       windows/ (PyInstaller spec, gstreamer.py + gstreamer-wheels.txt, vigem.py, build.py
                       with the GStreamer subset, smoke.py)
.github/workflows/     build.yml (EBOOT, tests, packages, image, releases); wiki.yml (publishes the wiki)
tools/                 fake_client.py, emu_*.py/sh, h264_probe_clips.py, kms/ (KMS helper)
wiki/                  the pages of this wiki
tests/                 tests of the server, web interface, translations and documentation links; fake_wolf.py mimics Wolf
```
