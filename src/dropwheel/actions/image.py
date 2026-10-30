"""Image actions: categories ``image`` (static and animated GIF/WebP/APNG) and ``svg``.

Rings (SPEC §4, feature-catalogue §2.1–2.3, §2.17):

* image: Convert→ · Compress→ · Resize→ · Crop… · Edit→ · Background→ · Privacy→ · More→
* animated image: Convert→ · Compress→ · Resize→ · Crop… · Edit→ · Frames→ · Privacy→ · Copy
* svg: Convert→ · Optimize · Copy as text · Copy image · Icons→ · Print
* multi:image: Combine→ · Convert→ · Compress→ · Resize→ · Crop… · Privacy→ · Edit→ · More→
  (still and animated images together get the still-image Convert/Compress/Resize, which skip the
  animations; the animated families need a selection without still images, Frames→ is in More)
* mixed (images + SVG): Convert each→ · Collage

Backends (each action lists alternatives in ``requires`` so the menu greys it out with the right
install hint): Pillow (+ pillow_heif, pillow_jxl) for decoding/encoding/editing; heif-dec/djxl/
avifdec/darktable-cli/ImageMagick/exiftool previews as decoders of last resort; jpegtran
(lossless rotate/flip/crop), jpegoptim, pngquant, oxipng, gifsicle, gifski; exiftool for
metadata (lossless JPEG/PNG segment rewriting as fallback); rembg; tesseract; zbarimg;
realesrgan-ncnn-vulkan / waifu2x-ncnn-vulkan; potrace / vtracer; rsvg-convert → resvg →
ImageMagick → CairoSVG → Inkscape for SVG; scour / svgo; ffmpeg; img2pdf / PyMuPDF; wl-copy;
caelestia / swww / hyprpaper; lp.

Inputs are never modified: outputs go through ``ctx.output_path`` / ``ctx.output_dir`` and are
written atomically; "Replace original" (``replace_ok``) goes through ``ctx.replace_original``.
The only exception is *Rename by date*, whose purpose is renaming (not undoable, like the File
page's Rename).
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .. import config as cfgmod
from .. import i18n, tools, util
from ..detect import FileInfo
from ..errors import Cancelled, MissingTool, ToolError, UserError
from ..jobs import Context, Result, atomic_target
from ..registry import P, Param, action, group, layout
from . import _image_ops as ops

log = logging.getLogger("dropwheel.actions.image")

__all__ = ["RAW_FORMATS", "ALPHA_FORMATS", "COMPRESSIBLE", "is_static", "is_animated", "same_format",
           "parse_rect", "ordered_files", "rembg_home"]

RAW_FORMATS = frozenset({"cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "dng", "raf", "orf", "rw2", "pef",
                         "srw", "x3f", "3fr", "iiq", "rwl", "erf", "kdc", "mrw", "mos", "dcr", "raw"})
#: formats that may carry transparency
ALPHA_FORMATS = frozenset({"png", "webp", "avif", "heic", "jxl", "tiff", "gif", "ico", "psd", "tga", "qoi", "xcf",
                           "dds", "icns", "exr"})
#: formats the static Compress presets handle
COMPRESSIBLE = frozenset({"jpg", "png", "webp", "avif", "heic", "jxl", "tiff", "bmp", "gif"})
#: formats we can write back (same-format edits)
_WRITABLE = frozenset({"png", "jpg", "webp", "avif", "heic", "jxl", "tiff", "gif", "bmp", "ico"})
_ANIM_WRITABLE = frozenset({"gif", "webp", "png"})
_LOSSY = frozenset({"jpg", "webp", "avif", "heic", "jxl"})
_CLI_ENCODERS = {"avif": ("avifenc", "magick"), "heic": ("heif-enc", "magick"), "jxl": ("cjxl", "magick")}
_REALESRGAN_MODELS = ("/usr/share/realesrgan-ncnn-vulkan/models", "/usr/lib/realesrgan-ncnn-vulkan/models",
                      "/opt/realesrgan-ncnn-vulkan/models")
_WAIFU2X_MODELS = ("/usr/share/waifu2x-ncnn-vulkan/models-cunet", "/usr/lib/waifu2x-ncnn-vulkan/models-cunet",
                   "/opt/waifu2x-ncnn-vulkan/models-cunet")
_KEY_RE = re.compile(r"^[A-Za-z][\w-]*(?::[A-Za-z][\w-]*)?$")


# ===================================================================================== predicates

def is_static(f: FileInfo) -> bool:
    """A still raster image."""
    return f.category == "image" and not f.animated


def is_animated(f: FileInfo) -> bool:
    """An animated GIF / WebP / APNG / AVIS."""
    return f.category == "image" and bool(f.animated)


def _fmt(f: FileInfo) -> str:
    return ops.fmt_of(f)


def _any(pred: Callable[[FileInfo], bool]) -> Callable[[list[FileInfo]], bool]:
    return lambda files: any(pred(f) for f in files)


_STATIC = _any(is_static)
_ANIM = _any(is_animated)
_STATIC_OR_SVG = _any(lambda f: is_static(f) or f.category == "svg")


def _anim_only(pred: Callable[[FileInfo], bool] = is_animated,
               still: Callable[[FileInfo], bool] = is_static) -> Callable[[list[FileInfo]], bool]:
    """``when`` of the animated Convert/Compress/Resize families: no file the still twin takes.

    Those groups share their labels and icons with the still-image ones, so a selection of still
    *and* animated images would show Convert, Compress and Resize twice each (and push Crop,
    Privacy and Edit into More).  There the still-image family wins and skips the animations
    (``still`` = what the still twin accepts); the job runner checks ``when`` per file, so an
    animation alone still passes.
    """
    return lambda files: not any(still(f) for f in files) and any(pred(f) for f in files)


_ANIM_ONLY = _anim_only()


def _not_target(target: str) -> Callable[[list[FileInfo]], bool]:
    def pred(files: list[FileInfo]) -> bool:
        return any(is_static(f) and _fmt(f) != target for f in files)

    return pred


def _compressible(f: FileInfo) -> bool:
    return is_static(f) and _fmt(f) in COMPRESSIBLE


def _maybe_alpha(f: FileInfo) -> bool:
    return is_static(f) and _fmt(f) in ALPHA_FORMATS


def _anim_fmt(*fmts: str) -> Callable[[FileInfo], bool]:
    return lambda f: is_animated(f) and _fmt(f) in fmts


def _anim_not(target: str) -> Callable[[list[FileInfo]], bool]:
    return lambda files: any(is_animated(f) and _fmt(f) != target for f in files)


def _mixed_image_svg(files: list[FileInfo]) -> bool:
    cats = {f.category for f in files}
    return cats == {"image", "svg"} and all(is_static(f) or f.category == "svg" for f in files)


# ======================================================================================== helpers

def _cfg(ctx: Context, key: str, default: Any = None) -> Any:
    return cfgmod.get(ctx.config, key, default)


def _keep_meta(ctx: Context) -> bool:
    return bool(_cfg(ctx, "image.keep_metadata", True))


def _struct(value: Any) -> Any:
    """Structured param that may arrive as JSON text (CLI ``-p rect='{…}'``)."""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "["):
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def _per_file(ctx: Context, f: FileInfo, name: str, default: Any = None) -> Any:
    """``params["per_file"][<path>][name]`` (multi-file editors) or the shared ``params[name]``."""
    per = _struct(ctx.params.get("per_file"))
    if isinstance(per, dict):
        orig = ctx.original(f)
        for key in (orig.path, f.path, orig.uri, f.uri, orig.name):
            entry = per.get(key)
            if isinstance(entry, dict) and name in entry:
                return _struct(entry[name])
    value = ctx.params.get(name)
    return default if value is None else _struct(value)


def parse_rect(value: Any) -> dict | None:
    """``{x,y,w,h}`` from a dict, a 4-sequence, ``"x,y,w,h"`` or ``"WxH+X+Y"`` (``None`` if invalid)."""
    value = _struct(value)
    if isinstance(value, dict):
        try:
            return {"x": float(value.get("x", 0)), "y": float(value.get("y", 0)),
                    "w": float(value.get("w", value.get("width", 0))),
                    "h": float(value.get("h", value.get("height", 0)))}
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            x, y, w, h = (float(v) for v in value[:4])
        except (TypeError, ValueError):
            return None
        return {"x": x, "y": y, "w": w, "h": h}
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)\s*",
                         value)
        if m:
            w, h, x, y = (float(g) for g in m.groups())
            return {"x": x, "y": y, "w": w, "h": h}
        parts = [p for p in re.split(r"[\s,;]+", value.strip()) if p]
        if len(parts) == 4:
            try:
                x, y, w, h = (float(p) for p in parts)
            except ValueError:
                return None
            return {"x": x, "y": y, "w": w, "h": h}
    return None


def _label(ctx: Context) -> str:
    return i18n.t(ctx.action.label, ctx.lang)


def _warn_key(ctx: Context, key: str, **fmt: Any) -> None:
    ctx.warn(ctx.t(key, **fmt))


def _tmp(ctx: Context, name: str) -> Path:
    """A fresh path in the job temp folder (never reused within the job)."""
    base = ctx.tmpdir()
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for i in range(10000):
        cand = base / (f"{stem}-{i}.{ext}" if ext else f"{stem}-{i}") if i else base / name
        if not cand.exists():
            return cand
    raise UserError("err.no_free_name", name=name)


def _link_input(ctx: Context, path: str | os.PathLike, name: str = "input") -> Path:
    """Symlink (or copy) a source into the temp folder under a plain ASCII name.

    Used for tools that interpret file names (ImageMagick ``[0]``/globs, ffmpeg patterns)."""
    src = Path(os.fspath(path))
    ext = util.split_name(src.name)[1] or "dat"
    dst = _tmp(ctx, f"{name}.{ext}")
    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError):
        shutil.copyfile(src, dst)
    return dst


def _work_copy(ctx: Context, path: str | os.PathLike, name: str = "work") -> Path:
    """A real copy in the temp folder (for tools that edit in place)."""
    src = Path(os.fspath(path))
    ext = util.split_name(src.name)[1] or "dat"
    dst = _tmp(ctx, f"{name}.{ext}")
    shutil.copyfile(src, dst)
    return dst


def _deliver(ctx: Context, produced: Path, out: Path) -> Path:
    """Copy a temp result into its reserved output path atomically."""
    with atomic_target(out) as part:
        try:
            os.link(produced, part)
        except OSError:
            shutil.copyfile(produced, part)
    return out


def _can_write(fmt: str) -> bool:
    fmt = ops.norm_fmt(fmt)
    if ops.pil_can_save(fmt):
        return True
    return any(tools.have(t) for t in _CLI_ENCODERS.get(fmt, ()))


def same_format(f: FileInfo, *, alpha: bool = False) -> str:
    """Output format of a same-format edit: the source format when writable, else PNG (JPG for RAW)."""
    fmt = _fmt(f)
    if fmt in _WRITABLE and _can_write(fmt):
        return fmt
    if fmt in RAW_FORMATS and not alpha:
        return "jpg"
    return "png"


def _out_ext(fmt: str) -> str:
    spec = ops.FORMATS.get(ops.norm_fmt(fmt))
    return spec.ext if spec else ops.norm_fmt(fmt)


def _quality_for(ctx: Context, fmt: str) -> int:
    fmt = ops.norm_fmt(fmt)
    if fmt == "jpg":
        return int(_cfg(ctx, "image.jpeg_quality", 85))
    if fmt == "webp":
        return int(_cfg(ctx, "image.webp_quality", 80))
    if fmt == "avif":
        return int(_cfg(ctx, "image.avif_quality", 60))
    if fmt == "heic":
        return 75
    if fmt == "jxl":
        return 90
    return 90


# ------------------------------------------------------------------------------------- decoding

def _decode_cli(ctx: Context, f: FileInfo) -> Path | None:
    """Decode a file Pillow cannot read into a temp PNG/TIFF with an external tool."""
    fmt = _fmt(f)
    out = _tmp(ctx, "decoded.png")
    src = f.path
    attempts: list[tuple[str, Callable[[], None]]] = []
    if fmt == "heic":
        attempts.append(("heif-dec", lambda: ctx.run(["heif-dec", src, out], timeout=300)))
    if fmt == "jxl":
        attempts.append(("djxl", lambda: ctx.run(["djxl", src, out], timeout=300)))
    if fmt == "avif":
        attempts.append(("avifdec", lambda: ctx.run(["avifdec", src, out], timeout=300)))
    if fmt in RAW_FORMATS:
        def darktable() -> None:
            conf = _tmp(ctx, "darktable-conf")
            conf.mkdir(parents=True, exist_ok=True)
            ctx.run(["darktable-cli", src, out.with_suffix(".tif"), "--core", "--library", ":memory:",
                     "--configdir", conf], timeout=900)
        attempts.append(("darktable-cli", darktable))

    def magick() -> None:
        link = _link_input(ctx, src)
        spec = f"{link}[0]" if fmt not in ("xcf",) else str(link)
        extra = ["-background", "none", "-flatten"] if fmt in ("xcf", "psd") else []
        ctx.run(["magick", spec, *extra, "-auto-orient", f"png:{out}"], timeout=600)

    attempts.append(("magick", magick))
    if fmt in RAW_FORMATS:
        def preview() -> None:
            for tag in ("-JpgFromRaw", "-PreviewImage"):
                res = ctx.run(["exiftool", "-b", tag, src], timeout=120, text=False, ok_codes=(0, 1))
                data = res.stdout or b""
                if len(data) > 1024 and data[:2] == b"\xff\xd8":
                    # embedded previews usually lack the RAW's Orientation: apply it to the pixels
                    orient = ctx.run(["exiftool", "-n", "-s3", "-Orientation", src], timeout=60, ok_codes=(0, 1))
                    try:
                        value = int((orient.stdout or "1").strip().split()[0])
                    except (ValueError, IndexError):
                        value = 1
                    from PIL import Image

                    with Image.open(io.BytesIO(data)) as prev:
                        prev.load()
                        pixels = ops.pil_transpose(prev.convert("RGB"), ops.orientation_op(value))
                    pixels.save(out, "PNG")
                    _warn_key(ctx, "img.warn.raw_preview")
                    return
        attempts.append(("exiftool", preview))
    candidates = (out, out.with_suffix(".tif"), out.with_suffix(".jpg"))
    for tool, run in attempts:
        if not tools.have(tool):
            continue
        ctx.check_cancel()
        try:
            run()
        except (ToolError, OSError) as exc:
            log.debug("decoder %s failed for %s: %s", tool, f.path, getattr(exc, "details", "") or exc)
            for cand in candidates:            # never pick up a partial file of a failed decoder
                try:
                    cand.unlink()
                except OSError:
                    pass
            continue
        for cand in candidates:
            if cand.exists() and cand.stat().st_size > 0:
                return cand
    return None


def _missing_decoder(f: FileInfo) -> list[str] | None:
    fmt = _fmt(f)
    if fmt == "heic":
        return ["py:pillow_heif"]
    if fmt == "jxl":
        return ["py:pillow_jxl"]
    if fmt in RAW_FORMATS:
        return ["darktable-cli"]
    if fmt in ("xcf", "psd", "exr", "hdr"):
        return ["magick"]
    return None


def _load(ctx: Context, f: FileInfo, *, frame: int = 0) -> tuple[Any, ops.Meta]:
    """Decode ``f`` (EXIF orientation applied) → ``(image, meta)``, with CLI decoders as fallback.

    RAW camera files never go through Pillow: it would open their TIFF container and return the
    small embedded thumbnail instead of the photo."""
    if _fmt(f) in RAW_FORMATS:
        ctx.progress(None, _label(ctx))
        decoded = _decode_cli(ctx, f)
        if decoded is None:
            raise MissingTool(["darktable-cli"])
        im, meta = ops.load_oriented(decoded, f.name)
        meta.fmt = _fmt(f)
        return im, meta
    try:
        return ops.load_oriented(f.path, f.name, frame)
    except UserError as exc:
        if exc.message not in ("img.err.unreadable", "err.damaged"):
            raise
        first = exc
    decoded = _decode_cli(ctx, f)
    if decoded is None:
        need = _missing_decoder(f)
        if need and not any(tools.have(t) for t in need + ["magick"]):
            raise MissingTool(need)
        raise first
    im, meta = ops.load_oriented(decoded, f.name)
    meta.fmt = _fmt(f)
    return im, meta


def _load_any(ctx: Context, f: FileInfo, *, size: int | None = None) -> tuple[Any, ops.Meta]:
    """Raster of an image or SVG (SVG rendered at ``size`` long edge, default intrinsic ≥ 1024)."""
    if f.category == "svg":
        png = _tmp(ctx, "render.png")
        _render_svg(ctx, f, png, long_edge=size or None, min_edge=1024 if size is None else None)
        im, meta = ops.load_oriented(png, f.name)
        meta.fmt = "svg"
        return im, meta
    return _load(ctx, f)


# --------------------------------------------------------------------------------------- writing

def _save_cli(ctx: Context, im: Any, meta: ops.Meta | None, part: Path, fmt: str, quality: int | None,
              lossless: bool) -> list[str]:
    """Encode AVIF/HEIC/JXL with the command-line encoders when Pillow lacks the plugin."""
    fmt = ops.norm_fmt(fmt)
    png = _tmp(ctx, "encode.png")
    ops.save_image(im, png, "png", meta=meta, keep_metadata=bool(meta is not None and _keep_meta(ctx)))
    q = int(quality or _quality_for(ctx, fmt))
    if fmt == "avif" and tools.have("avifenc"):
        args = ["avifenc", "-s", "6", "-j", "all"]
        args += ["-l"] if lossless else ["-q", str(q)]
        ctx.run([*args, png, part], timeout=900)
    elif fmt == "heic" and tools.have("heif-enc"):
        args = ["heif-enc", "-L"] if lossless else ["heif-enc", "-q", str(q)]
        ctx.run([*args, "-o", part, png], timeout=900)
    elif fmt == "jxl" and tools.have("cjxl"):
        ctx.run(["cjxl", png, part, "-d", "0"] if lossless else ["cjxl", png, part, "-q", str(q)], timeout=900)
    elif tools.have("magick"):
        args = ["magick", f"png:{png}"]
        args += ["-define", "heic:lossless=true"] if (lossless and fmt == "heic") else ["-quality",
                                                                                       "100" if lossless else str(q)]
        ctx.run([*args, f"{fmt}:{part}"], timeout=900)
    else:
        raise MissingTool([{"avif": "avifenc", "heic": "py:pillow_heif", "jxl": "py:pillow_jxl"}.get(fmt, "magick")])
    if not part.exists() or part.stat().st_size == 0:
        raise ToolError("err.no_output", name=part.name)
    return []


def _emit_warnings(ctx: Context, warns: Iterable[str], bg: Sequence[int] | None = None) -> None:
    for key in warns:
        if key == "warn.transparency_flattened":
            color = ops.hex_color(bg or (255, 255, 255))
            if color.lower() == "#ffffff":
                ctx.warn(ctx.t("warn.transparency_flattened"))
            else:
                ctx.warn(ctx.t("img.warn.flattened", color=color))
        else:
            ctx.warn(ctx.t(key))


def _encode_to(ctx: Context, im: Any, meta: ops.Meta | None, target: Path, fmt: str, *,
               quality: int | None = None, lossless: bool = False, bg: Sequence[int] = (255, 255, 255),
               keep_metadata: bool | None = None, icc: Any = True, qtables: Any = None, subsampling: Any = None,
               speed: int | None = None, ico_sizes: Sequence[int] | None = None, extra: dict | None = None) -> None:
    """Encode into an already-reserved temp/part path (CLI fallback for AVIF/HEIC/JXL)."""
    keep = _keep_meta(ctx) if keep_metadata is None else keep_metadata
    try:
        try:
            warns = ops.save_image(im, target, fmt, quality=quality, meta=meta, keep_metadata=keep,
                                   lossless=lossless, bg=bg, icc=icc, qtables=qtables, subsampling=subsampling,
                                   speed=speed, ico_sizes=ico_sizes, extra=extra)
        except (ValueError, TypeError, OSError) as exc:
            if qtables is None:
                raise
            log.debug("re-encode with source tables failed (%s); using quality", exc)
            warns = ops.save_image(im, target, fmt, quality=quality or _quality_for(ctx, fmt), meta=meta,
                                   keep_metadata=keep, lossless=lossless, bg=bg, icc=icc, speed=speed,
                                   ico_sizes=ico_sizes, extra=extra)
    except ops.FormatUnavailable:
        warns = _save_cli(ctx, im, meta if keep else None, target, fmt, quality, lossless)
    except OSError as exc:
        raise UserError("img.err.encode_failed", details=str(exc), fmt=fmt.upper()) from None
    _emit_warnings(ctx, warns, bg)


def _write(ctx: Context, im: Any, meta: ops.Meta | None, out: Path, fmt: str, **kw: Any) -> Path:
    """Encode ``im`` atomically into the reserved output ``out``."""
    with atomic_target(out) as part:
        _encode_to(ctx, im, meta, part, fmt, **kw)
    return out


def _edit_kwargs(ctx: Context, f: FileInfo, meta: ops.Meta, fmt: str, im: Any | None = None) -> dict:
    """Encoder settings for re-encoding an edited image without visible loss."""
    fmt = ops.norm_fmt(fmt)
    if fmt == "jpg":
        if meta.fmt == "jpg" and meta.qtables and (im is None or im.mode in ("RGB", "L", "RGBA", "LA", "P")):
            return {"qtables": meta.qtables, "subsampling": meta.subsampling}
        return {"quality": max(_quality_for(ctx, "jpg"), 90)}
    if fmt == "webp":
        if meta.fmt == "webp" and ops.webp_is_lossless(f.path):
            return {"lossless": True}
        return {"quality": max(_quality_for(ctx, "webp"), 90)}
    if fmt == "avif":
        return {"quality": max(_quality_for(ctx, "avif"), 80)}
    if fmt == "heic":
        return {"quality": 88}
    if fmt == "jxl":
        return {"quality": 92}
    return {}


def _exif_set_dims(meta: ops.Meta, size: tuple[int, int]) -> None:
    """Refresh the EXIF pixel dimensions after a resize/crop (best effort)."""
    if not meta.exif:
        return
    try:
        from PIL import Image

        ex = Image.Exif()
        ex.load(meta.exif)
        sub = ex.get_ifd(0x8769)
        if 0xA002 in sub or 0xA003 in sub:
            sub[0xA002] = int(size[0])
            sub[0xA003] = int(size[1])
            meta.exif = ex.tobytes()
    except Exception:
        pass


def _save_same(ctx: Context, f: FileInfo, im: Any, meta: ops.Meta, suffix: str | None, *,
               fmt: str | None = None, keep_metadata: bool | None = None, replace: bool = True,
               size_before: int | None = None, message: str = "") -> Result:
    """Write an edited image next to the source (same format when possible) and build the result."""
    fmt = fmt or same_format(f, alpha=ops.has_alpha(im))
    if fmt != _fmt(f) and _fmt(f) in _WRITABLE:
        _warn_key(ctx, "img.warn.format_changed", fmt=fmt.upper())
    out = ctx.output_path(f, ext=_out_ext(fmt), suffix=suffix)
    _write(ctx, im, meta, out, fmt, keep_metadata=keep_metadata, **_edit_kwargs(ctx, f, meta, fmt, im))
    final = ctx.replace_original(f, out) if replace else out
    return ctx.result([final], message=message, size_before=size_before)


# ----------------------------------------------------------------------------------- file order

def ordered_files(files: Sequence[FileInfo], order: str, ctx: Context | None = None) -> list[FileInfo]:
    """``name`` (natural sort), ``date`` (EXIF capture date, then mtime) or ``selection`` order."""
    items = list(files)
    if order == "selection":
        return items
    if order == "date":
        def key(f: FileInfo) -> tuple:
            src = ctx.original(f) if ctx is not None else f
            dt = ops.exif_datetime(f.path)
            ts = dt.timestamp() if dt else (src.mtime or 0.0)
            return (ts, util.natural_key(f.name))
        return sorted(items, key=key)
    return util.natural_sorted(items, key=lambda f: f.name)


def _order_param() -> Param:
    return P.select("order", "img.prm.order", options=[("name", "img.opt.order.name"),
                                                       ("date", "img.opt.order.date"),
                                                       ("selection", "img.opt.order.selection")], default="name")


# ====================================================================================== SVG render

def _svg_size(ctx: Context, f: FileInfo) -> tuple[float, float]:
    try:
        size = ops.svg_intrinsic_size(ops.svg_text(f.path))
    except (OSError, UserError):
        size = None
    if size is None and f.width and f.height:
        size = (float(f.width), float(f.height))
    return size or (512.0, 512.0)


def _render_svg(ctx: Context, f: FileInfo, out: Path, *, long_edge: int | None = None,
                width: int | None = None, height: int | None = None, min_edge: int | None = None,
                background: str | None = None) -> Path:
    """Render an SVG to PNG: rsvg-convert → resvg → ImageMagick → CairoSVG → Inkscape."""
    iw, ih = _svg_size(ctx, f)
    if width or height:
        if width and height:
            W, H = int(width), int(height)
        elif width:
            W, H = int(width), int(round(int(width) * ih / iw))
        else:
            W, H = int(round(int(height) * iw / ih)), int(height)
    else:
        edge = long_edge or max(iw, ih)
        if min_edge and edge < min_edge and not long_edge:
            edge = min_edge
        s = edge / max(iw, ih)
        W, H = int(round(iw * s)), int(round(ih * s))
    W = max(1, min(ops.MAX_SIDE, W))
    H = max(1, min(ops.MAX_SIDE, H))
    src = f.path
    bg = background
    errors: list[str] = []
    ctx.check_cancel()
    if tools.have("rsvg-convert"):
        args = ["rsvg-convert", "-w", str(W), "-h", str(H), "-f", "png", "-o", out]
        if bg:
            args += ["-b", bg]
        try:
            ctx.run([*args, src], timeout=300)
            if out.exists() and out.stat().st_size:
                return out
        except ToolError as exc:
            errors.append(exc.details or str(exc))
    if tools.have("resvg"):
        args = ["resvg", "-w", str(W), "-h", str(H)]
        if bg:
            args += ["--background", bg]
        try:
            ctx.run([*args, src, out], timeout=300)
            if out.exists() and out.stat().st_size:
                return out
        except ToolError as exc:
            errors.append(exc.details or str(exc))
    if tools.have("magick"):
        link = _link_input(ctx, src, "vector")
        density = max(72, min(2400, int(round(96 * W / max(1.0, iw)))))
        try:
            ctx.run(["magick", "-density", str(density), "-background", bg or "none", str(link), "-resize",
                     f"{W}x{H}!", f"png:{out}"], timeout=300)
            if out.exists() and out.stat().st_size:
                return out
        except ToolError as exc:
            errors.append(exc.details or str(exc))
    if tools.have("py:cairosvg"):
        try:
            import cairosvg  # type: ignore

            cairosvg.svg2png(url=src, write_to=str(out), output_width=W, output_height=H,
                             background_color=bg, unsafe=False)
            if out.exists() and out.stat().st_size:
                return out
        except Exception as exc:  # cairosvg raises many types
            errors.append(str(exc))
    if tools.have("inkscape"):
        try:
            args = ["inkscape", "--export-type=png", f"--export-filename={out}", "-w", str(W), "-h", str(H)]
            if bg:
                args.append(f"--export-background={bg}")
            ctx.run([*args, src], timeout=600)
            if out.exists() and out.stat().st_size:
                return out
        except ToolError as exc:
            errors.append(exc.details or str(exc))
    if errors:
        raise UserError("img.err.svg_render", details="\n".join(errors), name=f.name)
    raise MissingTool(["rsvg-convert"])


# ======================================================================================== CONVERT

_PAGE_SIZES = {"a4": (595.276, 841.89), "letter": (612.0, 792.0)}


def _bg_param(default: str = "#ffffff") -> Param:
    return P.color("background", "img.prm.background", default, help="img.prm.background.help")


def _quality_param(cfg_key: str | int, *, label: str = "img.prm.quality") -> Param:
    default = f"cfg:{cfg_key}" if isinstance(cfg_key, str) else cfg_key
    return P.slider("quality", label, default, min=1, max=100, step=1, unit="img.unit.pct")


def _expand_pages(ctx: Context, f: FileInfo) -> list[tuple[Any, ops.Meta]]:
    """Every page of a multi-page TIFF (oriented), or the single image."""
    im, meta = _load(ctx, f)
    if meta.n_frames <= 1 or f.animated or meta.fmt not in ("tiff",):
        return [(im, meta)]
    pages = [(im, meta)]
    for i in range(1, min(meta.n_frames, 2000)):
        ctx.check_cancel()
        try:
            pages.append(ops.load_oriented(f.path, f.name, frame=i))
        except UserError:
            break
    return pages


def _pdf_ready(ctx: Context, f: FileInfo, *, allow_alpha: bool) -> list[Path]:
    """Paths an embedding PDF tool can take as-is: original JPEGs, else lossless temp PNGs."""
    fmt = _fmt(f)
    if f.category == "image" and fmt == "jpg" and not f.animated:
        return [Path(f.path)]
    out: list[Path] = []
    if f.category == "svg":
        pages = [_load_any(ctx, f, size=2048)]
    else:
        pages = _expand_pages(ctx, f) if not f.animated else [_load(ctx, f)]
    for im, meta in pages:
        if not allow_alpha:
            im = ops.flatten(im) if ops.has_alpha(im) else im
        tmp = _tmp(ctx, "page.png")
        _encode_to(ctx, im, meta, tmp, "png", keep_metadata=False)
        out.append(tmp)
    return out


def _page_box(px: tuple[int, int], dpi: float | None, page: str, margin_mm: float) -> tuple[tuple[float, float],
                                                                                          tuple[float, float, float, float]]:
    """``(page size, image rect)`` in PDF points."""
    d = dpi if dpi and dpi > 1 else 72.0
    iw, ih = px[0] * 72.0 / d, px[1] * 72.0 / d
    if page not in _PAGE_SIZES:
        return (iw, ih), (0.0, 0.0, iw, ih)
    pw, ph = _PAGE_SIZES[page]
    if iw > ih and pw < ph:
        pw, ph = ph, pw          # landscape image → landscape page
    m = max(0.0, float(margin_mm)) * 72.0 / 25.4
    aw, ah = max(1.0, pw - 2 * m), max(1.0, ph - 2 * m)
    s = min(aw / iw, ah / ih)
    w, h = iw * s, ih * s
    x0, y0 = (pw - w) / 2, (ph - h) / 2
    return (pw, ph), (x0, y0, x0 + w, y0 + h)


def _images_to_pdf(ctx: Context, files: Sequence[FileInfo], part: Path, *, page: str = "image",
                   margin_mm: float = 10.0) -> None:
    """One page per image: img2pdf (lossless) → PyMuPDF → Pillow."""
    n = max(1, len(files))
    label = _label(ctx)
    if tools.have("img2pdf"):
        inputs: list[Path] = []
        for i, f in enumerate(files):
            ctx.check_cancel()
            ctx.progress(i / (n + 1), label)
            inputs.extend(_pdf_ready(ctx, f, allow_alpha=False))
        args: list[Any] = ["img2pdf", "-o", part]
        if page in _PAGE_SIZES:
            m = max(0.0, float(margin_mm))
            # --auto-orient swaps the page per image like _page_box does (landscape image →
            # landscape page); without it every image is fitted into a portrait sheet
            args += ["--pagesize", "A4" if page == "a4" else "Letter", "--auto-orient", "--fit", "into",
                     "--border", f"{m:g}mm:{m:g}mm"]
        ctx.progress(n / (n + 1), label)
        ctx.run([*args, "--", *inputs], timeout=1800)
        return
    from PIL import Image

    if tools.have("py:pymupdf"):
        import pymupdf  # type: ignore

        doc = pymupdf.open()
        try:
            for i, f in enumerate(files):
                ctx.check_cancel()
                ctx.progress(i / n, label)
                pages: list[tuple[Path, tuple[int, int], float | None]] = []
                if f.category == "image" and _fmt(f) == "jpg" and not f.animated:
                    im, meta = ops.load_oriented(f.path, f.name)
                    dpi = meta.dpi[0] if meta.dpi else None
                    if meta.orientation != 1:
                        tmp = _tmp(ctx, "page.jpg")
                        _encode_to(ctx, im, meta, tmp, "jpg", keep_metadata=False,
                                   **_edit_kwargs(ctx, f, meta, "jpg", im))
                        pages.append((tmp, im.size, dpi))
                    else:
                        pages.append((Path(f.path), im.size, dpi))
                else:
                    for p in _pdf_ready(ctx, f, allow_alpha=True):
                        with Image.open(p) as probe:
                            info_dpi = probe.info.get("dpi")
                            dpi = float(info_dpi[0]) if info_dpi and float(info_dpi[0]) > 1 else None
                            pages.append((p, probe.size, dpi))
                for path, px, dpi in pages:
                    (pw, ph), rect = _page_box(px, dpi, page, margin_mm)
                    pg = doc.new_page(width=pw, height=ph)
                    pg.insert_image(pymupdf.Rect(*rect), filename=str(path), keep_proportion=True)
            if doc.page_count == 0:
                raise UserError("err.no_output", name=part.name)
            doc.save(str(part), garbage=3, deflate=True)
        finally:
            doc.close()
        return

    sheets: list[Any] = []
    dpi_out = 150.0
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(i / n, label)
        pages = [_load_any(ctx, f, size=2048)] if f.category == "svg" else _expand_pages(ctx, f)
        for im, meta in pages:
            rgb = ops.flatten(ops.to_srgb(im, meta.icc))
            if page in _PAGE_SIZES:
                (pw, ph), rect = _page_box(rgb.size, meta.dpi[0] if meta.dpi else None, page, margin_mm)
                sw, sh = int(round(pw / 72 * dpi_out)), int(round(ph / 72 * dpi_out))
                canvas = Image.new("RGB", (sw, sh), (255, 255, 255))
                box = [int(round(v / 72 * dpi_out)) for v in rect]
                canvas.paste(ops.resize(rgb, (max(1, box[2] - box[0]), max(1, box[3] - box[1]))), (box[0], box[1]))
                sheets.append((canvas, dpi_out))
            else:
                sheets.append((rgb, meta.dpi[0] if meta.dpi else 72.0))
    if not sheets:
        raise UserError("err.no_output", name=part.name)
    first, rest = sheets[0][0], [s[0] for s in sheets[1:]]
    first.save(str(part), "PDF", save_all=True, append_images=rest, resolution=float(sheets[0][1]))


def _convert(ctx: Context, target: str, f: FileInfo | None = None) -> Path:
    """Convert one static image (or SVG for the mixed ring) to ``target``; returns the output path."""
    f = f or ctx.file
    target = ops.norm_fmt(target)
    if f.category == "svg":
        return _svg_convert(ctx, f, target)
    if target == "svg":
        return _vectorize(ctx, f, "color", conversion=True)
    if target == "pdf":
        out = ctx.output_path(f, ext="pdf")
        with atomic_target(out) as part:
            _images_to_pdf(ctx, [f], part, page="image")
        return out
    if target == "jxl" and _fmt(f) == "jpg" and tools.have("cjxl") and not ctx.pbool("lossless") \
            and ctx.p("quality") in (None, 90, "90"):
        out = ctx.output_path(f, ext="jxl")
        ctx.progress(None, _label(ctx))
        with atomic_target(out) as part:
            ctx.run(["cjxl", f.path, part, "--lossless_jpeg=1"], timeout=900)
        return out
    im, meta = _load(ctx, f)
    if meta.n_frames > 1 and meta.fmt == "tiff":
        _warn_key(ctx, "img.warn.first_page_only", n=meta.n_frames)
    bg = ops.parse_color(ctx.pstr("background") or "#ffffff")[:3]
    quality = ctx.pint("quality", _quality_for(ctx, target)) if target in _LOSSY else None
    out = ctx.output_path(f, ext=_out_ext(target))
    if max(im.size) > 2000 or target in ("avif", "heic", "jxl"):
        ctx.progress(None, _label(ctx))
    extra = None
    if target == "tiff" and ctx.pstr("compression") in ("tiff_adobe_deflate", "tiff_lzw", "raw"):
        extra = {"compression": None if ctx.pstr("compression") == "raw" else ctx.pstr("compression")}
    _write(ctx, im, meta, out, target, quality=quality, lossless=ctx.pbool("lossless"), bg=bg, extra=extra)
    return out


def _convert_action(target: str) -> Callable[[Context], Result]:
    def fn(ctx: Context) -> Result:
        out = _convert(ctx, target)
        return ctx.result([out])

    fn.__name__ = f"convert_{target}"
    fn.__doc__ = f"Convert to {target.upper()} (``<stem>.{_out_ext(target)}``)."
    return fn


_CONVERT_TABLE: list[tuple[str, str, int, list, list]] = [
    # target, mode, order, params, requires
    ("png", "instant", 10, [], ["py:PIL"]),
    ("jpg", "quick", 11, [_quality_param("image.jpeg_quality"), _bg_param()], ["py:PIL"]),
    ("webp", "quick", 12, [_quality_param("image.webp_quality"), P.bool("lossless", "img.prm.lossless", False)],
     ["py:PIL"]),
    ("avif", "quick", 13, [_quality_param("image.avif_quality")], [("avifenc", "py:PIL._avif", "magick")]),
    ("pdf", "instant", 14, [], ["py:PIL"]),
    ("jxl", "quick", 15, [_quality_param(90), P.bool("lossless", "img.prm.lossless", False)],
     [("py:pillow_jxl", "cjxl", "magick")]),
    ("tiff", "quick", 16, [P.select("compression", "img.prm.tiff_compression",
                                    options=[("tiff_lzw", "LZW"), ("tiff_adobe_deflate", "ZIP (Deflate)"),
                                             ("raw", "img.opt.compression.none")], default="tiff_lzw")], ["py:PIL"]),
    ("heic", "quick", 17, [_quality_param(75)], [("py:pillow_heif", "heif-enc", "magick")]),
    ("gif", "instant", 18, [], ["py:PIL"]),
    ("bmp", "quick", 19, [_bg_param()], ["py:PIL"]),
    ("ico", "instant", 20, [], ["py:PIL"]),
]

for _target, _mode, _order, _params, _req in _CONVERT_TABLE:
    action(id=f"image.convert.{_target}", label=_target.upper(), text=_target.upper(), icon="image",
           categories={"image"}, when=_not_target(_target), mode=_mode, params=_params, requires=_req,
           description=f"img.desc.convert.{_target}", order=_order)(_convert_action(_target))


@action(id="image.convert.svg", label="SVG", text="SVG", icon="polyline", categories={"image"},
        when=_not_target("svg"), mode="quick",
        params=[P.select("detail", "img.prm.detail", options=[("low", "img.opt.detail.low"),
                                                              ("medium", "img.opt.detail.medium"),
                                                              ("high", "img.opt.detail.high")], default="medium")],
        requires=[("vtracer", "py:vtracer", "potrace")], description="img.desc.convert.svg", order=21)
def convert_svg(ctx: Context) -> Result:
    """Trace to SVG (vtracer colour, else potrace black & white)."""
    return ctx.result([_vectorize(ctx, ctx.file, "color", conversion=True)])


def _convert_default(files: list[FileInfo]) -> str:
    return "image.convert.png" if files and _fmt(files[0]) == "jpg" else "image.convert.jpg"


# ======================================================================================= COMPRESS

_PRESET_Q = {
    "light": {"jpg": 90, "webp": 90, "avif": 70, "heic": 80, "jxl": 92},
    "balanced": {"jpg": 80, "webp": 75, "avif": 55, "heic": 65, "jxl": 80},
    "strong": {"jpg": 65, "webp": 60, "avif": 40, "heic": 50, "jxl": 65},
}
_PNGQUANT = {"balanced": "65-80", "strong": "40-65"}
_PNG_COLORS = {"balanced": 256, "strong": 128}
_GIFSICLE = {"light": ["-O3"], "balanced": ["-O3", "--lossy=60", "--colors", "192"],
             "strong": ["-O3", "--lossy=120", "--colors", "96"]}


def _shrink(im: Any, edge: int | None, meta: ops.Meta | None = None) -> Any:
    if not edge:
        return im
    size = ops.shrink_long_edge(im.size[0], im.size[1], edge)
    if size is None:
        return im
    if meta is not None:
        _exif_set_dims(meta, size)
    return ops.resize(im, size)


def _png_lossless(ctx: Context, src: Path, *, level: int = 2) -> Path:
    """oxipng (``-o level``) or Pillow ``optimize`` into a temp file."""
    tmp = _tmp(ctx, "opt.png")
    if tools.have("oxipng"):
        ctx.run(["oxipng", "-o", str(level), "-q", "--out", tmp, src], timeout=900)
        if tmp.exists():
            return tmp
    from PIL import Image, PngImagePlugin

    with Image.open(src) as im:
        im.load()
        info = PngImagePlugin.PngInfo()
        for key, value in (getattr(im, "text", None) or {}).items():
            try:
                info.add_text(str(key), str(value))
            except Exception:
                continue
        kw: dict[str, Any] = {"optimize": True, "pnginfo": info}
        for key in ("icc_profile", "dpi", "transparency", "exif"):
            if key in im.info:
                kw[key] = im.info[key]
        im.save(tmp, "PNG", **kw)
    return tmp


def _pil_quantize_png(ctx: Context, im: Any, meta: ops.Meta, colors: int) -> Path:
    from PIL import Image

    im = ops.normalize_mode(ops.to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if im.mode == "CMYK":
        im = ops.to_srgb(im, meta.icc)
    if im.mode == "RGBA":
        q = im.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
    else:
        q = im.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                                       dither=Image.Dither.FLOYDSTEINBERG)
    tmp = _tmp(ctx, "quant.png")
    kw: dict[str, Any] = {"optimize": True}
    if meta.icc and ops.icc_colorspace(meta.icc) == "RGB":
        kw["icc_profile"] = meta.icc
    if _keep_meta(ctx) and meta.exif:
        kw["exif"] = meta.exif
    q.save(tmp, "PNG", **kw)
    return tmp


def _compress_png(ctx: Context, f: FileInfo, preset: str) -> Path | None:
    src = Path(f.path)
    im = meta = None
    if preset == "strong":
        im, meta = _load(ctx, f)
        capped = _shrink(im, 2048, meta)
        if capped is not im:
            src = _tmp(ctx, "capped.png")
            _encode_to(ctx, capped, meta, src, "png")
            im = capped
    if preset == "light":
        return _png_lossless(ctx, src, level=2)
    if tools.have("pngquant"):
        tmp = _tmp(ctx, "quant.png")
        res = ctx.run(["pngquant", f"--quality={_PNGQUANT[preset]}", "--speed", "3", "--skip-if-larger", "--force",
                       "--output", tmp, "--", src], ok_codes=(0, 98, 99), timeout=900)
        if res.returncode == 0 and tmp.exists():
            return _png_lossless(ctx, tmp, level=2) if tools.have("oxipng") else tmp
        return _png_lossless(ctx, src, level=2)
    if im is None:
        im, meta = _load(ctx, f)
    return _pil_quantize_png(ctx, im, meta, _PNG_COLORS[preset])


def _compress_gif_static(ctx: Context, f: FileInfo, preset: str) -> Path:
    tmp = _tmp(ctx, "c.gif")
    if tools.have("gifsicle"):
        ctx.run(["gifsicle", *_GIFSICLE[preset], "-o", tmp, f.path], timeout=900)
        return tmp
    im, meta = _load(ctx, f)
    q = ops.gif_quantize(im, {"light": 255, "balanced": 192, "strong": 96}[preset])
    kw: dict[str, Any] = {"optimize": True}
    if "transparency" in q.info:
        kw["transparency"] = q.info["transparency"]
    q.save(tmp, "GIF", **kw)
    return tmp


def _compress_lossy(ctx: Context, f: FileInfo, fmt: str, preset: str) -> tuple[Path | None, str]:
    cap = 2048 if preset == "strong" else None
    if fmt == "jpg":
        q = _PRESET_Q[preset]["jpg"]
        if cap is None and tools.have("jpegoptim"):
            args = ["jpegoptim", f"-m{q}", "--all-progressive", "--stdout", "-q"]
            if not _keep_meta(ctx):
                args += ["--strip-exif", "--strip-iptc", "--strip-xmp", "--strip-com"]
            res = ctx.run([*args, f.path], text=False, timeout=600)
            data = res.stdout or b""
            if data[:2] == b"\xff\xd8":
                tmp = _tmp(ctx, "c.jpg")
                tmp.write_bytes(data)
                return tmp, "jpg"
        im, meta = _load(ctx, f)
        im = _shrink(im, cap, meta)
        tmp = _tmp(ctx, "c.jpg")
        _encode_to(ctx, im, meta, tmp, "jpg", quality=q)
        return tmp, "jpg"
    if fmt == "png":
        return _compress_png(ctx, f, preset), "png"
    if fmt == "gif":
        return _compress_gif_static(ctx, f, preset), "gif"
    if fmt in ("webp", "avif", "heic", "jxl"):
        im, meta = _load(ctx, f)
        im = _shrink(im, cap, meta)
        tmp = _tmp(ctx, f"c.{_out_ext(fmt)}")
        if fmt == "webp" and preset == "light" and ops.webp_is_lossless(f.path):
            _encode_to(ctx, im, meta, tmp, "webp", lossless=True, speed=6)
        else:
            _encode_to(ctx, im, meta, tmp, fmt, quality=_PRESET_Q[preset][fmt])
        return tmp, fmt
    if fmt in ("tiff", "bmp"):
        im, meta = _load(ctx, f)
        im = _shrink(im, cap, meta)
        if preset == "light":
            if fmt == "tiff":
                tmp = _tmp(ctx, "c.tiff")
                _encode_to(ctx, im, meta, tmp, "tiff", extra={"compression": "tiff_adobe_deflate"})
                return tmp, "tiff"
            tmp = _tmp(ctx, "c.png")
            _encode_to(ctx, im, meta, tmp, "png")
            _warn_key(ctx, "img.warn.format_changed", fmt="PNG")
            return tmp, "png"
        out_fmt = "webp" if ops.has_alpha(im) else "jpg"
        tmp = _tmp(ctx, f"c.{out_fmt}")
        _encode_to(ctx, im, meta, tmp, out_fmt, quality=_PRESET_Q[preset][out_fmt])
        _warn_key(ctx, "img.warn.format_changed", fmt=out_fmt.upper())
        return tmp, out_fmt
    raise UserError("err.not_supported")


def _compress_lossless(ctx: Context, f: FileInfo, fmt: str) -> tuple[Path | None, str]:
    if fmt == "jpg":
        tmp = _tmp(ctx, "c.jpg")
        if tools.have("jpegtran"):
            ctx.run(["jpegtran", "-copy", "all", "-optimize", "-progressive", "-outfile", tmp, f.path], timeout=600)
            return tmp, "jpg"
        if tools.have("jpegoptim"):
            res = ctx.run(["jpegoptim", "--all-progressive", "--stdout", "-q", f.path], text=False, timeout=600)
            if (res.stdout or b"")[:2] == b"\xff\xd8":
                tmp.write_bytes(res.stdout)
                return tmp, "jpg"
        raise MissingTool(["jpegtran"])
    if fmt == "png":
        return _png_lossless(ctx, Path(f.path), level=4), "png"
    if fmt == "gif":
        return _compress_gif_static(ctx, f, "light"), "gif"
    if fmt == "webp":
        if not ops.webp_is_lossless(f.path):
            return None, fmt
        im, meta = _load(ctx, f)
        tmp = _tmp(ctx, "c.webp")
        _encode_to(ctx, im, meta, tmp, "webp", lossless=True, speed=6)
        return tmp, "webp"
    if fmt == "tiff":
        im, meta = _load(ctx, f)
        tmp = _tmp(ctx, "c.tiff")
        _encode_to(ctx, im, meta, tmp, "tiff", extra={"compression": "tiff_adobe_deflate"})
        return tmp, "tiff"
    if fmt == "bmp":
        im, meta = _load(ctx, f)
        tmp = _tmp(ctx, "c.png")
        _encode_to(ctx, im, meta, tmp, "png")
        _warn_key(ctx, "img.warn.format_changed", fmt="PNG")
        return _png_lossless(ctx, tmp, level=4), "png"
    return None, fmt


def _compress_web(ctx: Context, f: FileInfo) -> tuple[Path, str]:
    im, meta = _load(ctx, f)
    im = _shrink(im, 1920, meta)
    if im.mode == "CMYK" or (meta.icc and ops.icc_colorspace(meta.icc) == "RGB"):
        im = ops.to_srgb(im, meta.icc)
    meta.icc = None
    meta.exif = ops.strip_gps_exif(meta.exif)
    meta.xmp = ops.xmp_strip_gps(meta.xmp)
    tmp = _tmp(ctx, "web.webp")
    _encode_to(ctx, im, meta, tmp, "webp", quality=80, icc=False)
    return tmp, "webp"


def _finish_compress(ctx: Context, f: FileInfo, produced: Path | None, out_fmt: str, before: int) -> Result:
    """Keep the result only when it is smaller than the original (files are never enlarged)."""
    after = produced.stat().st_size if produced is not None and produced.exists() else None
    if after is None or after >= before or after == 0:
        if ctx.total > 1:
            _warn_key(ctx, "img.warn.not_smaller", name=f.name)
        return ctx.result(kind="none", message=ctx.t("img.msg.not_smaller", name=f.name))
    out = ctx.output_path(f, ext=_out_ext(out_fmt), suffix="sfx.compressed")
    _deliver(ctx, produced, out)
    final = ctx.replace_original(f, out)
    pct = int(round((before - after) * 100.0 / before))
    return ctx.result([final], size_before=before, message=ctx.t("img.msg.saved", pct=pct))


def _compress(ctx: Context, preset: str) -> Result:
    f = ctx.file
    fmt = _fmt(f)
    before = ctx.original(f).size or f.size or os.path.getsize(f.path)
    ctx.progress(None, _label(ctx))
    if preset == "web":
        produced, out_fmt = _compress_web(ctx, f)
    elif preset == "lossless":
        produced, out_fmt = _compress_lossless(ctx, f, fmt)
    else:
        produced, out_fmt = _compress_lossy(ctx, f, fmt, preset)
    return _finish_compress(ctx, f, produced, out_fmt, before)


def _compress_action(preset: str) -> Callable[[Context], Result]:
    def fn(ctx: Context) -> Result:
        return _compress(ctx, preset)

    fn.__name__ = f"compress_{preset}"
    fn.__doc__ = f"Compress ({preset} preset); files that cannot shrink are reported, not enlarged."
    return fn


for _preset, _icon, _order in (("balanced", "compress", 30), ("light", "density_small", 31),
                               ("strong", "density_large", 32), ("web", "public", 33), ("lossless", "verified", 34)):
    action(id=f"image.compress.{_preset}", label=f"img.act.compress.{_preset}", icon=_icon, categories={"image"},
           when=_any(_compressible), mode="instant", replace_ok=True, requires=["py:PIL"],
           description=f"img.desc.compress.{_preset}", order=_order)(_compress_action(_preset))


_TARGET_OPTIONS = [(100, "100 KB"), (200, "200 KB"), (500, "500 KB"), (1000, "1 MB"), (2000, "2 MB"),
                   (5000, "5 MB"), ("custom", "img.opt.target.custom")]


def _target_bytes(ctx: Context) -> int:
    choice = ctx.p("target", 500)
    if str(choice) == "custom":
        kb = ctx.pfloat("custom_kb", 0)
    else:
        try:
            kb = float(choice)
        except (TypeError, ValueError):
            kb = 500.0
    if kb <= 0:
        raise UserError("err.invalid_param", param=ctx.t("img.prm.target"))
    return int(kb * 1000)


def _encode_bytes(ctx: Context, im: Any, meta: ops.Meta, fmt: str, quality: int) -> bytes:
    try:
        return ops.encode(im, fmt, quality=quality, meta=meta, keep_metadata=_keep_meta(ctx))
    except ops.FormatUnavailable:
        tmp = _tmp(ctx, f"t.{_out_ext(fmt)}")
        _save_cli(ctx, im, meta, tmp, fmt, quality, False)
        return tmp.read_bytes()


@action(id="image.compress.target", label="img.act.compress.target", icon="straighten", categories={"image"},
        when=_any(_compressible), mode="dialog", dialog="form", replace_ok=True, requires=["py:PIL"],
        params=[P.select("target", "img.prm.target", options=_TARGET_OPTIONS, default=500),
                P.int("custom_kb", "img.prm.custom_kb", 300, min=5, max=1_000_000, unit="img.unit.kb",
                      visible_if={"target": "custom"})],
        description="img.desc.compress.target", order=35)
def compress_target(ctx: Context) -> Result:
    """Reach a target file size: binary search on quality (lossy formats) or colours (PNG), then
    90 % downscale steps."""
    f = ctx.file
    target = _target_bytes(ctx)
    before = ctx.original(f).size or f.size
    if before <= target:
        return ctx.result(kind="none", message=ctx.t("img.msg.below_target", size=util.human_size(before, ctx.lang)))
    fmt = _fmt(f)
    im, meta = _load(ctx, f)
    out_fmt = fmt if fmt in ("jpg", "webp", "avif", "heic", "jxl", "png") else ("webp" if ops.has_alpha(im) else "jpg")
    if out_fmt != fmt:
        _warn_key(ctx, "img.warn.format_changed", fmt=out_fmt.upper())
    label = _label(ctx)
    w, h = im.size
    best: bytes | None = None
    scale = 1.0
    passes = 0
    max_passes = 60
    for _attempt in range(16):
        ctx.check_cancel()
        img = im if scale >= 0.999 else ops.resize(im, (max(1, round(w * scale)), max(1, round(h * scale))))
        if out_fmt == "png":
            found = None
            for colors in (256, 128, 64, 32, 16):
                ctx.check_cancel()
                passes += 1
                ctx.progress(min(0.95, passes / 30.0), label)
                from PIL import Image

                buf = io.BytesIO()
                q = ops.normalize_mode(ops.to_8bit(img))
                q = (q.quantize(colors=colors, method=Image.Quantize.FASTOCTREE) if q.mode == "RGBA"
                     else q.convert("RGB").quantize(colors=colors))
                q.save(buf, "PNG", optimize=True)
                data = buf.getvalue()
                if best is None or len(data) < len(best):
                    best = data
                if len(data) <= target:
                    found = data
                    break
            if found is not None:
                best = found
                break
        else:
            passes += 1
            ctx.progress(min(0.95, passes / 30.0), label)
            low = _encode_bytes(ctx, img, meta, out_fmt, 10)
            if best is None or len(low) < len(best):
                best = low
            if len(low) <= target:
                lo, hi, good = 10, 95, low
                for _ in range(7):
                    if lo >= hi:
                        break
                    ctx.check_cancel()
                    mid = (lo + hi + 1) // 2
                    passes += 1
                    ctx.progress(min(0.95, passes / 30.0), label)
                    data = _encode_bytes(ctx, img, meta, out_fmt, mid)
                    if len(data) <= target:
                        good, lo = data, mid
                    else:
                        hi = mid - 1
                best = good
                break
        if passes > max_passes or min(w * scale, h * scale) < 16:
            break
        ratio = target / float(len(best)) if best else 0.5
        scale *= max(0.5, min(0.9, math.sqrt(max(0.01, ratio)) * 0.97))
    if best is None or len(best) >= before:
        return _finish_compress(ctx, f, None, out_fmt, before)
    if len(best) > target:
        _warn_key(ctx, "img.warn.target_not_reached", size=util.human_size(len(best), ctx.lang))
    if scale < 0.999:
        _warn_key(ctx, "img.warn.downscaled", pct=int(round(scale * 100)))
    tmp = _tmp(ctx, f"target.{_out_ext(out_fmt)}")
    tmp.write_bytes(best)
    return _finish_compress(ctx, f, tmp, out_fmt, before)


# ========================================================================================= RESIZE

def _resize_with(ctx: Context, compute: Callable[[int, int], ops.ResizePlan | None], suffix: str) -> Result:
    f = ctx.file
    im, meta = _load(ctx, f)
    plan = compute(*im.size)
    if plan is None:
        if ctx.total > 1:
            _warn_key(ctx, "img.warn.already_small", name=f.name)
        return ctx.result(kind="none", message=ctx.t("img.msg.already_small", w=im.size[0], h=im.size[1]))
    if max(im.size) > 3000:
        ctx.progress(None, _label(ctx))
    out_im = ops.apply_plan(im, plan)
    _exif_set_dims(meta, out_im.size)
    fmt = same_format(f, alpha=ops.has_alpha(out_im))
    out = ctx.output_path(f, ext=_out_ext(fmt), suffix=suffix)
    _write(ctx, out_im, meta, out, fmt, **_edit_kwargs(ctx, f, meta, fmt, out_im))
    final = ctx.replace_original(f, out)
    return ctx.result([final], message=ctx.t("img.msg.resized", w=out_im.size[0], h=out_im.size[1]))


def _resize_edge(edge: int) -> Callable[[Context], Result]:
    def fn(ctx: Context) -> Result:
        def plan(w: int, h: int) -> ops.ResizePlan | None:
            size = ops.shrink_long_edge(w, h, edge)
            return ops.ResizePlan(size) if size else None

        return _resize_with(ctx, plan, f"{edge}px")

    fn.__name__ = f"resize_{edge}"
    fn.__doc__ = f"Long edge {edge} px (shrink only)."
    return fn


def _resize_pct(pct: int) -> Callable[[Context], Result]:
    def fn(ctx: Context) -> Result:
        return _resize_with(ctx, lambda w, h: ops.ResizePlan(ops.scale_pct(w, h, pct)), f"{pct}%")

    fn.__name__ = f"resize_{pct}pct"
    fn.__doc__ = f"Scale to {pct} %."
    return fn


for _edge, _order in ((1920, 40), (2560, 41), (1280, 42), (3840, 43)):
    action(id=f"image.resize.{_edge}", label=f"{_edge} px", text=str(_edge), icon="photo_size_select_large",
           categories={"image"}, when=_STATIC, mode="instant", replace_ok=True, requires=["py:PIL"],
           description="img.desc.resize.edge", order=_order)(_resize_edge(_edge))

for _pct, _order in ((50, 44), (25, 45)):
    action(id=f"image.resize.{_pct}pct", label=f"{_pct} %", text=f"{_pct}%", icon="photo_size_select_small",
           categories={"image"}, when=_STATIC, mode="instant", replace_ok=True, requires=["py:PIL"],
           description="img.desc.resize.pct", order=_order)(_resize_pct(_pct))

_FIT_OPTIONS = [("fit", "img.opt.fit.fit"), ("fill", "img.opt.fit.fill"), ("stretch", "img.opt.fit.stretch")]


@action(id="image.resize.custom", label="img.act.resize.custom", icon="aspect_ratio", categories={"image"},
        when=_STATIC, mode="dialog", dialog="form", replace_ok=True, requires=["py:PIL"],
        params=[P.int("width", "img.prm.width", 1600, min=0, max=ops.MAX_SIDE, help="img.prm.size.help"),
                P.int("height", "img.prm.height", 0, min=0, max=ops.MAX_SIDE),
                P.select("unit", "img.prm.unit", options=[("px", "img.unit.px"), ("pct", "img.unit.pct")],
                         default="px"),
                P.select("fit", "img.prm.fit", options=_FIT_OPTIONS, default="fit", help="img.prm.fit.help"),
                P.bool("upscale", "img.prm.upscale", False)],
        description="img.desc.resize.custom", order=46)
def resize_custom(ctx: Context) -> Result:
    """Custom size: fit / fill (cover + centre crop) / stretch; px or percent; optional enlargement."""
    tw, th = max(0, ctx.pint("width", 0)), max(0, ctx.pint("height", 0))
    unit = ctx.pstr("unit", "px")
    mode = ctx.pstr("fit", "fit")
    upscale = ctx.pbool("upscale", False)
    if tw <= 0 and th <= 0:
        raise UserError("img.err.no_size")

    def plan(w: int, h: int) -> ops.ResizePlan | None:
        if unit == "pct":
            pw = tw or th
            ph = th or tw
            nw, nh = max(1, round(w * pw / 100.0)), max(1, round(h * ph / 100.0))
            if not upscale and (nw > w or nh > h):
                return None
            return None if (nw, nh) == (w, h) else ops.ResizePlan((nw, nh))
        return ops.plan_resize(w, h, tw, th, mode if (tw and th) or mode == "fit" else "fit", upscale=upscale)

    suffix = f"{tw or th}%" if unit == "pct" else (f"{tw}×{th}" if tw and th else f"{tw or th}px")
    return _resize_with(ctx, plan, suffix)


def _social(preset: str) -> Callable[[Context], Result]:
    tw, th = ops.SOCIAL_PRESETS[preset]

    def fn(ctx: Context) -> Result:
        return _resize_with(ctx, lambda w, h: ops.plan_resize(w, h, tw, th, "fill", upscale=True), f"{tw}×{th}")

    fn.__name__ = f"resize_{preset}"
    fn.__doc__ = f"Social preset {preset}: {tw}×{th} (cover + centre crop)."
    return fn


_SOCIAL_LABELS = {"ig_portrait": ("Instagram 4:5", "1080×1350"), "ig_square": ("Instagram 1:1", "1080×1080"),
                  "story": ("img.act.social.story", "1080×1920"), "yt_thumb": ("img.act.social.yt", "1280×720"),
                  "x_post": ("X", "1600×900"), "linkedin": ("LinkedIn", "1200×627")}
for _i, (_preset, (_lbl, _txt)) in enumerate(_SOCIAL_LABELS.items()):
    action(id=f"image.resize.social.{_preset}", label=_lbl, icon="groups", categories={"image"}, when=_STATIC,
           mode="instant", requires=["py:PIL"], description="img.desc.resize.social", order=50 + _i)(_social(_preset))


def _monitor_size(ctx: Context) -> tuple[int, int]:
    res = ctx.run(["hyprctl", "-j", "monitors"], timeout=10)
    try:
        mons = json.loads(res.stdout or "[]")
    except ValueError:
        mons = []
    if not isinstance(mons, list) or not mons:
        raise UserError("img.err.no_monitor")
    mon = next((m for m in mons if isinstance(m, dict) and m.get("focused")), mons[0])
    try:
        w, h = int(mon.get("width", 0)), int(mon.get("height", 0))
        transform = int(mon.get("transform", 0) or 0)
    except (TypeError, ValueError):
        raise UserError("img.err.no_monitor") from None
    if transform % 2 == 1:
        w, h = h, w
    if w <= 0 or h <= 0:
        raise UserError("img.err.no_monitor")
    return w, h


@action(id="image.resize.social.monitor", label="img.act.social.monitor", icon="desktop_windows",
        categories={"image"}, when=_STATIC, mode="instant", requires=["py:PIL", "hyprctl"],
        description="img.desc.resize.monitor", order=56)
def resize_monitor(ctx: Context) -> Result:
    """Exactly the focused monitor's resolution (cover + centre crop), e.g. for a wallpaper."""
    tw, th = _monitor_size(ctx)
    return _resize_with(ctx, lambda w, h: ops.plan_resize(w, h, tw, th, "fill", upscale=True), f"{tw}×{th}")


