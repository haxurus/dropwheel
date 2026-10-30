"""Checks for the packaging and integration files (owner: pack).

They validate the files that cannot be exercised by importing Python code: the Makefile, the
PKGBUILD and the shell scripts, the desktop entries, the Thunar snippet, the icon and the
Hyprland Lua module (run against a mock ``hl`` table when the ``lupa`` Lua runtime is available).
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INTEGRATIONS = ROOT / "integrations"
BASH = shutil.which("bash")

SHELL_SCRIPTS = [
    "install.sh",
    "uninstall.sh",
    "PKGBUILD",
    "dropwheel.install",
    "integrations/nautilus/Dropwheel",
]

REQUIRED_DEPENDS = {
    "python",
    "python-pillow",
    "quickshell",
    "hyprland",
    "wl-clipboard",
    "libnotify",
    "ttf-material-symbols-variable",
    "glib2",
    "xdg-utils",
}

# Packages that only exist in the AUR or on PyPI: documented in README.md, never optdepends.
AUR_OR_PIP_ONLY = {
    "ocrmypdf",
    "python-rembg",
    "rembg",
    "realesrgan-ncnn-vulkan",
    "vtracer",
    "python-vtracer",
    "python-pdf2docx",
    "pdf2docx",
}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _desktop_file(rel: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True, comment_prefixes=("#",))
    parser.optionxform = str  # keys are case-sensitive in desktop entries
    parser.read_string(_read(rel))
    return parser


# --------------------------------------------------------------------------------------------
# All packaging files
# --------------------------------------------------------------------------------------------


def test_packaging_files_have_unix_line_endings() -> None:
    """A CR is fatal on Linux (``pkgname=dropwheel\\r``, ``Exec=dropwheel open %F\\r``) but invisible
    here: Git Bash on the Windows development machine ignores it, and so does ``read_text``."""
    files = [ROOT / name for name in ("PKGBUILD", "Makefile", "install.sh", "uninstall.sh", "dropwheel.install",
                                      "pyproject.toml", "README.md", "README.it.md", "LICENSE", ".gitignore")]
    files += sorted((ROOT / "docs").glob("*.md"))
    for folder in (INTEGRATIONS, ROOT / "assets"):
        files += sorted(path for path in folder.rglob("*") if path.is_file())
    offending = [str(path.relative_to(ROOT)) for path in files if b"\r" in path.read_bytes()]
    assert not offending, f"CR line endings in {offending}"


# --------------------------------------------------------------------------------------------
# Makefile
# --------------------------------------------------------------------------------------------


def test_makefile_recipes_are_indented_with_tabs() -> None:
    lines = _read("Makefile").splitlines()
    in_rule = False
    continued = False
    problems = []
    for number, line in enumerate(lines, start=1):
        if continued:
            continued = line.endswith("\\")
            continue
        if not line.strip():
            in_rule = False
        elif line.startswith("\t"):
            if not in_rule:
                problems.append(f"line {number}: tab-indented line outside a rule")
        elif line.startswith(" ") and in_rule and not line.lstrip().startswith("#"):
            problems.append(f"line {number}: recipe line indented with spaces")
        elif re.match(r"^[A-Za-z0-9_.%/-][^=:]*:(?!=)", line) and not line.startswith("#"):
            in_rule = True
        continued = line.endswith("\\")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    "target",
    ["install", "install-user", "uninstall", "test", "lint", "dist", "wheel", "clean", "help"],
)
def test_makefile_has_target(target: str) -> None:
    assert re.search(rf"^{re.escape(target)}:", _read("Makefile"), re.MULTILINE)


def test_makefile_builds_and_installs_the_wheel_as_specified() -> None:
    text = _read("Makefile")
    assert "-m build --wheel --no-isolation" in text
    assert "-m installer" in text
    assert "$(DATAROOTDIR)/dropwheel" in text
    assert "applications" in text and "icons/hicolor/scalable/apps" in text


# --------------------------------------------------------------------------------------------
# Shell scripts and PKGBUILD
# --------------------------------------------------------------------------------------------


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_shell_scripts_parse(script: str) -> None:
    result = subprocess.run([BASH, "-n", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_scripts_are_strict_and_have_shebang() -> None:
    for script in ("install.sh", "uninstall.sh"):
        text = _read(script)
        assert text.startswith("#!/usr/bin/env bash\n"), script
        assert "set -euo pipefail" in text, script
        # sudo may only appear inside printed hints, never as a command that runs.
        assert not re.search(r"^\s*(exec\s+|if\s+|!\s*)?sudo\b", text, re.MULTILINE), script
        assert not re.search(r"(&&|\|\||;|\$\()\s*sudo\b", text), script
    assert _read("integrations/nautilus/Dropwheel").startswith("#!/usr/bin/env bash\n")


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("script", ["install.sh", "uninstall.sh"])
def test_scripts_print_help(script: str) -> None:
    env = dict(os.environ, LANG="C", LC_ALL="C")
    result = subprocess.run(
        [BASH, script, "--help"], cwd=ROOT, capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, result.stderr
    assert "--prefix" in result.stdout
    if script == "install.sh":
        for option in ("--no-hyprland", "--yes", "--uninstall"):
            assert option in result.stdout
    else:
        assert "--purge" in result.stdout


PKGBUILD_DUMP = r"""
set -e
source ./PKGBUILD
for v in depends makedepends optdepends source arch license; do
    declare -n ref=$v
    for x in "${ref[@]}"; do printf '%s\t%s\0' "$v" "$x"; done
    unset -n ref
