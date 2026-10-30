"""JSON-lines server used by the QML shell (SPEC §5): ``dropwheel daemon``.

One UTF-8 JSON object per line on stdin (requests) and stdout (responses and events):

    → {"id": 7, "method": "menu", "params": {...}}
    ← {"id": 7, "result": {...}}   |   {"id": 7, "error": {"message", "details", "install", "code"}}
    ← {"event": "ready", "version": 1}   {"event": "job", …}   {"event": "log", …}
    ← {"event": "config", "config": {...}} + {"event": "config_changed", "config", "lang", …}

Design:

* the protocol channel is protected: the real stdout/stdin descriptors are duplicated for the
  protocol and fd 1/fd 0 are pointed at stderr and ``/dev/null``, so a stray ``print`` or a
  child process can never corrupt the stream or steal input;
* the reader loop runs in the main thread and never blocks on work: ``hello``, ``cancel`` and
  ``ping`` are answered inline, ``menu``/``run``/``undo``/``util``/``apps``/``clipboard_files``/
  ``share_stop``/``config`` go to a small pool, ``preview`` and ``doctor`` to a separate pool
  (so slow renders never delay a menu);
* one writer lock serialises stdout; a broken pipe stops the daemon quietly; EOF (the shell
  went away), SIGTERM, SIGINT and SIGHUP shut it down cleanly (running jobs are cancelled);
* the process the shell starts is only a supervisor: Quickshell ends its children with SIGKILL
  when it exits (``qs kill``), so the daemon runs in a forked child, which outlives that and
  shuts down on the EOF that follows (see :func:`_supervise`);
* the config file is checked every 2 s (and before every menu); a change reloads the
  configuration, re-registers custom actions and emits ``config`` + ``config_changed``;
* warnings and errors logged by any module are forwarded as ``log`` events (rate limited)
  and always written to stderr, which the shell logs.

Extra action modules can be loaded from ``$DROPWHEEL_EXTRA_ACTIONS`` (``os.pathsep``-separated
``.py`` files or directories), which is how plugins under development and the tests add
actions without touching the package.
"""

from __future__ import annotations

import collections
import dataclasses
import importlib.util
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO, Callable

from . import APP_NAME, PROTOCOL_VERSION, __version__
from . import config as cfgmod
from . import i18n, registry, tools, util
from .errors import UserError

log = logging.getLogger("dropwheel.daemon")

__all__ = ["Daemon", "main", "load_actions", "load_errors", "EXTRA_ERRORS", "MAX_LINE"]

#: Longest accepted request line (bytes).
MAX_LINE = 64 * 1024 * 1024
#: Seconds between two checks of the configuration file.
CONFIG_POLL = 2.0
#: Longest wait for the wheel-hub thumbnail of a ``menu`` request (seconds).
MENU_THUMB_BUDGET = 1.5
#: Errors of the modules listed in ``$DROPWHEEL_EXTRA_ACTIONS`` ({path: traceback}).
EXTRA_ERRORS: dict[str, str] = {}

_INLINE = frozenset({"hello", "cancel", "ping"})
_SLOW = frozenset({"preview", "doctor"})
_SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP")
_REQUIRED =(("py:PIL", "python-pillow"), ("qs", "quickshell"), ("hyprctl", "hyprland"), ("wl-copy", "wl-clipboard"),
             ("wl-paste", "wl-clipboard"))


# ============================================================================ action loading

def _extra_paths() -> list[Path]:
    raw = os.environ.get("DROPWHEEL_EXTRA_ACTIONS", "")
    out: list[Path] = []
    for item in raw.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        p = Path(os.path.expanduser(item))
        if p.is_dir():
            out.extend(sorted(x for x in p.glob("*.py") if not x.name.startswith("_")))
        else:
            out.append(p)
    return out


def _load_extra() -> None:
    EXTRA_ERRORS.clear()
    for index, path in enumerate(_extra_paths()):
        name = f"dropwheel_extra_{index}_{''.join(c if c.isalnum() else '_' for c in path.stem)}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except Exception:
            EXTRA_ERRORS[str(path)] = traceback.format_exc()[-6000:]
            log.error("extra action module %s failed to load", path)


