"""External engines used by the document actions (needs a job :class:`~dropwheel.jobs.Context`).

* **LibreOffice** (:func:`lo_convert`): ``soffice --headless --norestore --nolockcheck
  -env:UserInstallation=file:///…`` with a private profile under ``$XDG_CACHE_HOME/dropwheel``;
  jobs are serialised with a process-wide lock (plus an ``flock`` shared with other Dropwheel
  processes), the input is staged under a neutral name in the job temp dir (no lock files next to
  the user's document, no problem with odd names), "exit 0 but no output" is detected and retried
  once with a fresh profile, a corrupted persistent profile is reset, timeouts scale with size.
* **pandoc** (:func:`pandoc_run`) with a Lua filter that never downloads remote images.
* **Markdown → PDF** (:func:`markdown_to_pdf`): typst → WeasyPrint → XeLaTeX → Tectonic →
  LibreOffice (HTML), falling through to the next engine when one fails.
* **Text/code → HTML/PDF** (:func:`code_html`, :func:`html_to_pdf`): Pygments highlighting with
  line numbers; WeasyPrint → LibreOffice → PyMuPDF Story.
* PDF helpers: render pages to images, merge, images/SVG/PS/XPS to PDF.
* calibre ``ebook-convert`` with progress.
"""

from __future__ import annotations

import contextlib
import html
import io
import json
import logging
import os
import re
import secrets
import shutil
import threading
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from .. import tools, util
from ..errors import Cancelled, MissingTool, ToolError, UserError
from ..jobs import Context
from . import _docs_formats as F

log = logging.getLogger("dropwheel.actions.docs")

#: Base LibreOffice timeout (seconds); 2 s per MB are added.
LO_TIMEOUT = 180.0
PANDOC_TIMEOUT = 300.0
LATEX_TIMEOUT = 600.0
EBOOK_TIMEOUT = 900.0

_LO_LOCK = threading.Lock()

#: pandoc reader for source extensions it can read natively.
PANDOC_READERS = {
    "docx": "docx", "docm": "docx", "dotx": "docx", "odt": "odt", "ott": "odt", "rtf": "rtf", "epub": "epub",
    "html": "html", "htm": "html", "xhtml": "html", "fb2": "fb2", "rst": "rst", "org": "org", "textile": "textile",
    "tex": "latex", "latex": "latex", "ipynb": "ipynb", "csv": "csv", "tsv": "tsv", "md": "markdown",
    "markdown": "markdown", "mdown": "markdown", "mkd": "markdown", "mkdn": "markdown", "mdwn": "markdown",
    "mdx": "markdown", "rmd": "markdown", "qmd": "markdown", "wiki": "mediawiki", "opml": "opml",
}
#: pandoc Markdown dialect used for every Markdown source.
MD_READER = "markdown+autolink_bare_uris+emoji+task_lists"

#: LibreOffice application of a source extension (selects the PDF export filter).
LO_APP = {
    **{e: "writer" for e in ("docx", "docm", "dotx", "dotm", "doc", "dot", "odt", "ott", "fodt", "rtf", "wpd",
                             "pages", "abw", "wps", "lwp", "txt", "html", "htm", "xhtml", "epub", "fb2")},
    **{e: "calc" for e in ("xlsx", "xlsm", "xlsb", "xltx", "xls", "xlt", "ods", "ots", "fods", "csv", "tsv", "tab",
                           "numbers")},
    **{e: "impress" for e in ("pptx", "pptm", "ppsx", "potx", "ppt", "pps", "pot", "odp", "otp", "fodp", "key")},
    **{e: "draw" for e in ("odg", "fodg", "vsd", "vsdx", "pub", "cdr")},
}

#: Metric-compatible substitutes shipped by ttf-carlito / ttf-caladea / ttf-liberation / gelasio.
_METRIC_ALIASES = {"calibri": "carlito", "cambria": "caladea", "arial": "liberation sans",
                   "helvetica": "liberation sans", "times new roman": "liberation serif", "times": "liberation serif",
                   "courier new": "liberation mono", "courier": "liberation mono",
                   "arial narrow": "liberation sans narrow", "georgia": "gelasio"}
_IGNORED_FONTS = {"symbol", "wingdings", "wingdings 2", "wingdings 3", "webdings", "marlett", "ms mincho",
                  "ms gothic", "simsun", "mangal", "arial unicode ms", "opensymbol", "starsymbol"}


def tmp(ctx: Context, name: str) -> Path:
    """A fresh path in the job temp dir (random prefix, never derived from user text)."""
    return ctx.tmpdir() / f"{secrets.token_hex(4)}-{name}"


