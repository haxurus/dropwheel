"""Previews, thumbnails, PDF/video/audio inspection and metadata (SPEC §5.5).

Every preview is computed on demand and cached under ``$XDG_CACHE_HOME/dropwheel/previews``:
the cache key is ``sha1(path + mtime + size + kind + options)``, so a changed file never
returns a stale image.  A result is stored as ``<key>.json`` next to the PNG files it
references (``<key>.png``, ``<key>-3.png``…); a cache hit is only used when every referenced
PNG still exists.

Entry points:

* :func:`preview` — the ``preview`` RPC (``kind`` + params, raises :class:`UserError`);
* :func:`thumb_path` — fast thumbnail for the wheel hub and job results; never raises,
  returns ``None`` when no thumbnail can be made;
* :func:`prune_cache` — size/age based cleanup (run by the daemon in the background).

Strategy per kind (optional tools/modules are always imported lazily):

* raster images: Pillow (``draft`` for JPEG, EXIF transpose, first frame), then ``vips``,
  then ImageMagick; RAW files use their embedded preview (exiftool) with the RAW's orientation
  and the size of the full decode, or ``vips``/ImageMagick without one;
* SVG: ``rsvg-convert`` → ``resvg`` → ImageMagick;
* PDF: PyMuPDF (``import pymupdf``) → ``pdftoppm`` → ``mutool``; search with
  ``page.search_for`` (case variants for non-ASCII) or ``pdftotext -bbox``;
* video/audio: ``ffprobe``/``ffmpeg`` (frame at 10 %, ``showwavespic`` waveform);
* metadata: ``exiftool -j -G1 -a -s`` → Pillow EXIF / PyMuPDF / mutagen / ffprobe;
* text: BOM, UTF-8, ``charset_normalizer`` when installed, else cp1252/latin-1.

Thumbnails of files that the desktop already thumbnailed (freedesktop thumbnail cache,
``~/.cache/thumbnails``) are reused when their ``Thumb::MTime`` matches.

PyMuPDF is not thread safe, so every PyMuPDF call made here is serialised with
:data:`PYMUPDF_LOCK`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import signal
import struct
import subprocess
import tempfile
import threading
import time
import unicodedata
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from . import tools, util
from .detect import FileInfo, classify
from .errors import MissingTool, ToolError, UserError

log = logging.getLogger("dropwheel.preview")

__all__ = [
    "KINDS", "PYMUPDF_LOCK", "preview", "thumb_path", "thumb", "image", "pdf_info", "pdf_page", "pdf_search",
    "video_info", "video_frames", "audio_waveform", "metadata", "text", "cache_root", "prune_cache",
]

#: Bump to invalidate every cached preview after a format change.
CACHE_VERSION = 4

#: Supported preview kinds.
KINDS = ("thumb", "image", "pdf_info", "pdf_page", "pdf_search", "video_info", "video_frames",
         "audio_waveform", "metadata", "text")

#: Serialises PyMuPDF usage inside this module (PyMuPDF is not thread safe).
PYMUPDF_LOCK = threading.RLock()

#: Default cache limits (see :func:`prune_cache`).
CACHE_MAX_BYTES = 512 * 1024 * 1024
CACHE_MAX_AGE = 30 * 86400

_FFMPEG_BASE = ("-hide_banner", "-nostdin", "-loglevel", "error")
_RAW_EXTS = {"cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "dng", "raf", "orf", "rw2", "pef", "srw", "x3f",
             "3fr", "iiq", "rwl", "erf", "kdc", "mrw", "mos", "dcr"}
_EXIF_WRITABLE = {"jpg", "jpeg", "jpe", "jfif", "tif", "tiff", "png", "webp", "heic", "heif", "hif", "avif", "jxl",
                  "dng", "cr2", "cr3", "nef", "nrw", "arw", "orf", "rw2", "raf", "pef", "srw", "psd", "gif", "pdf",
                  "eps", "ai", "jp2"}
_EDITABLE_GROUPS = {"IFD0", "ExifIFD", "GPS", "IPTC", "PDF", "ItemList", "Keys", "UserData", "PNG", "Photoshop"}
_SKIP_GROUPS = {"ExifTool"}
_GROUP_ORDER = ("File", "System", "Composite", "IFD0", "ExifIFD", "GPS", "IPTC", "XMP", "PDF", "PNG", "QuickTime",
                "ItemList", "Keys", "UserData", "Track", "ID3", "Vorbis", "FLAC")
_FREEDESKTOP_SIZES = (("normal", 128), ("large", 256), ("x-large", 512), ("xx-large", 1024))
#: Tags offered even when the file has no value for them yet ("audio.tags", "video.metadata.edit").
_MEDIA_TAG_SLOTS = {"audio": ("title", "artist", "album", "albumartist", "date", "tracknumber", "genre"),
                    "video": ("title", "artist", "album", "date", "genre", "comment")}
#: exiftool groups holding the tags that the media actions rewrite (with mutagen or ffmpeg, not exiftool).
_MEDIA_TAG_GROUP_RE = re.compile(r"^(ID3|Vorbis|FLAC|APE|RIFF|AIFF|ASF|Matroska|ItemList|Keys|UserData|QuickTime)")
#: ffprobe tags that ffmpeg itself writes on every remux: never offered for editing.
_FF_INTERNAL_TAGS = {"major_brand", "minor_version", "compatible_brands", "encoder", "handler_name", "vendor_id"}
#: mutagen types whose ``easy=True`` keys are not the canonical tag names: their tags are listed
#: from ffprobe, under the names both writers understand.
_MUTAGEN_RAW_TYPES = {"WAVE", "AIFF", "DSF", "DSDIFF", "ASF"}


# =============================================================================== small helpers

def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(lo, min(hi, number))


def _err_tool(name: str) -> str:
    return tools.display_name(name)


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix" and hasattr(os, "killpg"):
            os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _run(argv: list[Any], *, timeout: float, input: bytes | None = None,
         ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    """Run a helper program (argv[0] is a :mod:`dropwheel.tools` name).

    Raises :class:`MissingTool`, or :class:`ToolError` on timeout / failure (stderr tail in
    ``details``).  The child gets its own session and is killed with its group on timeout.
    """
    name = str(argv[0])
    exe = tools.path(name)
    if exe is None:
        raise MissingTool(name)
    args = [exe] + [os.fspath(a) if isinstance(a, os.PathLike) else str(a) for a in argv[1:]]
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, **kwargs)
    except FileNotFoundError:
        raise MissingTool(name) from None
    except OSError as exc:
        raise ToolError("err.tool_failed", details=str(exc), tool=_err_tool(name), code="?") from None
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise ToolError("err.timeout", tool=_err_tool(name), seconds=int(timeout)) from None
    if proc.returncode not in ok_codes:
        tail = [ln for ln in err.decode("utf-8", "replace").splitlines() if ln.strip()][-15:]
        raise ToolError("err.tool_failed", details="\n".join(tail), tool=_err_tool(name), code=proc.returncode)
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


def _png_size(path: str | os.PathLike) -> tuple[int, int] | None:
    """Width/height from a PNG header (no decoding)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    return None


def _resolve_path(value: Any) -> str:
    """Absolute local path from a path or ``file://`` URI (raises ``UserError`` when unusable)."""
    if value is None or str(value).strip() == "":
        raise UserError("preview.err.no_path")
    text = str(value)
    if text.lower().startswith("file:"):
        text = util.uri_to_path(text)
    elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]+://", text):
        mapped = util.gvfs_path(text)
        if mapped is None:
            raise UserError("preview.err.not_local", name=text)
        text = mapped
    path = os.path.abspath(os.path.expanduser(text))
    if not os.path.exists(path):
        raise UserError("err.file_not_found", name=os.path.basename(path) or path)
    if os.path.isdir(path):
        raise UserError("preview.err.is_dir", name=os.path.basename(path.rstrip("/\\")) or path)
    if not os.access(path, os.R_OK):
        raise UserError("err.not_readable", name=os.path.basename(path))
    return path


# ===================================================================================== cache

def cache_root() -> Path:
    """``$XDG_CACHE_HOME/dropwheel/previews``."""
    return util.cache_dir() / "previews"


def _cache_key(path: str, st: os.stat_result, kind: str, opts: dict) -> str:
    raw = json.dumps([CACHE_VERSION, kind, path, st.st_mtime_ns, st.st_size, opts], sort_keys=True,
                     ensure_ascii=True, default=str)
    return hashlib.sha1(raw.encode("ascii")).hexdigest()


def _cache_base(key: str) -> Path:
    directory = cache_root() / key[:2]
    directory.mkdir(parents=True, exist_ok=True)
    return directory / key


