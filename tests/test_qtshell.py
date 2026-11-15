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
    ({"XDG_CURRENT_DESKTOP": "KDE"}, True),
    ({"XDG_CURRENT_DESKTOP": "KDE", "HYPRLAND_INSTANCE_SIGNATURE": "x"}, False),
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


@pytest.mark.parametrize(("os_release", "expected"), [
    ('ID=ubuntu\nID_LIKE=debian\n', "debian"),
    ('ID=fedora\n', "fedora"),
    ('ID="rocky"\nID_LIKE="rhel centos fedora"\n', "fedora"),
    ('ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n', "suse"),
    ('ID="opensuse-leap"\nID_LIKE="suse opensuse"\n', "suse"),
    ('ID=arch\n', "arch"),
])
def test_distro_reads_os_release(tmp_path, monkeypatch, os_release, expected):
    path = tmp_path / "os-release"
    path.write_text(os_release, encoding="utf-8")
    monkeypatch.setenv("DROPWHEEL_OS_RELEASE", str(path))
    tools.distro.cache_clear()
    try:
        assert tools.distro() == expected
    finally:
        tools.distro.cache_clear()


def test_fedora_install_hints(monkeypatch):
    monkeypatch.setattr(tools, "distro", lambda: "fedora")
    hint = tools.install_hint(["exiftool", "pdftoppm", "ffmpeg", "py:PySide6", "py:pymupdf"])
    assert hint.startswith("sudo dnf install perl-Image-ExifTool poppler-utils ffmpeg-free python3-pyside6 && ")
    assert "pip install" in hint and "pymupdf" in hint
    assert tools.install_hint(["caelestia"]) == tools.install_hint(["hyprctl"]) == ""


def test_suse_install_hints(monkeypatch):
    monkeypatch.setattr(tools, "distro", lambda: "suse")
    hint = tools.install_hint(["exiftool", "pdftoppm", "py:PIL", "py:PySide6", "py:pymupdf"])
    assert hint.startswith("sudo zypper install exiftool poppler-tools python3-Pillow "
                           "python3-pyside6 qt6-declarative-imports && ")
    assert "pip install" in hint and "pymupdf" in hint
    assert tools.install_hint(["mat2"]) == ""  # not packaged for openSUSE


def test_setup_gnome_dry_run_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("DROPWHEEL_DATA", str(ROOT))
    said = []
    assert gnome_setup.install("Ctrl+Alt+D", dry=True, say=lambda key, **fmt: said.append(key)) == 0
    assert "cli.setup.gnome_would_shortcut" in said
    assert not (tmp_path / "data").exists() and not (tmp_path / "config").exists()


# ================================================================================== KDE Plasma

from dropwheel.qtshell import kde, kde_setup  # noqa: E402


@pytest.mark.parametrize(("text", "seq"), [
    ("Ctrl+Alt+D", "Ctrl+Alt+D"),
    ("win+shift+f12", "Meta+Shift+F12"),
    ("ctrl+ctrl+pagedown", "Ctrl+PgDown"),
    ("Super+Space", "Meta+Space"),
    ("F9", "F9"),
    ("D", ""), ("Ctrl+", ""), ("Hyper+D", ""), ("", ""),
])
def test_kde_shortcut(text, seq):
    assert kde_setup.shortcut(text) == seq


def test_kde_backend(monkeypatch):
    monkeypatch.setattr(kde.gnome, "ACTIVE", False)
    assert kde.mods() == 0 and kde.cursor() is None
    assert not kde.key_down(kde.VK_SHIFT) and not kde.primary_button_down()
    monkeypatch.setattr(kde.gnome, "ACTIVE", True)
    monkeypatch.setattr(kde, "_x", lambda: {})
    monkeypatch.setattr(kde, "_query", lambda x, now: kde.SHIFT_MASK | (1 << 4))  # Shift and Num Lock
    monkeypatch.setitem(kde._state, "at", 0.0)
    monkeypatch.setitem(kde._state, "drag_at", -kde.DRAG_S)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert kde.key_down(kde.VK_SHIFT) and not kde.primary_button_down() and not kde.any_button_down()
    assert kde.cursor() is None  # X11: Qt's own cursor is right
    # Wayland: a drag started a moment ago (XFixes) counts as button 1 for DRAG_S seconds
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    now = time.monotonic()
    kde._state["drag_at"] = now - 1.0
    assert kde.mods(now) & kde.BUTTON1_MASK
    assert not kde.mods(now + kde.DRAG_S) & kde.BUTTON1_MASK
    monkeypatch.setitem(kde._state, "pushed", (5, 6))
    assert kde.cursor() == (5, 6)


def test_kde_pointer_is_scaled_to_qt():
    if not _have_pyside():
        pytest.skip("PySide6 is not installed")
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    g = app.primaryScreen().virtualGeometry()
    before = kde._state["pushed"]
    try:
        # KWin's logical pixels at twice the size of the X11 screen: halved
        kde.set_pointer(100, 50, g.width() * 2, g.height() * 2)
        assert kde._state["pushed"] == (g.x() + 50, g.y() + 25)
        kde.set_pointer(1, 1, 0, 0)  # ignored
        assert kde._state["pushed"] == (g.x() + 50, g.y() + 25)
    finally:
        kde._state["pushed"] = before