def link_or_copy(src: Path, dst: Path) -> None:
    """Hard-link ``src`` to ``dst`` (copy when linking is impossible)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def move(src: Path, dst: Path) -> None:
    """Move a produced file into place (``dst`` is usually an ``atomic_target`` part path)."""
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copyfile(src, dst)
        try:
            os.unlink(src)
        except OSError:
            pass


def paper_size() -> str:
    """``"letter"`` for US-style locales (LC_PAPER/LC_ALL/LANG), ``"a4"`` otherwise."""
    for var in ("LC_PAPER", "LC_ALL", "LANG"):
        value = os.environ.get(var, "")
        if value:
            region = value.split(".")[0].split("_")[-1].upper() if "_" in value else ""
            return "letter" if region in ("US", "CA", "MX", "PH", "CL", "CO", "VE", "CR", "GT", "SV", "PR") else "a4"
    return "a4"


def resolve_paper(value: str | None) -> str:
    """``auto`` → :func:`paper_size`."""
    value = (value or "auto").lower()
    return value if value in ("a4", "letter", "a5", "a3") else paper_size()


# ================================================================================ LibreOffice

def lo_profile_dir() -> Path:
    """The persistent private LibreOffice profile used by Dropwheel."""
    return util.cache_dir() / "libreoffice" / "profile"


def _wait(seconds: float) -> None:
    threading.Event().wait(seconds)


@contextlib.contextmanager
def lo_slot(ctx: Context) -> Iterator[None]:
    """Serialise LibreOffice jobs (threads of this process and other Dropwheel processes)."""
    announced = False
    while not _LO_LOCK.acquire(timeout=0.25):
        if not announced:
            ctx.progress(None, ctx.t("docs.prog.lo_wait"))
            announced = True
        ctx.check_cancel()
    handle = None
    try:
        try:
            import fcntl  # type: ignore

            lock_path = util.cache_dir() / "libreoffice" / "lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(lock_path, "a+")
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not announced:
                        ctx.progress(None, ctx.t("docs.prog.lo_wait"))
                        announced = True
                    ctx.check_cancel()
                    _wait(0.25)
        except ImportError:
            handle = None
        except OSError as exc:
            log.debug("LibreOffice flock unavailable: %s", exc)
        yield
    finally:
        if handle is not None:
            try:
                import fcntl  # type: ignore

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            handle.close()
        _LO_LOCK.release()


def lo_pdf_target(app: str, options: dict[str, Any] | None = None) -> str:
    """``--convert-to`` value for PDF with the export filter of ``app`` and JSON filter options."""
    filt = {"writer": "writer_pdf_Export", "calc": "calc_pdf_Export", "impress": "impress_pdf_Export",
            "draw": "draw_pdf_Export", "web": "writer_web_pdf_Export"}.get(app, "writer_pdf_Export")
    if not options:
        return f"pdf:{filt}"
    payload = {}
    for key, value in options.items():
        if isinstance(value, bool):
            payload[key] = {"type": "boolean", "value": "true" if value else "false"}
        elif isinstance(value, int):
            payload[key] = {"type": "long", "value": str(value)}
        else:
            payload[key] = {"type": "string", "value": str(value)}
    return f"pdf:{filt}:" + json.dumps(payload, separators=(",", ":"))


def _lo_messages(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-15:])


def lo_convert(ctx: Context, src: Path, convert_to: str, *, out_ext: str, src_ext: str | None = None,
               infilter: str | None = None, timeout: float | None = None, multi: bool = False,
               label: str | None = None) -> list[Path]:
    """Convert ``src`` with LibreOffice; returns the produced files (in a job temp folder).

    ``convert_to`` is the full ``--convert-to`` value (``"pdf:writer_pdf_Export"``,
    ``"docx:MS Word 2007 XML"``…); ``multi=True`` accepts several outputs (CSV per sheet).
    Raises :class:`MissingTool`, :class:`ToolError` (timeout / crash) or :class:`UserError`
    (``docs.err.lo_load`` / ``docs.err.lo_filter`` / ``docs.err.lo_no_output``).
    """
    if tools.path("soffice") is None:
        raise MissingTool("soffice")
    ext = (src_ext if src_ext is not None else util.split_name(src.name)[1]).lower()
    work = ctx.tmpdir() / f"lo-{secrets.token_hex(4)}"
    indir = work / "in"
    outdir = work / "out"
    indir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    staged = indir / (f"source.{ext}" if ext else "source")
    link_or_copy(src, staged)
    try:
        size_mb = staged.stat().st_size / 1_000_000
    except OSError:
        size_mb = 0
    limit = timeout if timeout is not None else LO_TIMEOUT + 2.0 * size_mb
    persistent = lo_profile_dir()
    persistent.parent.mkdir(parents=True, exist_ok=True)
    message = label or ctx.t("docs.prog.lo")
    details = ""
    load_error = filter_error = False
    fresh_profile = work / "profile"
    with lo_slot(ctx):
        plan: list[Path] = [persistent]
        restarted = fresh_tried = False
        while plan:
            profile = plan.pop(0)
            ctx.check_cancel()
            first_start = not (profile / "user").exists()
            ctx.progress(None, ctx.t("docs.prog.lo_first") if first_start and profile == persistent else message)
            for leftover in outdir.iterdir():
                if leftover.is_dir():
                    shutil.rmtree(leftover, ignore_errors=True)
                else:
                    leftover.unlink(missing_ok=True)
            argv = ["soffice", f"-env:UserInstallation={profile.as_uri()}", "--headless", "--invisible",
                    "--nologo", "--norestore", "--nolockcheck", "--nodefault"]
            if infilter:
                argv.append(f"--infilter={infilter}")
            argv += ["--convert-to", convert_to, "--outdir", str(outdir), str(staged)]
            rc = 0
            try:
                res = ctx.run(argv, timeout=limit, env={"SAL_USE_VCLPLUGIN": "svp"}, ok_codes=(0, 1, 77, 81),
                              cwd=str(work))
                rc = res.returncode
                details = _lo_messages((res.stdout or "") + "\n" + (res.stderr or ""))
            except ToolError as exc:
                if exc.message == "err.timeout":
                    raise
                details = exc.details
                rc = -1
            produced = sorted(p for p in outdir.iterdir()
                              if p.is_file() and p.name.lower().endswith("." + out_ext.lower())
                              and p.stat().st_size > 0)
            if produced:
                if profile == fresh_profile:
                    # the persistent profile did not work but a fresh one did: rebuild it next time
                    shutil.rmtree(persistent, ignore_errors=True)
                if multi:
                    return produced
                main = outdir / f"source.{out_ext}"
                return [main] if main in produced else produced[:1]
            low = details.lower()
            load_error = "could not be loaded" in low
            filter_error = "no export filter" in low
            if filter_error:
                break
            if profile == persistent and rc == 81 and not restarted:
                # first start of a new profile: LibreOffice restarts itself and converts nothing
                restarted = True
                plan.insert(0, persistent)
            elif not fresh_tried:
                fresh_tried = True
                plan.append(fresh_profile)
        if load_error:
            raise UserError("docs.err.lo_load", details=details, name=src.name)
        if filter_error:
            raise UserError("docs.err.lo_filter", details=details, fmt=out_ext.upper())
        raise UserError("docs.err.lo_no_output", details=details)


def missing_fonts(names: Sequence[str]) -> list[tuple[str, str]]:
    """``[(font, substitute)]`` for fonts fontconfig replaces with a non-compatible family."""
    if not names or not tools.have("fc-match"):
        return []
    out: list[tuple[str, str]] = []
    for name in list(names)[:25]:
        key = name.strip().lower()
        if not key or key in _IGNORED_FONTS or key.startswith(("wingdings", "ms ", "+")):
            continue
        res = util.run_quiet(["fc-match", "-f", "%{family}", name], timeout=3)
        if res is None or res.returncode != 0 or not (res.stdout or "").strip():
            continue
        families = [f.strip().lower() for f in res.stdout.split(",") if f.strip()]
        if key in families or any(f.startswith(key) for f in families):
            continue
        alias = _METRIC_ALIASES.get(key)
        if alias and alias in families:
            continue
        out.append((name, res.stdout.split(",")[0].strip()))
    return out


def warn_fonts(ctx: Context, path: Path) -> None:
    """Warn (HUD) about document fonts that are not installed (layout may change)."""
    try:
        names = F.office_fonts(path)
        miss = missing_fonts(names)
    except Exception:  # pragma: no cover - best effort
        log.debug("font check failed", exc_info=True)
        return
    if not miss:
        return
    listing = ", ".join(f"{a} → {b}" for a, b in miss[:5]) + (" …" if len(miss) > 5 else "")
    ms = any(a.lower() in _METRIC_ALIASES for a, _b in miss)
    ctx.warn(ctx.t("docs.warn.fonts_ms" if ms else "docs.warn.fonts", fonts=listing))


# ===================================================================================== pandoc

_REMOTE_IMAGES_LUA = r"""
-- Dropwheel: never download remote images; keep them as links.
local function remote(src)
  return src:match("^[%a][%w+.-]*://") ~= nil or src:sub(1, 2) == "//"
