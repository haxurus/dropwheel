"""Shared helpers: XDG paths, naming, sizes, clipboard, open/reveal, trash, terminals, desktop apps.

Every function that talks to the desktop is defensive: it never raises for a missing tool
unless documented (``trash`` raises :class:`~dropwheel.errors.UserError` when nothing worked),
uses argv lists (never ``shell=True``), applies timeouts, and never inherits the caller's
stdout (the daemon's stdout is the JSON protocol channel).
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from . import tools
from .errors import UserError

log = logging.getLogger("dropwheel.util")

__all__ = [
    "xdg_config_home", "xdg_cache_home", "xdg_state_home", "xdg_data_home", "xdg_runtime_dir",
    "xdg_data_dirs", "xdg_config_dirs", "download_dir", "config_dir", "cache_dir", "state_dir",
    "fallback_output_dir", "ensure_dir",
    "human_size", "human_duration", "parse_time", "split_name", "MULTI_EXTS", "NAME_MAX", "truncate_bytes",
    "safe_filename",
    "unique_path", "natural_key", "natural_sorted", "path_to_uri", "uri_to_path", "gvfs_path",
    "volatile_roots", "is_volatile", "fs_type", "is_case_insensitive_fs", "needs_portable_names",
    "free_space", "dir_size", "detach", "run_quiet", "open_path", "reveal_path", "copy_text",
    "copy_files", "files_clipboard_type", "copy_image", "read_clipboard_files", "parse_uri_list", "trash",
    "find_terminal", "launch_terminal", "desktop_apps_for", "find_desktop_file", "launch_desktop_app",
]

# ------------------------------------------------------------------------------------------ XDG


def _env_dir(var: str, default: str) -> Path:
    value = os.environ.get(var, "")
    if value and os.path.isabs(value):
        return Path(value)
    return Path(os.path.expanduser(default))


def xdg_config_home() -> Path:
    """``$XDG_CONFIG_HOME`` or ``~/.config``."""
    return _env_dir("XDG_CONFIG_HOME", "~/.config")


def xdg_cache_home() -> Path:
    """``$XDG_CACHE_HOME`` or ``~/.cache``."""
    return _env_dir("XDG_CACHE_HOME", "~/.cache")


def xdg_state_home() -> Path:
    """``$XDG_STATE_HOME`` or ``~/.local/state``."""
    return _env_dir("XDG_STATE_HOME", "~/.local/state")


def xdg_data_home() -> Path:
    """``$XDG_DATA_HOME`` or ``~/.local/share``."""
    return _env_dir("XDG_DATA_HOME", "~/.local/share")


def xdg_runtime_dir() -> Path | None:
    """``$XDG_RUNTIME_DIR`` (``/run/user/$UID``) or ``None``."""
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    if value and os.path.isabs(value):
        return Path(value)
    if hasattr(os, "getuid"):
        candidate = Path(f"/run/user/{os.getuid()}")
        if candidate.is_dir():
            return candidate
    return None


def xdg_data_dirs() -> list[Path]:
    """``$XDG_DATA_DIRS`` (default ``/usr/local/share:/usr/share``)."""
    value = os.environ.get("XDG_DATA_DIRS", "") or "/usr/local/share:/usr/share"
    return [Path(p) for p in value.split(":") if p and os.path.isabs(p)]


def xdg_config_dirs() -> list[Path]:
    """``$XDG_CONFIG_DIRS`` (default ``/etc/xdg``)."""
    value = os.environ.get("XDG_CONFIG_DIRS", "") or "/etc/xdg"
    return [Path(p) for p in value.split(":") if p and os.path.isabs(p)]


def download_dir() -> Path:
    """The user's download directory (``$XDG_DOWNLOAD_DIR``, ``user-dirs.dirs`` or ``~/Downloads``)."""
    env = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if env and os.path.isabs(os.path.expanduser(env)):
        return Path(os.path.expanduser(env))
    home = os.path.expanduser("~")
    try:
        text = (xdg_config_home() / "user-dirs.dirs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("XDG_DOWNLOAD_DIR="):
            continue
        value = line.split("=", 1)[1].strip().strip('"')
        value = value.replace("$HOME", home).replace("${HOME}", home)
        if os.path.isabs(value) and os.path.normpath(value) != os.path.normpath(home):
            return Path(value)
    return Path(home) / "Downloads"


def config_dir() -> Path:
    """``~/.config/dropwheel``."""
    return xdg_config_home() / "dropwheel"


def cache_dir() -> Path:
    """``~/.cache/dropwheel``."""
    return xdg_cache_home() / "dropwheel"


def state_dir() -> Path:
    """``~/.local/state/dropwheel``."""
    return xdg_state_home() / "dropwheel"


def fallback_output_dir() -> Path:
    """Where outputs go when the source folder is not usable: ``<Downloads>/Dropwheel``."""
    return download_dir() / "Dropwheel"


def ensure_dir(path: str | os.PathLike, mode: int = 0o755) -> Path:
    """Create ``path`` (and parents) if needed and return it as :class:`Path`."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True, mode=mode)
    return p


# -------------------------------------------------------------------------------- formatting

_SIZE_UNITS = ("kB", "MB", "GB", "TB", "PB", "EB")


def human_size(n: int | float | None, lang: str = "en") -> str:
    """Format a byte count with SI units like GLib (1 kB = 1000 B); Italian uses a decimal comma.

    >>> human_size(2400000, "it")
    '2,4 MB'
    """
    if n is None:
        return ""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n < 1000:
        return f"{sign}{n} B"
    value = float(n)
    unit = _SIZE_UNITS[0]
    for unit in _SIZE_UNITS:
        value /= 1000.0
        if round(value, 1 if value < 100 else 0) < 1000:
            break
    text = f"{value:.1f}" if value < 99.95 else f"{value:.0f}"
    if text.endswith(".0"):
        text = text[:-2]
    if (lang or "en").startswith("it"):
        text = text.replace(".", ",")
    return f"{sign}{text} {unit}"


def human_duration(seconds: float | None) -> str:
    """``75`` -> ``"1:15"``; ``3725`` -> ``"1:02:05"``."""
    if seconds is None:
        return ""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)\s*$")


def parse_time(value: str | float | int | None) -> float | None:
    """Parse ``"90"``, ``"1:30"``, ``"00:01:30.5"``, ``"1,5"`` or a number into seconds (``None`` if invalid)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    text = str(value).strip().lower()
    if text.endswith("s") and not text.endswith("ms"):
        text = text[:-1]
    m = _TIME_RE.match(text)
    if not m:
        return None
    a, b, c = m.groups()
    secs = float(c.replace(",", "."))
    if a is not None and b is not None:
        return int(a) * 3600 + int(b) * 60 + secs
    if a is not None:
        return int(a) * 60 + secs
    return secs


# -------------------------------------------------------------------------------------- names

#: Compound extensions kept together (lower case, longest first).
MULTI_EXTS: tuple[str, ...] = (
    "pkg.tar.zst", "pkg.tar.xz", "pkg.tar.gz", "pkg.tar.bz2",
    "tar.gz", "tar.xz", "tar.zst", "tar.bz2", "tar.lz4", "tar.lzma", "tar.lz", "tar.br", "tar.sz", "tar.z",
    "7z.001", "zip.001", "tar.001",
)
_EXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+~-]{0,11}$")


