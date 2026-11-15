[Documentation](README.md) · [Italiano](CLI.it.md)

# Command line

```sh
dropwheel open ~/Pictures/photo.heic             # the wheel, in click mode
dropwheel menu --json document.pdf               # available actions, as JSON
dropwheel run image.compress.balanced photo.jpg  # run an action without the interface
```

| Command | Description |
|---|---|
| `dropwheel start`, `stop`, `restart`, `status` | Control the shell and background service |
| `dropwheel open [FILE...]` | Open the wheel for files or URLs; without arguments, uses the clipboard |
| `dropwheel menu FILE... [--json]` | Show the actions available for files |
| `dropwheel actions [-c CATEGORY] [--json]` | List every action and whether it is available |
| `dropwheel run ACTION FILE... [-p KEY=VALUE]... [--json]` | Run an action; a group id runs its default |
| `dropwheel undo [JOB]`, `history [-n N]` | Undo a job (the last one by default), show recent jobs |
| `dropwheel doctor [--json] [--full]` | Check the desktop, the interface and the tools, with install commands |
| `dropwheel setup hyprland\|nautilus\|dolphin\|nemo\|thunar\|desktop\|all` | Install an integration |
| `dropwheel setup gnome [--remove]` | On GNOME: the Shell extension, the shortcut, starting at login, icon font ([details](GNOME.md)) |
| `dropwheel setup windows [--remove]` | On Windows: Explorer menu, *Send to*, Start menu and login shortcuts, icon font ([details](WINDOWS.md)) |
| `dropwheel shares [--stop ID\|--stop-all]` | List or stop active LAN shares |
| `dropwheel config [--path\|--init\|--edit]` | Show, create or edit the configuration |

Exit codes are `0` for success, `1` for an error, `2` for incorrect usage and `130` for an interruption.
`dropwheel setup` never overwrites a file without first saving a copy named
`<file>.dropwheel-bak-YYYYmmdd-HHMMSS`.

| Environment variable | Effect |
|---|---|
| `DROPWHEEL_CONFIG` | Alternative path for `config.toml` |
| `DROPWHEEL_DATA` | Data folder with the shell and integrations, normally `~/.local/share/dropwheel` or `/usr/share/dropwheel` |
| `DROPWHEEL_BACKEND` | The service command used by the shell, set by `dropwheel start` |

On Windows and GNOME, `start`, `stop` and `status` control the Dropwheel host (`python -m dropwheel.qtshell`)
instead of Quickshell, and the other commands reach it over a local connection on `127.0.0.1`.

---

← [Previous](FEATURES.md) · [Next](TROUBLESHOOTING.md) →
