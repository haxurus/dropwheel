"""Tests for the command line interface (dropwheel.cli) and the LAN share server it hosts."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from dropwheel import cli, i18n, registry
from dropwheel.errors import UserError

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_daemon import PLUGIN, make_png, write_plugin  # noqa: E402


@pytest.fixture(autouse=True)
def cli_env(dropwheel_env, monkeypatch):
    """Plugin actions, repository data dir, no colours; registry and language restored afterwards."""
    plugin_dir = dropwheel_env.base / "plugins"
    write_plugin(plugin_dir)
    monkeypatch.setenv("DROPWHEEL_EXTRA_ACTIONS", str(plugin_dir / "dw_test_actions.py"))
    monkeypatch.setenv("DROPWHEEL_DATA", str(ROOT))
    monkeypatch.setenv("NO_COLOR", "1")
    state = registry.snapshot()
    lang = i18n.get_lang()
    yield dropwheel_env
    registry.restore(state)
    i18n.set_lang(lang)


def run_cli(capsys, *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def png(cli_env):
    pytest.importorskip("PIL")
    return make_png(cli_env.work / "foto vacanze.png", (320, 200))


# ================================================================================== parsing

def test_help_english(capsys):
    code, out, _ = run_cli(capsys, "--help")
    assert code == 0
    assert out.startswith("usage: dropwheel")
    for command in ("start", "stop", "open", "run", "menu", "doctor", "setup", "undo", "history", "config"):
        assert f"    {command}" in out
    assert "share-serve" not in out


def test_help_italian_and_argparse_restored(capsys):
    code, out, _ = run_cli(capsys, "--lang", "it", "--help")
    assert code == 0
    assert out.startswith("uso: dropwheel")
    assert "comandi" in out and "avvia la shell" in out
    assert argparse._("usage: ") == "usage: "  # the translation hook is removed again


def test_usage_errors(capsys):
    assert run_cli(capsys, "frobnicate")[0] == 2
    assert run_cli(capsys)[0] == 2
    assert run_cli(capsys, "run")[0] == 2
    assert run_cli(capsys, "setup", "nowhere")[0] == 2


def test_version(capsys):
    code, out, _ = run_cli(capsys, "--version")
    assert code == 0 and out.strip().startswith("Dropwheel ")


def test_parse_param_value():
    assert cli.parse_param_value("80") == 80
    assert cli.parse_param_value("0.5") == 0.5
    assert cli.parse_param_value("true") is True and cli.parse_param_value("sì") is True
    assert cli.parse_param_value("off") is False
    assert cli.parse_param_value("null") is None
    assert cli.parse_param_value('{"x": 1, "w": 2}') == {"x": 1, "w": 2}
    assert cli.parse_param_value("[1, 2]") == [1, 2]
    assert cli.parse_param_value("1-3,5") == "1-3,5"
    assert cli.parse_param_value("#ff0000") == "#ff0000"
    assert cli.parse_param_value("{broken") == "{broken"
    assert cli.parse_param_value(" spaced ") == " spaced "


def test_resolve_inputs(cli_env, monkeypatch):
    from dropwheel import util

    f = cli_env.work / "a file.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.chdir(cli_env.work)
    assert cli.resolve_inputs(["a file.txt"]) == [str(f)]
    assert cli.resolve_inputs([util.path_to_uri(f), str(f)]) == [str(f)]
    assert cli.resolve_inputs(["https://example.com/x.png"]) == ["https://example.com/x.png"]
    with pytest.raises(UserError) as exc:
        cli.resolve_inputs(["missing.txt", "a file.txt"])
    assert exc.value.message == "cli.err.not_found" and "missing.txt" in exc.value.text("en")


def test_text_helpers():
    assert cli.text_width("abc") == 3
    assert cli.text_width("日本") == 4
    assert cli.text_width("\033[1mab\033[0m") == 2
    assert cli.truncate("abcdefgh", 5) == "abcd…"
    assert cli.pad("ab", 4) == "ab  "


# ====================================================================================== run

def test_run_plugin_action(capsys, png):
    code, out, err = run_cli(capsys, "run", "test.copy", str(png))
    assert code == 0, err
    produced = png.with_name("foto vacanze (Copy).png")
    assert produced.is_file()
    assert str(produced) in out


def test_run_json_prints_final_event(capsys, png):
    code, out, _ = run_cli(capsys, "run", "--json", "test.copy", str(png))
    assert code == 0
    event = json.loads(out)
    assert event["event"] == "job" and event["state"] == "done"
    assert event["action"] == "test.copy"
    assert Path(event["result"]["outputs"][0]["path"]).is_file()


def test_run_failure_and_usage(capsys, png):
    code, out, err = run_cli(capsys, "run", "test.fail", str(png))
    assert code == 1
    assert "Unsupported format" in err
    code, _, err = run_cli(capsys, "run", "test.cpy", str(png))
    assert code == 2
    assert "test.copy" in err  # did-you-mean suggestion
    assert run_cli(capsys, "run", "-p", "novalue", "test.copy", str(png))[0] == 2
    code, _, err = run_cli(capsys, "run", "test.copy", str(png.with_name("nope.png")))
    assert code == 1 and "nope.png" in err


def test_run_with_params(capsys, png):
    code, out, _ = run_cli(capsys, "run", "--json", "-p", "steps=3", "test.slow", str(png))
    assert code == 0
    event = json.loads(out)
    assert event["result"]["text"] == "finished"


def test_run_real_image_action(capsys, png):
    cli._load_actions(cli.Ctx(config={}, lang="en", term=cli.Term(None), eterm=cli.Term(None)))
    act = registry.get("image.convert.jpg")
    if act is None or act.missing():
        pytest.skip("image actions not available")
    code, out, err = run_cli(capsys, "run", "image.convert.jpg", str(png))
    assert code == 0, err
    assert png.with_suffix(".jpg").is_file()


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals")
def test_run_ctrl_c_cancels(cli_env, png):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC), env.get("PYTHONPATH", "")) if p)
    proc = subprocess.Popen([sys.executable, "-m", "dropwheel", "run", "-q", "-p", "steps=4000", "test.slow",
                             str(png)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2.5)
    proc.send_signal(signal.SIGINT)
    try:
        _out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 130, err.decode()


# ===================================================================================== menu

def test_menu_tree_and_json(capsys, png):
    code, out, _ = run_cli(capsys, "menu", str(png))
    assert code == 0
    assert out.startswith("foto vacanze.png")
    assert "test.copy" in out
    code, out, _ = run_cli(capsys, "menu", "--json", str(png))
    assert code == 0
    menu = json.loads(out)
    assert menu["category"] == "image" and menu["count"] == 1
    assert [p["id"] for p in menu["pages"]] == ["main", "file"]
    assert menu["thumb"] and os.path.isfile(menu["thumb"])


def test_actions(capsys, png):
    code, out, _ = run_cli(capsys, "actions", "--category", "image", "--json")
    assert code == 0
    rows = json.loads(out)
    ids = {r["id"] for r in rows}
    assert "test.copy" in ids and "test.fail" in ids
    for r in rows:
        assert set(r) >= {"id", "label", "available", "missing", "install"}
    code, out, _ = run_cli(capsys, "actions")
    assert code == 0 and "test.copy" in out
    assert run_cli(capsys, "actions", "-c", "bogus")[0] == 2


# ============================================================================ history / undo

def test_history_and_undo(capsys, png):
    assert run_cli(capsys, "history")[1].strip() == "No jobs yet"
    code, out, _ = run_cli(capsys, "undo")
    assert code == 1
    run_cli(capsys, "run", "test.copy", str(png))
    produced = png.with_name("foto vacanze (Copy).png")
    assert produced.is_file()
    code, out, _ = run_cli(capsys, "history", "--json")
    entries = json.loads(out)
    assert entries[0]["action"] == "test.copy" and entries[0]["undoable"] is True
    code, out, _ = run_cli(capsys, "history")
    assert "foto vacanze.png" in out and "undo possible" in out
    code, out, err = run_cli(capsys, "undo")
    assert code == 0, err
    assert not produced.exists()
    code, out, _ = run_cli(capsys, "history", "--json")
    assert json.loads(out)[0]["undone"] is True
    assert run_cli(capsys, "history", "-n", "0")[0] == 2


# =================================================================================== config

def test_config_commands(capsys, cli_env):
    from dropwheel import config as cfgmod

    code, out, _ = run_cli(capsys, "config", "--path")
    assert code == 0 and out.strip() == str(cfgmod.config_path())
    code, out, _ = run_cli(capsys, "config")
    assert code == 0 and "[wheel]" in out and "not found" in out
    assert run_cli(capsys, "config", "--init")[0] == 0
    assert cfgmod.config_path().is_file()
    code, _, err = run_cli(capsys, "config", "--init")
    assert code == 1 and "--force" in err
    assert run_cli(capsys, "config", "--init", "--force")[0] == 0
    code, out, _ = run_cli(capsys, "config", "--json")
    data = json.loads(out)
    assert data["exists"] is True and data["warnings"] == [] and "hud" in data["config"]
    assert run_cli(capsys, "config", "--path", "--init")[0] == 2


# ==================================================================================== setup

def _caelestia_dots(config: Path) -> None:
    """The lines of the Caelestia dots' hyprland.lua that matter here (research: caelestia.md)."""
    (config / "caelestia").mkdir(exist_ok=True)
    (config / "hypr").mkdir(exist_ok=True)
    (config / "hypr" / "hyprland.lua").write_text(
        'local home = os.getenv("HOME")\n'
        'package.path = package.path .. ";" .. home .. "/.config/caelestia/?.lua"\n'
        'maybe_create(home .. "/.config/caelestia/hypr-user.lua")\n'
        'require("hypr-user") -- loaded last\n', encoding="utf-8")