def split_name(name: str, is_dir: bool = False) -> tuple[str, str]:
    """Split a file name into ``(stem, ext)``; ``ext`` is lower case without the dot.

    Compound extensions (``tar.gz``) stay together, dot files (``.bashrc``) have no extension,
    folders never have one.
    """
    if is_dir or not name:
        return name, ""
    low = name.lower()
    for multi in MULTI_EXTS:
        if low.endswith("." + multi) and len(name) > len(multi) + 1:
            return name[: -(len(multi) + 1)], multi
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or not ext or not _EXT_RE.match(ext):
        return name, ""
    return stem, ext.lower()


#: Longest file name (a single path component) on Linux filesystems, in bytes.
NAME_MAX = 255

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PORTABLE_BAD_RE = re.compile(r'[<>:"\\|?*]')
_ELLIPSIS = "…"


def truncate_bytes(text: str, max_bytes: int) -> str:
    """Cut ``text`` to at most ``max_bytes`` UTF-8 bytes on a grapheme-ish boundary."""
    if max_bytes <= 0:
        return ""
    data = text.encode("utf-8", "surrogateescape")
    if len(data) <= max_bytes:
        return text
    cut = data[:max_bytes].decode("utf-8", "ignore")
    # do not leave a dangling combining mark / joiner / variation selector
    while cut and (unicodedata.combining(cut[-1]) or cut[-1] in "‍︎️"):
        cut = cut[:-1]
    if cut and "\ud800" <= cut[-1] <= "\udfff":
        cut = cut[:-1]
    return cut


def safe_filename(name: str, *, max_bytes: int = NAME_MAX, portable: bool = False, replacement: str = " ") -> str:
    """Make ``name`` usable as a single path component.

    Control characters become ``replacement`` (a space), ``/`` becomes ``-``, ``portable=True``
    (FAT/exFAT/NTFS targets) also replaces ``<>:"\\|?*`` and strips trailing dots/spaces.  Names
    longer than ``max_bytes`` UTF-8 bytes are shortened keeping the extension and appending ``…``.
    """
    text = str(name)
    text = text.replace("/", "-").replace("\x00", "")
    text = _CTRL_RE.sub(replacement, text)
    if portable or os.name == "nt":
        text = _PORTABLE_BAD_RE.sub("_", text)
        text = text.rstrip(". ")
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text in ("", ".", ".."):
        text = "file"
    if len(text.encode("utf-8", "surrogateescape")) <= max_bytes:
        return text
    stem, ext = split_name(text)
    tail = ("." + ext) if ext else ""
    budget = max_bytes - len(tail.encode("utf-8")) - len(_ELLIPSIS.encode("utf-8"))
    # keep a trailing " (suffix)" intact when there is room
    m = re.match(r"^(.*?)( \([^()]{1,60}\)(?: \d+)?)$", stem)
    keep = ""
    if m and len(m.group(2).encode("utf-8", "surrogateescape")) < budget // 2:
        stem, keep = m.group(1), m.group(2)
        budget -= len(keep.encode("utf-8", "surrogateescape"))
    short = truncate_bytes(stem, max(budget, 1)).rstrip()
    return f"{short}{_ELLIPSIS}{keep}{tail}"


def _norm_key(path: str | os.PathLike, ci: bool) -> str:
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    return key.lower() if ci else key


def unique_path(path: str | os.PathLike, *, taken: Iterable[str | os.PathLike] = (), ci: bool | None = None,
                is_dir: bool = False) -> Path:
    """Return ``path`` or the first free variant ``"<stem> 2.<ext>"``, ``"<stem> 3.<ext>"``…

    A candidate is free when nothing exists there (``lexists``) and it is not in ``taken``
    (compared case-insensitively on case-insensitive filesystems).
    """
    p = Path(path)
    if ci is None:
        ci = is_case_insensitive_fs(p.parent)
    taken_keys = {_norm_key(t, ci) for t in taken}

    def free(candidate: Path) -> bool:
        return not os.path.lexists(candidate) and _norm_key(candidate, ci) not in taken_keys

    if free(p):
        return p
    stem, ext = split_name(p.name, is_dir=is_dir)
    tail = f".{ext}" if ext else ""
    for n in range(2, 100000):
        counter = f" {n}{tail}"
        # the name may already use the whole NAME_MAX, and lexists() calls an over-long name free
        head = truncate_bytes(stem, NAME_MAX - len(counter.encode("utf-8", "surrogateescape")))
        candidate = p.parent / f"{head}{counter}"
        if free(candidate):
            return candidate
    raise UserError("err.no_free_name", name=p.name)


_NAT_RE = re.compile(r"(\d+)")


