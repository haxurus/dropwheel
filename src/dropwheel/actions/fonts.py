"""Font actions (category ``font``: ttf otf woff woff2 ttc otc, variable fonts included).

Top ring: Install★ (Uninstall when the file already lives in ``~/.local/share/fonts``), Preview sheet,
Convert → WOFF2★/WOFF/TTF/OTF, Web kit (WOFF2 + WOFF + CSS ``@font-face``), Subset…, Info.

* Install copies the font (WOFF/WOFF2 are unwrapped to TTF/OTF first, fontconfig does not index web
  fonts reliably) into ``$XDG_DATA_HOME/fonts/<Family>/`` and runs ``fc-cache -f``; Undo removes the
  installed copies again.  Families already known to fontconfig are reported.
* Convert re-wraps the sfnt data (WOFF/WOFF2 need Brotli for WOFF2); TrueType ↔ CFF outlines are
  converted with fontTools pens (``cu2qu`` for OTF → TTF, ``T2CharStringPen`` for TTF → OTF): the
  result is an approximation of the curves and loses TrueType hinting, so a warning is shown.
* The OS/2 ``fsType`` embedding bits are honoured: "no subsetting" blocks Subset, restricted
  licences produce a warning on Convert / Web kit.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import i18n, tools, util
from ..detect import FileInfo
from ..errors import MissingTool, UserError
from ..jobs import Context, atomic_target
from ..registry import P, action, group, layout
from .common import Progress, fmt_time, info_row

__all__ = [
    "FONT_EXTS", "sniff", "open_font", "font_names", "user_font_dir", "pangram", "parse_unicode_ranges",
    "subset_unicodes", "embedding_text", "css_font_face", "render_sheet",
]

FONT_EXTS = frozenset({"ttf", "otf", "woff", "woff2", "ttc", "otc"})
COLLECTION_EXTS = frozenset({"ttc", "otc"})
CONVERT_TARGETS: tuple[str, ...] = ("woff2", "woff", "ttf", "otf")
_TARGET_TEXT = {"woff2": "WOFF2", "woff": "WOFF", "ttf": "TTF", "otf": "OTF"}

PANGRAMS = {
    "it": "Ma la volpe col suo balzo ha raggiunto il quieto Fido",
    "en": "The quick brown fox jumps over the lazy dog",
}
ALPHABET_LINES = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789 àèéìòù ÀÈÉÌÒÙ €$£&@?!.,;:()",
)

#: Google Fonts "latin" and "latin-ext" ranges (what web sites usually ship).
SUBSET_PRESETS: dict[str, str] = {
    "latin": "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
             "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
    "latin_ext": "U+0000-00FF, U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, "
                 "U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2000-206F, U+2020, U+20A0-20C0, U+20AC, "
                 "U+2113, U+2122, U+2191, U+2193, U+2212, U+2215, U+2C60-2C7F, U+A720-A7FF, U+FEFF, U+FFFD",
}

# OS/2 fsType bits
_FS_RESTRICTED = 0x0002
_FS_PREVIEW = 0x0004
_FS_EDITABLE = 0x0008
_FS_NO_SUBSET = 0x0100
_FS_BITMAP = 0x0200

_SECONDS_1904_TO_1970 = 2082844800


# ================================================================================ low level

def sniff(path: str | os.PathLike) -> str:
    """Container/outline kind from the magic bytes: ``ttf`` | ``otf`` | ``woff`` | ``woff2`` | ``ttc`` | ``""``."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return ""
    if head == b"wOFF":
        return "woff"
    if head == b"wOF2":
        return "woff2"
    if head == b"ttcf":
        return "ttc"
    if head == b"OTTO":
        return "otf"
    if head in (b"\x00\x01\x00\x00", b"true", b"typ1"):
        return "ttf"
    return ""


