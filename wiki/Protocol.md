TCP **or** UDP, default port **5123** (the server serves both at the same
time; the PSP picks with `transport=` in `server.txt`). All integers are
**little-endian** (the PSP and x86 PCs are LE, so there is no byte-order
conversion). Definitions in `psp/src/protocol.h` and `server/protocol.py`,
which must stay in sync.

## "Pull" model

The PSP **asks** for each frame, and the server answers with the **most
recent** one available. Frames that went stale while the PSP was busy are
dropped on the PC and never enter the network.

```
PSP                                   PC
 |-- REQ (HELLO|FRAME) ------------->  |
 |  <------------- FRAME 1 (hdr+jpeg)  |   the newest at that moment
 |-- REQ (FRAME, ack=0) ------------>  |   asks for the next BEFORE decoding
 |   decodes and shows 1               |   ... 2 comes over the network meanwhile
 |  <------------- FRAME 2             |
 |-- REQ (FRAME, ack=1) ------------>  |   ack = last frame SHOWN
 |   decodes and shows 2               |
```

- There is only **one frame in flight** at a time, so TCP never builds a
  queue. A queue is what makes latency grow in regular video streams.
- Frameskip is automatic. If the Wi-Fi or the decode gets slow, the PSP
  asks less often and always gets the most current frame.
- Asking before decoding ("prefetch") overlaps network and decode. In that
  mode, what limits the FPS is the slower of the two, not the sum. With
  `prefetch=auto` (default), the PSP asks before the end of the arriving
  frame with JPEG and H.264 with full frames only; with P frames, it asks
  for the next one when decode takes the current one (~60 smooth fps on the
  PSP-3000). `prefetch=0` asks after showing.

## PSP -> PC: request (52 bytes)

| offset | type | field | description |
|---|---|---|---|
| 0 | char[4] | magic | `"PSC5"` (v5; an old EBOOT, `"PSC1"` to `"PSC4"`, is refused with a warning in the log) |
| 4 | u32 | buttons | `PSP_CTRL_*` mask (Milestone 4) |
| 8 | u8 | lx | analog X, 0..255 (128 = center) |
| 9 | u8 | ly | analog Y |
| 10 | u16 | flags | `0x1` FRAME = I want the next frame; `0x2` HELLO = first message; `0x4` NACK, `0x8` BYE and `0x10` PING (UDP only, below); `0x20` IDR = P frames without a reference, send an IDR (below) |
| 12 | u32 | ack_frame | last frame **shown** (0 = none yet) |
| 16 | u32 | echo_ts | `send_ts` of that frame, returned as it came |
| 20 | u16 | net_t | 0.1 ms: request sent -> frame fully received |
| 22 | u16 | local_t | 0.1 ms: frame received -> shown (wait + decode + flip) |
| 24 | u16 | since_t | 0.1 ms: frame shown -> this message sent |
| 26 | u16 | decode_t | 0.1 ms: decode only |
| 28 | u16 | first_t | 0.1 ms: request -> first chunk/byte of the frame (round trip + server reaction) |
| 30 | u16 | burst_t | 0.1 ms: first -> last chunk (gives the real link throughput) |
| 32 | u8 | signal | PSP Wi-Fi signal, % |
| 33 | u8 | wflags | `0x1` = "WLAN Power Save" on in the XMB; `0x2` = waiting for packets by polling, not `select()`; `0x4` = decodes H.264; `0x8` = accepts P frames (v0.9); `0x10` = plays audio (v1.1, UDP only; below) |
| 34 | u16 | lost | UDP: frames abandoned incomplete since the start of the stream |
| 36 | u32 | hdr_have | UDP: id of the JPEG header kept on the PSP (0 = none) |
| 40 | u16 | ping_select | 0.1 ms: pure round trip measured at the start, waiting with `select()` |
| 42 | u16 | ping_poll | 0.1 ms: the same, polling the socket every 0.5 ms (0 = not measured) |
| 44 | u16 | ping_live | 0.1 ms: ping every 1 s **during** the stream, moving average (0 = not yet) |
| 46 | u16 | ping_live_min | 0.1 ms: the lowest of the last 8 |
| 48 | i16 | idle_t | 0.1 ms: last chunk of the previous frame -> 1st chunk of this one ("dead time"; negative = arrived before the previous one completed, queued). `-32768` = not measured (TCP) |
| 50 | u16 | early_b | UDP: bytes left in the current frame when the PSP asked for the next one (0 = only at the end) |

