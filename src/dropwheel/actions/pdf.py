"""PDF actions: category ``pdf``, ``multi:pdf`` and the mixed "Combine into PDF" (pdf + images + svg).

Backends, in order of preference (each action lists the alternatives in ``requires`` so the menu
greys it out with the right install hint):

* PyMuPDF (``import pymupdf``; the legacy ``fitz`` name only when ``pymupdf`` is missing) for
  rendering, page assembly, redaction, stamps, metadata, text and images;
* pypdf / pikepdf / qpdf for page assembly, encryption and metadata when PyMuPDF is missing;
* Ghostscript for compression, grayscale and PDF/A-2b; poppler (``pdftoppm``, ``pdftotext``,
  ``pdfimages``, ``pdfinfo``, ``pdfdetach``) as CLI fallbacks; ocrmypdf (AUR) or tesseract for
  OCR; pdf2docx or LibreOffice for DOCX; pandoc / calibre for EPUB (a built-in EPUB writer
  otherwise); ``lp`` for printing; Xournal++ / Okular for annotating.

Encrypted PDFs: every action that reads page content accepts the optional ``password`` param.
The input is first decrypted into the job's temporary folder (external tools never see a
password), and the PDF outputs of single-file actions are protected again with the same password
and permissions (owner-only restrictions are kept).  A missing or wrong password raises a
readable :class:`UserError` (``pdf.err.password_needed`` / ``pdf.err.wrong_password``).
:func:`encryption_state` is the cheap probe used by the menu (and usable by previews).

Outputs never overwrite anything: conversions are ``<stem>.<ext>``, tools ``<stem> (<suffix>).pdf``,
several pages go into ``<stem> (pagine)/page-001.png``; inputs are never modified except through
``ctx.replace_original`` when the user enabled "Replace original".
"""

from __future__ import annotations

import base64
import html
import io
import logging
import math
import os
import re
import secrets
import shlex
import shutil
import sys
import threading
import unicodedata
import uuid
import warnings
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .. import config as cfgmod
from .. import i18n, tools, util
from ..detect import FileInfo
from ..errors import Cancelled, MissingTool, ToolError, UserError
from ..jobs import Context, Result, atomic_target
from ..registry import GroupSpec, P, Param, action, group, layout

log = logging.getLogger("dropwheel.actions.pdf")

__all__ = [
    "encryption_state", "parse_pages", "parse_ranges", "chunk_pages", "interleave_order", "piece_label",
    "format_page_label", "pdf_date", "format_pdf_date", "fix_toc_levels", "markdown_from_pages", "build_epub",
    "build_html", "normalize_metadata_key", "Perms", "blank_pdf_bytes", "paper_name", "gs_compress_args",
    "gs_image_quality_ps", "PasswordParam", "compress_default",
]

#: Backend names treated as missing (test hook: ``pdf._DISABLED.add("py:pymupdf")``).
_DISABLED: set[str] = set()

_LIB_LOCK = threading.Lock()
_LIBS: dict[str, Any] = {}

PT_PER_MM = 72.0 / 25.4
A4 = (595.276, 841.89)
LETTER = (612.0, 792.0)
MAX_RENDER_PIXELS = 120_000_000      # per page; larger renders are scaled down
SPLIT_FOLDER_THRESHOLD = 5           # more pieces than this go into a folder


# ======================================================================== optional libraries

def _import_lib(key: str) -> Any:
    """Import an optional library once (``None`` when missing or disabled)."""
    if key in _DISABLED:
        return None
    with _LIB_LOCK:
        if key in _LIBS:
            return _LIBS[key]
        mod: Any = None
        if key == "py:pymupdf":
            try:
                import pymupdf as mod  # type: ignore[no-redef]
            except ImportError:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        import fitz as mod  # type: ignore[no-redef]
                except ImportError:
                    mod = None
            if mod is not None:
                try:
                    mod.TOOLS.mupdf_display_errors(False)
                except Exception:
                    pass
        elif key == "py:pypdf":
            try:
                import pypdf as mod  # type: ignore[no-redef]

                logging.getLogger("pypdf").setLevel(logging.ERROR)
            except ImportError:
                mod = None
        elif key == "py:pikepdf":
            try:
                import pikepdf as mod  # type: ignore[no-redef]
            except ImportError:
                mod = None
        elif key == "py:PIL":
            try:
                from PIL import Image as mod  # type: ignore[no-redef]
            except ImportError:
                mod = None
        _LIBS[key] = mod
        return mod


def _mupdf() -> Any:
    return _import_lib("py:pymupdf")


def _need_mupdf() -> Any:
    mod = _mupdf()
    if mod is None:
        raise MissingTool("py:pymupdf")
    return mod


def _pypdf() -> Any:
    return _import_lib("py:pypdf")


def _pikepdf() -> Any:
    return _import_lib("py:pikepdf")


def _have(name: str) -> bool:
    """Availability of a backend (``py:`` modules are actually imported)."""
    if name in _DISABLED:
        return False
    if name in ("py:pymupdf", "py:pypdf", "py:pikepdf", "py:PIL"):
        return _import_lib(name) is not None
    return tools.have(name)


# ============================================================================ pure helpers

_END_WORDS = {"z", "end", "fine", "last", "ultima", "ultimo", "n"}
_ODD_WORDS = {"odd", "dispari"}
_EVEN_WORDS = {"even", "pari"}
_ALL_WORDS = {"all", "tutte", "tutto", "*"}


def _page_number(token: str, total: int) -> int:
    """``"5"`` → 5, ``"z"``/``"end"`` → total, ``"r2"`` → total-1 (1-based)."""
    tok = token.strip().lower()
    if tok in _END_WORDS:
        return total
    if tok.startswith("r") and tok[1:].isdigit():
        value = total - int(tok[1:]) + 1
    elif tok.isdigit():
        value = int(tok)
    else:
        raise ValueError(f"invalid page {token!r}")
    if value < 1 or value > total:
        raise ValueError(f"page {token} out of range 1-{total}")
    return value


def _normalize_spec(spec: str) -> str:
    text = (spec or "").strip().replace("–", "-").replace("—", "-").replace("..", "-")
    return re.sub(r"\s*-\s*", "-", text)


def _expand_token(tok: str, total: int) -> list[int]:
    low = tok.lower()
    if low in _ALL_WORDS:
        return list(range(1, total + 1))
    if low in _ODD_WORDS:
        return list(range(1, total + 1, 2))
    if low in _EVEN_WORDS:
        return list(range(2, total + 1, 2))
    if "-" in tok:
        a, _, b = tok.partition("-")
        start = _page_number(a, total) if a else 1
        if b:
            try:
                end = _page_number(b, total)
            except ValueError:
                if b.isdigit() and int(b) > total:
                    end = total          # "1-100" on a 5 page document means "1-5"
                else:
                    raise
        else:
            end = total
        step = 1 if end >= start else -1
        return list(range(start, end + step, step))
    return [_page_number(tok, total)]


def parse_pages(spec: str, total: int, *, empty_means_all: bool = False) -> list[int]:
    """Parse a page selection into 0-based indices, keeping the given order (duplicates kept).

    Syntax (qpdf-like, Italian words accepted): ``1-3,5,8-z`` · ``7-`` (to the end) · ``-3`` ·
    ``5-1`` (reversed) · ``r1`` (last page) · ``odd``/``dispari`` · ``even``/``pari`` ·
    ``x3`` or ``!3-4`` excludes pages.  Raises ``ValueError`` for invalid input.
    """
    if total <= 0:
        raise ValueError("document has no pages")
    text = _normalize_spec(spec)
    if not text:
        if empty_means_all:
            return list(range(total))
        raise ValueError("empty page selection")
    picked: list[int] = []
    excluded: set[int] = set()
    only_exclusions = True
    for tok in re.split(r"[,;\s]+", text):
        if not tok:
            continue
        if tok[0] in "x!X" and len(tok) > 1 and (tok[1].isdigit() or tok[1] in "rRzZ-"):
            excluded.update(_expand_token(tok[1:], total))
            continue
        only_exclusions = False
        picked.extend(_expand_token(tok, total))
    if only_exclusions:
        picked = list(range(1, total + 1))
    result = [p - 1 for p in picked if p not in excluded]
    if not result:
        raise ValueError("no pages selected")
    return result


def parse_ranges(spec: str, total: int) -> list[list[int]]:
    """``"1-3,5,7-"`` → ``[[0,1,2],[4],[6,…,total-1]]``: one group (output file) per range."""
    text = _normalize_spec(spec)
    groups: list[list[int]] = []
    for tok in re.split(r"[,;\s]+", text):
        if tok:
            groups.append(parse_pages(tok, total))
    if not groups:
        raise ValueError("empty page selection")
    return groups


def chunk_pages(total: int, size: int) -> list[list[int]]:
    """Pages ``0..total-1`` in chunks of ``size``."""
    size = max(1, int(size))
    return [list(range(i, min(i + size, total))) for i in range(0, total, size)]


def interleave_order(fronts: int, backs: int, reverse_backs: bool = True) -> list[tuple[int, int]]:
    """Collate duplex scans: ``(file, page)`` pairs F1 B1 F2 B2…; backs scanned last-to-first by default."""
    out: list[tuple[int, int]] = []
    for i in range(max(fronts, backs)):
        if i < fronts:
            out.append((0, i))
        if i < backs:
            out.append((1, backs - 1 - i if reverse_backs else i))
    return out


def _runs(indices: Sequence[int]) -> list[tuple[int, int]]:
    """Compress indices into runs ``(first, last)`` of consecutive ascending or descending values."""
    runs: list[tuple[int, int]] = []
    i = 0
    n = len(indices)
    while i < n:
        start = indices[i]
        j = i
        step = 0
        if i + 1 < n and abs(indices[i + 1] - start) == 1:
            step = indices[i + 1] - start
            while j + 1 < n and indices[j + 1] - indices[j] == step:
                j += 1
        runs.append((start, indices[j]))
        i = j + 1
    return runs


def piece_label(pages: Sequence[int], total: int) -> str:
    """File-name label of a split piece: ``p003`` or ``p001-004`` (1-based, zero padded)."""
    width = max(3, len(str(total)))
    parts = []
    for a, b in _runs(list(pages)):
        parts.append(f"{a + 1:0{width}d}" if a == b else f"{a + 1:0{width}d}-{b + 1:0{width}d}")
    return "p" + "+".join(parts)


