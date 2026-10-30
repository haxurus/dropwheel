"""Pure image helpers used by :mod:`dropwheel.actions.image` (no :class:`~dropwheel.jobs.Context`).

Everything here works on Pillow images, bytes or plain values so it can be unit-tested without
the job runner and reused by previews.  Pillow is imported lazily (SPEC §7: no third-party
imports at module top level).

Sections: formats · loading/orientation · colour (16-bit, ICC, CMYK, alpha) · saving ·
geometry (resize/crop/trim/D4 transforms) · edits (adjust, redact, watermark, background,
white→transparent) · composition (collage, icons) · metadata (JPEG/PNG segment rewriting, EXIF
dates, GPS) · animation (frames, retime) · misc parsers (OCR languages, zbar XML, SVG size and
minification, colours).
"""

from __future__ import annotations

import io
import math
import os
import re
import struct
import threading
import warnings
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

from ..errors import UserError

__all__ = [
    "Fmt", "FORMATS", "STATIC_TARGETS", "norm_fmt", "fmt_of", "register_plugins", "pil_can_save", "Meta",
    "open_image", "load_oriented", "capture_meta", "has_alpha", "flatten", "to_8bit", "icc_colorspace",
    "srgb_icc", "to_srgb", "normalize_mode", "prepare_for", "save_image", "FormatUnavailable", "encode",
    "parse_color", "hex_color", "shrink_long_edge", "scale_pct", "plan_resize", "apply_plan", "resize",
    "clamp_rect", "trim_box", "alpha_bbox", "D4", "orientation_op", "compose_ops", "jpegtran_args",
    "pil_transpose", "map_rect_to_raw", "jpeg_imcu", "adjust_image", "redact_image", "watermark_image",
    "compose_background", "white_to_transparent", "color_to_alpha_flatten", "square_pad", "ico_image",
    "ICO_SIZES", "PNG_SET_SIZES", "make_collage", "label_font", "exif_datetime", "parse_exif_datetime",
    "strip_gps_exif", "xmp_strip_gps", "jpeg_rewrite", "png_rewrite", "read_frames", "save_animation",
    "retime", "pick_ocr_languages", "parse_tesseract_langs", "parse_zbar_xml", "find_urls", "svg_text",
    "svg_intrinsic_size", "svg_minify", "is_svgz", "webp_is_lossless", "SOCIAL_PRESETS", "fit_into",
    "gradient_image", "parse_ratio", "MAX_SIDE", "MAX_ANIM_PIXELS",
]

#: Largest side we ever produce (SVG renders, upscales, canvases).
MAX_SIDE = 16384
#: Upper bound of frames × pixels decoded into memory for animated images (≈ 1.6 GB RGBA).
MAX_ANIM_PIXELS = 400_000_000

_PLUGIN_LOCK = threading.Lock()
_PLUGINS_DONE = False


# ============================================================================================ formats

@dataclass(frozen=True)
class Fmt:
    """An output format: Pillow name, extension and capabilities."""

    key: str
    pil: str
    ext: str
    mime: str
    alpha: bool        #: can store transparency
    icc: bool          #: can embed an ICC profile
    exif: bool         #: can embed EXIF
    lossy: bool        #: has a quality knob
    anim: bool = False  #: can store an animation (Pillow)


FORMATS: dict[str, Fmt] = {
    "png": Fmt("png", "PNG", "png", "image/png", True, True, True, False, True),
    "jpg": Fmt("jpg", "JPEG", "jpg", "image/jpeg", False, True, True, True),
    "webp": Fmt("webp", "WEBP", "webp", "image/webp", True, True, True, True, True),
    "avif": Fmt("avif", "AVIF", "avif", "image/avif", True, True, True, True, True),
    "heic": Fmt("heic", "HEIF", "heic", "image/heic", True, True, True, True),
    "jxl": Fmt("jxl", "JXL", "jxl", "image/jxl", True, True, True, True),
    "tiff": Fmt("tiff", "TIFF", "tiff", "image/tiff", True, True, True, False),
    "gif": Fmt("gif", "GIF", "gif", "image/gif", True, False, False, False, True),
    "bmp": Fmt("bmp", "BMP", "bmp", "image/bmp", False, False, False, False),
    "ico": Fmt("ico", "ICO", "ico", "image/vnd.microsoft.icon", True, False, False, False),
    "pdf": Fmt("pdf", "PDF", "pdf", "application/pdf", False, False, False, False),
}

#: Convert targets of a static raster image, in ring order.
STATIC_TARGETS = ("png", "jpg", "webp", "avif", "pdf", "jxl", "tiff", "heic", "gif", "bmp", "ico", "svg")

_ALIASES = {
    "jpeg": "jpg", "jpe": "jpg", "jfif": "jpg", "pjpeg": "jpg", "jpg": "jpg",
    "tif": "tiff", "tiff": "tiff", "heif": "heic", "hif": "heic", "heic": "heic", "heics": "heic",
    "apng": "png", "png": "png", "webp": "webp", "avif": "avif", "avifs": "avif", "jxl": "jxl", "gif": "gif",
    "bmp": "bmp", "dib": "bmp", "ico": "ico", "cur": "ico", "pdf": "pdf", "svg": "svg", "svgz": "svg",
}


def norm_fmt(value: str | None) -> str:
    """Normalise a format/extension name: ``jpeg``/``jfif`` → ``jpg``, ``tif`` → ``tiff``, ``heif`` → ``heic``…"""
    text = str(value or "").strip().lower().lstrip(".")
    return _ALIASES.get(text, text)


def fmt_of(info: Any) -> str:
    """Normalised format of a :class:`~dropwheel.detect.FileInfo` (content format first, then extension)."""
    fmt = norm_fmt(getattr(info, "format", "") or "")
    if fmt and fmt not in ("raw",):
        return fmt
    return norm_fmt(getattr(info, "ext", "") or "")


def register_plugins() -> None:
    """Register the optional Pillow plugins (HEIF via pillow_heif, JPEG XL via pillow_jxl) once."""
    global _PLUGINS_DONE
    if _PLUGINS_DONE:
        return
    with _PLUGIN_LOCK:
        if _PLUGINS_DONE:
            return
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
        except Exception:
            pass
        try:
            import pillow_jxl  # type: ignore  # noqa: F401  (registers on import)
        except Exception:
            pass
        _PLUGINS_DONE = True


def pil_can_save(fmt: str) -> bool:
    """``True`` when Pillow (with the optional plugins) can write ``fmt``."""
    spec = FORMATS.get(norm_fmt(fmt))
    if spec is None:
        return False
    register_plugins()
    from PIL import Image

    Image.init()
    return spec.pil in Image.SAVE


# ======================================================================================== loading

@dataclass
class Meta:
    """Metadata captured from a source image (before orientation is applied)."""

    fmt: str = ""
    icc: bytes | None = None
    exif: bytes | None = None          #: EXIF without the Orientation tag ("Exif\\0\\0" + TIFF)
    xmp: bytes | None = None
    dpi: tuple[float, float] | None = None
    orientation: int = 1
    n_frames: int = 1
    mode: str = ""
    size: tuple[int, int] = (0, 0)     #: raw (un-oriented) size
    qtables: Any = None                #: JPEG quantisation tables (re-encode with the same quality)
    subsampling: int | None = None     #: JPEG chroma subsampling (0 4:4:4, 1 4:2:2, 2 4:2:0)
    imcu: tuple[int, int] = (8, 8)     #: JPEG iMCU size in pixels
    extra: dict = field(default_factory=dict)


def open_image(path: str | os.PathLike, name: str = "") -> Any:
    """Open and load an image with Pillow; readable :class:`UserError` on failure.

    The returned image keeps its file open for multi-frame formats (close it, or use a ``with``).
    """
    register_plugins()
    from PIL import Image, UnidentifiedImageError

    shown = name or os.path.basename(os.fspath(path))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            im = Image.open(os.fspath(path))
        except Image.DecompressionBombError:
            raise UserError("img.err.too_large", name=shown) from None
        except UnidentifiedImageError:
            raise UserError("img.err.unreadable", name=shown) from None
        except FileNotFoundError:
            raise UserError("err.file_not_found", name=shown) from None
        except (OSError, SyntaxError, ValueError, EOFError) as exc:
            raise UserError("err.damaged", details=str(exc)) from None
        try:
            im.load()
        except Image.DecompressionBombError:
            im.close()
            raise UserError("img.err.too_large", name=shown) from None
        except (OSError, SyntaxError, ValueError, EOFError, struct.error) as exc:
            # truncated files: retry with the tolerant loader, else report damage
            from PIL import ImageFile

            prev = ImageFile.LOAD_TRUNCATED_IMAGES
            try:
                ImageFile.LOAD_TRUNCATED_IMAGES = True
                im.close()
                im = Image.open(os.fspath(path))
                im.load()
            except Exception:
                raise UserError("err.damaged", details=str(exc)) from None
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = prev
    return im


def capture_meta(im: Any) -> Meta:
    """Read ICC, EXIF (Orientation removed), XMP, DPI and JPEG encoder details from an open image."""
    meta = Meta(fmt=norm_fmt(im.format or ""), mode=im.mode, size=im.size)
    info = im.info or {}
    icc = info.get("icc_profile")
    meta.icc = icc if isinstance(icc, (bytes, bytearray)) and icc else None
    xmp = info.get("xmp") or info.get("XML:com.adobe.xmp")
    if isinstance(xmp, str):
        xmp = xmp.encode("utf-8")
    meta.xmp = bytes(xmp) if xmp else None
    dpi = info.get("dpi")
    try:
        if dpi and float(dpi[0]) > 1 and float(dpi[1]) > 1:
            meta.dpi = (float(dpi[0]), float(dpi[1]))
    except (TypeError, ValueError, IndexError):
        meta.dpi = None
    meta.n_frames = int(getattr(im, "n_frames", 1) or 1)
    try:
        # never mutate im.getexif(): it is the image's cached object and exif_transpose reads it later
        src = im.getexif()
        meta.orientation = int(src.get(0x0112, 1) or 1)
        if meta.orientation not in range(1, 9):
            meta.orientation = 1
        if len(src):
            from PIL import Image

            copy = Image.Exif()
            copy.load(src.tobytes())
            if 0x0112 in copy:
                del copy[0x0112]
            meta.exif = copy.tobytes() if len(copy) else None
    except Exception:
        meta.exif = None
    if meta.fmt == "jpg":
        try:
            from PIL import JpegImagePlugin

            meta.qtables = getattr(im, "quantization", None) or None
            meta.subsampling = JpegImagePlugin.get_sampling(im)
            if meta.subsampling is not None and meta.subsampling < 0:
                meta.subsampling = None
            layers = getattr(im, "layer", None) or []
            if layers:
                hmax = max(int(layer[1]) for layer in layers)
                vmax = max(int(layer[2]) for layer in layers)
                meta.imcu = (8 * max(1, hmax), 8 * max(1, vmax))
        except Exception:
            pass
    return meta


