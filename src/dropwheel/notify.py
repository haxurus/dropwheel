"""Desktop notifications.

:func:`notify` uses ``notify-send --app-name=Dropwheel``.  When actions are given (Open / Show in
folder…), a detached helper process (``python -m dropwheel.notify <json>``) runs
``notify-send --action=…`` (which waits for the click) and performs the chosen operation, so the
daemon never blocks.  Without ``notify-send`` the Caelestia toaster is used
(``qs -c caelestia ipc call toaster success|error|info|warn <title> <message> <icon>``).

``DROPWHEEL_NO_NOTIFY=1`` disables everything (tests, headless use).
"""

from __future__ import annotations

import html
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import APP_ID, APP_NAME, tools, util

log = logging.getLogger("dropwheel.notify")

__all__ = ["notify", "toast", "normalize_actions"]

_KIND_ICONS = {"success": "check_circle", "error": "error", "warn": "warning", "info": "info"}
_OPS = ("open", "reveal", "copy_files", "copy_text", "command")


def normalize_actions(actions: Iterable[Any] | None) -> list[dict]:
    """Accept dicts ``{id, label, op, path|paths|text|argv}`` or tuples ``(id, label, op, path)``."""
    out: list[dict] = []
    for item in actions or []:
        if isinstance(item, Mapping):
            entry = dict(item)
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            entry = {"id": item[0], "label": item[1]}
            if len(item) >= 3:
                entry["op"] = item[2]
            if len(item) >= 4:
                entry["path"] = item[3]
        else:
            continue
        ident = str(entry.get("id") or "").strip()
        label = str(entry.get("label") or ident).strip()
        if not ident or "=" in ident:
            continue
        op = str(entry.get("op") or ident)
        if op not in _OPS:
            op = "open" if ident == "open" else "reveal" if ident in ("reveal", "show") else op
        clean = {"id": ident, "label": label, "op": op}
        if "path" in entry and entry["path"]:
            clean["paths"] = [os.fspath(entry["path"])]
        if "paths" in entry and entry["paths"]:
            clean["paths"] = [os.fspath(p) for p in entry["paths"]]
        if "text" in entry:
            clean["text"] = str(entry["text"])
        if "argv" in entry and entry["argv"]:
            clean["argv"] = [str(a) for a in entry["argv"]]
        if entry.get("file_manager"):
            clean["file_manager"] = str(entry["file_manager"])
        out.append(clean)
    return out


def _disabled() -> bool:
    return os.environ.get("DROPWHEEL_NO_NOTIFY", "").lower() in ("1", "true", "yes")


def _escape(text: str) -> str:
    """Notification bodies are interpreted as markup by most servers (Caelestia included)."""
    return html.escape(text or "", quote=False)


def _notify_send_argv(summary: str, body: str, icon: str, urgency: str, timeout_ms: int | None,
                      app_name: str, actions: Sequence[dict]) -> list[str]:
    argv = ["notify-send", f"--app-name={app_name}", f"--urgency={urgency}",
            f"--hint=string:desktop-entry:{APP_ID}"]
    if icon:
        argv.append(f"--icon={icon}")
    if timeout_ms is not None:
        argv.append(f"--expire-time={int(timeout_ms)}")
    for act in actions:
        argv.append(f"--action={act['id']}={act['label']}")
    argv += ["--", summary or APP_NAME, _escape(body)]
    return argv


def _package_root() -> str:
    return str(Path(__file__).resolve().parent.parent)


