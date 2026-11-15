"""Start, stop and talk to the Windows host (``shellctl`` delegates here on Windows).

The host listens on ``127.0.0.1`` on a random port and writes ``{pid, port, token}`` to
:func:`state_path` in the per-user cache.  Every request carries the token, so another user on
the same machine cannot drive the host: one JSON line in, one JSON line out.  No Qt is imported
here, so the command line and the Explorer entries stay fast.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .. import util
from ..errors import UserError

MODULE = "dropwheel.winshell"


def state_path() -> Path:
    return util.cache_dir() / "winshell.json"


def log_path() -> Path:
    return util.cache_dir() / "winshell.log"


def gui_python() -> str:
    """``pythonw.exe`` next to the running interpreter (no console window), else the interpreter."""
    exe = Path(sys.executable or "python")
    if os.name == "nt":
        cand = exe.with_name("pythonw.exe")
        if cand.is_file():
            return str(cand)
    return str(exe)


def host_command() -> list[str]:
    return [gui_python(), "-P", "-m", MODULE]


def _read_state() -> dict | None:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("port"), int) and isinstance(data.get("token"), str):
        return data
    return None


def request(fn: str, args: list | None = None, *, timeout: float = 5.0) -> Any:
    """Send one call to the host; returns its result.  Raises :class:`UserError`."""
    state = _read_state()
    if state is None:
        raise UserError("shell.err.not_running")
    line = json.dumps({"token": state["token"], "fn": fn, "args": list(args or [])}, ensure_ascii=False) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", state["port"]), timeout=timeout) as sock:
            sock.sendall(line.encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except OSError:
        raise UserError("shell.err.not_running") from None
    try:
        reply = json.loads(buf.decode("utf-8"))
    except ValueError:
        raise UserError("shell.err.ipc_failed", details=buf[-3000:].decode("utf-8", "replace"), fn=fn) from None
    if not isinstance(reply, dict) or not reply.get("ok"):
        details = str(reply.get("error", "")) if isinstance(reply, dict) else ""
        raise UserError("shell.err.ipc_failed", details=details, fn=fn)
    return reply.get("result")


def ping(timeout: float = 2.0) -> bool:
    try:
        return request("ping", timeout=timeout) == "pong"
    except UserError:
        return False


def running() -> bool:
    return ping()


def wait_ready(timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if ping(timeout=max(0.5, min(2.0, deadline - time.monotonic()))):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def instances() -> list[dict]:
    state = _read_state()
    return [{"pid": state.get("pid")}] if state and ping() else []


def start(shell: Path, env: dict[str, str], wait: float = 5.0) -> dict:
    info = {"started": False, "already": False, "responding": None, "shell": str(shell), "log": str(log_path())}
    if ping():
        info.update(already=True, responding=True)
        return info
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(host_command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env, close_fds=True, cwd=str(Path.home()), **kwargs)
    except OSError as exc:
        raise UserError("shell.err.start_failed", details=str(exc), log=str(log_path())) from None
    info["started"] = True
    # the first start loads Qt and the QML: give it longer than a ping
    info["responding"] = wait_ready(max(wait, 15.0)) if wait > 0 else None
    return info


def stop(timeout: float = 10.0) -> bool:
    if not ping():
        return False
    try:
        request("quit", timeout=timeout)
    except UserError:
        pass
    deadline = time.monotonic() + timeout
    while ping(timeout=1.0) and time.monotonic() < deadline:
        time.sleep(0.2)
    return True


def run_foreground(env: dict[str, str]) -> int:
    try:
        return subprocess.call([sys.executable or "python", "-P", "-m", MODULE, "--foreground"], env=env)
    except KeyboardInterrupt:
        return 130