done
printf 'pkgname\t%s\0install\t%s\0' "$pkgname" "$install"
"""


def _pkgbuild_arrays() -> dict[str, list[str]]:
    result = subprocess.run(
        [BASH, "-c", PKGBUILD_DUMP], cwd=ROOT, capture_output=True, env=dict(os.environ, LC_ALL="C")
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    arrays: dict[str, list[str]] = {
        name: [] for name in ("depends", "makedepends", "optdepends", "source", "arch", "license")
    }
    for record in result.stdout.decode().split("\0"):
        if not record:
            continue
        name, _, value = record.partition("\t")
        arrays.setdefault(name, []).append(value)
    return arrays


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_pkgbuild_metadata() -> None:
    arrays = _pkgbuild_arrays()
    assert arrays["pkgname"] == ["dropwheel"]
    assert arrays["arch"] == ["any"]
    assert arrays["license"] == ["MIT"]
    assert arrays["source"] == []
    assert arrays["install"] == ["dropwheel.install"]
    assert set(arrays["depends"]) == REQUIRED_DEPENDS
    assert set(arrays["makedepends"]) == {
        "python-build",
        "python-installer",
        "python-setuptools",
        "python-wheel",
    }
    names = []
    for entry in arrays["optdepends"]:
        name, sep, description = entry.partition(": ")
        assert sep and description.strip(), f"optdepends entry without description: {entry!r}"
        names.append(name)
    assert len(names) == len(set(names)), "duplicate optdepends"
    assert not set(names) & AUR_OR_PIP_ONLY, "AUR/pip-only packages belong in README.md"
    for expected in ("ffmpeg", "python-pymupdf", "tesseract-data-ita", "7zip", "imagemagick"):
        assert expected in names


def test_install_sh_checks_the_same_required_packages_as_the_pkgbuild() -> None:
    match = re.search(r"^REQUIRED_PKGS=\(([^)]*)\)", _read("install.sh"), re.MULTILINE)
    assert match, "REQUIRED_PKGS not found in install.sh"
    assert set(match.group(1).split()) == REQUIRED_DEPENDS


def test_install_script_hint_mentions_setup_and_start() -> None:
    text = _read("dropwheel.install")
    assert "dropwheel setup hyprland" in text
    assert "dropwheel start" in text
    for function in ("post_install", "post_upgrade", "post_remove"):
        assert re.search(rf"^{function}\(\)", text, re.MULTILINE)


def test_make_test_hides_the_session_from_the_tests() -> None:
    """``makepkg -si`` runs inside the session: wl-copy must not replace the user's clipboard."""
    recipe = re.search(r"^test:\n((?:\t.*\n)+)", _read("Makefile"), re.MULTILINE)
    assert recipe, "test target not found"
    for variable in ("WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"):
        assert f"-u {variable}" in recipe.group(1)
    assert re.search(r"^\s*make test\b", _read("PKGBUILD"), re.MULTILINE)


