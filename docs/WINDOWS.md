[Documentation](README.md) · [Italiano](WINDOWS.it.md)

# Dropwheel on Windows (preview)

> [!WARNING]
> **This is a preview, and it has not been run on a real Windows machine yet.** The Windows code paths are
> covered by automated tests that simulate Windows on Linux, which checks the logic but not the behaviour
> of Windows itself. Please [report what you find](https://github.com/haxurus/dropwheel/issues).

On Windows 10 and 11, Dropwheel currently offers its **action engine and command line**: the same actions as
on Linux, run from a terminal. The wheel that opens when you tap <kbd>Shift</kbd> during a drag is not
available yet, because its interface is built on Quickshell, which exists only on Linux.

## What works

| Part | Status on Windows |
|---|---|
| Actions from the command line | **Preview.** `dropwheel run`, `menu`, `actions`, `undo`, `history`, `config`, `doctor` |
| External programs | Found in `PATH`, in `Program Files` and in the winget links folder |
| Install hints | `winget install` with the package id, `python -m pip install` for Python modules |
| Clipboard | Text, files and images through Windows PowerShell 5.1 |
| Open and show in folder | The default application, and Explorer with the file selected |
| Trash | The Recycle Bin, through `send2trash` |
| LAN share with QR code | Preview |
| The wheel, the job cards and the editors | **Not yet** |
| Shift during a drag, hot corner, SUPER+ALT+D | **Not yet** |
| Explorer integration | **Not yet** |

## Installing

**Requirements**

| | |
|---|---|
| **System** | Windows 10 or 11 |
| **Python** | 3.11 or later, from [python.org](https://www.python.org/downloads/windows/) or `winget install --id Python.Python.3.12 -e` |
| **Windows PowerShell** | 5.1, included in Windows |

**Install from the repository**

```powershell
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
py -m pip install .
dropwheel doctor
```

This also installs Pillow and `send2trash`, which are required on Windows. Optional Python modules enable
more actions, by family:

```powershell
py -m pip install ".[image,pdf,docs,media,fonts,archive,qr]"
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
Open a new terminal after installing a program, so that `PATH` is up to date.

## Using it

```powershell
dropwheel menu photo.heic                        # the actions available for a file
dropwheel run image.compress.balanced photo.jpg  # run one of them
dropwheel undo                                   # send the results of the last job to the Recycle Bin
```

Results are written next to the original, exactly as on Linux, and an existing file is never overwritten.
Every command is described in the [command line guide](CLI.md).

**Where Dropwheel keeps its files**

| What | Folder |
|---|---|
| Configuration | `%APPDATA%\dropwheel\config.toml` |
| History and undo backups | `%LOCALAPPDATA%\dropwheel` |
| Cache and thumbnails | `%LOCALAPPDATA%\Temp\dropwheel` |

## Differences from Linux

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

## What comes next

1. Testing the preview on real Windows 10 and 11 machines.
2. A Windows front end that reuses the wheel, the job cards and the editors.
3. Opening the wheel with <kbd>Shift</kbd> during a drag from Explorer. Shift in Explorer means *move*, so
   the drop will always be handled as a copy.
4. **Send to** and context menu entries in Explorer.

Progress is tracked in the [changelog](../CHANGELOG.md).

---

← [Documentation](README.md)
