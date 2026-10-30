"""Archive actions (category ``archive``) and the archive helpers shared by the File page,
folders and packages.

Actions: Extract here★ (smart: a single top-level item is extracted directly, otherwise into
``"<stem>/"``), Extract to…, List contents, Test integrity, Convert → ZIP★/7Z/TAR.ZST/TAR.GZ/TAR.XZ,
Protect → 7z AES-256 with encrypted names★ / ZIP AES-256 / Remove password, Split/Join → Join parts★
(first part of a series) / 100 MB / 1 GB / 4 GB (FAT32) / custom size.

Safety (every engine): entries with ``..`` components are refused, leading ``/`` and drive letters
are stripped, symlinks pointing outside the destination and device/FIFO entries are dropped,
setuid/setgid bits are cleared, a zip-bomb guard compares declared sizes with the archive size and
the free space (``force`` overrides the ratio check), and extraction happens in a hidden folder in
the destination that is renamed into place only when everything succeeded.

Engines: Python ``zipfile`` (stored/deflate/bzip2/lzma, ZipCrypto passwords) and ``tarfile`` with
``filter="data"`` (gz/bz2/xz/lzma natively, zstd through ``compression.zstd`` on Python ≥ 3.14,
``zstandard`` or the ``zstd`` binary); 7-Zip (``7z`` from the *7zip* package, RAR included) for
7z/RAR/ISO/CAB/AES zips and anything else; ``bsdtar`` and ``unrar`` as fallbacks.  Passwords are
always passed on stdin, never on the command line.

Public helpers: :func:`create_archive`, :func:`extract_archive`, :func:`extract_raw`,
:func:`list_entries`, :func:`detect_kind`, :func:`archive_output_path`, :func:`split_file`.
"""

from __future__ import annotations

import bz2
import functools
import gzip
import io
import lzma
import os
import re
import secrets
import stat
import subprocess
import tarfile
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, Sequence

from .. import i18n, tools, util
from ..detect import FileInfo
from ..errors import MissingTool, ToolError, UserError
from ..jobs import Context, atomic_target
from ..registry import P, action, group, layout
from .common import Progress, TreeNode, render_tree

__all__ = [
    "CREATE_FORMATS", "FORMAT_TEXT", "Kind", "Entry", "ExtractStats", "detect_kind", "create_archive",
    "archive_output_path", "extract_archive", "extract_raw", "list_entries", "format_of", "split_file", "split_names",
    "run_7z",
]

_CHUNK = 1024 * 1024
_MB = 1000 * 1000
_SPACE_MARGIN = 32 * 1024 * 1024
_ZIP_LINK_MAX = 4096   # PATH_MAX: a zip symlink's data is its target

#: formats that can be created
CREATE_FORMATS: tuple[str, ...] = ("zip", "7z", "tar.zst", "tar.gz", "tar.xz")
FORMAT_TEXT = {"zip": "ZIP", "7z": "7Z", "tar.zst": "TAR.ZST", "tar.gz": "TAR.GZ", "tar.xz": "TAR.XZ"}
_LEVELS: dict[str, dict[str, int]] = {
    "zip": {"fast": 1, "normal": 6, "max": 9},
    "7z": {"fast": 1, "normal": 5, "max": 9},
    "tar.gz": {"fast": 1, "normal": 6, "max": 9},
    "tar.xz": {"fast": 1, "normal": 6, "max": 9},
    "tar.zst": {"fast": 1, "normal": 6, "max": 19},
}

_CODEC_MAGIC = ((b"\x1f\x8b", "gz"), (b"\xfd7zXZ\x00", "xz"), (b"\x28\xb5\x2f\xfd", "zst"), (b"BZh", "bz2"),
                (b"\x04\x22\x4d\x18", "lz4"), (b"LZIP", "lz"), (b"\x1f\x9d", "z"))
_TAR_EXTS = {"tar", "tar.gz", "tgz", "tar.xz", "txz", "tar.zst", "tzst", "tar.bz2", "tbz2", "tbz", "tar.lz4",
             "tar.lz", "tar.lzma", "tlz", "tar.z", "tar.br", "cbt", "pkg.tar.zst", "pkg.tar.xz", "pkg.tar.gz",
             "pkg.tar.bz2", "tar.001"}
_EXT_CODEC = {"tar.lzma": "lzma", "tlz": "lzma", "lzma": "lzma", "tar.br": "br", "br": "br"}
_TAR_CODEC_FORMAT = {"": "tar", "gz": "tar.gz", "xz": "tar.xz", "zst": "tar.zst", "bz2": "tar.bz2",
                     "lz4": "tar.lz4", "lz": "tar.lz", "lzma": "tar.lzma", "z": "tar.z", "br": "tar.br"}
_EXTERNAL_DECOMP: dict[str, tuple[tuple[str, ...], ...]] = {
    "zst": (("zstd", "-dcq"),),
    "lz4": (("lz4", "-dcq"),),
    "lz": (("lzip", "-dcq"), ("plzip", "-dcq")),
    "z": (("gzip", "-dc"),),
    "br": (("brotli", "-dc"),),
    "gz": (("gzip", "-dc"),),
}
_PY_ZIP_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
_TAR_FILTER = hasattr(tarfile, "data_filter")
_PCT_RE = re.compile(r"(\d{1,3})%")
_SPLIT_RE = re.compile(r"\.(0*1)$")


class _NeedExternal(Exception):
    """The Python engine cannot handle this archive (AES zip, unusual compression method)."""


# ================================================================================ zstd support

@functools.lru_cache(maxsize=1)
def _zstd_module() -> tuple[str, Any] | None:
    try:
        from compression import zstd as std_zstd  # type: ignore  # Python ≥ 3.14

        return "std", std_zstd
    except ImportError:
        pass
    try:
        import zstandard  # type: ignore

        return "zstandard", zstandard
    except ImportError:
        return None


def _zstd_requires() -> list:
    try:
        import importlib.util

        if importlib.util.find_spec("compression.zstd") is not None:
            return []
    except (ImportError, ValueError):
        pass
    return [("zstd", "py:zstandard")]


_ZSTD_REQ = _zstd_requires()


# ==================================================================================== kinds

@dataclass(frozen=True)
class Kind:
    """What an archive file is: ``kind`` zip | tar | single | 7z | rar | other; ``codec`` for tar/single."""

    kind: str
    codec: str = ""
    split: bool = False


def _is_tar_block(block: bytes) -> bool:
    if len(block) < 512:
        return False
    if block[257:262] == b"ustar":
        return True
    try:
        tarfile.TarInfo.frombuf(block[:512], "utf-8", "surrogateescape")
        return True
    except (tarfile.HeaderError, ValueError, UnicodeDecodeError):
        return False


def _peek_tar(path: str, codec: str) -> bool:
    """Decompress the first block of a single-stream file and check for a tar header."""
    try:
        if codec == "gz":
            with gzip.open(path, "rb") as fh:
                return _is_tar_block(fh.read(512))
        if codec == "bz2":
            with bz2.open(path, "rb") as fh:
                return _is_tar_block(fh.read(512))
        if codec in ("xz", "lzma"):
            with lzma.open(path, "rb") as fh:
                return _is_tar_block(fh.read(512))
        if codec == "zst":
            mod = _zstd_module()
            if mod is not None and mod[0] == "std":
                with mod[1].open(path, "rb") as fh:
                    return _is_tar_block(fh.read(512))
            if mod is not None:
                with open(path, "rb") as raw:
                    reader = mod[1].ZstdDecompressor().stream_reader(raw, read_across_frames=True)
                    return _is_tar_block(reader.read(512))
    except (OSError, EOFError, zlib.error, lzma.LZMAError, ValueError):
        return False
    except Exception:  # third-party zstd errors
        return False
    return False


@functools.lru_cache(maxsize=256)
def _detect_cached(path: str, ext: str, size: int, mtime: float) -> Kind:
    name = os.path.basename(path).lower()
    split = bool(_SPLIT_RE.search(name))
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        head = b""
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return Kind("zip", split=split)
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return Kind("7z", split=split)
    if head.startswith(b"Rar!\x1a\x07"):
        return Kind("rar", split=split)
    codec = next((c for magic, c in _CODEC_MAGIC if head.startswith(magic)), "")
    if not codec:
        codec = _EXT_CODEC.get(ext, "")
    if codec:
        if ext in _TAR_EXTS or _peek_tar(path, codec):
            return Kind("tar", codec, split)
        return Kind("single", codec, split)
    if (len(head) >= 262 and head[257:262] == b"ustar") or ext in ("tar", "cbt"):
        return Kind("tar", "", split)
    if not split:
        try:
            if zipfile.is_zipfile(path):
                return Kind("zip")
        except OSError:
            pass
    return Kind("other", "", split)


def detect_kind(path: str | os.PathLike, ext: str = "") -> Kind:
    """Detect the archive kind of ``path`` from its content (magic bytes), then its extension."""
    p = os.fspath(path)
    if not ext:
        ext = util.split_name(os.path.basename(p))[1]
    try:
        st = os.stat(p)
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0.0
    return _detect_cached(p, ext.lower(), size, mtime)


def format_of(f: FileInfo) -> str:
    """Canonical format of an archive file: ``zip``, ``7z``, ``rar``, ``tar.gz``…, ``single`` or ``other``."""
    k = detect_kind(f.path, f.ext)
    if k.kind == "tar":
        return _TAR_CODEC_FORMAT.get(k.codec, "tar")
    return k.kind


def _codec_python(codec: str) -> bool:
    if codec in ("", "gz", "bz2", "xz", "lzma"):
        return True
    return codec == "zst" and _zstd_module() is not None


def _codec_available(codec: str) -> bool:
    if _codec_python(codec):
        return True
    return any(tools.have(argv[0]) for argv in _EXTERNAL_DECOMP.get(codec, ()))


# =================================================================================== streams

class _Counter(io.RawIOBase):
    """Readable wrapper counting bytes and checking for cancellation."""

    def __init__(self, raw: BinaryIO, ctx: Context) -> None:
        super().__init__()
        self.raw = raw
        self.ctx = ctx
        self.count = 0
        self.name = getattr(raw, "name", "")

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readinto(self, b) -> int:  # type: ignore[override]
        self.ctx.check_cancel()
        n = self.raw.readinto(b)
        n = n or 0
        self.count += n
        return n