def natural_key(text: str | os.PathLike) -> list:
    """Sort key giving "file 2" < "file 10" (case-insensitive)."""
    s = os.fspath(text) if not isinstance(text, str) else text
    return [(0, int(tok), "") if tok.isdigit() else (1, 0, tok.casefold()) for tok in _NAT_RE.split(s) if tok]


def natural_sorted(items: Iterable, key=None) -> list:
    """``sorted`` with :func:`natural_key` (``key`` maps an item to its text)."""
    if key is None:
        return sorted(items, key=natural_key)
    return sorted(items, key=lambda it: natural_key(key(it)))


# --------------------------------------------------------------------------------------- URIs


def path_to_uri(path: str | os.PathLike) -> str:
    """Absolute path -> percent-encoded ``file://`` URI (handles non-UTF-8 bytes)."""
    p = Path(os.path.abspath(os.fspath(path)))
    try:
        return p.as_uri()
    except ValueError:
        return "file://" + urllib.parse.quote(os.fspath(p))


def uri_to_path(uri: str) -> str:
    """``file://`` URI -> local path (percent-decoded as bytes, RFC 8089).  Other strings are returned as-is."""
    text = uri.strip()
    if not text.lower().startswith("file:"):
        return text
    parts = urllib.parse.urlsplit(text)
    raw = parts.path or ""
    if not parts.netloc and not raw.startswith("/"):
        raw = "/" + raw
    decoded = os.fsdecode(urllib.parse.unquote_to_bytes(raw))
    if os.name == "nt":
        if re.match(r"^/[A-Za-z]:", decoded):
            decoded = decoded[1:]
        if parts.netloc and parts.netloc.lower() not in ("", "localhost"):
            decoded = f"//{parts.netloc}{decoded}"
        return str(Path(decoded))
    return decoded


_GVFS_SCHEMES = {"sftp", "ssh", "ftp", "ftps", "smb", "dav", "davs", "mtp", "afp", "nfs", "gphoto2", "google-drive", "onedrive"}


def gvfs_path(uri: str) -> str | None:
    """Map a gvfs URI (``sftp://host/path``, ``smb://srv/share/p``, ``mtp://dev/p``) to its FUSE path.

    Returns ``None`` when no matching mount exists under ``$XDG_RUNTIME_DIR/gvfs``.
    """
    try:
        parts = urllib.parse.urlsplit(uri.strip())
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _GVFS_SCHEMES:
        return None
    runtime = xdg_runtime_dir()
    if runtime is None:
        return None
    root = runtime / "gvfs"
    try:
        mounts = [m for m in os.listdir(root)]
    except OSError:
        return None
    host = (parts.hostname or "").lower()
    user = urllib.parse.unquote(parts.username or "")
    path = os.fsdecode(urllib.parse.unquote_to_bytes(parts.path or "/"))
    segs = [s for s in path.split("/") if s]
    best = None
    for mount in mounts:
        low = mount.lower()
        if scheme == "smb":
            if not segs:
                continue
            if not (low.startswith("smb-share:") and f"server={host}" in low and f"share={segs[0].lower()}" in low):
                continue
            if user and f"user={user.lower()}" not in low:
                continue
            best = root / mount / "/".join(segs[1:])
            break
        prefix = "sftp" if scheme == "ssh" else scheme
        if not low.startswith(prefix + ":"):
            continue
        if host and f"host={host}" not in low:
            continue
        if user and "user=" in low and f"user={user.lower()}" not in low:
            continue
        if parts.port and "port=" in low and f"port={parts.port}" not in low:
            continue
        best = root / mount / "/".join(segs)
        break
    if best is None:
        return None
    return str(best)


# ------------------------------------------------------------------------------- filesystems


def volatile_roots() -> list[str]:
    """Directories whose files may vanish soon (temp dirs, runtime dir, caches).

    ``$DROPWHEEL_VOLATILE_DIRS`` (``os.pathsep`` separated) replaces the built-in list.
    """
    env = os.environ.get("DROPWHEEL_VOLATILE_DIRS")
    if env is not None:
        return [os.path.realpath(os.path.expanduser(p)) for p in env.split(os.pathsep) if p.strip()]
    roots = ["/tmp", "/var/tmp", "/dev/shm", tempfile.gettempdir(), str(xdg_cache_home()),
             os.path.expanduser("~/.cache")]
    runtime = xdg_runtime_dir()
    if runtime is not None:
        roots.append(str(runtime))
    out: list[str] = []
    for r in roots:
        try:
            real = os.path.realpath(r)
        except (OSError, ValueError):
            continue
        if real and real not in out and real != os.path.realpath(os.path.expanduser("~")):
            out.append(real)
    return out


_FLATPAK_CACHE_RE = re.compile(r"/\.var/app/[^/]+/cache(/|$)")


def _under(path: str, root: str) -> bool:
    p = os.path.normcase(path)
    r = os.path.normcase(root.rstrip("/\\")) or os.sep
    return p == r or p.startswith(r + os.sep) or (os.altsep is not None and p.startswith(r + os.altsep))


def is_volatile(path: str | os.PathLike) -> bool:
    """``True`` when ``path`` lives in a temporary location (browser drags, mail attachments, Ark temp dirs).

    gvfs FUSE mounts under the runtime directory are network mounts, not volatile.
    """
    try:
        real = os.path.realpath(os.fspath(path))
    except (OSError, ValueError):
        return False
    runtime = xdg_runtime_dir()
    if runtime is not None and os.environ.get("DROPWHEEL_VOLATILE_DIRS") is None:
        gvfs = os.path.realpath(str(runtime / "gvfs"))
        if _under(real, gvfs):
            return False
    if os.environ.get("DROPWHEEL_VOLATILE_DIRS") is None and _FLATPAK_CACHE_RE.search(real.replace("\\", "/")):
        return True
    return any(_under(real, root) for root in volatile_roots())


_MOUNTS_CACHE: tuple[float, list[tuple[str, str]]] = (0.0, [])
_MOUNTS_LOCK = threading.Lock()