def load_actions(config: dict | None) -> dict[str, str]:
    """Import every action module (``actions.load_all``) plus ``$DROPWHEEL_EXTRA_ACTIONS``.

    Returns ``{module: error}`` for everything that failed (never raises).
    """
    try:
        from . import actions

        actions.load_all(config)
    except Exception:  # the package itself is broken: report it, keep serving
        log.exception("cannot load the action modules")
    _load_extra()
    i18n.reload()
    return load_errors()


def load_errors() -> dict[str, str]:
    """Current action-module load errors (built-in and extra)."""
    errors: dict[str, str] = {}
    try:
        from . import actions

        errors.update(actions.LOAD_ERRORS)
    except Exception as exc:  # pragma: no cover - only with a broken installation
        errors["actions"] = str(exc)
    errors.update({f"extra:{k}": v for k, v in EXTRA_ERRORS.items()})
    return errors


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in str(text).strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# ===================================================================================== JSON

def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        to_json = getattr(obj, "to_json", None)
        return to_json() if callable(to_json) else dataclasses.asdict(obj)
    return str(obj)


def encode_message(obj: dict) -> bytes:
    """One protocol line (UTF-8, ``\\n`` terminated).  Lone surrogates (non-UTF-8 file names)
    are sent as JSON escapes so that the shell can send them back unchanged."""
    try:
        return (json.dumps(obj, ensure_ascii=False, default=_json_default, allow_nan=False) + "\n").encode("utf-8")
    except UnicodeEncodeError:
        return (json.dumps(obj, ensure_ascii=True, default=_json_default, allow_nan=False) + "\n").encode("ascii")
    except ValueError:  # NaN/Infinity somewhere: send them as null
        return (json.dumps(_sanitize(obj), ensure_ascii=True, default=_json_default) + "\n").encode("ascii")


def _sanitize(value: Any) -> Any:
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


# ================================================================================== logging

class _EventLogHandler(logging.Handler):
    """Forwards WARNING+ records to the shell as ``log`` events (at most 20 per 10 s)."""

    def __init__(self, daemon: "Daemon") -> None:
        super().__init__(logging.WARNING)
        self.daemon = daemon
        self.window: collections.deque[float] = collections.deque(maxlen=20)
        self.local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self.local, "busy", False):
            return
        now = time.monotonic()
        if len(self.window) == self.window.maxlen and now - self.window[0] < 10:
            return
        self.window.append(now)
        self.local.busy = True
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                message += f": {record.exc_info[1]}"
            self.daemon.emit({"event": "log", "level": record.levelname.lower(), "logger": record.name,
                              "message": message[:2000]})
        except Exception:
            pass
        finally:
            self.local.busy = False


class _Shutdown(BaseException):
    """Raised by the signal handlers to leave the reader loop (no ``except Exception`` of a
    request handler or of the set-up may swallow it)."""


# =================================================================================== daemon