`first_t` and `burst_t` split the network time into round trip (fixed per
frame) and transfer (proportional to the size). Each part has a different
remedy.

A message without the FRAME flag only updates controls and stats. In
Milestone 4 it lets buttons be sent more often than frames.

## PC -> PSP: frame (16 bytes + JPEG)

| offset | type | field | description |
|---|---|---|---|
| 0 | char[4] | magic | `"PSF1"` |
| 4 | u32 | frame_no | starts at 1 and grows with each send on the connection |
| 8 | u32 | size | JPEG bytes that follow (max. 256 KB) |
| 12 | u32 | send_ts | server monotonic clock in ms (32 bits, wraps around) |

The payload is a **baseline 4:2:0** JPEG, at most 480x272, or, with
`--codec h264`, an H.264 Annex B access unit (SPS + PPS + IDR, 480x272).
The PSP tells them apart by the first bytes: `FF D8` is JPEG, `00 00 00 01`
or `00 00 01` is H.264. If it is smaller, the PSP centers it. 4:2:0 is
required by the hardware decoder (`sceJpeg`); the software decoder accepts
any sampling.

### P frames (`--codec h264p`, PSP with `wflags & 0x8`)

The payload is **AUD + frame + AUD + copy + AUD + copy** (AUD =
`00 00 00 01 09 F0`). The frame is IDR (with SPS + PPS) or P; the copies
are P frames with no change (~20 bytes). The PSP decoder only releases
frame N after receiving N+2, and the two copies push the real frame to the
output: the PSP decodes the 3 AUs, without `sceMpegAvcDecodeStop` (which
resets the references), and shows what comes out of the last call. The PSP
recognizes the packet by the AUD at the start and the type by the next NAL
(7 or 5 = IDR, otherwise P).

Rules, because each P depends on the previous one:

- The server only encodes the frame it will send (it never skips an
  already encoded frame), and numbers them in sequence.
