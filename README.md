<p align="center">
  <img src="assets/dropwheel.svg" width="112" height="112" alt="Dropwheel icon">
</p>

<h1 align="center">Dropwheel</h1>

<p align="center">
  <b>A radial wheel of file actions for your Linux desktop.</b><br>
  Drag files, tap <kbd>Shift</kbd>, drop on a segment: converted, compressed, cropped, redacted, shared.
</p>

<p align="center">
  <img alt="Platform: Arch Linux" src="https://img.shields.io/badge/platform-Arch%20Linux-1793d1?logo=archlinux&logoColor=white">
  <img alt="Hyprland 0.57 or later" src="https://img.shields.io/badge/Hyprland-0.57%2B-58e1ff">
  <img alt="Quickshell 0.3.2 or later" src="https://img.shields.io/badge/Quickshell-0.3.2%2B-6f334a">
  <img alt="Python 3.11 or later" src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white">
  <img alt="Status: early release" src="https://img.shields.io/badge/status-early%20release-orange">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center">
  <b>English</b> · <a href="README.it.md">Italiano</a>
</p>

---

Dropwheel brings the idea of [Tangerine](https://tangerineformac.com) for macOS to Linux. While you
drag files from any application, tap <kbd>Shift</kbd> and a wheel appears under the pointer with the
actions that make sense for those files. Drop them on a segment and the job starts. A small card in
the corner of the screen shows the progress and then the result, which you can open, reveal, copy,
undo or drag straight into another application.

Everything runs **on your computer**, using free tools you probably already have, such as FFmpeg,
Ghostscript, LibreOffice and Tesseract. There are no accounts, no cloud services and no telemetry.

<p align="center">
  <img src="assets/wheel-preview.svg" width="420" alt="Illustration of the wheel for an image: Convert, Compress, Resize, Crop, Edit, Backdrop, Privacy and More around a thumbnail of the file">
  <br><sub>Illustration of the wheel for an image file. Colours follow your Caelestia theme.</sub>
</p>

> [!IMPORTANT]
> **Dropwheel currently runs on Arch Linux with Hyprland only.** The goal is to extend it to other
> operating systems: see [Platform support](#platform-support). A first preview of the engine and the
> command line on Windows is described in [Dropwheel on Windows](docs/WINDOWS.md).
>
> This is an early release. The code has an extensive automated test suite, but the complete
> drag-and-drop flow has not yet been exercised on a live Hyprland session, and many paths that call
> external programs have only run against test doubles. Expect rough edges, and please
> [open an issue](https://github.com/haxurus/dropwheel/issues) when you find one.

## Contents

- [Highlights](#highlights)
- [Platform support](#platform-support)
- [Quick start](#quick-start)
- [Documentation](#documentation)
- [Credits and license](#credits-and-license)

## Highlights

- **448 actions, chosen by file type.** Images, PDF, office documents, Markdown, code, data files,
  ebooks, subtitles, audio, video, archives, fonts, folders, web links, AppImages and disk images.
- **Only what makes sense.** A PDF is not offered "Convert to PDF". Selecting several files adds
  group actions such as merging PDFs or turning images into a slideshow.
- **Your originals are safe.** Tools write a new file next to the original and never overwrite an
  existing one. The only actions that touch the source are those that say so, such as Rename, Move
  and Trash. **Undo** sends a job's results to the trash.
- **Real redaction.** PDF redaction removes the underlying text and images, then reads the result
  back to verify they are gone.
- **Editors where you need them.** Crop, redact, trim, PDF page organizer, metadata, colour
  adjustment and background composer, all with previews.
- **Clear failures.** A feature whose program is missing is greyed out and shows the exact command
  that installs it. One failed file never stops the rest of a batch.
- **At home on Caelestia.** Colours, transparency and motion follow your theme. It also works without
  Caelestia, using a dark Material 3 palette.
- **Scriptable.** The same actions are available from a command line with JSON output.
- **Bilingual.** The interface is available in English and Italian.

## Platform support

| Platform | Status |
|---|---|
| Arch Linux, Hyprland 0.57+, Caelestia | **Primary target** |
| Arch Linux, Hyprland 0.57+, without Caelestia | **Supported**, with a built-in dark palette |
| Arch-based distributions | Expected to work with the same packages, not tested |
| Other Linux distributions | Not yet |
| Other Wayland compositors, X11 | Not yet |
| Windows 10 and 11 | **Preview**: the engine and the command line only, not yet tested on real Windows. See [Dropwheel on Windows](docs/WINDOWS.md) |
| macOS | Not yet |

### Where this is going

Arch and Hyprland are where the project started, not where it is meant to stop. **The goal is to
extend Dropwheel to other operating systems.** There is no schedule yet, and the order will depend on
what people need and who wants to help.

The architecture keeps most of the hard work portable, because the interface talks to the engine
through a small JSON protocol:

| Layer | Today | What a port needs |
|---|---|---|
| Action engine (Python) | Linux, plus Windows branches in preview for paths, trash, clipboard, processes and install hints | Notifications on Windows; the same work for macOS |
| Protocol | JSON lines over stdio | Nothing: any front end can speak it |
| Command line | Needs no compositor | Mostly nothing: a natural first step on a new platform |
| Trigger | Hyprland global shortcuts and `hyprctl` | A new way to detect Shift during a drag on each platform |
| Wheel and dialogs | Quickshell, Wayland layer-shell | Other Wayland compositors may reuse them; other systems need a new front end |
| Packaging | `PKGBUILD`, `install.sh` | Native packages for each system |

If you would like to see Dropwheel on your system, an
[issue](https://github.com/haxurus/dropwheel/issues) describing your setup is a useful start.

## Quick start

On Arch Linux with Hyprland 0.57 or later and Quickshell 0.3.2 or later:

```sh
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
makepkg -si                # or ./install.sh, without root, into ~/.local
dropwheel setup hyprland   # Shift, SUPER+ALT+D, layer rules and autostart
hyprctl reload
dropwheel start
```

Now start dragging a file and tap <kbd>Shift</kbd>. The requirements, the full set of optional programs,
updating and uninstalling are in the [installation guide](docs/INSTALL.md).

## Documentation

The complete documentation is in the [docs](docs/README.md) folder.

| Guide | What you will find |
|---|---|
| [Installing](docs/INSTALL.md) | Requirements, Arch package or `install.sh`, Hyprland setup, optional programs, updating, uninstalling |
| [Using Dropwheel](docs/USAGE.md) | The five ways to open the wheel, gestures and keys, job cards, configuration, file managers |
| [What it can do](docs/FEATURES.md) | Actions by file type, several files at once, where results go |
| [Command line](docs/CLI.md) | Every `dropwheel` command, exit codes, environment variables |
| [Configuration reference](docs/CONFIG.md) | Every key of `config.toml` and custom actions |
| [Function reference](docs/FUNCTIONS.md) | Every action, ring by ring, with the programs it needs |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | The wheel does not appear, logs, missing icons, greyed out features |
| [Privacy and security](docs/PRIVACY.md) | What uses the network and how untrusted files are handled |
| [Windows](docs/WINDOWS.md) | The Windows preview: what works, how to install it, what is missing |
| [Architecture and development](docs/DEVELOPMENT.md) | How the pieces fit, running from a checkout, tests |
| [Changelog](CHANGELOG.md) | What changed in each version |

## Credits and license

- The idea and the workflow come from **[Tangerine](https://tangerineformac.com)** for macOS, with
  inspiration from OpenShelf and DragShift. Dropwheel is not affiliated with these projects.
- The visual language comes from **[Caelestia](https://github.com/caelestia-dots)**. Dropwheel is built
  with **[Hyprland](https://hypr.land)** and **[Quickshell](https://quickshell.org)**, with Google's
  Material Symbols icons.
- The real work is done by outstanding free software, including FFmpeg, Pillow, ImageMagick, libvips,
  PyMuPDF, pypdf, qpdf, Ghostscript, Poppler, LibreOffice, pandoc, Typst, Tesseract, OCRmyPDF,
  whisper.cpp, ExifTool, 7-Zip, calibre, fontTools and yt-dlp, each under its own license.

Dropwheel is released under the [MIT License](LICENSE).
