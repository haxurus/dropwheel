"""macOS code paths, exercised on Linux by switching ``tools.MACOS`` on (or ``sys.platform`` in a
child interpreter, for what is decided at import time).

These check the branching, the arguments handed to macOS programs and the files ``dropwheel
setup macos`` writes; they do not prove that macOS accepts them (that needs a real Mac).
"""

from __future__ import annotations

import ctypes
import importlib
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from dropwheel import registry, tools, util
from dropwheel.errors import UserError

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(tools, "MACOS", True)
    monkeypatch.setattr(tools, "WINDOWS", False)
    tools.refresh()
    yield
    tools.refresh()


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _done(stdout: str = "ok", code: int = 0):
    return lambda argv, **kw: subprocess.CompletedProcess(argv, code, stdout, "")


# ===================================================================================== engine

def test_dirs_follow_library(mac, home, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    support = home / "Library" / "Application Support"
    assert util.config_dir() == support / "dropwheel"
    assert util.state_dir() == support / "dropwheel"
    assert util.xdg_data_home() / "dropwheel" == support / "dropwheel"
    assert util.cache_dir() == home / "Library" / "Caches" / "dropwheel"
    assert home / "Library" / "Caches" not in util.state_dir().parents  # undo backups are not volatile


def test_install_hint_uses_brew(mac):
    hint = tools.install_hint(["ffmpeg", "ffprobe", "tesseract", "soffice", "py:pymupdf", "swww"])
    python = sys.executable if sys.prefix != sys.base_prefix else "python3"
    assert hint == ("brew install ffmpeg tesseract tesseract-lang && brew install --cask libreoffice"
                    f" && {python} -m pip install pymupdf && brew search swww")
    assert tools.install_hint(["7z", "gs", "qrencode", "pdftoppm"]) == "brew install sevenzip ghostscript qrencode poppler"
    assert "pacman" not in tools.install_hint(["exiftool", "magick", "yt-dlp", "qpdf", "pandoc"])
    assert tools.install_hint(["rembg"]) == "pipx install 'rembg[cpu,cli]'"
    assert tools.install_hint(["gpg", "woff2_compress", "ebook-meta"]) == \
        "brew install gnupg woff2 && brew install --cask calibre"


def test_extend_path_adds_homebrew_once(mac, monkeypatch):
    """A LaunchAgent gets launchd's PATH: the tools Dropwheel starts must find ffmpeg, gs, tesseract."""
    monkeypatch.setattr(tools, "EXTRA_DIRS", ("/opt/homebrew/bin", "/usr/bin"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    tools.extend_path()
    tools.extend_path()
    assert os.environ["PATH"] == "/usr/bin:/bin:/opt/homebrew/bin"


def test_extend_path_leaves_other_systems_alone(monkeypatch):
    monkeypatch.setattr(tools, "MACOS", False)
    monkeypatch.setenv("PATH", "/usr/bin")
    tools.extend_path()
    assert os.environ["PATH"] == "/usr/bin"


def test_stop_share_never_signals_a_pid_on_macos(home, monkeypatch):
    """No /proc to tell a reused pid apart, and the state survives a reboot: remove the state file
    (the server watches it) instead of sending a signal to whatever owns the pid now."""
    from dropwheel import share

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(share, "shares_dir", lambda: home)
    (home / "abcd.json").write_text(json.dumps({"pid": 4242}))
    monkeypatch.setattr(share, "_pid_alive", lambda pid: (home / "abcd.json").exists())
    monkeypatch.setattr(share, "_is_our_server", lambda pid, sid: True)
    kills = []
    monkeypatch.setattr(share.os, "kill", lambda *a: kills.append(a))
    assert share.stop_share("abcd", wait=0.5) is True
    assert kills == [] and not (home / "abcd.json").exists()


def test_linux_names_map_to_macos_programs(mac, monkeypatch):
    monkeypatch.setattr(tools, "_which", lambda b: f"/usr/bin/{b}")
    tools.refresh()
    assert tools.path("wl-copy") == "/usr/bin/pbcopy"
    assert tools.path("wl-paste") == "/usr/bin/pbpaste"
    assert tools.path("xdg-open") == "/usr/bin/open"


def test_clipboard_text_uses_pbcopy_in_utf8(mac, monkeypatch):
    seen = []
    monkeypatch.setattr(util, "run_quiet", lambda argv, **kw: (seen.append((argv, kw)),
                                                               subprocess.CompletedProcess(argv, 0, "è", ""))[1])
    assert util.copy_text("perché")
    argv, kw = seen[-1]
    assert argv == ["pbcopy"] and kw["input"] == "perché".encode() and kw["env"]["LANG"].endswith("UTF-8")
    assert util.paste_text() == "è"
    assert seen[-1][0] == ["pbpaste"]


def test_clipboard_files_are_arguments_not_script(mac, monkeypatch):
    seen = []
    monkeypatch.setattr(util, "run_quiet", lambda argv, **kw: (seen.append(argv),
                                                               subprocess.CompletedProcess(argv, 0, "ok\n", ""))[1])
    assert util.copy_files(["/a b/c'.png", "/d;e/f.pdf"])
    argv = seen[-1]
    assert argv[:4] == ["osascript", "-l", "JavaScript", "-e"]
    assert "writeObjects" in argv[4] and "c'.png" not in argv[4]
    assert argv[5:] == ["/a b/c'.png", "/d;e/f.pdf"]
    monkeypatch.setattr(util, "run_quiet", _done("/x/1.png\n/y/2 b.pdf\n"))
    assert util.read_clipboard_files() == ["/x/1.png", "/y/2 b.pdf"]
    monkeypatch.setattr(util, "run_quiet", _done("no"))
    assert util.copy_files(["/a"]) is False


def test_trash_uses_the_finder_trash_and_never_deletes(mac, monkeypatch, tmp_path):
    victim = tmp_path / "gone.txt"
    victim.write_text("x")
    seen = []

    def ok(argv, **kw):
        seen.append(argv)
        victim.unlink()
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(util, "run_quiet", ok)
    assert util.trash(victim) is True
    assert "trashItemAtURL" in seen[0][4] and seen[0][5] == str(victim)

    keep = tmp_path / "keep.txt"
    keep.write_text("x")
    monkeypatch.setattr(util, "run_quiet", _done("no", 0))
    with pytest.raises(UserError):
        util.trash(keep)
    assert keep.exists()
    assert not (util.xdg_data_home() / "Trash").exists()  # no freedesktop fallback


def test_open_reveal_and_terminal(mac, monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(util, "detach", lambda argv, **kw: started.append(list(argv)) or True)
    util.open_path(tmp_path / "a.pdf")
    util.reveal_path([tmp_path / "a.pdf", tmp_path / "b.pdf"])
    util.launch_terminal(tmp_path)
    util.launch_terminal(tmp_path, preferred={"apps": {"terminal": "iTerm"}})
    assert started == [["open", str(tmp_path / "a.pdf")],
                       ["open", "-R", str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")],
                       ["open", "-a", "Terminal", str(tmp_path)],
                       ["open", "-a", "iTerm", str(tmp_path)]]
    with pytest.raises(UserError):
        util.launch_terminal(tmp_path, ["tool", "a;b.png"])
    assert len(started) == 4


def test_macos_hidden_ids_exist_and_cannot_run(monkeypatch):
    from dropwheel import actions

    actions.load_all({})
    known = {a.id for a in registry.all_actions()}
    if not known:
        pytest.skip("the registry was emptied by an earlier test")
    assert set(registry._MACOS_HIDDEN) <= known
    monkeypatch.setattr(registry, "PLATFORM_HIDDEN", registry._MACOS_HIDDEN)
    assert registry.get("image.wallpaper") is None
    assert registry.get("font.install") is not None  # ~/Library/Fonts, no fontconfig needed


#: stdlib modules that branch on darwin at import time (urllib, ctypes.util): loaded first
_PRELUDE = "import sys, json, ctypes.util, mimetypes, platform, urllib.request, webbrowser; sys.platform = 'darwin'\n"

_CHILD = _PRELUDE + """
from dropwheel import actions, cli, registry, tools
from dropwheel.actions import fonts
print(json.dumps({"macos": tools.MACOS, "qt": tools.qt_frontend(), "modules": list(actions.MODULES),
                  "hidden": list(registry.PLATFORM_HIDDEN), "targets": list(cli._SETUP_TARGETS),
                  "required": [n for n, _ in cli.REQUIRED_TOOLS], "fonts": str(fonts.user_font_dir()),
                  "font_requires": fonts._FONT_TOOLS}))
"""


def _child(code: str, home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(ROOT / "src"), "DROPWHEEL_DATA": str(ROOT)}
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, env=env,
                          timeout=60, cwd=str(home))


def test_import_time_choices_on_darwin(home):
    res = _child(_CHILD, home)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["macos"] and data["qt"]
    assert "system" not in data["modules"]
    assert data["hidden"] == list(registry._MACOS_HIDDEN)
    assert data["targets"] == ["macos"]
    assert data["required"] == ["py:PIL", "py:PySide6"]
    assert data["fonts"] == str(home / "Library" / "Fonts") and data["font_requires"] == []


def test_cli_setup_macos_dry_run(home):
    code = _PRELUDE + "from dropwheel import cli; sys.exit(cli.main(sys.argv[1:]))"
    res = _child(code, home, "--lang", "en", "setup", "macos", "--dry-run")
    assert res.returncode == 0, res.stderr
    assert "Open in Dropwheel.workflow" in res.stdout
    assert "io.github.haxurus.dropwheel.plist" in res.stdout
    assert "no wheel shortcut" in res.stdout
    assert not (home / "Library").exists()  # a dry run writes nothing


# ====================================================================================== setup

def test_setup_writes_valid_bundles_and_removes_them(home, monkeypatch):
    from dropwheel.qtshell import explorer, macos_setup

    monkeypatch.setattr(explorer, "download_font", lambda: explorer.font_path())
    said = []
    assert macos_setup.install("it", dry=False, say=lambda key, **fmt: said.append(key)) == 0
    contents = home / "Library" / "Services" / "Open in Dropwheel.workflow" / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    service = info["NSServices"][0]
    assert service["NSMessage"] == "runWorkflowAsService" and service["NSMenuItem"]["default"] == "Apri in Dropwheel"
    assert service["NSSendFileTypes"] == ["public.item"]
    flow = plistlib.loads((contents / "document.wflow").read_bytes())
    params = flow["actions"][0]["action"]["ActionParameters"]
    assert params["inputMethod"] == 1 and params["shell"] == "/bin/sh"  # the selection arrives as "$@"
    assert params["COMMAND_STRING"].startswith("exec ") and params["COMMAND_STRING"].rstrip().endswith(' open "$@"')
    assert flow["workflowMetaData"]["workflowTypeIdentifier"] == "com.apple.Automator.servicesMenu"
    agent = plistlib.loads((home / "Library" / "LaunchAgents" / "io.github.haxurus.dropwheel.plist").read_bytes())
    assert agent["Label"] == "io.github.haxurus.dropwheel" and agent["RunAtLoad"] is True
    assert agent["ProgramArguments"][1:] == ["-P", "-m", "dropwheel.qtshell"]
    assert agent["EnvironmentVariables"]["PYTHONPATH"].split(os.pathsep)[0] == str(ROOT / "src")
    assert macos_setup.state() == {"service": True, "login": True, "font": False}
    assert "cli.setup.mac_service" in said and "cli.setup.mac_login" in said

    macos_setup.remove(dry=False, say=lambda key, **fmt: None)
    assert not (home / "Library" / "Services" / "Open in Dropwheel.workflow").exists()
    assert macos_setup.state()["login"] is False


# ==================================================================================== backend

@pytest.fixture
def quartz(monkeypatch):
    """:mod:`dropwheel.qtshell.macos` imported as on a Mac, with the frameworks replaced by mocks."""
    libs: dict[str, mock.MagicMock] = {}

    def cdll(path, *a, **kw):
        return libs.setdefault(os.path.basename(path), mock.MagicMock(name=path))

    from dropwheel.qtshell import macos

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", cdll)
    importlib.reload(macos)
    yield SimpleNamespace(mod=macos, cg=libs["CoreGraphics"], cf=libs["CoreFoundation"])
    monkeypatch.undo()
    importlib.reload(macos)


def test_backend_reads_shift_buttons_and_cursor(quartz):
    m, cg = quartz.mod, quartz.cg
    assert m.MACOS
    cg.CGEventSourceFlagsState.return_value = m.FLAG_SHIFT | 0x100
    assert m.key_down(m.VK_SHIFT) is True
    assert m.key_down(0x41) is False
    cg.CGEventSourceFlagsState.assert_called_with(m.COMBINED_SESSION_STATE)
    cg.CGEventSourceButtonState.side_effect = lambda state, button: button == m.BUTTON_RIGHT
    assert m.primary_button_down() is False and m.any_button_down() is True
    cg.CGEventCreate.return_value = 0x1234
    cg.CGEventGetLocation.return_value = m.CGPoint(1439.6, 12.2)
    assert m.cursor() == (1440, 12)
    quartz.cf.CFRelease.assert_called_with(0x1234)
    assert m.register_hotkey(1, 0, 0) is False


def test_backend_window_style_follows_the_live_window_state(quartz, monkeypatch):
    m = quartz.mod
    calls = []
    window = {"ignoresMouseEvents": False, "level": 0}

    def msg(obj, sel, *args, **kw):
        calls.append((sel, args))
        if sel == "window":
            return 0xBEEF
        if sel in window:
            return window[sel]
        if sel == "setIgnoresMouseEvents:":
            window["ignoresMouseEvents"] = args[0]
        if sel == "setLevel:":
            window["level"] = args[0]
        return None

    monkeypatch.setattr(m, "_msg", msg)
    m.set_window_style(0x10, click_through=True, no_activate=True)
    assert [c[0] for c in calls if c[0].startswith("set")] == [
        "setIgnoresMouseEvents:", "setLevel:", "setCollectionBehavior:", "setHidesOnDeactivate:"]
    assert ("setCollectionBehavior:", (m.JOIN_ALL_SPACES | m.FULL_SCREEN_AUXILIARY,)) in calls
    calls.clear()
    m.set_window_style(0x10, click_through=True, no_activate=True)
    assert not [c for c in calls if c[0].startswith("set")]  # the mask timer asks every 30 ms
    window["level"] = 0  # Qt reset it, or a new window got the same NSView address
    m.set_window_style(0x10, click_through=True, no_activate=True)
    assert ("setLevel:", (m.STATUS_WINDOW_LEVEL,)) in calls
    calls.clear()
    m.set_window_style(0x10, click_through=False, no_activate=True)
    assert ("setIgnoresMouseEvents:", (False,)) in calls


def test_backend_is_inert_off_macos():
    from dropwheel.qtshell import macos

    if macos.MACOS:
        pytest.skip("macOS")
    assert macos.key_down(macos.VK_SHIFT) is False
    assert macos.cursor() is None and macos.any_button_down() is False
    macos.set_window_style(123, click_through=True, no_activate=True)  # no error
    macos.init_app()
    macos.foreground(123)


# ================================================================================ install.sh

def test_install_sh_on_macos_uses_homebrew_and_skips_the_linux_files(tmp_path):
    """install.sh with ``uname`` saying Darwin and a stand-in ``brew``: Homebrew commands, no desktop
    entry or file-manager files, the macOS summary.  (Run by this machine's bash, not macOS's 3.2.)"""
    from test_pack import BASH, Sandbox

    if BASH is None:
        pytest.skip("bash not available")
    box = Sandbox(tmp_path)
    box.add_tool("uname", "#!/bin/sh\necho Darwin\n")
    box.add_tool("sw_vers", "#!/bin/sh\necho 14.5\n")
    box.add_tool("brew", '#!/bin/sh\ncase "$1 $2" in "list --formula") echo ffmpeg ;; esac\nexit 0\n')
    result = box.install("--no-hyprland")
    out = result.stdout + result.stderr
    assert "macOS 14.5" in out
    assert "brew install --cask libreoffice" in out and "brew install imagemagick" in out
    assert re.search(r"Audio and video +yt-dlp \(pip", out)  # ffmpeg: already installed
    assert "pacman" not in out and "sudo" not in out
    assert "dropwheel setup macos" in out.split("━━")[-1] and "Preview" in out
    assert (box.home / ".local" / "bin" / "dropwheel").is_file()
    assert not (box.data / "applications" / "dropwheel.desktop").exists()
    assert not (box.data / "icons").exists()


def test_install_sh_on_macos_finds_homebrews_versioned_python(tmp_path):
    """python@3.13 installs only ``bin/python3.13``: the installer must pick it over other pythons."""
    from test_pack import BASH, ROOT as REPO, Sandbox

    if BASH is None:
        pytest.skip("bash not available")
    box = Sandbox(tmp_path)
    prefix = tmp_path / "homebrew"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "python3.13").symlink_to(sys.executable)
    box.add_tool("uname", "#!/bin/sh\necho Darwin\n")
    box.add_tool("sw_vers", "#!/bin/sh\necho 14.5\n")
    box.add_tool("brew", f'#!/bin/sh\n[ "$1" = --prefix ] && echo {prefix}\nexit 0\n')
    result = box.run(REPO / "install.sh", "--no-start", "--no-file-managers", "--no-packages", "--yes",
                     "--no-hyprland")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{prefix}/bin/python3.13 -m venv" in result.stdout