def _mounts() -> list[tuple[str, str]]:
    global _MOUNTS_CACHE
    now = time.monotonic()
    with _MOUNTS_LOCK:
        if now - _MOUNTS_CACHE[0] < 10.0 and _MOUNTS_CACHE[1]:
            return _MOUNTS_CACHE[1]
        entries: list[tuple[str, str]] = []
        try:
            with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as fh:
                for line in fh:
                    left, sep, right = line.partition(" - ")
                    if not sep:
                        continue
                    lf = left.split()
                    rf = right.split()
                    if len(lf) < 5 or not rf:
                        continue
                    mnt = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), lf[4])
                    entries.append((mnt, rf[0]))
        except OSError:
            entries = []
        entries.sort(key=lambda e: len(e[0]), reverse=True)
        _MOUNTS_CACHE = (now, entries)
        return entries


def fs_type(path: str | os.PathLike) -> str | None:
    """Filesystem type of the mount containing ``path`` (``"ext4"``, ``"vfat"``, …) or ``None``."""
    try:
        real = os.path.realpath(os.fspath(path))
    except (OSError, ValueError):
        return None
    for mnt, fstype in _mounts():
        if real == mnt or real.startswith(mnt.rstrip("/") + "/") or mnt == "/":
            return fstype
    return None


_CI_FS = {"vfat", "msdos", "exfat", "ntfs", "ntfs3", "fuseblk", "cifs", "smb3", "smbfs", "hfsplus"}
_PORTABLE_FS = {"vfat", "msdos", "exfat", "ntfs", "ntfs3", "fuseblk", "cifs", "smb3", "smbfs"}


def is_case_insensitive_fs(path: str | os.PathLike) -> bool:
    """``True`` on Windows/macOS or for FAT/exFAT/NTFS/SMB mounts."""
    if sys.platform in ("win32", "darwin", "cygwin"):
        return True
    return (fs_type(path) or "") in _CI_FS


def needs_portable_names(path: str | os.PathLike) -> bool:
    """``True`` when the target filesystem rejects ``<>:"\\|?*`` (FAT/exFAT/NTFS/SMB)."""
    if os.name == "nt":
        return True
    return (fs_type(path) or "") in _PORTABLE_FS


def free_space(path: str | os.PathLike) -> int | None:
    """Free bytes on the filesystem of ``path`` (nearest existing ancestor), or ``None``."""
    p = Path(os.path.abspath(os.fspath(path)))
    for candidate in (p, *p.parents):
        try:
            if candidate.exists():
                return shutil.disk_usage(candidate).free
        except OSError:
            continue
    return None


def dir_size(path: str | os.PathLike, limit: int = 50000) -> int:
    """Total size of the regular files below ``path`` (symlinks not followed, at most ``limit`` entries)."""
    total = 0
    count = 0
    stack = [os.fspath(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    count += 1
                    if count > limit:
                        return total
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


# ---------------------------------------------------------------------------------- processes


def _reap(proc: subprocess.Popen) -> None:
    try:
        proc.wait()
    except Exception:  # pragma: no cover
        pass


def detach(argv: Sequence[str], *, cwd: str | os.PathLike | None = None, env: dict | None = None) -> bool:
    """Start ``argv`` in its own session with stdio on ``/dev/null`` and return immediately.

    Returns ``False`` when the program cannot be started.  A daemon thread reaps the child.
    """
    if not argv:
        return False
    argv = [os.fspath(a) for a in argv]
    exe = tools.path(argv[0]) or (argv[0] if os.path.isabs(argv[0]) else None)
    if exe is None:
        log.warning("detach: %s not found", argv[0])
        return False
    argv[0] = exe
    full_env = None
    if env is not None:
        full_env = os.environ.copy()
        for k, v in env.items():
            if v is None:
                full_env.pop(k, None)
            else:
                full_env[k] = str(v)
    kwargs: dict = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, cwd=os.fspath(cwd) if cwd else None,
                                env=full_env, close_fds=True, **kwargs)
    except OSError as exc:
        log.warning("detach %s failed: %s", argv[0], exc)
        return False
    threading.Thread(target=_reap, args=(proc,), daemon=True, name="dropwheel-reap").start()
    return True


def run_quiet(argv: Sequence[str], *, timeout: float = 10.0, input: bytes | str | None = None,
              text: bool = True, env: dict | None = None) -> subprocess.CompletedProcess | None:
    """Run a short helper command, capturing output; ``None`` when it cannot run or times out."""
    if not argv:
        return None
    argv = [os.fspath(a) for a in argv]
    exe = tools.path(argv[0])
    if exe is None:
        return None
    argv[0] = exe
    full_env = None
    if env:
        full_env = os.environ.copy()
        full_env.update({k: str(v) for k, v in env.items()})
    data = input
    if text and isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    if not text and isinstance(data, str):
        data = data.encode("utf-8")
    try:
        return subprocess.run(argv, input=data, capture_output=True, timeout=timeout, text=text,
                              encoding="utf-8" if text else None, errors="replace" if text else None,
                              stdin=None if data is not None else subprocess.DEVNULL, env=full_env)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        log.debug("run_quiet %s failed: %s", argv[0], exc)
        return None


# ------------------------------------------------------------------------------ open / reveal


def open_path(path: str | os.PathLike, app: str = "") -> bool:
    """Open ``path`` with the default application (``xdg-open``, then ``gio open``) or ``app`` (argv string)."""
    target = os.fspath(path)
    if app:
        argv = shlex.split(app)
        if argv and detach([*argv, target]):
            return True
    if tools.have("xdg-open") and detach(["xdg-open", target]):
        return True
    if tools.have("gio") and detach(["gio", "open", target]):
        return True
    return False


def reveal_path(paths: str | os.PathLike | Iterable[str | os.PathLike], file_manager: str = "") -> bool:
    """Show ``paths`` selected in the file manager (``org.freedesktop.FileManager1.ShowItems``).

    Falls back to opening the parent folder (with ``file_manager`` when configured).
    """
    if isinstance(paths, (str, os.PathLike)):
        items = [os.fspath(paths)]
    else:
        items = [os.fspath(p) for p in paths]
    items = [os.path.abspath(p) for p in items if p]
    if not items:
        return False
    if not file_manager and tools.have("gdbus"):
        uris = ", ".join("'" + path_to_uri(p).replace("'", "%27") + "'" for p in items)
        res = run_quiet(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                         "--object-path", "/org/freedesktop/FileManager1",
                         "--method", "org.freedesktop.FileManager1.ShowItems", f"[{uris}]", ""], timeout=8)
        if res is not None and res.returncode == 0:
            return True
    parent = os.path.dirname(items[0]) or items[0]
    if file_manager:
        argv = shlex.split(file_manager)
        if argv:
            name = os.path.basename(argv[0]).lower()
            if name in ("nautilus", "dolphin") and len(items) == 1:
                if detach([*argv, "--select", items[0]]):
                    return True
            if detach([*argv, parent]):
                return True
    return open_path(parent)


