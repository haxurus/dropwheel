# Changelog

[Italiano](CHANGELOG.it.md)

Notable changes to Dropwheel. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **KDE Plasma 6 on Debian, Ubuntu, Fedora, openSUSE and Arch**, Wayland and X11, not yet tested in a real Plasma session. See
  [Dropwheel on KDE Plasma](docs/KDE.md). The same wheel and host as on GNOME.
  - A KWin script tells Dropwheel where the pointer is (on Wayland) over D-Bus and registers the shortcut
    (`trigger.shortcut`, then System Settings → Shortcuts → KWin).
  - Shift and the mouse buttons come from XWayland or X11; a drag from a Wayland program counts for 10 seconds from its start
    (its end is not reported).
  - `dropwheel setup kde [--remove]`: the KWin script, starting at login and the icon font; `dropwheel doctor`
    checks the script and KWin's Legacy X11 App Support setting. `install.sh` recognises Plasma
    (`--desktop kde`) and `uninstall.sh` removes the script.
- **GNOME on Debian, Ubuntu and Arch**, not yet tested in a real GNOME session. See
  [Dropwheel on GNOME](docs/GNOME.md). The same wheel, hosted by the PySide6 host of the Windows preview.
  - A GNOME Shell extension (GNOME 45 or later) tells Dropwheel where the pointer is and whether Shift and the
    mouse buttons are held, over one D-Bus method.
  - `dropwheel setup gnome [--remove]`: the extension, a custom shortcut (`trigger.shortcut`) running
    `dropwheel open`, starting at login and the icon font.
  - Install hints with `apt` on Debian and Ubuntu, and pip for Python modules.
- **Fedora and openSUSE with GNOME** (Fedora 43 or later, openSUSE Tumbleweed and Leap 16), not yet tested on
  a real machine. `install.sh` installs the packages with dnf or zypper, PySide6 and most Python modules
  included, and keeps a private Python environment for what pip installs, one module at a time; on Fedora
  Atomic and openSUSE Aeon or MicroOS it prints the `rpm-ostree` or `transactional-update` command and takes
  PySide6 from pip. Install hints and `dropwheel doctor` give the dnf and zypper package names (Tumbleweed
  and Leap apart). RHEL derivatives are recognised too, untested; they need EPEL.
- **Interactive installers.** `install.sh` (Arch, Debian, Ubuntu, Fedora, openSUSE, macOS) and `install.ps1`
  (Windows) open with the project name and links, recognise the system and the desktop, and ask before every
  step: required packages (installed with pacman, apt, dnf, zypper, Homebrew or winget once you say yes),
  optional feature groups from a numbered list, the desktop integration, `PATH`. `curl … | bash` and
  `irm … | iex` work without cloning first.
- **Windows preview of the engine and the command line**, not yet tested on real Windows. See
  [Dropwheel on Windows](docs/WINDOWS.md). Every Windows code path is separate from the Linux one, which does
  not change.
  - Configuration in `%APPDATA%\dropwheel`, history and undo backups in `%LOCALAPPDATA%\dropwheel`, cache
    in `%LOCALAPPDATA%\Temp\dropwheel`.
  - Opening files with the default application, and showing them selected in Explorer.
  - Clipboard for text, files and images through Windows PowerShell 5.1, with paths passed on standard
    input rather than on the command line.
  - The Recycle Bin through `send2trash`, now a dependency on Windows.
  - External programs started without a console window and stopped together with their children.
  - Program lookup in `PATH`, `Program Files` and the winget links folder; Ghostscript found as `gswin64c`.
  - Install hints with `winget install` and `python -m pip install`.
  - *Open a terminal here* opens a `cmd.exe` console.
- **Windows interface (preview)**: the same wheel, job cards, editors and QR card as on Linux, hosted by
  PySide6 (`dropwheel.qtshell`). Shift during a drag, the hot corner, a global shortcut and
  *Open in Dropwheel* / *Send to* in Explorer open it.
  - `dropwheel setup windows [--remove]`: Explorer menu, *Send to*, Start menu and login shortcuts, and
    the icon font (downloaded once from a fixed version, checked against its SHA-256).
  - `trigger.shortcut` (default `Ctrl+Alt+D`), used on Windows.
  - `dropwheel start`, `stop`, `status` and `open` control the host over `127.0.0.1` with a per-start token.
  - `tests/test_qtshell.py`: 20 tests, including loading the real shell offscreen without QML warnings.
