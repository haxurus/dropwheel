"""Document actions: office documents, spreadsheets, presentations, Markdown, plain text, source
code, structured data, ebooks and subtitles (plus their multi-file and mixed variants).

Engines (see :mod:`._docs_engines`): LibreOffice headless (private profile, serialised, "no
output" detection, timeouts), pandoc (Markdown and markup conversions, remote images never
downloaded), typst → WeasyPrint → XeLaTeX/Tectonic → LibreOffice for Markdown → PDF, Pygments +
WeasyPrint/LibreOffice/PyMuPDF for text and code → PDF, calibre for ebooks.  Pure-Python code
(:mod:`._docs_formats`) handles CSV/ODS/XLSX tables, JSON/YAML/TOML/XML/INI data, office
packages (text, pictures, metadata, picture recompression), ebook metadata and subtitles, so many
actions work without any external program.

Inputs are never modified: conversions write ``<stem>.<ext>``, tools ``<stem> (<suffix>).<ext>``,
multi-output actions a ``<stem> (<suffix>)/`` folder; "Replace original" goes through
``ctx.replace_original`` (the original is backed up for Undo).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .. import i18n, tools, util
from ..detect import FileInfo
from ..errors import Cancelled, MissingTool, ToolError, UserError
from ..jobs import Context, Result, atomic_target
from ..registry import P, action, group, layout
from . import _docs_engines as E
from . import _docs_formats as F

log = logging.getLogger("dropwheel.actions.docs")

# ============================================================================== format tables

DOC_FAMILY = frozenset({"document", "presentation", "spreadsheet", "markdown", "text", "code"})
TEXTUAL = frozenset({"text", "code", "markdown", "data", "subtitle"})

WRITER_EXTS = frozenset({"docx", "docm", "dotx", "dotm", "doc", "dot", "odt", "ott", "fodt", "rtf", "wpd", "pages",
                         "abw", "wps", "lwp"})
DRAW_EXTS = frozenset({"odg", "fodg", "otg"})
PS_EXTS = frozenset({"ps"})
XPS_EXTS = frozenset({"xps", "oxps"})
ZIP_DOC_EXTS = frozenset({"docx", "docm", "dotx", "dotm", "odt", "ott"})
CSV_EXTS = frozenset({"csv", "tsv", "tab"})
SHEET_OFFICE_EXTS = frozenset({"xlsx", "xlsm", "xlsb", "xltx", "xls", "xlt", "ods", "ots", "fods", "numbers"})
ZIP_SHEET_EXTS = frozenset({"xlsx", "xlsm", "xltx", "ods", "ots"})
PRES_EXTS = frozenset({"pptx", "pptm", "ppsx", "potx", "ppt", "pps", "pot", "odp", "otp", "fodp", "key"})
ZIP_PRES_EXTS = frozenset({"pptx", "pptm", "ppsx", "potx", "odp", "otp"})
CALIBRE_INPUT = frozenset({"epub", "kepub", "mobi", "azw", "azw3", "azw4", "fb2", "fbz", "djvu", "djv", "cbz", "cbr",
                           "cb7", "cbc", "lit", "lrf", "pdb", "snb", "tcr"})
EBOOK_TARGETS = ("epub", "azw3", "pdf", "docx", "txt", "mobi", "htmlz")

#: Text files larger than this are refused by in-memory text tools.
TEXT_LIMIT = 64 * 1024 * 1024
#: Above this size text → PDF/HTML is rendered without syntax highlighting.
HIGHLIGHT_LIMIT = 4 * 1024 * 1024
#: Above this size text → PDF/HTML is refused (hundreds of pages nobody wants).
RENDER_LIMIT = 24 * 1024 * 1024

LO_TARGETS = {
    "docx": "docx:MS Word 2007 XML", "odt": "odt:writer8", "rtf": "rtf:Rich Text Format", "doc": "doc:MS Word 97",
    "txt": "txt:Text (encoded):UTF8", "html": "html:XHTML Writer File:UTF8", "epub": "epub:EPUB",
    "xlsx": "xlsx:Calc MS Excel 2007 XML", "ods": "ods:calc8", "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "odp": "odp:impress8",
}


def kind(f: FileInfo) -> str:
    """The file's format key: lower-case extension, else the format detected from its content."""
    return (f.ext or f.format or "").lower()


def _is(*exts: Iterable[str] | str) -> Callable[[list[FileInfo]], bool]:
    """``when`` predicate: every file's format is in ``exts``."""
    allowed: set[str] = set()
    for e in exts:
        allowed.update([e] if isinstance(e, str) else e)
    return lambda files: all(kind(f) in allowed for f in files)


def _not(*exts: str) -> Callable[[list[FileInfo]], bool]:
    """``when`` predicate: no file is in ``exts``."""
    banned = set(exts)
    return lambda files: all(kind(f) not in banned for f in files)


def _both(*preds: Callable[[list[FileInfo]], bool]) -> Callable[[list[FileInfo]], bool]:
    return lambda files: all(p(files) for p in preds)


# ==================================================================================== helpers

def _fe(exc: F.FormatError) -> UserError:
    """FormatError → UserError (same key and placeholders)."""
    return UserError(exc.key, details=exc.details, **exc.fmt)


def _warn_all(ctx: Context, warnings: Sequence[tuple[str, dict]]) -> None:
    for key, fmt in warnings:
        ctx.warn(ctx.t(key, **fmt))


def _num(ctx: Context, value: float | int, decimals: int = 0) -> str:
    """Locale-formatted number (``1.234,5`` in Italian, ``1,234.5`` in English)."""
    text = f"{value:,.{decimals}f}"
    if ctx.lang == "it":
        text = text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return text


def _minutes(ctx: Context, minutes: float) -> str:
    if minutes < 1:
        return ctx.t("docs.info.under_minute")
    return ctx.t("docs.info.minutes", n=_num(ctx, round(minutes)))


def _row(ctx: Context, group_key: str, key: str, value: Any) -> dict:
    return {"group": ctx.t(group_key), "key": ctx.t(key), "value": value if isinstance(value, str) else str(value)}


def deliver(ctx: Context, produced: Path, out: Path) -> Path:
    """Move a produced temp file to its reserved output path atomically."""
    if not produced.exists() or produced.stat().st_size == 0:
        raise ToolError("err.no_output", name=out.name)
    ctx.check_cancel()
    with atomic_target(out) as part:
        E.move(produced, part)
    return out


def write_text(ctx: Context, out: Path, text: str, *, bom: bool = False) -> Path:
    """Write UTF-8 text atomically (line endings are written exactly as in ``text``)."""
    ctx.check_cancel()
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    with atomic_target(out) as part:
        part.write_bytes(data)
    return out


def write_bytes(ctx: Context, out: Path, data: bytes) -> Path:
    """Write bytes atomically."""
    ctx.check_cancel()
    with atomic_target(out) as part:
        part.write_bytes(data)
    return out


def _read(ctx: Context, f: FileInfo, limit: int = TEXT_LIMIT) -> F.Decoded:
    try:
        return F.read_text(f.path, limit=limit)
    except F.FormatError as exc:
        if exc.key == "docs.err.too_large_text":
            raise UserError("docs.err.too_large_text", size=util.human_size(f.size, ctx.lang),
                            limit=util.human_size(limit, ctx.lang)) from None
        raise _fe(exc) from None


def _sorted_files(files: Sequence[FileInfo]) -> list[FileInfo]:
    """Natural order by name (the order file managers put in a drag is not reliable)."""
    return util.natural_sorted(files, key=lambda f: f.name)


def _check_office(f: FileInfo) -> None:
    """Refuse password-protected office files early (LibreOffice would hang or fail silently)."""
    if F.office_encrypted(f.path, kind(f)):
        raise UserError("err.encrypted")


def _src(f: FileInfo) -> Path:
    return Path(f.path)


def _lo_single(ctx: Context, f: FileInfo, target: str, out_ext: str, *, infilter: str | None = None,
               src: Path | None = None, src_ext: str | None = None) -> Path:
    """LibreOffice conversion of one file; returns the produced temp file."""
    produced = E.lo_convert(ctx, src or _src(f), target, out_ext=out_ext, src_ext=src_ext or kind(f),
                            infilter=infilter)
    return produced[0]


def _csv_for_lo(ctx: Context, f: FileInfo) -> tuple[Path, str]:
    """A UTF-8 copy of a CSV/TSV plus the LibreOffice import filter for its delimiter."""
    dec = _read(ctx, f)
    delim = F.sniff_delimiter(dec.text[:65536], kind(f))
    copy = E.tmp(ctx, "data.csv")
    copy.write_text(dec.text, encoding="utf-8", newline="")
    return copy, f"Text - txt - csv (StarCalc):{ord(delim)},34,76,1"


def office_pdf(ctx: Context, f: FileInfo, out: Path, options: dict[str, Any] | None = None) -> None:
    """Office document / spreadsheet / presentation (or PS/XPS) → PDF at ``out`` (a temp path)."""
    k = kind(f)
    src = _src(f)
    if k in PS_EXTS:
        E.ps_to_pdf(ctx, src, out)
        return
    if k in XPS_EXTS:
        E.xps_to_pdf(ctx, src, out)
        return
    _check_office(f)
    app = E.LO_APP.get(k) or {"document": "writer", "presentation": "impress", "spreadsheet": "calc"}.get(
        f.category, "writer")
    infilter = None
    src_ext = k
    if k in CSV_EXTS:
        src, infilter = _csv_for_lo(ctx, f)
        src_ext = "csv"
    produced = _lo_single(ctx, f, E.lo_pdf_target(app, options), "pdf", infilter=infilter, src=src, src_ext=src_ext)
    E.move(produced, out)
    if k in ZIP_DOC_EXTS or k in ZIP_SHEET_EXTS or k in ZIP_PRES_EXTS:
        E.warn_fonts(ctx, _src(f))


def _text_pdf(ctx: Context, f: FileInfo, out: Path, *, line_numbers: bool, paper: str = "auto",
              font_pt: float = 9.0, monospace: bool | None = None) -> str:
    """Text or source code → PDF (highlighted when Pygments is installed)."""
    if f.size > RENDER_LIMIT:
        raise UserError("docs.err.too_large_render", limit=util.human_size(RENDER_LIMIT, ctx.lang))
    dec = _read(ctx, f)
    plain = f.category == "text"
    mono = (not plain) if monospace is None else monospace
    paper = E.resolve_paper(paper)
    highlight = f.size <= HIGHLIGHT_LIMIT

    def page(engine: str, wrap: int) -> str:
        return E.code_html(dec.text, filename=f.name, title=f.name, lang=ctx.lang, line_numbers=line_numbers,
                           plain=plain, monospace=mono, paper=paper, hard_wrap=wrap, highlight=highlight,
                           css_extra=f"\nhtml {{ font-size: {font_pt}pt; }}\n")

    ctx.progress(None, ctx.t("docs.prog.render"))
    return E.html_to_pdf(ctx, page, out, base=E.resource_dir(ctx, f), paper=paper)


def file_to_pdf(ctx: Context, f: FileInfo, out: Path) -> None:
    """Any supported file → PDF at the temp path ``out`` (used by Combine / Convert all)."""
    cat = f.category
    src = _src(f)
    if cat == "pdf":
        E.link_or_copy(src, out)
    elif cat in ("document", "presentation", "spreadsheet"):
        office_pdf(ctx, f, out)
    elif cat == "markdown":
        E.markdown_to_pdf(ctx, src, out, title=f.stem, lang=ctx.lang, base=E.resource_dir(ctx, f))
    elif cat in ("text", "code"):
        _text_pdf(ctx, f, out, line_numbers=(cat == "code"))
    elif cat == "image":
        E.image_to_pdf(src, out)
    elif cat == "svg":
        E.svg_to_pdf(ctx, src, out)
    else:
        raise UserError("err.not_supported")
    if not out.exists() or out.stat().st_size == 0:
        raise ToolError("err.no_output", name=f.name)


def _pandoc_source(ctx: Context, f: FileInfo) -> tuple[Path, str]:
    """``(path, pandoc reader)``: the file itself, or a LibreOffice DOCX copy for formats pandoc
    cannot read (doc, wps, pages…)."""
    k = kind(f)
    reader = E.PANDOC_READERS.get(k)
    if f.category == "markdown":
        return _src(f), E.MD_READER
    if reader:
        return _src(f), reader
    _check_office(f)
    if tools.path("soffice") is None:
        raise MissingTool("soffice")
    return _lo_single(ctx, f, LO_TARGETS["docx"], "docx"), "docx"


def _pdf_paper_param() -> Any:
    return P.select("paper", "docs.prm.paper", [("auto", "docs.opt.paper_auto"), ("a4", "A4"), ("letter", "Letter")],
                    default="auto")


# ============================================================================ office documents

def _office_pdf_options(ctx: Context, f: FileInfo) -> dict[str, Any]:
    pdfa = ctx.pstr("pdf_version", "default") == "pdfa"
    opts: dict[str, Any] = {"ExportBookmarks": ctx.pbool("bookmarks", True), "ExportNotes": ctx.pbool("comments", False)}
    if pdfa:
        opts["SelectPdfVersion"] = 2
        opts["UseTaggedPDF"] = True
    if f.category == "presentation" and ctx.pbool("notes", False):
        opts["ExportNotesPages"] = True
    if f.category == "spreadsheet" and ctx.pstr("fit", "pages") == "single":
        opts["SinglePageSheets"] = True
    return opts


def _convert_office_pdf(ctx: Context) -> Result:
    f = ctx.file
    produced = E.tmp(ctx, "out.pdf")
    office_pdf(ctx, f, produced, _office_pdf_options(ctx, f) if kind(f) not in PS_EXTS | XPS_EXTS else None)
    out = deliver(ctx, produced, ctx.output_path(f, ext="pdf"))
    return ctx.result(out)


_PDF_OPTION_PARAMS = [
    P.select("pdf_version", "docs.prm.pdf_version", [("default", "docs.opt.pdf_standard"), ("pdfa", "docs.opt.pdfa")],
             default="default", help="docs.prm.pdf_version.help"),
    P.bool("bookmarks", "docs.prm.bookmarks", True),
    P.bool("comments", "docs.prm.comments", False),
]


@action(id="document.convert.pdf", label="PDF", text="PDF", icon="picture_as_pdf", categories={"document"},
        when=_is(WRITER_EXTS | DRAW_EXTS), mode="quick", params=_PDF_OPTION_PARAMS, requires=["soffice"],
        description="docs.desc.office_pdf", order=1)
def document_to_pdf(ctx: Context) -> Result:
    """Office document → PDF with LibreOffice (bookmarks, optional PDF/A-2b and comments)."""
    return _convert_office_pdf(ctx)


@action(id="document.convert.pdf_ps", label="PDF", text="PDF", icon="picture_as_pdf", categories={"document"},
        when=_is(PS_EXTS), requires=["gs"], order=1)
def postscript_to_pdf(ctx: Context) -> Result:
    """PostScript → PDF with Ghostscript."""
    return _convert_office_pdf(ctx)


@action(id="document.convert.pdf_xps", label="PDF", text="PDF", icon="picture_as_pdf", categories={"document"},
        when=_is(XPS_EXTS), requires=[("py:pymupdf", "mutool")], order=1)
def xps_to_pdf(ctx: Context) -> Result:
    """XPS/OXPS → PDF with PyMuPDF (or mutool)."""
    return _convert_office_pdf(ctx)