def _drain(stream: BinaryIO, sink: list[bytes], limit: int = 256 * 1024) -> None:
    size = 0
    try:
        while True:
            data = stream.read(8192)
            if not data:
                break
            if size < limit:
                sink.append(data)
                size += len(data)
    except (OSError, ValueError):
        pass


def _popen(argv: Sequence[str], **kw: Any) -> subprocess.Popen:
    exe = tools.path(argv[0])
    if exe is None:
        raise MissingTool(argv[0])
    if os.name == "posix":
        kw["start_new_session"] = True
    return subprocess.Popen([exe, *argv[1:]], close_fds=True, **kw)


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix" and hasattr(os, "killpg"):
            os.killpg(proc.pid, 15)
        else:
            proc.terminate()
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix" and hasattr(os, "killpg"):
                os.killpg(proc.pid, 9)
            else:
                proc.kill()
        except OSError:
            pass


class _Source:
    """A readable *decompressed* stream of an archive file (Python codec or external decompressor)."""

    def __init__(self, ctx: Context, path: str | os.PathLike, codec: str) -> None:
        self.ctx = ctx
        self.path = os.fspath(path)
        self.codec = codec
        self.raw = open(self.path, "rb")
        try:
            self.size = os.fstat(self.raw.fileno()).st_size
        except OSError:
            self.size = 0
        self.counter = _Counter(self.raw, ctx)
        self.proc: subprocess.Popen | None = None
        self._err: list[bytes] = []
        self._thread: threading.Thread | None = None
        try:
            self.stream: Any = self._open()
        except BaseException:
            self.raw.close()
            raise

    def _open(self) -> Any:
        c = self.codec
        if not c:
            return self.counter
        if c == "gz":
            return gzip.GzipFile(fileobj=self.counter, mode="rb")
        if c == "bz2":
            return bz2.BZ2File(self.counter, "rb")
        if c in ("xz", "lzma"):
            return lzma.LZMAFile(self.counter, "rb")
        if c == "zst":
            mod = _zstd_module()
            if mod is not None and mod[0] == "std":
                return mod[1].ZstdFile(self.counter, "rb")
            if mod is not None:
                return mod[1].ZstdDecompressor().stream_reader(self.counter, read_across_frames=True)
        for argv in _EXTERNAL_DECOMP.get(c, ()):
            if tools.have(argv[0]):
                self.proc = _popen(argv, stdin=self.raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self._thread = threading.Thread(target=_drain, args=(self.proc.stderr, self._err), daemon=True)
                self._thread.start()
                return _Counter(self.proc.stdout, self.ctx)  # type: ignore[arg-type]
        alts = _EXTERNAL_DECOMP.get(c, ((c,),))
        raise MissingTool(alts[0][0])

    def fraction(self) -> float | None:
        """Share of the compressed file consumed so far (``None`` with external decompressors)."""
        if self.proc is not None or not self.size:
            return None
        return min(1.0, self.counter.count / self.size)

    def finish(self) -> None:
        """Check the external decompressor's exit status after reading everything."""
        if self.proc is None:
            return
        try:
            rc = self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill(self.proc)
            rc = -1
        if self._thread is not None:
            self._thread.join(timeout=2)
        if rc != 0:
            raise UserError("err.damaged", details=b"".join(self._err).decode("utf-8", "replace")[-2000:])

    def close(self) -> None:
        """Release everything (kills a still running decompressor)."""
        if self.proc is not None:
            _kill(self.proc)
            for s in (self.proc.stdout, self.proc.stderr):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass
        try:
            if self.stream is not self.counter and hasattr(self.stream, "close"):
                self.stream.close()
        except Exception:
            pass
        try:
            self.raw.close()
        except OSError:
            pass


class _FileSink:
    """Write side of a Python compressor."""

    def __init__(self, comp: Any, raw: BinaryIO) -> None:
        self.comp = comp
        self.raw = raw

    def write(self, data: bytes) -> int:
        self.comp.write(data)
        return len(data)

    def close(self) -> None:
        self.comp.close()
        if not self.raw.closed:
            self.raw.close()

    def abort(self) -> None:
        for obj in (self.comp, self.raw):
            try:
                obj.close()
            except Exception:
                pass


class _ProcSink:
    """Write side of an external compressor (``zstd -T0``, ``xz -T0``, ``pigz``): stdin → file."""

    def __init__(self, argv: Sequence[str], out_path: str | os.PathLike) -> None:
        self.name = argv[0]
        self.out = open(out_path, "wb")
        try:
            self.proc = _popen(argv, stdin=subprocess.PIPE, stdout=self.out, stderr=subprocess.PIPE)
        except BaseException:
            self.out.close()
            raise
        self._err: list[bytes] = []
        self._thread = threading.Thread(target=_drain, args=(self.proc.stderr, self._err), daemon=True)
        self._thread.start()

    def _details(self) -> str:
        return b"".join(self._err).decode("utf-8", "replace")[-2000:]

    def write(self, data: bytes) -> int:
        try:
            self.proc.stdin.write(data)  # type: ignore[union-attr]
        except (BrokenPipeError, OSError, ValueError):
            self._thread.join(timeout=2)
            raise ToolError("err.tool_failed", details=self._details(), tool=self.name,
                            code=self.proc.poll()) from None
        return len(data)

    def close(self) -> None:
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        rc = self.proc.wait()
        self._thread.join(timeout=2)
        self.out.close()
        if rc != 0:
            raise ToolError("err.tool_failed", details=self._details(), tool=self.name, code=rc)

    def abort(self) -> None:
        _kill(self.proc)
        try:
            self.out.close()
        except OSError:
            pass


def _open_sink(fmt: str, part: Path, level: int) -> Any:
    """A writable compressed sink for ``tar.gz`` / ``tar.xz`` / ``tar.zst`` written to ``part``."""
    if fmt == "tar.gz":
        if tools.have("pigz"):
            return _ProcSink(["pigz", f"-{level}", "-c"], part)
        raw = open(part, "wb")
        return _FileSink(gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=level), raw)
    if fmt == "tar.xz":
        if tools.have("xz"):
            return _ProcSink(["xz", "-T0", f"-{level}", "-c"], part)
        raw = open(part, "wb")
        return _FileSink(lzma.LZMAFile(raw, "wb", preset=level), raw)
    if fmt == "tar.zst":
        if tools.have("zstd"):
            return _ProcSink(["zstd", "-T0", f"-{level}", "-q", "-c"], part)
        mod = _zstd_module()
        if mod is not None and mod[0] == "std":
            raw = open(part, "wb")
            return _FileSink(mod[1].ZstdFile(raw, "w", level=level), raw)
        if mod is not None:
            raw = open(part, "wb")
            writer = mod[1].ZstdCompressor(level=level, threads=-1).stream_writer(raw, closefd=False)
            return _FileSink(writer, raw)
        raise MissingTool("zstd")
    raise ValueError(f"no tar sink for {fmt}")


# ================================================================================= creation

def archive_output_path(ctx: Context, fmt: str, files: Sequence[FileInfo] | None = None) -> Path:
    """``"<name>.<fmt>"`` for one item, ``"Archivio YYYY-MM-DD HH.MM.<fmt>"`` for several."""
    items = list(files or ctx.files)
    if len(items) == 1:
        return ctx.output_path(items[0], ext=fmt)
    stamp = datetime.now().strftime("%Y-%m-%d %H.%M")
    return ctx.output_path(items[0], name=ctx.t("arc.multi_name", date=stamp), ext=fmt)


def _arcname(name: str) -> str:
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


@dataclass
class _Item:
    path: str
    arc: str
    st: os.stat_result


def _collect(ctx: Context, paths: Sequence[Path]) -> tuple[list[_Item], int, list[str]]:
    """Every entry below ``paths`` with its archive name (top level = the item's own name)."""
    items: list[_Item] = []
    total = 0
    errors: list[str] = []
    used: set[str] = set()

    def onerror(exc: OSError) -> None:
        errors.append(str(getattr(exc, "filename", "") or exc))

    for p in paths:
        p = Path(p)
        base = p.name or "root"
        top = base
        n = 2
        while top.lower() in used:
            top = f"{base} {n}"
            n += 1
        used.add(top.lower())
        st = os.lstat(p)
        items.append(_Item(str(p), _arcname(top), st))
        if stat.S_ISREG(st.st_mode):
            total += st.st_size
        if not stat.S_ISDIR(st.st_mode):
            continue
        for root, dirs, files in os.walk(p, onerror=onerror, followlinks=False):
            ctx.check_cancel()
            dirs.sort()
            rel = os.path.relpath(root, p)
            prefix = top if rel == "." else top + "/" + rel.replace(os.sep, "/")
            for name in dirs + sorted(files):
                full = os.path.join(root, name)
                try:
                    est = os.lstat(full)
                except OSError as exc:
                    errors.append(f"{full}: {exc}")
                    continue
                items.append(_Item(full, _arcname(f"{prefix}/{name}"), est))
                if stat.S_ISREG(est.st_mode):
                    total += est.st_size
    return items, total, errors


def _zip_time(ts: float) -> tuple[int, int, int, int, int, int]:
    t = time.localtime(max(ts, 315532800))
    return (max(1980, t.tm_year), t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def _write_zip(ctx: Context, items: list[_Item], part: Path, level: int, prog: Progress) -> int:
    skipped = 0
    with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level, allowZip64=True,
                         strict_timestamps=False) as zf:
        for it in items:
            ctx.check_cancel()
            mode = it.st.st_mode
            if stat.S_ISDIR(mode):
                zi = zipfile.ZipInfo(it.arc + "/", date_time=_zip_time(it.st.st_mtime))
                zi.create_system = 3
                zi.external_attr = ((stat.S_IFDIR | (mode & 0o777)) << 16) | 0x10
                zf.writestr(zi, b"")
            elif stat.S_ISLNK(mode):
                zi = zipfile.ZipInfo(it.arc, date_time=_zip_time(it.st.st_mtime))
                zi.create_system = 3
                zi.external_attr = (stat.S_IFLNK | 0o777) << 16
                zi.compress_type = zipfile.ZIP_STORED
                zf.writestr(zi, os.fsencode(os.readlink(it.path)))
            elif stat.S_ISREG(mode):
                zi = zipfile.ZipInfo(it.arc, date_time=_zip_time(it.st.st_mtime))
                zi.create_system = 3
                zi.external_attr = (stat.S_IFREG | (mode & 0o777)) << 16
                zi.file_size = it.st.st_size
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi._compresslevel = level  # type: ignore[attr-defined]  (alias of compress_level on 3.13+)
                with open(it.path, "rb") as src, zf.open(zi, "w", force_zip64=it.st.st_size > 0x7F000000) as dst:
                    while True:
                        ctx.check_cancel()
                        data = src.read(_CHUNK)
                        if not data:
                            break
                        dst.write(data)
                        prog.add(len(data), it.arc)
            else:
                skipped += 1
    return skipped