def _pngs_in(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "png" and isinstance(v, str):
                yield v
            else:
                yield from _pngs_in(v)
    elif isinstance(value, list):
        for item in value:
            yield from _pngs_in(item)


def _cache_get(key: str) -> dict | None:
    meta = cache_root() / key[:2] / f"{key}.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "result" not in data:
        return None
    result = data["result"]
    if not all(os.path.isfile(p) for p in _pngs_in(result)):
        return None
    now = time.time()
    try:
        os.utime(meta, (now, now))
    except OSError:
        pass
    return result


def _cache_put(key: str, result: Any) -> None:
    base = _cache_base(key)
    meta = base.with_name(f"{key}.json")
    tmp = base.with_name(f".{key}.json.{secrets.token_hex(3)}")
    try:
        tmp.write_text(json.dumps({"version": CACHE_VERSION, "time": time.time(), "result": result},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, meta)
    except (OSError, TypeError, ValueError) as exc:
        log.debug("cannot cache %s: %s", key, exc)
        with contextlib.suppress(OSError):
            tmp.unlink()


_guard = threading.Lock()
_key_locks: dict[str, list] = {}


@contextlib.contextmanager
def _locked(key: str) -> Iterator[None]:
    """Serialise work on one cache key (two requests for the same preview compute it once)."""
    with _guard:
        entry = _key_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    entry[0].acquire()
    try:
        yield
    finally:
        entry[0].release()
        with _guard:
            entry[1] -= 1
            if entry[1] <= 0:
                _key_locks.pop(key, None)


def _cached(path: str, kind: str, opts: dict, compute: Callable[[Path], Any]) -> Any:
    """Return the cached result for ``(path, kind, opts)`` or compute and store it.

    ``compute(base)`` receives the cache base path (no extension) for its PNG files.
    ``None`` results are not cached.
    """
    st = os.stat(path)
    key = _cache_key(path, st, kind, opts)
    with _locked(key):
        hit = _cache_get(key)
        if hit is not None:
            return hit
        result = compute(_cache_base(key))
        if result is not None:
            _cache_put(key, result)
        return result


def _tmp_png(final: Path) -> Path:
    return final.with_name(f".{final.stem}.tmp-{secrets.token_hex(3)}.png")


def _commit_png(tmp: Path, final: Path) -> None:
    if not tmp.is_file() or tmp.stat().st_size == 0:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise ToolError("err.no_output", name=final.name)
    os.replace(tmp, final)


def _save_pil(im: Any, final: Path) -> Path:
    """Save a Pillow image as PNG atomically (fast compression: this is a cache)."""
    tmp = _tmp_png(final)
    try:
        im.save(tmp, format="PNG", compress_level=1)
        _commit_png(tmp, final)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return final


def prune_cache(max_bytes: int = CACHE_MAX_BYTES, max_age: float = CACHE_MAX_AGE) -> int:
    """Delete cache entries older than ``max_age`` seconds, then the least recently used ones
    until the cache is below ``max_bytes``.  Returns the number of files removed."""
    root = cache_root()
    entries: list[tuple[float, int, str]] = []
    try:
        subdirs = [d for d in os.scandir(root) if d.is_dir(follow_symlinks=False)]
    except OSError:
        return 0
    for sub in subdirs:
        try:
            for f in os.scandir(sub.path):
                try:
                    st = f.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append((max(st.st_mtime, st.st_atime), st.st_size, f.path))
        except OSError:
            continue
    now = time.time()
    removed = 0
    total = sum(e[1] for e in entries)
    entries.sort()
    for mtime, size, path in entries:
        stale_tmp = os.path.basename(path).startswith(".") and now - mtime > 3600
        if now - mtime > max_age or total > max_bytes or stale_tmp:
            try:
                os.unlink(path)
                removed += 1
                total -= size
            except OSError:
                continue
        else:
            break
    for sub in subdirs:
        with contextlib.suppress(OSError):
            os.rmdir(sub.path)
    return removed


# ============================================================================ freedesktop cache

def _freedesktop_thumb(path: str, size: int) -> str | None:
    """A matching thumbnail from ``~/.cache/thumbnails`` (Thumb::MTime must match the file)."""
    try:
        st = os.stat(path)
        uri = util.path_to_uri(path)
    except (OSError, ValueError):
        return None
    digest = hashlib.md5(uri.encode("utf-8", "surrogateescape")).hexdigest()
    base = util.xdg_cache_home() / "thumbnails"
    for folder, px in _FREEDESKTOP_SIZES:
        if px < min(size, 256):
            continue
        candidate = base / folder / f"{digest}.png"
        if not candidate.is_file():
            continue
        try:
            from PIL import Image

            with Image.open(candidate) as im:
                mtime = im.info.get("Thumb::MTime")
        except Exception:
            continue
        try:
            if mtime is not None and int(float(mtime)) == int(st.st_mtime):
                return str(candidate)
        except (TypeError, ValueError):
            continue
    return None


# ================================================================================ raster images

_plugins_lock = threading.Lock()
_plugins_done = False


def _register_plugins() -> None:
    """Register the optional HEIF/AVIF and JPEG XL Pillow plugins once."""
    global _plugins_done
    if _plugins_done:
        return
    with _plugins_lock:
        if _plugins_done:
            return
        _plugins_done = True
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
        except Exception:
            pass
        try:
            import pillow_jxl  # type: ignore  # noqa: F401  (registers on import)
        except Exception:
            pass


def _exif_orientation(im: Any) -> int:
    try:
        return int(im.getexif().get(0x0112) or 1)
    except Exception:
        return 1


def _open_raster(path: str, target: int) -> tuple[Any, tuple[int, int]]:
    """Open an image with Pillow, reduced towards ``target`` px (JPEG draft), first frame,
    EXIF-transposed, converted to RGB/RGBA.  Returns ``(image, original_size_rotated)``."""
    from PIL import Image, ImageOps

    _register_plugins()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        im = Image.open(path)
        try:
            orig_w, orig_h = im.size
            orientation = _exif_orientation(im)
            if orientation in (5, 6, 7, 8):
                orig_w, orig_h = orig_h, orig_w
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            if im.format == "JPEG" and target > 0:
                im.draft("RGB", (target, target))
            im.load()
            out = ImageOps.exif_transpose(im)
            if out is None:
                out = im
            if out.mode not in ("RGB", "RGBA"):
                has_alpha = out.mode in ("LA", "PA", "La", "RGBa") or (
                    out.mode == "P" and "transparency" in out.info) or "A" in out.getbands()
                if out.mode.startswith("I"):
                    # 16/32-bit greyscale: scale to 8 bit instead of clipping everything to white
                    out = out.convert("I").point(lambda v: v * (1 / 256)).convert("L")
                elif out.mode == "F":
                    out = out.convert("L")
                out = out.convert("RGBA" if has_alpha else "RGB")
            elif out is im:
                out = im.copy()
        finally:
            im.close()
    return out, (int(orig_w), int(orig_h))


def _raster_pil(path: str, box: int, final: Path) -> tuple[int, int, int, int]:
    """Render with Pillow (never upscales); returns ``(png_w, png_h, orig_w, orig_h)``."""
    from PIL import Image

    im, (ow, oh) = _open_raster(path, box)
    try:
        im.thumbnail((box, box), Image.Resampling.LANCZOS, reducing_gap=2.0)
        _save_pil(im, final)
        return im.size[0], im.size[1], ow, oh
    finally:
        im.close()


def _fit_up(w: int, h: int, box: int) -> tuple[int, int]:
    scale = box / float(max(w, h, 1))
    return max(1, int(round(w * scale))), max(1, int(round(h * scale)))


def _raster_vips(path: str, box: int, final: Path) -> tuple[int, int]:
    tmp = _tmp_png(final)
    try:
        _run(["vips", "thumbnail", path, tmp, str(box), "--height", str(box), "--size", "down"], timeout=60)
        _commit_png(tmp, final)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    return _png_size(final) or (0, 0)


def _raster_magick(path: str, box: int, final: Path) -> tuple[int, int]:
    tmp = _tmp_png(final)
    try:
        _run(["magick", f"{path}[0]", "-auto-orient", "-thumbnail", f"{box}x{box}>", f"png:{tmp}"], timeout=60)
        _commit_png(tmp, final)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    return _png_size(final) or (0, 0)


def _raw_embedded(path: str, workdir: Path) -> str | None:
    """Extract the largest embedded JPEG preview of a camera RAW file (exiftool)."""
    if not tools.have("exiftool"):
        return None
    for tag in ("-JpgFromRaw", "-PreviewImage", "-ThumbnailImage"):
        try:
            res = _run(["exiftool", "-b", tag, path], timeout=30)
        except UserError:
            continue
        data = res.stdout or b""
        if len(data) > 1024 and data[:3] == b"\xff\xd8\xff":
            out = workdir.with_name(f".{workdir.name}.raw-{secrets.token_hex(3)}.jpg")
            out.write_bytes(data)
            return str(out)
    return None


def _raw_props(path: str) -> tuple[tuple[int, int] | None, int]:
    """Size of the main image (as stored, not rotated) and EXIF Orientation of a camera RAW."""
    if not tools.have("exiftool"):
        return None, 1
    try:
        res = _run(["exiftool", "-j", "-n", "-q", "-Composite:ImageSize", "-Orientation", path], timeout=30,
                   ok_codes=(0, 1))
        data = json.loads(res.stdout.decode("utf-8", "replace") or "[]")
    except (UserError, ValueError) as exc:
        log.debug("exiftool cannot read %s: %s", path, exc)
        return None, 1
    row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    match = re.fullmatch(r"\s*(\d+)\D+(\d+)\s*", str(row.get("ImageSize") or ""))
    size = (int(match[1]), int(match[2])) if match and int(match[1]) and int(match[2]) else None
    try:
        orientation = int(row.get("Orientation") or 1)
    except (TypeError, ValueError):
        orientation = 1
    return size, orientation if 1 <= orientation <= 8 else 1


def _render_raw_preview(path: str, box: int, final: Path, raw_size: tuple[int, int] | None,
                        orientation: int) -> dict | None:
    """Render a camera RAW from its embedded JPEG; ``None`` when that gives no faithful picture.

    The picture must show what the actions decode, because crop and redaction rectangles are
    drawn on it (``actions.image._decode_cli``): embedded previews usually lack the RAW's
    Orientation, so it is applied to the pixels, and the reported size is the one of the full
    decode unless the embedded preview is the only decoder available."""
    extracted = _raw_embedded(path, final)
    if not extracted:
        return None
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with Image.open(extracted) as im:
                ow, oh = im.size
                im.draft("RGB", (box, box))
                pixels = im.convert("RGB")
        transpose = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                     4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270,
                     7: Image.Transpose.TRANSVERSE, 8: Image.Transpose.ROTATE_90}.get(orientation)
        if transpose is not None:
            pixels = pixels.transpose(transpose)
        turned = orientation in (5, 6, 7, 8)
        if turned:
            ow, oh = oh, ow
        if raw_size and (tools.have("darktable-cli") or tools.have("magick")):
            full_w, full_h = raw_size[::-1] if turned else raw_size
            if abs(full_w / full_h - ow / oh) > 0.05:
                # cropped in the camera (16:9, 1:1…): the preview is not the frame that gets decoded
                return None
            ow, oh = full_w, full_h
        pixels.thumbnail((box, box), Image.Resampling.LANCZOS, reducing_gap=2.0)
        _save_pil(pixels, final)
        return {"png": str(final), "width": pixels.size[0], "height": pixels.size[1], "orig_w": ow, "orig_h": oh}
    except ImportError:
        return None
    except Exception as exc:  # a damaged preview: the real decoders may still read the file
        log.debug("embedded preview of %s is unusable: %s", path, exc)
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(extracted)


def _image_dims_fallback(path: str) -> tuple[int, int]:
    info = classify(path, deep=True)
    return int(info.width or 0), int(info.height or 0)


def _tool_dims(path: str) -> tuple[int, int]:
    """Pixel size read by ImageMagick or libvips, for formats Pillow cannot open; ``(0, 0)`` if unknown."""
    if tools.have("magick"):
        try:
            res = _run(["magick", "identify", "-ping", "-quiet", "-format", "%w %h", f"{path}[0]"], timeout=60)
            match = re.match(r"\s*(\d+) (\d+)", res.stdout.decode("ascii", "replace"))
            if match:
                return int(match[1]), int(match[2])
        except UserError as exc:
            log.debug("magick identify failed for %s: %s", path, exc.details)
    if tools.have("vipsheader"):
        try:
            return (int(_run(["vipsheader", "-f", "width", path], timeout=60).stdout),
                    int(_run(["vipsheader", "-f", "height", path], timeout=60).stdout))
        except (UserError, ValueError) as exc:
            log.debug("vipsheader failed for %s: %s", path, exc)
    return 0, 0


def _render_raster(path: str, ext: str, box: int, final: Path) -> dict:
    """Render a raster image into ``final``; returns ``{png, width, height, orig_w, orig_h}``.

    ``orig_w``/``orig_h`` are the pixels the actions will see, or 0 when they cannot be told (the
    PNG itself is fine as a thumbnail, but rectangles cannot be mapped to the original)."""
    errors: list[str] = []
    raw = ext in _RAW_EXTS
    raw_size: tuple[int, int] | None = None
    if raw:  # never through Pillow first: it opens the TIFF container and finds a tiny thumbnail
        raw_size, orientation = _raw_props(path)
        res = _render_raw_preview(path, box, final, raw_size, orientation)
        if res is not None:
            return res
    else:
        try:
            pw, ph, ow, oh = _raster_pil(path, box, final)
            return {"png": str(final), "width": pw, "height": ph, "orig_w": ow, "orig_h": oh}
        except ImportError:
            errors.append("Pillow missing")
        except Exception as exc:  # unreadable by Pillow (bomb, unknown format…): try external tools
            errors.append(f"Pillow: {exc}")
    for renderer in (_raster_vips, _raster_magick):
        tool = "vips" if renderer is _raster_vips else "magick"
        if not tools.have(tool):
            continue
        try:
            pw, ph = renderer(path, box, final)
        except UserError as exc:
            errors.append(f"{tool}: {exc.text('en')} {exc.details}".strip())
            continue
        ow, oh = raw_size or (0, 0)
        if not (ow and oh) and not raw:
            ow, oh = _image_dims_fallback(path)
        if not (ow and oh):
            ow, oh = _tool_dims(path)
        if (ow > oh) != (pw > ph) and ow != oh and pw != ph:  # size as stored: the PNG is auto-oriented
            ow, oh = oh, ow
        return {"png": str(final), "width": pw, "height": ph, "orig_w": ow, "orig_h": oh}
    if raw:  # good enough for a thumbnail: the small preview of a TIFF-based RAW
        with contextlib.suppress(Exception):
            pw, ph, _ow, _oh = _raster_pil(path, box, final)
            return {"png": str(final), "width": pw, "height": ph, "orig_w": 0, "orig_h": 0}
        if not tools.have("exiftool"):
            raise MissingTool("exiftool")
    if not tools.have("py:PIL") and not tools.have("vips") and not tools.have("magick"):
        raise MissingTool("py:PIL")
    raise UserError("preview.err.unreadable", details="\n".join(errors), name=os.path.basename(path))


# ======================================================================================== SVG

def _svg_render(path: str, info: FileInfo, box: int, final: Path) -> dict:
    """Render an SVG to fit ``box`` (rsvg-convert → resvg → magick)."""
    w, h = info.width or 0, info.height or 0
    if not w or not h:
        full = classify(path, deep=True)
        w, h = full.width or 0, full.height or 0
    tw, th = _fit_up(w, h, box) if w and h else (box, box)
    tmp = _tmp_png(final)
    errors: list[str] = []
    try:
        if tools.have("rsvg-convert"):
            try:
                _run(["rsvg-convert", "-w", str(tw), "-h", str(th), "-a", "-f", "png", "-o", tmp, path], timeout=30)
                _commit_png(tmp, final)
            except UserError as exc:
                errors.append(f"rsvg-convert: {exc.details or exc.text('en')}")
        if not final.is_file() and tools.have("resvg"):
            try:
                size_args = ["-w", str(tw)] if tw >= th else ["-h", str(th)]
                _run(["resvg", *size_args, path, tmp], timeout=30)
                _commit_png(tmp, final)
            except UserError as exc:
                errors.append(f"resvg: {exc.details or exc.text('en')}")
        if not final.is_file() and tools.have("magick"):
            try:
                _run(["magick", "-background", "none", "-density", "192", path, "-resize", f"{box}x{box}",
                      f"png:{tmp}"], timeout=60)
                _commit_png(tmp, final)
            except UserError as exc:
                errors.append(f"magick: {exc.details or exc.text('en')}")
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    if not final.is_file():
        if not any(tools.have(t) for t in ("rsvg-convert", "resvg", "magick")):
            raise MissingTool("rsvg-convert")
        raise UserError("preview.err.unreadable", details="\n".join(errors), name=os.path.basename(path))
    pw, ph = _png_size(final) or (tw, th)
    return {"png": str(final), "width": pw, "height": ph, "orig_w": w or pw, "orig_h": h or ph}


# ======================================================================================== PDF

@contextlib.contextmanager
def _pymupdf_doc(path: str, password: str | None = None) -> Iterator[Any]:
    """Open a PDF with PyMuPDF under :data:`PYMUPDF_LOCK`; raises ``ImportError`` when missing."""
    import pymupdf  # type: ignore

    with PYMUPDF_LOCK:
        with contextlib.suppress(Exception):
            pymupdf.TOOLS.mupdf_display_errors(False)
        try:
            doc = pymupdf.open(path)
        except Exception as exc:
            raise UserError("err.damaged", details=str(exc)) from None
        try:
            if getattr(doc, "needs_pass", False):
                if not doc.authenticate(password or ""):
                    if password:
                        raise UserError("err.wrong_password")
                    raise UserError("err.password_required")
            yield doc
        finally:
            with contextlib.suppress(Exception):
                doc.close()


def _have_pymupdf() -> bool:
    return tools.have("py:pymupdf")


def _decrypt_pdf(path: str, password: str, dest: str) -> None:
    """Write a decrypted copy of ``path`` to ``dest`` (qpdf reads the password from stdin)."""
    if tools.have("qpdf"):
        try:
            _run(["qpdf", "--password-file=-", "--decrypt", path, dest], timeout=120, ok_codes=(0, 3),
                 input=password.encode("utf-8", "surrogatepass"))
            return
        except ToolError as exc:
            if "password" in (exc.details or "").lower():
                raise UserError("err.wrong_password") from None
            if not tools.have("py:pypdf"):
                raise UserError("err.damaged", details=exc.details) from None
    elif not tools.have("py:pypdf"):
        raise MissingTool("qpdf")
    from pypdf import PdfReader, PdfWriter  # type: ignore

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted and not reader.decrypt(password):
            raise UserError("err.wrong_password")
        with open(dest, "wb") as fh:
            PdfWriter(clone_from=reader).write(fh)
    except UserError:
        raise
    except Exception as exc:
        if type(exc).__name__ == "DependencyError":  # AES needs the "cryptography" module
            raise MissingTool("py:cryptography", details=str(exc)) from None
        raise UserError("err.damaged", details=str(exc)) from None


@contextlib.contextmanager
def _unlocked_pdf(path: str, password: str | None) -> Iterator[str]:
    """``path``, or a private decrypted copy of it when a password is given.

    pdfinfo, pdftoppm, pdftotext and mutool only accept a password on their command line, where
    any local process can read it (``/proc/<pid>/cmdline``).  The copy lives in a 0700 folder
    (in ``$XDG_RUNTIME_DIR``, i.e. in memory, when there is one) and is gone on exit."""
    if not password:
        yield path
        return
    runtime = util.xdg_runtime_dir()
    with tempfile.TemporaryDirectory(prefix="dropwheel-pdf-",
                                     dir=str(runtime) if runtime is not None and runtime.is_dir() else None) as folder:
        copy = os.path.join(folder, "unlocked.pdf")
        _decrypt_pdf(path, password, copy)
        yield copy


def _pdfinfo_pages(path: str, password: str | None) -> tuple[int, list[list[float]], bool]:
    """``pdfinfo`` fallback: page count, visible page sizes (points) and encryption flag."""
    res = _run(["pdfinfo", path], timeout=30, ok_codes=(0, 1, 3, 99))
    head = res.stdout.decode("utf-8", "replace")
    err = res.stderr.decode("utf-8", "replace")
    if "password" in err.lower() and res.returncode != 0:
        if not password:
            raise UserError("err.password_required")
        with _unlocked_pdf(path, password) as copy:
            pages, sizes, _encrypted = _pdfinfo_pages(copy, None)
        return pages, sizes, True
    m = re.search(r"^Pages:\s+(\d+)", head, re.M)
    if res.returncode != 0 or not m:
        raise UserError("err.damaged", details=(err or head)[-2000:])
    pages = int(m.group(1))
    encrypted = bool(re.search(r"^Encrypted:\s+yes", head, re.M))
    sizes: list[list[float]] = []
    if pages:
        detail = _run(["pdfinfo", "-f", "1", "-l", str(pages), path], timeout=60,
                      ok_codes=(0, 1, 3, 99)).stdout.decode("utf-8", "replace")
        size_re = re.compile(r"^Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)", re.M)
        rot_re = re.compile(r"^Page\s+(\d+)\s+rot:\s+(\d+)", re.M)
        rots = {int(a): int(b) for a, b in rot_re.findall(detail)}
        for num, w, h in size_re.findall(detail):
            wf, hf = float(w), float(h)
            if rots.get(int(num), 0) % 180 == 90:
                wf, hf = hf, wf
            sizes.append([round(wf, 2), round(hf, 2)])
    return pages, sizes, encrypted


def _pw_key(password: str | None) -> str:
    """Cache-key part for a password: results unlocked with one password must never be served
    for another one (or for none)."""
    if not password:
        return ""
    return hashlib.sha256(("dropwheel-pdf:" + password).encode("utf-8", "surrogatepass")).hexdigest()[:24]


def _locked_info(wrong: bool) -> dict:
    return {"pages": 0, "sizes": [], "encrypted": True, "needs_password": True, "wrong_password": wrong}


def _pdf_info_compute(path: str, password: str | None) -> dict:
    if _have_pymupdf():
        with _pymupdf_doc(path, password) as doc:
            sizes = []
            for page in doc:
                r = page.rect
                sizes.append([round(float(r.width), 2), round(float(r.height), 2)])
            return {"pages": int(doc.page_count), "sizes": sizes, "encrypted": bool(doc.is_encrypted),
                    "needs_password": False}
    if tools.have("pdfinfo"):
        pages, sizes, encrypted = _pdfinfo_pages(path, password)
        return {"pages": pages, "sizes": sizes, "encrypted": encrypted, "needs_password": False}
    if tools.have("py:pypdf"):
        return _pypdf_info(path, password)
    raise MissingTool("py:pymupdf")


def pdf_info(path: str, password: str | None = None) -> dict:
    """``{pages, sizes: [[w_pt, h_pt]…], encrypted, needs_password}`` (sizes as displayed, i.e.
    with the page rotation applied).  A locked PDF gives ``needs_password: true`` (plus
    ``wrong_password`` when a password was given but did not work)."""
    path = _resolve_path(path)
    try:
        return _cached(path, "pdf_info", {"pw": _pw_key(password)}, lambda _base: _pdf_info_compute(path, password))
    except UserError as exc:
        if exc.message in ("err.password_required", "err.wrong_password"):
            return _locked_info(exc.message == "err.wrong_password")
        raise


def _pypdf_info(path: str, password: str | None) -> dict:
    from pypdf import PdfReader  # type: ignore

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(path, strict=False)
    except Exception as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    encrypted = bool(reader.is_encrypted)
    if encrypted:
        try:
            ok = reader.decrypt(password or "")
        except Exception:
            ok = 0
        if not ok:
            return {"pages": 0, "sizes": [], "encrypted": True, "needs_password": True,
                    "wrong_password": bool(password)}
    sizes = []
    for page in reader.pages:
        box = page.cropbox
        w, h = float(box.width), float(box.height)
        if int(page.get("/Rotate", 0) or 0) % 180 == 90:
            w, h = h, w
        sizes.append([round(w, 2), round(h, 2)])
    return {"pages": len(sizes), "sizes": sizes, "encrypted": encrypted, "needs_password": False}


def _pdf_render(path: str, page: int, dpi: float | None, box: int | None, final: Path,
                password: str | None = None) -> dict:
    """Render one page (0-based) by ``dpi`` or to fit ``box`` px; returns png + page size in points."""
    max_pixels = 40_000_000
    if _have_pymupdf():
        import pymupdf  # type: ignore

        with _pymupdf_doc(path, password) as doc:
            if page < 0 or page >= doc.page_count:
                raise UserError("preview.err.bad_page", page=page + 1, pages=doc.page_count)
            pg = doc[page]
            rect = pg.rect
            if box:
                zoom = box / max(float(rect.width), float(rect.height), 1.0)
            else:
                zoom = float(dpi or 110) / 72.0
            area = float(rect.width) * float(rect.height) * zoom * zoom
            if area > max_pixels:
                zoom *= (max_pixels / area) ** 0.5
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=True)
            tmp = _tmp_png(final)
            try:
                pix.save(str(tmp))
                _commit_png(tmp, final)
            finally:
                with contextlib.suppress(OSError):
                    tmp.unlink()
            return {"png": str(final), "width": int(pix.width), "height": int(pix.height),
                    "width_pt": round(float(rect.width), 2), "height_pt": round(float(rect.height), 2),
                    "pages": int(doc.page_count)}
    if not (tools.have("pdftoppm") or tools.have("mutool")):
        raise MissingTool("py:pymupdf")
    info = pdf_info(path, password)
    if info.get("needs_password"):
        raise UserError("err.wrong_password" if password else "err.password_required")
    pages = int(info.get("pages") or 0)
    if page < 0 or page >= pages:
        raise UserError("preview.err.bad_page", page=page + 1, pages=pages)
    sizes = info.get("sizes") or []
    size = sizes[page] if page < len(sizes) else [612.0, 792.0]
    w_pt, h_pt = float(size[0]), float(size[1])
    res = float(dpi or 110)
    if box:
        res = box * 72.0 / max(w_pt, h_pt, 1.0)
    area = (w_pt * res / 72.0) * (h_pt * res / 72.0)
    if area > max_pixels:
        res *= (max_pixels / area) ** 0.5
    res = max(1.0, res)
    tmp = _tmp_png(final)  # ".<key>.tmp-xxxxxx.png"; pdftoppm -singlefile appends ".png" to the prefix
    prefix = tmp.with_suffix("")
    try:
        with _unlocked_pdf(path, password if info.get("encrypted") else None) as src:
            if tools.have("pdftoppm"):
                _run(["pdftoppm", "-f", str(page + 1), "-l", str(page + 1), "-r", f"{res:.2f}", "-png", "-singlefile",
                      src, prefix], timeout=120)
            else:
                _run(["mutool", "draw", "-q", "-r", f"{res:.2f}", "-o", tmp, src, str(page + 1)], timeout=120)
        _commit_png(tmp, final)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    pw, ph = _png_size(final) or (0, 0)
    return {"png": str(final), "width": pw, "height": ph, "width_pt": round(w_pt, 2), "height_pt": round(h_pt, 2),
            "pages": pages}