def _doc_lo_convert(target: str) -> Callable[[Context], Result]:
    def run(ctx: Context) -> Result:
        f = ctx.file
        _check_office(f)
        produced = _lo_single(ctx, f, LO_TARGETS[target], target)
        out = deliver(ctx, produced, ctx.output_path(f, ext=target))
        return ctx.result(out)

    run.__doc__ = f"Office document → {target.upper()} with LibreOffice."
    run.__name__ = f"document_to_{target}"
    return run


for _target, _order, _icon in (("docx", 2, "description"), ("odt", 3, "description"), ("rtf", 8, "description"),
                               ("doc", 9, "description")):
    action(id=f"document.convert.{_target}", label=_target.upper(), text=_target.upper(), icon=_icon,
           categories={"document"}, when=_both(_is(WRITER_EXTS), _not(_target)), requires=["soffice"],
           order=_order)(_doc_lo_convert(_target))


#: Writer formats whose text the built-in readers extract without any external program.
TXT_NATIVE_EXTS = ZIP_DOC_EXTS | {"rtf"}


@action(id="document.convert.txt", label="TXT", text="TXT", icon="text_snippet", categories={"document"},
        when=_is(TXT_NATIVE_EXTS), order=5)
def document_to_txt(ctx: Context) -> Result:
    """Plain UTF-8 text: LibreOffice when installed, else pandoc, else the built-in DOCX/ODT/RTF reader."""
    f = ctx.file
    _check_office(f)
    out = ctx.output_path(f, ext="txt")
    k = kind(f)
    if tools.have("soffice"):
        return ctx.result(deliver(ctx, _lo_single(ctx, f, LO_TARGETS["txt"], "txt"), out))
    if k in E.PANDOC_READERS and tools.have("pandoc"):
        produced = E.tmp(ctx, "out.txt")
        E.pandoc_run(ctx, [str(_src(f)), "-f", E.PANDOC_READERS[k], "-t", "plain", "--wrap=none", "-o", str(produced)])
        return ctx.result(deliver(ctx, produced, out))
    ctx.progress(None, ctx.t("docs.prog.reading"))
    try:
        if F.office_kind(f.path) in ("ooxml", "odf"):
            text = F.office_text(f.path, k)
        elif k == "rtf":
            text = F.rtf_to_text(_read(ctx, f).text)
        else:
            raise UserError("err.not_supported")
    except F.FormatError as exc:
        raise _fe(exc) from None
    if not text.strip():
        raise UserError("docs.err.no_text")
    return ctx.result(write_text(ctx, out, text if text.endswith("\n") else text + "\n"))


@action(id="document.convert.txt_lo", label="TXT", text="TXT", icon="text_snippet", categories={"document"},
        when=_is(WRITER_EXTS - TXT_NATIVE_EXTS), requires=["soffice"], order=5)
def legacy_document_to_txt(ctx: Context) -> Result:
    """Plain UTF-8 text of legacy/foreign word-processor files (DOC, WPS, Pages…) with LibreOffice."""
    f = ctx.file
    _check_office(f)
    return ctx.result(deliver(ctx, _lo_single(ctx, f, LO_TARGETS["txt"], "txt"), ctx.output_path(f, ext="txt")))


@action(id="document.convert.md", label="docs.act.markdown", text="MD", icon="article", categories={"document"},
        when=_is(WRITER_EXTS), requires=["pandoc"], description="docs.desc.doc_markdown", order=4)
def document_to_markdown(ctx: Context) -> Result:
    """Markdown (GFM) with pandoc; embedded pictures go to a ``media`` folder next to it."""
    f = ctx.file
    src, reader = _pandoc_source(ctx, f)
    work = E.tmp(ctx, "md")
    work.mkdir()
    md = work / "document.md"
    E.pandoc_run(ctx, [str(src), "-f", reader, "-t", "gfm", "--wrap=none", "--extract-media=media", "-o", str(md)],
                 cwd=work)
    media = work / "media"
    if media.is_dir() and any(media.rglob("*")):
        folder = ctx.output_dir(f, suffix="docs.sfx.markdown")
        shutil.move(str(md), str(folder / util.safe_filename(f"{f.stem}.md")))
        shutil.move(str(media), str(folder / "media"))
        return ctx.result(folder, message=ctx.t("docs.msg.markdown_media"))
    return ctx.result(deliver(ctx, md, ctx.output_path(f, ext="md")))


@action(id="document.convert.html", label="HTML", text="HTML", icon="html", categories={"document"},
        when=_is(WRITER_EXTS), requires=[("pandoc", "soffice")], order=6)
def document_to_html(ctx: Context) -> Result:
    """Self-contained HTML page (pandoc, pictures embedded; LibreOffice otherwise)."""
    f = ctx.file
    out = ctx.output_path(f, ext="html")
    if tools.have("pandoc"):
        src, reader = _pandoc_source(ctx, f)
        header = E.tmp(ctx, "style.html")
        header.write_text(f"<style>\n{E.document_css(print_mode=False)}\n</style>\n", encoding="utf-8")
        meta = E.metadata_file(ctx, {"pagetitle": f.stem, "lang": ctx.lang})
        produced = E.tmp(ctx, "out.html")
        E.pandoc_run(ctx, [str(src), "-f", reader, "-t", "html5", "-s", "--embed-resources", "-H", str(header),
                           "--metadata-file", str(meta), "-o", str(produced)], cwd=ctx.tmpdir())
        return ctx.result(deliver(ctx, produced, out))
    _check_office(f)
    return ctx.result(deliver(ctx, _lo_single(ctx, f, LO_TARGETS["html"], "html"), out))


@action(id="document.convert.epub", label="EPUB", text="EPUB", icon="menu_book", categories={"document"},
        when=_is(WRITER_EXTS), requires=[("pandoc", "soffice")], order=7)
def document_to_epub(ctx: Context) -> Result:
    """EPUB 3 (pandoc; LibreOffice's EPUB export otherwise)."""
    f = ctx.file
    out = ctx.output_path(f, ext="epub")
    if tools.have("pandoc") and (kind(f) in E.PANDOC_READERS or tools.have("soffice")):
        src, reader = _pandoc_source(ctx, f)
        meta = E.metadata_file(ctx, {"title": f.stem, "lang": ctx.lang})
        produced = E.tmp(ctx, "out.epub")
        E.pandoc_run(ctx, [str(src), "-f", reader, "-t", "epub3", "--metadata-file", str(meta), "-o", str(produced)],
                     cwd=ctx.tmpdir())
        return ctx.result(deliver(ctx, produced, out))
    _check_office(f)
    return ctx.result(deliver(ctx, _lo_single(ctx, f, LO_TARGETS["epub"], "epub"), out))


def _pages_to_images(ctx: Context, f: FileInfo, fmt: str, prefix: str, suffix: str, dpi: int,
                     options: dict | None = None) -> Result:
    pdf = E.tmp(ctx, "pages.pdf")
    office_pdf(ctx, f, pdf, options)
    folder = ctx.output_dir(f, suffix=suffix)
    images = E.pdf_to_images(ctx, pdf, folder, fmt=fmt, dpi=dpi, prefix=prefix)
    if not images:
        raise UserError("err.no_output", name=folder.name)
    if len(images) == 1:
        single = ctx.output_path(f, ext=fmt)
        E.move(images[0], single)
        shutil.rmtree(folder, ignore_errors=True)
        return ctx.result(single)
    return ctx.result(folder, message=i18n.tn("docs.msg.images", len(images), ctx.lang))


@action(id="document.convert.png", label="docs.act.page_images", text="PNG", icon="image", categories={"document"},
        when=_is(WRITER_EXTS | DRAW_EXTS), mode="quick",
        params=[P.int("dpi", "docs.prm.dpi", "cfg:pdf.dpi", min=36, max=600, step=10, unit="dpi")],
        requires=["soffice", ("py:pymupdf", "pdftoppm")], order=10)
def document_to_png(ctx: Context) -> Result:
    """One PNG per page (via PDF) in ``<stem> (pagine)/``."""
    return _pages_to_images(ctx, ctx.file, "png", "page", "sfx.pages", ctx.pint("dpi", 150))


@action(id="document.secure.pdf", label="docs.act.protected_pdf", icon="lock", categories={"document"},
        when=_is(WRITER_EXTS | DRAW_EXTS), mode="dialog", dialog="form",
        params=[P.password("password", "docs.prm.password", "", required=True, help="docs.prm.password.help")],
        requires=["soffice", ("py:pymupdf", "py:pypdf")], description="docs.desc.protected_pdf", order=60)
def document_protected_pdf(ctx: Context) -> Result:
    """PDF protected by an open password (AES-256); the password is never put on a command line."""
    f = ctx.file
    password = ctx.pstr("password", "")
    if not password:
        raise UserError("err.missing_param", param=ctx.t("docs.prm.password"))
    pdf = E.tmp(ctx, "plain.pdf")
    office_pdf(ctx, f, pdf, {"ExportBookmarks": True})
    ctx.progress(0.9, ctx.t("docs.prog.encrypt"))
    protected = E.tmp(ctx, "protected.pdf")
    E.encrypt_pdf(ctx, pdf, protected, password)
    out = deliver(ctx, protected, ctx.output_path(f, ext="pdf", suffix="sfx.protected"))
    return ctx.result(out)


# ------------------------------------------------------------------ office zips (shared tools)

def _office_compress(ctx: Context) -> Result:
    f = ctx.file
    src = _src(f)
    if F.office_kind(src) not in ("ooxml", "odf"):
        raise UserError("docs.err.not_office_zip")
    _check_office(f)
    produced = E.tmp(ctx, f"compressed.{kind(f) or 'zip'}")
    quality = max(30, min(95, ctx.pint("quality", 80)))
    max_px = max(320, ctx.pint("max_px", 2000))
    ctx.progress(0.0, ctx.t("docs.prog.images"))
    try:
        total, shrunk = F.compress_office_media(src, produced, quality=quality, max_px=max_px,
                                                progress=lambda x: ctx.progress(x * 0.95, ctx.t("docs.prog.images")),
                                                check=ctx.check_cancel)
    except ImportError:
        raise MissingTool("py:PIL") from None
    except F.FormatError as exc:
        raise _fe(exc) from None
    before = f.size
    after = produced.stat().st_size
    if after >= before * 0.99:
        ctx.warn(ctx.t("warn.already_optimized"))
        return ctx.result(kind="none", message=ctx.t("docs.msg.not_smaller"))
    out = deliver(ctx, produced, ctx.output_path(f, suffix="sfx.compressed"))
    final = ctx.replace_original(f, out)
    return ctx.result(final, size_before=before, message=i18n.tn("docs.msg.images_recompressed", shrunk, ctx.lang))


def _office_strip(ctx: Context) -> Result:
    f = ctx.file
    _check_office(f)
    produced = E.tmp(ctx, f"clean.{kind(f) or 'zip'}")
    ctx.progress(None, ctx.t("docs.prog.metadata"))
    try:
        F.strip_office_metadata(_src(f), produced)
    except F.FormatError as exc:
        raise _fe(exc) from None
    out = deliver(ctx, produced, ctx.output_path(f, suffix="sfx.no_metadata"))
    final = ctx.replace_original(f, out)
    return ctx.result(final, message=ctx.t("docs.msg.metadata_removed"))


def _office_images(ctx: Context) -> Result:
    f = ctx.file
    _check_office(f)
    src = _src(f)
    if F.office_kind(src) not in ("ooxml", "odf"):
        # legacy binary formats: let LibreOffice rewrite them as an open package first
        target = "docx" if f.category == "document" else ("pptx" if f.category == "presentation" else "xlsx")
        src = _lo_single(ctx, f, LO_TARGETS[target], target)
    skipped: list[str] = []
    try:
        media = F.office_media(src, skipped)
    except F.FormatError as exc:
        raise _fe(exc) from None
    if not media:
        raise UserError("docs.err.no_images")
    if skipped:
        ctx.warn(ctx.t("docs.warn.media_skipped", names=", ".join(skipped[:5]) + (" …" if len(skipped) > 5 else "")))
    if len(media) == 1:
        name, data = media[0]
        ext = util.split_name(name)[1] or "bin"
        return ctx.result(write_bytes(ctx, ctx.output_path(f, ext=ext, suffix="docs.sfx.image"), data))
    folder = ctx.output_dir(f, suffix="docs.sfx.images")
    used: set[str] = set()
    for i, (name, data) in enumerate(media, 1):
        ctx.check_cancel()
        safe = util.safe_filename(name)
        if safe.lower() in used:
            stem, ext = util.split_name(safe)
            safe = f"{stem}-{i}.{ext}" if ext else f"{stem}-{i}"
        used.add(safe.lower())
        (folder / safe).write_bytes(data)
    return ctx.result(folder, message=i18n.tn("docs.msg.images", len(media), ctx.lang))


_COMPRESS_PARAMS = [
    P.slider("quality", "docs.prm.quality", 80, min=30, max=95, step=1, help="docs.prm.quality.help"),
    P.int("max_px", "docs.prm.max_px", 2000, min=320, max=10000, step=100, unit="px", help="docs.prm.max_px.help"),
]


@action(id="document.compress", label="grp.compress", icon="compress", categories={"document"},
        when=_is(ZIP_DOC_EXTS), mode="quick", params=_COMPRESS_PARAMS, requires=["py:PIL"], replace_ok=True,
        description="docs.desc.office_compress", order=20)
def document_compress(ctx: Context) -> Result:
    """Re-encode the pictures inside a DOCX/ODT and re-zip it at maximum compression."""
    return _office_compress(ctx)


@action(id="document.privacy.strip", label="docs.act.strip_metadata", icon="person_off", categories={"document"},
        when=_is(ZIP_DOC_EXTS), replace_ok=True, description="docs.desc.strip_metadata", order=30)
def document_strip(ctx: Context) -> Result:
    """Remove author, dates, company, template path and comment authors from a DOCX/ODT."""
    return _office_strip(ctx)


@action(id="document.extract.images", label="docs.act.extract_images", icon="photo_library", categories={"document"},
        when=_is(WRITER_EXTS - {"rtf"}), requires=[], order=40)
def document_images(ctx: Context) -> Result:
    """Save the pictures embedded in a document (``<stem> (immagini)/``)."""
    return _office_images(ctx)


def _document_text(ctx: Context, f: FileInfo) -> str:
    k = kind(f)
    try:
        if F.office_kind(f.path) in ("ooxml", "odf"):
            return F.office_text(f.path, k)
        if k == "rtf":
            return F.rtf_to_text(_read(ctx, f).text)
    except F.FormatError as exc:
        raise _fe(exc) from None
    if not tools.have("soffice"):
        raise MissingTool("soffice")
    produced = _lo_single(ctx, f, LO_TARGETS["txt"], "txt")
    return F.read_text(produced).text


@action(id="document.info", label="docs.act.stats", icon="query_stats", categories={"document"},
        when=_is(WRITER_EXTS), undoable=False, description="docs.desc.stats", order=70)
