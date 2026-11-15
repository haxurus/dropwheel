"""Windows code paths, exercised on Linux by switching ``tools.WINDOWS`` on.

These check the branching and the arguments handed to Windows programs; they do not prove the
programs behave as expected (that needs a real Windows run).
"""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

from dropwheel import registry, tools, util
from dropwheel.errors import UserError


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(tools, "WINDOWS", True)
    for name, value in (("CREATE_NEW_PROCESS_GROUP", 0x200), ("CREATE_NO_WINDOW", 0x8000000),
                        ("DETACHED_PROCESS", 0x8), ("CREATE_NEW_CONSOLE", 0x10)):
        monkeypatch.setattr(subprocess, name, value, raising=False)
    tools.refresh()
    yield
    tools.refresh()


def test_split_command_keeps_windows_paths(win):
    assert util.split_command(r'"C:\Program Files\Foo\foo.exe" --x "a b"') == [
        r"C:\Program Files\Foo\foo.exe", "--x", "a b"]
    assert util.split_command(r"C:\tools\bar.exe -y") == [r"C:\tools\bar.exe", "-y"]
    assert util.split_command('code --opt="a b" ""') == ["code", "--opt=a b", ""]


def test_split_command_posix_unchanged():
    assert util.split_command("foo 'a b' c\\ d") == ["foo", "a b", "c d"]


def test_dirs_follow_appdata(win, monkeypatch, tmp_path):
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert util.config_dir() == tmp_path / "Roaming" / "dropwheel"
    assert util.state_dir() == tmp_path / "Local" / "dropwheel"
    assert util.cache_dir() == tmp_path / "Local" / "Temp" / "dropwheel"
    # the cache is a volatile root: it must never contain the state (undo backups) or the data
    assert tmp_path / "Local" / "Temp" not in util.state_dir().parents


def test_install_hint_uses_winget(win):
    hint = tools.install_hint(["ffmpeg", "ffprobe", "py:pymupdf", "gifsicle"])
    assert hint == ("winget install --id Gyan.FFmpeg -e; python -m pip install pymupdf"
                    "; winget search gifsicle")
    assert tools.install_hint(["py:docx", "py:yaml", "py:cv2"]) == "python -m pip install python-docx PyYAML opencv-python"
    assert tools.install_hint(["gs"]) == "winget search Ghostscript"
    assert "pacman" not in tools.install_hint(["gs", "soffice", "7z"])


def test_convert_never_resolves_to_system32(win, monkeypatch):
    monkeypatch.setattr(tools, "_which", lambda b: r"C:\Windows\System32\%s.exe" % b)
    tools.refresh()
    assert tools.path("convert") is None
    assert tools.path("wl-copy").endswith("powershell.exe")
    assert tools.path("xdg-open").endswith("explorer.exe")