def pdf_page(path: str, page: int = 0, dpi: float = 110, password: str | None = None) -> dict:
    """Render page ``page`` (0-based) at ``dpi``: ``{png, width_pt, height_pt, width, height}``."""
    path = _resolve_path(path)
    page_i = _clamp_int(page, 0, 0, 1_000_000)
    dpi_i = _clamp_int(dpi, 110, 18, 600)
    opts = {"page": page_i, "dpi": dpi_i, "pw": _pw_key(password)}
    return _cached(path, "pdf_page", opts, lambda base: _pdf_render(path, page_i, dpi_i, None,
                                                                    base.with_suffix(".png"), password))


def _case_variants(query: str) -> list[str]:
    variants = []
    for v in (query, query.lower(), query.upper(), query.capitalize(), query.title(),
              unicodedata.normalize("NFC", query), unicodedata.normalize("NFD", query)):
        if v and v not in variants:
            variants.append(v)
    return variants


def _merge_rects(rects: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for r in rects:
        if any(abs(r[0] - o[0]) < 0.5 and abs(r[1] - o[1]) < 0.5 and abs(r[2] - o[2]) < 0.5 and
               abs(r[3] - o[3]) < 0.5 for o in out):
            continue
        out.append(r)
    out.sort(key=lambda r: (round(r[1]), r[0]))
    return out


def _search_pymupdf(path: str, query: str, password: str | None, limit: int) -> list[dict]:
    import pymupdf  # type: ignore

    # search_for ignores case for ASCII only: non-ASCII queries are searched in several case forms
    variants = _case_variants(query)
    ascii_only = query.isascii()
    # Without TEXT_PRESERVE_LIGATURES a term also matches words set with ligature glyphs (U+FB01 "fi"), as in the
    # redaction itself: the boxes of the Redact dialog must show the same hits.
    flags = pymupdf.TEXT_DEHYPHENATE | pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
    matches = []
    total = 0
    with _pymupdf_doc(path, password) as doc:
        for index, page in enumerate(doc):
            rects: list[list[float]] = []
            textpage = page.get_textpage(flags=flags)
            # search_for() answers in unrotated page space; rendering (pdf_page) and the
            # displayed page size use the rotated space
            rot = page.rotation_matrix if page.rotation else None
            try:
                for variant in (variants[:1] if ascii_only else variants):
                    for r in page.search_for(variant, textpage=textpage):
                        if rot is not None:
                            r = pymupdf.Rect(r) * rot
                        rects.append([round(float(r.x0), 2), round(float(r.y0), 2), round(float(r.x1), 2),
                                      round(float(r.y1), 2)])
            finally:
                del textpage
            rects = _merge_rects(rects)
            if rects:
                matches.append({"page": index, "rects": rects})
                total += len(rects)
                if total >= limit:
                    break
    return matches


_BBOX_PAGE_RE = re.compile(r"<page\b[^>]*>(.*?)</page>", re.S)
_BBOX_WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',
                           re.S)