def document_stats(ctx: Context) -> Result:
    """Words, characters, paragraphs and reading time (plus the statistics the editor saved)."""
    f = ctx.file
    _check_office(f)
    ctx.progress(None, ctx.t("docs.prog.reading"))
    text = _document_text(ctx, f)
    stats = F.text_stats(text)
    grp = "docs.info.grp_text"
    rows = [
        _row(ctx, grp, "docs.info.words", _num(ctx, stats["words"])),
        _row(ctx, grp, "docs.info.chars", _num(ctx, stats["chars"])),
        _row(ctx, grp, "docs.info.chars_no_spaces", _num(ctx, stats["chars_no_spaces"])),
        _row(ctx, grp, "docs.info.paragraphs", _num(ctx, stats["paragraphs"])),
        _row(ctx, grp, "docs.info.reading", _minutes(ctx, stats["reading_minutes"])),
    ]
    saved = F.office_app_stats(f.path)
    page_key = "Pages" if "Pages" in saved else "page-count" if "page-count" in saved else None
    if page_key:
        rows.append(_row(ctx, "docs.info.grp_saved", "docs.info.pages", _num(ctx, saved[page_key])))
    for src_key, label in (("Words", "docs.info.words"), ("word-count", "docs.info.words"),
                           ("TotalTime", "docs.info.edit_minutes")):
        if src_key in saved:
            value = _minutes(ctx, saved[src_key]) if src_key == "TotalTime" else _num(ctx, saved[src_key])
            rows.append(_row(ctx, "docs.info.grp_saved", label, value))
    return ctx.result(kind="info", info=rows, message=ctx.t("docs.msg.words", n=_num(ctx, stats["words"])))


# =============================================================================== spreadsheets

#: CSV larger than this is refused (everything is processed in memory).
CSV_LIMIT = 256 * 1024 * 1024
_DELIMS = {",": "docs.opt.comma", ";": "docs.opt.semicolon", "\t": "docs.opt.tab", "|": "docs.opt.pipe"}


def read_sheets(ctx: Context, f: FileInfo) -> tuple[list[F.Sheet], dict[str, Any]]:
    """Every sheet of a spreadsheet as ``[(name, rows)]`` plus ``{"csv", "delimiter", "encoding"}``."""
    k = kind(f)
    src = _src(f)
    ctx.progress(None, ctx.t("docs.prog.reading"))
    try:
        if k in CSV_EXTS:
            rows, delim, dec = F.read_csv_rows(src, k, limit=CSV_LIMIT)
            return [(f.stem, rows)], {"csv": True, "delimiter": delim, "encoding": dec.label, "bom": dec.bom}
        _check_office(f)
        missing = "soffice"
        if k in ("xlsx", "xlsm", "xltx"):
            try:
                return F.read_xlsx(src), {"csv": False}
            except ImportError:
                missing = "py:openpyxl"
        elif k in ("ods", "ots"):
            return F.read_ods(src), {"csv": False}
        elif k in ("xls", "xlt"):
            try:
                return F.read_xls(src), {"csv": False}
            except ImportError:
                missing = "py:xlrd"
        if tools.have("soffice"):
            return F.read_ods(_lo_single(ctx, f, LO_TARGETS["ods"], "ods")), {"csv": False}
        raise MissingTool(missing)
    except F.FormatError as exc:
        raise _fe(exc) from None


def _infer_sheets(sheets: list[F.Sheet], meta: dict[str, Any]) -> list[F.Sheet]:
    """Numbers in CSV cells become numbers (codes with leading zeros stay text); a ";" CSV reads
    "1.234,56" the Italian way unless a column clearly uses "." decimals."""
    if not meta.get("csv"):
        return sheets
    comma = meta.get("delimiter") == ";"
    return [(name, F.infer_rows(rows, decimal_comma=comma)) for name, rows in sheets]


def _write_xlsx_any(ctx: Context, sheets: list[F.Sheet]) -> Path:
    """XLSX from sheets: openpyxl, else the built-in ODS writer + LibreOffice."""
    produced = E.tmp(ctx, "out.xlsx")
    ctx.progress(None, ctx.t("docs.prog.writing"))
    try:
        F.write_xlsx(produced, sheets)
        return produced
    except ImportError:
        if not tools.have("soffice"):
            raise MissingTool("py:openpyxl") from None
    except F.FormatError as exc:
        raise _fe(exc) from None
    ods = E.tmp(ctx, "data.ods")
    F.write_ods(ods, sheets)
    return E.lo_convert(ctx, ods, LO_TARGETS["xlsx"], out_ext="xlsx", src_ext="ods")[0]


@action(id="spreadsheet.convert.xlsx", label="XLSX", text="XLSX", icon="table_chart", categories={"spreadsheet"},
        when=_not("xlsx"), requires=[("py:openpyxl", "soffice")], order=1)
def sheet_to_xlsx(ctx: Context) -> Result:
    """Excel workbook (LibreOffice keeps formatting; CSV is typed with openpyxl)."""
    f = ctx.file
    out = ctx.output_path(f, ext="xlsx")
    if kind(f) not in CSV_EXTS and tools.have("soffice"):
        _check_office(f)
        return ctx.result(deliver(ctx, _lo_single(ctx, f, LO_TARGETS["xlsx"], "xlsx"), out))
    sheets, meta = read_sheets(ctx, f)
    produced = _write_xlsx_any(ctx, _infer_sheets(sheets, meta))
    if not meta.get("csv"):
        ctx.warn(ctx.t("docs.warn.values_only"))
    return ctx.result(deliver(ctx, produced, out))


@action(id="spreadsheet.convert.ods", label="ODS", text="ODS", icon="table_chart", categories={"spreadsheet"},
        when=_not("ods"), order=2)
def sheet_to_ods(ctx: Context) -> Result:
    """OpenDocument spreadsheet (LibreOffice for office files; built-in writer for CSV)."""
    f = ctx.file
    out = ctx.output_path(f, ext="ods")
    if kind(f) not in CSV_EXTS and tools.have("soffice"):
        _check_office(f)
        return ctx.result(deliver(ctx, _lo_single(ctx, f, LO_TARGETS["ods"], "ods"), out))
    sheets, meta = read_sheets(ctx, f)
    produced = E.tmp(ctx, "out.ods")
    ctx.progress(None, ctx.t("docs.prog.writing"))
    F.write_ods(produced, _infer_sheets(sheets, meta))
    if not meta.get("csv"):
        ctx.warn(ctx.t("docs.warn.values_only"))
    return ctx.result(deliver(ctx, produced, out))


def _delimited(ctx: Context, delim: str, ext: str, *, bom: bool = False) -> Result:
    f = ctx.file
    k = kind(f)
    items: list[tuple[str, Path]] = []
    if k not in CSV_EXTS and tools.have("soffice"):
        _check_office(f)
        target = f"csv:Text - txt - csv (StarCalc):{ord(delim)},34,76,1,,0,false,true,true,false,false,-1"
        produced = E.lo_convert(ctx, _src(f), target, out_ext="csv", src_ext=k, multi=True)
        for p in produced:
            stem = p.name[:-4]
            sheet = stem[len("source-"):] if stem.startswith("source-") else f.stem
            if bom:
                data = p.read_bytes()
                if not data.startswith(b"\xef\xbb\xbf"):
                    p.write_bytes(b"\xef\xbb\xbf" + data)
            items.append((sheet, p))
    else:
        sheets, _meta = read_sheets(ctx, f)
        ctx.progress(None, ctx.t("docs.prog.writing"))
        for i, (name, rows) in enumerate(sheets):
            path = E.tmp(ctx, f"{i}.{ext}")
            F.write_csv_rows(path, rows, delimiter=delim, bom=bom, crlf=bom)
            items.append((name, path))
    if not items:
        raise UserError("docs.err.empty_sheet")
    if len(items) == 1:
        return ctx.result(deliver(ctx, items[0][1], ctx.output_path(f, ext=ext)))
    folder = ctx.output_dir(f, suffix="docs.sfx.sheets")
    used: set[str] = set()
    for i, (name, path) in enumerate(items, 1):
        fname = util.safe_filename(f"{name}.{ext}")
        if fname.lower() in used:
            fname = util.safe_filename(f"{name} {i}.{ext}")
        used.add(fname.lower())
        E.move(path, folder / fname)
    return ctx.result(folder, message=i18n.tn("docs.msg.sheets", len(items), ctx.lang))


@action(id="spreadsheet.convert.csv", label="CSV", text="CSV", icon="csv", categories={"spreadsheet"},
        when=_not("csv"), mode="quick",
        params=[P.select("delimiter", "docs.prm.delimiter", [(",", "docs.opt.comma"), (";", "docs.opt.semicolon")],
                         default=","),
                P.bool("excel", "docs.prm.excel_bom", False, help="docs.prm.excel_bom.help")],
        description="docs.desc.csv_sheets", order=3)
def sheet_to_csv(ctx: Context) -> Result:
    """CSV (one file per sheet in a ``<stem> (fogli)`` folder when there are several)."""
    delim = ctx.pstr("delimiter", ",")
    return _delimited(ctx, delim if delim in (",", ";") else ",", "csv", bom=ctx.pbool("excel", False))


@action(id="spreadsheet.convert.tsv", label="TSV", text="TSV", icon="table_rows", categories={"spreadsheet"},
        when=_not("tsv", "tab"), order=8)
def sheet_to_tsv(ctx: Context) -> Result:
    """Tab-separated values (one file per sheet)."""
    return _delimited(ctx, "\t", "tsv")


@action(id="spreadsheet.convert.json", label="JSON", text="JSON", icon="data_object", categories={"spreadsheet"},
        mode="quick", params=[P.bool("types", "docs.prm.types", True, help="docs.prm.types.help")],
        description="docs.desc.sheet_json", order=5)
def sheet_to_json(ctx: Context) -> Result:
    """JSON records (first row = keys); ``{sheet: records}`` for several sheets."""
    f = ctx.file
    sheets, meta = read_sheets(ctx, f)
    csv_src = bool(meta.get("csv"))
    infer = ctx.pbool("types", True) and csv_src
    comma = meta.get("delimiter") == ";"
    tables = {name: F.records_from_rows(rows, infer=infer, decimal_comma=comma, empty_as_null=infer or not csv_src)
              for name, rows in sheets}
    value: Any = next(iter(tables.values())) if len(tables) == 1 else tables
    warnings: list = []
    text = json.dumps(F.to_json_compatible(value, warnings), ensure_ascii=False, indent=2) + "\n"
    _warn_all(ctx, warnings)
    return ctx.result(write_text(ctx, ctx.output_path(f, ext="json"), text))


@action(id="spreadsheet.convert.html", label="HTML", text="HTML", icon="html", categories={"spreadsheet"}, order=7)
def sheet_to_html(ctx: Context) -> Result:
    """A standalone HTML page with one table per sheet."""
    f = ctx.file
    sheets, _meta = read_sheets(ctx, f)
    page = F.html_tables_document(sheets, title=f.stem, lang=ctx.lang)
    return ctx.result(write_text(ctx, ctx.output_path(f, ext="html"), page))


@action(id="spreadsheet.convert.md", label="docs.act.markdown_table", text="MD", icon="table", categories={"spreadsheet"},
        order=6)
def sheet_to_markdown(ctx: Context) -> Result:
    """Markdown table(s) (``## sheet`` headings when there are several sheets)."""
    f = ctx.file
    sheets, _meta = read_sheets(ctx, f)
    if len(sheets) == 1:
        text = F.markdown_table(sheets[0][1])
    else:
        text = "\n".join(f"## {name}\n\n{F.markdown_table(rows) or '—'}\n" for name, rows in sheets)
    if not text.strip():
        raise UserError("docs.err.empty_sheet")
    return ctx.result(write_text(ctx, ctx.output_path(f, ext="md"), text))


@action(id="spreadsheet.convert.pdf", label="PDF", text="PDF", icon="picture_as_pdf", categories={"spreadsheet"},
        mode="quick", params=[P.select("fit", "docs.prm.fit", [("pages", "docs.opt.fit_pages"),
                                                               ("single", "docs.opt.fit_single")], default="pages")],
        requires=["soffice"], order=4)
def sheet_to_pdf(ctx: Context) -> Result:
    """PDF with LibreOffice (normal pages, or each sheet on one page)."""
    return _convert_office_pdf(ctx)


def _csv_tool(ctx: Context, delim: str, *, bom: bool, suffix: str) -> Result:
    f = ctx.file
    try:
        rows, current, dec = F.read_csv_rows(_src(f), kind(f), limit=CSV_LIMIT)
    except F.FormatError as exc:
        raise _fe(exc) from None
    unchanged = current == delim and ((bom and dec.bom and dec.encoding.startswith("utf-8") and not dec.repaired)
                                      or (not bom and dec.is_utf8))
    if unchanged and kind(f) == "csv":
        return ctx.result(kind="none", message=ctx.t("docs.msg.csv_unchanged"))
    produced = E.tmp(ctx, "out.csv")
    F.write_csv_rows(produced, rows, delimiter=delim, bom=bom, crlf=bom)
    out = deliver(ctx, produced, ctx.output_path(f, ext="csv", suffix=suffix))
    final = ctx.replace_original(f, out)
    return ctx.result(final)


@action(id="spreadsheet.csv.excel", label="docs.act.csv_excel", icon="grid_on", categories={"spreadsheet"},
        when=_is(CSV_EXTS), replace_ok=True, description="docs.desc.csv_excel", order=30)
def csv_for_excel(ctx: Context) -> Result:
    """CSV that Excel (Italian locale) opens correctly: UTF-8 with BOM, ";" separator, CRLF."""
    return _csv_tool(ctx, ";", bom=True, suffix="docs.sfx.excel")


@action(id="spreadsheet.csv.comma", label="docs.act.csv_comma", icon="csv", categories={"spreadsheet"},
        when=_is(CSV_EXTS), replace_ok=True, description="docs.desc.csv_comma", order=31)
def csv_comma(ctx: Context) -> Result:
    """Standard CSV: UTF-8, "," separator."""
    return _csv_tool(ctx, ",", bom=False, suffix="docs.sfx.comma")


@action(id="spreadsheet.csv.semicolon", label="docs.act.csv_semicolon", icon="view_column",
        categories={"spreadsheet"}, when=_is(CSV_EXTS), replace_ok=True, description="docs.desc.csv_semicolon",
        order=32)
def csv_semicolon(ctx: Context) -> Result:
    """UTF-8 CSV with ";" separator."""
    return _csv_tool(ctx, ";", bom=False, suffix="docs.sfx.semicolon")


@action(id="spreadsheet.privacy.strip", label="docs.act.strip_metadata", icon="person_off",
        categories={"spreadsheet"}, when=_is(ZIP_SHEET_EXTS), replace_ok=True, description="docs.desc.strip_metadata",
        order=40)
def sheet_strip(ctx: Context) -> Result:
    """Remove author, dates, company and comment authors from an XLSX/ODS."""
    return _office_strip(ctx)


@action(id="spreadsheet.info", label="docs.act.sheet_info", icon="info", categories={"spreadsheet"},
        undoable=False, order=60)
def sheet_info(ctx: Context) -> Result:
    """Sheets, rows and columns (plus delimiter and encoding for CSV)."""
    f = ctx.file
    sheets, meta = read_sheets(ctx, f)
    rows_out: list[dict] = []
    if meta.get("csv"):
        grp = "docs.info.grp_file"
        delim = meta.get("delimiter", ",")
        rows_out.append(_row(ctx, grp, "docs.info.delimiter", ctx.t(_DELIMS.get(delim, delim))))
        rows_out.append(_row(ctx, grp, "docs.info.encoding", meta.get("encoding", "?")))
    else:
        rows_out.append(_row(ctx, "docs.info.grp_file", "docs.info.sheets", _num(ctx, len(sheets))))
    total_rows = 0
    for name, rows in sheets:
        width = max((len(r) for r in rows), default=0)
        total_rows += len(rows)
        rows_out.append({"group": name, "key": ctx.t("docs.info.rows"), "value": _num(ctx, len(rows))})
        rows_out.append({"group": name, "key": ctx.t("docs.info.columns"), "value": _num(ctx, width)})
        if rows:
            header = [F.cell_text(c) for c in rows[0] if F.cell_text(c).strip()]
            if header:
                shown = ", ".join(header[:8]) + (" …" if len(header) > 8 else "")
                rows_out.append({"group": name, "key": ctx.t("docs.info.header"), "value": shown})
    message = ctx.t("docs.msg.sheet_info", sheets=_num(ctx, len(sheets)), rows=_num(ctx, total_rows))
    return ctx.result(kind="info", info=rows_out, message=message)