- The PSP decodes all of them, in order: ready ones wait in a queue, and a
  complete frame that arrives before an older incomplete one waits for its
  resend. The next one is requested when decode takes the last one in the
  queue; over UDP, with `prefetch=auto`, that request allows up to 2 frames
  ahead (the window, in [UDP](#udp)); with `prefetch=1`, also before the
  end of the arriving frame; with `prefetch=0`, after showing the current
  one. The request made by the decode thread is counted before the
  "deferred request" is released: in the reverse order, the network thread
  saw "nobody asked" and asked for the same frame again, and the phantom
  request stalled the next one until the RTO (the prefetch hitch with P
  frames up to v1.0).
- Frame abandoned after 3 NACKs, a gap in the numbering the resend did not
  cover, or a decode error: the following P frames have no reference. The
  PSP skips those P frames and sends `IDR` (0x20) in every request until it
  decodes an IDR. The server ignores IDR requests for 150 ms after sending
  one (that is what is still on its way).
- An EBOOT without `0x8` gets every frame as IDR (like `--codec h264`).

## Measuring latency without syncing clocks

The server computes, with its own clock only:

```
path    = (now - echo_ts) - since_t     -> send -> shown on the PSP (+ the ack going up)
latency = age + path                    -> frame ready on the PC -> shown on the PSP
```

`age` is how long the frame sat ready on the server before being sent.
`net_t`, `local_t` and `decode_t` break that total down. The real
"glass-to-glass" latency also adds the capture time on the PC (compositor +
GStreamer, a few ms) and the PSP LCD scanout. That can only be measured by
filming both screens (see
[Measurements](Measurements#53-glass-to-glass-latency-the-only-one-that-includes-everything)).

## UDP

The model is the same (pull, one frame in flight). The framing changes:

**PC -> PSP:** each frame goes in chunks of up to 1400 bytes, one per
datagram (24 + 1400 + 28 of IP/UDP = 1452 bytes, fits in Wi-Fi's 1500):

| offset | type | field | description |
|---|---|---|---|
| 0 | char[4] | magic | `"PSU2"` |
| 4 | u32 | frame_no | |
| 8 | u32 | size | total payload size |
| 12 | u32 | send_ts | as in TCP |
| 16 | u16 | chunk | index of this chunk (bytes `chunk*1400 ...` of the payload) |
| 18 | u16 | count | total chunks (`ceil(size/1400)`, max. 256) |
| 20 | u32 | hdr | bits 0-30: id of this JPEG's header (CRC32); bit 31: the payload came **without** it |

**JPEG header only once.** The "header" goes from SOI to the end of the SOS
segment (quantization and Huffman tables, ~620 bytes with `jpegenc`). It is
the same in every frame of the same quality and size. The PSP keeps the
last two it received whole and reports the newest in `hdr_have`. If the
frame's header has that id, the server sends only the rest (bit 31) and the
PSP copies the header back at the start of the buffer. When the quality
changes, the first frame goes whole. Since the id is a CRC of the content,
a kept header stays valid even after restarting the server or the PSP.

**Ping.** Before HELLO, the PSP sends 16 requests with the `PING` flag
(0x10) and `echo_ts` = a token. The server answers right away, without
going through the session, with 8 bytes: `"PSO1"` + the token. Half the
pings wait for the answer with `select()` and half by polling the socket
every 0.5 ms. The medians go in `ping_select`/`ping_poll` of every request.
During the stream, the PSP sends one ping per second (token with bit 31 set
= send time) and reports the average and the minimum in
`ping_live`/`ping_live_min`. Comparing with the "1st chunk" separates the
radio time under the stream traffic from the time of answers with a frame.
With `rxwait=auto`, the PSP switches to waiting by polling if that is more
than 1 ms faster.

**PSP -> PC:** the same 52-byte request, one per datagram. With the `NACK`
flag (0x4), right after it comes:

| offset | type | field | description |
|---|---|---|---|
| 52 | u32 | frame_no | incomplete frame |
| 56 | u32[8] | missing | bit `i` = chunk `i` missing |

The server resends only those chunks. It keeps the last 4 frames sent.

**FRAME + NACK** (P frames): request for a frame with its number (all
chunks marked). A small P frame fits in one packet; if it vanishes, the PSP
does not even know the frame existed, and a new frame would arrive without
its reference. If the server already sent that frame (more than 15 ms ago),
it resends the same one and does not send another; if it sent it less than
15 ms ago, it is still on its way and the request is ignored; if it has not
sent it yet, it counts as a normal request (and if a request is already
waiting for a new frame, the two become one).

Since v1.0, with P frames over UDP, **every new frame request** goes like
that, with the number after the highest frame seen so far, and goes **again
6 ms later** if no chunk of that frame arrived. By the rules above, the copy
never becomes one more frame: either it merges with the waiting request, or
it arrives with the frame already in the air. But if the original was lost
on the way up, the copy is the request, and the frame leaves 6 ms later
instead of waiting for the RTO (>= 30 ms) with the stream stopped (a P
cannot be skipped). The request repeated for lack of an answer stays the
same, with the NACK of the last complete one + 1.

**2-frame window** (v1.1, P frames with `prefetch=auto` over UDP): the
number in FRAME + NACK now means "you may send **up to** this frame". When
the decode thread takes frame N, the PSP sends FRAME + NACK(N+2). The
server keeps the credit (`want_upto`, at most the last one sent + 2) and,
while `frame_no < want_upto`, sends each new frame as soon as it is
captured. A plain request (without NACK) still means "the next one"; copies
and old requests do not add up (the credit only goes up). With only N+1
allowed, the request left when N arrived and had to reach the PC, and the
frame be encoded, before the next capture (16.7 ms at 60 fps); with the
Wi-Fi swinging, the server missed captures (52-55 fps on the PSP-3000 with
the source at 60). In this mode the request does **not** go again after
6 ms: the frame leaves at capture time, not at request time, and the next
request covers a lost one.

**Whole frame lost with the window:** N+1 can vanish and N+2 arrive before
any repeated request. The server numbers the frames in sequence and sends
them in order, so the PSP knows: `done + 2` arrived without any chunk of
`done + 1`. It immediately sends a NACK with all the chunks of `done + 1`
(size still unknown; the server resends the ones that exist), and the
complete `done + 2` waits for it, as it waits for an older incomplete one.
If the resend does not come after 3 NACKs, it asks for an IDR.

**Last chunk twice** (P frames, server v1.0): the last chunk of each P
packet goes again 6 ms later (`--p-redundancy-ms`; the PSP ignores what it
already has). Losing the last chunk was the slow case: with no following
chunk, the PSP only notices by the silence (20-50 ms) and the NACK takes
one more round trip. A small P frame is a single chunk, so the copy also
covers the whole lost frame. It costs 1 packet per frame: ~40 KB/s at
60 fps on a game clip (+27-53% over the P frames), still well below the
350-450 KB/s of H.264 with full frames only. `BYE` (0x8) says the PSP app
is quitting: the server releases the keys and ends the session right away
(over UDP there is no "close connection").

**Timings on the PSP** (`psp/src/stream.c`):

| situation | action |
|---|---|
| the last chunk arrived and others are missing | NACK right away (chunks come in order: the missing ones were lost) |
| incomplete frame without a new chunk for the mean + 4 deviations of the gap between chunks (20-50 ms) | NACK with the missing ones (the end of the frame was lost) |
| after a NACK | waits for the answer for the mean + 4 deviations of the round trip (request -> 1st chunk, 30-200 ms); each resent chunk that arrives postpones the wait |
| the last chunk of the resend arrived and others are still missing | NACK again right away |
| 3 NACKs without completing | gives up on the frame (counts as "lost") and asks for another |
| NACK time, but a newer frame is already arriving | gives up on the frame without a NACK: the resend would come queued behind the newer one (P frames: sends the NACK, the newer one depends on it) |
| P frames: new frame request without any chunk of it in 6 ms | sends the same request again, once (see above; with the window, no) |
| P frames: `done + 2` arrives without anything of `done + 1` | NACK of the whole `done + 1` right away; `done + 2` waits (see above) |
| request with no answer for a measured round trip (mean + 4 deviations of request -> 1st chunk, 30-200 ms) | resends the request (P frames: with a NACK of the expected frame, see above) |
| P frames: a ready frame waiting for decode | the next one is requested by the decode thread when it takes that frame (with the window, up to 2 ahead); until then there is no request to repeat |
| 3 s without completing any frame | the request goes with HELLO (the server may have restarted) |
| chunk of an older or duplicate frame | ignored |

**Early request** (`early_kb`, default `auto`): when `early_kb` KB of the
current frame are left, the PSP already asks for the next one. With
`auto`, the limit is the round trip times the throughput: the median
initial ping divided by the average gap between chunks, times 1400 bytes
(~2-3 KB on the PSP-3000, capped at 8 KB). That way the 1st chunk of the
next one arrives right after the last one of the current frame, without an
idle radio and without a queue. Fixed values of 6-14 KB, tested on the
PSP-3000 with JPEG, asked too early: the next frame waited whole in the
router queue and latency rose. A frame with a gap does not ask early, so
the next one does not get in front of the resend. `early_kb=0` asks only at
the end, as up to v0.7. With an early request there can be **two frames
being reassembled**. When a newer one completes, the older incomplete one
is abandoned: showing N after N+1 is useless, and waiting for the NACK
would delay N+1. In practice, a loss at the end of a frame becomes a frame
skip instead of a stall. The reported "network" counts from when the radio
was free for that frame (`max(request, previous frame complete)`).

On the server side, there is at most **one pending request**: requests
repeated while it waits for a new frame do not become a burst of frames.
The exception is the P frame window (above): a credit of up to 2 frames,
given by the number in the request. A UDP session starts with a HELLO (or
with any request, if there is no active session) and is identified by the
PSP's IP:port.

**Controls over UDP:** each change is sent twice (again in the next
sample). While something is held, the state is restated every ~100 ms, over
TCP and UDP.

## Audio (UDP, v1.1)

Audio does not follow the pull model: while the PSP's latest request has
`wflags & 0x10`, the server **pushes** a packet every ~20 ms to the UDP
session address. Without the bit (`audio=0` in `server.txt`, or turned off
with SELECT + START + up), no audio packet goes out, and an old EBOOT never
gets audio.

| offset | type | field | description |
|---|---|---|---|
| 0 | char[4] | magic | `PSA1` |
| 4 | u32 | seq | +1 per packet; a gap is a lost packet |
| 8 | u32 | pos | sample (per channel) at the start of the block |
| 12 | u16 | rate | Hz: 22050, 32000, 44100 (default, the PSP's) or 48000 |
| 14 | u8 | channels | 1 or 2 |
| 15 | u8 | codec | `1` = IMA ADPCM, WAV block (GStreamer's `adpcmenc`, dvi layout) |
| 16 | u16 | samples | samples per channel in the block (881 at 44.1 kHz: 1 + 8 x 110) |
| 18 | u16 | reserved | 0 |
| 20 | | block | per channel, 4 bytes (1st sample int16, step index, 0); then groups of 4 bytes (8 samples) alternating the channels, low nibble first |

Each block decodes on its own. The PSP decodes into a ring and plays
through `sceAudioSRC` (a channel with rate conversion), in chunks of 256
samples, alternating two buffers (the hardware reads the chunk while it
plays). The ring starts playing with 40 ms and adjusts: +10 ms each time it
runs dry, -5 ms every 10 s without a gap, between 30 and 120 ms. A lost
packet (up to 5 in a row) becomes silence of the same length; a bigger gap,
or `seq` going back (server restarted), restarts the ring. Audio above the
target + 40 ms is dropped down to the target, so the delay does not grow
(bursts after a delay, or the PC clock slightly faster than the PSP's).

## Connection

- One PSP at a time. A new connection (TCP, or HELLO over UDP) drops the
  previous one (the PSP may have restarted the app and left a half-open
  session).
- If no new frame shows up in 1 s (still screen on Wayland), the server
  resends the last one so the connection stays alive (with P frames, a P
  with no change, ~100 bytes). That resend stays out of the latency average
  (the image did not change) and shows up as "resends".
- **Find the PC on the network** (PSP settings screen): the PSP sends the
  52-byte request with `PING` (0x10) to 255.255.255.255 and to the subnet
  broadcast, on the configured port, every 200 ms for up to 2 s. The server
  answers a ping from any address, without a session, and the source IP of
  the `ps_pong_t` becomes the PC's IP. The `echo_ts` of the search ping has
  bit 30 set, so it is not confused with the stream pings.
- If the PSP goes 10 s without sending anything, the server ends the
  session and goes back to waiting for connections.
- **Stuck key:** if the PSP goes 500 ms without sending anything while a
  key or the analog stick is held (`--input-timeout`), the server releases
  everything. Since the PSP restates the state every ~100 ms, that only
  happens if the network stalls.