def test_setup_hyprland_caelestia(capsys, cli_env):
    _caelestia_dots(cli_env.config)
    cael = cli_env.config / "caelestia"
    loader = cael / "hypr-user.lua"
    loader.write_text('-- my binds\n-- require("dropwheel") is commented\nhl.bind("SUPER + Q", hl.dsp.kill())',
                      encoding="utf-8")
    code, out, _ = run_cli(capsys, "setup", "hyprland", "--dry-run")
    assert code == 0 and not (cael / "dropwheel.lua").exists()
    assert "require" not in loader.read_text(encoding="utf-8").split("commented")[1]
    code, out, _ = run_cli(capsys, "setup", "hyprland")
    assert code == 0
    module = cael / "dropwheel.lua"
    assert module.read_bytes() == (ROOT / "integrations" / "hyprland" / "dropwheel.lua").read_bytes()
    text = loader.read_text(encoding="utf-8")
    assert text.startswith("-- my binds\n")
    assert text.count('\nrequire("dropwheel")') == 1 and text.endswith('require("dropwheel")\n')
    backups = list(cael.glob("hypr-user.lua.dropwheel-bak-*"))
    assert len(backups) == 1 and "require" not in backups[0].read_text(encoding="utf-8").split("commented")[1]
    assert cli.hyprland_setup_state()["configured"] is True
    # idempotent
    code, out, _ = run_cli(capsys, "setup", "hyprland")
    assert code == 0 and "already" in out
    assert loader.read_text(encoding="utf-8") == text
    assert len(list(cael.glob("*.dropwheel-bak-*"))) == 1
    # a modified module is backed up before being refreshed
    module.write_text("-- edited\n", encoding="utf-8")
    run_cli(capsys, "setup", "hyprland")
    assert module.read_bytes() == (ROOT / "integrations" / "hyprland" / "dropwheel.lua").read_bytes()
    assert len(list(cael.glob("dropwheel.lua.dropwheel-bak-*"))) == 1