def collection_size(path: str | os.PathLike) -> int:
    """Number of faces in a TTC/OTC (1 for single fonts)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return 1
    if len(head) < 12 or head[:4] != b"ttcf":
        return 1
    return max(1, struct.unpack(">I", head[8:12])[0])


def _need_brotli() -> None:
    import importlib.util

    for module in ("brotli", "brotlicffi"):
        try:
            if importlib.util.find_spec(module) is not None:
                return
        except (ImportError, ValueError):
            continue
    raise MissingTool("py:brotli")


def _ttlib() -> Any:
    try:
        from fontTools import ttLib
    except ImportError:
        raise MissingTool("py:fontTools") from None
    return ttLib


def open_font(path: str | os.PathLike, index: int = 0, *, lazy: bool | None = None) -> Any:
    """Open a font with fontTools (``index`` selects the face of a collection)."""
    ttLib = _ttlib()
    kind = sniff(path)
    if kind == "woff2":
        _need_brotli()
    try:
        return ttLib.TTFont(os.fspath(path), fontNumber=index if kind == "ttc" else -1, lazy=lazy)
    except ImportError as exc:  # brotli missing deep inside fontTools
        raise MissingTool("py:brotli", details=str(exc)) from None
    except (ttLib.TTLibError, OSError, ValueError, KeyError, AssertionError, struct.error, IndexError) as exc:
        raise UserError("err.font.damaged", details=str(exc)) from None


def font_names(font: Any) -> dict[str, str]:
    """Human readable names of an opened font (empty strings when missing)."""
    table = font["name"] if "name" in font else None

    def get(*ids: int) -> str:
        if table is None:
            return ""
        for ident in ids:
            try:
                value = table.getDebugName(ident)
            except Exception:
                value = None
            if value and value.strip():
                return value.strip()
        return ""

    return {
        "family": get(16, 1), "style": get(17, 2), "full": get(4), "postscript": get(6), "version": get(5),
        "copyright": get(0), "trademark": get(7), "manufacturer": get(8), "designer": get(9),
        "description": get(10), "vendor_url": get(11), "designer_url": get(12), "license": get(13),
        "license_url": get(14), "sample": get(19),
    }


def _outline(font: Any) -> str:
    if "CFF2" in font:
        return "cff2"
    if "CFF " in font:
        return "cff"
    if "glyf" in font:
        return "glyf"
    return ""


def _fs_type(font: Any) -> int:
    try:
        return int(font["OS/2"].fsType)
    except (KeyError, AttributeError, TypeError, ValueError):
        return 0


def embedding_text(fs_type: int, t) -> str:
    """Readable OS/2 fsType (``t`` translates keys)."""
    kind = fs_type & 0x000F
    if kind & _FS_RESTRICTED:
        text = t("info.font.embed_restricted")
    elif kind & _FS_PREVIEW:
        text = t("info.font.embed_preview")
    elif kind & _FS_EDITABLE:
        text = t("info.font.embed_editable")
    else:
        text = t("info.font.embed_installable")
    extra = []
    if fs_type & _FS_NO_SUBSET:
        extra.append(t("info.font.embed_no_subset"))
    if fs_type & _FS_BITMAP:
        extra.append(t("info.font.embed_bitmap"))
    return text + (" · " + " · ".join(extra) if extra else "")


def user_font_dir() -> Path:
    """``$XDG_DATA_HOME/fonts`` (``~/.local/share/fonts``)."""
    return util.xdg_data_home() / "fonts"


def _under(path: str, root: Path) -> bool:
    try:
        p = os.path.realpath(path)
        r = os.path.realpath(root)
    except OSError:
        return False
    return p.startswith(r.rstrip(os.sep) + os.sep)


def pangram(lang: str) -> str:
    """Sample sentence for previews (Italian or English)."""
    return PANGRAMS.get(lang, PANGRAMS["en"])


def _codepoints(font: Any) -> set[int]:
    try:
        cmap = font.getBestCmap() or {}
    except Exception:
        cmap = {}
    return set(cmap)


def _printable(cp: int) -> bool:
    try:
        ch = chr(cp)
    except ValueError:
        return False
    cat = unicodedata.category(ch)
    return not (cat.startswith("C") or cat.startswith("Z") or cat in ("Mn", "Me"))


# ================================================================================ unicode ranges

_RANGE_RE = re.compile(r"^(?:U\+)?([0-9A-Fa-f?]{1,6})(?:\s*-\s*(?:U\+)?([0-9A-Fa-f]{1,6}))?$")


def parse_unicode_ranges(text: str) -> list[int]:
    """Parse ``"U+0000-00FF, U+20AC, 41-5A, U+00??"`` into a sorted list of code points.

    Raises :class:`UserError` (``err.font.bad_unicodes``) on malformed input.
    """
    out: set[int] = set()
    tokens = [tok for tok in re.split(r"[,;\s]+", text or "") if tok]
    if not tokens:
        raise UserError("err.font.bad_unicodes", details=text or "")
    for tok in tokens:
        m = _RANGE_RE.match(tok.strip())
        if not m:
            raise UserError("err.font.bad_unicodes", details=tok)
        start_s, end_s = m.group(1), m.group(2)
        if "?" in start_s:
            if end_s or not re.fullmatch(r"[0-9A-Fa-f]*\?+", start_s):
                raise UserError("err.font.bad_unicodes", details=tok)
            lo = int(start_s.replace("?", "0"), 16)
            hi = int(start_s.replace("?", "F"), 16)
        else:
            lo = int(start_s, 16)
            hi = int(end_s, 16) if end_s else lo
        if lo > hi or hi > 0x10FFFF:
            raise UserError("err.font.bad_unicodes", details=tok)
        if hi - lo > 0x30000:
            raise UserError("err.font.bad_unicodes", details=tok)
        out.update(range(lo, hi + 1))
    return sorted(out)


def subset_unicodes(preset: str, text: str = "", unicodes: str = "") -> tuple[list[int], str]:
    """Code points and text for a subset request (``preset`` latin | latin_ext | text | unicodes)."""
    if preset in SUBSET_PRESETS:
        return parse_unicode_ranges(SUBSET_PRESETS[preset]), ""
    if preset == "text":
        if not text.strip():
            raise UserError("err.missing_param", param=i18n.t("prm.font.subset_text"))
        return [], text
    if preset == "unicodes":
        return parse_unicode_ranges(unicodes), ""
    raise UserError("err.invalid_param", param="preset", details=preset)


# ================================================================================ outlines

def otf_to_ttf(ctx: Context | None, font: Any, max_err: float = 1.0) -> None:
    """Convert CFF (cubic) outlines to TrueType (quadratic) in place (fontTools ``otf2ttf`` recipe)."""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable

    if "CFF " not in font:
        raise UserError("err.font.not_cff")
    glyph_order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    quads = {}
    for name in glyph_order:
        if ctx is not None:
            ctx.check_cancel()
        tt_pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(tt_pen, max_err, reverse_direction=True))
        quads[name] = tt_pen.glyph()
    font["loca"] = newTable("loca")
    font["glyf"] = glyf = newTable("glyf")
    glyf.glyphOrder = glyph_order
    glyf.glyphs = quads
    del font["CFF "]
    if "VORG" in font:
        del font["VORG"]
    glyf.compile(font)
    hmtx = font["hmtx"]
    for name, glyph in glyf.glyphs.items():
        if hasattr(glyph, "xMin"):
            hmtx[name] = (hmtx[name][0], glyph.xMin)
    font["maxp"] = maxp = newTable("maxp")
    maxp.tableVersion = 0x00010000
    maxp.maxZones = 1
    maxp.maxTwilightPoints = 0
    maxp.maxStorage = 0
    maxp.maxFunctionDefs = 0
    maxp.maxInstructionDefs = 0
    maxp.maxStackElements = 0
    maxp.maxSizeOfInstructions = 0
    maxp.maxComponentElements = max(
        (len(g.components) if hasattr(g, "components") else 0) for g in glyf.glyphs.values()) if glyf.glyphs else 0
    maxp.compile(font)
    post = font["post"]
    post.formatType = 2.0
    post.extraNames = []
    post.mapping = {}
    post.glyphOrder = glyph_order
    try:
        post.compile(font)
    except (OverflowError, struct.error):
        post.formatType = 3.0
    font.sfntVersion = "\x00\x01\x00\x00"


_TT_ONLY_TABLES = ("glyf", "loca", "cvt ", "fpgm", "prep", "hdmx", "LTSH", "VDMX", "cvar", "gasp")


def _ps_name(text: str) -> str:
    clean = re.sub(r"[^\x21-\x7e]", "", text or "")
    clean = re.sub(r"[\[\](){}<>/%]", "", clean)
    return clean[:63] or "Font"


def ttf_to_otf(ctx: Context | None, font: Any) -> None:
    """Convert TrueType (quadratic) outlines to CFF (cubic) in place."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    if "glyf" not in font:
        raise UserError("err.font.not_truetype")
    if "gvar" in font:
        raise UserError("err.font.variable_outlines")
    glyph_order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    hmtx = font["hmtx"]
    charstrings = {}
    for name in glyph_order:
        if ctx is not None:
            ctx.check_cancel()
        pen = T2CharStringPen(hmtx[name][0], glyph_set)
        glyph_set[name].draw(pen)
        charstrings[name] = pen.getCharString()
    names = font_names(font)
    for tag in _TT_ONLY_TABLES:
        if tag in font:
            del font[tag]
    builder = FontBuilder(font=font)
    builder.isTTF = False
    info = {"FullName": names["full"] or names["family"] or "Font", "FamilyName": names["family"] or "Font",
            "Weight": names["style"] or "Regular"}
    if names["copyright"]:
        info["Notice"] = names["copyright"][:500]
    builder.setupCFF(_ps_name(names["postscript"] or names["full"]), info, charstrings, {})
    maxp = font["maxp"]
    maxp.tableVersion = 0x00005000
    post = font["post"]
    post.formatType = 3.0
    font.sfntVersion = "OTTO"