def load_oriented(path: str | os.PathLike, name: str = "", frame: int = 0) -> tuple[Any, Meta]:
    """Open ``path``, capture its metadata and return ``(image, meta)`` with EXIF orientation applied.

    The returned image is a standalone copy (the file is closed).
    """
    from PIL import ImageOps

    im = open_image(path, name)
    try:
        if frame:
            try:
                im.seek(frame)
            except EOFError:
                im.seek(0)
        meta = capture_meta(im)
        try:
            out = ImageOps.exif_transpose(im)
        except Exception:
            out = None
        if out is None or out is im:
            out = im.copy()
        out.info.pop("exif", None)
        return out, meta
    finally:
        try:
            im.close()
        except Exception:
            pass


# ========================================================================================= colour

def has_alpha(im: Any) -> bool:
    """``True`` when the image has a transparency channel that is not fully opaque."""
    mode = im.mode
    if mode == "P":
        if "transparency" not in im.info:
            return False
        return has_alpha(im.convert("RGBA"))
    if mode in ("RGBA", "LA", "PA", "RGBa", "La"):
        try:
            lo, _hi = im.getchannel("A").getextrema()
        except Exception:
            return True
        return lo < 255
    if mode in ("RGB", "L", "I", "I;16", "1") and "transparency" in im.info:
        return True
    return False


def flatten(im: Any, color: Sequence[int] = (255, 255, 255)) -> Any:
    """Composite the image on an opaque ``color`` background → ``RGB`` (``L`` stays ``L``)."""
    from PIL import Image

    rgb = tuple(int(c) for c in list(color)[:3]) or (255, 255, 255)
    if im.mode in ("P", "PA", "LA", "La", "RGBa") or (im.mode in ("RGB", "L") and "transparency" in im.info):
        im = im.convert("RGBA")
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, rgb)
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    if im.mode in ("RGB", "L"):
        return im
    if im.mode == "CMYK":
        return im.convert("RGB")
    return to_8bit(im).convert("RGB")


def to_8bit(im: Any) -> Any:
    """Scale 16/32-bit and float images down to 8 bit (``L``); other modes are returned unchanged."""
    mode = im.mode
    if mode.startswith("I;16"):
        return im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    if mode == "I":
        lo, hi = im.getextrema()
        if hi > 255:
            return im.point(lambda v: v * (1 / 256)).convert("L")
        return im.convert("L")
    if mode == "F":
        lo, hi = im.getextrema()
        if hi <= 1.0:
            return im.point(lambda v: v * 255).convert("L")
        if hi > 255:
            return im.point(lambda v: v * (255.0 / hi)).convert("L")
        return im.convert("L")
    return im


def icc_colorspace(icc: bytes | None) -> str:
    """Colour space of an ICC profile: ``"RGB"``, ``"GRAY"``, ``"CMYK"`` or ``""`` when unknown."""
    if not icc or len(icc) < 20:
        return ""
    space = bytes(icc[16:20]).decode("latin-1", "replace").strip().upper()
    return space if space in ("RGB", "GRAY", "CMYK", "LAB") else ""


_SRGB_CACHE: list[bytes] = []


def srgb_icc() -> bytes | None:
    """The bytes of a built-in sRGB ICC profile (``None`` when LittleCMS is unavailable)."""
    if _SRGB_CACHE:
        return _SRGB_CACHE[0]
    try:
        from PIL import ImageCms

        data = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:
        return None
    _SRGB_CACHE.append(data)
    return data


def _is_srgb(icc: bytes) -> bool:
    try:
        from PIL import ImageCms

        desc = ImageCms.ImageCmsProfile(io.BytesIO(icc)).profile.profile_description or ""
    except Exception:
        return False
    return "srgb" in desc.lower()


def to_srgb(im: Any, icc: bytes | None) -> Any:
    """Convert pixels from the ``icc`` profile to sRGB (CMYK → RGB too); returns the new image.

    Without a profile CMYK/LAB/YCbCr are converted with Pillow's built-in formulas.
    """
    mode = im.mode
    if mode in ("YCbCr", "LAB", "HSV"):
        return im.convert("RGB")
    if not icc:
        return im.convert("RGB") if mode == "CMYK" else im
    space = icc_colorspace(icc)
    if mode == "CMYK" and space != "CMYK":
        return im.convert("RGB")
    if mode in ("RGB", "RGBA") and space != "RGB":
        return im
    if mode in ("L", "LA", "1", "P", "PA") or mode.startswith("I") or mode == "F":
        return im
    if mode in ("RGB", "RGBA") and _is_srgb(icc):
        return im
    try:
        from PIL import ImageCms

        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        out_mode = "RGBA" if mode == "RGBA" else "RGB"
        return ImageCms.profileToProfile(im, src, dst, outputMode=out_mode,
                                         renderingIntent=ImageCms.Intent.PERCEPTUAL)
    except Exception:
        return im.convert("RGB") if mode == "CMYK" else im


def normalize_mode(im: Any) -> Any:
    """Bring exotic modes to something every encoder accepts (keeps alpha, 16-bit kept)."""
    mode = im.mode
    if mode == "P":
        return im.convert("RGBA") if "transparency" in im.info else im.convert("RGB")
    if mode in ("PA", "La", "RGBa", "RGBX"):
        return im.convert("RGBA")
    if mode == "1":
        return im.convert("L")
    if mode in ("YCbCr", "LAB", "HSV"):
        return im.convert("RGB")
    return im


def prepare_for(im: Any, fmt: str, *, icc: bytes | None = None, bg: Sequence[int] = (255, 255, 255),
                keep_cmyk: bool = False) -> tuple[Any, bytes | None, bool]:
    """Convert ``im`` so that format ``fmt`` can store it.

    Returns ``(image, icc_to_embed, flattened)``; ``flattened`` is ``True`` when real transparency
    was replaced by ``bg``.  CMYK becomes sRGB (except TIFF with ``keep_cmyk``), 16-bit becomes 8-bit
    where the format needs it, ICC profiles that no longer match the pixels are dropped and formats
    without ICC support get their pixels converted to sRGB.
    """
    fmt = norm_fmt(fmt)
    spec = FORMATS.get(fmt)
    flattened = False
    if im.mode == "CMYK" and not (fmt == "tiff" and keep_cmyk):
        im = to_srgb(im, icc)
        icc = srgb_icc() if icc else None
    im = normalize_mode(im)
    sixteen = im.mode.startswith("I") or im.mode == "F"
    if sixteen and fmt not in ("png", "tiff"):
        im = to_8bit(im)
    if sixteen and fmt == "png" and im.mode == "F":
        im = to_8bit(im)
    alpha = has_alpha(im)
    if spec is not None and not spec.alpha and alpha:
        im = flatten(im, bg)
        flattened = True
    elif im.mode in ("RGBA", "LA") and not alpha and fmt not in ("png", "tiff", "ico"):
        im = im.convert("RGB" if im.mode == "RGBA" else "L")
    if fmt in ("jpg", "bmp", "pdf") and im.mode not in ("L", "RGB", "CMYK"):
        im = flatten(im, bg) if im.mode in ("RGBA", "LA") else im.convert("RGB")
    if fmt in ("webp", "avif", "heic", "jxl", "ico") and im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if has_alpha(im) else "RGB")
    if fmt == "gif" and im.mode not in ("P", "L"):
        im = gif_quantize(im)
    # ICC: keep only when it matches the pixels; formats without ICC get sRGB pixels
    space = icc_colorspace(icc)
    if icc:
        family = "GRAY" if im.mode in ("L", "LA", "P", "1") or im.mode.startswith("I") else (
            "CMYK" if im.mode == "CMYK" else "RGB")
        if fmt == "gif" or (im.mode == "P"):
            icc = None
        elif space and space != family:
            icc = None
        elif spec is not None and not spec.icc:
            if family == "RGB" and space == "RGB":
                im = to_srgb(im, icc)
            icc = None
    return im, icc, flattened


def gif_quantize(im: Any, colors: int = 255) -> Any:
    """Quantise to a ``P`` image for GIF; transparent pixels map to index 255 (``info["transparency"]``)."""
    from PIL import Image

    im = normalize_mode(to_8bit(im) if im.mode.startswith("I") or im.mode == "F" else im)
    alpha = None
    if im.mode in ("RGBA", "LA"):
        alpha = im.getchannel("A")
        if alpha.getextrema()[0] >= 255:
            alpha = None
    rgb = im.convert("RGB")
    colors = max(2, min(255 if alpha is not None else 256, int(colors)))
    try:
        pal = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
    except Exception:
        pal = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors)
    palette = list(pal.getpalette() or [])[: 256 * 3]
    palette += [0] * (256 * 3 - len(palette))
    pal.putpalette(palette)
    if alpha is not None:
        mask = alpha.point(lambda a: 255 if a < 128 else 0)
        pal.paste(255, (0, 0) + pal.size, mask=mask)
        pal.info["transparency"] = 255
    return pal


# ========================================================================================= saving

class FormatUnavailable(Exception):
    """Pillow cannot write this format (missing plugin): use a CLI encoder."""

    def __init__(self, fmt: str) -> None:
        super().__init__(fmt)
        self.fmt = fmt


def _exif_for_save(exif: bytes | None) -> bytes | None:
    if not exif:
        return None
    return exif if exif.startswith(b"Exif\x00\x00") else b"Exif\x00\x00" + exif