class Daemon:
    """The protocol server.  ``inp``/``out`` are binary streams (stdin/stdout of the process)."""

    def __init__(self, inp: BinaryIO, out: BinaryIO, *, config: dict | None = None) -> None:
        self._in = inp
        self._out = out
        self._write_lock = threading.Lock()
        self._closed = False
        self._stop = threading.Event()
        self._config_lock = threading.RLock()
        self.config: dict = config if config is not None else cfgmod.load()
        self.lang: str = i18n.detect_lang(self.config)
        i18n.set_lang(self.lang)
        self._config_sig = self._config_signature()
        self.runner: Any = None
        self._ui_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dropwheel-rpc")
        self._slow_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="dropwheel-preview")
        self.methods: dict[str, Callable[[dict], Any]] = {
            "hello": self.m_hello, "menu": self.m_menu, "run": self.m_run, "cancel": self.m_cancel,
            "undo": self.m_undo, "preview": self.m_preview, "apps": self.m_apps, "util": self.m_util,
            "clipboard_files": self.m_clipboard_files, "share_stop": self.m_share_stop, "shares": self.m_shares,
            "config": self.m_config, "doctor": self.m_doctor, "ping": self.m_ping,
        }

    # ------------------------------------------------------------------ setup
    def setup(self) -> None:
        """Load the actions and create the job runner (before ``ready``)."""
        from .jobs import JobRunner
        from . import preview

        load_actions(self.config)
        self.runner = JobRunner(self.config, self.lang, emit=self.emit, thumb_fn=preview.thumb_path)
        threading.Thread(target=self._watch_config, daemon=True, name="dropwheel-config-watch").start()
        threading.Thread(target=self._maintenance, daemon=True, name="dropwheel-maintenance").start()

    def t(self, key: str, /, **fmt: Any) -> str:
        """Translate ``key`` in the current language."""
        return i18n.t(key, self.lang, **fmt)

    # ------------------------------------------------------------------ output
    def emit(self, obj: dict) -> None:
        """Write one message (thread safe; silently stops on a broken pipe)."""
        try:
            data = encode_message(obj)
        except (TypeError, ValueError) as exc:
            log.error("cannot encode message: %s", exc)
            if "id" in obj:
                data = encode_message({"id": obj["id"], "error": {"message": self.t("err.unexpected"),
                                                                  "details": str(exc), "install": "",
                                                                  "code": "encode"}})
            else:
                return
        with self._write_lock:
            if self._closed:
                return
            try:
                self._out.write(data)
                self._out.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                self._closed = True
                self._stop.set()

    def _reply(self, rid: Any, result: Any) -> None:
        if rid is not None:
            self.emit({"id": rid, "result": result})

    def _reply_error(self, rid: Any, error: dict) -> None:
        if rid is not None:
            self.emit({"id": rid, "error": error})

    def _error(self, key: str, code: str, details: str = "", **fmt: Any) -> dict:
        return {"message": self.t(key, **fmt), "details": details, "install": "", "code": code}

    # ------------------------------------------------------------------ requests
    def handle_line(self, raw: bytes) -> None:
        """Parse and dispatch one request line."""
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return
        try:
            msg = json.loads(text)
        except ValueError as exc:
            log.warning("ignoring invalid request line (%s): %.200s", exc, text)
            return
        if not isinstance(msg, dict):
            log.warning("ignoring non-object request: %.200s", text)
            return
        rid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(method, str) or not method:
            self._reply_error(rid, self._error("daemon.err.bad_request", "bad_request"))
            return
        if not isinstance(params, dict):
            self._reply_error(rid, self._error("daemon.err.bad_params", "bad_params", method=method))
            return
        handler = self.methods.get(method)
        if handler is None:
            self._reply_error(rid, self._error("daemon.err.unknown_method", "unknown_method", method=method))
            return
        if method in _INLINE:
            self._dispatch(rid, method, handler, params)
            return
        pool = self._slow_pool if method in _SLOW else self._ui_pool
        try:
            pool.submit(self._dispatch, rid, method, handler, params)
        except RuntimeError:  # pool shut down while stopping
            self._reply_error(rid, self._error("daemon.err.stopping", "stopping"))

    def _dispatch(self, rid: Any, method: str, handler: Callable[[dict], Any], params: dict) -> None:
        try:
            result = handler(params)
        except UserError as exc:
            err = exc.to_json(self.lang)
            err["code"] = exc.message if exc.message and " " not in exc.message else "error"
            self._reply_error(rid, err)
        except Exception:
            log.error("method %s failed", method, exc_info=True)
            self._reply_error(rid, {"message": self.t("err.unexpected"), "details": traceback.format_exc()[-8000:],
                                    "install": "", "code": "unexpected"})
        else:
            self._reply(rid, result)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _files(params: dict, key: str = "files", *, required: bool = True) -> list[str]:
        value = params.get(key)
        if value is None:
            value = params.get("paths")
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            value = []
        files = [str(v) for v in value if isinstance(v, (str, os.PathLike)) and str(v)]
        if required and not files:
            raise UserError("err.no_files")
        return files

    def warnings(self) -> list[str]:
        """Config warnings plus action-module load errors (translated, one line each)."""
        out = [str(w) for w in (self.config.get("_warnings") or [])]
        for module, error in load_errors().items():
            out.append(self.t("daemon.warn.module_failed", module=module, error=_last_line(error)))
        return out

    # ------------------------------------------------------------------ config
    @staticmethod
    def _config_signature() -> tuple:
        path = cfgmod.config_path()
        try:
            st = os.stat(path)
            return (str(path), st.st_mtime_ns, st.st_size)
        except OSError:
            return (str(path), None, None)

    def reload_config(self, *, emit: bool = True) -> dict:
        """Reload the configuration from disk (custom actions too); the shell is notified
        (``config`` + ``config_changed`` events) only when something actually changed."""
        with self._config_lock:
            old_lang = self.lang
            old_custom = self.config.get("custom")
            old_public = cfgmod.public_config(self.config)
            old_warnings = list(self.config.get("_warnings") or [])
            new = cfgmod.load()
            self.config = new
            self.lang = i18n.detect_lang(new)
            i18n.set_lang(self.lang)
            self._config_sig = self._config_signature()
            if self.runner is not None:
                self.runner.set_config(new, self.lang)
            if new.get("custom") != old_custom:
                load_actions(new)
            public = cfgmod.public_config(new)
            changed = (public != old_public or self.lang != old_lang
                       or list(new.get("_warnings") or []) != old_warnings)
        if emit and changed:
            self.emit({"event": "config", "config": public})
            self.emit({"event": "config_changed", "config": public, "lang": self.lang,
                       "lang_changed": old_lang != self.lang, "warnings": self.warnings()})
        return public

    def _maybe_reload(self) -> None:
        if self._config_signature() != self._config_sig:
            self.reload_config()

    def _watch_config(self) -> None:
        while not self._stop.wait(CONFIG_POLL):
            try:
                self._maybe_reload()
            except Exception:
                log.exception("config reload failed")

    def _maintenance(self) -> None:
        from . import preview

        if self._stop.wait(30):
            return
        while True:
            try:
                removed = preview.prune_cache()
                if removed:
                    log.info("preview cache: %d files removed", removed)
            except Exception:
                log.debug("preview cache cleanup failed", exc_info=True)
            if self._stop.wait(6 * 3600):
                return

    # ------------------------------------------------------------------ methods
    def m_hello(self, params: dict) -> dict:
        """Handshake: version, language, every string, public config, warnings."""
        return {
            "version": __version__, "protocol": PROTOCOL_VERSION, "app": APP_NAME, "lang": self.lang,
            "strings": i18n.all_strings(self.lang), "config": cfgmod.public_config(self.config),
            "warnings": self.warnings(), "pid": os.getpid(), "python": sys.version.split()[0],
        }

    def m_ping(self, params: dict) -> dict:
        return {"pong": True, "version": __version__}

    def _menu_thumb(self, info: Any) -> str | None:
        """Hub thumbnail with a time budget: the wheel must not wait for a slow video frame or a
        network file.  A late thumbnail keeps rendering in the background and is cached, so the
        next menu for the same file gets it."""
        from . import preview

        try:
            future = self._slow_pool.submit(preview.thumb_path, info)
        except RuntimeError:  # stopping
            return None
        try:
            return future.result(timeout=MENU_THUMB_BUDGET)
        except Exception:  # TimeoutError, or a bug in a renderer (thumb_path itself never raises)
            return None

    def m_menu(self, params: dict) -> dict:
        files = self._files(params, required=False)
        context = params.get("context") if params.get("context") in ("drag", "click") else "click"
        self._maybe_reload()
        return registry.build_menu(files, self.config, self.lang, context, thumb_fn=self._menu_thumb)

    def _jobs(self) -> Any:
        """The job runner (``setup()`` failed when it is missing: report it, do not crash)."""
        if self.runner is None:
            raise UserError("err.unexpected", details="the job runner could not be created (see the daemon log)")
        return self.runner

    def m_run(self, params: dict) -> dict:
        action_id = str(params.get("action") or "").strip()
        files = self._files(params)
        given = params.get("params") if isinstance(params.get("params"), dict) else {}
        if not action_id:
            raise UserError("err.unknown_action", id="?")
        target = action_id if registry.get(action_id) is not None else registry.resolve_target(
            action_id, files, self.config, self.lang)
        if target is None:
            raise UserError("err.unknown_action", id=action_id)
        return {"job": self._jobs().submit(target, files, given)}

    def m_cancel(self, params: dict) -> dict:
        job = str(params.get("job") or "")
        return {"ok": bool(job) and self._jobs().cancel(job)}

    def m_undo(self, params: dict) -> dict:
        job = params.get("job")
        return self._jobs().undo(str(job) if job else None)

    def m_preview(self, params: dict) -> Any:
        from . import preview

        return preview.preview(str(params.get("kind") or ""), params, self.lang)

    def m_apps(self, params: dict) -> list[dict]:
        from .detect import classify

        files = self._files(params)
        mimes = []
        for f in files[:200]:
            info = classify(f, deep=False)
            if info.category == "url":
                mime = "x-scheme-handler/" + (info.uri.split(":", 1)[0].lower() or "https")
            else:
                mime = info.mime
            if mime and mime not in mimes:
                mimes.append(mime)
        apps = util.desktop_apps_for(mimes, self.lang)
        return [{"id": a["id"], "name": a["name"], "icon": a.get("icon", ""), "default": bool(a.get("default")),
                 "terminal": bool(a.get("terminal"))}
                for a in apps if a.get("id") != "dropwheel.desktop"]

    def m_util(self, params: dict) -> dict:
        op = str(params.get("op") or "")
        paths = self._files(params, "paths", required=False)
        cfg = self.config
        if op == "open":
            if not paths:
                raise UserError("err.no_files")
            failed = [p for p in paths[:20] if not util.open_path(p)]
            if failed:
                raise UserError("err.open_failed", name=os.path.basename(failed[0]) or failed[0],
                                install=tools.install_hint(["xdg-open"]) if not tools.have("xdg-open") else "")
            return {"ok": True}
        if op == "reveal":
            if not paths:
                raise UserError("err.no_files")
            fm = str(cfgmod.get(cfg, "apps.file_manager", "") or "")
            if not util.reveal_path(paths, file_manager=fm):
                raise UserError("err.open_failed", name=os.path.dirname(paths[0]) or paths[0])
            return {"ok": True}
        if op == "copy_files":
            if not paths:
                raise UserError("err.no_files")
            if not util.copy_files(paths, file_manager=str(cfgmod.get(cfg, "apps.file_manager", "") or "")):
                raise UserError("daemon.err.clipboard", install=tools.install_hint(["wl-copy"])
                                if not tools.have("wl-copy") else "")
            return {"ok": True}
        if op == "copy_text":
            if not util.copy_text(str(params.get("text") or "")):
                raise UserError("daemon.err.clipboard", install=tools.install_hint(["wl-copy"])
                                if not tools.have("wl-copy") else "")
            return {"ok": True}
        if op == "copy_image":
            if not paths or not util.copy_image(paths[0]):
                raise UserError("daemon.err.clipboard", install=tools.install_hint(["wl-copy"])
                                if not tools.have("wl-copy") else "")
            return {"ok": True}
        if op == "open_with":
            app = str(params.get("app") or "")
            if not app:
                raise UserError("daemon.err.no_app")
            if not util.launch_desktop_app(app, paths):
                raise UserError("err.open_failed", name=app)
            return {"ok": True}
        if op == "terminal":
            target = paths[0] if paths else os.path.expanduser("~")
            util.launch_terminal(target, None, cfg)
            return {"ok": True}
        if op == "save_text":
            return {"ok": True, "path": str(self._save_text(params, paths))}
        raise UserError("daemon.err.unknown_op", op=op or "?")

    def _save_text(self, params: dict, paths: list[str]) -> Path:
        """Write ``text`` next to the first path (or into the fallback folder) without clobbering."""
        text = str(params.get("text") or "")
        explicit = str(params.get("path") or "")
        if explicit:
            target = Path(os.path.expanduser(explicit))
        else:
            name = str(params.get("name") or "").strip()
            ext = str(params.get("ext") or "txt").lstrip(".") or "txt"
            directory: Path | None = None
            if paths and not paths[0].lower().startswith(("http://", "https://")):
                src = Path(paths[0])
                parent = src.parent if not src.is_dir() else src
                if parent.is_dir() and os.access(parent, os.W_OK) and not util.is_volatile(str(src)):
                    directory = parent
                if not name:
                    stem = util.split_name(src.name, is_dir=src.is_dir())[0] or "text"
                    name = f"{stem} ({self.t('daemon.sfx.text')})"
            if directory is None:
                directory = util.fallback_output_dir()
            if not name:
                name = self.t("daemon.sfx.text")
            fname = util.safe_filename(name if name.lower().endswith(f".{ext}") else f"{name}.{ext}")
            target = directory / fname
        target.parent.mkdir(parents=True, exist_ok=True)
        target = util.unique_path(target)
        from .jobs import atomic_target

        with atomic_target(target) as tmp:
            Path(tmp).write_text(text, encoding="utf-8")
        return target

    def m_clipboard_files(self, params: dict) -> dict:
        files = []
        for f in util.read_clipboard_files():
            if f.lower().startswith(("http://", "https://")) or os.path.exists(f):
                files.append(f)
        return {"files": files}

    def m_share_stop(self, params: dict) -> dict:
        from . import share

        share_id = str(params.get("id") or "")
        if not share_id:
            raise UserError("share.err.unknown", id="?")
        return {"ok": share.stop_share(share_id)}

    def m_shares(self, params: dict) -> dict:
        from . import share

        return {"shares": share.list_shares()}

    def m_config(self, params: dict) -> dict:
        return self.reload_config()

    def m_doctor(self, params: dict) -> dict:
        report = tools.report()
        report["load_errors"] = load_errors()
        report["config_warnings"] = list(self.config.get("_warnings") or [])
        report["config_path"] = str(cfgmod.config_path())
        report["required_missing"] = [pkg for name, pkg in _REQUIRED if not tools.have(name)]
        report["version"] = __version__
        return report

    # ------------------------------------------------------------------ main loop
    def run(self) -> int:
        """Serve until EOF or a termination signal; returns the exit status."""
        self.emit({"event": "ready", "version": PROTOCOL_VERSION})
        try:
            while not self._stop.is_set():
                try:
                    raw = self._in.readline(MAX_LINE)
                except (OSError, ValueError):
                    break
                if not raw:
                    break
                if len(raw) >= MAX_LINE and not raw.endswith(b"\n"):
                    log.warning("request line longer than %d bytes ignored", MAX_LINE)
                    while raw and not raw.endswith(b"\n"):
                        raw = self._in.readline(MAX_LINE)
                    continue
                self.handle_line(raw)
        except (_Shutdown, KeyboardInterrupt):
            pass
        finally:
            self.shutdown()
        return 0

    def shutdown(self, grace: float = 5.0) -> None:
        """Cancel running jobs, stop the pools and wait (at most ``grace`` s) for jobs to end."""
        self._stop.set()
        runner = self.runner
        if runner is not None:
            try:
                runner.shutdown(wait=False)
            except Exception:
                log.debug("runner shutdown failed", exc_info=True)
        self._ui_pool.shutdown(wait=False, cancel_futures=True)
        self._slow_pool.shutdown(wait=False, cancel_futures=True)
        if runner is not None:
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and any(not j.done for j in runner.jobs()):
                time.sleep(0.05)
        with self._write_lock:
            try:
                self._out.flush()
            except (OSError, ValueError):
                pass