# ================================================================================ install helpers

@dataclass
class _InstallItem:
    source: Path
    filename: str
    families: list[str] = field(default_factory=list)


def _same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        ha, hb = hashlib.sha256(), hashlib.sha256()
        with open(a, "rb") as fa, open(b, "rb") as fb:
            for h, fh in ((ha, fa), (hb, fb)):
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
        return ha.digest() == hb.digest()
    except OSError:
        return False


def _fc_query_families(path: str) -> list[str]:
    if not tools.have("fc-query"):
        return []
    res = util.run_quiet(["fc-query", "-f", "%{family[0]}\\n", path], timeout=15)
    if res is None or res.returncode != 0:
        return []
    out: list[str] = []
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if line and line not in out:
            out.append(line)
    return out


def _families(path: str) -> list[str]:
    """Family names of every face of ``path`` (fontTools, else fc-query)."""
    try:
        _ttlib()
    except MissingTool:
        return _fc_query_families(path)
    out: list[str] = []
    for index in range(min(collection_size(path), 64)):
        font = open_font(path, index, lazy=True)
        try:
            fam = font_names(font)["family"]
        finally:
            font.close()
        if fam and fam not in out:
            out.append(fam)
    return out


def _system_families() -> set[str]:
    """Lower-case family names fontconfig already knows (empty when fc-list is missing)."""
    if not tools.have("fc-list"):
        return set()
    res = util.run_quiet(["fc-list", "-f", "%{family}\\n"], timeout=20)
    if res is None or res.returncode != 0:
        return set()
    out: set[str] = set()
    for line in (res.stdout or "").splitlines():
        for name in line.split(","):
            name = name.strip().lower()
            if name:
                out.add(name)
    return out


def _prepare_install(ctx: Context, f: FileInfo) -> _InstallItem:
    orig = ctx.original(f)
    kind = sniff(f.path)
    if not kind:
        raise UserError("err.font.damaged", details=orig.name)
    if kind in ("woff", "woff2"):
        font = open_font(f.path)
        try:
            ext = "otf" if _outline(font) in ("cff", "cff2") else "ttf"
            font.flavor = None
            target = ctx.tmpdir() / f"{util.safe_filename(orig.stem or 'font')}.{ext}"
            font.save(str(target))
            families = [font_names(font)["family"]] if font_names(font)["family"] else []
        finally:
            font.close()
        return _InstallItem(target, target.name, families)
    families = _families(f.path)
    return _InstallItem(Path(f.path), util.safe_filename(orig.name), families)


# ================================================================================ predicates

def _font_files(files: list[FileInfo]) -> bool:
    return all(f.exists and not f.is_dir and f.path for f in files)


def _not_installed(files: list[FileInfo]) -> bool:
    root = user_font_dir()
    return _font_files(files) and not any(_under(f.path, root) for f in files)


def _installed(files: list[FileInfo]) -> bool:
    root = user_font_dir()
    return _font_files(files) and all(_under(f.path, root) for f in files)