@pytest.mark.parametrize("d", [1, 1.5, 2])
def test_kde_pointer_with_two_scaled_screens(d):
    # two 1920x1080 monitors side by side; Qt's xcb keeps each origin in X11 pixels, sizes divided by d
    w, h = round(1920 / d), round(1080 / d)
    rects = [(0, 0, w, h, d), (1920, 0, w, h, d)]
    assert kde.to_qt(10, 20, 2 * w, h, rects) == (10, 20)
    # 40 logical pixels into the second monitor: X11 pixel 1920 + 40 * d, Qt's 1920 + 40
    assert kde.to_qt(w + 40, 100, 2 * w, h, rects) == (1960, 100)
    assert kde.to_qt(1, 1, 0, 0, rects) is None and kde.to_qt(1, 1, 10, 10, []) is None


def test_kde_shift_state_when_kwinrc_has_no_setting(monkeypatch):
    values = {}
    monkeypatch.setattr(kde_setup, "_kwinrc", lambda group, key, value=None, **kw: values.get(key))
    monkeypatch.setattr(kde_setup, "_dbus", lambda *a: None)
    assert kde_setup.state()["shift"] is None  # KWin's default changed in Plasma 6.2
    values["XwaylandEavesdrops"] = "None\n"
    assert kde_setup.state()["shift"] is False
    values["XwaylandEavesdrops"] = "Modifiers"
    assert kde_setup.state()["shift"] is True


def test_setup_kde_reloads_a_running_script(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("DROPWHEEL_DATA", str(ROOT))
    calls, rc = [], {"Shortcut": "Ctrl+Alt+D"}

    def kwinrc(group, key, value=None, *, delete=False):
        calls.append(("kwinrc", key, value, delete))
        return rc.get(key, "") if value is None and not delete else ""

    monkeypatch.setattr(kde_setup, "_kwinrc", kwinrc)
    monkeypatch.setattr(kde_setup, "_dbus", lambda dest, path, method, *a: calls.append(method) or "()")
    monkeypatch.setattr(kde_setup.gnome_setup, "write_autostart", lambda cmd, say: True)
    monkeypatch.setattr(kde_setup.gnome_setup, "install_font", lambda say: True)
    say = lambda key, **fmt: None  # noqa: E731
    assert kde_setup.install("ctrl+alt+d", dry=False, say=say) == 0
    methods = [c for c in calls if isinstance(c, str)]
    # same shortcut: unloaded (reconfigure alone keeps the old copy) and started again, shortcut kept
    assert methods == ["org.kde.kwin.Scripting.unloadScript", "org.kde.KWin.reconfigure"]
    calls.clear()
    assert kde_setup.install("ctrl+alt+e", dry=False, say=say) == 0
    assert "org.kde.KGlobalAccel.unregister" in calls  # or kglobalaccel keeps the old key
    calls.clear()
    assert kde_setup.remove(dry=False, say=say) == 0
    methods = [c for c in calls if isinstance(c, str)]
    assert methods[0] == "org.kde.kwin.Scripting.unloadScript" and not kde_setup.script_dir().exists()


def test_the_kwin_script_matches_the_backend():
    pkg = ROOT / "integrations" / "kde" / kde.SCRIPT_ID
    meta = json.loads((pkg / "metadata.json").read_text(encoding="utf-8"))
    assert meta["KPlugin"]["Id"] == kde.SCRIPT_ID  # also the folder name and the kwinrc key
    assert meta["X-Plasma-API"] == "javascript" and meta["KPackageStructure"] == "KWin/Script"
    js = (pkg / "contents" / meta["X-Plasma-MainScript"]).read_text(encoding="utf-8")
    for name in (kde.SERVICE, kde.PATH, kde.INTERFACE, '"SetPointer"', '"Open"', "workspace.cursorPos",
                 'readConfig("Shortcut"'):
        assert name in js
    import shutil
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    # parse only: KWin's globals (workspace, callDBus, QTimer…) do not exist outside KWin
    res = subprocess.run([node, "-e", "new Function(require('fs').readFileSync(process.argv[1], 'utf8'))",
                          str(pkg / "contents" / "code" / "main.js")], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr


def test_kde_session_picks_the_kde_backend():
    code = "from dropwheel.qtshell import native, kde; print(native.plat is kde)"
    env = dict(os.environ, XDG_CURRENT_DESKTOP="KDE", QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT / "src"))
    if not _have_pyside():
        pytest.skip("PySide6 is not installed")
    res = subprocess.run([sys.executable, "-P", "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert res.stdout.strip() == ("False" if win32.WINDOWS or tools.MACOS else "True"), res.stderr


def test_setup_kde_dry_run_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("DROPWHEEL_DATA", str(ROOT))
    said = []
    assert kde_setup.install("Ctrl+Alt+D", dry=True, say=lambda key, **fmt: said.append((key, fmt))) == 0
    assert ("cli.setup.kde_would_shortcut", {"accel": "Ctrl+Alt+D"}) in said
    assert any(str(kde_setup.script_dir()) == fmt.get("path") for _, fmt in said)
    assert kde_setup.remove(dry=True, say=lambda key, **fmt: said.append((key, fmt))) == 0
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
