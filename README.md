# hypr-windows

An on-screen window map and dock for **Hyprland 0.57**, plus the bar button
that opens it.

Every open window, grouped by the workspace it sits on. Click a window to go
to it, drag it onto another workspace to move it, press its **✕** to close it,
and the top line always names the window that currently has focus.

It docks to any edge, either locked open — reserving its strip so tiled
windows sit beside it — or popping in when the pointer touches the edge.

![left dock](docs/dock-left.png)

## Why

Hyprland hides a lot of state: windows parked on another workspace, windows in
a special workspace (the usual "show desktop" minimise), and nothing on screen
says what has focus. This puts all of it in one place you can act on.

## Install

```sh
./install.sh            # symlinks bin/* into ~/.local/bin
hypr-windows            # start it (run again to toggle)
```

Needs `gtk4`, `gtk4-layer-shell`, `python-gobject` and `hyprctl`.

To put it on the bar in place of waybar's workspace buttons, see
[`waybar/README.md`](waybar/README.md).

## Using it

| Action | What it does |
| --- | --- |
| Click a window chip | Switches to that window's workspace **and** focuses it |
| Drag a chip onto a workspace | Moves that window there |
| Click a chip's ✕ | Closes that window |
| `L` `R` `T` `B` | Moves the dock to that edge |
| `LOCKED` / `POP-IN` | Locks the dock open, or lets it slide away |

Settings live in `~/.config/hypr-windows/config.json` and are written whenever
you change them from the dock:

```json
{
  "position": "left",      // left | right | top | bottom
  "locked": true,          // true: a dock that reserves its strip
                           // false: slides off the edge, pops in on touch
  "width": 300,            // thickness of a left/right dock
  "hide_delay_ms": 600,    // grace period before it slides away again
  "trigger_px": 4          // sliver left on screen to touch when slid out
}
```

Flags override the stored settings for one run; add `--save` to keep them:

```sh
hypr-windows --right --save      # dock on the right from now on
hypr-windows --pop-in            # just this once, slide away when unused
hypr-windows --width 360 --save
```

`SIGUSR1` slides a pop-in dock in or out, so a keybind can reach it:

```lua
hl.bind("SUPER + W", hl.dsp.exec_cmd("pkill -USR1 -f hypr-windows"))
```

Running `hypr-windows` again quits a locked dock, and slides a pop-in one
in or out — so one bar button or keybind does the right thing in both modes.

## Notes for anyone hacking on it

Three things here cost real time to find:

- **Hyprland 0.57's dispatchers are Lua, and only honour a window *object*.**
  `hyprctl dispatch "movetoworkspace 1,address:0x..."` is parsed as Lua and
  dies; worse, `hl.dsp.window.close("address:0x...")` returns `ok` and closes
  **whatever has focus**. Every action here loops over `hl.get_windows()` and
  matches `w.address`, then passes the object: `hl.dsp.window.move({ workspace
  = 1, window = w })`. Targeting by object was checked for close by killing an
  unfocused throwaway while a second throwaway held focus — the right one died.
- **gtk4-layer-shell must be loaded before libwayland-client**, which a script
  cannot arrange at link time. Without it the window silently comes up as an
  ordinary toplevel. `hypr-windows` re-execs itself once with
  `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so`; the `liblayer-shell-preload.so`
  in the same package does *not* fix it.
- **A hair-thin trigger window would not map** beside a hidden dock, and an
  unmapped dock cannot catch a pointer. Pop-in therefore keeps one surface
  mapped and slides it off the edge with a negative margin, leaving a few
  pixels to touch.

The dock sets `KeyboardMode.NONE`: with keyboard focus it would become the
focused window and the focus line would only ever name itself. It sits on
layer `TOP`, the same as a bar, so a fullscreen window and anything on the
`OVERLAY` layer still draw above it.

## Licence

MIT. See [LICENSE](LICENSE).
