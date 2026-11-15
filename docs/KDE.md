[Documentation](README.md) · [Italiano](KDE.it.md)

# Dropwheel on KDE Plasma (Debian, Ubuntu, Arch)

> [!WARNING]
> **This version is new, and it has not been run in a real Plasma session yet.** The interface has run on
> Linux through the same host, the D-Bus service the KWin script calls has been exercised with a stand-in,
> and the installer in a private home directory, but what only Plasma can show (Shift during a drag from
> Dolphin, the KWin script, the shortcut) is unverified. Please [report what you find](https://github.com/haxurus/dropwheel/issues).

On KDE Plasma Dropwheel runs **the same wheel as on Hyprland and GNOME**: the interface (wheel, job cards,
editors) is the same QML code, hosted by PySide6 instead of Quickshell, and the actions are the same Python
engine. On Wayland a small KWin script tells it where the pointer is, which KWin does not tell ordinary
programs; on X11 nothing extra is needed for that.

## What works

| Part | Status on KDE Plasma |
|---|---|
| The wheel, the job cards, the editors, the QR code for sharing | **New** |
| Shift during a drag from Dolphin or the desktop | **New**: on Wayland, needs the KWin script |
| Hot corner | **New**: at the top right |
| Shortcut (<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd> by default) | **New**: registered by the KWin script |
| Dolphin: right click → *Open in Dropwheel* | **Supported** |
| Command line | **Supported**: every command |
| Colours from Caelestia | Not available: Dropwheel uses its built-in dark palette |

## Installing

**Requirements**

| | |
|---|---|
| **System** | Debian 13, Ubuntu or Kubuntu 25.04 or later, Arch Linux, with Plasma 6 (Plasma 5, as on Kubuntu 24.04, is not supported) |
| **Python** | 3.11 or later (already there on these systems) |
| **Session** | Plasma on Wayland or on X11 |

**Install with one command**

```sh
curl -fsSL https://raw.githubusercontent.com/haxurus/dropwheel/main/install.sh | bash
```

The installer recognises the Plasma session and asks before every step:

- the packages Dropwheel needs, installed with apt or pacman (sudo asks for your password once);
- which groups of optional features you want (images, PDF, OCR, documents, audio and video, archives,
  fonts, sharing): a numbered list, <kbd>Enter</kbd> takes them all;
- on Debian and Ubuntu, a private Python environment in `~/.local/share/dropwheel/venv` with PySide6
  (an 80 MB download) and the Python modules of the chosen groups, so the system Python is never touched;
- the Dolphin entry (*Open in Dropwheel*);
- `dropwheel setup kde`: the KWin script, the shortcut, starting at login and the icon font.

KWin loads the script right away, no logout needed. From a clone, `./install.sh` does the same;
`./install.sh --desktop kde` chooses Plasma when the installer runs outside a Plasma session.
`./install.sh --help` lists the options; `--no-packages` only prints the commands.

**What `dropwheel setup kde` changes**

| What | Where |
|---|---|
| The KWin script | `~/.local/share/kwin/scripts/dropwheel`, enabled in `~/.config/kwinrc` (`[Plugins] dropwheelEnabled`) |
| The shortcut | Registered by the script: System Settings → Keyboard → Shortcuts → KWin → *Dropwheel* |
| Starting at login | `~/.config/autostart/dropwheel.desktop` |
| The icon font | `~/.local/share/dropwheel/fonts`, downloaded once from a fixed version and checked against its SHA-256 |

`dropwheel setup kde --dry-run` shows what would change, `--remove` removes all of it, and
`~/.local/share/dropwheel/uninstall.sh` removes Dropwheel itself. The Dolphin entry is
`dropwheel setup dolphin` (`~/.local/share/kio/servicemenus/dropwheel.desktop`, executable as Plasma 6 requires).

## Using it

| Way | What you do |
|---|---|
| **Shift during a drag** | Start dragging files in Dolphin or on the desktop, tap <kbd>Shift</kbd>: the wheel opens under the pointer. Move onto a segment and let go |
| **Hot corner** | Drag files into the top right corner: the wheel opens next to it |
| **Shortcut** | Copy files in Dolphin (<kbd>Ctrl</kbd>+<kbd>C</kbd>), then press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd>: the wheel opens at the pointer with those files |
| **Dolphin menu** | Right click on files → *Open in Dropwheel* |
| **Command line** | `dropwheel open photo.jpg`, `dropwheel run image.compress.balanced photo.jpg` |

The shortcut starts as the `shortcut` key of [`[trigger]`](CONFIG.md#trigger); once the script has
registered it, change it in System Settings → Keyboard → Shortcuts → KWin. The [usage guide](USAGE.md)
describes the wheel itself.

## Differences from Hyprland

- **How a drag is recognised.** On X11, as on GNOME and Windows, "Shift while the mouse button is held".
  On Wayland KWin tells other programs when a drag starts but not when it ends, so a Shift tap within
  10 seconds of the start of a drag counts as "during a drag". A drag that ends elsewhere leaves the wheel
  open until those 10 seconds are over.
- **Shift on Wayland** reaches Dropwheel because KWin passes the modifier keys to X11 programs (System
  Settings → Legacy X11 App Support, the default since Plasma 6.2). With *Never* there, Shift during a drag
  does nothing; `dropwheel doctor` says so.
- **The window runs through XWayland**, so that it can place itself and stay above the other windows.
- **Without the KWin script** the wheel still opens from the Dolphin menu and the command line; on Wayland
  it opens where the pointer last crossed a Dropwheel window, and there is no shortcut.
- **Colours.** Caelestia's colours do not exist on Plasma: the interface uses its built-in dark palette.

## Troubleshooting

| Problem | What to do |
|---|---|
| Nothing happens | `dropwheel status` tells whether Dropwheel runs, `dropwheel doctor` what is missing. The log is `~/.cache/dropwheel/qtshell.log` |
| Shift during a drag does nothing | `dropwheel doctor` checks the KWin script and the Legacy X11 App Support setting. In System Settings → Window Management → KWin Scripts, *Dropwheel* must be on |
| The wheel opens in the wrong place | The KWin script is not running: `dropwheel setup kde`, then move the mouse |
| The shortcut does nothing | Another action may use it: change it in System Settings → Keyboard → Shortcuts → KWin |
| The icons show as words | The icon font is missing: run `dropwheel setup kde` again (it needs the network once) |
| The wheel is blurred, or in the wrong place, with display scaling | Report it with your scaling setting and the Legacy Applications (X11) scaling choice: they have not been tried yet |

## What comes next

1. Testing on real Debian, Kubuntu and Arch machines, on Wayland and X11, with and without display scaling.
2. Recognising the end of a drag on Wayland, if KWin's scripting API ever reports the mouse buttons.
3. Colours that follow the Plasma colour scheme.

Progress is tracked in the [changelog](../CHANGELOG.md).

---

← [Documentation](README.md)
