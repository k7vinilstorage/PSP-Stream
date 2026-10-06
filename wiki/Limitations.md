- **One PSP at a time.** With Wolf, also one PSPStream per Wolf
  ([Wolf Internals](Wolf-Internals#wolf-limits-that-cannot-be-changed-through-the-api-alone)).
- **Local network only.** The stream has no authentication or encryption, and
  the PSP must be on the same network as the PC (port 5123 UDP and TCP). The
  web interface can have a password, but it goes over HTTP, unencrypted
  ([Web Interface](Web-Interface#on-the-local-network-with-a-password)).
- **The Windows server is experimental** ([Windows](Windows)), and its Xbox
  controller needs the ViGEmBus driver. Wolf, KMS capture and the portal are
  Linux-only.
- **No microphone**: audio only goes from the PC to the PSP.
- **Audio only goes over UDP** (the default).
- **Fixed 480x272 for H.264**; smaller resolutions (JPEG only) show up
  centered, without scaling.
- **KMS capture does not show the mouse cursor.**
- **The PSP decoder holds 2 frames**, so P frames cost 3 decodes (10.6 ms)
  instead of 1.
- **The PSP only speaks 802.11b** on 2.4 GHz, which limits throughput to
  ~380-460 KB/s ([Performance](Performance)).
- **The PSP screens only have the English and Portuguese texts**, without
  accents (the PSP font is ASCII only).
