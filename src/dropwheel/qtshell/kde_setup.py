"""KDE Plasma integration for the current user (``dropwheel setup kde [--remove]``).

* the KWin script (``integrations/kde/dropwheel``) copied to ``~/.local/share/kwin/scripts`` and
  enabled in ``kwinrc`` (``[Plugins] dropwheelEnabled``), then reloaded through KWin's D-Bus
  ``reconfigure``; it sends the pointer position to the host and registers the keyboard shortcut
  (``trigger.shortcut``, written to ``[Script-dropwheel] Shortcut``);
* ``~/.config/autostart/dropwheel.desktop`` and the icon font, as on GNOME.

Dolphin's right-click entry is ``dropwheel setup dolphin``.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import shellctl, util
from . import explorer, gnome_setup
from .kde import SCRIPT_ID

_MODS = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Meta", "super": "Meta",
         "meta": "Meta"}
_NAMED = {"space": "Space", "tab": "Tab", "home": "Home", "end": "End", "insert": "Ins", "delete": "Del",
          "enter": "Return", "pageup": "PgUp", "pagedown": "PgDown"}


def script_dir() -> Path:
    return util.xdg_data_home() / "kwin" / "scripts" / SCRIPT_ID


def shortcut(text: str) -> str:
    """``"ctrl+alt+d"`` → ``"Ctrl+Alt+D"`` (a ``QKeySequence`` string); ``""`` when not valid.
    At least one modifier is required, except for function keys (as on Windows and GNOME)."""
    parts = [p.strip().lower() for p in text.split("+")] if text else []
    if not parts or not parts[-1]:
        return ""
    *mods, key = parts
    if any(m not in _MODS for m in mods):
        return ""
    if re.fullmatch(r"[a-z0-9]", key):
        if not mods:
            return ""
        key = key.upper()
    elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key):
        key = key.upper()
    elif key in _NAMED:
        key = _NAMED[key]
    else:
        return ""
    return "+".join([*dict.fromkeys(_MODS[m] for m in mods), key])


def _run(*argv: str) -> str | None:
    exe = shutil.which(argv[0])
    if exe is None:
        return None
    try:
        res = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def _kwinrc(group: str, key: str, value: str | None = None, *, delete: bool = False) -> str | None:
    """Read (``value`` None), write or delete a ``kwinrc`` key; ``None`` when that failed."""
    where = ["--file", "kwinrc", "--group", group, "--key", key]
    if delete:
        return _run("kwriteconfig6", *where, "--delete")
    if value is None:
        return _run("kreadconfig6", *where)
    return _run("kwriteconfig6", *where, value)


def _dbus(dest: str, path: str, method: str, *args: str) -> str | None:
    return _run("gdbus", "call", "--session", "--dest", dest, "--object-path", path, "--method", method, *args)


def _reconfigure() -> bool:
    """KWin reads ``kwinrc`` again: enabled scripts that are not loaded start, disabled ones stop."""
    return _dbus("org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure") is not None


def _unload() -> None:
    """Stop the running copy now (``reconfigure`` neither reloads a loaded script nor acts at once)."""
    _dbus("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", gnome_setup._str(SCRIPT_ID))


def _unregister_shortcut() -> None:
    """kglobalaccel keeps a script's shortcut after it stops, and keeps the old key when it starts again."""
    _dbus("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel.unregister",
          gnome_setup._str("kwin"), gnome_setup._str("Dropwheel"))


def state() -> dict[str, Any]:
    """What is installed (for ``doctor``).  ``loaded`` is None when KWin cannot be asked;
    ``shift`` is False when KWin passes no key to X11 programs (Legacy X11 App Support: Never),
    None when not set (KWin's default: Never before Plasma 6.2, the modifier keys since)."""
    enabled = (_kwinrc("Plugins", f"{SCRIPT_ID}Enabled") or "").strip() == "true"
    loaded = _dbus("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.isScriptLoaded",
                   gnome_setup._str(SCRIPT_ID))
    eavesdrop = (_kwinrc("Xwayland", "XwaylandEavesdrops") or "").strip()
    return {"script": (script_dir() / "contents" / "code" / "main.js").is_file() and enabled,
            "loaded": None if loaded is None else "true" in loaded, "shift": None if not eavesdrop else eavesdrop != "None",
            "autostart": gnome_setup.autostart_path().is_file(), "font": gnome_setup.font_installed()}


# ================================================================================ install/remove

def install(text: str, *, dry: bool, say: Any) -> int:
    """Add every entry; ``say(key, **fmt)`` reports each step.  Returns an exit status."""
    code = 0
    src = shellctl.integrations_dir() / "kde" / SCRIPT_ID
    if not (src / "contents" / "code" / "main.js").is_file():
        say("cli.setup.write_failed", path=str(src))
        return 1
    cmd = gnome_setup.dropwheel_command()
    accel = shortcut(text)
    if dry:
        say("cli.setup.would_create", path=str(script_dir()), line=f"kwinrc [Plugins] {SCRIPT_ID}Enabled=true")
        if accel:
            say("cli.setup.kde_would_shortcut", accel=accel)
        say("cli.setup.would_create", path=str(gnome_setup.autostart_path()),
            line=shlex.join([*cmd, "start"]))
        if not gnome_setup.font_installed():
            say("cli.setup.win_would_font", url=explorer.FONT_URL)
        return 0

    dest = script_dir()
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
    except OSError as exc:
        say("cli.setup.write_failed", path=f"{dest} ({exc})")
        return 1
    # the script reads the shortcut when it starts: unload the running copy, reconfigure starts this one
    old = (_kwinrc(f"Script-{SCRIPT_ID}", "Shortcut") or "").strip()
    if _kwinrc(f"Script-{SCRIPT_ID}", "Shortcut", accel) is None or \
            _kwinrc("Plugins", f"{SCRIPT_ID}Enabled", "true") is None:
        say("cli.setup.kde_enable_failed")
        code = 1
    else:
        _unload()
        if old != accel:
            _unregister_shortcut()
        say("cli.setup.kde_script" if _reconfigure() else "cli.setup.kde_script_later", path=str(dest))
        if accel:
            say("cli.setup.kde_shortcut", accel=accel)

    if not gnome_setup.write_autostart(cmd, say):
        code = 1
    if not gnome_setup.install_font(say):
        code = 1
    return code


def remove(*, dry: bool, say: Any) -> int:
    paths = [script_dir(), gnome_setup.autostart_path(), explorer.font_path()]
    if dry:
        for p in paths:
            if p.exists():
                say("cli.setup.win_would_delete", path=str(p))
        say("cli.setup.kde_would_unset")
        return 0
    _kwinrc("Plugins", f"{SCRIPT_ID}Enabled", delete=True)
    _kwinrc(f"Script-{SCRIPT_ID}", "Shortcut", delete=True)
    _unload()  # before its files go, or it runs until the next login
    _reconfigure()
    _unregister_shortcut()
    say("cli.setup.kde_unset")
    for p in paths:
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            say("cli.setup.win_deleted", path=str(p))
        except FileNotFoundError:
            pass
        except OSError as exc:
            say("cli.setup.write_failed", path=f"{p} ({exc})")
    return 0
