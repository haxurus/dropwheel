"""The Windows front end (``dropwheel.qtshell``), on Linux.

The pure parts are tested directly.  ``test_host_*`` start the real host offscreen (PySide6
required, else skipped): it must load the unchanged Linux shell through the Quickshell stand-ins
without a single QML warning and answer over IPC.  What only Windows can show (the hotkey, the
Shift watcher, input pass-through, Explorer drops) is not covered here.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dropwheel import config as cfgmod
from dropwheel import daemon, tools
from dropwheel.qtshell import control, explorer, virtual, win32

ROOT = Path(__file__).resolve().parent.parent


# ===================================================================================== win32

@pytest.mark.parametrize(("text", "expected"), [
    ("Ctrl+Alt+D", (win32.MOD_CONTROL | win32.MOD_ALT, ord("D"))),
    ("ctrl + shift + f5", (win32.MOD_CONTROL | win32.MOD_SHIFT, 0x74)),
    ("Win+Space", (win32.MOD_WIN, 0x20)),
    ("F9", (0, 0x78)),
    ("D", None),            # a plain key would fire while typing
    ("Ctrl+", None),
    ("Hyper+D", None),
    ("Ctrl+Alt+Del", None),
])
def test_parse_shortcut(text, expected):
    assert win32.parse_shortcut(text) == expected


def test_win32_is_inert_off_windows():
    if win32.WINDOWS:
        pytest.skip("Windows")
    assert win32.key_down(win32.VK_SHIFT) is False
    assert win32.register_hotkey(1, win32.MOD_ALT, ord("D")) is False
    win32.set_window_style(123, click_through=True, no_activate=True)  # no error


def test_shortcut_config_key():
    assert cfgmod.DEFAULTS["trigger"]["shortcut"] == "Ctrl+Alt+D"
    for good in ("Ctrl+Alt+D", "win+shift+f12", "F9", ""):
        assert cfgmod._check("regex", cfgmod._SCHEMA["trigger.shortcut"][1], good)[0], good
    for bad in ("D", "Ctrl+", "Ctrl+Alt+Del"):
        assert not cfgmod._check("regex", cfgmod._SCHEMA["trigger.shortcut"][1], bad)[0], bad


# ============================================================================== path forms

def test_daemon_path_forms():
    out = daemon.nt_to_shell({"files": ["C:\\Users\\a b\\é.png", "d:/x", "\\\\srv\\share\\f", "rel\\x"],
                              "n": 3, "t": ("E:\\y",)})
    assert out == {"files": ["/C:/Users/a b/é.png", "/d:/x", "//srv/share/f", "rel\\x"], "n": 3, "t": ["/E:/y"]}
    back = daemon.nt_from_shell({"files": ["/C:/Users/a b/é.png", "/home/x", "//srv/share/f"], "k": {"p": "/d:/x"}})
    assert back == {"files": ["C:/Users/a b/é.png", "/home/x", "//srv/share/f"], "k": {"p": "d:/x"}}


def test_daemon_converts_only_on_windows(monkeypatch):
    buf = io.BytesIO()
    d = daemon.Daemon(io.BytesIO(), buf, config=cfgmod.defaults())
    d.emit({"event": "x", "path": "C:\\a\\b.png"})
    monkeypatch.setattr(tools, "WINDOWS", True)
    d.emit({"event": "x", "path": "C:\\a\\b.png"})
    lines = [json.loads(line) for line in buf.getvalue().decode().splitlines()]
    assert [m["path"] for m in lines] == ["C:\\a\\b.png", "/C:/a/b.png"]


def test_virtual_paths():
    assert virtual.to_shell_path("C:\\Users\\x.png") == "/C:/Users/x.png"
    assert virtual.to_shell_path("/home/x") == "/home/x"
    assert virtual.from_shell_path("/C:/Users/") == "C:/Users/"
    assert virtual.from_shell_path("/home/x") == "/home/x"


# ========================================================================= virtual commands

def test_virtual_lookup_covers_every_shell_command():
    """Every command the QML spawns is either the daemon or answered in-process."""
    texts = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "shell").rglob("*.qml"))
    assert '["hyprctl", "-j", "monitors"]' in texts and '["hyprctl", "-j", "cursorpos"]' in texts
    assert virtual.lookup(["hyprctl", "-j", "monitors"]) is virtual._monitors
    assert virtual.lookup(["hyprctl", "-j", "cursorpos"]) is virtual._cursorpos
    assert virtual.lookup(["hyprctl", "dispatch", "x"]) is virtual._missing
    assert virtual.lookup(["hyprpicker", "-f", "hex"]) is virtual._hyprpicker
    assert virtual.lookup(["zenity", "--file-selection"]) is virtual._zenity
    assert virtual.lookup(["kdialog", "--getopenfilename"]) is virtual._missing
    assert virtual.lookup(["sh", "-c", "rm -rf /"]) is virtual._missing
    assert virtual.lookup(["python", "-m", "dropwheel", "daemon"]) is None
    for script in ("command -v hyprpicker >/dev/null 2>&1",
                   "command -v zenity >/dev/null 2>&1 && echo zenity; command -v kdialog >/dev/null 2>&1"):
        assert script in texts
        assert virtual.lookup(["sh", "-c", script]) not in (None, virtual._missing)


def test_zenity_args():
    o = virtual.zenity_args(["zenity", "--file-selection", "--title=Pick", "--filename=/C:/Users/x/",
                             "--file-filter=Images | *.png *.jpg"])
    assert o == {"title": "Pick", "directory": False, "start": "C:/Users/x/", "filter": "Images (*.png *.jpg)"}
    assert virtual.zenity_args(["zenity", "--file-selection", "--directory"])["directory"] is True


# ================================================================================= explorer

def test_explorer_command_and_paths(monkeypatch):
    monkeypatch.setenv("APPDATA", "/appdata")
    cmd = explorer.open_command()
    assert cmd.endswith(' -P -m dropwheel.qtshell.open "%1"')
    paths = explorer.shortcuts()
    assert paths["send_to"].parts[-2:] == ("SendTo", "Dropwheel.lnk")
    assert paths["startup"].parts[-3:] == ("Programs", "Startup", "Dropwheel.lnk")
    assert all(k.startswith("Software\\Classes\\") and k.endswith("\\shell\\Dropwheel") for k in explorer.REG_KEYS)


def test_explorer_dry_run_changes_nothing(tmp_path):
    said = []
    assert explorer.install("en", dry=True, say=lambda key, **fmt: said.append(key)) == 0
    assert "cli.setup.win_would_menu" in said
    assert not explorer.home().exists()


def test_font_pin_is_a_fixed_commit():
    assert "/master/" not in explorer.FONT_URL and len(explorer.FONT_SHA256) == 64


# ===================================================================================== host

def _have_pyside() -> bool:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def host(tmp_path):
    if not _have_pyside():
        pytest.skip("PySide6 is not installed")
    env = dict(os.environ)
    env.update(QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT / "src"), DROPWHEEL_DATA=str(ROOT))
    proc = subprocess.Popen([sys.executable, "-P", "-m", "dropwheel.qtshell"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 60
        while not control.ping(timeout=1.0):
            assert proc.poll() is None, control.log_path().read_text(encoding="utf-8", errors="replace")
            assert time.monotonic() < deadline, "the host did not start"
            time.sleep(0.3)
        yield proc
    finally:
        if proc.poll() is None:
            control.stop(timeout=10)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def _warnings() -> list[str]:
    text = control.log_path().read_text(encoding="utf-8", errors="replace")
    return [line for line in text.splitlines() if " WARNING " in line or " ERROR " in line or " CRITICAL " in line]


def test_host_loads_the_shell_cleanly(host, tmp_path):
    img = tmp_path / "a.png"
    from PIL import Image
    Image.new("RGB", (40, 30), "red").save(img)
    assert control.request("ping") == "pong"
    assert control.request("open", [json.dumps({"files": [str(img)], "x": 100, "y": 100})]) is None
    assert control.request("open", [json.dumps({"files": [str(img)], "merge": True})]) is None
    assert control.request("setHotzone", ["false"]) is None
    assert control.request("close") is None
    time.sleep(2.0)  # the merged open fires after 400 ms; the menu comes from the daemon
    assert control.request("ping") == "pong"
    assert _warnings() == []


def test_host_rejects_unknown_calls_and_tokens(host):
    from dropwheel.errors import UserError
    with pytest.raises(UserError):
        control.request("toString")
    state = json.loads(control.state_path().read_text(encoding="utf-8"))
    import socket
    with socket.create_connection(("127.0.0.1", state["port"]), timeout=5) as sock:
        sock.sendall(b'{"token": "nope", "fn": "ping", "args": []}\n')
        assert sock.recv(100) == b""  # dropped without a reply


# ==================================================================================== GNOME

from dropwheel.qtshell import gnome, gnome_setup  # noqa: E402


@pytest.mark.parametrize(("text", "accel"), [
    ("Ctrl+Alt+D", "<Control><Alt>d"),
    ("win+shift+f12", "<Super><Shift>F12"),
    ("Super+Space", "<Super>Space"),
    ("F9", "F9"),
    ("D", ""), ("Ctrl+", ""), ("Hyper+D", ""), ("", ""),
])
def test_gnome_accelerator(text, accel):
    assert gnome_setup.accelerator(text) == accel


def test_gsettings_string_lists():
    assert gnome_setup.parse_strv("@as []") == []
    assert gnome_setup.parse_strv("['a@b.c', 'x']\n") == ["a@b.c", "x"]
    assert gnome_setup.parse_strv("garbage") == []
    assert gnome_setup.parse_strv(gnome_setup._strv(["it's", "b"])) == ["it's", "b"]


def test_gnome_backend_without_the_extension(monkeypatch):
    monkeypatch.setattr(gnome, "ACTIVE", False)
    assert gnome.pointer() is None and gnome.cursor() is None
    assert not gnome.key_down(gnome.VK_SHIFT) and not gnome.primary_button_down()
    monkeypatch.setattr(gnome, "ACTIVE", True)
    monkeypatch.setattr(gnome, "_call", lambda: (10, 20, gnome.SHIFT_MASK | gnome.BUTTON1_MASK))
    gnome._state.update(at=0.0, value=None, failed_at=0.0)
    assert gnome.cursor() == (10, 20)
    assert gnome.key_down(gnome.VK_SHIFT) and gnome.primary_button_down() and gnome.any_button_down()


def test_the_extension_matches_the_backend():
    ext = ROOT / "integrations" / "gnome" / gnome.EXTENSION_UUID
    meta = json.loads((ext / "metadata.json").read_text(encoding="utf-8"))
    assert meta["uuid"] == gnome.EXTENSION_UUID
    assert all(int(v) >= 45 for v in meta["shell-version"])  # ES modules: GNOME 45 or later
    js = (ext / "extension.js").read_text(encoding="utf-8")
    for name in (gnome.SERVICE, gnome.PATH, gnome.INTERFACE, "GetPointer", "global.get_pointer()"):
        assert name in js


@pytest.mark.parametrize(("env", "expected"), [
    ({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, True),
    ({"XDG_CURRENT_DESKTOP": "GNOME", "HYPRLAND_INSTANCE_SIGNATURE": "x"}, False),
    ({"XDG_CURRENT_DESKTOP": "Hyprland"}, False),
    ({"XDG_CURRENT_DESKTOP": "KDE", "DROPWHEEL_FRONTEND": "qt"}, True),
    ({"XDG_CURRENT_DESKTOP": "GNOME", "DROPWHEEL_FRONTEND": "quickshell"}, False),
])
def test_qt_frontend_follows_the_session(monkeypatch, env, expected):
    if tools.WINDOWS:
        pytest.skip("always the Qt host on Windows")
    for var in ("XDG_CURRENT_DESKTOP", "HYPRLAND_INSTANCE_SIGNATURE", "DROPWHEEL_FRONTEND"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert tools.qt_frontend() is expected


def test_debian_install_hints(monkeypatch):
    monkeypatch.setattr(tools, "distro", lambda: "debian")
    hint = tools.install_hint(["exiftool", "pdftoppm", "py:pymupdf", "hyprctl"])
    assert hint.startswith("sudo apt install libimage-exiftool-perl poppler-utils hyprland")
    assert "pip install" in hint and "pymupdf" in hint
    assert tools.install_hint(["caelestia"]) == ""  # not packaged for Debian


def test_setup_gnome_dry_run_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("DROPWHEEL_DATA", str(ROOT))
    said = []
    assert gnome_setup.install("Ctrl+Alt+D", dry=True, say=lambda key, **fmt: said.append(key)) == 0
    assert "cli.setup.gnome_would_shortcut" in said
    assert not (tmp_path / "data").exists() and not (tmp_path / "config").exists()


# ============================================================================== install.ps1

def test_install_ps1_is_ascii_and_parses():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert text.isascii(), "Windows PowerShell 5.1 reads a file without BOM in the ANSI code page"
    for needle in ("Haxurus", "https://github.com/haxurus/dropwheel", "setup windows", "Choose", "Ask"):
        assert needle in text
    import shutil
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh not installed")
    script = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
              f"'{ROOT / 'install.ps1'}', [ref]$null, [ref]$e); @($e).Count")
    res = subprocess.run([pwsh, "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60)
    assert res.stdout.strip() == "0", res.stdout + res.stderr