def _write_tar(ctx: Context, items: list[_Item], part: Path, fmt: str, level: int, prog: Progress) -> int:
    sink = _open_sink(fmt, part, level)
    skipped = 0

    class _Reader(io.RawIOBase):
        def __init__(self, fh: BinaryIO, label: str) -> None:
            super().__init__()
            self.fh = fh
            self.label = label

        def readable(self) -> bool:
            return True

        def readinto(self, b) -> int:  # type: ignore[override]
            ctx.check_cancel()
            n = self.fh.readinto(b) or 0
            prog.add(n, self.label)
            return n

    try:
        with tarfile.open(fileobj=sink, mode="w|", format=tarfile.PAX_FORMAT) as tf:
            for it in items:
                ctx.check_cancel()
                info = tf.gettarinfo(it.path, arcname=it.arc)
                if info is None:
                    skipped += 1
                    continue
                if info.isreg():
                    with open(it.path, "rb") as fh:
                        tf.addfile(info, _Reader(fh, it.arc))
                else:
                    tf.addfile(info)
        sink.close()
    except BaseException:
        sink.abort()
        raise
    return skipped


_PCT_BYTES_RE = re.compile(rb"(\d{1,3})%")
_7Z_TAIL_LINES = 15


def run_7z(ctx: Context, argv: Sequence[str], *, password: str = "", prog: Progress | None = None,
           tick: Callable[[], None] | None = None, ok_codes: Sequence[int] = (0, 1),
           timeout: float | None = None, cwd: str | os.PathLike | None = None) -> str:
    """Run 7-Zip (``argv[0] == "7z"``) with live progress and return its standard output.

    7-Zip redraws its ``-bsp1`` percentage with backspaces, never with a newline, so a line based
    reader would only see it at the very end: here the output is scanned as it arrives.  ``tick``
    is called about every 100 ms (e.g. the zip-bomb guard); whatever it raises stops 7-Zip.  The
    password goes to standard input (never on the command line).  Cancellation kills the whole
    process group.  Raises :class:`MissingTool`, :class:`ToolError` (stderr tail in ``details``)
    or :class:`~dropwheel.errors.Cancelled`.

    File and archive names on the command line need ``-spd``, or 7-Zip expands the ``*`` and ``?``
    they may contain.  ``-o`` replaces a ``*`` with the archive name whatever the switches: extract
    with ``cwd`` set to the destination and ``-o.``.
    """
    exe = tools.path("7z")
    if exe is None:
        raise MissingTool("7z")
    ctx.check_cancel()
    ctx.log("$ 7z " + " ".join(str(a) for a in argv[1:]))
    try:
        proc = _popen(["7z", *[str(a) for a in argv[1:]]], stdin=subprocess.PIPE if password else subprocess.DEVNULL,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=os.fspath(cwd) if cwd is not None else None)
    except PermissionError as exc:
        raise ToolError("err.tool_not_executable", details=str(exc), tool="7-Zip") from None
    except OSError as exc:
        raise ToolError("err.tool_failed", details=str(exc), tool="7-Zip", code="?") from None
    state: dict[str, Any] = {"pct": None, "out": [], "size": 0}
    err: list[bytes] = []

    def read_out() -> None:
        tail = b""
        try:
            while True:
                data = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)  # type: ignore[union-attr]
                if not data:
                    break
                if state["size"] < 8 * 1024 * 1024:
                    state["out"].append(data)
                    state["size"] += len(data)
                found = _PCT_BYTES_RE.findall(tail + data)
                if found:
                    state["pct"] = min(100, int(found[-1]))
                tail = data[-8:]
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=read_out, daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, err, 1024 * 1024), daemon=True)]
    if password:
        def write_in() -> None:
            try:
                proc.stdin.write((password + "\n").encode("utf-8"))  # type: ignore[union-attr]
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    proc.stdin.close()  # type: ignore[union-attr]
                except (OSError, ValueError):
                    pass

        threads.append(threading.Thread(target=write_in, daemon=True))
    for th in threads:
        th.start()
    start = time.monotonic()
    try:
        while True:
            try:
                proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
            if ctx.cancelled:
                _kill(proc)
                ctx.check_cancel()
            if timeout is not None and time.monotonic() - start > timeout:
                _kill(proc)
                raise ToolError("err.timeout", tool="7-Zip", seconds=int(timeout))
            if prog is not None and state["pct"] is not None:
                prog.frac(state["pct"] / 100.0)
            if tick is not None:
                tick()
    except BaseException:
        _kill(proc)
        raise
    finally:
        for th in threads:
            th.join(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
    ctx.check_cancel()
    rc = proc.returncode
    if rc not in tuple(ok_codes):
        text = b"".join(err).decode("utf-8", "replace")
        lines = [ln.strip() for ln in re.split(r"[\r\n\x08]+", text) if ln.strip()]
        if not lines:
            out_text = b"".join(state["out"]).decode("utf-8", "replace")
            lines = [ln.strip() for ln in re.split(r"[\r\n\x08]+", out_text) if ln.strip()]
        code = rc if rc >= 0 else f"{rc} (signal {-rc})"
        raise ToolError("err.tool_failed", details="\n".join(lines[-_7Z_TAIL_LINES:]), tool="7-Zip", code=code)
    if prog is not None:
        prog.frac(1.0, force=True)
    return b"".join(state["out"]).decode("utf-8", "replace")


def _pct_callback(prog: Progress, extra: Callable[[str], None] | None = None) -> Callable[[str], None]:
    def on_line(line: str) -> None:
        found = _PCT_RE.findall(line)
        if found:
            prog.frac(int(found[-1]) / 100.0)
        if extra is not None:
            extra(line)

    return on_line


def _create_7z(ctx: Context, paths: Sequence[Path], part: Path, fmt: str, level: int, password: str,
               prog: Progress) -> None:
    argv: list[str] = ["7z", "a", "-t7z" if fmt == "7z" else "-tzip", f"-mx={level}", "-mmt=on", "-bsp1", "-bso0",
                       "-bse2", "-y", "-ssw", "-spd", "-sccUTF-8"]
    if fmt == "zip":
        argv.append("-mcu=on")
    if password:
        argv += ["-p", "-mhe=on"] if fmt == "7z" else ["-p", "-mem=AES256"]
    argv += [str(part), "--", *[str(Path(p).absolute()) for p in paths]]
    try:
        run_7z(ctx, argv, password=password, prog=prog, ok_codes=(0, 1))
    except ToolError as exc:
        raise _map_7z_error(exc, password) from None


def _split_volumes(ctx: Context, path: Path, size: int, prog: Progress | None = None) -> list[Path]:
    """Cut ``path`` (our own fresh output) into ``<name>.001``, ``.002``… of ``size`` bytes."""
    total = path.stat().st_size
    if size <= 0 or total <= size:
        return [path]
    count = (total + size - 1) // size
    parts: list[Path] = []
    with open(path, "rb") as fh:
        for index in range(1, count + 1):
            target = ctx.output_path(name=f"{path.name}.{index:03d}", directory=path.parent)
            with atomic_target(target) as part, open(part, "wb") as out:
                left = size
                while left > 0:
                    ctx.check_cancel()
                    data = fh.read(min(_CHUNK, left))
                    if not data:
                        break
                    out.write(data)
                    left -= len(data)
                    if prog is not None:
                        prog.frac(fh.tell() / total)
            parts.append(target)
    os.unlink(path)
    return parts


def create_archive(ctx: Context, paths: Sequence[str | os.PathLike], fmt: str, out: Path | None = None, *,
                   password: str = "", level: str = "normal", volume_mb: int = 0,
                   prog: Progress | None = None) -> list[Path]:
    """Create one archive of ``paths`` (files and folders, each stored under its own name).

    ``fmt``: zip | 7z | tar.zst | tar.gz | tar.xz.  ``password`` works for zip (AES-256 via 7-Zip)
    and 7z (AES-256 with encrypted names).  ``level``: fast | normal | max.  ``volume_mb > 0`` splits
    the result into ``.001``… parts of that many MB (joinable with ``cat`` or 7-Zip).  Returns the
    created file(s).  Raises :class:`MissingTool` when 7-Zip is needed but missing.
    """
    fmt = fmt.lower()
    if fmt not in CREATE_FORMATS:
        raise UserError("err.not_supported")
    if password and fmt not in ("zip", "7z"):
        raise UserError("err.arc.password_format", fmt=FORMAT_TEXT[fmt])
    plist = [Path(p) for p in paths]
    if not plist:
        raise UserError("err.no_files")
    lvl = _LEVELS[fmt].get(level or "normal", _LEVELS[fmt]["normal"])
    target = out if out is not None else archive_output_path(ctx, fmt)
    use_7z = fmt == "7z" or bool(password)
    if use_7z and not tools.have("7z"):
        raise MissingTool("7z")
    prog = prog or Progress(ctx, label=ctx.t("msg.arc.compressing"))
    skipped = 0
    try:
        with atomic_target(target) as part:
            if use_7z:
                _create_7z(ctx, plist, part, fmt, lvl, password, prog)
            else:
                items, total, errors = _collect(ctx, plist)
                if errors:
                    ctx.warn(i18n.tn("warn.arc.unreadable", len(errors), ctx.lang))
                    ctx.log("\n".join(errors[:50]))
                prog.total = total or None
                if fmt == "zip":
                    skipped = _write_zip(ctx, items, part, lvl, prog)
                else:
                    skipped = _write_tar(ctx, items, part, fmt, lvl, prog)
    except OSError as exc:
        if getattr(exc, "errno", None) == 28:
            raise UserError("err.disk_full", details=str(exc)) from None
        raise UserError("err.arc.create_failed", details=str(exc)) from None
    if skipped:
        ctx.warn(i18n.tn("warn.file.special_skipped", skipped, ctx.lang))
    if volume_mb and volume_mb > 0:
        return _split_volumes(ctx, target, int(volume_mb) * _MB)
    return [target]


# ================================================================================== listing

@dataclass
class Entry:
    """One archive member."""

    name: str
    size: int = 0
    is_dir: bool = False
    is_link: bool = False
    link: str = ""
    special: bool = False
    encrypted: bool = False
    packed: int | None = None


@dataclass
class ExtractStats:
    """What an extraction did (counts of skipped unsafe entries for the warnings)."""

    files: int = 0
    dirs: int = 0
    unsafe: int = 0
    links: int = 0
    special: int = 0
    duplicates: int = 0
    encrypted: bool = False


def _fix_zip_name(zi: zipfile.ZipInfo) -> str:
    name = zi.filename
    if not zi.flag_bits & 0x800 and any(ord(ch) > 127 for ch in name):
        try:
            return name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return name
    return name


def _list_zip(path: str | os.PathLike) -> list[Entry]:
    try:
        with zipfile.ZipFile(path) as zf:
            out = []
            for zi in zf.infolist():
                mode = (zi.external_attr >> 16) & 0xFFFF
                is_link = zi.create_system == 3 and stat.S_ISLNK(mode)
                out.append(Entry(_fix_zip_name(zi), zi.file_size, zi.is_dir(), is_link, "", False,
                                 bool(zi.flag_bits & 0x1) or zi.compress_type == 99, zi.compress_size))
            return out
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError) as exc:
        raise UserError("err.damaged", details=str(exc)) from None