end
function Image(el)
  if remote(el.src) then
    local label = el.caption
    if label == nil or #label == 0 then label = { pandoc.Str(el.src) } end
    return pandoc.Link(label, el.src)
  end
end
"""


def remote_images_filter(ctx: Context) -> Path:
    """Path of the Lua filter that turns remote images into links (written once per job)."""
    path = ctx.tmpdir() / "dropwheel-remote-images.lua"
    if not path.exists():
        path.write_text(_REMOTE_IMAGES_LUA, encoding="utf-8")
    return path


def metadata_file(ctx: Context, data: dict[str, Any]) -> Path:
    """A pandoc ``--metadata-file`` (JSON is valid YAML; document metadata keeps precedence)."""
    path = tmp(ctx, "meta.yaml")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def pandoc_run(ctx: Context, args: Sequence[Any], *, cwd: str | os.PathLike | None = None,
               timeout: float = PANDOC_TIMEOUT, label: str | None = None) -> Any:
    """Run pandoc (progress indeterminate); raises :class:`MissingTool` without it."""
    if tools.path("pandoc") is None:
        raise MissingTool("pandoc")
    ctx.progress(None, label or ctx.t("docs.prog.pandoc"))
    return ctx.run(["pandoc", *[os.fspath(a) if isinstance(a, os.PathLike) else str(a) for a in args]],
                   cwd=os.fspath(cwd) if cwd is not None else None, timeout=timeout)


def resource_dir(ctx: Context, f: Any) -> Path:
    """Directory used to resolve relative images: the original's folder when it still exists."""
    try:
        orig = ctx.original(f)
        parent = Path(orig.path).parent
        if parent.is_dir() and os.access(parent, os.R_OK | os.X_OK):
            return parent
    except Exception:
        pass
    return Path(f.path).parent


# ====================================================================================== CSS

