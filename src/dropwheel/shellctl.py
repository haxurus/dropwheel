"""Locate the QML shell and control its Quickshell instance (SPEC §8).

* :func:`data_dir` — ``$DROPWHEEL_DATA``, then the directory two levels above this package
  when it holds ``shell/shell.qml`` (source checkout ``<repo>/src/dropwheel`` and the user-local
  install ``~/.local/share/dropwheel/python/dropwheel`` both resolve to their own data), then
  ``$XDG_DATA_HOME/dropwheel``, ``/usr/local/share/dropwheel``, ``/usr/share/dropwheel``.
* The shell is always launched **by path**: ``qs -p <data>/shell -n -d`` with
  ``DROPWHEEL_BACKEND="<sys.executable> -P -m dropwheel daemon"``, ``PYTHONPATH`` and
  ``DROPWHEEL_DATA`` exported, so the shell talks to the same Python package that started it.
* IPC: ``qs ipc -p <data>/shell call dropwheel <fn> <args…>``; status: ``qs list -p … -j``;
  stop: ``qs kill -p …`` (repeated until no instance is left).  Instance selection options
  (``-p``, ``--any-display``) are given after the subcommand, as in ``qs ipc -c <name> call``.

``qs`` filters instances by ``WAYLAND_DISPLAY`` (since 0.3.0): ``list``/``kill`` always pass
``--any-display``; IPC calls pass it only when no display is set (e.g. from a TTY).  An older
``qs`` that rejects the flag is retried without it.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import tools, util
from .errors import UserError

log = logging.getLogger("dropwheel.shellctl")

__all__ = ["IPC_TARGET", "package_root", "data_dir", "data_candidates", "shell_dir", "integrations_dir", "qs_path",
           "backend_command", "shell_env", "instances", "running", "ipc_call", "ping", "wait_ready", "start",
           "stop", "restart", "run_foreground", "status", "log_command", "start_log_path"]

#: ``IpcHandler { target: "dropwheel" }`` in shell.qml.
IPC_TARGET = "dropwheel"


# ===================================================================================== paths

def package_root() -> Path:
    """The directory that contains the ``dropwheel`` package (goes on ``PYTHONPATH``)."""
    return Path(__file__).resolve().parent.parent


def _has_shell(base: Path) -> bool:
    return (base / "shell" / "shell.qml").is_file()


def data_candidates() -> list[Path]:
    """Every data directory considered by :func:`data_dir`, in order."""
    out: list[Path] = []
    env = os.environ.get("DROPWHEEL_DATA", "").strip()
    if env:
        out.append(Path(os.path.expanduser(env)))
    out.append(package_root().parent)
    out.append(util.xdg_data_home() / "dropwheel")
    for base in util.xdg_data_dirs():
        out.append(base / "dropwheel")
    for fixed in (Path("/usr/local/share/dropwheel"), Path("/usr/share/dropwheel")):
        if fixed not in out:
            out.append(fixed)
    unique: list[Path] = []
    for p in out:
        if p not in unique:
            unique.append(p)
    return unique


def data_dir() -> Path:
    """The data directory holding ``shell/`` and ``integrations/``.

    ``$DROPWHEEL_DATA`` always wins (even when it is wrong, so errors point at it); otherwise
    the first candidate that contains ``shell/shell.qml``; otherwise ``$XDG_DATA_HOME/dropwheel``.
    """
    env = os.environ.get("DROPWHEEL_DATA", "").strip()
    if env:
        return Path(os.path.expanduser(env)).resolve()
    for cand in data_candidates():
        if _has_shell(cand):
            return cand.resolve()
    return (util.xdg_data_home() / "dropwheel").resolve()


def shell_dir() -> Path:
    """``<data>/shell`` (passed to ``qs -p``)."""
    return data_dir() / "shell"


def integrations_dir() -> Path:
    """``<data>/integrations``."""
    return data_dir() / "integrations"


def qs_path() -> str:
    """Absolute path of ``qs``; raises :class:`UserError` with the install command when missing."""
    path = tools.path("qs")
    if path is None:
        raise UserError("shell.err.no_qs", install=tools.install_hint(["qs"]))
    return path


def _require_shell() -> Path:
    shell = shell_dir()
    if not (shell / "shell.qml").is_file():
        raise UserError("shell.err.no_shell", details="\n".join(str(c) for c in data_candidates()),
                        path=str(shell))
    return shell


def backend_command() -> list[str]:
    """argv the shell uses to start the daemon (same interpreter as the caller).

    ``-P``: ``-m`` would otherwise put the current directory first on ``sys.path``, where a stray
    ``json.py`` or ``dropwheel/`` would run instead of the real modules."""
    return [sys.executable or "python3", "-P", "-m", "dropwheel", "daemon"]


def _needs_pythonpath(root: Path) -> bool:
    return root.name not in ("site-packages", "dist-packages")


def shell_env() -> dict[str, str]:
    """Environment for ``qs``: ``DROPWHEEL_BACKEND``, ``DROPWHEEL_DATA`` and (for checkouts and
    user-local installs) ``PYTHONPATH``."""
    env = os.environ.copy()
    env["DROPWHEEL_BACKEND"] = shlex.join(backend_command())
    env["DROPWHEEL_DATA"] = str(data_dir())
    root = package_root()
    if _needs_pythonpath(root):
        parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        if str(root) not in parts:
            parts.insert(0, str(root))
        env["PYTHONPATH"] = os.pathsep.join(parts)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def log_command() -> str:
    """The command that shows the shell log (for messages)."""
    return f"qs log -p {shlex.quote(str(shell_dir()))}"


# ======================================================================================= qs

def _run_qs(args: list[str], *, timeout: float = 10.0, env: dict | None = None) -> subprocess.CompletedProcess:
    qs = qs_path()
    try:
        return subprocess.run([qs, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise UserError("err.timeout", tool="qs", seconds=int(timeout)) from None
    except OSError as exc:
        raise UserError("err.tool_failed", details=str(exc), tool="qs", code="?") from None


#: ``False`` once ``qs`` rejected ``--any-display`` (Quickshell < 0.3.0 has no display filter).
_ANY_DISPLAY_OK: bool | None = None


def _rejected_option(res: subprocess.CompletedProcess, option: str) -> bool:
    """``True`` when CLI11 refused ``option`` ("The following argument was not expected: …")."""
    if res.returncode == 0:
        return False
    text = ((res.stdout or "") + "\n" + (res.stderr or "")).lower()
    return option in text and any(w in text for w in ("not expected", "unrecognized", "unknown option",
                                                       "extras"))


def _run_qs_filtered(args: list[str], *, any_display: bool, timeout: float) -> subprocess.CompletedProcess:
    """``qs <subcommand> [--any-display] <rest…>``.

    ``--any-display`` (Quickshell ≥ 0.3.0) disables the ``WAYLAND_DISPLAY`` instance filter; a
    ``qs`` that does not know it is retried without it (and not asked again).
    """
    global _ANY_DISPLAY_OK
    if any_display and _ANY_DISPLAY_OK is not False:
        res = _run_qs([args[0], "--any-display", *args[1:]], timeout=timeout)
        if not _rejected_option(res, "--any-display"):
            _ANY_DISPLAY_OK = True
            return res
        _ANY_DISPLAY_OK = False
        log.info("qs does not support --any-display; retrying without it")
    return _run_qs(args, timeout=timeout)


def _has_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def instances() -> list[dict]:
    """Running instances of our shell (``qs list --any-display -p <shell> -j``)."""
    shell = shell_dir()
    if not (shell / "shell.qml").is_file() or tools.path("qs") is None:
        return []
    res = _run_qs_filtered(["list", "-p", str(shell), "-j"], any_display=True, timeout=10)
    text = (res.stdout or "").strip()
    if not text.startswith(("[", "{")):
        return []  # "No running instances for …"
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        inner = data.get("instances")
        data = inner if isinstance(inner, list) else [data]
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def running() -> bool:
    """``True`` when an instance of the shell is running."""
    try:
        return bool(instances())
    except UserError:
        return False


def ipc_call(fn: str, *args: Any, timeout: float = 5.0) -> str:
    """``qs ipc -p <shell> call dropwheel <fn> <args…>``; returns stdout (stripped).

    Raises :class:`UserError` (``shell.err.not_running`` / ``shell.err.ipc_failed``).
    """
    shell = _require_shell()
    argv = ["ipc", "-p", str(shell), "call", IPC_TARGET, str(fn)]
    argv += [str(a).lower() if isinstance(a, bool) else str(a) for a in args]
    # from a TTY or a file-manager script without WAYLAND_DISPLAY the display filter would hide
    # the instance: only then is --any-display needed
    res = _run_qs_filtered(argv, any_display=not _has_display(), timeout=timeout)
    output = ((res.stdout or "") + (res.stderr or "")).strip()
    if res.returncode != 0:
        if "No running instances" in output or "no running instance" in output.lower():
            raise UserError("shell.err.not_running")
        raise UserError("shell.err.ipc_failed", details=output[-3000:], fn=fn)
    return (res.stdout or "").strip()


def ping(timeout: float = 2.0) -> bool:
    """``True`` when the shell answers ``ping`` with ``pong``."""
    try:
        return ipc_call("ping", timeout=timeout).strip().strip('"') == "pong"
    except UserError:
        return False


def wait_ready(timeout: float = 5.0) -> bool:
    """Poll :func:`ping` until it answers or ``timeout`` seconds pass."""
    deadline = time.monotonic() + timeout
    while True:
        if ping(timeout=max(0.5, min(2.0, deadline - time.monotonic()))):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def _require_display() -> None:
    if not _has_display():
        raise UserError("shell.err.no_display")


def start_log_path() -> Path:
    """File receiving the output of the last ``qs -p <shell> -n -d`` launch."""
    return util.cache_dir() / "shell-start.log"


def start(wait: float = 5.0) -> dict:
    """Start the shell daemonised (``qs -p <shell> -n -d``) unless it already runs.

    Returns ``{started, already, responding, shell, log}``; ``responding`` tells whether it
    answered ``ping`` within ``wait`` seconds.  Raises :class:`UserError` when ``qs`` or the
    shell directory is missing or ``qs`` fails to launch.
    """
    shell = _require_shell()
    qs_path()
    if running():
        return {"started": False, "already": True, "responding": ping() if wait else None, "shell": str(shell),
                "log": log_command()}
    _require_display()
    env = shell_env()
    log_file = start_log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    # A named file (truncated at every start) rather than a pipe: the daemonised instance keeps
    # the descriptor, and whatever it prints stays readable for troubleshooting.
    with open(log_file, "w+b") as out:
        kwargs: dict[str, Any] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen([qs_path(), "-p", str(shell), "-n", "-d"], stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, env=env, close_fds=True, cwd=str(Path.home()), **kwargs)
        except OSError as exc:
            raise UserError("shell.err.start_failed", details=str(exc), log=log_command()) from None
        try:
            # with -d the parent returns once the forked instance has finished launching
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            rc = None
        out.flush()
        out.seek(0)
        text = out.read(256 * 1024).decode("utf-8", "replace")
    if rc not in (0, None):
        tail = "\n".join(text.strip().splitlines()[-20:])
        raise UserError("shell.err.start_failed", details=tail, log=log_command())
    responding = wait_ready(wait) if wait > 0 else None
    return {"started": True, "already": False, "responding": responding, "shell": str(shell), "log": log_command()}


def run_foreground() -> int:
    """Run the shell in the foreground (logs on the terminal); returns qs' exit status."""
    shell = _require_shell()
    _require_display()
    try:
        return subprocess.call([qs_path(), "-p", str(shell)], env=shell_env())
    except KeyboardInterrupt:
        return 130