def _map_7z_error(exc: ToolError, password: str) -> UserError:
    text = (exc.details or "").lower()
    if any(s in text for s in ("wrong password", "can not open encrypted archive", "enter password",
                               "break signaled", "password")):
        return UserError("err.wrong_password" if password else "err.arc.password_needed", details=exc.details)
    if "can not open the file as archive" in text or "is not archive" in text:
        return UserError("err.arc.not_archive", details=exc.details)
    if "unsupported method" in text:
        return UserError("err.not_supported", details=exc.details)
    if "no space left" in text or "disk full" in text:
        return UserError("err.disk_full", details=exc.details)
    if any(s in text for s in ("data error", "crc failed", "headers error", "unexpected end", "is truncated")):
        return UserError("err.damaged", details=exc.details)
    return exc


def _entry_from_7z(d: dict) -> Entry:
    attrs = d.get("Attributes", "")
    mode_str = ""
    for token in attrs.split():
        if re.match(r"^[-dlcbps][-rwxsStT]{9}", token):
            mode_str = token
    is_dir = d.get("Folder") == "+" or mode_str.startswith("d") or attrs.startswith("D")
    is_link = mode_str.startswith("l") or bool(d.get("Symbolic Link")) or bool(d.get("Link"))

    def num(key: str) -> int | None:
        value = d.get(key, "")
        return int(value) if value.isdigit() else None

    return Entry(name=d.get("Path", "").replace("\\", "/") if os.sep == "\\" else d.get("Path", ""),
                 size=num("Size") or 0, is_dir=is_dir, is_link=is_link,
                 link=d.get("Symbolic Link", "") or d.get("Link", ""),
                 special=mode_str[:1] in ("c", "b", "p", "s"), encrypted=d.get("Encrypted") == "+",
                 packed=num("Packed Size"))


def _list_7z(ctx: Context, path: str | os.PathLike, password: str) -> list[Entry]:
    argv = ["7z", "l", "-slt", "-ba", "-spd", "-sccUTF-8"]
    if password:
        argv.append("-p")
    argv += ["--", os.fspath(path)]
    try:
        res = ctx.run(argv, input=(password + "\n") if password else None, timeout=600, ok_codes=(0, 1))
    except ToolError as exc:
        raise _map_7z_error(exc, password) from None
    entries: list[Entry] = []
    cur: dict[str, str] = {}
    for line in (res.stdout or "").splitlines() + [""]:
        if not line.strip():
            if "Path" in cur and "Physical Size" not in cur:
                entries.append(_entry_from_7z(cur))
            cur = {}
            continue
        key, sep, value = line.partition(" = ")
        if sep:
            cur[key.strip()] = value
    return entries


_BSDTAR_LINE = re.compile(r"^([-dlcbps][-rwxsStT]{9})\S*\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\d+\s+[\d:]+\s+(.*)$")


def _list_bsdtar(ctx: Context, path: str | os.PathLike) -> list[Entry]:
    res = ctx.run(["bsdtar", "-tvf", os.fspath(path)], timeout=600)
    entries: list[Entry] = []
    for line in (res.stdout or "").splitlines():
        m = _BSDTAR_LINE.match(line)
        if not m:
            continue
        mode, size, name = m.group(1), int(m.group(2)), m.group(3)
        link = ""
        if mode.startswith("l") and " -> " in name:
            name, link = name.split(" -> ", 1)
        entries.append(Entry(name=name.rstrip("/"), size=size, is_dir=mode.startswith("d"), is_link=mode.startswith("l"),
                             link=link, special=mode[:1] in ("c", "b", "p", "s")))
    return entries


def _list_tar(ctx: Context, path: str | os.PathLike, codec: str, prog: Progress | None = None) -> list[Entry]:
    src = _Source(ctx, path, codec)
    entries: list[Entry] = []
    try:
        with tarfile.open(fileobj=src.stream, mode="r|") as tf:
            for m in tf:
                ctx.check_cancel()
                if prog is not None:
                    prog.frac(src.fraction(), m.name)
                entries.append(Entry(m.name, m.size if m.isreg() else 0, m.isdir(), m.issym() or m.islnk(),
                                     m.linkname, m.isdev() or m.isfifo()))
        src.finish()
    except (tarfile.TarError, EOFError, zlib.error, lzma.LZMAError, OSError) as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    finally:
        src.close()
    return entries


def _gzip_size(path: str) -> int | None:
    try:
        with open(path, "rb") as fh:
            fh.seek(-4, os.SEEK_END)
            return int.from_bytes(fh.read(4), "little")
    except OSError:
        return None


def list_entries(ctx: Context, path: str | os.PathLike, kind: Kind | None = None, password: str = "",
                 prog: Progress | None = None) -> list[Entry]:
    """Members of an archive (zip/tar: Python; others: 7-Zip, then bsdtar)."""
    p = os.fspath(path)
    kind = kind or detect_kind(p)
    if kind.kind == "zip" and not kind.split:
        return _list_zip(p)
    if kind.kind == "tar" and _codec_available(kind.codec):
        return _list_tar(ctx, p, kind.codec, prog)
    if kind.kind == "single":
        name = util.split_name(os.path.basename(p))[0]
        return [Entry(name, (_gzip_size(p) or 0) if kind.codec == "gz" else 0)]
    if tools.have("7z"):
        return _list_7z(ctx, p, password)
    if tools.have("bsdtar"):
        return _list_bsdtar(ctx, p)
    raise MissingTool("7z")


# ================================================================================ extraction

def _safe_rel(name: str) -> list[str] | None:
    """Sanitised relative path components, or ``None`` for unsafe/empty names."""
    n = name.replace("\\", "/").replace("\x00", "")
    n = re.sub(r"^[A-Za-z]:", "", n)
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if os.name == "nt":
        parts = [util.safe_filename(p) for p in parts]
    return parts


def _link_ok(rel_parts: Sequence[str], link: str) -> bool:
    """Lexical check of a member's link target (only half of it: see :func:`_create_zip_links`)."""
    if not link or "\x00" in link or link.startswith("/") or re.match(r"^[A-Za-z]:", link):
        return False
    joined = os.path.normpath(os.path.join(*rel_parts[:-1], link) if len(rel_parts) > 1 else link)
    return not (joined == ".." or joined.startswith(".." + os.sep) or joined.startswith("../") or os.path.isabs(joined))


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _create_zip_links(work: Path, links: Sequence[tuple[Sequence[str], str]], stats: ExtractStats) -> None:
    """Create the symlinks of a zip after every regular member has been written.

    Nothing is written after this point, so a link can never be used as a way out by a later
    member.  Each link must also sit in a real folder of ``work``: ``symlink(2)`` on ``a/a/b`` with
    ``a -> .`` would really create ``b``, whose target passed the lexical check for another depth.
    Links whose meaning only changes when another link appears are for :func:`_sanitize_tree`,
    which looks at the final tree.
    """
    root = os.path.realpath(work)
    for rel, link in links:
        target = work.joinpath(*rel)
        parents = [work.joinpath(*rel[:i]) for i in range(1, len(rel))]
        try:
            if (not _link_ok(rel, link) or os.path.lexists(target) or any(os.path.islink(p) for p in parents)
                    or not _within(os.path.realpath(os.path.join(root, *rel[:-1], link)), root)):
                stats.links += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(link, target)
        except OSError:
            stats.links += 1


class _Guard:
    """Zip-bomb and disk-space guard (declared sizes, actual bytes, periodic folder size)."""

    def __init__(self, ctx: Context, dest: Path, cap: int | None) -> None:
        self.ctx = ctx
        self.dest = dest
        self.cap = cap
        self.declared = 0
        self.written = 0
        self._next = 64 * _MB
        self._last_dir = 0.0

    def _too_big(self) -> UserError:
        return UserError("err.arc.too_big", size=util.human_size(self.cap or 0, self.ctx.lang))

    def space(self, need: int = 0) -> None:
        free = util.free_space(self.dest)
        if free is not None and free - need < _SPACE_MARGIN:
            raise UserError("err.disk_full")

    def add(self, n: int) -> None:
        """Account for a member of declared size ``n`` before writing it."""
        self.declared += max(0, int(n))
        if self.cap is not None and self.declared > self.cap:
            raise self._too_big()
        if n > 16 * _MB or self.declared >= self._next:
            self._next = self.declared + 64 * _MB
            self.space(n)

    def write(self, n: int) -> None:
        """Account for ``n`` bytes actually written."""
        self.written += n
        if self.cap is not None and self.written > self.cap:
            raise self._too_big()
        if self.written >= self._next:
            self._next = self.written + 64 * _MB
            self.space()

    def watch(self, path: Path) -> None:
        """External engines: measure the extraction folder every 2 s."""
        now = time.monotonic()
        if now - self._last_dir < 2.0:
            return
        self._last_dir = now
        size = util.dir_size(path, limit=500_000)
        if self.cap is not None and size > self.cap:
            raise self._too_big()
        self.space()