def document_css(paper: str = "a4", *, print_mode: bool = True) -> str:
    """Readable default stylesheet for Markdown/HTML documents (hyphenation, code, tables)."""
    size = {"a4": "A4", "letter": "Letter", "a5": "A5", "a3": "A3"}.get(paper, "A4")
    page = (f"@page {{ size: {size}; margin: 2cm 2cm 2.2cm; "
            "@bottom-center { content: counter(page); font-size: 9pt; color: #777; } }\n") if print_mode else ""
    return page + """
html { font-size: 11pt; }
body { font-family: "Noto Serif", "Source Serif 4", "DejaVu Serif", "Liberation Serif", Georgia, serif;
  line-height: 1.55; color: #1f1f1f; background: #ffffff; max-width: 46em; margin: 0 auto; padding: 1.5em;
  hyphens: auto; -webkit-hyphens: auto; overflow-wrap: break-word; text-rendering: optimizeLegibility; }
@media print { body { max-width: none; padding: 0; margin: 0; } }
h1, h2, h3, h4, h5, h6 { font-family: "Inter", "Noto Sans", "DejaVu Sans", "Liberation Sans", sans-serif;
  line-height: 1.25; margin: 1.6em 0 .6em; hyphens: manual; color: #111; break-after: avoid; page-break-after: avoid; }
h1 { font-size: 1.9em; } h2 { font-size: 1.45em; border-bottom: 1px solid #e3e3e3; padding-bottom: .2em; }
h3 { font-size: 1.2em; } h4 { font-size: 1.05em; }
p, li { orphans: 3; widows: 3; }
a { color: #1a5fb4; text-decoration: none; } a:hover { text-decoration: underline; }
code, pre, kbd, samp { font-family: "JetBrains Mono", "Fira Code", "Noto Sans Mono", "DejaVu Sans Mono",
  "Liberation Mono", monospace; font-size: .88em; hyphens: none; }
code { background: #f3f3f5; padding: .1em .3em; border-radius: 4px; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px; padding: .8em 1em; overflow-x: auto;
  white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.4; }
pre code { background: none; padding: 0; border-radius: 0; font-size: 1em; }
blockquote { margin: 1em 0; padding: .2em 1em; border-left: 4px solid #d0d7de; color: #555; }
table { border-collapse: collapse; margin: 1em 0; max-width: 100%; font-size: .95em; }
th, td { border: 1px solid #d0d7de; padding: .35em .7em; vertical-align: top; }
thead th { background: #f3f4f6; }
tbody tr:nth-child(even) td { background: #fafbfc; }
img { max-width: 100%; height: auto; }
figure { margin: 1em 0; text-align: center; } figcaption { font-size: .9em; color: #666; }
hr { border: 0; border-top: 1px solid #ddd; margin: 2em 0; }
li.task-list-item, ul.task-list li { list-style: none; }
nav#TOC { border: 1px solid #e1e4e8; border-radius: 6px; padding: .5em 1.2em; margin: 1em 0 2em; background: #fafbfc; }
.remote-image { color: #666; font-style: italic; }
"""


def code_css(paper: str = "a4", *, monospace: bool = True, font_pt: float = 9.0) -> str:
    """Stylesheet for text/code pages (wrapped lines, grey line numbers)."""
    size = {"a4": "A4", "letter": "Letter", "a5": "A5", "a3": "A3"}.get(paper, "A4")
    family = ('"JetBrains Mono", "Fira Code", "Noto Sans Mono", "DejaVu Sans Mono", "Liberation Mono", monospace'
              if monospace else '"Noto Serif", "DejaVu Serif", "Liberation Serif", Georgia, serif')
    return f"""
@page {{ size: {size}; margin: 1.6cm 1.5cm 1.8cm;
  @top-left {{ content: string(doctitle); font-size: 8pt; color: #888; }}
  @bottom-right {{ content: counter(page) " / " counter(pages); font-size: 8pt; color: #888; }} }}
html {{ font-size: {font_pt}pt; }}
body {{ margin: 0; color: #1f1f1f; background: #ffffff; }}
h1.doctitle {{ string-set: doctitle content(); font-family: "Inter", "Noto Sans", "DejaVu Sans", sans-serif;
  font-size: 10pt; font-weight: 600; margin: 0 0 .8em; color: #444; }}
pre, .highlight pre {{ font-family: {family}; font-size: 1em; line-height: 1.38; white-space: pre-wrap;
  overflow-wrap: anywhere; word-break: break-word; margin: 0; padding: 0; background: none; border: 0; tab-size: 4; }}
.highlight {{ background: none !important; }}
span.linenos, .linenos {{ color: #9a9a9a; background: none !important; padding-right: .8em; user-select: none; }}
"""


# ============================================================================ markdown → HTML

def _html_page(body: str, *, title: str, lang: str, css: str, base: str | None = None) -> str:
    base_tag = f'<base href="{html.escape(base, quote=True)}">\n' if base else ""
    return (f"<!DOCTYPE html>\n<html lang=\"{html.escape(lang)}\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n{base_tag}"
            f"<title>{html.escape(title)}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n")


_IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.I)


def _embed_images(page: str, base: Path, *, inline_local: bool = True, max_bytes: int = 25 * 1024 * 1024) -> str:
    """Inline local ``<img src>`` as data URIs (``inline_local``); remote images become links
    (never downloaded)."""
    import base64
    import mimetypes
    from urllib.parse import unquote

    def repl(m: re.Match) -> str:
        src = html.unescape(m.group(2))
        if not inline_local or re.match(r"^[a-zA-Z][\w+.-]*://", src) or src.startswith("//"):
            return m.group(0)
        if src.startswith("data:"):
            return m.group(0)
        target = (base / unquote(src.split("#")[0].split("?")[0])).resolve()
        try:
            if not target.is_file() or target.stat().st_size > max_bytes:
                return m.group(0)
            data = target.read_bytes()
        except OSError:
            return m.group(0)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return f'{m.group(1)}data:{mime};base64,{base64.b64encode(data).decode("ascii")}{m.group(3)}'

    page = _IMG_SRC_RE.sub(repl, page)
    # remote images: replace the <img> by a link (privacy: nothing is fetched)
    return re.sub(r'<img\b[^>]*?\bsrc="((?:https?:)?//[^"]+)"[^>]*?(?:\balt="([^"]*)")?[^>]*>',
                  lambda m: f'<a class="remote-image" href="{m.group(1)}">{m.group(2) or m.group(1)}</a>', page,
                  flags=re.I)


