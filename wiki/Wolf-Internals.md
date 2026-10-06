Technical details of the `--source wolf` source. How to install and use it:
[Wolf](Wolf). The references to the Wolf code are from the `stable` branch
(commit `facb8e0`), read before writing this integration.

## Overview

```
                    server (host network)
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ Wolf (process)                                   PSPStream (container)       │
 │                                                                              │
 │ lobby: compositor ─ interpipesink <lobby>_video   wolf_source.py             │
 │        PulseAudio ─ interpipesink <lobby>_audio     WolfSource (video)       │
 │                         │                           WolfAudio  (audio)       │
 │ PSPStream session:      ▼                         wolf_input.py              │
 │   video pipeline: interpipesrc ! GPU 480x272        WolfInjector (buttons)   │
 │     ! I420 ! gdppay ! tcpclientsink ──TCP 127.0.0.1──> tcpserversrc ─┐       │
 │   audio pipeline: interpipesrc ! S16LE                               │       │
 │     ! gdppay ! tcpclientsink ─────────TCP 127.0.0.1──> tcpserversrc ─┤       │
 │                                                                      ▼       │
 │ API (Unix socket /var/run/wolf/wolf.sock) <── HTTP ── wolf_api.py     PSP    │
 │ virtual controller (inputtino) ──> lobby game                       server   │
 └──────────────────────────────────────────────────────────────────── │ ───────┘
                                                                      │ UDP/TCP 5123
                                                                     PSP (Wi-Fi)
```

Wolf has no way to "export" the image of a lobby: the producers
(`interpipesink <id>_video` and `<id>_audio`, in
`streaming/streaming.cpp`) only exist inside its process, and what reads
them is a pipeline Wolf itself runs for each Moonlight session. PSPStream
takes advantage of that: through the API it creates **its own session**,
like a Moonlight client's without Moonlight, and puts **its own pipelines**
in it, which listen to the lobby and deliver the image and the audio
already shaped for the PSP over local TCP. From there on, the PSPStream
server treats Wolf like any other source (portal, KMS): the same H.264 with
P frames, the same pull model, the same audio.

## The session, step by step

All of this runs in a `WolfSource` thread (`server/wolf_source.py`):

1. **Find the target.** `GET /api/v1/lobbies` and `GET /api/v1/sessions`.
   Without `--wolf-target`, the only open lobby; with several, an error at
   startup with the list; with none, it waits. A target that is a Moonlight
   session in a lobby becomes the lobby (the pipeline listens to what that
   session sees).
2. **Clean up leftovers.** A session with `rtsp_fake_ip = "pspstream"` (the
   mark of ours) left over from a PSPStream that died is stopped: it would
   have the same id as the new one (item 4).
3. **Open the receivers.** `tcpserversrc host=127.0.0.1 port=0 ! gdpdepay`
   for the video and another for the audio; the system picks the ports.
4. **Create the session.** `POST /api/v1/sessions/add` with `client_ip
   127.0.0.1`, 480x272 at 30 Hz and random keys. Without `app_id`, Wolf uses
   a "dummy" app (a `sleep` in a loop) with its own compositor and audio
   sink; without `client_id`, a fictitious client, whose id is the hash of
   an empty certificate: **the same for every such session** (hence one per
   Wolf). It returns the `session_id`.
5. **Send the pipelines.** `POST /api/v1/sessions/start` with
   `video_session` and `audio_session` (all the required fields of the
   `VideoSession` and `AudioSession` structs), including the `gst_pipeline`
   of each and a 16-byte `rtp_secret_payload` chosen here.
6. **The ping.** Wolf only runs the pipelines after a UDP ping on the video
   ping port (48100) and on the audio one (48200) that matches the session
   (`sessions/moonlight.cpp`, `wait_for_ping`). PSPStream sends an `SS_PING`
   (the secret + a sequence number) to both ports every 0.5 s until the
   first frame arrives. The audio ping goes even without audio: without it,
   a Wolf thread waits forever.