def test_setup_hyprland_without_caelestia(capsys, cli_env):
    code, out, _ = run_cli(capsys, "setup", "hyprland")
    assert code == 0
    assert (cli_env.config / "hypr" / "dropwheel.lua").is_file()
    assert not (cli_env.config / "hypr" / "hyprland.lua").exists()  # never edited automatically
    assert "package.path" in out and 'require("dropwheel")' in out


@pytest.mark.parametrize("main_cfg", [
    None,
    'hl.bind("SUPER + Q", hl.dsp.kill())\n',
    '-- require("hypr-user")\n',  # commented out: nothing loads it
])
def test_setup_hyprland_caelestia_folder_alone_is_not_enough(capsys, cli_env, main_cfg):
    # caelestia-shell with a Hyprland configuration of one's own: ~/.config/caelestia exists,
    # but nothing would ever load a require added to hypr-user.lua
    cael = cli_env.config / "caelestia"
    cael.mkdir()
    (cael / "shell.json").write_text("{}", encoding="utf-8")
    hypr = cli_env.config / "hypr"
    if main_cfg is not None:
        hypr.mkdir()
        (hypr / "hyprland.lua").write_text(main_cfg, encoding="utf-8")
    code, out, _ = run_cli(capsys, "setup", "hyprland")
    assert code == 0
    assert not (cael / "dropwheel.lua").exists() and not (cael / "hypr-user.lua").exists()
    assert (hypr / "dropwheel.lua").is_file()
    assert "package.path" in out and "does not load" in out
    state = cli.hyprland_setup_state()
    assert state["configured"] is False and state["module"] == str(hypr / "dropwheel.lua")
    assert state["loader"] == str(hypr / "hyprland.lua")
    # the user follows the instructions
    (hypr / "hyprland.lua").write_text((main_cfg or "") + 'require("dropwheel")\n', encoding="utf-8")
    assert cli.hyprland_setup_state()["configured"] is True


