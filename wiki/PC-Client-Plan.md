Status: **a plan, nothing implemented.** A PSPStream **receiver for Linux
and Windows PCs**: it connects to a PSPStream server like a PSP does, shows
the video, plays the audio and sends keyboard or gamepad input back. The
server and the PSP do not change for the first version.

## Why, and why not just Moonlight

For streaming a gaming PC to another PC, [Moonlight](https://moonlight-stream.org/)
already does it very well: high resolutions, hardware decoding, gamepads.
A PSPStream PC client is not meant to replace it. It is useful for:

- **seeing what the PSP sees, without a PSP**: debugging the server, the web
  interface or a new capture source, with the real image instead of the
  numbers from `tools/fake_client.py`;
- **measuring the server on a PC** with the same protocol, loss recovery and
  credit window as the PSP (glass-to-glass latency with
  `tools/latency_clock.html`, and no 802.11b limit);
- **old or small machines as a screen** (a netbook, a Raspberry Pi, a
  handheld PC) on a weak network: the PSP protocol was designed for ~400 KB/s
  links;
- later, if the protocol grows (phase C2), a light remote screen at higher
  resolutions.

## How it fits the protocol

The client **speaks protocol v5 exactly like the PSP** ([Protocol](Protocol)):
HELLO, frame requests with the capability flags (H.264, P frames, audio),
UDP chunks with NACK, the 2-frame credit window, the IDR request after a
loss, the audio packets and the "Find the PC" broadcast. Everything the
server needs to see is already in `tools/fake_client.py`, which mimics the
PSP's threads and is tested against the server on every CI run. The client
is that same logic plus the parts the fake PSP skips: decoding, showing,
playing and reading input.

With protocol v5 the image is at most 480x272, and each P-frame packet
carries 2 copies that only the PSP decoder needs. That is fine for the
first version; phase C2 lifts it.

## The parts

| part | proposal | notes |
|---|---|---|
| protocol | the `fake_client.py` core, moved to a shared module (`client/stream.py`) | the fake PSP keeps its simulation options (loss, jitter, decode time) on top of it |
| H.264 decode | **openh264's decoder** through ctypes (`ISVCDecoder::DecodeFrameNoDelay` → I420) | the same DLL/.so the server already finds (`server/openh264.py`); baseline/CAVLC is all the server sends. The 2 copies in each P packet decode to nothing and are skipped |
| JPEG decode | Pillow | for servers forced to `--codec jpeg` |
| video output | **SDL2** (PySDL2 + the `pysdl2-dll` wheel): an IYUV texture, so the GPU does the colour conversion and the scaling | window x1/x2/x3 or full screen, integer scaling by default (sharp pixels), vsync |
| audio | IMA ADPCM decoded in Python (`server/audio.py` already has the reference decoder), played with SDL's audio queue | ~88k samples/s in pure Python is ~10% of a core; a small C extension or PyAV if it is too much. Jitter buffer like the PSP's: 40 ms, adapting between 30 and 120 |
| input | SDL game controllers (XInput, DualShock, evdev, with hotplug) and the keyboard, mapped to the PSP buttons and analog stick | the server injects them on its side with its own profile (`game`, `xbox`...), exactly as with a PSP |
| overlay | FPS, KB/frame, network/decode times, losses, like the PSP's | toggled with a key |
| discovery | the broadcast ping ("Find the PC"), or an address on the command line | |

**Why Python first:** the protocol logic already exists and is tested in
Python (`fake_client.py`), the heavy work (H.264 decode, colour conversion,
scaling) runs in C libraries and on the GPU, and the packaging path
(PyInstaller, zip built by CI) already exists for the Windows server. At
480x272 the Python overhead is small. A native C client is an option for
later (C3).

## Phases

Each phase ends with something that runs.

**C0, proof (a few days).** `tools/pc_client.py`: the fake PSP's core +
openh264 decoder + SDL window + keyboard to PSP buttons, no audio. Done
when it shows the server's `--source test` on Linux and Windows at 60 fps,
and the measured glass-to-glass latency is in this page.

**C1, usable client (about a week).** Audio, gamepads, the overlay, the
settings (server, scale, full screen, key mapping) in a small config file,
the reconnection logic of the PSP (keepalive, HELLO after 3 s without a
frame). Packaging: a Windows zip built by CI (like the server), and on Linux
`pip install`/an AppImage. A CI test runs the client with SDL's dummy video
and audio drivers against the server and checks the decoded frames.

**C2, protocol v6 (optional, server changes).** Only if the client proves
useful beyond testing:

- a HELLO extension with the wanted resolution (e.g. 960x544 or 1280x720)
  and a capability flag "no copies" (PC decoders output each frame right
  away);
- the server encodes at that size (the capture pipelines already scale to
  `--size`; the 480x272 limit is only in the argument check and the PSP
  decoder);
- the full gamepad state (two sticks, triggers, all buttons), so a
  `passthrough` profile can give the server a 1:1 virtual controller instead
  of the PSP's SELECT layer.

Old EBOOTs keep working: they never send the extension.

**C3, native client (optional).** If Python gets in the way (a Raspberry
Pi, battery use), a C client with SDL2 and openh264 that shares the network
code with the PSP: `psp/src/stream.c` split into a portable core and a
platform layer (sceNet/sceMpeg/sceAudio on the PSP; sockets/openh264/SDL on
the PC).

## Risks

| risk | effect | what to do |
|---|---|---|
| overlap with Moonlight | effort spent on something better done elsewhere | keep the scope on testing and low-bandwidth use until C1 shows otherwise |
| openh264 missing on the client PC | no H.264 | the same search as the server (Cisco's DLL on Windows, the distribution package on Linux); JPEG as the fallback |
| audio decode cost in Python | CPU and possible crackles on weak machines | measure in C1; C extension or PyAV |
| input latency from SDL's event loop | a frame of delay on the controls | send input from its own thread, as the PSP does |
| the server only allows one PSP at a time | a client and a PSP cannot watch together | expected; a second viewer would be a server change (C2 or later) |

## Tests

- Unit: the shared protocol core keeps passing the fake PSP's tests
  (`tests/test_server.py` and friends).
- End to end in CI: the client with `SDL_VIDEODRIVER=dummy` and
  `SDL_AUDIODRIVER=dummy` against `pspstream --source test`, checking frame
  count, no broken P frames and the decoded image against the test pattern.
- On hardware: Linux and Windows PCs, Wi-Fi and cable; FPS and latency in
  the overlay and filmed with `tools/latency_clock.html`, as in
  [Measurements](Measurements).