# ===================================================================================== main

def _protocol_streams() -> tuple[BinaryIO, BinaryIO]:
    """Duplicate stdin/stdout for the protocol and neutralise fd 0/1 for everything else."""
    try:
        in_fd = os.dup(sys.stdin.fileno())
        out_fd = os.dup(sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        return sys.stdin.buffer, sys.stdout.buffer
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    try:
        os.dup2(sys.stderr.fileno(), 1)
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
    except (AttributeError, OSError, ValueError):
        pass
    sys.stdout = sys.stderr
    try:
        sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - lives as long as the process
    except OSError:
        pass
    return os.fdopen(in_fd, "rb", buffering=1 << 16), os.fdopen(out_fd, "wb", buffering=1 << 16)


def _setup_logging(daemon: Daemon | None, debug: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(logging.DEBUG if debug else logging.WARNING)
    stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(stream)
    if daemon is not None:
        root.addHandler(_EventLogHandler(daemon))


def _set_signal_handlers(handler: Any) -> None:
    for name in _SIGNALS:
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), handler)
            except (ValueError, OSError):
                pass


def _exit_code(status: int) -> int:
    """Exit status of a child from its ``waitpid`` status (128 + signal when it was killed)."""
    code = os.waitstatus_to_exitcode(status)
    return 128 - code if code < 0 else code


