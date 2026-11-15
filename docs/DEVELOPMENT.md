[Documentation](README.md) · [Italiano](DEVELOPMENT.it.md)

# Architecture and development

## Architecture

```mermaid
flowchart LR
    H["Hyprland<br/>Shift bind, drag state"] -->|global shortcut, hyprctl| S
    S["Quickshell shell (QML)<br/>wheel, job cards, dialogs"] <-->|JSON lines over stdio| D
    C["dropwheel CLI"] --> R
    D["Python daemon"] --> R["Action registry<br/>and job runner"]
    R --> T["FFmpeg, Pillow, PyMuPDF,<br/>LibreOffice, Tesseract, ..."]
```

- **Shell (`shell/`).** A Quickshell configuration launched by path, so it never interferes with
  Caelestia's own shell.
- **Daemon (`src/dropwheel/daemon.py`).** Answers menu, preview and job requests on stdin and stdout.
- **Actions (`src/dropwheel/actions/`).** One module per family, registered with a decorator that
  declares categories, parameters and required tools. Menus are built from those declarations.
- **Jobs (`src/dropwheel/jobs.py`).** Parallelism, cancellation, progress, atomic output, undo.
- **Qt host for Windows, macOS, GNOME and KDE Plasma (`src/dropwheel/qtshell/`).** A PySide6 program that loads the same
  `shell/shell.qml` through small stand-ins for the Quickshell modules, and answers the Hyprland commands
  itself; `win32.py`, `macos.py` (Quartz through `ctypes`), `gnome.py` (with the Shell extension in `integrations/gnome/`) and `kde.py` (with the KWin
  script in `integrations/kde/`) supply Shift and the pointer.
  `tests/test_qtshell.py` starts it offscreen on Linux.

[docs/ARCHITECTURE.md](ARCHITECTURE.md) explains how to add an action or a translation, and
[docs/SPEC.md](SPEC.md) is the full contract.

## Development

```sh
PYTHONPATH=src python -m dropwheel doctor
PYTHONPATH=src python -m dropwheel start   # uses shell/ and integrations/ from this checkout
make test                                  # pytest
make lint                                  # Python, scripts, Lua, XML, desktop entries, QML
```

Contributions are welcome, and reports from real Hyprland sessions are especially valuable right now.
So is help with bringing Dropwheel to [other systems](../README.md#where-this-is-going).

---

← [Previous](PRIVACY.md) · [Documentation](README.md) →