# ---------------------------------------------------------------------------------- clipboard


def _wl_copy(data: bytes, mime: str) -> bool:
    exe = tools.path("wl-copy")
    if exe is None:
        return False
    try:
        # wl-copy forks a server that keeps serving the clipboard: never capture its stdio
        proc = subprocess.run([exe, "--type", mime], input=data, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def copy_text(text: str, mime: str = "text/plain;charset=utf-8") -> bool:
    """Put ``text`` on the Wayland clipboard."""
    return _wl_copy(str(text).encode("utf-8"), mime)


#: File managers that paste files only from ``text/uri-list`` (KIO / Qt).  The GTK ones (Thunar, the
#: Caelestia default, Nautilus, Nemo, Caja, PCManFM) and libfm-qt paste only from, or prefer,
#: ``x-special/gnome-copied-files``; wl-copy can offer a single type, so the paster decides.
_URI_LIST_FILE_MANAGERS = ("dolphin", "konqueror", "krusader")
_GTK_FILE_MANAGERS = ("thunar", "nautilus", "nemo", "caja", "pcmanfm")


def files_clipboard_type(file_manager: str = "") -> str:
    """Clipboard type for :func:`copy_files`: what the user's file manager pastes files from.

    ``file_manager`` is the configured program (``apps.file_manager``); otherwise the default
    handler of ``inode/directory`` in ``mimeapps.list`` decides.  When that is no known file manager
    (nothing set, or an editor that claimed folders), a lone KDE file manager means
    ``text/uri-list``, anything else ``x-special/gnome-copied-files``.
    """
    name = str(file_manager or "").strip().lower()
    if not name:
        defaults = _mimeapps()[0].get("inode/directory") or []
        name = defaults[0].lower() if defaults else ""
    if any(fm in name for fm in _URI_LIST_FILE_MANAGERS):
        return "text/uri-list"
    if any(fm in name for fm in _GTK_FILE_MANAGERS) or any(tools.have(fm) for fm in _GTK_FILE_MANAGERS):
        return "x-special/gnome-copied-files"
    return "text/uri-list" if any(tools.have(fm) for fm in _URI_LIST_FILE_MANAGERS) else "x-special/gnome-copied-files"


def copy_files(paths: Iterable[str | os.PathLike], file_manager: str = "") -> bool:
    """Put files on the clipboard so that Ctrl+V pastes them in the file manager.

    GTK file managers (Thunar, Nautilus, Nemo…) read ``x-special/gnome-copied-files``
    (``copy\\n<uri>\\n<uri>``), KDE's read ``text/uri-list``; see :func:`files_clipboard_type`.
    """
    uris = [path_to_uri(p) for p in paths]
    if not uris:
        return False
    mime = files_clipboard_type(file_manager)
    if mime == "text/uri-list":
        data = ("\r\n".join(uris) + "\r\n").encode("utf-8")
    else:
        data = ("copy\n" + "\n".join(uris)).encode("utf-8")
    return _wl_copy(data, mime)


def copy_image(path: str | os.PathLike, mime: str = "") -> bool:
    """Put an image file's bytes on the clipboard (``image/png`` by default for .png)."""
    p = Path(path)
    if not mime:
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
                "svg": "image/svg+xml", "avif": "image/avif", "bmp": "image/bmp"}.get(ext, "image/png")
    try:
        data = p.read_bytes()
    except OSError:
        return False
    return _wl_copy(data, mime)


def parse_uri_list(text: str, *, keep_urls: bool = True) -> list[str]:
    """Parse a ``text/uri-list`` payload into local paths (``file://``) and http(s) URLs."""
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("file:"):
            out.append(uri_to_path(line))
        elif low.startswith(("http://", "https://")):
            if keep_urls:
                out.append(line)
        elif os.path.isabs(line):
            out.append(line)
        else:
            mapped = gvfs_path(line)
            if mapped:
                out.append(mapped)
    return out


def read_clipboard_files() -> list[str]:
    """Return the files currently on the clipboard.

    Tries ``text/uri-list``, then ``x-special/gnome-copied-files``, then plain text containing
    absolute paths / ``file://`` URIs of existing files.  http(s) URLs from a uri-list are kept.
    """
    if not tools.have("wl-paste"):
        return []
    res = run_quiet(["wl-paste", "--list-types"], timeout=3)
    if res is None or res.returncode != 0:
        return []
    types = [t.strip() for t in res.stdout.splitlines() if t.strip()]

    def paste(mime: str) -> str:
        r = run_quiet(["wl-paste", "--no-newline", "--type", mime], timeout=5)
        return r.stdout if r is not None and r.returncode == 0 else ""

    if "text/uri-list" in types:
        files = parse_uri_list(paste("text/uri-list"))
        if files:
            return files
    if "x-special/gnome-copied-files" in types:
        lines = paste("x-special/gnome-copied-files").splitlines()
        if lines and lines[0].strip() in ("copy", "cut"):
            lines = lines[1:]
        files = parse_uri_list("\n".join(lines), keep_urls=False)
        if files:
            return files
    for mime in ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING"):
        if mime in types:
            text = paste(mime)
            cands = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                p = uri_to_path(line) if line.lower().startswith("file:") else os.path.expanduser(line)
                if os.path.isabs(p) and os.path.exists(p):
                    cands.append(p)
                else:
                    cands = []
                    break
            return cands
    return []