def save_image(im: Any, target: Any, fmt: str, *, quality: int | None = None, meta: Meta | None = None,
               keep_metadata: bool = True, lossless: bool = False, bg: Sequence[int] = (255, 255, 255),
               icc: bytes | None | bool = True, progressive: bool = True, speed: int | None = None,
               keep_cmyk: bool = False, qtables: Any = None, subsampling: Any = None,
               ico_sizes: Sequence[int] | None = None, extra: dict | None = None) -> list[str]:
    """Encode ``im`` as ``fmt`` into ``target`` (path or binary file object).

    ``icc=True`` embeds the profile from ``meta`` (``False``/``None`` none, bytes = that profile).
    EXIF/XMP from ``meta`` are written when ``keep_metadata``; ``extra`` overrides encoder
    options (e.g. ``{"compression": "tiff_adobe_deflate"}``).  Returns warning keys
    (``"warn.transparency_flattened"``).  Raises :class:`FormatUnavailable` when Pillow lacks the
    encoder (AVIF/HEIF/JXL plugins).
    """
    fmt = norm_fmt(fmt)
    spec = FORMATS.get(fmt)
    if spec is None or fmt == "svg":
        raise ValueError(f"unsupported output format {fmt!r}")
    if not pil_can_save(fmt):
        raise FormatUnavailable(fmt)
    warns: list[str] = []
    src_icc: bytes | None
    if icc is True:
        src_icc = meta.icc if meta is not None else None
    elif isinstance(icc, (bytes, bytearray)):
        src_icc = bytes(icc)
    else:
        src_icc = None
    im, emb_icc, flattened = prepare_for(im, fmt, icc=src_icc, bg=bg, keep_cmyk=keep_cmyk)
    if flattened:
        warns.append("warn.transparency_flattened")
    kw: dict[str, Any] = {}
    if emb_icc and spec.icc:
        kw["icc_profile"] = emb_icc
    exif = _exif_for_save(meta.exif) if (meta is not None and keep_metadata and spec.exif) else None
    if exif:
        kw["exif"] = exif
    if meta is not None and keep_metadata and meta.xmp and fmt in ("jpg", "webp", "png", "tiff", "avif"):
        kw["xmp"] = meta.xmp
    if meta is not None and meta.dpi and fmt in ("jpg", "png", "tiff", "bmp"):
        kw["dpi"] = tuple(int(round(d)) for d in meta.dpi)
    if fmt == "jpg":
        if qtables is not None:
            kw["qtables"] = qtables
            if subsampling is not None:
                kw["subsampling"] = subsampling
        else:
            q = int(quality or 85)
            kw["quality"] = max(1, min(100, q))
            kw["subsampling"] = subsampling if subsampling is not None else (0 if q >= 90 else 2)
        kw["optimize"] = True
        kw["progressive"] = bool(progressive)
    elif fmt == "png":
        kw["optimize"] = False
        kw["compress_level"] = 6 if quality is None else max(0, min(9, int(quality)))
    elif fmt == "webp":
        kw["method"] = 4 if speed is None else max(0, min(6, int(speed)))
        if lossless:
            kw["lossless"] = True
            kw["quality"] = 100
            kw["exact"] = False
        else:
            kw["quality"] = max(1, min(100, int(quality or 80)))
    elif fmt == "avif":
        kw["quality"] = 100 if lossless else max(1, min(100, int(quality or 60)))
        kw["speed"] = 6 if speed is None else max(0, min(10, int(speed)))
        if lossless:
            kw["subsampling"] = "4:4:4"
    elif fmt == "heic":
        kw["quality"] = -1 if lossless else max(1, min(100, int(quality or 75)))
    elif fmt == "jxl":
        if lossless:
            kw["lossless"] = True
        else:
            kw["quality"] = max(1, min(100, int(quality or 90)))
    elif fmt == "tiff":
        kw["compression"] = "tiff_lzw"
    elif fmt == "gif":
        kw["optimize"] = True
        if "transparency" in im.info:
            kw["transparency"] = im.info["transparency"]
    elif fmt == "ico":
        im = ico_image(im)
        sizes = [s for s in (ico_sizes or ICO_SIZES) if s <= max(im.size)]
        kw["sizes"] = [(s, s) for s in sizes] or [(im.size[0], im.size[1])]
    elif fmt == "pdf":
        res = meta.dpi[0] if (meta is not None and meta.dpi) else 72.0
        kw["resolution"] = float(res)
    if extra:
        kw.update(extra)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            _pil_save(im, target, spec.pil, kw)
        except OSError:
            if fmt != "jpg" or not (kw.get("optimize") or kw.get("progressive")):
                raise
            _save_jpeg_big_buffer(im, target, spec.pil, kw)
    return warns


def _pil_save(im: Any, target: Any, pil_format: str, kw: dict[str, Any]) -> None:
    if hasattr(target, "write"):
        im.save(target, pil_format, **kw)
    else:
        im.save(os.fspath(target), pil_format, **kw)


_JPEG_BUFFER_LOCK = threading.Lock()


def _save_jpeg_big_buffer(im: Any, target: Any, pil_format: str, kw: dict[str, Any]) -> None:
    """Retry a JPEG save that ran out of Pillow's output buffer.

    With ``optimize`` or ``progressive`` libjpeg must write the whole file into one buffer, which
    Pillow sizes at about one byte per pixel.  Very detailed pictures at 4:4:4 need more, and the
    save fails with a generic "broken data stream" error.  The buffer is only enlarged for this
    retry, so ordinary saves keep their small allocation.  A concurrent save that sees the larger
    limit just allocates more for a moment.
    """
    from PIL import ImageFile

    if hasattr(target, "seek"):
        target.seek(0)
        target.truncate()
    with _JPEG_BUFFER_LOCK:
        previous = ImageFile.MAXBLOCK
        ImageFile.MAXBLOCK = max(previous, 6 * im.size[0] * im.size[1] + (1 << 20))
        try:
            _pil_save(im, target, pil_format, kw)
        finally:
            ImageFile.MAXBLOCK = previous


def encode(im: Any, fmt: str, **kw: Any) -> bytes:
    """:func:`save_image` into memory and return the bytes."""
    buf = io.BytesIO()
    save_image(im, buf, fmt, **kw)
    return buf.getvalue()


# ======================================================================================== colours

_NAMED = {
    "white": (255, 255, 255), "black": (0, 0, 0), "transparent": (0, 0, 0, 0), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "gray": (128, 128, 128), "grey": (128, 128, 128),
    "yellow": (255, 255, 0), "orange": (255, 165, 0),
}


def parse_color(value: Any, default: Sequence[int] = (255, 255, 255, 255)) -> tuple[int, int, int, int]:
    """Parse ``#rgb``, ``#rrggbb``, ``#rrggbbaa``, ``rrggbb``, ``rgb(…)``, names or tuples → RGBA."""
    dflt = tuple(int(c) for c in default)
    if len(dflt) == 3:
        dflt = dflt + (255,)
    if value is None or value == "":
        return dflt  # type: ignore[return-value]
    if isinstance(value, (tuple, list)):
        vals = [int(max(0, min(255, round(float(v))))) for v in value[:4]]
        if len(vals) == 3:
            vals.append(255)
        return tuple(vals) if len(vals) == 4 else dflt  # type: ignore[return-value]
    text = str(value).strip().lower()
    if text in _NAMED:
        c = _NAMED[text]
        return c if len(c) == 4 else c + (255,)  # type: ignore[return-value]
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", text)
    if m:
        r, g, b = (int(max(0, min(255, round(float(m.group(i)))))) for i in (1, 2, 3))
        a = m.group(4)
        alpha = 255 if a is None else int(round(max(0.0, min(1.0, float(a))) * 255)) if float(a) <= 1 else \
            int(max(0, min(255, float(a))))
        return (r, g, b, alpha)
    h = text.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{3,4}", h):
        h = "".join(ch * 2 for ch in h)
    if re.fullmatch(r"[0-9a-f]{6}([0-9a-f]{2})?", h):
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) if len(h) == 8 else 255
        return (r, g, b, a)
    return dflt  # type: ignore[return-value]


def hex_color(rgba: Sequence[int]) -> str:
    """``(r, g, b[, a])`` → ``#rrggbb`` (``#rrggbbaa`` when not opaque)."""
    vals = [int(v) for v in rgba]
    text = "#" + "".join(f"{v:02x}" for v in vals[:3])
    if len(vals) > 3 and vals[3] != 255:
        text += f"{vals[3]:02x}"
    return text


# ======================================================================================= geometry

#: Social presets: id → (width, height) — "fill" (cover + centre crop).
SOCIAL_PRESETS: dict[str, tuple[int, int]] = {
    "ig_portrait": (1080, 1350),
    "ig_square": (1080, 1080),
    "story": (1080, 1920),
    "yt_thumb": (1280, 720),
    "x_post": (1600, 900),
    "linkedin": (1200, 627),
}


def shrink_long_edge(w: int, h: int, edge: int) -> tuple[int, int] | None:
    """New size with the long edge = ``edge``; ``None`` when the image is already that small."""
    w, h, edge = int(w), int(h), int(edge)
    longest = max(w, h)
    if edge <= 0 or longest <= edge:
        return None
    s = edge / float(longest)
    return max(1, round(w * s)), max(1, round(h * s))


def scale_pct(w: int, h: int, pct: float) -> tuple[int, int]:
    """Size scaled by ``pct`` percent (at least 1×1)."""
    s = float(pct) / 100.0
    return max(1, round(int(w) * s)), max(1, round(int(h) * s))


@dataclass
class ResizePlan:
    """How to get a target size: resize to ``size`` then optionally crop ``crop`` (fill mode)."""

    size: tuple[int, int]
    crop: tuple[int, int, int, int] | None = None


def plan_resize(w: int, h: int, tw: int, th: int, mode: str = "fit", upscale: bool = True) -> ResizePlan | None:
    """PowerToys-like resize plan.

    * ``fit``: largest size inside ``tw×th`` keeping the ratio (``0`` = unconstrained side);
    * ``fill``: cover ``tw×th`` then centre-crop to exactly that size;
    * ``stretch``: exactly ``tw×th``.

    Returns ``None`` when the result equals the source, or when ``upscale`` is false and the
    image would be enlarged.
    """
    w, h, tw, th = int(w), int(h), int(tw or 0), int(th or 0)
    if w <= 0 or h <= 0 or (tw <= 0 and th <= 0):
        return None
    if mode == "stretch":
        nw, nh = tw or w, th or h
        if not upscale and (nw > w or nh > h):
            nw, nh = min(nw, w), min(nh, h)
        return None if (nw, nh) == (w, h) else ResizePlan((max(1, nw), max(1, nh)))
    if mode == "fill" and tw > 0 and th > 0:
        s = max(tw / w, th / h)
        if not upscale and s > 1:
            s = 1.0
            tw, th = min(tw, w), min(th, h)
        nw, nh = max(tw, round(w * s)), max(th, round(h * s))
        left, top = (nw - tw) // 2, (nh - th) // 2
        crop = (left, top, left + tw, top + th)
        if (nw, nh) == (w, h) and crop == (0, 0, w, h):
            return None
        return ResizePlan((nw, nh), None if crop == (0, 0, nw, nh) else crop)
    sx = tw / w if tw > 0 else float("inf")
    sy = th / h if th > 0 else float("inf")
    s = min(sx, sy)
    if not upscale and s >= 1:
        return None
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    return None if (nw, nh) == (w, h) else ResizePlan((nw, nh))


def resize(im: Any, size: tuple[int, int]) -> Any:
    """High-quality resize (Lanczos; alpha-correct)."""
    from PIL import Image

    size = (max(1, int(size[0])), max(1, int(size[1])))
    if im.size == size:
        return im.copy()
    if im.mode in ("P", "1"):
        im = normalize_mode(im)
    if im.mode == "RGBA":
        # premultiplied resize avoids dark fringes around transparent edges
        pm = im.convert("RGBa")
        return pm.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0).convert("RGBA")
    if im.mode.startswith("I;16"):
        return im.convert("I").resize(size, Image.Resampling.LANCZOS)
    return im.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0 if im.mode in ("RGB", "L", "LA") else None)


def apply_plan(im: Any, plan: ResizePlan) -> Any:
    """Apply a :class:`ResizePlan`."""
    out = resize(im, plan.size)
    if plan.crop is not None:
        out = out.crop(plan.crop)
    return out


def fit_into(im: Any, box: tuple[int, int], *, upscale: bool = True) -> Any:
    """Resize so the image fits in ``box`` keeping the ratio."""
    plan = plan_resize(im.size[0], im.size[1], box[0], box[1], "fit", upscale=upscale)
    return apply_plan(im, plan) if plan else im.copy()