def markdown_html(ctx: Context, src: Path, *, title: str, lang: str, base: Path, css: str,
                  embed: bool = True, toc: bool = False) -> str:
    """A standalone HTML page from Markdown (pandoc, else python-markdown)."""
    if tools.have("pandoc"):
        header = tmp(ctx, "style.html")
        header.write_text(f"<style>\n{css}\n</style>\n", encoding="utf-8")
        out = tmp(ctx, "page.html")
        meta = metadata_file(ctx, {"pagetitle": title, "lang": lang})
        args: list[Any] = [str(src), "-f", MD_READER, "-t", "html5", "-s", "--mathml", "-H", str(header),
                           "--metadata-file", str(meta), "--lua-filter", str(remote_images_filter(ctx)),
                           "-o", str(out)]
        if embed:
            args.append("--embed-resources")
        if toc:
            args.append("--toc")
        pandoc_run(ctx, args, cwd=base)
        return out.read_text(encoding="utf-8", errors="replace")
    try:
        import markdown as mdlib  # type: ignore
    except ImportError:
        raise MissingTool(["pandoc"]) from None
    ctx.progress(None, ctx.t("docs.prog.render"))
    text = F.read_text(src).text
    _meta, body_md = F.split_front_matter(text)
    extensions = ["extra", "sane_lists", "toc", "smarty"]
    configs: dict[str, Any] = {}
    extra_css = ""
    if tools.have("py:pygments"):
        extensions.append("codehilite")
        configs["codehilite"] = {"guess_lang": False, "css_class": "highlight"}
        try:
            from pygments.formatters import HtmlFormatter  # type: ignore

            extra_css = HtmlFormatter(style="friendly").get_style_defs(".highlight")
        except Exception:
            extra_css = ""
    body = mdlib.markdown(body_md, extensions=extensions, extension_configs=configs, output_format="html")
    if toc:
        heads = F.markdown_headings(text)
        toc_md = F.toc_markdown(heads)
        if toc_md:
            body = '<nav id="TOC">' + mdlib.markdown(toc_md) + "</nav>\n" + body
    page = _html_page(body, title=title, lang=lang, css=css + "\n" + extra_css)
    return _embed_images(page, base) if embed else page


# ============================================================================ markdown → PDF

MD_PDF_ENGINES = ("typst", "weasyprint", "xelatex", "tectonic", "libreoffice")


def md_engine_available(engine: str) -> bool:
    """Whether a Markdown → PDF engine can run here."""
    pandoc = tools.have("pandoc")
    mdlib = tools.have("py:markdown")
    if engine == "typst":
        return pandoc and tools.have("typst")
    if engine == "weasyprint":
        return (pandoc or mdlib) and tools.have("weasyprint")
    if engine in ("xelatex", "tectonic"):
        return pandoc and tools.have(engine)
    if engine == "libreoffice":
        return (pandoc or mdlib) and tools.have("soffice")
    return False


def _engine_tools(engine: str) -> list[str]:
    return {"typst": ["pandoc", "typst"], "weasyprint": ["pandoc", "weasyprint"], "xelatex": ["pandoc", "xelatex"],
            "tectonic": ["pandoc", "tectonic"], "libreoffice": ["pandoc", "soffice"]}.get(engine, ["pandoc", "typst"])


def _md_pdf(ctx: Context, engine: str, src: Path, out: Path, *, title: str, lang: str, paper: str, toc: bool,
            base: Path) -> None:
    filt = str(remote_images_filter(ctx))
    if engine in ("typst", "xelatex", "tectonic"):
        meta: dict[str, Any] = {"lang": lang, "fontsize": "11pt", "colorlinks": True, "linkcolor": "blue",
                                "urlcolor": "blue"}
        if engine == "typst":
            meta["papersize"] = "us-letter" if paper == "letter" else paper
            meta["margin"] = {"x": "2cm", "y": "2.2cm"}
            meta["section-numbering"] = None
        else:
            meta["papersize"] = paper
            meta["geometry"] = ["margin=2cm"]
            meta["documentclass"] = "article"
        meta = {k: v for k, v in meta.items() if v is not None}
        args: list[Any] = [str(src), "-f", MD_READER, "-o", str(out), f"--pdf-engine={engine}",
                           "--metadata-file", str(metadata_file(ctx, meta)), "--lua-filter", filt]
        if toc:
            args.append("--toc")
        pandoc_run(ctx, args, cwd=base, timeout=LATEX_TIMEOUT if engine != "typst" else PANDOC_TIMEOUT,
                   label=ctx.t("docs.prog.pdf_engine", engine=engine))
        return
    if engine == "weasyprint":
        page = markdown_html(ctx, src, title=title, lang=lang, base=base, css=document_css(paper), embed=False,
                             toc=toc)
        page = _embed_images(page, base, inline_local=False)  # remote images → links, local stay relative
        page_path = tmp(ctx, "page.html")
        page_path.write_text(page, encoding="utf-8")
        weasyprint(ctx, page_path, out, base=base)
        return
    if engine == "libreoffice":
        page = markdown_html(ctx, src, title=title, lang=lang, base=base, css=document_css(paper), embed=True,
                             toc=toc)
        page_path = tmp(ctx, "page.html")
        page_path.write_text(page, encoding="utf-8")
        produced = lo_convert(ctx, page_path, lo_pdf_target("web"), out_ext="pdf", src_ext="html")
        move(produced[0], out)
        return
    raise MissingTool(_engine_tools(engine))


def weasyprint(ctx: Context, page: Path, out: Path, *, base: Path | None = None) -> None:
    """Render an HTML file to PDF with the WeasyPrint CLI."""
    if tools.path("weasyprint") is None:
        raise MissingTool("weasyprint")
    ctx.progress(None, ctx.t("docs.prog.pdf_engine", engine="WeasyPrint"))
    argv: list[Any] = ["weasyprint"]
    if base is not None:
        argv += ["--base-url", base.as_uri() + "/"]
    argv += [str(page), str(out)]
    ctx.run(argv, timeout=PANDOC_TIMEOUT)