def _single_face(files: list[FileInfo]) -> bool:
    return _font_files(files) and all(f.ext not in COLLECTION_EXTS and sniff(f.path) not in ("ttc", "") for f in files)


# ================================================================================ actions

@action(id="font.install", label="act.font.install", icon="install_desktop", categories={"font"}, multi="any",
        requires=["fc-cache"], when=_not_installed, description="desc.font.install", order=1)
def font_install(ctx: Context):
    """Install the fonts for the current user (``~/.local/share/fonts/<Family>/``) and refresh fontconfig."""
    root = user_font_dir()
    known = _system_families()
    prog = Progress(ctx, total=len(ctx.files), label=ctx.t("act.font.install"))
    installed: list[Path] = []
    families: list[str] = []
    already: list[str] = []
    duplicates: list[str] = []
    errors: list[str] = []
    for f in ctx.files:
        ctx.check_cancel()
        orig = ctx.original(f)
        try:
            item = _prepare_install(ctx, f)
        except MissingTool:
            raise
        except UserError as exc:
            if len(ctx.files) == 1:
                raise
            errors.append(f"{orig.name}: {exc.text(ctx.lang)}")
            prog.add(1)
            continue
        family = item.families[0] if item.families else (orig.stem or "Font")
        folder = root / (util.safe_filename(family).strip(" .") or "Fonts")
        existing = folder / item.filename
        if existing.exists() and _same_content(existing, item.source):
            already.append(family)
            prog.add(1)
            continue
        if any(fam.lower() in known for fam in item.families):
            duplicates.extend(fam for fam in item.families if fam.lower() in known)
        out = ctx.output_path(f, name=item.filename, directory=folder)
        with atomic_target(out) as part:
            shutil.copyfile(item.source, part)
        try:
            os.chmod(out, 0o644)
        except OSError:
            pass
        installed.append(out)
        for fam in item.families or [family]:
            if fam not in families:
                families.append(fam)
        prog.add(1)
    if errors:
        ctx.warn("\n".join(errors[:10]))
    if duplicates:
        ctx.warn(ctx.t("warn.font.duplicate", families=", ".join(sorted(set(duplicates)))))
    if not installed:
        if already:
            return ctx.result(kind="none", message=ctx.t("msg.font.already", families=", ".join(already)))
        raise UserError("err.font.install_failed", details="\n".join(errors))
    ctx.progress(None, ctx.t("msg.font.cache"))
    ctx.run(["fc-cache", "-f", str(root)], timeout=600)
    return ctx.result(installed, message=i18n.tn("msg.font.installed", len(families), ctx.lang,
                                                   families=", ".join(families)))


@action(id="font.uninstall", label="act.font.uninstall", icon="delete", categories={"font"}, multi="any",
        requires=["fc-cache"], when=_installed, danger=True, undoable=False, description="desc.font.uninstall",
        order=2)
def font_uninstall(ctx: Context):
    """Remove fonts installed in ``~/.local/share/fonts`` (they go to the trash) and refresh fontconfig."""
    root = user_font_dir()
    removed = 0
    for f in ctx.files:
        ctx.check_cancel()
        path = Path(ctx.original(f).path)
        util.trash(path)
        removed += 1
        parent = path.parent
        try:
            if parent != root and _under(str(parent), root) and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
    ctx.run(["fc-cache", "-f", str(root)], timeout=600)
    return ctx.result(kind="none", message=i18n.tn("msg.font.uninstalled", removed, ctx.lang))


# ------------------------------------------------------------------------------- conversion

def _warn_restricted(ctx: Context, font: Any) -> None:
    fs = _fs_type(font)
    if fs & _FS_RESTRICTED or fs & _FS_PREVIEW:
        ctx.warn(ctx.t("warn.font.restricted"))


def _convert(ctx: Context, fmt: str):
    f = ctx.file
    if sniff(f.path) == "ttc":
        raise UserError("err.font.collection")
    font = open_font(f.path)
    try:
        _warn_restricted(ctx, font)
        outline = _outline(font)
        if fmt in ("woff", "woff2"):
            if fmt == "woff2":
                _need_brotli()
            font.flavor = fmt
        else:
            font.flavor = None
            if fmt == "ttf" and outline in ("cff", "cff2"):
                if outline == "cff2":
                    raise UserError("err.font.variable_outlines")
                ctx.progress(None, ctx.t("msg.font.outlines"))
                otf_to_ttf(ctx, font)
                ctx.warn(ctx.t("warn.font.outlines"))
            elif fmt == "otf" and outline == "glyf":
                ctx.progress(None, ctx.t("msg.font.outlines"))
                ttf_to_otf(ctx, font)
                ctx.warn(ctx.t("warn.font.outlines"))
        out = ctx.output_path(f, ext=fmt)
        with atomic_target(out) as part:
            try:
                font.save(str(part))
            except ImportError as exc:
                raise MissingTool("py:brotli", details=str(exc)) from None
            except (struct.error, OverflowError, ValueError, KeyError, AssertionError) as exc:
                raise UserError("err.font.convert_failed", details=str(exc)) from None
    finally:
        font.close()
    return ctx.result([out], size_before=f.size)


def _not_target(fmt: str):
    def pred(files: list[FileInfo]) -> bool:
        if not _single_face(files):
            return False
        return all(sniff(f.path) != fmt and f.ext != fmt for f in files)

    return pred