def _search_pdftotext(path: str, query: str, password: str | None, limit: int) -> list[dict]:
    import html as htmlmod

    with _unlocked_pdf(path, password) as src:
        out = _run(["pdftotext", "-bbox", "-enc", "UTF-8", src, "-"], timeout=180).stdout.decode("utf-8", "replace")
    tokens = [t.casefold() for t in query.split()]
    if not tokens:
        return []
    matches = []
    total = 0
    for index, body in enumerate(_BBOX_PAGE_RE.findall(out)):
        words = [(float(a), float(b), float(c), float(d), htmlmod.unescape(w).casefold())
                 for a, b, c, d, w in _BBOX_WORD_RE.findall(body)]
        rects: list[list[float]] = []
        n = len(tokens)
        for i in range(len(words) - n + 1):
            seq = words[i:i + n]
            if n == 1:
                ok = tokens[0] in seq[0][4]
            else:
                ok = (seq[0][4].endswith(tokens[0]) and seq[-1][4].startswith(tokens[-1])
                      and all(seq[k][4] == tokens[k] for k in range(1, n - 1)))
            if not ok:
                continue
            # one rectangle per line covered by the match
            lines: dict[int, list[float]] = {}
            for x0, y0, x1, y1, _w in seq:
                key = int(round(y1))
                cur = lines.get(key)
                lines[key] = [x0, y0, x1, y1] if cur is None else [min(cur[0], x0), min(cur[1], y0),
                                                                  max(cur[2], x1), max(cur[3], y1)]
            rects.extend([round(v, 2) for v in r] for r in lines.values())
        rects = _merge_rects(rects)
        if rects:
            matches.append({"page": index, "rects": rects})
            total += len(rects)
            if total >= limit:
                break
    return matches


