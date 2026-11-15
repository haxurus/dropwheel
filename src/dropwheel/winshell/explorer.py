"""Explorer integration for the current user (``dropwheel setup windows``).

Everything is per user and owned by Dropwheel, so it can be removed without touching anything
else (``--remove``):

* ``HKCU\\Software\\Classes\\{*,Directory}\\shell\\Dropwheel``: "Open in Dropwheel" in the context
  menu of files and folders (under "Show more options" on Windows 11).  Explorer starts one
  process per selected item: the host gathers them into one wheel;
* ``Dropwheel.lnk`` in *Send to*, in the Start menu and in the Startup folder (the host starts
  at login);
* the icon (``dropwheel.ico``, rendered from ``assets/dropwheel.svg``) and the icon font of the
  interface (Material Symbols Rounded, Apache-2.0, downloaded once from a fixed commit of
  google/material-design-icons and checked against its SHA-256) in ``%LOCALAPPDATA%\\dropwheel``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from .. import i18n, shellctl, util
from . import control

FONT_FILE = "MaterialSymbolsRounded.ttf"
FONT_URL = ("https://raw.githubusercontent.com/google/material-design-icons/"
            "10ecc14790612ca8fd164e3f4ffe9c16fc50d5b7/variablefont/"
            "MaterialSymbolsRounded%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf")
FONT_SHA256 = "7e74e36533ccee12c2b81e75bfcf3ccf123d3bb7e7e46d65d94445f23db0c97c"
FONT_FAMILY = "Material Symbols Rounded"

REG_KEYS = (r"Software\Classes\*\shell\Dropwheel", r"Software\Classes\Directory\shell\Dropwheel")
OPEN_MODULE = "dropwheel.winshell.open"


def home() -> Path:
    """``%LOCALAPPDATA%\\dropwheel`` (icon, fonts)."""
    return util.xdg_data_home() / "dropwheel"


def font_path() -> Path:
    return home() / "fonts" / FONT_FILE


def icon_path() -> Path:
    return home() / "dropwheel.ico"


def _shell_folder(*parts: str) -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))).joinpath(
        "Microsoft", "Windows", *parts)


def shortcuts() -> dict[str, Path]:
    return {"send_to": _shell_folder("SendTo", "Dropwheel.lnk"),
            "start_menu": _shell_folder("Start Menu", "Programs", "Dropwheel.lnk"),
            "startup": _shell_folder("Start Menu", "Programs", "Startup", "Dropwheel.lnk")}


def open_command() -> str:
    """The context-menu command line (``%1`` is the selected item)."""
    return subprocess.list2cmdline([control.gui_python(), "-P", "-m", OPEN_MODULE]) + ' "%1"'


def _reg_exists(key: str) -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, key))
    except OSError:
        return False
    return True


def state() -> dict[str, bool]:
    """What is installed (for ``doctor``)."""
    info = {name: path.is_file() for name, path in shortcuts().items()}
    info["context_menu"] = all(_reg_exists(k) for k in REG_KEYS)
    info["font"] = font_path().is_file()
    return info


# ======================================================================================= steps

def _write_menu(label: str, icon: str) -> None:
    import winreg

    for key in REG_KEYS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, label)
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
            if icon:
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, open_command())


def _delete_menu() -> None:
    import winreg

    for key in REG_KEYS:
        for sub in (key + r"\command", key):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass


_LNK_SCRIPT = (
    "$items = [Console]::In.ReadToEnd() | ConvertFrom-Json; $sh = New-Object -ComObject WScript.Shell; "
    "foreach ($i in @($items)) { $s = $sh.CreateShortcut($i.path); $s.TargetPath = $i.target; "
    "$s.Arguments = $i.args; $s.WorkingDirectory = $i.cwd; $s.Description = $i.desc; "
    "if ($i.icon) { $s.IconLocation = $i.icon }; $s.Save() }")


def _write_shortcuts(items: list[dict[str, str]]) -> bool:
    """Create ``.lnk`` files with WScript.Shell; the data goes on stdin, never in the script."""
    for item in items:
        Path(item["path"]).parent.mkdir(parents=True, exist_ok=True)
    res = util._powershell(_LNK_SCRIPT, json.dumps(items, ensure_ascii=False))
    return util._nt_ok(res)


def make_icon() -> Path | None:
    """Render ``assets/dropwheel.svg`` into a multi-size ``.ico``; ``None`` when it cannot."""
    svg = shellctl.data_dir() / "assets" / "dropwheel.svg"
    if not svg.is_file():
        return None
    try:
        from io import BytesIO

        from PIL import Image
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return None
    _app = QGuiApplication.instance() or QGuiApplication(["dropwheel", "-platform", "offscreen"])
    renderer = QSvgRenderer(str(svg))
    img = QImage(256, 256, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    target = icon_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.open(BytesIO(bytes(data.data()))).save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                               (128, 128), (256, 256)])
    return target


def font_installed() -> bool:
    """The icon font is ours, or installed system-wide."""
    if font_path().is_file():
        return True
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
    local = util.xdg_data_home() / "Microsoft" / "Windows" / "Fonts"
    return any(d.is_dir() and any(d.glob("MaterialSymbolsRounded*")) for d in (windir, local))


def download_font() -> Path:
    """Fetch the pinned icon font; raises ``OSError`` / ``ValueError`` (bad checksum)."""
    target = font_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(FONT_URL, headers={"User-Agent": "dropwheel-setup"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https URL
        data = resp.read(64 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    if digest != FONT_SHA256:
        raise ValueError(f"checksum mismatch ({digest})")
    tmp = target.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return target


# ================================================================================ install/remove

def install(lang: str, *, dry: bool, say: Any) -> int:
    """Add every entry; ``say(key, **fmt)`` reports each step.  Returns an exit status."""
    code = 0
    label = i18n.t("win.menu.open", lang)
    python = control.gui_python()
    if dry:
        say("cli.setup.win_would_menu", command=open_command())
        for path in shortcuts().values():
            say("cli.setup.would_create", path=str(path), line=f"{python} -P -m …")
        if not font_installed():
            say("cli.setup.win_would_font", url=FONT_URL)
        return 0
    icon = make_icon()
    icon_ref = f"{icon},0" if icon else ""
    _write_menu(label, str(icon) if icon else "")
    say("cli.setup.win_menu", label=label)
    cwd = str(Path.home())
    items = []
    for name, path in shortcuts().items():
        module = OPEN_MODULE if name == "send_to" else control.MODULE
        items.append({"path": str(path), "target": python, "args": f"-P -m {module}", "cwd": cwd,
                      "desc": label if name == "send_to" else "Dropwheel", "icon": icon_ref})
    if _write_shortcuts(items):
        for item in items:
            say("cli.setup.created", path=item["path"])
    else:
        say("cli.setup.write_failed", path=", ".join(i["path"] for i in items))
        code = 1
    if font_installed():
        say("cli.setup.win_font_present", family=FONT_FAMILY)
    else:
        try:
            say("cli.setup.win_font_done", path=str(download_font()))
        except (OSError, ValueError) as exc:
            say("cli.setup.win_font_failed", details=str(exc), url=FONT_URL, path=str(font_path()))
            code = 1
    return code


def remove(*, dry: bool, say: Any) -> int:
    paths = [*shortcuts().values(), icon_path(), font_path()]
    if dry:
        say("cli.setup.win_would_remove", keys=", ".join(REG_KEYS))
        for p in paths:
            if p.exists():
                say("cli.setup.win_would_delete", path=str(p))
        return 0
    _delete_menu()
    say("cli.setup.win_removed_menu")
    for p in paths:
        try:
            p.unlink()
            say("cli.setup.win_deleted", path=str(p))
        except FileNotFoundError:
            pass
        except OSError as exc:
            say("cli.setup.write_failed", path=f"{p} ({exc})")
    return 0