def _register_convert(fmt: str, order: int) -> None:
    def fn(ctx: Context):
        return _convert(ctx, fmt)

    fn.__name__ = f"font_convert_{fmt}"
    fn.__doc__ = f"Convert the font to {_TARGET_TEXT[fmt]}."
    requires: list = ["py:fontTools"] + (["py:brotli"] if fmt == "woff2" else [])
    action(id=f"font.convert.{fmt}", label=_TARGET_TEXT[fmt], text=_TARGET_TEXT[fmt], categories={"font"},
           multi="each", when=_not_target(fmt), requires=requires, description="desc.font.convert",
           order=order)(fn)


for _i, _fmt in enumerate(CONVERT_TARGETS):
    _register_convert(_fmt, _i + 1)


# ------------------------------------------------------------------------------- web kit

def _weight_style(font: Any) -> tuple[str, str]:
    weight = "400"
    style = "normal"
    try:
        weight = str(int(font["OS/2"].usWeightClass) or 400)
    except (KeyError, AttributeError, TypeError, ValueError):
        pass
    try:
        if font["OS/2"].fsSelection & 0x0001 or font["head"].macStyle & 0x0002:
            style = "italic"
    except (KeyError, AttributeError, TypeError):
        pass
    if "fvar" in font:
        for axis in font["fvar"].axes:
            if axis.axisTag == "wght":
                weight = f"{int(round(axis.minValue))} {int(round(axis.maxValue))}"
            elif axis.axisTag == "slnt" and style == "normal":
                style = f"oblique {int(round(-axis.maxValue))}deg {int(round(-axis.minValue))}deg"
    return weight, style


def css_font_face(family: str, stem: str, weight: str, style: str) -> str:
    """The ``@font-face`` rule of the web kit (WOFF2 first, WOFF fallback)."""
    fam = family.replace("\\", "\\\\").replace('"', '\\"')
    from urllib.parse import quote

    url = quote(stem)
    return (
        "@font-face {\n"
        f'  font-family: "{fam}";\n'
        f'  src: url("{url}.woff2") format("woff2"),\n'
        f'       url("{url}.woff") format("woff");\n'
        f"  font-weight: {weight};\n"
        f"  font-style: {style};\n"
        "  font-display: swap;\n"
        "}\n"
    )


def _demo_html(family: str, stem: str, sample: str, lang: str) -> str:
    import html

    fam = html.escape(family)
    return (
        "<!doctype html>\n"
        f'<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n<title>{fam}</title>\n'
        f'<link rel="stylesheet" href="{html.escape(stem)}.css">\n'
        "<style>\n"
        f'  body {{ font-family: "{fam.replace(chr(34), "")}", sans-serif; margin: 3rem; color: #1b1b1f; }}\n'
        "  p { margin: .4em 0; }\n"
        "</style>\n</head>\n<body>\n"
        f"<h1>{fam}</h1>\n"
        + "".join(f'<p style="font-size:{s}px">{html.escape(sample)}</p>\n' for s in (16, 24, 36, 56))
        + "</body>\n</html>\n"
    )


@action(id="font.webkit", label="act.font.webkit", icon="web", categories={"font"}, multi="each",
        requires=["py:fontTools", "py:brotli"], when=_single_face, description="desc.font.webkit", order=4)
def font_webkit(ctx: Context):
    """Folder with WOFF2 + WOFF, a CSS ``@font-face`` rule and a demo page."""
    f = ctx.file
    orig = ctx.original(f)
    _need_brotli()
    font = open_font(f.path)
    try:
        _warn_restricted(ctx, font)
        names = font_names(font)
        family = names["family"] or orig.stem
        weight, style = _weight_style(font)
    finally:
        font.close()
    stem = util.safe_filename(orig.stem or "font").replace("/", "-")
    outdir = ctx.output_dir(f, suffix="sfx.webkit")
    prog = Progress(ctx, label=ctx.t("act.font.webkit"))
    for i, flavor in enumerate(("woff2", "woff")):
        ctx.check_cancel()
        prog.frac(i / 3, force=True)
        font = open_font(f.path)
        try:
            font.flavor = flavor
            with atomic_target(outdir / f"{stem}.{flavor}") as part:
                font.save(str(part))
        finally:
            font.close()
    with atomic_target(outdir / f"{stem}.css") as part:
        part.write_text(css_font_face(family, stem, weight, style), encoding="utf-8")
    with atomic_target(outdir / "demo.html") as part:
        part.write_text(_demo_html(family, stem, pangram(ctx.lang), ctx.lang), encoding="utf-8")
    prog.frac(1.0, force=True)
    return ctx.result([outdir], message=ctx.t("msg.font.webkit", family=family))


# ------------------------------------------------------------------------------- subset

def _subset_ext(fmt: str, f: FileInfo) -> str:
    if fmt in ("woff2", "woff"):
        return fmt
    kind = sniff(f.path)
    if kind in ("ttf", "otf"):
        return f.ext if f.ext in ("ttf", "otf") else kind
    return "ttf"


@action(id="font.subset", label="act.font.subset", icon="filter_alt", categories={"font"}, multi="each",
        mode="dialog", dialog="form", requires=["py:fontTools"], when=_single_face, description="desc.font.subset",
        order=5, params=[
            P.select("preset", "prm.font.subset_preset", options=[
                ("latin", "opt.font.subset.latin"), ("latin_ext", "opt.font.subset.latin_ext"),
                ("text", "opt.font.subset.text"), ("unicodes", "opt.font.subset.unicodes")], default="latin"),
            P.textarea("text", "prm.font.subset_text", default="", visible_if={"preset": "text"}),
            P.text("unicodes", "prm.font.subset_unicodes", default="U+0000-00FF, U+20AC",
                   visible_if={"preset": "unicodes"}, help="prm.font.subset_unicodes.help"),
            P.select("format", "prm.font.format", options=[("woff2", "WOFF2"), ("woff", "WOFF"),
                                                           ("same", "opt.font.format_same")], default="woff2"),
            P.bool("features", "prm.font.keep_features", True, help="prm.font.keep_features.help"),
        ])