def test_doctor_state_of_a_require_that_nothing_loads(cli_env):
    # left by an earlier setup: hypr-user.lua requires us, but hyprland.lua never reads it
    cael = cli_env.config / "caelestia"
    cael.mkdir()
    (cael / "dropwheel.lua").write_text("-- module\n", encoding="utf-8")
    (cael / "hypr-user.lua").write_text('require("dropwheel")\n', encoding="utf-8")
    state = cli.hyprland_setup_state()
    assert state["configured"] is False and state["module"] == str(cael / "dropwheel.lua")
    _caelestia_dots(cli_env.config)
    assert cli.hyprland_setup_state() == {"configured": True, "module": str(cael / "dropwheel.lua"),
                                          "loader": str(cael / "hypr-user.lua")}


@pytest.mark.parametrize(("source", "expected"), [
    ("", None),
    ('hl.bind("SUPER + Q", hl.dsp.kill())\n', None),
    ('-- return M\nlocal x = 1\n', None),
    ('--[[ return M ]]\nlocal x = 1\n', None),
    ('--[==[\nreturn M\n]==]\nlocal x = 1\n', None),
    ('local s = "return"\nlocal t = [[\nreturn\n]]\n', None),
    ('local function f()\n  return 1\nend\n', None),
    ('if a then return end\nfor i = 1, 2 do\n  if i then return i end\nend\nwhile x do return end\n', None),
    ('repeat local y = 1; return y until true\n', None),
    ('local M = {}\nreturn M\n', 'return M\n'),
    ('local M = {}\n\nreturn M -- the module\n-- bye\n', 'return M -- the module\n-- bye\n'),
    ('local M = {}\nreturn {\n  a = 1,\n}\n', 'return {\n  a = 1,\n}\n'),
    ('local function f() return 1 end return f\n', 'return f\n'),
    ('hl.on("hyprland.start", function()\n  hl.exec_cmd("x")\nend)\nreturn {}', 'return {}'),
])
def test_lua_final_return(source, expected):
    pos = cli._lua_final_return(source)
    assert (None if pos is None else source[pos:]) == expected


@pytest.mark.parametrize(("original", "tail"), [
    ('local M = {}\nreturn M\n', "return M\n"),
    ('local M = {}\nreturn {\n  a = 1,\n}', "return {\n  a = 1,\n}"),
    ('local M = {}\r\nreturn M\r\n', "return M\r\n"),
])
def test_setup_hyprland_require_goes_before_a_final_return(capsys, cli_env, original, tail):
    # Lua only accepts "return" as the last statement: appending would break the whole file
    _caelestia_dots(cli_env.config)
    loader = cli_env.config / "caelestia" / "hypr-user.lua"
    with open(loader, "w", encoding="utf-8", newline="") as fh:
        fh.write(original)
    assert run_cli(capsys, "setup", "hyprland")[0] == 0
    with open(loader, encoding="utf-8", newline="") as fh:
        text = fh.read()
    assert text.endswith(tail) and text.startswith("local M = {}")
    assert text.index('require("dropwheel")') < text.rindex("return")
    assert cli._lua_final_return(text) == text.rindex("return")
    assert cli.lua_requires_dropwheel(loader)
    backups = list(loader.parent.glob("hypr-user.lua.dropwheel-bak-*"))
    assert len(backups) == 1
    with open(backups[0], encoding="utf-8", newline="") as fh:
        assert fh.read() == original
    # idempotent
    assert run_cli(capsys, "setup", "hyprland")[0] == 0
    with open(loader, encoding="utf-8", newline="") as fh:
        assert fh.read() == text