@action(id="spreadsheet.merge.csv", label="docs.act.merge_csv", icon="merge", categories={"spreadsheet"},
        when=_is(CSV_EXTS), multi="combine", mode="quick",
        params=[P.bool("add_source", "docs.prm.add_source", False, help="docs.prm.add_source.help")],
        description="docs.desc.merge_csv", order=2)
def merge_csv(ctx: Context) -> Result:
    """Append CSV files that share the same header (columns may be in a different order)."""
    files = _sorted_files(ctx.files)
    add_source = ctx.pbool("add_source", False)
    header: list[str] | None = None
    merged: list[list[Any]] = []
    first_delim, first_bom = ",", False
    norm = lambda row: [str(c).strip().lower() for c in row]  # noqa: E731
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(i / len(files), ctx.t("docs.prog.file", n=i + 1, total=len(files)))
        try:
            rows, delim, dec = F.read_csv_rows(_src(f), kind(f), limit=CSV_LIMIT)
        except F.FormatError as exc:
            raise _fe(exc) from None
        if not rows:
            continue
        if header is None:
            header = list(rows[0])
            first_delim, first_bom = delim, dec.bom
            merged.append(header + ([ctx.t("docs.info.source_column")] if add_source else []))
            body = rows[1:]
        else:
            this = norm(rows[0])
            want = norm(header)
            if this == want:
                body = rows[1:]
            elif sorted(this) == sorted(want) and len(set(this)) == len(this):
                index = [this.index(col) for col in want]
                body = [[row[j] if j < len(row) else "" for j in index] for row in rows[1:]]
            else:
                raise UserError("docs.err.header_mismatch", name=f.name, first=files[0].name)
        for row in body:
            merged.append(list(row) + ([f.name] if add_source else []))
    if header is None:
        raise UserError("docs.err.empty_sheet")
    produced = E.tmp(ctx, "merged.csv")
    F.write_csv_rows(produced, merged, delimiter=first_delim, bom=first_bom)
    out = deliver(ctx, produced, ctx.output_path(files[0], ext="csv", suffix="sfx.merged"))
    return ctx.result(out, message=ctx.t("docs.msg.rows_merged", rows=_num(ctx, len(merged) - 1), n=len(files)))


@action(id="spreadsheet.merge.xlsx", label="docs.act.combine_xlsx", icon="library_books", categories={"spreadsheet"},
        multi="combine", requires=[("py:openpyxl", "soffice")], description="docs.desc.combine_xlsx", order=1)
def combine_xlsx(ctx: Context) -> Result:
    """One workbook with a sheet per file (``file - sheet`` for multi-sheet files)."""
    files = _sorted_files(ctx.files)
    sheets: list[F.Sheet] = []
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(i / (len(files) + 1), ctx.t("docs.prog.file", n=i + 1, total=len(files)))
        file_sheets, meta = read_sheets(ctx, f)
        file_sheets = _infer_sheets(file_sheets, meta)
        for name, rows in file_sheets:
            sheets.append((f.stem if len(file_sheets) == 1 else f"{f.stem} - {name}", rows))
    produced = _write_xlsx_any(ctx, sheets)
    out = deliver(ctx, produced, ctx.output_path(files[0], ext="xlsx", suffix="sfx.merged"))
    return ctx.result(out, message=i18n.tn("docs.msg.sheets", len(sheets), ctx.lang))


# ============================================================================== presentations

def _slides(ctx: Context, f: FileInfo) -> list[dict[str, Any]]:
    _check_office(f)
    src = _src(f)
    ctx.progress(None, ctx.t("docs.prog.reading"))
    try:
        office = F.office_kind(src)
        if office == "ooxml":
            return F.pptx_slides(src)
        if office == "odf":
            return F.odp_slides(src)
        if not tools.have("soffice"):
            raise MissingTool("soffice")
        return F.pptx_slides(_lo_single(ctx, f, LO_TARGETS["pptx"], "pptx"))
    except F.FormatError as exc:
        raise _fe(exc) from None


def _slide_heading(ctx: Context, n: int, slide: dict[str, Any]) -> str:
    title = (slide.get("title") or "").replace("\n", " ").strip()
    base = ctx.t("docs.info.slide_n", n=n)
    return f"{base} — {title}" if title else base


@action(id="presentation.convert.pdf", label="PDF", text="PDF", icon="picture_as_pdf", categories={"presentation"},
        mode="quick", params=[P.bool("notes", "docs.prm.notes_pages", False, help="docs.prm.notes_pages.help"),
                              *_PDF_OPTION_PARAMS[:1]],
        requires=["soffice"], order=1)
def slides_to_pdf(ctx: Context) -> Result:
    """Presentation → PDF (optionally with the notes pages)."""
    return _convert_office_pdf(ctx)


for _target, _order in (("pptx", 2), ("odp", 3)):
    def _make(target: str) -> Callable[[Context], Result]:
        def run(ctx: Context) -> Result:
            f = ctx.file
            _check_office(f)
            return ctx.result(deliver(ctx, _lo_single(ctx, f, LO_TARGETS[target], target),
                                      ctx.output_path(f, ext=target)))

        run.__doc__ = f"Presentation → {target.upper()} with LibreOffice."
        run.__name__ = f"slides_to_{target}"
        return run

    action(id=f"presentation.convert.{_target}", label=_target.upper(), text=_target.upper(), icon="slideshow",
           categories={"presentation"}, when=_not(_target), requires=["soffice"], order=_order)(_make(_target))


_DPI_PARAM = P.int("dpi", "docs.prm.dpi", "cfg:pdf.dpi", min=36, max=600, step=10, unit="dpi")


@action(id="presentation.convert.png", label="docs.act.slides_png", text="PNG", icon="image",
        categories={"presentation"}, mode="quick", params=[_DPI_PARAM],
        requires=["soffice", ("py:pymupdf", "pdftoppm")], description="docs.desc.slide_images", order=4)
def slides_to_png(ctx: Context) -> Result:
    """One PNG per slide in ``<stem> (slide)/``."""
    return _pages_to_images(ctx, ctx.file, "png", "slide", "docs.sfx.slides", ctx.pint("dpi", 150))


@action(id="presentation.convert.jpg", label="docs.act.slides_jpg", text="JPG", icon="image",
        categories={"presentation"}, mode="quick", params=[_DPI_PARAM],
        requires=["soffice", ("py:pymupdf", "pdftoppm")], description="docs.desc.slide_images", order=5)
def slides_to_jpg(ctx: Context) -> Result:
    """One JPG per slide in ``<stem> (slide)/``."""
    return _pages_to_images(ctx, ctx.file, "jpg", "slide", "docs.sfx.slides", ctx.pint("dpi", 150))


@action(id="presentation.extract.text", label="docs.act.slides_text", icon="notes", categories={"presentation"},
        description="docs.desc.slides_text", order=20)
def slides_text(ctx: Context) -> Result:
    """Markdown outline: one ``##`` section per slide with its text and speaker notes."""
    f = ctx.file
    slides = _slides(ctx, f)
    parts = [f"# {f.stem}\n"]
    for n, slide in enumerate(slides, 1):
        parts.append(f"## {_slide_heading(ctx, n, slide)}\n")
        body = [f"- {p.strip()}".replace("\n", "  \n  ") for p in slide["paragraphs"] if p.strip()]
        if body:
            parts.append("\n".join(body) + "\n")
        if slide.get("notes"):
            notes = "\n".join(f"> {ln}" if ln.strip() else ">" for ln in slide["notes"].split("\n"))
            parts.append(f"> **{ctx.t('docs.info.notes')}**\n>\n{notes}\n")
    text = "\n".join(parts)
    out = ctx.output_path(f, ext="md", suffix="docs.sfx.text")
    return ctx.result(write_text(ctx, out, text), message=i18n.tn("docs.msg.slides", len(slides), ctx.lang))


@action(id="presentation.extract.notes", label="docs.act.slides_notes", icon="speaker_notes",
        categories={"presentation"}, order=21)
def slides_notes(ctx: Context) -> Result:
    """Speaker notes only (Markdown, one section per slide that has notes)."""
    f = ctx.file
    slides = _slides(ctx, f)
    parts = [f"# {f.stem} — {ctx.t('docs.info.notes')}\n"]
    count = 0
    for n, slide in enumerate(slides, 1):
        if slide.get("notes"):
            count += 1
            parts.append(f"## {_slide_heading(ctx, n, slide)}\n\n{slide['notes'].strip()}\n")
    if not count:
        raise UserError("docs.err.no_notes")
    out = ctx.output_path(f, ext="md", suffix="docs.sfx.notes")
    return ctx.result(write_text(ctx, out, "\n".join(parts)), message=i18n.tn("docs.msg.notes", count, ctx.lang))


@action(id="presentation.extract.images", label="docs.act.extract_images", icon="photo_library",
        categories={"presentation"}, order=22)
def slides_images(ctx: Context) -> Result:
    """Save the pictures embedded in a presentation."""
    return _office_images(ctx)


@action(id="presentation.compress", label="grp.compress", icon="compress", categories={"presentation"},
        when=_is(ZIP_PRES_EXTS), mode="quick", params=_COMPRESS_PARAMS, requires=["py:PIL"], replace_ok=True,
        description="docs.desc.office_compress", order=10)
def slides_compress(ctx: Context) -> Result:
    """Re-encode the pictures inside a PPTX/ODP and re-zip it."""
    return _office_compress(ctx)


@action(id="presentation.privacy.strip", label="docs.act.strip_metadata", icon="person_off",
        categories={"presentation"}, when=_is(ZIP_PRES_EXTS), replace_ok=True, description="docs.desc.strip_metadata",
        order=30)
def slides_strip(ctx: Context) -> Result:
    """Remove author, dates, company and comment authors from a PPTX/ODP."""
    return _office_strip(ctx)


@action(id="presentation.info", label="docs.act.stats", icon="query_stats", categories={"presentation"},
        undoable=False, order=60)
def slides_info(ctx: Context) -> Result:
    """Slides, hidden slides, slides with notes, words."""
    f = ctx.file
    slides = _slides(ctx, f)
    words = sum(F.count_words("\n".join([s.get("title", "")] + s["paragraphs"])) for s in slides)
    notes_words = sum(F.count_words(s.get("notes", "")) for s in slides)
    grp = "docs.info.grp_slides"
    rows = [
        _row(ctx, grp, "docs.info.slides", _num(ctx, len(slides))),
        _row(ctx, grp, "docs.info.hidden", _num(ctx, sum(1 for s in slides if s.get("hidden")))),
        _row(ctx, grp, "docs.info.with_notes", _num(ctx, sum(1 for s in slides if s.get("notes")))),
        _row(ctx, grp, "docs.info.words", _num(ctx, words)),
        _row(ctx, grp, "docs.info.notes_words", _num(ctx, notes_words)),
    ]
    return ctx.result(kind="info", info=rows, message=i18n.tn("docs.msg.slides", len(slides), ctx.lang))


# =================================================================================== markdown

_TITLE_RE = re.compile(r"(?m)^title\s*:")


def _md_has_title(path: Path) -> bool:
    try:
        meta, _body = F.split_front_matter(F.read_text(path, limit=TEXT_LIMIT).text)
    except F.FormatError:
        return False
    return bool(meta and _TITLE_RE.search(meta))


@action(id="markdown.convert.pdf", label="PDF", text="PDF", icon="picture_as_pdf", categories={"markdown"},
        mode="quick",
        params=[P.select("engine", "docs.prm.engine", [("auto", "docs.opt.engine_auto"), ("typst", "Typst"),
                                                       ("weasyprint", "WeasyPrint"), ("xelatex", "XeLaTeX"),
                                                       ("tectonic", "Tectonic"), ("libreoffice", "LibreOffice")],
                         default="auto", help="docs.prm.engine.help"),
                _pdf_paper_param(), P.bool("toc", "docs.prm.toc", False)],
        # typst/XeLaTeX/Tectonic run through pandoc; WeasyPrint/LibreOffice also accept python-markdown HTML
        requires=[("pandoc", "py:markdown"), ("typst", "weasyprint", "xelatex", "tectonic", "soffice"),
                  ("pandoc", "weasyprint", "soffice")],
        description="docs.desc.md_pdf", order=1)
def markdown_to_pdf(ctx: Context) -> Result:
    """Markdown → PDF (typst → WeasyPrint → LaTeX → LibreOffice), Italian/English hyphenation."""
    f = ctx.file
    produced = E.tmp(ctx, "out.pdf")
    E.markdown_to_pdf(ctx, _src(f), produced, title=f.stem, lang=ctx.lang, paper=ctx.pstr("paper", "auto"),
                      engine=ctx.pstr("engine", "auto"), toc=ctx.pbool("toc", False), base=E.resource_dir(ctx, f))
    return ctx.result(deliver(ctx, produced, ctx.output_path(f, ext="pdf")))


@action(id="markdown.convert.html", label="HTML", text="HTML", icon="html", categories={"markdown"}, mode="quick",
        params=[P.bool("toc", "docs.prm.toc", False)], requires=[("pandoc", "py:markdown")],
        description="docs.desc.md_html", order=2)
def markdown_to_html(ctx: Context) -> Result:
    """Standalone HTML page with a readable stylesheet and embedded local pictures."""
    f = ctx.file
    page = E.markdown_html(ctx, _src(f), title=f.stem, lang=ctx.lang, base=E.resource_dir(ctx, f),
                           css=E.document_css(print_mode=False), embed=True, toc=ctx.pbool("toc", False))
    return ctx.result(write_text(ctx, ctx.output_path(f, ext="html"), page))


def _md_office(ctx: Context, target: str) -> Result:
    f = ctx.file
    base = E.resource_dir(ctx, f)
    produced = E.tmp(ctx, f"out.{target}")
    if tools.have("pandoc"):
        meta: dict[str, Any] = {"lang": ctx.lang}
        if target == "epub" and not _md_has_title(_src(f)):
            meta["title"] = f.stem
        writer = {"epub": "epub3", "rtf": "rtf"}.get(target, target)
        args: list[Any] = [str(_src(f)), "-f", E.MD_READER, "-t", writer, "--metadata-file",
                           str(E.metadata_file(ctx, meta)), "--lua-filter", str(E.remote_images_filter(ctx)),
                           "-o", str(produced)]
        if target == "rtf":
            args.append("-s")
        E.pandoc_run(ctx, args, cwd=base)
    elif target in ("docx", "odt") and tools.have("soffice") and tools.have("py:markdown"):
        page = E.markdown_html(ctx, _src(f), title=f.stem, lang=ctx.lang, base=base, css=E.document_css(), embed=True)
        html_path = E.tmp(ctx, "page.html")
        html_path.write_text(page, encoding="utf-8")
        produced = E.lo_convert(ctx, html_path, LO_TARGETS[target], out_ext=target, src_ext="html",
                                infilter="HTML (StarWriter)")[0]
    else:
        raise MissingTool("pandoc")
    return ctx.result(deliver(ctx, produced, ctx.output_path(f, ext=target)))


