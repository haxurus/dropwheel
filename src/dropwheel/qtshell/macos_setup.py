"""macOS integration for the current user (``dropwheel setup macos [--remove]``).

* a Finder Quick Action, ``~/Library/Services/Open in Dropwheel.workflow``: an Automator bundle
  (``Contents/Info.plist`` declares the service, ``Contents/document.wflow`` holds one "Run Shell
  Script" action) that runs ``dropwheel open "$@"`` with the selected files and folders;
* ``~/Library/LaunchAgents/io.github.haxurus.dropwheel.plist``: launchd starts the host at every
  login.  The file is only written, not loaded with ``launchctl bootstrap``: ``dropwheel start``
  starts the host now, and a second, launchd-owned copy would only find the lock taken;
* the icon font, downloaded once like on Windows (:func:`explorer.download_font`) into
  ``~/Library/Application Support/dropwheel/fonts``, where the host loads it for itself only.

There is no global shortcut on macOS (yet): Shift during a drag, the Quick Action and
``dropwheel open`` open the wheel.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import uuid
from pathlib import Path
from typing import Any

from .. import i18n, shellctl, util
from . import control, explorer
from .gnome_setup import dropwheel_command

LABEL = "io.github.haxurus.dropwheel"
WORKFLOW = "Open in Dropwheel.workflow"
#: the Services cache: refreshed so that the Quick Action shows up without logging out
PBS = "/System/Library/CoreServices/pbs"


def workflow_path() -> Path:
    return Path.home() / "Library" / "Services" / WORKFLOW


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def shell_script() -> str:
    """What the Quick Action runs (``/bin/sh``, the selection as arguments)."""
    return f'exec {shlex.join([*dropwheel_command(), "open"])} "$@"\n'


def info_plist(label: str) -> dict[str, Any]:
    return {"NSServices": [{
        "NSMenuItem": {"default": label},
        "NSMessage": "runWorkflowAsService",
        "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
        "NSSendFileTypes": ["public.item"],
    }]}


def document_wflow(script: str) -> dict[str, Any]:
    """An Automator Quick Action with one "Run Shell Script" action (keys as Automator 2.7 saves them)."""
    names = ("inputMethod", "source", "CheckedForUserDefaultShell", "COMMAND_STRING", "shell")
    defaults = (0, "", False, "", "/bin/sh")
    action = {
        "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.path"]},
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {n: {} for n in names},
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {"COMMAND_STRING": script, "CheckedForUserDefaultShell": True, "inputMethod": 1,
                             "shell": "/bin/sh", "source": ""},
        "BundleIdentifier": "com.apple.RunShellScript",
        "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "Class Name": "RunShellScriptAction",
        "InputUUID": str(uuid.uuid4()).upper(),
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
        "OutputUUID": str(uuid.uuid4()).upper(),
        "UUID": str(uuid.uuid4()).upper(),
        "UnlocalizedApplications": ["Automator"],
        "arguments": {str(i): {"default value": d, "name": n, "required": "0", "type": "0", "uuid": str(i)}
                      for i, (n, d) in enumerate(zip(names, defaults))},
        "isViewVisible": True,
        "location": "504.500000:433.000000",
        "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/English.lproj/main.nib",
    }
    return {
        "AMApplicationBuild": "428", "AMApplicationVersion": "2.7", "AMDocumentVersion": "2",
        "actions": [{"action": action, "isViewVisible": True}],
        "connectors": {},
        "workflowMetaData": {
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def launch_agent() -> dict[str, Any]:
    """The host in the foreground under launchd, with the variables a user-local install needs."""
    env = shellctl.shell_env()
    extra = {k: env[k] for k in ("DROPWHEEL_DATA", "PYTHONPATH") if env.get(k)}
    plist: dict[str, Any] = {
        "Label": LABEL, "ProgramArguments": control.host_command(), "RunAtLoad": True,
        "ProcessType": "Interactive", "LimitLoadToSessionType": "Aqua", "WorkingDirectory": str(Path.home()),
    }
    if extra:
        plist["EnvironmentVariables"] = extra
    return plist


def font_installed() -> bool:
    if explorer.font_path().is_file():
        return True
    dirs = (Path.home() / "Library" / "Fonts", Path("/Library/Fonts"))
    return any(d.is_dir() and any(d.glob("MaterialSymbolsRounded*")) for d in dirs)


def state() -> dict[str, bool]:
    """What is installed (for ``doctor``)."""
    return {"service": (workflow_path() / "Contents" / "document.wflow").is_file(),
            "login": launch_agent_path().is_file(), "font": font_installed()}


# ================================================================================ install/remove

def _write_plist(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(plistlib.dumps(data))
    os.replace(tmp, path)


def install(lang: str, *, dry: bool, say: Any) -> int:
    """Add every entry; ``say(key, **fmt)`` reports each step.  Returns an exit status."""
    code = 0
    label = i18n.t("win.menu.open", lang)
    script = shell_script()
    if dry:
        say("cli.setup.would_create", path=str(workflow_path()), line=script.strip())
        say("cli.setup.would_create", path=str(launch_agent_path()), line=shlex.join(control.host_command()))
        if not font_installed():
            say("cli.setup.win_would_font", url=explorer.FONT_URL)
        say("cli.setup.mac_no_shortcut")
        return 0

    contents = workflow_path() / "Contents"
    try:
        _write_plist(contents / "Info.plist", info_plist(label))
        _write_plist(contents / "document.wflow", document_wflow(script))
        say("cli.setup.mac_service", label=label)
    except OSError as exc:
        say("cli.setup.write_failed", path=f"{workflow_path()} ({exc})")
        code = 1
    if os.path.exists(PBS):
        util.run_quiet([PBS, "-update"], timeout=20)  # best effort: otherwise it appears at the next login

    try:
        _write_plist(launch_agent_path(), launch_agent())
        say("cli.setup.mac_login", path=str(launch_agent_path()))
    except OSError as exc:
        say("cli.setup.write_failed", path=f"{launch_agent_path()} ({exc})")
        code = 1

    if font_installed():
        say("cli.setup.win_font_present", family=explorer.FONT_FAMILY)
    else:
        try:
            say("cli.setup.win_font_done", path=str(explorer.download_font()))
        except (OSError, ValueError) as exc:
            say("cli.setup.win_font_failed", details=str(exc), url=explorer.FONT_URL, path=str(explorer.font_path()))
            code = 1
    say("cli.setup.mac_no_shortcut")
    return code


def remove(*, dry: bool, say: Any) -> int:
    paths = [workflow_path(), launch_agent_path(), explorer.font_path()]
    if dry:
        for p in paths:
            if p.exists():
                say("cli.setup.win_would_delete", path=str(p))
        return 0
    for p in paths:
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            say("cli.setup.win_deleted", path=str(p))
        except FileNotFoundError:
            pass
        except OSError as exc:
            say("cli.setup.write_failed", path=f"{p} ({exc})")
    return 0
