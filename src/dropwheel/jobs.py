"""Job execution: :class:`Context` (the API actions use), :class:`Result`, :class:`JobRunner`.

Runner guarantees (SPEC §3.3): inputs are validated (exist/readable), volatile inputs are
staged into ``$XDG_CACHE_HOME/dropwheel/staging/<job>``, ``multi="each"`` actions run once per
file and continue on per-file failures ("3/4 completati, 1 non riuscito"), every job has a
temporary directory removed at the end, outputs get Tangerine-style names and are written
atomically (:func:`atomic_target`), inputs are never deleted (``replace_original`` backs the
original up for Undo), history goes to ``$XDG_STATE_HOME/dropwheel/history.jsonl``, external
processes run in their own process group and are killed on cancel (SIGTERM, SIGKILL after 3 s).

Events (SPEC §5.2) are passed to ``emit(dict)``; progress events are throttled to ≥ 80 ms.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
import traceback
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from . import config as cfgmod
from . import i18n, registry, tools, util
from .detect import FileInfo, classify
from .errors import Cancelled, MissingTool, ToolError, UserError

log = logging.getLogger("dropwheel.jobs")

__all__ = ["Context", "Result", "Job", "JobRunner", "atomic_target", "FINAL_STATES", "PROGRESS_INTERVAL"]

PROGRESS_INTERVAL = 0.08
STDERR_TAIL_LINES = 15
FINAL_STATES = ("done", "failed", "cancelled")
KILL_GRACE = 3.0
HISTORY_MAX_BYTES = 2 * 1024 * 1024
HISTORY_KEEP = 1000
BACKUP_MAX_AGE = 30 * 86400
TMP_MAX_AGE = 86400

_HISTORY_LOCK = threading.Lock()
_PROBE_CACHE: dict[tuple, dict] = {}
_PROBE_LOCK = threading.Lock()


# ===================================================================================== Result

@dataclass
class Result:
    """What an action returns (build it with :meth:`Context.result`)."""

    outputs: list[Path] = field(default_factory=list)
    kind: str = "files"            #: files | text | info | qr | share | none
    message: str = ""
    text: str | None = None
    info: list[dict] | None = None
    extra: dict = field(default_factory=dict)
    size_before: int | None = None


# =============================================================================== atomic writes

def _remove(path: str | os.PathLike) -> None:
    p = os.fspath(path)
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.lexists(p):
            os.unlink(p)
    except OSError:
        pass


def _fsync_file(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _commit(part: Path, final: Path) -> None:
    if part.is_dir() and not part.is_symlink():
        if os.path.lexists(final):
            _remove(part)
            raise ToolError("err.output_exists", name=final.name)
        os.rename(part, final)
        return
    _fsync_file(part)
    try:
        os.link(part, final)
    except FileExistsError:
        try:
            empty = final.is_file() and final.stat().st_size == 0
        except OSError:
            empty = False
        if empty:
            os.replace(part, final)
            return
        _remove(part)
        raise ToolError("err.output_exists", name=final.name) from None
    except (OSError, NotImplementedError):
        # filesystems without hard links (vfat, exfat, some FUSE): best effort no-clobber
        if os.path.lexists(final):
            _remove(part)
            raise ToolError("err.output_exists", name=final.name) from None
        os.replace(part, final)
        return
    try:
        os.unlink(part)
    except OSError:
        pass


def _fit_name(name: str) -> str:
    """``name`` itself, shortened (extension and final "(suffix)" kept) only when it exceeds NAME_MAX."""
    return name if len(name.encode("utf-8", "surrogateescape")) <= util.NAME_MAX else util.safe_filename(name)


@contextlib.contextmanager
def atomic_target(path: str | os.PathLike) -> Iterator[Path]:
    """Write ``path`` atomically: yields a hidden ``.part`` path in the same directory (keeping the
    extension so tools pick the right format); on success it is renamed to ``path`` without
    clobbering anything, on error it is removed.  Works for files and directories.

    Example::

        out = ctx.output_path(ext="png")
        with atomic_target(out) as tmp:
            image.save(tmp)
    """
    final = Path(path)
    final.parent.mkdir(parents=True, exist_ok=True)
    _stem, ext = util.split_name(final.name)
    tail = f".part-{secrets.token_hex(3)}" + (f".{ext}" if ext else "")
    # the final name may already use the whole NAME_MAX (255 bytes): shorten the copy inside the part name
    head = util.truncate_bytes(final.name, util.NAME_MAX - 1 - len(tail.encode("utf-8")))
    part = final.parent / f".{head}{tail}"
    try:
        yield part
    except BaseException:
        _remove(part)
        raise
    if not os.path.lexists(part):
        raise ToolError("err.no_output", name=final.name)
    try:
        _commit(part, final)
    except BaseException:
        _remove(part)
        raise


# ================================================================================ processes

def _kill_proc(proc: subprocess.Popen, grace: float = KILL_GRACE) -> None:
    """SIGTERM the process group, SIGKILL after ``grace`` seconds."""
    posix = os.name == "posix" and hasattr(os, "killpg")
    try:
        if posix:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    try:
        if posix:
            os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[attr-defined]
        elif proc.poll() is None:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


_SECRET_RE = re.compile(r"(?i)(pass(?!through)|pwd|secret|token|key|upw|opw)")
#: options whose next argument is a password, whatever the tool (pdfinfo/pdftoppm/pdftotext: -upw, -opw; mutool: -p)
_SECRET_OPTIONS = frozenset({"-upw", "-opw", "-p"})


def _mask_args(argv: Sequence[str]) -> str:
    shown = []
    hide_next = False
    for a in argv:
        if hide_next:
            shown.append("***")
            hide_next = False
            continue
        if a in _SECRET_OPTIONS:
            shown.append(a)
            hide_next = True
        elif _SECRET_RE.search(a) and "=" in a:
            shown.append(a.split("=", 1)[0] + "=***")
        elif re.match(r"^-p.+", a) and not a.startswith("-pr"):
            shown.append("-p***")
        elif _SECRET_RE.search(a) and a.startswith("-"):
            shown.append(a)
            hide_next = True
        else:
            shown.append(a if len(a) < 200 else a[:200] + "…")
    return " ".join(shown)


def _split_lines(buf: bytes) -> tuple[list[bytes], bytes]:
    # \r: ffmpeg/curl-style redraws; runs of \b: 7-Zip and unrar redraw their percentage with backspaces.
    parts = re.split(rb"\r\n|\r|\n|\x08+", buf)
    return parts[:-1], parts[-1]


class _StreamReader(threading.Thread):
    """Reads a pipe, keeps the bytes (bounded for stderr), a line tail and calls ``on_line``."""

    def __init__(self, stream, *, keep: bool, limit: int | None, on_line: Callable[[str], None] | None,
                 tail: int, errors: list) -> None:
        super().__init__(daemon=True, name="dropwheel-pipe")
        self.stream = stream
        self.keep = keep
        self.limit = limit
        self.on_line = on_line
        self.tail: deque[str] = deque(maxlen=max(tail, 1))
        self.chunks: list[bytes] = []
        self.size = 0
        self.errors = errors
        self.split = on_line is not None or tail > 0

    def _line(self, raw: bytes) -> None:
        if not raw:
            return
        line = raw.decode("utf-8", "replace")
        self.tail.append(line)
        if self.on_line is not None and not self.errors:
            try:
                self.on_line(line)
            except BaseException as exc:  # propagated to the waiting thread
                self.errors.append(exc)

    def run(self) -> None:
        buf = b""
        read = getattr(self.stream, "read1", None) or self.stream.read
        try:
            while True:
                data = read(65536)
                if not data:
                    break
                if self.keep and (self.limit is None or self.size < self.limit):
                    self.chunks.append(data)
                    self.size += len(data)
                if self.split:
                    buf += data
                    lines, buf = _split_lines(buf)
                    for raw in lines:
                        self._line(raw)
                    if len(buf) > 1_000_000:
                        self._line(buf)
                        buf = b""
        except (OSError, ValueError):
            pass
        finally:
            if self.split and buf:
                self._line(buf)
            try:
                self.stream.close()
            except OSError:
                pass

    def data(self) -> bytes:
        return b"".join(self.chunks)


# ======================================================================================== Job

class Job:
    """State of one submitted job."""

    def __init__(self, job_id: str, act: registry.Action, files: list[FileInfo], params: dict, label: str) -> None:
        self.id = job_id
        self.action = act
        self.files = files
        self.given_params = dict(params or {})
        self.params: dict = dict(params or {})
        self.label = label
        self.state = "queued"
        self.progress: float | None = None
        self.message = ""
        self.warnings: list[str] = []
        self.result: dict | None = None
        self.error: dict | None = None
        self.created = time.time()
        self.started: float | None = None
        self.finished: float | None = None
        self.cancel_event = threading.Event()
        self.procs: set[subprocess.Popen] = set()
        self.lock = threading.RLock()
        self.outputs: list[Path] = []
        self.backups: list[dict] = []
        self.originals: dict[str, FileInfo] = {}
        self.reserved: list[str] = []
        self.tmp_root: Path | None = None
        self.staging: Path | None = None
        self.last_emit = 0.0
        self.pending = False
        self.timer_active = False
        self.log_lines: deque[str] = deque(maxlen=300)
        self.future: Future | None = None
        self.undone = False
        self.warned: set[str] = set()

    @property
    def done(self) -> bool:
        """``True`` once the job reached a final state."""
        return self.state in FINAL_STATES

    def to_json(self) -> dict:
        """Summary for the CLI/UI."""
        return {
            "job": self.id, "state": self.state, "action": self.action.id, "label": self.label,
            "files": [f.name for f in self.files], "paths": [f.path or f.uri for f in self.files],
            "progress": self.progress, "message": self.message, "warnings": list(self.warnings),
            "result": self.result, "error": self.error, "created": self.created, "started": self.started,
            "finished": self.finished,
        }


# ==================================================================================== Context

def _display_dir(path: Path) -> str:
    home = os.path.expanduser("~")
    text = str(path)
    if text == home or text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


def _norm_key(path: str | os.PathLike, ci: bool) -> str:
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    return key.lower() if ci else key


class Context:
    """Everything an action function receives (``fn(ctx) -> Result | None``)."""

    def __init__(self, runner: "JobRunner", job: Job, files: list[FileInfo], index: int = 0, total: int = 1) -> None:
        self._runner = runner
        self._job = job
        self.job_id: str = job.id
        self.action: registry.Action = job.action
        self.files: list[FileInfo] = list(files)
        self.all_files: list[FileInfo] = list(job.files)
        self.params: dict = dict(job.params)
        self.config: dict = runner.config
        self.lang: str = runner.lang
        self.index = index
        self.total = total
        self._reserved_paths: list[Path] = []
        self._created_dirs: list[Path] = []
        self._backups: list[dict] = []
        self._tmp: Path | None = None
        self.started = time.time()

    # ------------------------------------------------------------------ basics
    @property
    def file(self) -> FileInfo:
        """The first (for ``multi="each"``: the current) file."""
        if not self.files:
            raise UserError("err.no_files")
        return self.files[0]

    def t(self, key: str, /, **fmt: Any) -> str:
        """Translate ``key`` in the job language."""
        return i18n.t(key, self.lang, **fmt)

    def original(self, f: FileInfo | None = None) -> FileInfo:
        """The original (pre-staging) FileInfo of ``f`` (default: :attr:`file`)."""
        f = f or self.file
        return self._job.originals.get(f.path, f)

    def p(self, name: str, default: Any = None) -> Any:
        """Raw parameter value (``default`` when missing or ``None``)."""
        value = self.params.get(name)
        return default if value is None else value

    def pint(self, name: str, default: int = 0) -> int:
        """Parameter as ``int``."""
        value = self.params.get(name)
        if value is None or value == "":
            return default
        try:
            return int(round(float(str(value).replace(",", "."))))
        except (TypeError, ValueError):
            return default

    def pfloat(self, name: str, default: float = 0.0) -> float:
        """Parameter as ``float``."""
        value = self.params.get(name)
        if value is None or value == "":
            return default
        try:
            result = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return default
        return default if result != result else result

    def pbool(self, name: str, default: bool = False) -> bool:
        """Parameter as ``bool`` (accepts "true"/"1"/"yes"/"on")."""
        value = self.params.get(name)
        if value is None:
            return default
        try:
            return registry._to_bool(value)
        except ValueError:
            return default

    def pstr(self, name: str, default: str = "") -> str:
        """Parameter as ``str``."""
        value = self.params.get(name)
        return default if value is None else str(value)

    # ------------------------------------------------------------------ progress / log
    def progress(self, frac: float | None, message: str | None = None) -> None:
        """Report progress (``None`` = indeterminate); batch-aware (scaled into this file's window)."""
        if frac is not None:
            try:
                frac = float(frac)
            except (TypeError, ValueError):
                frac = None
            else:
                frac = 0.0 if frac != frac else min(1.0, max(0.0, frac))
        job = self._job
        if self.total > 1:
            overall: float | None = (self.index + (frac or 0.0)) / self.total
            prefix = f"{self.index + 1}/{self.total}"
            msg: str | None = f"{prefix} · {message}" if message else f"{prefix} · {self.file.name}"
        else:
            overall = frac
            msg = message
        with job.lock:
            job.progress = overall
            if msg is not None:
                job.message = msg
        self._runner._emit_progress(job)

    def log(self, msg: str) -> None:
        """Debug log line (kept with the job)."""
        self._job.log_lines.append(str(msg))
        log.debug("[%s] %s", self.job_id, msg)

    def warn(self, msg: str) -> None:
        """Warning shown on the HUD card."""
        self._runner._warn(self._job, str(msg))

    def check_cancel(self) -> None:
        """Raise :class:`~dropwheel.errors.Cancelled` when the job was cancelled."""
        if self._job.cancel_event.is_set():
            raise Cancelled()

    @property
    def cancelled(self) -> bool:
        """``True`` when cancellation was requested."""
        return self._job.cancel_event.is_set()

    def tmpdir(self) -> Path:
        """Per-job temporary directory (under ``$XDG_CACHE_HOME``, removed when the job ends)."""
        if self._tmp is None:
            job = self._job
            with job.lock:
                if job.tmp_root is None:
                    job.tmp_root = util.cache_dir() / "tmp" / job.id
                    job.tmp_root.mkdir(parents=True, exist_ok=True)
            self._tmp = job.tmp_root / str(self.index) if self.total > 1 else job.tmp_root
            self._tmp.mkdir(parents=True, exist_ok=True)
        return self._tmp

    def tmp_path(self, name: str) -> Path:
        """A path inside :meth:`tmpdir`."""
        return self.tmpdir() / util.safe_filename(name)

    def atomic(self, path: str | os.PathLike):
        """Alias of :func:`atomic_target`."""
        return atomic_target(path)

    # ------------------------------------------------------------------ naming
    def _resolve_src(self, src: Any) -> FileInfo:
        if src is None:
            src = self.file
        if isinstance(src, FileInfo):
            return self._job.originals.get(src.path, src)
        p = Path(os.fspath(src))
        text = str(p.absolute())
        for f in self.files + self.all_files:
            if f.path == text:
                return self._job.originals.get(f.path, f)
        if text in self._job.originals:
            return self._job.originals[text]
        is_dir = p.is_dir()
        stem, ext = util.split_name(p.name, is_dir=is_dir)
        return FileInfo(path=text, uri=util.path_to_uri(text), name=p.name, stem=stem, ext=ext, exists=p.exists(),
                        is_dir=is_dir, volatile=util.is_volatile(text),
                        writable_dir=os.access(p.parent, os.W_OK) if p.parent.exists() else False)

    def _target_dir(self, orig: FileInfo | None) -> Path:
        reason = ""
        out_cfg = self.config.get("output") if isinstance(self.config.get("output"), dict) else {}
        if out_cfg.get("location") == "folder":
            folder = cfgmod.output_folder(self.config)
            try:
                folder.mkdir(parents=True, exist_ok=True)
                if os.access(folder, os.W_OK):
                    return folder
            except OSError:
                pass
            reason = "readonly"
        elif orig is not None and orig.path and orig.category != "url":
            parent = Path(orig.path).parent
            staging_root = util.cache_dir() / "staging"
            in_staging = str(parent).startswith(str(staging_root))
            if not orig.volatile and not in_staging and parent.is_dir() and os.access(parent, os.W_OK):
                return parent
            reason = "volatile" if (orig.volatile or in_staging) else "readonly"
        fb = util.fallback_output_dir()
        try:
            fb.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UserError("err.not_writable", details=str(exc), dir=str(fb)) from None
        if reason:
            self._runner._warn_once(self._job, f"fallback:{reason}",
                                    self.t(f"warn.saved_fallback_{reason}", dir=_display_dir(fb)))
        return fb

    def _input_keys(self, ci: bool) -> set[str]:
        keys = set()
        for f in list(self.all_files) + list(self._job.originals.values()) + list(self.files):
            if f.path:
                keys.add(_norm_key(f.path, ci))
        for staged in self._job.originals:
            keys.add(_norm_key(staged, ci))
        return keys

    def _reserve(self, path: Path, is_dir: bool = False) -> Path:
        runner = self._runner
        ci = util.is_case_insensitive_fs(path.parent)
        with runner._reserve_lock:
            taken = set(runner._reserved) | self._input_keys(ci)
            chosen = util.unique_path(path, taken=taken, ci=ci, is_dir=is_dir)
            key = _norm_key(chosen, ci)
            runner._reserved.add(key)
            self._job.reserved.append(key)
        self._reserved_paths.append(chosen)
        return chosen

    def _suffix_text(self, suffix: str) -> str:
        if i18n.has(suffix, self.lang):
            return self.t(suffix)
        if not suffix.startswith("sfx.") and i18n.has(f"sfx.{suffix}", self.lang):
            return self.t(f"sfx.{suffix}")
        return suffix

    def output_path(self, src: FileInfo | str | os.PathLike | None = None, *, ext: str | None = None,
                    suffix: str | None = None, name: str | None = None,
                    directory: str | os.PathLike | None = None) -> Path:
        """Reserve a unique output path.

        * conversions (``suffix=None``): ``"<stem>.<ext>"``;
        * tools: ``suffix="sfx.compressed"`` → ``"<stem> (compresso).<ext>"`` (i18n key, bare name
          like ``"compressed"`` or literal text);
        * ``name``: explicit file name (``ext`` appended when missing);
        * collisions → ``"<stem> 2.<ext>"``, never equal to an input; placed next to the source,
          in ``output.folder`` (``output.location = "folder"``) or in ``~/Downloads/Dropwheel`` when the
          source directory is not writable or the source is volatile; ``directory`` overrides the folder.
        """
        orig = self._resolve_src(src)
        if directory is not None:
            target = Path(os.fspath(directory))
            target.mkdir(parents=True, exist_ok=True)
        else:
            target = self._target_dir(orig)
        portable = util.needs_portable_names(target)
        if name:
            fname = str(name)
            if ext:
                e = ext.lstrip(".").lower()
                if not fname.lower().endswith("." + e):
                    fname = f"{fname}.{e}"
        else:
            e = orig.ext if ext is None else ext.lstrip(".").lower()
            stem = orig.stem or orig.name or "file"
            if orig.is_dir and ext is None:
                e = ""
            if suffix:
                stem = f"{stem} ({self._suffix_text(suffix)})"
            fname = f"{stem}.{e}" if e else stem
        fname = util.safe_filename(fname, portable=portable)
        return self._reserve(target / fname)

    def output_dir(self, src: FileInfo | str | os.PathLike | None = None, *, suffix: str | None = None,
                   name: str | None = None) -> Path:
        """Create and return a unique output folder (``"<stem> (<suffix>)"`` or ``name``)."""
        orig = self._resolve_src(src)
        target = self._target_dir(orig)
        if name:
            dname = str(name)
        else:
            stem = orig.stem or orig.name or "folder"
            dname = f"{stem} ({self._suffix_text(suffix)})" if suffix else stem
        dname = util.safe_filename(dname, portable=util.needs_portable_names(target))
        for _ in range(100):
            path = self._reserve(target / dname, is_dir=True)
            try:
                path.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                continue
            except OSError as exc:
                raise UserError("err.not_writable", details=str(exc), dir=_display_dir(target)) from None
            self._created_dirs.append(path)
            return path
        raise UserError("err.no_free_name", name=dname)

    # ------------------------------------------------------------------ processes
    def run(self, argv: Sequence[Any], *, input: bytes | str | None = None, timeout: float | None = None,
            env: dict | None = None, cwd: str | os.PathLike | None = None, ok_codes: Iterable[int] = (0,),
            on_line: Callable[[str], None] | None = None, stderr_lines: bool = False,
            text: bool = True) -> subprocess.CompletedProcess:
        """Run an external program.

        ``argv[0]`` is a :mod:`dropwheel.tools` name or a binary (resolved with ``tools.path``).
        ``on_line`` receives stdout lines (stderr lines when ``stderr_lines=True``; ``\\r`` also
        splits lines).  ``text=True`` decodes stdout as UTF-8 (``False`` keeps bytes).  Raises
        :class:`MissingTool`, :class:`ToolError` (last 15 stderr lines in ``details``) or
        :class:`Cancelled` (the whole process group is killed).
        """
        self.check_cancel()
        args = [os.fspath(a) if isinstance(a, os.PathLike) else str(a) for a in argv]
        if not args:
            raise ValueError("empty argv")
        tool = args[0]
        exe = tools.path(tool)
        if exe is None:
            raise MissingTool(tool)
        args[0] = exe
        shown = tools.display_name(tool) if not os.path.isabs(tool) else os.path.basename(tool)
        full_env = os.environ.copy()
        for k, v in (env or {}).items():
            if v is None:
                full_env.pop(k, None)
            else:
                full_env[k] = str(v)
        data = input.encode("utf-8") if isinstance(input, str) else input
        self.log("$ " + _mask_args([shown, *args[1:]]))
        kwargs: dict = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        job = self._job
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=os.fspath(cwd) if cwd is not None else None, env=full_env,
                                    close_fds=True, **kwargs)
        except FileNotFoundError:
            raise MissingTool(tool) from None
        except PermissionError as exc:
            raise ToolError("err.tool_not_executable", details=str(exc), tool=shown) from None
        except OSError as exc:
            raise ToolError("err.tool_failed", details=str(exc), tool=shown, code="?") from None
        with job.lock:
            job.procs.add(proc)
        cb_errors: list = []
        out_reader = _StreamReader(proc.stdout, keep=True, limit=None,
                                   on_line=None if stderr_lines else on_line,
                                   tail=0 if (stderr_lines or on_line is None) else 50, errors=cb_errors)
        err_reader = _StreamReader(proc.stderr, keep=True, limit=4 * 1024 * 1024,
                                   on_line=on_line if stderr_lines else None, tail=200, errors=cb_errors)
        out_reader.start()
        err_reader.start()
        writer = None
        if data is not None:
            def _write() -> None:
                try:
                    proc.stdin.write(data)  # type: ignore[union-attr]
                except (BrokenPipeError, OSError, ValueError):
                    pass
                finally:
                    try:
                        proc.stdin.close()  # type: ignore[union-attr]
                    except (OSError, ValueError):
                        pass

            writer = threading.Thread(target=_write, daemon=True, name="dropwheel-stdin")
            writer.start()
        start = time.monotonic()
        cancelled = timed_out = False
        try:
            while True:
                try:
                    proc.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if job.cancel_event.is_set():
                    cancelled = True
                    _kill_proc(proc)
                    break
                if timeout is not None and time.monotonic() - start > timeout:
                    timed_out = True
                    _kill_proc(proc)
                    break
                if cb_errors:
                    _kill_proc(proc)
                    break
            for th in (out_reader, err_reader):
                th.join(timeout=5)
            if out_reader.is_alive() or err_reader.is_alive():
                # a grandchild keeps the pipes open: kill the whole group, then give up on the pipes
                _kill_proc(proc, grace=0.5)
                for th in (out_reader, err_reader):
                    th.join(timeout=2)
            if writer is not None:
                writer.join(timeout=2)
        finally:
            with job.lock:
                job.procs.discard(proc)
        if cancelled or job.cancel_event.is_set() and proc.returncode not in set(ok_codes):
            raise Cancelled()
        stderr_text = err_reader.data().decode("utf-8", "replace")
        tail_lines = [ln for ln in err_reader.tail if ln.strip()][-STDERR_TAIL_LINES:]
        if not tail_lines and out_reader.chunks:
            tail_lines = [ln for ln in out_reader.data().decode("utf-8", "replace").splitlines() if ln.strip()][
                -STDERR_TAIL_LINES:]
        details = "\n".join(tail_lines)
        if timed_out:
            raise ToolError("err.timeout", details=details, tool=shown, seconds=int(timeout or 0))
        if cb_errors:
            raise cb_errors[0]
        rc = proc.returncode
        if rc not in set(ok_codes):
            code = rc if rc is None or rc >= 0 else f"{rc} (signal {-rc})"
            raise ToolError("err.tool_failed", details=details, tool=shown, code=code)
        raw_out = out_reader.data()
        stdout: Any = raw_out.decode("utf-8", "replace") if text else raw_out
        return subprocess.CompletedProcess(args, rc, stdout, stderr_text)

    def duration(self, path: str | os.PathLike) -> float | None:
        """Media duration in seconds via :meth:`probe` (``None`` when unknown)."""
        try:
            info = self.probe(path)
        except UserError:
            return None
        fmt = info.get("format") or {}
        for candidate in (fmt.get("duration"),):
            try:
                if candidate not in (None, "N/A"):
                    value = float(candidate)
                    if value > 0:
                        return value
            except (TypeError, ValueError):
                pass
        best = 0.0
        for stream in info.get("streams") or []:
            for candidate in (stream.get("duration"), (stream.get("tags") or {}).get("DURATION")):
                if not candidate or candidate == "N/A":
                    continue
                value = util.parse_time(candidate)
                if value and value > best:
                    best = value
        return best or None

    def probe(self, path: str | os.PathLike) -> dict:
        """``ffprobe -show_format -show_streams -show_chapters`` as dict (cached by path+mtime+size)."""
        p = os.path.abspath(os.fspath(path))
        try:
            st = os.stat(p)
            key = (p, st.st_mtime_ns, st.st_size)
        except OSError:
            key = (p, 0, 0)
        with _PROBE_LOCK:
            hit = _PROBE_CACHE.get(key)
        if hit is not None:
            return hit
        res = self.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        "-show_chapters", p], timeout=60)
        try:
            data = json.loads(res.stdout or "{}")
        except ValueError as exc:
            raise ToolError("err.probe_failed", details=str(exc), name=os.path.basename(p)) from None
        if not isinstance(data, dict):
            data = {}
        with _PROBE_LOCK:
            if len(_PROBE_CACHE) > 128:
                _PROBE_CACHE.clear()
            _PROBE_CACHE[key] = data
        return data

    def ffmpeg(self, args: Sequence[Any], *, src: str | os.PathLike | FileInfo | None = None,
               duration: float | None = None, label: str | None = None) -> subprocess.CompletedProcess:
        """Run ffmpeg with progress: prepends ``ffmpeg -hide_banner -nostdin -y -loglevel error -progress
        pipe:1 -nostats`` and maps ``out_time_us`` (microseconds, like ``out_time_ms``) to progress.

        The duration comes from ``duration``, else ffprobe on ``src`` (default: the first ``-i``
        input), capped by a ``-t`` option.  Do not write the output to stdout.
        """
        str_args = [os.fspath(a) if isinstance(a, os.PathLike) else str(a) for a in args]
        if duration is None:
            source: Any = src.path if isinstance(src, FileInfo) else src
            if source is None:
                for i, a in enumerate(str_args[:-1]):
                    if a == "-i":
                        cand = str_args[i + 1]
                        if cand.startswith("file:"):
                            cand = cand[5:]
                        if os.path.isfile(cand):
                            source = cand
                        break
            if source is not None and tools.have("ffprobe"):
                duration = self.duration(source)
        for i, a in enumerate(str_args[:-1]):
            if a == "-t":
                limit = util.parse_time(str_args[i + 1])
                if limit and limit > 0:
                    duration = min(duration, limit) if duration else limit
        argv = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1",
                "-nostats", *str_args]
        started = time.monotonic()
        if not duration:
            self.progress(None, label)

        def on_line(line: str) -> None:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key in ("out_time_us", "out_time_ms") and duration:
                if not value or value == "N/A":
                    return
                try:
                    seconds = max(0, int(value)) / 1_000_000.0
                except ValueError:
                    return
                frac = min(1.0, seconds / duration)
                message = label
                elapsed = time.monotonic() - started
                if frac > 0.02 and elapsed > 2:
                    left = elapsed * (1 - frac) / frac
                    eta = self.t("job.eta", time=util.human_duration(left))
                    message = f"{label} · {eta}" if label else eta
                self.progress(frac, message)
            elif key == "progress" and value == "end" and duration:
                self.progress(1.0, label)

        return self.run(argv, on_line=on_line)

    # ------------------------------------------------------------------ replace / result
    def replace_original(self, src: FileInfo, produced: str | os.PathLike) -> Path:
        """When ``params["replace"]`` is true, swap ``produced`` in place of the original (which is
        backed up to ``$XDG_STATE_HOME/dropwheel/backups/<job>/`` for Undo); otherwise a no-op.
        Returns the final path of the produced file."""
        produced_path = Path(os.fspath(produced))
        if not self.pbool("replace"):
            return produced_path
        orig = self.original(src)
        reason = ""
        try:
            lst = os.lstat(orig.path)
        except OSError:
            lst = None
        if lst is None:
            reason = "missing"
        elif orig.volatile or src.path in self._job.originals:
            reason = "volatile"
        elif os.path.islink(orig.path):
            reason = "symlink"
        elif getattr(lst, "st_nlink", 1) > 1 and not orig.is_dir:
            reason = "hardlink"
        elif not os.access(os.path.dirname(orig.path), os.W_OK):
            reason = "readonly"
        elif not produced_path.exists():
            reason = "missing"
        if reason:
            self.warn(self.t("warn.replace_skipped", reason=self.t(f"reason.{reason}")))
            return produced_path
        backup_dir = util.state_dir() / "backups" / self.job_id / str(len(self._job.backups))
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / orig.name
        orig_path = Path(orig.path)
        mode = None
        try:
            mode = os.stat(orig_path).st_mode
        except OSError:
            pass
        _stem, new_ext = util.split_name(produced_path.name, is_dir=produced_path.is_dir())
        same_name = new_ext == orig.ext
        if same_name:
            final = orig_path
        else:
            candidate = orig_path.parent / _fit_name(f"{orig.stem}.{new_ext}" if new_ext else orig.stem)
            if _norm_key(candidate, False) == _norm_key(produced_path, False) or os.path.lexists(candidate):
                # already the conversion name, or that name is taken: keep the produced name
                final = produced_path
            else:
                final = candidate
        moved = False
        try:
            os.rename(orig_path, backup)
            moved = True
        except OSError:
            try:
                if orig.is_dir:
                    shutil.copytree(orig_path, backup, symlinks=True)
                else:
                    shutil.copy2(orig_path, backup)
            except OSError as exc:
                raise UserError("err.backup_failed", details=str(exc), name=orig.name) from None
        try:
            if same_name and not moved:
                os.replace(produced_path, final)
            elif final != produced_path:
                if os.path.lexists(final) and not same_name:
                    raise UserError("err.output_exists", name=final.name)
                try:
                    os.replace(produced_path, final)
                except OSError:
                    shutil.move(str(produced_path), str(final))
            if not same_name and not moved:
                _remove(orig_path)
        except BaseException:
            if moved and not os.path.lexists(orig_path):
                try:
                    os.rename(backup, orig_path)
                except OSError:
                    shutil.move(str(backup), str(orig_path))
            raise
        if mode is not None and not orig.is_dir:
            try:
                os.chmod(final, mode & 0o7777)
            except OSError:
                pass
        record = {"original": str(orig_path), "backup": str(backup), "final": str(final)}
        self._backups.append(record)
        with self._job.lock:
            self._job.backups.append(record)
        return final

    def result(self, outputs: Iterable[str | os.PathLike] | str | os.PathLike = (), *, message: str = "",
               kind: str = "files", text: str | None = None, info: list[dict] | None = None,
               size_before: int | None = None, **extra: Any) -> Result:
        """Build a :class:`Result` (outputs are paths; ``extra`` keys go into the result JSON)."""
        if isinstance(outputs, (str, os.PathLike)):
            outs = [Path(os.fspath(outputs))]
        else:
            outs = [Path(os.fspath(o)) for o in outputs or []]
        return Result(outputs=outs, kind=kind, message=message, text=text, info=info, extra=dict(extra),
                      size_before=size_before)

    # ------------------------------------------------------------------ cleanup (runner use)
    def _rollback(self) -> None:
        """Undo partial work of a failed item: restore backups, delete reserved outputs."""
        for rec in reversed(self._backups):
            try:
                orig = Path(rec["original"])
                final = Path(rec["final"])
                if os.path.lexists(final) and final != orig:
                    _remove(final)
                elif final == orig and os.path.lexists(orig):
                    _remove(orig)
                os.replace(rec["backup"], orig)
            except OSError:
                log.exception("rollback of %s failed", rec)
            with self._job.lock:
                if rec in self._job.backups:
                    self._job.backups.remove(rec)
        start = self.started - 2
        for path in self._reserved_paths + self._created_dirs:
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if st.st_mtime >= start or path in self._created_dirs:
                _remove(path)


