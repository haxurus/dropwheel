"""GNOME integration for the current user (``dropwheel setup gnome [--remove]``).

* the GNOME Shell extension (``integrations/gnome/<uuid>``) copied to
  ``~/.local/share/gnome-shell/extensions`` and added to ``org.gnome.shell enabled-extensions``
  (GNOME loads a new extension only at the next login on Wayland);
* a custom keyboard shortcut (``trigger.shortcut``, as on Windows) running ``dropwheel open``;
* ``~/.config/autostart/dropwheel.desktop`` running ``dropwheel start`` at login;
* the icon font, downloaded once like on Windows (:func:`explorer.download_font`), unless
  Material Symbols Rounded is installed system-wide.

The Nautilus script is ``dropwheel setup nautilus``.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .. import shellctl, util
from . import explorer
from .gnome import EXTENSION_UUID

KEYBINDINGS = "org.gnome.settings-daemon.plugins.media-keys"
KEY_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dropwheel/"
KEY_SCHEMA = f"{KEYBINDINGS}.custom-keybinding:{KEY_PATH}"

_ACCEL_MODS = {"ctrl": "<Control>", "control": "<Control>", "alt": "<Alt>", "shift": "<Shift>",
               "win": "<Super>", "super": "<Super>"}


def extension_dir() -> Path:
    return util.xdg_data_home() / "gnome-shell" / "extensions" / EXTENSION_UUID


def autostart_path() -> Path:
    return util.xdg_config_home() / "autostart" / "dropwheel.desktop"


def accelerator(text: str) -> str:
    """``"Ctrl+Alt+D"`` → ``"<Control><Alt>d"`` (GTK accelerator); ``""`` when not valid."""
    parts = [p.strip().lower() for p in text.split("+")] if text else []
    if not parts or not parts[-1]:
        return ""
    *mods, key = parts
    if any(m not in _ACCEL_MODS for m in mods):
        return ""
    if re.fullmatch(r"[a-z0-9]", key):
        if not mods:
            return ""
    elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key):
        key = key.upper()
    elif key in ("space", "tab", "home", "end", "insert", "delete"):
        key = key.capitalize()
    elif key == "enter":
        key = "Return"
    elif key in ("pageup", "pagedown"):
        key = "Page_Up" if key == "pageup" else "Page_Down"
    else:
        return ""
    return "".join(dict.fromkeys(_ACCEL_MODS[m] for m in mods)) + key


def parse_strv(text: str) -> list[str]:
    """A ``gsettings get`` string list (``['a', 'b']`` or ``@as []``)."""
    text = text.strip().removeprefix("@as").strip()
    try:
        value = ast.literal_eval(text or "[]")
    except (ValueError, SyntaxError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _str(text: str) -> str:
    """A GVariant string literal, as ``gsettings set`` parses it."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _strv(items: list[str]) -> str:
    return "[" + ", ".join(_str(i) for i in items) + "]"