7. **The first frame.** Up to 10 s. Without a frame (the conversion does
   not fit that Wolf's GPU), the session is stopped and, with `auto`, the
   next conversion is tried (nvidia → va → cpu).
8. **The controls.** With controls on and a lobby as the target, `POST
   /api/v1/lobbies/join` (with the PIN, if any).
9. **Watch.** Every 2 s, the lobbies and sessions again. The session is
   rebuilt when: the target closes (and PSPStream waits for another to
   open), the session vanishes (Wolf restarted), the target Moonlight
   session moves to another lobby, the audio rate or channels change, or
   the video stops arriving. If the session left the lobby (the Wolf UI
   shortcut), it joins again.
10. **Stop.** `POST /api/v1/sessions/stop`: Wolf sends EOS to the pipelines
    (the TCP connections close), takes the session out of the lobby and
    stops the dummy app. It happens when the container stops (SIGTERM),
    when the target changes and when the lobby closes.

Between one session and the next there is a 1 s pause (the new one has the
same id), and after failures in a row the wait grows up to 30 s: each new
session starts a compositor in Wolf.

## The pipelines

With `-v`, the log shows the exact text. For a lobby `L`, the session `S`,
NVIDIA and the local ports `P1` and `P2`:

```
interpipesrc name=pspstream_S_video listen-to=L_video is-live=true
    stream-sync=restart-ts max-bytes=0 max-buffers=1 leaky-type=downstream
  ! cudaupload ! cudaconvertscale add-borders=true
  ! video/x-raw(memory:CUDAMemory),format=I420,width=480,height=272,pixel-aspect-ratio=1/1
  ! cudadownload
  ! video/x-raw,format=I420,width=480,height=272,pixel-aspect-ratio=1/1
  ! gdppay ! tcpclientsink host=127.0.0.1 port=P1 sync=false

interpipesrc name=pspstream_S_audio listen-to=L_audio is-live=true
    stream-sync=restart-ts max-bytes=0 max-buffers=3 block=false
  ! queue max-size-buffers=3 leaky=downstream ! audioconvert ! audioresample
  ! audio/x-raw,format=S16LE,layout=interleaved,rate=44100,channels=2
  ! gdppay ! tcpclientsink host=127.0.0.1 port=P2 sync=false
```

- Wolf passes the text through `fmt::format` (with `{session_id}`,
  `{width}` etc.): literal braces are escaped (`{{`), and the ids that go
  into the text are checked (letters, digits, `-` and `_`).
- The `interpipesrc` is **not** named `interpipesrc_S_video`, the name Wolf
  looks for to switch the producer when the session joins or leaves a lobby
  (`SwitchStreamProducerEvents`). That way the image always follows the
  target, even when the session joins the lobby because of the controls.
  Side effect: the Wolf log says `Failed to get video interpipesrc for ...`
  at that moment.
- Without an appsink named `wolf_udp_sink`, Wolf runs the pipeline without
  sending anything over Moonlight's RTP.

### The conversion

The lobby image reaches the `interpipesrc` in the memory the producer
leaves it in (`configTOML.cpp`, `producer_buffer_caps`):

| Wolf | memory | `--wolf-video-convert` |
|---|---|---|
| NVIDIA with zero-copy (the default) | `video/x-raw(memory:CUDAMemory)` | `nvidia`: `cudaupload ! cudaconvertscale ! ... ! cudadownload` |
| Intel/AMD with zero-copy | `video/x-raw(memory:DMABuf)` | `va`: `vapostproc` |
| `WOLF_USE_ZERO_COPY=FALSE` | `video/x-raw` (regular memory) | `cpu`: `videoconvertscale` |

The API does not say which case it is, so `auto` tries all three and keeps
the one that worked. The downscale to 480x272 happens on the GPU, inside
Wolf: what comes over TCP is already small (~12 MB/s at 60 fps, only on the
loopback).

### Why TCP with GDP

- `interpipe` only works inside the Wolf process: the image has to leave
  some other way.
- `shmsink`/`unixfdsink` would create a socket file in the shared volume,
  as root (Wolf runs as root), which the PSPStream container could not open
  without changing permissions; `unixfdsrc` also needs GStreamer 1.24+.
- `gdppay` carries the boundaries of each frame and the format (caps): on
  the other side, `gdpdepay` gives back whole buffers.
- The pull model holds: the `interpipesrc` keeps 1 buffer (leaky), the
  appsink here only the newest one, and the frame is encoded when the PSP
  asks.

## The audio

The session's audio pipeline listens to `<lobby>_audio` (the monitor of the
lobby's PulseAudio sink, inside Wolf), converts it to S16LE at the PSP's
rate and channels and sends it over TCP. Here, the usual `AudioCapture`
(`server/audio.py`) receives it with `tcpserversrc ! gdpdepay` in place of
`pulsesrc` and encodes IMA ADPCM in 20 ms blocks.

It was picked over reading Wolf's PulseAudio directly: its socket lives in
a volume Wolf creates, and the audio would not follow the reconnections.
Each Wolf session has its own capture; `WolfAudio` is the interface the
server sees, and it carries the packet numbering from one session to the
next (the PSP treats a lower number as a late packet). Changing the rate or
mono rebuilds the session, because the Wolf pipeline is fixed.

## The controls

`server/wolf_input.py`. `WolfInjector` is the same `GamepadInjector` as
the PC's virtual Xbox controller (`keymap.json` profiles, the SELECT layer,
the dead zone, `--input-timeout`), with the output swapped: instead of
`/dev/uinput`, Moonlight controller packets sent through `POST
/api/v1/sessions/input` (`{session_id, input_packet_hex}`), in a thread
that only sends the newest state.