def stop(timeout: float = 10.0) -> bool:
    """Stop every instance of the shell (``qs kill``); ``True`` when something was stopped."""
    qs_path()
    shell = shell_dir()
    if not (shell / "shell.qml").is_file():
        return False
    stopped = False
    deadline = time.monotonic() + timeout
    for _ in range(8):
        if not instances():
            break
        res = _run_qs_filtered(["kill", "-p", str(shell)], any_display=True,
                               timeout=max(2.0, deadline - time.monotonic()))
        if res.returncode == 0:
            stopped = True
        elif time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    return stopped


def restart(wait: float = 5.0) -> dict:
    """Stop then start the shell."""
    stop()
    return start(wait=wait)


def _qs_version() -> str:
    try:
        res = _run_qs(["--version"], timeout=5)
    except UserError:
        return ""
    return (res.stdout or res.stderr or "").strip().splitlines()[0] if (res.stdout or res.stderr) else ""


def status(check_ping: bool = True) -> dict:
    """Everything ``dropwheel status`` shows (never raises)."""
    shell = shell_dir()
    qs = tools.path("qs")
    info: dict[str, Any] = {
        "data_dir": str(data_dir()), "shell_dir": str(shell), "shell_ok": (shell / "shell.qml").is_file(),
        "integrations_ok": integrations_dir().is_dir(), "qs": qs, "qs_version": _qs_version() if qs else "",
        "backend": backend_command(), "python": sys.executable, "wayland_display": os.environ.get("WAYLAND_DISPLAY", ""),
        "hyprland": bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")), "instances": [], "running": False,
        "responding": None, "log": log_command(), "error": "",
    }
    if qs and info["shell_ok"]:
        try:
            info["instances"] = instances()
        except UserError as exc:
            info["error"] = exc.text()
        info["running"] = bool(info["instances"])
        if info["running"] and check_ping:
            info["responding"] = ping()
    return info