def test_makefile_uninstall_keeps_what_it_did_not_install() -> None:
    """With PREFIX=~/.local the data directory also holds the Whisper models and the wallpapers."""
    recipe = re.search(r"^uninstall:\n((?:\t.*\n)+)", _read("Makefile"), re.MULTILINE)
    assert recipe, "uninstall target not found"
    removals = [line for line in recipe.group(1).splitlines() if line.strip().startswith("rm -rf")]
    assert not [line for line in removals if '"$(DEST)$(DATADIR)"' in line], "removes the whole data directory"
    assert any("$(DATADIR)/shell" in line and "$(DATADIR)/integrations" in line for line in removals)


def test_update_instructions_force_a_rebuild() -> None:
    """pkgver does not change between commits: without -f makepkg reinstalls the previous build."""
    for name, heading in (("README.it.md", "**Aggiornare**"), ("README.md", "**Updating**")):
        update = _read(name).split(heading, 1)[1].split("\n## ", 1)[0]
        assert "makepkg -sif" in update
        assert not re.search(r"makepkg -si(?!f)", update)
    assert "makepkg -sif" in _read("PKGBUILD").split("pkgname=", 1)[0]


# --------------------------------------------------------------------------------------------
# install.sh and uninstall.sh, run for real in a private home directory
# --------------------------------------------------------------------------------------------


def _posix(path: Path | str) -> str:
    """A path as bash sees it (``/c/Users/...`` for Git Bash on the Windows development machine)."""
    text = str(path)
    if os.name != "nt":
        return text
    drive, rest = os.path.splitdrive(text)
    return "/" + drive.rstrip(":").lower() + rest.replace("\\", "/")


