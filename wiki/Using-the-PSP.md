First use, the settings screen, the shortcuts during the stream, the overlay
and `server.txt`. How to install: [Installation](Installation).

## First use

On the PC:

```sh
python3 server/pspstream.py --source kms --profile xbox    # or: pspstream --source kms --profile xbox
```

On the PSP, open PSPStream. The first time there is no IP set, and the
settings screen waits: pick **Find the PC on the network** (X) and then
press **START** (saves and connects). The next times, it connects by itself
in 3 s.

Without `--source kms`, the capture goes through the portal: the first time
GNOME/KDE asks which monitor or window to stream, and the choice is saved
(`--forget` asks again).

The PC settings can also be changed in the browser, at
**http://localhost:5124** (see [Web Interface](Web-Interface)).

## Settings screen

It shows up on launch (connects by itself in 3 s if the IP is already there;
any button stops the countdown), with **SELECT + START + R** during the
stream and with **START** when the Wi-Fi or the PC does not answer.

- **Up/Down** picks the item, **Left/Right** changes the value.
- **PC IP address** and **Port**: X enters digit-by-digit editing
  (Left/Right picks the digit, Up/Down changes it, X finishes).
- **Wi-Fi profile**: shows the name saved in the XMB.
- **Find the PC on the network**: turns the Wi-Fi on and sends a broadcast
  ping; the server answers and its IP goes in.
- Transport, H.264, P frames, decoder, vsync, overlay, controls, audio,
  prefetch (auto/yes/no) and the network tweaks: the same options as
  [`server.txt`](#servertxt).
- **Language / Idioma**: English (default) or Portuguese for the PSP
  screens. It changes right away; "Save and connect" keeps it.
- **START** writes `server.txt` and connects; **O** connects without saving.
  When saving, the comments of the old file go away.

## Shortcuts during the stream

Hold **SELECT + START** and press:

| button | does |
|---|---|
| triangle | overlay on/off |
| square | JPEG decoder hardware/software |
| circle | vsync |
| up | audio on/off (the PC stops sending when it is off) |
| cross | prefetch: auto -> yes -> no (see [Prefetch](#prefetch)) |
| L | switches the transport TCP/UDP (reconnects) |
| R | opens the settings screen |

While the shortcut is held, nothing is sent to the PC. SELECT pressed alone
before START reaches the PC.

## Overlay

```
 41.3 fps   1.2 KB  52 KB/s
dec 10.6 ms (h264p) net 9.8 ms udp drop 0
lost 0 nack 1 rep 0 idr 0 ping 7.1 ms (min 5.2, ini 6.3 sel)
asks up to 2 frames ahead when decoding starts (auto)
```

The 4th line says when the next frame is requested: when decoding starts,
allowing up to 2 ahead (P frames with `prefetch=auto`; the frame leaves the
PC at capture time), when so many KB of the current one are left (early
request: JPEG and H.264 with full frames only, or `prefetch=1`) or after
showing (`prefetch=0`). The 5th is the audio:

```
audio 44.1 kHz buf 38 ms (target 40) lost 0 empty 0 skips 0
```

`buf` is the received audio waiting to play, and `target`, how much the PSP
tries to keep (starts at 40 ms; goes up 10 ms each time the buffer runs dry,
`empty`, and down 5 ms every 10 s without a gap, between 30 and 120 ms).
`lost` are packets that did not arrive (they become 20 ms of silence) and
`skips`, audio dropped because too much piled up (the delay does not grow).

`h264p` = H.264 with P frames (`h264`: full frames only; `hw`/`sw`: JPEG).
`idr` counts the full frames requested after a loss, and `rep`, the requests
repeated for lack of an answer (request or whole frame lost, or a still
screen). `nack` counts the requests for missing chunks.

## `server.txt`

It lives in `ms0:/PSP/GAME/PSPStream/server.txt`. Everything can be changed
on the settings screen. By hand:

```
192.168.1.100        # PC IP (optional :port, default 5123)
lang=en              # PSP screens: en (default) | pt
wifi_profile=1       # XMB network profile
transport=udp        # udp (default) | tcp
h264=1               # accepts H.264
h264p=1              # accepts H.264 with P frames
decoder=auto         # JPEG: auto (hardware with software as a fallback) | hw | sw
vsync=1              # 1 = no tearing (+0 to 16 ms); 0 = immediate swap
overlay=1            # FPS, KB/frame, timings
input=1              # PSP controls -> PC
audio=1              # PC audio (UDP only); 0 = the PC does not even send it
prefetch=auto        # auto (default) | 1 | 0 (see below)
early_kb=auto        # UDP: ask for the next frame when this much of the current one is left (auto = round trip x throughput; 0 = at the end)
rxwait=auto          # UDP: auto | select | poll
rcvbuf=64            # socket receive buffer (KB)
bench=0              # 1 = measures hw x sw JPEG decode on the PSP itself on connect
menu_wait=3          # s with the settings screen open before connecting by itself (0 = right away)
```

The PSP only takes the IP as numbers (no DNS names).

### Prefetch

Prefetch is asking for the next frame before the current one is done, so
network and decode work together:

| `prefetch=` | JPEG and H.264 with full frames only | P frames |
|---|---|---|
| `auto` (default) | asks before the end of the arriving frame (1.2-1.7x the FPS, measured on the PSP-3000) | asks when decode takes the current one: **~60 smooth fps** on the PSP-3000 (Hollow Knight). Over UDP, it allows up to 2 frames ahead (v1.1): the frame leaves at capture time |
| `1` | the same | also asks before the end of the arriving frame (early request) |
| `0` | asks after showing the current one | asks after showing the current one: ~45 fps in the same test |

SELECT + START + cross cycles through the three during the stream, and the
overlay shows which one is in effect. Up to v1.0, `0` gave 45 or 60 fps
depending on the history (a stale flag made it ask when decoding started),
and `1` with P frames hitched because of a mistake in counting the
requests; both were fixed in v1.1.

The 2-frame window (v1.1): with only the next one allowed, the request had
to go and the frame be encoded before the next capture (16.7 ms at 60 fps);
with the Wi-Fi swinging, the server missed captures and the PSP-3000 stayed
at 52-55 fps with the source at 60. With 2 ahead, the request is already
waiting on the PC (simulation: 53-55 → 59.5-60 fps, same latency). The PSP
queue holds at most one more ready frame, and only if the network delivers
two at once.
