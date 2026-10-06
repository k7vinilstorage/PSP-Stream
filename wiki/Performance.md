PSP-3000 and Fedora 44 with 802.11b Wi-Fi. The details and history of each
number are in [Measurements](Measurements).

| | FPS | average latency | KB/frame |
|---|---|---|---|
| MJPEG (v0.4), still image q50 / q90 | 20 / 11 | 46 / 90 ms | 13-35 |
| H.264 full frames only (v0.8), still image q50 / q90 | 69 / 61 | 21 / 26 ms | 2.4 / 5.4 |
| H.264, Minecraft through the portal (source ~38 fps) | 35-37 | 30-38 ms | 5-9 |
| H.264 + KMS capture, light scenes / game | 43-56 / 40-42 | 32-38 / 55-66 ms | 4 / 8-9 |
| H.264 with P frames (v0.9), Hollow Knight + KMS (report) | almost 60, with a hitch now and then | not measured | 1.2-2.5 (70-150 KB/s, against 400-450 KB/s for H.264 full frames only) |
| H.264 with P frames (v1.0), Hollow Knight + KMS (report), request when decoding starts | **close to 60, smooth** ("excellent") | not measured | |
| the same, request after showing (real `prefetch=0`) | ~45 | not measured | |

- Decode on the PSP: JPEG 7.9 ms (hardware); H.264 full frames only 3.7 ms;
  P frame + 2 copies 10.6 ms.
- On the PC, the P packet leaves 1.8-2.7 ms after the request (openh264
  directly).
- In game scenes, the network is the limit: the PSP's 802.11b delivers
  380-460 KB/s in practice. That is what P frames are for.
- Decoding P frames (10.6 ms) fits in the 16.7 ms of a frame at 60 fps and
  runs in parallel with the arrival of the next one. The h264p hitches came
  from Wi-Fi losses (each P needs the previous one, so one loss stopped the
  stream until the resend); v1.0 sends the last chunk twice and repeats the
  request, and the server line shows what is left and why
  ([Server Options](Server-Options#the-stats-line)). With the next frame
  requested when decoding starts (`prefetch=auto`), the stream is smooth
  and close to 60 fps on the PSP-3000.