def test_setup_thunar(capsys, cli_env):
    import xml.etree.ElementTree as ET

    uca = cli_env.config / "Thunar" / "uca.xml"
    code, out, _ = run_cli(capsys, "setup", "thunar")
    assert code == 0 and "<unique-id>dropwheel-open-in-dropwheel</unique-id>" in out
    assert not uca.exists()
    uca.parent.mkdir(parents=True)
    original = ('<?xml version="1.0" encoding="UTF-8"?>\n<actions>\n<action>\n\t<icon>utilities-terminal</icon>\n'
                '\t<name>Open Terminal Here</name>\n\t<unique-id>1-1</unique-id>\n'
                '\t<command>foot</command>\n</action>\n</actions>\n')
    uca.write_text(original, encoding="utf-8")
    assert run_cli(capsys, "setup", "thunar", "--apply", "--dry-run")[0] == 0
    assert uca.read_text(encoding="utf-8") == original
    code, out, _ = run_cli(capsys, "setup", "thunar", "--apply")
    assert code == 0
    root = ET.fromstring(uca.read_bytes())
    assert [a.findtext("unique-id") for a in root.findall("action")] == ["1-1", "dropwheel-open-in-dropwheel"]
    backups = list(uca.parent.glob("uca.xml.dropwheel-bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == original
    code, out, _ = run_cli(capsys, "setup", "thunar", "--apply")
    assert code == 0 and "already" in out
    assert len(ET.fromstring(uca.read_bytes()).findall("action")) == 2


def test_setup_thunar_invalid_xml_untouched(capsys, cli_env):
    uca = cli_env.config / "Thunar" / "uca.xml"
    uca.parent.mkdir(parents=True)
    uca.write_text("<actions><action>", encoding="utf-8")
    code, _, err = run_cli(capsys, "setup", "thunar", "--apply")
    assert code == 1 and "not valid XML" in err
    assert uca.read_text(encoding="utf-8") == "<actions><action>"


def test_setup_file_managers_and_desktop(capsys, cli_env):
    data = cli_env.data
    assert run_cli(capsys, "setup", "nautilus")[0] == 0
    script = data / "nautilus" / "scripts" / "Dropwheel"
    assert script.is_file()
    if os.name == "posix":
        assert os.stat(script).st_mode & 0o111
    assert run_cli(capsys, "setup", "dolphin")[0] == 0
    assert (data / "kio" / "servicemenus" / "dropwheel.desktop").is_file()
    assert run_cli(capsys, "setup", "nemo")[0] == 0
    assert (data / "nemo" / "actions" / "dropwheel.nemo_action").is_file()
    assert run_cli(capsys, "setup", "desktop")[0] == 0
    assert (data / "applications" / "dropwheel.desktop").is_file()
    assert run_cli(capsys, "setup", "dolphin", "--apply")[0] == 2


USER_LAUNCHER = "/home/ale/.local/bin/dropwheel"


def test_rewrite_launcher_matches_install_sh():
    text = ("[Desktop Entry]\nExec=dropwheel open %F\nTryExec=dropwheel\nIcon=dropwheel\n\n"
            "[Desktop Action start]\nExec=dropwheel start\nExec=dropwheel\nExec=dropwheelx\nDependencies=dropwheel;\n")
    assert cli.rewrite_launcher(text, USER_LAUNCHER, "desktop") == (
        f"[Desktop Entry]\nExec={USER_LAUNCHER} open %F\nTryExec={USER_LAUNCHER}\nIcon=dropwheel\n\n"
        f"[Desktop Action start]\nExec={USER_LAUNCHER} start\nExec={USER_LAUNCHER}\nExec=dropwheelx\n"
        f"Dependencies={USER_LAUNCHER};\n")
    # Desktop Entry quoting, same steps as desktop_exec_arg in install.sh
    assert cli._desktop_exec_arg("/home/a b/$x%y/dropwheel") == '"/home/a b/\\\\$x%%y/dropwheel"'
    # Nemo's Exec is split like a shell command, same as sq in install.sh
    assert cli.rewrite_launcher("Exec=dropwheel open %F\n", "/home/o'neil/bin/dropwheel", "shell") == (
        "Exec='/home/o'\\''neil/bin/dropwheel' open %F\n")


def test_setup_points_launchers_at_a_user_install(capsys, cli_env, monkeypatch):
    # ~/.local/bin is often missing from the PATH of the graphical session
    monkeypatch.setattr(cli, "launcher_path", lambda: USER_LAUNCHER)
    data = cli_env.data
    assert run_cli(capsys, "setup", "desktop")[0] == 0
    desktop = (data / "applications" / "dropwheel.desktop").read_text(encoding="utf-8")
    assert f"\nExec={USER_LAUNCHER} open %F\n" in desktop and f"\nTryExec={USER_LAUNCHER}\n" in desktop
    assert f"\nExec={USER_LAUNCHER} start\n" in desktop and "Exec=dropwheel" not in desktop
    assert "\nIcon=dropwheel\n" in desktop
    assert run_cli(capsys, "setup", "dolphin")[0] == 0
    dolphin = (data / "kio" / "servicemenus" / "dropwheel.desktop").read_text(encoding="utf-8")
    assert f"\nExec={USER_LAUNCHER} open %F\n" in dolphin
    assert run_cli(capsys, "setup", "nemo")[0] == 0
    nemo = (data / "nemo" / "actions" / "dropwheel.nemo_action").read_text(encoding="utf-8")
    assert f"\nExec='{USER_LAUNCHER}' open %F\n" in nemo and f"\nDependencies={USER_LAUNCHER};\n" in nemo
    # a re-run compares against the rewritten text: nothing to back up
    code, out, _ = run_cli(capsys, "setup", "desktop")
    assert code == 0 and "up to date" in out
    assert not list((data / "applications").glob("*.dropwheel-bak-*"))
    # the Nautilus script finds the launcher by itself
    assert run_cli(capsys, "setup", "nautilus")[0] == 0
    assert (data / "nautilus" / "scripts" / "Dropwheel").read_bytes() == (
        ROOT / "integrations" / "nautilus" / "Dropwheel").read_bytes()


@pytest.mark.parametrize(("launcher", "command"), [
    (USER_LAUNCHER, f"{USER_LAUNCHER} open %F"),
    ("/home/a&b c/100%/dropwheel", "'/home/a&b c/100%%/dropwheel' open %F"),
])
def test_setup_thunar_action_uses_the_launcher(capsys, cli_env, monkeypatch, launcher, command):
    import xml.etree.ElementTree as ET

    monkeypatch.setattr(cli, "launcher_path", lambda: launcher)
    assert run_cli(capsys, "setup", "thunar", "--apply")[0] == 0
    uca = cli_env.config / "Thunar" / "uca.xml"
    assert ET.fromstring(uca.read_bytes()).find("action").findtext("command") == command
    code, out, _ = run_cli(capsys, "setup", "thunar", "--apply")
    assert code == 0 and "already" in out


def test_setup_keeps_bare_name_for_the_package(capsys, cli_env, monkeypatch):
    monkeypatch.setattr(cli, "launcher_path", lambda: None)
    assert run_cli(capsys, "setup", "desktop")[0] == 0
    assert (cli_env.data / "applications" / "dropwheel.desktop").read_bytes() == (
        ROOT / "integrations" / "desktop" / "dropwheel.desktop").read_bytes()
    assert run_cli(capsys, "setup", "thunar", "--apply")[0] == 0
    assert "<command>dropwheel open %F</command>" in (cli_env.config / "Thunar" / "uca.xml").read_text(encoding="utf-8")


def test_launcher_path(cli_env, monkeypatch):
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: cli_env.base))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.launcher_path() is None
    wrapper = cli_env.base / ".local" / "bin" / "dropwheel"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\nexec something-else\n", encoding="utf-8")
    assert cli.launcher_path() is None  # not ours
    wrapper.write_text("#!/bin/sh\n# DROPWHEEL-USER-WRAPPER\nexec python -m dropwheel\n", encoding="utf-8")
    assert cli.launcher_path() == str(wrapper)
    elsewhere = str(cli_env.base / "bin" / "dropwheel")
    monkeypatch.setattr(cli.shutil, "which", lambda name: elsewhere)
    assert cli.launcher_path() == elsewhere
    monkeypatch.setattr(cli.os.path, "realpath", lambda p: "/usr/bin/dropwheel")
    assert cli.launcher_path() is None