# ============================================================================ animation helpers

def _anim_fmt_out(f: FileInfo) -> str:
    fmt = _fmt(f)
    return fmt if fmt in _ANIM_WRITABLE else "webp"


def _read_frames(ctx: Context, f: FileInfo, share: float = 0.4) -> ops.Frames:
    label = _label(ctx)
    return ops.read_frames(f.path, f.name, check=ctx.check_cancel,
                           progress=lambda p: ctx.progress(p * share, label))


def _write_frames(ctx: Context, f: FileInfo, frames: list[Any], durations: Sequence[int], loop: int | None,
                  suffix: str | None, *, fmt: str | None = None, quality: int | None = None,
                  ext_override: str | None = None, replace: bool = True) -> Result:
    fmt = fmt or _anim_fmt_out(f)
    out = ctx.output_path(f, ext=ext_override or _out_ext(fmt), suffix=suffix)
    ctx.progress(0.9, _label(ctx))
    with atomic_target(out) as part:
        ops.save_animation(frames, durations, part, fmt, loop=loop, quality=quality or 90)
    final = ctx.replace_original(f, out) if replace else out
    return ctx.result([final])


def _anim_map(ctx: Context, f: FileInfo, fn: Callable[[Any], Any], suffix: str | None) -> Result:
    """Apply ``fn`` to every frame of an animation and write it back in the same format."""
    fr = _read_frames(ctx, f)
    label = _label(ctx)
    out_frames = []
    n = len(fr.frames)
    for i, frame in enumerate(fr.frames):
        ctx.check_cancel()
        out_frames.append(fn(frame))
        ctx.progress(0.4 + 0.5 * (i + 1) / n, label)
    return _write_frames(ctx, f, out_frames, fr.durations, fr.loop, suffix)