def pdf_search(path: str, query: str, password: str | None = None, limit: int = 5000) -> dict:
    """Find ``query`` in a PDF: ``{matches: [{page, rects: [[x0, y0, x1, y1]…]}]}`` in PDF points
    (top-left origin, page rotation applied — the same space as :func:`pdf_page`)."""
    path = _resolve_path(path)
    query = str(query or "").strip()
    if not query:
        return {"matches": [], "count": 0}
    limit = _clamp_int(limit, 5000, 1, 100000)
    if _have_pymupdf():
        matches = _search_pymupdf(path, query, password, limit)
    elif tools.have("pdftotext"):
        matches = _search_pdftotext(path, query, password, limit)
    else:
        raise MissingTool("py:pymupdf")
    return {"matches": matches, "count": sum(len(m["rects"]) for m in matches)}


# ================================================================================ video / audio

def _ffprobe(path: str) -> dict:
    res = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", f"file:{path}"],
               timeout=30)
    try:
        data = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
    except ValueError as exc:
        raise ToolError("err.probe_failed", details=str(exc), name=os.path.basename(path)) from None
    return data if isinstance(data, dict) else {}


def _to_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return util.parse_time(value)
    return number if number == number and number >= 0 else None


def _rate(value: Any) -> float | None:
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        if "/" in str(value):
            num, den = str(value).split("/", 1)
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else None
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _duration_of(probe: dict) -> float | None:
    fmt = probe.get("format") or {}
    best = _to_float(fmt.get("duration"))
    if best:
        return best
    longest = 0.0
    for stream in probe.get("streams") or []:
        for cand in (stream.get("duration"), (stream.get("tags") or {}).get("DURATION")):
            value = _to_float(cand)
            if value and value > longest:
                longest = value
    return longest or None


def _video_stream(probe: dict) -> dict | None:
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic"):
            return stream
    return None


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def video_info(path: str) -> dict:
    """``{duration, width, height, fps, has_audio, codec, …}`` (display size: rotation applied)."""
    path = _resolve_path(path)

    def compute(_base: Path) -> dict:
        probe = _ffprobe(path)
        video = _video_stream(probe)
        audio = next((s for s in probe.get("streams") or [] if s.get("codec_type") == "audio"), None)
        fmt = probe.get("format") or {}
        width = height = None
        rotation = 0
        fps = None
        codec = None
        vfr = False
        if video:
            width, height = video.get("width"), video.get("height")
            rotation = _rotation(video)
            if rotation % 180 == 90 and width and height:
                width, height = height, width
            avg = _rate(video.get("avg_frame_rate"))
            real = _rate(video.get("r_frame_rate"))
            fps = avg or real
            vfr = bool(avg and real and abs(avg - real) > 0.01)
            codec = video.get("codec_name")
        try:
            bitrate = int(fmt.get("bit_rate")) if fmt.get("bit_rate") not in (None, "N/A") else None
        except (TypeError, ValueError):
            bitrate = None
        subtitles = sum(1 for s in probe.get("streams") or [] if s.get("codec_type") == "subtitle")
        return {
            "duration": _duration_of(probe), "width": width, "height": height, "fps": fps,
            "has_audio": audio is not None, "has_video": video is not None, "codec": codec,
            "audio_codec": audio.get("codec_name") if audio else None,
            "channels": audio.get("channels") if audio else None,
            "sample_rate": _to_float(audio.get("sample_rate")) if audio else None,
            "rotation": rotation, "bitrate": bitrate, "format": fmt.get("format_name"),
            "pix_fmt": video.get("pix_fmt") if video else None, "vfr": vfr,
            "subtitles": subtitles, "size": os.path.getsize(path),
        }

    return _cached(path, "video_info", {}, compute)


def _ffmpeg_frame(path: str, t: float, scale_filter: str, final: Path, timeout: float = 30) -> bool:
    tmp = _tmp_png(final)
    try:
        _run(["ffmpeg", *_FFMPEG_BASE, "-ss", f"{max(0.0, t):.3f}", "-i", f"file:{path}", "-map", "0:v:0",
              "-frames:v", "1", "-vf", scale_filter, "-update", "1", "-y", f"file:{tmp}"], timeout=timeout)
        if tmp.is_file() and tmp.stat().st_size > 0:
            os.replace(tmp, final)
            return True
        return False
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _video_thumb(path: str, box: int, final: Path) -> dict | None:
    probe = _ffprobe(path)
    video = _video_stream(probe)
    scale = f"scale=w={box}:h={box}:force_original_aspect_ratio=decrease"
    if video is None:
        return _cover_art(path, probe, box, final)
    duration = _duration_of(probe) or 0.0
    for t in ((duration * 0.1) if duration else 0.0, 0.0):
        try:
            if _ffmpeg_frame(path, t, scale, final, timeout=20):
                pw, ph = _png_size(final) or (0, 0)
                return {"png": str(final), "width": pw, "height": ph}
        except ToolError:
            if t == 0.0:
                raise
    return None


def _cover_art(path: str, probe: dict, box: int, final: Path) -> dict | None:
    """Embedded cover art of an audio file (attached picture stream)."""
    for index, stream in enumerate(probe.get("streams") or []):
        if stream.get("codec_type") == "video" and (stream.get("disposition") or {}).get("attached_pic"):
            tmp = _tmp_png(final)
            try:
                _run(["ffmpeg", *_FFMPEG_BASE, "-i", f"file:{path}", "-map", f"0:{index}", "-frames:v", "1",
                      "-vf", f"scale=w={box}:h={box}:force_original_aspect_ratio=decrease", "-update", "1", "-y",
                      f"file:{tmp}"], timeout=20)
                _commit_png(tmp, final)
            finally:
                with contextlib.suppress(OSError):
                    tmp.unlink()
            pw, ph = _png_size(final) or (0, 0)
            return {"png": str(final), "width": pw, "height": ph}
    return None