def _bomb_check(ctx: Context, total: int, packed: int, dest: Path, force: bool) -> None:
    free = util.free_space(dest)
    if free is not None and total > free - _SPACE_MARGIN:
        raise UserError("err.arc.no_space", need=util.human_size(total, ctx.lang), free=util.human_size(free, ctx.lang))
    if not force and total > 1000 * _MB and packed > 0 and total / packed > 100:
        raise UserError("err.arc.bomb", ratio=int(total / packed), size=util.human_size(total, ctx.lang))


def _set_zip_attrs(target: Path, zi: zipfile.ZipInfo) -> None:
    try:
        ts = time.mktime(zi.date_time + (0, 0, -1))
        os.utime(target, (ts, ts))
    except (OverflowError, ValueError, OSError):
        pass
    mode = (zi.external_attr >> 16) & 0o777
    if zi.create_system == 3 and mode:
        try:
            os.chmod(target, (mode & 0o777) | 0o600)
        except OSError:
            pass


def _extract_zip_py(ctx: Context, path: str, work: Path, password: str, guard: _Guard, prog: Progress,
                    stats: ExtractStats) -> None:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    with zf:
        infos = zf.infolist()
        if any(zi.compress_type not in _PY_ZIP_METHODS for zi in infos):
            raise _NeedExternal()
        encrypted = any(zi.flag_bits & 0x1 for zi in infos)
        stats.encrypted = encrypted
        if encrypted and not password:
            raise UserError("err.arc.password_needed")
        pwd = password.encode("utf-8") if password else None
        total = sum(zi.file_size for zi in infos) or 1
        done = 0
        links: list[tuple[list[str], str]] = []
        for zi in infos:
            ctx.check_cancel()
            rel = _safe_rel(_fix_zip_name(zi))
            if rel is None:
                stats.unsafe += 1
                continue
            target = work.joinpath(*rel)
            mode = (zi.external_attr >> 16) & 0xFFFF
            try:
                if zi.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if zi.create_system == 3 and stat.S_ISLNK(mode):
                    if zi.file_size > _ZIP_LINK_MAX:           # the content is the target: never load a huge one
                        stats.links += 1
                    else:
                        links.append((rel, os.fsdecode(zf.read(zi, pwd=pwd))))
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if os.path.lexists(target):
                    stats.duplicates += 1
                    continue
                guard.add(zi.file_size)
                with zf.open(zi, pwd=pwd) as src, open(target, "xb") as dst:
                    while True:
                        ctx.check_cancel()
                        chunk = src.read(_CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
                        guard.write(len(chunk))
                        done += len(chunk)
                        prog.frac(done / total, zi.filename)
            except (FileExistsError, NotADirectoryError):      # a file and a folder with the same name
                stats.duplicates += 1
                continue
            except OSError as exc:
                if exc.errno == 28:
                    raise UserError("err.disk_full", details=str(exc)) from None
                raise UserError("err.arc.extract_failed", details=str(exc), name=zi.filename) from None
            except RuntimeError as exc:
                msg = str(exc).lower()
                if "password required" in msg:
                    raise UserError("err.arc.password_needed") from None
                if "bad password" in msg:
                    raise UserError("err.wrong_password") from None
                raise UserError("err.damaged", details=str(exc)) from None
            except NotImplementedError:
                raise _NeedExternal() from None
            except zipfile.BadZipFile as exc:
                if encrypted and "crc" in str(exc).lower():
                    raise UserError("err.wrong_password", details=str(exc)) from None
                raise UserError("err.damaged", details=str(exc)) from None
            except (zlib.error, lzma.LZMAError, EOFError) as exc:
                raise UserError("err.damaged", details=str(exc)) from None
            _set_zip_attrs(target, zi)
        _create_zip_links(work, links, stats)


def _keep(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
    return member


def _filter_tar_member(member: tarfile.TarInfo, dest: str, stats: ExtractStats) -> tarfile.TarInfo | None:
    if _TAR_FILTER:
        try:
            return tarfile.data_filter(member, dest)
        except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError):
            stats.links += 1
        except tarfile.SpecialFileError:
            stats.special += 1
        except tarfile.FilterError:
            stats.unsafe += 1
        return None
    rel = _safe_rel(member.name)
    if rel is None:
        stats.unsafe += 1
        return None
    if member.isdev() or member.isfifo():
        stats.special += 1
        return None
    if member.issym() and not _link_ok(rel, member.linkname):
        stats.links += 1
        return None
    if member.islnk() and _safe_rel(member.linkname) is None:
        stats.links += 1
        return None
    member.name = "/".join(rel)
    member.mode &= 0o755 if member.isdir() else 0o755
    return member


def _extract_tar_py(ctx: Context, path: str, codec: str, work: Path, guard: _Guard, prog: Progress,
                    stats: ExtractStats) -> None:
    src = _Source(ctx, path, codec)
    dest = str(work)
    try:
        try:
            tf = tarfile.open(fileobj=src.stream, mode="r|", errorlevel=1)
        except tarfile.ReadError as exc:
            raise UserError("err.damaged", details=str(exc)) from None
        with tf:
            for member in tf:
                ctx.check_cancel()
                prog.frac(src.fraction(), member.name)
                safe = _filter_tar_member(member, dest, stats)
                if safe is None:
                    continue
                if safe.isreg():
                    guard.add(safe.size)
                try:
                    if _TAR_FILTER:
                        tf.extract(safe, dest, set_attrs=True, filter=_keep)
                    else:
                        tf.extract(safe, dest, set_attrs=True)
                except tarfile.StreamError:
                    stats.links += 1
                except tarfile.ExtractError as exc:
                    ctx.log(f"tar: {exc}")
                    stats.special += 1
                except OSError as exc:
                    if exc.errno == 28:
                        raise UserError("err.disk_full", details=str(exc)) from None
                    if safe.islnk() or safe.issym():
                        stats.links += 1
                        continue
                    raise UserError("err.arc.extract_failed", details=str(exc), name=safe.name) from None
        src.finish()
    except (tarfile.TarError, EOFError, zlib.error, lzma.LZMAError) as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    except OSError as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    finally:
        src.close()


def _decompress_single(ctx: Context, path: str, codec: str, part: Path, guard: _Guard, prog: Progress) -> None:
    src = _Source(ctx, path, codec)
    try:
        with open(part, "wb") as out:
            while True:
                ctx.check_cancel()
                chunk = src.stream.read(_CHUNK)
                if not chunk:
                    break
                guard.write(len(chunk))
                out.write(chunk)
                prog.frac(src.fraction())
        src.finish()
    except (EOFError, zlib.error, lzma.LZMAError) as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    except OSError as exc:
        if exc.errno == 28:
            raise UserError("err.disk_full", details=str(exc)) from None
        raise UserError("err.damaged", details=str(exc)) from None
    finally:
        src.close()


def _extract_7z(ctx: Context, path: str, work: Path, password: str, guard: _Guard, prog: Progress) -> None:
    argv = ["7z", "x", "-y", "-bsp1", "-bso0", "-bse2", "-spd", "-sccUTF-8", "-aou", "-o."]
    if password:
        argv.append("-p")
    argv += ["--", os.path.abspath(path)]
    try:
        run_7z(ctx, argv, password=password, prog=prog, tick=lambda: guard.watch(work), ok_codes=(0, 1), cwd=work)
    except ToolError as exc:
        raise _map_7z_error(exc, password) from None


def _extract_bsdtar(ctx: Context, path: str, work: Path, guard: _Guard, prog: Progress) -> None:
    def on_line(line: str) -> None:
        name = line[2:] if line.startswith("x ") else line
        prog.frac(None, name.strip()[:200])
        guard.watch(work)

    try:
        ctx.run(["bsdtar", "-x", "-v", "-f", path, "-C", str(work), "--no-same-owner"], on_line=on_line,
                stderr_lines=True)
    except ToolError as exc:
        text = (exc.details or "").lower()
        if "passphrase" in text or "encrypt" in text:
            raise UserError("err.arc.password_needed", details=exc.details) from None
        if "unrecognized archive format" in text or "not an archive" in text:
            raise UserError("err.arc.not_archive", details=exc.details) from None
        raise UserError("err.damaged", details=exc.details) from None


def _extract_unrar(ctx: Context, path: str, work: Path, guard: _Guard, prog: Progress) -> None:
    if "*" in path or "?" in path:
        # unrar expands them in the archive name and would extract every matching archive
        safe = ctx.tmpdir() / f"archive-{secrets.token_hex(3)}.rar"
        os.symlink(os.path.abspath(path), safe)
        path = str(safe)
    try:
        ctx.run(["unrar", "x", "-o-", "-p-", "-y", path, str(work) + os.sep],
                on_line=_pct_callback(prog, lambda _line: guard.watch(work)), ok_codes=(0, 1))
    except ToolError as exc:
        code = exc.fmt.get("code")
        if code == 11 or "password" in (exc.details or "").lower():
            raise UserError("err.arc.password_needed", details=exc.details) from None
        if code in (3, 13):
            raise UserError("err.damaged", details=exc.details) from None
        raise


def _sanitize_tree(root: Path, stats: ExtractStats) -> None:
    """Drop escaping symlinks and special files, clear setuid bits, make everything owner-writable."""
    root_real = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames) + list(filenames):
            p = os.path.join(dirpath, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            mode = st.st_mode
            if stat.S_ISLNK(mode):
                try:
                    target = os.readlink(p)
                except OSError:
                    target = ""
                ok = bool(target) and not os.path.isabs(target) and _within(os.path.realpath(p), root_real)
                if not ok:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                    stats.links += 1
                    if name in dirnames:
                        dirnames.remove(name)
                else:
                    stats.files += 1
            elif stat.S_ISDIR(mode):
                stats.dirs += 1
                if (mode & 0o7000) or (mode & 0o700) != 0o700:
                    try:
                        os.chmod(p, (mode & 0o777) | 0o700)
                    except OSError:
                        pass
            elif stat.S_ISREG(mode):
                stats.files += 1
                if (mode & 0o7000) or (mode & 0o600) != 0o600:
                    try:
                        os.chmod(p, (mode & 0o777) | 0o600)
                    except OSError:
                        pass
            else:
                try:
                    os.unlink(p)
                except OSError:
                    pass
                stats.special += 1


def _split_parts(path: Path) -> list[Path]:
    """``x.7z.001`` → [x.7z.001, x.7z.002, …] (contiguous)."""
    m = _SPLIT_RE.search(path.name)
    if not m:
        return [path]
    width = len(m.group(1))
    base = path.name[: m.start(1)]
    parts = []
    index = 1
    while True:
        cand = path.with_name(f"{base}{index:0{width}d}")
        if not cand.is_file():
            break
        parts.append(cand)
        index += 1
    return parts or [path]


def _join_parts(ctx: Context, parts: Sequence[Path], out: Path, prog: Progress) -> None:
    total = sum(p.stat().st_size for p in parts)
    prog.total = total or None
    with open(out, "xb") as dst:
        for p in parts:
            with open(p, "rb") as src:
                while True:
                    ctx.check_cancel()
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    prog.add(len(chunk))


def extract_raw(ctx: Context, src_path: str | os.PathLike, work: Path, *, password: str = "", force: bool = False,
                prog: Progress | None = None, kind: Kind | None = None) -> ExtractStats:
    """Extract every (safe) member of an archive into the existing folder ``work``.

    No placement logic: the caller decides what to do with ``work``'s content.  Raises
    :class:`UserError` for passwords (``err.arc.password_needed`` / ``err.wrong_password``), damage,
    unsafe archives, zip bombs and missing space.
    """
    path = Path(src_path)
    kind = kind or detect_kind(path)
    prog = prog or Progress(ctx, label=ctx.t("msg.arc.extracting"))
    stats = ExtractStats()
    if kind.split and not (kind.kind in ("7z", "rar") and tools.have("7z")):
        parts = _split_parts(path)
        joined = ctx.tmpdir() / f"joined-{secrets.token_hex(3)}"
        _join_parts(ctx, parts, joined, prog.sub(0.0, 0.3))
        prog = prog.sub(0.3, 1.0)
        path = joined
        inner = detect_kind(joined, util.split_name(parts[0].name[: -4])[1] if len(parts[0].name) > 4 else "")
        kind = Kind(inner.kind, inner.codec, False)
    if kind.kind == "single":
        raise UserError("err.arc.single_stream")
    src_size = path.stat().st_size
    entries: list[Entry] | None = None
    if kind.kind == "zip":
        entries = _list_zip(path)
    elif kind.kind in ("7z", "rar", "other") and tools.have("7z"):
        entries = _list_7z(ctx, path, password)
    if entries is not None:
        stats.encrypted = any(e.encrypted for e in entries)
        if stats.encrypted and not password:
            raise UserError("err.arc.password_needed")
        bad = [e.name for e in entries if _safe_rel(e.name) is None and e.name.strip("/.")]
        if bad and kind.kind != "zip":
            raise UserError("err.arc.unsafe", details="\n".join(bad[:50]), n=len(bad))
        total = sum(e.size for e in entries if not e.is_dir)
        _bomb_check(ctx, total, src_size, work, force)
        cap: int | None = int(total * 1.05) + 16 * _MB
    else:
        cap = None if force else max(src_size * 200, 2000 * _MB)
    guard = _Guard(ctx, work, cap)
    p = str(path)
    if kind.kind == "zip":
        try:
            _extract_zip_py(ctx, p, work, password, guard, prog, stats)
        except _NeedExternal:
            if not tools.have("7z"):
                raise MissingTool("7z") from None
            _extract_7z(ctx, p, work, password, guard, prog)
    elif kind.kind == "tar" and _codec_available(kind.codec):
        _extract_tar_py(ctx, p, kind.codec, work, guard, prog, stats)
    elif tools.have("7z"):
        _extract_7z(ctx, p, work, password, guard, prog)
    elif kind.kind == "rar" and tools.have("unrar") and not password:
        _extract_unrar(ctx, p, work, guard, prog)
    elif tools.have("bsdtar") and not password:
        _extract_bsdtar(ctx, p, work, guard, prog)
    else:
        raise MissingTool("7z")
    counted = ExtractStats()
    _sanitize_tree(work, counted)
    stats.files, stats.dirs = counted.files, counted.dirs
    stats.links += counted.links
    stats.special += counted.special
    return stats


def _single_name(src: FileInfo) -> str:
    stem = src.stem or src.name
    return stem if stem != src.name else stem + ".out"


def _place(ctx: Context, src: FileInfo, work: Path, smart: bool, stats: ExtractStats) -> Path:
    target_dir = work.parent
    entries = sorted(work.iterdir(), key=lambda e: e.name)
    if not entries:
        raise UserError("err.arc.empty")
    if smart and len(entries) == 1 and not entries[0].is_symlink() and (entries[0].is_dir() or entries[0].is_file()):
        entry = entries[0]
        is_dir = entry.is_dir()
        if is_dir:
            # this folder becomes the root of the result: "proj/up -> .." was inside ``work``, but
            # after the move it would point at the user's folder (and "../x" at a sibling)
            moved = ExtractStats()
            _sanitize_tree(entry, moved)
            stats.links += moved.links
            stats.files = max(0, stats.files - moved.links)
        cand = util.unique_path(target_dir / entry.name, is_dir=is_dir)
        final = ctx.output_path(src, name=cand.name, directory=target_dir)
        if os.path.lexists(final):
            final = util.unique_path(final, is_dir=is_dir)
        os.rename(entry, final)
        try:
            work.rmdir()
        except OSError:
            pass
        return final
    name = src.stem or src.name
    if name == src.name and src.ext:
        name = util.split_name(src.name)[0]
    cand = util.unique_path(target_dir / util.safe_filename(name), is_dir=True)
    final = ctx.output_path(src, name=cand.name, directory=target_dir)
    if os.path.lexists(final):
        final = util.unique_path(final, is_dir=True)
    os.rename(work, final)
    return final


def extract_archive(ctx: Context, src: FileInfo, *, dest_dir: Path | None = None, password: str = "",
                    force: bool = False, smart: bool = True,
                    prog: Progress | None = None) -> tuple[list[Path], ExtractStats]:
    """Extract ``src`` next to it (or into ``dest_dir``): a single top-level folder or file is
    extracted directly, several items go into ``"<stem>/"``; single-stream files (``.gz``, ``.xz``…)
    become ``"<stem>"``.  Returns ``(outputs, stats)``."""
    path = Path(src.path)
    kind = detect_kind(path, src.ext)
    prog = prog or Progress(ctx, label=ctx.t("msg.arc.extracting"))
    if kind.kind == "single" and not kind.split:
        out = ctx.output_path(src, name=_single_name(src), directory=dest_dir)
        size = path.stat().st_size
        cap = None if force else max(size * 200, 2000 * _MB)
        guard = _Guard(ctx, out.parent, cap)
        with atomic_target(out) as part:
            _decompress_single(ctx, str(path), kind.codec, part, guard, prog)
        stats = ExtractStats(files=1)
        return [out], stats
    token = secrets.token_hex(3)
    work = ctx.output_path(src, name=f".{util.safe_filename(src.stem or 'archive')}.extracting-{token}",
                           directory=dest_dir)
    work.mkdir(parents=True, exist_ok=False)
    try:
        stats = extract_raw(ctx, path, work, password=password, force=force, prog=prog, kind=kind)
        final = _place(ctx, src, work, smart, stats)
    finally:
        if work.exists():
            import shutil

            shutil.rmtree(work, ignore_errors=True)
    return [final], stats


def _warn_stats(ctx: Context, stats: ExtractStats) -> None:
    if stats.unsafe:
        ctx.warn(i18n.tn("warn.arc.unsafe_skipped", stats.unsafe, ctx.lang))
    if stats.links:
        ctx.warn(i18n.tn("warn.arc.links_skipped", stats.links, ctx.lang))
    if stats.special:
        ctx.warn(i18n.tn("warn.arc.special_skipped", stats.special, ctx.lang))
    if stats.duplicates:
        ctx.warn(i18n.tn("warn.arc.duplicates", stats.duplicates, ctx.lang))


# ================================================================================== actions

def _is_extractable(files: list[FileInfo]) -> bool:
    return all(f.exists and not f.is_dir for f in files)


def _not_single(files: list[FileInfo]) -> bool:
    return all(f.exists and detect_kind(f.path, f.ext).kind != "single" for f in files)


def _extract_params(with_dest: bool) -> list:
    params: list = []
    if with_dest:
        params.append(P.dir("dest", "prm.arc.dest", default=lambda files: os.path.dirname(files[0].path) if files else "",
                            required=True))
    params += [
        P.password("password", "prm.arc.password_extract", default=""),
        P.bool("force", "prm.arc.force", False, help="prm.arc.force.help"),
    ]
    if with_dest:
        params.append(P.bool("smart", "prm.arc.smart", True, help="prm.arc.smart.help"))
    return params


@action(id="archive.extract_here", label="act.arc.extract_here", icon="unarchive", categories={"archive"},
        multi="each", mode="quick", when=_is_extractable, description="desc.arc.extract_here", order=1,
        params=_extract_params(False))
def archive_extract_here(ctx: Context):
    """Extract next to the archive (smart placement, safe, password via Alt)."""
    outs, stats = extract_archive(ctx, ctx.file, password=ctx.pstr("password"), force=ctx.pbool("force"))
    _warn_stats(ctx, stats)
    return ctx.result(outs, message=i18n.tn("msg.arc.extracted", stats.files, ctx.lang))


@action(id="archive.extract_to", label="act.arc.extract_to", icon="drive_folder_upload", categories={"archive"},
        multi="each", mode="dialog", dialog="form", when=_is_extractable, description="desc.arc.extract_to",
        order=2, params=_extract_params(True))
def archive_extract_to(ctx: Context):
    """Extract into a chosen folder."""
    raw = ctx.pstr("dest").strip()
    if not raw:
        raise UserError("err.missing_param", param=ctx.t("prm.arc.dest"))
    dest = Path(os.path.expanduser(raw))
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserError("err.not_writable", details=str(exc), dir=str(dest)) from None
    if not os.access(dest, os.W_OK | os.X_OK):
        raise UserError("err.not_writable", dir=str(dest))
    outs, stats = extract_archive(ctx, ctx.file, dest_dir=dest, password=ctx.pstr("password"),
                                  force=ctx.pbool("force"), smart=ctx.pbool("smart", True))
    _warn_stats(ctx, stats)
    return ctx.result(outs, message=i18n.tn("msg.arc.extracted", stats.files, ctx.lang))


@action(id="archive.list", label="act.arc.list", icon="list", categories={"archive"}, multi="single",
        when=_is_extractable, undoable=False, description="desc.arc.list", order=3,
        params=[P.password("password", "prm.arc.password_extract", default="")])
def archive_list(ctx: Context):
    """Show the contents as a tree with sizes (text result)."""
    f = ctx.file
    kind = detect_kind(f.path, f.ext)
    prog = Progress(ctx, label=ctx.t("act.arc.list"))
    entries = list_entries(ctx, f.path, kind, ctx.pstr("password"), prog)
    root = TreeNode("")
    mark = ctx.t("info.arc.encrypted_mark")
    for e in entries:
        parts = [p for p in e.name.replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts:
            continue
        extra = ""
        if e.is_link and e.link:
            extra = f"→ {e.link}"
        if e.encrypted:
            extra = (extra + " " + mark).strip()
        root.add_path(parts, None if e.is_dir else e.size, e.is_dir, extra)
    files, folders = root.count()
    total = root.total()
    header = ctx.t("msg.arc.list_header", name=ctx.original(f).name, files=files, folders=folders,
                   size=util.human_size(total, ctx.lang), packed=util.human_size(f.size, ctx.lang))
    lines = render_tree(root, ctx.lang, more_text=ctx.t("msg.arc.list_more"))
    return ctx.result(kind="text", text=header + "\n\n" + "\n".join(lines),
                      message=i18n.tn("msg.arc.list_summary", files, ctx.lang, size=util.human_size(total, ctx.lang)))


def _test_zip(ctx: Context, path: str, password: str, prog: Progress) -> int:
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if any(zi.compress_type not in _PY_ZIP_METHODS for zi in infos):
            raise _NeedExternal()
        if any(zi.flag_bits & 0x1 for zi in infos) and not password:
            raise UserError("err.arc.password_needed")
        pwd = password.encode("utf-8") if password else None
        total = sum(zi.file_size for zi in infos) or 1
        done = 0
        count = 0
        for zi in infos:
            if zi.is_dir():
                continue
            with zf.open(zi, pwd=pwd) as fh:
                while True:
                    ctx.check_cancel()
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    prog.frac(done / total, zi.filename)
            count += 1
        return count


def _test_tar(ctx: Context, path: str, codec: str, prog: Progress) -> int:
    src = _Source(ctx, path, codec)
    count = 0
    try:
        with tarfile.open(fileobj=src.stream, mode="r|") as tf:
            for m in tf:
                ctx.check_cancel()
                prog.frac(src.fraction(), m.name)
                if m.isreg():
                    fh = tf.extractfile(m)
                    if fh is not None:
                        while fh.read(_CHUNK):
                            ctx.check_cancel()
                count += 1
        src.finish()
    finally:
        src.close()
    return count


def _test_single(ctx: Context, path: str, codec: str, prog: Progress) -> int:
    src = _Source(ctx, path, codec)
    try:
        while src.stream.read(_CHUNK):
            ctx.check_cancel()
            prog.frac(src.fraction())
        src.finish()
    finally:
        src.close()
    return 1


@action(id="archive.test", label="act.arc.test", icon="fact_check", categories={"archive"}, multi="each",
        mode="quick", when=_is_extractable, undoable=False, description="desc.arc.test", order=5,
        params=[P.password("password", "prm.arc.password_extract", default="")])
def archive_test(ctx: Context):
    """Read every member and check the CRCs without writing anything."""
    f = ctx.file
    kind = detect_kind(f.path, f.ext)
    password = ctx.pstr("password")
    prog = Progress(ctx, label=ctx.t("act.arc.test"))
    try:
        if kind.kind == "zip" and not kind.split:
            try:
                count = _test_zip(ctx, f.path, password, prog)
            except _NeedExternal:
                count = _test_7z(ctx, f.path, password, prog)
        elif kind.kind == "tar" and _codec_available(kind.codec) and not kind.split:
            count = _test_tar(ctx, f.path, kind.codec, prog)
        elif kind.kind == "single" and _codec_available(kind.codec) and not kind.split:
            count = _test_single(ctx, f.path, kind.codec, prog)
        else:
            count = _test_7z(ctx, f.path, password, prog)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "bad password" in msg:
            raise UserError("err.wrong_password") from None
        if "password required" in msg:
            raise UserError("err.arc.password_needed") from None
        raise UserError("err.arc.test_failed", details=str(exc)) from None
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, zlib.error, lzma.LZMAError, OSError) as exc:
        raise UserError("err.arc.test_failed", details=str(exc)) from None
    g = "info.grp.archive"
    rows = [
        {"group": ctx.t(g), "key": ctx.t("info.file.result"), "value": ctx.t("msg.arc.test_ok")},
        {"group": ctx.t(g), "key": ctx.t("info.arc.entries"), "value": str(count)},
        {"group": ctx.t(g), "key": ctx.t("info.arc.format"), "value": format_of(f).upper()},
        {"group": ctx.t(g), "key": ctx.t("info.file.size"), "value": util.human_size(f.size, ctx.lang)},
    ]
    return ctx.result(kind="info", info=rows, message=ctx.t("msg.arc.test_ok"))


def _test_7z(ctx: Context, path: str, password: str, prog: Progress) -> int:
    if not tools.have("7z"):
        raise MissingTool("7z")
    argv = ["7z", "t", "-bsp1", "-bso0", "-bse2", "-spd", "-sccUTF-8"]
    if password:
        argv.append("-p")
    argv += ["--", path]
    try:
        run_7z(ctx, argv, password=password, prog=prog, ok_codes=(0,))
    except ToolError as exc:
        mapped = _map_7z_error(exc, password)
        if mapped is exc:
            raise UserError("err.arc.test_failed", details=exc.details) from None
        raise mapped from None
    try:
        return sum(1 for e in _list_7z(ctx, path, password) if not e.is_dir)
    except UserError:
        return 0


# ------------------------------------------------------------------------------- conversion

def _recompress_tar(ctx: Context, path: str, codec: str, part: Path, fmt: str, level: int, prog: Progress) -> None:
    src = _Source(ctx, path, codec)
    sink = _open_sink(fmt, part, level)
    try:
        while True:
            ctx.check_cancel()
            chunk = src.stream.read(_CHUNK)
            if not chunk:
                break
            sink.write(chunk)
            prog.frac(src.fraction())
        src.finish()
        sink.close()
    except BaseException:
        sink.abort()
        raise
    finally:
        src.close()


def _repack(ctx: Context, f: FileInfo, fmt: str, out: Path, *, source_password: str = "", new_password: str = "",
            force: bool = False) -> tuple[list[Path], ExtractStats]:
    work = ctx.tmpdir() / f"repack-{secrets.token_hex(3)}"
    work.mkdir(parents=True)
    stats = extract_raw(ctx, f.path, work, password=source_password, force=force,
                        prog=Progress(ctx, 0.0, 0.5, label=ctx.t("msg.arc.extracting")))
    children = sorted(work.iterdir(), key=lambda e: e.name)
    if not children:
        raise UserError("err.arc.empty")
    outs = create_archive(ctx, children, fmt, out, password=new_password,
                          prog=Progress(ctx, 0.5, 1.0, label=ctx.t("msg.arc.compressing")))
    return outs, stats


def _convert(ctx: Context, fmt: str):
    f = ctx.file
    kind = detect_kind(f.path, f.ext)
    out = ctx.output_path(f, ext=fmt)
    if kind.kind == "tar" and fmt.startswith("tar.") and _codec_available(kind.codec) and not kind.split:
        prog = Progress(ctx, label=ctx.t("msg.arc.compressing"))
        with atomic_target(out) as part:
            _recompress_tar(ctx, f.path, kind.codec, part, fmt, _LEVELS[fmt]["normal"], prog)
        return ctx.result([out], size_before=f.size)
    outs, stats = _repack(ctx, f, fmt, out, source_password=ctx.pstr("password"), force=ctx.pbool("force"))
    _warn_stats(ctx, stats)
    if stats.encrypted:
        ctx.warn(ctx.t("warn.arc.password_dropped"))
    return ctx.result(outs, size_before=f.size)


def _not_format(fmt: str) -> Callable[[list[FileInfo]], bool]:
    def pred(files: list[FileInfo]) -> bool:
        return all(f.exists and not f.is_dir and format_of(f) not in (fmt, "single") for f in files)

    return pred


def _register_convert(fmt: str, order: int) -> None:
    key = fmt.replace(".", "_")

    def fn(ctx: Context):
        return _convert(ctx, fmt)

    fn.__name__ = f"archive_convert_{key}"
    fn.__doc__ = f"Repack the archive as {FORMAT_TEXT[fmt]} (tar → tar only recompresses the stream)."
    requires: list = ["7z"] if fmt == "7z" else (list(_ZSTD_REQ) if fmt == "tar.zst" else [])
    action(id=f"archive.convert.{key}", label=FORMAT_TEXT[fmt], text=FORMAT_TEXT[fmt], categories={"archive"},
           multi="each", when=_not_format(fmt), requires=requires, description="desc.arc.convert", order=order,
           params=[P.password("password", "prm.arc.password_extract", default=""),
                   P.bool("force", "prm.arc.force", False, help="prm.arc.force.help")])(fn)


for _i, _fmt in enumerate(CREATE_FORMATS):
    _register_convert(_fmt, _i + 1)


# ------------------------------------------------------------------------------- protection

def _protect_params() -> list:
    return [
        P.password("password", "prm.arc.new_password", default="", required=True),
        P.password("confirm", "prm.file.confirm", default=""),
        P.password("old_password", "prm.arc.old_password", default=""),
    ]


def _protect(ctx: Context, fmt: str):
    password = ctx.pstr("password")
    if not password:
        raise UserError("err.missing_param", param=ctx.t("prm.arc.new_password"))
    if ctx.pstr("confirm") and ctx.pstr("confirm") != password:
        raise UserError("err.file.password_mismatch")
    f = ctx.file
    out = ctx.output_path(f, ext=fmt, suffix="sfx.protected")
    outs, stats = _repack(ctx, f, fmt, out, source_password=ctx.pstr("old_password"), new_password=password)
    _warn_stats(ctx, stats)
    if fmt == "zip":
        ctx.warn(ctx.t("warn.arc.zip_aes"))
    return ctx.result(outs, message=ctx.t("msg.arc.protected"))


@action(id="archive.protect.7z", label="act.arc.protect_7z", icon="lock", categories={"archive"}, multi="each",
        mode="dialog", dialog="form", requires=["7z"], when=_is_extractable, description="desc.arc.protect_7z",
        order=1, params=_protect_params())
def archive_protect_7z(ctx: Context):
    """Repack as 7z with AES-256 and encrypted file names (``-mhe=on``)."""
    return _protect(ctx, "7z")


@action(id="archive.protect.zip", label="act.arc.protect_zip", icon="lock", categories={"archive"}, multi="each",
        mode="dialog", dialog="form", requires=["7z"], when=_is_extractable, description="desc.arc.protect_zip",
        order=2, params=_protect_params())
def archive_protect_zip(ctx: Context):
    """Repack as ZIP with AES-256 (names stay visible; not every unzip tool supports AES)."""
    return _protect(ctx, "zip")


def _encrypted_zip_or_7z(files: list[FileInfo]) -> bool:
    for f in files:
        if not f.exists or f.is_dir:
            return False
        fmt = format_of(f)
        if fmt == "7z":
            continue
        if fmt != "zip":
            return False
        try:
            with zipfile.ZipFile(f.path) as zf:
                if not any(zi.flag_bits & 0x1 or zi.compress_type == 99 for zi in zf.infolist()):
                    return False
        except (zipfile.BadZipFile, OSError, ValueError):
            return False
    return True


@action(id="archive.protect.remove", label="act.arc.unprotect", icon="lock_open", categories={"archive"},
        multi="each", mode="dialog", dialog="form", when=_encrypted_zip_or_7z, description="desc.arc.unprotect",
        order=3, params=[P.password("password", "prm.arc.password_extract", default="", required=True)])
def archive_unprotect(ctx: Context):
    """Repack a password-protected ZIP/7z without the password."""
    f = ctx.file
    fmt = format_of(f)
    if fmt == "7z" and not tools.have("7z"):
        raise MissingTool("7z")
    out = ctx.output_path(f, ext=fmt, suffix="sfx.unlocked")
    outs, stats = _repack(ctx, f, fmt, out, source_password=ctx.pstr("password"))
    _warn_stats(ctx, stats)
    return ctx.result(outs, message=ctx.t("msg.arc.unprotected"))


# ------------------------------------------------------------------------------------- join

def _is_first_part(files: list[FileInfo]) -> bool:
    for f in files:
        if f.is_dir or not f.exists or not _SPLIT_RE.search(f.name.lower()):
            return False
        if len(_split_parts(Path(f.path))) < 2:
            return False
    return True


@action(id="archive.join", label="act.arc.join", icon="merge", categories={"archive", "unknown"}, multi="each",
        when=_is_first_part, description="desc.arc.join", order=7)
def archive_join(ctx: Context):
    """Join ``name.001``, ``name.002``… into ``name``."""
    f = ctx.file
    orig = ctx.original(f)
    parts = _split_parts(Path(orig.path))
    if len(parts) < 2:
        raise UserError("err.arc.no_parts")
    m = _SPLIT_RE.search(orig.name)
    base = orig.name[: m.start(0)] if m else orig.stem
    out = ctx.output_path(f, name=base or orig.stem)
    prog = Progress(ctx, label=ctx.t("act.arc.join"))
    with atomic_target(out) as part:
        os.unlink(part) if os.path.exists(part) else None
        _join_parts(ctx, parts, part, prog)
    return ctx.result([out], message=i18n.tn("msg.arc.joined", len(parts), ctx.lang))


# ------------------------------------------------------------------------------------ split

#: preset id -> part size in MB (decimal, like 7-Zip's ``-v100m``); 4 GB stays under the FAT32 limit
SPLIT_PRESETS: dict[str, int] = {"100": 100, "1000": 1000, "4000": 4000}
_SPLIT_MAX_PARTS = 9999
_FAT32_MAX = 4 * 1024 ** 3 - 1


def split_names(name: str, count: int) -> list[str]:
    """``["x.zip.001", "x.zip.002", …]`` (at least three digits, more when ``count`` needs them)."""
    width = max(3, len(str(count)))
    return [f"{name}.{i:0{width}d}" for i in range(1, count + 1)]


def split_file(ctx: Context, f: FileInfo, part_size: int) -> list[Path]:
    """Copy ``f`` into numbered parts of ``part_size`` bytes (the input is left untouched).

    The parts go next to the source when every name of the series is free, otherwise into a
    ``"<name> (diviso)"`` folder, so a series is never mixed with older parts.  Joinable with
    :func:`archive_join`, ``cat`` or ``7z x name.001``.
    """
    src = Path(f.path)
    total = src.stat().st_size
    if part_size <= 0 or total <= part_size:
        raise UserError("err.arc.split_small", size=util.human_size(part_size, ctx.lang))
    count = (total + part_size - 1) // part_size
    if count > _SPLIT_MAX_PARTS:
        raise UserError("err.arc.split_too_many", n=count)
    orig = ctx.original(f)
    names = split_names(util.safe_filename(orig.name), count)
    first = ctx.output_path(f, name=names[0])
    directory = first.parent
    if first.name != names[0] or any(os.path.lexists(directory / n) for n in names[1:]):
        directory = ctx.output_dir(f, suffix="sfx.split")
        first = ctx.output_path(f, name=names[0], directory=directory)
    targets = [first] + [ctx.output_path(f, name=n, directory=directory) for n in names[1:]]
    if any(t.name != n for t, n in zip(targets, names)):
        raise UserError("err.no_free_name", name=names[0])
    free = util.free_space(directory)
    if free is not None and free - total < _SPACE_MARGIN:
        raise UserError("err.disk_full")
    prog = Progress(ctx, total=total, label=ctx.t("act.arc.split"))
    done: list[Path] = []
    with open(src, "rb") as fh:
        for target in targets:
            with atomic_target(target) as part, open(part, "wb") as out:
                left = part_size
                while left > 0:
                    ctx.check_cancel()
                    data = fh.read(min(_CHUNK, left))
                    if not data:
                        break
                    out.write(data)
                    left -= len(data)
                    prog.add(len(data), target.name)
            done.append(target)
    return done


def _bigger_than(size: int) -> Callable[[list[FileInfo]], bool]:
    def pred(files: list[FileInfo]) -> bool:
        return all(f.exists and not f.is_dir and f.size > size for f in files)

    return pred


def _split_result(ctx: Context, parts: list[Path], part_size: int):
    if part_size > _FAT32_MAX:
        ctx.warn(ctx.t("warn.arc.split_fat32"))
    return ctx.result(parts, message=i18n.tn("msg.arc.split", len(parts), ctx.lang,
                                             size=util.human_size(part_size, ctx.lang)))


def _register_split(key: str, mb: int, order: int) -> None:
    size = mb * _MB

    def fn(ctx: Context):
        return _split_result(ctx, split_file(ctx, ctx.file, size), size)

    fn.__name__ = f"archive_split_{key}"
    fn.__doc__ = f"Split the file into parts of {mb} MB (.001, .002…)."
    label = f"opt.arc.split.{key}"
    action(id=f"archive.split.{key}", label=label, icon="call_split", categories={"archive", "unknown", "disk"},
           multi="each", when=_bigger_than(size), description="desc.arc.split", order=order)(fn)


for _i, (_key, _mb) in enumerate(SPLIT_PRESETS.items()):
    _register_split(_key, _mb, _i + 1)


@action(id="archive.split.custom", label="act.arc.split_custom", icon="tune", categories={"archive", "unknown", "disk"},
        multi="each", mode="dialog", dialog="form", when=_bigger_than(_MB), description="desc.arc.split", order=9,
        params=[P.int("size_mb", "prm.arc.part_size", 50, min=1, max=1_000_000, unit="MB")])
def archive_split_custom(ctx: Context):
    """Split the file into parts of a chosen size in MB."""
    size = max(1, ctx.pint("size_mb", 50)) * _MB
    return _split_result(ctx, split_file(ctx, ctx.file, size), size)


# =================================================================================== layout

group("archive.convert", "grp.convert", "swap_horiz",
      [f"archive.convert.{fmt.replace('.', '_')}" for fmt in CREATE_FORMATS],
      default=["archive.convert.zip", "archive.convert.7z"])
group("archive.protect", "grp.protect", "lock",
      ["archive.protect.7z", "archive.protect.zip", "archive.protect.remove"], default="archive.protect.7z")
# Split/Join: "Join parts" is the default when the file is the first part of a series
group("archive.split", "grp.split", "call_split",
      ["archive.join", "archive.split.100", "archive.split.1000", "archive.split.4000", "archive.split.custom"],
      default=["archive.join", "archive.split.100", "archive.split.custom"])

layout("archive", ["archive.extract_here", "archive.extract_to", "archive.list", "archive.convert", "archive.test",
                   "archive.protect", "archive.split"])
layout("multi:archive", ["archive.extract_here", "archive.extract_to", "archive.convert", "file.zip", "archive.test",
                         "archive.split"])
