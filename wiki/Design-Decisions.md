Why PSPStream is built the way it is. The numbers behind each choice are in
[Measurements](Measurements), and the message format, in
[Protocol](Protocol).

- **"Pull" model** (the core idea of RNDS-Stream). The PSP asks for a frame
  and the server answers with the most recent one, encoded on the spot. No
  queue ever builds up on the network, and latency stays close to one frame.
- **Server in Python, the heavy work in C.** Capture, downscaling, color
  conversion and encoding are GStreamer, openh264 and OpenGL (C/C++, GPU);
  the KMS helper is C. Python only stitches it together: it reads requests,
  calls the encoder and cuts the frame into packets. Measured at 60 fps: the
  Python threads use 3-6% of a core, and from the PSP request to the 1st
  packet (JPEG/H.264 already done) it takes 0.43-0.48 ms on localhost, the
  same as a ping. Rewriting it in C or Rust would save some 20-30 MB of RAM
  and nothing noticeable in latency.
- **openh264 called directly.** Through GStreamer (appsrc -> openh264enc ->
  appsink), each of the 3 AUs of the P packet went through two queues and
  two threads, and the QP only changed by rebuilding the encoder (an IDR).
  With libopenh264 directly (ctypes): request -> 1st packet went from 2.8 to
  1.8 ms, and the quality changes without an IDR. The stream is the same,
  byte for byte; if the library is missing or its layout does not match, it
  falls back to GStreamer by itself.
- **P frames with 2 copies.** The PSP decoder only releases frame N after
  N+2, and `sceMpegAvcDecodeStop`, which releases it right away, resets the
  references. Each packet carries the frame and 2 copies (P with no change,
  ~20-80 bytes): the PSP makes 3 calls and shows the real frame, with no
  delay. Stop only goes in before an IDR, and it is mandatory there (without
  it, the PSP powers off).
- **UDP (default) and TCP, both with the pull model.** On the PSP-3000, the
  Wi-Fi loses 1-3% of the packets; over TCP, each loss with a frame in
  flight becomes a retransmission timeout, and the video **and the
  controls** stalled for hundreds of ms. Over UDP, a lost chunk comes back
  through the NACK, and a whole lost frame, through the repeated request.
- **Early request, sized right.** The PSP asks for the next frame when what
  is left of the current one takes a round trip to arrive (initial ping x
  throughput, ~2-3 KB on the PSP-3000). Larger fixed values, which looked
  good in the simulation, built a queue in the router on the real PSP.
- **P frames requested when decoding starts (`prefetch=auto`).** The next
  one arrives while the current one decodes, never with two frames in the
  queue and without asking in the middle of an arriving frame. ~60 smooth
  fps on the PSP-3000. Asking only after showing gave ~45 fps. Over UDP, the
  request allows up to 2 frames ahead (v1.1): the server keeps the credit
  and sends each frame at capture time, without waiting for the round trip
  of that request.
- **Losses in P frames.** Each P needs the previous one, so losing a packet
  stops the stream until the resend. The server sends the last chunk of
  each P frame again 6 ms later (losing the last one was only noticed by
  the silence). Without the window, the PSP repeats the new frame request
  after 6 ms, with the frame number so the server recognizes the copy; with
  it, the next request covers a lost one, and a frame that vanishes
  entirely is noticed when the next one arrives (the PSP asks for the
  resend right away).
- **`--fps` on a fixed grid.** The limit counts each frame's turn from the
  previous turn, not from the frame that arrived: a frame delayed by the
  capture does not push the next ones, and the rate comes out exact (the
  previous limit cut frames with timestamps jittering 2-3 ms).
- **Audio in IMA ADPCM, pushed.** 4 bits per sample (~46 KB/s at 44.1 kHz
  stereo, the PSP rate: the PSP does not resample), encoded in C by
  `adpcmenc` (~2% of a core on the PC), and decoded on the PSP CPU with
  additions and shifts. MP3 or ATRAC would weigh less on the network, but
  would add 50-100 ms of delay and fight with H.264 for the Media Engine.
  Audio does not depend on the video request (a video stall does not cut the
  audio), and goes in 20 ms packets: with 10 ms, there would be 100 packets
  per second fighting the video for 802.11b airtime.
- **GStreamer instead of ffmpeg** for the capture: the portal delivers
  PipeWire, which GStreamer reads natively, and everything runs inside the
  process.
- **Direct writes to the framebuffer** (stride 512), without sceGu: the
  decoders write to VRAM, with no copies.
- **Thread priorities on the PSP**: decode sits below the TCP/IP stack,
  otherwise the network stops during decode.
- **`-lpspnet_inet` and `-lpsputility` out of `LIBS`**: psp-gcc already adds
  them; listed twice, the stubs split and the loader reads the wrong NIDs.
- **Wolf through the API only.** `--source wolf` changes nothing in Wolf: it
  creates its own session through the API socket and receives the image and
  the audio over local TCP. The reason for each part is in
  [Wolf Internals](Wolf-Internals).
- **English by default, Portuguese as an option.** Server messages go
  through `tr()` with the English text as the key and the Portuguese one in
  `server/lang_pt.py` (a test checks that every message has a translation
  with the same fields); the PSP uses `T(en, pt)`, plain ASCII because the
  PSP font has no accents.