def font_subset(ctx: Context):
    """Keep only the characters you need (smaller web fonts); OpenType features are kept by default."""
    from fontTools import subset

    f = ctx.file
    fmt = ctx.pstr("format", "woff2")
    if fmt == "woff2":
        _need_brotli()
    unicodes, text = subset_unicodes(ctx.pstr("preset", "latin"), ctx.pstr("text"), ctx.pstr("unicodes"))
    probe = open_font(f.path, lazy=True)
    try:
        if _fs_type(probe) & _FS_NO_SUBSET:
            raise UserError("err.font.no_subsetting")
        _warn_restricted(ctx, probe)
        available = _codepoints(probe)
    finally:
        probe.close()
    wanted = set(unicodes) | {ord(ch) for ch in text}
    if available and not (wanted & available):
        raise UserError("err.font.subset_empty")
    ctx.progress(None, ctx.t("act.font.subset"))
    options = subset.Options()
    options.layout_features = ["*"] if ctx.pbool("features", True) else subset.Options().layout_features
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.flavor = fmt if fmt in ("woff", "woff2") else None
    try:
        font = subset.load_font(f.path, options, lazy=False)
    except ImportError as exc:
        raise MissingTool("py:brotli", details=str(exc)) from None
    except Exception as exc:
        raise UserError("err.font.damaged", details=str(exc)) from None
    try:
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(unicodes=unicodes, text=text)
        try:
            subsetter.subset(font)
        except Exception as exc:  # fontTools raises plain exceptions on unusual tables
            raise UserError("err.font.subset_failed", details=str(exc)) from None
        out = ctx.output_path(f, ext=_subset_ext(fmt, f), suffix="sfx.subset")
        with atomic_target(out) as part:
            try:
                subset.save_font(font, str(part), options)
            except ImportError as exc:
                raise MissingTool("py:brotli", details=str(exc)) from None
        glyphs = len(font.getGlyphOrder())
    finally:
        font.close()
    return ctx.result([out], size_before=f.size, message=i18n.tn("msg.font.subset", glyphs, ctx.lang))


# ------------------------------------------------------------------------------- info

def _head_time(value: Any) -> float | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return (datetime(1904, 1, 1) + timedelta(seconds=seconds) - datetime(1970, 1, 1)).total_seconds()
    except OverflowError:
        return None


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _face_rows(ctx: Context, font: Any, group_key: str, group_extra: str = "") -> list[dict]:
    def row(key: str, value: Any) -> dict:
        r = info_row(ctx, group_key, key, value)
        if group_extra:
            r["group"] = f"{r['group']} · {group_extra}"
        return r

    names = font_names(font)
    rows: list[dict] = []
    for key, name_key in (("info.font.family", "family"), ("info.font.style", "style"), ("info.font.full", "full"),
                          ("info.font.version", "version"), ("info.font.postscript", "postscript"),
                          ("info.font.designer", "designer"), ("info.font.manufacturer", "manufacturer")):
        if names[name_key]:
            rows.append(row(key, names[name_key]))
    outline = _outline(font)
    fmt_key = {"glyf": "info.font.fmt_truetype", "cff": "info.font.fmt_cff", "cff2": "info.font.fmt_cff2"}.get(
        outline, "info.font.fmt_unknown")
    fmt = ctx.t(fmt_key)
    if font.flavor:
        fmt += f" · {font.flavor.upper()}"
    rows.append(row("info.font.format", fmt))
    try:
        rows.append(row("info.font.glyphs", font["maxp"].numGlyphs))
    except (KeyError, AttributeError):
        pass
    cps = _codepoints(font)
    if cps:
        rows.append(row("info.font.characters", len(cps)))
    try:
        rows.append(row("info.font.upm", font["head"].unitsPerEm))
    except (KeyError, AttributeError):
        pass
    try:
        rows.append(row("info.font.weight", font["OS/2"].usWeightClass))
    except (KeyError, AttributeError):
        pass
    if "fvar" in font:
        axes = [f"{a.axisTag} {_num(a.minValue)}–{_num(a.maxValue)} ({_num(a.defaultValue)})" for a in font["fvar"].axes]
        rows.append(row("info.font.axes", ", ".join(axes)))
        rows.append(row("info.font.instances", len(font["fvar"].instances)))
    features: set[str] = set()
    for tag in ("GSUB", "GPOS"):
        if tag in font:
            try:
                table = font[tag].table
                if table.FeatureList is not None:
                    features.update(rec.FeatureTag for rec in table.FeatureList.FeatureRecord)
            except (AttributeError, KeyError):
                continue
    if features:
        rows.append(row("info.font.features", " ".join(sorted(features)[:60])))
    try:
        head = font["head"]
        for key, attr in (("info.font.created", "created"), ("info.font.modified", "modified")):
            ts = _head_time(getattr(head, attr, 0))
            if ts:
                rows.append(row(key, fmt_time(ts, ctx.lang)))
    except KeyError:
        pass
    try:
        vendor = str(font["OS/2"].achVendID).strip()
        if vendor:
            rows.append(row("info.font.vendor", vendor))
    except (KeyError, AttributeError):
        pass
    rows.append(info_row(ctx, "info.grp.license", "info.font.embedding", embedding_text(_fs_type(font), ctx.t)))
    for key, name_key, limit in (("info.font.copyright", "copyright", 300), ("info.font.license", "license", 600),
                                 ("info.font.license_url", "license_url", 300), ("info.font.trademark", "trademark", 200)):
        value = names[name_key]
        if value:
            rows.append(info_row(ctx, "info.grp.license", key, value if len(value) <= limit else value[:limit] + "…"))
    return rows