def _gifsicle(ctx: Context, f: FileInfo, args: Sequence[str], suffix: str | None, *, replace: bool = True,
              compare: bool = False) -> Result | None:
    """Run gifsicle on a GIF (lossless frame operations); ``None`` when gifsicle is missing."""
    if not tools.have("gifsicle") or _fmt(f) != "gif":
        return None
    ctx.progress(None, _label(ctx))
    tmp = _tmp(ctx, "g.gif")
    ctx.run(["gifsicle", *args, f.path, "-o", tmp], timeout=1800)
    if compare:
        before = ctx.original(f).size or f.size
        return _finish_compress(ctx, f, tmp, "gif", before)
    out = ctx.output_path(f, ext="gif", suffix=suffix)
    _deliver(ctx, tmp, out)
    final = ctx.replace_original(f, out) if replace else out
    return ctx.result([final])


# =========================================================================================== CROP

def _raw_rect_to_decoded(ctx: Context, f: FileInfo, rect: dict, size: tuple[int, int]) -> dict:
    """A rectangle drawn on the preview of a RAW → pixels of the picture the action decoded.

    The dialog gets exiftool's size, while darktable-cli's output can differ by some pixels and, when
    magick has no libraw, the decoders fall back to the much smaller embedded preview."""
    from .. import preview

    try:
        ref = preview.image(ctx.original(f).path)
        rw, rh = int(ref["width"]), int(ref["height"])
    except (UserError, OSError, KeyError, TypeError, ValueError) as exc:
        log.debug("no reference size for the crop of %s: %s", f.name, exc)
        return rect
    if rw <= 0 or rh <= 0 or (rw, rh) == tuple(size):
        return rect
    sx, sy = size[0] / rw, size[1] / rh
    return {"x": rect["x"] * sx, "y": rect["y"] * sy, "w": rect["w"] * sx, "h": rect["h"] * sy}