def video_frames(path: str, count: int = 12, height: int = 72) -> dict:
    """``count`` evenly spaced frames scaled to ``height`` px: ``{frames: [{t, png}], duration}``."""
    path = _resolve_path(path)
    count_i = _clamp_int(count, 12, 1, 60)
    height_i = _clamp_int(height, 72, 16, 720)

    def compute(base: Path) -> dict:
        duration = _duration_of(_ffprobe(path))
        if not duration:
            raise UserError("preview.err.no_duration", name=os.path.basename(path))
        times = [round((i + 0.5) * duration / count_i, 3) for i in range(count_i)]
        scale = f"scale=-2:{height_i}"

        def grab(item: tuple[int, float]) -> dict | None:
            i, t = item
            final = base.with_name(f"{base.name}-{i}.png")
            try:
                if _ffmpeg_frame(path, t, scale, final, timeout=30):
                    return {"t": t, "png": str(final)}
            except UserError as exc:
                log.debug("frame %s of %s failed: %s", t, path, exc)
            return None

        with ThreadPoolExecutor(max_workers=min(4, count_i), thread_name_prefix="dropwheel-frames") as pool:
            frames = [f for f in pool.map(grab, enumerate(times)) if f]
        if not frames:
            raise UserError("preview.err.no_frames", name=os.path.basename(path))
        return {"frames": frames, "duration": duration}

    if not tools.have("ffmpeg"):
        raise MissingTool("ffmpeg")
    return _cached(path, "video_frames", {"count": count_i, "height": height_i}, compute)


def _ffmpeg_color(value: Any) -> str:
    """``#RRGGBB``, Qt's ``#AARRGGBB``, ``RRGGBB`` or a named colour → ffmpeg ``0xRRGGBBAA``."""
    text = str(value or "").strip()
    if not text:
        return "0xffffffff"
    hexpart = text[1:] if text.startswith("#") else text[2:] if text.lower().startswith("0x") else text
    if re.fullmatch(r"[0-9A-Fa-f]{6}", hexpart):
        return f"0x{hexpart.lower()}ff"
    if re.fullmatch(r"[0-9A-Fa-f]{8}", hexpart):
        if text.startswith("#"):  # Qt colour strings are #AARRGGBB
            return f"0x{hexpart[2:].lower()}{hexpart[:2].lower()}"
        return f"0x{hexpart.lower()}"
    if re.fullmatch(r"[0-9A-Fa-f]{3}", hexpart):
        return "0x" + "".join(c * 2 for c in hexpart.lower()) + "ff"
    if re.fullmatch(r"[A-Za-z]{3,20}", text):
        return text.lower()
    return "0xffffffff"


def audio_waveform(path: str, width: int = 1200, height: int = 160, color: Any = None) -> dict:
    """Waveform PNG (transparent background, ``color`` lines, default white): ``{png, duration}``."""
    path = _resolve_path(path)
    width_i = _clamp_int(width, 1200, 64, 8000)
    height_i = _clamp_int(height, 160, 16, 2000)
    col = _ffmpeg_color(color)

    def compute(base: Path) -> dict:
        probe = _ffprobe(path)
        if not any(s.get("codec_type") == "audio" for s in probe.get("streams") or []):
            raise UserError("preview.err.no_audio", name=os.path.basename(path))
        final = base.with_suffix(".png")
        tmp = _tmp_png(final)
        graph = (f"[0:a:0]aformat=channel_layouts=mono,"
                 f"showwavespic=s={width_i}x{height_i}:colors={col}:scale=sqrt[w]")
        try:
            _run(["ffmpeg", *_FFMPEG_BASE, "-i", f"file:{path}", "-filter_complex", graph, "-map", "[w]",
                  "-frames:v", "1", "-update", "1", "-y", f"file:{tmp}"], timeout=180)
            _commit_png(tmp, final)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        return {"png": str(final), "duration": _duration_of(probe), "width": width_i, "height": height_i}

    if not tools.have("ffmpeg"):
        raise MissingTool("ffmpeg")
    return _cached(path, "audio_waveform", {"w": width_i, "h": height_i, "c": col}, compute)


# ================================================================================= thumbnails

def _thumb_for(info: FileInfo, size: int) -> dict | None:
    """Thumbnail dict for an existing file (may raise)."""
    path = info.path
    cat = info.category
    heavy = cat in ("video", "audio", "pdf", "svg") or (cat == "image" and info.size > 30 * 1024 * 1024)
    if cat not in ("image", "svg", "pdf", "video", "audio"):
        if cat in ("document", "spreadsheet", "presentation", "ebook", "font", "markdown", "text", "code"):
            existing = _freedesktop_thumb(path, size)
            if existing:
                pw, ph = _png_size(existing) or (size, size)
                return {"png": existing, "width": pw, "height": ph}
        return None
    if heavy and size <= 256:
        existing = _freedesktop_thumb(path, size)
        if existing:
            pw, ph = _png_size(existing) or (size, size)
            return {"png": existing, "width": pw, "height": ph}

    def compute(base: Path) -> dict | None:
        final = base.with_suffix(".png")
        if cat == "image":
            res = _render_raster(path, info.ext, size, final)
            return {"png": res["png"], "width": res["width"], "height": res["height"]}
        if cat == "svg":
            res = _svg_render(path, info, size, final)
            return {"png": res["png"], "width": res["width"], "height": res["height"]}
        if cat == "pdf":
            res = _pdf_render(path, 0, None, size, final)
            return {"png": res["png"], "width": res["width"], "height": res["height"]}
        if cat == "video":
            if not tools.have("ffmpeg") or not tools.have("ffprobe"):
                return None
            return _video_thumb(path, size, final)
        if cat == "audio":
            if not tools.have("ffmpeg") or not tools.have("ffprobe"):
                return None
            return _cover_art(path, _ffprobe(path), size, final)
        return None

    return _cached(path, "thumb", {"size": size}, compute)


def thumb(path: str, size: int = 256) -> dict | None:
    """``{png, width, height}`` for images, SVG, PDF page 1, a video frame at 10 % (and audio cover
    art); ``None`` for other files."""
    path = _resolve_path(path)
    size_i = _clamp_int(size, 256, 16, 1024)
    return _thumb_for(classify(path, deep=False), size_i)


def thumb_path(target: FileInfo | str | os.PathLike, size: int = 256) -> str | None:
    """Fast thumbnail path for the wheel hub / job outputs.  Never raises; ``None`` when no
    thumbnail can be made (unsupported type, missing tool, broken file…)."""
    try:
        if isinstance(target, FileInfo):
            info = target
        else:
            info = classify(os.fspath(target), deep=False)
        if not info.path or not info.exists or info.is_dir or info.size <= 0:
            return None
        if not os.access(info.path, os.R_OK):
            return None
        res = _thumb_for(info, _clamp_int(size, 256, 16, 1024))
        return res["png"] if res else None
    except Exception as exc:  # thumbnails are best effort
        log.debug("thumbnail of %r failed: %s", getattr(target, "path", target), exc)
        return None


def image(path: str, max: int = 1600) -> dict:  # noqa: A002 - protocol name
    """Editor preview: ``{png, width, height, scale}`` where width/height are the ORIGINAL pixel
    size (EXIF-rotated) and ``scale`` = png width / original width (≤ 1)."""
    path = _resolve_path(path)
    box = _clamp_int(max, 1600, 64, 8192)
    info = classify(path, deep=False)
    if info.category not in ("image", "svg"):
        raise UserError("preview.err.not_image", name=info.name)

    def compute(base: Path) -> dict:
        final = base.with_suffix(".png")
        if info.category == "svg":
            res = _svg_render(path, info, box, final)
        else:
            res = _render_raster(path, info.ext, box, final)
        ow, oh = int(res.get("orig_w") or 0), int(res.get("orig_h") or 0)
        if not (ow and oh):
            # Rectangles drawn on this picture are applied in original pixels: never guess them.
            with contextlib.suppress(OSError):
                final.unlink()
            if info.ext in _RAW_EXTS and not tools.have("exiftool"):
                raise MissingTool("exiftool")
            raise UserError("preview.err.unreadable", name=info.name)
        scale = round(res["width"] / float(ow), 6)
        return {"png": res["png"], "width": ow, "height": oh, "scale": scale, "png_width": res["width"],
                "png_height": res["height"]}

    return _cached(path, "image", {"max": box}, compute)


# =================================================================================== metadata

def _stringify(value: Any, limit: int = 2000) -> str:
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return f"({len(value)} bytes)"
    elif isinstance(value, (list, tuple)):
        text = ", ".join(_stringify(v, limit) for v in value)
    elif isinstance(value, dict):
        text = ", ".join(f"{k}: {_stringify(v, limit)}" for k, v in value.items())
    elif isinstance(value, float):
        text = f"{value:g}"
    else:
        text = str(value)
    text = text.replace("\x00", "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _group_rank(name: str) -> tuple[int, str]:
    for i, prefix in enumerate(_GROUP_ORDER):
        if name == prefix or name.startswith(prefix):
            return i, name
    return len(_GROUP_ORDER), name


def _file_group(path: str, info: FileInfo, lang: str | None) -> dict:
    st = os.stat(path)
    items = [
        {"key": "FileName", "value": info.name, "editable": False, "id": "File:FileName"},
        {"key": "FileSize", "value": f"{util.human_size(st.st_size, lang or 'en')} ({st.st_size} B)",
         "editable": False, "id": "File:FileSize"},
        {"key": "FileModifyDate", "value": datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="seconds"),
         "editable": False, "id": "File:FileModifyDate"},
        {"key": "MIMEType", "value": info.mime, "editable": False, "id": "File:MIMEType"},
    ]
    if info.width and info.height:
        items.append({"key": "ImageSize", "value": f"{info.width}×{info.height}", "editable": False,
                      "id": "File:ImageSize"})
    if info.pages:
        items.append({"key": "PageCount", "value": str(info.pages), "editable": False, "id": "File:PageCount"})
    return {"name": "File", "items": items}