def test_setup_missing_integrations(capsys, cli_env, monkeypatch):
    monkeypatch.setenv("DROPWHEEL_DATA", str(cli_env.base / "nowhere"))
    code, _, err = run_cli(capsys, "setup", "nautilus")
    assert code == 1 and "nowhere" in err


# =================================================================================== doctor

def test_doctor(capsys):
    code, out, _ = run_cli(capsys, "doctor", "--json")
    data = json.loads(out)
    assert code == (1 if data["required_missing"] else 0)
    assert {r["package"] for r in data["required"]} == {"python-pillow", "quickshell", "hyprland", "wl-clipboard"}
    assert isinstance(data["install_all"], str)
    assert "extra:" not in json.dumps(data["load_errors"])  # the test plugin loads fine
    code2, out, _ = run_cli(capsys, "doctor")
    assert code2 == code
    assert "Required dependencies" in out and "Optional tools" in out


def test_install_all_command_skips_choices_and_alternatives(monkeypatch):
    from dropwheel import tools

    have = {"rsvg-convert"}
    monkeypatch.setattr(tools, "have", lambda name: name in have)
    report = {"tools": [{"name": n, "available": n in have} for n in ("rsvg-convert", "resvg", "kitty", "ffmpeg")]}
    command = cli.install_all_command(report)
    assert "ffmpeg" in command and "resvg" not in command and "kitty" not in command


