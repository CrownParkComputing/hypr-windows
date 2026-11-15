# The bar button

`ws-overview` is a waybar custom module that replaces the usual row of
numbered workspace buttons with one button:

- it still prints the numbers — the active workspace in the accent colour,
  workspaces holding windows bright, empty ones dim, and `+N` when windows are
  parked in a special workspace (the "show desktop" minimise);
- **clicking it** opens or closes `hypr-windows`;
- **scrolling it** still changes workspace.

It refreshes on `SIGRTMIN+8` and every 5 seconds.

## Install

Copy the module into your waybar config, put `custom/windows` in
`modules-left` where your workspace buttons were, and add the style:

```jsonc
"custom/windows": {
  "exec": "$HOME/.local/bin/ws-overview",
  "return-type": "json",
  "interval": 5,
  "signal": 8,
  "format": "{}",
  "tooltip": true,
  "on-click": "setsid -f $HOME/.local/bin/hypr-windows >/dev/null 2>&1",
  "on-scroll-up": "$HOME/.local/bin/ws-go e-1",
  "on-scroll-down": "$HOME/.local/bin/ws-go e+1"
}
```

```css
#custom-windows {
    padding: 0 10px;
    margin: 0 1px;
    border: none;
    border-radius: 0;
    background: transparent;
    color: #F6F5F4;
    font-weight: 800;
}

#custom-windows:hover { background: rgba(255, 255, 255, 0.08); }
```

Then `pkill -USR2 waybar` to reload — **never** SIGTERM, which waybar does not
catch, so it dies for good.

The scroll actions call `ws-go`, a two-line helper of your own that switches
workspace (`hyprctl dispatch 'hl.dsp.focus({ workspace = "e+1" })'`); drop
those two lines if you have no such script.

## Colours

The active-workspace colour is read from `$primary` in
`~/.config/hypr/colors.conf` when that exists (the wallpaper-derived Material
palette that matugen-style rices generate), so the button re-themes itself with
the wallpaper. It falls back to a sane gold.

Deliberately **not** waybar's own `@ws_active`: in many rices that is a muted
grey that only reads as "active" because of a CSS glow behind it, and pango
markup has no glow — the workspace you are on would end up the dimmest number
in the row.