class Sandbox:
    """A private home directory with the environment to run the scripts in it."""

    def __init__(self, base: Path) -> None:
        self.home = base / "home"
        self.bin = base / "bin"
        self.config = self.home / ".config"
        self.data = self.home / ".local" / "share"
        self.state = self.home / ".local" / "state"
        self.cache = self.home / ".cache"
        for folder in (self.bin, self.config, self.data, self.state, self.cache):
            folder.mkdir(parents=True)
        self.add_tool("uname", "#!/bin/sh\necho Linux\n")  # the installer refuses anything else
        # The real interpreter, minus the slow byte-compiling that the tests do not need.
        self.python = self.bin / "python-for-tests"
        self.add_tool(
            self.python.name,
            '#!/bin/sh\nif [ "$1 $2" = "-m compileall" ]; then exit 0; fi\n'
            f"exec '{_posix(sys.executable)}' \"$@\"\n",
        )
        self.env = dict(os.environ)
        for variable in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "DROPWHEEL_DATA", "LC_MESSAGES", "PYTHONPATH"):
            self.env.pop(variable, None)
        self.env.update(
            HOME=_posix(self.home),
            XDG_CONFIG_HOME=_posix(self.config),
            XDG_DATA_HOME=_posix(self.data),
            XDG_STATE_HOME=_posix(self.state),
            XDG_CACHE_HOME=_posix(self.cache),
            PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            LANG="C",
            LC_ALL="C",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONUTF8="1",
        )

    def add_tool(self, name: str, script: str) -> None:
        tool = self.bin / name
        tool.write_bytes(script.encode())
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)

    def run(self, script: Path | str, *args: str, cwd: Path | None = None, env: dict | None = None):
        return subprocess.run(
            [BASH, _posix(script), *args],
            cwd=cwd or self.home,
            env={**self.env, **(env or {})},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

    def install(self, *args: str, env: dict | None = None):
        """install.sh without the steps that need a session, unless ``args`` say otherwise."""
        result = self.run(
            ROOT / "install.sh", "--no-start", "--no-file-managers", "--yes", "--python", _posix(self.python),
            *args, env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    def uninstall(self, *args: str, script: Path | str | None = None):
        result = self.run(script or ROOT / "uninstall.sh", *args)
        assert result.returncode == 0, result.stdout + result.stderr
        return result


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    if BASH is None:
        pytest.skip("bash not available")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("the scripts refuse to run as root")
    return Sandbox(tmp_path)


def _user_data(box: Sandbox) -> list[Path]:
    """What the user has in the same folder as the program, and in the XDG directories."""
    files = [
        box.data / "dropwheel" / "whisper" / "ggml-base.bin",
        box.data / "dropwheel" / "wallpapers" / "sfondo.png",
        box.config / "dropwheel" / "config.toml",
        box.state / "dropwheel" / "history.json",
        box.cache / "dropwheel" / "thumb.png",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("user data", encoding="utf-8")
    return files


def test_uninstall_keeps_the_user_data_in_the_data_directory(sandbox: Sandbox) -> None:
    sandbox.install("--no-hyprland", "--no-mime-guard")
    data_dir = sandbox.data / "dropwheel"
    launcher = sandbox.home / ".local" / "bin" / "dropwheel"
    assert launcher.is_file() and (data_dir / "shell" / "shell.qml").is_file()
    files = _user_data(sandbox)
    other = data_dir / "my-notes.txt"
    other.write_text("not ours", encoding="utf-8")
    # what an update interrupted half way leaves behind
    (data_dir / ".shell.old").mkdir()
    (data_dir / ".integrations.new.abc123").mkdir()

    result = sandbox.uninstall(script=data_dir / "uninstall.sh")

    for path in files + [other]:
        assert path.read_text(encoding="utf-8") in ("user data", "not ours"), path
    assert sorted(p.name for p in data_dir.iterdir()) == ["my-notes.txt", "wallpapers", "whisper"]
    assert not launcher.exists()
    assert not (sandbox.data / "applications" / "dropwheel.desktop").exists()
    assert "--purge" in result.stdout and _posix(files[0].parent) in result.stdout


def test_uninstall_purge_needs_a_confirmation_and_removes_the_user_data(sandbox: Sandbox) -> None:
    sandbox.install("--no-hyprland", "--no-mime-guard")
    data_dir = sandbox.data / "dropwheel"
    files = _user_data(sandbox)

    # --purge without a terminal and without --yes deletes nothing
    result = sandbox.uninstall("--purge", script=data_dir / "uninstall.sh")
    assert all(path.is_file() for path in files)
    assert _posix(files[0].parent) in result.stdout, "the confirmation must list the Whisper models"
    assert _posix(files[1].parent) in result.stdout, "the confirmation must list the wallpapers"

    sandbox.uninstall("--purge", "--yes")
    assert not any(path.exists() for path in files)
    assert not (sandbox.config / "dropwheel").exists()
    assert not data_dir.exists(), "the data directory is removed once it is empty"


def test_uninstall_removes_the_data_directory_when_nothing_else_is_there(sandbox: Sandbox) -> None:
    sandbox.install("--no-hyprland", "--no-mime-guard")
    sandbox.uninstall(script=sandbox.data / "dropwheel" / "uninstall.sh")
    assert not (sandbox.data / "dropwheel").exists()


def test_installed_uninstaller_removes_its_own_prefix(sandbox: Sandbox) -> None:
    """The command printed at the end of ``install.sh --prefix`` must work exactly as printed."""
    prefix = sandbox.home / "opt" / "dw"
    result = sandbox.install("--no-hyprland", "--no-mime-guard", "--prefix", _posix(prefix))
    script = prefix / "share" / "dropwheel" / "uninstall.sh"
    assert _posix(script) in result.stdout
    assert (prefix / "bin" / "dropwheel").is_file()

    result = sandbox.uninstall(script=script)

    assert "No user installation" not in result.stdout + result.stderr
    assert not (prefix / "bin" / "dropwheel").exists()
    assert not (prefix / "share" / "dropwheel").exists()
    assert not (prefix / "share" / "applications" / "dropwheel.desktop").exists()


def test_uninstall_with_no_installation_leaves_the_configuration_alone(sandbox: Sandbox) -> None:
    """A wrong prefix (or only the system package): the Hyprland wiring belongs to the other install."""
    loader = sandbox.config / "caelestia" / "hypr-user.lua"
    module = sandbox.config / "caelestia" / "dropwheel.lua"
    loader.parent.mkdir()
    loader.write_text('hl.bind("SUPER + Q", hl.dsp.kill())\nrequire("dropwheel")\n', encoding="utf-8")
    module.write_text("-- module\n", encoding="utf-8")
    nautilus = sandbox.data / "nautilus" / "scripts" / "Dropwheel"
    nautilus.parent.mkdir(parents=True)
    nautilus.write_text("#!/bin/sh\n# dropwheel\n", encoding="utf-8")
    whisper = sandbox.data / "dropwheel" / "whisper" / "ggml-base.bin"
    whisper.parent.mkdir(parents=True)
    whisper.write_text("model", encoding="utf-8")

    result = sandbox.uninstall()
    assert "No user installation" in result.stderr
    assert "has been uninstalled" not in result.stdout
    assert 'require("dropwheel")' in loader.read_text(encoding="utf-8")
    assert module.is_file() and nautilus.is_file() and whisper.is_file()
    assert not list(loader.parent.glob("*.dropwheel-bak-*"))

    # --yes is an explicit request to clean up what is left
    sandbox.uninstall("--yes")
    assert 'require("dropwheel")' not in loader.read_text(encoding="utf-8")
    assert not nautilus.exists() and whisper.is_file()


def test_user_launcher_never_imports_from_the_current_directory(sandbox: Sandbox, tmp_path: Path) -> None:
    sandbox.install("--no-hyprland", "--no-mime-guard")
    launcher = sandbox.home / ".local" / "bin" / "dropwheel"
    assert " -P -m dropwheel " in launcher.read_text(encoding="utf-8")
    downloads = tmp_path / "downloads"
    (downloads / "dropwheel").mkdir(parents=True)
    trap = "print('TRAPPED: code from the current directory ran')\n"
    (downloads / "argparse.py").write_text(trap, encoding="utf-8")
    (downloads / "dropwheel" / "__init__.py").write_text(trap, encoding="utf-8")

    result = sandbox.run(launcher, "--version", cwd=downloads)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "TRAPPED" not in result.stdout + result.stderr
    assert "Dropwheel" in result.stdout


@pytest.mark.parametrize(
    ("caelestia", "lang", "wired", "reminder"),
    [
        (True, "C", "Hyprland configured", None),
        (False, "C", None, "The shortcuts need: dropwheel setup hyprland"),
        (False, "it_IT.UTF-8", None, "Per le scorciatoie serve prima: dropwheel setup hyprland"),
    ],
)
def test_install_reports_hyprland_as_configured_only_when_it_is(
    sandbox: Sandbox, caelestia: bool, lang: str, wired: str | None, reminder: str | None
) -> None:
    """Without Caelestia ``dropwheel setup hyprland`` copies the module and only prints what to add."""
    sandbox.add_tool("hyprctl", "#!/bin/sh\nexit 0\n")
    (sandbox.config / "caelestia").mkdir()  # also there with a Hyprland configuration of one's own
    (sandbox.config / "hypr").mkdir()
    if caelestia:
        (sandbox.config / "hypr" / "hyprland.lua").write_text('require("hypr-user")\n', encoding="utf-8")
    result = sandbox.install("--no-mime-guard", env={"LANG": lang, "LC_ALL": lang})
    if wired:
        assert wired in result.stdout
        assert 'require("dropwheel")' in (sandbox.config / "caelestia" / "hypr-user.lua").read_text(encoding="utf-8")
        assert "dropwheel setup hyprland" not in result.stdout.split("==>")[-1]
    else:
        assert "Hyprland configured" not in result.stdout and "Hyprland configurato" not in result.stdout
        assert reminder in result.stdout
        assert (sandbox.config / "hypr" / "dropwheel.lua").is_file()


def test_install_ignores_a_require_in_a_hypr_user_file_that_nothing_loads(sandbox: Sandbox) -> None:
    (sandbox.config / "caelestia").mkdir()
    (sandbox.config / "caelestia" / "hypr-user.lua").write_text('require("dropwheel")\n', encoding="utf-8")
    (sandbox.config / "hypr").mkdir()
    (sandbox.config / "hypr" / "hyprland.lua").write_text('hl.bind("SUPER + Q", hl.dsp.kill())\n', encoding="utf-8")
    result = sandbox.install("--no-mime-guard", "--no-start")
    assert "already wired" not in result.stdout
    assert "The shortcuts need: dropwheel setup hyprland" in result.stdout


FAKE_XDG_MIME = """#!/bin/sh
# Every type in $DW_FAKE_TYPES has a default until the new desktop entry takes it over.
if [ "$1" = default ]; then echo "$3" >> "$DW_FAKE_PINNED"; exit 0; fi
for mime in $DW_FAKE_TYPES; do
    if [ "$3" = "$mime" ]; then
        if [ -e "$XDG_DATA_HOME/applications/dropwheel.desktop" ]; then
            echo dropwheel.desktop
        else
            echo viewer.desktop
        fi
    fi
done
"""


@pytest.mark.parametrize(
    ("types", "lang", "expected"),
    [
        ("image/png", "it_IT.UTF-8", "Ripristinata 1 applicazione predefinita"),
        ("image/png application/pdf", "C", "Restored 2 default applications"),
    ],
)
def test_mime_guard_restores_the_previous_defaults(
    sandbox: Sandbox, tmp_path: Path, types: str, lang: str, expected: str
) -> None:
    sandbox.add_tool("xdg-mime", FAKE_XDG_MIME)
    pinned = tmp_path / "pinned"
    result = sandbox.install(
        "--no-hyprland",
        env={"LANG": lang, "LC_ALL": lang, "DW_FAKE_TYPES": types, "DW_FAKE_PINNED": _posix(pinned)},
    )
    assert expected in result.stdout
    assert pinned.read_text(encoding="utf-8").split() == types.split()


# --------------------------------------------------------------------------------------------
# Desktop entries, file-manager integrations
# --------------------------------------------------------------------------------------------


def test_desktop_entry() -> None:
    entry = _desktop_file("integrations/desktop/dropwheel.desktop")
    main = entry["Desktop Entry"]
    assert main["Type"] == "Application"
    assert main["Name"] == "Dropwheel"
    assert main["Exec"] == "dropwheel open %F"
    assert main["Icon"] == "dropwheel"
    assert main["Categories"] == "Utility;FileTools;"
    assert main["GenericName"] and main["GenericName[it]"]
    assert main["Comment"] and main["Comment[it]"]
    mimes = main["MimeType"].split(";")
    assert mimes[-1] == "", "MimeType must end with ';'"
    for mime in ("image/png", "application/pdf", "application/zip", "video/mp4"):
        assert mime in mimes
    # xdg-open picks the first entry of mimeinfo.cache when a type has no explicit default, and
    # nobody wants folders or unknown files to pop up the wheel: file managers have their actions.
    for mime in ("application/octet-stream", "inode/directory"):
        assert mime not in mimes
    assert not [m for m in mimes if "*" in m], "wildcards would claim every type of a family"
    assert len(mimes) == len(set(mimes)), "duplicate MIME types"
    assert main["Actions"] == "start;stop;"
    assert entry["Desktop Action start"]["Exec"] == "dropwheel start"
    assert entry["Desktop Action stop"]["Exec"] == "dropwheel stop"
    for section in entry.sections():
        for key, value in entry[section].items():
            assert value == value.strip(), f"{section}/{key} has stray whitespace"


def test_dolphin_service_menu() -> None:
    menu = _desktop_file("integrations/dolphin/dropwheel.desktop")
    main = menu["Desktop Entry"]
    assert main["Type"] == "Service"
    assert main["MimeType"] == "all/all;inode/directory;"
    assert main["X-KDE-Priority"] == "TopLevel"
    actions = [a for a in main["Actions"].split(";") if a]
    assert actions
    for action in actions:
        section = menu[f"Desktop Action {action}"]
        assert section["Exec"] == "dropwheel open %F"
        assert section["Name"] and section["Name[it]"]


def test_nemo_action() -> None:
    action = _desktop_file("integrations/nemo/dropwheel.nemo_action")["Nemo Action"]
    assert action["Exec"] == "dropwheel open %F"
    assert action["Selection"] == "notnone"
    assert action["Extensions"] == "any;"
    assert action["Name"] and action["Name[it]"]


def test_thunar_action_snippet() -> None:
    tree = ET.parse(INTEGRATIONS / "thunar" / "uca-action.xml")
    action = tree.getroot()
    assert action.tag == "action"
    assert action.findtext("command") == "dropwheel open %F"
    assert action.findtext("patterns") == "*"
    assert action.findtext("icon") == "dropwheel"
    assert action.findtext("unique-id")
    for kind in ("directories", "audio-files", "image-files", "other-files", "text-files", "video-files"):
        assert action.find(kind) is not None, kind
    assert (INTEGRATIONS / "thunar" / "README.md").is_file()


def test_nautilus_script_reads_selection_variable() -> None:
    text = _read("integrations/nautilus/Dropwheel")
    assert "NAUTILUS_SCRIPT_SELECTED_FILE_PATHS" in text
    assert 'open "${files[@]}"' in text


@pytest.mark.skipif(os.name != "posix" or BASH is None, reason="needs a POSIX shell environment")
def test_nautilus_script_passes_absolute_paths(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "args"
    fake = bin_dir / "dropwheel"
    fake.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\0" "$a"; done > "{log}"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    folder = tmp_path / "dir"
    folder.mkdir()
    names = ["a b.png", "-dash.pdf", "perché.txt"]
    for name in names:
        (folder / name).write_text("x")
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    env.pop("NAUTILUS_SCRIPT_SELECTED_FILE_PATHS", None)
    script = ROOT / "integrations" / "nautilus" / "Dropwheel"

    # 1) arguments only (relative names, cwd = folder)
    subprocess.run([BASH, str(script), *names], cwd=folder, env=env, check=True)
    args = log.read_bytes().decode().split("\0")[:-1]
    assert args[0] == "open"
    assert args[1:] == [str(folder / name) for name in names]

    # 2) the Nautilus variable (newline separated) wins over the arguments
    env["NAUTILUS_SCRIPT_SELECTED_FILE_PATHS"] = "\n".join(str(folder / n) for n in names[:2]) + "\n"
    subprocess.run([BASH, str(script), "ignored"], cwd=folder, env=env, check=True)
    args = log.read_bytes().decode().split("\0")[:-1]
    assert args[1:] == [str(folder / name) for name in names[:2]]

    # 3) nothing selected → the current folder
    env.pop("NAUTILUS_SCRIPT_SELECTED_FILE_PATHS")
    subprocess.run([BASH, str(script)], cwd=folder, env=env, check=True)
    args = log.read_bytes().decode().split("\0")[:-1]
    assert args[1:] == [str(folder)]


# --------------------------------------------------------------------------------------------
# Icon
# --------------------------------------------------------------------------------------------


def test_icon_is_a_self_contained_square_svg() -> None:
    text = _read("assets/dropwheel.svg")
    root = ET.fromstring(text)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox") == "0 0 256 256"
    assert "href" not in text, "the icon must not reference external resources"
    for tag in ("filter", "image", "script", "foreignObject"):
        assert f"<{tag}" not in text


# --------------------------------------------------------------------------------------------
# Hyprland Lua module
# --------------------------------------------------------------------------------------------

LUA_MOCK = r"""
calls = { binds = {}, rules = {}, events = {}, execs = {} }
local function handle(kind)
  return { kind = kind, enabled = true, set_enabled = function(self, v) self.enabled = v end }
end
hl = {
  dsp = { global = function(name) return { dispatcher = "global", name = name } end },
  bind = function(keys, dispatcher, opts)
    assert(type(keys) == "string" and type(dispatcher) == "table" and type(opts) == "table")
    table.insert(calls.binds, { combo = keys, name = dispatcher.name, opts = opts })
    return handle("bind")
  end,
  layer_rule = function(rule)
    table.insert(calls.rules, rule)
    return handle("rule")
  end,
  on = function(event, fn)
    assert(type(fn) == "function")
    calls.events[event] = fn
  end,
  exec_cmd = function(cmd) table.insert(calls.execs, cmd) end,
}
"""


def _lua_runtime():
    lupa = pytest.importorskip("lupa")
    runtime_cls = getattr(lupa, "LuaRuntime", None)
    if runtime_cls is None:  # lupa >= 2 exposes per-version modules
        import importlib

        for name in ("lupa.lua54", "lupa.lua53", "lupa.luajit21"):
            try:
                runtime_cls = importlib.import_module(name).LuaRuntime
                break
            except ImportError:
                continue
    if runtime_cls is None:
        pytest.skip("no Lua runtime in lupa")
    return runtime_cls(unpack_returned_tuples=True)


def _lua_list(table) -> list:
    return [table[i] for i in range(1, len(table) + 1)]


@pytest.mark.parametrize("name, label", [("README.md", "| Layer rules |"), ("README.it.md", "| Regole dei layer |")])
def test_readme_describes_the_layer_rules_of_the_module(name, label) -> None:
    """The dialog is full-screen with its own scrim: blurring it would blur the whole screen."""
    row = next(line for line in _read(name).splitlines() if line.startswith(label))
    blurred, _, plain = row.partition(";")
    assert "dropwheel-wheel" in blurred and "dropwheel-hud" in blurred
    assert "dropwheel-dialog" not in blurred and "dropwheel-dialog" in plain


def test_lua_module_compiles_and_runs_without_hyprland() -> None:
    lua = _lua_runtime()
    source = _read("integrations/hyprland/dropwheel.lua")
    module = lua.execute(source)
    assert len(module.binds) == 0 and len(module.errors) == 0


def test_lua_module_registers_binds_rules_and_autostart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LANG", "it_IT.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    lua = _lua_runtime()
    lua.execute(LUA_MOCK)
    module = lua.execute(_read("integrations/hyprland/dropwheel.lua"))
    calls = lua.globals().calls

    binds = {b.combo: b for b in _lua_list(calls.binds)}
    for key in ("Shift_L", "Shift_R"):
        bind = binds[key]
        assert bind.name == "dropwheel:shift"
        assert bind.opts.non_consuming is True and bind.opts.ignore_mods is True
        assert "Dropwheel" in bind.opts.description
    assert binds["SUPER + ALT + D"].name == "dropwheel:open"
    assert "appunti" in binds["SUPER + ALT + D"].opts.description  # Italian session

    rules = {r.match.namespace: r for r in _lua_list(calls.rules)}
    surfaces = rules["dropwheel-(wheel|hud)"]
    assert surfaces.blur is True and surfaces.ignore_alpha == pytest.approx(0.3)
    assert surfaces.no_anim is True
    plain = rules["dropwheel-(dialog|hotzone)"]
    assert plain.no_anim is True and not plain.blur  # full-screen dialog: never blur the whole screen
    for ns in ("dropwheel-wheel", "dropwheel-hud", "dropwheel-dialog", "dropwheel-hotzone"):
        matching = [r for r in rules.values() if re.fullmatch(r.match.namespace, ns)]
        assert len(matching) == 1, ns
    assert not any(re.fullmatch(r.match.namespace, "caelestia-bar") for r in rules.values())

    calls.events["hyprland.start"]()
    assert _lua_list(calls.execs) == ["dropwheel start"]
    assert module.command == "dropwheel"
    assert len(module.errors) == 0

    module.set_enabled(False)
    assert all(not h.enabled for h in _lua_list(module.binds))
    assert all(not h.enabled for h in _lua_list(module.rules))


def test_lua_module_prefers_the_user_launcher(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "it's home"
    launcher = home / ".local" / "bin" / "dropwheel"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    monkeypatch.setenv("HOME", str(home))
    lua = _lua_runtime()
    lua.execute(LUA_MOCK)
    module = lua.execute(_read("integrations/hyprland/dropwheel.lua"))
    # The module joins $HOME and "/.local/bin/dropwheel" itself (also on the dev machine).
    expected = "'" + f"{home}/.local/bin/dropwheel".replace("'", "'\\''") + "'"
    assert module.command == expected
    lua.globals().calls.events["hyprland.start"]()
    assert _lua_list(lua.globals().calls.execs) == [expected + " start"]


def test_lua_module_survives_a_failing_bind() -> None:
    lua = _lua_runtime()
    lua.execute(LUA_MOCK)
    lua.execute(
        "local real = hl.bind; hl.bind = function(k, d, o) "
        "if k == 'Shift_R' then error('bad key') end return real(k, d, o) end"
    )
    module = lua.execute(_read("integrations/hyprland/dropwheel.lua"))
    calls = lua.globals().calls
    assert len(module.errors) == 1
    assert len(calls.rules) == 2, "an invalid bind must not stop the rest of the module"
    assert any("notify-send" in cmd for cmd in _lua_list(calls.execs))


def test_readme_states_the_platform_scope_and_links_exist() -> None:
    """The English README says what runs today and where it is going, and its local links resolve."""
    readme = _read("README.md")
    assert "currently runs on Arch Linux with Hyprland only" in readme
    assert "extend Dropwheel to other operating systems" in readme
    italian = _read("README.it.md")
    assert "funziona solo su Arch Linux con Hyprland" in italian
    assert "estendere\nDropwheel ad altri sistemi operativi" in italian
    targets = re.findall(r"""(?:\]\(|src="|href=")([^)"#\s]+)""", readme)
    local = [t for t in targets if not t.startswith(("http://", "https://"))]
    assert local, "no local links found"
    assert not [t for t in local if not (ROOT / t).exists()], "broken local links"