# ============================================================================ shell control

def test_status_json(capsys):
    code, out, _ = run_cli(capsys, "status", "--json")
    data = json.loads(out)
    assert code == (0 if data["running"] else 1)
    assert data["data_dir"] == str(ROOT.resolve())


def test_open_checks_files_first(capsys, cli_env):
    code, _, err = run_cli(capsys, "open", str(cli_env.work / "missing.pdf"))
    assert code == 1 and "missing.pdf" in err
    assert run_cli(capsys, "open", "--at", "nonsense")[0] == 2


def test_open_without_quickshell(capsys, png, monkeypatch):
    from dropwheel import tools

    real_path = tools.path
    monkeypatch.setattr(tools, "path", lambda name: None if name == "qs" else real_path(name))
    code, _, err = run_cli(capsys, "open", str(png))
    assert code == 1
    assert "Quickshell" in err and "quickshell" in err  # message + install command


# =================================================================================== shares

def _get(url: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_share_server(capsys, cli_env):
    from dropwheel import share

    a = cli_env.work / "caffè ☕.txt"
    a.write_text("hello share\n" * 100, encoding="utf-8")
    folder = cli_env.work / "cartella"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "x.txt").write_text("x", encoding="utf-8")
    info = share.start_share([str(a), str(folder)], minutes=2, port=0, host="127.0.0.1", lang="it")
    try:
        assert info["url"].startswith("http://127.0.0.1:") and info["url"].endswith("/")
        if info["qr"]:
            assert os.path.isfile(info["qr"])
        status, headers, body = _get(info["url"])
        assert status == 200 and "text/html" in headers["Content-Type"]
        page = body.decode("utf-8")
        assert "caffè ☕.txt" in page and "Scarica" in page
        status, headers, body = _get(info["url"] + "f/0/x")
        assert status == 200 and body == a.read_bytes()
        assert "filename*=UTF-8''caff%C3%A8%20%E2%98%95.txt" in headers["Content-Disposition"]
        status, headers, body = _get(info["url"] + "f/0/x", {"Range": "bytes=6-10"})
        assert status == 206 and body == b"share" and headers["Content-Range"].endswith(f"/{a.stat().st_size}")
        status, _, body = _get(info["url"] + "f/1/cartella.zip")
        assert status == 200
        names = zipfile.ZipFile(__import__("io").BytesIO(body)).namelist()
        assert "cartella/sub/x.txt" in names
        status, _, body = _get(info["url"] + "all.zip")
        assert status == 200 and len(zipfile.ZipFile(__import__("io").BytesIO(body)).namelist()) == 2
        bad = info["url"].rsplit("/", 2)[0] + "/wrongtoken/"
        assert _get(bad)[0] == 404
        code, out, _ = run_cli(capsys, "shares", "--json")
        assert [s["id"] for s in json.loads(out)] == [info["id"]]
    finally:
        code, out, _ = run_cli(capsys, "shares", "--stop", info["id"])
    assert code == 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            _get(info["url"])
        except (urllib.error.URLError, ConnectionError, OSError):
            break
        time.sleep(0.2)
    else:
        pytest.fail("share server still answering after stop")
    assert share.list_shares() == []
    assert run_cli(capsys, "shares", "--stop", info["id"])[0] == 1


def test_content_disposition():
    from dropwheel import share

    value = share.content_disposition('perché "sì".pdf')
    assert value.startswith('attachment; filename="perche _si_.pdf"; ')
    assert "filename*=UTF-8''perch%C3%A9%20%22s%C3%AC%22.pdf" in value
    assert share.content_disposition("a.txt", inline=True).startswith("inline;")


def test_plugin_source_is_shared_with_daemon_tests():
    assert "test.copy" in PLUGIN


# ================================================================================= shellctl