# without pandoc, DOCX/ODT go through python-markdown HTML + LibreOffice (both needed)
for _target, _order, _icon, _req in (("docx", 3, "description", [("pandoc", "soffice"), ("pandoc", "py:markdown")]),
                                     ("odt", 4, "description", [("pandoc", "soffice"), ("pandoc", "py:markdown")]),
                                     ("epub", 5, "menu_book", ["pandoc"]), ("rtf", 9, "description", ["pandoc"])):
    def _make_md(target: str) -> Callable[[Context], Result]:
        def run(ctx: Context) -> Result:
            return _md_office(ctx, target)

        run.__doc__ = f"Markdown → {target.upper()} with pandoc."
        run.__name__ = f"markdown_to_{target}"
        return run

    action(id=f"markdown.convert.{_target}", label=_target.upper(), text=_target.upper(), icon=_icon,
           categories={"markdown"}, requires=_req, order=_order)(_make_md(_target))


@action(id="markdown.convert.txt", label="TXT", text="TXT", icon="text_snippet", categories={"markdown"}, order=6)
def markdown_to_txt(ctx: Context) -> Result:
    """Plain text without Markdown markup (pandoc when installed, built-in otherwise)."""
    f = ctx.file
    out = ctx.output_path(f, ext="txt")
    if tools.have("pandoc"):
        produced = E.tmp(ctx, "out.txt")
        E.pandoc_run(ctx, [str(_src(f)), "-f", E.MD_READER, "-t", "plain", "--wrap=none", "-o", str(produced)])
        if produced.exists() and produced.stat().st_size > 0:
            return ctx.result(deliver(ctx, produced, out))
    return ctx.result(write_text(ctx, out, F.strip_markdown(_read(ctx, f).text)))


def _md_slides(ctx: Context, writer: str, ext: str, extra: Sequence[str] = (), *,
               timeout: float = E.PANDOC_TIMEOUT) -> Result:
    f = ctx.file
    produced = E.tmp(ctx, f"slides.{ext}")
    meta: dict[str, Any] = {"lang": ctx.lang}
    if not _md_has_title(_src(f)):
        meta["pagetitle"] = f.stem
    args: list[Any] = [str(_src(f)), "-f", E.MD_READER, "-t", writer, "--metadata-file", str(E.metadata_file(ctx, meta)),
                       "--lua-filter", str(E.remote_images_filter(ctx)), "-o", str(produced), *extra]
    E.pandoc_run(ctx, args, cwd=E.resource_dir(ctx, f), timeout=timeout, label=ctx.t("docs.prog.slides"))
    return ctx.result(deliver(ctx, produced, ctx.output_path(f, ext=ext, suffix="docs.sfx.slides")))


@action(id="markdown.slides.pdf", label="PDF", text="PDF", icon="slideshow", categories={"markdown"},
        requires=["pandoc", ("xelatex", "tectonic")], description="docs.desc.slides_pdf", order=1)
def markdown_slides_pdf(ctx: Context) -> Result:
    """Beamer slides (each ``##`` heading starts a slide)."""
    engine = "xelatex" if tools.have("xelatex") else "tectonic"
    return _md_slides(ctx, "beamer", "pdf", [f"--pdf-engine={engine}", "-V", "aspectratio=169"],
                      timeout=E.LATEX_TIMEOUT)


@action(id="markdown.slides.pptx", label="PPTX", text="PPTX", icon="slideshow", categories={"markdown"},
        requires=["pandoc"], order=2)
def markdown_slides_pptx(ctx: Context) -> Result:
    """PowerPoint deck from Markdown headings."""
    return _md_slides(ctx, "pptx", "pptx")


@action(id="markdown.slides.html", label="docs.act.revealjs", text="HTML", icon="slideshow", categories={"markdown"},
        requires=["pandoc"], description="docs.desc.revealjs", order=3)
def markdown_slides_html(ctx: Context) -> Result:
    """reveal.js presentation (loads reveal.js from its CDN when opened)."""
    return _md_slides(ctx, "revealjs", "html", ["-s"])


def _toc(ctx: Context, f: FileInfo) -> tuple[str, str, int]:
    text = _read(ctx, f).text
    heads = F.markdown_headings(text)
    toc = F.toc_markdown(heads)
    if not toc:
        raise UserError("docs.err.no_headings")
    return text, toc, len(heads)


@action(id="markdown.toc", label="docs.act.toc", icon="toc", categories={"markdown"}, multi="single",
        undoable=False, description="docs.desc.toc", order=40)
def markdown_toc(ctx: Context) -> Result:
    """Table of contents (Markdown list of links) shown as copyable text."""
    _text, toc, n = _toc(ctx, ctx.file)
    return ctx.result(kind="text", text=toc + "\n", message=i18n.tn("docs.msg.headings", n, ctx.lang))


@action(id="markdown.toc.insert", label="docs.act.toc_insert", icon="format_list_numbered", categories={"markdown"},
        replace_ok=True, description="docs.desc.toc_insert", order=41)
def markdown_toc_insert(ctx: Context) -> Result:
    """Insert (or refresh) a ``<!-- toc -->`` block after the title."""
    f = ctx.file
    text, toc, _n = _toc(ctx, f)
    new = F.insert_toc(text, toc, title=ctx.t("docs.info.toc_title"))
    out = write_text(ctx, ctx.output_path(f, suffix="docs.sfx.toc"), new)
    return ctx.result(ctx.replace_original(f, out))


_MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\([^)]+\)")
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


@action(id="markdown.stats", label="docs.act.stats", icon="query_stats", categories={"markdown"}, undoable=False,
        order=50)
def markdown_stats(ctx: Context) -> Result:
    """Words, characters, headings, links, pictures, code blocks, reading time."""
    f = ctx.file
    text = _read(ctx, f).text
    plain = F.strip_markdown(text)
    stats = F.text_stats(plain)
    _meta, body = F.split_front_matter(text)
    fences = sum(1 for _i, line, inside in F._iter_outside_fences(body.splitlines()) if inside
                 and F._FENCE_RE.match(line)) // 2
    grp = "docs.info.grp_text"
    rows = [
        _row(ctx, grp, "docs.info.words", _num(ctx, stats["words"])),
        _row(ctx, grp, "docs.info.chars", _num(ctx, stats["chars"])),
        _row(ctx, grp, "docs.info.chars_no_spaces", _num(ctx, stats["chars_no_spaces"])),
        _row(ctx, grp, "docs.info.reading", _minutes(ctx, stats["reading_minutes"])),
        _row(ctx, "docs.info.grp_structure", "docs.info.headings", _num(ctx, len(F.markdown_headings(text)))),
        _row(ctx, "docs.info.grp_structure", "docs.info.links", _num(ctx, len(_MD_LINK_RE.findall(body)))),
        _row(ctx, "docs.info.grp_structure", "docs.info.images", _num(ctx, len(_MD_IMG_RE.findall(body)))),
        _row(ctx, "docs.info.grp_structure", "docs.info.code_blocks", _num(ctx, fences)),
    ]
    return ctx.result(kind="info", info=rows, message=ctx.t("docs.msg.words", n=_num(ctx, stats["words"])))


@action(id="markdown.copy.html", label="docs.act.copy_html", icon="content_paste", categories={"markdown"},
        multi="single", undoable=False, requires=["wl-copy", ("pandoc", "py:markdown")],
        description="docs.desc.copy_html", order=31)
def markdown_copy_html(ctx: Context) -> Result:
    """Copy the rendered Markdown as rich text (``text/html``) to paste into mail or office apps."""
    f = ctx.file
    if tools.have("pandoc"):
        res = E.pandoc_run(ctx, [str(_src(f)), "-f", E.MD_READER, "-t", "html5", "--lua-filter",
                                 str(E.remote_images_filter(ctx))], cwd=E.resource_dir(ctx, f))
        fragment = res.stdout
    else:
        page = E.markdown_html(ctx, _src(f), title=f.stem, lang=ctx.lang, base=E.resource_dir(ctx, f), css="",
                               embed=False)
        m = re.search(r"<body>(.*)</body>", page, re.S)
        fragment = m.group(1) if m else page
    if not util.copy_text(fragment, mime="text/html"):
        raise UserError("docs.err.clipboard")
    return ctx.result(kind="none", message=ctx.t("docs.msg.copied_html"))


@action(id="markdown.merge.md", label="docs.act.merge_md", icon="merge", categories={"markdown"}, multi="combine",
        description="docs.desc.merge_md", order=2)
def merge_markdown(ctx: Context) -> Result:
    """Concatenate Markdown files (front matter kept only from the first one)."""
    files = _sorted_files(ctx.files)
    parts: list[str] = []
    for i, f in enumerate(files):
        ctx.check_cancel()
        text = _read(ctx, f).text.replace("\r\n", "\n")
        if i:
            _meta, text = F.split_front_matter(text)
        parts.append(text.strip("\n"))
    out = ctx.output_path(files[0], ext="md", suffix="sfx.merged")
    return ctx.result(write_text(ctx, out, "\n\n".join(parts) + "\n"),
                      message=i18n.tn("docs.msg.files_merged", len(files), ctx.lang))


# ============================================================================== text and code

def _text_like(files: list[FileInfo]) -> bool:
    return all(f.category in TEXTUAL or (f.category == "spreadsheet" and kind(f) in CSV_EXTS) for f in files)


TEXT_TOOL_CATS = set(TEXTUAL) | {"spreadsheet"}


def _encode_like(ctx: Context, dec: F.Decoded, text: str) -> bytes:
    """Encode ``text`` like the original (same codec and BOM); UTF-8 when that is impossible."""
    bom = {"utf-8": b"\xef\xbb\xbf", "utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff",
           "utf-32-le": b"\xff\xfe\x00\x00", "utf-32-be": b"\x00\x00\xfe\xff"}
    try:
        data = text.encode(dec.encoding)
        if dec.bom:
            data = bom.get(dec.encoding.lower(), b"") + data
        if "�" in text and not dec.confident:
            raise UnicodeEncodeError(dec.encoding, text, 0, 1, "replacement characters")
        return data
    except (UnicodeEncodeError, LookupError):
        ctx.warn(ctx.t("docs.warn.saved_utf8", enc=dec.label))
        return text.encode("utf-8")


def _text_rewrite(ctx: Context, transform: Callable[[str], str], suffix: str, unchanged_msg: str) -> Result:
    f = ctx.file
    dec = _read(ctx, f)
    new = transform(dec.text)
    if new == dec.text:
        return ctx.result(kind="none", message=ctx.t(unchanged_msg))
    out = write_bytes(ctx, ctx.output_path(f, suffix=suffix), _encode_like(ctx, dec, new))
    return ctx.result(ctx.replace_original(f, out))


@action(id="text.encoding.utf8", label="UTF-8", text="UTF-8", icon="translate", categories=TEXT_TOOL_CATS,
        when=_text_like, replace_ok=True, mixed_ok=True, description="docs.desc.utf8", order=30)
def text_to_utf8(ctx: Context) -> Result:
    """Re-encode as UTF-8 without BOM (source encoding detected with charset-normalizer)."""
    f = ctx.file
    dec = _read(ctx, f)
    if dec.is_utf8:
        return ctx.result(kind="none", message=ctx.t("docs.msg.already_utf8"))
    if not dec.confident:
        ctx.warn(ctx.t("docs.warn.encoding_guess", enc=dec.label))
    if "�" in dec.text:
        ctx.warn(ctx.t("docs.warn.replacement_chars"))
    out = write_text(ctx, ctx.output_path(f, suffix="docs.sfx.utf8"), dec.text)
    return ctx.result(ctx.replace_original(f, out), message=ctx.t("docs.msg.converted_from", enc=dec.label))


@action(id="text.line_endings.lf", label="docs.act.eol_lf", text="LF", icon="wrap_text", categories=TEXT_TOOL_CATS,
        when=_text_like, replace_ok=True, mixed_ok=True, description="docs.desc.eol_lf", order=31)
def text_eol_lf(ctx: Context) -> Result:
    """Unix line endings (LF); encoding and BOM are kept."""
    return _text_rewrite(ctx, lambda t: F.convert_eol(t, "lf"), "docs.sfx.lf", "docs.msg.eol_unchanged")


@action(id="text.line_endings.crlf", label="docs.act.eol_crlf", text="CRLF", icon="wrap_text",
        categories=TEXT_TOOL_CATS, when=_text_like, replace_ok=True, mixed_ok=True, description="docs.desc.eol_crlf", order=32)
def text_eol_crlf(ctx: Context) -> Result:
    """Windows line endings (CRLF); encoding and BOM are kept."""
    return _text_rewrite(ctx, lambda t: F.convert_eol(t, "crlf"), "docs.sfx.crlf", "docs.msg.eol_unchanged")


@action(id="text.clean.trailing", label="docs.act.trailing", icon="format_clear", categories=TEXTUAL,
        replace_ok=True, description="docs.desc.trailing", order=33)
def text_trailing(ctx: Context) -> Result:
    """Remove spaces and tabs at the end of lines (Markdown hard breaks become ``\\``)."""
    md = ctx.file.category == "markdown"
    return _text_rewrite(ctx, lambda t: F.strip_trailing(t, markdown=md)[0], "docs.sfx.trimmed",
                         "docs.msg.no_trailing")


@action(id="text.stats", label="docs.act.stats", icon="query_stats", categories={"text", "code", "data", "subtitle"},
        undoable=False, order=50)
def text_stats(ctx: Context) -> Result:
    """Lines, words, characters, bytes, encoding, line endings (and language for code)."""
    f = ctx.file
    dec = _read(ctx, f)
    stats = F.text_stats(dec.text)
    grp = "docs.info.grp_text"
    rows = [
        _row(ctx, grp, "docs.info.lines", _num(ctx, stats["lines"])),
        _row(ctx, grp, "docs.info.words", _num(ctx, stats["words"])),
        _row(ctx, grp, "docs.info.chars", _num(ctx, stats["chars"])),
        _row(ctx, grp, "docs.info.chars_no_spaces", _num(ctx, stats["chars_no_spaces"])),
        _row(ctx, grp, "docs.info.blank_lines", _num(ctx, stats["blank_lines"])),
        _row(ctx, grp, "docs.info.longest_line", _num(ctx, stats["longest_line"])),
        _row(ctx, "docs.info.grp_file", "docs.info.bytes", _num(ctx, f.size)),
        _row(ctx, "docs.info.grp_file", "docs.info.encoding", dec.label),
        _row(ctx, "docs.info.grp_file", "docs.info.line_endings", ctx.t(f"docs.info.eol.{F.detect_eol(dec.text).lower()}")),
    ]
    if f.category == "code":
        lang = E.language_name(f.name, dec.text)
        if lang:
            rows.insert(0, _row(ctx, grp, "docs.info.language", lang))
    elif f.category == "text":
        rows.append(_row(ctx, grp, "docs.info.reading", _minutes(ctx, stats["reading_minutes"])))
    return ctx.result(kind="info", info=rows, message=i18n.tn("docs.msg.lines", stats["lines"], ctx.lang))


#: Clipboard copies larger than this are refused (Wayland clients choke on huge selections).
CLIPBOARD_LIMIT = 16 * 1024 * 1024


@action(id="text.copy", label="docs.act.copy_text", icon="content_copy", categories=TEXT_TOOL_CATS, multi="any",
        when=_text_like, undoable=False, mixed_ok=True, requires=["wl-copy"], description="docs.desc.copy_text", order=20)
