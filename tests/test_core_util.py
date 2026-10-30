"""Tests for dropwheel.util (formatting, naming, URIs, trash, desktop entries, terminals)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dropwheel import util


@pytest.mark.parametrize("n,lang,expected", [
    (0, "en", "0 B"),
    (512, "it", "512 B"),
    (1000, "en", "1 kB"),
    (1234, "it", "1,2 kB"),
    (1234, "en", "1.2 kB"),
    (2_400_000, "it", "2,4 MB"),
    (999_999, "en", "1 MB"),
    (245_000_000, "en", "245 MB"),
    (3_500_000_000, "it", "3,5 GB"),
    (None, "en", ""),
])
def test_human_size(n, lang, expected):
    assert util.human_size(n, lang) == expected


def test_durations_and_times():
    assert util.human_duration(75) == "1:15"
    assert util.human_duration(3725) == "1:02:05"
    assert util.human_duration(None) == ""
    assert util.parse_time("90") == 90
    assert util.parse_time("1:30") == 90
    assert util.parse_time("00:01:30.5") == 90.5
    assert util.parse_time("1,5") == 1.5
    assert util.parse_time("12s") == 12
    assert util.parse_time("abc") is None
    assert util.parse_time(None) is None


@pytest.mark.parametrize("name,expected", [
    ("photo.JPG", ("photo", "jpg")),
    ("archive.tar.gz", ("archive", "tar.gz")),
    ("backup.2024.tar.zst", ("backup.2024", "tar.zst")),
    ("pkg-1.0-1-x86_64.pkg.tar.zst", ("pkg-1.0-1-x86_64", "pkg.tar.zst")),
    (".bashrc", (".bashrc", "")),
    ("README", ("README", "")),
    ("weird.", ("weird.", "")),
    ("a.b c", ("a.b c", "")),
    ("movie.part1.rar", ("movie.part1", "rar")),
])
def test_split_name(name, expected):
    assert util.split_name(name) == expected


def test_split_name_dir():
    assert util.split_name("v1.2", is_dir=True) == ("v1.2", "")


def test_safe_filename():
    assert util.safe_filename("a/b") == "a-b"
    assert util.safe_filename("line\nbreak\ttab") == "line break tab"
    assert util.safe_filename("") == "file"
    assert util.safe_filename("..") == "file"
    assert util.safe_filename('what?:*.txt', portable=True) == "what___.txt"
    long = "è" * 300 + " (compresso).jpg"
    out = util.safe_filename(long)
    assert len(out.encode("utf-8")) <= 255
    assert out.endswith(" (compresso).jpg") and "…" in out


def test_unique_path(dropwheel_env):
    d = dropwheel_env.work
    p = d / "photo.png"
    assert util.unique_path(p) == p
    p.write_text("x")
    assert util.unique_path(p) == d / "photo 2.png"
    (d / "photo 2.png").write_text("x")
    assert util.unique_path(p) == d / "photo 3.png"
    assert util.unique_path(d / "new.png", taken=[d / "new.png"]) == d / "new 2.png"
    t = d / "a.tar.gz"
    t.write_text("x")
    assert util.unique_path(t) == d / "a 2.tar.gz"
    folder = d / "v1.2"
    folder.mkdir()
    assert util.unique_path(folder, is_dir=True) == d / "v1.2 2"


def test_unique_path_stays_within_name_max(dropwheel_env):
    d = dropwheel_env.work
    full = d / ("\u65e5" * 83 + "xx.jpg")  # 255 bytes: no room for " 2"
    got = util.unique_path(full, taken=[full])
    assert len(got.name.encode("utf-8")) <= util.NAME_MAX
    assert got.name.startswith("\u65e5") and got.name.endswith(" 2.jpg")
    second = util.unique_path(full, taken=[full, got])
    assert len(second.name.encode("utf-8")) <= util.NAME_MAX and second.name.endswith(" 3.jpg")


def test_safe_filename_shortens_non_utf8_names():
    name = util.safe_filename("a" * 300 + " (\udc80).txt")
    assert len(name.encode("utf-8", "surrogateescape")) <= util.NAME_MAX and name.endswith(" (\udc80).txt")


def test_unique_path_case_insensitive(dropwheel_env):
    d = dropwheel_env.work
    assert util.unique_path(d / "Photo.PNG", taken=[d / "photo.png"], ci=True) == d / "Photo 2.PNG"


def test_natural_sort():
    items = ["file 10.txt", "file 2.txt", "File 1.txt"]
    assert util.natural_sorted(items) == ["File 1.txt", "file 2.txt", "file 10.txt"]


def test_uri_roundtrip(dropwheel_env):
    p = dropwheel_env.work / "spazi e accènti #1.txt"
    p.write_text("x")
    uri = util.path_to_uri(p)
    assert uri.startswith("file://") and " " not in uri and "#" not in uri.split("//", 1)[1]
    assert Path(util.uri_to_path(uri)) == p
    assert util.uri_to_path("/plain/path") == "/plain/path"


def test_parse_uri_list(dropwheel_env):
    p = dropwheel_env.work / "a b.txt"
    text = f"# comment\r\n{util.path_to_uri(p)}\r\nhttps://example.com/x\r\n\r\n"
    out = util.parse_uri_list(text)
    assert Path(out[0]) == p and out[1] == "https://example.com/x"
    assert util.parse_uri_list(text, keep_urls=False) == [out[0]]


def test_xdg_and_download_dir(dropwheel_env, monkeypatch):
    assert util.cache_dir() == dropwheel_env.cache / "dropwheel"
    assert util.state_dir() == dropwheel_env.state / "dropwheel"
    assert util.download_dir() == dropwheel_env.downloads
    assert util.fallback_output_dir() == dropwheel_env.downloads / "Dropwheel"
    monkeypatch.delenv("XDG_DOWNLOAD_DIR")
    custom = dropwheel_env.base / "Scaricati"
    (dropwheel_env.config / "user-dirs.dirs").write_text(f'XDG_DOWNLOAD_DIR="{custom}"\n', encoding="utf-8")
    assert util.download_dir() == custom


def test_volatile(dropwheel_env):
    assert util.is_volatile(dropwheel_env.volatile / "x.png") is True
    assert util.is_volatile(dropwheel_env.work / "x.png") is False
    assert str(dropwheel_env.volatile.resolve()) in [str(Path(r)) for r in util.volatile_roots()] or \
        os.path.realpath(dropwheel_env.volatile) in util.volatile_roots()


def test_trash_manual_freedesktop(dropwheel_env):
    f = dropwheel_env.work / "to trash.txt"
    f.write_text("bye")
    util._trash_manual(str(f))
    assert not f.exists()
    trash = dropwheel_env.data / "Trash"
    files = list((trash / "files").iterdir())
    infos = list((trash / "info").iterdir())
    assert [x.name for x in files] == ["to trash.txt"]
    info = infos[0].read_text(encoding="utf-8")
    assert info.startswith("[Trash Info]\nPath=") and "to%20trash.txt" in info and "DeletionDate=" in info
    g = dropwheel_env.work / "to trash.txt"
    g.write_text("again")
    util._trash_manual(str(g))
    assert sorted(x.name for x in (trash / "files").iterdir()) == ["to trash.2.txt", "to trash.txt"]


def test_trash_public(dropwheel_env):
    f = dropwheel_env.work / "gone.txt"
    f.write_text("x")
    assert util.trash(f) is True
    assert not f.exists()
    assert util.trash(dropwheel_env.work / "never-existed") is True


def _desktop(path: Path, **fields) -> Path:
    lines = ["[Desktop Entry]", "Type=Application"] + [f"{k}={v}" for k, v in fields.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_desktop_apps_for(dropwheel_env, monkeypatch):
    apps = dropwheel_env.data / "applications"
    apps.mkdir()
    sysdir = dropwheel_env.base / "sysdata"
    (sysdir / "applications").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_DIRS", str(sysdir))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(dropwheel_env.base / "sysconfig"))
    _desktop(apps / "viewer.desktop", Name="Viewer", **{"Name[it]": "Visualizzatore"}, Exec="viewer %F",
             MimeType="image/png;image/jpeg;", Icon="viewer")
    _desktop(apps / "editor.desktop", Name="Editor", Exec="editor %U", MimeType="image/*;")
    _desktop(apps / "hidden.desktop", Name="Hidden", Exec="hidden %f", MimeType="image/png;", NoDisplay="true")
    _desktop(apps / "missing.desktop", Name="Missing", Exec="x %f", MimeType="image/png;",
             TryExec="definitely-not-installed-dropwheel")
    _desktop(apps / "texty.desktop", Name="Texty", Exec="texty %F", MimeType="text/plain;")
    (dropwheel_env.config / "mimeapps.list").write_text("[Default Applications]\nimage/png=editor.desktop;\n",
                                                        encoding="utf-8")
    found = util.desktop_apps_for(["image/png"], lang="it")
    ids = [a["id"] for a in found]
    assert ids == ["editor.desktop", "viewer.desktop"]
    assert found[0]["default"] is True and found[1]["name"] == "Visualizzatore"
    both = [a["id"] for a in util.desktop_apps_for(["image/png", "image/jpeg"])]
    assert set(both) == {"editor.desktop", "viewer.desktop"}
    code = [a["id"] for a in util.desktop_apps_for(["text/x-python"])]
    assert code == ["texty.desktop"]
    assert util.desktop_apps_for([]) == []


def test_expand_exec(dropwheel_env):
    entry = {"Name": "App", "Icon": "app"}
    argvs = util._expand_exec('app --new "%F" %i', ["/a b.png", "/c.png"], entry, Path("/x/app.desktop"))
    assert argvs == [["app", "--new", "/a b.png", "/c.png", "--icon", "app"]]
    single = util._expand_exec("app %f", ["/a.png", "/b.png"], entry, Path("/x/app.desktop"))
    assert single == [["app", "/a.png"], ["app", "/b.png"]]
    uris = util._expand_exec("app %U", ["/a b.png"], entry, Path("/x/app.desktop"))
    assert uris[0][1].startswith("file://") and "%20" in uris[0][1]


@pytest.mark.parametrize("term,expected", [
    ("foot", ["foot", "--working-directory=/w", "ls"]),
    ("kitty", ["kitty", "--directory", "/w", "ls"]),
    ("alacritty", ["alacritty", "--working-directory", "/w", "-e", "ls"]),
    ("wezterm", ["wezterm", "start", "--cwd", "/w", "--", "ls"]),
    ("ghostty", ["ghostty", "--working-directory=/w", "-e", "ls"]),
    ("konsole", ["konsole", "--workdir", "/w", "-e", "ls"]),
    ("gnome-terminal", ["gnome-terminal", "--working-directory=/w", "--", "ls"]),
    ("xterm", ["xterm", "-e", "ls"]),
    ("xdg-terminal-exec", ["xdg-terminal-exec", "--dir=/w", "ls"]),
])
def test_terminal_argv(term, expected):
    argv, cwd = util._terminal_argv([term], "/w", ["ls"])
    assert argv == expected and cwd == "/w"


def test_terminal_argv_without_command():
    argv, _ = util._terminal_argv(["alacritty"], "/w", None)
    assert argv == ["alacritty", "--working-directory", "/w"]


def test_find_terminal_preferred(monkeypatch):
    import sys

    monkeypatch.delenv("TERMINAL", raising=False)
    assert util.find_terminal(f'"{sys.executable}" --flag') == [sys.executable, "--flag"]
    cfg = {"apps": {"terminal": f'"{sys.executable}"'}}
    assert util.find_terminal(cfg) == [sys.executable]


GNOME_COPIED, URI_LIST = "x-special/gnome-copied-files", "text/uri-list"


@pytest.mark.parametrize("configured,default,installed,expected", [
    ("thunar", [], (), GNOME_COPIED),
    ("/usr/bin/dolphin --new-window", ["thunar.desktop"], ("thunar",), URI_LIST),
    ("", ["thunar.desktop"], ("dolphin",), GNOME_COPIED),
    ("", ["org.gnome.Nautilus.desktop"], ("dolphin",), GNOME_COPIED),
    ("", ["org.kde.dolphin.desktop"], ("thunar",), URI_LIST),
    ("", [], ("thunar", "dolphin"), GNOME_COPIED),
    ("", [], ("dolphin",), URI_LIST),
    ("", [], (), GNOME_COPIED),
    # an editor that claimed inode/directory says nothing about who will paste
    ("", ["code.desktop"], ("dolphin",), URI_LIST),
    ("", ["code.desktop"], ("nautilus", "dolphin"), GNOME_COPIED),
])
def test_files_clipboard_type(monkeypatch, configured, default, installed, expected):
    monkeypatch.setattr(util, "_mimeapps", lambda: ({"inode/directory": default}, {}, {}))
    monkeypatch.setattr(util.tools, "have", lambda name: name in installed)
    assert util.files_clipboard_type(configured) == expected


def test_copy_files_payload_matches_the_type(dropwheel_env, monkeypatch):
    sent = []
    monkeypatch.setattr(util, "_wl_copy", lambda data, mime: sent.append((data, mime)) or True)
    a, b = dropwheel_env.work / "una foto.jpg", dropwheel_env.work / "b.pdf"
    uris = [util.path_to_uri(a), util.path_to_uri(b)]
    assert util.copy_files([a, b], file_manager="thunar") is True
    assert sent[-1] == (("copy\n" + "\n".join(uris)).encode("utf-8"), GNOME_COPIED)
    assert util.copy_files([a, b], file_manager="dolphin") is True
    assert sent[-1] == (("\r\n".join(uris) + "\r\n").encode("utf-8"), URI_LIST)
    assert util.copy_files([], file_manager="thunar") is False and len(sent) == 2


def test_free_space_and_dir_size(dropwheel_env):
    d = dropwheel_env.work / "sub"
    d.mkdir()
    (d / "a").write_bytes(b"x" * 100)
    (d / "b").write_bytes(b"x" * 50)
    assert util.dir_size(d) == 150
    assert (util.free_space(d / "does" / "not" / "exist") or 0) > 0


def test_file_copy_and_hud_copy_use_the_configured_file_manager(dropwheel_env, make_runner, monkeypatch):
    """A user with apps.file_manager=dolphin and thunar as the mimeapps default pastes in Dolphin."""
    import importlib

    from dropwheel import config as cfgmod, daemon, registry, tools
    from dropwheel.actions import common

    if registry.get("file.copy") is None:       # another test cleared the registry
        importlib.reload(common)
    src = dropwheel_env.work / "a.txt"
    src.write_text("x", encoding="utf-8")
    seen: list[str] = []
    monkeypatch.setattr(util, "copy_files", lambda paths, file_manager="": seen.append(file_manager) or True)
    real_have = tools.have
    monkeypatch.setattr(tools, "have", lambda name: True if name == "wl-copy" else real_have(name))
    config = cfgmod.defaults()
    config["apps"]["file_manager"] = "dolphin"
    runner, _events = make_runner(config=config)
    job = runner.run_sync("file.copy", [str(src)], {})
    assert job.state == "done", job.error
    assert seen == ["dolphin"]
    server = daemon.Daemon.__new__(daemon.Daemon)
    server.config = config
    assert server.m_util({"op": "copy_files", "paths": [str(src)]}) == {"ok": True}
    assert seen == ["dolphin", "dolphin"]
