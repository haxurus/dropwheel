[Documentation](README.md) · [Italiano](TROUBLESHOOTING.it.md)

# Troubleshooting

Always start with `dropwheel doctor`.

<details>
<summary><b>The wheel does not appear when I press Shift</b></summary>

<br>

1. **Is it running?** `hyprctl globalshortcuts` must list `dropwheel:shift` and `dropwheel:open`. If
   not, run `dropwheel start` from a terminal and read any errors.
2. **Are the binds loaded?** `hyprctl binds | grep -i -B2 -A8 dropwheel` must show them. If not, run
   `dropwheel setup hyprland`, then `hyprctl reload`.
3. **Does it appear only when you release Shift?** That is normal if you have binds starting with
   Shift, because Hyprland waits to see whether you are typing a longer combination. Tap Shift instead
   of holding it.
4. **Are you dragging from an XWayland application?** Drag-and-drop from X11 clients to Wayland surfaces
   does not work in Hyprland 0.57. Check with `hyprctl clients` (`xwayland: 1`). Run the application in
   Wayland mode, or copy the files and use SUPER+ALT+D.
5. **Still nothing?** As a test, set `dnd_check = false` under `[trigger]` in `config.toml`. The wheel
   then opens on every Shift and closes by itself if no drag arrives.

</details>

<details>
<summary><b>Where are the logs?</b></summary>

<br>

```sh
qs log -p /usr/share/dropwheel/shell -f            # system package
qs log -p ~/.local/share/dropwheel/shell -f        # install.sh
```

The shell log includes errors from the Python service. Commands started by Hyprland send their output
to `/dev/null`, so run `dropwheel start` from a terminal to see startup errors.

</details>

<details>
<summary><b>The icons are squares or letters</b></summary>

<br>

The icon font is missing. Run `sudo pacman -S ttf-material-symbols-variable`, then `fc-cache -f`, then
`dropwheel stop && dropwheel start`.

</details>

<details>
<summary><b>A feature is greyed out</b></summary>

<br>

The program it needs is not installed. Hover the segment to see which one and the command to install
it. Dropwheel checks again every ten minutes, or immediately after `dropwheel stop && dropwheel start`.

</details>

<details>
<summary><b>The result ended up in ~/Downloads/Dropwheel</b></summary>

<br>

This happens when the original is in a read-only or temporary location, such as a browser download in
progress, `/tmp` or a file shared by a Flatpak app. To always use one folder, set
`output.location = "folder"`.

</details>

<details>
<summary><b>Does pressing Shift make the source application move my files?</b></summary>

<br>

Hyprland also forwards Shift to the source application, where it often means "move". Dropwheel always
reads files as a copy and never moves or deletes them on drop. If you open the wheel and then drop in a
different application, release Shift first.

</details>

<details>
<summary><b>The <code>dropwheel</code> command is not found</b></summary>

<br>

With `install.sh`, the command is in `~/.local/bin`, which must be on your `PATH`. On fish, run
`fish_add_path ~/.local/bin`. On bash or zsh, add `export PATH="$HOME/.local/bin:$PATH"` to your shell's
startup file. Hyprland and the file managers use the full path anyway.

</details>

<details>
<summary><b>I have a file at <code>~/.config/quickshell/shell.qml</code></b></summary>

<br>

With a `shell.qml` directly in `~/.config/quickshell/`, Quickshell no longer finds configurations in its
subfolders. Dropwheel is **not affected**, because it is always started by path. Do not move its shell
into `~/.config/quickshell/`.

</details>

<details>
<summary><b>Document conversions produce nothing</b></summary>

<br>

Headless LibreOffice silently does nothing when its profile is already in use. Dropwheel uses a **private
profile**, so this does not affect it, but the first conversion is slower because the profile is being
created. If it still fails, `soffice --version` must answer and `dropwheel doctor` must see `soffice`.

</details>

<details>
<summary><b>Some files open with Dropwheel</b></summary>

<br>

Dropwheel's desktop entry declares the most common file types so that it appears in Open with. If one of
those types has no default application, a program might choose Dropwheel. Set the right one, for example:

```sh
xdg-mime default org.gnome.Loupe.desktop image/png
```

`install.sh` does this by itself for types that already had a default application.

</details>

<details>
<summary><b>File manager actions do nothing</b></summary>

<br>

Try `dropwheel open ~/some/file.jpg` from a terminal. Communication with the shell only works on the same
Wayland display, so the file manager needs `WAYLAND_DISPLAY` in its environment. That is normal when it
was started from the Hyprland session.

</details>

<details>
<summary><b>Dropwheel closes by itself at startup</b></summary>

<br>

The global shortcuts protocol does not allow two clients with the same name, so if a second copy starts,
one of them is closed. Check with `qs list`, then run `dropwheel stop && dropwheel start`.

</details>

<details>
<summary><b>A document is refused as too large</b></summary>

<br>

An Office document, EPUB or CBZ can declare enormous parts inside a tiny file. To protect your memory,
Dropwheel does not open parts compressed in unusual ways (bzip2, LZMA) or read text parts above 64 MiB
(256 MiB for the content of an ODS sheet). **Compress** and **Remove metadata** still copy embedded videos,
up to 8 GiB in total, and **Extract images** skips parts above 64 MiB and says which ones.

</details>

---

← [Previous](CLI.md) · [Next](PRIVACY.md) →
