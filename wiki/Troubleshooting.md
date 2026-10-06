First, `python3 server/pspstream.py --check` (or `pspstream --check`): it
lists what is missing and the command for your distribution. On Ubuntu, also
see the [Ubuntu Guide](Ubuntu-Guide#10-common-problems-on-ubuntu); with
Wolf, the table on the [Wolf](Wolf#10-troubleshooting) page.

The messages below are the English ones (the default). With `--lang pt`, the
server, the web interface and the PSP (`lang=pt`) show them in Portuguese.

| symptom | what to do |
|---|---|
| "No answer from the PC" / "Find" does not find it | is the server running? Open 5123/udp and 5123/tcp in the firewall (`--check` gives the command). PC and PSP on the same network, without client isolation on the router |
| something is missing or does not open | `--check`: lists what is missing and the command for your distribution |
| high latency, FPS swinging | turn off the PSP's WLAN Power Save; keep the PC on 5 GHz or on a cable (the server warns if it shares the 2.4 GHz channel with the PSP); router in b/g/n mixed mode |
| a hitch now and then (h264p) | check the prefetch (4th overlay line; the default `auto` says "asks up to 2 frames ahead when decoding starts"). Then look at the hitches and their cause in the [server line](Server-Options#the-stats-line). `loss`/`late request`: Wi-Fi (distance, crowded 2.4 GHz channel, microwave, Bluetooth); `capture`: the PC; frequent `IDR`: losses in a row. `--codec h264` copes better with losses (each frame stands alone), with 2-3x more bandwidth |
| smooth h264p, but FPS well below 60 | with `prefetch=0`, each frame waits for the previous one to be shown (~45 fps): use `auto`. Check the `source` in the server line: below 60, it is the capture (`--source kms`). Up to v1.1, `auto` stayed at 52-55 with the Wi-Fi swinging (the request arrived after the next capture) and the `--fps` limit cut frames from a source with jittery timestamps: update the server and the EBOOT |
| `--fps 40` does not give 40 | up to v1.1, the `--fps` limit cut frames when the capture timestamps jittered (~38) and the PSP skipped captures (~35): update. 40 out of a 60 Hz screen alternates 17 and 33 ms; for even motion, `--fps 30` |
| audio vanishes after the settings screen ("audio: audio channel error") | EBOOT 1.1 before the fix: the audio channel was not released with audio in the queue. Update the EBOOT |
| capture at ~38-40 fps on GNOME 50 | use `--source kms` ([Installation](Installation#kms-capture-recommended-on-gnome-50)) |
| KMS: "no permission to read the screen" | the message says which helper and the command. Package: `sudo setcap cap_sys_admin+ep /usr/libexec/pspstream/pspstream-kms`; repository: `make -C tools/kms cap` (again after each `make`). Check with `getcap` on the same file: it should show `cap_sys_admin=ep`. With the permission there and the error still showing: the partition is mounted with `nosuid`, or the server runs in a Flatpak terminal (like VS Code's), in a container or in a toolbox, where it does not apply (the message says which) |
| controls do not arrive | the log says "controls disabled": set up `/dev/uinput` ([Installation](Installation#controls-uinput)) |
| no audio | the server log says `audio: ...` at startup: without `pulsesrc`/`adpcmenc`, install `gstreamer1-plugins-good` and `gstreamer1-plugins-bad-free`. On the PSP, the 5th overlay line: "audio off" = SELECT + START + up; "waiting for the PC" = the server is not sending. UDP only |
| choppy audio | `empty` going up on the overlay: Wi-Fi swinging (the buffer grows by itself up to 120 ms). `--audio-rate 32000`, `22050` or `--audio-mono` ease the network |
| buzzing audio (EBOOT 1.1 before the fix) | it was the output buffer being reused while playing; update the EBOOT |
| the PC keeps playing the audio | the server records what goes out to the speakers. For the audio to go only to the PSP: `pactl load-module module-null-sink sink_name=psp`, pick "Null Output" as the output in the sound settings and run the server with `--audio-device psp.monitor` |
| the server warns about an old EBOOT, or that it "does not take P frames" | update the EBOOT (v1.0 or newer) |
| skewed image or wrong colors (JPEG) | `decoder=sw` in `server.txt` |
| something odd in the PC's H.264 | `--h264-encoder gstreamer` uses the old path; send the log |
| the web interface says "address not allowed" | use the IP or `localhost`, or allow the name with `--web-allow-host` ([Web Interface](Web-Interface#protections)) |
| messages in the wrong language | English is the default. `--lang pt` (or `PSPSTREAM_LANG=pt`, or Language in the web interface) for the server; `lang=pt` in `server.txt` (or "Language / Idioma" on the settings screen) for the PSP |
