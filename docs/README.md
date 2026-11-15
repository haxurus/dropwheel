[Dropwheel](../README.md) · [Italiano](README.it.md)

# Dropwheel documentation

Dropwheel is a radial wheel of file actions: drag files, tap <kbd>Shift</kbd>, drop them on a segment, and they
are converted, compressed, cropped, redacted or shared, all locally. These pages cover installing, using and
extending it.

## For users

| Guide | What you will find |
|---|---|
| [Installing Dropwheel](INSTALL.md) | Requirements, Arch package or `install.sh`, Hyprland setup, optional programs, updating, uninstalling |
| [Using Dropwheel](USAGE.md) | The five ways to open the wheel, gestures and keys, job cards, configuration, file managers |
| [What Dropwheel can do](FEATURES.md) | Actions by file type, several files at once, where results go |
| [Command line](CLI.md) | Every `dropwheel` command, exit codes, environment variables |
| [Configuration reference](CONFIG.md) | Every key of `config.toml` and custom actions |
| [Function reference](FUNCTIONS.md) | Every action, ring by ring, with the programs it needs |
| [Troubleshooting](TROUBLESHOOTING.md) | The wheel does not appear, logs, missing icons, greyed out features |
| [Privacy and security](PRIVACY.md) | What uses the network and how untrusted files are handled |
| [Dropwheel on GNOME](GNOME.md) | Debian, Ubuntu and Arch with GNOME: installing, the extension, differences from Hyprland |
| [Dropwheel on Windows](WINDOWS.md) | The Windows preview: installing, the wheel from Explorer, differences from Linux |

## For contributors

| Guide | What you will find |
|---|---|
| [Architecture and development](DEVELOPMENT.md) | Overview of the pieces, running from a checkout, tests |
| [Architecture and developer guide](ARCHITECTURE.md) | Runtime design, protocol, how to add an action or a translation (English only) |
| [Specification](SPEC.md) | The binding contract: APIs, protocol fields, UX numbers (English only) |
| [Changelog](../CHANGELOG.md) | What changed in each version |

## Suggested reading order

1. [Installing Dropwheel](INSTALL.md), then [Using Dropwheel](USAGE.md).
2. [What Dropwheel can do](FEATURES.md) when you want to know whether a task is covered.
3. [Troubleshooting](TROUBLESHOOTING.md) the first time something does not behave.
4. [Configuration reference](CONFIG.md) once you want to change defaults or add your own actions.