@action(id="image.crop.crop", label="img.act.crop", icon="crop", categories={"image"}, mode="dialog", dialog="crop",
        params=[P.rect("rect", "img.prm.rect", required=True),
                P.bool("lossless", "img.prm.lossless_crop", True, help="img.prm.lossless_crop.help")],
        replace_ok=True, requires=["py:PIL"], description="img.desc.crop", order=60)
def crop(ctx: Context) -> Result:
    """Crop to ``rect`` (original pixels, EXIF-oriented; ``per_file`` for multi-file edits).

    JPEG: lossless ``jpegtran -crop`` when the corner falls on the iMCU grid, else a re-encode with
    the source quantisation tables.  Animations: every frame (gifsicle for GIF)."""
    f = ctx.file
    rect = parse_rect(_per_file(ctx, f, "rect"))
    if rect is None:
        raise UserError("img.err.bad_rect")
    if f.animated:
        w, h = f.width or 0, f.height or 0
        if not (w and h):
            with ops.open_image(f.path, f.name) as probe:
                w, h = probe.size
        box = ops.clamp_rect(rect, w, h)
        if box is None:
            raise UserError("img.err.bad_rect")
        if box == (0, 0, w, h):
            return ctx.result(kind="none", message=ctx.t("img.msg.crop_nothing"))
        res = _gifsicle(ctx, f, ["--crop", f"{box[0]},{box[1]}-{box[2]},{box[3]}"], "sfx.cropped")
        return res or _anim_map(ctx, f, lambda fr: fr.crop(box), "sfx.cropped")
    im, meta = _load(ctx, f)
    if _fmt(f) in RAW_FORMATS:
        rect = _raw_rect_to_decoded(ctx, f, rect, im.size)
    box = ops.clamp_rect(rect, *im.size)
    if box is None:
        raise UserError("img.err.bad_rect")
    if box == (0, 0) + im.size:
        return ctx.result(kind="none", message=ctx.t("img.msg.crop_nothing"))
    fmt = same_format(f, alpha=ops.has_alpha(im))
    if fmt == "jpg" and meta.fmt == "jpg" and ctx.pbool("lossless", True) and tools.have("jpegtran"):
        raw = ops.map_rect_to_raw(box, meta.size, meta.orientation)
        mx, my = ops.jpeg_imcu(meta)
        if raw[0] % mx == 0 and raw[1] % my == 0:
            out = ctx.output_path(f, ext="jpg", suffix="sfx.cropped")
            geometry = f"{raw[2] - raw[0]}x{raw[3] - raw[1]}+{raw[0]}+{raw[1]}"
            try:
                with atomic_target(out) as part:
                    ctx.run(["jpegtran", "-copy", "all", "-crop", geometry, "-outfile", part, f.path], timeout=300)
                return ctx.result([ctx.replace_original(f, out)])
            except ToolError as exc:
                log.debug("lossless crop failed: %s", exc.details)
    cropped = im.crop(box)
    _exif_set_dims(meta, cropped.size)
    return _save_same(ctx, f, cropped, meta, "sfx.cropped", fmt=fmt)


# =========================================================================================== EDIT

def _jpeg_lossless(ctx: Context, f: FileInfo, op: str) -> Path | None:
    """``jpegtran -perfect`` transform composed with the EXIF orientation (which is reset to 1)."""
    if not tools.have("jpegtran") or _fmt(f) != "jpg":
        return None
    try:
        with ops.open_image(f.path, f.name) as probe:
            meta = ops.capture_meta(probe)
    except UserError:
        return None
    total = ops.compose_ops(ops.orientation_op(meta.orientation), op)
    tmp = _tmp(ctx, "t.jpg")
    try:
        ctx.run(["jpegtran", "-copy", "all", "-perfect", *ops.jpegtran_args(total), "-outfile", tmp, f.path],
                timeout=300)
    except ToolError as exc:
        log.debug("jpegtran -perfect failed (%s); re-encoding", exc.details)
        return None
    if meta.orientation != 1:
        try:
            tmp.write_bytes(ops.jpeg_rewrite(tmp.read_bytes(), exif=meta.exif))
        except ValueError:
            return None
    return tmp


def _transform(ctx: Context, op: str, suffix: str) -> Result:
    f = ctx.file
    if f.animated:
        gif_args = {"rot90": ["--rotate-90"], "rot270": ["--rotate-270"], "rot180": ["--rotate-180"],
                    "flip_h": ["--flip-horizontal"], "flip_v": ["--flip-vertical"]}[op]
        res = _gifsicle(ctx, f, gif_args, suffix)
        return res or _anim_map(ctx, f, lambda fr: ops.pil_transpose(fr, op), suffix)
    lossless = _jpeg_lossless(ctx, f, op)
    if lossless is not None:
        out = ctx.output_path(f, ext="jpg", suffix=suffix)
        _deliver(ctx, lossless, out)
        return ctx.result([ctx.replace_original(f, out)])
    im, meta = _load(ctx, f)
    return _save_same(ctx, f, ops.pil_transpose(im, op), meta, suffix)


def _transform_action(op: str, suffix: str) -> Callable[[Context], Result]:
    def fn(ctx: Context) -> Result:
        return _transform(ctx, op, suffix)

    fn.__name__ = f"transform_{op}"
    fn.__doc__ = f"{op} (lossless jpegtran for JPEG, gifsicle for GIF)."
    return fn


for _id, _op, _icon, _sfx, _order in (("rotate_cw", "rot90", "rotate_right", "sfx.rotated", 70),
                                      ("rotate_ccw", "rot270", "rotate_left", "sfx.rotated", 71),
                                      ("flip_h", "flip_h", "flip", "sfx.flipped", 72),
                                      ("flip_v", "flip_v", "swap_vert", "sfx.flipped", 73)):
    action(id=f"image.edit.{_id}", label=f"img.act.{_id}", icon=_icon, categories={"image"}, mode="instant",
           replace_ok=True, requires=["py:PIL"], description=f"img.desc.{_id}", order=_order)(
        _transform_action(_op, _sfx))


@action(id="image.edit.grayscale", label="img.act.grayscale", icon="filter_b_and_w", categories={"image"},
        when=_STATIC, mode="instant", replace_ok=True, requires=["py:PIL"], description="img.desc.grayscale",
        order=74)