def _gsettings(*args: str) -> str | None:
    exe = shutil.which("gsettings")
    if exe is None:
        return None
    try:
        res = subprocess.run([exe, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def dropwheel_command() -> list[str]:
    from ..cli import launcher_path

    found = launcher_path() or shutil.which("dropwheel")
    return [found] if found else [sys.executable, "-P", "-m", "dropwheel"]


def state() -> dict[str, bool]:
    """What is installed (for ``doctor``)."""
    enabled = parse_strv(_gsettings("get", "org.gnome.shell", "enabled-extensions") or "")
    paths = parse_strv(_gsettings("get", KEYBINDINGS, "custom-keybindings") or "")
    return {"extension": (extension_dir() / "extension.js").is_file() and EXTENSION_UUID in enabled,
            "shortcut": KEY_PATH in paths, "autostart": autostart_path().is_file(), "font": font_installed()}


def font_installed() -> bool:
    if explorer.font_path().is_file():
        return True
    fc = shutil.which("fc-list")
    if fc is None:
        return False
    try:
        res = subprocess.run([fc, ":family=Material Symbols Rounded", "family"], capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(res.stdout.strip())


# ================================================================================ install/remove

def install(shortcut: str, *, dry: bool, say: Any) -> int:
    """Add every entry; ``say(key, **fmt)`` reports each step.  Returns an exit status."""
    code = 0
    src = shellctl.integrations_dir() / "gnome" / EXTENSION_UUID
    if not (src / "extension.js").is_file():
        say("cli.setup.write_failed", path=str(src))
        return 1
    cmd = dropwheel_command()
    accel = accelerator(shortcut)
    if dry:
        say("cli.setup.would_create", path=str(extension_dir()), line=EXTENSION_UUID)
        if accel:
            say("cli.setup.gnome_would_shortcut", accel=accel, command=shlex.join([*cmd, "open"]))
        say("cli.setup.would_create", path=str(autostart_path()), line=shlex.join([*cmd, "start"]))
        if not font_installed():
            say("cli.setup.win_would_font", url=explorer.FONT_URL)
        return 0

    # the extension: a fresh copy, then enabled for the next login
    dest = extension_dir()
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
    except OSError as exc:
        say("cli.setup.write_failed", path=f"{dest} ({exc})")
        return 1
    enabled = parse_strv(_gsettings("get", "org.gnome.shell", "enabled-extensions") or "")
    if EXTENSION_UUID not in enabled and _gsettings("set", "org.gnome.shell", "enabled-extensions",
                                                    _strv([*enabled, EXTENSION_UUID])) is None:
        say("cli.setup.gnome_enable_failed", uuid=EXTENSION_UUID)
        code = 1
    else:
        say("cli.setup.gnome_extension", path=str(dest))
    # user extensions are off as a whole when this is true (Extensions app, main switch)
    if (_gsettings("get", "org.gnome.shell", "disable-user-extensions") or "").strip() == "true":
        _gsettings("set", "org.gnome.shell", "disable-user-extensions", "false")

    # the keyboard shortcut
    if accel:
        paths = parse_strv(_gsettings("get", KEYBINDINGS, "custom-keybindings") or "")
        ok = all(_gsettings("set", KEY_SCHEMA, key, _str(value)) is not None for key, value in
                 (("name", "Dropwheel"), ("command", shlex.join([*cmd, "open"])), ("binding", accel)))
        if ok and KEY_PATH not in paths:
            ok = _gsettings("set", KEYBINDINGS, "custom-keybindings", _strv([*paths, KEY_PATH])) is not None
        if ok:
            say("cli.setup.gnome_shortcut", accel=accel)
        else:
            say("cli.setup.gnome_shortcut_failed", accel=accel)
            code = 1

    # start at login
    from ..cli import _desktop_exec_arg

    exec_line = " ".join(_desktop_exec_arg(a) for a in [*cmd, "start"])
    text = ("[Desktop Entry]\nType=Application\nName=Dropwheel\nComment=Radial wheel of file actions\n"
            f"Exec={exec_line}\nIcon=dropwheel\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
    try:
        _write(autostart_path(), text)
        say("cli.setup.created", path=str(autostart_path()))
    except OSError as exc:
        say("cli.setup.write_failed", path=f"{autostart_path()} ({exc})")
        code = 1

    # the icon font
    if font_installed():
        say("cli.setup.win_font_present", family=explorer.FONT_FAMILY)
    else:
        try:
            say("cli.setup.win_font_done", path=str(explorer.download_font()))
        except (OSError, ValueError) as exc:
            say("cli.setup.win_font_failed", details=str(exc), url=explorer.FONT_URL, path=str(explorer.font_path()))
            code = 1
    return code


def remove(*, dry: bool, say: Any) -> int:
    paths = [extension_dir(), autostart_path(), explorer.font_path()]
    if dry:
        for p in paths:
            if p.exists():
                say("cli.setup.win_would_delete", path=str(p))
        say("cli.setup.gnome_would_unset", path=KEY_PATH)
        return 0
    enabled = parse_strv(_gsettings("get", "org.gnome.shell", "enabled-extensions") or "")
    if EXTENSION_UUID in enabled:
        _gsettings("set", "org.gnome.shell", "enabled-extensions", _strv([e for e in enabled if e != EXTENSION_UUID]))
    keys = parse_strv(_gsettings("get", KEYBINDINGS, "custom-keybindings") or "")
    if KEY_PATH in keys:
        _gsettings("set", KEYBINDINGS, "custom-keybindings", _strv([k for k in keys if k != KEY_PATH]))
        for key in ("name", "command", "binding"):
            _gsettings("reset", KEY_SCHEMA, key)
        say("cli.setup.gnome_unset", path=KEY_PATH)
    for p in paths:
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            say("cli.setup.win_deleted", path=str(p))
        except FileNotFoundError:
            pass
        except OSError as exc:
            say("cli.setup.write_failed", path=f"{p} ({exc})")
    return 0


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

