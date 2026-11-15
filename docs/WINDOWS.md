[Documentation](README.md) · [Italiano](WINDOWS.it.md)

# Dropwheel on Windows (preview)

> [!WARNING]
> **This is a preview, and it has not been run on a real Windows machine yet.** The interface has run
> on Linux through the same Windows host, and the Windows code paths are covered by automated tests,
> but what only Windows can show (Shift during a drag from Explorer, the shortcut, Explorer's menus)
> is unverified. Please [report what you find](https://github.com/haxurus/dropwheel/issues).

On Windows 10 and 11 Dropwheel runs **the same wheel as on Linux**: the interface (wheel, job cards,
editors) is the same QML code, hosted by PySide6 instead of Quickshell, and the actions are the same
Python engine.

## What works

| Part | Status on Windows |
|---|---|
| The wheel, the job cards, the editors, the QR code for sharing | **Preview** |
| Shift during a drag from Explorer or the desktop | **Preview** |
| Hot corner | **Preview** |
| Shortcut (<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd> by default) | **Preview** |
| Explorer: *Open in Dropwheel* in the context menu, *Send to* | **Preview** |
| Command line | **Preview**: every command, including `start`, `stop`, `open` |
| Colour picker and file picker in the editors | **Preview**: Dropwheel's own picker and the Windows file dialog |
| Colours from Caelestia | Not available: Dropwheel uses its built-in dark palette |
| Desktop notifications | Not yet: the job cards show every result |

## Installing

**Requirements**

| | |
|---|---|
| **System** | Windows 10 or 11 |
| **Python** | 3.11 or later, from [python.org](https://www.python.org/downloads/windows/) or `winget install --id Python.Python.3.12 -e` |
| **Git** | To get the code: `winget install --id Git.Git -e` |

**Install with one command**

```powershell
irm https://raw.githubusercontent.com/haxurus/dropwheel/main/install.ps1 | iex
```

The installer asks before every step: it installs Python and Git with winget when they are missing, gets
the code into `%LOCALAPPDATA%\dropwheel\src`, lets you pick the optional Python modules and programs from
numbered lists, can add the `dropwheel` command to `PATH`, then runs `dropwheel setup windows` and starts
Dropwheel. From a clone, `powershell -ExecutionPolicy Bypass -File install.ps1` does the same.

**Or install from the repository by hand**

```powershell
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
py -m pip install -e .
dropwheel setup windows
dropwheel start
```

`pip install -e` keeps Dropwheel linked to the cloned folder, where the interface files are: keep the
folder where it is. This also installs Pillow, `send2trash` and PySide6-Essentials (an 80 MB download). To update,
run `git pull` in the folder, then `dropwheel restart`.

`dropwheel setup windows` prepares everything for the current user only:

- *Open in Dropwheel* in the context menu of files and folders, and *Dropwheel* in *Send to*;
- a *Dropwheel* shortcut in the Start menu, and one in the Startup folder, so Dropwheel starts at login;
- the icon font of the interface (Material Symbols Rounded, 15 MB): downloaded once from a fixed
  version on GitHub and checked against its SHA-256 before it is used.

`--dry-run` shows what would change, `--remove` removes all of it. `dropwheel doctor` checks the
installation and lists what is missing.

Optional Python modules enable more actions, by family:

```powershell
py -m pip install -e ".[image,pdf,docs,media,fonts,archive,qr]"
```

If one of them fails to install, leave it out: only the actions that need it are affected.

**External programs**

As on Linux, every program is optional and enables its own actions. `dropwheel doctor` lists what is
missing, with the command that installs it. The most useful ones:

```powershell
winget install --id Gyan.FFmpeg -e
winget install --id ImageMagick.ImageMagick -e
winget install --id OliverBetz.ExifTool -e
winget install --id TheDocumentFoundation.LibreOffice -e
winget install --id JohnMacFarlane.Pandoc -e
winget install --id QPDF.QPDF -e
winget install --id oschwartz10612.Poppler -e
winget install --id UB-Mannheim.TesseractOCR -e
winget install --id 7zip.7zip -e
winget install --id yt-dlp.yt-dlp -e
```

Ghostscript is found as `gswin64c` once installed from the [official site](https://ghostscript.com/releases/).
After installing a program, run `dropwheel restart` so that Dropwheel sees the new `PATH`.

## Using it

| Way | What you do |
|---|---|
| **Shift during a drag** | Start dragging files in Explorer or on the desktop, tap <kbd>Shift</kbd>: the wheel opens under the pointer. Move onto a segment and let go. Tap <kbd>Shift</kbd> again to change page |
| **Hot corner** | Drag files into the top right corner of the screen: the wheel opens next to it. Dropping them right on the corner opens the wheel in click mode |
| **Shortcut** | Copy files in Explorer (<kbd>Ctrl</kbd>+<kbd>C</kbd>), then press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd>: the wheel opens at the pointer with those files. With nothing copied, it is an empty target to drop files on |
| **Context menu** | Right click on files or folders → *Open in Dropwheel* (on Windows 11, under *Show more options*) |
| **Send to** | Right click → *Send to* → *Dropwheel* |
| **Command line** | `dropwheel open photo.jpg`, `dropwheel run image.compress.balanced photo.jpg` |

While you hold <kbd>Shift</kbd>, Explorer shows *Move*: Dropwheel always works on a copy, so the
original stays where it is. Results are written next to the original and an existing file is never
overwritten, as on Linux. The [usage guide](USAGE.md) describes the wheel itself; every command is in
the [command line guide](CLI.md).

The shortcut is the `shortcut` key of [`[trigger]`](CONFIG.md#trigger), for example
`shortcut = "Win+Shift+D"`; an empty value turns it off. Run `dropwheel restart` after changing it.

**Where Dropwheel keeps its files**

| What | Folder |
|---|---|
| Configuration | `%APPDATA%\dropwheel\config.toml` |
| History and undo backups | `%LOCALAPPDATA%\dropwheel` |
| Icon and icon font | `%LOCALAPPDATA%\dropwheel` |
| Cache, thumbnails and the log (`qtshell.log`) | `%LOCALAPPDATA%\Temp\dropwheel` |

## Differences from Linux

- **How a drag is recognised.** Windows does not tell other programs that a drag is under way, so
  Dropwheel treats "Shift while the mouse button is held" as one. Shift while you select text with the
  mouse opens the wheel too; it closes again when you release the button.
- **Colours.** Caelestia's colours do not exist on Windows: the interface uses its built-in dark palette.
- **Actions that are not available.** AppImages, Linux disk images and distribution packages are not
  loaded at all. Setting the wallpaper, the monitor size preset, annotating with satty or swappy,
  printing, installing fonts and KDE Connect are hidden and cannot be run.
- **The Recycle Bin only.** If `send2trash` is missing, or the file is on a network share or a removable
  drive, which have no Recycle Bin, Dropwheel refuses and leaves the file where it is.
- **Only real executables.** Programs are looked up as `.exe` or `.com`, never in the current folder, and
  never as `.cmd` or `.bat` scripts, because those run through `cmd.exe`, which would read `&` or `%` in a
  file name as a command. Tools installed with npm (`svgo`, `prettier`) are therefore reported as missing.
  For an editor, write the full path to its `.exe`.
- **Custom actions.** A command written as a list runs directly. A command written as a single string
  needs `sh`, for example from Git for Windows; otherwise it is greyed out.
- **Terminal.** *Open a terminal here* opens a `cmd.exe` console in the folder.
- **Show in folder** selects the first file when there are several.
- **Configured commands** follow Windows quoting: `"C:\Program Files\App\app.exe" --flag`.

## Troubleshooting

| Problem | What to do |
|---|---|
| Nothing happens | `dropwheel status` tells whether Dropwheel runs. `dropwheel start --foreground` runs it in the terminal with its messages; the log is `%LOCALAPPDATA%\Temp\dropwheel\qtshell.log` |
| The icons show as words | The icon font is missing: run `dropwheel setup windows` again (it needs the network once) |
| The shortcut does nothing | Another program owns it: the log says so. Choose another one in `trigger.shortcut` |
| *Open in Dropwheel* is not in the menu | On Windows 11 it is under *Show more options* (or <kbd>Shift</kbd>+<kbd>F10</kbd>). Run `dropwheel setup windows` again if it is missing there too |
| The wheel opens on the wrong screen, or blurred | Report it with your screens and their scaling: mixed scaling has not been tried yet |
| The antivirus warns | Dropwheel reads the state of Shift and of the mouse buttons (no keyboard hook) and listens only on `127.0.0.1`, with a token that changes at every start. Please report the warning |

## What comes next

1. Testing the preview on real Windows 10 and 11 machines, including screens with different scaling.
2. Desktop notifications for long jobs.
3. An entry in the new Windows 11 context menu, which requires a signed package.
4. Colours that follow the Windows light or dark mode and accent colour.

Progress is tracked in the [changelog](../CHANGELOG.md).

---

← [Documentation](README.md)
