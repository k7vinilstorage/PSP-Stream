The development measurement notebook, in order: each section records what
was known at that moment, including hypotheses that later numbers knocked
down. The current summary is in [Performance](Performance).

Every number here has a source, and they do not mix:

| tag | source | does it hold for the PSP? |
|---|---|---|
| **[PC]** | really measured on the development PC (x86 CPU, 4 cores) | yes, for the PC part (frame size, capture/encode cost) |
| **[SIM]** | `tools/fake_client.py` with simulated Wi-Fi and decode | no: it is a model of the pipeline |
| **[EMU]** | PPSSPPHeadless | no: emulated time is not hardware time |
| **[PSP]** | real PSP-3000 (ARK-4) + Fedora 44, measured by the user | yes |
| **[PSP report]** | what the user saw while playing on the PSP-3000, without written-down numbers | yes, but qualitative |

## 0. Real PSP numbers [PSP]

### Decode (`bench=1`, 480x272 frame)

| decoder | q20 | q70 | q90 |
|---|---|---|---|
| hw (sceJpeg) | ~7.9 ms | 7.91 ms | ~7.9 ms |
| sw (libjpeg-turbo) | ~34 ms | 34 ms | ~34 ms |

The hardware is ~4.3x faster, and the time hardly depends on the quality.
With hw, decode would only limit the stream above ~120 fps, so the
bottleneck is all on the network. `decoder=auto` already uses hw.

### First `--bench` (TCP, `--source portal`, real screen)

| q | KB/frame | FPS | Wi-Fi (KB/s) | average latency (ms) | p95 (ms) | network (ms) | decode (ms) | PSP received->shown (ms) |
|---|---|---|---|---|---|---|---|---|
| 30 | 11.1 | 3.5 | 235 | 129.9 | 775.4 | 83.2 | 7.6 | 18.5 |
| 50 | 15.2 | 6.7 | 315 | 90.7 | 106.0 | 58.5 | 7.6 | 9.4 |
| 70 | 20.7 | 6.2 | 356 | 110.5 | 163.7 | 81.3 | 7.7 | 8.9 |
| 90 | 36.4 | 3.0 | 316 | 214.2 | 960.1 | 173.4 | 8.7 | 10.0 |

Reading:

- **Real Wi-Fi throughput: ~235-356 KB/s** (median per frame). It fell
  within the expected range for 802.11b with TCP.
- **The FPS (3-7) is not a network limit.** At q50, 58 ms of network + 8 ms
  of decode would give ~15 fps. The ~80 ms missing per frame are the server
  **waiting for a new frame from the portal**: GNOME only sends a frame when
  the screen changes. That version of the table did not show that wait yet;
  the "source (fps)" and "wait for a new frame" columns were added after
  this test.
- **The 775/960 ms p95 is an artifact**: with a still screen, the server
  resends the last frame every 1 s, and that resend went into the average
  with an "age" of ~1 s. Now it stays out and is counted in "resends 1 s".
- The network is most of the remaining latency (58-81 ms at q50-q70).
  That is why it is worth comparing with the UDP transport.

### TCP x UDP (`--bench`, `--source static`, same image)

| q | KB/frame | UDP: FPS | UDP: latency / p95 | UDP: network | UDP: chunks resent | TCP: FPS | TCP: latency / p95 | TCP: network |
|---|---|---|---|---|---|---|---|---|
| 30 | 7.4 | 26.9 | 34 / 55 ms | 36 ms | 0.9% | 0.5* | 1314 / 6445 ms* | 53 ms |
| 50 | 9.9 | 21.8 | 42 / 63 ms | 50 ms | 1.3% | 8.5 | 203 / 805 ms | 169 ms |
| 70 | 13.1 | 19.6 | 47 / 72 ms | 51 ms | 2.1% | 14.1 | 67 / 66 ms | 59 ms |
| 90 | 24.2 | 13.9 | 74 / 151 ms | 72 ms | 2.7% | 3.0 | 265 / 1038 ms | 257 ms |

\* The TCP q30 phase includes a ~6 s pause from the decode benchmark
(`bench=1` was left on). After that, the benchmark frame stopped going into
the stats.

- **The Wi-Fi loses 1-3% of the packets** ("chunks resent" column). With
  one frame in flight, each TCP loss becomes a retransmission timeout, with
  stalls from hundreds of ms to seconds. UDP recovers with a NACK in
  milliseconds. **UDP became the default.**
- In real use with TCP (portal, game running), the server logged three
  times "PSP has sent nothing for ~505 ms, releasing everything". It was the
  PSP TCP stack stalled, with the controls stuck behind it. That was the
  cause of the stuck key.
- The game capture through the portal delivers **~37 fps** ("source"). The
  limit was in the transport, not in the capture.
- Fitting a line to the UDP "network" column (7.4 KB -> 36 ms, 24.2 KB ->
  72 ms): **a ~470 KB/s link + ~21 ms fixed per frame** (the request round
  trip). Those 21 ms with an idle radio motivated the early request.

### Early request (`early_kb`): the simulation was wrong, the PSP decided [PSP]

The hypothesis: the ~21 ms fixed per frame (from the line above) would be
an idle radio waiting for the request to go and come back. Asking for the
next frame before the current one finishes would hide that time. The
simulation (`fake_client --rtt-ms 21 --kbps 470`) predicted 17.8 -> 28.9
fps with the same latency.

On the PSP-3000 (UDP, `--source static`, same image; order of the runs: 10, 14, 0, 6):

| early_kb | q30: fps / lat / p95 | q50: fps / lat / p95 | q70: fps / lat / p95 | q90: fps / lat / p95 |
|---|---|---|---|---|
| **0** | 20.3 / **38** / **89** | **19.7** / **46** / **75** | 16.6 / **51** / **80** | 10.9 / 90 / 162 |
| 6 | 20.7 / 48 / 105 | 16.5 / 61 / 143 | 15.8 / 67 / 115 | 10.5 / 89 / 160 |
| 10 | 22.4 / 49 / 100 | 16.0 / 64 / 126 | 15.1 / 73 / 134 | 11.4 / 105 / 175 |
| 14 | 14.9 / 65 / 137 | 14.0 / 76 / 155 | 17.5 / 76 / 126 | 12.2 / 114 / 188 |

**The FPS did not go up and the latency got 10-30 ms worse (p95 almost
doubled).** The simulation model was wrong: those ~21 ms are not an idle
radio. 802.11b is half duplex. The early request fights for the air with
the frame still arriving, and the next frame just waits in the router queue
(that is the extra latency). The 21 ms are air cost per frame (medium
contention, ACKs), not waiting. **`early_kb=0` went back to being the
default.**

Other observations from these runs:

- **Variation between runs:** the same configuration (without early
  request) gave q30 = 26.9 fps on the first day and 20.3 fps now. The Wi-Fi
  varies about 20%. Only differences bigger than that count, like the p95,
  which consistently doubled.
- **Loss grows with the frame size:** ~1% of the chunks at q30 (6 chunks
  per frame) and 4-8% at q90 (18 chunks). That points to bursts overflowing
  some buffer (router or PSP queue). A possible test: `--udp-pace 450`
  spaces the chunks at the link speed.
- **Adaptive quality target:** even q30 takes ~46 ms of network, so the old
  `--target-fps 30` was unreachable and pushed the quality to the minimum.
  The default is now **20 fps**, which leads to ~q55: ~20 fps and ~48 ms.

### Optimization plan: where the time goes and what each idea yields

Model taken from the PSP measurements (UDP): **each frame costs ~21-25 ms
fixed + size / (360-470 KB/s)**. At q50 (~10 KB), half the time is the
fixed part. That decides what is worth it:

| idea | attacks | measured here [PC] | estimated gain at q50 |
|---|---|---|---|
| sending the JPEG header (623 bytes) only once (**done, v0.4**) | proportional part | 6.0% of the frame at q30, 4.5% at q50, 3.3% at q70 (games + desktop) | ~3-5% FPS |
| optimized Huffman tables | proportional part | 4-6% of the frame | ~2-3% FPS |
| 400x228 + upscaling on the PSP (sceGu) | proportional part | 67% of the bytes | ~+20% FPS, softer image |
| 360x204 + upscaling | proportional part | 59% of the bytes | ~+25% FPS |
| multithreaded downscale on the PC (done) | capture on the PC | 4.0 -> 2.5 ms/frame at 2240x1400 | -1.5 ms latency |
| fast NACK + a round-trip wait (done) | losses | the 6 ms floor **got worse on the PSP**; fixed (below) | lower p95 with loss |
| early request | fixed part | fixed at 6-14 KB **got worse on the PSP**; v0.8: automatic (~2-3 KB), +12-33% FPS [SIM] | to be measured on the PSP |
| **finding out the ~25 ms fixed** | fixed part | `first_t` (v2) + ping at the start (v0.4) | up to ~2x FPS if it is something fixable |
| server reaction (request -> 1st chunk sent) | fixed part | **0.6 ms** (median; p95 1.2-2.4 ms) on localhost, static and live source | nothing to gain: it is not the server |
| waiting for packets by polling the socket instead of `select()` (`rxwait=auto`, v0.4) | fixed part | the PSP measures both on connect | depends on how long the PSP's `select()` takes to wake up |
| DSCP EF / WMM voice queue (`--dscp`, v0.4) | fixed part (queue in the PC card and in the router) | not measured | probably small on an empty home network |
| H.264 on the PSP Media Engine instead of MJPEG | proportional part | not measured (see below) | 2-3x fewer bytes at the same quality |

Why the fixed part comes first: on normal Wi-Fi, the round trip takes 2-5
ms, not 25. Suspects, each with a remedy:

1. **PSP Wi-Fi power save** (the router holds the packets until the PSP
   wakes up). The server now logs the state the PSP reports.