def markdown_to_pdf(ctx: Context, src: Path, out: Path, *, title: str, lang: str, paper: str = "auto",
                    engine: str = "auto", toc: bool = False, base: Path | None = None) -> str:
    """Markdown → PDF with the first engine that works; returns the engine used.

    Raises :class:`MissingTool` (recommending pandoc + typst) when no engine is installed and
    :class:`ToolError` ``docs.err.pdf_engines`` with every engine's error when all of them fail.
    """
    paper = resolve_paper(paper)
    base = base or src.parent
    order = list(MD_PDF_ENGINES) if engine in ("", "auto", None) else [engine]
    available = [e for e in order if md_engine_available(e)]
    if not available:
        raise MissingTool(_engine_tools(order[0]) if len(order) == 1 else ["pandoc", "typst"])
    failures: list[tuple[str, UserError]] = []
    for name in available:
        ctx.check_cancel()
        try:
            if out.exists():
                out.unlink()
            _md_pdf(ctx, name, src, out, title=title, lang=lang, paper=paper, toc=toc, base=base)
            if out.exists() and out.stat().st_size > 0:
                if failures:
                    ctx.log(f"markdown → pdf: {name} succeeded after {[n for n, _ in failures]} failed")
                return name
            failures.append((name, UserError("err.no_output", name=out.name)))
        except (Cancelled, MissingTool):
            raise
        except UserError as exc:
            failures.append((name, exc))
            ctx.log(f"markdown → pdf with {name} failed: {exc.text('en')}")
    details = "\n\n".join(f"[{name}] {exc.text(ctx.lang)}\n{exc.details}".strip() for name, exc in failures)
    raise ToolError("docs.err.pdf_engines", details=details,
                    engines=", ".join(name for name, _ in failures))


# ============================================================================ text/code → HTML

def _lexer(filename: str, text: str, plain: bool) -> Any:
    from pygments.lexers import TextLexer, get_lexer_for_filename, guess_lexer  # type: ignore
    from pygments.util import ClassNotFound  # type: ignore

    if plain:
        return TextLexer()
    try:
        return get_lexer_for_filename(filename, text[:4096])
    except ClassNotFound:
        pass
    if text.startswith("#!"):
        try:
            return guess_lexer(text[:4096])
        except ClassNotFound:
            pass
    return TextLexer()


def language_name(filename: str, text: str) -> str:
    """Human name of the programming language (Pygments), ``""`` when unknown or without Pygments."""
    try:
        lexer = _lexer(filename, text, False)
    except ImportError:
        return ""
    name = getattr(lexer, "name", "") or ""
    return "" if name in ("Text only", "Text") else name


def _hard_wrap(text: str, width: int) -> str:
    out = []
    for line in text.split("\n"):
        if len(line) <= width:
            out.append(line)
            continue
        pieces = []
        for token in re.split(r"( +)", line):
            while len(token) > width:
                pieces.append(token[:width] + "\n")
                token = token[width:]
            pieces.append(token)
        out.append("".join(pieces))
    return "\n".join(out)


def code_html(text: str, *, filename: str, title: str, lang: str, line_numbers: bool, plain: bool,
              monospace: bool = True, paper: str = "a4", css_extra: str = "", hard_wrap: int = 0,
              print_mode: bool = True, highlight: bool = True) -> str:
    """A standalone HTML page showing ``text`` (Pygments inline styles; line numbers optional)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    if hard_wrap:
        text = _hard_wrap(text, hard_wrap)
    body = ""
    if highlight and not plain:
        try:
            from pygments import highlight as pyg_highlight  # type: ignore
            from pygments.formatters import HtmlFormatter  # type: ignore

            formatter = HtmlFormatter(style="friendly", noclasses=True, linenos="inline" if line_numbers else False,
                                      wrapcode=True)
            body = pyg_highlight(text, _lexer(filename, text, plain), formatter)
        except ImportError:
            body = ""
    if not body:
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if line_numbers:
            width = len(str(len(lines)))
            rendered = "\n".join(f'<span class="linenos">{n:>{width}}</span>{html.escape(ln)}'
                                 for n, ln in enumerate(lines, 1))
        else:
            rendered = html.escape("\n".join(lines))
        body = f"<pre>{rendered}</pre>"
    page_css = code_css(paper, monospace=monospace) if print_mode else (
        code_css(paper, monospace=monospace) + "\nbody { margin: 2rem auto; max-width: 110ch; padding: 0 1rem; }")
    head = f'<h1 class="doctitle">{html.escape(title)}</h1>\n'
    return _html_page(head + body, title=title, lang=lang, css=page_css + css_extra)


def story_pdf(ctx: Context, page_html: str, out: Path, *, paper: str = "a4") -> None:
    """Lay out simple HTML into a PDF with PyMuPDF's Story engine (no external program)."""
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            raise MissingTool("py:pymupdf") from None
    ctx.progress(None, ctx.t("docs.prog.render"))
    css = re.search(r"<style>(.*?)</style>", page_html, re.S)
    user_css = re.sub(r"@page\s*\{(?:[^{}]|\{[^{}]*\})*\}", "", css.group(1) if css else "")
    body = re.search(r"<body>(.*)</body>", page_html, re.S)
    story = pymupdf.Story(html=body.group(1) if body else page_html, user_css=user_css)
    mediabox = pymupdf.paper_rect("letter" if paper == "letter" else paper)
    where = mediabox + (42, 48, -42, -48)
    writer = pymupdf.DocumentWriter(str(out))
    more = True
    pages = 0
    try:
        while more:
            ctx.check_cancel()
            device = writer.begin_page(mediabox)
            more, _filled = story.place(where)
            story.draw(device)
            writer.end_page()
            pages += 1
            if pages > 20000:
                raise UserError("err.too_large")
    finally:
        writer.close()