def _meta_exiftool(path: str, info: FileInfo) -> list[dict]:
    res = _run(["exiftool", "-j", "-G1", "-a", "-s", "-q", "-api", "largefilesupport=1", path], timeout=60,
               ok_codes=(0, 1))
    try:
        data = json.loads(res.stdout.decode("utf-8", "replace") or "[]")
    except ValueError as exc:
        raise ToolError("err.probe_failed", details=str(exc), name=info.name) from None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return []
    writable = info.ext in _EXIF_WRITABLE
    groups: dict[str, list[dict]] = {}
    for full, value in data[0].items():
        if full == "SourceFile":
            continue
        group, _, tag = full.partition(":")
        if not tag:
            group, tag = "Other", group
        if group in _SKIP_GROUPS:
            continue
        editable = writable and (group in _EDITABLE_GROUPS or group.startswith("XMP-"))
        groups.setdefault(group, []).append({"key": tag, "value": _stringify(value), "editable": editable,
                                             "id": full})
    return [{"name": name, "items": items} for name, items in sorted(groups.items(), key=lambda g: _group_rank(g[0]))]


def _meta_pillow(path: str) -> list[dict]:
    from PIL import ExifTags, Image

    _register_plugins()
    groups: list[dict] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with Image.open(path) as im:
            basic = [
                {"key": "Format", "value": str(im.format or ""), "editable": False, "id": "Image:Format"},
                {"key": "Mode", "value": str(im.mode), "editable": False, "id": "Image:Mode"},
                {"key": "Size", "value": f"{im.size[0]}×{im.size[1]}", "editable": False, "id": "Image:Size"},
            ]
            frames = getattr(im, "n_frames", 1)
            if frames and frames > 1:
                basic.append({"key": "Frames", "value": str(frames), "editable": False, "id": "Image:Frames"})
            if im.info.get("dpi"):
                basic.append({"key": "DPI", "value": _stringify(im.info["dpi"]), "editable": False, "id": "Image:DPI"})
            if im.info.get("icc_profile"):
                basic.append({"key": "ICCProfile", "value": f"({len(im.info['icc_profile'])} bytes)",
                              "editable": False, "id": "Image:ICCProfile"})
            groups.append({"name": "Image", "items": basic})
            try:
                exif = im.getexif()
            except Exception:
                exif = None
            if exif:
                main = [{"key": ExifTags.TAGS.get(k, f"0x{k:04x}"), "value": _stringify(v), "editable": False,
                         "id": f"IFD0:{ExifTags.TAGS.get(k, f'0x{k:04x}')}"}
                        for k, v in exif.items() if k not in (0x8769, 0x8825, 0x927C)]
                if main:
                    groups.append({"name": "IFD0", "items": main})
                for ifd_id, name, table in ((0x8769, "ExifIFD", ExifTags.TAGS), (0x8825, "GPS", ExifTags.GPSTAGS)):
                    try:
                        sub = exif.get_ifd(ifd_id)
                    except Exception:
                        sub = {}
                    rows = [{"key": table.get(k, f"0x{k:04x}"), "value": _stringify(v), "editable": False,
                             "id": f"{name}:{table.get(k, f'0x{k:04x}')}"}
                            for k, v in (sub or {}).items() if k != 0x927C]
                    if rows:
                        groups.append({"name": name, "items": rows})
            text_chunks = getattr(im, "text", None) or {}
            if text_chunks:
                groups.append({"name": "PNG", "items": [
                    {"key": str(k), "value": _stringify(v), "editable": False, "id": f"PNG:{k}"}
                    for k, v in text_chunks.items()]})
            xmp = im.info.get("xmp") or im.info.get("XML:com.adobe.xmp")
            if xmp:
                groups.append({"name": "XMP", "items": [
                    {"key": "XMP", "value": _stringify(xmp, 4000), "editable": False, "id": "XMP:XMP"}]})
    return groups


def _meta_pdf(path: str) -> list[dict]:
    editable_keys = {"title", "author", "subject", "keywords", "creator", "producer"}
    try:
        with _pymupdf_doc(path) as doc:
            meta = dict(doc.metadata or {})
            rows = [{"key": k[:1].upper() + k[1:], "value": _stringify(v), "editable": k in editable_keys,
                     "id": f"PDF:{k[:1].upper() + k[1:]}"} for k, v in meta.items() if v not in (None, "")]
            for k in editable_keys:
                if not meta.get(k):
                    rows.append({"key": k[:1].upper() + k[1:], "value": "", "editable": True,
                                 "id": f"PDF:{k[:1].upper() + k[1:]}"})
            rows.append({"key": "PageCount", "value": str(doc.page_count), "editable": False, "id": "PDF:PageCount"})
            return [{"name": "PDF", "items": sorted(rows, key=lambda r: r["key"])}]
    except ImportError:
        pass
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        reader.decrypt("")
    info = reader.metadata or {}
    rows = [{"key": str(k).lstrip("/"), "value": _stringify(v), "editable": False, "id": f"PDF:{str(k).lstrip('/')}"}
            for k, v in info.items()]
    rows.append({"key": "PageCount", "value": str(len(reader.pages)), "editable": False, "id": "PDF:PageCount"})
    return [{"name": "PDF", "items": rows}]


def _meta_mutagen(path: str) -> list[dict]:
    """Stream information of an audio file (its tags come from :func:`_media_tag_group`)."""
    import mutagen  # type: ignore

    easy = mutagen.File(path, easy=True)
    stream = getattr(easy, "info", None)
    if stream is None:
        return []
    rows = []
    for attr in ("length", "bitrate", "sample_rate", "channels", "bits_per_sample", "codec"):
        value = getattr(stream, attr, None)
        if value not in (None, "", 0):
            if attr == "length":
                value = util.human_duration(value)
            rows.append({"key": attr, "value": _stringify(value), "editable": False, "id": f"Audio:{attr}"})
    return [{"name": "Audio", "items": rows}] if rows else []


def _meta_ffprobe(path: str) -> list[dict]:
    probe = _ffprobe(path)
    groups = []
    fmt = probe.get("format") or {}
    rows = []
    for key in ("format_long_name", "duration", "bit_rate", "nb_streams"):
        if fmt.get(key) not in (None, "", "N/A"):
            value = util.human_duration(_to_float(fmt[key])) if key == "duration" else fmt[key]
            rows.append({"key": key, "value": _stringify(value), "editable": False, "id": f"Format:{key}"})
    for key, value in (fmt.get("tags") or {}).items():
        if key.lower() in _FF_INTERNAL_TAGS:  # the others are in the editable "Tags" group
            rows.append({"key": key, "value": _stringify(value), "editable": False, "id": f"Format:{key}"})
    if rows:
        groups.append({"name": "Format", "items": rows})
    for stream in probe.get("streams") or []:
        idx = stream.get("index")
        kind = stream.get("codec_type", "stream")
        srows = [{"key": "codec", "value": _stringify(stream.get("codec_long_name") or stream.get("codec_name") or ""),
                  "editable": False, "id": f"Stream{idx}:codec"}]
        for key in ("width", "height", "pix_fmt", "avg_frame_rate", "sample_rate", "channels", "channel_layout",
                    "bit_rate"):
            if stream.get(key) not in (None, "", "N/A", "0/0"):
                srows.append({"key": key, "value": _stringify(stream[key]), "editable": False,
                              "id": f"Stream{idx}:{key}"})
        for key, value in (stream.get("tags") or {}).items():
            srows.append({"key": key, "value": _stringify(value), "editable": False, "id": f"Stream{idx}:{key}"})
        groups.append({"name": f"Stream #{idx} ({kind})", "items": srows})
    return groups


def _ffprobe_tags(probe: dict) -> dict[str, Any]:
    """The tags ffmpeg sees in a file, without the ones it writes itself.  Ogg keeps them on the
    audio stream instead of the container."""
    fmt = probe.get("format") or {}
    tags = dict(fmt.get("tags") or {})
    if "ogg" in str(fmt.get("format_name") or ""):
        stream = next((s for s in probe.get("streams") or [] if s.get("codec_type") == "audio"), None)
        for key, value in ((stream or {}).get("tags") or {}).items():
            tags.setdefault(key, value)
    return {k: v for k, v in tags.items() if k.lower() not in _FF_INTERNAL_TAGS}


def _mutagen_tags(path: str) -> list[tuple[str, Any]] | None:
    """Tags of an audio file through mutagen's "easy" keys; ``None`` when mutagen cannot write
    them by those names (``audio.tags`` then falls back to ffmpeg, or maps the names itself)."""
    import mutagen  # type: ignore

    easy = mutagen.File(path, easy=True)
    if easy is None or type(easy).__name__ in _MUTAGEN_RAW_TYPES:
        return None
    existing = dict(easy.tags.items()) if easy.tags is not None else {}
    return sorted(existing.items(), key=lambda kv: kv[0])


