Welcome to the **PSPStream** wiki: the PC screen and audio on the PSP over
Wi-Fi, with the PSP buttons going back to the PC as keyboard and mouse or an
Xbox controller. The summary and quick start are in the
[README](https://github.com/k7vinilstorage/PSP-Stream#readme); the details
live here.

Everything is in English by default, with Portuguese as an option (server,
web interface, installer and the PSP screens): see [Language](Server-Options#language).

## Getting started

- [Installation](Installation): requirements, ready-made downloads, the
  server from source (any Linux), the EBOOT and the PSP Wi-Fi.
- [Ubuntu Guide](Ubuntu-Guide): step by step on Ubuntu, Debian, Mint and
  derivatives.
- [Using the PSP](Using-the-PSP): first use, settings screen, shortcuts,
  overlay and `server.txt`.
- [Controls](Controls): the virtual Xbox controller and the keyboard and
  mouse profiles.
- [Web Interface](Web-Interface): the PC settings in the browser, also on
  the local network with a password.
- [Wolf](Wolf): PSPStream next to Wolf (Games on Whales), with Docker:
  installer, compose, Portainer, lobbies and troubleshooting.
- [Windows](Windows): the server on Windows 10/11 (experimental): download,
  setup, what works and troubleshooting.

## Reference

- [Server Options](Server-Options): the command line and the stats line.
- [Troubleshooting](Troubleshooting): symptom and what to do.
- [Limitations](Limitations): what PSPStream does not do.

## Under the hood

- [Performance](Performance): FPS, latency and bandwidth measured on the PSP-3000.
- [Measurements](Measurements): the development measurement notebook.
- [Protocol](Protocol): protocol v5 between the PSP and the PC (TCP and UDP).
- [Design Decisions](Design-Decisions): why each part is the way it is.
- [Wolf Internals](Wolf-Internals): the Wolf session, the pipelines, the
  controls and what was checked in the Wolf source.
- [Development](Development): builds, releases, tests, code layout and how
  to edit this wiki.
- [Windows Server Plan](Windows-Server-Plan): the plan behind the Windows
  server, and what of it is done.
- [PC Client Plan](PC-Client-Plan): a receiver for Linux and Windows PCs
  (a plan, nothing implemented).
