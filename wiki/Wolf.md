PSPStream shows on the PSP what runs in a
[Wolf](https://github.com/games-on-whales/wolf) lobby: the image, the audio
and the controls, next to Moonlight and without changing anything in Wolf.
It runs in a container next to Wolf and talks to its API. How it works on
the inside is in [Wolf Internals](Wolf-Internals).

**Status:** it works on a `stable` Wolf with NVIDIA, managed by Portainer.
The fresh install (`install.sh`, the full compose files) and the Intel/AMD
GPUs were tested against a fake Wolf, which mimics Wolf's API and
pipelines, but not on a real Wolf.

Contents:

1. [What you need](#1-what-you-need)
2. [Fresh install with the installer](#2-fresh-install-with-the-installer)
3. [Fresh install by hand (compose)](#3-fresh-install-by-hand-compose)
4. [Through Portainer](#4-through-portainer)
5. [I already have Wolf](#5-i-already-have-wolf)
6. [First use: Moonlight, lobby and PSP](#6-first-use-moonlight-lobby-and-psp)
7. [Web interface: on the server or on the network](#7-web-interface-on-the-server-or-on-the-network)
8. [Settings (`.env`)](#8-settings-env)
9. [Updating and undoing](#9-updating-and-undoing)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What you need

- A Linux server with **Docker** and the **compose** plugin
  (`docker compose version`).
- A GPU for Wolf to encode the Moonlight video:
  - **NVIDIA**: driver 530.30.02 or newer, the
    [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
    1.16 or newer (`sudo nvidia-ctk runtime configure --runtime=docker
    && sudo systemctl restart docker`) and `nvidia-drm.modeset=1` (`cat
    /sys/module/nvidia_drm/parameters/modeset` must say `Y`);
  - **Intel or AMD**: nothing besides the kernel driver (`/dev/dri`).
- **The PSP and the server on the same local network.** The PSP only speaks
  802.11b (2.4 GHz). On the PSP, use the server's LAN IP (`hostname -I`,
  something like `192.168.0.10`), not the Tailscale one (`100.x.y.z`) nor
  the Docker ones (`172.x.y.z`).
- PSPStream on the PSP (the same EBOOT as always, from
  [Releases](https://github.com/k7vinilstorage/PSP-Stream/releases)).

Ports in the server firewall (the installer opens them, if ufw or firewalld
is active):

| who | ports |
|---|---|
| PSPStream (the PSP) | 5123 UDP and TCP |
| web interface on the network (optional) | 5124 TCP |
| Wolf (Moonlight) | 47984, 47989, 48010 TCP; 47999, 48100, 48200 UDP |

---

## 2. Fresh install with the installer

On a server without Wolf. The installer shows each command and asks before
changing the system:

```sh
curl -fsSLO https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/install.sh
sudo bash install.sh
```

(From a clone of the repository: `sudo docker/install.sh`. To see
everything without changing anything: `--dry-run`. In Portuguese:
`--lang pt`.)

What it does, in order:

1. **Checks Docker** and whether there is already a Wolf on the server.
   With a Wolf from another install, it stops and points to
   [section 5](#5-i-already-have-wolf) (two Wolfs on the host network would
   fight over the ports).
2. **Detects the GPU**: NVIDIA (`nvidia-smi`), Intel/AMD (`/dev/dri`) or
   none. With NVIDIA, it checks the Container Toolkit, the runtime in
   Docker and `modeset`.
3. **Prepares the system as the Wolf documentation asks**: loads the
   `uinput` and `uhid` modules (and on every boot, in
   `/etc/modules-load.d/wolf.conf`), and installs the Wolf udev rules in
   `/etc/udev/rules.d/85-wolf.rules`, downloaded from the Wolf repository
   (they give Wolf access to the virtual controllers and keep them away
   from the server desktop).
4. **Opens the ports** in ufw or firewalld, if active.
5. **Writes the configuration** to `/opt/wolf-pspstream`: the compose files
   and a `.env` (permission 600) with the detected GPU and, if you want, the
   web interface open to the network with a password generated on the spot.
6. **Pulls the images and starts** Wolf and PSPStream. If the ready
   PSPStream image is not available, it builds it from GitHub.
7. **Shows the next steps**: the Moonlight pairing, the IP for the PSP and
   the web interface password.

Useful options:

| option | what for |
|---|---|
| `--gpu nvidia\|intel\|amd\|cpu` | skips detection |
| `--dir DIR` | another folder instead of `/opt/wolf-pspstream` |
| `--ref BRANCH` | the files from another branch or tag of the repository |
| `--lang pt` | installer in Portuguese, and `PSPSTREAM_LANG=pt` in the `.env` (server and web interface in Portuguese) |
| `--yes` | no questions (yes to everything) |
| `--no-host` | does not touch the system (modules, udev, firewall) |
| `--no-start` | only prepares the files |
| `--only-pspstream` | Wolf already runs elsewhere: PSPStream only ([section 5](#5-i-already-have-wolf)) |

Running it again updates the compose files and the images and keeps the
`.env`.

Next: [section 6](#6-first-use-moonlight-lobby-and-psp).

---

## 3. Fresh install by hand (compose)

The same the installer does, step by step.

1. **System** (as the Wolf documentation asks):

   ```sh
   sudo modprobe uinput uhid
   printf 'uinput\nuhid\n' | sudo tee /etc/modules-load.d/wolf.conf
   sudo curl -fsSL https://raw.githubusercontent.com/games-on-whales/wolf/stable/85-wolf.rules \
     -o /etc/udev/rules.d/85-wolf.rules
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```

2. **Files**: the [`docker/`](https://github.com/k7vinilstorage/PSP-Stream/tree/main/docker) folder of the repository has:

   | file | what |
   |---|---|
   | `compose.yml` | Wolf (Intel/AMD) + PSPStream |
   | `compose.nvidia.yml` | Wolf (NVIDIA) + PSPStream |
   | `pspstream.yml` | PSPStream only (Wolf already runs) |
   | `build.yml` | builds PSPStream instead of pulling the image |
   | `.env.example` | the settings ([section 8](#8-settings-env)) |

   ```sh
   sudo mkdir -p /opt/wolf-pspstream && cd /opt/wolf-pspstream
   for f in compose.yml compose.nvidia.yml pspstream.yml build.yml .env.example; do
     sudo curl -fsSLO "https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/$f"
   done
   sudo cp .env.example .env && sudo chmod 600 .env
   ```

3. **Configuration**: in `.env`, `COMPOSE_FILE=compose.nvidia.yml` (NVIDIA)
   or `compose.yml` (Intel/AMD), and the rest of
   [section 8](#8-settings-env).

4. **Start**:

   ```sh
   sudo docker compose up -d
   sudo docker compose logs -f pspstream
   ```

---

## 4. Through Portainer

**With the Git repository (recommended; updates with one click):**
*Stacks* → *Add stack* → **Repository**:

| field | value |
|---|---|
| Repository URL | `https://github.com/k7vinilstorage/PSP-Stream` |
| Repository reference | `refs/heads/main` (before the merge: `refs/heads/claude/psp-pc-screen-stream-lou5q7`) |
| Compose path | `docker/compose.nvidia.yml` (NVIDIA), `docker/compose.yml` (Intel/AMD) or `docker/pspstream.yml` (Wolf already runs) |
| Environment variables | the ones from [section 8](#8-settings-env) you want to change (e.g. `PSPSTREAM_WEB=0.0.0.0:5124` and `PSPSTREAM_WEB_PASSWORD=...`) |

To update: *Pull and redeploy* on the stack (checking "re-pull image").

**With the web editor:** paste the content of one of the compose files in
*Web editor* and the variables in *Environment variables*.

The system part (modules and udev rules from step 1 of
[section 3](#3-fresh-install-by-hand-compose)) is still done on the
server, once.

---

## 5. I already have Wolf

This was the tested case: Wolf already runs (for example in Portainer's
`steam` stack, container `steam-wolf-1`).

1. **In the Wolf service, two lines**: the API socket starts showing up on
   the host, in `/var/run/wolf`. Wolf UI's default configuration already
   looks for it there, so it keeps working.

   ```yaml
   services:
     wolf:
       # ... what you already have ...
       environment:
         - WOLF_SOCKET_PATH=/var/run/wolf/wolf.sock
       volumes:
         - /var/run/wolf:/var/run/wolf
   ```

   Update the stack (Wolf restarts; Moonlight drops and reconnects) and
   check:

   ```sh
   ls -l /var/run/wolf/       # srwxr-xr-x root root ... wolf.sock
   sudo curl -s --unix-socket /var/run/wolf/wolf.sock http://localhost/api/v1/lobbies; echo
   ```

   The API socket gives full control of Wolf (pairing clients, starting
   apps): mount it only in the PSPStream container (read-only, as the
   compose files do) and never expose it over TCP.

2. **PSPStream, in a separate stack** with
   [`docker/pspstream.yml`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/docker/pspstream.yml),
   through Portainer (section 4, compose path `docker/pspstream.yml`) or on
   the command line:

   ```sh
   sudo bash install.sh --only-pspstream
   ```

   If your Wolf changed the ping ports (`WOLF_VIDEO_PING_PORT`,
   `WOLF_AUDIO_PING_PORT`), repeat the values in PSPStream's `.env`. To find
   out: `docker inspect steam-wolf-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep WOLF_`.

3. **Firewall**: 5123 UDP and TCP (and 5124 TCP for the web interface on
   the network).

---

## 6. First use: Moonlight, lobby and PSP

1. **Moonlight**: add the server by IP. The first time, Moonlight shows a
   PIN, and Wolf writes a link to type it in the log:

   ```sh
   cd /opt/wolf-pspstream && sudo docker compose logs wolf | grep -i pin
   ```

   (replace `localhost` in the link with the server IP). A one-time setup:
   the first time an app opens, Wolf pulls its image, which can take a few
   minutes with a black screen.

2. **Open a game through Wolf UI**. Wolf UI creates a **lobby**, in one of
   two ways:

   | button | lobby | the PSP |
   |---|---|---|
   | **Start** | single player | **watches** while Moonlight is in it. Leaving Moonlight (closing the stream), the lobby keeps running, and the PSP joins by itself and becomes **the only controller** (player 1) |
   | **Coop** | multiplayer | joins and plays as **one more controller** (Moonlight's, if any, is the first) |

   Wolf UI creates both kinds without "stop when everyone leaves", so the
   lobby stays open until someone stops it through Wolf UI.

3. **On the PSP**: PSPStream → **Find the PC on the network** (or the
   server's LAN IP, port 5123). The PSPStream log shows:

   ```
   Wolf: session ... mirroring lobby <game> (...), conversion nvidia
   controls: the PSP session joined lobby <game> (...); the virtual controller goes to the game
   PSP connected over UDP: 192.168.0.50:...
   ```

### Controls in the game

The PSP reaches the game as **an Xbox controller**. In a Coop lobby with
Moonlight using a controller, the PSP's is the second one: the game (or
Steam Big Picture) may ask which controller belongs to which player. For
the PSP to be the first, use Start and leave Moonlight, or leave Moonlight
without a controller (no gamepad plugged into it, no on-screen controls and
without the option to always keep controller 1 connected, if your Moonlight
has it).

| PSP | `xbox` profile | holding SELECT |
|---|---|---|
| cross / circle / square / triangle | A / B / X / Y | L3 / R3 / BACK / Guide |
| D-pad | D-pad | right stick |
| L / R | LT / RT (full trigger) | LB / RB |
| START | Start | (SELECT + START is the PSP menu) |
| analog stick | left stick | left stick |

A quick tap on SELECT alone is BACK. Other profiles
(`PSPSTREAM_PROFILE`):
- `xbox-camera`: cross/circle/square/triangle become the right stick
  (camera) and the D-pad becomes A/B/X/Y.
- `xbox-shoulders`: L/R = LB/RB, and SELECT + L/R = LT/RT.

START + up + RB together is the Wolf UI shortcut and takes the session out
of the lobby. In the `xbox-shoulders` profile, that is START + up + R on the
PSP. If it happens, PSPStream joins the lobby again within 2 s.

---

## 7. Web interface: on the server or on the network

The web interface shows the state (what is being mirrored, FPS, latency,
audio, controls) and changes the settings with the PSP connected. The
changes stay in the `pspstream-config` volume.

**On the server only (the default):** `PSPSTREAM_WEB=127.0.0.1:5124`. From
another PC, through an SSH tunnel: `ssh -L 5124:127.0.0.1:5124 user@server`
and http://localhost:5124.

**On the local network:** in `.env`,

```sh
PSPSTREAM_WEB=0.0.0.0:5124
PSPSTREAM_WEB_PASSWORD=a-long-password
```

and open port 5124/tcp in the firewall. Go to **http://server-IP:5124**;
the browser asks for a user (any) and password. On the local network the
password goes unencrypted (HTTP): it is meant for home, not for a network
you do not trust. Without a password, anyone who reaches the port can
change the settings.

The web interface only accepts addresses it knows: `localhost`, the server
name (and `name.local`) and any IP. Another name, like one from the
router's DNS, goes in `PSPSTREAM_WEB_HOSTS` (comma separated); without it,
the page answers `address not allowed`. It is a protection against DNS
rebinding attacks.

---

## 8. Settings (`.env`)

They live in the `.env` of the compose folder (or in the Portainer stack
variables). After changing: `docker compose up -d`. What the web interface
changes wins over `.env` and is saved in the volume.

| variable | default | what |
|---|---|---|
| `COMPOSE_FILE` | `compose.yml` | which compose: `compose.yml`, `compose.nvidia.yml` or `pspstream.yml` |
| `PSPSTREAM_IMAGE` | `ghcr.io/k7vinilstorage/pspstream:nightly` | `nightly` (`main`), `latest` (the latest version), `1.2` (one version) or `pspstream:local` (built) |
| `PSPSTREAM_VIDEO_CONVERT` | `auto` (`nvidia` in the NVIDIA compose) | how Wolf brings the image down from the GPU: `nvidia`, `va` (Intel/AMD), `cpu` (Wolf with `WOLF_USE_ZERO_COPY=FALSE`) or `auto` (tries in that order, ~10 s per failed attempt) |
| `PSPSTREAM_WOLF_TARGET` | empty = the only open lobby | the name (or id) of the lobby to mirror; with several open, the log lists the names |
| `PSPSTREAM_WOLF_PIN` | empty | the lobby PIN, if it asks for one |
| `PSPSTREAM_PROFILE` | `xbox` | `xbox`, `xbox-camera` or `xbox-shoulders` (the old name `xbox-ombros` still works) |
| `PSPSTREAM_LANG` | `en` | language of the server messages and the web interface: `en` or `pt` |
| `PSPSTREAM_WEB` | `127.0.0.1:5124` | `0.0.0.0:5124` opens it to the network ([section 7](#7-web-interface-on-the-server-or-on-the-network)) |
| `PSPSTREAM_WEB_PASSWORD` | empty | web interface password |
| `PSPSTREAM_WEB_HOSTS` | empty | names accepted besides the server IP and name (e.g. one from the router's DNS), comma separated |
| `WOLF_IMAGE` | `ghcr.io/games-on-whales/wolf:stable` | the Wolf image |
| `WOLF_VIDEO_PING_PORT`, `WOLF_AUDIO_PING_PORT` | 48100, 48200 | only if you changed Wolf's |

For other server options (`--fps`, `--codec`, `-v`...), add them to the
`command` of the `pspstream` service in the compose file, for example
`command: ["--source", "wolf", "--fps", "30", "-v"]`.

### The PSPStream image

CI publishes the image at `ghcr.io/k7vinilstorage/pspstream` on every push
to `main` (`nightly` and `sha-<commit>`) and on every version (`latest` and
`1.2`). Before the code reaches `main`, or if the image does not pull,
build it:

```sh
cd /opt/wolf-pspstream
sudo docker build -t pspstream:local "https://github.com/k7vinilstorage/PSP-Stream.git#main"
# in .env: PSPSTREAM_IMAGE=pspstream:local
```

(or, from a clone: `docker compose -f compose.yml -f build.yml up -d --build`).

---

## 9. Updating and undoing

**Updating:** run the installer again, or:

```sh
cd /opt/wolf-pspstream && sudo docker compose pull && sudo docker compose up -d
```

In Portainer with Git: *Pull and redeploy*. The PSP EBOOT only changes if
the release notes say so.

**Undoing:**

```sh
cd /opt/wolf-pspstream && sudo docker compose down -v
```

This also deletes the volume with the web interface settings. Wolf keeps
its state (pairings, apps) in `/etc/wolf`, which stays there. On a Wolf
that already existed, also remove the two lines from its service.

---

## 10. Troubleshooting

| in the PSPStream log | what to do |
|---|---|
| `the Wolf API socket does not exist: /var/run/wolf/wolf.sock` | Wolf does not have the two lines (`WOLF_SOCKET_PATH` and the `/var/run/wolf` volume) or is still starting: `ls -l /var/run/wolf/` |
| `no permission to open /var/run/wolf/wolf.sock` | the `pspstream` service needs `user: "0:0"` (already in the compose files); `ls -ld /var/run/wolf` must be `drwxr-xr-x root root` |
| `nobody answers at /var/run/wolf/wolf.sock` | Wolf is stopped or restarting: `docker compose logs wolf` |
| `no lobby open in Wolf; waiting for one` | open a game through Wolf UI |
| `there are several lobbies open in Wolf` | `PSPSTREAM_WOLF_TARGET=<name>`; the message lists the names |
| `no frame with the ... conversion` | the conversion does not match Wolf's GPU: `PSPSTREAM_VIDEO_CONVERT` (`nvidia`, `va`, `cpu`) and the error in `docker compose logs wolf` |
| `... join the lobby: ... Lobby is full` | single-player lobby (Start) with Moonlight in it: the PSP watches until Moonlight leaves. To play along, use Coop |
| `... Invalid PIN` | `PSPSTREAM_WOLF_PIN` |
| `the target is a standalone Moonlight session` | the target points to a session outside a lobby: it can only be watched |

| symptom | what to do |
|---|---|
| the PSP does not find the server | firewall (5123 UDP and TCP), LAN IP, "client isolation" on the router |
| black screen on the PSP | does the log say `mirroring lobby`? If it says `no frame`, see the conversion above |
| image ok, the buttons do nothing | does the log say `joined lobby`? In a Start lobby with Moonlight in it, the PSP only watches. In the game, the PSP may be the second controller ([section 6](#controls-in-the-game)) |
| the web interface asks for a password and does not accept it | the password is in `PSPSTREAM_WEB_PASSWORD` in `.env` (the user can be anything) |
| `address not allowed` in the web interface | a name it does not know: use the IP or put the name in `PSPSTREAM_WEB_HOSTS` |
| Wolf UI stopped opening | in `/etc/wolf/cfg/config.toml`, the Wolf UI app must mount `/var/run/wolf/wolf.sock:/var/run/wolf/wolf.sock` and have `WOLF_SOCKET_PATH=/var/run/wolf/wolf.sock` (it is Wolf's default) |
| messages in Portuguese (or English) | `PSPSTREAM_LANG` in `.env`, or Language in the web interface (it wins over `.env`) |

**What to send if it does not work:**

```sh
cd /opt/wolf-pspstream    # or your folder/stack
sudo docker compose logs --tail 80 pspstream
sudo docker compose logs --since 10m wolf 2>&1 | grep -iE "pspstream|gstreamer|pipeline|error|warn|lobby|api" | tail -80
ls -l /var/run/wolf/
```

With `-v` in the `command` of `pspstream`, the log shows the pipelines
PSPStream asks Wolf for.