@pytest.fixture
def fake_qs(cli_env, monkeypatch):
    """A data dir with shell/shell.qml and a scripted ``qs`` (``_run_qs`` replaced)."""
    from dropwheel import shellctl, tools

    data = cli_env.base / "dwdata"
    (data / "shell").mkdir(parents=True)
    (data / "shell" / "shell.qml").write_text("import Quickshell\nShellRoot {}\n", encoding="utf-8")
    monkeypatch.setenv("DROPWHEEL_DATA", str(data))
    real_path = tools.path
    monkeypatch.setattr(tools, "path", lambda name: "/usr/bin/qs" if name == "qs" else real_path(name))
    monkeypatch.setattr(shellctl, "_ANY_DISPLAY_OK", None)
    state = {"calls": [], "running": True, "any_display": True}

    def run_qs(args, *, timeout=10.0, env=None):
        state["calls"].append(list(args))
        if "--any-display" in args and not state["any_display"]:
            return subprocess.CompletedProcess(args, 106, "", "The following argument was not expected: "
                                                              "--any-display\nRun with --help for more information.")
        if args[0] == "list":
            if state["running"]:
                return subprocess.CompletedProcess(args, 0, '[{"id": "abc", "pid": 4242}]\n', "")
            return subprocess.CompletedProcess(args, 255, "", "No running instances for /x/shell.qml\n")
        if args[0] == "kill":
            state["running"] = False
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "ipc":
            if not state["running"]:
                return subprocess.CompletedProcess(args, 255, "", "No running instances for /x/shell.qml\n")
            fn = args[args.index("call") + 2]
            return subprocess.CompletedProcess(args, 0, "pong\n" if fn == "ping" else "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(shellctl, "_run_qs", run_qs)
    state["shell"] = str((data / "shell").resolve())
    return state


def test_shellctl_instances_and_any_display_fallback(fake_qs, monkeypatch):
    from dropwheel import shellctl

    fake_qs["any_display"] = False
    assert shellctl.instances() == [{"id": "abc", "pid": 4242}]
    shell = fake_qs["shell"]
    assert fake_qs["calls"][0] == ["list", "--any-display", "-p", shell, "-j"]
    assert fake_qs["calls"][1] == ["list", "-p", shell, "-j"]
    assert shellctl.running() is True
    assert ["list", "--any-display", "-p", shell, "-j"] not in fake_qs["calls"][2:]  # remembered


def test_shellctl_ipc_ping_stop(fake_qs, monkeypatch):
    from dropwheel import shellctl

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    assert shellctl.ping() is True
    call = [c for c in fake_qs["calls"] if c[0] == "ipc"][-1]
    assert call == ["ipc", "-p", fake_qs["shell"], "call", "dropwheel", "ping"]
    assert shellctl.ipc_call("open", '{"files": ["/a b.png"]}') == ""
    assert fake_qs["calls"][-1][-1] == '{"files": ["/a b.png"]}'
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.delenv("DISPLAY", raising=False)
    shellctl.ping()
    assert "--any-display" in fake_qs["calls"][-1]  # from a TTY the display filter is disabled
    assert shellctl.stop() is True
    assert fake_qs["running"] is False
    with pytest.raises(UserError) as exc:
        shellctl.ipc_call("ping")
    assert exc.value.message == "shell.err.not_running"
    assert shellctl.status(check_ping=False)["running"] is False


def test_shellctl_paths_and_env(fake_qs):
    from dropwheel import shellctl

    env = shellctl.shell_env()
    assert env["DROPWHEEL_BACKEND"].endswith("-P -m dropwheel daemon")   # -P: the cwd must not shadow any module
    assert env["DROPWHEEL_DATA"] == str(Path(fake_qs["shell"]).parent)
    assert str(shellctl.package_root()) in env["PYTHONPATH"].split(os.pathsep)
    assert shellctl.integrations_dir() == Path(fake_qs["shell"]).parent / "integrations"


def test_cli_open_and_stop_with_fake_qs(capsys, fake_qs, png, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    assert run_cli(capsys, "open", str(png), "--at", "100,200")[0] == 0
    call = fake_qs["calls"][-1]
    assert call[:6] == ["ipc", "-p", fake_qs["shell"], "call", "dropwheel", "open"]
    assert json.loads(call[6]) == {"files": [str(png)], "x": 100, "y": 200}
    assert run_cli(capsys, "open")[0] == 0
    assert fake_qs["calls"][-1][-1] == "openAtCursor"
    code, out, _ = run_cli(capsys, "status")
    assert code == 0 and "4242" in out
    code, out, _ = run_cli(capsys, "stop")
    assert code == 0 and "stopped" in out
    assert run_cli(capsys, "status")[0] == 1
