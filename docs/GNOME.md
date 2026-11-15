[Documentation](README.md) · [Italiano](GNOME.it.md)

# Dropwheel on GNOME (Debian, Ubuntu, Arch)

> [!WARNING]
> **This version is new, and it has not been run in a real GNOME session yet.** The interface has run on
> Linux through the same host, the GNOME extension has been exercised with a stand-in, and the installer
> with a simulated Ubuntu, but what only GNOME can show (Shift during a drag from Files, the extension,
> the shortcut) is unverified. Please [report what you find](https://github.com/haxurus/dropwheel/issues).

On GNOME Dropwheel runs **the same wheel as on Hyprland**: the interface (wheel, job cards, editors) is the
same QML code, hosted by PySide6 instead of Quickshell, and the actions are the same Python engine. A small
GNOME Shell extension tells it where the pointer is and whether Shift is held, which GNOME does not tell
ordinary programs.

## What works

| Part | Status on GNOME |
|---|---|
| The wheel, the job cards, the editors, the QR code for sharing | **New** |
| Shift during a drag from Files (Nautilus) or the desktop | **New**: needs the extension |
| Hot corner | **New**: at the top right, under the top bar |
| Shortcut (<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd> by default) | **New** |
| Files: right click → *Scripts* → *Dropwheel* | **Supported** |
| Command line | **Supported**: every command |
| Colours from Caelestia | Not available: Dropwheel uses its built-in dark palette |

## Installing

**Requirements**

| | |
|---|---|
| **System** | Debian 13, Ubuntu 24.04 or later, Arch Linux, with GNOME 45 or later |
| **Python** | 3.11 or later (already there on these systems) |
| **Session** | GNOME on Wayland or on X11 |

**Install with one command**

```sh
curl -fsSL https://raw.githubusercontent.com/haxurus/dropwheel/main/install.sh | bash
```

The installer shows what it found (system, desktop, folder) and asks before every step:

- the packages Dropwheel needs, installed with apt or pacman (sudo asks for your password once);
- which groups of optional features you want (images, PDF, OCR, documents, audio and video, archives,
  fonts, sharing): a numbered list, <kbd>Enter</kbd> takes them all;
- on Debian and Ubuntu, a private Python environment in `~/.local/share/dropwheel/venv` with PySide6
  (an 80 MB download) and the Python modules of the chosen groups, so the system Python is never touched;
- `dropwheel setup gnome`: the extension, the shortcut, starting at login and the icon font.

Then **log out and back in once**: GNOME loads a new extension only at login. From a clone, `./install.sh`
does the same. `./install.sh --help` lists the options; `--no-packages` only prints the commands.

**What `dropwheel setup gnome` changes**

| What | Where |
|---|---|
| The GNOME Shell extension | `~/.local/share/gnome-shell/extensions/dropwheel@haxurus.github.io`, enabled in `org.gnome.shell` |
| The shortcut | A custom shortcut named *Dropwheel* (Settings → Keyboard), running `dropwheel open` |
| Starting at login | `~/.config/autostart/dropwheel.desktop` |
| The icon font | `~/.local/share/dropwheel/fonts`, downloaded once from a fixed version and checked against its SHA-256 |

`dropwheel setup gnome --dry-run` shows what would change, `--remove` removes all of it, and
`~/.local/share/dropwheel/uninstall.sh` removes Dropwheel itself.

## Using it

| Way | What you do |
|---|---|
| **Shift during a drag** | Start dragging files in Files or on the desktop, tap <kbd>Shift</kbd>: the wheel opens under the pointer. Move onto a segment and let go |
| **Hot corner** | Drag files into the top right corner, just under the top bar: the wheel opens next to it |
| **Shortcut** | Copy files in Files (<kbd>Ctrl</kbd>+<kbd>C</kbd>), then press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd>: the wheel opens at the pointer with those files |
| **Files menu** | Right click on files → *Scripts* → *Dropwheel* |
| **Command line** | `dropwheel open photo.jpg`, `dropwheel run image.compress.balanced photo.jpg` |

The shortcut is the `shortcut` key of [`[trigger]`](CONFIG.md#trigger); run `dropwheel setup gnome` again
after changing it. The [usage guide](USAGE.md) describes the wheel itself.

## Differences from Hyprland

- **How a drag is recognised.** GNOME does not tell other programs that a drag is under way, so Dropwheel
  treats "Shift while the mouse button is held" as one, like on Windows. Shift while you select text with
  the mouse opens the wheel too; it closes again when you release the button.
- **The window runs through XWayland**, so that it can place itself and stay above the other windows.
- **Without the extension** (before the first logout, or when it is turned off in the Extensions app)
  the wheel still opens from the shortcut, the Files menu and the command line, but Shift during a drag
  does nothing.
- **Colours.** Caelestia's colours do not exist on GNOME: the interface uses its built-in dark palette.

## Troubleshooting

| Problem | What to do |
|---|---|
| Nothing happens | `dropwheel status` tells whether Dropwheel runs, `dropwheel doctor` what is missing. The log is `~/.cache/dropwheel/qtshell.log` |
| Shift during a drag does nothing | Log out and back in once after installing. In the Extensions app, *Dropwheel* must be on |
| The shortcut does nothing | Another action may use it: change `trigger.shortcut`, then `dropwheel setup gnome` |
| The icons show as words | The icon font is missing: run `dropwheel setup gnome` again (it needs the network once) |
| The wheel is blurred, or in the wrong place, with display scaling | Report it with your scaling setting: fractional scaling has not been tried yet |

## What comes next

1. Testing on real Debian and Ubuntu machines, with and without display scaling.
2. Other desktops, starting from the same host (KDE Plasma: [Dropwheel on KDE Plasma](KDE.md)).
3. Colours that follow the GNOME light or dark style and accent colour.

Progress is tracked in the [changelog](../CHANGELOG.md).

---

← [Documentation](README.md)