class _SafeFmt(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def format_page_label(fmt: str, n: int, total: int) -> str:
    """``"{n}/{total}"`` → ``"3/12"``; unknown placeholders are kept literally."""
    try:
        return (fmt or "{n}").format_map(_SafeFmt(n=n, total=total, N=total, page=n, pages=total))
    except (ValueError, IndexError, AttributeError, TypeError):
        return (fmt or "").replace("{n}", str(n)).replace("{total}", str(total))


_PDF_DATE_RE = re.compile(
    r"^D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*(Z|[+-]\d{2}'?(?:\d{2}'?)?)?")


def format_pdf_date(value: str) -> str:
    """``"D:20261029203902+02'00'"`` → ``"2026-10-29 20:39:02 +02:00"`` (raw text when unparsable)."""
    text = (value or "").strip()
    m = _PDF_DATE_RE.match(text)
    if not m or not text:
        return text
    y, mo, d, h, mi, s, tz = m.groups()
    out = f"{y}-{mo or '01'}-{d or '01'}"
    if h:
        out += f" {h}:{mi or '00'}:{s or '00'}"
    if tz:
        if tz == "Z":
            out += " UTC"
        else:
            digits = tz.replace("'", "")
            out += f" {digits[:3]}:{digits[3:5] or '00'}"
    return out


def pdf_date(value: Any) -> str:
    """Normalise a date to PDF syntax ``D:YYYYMMDDHHmmSS+hh'mm'``.

    Accepts PDF dates, ISO 8601 (``2026-10-29``, ``2026-10-29T14:03:00+01:00``), exiftool's form
    (``2026:10:29 14:03:00+01:00``), ``29/10/2026``, what :func:`format_pdf_date` shows
    (``2026-10-29 14:03:00 +01:00``, ``2026-10-29 UTC``) and :class:`datetime`.  Raises
    ``ValueError`` when unparsable.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty date")
        if text.startswith("D:") or re.fullmatch(r"\d{8,14}([-+Z].*)?", text):
            m = _PDF_DATE_RE.match(text)
            if m and m.group(1):
                return text if text.startswith("D:") else "D:" + text
        m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
        if m:
            d, mo, y, h, mi, s = m.groups()
            dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
        else:
            iso = re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", text)
            iso = re.sub(r"\s*(?:UTC|Z)$", "+00:00", iso)
            iso = re.sub(r"\s+([-+]\d{2}(?::?\d{2})?)$", r"\1", iso)
            iso = re.sub(r"^(\d{4}-\d{2}-\d{2})(?=[-+])", r"\1T00:00:00", iso)      # else "+02:00" reads as a time
            iso = iso.replace(" ", "T", 1) if re.match(r"\d{4}-\d{2}-\d{2} \d", iso) else iso
            dt = datetime.fromisoformat(iso)
    stamp = dt.strftime("D:%Y%m%d%H%M%S")
    off = dt.utcoffset()
    if off is None:
        return stamp
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    if minutes == 0:
        return stamp + "Z"
    return f"{stamp}{sign}{minutes // 60:02d}'{minutes % 60:02d}'"


def fix_toc_levels(toc: Iterable[Sequence]) -> list[list]:
    """Make a table of contents acceptable for ``set_toc``: first level 1, each level ≤ previous + 1."""
    out: list[list] = []
    prev = 0
    for entry in toc:
        if len(entry) < 3:
            continue
        level = max(1, min(int(entry[0]), prev + 1))
        out.append([level, str(entry[1]), *entry[2:]])
        prev = level
    return out


_META_ALIASES = {
    "title": "title", "dctitle": "title", "author": "author", "creator": "creator", "dccreator": "author",
    "subject": "subject", "description": "subject", "dcdescription": "subject", "keywords": "keywords",
    "pdfkeywords": "keywords", "creatortool": "creator", "xmpcreatortool": "creator", "application": "creator",
    "producer": "producer", "pdfproducer": "producer", "creationdate": "creationDate", "createdate": "creationDate",
    "created": "creationDate", "xmpcreatedate": "creationDate", "moddate": "modDate", "modifydate": "modDate",
    "modified": "modDate", "modificationdate": "modDate", "xmpmodifydate": "modDate", "trapped": "trapped",
}
META_KEYS = ("title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate")


def normalize_metadata_key(key: str) -> str | None:
    """Map editor/exiftool style keys (``"PDF:Title"``, ``"XMP-xmp:CreateDate"``) to PyMuPDF keys."""
    raw = str(key or "")
    if re.search(r"(?i)(^|[^a-z])dc:creator$", raw):
        return "author"                    # XMP dc:creator is the author, not the creating tool
    if ":" in raw:
        raw = raw.rsplit(":", 1)[-1]
    flat = re.sub(r"[^a-z]", "", raw.lower())
    if flat in _META_ALIASES:
        return _META_ALIASES[flat]
    for prefix in ("pdf", "xmp", "dc", "info"):
        if flat.startswith(prefix) and flat[len(prefix):] in _META_ALIASES:
            return _META_ALIASES[flat[len(prefix):]]
    return None


@dataclass
class Perms:
    """PDF permission flags (PDF 32000 table 22)."""

    print: bool = True
    print_hq: bool = True
    modify: bool = True
    copy: bool = True
    annotate: bool = True
    form: bool = True
    accessibility: bool = True
    assemble: bool = True

    _BITS = (("print", 4), ("modify", 8), ("copy", 16), ("annotate", 32), ("form", 256), ("accessibility", 512),
             ("assemble", 1024), ("print_hq", 2048))

    @classmethod
    def from_int(cls, value: int | None) -> "Perms":
        """Decode a /P value (``None`` → everything allowed)."""
        if value is None:
            return cls()
        v = int(value) & 0xFFFFFFFF
        return cls(**{name: bool(v & bit) for name, bit in cls._BITS})

    def bits(self) -> int:
        """Positive sum of the allowed bits (PyMuPDF ``permissions=``)."""
        return sum(bit for name, bit in self._BITS if getattr(self, name))

    def to_int(self) -> int:
        """Signed 32-bit /P value with the reserved bits set."""
        v = (self.bits() | 0xFFFFF0C0) & 0xFFFFFFFF
        return v - (1 << 32) if v >= (1 << 31) else v

    @property
    def all_allowed(self) -> bool:
        return all(getattr(self, name) for name, _ in self._BITS)

    def qpdf_args(self) -> list[str]:
        """Restriction flags for ``qpdf --encrypt … --`` (256-bit)."""
        yn = lambda flag: "y" if flag else "n"  # noqa: E731
        printing = "full" if self.print and self.print_hq else ("low" if self.print else "none")
        return [f"--print={printing}", f"--modify-other={yn(self.modify)}", f"--extract={yn(self.copy)}",
                f"--annotate={yn(self.annotate)}", f"--form={yn(self.form)}",
                f"--accessibility={yn(self.accessibility)}", f"--assemble={yn(self.assemble)}"]


def blank_pdf_bytes(width: float, height: float, pages: int = 1) -> bytes:
    """A minimal valid PDF with ``pages`` blank pages of ``width``×``height`` points."""
    w = f"{float(width):.3f}".rstrip("0").rstrip(".")
    h = f"{float(height):.3f}".rstrip("0").rstrip(".")
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>")
    for _ in range(pages):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] /Resources << >> >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode("ascii"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("ascii"))
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return out.getvalue()


_PAPERS = (("A0", 841, 1189), ("A1", 594, 841), ("A2", 420, 594), ("A3", 297, 420), ("A4", 210, 297),
           ("A5", 148, 210), ("A6", 105, 148), ("B4", 250, 353), ("B5", 176, 250), ("Letter", 215.9, 279.4),
           ("Legal", 215.9, 355.6), ("Tabloid", 279.4, 431.8))


def paper_name(width_pt: float, height_pt: float) -> str:
    """``"A4"`` / ``"Letter"`` … for a page size in points (``""`` when no standard size matches)."""
    w = min(width_pt, height_pt) / PT_PER_MM
    h = max(width_pt, height_pt) / PT_PER_MM
    for name, pw, ph in _PAPERS:
        if abs(w - pw) <= 2.5 and abs(h - ph) <= 2.5:
            return name
    return ""


def gs_image_quality_ps(quality: int) -> str:
    """PostScript setting the JPEG quality of pdfwrite (image dictionaries cannot be given as ``-d``).

    ``QFactor`` × 100 is the scale of libjpeg's quantisation tables, so ``quality`` follows libjpeg's
    own curve (and means the same as in the PyMuPDF fallback): 70 → 0.6, 30 → 1.667.  The parameters
    are locked because the ``setpagedevice`` run for each page of a PDF would reset them otherwise.
    """
    quality = max(1, min(100, int(quality)))
    scale = 5000 / quality if quality < 50 else 200 - 2 * quality
    samples = "[1 1 1 1]" if quality >= 90 else "[2 1 1 2]"
    image = f"<< /QFactor {max(scale, 1) / 100:.3f} /Blend 1 /HSamples {samples} /VSamples {samples} >>"
    return f"<< /LockDistillerParams true /ColorImageDict {image} /GrayImageDict {image} >> setdistillerparams"


def gs_compress_args(preset: str, dpi: int | None = None, quality: int | None = None) -> list[str]:
    """Ghostscript pdfwrite options for a compression preset (``light``/``balanced``/``strong``/``custom``).

    ``quality`` (``custom`` only) makes every colour and gray image a JPEG, those already in JPEG
    included, at the quality set by :func:`gs_image_quality_ps`.
    """
    settings = {"light": "/printer", "balanced": "/ebook", "strong": "/screen", "custom": "/ebook"}.get(preset, "/ebook")
    args = ["-dCompatibilityLevel=1.7", f"-dPDFSETTINGS={settings}", "-dDetectDuplicateImages=true",
            "-dCompressFonts=true", "-dSubsetFonts=true"]
    if preset == "light":
        # /printer quality settings but images keep their resolution
        args += ["-dDownsampleColorImages=false", "-dDownsampleGrayImages=false", "-dDownsampleMonoImages=false"]
    if preset == "custom" and dpi:
        dpi = max(36, min(1200, int(dpi)))
        mono = min(1200, dpi * 2)
        args += ["-dDownsampleColorImages=true", "-dDownsampleGrayImages=true", "-dDownsampleMonoImages=true",
                 "-dColorImageDownsampleType=/Bicubic", "-dGrayImageDownsampleType=/Bicubic",
                 f"-dColorImageResolution={dpi}", f"-dGrayImageResolution={dpi}", f"-dMonoImageResolution={mono}",
                 "-dColorImageDownsampleThreshold=1.1", "-dGrayImageDownsampleThreshold=1.1"]
    if preset == "custom" and quality is not None:
        args += ["-dAutoFilterColorImages=false", "-dAutoFilterGrayImages=false", "-dColorImageFilter=/DCTEncode",
                 "-dGrayImageFilter=/DCTEncode", "-dPassThroughJPEGImages=false"]
    return args


def _rgb(value: Any, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """``"#rrggbb"`` / ``"rgb"`` → floats 0..1."""
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) >= 6 and re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", text):
        return (int(text[0:2], 16) / 255.0, int(text[2:4], 16) / 255.0, int(text[4:6], 16) / 255.0)
    return default


# -------------------------------------------------------------------- Markdown (structured fallback)

_BULLET_RE = re.compile(r"^\s*([•●○◦▪▫■□‣⁃∙·*–—-])\s+")
_NUMBERED_RE = re.compile(r"^\s*(\(?\d{1,3}[.)]|[a-zA-Z][.)])\s+")


def _span_runs(spans: list[dict]) -> str:
    """Inline Markdown for a line: bold/italic spans are wrapped (only when not the whole line)."""
    runs: list[list] = []
    for sp in spans:
        text = sp.get("text", "")
        if not text:
            continue
        flags = int(sp.get("flags", 0))
        style = ("b" if flags & 16 else "") + ("i" if flags & 2 else "")
        if not text.strip():
            style = runs[-1][0] if runs else ""
        if runs and runs[-1][0] == style:
            runs[-1][1] += text
        else:
            runs.append([style, text])
    styled = {s for s, t in runs if t.strip()}
    if len(styled) == 1:
        return "".join(t for _, t in runs)        # uniform line: block level decides
    out = []
    for style, text in runs:
        core = text.strip()
        if not style or not core:
            out.append(text)
            continue
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        mark = "***" if style == "bi" else "**" if style == "b" else "*"
        out.append(f"{lead}{mark}{core}{mark}{trail}")
    return "".join(out)


def _line_info(line: dict) -> dict | None:
    spans = [s for s in line.get("spans", []) if s.get("text")]
    text = "".join(s["text"] for s in spans)
    if not text.strip():
        return None
    sizes: Counter = Counter()
    bold = True
    mono = True
    italic = True
    for s in spans:
        t = s["text"].strip()
        if not t:
            continue
        sizes[round(float(s.get("size", 0)) * 2) / 2] += len(t)
        flags = int(s.get("flags", 0))
        font = str(s.get("font", "")).lower()
        bold = bold and bool(flags & 16 or "bold" in font or "black" in font or "heavy" in font)
        italic = italic and bool(flags & 2 or "italic" in font or "oblique" in font)
        mono = mono and bool(flags & 8 or "mono" in font or "courier" in font or "consol" in font)
    return {"text": text.strip(), "raw": text.rstrip(), "size": sizes.most_common(1)[0][0] if sizes else 0.0,
            "bold": bold, "italic": italic, "mono": mono, "inline": _span_runs(spans).strip(), "chars": len(text)}


def _escape_md_line(text: str) -> str:
    if re.match(r"^#{1,6}\s", text) or re.match(r"^(\d+)\.\s", text) and False:
        return "\\" + text
    if text.startswith(">"):
        return "\\" + text
    return text


def _render_md_block(block: dict, body: float, level_of: dict[float, int]) -> str:
    lines = [li for li in (_line_info(ln) for ln in block.get("lines", [])) if li]
    if not lines:
        return ""
    chars: Counter = Counter()
    for li in lines:
        chars[li["size"]] += li["chars"]
    dominant = chars.most_common(1)[0][0]
    joined = " ".join(li["text"] for li in lines)
    if dominant in level_of and len(joined) <= 300:
        return "#" * level_of[dominant] + " " + re.sub(r"\s+", " ", joined).strip()
    if all(li["mono"] for li in lines):
        return "```\n" + "\n".join(li["raw"] for li in lines) + "\n```"
    if len(lines) == 1 and lines[0]["bold"] and len(joined) <= 120 and not _BULLET_RE.match(joined):
        return f"**{joined}**"
    items: list[str] = []
    current = ""
    is_list = False
    for li in lines:
        piece = li["inline"] if not (li["bold"] or li["italic"]) else li["text"]
        if li["bold"] and not li["italic"] and len(lines) > 1:
            piece = f"**{li['text']}**"
        bullet = _BULLET_RE.match(li["text"])
        numbered = _NUMBERED_RE.match(li["text"])
        if bullet or (numbered and li is not lines[0] or numbered and len(lines) > 1):
            if current:
                items.append(current)
            is_list = True
            if bullet:
                current = "- " + _BULLET_RE.sub("", piece, count=1).strip()
            else:
                current = piece.strip()
            continue
        if current.endswith("-") and not current.endswith(" -") and piece[:1].islower():
            current = current[:-1] + piece
        elif current:
            current += " " + piece
        else:
            current = piece
    if current:
        items.append(current)
    items = [_escape_md_line(re.sub(r"[ \t]+", " ", it).strip()) for it in items]
    return ("\n" if is_list else "\n\n").join(it for it in items if it)


def markdown_from_pages(pages: list[dict]) -> str:
    """Structured Markdown from ``page.get_text("dict")`` data (fallback when pymupdf4llm is missing).

    ``pages``: ``[{"blocks": [...], "tables": [{"bbox": (x0,y0,x1,y1), "md": "…"}]}]``.  The body
    font size is the most common size by character count; larger sizes become headings (#…###),
    monospace blocks code fences, bullet/numbered lines list items, bold/italic spans emphasis.
    """
    sizes: Counter = Counter()
    for pg in pages:
        for b in pg.get("blocks", []):
            if b.get("type", 0) != 0:
                continue
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    t = (sp.get("text") or "").strip()
                    if t:
                        sizes[round(float(sp.get("size", 0)) * 2) / 2] += len(t)
    if not sizes and not any(pg.get("tables") for pg in pages):
        return ""
    body = sizes.most_common(1)[0][0] if sizes else 0.0
    heads = sorted({s for s in sizes if s >= body * 1.15 and s - body >= 1.0}, reverse=True)
    level_of = {s: min(i + 1, 6) for i, s in enumerate(heads[:6])}
    chunks: list[str] = []
    for pg in pages:
        tables = [t for t in (pg.get("tables") or []) if t.get("md")]
        items: list[tuple[float, float, str]] = []
        for t in tables:
            x0, y0, _x1, _y1 = t["bbox"]
            items.append((y0, x0, t["md"].strip()))
        for b in pg.get("blocks", []):
            if b.get("type", 0) != 0:
                continue
            x0, y0, x1, y1 = b.get("bbox", (0, 0, 0, 0))
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if any(t["bbox"][0] <= cx <= t["bbox"][2] and t["bbox"][1] <= cy <= t["bbox"][3] for t in tables):
                continue
            text = _render_md_block(b, body, level_of)
            if text:
                items.append((y0, x0, text))
        if tables:
            items.sort(key=lambda it: (round(it[0], 1), it[1]))
        page_text = "\n\n".join(it[2] for it in items).strip()
        if page_text:
            chunks.append(page_text)
    md = "\n\n".join(chunks)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n" if md.strip() else ""


# ------------------------------------------------------------------------ HTML / EPUB writers

_HTML_CSS = ("body{font-family:sans-serif;line-height:1.5;max-width:48em;margin:2em auto;padding:0 1em}"
             "img{max-width:100%;height:auto}div[id^=page]{margin-bottom:2em}")
_XML_ENTITIES = {"lt", "gt", "amp", "quot", "apos"}


def _xml_entities(text: str) -> str:
    """Turn HTML named entities into numeric references (XHTML/XML safe)."""

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in _XML_ENTITIES:
            return m.group(0)
        value = html.unescape(m.group(0))
        if value != m.group(0) and value:
            return "".join(f"&#{ord(c)};" for c in value)
        return "&amp;" + name + ";"

    text = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", repl, text)
    return re.sub(r"&(?!#?[A-Za-z0-9]+;)", "&amp;", text)


def _well_formed(fragment: str) -> bool:
    try:
        ET.fromstring(f"<root>{fragment}</root>")
        return True
    except ET.ParseError:
        return False


def _text_to_xhtml(text: str, anchor: str = "") -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    body = "".join(f"<p>{html.escape(re.sub(r'\s+', ' ', p), quote=False)}</p>" for p in paras)
    return f'<div id="{anchor}">{body}</div>' if anchor else body


_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/gif": "gif", "image/webp": "webp",
             "image/svg+xml": "svg", "image/bmp": "bmp", "image/tiff": "tif"}
_EXT_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
             "svg": "image/svg+xml", "bmp": "image/bmp", "tif": "image/tiff"}


def _extract_data_uris(fragment: str, prefix: str, images: dict[str, bytes]) -> str:
    """Move ``data:`` images of an XHTML fragment into ``images`` (EPUB needs real files)."""
    counter = [0]

    def repl(m: re.Match) -> str:
        mime = m.group(1).lower()
        try:
            data = base64.b64decode(re.sub(r"\s+", "", m.group(2)))
        except (ValueError, TypeError):
            return 'src=""'
        counter[0] += 1
        ext = _MIME_EXT.get(mime, "png")
        name = f"images/{prefix}-{counter[0]:02d}.{ext}"
        images[name] = data
        return f'src="{name}"'

    return re.sub(r'src="data:([^;",]+);base64,([^"]*)"', repl, fragment)


def _ensure_img_alt(fragment: str) -> str:
    return re.sub(r"<img(?![^>]*\balt=)", '<img alt=""', fragment)


def build_html(title: str, lang: str, body: str) -> str:
    """A standalone HTML5 document around ``body``."""
    return ("<!DOCTYPE html>\n"
            f'<html lang="{html.escape(lang or "en")}">\n<head>\n<meta charset="utf-8"/>\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
            f"<title>{html.escape(title or '')}</title>\n<style>{_HTML_CSS}</style>\n</head>\n<body>\n"
            f"{body}\n</body>\n</html>\n")


def _xhtml_doc(title: str, lang: str, body: str, css: bool = True) -> str:
    link = '<link rel="stylesheet" type="text/css" href="style.css"/>' if css else ""
    t = html.escape(title or "", quote=False)
    lg = html.escape(lang or "en")
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lg}" '
            f'xml:lang="{lg}">\n<head>\n<meta charset="utf-8"/>\n<title>{t}</title>\n{link}\n</head>\n<body>\n'
            f"{body}\n</body>\n</html>\n")


def build_epub(dst: str | os.PathLike, *, title: str, author: str = "", lang: str = "en",
               chapters: Sequence[tuple[str, str]], images: dict[str, bytes] | None = None,
               identifier: str | None = None) -> None:
    """Write a minimal valid EPUB 3 (with an EPUB 2 NCX for old readers).

    ``chapters``: ``[(title, xhtml_body_fragment)]``; ``images``: ``{"images/x.png": bytes}``
    referenced by the fragments with relative paths.
    """
    images = dict(images or {})
    ident = identifier or f"urn:uuid:{uuid.uuid4()}"
    title = title or "Untitled"
    lang = lang or "en"
    esc = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    chapters = list(chapters) or [(title, "<p></p>")]
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    nav_items = []
    ncx_points = []
    for i, (ch_title, _body) in enumerate(chapters, start=1):
        manifest.append(f'<item id="ch{i:03d}" href="ch{i:03d}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{i:03d}"/>')
        nav_items.append(f'<li><a href="ch{i:03d}.xhtml">{esc(ch_title or title)}</a></li>')
        ncx_points.append(f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{esc(ch_title or title)}</text>'
                          f'</navLabel><content src="ch{i:03d}.xhtml"/></navPoint>')
    for k, name in enumerate(sorted(images), start=1):
        ext = name.rsplit(".", 1)[-1].lower()
        manifest.append(f'<item id="img{k:04d}" href="{esc(name)}" media-type="{_EXT_MIME.get(ext, "image/png")}"/>')
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           f'<dc:identifier id="bookid">{esc(ident)}</dc:identifier>\n<dc:title>{esc(title)}</dc:title>\n'
           f"<dc:language>{esc(lang)}</dc:language>\n"
           + (f"<dc:creator>{esc(author)}</dc:creator>\n" if author else "")
           + f'<meta property="dcterms:modified">{modified}</meta>\n</metadata>\n'
           f'<manifest>\n{chr(10).join(manifest)}\n</manifest>\n<spine toc="ncx">\n{chr(10).join(spine)}\n</spine>\n'
           "</package>\n")
    nav_body = (f'<nav epub:type="toc" id="toc"><h1>{esc(title)}</h1><ol>{"".join(nav_items)}</ol></nav>')
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{esc(ident)}"/></head><docTitle><text>{esc(title)}</text></docTitle>'
           f'<navMap>{"".join(ncx_points)}</navMap></ncx>\n')
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 "</rootfiles></container>\n")
    with zipfile.ZipFile(dst, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")      # stored, first entry
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", _xhtml_doc(title, lang, nav_body, css=False), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/style.css", _HTML_CSS, compress_type=zipfile.ZIP_DEFLATED)
        for i, (ch_title, body) in enumerate(chapters, start=1):
            zf.writestr(f"OEBPS/ch{i:03d}.xhtml", _xhtml_doc(ch_title or title, lang, body),
                        compress_type=zipfile.ZIP_DEFLATED)
        for name, data in images.items():
            zf.writestr(f"OEBPS/{name}", data, compress_type=zipfile.ZIP_STORED)


# ============================================================================ encryption probe

_ENC_CACHE: dict[tuple, dict] = {}
_ENC_LOCK = threading.Lock()


def _byte_scan_encrypted(path: str) -> bool:
    """Heuristic: ``/Encrypt`` in the trailer area (start and end of the file)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(262144)
            if size > 262144:
                fh.seek(max(262144, size - 262144))
                tail = fh.read()
            else:
                tail = b""
    except OSError:
        return False
    return b"/Encrypt" in head or b"/Encrypt" in tail


def encryption_state(path: str | os.PathLike) -> dict:
    """``{"encrypted": bool, "needs_password": bool}`` for a PDF (cached by path, mtime and size).

    ``needs_password`` = a user (open) password is required; ``encrypted`` also covers owner-only
    restrictions.  Never raises.
    """
    p = os.fspath(path)
    try:
        st = os.stat(p)
        key = (p, st.st_mtime_ns, st.st_size)
    except OSError:
        return {"encrypted": False, "needs_password": False}
    with _ENC_LOCK:
        hit = _ENC_CACHE.get(key)
    if hit is not None:
        return dict(hit)
    state = {"encrypted": False, "needs_password": False}
    m = _mupdf()
    done = False
    if m is not None:
        try:
            with m.open(p) as doc:
                if doc.is_pdf:
                    if doc.needs_pass:
                        state = {"encrypted": True, "needs_password": True}
                    else:
                        state = {"encrypted": bool((doc.metadata or {}).get("encryption")), "needs_password": False}
                done = True
        except Exception:
            done = False
    if not done:
        pp = _pypdf()
        if pp is not None:
            try:
                reader = pp.PdfReader(p, strict=False)
                if reader.is_encrypted:
                    needs = True
                    try:
                        needs = not bool(reader.decrypt(""))
                    except Exception:
                        needs = True
                    state = {"encrypted": True, "needs_password": needs}
                done = True
            except Exception:
                done = False
    if not done:
        enc = _byte_scan_encrypted(p)
        state = {"encrypted": enc, "needs_password": enc}
    with _ENC_LOCK:
        if len(_ENC_CACHE) > 512:
            _ENC_CACHE.clear()
        _ENC_CACHE[key] = dict(state)
    return state


def _is_encrypted(f: FileInfo) -> bool:
    return bool(f.path) and encryption_state(f.path)["encrypted"]


def _when_unencrypted(files: list[FileInfo]) -> bool:
    return any(not _is_encrypted(f) for f in files)


def _when_encrypted(files: list[FileInfo]) -> bool:
    return any(_is_encrypted(f) for f in files)


# ===================================================================== prepared (decrypted) input

@dataclass
class _Prep:
    """A readable, unencrypted copy (or the original) of an input PDF."""

    path: Path
    info: FileInfo
    encrypted: bool = False
    user_pw: str = ""
    owner_pw: str = ""
    perms: Perms = field(default_factory=Perms)
    method: str = ""
    pages: int = 0


def _password(ctx: Context) -> str:
    return str(ctx.params.get("password") or "").replace("\r", "").replace("\n", "")


def _tmp(ctx: Context, name: str) -> Path:
    """A unique path in the job temp dir (never containing user text)."""
    return ctx.tmpdir() / f"{secrets.token_hex(4)}-{name}"


def _move(src: Path, dst: Path) -> None:
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copyfile(src, dst)
        try:
            os.unlink(src)
        except OSError:
            pass


def _raw_perms_mupdf(doc: Any) -> int | None:
    try:
        kind, value = doc.xref_get_key(-1, "Encrypt/P")
        if kind == "int":
            return int(value)
    except Exception:
        pass
    try:
        perms = int(doc.permissions)
        return None if perms == -4 else perms
    except Exception:
        return None


def _prep_mupdf(ctx: Context, f: FileInfo, pw: str, decrypt: bool) -> _Prep:
    m = _need_mupdf()
    try:
        doc = m.open(f.path)
    except Exception as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    try:
        if not doc.is_pdf:
            raise UserError("pdf.err.not_pdf", name=f.name)
        owner = False
        needs = bool(doc.needs_pass)
        if needs:
            if not pw:
                raise UserError("pdf.err.password_needed", name=f.name)
            rc = doc.authenticate(pw)
            if not rc:
                raise UserError("pdf.err.wrong_password", name=f.name)
            owner = bool(rc & 4)
        elif pw:
            try:
                owner = bool(doc.authenticate(pw) & 4)
            except Exception:
                owner = False
        method = str((doc.metadata or {}).get("encryption") or "")
        encrypted = needs or bool(method)
        prep = _Prep(path=Path(f.path), info=f, encrypted=encrypted, user_pw=pw if needs else "",
                     owner_pw=pw if owner else "", perms=Perms.from_int(_raw_perms_mupdf(doc)) if encrypted else Perms(),
                     method=method, pages=int(doc.page_count))
        if encrypted and decrypt:
            ctx.progress(None, ctx.t("pdf.prog.decrypt"))
            dst = _tmp(ctx, "decrypted.pdf")
            doc.save(str(dst), encryption=m.PDF_ENCRYPT_NONE)
            prep.path = dst
        return prep
    finally:
        doc.close()


def _prep_pikepdf(ctx: Context, f: FileInfo, pw: str, decrypt: bool) -> _Prep:
    pk = _pikepdf()
    needs = False
    try:
        pdf = pk.open(f.path)
    except pk.PasswordError:
        needs = True
        if not pw:
            raise UserError("pdf.err.password_needed", name=f.name) from None
        try:
            pdf = pk.open(f.path, password=pw)
        except pk.PasswordError:
            raise UserError("pdf.err.wrong_password", name=f.name) from None
        except pk.PdfError as exc:
            raise UserError("err.damaged", details=str(exc)) from None
    except pk.PdfError as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    try:
        encrypted = bool(pdf.is_encrypted)
        raw = None
        try:
            raw = int(pdf.encryption.P)  # type: ignore[union-attr]
        except Exception:
            try:
                raw = int(pdf.trailer.Encrypt.P)
            except Exception:
                raw = None
        owner = bool(getattr(pdf, "owner_password_matched", False)) and bool(pw)
        prep = _Prep(path=Path(f.path), info=f, encrypted=encrypted, user_pw=pw if needs else "",
                     owner_pw=pw if owner else "", perms=Perms.from_int(raw) if encrypted else Perms(),
                     method="AES/RC4" if encrypted else "", pages=len(pdf.pages))
        if encrypted and decrypt:
            ctx.progress(None, ctx.t("pdf.prog.decrypt"))
            dst = _tmp(ctx, "decrypted.pdf")
            pdf.save(str(dst))
            prep.path = dst
        return prep
    finally:
        pdf.close()


def _qpdf_pwfile(ctx: Context, pw: str) -> Path:
    path = _tmp(ctx, "pw.txt")
    path.write_text(pw, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _prep_qpdf(ctx: Context, f: FileInfo, pw: str, decrypt: bool) -> _Prep:
    res = ctx.run(["qpdf", "--is-encrypted", f.path], ok_codes=(0, 2), timeout=120)
    if res.returncode == 2:
        pages = 0
        try:
            pages = int(ctx.run(["qpdf", "--show-npages", f.path], ok_codes=(0, 3), timeout=120).stdout.strip() or 0)
        except (ToolError, ValueError):
            pages = 0
        return _Prep(path=Path(f.path), info=f, pages=pages)
    needs = ctx.run(["qpdf", "--requires-password", f.path], ok_codes=(0, 2, 3), timeout=120).returncode == 0
    if needs and not pw:
        raise UserError("pdf.err.password_needed", name=f.name)
    pwargs: list[str] = []
    if pw:
        pwargs = [f"--password-file={_qpdf_pwfile(ctx, pw)}"]
    raw = None
    try:
        info = ctx.run(["qpdf", *pwargs, "--show-encryption", f.path], ok_codes=(0, 3), timeout=120).stdout
        m = re.search(r"^P\s*=\s*(-?\d+)", info, re.M)
        raw = int(m.group(1)) if m else None
    except ToolError as exc:
        if needs and "password" in (exc.details or "").lower():
            raise UserError("pdf.err.wrong_password", name=f.name) from None
        raise
    prep = _Prep(path=Path(f.path), info=f, encrypted=True, user_pw=pw if needs else "", owner_pw="",
                 perms=Perms.from_int(raw), method="qpdf")
    if decrypt:
        ctx.progress(None, ctx.t("pdf.prog.decrypt"))
        dst = _tmp(ctx, "decrypted.pdf")
        try:
            ctx.run(["qpdf", *pwargs, "--decrypt", f.path, str(dst)], ok_codes=(0, 3))
        except ToolError as exc:
            if "password" in (exc.details or "").lower():
                raise UserError("pdf.err.wrong_password", name=f.name) from None
            raise
        prep.path = dst
    return prep


def _pypdf_crypto_missing(exc: BaseException) -> None:
    """pypdf needs the ``cryptography`` module for AES: report it as a missing dependency."""
    if type(exc).__name__ == "DependencyError":
        raise MissingTool("py:cryptography", details=str(exc)) from None


def _prep_pypdf(ctx: Context, f: FileInfo, pw: str, decrypt: bool) -> _Prep:
    pp = _pypdf()
    try:
        reader = pp.PdfReader(f.path, strict=False)
    except Exception as exc:
        _pypdf_crypto_missing(exc)
        raise UserError("err.damaged", details=str(exc)) from None
    if not reader.is_encrypted:
        return _Prep(path=Path(f.path), info=f, pages=len(reader.pages))
    needs = False
    try:
        ok = bool(reader.decrypt(""))
    except Exception as exc:
        _pypdf_crypto_missing(exc)
        ok = False
    if not ok:
        needs = True
        if not pw:
            raise UserError("pdf.err.password_needed", name=f.name)
        try:
            ok = bool(reader.decrypt(pw))
        except Exception as exc:
            _pypdf_crypto_missing(exc)
            raise UserError("pdf.err.wrong_password", details=str(exc), name=f.name) from None
        if not ok:
            raise UserError("pdf.err.wrong_password", name=f.name)
    raw = None
    try:
        raw = int(reader.trailer["/Encrypt"]["/P"])
    except Exception:
        raw = None
    prep = _Prep(path=Path(f.path), info=f, encrypted=True, user_pw=pw if needs else "", perms=Perms.from_int(raw),
                 method="pypdf", pages=len(reader.pages))
    if decrypt:
        ctx.progress(None, ctx.t("pdf.prog.decrypt"))
        writer = pp.PdfWriter()
        writer.append(reader)
        dst = _tmp(ctx, "decrypted.pdf")
        with open(dst, "wb") as fh:
            writer.write(fh)
        prep.path = dst
    return prep


def _prepare(ctx: Context, f: FileInfo | None = None, *, decrypt: bool = True) -> _Prep:
    """Inspect ``f`` (default: the current file) and return a readable, unencrypted path.

    Raises ``pdf.err.password_needed`` / ``pdf.err.wrong_password`` / ``err.damaged``.  When no
    PDF library or qpdf is installed the original path is returned unchecked.
    """
    f = f or ctx.file
    pw = _password(ctx)
    ctx.check_cancel()
    if _have("py:pymupdf"):
        prep = _prep_mupdf(ctx, f, pw, decrypt)
    elif _have("py:pikepdf"):
        prep = _prep_pikepdf(ctx, f, pw, decrypt)
    elif _have("qpdf"):
        prep = _prep_qpdf(ctx, f, pw, decrypt)
    elif _have("py:pypdf"):
        prep = _prep_pypdf(ctx, f, pw, decrypt)
    else:
        prep = _Prep(path=Path(f.path), info=f)
    if prep.pages == 0 and _have("py:pymupdf") and not prep.encrypted:
        raise UserError("pdf.err.no_pages")
    return prep


# ---------------------------------------------------------------------------- encryption output

def _encrypt_to(ctx: Context, src: Path, dst: Path, *, user: str, owner: str, perms: Perms) -> None:
    """Write ``src`` to ``dst`` encrypted with AES-256 (qpdf → pikepdf → PyMuPDF)."""
    owner = owner or secrets.token_urlsafe(24)
    if _have("qpdf"):
        argsfile = _tmp(ctx, "qpdf-args.txt")
        lines = ["--encrypt", f"--user-password={user}", f"--owner-password={owner}", "--bits=256",
                 *perms.qpdf_args(), "--", str(src), str(dst)]
        argsfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(argsfile, 0o600)
        except OSError:
            pass
        try:
            ctx.run(["qpdf", f"@{argsfile}"], ok_codes=(0, 3))
        finally:
            try:
                argsfile.unlink()
            except OSError:
                pass
        return
    pk = _pikepdf()
    if pk is not None:
        allow = pk.Permissions(accessibility=perms.accessibility, extract=perms.copy,
                               modify_annotation=perms.annotate, modify_assembly=perms.assemble,
                               modify_form=perms.form, modify_other=perms.modify, print_lowres=perms.print,
                               print_highres=perms.print and perms.print_hq)
        with pk.open(str(src)) as pdf:
            pdf.save(str(dst), encryption=pk.Encryption(user=user, owner=owner, R=6, allow=allow))
        return
    m = _mupdf()
    if m is not None:
        with m.open(str(src)) as doc:
            doc.save(str(dst), encryption=m.PDF_ENCRYPT_AES_256, user_pw=user, owner_pw=owner,
                     permissions=perms.bits(), garbage=1, deflate=True)
        return
    raise MissingTool(("qpdf",))


def _can_encrypt() -> bool:
    return _have("qpdf") or _have("py:pikepdf") or _have("py:pymupdf")


def _deliver(ctx: Context, prep: _Prep | None, produced: Path, out: Path, *, reprotect: bool = True) -> Path:
    """Move ``produced`` to ``out`` atomically, re-protecting it like the source when needed."""
    if not produced.exists() or produced.stat().st_size == 0:
        raise ToolError("err.no_output", name=out.name)
    ctx.check_cancel()
    with atomic_target(out) as part:
        if prep is not None and prep.encrypted and reprotect and _can_encrypt():
            _encrypt_to(ctx, produced, part, user=prep.user_pw, owner=prep.owner_pw, perms=prep.perms)
            if prep.user_pw:
                ctx.warn(ctx.t("pdf.warn.reprotected"))
        else:
            _move(produced, part)
            if prep is not None and prep.encrypted and prep.user_pw:
                ctx.warn(ctx.t("pdf.warn.protection_removed"))
    return out


# ================================================================================ page assembly

@dataclass
class _Spec:
    """One output page: ``src`` page (0-based, ``None`` = blank) with an extra clockwise rotation."""

    src: int | None
    rotate: int = 0
    size: tuple[float, float] | None = None


def _norm_rot(value: Any) -> int:
    try:
        r = int(round(float(value)))
    except (TypeError, ValueError):
        raise UserError("pdf.err.bad_pageops", details=f"rotate={value!r}") from None
    r %= 360
    if r % 90:
        raise UserError("pdf.err.bad_pageops", details=f"rotate={value!r}")
    return r


def _embedded_files(doc: Any) -> list[tuple[str, bytes, dict]]:
    out = []
    try:
        for name in doc.embfile_names():
            try:
                out.append((name, doc.embfile_get(name), doc.embfile_info(name)))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _restore_embedded(doc: Any, files: list[tuple[str, bytes, dict]]) -> None:
    if not files:
        return
    try:
        existing = set(doc.embfile_names())
    except Exception:
        existing = set()
    for name, data, info in files:
        if name in existing:
            continue
        try:
            doc.embfile_add(name, data, filename=info.get("filename") or name,
                            ufilename=info.get("ufilename") or info.get("filename") or name,
                            desc=info.get("description") or "")
        except Exception:
            log.debug("could not restore attachment %s", name, exc_info=True)


def _blank_sizes_mupdf(doc: Any, specs: list[_Spec]) -> dict[int, tuple[float, float]]:
    sizes: dict[int, tuple[float, float]] = {}
    for i, s in enumerate(specs):
        if s.src is not None:
            continue
        if s.size:
            sizes[i] = s.size
            continue
        ref = next((specs[j].src for j in range(i - 1, -1, -1) if specs[j].src is not None), None)
        if ref is None:
            ref = next((sp.src for sp in specs[i + 1:] if sp.src is not None), None)
        if ref is None or not doc.page_count:
            sizes[i] = A4
        else:
            r = doc[ref].rect
            sizes[i] = (float(r.width), float(r.height))
    return sizes


def _rebuild_mupdf(ctx: Context, src: Path, specs: list[_Spec], dst: Path) -> None:
    m = _need_mupdf()
    with m.open(str(src)) as doc:
        total = doc.page_count
        for s in specs:
            if s.src is not None and not 0 <= s.src < total:
                raise UserError("pdf.err.bad_pageops", details=f"page {s.src + 1} > {total}")
        emb = _embedded_files(doc)
        sizes = _blank_sizes_mupdf(doc, specs)
        order = [s.src for s in specs if s.src is not None]
        if not order:
            raise UserError("pdf.err.no_pages_left")
        if order != list(range(total)):
            doc.select(order)
            # select() links repeated pages to the same page object: give each repetition its own
            # copy, otherwise rotating one duplicate would rotate all of them
            seen: set[int] = set()
            for pos, src_page in enumerate(order):
                if src_page in seen:
                    doc.fullcopy_page(pos, pos)
                    doc.delete_page(pos + 1)
                seen.add(src_page)
        k = 0
        for s in specs:
            if s.src is None:
                continue
            if s.rotate:
                page = doc[k]
                page.set_rotation((page.rotation + s.rotate) % 360)
            k += 1
        for pos, s in enumerate(specs):
            if s.src is None:
                w, h = sizes[pos]
                pno = pos if pos < doc.page_count else -1
                page = doc.new_page(pno=pno, width=w, height=h)
                if s.rotate:
                    page.set_rotation(s.rotate)
        _restore_embedded(doc, emb)
        ctx.progress(None, ctx.t("pdf.prog.saving"))
        doc.save(str(dst), garbage=3, deflate=True)


def _page_box(page: Any) -> tuple[float, float]:
    """Visible size of a pypdf page (rotation applied)."""
    w = float(page.mediabox.width)
    h = float(page.mediabox.height)
    rot = int(page.get("/Rotate", 0) or 0) % 360
    return (h, w) if rot in (90, 270) else (w, h)


def _rebuild_pypdf(ctx: Context, src: Path, specs: list[_Spec], dst: Path) -> None:
    pp = _pypdf()
    reader = pp.PdfReader(str(src), strict=False)
    total = len(reader.pages)
    writer = pp.PdfWriter()
    for i, s in enumerate(specs):
        ctx.check_cancel()
        if s.src is None:
            if s.size:
                w, h = s.size
            else:
                ref = next((specs[j].src for j in range(i - 1, -1, -1) if specs[j].src is not None), None)
                if ref is None:
                    ref = next((sp.src for sp in specs[i + 1:] if sp.src is not None), None)
                w, h = _page_box(reader.pages[ref]) if ref is not None and total else A4
            page = writer.add_blank_page(width=w, height=h)
            if s.rotate:
                page.rotate(s.rotate)
            continue
        if not 0 <= s.src < total:
            raise UserError("pdf.err.bad_pageops", details=f"page {s.src + 1} > {total}")
        page = writer.add_page(reader.pages[s.src])
        if s.rotate:
            page.rotate(s.rotate)
    try:
        meta = reader.metadata or {}
        writer.add_metadata({str(k): str(v) for k, v in meta.items() if isinstance(k, str) and v is not None})
    except Exception:
        pass
    with open(dst, "wb") as fh:
        writer.write(fh)


def _qpdf_ranges(indices: Sequence[int]) -> str:
    return ",".join(str(a + 1) if a == b else f"{a + 1}-{b + 1}" for a, b in _runs(list(indices)))


def _rebuild_qpdf(ctx: Context, src: Path, specs: list[_Spec], dst: Path) -> None:
    total = 0
    try:
        total = int(ctx.run(["qpdf", "--show-npages", str(src)], ok_codes=(0, 3), timeout=120).stdout.strip() or 0)
    except (ToolError, ValueError):
        total = 0
    for s in specs:
        if s.src is not None and total and not 0 <= s.src < total:
            raise UserError("pdf.err.bad_pageops", details=f"page {s.src + 1} > {total}")
    blanks: dict[tuple[float, float], Path] = {}
    groups: list[tuple[str, list[int]]] = []
    for s in specs:
        if s.src is None:
            size = s.size or A4
            if size not in blanks:
                bp = _tmp(ctx, "blank.pdf")
                bp.write_bytes(blank_pdf_bytes(*size))
                blanks[size] = bp
            key = str(blanks[size])
            page = 0
        else:
            key, page = ".", s.src
        if groups and groups[-1][0] == key and key == ".":
            groups[-1][1].append(page)
        else:
            groups.append((key, [page]))
    # rotations refer to the pages of the output (qpdf applies them after the page selection)
    args: list[str] = []
    by_angle: dict[int, list[int]] = {}
    for n, s in enumerate(specs):
        if s.rotate:
            by_angle.setdefault(s.rotate, []).append(n)
    for angle, pages in by_angle.items():
        args.append(f"--rotate=+{angle}:{_qpdf_ranges(pages)}")
    args += [str(src), "--pages"]
    for key, pages in groups:
        args += [key, _qpdf_ranges(pages)]
    args += ["--", str(dst)]
    ctx.run(["qpdf", *args], ok_codes=(0, 3))


def _rebuild(ctx: Context, src: Path, specs: list[_Spec], dst: Path) -> None:
    """Write ``dst`` with the pages of ``src`` arranged as ``specs`` (PyMuPDF → pypdf → qpdf)."""
    if not any(s.src is not None for s in specs):
        raise UserError("pdf.err.no_pages_left")
    ctx.progress(None, ctx.t("pdf.prog.working"))
    if _have("py:pymupdf"):
        _rebuild_mupdf(ctx, src, specs, dst)
    elif _have("py:pypdf"):
        _rebuild_pypdf(ctx, src, specs, dst)
    elif _have("qpdf"):
        _rebuild_qpdf(ctx, src, specs, dst)
    else:
        raise MissingTool(["py:pymupdf"])


def _assemble(ctx: Context, sources: Sequence[Path], plan: Sequence[tuple[int, int]], dst: Path, *,
              outline: Sequence[tuple[str, int]] = (), keep_tocs: bool = False) -> None:
    """Write ``dst`` from pages of several PDFs: ``plan`` = ``[(source, page)]`` (0-based).

    ``outline`` adds top-level bookmarks ``(title, output page index)``; ``keep_tocs`` nests each
    source's bookmarks below its entry (merge of whole files).
    """
    if not plan:
        raise UserError("pdf.err.no_pages_left")
    total = len(plan)
    if _have("py:pymupdf"):
        m = _need_mupdf()
        docs = [m.open(str(p)) for p in sources]
        try:
            out = m.open()
            # group consecutive pages of the same source into runs
            runs: list[tuple[int, int, int]] = []
            for s, p in plan:
                if runs and runs[-1][0] == s and abs(p - runs[-1][2]) == 1 and (
                        runs[-1][1] == runs[-1][2] or (p - runs[-1][2]) == (runs[-1][2] - runs[-1][1]) //
                        max(1, abs(runs[-1][2] - runs[-1][1]))):
                    runs[-1] = (s, runs[-1][1], p)
                else:
                    runs.append((s, p, p))
            done = 0
            for idx, (s, a, b) in enumerate(runs):
                ctx.check_cancel()
                last_of_src = all(r[0] != s for r in runs[idx + 1:])
                out.insert_pdf(docs[s], from_page=a, to_page=b, final=1 if last_of_src else 0)
                done += abs(b - a) + 1
                ctx.progress(0.9 * done / total, ctx.t("pdf.prog.page", n=done, total=total))
            if outline:
                toc: list[list] = []
                starts = sorted(outline, key=lambda o: o[1])
                for title, start in starts:
                    toc.append([1, title, start + 1])
                    if keep_tocs:
                        src_index = next((s for (s, p), i in zip(plan, range(total)) if i == start), None)
                        if src_index is not None:
                            for entry in docs[src_index].get_toc(simple=True):
                                page = int(entry[2])
                                if page >= 1:
                                    toc.append([int(entry[0]) + 1, str(entry[1]), start + page])
                try:
                    out.set_toc(fix_toc_levels(toc))
                except Exception:
                    log.debug("set_toc failed", exc_info=True)
            ctx.progress(0.95, ctx.t("pdf.prog.saving"))
            out.save(str(dst), garbage=3, deflate=True)
            out.close()
        finally:
            for d in docs:
                d.close()
        return
    if _have("py:pypdf"):
        pp = _pypdf()
        readers = [pp.PdfReader(str(p), strict=False) for p in sources]
        writer = pp.PdfWriter()
        for n, (s, p) in enumerate(plan, start=1):
            ctx.check_cancel()
            writer.add_page(readers[s].pages[p])
            if n % 10 == 0:
                ctx.progress(0.9 * n / total, ctx.t("pdf.prog.page", n=n, total=total))
        for title, start in outline:
            try:
                writer.add_outline_item(title, start)
            except Exception:
                pass
        with open(dst, "wb") as fh:
            writer.write(fh)
        return
    if _have("qpdf"):
        args = ["--empty", "--pages"]
        groups: list[tuple[int, list[int]]] = []
        for s, p in plan:
            if groups and groups[-1][0] == s:
                groups[-1][1].append(p)
            else:
                groups.append((s, [p]))
        for s, pages in groups:
            args += [str(sources[s]), _qpdf_ranges(pages)]
        args += ["--", str(dst)]
        ctx.run(["qpdf", *args], ok_codes=(0, 3))
        return
    raise MissingTool(["py:pymupdf"])


def _page_count(ctx: Context, path: Path) -> int:
    if _have("py:pymupdf"):
        with _need_mupdf().open(str(path)) as doc:
            return int(doc.page_count)
    if _have("py:pypdf"):
        return len(_pypdf().PdfReader(str(path), strict=False).pages)
    if _have("qpdf"):
        try:
            return int(ctx.run(["qpdf", "--show-npages", str(path)], ok_codes=(0, 3), timeout=120).stdout.strip() or 0)
        except ValueError:
            return 0
    if _have("pdfinfo"):
        out = ctx.run(["pdfinfo", str(path)], timeout=60).stdout
        m = re.search(r"^Pages:\s*(\d+)", out, re.M)
        return int(m.group(1)) if m else 0
    raise MissingTool(["py:pymupdf"])


# ================================================================================ common flows

def _out_pdf(ctx: Context, f: FileInfo, suffix: str) -> Path:
    return ctx.output_path(f, suffix=suffix, ext="pdf")


def _finish_pdf(ctx: Context, prep: _Prep, produced: Path, suffix: str, *, message: str = "",
                reprotect: bool = True, replace: bool = True, size_before: int | None = None) -> Result:
    f = prep.info
    out = _out_pdf(ctx, f, suffix)
    _deliver(ctx, prep, produced, out, reprotect=reprotect)
    final = ctx.replace_original(f, out) if replace else out
    return ctx.result(final, message=message, size_before=size_before)


def _pages_param(ctx: Context, name: str, total: int, *, empty_means_all: bool = False) -> list[int]:
    spec = ctx.pstr(name)
    try:
        return parse_pages(spec, total, empty_means_all=empty_means_all)
    except ValueError as exc:
        raise UserError("pdf.err.bad_pages", details=str(exc), spec=spec) from None


def _rebuild_action(ctx: Context, specs_fn: Callable[[_Prep], list[_Spec]], suffix: str,
                    message: str = "") -> Result:
    prep = _prepare(ctx)
    if not prep.pages:
        prep.pages = _page_count(ctx, prep.path)
    specs = specs_fn(prep)
    produced = _tmp(ctx, "result.pdf")
    _rebuild(ctx, prep.path, specs, produced)
    return _finish_pdf(ctx, prep, produced, suffix, message=message)


def _run_gs(ctx: Context, args: list[str], src: Path, dst: Path, pages: int, label: str, *,
            prelude: Sequence[Path] = (), postscript: str = "") -> None:
    """Ghostscript pdfwrite with per-page progress (``Page N`` lines).

    ``prelude``: PostScript files run after the output device is set up and before the input
    (e.g. the PDF/A definition file, which uses pdfmark and needs ``-sOutputFile`` first).
    ``postscript``: PostScript code run at that same point.
    """
    total = max(0, int(pages or 0))
    ctx.progress(0.0 if total else None, label)

    def on_line(line: str) -> None:
        m = re.match(r"^\s*Page\s+(\d+)", line)
        if m and total:
            ctx.progress(min(0.99, int(m.group(1)) / total), label)

    out = str(dst).replace("%", "%%")
    pre: list[str] = []
    for p in prelude:
        pre += ["-f", str(p)]
    if postscript:
        pre += ["-c", postscript]
    ctx.run(["gs", "-sDEVICE=pdfwrite", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dAutoRotatePages=/None", *args,
             f"-sOutputFile={out}", *pre, "-f", str(src)], on_line=on_line)
    if not dst.exists() or dst.stat().st_size == 0:
        raise ToolError("pdf.err.convert_failed", details="ghostscript produced no output")


def _locked_files(files: Sequence[FileInfo] | None) -> list[FileInfo]:
    """The PDFs of ``files`` that cannot be opened without a password."""
    out = []
    for f in files or []:
        if getattr(f, "category", "") == "pdf" and getattr(f, "path", "") and \
                encryption_state(f.path)["needs_password"]:
            out.append(f)
    return out


class PasswordParam(Param):
    """The optional ``password`` param of every content action.

    Its menu JSON is marked ``required: true`` with an explanatory ``help`` (and
    ``locked: true``) when a dropped PDF needs a password, so the form asks for it; the static
    ``required`` stays ``False`` so the action can still report a readable
    ``pdf.err.password_needed`` when run without one.
    """

    def to_json(self, files: list[FileInfo] | None = None, lang: str | None = None,
                config: dict | None = None) -> dict:
        data = super().to_json(files, lang, config)
        try:
            locked = _locked_files(files)
        except Exception:
            log.debug("encryption probe failed", exc_info=True)
            locked = []
        if locked:
            data["required"] = True
            data["locked"] = True
            if len(locked) == 1:
                data["help"] = i18n.t("pdf.prm.password.locked", lang, name=locked[0].name)
            else:
                data["help"] = i18n.tn("pdf.prm.password.locked_many", len(locked), lang)
        return data


def _password_param() -> Param:
    return PasswordParam(name="password", type="password", label="pdf.prm.password", default="",
                         help="pdf.prm.password.help")


def _doc_text_chars(path: Path, limit_pages: int = 30) -> int:
    """Characters of extractable text in the first pages (0 = probably a scan)."""
    m = _mupdf()
    if m is None:
        return -1
    try:
        with m.open(str(path)) as doc:
            return sum(len(doc[i].get_text("text").strip()) for i in range(min(limit_pages, doc.page_count)))
    except Exception:
        return -1


def _lang_of(ctx: Context, path: Path | None = None) -> str:
    m = _mupdf()
    if m is not None and path is not None:
        try:
            with m.open(str(path)) as doc:
                kind, value = doc.xref_get_key(doc.pdf_catalog(), "Lang")
                if kind == "string" and value:
                    return value.split("-")[0].lower()[:8]
        except Exception:
            pass
    return ctx.lang


# ==================================================================================== CONVERT

def _render_dpi(width_pt: float, height_pt: float, dpi: int) -> int:
    px = (width_pt * dpi / 72.0) * (height_pt * dpi / 72.0)
    if px <= MAX_RENDER_PIXELS:
        return dpi
    return max(36, int(dpi * math.sqrt(MAX_RENDER_PIXELS / px)))


def _render_page_to(ctx: Context, doc: Any, pno: int, dpi: int, dst: Path, fmt: str, quality: int) -> int:
    """Render page ``pno`` of an open PyMuPDF document to ``dst``; returns the dpi used."""
    page = doc[pno]
    use = _render_dpi(page.rect.width, page.rect.height, dpi)
    pix = page.get_pixmap(dpi=use, alpha=False)
    if fmt == "jpg":
        pix.save(str(dst), output="jpeg", jpg_quality=int(quality))
    else:
        pix.save(str(dst), output="png")
    return use


def _pdftoppm_page(ctx: Context, src: Path, pno: int, dpi: int, dst: Path, fmt: str, quality: int) -> None:
    base = _tmp(ctx, "ppm")
    args = ["pdftoppm", "-r", str(dpi), "-f", str(pno + 1), "-l", str(pno + 1), "-singlefile"]
    args += ["-jpeg", "-jpegopt", f"quality={int(quality)}"] if fmt == "jpg" else ["-png"]
    ctx.run([*args, str(src), str(base)], timeout=600)
    produced = base.with_name(base.name + (".jpg" if fmt == "jpg" else ".png"))
    if not produced.exists():
        raise ToolError("pdf.err.convert_failed", details=f"pdftoppm wrote no {produced.name}")
    _move(produced, dst)


def _to_images(ctx: Context, fmt: str) -> Result:
    f = ctx.file
    prep = _prepare(ctx)
    dpi = max(36, min(1200, ctx.pint("dpi", int(cfgmod.get(ctx.config, "pdf.dpi", 150)))))
    quality = max(1, min(100, ctx.pint("quality", int(cfgmod.get(ctx.config, "image.jpeg_quality", 85)))))
    total = prep.pages or _page_count(ctx, prep.path)
    if total <= 0:
        raise UserError("pdf.err.no_pages")
    pages = [0] if ctx.pbool("first_only") else _pages_param(ctx, "pages", total, empty_means_all=True)
    use_mupdf = _have("py:pymupdf")
    if not use_mupdf and not _have("pdftoppm"):
        raise MissingTool(["py:pymupdf"])
    doc = _need_mupdf().open(str(prep.path)) if use_mupdf else None
    try:
        width = max(3, len(str(total)))
        if len(pages) == 1:
            targets = [ctx.output_path(f, ext=fmt)]
            folder = None
        else:
            folder = ctx.output_dir(f, suffix="sfx.pages")
            targets = [folder / f"page-{p + 1:0{width}d}.{fmt}" for p in pages]
        reduced = False
        for i, (pno, target) in enumerate(zip(pages, targets)):
            ctx.check_cancel()
            ctx.progress(i / len(pages), ctx.t("pdf.prog.page", n=i + 1, total=len(pages)))
            if target.exists():
                target = util.unique_path(target)
            with atomic_target(target) as part:
                if doc is not None:
                    used = _render_page_to(ctx, doc, pno, dpi, part, fmt, quality)
                    reduced = reduced or used < dpi
                else:
                    _pdftoppm_page(ctx, prep.path, pno, dpi, part, fmt, quality)
        if reduced:
            ctx.warn(ctx.t("pdf.warn.dpi_reduced"))
    finally:
        if doc is not None:
            doc.close()
    if folder is not None:
        return ctx.result([folder], message=i18n.tn("pdf.msg.pages_exported", len(pages), ctx.lang))
    return ctx.result(targets)


def _extract_text(ctx: Context, path: Path, *, layout: bool = True) -> str:
    """Text layer of a PDF (pdftotext -layout → PyMuPDF)."""
    if layout and _have("pdftotext"):
        res = ctx.run(["pdftotext", "-layout", "-enc", "UTF-8", "-nopgbrk", str(path), "-"], timeout=None)
        return res.stdout
    m = _mupdf()
    if m is not None:
        parts = []
        with m.open(str(path)) as doc:
            n = doc.page_count
            for i in range(n):
                ctx.check_cancel()
                if n > 5:
                    ctx.progress(i / n, ctx.t("pdf.prog.page", n=i + 1, total=n))
                parts.append(doc[i].get_text("text", sort=True).rstrip())
        return "\n\n".join(parts).strip() + "\n"
    if _have("pdftotext"):
        return ctx.run(["pdftotext", "-enc", "UTF-8", "-nopgbrk", str(path), "-"], timeout=None).stdout
    raise MissingTool(["pdftotext"])


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _markdown_llm(ctx: Context, doc: Any) -> str | None:
    """Markdown from the pymupdf4llm code, ``None`` when this PyMuPDF has none or it fails.

    That code is the ``pymupdf4llm`` module (AUR, pinned to PyMuPDF 1.28) or, from PyMuPDF 2.0,
    ``Document.to_markdown``, which only the PyPI wheels can run: Arch's package raises
    ``NotImplementedError``.  OCR stays off: it has its own actions, with the right languages.
    """
    n = doc.page_count
    kw: dict = {"show_progress": False, "use_ocr": False}
    try:
        import pymupdf4llm  # type: ignore
    except Exception:
        pymupdf4llm = None
    if pymupdf4llm is not None:
        convert: Callable[..., Any] = lambda **args: pymupdf4llm.to_markdown(doc, **args)
        try:
            kw["hdr_info"] = pymupdf4llm.IdentifyHeaders(doc)
        except Exception:
            pass
    elif hasattr(doc, "to_markdown"):
        convert = doc.to_markdown
    else:
        return None
    step = max(1, n // 20)                              # a call without hdr_info scans the whole document
    try:
        parts = []
        for first in range(0, n, step):
            ctx.check_cancel()
            ctx.progress(first / max(1, n), ctx.t("pdf.prog.page", n=first + 1, total=n))
            pages = list(range(first, min(n, first + step)))
            try:
                parts.append(convert(pages=pages, **kw))
            except TypeError:                           # a pymupdf4llm without use_ocr
                kw.pop("use_ocr", None)
                parts.append(convert(pages=pages, **kw))
        return "\n\n".join(p.strip() for p in parts if p.strip()) + "\n"
    except (Cancelled, UserError):
        raise
    except Exception:
        log.debug("pymupdf4llm code failed or missing, using the fallback", exc_info=True)
        return None


def _markdown(ctx: Context, path: Path) -> tuple[str, bool]:
    """Markdown of a PDF: the pymupdf4llm code when available, else the structured fallback (second item False)."""
    m = _need_mupdf()
    with m.open(str(path)) as doc:
        n = doc.page_count
        md = _markdown_llm(ctx, doc)
        if md is not None:
            return md, True
        flags = 0
        try:
            flags = m.TEXTFLAGS_DICT & ~m.TEXT_PRESERVE_IMAGES
        except AttributeError:
            flags = 0
        pages = []
        for i in range(n):
            ctx.check_cancel()
            ctx.progress(i / max(1, n), ctx.t("pdf.prog.page", n=i + 1, total=n))
            page = doc[i]
            data = page.get_text("dict", sort=True, flags=flags) if flags else page.get_text("dict", sort=True)
            tables = []
            if n <= 400:
                try:
                    for tab in page.find_tables().tables:
                        if tab.row_count >= 2 and tab.col_count >= 2:
                            tables.append({"bbox": tuple(tab.bbox), "md": tab.to_markdown(clean=False)})
                except Exception:
                    tables = []
            pages.append({"blocks": data.get("blocks", []), "tables": tables})
        return markdown_from_pages(pages), False


def _xhtml_pages(ctx: Context, path: Path) -> tuple[list[str], dict, list]:
    """Per-page XHTML fragments (well-formed), the document metadata and the TOC."""
    m = _need_mupdf()
    frags: list[str] = []
    with m.open(str(path)) as doc:
        n = doc.page_count
        for i in range(n):
            ctx.check_cancel()
            ctx.progress(0.8 * i / max(1, n), ctx.t("pdf.prog.page", n=i + 1, total=n))
            page = doc[i]
            try:
                frag = _xml_entities(page.get_text("xhtml"))
            except Exception:
                frag = ""
            frag = _ensure_img_alt(frag)
            if not frag.strip() or not _well_formed(frag):
                frag = _text_to_xhtml(page.get_text("text", sort=True), anchor=f"page{i}")
            frags.append(frag)
        meta = dict(doc.metadata or {})
        toc = doc.get_toc(simple=True)
    return frags, meta, toc


def _epub_chapters(frags: list[str], toc: list, title: str) -> list[tuple[str, list[int]]]:
    starts = sorted({(int(e[2]) - 1, str(e[1])) for e in toc if int(e[0]) == 1 and int(e[2]) >= 1},
                    key=lambda x: x[0])
    starts = [(p, t) for p, t in starts if p < len(frags)]
    dedup: list[tuple[int, str]] = []
    for p, t in starts:
        if not dedup or dedup[-1][0] != p:
            dedup.append((p, t))
    if not dedup:
        size = 20
        return [(title if len(frags) <= size else f"{title} ({a + 1}–{min(a + size, len(frags))})",
                 list(range(a, min(a + size, len(frags))))) for a in range(0, len(frags), size)]
    chapters: list[tuple[str, list[int]]] = []
    if dedup[0][0] > 0:
        chapters.append((title, list(range(0, dedup[0][0]))))
    for k, (p, t) in enumerate(dedup):
        end = dedup[k + 1][0] if k + 1 < len(dedup) else len(frags)
        chapters.append((t, list(range(p, end))))
    return chapters


# ===================================================================================== COMPRESS

def _compress_mupdf(ctx: Context, src: Path, dst: Path, preset: str, dpi: int, quality: int) -> None:
    m = _need_mupdf()
    with m.open(str(src)) as doc:
        if preset != "light" and hasattr(doc, "rewrite_images"):
            target = {"balanced": 150, "strong": 72}.get(preset, dpi)
            q = {"balanced": 75, "strong": 55}.get(preset, quality)
            ctx.progress(None, ctx.t("pdf.prog.images"))
            try:
                doc.rewrite_images(dpi_threshold=int(target * 1.25), dpi_target=int(target), quality=int(q))
            except Exception as exc:
                log.debug("rewrite_images failed: %s", exc)
        elif preset != "light":
            ctx.warn(ctx.t("pdf.warn.lossless_only"))
        try:
            doc.subset_fonts()
        except Exception:
            pass
        ctx.progress(None, ctx.t("pdf.prog.saving"))
        doc.save(str(dst), garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=1, clean=True)


def _compress(ctx: Context, preset: str) -> Result:
    f = ctx.file
    prep = _prepare(ctx)
    before = f.size or os.path.getsize(f.path)
    produced = _tmp(ctx, "compressed.pdf")
    dpi = max(36, min(600, ctx.pint("dpi", 120)))
    quality = max(10, min(95, ctx.pint("quality", 70)))
    label = ctx.t("pdf.prog.compressing")
    if _have("gs"):
        custom = preset == "custom"
        _run_gs(ctx, gs_compress_args(preset, dpi, quality if custom else None), prep.path, produced, prep.pages, label,
                postscript=gs_image_quality_ps(quality) if custom else "")
    elif preset == "light" and _have("qpdf"):
        ctx.progress(None, label)
        ctx.run(["qpdf", "--object-streams=generate", "--recompress-flate", "--compression-level=9",
                 str(prep.path), str(produced)], ok_codes=(0, 3))
    else:
        _compress_mupdf(ctx, prep.path, produced, preset, dpi, quality)
    after = produced.stat().st_size if produced.exists() else 0
    if not after or after >= before:
        ctx.warn(ctx.t("warn.already_optimized"))
        return ctx.result(message=ctx.t("pdf.msg.already_small", size=util.human_size(before, ctx.lang)))
    return _finish_pdf(ctx, prep, produced, "sfx.compressed", size_before=before)


# ======================================================================================= ACTIONS
# --------------------------------------------------------------------------------- convert ----

_IMG_PARAMS = [
    P.int("dpi", "pdf.prm.dpi", "cfg:pdf.dpi", min=36, max=1200, step=1, unit="pdf.unit.dpi"),
    P.bool("first_only", "pdf.prm.first_only", False),
    P.pages("pages", "pdf.prm.pages", "", help="pdf.prm.pages_all.help"),
]


@action(id="pdf.convert.jpg", label="JPG", text="JPG", icon="image", categories={"pdf"}, mode="quick",
        params=[*_IMG_PARAMS, P.int("quality", "pdf.prm.quality", "cfg:image.jpeg_quality", min=1, max=100,
                                    unit="pdf.unit.pct"), _password_param()],
        requires=[("py:pymupdf", "pdftoppm")], description="pdf.desc.to_images", order=11)
def convert_jpg(ctx: Context) -> Result:
    """One JPEG per page (folder when several pages)."""
    return _to_images(ctx, "jpg")


@action(id="pdf.convert.png", label="PNG", text="PNG", icon="image", categories={"pdf"}, mode="quick",
        params=[*_IMG_PARAMS, _password_param()], requires=[("py:pymupdf", "pdftoppm")],
        description="pdf.desc.to_images", order=12)
def convert_png(ctx: Context) -> Result:
    """One PNG per page (folder when several pages)."""
    return _to_images(ctx, "png")


@action(id="pdf.convert.txt", label="TXT", text="TXT", icon="description", categories={"pdf"}, mode="quick",
        params=[P.bool("layout", "pdf.prm.layout", True), _password_param()],
        requires=[("pdftotext", "py:pymupdf")], description="pdf.desc.to_txt", order=13)
def convert_txt(ctx: Context) -> Result:
    """Plain text (``pdftotext -layout`` or PyMuPDF)."""
    f = ctx.file
    prep = _prepare(ctx)
    ctx.progress(None, ctx.t("pdf.prog.text"))
    text = _extract_text(ctx, prep.path, layout=ctx.pbool("layout", True))
    if not text.strip():
        ctx.warn(ctx.t("pdf.warn.no_text"))
    out = ctx.output_path(f, ext="txt")
    with atomic_target(out) as part:
        _write_text(part, text)
    return ctx.result(out)


@action(id="pdf.convert.md", label="pdf.act.markdown", text="MD", icon="article", categories={"pdf"},
        mode="quick", params=[_password_param()], requires=["py:pymupdf"], description="pdf.desc.to_md", order=14)
def convert_md(ctx: Context) -> Result:
    """Markdown (the pymupdf4llm code, or headings from font sizes)."""
    f = ctx.file
    prep = _prepare(ctx)
    md, full = _markdown(ctx, prep.path)
    if not full:
        ctx.warn(ctx.t("pdf.warn.markdown_basic"))
    if not md.strip():
        ctx.warn(ctx.t("pdf.warn.no_text"))
    out = ctx.output_path(f, ext="md")
    with atomic_target(out) as part:
        _write_text(part, md)
    return ctx.result(out)


_PDF2DOCX_SCRIPT = r"""
import logging, sys, warnings
warnings.simplefilter("ignore")
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
from pdf2docx import Converter
cv = Converter(sys.argv[1])
try:
    cv.convert(sys.argv[2])
finally:
    cv.close()
"""


def _soffice_convert(ctx: Context, src: Path, target_ext: str, filter_name: str, *, infilter: str = "") -> Path:
    """LibreOffice headless conversion with a private profile; returns the produced file in tmp."""
    work = ctx.tmpdir() / f"lo-{secrets.token_hex(3)}"
    outdir = work / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    local = work / "input.pdf"
    shutil.copyfile(src, local)
    profile = (work / "profile").absolute().as_uri()
    args = ["soffice", f"-env:UserInstallation={profile}", "--headless", "--norestore", "--nologo", "--nodefault",
            "--nolockcheck"]
    if infilter:
        args.append(f"--infilter={infilter}")
    args += ["--convert-to", f"{target_ext}:{filter_name}", "--outdir", str(outdir), str(local)]
    ctx.progress(None, ctx.t("pdf.prog.libreoffice"))
    size_mb = max(1, src.stat().st_size // 1_000_000)
    ctx.run(args, timeout=min(3600, 240 + size_mb * 30), env={"SAL_USE_VCLPLUGIN": "svp"})
    produced = outdir / f"input.{target_ext}"
    if not produced.exists() or produced.stat().st_size == 0:
        raise ToolError("pdf.err.convert_failed", details="LibreOffice produced no output")
    return produced


@action(id="pdf.convert.docx", label="DOCX", text="DOCX", icon="description", categories={"pdf"}, mode="quick",
        params=[_password_param()], requires=[("py:pdf2docx", "soffice")], description="pdf.desc.to_docx",
        order=10)
def convert_docx(ctx: Context) -> Result:
    """Word document (pdf2docx, else LibreOffice ``writer_pdf_import``)."""
    f = ctx.file
    prep = _prepare(ctx)
    if _doc_text_chars(prep.path) == 0:
        ctx.warn(ctx.t("pdf.warn.scan_docx"))
    out = ctx.output_path(f, ext="docx")
    if _have("py:pdf2docx"):
        produced = _tmp(ctx, "result.docx")
        state = {"phase": 0}

        def on_line(line: str) -> None:
            ph = re.search(r"\[(\d)/4\]", line)
            if ph:
                state["phase"] = int(ph.group(1))
                ctx.progress({1: 0.02, 2: 0.05, 3: 0.1, 4: 0.55}.get(state["phase"], None), ctx.t("pdf.prog.converting"))
                return
            pg = re.search(r"\((\d+)/(\d+)\)", line)
            if pg and int(pg.group(2)):
                frac = int(pg.group(1)) / int(pg.group(2))
                base, span = (0.1, 0.45) if state["phase"] <= 3 else (0.55, 0.43)
                ctx.progress(base + span * frac, ctx.t("pdf.prog.converting"))

        ctx.progress(None, ctx.t("pdf.prog.converting"))
        ctx.run([sys.executable, "-c", _PDF2DOCX_SCRIPT, str(prep.path), str(produced)], on_line=on_line,
                stderr_lines=True, env={"PYTHONWARNINGS": "ignore"})
        if not produced.exists() or produced.stat().st_size == 0:
            raise ToolError("pdf.err.convert_failed", details="pdf2docx produced no output")
    else:
        produced = _soffice_convert(ctx, prep.path, "docx", "MS Word 2007 XML", infilter="writer_pdf_import")
    _deliver(ctx, None, produced, out)
    return ctx.result(out)


@action(id="pdf.convert.html", label="HTML", text="HTML", icon="html", categories={"pdf"}, mode="quick",
        params=[_password_param()], requires=["py:pymupdf"], description="pdf.desc.to_html", order=16)
def convert_html(ctx: Context) -> Result:
    """Reflowable HTML (one file, images embedded)."""
    f = ctx.file
    prep = _prepare(ctx)
    frags, meta, _toc = _xhtml_pages(ctx, prep.path)
    title = (meta.get("title") or "").strip() or f.stem
    doc_html = build_html(title, _lang_of(ctx, prep.path), "\n".join(frags))
    out = ctx.output_path(f, ext="html")
    with atomic_target(out) as part:
        _write_text(part, doc_html)
    return ctx.result(out)


@action(id="pdf.convert.epub", label="EPUB", text="EPUB", icon="menu_book", categories={"pdf"}, mode="quick",
        params=[_password_param()], requires=["py:pymupdf"], description="pdf.desc.to_epub", order=15)
def convert_epub(ctx: Context) -> Result:
    """EPUB 3 (pandoc or calibre when installed, else the built-in writer)."""
    f = ctx.file
    prep = _prepare(ctx)
    frags, meta, toc = _xhtml_pages(ctx, prep.path)
    title = (meta.get("title") or "").strip() or f.stem
    author = (meta.get("author") or "").strip()
    lang = _lang_of(ctx, prep.path)
    out = ctx.output_path(f, ext="epub")
    produced = _tmp(ctx, "book.epub")
    ctx.progress(0.85, ctx.t("pdf.prog.converting"))
    done = False
    if _have("pandoc") or _have("ebook-convert"):
        src_html = _tmp(ctx, "book.html")
        _write_text(src_html, build_html(title, lang, "\n".join(frags)))
        try:
            if _have("pandoc"):
                args = ["pandoc", "-f", "html", "-t", "epub3", "--metadata", f"title={title}",
                        "--metadata", f"lang={lang}"]
                if author:
                    args += ["--metadata", f"author={author}"]
                ctx.run([*args, "-o", str(produced), str(src_html)], timeout=1800, cwd=str(ctx.tmpdir()))
            else:
                args = ["ebook-convert", str(src_html), str(produced), "--title", title, "--language", lang]
                if author:
                    args += ["--authors", author]
                ctx.run(args, timeout=1800, env={"QT_QPA_PLATFORM": "offscreen"})
            done = produced.exists() and produced.stat().st_size > 0
        except ToolError as exc:
            log.info("external EPUB conversion failed (%s), using the built-in writer", exc)
            done = False
    if not done:
        images: dict[str, bytes] = {}
        bodies = [_extract_data_uris(frag, f"p{i + 1:04d}", images) for i, frag in enumerate(frags)]
        chapters = [(t, "\n".join(bodies[i] for i in idx)) for t, idx in _epub_chapters(bodies, toc, title)]
        build_epub(produced, title=title, author=author, lang=lang, chapters=chapters, images=images)
    _deliver(ctx, None, produced, out)
    return ctx.result(out)


def _icc_profile() -> str | None:
    import glob

    candidates = ["/usr/share/ghostscript/iccprofiles/srgb.icc", *sorted(glob.glob(
        "/usr/share/ghostscript/*/iccprofiles/srgb.icc")), "/usr/share/color/icc/colord/sRGB.icc",
                  "/usr/share/color/icc/sRGB.icc", "/usr/share/color/icc/ghostscript/srgb.icc"]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _ps_string(text: str) -> str:
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


_PDFA_DEF = """%!
% PDF/A-2b definition written by Dropwheel
/ICCProfile {icc} def
[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[{{icc_PDFA}} << /N 3 >> /PUT pdfmark
[{{icc_PDFA}} ICCProfile (r) file /PUT pdfmark
[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFA}} <<
  /Type /OutputIntent
  /S /GTS_PDFA1
  /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB)
>> /PUT pdfmark
[{{Catalog}} <</OutputIntents [ {{OutputIntent_PDFA}} ]>> /PUT pdfmark
"""


@action(id="pdf.convert.pdfa", label="pdf.act.pdfa", text="PDF/A", icon="verified", categories={"pdf"},
        mode="quick", params=[_password_param()], requires=["gs"], description="pdf.desc.pdfa", order=17)
def convert_pdfa(ctx: Context) -> Result:
    """PDF/A-2b with Ghostscript (sRGB output intent)."""
    prep = _prepare(ctx)
    icc = _icc_profile()
    if icc is None:
        raise UserError("pdf.err.no_icc")
    deffile = _tmp(ctx, "PDFA_def.ps")
    deffile.write_text(_PDFA_DEF.format(icc=_ps_string(icc)), encoding="utf-8")
    produced = _tmp(ctx, "pdfa.pdf")
    args = ["-dPDFA=2", "-dPDFACompatibilityPolicy=1", "-sColorConversionStrategy=RGB",
            "-dProcessColorModel=/DeviceRGB", "-dCompatibilityLevel=1.7", f"--permit-file-read={icc}"]
    # the definition file runs after -sOutputFile and before the input document
    _run_gs(ctx, args, prep.path, produced, prep.pages, ctx.t("pdf.prog.pdfa"), prelude=[deffile])
    if prep.encrypted:
        ctx.warn(ctx.t("pdf.warn.pdfa_unprotected"))
    return _finish_pdf(ctx, prep, produced, "sfx.pdfa", reprotect=False, replace=False)


@action(id="pdf.convert.gray", label="pdf.act.grayscale", icon="filter_b_and_w", categories={"pdf"},
        mode="quick", params=[_password_param()], requires=["gs"], replace_ok=True,
        description="pdf.desc.grayscale", order=18)
def convert_gray(ctx: Context) -> Result:
    """Grayscale PDF (Ghostscript ``ColorConversionStrategy=Gray``)."""
    prep = _prepare(ctx)
    produced = _tmp(ctx, "gray.pdf")
    _run_gs(ctx, ["-dCompatibilityLevel=1.7", "-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray",
                  "-dOverrideICC=true"], prep.path, produced, prep.pages, ctx.t("pdf.prog.grayscale"))
    return _finish_pdf(ctx, prep, produced, "sfx.grayscale")


# -------------------------------------------------------------------------------- compress ----

_COMPRESS_REQ = [("gs", "py:pymupdf")]


@action(id="pdf.compress.balanced", label="pdf.act.compress.balanced", icon="compress", categories={"pdf"},
        mode="quick", params=[_password_param()], requires=_COMPRESS_REQ, replace_ok=True,
        description="pdf.desc.compress.balanced", order=20)
def compress_balanced(ctx: Context) -> Result:
    """Ghostscript /ebook (150 dpi images)."""
    return _compress(ctx, "balanced")


@action(id="pdf.compress.light", label="pdf.act.compress.light", icon="density_small", categories={"pdf"},
        mode="quick", params=[_password_param()], requires=_COMPRESS_REQ, replace_ok=True,
        description="pdf.desc.compress.light", order=21)
def compress_light(ctx: Context) -> Result:
    """Ghostscript /printer (no downsampling)."""
    return _compress(ctx, "light")


@action(id="pdf.compress.strong", label="pdf.act.compress.strong", icon="density_large", categories={"pdf"},
        mode="quick", params=[_password_param()], requires=_COMPRESS_REQ, replace_ok=True,
        description="pdf.desc.compress.strong", order=22)
def compress_strong(ctx: Context) -> Result:
    """Ghostscript /screen (72 dpi images)."""
    return _compress(ctx, "strong")


@action(id="pdf.compress.custom", label="pdf.act.compress.custom", icon="tune", categories={"pdf"},
        mode="dialog", dialog="form",
        params=[P.int("dpi", "pdf.prm.image_dpi", 120, min=36, max=600, step=1, unit="pdf.unit.dpi"),
                P.slider("quality", "pdf.prm.quality", 70, min=10, max=95, step=5, unit="pdf.unit.pct",
                         help="pdf.prm.quality.help"),
                _password_param()],
        requires=_COMPRESS_REQ, replace_ok=True, description="pdf.desc.compress.custom", order=23)
def compress_custom(ctx: Context) -> Result:
    """Downsample images to the chosen resolution and recompress them at the chosen JPEG quality."""
    return _compress(ctx, "custom")


# ----------------------------------------------------------------------------------- pages ----

_PAGES_REQ = [("py:pymupdf", "py:pypdf", "qpdf")]


@action(id="pdf.pages.organize", label="pdf.act.organize", icon="view_module", categories={"pdf"},
        multi="single", mode="dialog", dialog="pages",
        params=[P.pageops("pageops", "pdf.prm.pageops", required=True), _password_param()],
        requires=_PAGES_REQ, replace_ok=True, description="pdf.desc.organize", order=30)
def pages_organize(ctx: Context) -> Result:
    """Apply the page organizer: new order, rotations, deletions, duplicates (and blank pages)."""
    ops = ctx.p("pageops")
    if isinstance(ops, str):
        import json

        try:
            ops = json.loads(ops)
        except ValueError:
            raise UserError("pdf.err.bad_pageops", details=ops[:200]) from None
    if not isinstance(ops, list) or not ops:
        raise UserError("pdf.err.no_pages_left")

    def specs(prep: _Prep) -> list[_Spec]:
        out: list[_Spec] = []
        for op in ops:
            if isinstance(op, int):
                op = {"src": op}
            if not isinstance(op, dict):
                raise UserError("pdf.err.bad_pageops", details=repr(op)[:200])
            src = op.get("src")
            rot = _norm_rot(op.get("rotate", 0) or 0)
            if op.get("blank") or src is None or src == -1 or src == "blank":
                size = None
                if op.get("width") and op.get("height"):
                    size = (float(op["width"]), float(op["height"]))
                out.append(_Spec(None, rot, size))
                continue
            try:
                idx = int(src)
            except (TypeError, ValueError):
                raise UserError("pdf.err.bad_pageops", details=repr(op)[:200]) from None
            if not 0 <= idx < prep.pages:
                raise UserError("pdf.err.bad_pageops", details=f"page {idx + 1} > {prep.pages}")
            out.append(_Spec(idx, rot))
        return out

    return _rebuild_action(ctx, specs, "sfx.organized")


@action(id="pdf.pages.extract", label="pdf.act.extract_pages", icon="output", categories={"pdf"},
        mode="dialog", dialog="form",
        params=[P.pages("pages", "pdf.prm.pages", "", required=True, help="pdf.prm.pages.help"), _password_param()],
        requires=_PAGES_REQ, description="pdf.desc.extract_pages", order=33)
def pages_extract(ctx: Context) -> Result:
    """New PDF with the selected pages (in the given order)."""
    return _rebuild_action(ctx, lambda prep: [_Spec(p) for p in _pages_param(ctx, "pages", prep.pages)],
                           "sfx.extracted")


@action(id="pdf.pages.delete", label="pdf.act.delete_pages", icon="delete_sweep", categories={"pdf"},
        mode="dialog", dialog="form",
        params=[P.pages("pages", "pdf.prm.pages_delete", "", required=True, help="pdf.prm.pages.help"),
                _password_param()],
        requires=_PAGES_REQ, replace_ok=True, description="pdf.desc.delete_pages", order=34)
def pages_delete(ctx: Context) -> Result:
    """Copy without the selected pages."""

    def specs(prep: _Prep) -> list[_Spec]:
        drop = set(_pages_param(ctx, "pages", prep.pages))
        keep = [_Spec(p) for p in range(prep.pages) if p not in drop]
        if not keep:
            raise UserError("pdf.err.no_pages_left")
        return keep

    return _rebuild_action(ctx, specs, "sfx.edited")


@action(id="pdf.pages.reverse", label="pdf.act.reverse", icon="swap_vert", categories={"pdf"}, mode="quick",
        params=[_password_param()], requires=_PAGES_REQ, replace_ok=True, description="pdf.desc.reverse", order=35)
def pages_reverse(ctx: Context) -> Result:
    """Pages in reverse order."""
    return _rebuild_action(ctx, lambda prep: [_Spec(p) for p in range(prep.pages - 1, -1, -1)], "sfx.reversed")


@action(id="pdf.pages.blank", label="pdf.act.blank", icon="note_add", categories={"pdf"}, mode="quick",
        params=[P.select("position", "pdf.prm.position", [("end", "pdf.opt.end"), ("start", "pdf.opt.start"),
                                                          ("after", "pdf.opt.after_page")], default="end"),
                P.int("after", "pdf.prm.after", 1, min=1, max=100000, visible_if={"position": "after"}),
                P.int("count", "pdf.prm.count", 1, min=1, max=100),
                P.select("size", "pdf.prm.size", [("same", "pdf.opt.same_size"), ("a4", "A4"),
                                                  ("letter", "Letter")], default="same"),
                _password_param()],
        requires=_PAGES_REQ, replace_ok=True, description="pdf.desc.blank", order=36)
def pages_blank(ctx: Context) -> Result:
    """Insert blank pages at the start, the end or after a page."""

    def specs(prep: _Prep) -> list[_Spec]:
        n = prep.pages
        count = max(1, min(100, ctx.pint("count", 1)))
        size = {"a4": A4, "letter": LETTER}.get(ctx.pstr("size", "same"))
        pos = ctx.pstr("position", "end")
        at = 0 if pos == "start" else n if pos == "end" else max(0, min(n, ctx.pint("after", 1)))
        pages = [_Spec(p) for p in range(n)]
        return pages[:at] + [_Spec(None, 0, size) for _ in range(count)] + pages[at:]

    return _rebuild_action(ctx, specs, "sfx.edited")


def _rotate_all(ctx: Context, angle: int) -> Result:
    def specs(prep: _Prep) -> list[_Spec]:
        if ctx.pstr("pages").strip():
            chosen = set(_pages_param(ctx, "pages", prep.pages))
        else:
            chosen = set(range(prep.pages))
        return [_Spec(p, angle if p in chosen else 0) for p in range(prep.pages)]

    return _rebuild_action(ctx, specs, "sfx.rotated")


_ROT_PARAMS = [P.pages("pages", "pdf.prm.pages", "", help="pdf.prm.pages_all.help"), _password_param()]


@action(id="pdf.rotate.cw", label="pdf.act.rotate.cw", icon="rotate_right", categories={"pdf"}, mode="quick",
        params=_ROT_PARAMS, requires=_PAGES_REQ, replace_ok=True, description="pdf.desc.rotate", order=37)
def rotate_cw(ctx: Context) -> Result:
    """Rotate every page 90° clockwise."""
    return _rotate_all(ctx, 90)


@action(id="pdf.rotate.180", label="pdf.act.rotate.180", icon="u_turn_left", categories={"pdf"}, mode="quick",
        params=_ROT_PARAMS, requires=_PAGES_REQ, replace_ok=True, description="pdf.desc.rotate", order=38)
def rotate_180(ctx: Context) -> Result:
    """Rotate every page 180°."""
    return _rotate_all(ctx, 180)


@action(id="pdf.rotate.ccw", label="pdf.act.rotate.ccw", icon="rotate_left", categories={"pdf"}, mode="quick",
        params=_ROT_PARAMS, requires=_PAGES_REQ, replace_ok=True, description="pdf.desc.rotate", order=39)
def rotate_ccw(ctx: Context) -> Result:
    """Rotate every page 90° counter-clockwise."""
    return _rotate_all(ctx, 270)


# ----------------------------------------------------------------------------------- split ----

_Groups = list[tuple[str, list[int]]]


def _split_write(ctx: Context, make_groups: Callable[[_Prep, int], _Groups], *, allow_single: bool = False,
                 always_folder: bool = False) -> Result:
    """Write one PDF per ``(label, pages)`` group returned by ``make_groups(prep, total)``.

    The input is decrypted once; pieces are named ``"<stem> <label>.pdf"`` (``p001``, ``p003-004``…)
    and go into ``"<stem> (diviso)/"`` when there are more than :data:`SPLIT_FOLDER_THRESHOLD`.
    """
    f = ctx.file
    prep = _prepare(ctx)
    total = prep.pages or _page_count(ctx, prep.path)
    if total <= 0:
        raise UserError("pdf.err.no_pages")
    groups = [(label, pages) for label, pages in make_groups(prep, total) if pages]
    if not groups or (len(groups) < 2 and not allow_single):
        raise UserError("pdf.err.nothing_to_split")
    folder = None
    if always_folder or len(groups) > SPLIT_FOLDER_THRESHOLD:
        folder = ctx.output_dir(f, suffix="sfx.split")
    outputs: list[Path] = []
    for i, (label, pages) in enumerate(groups):
        ctx.check_cancel()
        ctx.progress(i / len(groups), ctx.t("pdf.prog.piece", n=i + 1, total=len(groups)))
        name = util.safe_filename(f"{f.stem} {label}.pdf")
        target = ctx.output_path(f, name=name, directory=folder) if folder else ctx.output_path(f, name=name)
        produced = _tmp(ctx, "piece.pdf")
        _assemble(ctx, [prep.path], [(0, p) for p in pages], produced)
        _deliver(ctx, prep, produced, target)
        outputs.append(target)
    msg = i18n.tn("pdf.msg.split", len(groups), ctx.lang)
    return ctx.result([folder] if folder else outputs, message=msg)


@action(id="pdf.split.pages", label="pdf.act.split.pages", icon="call_split", categories={"pdf"}, mode="quick",
        params=[_password_param()], requires=_PAGES_REQ, description="pdf.desc.split.pages", order=40)
def split_pages(ctx: Context) -> Result:
    """One PDF per page."""
    return _split_write(ctx, lambda prep, total: [(piece_label([p], total), [p]) for p in range(total)])


@action(id="pdf.split.every", label="pdf.act.split.every", icon="view_agenda", categories={"pdf"},
        mode="dialog", dialog="form",
        params=[P.int("every", "pdf.prm.every", 2, min=1, max=10000), _password_param()],
        requires=_PAGES_REQ, description="pdf.desc.split.every", order=41)
def split_every(ctx: Context) -> Result:
    """A PDF every N pages."""
    size = max(1, ctx.pint("every", 2))
    return _split_write(ctx, lambda prep, total: [(piece_label(c, total), c) for c in chunk_pages(total, size)])


@action(id="pdf.split.ranges", label="pdf.act.split.ranges", icon="segment", categories={"pdf"},
        mode="dialog", dialog="form",
        params=[P.text("ranges", "pdf.prm.ranges", "", required=True, help="pdf.prm.ranges.help"),
                _password_param()],
        requires=_PAGES_REQ, description="pdf.desc.split.ranges", order=42)
def split_ranges(ctx: Context) -> Result:
    """One PDF per range ("1-3, 5, 7-"); a single range gives one piece (an extraction)."""
    spec = ctx.pstr("ranges")

    def groups(prep: _Prep, total: int) -> _Groups:
        try:
            ranges = parse_ranges(spec, total)
        except ValueError as exc:
            raise UserError("pdf.err.bad_pages", details=str(exc), spec=spec) from None
        return [(piece_label(g, total), g) for g in ranges]

    return _split_write(ctx, groups, allow_single=True)


def _bookmark_starts(path: Path, level: int) -> list[tuple[int, str]]:
    m = _mupdf()
    entries: list[tuple[int, str]] = []
    if m is not None:
        with m.open(str(path)) as doc:
            for lvl, title, page, *_ in doc.get_toc(simple=True):
                if int(lvl) <= level and int(page) >= 1:
                    entries.append((int(page) - 1, str(title).strip()))
    else:
        pp = _pypdf()
        reader = pp.PdfReader(str(path), strict=False)

        def walk(items: list, depth: int) -> None:
            for it in items:
                if isinstance(it, list):
                    if depth < level:
                        walk(it, depth + 1)
                    continue
                try:
                    entries.append((reader.get_destination_page_number(it), str(it.title).strip()))
                except Exception:
                    continue

        walk(reader.outline, 1)
    entries.sort(key=lambda e: e[0])
    out: list[tuple[int, str]] = []
    for page, title in entries:
        if out and out[-1][0] == page:
            continue
        out.append((page, title))
    return out


@action(id="pdf.split.bookmarks", label="pdf.act.split.bookmarks", icon="bookmarks", categories={"pdf"},
        mode="quick",
        params=[P.select("level", "pdf.prm.level", [(1, "1"), (2, "2"), (3, "3")], default=1), _password_param()],
        requires=[("py:pymupdf", "py:pypdf")], description="pdf.desc.split.bookmarks", order=43)
def split_bookmarks(ctx: Context) -> Result:
    """One PDF per bookmark (chapter); pages before the first bookmark become piece "00"."""
    level = max(1, ctx.pint("level", 1))

    def groups(prep: _Prep, total: int) -> _Groups:
        starts = [(p, t) for p, t in _bookmark_starts(prep.path, level) if 0 <= p < total]
        if not starts:
            raise UserError("pdf.err.no_bookmarks")
        out: _Groups = []
        if starts[0][0] > 0:
            out.append((f"00 {piece_label(list(range(starts[0][0])), total)}", list(range(starts[0][0]))))
        for k, (page, title) in enumerate(starts):
            end = starts[k + 1][0] if k + 1 < len(starts) else total
            clean = re.sub(r"\s+", " ", title)[:80].strip() or piece_label([page], total)
            out.append((f"{k + 1:02d} {clean}", list(range(page, max(page + 1, end)))))
        return out

    return _split_write(ctx, groups)


# ----------------------------------------------------------------------------- multi files ----

def _merge_sources(ctx: Context) -> list[_Prep]:
    preps = []
    for f in ctx.files:
        ctx.check_cancel()
        prep = _prepare(ctx, f)
        if not prep.pages:
            prep.pages = _page_count(ctx, prep.path)
        preps.append(prep)
    if any(p.encrypted and p.user_pw for p in preps):
        ctx.warn(ctx.t("pdf.warn.protection_removed"))
    return preps


@action(id="pdf.merge", label="pdf.act.merge", icon="merge", categories={"pdf"}, multi="combine", mode="quick",
        params=[P.bool("bookmarks", "pdf.prm.bookmarks", True), _password_param()],
        requires=[("py:pymupdf", "py:pypdf", "qpdf")], description="pdf.desc.merge", order=5)
def merge(ctx: Context) -> Result:
    """Merge PDFs in drop order ("<first> (unito).pdf")."""
    preps = _merge_sources(ctx)
    plan: list[tuple[int, int]] = []
    outline: list[tuple[str, int]] = []
    for s, prep in enumerate(preps):
        outline.append((prep.info.stem, len(plan)))
        plan.extend((s, p) for p in range(prep.pages))
    produced = _tmp(ctx, "merged.pdf")
    _assemble(ctx, [p.path for p in preps], plan, produced,
              outline=outline if ctx.pbool("bookmarks", True) else (), keep_tocs=ctx.pbool("bookmarks", True))
    out = ctx.output_path(ctx.files[0], suffix="sfx.merged", ext="pdf")
    _deliver(ctx, None, produced, out)
    return ctx.result(out, message=i18n.tn("pdf.msg.merged", len(preps), ctx.lang))


@action(id="pdf.interleave", label="pdf.act.interleave", icon="shuffle", categories={"pdf"}, multi="combine",
        mode="quick", when=lambda files: len(files) == 2,
        params=[P.bool("reverse_second", "pdf.prm.reverse_second", True, help="pdf.prm.reverse_second.help"),
                _password_param()],
        requires=[("py:pymupdf", "py:pypdf", "qpdf")], description="pdf.desc.interleave", order=6)
def interleave(ctx: Context) -> Result:
    """Collate duplex scans: fronts from the first PDF, backs from the second."""
    preps = _merge_sources(ctx)
    a, b = preps[0].pages, preps[1].pages
    if abs(a - b) > 1:
        ctx.warn(ctx.t("pdf.warn.page_mismatch", a=a, b=b))
    plan = interleave_order(a, b, ctx.pbool("reverse_second", True))
    produced = _tmp(ctx, "duplex.pdf")
    _assemble(ctx, [p.path for p in preps], plan, produced)
    out = ctx.output_path(ctx.files[0], suffix="sfx.duplex", ext="pdf")
    _deliver(ctx, None, produced, out)
    return ctx.result(out)


def _image_page_pdf(ctx: Context, f: FileInfo) -> Any:
    """A one-page PyMuPDF document for an image / SVG (EXIF orientation applied)."""
    m = _need_mupdf()
    native = {"png", "jpg", "jpeg", "jpe", "jfif", "bmp", "gif", "tif", "tiff", "pnm", "pbm", "pgm", "ppm", "pam",
              "jxr", "jp2", "jpx", "psd", "svg"}
    fmt = (f.format or f.ext or "").lower()
    pil = _import_lib("py:PIL")
    needs_pil = fmt not in native
    orientation = 1
    if pil is not None and fmt in ("jpg", "jpeg", "jpe", "jfif", "tif", "tiff"):
        try:
            with pil.open(f.path) as im:
                orientation = int(im.getexif().get(0x0112, 1) or 1)
        except Exception:
            orientation = 1
    if needs_pil or orientation not in (1, 0):
        if pil is None:
            raise MissingTool("py:PIL")
        from PIL import ImageOps  # type: ignore

        try:
            try:
                import pillow_heif  # type: ignore

                pillow_heif.register_heif_opener()
            except Exception:
                pass
            with pil.open(f.path) as im:
                im.seek(0)
                dpi = im.info.get("dpi", (96, 96))
                im = ImageOps.exif_transpose(im)
                buf = io.BytesIO()
                has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
                if has_alpha:
                    im.convert("RGBA").save(buf, "PNG", dpi=dpi)
                else:
                    im.convert("RGB").save(buf, "JPEG", quality=92, dpi=dpi)
        except Exception as exc:
            raise UserError("pdf.err.image_unreadable", details=str(exc), name=f.name) from None
        src = m.open(stream=buf.getvalue(), filetype="png" if has_alpha else "jpeg")
    else:
        try:
            src = m.open(f.path, filetype="svg" if fmt == "svg" else None)
        except Exception as exc:
            raise UserError("pdf.err.image_unreadable", details=str(exc), name=f.name) from None
    try:
        return m.open("pdf", src.convert_to_pdf())
    finally:
        src.close()


@action(id="pdf.combine", label="pdf.act.combine", icon="picture_as_pdf", categories={"pdf", "image", "svg"},
        multi="combine", mode="quick", mixed_ok=True, when=lambda files: len({f.category for f in files}) > 1,
        params=[P.select("page_size", "pdf.prm.page_size", [("image", "pdf.opt.image_size"), ("a4", "A4"),
                                                            ("letter", "Letter")], default="image"),
                P.int("margin", "pdf.prm.margin", 10, min=0, max=100, unit="pdf.unit.mm", help="pdf.prm.margin.help"),
                _password_param()],
        requires=["py:pymupdf"], description="pdf.desc.combine", order=7)
def combine(ctx: Context) -> Result:
    """PDFs, images and SVGs into one PDF (drop order; one page per image)."""
    m = _need_mupdf()
    size_mode = ctx.pstr("page_size", "image")
    margin = max(0, ctx.pint("margin", 10)) * PT_PER_MM
    out_doc = m.open()
    total = len(ctx.files)
    skipped = 0
    toc: list[list] = []
    try:
        for i, f in enumerate(ctx.files):
            ctx.check_cancel()
            ctx.progress(i / total, ctx.t("pdf.prog.file", n=i + 1, total=total))
            start = out_doc.page_count
            if f.category == "pdf":
                prep = _prepare(ctx, f)
                with m.open(str(prep.path)) as src:
                    out_doc.insert_pdf(src)
            elif f.category in ("image", "svg"):
                try:
                    one = _image_page_pdf(ctx, f)
                except UserError as exc:
                    skipped += 1
                    ctx.warn(f"{f.name}: {exc.text(ctx.lang)}")
                    continue
                try:
                    if size_mode in ("a4", "letter"):
                        pw, ph = A4 if size_mode == "a4" else LETTER
                        r = one[0].rect
                        if r.width > r.height:
                            pw, ph = ph, pw
                        page = out_doc.new_page(width=pw, height=ph)
                        box = m.Rect(margin, margin, pw - margin, ph - margin)
                        if box.is_empty:
                            box = page.rect
                        page.show_pdf_page(box, one, 0, keep_proportion=True)
                    else:
                        out_doc.insert_pdf(one)
                finally:
                    one.close()
            else:
                skipped += 1
                continue
            toc.append([1, f.stem, start + 1])
        if out_doc.page_count == 0:
            raise UserError("pdf.err.no_pages")
        if skipped:
            ctx.warn(i18n.tn("pdf.warn.skipped_files", skipped, ctx.lang))
        try:
            out_doc.set_toc(fix_toc_levels(toc))
        except Exception:
            pass
        produced = _tmp(ctx, "combined.pdf")
        ctx.progress(0.95, ctx.t("pdf.prog.saving"))
        out_doc.save(str(produced), garbage=3, deflate=True)
    finally:
        out_doc.close()
    out = ctx.output_path(ctx.files[0], suffix="sfx.merged", ext="pdf")
    _deliver(ctx, None, produced, out)
    return ctx.result(out, message=i18n.tn("pdf.msg.merged", total - skipped, ctx.lang))


# ---------------------------------------------------------------------------------- secure ----

@action(id="pdf.secure.protect", label="pdf.act.protect", icon="lock", categories={"pdf"}, mode="dialog",
        dialog="form", when=_when_unencrypted,
        params=[P.password("user_password", "pdf.prm.user_password", "", help="pdf.prm.user_password.help"),
                P.password("user_password2", "pdf.prm.confirm_password", ""),
                P.password("owner_password", "pdf.prm.owner_password", "", help="pdf.prm.owner_password.help"),
                P.bool("allow_print", "pdf.prm.allow_print", True),
                P.bool("allow_copy", "pdf.prm.allow_copy", True),
                P.bool("allow_modify", "pdf.prm.allow_modify", True),
                P.bool("allow_annotate", "pdf.prm.allow_annotate", True)],
        requires=[("qpdf", "py:pikepdf", "py:pymupdf")], description="pdf.desc.protect", order=60)
def secure_protect(ctx: Context) -> Result:
    """AES-256 encryption with open/owner passwords and permissions."""
    f = ctx.file
    clean = lambda v: str(v or "").replace("\r", "").replace("\n", "")  # noqa: E731 (single-line fields)
    user = clean(ctx.params.get("user_password"))
    confirm = clean(ctx.params.get("user_password2"))
    owner = clean(ctx.params.get("owner_password"))
    if confirm and confirm != user:
        raise UserError("pdf.err.password_mismatch")
    perms = Perms(print=ctx.pbool("allow_print", True), print_hq=ctx.pbool("allow_print", True),
                  copy=ctx.pbool("allow_copy", True), modify=ctx.pbool("allow_modify", True),
                  assemble=ctx.pbool("allow_modify", True), annotate=ctx.pbool("allow_annotate", True),
                  form=ctx.pbool("allow_annotate", True))
    if not user and perms.all_allowed:
        raise UserError("pdf.err.password_empty")
    if _is_encrypted(f):
        raise UserError("pdf.err.already_protected", name=f.name)
    if not owner:
        owner = user if perms.all_allowed else secrets.token_urlsafe(24)
    out = _out_pdf(ctx, f, "sfx.protected")
    ctx.progress(None, ctx.t("pdf.prog.encrypting"))
    with atomic_target(out) as part:
        _encrypt_to(ctx, Path(f.path), part, user=user, owner=owner, perms=perms)
        m = _mupdf()
        if m is not None:
            with m.open(str(part)) as check:
                if user and (not check.needs_pass or not check.authenticate(user)):
                    raise UserError("pdf.err.encrypt_failed")
    return ctx.result(out, message=ctx.t("pdf.msg.protected"))


@action(id="pdf.secure.unlock", label="pdf.act.unlock", icon="lock_open", categories={"pdf"}, mode="dialog",
        dialog="form", when=_when_encrypted,
        params=[P.password("password", "pdf.prm.password", "", help="pdf.prm.unlock_password.help")],
        requires=[("py:pymupdf", "qpdf", "py:pikepdf", "py:pypdf")], description="pdf.desc.unlock", order=61)
def secure_unlock(ctx: Context) -> Result:
    """Remove the password and restrictions (the password is needed)."""
    f = ctx.file
    prep = _prepare(ctx)
    if not prep.encrypted:
        raise UserError("pdf.err.not_protected", name=f.name)
    out = _out_pdf(ctx, f, "sfx.unlocked")
    with atomic_target(out) as part:
        _move(prep.path, part) if prep.path != Path(f.path) else shutil.copyfile(f.path, part)
    return ctx.result(out, message=ctx.t("pdf.msg.unlocked"))


# --------------------------------------------------------------------------------- privacy ----

def _terms_param(ctx: Context) -> list[str]:
    raw = ctx.params.get("terms")
    if raw is None:
        return []
    if isinstance(raw, str):
        items = re.split(r"[\r\n]+", raw)
    elif isinstance(raw, (list, tuple, set)):
        items = [str(x) for x in raw]
    else:
        items = [str(raw)]
    out = []
    for it in items:
        t = it.strip()
        if t and t not in out:
            out.append(t)
    return out


#: Placeholder for a redacted term in bookmark titles, page labels and the like.
_REDACT_MARK = "█" * 5

#: A hyphen at the end of a line, where a word may continue on the next one.
_EOL_HYPHEN = "[-\u00ad\u2010\u2011]\\s*\\n\\s*"
_EOL_HYPHEN_RE = re.compile(_EOL_HYPHEN)


def _nfkc(text: Any) -> str:
    """Compatibility-normalised text: ligature characters (U+FB00 "ﬀ"…) expanded, NBSP → space."""
    return unicodedata.normalize("NFKC", str(text or ""))


def _case_variants(term: str) -> list[str]:
    norm = _nfkc(term)
    variants = [term, norm, term.lower(), term.upper(), term.capitalize(), term.title(), norm.casefold()]
    out: list[str] = []
    for v in variants:
        if v not in out:
            out.append(v)
    return out


def _word_regex(word: str) -> str:
    """Regex of one word of a term; the word may be split by a hyphen at the end of a line."""
    parts = []
    for c in word:
        parts.append(re.escape(c) + (r"\s*" if c in "-\u2010" else ""))   # "anti-" / "virus": the word's own hyphen
    return f"(?:{_EOL_HYPHEN})?".join(parts)


def _terms_pattern(terms: Sequence[str], case_sensitive: bool = False) -> "re.Pattern[str] | None":
    """Regex finding any of ``terms`` in NFKC-normalised text (see :func:`_nfkc`).

    A run of spaces in a term matches any whitespace, line breaks included; a word may be split
    by a hyphen at the end of a line; without ``case_sensitive`` the case is ignored with full
    case folding ("Straße" also matches "STRASSE").
    """
    alts: list[str] = []
    for term in terms:
        forms = [_nfkc(term)]
        if not case_sensitive:
            forms.append(forms[0].casefold())
        for form in forms:
            alt = r"\s+".join(_word_regex(w) for w in form.split())
            if alt and alt not in alts:
                alts.append(alt)
    if not alts:
        return None
    alts.sort(key=len, reverse=True)
    return re.compile("|".join(alts), 0 if case_sensitive else re.IGNORECASE)


def _term_hit(pattern: "re.Pattern[str] | None", value: Any) -> bool:
    return bool(pattern is not None and value and pattern.search(_nfkc(value)))


def _set_catalog_item(doc: Any, key: str, item: str, value: str) -> None:
    """Set ``item`` in the catalog's dictionary ``key``, which may be direct or indirect."""
    catalog = doc.pdf_catalog()
    kind, ref = doc.xref_get_key(catalog, key)
    if kind == "xref":
        doc.xref_set_key(int(ref.split()[0]), item, value)
    elif kind == "dict":
        doc.xref_set_key(catalog, f"{key}/{item}", value)


def _open_all_layers(m: Any, path: Path) -> Any:
    """Open ``path`` for reading its text, with every layer visible: the content of a layer that is
    off is not in the text pages, but it is in the file and one click away in a viewer."""
    doc = m.open(str(path))
    if not doc.get_ocgs():
        return doc
    try:
        for xref in doc.get_ocgs():
            doc.xref_set_key(xref, "Usage", "null")
            doc.xref_set_key(xref, "Intent", "null")
        _set_catalog_item(doc, "OCProperties", "D", "<</BaseState/ON>>")
        return m.open("pdf", doc.tobytes())
    finally:
        doc.close()


def _redact_textpages(m: Any, page: Any) -> list:
    """Text pages used to find and verify terms: the page's text as viewers extract it, and the
    glyphs alone, which differ when marked content carries an ``/ActualText`` replacement.

    Ligatures are expanded (the default of ``search_for``/``get_text`` keeps the U+FB00… glyphs,
    so "Raffaele" set with an "ff" ligature would be neither found nor seen by the check) and
    text outside the visible page is included too.
    """
    flags = m.TEXT_DEHYPHENATE | m.TEXT_PRESERVE_WHITESPACE
    pages = []
    for extra in (0, getattr(m, "TEXT_IGNORE_ACTUALTEXT", None)):
        if extra is None:
            continue
        try:
            pages.append(page.get_textpage(clip=m.INFINITE_RECT(), flags=flags | extra))
        except (AttributeError, TypeError):
            pages.append(page.get_textpage(flags=flags | extra))
    return pages


def _find_term(page: Any, term: str, case_sensitive: bool, textpage: Any = None) -> list:
    """Quads of ``term`` on ``page`` (one per line for a term broken across lines)."""
    hits: list = []
    seen: set = set()
    want = " ".join(_nfkc(term).split())
    variants = [term] if case_sensitive else _case_variants(term)
    if case_sensitive and _nfkc(term) != term:
        variants.append(_nfkc(term))
    for v in variants:
        try:
            found = page.search_for(v, quads=True, textpage=textpage)
        except Exception:
            found = []
        for q in found:
            key = tuple(round(c, 1) for c in q.rect)
            if key in seen:
                continue
            if case_sensitive:
                # MuPDF always ignores case: keep the hit when its text is the term, or one line of it
                try:
                    txt = " ".join(_nfkc(page.get_textbox(q.rect, textpage=textpage)).split())
                except Exception:
                    txt = ""
                if txt and want not in txt and txt not in want:
                    continue
            seen.add(key)
            hits.append(q)
    return hits


def _hyphenated_hits(m: Any, page: Any, textpage: Any, pattern: "re.Pattern[str] | None") -> list:
    """Quads (one per line) of the terms split by a hyphen at the end of a line: "Raf-" / "faele".

    MuPDF's search does not find these whatever its flags, because ``TEXT_DEHYPHENATE`` leaves
    the two lines apart.
    """
    if pattern is None or not _EOL_HYPHEN_RE.search(_nfkc(page.get_text("text", textpage=textpage))):
        return []
    chars: list[str] = []
    boxes: list = []
    for block in page.get_text("rawdict", textpage=textpage).get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    norm = _nfkc(ch["c"])
                    chars.extend(norm)
                    boxes.extend([ch["bbox"]] * len(norm))
            chars.append("\n")
            boxes.append(None)
    hits = []
    for match in pattern.finditer("".join(chars)):
        if not _EOL_HYPHEN_RE.search(match.group(0)):
            continue                                    # search_for() finds the others
        rect = None
        for box in boxes[match.start():match.end()]:
            if box is not None:
                rect = m.Rect(box) if rect is None else rect | m.Rect(box)
            elif rect is not None:
                hits.append(rect.quad)
                rect = None
        if rect is not None:
            hits.append(rect.quad)
    return hits


def _term_areas(m: Any, page: Any, terms: Sequence[str], case_sensitive: bool,
                pattern: "re.Pattern[str] | None") -> list:
    """Quads to black out for ``terms`` on ``page``, each one once."""
    areas: list = []
    seen: set = set()
    for textpage in _redact_textpages(m, page):
        found = [q for term in terms for q in _find_term(page, term, case_sensitive, textpage)]
        for q in found + _hyphenated_hits(m, page, textpage, pattern):
            key = tuple(round(c, 1) for c in q.rect)
            if key not in seen:
                seen.add(key)
                areas.append(q)
    return areas


def _outline_targets(doc: Any) -> Iterable[tuple[int, str, str]]:
    """``(xref, key, text)`` of every bookmark link target (URI, file)."""
    try:
        xrefs = doc.get_outline_xrefs()
    except Exception:
        return
    for xref in xrefs:
        for key in ("A/URI", "A/F", "A/F/F", "A/F/UF"):
            try:
                kind, value = doc.xref_get_key(xref, key)
            except Exception:
                continue
            if kind == "string" and value:
                yield xref, key, value


_PDF_ESCAPES = {"n": b"\n", "r": b"\r", "t": b"\t", "b": b"\b", "f": b"\f", "(": b"(", ")": b")", "\\": b"\\"}


def _pdf_string_at(src: str, i: int) -> tuple[bytes, int]:
    """Bytes of the PDF string token (``(literal)`` or ``<hex>``) at ``src[i]`` and the index after it."""
    if src[i] == "<":
        end = src.index(">", i)
        digits = re.sub(r"\s", "", src[i + 1:end])
        return bytes.fromhex(digits + "0" * (len(digits) % 2)), end + 1
    out = bytearray()
    depth = 0
    k = i
    while k < len(src):
        c = src[k]
        if c == "\\":
            nxt = src[k + 1:k + 2]
            octal = re.match(r"[0-7]{1,3}", src[k + 1:k + 4])
            if nxt in _PDF_ESCAPES:
                out += _PDF_ESCAPES[nxt]
                k += 2
            elif octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                k += 1 + len(octal.group(0))
            else:                                       # line continuation or unknown escape
                k += 2 if src[k + 1:k + 3] != "\r\n" else 3
            continue
        if c == "(":
            depth += 1
            if depth == 1:
                k += 1
                continue
        elif c == ")":
            depth -= 1
            if depth == 0:
                return bytes(out), k + 1
        out += c.encode("latin-1", "replace")
        k += 1
    return bytes(out), len(src)


def _pdf_text(data: bytes) -> str:
    """A PDF text string: UTF-16BE or UTF-8 with BOM, else PDFDocEncoding (≈ Latin-1)."""
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    return data.decode("latin-1")


_PDF_TOKEN_RE = re.compile(r"[(<>\[\]/]")
_PDF_NAME_RE = re.compile(r"[^\s()<>\[\]{}/%]*")


def _object_strings(src: str) -> Iterable[tuple[int, int, str, str]]:
    """``(start, end, key, text)`` of each string in the source of a PDF object; ``key`` is the
    dictionary key holding the string, or its array."""
    key = ""
    stack: list[str | None] = []        # the key of an open array, None for an open dictionary
    i = 0
    while (token := _PDF_TOKEN_RE.search(src, i)) is not None:
        i = token.start()
        c = src[i]
        if c == "/":
            name = _PDF_NAME_RE.match(src, i + 1)
            key = name.group(0)
            i = name.end()
        elif c == "[":
            stack.append(key)
            i += 1
        elif c == "]" or src.startswith(">>", i):
            if stack:
                stack.pop()
            i += 1 if c == "]" else 2
        elif src.startswith("<<", i):
            stack.append(None)
            i += 2
        elif c == ">":
            i += 1
        else:
            try:
                data, end = _pdf_string_at(src, i)
            except (ValueError, IndexError):
                i += 1
                continue
            owner = stack[-1] if stack and stack[-1] is not None else key
            yield i, end, owner, _pdf_text(data)
            i = end


#: Keys whose strings are text a person can read outside the page content.  Only these are touched:
#: other strings are data a viewer needs (/Registry and /Ordering of fonts, /DA of form fields, the
#: colour table of an /Indexed space, whose bytes can look like a short term by chance).
_TEXT_KEYS = frozenset({
    "Title", "Author", "Subject", "Keywords", "Creator", "Producer",    # bookmarks, document information
    "Alt", "ActualText", "E", "T", "TU", "TM", "V", "DV", "Opt", "CA", "AC",    # structure tree, form fields
    "Contents", "Subj", "RC", "Name", "Reason", "Location", "ContactInfo",      # annotations, layers, signatures
    "URI", "F", "UF", "Desc", "D", "Dest", "Names", "Limits",                   # link targets, named destinations
    "P",                                                                        # page label prefixes
})


def _doc_text_strings(doc: Any) -> Iterable[tuple[int, str, list[tuple[int, int, str, str]]]]:
    """``(xref, source, strings)`` of the objects having strings under :data:`_TEXT_KEYS`."""
    for xref in range(1, doc.xref_length()):
        try:
            src = doc.xref_object(xref, compressed=True)
        except Exception:
            continue
        found = [s for s in _object_strings(src) if s[2] in _TEXT_KEYS]
        if found:
            yield xref, src, found


def _redact_doc_strings(m: Any, doc: Any, pattern: "re.Pattern[str] | None") -> int:
    """Remove the terms from the text kept outside the page content: links and bookmarks pointing
    to a URI or file containing a term lose that target, every other string (bookmark title, page
    label prefix, alternate text, layer name, named destination…) gets :data:`_REDACT_MARK` instead.
    Returns the number of changed items."""
    if pattern is None:
        return 0
    changed = 0
    for xref, _key, value in list(_outline_targets(doc)):
        if _term_hit(pattern, value):
            doc.xref_set_key(xref, "A", "null")      # the bookmark stays, without its target
            changed += 1
    for page in doc:
        for link in page.get_links():
            if _term_hit(pattern, link.get("uri")) or _term_hit(pattern, link.get("file")):
                page.delete_link(link)
                changed += 1
    for xref, src, strings in list(_doc_text_strings(doc)):
        parts: list[str] = []
        pos = 0
        for start, end, _key, text in strings:
            if _term_hit(pattern, text):
                parts += [src[pos:start], m.get_pdf_str(pattern.sub(_REDACT_MARK, _nfkc(text)))]
                pos = end
                changed += 1
        if parts:
            doc.update_object(xref, "".join(parts) + src[pos:])
    return changed


def _doc_string_leaks(doc: Any, pattern: "re.Pattern[str] | None") -> list[str]:
    """Terms still present in the text kept outside the page content."""
    if pattern is None:
        return []
    return [f"object {xref} /{key}: {text}" for xref, _src, strings in _doc_text_strings(doc)
            for _start, _end, key, text in strings if _term_hit(pattern, text)]


def _box_rect(m: Any, page: Any, box: dict) -> Any:
    vis = page.rect
    try:
        x, y, w, h = (float(box.get(k, 0)) for k in ("x", "y", "w", "h"))
    except (TypeError, ValueError):
        raise UserError("pdf.err.bad_boxes", details=repr(box)[:200]) from None
    if max(x, y, w, h) > 1.5:      # absolute points given instead of 0..1
        r = m.Rect(x, y, x + w, y + h)
    else:
        r = m.Rect(vis.x0 + x * vis.width, vis.y0 + y * vis.height, vis.x0 + (x + w) * vis.width,
                   vis.y0 + (y + h) * vis.height)
    r = r * page.derotation_matrix
    r.normalize()
    return r


@action(id="pdf.privacy.redact", label="pdf.act.redact", icon="ink_eraser", categories={"pdf"}, multi="single",
        mode="dialog", dialog="redact", params=[P.boxes("boxes", "pdf.prm.boxes"), _password_param()],
        requires=["py:pymupdf"], description="pdf.desc.redact", order=70)
def privacy_redact(ctx: Context) -> Result:
    """True redaction: text, images and vector graphics under the areas are removed, then verified."""
    m = _need_mupdf()
    boxes = ctx.p("boxes") or []
    if isinstance(boxes, str):
        import json

        try:
            boxes = json.loads(boxes)
        except ValueError:
            raise UserError("pdf.err.bad_boxes", details=boxes[:200]) from None
    if isinstance(boxes, dict):
        boxes = [boxes]
    terms = _terms_param(ctx)
    case_sensitive = ctx.pbool("case_sensitive", False)
    if not boxes and not terms:
        raise UserError("pdf.err.nothing_to_redact")
    prep = _prepare(ctx)
    produced = _tmp(ctx, "redacted.pdf")
    areas: dict[int, list] = {}
    count = 0
    styles = set()
    with m.open(str(prep.path)) as doc:
        n = doc.page_count
        for b in boxes:
            if not isinstance(b, dict):
                raise UserError("pdf.err.bad_boxes", details=repr(b)[:200])
            styles.add(str(b.get("style") or "solid"))
            pages = range(n) if b.get("page") is None else [int(b["page"])]
            for pno in pages:
                if not 0 <= pno < n:
                    raise UserError("pdf.err.bad_boxes", details=f"page {pno + 1} > {n}")
                page = doc[pno]
                r = _box_rect(m, page, b)
                if r.is_empty:
                    continue
                page.add_redact_annot(r, fill=(0, 0, 0))
                areas.setdefault(pno, []).append(r)
                count += 1
        pattern = _terms_pattern(terms, case_sensitive)
        found_terms = 0
        if terms:
            with _open_all_layers(m, prep.path) as visible:
                for pno in range(n):
                    ctx.check_cancel()
                    ctx.progress(0.6 * pno / max(1, n), ctx.t("pdf.prog.page", n=pno + 1, total=n))
                    page = doc[pno]
                    for q in _term_areas(m, visible[pno], terms, case_sensitive, pattern):
                        page.add_redact_annot(q, fill=(0, 0, 0))
                        areas.setdefault(pno, []).append(q.rect)
                        count += 1
                        found_terms += 1
        # annotations and form fields over redacted areas (or containing the terms) go too
        outside = 0
        for pno in range(n):
            ctx.check_cancel()
            page = doc[pno]
            rects = areas.get(pno, [])
            for annot in list(page.annots() or []):
                if annot.type[0] == m.PDF_ANNOT_REDACT:
                    continue
                named = any(_term_hit(pattern, v) for v in (annot.info or {}).values())
                if named or any(annot.rect.intersects(r) for r in rects):
                    page.delete_annot(annot)
                    outside += named
            for widget in list(page.widgets() or []):
                named = any(_term_hit(pattern, v) for v in (widget.field_value, widget.field_name,
                                                            widget.field_label))
                if named or any(widget.rect.intersects(r) for r in rects):
                    page.delete_widget(widget)
                    outside += named
        # bookmarks, link targets, page labels and the like are not page text: the terms go there too
        outside += _redact_doc_strings(m, doc, pattern)
        count += outside
        if terms and not found_terms and not outside and not boxes:
            raise UserError("pdf.err.terms_not_found")
        if styles - {"solid"}:
            ctx.warn(ctx.t("pdf.warn.redact_solid"))
        for pno in range(n):
            ctx.check_cancel()
            page = doc[pno]
            rects = areas.get(pno, [])
            if rects:
                try:
                    page.apply_redactions(images=m.PDF_REDACT_IMAGE_PIXELS,
                                          graphics=m.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                          text=m.PDF_REDACT_TEXT_REMOVE)
                except TypeError:
                    page.apply_redactions(images=m.PDF_REDACT_IMAGE_PIXELS)
        ctx.progress(0.75, ctx.t("pdf.prog.cleaning"))
        _set_catalog_item(doc, "AcroForm", "XFA", "null")   # XFA repeats the form's data in XML no check can read
        try:
            doc.scrub(attached_files=True, clean_pages=True, embedded_files=True, hidden_text=False,
                      javascript=True, metadata=True, redactions=True, redact_images=0, remove_links=False,
                      reset_fields=False, reset_responses=True, thumbnails=True, xml_metadata=True)
        except Exception:
            doc.set_metadata({})
            doc.del_xml_metadata()
        doc.save(str(produced), garbage=4, deflate=True, clean=True)
    # verification pass on the written file
    ctx.progress(0.9, ctx.t("pdf.prog.verifying"))
    leaks: list[str] = []
    with _open_all_layers(m, produced) as check:
        for pno in range(check.page_count):
            page = check[pno]
            if pattern is not None:
                for textpage in _redact_textpages(m, page):
                    found = pattern.search(_nfkc(page.get_text("text", textpage=textpage)))
                    if found:
                        leaks.append(f"p{pno + 1}: {' '.join(found.group(0).split())}")
                        break
            for r in areas.get(pno, []):
                inner = m.Rect(r)
                inner.x0 += 0.75
                inner.y0 += 0.75
                inner.x1 -= 0.75
                inner.y1 -= 0.75
                if inner.is_empty:
                    continue
                if page.get_text("text", clip=inner).strip():
                    leaks.append(f"p{pno + 1}: {tuple(round(c) for c in r)}")
        leaks += _doc_string_leaks(check, pattern)
    if leaks:
        raise UserError("pdf.err.redact_verify", details="\n".join(leaks[:50]))
    return _finish_pdf(ctx, prep, produced, "sfx.redacted", replace=False,
                       message=i18n.tn("pdf.msg.redacted", count, ctx.lang))


def _strip_metadata_mupdf(src: Path, dst: Path) -> None:
    m = _need_mupdf()
    with m.open(str(src)) as doc:
        doc.set_metadata({})
        doc.del_xml_metadata()
        cat = doc.pdf_catalog()
        for key in ("Info", "PieceInfo", "Metadata"):
            try:
                if doc.xref_get_key(cat, key)[0] != "null":
                    doc.xref_set_key(cat, key, "null")
            except Exception:
                pass
        for pno in range(doc.page_count):
            xref = doc[pno].xref
            for key in ("PieceInfo", "Metadata"):
                try:
                    if doc.xref_get_key(xref, key)[0] != "null":
                        doc.xref_set_key(xref, key, "null")
                except Exception:
                    pass
        doc.save(str(dst), garbage=4, deflate=True, clean=True)


@action(id="pdf.privacy.strip", label="pdf.act.strip_metadata", icon="visibility_off", categories={"pdf"},
        mode="quick", params=[_password_param()], requires=[("py:pymupdf", "qpdf", "py:pikepdf")],
        replace_ok=True, description="pdf.desc.strip_metadata", order=71)
def privacy_strip(ctx: Context) -> Result:
    """Remove document info and XMP with a full rewrite (no incremental history survives)."""
    prep = _prepare(ctx)
    produced = _tmp(ctx, "clean.pdf")
    ctx.progress(None, ctx.t("pdf.prog.cleaning"))
    if _have("py:pymupdf"):
        _strip_metadata_mupdf(prep.path, produced)
    elif _have("qpdf"):
        ctx.run(["qpdf", "--remove-info", "--remove-metadata", str(prep.path), str(produced)], ok_codes=(0, 3))
    else:
        pk = _pikepdf()
        with pk.open(str(prep.path)) as pdf:
            try:
                del pdf.trailer.Info
            except (AttributeError, KeyError):
                pass
            try:
                del pdf.Root.Metadata
            except (AttributeError, KeyError):
                pass
            pdf.save(str(produced), fix_metadata_version=False)
    return _finish_pdf(ctx, prep, produced, "sfx.no_metadata", message=ctx.t("pdf.msg.metadata_removed"))


def _metadata_changes(raw: Any) -> tuple[dict[str, str], set[str], bool]:
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            raise UserError("pdf.err.bad_metadata", details=raw[:200]) from None
    if not isinstance(raw, dict):
        raise UserError("pdf.err.bad_metadata", details=repr(raw)[:200])
    sets: dict[str, str] = {}
    for key, value in (raw.get("set") or {}).items():
        norm = normalize_metadata_key(key)
        if norm is None or norm == "trapped":
            continue
        text = "" if value is None else str(value)
        if norm in ("creationDate", "modDate") and text.strip():
            try:
                text = pdf_date(text)
            except ValueError as exc:
                raise UserError("pdf.err.bad_date", details=str(exc), value=text) from None
        sets[norm] = text
    removes = {k for k in (normalize_metadata_key(x) for x in (raw.get("remove") or [])) if k}
    return sets, removes, bool(raw.get("remove_all"))


@action(id="pdf.privacy.metadata", label="pdf.act.metadata", icon="edit_note", categories={"pdf"},
        multi="single", mode="dialog", dialog="metadata",
        params=[P.metadata("metadata", "pdf.prm.metadata"), _password_param()],
        requires=[("py:pymupdf", "py:pypdf")], replace_ok=True, description="pdf.desc.metadata", order=72)
def privacy_metadata(ctx: Context) -> Result:
    """Edit title, author, subject, keywords, creator, producer and dates (full rewrite)."""
    sets, removes, remove_all = _metadata_changes(ctx.p("metadata") or {})
    if not sets and not removes and not remove_all:
        raise UserError("pdf.err.nothing_to_change")
    prep = _prepare(ctx)
    produced = _tmp(ctx, "metadata.pdf")
    if _have("py:pymupdf"):
        m = _need_mupdf()
        with m.open(str(prep.path)) as doc:
            meta = {} if remove_all else {k: v for k, v in (doc.metadata or {}).items() if k in META_KEYS and v}
            for k in removes:
                meta.pop(k, None)
            meta.update({k: v for k, v in sets.items()})
            # set_metadata() leaves keys that are not given unchanged: pass None to delete them
            meta = {k: (meta.get(k) or None) for k in META_KEYS}
            xmp = ""
            try:
                xmp = doc.get_xml_metadata() or ""
            except Exception:
                xmp = ""
            if xmp and (remove_all or sets or removes):
                if "pdfaid" in xmp:
                    ctx.warn(ctx.t("pdf.warn.pdfa_lost"))
                doc.del_xml_metadata()
            doc.set_metadata(meta)
            doc.save(str(produced), garbage=4, deflate=True)
    else:
        pp = _pypdf()
        reader = pp.PdfReader(str(prep.path), strict=False)
        writer = pp.PdfWriter()
        writer.append(reader)
        names = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords",
                 "creator": "/Creator", "producer": "/Producer", "creationDate": "/CreationDate",
                 "modDate": "/ModDate"}
        current = {} if remove_all else {k: str(v) for k, v in (reader.metadata or {}).items() if isinstance(k, str)}
        for k in removes:
            current.pop(names[k], None)
        for k, v in sets.items():
            if v:
                current[names[k]] = v
            else:
                current.pop(names[k], None)
        writer.metadata = None
        writer.add_metadata(current)
        with open(produced, "wb") as fh:
            writer.write(fh)
    return _finish_pdf(ctx, prep, produced, "sfx.edited", message=ctx.t("pdf.msg.metadata_saved"))


# ------------------------------------------------------------------------------------- OCR ----

def _ocr_lang(ctx: Context) -> str:
    lang = re.sub(r"[^A-Za-z_+]", "", ctx.pstr("language") or str(cfgmod.get(ctx.config, "ocr.languages", "ita+eng")))
    return lang.strip("+") or "eng"


def _check_ocr_langs(ctx: Context, lang: str) -> None:
    if not _have("tesseract"):
        return
    res = util.run_quiet(["tesseract", "--list-langs"], timeout=20)
    if res is None or res.returncode != 0:
        return
    text = (res.stdout or "") + "\n" + (res.stderr or "")
    lines = [ln.strip() for ln in text.splitlines()]
    available = {ln for ln in lines if ln and not ln.lower().startswith("list of") and " " not in ln}
    if not available:
        return
    missing = [code for code in lang.split("+") if code and code not in available]
    if missing:
        install = "sudo pacman -S --needed " + " ".join(f"tesseract-data-{c.lower()}" for c in missing)
        raise UserError("pdf.err.ocr_lang_missing", install=install, langs=", ".join(missing))


def _signed(path: Path) -> bool:
    m = _mupdf()
    if m is None:
        return False
    try:
        with m.open(str(path)) as doc:
            return int(doc.get_sigflags()) > 0
    except Exception:
        return False


def _ocr_page_textlayer(ctx: Context, page: Any, lang: str, dpi: int = 300) -> Any:
    """Tesseract text-only PDF for one page (visible orientation) as a PyMuPDF document."""
    m = _need_mupdf()
    img = _tmp(ctx, "ocr.png")
    use = _render_dpi(page.rect.width, page.rect.height, dpi)
    page.get_pixmap(dpi=use, alpha=False).save(str(img))
    base = _tmp(ctx, "ocr")
    ctx.run(["tesseract", str(img), str(base), "-l", lang, "--dpi", str(use), "-c", "textonly_pdf=1", "pdf"],
            timeout=900)
    pdf = base.with_name(base.name + ".pdf")
    if not pdf.exists():
        raise ToolError("pdf.err.ocr_failed", details="tesseract wrote no PDF")
    return m.open(str(pdf))


def _ocr_tesseract(ctx: Context, src: Path, dst: Path, lang: str, mode: str) -> None:
    m = _need_mupdf()
    if mode != "skip":
        ctx.warn(ctx.t("pdf.warn.ocr_fallback_mode"))
    with m.open(str(src)) as doc:
        n = doc.page_count
        done = 0
        for pno in range(n):
            ctx.check_cancel()
            ctx.progress(pno / max(1, n), ctx.t("pdf.prog.ocr_page", n=pno + 1, total=n))
            page = doc[pno]
            if mode == "skip" and page.get_text("text").strip():
                continue
            layer = _ocr_page_textlayer(ctx, page, lang)
            try:
                page.show_pdf_page(page.rect * page.derotation_matrix, layer, 0, rotate=page.rotation, overlay=True)
            finally:
                layer.close()
            done += 1
        ctx.progress(0.97, ctx.t("pdf.prog.saving"))
        doc.save(str(dst), garbage=3, deflate=True)


_OCRMYPDF_ERRORS = {"2": "pdf.err.ocr_input", "3": "pdf.err.ocr_dependency", "6": "pdf.err.has_text",
                    "8": "pdf.err.password_needed"}


@action(id="pdf.ocr.searchable", label="pdf.act.ocr", icon="document_scanner", categories={"pdf"}, mode="quick",
        params=[P.select("mode", "pdf.prm.ocr_mode", [("skip", "pdf.opt.ocr.skip"), ("redo", "pdf.opt.ocr.redo"),
                                                      ("force", "pdf.opt.ocr.force")], default="skip"),
                P.text("language", "pdf.prm.language", "cfg:ocr.languages", help="pdf.prm.language.help"),
                P.bool("deskew", "pdf.prm.deskew", False, help="pdf.prm.deskew.help"),
                _password_param()],
        requires=[("ocrmypdf", "tesseract")], replace_ok=True, description="pdf.desc.ocr", order=80)
def ocr_searchable(ctx: Context) -> Result:
    """Searchable PDF (ocrmypdf; tesseract text layer over the original pages as fallback)."""
    prep = _prepare(ctx)
    lang = _ocr_lang(ctx)
    mode = ctx.pstr("mode", "skip")
    if mode not in ("skip", "redo", "force"):
        mode = "skip"
    _check_ocr_langs(ctx, lang)
    produced = _tmp(ctx, "ocr.pdf")
    if _have("ocrmypdf"):
        args = ["ocrmypdf", "-l", lang, "--output-type", "pdf",
                {"skip": "--skip-text", "redo": "--redo-ocr", "force": "--force-ocr"}[mode],
                "--jobs", str(max(1, (os.cpu_count() or 2) // 2))]
        if ctx.pbool("deskew") and mode != "redo":
            args.append("--deskew")
        if _signed(prep.path):
            args.append("--invalidate-digital-signatures")
            ctx.warn(ctx.t("pdf.warn.signature_invalidated"))
        ctx.progress(None, ctx.t("pdf.prog.ocr"))
        try:
            ctx.run([*args, "--", str(prep.path), str(produced)], timeout=None)
        except ToolError as exc:
            key = _OCRMYPDF_ERRORS.get(str(exc.fmt.get("code")))
            if key:
                raise UserError(key, details=exc.details, name=prep.info.name) from None
            raise
    else:
        if not _have("py:pymupdf"):
            raise MissingTool(["py:pymupdf"])
        ctx.warn(ctx.t("pdf.warn.ocr_fallback"))
        _ocr_tesseract(ctx, prep.path, produced, lang, mode)
    return _finish_pdf(ctx, prep, produced, "sfx.ocr", message=ctx.t("pdf.msg.ocr_done"))


def _ocr_text(ctx: Context, path: Path, lang: str) -> str:
    """OCR every page with tesseract and return the text."""
    if not _have("tesseract"):
        raise MissingTool("tesseract")
    _check_ocr_langs(ctx, lang)
    texts = []
    m = _mupdf()
    total = _page_count(ctx, path)
    doc = m.open(str(path)) if m is not None else None
    try:
        for pno in range(total):
            ctx.check_cancel()
            ctx.progress(pno / max(1, total), ctx.t("pdf.prog.ocr_page", n=pno + 1, total=total))
            img = _tmp(ctx, "ocr.png")
            if doc is not None:
                page = doc[pno]
                use = _render_dpi(page.rect.width, page.rect.height, 300)
                page.get_pixmap(dpi=use, alpha=False).save(str(img))
            else:
                _pdftoppm_page(ctx, path, pno, 300, img, "png", 90)
                use = 300
            res = ctx.run(["tesseract", str(img), "stdout", "-l", lang, "--dpi", str(use)], timeout=900)
            texts.append(res.stdout.strip())
    finally:
        if doc is not None:
            doc.close()
    return "\n\n".join(t for t in texts if t)


@action(id="pdf.ocr.copy_text", label="pdf.act.copy_text", icon="content_copy", categories={"pdf"}, multi="any",
        mode="quick", undoable=False,
        params=[P.bool("force_ocr", "pdf.prm.force_ocr", False, help="pdf.prm.force_ocr.help"),
                P.text("language", "pdf.prm.language", "cfg:ocr.languages", help="pdf.prm.language.help"),
                _password_param()],
        requires=[("py:pymupdf", "pdftotext")], description="pdf.desc.copy_text", order=81)
def ocr_copy_text(ctx: Context) -> Result:
    """Text of the PDF(s) to the clipboard (OCR when there is no text layer)."""
    parts = []
    lang = _ocr_lang(ctx)
    for f in ctx.files:
        ctx.check_cancel()
        prep = _prepare(ctx, f)
        force = ctx.pbool("force_ocr")
        text = "" if force else _extract_text(ctx, prep.path, layout=False).strip()
        if not text:
            if _have("tesseract"):
                text = _ocr_text(ctx, prep.path, lang).strip()
            elif force or len(ctx.files) == 1:
                # a scan without a text layer: OCR is the only way to read it
                raise MissingTool("tesseract", details=ctx.t("pdf.err.no_text"))
            else:
                ctx.warn(ctx.t("pdf.warn.no_text_in", name=f.name))
        if text:
            parts.append(f"{f.name}\n\n{text}" if len(ctx.files) > 1 else text)
    if not parts:
        raise UserError("pdf.err.no_text")
    full = "\n\n\n".join(parts)
    copied = util.copy_text(full)
    return ctx.result(kind="text", text=full, message=ctx.t("pdf.msg.copied" if copied else "pdf.msg.text_ready"))


# -------------------------------------------------------------------------------- extract ----

def _extract_images_mupdf(ctx: Context, src: Path, folder: Path) -> int:
    m = _need_mupdf()
    count = 0
    seen: set[int] = set()
    with m.open(str(src)) as doc:
        n = doc.page_count
        for pno in range(n):
            ctx.check_cancel()
            ctx.progress(pno / max(1, n), ctx.t("pdf.prog.page", n=pno + 1, total=n))
            k = 0
            for info in doc.get_page_images(pno, full=True):
                xref, smask = int(info[0]), int(info[1])
                if xref in seen or xref <= 0:
                    continue
                seen.add(xref)
                try:
                    data: bytes
                    if smask > 0:
                        base = m.Pixmap(doc, xref)
                        if base.alpha:
                            base = m.Pixmap(base, 0)
                        if base.colorspace and base.colorspace.n > 3:
                            base = m.Pixmap(m.csRGB, base)
                        pix = m.Pixmap(base, m.Pixmap(doc, smask))
                        data, ext = pix.tobytes("png"), "png"
                    else:
                        ei = doc.extract_image(xref)
                        if not ei or not ei.get("image"):
                            continue
                        if int(ei.get("width", 0)) <= 2 and int(ei.get("height", 0)) <= 2:
                            continue
                        data, ext = ei["image"], str(ei.get("ext") or "png").lower()
                        if ext not in ("png", "jpeg", "jpg", "jpx", "jp2", "tiff", "tif", "bmp", "gif", "jxr"):
                            pix = m.Pixmap(doc, xref)
                            if pix.colorspace and pix.colorspace.n > 3:
                                pix = m.Pixmap(m.csRGB, pix)
                            data, ext = pix.tobytes("png"), "png"
                except Exception:
                    log.debug("image %s not extracted", xref, exc_info=True)
                    continue
                k += 1
                ext = "jpg" if ext == "jpeg" else ext
                target = folder / f"page-{pno + 1:03d}-{k:02d}.{ext}"
                with atomic_target(target) as part:
                    part.write_bytes(data)
                count += 1
    return count


@action(id="pdf.extract.images", label="pdf.act.extract_images", icon="image", categories={"pdf"}, mode="quick",
        params=[_password_param()], requires=[("py:pymupdf", "pdfimages")], description="pdf.desc.extract_images",
        order=90)
def extract_images(ctx: Context) -> Result:
    """Embedded images in their original format, into "<stem> (immagini)"."""
    f = ctx.file
    prep = _prepare(ctx)
    folder = ctx.output_dir(f, suffix="sfx.images")
    if _have("py:pymupdf"):
        count = _extract_images_mupdf(ctx, prep.path, folder)
    else:
        ctx.progress(None, ctx.t("pdf.prog.images"))
        ctx.run(["pdfimages", "-all", "-p", str(prep.path), str(folder / "image")], timeout=None)
        count = sum(1 for p in folder.iterdir() if p.is_file())
    if count == 0:
        raise UserError("pdf.err.no_images")
    return ctx.result([folder], message=i18n.tn("pdf.msg.images_extracted", count, ctx.lang))


def _attachments(ctx: Context, src: Path) -> list[tuple[str, bytes]]:
    items: list[tuple[str, bytes]] = []
    m = _mupdf()
    if m is not None:
        with m.open(str(src)) as doc:
            for name in doc.embfile_names():
                try:
                    info = doc.embfile_info(name)
                    items.append((info.get("ufilename") or info.get("filename") or name, doc.embfile_get(name)))
                except Exception:
                    continue
            for page in doc:
                for annot in page.annots() or []:
                    try:
                        if annot.type[0] == m.PDF_ANNOT_FILE_ATTACHMENT:
                            fi = annot.file_info
                            items.append((fi.get("filename") or "attachment", annot.get_file()))
                    except Exception:
                        continue
        return items
    pp = _pypdf()
    if pp is not None:
        reader = pp.PdfReader(str(src), strict=False)
        for name, contents in (reader.attachments or {}).items():
            for data in contents:
                items.append((name, data))
        return items
    raise MissingTool(["py:pymupdf"])


@action(id="pdf.extract.attachments", label="pdf.act.extract_attachments", icon="attach_file", categories={"pdf"},
        mode="quick", params=[_password_param()], requires=[("py:pymupdf", "pdfdetach", "py:pypdf")],
        description="pdf.desc.extract_attachments", order=91)
def extract_attachments(ctx: Context) -> Result:
    """Files embedded in the PDF (e.g. the XML of an e-invoice)."""
    f = ctx.file
    prep = _prepare(ctx)
    ctx.progress(None, ctx.t("pdf.prog.working"))
    if not _have("py:pymupdf") and not _have("py:pypdf"):
        folder = ctx.output_dir(f, suffix="sfx.attachments")
        ctx.run(["pdfdetach", "-saveall", "-o", str(folder), str(prep.path)], timeout=600)
        count = sum(1 for p in folder.iterdir() if p.is_file())
        if not count:
            raise UserError("pdf.err.no_attachments")
        return ctx.result([folder], message=i18n.tn("pdf.msg.attachments_extracted", count, ctx.lang))
    items = _attachments(ctx, prep.path)
    if not items:
        raise UserError("pdf.err.no_attachments")
    if len(items) == 1:
        name, data = items[0]
        out = ctx.output_path(f, name=util.safe_filename(os.path.basename(name.replace("\\", "/")) or "attachment"))
        with atomic_target(out) as part:
            part.write_bytes(data)
        return ctx.result(out, message=i18n.tn("pdf.msg.attachments_extracted", 1, ctx.lang))
    folder = ctx.output_dir(f, suffix="sfx.attachments")
    for name, data in items:
        base = util.safe_filename(os.path.basename(name.replace("\\", "/")) or "attachment")
        target = util.unique_path(folder / base)
        with atomic_target(target) as part:
            part.write_bytes(data)
    return ctx.result([folder], message=i18n.tn("pdf.msg.attachments_extracted", len(items), ctx.lang))


# ------------------------------------------------------------------------------------ more ----

def _default_wm_text(files: list, config: dict) -> str:
    return i18n.t("pdf.watermark.default", i18n.detect_lang(config))


_SYSTEM_FONT: dict[str, str | None] = {}


def _system_sans_file() -> str | None:
    """A Unicode sans-serif TrueType/OpenType file from fontconfig (cached; ``None`` when unavailable)."""
    if "sans" in _SYSTEM_FONT:
        return _SYSTEM_FONT["sans"]
    path: str | None = None
    if tools.have("fc-match"):
        res = util.run_quiet(["fc-match", "-f", "%{file}", "sans-serif:lang=it"], timeout=10)
        cand = (res.stdout or "").strip() if res is not None and res.returncode == 0 else ""
        if cand and os.path.isfile(cand) and cand.lower().endswith((".ttf", ".otf")):
            path = cand
    _SYSTEM_FONT["sans"] = path
    return path


def _font_for(m: Any, text: str) -> tuple[str, Any]:
    """Font for stamped text: Helvetica (not embedded) for plain ASCII, otherwise a Unicode font
    (the system sans-serif when it covers ``text``, else PyMuPDF's built-in Droid Sans Fallback).

    Base-14 Helvetica is written with a simple encoding, so accented letters or "€" would not be
    extractable (copy/paste gives "�"); embedded Unicode fonts keep the text searchable.
    """
    helv = m.Font("helv")
    if all(32 <= ord(c) < 127 or c.isspace() for c in text):
        return "helv", helv
    sysfile = _system_sans_file()
    if sysfile:
        try:
            font = m.Font(fontfile=sysfile)
            if all(c.isspace() or font.has_glyph(ord(c)) for c in text):
                return "dwsans", font
        except Exception:
            log.debug("system font %s unusable", sysfile, exc_info=True)
    try:
        return "dwuni", m.Font("cjk")
    except Exception:
        return "helv", helv


def _stamp(m: Any, target: Any, stamp_doc: Any, pno: int, overlay: bool = True) -> None:
    """Show stamp page ``pno`` (sized like the visible target page) upright on ``target``."""
    target.show_pdf_page(target.rect * target.derotation_matrix, stamp_doc, pno, rotate=target.rotation,
                         overlay=overlay)


def _place(position: str, w: float, h: float, tw: float, fs: float, margin: float) -> tuple[float, float]:
    """Centre point of a text box of width ``tw`` and size ``fs`` for a named position."""
    horiz, vert = "center", "center"
    if position in ("top", "bottom"):
        vert = position
    elif "-" in position:
        vert, horiz = position.split("-", 1)
    elif position in ("left", "right"):
        horiz = position
    cx = {"left": margin + tw / 2, "right": w - margin - tw / 2}.get(horiz, w / 2)
    cy = {"top": margin + fs / 2, "bottom": h - margin - fs / 2}.get(vert, h / 2)
    return cx, cy


@action(id="pdf.watermark", label="pdf.act.watermark", icon="branding_watermark", categories={"pdf"},
        mode="dialog", dialog="form",
        params=[P.text("text", "pdf.prm.wm_text", _default_wm_text, required=True),
                P.slider("opacity", "pdf.prm.opacity", 20, min=5, max=100, step=5, unit="pdf.unit.pct"),
                P.select("rotation", "pdf.prm.rotation", [(45, "45°"), (0, "0°"), (90, "90°"), (-45, "-45°")],
                         default=45),
                P.select("position", "pdf.prm.position",
                         [("center", "pdf.opt.center"), ("top", "pdf.opt.top"), ("bottom", "pdf.opt.bottom"),
                          ("top-left", "pdf.opt.top_left"), ("top-right", "pdf.opt.top_right"),
                          ("bottom-left", "pdf.opt.bottom_left"), ("bottom-right", "pdf.opt.bottom_right")],
                         default="center"),
                P.int("font_size", "pdf.prm.font_size", 0, min=0, max=400, unit="pdf.unit.pt",
                      help="pdf.prm.font_size.help"),
                P.color("color", "pdf.prm.color", "#808080"),
                P.select("layer", "pdf.prm.layer", [("over", "pdf.opt.over"), ("under", "pdf.opt.under")],
                         default="over"),
                P.pages("pages", "pdf.prm.pages", "", help="pdf.prm.pages_all.help"),
                _password_param()],
        requires=["py:pymupdf"], replace_ok=True, description="pdf.desc.watermark", order=100)
def watermark(ctx: Context) -> Result:
    """Text stamp (opacity, rotation, position, pages; over or under the content)."""
    m = _need_mupdf()
    text = ctx.pstr("text").strip()
    if not text:
        raise UserError("err.missing_param", param=ctx.t("pdf.prm.wm_text"))
    opacity = max(0.05, min(1.0, ctx.pfloat("opacity", 20) / 100.0))
    angle = ctx.pint("rotation", 45)
    position = ctx.pstr("position", "center")
    color = _rgb(ctx.pstr("color"), (0.5, 0.5, 0.5))
    overlay = ctx.pstr("layer", "over") != "under"
    prep = _prepare(ctx)
    produced = _tmp(ctx, "watermark.pdf")
    fontname, font = _font_for(m, text)
    unit_len = max(1e-3, font.text_length(text, fontsize=1))
    stamps = m.open()
    cache: dict[tuple[int, int], int] = {}
    try:
        with m.open(str(prep.path)) as doc:
            n = doc.page_count
            chosen = sorted(set(_pages_param(ctx, "pages", n, empty_means_all=True)))
            # pass 1: one stamp page per distinct page size (the stamp document must be complete
            # before it is shown on the target pages: PyMuPDF caches its object map)
            for pno in chosen:
                rect = doc[pno].rect
                w, h = rect.width, rect.height
                key = (round(w), round(h))
                if key in cache:
                    continue
                sp = stamps.new_page(width=w, height=h)
                rad = math.radians(abs(angle))
                avail = w if angle == 0 else h if abs(angle) == 90 else min(w / max(1e-3, math.cos(rad)),
                                                                          h / max(1e-3, math.sin(rad)))
                fs = ctx.pint("font_size", 0) or max(8.0, min(0.75 * avail / unit_len, 0.3 * min(w, h)))
                tw = unit_len * fs
                margin = max(18.0, fs * 0.6)
                cx, cy = _place(position, w, h, tw if angle == 0 else fs, fs, margin)
                if fontname != "helv":
                    sp.insert_font(fontname=fontname, fontbuffer=font.buffer)
                sp.insert_text(m.Point(cx - tw / 2, cy + fs * 0.35), text, fontsize=fs, fontname=fontname,
                               color=color, fill_opacity=opacity, morph=(m.Point(cx, cy), m.Matrix(angle)))
                cache[key] = sp.number
            # pass 2: stamp
            for k, pno in enumerate(chosen):
                ctx.check_cancel()
                ctx.progress(0.9 * k / max(1, len(chosen)), ctx.t("pdf.prog.page", n=pno + 1, total=n))
                page = doc[pno]
                _stamp(m, page, stamps, cache[(round(page.rect.width), round(page.rect.height))], overlay=overlay)
            ctx.progress(0.95, ctx.t("pdf.prog.saving"))
            doc.save(str(produced), garbage=3, deflate=True)
    finally:
        stamps.close()
    return _finish_pdf(ctx, prep, produced, "sfx.watermarked")


@action(id="pdf.page_numbers", label="pdf.act.page_numbers", icon="format_list_numbered", categories={"pdf"},
        mode="dialog", dialog="form",
        params=[P.select("position", "pdf.prm.position",
                         [("bottom-center", "pdf.opt.bottom"), ("bottom-right", "pdf.opt.bottom_right"),
                          ("bottom-left", "pdf.opt.bottom_left"), ("top-center", "pdf.opt.top"),
                          ("top-right", "pdf.opt.top_right"), ("top-left", "pdf.opt.top_left")],
                         default="bottom-center"),
                P.text("format", "pdf.prm.number_format", "{n}/{total}", help="pdf.prm.number_format.help"),
                P.int("start", "pdf.prm.start", 1, min=0, max=100000),
                P.int("font_size", "pdf.prm.font_size", 10, min=4, max=72, unit="pdf.unit.pt"),
                P.bool("skip_first", "pdf.prm.skip_first", False),
                P.color("color", "pdf.prm.color", "#000000"),
                _password_param()],
        requires=["py:pymupdf"], replace_ok=True, description="pdf.desc.page_numbers", order=101)
def page_numbers(ctx: Context) -> Result:
    """Page numbers ("{n}/{total}", start, position, size)."""
    m = _need_mupdf()
    fmt = ctx.pstr("format", "{n}/{total}") or "{n}/{total}"
    fs = float(max(4, min(72, ctx.pint("font_size", 10))))
    start = ctx.pint("start", 1)
    position = ctx.pstr("position", "bottom-center").replace("-center", "")
    color = _rgb(ctx.pstr("color"), (0, 0, 0))
    prep = _prepare(ctx)
    produced = _tmp(ctx, "numbered.pdf")
    stamps = m.open()
    try:
        with m.open(str(prep.path)) as doc:
            n = doc.page_count
            first = 1 if ctx.pbool("skip_first") and n > 1 else 0
            numbered = n - first
            last = start + numbered - 1
            fonts: dict[str, Any] = {}
            # pass 1: build every stamp page (the stamp document must not change while it is shown)
            for k, pno in enumerate(range(first, n)):
                ctx.check_cancel()
                ctx.progress(0.4 * k / max(1, numbered), ctx.t("pdf.prog.page", n=pno + 1, total=n))
                rect = doc[pno].rect
                label = format_page_label(fmt, start + k, last)
                fontname, font = _font_for(m, label)
                fonts.setdefault(fontname, font)
                w, h = rect.width, rect.height
                sp = stamps.new_page(width=w, height=h)
                tw = font.text_length(label, fontsize=fs)
                margin = max(20.0, fs * 2.0)
                cx, cy = _place(position, w, h, tw, fs, margin)
                if fontname != "helv":
                    sp.insert_font(fontname=fontname, fontbuffer=fonts[fontname].buffer)
                sp.insert_text(m.Point(cx - tw / 2, cy + fs * 0.35), label, fontsize=fs, fontname=fontname,
                               color=color)
            # pass 2: stamp page k of the stamp document on page first + k
            for k, pno in enumerate(range(first, n)):
                ctx.check_cancel()
                ctx.progress(0.4 + 0.5 * k / max(1, numbered), ctx.t("pdf.prog.page", n=pno + 1, total=n))
                _stamp(m, doc[pno], stamps, k)
            ctx.progress(0.95, ctx.t("pdf.prog.saving"))
            doc.save(str(produced), garbage=3, deflate=True)
    finally:
        stamps.close()
    return _finish_pdf(ctx, prep, produced, "sfx.numbered")


@action(id="pdf.flatten", label="pdf.act.flatten", icon="layers_clear", categories={"pdf"}, mode="quick",
        params=[_password_param()], requires=[("py:pymupdf", "qpdf")], replace_ok=True,
        description="pdf.desc.flatten", order=102)
def flatten(ctx: Context) -> Result:
    """Burn form fields and annotations into the page content."""
    prep = _prepare(ctx)
    produced = _tmp(ctx, "flat.pdf")
    ctx.progress(None, ctx.t("pdf.prog.working"))
    m = _mupdf()
    baked = False
    if m is not None:
        with m.open(str(prep.path)) as doc:
            items = 0
            for page in doc:
                items += sum(1 for _ in page.annots() or []) + sum(1 for _ in page.widgets() or [])
            if not items:
                return ctx.result(message=ctx.t("pdf.msg.nothing_to_flatten"))
            if hasattr(doc, "bake"):
                doc.bake(annots=True, widgets=True)
                doc.save(str(produced), garbage=3, deflate=True)
                baked = True
            elif not _have("qpdf"):
                raise MissingTool("qpdf")
    if not baked:
        ctx.run(["qpdf", "--generate-appearances", "--flatten-annotations=all", str(prep.path), str(produced)],
                ok_codes=(0, 3))
    return _finish_pdf(ctx, prep, produced, "sfx.flattened")


@action(id="pdf.repair", label="pdf.act.repair", icon="build", categories={"pdf"}, mode="quick",
        params=[_password_param()], requires=[("qpdf", "mutool", "py:pymupdf", "gs")], replace_ok=True,
        description="pdf.desc.repair", order=103)
def repair(ctx: Context) -> Result:
    """Rebuild a damaged PDF (qpdf → mutool clean → PyMuPDF → Ghostscript)."""
    f = ctx.file
    try:
        prep = _prepare(ctx)
    except UserError as exc:
        if exc.message != "err.damaged":
            raise
        prep = _Prep(path=Path(f.path), info=f)
    produced = _tmp(ctx, "repaired.pdf")
    ctx.progress(None, ctx.t("pdf.prog.repairing"))
    errors: list[str] = []
    repaired: bool | None = None
    if _have("qpdf"):
        try:
            res = ctx.run(["qpdf", str(prep.path), str(produced)], ok_codes=(0, 3))
            repaired = res.returncode == 3
        except ToolError as exc:
            errors.append(f"qpdf: {exc.details}")
    if repaired is None and _have("mutool"):
        try:
            res = ctx.run(["mutool", "clean", "-gggg", str(prep.path), str(produced)])
            repaired = bool(re.search(r"(?i)\b(error|warning)\b", res.stderr or ""))
        except ToolError as exc:
            errors.append(f"mutool: {exc.details}")
    if repaired is None and _have("py:pymupdf"):
        m = _need_mupdf()
        try:
            with m.open(str(prep.path)) as doc:
                repaired = bool(getattr(doc, "is_repaired", False))
                if repaired:
                    doc.save(str(produced), garbage=4, deflate=True, clean=True)
        except Exception as exc:
            errors.append(f"pymupdf: {exc}")
            repaired = None
    if repaired is None and _have("gs"):
        try:
            _run_gs(ctx, ["-dCompatibilityLevel=1.7", "-dPDFSETTINGS=/default"], prep.path, produced, prep.pages,
                    ctx.t("pdf.prog.repairing"))
            repaired = True
        except ToolError as exc:
            errors.append(f"gs: {exc.details}")
    if repaired is None:
        raise UserError("pdf.err.repair_failed", details="\n".join(errors))
    if not repaired:
        return ctx.result(message=ctx.t("pdf.msg.no_problems"))
    return _finish_pdf(ctx, prep, produced, "sfx.repaired", message=ctx.t("pdf.msg.repaired"))


_VIEWERS = (("xournalpp", "Xournal++"), ("okular", "Okular"))


@action(id="pdf.annotate", label="pdf.act.annotate", icon="draw", categories={"pdf"}, multi="single",
        mode="instant", undoable=False, requires=[("xournalpp", "okular")], description="pdf.desc.annotate",
        order=104)
def annotate(ctx: Context) -> Result:
    """Open the PDF in Xournal++ (or Okular / ``apps.pdf_editor``)."""
    f = ctx.original(ctx.file)
    custom = str(cfgmod.get(ctx.config, "apps.pdf_editor", "") or "").strip()
    if custom:
        try:
            argv = shlex.split(custom)
        except ValueError:
            argv = []
        if argv and util.detach([*argv, f.path]):
            return ctx.result(message=ctx.t("pdf.msg.opened_in", app=os.path.basename(argv[0])))
    for binary, name in _VIEWERS:
        if _have(binary) and util.detach([binary, f.path]):
            return ctx.result(message=ctx.t("pdf.msg.opened_in", app=name))
    raise MissingTool(["xournalpp"])


@action(id="pdf.print", label="pdf.act.print", icon="print", categories={"pdf"}, multi="any", mode="quick",
        undoable=False,
        params=[P.int("copies", "pdf.prm.copies", 1, min=1, max=999),
                P.select("sides", "pdf.prm.sides", [("one-sided", "pdf.opt.one_sided"),
                                                    ("two-sided-long-edge", "pdf.opt.two_long"),
                                                    ("two-sided-short-edge", "pdf.opt.two_short")],
                         default="one-sided"),
                P.text("printer", "pdf.prm.printer", "", help="pdf.prm.printer.help"),
                _password_param()],
        requires=["lp"], description="pdf.desc.print", order=105)
def print_pdf(ctx: Context) -> Result:
    """Send to the printer with CUPS ``lp``."""
    paths = []
    for f in ctx.files:
        prep = _prepare(ctx, f)
        paths.append(str(prep.path))
    args = ["lp", "-n", str(max(1, ctx.pint("copies", 1))), "-o", f"sides={ctx.pstr('sides', 'one-sided')}"]
    printer = ctx.pstr("printer").strip()
    if printer:
        args += ["-d", printer]
    ctx.progress(None, ctx.t("pdf.prog.printing"))
    try:
        res = ctx.run([*args, "--", *paths], timeout=120)
    except ToolError as exc:
        if "no default destination" in (exc.details or "").lower():
            raise UserError("pdf.err.no_printer", details=exc.details) from None
        raise
    m = re.search(r"request id is (\S+)", res.stdout or "")
    shown = printer or (m.group(1).rsplit("-", 1)[0] if m else "")
    return ctx.result(message=ctx.t("pdf.msg.printed", printer=shown) if shown else ctx.t("pdf.msg.printed_default"))


def _yes(ctx: Context, flag: bool) -> str:
    return ctx.t("pdf.info.yes") if flag else ctx.t("pdf.info.no")


def _info_rows_mupdf(ctx: Context, f: FileInfo) -> list[dict]:
    m = _need_mupdf()
    pw = _password(ctx)
    rows: list[dict] = []

    def add(grp: str, key: str, value: Any) -> None:
        if value is None or value == "":
            return
        rows.append({"group": ctx.t(f"pdf.info.grp.{grp}"), "key": ctx.t(f"pdf.info.{key}"), "value": str(value)})

    try:
        doc = m.open(f.path)
    except Exception as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    with doc:
        add("general", "file", f.name)
        add("general", "size", util.human_size(f.size, ctx.lang))
        locked = False
        if doc.needs_pass:
            if not pw or not doc.authenticate(pw):
                locked = True
        if locked:
            add("security", "encrypted", ctx.t("pdf.info.locked"))
            add("security", "open_password", _yes(ctx, True))
            return rows
        meta = doc.metadata or {}
        add("general", "version", (meta.get("format") or "").replace("PDF ", ""))
        add("general", "pages", doc.page_count)
        sizes: Counter = Counter()
        for page in doc:
            r = page.rect
            name = paper_name(r.width, r.height)
            orient = ctx.t("pdf.info.portrait") if r.height >= r.width else ctx.t("pdf.info.landscape")
            dims = f"{r.width / PT_PER_MM:.0f} × {r.height / PT_PER_MM:.0f} mm"
            sizes[f"{name + ' · ' if name else ''}{dims} ({orient})"] += 1
        for label, count in sizes.most_common(6):
            add("general", "page_size", f"{label}" + (f" × {count}" if len(sizes) > 1 else ""))
        add("general", "linearized", _yes(ctx, bool(getattr(doc, "is_fast_webaccess", False))))
        try:
            kind, marked = doc.xref_get_key(doc.pdf_catalog(), "MarkInfo/Marked")
            add("general", "tagged", _yes(ctx, kind == "bool" and marked == "true"))
        except Exception:
            pass
        annots = widgets = 0
        for page in doc:
            annots += sum(1 for _ in page.annots() or [])
            widgets += sum(1 for _ in page.widgets() or [])
        add("general", "forms", widgets)
        add("general", "annotations", annots)
        add("general", "attachments", doc.embfile_count())
        add("general", "bookmarks", len(doc.get_toc(simple=True)))
        try:
            add("general", "signed", _yes(ctx, int(doc.get_sigflags()) > 0))
        except Exception:
            pass
        for key in ("title", "author", "subject", "keywords", "creator", "producer"):
            add("document", key, (meta.get(key) or "").strip())
        add("document", "created", format_pdf_date(meta.get("creationDate") or ""))
        add("document", "modified", format_pdf_date(meta.get("modDate") or ""))
        method = meta.get("encryption") or ""
        add("security", "encrypted", method or ctx.t("pdf.info.none"))
        if method:
            add("security", "open_password", _yes(ctx, bool(doc.needs_pass)))
            perms = Perms.from_int(_raw_perms_mupdf(doc))
            for key, flag in (("perm_print", perms.print), ("perm_copy", perms.copy),
                              ("perm_modify", perms.modify), ("perm_annotate", perms.annotate)):
                add("security", key, ctx.t("pdf.info.allowed") if flag else ctx.t("pdf.info.denied"))
        fonts: dict[str, str] = {}
        for pno in range(min(doc.page_count, 200)):
            for xref, ext, ftype, basefont, *_ in doc.get_page_fonts(pno, full=True):
                base = str(basefont or "?")
                subset = bool(re.match(r"^[A-Z]{6}\+", base))
                clean = base.split("+", 1)[1] if subset else base
                if clean in fonts:
                    continue
                state = ctx.t("pdf.info.embedded") if ext and ext != "n/a" else ctx.t("pdf.info.not_embedded")
                if subset:
                    state += ", " + ctx.t("pdf.info.subset")
                fonts[clean] = f"{ftype} · {state}"
                if len(fonts) >= 40:
                    break
        for name, desc in sorted(fonts.items()):
            rows.append({"group": ctx.t("pdf.info.grp.fonts"), "key": name, "value": desc})
    return rows


def _info_rows_pdfinfo(ctx: Context, f: FileInfo) -> list[dict]:
    # pdfinfo takes a password only on its command line, where "ps" and the job log would show it:
    # a locked file is decrypted first (pdfinfo opens the others by itself, restrictions included)
    locked = bool(_prepare(ctx, f, decrypt=False).user_pw)
    path = _prepare(ctx, f).path if locked else f.path
    try:
        out = ctx.run(["pdfinfo", "-isodates", str(path)], timeout=60).stdout
    except ToolError as exc:
        if "password" not in (exc.details or "").lower():
            raise
        if _password(ctx):                      # no installed library can open it
            raise MissingTool("qpdf") from None
        raise UserError("pdf.err.password_needed", name=f.name) from None
    original = {"Encrypted": "yes", "File size": f"{f.size or os.path.getsize(f.path)} bytes"} if locked else {}
    rows = []
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if sep and value:
            rows.append({"group": ctx.t("pdf.info.grp.general"), "key": key, "value": original.get(key, value)})
    return rows


@action(id="pdf.info", label="pdf.act.info", icon="info", categories={"pdf"}, mode="quick", undoable=False,
        params=[_password_param()], requires=[("py:pymupdf", "pdfinfo")], description="pdf.desc.info", order=106)
def info(ctx: Context) -> Result:
    """Pages, page sizes, version, encryption, fonts, producer (``kind="info"``)."""
    f = ctx.file
    rows = _info_rows_mupdf(ctx, f) if _have("py:pymupdf") else _info_rows_pdfinfo(ctx, f)
    pages = next((r["value"] for r in rows if r["key"] in (ctx.t("pdf.info.pages"), "Pages")), "")
    return ctx.result(kind="info", info=rows, message=f.name + (f" · {pages} pp." if pages else ""))


# ======================================================================================= LAYOUTS

group("pdf.convert", "grp.convert", "swap_horiz",
      ["pdf.convert.docx", "pdf.convert.jpg", "pdf.convert.png", "pdf.convert.txt", "pdf.convert.md",
       "pdf.convert.epub", "pdf.convert.html",
       GroupSpec(id="pdf.convert.more", label="grp.more", icon="more_horiz",
                 items=["pdf.convert.pdfa", "pdf.convert.gray"])],
      default="pdf.convert.docx")
_COMPRESS_BY_CONFIG = {"screen": "pdf.compress.strong", "ebook": "pdf.compress.balanced",
                       "printer": "pdf.compress.light", "prepress": "pdf.compress.light",
                       "default": "pdf.compress.light"}


def compress_default(files: list, config: dict) -> list[str]:
    """★ of the Compress ring from ``[pdf] compress`` (Ghostscript preset names)."""
    preset = str(cfgmod.get(config, "pdf.compress", "ebook") or "ebook").strip().lower()
    return [_COMPRESS_BY_CONFIG.get(preset, "pdf.compress.balanced"), "pdf.compress.balanced"]


group("pdf.compress", "grp.compress", "compress",
      ["pdf.compress.balanced", "pdf.compress.light", "pdf.compress.strong", "pdf.compress.custom"],
      default=compress_default)
group("pdf.split", "grp.split", "call_split",
      ["pdf.split.pages", "pdf.split.every", "pdf.split.ranges", "pdf.split.bookmarks"], default="pdf.split.pages")
group("pdf.rotate", "grp.rotate", "rotate_right", ["pdf.rotate.cw", "pdf.rotate.180", "pdf.rotate.ccw"],
      default="pdf.rotate.cw")
group("pdf.pages", "grp.pages", "auto_stories",
      ["pdf.pages.organize", "pdf.split", "pdf.pages.extract", "pdf.pages.delete", "pdf.rotate",
       "pdf.pages.reverse", "pdf.pages.blank"],
      default="pdf.pages.organize")
# Secure→ Protect★ (Unlock★ for a protected PDF) · Unlock · Redact… · Remove metadata · Metadata…
group("pdf.secure", "pdf.grp.secure", "lock",
      ["pdf.secure.protect", "pdf.secure.unlock", "pdf.privacy.redact", "pdf.privacy.strip", "pdf.privacy.metadata"],
      default=["pdf.secure.protect", "pdf.secure.unlock"])
group("pdf.ocr", "grp.ocr", "document_scanner", ["pdf.ocr.searchable", "pdf.ocr.copy_text"],
      default="pdf.ocr.searchable")
group("pdf.extract", "grp.extract", "unarchive", ["pdf.extract.images", "pdf.extract.attachments"],
      default="pdf.extract.images")
group("pdf.more", "grp.more", "more_horiz",
      ["pdf.watermark", "pdf.page_numbers", "pdf.flatten", "pdf.repair", "pdf.print", "pdf.info"],
      default="pdf.info")

# Top ring: Convert · Compress · Pages · Secure · OCR · Extract · Annotate · More
layout("pdf", ["pdf.convert", "pdf.compress", "pdf.pages", "pdf.secure", "pdf.ocr", "pdf.extract", "pdf.annotate",
               "pdf.more"])

group("pdf.multi.secure", "pdf.grp.secure", "lock", ["pdf.secure.protect", "pdf.secure.unlock"],
      default=["pdf.secure.protect", "pdf.secure.unlock"])
group("pdf.multi.more", "grp.more", "more_horiz",
      ["pdf.rotate", "pdf.split", "pdf.pages.reverse", "pdf.pages.extract", "pdf.pages.delete", "pdf.pages.blank",
       "pdf.extract", "pdf.watermark", "pdf.page_numbers", "pdf.flatten", "pdf.repair", "pdf.print", "pdf.info"],
      default="pdf.info")

# N PDFs: Merge★ · Compress each · Convert each · OCR each · Collate duplex (2 files) · Secure each · Remove
# metadata each · More
layout("multi:pdf", ["pdf.merge", "pdf.compress", "pdf.convert", "pdf.ocr", "pdf.interleave", "pdf.multi.secure",
                     "pdf.privacy.strip", "pdf.multi.more"])


# ------------------------------------------------------------------------- locked-PDF marking
def _locked_node_extra(files: list[FileInfo], lang: str) -> dict:
    """Menu-node extras for a selection holding password-protected PDFs: ``locked`` (the shell opens
    the parameter form instead of running the action) and a description shown in the wheel pill."""
    try:
        locked = _locked_files(files)
    except Exception:
        log.debug("encryption probe failed", exc_info=True)
        return {}
    if not locked:
        return {}
    if len(locked) == 1:
        text = i18n.t("pdf.prm.password.locked", lang, name=locked[0].name)
    else:
        text = i18n.tn("pdf.prm.password.locked_many", len(locked), lang)
    return {"locked": True, "needs_password": True, "description": text}


def _mark_password_actions() -> None:
    """Attach :func:`_locked_node_extra` to every action of this module taking the PDF password."""
    from .. import registry

    for act in registry.all_actions():
        if act.module == __name__ and any(isinstance(p, PasswordParam) for p in act.params):
            act.node_extra = _locked_node_extra


_mark_password_actions()
