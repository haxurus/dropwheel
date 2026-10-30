"""Generic file actions: the **File page** shown for every category (layout key ``"file"``).

Open, Open with…, Show in folder, Copy file, Copy path, Move to… / Copy to…, Rename… (single name or
multi-file pattern), Duplicate, Compress to ZIP, Share → (phone QR / KDE Connect / LocalSend / email),
Properties, Checksum → (SHA-256★, SHA-1, MD5, BLAKE2, XXH128), Verify checksum, Encrypt / Decrypt (gpg),
Open terminal here, Make executable, Copy as data URI and Move to trash.

The module also hosts helpers shared by the other ``act-misc`` modules (:class:`Progress`, hashing,
copying with progress, rename planning, tree rendering, info rows).

Rules: inputs are never modified, except by the explicit in-place actions of this page (Move, Rename,
Make executable, Trash), which act on the user's original path (never on a staged copy) and are not
undoable through the job history (the runner cannot restore moved originals).
"""

from __future__ import annotations

import base64
import errno
import hashlib
import inspect
import os
import re
import shutil
import stat
import tarfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .. import config as cfgmod
from .. import i18n, tools, util
from ..detect import EXT_TABLE, FileInfo
from ..errors import ToolError, UserError
from ..jobs import Context, atomic_target
from ..registry import P, action, group, layout

__all__ = [
    "Progress", "HASH_ALGOS", "hash_file", "new_hash", "parse_expected_checksum", "find_checksum_for",
    "copy_entry", "tree_stats", "plan_renames", "apply_renames", "render_pattern", "TreeNode", "render_tree",
    "info_row", "fmt_time", "original_paths", "kdeconnect_devices", "TERMINALS",
]

#: Terminal emulators accepted by :func:`dropwheel.util.launch_terminal` (any one is enough).
TERMINALS = ("foot", "kitty", "alacritty", "wezterm", "ghostty", "konsole", "gnome-terminal", "xfce4-terminal",
             "xdg-terminal-exec", "xterm")

_CHUNK = 4 * 1024 * 1024


# ============================================================================== shared helpers

class Progress:
    """Map a sub-task's progress into ``[lo, hi]`` of the job progress (throttled to 10 Hz).

    ``total`` enables :meth:`add` (byte or item counting); ``frac(None)`` means indeterminate.
    """

    def __init__(self, ctx: Context, lo: float = 0.0, hi: float = 1.0, total: float | None = None,
                 label: str | None = None) -> None:
        self.ctx = ctx
        self.lo = lo
        self.hi = hi
        self.total = total
        self.done = 0.0
        self.label = label
        self._last = 0.0

    def frac(self, value: float | None, message: str | None = None, force: bool = False) -> None:
        """Report ``value`` (0..1 of this sub-task, ``None`` = indeterminate)."""
        now = time.monotonic()
        if not force and now - self._last < 0.1:
            return
        self._last = now
        msg = message if message is not None else self.label
        if value is None:
            self.ctx.progress(None, msg)
            return
        value = min(1.0, max(0.0, float(value)))
        self.ctx.progress(self.lo + (self.hi - self.lo) * value, msg)

    def add(self, amount: float, message: str | None = None) -> None:
        """Count ``amount`` units of :attr:`total` and report."""
        self.done += amount
        if self.total:
            self.frac(self.done / self.total, message)
        else:
            self.frac(None, message)

    def sub(self, a: float, b: float, total: float | None = None, label: str | None = None) -> "Progress":
        """A child reporter covering ``[a, b]`` of this one."""
        span = self.hi - self.lo
        return Progress(self.ctx, self.lo + span * a, self.lo + span * b, total, label or self.label)


def original_paths(ctx: Context) -> list[Path]:
    """The user's original paths of the job files (not the staged copies of volatile inputs)."""
    return [Path(ctx.original(f).path) for f in ctx.files]


def fmt_time(ts: float | None, lang: str) -> str:
    """Local date/time of a timestamp (``31/10/2026 14:03:05`` in Italian, ISO-like in English)."""
    if ts is None:
        return ""
    try:
        dt = datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return ""
    return dt.strftime("%d/%m/%Y %H:%M:%S" if lang == "it" else "%Y-%m-%d %H:%M:%S")


def info_row(ctx: Context, group_key: str, key: str, value: Any) -> dict:
    """One row of a ``kind="info"`` result (group and key are translated)."""
    return {"group": ctx.t(group_key), "key": ctx.t(key), "value": "" if value is None else str(value)}


def size_text(n: int, lang: str) -> str:
    """``"2,4 MB (2.400.000 byte)"`` / ``"2.4 MB (2,400,000 bytes)"``."""
    grouped = f"{int(n):,}"
    if lang == "it":
        grouped = grouped.replace(",", ".")
    return f"{util.human_size(n, lang)} ({grouped} {'byte' if lang == 'it' else 'bytes'})"


def _display_dir(path: str | os.PathLike) -> str:
    home = os.path.expanduser("~")
    text = os.fspath(path)
    if text == home or text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


# ------------------------------------------------------------------------------------ hashing

#: algorithm id -> display name
HASH_ALGOS: dict[str, str] = {
    "sha256": "SHA-256", "sha1": "SHA-1", "md5": "MD5", "blake2": "BLAKE2b", "xxh128": "XXH128",
    "sha512": "SHA-512", "sha384": "SHA-384", "sha224": "SHA-224",
}


def new_hash(algo: str) -> Any | None:
    """A fresh hash object for ``algo`` (``None`` for xxh128 without the ``xxhash`` module)."""
    if algo == "sha1":
        return hashlib.sha1(usedforsecurity=False)
    if algo == "md5":
        return hashlib.md5(usedforsecurity=False)
    if algo == "blake2":
        return hashlib.blake2b()
    if algo in ("sha256", "sha512", "sha384", "sha224"):
        return hashlib.new(algo)
    if algo == "xxh128":
        try:
            import xxhash  # type: ignore

            return xxhash.xxh3_128()
        except ImportError:
            return None
    raise ValueError(f"unknown hash {algo}")


def hash_file(ctx: Context, path: str | os.PathLike, algo: str, prog: Progress | None = None) -> str:
    """Hex digest of ``path`` with progress (bytes) and cancellation."""
    h = new_hash(algo)
    if h is None:
        res = ctx.run(["xxh128sum", os.path.abspath(os.fspath(path))])
        first = (res.stdout or "").strip().split()
        if not first:
            raise ToolError("err.tool_failed", tool="xxh128sum", code=0)
        return first[0].lstrip("\\").lower()
    with open(path, "rb") as fh:
        while True:
            ctx.check_cancel()
            data = fh.read(_CHUNK)
            if not data:
                break
            h.update(data)
            if prog is not None:
                prog.add(len(data))
    return h.hexdigest()


_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_ALGOS_BY_LEN: dict[int, list[str]] = {
    32: ["md5", "xxh128"], 40: ["sha1"], 56: ["sha224"], 64: ["sha256"], 96: ["sha384"], 128: ["sha512", "blake2"],
}
_BSD_RE = re.compile(r"^\s*(SHA256|SHA512|SHA384|SHA224|SHA1|MD5|BLAKE2b?|B2)\s*\((.+)\)\s*=\s*([0-9a-fA-F]{32,128})\s*$",
                     re.I)
_GNU_RE = re.compile(r"^\s*\\?([0-9a-fA-F]{32,128})\s+[ *]?(.+?)\s*$")
_BARE_RE = re.compile(r"^\s*([0-9a-fA-F]{32,128})\s*$")
_PREFIX_RE = re.compile(r"^\s*(sha256|sha512|sha384|sha224|sha1|md5|blake2b?|b2|xxh128)\s*[:=]\s*([0-9a-fA-F]{32,128})\s*$",
                        re.I)
_SUMFILE_RE = re.compile(r"(?i)(sums?|checksums?|digests?)(\.txt|\.asc)?$")
_SUM_EXTS = {"sha256": "sha256", "sha256sum": "sha256", "sha512": "sha512", "sha512sum": "sha512", "sha1": "sha1",
             "sha1sum": "sha1", "md5": "md5", "md5sum": "md5", "b2": "blake2", "b2sum": "blake2", "sha384": "sha384",
             "sha224": "sha224", "xxh128": "xxh128"}