# -------------------------------------------------------------------------------------- trash


def _trash_topdir(path: str) -> str:
    """Mount point (top directory) containing ``path``."""
    current = os.path.abspath(path)
    dev = os.lstat(current).st_dev
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return current
        try:
            if os.lstat(parent).st_dev != dev:
                return current
        except OSError:
            return current
        current = parent


def _trash_dirs_for(path: str) -> tuple[Path, str]:
    """Return ``(trash_dir, path_for_info)`` following the freedesktop trash spec."""
    home_trash = xdg_data_home() / "Trash"
    st = os.lstat(path)
    try:
        ensure_dir(xdg_data_home())
        home_dev = os.stat(xdg_data_home()).st_dev
    except OSError:
        home_dev = None
    if home_dev is None or st.st_dev == home_dev or not hasattr(os, "getuid"):
        return home_trash, os.path.abspath(path)
    top = _trash_topdir(path)
    uid = os.getuid()
    shared = Path(top) / ".Trash"
    try:
        sst = os.lstat(shared)
        if stat.S_ISDIR(sst.st_mode) and not stat.S_ISLNK(sst.st_mode) and sst.st_mode & stat.S_ISVTX:
            candidate = shared / str(uid)
            candidate.mkdir(mode=0o700, exist_ok=True)
            return candidate, os.path.relpath(os.path.abspath(path), top)
    except OSError:
        pass
    own = Path(top) / f".Trash-{uid}"
    try:
        own.mkdir(mode=0o700, exist_ok=True)
        if os.access(own, os.W_OK):
            return own, os.path.relpath(os.path.abspath(path), top)
    except OSError:
        pass
    return home_trash, os.path.abspath(path)


