The PSP buttons reach the PC as an Xbox controller or as keyboard and mouse.
Pick the profile with `--profile` or in the [Web Interface](Web-Interface).
For that, the server needs `/dev/uinput`
([Installation](Installation#controls-uinput)); on Windows, keyboard and
mouse need nothing and the Xbox controller needs the ViGEmBus driver
([Windows](Windows#xbox-controller-vigembus)). With Wolf, the Xbox profiles
apply inside the game ([Wolf](Wolf#controls-in-the-game)).

## Xbox controller (`--profile xbox`)

As with Sunshine, the PC gets a **virtual Xbox 360 controller**, with the
same vendor, model, buttons and axes as the `xpad` driver. Native and Proton
(SDL) games, Steam and the browser recognize it with no setup. It has no
rumble: the PSP has no motor.

The PSP has fewer controls than an Xbox pad. The rest comes from a layer:
**while SELECT is held**, the other buttons change function, and **a quick
tap on SELECT alone** is BACK (View).

| PSP | `xbox` | holding SELECT |
|---|---|---|
| cross / circle / square / triangle | A / B / X / Y | L3 / R3 / BACK / Guide |
| D-pad | D-pad | right stick |
| L / R | LT / RT (full trigger) | LB / RB |
| START | Start | (SELECT + START is the PSP menu) |
| analog stick | left stick | left stick |

- `xbox-camera`, for 3D games: cross/circle/square/triangle become the
  **right stick** (camera) and the D-pad becomes A/B/X/Y (down = A,
  right = B, left = X, up = Y). Holding SELECT, the D-pad is a D-pad again.
- `xbox-shoulders`: L/R = LB/RB and SELECT + L/R = LT/RT.

## Keyboard and mouse

`game` profile (default):

| PSP | PC |
|---|---|
| D-pad | W A S D |
| cross / circle / square / triangle | space / Ctrl / R / E |
| R / L | left / right click |
| START / SELECT | Esc / Tab |
| analog stick | mouse |

`desktop` profile: D-pad = arrow keys, cross/circle = clicks, SELECT = Alt+Tab.
`arrows` profile: for emulators and old games.

The profiles used to have Portuguese names (`jogo`, `setas`, `xbox-ombros`);
those names still work, on the command line, in `PSPSTREAM_PROFILE` and in
saved settings.

## Your own profiles

The profiles live in
[`server/keymap.json`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/keymap.json)
(the `_help` keys explain the format), with adjustable dead zone, curve and
speed. If the PSP goes away with something pressed, everything is released
in 0.5 s (`--input-timeout`). `--input-dry-run` only shows in the log what
would be injected.
