# Changelog

[Italiano](CHANGELOG.it.md)

Notable changes to Dropwheel. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
- `tests/test_windows.py`: 16 tests that exercise the Windows code paths on Linux.
- Documentation split into guides in [docs](docs/README.md): installing, using, features, command line,
  troubleshooting, privacy, development and Windows, in English and Italian.
- This changelog.

### Changed

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
