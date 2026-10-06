The most used server options (`python3 server/pspstream.py` or
`pspstream`). All of them, each with its explanation: `--help`. Many can also
be changed in the [Web Interface](Web-Interface), with the PSP connected.

## Options

| option | what it does |
|---|---|
| `--source kms` | straight from the graphics card: 60 fps, no cursor (recommended on GNOME 50); `--kms-monitor 1` picks the second monitor |
| `--source portal` | Wayland portal (default); `--window` captures a window, `--forget` asks again what to capture |
| `--source test` / `static --image file.png` | animated pattern / still image (tests) |
| `--source x11` / `gst --gst-src "..."` | X11 session / your own GStreamer pipeline |
| `--source wolf` | what runs in a Wolf lobby, through its API; `--wolf-target`, `--wolf-pin`, `--wolf-video-convert` (see [Wolf](Wolf#8-settings-env)) |
| `--source screen`, `--monitor 1` | Windows: the monitor through Desktop Duplication (default there); `--monitor` picks which ([Windows](Windows)) |
| `--profile xbox` | controls: `game` (default), `desktop`, `arrows`, `xbox`, `xbox-camera`, `xbox-shoulders` ([Controls](Controls)) |
| `--codec auto` | `h264p` (default, if openh264 is there), `h264` (full frames only) or `jpeg` |
| `--h264-encoder auto` | libopenh264 directly, with GStreamer as a fallback (default); `gstreamer` forces the old path |
| `--fixed-quality -q 70` | fixed quality instead of adaptive |
| `--target-fps 20`, `--q-min 25 --q-max 90` | target and limits of the adaptive quality |
| `--fps 60` | maximum capture rate. From a 60 Hz screen, 30 is every other frame (even); 40 is 2 out of 3 (17 and 33 ms gaps: the average is 40, but motion is less even than at 30 or 60) |
| `--scale bilinear2` | downscaling filter (default; `lanczos` makes text a little sharper) |
| `--input-dry-run` | only show in the log what would be injected |
| `--dscp ef` | marks the packets for the Wi-Fi voice queue (WMM); `0` turns it off |
| `--p-redundancy-ms 6` | P frames: the last chunk of each frame goes again after this, and losing it does not stall the stream; `0` turns it off |
| `--no-audio` | no audio (the PSP also turns it off with `audio=0` or SELECT + START + up) |
| `--audio-device NAME` | audio source: `monitor` (default, what goes out to the speakers), `test` (440 Hz tone) or a source from `pactl list short sources` |
| `--audio-rate 44100`, `--audio-mono` | rate (22050, 32000, 44100 or 48000 Hz; 44100 is the PSP's) and mono: ~46 KB/s at 44.1 kHz stereo, ~34 at 32 kHz, half in mono |
| `--bench 30,50,70,90` | sweeps qualities with the PSP connected and writes a table |
| `--web 127.0.0.1:5124`, `--no-web` | address of the [Web Interface](Web-Interface) (`0.0.0.0:5124` = local network; password in `PSPSTREAM_WEB_PASSWORD`) or none |
| `--web-allow-host NAME` | another name accepted in the web interface address, besides the PC's IP and name |
| `--config FILE` | settings saved by the web interface (default `~/.config/pspstream/server.json`) |
| `--lang en` | language of the messages, `--help`, `--check` and the web interface: `en` (default) or `pt` ([Language](#language)) |
| `--check`, `--setup` | checks the dependencies and gives the command for your distribution; `--setup` also installs and configures, asking before each step |
| `-v` | detailed log |

## Language

Everything is in **English by default**, with **Portuguese** as an option.
Each part has its own switch:

| what | how to switch to Portuguese |
|---|---|
| server (log, `--help`, `--check`, errors) and web interface | `--lang pt`, or `PSPSTREAM_LANG=pt`, or **Language** in the web interface (applies right away and is saved in `server.json`) |
| PSP screens (settings, overlay, messages) | `lang=pt` in `server.txt`, or the **Language / Idioma** item on the settings screen (then "Save and connect") |
| Docker installer | `sudo bash install.sh --lang pt` (it also writes `PSPSTREAM_LANG=pt` to the `.env`) |
| Docker / Portainer | `PSPSTREAM_LANG=pt` in the `.env` or in the stack variables |

The order on the server: `--lang` on the command line wins over the setting
saved by the web interface, which wins over `PSPSTREAM_LANG`. Code comments
are in Portuguese.

## The stats line

Every 2 s, the server shows:

```
41.3 fps (source 59.8) | 1.2 KB/frame | Wi-Fi 52 KB/s | latency 29.1 ms (p95 41.0) ~ capture 1.0 + age 3.4 + network 11.8 + psp 12.9 | ...
```

"Latency" goes from the capture on the PC until the frame shows up on the
PSP, measured with the server clock only (see
[Protocol](Protocol#measuring-latency-without-syncing-clocks)).

When there were any, the line ends with the **hitches**: 50 ms or more
between two frames, with the likely cause:

```
... | hitches 3 (worst 74 ms: 2 loss, 1 late request)
```

| cause | what it is |
|---|---|
| `loss` | a chunk or frame was lost on the Wi-Fi and resent |
| `IDR` | the PSP lost the chain of P frames and asked for a full frame |
| `late request` | the PSP request took long to arrive: slow Wi-Fi at that moment, or the PSP busy |
| `capture` | the PC took long to have a new frame (the game or the capture stalled on the PC) |
