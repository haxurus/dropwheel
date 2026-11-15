[Documentation](README.md) · [Italiano](MACOS.it.md)

# Dropwheel on macOS (preview)

> [!WARNING]
> **This is a preview, and it has never run on a real Mac.** It was written and tested on Linux only:
> the macOS code paths are covered by automated tests that pretend to be macOS, but nothing that only a
> Mac can show (Shift during a drag from the Finder, the wheel above other windows, the Quick Action,
> start at login) has been seen working. Expect rough edges, and please
> [report what you find](https://github.com/haxurus/dropwheel/issues).

On macOS 13 Ventura or later, on Apple silicon and Intel Macs, Dropwheel runs **the same wheel as on
Linux and Windows**: the interface (wheel, job cards, editors) is the same QML code, hosted by PySide6,
and the actions are the same Python engine.

## What works

| Part | Status on macOS |
|---|---|
| The wheel, the job cards, the editors, the QR code for sharing | **Preview** |
| Shift during a drag from the Finder or the desktop | **Preview** |
| Hot corner | **Preview** |
| Finder: *Open in Dropwheel* under *Quick Actions* | **Preview** |
| Start at login | **Preview**: a LaunchAgent |
| Command line | **Preview**: every command, including `start`, `stop`, `open` |
| Colour picker and file picker in the editors | **Preview**: Dropwheel's own picker and the macOS file dialog |
| Shortcut | Not yet: use Shift during a drag, the Quick Action or `dropwheel open` |
| Colours from Caelestia | Not available: Dropwheel uses its built-in dark palette |
| Desktop notifications | Not yet: the job cards show every result |

## Installing

**Requirements**

| | |
|---|---|
| **System** | macOS 13 Ventura or later, Apple silicon or Intel |
| **Homebrew** | From [brew.sh](https://brew.sh): Python and the optional programs come from there |
| **Python** | 3.11 or later: `brew install python@3.13` (the `python3` of the Command Line Tools is too old) |

**Install with one command**

```bash
curl -fsSL https://raw.githubusercontent.com/haxurus/dropwheel/main/install.sh | bash
```

This is the same installer as on Linux, and it runs with the `bash` 3.2 that comes with macOS. It asks
before every step. When Homebrew is missing it shows Homebrew's official install command and runs it only
if you say yes. It installs Python with Homebrew when needed, creates a private Python environment
(`~/.local/share/dropwheel/venv`) with PySide6-Essentials (an 80 MB download) and Pillow, and lets you
pick the optional programs from numbered lists. It then installs Dropwheel into `~/.local`, offers to run
`dropwheel setup macos`, and starts it. From a clone, `./install.sh` does the same.

**Or install from the repository by hand**

```bash
brew install python@3.13
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
python3.13 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/dropwheel setup macos
.venv/bin/dropwheel start
```

`pip install -e` keeps Dropwheel linked to the cloned folder, where the interface files are: keep the
folder where it is. To update, run `git pull` in the folder, then `dropwheel restart`.

`dropwheel setup macos` prepares everything for the current user only:

- *Open in Dropwheel* in the Finder's *Quick Actions* (right click on files or folders): a small
  Automator workflow in `~/Library/Services` that runs `dropwheel open` with the selected items;
- a LaunchAgent in `~/Library/LaunchAgents`, so Dropwheel starts at every login. It is written, not
  loaded: `dropwheel start` starts Dropwheel now, and launchd picks the file up at the next login;
- the icon font of the interface (Material Symbols Rounded, 15 MB): downloaded once from a fixed
  version on GitHub and checked against its SHA-256 before it is used. Dropwheel loads it for itself
  only, it is not added to the Fonts of the system.

`--dry-run` shows what would change, `--remove` removes all of it. `dropwheel doctor` checks the
installation and lists what is missing.

**External programs**

As on Linux, every program is optional and enables its own actions. `dropwheel doctor` lists what is
missing, with the `brew` command that installs it. The most useful ones:

```bash
brew install ffmpeg imagemagick exiftool qpdf poppler ghostscript tesseract tesseract-lang
brew install pandoc sevenzip yt-dlp qrencode
brew install --cask libreoffice
```

Dropwheel also looks in `/opt/homebrew/bin` (Apple silicon) and `/usr/local/bin` (Intel), so it finds
the Homebrew programs even when it is started at login, without your shell's `PATH`.

## Using it

| Way | What you do |
|---|---|
| **Shift during a drag** | Start dragging files in the Finder or on the desktop, tap <kbd>Shift</kbd>: the wheel opens under the pointer. Move onto a segment and let go. Tap <kbd>Shift</kbd> again to change page |
| **Hot corner** | Drag files into the top right corner of the screen: the wheel opens next to it. Dropping them right on the corner opens the wheel in click mode |
| **Quick Action** | Right click on files or folders → *Quick Actions* → *Open in Dropwheel* (or *Services*, depending on the macOS version) |
| **Command line** | `dropwheel open photo.jpg`, `dropwheel run image.compress.balanced photo.jpg` |

Dropwheel always works on a copy, so the original stays where it is. Results are written next to the
original and an existing file is never overwritten, as on Linux. The [usage guide](USAGE.md) describes
the wheel itself; every command is in the [command line guide](CLI.md).

**Where Dropwheel keeps its files**

| What | Folder |
|---|---|
| Configuration | `~/Library/Application Support/dropwheel/config.toml` |
| History, undo backups and the icon font | `~/Library/Application Support/dropwheel` |
| Cache, thumbnails and the log (`qtshell.log`) | `~/Library/Caches/dropwheel` |
| Quick Action | `~/Library/Services/Open in Dropwheel.workflow` |
| Start at login | `~/Library/LaunchAgents/io.github.haxurus.dropwheel.plist` |

The `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME` variables, when set, still win.

## Differences from Linux

- **How a drag is recognised.** macOS does not tell other programs that a drag is under way, so
  Dropwheel treats "Shift while the mouse button is held" as one. It reads the state of Shift and of the
  mouse buttons (Quartz event source state); it installs no event tap and no keyboard hook. As far as
  Apple documents, that needs neither Accessibility nor Input Monitoring permission, but this has not
  been checked on a real Mac.
- **No shortcut yet.** A global shortcut needs Carbon's `RegisterEventHotKey`, which is not done yet.
  `trigger.shortcut` is ignored on macOS.
- **No Dock icon.** Dropwheel runs as a background helper, without a Dock icon or a menu bar.
- **Unsigned.** Dropwheel is not an app bundle and is not signed or notarised: it is Homebrew's Python
  running Dropwheel's code. Gatekeeper does not get involved, because nothing is downloaded through a
  browser, but macOS may show *Background Items Added* for the LaunchAgent (listed as Python, in System
  Settings → General → Login Items), and may ask whether Python may open files in Desktop, Documents or
  Downloads the first time an action works there.
- **Colours.** Caelestia's colours do not exist on macOS: the interface uses its built-in dark palette.
- **Actions that are not available.** AppImages, Linux disk images and distribution packages are not
  loaded at all. Setting the wallpaper, the monitor size preset, annotating with satty or swappy and
  KDE Connect are hidden and cannot be run. Printing works through CUPS, which is part of macOS.
- **Fonts** are installed into `~/Library/Fonts`, where macOS picks them up without fontconfig.
- **The Finder's trash only.** Files go to the trash through macOS itself; when a volume has no trash,
  Dropwheel refuses and leaves the file where it is.
- **Clipboard.** Text goes through `pbcopy` and `pbpaste`, files and images through the system
  pasteboard, so the Finder pastes the copied files.
- **Terminal.** *Open a terminal here* opens Terminal in the folder; `apps.terminal` may name another
  app, for example `iTerm`.
- **Show in folder** selects the files in the Finder.

## Troubleshooting

| Problem | What to do |
|---|---|
| Nothing happens | `dropwheel status` tells whether Dropwheel runs. `dropwheel start --foreground` runs it in the terminal with its messages; the log is `~/Library/Caches/dropwheel/qtshell.log` |
| The icons show as words | The icon font is missing: run `dropwheel setup macos` again (it needs the network once) |
| *Open in Dropwheel* is not in the menu | Look under *Services* too. Turn it on in System Settings → Keyboard → Keyboard Shortcuts → Services, or log out and back in once |
| Shift during a drag does nothing | Check that Dropwheel runs, then report your macOS version: whether macOS lets it read the Shift key is unverified |
| The wheel opens in the wrong place, or behind a full-screen app | Report it with your screens and their scaling: none of this has been tried on a Mac |
| A program is reported as missing although it is installed | Run `dropwheel restart`; programs outside Homebrew's folders must be in the `PATH` of the session |

## What comes next

1. Running the preview on real Macs, Apple silicon and Intel, and fixing what breaks.
2. A global shortcut through Carbon's `RegisterEventHotKey`.
3. A signed and notarised app bundle, so that Dropwheel appears under its own name in the system settings.
4. Desktop notifications for long jobs.

Progress is tracked in the [changelog](../CHANGELOG.md).

---

← [Documentation](README.md)