def text_copy(ctx: Context) -> Result:
    """Copy the file contents (several files joined by a blank line) to the clipboard."""
    texts = []
    total = 0
    for f in ctx.files:
        total += f.size
        if total > CLIPBOARD_LIMIT:
            raise UserError("docs.err.too_large_clipboard", limit=util.human_size(CLIPBOARD_LIMIT, ctx.lang))
        texts.append(_read(ctx, f, limit=CLIPBOARD_LIMIT).text)
    text = "\n\n".join(t.rstrip("\n") for t in texts) + ("\n" if len(texts) > 1 else "")
    if len(texts) == 1:
        text = texts[0]
    if not util.copy_text(text):
        raise UserError("docs.err.clipboard")
    return ctx.result(kind="none", message=ctx.t("docs.msg.copied", n=_num(ctx, len(text))))


def _lexer_alias(f: FileInfo, text: str) -> str:
    if f.category != "code":
        return ""
    try:
        lexer = E._lexer(f.name, text, False)
    except ImportError:
        return f.ext
    aliases = getattr(lexer, "aliases", None) or []
    return aliases[0] if aliases and aliases[0] not in ("text", "output") else (f.ext or "")


def _fence(text: str, lang: str) -> str:
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=0)
    fence = "`" * max(3, longest + 1)
    body = text if text.endswith("\n") else text + "\n"
    return f"{fence}{lang}\n{body}{fence}\n"


_MD_PUNCT_RE = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~^$&])")


def _text_as_markdown(text: str) -> str:
    """Plain text as Markdown that renders identically (punctuation escaped, line breaks kept)."""
    paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip("\n"))
    out = []
    for para in paragraphs:
        lines = []
        for line in para.split("\n"):
            indent = len(line) - len(line.lstrip(" \t"))
            body = _MD_PUNCT_RE.sub(r"\\\1", line.strip(" \t"))
            lines.append(" " * indent + body)
        out.append("\\\n".join(lines))
    return "\n\n".join(out) + "\n"


@action(id="text.convert.pdf", label="PDF", text="PDF", icon="picture_as_pdf", categories={"text", "code"},
        mode="quick",
        params=[P.bool("line_numbers", "docs.prm.line_numbers", lambda files: all(f.category == "code" for f in files)),
                P.bool("monospace", "docs.prm.monospace", lambda files: all(f.category == "code" for f in files)),
                P.int("font_size", "docs.prm.font_size", 9, min=6, max=16, unit="pt"), _pdf_paper_param()],
        requires=[("weasyprint", "soffice", "py:pymupdf")], description="docs.desc.text_pdf", order=1)
def text_to_pdf(ctx: Context) -> Result:
    """Text or code → PDF (Pygments colours, optional line numbers, wrapped long lines)."""
    f = ctx.file
    produced = E.tmp(ctx, "out.pdf")
    _text_pdf(ctx, f, produced, line_numbers=ctx.pbool("line_numbers", f.category == "code"),
              paper=ctx.pstr("paper", "auto"), font_pt=float(ctx.pint("font_size", 9)),
              monospace=ctx.pbool("monospace", f.category == "code"))
    return ctx.result(deliver(ctx, produced, ctx.output_path(f, ext="pdf")))


@action(id="text.convert.html", label="HTML", text="HTML", icon="html", categories={"text", "code"}, mode="quick",
        when=_not("html", "htm", "xhtml"),
        params=[P.bool("line_numbers", "docs.prm.line_numbers", lambda files: all(f.category == "code" for f in files))],
        description="docs.desc.text_html", order=2)
def text_to_html(ctx: Context) -> Result:
    """Highlighted HTML page (Pygments when installed)."""
    f = ctx.file
    if f.size > RENDER_LIMIT:
        raise UserError("docs.err.too_large_render", limit=util.human_size(RENDER_LIMIT, ctx.lang))
    dec = _read(ctx, f)
    page = E.code_html(dec.text, filename=f.name, title=f.name, lang=ctx.lang,
                       line_numbers=ctx.pbool("line_numbers", f.category == "code"), plain=f.category == "text",
                       monospace=True, print_mode=False, highlight=f.size <= HIGHLIGHT_LIMIT)
    return ctx.result(write_text(ctx, ctx.output_path(f, ext="html"), page))


@action(id="text.convert.md", label="docs.act.markdown", text="MD", icon="article", categories={"text", "code"},
        description="docs.desc.text_md", order=3)
def text_to_markdown(ctx: Context) -> Result:
    """Markdown: code in a fenced block with its language, text escaped so it renders the same."""
    f = ctx.file
    dec = _read(ctx, f)
    if f.category == "code":
        md = f"# {f.name}\n\n" + _fence(dec.text.replace("\r\n", "\n"), _lexer_alias(f, dec.text))
    else:
        md = _text_as_markdown(dec.text)
    return ctx.result(write_text(ctx, ctx.output_path(f, ext="md"), md))


def _text_docx_python(ctx: Context, f: FileInfo, text: str, out: Path) -> None:
    import docx  # type: ignore
    from docx.shared import Pt  # type: ignore

    document = docx.Document()
    code = f.category == "code"
    normal = document.styles["Normal"]
    if code:
        normal.font.name = "DejaVu Sans Mono"
        normal.font.size = Pt(9)
    normal.paragraph_format.space_after = Pt(0 if code else 6)
    clean = F._strip_xml_illegal(text.replace("\r\n", "\n").replace("\r", "\n"))
    if code:
        for line in clean.rstrip("\n").split("\n"):
            ctx.check_cancel()
            document.add_paragraph(line.expandtabs(4))
    else:
        for para in re.split(r"\n\s*\n", clean.strip("\n")):
            p = document.add_paragraph()
            lines = para.split("\n")
            for i, line in enumerate(lines):
                run = p.add_run(line)
                if i < len(lines) - 1:
                    run.add_break()
    document.core_properties.title = f.stem
    document.save(str(out))


def _text_office(ctx: Context, target: str) -> Result:
    f = ctx.file
    dec = _read(ctx, f)
    produced = E.tmp(ctx, f"out.{target}")
    ctx.progress(None, ctx.t("docs.prog.convert"))
    if target == "docx" and tools.have("py:docx"):
        _text_docx_python(ctx, f, dec.text, produced)
    elif tools.have("soffice"):
        plain = E.tmp(ctx, "text.txt")
        plain.write_text(dec.text, encoding="utf-8")
        produced = E.lo_convert(ctx, plain, LO_TARGETS[target], out_ext=target, src_ext="txt",
                                infilter="Text (encoded):UTF8")[0]
    elif tools.have("pandoc"):
        md = E.tmp(ctx, "text.md")
        md.write_text(_fence(dec.text, _lexer_alias(f, dec.text)) if f.category == "code"
                      else _text_as_markdown(dec.text), encoding="utf-8")
        E.pandoc_run(ctx, [str(md), "-f", "markdown", "-t", target, "-o", str(produced)])
    else:
        raise MissingTool("py:docx" if target == "docx" else "soffice")
    return ctx.result(deliver(ctx, produced, ctx.output_path(f, ext=target)))


@action(id="text.convert.docx", label="DOCX", text="DOCX", icon="description", categories={"text", "code"},
        requires=[("py:docx", "soffice", "pandoc")], order=4)
def text_to_docx(ctx: Context) -> Result:
    """Word document (python-docx; LibreOffice or pandoc otherwise)."""
    return _text_office(ctx, "docx")


@action(id="text.convert.odt", label="ODT", text="ODT", icon="description", categories={"text", "code"},
        requires=[("soffice", "pandoc")], order=5)
def text_to_odt(ctx: Context) -> Result:
    """OpenDocument text (LibreOffice; pandoc otherwise)."""
    return _text_office(ctx, "odt")


@action(id="text.merge.txt", label="docs.act.merge_txt", icon="merge", categories={"text", "code"},
        multi="combine", mode="quick", mixed_ok=True,
        params=[P.bool("separator", "docs.prm.separator", True, help="docs.prm.separator.help")],
        description="docs.desc.merge_txt", order=2)
def merge_text(ctx: Context) -> Result:
    """Concatenate text files into one UTF-8 file (optionally with a "===== name =====" line)."""
    files = _sorted_files(ctx.files)
    sep = ctx.pbool("separator", True)
    parts = []
    newline = "\n"
    for i, f in enumerate(files):
        ctx.check_cancel()
        text = _read(ctx, f).text
        if i == 0 and "\r\n" in text:
            newline = "\r\n"
        text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        parts.append((f"===== {f.name} =====\n" if sep else "") + text)
    merged = ("\n\n".join(parts) + "\n").replace("\n", newline)
    ext = kind(files[0]) if all(kind(f) == kind(files[0]) for f in files) and kind(files[0]) else "txt"
    out = ctx.output_path(files[0], ext=ext, suffix="sfx.merged")
    return ctx.result(write_text(ctx, out, merged), message=i18n.tn("docs.msg.files_merged", len(files), ctx.lang))


# ====================================================================================== data

def _data_family(f: FileInfo) -> str | None:
    return F.data_format(kind(f), f.name)


def _data_is(*families: str) -> Callable[[list[FileInfo]], bool]:
    fams = set(families)
    return lambda files: all(_data_family(f) in fams for f in files)


def _data_not(*families: str) -> Callable[[list[FileInfo]], bool]:
    fams = set(families)
    return lambda files: all(_data_family(f) is not None and _data_family(f) not in fams for f in files)


def _data_ok(files: list[FileInfo]) -> bool:
    return all(_data_family(f) is not None for f in files)


def load_data(ctx: Context, f: FileInfo, *, warn: bool = True) -> tuple[Any, str, list]:
    """Parse a data file (warnings shown on the HUD); readable errors with line/column."""
    ctx.progress(None, ctx.t("docs.prog.reading"))
    try:
        value, fmt, warnings = F.load_data(f.path, kind(f), name=f.name)
    except ImportError as exc:
        raise MissingTool("py:yaml" if "yaml" in str(exc) else "py:" + (exc.name or "yaml")) from None
    except F.FormatError as exc:
        if exc.key == "docs.err.too_large_text":
            raise UserError("docs.err.too_large_text", size=util.human_size(f.size, ctx.lang),
                            limit=util.human_size(int(exc.fmt.get("limit") or 0), ctx.lang)) from None
        raise _fe(exc) from None
    if warn:
        _warn_all(ctx, warnings)
    return value, fmt, warnings


def _dump(ctx: Context, value: Any, fmt: str) -> str:
    warnings: list = []
    try:
        text = F.dump_data(value, fmt, warnings)
    except ImportError:
        raise MissingTool("py:yaml") from None
    except F.FormatError as exc:
        raise _fe(exc) from None
    _warn_all(ctx, warnings)
    return text


def _data_convert(ctx: Context, target: str) -> Result:
    f = ctx.file
    value, _fmt, _w = load_data(ctx, f)
    if target in ("json", "yaml", "toml", "xml"):
        text = _dump(ctx, value, target)
        return ctx.result(write_text(ctx, ctx.output_path(f, ext="yml" if target == "yaml" and kind(f) == "yml"
                                                          else target), text))
    rows = F.rows_from_data(value)
    if not rows or len(rows) < 2:
        raise UserError("docs.err.not_tabular")
    if target == "csv":
        produced = E.tmp(ctx, "out.csv")
        F.write_csv_rows(produced, rows)
        return ctx.result(deliver(ctx, produced, ctx.output_path(f, ext="csv")))
    produced = _write_xlsx_any(ctx, [(f.stem, rows)])
    return ctx.result(deliver(ctx, produced, ctx.output_path(f, ext="xlsx")))


for _target, _order, _when, _req in (("json", 1, _data_not("json"), []), ("yaml", 2, _data_not("yaml"), ["py:yaml"]),
                                     ("toml", 3, _data_not("toml"), []), ("xml", 4, _data_not("xml"), []),
                                     ("csv", 5, _data_ok, []), ("xlsx", 6, _data_ok, [("py:openpyxl", "soffice")])):
    def _make_data(target: str) -> Callable[[Context], Result]:
        def run(ctx: Context) -> Result:
            return _data_convert(ctx, target)

        run.__doc__ = f"Structured data → {target.upper()}."
        run.__name__ = f"data_to_{target}"
        return run

    action(id=f"data.convert.{_target}", label=_target.upper(), text=_target.upper(), icon="data_object",
           categories={"data"}, when=_when, requires=_req, order=_order,
           description="docs.desc.data_tabular" if _target in ("csv", "xlsx") else "")(_make_data(_target))


def _data_rewrite(ctx: Context, text: str | bytes, suffix: str) -> Result:
    f = ctx.file
    out = ctx.output_path(f, suffix=suffix)
    if isinstance(text, bytes):
        write_bytes(ctx, out, text)
    else:
        write_text(ctx, out, text)
    return ctx.result(ctx.replace_original(f, out))


@action(id="data.pretty", label="docs.act.pretty", icon="format_align_left", categories={"data"},
        when=_data_is("json", "xml", "plist"), replace_ok=True, description="docs.desc.pretty", order=10)
def data_pretty(ctx: Context) -> Result:
    """Indent JSON (2 spaces) or XML (comments, prefixes and DOCTYPE kept); binary plist → XML plist."""
    f = ctx.file
    fam = _data_family(f)
    if fam == "xml":
        try:
            text = F.xml_pretty(_src(f).read_bytes())
        except F.FormatError as exc:
            raise _fe(exc) from None
        return _data_rewrite(ctx, text, "sfx.formatted")
    value, _fmt, _w = load_data(ctx, f)
    if fam == "plist":
        import plistlib

        try:
            data = plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=False)
        except (TypeError, OverflowError) as exc:
            raise UserError("docs.err.write_failed", details=str(exc), fmt="plist") from None
        return _data_rewrite(ctx, data, "sfx.formatted")
    warnings: list = []
    text = F.json_pretty(value, warnings)
    _warn_all(ctx, warnings)
    return _data_rewrite(ctx, text, "sfx.formatted")


@action(id="data.minify", label="docs.act.minify", icon="compress", categories={"data"},
        when=_data_is("json", "xml"), replace_ok=True, description="docs.desc.minify", order=11)
def data_minify(ctx: Context) -> Result:
    """Compact JSON / XML (indentation and comments removed)."""
    f = ctx.file
    if _data_family(f) == "xml":
        try:
            text = F.xml_minify(_src(f).read_bytes())
        except F.FormatError as exc:
            raise _fe(exc) from None
    else:
        value, _fmt, _w = load_data(ctx, f)
        warnings: list = []
        text = F.json_minify(value, warnings)
        _warn_all(ctx, warnings)
    result = _data_rewrite(ctx, text, "sfx.minified")
    result.size_before = f.size
    return result


@action(id="data.validate", label="docs.act.validate", icon="rule", categories={"data"}, when=_data_ok,
        undoable=False, description="docs.desc.validate", order=12)