def parse_ratio(text: Any) -> float | None:
    """``"16:9"`` / ``"4/5"`` / ``1.5`` → width/height ratio; ``None`` for free/auto/invalid."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*[:/x×]\s*(\d+(?:[.,]\d+)?)\s*", str(text))
    if not m:
        try:
            value = float(str(text).replace(",", "."))
        except ValueError:
            return None
        return value if value > 0 else None
    a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
    return a / b if a > 0 and b > 0 else None


def clamp_rect(rect: Any, w: int, h: int) -> tuple[int, int, int, int] | None:
    """``{x,y,w,h}`` (or a 4-sequence) in pixels → Pillow box clamped to the image; ``None`` if empty."""
    try:
        if isinstance(rect, dict):
            x, y = float(rect.get("x", 0)), float(rect.get("y", 0))
            rw, rh = float(rect.get("w", rect.get("width", 0))), float(rect.get("h", rect.get("height", 0)))
        else:
            x, y, rw, rh = (float(v) for v in list(rect)[:4])
    except (TypeError, ValueError):
        return None
    if any(math.isnan(v) or math.isinf(v) for v in (x, y, rw, rh)):
        return None
    left = int(round(max(0.0, min(x, w))))
    top = int(round(max(0.0, min(y, h))))
    right = int(round(max(0.0, min(x + rw, w))))
    bottom = int(round(max(0.0, min(y + rh, h))))
    if right - left < 1 or bottom - top < 1:
        return None
    return left, top, right, bottom


def alpha_bbox(im: Any, threshold: int = 8) -> tuple[int, int, int, int] | None:
    """Bounding box of the pixels whose alpha exceeds ``threshold`` (``None`` without alpha/visible pixels)."""
    if im.mode not in ("RGBA", "LA", "PA") and not (im.mode == "P" and "transparency" in im.info):
        return None
    alpha = im.convert("RGBA").getchannel("A")
    return alpha.point(lambda a: 255 if a > threshold else 0).getbbox()


def trim_box(im: Any, fuzz: float = 0.05) -> tuple[int, int, int, int] | None:
    """Box that removes uniform borders (the colour of the corners, ±``fuzz``); ``None`` when nothing to trim."""
    from PIL import Image, ImageChops

    w, h = im.size
    if w < 3 or h < 3:
        return None
    if has_alpha(im):
        corners = [im.convert("RGBA").getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        if sum(1 for c in corners if c[3] < 16) >= 3:
            box = alpha_bbox(im, 16)
            return None if box is None or box == (0, 0, w, h) else box
    rgb = flatten(im) if im.mode not in ("RGB", "L") else im
    rgb = rgb.convert("RGB")
    corners = [rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    best = max(corners, key=lambda c: sum(1 for d in corners if max(abs(c[i] - d[i]) for i in range(3)) <= 255 * fuzz))
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, best))
    r, g, b = diff.split()
    mx = ImageChops.lighter(r, ImageChops.lighter(g, b))
    thr = int(round(255 * max(0.0, min(1.0, fuzz))))
    box = mx.point(lambda v: 255 if v > thr else 0).getbbox()
    if box is None or box == (0, 0, w, h):
        return None
    return box


# ---------------------------------------------------------------------------- D4 transforms

#: Name → 2×2 integer matrix acting on (x right, y down) coordinates.
D4: dict[str, tuple[int, int, int, int]] = {
    "none": (1, 0, 0, 1),
    "flip_h": (-1, 0, 0, 1),
    "flip_v": (1, 0, 0, -1),
    "rot180": (-1, 0, 0, -1),
    "rot90": (0, -1, 1, 0),        # 90° clockwise
    "rot270": (0, 1, -1, 0),       # 90° counter-clockwise
    "transpose": (0, 1, 1, 0),
    "transverse": (0, -1, -1, 0),
}
_ORIENT_OP = {1: "none", 2: "flip_h", 3: "rot180", 4: "flip_v", 5: "transpose", 6: "rot90", 7: "transverse",
              8: "rot270"}
_JPEGTRAN = {"none": [], "flip_h": ["-flip", "horizontal"], "flip_v": ["-flip", "vertical"],
             "rot180": ["-rotate", "180"], "rot90": ["-rotate", "90"], "rot270": ["-rotate", "270"],
             "transpose": ["-transpose"], "transverse": ["-transverse"]}


def orientation_op(orientation: int) -> str:
    """D4 operation that displays raw pixels with EXIF ``orientation``."""
    return _ORIENT_OP.get(int(orientation or 1), "none")


def compose_ops(first: str, then: str) -> str:
    """The single D4 operation equal to applying ``first`` then ``then``."""
    a, b = D4[first], D4[then]
    # matrix product then·first
    m = (b[0] * a[0] + b[1] * a[2], b[0] * a[1] + b[1] * a[3], b[2] * a[0] + b[3] * a[2], b[2] * a[1] + b[3] * a[3])
    for name, mat in D4.items():
        if mat == m:
            return name
    raise ValueError("not a D4 element")  # pragma: no cover - closed group


def jpegtran_args(op: str) -> list[str]:
    """``jpegtran`` options for a D4 operation."""
    return list(_JPEGTRAN[op])


def pil_transpose(im: Any, op: str) -> Any:
    """Apply a D4 operation with Pillow."""
    from PIL import Image

    table = {
        "flip_h": Image.Transpose.FLIP_LEFT_RIGHT, "flip_v": Image.Transpose.FLIP_TOP_BOTTOM,
        "rot180": Image.Transpose.ROTATE_180, "rot90": Image.Transpose.ROTATE_270,
        "rot270": Image.Transpose.ROTATE_90, "transpose": Image.Transpose.TRANSPOSE,
        "transverse": Image.Transpose.TRANSVERSE,
    }
    if op == "none":
        return im.copy()
    return im.transpose(table[op])


def map_rect_to_raw(box: tuple[int, int, int, int], raw_size: tuple[int, int], orientation: int
                    ) -> tuple[int, int, int, int]:
    """Map a box in displayed (oriented) coordinates back to raw pixel coordinates."""
    op = orientation_op(orientation)
    m = D4[op]
    W, H = raw_size
    swapped = m[0] == 0
    Wd, Hd = (H, W) if swapped else (W, H)
    cx, cy, cdx, cdy = W / 2.0, H / 2.0, Wd / 2.0, Hd / 2.0
    pts = []
    for (px, py) in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        dx, dy = px - cdx, py - cdy
        # inverse of an orthogonal matrix = transpose
        rx = m[0] * dx + m[2] * dy
        ry = m[1] * dx + m[3] * dy
        pts.append((rx + cx, ry + cy))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return int(round(min(xs))), int(round(min(ys))), int(round(max(xs))), int(round(max(ys)))


def jpeg_imcu(meta: Meta) -> tuple[int, int]:
    """iMCU size of a JPEG (8×8 for 4:4:4/grey, 16×16 for 4:2:0…)."""
    return meta.imcu or (8, 8)


# ========================================================================================== edits

_ADJUST_KEYS = ("exposure", "brightness", "contrast", "saturation", "temperature", "tint", "sharpen", "vignette")


def _clamp100(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(v):
        return 0.0
    return max(-100.0, min(100.0, v))


def _vignette_mask(size: tuple[int, int], strength: float) -> Any:
    from PIL import Image

    n = 256
    data = []
    for y in range(n):
        fy = (y + 0.5) / n * 2 - 1
        for x in range(n):
            fx = (x + 0.5) / n * 2 - 1
            d = math.sqrt(fx * fx + fy * fy) / math.sqrt(2)
            t = max(0.0, min(1.0, (d - 0.3) / 0.7))
            t = t * t * (3 - 2 * t)
            data.append(int(round(255 * strength * t)))
    small = Image.new("L", (n, n))
    small.putdata(data)
    return small.resize(size, Image.Resampling.BICUBIC)


def adjust_image(im: Any, adjust: dict | None) -> Any:
    """Apply the "adjust" dialog values (−100..100, 0 = neutral): exposure (±2 EV), brightness,
    contrast, saturation, temperature, tint, sharpen (negative = soften) and vignette
    (negative = light corners).  Alpha is preserved; unknown keys are ignored."""
    from PIL import Image, ImageEnhance, ImageFilter

    vals = {k: _clamp100((adjust or {}).get(k, 0)) for k in _ADJUST_KEYS}
    im = normalize_mode(to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if im.mode == "CMYK":
        im = im.convert("RGB")
    alpha = im.getchannel("A") if im.mode in ("RGBA", "LA") else None
    gray = im.mode in ("L", "LA")
    rgb = im.convert("RGB")
    if vals["exposure"]:
        factor = 2.0 ** (vals["exposure"] / 50.0)
        lut = [int(round(255 * min(1.0, ((i / 255.0) ** 2.2 * factor)) ** (1 / 2.2))) for i in range(256)]
        rgb = rgb.point(lut * 3)
    if vals["brightness"]:
        rgb = ImageEnhance.Brightness(rgb).enhance(1.0 + vals["brightness"] / 100.0)
    if vals["contrast"]:
        rgb = ImageEnhance.Contrast(rgb).enhance(1.0 + vals["contrast"] / 100.0)
    if vals["saturation"] and not gray:
        rgb = ImageEnhance.Color(rgb).enhance(1.0 + vals["saturation"] / 100.0)
    if (vals["temperature"] or vals["tint"]) and not gray:
        t, n = vals["temperature"] / 100.0, vals["tint"] / 100.0
        r_gain = (1.0 + 0.22 * t) * (1.0 + 0.06 * n)
        g_gain = 1.0 - 0.16 * n
        b_gain = (1.0 - 0.22 * t) * (1.0 + 0.06 * n)
        rgb = rgb.convert("RGB", (r_gain, 0, 0, 0, 0, g_gain, 0, 0, 0, 0, b_gain, 0))
    if vals["sharpen"] > 0:
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=int(vals["sharpen"] * 2.5), threshold=2))
    elif vals["sharpen"] < 0:
        rgb = rgb.filter(ImageFilter.GaussianBlur(radius=-vals["sharpen"] / 100.0 * 4.0))
    if vals["vignette"]:
        mask = _vignette_mask(rgb.size, abs(vals["vignette"]) / 100.0)
        fill = Image.new("RGB", rgb.size, (0, 0, 0) if vals["vignette"] > 0 else (255, 255, 255))
        rgb = Image.composite(fill, rgb, mask)
    out = rgb.convert("L") if gray else rgb
    if alpha is not None:
        out = out.convert("LA" if gray else "RGBA")
        out.putalpha(alpha)
    return out


def redact_image(im: Any, boxes: Iterable[dict], *, color: Sequence[int] = (0, 0, 0)) -> tuple[Any, set[str]]:
    """Burn redaction boxes (normalised ``x,y,w,h`` 0..1) into a copy of ``im``.

    Styles: ``solid`` (default, ``color`` or the box's own ``color``), ``blur`` (heavy, irreversible
    down-sampling + blur) and ``pixelate``.  Returns ``(image, styles_used)``.
    """
    from PIL import Image, ImageDraw, ImageFilter

    out = normalize_mode(to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if out.mode == "CMYK":
        out = out.convert("RGB")
    out = out.copy()
    W, H = out.size
    used: set[str] = set()
    draw = ImageDraw.Draw(out)
    for box in boxes or []:
        if not isinstance(box, dict):
            continue
        try:
            x, y = float(box.get("x", 0)), float(box.get("y", 0))
            bw, bh = float(box.get("w", 0)), float(box.get("h", 0))
        except (TypeError, ValueError):
            continue
        if any(math.isnan(v) for v in (x, y, bw, bh)):
            continue
        left = int(math.floor(max(0.0, min(1.0, x)) * W))
        top = int(math.floor(max(0.0, min(1.0, y)) * H))
        right = int(math.ceil(max(0.0, min(1.0, x + bw)) * W))
        bottom = int(math.ceil(max(0.0, min(1.0, y + bh)) * H))
        if right - left < 1 or bottom - top < 1:
            continue
        style = str(box.get("style") or "solid").lower()
        if style not in ("solid", "blur", "pixelate"):
            style = "solid"
        used.add(style)
        region_box = (left, top, right, bottom)
        rw, rh = right - left, bottom - top
        if style == "solid":
            fill = parse_color(box.get("color"), tuple(color) + ((255,) if len(color) == 3 else ()))
            if out.mode in ("L", "LA"):
                lum = int(round(0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]))
                draw.rectangle([left, top, right - 1, bottom - 1], fill=(lum, 255) if out.mode == "LA" else lum)
            elif out.mode == "RGBA":
                draw.rectangle([left, top, right - 1, bottom - 1], fill=(fill[0], fill[1], fill[2], 255))
            else:
                draw.rectangle([left, top, right - 1, bottom - 1], fill=tuple(fill[:3]))
            continue
        region = out.crop(region_box)
        if style == "pixelate":
            block = max(6, min(rw, rh) // 8)
            small = region.resize((max(1, rw // block), max(1, rh // block)), Image.Resampling.BOX)
            region = small.resize((rw, rh), Image.Resampling.NEAREST)
        else:
            factor = max(8, min(rw, rh) // 6)
            small = region.resize((max(1, rw // factor), max(1, rh // factor)), Image.Resampling.BOX)
            region = small.resize((rw, rh), Image.Resampling.BILINEAR)
            region = region.filter(ImageFilter.GaussianBlur(radius=max(4.0, min(rw, rh) / 10.0)))
        out.paste(region, region_box)
    return out, used


_POSITIONS = ("tl", "t", "tr", "l", "c", "r", "bl", "b", "br")


def _place(pos: str, W: int, H: int, w: int, h: int, margin: int) -> tuple[int, int]:
    """Top-left corner of a ``w×h`` mark at 9-grid position ``pos`` (``tl t tr l c r bl b br``)."""
    pos = pos if pos in _POSITIONS else "br"
    vmap = {"t": 0, "b": 2}
    hmap = {"l": 0, "r": 2}
    if pos == "c":
        vertical = horizontal = 1
    elif len(pos) == 2:
        vertical, horizontal = vmap[pos[0]], hmap[pos[1]]
    elif pos in vmap:
        vertical, horizontal = vmap[pos], 1
    else:
        vertical, horizontal = 1, hmap[pos]
    x = (margin, (W - w) // 2, W - w - margin)[horizontal]
    y = (margin, (H - h) // 2, H - h - margin)[vertical]
    return max(0, x), max(0, y)


def label_font(size: int, font_path: str | None = None) -> Any:
    """A TrueType font of ``size`` px (``font_path``, else Pillow's bundled default)."""
    from PIL import ImageFont

    size = max(6, int(size))
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    for cand in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def watermark_image(im: Any, *, text: str = "", logo: Any = None, position: str = "br", opacity: float = 30,
                    scale: float = 20, tile: bool = False, color: Any = "#ffffff", font_path: str | None = None,
                    angle: float = -30.0) -> Any:
    """Overlay a text or logo watermark.

    ``scale`` is the watermark width in percent of the image width, ``opacity`` 0..100,
    ``position`` one of ``tl t tr l c r bl b br``; ``tile`` repeats it diagonally over the image.
    """
    from PIL import Image, ImageDraw

    base = normalize_mode(to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if base.mode == "CMYK":
        base = base.convert("RGB")
    had_alpha = base.mode in ("RGBA", "LA")
    canvas = base.convert("RGBA")
    W, H = canvas.size
    alpha_scale = max(0.0, min(100.0, float(opacity))) / 100.0
    target_w = max(8, int(round(W * max(1.0, min(100.0, float(scale))) / 100.0)))
    mark: Any
    if logo is not None:
        lg = normalize_mode(logo).convert("RGBA")
        ratio = target_w / float(max(1, lg.size[0]))
        lg = resize(lg, (target_w, max(1, round(lg.size[1] * ratio))))
        a = lg.getchannel("A").point(lambda v: int(v * alpha_scale))
        lg.putalpha(a)
        mark = lg
    else:
        text = str(text or "").strip() or "©"
        probe = label_font(100, font_path)
        bbox = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=probe)
        tw = max(1, bbox[2] - bbox[0])
        size = max(8, int(100 * target_w / tw))
        font = label_font(size, font_path)
        fill = parse_color(color, (255, 255, 255, 255))
        lum = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
        stroke = (0, 0, 0) if lum > 128 else (255, 255, 255)
        sw = max(1, size // 28)
        tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
        bb = tmp.textbbox((0, 0), text, font=font, stroke_width=sw)
        mw, mh = bb[2] - bb[0] + 2, bb[3] - bb[1] + 2
        mark = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
        d = ImageDraw.Draw(mark)
        a = int(round(fill[3] * alpha_scale))
        d.text((-bb[0] + 1, -bb[1] + 1), text, font=font, fill=(fill[0], fill[1], fill[2], a),
               stroke_width=sw, stroke_fill=stroke + (int(a * 0.45),))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if tile:
        rot = mark.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
        rw, rh = rot.size
        step_x, step_y = int(rw * 1.5) + 1, int(rh * 1.5) + 1
        row = 0
        # tiles never overlap (steps > tile size) and the layer is empty: a plain paste keeps the alpha;
        # the grid starts half a tile outside the top-left corner so the edges are covered too
        for y in range(-(rh // 2), H + rh, step_y):
            offset = (step_x // 2) if row % 2 else 0
            for x in range(-(rw // 2) - offset, W + rw, step_x):
                layer.paste(rot, (x, y))
            row += 1
    else:
        margin = int(round(min(W, H) * 0.03))
        if mark.size[0] > W or mark.size[1] > H:
            mark = fit_into(mark, (W, H))
        x, y = _place(position, W, H, mark.size[0], mark.size[1], margin)
        layer.paste(mark, (x, y))
    out = Image.alpha_composite(canvas, layer)
    if not had_alpha:
        out = out.convert("RGB") if base.mode != "L" else out.convert("L")
    return out


def gradient_image(size: tuple[int, int], colors: Sequence[Any], angle: float = 180.0) -> Any:
    """Linear gradient (CSS angle: 0 = to top, 90 = to right, 180 = to bottom) through ``colors``."""
    from PIL import Image

    w, h = max(1, int(size[0])), max(1, int(size[1]))
    stops = [parse_color(c, (255, 255, 255, 255)) for c in (colors or ["#ffffff"])] or [(255, 255, 255, 255)]
    if len(stops) == 1:
        return Image.new("RGBA", (w, h), stops[0])
    scale = min(1.0, 1024.0 / max(w, h))
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    side = int(math.ceil(math.hypot(sw, sh))) + 2
    ramp = Image.linear_gradient("L").resize((side, side), Image.Resampling.BICUBIC)
    ramp = ramp.rotate(180.0 - float(angle or 0), resample=Image.Resampling.BICUBIC)
    left, top = (side - sw) // 2, (side - sh) // 2
    ramp = ramp.crop((left, top, left + sw, top + sh))
    n = len(stops) - 1
    luts: list[list[int]] = [[], [], [], []]
    for i in range(256):
        pos = i / 255.0 * n
        k = min(n - 1, int(pos))
        t = pos - k
        a, b = stops[k], stops[k + 1]
        for ch in range(4):
            luts[ch].append(int(round(a[ch] + (b[ch] - a[ch]) * t)))
    out = Image.merge("RGBA", [ramp.point(luts[ch]) for ch in range(4)])
    return out.resize((w, h), Image.Resampling.BICUBIC) if (sw, sh) != (w, h) else out


def _cover(im: Any, size: tuple[int, int]) -> Any:
    plan = plan_resize(im.size[0], im.size[1], size[0], size[1], "fill", upscale=True)
    out = apply_plan(im, plan) if plan else im.copy()
    if out.size != tuple(size):
        out = out.resize(size)
    return out


def compose_background(im: Any, spec: dict | None, *, fill_image: Any = None) -> Any:
    """"Add background": rounded corners, drop shadow, padding, canvas ratio and a colour /
    gradient / blurred-image / image fill.  Values are in pixels of the original image.

    ``spec`` = ``{padding, radius, shadow, ratio: "auto"|"1:1"|"16:9"|"4:5"|"W:H",
    fill: {type: "color"|"gradient"|"blur"|"image", colors: [...], angle, image}}``.
    Returns an RGBA image (opaque unless the fill colours are transparent).
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    spec = dict(spec or {})

    def num(key: str, default: float, lo: float = 0.0, hi: float = 100000.0) -> float:
        try:
            v = float(spec.get(key, default))
        except (TypeError, ValueError):
            v = default
        if math.isnan(v):
            v = default
        return max(lo, min(hi, v))

    subject = normalize_mode(to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if subject.mode == "CMYK":
        subject = subject.convert("RGB")
    subject = subject.convert("RGBA")
    w, h = subject.size
    padding = int(round(num("padding", round(max(w, h) * 0.08))))
    radius = int(round(num("radius", 0, 0, min(w, h) / 2.0)))
    shadow = int(round(num("shadow", 0, 0, 2000)))
    if radius > 0:
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        subject.putalpha(ImageChops.multiply(subject.getchannel("A"), mask))
    cw, ch = w + 2 * padding, h + 2 * padding
    ratio = parse_ratio(spec.get("ratio")) if str(spec.get("ratio") or "auto").lower() not in ("auto", "free") else None
    if ratio:
        if cw / ch < ratio:
            cw = int(round(ch * ratio))
        else:
            ch = int(round(cw / ratio))
    cw, ch = min(cw, MAX_SIDE), min(ch, MAX_SIDE)
    fill = spec.get("fill") if isinstance(spec.get("fill"), dict) else {}
    ftype = str(fill.get("type") or "color").lower()
    colors = fill.get("colors") or fill.get("color") or ["#ffffff"]
    if isinstance(colors, str):
        colors = [colors]
    if ftype == "gradient":
        canvas = gradient_image((cw, ch), colors if len(colors) > 1 else list(colors) * 2,
                                float(fill.get("angle", 135) or 0))
    elif ftype in ("blur", "image"):
        source = fill_image if (ftype == "image" and fill_image is not None) else subject
        src = normalize_mode(source).convert("RGBA")
        canvas = _cover(src, (cw, ch))
        if ftype == "blur" or fill.get("blur"):
            radius_blur = max(8.0, max(cw, ch) / 30.0)
            small = canvas.resize((max(1, cw // 4), max(1, ch // 4)), Image.Resampling.BOX)
            small = small.filter(ImageFilter.GaussianBlur(radius_blur / 4.0))
            canvas = small.resize((cw, ch), Image.Resampling.BICUBIC)
            dim = Image.new("RGBA", (cw, ch), (0, 0, 0, 40))
            canvas = Image.alpha_composite(canvas, dim)
        if canvas.getchannel("A").getextrema()[0] < 255:
            opaque = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
            canvas = Image.alpha_composite(opaque, canvas)
    else:
        canvas = Image.new("RGBA", (cw, ch), parse_color(colors[0], (255, 255, 255, 255)))
    x, y = (cw - w) // 2, (ch - h) // 2
    if shadow > 0:
        sh_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        a = subject.getchannel("A").point(lambda v: int(v * 0.45))
        black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        black.putalpha(a)
        sh_layer.paste(black, (x, y + max(1, shadow // 3)))
        sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(max(1.0, shadow / 2.0)))
        canvas = Image.alpha_composite(canvas, sh_layer)
    canvas.alpha_composite(subject, (x, y))
    return canvas


def white_to_transparent(im: Any, fuzz: float = 0.15, *, edges_only: bool = True) -> Any:
    """Make near-white pixels transparent (soft edge).  ``edges_only`` keeps white areas that are
    not connected to the image border (e.g. eyes, text counters) opaque."""
    from PIL import Image, ImageChops, ImageDraw

    rgba = normalize_mode(to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if rgba.mode == "CMYK":
        rgba = rgba.convert("RGB")
    rgba = rgba.convert("RGBA")
    r, g, b, a = rgba.split()
    minc = ImageChops.darker(r, ImageChops.darker(g, b))
    fuzz = max(0.0, min(0.9, float(fuzz)))
    hi = 255 - int(round(255 * fuzz * 0.5))
    lo = 255 - int(round(255 * fuzz))
    span = max(1, hi - lo)
    # 0 where white, 255 where clearly not white, ramp in between
    keep = minc.point(lambda v: 0 if v >= hi else (255 if v <= lo else int(255 * (hi - v) / span)))
    if edges_only:
        region = minc.point(lambda v: 255 if v > lo else 0)
        w, h = region.size
        scale = min(1.0, 512.0 / max(w, h))
        small = region.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.NEAREST) \
            if scale < 1 else region.copy()
        sw, sh = small.size
        px = small.load()
        seeds = [(x, 0) for x in range(sw)] + [(x, sh - 1) for x in range(sw)] + \
                [(0, y) for y in range(sh)] + [(sw - 1, y) for y in range(sh)]
        for sx, sy in seeds:
            if px[sx, sy] == 255:
                ImageDraw.floodfill(small, (sx, sy), 128, thresh=0)
        connected = small.point(lambda v: 255 if v == 128 else 0)
        if scale < 1:
            connected = connected.resize((w, h), Image.Resampling.NEAREST)
        # pixels not connected to the border stay opaque
        keep = ImageChops.lighter(keep, ImageChops.invert(connected))
    rgba.putalpha(ImageChops.multiply(a, keep))
    return rgba


def color_to_alpha_flatten(im: Any, color: Any = "#ffffff") -> Any:
    """Transparent → colour: composite on an opaque background colour."""
    rgba = parse_color(color, (255, 255, 255, 255))
    return flatten(im, rgba[:3])


# ==================================================================================== composition

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SET_SIZES = (16, 32, 48, 64, 128, 256, 512)


def square_pad(im: Any) -> Any:
    """Centre the image on a transparent square canvas."""
    from PIL import Image

    rgba = normalize_mode(to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
    if rgba.mode == "CMYK":
        rgba = rgba.convert("RGB")
    rgba = rgba.convert("RGBA")
    w, h = rgba.size
    if w == h:
        return rgba
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(rgba, ((side - w) // 2, (side - h) // 2))
    return canvas


def ico_image(im: Any, base: int = 256) -> Any:
    """Square RGBA image of ``base`` px used as the ICO source (largest size first)."""
    sq = square_pad(im)
    if sq.size[0] != base:
        sq = resize(sq, (base, base))
    return sq


def make_collage(images: Sequence[Any], *, layout: str = "grid", columns: int = 0, cell: int = 600,
                 spacing: int = 16, background: Any = "#ffffff", labels: Sequence[str] | None = None,
                 radius: int = 0, font_path: str | None = None) -> Any:
    """Grid / row / column collage (contact sheet when ``labels`` are given).  Returns RGBA."""
    from PIL import Image, ImageChops, ImageDraw

    n = len(images)
    if n == 0:
        raise ValueError("no images")
    cell = max(32, min(4096, int(cell)))
    spacing = max(0, min(512, int(spacing)))
    bg = parse_color(background, (255, 255, 255, 255))
    font = label_font(max(10, cell // 18), font_path) if labels else None
    label_h = int(cell // 18 * 1.8) if labels else 0
    tiles = []
    for im in images:
        t = normalize_mode(to_8bit(im) if (im.mode.startswith("I") or im.mode == "F") else im)
        if t.mode == "CMYK":
            t = t.convert("RGB")
        tiles.append(t.convert("RGBA"))
    layout = layout if layout in ("grid", "row", "column") else "grid"
    placements: list[tuple[Any, int, int, int, int]] = []   # tile, x, y, slot_w, slot_h
    if layout == "row":
        scaled = [resize(t, (max(1, round(t.size[0] * cell / t.size[1])), cell)) for t in tiles]
        x = spacing
        for t in scaled:
            placements.append((t, x, spacing, t.size[0], cell))
            x += t.size[0] + spacing
        W, H = x, cell + label_h + 2 * spacing
    elif layout == "column":
        scaled = [resize(t, (cell, max(1, round(t.size[1] * cell / t.size[0])))) for t in tiles]
        y = spacing
        for t in scaled:
            placements.append((t, spacing, y, cell, t.size[1]))
            y += t.size[1] + label_h + spacing
        W, H = cell + 2 * spacing, y
    else:
        cols = int(columns) if columns and int(columns) > 0 else int(math.ceil(math.sqrt(n)))
        cols = max(1, min(cols, n))
        rows = int(math.ceil(n / cols))
        for i, t in enumerate(tiles):
            r, c = divmod(i, cols)
            fitted = fit_into(t, (cell, cell))
            x = spacing + c * (cell + spacing) + (cell - fitted.size[0]) // 2
            y = spacing + r * (cell + label_h + spacing) + (cell - fitted.size[1]) // 2
            placements.append((fitted, x, y, cell, cell))
        W = cols * cell + (cols + 1) * spacing
        H = rows * (cell + label_h) + (rows + 1) * spacing
    if W > MAX_SIDE or H > MAX_SIDE:
        raise UserError("img.err.collage_too_large", w=W, h=H)
    canvas = Image.new("RGBA", (W, H), bg)
    draw = ImageDraw.Draw(canvas)
    for i, (t, x, y, slot_w, slot_h) in enumerate(placements):
        if radius > 0:
            mask = Image.new("L", t.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, t.size[0] - 1, t.size[1] - 1],
                                                   radius=min(radius, min(t.size) // 2), fill=255)
            t = t.copy()
            t.putalpha(ImageChops.multiply(t.getchannel("A"), mask))
        canvas.alpha_composite(t, (x, y))
        if labels and font is not None and i < len(labels):
            text = str(labels[i])
            lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
            fg = (20, 20, 20, 255) if lum > 128 else (235, 235, 235, 255)
            if layout == "grid":
                top = y - (slot_h - t.size[1]) // 2 + slot_h + 4
                left = x - (slot_w - t.size[0]) // 2
            elif layout == "row":
                top, left = y + slot_h + 4, x
            else:
                top, left = y + slot_h + 4, x
            while text and draw.textlength(text, font=font) > slot_w and len(text) > 4:
                text = text[: len(text) - 2] + "…" if not text.endswith("…") else text[:-2] + "…"
            tw = draw.textlength(text, font=font)
            draw.text((left + max(0, (slot_w - tw) / 2), top), text, font=font, fill=fg)
    return canvas


# ======================================================================================= metadata

KEEP = object()   #: sentinel: keep the existing value

_EXIF_DT_RE = re.compile(r"^\s*(\d{4})[:\-/.](\d{1,2})[:\-/.](\d{1,2})[ T](\d{1,2})[:.](\d{1,2})(?:[:.](\d{1,2}))?")


def parse_exif_datetime(value: Any) -> datetime | None:
    """``"2024:05:01 14:32:10"`` (also ``-``/``T`` variants, sub-seconds, zones) → naive datetime."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("latin-1", "replace")
    text = str(value).strip().strip("\x00")
    m = _EXIF_DT_RE.match(text)
    if not m:
        return None
    parts = [int(g) if g else 0 for g in m.groups()]
    try:
        dt = datetime(parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])
    except ValueError:
        return None
    if dt.year < 1900:
        return None
    return dt


def exif_datetime(path: str | os.PathLike) -> datetime | None:
    """Capture date of a photo: EXIF DateTimeOriginal → DateTimeDigitized → DateTime (Pillow)."""
    register_plugins()
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with Image.open(os.fspath(path)) as im:
                exif = im.getexif()
                sub = {}
                try:
                    sub = exif.get_ifd(0x8769) or {}
                except Exception:
                    sub = {}
                for value in (sub.get(0x9003), sub.get(0x9004), exif.get(0x0132)):
                    dt = parse_exif_datetime(value)
                    if dt is not None:
                        return dt
    except Exception:
        return None
    return None


def strip_gps_exif(exif: bytes | None) -> bytes | None:
    """EXIF bytes without the GPS IFD (``None`` when nothing is left or the data is unreadable)."""
    if not exif:
        return None
    try:
        from PIL import Image

        ex = Image.Exif()
        ex.load(exif)
        if 0x8825 in ex:
            del ex[0x8825]
        if not len(ex):
            return None
        return ex.tobytes()
    except Exception:
        return None


_XMP_GPS_ATTR = re.compile(r"\s+[A-Za-z][\w.-]*:GPS[A-Za-z]*\s*=\s*(\"[^\"]*\"|'[^']*')")
_XMP_GPS_ELEM = re.compile(r"<([A-Za-z][\w.-]*:GPS[A-Za-z]*)\b[^>]*?(?:/>|>.*?</\1\s*>)", re.S)
_XMP_LOC_ATTR = re.compile(r"\s+(?:Iptc4xmpExt:LocationCreated|Iptc4xmpExt:LocationShown|photoshop:City|"
                           r"photoshop:State|photoshop:Country|Iptc4xmpCore:Location)\s*=\s*(\"[^\"]*\"|'[^']*')")
_XMP_LOC_ELEM = re.compile(r"<((?:Iptc4xmpExt:LocationCreated|Iptc4xmpExt:LocationShown|photoshop:City|"
                           r"photoshop:State|photoshop:Country|Iptc4xmpCore:Location))\b[^>]*?(?:/>|>.*?</\1\s*>)",
                           re.S)


def xmp_strip_gps(xmp: bytes | None) -> bytes | None:
    """Remove ``exif:GPS*`` (and location) properties from an XMP packet."""
    if not xmp:
        return xmp
    text = xmp.decode("utf-8", "replace")
    for rx in (_XMP_GPS_ELEM, _XMP_LOC_ELEM):
        text = rx.sub("", text)
    for rx in (_XMP_GPS_ATTR, _XMP_LOC_ATTR):
        text = rx.sub("", text)
    return text.encode("utf-8")


_XMP_SIG = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_EXT_SIG = b"http://ns.adobe.com/xmp/extension/\x00"


def _jpeg_parse(data: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """Split a JPEG into ``[(marker, full segment bytes)]`` up to and including EOI, plus the trailer."""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG")
    segs: list[tuple[int, bytes]] = [(0xD8, b"\xff\xd8")]
    i, n = 2, len(data)
    while i < n:
        if data[i] != 0xFF:
            raise ValueError("bad JPEG marker")
        j = i
        while j < n and data[j] == 0xFF:
            j += 1
        if j >= n:
            raise ValueError("truncated JPEG")
        marker = data[j]
        start = i
        i = j + 1
        if marker == 0xD9:
            segs.append((marker, data[start:i]))
            return segs, data[i:]
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            segs.append((marker, data[start:i]))
            continue
        if i + 2 > n:
            raise ValueError("truncated JPEG")
        length = struct.unpack(">H", data[i:i + 2])[0]
        if length < 2 or i + length > n:
            raise ValueError("bad JPEG segment length")
        i += length
        if marker == 0xDA:
            # entropy-coded data: up to the next marker that is not a stuffed 0xFF00 / RSTn
            k = i
            while k < n - 1:
                if data[k] == 0xFF and data[k + 1] != 0x00 and not (0xD0 <= data[k + 1] <= 0xD7) \
                        and data[k + 1] != 0xFF:
                    break
                k += 1
            else:
                k = n
            segs.append((marker, data[start:k]))
            i = k
            continue
        segs.append((marker, data[start:i]))
    return segs, b""


def _app_segment(marker: int, payload: bytes) -> bytes:
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("metadata segment too large")
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg_rewrite(data: bytes, *, exif: Any = KEEP, xmp: Any = KEEP, drop_iptc: bool = False,
                 drop_comments: bool = False, drop_other_app: bool = False, keep_icc: bool = True,
                 drop_trailer: bool = False) -> bytes:
    """Rewrite JPEG metadata segments losslessly (pixel data is copied byte for byte).

    ``exif``/``xmp``: :data:`KEEP`, ``None`` (remove) or new bytes.  ``drop_other_app`` removes
    APP3–APP13/APP15 and non-metadata APP1/APP2 (APP0 JFIF and APP14 Adobe are always kept);
    ``drop_trailer`` also removes data after EOI (MPF secondary images).
    """
    segs, trailer = _jpeg_parse(data)
    out: list[bytes] = []
    exif_done = exif is KEEP or exif is None
    xmp_done = xmp is KEEP or xmp is None
    for marker, seg in segs:
        payload = seg[4:] if len(seg) >= 4 and marker not in (0xD8, 0xD9, 0x01) and not 0xD0 <= marker <= 0xD7 \
            else b""
        if marker == 0xE1:
            if payload.startswith(b"Exif\x00\x00"):
                if exif is KEEP:
                    out.append(seg)
                elif exif is not None and not exif_done:
                    out.append(_app_segment(0xE1, _exif_for_save(exif) or b""))
                    exif_done = True
                continue
            if payload.startswith(_XMP_SIG):
                if xmp is KEEP:
                    out.append(seg)
                elif xmp is not None and not xmp_done:
                    out.append(_app_segment(0xE1, _XMP_SIG + bytes(xmp)))
                    xmp_done = True
                continue
            if payload.startswith(_XMP_EXT_SIG):
                if xmp is KEEP:
                    out.append(seg)
                continue
            if not drop_other_app:
                out.append(seg)
            continue
        if marker == 0xE2:
            if payload.startswith(b"ICC_PROFILE\x00"):
                if keep_icc:
                    out.append(seg)
                continue
            if payload.startswith(b"MPF\x00"):
                if not drop_trailer:
                    out.append(seg)
                continue
            if not drop_other_app:
                out.append(seg)
            continue
        if marker == 0xED:
            if not drop_iptc and not drop_other_app:
                out.append(seg)
            continue
        if marker == 0xFE:
            if not drop_comments:
                out.append(seg)
            continue
        if 0xE3 <= marker <= 0xEF and marker != 0xEE:
            if not drop_other_app:
                out.append(seg)
            continue
        out.append(seg)
    # new EXIF / XMP when the file had none: right after SOI (and APP0 JFIF when present)
    inserts: list[bytes] = []
    if not exif_done and exif is not None and exif is not KEEP:
        inserts.append(_app_segment(0xE1, _exif_for_save(exif) or b""))
    if not xmp_done and xmp is not None and xmp is not KEEP:
        inserts.append(_app_segment(0xE1, _XMP_SIG + bytes(xmp)))
    if inserts:
        pos = 1
        if len(out) > 1 and out[1][:2] == b"\xff\xe0":
            pos = 2
        out[pos:pos] = inserts
    return b"".join(out) + (b"" if drop_trailer else trailer)


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_XMP_KEY = b"XML:com.adobe.xmp"


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)


def png_rewrite(data: bytes, *, exif: Any = KEEP, xmp: Any = KEEP, drop_text: bool = False,
                drop_time: bool = False) -> bytes:
    """Rewrite PNG metadata chunks losslessly: ``eXIf`` (raw TIFF), XMP ``iTXt``, other text and ``tIME``."""
    if not data.startswith(_PNG_SIG):
        raise ValueError("not a PNG")
    pos, n = 8, len(data)
    chunks: list[tuple[bytes, bytes]] = []
    while pos + 8 <= n:
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > n:
            raise ValueError("truncated PNG")
        chunks.append((ctype, data[pos:end]))
        pos = end
        if ctype == b"IEND":
            break
    out = [_PNG_SIG]
    exif_done = exif is KEEP or exif is None
    xmp_done = xmp is KEEP or xmp is None
    for ctype, raw in chunks:
        payload = raw[8:-4]
        if ctype == b"eXIf":
            if exif is KEEP:
                out.append(raw)
            elif exif is not None and not exif_done:
                body = bytes(exif)
                if body.startswith(b"Exif\x00\x00"):
                    body = body[6:]
                out.append(_png_chunk(b"eXIf", body))
                exif_done = True
            continue
        if ctype in (b"tEXt", b"zTXt", b"iTXt"):
            is_xmp = ctype == b"iTXt" and payload.split(b"\x00", 1)[0] == _PNG_XMP_KEY
            if is_xmp:
                if xmp is KEEP and not drop_text:
                    out.append(raw)
                elif xmp is not None and xmp is not KEEP and not xmp_done:
                    out.append(_png_chunk(b"iTXt", _PNG_XMP_KEY + b"\x00\x00\x00\x00\x00" + bytes(xmp)))
                    xmp_done = True
                continue
            if not drop_text:
                out.append(raw)
            continue
        if ctype == b"tIME" and drop_time:
            continue
        if ctype == b"IDAT" and (not exif_done or not xmp_done):
            if not exif_done and exif is not None and exif is not KEEP:
                body = bytes(exif)
                if body.startswith(b"Exif\x00\x00"):
                    body = body[6:]
                out.append(_png_chunk(b"eXIf", body))
                exif_done = True
            if not xmp_done and xmp is not None and xmp is not KEEP:
                out.append(_png_chunk(b"iTXt", _PNG_XMP_KEY + b"\x00\x00\x00\x00\x00" + bytes(xmp)))
                xmp_done = True
        out.append(raw)
    return b"".join(out)


def read_jpeg_exif_xmp(data: bytes) -> tuple[bytes | None, bytes | None]:
    """EXIF (``Exif\\0\\0`` + TIFF) and XMP packet of a JPEG (``None`` when absent)."""
    exif = xmp = None
    try:
        segs, _ = _jpeg_parse(data)
    except ValueError:
        return None, None
    for marker, seg in segs:
        if marker != 0xE1:
            continue
        payload = seg[4:]
        if exif is None and payload.startswith(b"Exif\x00\x00"):
            exif = payload
        elif xmp is None and payload.startswith(_XMP_SIG):
            xmp = payload[len(_XMP_SIG):]
    return exif, xmp


def read_png_exif_xmp(data: bytes) -> tuple[bytes | None, bytes | None]:
    """EXIF (raw TIFF) and XMP packet of a PNG."""
    exif = xmp = None
    if not data.startswith(_PNG_SIG):
        return None, None
    pos, n = 8, len(data)
    while pos + 8 <= n:
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if ctype == b"eXIf" and exif is None:
            exif = payload
        elif ctype == b"iTXt" and xmp is None and payload.split(b"\x00", 1)[0] == _PNG_XMP_KEY:
            rest = payload[len(_PNG_XMP_KEY) + 1:]
            if len(rest) >= 2:
                compressed = rest[0] == 1
                parts = rest[2:].split(b"\x00", 2)
                if len(parts) == 3:
                    body = parts[2]
                    if compressed:
                        try:
                            body = zlib.decompress(body)
                        except zlib.error:
                            body = b""
                    xmp = body or None
        if ctype == b"IEND":
            break
        pos += 12 + length
    return exif, xmp


def webp_is_lossless(path: str | os.PathLike) -> bool:
    """``True`` for a lossless (VP8L) still WebP."""
    try:
        with open(os.fspath(path), "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return False
    if head[:4] != b"RIFF" or head[8:12] != b"WEBP":
        return False
    pos = 12
    while pos + 8 <= len(head):
        fourcc = head[pos:pos + 4]
        size = struct.unpack("<I", head[pos + 4:pos + 8])[0]
        if fourcc == b"VP8L":
            return True
        if fourcc in (b"VP8 ", b"ANMF", b"ALPH"):
            return False
        pos += 8 + size + (size & 1)
    return False


# ====================================================================================== animation

@dataclass
class Frames:
    """Decoded animation: full RGBA frames, per-frame durations (ms) and the loop count."""

    frames: list[Any]
    durations: list[int]
    loop: int | None        #: 0 = forever, N = N plays, None = once (GIF without NETSCAPE block)
    size: tuple[int, int]


def read_frames(path: str | os.PathLike, name: str = "", *, max_pixels: int = MAX_ANIM_PIXELS,
                check: Callable[[], None] | None = None, progress: Callable[[float], None] | None = None) -> Frames:
    """Decode every frame of a GIF / WebP / APNG / AVIF animation (composited, RGBA)."""
    im = open_image(path, name)
    try:
        n = int(getattr(im, "n_frames", 1) or 1)
        w, h = im.size
        if n * w * h > max_pixels:
            raise UserError("img.err.too_many_frames", n=n, w=w, h=h)
        info = dict(im.info or {})
        fmt = norm_fmt(im.format or "")
        if "loop" in info:
            try:
                loop: int | None = int(info["loop"])
            except (TypeError, ValueError):
                loop = 0
        else:
            loop = None if fmt == "gif" else 0
        frames: list[Any] = []
        durations: list[int] = []
        for i in range(n):
            if check is not None:
                check()
            try:
                im.seek(i)
            except EOFError:
                break
            # decode first: the WebP and AVIF plugins only refresh info["duration"] in load(), so
            # reading it right after seek() would give every frame the previous frame's delay
            frame = im.convert("RGBA")
            try:
                dur = int(round(float(im.info.get("duration", 100) or 0)))
            except (TypeError, ValueError):
                dur = 100
            if dur <= 10:
                dur = 100   # what browsers do for 0/10 ms GIF delays
            frames.append(frame.copy() if frame is im else frame)
            durations.append(dur)
            if progress is not None and n > 1:
                progress((i + 1) / n)
        if not frames:
            raise UserError("img.err.unreadable", name=name or os.path.basename(os.fspath(path)))
        return Frames(frames, durations, loop, (w, h))
    finally:
        try:
            im.close()
        except Exception:
            pass


def save_animation(frames: Sequence[Any], durations: Sequence[int], target: Any, fmt: str, *,
                   loop: int | None = 0, quality: int | None = None, lossless: bool = False,
                   gif_colors: int = 255) -> None:
    """Write an animation as GIF, WebP or APNG (``fmt`` = gif/webp/png)."""
    register_plugins()
    fmt = norm_fmt(fmt)
    if not frames:
        raise ValueError("no frames")
    durs = [max(10 if fmt != "gif" else 20, int(round(d))) for d in durations]
    if len(durs) < len(frames):
        durs += [durs[-1] if durs else 100] * (len(frames) - len(durs))
    if len(frames) == 1:
        single = frames[0]
        kw: dict[str, Any] = {}
        if fmt == "webp":
            kw = {"quality": int(quality or 80), "lossless": bool(lossless)}
        if fmt == "gif":
            single = gif_quantize(single, gif_colors)
            if "transparency" in single.info:
                kw["transparency"] = single.info["transparency"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            single.save(target, FORMATS[fmt].pil, **kw)
        return
    transparent = any(has_alpha(f) for f in frames)
    kw = {"save_all": True, "duration": durs}
    if fmt == "gif":
        seq = [gif_quantize(f, gif_colors) for f in frames]
        if loop is not None:
            kw["loop"] = int(loop)
        if transparent:
            kw["disposal"] = 2
            kw["transparency"] = 255
        kw["optimize"] = False
    elif fmt == "webp":
        seq = [f.convert("RGBA") if f.mode != "RGBA" else f for f in frames]
        kw.update({"loop": 1 if loop is None else int(loop), "quality": int(quality or 80), "method": 4,
                   "lossless": bool(lossless), "minimize_size": False, "allow_mixed": False})
        if not transparent:
            seq = [f.convert("RGB") for f in seq]
    elif fmt == "png":
        seq = [f.convert("RGBA") if transparent else f.convert("RGB") for f in frames]
        kw.update({"loop": 1 if loop is None else int(loop)})
        if transparent:
            kw["disposal"] = 1
    else:
        raise ValueError(f"cannot animate {fmt}")
    # frames decoded from another animation carry its loop/duration/background in .info, which
    # Pillow would reuse as defaults: keep only what we set on purpose (GIF transparency)
    for frame in seq:
        for key in ("loop", "duration", "background", "disposal", "exif", "comment", "extension"):
            frame.info.pop(key, None)
    first, rest = seq[0], list(seq[1:])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if hasattr(target, "write"):
            first.save(target, FORMATS[fmt].pil, append_images=rest, **kw)
        else:
            first.save(os.fspath(target), FORMATS[fmt].pil, append_images=rest, **kw)


def retime(durations: Sequence[int], factor: float, *, min_ms: int = 20, quantum: int = 1) -> list[tuple[int, int]]:
    """Speed an animation up/down by ``factor``: ``[(frame index, new duration ms)]``.

    Frames shorter than ``min_ms`` after the change are merged (dropped), so very fast results
    keep the right total time; durations are rounded to ``quantum`` (10 ms for GIF) without drift.
    """
    factor = float(factor)
    if factor <= 0 or math.isnan(factor):
        raise ValueError("factor must be > 0")
    quantum = max(1, int(quantum))
    min_ms = max(quantum, int(min_ms))
    starts: list[float] = []
    t = 0.0
    for d in durations:
        starts.append(t)
        t += max(0.0, float(d)) / factor
    end = max(quantum, int(t / quantum + 0.5) * quantum)
    keep: list[list[int]] = []
    last: int | None = None
    for i, s in enumerate(starts):
        rs = int(s / quantum + 0.5) * quantum
        if keep and rs >= end:
            break                      # would start at (or after) the end: nothing left to show
        if last is None or rs - last >= min_ms:
            keep.append([i, rs])
            last = rs
    out: list[tuple[int, int]] = []
    for k, (i, rs) in enumerate(keep):
        nxt = keep[k + 1][1] if k + 1 < len(keep) else max(end, rs + quantum)
        out.append((i, max(quantum, nxt - rs)))
    if len(out) > 1 and out[-1][1] < min_ms:
        idx, dur = out.pop()
        out[-1] = (out[-1][0], out[-1][1] + dur)
    if out and out[-1][1] < min_ms and len(out) == 1:
        out[-1] = (out[-1][0], min_ms)
    return out


# ========================================================================================== misc

def parse_tesseract_langs(output: str) -> set[str]:
    """Languages from ``tesseract --list-langs`` output."""
    langs: set[str] = set()
    for line in (output or "").splitlines():
        token = line.strip()
        if not token or " " in token or token.endswith(":") or token.lower().startswith("list of"):
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\-/]*", token):
            langs.add(token)
    return langs


def pick_ocr_languages(requested: str, available: Iterable[str]) -> tuple[list[str], list[str]]:
    """Requested ``"ita+eng"`` ∩ installed traineddata → ``(languages, missing)``.

    Falls back to ``eng`` (or any installed language) when none of the requested ones is installed.
    """
    avail = set(available or [])
    req = [p.strip() for p in str(requested or "").split("+") if p.strip()]
    if not req:
        req = ["eng"]
    ok = [r for r in req if r in avail]
    missing = [r for r in req if r not in avail]
    if not ok:
        if "eng" in avail:
            ok = ["eng"]
        else:
            others = sorted(a for a in avail if a not in ("osd", "equ", "snum"))
            ok = others[:1]
    return ok, missing


def parse_zbar_xml(text: str) -> list[tuple[str, str]]:
    """``zbarimg --xml`` output → ``[(symbology, data)]`` (base64 payloads decoded)."""
    import base64
    import xml.etree.ElementTree as ET

    out: list[tuple[str, str]] = []
    try:
        root = ET.fromstring(text or "")
    except ET.ParseError:
        for m in re.finditer(r"<symbol[^>]*type=['\"]([^'\"]+)['\"][^>]*>.*?<data[^>]*><!\[CDATA\[(.*?)\]\]></data>",
                             text or "", re.S):
            out.append((m.group(1), m.group(2)))
        return out
    for el in root.iter():
        if not el.tag.endswith("symbol"):
            continue
        kind = el.get("type", "")
        for child in el:
            if not child.tag.endswith("data"):
                continue
            data = child.text or ""
            if (child.get("format") or "").lower() == "base64":
                try:
                    raw = base64.b64decode(data)
                except ValueError:
                    raw = None
                if raw is not None:
                    try:
                        data = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        data = raw.decode("latin-1", "replace")
            out.append((kind, data))
    return out


_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.I)


def find_urls(text: str) -> list[str]:
    """http(s) / www links found in ``text`` (trailing punctuation removed)."""
    out = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?)]}'\"")
        if url.lower().startswith("www."):
            url = "https://" + url
        if url not in out:
            out.append(url)
    return out


def is_svgz(data: bytes) -> bool:
    """Gzip magic (``.svgz``)."""
    return data[:2] == b"\x1f\x8b"


def svg_text(path: str | os.PathLike, limit: int = 64 * 1024 * 1024) -> str:
    """Markup of an SVG/SVGZ file (decompressed, decoded)."""
    import gzip

    with open(os.fspath(path), "rb") as fh:
        raw = fh.read(limit + 1)
    if is_svgz(raw):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                raw = gz.read(limit + 1)
        except (OSError, EOFError) as exc:
            raise UserError("err.damaged", details=str(exc)) from None
    if len(raw) > limit:
        raise UserError("err.too_large")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    m = re.match(rb"<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)[\"']", raw)
    enc = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return raw.decode(enc)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")


_UNITS = {"px": 1.0, "": 1.0, "pt": 4.0 / 3.0, "pc": 16.0, "mm": 96.0 / 25.4, "cm": 96.0 / 2.54, "in": 96.0,
          "em": 16.0, "ex": 8.0}


def _svg_len(value: str | None) -> float | None:
    if not value:
        return None
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s*([a-z%]*)\s*", value)
    if not m or m.group(2) == "%":
        return None
    unit = m.group(2)
    if unit not in _UNITS:
        return None
    v = float(m.group(1)) * _UNITS[unit]
    return v if v > 0 else None


def svg_intrinsic_size(text: str) -> tuple[float, float] | None:
    """Intrinsic size (CSS px) from the root ``width``/``height`` or the ``viewBox``."""
    m = re.search(r"<svg\b([^>]*)>", text or "", re.S | re.I)
    if not m:
        return None
    attrs = dict((k.lower(), v) for k, v in re.findall(r"([\w:.-]+)\s*=\s*[\"']([^\"']*)[\"']", m.group(1)))
    w, h = _svg_len(attrs.get("width")), _svg_len(attrs.get("height"))
    vb = attrs.get("viewbox")
    vbw = vbh = None
    if vb:
        parts = re.split(r"[\s,]+", vb.strip())
        if len(parts) == 4:
            try:
                vbw, vbh = float(parts[2]), float(parts[3])
            except ValueError:
                vbw = vbh = None
    if w and h:
        return w, h
    if vbw and vbh and vbw > 0 and vbh > 0:
        if w:
            return w, w * vbh / vbw
        if h:
            return h * vbw / vbh, h
        return vbw, vbh
    return None


_PROTECTED_RE = re.compile(r"(<(text|style|script|pre|textArea)\b.*?</\2\s*>)", re.S | re.I)


def svg_minify(text: str) -> str:
    """Conservative pure-Python SVG clean-up: comments, ``<metadata>``, editor (Inkscape/Sodipodi)
    data — which leaks absolute paths — and indentation outside text/style/script elements."""
    pieces = _PROTECTED_RE.split(text)
    out: list[str] = []
    i = 0
    while i < len(pieces):
        chunk = pieces[i]
        if i % 3 == 1:
            out.append(chunk)        # protected element, verbatim
            i += 2                   # skip the tag-name group
            continue
        chunk = re.sub(r"<!--.*?-->", "", chunk, flags=re.S)
        chunk = re.sub(r"<metadata\b.*?</metadata\s*>|<metadata\b[^>]*/>", "", chunk, flags=re.S | re.I)
        chunk = re.sub(r"<sodipodi:namedview\b.*?(?:/>|</sodipodi:namedview\s*>)", "", chunk, flags=re.S)
        chunk = re.sub(r"<inkscape:[\w-]+\b.*?(?:/>|</inkscape:[\w-]+\s*>)", "", chunk, flags=re.S)
        chunk = re.sub(r"\s+(?:inkscape|sodipodi):[\w.-]+\s*=\s*(\"[^\"]*\"|'[^']*')", "", chunk)
        chunk = re.sub(r">\s+<", "><", chunk)
        chunk = re.sub(r"[ \t]*\n\s*", "\n", chunk)
        out.append(chunk)
        i += 1
    result = "".join(out)
    for prefix in ("inkscape", "sodipodi", "rdf", "cc", "dc"):
        decl = re.compile(r"\s+xmlns:" + prefix + r"\s*=\s*(\"[^\"]*\"|'[^']*')")
        if re.search(r"(?<![\w:])" + prefix + r":", decl.sub("", result)) is None:
            result = decl.sub("", result)
    return result.strip() + "\n"