def html_to_pdf(ctx: Context, make_page: Callable[[str, int], str], out: Path, *, base: Path | None = None,
                paper: str = "a4") -> str:
    """Render HTML to PDF: WeasyPrint → LibreOffice → PyMuPDF Story; returns the engine used.

    ``make_page(engine, hard_wrap)`` builds the HTML for that engine.
    """
    engines = [e for e, ok in (("weasyprint", tools.have("weasyprint")), ("libreoffice", tools.have("soffice")),
                               ("story", tools.have("py:pymupdf"))) if ok]
    if not engines:
        raise MissingTool(["weasyprint"])
    failures: list[tuple[str, UserError]] = []
    for engine in engines:
        ctx.check_cancel()
        try:
            if out.exists():
                out.unlink()
            if engine == "weasyprint":
                page = tmp(ctx, "page.html")
                page.write_text(make_page(engine, 0), encoding="utf-8")
                weasyprint(ctx, page, out, base=base)
            elif engine == "libreoffice":
                page = tmp(ctx, "page.html")
                page.write_text(make_page(engine, 0), encoding="utf-8")
                produced = lo_convert(ctx, page, lo_pdf_target("web"), out_ext="pdf", src_ext="html")
                move(produced[0], out)
            else:
                story_pdf(ctx, make_page(engine, 110), out, paper=paper)
            if out.exists() and out.stat().st_size > 0:
                return engine
            failures.append((engine, UserError("err.no_output", name=out.name)))
        except (Cancelled, MissingTool):
            raise
        except UserError as exc:
            failures.append((engine, exc))
        except Exception as exc:  # Story layout errors are plain exceptions
            failures.append((engine, UserError("err.unexpected", details=str(exc))))
    details = "\n\n".join(f"[{name}] {exc.text(ctx.lang)}\n{exc.details}".strip() for name, exc in failures)
    raise ToolError("docs.err.pdf_engines", details=details, engines=", ".join(n for n, _ in failures))


# ===================================================================================== PDF misc

def _mupdf() -> Any:
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        try:
            import fitz  # type: ignore

            return fitz
        except ImportError:
            return None


def pdf_page_count(path: Path) -> int | None:
    """Pages of a PDF (PyMuPDF or pypdf), ``None`` when unknown."""
    m = _mupdf()
    if m is not None:
        try:
            with m.open(str(path)) as doc:
                return doc.page_count
        except Exception:
            return None
    try:
        from pypdf import PdfReader  # type: ignore

        return len(PdfReader(str(path)).pages)
    except Exception:
        return None


def pdf_to_images(ctx: Context, pdf: Path, outdir: Path, *, fmt: str = "png", dpi: int = 150,
                  prefix: str = "page") -> list[Path]:
    """Render every page into ``outdir/<prefix>-001.<fmt>`` (PyMuPDF, else pdftoppm)."""
    fmt = "jpg" if fmt in ("jpg", "jpeg") else "png"
    outputs: list[Path] = []
    m = _mupdf()
    if m is not None:
        with m.open(str(pdf)) as doc:
            total = doc.page_count
            width = max(3, len(str(total)))
            for i, page in enumerate(doc):
                ctx.check_cancel()
                ctx.progress(i / max(1, total), ctx.t("docs.prog.page", n=i + 1, total=total))
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                target = outdir / f"{prefix}-{i + 1:0{width}d}.{fmt}"
                if fmt == "jpg":
                    try:
                        pix.save(str(target), jpg_quality=90)
                    except TypeError:
                        pix.save(str(target))
                else:
                    pix.save(str(target))
                outputs.append(target)
        return outputs
    if tools.path("pdftoppm") is None:
        raise MissingTool(["py:pymupdf"])
    ctx.progress(None, ctx.t("docs.prog.render"))
    work = ctx.tmpdir() / f"ppm-{secrets.token_hex(3)}"
    work.mkdir(parents=True, exist_ok=True)
    argv: list[Any] = ["pdftoppm", "-r", str(dpi)]
    argv += ["-jpeg", "-jpegopt", "quality=90"] if fmt == "jpg" else ["-png"]
    argv += [str(pdf), str(work / "p")]
    ctx.run(argv, timeout=600)
    produced = util.natural_sorted(work.iterdir(), key=lambda p: p.name)
    width = max(3, len(str(len(produced))))
    for i, p in enumerate(produced, 1):
        target = outdir / f"{prefix}-{i:0{width}d}.{fmt}"
        move(p, target)
        outputs.append(target)
    return outputs


def image_to_pdf(path: Path, out: Path) -> None:
    """One raster image → one PDF page (EXIF orientation applied, transparency on white)."""
    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        m = _mupdf()
        if m is None:
            raise MissingTool("py:PIL") from None
        with m.open(str(path)) as img:
            data = img.convert_to_pdf()
        out.write_bytes(data)
        return
    try:
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
        except Exception:
            pass
        with Image.open(path) as im:
            im.seek(0)
            dpi = im.info.get("dpi", (96, 96))
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            elif im.mode not in ("RGB", "L", "CMYK"):
                im = im.convert("RGB")
            try:
                res = float(dpi[0]) if dpi and dpi[0] else 96.0
            except (TypeError, ValueError):
                res = 96.0
            im.save(out, "PDF", resolution=max(36.0, min(res, 1200.0)))
    except Exception as exc:
        raise UserError("docs.err.image_unreadable", details=str(exc), name=path.name) from None