# ================================================================================== JobRunner

class JobRunner:
    """Runs actions in a thread pool and reports SPEC §5.2 events through ``emit``.

    Args:
        config: configuration dict (:func:`dropwheel.config.load` when ``None``).
        lang: UI language (resolved from the config when ``None``).
        emit: ``callable(event_dict)``; ``None`` = headless (CLI).
        thumb_fn: optional ``fn(Path) -> str|None`` filling ``outputs[].thumb``.
        notify_fn: notification function (default :func:`dropwheel.notify.notify`; ``False`` disables).
    """

    def __init__(self, config: dict | None = None, lang: str | None = None,
                 emit: Callable[[dict], None] | None = None, *, thumb_fn: Callable[[Path], Any] | None = None,
                 notify_fn: Callable[..., Any] | None | bool = None) -> None:
        self.config: dict = config if config is not None else cfgmod.load()
        self.lang: str = i18n.normalize_lang(lang or i18n.detect_lang(self.config))
        self.emit = emit
        self.thumb_fn = thumb_fn
        if notify_fn is None:
            from . import notify as notify_mod

            notify_fn = notify_mod.notify
        self.notify_fn = notify_fn or None
        #: whether the HUD is visible (daemon may update it); headless → notifications for long jobs
        self.hud_visible = emit is not None
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._emit_lock = threading.Lock()
        self._reserve_lock = threading.Lock()
        self._reserved: set[str] = set()
        self._seq = itertools.count(1)
        self._pool = self._make_pool()
        threading.Thread(target=self._cleanup_old, daemon=True, name="dropwheel-cleanup").start()

    # ------------------------------------------------------------------ setup
    def _max_parallel(self) -> int:
        try:
            value = int(cfgmod.get(self.config, "jobs.max_parallel", 2))
        except (TypeError, ValueError):
            value = 2
        return max(1, min(16, value))

    def _make_pool(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=self._max_parallel(), thread_name_prefix="dropwheel-job")

    def set_config(self, config: dict, lang: str | None = None) -> None:
        """Apply a new configuration (new pool size applies to new jobs)."""
        old = self._max_parallel()
        self.config = config
        self.lang = i18n.normalize_lang(lang or i18n.detect_lang(config))
        if self._max_parallel() != old:
            previous = self._pool
            self._pool = self._make_pool()
            previous.shutdown(wait=False)

    def shutdown(self, wait: bool = False) -> None:
        """Cancel every running job and stop the pool."""
        for job in list(self._jobs.values()):
            if not job.done:
                self.cancel(job.id)
        self._pool.shutdown(wait=wait, cancel_futures=True)

    def _cleanup_old(self) -> None:
        now = time.time()
        for base, max_age in ((util.cache_dir() / "staging", TMP_MAX_AGE), (util.cache_dir() / "tmp", TMP_MAX_AGE),
                              (util.state_dir() / "backups", BACKUP_MAX_AGE)):
            try:
                entries = list(os.scandir(base))
            except OSError:
                continue
            for entry in entries:
                try:
                    if now - entry.stat(follow_symlinks=False).st_mtime > max_age:
                        _remove(entry.path)
                except OSError:
                    continue

    # ------------------------------------------------------------------ public API
    def get(self, job_id: str) -> Job | None:
        """A job of this session."""
        return self._jobs.get(job_id)

    def jobs(self) -> list[Job]:
        """Jobs of this session (oldest first)."""
        return sorted(self._jobs.values(), key=lambda j: j.created)

    def _new_id(self) -> str:
        # the counter restarts in every daemon session and every `dropwheel run`, while the history (undo
        # markers), the backups and the staging folders are keyed by this id across sessions and processes:
        # 48 random bits make a clash with any of them practically impossible
        return f"j{next(self._seq)}-{secrets.token_hex(6)}"

    def _create(self, action_id: str, files: Sequence[FileInfo | str | os.PathLike], params: dict | None) -> Job:
        act = registry.get(action_id)
        if act is None:
            raise UserError("err.unknown_action", id=action_id)
        files = list(files or [])
        deep = len(files) <= 50
        infos = [f if isinstance(f, FileInfo) else classify(f, deep=deep) for f in files]
        if not infos:
            raise UserError("err.no_files")
        label = registry.display_label(action_id, self.lang)
        job = Job(self._new_id(), act, infos, dict(params or {}), label)
        with self._lock:
            self._jobs[job.id] = job
            if len(self._jobs) > 500:
                for old in sorted(self._jobs.values(), key=lambda j: j.created)[:100]:
                    if old.done:
                        self._jobs.pop(old.id, None)
        return job

    def submit(self, action_id: str, files: Sequence[FileInfo | str | os.PathLike], params: dict | None = None) -> str:
        """Queue a job and return its id (raises :class:`UserError` for unknown actions / no files)."""
        job = self._create(action_id, files, params)
        self._send(self._event(job))
        job.future = self._pool.submit(self._execute, job)
        return job.id

    def run_sync(self, action_id: str, files: Sequence[FileInfo | str | os.PathLike],
                 params: dict | None = None) -> Job:
        """Run a job in the calling thread and return it (final state in ``job.state``)."""
        job = self._create(action_id, files, params)
        self._send(self._event(job))
        self._execute(job)
        return job

    def wait(self, job_id: str, timeout: float | None = None) -> Job | None:
        """Block until a submitted job finishes."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.future is not None:
            try:
                job.future.result(timeout=timeout)
            except Exception:
                pass
        return job

    def cancel(self, job_id: str) -> bool:
        """Request cancellation; running processes are killed.  ``False`` for unknown/finished jobs."""
        job = self._jobs.get(job_id)
        if job is None or job.done:
            return False
        job.cancel_event.set()
        if job.state == "queued":
            if job.future is not None and job.future.cancel():
                self._finish(job, "cancelled")
        with job.lock:
            procs = list(job.procs)
        for proc in procs:
            threading.Thread(target=_kill_proc, args=(proc,), daemon=True, name="dropwheel-kill").start()
        return True

    # ------------------------------------------------------------------ events
    def _send(self, event: dict) -> None:
        if self.emit is None:
            return
        with self._emit_lock:
            try:
                self.emit(event)
            except Exception:
                log.exception("emit failed")

    def _event(self, job: Job, **extra: Any) -> dict:
        with job.lock:
            ev = {
                "event": "job", "job": job.id, "state": job.state, "action": job.action.id, "label": job.label,
                "files": [f.name for f in job.files], "paths": [f.path or f.uri for f in job.files],
                "progress": job.progress, "message": job.message, "warnings": list(job.warnings),
            }
        ev.update(extra)
        return ev

    def _emit_progress(self, job: Job) -> None:
        now = time.monotonic()
        send = False
        delay = 0.0
        with job.lock:
            if job.state != "running":
                return
            due = job.last_emit + PROGRESS_INTERVAL
            if now >= due and not job.timer_active:
                job.last_emit = now
                job.pending = False
                send = True
            else:
                job.pending = True
                if not job.timer_active:
                    job.timer_active = True
                    delay = max(0.0, due - now)
                    timer = threading.Timer(delay, self._flush, args=(job,))
                    timer.daemon = True
                    timer.start()
        if send:
            self._send(self._event(job))

    def _flush(self, job: Job) -> None:
        with job.lock:
            job.timer_active = False
            if not job.pending or job.state != "running":
                return
            job.pending = False
            job.last_emit = time.monotonic()
        self._send(self._event(job))

    def _warn(self, job: Job, msg: str) -> None:
        with job.lock:
            if msg in job.warnings:
                return
            job.warnings.append(msg)
            running = job.state == "running"
            if running:
                job.last_emit = time.monotonic()
        if running:
            self._send(self._event(job))

    def _warn_once(self, job: Job, key: str, msg: str) -> None:
        with job.lock:
            if key in job.warned:
                return
            job.warned.add(key)
        self._warn(job, msg)

    # ------------------------------------------------------------------ execution
    def _execute(self, job: Job) -> None:
        if job.cancel_event.is_set():
            self._finish(job, "cancelled")
            return
        with job.lock:
            job.state = "running"
            job.started = time.time()
            job.progress = None
            job.last_emit = time.monotonic()
        self._send(self._event(job))
        try:
            result = self._run_job(job)
        except Cancelled as exc:
            partial = exc.partial if isinstance(exc.partial, Result) else None
            self._finish(job, "cancelled", result=partial)
        except UserError as exc:
            self._finish(job, "failed", error=exc.to_json(self.lang))
        except Exception:
            log.exception("job %s (%s) crashed", job.id, job.action.id)
            self._finish(job, "failed", error={"message": i18n.t("err.unexpected", self.lang),
                                               "details": traceback.format_exc(), "install": ""})
        else:
            if job.cancel_event.is_set() and not (result.outputs or job.backups):
                self._finish(job, "cancelled")
            else:
                self._finish(job, "done", result=result)
        finally:
            self._cleanup_job(job)

    def _filter_applicable(self, job: Job) -> list[FileInfo]:
        act = job.action
        files = list(job.files)
        if act.multi == "each":
            ok = [f for f in files if act.matches_category(f.category) and act.when_ok([f])]
        else:
            ok = [f for f in files if act.matches_category(f.category)]
            if ok and not act.when_ok(ok):
                ok = []
        if not ok or not act.multi_ok(len(ok)):
            raise UserError("err.not_applicable", label=job.label)
        skipped = len(files) - len(ok)
        if skipped:
            self._warn(job, i18n.tn("warn.skipped", skipped, self.lang))
        return ok

    def _validate_input(self, f: FileInfo) -> None:
        if f.category == "url":
            return
        if not f.path or not os.path.exists(f.path):
            raise UserError("err.file_not_found", name=f.name or f.path)
        mode = os.R_OK | os.X_OK if f.is_dir else os.R_OK
        if not os.access(f.path, mode):
            raise UserError("err.not_readable", name=f.name)

    def _stage(self, job: Job, f: FileInfo, index: int) -> FileInfo:
        if f.category == "url" or not f.volatile or not f.exists:
            return f
        root = util.cache_dir() / "staging" / job.id
        dest_dir = root / str(index)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f.name
            if f.is_dir:
                shutil.copytree(f.path, dest, symlinks=True)
            else:
                shutil.copy2(f.path, dest)
        except FileNotFoundError:
            raise UserError("err.file_not_found", name=f.name) from None
        except OSError as exc:
            raise UserError("err.stage_failed", details=str(exc), name=f.name) from None
        job.staging = root
        staged = classify(dest)
        staged.volatile = True
        with job.lock:
            job.originals[staged.path] = f
        return staged

    def _check_space(self, job: Job, files: list[FileInfo]) -> None:
        need = sum(f.size for f in files if f.exists and not f.is_dir)
        if need <= 0:
            return
        targets: set[str] = set()
        if cfgmod.get(self.config, "output.location") == "folder":
            targets.add(str(cfgmod.output_folder(self.config)))
        else:
            for f in files:
                if f.path and not f.volatile and os.access(os.path.dirname(f.path) or ".", os.W_OK):
                    targets.add(os.path.dirname(f.path))
                else:
                    targets.add(str(util.fallback_output_dir()))
        if any(f.volatile for f in files):
            targets.add(str(util.cache_dir()))
        for target in targets:
            free = util.free_space(target)
            if free is not None and free < need * 1.2 + 20 * 1024 * 1024:
                self._warn(job, i18n.t("warn.low_disk", self.lang, free=util.human_size(free, self.lang),
                                       dir=_display_dir(Path(target))))
                break

    @staticmethod
    def _normalize_result(value: Any) -> Result:
        if value is None:
            return Result(kind="none")
        if isinstance(value, Result):
            return value
        if isinstance(value, (str, os.PathLike)):
            return Result(outputs=[Path(os.fspath(value))])
        if isinstance(value, dict):
            data = dict(value)
            outs = [Path(os.fspath(p)) for p in data.pop("outputs", []) or []]
            return Result(outputs=outs, kind=data.pop("kind", "files"), message=data.pop("message", ""),
                          text=data.pop("text", None), info=data.pop("info", None),
                          size_before=data.pop("size_before", None), extra=data)
        if isinstance(value, (list, tuple)):
            return Result(outputs=[Path(os.fspath(p)) for p in value])
        raise TypeError(f"action returned unsupported value {type(value).__name__}")

    def _call(self, ctx: Context) -> Result:
        try:
            value = ctx.action.fn(ctx)
            res = self._normalize_result(value)
        except BaseException:
            ctx._rollback()
            raise
        kept = []
        for p in res.outputs:
            if os.path.lexists(p):
                kept.append(Path(p))
            else:
                log.warning("action %s reported missing output %s", ctx.action.id, p)
        res.outputs = kept
        if res.kind == "files" and not kept and not ctx._backups and res.text is None and not res.info:
            res.kind = "none"
        return res

    def _run_job(self, job: Job) -> Result:
        act = job.action
        files = self._filter_applicable(job)
        missing = tools.missing(act.requires)
        if missing:
            raise MissingTool(missing)
        job.params = act.resolve_params(files, job.given_params, self.config)
        if act.multi != "each":
            for f in files:
                self._validate_input(f)
            self._check_space(job, files)
            staged = [self._stage(job, f, i) for i, f in enumerate(files)]
            ctx = Context(self, job, staged, 0, 1)
            return self._call(ctx)
        self._check_space(job, [f for f in files if f.exists])
        total = len(files)
        results: list[tuple[FileInfo, Result]] = []
        errors: list[tuple[FileInfo, dict]] = []
        for index, f in enumerate(files):
            if job.cancel_event.is_set():
                exc = Cancelled()
                exc.partial = self._aggregate(job, results, errors, total)
                raise exc
            try:
                self._validate_input(f)
                staged = self._stage(job, f, index)
                ctx = Context(self, job, [staged], index, total)
                if total > 1:
                    ctx.progress(0.0)
                results.append((f, self._call(ctx)))
            except Cancelled as exc:
                exc.partial = self._aggregate(job, results, errors, total)
                raise
            except UserError as exc:
                if total == 1:
                    raise
                errors.append((f, exc.to_json(self.lang)))
            except Exception:
                if total == 1:
                    raise
                log.exception("job %s: %s failed", job.id, f.path)
                errors.append((f, {"message": i18n.t("err.unexpected", self.lang),
                                   "details": traceback.format_exc(), "install": ""}))
        if errors and not results:
            messages = {e["message"] for _, e in errors}
            message = errors[0][1]["message"] if len(messages) == 1 else i18n.t(
                "job.batch_summary", self.lang, done=0, total=total, failed=len(errors))
            details = "\n\n".join(f"{f.name}: {e['message']}" + (f"\n{e['details']}" if e.get("details") else "")
                                  for f, e in errors)
            install = next((e["install"] for _, e in errors if e.get("install")), "")
            raise UserError(message, details=details, install=install)
        return self._aggregate(job, results, errors, total)

    def _aggregate(self, job: Job, results: list[tuple[FileInfo, Result]], errors: list[tuple[FileInfo, dict]],
                   total: int) -> Result:
        if total == 1 and results and not errors:
            return results[0][1]
        outputs: list[Path] = []
        texts: list[str] = []
        infos: list[dict] = []
        extra: dict = {}
        messages: list[str] = []
        kinds: list[str] = []
        size_before: int | None = None
        for f, res in results:
            outputs.extend(res.outputs)
            if res.text:
                texts.append(f"{f.name}:\n{res.text}" if total > 1 else res.text)
            for row in res.info or []:
                row = dict(row)
                if total > 1:
                    row["group"] = f"{f.name} · {row.get('group', '')}".rstrip(" ·")
                infos.append(row)
            if res.size_before is not None:
                size_before = (size_before or 0) + int(res.size_before)
            for k, v in res.extra.items():
                extra.setdefault(k, v)
            if res.message:
                messages.append(res.message)
            kinds.append(res.kind)
        kind = next((k for k in kinds if k != "none"), "none")
        if outputs and kind == "none":
            kind = "files"
        done = len(results)
        failed = len(errors)
        if failed:
            message = i18n.t("job.batch_summary", self.lang, done=done, total=total, failed=failed)
            extra["errors"] = [{"file": f.name, **e} for f, e in errors]
            for f, e in errors:
                self._warn(job, f"{f.name}: {e['message']}")
        elif len(set(messages)) == 1 and len(messages) == done:
            message = messages[0]
        else:
            message = i18n.tn("job.batch_done", done, self.lang) if done else ""
        return Result(outputs=outputs, kind=kind, message=message, text="\n\n".join(texts) if texts else None,
                      info=infos or None, extra=extra, size_before=size_before)

    # ------------------------------------------------------------------ finishing
    def _result_json(self, job: Job, res: Result) -> dict:
        outs = []
        for i, p in enumerate(res.outputs):
            info = classify(p, deep=False)
            size = util.dir_size(p) if info.is_dir else info.size
            thumb = None
            if self.thumb_fn is not None and i < 12 and not info.is_dir:
                try:
                    value = self.thumb_fn(Path(p))
                    if isinstance(value, dict):
                        value = value.get("png")
                    thumb = str(value) if value else None
                except Exception:
                    log.debug("thumb failed for %s", p, exc_info=True)
            outs.append({"path": str(p), "uri": info.uri, "name": info.name, "size": size,
                         "size_h": util.human_size(size, self.lang), "category": info.category, "mime": info.mime,
                         "thumb": thumb, "is_dir": info.is_dir})
        size_after = sum(o["size"] for o in outs) if outs else None
        data: dict[str, Any] = {
            "kind": res.kind,
            "message": res.message or "",
            "outputs": outs,
            "size_before": res.size_before,
            "size_after": size_after,
            "undoable": bool((outs and job.action.undoable) or job.backups),
            "text": res.text,
            "info": res.info,
        }
        if res.size_before and size_after is not None and res.size_before > 0:
            data["size_before_h"] = util.human_size(res.size_before, self.lang)
            data["size_after_h"] = util.human_size(size_after, self.lang)
            data["delta"] = round((size_after - res.size_before) * 100.0 / res.size_before)
        for key, value in res.extra.items():
            if key not in data or data[key] is None:
                data[key] = registry._jsonable(value)
        return data

    def _finish(self, job: Job, state: str, result: Result | None = None, error: dict | None = None) -> None:
        with job.lock:
            if job.done:
                return
            job.state = state
            job.finished = time.time()
            if result is not None:
                job.outputs = list(result.outputs)
            if state == "done":
                job.progress = 1.0
            if error is not None:
                job.error = error
                job.message = error.get("message", "")
        if result is not None:
            try:
                job.result = self._result_json(job, result)
            except Exception:
                log.exception("result json failed")
                job.result = {"kind": result.kind, "message": result.message, "outputs": [],
                              "undoable": False}
        if state in ("done", "cancelled") and (job.outputs or job.backups):
            self._record_history(job)
        extra: dict[str, Any] = {}
        if job.result is not None:
            extra["result"] = job.result
        if job.error is not None:
            extra["error"] = job.error
        self._send(self._event(job, **extra))
        try:
            self._after_finish(job)
        except Exception:
            log.exception("post-job hooks failed")

    def _after_finish(self, job: Job) -> None:
        if job.state == "done" and job.outputs and cfgmod.get(self.config, "output.open_after", False):
            util.open_path(job.outputs[0])
        self._maybe_notify(job)

    def _maybe_notify(self, job: Job) -> None:
        if not self.notify_fn or job.state == "cancelled":
            return
        mode = cfgmod.get(self.config, "notifications.mode", "long")
        if mode == "never":
            return
        duration = (job.finished or time.time()) - (job.started or job.created)
        if not (mode == "always" or duration > 10 or not self.hud_visible):
            return
        if job.state == "done":
            res = job.result or {}
            outs = res.get("outputs") or []
            parts = []
            if res.get("message"):
                parts.append(res["message"])
            if outs:
                names = ", ".join(o["name"] for o in outs[:3])
                if len(outs) > 3:
                    names += f" (+{len(outs) - 3})"
                parts.append(names)
            if res.get("size_before_h") and res.get("size_after_h"):
                parts.append(f"{res['size_before_h']} → {res['size_after_h']}")
            icon = ""
            actions: list[dict] = []
            if outs:
                first = outs[0]
                icon = first.get("thumb") or (first["path"] if first.get("category") == "image" else "")
                actions = [
                    {"id": "open", "label": i18n.t("notify.open", self.lang), "op": "open", "path": first["path"]},
                    {"id": "reveal", "label": i18n.t("notify.reveal", self.lang), "op": "reveal",
                     "paths": [o["path"] for o in outs],
                     "file_manager": str(cfgmod.get(self.config, "apps.file_manager", "") or "")},
                ]
            self.notify_fn(job.label, "\n".join(parts), icon, actions, urgency="normal", kind="success")
        elif job.state == "failed":
            err = job.error or {}
            self.notify_fn(i18n.t("notify.failed", self.lang, label=job.label), err.get("message", ""), "dialog-error",
                           None, urgency="critical", kind="error")

    def _cleanup_job(self, job: Job) -> None:
        for path in (job.tmp_root, job.staging):
            if path is not None:
                _remove(path)
        with self._reserve_lock:
            for key in job.reserved:
                self._reserved.discard(key)
            job.reserved.clear()

    # ------------------------------------------------------------------ history / undo
    @staticmethod
    def history_path() -> Path:
        """``$XDG_STATE_HOME/dropwheel/history.jsonl``."""
        return util.state_dir() / "history.jsonl"

    def _history_entry(self, job: Job) -> dict:
        outs = []
        for p in job.outputs:
            try:
                st = os.lstat(p)
                outs.append({"path": str(p), "size": st.st_size, "mtime": st.st_mtime,
                             "is_dir": os.path.isdir(p) and not os.path.islink(p)})
            except OSError:
                continue
        originals = [job.originals.get(f.path, f) for f in job.files]
        return {
            "type": "job", "job": job.id, "action": job.action.id, "label": job.label,
            "time": datetime.now().isoformat(timespec="seconds"), "state": job.state,
            "files": [f.path or f.uri for f in originals], "outputs": outs, "backups": list(job.backups),
            "undoable": bool((outs and job.action.undoable) or job.backups),
        }

    def _record_history(self, job: Job) -> None:
        self._append_history(self._history_entry(job))

    def _append_history(self, entry: dict) -> None:
        path = self.history_path()
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _HISTORY_LOCK:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line)
                if path.stat().st_size > HISTORY_MAX_BYTES:
                    self._trim_history(path)
            except OSError:
                log.exception("cannot write history")

    @staticmethod
    def _trim_history(path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        job_lines = [i for i, ln in enumerate(lines) if '"type": "job"' in ln]
        if len(job_lines) <= HISTORY_KEEP:
            return
        cutoff = job_lines[-HISTORY_KEEP]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(lines[cutoff:]) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def _read_history(self) -> list[dict]:
        path = self.history_path()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def history(self, limit: int = 50) -> list[dict]:
        """Recent job entries, newest first, each with an ``undone`` flag."""
        entries = self._read_history()
        undone = {e.get("job") for e in entries if e.get("type") == "undo"}
        jobs = [dict(e, undone=e.get("job") in undone) for e in entries if e.get("type") == "job"]
        jobs.reverse()
        return jobs[: max(0, int(limit))] if limit else jobs

    def undo(self, job_id: str | None = None) -> dict:
        """Undo a job: outputs go to the trash, replaced originals are restored.

        ``job_id=None`` undoes the most recent undoable job.  Returns ``{ok, message, trashed,
        restored, problems}``.
        """
        entries = self._read_history()
        undone = {e.get("job") for e in entries if e.get("type") == "undo"}
        entry = None
        if job_id is None:
            for e in reversed(entries):
                if e.get("type") == "job" and e.get("undoable") and e.get("job") not in undone:
                    entry = e
                    break
        else:
            for e in reversed(entries):
                if e.get("type") == "job" and e.get("job") == job_id:
                    entry = e
                    break
            if entry is None:
                job = self._jobs.get(job_id)
                if job is not None and job.done and (job.outputs or job.backups):
                    entry = self._history_entry(job)
        if entry is None:
            # no id given: there is simply nothing left to undo; an explicit id that is unknown is "not found"
            key = "undo.nothing" if job_id is None else "undo.not_found"
            return {"ok": False, "message": i18n.t(key, self.lang), "trashed": 0, "restored": 0,
                    "problems": []}
        jid = entry.get("job")
        if jid in undone or (jid in self._jobs and self._jobs[jid].undone):
            return {"ok": False, "message": i18n.t("undo.already", self.lang), "trashed": 0, "restored": 0,
                    "problems": []}
        if not entry.get("undoable"):
            return {"ok": False, "message": i18n.t("undo.not_possible", self.lang), "trashed": 0, "restored": 0,
                    "problems": []}
        trashed = restored = 0
        problems: list[str] = []
        backups = entry.get("backups") or []
        for out in entry.get("outputs") or []:
            p = Path(out.get("path", ""))
            if not str(p) or not os.path.lexists(p):
                continue
            if not out.get("is_dir"):
                try:
                    st = os.lstat(p)
                    changed = st.st_size != out.get("size") or abs(st.st_mtime - float(out.get("mtime") or 0)) > 2
                except OSError:
                    changed = True
                if changed:
                    problems.append(i18n.t("undo.changed", self.lang, name=p.name))
                    continue
            try:
                util.trash(p)
                trashed += 1
            except UserError as exc:
                problems.append(exc.text(self.lang))
        for rec in reversed(backups):
            backup = Path(rec.get("backup", ""))
            orig = Path(rec.get("original", ""))
            if not str(backup) or not os.path.lexists(backup):
                problems.append(i18n.t("undo.backup_missing", self.lang, name=orig.name))
                continue
            target = orig
            if os.path.lexists(orig):
                stem, ext = util.split_name(orig.name, is_dir=backup.is_dir())
                restored_name = f"{stem} ({i18n.t('sfx.restored', self.lang)})" + (f".{ext}" if ext else "")
                target = util.unique_path(orig.parent / _fit_name(restored_name))
            try:
                try:
                    os.rename(backup, target)
                except OSError:
                    shutil.move(str(backup), str(target))
                restored += 1
            except OSError as exc:
                problems.append(f"{orig.name}: {exc}")
        self._append_history({"type": "undo", "job": jid, "time": datetime.now().isoformat(timespec="seconds"),
                              "trashed": trashed, "restored": restored})
        job = self._jobs.get(jid)
        if job is not None:
            job.undone = True
        ok = (trashed + restored) > 0 or not problems
        parts = []
        if trashed:
            parts.append(i18n.tn("undo.done", trashed, self.lang))
        if restored:
            parts.append(i18n.tn("undo.restored", restored, self.lang))
        if not trashed and not restored and not problems:
            parts.append(i18n.t("undo.nothing", self.lang))
        parts.extend(problems)
        # separate statements: "Annullato: 1 file nel cestino · Originale ripristinato"
        return {"ok": ok, "message": " · ".join(parts), "trashed": trashed, "restored": restored,
                "problems": problems}
