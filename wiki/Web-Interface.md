With the server running, open **http://localhost:5124** on the PC. The page
shows the state (PSP connected, FPS on the PSP and of the capture, latency,
Wi-Fi, hitches, quality), the general settings and the log. In Docker, next
to Wolf, also see [Wolf](Wolf#7-web-interface-on-the-server-or-on-the-network).

## What you can change

| group | what it changes |
|---|---|
| General | language of the page and of the server messages (English or Portuguese) |
| Capture | source (portal, kms, x11, test, static, wolf), KMS monitor, portal window and cursor, Wolf target and conversion, FPS limit, downscaling filter, stretch |
| Video | codec, adaptive or fixed quality, target and limits of the adaptive one |
| Audio | on, source (what goes out to the speakers, test tone or a PipeWire source), rate, mono |
| Controls | on, profile, mouse speed |
| Network | Wi-Fi priority (DSCP), copy of the last chunk, port |

Each field says when the change applies:

- **applies right away**: language, quality, FPS limit, DSCP. Changing the
  language reloads the page in the new one;
- **restarts the capture**, with the PSP staying connected: source, codec,
  filter. The new capture comes up before the old one stops; if it does not
  come up (e.g. KMS without the helper), the old one goes on and the page
  shows why. With P frames, the first frame of the new capture is an IDR;
- **restarts the audio capture** or **the controls** (held keys are
  released);
- **on the PSP's next connection** (copy of the last chunk) or **when the
  server restarts** (port: also change the PSP's `server.txt`).

## Where it is saved

The changes go to `~/.config/pspstream/server.json` (only what differs from
the default; `--config` picks another file; in Docker, the
`pspstream-config` volume). At startup, the order is default < file <
command line: an option given on the command line wins over the file, and
the page marks those fields with "command line". If the capture saved in the
file does not come up, the server uses the command line one and warns in the
log, and the page stays reachable to change it.

## On the local network, with a password

By default, the page only opens on the PC itself (127.0.0.1). `--web
0.0.0.0:5124` (or the `PSPSTREAM_WEB` variable) opens it to the local
network, from a phone, for example. With the `PSPSTREAM_WEB_PASSWORD`
variable, the browser asks for a password (the user can be anything), with a
1 s wait after each wrong password; without it, anyone on the network can
change the settings. The password goes over HTTP, unencrypted: it is meant
for a home network. Open port 5124/tcp in the firewall. `--no-web` turns the
page off.

## Protections

- The page refuses addresses it does not know, against DNS rebinding: it
  accepts `localhost`, the PC name (and `name.local`) and any IP. Other
  names, like one from the router's DNS, go in `--web-allow-host` or
  `PSPSTREAM_WEB_HOSTS` (comma separated).
- POST requests only with `Content-Type: application/json` and with an
  Origin, if any, equal to the address: another site open in the browser
  cannot change anything.
- Nothing in it takes a file path or a GStreamer pipeline (`--source gst`
  and `--image` only on the command line).

The code is in
[`server/web.py`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/web.py).