def _supervise() -> None:
    """Fork the daemon off the process that Quickshell manages; returns only in the child.

    Quickshell's ``Process`` sends SIGKILL to its child when the shell exits (``qs kill``, which
    is ``dropwheel stop`` and ``restart``).  No handler runs then: tools started in their own
    session keep going, and ``.part`` files and job folders stay behind.  So the process the shell
    knows does nothing but forward signals and report the exit status.  The daemon, in a child
    sharing the same pipes, sees EOF when the shell closes them and shuts down cleanly whether
    its parent was killed or not.  Without ``fork`` (or when it fails) the daemon runs right here.
    """
    if not hasattr(os, "fork"):
        return
    for stream in (sys.stdout, sys.stderr):  # the child must not inherit (and repeat) buffered output
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    # Signals wait until the handlers below know the child (they stay pending in the parent only).
    watched = {getattr(signal, name) for name in _SIGNALS if hasattr(signal, name)}
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, watched)
    try:
        pid = os.fork()
    except OSError as exc:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        log.warning("cannot fork the daemon, running without a supervisor: %s", exc)
        return
    if pid == 0:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        return

    def forward(signum: int, _frame: Any) -> None:
        try:
            os.kill(pid, signum)
        except OSError:
            pass

    _set_signal_handlers(forward)
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    while True:
        try:
            _pid, status = os.waitpid(pid, 0)
        except InterruptedError:
            continue
        except OSError:
            os._exit(1)
        os._exit(_exit_code(status))