def _trash_manual(path: str) -> None:
    trash_dir, info_path = _trash_dirs_for(path)
    files_dir = trash_dir / "files"
    info_dir = trash_dir / "info"
    files_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    info_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    base = os.path.basename(os.path.abspath(path).rstrip("/\\")) or "file"
    stem, ext = split_name(base, is_dir=os.path.isdir(path) and not os.path.islink(path))
    tail = f".{ext}" if ext else ""
    info_file = None
    name = base
    for n in range(1, 100000):
        name = base if n == 1 else f"{stem}.{n}{tail}"
        candidate = info_dir / f"{name}.trashinfo"
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        if os.path.lexists(files_dir / name):
            os.close(fd)
            os.unlink(candidate)
            continue
        info_file = candidate
        encoded = urllib.parse.quote(os.fsencode(info_path) if os.name == "posix" else info_path.encode("utf-8"),
                                     safe="/")
        content = (f"[Trash Info]\nPath={encoded}\n"
                   f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        break
    if info_file is None:
        raise OSError("no free trash name")
    try:
        os.rename(path, files_dir / name)
    except OSError:
        try:
            shutil.move(path, files_dir / name)
        except Exception:
            try:
                info_file.unlink()
            except OSError:
                pass
            raise


def trash(path: str | os.PathLike) -> bool:
    """Move ``path`` to the trash (``gio trash`` → ``trash-put`` → send2trash → freedesktop trash).

    Raises :class:`~dropwheel.errors.UserError` (``err.trash_failed``) when every method failed.
    Never deletes permanently.
    """
    target = os.path.abspath(os.fspath(path))
    if not os.path.lexists(target):
        return True
    errors: list[str] = []
    if tools.have("gio"):
        res = run_quiet(["gio", "trash", "--", target], timeout=60)
        if res is not None and res.returncode == 0 and not os.path.lexists(target):
            return True
        if res is not None:
            errors.append(f"gio: {res.stderr.strip()}")
    if tools.have("trash-put"):
        res = run_quiet(["trash-put", "--", target], timeout=60)
        if res is not None and res.returncode == 0 and not os.path.lexists(target):
            return True
        if res is not None:
            errors.append(f"trash-put: {res.stderr.strip()}")
    try:
        from send2trash import send2trash  # type: ignore

        send2trash(target)
        if not os.path.lexists(target):
            return True
    except ImportError:
        pass
    except Exception as exc:  # send2trash raises its own TrashPermissionError / OSError
        errors.append(f"send2trash: {exc}")
    try:
        _trash_manual(target)
        if not os.path.lexists(target):
            return True
    except Exception as exc:
        errors.append(f"trash: {exc}")
    raise UserError("err.trash_failed", details="\n".join(errors), name=os.path.basename(target))


# ---------------------------------------------------------------------------------- terminals

_TERMINALS = ("foot", "kitty", "alacritty", "wezterm", "ghostty", "konsole", "gnome-terminal", "xfce4-terminal", "xterm")


def _preferred_terminal(preferred: str | dict | None) -> str:
    if isinstance(preferred, dict):
        apps = preferred.get("apps") if isinstance(preferred.get("apps"), dict) else {}
        value = apps.get("terminal", "")
        return value if isinstance(value, str) else ""
    return preferred or ""


def find_terminal(preferred: str | dict | None = "") -> list[str] | None:
    """Return the argv prefix of the terminal to use (``preferred`` = ``apps.terminal`` string or the
    whole config dict, then ``$TERMINAL``, ``xdg-terminal-exec`` and the known terminals)."""
    for cand in (_preferred_terminal(preferred), os.environ.get("TERMINAL", "")):
        if cand:
            argv = shlex.split(cand)
            if argv and tools.have(argv[0]):
                return argv
    for name in ("xdg-terminal-exec", *_TERMINALS):
        if tools.have(name):
            return [name]
    return None


def _terminal_argv(term: list[str], cwd: str, command: list[str] | None) -> tuple[list[str], str | None]:
    """Build the argv for terminal ``term`` opening in ``cwd`` running ``command``."""
    name = os.path.basename(term[0]).lower()
    base = list(term)
    cmd = list(command or [])
    if name == "foot":
        return base + [f"--working-directory={cwd}"] + cmd, cwd
    if name == "kitty":
        return base + ["--directory", cwd] + cmd, cwd
    if name == "alacritty":
        return base + ["--working-directory", cwd] + (["-e", *cmd] if cmd else []), cwd
    if name == "wezterm":
        return base + ["start", "--cwd", cwd] + (["--", *cmd] if cmd else []), cwd
    if name == "ghostty":
        return base + [f"--working-directory={cwd}"] + (["-e", *cmd] if cmd else []), cwd
    if name == "konsole":
        return base + ["--workdir", cwd] + (["-e", *cmd] if cmd else []), cwd
    if name in ("gnome-terminal", "kgx", "ptyxis"):
        return base + [f"--working-directory={cwd}"] + (["--", *cmd] if cmd else []), cwd
    if name in ("xfce4-terminal", "mate-terminal", "tilix", "terminator"):
        return base + [f"--working-directory={cwd}"] + (["-x", *cmd] if cmd else []), cwd
    if name == "xdg-terminal-exec":
        return base + [f"--dir={cwd}"] + cmd, cwd
    # xterm, urxvt, st and unknown terminals: rely on the process cwd and -e
    return base + (["-e", *cmd] if cmd else []), cwd


def launch_terminal(cwd: str | os.PathLike, command: Sequence[str] | str | None = None,
                    preferred: str | dict | None = "", *, hold: bool = False, hold_message: str = "") -> list[str]:
    """Open a terminal in ``cwd`` (optionally running ``command``) and return the argv used.

    ``command`` may be an argv list or a shell string; ``preferred`` is ``apps.terminal`` or the
    config dict.  ``hold=True`` keeps the window open after the command ends ("press Enter to
    close").  Supports foot, kitty, alacritty, wezterm, ghostty, konsole, gnome-terminal,
    xfce4-terminal, xterm and xdg-terminal-exec.  Raises ``UserError("err.no_terminal")``.
    """
    directory = os.fspath(cwd)
    if os.path.isfile(directory):
        directory = os.path.dirname(directory)
    if not os.path.isdir(directory):
        directory = os.path.expanduser("~")
    term = find_terminal(preferred)
    if term is None:
        raise UserError("err.no_terminal", install=tools.install_hint(["foot"]))
    cmd: list[str] | None
    if command is None:
        cmd = None
    elif isinstance(command, str):
        cmd = ["sh", "-c", command]
    else:
        cmd = [os.fspath(c) for c in command]
    if cmd and hold:
        msg = hold_message or "Press Enter to close / Premi Invio per chiudere"
        script = '"$@"; rc=$?; printf "\\n%s " "$DROPWHEEL_HOLD_MSG"; read -r _; exit $rc'
        cmd = ["sh", "-c", script, "dropwheel-hold", *cmd]
        env = {"DROPWHEEL_HOLD_MSG": msg}
    else:
        env = None
    argv, run_cwd = _terminal_argv(term, directory, cmd)
    if not detach(argv, cwd=run_cwd, env=env):
        raise UserError("err.no_terminal", details=" ".join(argv))
    return argv


# ------------------------------------------------------------------------------- desktop apps


def _application_dirs() -> list[Path]:
    dirs = [xdg_data_home() / "applications"]
    dirs += [d / "applications" for d in xdg_data_dirs()]
    for extra in (xdg_data_home() / "flatpak/exports/share/applications",
                  Path("/var/lib/flatpak/exports/share/applications")):
        if extra not in dirs:
            dirs.append(extra)
    out = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def _read_desktop(path: Path) -> dict | None:
    parser = configparser.RawConfigParser(interpolation=None, strict=False, delimiters=("=",))
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    return dict(parser.items("Desktop Entry"))


def _localized(entry: dict, key: str, lang: str) -> str:
    for k in (f"{key}[{lang}]", f"{key}[{lang.split('_')[0]}]", key):
        if k in entry and entry[k]:
            return entry[k]
    return ""


def _scan_desktop_files() -> dict[str, Path]:
    """Desktop-file ID -> path; earlier (user) directories win."""
    found: dict[str, Path] = {}
    for base in _application_dirs():
        if not base.is_dir():
            continue
        for root, _dirs, files in os.walk(base):
            for fname in files:
                if not fname.endswith(".desktop"):
                    continue
                full = Path(root) / fname
                rel = os.path.relpath(full, base).replace(os.sep, "-")
                found.setdefault(rel, full)
    return found


def _mimeapps() -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """Return (defaults, added, removed) associations from every mimeapps.list."""
    defaults: dict[str, list[str]] = {}
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    desktops = [d.lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d]
    files: list[Path] = []
    for base in [xdg_config_home(), *xdg_config_dirs()]:
        files += [base / f"{d}-mimeapps.list" for d in desktops] + [base / "mimeapps.list"]
    for base in [xdg_data_home(), *xdg_data_dirs()]:
        files += [base / "applications" / f"{d}-mimeapps.list" for d in desktops]
        files += [base / "applications" / "mimeapps.list"]
    for f in files:
        parser = configparser.RawConfigParser(interpolation=None, strict=False, delimiters=("=",))
        parser.optionxform = str  # type: ignore[assignment]
        try:
            if not parser.read(f, encoding="utf-8"):
                continue
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
        for section, target in (("Default Applications", defaults), ("Added Associations", added),
                                ("Removed Associations", removed)):
            if parser.has_section(section):
                for mime, value in parser.items(section):
                    ids = [v for v in value.split(";") if v]
                    target.setdefault(mime, [])
                    for i in ids:
                        if i not in target[mime]:
                            target[mime].append(i)
    return defaults, added, removed


_TEXTISH = ("application/json", "application/xml", "application/x-yaml", "application/toml",
            "application/javascript", "application/x-shellscript", "application/sql")


def _mime_matches(mime: str, supported: set[str]) -> bool:
    if mime in supported:
        return True
    major = mime.split("/", 1)[0]
    if f"{major}/*" in supported or "*/*" in supported or "all/all" in supported:
        return True
    if (mime.startswith("text/") or mime in _TEXTISH or mime.endswith(("+xml", "+json"))) and "text/plain" in supported:
        return True
    if mime == "image/svg+xml" and ("image/svg+xml-compressed" in supported):
        return True
    return False


def desktop_apps_for(mimes: Iterable[str], lang: str = "") -> list[dict]:
    """Applications able to open **all** ``mimes``: ``[{id, name, icon, exec, path, terminal, default}]``.

    Scans ``$XDG_DATA_HOME/applications``, ``$XDG_DATA_DIRS/*/applications`` and flatpak exports,
    honours ``NoDisplay``, ``Hidden``, ``TryExec`` and the ``mimeapps.list`` associations;
    default applications come first, then alphabetical order.
    """
    wanted = sorted({m for m in mimes if m})
    if not wanted:
        return []
    lang = lang or (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "en").split(".")[0]
    defaults, added, removed = _mimeapps()
    files = _scan_desktop_files()
    apps: list[dict] = []
    for app_id, path in files.items():
        entry = _read_desktop(path)
        if not entry or entry.get("Type", "Application") != "Application":
            continue
        if entry.get("Hidden", "").lower() == "true" or entry.get("NoDisplay", "").lower() == "true":
            continue
        try_exec = entry.get("TryExec", "")
        if try_exec and not tools.have(try_exec):
            continue
        if not entry.get("Exec"):
            continue
        supported = {m.strip() for m in entry.get("MimeType", "").split(";") if m.strip()}
        ok = True
        for mime in wanted:
            if app_id in removed.get(mime, []):
                ok = False
                break
            if _mime_matches(mime, supported) or app_id in added.get(mime, []):
                continue
            ok = False
            break
        if not ok:
            continue
        is_default = all(app_id in defaults.get(m, [])[:1] for m in wanted)
        apps.append({
            "id": app_id,
            "name": _localized(entry, "Name", lang) or app_id[:-8],
            "icon": entry.get("Icon", ""),
            "exec": entry.get("Exec", ""),
            "path": str(path),
            "terminal": entry.get("Terminal", "").lower() == "true",
            "default": is_default,
        })
    apps.sort(key=lambda a: (not a["default"], a["name"].casefold()))
    return apps


def find_desktop_file(app_id: str) -> Path | None:
    """Path of desktop file ``app_id`` (``"org.gnome.Loupe.desktop"``; ``.desktop`` optional)."""
    if not app_id:
        return None
    if os.path.isabs(app_id) and os.path.isfile(app_id):
        return Path(app_id)
    key = app_id if app_id.endswith(".desktop") else app_id + ".desktop"
    return _scan_desktop_files().get(key)


_FIELD_RE = re.compile(r"%[fFuUick%dDnNvm]")


def _expand_exec(exec_line: str, paths: list[str], entry: dict, desktop_path: Path) -> list[list[str]]:
    """Expand a desktop ``Exec`` line (field codes) into one or more argv lists."""
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = exec_line.split()
    uris = [path_to_uri(p) if not re.match(r"^[a-z][a-z0-9+.-]*://", p) else p for p in paths]
    single = any(t in ("%f", "%u") for t in tokens)
    batches = [[p] for p in paths] if single and len(paths) > 1 else [paths]
    out = []
    for batch in batches:
        argv: list[str] = []
        for tok in tokens:
            if tok == "%f":
                argv += batch[:1]
            elif tok == "%F":
                argv += batch
            elif tok == "%u":
                argv += [path_to_uri(p) for p in batch[:1]]
            elif tok == "%U":
                argv += [path_to_uri(p) for p in batch] if batch is not paths else uris
            elif tok == "%i":
                if entry.get("Icon"):
                    argv += ["--icon", entry["Icon"]]
            elif tok == "%c":
                argv.append(entry.get("Name", ""))
            elif tok == "%k":
                argv.append(str(desktop_path))
            else:
                argv.append(_FIELD_RE.sub(lambda m: "%" if m.group(0) == "%%" else "", tok))
        argv = [a for a in argv if a != ""]
        if argv:
            out.append(argv)
    return out


def launch_desktop_app(app_id: str, paths: Iterable[str | os.PathLike]) -> bool:
    """Open ``paths`` with desktop application ``app_id`` (``gtk-launch`` → ``gio launch`` → Exec expansion)."""
    files = [os.path.abspath(os.fspath(p)) if not re.match(r"^[a-z][a-z0-9+.-]*://", os.fspath(p)) else os.fspath(p)
             for p in paths]
    desktop = find_desktop_file(app_id)
    entry = _read_desktop(desktop) if desktop else None
    if entry and entry.get("Terminal", "").lower() != "true":
        app_name = app_id[:-8] if app_id.endswith(".desktop") else app_id
        if tools.have("gtk-launch") and detach(["gtk-launch", app_name, *files]):
            return True
        if tools.have("gio") and desktop is not None and detach(["gio", "launch", str(desktop), *files]):
            return True
    if entry and desktop is not None and entry.get("Exec"):
        argvs = _expand_exec(entry["Exec"], files, entry, desktop)
        ok = False
        for argv in argvs:
            if entry.get("Terminal", "").lower() == "true":
                try:
                    launch_terminal(entry.get("Path") or (os.path.dirname(files[0]) if files else "~"), argv)
                    ok = True
                except UserError:
                    ok = False
            else:
                ok = detach(argv, cwd=entry.get("Path") or None) or ok
        if ok:
            return True
    if desktop is not None and not files and tools.have("dex"):
        # dex cannot pass file arguments, so it is only a last resort for launching the app itself
        return detach(["dex", str(desktop)])
    return False