2. **PC on Wi-Fi**, especially with the card's power save on
   (NetworkManager's default on many laptops). The server now warns at
   startup. The user ruled this suspect out: the same network handles heavy
   streaming (Moonlight) between PCs without trouble.
3. **The PSP network stack** delivering late: it shows as a high `first_t`
   even with the two above ruled out.
4. Link at 5.5/2 Mbps (bad signal, interference): it shows as a low "burst
   throughput" (< 300 KB/s) and not as a high `first_t`.

The next `--bench` brings the "1st chunk" and "burst throughput" columns.
With them, we know which case it is before touching more code.

### First `--bench` with `first_t` (protocol v2): a regression of mine [PSP]

UDP, `--source static`, same image. At startup, the server warned: **PC on
Wi-Fi (`wlp0s20f3`) with power save ON**. The PSP reported 100% signal and
WLAN power save off.

| q | KB/frame | FPS | latency / p95 (ms) | network (ms) | 1st chunk (ms) | burst (ms) | burst throughput (KB/s) | chunks resent | frames lost |
|---|---|---|---|---|---|---|---|---|---|
| 30 | 7.4 | 19.4 | 41.8 / 99.2 | 52.1 | 36.6 | 15.7 | 448 | 19.3% | 2 |
| 50 | 9.9 | 13.5 | 60.3 / 127.4 | 75.6 | 51.0 | 24.8 | 394 | 42.9% | 12 |
| 70 | 13.1 | 10.1 | 89.4 / 184.5 | 95.4 | 57.5 | 38.0 | 356 | 56.5% | 14 |
| 90 | 24.2 | 7.5 | 170.7 / 362.1 | 131.1 | 66.8 | 64.4 | 364 | 66.1% | 13 |

In the previous run, with the same `early_kb=0`, it was q50 = 19.7 fps and
1-8% resent. **The culprit is version 8ab5795**, which brought the "fast
NACK + adaptive interval":

- After a NACK, the PSP waited only the gap between chunks (6 ms floor)
  before the next one. The answer takes a whole round trip (20-40 ms).
  Result: 3 NACKs for the same chunks in ~18 ms, frame abandoned before the
  first resend arrived ("frames lost" 12-14), and the server sending each
  missing chunk up to 3 times ("resent" 19-66%).
- Those repeated resends take up the air and the router queue **in front
  of the next frame**. That is why the "1st chunk" grows with the quality
  (36 -> 67 ms) along with the resends. In this test, it does not measure
  the clean round trip.
- The average gap between chunks was also badly measured: the PSP reads the
  already queued chunks in a row, with a ~0 gap. The average dropped, and 4x
  the average hit the 6 ms floor. Any normal Wi-Fi pause became a "loss".

The simulator did not catch the problem because it still used the old
PSP's fixed 20 ms wait.

**Fix (v0.3.1):**

- After a NACK, the PSP waits the mean + 4 deviations of the measured
  round trip (request -> 1st chunk, like TCP's RTO; 30-200 ms).
- The silence that means "end of the frame lost" is the mean + 4 deviations
  of the gap between chunks, from 20 to 50 ms. The deviation covers the
  burst reads.
- `fake_client` now follows the same logic, and a test with a 30 ms round
  trip and 5% loss requires zero repeated chunks. With the old wait, the
  same test gives 34-35 repeated in 3 s [SIM].

What these numbers already say, even with the regression:

- **Burst throughput of 356-448 KB/s**: the link is fine (suspect 4 ruled
  out). The part proportional to the size is 802.11b doing what it can.
- **PSP without power save and with 100% signal** (suspect 1 ruled out).
- **The fixed part still has to be measured without the repeated resends
  and without the PC power save.** In the previous runs the PC power save
  was probably already on. So it does not explain everything, but it is the
  remaining suspect (2), and the test is cheap.

### H.264: Moonlight-PSP and the hardware decoder

**[Moonlight-PSP](https://github.com/k4idyn/Moonlight-PSP) uses H.264, but
decoded in software** (OpenH264 on the main CPU; the Media Engine only
converts the colors). According to its changelog, at 480x272 the result is
15-18 fps (the "Quality" preset uses 10 fps), limited by the decode and not
by the network (500 kbps). Today MJPEG with `sceJpeg` already gives ~20 fps
at 480x272. Copying that path would be a step back. The author tried the
hardware decoder (`sceMpeg`) and gave up: the ringbuffer expects MPEG-PS in
order, and Moonlight's RTP delivers chunks out of order with FEC in the
middle. **That does not apply to PSPStream**, which delivers each frame
whole and in order (NACK).

**The hardware decoder is fast.** PPSSPP measured on a real PSP
(pspautotests `video/mpeg/playertiming`): ~3.4 ms to decode a 480x272
frame, plus 2.4 ms to convert to RGBA. That is less than `sceJpeg`'s
7.9 ms. The question was how to call it with raw H.264:

- **PMP Mod / PMPlayer** (2006, magiK's code) did it: an empty ringbuffer,
  `sceMpegBasePESpacketCopy` taking the frame (Annex B) to the Media Engine
  memory in 4095-byte blocks, and `sceMpegAvcDecode`, which already returns
  RGBA 8888 with a 512 width. PPSSPP emulates that path.
- pspautotests `video/mp4/mp4timing` shows another one (no ringbuffer, with
  `sceMpegAvcResourceInit`), tested on hardware, but PPSSPP only runs it
  with the firmware's original `mpeg.prx`.

**`psp/probe` uses the PMP path** [EMU]: on PPSSPP (with
`tools/ppsspp-pmp-fix.patch`), the 60 frames of each clip (baseline/CAVLC
and main/CABAC) decode, including straight into VRAM, and the number drawn
on each frame matches the AU delivered. The emulator decodes with FFmpeg in
low-latency mode and with fixed timing, so it **does not answer** the
questions that matter: the real time and whether the PSP holds frames. Two
mistakes of mine that would have frozen the PSP showed up in the emulator
and were fixed: pspsdk's `SceMpegRingbuffer` has 44 bytes and the library
writes 48, and an unaligned u32 read.

#### Result on the PSP-3000 (6.61 ARK-4), test v1 [PSP]

| step | AUs ok | decode per frame (RGBA included) | frames held |
|---|---|---|---|
| baseline/CAVLC in RAM | 60/60 | 4.05 ms (3.02-4.13) | **2** |
| main/CABAC in RAM | 60/60 | 4.17 ms (3.10-4.26) | **2** |
| baseline straight into VRAM | 60/60 | 3.55 ms (3.00-3.61) | **2** |
| baseline without frame 20 | 59/60, no error and no freeze | 4.05 ms | 2 to 8 (wrong image until the next IDR, as expected) |

- **It works** when called from a regular app, and it is fast: half of
  `sceJpeg` (7.9 ms), even writing straight into VRAM. CABAC costs only ~3%
  more.
- **But frame N only comes out after delivering N+2**: the first two calls
  come back without an image, and then it always outputs the one from 2 AUs
  before. The SPS already says `max_num_reorder_frames=0` and
  `max_dec_frame_buffering=1`, so it is not reordering asked for by the
  stream: it is a fixed pipeline depth of the decoder. With one frame per
  call, at 20 fps, that would be +100 ms of latency.
- If the delay is counted in calls, the frame can be pushed out with cheap
  calls. Test v2 compares three ways: the frame + 2 copies (P with no
  change, ~300 bytes in the clip), the frame + 2 AUs with only the AUD, and
  the frame + `sceMpegAvcDecodeStop`.

#### Test v2: releasing the 2 held frames [PSP]

| way | calls ok | time per frame shown | delay |
|---|---|---|---|
| 1 call per frame | 60/60 | 4.05 ms | 2 frames |
| **frame + 2 copies** | 180/180 | **12.1 ms** (each copy ~4.0 ms) | **0** |
| frame + 2 AUs with only the AUD | error `80628002` | (26 ms per call with an error) | 2 |
| frame + `sceMpegAvcDecodeStop` | 60/60; Stop releases 1 image, 1.12 ms | 4.2 + 1.1 ms | 0 on the 1st frame, **up to 58 afterwards** |

- The copies work, but each call costs ~4 ms even with nothing to decode:
  12 ms per frame, against JPEG's 7.9 ms.
- Stop releases the image right away and is cheap, but it resets the
  references. The 1st frame (IDR) came out right, and the following P
  frames came out wrong. **With every frame an IDR, Stop would have nothing
  to break.**

#### IDR-only (intra) H.264 x JPEG, same quality [PC]

The same 12 frames (2 desktop, 10 games) downscaled to 480x272. JPEG from
`jpegenc` (the server's) and x264 baseline with `keyint=1`, at the smallest
size with equal or higher SSIM:

| JPEG | KB (JPEG) | KB (H.264 intra, same SSIM) |
|---|---|---|
| q50 | 13.7 | **6.8 (49%)** |
| q70 | 18.5 | **8.4 (46%)** |

**Half the bytes, and each frame stays independent** (as with MJPEG: a
loss only spoils that frame, and the server can skip frames at will). If
"IDR + Stop" comes out right away on the PSP, that is ~5.3 ms of decode
(less than JPEG's 7.9 ms) with half the bytes on the network. By the cost
per frame model (~21 ms fixed + size / ~400 KB/s), at q50 that would be
~38 ms instead of ~55 ms per frame. Test v3 checks that on the PSP: intra
with and without Stop, CABAC, a skipped frame and Stop writing straight
into VRAM.

#### Test v3: every frame IDR + Stop [PSP]

| step | time per frame | delay |
|---|---|---|
| intra, 1 call | 4.16 ms | 1 frame |
| **intra + Stop** | **4.23 ms** (Stop 1.12 ms) | **0** |
| intra CABAC + Stop | 4.30 ms, 11% fewer bytes | 0 |
| intra + Stop, without frame 10 | 4.24 ms, no errors | 0 |
| **intra + Stop straight into VRAM** | **3.72 ms** (Stop 0.62 ms) | **0** |

**That is what went into the stream (v0.5, `--codec h264`).** Compared
with `sceJpeg`: half the decode time, ~40% of the bytes at the same
quality, no frame of delay, and each frame independent as with MJPEG.

#### Server encoder [PC]

- **x264enc (GStreamer) holds 1 frame** in every configuration tested
  (`tune=zerolatency`, `threads=1`, `rc-lookahead=0`, `sync-lookahead=0`,
  `pass=quant`): the AU of frame N only comes out after N+1 goes in. On a
  still screen (the portal only sends a frame when something changes), the
  last change would never come out. Discarded.
- **openh264enc delivers right away.** It only does baseline/CAVLC (11%
  bigger than CABAC in the PSP test). With `rate-control=off` it ignores
  `qp-min/qp-max`; with `rate-control=quality`, a high bitrate and
  `qp-min = qp-max`, the QP applies. The QP does not change with the
  pipeline running: since every frame is an IDR, the server rebuilds the
  encoder pipeline when the quality changes.
- Calibration (12 frames, desktop + games, same SSIM as `jpegenc`), 3.7 ms
  of encode per frame:

| JPEG | KB | openh264 QP | KB | % of JPEG |
|---|---|---|---|---|
| q30 | 10.2 | 40 | 4.0 | 40% |
| q50 | 13.6 | 36 | 6.1 | 45% |
| q70 | 18.3 | 34 | 7.7 | 42% |
| q90 | 34.5 | 30 | 11.3 | 33% |

  Line used: `QP = 44.6 - 0.16·q` (q on the JPEG scale).

#### v0.5 on the PSP-3000: H.264 x JPEG in the real stream [PSP]

UDP, `--source static`, same image, PSP signal 45-47%. Ping at the start of
the stream: 6.3 ms (select) / 5.8 ms (polling).

| q | H.264: KB | JPEG: KB | H.264: FPS | JPEG: FPS | H.264: latency / p95 | JPEG: latency / p95 | H.264: decode | JPEG: decode | H.264: resent | JPEG: resent |
|---|---|---|---|---|---|---|---|---|---|---|
| 30 | 1.7 | 6.8 | 27.1 | 22.5 | 26 / 103 ms | 40 / 65 ms | 4.1 ms | 7.3 ms | 0.6% | 1.6% |
| 50 | 2.4 | 9.3 | **28.7** | 19.5 | **28 / 77 ms** | 48 / 117 ms | 4.0 ms | 7.4 ms | 0.3% | 2.3% |
| 70 | 3.8 | 12.5 | **28.6** | 12.8 | **27 / 55 ms** | 62 / 116 ms | 4.1 ms | 7.4 ms | 0.8% | 3.4% |
| 90 | 5.4 | 23.5 | **22.1** | 7.0 | **33 / 61 ms** | 124 / 236 ms | 4.2 ms | 7.9 ms | 1.9% | 14.8% |

- At the same quality (q on the JPEG scale), H.264 sent **23-30% of the
  bytes** on this image, less than the 33-45% of the 12-frame calibration.
  Result: **+47% FPS at q50, 2.2x at q70, 3.2x at q90**, and 41-74% lower
  latency. The decode in the stream (4.0-4.2 ms) matches test v3.
- With 2-4 KB frames, **the fixed cost per frame became the bottleneck**:
  the FPS stays at ~28.7 from q30 to q70. The network takes ~38-40 ms per
  frame, of which the "1st chunk" is 22-35 ms and the burst only 4-8 ms.
  The server reacts in 0.1-0.2 ms ("wait for a new frame"), and the ping at
  the start is 6 ms. That leaves ~16-29 ms per frame between the request
  leaving the PSP and the 1st chunk arriving, still unexplained.
- With the radio idle ~80% of the time, asking for the next frame before
  the current one arrives (`early_kb`) should hide that fixed cost. With
  big JPEGs, it got worse because the radio was busy; with H.264, the
  situation is different.
- The q30 "burst throughput" (62 KB/s) does not count: with 2 chunks per
  frame, the math has a single chunk.

#### v0.5: real screen and early request [PSP]

**Portal (Fedora screen), `--codec h264`, adaptive quality:** at q90 (the
maximum), 2.2-3.1 KB per frame, 16-27 fps (average ~21), average latency
22-41 ms (p95 30-118 ms), source at ~38 fps. With JPEG, the same adaptive
mode stayed near q55 with ~46 ms. Now the maximum quality comes out with
lower latency than the average before.

**`early_kb=8` (static, H.264):** q30 32.3 fps, q50 31.8, q70 25.8, q90
31.8. Without it: 27.1 / 28.7 / 28.6 / 22.1. A 10-44% gain (except q70,
within the ~20% variation between runs) and 0-8 ms higher latency. If the
fixed cost per frame were only waiting, the early request would double the
FPS. It did not.

**The fixed cost, by elimination:** the server reacts in 0.1-0.2 ms. The
ping with the network idle takes 6 ms. But request -> 1st chunk takes
15-35 ms during the stream (and 30-54 ms with the portal, which includes
~12 ms waiting for a new frame). What differs between the two: the ping
goes in a burst (16 in a row), and the stream is request/response with
20-30 ms pauses. That is the pattern where a Wi-Fi card's power save gets
in the way: it dozes during the pauses and the router holds the next
request until it wakes up. The server detects the PC power save on.
Continuous streams (Moonlight, for example) have no pauses and do not feel
it. **Deciding test:** `sudo iw dev wlp0s20f3 set power_save off` (reverts
on reboot) and the same bench.

#### H.264, the 12:37 bench [PSP]

No note on power save or `early_kb` (to be confirmed), signal 55%:

| q | KB | FPS | latency / p95 | network | 1st chunk | burst | resent |
|---|---|---|---|---|---|---|---|
| 30 | 1.7 | **44.8** | **20 / 34 ms** | 26.0 ms | 23.7 ms | 2.4 ms | 0.4% |
| 50 | 2.4 | **40.8** | **22 / 36 ms** | 25.6 ms | 21.3 ms | 4.4 ms | 1.1% |
| 70 | 3.8 | 21.1 | 55 / 154 ms | 51.2 ms | 32.3 ms | 19.1 ms | 6.3% |
| 90 | 5.4 | 6.9 | 558 / 957 ms | 148.7 ms | 55.6 ms | 93.2 ms | 15.2% |

- q30/q50: the best result so far, 41-45 fps and ~21 ms average latency
  (before 27-32 fps).
- q70/q90: the link got worse in the middle of the run (burst throughput of
  205 and 56 KB/s, 6-15% resent, the Wi-Fi dropped to 46 KB/s). With
  3.8-5.4 KB per frame, the previous runs did 22-32 fps; it is not an effect
  of the quality.
- v0.6 sends a ping per second during the stream ("ping in the stream"
  column in the bench), to tell the radio apart from answers with a frame.

#### H.264, PC power save off, `early_kb=0`, v0.6 [PSP]

Signal 72%. Ping at the start not recorded.

| q | FPS | latency / p95 | network | 1st chunk | ping in the stream (min) | burst | resent |
|---|---|---|---|---|---|---|---|
| 30 | 32.1 | 23 / 50 ms | 33.2 | 32.2 | 11.2 (3.5) | 7.6 | 1.6% |
| 50 | **42.8** | **19 / 34 ms** | 21.8 | 18.4 | 8.4 (3.5) | 3.5 | 1.1% |
| 70 | 23.9 | 31 / 68 ms | 39.4 | 30.7 | 13.3 (3.8) | 8.8 | 2.0% |
| 90 | 28.6 | 29 / 45 ms | 34.8 | 23.3 | 9.7 (3.5) | 11.6 | 1.2% |

- With power save on (12:22): 27.1 / 28.7 / 28.6 / 22.1 fps. It improved at
  q50 and q90, tied at q30 and q70: a smaller effect than I expected,
  within the variation between runs except at q50.
- **The ping during the stream measures 8-13 ms (minimum 3.5 ms), and the
  1st chunk 18-32 ms.** The network path is the same (PSP -> router -> PC
  -> server -> back). That leaves ~10-20 ms per frame that only show up
  when the answer is a frame: it is not the radio round trip in general.
- Suspects: (1) the PSP starts decode and flip right after asking for the
  next frame (prefetch), and that would delay the reception; (2) the
  1452-byte packet against the ping's 8, if the router sends to the PSP at
  a low rate. Test of (1): `prefetch=0`. The bench now shows the minimum
  and the median of the 1st chunk.

#### prefetch=1 x prefetch=0: the fixed cost was almost all 200 ms stalls [PSP]

H.264, PC power save off, `early_kb=0`, v0.6:

| q | FPS (pre1 / pre0) | 1st chunk pre1: average (min, median) | 1st chunk pre0: average (min, median) | ping in the stream (pre1 / pre0) |
|---|---|---|---|---|
| 30 | 29.0 / 24.2 | 41.6 (5.2, **8.7**) | 37.5 (5.3, **8.1**) | 7.4 / 8.1 |
| 50 | 42.3 / 24.5 | 23.0 (5.1, **7.5**) | 33.0 (5.2, **9.1**) | 10.8 / 9.4 |
| 70 | 37.2 / 25.6 | 20.9 (5.0, **7.2**) | 28.0 (5.0, **9.4**) | 9.2 / 11.4 |
| 90 | 29.6 / 25.8 | 25.5 (5.0, **7.7**) | 23.5 (5.3, **8.9**) | 7.0 / 13.3 |

- **The typical frame arrives in ~8 ms** (median), the same as the ping.
  The 20-40 ms average comes from a few very late frames. Prefetch does not
  change the median: decode does not get in the way of reception (suspect 1
  ruled out). Prefetch is worth 1.2-1.7x the FPS.
- Average - median = 13-33 ms. With the PSP waiting a **fixed 200 ms** to
  repeat an unanswered request, that is ~1 frame in 6-15 that lost the
  request or the whole answer. q30 is the worst: 2-chunk frames vanish
  whole in an interference burst, and the NACK solves partial losses
  quickly.
- **v0.7:** the request is repeated after a measured round trip (mean + 4
  deviations of the 1st chunk, 30-200 ms), and the overlay counts the
  repeats ("rep"). Simulation (`fake_client --loss 0.02 --loss-up 0.05
  --rtt-ms 8`, H.264 q30) [SIM]: **63-70 fps** against 40-47 fps with the
  200 ms.

#### v0.7 on the PSP-3000: the FPS doubled [PSP]

H.264, `--source static`, prefetch=1, `early_kb=0`, PC power save off,
signal 72%:

| q | KB | FPS | latency / p95 | network | 1st chunk: average (min, median) | ping in the stream | burst | resent |
|---|---|---|---|---|---|---|---|---|
| 30 | 1.7 | **64.2** | 19 / 37 ms | 14.3 | 12.7 (5.1, 6.7) | 11.4 | 7.0 | 0.5% |
| 50 | 2.4 | **60.6** | 20 / 39 ms | 15.7 | 12.0 (1.8, 7.2) | 9.7 | 3.8 | 1.5% |
| 70 | 3.8 | **53.4** | 23 / 40 ms | 18.7 | 11.6 (1.8, 6.9) | 10.4 | 7.2 | 1.4% |
| 90 | 5.4 | **43.3** | 28 / 55 ms | 22.7 | 12.9 (1.8, 6.9) | 24.1 | 9.9 | 1.1% |

- The 1st chunk average dropped from 21-42 ms to 12-13 ms (median ~7 ms):
  the 200 ms stalls are gone.
- q30-q50 hits the limit of the PSP screen (60 Hz). At q90, the network is
  the limit: 5.4 KB per frame.

**Evolution on the same image (`--source static`):**

| version | q50: FPS / latency | q90: FPS / latency |
|---|---|---|
| v0.3 (JPEG, TCP pull) | 8.5 / 203 ms | 3.0 / 265 ms |
| v0.3 (JPEG, UDP) | 19.7 / 46 ms | 10.9 / 90 ms |
| v0.5 (H.264 intra + Stop) | 28.7 / 28 ms | 22.1 / 33 ms |
| v0.6 (+ PC power save off) | 42.8 / 19 ms | 28.6 / 29 ms |
| v0.7 (adaptive request repeat) | 60.6 / 20 ms | 43.3 / 28 ms |
| **v0.8 (automatic early request)** | **69.5 / 21 ms** | **61.3 / 26 ms** |

Above 60 fps, the PSP receives more frames than the 60 Hz screen shows,
and the surplus is dropped before decode. Even so, there is always a new
frame ready at screen swap time.

#### v0.7 with the real screen: desktop and Minecraft [PSP]

`python3 server/pspstream.py --codec h264` (portal, adaptive with a 20 fps
target, which stayed at q90), `early_kb=0`, PSP signal swinging between 47%
and 92%. Log lines every 2 s:

| stretch | KB/frame | FPS (source) | latency / p95 | 1st chunk | burst (throughput) | wait for a new frame | resent |
|---|---|---|---|---|---|---|---|
| desktop | 2.0-2.6 | 24-30 (36-40) | 27-34 / 48-81 ms | 24-35 ms | 4-7 ms | 8-10 ms | 0-3% |
| Minecraft | 6-10.7 | 18-36, typically ~28 (37-42) | 38-72 / 57-170 ms | 9-33, typically ~13 ms | 13-45 ms (243-485 KB/s) | 0.3-4.6 ms | 0-16.5% |
| signal drop (50%) | 2.6 | 9 | 102 / 200 ms | 54 ms | 56 ms (29 KB/s) | 1.3 ms | 16.7% |

- **Minecraft:** the network is the bottleneck. Each frame spends ~12 ms
  waiting for the 1st chunk and 15-30 ms transferring. The dead time
  between one frame and the request for the next is ~1/3 of the cycle.
- **Desktop:** small frames. The server waits ~10 ms for a new frame from
  the capture, and the FPS (24-30) stays below the capture's (36-40).
- Open question: on the desktop, "1st chunk" minus "wait" gives ~20 ms. In
  the game it gives ~10 ms and in the static bench ~7 ms (median). I still
  do not know where the extra ~10 ms come from.

#### v0.8: automatic early request [SIM]

The early request comes back, now computed: the PSP asks for the next frame
when what is left of the current one takes a round trip to arrive. In
bytes, that is the initial ping / average gap between chunks x 1400 bytes.
On the PSP-3000, ~5 ms / ~3 ms x 1400 ≈ 2-3 KB, capped at 8 KB. The old
tests (fixed 6-14 KB, JPEG) asked 2-5x too early: the next frame went whole
into the router queue, and latency rose with no FPS gain. Two rules avoid
waste:

- **A frame with a gap does not ask early.** The next one would get in the
  queue in front of the resend, and the frame with the loss would be
  dropped.
- **A frame with a gap and a newer one already arriving:** drops the frame
  instead of asking for a resend. The resend would arrive after the newer
  one and be thrown away. The 1st simulation, without these rules, lost 12%
  of the frames and resent ~80 chunks for nothing every 12 s.

Simulation: `tools/fake_client.py` with 400 KB/s, 5 ms round trip, 2% loss
and 4 ms decode. Local H.264 server, capture at 38 fps like the portal. The
FPS is the fake PSP's, and the latency and dead time come from the server
log.

| scenario | `early_kb=0`: FPS / latency / dead time | `auto`: FPS / latency / dead time | asks early at |
|---|---|---|---|
| desktop (test source, 2.3 KB) | 37.5-37.8 / 27-30 ms / 26-28 ms | 37.0-37.7 / 26.4-26.8 ms / 29 ms | 2.5 KB |
| game (pinwheel q70, 10 KB) | 26.7-28.1 / 63-68 ms / 9.7-10 ms | **31.0-31.6** / 60-64 ms / 3.9-4.5 ms | 2.3 KB |
| static q30 (4.7 KB) | 49.7 / 24.2 ms / 9.8 ms | **66.2** / 24.2 ms / 3.8 ms | 2.6 KB |
| static q90 (8.4 KB) | 31.9 / 35.6 ms / 9.8 ms | **36.3** / 35.3 ms / 4.8 ms | 2.5 KB |
| static q90, 5% loss, 10 ms, 350 KB/s | 22.4 / 50 (p95 74) ms | **26.6** / 47 (p95 58) ms | 4.0 KB |

- The "dead time" measures from the last chunk of a frame to the 1st of the
  next. The floor is the gap of one chunk (~3.5 ms at 400 KB/s), so `auto`'s
  ~4 ms are the best possible.
- Latency did not go up, so no queue built up. On the desktop, both modes
  already keep up with the capture in the simulation. There the simulated
  round trip is clean (5 ms), and on the real PSP the desktop "1st chunk"
  was 24-35 ms.
- **This is a simulation.** The first version of the early request also
  won in the simulation (+62%) and gained nothing on the PSP. The bench and
  the game test on the PSP decide. `early_kb=0` in `server.txt` goes back
  to the v0.7 behavior.

#### v0.8 on the PSP-3000: bench and Minecraft [PSP]

**Bench** (H.264, `--source static`, `early_kb=auto`, signal 100%):

| q | KB | v0.7: FPS / latency / p95 | v0.8: FPS / latency / p95 | dead time between frames | asks early at |
|---|---|---|---|---|---|
| 30 | 1.7 | 64.2 / 19 / 37 ms | **71.2** / 21 / 36 ms | +6.7 ms | 3.2 KB |
| 50 | 2.4 | 60.6 / 20 / 39 ms | **69.5** / 21 / 38 ms | +5.2 ms | 2.5 KB |
| 70 | 3.8 | 53.4 / 23 / 40 ms | 55.7 / 31 / 70 ms | +4.5 ms | 2.5 KB |
| 90 | 5.4 | 43.3 / 28 / 55 ms | **61.3** / 26 / 36 ms | +4.0 ms | 2.5 KB |

- The computed limit stayed at 2.5-3.2 KB, as predicted. The dead time
  stayed at 4-7 ms, close to the floor (the gap of one chunk).
- At q90, +42% FPS, with lower latency and p95. The q70 phase had a higher
  ping in the stream (12 ms against 7-10 ms) and worse latency. Between
  runs, the Wi-Fi varies ~20%.

**Real screen** (`--source portal`, adaptive at q90, signal 70-100%):

| stretch | KB/frame | FPS (source) | latency / p95 | dead time | wait for a new frame |
|---|---|---|---|---|---|
| desktop | 2.1-4.2 | 29-37.5 (36-40) | 24-37 / 31-77 ms | +15-24 ms | 10-17 ms |
| Minecraft | 5-9 | 25-39.5, typically 35-37 (37-40) | 28-46 / 37-85 ms, typically 30-38 | +6-14 ms | 3-11 ms |
| interference | 11 | 9.5-25.5 | 58-115 / 84-239 ms | | 0.1-0.8 ms |

- **Minecraft:** from ~28 fps and 50-60 ms (v0.7) to 35-37 fps and
  30-38 ms. The FPS reached the source's, so the limit became the capture.
- **Desktop:** from 24-30 to 29-37.5 fps, also close to the source. The
  dead time here is the wait for a new frame.
- The ~38 fps "source" comes from the portal itself (below), not from the
  server.

#### Portal capture: videorate cut 60 fps down to ~38 [PC]

The pipeline had `videorate drop-only=true max-rate=60` to honor `--fps`.
The portal delivers a variable rate (`framerate=0/1`), and the frame
timestamps jitter by ±1 ms. In that situation, videorate (GStreamer 1.24)
drops ~1/3 of the frames:

| input (60 Hz, `framerate=0/1`) | videorate `max-rate=60` | videorate `max-rate=75` | new limiter |
|---|---|---|---|
| no jitter | 60.0 | - | 60.0 |
| jitter ±1 ms | **38.2** | 38.0 | 60.0 |
| jitter ±2 ms | 39.5 | 37.7 | 60.0 |
| live, full pipeline, ±1-2 ms | **37.3** | - | **60.0** |

videorate went away. `--fps` is now applied by a probe at the queue output
(`RateLimiter`): a 1/fps schedule with 25% tolerance. A 60 Hz source goes
through whole, and a 144 Hz one stays at ~60-70 fps. The server now logs
how much the source delivers, how much passes the limit and how much is
encoded.

**On the real portal, that was not the limit [PSP].** With the new log
(Fedora 44, GNOME, 2240x1400 screen at 59.998 Hz, Minecraft open), PipeWire
itself delivers 37.7-38.7 fps, and everything passes the limit:

```
capture format: video/x-raw, format=BGRA, width=2240, height=1400, framerate=0/1,
                max-framerate=7864015/131072 (59.998), ...
capture: the source delivers 37.7 fps (median gap 32.2 ms, p10 16.8, p90 33.8);
         passing the 60 fps limit: 37.7; encoded: 37.9
```

The gaps are 1 or 2 screen frames (16.7 or 33.3 ms), with ~40% of 1 frame.
Two explanations fit, and a test tells them apart:

1. **The game runs at ~38-40 fps.** An integrated GPU with Minecraft at
   2240x1400 easily gives that. GNOME only sends a frame when the screen
   changes, so the capture does not go above the game FPS.
2. **GNOME (mutter) limits the capture.** Its limiter uses a minimum gap
   equal to the screen period (16,667 µs). A frame that arrives a few µs
   early is skipped, and the next one comes 2 frames later. The other
   hypothesis is the copy of the whole screen (2240x1400, 12.5 MB) to
   regular memory, done by GNOME itself.

Test: look at the "source" with something that really changes at 60 fps
(the mouse circling non-stop on the desktop, or a 60 fps video) and the
Minecraft FPS on the F3 screen.

**Result:** Minecraft runs at 60 fps on the laptop, so explanation 1 falls
and the limit is GNOME's capture. The negotiated format has no
`memory:DMABuf`, so on every frame GNOME copies the whole screen (12.5 MB)
from the GPU to regular memory. `--dmabuf` (experimental) asks for the
screen as a DMA-BUF and downscales in OpenGL: only 480x272 (~0.5 MB)
reaches the CPU. If the limit is that copy, the source goes up to ~60 fps.
If it is GNOME's gap limiter, nothing changes. Tested here only without a
GPU (headless EGL, llvmpipe): the downscale produces 480x272 with the right
borders, and a pipeline without DMA-BUF falls back to the normal mode by
itself.

**`--dmabuf` on the user's PC [PSP]:** it worked, with the screen in
DMA-BUF (`drm-format=XR24:0x0100000000000002`, Intel tiled). But the source
stayed at 38.3-38.7 fps (median gap 32.4-33.1 ms, p10 16.0-16.6 ms), and
the "capture" went up from ~4 to 5-9 ms (the GPU syncs to return the
image). No gain: the copy was not the limit. It stays as an option, but it
is not recommended.

**The cause, in the mutter 50 code** (`meta-screen-cast-stream-src.c`,
checked in tags 46.0, 48.0, 50.0 and 51.0):

```c
min_interval_us = (G_USEC_PER_SEC * max_framerate.denom) / max_framerate.num;
if (time_since_last_frame_us < min_interval_us) {
    /* "Skipped recording frame on stream %u, too early" */
    meta_screen_cast_stream_src_queue_follow_up (src, flags);
    return;
}
```

- The negotiated `max-framerate` is the screen rate (7864015/131072 =
  59.998 Hz), so the minimum gap becomes 16,667 µs (integer division). The
  timestamp of each frame is the expected presentation time of the screen
  frame, which jitters a few µs around 16,667.3. Every frame that falls
  below 16,667 is skipped.
- In monitor capture, replacing a skipped frame asks for a redraw
  (`clutter_actor_queue_redraw_with_clip` of 1x1 pixel), which only comes
  out on the next screen frame: 33.3 ms after the last recorded frame.
- If a fraction p of the frames gets through, the average gap is
  16.7p + 33.3(1-p). With 38.3 fps, p = 0.43: ~43% of 16.7 ms gaps and the
  rest at 33 ms. That matches the 16.6 ms p10 and the 33 ms median.
- In **window** capture, the replacement is a 1/60 s timer that records
  without waiting for a redraw. That is why OBS reports that only full
  screen capture gets stuck
  ([mutter #4214](https://gitlab.gnome.org/GNOME/mutter/-/work_items/4214)).
- In mutter 50, the range announced for monitors goes from 1/1 up to the
  screen rate, and the client cannot negotiate another value. In mutter 51,
  the minimum went back to 0/1, and `max-framerate=0/1` turns the limiter
  off (`max_framerate.num > 0`).

**`--window` (the Minecraft window in full screen) [PSP]:**
`max-framerate=60/1`, and the source delivered 36.5, 38.8 and 41.3 fps in
the three reports (median gap 21-26 ms, p10 16.8, p90 32-43), with 2 s
windows reaching 45-47 fps. Almost the same as the 38 of the screen
capture: the timer replacement helps little, because the limiter still
skips ~half the frames. **On GNOME 50, the capture ceiling is ~40 fps**,
through any portal path.

PipeWire passes on the `max-framerate` asked for in the GStreamer caps
(`src/gst/gstpipewireformat.c`). So, on GNOME 51, asking for
`max-framerate=0/1` turns the limiter off. On GNOME 50, the announced range
goes from 1/1 up to the screen rate, and no value helps.

In the same run: light scenes (1.4-3.7 KB) gave 25-38 fps with 29-46 ms;
heavy scenes (9-12 KB), 25-37 fps with 53-75 ms (network 25-36 ms per
frame).

**Sunshine as a Flatpak** uses the same portal capture (Flatpak does not
allow KMS). In its code (`src/platform/linux/pipewire.cpp`): it asks for a
`max-framerate` from 0/1 to 1000/1 preferring the requested rate (60).
Since GNOME 50 offers at most 59.998, it turns off its own pacing and sends
each frame as it arrives, that is, ~40 fps on the same PC. It only repeats
the image after 1 s with nothing new.

**KMS capture (`--source kms`):** reads the main plane of the graphics
card, without the compositor, like Sunshine's KMS capture. The
`tools/kms/pspstream-kms` helper (with `CAP_SYS_ADMIN`) exports the
framebuffer as a DMA-BUF on each buffer swap, and the server downscales in
OpenGL.

**First KMS run [PSP]:** it worked. The source delivered **58.3 fps**
(median gap 16.5 ms, p10 16.2, p90 17.5), and the 2 s windows stayed at
58-60 fps, against ~38 through the portal. The screen buffer uses Intel
Gen12 compression with clear color (modifier `0x0100000000000008`, 3
planes), and the OpenGL import accepted it. When the game enters or leaves
full screen, the format switches between XR30 (10 bits) and XR24, and the
pipeline renegotiates without stalling. The "age" of the sent frame dropped
to 7-10 ms, and the wait for a new frame to ~0.1 ms.

The PSP stayed at 9-28 fps and 60-130 ms, because in that run the network
was the limit:
- 10-19 KB frames at q90;
- signal swinging between 47% and 85%, with up to 20% of chunks resent;
- ping in the stream from 17 to 346 ms.

The capture was of monitor 0 of 2 (1280x720 at 59.855 Hz). The log now
lists the other monitors and the `--kms-monitor` of each.

**Second KMS run [PSP]** (laptop screen at 1280x720, source 57.9 fps):

| stretch | KB/frame | FPS | latency / p95 | before, through the portal (~38 fps) |
|---|---|---|---|---|
| light scenes | 3.7-4.4 | **43-56** | **32-38 / 54-62 ms** | 25-38 fps, 29-46 ms |
| game, good Wi-Fi | 8-8.6 | **40-42** | 55-66 / 71-97 ms | 29-39 fps, 35-65 ms (6-11 KB) |
| game, signal 57-67% | 8-11 | 14-23 | 74-94 / 148-212 ms | |

- With the capture at 60 fps, the limit is now the Wi-Fi: 380-460 KB/s in
  the burst. During signal drops, 10-15% of the chunks are resent.
- In this run the initial ping gave 9.9 ms (and 28.7 ms with select), so
  the early request stayed at 4-5 KB, against 2-3 KB in the others. The dead
  time stayed at 3-5 ms, with no queue.
- The PC is also on Wi-Fi. The server now reads its band (`iw dev ...
  link`): on 2.4 GHz, the PC and the PSP share the same channel, and the
  hint is to use the router's 5 GHz or a cable.

**Minecraft in this run** (q90, signal 50-100%): 6-11 KB frames give 29-39
fps and 35-65 ms. In the 13-15.6 KB scenes, 24-28 fps and 70-84 ms (p95
88-133 ms): there the network is the limit, and the decode goes up to 6-7
ms.

#### P frames (v0.9, `--codec h264p`) [PC] [SIM] [EMU]

With the capture at 60 fps, what was left was the network: in game scenes,
12-18 KB frames at ~450 KB/s, even with the PC on 5 GHz. Every IDR frame
sends the whole image every time. With P frames, only what changed goes.

**Size [PC]** (openh264, QP 30, game frames with the camera panning):

| shift per frame | P / IDR |
|---|---|
| 2 px | 5% |
| 8 px | 13% |
| 24 px | 51% |
| still screen | whole packet ~100 bytes |

Encode on the PC: ~1.2 ms for the P and ~0.5 ms for the 2 copies (packet in
~1.5-1.8 ms).

**How it works** (details in [Protocol](Protocol)):

- The PSP decoder only releases frame N after N+2 (test v2 above), and
  `Stop` resets the references. So each packet carries the frame and 2
  copies (P with no change), and the PSP makes 3 calls: **10.6 ms of
  decode** on the PSP-3000 with openh264 (test v4 below), against 3.7 ms
  for IDR + Stop.
- The server only encodes the frame it will send. The PSP decodes all of
  them, in order, and skips the P frames without a reference until an IDR
  arrives (requested with the `IDR` flag).
- **Queue [EMU/SIM]:** the first version asked for the next frame as soon
  as one arrived, as with JPEG. Without dropping the old frame, small P
  frames arrive faster than the 12 ms of decode and the queue would grow
  (latency) until it overflowed the slots (IDR). Now the next one is
  requested when decode takes the last one in the queue: it arrives while
  the current one decodes.
- **Whole frame lost [SIM]:** a small P fits in one packet. If it
  vanishes, nothing arrives, and the next frame would come without the
  reference. The repeated request now carries a NACK of the expected frame,
  and the server resends the same one. In the simulation with 2-5% loss, no
  IDR was needed.
- **QP [PC]:** openh264enc ignores `qp-min/qp-max` with the pipeline
  running (measured: the size does not change). Changing the quality means
  rebuilding the encoder, which starts with an IDR. Adaptive quality comes
  in together with an IDR the PSP asked for, or at most every 3 s.

**Simulation [SIM]** (`tools/fake_client.py`: 450 KB/s, 6 ms round trip,
5 ms decode for intra and 12 ms for P; server with a game clip panning
8 px per frame, q90):

| | KB/frame | FPS | network | received -> shown |
|---|---|---|---|---|
| H.264 intra | 7.0 | 54.6 | 18.2 ms | 5.3 ms |
| **P frames** | **0.4** | **59.1** | 16.6 ms | 12.8 ms |
| intra, 2% loss | 7.0 | 47.9 (15 frames lost) | 20.2 ms | 5.3 ms |
| **P, 2% loss** | 0.5 | **50.8** (0 lost, 0 IDR) | 19.0 ms | 13.0 ms |

Pure panning is the best case for P (motion compensation gets everything
right). A real game has animation and scene changes: the P frames will be
bigger, and a scene change costs almost an IDR. With the whole image
changing, the P gets close to the IDR size and only the more expensive
decode is left.

**Emulator [EMU]:** 400 P frames in a row from the same clip, over UDP and
TCP, came out without any image defect. The timings do not count: with P
frames the PSP sits idle waiting for the network, and the emulated clock
runs ~30x real time (400 frames in ~1.5 s of real time show up as tens of
seconds in the overlay).

**First test on the PSP-3000 [PSP]: the PSP powered off.** No more details
yet. The decode path is the one from v2 (3 calls without Stop, each AU with
the AUD, from an aligned buffer), but the stream is different: openh264
writes `level_idc` 41 (v2's x264: 30), POC type 0 with 16 bits and a 15-bit
`frame_num` (x264: POC type 2, 4 bits). In intra mode that never mattered,
because each frame is IDR + Stop. Probe v4 (`psp/probe`) separates each
difference into a step and writes before each one, so a power-off points
to the step.

#### Test v4 on the PSP-3000: the format is not the problem [PSP]

60 frames per step (testsrc2 with the frame number; openh264 at q90).
Nothing powered off:

| step | calls ok | per frame shown | each copy | delay |
|---|---|---|---|---|
| x264 + 2 copies, level 3.0 (v2) | 180/180 | 12.11 ms | 4.02 ms | 0 |
| x264 + 2 copies, level 4.1 | 180/180 | 12.12 ms | 4.02 ms | 0 |
| openh264 IDR + Stop in VRAM (`--codec h264`) | 60/60 | 3.73 ms (Stop 0.62) | | 0 |
| openh264 P, 1 call, level 3.0 | 60/60 | 3.55 ms | | 2 |
| openh264 + 2 copies, level 3.0 | 180/180 | **10.63 ms** | 3.53 ms | 0 |
| openh264 + 2 copies, level 4.1 (the stream) | 180/180 | **10.63 ms** | 3.53 ms | 0 |

- The declared level changes nothing, and the openh264 stream with 2
  copies decodes correctly and without delay. openh264's copies (~20-80
  bytes) cost 3.5 ms, against 4.0 ms for x264's.
- So the cause is in what the probe did not have: time (60 frames against
  minutes), real content and losses, or the network running alongside.
- **Suspect (ruled out in v4.1):** without a periodic IDR, openh264's
  `frame_num` (15 bits) and POC (16 bits, +2 per AU) wrap around at AU
  32768. With 3 AUs per frame shown, at ~60 fps, that happens in ~3 min. A
  PSP video never gets there: each IDR resets both. The server started
  sending an IDR every 1800 frames (30 s at 60 fps; removed in v1.0), and
  v4.1 tests the wrap (step 7, 12000 frames) and recovering from a loss
  through an IDR without Stop (step 8).

#### Test v4.1 on the PSP-3000: an IDR in the middle of the P frames powers the PSP off [PSP]

The stream really powered the PSP off (on power-up, it started from
scratch), in 10-20 s, on battery.

| step | result |
|---|---|
| 7: openh264 + 2 copies, 12000 frames without IDR (counters wrap at AU 32768) | 36000/36000 calls ok, 10.60 ms per frame, delay 0 |
| 8: the same with 60 frames, skips frames 20-29 and delivers the IDR of frame 30 without Stop | **the PSP powered off** |

- The `frame_num`/POC wrap is not the problem.
- Step 8 mimics the stream after a loss: the P frames without a reference
  are skipped and the requested IDR goes in **without Stop**, with the
  decoder still holding 2 frames. No earlier test did that: in intra mode
  every IDR comes after a Stop, and in the P clips the only IDR was the
  first one. In the stream, an IDR in the middle shows up within seconds:
  a loss, a quality change (the encoder is rebuilt) or the periodic IDR.
  That matches the 10-20 s.
- **Fix (v0.9):** `sceMpegAvcDecodeStop` before every IDR that arrives with
  P frames inside the decoder. Stop + IDR is the intra mode (step 3), and
  IDR + P with copies is the start of every stream (steps 5-7): both halves
  were already measured. Probe v4.2 tests the combination: an IDR every 10
  frames with a Stop before it (step 9) and loss + Stop + IDR (step 10).

Other possible causes the probe does not cover:
- **Automatic standby:** the app did not call `scePowerTick`, so the PSP
  could suspend in the middle of the stream with no button pressed (it
  looks powered off; when turned on again, it goes back to where it was).
  It now calls it every 2 s.
- **Low battery:** 3 decodes per frame use more.

#### Test v4.2 and gameplay: P frames working [PSP]

Probe v4.2 tested the fix (Stop before every IDR that arrives with P frames
in the decoder): an IDR every 10 frames with a Stop before it, and a loss of
5 frames + Stop + IDR, the same scenario as step 8 that powered it off. Both
passed, and the stream with `--codec h264p` (EBOOT v0.9 with the fix) ran
in gameplay without problems. Left to measure on the PSP: FPS and latency
against `--codec h264` in the same scene, the `dec` on the overlay
(expected ~10.6 ms) and the `idr` and `rep` counters.

#### openh264 directly, without GStreamer (v1.0) [PC]

Through GStreamer (appsrc -> openh264enc -> appsink), each AU goes through
two queues and two threads. With libopenh264 called directly (ctypes), the
same encode, a still image with the real server and a request every ~5 ms:

| | request -> 1st packet, median | p95 |
|---|---|---|
| JPEG or H.264 already done (reference: round trip on localhost) | 0.43-0.48 ms | 0.66-0.72 ms |
| P frames through GStreamer | 2.83 ms | 4.17 ms |
| **P frames, openh264 directly** | **1.78 ms** | **2.67 ms** |

Per AU, with the image moving: frame 0.85 ms and copy 0.30 ms with the
encoder "warm"; with 16 ms idle between packets (60 fps), 1.7 and 0.5 ms
(the CPU drops out of its rhythm). Turning off background or scene change
detection, or lowering the complexity, changed less than the measurement
noise, so the parameters stayed the same as openh264enc's: the stream comes
out identical, byte for byte (test `test_direct_matches_gstreamer`).

The bigger gain is another: with rate control off, each frame's QP comes
from `iDLayerQp`, which `SetOption(ENCODER_OPTION_SVC_ENCODE_PARAM_EXT)`
updates without a reset (`WelsEncoderParamAdjust`, the no-reset branch,
checked in the 2.6 code). Adaptive quality changes without an IDR; through
GStreamer, each change rebuilt the encoder (IDR) and waited up to 3 s.

#### Hitches with P frames: loss, not decode (v1.0) [PSP report + SIM]

**PSP-3000 report** (Minecraft and Hollow Knight, KMS capture): good
results in both. In Hollow Knight, `h264p` stays at almost 60 fps with
70-150 KB/s, but now and then it hitches a lot and the FPS drops; `h264`
(full frames only) is more stable, with a slightly lower FPS and 400-450
KB/s.

**Decode is not the limit.** The 3 calls of the P packet cost 10.6 ms on
the PSP-3000 (test v4), and the cost is fixed per call: a copy with nothing
to decode costs 3.5 ms, the same as a frame with content (1 call: 3.55 ms).
That fits in the 16.7 ms of a frame at 60 fps, and the next frame is
requested when decode takes the current one, so network and decode run in
parallel: with the capture at 60 fps, the next frame arrives ~16.7 ms
later, and the decode is already done. The only possible cut would be
sending fewer copies, and each one less is one frame (16.7 ms) more
latency. The overlay `dec` shows the real time; only above ~14 ms would it
start to matter.

**What differs between the two modes is loss.** In `h264`, each frame is
independent: a lost chunk only spoils that frame, and the next one is
already on its way (early request). In `h264p`, each P needs the previous
one, so a loss stops the stream until the resend:

| what is lost | v0.9 | wait |
|---|---|---|
| a middle chunk | the last one arrives with a gap: NACK right away | ~1 round trip |
| the last chunk (or the whole frame, which is usually 1 chunk) | only noticed by the silence | 20-50 ms + 1 round trip |
| the request, on the way up | nothing arrives | RTO (>= 30 ms) + the answer |

With 1-2% loss and 60 frames per second, that is a 2-4 frame stall every
second or two.

**Simulation** (`tools/fake_client.py`, 450 KB/s, 6 ms round trip, 10.6 ms
decode for P and 3.7 ms for intra; a game clip panning with noise, P of
~1.5 KB and a scene change every 2.4 s; 16 s per run, 2 runs). Hitch = 50
ms or more between two shown frames.

| scenario | hitches v0.9 | hitches v1.0 | gap p99 v0.9 / v1.0 | FPS v0.9 / v1.0 | `h264`: hitches, FPS, KB/s |
|---|---|---|---|---|---|
| no loss | 0-1 | 0 | 38-39 / 36-37 ms | 59.6 / 59.5 | 0, 48.6, 389 |
| 1% down and up | 9-12 | 0-1 | 49-51 / 38-39 ms | 57.1 / 59.0 | 10, 44.7, 359 |
| 2% down, 0.5% up | 4-11 | 2-3 | 43-53 / 41-42 ms | 57.0 / 58.1 | 4, 43.8, 351 |
| 6 ms bursts that take everything (0.5%) | 10-12 | 5-8 | 51-60 / 40-46 ms | 57.9 / 57.9 | 5, 45.9, 368 |

The same pattern as the report: without loss `h264p` stays at 60 fps; with
loss, it hitches more than `h264`, which has a lower FPS and 2-3x the
bandwidth.

**What went into v1.0:**

- **Last chunk twice:** the server sends the last chunk of each P packet
  again 6 ms later (`--p-redundancy-ms`). Cost measured on the clip: ~0.7
  KB per frame, ~40 KB/s at 60 fps (+27-53% over the P frames).
- **Request twice:** the PSP sends the new frame request with the frame
  number (FRAME + NACK) and repeats it after 6 ms if no chunk arrived. The
  server recognizes the copy (frame not sent yet: merges it with the
  pending request; sent less than 15 ms ago: ignores it). A lost request
  costs 6 ms instead of >= 30.
- 3-4 ms delays fell inside the 6 ms bursts (no gain in them); 9 ms made
  the isolated loss worse. 6 ms stayed for both.
- The PSP no longer repeats a request it has not even made yet (with the
  next one deferred to the decode thread, the "unanswered" request was
  false), and while it waits for the decode thread to ask, the network
  thread checks every 1 ms (before, it could sleep up to 100 ms without
  knowing about the request).
- **No periodic IDR:** the `frame_num`/POC wrap passed on the PSP (test
  v4.1, step 7), and the IDR every 30 s cost ~10 KB and a small stall.
- The server line shows the hitches with the likely cause (loss, IDR, late
  request, capture), so the next PSP test says what is left.

A mistake of mine in the simulation: `fake_client` counted the request made
by the "decode" after reading the packet that answered it, was left with a
phantom request and waited for the RTO. That made `h264p` hitch even
without loss; the PSP does not have that mistake (the network thread counts
the request before reading the packet), and the simulator was fixed before
the measurements above.

**Not yet measured on the PSP:** everything above is simulation, and the
emulator only checks the logic (601 P frames over UDP and 301 over TCP
without errors; its clock skips idle time). What is left on the real PSP
(long bursts, IDR after losses in a row) shows up in the server's hitch
line.

#### P frames without prefetch (v1.0) [PSP report + SIM]

**PSP-3000 report** (Hollow Knight, KMS, EBOOT and server with the changes
above): it got excellent, and for that **prefetch has to be off**. With
`prefetch=0`, the FPS stays close to 60, sometimes locked at 60. Latency not
written down.

With prefetch, the next frame is requested when decode takes the current
one and arrives while it decodes: network and decode go together, and the
FPS stays close to 60. But each frame depends on the round trip at that
instant, so a Wi-Fi swing becomes a frame that stays 2 or 3 vblanks on
screen among others that stay 1. Without prefetch, the next one is only
requested after showing the current one: one frame at a time, with nothing
arriving during decode and no queue, and the pace depends only on the
request -> frame -> decode cycle, which on the PSP-3000 fit in ~16.7 ms.

(**v1.1:** that ~60 fps "no prefetch" was, by chance, the request made when
decode starts, because of a stale signal in a semaphore; without it,
`prefetch=0` gives ~45 fps. See the section "45 or 60 fps depending on the
history".)

**The simulation was wrong here** [SIM, did not match the PSP]: it
predicted ~30 fps without prefetch. In the model, the cycle (6 ms fixed
round trip + wait for the clip capture + encode + transfer at 450 KB/s +
10.6 ms of decode) went over 16.7 ms, and the stream stayed at one frame
for every 2 captured. On the real PSP (PC on 5 GHz) it fit in one frame.
The table stays as a record; for this decision the PSP counts.

Simulation (same scenario as the table above, 16 s, `fake_client --prefetch`):

| | FPS | gap p99 between frames | from frame ready on the PC to the screen (without the capture) |
|---|---|---|---|
| prefetch, no loss | 58-60 | 37-38 ms | ~29 ms |
| no prefetch, no loss | ~30, steady | 47 ms (almost all 33 ms) | ~41 ms |
| prefetch, 1% loss | 58.7 | 38.5 ms | |
| no prefetch, 1% loss | 29.7 | 47.9 ms | |

In the emulator, both modes decode correctly (601 P frames over UDP, 301
over TCP; its clock skips idle time, so the timings do not count).

**v1.0: `prefetch=auto` is the default.** No prefetch with P frames, with
prefetch for JPEG and H.264 with full frames only, where it yields 1.2-1.7x
the FPS (measured above) and each frame is independent. `prefetch=1` turns
it back on with P frames (useful if the Wi-Fi is slow enough that the cycle
does not fit in a frame); SELECT + START + cross flips it during the
stream, and the overlay says when the next one is requested.

#### Prefetch with P frames: 45 or 60 fps depending on the history (v1.1) [PSP report + SIM]

**Report:** in Hollow Knight, with `prefetch=0`, close to 45 fps; turning
prefetch on and off (SELECT + START + cross), it goes to 60 fps and stays.

**Two bugs, found through that:**

- **`prefetch=0` depended on a stale signal.** Without prefetch, the
  network thread waited for "decode finished" on a binary semaphore. A
  signal was left over when two frames were published at once (a complete
  frame that waited for the resend of an older one), or when prefetch was
  turned on (which signals to unblock the wait). With the signal left over,
  each request went out one frame earlier: when decode took the current
  frame, not after showing it. That was the ~60 smooth fps mode; without
  the signal, after showing, ~45 fps. The "smooth and close to 60 with
  prefetch off" reports (v1.0) were that mode, by chance.
- **With prefetch, a phantom request.** The decode thread released the
  "deferred request" (`ask_deferred = 0`) before counting the request it
  made (`dec_asks++`). The network thread, which since v1.0 checks every
  1 ms during that wait, could see "nobody asked" in between and ask for
  the same frame again. The server merged the two into a single frame, the
  PSP was left counting one request too many, the next frame was not
  requested, and the stream waited for the RTO (>= 30 ms). Before v1.0, the
  false repeated request did the same. That was the prefetch hitch with P
  frames.

In the simulation, after the fixes (16 s, same scenario as the tables
above):

| | no loss: FPS / hitches / repeated | 1% loss: FPS / hitches / repeated |
|---|---|---|
| before: `auto` with the phantom request | 50-51 / 3-4 / 15-39 | 53.8 / 2 / 17 |
| `auto`: asks when decode starts | 55.6 / 0 / 0 | 53.7 / 1 / 0 |
| `1`: and also before the end of the frame | 59.5 / 1 / 0 | 58.9 / 0 / 2 |
| `0`: after showing | 30.8 / 0 / 0 | 30.4 / 1 / 0 |

The simulator is pessimistic about the network (fixed 6 ms round trip and
450 KB/s): in it `0` gives 30 fps, and on the PSP it gave 45. **On the PSP,
the ~60 smooth fps mode was "asks when decode starts"**, and it becomes
`auto` (the default) on purpose: no semaphore, no early request in the
middle of a frame. `1`, now without the phantom request, did better in the
simulation; it is left to measure on the PSP (SELECT + START + cross cycles
auto, yes and no).

#### Audio (v1.1) [PC + EMU]

**Format (first version):** WAV IMA ADPCM (GStreamer's `adpcmenc`), 32 kHz
stereo, blocks of 641 samples per channel (20.03 ms) = 648 bytes + 20 of
header + 28 of UDP/IP: **~34 KB/s and 50 packets/s** on the air. In mono or
at 22.05 kHz, half of that or less. (Later: 44.1 kHz, below.)

**Fidelity [PC]:** on a test signal (440 + 3000 Hz sines on one channel,
220 Hz on the other), 34 dB SNR against the original. The PSP decoder
(`psp/src/ima.c`) is the exact inverse of the encoder: redoing the encode
in Python with the decoder state, 0 of 30720 nibbles differ. GStreamer's
own `adpcmdec` uses the formula with multiplication and differs by up to
36 (of 32767) from the encoder's reconstruction; the same SNR. `ima.c`
compiled on the PC gives the same samples as the Python reference (test
`test_psp_decoder_matches_reference`, stereo and mono).

**CPU on the PC [PC]:** test capture + `audioresample` + `adpcmenc` +
packet + `sendto`: ~2% of a core (10 s, 49.8 packets/s, 32.5 KB/s of data).

**Emulator [EMU]:** the `sceAudioSRC` channel opens at 32 kHz, the packets
arrive whole (0 lost), the video goes on (401 P frames without errors), and
the shortcut turns the audio off and on (the server stops and resumes
sending; `tools/emu_audio_test.py`). The buffer runs dry in the emulator
("empty" 13, target at 120 ms) because the emulated clock runs faster than
real time when the PSP is idle: it does not count as a measurement.

**Expected delay (not measured on the PSP):** PipeWire read (10 ms chunks)
+ 20 ms block + network (~5-10 ms) + PSP buffer (starts at 40 ms, 30-120
ms) + output (8 ms chunks): ~80-100 ms at the start, less when the buffer
goes down to 30 ms. The video comes out in ~30-45 ms, so the audio should
arrive a little after the image. To measure on the PSP: the overlay target
and "empty" after a few minutes of play, and whether the video FPS changes
with audio on.

#### Audio on the PSP-3000: a "bee" buzz (v1.1) [PSP report + PC]

**Report:** the audio worked, but with a buzz that sounds like a bee.

**Cause (in the code):** the audio thread had a single output buffer. On
the PSP, `sceAudioSRCOutputBlocking` returns when the chunk enters the
queue, and the hardware reads the buffer (DMA) while it plays; the thread
was already writing the next chunk over it. The end of each 256-sample
chunk (8 ms at 32 kHz) came out spoiled: a periodic defect at ~125 Hz, the
frequency of a buzz. PPSSPP does not show it: it copies the samples at call
time. pspsdk's `pspaudiolib` uses two buffers for the same reason.
**Fix:** two alternating buffers, and each chunk goes from the cache to RAM
(`sceKernelDcacheWritebackRange`) before going to the DMA. To be confirmed
on the PSP.

**Rate and encoder [PC]:** 10 s of two game songs (`triTenkemusikk.wav`
and `Monstertruck_intro`, from opentri), SNR against the original at the
same rate, decoded by the PSP's `ima.c` compiled on the PC (1024-byte
blocks to compare with ffmpeg, which only accepts powers of 2):

| | adpcmenc (the server's) | ffmpeg, trellis 8 | ffmpeg, trellis 16 |
|---|---|---|---|
| tri, 32 kHz | 32.0 dB | 34.1 | 34.8 |
| tri, 44.1 kHz | 32.3 | 33.8 | 35.6 |
| Monstertruck, 32 kHz | 27.8 | 30.1 | 30.7 |
| Monstertruck, 44.1 kHz | 29.6 | 32.2 | 33.0 |

- **44.1 kHz becomes the default:** it is the PSP hardware rate, so
  `sceAudioSRC` does not resample (the conversion stays in the PC's
  `audioresample`), the highs go up to 22 kHz, and the fidelity goes up
  0.3-1.8 dB. It costs ~12 KB/s (~46 KB/s in total).
- An encoder with trellis (searches the best nibble sequence instead of the
  closest one at each sample) would gain 2-3 dB more, with the same decoder
  on the PSP. Left for later: `adpcmenc` has no trellis, and ffmpeg's needs
  power-of-2 blocks and a separate process.

#### Audio that did not come back after the settings screen (v1.1) [PSP report + EMU]

**Report:** after changing any option in an already open session and
applying it, the audio gave an error and only came back by restarting the
app.

**Cause:** the `sceAudioSRC` channel is only released
(`sceAudioSRCChRelease`) with the output queue empty; with audio in the
queue, the call fails. The return value was ignored, the channel stayed
reserved, and the next stream (after the settings screen, or turning the
audio off and on) could not reserve it: `sceAudioSRCChReserve` failed
(0x80268002). PPSSPP has the same rule, and `tools/emu_audio_test.py`,
which before only looked at the server log, now checks the PSP log and
reproduced the failure. **Fix:** before releasing, wait for the queue to
drain (`sceAudioOutput2GetRestSample`, < 30 ms) and try again for up to
~100 ms; when reserving, if it fails, release and try once more. In the
emulator: turning it off and on (SELECT + START + up) and coming back from
the settings screen reopen the channel, without any failure. To be
confirmed on the PSP.

#### P frames: 2-frame window and exact `--fps` (v1.1) [PSP report + SIM]

**Report** (P frames, `prefetch=auto`, Wi-Fi below 150 KB/s, that is,
bandwidth is not the limit): with `--fps 40`, the PSP rarely stays at 40,
it stays around 35 with drops; without a limit, 52-55 instead of 60, also
with drops now and then.

**Cause 1: the request raced against the capture.** With `auto`, the PSP
asked for N+1 when decode took N, and the server only sends a requested
frame. The request had to go, and the frame be encoded, before the next
capture (16.7 ms at 60 fps): round trip + encode + transfer took ~12 ms,
and any Wi-Fi swing delayed the frame or made the server skip a capture.
The simulator with a fixed round trip did not show it; with the round trip
swinging (4 ms + an exponential with a 3 ms mean, `--rtt-jitter-ms`), it
gave 53-55 fps, as on the PSP. **Fix: the window.** The request made at
decode allows up to N+2 (FRAME + NACK "up to frame F"; the server keeps the
credit, at most 2 ahead) and the frame leaves at capture time. A P frame
that is lost whole is noticed when the next one arrives (a gap in the
numbering), and the PSP asks for the resend right away, instead of an IDR.

**Cause 2: the `--fps` limit cut frames.** `RateLimiter` counted the next
frame's turn from the frame that arrived, with 25% tolerance. A late frame
pushed the turn of the following ones, and the next one, on time, was cut.
A 60 Hz source with each frame's timestamp jittering (normal with deviation
σ), 100 s:

| | old limit | fixed grid (v1.1) |
|---|---|---|
| `--fps 60`, σ 2 ms / 3 ms | 59.0 / 55.5 | 60.0 / 59.9 |
| `--fps 40`, σ 2 ms / 3 ms | 39.0 / 38.3 | 40.0 / 40.0 |
| `--fps 50`, σ 0-3 ms | 45.7-48.0 | 50.0 |
| 75 Hz source, `--fps 60` | 54.5-56.2 | 59.9-60.0 |
| 144 Hz source, `--fps 60`, σ 3 ms | 58.7 | 60.0 |
| `--fps 30` | 30.0 | 30.0 |

The grid counts each turn from the previous one; a frame passes if it
arrives up to half a period before its turn (half the source period, if
the source is faster), and the grid only restarts when it falls behind (a
stopped or slower source). The worst gap between frames also dropped (60 Hz
source, `--fps 60`, σ 3 ms: 43 → 38 ms).

**Simulation** (`fake_client`, game clip with motion, 450 KB/s, 10.6 ms
decode, 4 ms round trip + 3 ms swing, 12 s):

| `--fps` | without the window | 2-frame window |
|---|---|---|
| 60 | 53-55 fps | 59.4-60 fps |
| 60, 2% loss (3 runs) | 52.5-53.7 fps, 5 hitches (287 ms) | 59.0-59.7 fps, 4 hitches (328 ms) |
| 40 | 39.9 | 39.8-39.9 |
| 30 | 29.9 | 29.9 |

Latency does not change (network ~17-25 ms in both). Without the window,
the PSP also repeated each request after 6 ms (~60 more packets/s on the
way up); with it, it does not need to (the next request covers a lost
one).

**40 out of 60 Hz is not even.** The screen only has frames every 16.7 ms,
so `--fps 40` is 2 out of 3: gaps of 17 and 33 ms, always. Added to the
transfer time, which varies with each frame's size, some gaps on screen go
over 50 ms (in the simulation, a p99 of ~52 ms with and without the
window). For even motion: `--fps 30` (every other frame) or 60.

**On the PSP, to measure:** FPS with `--fps 60` and `--fps 40` (the overlay
and the server's `source` line, which shows the capture rate after the
limit).

## 1. Frame size [PC]

Same pipeline as the server (`videoscale` -> I420 -> `jpegenc`), 480x272
4:2:0 output. "FPS ceiling" = only the bandwidth limit (throughput / size),
without counting decode or losses. The real PSP throughput still has to be
measured; 300-500 KB/s is the expected range for 802.11b.

### PSP games (30 real frames from PPSSPP's `frametests`, already at 480x272)

LocoRoco, Monster Hunter 3rd, Final Fantasy Zero, Need for Speed Carbon,
GTA, Pursuit Force, Valkyria Chronicles, Project Diva and others. It is the
content closest to "a game on the PSP screen".

| quality | KB/frame (median) | min - max | FPS ceiling @ 300 KB/s | @ 400 KB/s | @ 500 KB/s |
|---|---|---|---|---|---|
| 30 | 11.1 | 3.9 - 18.2 | 26.6 | 35.5 | 44.3 |
| 40 | 13.1 | 4.2 - 20.9 | 22.5 | 29.9 | 37.4 |
| 50 | 15.1 | 4.4 - 23.5 | 19.6 | 26.1 | 32.6 |
| 60 | 17.3 | 4.8 - 26.4 | 17.0 | 22.7 | 28.4 |
| 70 | 20.8 | 5.3 - 30.6 | 14.2 | 18.9 | 23.7 |
| 80 | 25.6 | 6.1 - 38.5 | 11.5 | 15.4 | 19.2 |
| 90 | 38.7 | 8.5 - 55.7 | 7.6 | 10.2 | 12.7 |

### 1080p desktop downscaled to 480x272 (IDE with code + web page), bilinear2 filter

| quality | KB/frame (median) | min - max | FPS ceiling @ 300 KB/s | @ 400 KB/s | @ 500 KB/s |
|---|---|---|---|---|---|
| 30 | 8.0 | 5.8 - 10.3 | 36.7 | 48.9 | 61.1 |
| 40 | 9.2 | 6.6 - 11.8 | 32.0 | 42.6 | 53.3 |
| 50 | 10.2 | 7.3 - 13.1 | 28.9 | 38.5 | 48.2 |
| 60 | 11.4 | 8.2 - 14.7 | 25.8 | 34.5 | 43.1 |
| 70 | 13.3 | 9.6 - 17.1 | 22.2 | 29.5 | 36.9 |
| 80 | 16.1 | 11.6 - 20.6 | 18.3 | 24.4 | 30.5 |
| 90 | 22.4 | 16.2 - 28.5 | 13.2 | 17.6 | 22.0 |

### Downscaling filter: the choice that changed the result the most [PC]

1080p desktop -> 480x272:

| filter | KB/frame q50 | KB/frame q70 | text | extra cost on the PC |
|---|---|---|---|---|
| bilinear (2 taps, GStreamer's default) | 14.0 | 18.1 | jagged, broken letters | — |
| **bilinear2** (multi-tap, PSPStream's default) | **10.2** | **13.3** | smooth, readable | ~2 ms/frame |
| lanczos | 10.9 | 14.3 | smooth, a little sharper | ~4 ms/frame |

The 2-tap bilinear does not filter when downscaling 4x. The aliasing
becomes high-frequency noise, which JPEG compresses poorly. `bilinear2`
keeps text readable **and** produces 27% smaller frames. On the same
bandwidth, that means less network time per frame, that is, more FPS and
less latency.

## 2. Cost on the PC [PC]

| stage | cost |
|---|---|
| downscale 1080p BGRx -> 480x272 (bilinear / bilinear2 / lanczos) | ~1 / ~3 / ~5 ms |
| downscale 1440p -> 480x272 (bilinear / lanczos) | ~2.5 / ~7.6 ms |
| jpegenc 480x272 | ~1 ms |
| capture -> JPEG ready (live 720p60 test source, measured by the PTS) | ~9 ms |
| PipeWire 1080p60 BGRx -> JPEG (test node, same path as the portal) | 59 fps sustained |

The average age of the frame when it is sent stays at ~7-8 ms with capture
at 60 fps, which is half a capture interval.

## 3. Full pipeline, with simulated network and decode [SIM]

`fake_client.py --kbps 400 --decode-ms 11` mimics the PSP, with a network
thread receiving during decode. The 400 KB/s throughput is an assumption,
and the 11 ms of decode come from the time PPSSPP assigns to `sceJpeg`.

Adaptive quality (default), 1080p desktop, starting at q90:

| time | q | KB/frame | FPS | latency capture->shown |
|---|---|---|---|---|
| 0-2 s | 90 -> 63 | 20.0 | 18.5 | 65 ms |
| 2-4 s | 63 -> 51 | 13.9 | 24.6 | 49 ms |
| after | 51 (stable) | 13.2 | 28.2 | 47 ms |

Fixed quality benchmark (`--bench 30,60,90`, same scenario):

| q | KB/frame | FPS | average latency | network | decode |
|---|---|---|---|---|---|
| 30 | 10.3 | 36.2 | 38.8 ms | 27.4 ms | 11.0 ms |
| 60 | 14.7 | 25.5 | 50.6 ms | 39.2 ms | 11.0 ms |
| 90 | 28.5 | 13.2 | 87.1 ms | 75.8 ms | 11.0 ms |

Live test source (720p60), same model: ~47 ms, broken down into capture 9
+ age 7 + network 19 + PSP 11.

**Provisional conclusion** (before the PSP tests): with 802.11b, the
network dominates the latency. That is why the default is adaptive
quality, which picks the highest quality whose transfer fits in the time
budget per frame. The target became 20 fps after the PSP measurements
(section 0).

## 4. Emulator [EMU] (not representative)

`bench=1` on PPSSPPHeadless, 17 KB 480x272 frame:

| decoder | emulated time |
|---|---|
| sw (libjpeg-turbo) | 20.5 ms |
| hw (sceJpeg) | 10.85 ms |

The hw value is the delay PPSSPP itself assigns to `sceJpeg` (300 µs +
w·h/14 + w·h/110). The sw one is an estimate by instruction count, without
the real Allegrex's cache and memory stalls. **Do not use these numbers to
decide anything.** They only confirm that both paths work.

## 5. How to measure on the hardware [PSP]

The results are saved to files to paste here.

### 5.1 Decode: hardware x software

1. In the PSP's `server.txt`, add `bench=1`.
2. On the PC: `python3 server/pspstream.py --source static --fixed-quality -q 70`
3. The PSP shows `sw: X ms/frame` and `hw: Y ms/frame` before starting the
   stream.
4. Repeat with `-q 30` and `-q 90`: the decode time grows with the size.

### 5.2 FPS, KB/frame, Wi-Fi throughput and latency per quality

With a screenshot of your game or desktop:

```sh
python3 server/pspstream.py --source static --image my_screen.png --bench 30,50,70,90
```

Open PSPStream on the PSP and wait ~50 s. The table comes out on the
console and in `bench_YYYYMMDD_HHMMSS.md`. Run it twice, with the hw and
with the sw decoder (SELECT+START+square on the PSP switches the decoder).

### 5.3 Glass-to-glass latency (the only one that includes everything)

1. On the PC, open `tools/latency_clock.html` in the browser, in full
   screen (F11).
2. `python3 server/pspstream.py --source portal` and pick that monitor.
3. Film the monitor and the PSP together with a phone. Slow motion
   (120/240 fps) gives more precision.
4. Pause the video at several frames. In each, latency = monitor clock −
   clock on the PSP. Average about 10 readings. The precision is ±1 monitor
   frame (~17 ms at 60 Hz) per reading; the average reduces that.

That number includes everything: compositor, capture, encode, Wi-Fi,
decode, vsync and the PSP LCD. The difference from the "latency" in the
server log is the part the server does not see (compositor and LCD).

### 5.4 TCP x UDP

Run the same `--bench` twice, once with `transport=tcp` and once with
`transport=udp` in `server.txt` (or switch with SELECT+START+L and restart
the benchmark). The transport shows in the table header. Compare the
"Wi-Fi (KB/s)", "network (ms)" and "p95" columns. The "chunks resent"
column shows how much the Wi-Fi is losing. So the content does not vary
between runs, use `--source static --image capture.png`.

Open both ports in the firewall: `sudo firewall-cmd --add-port=5123/tcp --add-port=5123/udp`.

### 5.5 Tweaks to compare (SELECT + START + button shortcuts)

| shortcut | what | what to watch |
|---|---|---|
| square | decoder hw <-> sw | decode (ms) on the overlay |
| circle | vsync on/off | tearing x ~8 ms of average latency |
| cross | prefetch on/off | FPS (without prefetch: network + decode in series) |
| L | transport TCP <-> UDP (reconnects) | network, FPS, p95 |
| triangle | overlay | — |

It is also worth testing the XMB **"WLAN Power Save"** on and off.
PSPStream warns on screen when it is on.

### Table to fill in

| test | result |
|---|---|
| sw decode, q70 (ms/frame) | 34 |
| hw decode, q70 (ms/frame) | 7.91 |
| measured Wi-Fi throughput, TCP (KB/s, the bench's "Wi-Fi" column) | 235-356 |
| measured Wi-Fi throughput, UDP (KB/s) | 367-412 (link ~470 minus the overhead) |
| UDP, q50: FPS / latency / p95 | 21.8 / 42 / 63 ms |
| TCP, q50: FPS / latency / p95 | 8.5 / 203 / 805 ms |
| UDP + early_kb=10, q50: FPS / latency / p95 | 16.0 / 64 / 126 ms (worse than without: 19.7 / 46 / 75 ms) |
| FPS at q50 / q70 (bench) | |
| server latency (capture -> shown), adaptive q | |
| glass-to-glass latency (camera) | |
| WLAN power save on: latency | |