Wolf turns the hex straight into the `INPUT_PKT` struct, without checking
the size (`api/endpoints.cpp`), so the packet always goes whole:

```
06 02 | 22 00 | 00 00 00 1e | 0c 00 00 00 | 1a 00 00 00 01 00 14 00 00 10 00 00 ...
 type   rest     data (BE)     CONTROLLER_   headerB, controller 0, mask 1, midB,
 0x0206 (LE)                   MULTI (LE)    buttons 0x1000 (A), triggers, axes...
```

- **`CONTROLLER_ARRIVAL`** (`0x55000004`) first, on each new session:
  controller 0, Xbox type, analog triggers, the buttons that exist. In Wolf
  the capabilities field is 1 byte and in Moonlight 2; the Moonlight format
  goes (what real clients send), and Wolf reads the low byte.
- **`CONTROLLER_MULTI`** (`0x0C`) with the state: the Y axis is XInput's
  (up is positive), because inputtino (`joypad_xbox.cpp`, `set_stick`)
  writes `ABS_Y = -y`. The packet above is byte for byte the example from
  the Wolf tests (`tests/testWolfAPI.cpp`).
- On exit, a `CONTROLLER_MULTI` without the controller bit in the mask:
  Wolf turns the virtual controller off.

Wolf creates the virtual controller (inputtino) in the PSPStream session.
For it to reach the game, the session must be **in the lobby**: `LobbyJoin`
moves the session's controllers into the game container, and the ones
created later go straight there (`sessions/lobbies.cpp`). `LobbyJoin`
rules:

- a single-player lobby (`multi_user = false`) with someone in it refuses
  (`Lobby is full`). Wolf UI creates the **Start** button lobbies like that,
  and the **Coop** ones as multiplayer;
- a lobby with a PIN asks for the PIN (`--wolf-pin`);
- in a lobby that closes when everyone leaves (`stop_when_everyone_leaves`),
  the PSP counts as a player. Wolf UI creates lobbies without that.

When refused, PSPStream stays view-only and tries again every 2 s: in a
Start lobby, it joins as soon as Moonlight leaves. START + up + RB on the
controller is the Wolf UI shortcut and takes the session out of the lobby;
PSPStream joins again.

In general, games number the players in the order the controllers show up
in the lobby container: Moonlight's, which joins first, comes before the
PSP. For the PSP to be player 1: a Start lobby with Moonlight out of it, or
Moonlight without a controller.

## Wolf limits that cannot be changed through the API alone

- **One PSPStream session per Wolf.** All sessions created without
  `client_id` have the same id (`state/config.hpp`, `get_client_id`: the
  hash of the empty certificate). Inside one process, a lock makes the new
  capture wait for the old one to stop (the web interface starts the new
  one before stopping the old one).
- **The dummy app** of each session starts a compositor and an audio sink
  (that nobody sees) and creates an empty `<uuid>/dummy` folder in the Wolf
  state directory (`state/sessions.hpp`, `create_stream_session`).