def notify(summary: str, body: str = "", icon: str = "", actions: Iterable[Any] | None = None, *,
           urgency: str = "normal", kind: str = "info", timeout_ms: int | None = None,
           app_name: str = APP_NAME) -> bool:
    """Show a desktop notification; returns ``True`` when something was shown.

    Args:
        summary: title.
        body: plain text (escaped for markup).
        icon: icon name or image path (e.g. the output thumbnail); default "dropwheel".
        actions: buttons, e.g. ``[{"id": "open", "label": "Apri", "op": "open", "path": "/x.png"}]``;
            ops: ``open``, ``reveal``, ``copy_files``, ``copy_text`` (``text``), ``command`` (``argv``);
            ``reveal`` / ``copy_files`` honour an optional ``file_manager`` (``apps.file_manager``).
        urgency: ``low`` | ``normal`` | ``critical``.
        kind: ``info`` | ``success`` | ``warn`` | ``error`` (used by the Caelestia toaster fallback).
        timeout_ms: expiry in milliseconds (server default when ``None``).
    """
    if _disabled():
        return False
    if urgency not in ("low", "normal", "critical"):
        urgency = "normal"
    acts = normalize_actions(actions)
    icon = icon or APP_ID
    if tools.have("notify-send"):
        if acts:
            payload = {"summary": summary, "body": body, "icon": icon, "urgency": urgency,
                       "timeout_ms": timeout_ms, "app_name": app_name, "actions": acts}
            env = {"PYTHONPATH": os.pathsep.join(p for p in (_package_root(), os.environ.get("PYTHONPATH", "")) if p)}
            # -P: the caller's current directory must not shadow the package or the standard library
            if util.detach([sys.executable, "-P", "-m", "dropwheel.notify", json.dumps(payload)], env=env):
                return True
        res = util.run_quiet(_notify_send_argv(summary, body, icon, urgency, timeout_ms, app_name, []), timeout=5)
        if res is not None and res.returncode == 0:
            return True
    return toast(summary, body, kind=kind, icon="" if os.sep in icon else icon)


def toast(title: str, message: str = "", *, kind: str = "info", icon: str = "") -> bool:
    """Show a Caelestia toast (``qs -c caelestia ipc call toaster <kind> …``)."""
    if _disabled():
        return False
    if kind not in _KIND_ICONS:
        kind = "info"
    if not tools.have("qs"):
        log.info("notification (no notify-send / qs): %s — %s", title, message)
        return False
    material = icon if icon and "/" not in icon and "." not in icon and icon != APP_ID else _KIND_ICONS[kind]
    res = util.run_quiet(["qs", "-c", "caelestia", "ipc", "call", "toaster", kind, title or APP_NAME,
                          message or "", material], timeout=5)
    return res is not None and res.returncode == 0


def _perform(act: Mapping[str, Any]) -> None:
    op = act.get("op")
    paths = [p for p in act.get("paths") or [] if p]
    fm = str(act.get("file_manager") or "")
    if op == "open" and paths:
        util.open_path(paths[0])
    elif op == "reveal" and paths:
        util.reveal_path(paths, file_manager=fm)
    elif op == "copy_files" and paths:
        util.copy_files(paths, file_manager=fm)
    elif op == "copy_text":
        util.copy_text(str(act.get("text") or "\n".join(paths)))
    elif op == "command" and act.get("argv"):
        util.detach([str(a) for a in act["argv"]])


def _helper_main(argv: Sequence[str]) -> int:
    """Body of the detached helper: show the notification, wait for a click, perform the action."""
    if not argv:
        return 2
    try:
        payload = json.loads(argv[0])
    except ValueError:
        return 2
    acts = normalize_actions(payload.get("actions"))
    exe = tools.path("notify-send")
    if exe is None:
        return 1
    cmd = _notify_send_argv(str(payload.get("summary") or ""), str(payload.get("body") or ""),
                            str(payload.get("icon") or ""), str(payload.get("urgency") or "normal"),
                            payload.get("timeout_ms"), str(payload.get("app_name") or APP_NAME), acts)
    cmd[0] = exe
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=6 * 3600)
    except subprocess.TimeoutExpired:
        return 0
    except OSError:
        return 1
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return 0
    chosen = lines[-1]
    for act in acts:
        if act["id"] == chosen:
            _perform(act)
            break
    return 0


if __name__ == "__main__":  # pragma: no cover - executed in the detached helper process
    sys.exit(_helper_main(sys.argv[1:]))