def _fc_info_rows(ctx: Context, path: str) -> list[dict]:
    res = util.run_quiet(["fc-query", "-f",
                          "%{family[0]}\\t%{style[0]}\\t%{fullname[0]}\\t%{fontversion}\\t%{fontformat}\\t"
                          "%{variable}\\t%{postscriptname}\\n", path], timeout=15)
    if res is None or res.returncode != 0 or not (res.stdout or "").strip():
        raise UserError("err.font.damaged", details=(res.stderr if res is not None else "") or path)
    rows: list[dict] = []
    for index, line in enumerate((res.stdout or "").splitlines()[:8]):
        parts = (line.split("\t") + [""] * 7)[:7]
        family, style, full, version, fmt, variable, ps = parts
        g = "info.grp.font"
        try:
            version = f"{int(version) / 65536:.3f}" if version else ""
        except ValueError:
            pass
        for key, value in (("info.font.family", family), ("info.font.style", style), ("info.font.full", full),
                           ("info.font.version", version), ("info.font.postscript", ps), ("info.font.format", fmt),
                           ("info.font.variable", ctx.t("info.yes") if variable == "True" else "")):
            if value:
                r = info_row(ctx, g, key, value)
                if index:
                    r["group"] = f"{r['group']} · {index + 1}"
                rows.append(r)
    return rows


@action(id="font.info", label="act.font.info", icon="info", categories={"font"}, multi="each", undoable=False,
        requires=[("py:fontTools", "fc-query")], when=_font_files, description="desc.font.info", order=6)
def font_info(ctx: Context):
    """Family, style, version, glyph count, variable axes, features and licence."""
    f = ctx.file
    try:
        _ttlib()
    except MissingTool:
        return ctx.result(kind="info", info=_fc_info_rows(ctx, f.path), message=ctx.original(f).name)
    count = collection_size(f.path)
    rows: list[dict] = []
    if count > 1:
        rows.append(info_row(ctx, "info.grp.font", "info.font.collection", count))
    for index in range(min(count, 8)):
        font = open_font(f.path, index, lazy=True)
        try:
            extra = ""
            if count > 1:
                extra = font_names(font)["full"] or str(index + 1)
            rows.extend(_face_rows(ctx, font, "info.grp.font", extra))
        finally:
            font.close()
    family = rows[0]["value"] if rows and count == 1 else ctx.original(f).name
    return ctx.result(kind="info", info=rows, message=family)


# ------------------------------------------------------------------------------- preview sheet

@dataclass
class _SheetData:
    title: str
    subtitle: str
    sample: str
    lines: list[str]
    glyphs: list[int]
    covered: set[int] | None


def _sheet_data(ctx: Context, f: FileInfo, custom: str) -> tuple[str, int, _SheetData]:
    """Font file usable by Pillow (WOFF unwrapped), its face index and the texts to draw."""
    orig = ctx.original(f)
    kind = sniff(f.path)
    path, index = f.path, 0
    title, subtitle = orig.stem, ""
    covered: set[int] | None = None
    try:
        _ttlib()
        have_ft = True
    except MissingTool:
        have_ft = False
    if kind in ("woff", "woff2") and not have_ft:
        raise MissingTool("py:fontTools")
    if have_ft:
        font = open_font(f.path, 0)
        try:
            names = font_names(font)
            title = " ".join(x for x in (names["family"], names["style"]) if x) or orig.stem
            bits = []
            if names["version"]:
                bits.append(names["version"])
            try:
                bits.append(i18n.tn("msg.font.glyph_count", int(font["maxp"].numGlyphs), ctx.lang))
            except (KeyError, AttributeError):
                pass
            if "fvar" in font:
                bits.append(", ".join(a.axisTag for a in font["fvar"].axes))
            subtitle = " · ".join(bits)
            covered = _codepoints(font) or None
            if kind in ("woff", "woff2"):
                font.flavor = None
                target = ctx.tmpdir() / f"preview.{'otf' if _outline(font) in ('cff', 'cff2') else 'ttf'}"
                font.save(str(target))
                path = str(target)
        finally:
            font.close()
    sample = custom.strip() or names_sample(covered, ctx.lang)
    lines = list(ALPHABET_LINES)
    if covered is not None:
        lines = [ln for ln in lines if _coverage(ln, covered) >= 0.6]
    glyphs: list[int]
    if covered:
        glyphs = sorted(cp for cp in covered if _printable(cp))
    else:
        glyphs = [ord(ch) for ch in "".join(ALPHABET_LINES) if not ch.isspace()]
        glyphs = sorted(set(glyphs))
    return path, index, _SheetData(title, subtitle, sample, lines, glyphs, covered)


def _coverage(text: str, covered: set[int]) -> float:
    chars = [ord(ch) for ch in text if not ch.isspace()]
    if not chars:
        return 1.0
    return sum(1 for cp in chars if cp in covered) / len(chars)


def names_sample(covered: set[int] | None, lang: str) -> str:
    """The pangram, or (for non-Latin / symbol fonts) a run of the font's own characters."""
    text = pangram(lang)
    if covered is None or _coverage(text, covered) >= 0.8:
        return text
    chars = [chr(cp) for cp in sorted(covered) if _printable(cp)][:40]
    return " ".join("".join(chars[i:i + 8]) for i in range(0, len(chars), 8)) or text