def test_trash_requires_send2trash(win, monkeypatch, tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("x")
    monkeypatch.setitem(sys.modules, "send2trash", None)  # import fails
    with pytest.raises(UserError):
        util.trash(victim)
    assert victim.exists()  # never moved to a hidden freedesktop folder
    assert not (util.xdg_data_home() / "Trash").exists()


def test_trash_uses_send2trash(win, monkeypatch, tmp_path):
    victim = tmp_path / "gone.txt"
    victim.write_text("x")
    calls = []
    fake = types.ModuleType("send2trash")
    fake.send2trash = lambda p: (calls.append(p), victim.unlink())
    monkeypatch.setitem(sys.modules, "send2trash", fake)
    monkeypatch.setattr(util, "_nt_has_recycle_bin", lambda p: True)
    assert util.trash(victim) is True
    assert calls == [str(victim)]


def test_clipboard_files_go_through_stdin(win, monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["input"] = argv, kw.get("input")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(util, "run_quiet", fake_run)
    assert util.copy_files([r"C:\a b\c.png", r"C:\d;e\f.pdf"])
    assert seen["argv"][0] == "powershell" and "Set-Clipboard -LiteralPath" in seen["argv"][-1]
    assert "c.png" not in seen["argv"][-1]  # paths never on the command line
    assert seen["input"].splitlines()[1].endswith("f.pdf")


def test_kill_tree_uses_taskkill(win, monkeypatch):
    ran = []
    monkeypatch.setattr(tools, "path", lambda n: r"C:\Windows\System32\taskkill.exe" if n == "taskkill" else None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: ran.append(argv))
    proc = types.SimpleNamespace(pid=42, poll=lambda: None if not ran else 1, kill=lambda: None)
    util.kill_tree(proc)
    assert ran == [[r"C:\Windows\System32\taskkill.exe", "/T", "/F", "/PID", "42"]]


def test_terminal_opens_console_but_refuses_commands(win, monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(tools, "path", lambda n: r"C:\Windows\System32\cmd.exe" if n == "cmd" else None)
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: started.update(argv=argv, **kw))
    util.launch_terminal(tmp_path)
    assert started["argv"] == [r"C:\Windows\System32\cmd.exe"] and started["cwd"] == str(tmp_path)
    started.clear()
    with pytest.raises(UserError):
        util.launch_terminal(tmp_path, ["tool", "a&b.png"])  # cmd.exe would run "b.png"
    assert not started


def test_windows_hidden_ids_exist():
    from dropwheel import actions

    actions.load_all({})
    known = {a.id for a in registry.all_actions()}
    assert set(registry._WINDOWS_HIDDEN) <= known


def test_trash_refuses_drives_without_recycle_bin(win, monkeypatch, tmp_path):
    victim = tmp_path / "net.txt"
    victim.write_text("x")
    fake = types.ModuleType("send2trash")
    fake.send2trash = lambda p: victim.unlink()  # what the shell does there: delete for good
    monkeypatch.setitem(sys.modules, "send2trash", fake)
    monkeypatch.setattr(util, "_nt_has_recycle_bin", lambda p: False)
    with pytest.raises(UserError):
        util.trash(victim)
    assert victim.exists()


def test_lookup_ignores_current_folder_and_cmd_shims(win, monkeypatch, tmp_path):
    here = tmp_path / "work"
    bindir = tmp_path / "bin"
    here.mkdir()
    bindir.mkdir()
    for f in (here / "magick.exe", bindir / "svgo.cmd", bindir / "ffmpeg.exe"):
        f.write_text("")
        f.chmod(0o755)
    monkeypatch.chdir(here)
    monkeypatch.setenv("PATH", f".;{bindir}")
    monkeypatch.setattr(tools.os, "pathsep", ";")
    monkeypatch.setattr(tools, "EXTRA_DIRS", ())
    tools.refresh()
    assert tools.path("magick") is None  # not taken from the folder being worked on
    assert tools.path("svgo") is None  # .cmd runs through cmd.exe
    assert tools.path(str(bindir / "svgo.cmd")) is None
    assert tools.path("ffmpeg") == str(bindir / "ffmpeg.exe")


def test_system_programs_come_from_systemroot(win, monkeypatch, tmp_path):
    root = tmp_path / "Windows"
    (root / "System32").mkdir(parents=True)
    cmd = root / "System32" / "cmd.exe"
    cmd.write_text("")
    cmd.chmod(0o755)
    monkeypatch.setenv("SystemRoot", str(root))
    monkeypatch.setattr(tools.os.path, "join", lambda *a: "/".join(a).replace("\\", "/"))
    tools.refresh()
    assert tools.path("cmd") == str(cmd)


def test_clipboard_text_and_files_handle_crlf(win, monkeypatch):
    seen = []
    monkeypatch.setattr(util, "run_quiet", lambda argv, **kw: (seen.append((argv[-1], kw["input"])),
                                                               subprocess.CompletedProcess(argv, 0, "\ufeffx", ""))[1])
    util.copy_text("a\r\nb")
    assert seen[-1][1] == "a\nb"  # Windows text mode turns it back into one CRLF
    util.copy_files(["C:\\a.png", "C:\\b.png"])
    assert '-split "\\r?\\n"' in seen[-1][0]
    assert util.paste_text() == "x"  # BOM dropped


def test_platform_hidden_actions_cannot_run(monkeypatch):
    from dropwheel import actions

    actions.load_all({})
    monkeypatch.setattr(registry, "PLATFORM_HIDDEN", registry._WINDOWS_HIDDEN)
    assert registry.get("font.install") is None
    assert "font.install" not in {a.id for a in registry.all_actions()}