- **macOS preview (macOS 13 or later, Apple silicon and Intel)**, never run on a real Mac. See
  [Dropwheel on macOS](docs/MACOS.md). The same wheel, hosted by the PySide6 host.
  - Configuration, history and the icon font in `~/Library/Application Support/dropwheel`, cache in
    `~/Library/Caches/dropwheel`.
  - `open` and `open -R`, `pbcopy` and `pbpaste`, files and images on the pasteboard and the trash through
    `osascript` with the paths as arguments, Terminal for *Open a terminal here*, fonts in `~/Library/Fonts`.
  - Shift during a drag from the Quartz event source state, read through `ctypes`; the wheel floats above
    every Space and full-screen app without taking the focus. No global shortcut yet.
  - `dropwheel setup macos [--remove] [--dry-run]`: a Finder Quick Action (*Open in Dropwheel*), a
    LaunchAgent to start at login, and the icon font.
  - Install hints with `brew install` and `brew install --cask`; `install.sh` runs with macOS's bash 3.2,
    uses Homebrew, and offers to install Homebrew only by showing its official command and asking.
  - `tests/test_macos.py`: tests that exercise the macOS code paths on Linux.
- `tests/test_windows.py`: 16 tests that exercise the Windows code paths on Linux.
- Documentation split into guides in [docs](docs/README.md): installing, using, features, command line,
  troubleshooting, privacy, development and Windows, in English and Italian.
- This changelog.

### Changed

- On Windows the daemon exchanges paths with the shell as `/C:/…`; PySide6-Essentials is a Windows dependency.
- `install.sh` installs the missing packages after asking, from a terminal; before, it only printed the
  commands. Without a terminal, or with `--no-packages`, it still only prints them.
- The PySide6 front end moved from `dropwheel.winshell` to `dropwheel.qtshell`, since it now serves GNOME too.
- The main README is shorter. It keeps the overview, the platform table and a quick start, and links to
  the guides.

### Security

- On Windows, programs are looked up only as `.exe` or `.com` in absolute folders, never in the current
  folder, so a program lying next to the files being processed is never run. `.cmd` and `.bat` scripts are
  ignored, because `cmd.exe` would read `&` or `%` in a file name as a command. System programs
  (`powershell`, `cmd`, `taskkill`, `explorer`) are taken only from `%SystemRoot%`.
- On Windows, the trash refuses network shares and removable drives, which have no Recycle Bin and where
  the shell would delete files for good. Without `send2trash` it refuses instead of falling back to a hidden
  folder.
- On Windows, a LAN share is stopped through its state file, never with `os.kill`, which there terminates
  whatever process now has that id.
- On Windows, the terminal never runs a command line, and `System32\convert.exe` (a disk conversion tool)
  is never mistaken for ImageMagick.

### Removed

- On Windows, the AppImage, disk image and Linux package actions are not loaded, and the actions that depend
  on Linux desktop programs (wallpaper, monitor size preset, satty and swappy annotation, printing, font
  installation, KDE Connect) are neither listed nor runnable.
- Unused imports and an unused test variable.

### Fixed

- `actions/pdf.py` used a backslash inside an f-string expression, which Python 3.11 rejects although the
  project supports it.

## [1.0.0] - 2026-10-30

### Added

- First release: a radial *drag + Shift* file-action wheel for Arch Linux with Hyprland 0.57+ and
  Quickshell, with 448 actions for images, PDF, documents, data, ebooks, subtitles, audio, video, archives,
  fonts, folders, links, AppImages and disk images.
- Editors for crop, redaction, trimming, PDF pages, metadata, colour adjustment and backgrounds.
- Command line with JSON output, file manager integrations, English and Italian interface.

[Unreleased]: https://github.com/haxurus/dropwheel/compare/1bfaa50...HEAD
[1.0.0]: https://github.com/haxurus/dropwheel/tree/1bfaa50