def grayscale(ctx: Context) -> Result:
    """Black & white copy (alpha kept)."""
    from PIL import ImageOps

    f = ctx.file
    im, meta = _load(ctx, f)
    base = ops.normalize_mode(ops.to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if base.mode == "CMYK":
        base = ops.to_srgb(base, meta.icc)
    alpha = base.getchannel("A") if base.mode in ("RGBA", "LA") else None
    gray = ImageOps.grayscale(base.convert("RGB"))
    if alpha is not None:
        gray = gray.convert("LA")
        gray.putalpha(alpha)
    meta.icc = None
    return _save_same(ctx, f, gray, meta, "sfx.grayscale")


@action(id="image.edit.invert", label="img.act.invert", icon="invert_colors", categories={"image"}, when=_STATIC,
        mode="instant", replace_ok=True, requires=["py:PIL"], description="img.desc.invert", order=75)
def invert(ctx: Context) -> Result:
    """Negative (alpha kept)."""
    from PIL import ImageOps

    f = ctx.file
    im, meta = _load(ctx, f)
    base = ops.normalize_mode(ops.to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if base.mode == "CMYK":
        base = ops.to_srgb(base, meta.icc)
    alpha = base.getchannel("A") if base.mode in ("RGBA", "LA") else None
    gray = base.mode in ("L", "LA")
    inv = ImageOps.invert(base.convert("L" if gray else "RGB"))
    if alpha is not None:
        inv = inv.convert("LA" if gray else "RGBA")
        inv.putalpha(alpha)
    return _save_same(ctx, f, inv, meta, "img.sfx.inverted")


@action(id="image.edit.trim", label="img.act.trim", icon="crop_free", categories={"image"}, when=_STATIC,
        mode="quick", params=[P.slider("fuzz", "img.prm.fuzz", 5, min=0, max=50, unit="img.unit.pct")],
        replace_ok=True, requires=["py:PIL"], description="img.desc.trim", order=76)
def trim(ctx: Context) -> Result:
    """Auto-trim uniform (or transparent) borders, like ``magick -trim -fuzz 5%``."""
    f = ctx.file
    im, meta = _load(ctx, f)
    box = ops.trim_box(im, ctx.pint("fuzz", 5) / 100.0)
    if box is None:
        return ctx.result(kind="none", message=ctx.t("img.msg.nothing_to_trim"))
    out = im.crop(box)
    _exif_set_dims(meta, out.size)
    return _save_same(ctx, f, out, meta, "sfx.cropped")


@action(id="image.edit.adjust", label="img.act.adjust", icon="tune", categories={"image"}, when=_STATIC,
        mode="dialog", dialog="adjust", params=[P.adjust("adjust", "img.prm.adjust", required=True)],
        replace_ok=True, requires=["py:PIL"], description="img.desc.adjust", order=77)
def adjust(ctx: Context) -> Result:
    """Exposure, brightness, contrast, saturation, temperature, tint, sharpen, vignette (−100..100)."""
    f = ctx.file
    values = _per_file(ctx, f, "adjust")
    if not isinstance(values, dict) or not any(
            abs(float(v)) > 1e-9 for v in values.values() if isinstance(v, (int, float))):
        raise UserError("img.err.no_adjust")
    im, meta = _load(ctx, f)
    if max(im.size) > 3000:
        ctx.progress(None, _label(ctx))
    out = ops.adjust_image(im, values)
    if out.mode == "RGB" and meta.icc and ops.icc_colorspace(meta.icc) != "RGB":
        meta.icc = None
    return _save_same(ctx, f, out, meta, "sfx.edited")


def _annotate_with(ctx: Context, tool: str, work: Path, result: Path) -> bool:
    if tool == "satty":
        ctx.run(["satty", "--filename", work, "--output-filename", result], timeout=None)
    else:
        ctx.run(["swappy", "-f", work, "-o", result], timeout=None)
    return result.exists() and result.stat().st_size > 0


@action(id="image.edit.annotate", label="img.act.annotate", icon="draw", categories={"image"}, when=_STATIC,
        multi="single", mode="instant", requires=[("satty", "swappy")], description="img.desc.annotate", order=78)
def annotate(ctx: Context) -> Result:
    """Open a PNG working copy in satty (→ swappy → ``apps.image_editor``); the saved drawing becomes
    ``<stem> (annotato).png`` and the original is never touched."""
    f = ctx.file
    im, meta = _load(ctx, f)
    work = _tmp(ctx, "annotate.png")
    _encode_to(ctx, im, meta, work, "png", keep_metadata=False)
    result = _tmp(ctx, "annotated.png")
    ctx.progress(None, ctx.t("img.msg.annotating"))
    errors: list[str] = []
    ran = False
    for tool in ("satty", "swappy"):
        if not tools.have(tool):
            continue
        try:
            ran = True
            if _annotate_with(ctx, tool, work, result):
                out = ctx.output_path(f, ext="png", suffix="sfx.annotated")
                _deliver(ctx, result, out)
                return ctx.result([out])
            return ctx.result(kind="none", message=ctx.t("img.msg.annotate_cancelled"))
        except (ToolError, MissingTool) as exc:
            errors.append(f"{tool}: {exc.details or exc}")
            continue
    editor = str(_cfg(ctx, "apps.image_editor", "") or "").strip()
    if editor:
        import shlex

        argv = shlex.split(editor)
        out = ctx.output_path(f, ext="png", suffix="sfx.annotated")
        _deliver(ctx, work, out)
        if argv and util.detach([*argv, str(out)]):
            return ctx.result([out], message=ctx.t("img.msg.edit_in_app", app=os.path.basename(argv[0])))
        errors.append(editor)
    if errors or ran:
        raise UserError("img.err.annotate_failed", details="\n".join(errors))
    raise MissingTool(["satty"])


_FONT_CACHE: list[str | None] = []


def _font_path() -> str | None:
    """A bold sans TrueType font (fontconfig), cached."""
    if _FONT_CACHE:
        return _FONT_CACHE[0]
    found: str | None = None
    res = util.run_quiet(["fc-match", "-f", "%{file}", "sans-serif:bold"], timeout=5)
    if res is not None and res.returncode == 0 and res.stdout.strip() and os.path.isfile(res.stdout.strip()):
        found = res.stdout.strip()
    if found is None:
        for cand in ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/noto/NotoSans-Bold.ttf",
                     "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
                     "/usr/share/fonts/TTF/Roboto-Bold.ttf"):
            if os.path.isfile(cand):
                found = cand
                break
    _FONT_CACHE.append(found)
    return found


_POSITION_OPTIONS = [("tl", "img.opt.pos.tl"), ("t", "img.opt.pos.t"), ("tr", "img.opt.pos.tr"),
                     ("l", "img.opt.pos.l"), ("c", "img.opt.pos.c"), ("r", "img.opt.pos.r"),
                     ("bl", "img.opt.pos.bl"), ("b", "img.opt.pos.b"), ("br", "img.opt.pos.br")]


@action(id="image.edit.watermark", label="img.act.watermark", icon="branding_watermark", categories={"image"},
        when=_STATIC, mode="dialog", dialog="form",
        params=[P.select("kind", "img.prm.wm_kind", options=[("text", "img.opt.wm.text"), ("logo", "img.opt.wm.logo")],
                         default="text"),
                P.text("text", "img.prm.wm_text", default=lambda files: f"© {datetime.now().year}",
                       visible_if={"kind": "text"}),
                P.path("logo", "img.prm.wm_logo", "", visible_if={"kind": "logo"}, help="img.prm.wm_logo.help"),
                P.select("position", "img.prm.position", options=_POSITION_OPTIONS, default="br"),
                P.slider("opacity", "img.prm.opacity", 30, min=5, max=100, unit="img.unit.pct"),
                P.slider("scale", "img.prm.wm_scale", 20, min=2, max=100, unit="img.unit.pct",
                         help="img.prm.wm_scale.help"),
                P.bool("tile", "img.prm.tile", False),
                P.color("color", "img.prm.color", "#ffffff", visible_if={"kind": "text"})],
        replace_ok=True, requires=["py:PIL"], description="img.desc.watermark", order=79)
def watermark(ctx: Context) -> Result:
    """Text or logo watermark: 9-grid position, opacity, width in % of the image, optional tiling."""
    f = ctx.file
    kind = ctx.pstr("kind", "text")
    logo = None
    if kind == "logo":
        path = os.path.expanduser(ctx.pstr("logo").strip())
        if path.startswith("file://"):
            path = util.uri_to_path(path)
        if not path or not os.path.isfile(path):
            raise UserError("img.err.logo_missing")
        logo, _lm = ops.load_oriented(path, os.path.basename(path))
    elif not ctx.pstr("text").strip():
        raise UserError("err.missing_param", param=ctx.t("img.prm.wm_text"))
    im, meta = _load(ctx, f)
    out = ops.watermark_image(im, text=ctx.pstr("text"), logo=logo, position=ctx.pstr("position", "br"),
                              opacity=ctx.pfloat("opacity", 30), scale=ctx.pfloat("scale", 20),
                              tile=ctx.pbool("tile"), color=ctx.pstr("color", "#ffffff"), font_path=_font_path())
    return _save_same(ctx, f, out, meta, "sfx.watermarked")


# ===================================================================================== BACKGROUND

_REMBG_MODELS = [("isnet-general-use", "img.opt.rembg.isnet"), ("u2net", "img.opt.rembg.u2net"),
                 ("birefnet-general-lite", "img.opt.rembg.birefnet"), ("u2netp", "img.opt.rembg.u2netp"),
                 ("u2net_human_seg", "img.opt.rembg.human"), ("isnet-anime", "img.opt.rembg.anime")]
_REMBG_SESSIONS: dict[str, Any] = {}


def rembg_home() -> str:
    """Model folder for rembg: ``$REMBG_HOME`` / ``$U2NET_HOME`` when set, else ``$XDG_DATA_HOME/rembg``."""
    for var in ("REMBG_HOME", "U2NET_HOME"):
        value = os.environ.get(var, "")
        if value:
            return value
    return str(util.xdg_data_home() / "rembg")


def _rembg(ctx: Context, src: Path, out: Path, model: str, matting: bool) -> None:
    home = rembg_home()
    try:
        os.makedirs(home, exist_ok=True)
    except OSError:
        pass
    ctx.progress(None, ctx.t("img.msg.rembg"))
    if tools.have("rembg"):
        args: list[Any] = ["rembg", "i", "-m", model]
        if matting:
            args.append("-a")
        ctx.run([*args, src, out], env={"REMBG_HOME": home}, timeout=3600)
        return
    os.environ.setdefault("REMBG_HOME", home)
    try:
        from PIL import Image
        from rembg import new_session, remove  # type: ignore
    except ImportError:
        raise MissingTool(["rembg"]) from None
    try:
        session = _REMBG_SESSIONS.get(model)
        if session is None:
            session = new_session(model)
            _REMBG_SESSIONS[model] = session
        ctx.check_cancel()
        with Image.open(src) as im:
            result = remove(im, session=session, alpha_matting=matting)
        result.save(out, "PNG")
    except Cancelled:
        raise
    except Exception as exc:  # rembg/onnxruntime raise many types (network, model, runtime)
        raise UserError("img.err.rembg_failed", details=str(exc)) from None


@action(id="image.background.remove", label="img.act.bg_remove", icon="hide_image", categories={"image"},
        when=_STATIC, mode="quick",
        params=[P.select("model", "img.prm.rembg_model", options=_REMBG_MODELS, default="isnet-general-use",
                         help="img.prm.rembg_model.help"),
                P.bool("alpha_matting", "img.prm.alpha_matting", False),
                P.bool("crop", "img.prm.crop_subject", False)],
        requires=[("rembg", "py:rembg")], description="img.desc.bg_remove", order=80)
def bg_remove(ctx: Context) -> Result:
    """Remove the background with rembg (explicit model, ``REMBG_HOME``) → ``<stem> (senza sfondo).png``."""
    f = ctx.file
    im, meta = _load(ctx, f)
    src = _tmp(ctx, "rembg-in.png")
    _encode_to(ctx, ops.flatten(im) if ops.has_alpha(im) else im, meta, src, "png", keep_metadata=False)
    produced = _tmp(ctx, "rembg-out.png")
    model = ctx.pstr("model", "isnet-general-use")
    if model not in {m for m, _ in _REMBG_MODELS} and not re.fullmatch(r"[\w.-]+", model):
        model = "isnet-general-use"
    _rembg(ctx, src, produced, model, ctx.pbool("alpha_matting"))
    if not produced.exists():
        raise ToolError("err.no_output", name=produced.name)
    result, _m = ops.load_oriented(produced, f.name)
    result = result.convert("RGBA")
    if ctx.pbool("crop"):
        box = ops.alpha_bbox(result)
        if box:
            result = result.crop(box)
    if result.getchannel("A").getextrema()[1] == 0:
        raise UserError("img.err.no_subject")
    out = ctx.output_path(f, ext="png", suffix="sfx.no_background")
    _write(ctx, result, meta, out, "png")
    return ctx.result([out])


def _load_fill_image(value: Any) -> Any:
    if not value:
        return None
    path = str(value)
    if path.startswith("file://"):
        path = util.uri_to_path(path)
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise UserError("err.file_not_found", name=os.path.basename(path) or path)
    im, _meta = ops.load_oriented(path, os.path.basename(path))
    return im


@action(id="image.background.add", label="img.act.bg_add", icon="wallpaper", categories={"image"}, when=_STATIC,
        mode="dialog", dialog="background", params=[P.background("background", "img.prm.background_spec",
                                                                 required=True)],
        requires=["py:PIL"], description="img.desc.bg_add", order=81)
def bg_add(ctx: Context) -> Result:
    """Padding, rounded corners, shadow, ratio and colour / gradient / blurred / image fill
    (values in pixels of the original image)."""
    f = ctx.file
    spec = _per_file(ctx, f, "background")
    if not isinstance(spec, dict):
        raise UserError("img.err.bad_background")
    fill = spec.get("fill") if isinstance(spec.get("fill"), dict) else {}
    fill_image = _load_fill_image(fill.get("image")) if str(fill.get("type") or "") == "image" else None
    im, meta = _load(ctx, f)
    if max(im.size) > 3000:
        ctx.progress(None, _label(ctx))
    out_im = ops.compose_background(im, spec, fill_image=fill_image)
    src_fmt = _fmt(f)
    opaque = out_im.getchannel("A").getextrema()[0] == 255
    fmt = src_fmt if (opaque and src_fmt in _LOSSY and _can_write(src_fmt)) else "png"
    meta.exif = None if not _keep_meta(ctx) else meta.exif
    out = ctx.output_path(f, ext=_out_ext(fmt), suffix="sfx.background")
    _write(ctx, out_im, meta, out, fmt, **_edit_kwargs(ctx, f, meta, fmt, out_im))
    return ctx.result([out])


@action(id="image.background.flatten", label="img.act.bg_flatten", icon="format_color_fill", categories={"image"},
        when=_any(_maybe_alpha), mode="quick", params=[P.color("color", "img.prm.color", "#ffffff")],
        replace_ok=True, requires=["py:PIL"], description="img.desc.bg_flatten", order=82)
def bg_flatten(ctx: Context) -> Result:
    """Transparent → colour (opaque copy)."""
    f = ctx.file
    im, meta = _load(ctx, f)
    if not ops.has_alpha(im):
        return ctx.result(kind="none", message=ctx.t("img.msg.no_transparency"))
    color = ops.parse_color(ctx.pstr("color") or "#ffffff")
    out = ops.flatten(im, color[:3])
    return _save_same(ctx, f, out, meta, "sfx.flattened")


@action(id="image.background.white", label="img.act.bg_white", icon="texture", categories={"image"}, when=_STATIC,
        mode="quick", params=[P.slider("fuzz", "img.prm.fuzz", 15, min=1, max=60, unit="img.unit.pct"),
                              P.bool("edges_only", "img.prm.edges_only", True, help="img.prm.edges_only.help")],
        requires=["py:PIL"], description="img.desc.bg_white", order=83)
def bg_white(ctx: Context) -> Result:
    """White → transparent (soft edge; by default only white connected to the border)."""
    f = ctx.file
    im, meta = _load(ctx, f)
    if max(im.size) > 3000:
        ctx.progress(None, _label(ctx))
    out = ops.white_to_transparent(im, ctx.pint("fuzz", 15) / 100.0, edges_only=ctx.pbool("edges_only", True))
    if out.getchannel("A").getextrema()[0] == 255:
        return ctx.result(kind="none", message=ctx.t("img.msg.no_white"))
    src = _fmt(f)
    fmt = src if src in ("png", "webp", "tiff") else "png"
    return _save_same(ctx, f, out, meta, "img.sfx.transparent", fmt=fmt, replace=False)


@action(id="image.background.crop_subject", label="img.act.crop_subject", icon="center_focus_strong",
        categories={"image"}, when=_any(_maybe_alpha), mode="instant", replace_ok=True, requires=["py:PIL"],
        description="img.desc.crop_subject", order=84)
def crop_subject(ctx: Context) -> Result:
    """Crop to the bounding box of the visible (non-transparent) pixels."""
    f = ctx.file
    im, meta = _load(ctx, f)
    if not ops.has_alpha(im):
        raise UserError("img.err.no_alpha")
    box = ops.alpha_bbox(im)
    if box is None:
        raise UserError("img.err.no_subject")
    if box == (0, 0) + im.size:
        return ctx.result(kind="none", message=ctx.t("img.msg.crop_nothing"))
    out = im.crop(box)
    _exif_set_dims(meta, out.size)
    return _save_same(ctx, f, out, meta, "sfx.cropped")


# ======================================================================================== PRIVACY

_BLOCKED_GROUPS = {"file", "system", "composite", "exiftool"}
_BLOCKED_TAGS = {"filename", "directory", "testname", "hardlink", "symlink", "filepermissions", "fileuserid",
                 "filegroupid", "filecreatedate", "filemodifydate", "fileaccessdate"}


def _orientation_only_exif(exif: bytes | None) -> bytes | None:
    """EXIF holding only the Orientation tag of ``exif`` (``None`` when it is 1/absent)."""
    if not exif:
        return None
    try:
        from PIL import Image

        src = Image.Exif()
        src.load(exif)
        orient = int(src.get(0x0112, 1) or 1)
        if orient == 1:
            return None
        ex = Image.Exif()
        ex[0x0112] = orient
        return ex.tobytes()
    except Exception:
        return None


def _exiftool_strip(ctx: Context, f: FileInfo, mode: str) -> Path:
    work = _work_copy(ctx, f.path)
    raw = _fmt(f) in RAW_FORMATS
    if mode == "gps":
        args = ["-gps:all=", "-xmp:gps*="]
    elif raw:
        args = ["-gps:all=", "-xmp:all=", "-iptc:all=", "-comment="]
        _warn_key(ctx, "img.warn.raw_partial_strip")
    else:
        args = ["-all=", "-tagsfromfile", "@", "-icc_profile", "-orientation"]
    ctx.run(["exiftool", "-q", "-q", "-m", "-overwrite_original", *args, "--", work], timeout=300)
    return work


def _strip_fallback(ctx: Context, f: FileInfo, mode: str) -> Path:
    """Lossless JPEG/PNG segment rewriting; Pillow re-encode for other formats."""
    fmt = _fmt(f)
    if fmt in ("jpg", "png") and not f.animated:
        data = Path(f.path).read_bytes()
        if fmt == "jpg":
            exif, xmp = ops.read_jpeg_exif_xmp(data)
        else:
            exif, xmp = ops.read_png_exif_xmp(data)
        try:
            if mode == "gps":
                new_exif = ops.strip_gps_exif(exif) if exif else ops.KEEP
                new_xmp = ops.xmp_strip_gps(xmp) if xmp else ops.KEEP
                if fmt == "jpg":
                    out_data = ops.jpeg_rewrite(data, exif=new_exif, xmp=new_xmp)
                else:
                    out_data = ops.png_rewrite(data, exif=new_exif, xmp=new_xmp)
            else:
                keep_exif = _orientation_only_exif(exif)
                if fmt == "jpg":
                    out_data = ops.jpeg_rewrite(data, exif=keep_exif, xmp=None, drop_iptc=True, drop_comments=True,
                                                drop_other_app=True, drop_trailer=True)
                else:
                    out_data = ops.png_rewrite(data, exif=keep_exif, xmp=None, drop_text=True, drop_time=True)
        except ValueError as exc:
            raise UserError("err.damaged", details=str(exc)) from None
        tmp = _tmp(ctx, f"strip.{fmt}")
        tmp.write_bytes(out_data)
        return tmp
    _warn_key(ctx, "img.warn.reencoded")
    if f.animated:
        fr = _read_frames(ctx, f)
        out_fmt = _anim_fmt_out(f)
        tmp = _tmp(ctx, f"strip.{_out_ext(out_fmt)}")
        ops.save_animation(fr.frames, fr.durations, tmp, out_fmt, loop=fr.loop, quality=90)
        return tmp
    im, meta = _load(ctx, f)
    if mode == "gps":
        meta.exif = ops.strip_gps_exif(meta.exif)
        meta.xmp = ops.xmp_strip_gps(meta.xmp)
    else:
        meta.exif = None
        meta.xmp = None
    out_fmt = same_format(f, alpha=ops.has_alpha(im))
    tmp = _tmp(ctx, f"strip.{_out_ext(out_fmt)}")
    _encode_to(ctx, im, meta, tmp, out_fmt, keep_metadata=(mode == "gps"), **_edit_kwargs(ctx, f, meta, out_fmt, im))
    return tmp


def _same_bytes(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    import filecmp

    try:
        return filecmp.cmp(os.fspath(a), os.fspath(b), shallow=False)
    except OSError:
        return False


def _strip(ctx: Context, mode: str) -> Result:
    f = ctx.file
    before = ctx.original(f).size or f.size
    if tools.have("exiftool"):
        produced = _exiftool_strip(ctx, f, mode)
    else:
        produced = _strip_fallback(ctx, f, mode)
    if _same_bytes(produced, f.path):
        key = "img.msg.no_location" if mode == "gps" else "img.msg.no_metadata"
        return ctx.result(kind="none", message=ctx.t(key))
    ext = util.split_name(produced.name)[1] or f.ext
    suffix = "img.sfx.no_location" if mode == "gps" else "sfx.no_metadata"
    out = ctx.output_path(f, ext=ext, suffix=suffix)
    _deliver(ctx, produced, out)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=before)


@action(id="image.privacy.strip_gps", label="img.act.strip_gps", icon="location_off", categories={"image"},
        mode="instant", replace_ok=True, requires=["py:PIL"], description="img.desc.strip_gps", order=90)
def strip_gps(ctx: Context) -> Result:
    """Remove GPS position (EXIF GPS IFD and XMP GPS) — exiftool, else lossless JPEG/PNG rewriting."""
    return _strip(ctx, "gps")


@action(id="image.privacy.strip_all", label="img.act.strip_all", icon="privacy_tip", categories={"image"},
        mode="instant", replace_ok=True, requires=["py:PIL"], description="img.desc.strip_all", order=91)
def strip_all(ctx: Context) -> Result:
    """Remove all metadata, keeping the ICC profile and the orientation."""
    return _strip(ctx, "all")


@action(id="image.privacy.redact", label="img.act.redact", icon="ink_eraser", categories={"image"}, when=_STATIC,
        mode="dialog", dialog="redact",
        params=[P.boxes("boxes", "img.prm.boxes", required=True),
                P.color("color", "img.prm.redact_color", "#000000")],
        requires=["py:PIL"], description="img.desc.redact", order=92)
def redact(ctx: Context) -> Result:
    """Burn solid / blur / pixelate boxes into a copy; the output carries no metadata."""
    f = ctx.file
    boxes = _per_file(ctx, f, "boxes")
    if isinstance(boxes, dict):
        boxes = [boxes]
    if not isinstance(boxes, list):
        raise UserError("img.err.no_boxes")
    boxes = [b for b in boxes if isinstance(b, dict) and b.get("page") in (None, 0, "0", "")]
    if not boxes:
        raise UserError("img.err.no_boxes")
    im, meta = _load(ctx, f)
    out, used = ops.redact_image(im, boxes, color=ops.parse_color(ctx.pstr("color") or "#000000")[:3])
    if used & {"blur", "pixelate"}:
        _warn_key(ctx, "img.warn.redact_reversible")
    meta.exif = None
    meta.xmp = None
    return _save_same(ctx, f, out, meta, "sfx.redacted", keep_metadata=False, replace=False)


def _metadata_args(spec: dict, raw: bool) -> tuple[list[str], list[str]]:
    """exiftool arguments for a metadata dialog result; ``(args, skipped keys)``."""
    args: list[str] = []
    skipped: list[str] = []
    if spec.get("remove_all"):
        if raw:
            args += ["-gps:all=", "-xmp:all=", "-iptc:all=", "-comment="]
        else:
            args += ["-all=", "-tagsfromfile", "@", "-icc_profile", "-orientation"]

    def ok(key: str) -> bool:
        if not _KEY_RE.match(key):
            return False
        group_name, _, tag = key.rpartition(":")
        if group_name.lower() in _BLOCKED_GROUPS or (tag or key).lower() in _BLOCKED_TAGS:
            return False
        return True

    for key in spec.get("remove") or []:
        key = str(key).strip()
        if ok(key):
            args.append(f"-{key}=")
        elif key:
            skipped.append(key)
    sets = spec.get("set") or {}
    if isinstance(sets, dict):
        iptc = False
        for key, value in sets.items():
            key = str(key).strip()
            if not ok(key):
                if key:
                    skipped.append(key)
                continue
            if key.lower().startswith("iptc:"):
                iptc = True
            values = value if isinstance(value, list) else [value]
            if not values or all(v in (None, "") for v in values):
                args.append(f"-{key}=")
                continue
            for v in values:
                text = "" if v is None else str(v)
                args.append(f"-{key}={text}")
        if iptc:
            args.append("-IPTC:CodedCharacterSet=UTF8")
    return args, skipped


@action(id="image.privacy.metadata", label="img.act.metadata", icon="sell", categories={"image"}, mode="dialog",
        dialog="metadata", params=[P.metadata("metadata", "img.prm.metadata", required=True)], replace_ok=True,
        requires=["exiftool"], description="img.desc.metadata", order=93)
def metadata(ctx: Context) -> Result:
    """Apply the metadata editor's changes (``set`` / ``remove`` / ``remove_all``) with exiftool."""
    f = ctx.file
    spec = _per_file(ctx, f, "metadata")
    if not isinstance(spec, dict):
        raise UserError("img.err.metadata_nothing")
    args, skipped = _metadata_args(spec, _fmt(f) in RAW_FORMATS)
    if skipped:
        _warn_key(ctx, "img.warn.keys_skipped", keys=", ".join(skipped[:8]))
    if not args:
        raise UserError("img.err.metadata_nothing")
    work = _work_copy(ctx, f.path)
    ctx.run(["exiftool", "-q", "-q", "-m", "-overwrite_original", "-charset", "UTF8", *args, "--", work],
            timeout=300)
    if _same_bytes(work, f.path):
        return ctx.result(kind="none", message=ctx.t("img.msg.metadata_unchanged"))
    suffix = "sfx.no_metadata" if (spec.get("remove_all") and not spec.get("set")) else "img.sfx.metadata"
    out = ctx.output_path(f, suffix=suffix)
    _deliver(ctx, work, out)
    return ctx.result([ctx.replace_original(f, out)])


_DATE_PATTERNS = [("%Y-%m-%d %H.%M.%S", "2024-05-01 14.32.10"), ("%Y%m%d_%H%M%S", "20240501_143210"),
                  ("%Y-%m-%d", "2024-05-01"), ("%Y-%m-%d_%H-%M-%S", "2024-05-01_14-32-10")]


def _capture_date(ctx: Context, f: FileInfo, fallback_mtime: bool) -> datetime | None:
    dt = ops.exif_datetime(f.path)
    if dt is None and tools.have("exiftool"):
        res = ctx.run(["exiftool", "-j", "-n", "-DateTimeOriginal", "-CreateDate", "-ModifyDate", "--", f.path],
                      timeout=60, ok_codes=(0, 1))
        try:
            rows = json.loads(res.stdout or "[]")
        except ValueError:
            rows = []
        if rows and isinstance(rows[0], dict):
            for key in ("DateTimeOriginal", "CreateDate", "ModifyDate"):
                dt = ops.parse_exif_datetime(rows[0].get(key))
                if dt is not None:
                    break
    if dt is None and fallback_mtime:
        mt = ctx.original(f).mtime or f.mtime
        if mt:
            dt = datetime.fromtimestamp(mt)
    return dt


@action(id="image.privacy.rename_date", label="img.act.rename_date", icon="event", categories={"image"},
        multi="any", mode="quick", undoable=False,
        params=[P.select("pattern", "img.prm.date_pattern", options=_DATE_PATTERNS, default="%Y-%m-%d %H.%M.%S"),
                P.bool("keep_name", "img.prm.keep_name", False),
                P.bool("fallback_mtime", "img.prm.fallback_mtime", True)],
        requires=["py:PIL"], description="img.desc.rename_date", order=94)
def rename_date(ctx: Context) -> Result:
    """Rename photos after their capture date (EXIF DateTimeOriginal; file date as fallback)."""
    pattern = ctx.pstr("pattern", "%Y-%m-%d %H.%M.%S")
    if pattern not in {p for p, _ in _DATE_PATTERNS}:
        pattern = "%Y-%m-%d %H.%M.%S"
    keep = ctx.pbool("keep_name")
    fallback = ctx.pbool("fallback_mtime", True)
    files = ordered_files(ctx.files, "name")
    lines: list[str] = []
    outputs: list[Path] = []
    taken: set[str] = set()
    n = len(files)
    no_date: list[str] = []
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(i / max(1, n), f.name)
        orig = ctx.original(f)
        if not orig.path or not os.path.exists(orig.path):
            no_date.append(f.name)
            continue
        dt = _capture_date(ctx, f, fallback)
        if dt is None:
            no_date.append(f.name)
            continue
        src = Path(orig.path)
        stem = dt.strftime(pattern) + (f" {orig.stem}" if keep else "")
        ext = orig.ext
        target = src.parent / util.safe_filename(f"{stem}.{ext}" if ext else stem)
        if target == src:
            continue
        target = util.unique_path(target, taken=taken | {str(src)})
        taken.add(str(target))
        try:
            if os.path.lexists(target):
                raise FileExistsError(str(target))
            os.rename(src, target)
        except OSError as exc:
            raise UserError("img.err.rename_failed", details=str(exc), name=orig.name) from None
        outputs.append(target)
        lines.append(f"{orig.name} → {target.name}")
    if no_date:
        _warn_key(ctx, "img.warn.no_date", names=", ".join(no_date[:5]) + ("…" if len(no_date) > 5 else ""))
    if not outputs:
        return ctx.result(kind="none", message=ctx.t("img.msg.rename_nothing"))
    return ctx.result(outputs, text="\n".join(lines), message=i18n.tn("img.msg.renamed", len(outputs), ctx.lang))


# =========================================================================================== MORE

def _ocr_languages(ctx: Context) -> list[str]:
    requested = str(_cfg(ctx, "ocr.languages", "ita+eng") or "ita+eng")
    res = ctx.run(["tesseract", "--list-langs"], timeout=30, ok_codes=(0, 1))
    available = ops.parse_tesseract_langs((res.stdout or "") + "\n" + (res.stderr or ""))
    langs, missing = ops.pick_ocr_languages(requested, available)
    if not langs:
        raise UserError("img.err.ocr_no_langs", install=tools.install_hint(["tesseract"]))
    if missing:
        pkgs = " ".join(f"tesseract-data-{m.lower()}" for m in missing)
        ctx.warn(ctx.t("img.warn.ocr_lang_missing", langs="+".join(missing), install=f"sudo pacman -S {pkgs}"))
    return langs


def _ocr_input(ctx: Context, f: FileInfo) -> tuple[Path, int]:
    """8-bit, oriented, flattened PNG for tesseract (small images ×2); returns ``(path, dpi)``."""
    im, meta = _load_any(ctx, f)
    im = ops.flatten(ops.to_srgb(im, meta.icc)) if ops.has_alpha(im) or im.mode not in ("L", "RGB") else im
    dpi = int(round(meta.dpi[0])) if meta.dpi and 70 <= meta.dpi[0] <= 1200 else 300
    if max(im.size) < 1200:
        im = ops.resize(im, (im.size[0] * 2, im.size[1] * 2))
        dpi *= 2
    png = _tmp(ctx, "ocr.png")
    im.save(png, "PNG", dpi=(dpi, dpi))
    return png, dpi


def _ocr(ctx: Context, kind: str) -> Result:
    f = ctx.file
    ctx.progress(None, ctx.t("img.msg.ocr_running"))
    langs = _ocr_languages(ctx)
    png, dpi = _ocr_input(ctx, f)
    base = _tmp(ctx, "ocr-out")
    config = "pdf" if kind == "pdf" else "txt"
    ctx.run(["tesseract", png, base, "-l", "+".join(langs), "--psm", "3", "--dpi", str(dpi), config], timeout=1800)
    produced = Path(f"{base}.{config}")
    if not produced.exists():
        raise ToolError("err.no_output", name=produced.name)
    if kind == "pdf":
        out = ctx.output_path(f, ext="pdf", suffix="sfx.ocr")
        _deliver(ctx, produced, out)
        return ctx.result([out])
    text = produced.read_text(encoding="utf-8", errors="replace").replace("\f", "\n").strip()
    if not text:
        return ctx.result(kind="none", message=ctx.t("img.msg.ocr_empty"))
    if kind == "txt":
        out = ctx.output_path(f, ext="txt", suffix="sfx.ocr")
        with atomic_target(out) as part:
            Path(part).write_text(text + "\n", encoding="utf-8")
        return ctx.result([out], text=text)
    copied = tools.have("wl-copy") and util.copy_text(text)
    words = len(text.split())
    msg = ctx.t("img.msg.ocr_copied" if copied else "img.msg.ocr_done", words=words)
    return ctx.result(kind="text", text=text, message=msg)


for _kind, _label_key, _icon, _order in (("copy", "img.act.ocr.copy", "content_copy", 100),
                                         ("txt", "img.act.ocr.txt", "description", 101),
                                         ("pdf", "img.act.ocr.pdf", "picture_as_pdf", 102)):
    def _ocr_fn(ctx: Context, _k: str = _kind) -> Result:
        """OCR with tesseract (languages from ``ocr.languages``, ``eng`` fallback)."""
        return _ocr(ctx, "text" if _k == "copy" else _k)

    _ocr_fn.__name__ = f"ocr_{_kind}"
    action(id=f"image.ocr.{_kind}", label=_label_key, icon=_icon, categories={"image", "svg"},
           when=_STATIC_OR_SVG, mode="instant", undoable=_kind != "copy", requires=["tesseract"],
           description=f"img.desc.ocr.{_kind}", order=_order)(_ocr_fn)


@action(id="image.qr", label="img.act.qr", icon="qr_code_scanner", categories={"image", "svg"}, when=_STATIC_OR_SVG,
        mode="instant", undoable=False, requires=["zbarimg"], description="img.desc.qr", order=103)
def read_qr(ctx: Context) -> Result:
    """Read QR codes and barcodes (zbarimg); the text goes to the clipboard, links can be opened."""
    f = ctx.file
    im, meta = _load_any(ctx, f)
    im = ops.flatten(im) if ops.has_alpha(im) else ops.normalize_mode(ops.to_8bit(im))
    if im.mode not in ("L", "RGB"):
        im = im.convert("RGB")
    png = _tmp(ctx, "qr.png")
    im.save(png, "PNG")
    res = ctx.run(["zbarimg", "-q", "--xml", png], ok_codes=(0, 4), timeout=120)
    items = ops.parse_zbar_xml(res.stdout or "")
    if not items:
        return ctx.result(kind="none", message=ctx.t("img.msg.qr_none"))
    text = "\n".join(data for _kind, data in items)
    urls = ops.find_urls(text)
    if tools.have("wl-copy"):
        util.copy_text(text)
    if urls:
        msg = ctx.t("img.msg.qr_link", url=urls[0])
    else:
        msg = i18n.tn("img.msg.qr_found", len(items), ctx.lang)
    return ctx.result(kind="text", text=text, message=msg, url=urls[0] if urls else None,
                      codes=[{"type": k, "data": d} for k, d in items])


def _models_dir(candidates: Sequence[str], exe: str | None, sub: str) -> str | None:
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    if exe:
        local = os.path.join(os.path.dirname(os.path.realpath(exe)), sub)
        if os.path.isdir(local):
            return local
    return None


def _upscale(ctx: Context, scale: int, anime: bool = False) -> Result:
    f = ctx.file
    im, meta = _load(ctx, f)
    w, h = im.size
    if max(w, h) * scale > ops.MAX_SIDE or w * h * scale * scale > 268_000_000:
        raise UserError("img.err.upscale_too_large", w=w * scale, h=h * scale)
    alpha = ops.has_alpha(im)
    base = ops.normalize_mode(ops.to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if base.mode == "CMYK":
        base = ops.to_srgb(base, meta.icc)
    base = base.convert("RGBA" if alpha else "RGB")
    src = _tmp(ctx, "up-in.png")
    base.save(src, "PNG")
    produced = _tmp(ctx, "up-out.png")
    label = _label(ctx)
    ctx.progress(None, label)
    pct_re = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")

    def on_line(line: str) -> None:
        m = pct_re.search(line)
        if m:
            try:
                ctx.progress(min(0.98, float(m.group(1).replace(",", ".")) / 100.0), label)
            except ValueError:
                pass

    result = None
    errors: list[str] = []
    if tools.have("realesrgan"):
        exe = tools.path("realesrgan")
        model = "realesrgan-x4plus-anime" if anime else "realesrgan-x4plus"
        args: list[Any] = ["realesrgan", "-i", src, "-o", produced, "-n", model, "-s", "4", "-f", "png"]
        models = _models_dir(_REALESRGAN_MODELS, exe, "models")
        if models:
            args += ["-m", models]
        try:
            ctx.run(args, on_line=on_line, stderr_lines=True, timeout=7200)
            if produced.exists():
                result, _m = ops.load_oriented(produced, f.name)
                if scale != 4:
                    result = ops.resize(result, (w * scale, h * scale))
        except ToolError as exc:
            errors.append(exc.details or str(exc))
    if result is None and tools.have("waifu2x"):
        exe = tools.path("waifu2x")
        args = ["waifu2x", "-i", src, "-o", produced, "-n", "1" if anime else "0", "-s", str(scale), "-f", "png"]
        models = _models_dir(_WAIFU2X_MODELS, exe, "models-cunet")
        if models:
            args += ["-m", models]
        try:
            ctx.run(args, on_line=on_line, stderr_lines=True, timeout=7200)
            if produced.exists():
                result, _m = ops.load_oriented(produced, f.name)
        except ToolError as exc:
            errors.append(exc.details or str(exc))
    if result is None:
        from PIL import ImageFilter

        if errors:
            log.debug("AI upscalers failed: %s", errors)
        _warn_key(ctx, "img.warn.upscale_basic")
        result = ops.resize(base, (w * scale, h * scale))
        result = result.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
    if result.size != (w * scale, h * scale):
        result = ops.resize(result, (w * scale, h * scale))
    if alpha and result.mode != "RGBA":
        result = result.convert("RGBA")
    _exif_set_dims(meta, result.size)
    src_fmt = _fmt(f)
    fmt = src_fmt if src_fmt in ("jpg", "png", "webp") else ("png" if alpha or src_fmt not in _LOSSY else "jpg")
    out = ctx.output_path(f, ext=_out_ext(fmt), suffix=f"img.sfx.x{scale}")
    quality = 92 if fmt in _LOSSY else None
    _write(ctx, result, meta, out, fmt, quality=quality)
    return ctx.result([out], message=ctx.t("img.msg.resized", w=result.size[0], h=result.size[1]))


for _scale, _anime, _id, _order in ((2, False, "x2", 104), (4, False, "x4", 105), (4, True, "anime4", 106)):
    def _up_fn(ctx: Context, _s: int = _scale, _a: bool = _anime) -> Result:
        """AI upscale (realesrgan-ncnn-vulkan → waifu2x-ncnn-vulkan → Lanczos with a warning)."""
        return _upscale(ctx, _s, _a)

    _up_fn.__name__ = f"upscale_{_id}"
    action(id=f"image.upscale.{_id}", label=f"img.act.upscale.{_id}", text={"x2": "×2", "x4": "×4"}.get(_id),
           icon="hd", categories={"image"}, when=_STATIC, mode="instant", requires=["py:PIL"],
           description=f"img.desc.upscale.{_id}", order=_order)(_up_fn)


_DETAIL = {"low": {"speckle": 8, "precision": 5, "gradient": 24},
           "medium": {"speckle": 4, "precision": 6, "gradient": 16},
           "high": {"speckle": 2, "precision": 7, "gradient": 8}}


def _vectorize(ctx: Context, f: FileInfo, mode: str, *, conversion: bool = False) -> Path:
    """Trace a raster to SVG: ``mode="bw"`` potrace (via PBM), ``"color"`` vtracer 0.6.15 (CLI or Python)."""
    im, meta = _load(ctx, f)
    ctx.progress(None, _label(ctx))
    im = ops.normalize_mode(ops.to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if im.mode == "CMYK":
        im = ops.to_srgb(im, meta.icc)
    if max(im.size) > 2000:
        im = ops.resize(im, ops.shrink_long_edge(im.size[0], im.size[1], 2000) or im.size)
    out = ctx.output_path(f, ext="svg")
    color_ok = tools.have("vtracer") or tools.have("py:vtracer")
    if mode == "color" and not color_ok and tools.have("potrace") and conversion:
        mode = "bw"
        _warn_key(ctx, "img.warn.trace_bw")
    if mode == "bw":
        if not tools.have("potrace"):
            raise MissingTool(["potrace"])
        thr = max(1, min(99, ctx.pint("threshold", 50))) * 255 // 100
        gray = ops.flatten(im).convert("L")
        bits = gray.point(lambda v: 255 if v > thr else 0).convert("1")
        if ctx.pbool("invert"):
            from PIL import ImageOps

            bits = ImageOps.invert(bits.convert("L")).convert("1")
        pbm = _tmp(ctx, "trace.pbm")
        bits.save(pbm, "PPM")
        with atomic_target(out) as part:
            ctx.run(["potrace", "-s", "--turdsize", str(max(0, ctx.pint("speckle", 2))), "-o", part, pbm],
                    timeout=900)
        return out
    detail = _DETAIL.get(ctx.pstr("detail", "medium"), _DETAIL["medium"])
    png = _tmp(ctx, "trace.png")
    im.convert("RGBA" if ops.has_alpha(im) else "RGB").save(png, "PNG")
    with atomic_target(out) as part:
        if tools.have("vtracer"):
            ctx.run(["vtracer", "--input", png, "--output", part, "--colormode", "color", "--hierarchical",
                     "stacked", "--mode", "spline", "--filter_speckle", str(detail["speckle"]),
                     "--color_precision", str(detail["precision"]), "--gradient_step", str(detail["gradient"])],
                    timeout=1800)
        elif tools.have("py:vtracer"):
            import vtracer  # type: ignore

            try:
                vtracer.convert_image_to_svg_py(str(png), str(part), colormode="color", hierarchical="stacked",
                                                mode="spline", filter_speckle=detail["speckle"],
                                                color_precision=detail["precision"],
                                                layer_difference=detail["gradient"])
            except TypeError:
                vtracer.convert_image_to_svg_py(str(png), str(part))
        else:
            raise MissingTool(["vtracer"])
    return out


@action(id="image.vectorize.bw", label="img.act.vectorize.bw", icon="gesture", categories={"image"}, when=_STATIC,
        mode="quick", params=[P.slider("threshold", "img.prm.threshold", 50, min=1, max=99, unit="img.unit.pct"),
                              P.int("speckle", "img.prm.speckle", 2, min=0, max=100),
                              P.bool("invert", "img.prm.invert", False)],
        requires=["potrace"], description="img.desc.vectorize.bw", order=107)
def vectorize_bw(ctx: Context) -> Result:
    """Black & white trace with potrace → ``<stem>.svg``."""
    return ctx.result([_vectorize(ctx, ctx.file, "bw")])


@action(id="image.vectorize.color", label="img.act.vectorize.color", icon="palette", categories={"image"},
        when=_STATIC, mode="quick",
        params=[P.select("detail", "img.prm.detail", options=[("low", "img.opt.detail.low"),
                                                              ("medium", "img.opt.detail.medium"),
                                                              ("high", "img.opt.detail.high")], default="medium")],
        requires=[("vtracer", "py:vtracer")], description="img.desc.vectorize.color", order=108)
def vectorize_color(ctx: Context) -> Result:
    """Colour trace with vtracer → ``<stem>.svg``."""
    return ctx.result([_vectorize(ctx, ctx.file, "color")])


def _icon_source(ctx: Context, f: FileInfo) -> Any:
    if f.category == "svg":
        im, _meta = _load_any(ctx, f, size=1024)
    else:
        im, meta = _load(ctx, f)
        if im.mode == "CMYK" or (meta.icc and ops.icc_colorspace(meta.icc) == "RGB"):
            im = ops.to_srgb(im, meta.icc)
    if min(im.size) < 16:
        raise UserError("img.err.too_small", w=im.size[0], h=im.size[1])
    return ops.square_pad(im)


@action(id="image.icon.ico", label="img.act.icon.ico", text="ICO", icon="apps", categories={"image", "svg"},
        when=_STATIC_OR_SVG, mode="instant", requires=["py:PIL"], description="img.desc.icon.ico", order=109)
def icon_ico(ctx: Context) -> Result:
    """Multi-size ICO (16–256, square-padded) → ``<stem>.ico``."""
    f = ctx.file
    sq = _icon_source(ctx, f)
    out = ctx.output_path(f, ext="ico")
    _write(ctx, sq, None, out, "ico", keep_metadata=False, icc=False)
    return ctx.result([out])


@action(id="image.icon.png_set", label="img.act.icon.png_set", icon="grid_view", categories={"image", "svg"},
        when=_STATIC_OR_SVG, mode="instant", requires=["py:PIL"], description="img.desc.icon.png_set", order=110)
def icon_png_set(ctx: Context) -> Result:
    """PNG icons 16–512 px in ``<stem> (icona)/icon-<size>.png``."""
    f = ctx.file
    if f.category == "svg":
        folder = ctx.output_dir(f, suffix="sfx.icon")
        for size in ops.PNG_SET_SIZES:
            ctx.check_cancel()
            tmp = _tmp(ctx, f"icon-{size}.png")
            _render_svg(ctx, f, tmp, long_edge=size)       # crisp vector render at every size
            with ops.open_image(tmp) as rendered:
                sq = ops.square_pad(rendered)
            if sq.size != (size, size):
                sq = ops.resize(sq, (size, size))
            sq.save(folder / f"icon-{size}.png", "PNG", optimize=True)
        return ctx.result([folder])
    sq = _icon_source(ctx, f)
    if sq.size[0] < 256:
        _warn_key(ctx, "img.warn.icon_upscaled", size=sq.size[0])
    folder = ctx.output_dir(f, suffix="sfx.icon")
    for size in ops.PNG_SET_SIZES:
        ctx.check_cancel()
        ops.resize(sq, (size, size)).save(folder / f"icon-{size}.png", "PNG", optimize=True)
    return ctx.result([folder])


_WEBMANIFEST = """{
  "name": "",
  "short_name": "",
  "icons": [
    {"src": "/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png"}
  ],
  "theme_color": "#ffffff",
  "background_color": "#ffffff",
  "display": "standalone"
}
"""
_FAVICON_HTML = """<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
"""


@action(id="image.icon.favicon", label="img.act.icon.favicon", icon="language", categories={"image", "svg"},
        when=_STATIC_OR_SVG, mode="instant", requires=["py:PIL"], description="img.desc.icon.favicon", order=111)
def icon_favicon(ctx: Context) -> Result:
    """Favicon pack: favicon.ico, 16/32 PNG, apple-touch-icon, Android 192/512, site.webmanifest and the
    HTML snippet."""
    from PIL import Image

    f = ctx.file
    sq = _icon_source(ctx, f)
    folder = ctx.output_dir(f, suffix="img.sfx.favicon")
    ops.save_image(sq, folder / "favicon.ico", "ico", ico_sizes=(16, 32, 48), icc=False)
    for name, size in (("favicon-16x16.png", 16), ("favicon-32x32.png", 32), ("android-chrome-192x192.png", 192),
                       ("android-chrome-512x512.png", 512)):
        ctx.check_cancel()
        ops.resize(sq, (size, size)).save(folder / name, "PNG", optimize=True)
    touch = Image.new("RGB", (180, 180), (255, 255, 255))
    small = ops.resize(sq, (180, 180))
    touch.paste(small, (0, 0), small)
    touch.save(folder / "apple-touch-icon.png", "PNG", optimize=True)
    (folder / "site.webmanifest").write_text(_WEBMANIFEST, encoding="utf-8")
    (folder / "favicon.html").write_text(_FAVICON_HTML, encoding="utf-8")
    return ctx.result([folder])


@action(id="image.copy", label="img.act.copy", icon="content_copy", categories={"image", "svg"},
        when=_STATIC_OR_SVG, multi="single", mode="instant", undoable=False, requires=["wl-copy"],
        description="img.desc.copy", order=112)
def copy_image(ctx: Context) -> Result:
    """Put the image on the clipboard (``image/png``; animated GIFs as ``image/gif``)."""
    f = ctx.file
    if f.animated and _fmt(f) == "gif":
        ok = util.copy_image(f.path, "image/gif")
    else:
        im, meta = _load_any(ctx, f, size=None) if f.category == "svg" else _load(ctx, f)
        if im.mode == "CMYK" or (meta.icc and ops.icc_colorspace(meta.icc) == "RGB"):
            im = ops.to_srgb(im, meta.icc)
        png = _tmp(ctx, "clipboard.png")
        ops.save_image(im, png, "png", icc=False)
        ok = util.copy_image(png, "image/png")
    if not ok:
        raise UserError("img.err.clipboard")
    return ctx.result(kind="none", message=ctx.t("img.msg.copied"))


def _wallpaper_path(ctx: Context, f: FileInfo) -> str:
    """A stable path for the wallpaper (volatile or exotic formats are copied as PNG)."""
    orig = ctx.original(f)
    fmt = _fmt(f)
    if not orig.volatile and fmt in ("jpg", "png", "webp") and os.path.isfile(orig.path):
        return orig.path
    folder = util.xdg_data_home() / "dropwheel" / "wallpapers"
    folder.mkdir(parents=True, exist_ok=True)
    if fmt in ("jpg", "png", "webp"):
        target = util.unique_path(folder / util.safe_filename(orig.name))
        shutil.copyfile(f.path, target)
        return str(target)
    im, meta = _load(ctx, f)
    target = util.unique_path(folder / util.safe_filename(f"{orig.stem or 'wallpaper'}.png"))
    _encode_to(ctx, im, meta, target, "png", keep_metadata=False)
    return str(target)


@action(id="image.wallpaper", label="img.act.wallpaper", icon="wallpaper", categories={"image"}, when=_STATIC,
        multi="single", mode="instant", undoable=False, requires=[("caelestia", "swww", "hyprctl")],
        description="img.desc.wallpaper", order=113)
def set_wallpaper(ctx: Context) -> Result:
    """Set as wallpaper: ``caelestia wallpaper -f`` (also updates the colour scheme), else swww or hyprpaper."""
    f = ctx.file
    path = _wallpaper_path(ctx, f)
    ctx.progress(None, _label(ctx))
    errors: list[str] = []
    if tools.have("caelestia"):
        try:
            ctx.run(["caelestia", "wallpaper", "-f", path], timeout=180)
            return ctx.result(kind="none", message=ctx.t("img.msg.wallpaper_set"))
        except ToolError as exc:
            errors.append(f"caelestia: {exc.details or exc}")
    if tools.have("swww"):
        try:
            ctx.run(["swww", "img", path], timeout=60)
            return ctx.result(kind="none", message=ctx.t("img.msg.wallpaper_set"))
        except ToolError as exc:
            errors.append(f"swww: {exc.details or exc}")
    if tools.have("hyprctl"):
        try:
            ctx.run(["hyprctl", "hyprpaper", "preload", path], timeout=30)
            ctx.run(["hyprctl", "hyprpaper", "wallpaper", f",{path}"], timeout=30)
            return ctx.result(kind="none", message=ctx.t("img.msg.wallpaper_set"))
        except ToolError as exc:
            errors.append(f"hyprpaper: {exc.details or exc}")
    raise UserError("img.err.wallpaper", details="\n".join(errors))


@action(id="image.print", label="img.act.print", icon="print", categories={"image", "svg"}, when=_STATIC_OR_SVG,
        multi="each", mode="quick", undoable=False,
        params=[P.int("copies", "img.prm.copies", 1, min=1, max=99), P.bool("fit", "img.prm.fit_page", True)],
        requires=["lp"], description="img.desc.print", order=114)
def print_image(ctx: Context) -> Result:
    """Send to the default printer with ``lp`` (oriented PNG/PDF copy when needed)."""
    f = ctx.file
    fmt = _fmt(f)
    path: str
    if f.category == "svg":
        pdf = _tmp(ctx, "print.pdf")
        if tools.have("rsvg-convert"):
            ctx.run(["rsvg-convert", "-f", "pdf", "-o", pdf, f.path], timeout=300)
        else:
            png = _tmp(ctx, "print.png")
            _render_svg(ctx, f, png, min_edge=2048)
            pdf = png
        path = str(pdf)
    else:
        im, meta = _load(ctx, f)
        if fmt in ("jpg", "png") and meta.orientation == 1 and not ctx.original(f).volatile:
            path = f.path
        else:
            png = _tmp(ctx, "print.png")
            _encode_to(ctx, ops.flatten(im) if ops.has_alpha(im) else im, meta, png, "png", keep_metadata=False)
            path = str(png)
    args = ["lp", "-n", str(max(1, ctx.pint("copies", 1)))]
    if ctx.pbool("fit", True):
        args += ["-o", "fit-to-page"]
    res = ctx.run([*args, path], timeout=120)
    m = re.search(r"request id is (\S+)", res.stdout or "")
    return ctx.result(kind="none", message=ctx.t("img.msg.printed", job=m.group(1) if m else ""))


# ============================================================================================ SVG

_SVG_RENDER = ("rsvg-convert", "resvg", "magick", "py:cairosvg", "inkscape")
_SVG_SIZE_OPTIONS = [("native", "img.opt.size.native"), (512, "512 px"), (1024, "1024 px"), (2048, "2048 px"),
                     (4096, "4096 px")]


def _svg_edge(ctx: Context, default: Any = "native") -> int | None:
    value = ctx.p("size", default)
    if str(value) == "native":
        return None
    try:
        return max(1, min(ops.MAX_SIDE, int(value)))
    except (TypeError, ValueError):
        return None


def _svg_convert(ctx: Context, f: FileInfo, target: str, *, long_edge: int | None = None, width: int | None = None,
                 height: int | None = None, background: str | None = None) -> Path:
    """SVG → PDF (vector) / PNG / JPG / WebP / AVIF / ICO."""
    target = ops.norm_fmt(target)
    label = _label(ctx)
    ctx.progress(None, label)
    if target == "pdf":
        out = ctx.output_path(f, ext="pdf")
        with atomic_target(out) as part:
            if tools.have("rsvg-convert"):
                ctx.run(["rsvg-convert", "-f", "pdf", "-o", part, f.path], timeout=600)
            elif tools.have("py:cairosvg"):
                import cairosvg  # type: ignore

                try:
                    cairosvg.svg2pdf(url=f.path, write_to=str(part), unsafe=False)
                except Exception as exc:
                    raise UserError("img.err.svg_render", details=str(exc), name=f.name) from None
            elif tools.have("inkscape"):
                ctx.run(["inkscape", "--export-type=pdf", f"--export-filename={part}", f.path], timeout=600)
            else:
                png = _tmp(ctx, "svg.png")
                _render_svg(ctx, f, png, min_edge=2048)
                im, meta = ops.load_oriented(png, f.name)
                meta.dpi = (192.0, 192.0)
                ops.save_image(ops.flatten(im), part, "pdf", meta=meta)
                _warn_key(ctx, "img.warn.svg_raster_pdf")
        return out
    if target == "png":
        out = ctx.output_path(f, ext="png")
        with atomic_target(out) as part:
            _render_svg(ctx, f, Path(part), long_edge=long_edge, width=width, height=height, background=background)
        return out
    png = _tmp(ctx, "svg.png")
    if target == "ico":
        _render_svg(ctx, f, png, long_edge=256)
        with ops.open_image(png) as rendered:
            sq = ops.square_pad(rendered)
        out = ctx.output_path(f, ext="ico")
        _write(ctx, sq, None, out, "ico", keep_metadata=False, icc=False)
        return out
    _render_svg(ctx, f, png, long_edge=long_edge, width=width, height=height, min_edge=1024 if not long_edge else None,
                background=background)
    im, meta = ops.load_oriented(png, f.name)
    bg = ops.parse_color(background or ctx.pstr("background") or "#ffffff")[:3]
    quality = ctx.pint("quality", _quality_for(ctx, target)) if target in _LOSSY else None
    out = ctx.output_path(f, ext=_out_ext(target))
    _write(ctx, im, None, out, target, quality=quality, bg=bg, keep_metadata=False)
    return out


def _svg_png_action(size: int | None) -> Callable[[Context], Result]:
    def fn(ctx: Context) -> Result:
        return ctx.result([_svg_convert(ctx, ctx.file, "png", long_edge=size)])

    fn.__name__ = f"svg_png_{size or 'native'}"
    fn.__doc__ = "SVG → PNG at the intrinsic size." if size is None else f"SVG → PNG, long edge {size} px."
    return fn


action(id="svg.convert.png.native", label="img.act.svg.native", icon="image", categories={"svg"}, mode="instant",
       requires=[_SVG_RENDER], description="img.desc.svg.png", order=10)(_svg_png_action(None))
for _size, _order in ((512, 11), (1024, 12), (2048, 13), (4096, 14)):
    action(id=f"svg.convert.png.{_size}", label=f"{_size} px", text=str(_size), icon="image", categories={"svg"},
           mode="instant", requires=[_SVG_RENDER], description="img.desc.svg.png", order=_order)(
        _svg_png_action(_size))


@action(id="svg.convert.png.custom", label="img.act.resize.custom", icon="aspect_ratio", categories={"svg"},
        mode="dialog", dialog="form",
        params=[P.int("width", "img.prm.width", 1024, min=0, max=ops.MAX_SIDE, help="img.prm.size.help"),
                P.int("height", "img.prm.height", 0, min=0, max=ops.MAX_SIDE),
                P.bool("transparent", "img.prm.transparent", True),
                P.color("background", "img.prm.background", "#ffffff", visible_if={"transparent": False})],
        requires=[_SVG_RENDER], description="img.desc.svg.png_custom", order=15)
def svg_png_custom(ctx: Context) -> Result:
    """SVG → PNG at a custom size (0 = proportional) and optional background."""
    w, h = max(0, ctx.pint("width", 0)), max(0, ctx.pint("height", 0))
    if not w and not h:
        raise UserError("img.err.no_size")
    bg = None if ctx.pbool("transparent", True) else ops.hex_color(ops.parse_color(ctx.pstr("background")))
    return ctx.result([_svg_convert(ctx, ctx.file, "png", width=w or None, height=h or None, background=bg)])


@action(id="svg.convert.pdf", label="PDF", text="PDF", icon="picture_as_pdf", categories={"svg"}, mode="instant",
        requires=[_SVG_RENDER], description="img.desc.svg.pdf", order=16)
def svg_pdf(ctx: Context) -> Result:
    """SVG → vector PDF (rsvg-convert / CairoSVG / Inkscape)."""
    return ctx.result([_svg_convert(ctx, ctx.file, "pdf")])


@action(id="svg.convert.jpg", label="JPG", text="JPG", icon="image", categories={"svg"}, mode="quick",
        params=[P.select("size", "img.prm.size", options=_SVG_SIZE_OPTIONS, default=2048),
                _quality_param("image.jpeg_quality"), _bg_param()],
        requires=[_SVG_RENDER], description="img.desc.svg.raster", order=17)
def svg_jpg(ctx: Context) -> Result:
    """SVG → JPG (flattened on the background colour)."""
    return ctx.result([_svg_convert(ctx, ctx.file, "jpg", long_edge=_svg_edge(ctx, 2048))])


@action(id="svg.convert.webp", label="WEBP", text="WEBP", icon="image", categories={"svg"}, mode="quick",
        params=[P.select("size", "img.prm.size", options=_SVG_SIZE_OPTIONS, default=2048),
                _quality_param("image.webp_quality")],
        requires=[_SVG_RENDER], description="img.desc.svg.raster", order=18)
def svg_webp(ctx: Context) -> Result:
    """SVG → WebP (transparency kept)."""
    return ctx.result([_svg_convert(ctx, ctx.file, "webp", long_edge=_svg_edge(ctx, 2048))])


@action(id="svg.convert.ico", label="ICO", text="ICO", icon="apps", categories={"svg"}, mode="instant",
        requires=[_SVG_RENDER], description="img.desc.icon.ico", order=19)
def svg_ico(ctx: Context) -> Result:
    """SVG → multi-size ICO."""
    return ctx.result([_svg_convert(ctx, ctx.file, "ico")])


_SVGO_V3_CONFIG = """export default {
  multipass: true,
  floatPrecision: 3,
  plugins: [{ name: "preset-default", params: { overrides: { removeViewBox: false } } }],
};
"""


def _svgo_major() -> int:
    res = util.run_quiet(["svgo", "--version"], timeout=20)
    m = re.match(r"\s*(\d+)", res.stdout if res is not None and res.returncode == 0 else "")
    return int(m.group(1)) if m else 0


@action(id="svg.optimize", label="img.act.svg.optimize", icon="compress", categories={"svg"}, mode="instant",
        replace_ok=True, description="img.desc.svg.optimize", order=20)
def svg_optimize(ctx: Context) -> Result:
    """Optimise the markup (scour → svgo → built-in clean-up of comments/metadata/editor data);
    never enlarges, keeps ``viewBox``."""
    import gzip

    f = ctx.file
    raw = Path(f.path).read_bytes()
    gz = ops.is_svgz(raw)
    text = ops.svg_text(f.path)
    src = _tmp(ctx, "in.svg")
    src.write_text(text, encoding="utf-8")
    produced = _tmp(ctx, "out.svg")
    ctx.progress(None, _label(ctx))
    done = False
    if tools.have("scour"):
        try:
            # no --enable-viewboxing: it rewrites width/height to 100 % and changes the intrinsic size
            ctx.run(["scour", "-i", src, "-o", produced, "--enable-id-stripping", "--shorten-ids",
                     "--enable-comment-stripping", "--remove-metadata", "--indent=none", "--quiet"], timeout=600)
            done = produced.exists() and produced.stat().st_size > 0
        except ToolError as exc:
            log.debug("scour failed: %s", exc.details)
    if not done and tools.have("svgo"):
        args: list[Any] = ["svgo", "--multipass", "-p", "3"]
        if _svgo_major() == 3:
            cfg = _tmp(ctx, "svgo.config.mjs")
            cfg.write_text(_SVGO_V3_CONFIG, encoding="utf-8")
            args += ["--config", cfg]
        try:
            ctx.run([*args, "-i", src, "-o", produced], timeout=600)
            done = produced.exists() and produced.stat().st_size > 0
        except ToolError as exc:
            log.debug("svgo failed: %s", exc.details)
    if not done:
        produced.write_text(ops.svg_minify(text), encoding="utf-8")
    if gz:
        packed = _tmp(ctx, "out.svgz")
        with gzip.open(packed, "wb", compresslevel=9) as fh:
            fh.write(produced.read_bytes())
        produced = packed
    before = ctx.original(f).size or len(raw)
    after = produced.stat().st_size
    if after >= before:
        return ctx.result(kind="none", message=ctx.t("img.msg.not_smaller", name=f.name))
    out = ctx.output_path(f, suffix="sfx.optimized")
    _deliver(ctx, produced, out)
    return ctx.result([ctx.replace_original(f, out)], size_before=before,
                      message=ctx.t("img.msg.saved", pct=int(round((before - after) * 100.0 / before))))


@action(id="svg.copy_text", label="img.act.svg.copy_text", icon="code", categories={"svg"}, multi="single",
        mode="instant", undoable=False, requires=["wl-copy"], description="img.desc.svg.copy_text", order=21)
def svg_copy_text(ctx: Context) -> Result:
    """Copy the SVG markup to the clipboard (also shown as text)."""
    f = ctx.file
    text = ops.svg_text(f.path)
    if not util.copy_text(text):
        raise UserError("img.err.clipboard")
    preview = text if len(text) <= 100_000 else text[:100_000] + "\n…"
    return ctx.result(kind="text", text=preview, message=ctx.t("img.msg.svg_copied", size=util.human_size(
        len(text.encode("utf-8")), ctx.lang)))


# ======================================================================================= ANIMATED

def _frames_to_dir(ctx: Context, frames: Sequence[Any], durations: Sequence[int], *, flatten_bg: Sequence[int] | None,
                   share: tuple[float, float] = (0.3, 0.6)) -> tuple[Path, Path]:
    """Write frames as PNG + ffmpeg concat lists with per-frame durations.

    ``list.txt`` sets ``option framerate 1000`` on every file so the PNG demuxer uses a 1 ms time
    base (otherwise timestamps are rounded to its default 1/25 s); ``list-plain.txt`` omits it and
    is the fallback for ffmpeg builds that reject the per-file option."""
    folder = _tmp(ctx, "frames")
    folder.mkdir(parents=True, exist_ok=True)
    exact = ["ffconcat version 1.0"]
    plain = ["ffconcat version 1.0"]
    n = len(frames)
    label = _label(ctx)
    last = ""
    for i, (fr, dur) in enumerate(zip(frames, durations)):
        ctx.check_cancel()
        name = f"f_{i + 1:06d}.png"
        img = ops.flatten(fr, flatten_bg) if flatten_bg is not None else fr
        img.save(folder / name, "PNG", compress_level=1)
        seconds = f"duration {max(0.01, dur / 1000.0):.3f}"
        exact += [f"file '{name}'", "option framerate 1000", seconds]
        plain += [f"file '{name}'", seconds]
        last = name
        ctx.progress(share[0] + (share[1] - share[0]) * (i + 1) / max(1, n), label)
    exact += [f"file '{last}'", "option framerate 1000"]
    plain += [f"file '{last}'"]
    (folder / "list-plain.txt").write_text("\n".join(plain) + "\n", encoding="utf-8")
    listing = folder / "list.txt"
    listing.write_text("\n".join(exact) + "\n", encoding="utf-8")
    return folder, listing


def _ffmpeg_frames(ctx: Context, listing: Path, args: Sequence[Any], part: Path, total: float) -> None:
    """Encode a frames concat list; retries without per-file options when ffmpeg rejects them."""
    try:
        ctx.ffmpeg(["-f", "concat", "-safe", "0", "-i", listing, *args, part], duration=total, label=_label(ctx))
    except ToolError as exc:
        if "option" not in (exc.details or "").lower() and "framerate" not in (exc.details or "").lower():
            raise
        log.debug("ffconcat per-file options rejected, using the plain list: %s", exc.details)
        ctx.ffmpeg(["-f", "concat", "-safe", "0", "-i", listing.with_name("list-plain.txt"), *args, part],
                   duration=total, label=_label(ctx))


def _anim_to_video(ctx: Context, codec: str) -> Result:
    f = ctx.file
    fr = _read_frames(ctx, f, share=0.3)
    transparent = any(ops.has_alpha(x) for x in fr.frames)
    bg = ops.parse_color(ctx.pstr("background") or "#ffffff")[:3]
    flatten_bg = bg if (codec == "mp4" or not transparent) else None
    if codec == "mp4" and transparent:
        _emit_warnings(ctx, ["warn.transparency_flattened"], bg)
    _folder, listing = _frames_to_dir(ctx, fr.frames, fr.durations, flatten_bg=flatten_bg)
    total = sum(fr.durations) / 1000.0
    scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos"
    args: list[Any] = []
    if codec == "mp4":
        preset = str(_cfg(ctx, "video.preset", "medium") or "medium")
        args += ["-vf", f"{scale},format=yuv420p", "-fps_mode", "vfr", "-c:v", "libx264", "-preset", preset,
                 "-crf", "20", "-movflags", "+faststart", "-an"]
    else:
        pix = "yuva420p" if (transparent and flatten_bg is None) else "yuv420p"
        args += ["-vf", f"{scale},format={pix}", "-fps_mode", "vfr", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "30",
                 "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-an"]
        if pix == "yuva420p":
            args += ["-auto-alt-ref", "0"]
    out = ctx.output_path(f, ext=codec)
    with atomic_target(out) as part:
        _ffmpeg_frames(ctx, listing, args, part, total)
    return ctx.result([out])


@action(id="image.anim.convert.mp4", label="MP4", text="MP4", icon="movie", categories={"image"}, when=_ANIM_ONLY,
        mode="quick", params=[_bg_param()], requires=["ffmpeg"], description="img.desc.anim.mp4", order=200)
def anim_mp4(ctx: Context) -> Result:
    """Animation → H.264 MP4 (exact frame timing, transparency flattened)."""
    return _anim_to_video(ctx, "mp4")


@action(id="image.anim.convert.webm", label="WEBM", text="WEBM", icon="movie", categories={"image"},
        when=_ANIM_ONLY, mode="instant", requires=["ffmpeg"], description="img.desc.anim.webm", order=201)
def anim_webm(ctx: Context) -> Result:
    """Animation → VP9 WebM (transparency kept)."""
    return _anim_to_video(ctx, "webm")


@action(id="image.anim.convert.webp", label="WEBP", text="WEBP", icon="animation", categories={"image"},
        when=_anim_only(lambda f: is_animated(f) and _fmt(f) != "webp"), mode="quick",
        params=[_quality_param(75), P.bool("lossless", "img.prm.lossless", False)], requires=["py:PIL"],
        description="img.desc.anim.webp", order=202)
def anim_webp(ctx: Context) -> Result:
    """Animation → animated WebP (loop kept)."""
    f = ctx.file
    fr = _read_frames(ctx, f, share=0.5)
    out = ctx.output_path(f, ext="webp")
    ctx.progress(0.6, _label(ctx))
    with atomic_target(out) as part:
        ops.save_animation(fr.frames, fr.durations, part, "webp", loop=fr.loop, quality=ctx.pint("quality", 75),
                           lossless=ctx.pbool("lossless"))
    return ctx.result([out])


def _uniform(durations: Sequence[int]) -> bool:
    return bool(durations) and max(durations) <= 1.5 * max(1, min(durations))


@action(id="image.anim.convert.gif", label="GIF", text="GIF", icon="gif", categories={"image"},
        when=_anim_only(lambda f: is_animated(f) and _fmt(f) != "gif"), mode="instant", requires=["py:PIL"],
        description="img.desc.anim.gif", order=203)
def anim_gif(ctx: Context) -> Result:
    """Animation → GIF (gifski when installed and the timing is uniform, else Pillow)."""
    f = ctx.file
    fr = _read_frames(ctx, f, share=0.3)
    out = ctx.output_path(f, ext="gif")
    if tools.have("gifski") and _uniform(fr.durations):
        folder, _listing = _frames_to_dir(ctx, fr.frames, fr.durations, flatten_bg=None)
        median = sorted(fr.durations)[len(fr.durations) // 2]
        fps = max(1, min(50, int(round(1000.0 / max(1, median)))))
        # --width: by default gifski halves anything above about 800×600; never --height too, it would stretch
        args: list[Any] = ["gifski", "--fps", str(fps), "--quality", "90", "--width", str(fr.size[0])]
        if fr.loop is None:
            args.append("--repeat=-1")            # "=": a separate "-1" could be parsed as a flag
        elif fr.loop > 0:
            args.append(f"--repeat={fr.loop}")
        frames = sorted(folder.glob("f_*.png"))
        ctx.progress(None, _label(ctx))
        with atomic_target(out) as part:
            ctx.run([*args, "-o", part, *frames], timeout=3600)
        return ctx.result([out])
    ctx.progress(0.6, _label(ctx))
    with atomic_target(out) as part:
        ops.save_animation(fr.frames, fr.durations, part, "gif", loop=fr.loop)
    return ctx.result([out])


@action(id="image.anim.convert.first_frame", label="img.act.anim.first_frame", icon="image", categories={"image"},
        when=_ANIM_ONLY, mode="instant", requires=["py:PIL"], description="img.desc.anim.first_frame", order=204)
def anim_first_frame(ctx: Context) -> Result:
    """First frame as PNG."""
    f = ctx.file
    im, meta = _load(ctx, f)
    out = ctx.output_path(f, ext="png", suffix="img.sfx.first_frame")
    _write(ctx, im, meta, out, "png")
    return ctx.result([out])


@action(id="image.anim.frames.extract", label="img.act.anim.extract", icon="burst_mode", categories={"image"},
        when=_ANIM, mode="instant", requires=["py:PIL"], description="img.desc.anim.extract", order=210)
def anim_extract(ctx: Context) -> Result:
    """Every frame as PNG in ``<stem> (fotogrammi)/frame-0001.png``."""
    f = ctx.file
    fr = _read_frames(ctx, f, share=0.4)
    folder = ctx.output_dir(f, suffix="sfx.frames")
    n = len(fr.frames)
    width = max(4, len(str(n)))
    label = _label(ctx)
    for i, frame in enumerate(fr.frames):
        ctx.check_cancel()
        frame.save(folder / f"frame-{i + 1:0{width}d}.png", "PNG", optimize=False)
        ctx.progress(0.4 + 0.6 * (i + 1) / n, label)
    return ctx.result([folder], message=i18n.tn("img.msg.frames", n, ctx.lang))


@action(id="image.anim.frames.reverse", label="img.act.anim.reverse", icon="fast_rewind", categories={"image"},
        when=_any(lambda f: is_animated(f)), mode="instant", replace_ok=True, requires=["py:PIL"],
        description="img.desc.anim.reverse", order=211)
def anim_reverse(ctx: Context) -> Result:
    """Play backwards (gifsicle frame selection for GIF, else Pillow)."""
    f = ctx.file
    if _fmt(f) == "gif" and tools.have("gifsicle"):
        with ops.open_image(f.path, f.name) as probe:
            n = int(getattr(probe, "n_frames", 1) or 1)
        if 1 < n <= 20000:
            tmp = _tmp(ctx, "rev.gif")
            ctx.progress(None, _label(ctx))
            ctx.run(["gifsicle", "--unoptimize", f.path, *[f"#{i}" for i in range(n - 1, -1, -1)], "-O2", "-o", tmp],
                    timeout=1800)
            out = ctx.output_path(f, ext="gif", suffix="sfx.reversed")
            _deliver(ctx, tmp, out)
            return ctx.result([ctx.replace_original(f, out)])
    fr = _read_frames(ctx, f)
    return _write_frames(ctx, f, list(reversed(fr.frames)), list(reversed(fr.durations)), fr.loop, "sfx.reversed")


_SPEED_OPTIONS = [(0.25, "0,25×"), (0.5, "0,5×"), (0.75, "0,75×"), (1.5, "1,5×"), (2.0, "2×"), (3.0, "3×"),
                  (4.0, "4×")]


@action(id="image.anim.frames.speed", label="img.act.anim.speed", icon="speed", categories={"image"},
        when=_ANIM, mode="quick", params=[P.select("factor", "img.prm.speed", options=_SPEED_OPTIONS, default=2.0)],
        replace_ok=True, requires=["py:PIL"], description="img.desc.anim.speed", order=212)
def anim_speed(ctx: Context) -> Result:
    """Change the playback speed (frames under 20 ms are merged so the timing stays right)."""
    f = ctx.file
    factor = ctx.pfloat("factor", 2.0)
    if not 0.05 <= factor <= 20:
        raise UserError("err.invalid_param", param=ctx.t("img.prm.speed"))
    fr = _read_frames(ctx, f)
    fmt = _anim_fmt_out(f)
    plan = ops.retime(fr.durations, factor, min_ms=20, quantum=10 if fmt == "gif" else 1)
    frames = [fr.frames[i] for i, _d in plan]
    durations = [d for _i, d in plan]
    suffix = f"{ctx.t('sfx.speed')} {factor:g}x"
    return _write_frames(ctx, f, frames, durations, fr.loop, suffix)


def _anim_compress(ctx: Context, preset: str) -> Result:
    f = ctx.file
    before = ctx.original(f).size or f.size
    fmt = _fmt(f)
    if fmt == "gif":
        args = list(_GIFSICLE[preset]) + (["--scale", "0.75"] if preset == "strong" else [])
        res = _gifsicle(ctx, f, args, "sfx.compressed", compare=True)
        if res is not None:
            return res
        _warn_key(ctx, "img.warn.gifsicle_missing")
    fr = _read_frames(ctx, f)
    frames = fr.frames
    if preset == "strong":
        w, h = fr.size
        size = (max(1, round(w * 0.75)), max(1, round(h * 0.75)))
        frames = [ops.resize(x, size) for x in frames]
    out_fmt = fmt if fmt in ("gif", "webp") else "webp"
    tmp = _tmp(ctx, f"c.{out_fmt}")
    quality = {"light": 85, "balanced": 70, "strong": 55}[preset]
    colors = {"light": 255, "balanced": 192, "strong": 96}[preset]
    ops.save_animation(frames, fr.durations, tmp, out_fmt, loop=fr.loop, quality=quality, gif_colors=colors)
    return _finish_compress(ctx, f, tmp, out_fmt, before)


for _preset, _icon, _order in (("balanced", "compress", 220), ("light", "density_small", 221),
                               ("strong", "density_large", 222)):
    def _anim_c(ctx: Context, _p: str = _preset) -> Result:
        """Compress an animation (gifsicle -O3 --lossy for GIF, re-encode for WebP); never enlarges."""
        return _anim_compress(ctx, _p)

    _anim_c.__name__ = f"anim_compress_{_preset}"
    action(id=f"image.anim.compress.{_preset}", label=f"img.act.compress.{_preset}", icon=_icon,
           categories={"image"}, when=_anim_only(_anim_fmt("gif", "webp"), still=_compressible), mode="instant",
           replace_ok=True, requires=["py:PIL"], description=f"img.desc.anim.compress.{_preset}",
           order=_order)(_anim_c)


def _anim_resize(ctx: Context, *, edge: int | None = None, pct: float | None = None,
                 box: tuple[int, int] | None = None, suffix: str) -> Result:
    f = ctx.file
    w, h = f.width or 0, f.height or 0
    if not (w and h):
        with ops.open_image(f.path, f.name) as probe:
            w, h = probe.size
    if edge:
        size = ops.shrink_long_edge(w, h, edge)
    elif pct:
        size = ops.scale_pct(w, h, pct)
    elif box:
        plan = ops.plan_resize(w, h, box[0], box[1], "fit", upscale=False)
        size = plan.size if plan else None
    else:
        size = None
    if size is None or size == (w, h):
        return ctx.result(kind="none", message=ctx.t("img.msg.already_small", w=w, h=h))
    res = _gifsicle(ctx, f, ["--resize", f"{size[0]}x{size[1]}", "--resize-method", "lanczos3",
                             "--resize-colors", "128"], suffix)
    if res is not None:
        return res
    return _anim_map(ctx, f, lambda fr: ops.resize(fr, size), suffix)


for _edge, _order in ((480, 230), (720, 231), (320, 232)):
    def _anim_r(ctx: Context, _e: int = _edge) -> Result:
        """Resize an animation (long edge, shrink only)."""
        return _anim_resize(ctx, edge=_e, suffix=f"{_e}px")

    _anim_r.__name__ = f"anim_resize_{_edge}"
    action(id=f"image.anim.resize.{_edge}", label=f"{_edge} px", text=str(_edge), icon="photo_size_select_large",
           categories={"image"}, when=_ANIM_ONLY, mode="instant", replace_ok=True, requires=["py:PIL"],
           description="img.desc.resize.edge", order=_order)(_anim_r)


@action(id="image.anim.resize.50pct", label="50 %", text="50%", icon="photo_size_select_small", categories={"image"},
        when=_ANIM_ONLY, mode="instant", replace_ok=True, requires=["py:PIL"], description="img.desc.resize.pct",
        order=233)
def anim_resize_half(ctx: Context) -> Result:
    """Half size."""
    return _anim_resize(ctx, pct=50, suffix="50%")


@action(id="image.anim.resize.custom", label="img.act.resize.custom", icon="aspect_ratio", categories={"image"},
        when=_ANIM_ONLY, mode="dialog", dialog="form",
        params=[P.int("width", "img.prm.width", 480, min=0, max=8192, help="img.prm.size.help"),
                P.int("height", "img.prm.height", 0, min=0, max=8192)],
        replace_ok=True, requires=["py:PIL"], description="img.desc.resize.custom", order=234)
def anim_resize_custom(ctx: Context) -> Result:
    """Fit inside width × height (0 = proportional), shrink only."""
    w, h = max(0, ctx.pint("width", 0)), max(0, ctx.pint("height", 0))
    if not w and not h:
        raise UserError("img.err.no_size")
    return _anim_resize(ctx, box=(w, h), suffix=f"{w}×{h}" if w and h else f"{w or h}px")


@action(id="image.anim.copy", label="img.act.copy", icon="content_copy", categories={"image"}, when=_ANIM,
        multi="single", mode="instant", undoable=False, requires=["wl-copy"], description="img.desc.copy", order=240)
def anim_copy(ctx: Context) -> Result:
    """Copy the animation to the clipboard (GIF bytes; other formats as their first frame)."""
    return copy_image(ctx)


# ======================================================================================== COMBINE

def _combine_name(ctx: Context, files: Sequence[FileInfo], ext: str, suffix: str) -> Path:
    return ctx.output_path(files[0], ext=ext, suffix=suffix)


def _raster_for_combine(ctx: Context, f: FileInfo, edge: int | None = None) -> Any:
    """Oriented sRGB raster of an image (first frame) or SVG, optionally shrunk to ``edge``."""
    if f.category == "svg":
        im, meta = _load_any(ctx, f, size=edge or 2048)
    else:
        im, meta = _load(ctx, f)
    if im.mode == "CMYK" or (meta.icc and ops.icc_colorspace(meta.icc) == "RGB"):
        im = ops.to_srgb(im, meta.icc)
    im = ops.normalize_mode(ops.to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if edge:
        im = _shrink(im, edge)
    return im


@action(id="image.combine.pdf", label="img.act.combine.pdf", icon="picture_as_pdf", categories={"image", "svg"},
        multi="combine", mode="quick",
        params=[P.select("page", "img.prm.page", options=[("image", "img.opt.page.image"), ("a4", "A4"),
                                                          ("letter", "Letter")], default="image"),
                P.int("margin", "img.prm.margin", 10, min=0, max=100, unit="img.unit.mm", help="img.prm.margin.help"),
                _order_param()],
        requires=["py:PIL"], description="img.desc.combine.pdf", order=1)
def combine_pdf(ctx: Context) -> Result:
    """One PDF page per image (img2pdf lossless → PyMuPDF → Pillow); page = image size, A4 or Letter."""
    files = ordered_files(ctx.files, ctx.pstr("order", "name"), ctx)
    out = _combine_name(ctx, files, "pdf", "sfx.merged")
    with atomic_target(out) as part:
        _images_to_pdf(ctx, files, part, page=ctx.pstr("page", "image"), margin_mm=ctx.pfloat("margin", 10))
    return ctx.result([out], message=i18n.tn("img.msg.pages", len(files), ctx.lang))


@action(id="image.combine.anim", label="img.act.combine.anim", icon="animation", categories={"image", "svg"},
        multi="combine", mode="dialog", dialog="form",
        params=[P.select("format", "img.prm.format", options=[("gif", "GIF"), ("webp", "WebP")], default="gif"),
                P.float("fps", "img.prm.fps", 1.0, min=0.1, max=50, step=0.5, unit="img.unit.fps"),
                P.int("width", "img.prm.width", 800, min=0, max=4096, unit="img.unit.px", help="img.prm.width0.help"),
                P.bool("loop", "img.prm.loop", True),
                _order_param()],
        requires=["py:PIL"], description="img.desc.combine.anim", order=2)
def combine_anim(ctx: Context) -> Result:
    """Images → animated GIF (gifski when installed) or animated WebP."""
    files = ordered_files(ctx.files, ctx.pstr("order", "name"), ctx)
    fmt = "webp" if ctx.pstr("format", "gif") == "webp" else "gif"
    fps = max(0.1, min(50.0, ctx.pfloat("fps", 1.0)))
    width = max(0, ctx.pint("width", 800))
    label = _label(ctx)
    images = []
    n = len(files)
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(0.5 * i / n, label)
        images.append(_raster_for_combine(ctx, f, edge=max(width, 1) * 2 if width else 4096))
    w0, h0 = images[0].size
    W = min(4096, width or w0)
    H = max(1, min(4096, int(round(W * h0 / max(1, w0)))))
    from PIL import Image

    frames = []
    for im in images:
        ctx.check_cancel()
        canvas = Image.new("RGBA", (W, H), (255, 255, 255, 0 if fmt == "webp" else 255))
        fitted = ops.fit_into(im.convert("RGBA"), (W, H))
        canvas.alpha_composite(fitted, ((W - fitted.size[0]) // 2, (H - fitted.size[1]) // 2))
        frames.append(canvas)
    duration = int(round(1000.0 / fps))
    out = _combine_name(ctx, files, fmt, "img.sfx.animation")
    loop = 0 if ctx.pbool("loop", True) else None
    if fmt == "gif" and tools.have("gifski") and fps >= 1:
        folder, _listing = _frames_to_dir(ctx, frames, [duration] * len(frames), flatten_bg=None, share=(0.5, 0.8))
        # --width: by default gifski halves anything above about 800×600; never --height too, it would stretch
        args: list[Any] = ["gifski", "--fps", f"{fps:g}", "--quality", "90", "--width", str(W)]
        if loop is None:
            args.append("--repeat=-1")
        with atomic_target(out) as part:
            ctx.run([*args, "-o", part, *sorted(folder.glob("f_*.png"))], timeout=3600)
    else:
        ctx.progress(0.8, label)
        with atomic_target(out) as part:
            ops.save_animation(frames, [duration] * len(frames), part, fmt, loop=loop, quality=85)
    return ctx.result([out])


_RESOLUTIONS = [("1920x1080", "1920×1080 (16:9)"), ("1280x720", "1280×720 (16:9)"), ("3840x2160", "3840×2160 (4K)"),
                ("1080x1920", "1080×1920 (9:16)"), ("1080x1080", "1080×1080 (1:1)"), ("1080x1350", "1080×1350 (4:5)")]


@action(id="image.combine.video", label="img.act.combine.video", icon="slideshow", categories={"image", "svg"},
        multi="combine", mode="dialog", dialog="form",
        params=[P.float("duration", "img.prm.duration", 3.0, min=0.2, max=60, step=0.5, unit="img.unit.s"),
                P.select("resolution", "img.prm.resolution", options=_RESOLUTIONS, default="1920x1080"),
                P.select("fps", "img.prm.fps", options=[(24, "24"), (25, "25"), (30, "30"), (60, "60")], default=30),
                P.color("background", "img.prm.background", "#000000"),
                P.bool("fade", "img.prm.fade", True),
                _order_param()],
        requires=["ffmpeg"], description="img.desc.combine.video", order=3)
def combine_video(ctx: Context) -> Result:
    """Images → MP4 slideshow (H.264, each image letterboxed for ``duration`` seconds)."""
    from PIL import Image

    files = ordered_files(ctx.files, ctx.pstr("order", "name"), ctx)
    m = re.fullmatch(r"(\d+)x(\d+)", ctx.pstr("resolution", "1920x1080"))
    W, H = (int(m.group(1)), int(m.group(2))) if m else (1920, 1080)
    W, H = W - W % 2, H - H % 2
    seconds = max(0.2, min(60.0, ctx.pfloat("duration", 3.0)))
    fps = max(1, min(120, ctx.pint("fps", 30)))
    bg = ops.parse_color(ctx.pstr("background") or "#000000")[:3]
    label = _label(ctx)
    frames = []
    n = len(files)
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(0.3 * i / n, label)
        im = _raster_for_combine(ctx, f, edge=max(W, H))
        canvas = Image.new("RGB", (W, H), tuple(bg))
        fitted = ops.fit_into(im.convert("RGBA"), (W, H))
        canvas.paste(fitted, ((W - fitted.size[0]) // 2, (H - fitted.size[1]) // 2), fitted)
        frames.append(canvas)
    _folder, listing = _frames_to_dir(ctx, frames, [int(seconds * 1000)] * n, flatten_bg=None, share=(0.3, 0.4))
    total = seconds * n
    vf = f"fps={fps},format=yuv420p"
    if ctx.pbool("fade", True) and total > 1.2:
        d = min(0.6, total / 4)
        vf += f",fade=t=in:st=0:d={d:.2f},fade=t=out:st={max(0.0, total - d):.2f}:d={d:.2f}"
    preset = str(_cfg(ctx, "video.preset", "medium") or "medium")
    out = _combine_name(ctx, files, "mp4", "sfx.slideshow")
    with atomic_target(out) as part:
        _ffmpeg_frames(ctx, listing, ["-vf", vf, "-c:v", "libx264", "-preset", preset, "-crf", "20",
                                      "-movflags", "+faststart", "-an", "-t", f"{total:.3f}"], part, total)
    return ctx.result([out])


@action(id="image.combine.collage", label="img.act.combine.collage", icon="dashboard", categories={"image", "svg"},
        multi="combine", mode="dialog", dialog="form", mixed_ok=True,
        params=[P.select("layout", "img.prm.layout", options=[("grid", "img.opt.layout.grid"),
                                                              ("row", "img.opt.layout.row"),
                                                              ("column", "img.opt.layout.column")], default="grid"),
                P.int("columns", "img.prm.columns", 0, min=0, max=20, help="img.prm.columns.help"),
                P.int("cell", "img.prm.cell", 600, min=64, max=4096, unit="img.unit.px"),
                P.int("spacing", "img.prm.spacing", 16, min=0, max=256, unit="img.unit.px"),
                P.color("background", "img.prm.background", "#ffffff"),
                P.int("radius", "img.prm.radius", 0, min=0, max=512, unit="img.unit.px"),
                P.bool("labels", "img.prm.labels", False),
                _order_param()],
        requires=["py:PIL"], description="img.desc.combine.collage", order=4)
def combine_collage(ctx: Context) -> Result:
    """Grid / row / column collage or contact sheet (file names under the pictures)."""
    files = ordered_files(ctx.files, ctx.pstr("order", "name"), ctx)
    cell = max(64, min(4096, ctx.pint("cell", 600)))
    label = _label(ctx)
    images = []
    n = len(files)
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(0.8 * i / n, label)
        images.append(_raster_for_combine(ctx, f, edge=cell * 2))
    canvas = ops.make_collage(images, layout=ctx.pstr("layout", "grid"), columns=ctx.pint("columns", 0), cell=cell,
                              spacing=ctx.pint("spacing", 16), background=ctx.pstr("background") or "#ffffff",
                              labels=[f.name for f in files] if ctx.pbool("labels") else None,
                              radius=ctx.pint("radius", 0), font_path=_font_path())
    opaque = canvas.getchannel("A").getextrema()[0] == 255
    fmt = "jpg" if opaque else "png"
    out = _combine_name(ctx, files, fmt, "sfx.collage")
    ctx.progress(0.9, label)
    _write(ctx, canvas.convert("RGB") if opaque else canvas, None, out, fmt, quality=92, keep_metadata=False)
    return ctx.result([out], message=ctx.t("img.msg.resized", w=canvas.size[0], h=canvas.size[1]))


# ========================================================================================== MIXED

def _mixed_convert(target: str) -> Callable[[Context], Result]:
    def fn(ctx: Context) -> Result:
        outs: list[Path] = []
        errors: list[tuple[str, UserError]] = []
        files = list(ctx.files)
        label = _label(ctx)
        for i, f in enumerate(files):
            ctx.check_cancel()
            ctx.progress(i / len(files), f"{i + 1}/{len(files)} · {label}")
            if ops.norm_fmt(_fmt(f)) == target and f.category == "image":
                continue
            try:
                outs.append(_convert(ctx, target, f))
            except UserError as exc:
                errors.append((f.name, exc))
        if errors and not outs:
            first = errors[0][1]
            raise UserError(first.message, details="\n".join(f"{n}: {e.text(ctx.lang)}" for n, e in errors),
                            install=first.install, **first.fmt)
        for name, exc in errors:
            ctx.warn(f"{name}: {exc.text(ctx.lang)}")
        return ctx.result(outs)

    fn.__name__ = f"mixed_convert_{target}"
    fn.__doc__ = f"Convert every image and SVG of a mixed selection to {target.upper()}."
    return fn


# ids live under "image.convert." so the auto-built mixed ring groups them as "Convert" (ranked first);
# their predicate only matches image+SVG selections, so they never show up on single-category rings.
for _target, _order in (("png", 10), ("jpg", 11), ("webp", 12), ("pdf", 13)):
    action(id=f"image.convert.mixed_{_target}", label=_target.upper(), text=_target.upper(), icon="image",
           categories={"image", "svg"}, when=_mixed_image_svg, multi="any", mode="instant", mixed_ok=True,
           requires=["py:PIL"], description=f"img.desc.convert.{_target}", order=_order)(_mixed_convert(_target))


# ======================================================================================== LAYOUTS

group("image.convert.more", "grp.more", "more_horiz",
      ["image.convert.heic", "image.convert.gif", "image.convert.bmp", "image.convert.ico", "image.convert.svg"])
group("image.convert", "grp.convert", "swap_horiz",
      ["image.convert.png", "image.convert.jpg", "image.convert.webp", "image.convert.avif", "image.convert.pdf",
       "image.convert.jxl", "image.convert.tiff", "image.convert.more"],
      default=_convert_default)
group("image.compress", "grp.compress", "compress",
      ["image.compress.balanced", "image.compress.light", "image.compress.strong", "image.compress.web",
       "image.compress.lossless", "image.compress.target"],
      default="image.compress.balanced")
group("image.resize.social", "grp.social", "groups",
      ["image.resize.social.ig_portrait", "image.resize.social.ig_square", "image.resize.social.story",
       "image.resize.social.yt_thumb", "image.resize.social.x_post", "image.resize.social.linkedin",
       "image.resize.social.monitor"],
      default="image.resize.social.ig_portrait")
group("image.resize", "grp.resize", "photo_size_select_large",
      ["image.resize.1920", "image.resize.2560", "image.resize.1280", "image.resize.3840", "image.resize.50pct",
       "image.resize.25pct", "image.resize.custom", "image.resize.social"],
      default="image.resize.1920")
group("image.edit.flip", "grp.flip", "flip", ["image.edit.flip_h", "image.edit.flip_v"], default="image.edit.flip_h")
group("image.edit.more", "grp.more", "more_horiz", ["image.edit.invert", "image.edit.trim"])
group("image.edit", "grp.edit", "edit",
      ["image.edit.rotate_cw", "image.edit.rotate_ccw", "image.edit.flip", "image.edit.adjust", "image.edit.annotate",
       "image.edit.watermark", "image.edit.grayscale", "image.edit.more"],
      default="image.edit.rotate_cw")
group("image.background", "grp.background", "wallpaper",
      ["image.background.remove", "image.background.add", "image.background.flatten", "image.background.white",
       "image.background.crop_subject"],
      default="image.background.remove")
group("image.privacy", "grp.privacy", "privacy_tip",
      ["image.privacy.strip_gps", "image.privacy.strip_all", "image.privacy.redact", "image.privacy.metadata",
       "image.privacy.rename_date"],
      default="image.privacy.strip_gps")
group("image.ocr", "grp.ocr", "document_scanner", ["image.ocr.copy", "image.ocr.txt", "image.ocr.pdf"],
      default="image.ocr.copy")
group("image.upscale", "grp.upscale", "hd", ["image.upscale.x2", "image.upscale.x4", "image.upscale.anime4"],
      default="image.upscale.x2")
group("image.vectorize", "grp.vectorize", "polyline", ["image.vectorize.bw", "image.vectorize.color"],
      default="image.vectorize.bw")
group("image.icon", "grp.icon", "apps", ["image.icon.ico", "image.icon.png_set", "image.icon.favicon"],
      default="image.icon.ico")
group("image.more", "grp.more", "more_horiz",
      ["image.ocr", "image.qr", "image.upscale", "image.vectorize", "image.icon", "image.copy", "image.wallpaper",
       "image.print"],
      default="image.ocr.copy")

group("image.anim.convert", "grp.convert", "swap_horiz",
      ["image.anim.convert.mp4", "image.anim.convert.webm", "image.anim.convert.webp", "image.anim.convert.gif",
       "image.anim.convert.first_frame"],
      default="image.anim.convert.mp4")
group("image.anim.compress", "grp.compress", "compress",
      ["image.anim.compress.balanced", "image.anim.compress.light", "image.anim.compress.strong"],
      default="image.anim.compress.balanced")
group("image.anim.resize", "grp.resize", "photo_size_select_large",
      ["image.anim.resize.480", "image.anim.resize.720", "image.anim.resize.320", "image.anim.resize.50pct",
       "image.anim.resize.custom"],
      default="image.anim.resize.480")
group("image.anim.frames", "grp.frames", "burst_mode",
      ["image.anim.frames.extract", "image.anim.frames.reverse", "image.anim.frames.speed"],
      default="image.anim.frames.extract")

group("image.combine", "grp.combine", "join",
      ["image.combine.pdf", "image.combine.anim", "image.combine.video", "image.combine.collage"],
      default="image.combine.pdf")
group("image.multi.more", "grp.more", "more_horiz",
      ["image.background", "image.ocr", "image.upscale", "image.vectorize", "image.icon", "image.qr", "image.print",
       "image.anim.frames"],
      default="image.background.remove")

group("svg.convert.png", "PNG", "image",
      ["svg.convert.png.native", "svg.convert.png.512", "svg.convert.png.1024", "svg.convert.png.2048",
       "svg.convert.png.4096", "svg.convert.png.custom"],
      default="svg.convert.png.native", text="PNG")
group("svg.convert", "grp.convert", "swap_horiz",
      ["svg.convert.png", "svg.convert.pdf", "svg.convert.jpg", "svg.convert.webp", "svg.convert.ico"],
      default="svg.convert.png")

# Static image: Convert · Compress · Resize · Crop · Edit · Background · Privacy · More
# Animated image: Convert · Compress · Resize · Crop · Edit · Frames · Privacy · Copy
layout("image", ["image.convert", "image.anim.convert", "image.compress", "image.anim.compress", "image.resize",
                 "image.anim.resize", "image.crop.crop", "image.edit", "image.anim.frames", "image.background",
                 "image.privacy", "image.more", "image.anim.copy"])
# Each still/animated pair below is mutually exclusive (_anim_only), so a selection mixing still and
# animated images shows Convert · Compress · Resize once each and keeps Crop · Privacy · Edit on top.
# Frames lives in image.multi.more: a mix gets one flat More, and with animations only (every other
# item of that group wants a still image) the one-child group collapses to Frames on the top ring.
layout("multi:image", ["image.combine", "image.convert", "image.anim.convert", "image.compress",
                       "image.anim.compress", "image.resize", "image.anim.resize", "image.crop.crop",
                       "image.privacy", "image.edit", "image.multi.more"])
layout("svg", ["svg.convert", "svg.optimize", "svg.copy_text", "image.copy", "image.icon", "image.qr", "image.ocr",
               "image.print"])
layout("multi:svg", ["svg.convert", "image.combine", "svg.optimize", "image.icon", "image.print"])