def svg_to_pdf(ctx: Context, path: Path, out: Path) -> None:
    """SVG → PDF (rsvg-convert, else PyMuPDF)."""
    if tools.have("rsvg-convert"):
        ctx.run(["rsvg-convert", "-f", "pdf", "-o", str(out), str(path)], timeout=120)
        return
    m = _mupdf()
    if m is None:
        raise MissingTool(["rsvg-convert"])
    try:
        with m.open(str(path), filetype="svg") as doc:
            out.write_bytes(doc.convert_to_pdf())
    except Exception as exc:
        raise UserError("docs.err.image_unreadable", details=str(exc), name=path.name) from None


def ps_to_pdf(ctx: Context, path: Path, out: Path) -> None:
    """PostScript/EPS → PDF with Ghostscript (``-dSAFER``)."""
    ctx.progress(None, ctx.t("docs.prog.convert"))
    ctx.run(["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET", "-sDEVICE=pdfwrite", "-dEPSCrop",
             f"-sOutputFile={out}", str(path)], timeout=LO_TIMEOUT)


def xps_to_pdf(ctx: Context, path: Path, out: Path) -> None:
    """XPS/OXPS → PDF (PyMuPDF, else ``mutool convert``)."""
    m = _mupdf()
    ctx.progress(None, ctx.t("docs.prog.convert"))
    if m is not None:
        try:
            with m.open(str(path), filetype="xps") as doc:
                out.write_bytes(doc.convert_to_pdf())
            return
        except Exception as exc:
            if not tools.have("mutool"):
                raise UserError("err.damaged", details=str(exc)) from None
    ctx.run(["mutool", "convert", "-F", "pdf", "-o", str(out), str(path)], timeout=LO_TIMEOUT)


def merge_pdfs(ctx: Context, parts: Sequence[tuple[Path, str]], out: Path) -> int:
    """Concatenate PDFs (one bookmark per part); returns the page count (encrypted parts skipped)."""
    m = _mupdf()
    if m is not None:
        doc = m.open()
        toc: list[list[Any]] = []
        try:
            for i, (path, title) in enumerate(parts):
                ctx.check_cancel()
                ctx.progress(0.9 + 0.08 * i / max(1, len(parts)), ctx.t("docs.prog.merge"))
                with m.open(str(path)) as src:
                    if src.needs_pass and not src.authenticate(""):
                        ctx.warn(ctx.t("docs.warn.encrypted_skipped", name=title))
                        continue
                    start = doc.page_count
                    doc.insert_pdf(src)
                    if doc.page_count > start:
                        toc.append([1, title, start + 1])
            if doc.page_count == 0:
                raise UserError("docs.err.nothing_combined")
            try:
                doc.set_toc(toc)
            except Exception:
                pass
            doc.save(str(out), garbage=3, deflate=True)
            return doc.page_count
        finally:
            doc.close()
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore
    except ImportError:
        raise MissingTool(["py:pymupdf"]) from None
    writer = PdfWriter()
    for path, title in parts:
        ctx.check_cancel()
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ValueError("password")
            except Exception:
                ctx.warn(ctx.t("docs.warn.encrypted_skipped", name=title))
                continue
        writer.append(reader, outline_item=title)
    if len(writer.pages) == 0:
        raise UserError("docs.err.nothing_combined")
    with open(out, "wb") as fh:
        writer.write(fh)
    return len(writer.pages)


def encrypt_pdf(ctx: Context, src: Path, out: Path, password: str) -> None:
    """Protect a PDF with AES-256 (PyMuPDF, else pypdf); the password never reaches a command line."""
    m = _mupdf()
    if m is not None:
        with m.open(str(src)) as doc:
            perms = int(m.PDF_PERM_ACCESSIBILITY | m.PDF_PERM_PRINT | m.PDF_PERM_COPY | m.PDF_PERM_ANNOTATE)
            doc.save(str(out), encryption=m.PDF_ENCRYPT_AES_256, user_pw=password,
                     owner_pw=password + "-" + secrets.token_hex(8), permissions=perms, garbage=3, deflate=True)
        return
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore
    except ImportError:
        raise MissingTool(["py:pymupdf"]) from None
    writer = PdfWriter(clone_from=PdfReader(str(src)))
    try:
        writer.encrypt(user_password=password, owner_password=None, algorithm="AES-256")
    except Exception as exc:  # pypdf needs "cryptography" for AES
        raise UserError("docs.err.encrypt_failed", details=str(exc)) from None
    with open(out, "wb") as fh:
        writer.write(fh)


# ===================================================================================== calibre

_PERCENT_RE = re.compile(r"^\s*(\d{1,3})%\s*(.*)$")


def ebook_convert(ctx: Context, src: Path, out: Path, *, extra: Sequence[str] = ()) -> None:
    """``ebook-convert src out`` (format from ``out``'s extension) with progress from its output."""
    if tools.path("ebook-convert") is None:
        raise MissingTool("ebook-convert")
    ctx.progress(0.0, ctx.t("docs.prog.ebook"))

    def on_line(line: str) -> None:
        m = _PERCENT_RE.match(line)
        if m:
            try:
                ctx.progress(min(1.0, int(m.group(1)) / 100.0), ctx.t("docs.prog.ebook"))
            except ValueError:
                pass

    ctx.run(["ebook-convert", str(src), str(out), *extra], on_line=on_line, timeout=EBOOK_TIMEOUT,
            env={"QT_QPA_PLATFORM": "offscreen", "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu"})
    if not out.exists() or out.stat().st_size == 0:
        raise UserError("err.no_output", name=out.name)