- **No rumble**: Wolf sends rumble through the Moonlight control channel,
  which the PSPStream session does not have (it only logs a warning).
- **No keyboard and mouse**: only the Xbox controller (a keyboard profile
  becomes `xbox`).

## Security

- **The API socket gives full control of Wolf** (pairing clients, starting
  apps). PSPStream only uses: listing lobbies and sessions, creating,
  starting and stopping its own session, sending controller packets,
  joining and leaving the lobby. The socket is only mounted in the
  PSPStream container (`:ro`, the directory), never exposed over TCP.
  Do not mount it in any other container.
- **uid 0 without powers.** Wolf creates the socket as root, without
  changing the permission (`srwxr-xr-x`), and recreates it on every start:
  only the owner can connect, and that is why Wolf UI itself runs as root.
  The image runs as a regular user (10001), and the compose files run it
  with uid 0, but with `cap_drop: ALL`, `no-new-privileges` and a read-only
  file system: the process owns the socket and nothing else. An ACL on the
  socket (`setfacl`) also works with the regular user, but it is lost on
  every Wolf start; a default ACL on the directory does not work, because
  Wolf creates the socket without write access for others (the umask
  applies to sockets).
- **The pipeline Wolf runs** (root, with the GPU) only carries fixed
  elements and checked ids. A custom conversion (`--wolf-video-convert
  "..."`) only works from the command line or the variable; the web
  interface only picks among `auto`, `nvidia`, `va` and `cpu`.
- **The web interface** listens on 127.0.0.1 by default. On the network,
  with `PSPSTREAM_WEB_PASSWORD` (HTTP basic authentication, 1 s wait after
  each wrong password). It refuses addresses it does not know (against DNS
  rebinding: only localhost, the server name, IPs and
  `PSPSTREAM_WEB_HOSTS`), POST requests without `application/json` and with
  an Origin different from the Host.

## Where everything is

| file | what |
|---|---|
| `server/wolf_api.py` | API client: HTTP/1.0 over the Unix socket, standard library only, clear errors |
| `server/wolf_source.py` | `WolfSource` (session, pipelines, ping, target, lobby, reconnection), `WolfAudio`, texts for the API |
| `server/wolf_input.py` | controller packets and `WolfInjector` |
| `server/capture.py` | builds the Wolf source, audio and controls from the options |
| `tests/fake_wolf.py` | the fake Wolf: the API, the ping and the pipelines (with `videotestsrc` in place of `interpipesrc`) |
| `tests/test_wolf.py` | tests against the fake Wolf and the packet bytes |
| `Dockerfile`, `docker/` | the image, the compose files, `.env.example` and `install.sh` |
| `packaging/docker-test.sh`, `compose-check.sh` | the image against the fake Wolf; the compose files and the installer (in CI) |

## What was checked in the Wolf code

| fact | where |
|---|---|
| the producers `interpipesink <id>_video` and `<id>_audio` | `streaming/streaming.cpp`, `start_video_producer`, `start_audio_producer` |
| the API routes and HTTP/1.0 with Content-Length, one request per connection | `api/endpoints.cpp`, `api/unix_socket_server.cpp` |
| the socket at `WOLF_SOCKET_PATH`, or `$XDG_RUNTIME_DIR/wolf.sock` | `api/api.cpp` |
| the pipeline through `fmt::format` and the accepted placeholders | `streaming.cpp`, `start_streaming_video/audio` |
| the mandatory ping, the match by the secret, ports 48100/48200 | `sessions/moonlight.cpp`, `rtp/udp-ping.cpp`, `state/data-structures.hpp` |
| the session without an app and without a client: the dummy app, the same id | `api/endpoints.cpp` (`StreamSessionAdd`), `state/config.hpp` |
| `LobbyJoin`: moving the controllers, full lobby, PIN | `sessions/lobbies.cpp`, `api/endpoints.cpp` (`check_lobby_pin`) |
| the controller packet format | `moonlight/control.hpp`, `control/input_handler.cpp`, moonlight-common-c `Input.h` |
| the inverted Y axis | inputtino `fd136cf` (Wolf's version), `joypad_xbox.cpp` |
| the Wolf UI lobbies: Start and Coop | wolf-ui `App.cs` (`OnStartPressed`, `OnCoopPressed`) |