def _wrap(font: Any, text: str, width: int, max_lines: int = 3) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if font.getlength(cand) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    out = []
    for line in lines[:max_lines]:
        while line and font.getlength(line) > width:
            line = line[:-2] + "…" if len(line) > 2 else ""
        out.append(line)
    return out


def _label_font(size: int) -> Any:
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_sheet(font_path: str, index: int, data: _SheetData, out: str | os.PathLike, *,
                 width: int = 1600, max_glyphs: int = 320, labels: dict[str, str] | None = None) -> tuple[int, int]:
    """Draw the preview sheet PNG and return its size."""
    from PIL import Image, ImageDraw, ImageFont

    labels = labels or {}
    margin = 64
    inner = width - 2 * margin
    sizes = (14, 18, 24, 32, 48, 64, 96)
    try:
        faces = {s: ImageFont.truetype(font_path, s, index=index) for s in set(sizes) | {36, 40}}
    except OSError as exc:
        raise UserError("err.font.unreadable", details=str(exc)) from None
    small = _label_font(14)
    title_font = _label_font(34)
    ink, grey, rule = (27, 27, 31), (110, 110, 118), (225, 225, 230)

    # height estimation pass
    height = margin
    height += 46 + (26 if data.subtitle else 0) + 28
    alpha_lines = data.lines
    for _ in alpha_lines:
        height += int(36 * 1.35)
    if alpha_lines:
        height += 24
    sample_rows: list[tuple[int, list[str]]] = []
    for s in sizes:
        wrapped = _wrap(faces[s], data.sample, inner - 70, max_lines=2 if s >= 48 else 3)
        sample_rows.append((s, wrapped))
        height += int(s * 1.3) * max(1, len(wrapped)) + 14
    glyphs = data.glyphs[:max_glyphs]
    cell_w, cell_h = 72, 92
    cols = max(1, inner // cell_w)
    rows = (len(glyphs) + cols - 1) // cols
    if glyphs:
        height += 40 + rows * cell_h
    if len(data.glyphs) > max_glyphs:
        height += 30
    height += margin
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    y = margin
    draw.text((margin, y), data.title, font=title_font, fill=ink)
    y += 46
    if data.subtitle:
        draw.text((margin, y), data.subtitle, font=small, fill=grey)
        y += 26
    y += 12
    draw.line((margin, y, width - margin, y), fill=rule, width=2)
    y += 16
    for line in alpha_lines:
        draw.text((margin, y), line, font=faces[36], fill=ink)
        y += int(36 * 1.35)
    if alpha_lines:
        y += 24
    for s, wrapped in sample_rows:
        draw.text((margin, y + max(0, int(s * 0.9) - 14)), f"{s}", font=small, fill=grey)
        for line in wrapped or [""]:
            draw.text((margin + 70, y), line, font=faces[s], fill=ink)
            y += int(s * 1.3)
        y += 14
    if glyphs:
        draw.line((margin, y, width - margin, y), fill=rule, width=2)
        y += 12
        draw.text((margin, y), labels.get("glyphs", "Glyphs"), font=small, fill=grey)
        y += 28
        for i, cp in enumerate(glyphs):
            cx = margin + (i % cols) * cell_w
            cy = y + (i // cols) * cell_h
            draw.rectangle((cx, cy, cx + cell_w - 6, cy + cell_h - 8), outline=rule)
            ch = chr(cp)
            try:
                w = faces[40].getlength(ch)
            except Exception:
                w = 20
            draw.text((cx + (cell_w - 6 - w) / 2, cy + 8), ch, font=faces[40], fill=ink)
            code = f"{cp:04X}"
            cw = small.getlength(code) if hasattr(small, "getlength") else 30
            draw.text((cx + (cell_w - 6 - cw) / 2, cy + cell_h - 30), code, font=small, fill=grey)
        y += rows * cell_h
        if len(data.glyphs) > max_glyphs:
            more = labels.get("more", "+{n}").replace("{n}", str(len(data.glyphs) - max_glyphs))
            draw.text((margin, y + 4), more, font=small, fill=grey)
    img.save(os.fspath(out), "PNG", optimize=True)
    return img.size


@action(id="font.preview", label="act.font.preview", icon="text_fields", categories={"font"}, multi="each",
        mode="quick", requires=["py:PIL"], when=_font_files, description="desc.font.preview", order=3,
        params=[P.text("text", "prm.font.sample", default="", help="prm.font.sample.help")])
def font_preview(ctx: Context):
    """PNG sheet: the alphabet, a sample sentence in several sizes and the glyph grid."""
    f = ctx.file
    ctx.progress(None, ctx.t("act.font.preview"))
    path, index, data = _sheet_data(ctx, f, ctx.pstr("text"))
    out = ctx.output_path(f, ext="png", suffix="sfx.preview")
    labels = {"glyphs": ctx.t("msg.font.glyphs_title"), "more": ctx.t("msg.font.glyphs_more")}
    with atomic_target(out) as part:
        render_sheet(path, index, data, part, labels=labels)
    return ctx.result([out])


# =================================================================================== layout

group("font.convert", "grp.convert", "swap_horiz", [f"font.convert.{fmt}" for fmt in CONVERT_TARGETS],
      default="font.convert.woff2")

layout("font", ["font.install", "font.uninstall", "font.preview", "font.convert", "font.webkit", "font.subset",
                "font.info"])
layout("multi:font", ["font.install", "font.uninstall", "font.convert", "font.webkit", "font.preview", "file.zip"])
