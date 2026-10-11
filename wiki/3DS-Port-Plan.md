Status: **phase 0 (hardware probes) in progress** in its own repository,
[3ds-stream](https://github.com/k7vinilstorage/3ds-stream); the client
itself is not implemented yet. A PSPStream **client for the
Nintendo 3DS** (homebrew, `.3dsx` and `.cia`): it connects to the same
PSPStream server, shows the PC screen on the top screen, plays the audio
and sends the 3DS buttons, Circle Pad and touch screen back to the PC.

The server stays the one already shipped (Linux, Windows, Wolf). The first
version speaks [protocol v5](Protocol) unchanged; the server changes come
in a later phase, guarded so the PSP keeps working byte for byte.

## What carries over and what does not

| part | PSP today | 3DS | effort |
|---|---|---|---|
| protocol, pull model, UDP chunks, NACK, credit window, early request, pings | `psp/src/stream.c` | **the same logic**; only threads, semaphores, the clock and sockets change | small, once the code is split (phase 1) |
| network bring-up | `sceNetApctl*`, XMB connection | `socInit` (libctru, BSD sockets); the console connects by itself with the system Wi-Fi settings, `acWaitInternetConnection` | small |
| "Find the PC" | UDP broadcast, `net_discover` | the same broadcast over `soc` | small (check that broadcast works, phase 0) |
| H.264 decode | Media Engine (`sceMpegAvc*`), the "2 copies" trick | **New 3DS: the MVD hardware decoder** (`mvdstd` in libctru) → RGB565/BGR in memory, then a texture. **Old 3DS: no usable hardware**, see below | large: the core of the port |
| JPEG decode | `sceJpeg` (hardware) | software (libjpeg-turbo from the devkitPro portlibs) to YCbCr, colour conversion on the **Y2R** hardware service | medium |
| display | direct writes to the framebuffer, stride 512 | **citro3d/citro2d**: one 512x256 texture drawn on the top screen (the 3DS framebuffer is rotated, 240x400; the GPU handles it); the bottom screen for the overlay and menus | medium |
| audio | IMA ADPCM decoded on the CPU (`ima.c`) → `sceAudioSRC` | **the same `ima.c`** → **NDSP** (resamples any rate; needs the user's `dspfirm.cdc` dump) | small |
| input | `sceCtrl`, PSP bitmask | `hidScanInput`: A B X Y L R ZL ZR START SELECT, D-pad, Circle Pad (±156), C-Stick (New 3DS), touch screen | small in v5 (mapped to the PSP mask), more with v6 |
| settings screen, `server.txt` | `menu.c`, `config.c` | the same INI in `sdmc:/3ds/PSPStream/server.txt`; the menu redrawn with citro2d on the bottom screen, software keyboard (`swkbd`) for the IP | medium |
| language | `lang.h` | the same table | none |

## The hardware, and what it means for the image

| | PSP-3000 | Old 3DS / 2DS | New 3DS / New 2DS XL |
|---|---|---|---|
| CPU | MIPS 333 MHz + Media Engine | 2x ARM11 268 MHz (1 for the app, the 2nd only in part) | 4x ARM11 804 MHz, L2 cache |
| H.264 in hardware | yes (ME) | **no** | **yes (MVD)** |
| top screen | 480x272 | 400x240 (800x240 in "wide" mode, no 3D) | same |
| bottom screen | - | 320x240, touch | same |
| Wi-Fi | 802.11b | 802.11b/g, homebrew throughput to measure | same |

Two image paths follow from it:

- **New 3DS: H.264 with P frames at 400x240** through MVD, aiming at 60 fps
  like the PSP. MVD's behaviour with low-delay streams (does it hold frames
  back like the ME, which forced the "2 copies"?) is the first thing
  phase 0 measures.
- **Old 3DS: JPEG at 400x240** (the server already has the JPEG path with
  header stripping), decoded in software straight to YCbCr and converted by
  Y2R, aiming at 20-30 fps. A software H.264 decoder (baseline only, as the
  server sends) is an option only if phase 0 shows the CPU has room, with
  the decode on the second core.

Image size: **400x240**, the top screen 1:1, which is also the PC's 16:9
with no borders (the PSP's 480x272 would need downscaling on the 3DS, with
blur and wasted bandwidth). The "wide" 800x240 mode is a later option for
the New 3DS.

## Code sharing

The [PC Client Plan](PC-Client-Plan) already proposes, in its C3 phase, to
split `psp/src/stream.c` into a portable core and a platform layer. The 3DS
is the real reason to do it:

```
client/core/        protocol.h, stream.c (no sce*), ima.c, config.c, lang.h
client/core/plat.h  plat_now_us, plat_thread_*, plat_sema_*, plat_udp_*, plat_sleep_us
psp/src/            plat_psp.c (sceKernel*, sceNetInet*), decode.c, display.c, audio.c, menu.c
3ds/source/         plat_3ds.c (libctru threads, LightLock, soc), decode_mvd.c,
                    decode_jpeg.c, display.c (citro2d), audio.c (NDSP), menu.c
```

The PSP build keeps its Makefile and its output; the refactor is checked
by the existing PPSSPP tests (`tools/emu_test.sh`) and by
`tools/fake_client.py` against the server, before any 3DS code goes in.

## Phases

Each phase ends with something that runs.

**Phase 0, probes (about a week).** A small `3ds/probe` homebrew, like
`psp/probe`, that answers the questions the rest depends on:

- MVD: feed it the clips from `tools/h264_probe_clips.py` at 400x240
  (I frame, P frames, the AUD + frame + 2 copies packet) and measure
  per-frame decode time and how many frames it holds back;
- Old 3DS: software JPEG decode time at 400x240 for qualities 50-90, with
  and without Y2R;
- network: UDP throughput and loss with `soc` on a real router, broadcast,
  and the round trip (the numbers the early request uses);
- NDSP: latency of a 20 ms buffer chain.

The results go to [Measurements](Measurements) and decide the codec and
frame rate per model.

**Phase 1, shared core (a few days).** The split above, with no change in
behaviour on the PSP: the same EBOOT size within a few KB, the emulator
and fake-client tests green.

**Phase 2, first image (about a week).** `3ds/` with devkitARM + libctru +
citro2d: connect to an IP from `server.txt`, protocol v5 with the
capability flags of the path chosen in phase 0, show the frames on the top
screen, overlay (FPS, KB/frame, times) on the bottom screen. The server
runs with `--size 400x240` (see phase 4 for H.264). Buttons mapped to the
PSP mask so every existing server profile works:

| 3DS | sent as (PSP) |
|---|---|
| A / B / X / Y | CIRCLE / CROSS / TRIANGLE / SQUARE (same positions as the PSP) |
| L / R | L / R |
| D-pad, START, SELECT | the same |
| Circle Pad | analog stick (±156 scaled to 0..255) |
| ZL / ZR, C-Stick, touch | not sent until v6 (or a local shortcut) |

**Phase 3, usable client (about a week).** Audio over NDSP, the settings
screen and "Find the PC" with the touch screen and the software keyboard,
reconnection like the PSP (keepalive, HELLO after 3 s without a frame),
home menu and sleep handled (`aptMainLoop`, close the socket on sleep and
reconnect on wake), the `.cia` with icon and banner. Release in the same
workflow: `PSPStream-3DS.zip` with the `.3dsx` and the `.cia`.

**Phase 4, protocol v6 (server changes).** Shared with phase C2 of the
[PC Client Plan](PC-Client-Plan); old EBOOTs never send the extension and
keep v5 behaviour:

- a HELLO extension with the device (PSP / 3DS / PC) and the wanted size,
  so the server picks 400x240 by itself and allows **H.264 at sizes other
  than 480x272** (today `server/capture.py` falls back to JPEG for any
  other size, because the PSP decoder writes the whole screen);
- a capability flag "no copies", if MVD does not need the 2 extra copies
  in each P packet;
- extended input: ZL/ZR, the C-Stick as the right stick of the Xbox
  profile, and **the touch screen as an absolute mouse** (tap = click,
  drag = move; the bottom screen showing the image or a trackpad);
- `keymap.json` targets for the new buttons, with defaults so the current
  profiles keep the same behaviour.

**Phase 5, extras (optional).** The bottom screen as a second view (zoom
on the mouse area, or a second PC monitor), the 800x240 wide mode on the
New 3DS, the gyroscope as mouse or right stick, and the 3D slider ignored
or used for a nothing-but-2D stream.

## Build and tests

- **Toolchain**: devkitPro (devkitARM, libctru, citro3d, citro2d,
  `3ds-libjpeg-turbo` from the portlibs), `makerom` and `bannertool` for the
  `.cia`. In CI, the `devkitpro/devkitarm` Docker image, like the PSP
  build uses `pspdev`.
- **Emulator**: an Azahar/Citra build in CI to start the app, reach the
  server with `--source test` and check frames arrive, like the PPSSPP test
  for the PSP. MVD is not emulated in every build, so the H.264 path is
  checked on hardware and the emulator test runs the JPEG path.
- **Hardware**: an Old 3DS and a New 3DS, FPS and latency from the overlay
  and filmed with `tools/latency_clock.html`, as in
  [Measurements](Measurements).

## Risks

| risk | effect | what to do |
|---|---|---|
| MVD holds frames back or needs a reset between streams | latency, or the PSP-style "2 copies" again | phase 0 measures it before any client code; the copies already exist on the server |
| Old 3DS too slow for software decode at 400x240 | low frame rate | JPEG at lower quality, `--fps 30`, or 200x240 doubled horizontally; state the Old 3DS as "best effort" |
| 3DS Wi-Fi throughput in homebrew | lower quality than the PSP | the server's adaptive quality already follows the measured throughput |
| NDSP needs the DSP firmware dump | no audio on consoles without `dspfirm.cdc` | detect it and show how to dump it (the usual `DSP1` homebrew); video works without it |
| a v6 change breaks old EBOOTs | PSP users lose the stream | every v6 field is opt-in from the HELLO; the existing server tests run v5 untouched |
| the core split slows the PSP | lost fps on the PSP-3000 | the split is pure moves and inline wrappers; measured before and after on hardware |

## Related

- [Protocol](Protocol): what the 3DS sends and receives.
- [Design Decisions](Design-Decisions): why the pull model, UDP with NACK
  and IMA ADPCM, which the 3DS reuses as they are.
- [PC Client Plan](PC-Client-Plan): the other client, sharing phase 1 (the
  portable core) and phase 4 (protocol v6).