def _media_tag_group(path: str, info: FileInfo) -> dict | None:
    """The tags ``audio.tags`` / ``video.metadata.edit`` can rewrite, whatever the file format.

    Those actions write through mutagen or ffmpeg (never exiftool), so the rows come from the
    same tools.  Every ``id`` goes through ``normalize_tag_key`` in the action; the common tags
    are always listed, because the dialog has no way to add a new tag."""
    from .actions import _media_util as mu

    pairs: list[tuple[str, Any]] | None = None
    if info.category == "audio" and tools.have("py:mutagen"):
        try:
            pairs = _mutagen_tags(path)
        except Exception as exc:  # mutagen raises many exception types for odd files
            log.debug("mutagen cannot read %s: %s", path, exc)
    if pairs is None:
        if not tools.have("ffprobe"):
            return None
        try:
            pairs = list(_ffprobe_tags(_ffprobe(path)).items())
        except UserError as exc:
            log.debug("ffprobe cannot read %s: %s", path, exc)
            return None
    rows: list[dict] = []
    seen: set[str] = set()
    for key, value in pairs:
        canon = mu.normalize_tag_key(key)
        if canon in seen:
            continue
        seen.add(canon)
        if canon == "cover":  # an image, not text: it can only be removed, and only by audio.tags
            if info.category == "audio":
                rows.append({"key": key, "value": "(image)", "editable": False, "removable": True, "id": f"Tag:{key}"})
            continue
        text = "; ".join(_stringify(v) for v in value) if isinstance(value, list) else _stringify(value)
        # normalize_tag_key() reads the part after the last colon, so such a key cannot be addressed;
        # dotted keys (com.apple.quicktime.…) are not written back by a default ffmpeg remux
        rows.append({"key": key, "value": text, "editable": ":" not in key and "." not in key, "id": f"Tag:{key}"})
    for key in _MEDIA_TAG_SLOTS[info.category]:
        if key not in seen:
            rows.append({"key": key, "value": "", "editable": True, "removable": False, "id": f"Tag:{key}"})
    return {"name": "Tags", "items": rows}


def _merge_media_tags(groups: list[dict], tags: dict, category: str) -> list[dict]:
    """``tags`` first, then ``groups`` without the rows repeating one of its tags (their value
    would not follow the edits).  An embedded cover listed by exiftool can be removed."""
    from .actions import _media_util as mu

    shown = {mu.normalize_tag_key(item["key"]) for item in tags["items"]}
    merged = [tags]
    for group in groups:
        holds_tags = bool(_MEDIA_TAG_GROUP_RE.match(str(group.get("name") or "")))
        items = []
        for item in group.get("items") or []:
            canon = mu.normalize_tag_key(item.get("key"))
            if holds_tags and canon in shown:
                continue
            if holds_tags and canon == "cover" and category == "audio":
                item = {**item, "removable": True}
            items.append(item)
        if items:
            merged.append({**group, "items": items})
    return merged


def metadata(path: str, lang: str | None = None) -> dict:
    """``{groups: [{name, items: [{key, value, editable, id}]}], editable}``.

    ``id`` is the fully qualified tag (``"IFD0:Make"``) to use in the metadata dialog's ``set``/
    ``remove`` lists.  exiftool is preferred; without it Pillow (images), PyMuPDF/pypdf (PDF),
    mutagen (audio) or ffprobe (media) provide read-mostly information.  Audio and video files
    also get a "Tags" group with what their actions can rewrite (see :func:`_media_tag_group`).
    An item may carry ``removable`` when that differs from ``editable``.
    """
    path = _resolve_path(path)
    info = classify(path, deep=True)
    groups: list[dict] = []
    source = ""
    errors: list[str] = []
    if tools.have("exiftool"):
        try:
            groups = _meta_exiftool(path, info)
            source = "exiftool"
        except UserError as exc:
            errors.append(f"exiftool: {exc.text('en')} {exc.details}".strip())
    if not groups:
        attempts: list[tuple[str, Callable[[str], list[dict]], bool]] = []
        if info.category == "image":
            attempts.append(("pillow", _meta_pillow, tools.have("py:PIL")))
        if info.category == "pdf":
            attempts.append(("pdf", _meta_pdf, tools.have("py:pymupdf") or tools.have("py:pypdf")))
        if info.category == "audio":
            attempts.append(("mutagen", _meta_mutagen, tools.have("py:mutagen")))
        if info.category in ("audio", "video"):
            attempts.append(("ffprobe", _meta_ffprobe, tools.have("ffprobe")))
        for name, fn, available in attempts:
            if not available:
                continue
            try:
                groups = fn(path)
                source = name
                if groups:
                    break
            except Exception as exc:
                errors.append(f"{name}: {exc}")
    if info.category in _MEDIA_TAG_SLOTS:
        tag_group = _media_tag_group(path, info)
        if tag_group is not None:
            groups = _merge_media_tags(groups, tag_group, info.category)
            source = source or "tags"
    if source != "exiftool":  # exiftool already reports the File/System groups
        groups.insert(0, _file_group(path, info, lang))
    editable = any(item.get("editable") for g in groups for item in g.get("items") or [])
    result = {"groups": groups, "editable": editable, "source": source or "file"}
    if errors and not source:
        result["warnings"] = errors
    if not tools.have("exiftool"):
        result["install"] = tools.install_hint(["exiftool"])
    return result


# ======================================================================================= text

_BOMS = ((b"\xef\xbb\xbf", "utf-8-sig", "utf-8"), (b"\xff\xfe\x00\x00", "utf-32-le", "utf-32-le"),
         (b"\x00\x00\xfe\xff", "utf-32-be", "utf-32-be"), (b"\xff\xfe", "utf-16-le", "utf-16-le"),
         (b"\xfe\xff", "utf-16-be", "utf-16-be"))


_WESTERN_CODECS = ("utf_8", "cp1252", "latin_1", "iso8859_15")


def _decode(data: bytes, truncated: bool) -> tuple[str, str]:
    for bom, codec, name in _BOMS:
        if data.startswith(bom):
            body = data[len(bom):]
            unit = 4 if "32" in codec else 2 if "16" in codec else 1
            if truncated and unit > 1:
                body = body[: len(body) - (len(body) % unit)]
            return body.decode(codec.replace("-sig", ""), "replace"), name
    if b"\x00" in data[:8192]:
        raise UserError("preview.err.binary")
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError as exc:
        if truncated and exc.start >= len(data) - 3:
            try:
                return data[: exc.start].decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                pass
    try:
        from charset_normalizer import from_bytes  # type: ignore

        results = from_bytes(data)
        best = results.best()
        if best is not None and best.encoding:
            # Short texts fit several single-byte code pages equally well (è is č in cp1250):
            # prefer the Western ones (the UI targets Italian and English users) when they fit as
            # well as the best guess; a Cyrillic or Greek text never has them as candidates.
            chosen = best
            for match in results:
                if (match.encoding in _WESTERN_CODECS and match.chaos <= best.chaos + 0.02
                        and match.coherence >= best.coherence - 0.05):
                    chosen = match
                    break
            return str(chosen), str(chosen.encoding).replace("_", "-")
    except ImportError:
        pass
    except Exception as exc:  # charset_normalizer edge cases must not break the preview
        log.debug("charset detection failed: %s", exc)
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def text(path: str, max_bytes: int = 65536) -> dict:
    """``{text, encoding, truncated, size}``: the first ``max_bytes`` bytes decoded (newlines
    normalised to ``\\n``).  Binary files raise ``UserError``."""
    path = _resolve_path(path)
    limit = _clamp_int(max_bytes, 65536, 1, 4 * 1024 * 1024)
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        data = fh.read(limit)
    truncated = size > len(data)
    decoded, encoding = _decode(data, truncated)
    decoded = decoded.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    return {"text": decoded, "encoding": encoding, "truncated": truncated, "size": size}


# ================================================================================== dispatcher

def preview(kind: str, params: dict | None = None, lang: str | None = None) -> Any:
    """The ``preview`` RPC: dispatch ``kind`` with ``params`` (SPEC §5.5).

    Raises :class:`UserError` (translated by the caller) for unknown kinds, missing files,
    missing tools and failures.
    """
    p = dict(params or {})
    kind = str(kind or p.pop("kind", "") or "")
    password = p.get("password") or None
    if kind == "thumb":
        return thumb(p.get("path"), p.get("size", 256))
    if kind == "image":
        return image(p.get("path"), p.get("max", 1600))
    if kind == "pdf_info":
        return pdf_info(p.get("path"), password)
    if kind == "pdf_page":
        return pdf_page(p.get("path"), p.get("page", 0), p.get("dpi", 110), password)
    if kind == "pdf_search":
        return pdf_search(p.get("path"), p.get("query", ""), password, p.get("limit", 5000))
    if kind == "video_info":
        if not tools.have("ffprobe"):
            raise MissingTool("ffprobe")
        return video_info(p.get("path"))
    if kind == "video_frames":
        if not tools.have("ffprobe"):
            raise MissingTool("ffprobe")
        return video_frames(p.get("path"), p.get("count", 12), p.get("height", 72))
    if kind == "audio_waveform":
        if not tools.have("ffprobe"):
            raise MissingTool("ffprobe")
        return audio_waveform(p.get("path"), p.get("width", 1200), p.get("height", 160), p.get("color"))
    if kind == "metadata":
        return metadata(p.get("path"), lang)
    if kind == "text":
        return text(p.get("path"), p.get("max_bytes", 65536))
    raise UserError("preview.err.unknown_kind", kind=kind or "?")