def main(argv: list[str] | None = None) -> int:
    """Entry point of ``dropwheel daemon`` (``--debug`` logs everything on stderr)."""
    args = list(sys.argv[1:] if argv is None else argv)
    debug = "--debug" in args or os.environ.get("DROPWHEEL_DEBUG", "").lower() in ("1", "true", "yes")
    _supervise()
    inp, out = _protocol_streams()
    _setup_logging(None, debug)
    try:
        daemon = Daemon(inp, out)
    except Exception:
        log.critical("daemon start-up failed:\n%s", traceback.format_exc())
        return 1

    def on_signal(signum: int, _frame: Any) -> None:
        _set_signal_handlers(signal.SIG_IGN)  # one is enough: a second one must not interrupt the shutdown
        raise _Shutdown(signum)

    _set_signal_handlers(on_signal)
    code = 0
    try:
        try:
            daemon.setup()
        except Exception:
            log.critical("daemon set-up failed:\n%s", traceback.format_exc())
        # Forward log records to the shell only from now on: "ready" must be the first message,
        # and set-up problems (action modules that failed to load) reach the shell through the
        # hello warnings anyway.
        _setup_logging(daemon, debug)
        code = daemon.run()
    except _Shutdown:  # during the set-up, or during the shutdown that followed an EOF
        daemon.shutdown()
    try:
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    # Worker threads blocked in long C calls (Pillow, a preview renderer) must not keep a dead
    # daemon alive after the shell went away: jobs were cancelled and given time to clean up.
    os._exit(code)