def _algo_name(text: str) -> str | None:
    low = text.lower().replace("-", "")
    if low in ("blake2b", "blake2", "b2"):
        return "blake2"
    return low if low in HASH_ALGOS else None


def _algos_for(hexdigest: str, hint: str | None = None) -> list[str]:
    if hint:
        return [hint]
    return list(_ALGOS_BY_LEN.get(len(hexdigest), []))


def parse_expected_checksum(text: str, name: str = "") -> tuple[list[str], str] | None:
    """Parse a pasted checksum: ``HEX``, ``sha256:HEX``, ``HEX  file`` or ``SHA256 (file) = HEX``.

    Returns ``(candidate algorithms, lower-case hex)`` or ``None``.  When ``name`` is given and the
    text holds several lines, the line for that file name is used.
    """
    lines = [ln for ln in (text or "").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return None
    if len(lines) == 1:
        line = lines[0].strip()
        m = _PREFIX_RE.match(line)
        if m:
            algo = _algo_name(m.group(1))
            return _algos_for(m.group(2), algo), m.group(2).lower()
        m = _BARE_RE.match(line)
        if m:
            return _algos_for(m.group(1)), m.group(1).lower()
    for line in lines:
        m = _BSD_RE.match(line)
        if m and (not name or os.path.basename(m.group(2).strip()) == name or len(lines) == 1):
            return _algos_for(m.group(3), _algo_name(m.group(1))), m.group(3).lower()
        m = _GNU_RE.match(line)
        if m:
            entry = m.group(2).strip()
            if entry.startswith("./"):
                entry = entry[2:]
            if not name or os.path.basename(entry) == name or len(lines) == 1:
                return _algos_for(m.group(1)), m.group(1).lower()
    return None


def find_checksum_for(path: str | os.PathLike) -> tuple[list[str], str, Path] | None:
    """Look next to ``path`` for a checksum file listing it (``<name>.sha256``, ``SHA256SUMS``,
    ``CHECKSUM``, ``*.DIGESTS``…).  Returns ``(algorithms, hex, checksum file)`` or ``None``."""
    p = Path(path)
    name = p.name
    try:
        siblings = sorted(p.parent.iterdir(), key=lambda e: e.name)
    except OSError:
        return None
    own: list[Path] = []
    shared: list[Path] = []
    for entry in siblings:
        if entry == p or not entry.is_file():
            continue
        low = entry.name.lower()
        ext = low.rsplit(".", 1)[-1] if "." in low else ""
        if low.startswith(name.lower() + ".") and ext in _SUM_EXTS:
            own.append(entry)
        elif ext in _SUM_EXTS or _SUMFILE_RE.search(low):
            shared.append(entry)
    for cand in own + shared:
        try:
            if cand.stat().st_size > 4 * 1024 * 1024:
                continue
            text = cand.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = cand.name.lower()
        ext = low.rsplit(".", 1)[-1] if "." in low else ""
        hint = _SUM_EXTS.get(ext)
        if hint is None:
            for key in ("sha256", "sha512", "sha384", "sha224", "sha1", "md5", "b2"):
                if low.startswith(key):
                    hint = _algo_name(key)
                    break
        parsed = None
        if cand in own:
            parsed = parse_expected_checksum(text, name)
        else:
            for line in text.splitlines():
                m = _BSD_RE.match(line)
                if m and os.path.basename(m.group(2).strip()) == name:
                    parsed = (_algos_for(m.group(3), _algo_name(m.group(1))), m.group(3).lower())
                    break
                m = _GNU_RE.match(line)
                if m:
                    entry = m.group(2).strip()
                    if entry.startswith("./"):
                        entry = entry[2:]
                    if os.path.basename(entry) == name:
                        parsed = (_algos_for(m.group(1)), m.group(1).lower())
                        break
        if parsed is None:
            continue
        algos, digest = parsed
        if hint and len(digest) in [k for k, v in _ALGOS_BY_LEN.items() if hint in v]:
            algos = [hint]
        if algos:
            return algos, digest, cand
    return None


def _clipboard_digest() -> str:
    if not tools.have("wl-paste"):
        return ""
    res = util.run_quiet(["wl-paste", "--no-newline", "--type", "text/plain"], timeout=3)
    if res is None or res.returncode != 0:
        return ""
    text = (res.stdout or "").strip()
    return text if 32 <= len(text) <= 200 else ""


# ------------------------------------------------------------------------------------ copying

def tree_stats(path: str | os.PathLike, ctx: Context | None = None, limit: int = 2_000_000) -> tuple[int, int, int]:
    """``(bytes, files, folders)`` below ``path`` (symlinks not followed; a file counts as itself)."""
    p = os.fspath(path)
    try:
        st = os.lstat(p)
    except OSError:
        return 0, 0, 0
    if not stat.S_ISDIR(st.st_mode):
        return (st.st_size if stat.S_ISREG(st.st_mode) else 0), 1, 0
    total = files = dirs = 0
    stack = [p]
    seen = 0
    while stack:
        if ctx is not None:
            ctx.check_cancel()
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    seen += 1
                    if seen > limit:
                        return total, files, dirs
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs += 1
                            stack.append(entry.path)
                        else:
                            files += 1
                            if entry.is_file(follow_symlinks=False):
                                total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files, dirs


def _copy_file(ctx: Context, src: str, dst: str, prog: Progress | None) -> None:
    with open(src, "rb") as fi, open(dst, "xb") as fo:
        while True:
            ctx.check_cancel()
            buf = fi.read(_CHUNK)
            if not buf:
                break
            fo.write(buf)
            if prog is not None:
                prog.add(len(buf))
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass


def copy_entry(ctx: Context, src: str | os.PathLike, dst: str | os.PathLike, prog: Progress | None = None,
               skipped: list[str] | None = None) -> None:
    """Copy a file, a symlink (as a link) or a whole folder from ``src`` to the new path ``dst``.

    Special files (FIFOs, sockets, devices) are skipped and listed in ``skipped``.  Unreadable
    entries raise :class:`UserError` (``err.file.copy_failed``).
    """
    s = os.fspath(src)
    d = os.fspath(dst)
    try:
        st = os.lstat(s)
        if stat.S_ISLNK(st.st_mode):
            os.symlink(os.readlink(s), d)
            return
        if stat.S_ISDIR(st.st_mode):
            os.mkdir(d)
            with os.scandir(s) as it:
                names = sorted(e.name for e in it)
            for name in names:
                copy_entry(ctx, os.path.join(s, name), os.path.join(d, name), prog, skipped)
            try:
                shutil.copystat(s, d, follow_symlinks=False)
            except OSError:
                pass
            return
        if stat.S_ISREG(st.st_mode):
            _copy_file(ctx, s, d, prog)
            return
    except UserError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise UserError("err.disk_full", details=str(exc)) from None
        raise UserError("err.file.copy_failed", details=str(exc), name=os.path.basename(s)) from None
    if skipped is not None:
        skipped.append(s)


def _check_space(target_dir: str | os.PathLike, need: int) -> None:
    free = util.free_space(target_dir)
    if free is not None and need > 0 and free < need + 16 * 1024 * 1024:
        raise UserError("err.file.no_space", need=util.human_size(need, i18n.get_lang()),
                        free=util.human_size(free, i18n.get_lang()))


# --------------------------------------------------------------------------------- renaming

_PLACEHOLDER_RE = re.compile(r"\{(name|n|ext|date|parent)(?::([^{}]*))?\}")


def render_pattern(pattern: str, *, name: str, ext: str, n: int, date: datetime | None = None,
                   parent: str = "") -> str:
    """Fill ``{name}`` ``{n}`` ``{n:03}`` ``{ext}`` ``{date}`` ``{date:%Y%m%d}`` ``{parent}`` in ``pattern``."""
    when = date or datetime.now()

    def sub(m: re.Match) -> str:
        key, spec = m.group(1), m.group(2)
        if key == "n":
            try:
                return format(n, spec) if spec else str(n)
            except ValueError:
                return str(n)
        if key == "date":
            try:
                return when.strftime(spec) if spec else when.strftime("%Y-%m-%d")
            except ValueError:
                return when.strftime("%Y-%m-%d")
        value = {"name": name, "ext": ext, "parent": parent}[key]
        if spec:
            try:
                return format(value, spec)
            except ValueError:
                return value
        return value

    return _PLACEHOLDER_RE.sub(sub, pattern)


def _known_ext(ext: str) -> bool:
    return bool(ext) and (ext in EXT_TABLE or ext in util.MULTI_EXTS)


def _final_name(rendered: str, orig_ext: str, is_dir: bool, pattern_has_ext: bool) -> str:
    """Append the original extension unless the user typed one."""
    text = rendered.strip()
    if is_dir or not orig_ext or pattern_has_ext:
        return text
    _stem, typed = util.split_name(text)
    if typed and (typed == orig_ext or _known_ext(typed)):
        return text
    return f"{text}.{orig_ext}"


@dataclass
class RenamePlan:
    """Result of :func:`plan_renames`: ``pairs`` to apply, ``unchanged`` names, ``conflicts`` (messages)."""

    pairs: list[tuple[Path, Path]] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def plan_renames(paths: Sequence[Path], pattern: str, *, start: int = 1, find: str = "", replace: str = "",
                 regex: bool = False, case: str = "keep") -> RenamePlan:
    """Compute new names for ``paths`` (in order) from ``pattern`` with optional find/replace and case.

    ``{n}`` counts from ``start``.  Names with ``/`` or control characters are sanitised; conflicts
    (two files with the same new name, or a name already taken by a file that is not renamed) are
    reported in :attr:`RenamePlan.conflicts`.
    """
    plan = RenamePlan()
    compiled = None
    if find and regex:
        try:
            compiled = re.compile(find)
        except re.error as exc:
            raise UserError("err.file.bad_regex", details=str(exc)) from None
    pattern_has_ext = "{ext}" in pattern or bool(re.search(r"\{ext:", pattern))
    ci_cache: dict[str, bool] = {}

    def key_of(p: Path) -> str:
        parent = str(p.parent)
        if parent not in ci_cache:
            ci_cache[parent] = util.is_case_insensitive_fs(p.parent)
        k = os.path.normcase(os.path.abspath(p))
        return k.lower() if ci_cache[parent] else k

    sources = {key_of(p) for p in paths}
    seen: dict[str, Path] = {}
    for index, path in enumerate(paths):
        is_dir = path.is_dir() and not path.is_symlink()
        stem, ext = util.split_name(path.name, is_dir=is_dir)
        try:
            mtime = datetime.fromtimestamp(os.lstat(path).st_mtime)
        except OSError:
            mtime = datetime.now()
        rendered = render_pattern(pattern or "{name}", name=stem, ext=ext, n=start + index, date=mtime,
                                  parent=path.parent.name)
        if find:
            if compiled is not None:
                rendered = compiled.sub(replace, rendered)
            else:
                rendered = rendered.replace(find, replace)
        if case == "lower":
            rendered = rendered.lower()
        elif case == "upper":
            rendered = rendered.upper()
        elif case == "title":
            rendered = rendered.title()
        new_name = util.safe_filename(_final_name(rendered, ext, is_dir, pattern_has_ext))
        target = path.with_name(new_name)
        if new_name == path.name:
            plan.unchanged.append(path)
            continue
        key = key_of(target)
        if key in seen:
            plan.conflicts.append(f"{seen[key].name} / {path.name} → {new_name}")
            continue
        seen[key] = path
        same_file_case_change = key == key_of(path)
        if os.path.lexists(target) and not same_file_case_change and key not in sources:
            plan.conflicts.append(f"{path.name} → {new_name}")
            continue
        plan.pairs.append((path, target))
    # a target may not take the name of a selected file that keeps its name
    while True:
        renamed = {key_of(src) for src, _dst in plan.pairs}
        blocked = [(src, dst) for src, dst in plan.pairs
                   if key_of(dst) in sources and key_of(dst) not in renamed and key_of(dst) != key_of(src)]
        if not blocked:
            break
        for src, dst in blocked:
            plan.pairs.remove((src, dst))
            plan.conflicts.append(f"{src.name} → {dst.name}")
    return plan


def apply_renames(pairs: Sequence[tuple[Path, Path]]) -> list[Path]:
    """Rename atomically as a group (two phases through temporary names, so swaps and case-only
    changes work); everything is rolled back when one rename fails.  Returns the new paths."""
    import secrets as _secrets

    token = _secrets.token_hex(3)
    staged: list[tuple[Path, Path, Path]] = []
    done: list[tuple[Path, Path]] = []
    try:
        for i, (src, dst) in enumerate(pairs):
            tmp = src.with_name(f".dw-rename-{token}-{i}")
            os.rename(src, tmp)
            staged.append((src, tmp, dst))
        for src, tmp, dst in staged:
            if os.path.lexists(dst):
                raise UserError("err.output_exists", name=dst.name)
            os.rename(tmp, dst)
            done.append((src, dst))
    except BaseException as exc:
        for src, dst in reversed(done):
            try:
                os.rename(dst, src)
            except OSError:
                pass
        moved_back = {s for s, _d in done}
        for src, tmp, _dst in staged:
            if src in moved_back:
                continue
            try:
                if os.path.lexists(tmp):
                    os.rename(tmp, src)
            except OSError:
                pass
        if isinstance(exc, OSError):
            raise UserError("err.file.rename_failed", details=str(exc)) from None
        raise
    return [dst for _src, dst in done]


# ----------------------------------------------------------------------------- tree rendering

class TreeNode:
    """A node of a text tree (``name``, ``size`` for files, ``children`` for folders)."""

    __slots__ = ("name", "size", "is_dir", "children", "extra")

    def __init__(self, name: str, is_dir: bool = True, size: int | None = None, extra: str = "") -> None:
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.extra = extra
        self.children: dict[str, TreeNode] = {}

    def add_path(self, parts: Sequence[str], size: int | None, is_dir: bool, extra: str = "") -> None:
        """Insert ``parts`` (path components) below this node."""
        node = self
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            child = node.children.get(part)
            if child is None:
                child = TreeNode(part, is_dir=(is_dir if last else True), size=(size if last else None),
                                 extra=(extra if last else ""))
                node.children[part] = child
            elif last:
                child.is_dir = child.is_dir or is_dir
                if size is not None:
                    child.size = size
                if extra:
                    child.extra = extra
            node = child

    def total(self) -> int:
        """Total size of the files below (or own size)."""
        if not self.is_dir:
            return int(self.size or 0)
        return sum(c.total() for c in self.children.values())

    def count(self) -> tuple[int, int]:
        """``(files, folders)`` below this node."""
        files = folders = 0
        for c in self.children.values():
            if c.is_dir:
                folders += 1
                f, d = c.count()
                files += f
                folders += d
            else:
                files += 1
        return files, folders


def render_tree(root: TreeNode, lang: str, *, sizes: bool = True, max_lines: int = 5000,
                more_text: str = "… +{n}") -> list[str]:
    """Render ``root``'s children as ``├──``/``└──`` lines (folders first, natural order)."""
    lines: list[str] = []
    hidden = 0

    def label(node: TreeNode) -> str:
        text = node.name + ("/" if node.is_dir else "")
        if node.extra:
            text += f" {node.extra}"
        if sizes:
            text += f"  ({util.human_size(node.total(), lang)})"
        return text

    def walk(node: TreeNode, prefix: str) -> None:
        nonlocal hidden
        kids = sorted(node.children.values(), key=lambda c: (not c.is_dir, util.natural_key(c.name)))
        for i, child in enumerate(kids):
            last = i == len(kids) - 1
            if len(lines) >= max_lines:
                hidden += 1 + (sum(child.count()) if child.is_dir else 0)
                continue
            lines.append(prefix + ("└── " if last else "├── ") + label(child))
            if child.is_dir and child.children:
                walk(child, prefix + ("    " if last else "│   "))

    walk(root, "")
    if hidden:
        lines.append(more_text.replace("{n}", str(hidden)))
    return lines


# ===================================================================================== caches

_APPS_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_KDE_CACHE: dict[str, Any] = {"t": 0.0, "devices": []}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 30.0


def _apps_for(files: Sequence[FileInfo]) -> list[dict]:
    mimes = tuple(sorted({f.mime for f in files if f.mime}))
    if not mimes:
        return []
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _APPS_CACHE.get(mimes)
        if hit is not None and now - hit[0] < _CACHE_TTL:
            return hit[1]
    try:
        apps = util.desktop_apps_for(mimes)
    except Exception:  # a broken desktop file must never break the menu
        apps = []
    with _CACHE_LOCK:
        if len(_APPS_CACHE) > 64:
            _APPS_CACHE.clear()
        _APPS_CACHE[mimes] = (now, apps)
    return apps


def _app_options(files: list[FileInfo]) -> list[tuple[str, str]]:
    return [(a["id"], a["name"]) for a in _apps_for(files)]


def _app_default(files: list[FileInfo]) -> str:
    apps = _apps_for(files)
    return apps[0]["id"] if apps else ""


def kdeconnect_devices(force: bool = False, timeout: float = 3.0) -> list[tuple[str, str]]:
    """Reachable, paired KDE Connect devices ``[(id, name)]`` (cached for 30 s)."""
    if not tools.have("kdeconnect-cli"):
        return []
    now = time.monotonic()
    with _CACHE_LOCK:
        if not force and now - _KDE_CACHE["t"] < _CACHE_TTL:
            return list(_KDE_CACHE["devices"])
    res = util.run_quiet(["kdeconnect-cli", "-a", "--id-name-only"], timeout=timeout)
    devices: list[tuple[str, str]] = []
    if res is not None and res.returncode == 0:
        for line in (res.stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("-"):
                continue
            ident, _sep, name = line.partition(" ")
            if ident:
                devices.append((ident, name.strip() or ident))
    with _CACHE_LOCK:
        _KDE_CACHE["t"] = now
        _KDE_CACHE["devices"] = devices
    return list(devices)


def _kde_options(files: list[FileInfo]) -> list[tuple[str, str]]:
    return kdeconnect_devices()


def _kde_default(files: list[FileInfo]) -> str:
    devices = kdeconnect_devices()
    return devices[0][0] if devices else ""


# ================================================================================ predicates

def _no_dirs(files: list[FileInfo]) -> bool:
    return all(not f.is_dir for f in files)


def _files_only(files: list[FileInfo]) -> bool:
    return all(f.exists and not f.is_dir for f in files)


_EXEC_EXTS = {"sh", "bash", "zsh", "fish", "ksh", "py", "pl", "rb", "lua", "php", "run", "bin", "appimage", "out",
              "elf"}


def _looks_executable(f: FileInfo) -> bool:
    if f.category == "appimage" or f.format in ("elf", "script", "appimage"):
        return True
    if f.mime in ("application/x-executable", "application/x-shellscript", "application/x-makeself",
                  "application/x-sharedlib"):
        return True
    if f.ext in _EXEC_EXTS:
        return True
    if f.category in ("code", "unknown", "text") and f.size:
        try:
            with open(f.path, "rb") as fh:
                return fh.read(2) == b"#!"
        except OSError:
            return False
    return False


def _can_chmodx(files: list[FileInfo]) -> bool:
    for f in files:
        if f.is_dir or not f.exists or not f.path:
            return False
        try:
            st = os.stat(f.path)
        except OSError:
            return False
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o111:
            return False
        if not _looks_executable(f):
            return False
    return True


DATA_URI_MAX = 1024 * 1024


def _small_single(files: list[FileInfo]) -> bool:
    return len(files) == 1 and files[0].exists and not files[0].is_dir and 0 < files[0].size <= DATA_URI_MAX


_PGP_HEAD = b"-----BEGIN PGP MESSAGE-----"


def _is_pgp(f: FileInfo) -> bool:
    if f.is_dir or not f.exists:
        return False
    if f.ext in ("gpg", "pgp"):
        return True
    if f.ext == "asc":
        try:
            with open(f.path, "rb") as fh:
                return fh.read(64).lstrip().startswith(_PGP_HEAD)
        except OSError:
            return False
    return False


# ==================================================================================== actions
# ---------------------------------------------------------------------------- open / reveal

@action(id="file.open", label="act.file.open", icon="open_in_new", categories={"*"}, multi="any",
        requires=[("xdg-open", "gio")], undoable=False, description="desc.file.open", order=10)
def file_open(ctx: Context):
    """Open the files with their default applications."""
    paths = original_paths(ctx)
    if len(paths) > 20:
        ctx.warn(ctx.t("warn.file.open_limit", n=20))
        paths = paths[:20]
    failed = [p.name for p in paths if not util.open_path(p)]
    if failed:
        raise UserError("err.open_failed", name=", ".join(failed))
    return ctx.result(kind="none", message=ctx.t("msg.file.opened"))


@action(id="file.open_with", label="act.file.open_with", icon="apps", categories={"*"}, multi="any",
        mode="dialog", dialog="form", undoable=False, description="desc.file.open_with", order=20,
        params=[P.select("app", "prm.file.app", options=_app_options, default=_app_default, required=True)])
def file_open_with(ctx: Context):
    """Open the files with the chosen desktop application."""
    app = ctx.pstr("app")
    if not app:
        raise UserError("err.file.no_app")
    paths = original_paths(ctx)
    if not util.launch_desktop_app(app, paths):
        raise UserError("err.open_failed", name=app)
    return ctx.result(kind="none", message=ctx.t("msg.file.opened"))


@action(id="file.reveal", label="act.file.reveal", icon="folder_open", categories={"*"}, multi="any",
        undoable=False, description="desc.file.reveal", order=10)
def file_reveal(ctx: Context):
    """Show the files selected in the file manager."""
    fm = str(cfgmod.get(ctx.config, "apps.file_manager", "") or "")
    if not util.reveal_path(original_paths(ctx), file_manager=fm):
        raise UserError("err.open_failed", name=ctx.file.name)
    return ctx.result(kind="none", message=ctx.t("msg.file.revealed"))


@action(id="file.terminal", label="act.file.terminal", icon="terminal", categories={"*"}, multi="any",
        requires=[TERMINALS], undoable=False, description="desc.file.terminal", order=30)
def file_terminal(ctx: Context):
    """Open a terminal in the folder (or in the folder containing the file)."""
    first = Path(ctx.original(ctx.file).path)
    directory = first if first.is_dir() else first.parent
    util.launch_terminal(directory, preferred=ctx.config)
    return ctx.result(kind="none", message=ctx.t("msg.file.terminal", dir=_display_dir(directory)))


# ------------------------------------------------------------------------------- clipboard

@action(id="file.copy", label="act.file.copy", icon="content_copy", categories={"*"}, multi="any",
        requires=["wl-copy"], undoable=False, description="desc.file.copy", order=1)
def file_copy(ctx: Context):
    """Put the files on the clipboard in the format the file manager pastes (see util.files_clipboard_type)."""
    paths = original_paths(ctx)
    if not util.copy_files(paths, file_manager=str(cfgmod.get(ctx.config, "apps.file_manager", "") or "")):
        raise UserError("err.file.clipboard", install=tools.install_hint(["wl-copy"]))
    return ctx.result(kind="none", message=i18n.tn("msg.file.copied", len(paths), ctx.lang))


@action(id="file.copy_path", label="act.file.copy_path", icon="link", categories={"*"}, multi="any",
        undoable=False, description="desc.file.copy_path", order=2)
def file_copy_path(ctx: Context):
    """Copy the full paths as text (one per line)."""
    text = "\n".join(str(p) for p in original_paths(ctx))
    copied = util.copy_text(text)
    message = ctx.t("msg.file.path_copied") if copied else ctx.t("msg.file.path_shown")
    return ctx.result(kind="text", text=text, message=message)


@action(id="file.data_uri", label="act.file.data_uri", icon="data_object", categories={"*"}, multi="single",
        when=_small_single, undoable=False, description="desc.file.data_uri", order=60)
def file_data_uri(ctx: Context):
    """Copy the file as a ``data:<mime>;base64,…`` URI (files up to 1 MiB)."""
    f = ctx.file
    size = os.path.getsize(f.path)
    if size > DATA_URI_MAX:
        raise UserError("err.too_large")
    with open(f.path, "rb") as fh:
        data = fh.read()
    mime = f.mime or "application/octet-stream"
    if mime == "image/svg+xml-compressed":
        mime = "image/svg+xml"
    uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    copied = util.copy_text(uri)
    return ctx.result(kind="text", text=uri,
                      message=ctx.t("msg.file.uri_copied" if copied else "msg.file.uri_ready",
                                    size=util.human_size(len(uri), ctx.lang)))


# --------------------------------------------------------------------- move / copy / rename

def _dest_dir(ctx: Context) -> Path:
    raw = ctx.pstr("dest").strip()
    if not raw:
        raise UserError("err.missing_param", param=ctx.t("prm.file.dest"))
    dest = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not dest.is_absolute():
        dest = Path.home() / dest
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserError("err.not_writable", details=str(exc), dir=_display_dir(dest)) from None
    if not dest.is_dir():
        raise UserError("err.file.not_a_folder", dir=_display_dir(dest))
    if not os.access(dest, os.W_OK | os.X_OK):
        raise UserError("err.not_writable", dir=_display_dir(dest))
    return dest


def _same_device(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _inside(child: Path, parent: Path) -> bool:
    try:
        c = os.path.realpath(child)
        p = os.path.realpath(parent)
    except OSError:
        return False
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


@action(id="file.organize.move_to", label="act.file.move_to", icon="drive_file_move", categories={"*"},
        multi="any", mode="dialog", dialog="form", undoable=False, mixed_ok=True, description="desc.file.move_to",
        order=10, params=[P.dir("dest", "prm.file.dest", default="", required=True)])
def file_move_to(ctx: Context):
    """Move the originals into the chosen folder (never overwriting; across disks: copy, then the
    original goes to the trash)."""
    dest = _dest_dir(ctx)
    paths = original_paths(ctx)
    for p in paths:
        if p.is_dir() and _inside(dest, p):
            raise UserError("err.file.move_into_self", name=p.name)
    cross = [p for p in paths if not _same_device(p.parent, dest)]
    need = sum(tree_stats(p, ctx)[0] for p in cross)
    if need:
        _check_space(dest, need)
    prog = Progress(ctx, total=need or None, label=ctx.t("act.file.move_to"))
    outputs: list[Path] = []
    skipped_same = 0
    for p in paths:
        ctx.check_cancel()
        if os.path.realpath(p.parent) == os.path.realpath(dest):
            skipped_same += 1
            continue
        is_dir = p.is_dir() and not p.is_symlink()
        target = util.unique_path(dest / p.name, is_dir=is_dir)
        moved = False
        if p not in cross:
            try:
                if os.path.lexists(target):
                    target = util.unique_path(dest / p.name, is_dir=is_dir)
                os.rename(p, target)
                moved = True
            except OSError as exc:
                if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EACCES):
                    raise UserError("err.file.move_failed", details=str(exc), name=p.name) from None
                if exc.errno in (errno.EPERM, errno.EACCES):
                    raise UserError("err.permission", details=str(exc)) from None
        if not moved:
            with atomic_target(target) as part:
                copy_entry(ctx, p, part, prog)
            try:
                util.trash(p)
            except UserError as exc:
                ctx.warn(ctx.t("warn.file.original_kept", name=p.name))
                ctx.log(str(exc))
        outputs.append(target)
    if skipped_same:
        ctx.warn(i18n.tn("warn.file.already_there", skipped_same, ctx.lang))
    return ctx.result(outputs, message=i18n.tn("msg.file.moved", len(outputs), ctx.lang, dir=_display_dir(dest)))


@action(id="file.organize.copy_to", label="act.file.copy_to", icon="file_copy", categories={"*"}, multi="any",
        mode="dialog", dialog="form", mixed_ok=False, description="desc.file.copy_to", order=20,
        params=[P.dir("dest", "prm.file.dest", default="", required=True)])
def file_copy_to(ctx: Context):
    """Copy the files (folders recursively) into the chosen folder; names never collide."""
    dest = _dest_dir(ctx)
    srcs = [Path(f.path) for f in ctx.files]
    for p, orig in zip(srcs, original_paths(ctx)):
        if orig.is_dir() and _inside(dest, orig):
            raise UserError("err.file.copy_into_self", name=orig.name)
    need = sum(tree_stats(p, ctx)[0] for p in srcs)
    _check_space(dest, need)
    prog = Progress(ctx, total=need or None, label=ctx.t("act.file.copy_to"))
    outputs: list[Path] = []
    skipped: list[str] = []
    for f, p in zip(ctx.files, srcs):
        is_dir = p.is_dir() and not p.is_symlink()
        cand = util.unique_path(dest / ctx.original(f).name, is_dir=is_dir)
        target = ctx.output_path(f, name=cand.name, directory=dest)
        with atomic_target(target) as part:
            copy_entry(ctx, p, part, prog, skipped)
        outputs.append(target)
    if skipped:
        ctx.warn(i18n.tn("warn.file.special_skipped", len(skipped), ctx.lang))
    return ctx.result(outputs, message=i18n.tn("msg.file.copied_to", len(outputs), ctx.lang, dir=_display_dir(dest)))


def _rename_default(files: list[FileInfo]) -> str:
    if len(files) == 1:
        return files[0].name
    return "{name} {n:02}"


@action(id="file.organize.rename", label="act.file.rename", icon="drive_file_rename_outline", categories={"*"},
        multi="any", mode="dialog", dialog="form", undoable=False, mixed_ok=True, description="desc.file.rename",
        order=30, params=[
            P.text("name", "prm.file.new_name", default=_rename_default, required=True, help="prm.file.new_name.help"),
            P.int("start", "prm.file.start", 1, min=0, max=1_000_000),
            P.bool("dry_run", "prm.file.dry_run", False),
        ])
def file_rename(ctx: Context):
    """Rename the file (the extension is kept unless you type one) or several files with a pattern
    such as ``{name} {n:02}`` (``{n}`` counts from *start*; ``{ext}``, ``{date}`` are available)."""
    pattern = ctx.pstr("name").strip()
    if not pattern:
        raise UserError("err.missing_param", param=ctx.t("prm.file.new_name"))
    paths = original_paths(ctx)
    if len(paths) > 1 and not _PLACEHOLDER_RE.search(pattern):
        pattern = pattern + " {n}"
    if len(paths) > 1:
        paths = util.natural_sorted(paths, key=lambda p: p.name)
    plan = plan_renames(paths, pattern, start=ctx.pint("start", 1))
    return _rename_result(ctx, plan, dry_run=ctx.pbool("dry_run"))


def _rename_result(ctx: Context, plan: RenamePlan, dry_run: bool):
    lines = [f"{src.name} → {dst.name}" for src, dst in plan.pairs]
    if plan.conflicts:
        raise UserError("err.file.rename_conflict", details="\n".join(plan.conflicts), n=len(plan.conflicts))
    if not plan.pairs:
        return ctx.result(kind="none", message=ctx.t("msg.file.rename_nothing"))
    if dry_run:
        return ctx.result(kind="text", text="\n".join(lines),
                          message=i18n.tn("msg.file.rename_preview", len(lines), ctx.lang))
    new_paths = apply_renames(plan.pairs)
    return ctx.result(new_paths, text="\n".join(lines),
                      message=i18n.tn("msg.file.renamed", len(new_paths), ctx.lang))


@action(id="file.organize.duplicate", label="act.file.duplicate", icon="control_point_duplicate", categories={"*"},
        multi="each", description="desc.file.duplicate", order=40)
def file_duplicate(ctx: Context):
    """Make a copy next to the original: ``"<stem> (copia).<ext>"``."""
    f = ctx.file
    src = Path(f.path)
    size = tree_stats(src, ctx)[0]
    out = ctx.output_path(f, suffix="sfx.copy")
    _check_space(out.parent, size)
    prog = Progress(ctx, total=size or None, label=ctx.t("act.file.duplicate"))
    skipped: list[str] = []
    with atomic_target(out) as part:
        copy_entry(ctx, src, part, prog, skipped)
    if skipped:
        ctx.warn(i18n.tn("warn.file.special_skipped", len(skipped), ctx.lang))
    return ctx.result([out])


# --------------------------------------------------------------------------------- archives

def _zip_params() -> list:
    return [
        P.select("level", "prm.arc.level", options=[("fast", "opt.arc.level.fast"), ("normal", "opt.arc.level.normal"),
                                                    ("max", "opt.arc.level.max")], default="normal"),
        P.password("password", "prm.arc.password", default="", help="prm.arc.password.help"),
        P.select("split", "prm.arc.split", options=[(0, "opt.arc.split.none"), (25, "25 MB"), (100, "100 MB"),
                                                    (700, "700 MB"), (1000, "1 GB"), (4000, "4 GB")], default=0),
    ]


@action(id="file.zip", label="act.file.zip", icon="folder_zip", categories={"*"}, multi="any", mode="quick",
        mixed_ok=True, description="desc.file.zip", order=1, params=_zip_params())
def file_zip(ctx: Context):
    """Compress the selection into one ZIP (``"<name>.zip"``, or ``"Archivio <date>.zip"`` for several items)."""
    from . import archive

    paths = [Path(f.path) for f in ctx.files]
    outputs = archive.create_archive(ctx, paths, "zip", password=ctx.pstr("password"),
                                     level=ctx.pstr("level", "normal"), volume_mb=ctx.pint("split", 0))
    before = sum(tree_stats(p)[0] for p in paths)
    return ctx.result(outputs, size_before=before or None)


# ------------------------------------------------------------------------------------ share

def _share_start(ctx: Context, paths: list[Path]) -> dict:
    try:
        from .. import share as share_mod  # core-B: LAN share server + QR
    except ImportError as exc:
        raise UserError("err.file.share_unavailable", details=str(exc)) from None
    fn = getattr(share_mod, "start_share", None)
    if not callable(fn):
        raise UserError("err.file.share_unavailable")
    minutes = int(cfgmod.get(ctx.config, "share.minutes", 15) or 15)
    port = int(cfgmod.get(ctx.config, "share.port", 0) or 0)
    title = paths[0].name if len(paths) == 1 else i18n.tn("menu.files", len(paths), ctx.lang)
    offered = {"minutes": minutes, "port": port, "config": ctx.config, "lang": ctx.lang, "title": title,
               "label": title}
    try:
        sig = inspect.signature(fn)
        names = set(sig.parameters)
        var_kw = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    except (TypeError, ValueError):
        names, var_kw = set(), False
    kwargs = {k: v for k, v in offered.items() if k in names or (var_kw and k in ("minutes", "port"))}
    try:
        res = fn([str(p) for p in paths], **kwargs)
    except UserError:
        raise
    except OSError as exc:
        raise UserError("err.file.share_failed", details=str(exc)) from None
    if isinstance(res, dict):
        data = dict(res)
    else:
        data = {k: getattr(res, k) for k in ("url", "qr", "qr_path", "png", "id", "share_id", "expires")
                if hasattr(res, k)}
    url = data.get("url")
    if not url:
        raise UserError("err.file.share_failed")
    return {"url": str(url), "qr": data.get("qr") or data.get("qr_path") or data.get("png"),
            "share_id": data.get("share_id") or data.get("id"), "expires": data.get("expires"), "minutes": minutes}


@action(id="file.share.phone", label="act.file.share_phone", icon="qr_code_2", categories={"*"}, multi="any",
        when=_files_only, undoable=False, mixed_ok=True, description="desc.file.share_phone", order=10)
def file_share_phone(ctx: Context):
    """Serve the files on the local network for a few minutes and show a QR code for the phone."""
    info = _share_start(ctx, persistent_paths(ctx))
    return ctx.result(kind="share", text=info["url"], url=info["url"], qr=info["qr"], share_id=info["share_id"],
                      expires=info["expires"], message=ctx.t("msg.file.share_ready", minutes=info["minutes"]))


@action(id="file.share.kdeconnect", label="act.file.share_kde", icon="phonelink", categories={"*"}, multi="any",
        mode="quick", when=_files_only, requires=["kdeconnect-cli"], undoable=False,
        description="desc.file.share_kde", order=20,
        params=[P.select("device", "prm.file.device", options=_kde_options, default=_kde_default)])
def file_share_kdeconnect(ctx: Context):
    """Send the files to a paired, reachable KDE Connect device."""
    device = ctx.pstr("device")
    devices = kdeconnect_devices(force=True, timeout=10)
    if not devices:
        raise UserError("err.file.kde_no_device")
    names = dict(devices)
    if not device or device not in names:
        device = devices[0][0]
    paths = persistent_paths(ctx)
    prog = Progress(ctx, total=len(paths), label=ctx.t("act.file.share_kde"))
    for p in paths:
        ctx.check_cancel()
        ctx.run(["kdeconnect-cli", "-d", device, "--share", str(p.absolute())], timeout=120)
        prog.add(1)
    return ctx.result(kind="none", message=i18n.tn("msg.file.sent_to", len(paths), ctx.lang, device=names[device]))


@action(id="file.share.localsend", label="act.file.share_localsend", icon="send_to_mobile", categories={"*"},
        multi="any", mode="dialog", dialog="form", when=_files_only, requires=["localsend-cli"], undoable=False,
        description="desc.file.share_localsend", order=30,
        params=[P.text("target", "prm.file.localsend_target", default="cfg:share.localsend_target", required=True,
                       help="prm.file.localsend_target.help")])
def file_share_localsend(ctx: Context):
    """Send the files with LocalSend to a device (alias or IP address); the receiver must accept."""
    target = ctx.pstr("target").strip()
    if not target:
        raise UserError("err.missing_param", param=ctx.t("prm.file.localsend_target"))
    paths = [str(Path(f.path).absolute()) for f in ctx.files]
    ctx.progress(None, ctx.t("msg.file.localsend_waiting", target=target))
    ctx.run(["localsend-cli", "send", "--to", target, *paths])
    return ctx.result(kind="none", message=i18n.tn("msg.file.sent_to", len(paths), ctx.lang, device=target))


@action(id="file.share.email", label="act.file.share_email", icon="mail", categories={"*"}, multi="any",
        when=_files_only, requires=["xdg-email"], undoable=False, description="desc.file.share_email", order=40)
def file_share_email(ctx: Context):
    """Open a new e-mail with the files attached (``xdg-email``)."""
    paths = persistent_paths(ctx)
    argv: list[str] = ["xdg-email", "--utf8", "--subject", ", ".join(p.name for p in paths)[:200]]
    for p in paths:
        argv += ["--attach", str(p.absolute())]
    if not util.detach(argv):
        raise _launch_error("xdg-email")
    return ctx.result(kind="none", message=ctx.t("msg.file.email_opened"))


def _launch_error(tool: str) -> UserError:
    """``MissingTool`` when ``tool`` is absent, otherwise a generic launch failure."""
    from ..errors import MissingTool

    if not tools.have(tool):
        return MissingTool(tool)
    return UserError("err.open_failed", name=tool)


def persistent_paths(ctx: Context) -> list[Path]:
    """Paths that stay valid after the job ends (for programs that read the files later: mail
    clients, KDE Connect, the share server).  Volatile inputs are copied into
    ``$XDG_CACHE_HOME/dropwheel/tmp/outbox-<job>`` (removed by the runner's cleanup after a day)."""
    out: list[Path] = []
    box: Path | None = None
    for f in ctx.files:
        orig = ctx.original(f)
        if not orig.volatile and orig.path and os.path.exists(orig.path):
            out.append(Path(orig.path))
            continue
        if box is None:
            box = util.cache_dir() / "tmp" / f"outbox-{ctx.job_id}"
        target = box / str(len(out)) / util.safe_filename(orig.name or f.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            copy_entry(ctx, f.path, target)
        out.append(target)
    return out


# ------------------------------------------------------------------------------- properties

def _category_label(ctx: Context, f: FileInfo) -> str:
    return ctx.t(f"cat.{f.category}")


def _perm_text(ctx: Context, path: str) -> str:
    parts = []
    if os.access(path, os.R_OK):
        parts.append(ctx.t("info.file.perm_read"))
    if os.access(path, os.W_OK):
        parts.append(ctx.t("info.file.perm_write"))
    if os.access(path, os.X_OK) and not os.path.isdir(path):
        parts.append(ctx.t("info.file.perm_exec"))
    return ", ".join(parts) or ctx.t("info.file.perm_none")


def _owner(st: os.stat_result) -> tuple[str, str]:
    try:
        import grp
        import pwd

        try:
            user = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            user = str(st.st_uid)
        try:
            group_name = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group_name = str(st.st_gid)
        return user, group_name
    except ImportError:
        return str(getattr(st, "st_uid", "")), str(getattr(st, "st_gid", ""))


def properties_rows(ctx: Context, f: FileInfo, prog: Progress | None = None) -> list[dict]:
    """Info rows (general, contents, dates, permissions, media) for one file or folder."""
    orig = ctx.original(f)
    path = orig.path or f.path
    rows: list[dict] = []
    g_gen, g_dates, g_perm, g_media = "info.grp.general", "info.grp.dates", "info.grp.permissions", "info.grp.media"
    st = os.lstat(path)
    rows.append(info_row(ctx, g_gen, "info.file.name", orig.name))
    rows.append(info_row(ctx, g_gen, "info.file.type", f"{_category_label(ctx, f)} ({f.mime})"))
    if stat.S_ISLNK(st.st_mode):
        try:
            rows.append(info_row(ctx, g_gen, "info.file.link", os.readlink(path)))
        except OSError:
            pass
    rows.append(info_row(ctx, g_gen, "info.file.location", _display_dir(os.path.dirname(path))))
    if f.is_dir:
        size, files, dirs = tree_stats(path, ctx)
        rows.append(info_row(ctx, g_gen, "info.file.size", size_text(size, ctx.lang)))
        rows.append(info_row(ctx, g_gen, "info.file.contents", ctx.t("info.file.contents_value", files=files,
                                                                     folders=dirs)))
    else:
        size = os.stat(path).st_size
        rows.append(info_row(ctx, g_gen, "info.file.size", size_text(size, ctx.lang)))
    full = os.stat(path) if os.path.exists(path) else st
    rows.append(info_row(ctx, g_dates, "info.file.modified", fmt_time(full.st_mtime, ctx.lang)))
    birth = getattr(full, "st_birthtime", None)
    if birth:
        rows.append(info_row(ctx, g_dates, "info.file.created", fmt_time(birth, ctx.lang)))
    rows.append(info_row(ctx, g_dates, "info.file.accessed", fmt_time(full.st_atime, ctx.lang)))
    if os.name == "posix":
        rows.append(info_row(ctx, g_dates, "info.file.changed", fmt_time(full.st_ctime, ctx.lang)))
    rows.append(info_row(ctx, g_perm, "info.file.mode", f"{stat.filemode(full.st_mode)} ({oct(full.st_mode & 0o7777)[2:]:0>4})"))
    user, group_name = _owner(full)
    rows.append(info_row(ctx, g_perm, "info.file.owner", f"{user}:{group_name}"))
    rows.append(info_row(ctx, g_perm, "info.file.you_can", _perm_text(ctx, path)))
    if f.width and f.height:
        mp = f.width * f.height / 1_000_000
        mp_text = f"{mp:.1f}".replace(".", "," if ctx.lang == "it" else ".")
        rows.append(info_row(ctx, g_media, "info.file.dimensions", f"{f.width} × {f.height} px ({mp_text} MP)"))
    if f.pages:
        rows.append(info_row(ctx, g_media, "info.file.pages", f.pages))
    if f.category in ("audio", "video") and tools.have("ffprobe"):
        try:
            duration = ctx.duration(path)
        except UserError:
            duration = None
        if duration:
            rows.append(info_row(ctx, g_media, "info.file.duration", util.human_duration(duration)))
    return rows


@action(id="file.properties", label="act.file.properties", icon="info", categories={"*"}, multi="each",
        undoable=False, description="desc.file.properties", order=10)
def file_properties(ctx: Context):
    """Name, type, size (folders: recursive), dates, permissions, owner and media details."""
    f = ctx.file
    if f.is_dir:
        ctx.progress(None, ctx.t("msg.file.measuring"))
    rows = properties_rows(ctx, f)
    return ctx.result(kind="info", info=rows, message=f.name)


# -------------------------------------------------------------------------------- checksums

def _checksum(ctx: Context, algo: str):
    files = [f for f in ctx.files if not f.is_dir]
    total = sum(os.path.getsize(f.path) for f in files)
    prog = Progress(ctx, total=total or None, label=HASH_ALGOS[algo])
    lines = []
    for f in files:
        digest = hash_file(ctx, f.path, algo, prog)
        lines.append((digest, ctx.original(f).name))
    if len(lines) == 1:
        text = lines[0][0]
    else:
        text = "\n".join(f"{d}  {n}" for d, n in lines)
    copied = util.copy_text(text)
    key = "msg.file.checksum_copied" if copied else "msg.file.checksum"
    return ctx.result(kind="text", text=text, message=ctx.t(key, algo=HASH_ALGOS[algo]))


def _checksum_action(algo: str, order: int, requires: list | None = None) -> None:
    def fn(ctx: Context):
        return _checksum(ctx, algo)

    fn.__name__ = f"file_checksum_{algo}"
    fn.__doc__ = f"{HASH_ALGOS[algo]} of the files (one line per file, copied to the clipboard)."
    action(id=f"file.checksum.{algo}", label=HASH_ALGOS[algo], icon="fingerprint", categories={"*"}, multi="any",
           when=_no_dirs, undoable=False, requires=requires or [], mixed_ok=(algo == "sha256"),
           description="desc.file.checksum", order=order)(fn)


_checksum_action("sha256", 1)
_checksum_action("sha1", 2)
_checksum_action("md5", 3)
_checksum_action("blake2", 4)
_checksum_action("xxh128", 5, requires=[("py:xxhash", "xxh128sum")])


def verify_file(ctx: Context, f: FileInfo, expected_text: str = "") -> dict:
    """Verify ``f`` against ``expected_text`` (hash or checksum line), a checksum file next to it
    or a digest on the clipboard.  Returns ``{algo, expected, actual, source}``; raises on mismatch."""
    orig = ctx.original(f)
    parsed = parse_expected_checksum(expected_text, orig.name) if expected_text.strip() else None
    source = ctx.t("info.file.source_typed")
    if expected_text.strip() and parsed is None:
        raise UserError("err.file.bad_checksum")
    if parsed is None:
        found = find_checksum_for(orig.path)
        if found is not None:
            algos, digest, src_file = found
            parsed = (algos, digest)
            source = src_file.name
    if parsed is None:
        clip = _clipboard_digest()
        if clip:
            parsed = parse_expected_checksum(clip, orig.name)
            source = ctx.t("info.file.source_clipboard")
    if parsed is None:
        raise UserError("err.file.no_checksum", name=orig.name)
    algos, digest = parsed
    algos = [a for a in algos if a != "xxh128" or tools.have("xxh128sum") or new_hash("xxh128") is not None]
    if not algos:
        raise UserError("err.file.bad_checksum")
    size = os.path.getsize(f.path)
    prog = Progress(ctx, total=(size * len(algos)) or None, label=ctx.t("act.file.verify"))
    actual = ""
    for algo in algos:
        actual = hash_file(ctx, f.path, algo, prog)
        if actual.lower() == digest.lower():
            return {"algo": HASH_ALGOS[algo], "expected": digest, "actual": actual, "source": source}
    raise UserError("err.file.checksum_mismatch", details=f"{ctx.t('info.file.expected')}: {digest}\n"
                                                          f"{ctx.t('info.file.actual')}: {actual}",
                    algo=HASH_ALGOS[algos[-1]])


@action(id="file.verify", label="act.file.verify", icon="verified", categories={"*"}, multi="single",
        mode="quick", when=_no_dirs, undoable=False, description="desc.file.verify", order=30,
        params=[P.textarea("expected", "prm.file.expected", default="", help="prm.file.expected.help")])
def file_verify(ctx: Context):
    """Compare the file with an expected checksum (pasted, from ``SHA256SUMS``/``<name>.sha256`` next
    to it, or from the clipboard)."""
    res = verify_file(ctx, ctx.file, ctx.pstr("expected"))
    g = "info.grp.checksum"
    rows = [info_row(ctx, g, "info.file.result", ctx.t("msg.file.checksum_ok")),
            info_row(ctx, g, "info.file.algorithm", res["algo"]),
            info_row(ctx, g, "info.file.expected", res["expected"]),
            info_row(ctx, g, "info.file.actual", res["actual"]),
            info_row(ctx, g, "info.file.source", res["source"])]
    return ctx.result(kind="info", info=rows, message=ctx.t("msg.file.checksum_match", algo=res["algo"]))


# ------------------------------------------------------------------------------- gpg crypto

def _gpg_error(exc: ToolError, have_password: bool) -> UserError:
    text = (exc.details or "").lower()
    if "bad session key" in text or "bad passphrase" in text or "decryption failed" in text and "session" in text:
        return UserError("err.wrong_password" if have_password else "err.password_required", details=exc.details)
    if "no secret key" in text:
        return UserError("err.file.gpg_no_key", details=exc.details)
    if "no valid openpgp data" in text or "unknown packet" in text:
        return UserError("err.file.not_pgp", details=exc.details)
    if "no passphrase given" in text or "missing passphrase" in text:
        return UserError("err.password_required", details=exc.details)
    return exc


_GPG_BASE = ["gpg", "--batch", "--yes", "--no-tty", "--quiet", "--pinentry-mode", "loopback", "--passphrase-fd", "0"]


@action(id="file.encrypt", label="act.file.encrypt", icon="enhanced_encryption", categories={"*"}, multi="each",
        mode="dialog", dialog="form", requires=["gpg"], description="desc.file.encrypt", order=40, params=[
            P.password("password", "prm.file.password", default="", required=True),
            P.password("confirm", "prm.file.confirm", default=""),
            P.bool("armor", "prm.file.armor", False),
        ])
def file_encrypt(ctx: Context):
    """Encrypt with a password (OpenPGP, AES-256; folders are packed into a .tar first)."""
    password = ctx.pstr("password")
    if not password:
        raise UserError("err.missing_param", param=ctx.t("prm.file.password"))
    confirm = ctx.pstr("confirm")
    if confirm and confirm != password:
        raise UserError("err.file.password_mismatch")
    f = ctx.file
    src = Path(f.path)
    name = ctx.original(f).name
    if f.is_dir:
        tar_path = ctx.tmpdir() / (util.safe_filename(name) + ".tar")
        prog = Progress(ctx, 0.0, 0.5, total=tree_stats(src, ctx)[0] or None, label=ctx.t("act.file.encrypt"))
        _tar_folder(ctx, src, tar_path, prog)
        src = tar_path
        name = name + ".tar"
    armor = ctx.pbool("armor")
    out = ctx.output_path(f, name=name + (".asc" if armor else ".gpg"))
    ctx.progress(None, ctx.t("act.file.encrypt"))
    with atomic_target(out) as part:
        argv = [*_GPG_BASE, "--symmetric", "--cipher-algo", "AES256", "--s2k-digest-algo", "SHA512",
                "--no-symkey-cache"]
        if armor:
            argv.append("--armor")
        argv += ["--output", str(part), "--", str(src)]
        try:
            ctx.run(argv, input=password + "\n")
        except ToolError as exc:
            raise _gpg_error(exc, True) from None
    return ctx.result([out], message=ctx.t("msg.file.encrypted"))


def _tar_folder(ctx: Context, src: Path, out: Path, prog: Progress) -> None:
    """Pack folder ``src`` into the plain tar ``out`` (symlinks kept as links, progress per file)."""

    def flt(info: tarfile.TarInfo) -> tarfile.TarInfo:
        ctx.check_cancel()
        if info.isreg():
            prog.add(info.size, info.name)
        return info

    try:
        with tarfile.open(out, "w", format=tarfile.PAX_FORMAT) as tf:
            tf.add(str(src), arcname=src.name, recursive=True, filter=flt)
    except OSError as exc:
        raise UserError("err.file.copy_failed", details=str(exc), name=src.name) from None


def _decrypted_name(name: str) -> str:
    low = name.lower()
    for ext in (".gpg", ".pgp", ".asc"):
        if low.endswith(ext) and len(name) > len(ext):
            return name[: -len(ext)]
    return name + ".out"


@action(id="file.decrypt", label="act.file.decrypt", icon="no_encryption", categories={"*"}, multi="each",
        mode="dialog", dialog="form", requires=["gpg"], when=lambda files: all(_is_pgp(f) for f in files),
        description="desc.file.decrypt", order=50,
        params=[P.password("password", "prm.file.password", default="", help="prm.file.decrypt_password.help")])
def file_decrypt(ctx: Context):
    """Decrypt an OpenPGP file (.gpg/.pgp/.asc) with its password or your secret key."""
    f = ctx.file
    password = ctx.pstr("password")
    out = ctx.output_path(f, name=_decrypted_name(ctx.original(f).name))
    ctx.progress(None, ctx.t("act.file.decrypt"))
    with atomic_target(out) as part:
        argv = [*_GPG_BASE, "--output", str(part), "--decrypt", "--", f.path]
        try:
            ctx.run(argv, input=(password + "\n") if password else "")
        except ToolError as exc:
            raise _gpg_error(exc, bool(password)) from None
    return ctx.result([out], message=ctx.t("msg.file.decrypted"))


# -------------------------------------------------------------------------- chmod / trash

@action(id="file.chmodx", label="act.file.chmodx", icon="play_circle", categories={"*"}, multi="each",
        when=_can_chmodx, undoable=False, description="desc.file.chmodx", order=55)
def file_chmodx(ctx: Context):
    """Add the execute permission where the file is readable (like ``chmod +x``)."""
    path = ctx.original(ctx.file).path
    st = os.stat(path)
    mode = st.st_mode & 0o7777
    new_mode = mode | ((mode & 0o444) >> 2) | stat.S_IXUSR
    try:
        os.chmod(path, new_mode)
    except OSError as exc:
        raise UserError("err.permission", details=str(exc)) from None
    return ctx.result(kind="none", message=ctx.t("msg.file.executable", name=ctx.original(ctx.file).name))


@action(id="file.trash", label="act.file.trash", icon="delete", categories={"*"}, multi="any", danger=True,
        undoable=False, description="desc.file.trash", order=90)
def file_trash(ctx: Context):
    """Move the originals to the trash (they can be restored from the trash)."""
    paths = original_paths(ctx)
    for p in paths:
        ctx.check_cancel()
        util.trash(p)
    return ctx.result(kind="none", message=i18n.tn("msg.file.trashed", len(paths), ctx.lang))


# ===================================================================================== layout

group("file.checksum", "grp.checksum", "fingerprint",
      ["file.checksum.sha256", "file.checksum.sha1", "file.checksum.md5", "file.checksum.blake2",
       "file.checksum.xxh128"], default="file.checksum.sha256")
group("file.organize", "grp.organize", "drive_file_move",
      ["file.organize.move_to", "file.organize.copy_to", "file.organize.rename", "file.organize.duplicate"],
      default="file.organize.move_to")
group("file.share", "grp.share", "share",
      ["file.share.phone", "file.share.kdeconnect", "file.share.localsend", "file.share.email"],
      default="file.share.phone")
group("file.info", "grp.info", "info", ["file.properties", "file.checksum", "file.verify"], default="file.properties")
# the id ends with ".more" so job labels show the action alone ("Apri", not "Altro · Apri")
group("file.page.more", "grp.more", "more_horiz",
      ["file.open", "file.reveal", "file.terminal", "file.encrypt", "file.decrypt", "file.chmodx", "file.data_uri",
       "file.trash"], default="file.open")

layout("file", ["file.copy", "file.copy_path", "file.organize", "file.share", "file.open_with", "file.zip",
                "file.info", "file.page.more"])
layout("mixed", ["file.zip"])