def data_validate(ctx: Context) -> Result:
    """Check the syntax; the result shows the error line/column or a summary of the content."""
    f = ctx.file
    fam = _data_family(f) or "?"
    label = F.FORMAT_LABELS.get(fam, fam.upper())
    grp = "docs.info.grp_validation"
    rows = [_row(ctx, grp, "docs.info.format", label)]
    try:
        value, _fmt, warnings = load_data(ctx, f, warn=False)
    except UserError as exc:
        if isinstance(exc, MissingTool):
            raise
        line, col = exc.fmt.get("line"), exc.fmt.get("col")
        rows.append(_row(ctx, grp, "docs.info.status", ctx.t("docs.info.invalid")))
        if line not in (None, "?"):
            rows.append(_row(ctx, grp, "docs.info.line", str(line)))
            rows.append(_row(ctx, grp, "docs.info.column", str(col)))
        rows.append(_row(ctx, grp, "docs.info.error", str(exc.fmt.get("error") or exc.text(ctx.lang))))
        msg = (ctx.t("docs.msg.invalid_at", fmt=label, line=line, col=col) if line not in (None, "?")
               else ctx.t("docs.msg.invalid", fmt=label))
        return ctx.result(kind="info", info=rows, message=msg)
    rows.append(_row(ctx, grp, "docs.info.status", ctx.t("docs.info.valid")))
    if isinstance(value, dict):
        rows.append(_row(ctx, grp, "docs.info.top_level", ctx.t("docs.info.object_keys", n=_num(ctx, len(value)))))
    elif isinstance(value, list):
        rows.append(_row(ctx, grp, "docs.info.top_level", ctx.t("docs.info.array_items", n=_num(ctx, len(value)))))
    for key, fmt in warnings:
        rows.append(_row(ctx, grp, "docs.info.warning", ctx.t(key, **fmt)))
    return ctx.result(kind="info", info=rows, message=ctx.t("docs.msg.valid", fmt=label))


@action(id="data.sort_keys", label="docs.act.sort_keys", icon="sort_by_alpha", categories={"data"},
        when=_data_is("json", "yaml", "toml"), replace_ok=True, description="docs.desc.sort_keys", order=13)
def data_sort_keys(ctx: Context) -> Result:
    """Sort object keys alphabetically at every level (same format)."""
    f = ctx.file
    value, fam, _w = load_data(ctx, f)
    text = _dump(ctx, F.sort_keys(value), F.NATIVE_OUTPUT.get(fam, "json"))
    return _data_rewrite(ctx, text, "sfx.sorted")


def _deep_merge(a: Any, b: Any, conflicts: list[int]) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if k in out:
                if not (isinstance(out[k], dict) and isinstance(v, dict)) and out[k] != v:
                    conflicts[0] += 1
                out[k] = _deep_merge(out[k], v, conflicts)
            else:
                out[k] = v
        return out
    return b


@action(id="data.merge.json", label="docs.act.merge_json", icon="merge", categories={"data"}, multi="combine",
        when=_data_ok, description="docs.desc.merge_json", order=1)
def merge_json(ctx: Context) -> Result:
    """Arrays are concatenated, objects deep-merged (later files win); anything else → an array."""
    files = _sorted_files(ctx.files)
    values = []
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(i / len(files), ctx.t("docs.prog.file", n=i + 1, total=len(files)))
        values.append(load_data(ctx, f)[0])
    if all(isinstance(v, list) for v in values):
        merged: Any = [item for v in values for item in v]
    elif all(isinstance(v, dict) for v in values):
        conflicts = [0]
        merged = {}
        for v in values:
            merged = _deep_merge(merged, v, conflicts)
        if conflicts[0]:
            ctx.warn(i18n.tn("docs.warn.merge_conflicts", conflicts[0], ctx.lang))
    else:
        merged = values
    text = _dump(ctx, merged, "json")
    out = ctx.output_path(files[0], ext="json", suffix="sfx.merged")
    return ctx.result(write_text(ctx, out, text), message=i18n.tn("docs.msg.files_merged", len(files), ctx.lang))


# ===================================================================================== ebooks

_EBOOK_DRM_RE = re.compile(r"\bDRM\b|locked|encrypted", re.I)


def _ebook_input(ctx: Context, f: FileInfo) -> Path:
    """The ebook path with the extension calibre expects (KEPUB is read as EPUB)."""
    k = kind(f)
    if k in ("epub", "kepub") and F.epub_drm(f.path):
        raise UserError("err.drm")
    wanted = {"kepub": "epub"}.get(k, k)
    if f.ext == wanted:
        return _src(f)
    # calibre picks the input format from the extension (and uses the name as a fallback title)
    folder = ctx.tmpdir() / "ebook-in"
    folder.mkdir(parents=True, exist_ok=True)
    staged = folder / util.safe_filename(f"{f.stem or 'book'}.{wanted}")
    if not staged.exists():
        E.link_or_copy(_src(f), staged)
    return staged


def _ebook_convert(ctx: Context, target: str) -> Result:
    f = ctx.file
    src = _ebook_input(ctx, f)
    produced = ctx.tmpdir() / f"converted.{target}"
    extra: list[str] = []
    if target == "pdf":
        extra = ["--paper-size", "letter" if E.paper_size() == "letter" else "a4", "--pdf-page-numbers"]
    try:
        E.ebook_convert(ctx, src, produced, extra=extra)
    except ToolError as exc:
        if _EBOOK_DRM_RE.search(exc.details or ""):
            raise UserError("err.drm", details=exc.details) from None
        raise
    return ctx.result(deliver(ctx, produced, ctx.output_path(f, ext=target)))


for _order, _target in enumerate(EBOOK_TARGETS, 1):
    def _make_ebook(target: str) -> Callable[[Context], Result]:
        def run(ctx: Context) -> Result:
            return _ebook_convert(ctx, target)

        run.__doc__ = f"Ebook → {target.upper()} with calibre."
        run.__name__ = f"ebook_to_{target}"
        return run

    _src_ok = (lambda t: lambda files: all(kind(f) in CALIBRE_INPUT and kind(f) != t for f in files))(_target)
    action(id=f"ebook.convert.{_target}", label=_target.upper(), text=_target.upper(),
           icon="picture_as_pdf" if _target == "pdf" else "menu_book", categories={"ebook"}, when=_src_ok,
           requires=["ebook-convert"], description="docs.desc.mobi" if _target == "mobi" else "",
           order=_order)(_make_ebook(_target))


def _sniff_image_ext(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.lstrip()[:5] in (b"<?xml", b"<svg ") or b"<svg" in data[:512]:
        return "svg"
    return "jpg"


@action(id="ebook.cover", label="docs.act.cover", icon="image", categories={"ebook"}, when=_not("djvu", "djv"),
        description="docs.desc.cover", order=20)
def ebook_cover(ctx: Context) -> Result:
    """Save the cover picture (EPUB/FB2/CBZ natively, other formats with calibre's ebook-meta)."""
    f = ctx.file
    k = kind(f)
    found: tuple[bytes, str] | None = None
    try:
        if k in ("epub", "kepub"):
            found = F.epub_cover(f.path)
        elif k in ("fb2", "fbz"):
            found = F.fb2_cover(f.path)
        elif k == "cbz":
            found = F.cbz_cover(f.path)
    except F.FormatError as exc:
        if k not in ("epub", "kepub", "fb2", "fbz", "cbz") or not tools.have("ebook-meta"):
            raise _fe(exc) from None
    if found is None and k not in ("epub", "kepub", "fb2", "fbz", "cbz"):
        if not tools.have("ebook-meta"):
            raise MissingTool("ebook-meta")
        cover = E.tmp(ctx, "cover.img")
        ctx.progress(None, ctx.t("docs.prog.reading"))
        ctx.run(["ebook-meta", str(_ebook_input(ctx, f)), f"--get-cover={cover}"], timeout=120,
                env={"QT_QPA_PLATFORM": "offscreen"})
        if cover.exists() and cover.stat().st_size > 0:
            data = cover.read_bytes()
            found = (data, _sniff_image_ext(data))
    if found is None:
        raise UserError("docs.err.no_cover")
    data, ext = found
    return ctx.result(write_bytes(ctx, ctx.output_path(f, ext=ext, suffix="docs.sfx.cover"), data))


_BOOK_FIELD_KEYS = {"title": "docs.info.title", "author": "docs.info.author", "publisher": "docs.info.publisher",
                    "language": "docs.info.lang", "date": "docs.info.date", "identifier": "docs.info.identifier",
                    "subject": "docs.info.subject", "series": "docs.info.series",
                    "description": "docs.info.description", "chapters": "docs.info.chapters",
                    "format": "docs.info.format", "pages": "docs.info.pages", "drm": "docs.info.drm"}


@action(id="ebook.info", label="docs.act.book_info", icon="info", categories={"ebook"}, undoable=False,
        description="docs.desc.book_info", order=30)
def ebook_info(ctx: Context) -> Result:
    """Title, author, language, publisher, series… (EPUB/FB2/CBZ natively, others via ebook-meta)."""
    f = ctx.file
    k = kind(f)
    ctx.progress(None, ctx.t("docs.prog.reading"))
    try:
        if k in ("epub", "kepub"):
            fields = F.epub_info(f.path)
            if F.epub_drm(f.path):
                fields.append(("drm", ctx.t("docs.info.yes")))
        elif k in ("fb2", "fbz"):
            fields = F.fb2_info(f.path)
        elif k == "cbz":
            fields = F.cbz_info(f.path)
        else:
            if not tools.have("ebook-meta"):
                raise MissingTool("ebook-meta")
            res = ctx.run(["ebook-meta", str(_ebook_input(ctx, f))], timeout=120, env={"QT_QPA_PLATFORM": "offscreen"})
            fields = F.parse_ebook_meta(res.stdout)
            fields.append(("format", k.upper()))
    except F.FormatError as exc:
        raise _fe(exc) from None
    if not fields:
        raise UserError("docs.err.no_metadata")
    rows = [{"group": ctx.t("docs.info.grp_book"), "key": ctx.t(_BOOK_FIELD_KEYS.get(key, "docs.info.format")),
             "value": value} for key, value in fields]
    title = next((v for k2, v in fields if k2 == "title"), f.stem)
    author = next((v for k2, v in fields if k2 == "author"), "")
    return ctx.result(kind="info", info=rows, message=f"{title} — {author}" if author else title)


@action(id="ebook.extract.images", label="docs.act.comic_pages", icon="photo_library", categories={"ebook"},
        when=_is("cbz"), order=40)
def comic_pages(ctx: Context) -> Result:
    """Save the pages of a CBZ comic into ``<stem> (pagine)/``."""
    f = ctx.file
    import zipfile

    try:
        zf = zipfile.ZipFile(f.path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UserError("err.damaged", details=str(exc)) from None
    with zf:
        images = F._comic_images(zf)
        if not images:
            raise UserError("docs.err.no_images")
        folder = ctx.output_dir(f, suffix="sfx.pages")
        width = max(3, len(str(len(images))))
        for i, info in enumerate(images, 1):
            ctx.check_cancel()
            ctx.progress(i / len(images), ctx.t("docs.prog.page", n=i, total=len(images)))
            ext = info.filename.rsplit(".", 1)[-1].lower()
            try:
                data = F._read_member(zf, info)
            except F.FormatError as exc:
                raise _fe(exc) from None
            (folder / f"page-{i:0{width}d}.{'jpg' if ext == 'jpeg' else ext}").write_bytes(data)
    return ctx.result(folder, message=i18n.tn("docs.msg.images", len(images), ctx.lang))


# ================================================================================== subtitles

def _sub_ok(files: list[FileInfo]) -> bool:
    return all(F.SUBTITLE_FORMATS.get(kind(f)) for f in files)


def _sub_fmt_not(target: str) -> Callable[[list[FileInfo]], bool]:
    return lambda files: all(F.SUBTITLE_FORMATS.get(kind(f)) not in (None, target) for f in files)


def _subs(ctx: Context, f: FileInfo) -> tuple[F.Decoded, str, list[F.Cue]]:
    dec = _read(ctx, f, limit=32 * 1024 * 1024)
    fmt = F.subtitle_format(kind(f), dec.text)
    if fmt is None:
        raise UserError("err.not_supported")
    try:
        return dec, fmt, F.parse_subtitles(dec.text, fmt)
    except F.FormatError as exc:
        raise _fe(exc) from None


def _sub_convert(ctx: Context, target: str) -> Result:
    f = ctx.file
    _dec, _fmt, cues = _subs(ctx, f)
    text = F.write_subtitles(cues, target, title=f.stem)
    if not text.strip():
        raise UserError("docs.err.no_cues")
    return ctx.result(write_text(ctx, ctx.output_path(f, ext=target), text),
                      message=i18n.tn("docs.msg.cues", len(cues), ctx.lang))


for _order, _target in enumerate(("srt", "vtt", "ass", "txt"), 1):
    def _make_sub(target: str) -> Callable[[Context], Result]:
        def run(ctx: Context) -> Result:
            return _sub_convert(ctx, target)

        run.__doc__ = f"Subtitles → {target.upper()} (pure Python, UTF-8)."
        run.__name__ = f"subtitles_to_{target}"
        return run

    action(id=f"subtitle.convert.{_target}", label="docs.act.plain_text" if _target == "txt" else _target.upper(),
           text=_target.upper(), icon="subtitles", categories={"subtitle"},
           when=_sub_ok if _target == "txt" else _sub_fmt_not(_target), order=_order)(_make_sub(_target))


def _sub_remap(ctx: Context, fn: Callable[[float], float], suffix: str) -> Result:
    f = ctx.file
    dec, fmt, _cues = _subs(ctx, f)
    try:
        text, kept, dropped = F.remap_subtitle_times(dec.text, fmt, fn)
    except F.FormatError as exc:
        raise _fe(exc) from None
    if dropped:
        ctx.warn(i18n.tn("docs.warn.cues_dropped", dropped, ctx.lang))
    if not kept:
        raise UserError("docs.err.no_cues")
    if F.detect_eol(dec.text) == "CRLF":
        text = F.convert_eol(text, "crlf")
    out = write_bytes(ctx, ctx.output_path(f, suffix=suffix), _encode_like(ctx, dec, text))
    return ctx.result(ctx.replace_original(f, out), message=i18n.tn("docs.msg.cues", kept, ctx.lang))


@action(id="subtitle.timing.shift", label="docs.act.shift", icon="more_time", categories={"subtitle"},
        when=_sub_ok, mode="dialog", dialog="form",
        params=[P.float("offset", "docs.prm.offset", 1.0, min=-36000.0, max=36000.0, step=0.1, unit="docs.unit.s",
                        help="docs.prm.offset.help")],
        replace_ok=True, description="docs.desc.shift", order=10)
def subtitle_shift(ctx: Context) -> Result:
    """Move every subtitle by ± seconds (cues pushed before 0 are dropped)."""
    offset = ctx.pfloat("offset", 0.0)
    if abs(offset) < 0.0005:
        raise UserError("docs.err.zero_offset")
    return _sub_remap(ctx, lambda t: t + offset, "docs.sfx.shifted")


_FPS = [("23.976", "23,976"), ("24", "24"), ("25", "25"), ("29.97", "29,97"), ("30", "30"), ("50", "50"),
        ("59.94", "59,94"), ("60", "60")]


@action(id="subtitle.timing.fps", label="docs.act.fps", icon="speed", categories={"subtitle"}, when=_sub_ok,
        mode="dialog", dialog="form",
        params=[P.select("from_fps", "docs.prm.from_fps", _FPS, default="23.976"),
                P.select("to_fps", "docs.prm.to_fps", _FPS, default="25")],
        replace_ok=True, description="docs.desc.fps", order=11)
def subtitle_fps(ctx: Context) -> Result:
    """Retime subtitles made for another frame rate (e.g. 23.976 → 25 fps)."""
    try:
        src_fps = float(str(ctx.p("from_fps", "23.976")).replace(",", "."))
        dst_fps = float(str(ctx.p("to_fps", "25")).replace(",", "."))
    except ValueError:
        raise UserError("err.invalid_param", param=ctx.t("docs.prm.from_fps")) from None
    if src_fps <= 0 or dst_fps <= 0 or not math.isfinite(src_fps) or not math.isfinite(dst_fps):
        raise UserError("err.invalid_param", param=ctx.t("docs.prm.from_fps"))
    if abs(src_fps - dst_fps) < 1e-6:
        raise UserError("docs.err.same_fps")
    factor = src_fps / dst_fps
    return _sub_remap(ctx, lambda t: t * factor, "docs.sfx.retimed")


@action(id="subtitle.clean.hi", label="docs.act.clean_hi", icon="hearing_disabled", categories={"subtitle"},
        when=_both(_sub_ok, _not("ass", "ssa")), replace_ok=True, description="docs.desc.clean_hi", order=12)
def subtitle_clean(ctx: Context) -> Result:
    """Remove [sound] / (sound) descriptions, music lines and SPEAKER: labels."""
    f = ctx.file
    _dec, fmt, cues = _subs(ctx, f)
    cleaned, changed = F.clean_hearing_impaired(cues)
    if not changed:
        return ctx.result(kind="none", message=ctx.t("docs.msg.nothing_to_clean"))
    if not cleaned:
        raise UserError("docs.err.no_cues")
    text = F.write_subtitles(cleaned, fmt, title=f.stem)
    out = write_text(ctx, ctx.output_path(f, suffix="sfx.clean"), text)
    return ctx.result(ctx.replace_original(f, out), message=i18n.tn("docs.msg.cues_cleaned", changed, ctx.lang))


# ============================================================================ multi and mixed

_COMBINE_CATS = set(DOC_FAMILY) | {"pdf", "image", "svg"}


def _has_doc(files: list[FileInfo]) -> bool:
    return any(f.category in DOC_FAMILY for f in files)


@action(id="docs.combine.pdf", label="docs.act.combine_pdf", icon="picture_as_pdf", categories=_COMBINE_CATS,
        multi="combine", mode="quick", mixed_ok=True, when=_has_doc,
        params=[P.select("order", "docs.prm.order", [("name", "docs.opt.order_name"), ("drop", "docs.opt.order_drop")],
                         default="name")],
        requires=[("py:pymupdf", "py:pypdf")], description="docs.desc.combine_pdf", order=0)
def combine_pdf(ctx: Context) -> Result:
    """Convert every file to PDF (documents, slides, sheets, Markdown, text, images) and merge them,
    one bookmark per file; files that cannot be converted are skipped with a warning."""
    files = _sorted_files(ctx.files) if ctx.pstr("order", "name") == "name" else list(ctx.files)
    parts: list[tuple[Path, str]] = []
    failed: list[str] = []
    missing: MissingTool | None = None
    total = len(files)
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(0.9 * i / total, ctx.t("docs.prog.file", n=i + 1, total=total))
        pdf = ctx.tmpdir() / f"part-{i:04d}.pdf"
        try:
            file_to_pdf(ctx, f, pdf)
            parts.append((pdf, f.stem))
        except UserError as exc:
            if isinstance(exc, MissingTool) and missing is None:
                missing = exc
            failed.append(f.name)
            ctx.warn(f"{f.name}: {exc.text(ctx.lang)}")
    if not parts:
        if missing is not None:
            raise missing
        raise UserError("docs.err.nothing_combined")
    produced = E.tmp(ctx, "combined.pdf")
    pages = E.merge_pdfs(ctx, parts, produced)
    out = deliver(ctx, produced, ctx.output_path(files[0], ext="pdf", suffix="sfx.merged"))
    message = ctx.t("docs.msg.combined", files=len(parts), pages=_num(ctx, pages))
    if failed:
        message += " · " + i18n.tn("docs.msg.skipped", len(failed), ctx.lang)
    return ctx.result(out, message=message)


@action(id="docs.mixed.pdf", label="docs.act.all_pdf", icon="dynamic_feed", categories=_COMBINE_CATS,
        multi="any", mixed_ok=True,
        when=lambda files: _has_doc(files) and len({f.category for f in files}) > 1,
        description="docs.desc.all_pdf", order=1)
def all_to_pdf(ctx: Context) -> Result:
    """Convert each file of a mixed selection to PDF (PDFs are left as they are)."""
    files = list(ctx.files)
    outputs: list[Path] = []
    failed = 0
    missing: MissingTool | None = None
    todo = [f for f in files if f.category != "pdf"]
    for i, f in enumerate(todo):
        ctx.check_cancel()
        ctx.progress(i / max(1, len(todo)), ctx.t("docs.prog.file", n=i + 1, total=len(todo)))
        pdf = ctx.tmpdir() / f"one-{i:04d}.pdf"
        try:
            file_to_pdf(ctx, f, pdf)
            outputs.append(deliver(ctx, pdf, ctx.output_path(f, ext="pdf")))
        except UserError as exc:
            if isinstance(exc, MissingTool) and missing is None:
                missing = exc
            failed += 1
            ctx.warn(f"{f.name}: {exc.text(ctx.lang)}")
    if not outputs:
        if missing is not None:
            raise missing
        raise UserError("docs.err.nothing_converted")
    message = i18n.tn("docs.msg.converted", len(outputs), ctx.lang)
    if failed:
        message += " · " + i18n.tn("docs.msg.skipped", failed, ctx.lang)
    return ctx.result(outputs, message=message)


_PAGE_BREAK = {"t": "RawBlock", "c": ["openxml", '<w:p><w:r><w:br w:type="page"/></w:r></w:p>']}


@action(id="docs.merge.docx", label="docs.act.merge_docx", icon="merge", categories={"document", "markdown", "text", "code"},
        multi="combine", mode="quick", mixed_ok=True,
        params=[P.bool("page_breaks", "docs.prm.page_breaks", True), P.bool("titles", "docs.prm.titles", False)],
        requires=["pandoc"], description="docs.desc.merge_docx", order=3)
def merge_docx(ctx: Context) -> Result:
    """Merge documents, Markdown and text into one DOCX with pandoc (page break between files)."""
    files = _sorted_files(ctx.files)
    work = E.tmp(ctx, "merge")
    work.mkdir()
    blocks: list[Any] = []
    api: Any = None
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(0.9 * i / len(files), ctx.t("docs.prog.file", n=i + 1, total=len(files)))
        if f.category in ("text", "code"):
            dec = _read(ctx, f)
            src = work / f"text-{i}.md"
            src.write_text(_fence(dec.text, _lexer_alias(f, dec.text)) if f.category == "code"
                           else _text_as_markdown(dec.text), encoding="utf-8")
            reader, cwd = "markdown", work
        else:
            src, reader = _pandoc_source(ctx, f)
            cwd = E.resource_dir(ctx, f)
        args: list[Any] = [str(src), "-f", reader, "-t", "json", f"--extract-media={work / f'media-{i}'}"]
        if f.category == "markdown":
            args += ["--lua-filter", str(E.remote_images_filter(ctx))]
        res = E.pandoc_run(ctx, args, cwd=cwd)
        try:
            doc = json.loads(res.stdout)
        except ValueError as exc:
            raise ToolError("err.tool_failed", details=str(exc), tool="pandoc", code="json") from None
        api = api or doc.get("pandoc-api-version")
        if i and ctx.pbool("page_breaks", True):
            blocks.append(_PAGE_BREAK)
        if ctx.pbool("titles", False):
            blocks.append({"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": f.stem}]]})
        blocks.extend(doc.get("blocks") or [])
    merged = {"pandoc-api-version": api, "meta": {}, "blocks": blocks}
    ast = work / "merged.json"
    ast.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    produced = work / "merged.docx"
    E.pandoc_run(ctx, [str(ast), "-f", "json", "-t", "docx", "-o", str(produced)], cwd=work)
    out = deliver(ctx, produced, ctx.output_path(files[0], ext="docx", suffix="sfx.merged"))
    return ctx.result(out, message=i18n.tn("docs.msg.files_merged", len(files), ctx.lang))


# ===================================================================================== layouts

group("document.convert", "grp.convert", "swap_horiz",
      ["document.convert.pdf", "document.convert.pdf_ps", "document.convert.pdf_xps", "document.convert.docx",
       "document.convert.odt", "document.convert.md", "document.convert.txt", "document.convert.txt_lo",
       "document.convert.html", "document.convert.epub", "document.convert.rtf", "document.convert.doc",
       "document.convert.png"],
      default=["document.convert.pdf", "document.convert.pdf_ps", "document.convert.pdf_xps"])
group("document.extract", "grp.extract", "unarchive", ["document.extract.images", "document.convert.txt",
                                                       "document.convert.txt_lo", "document.convert.md"],
      default="document.extract.images")
layout("document", ["document.convert", "document.convert.pdf", "document.convert.pdf_ps", "document.convert.pdf_xps",
                    "document.compress", "document.privacy.strip", "document.extract", "document.secure.pdf",
                    "document.info"])
layout("multi:document", ["docs.combine.pdf", "document.convert", "docs.merge.docx", "document.compress",
                          "document.privacy.strip", "document.info"])

group("spreadsheet.convert", "grp.convert", "swap_horiz",
      ["spreadsheet.convert.xlsx", "spreadsheet.convert.ods", "spreadsheet.convert.csv", "spreadsheet.convert.pdf",
       "spreadsheet.convert.json", "spreadsheet.convert.md", "spreadsheet.convert.html", "spreadsheet.convert.tsv"],
      default=lambda files: ("spreadsheet.convert.ods" if all(kind(f) in ("xlsx", "xlsm") for f in files)
                             else "spreadsheet.convert.xlsx"))
group("spreadsheet.csv", "grp.csv", "csv", ["spreadsheet.csv.excel", "spreadsheet.csv.comma",
                                            "spreadsheet.csv.semicolon", "text.encoding.utf8", "text.line_endings.lf",
                                            "text.line_endings.crlf"],
      default="spreadsheet.csv.excel")
layout("spreadsheet", ["spreadsheet.convert", "spreadsheet.convert.pdf", "spreadsheet.csv", "spreadsheet.privacy.strip",
                       "spreadsheet.info", "text.copy"])
layout("multi:spreadsheet", ["spreadsheet.merge.xlsx", "spreadsheet.merge.csv", "spreadsheet.convert",
                             "docs.combine.pdf", "spreadsheet.csv", "spreadsheet.privacy.strip"])

group("presentation.convert", "grp.convert", "swap_horiz",
      ["presentation.convert.pdf", "presentation.convert.pptx", "presentation.convert.odp",
       "presentation.convert.png", "presentation.convert.jpg", "presentation.extract.text"],
      default="presentation.convert.pdf")
group("presentation.images", "docs.grp.slide_images", "burst_mode",
      ["presentation.convert.png", "presentation.convert.jpg"], default="presentation.convert.png")
group("presentation.extract", "grp.extract", "unarchive",
      ["presentation.extract.text", "presentation.extract.notes", "presentation.extract.images"],
      default="presentation.extract.text")
layout("presentation", ["presentation.convert", "presentation.convert.pdf", "presentation.images",
                        "presentation.compress", "presentation.extract", "presentation.privacy.strip",
                        "presentation.info"])
layout("multi:presentation", ["docs.combine.pdf", "presentation.convert", "presentation.compress",
                              "presentation.extract", "presentation.privacy.strip"])

group("markdown.convert", "grp.convert", "swap_horiz",
      ["markdown.convert.pdf", "markdown.convert.html", "markdown.convert.docx", "markdown.convert.odt",
       "markdown.convert.epub", "markdown.convert.txt", "markdown.convert.rtf"], default="markdown.convert.pdf")
group("markdown.copy", "grp.copy", "content_copy", ["text.copy", "markdown.copy.html"], default="text.copy")
group("markdown.format", "grp.format", "format_align_left",
      ["markdown.toc.insert", "text.clean.trailing", "text.line_endings.lf", "text.line_endings.crlf",
       "text.encoding.utf8"],
      default="markdown.toc.insert")
group("markdown.slides", "grp.slides", "slideshow", ["markdown.slides.pdf", "markdown.slides.pptx",
                                                     "markdown.slides.html"], default="markdown.slides.pdf")
layout("markdown", ["markdown.convert", "markdown.convert.pdf", "markdown.copy", "markdown.format", "markdown.slides",
                    "markdown.stats", "markdown.toc"])
layout("multi:markdown", ["docs.combine.pdf", "markdown.merge.md", "docs.merge.docx", "markdown.convert",
                          "markdown.format"])

group("text.convert", "grp.convert", "swap_horiz",
      ["text.convert.pdf", "text.convert.docx", "text.convert.odt", "text.convert.md", "text.convert.html"],
      default="text.convert.pdf")
group("text.line_endings", "grp.line_endings", "wrap_text", ["text.line_endings.lf", "text.line_endings.crlf"],
      default="text.line_endings.lf")
group("text.format", "grp.encoding", "translate",
      ["text.encoding.utf8", "text.line_endings.lf", "text.line_endings.crlf", "text.clean.trailing"],
      default="text.encoding.utf8")
layout("text", ["text.convert", "text.copy", "text.encoding.utf8", "text.line_endings", "text.clean.trailing",
                "text.stats"])
layout("multi:text", ["docs.combine.pdf", "text.merge.txt", "docs.merge.docx", "text.convert", "text.format"])

group("code.convert", "grp.export", "ios_share",
      ["text.convert.pdf", "text.convert.html", "text.convert.md", "text.convert.docx", "text.convert.odt"],
      default="text.convert.pdf")
layout("code", ["code.convert", "text.copy", "text.line_endings", "text.encoding.utf8", "text.clean.trailing",
                "text.stats"])
layout("multi:code", ["docs.combine.pdf", "text.merge.txt", "code.convert", "text.format"])

group("data.convert", "grp.convert", "swap_horiz",
      ["data.convert.json", "data.convert.yaml", "data.convert.toml", "data.convert.xml", "data.convert.csv",
       "data.convert.xlsx"],
      default=lambda files: ("data.convert.yaml" if all(_data_family(f) == "json" for f in files)
                             else "data.convert.json"))
layout("data", ["data.convert", "data.pretty", "data.minify", "data.validate", "data.sort_keys", "text.copy",
                "text.stats", "text.format"])
layout("multi:data", ["data.merge.json", "data.convert", "data.pretty", "data.minify", "data.validate",
                      "data.sort_keys"])

group("ebook.convert", "grp.convert", "swap_horiz", [f"ebook.convert.{t}" for t in EBOOK_TARGETS],
      default=lambda files: ("ebook.convert.azw3" if all(kind(f) in ("epub", "kepub") for f in files)
                             else "ebook.convert.epub"))
layout("ebook", ["ebook.convert", "ebook.convert.pdf", "ebook.cover", "ebook.info", "ebook.extract.images"])

group("subtitle.convert", "grp.convert", "swap_horiz",
      ["subtitle.convert.srt", "subtitle.convert.vtt", "subtitle.convert.ass", "subtitle.convert.txt"],
      default=lambda files: ("subtitle.convert.vtt" if all(kind(f) == "srt" for f in files)
                             else "subtitle.convert.srt"))
group("subtitle.timing", "docs.grp.timing", "more_time", ["subtitle.timing.shift", "subtitle.timing.fps"],
      default="subtitle.timing.shift")
layout("subtitle", ["subtitle.convert", "subtitle.timing", "subtitle.clean.hi", "text.format", "text.copy",
                    "text.stats"])
layout("multi:subtitle", ["subtitle.convert", "subtitle.timing", "subtitle.clean.hi", "text.format"])
layout("multi:ebook", ["ebook.convert", "ebook.cover", "ebook.info"])
