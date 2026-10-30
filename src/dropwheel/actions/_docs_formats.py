"""Pure-Python helpers of the document actions (no external programs, no Context).

Everything here is deterministic and unit-testable on any platform:

* text: decoding with BOM / charset detection, line endings, trailing spaces, statistics;
* markdown: front matter, headings, GitHub-style anchors, table of contents, plain-text strip;
* rtf: a small RTF → text converter (statistics of ``.rtf`` without LibreOffice);
* tables: CSV sniffing/reading/writing, ODS reader and writer, XLSX/XLS readers (optional
  libraries imported lazily), Markdown/HTML tables, JSON records;
* structured data: load JSON/JSONC/JSON5/NDJSON/YAML/TOML/XML/plist/INI/env/properties, dump
  JSON/YAML/TOML/XML/CSV rows, built-in TOML writer, safe XML, XML pretty/minify, sort keys;
* office zips (OOXML/ODF): text, media, metadata stripping, image recompression;
* ebooks: EPUB/FB2/CBZ metadata and covers, DRM detection;
* subtitles: SRT/VTT/ASS/SBV parse and write, shift, frame-rate change, hearing-impaired cleanup.

Errors meant for the user are raised as :class:`FormatError` (``key`` is an i18n key of
``locales/*/docs.json`` and ``fmt`` its placeholders); the action layer turns them into
:class:`~dropwheel.errors.UserError`.
"""

from __future__ import annotations

import base64
import codecs
import configparser
import csv
import datetime as _dt
import html
import io
import json
import math
import os
import plistlib
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..detect import _zip_member_head

__all__ = [
    "FormatError", "Decoded", "decode_bytes", "read_text", "encoding_label", "detect_eol", "convert_eol",
    "strip_trailing", "text_stats", "count_words", "split_front_matter", "markdown_headings", "slugify",
    "toc_markdown", "insert_toc", "strip_markdown", "rtf_to_text",
    "sniff_delimiter", "read_csv_rows", "write_csv_rows", "read_ods", "write_ods", "read_xlsx", "write_xlsx",
    "read_xls", "cell_text", "infer_cell", "decimal_comma_columns", "infer_rows", "records_from_rows",
    "markdown_table", "html_tables_document",
    "sanitize_sheet_name", "unique_sheet_names",
    "DATA_FORMATS", "data_format", "load_data", "dump_data", "toml_dumps", "to_json_compatible", "sort_keys",
    "rows_from_data", "xml_pretty", "xml_minify", "json_pretty", "json_minify", "check_xml_safety",
    "OFFICE_MEDIA_PREFIXES", "office_kind", "office_text", "office_media", "office_fonts", "office_app_stats",
    "strip_office_metadata", "compress_office_media", "office_encrypted", "pptx_slides", "odp_slides",
    "epub_info", "epub_cover", "epub_drm", "fb2_info", "fb2_cover", "cbz_info", "cbz_cover", "parse_ebook_meta",
    "Cue", "parse_subtitles", "subtitle_format", "write_subtitles", "format_timestamp", "parse_timestamp",
    "remap_subtitle_times", "clean_hearing_impaired", "cues_to_text",
]


class FormatError(Exception):
    """A user-facing parsing/serialisation error (``key`` = i18n key, ``fmt`` = placeholders)."""

    def __init__(self, key: str, details: str = "", **fmt: Any) -> None:
        super().__init__(key)
        self.key = key
        self.details = details
        self.fmt = fmt


# ============================================================================== text decoding

_BOMS: tuple[tuple[bytes, str, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le", "UTF-32 LE"),
    (codecs.BOM_UTF32_BE, "utf-32-be", "UTF-32 BE"),
    (codecs.BOM_UTF8, "utf-8", "UTF-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le", "UTF-16 LE"),
    (codecs.BOM_UTF16_BE, "utf-16-be", "UTF-16 BE"),
)

_ENCODING_LABELS = {
    "utf_8": "UTF-8", "utf-8": "UTF-8", "utf8": "UTF-8", "ascii": "ASCII", "cp1252": "Windows-1252",
    "windows-1252": "Windows-1252", "cp1250": "Windows-1250", "cp1251": "Windows-1251", "latin_1": "ISO-8859-1",
    "latin-1": "ISO-8859-1", "iso8859_1": "ISO-8859-1", "iso-8859-1": "ISO-8859-1", "iso8859_15": "ISO-8859-15",
    "iso-8859-15": "ISO-8859-15", "utf_16": "UTF-16", "utf-16": "UTF-16", "utf_16_le": "UTF-16 LE",
    "utf-16-le": "UTF-16 LE", "utf_16_be": "UTF-16 BE", "utf-16-be": "UTF-16 BE", "utf_32": "UTF-32",
    "mac_roman": "Mac Roman", "cp437": "CP437", "cp850": "CP850", "shift_jis": "Shift_JIS", "big5": "Big5",
    "gb2312": "GB2312", "gb18030": "GB18030", "euc_jp": "EUC-JP", "euc_kr": "EUC-KR", "koi8_r": "KOI8-R",
    "utf-32-le": "UTF-32 LE", "utf-32-be": "UTF-32 BE",
}

#: Bytes sampled for charset detection (detection of a few MB is plenty and keeps it fast).
DETECT_SAMPLE = 2 * 1024 * 1024


@dataclass
class Decoded:
    """Decoded text plus what was learned about the bytes."""

    text: str
    encoding: str          #: Python codec name actually used ("utf-8", "cp1252", "utf-16-le"…)
    bom: bool = False      #: the bytes started with a byte-order mark
    confident: bool = True #: False when the encoding is a guess (fallback decoder)
    repaired: bool = False #: UTF-8 with a few invalid bytes, read as Windows-1252

    @property
    def label(self) -> str:
        """Human label ("UTF-8", "Windows-1252"…), "+ BOM" when a BOM was present."""
        base = encoding_label(self.encoding)
        if self.bom:
            base = f"{base} (BOM)"
        return f"{base} + Windows-1252" if self.repaired else base

    @property
    def is_utf8(self) -> bool:
        """True for valid UTF-8 without BOM (and plain ASCII)."""
        return encoding_label(self.encoding) in ("UTF-8", "ASCII") and not self.bom and not self.repaired


def encoding_label(name: str) -> str:
    """Human-readable label of a codec name."""
    if not name:
        return "?"
    key = name.lower().replace(" ", "")
    if key in _ENCODING_LABELS:
        return _ENCODING_LABELS[key]
    try:
        canonical = codecs.lookup(name).name
    except LookupError:
        return name.upper()
    return _ENCODING_LABELS.get(canonical, canonical.upper().replace("_", "-"))


def _looks_utf16(data: bytes) -> str | None:
    """Detect BOM-less UTF-16 by the NUL pattern of mostly-ASCII text."""
    sample = data[:4096]
    if len(sample) < 4 or len(sample) % 2:
        sample = sample[: len(sample) - (len(sample) % 2)]
    if len(sample) < 4:
        return None
    even = sample[0::2]
    odd = sample[1::2]
    if odd.count(0) > len(odd) * 0.6 and even.count(0) < len(even) * 0.1:
        return "utf-16-le"
    if even.count(0) > len(even) * 0.6 and odd.count(0) < len(odd) * 0.1:
        return "utf-16-be"
    return None


_WESTERN_PUNCT = set("€‚„…†‡ˆ‰‹›‘’“”•–—˜™¡¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿×÷ ")


def _western_text(data: bytes) -> str | None:
    """``data`` decoded as Windows-1252 when that yields only Latin letters and usual punctuation."""
    try:
        text = data.decode("cp1252")
    except UnicodeDecodeError:
        return None
    bad = 0
    for ch in text:
        o = ord(ch)
        if o < 0x80:
            if o < 0x20 and ch not in "\t\n\r\f\v":
                bad += 1
        elif not ((ch.isalpha() and o <= 0x24F) or ch in _WESTERN_PUNCT):
            bad += 1
    return text if bad == 0 else None


#: what Windows-1252 text in Italian, French, Spanish, Portuguese, German, Dutch, Catalan and the
#: Nordic languages is made of beyond ASCII.  Letters that only appear when another code page is
#: read as Windows-1252 are left out: š ž ý ð þ (Czech, Turkish ı ş ğ, Russian р ю э…),
#: ¹ ¥ ¯ Ÿ ¾ (Polish ą Ą Ż ź, Slovak ľ)
_WESTERN_USUAL = set("àáâãäåæçèéêëìíîïñòóôõöøùúûüÿœßÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÑÒÓÔÕÖØÙÚÛÜŒ"
                     "€‚„…‘’“”•–—™¡¿«»°ºª§©®£¢±×²³µ·\u00a0")


#: languages charset-normalizer recognises whose letters Windows-1252 lacks (Hungarian ő ű read
#: as Windows-1252 still look Western: õ û)
_NOT_CP1252_LANGUAGES = {"Hungarian", "Polish", "Czech", "Slovak", "Slovene", "Croatian", "Romanian", "Turkish",
                         "Lithuanian", "Latvian", "Estonian", "Vietnamese"}


def _western_language(text: str) -> bool:
    """``text`` (a Windows-1252 decoding) reads like a Western-European language: accented letters
    are a minority of the letters and nearly all of them are the usual ones.

    charset-normalizer cannot tell the Latin code pages apart on such text (it reports CP1258 for
    Italian, turning "così" into "cos" + a combining accent, CP1257 for French, CP1250 for
    Portuguese), while it reliably recognises Cyrillic, Greek or Central-European text, which
    this check rejects.
    """
    letters = accented = high = unusual = 0
    for ch, n in Counter(text).items():
        alpha = ch.isalpha()
        if alpha:
            letters += n
        if ord(ch) < 0x80:
            continue
        high += n
        if alpha:
            accented += n
        if ch not in _WESTERN_USUAL:
            unusual += n
    return bool(high) and accented <= letters * 0.3 and unusual <= high * 0.03


def _foreign_script(text: str) -> bool:
    """``text`` (a Windows-1252 decoding) is nearly all accented letters: that is what Cyrillic,
    Greek or Chinese bytes read as Windows-1252 look like ("Ïðèâåò"), never a Western language.
    A few letters prove nothing ("èàù")."""
    letters = accented = 0
    for ch, n in Counter(text).items():
        if ch.isalpha():
            letters += n
            if ord(ch) >= 0x80:
                accented += n
    return letters >= 12 and accented > letters * 0.6


#: invalid bytes of a ``surrogateescape`` UTF-8 decoding (U+DC80–U+DCFF, one per byte)
_ESCAPED_RE = re.compile("[\udc80-\udcff]+")


def _utf8_counts(data: bytes) -> tuple[int, int]:
    """``(valid multi-byte UTF-8 sequences, invalid bytes)`` in ``data``."""
    raw = data.decode("utf-8", "surrogateescape")
    bad = sum(map(len, _ESCAPED_RE.findall(raw)))
    return len(raw) - len(raw.encode("ascii", "ignore")) - bad, bad


def _utf8_repaired(data: bytes) -> str:
    """``data`` as UTF-8, the bytes that are not valid UTF-8 read as Windows-1252."""
    raw = data.decode("utf-8", "surrogateescape")
    return _ESCAPED_RE.sub(lambda m: bytes(ord(c) - 0xDC00 for c in m.group()).decode("cp1252", "replace"), raw)


def decode_bytes(data: bytes, *, fallback: str = "cp1252") -> Decoded:
    """Decode ``data``: BOM → UTF-8 (stray legacy bytes repaired) → BOM-less UTF-16 →
    charset-normalizer → ``fallback``.

    Never raises: undecodable bytes become U+FFFD as a last resort.
    """
    for bom, codec, _label in _BOMS:
        if data.startswith(bom):
            body = data[len(bom):]
            if codec == "utf-8":
                try:
                    return Decoded(body.decode(codec), codec, True, True)
                except UnicodeDecodeError:
                    return Decoded(_utf8_repaired(body), codec, True, False, repaired=True)
            return Decoded(body.decode(codec, "replace"), codec, True, True)
    utf16 = _looks_utf16(data) if b"\x00" in data[:4096] else None
    if utf16:
        try:
            return Decoded(data.decode(utf16), utf16, False, True)
        except UnicodeDecodeError:
            pass
    try:
        return Decoded(data.decode("utf-8"), "utf-8", False, True)
    except UnicodeDecodeError:
        pass
    valid, bad = _utf8_counts(data)
    if valid > bad:
        # overwhelmingly UTF-8 with a few stray legacy bytes (a "€" or "è" pasted from a
        # Windows-1252 source): reading it all as Windows-1252 would turn every "è" into "Ã¨"
        return Decoded(_utf8_repaired(data), "utf-8", False, False, repaired=True)
    sample = data[:DETECT_SAMPLE]
    western = _western_text(data)
    if (western is not None and sum(1 for b in sample if b >= 0x80) < 200
            and not _foreign_script(western[:DETECT_SAMPLE])):
        # short or mostly-ASCII text: statistical detectors are unreliable there (they report
        # e.g. CP1006 for "caffè"), Windows-1252 is by far the most likely legacy encoding;
        # still a guess when some bytes also form valid UTF-8 sequences
        return Decoded(western, "cp1252", False, valid == 0)
    try:
        from charset_normalizer import from_bytes  # type: ignore

        best = from_bytes(sample).best()
        if best is not None and best.encoding:
            enc = best.encoding
            if (western is not None and enc != "cp1252" and _western_language(western[:DETECT_SAMPLE])
                    and getattr(best, "language", "") not in _NOT_CP1252_LANGUAGES):
                # longer Western-European text, which the detector spreads over the Latin code
                # pages (CP1258 for Italian, CP1257 for French, CP1250 for Portuguese)
                return Decoded(western, "cp1252", False, valid == 0)
            try:
                return Decoded(data.decode(enc, "replace"), enc, False, True)
            except LookupError:
                pass
    except ImportError:
        pass
    except Exception:  # pragma: no cover - detection must never break decoding
        pass
    for enc in (fallback, "latin-1"):
        try:
            return Decoded(data.decode(enc), enc, False, False)
        except (UnicodeDecodeError, LookupError):
            continue
    return Decoded(data.decode("utf-8", "replace"), "utf-8", False, False)  # pragma: no cover


def read_text(path: str | os.PathLike, *, limit: int | None = None) -> Decoded:
    """Read and decode a text file (``limit`` = maximum bytes, ``FormatError`` beyond it)."""
    p = Path(path)
    if limit is not None:
        try:
            size = p.stat().st_size
        except OSError as exc:
            raise FormatError("err.not_readable", details=str(exc), name=p.name) from None
        if size > limit:
            raise FormatError("docs.err.too_large_text", size=size, limit=limit)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise FormatError("err.not_readable", details=str(exc), name=p.name) from None
    return decode_bytes(data)


# ============================================================================ line endings etc.

def detect_eol(text: str) -> str:
    """``"LF"``, ``"CRLF"``, ``"CR"``, ``"mixed"`` or ``"none"``."""
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    kinds = [name for name, n in (("CRLF", crlf), ("LF", lf), ("CR", cr)) if n]
    if not kinds:
        return "none"
    return kinds[0] if len(kinds) == 1 else "mixed"


def convert_eol(text: str, eol: str) -> str:
    """Normalise every line ending to ``eol`` (``"lf"`` or ``"crlf"``)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if eol.lower() == "crlf":
        return normalized.replace("\n", "\r\n")
    return normalized


_TRAIL_RE = re.compile(r"[ \t\u00a0\u3000]+(?=\r\n|\n|\r|\Z)")
_MD_HARDBREAK_RE = re.compile(r"(?<=\S)[ ]{2,}(?=\r\n|\n|\r)")


def strip_trailing(text: str, *, markdown: bool = False) -> tuple[str, int]:
    """Remove trailing blanks on every line; returns ``(text, lines_changed)``.

    With ``markdown=True`` a "two trailing spaces" hard break is turned into the equivalent
    CommonMark backslash break instead of being lost.
    """
    changed = 0
    if markdown:
        text, n = _MD_HARDBREAK_RE.subn("\\\\", text)
        changed += n
    text, n = _TRAIL_RE.subn("", text)
    return text, changed + n


_WORD_RE = re.compile(r"\w", re.UNICODE)


def count_words(text: str) -> int:
    """Words the way word processors count them: whitespace-separated tokens containing a letter or digit."""
    return sum(1 for tok in text.split() if _WORD_RE.search(tok))


def text_stats(text: str, *, wpm: int = 220) -> dict[str, Any]:
    """Statistics of a text: lines, words, characters, paragraphs, longest line, reading minutes."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "" and normalized.endswith("\n"):
        lines = lines[:-1]
    words = count_words(normalized)
    chars = len(normalized.replace("\n", ""))
    no_spaces = sum(1 for ch in normalized if not ch.isspace())
    blank = sum(1 for ln in lines if not ln.strip())
    paragraphs = len([blk for blk in re.split(r"\n\s*\n", normalized) if blk.strip()])
    longest = max((len(ln) for ln in lines), default=0)
    minutes = words / float(wpm) if wpm > 0 else 0.0
    return {
        "lines": len(lines), "words": words, "chars": chars, "chars_no_spaces": no_spaces, "blank_lines": blank,
        "paragraphs": paragraphs, "longest_line": longest, "reading_minutes": minutes,
    }


# ===================================================================================== markdown

_FRONT_RE = re.compile(r"\A(?:\ufeff)?---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)
_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")


def split_front_matter(text: str) -> tuple[str | None, str]:
    """``(yaml_text, body)``: a leading ``---`` … ``---`` block (``None`` when absent)."""
    m = _FRONT_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _iter_outside_fences(lines: Sequence[str]) -> Iterable[tuple[int, str, bool]]:
    """Yield ``(index, line, inside_fence)``."""
    fence: str | None = None
    for i, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(2)[0] * len(m.group(2))
                yield i, line, True
                continue
            yield i, line, False
        else:
            if m and m.group(2).startswith(fence[0]) and len(m.group(2)) >= len(fence) and not m.group(3).strip():
                fence = None
            yield i, line, True


_INLINE_IMG_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_EMPH_RE = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_AUTOLINK_RE = re.compile(r"<((?:https?|mailto|ftp):[^>\s]+)>")


def _strip_inline(text: str) -> str:
    text = _CODE_SPAN_RE.sub(lambda m: m.group(2).strip(), text)
    text = _INLINE_IMG_RE.sub(lambda m: m.group(1), text)
    text = _INLINE_LINK_RE.sub(lambda m: m.group(1), text)
    text = _REF_LINK_RE.sub(lambda m: m.group(1), text)
    text = _AUTOLINK_RE.sub(lambda m: m.group(1), text)
    for _ in range(3):
        text = _EMPH_RE.sub(lambda m: m.group(2), text)
    text = _HTML_TAG_RE.sub("", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", r"\1", text)
    return html.unescape(text)


def markdown_headings(text: str) -> list[tuple[int, str]]:
    """``[(level, title)]`` of ATX and setext headings (code fences and front matter skipped)."""
    _meta, body = split_front_matter(text)
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[tuple[int, str]] = []
    prev_plain: str | None = None
    for _i, line, in_fence in _iter_outside_fences(lines):
        if in_fence:
            prev_plain = None
            continue
        m = _ATX_RE.match(line)
        if m:
            title = _strip_inline((m.group(2) or "").strip())
            if title:
                out.append((len(m.group(1)), title))
            prev_plain = None
            continue
        s = _SETEXT_RE.match(line)
        if s and prev_plain is not None and prev_plain.strip():
            # "---" after a paragraph line is a level-2 heading, not a rule (CommonMark)
            level = 1 if s.group(1).startswith("=") else 2
            out.append((level, _strip_inline(prev_plain.strip())))
            prev_plain = None
            continue
        stripped = line.strip()
        if (not stripped or stripped.startswith((">", "- ", "* ", "+ ", "|", "<")) or re.match(r"^\d+[.)]\s", stripped)
                or line.startswith(("    ", "\t"))):
            prev_plain = None
        else:
            prev_plain = line
    return out


def slugify(title: str, used: dict[str, int] | None = None) -> str:
    """GitHub-style heading anchor (lower case, punctuation dropped, spaces → dashes, ``-1`` dedupe)."""
    slug = title.strip().lower()
    slug = "".join(ch for ch in slug if ch.isalnum() or ch in " -_" or unicodedata.category(ch) in ("Mn", "Mc"))
    slug = slug.replace(" ", "-")
    if used is not None:
        count = used.get(slug)
        if count is None:
            used[slug] = 0
        else:
            count += 1
            used[slug] = count
            slug = f"{slug}-{count}"
    return slug


def toc_markdown(headings: Sequence[tuple[int, str]], *, max_level: int = 3, skip_title: bool = True) -> str:
    """Nested Markdown list of links to the headings (the lone leading H1 = document title is skipped)."""
    items = list(headings)
    used: dict[str, int] = {}
    anchors = [slugify(title, used) for _lvl, title in items]
    pairs = list(zip(items, anchors))
    if skip_title and pairs and pairs[0][0][0] == 1 and sum(1 for (lvl, _t), _a in pairs if lvl == 1) == 1:
        pairs = pairs[1:]
    pairs = [((lvl, t), a) for (lvl, t), a in pairs if lvl <= max_level]
    if not pairs:
        return ""
    base = min(lvl for (lvl, _t), _a in pairs)
    lines = []
    for (lvl, title), anchor in pairs:
        label = title.replace("[", "\\[").replace("]", "\\]")
        lines.append("  " * (lvl - base) + f"- [{label}](#{anchor})")
    return "\n".join(lines)


_TOC_BLOCK_RE = re.compile(r"<!-- toc -->.*?<!-- tocstop -->\n?", re.S)


def insert_toc(text: str, toc: str, *, title: str = "") -> str:
    """Insert (or replace) a ``<!-- toc -->`` block after the front matter and the leading H1."""
    newline = "\r\n" if "\r\n" in text else "\n"
    block = "<!-- toc -->\n" + (f"**{title}**\n\n" if title else "") + toc + "\n<!-- tocstop -->\n"
    block = block.replace("\n", newline)
    if _TOC_BLOCK_RE.search(text.replace("\r\n", "\n")):
        normalized = text.replace("\r\n", "\n")
        replaced = _TOC_BLOCK_RE.sub(lambda _m: block.replace(newline, "\n"), normalized, count=1)
        return replaced.replace("\n", newline) if newline == "\r\n" else replaced
    meta, body = split_front_matter(text)
    head = text[: len(text) - len(body)] if meta is not None else ""
    lines = body.split(newline) if newline in body else body.split("\n")
    sep = newline if newline in body else "\n"
    insert_at = 0
    # skip blank lines, then a leading ATX/setext H1
    while insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1
    if insert_at < len(lines):
        m = _ATX_RE.match(lines[insert_at])
        if m and len(m.group(1)) == 1:
            insert_at += 1
        elif insert_at + 1 < len(lines) and re.match(r"^ {0,3}=+[ \t]*$", lines[insert_at + 1] or ""):
            insert_at += 2
        else:
            insert_at = 0
    else:
        insert_at = 0
    new_lines = lines[:insert_at] + ([""] if insert_at else []) + block.rstrip(newline).split(newline) + [""] + lines[insert_at:]
    return head + sep.join(new_lines)


def strip_markdown(text: str) -> str:
    """Plain text from Markdown: markup removed, code kept, lists and paragraphs preserved."""
    _meta, body = split_front_matter(text)
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for _i, line, in_fence in _iter_outside_fences(lines):
        if in_fence:
            if _FENCE_RE.match(line):
                continue
            out.append(line)
            continue
        if _SETEXT_RE.match(line) and out and out[-1].strip():
            continue
        if re.match(r"^ {0,3}([-*_])( *\1){2,} *$", line):
            out.append("")
            continue
        m = _ATX_RE.match(line)
        if m:
            out.append(_strip_inline((m.group(2) or "").strip()))
            continue
        if re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", line):
            continue
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out.append("\t".join(_strip_inline(c) for c in cells))
            continue
        line = re.sub(r"^(\s*)>\s?", r"\1", line)
        line = re.sub(r"^(\s*)[-*+]\s+\[[ xX]\]\s+", r"\1- ", line)
        line = re.sub(r"^\s*\[[^\]]+\]:\s+\S+.*$", "", line)
        out.append(_strip_inline(line))
    result = "\n".join(out)
    result = re.sub(r"<!--.*?-->", "", result, flags=re.S)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip() + "\n"


# ========================================================================================== rtf

_RTF_SKIP_DESTS = {
    "aftncn", "aftnsep", "aftnsepc", "annotation", "atnauthor", "atndate", "atnicn", "atnid", "atnparent",
    "atnref", "atntime", "atrfend", "atrfstart", "author", "background", "bkmkend", "bkmkstart", "blipuid",
    "buptim", "category", "colorschememapping", "colortbl", "comment", "company", "creatim", "datafield",
    "datastore", "defchp", "defpap", "do", "doccomm", "docvar", "dptxbxtext", "ebcend", "ebcstart",
    "factoidname", "falt", "fchars", "ffdeftext", "ffentrymcr", "ffexitmcr", "ffformat", "ffhelptext", "ffl",
    "ffname", "ffstattext", "file", "filetbl", "fldinst", "fldtype", "fname", "fontemb", "fontfile",
    "fonttbl", "footer", "footerf", "footerl", "footerr", "footnote", "formfield", "ftncn", "ftnsep",
    "ftnsepc", "g", "generator", "gridtbl", "header", "headerf", "headerl", "headerr", "hl", "hlfr", "hlinkbase",
    "hlloc", "hlsrc", "hsv", "htmltag", "info", "keycode", "keywords", "latentstyles", "lchars", "levelnumbers",
    "leveltext", "lfolevel", "linkval", "list", "listlevel", "listname", "listoverride", "listoverridetable",
    "listpicture", "liststylename", "listtable", "listtext", "lsdlockedexcept", "macc", "maccPr", "mailmerge",
    "maln", "malnScr", "manager", "margPr", "mbar", "mbarPr", "mbaseJc", "mbegChr", "mborderBox",
    "mborderBoxPr", "mbox", "mboxPr", "mchr", "mcount", "mctrlPr", "md", "mdeg", "mdegHide", "mden", "mdiff",
    "mdPr", "me", "mendChr", "meqArr", "meqArrPr", "mf", "mfName", "mfPr", "mfunc", "mfuncPr", "mgroupChr",
    "mgroupChrPr", "mgrow", "mhideBot", "mhideLeft", "mhideRight", "mhideTop", "mhtmltag", "mlim", "mlimloc",
    "mlimlow", "mlimlowPr", "mlimupp", "mlimuppPr", "mm", "mmaddfieldname", "mmath", "mmathPict", "mmathPr",
    "mmaxdist", "mmc", "mmcJc", "mmconnectstr", "mmconnectstrdata", "mmcPr", "mmcs", "mmdatasource",
    "mmheadersource", "mmmailsubject", "mmodso", "mmodsofilter", "mmodsofldmpdata", "mmodsomappedname",
    "mmodsoname", "mmodsorecipdata", "mmodsosort", "mmodsosrc", "mmodsotable", "mmodsoudl",
    "mmodsoudldata", "mmodsouniquetag", "mmPr", "mmquery", "mmr", "mnary", "mnaryPr", "mnoBreak", "mnum",
    "mobjDist", "moMath", "moMathPara", "moMathParaPr", "mopEmu", "mphant", "mphantPr", "mplcHide", "mpos",
    "mr", "mrad", "mradPr", "mrPr", "msepChr", "mshow", "mshp", "msPre", "msPrePr", "msSub", "msSubPr",
    "msSubSup", "msSubSupPr", "msSup", "msSupPr", "mstrikeBLTR", "mstrikeH", "mstrikeTLBR", "mstrikeV",
    "msub", "msubHide", "msup", "msupHide", "mtransp", "mtype", "mvertJc", "mvfmf", "mvfml", "mvtof", "mvtol",
    "mzeroAsc", "mzeroDesc", "mzeroWid", "nesttableprops", "nextfile", "nonesttables", "objalias", "objclass",
    "objdata", "object", "objname", "objsect", "objtime", "oldcprops", "oldpprops", "oldsprops", "oldtprops",
    "oleclsid", "operator", "panose", "password", "passwordhash", "pgp", "pgptbl", "picprop", "pict", "pn",
    "pnseclvl", "pntext", "pntxta", "pntxtb", "printim", "private", "propname", "protend", "protstart",
    "protusertbl", "pxe", "result", "revtbl", "revtim", "rsidtbl", "rxe", "shp", "shpgrp", "shpinst",
    "shppict", "shprslt", "shptxt", "sn", "sp", "staticval", "stylesheet", "subject", "sv", "svb", "tc",
    "template", "themedata", "title", "txe", "ud", "upr", "userprops", "wgrffmtfilter", "windowcaption",
    "writereservation", "writereservhash", "xe", "xform", "xmlattrname", "xmlattrvalue", "xmlclose",
    "xmlname", "xmlnstbl", "xmlopen",
}
_RTF_SPECIAL = {"par": "\n", "sect": "\n\n", "page": "\n\n", "line": "\n", "tab": "\t", "emdash": "\u2014",
                "endash": "\u2013", "emspace": "\u2003", "enspace": "\u2002", "qmspace": "\u2005",
                "bullet": "\u2022", "lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c",
                "rdblquote": "\u201d", "row": "\n", "cell": "\t", "nestcell": "\t", "nestrow": "\n"}
_RTF_TOKEN_RE = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|[\r\n]+|(.)", re.S)


def rtf_to_text(rtf: str) -> str:
    """Plain text of an RTF document (destinations like font tables, pictures and fields skipped)."""
    stack: list[tuple[int, bool]] = []
    ignorable = False
    uc_skip = 1
    cur_skip = 0
    codepage = "cp1252"
    out: list[str] = []
    pending: bytearray = bytearray()

    def flush() -> None:
        if pending:
            try:
                out.append(pending.decode(codepage, "replace"))
            except LookupError:
                out.append(pending.decode("cp1252", "replace"))
            pending.clear()

    for m in _RTF_TOKEN_RE.finditer(rtf):
        word, arg, hexcode, char, brace, tchar = m.groups()
        if brace:
            flush()
            cur_skip = 0
            if brace == "{":
                stack.append((uc_skip, ignorable))
            elif stack:
                uc_skip, ignorable = stack.pop()
        elif char:
            flush()
            cur_skip = 0
            if char == "~":
                if not ignorable:
                    out.append("\u00a0")
            elif char in "{}\\":
                if not ignorable:
                    out.append(char)
            elif char == "*":
                ignorable = True
            elif char == "-":
                pass
            elif char == "_":
                if not ignorable:
                    out.append("\u2011")
            elif char in "\r\n":
                if not ignorable:
                    out.append("\n")
        elif word:
            flush()
            cur_skip = 0
            if word in _RTF_SKIP_DESTS:
                ignorable = True
            elif ignorable:
                pass
            elif word in _RTF_SPECIAL:
                out.append(_RTF_SPECIAL[word])
            elif word == "uc":
                uc_skip = int(arg or 1)
            elif word == "ansicpg":
                codepage = f"cp{arg}" if arg else codepage
            elif word == "u":
                code = int(arg or 0)
                if code < 0:
                    code += 0x10000
                try:
                    out.append(chr(code))
                except ValueError:
                    pass
                cur_skip = uc_skip
        elif hexcode:
            if cur_skip > 0:
                cur_skip -= 1
            elif not ignorable:
                pending.append(int(hexcode, 16))
        elif tchar:
            flush()
            if cur_skip > 0:
                cur_skip -= 1
            elif not ignorable:
                out.append(tchar)
    flush()
    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


# ======================================================================================= tables

#: Excel limits.
XLSX_MAX_ROWS = 1_048_576
XLSX_MAX_COLS = 16_384

Sheet = tuple[str, list[list[Any]]]


def _raise_csv_limit() -> None:
    limit = 2 ** 31 - 1
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:  # pragma: no cover - 32-bit platforms
            limit //= 2


def sniff_delimiter(sample: str, ext: str = "") -> str:
    """Field delimiter of CSV text (``\\t`` for ``.tsv``/``.tab``; ``,`` when nothing fits)."""
    if ext in ("tsv", "tab"):
        return "\t"
    lines = [ln for ln in sample.splitlines()[:60] if ln.strip()]
    if not lines:
        return ","
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines), delimiters=",;\t|")
        if dialect.delimiter in ",;\t|":
            # the sniffer sometimes picks a delimiter that appears in one line only: double-check
            counts = [ln.count(dialect.delimiter) for ln in lines]
            if counts[0] > 0 and sum(1 for c in counts if c >= 1) >= max(1, len(lines) * 0.8):
                return dialect.delimiter
    except csv.Error:
        pass
    best, best_score = ",", -1.0
    for cand in (",", ";", "\t", "|"):
        counts = [ln.count(cand) for ln in lines]
        if not counts or counts[0] == 0:
            continue
        consistent = sum(1 for c in counts if c == counts[0]) / len(counts)
        score = consistent * 100 + min(counts[0], 50)
        if score > best_score:
            best, best_score = cand, score
    return best


def _trim_rows(rows: list[list[Any]]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for row in rows:
        row = list(row)
        while row and (row[-1] is None or row[-1] == ""):
            row.pop()
        out.append(row)
    while out and not out[-1]:
        out.pop()
    return out


def read_csv_rows(path: str | os.PathLike, ext: str = "", *, limit: int | None = None,
                  delimiter: str | None = None) -> tuple[list[list[str]], str, Decoded]:
    """Rows of a CSV/TSV file: ``(rows, delimiter, decoded)`` (encoding and delimiter detected)."""
    dec = read_text(path, limit=limit)
    delim = delimiter or sniff_delimiter(dec.text[:65536], ext)
    _raise_csv_limit()
    try:
        reader = csv.reader(io.StringIO(dec.text, newline=""), delimiter=delim)
        rows = [list(row) for row in reader]
    except csv.Error as exc:
        raise FormatError("docs.err.csv", details=str(exc)) from None
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    return rows, delim, dec


def cell_text(value: Any) -> str:
    """Text of a spreadsheet value (integral floats without ``.0``, ISO dates, TRUE/FALSE)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None and value.time() == _dt.time(0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        total = int(value.total_seconds())
        sign = "-" if total < 0 else ""
        total = abs(total)
        return f"{sign}{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return str(value)


_NUM_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_COMMA_NUM_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*),\d+$")
#: Italian/European number with "." thousands groups: "1.250", "12.345.678", "1.234.567.890,12";
#: three groups with no decimals are left out: "192.168.100.200" is an address far more often
_GROUPED_NUM_RE = re.compile(r"^[+-]?[1-9]\d{0,2}(?:(?:\.\d{3}){1,2}|(?:\.\d{3})+,\d+)$")


def infer_cell(text: str, *, decimal_comma: bool = False) -> Any:
    """Conservative type inference for CSV cells: integers/decimals without leading zeros become
    numbers (so codes like "00123" or "+39 333…" stay text); everything else stays a string.

    With ``decimal_comma`` (Italian style) "," is the decimal separator and "." only groups
    thousands ("1.250" → 1250, "1.234,56" → 1234.56); other dotted values stay text.
    """
    s = text.strip()
    if not s:
        return None
    if decimal_comma:
        if _COMMA_NUM_RE.match(s) or _GROUPED_NUM_RE.match(s):
            s = s.replace(".", "").replace(",", ".")
        elif "." in s:
            return text
    if _NUM_RE.match(s):
        if "." not in s and "e" not in s.lower():
            digits = s.lstrip("+-")
            if len(digits) <= 15:
                return int(s)
            return text
        try:
            value = float(s)
        except ValueError:
            return text
        return value if math.isfinite(value) else text
    return text


def decimal_comma_columns(rows: Sequence[Sequence[Any]], decimal_comma: bool) -> list[bool]:
    """Per column: does it use the decimal comma?  ``decimal_comma`` is the file-wide hint (";"
    separator); a column whose numbers can only be read with a decimal point ("3.5", "0.125",
    "1e3") and none with a decimal comma keeps the point, so a ";" CSV written by a tool that
    uses "." decimals is not turned into text."""
    width = max((len(r) for r in rows), default=0)
    if not decimal_comma:
        return [False] * width
    out: list[bool] = []
    for i in range(width):
        comma = dot = False
        for row in rows:
            value = row[i] if i < len(row) else None
            if not isinstance(value, str):
                continue
            s = value.strip()
            if _COMMA_NUM_RE.match(s) or (_GROUPED_NUM_RE.match(s) and "," in s):
                comma = True
                break
            if _NUM_RE.match(s) and ("." in s or "e" in s.lower()) and not _GROUPED_NUM_RE.match(s):
                dot = True
        out.append(comma or not dot)
    return out


def infer_rows(rows: Sequence[Sequence[Any]], *, decimal_comma: bool = False) -> list[list[Any]]:
    """``infer_cell`` over every string cell, the decimal separator decided per column."""
    commas = decimal_comma_columns(rows, decimal_comma)
    return [[infer_cell(c, decimal_comma=commas[i]) if isinstance(c, str) else c for i, c in enumerate(row)]
            for row in rows]


def _is_number_text(s: str) -> bool:
    s = s.strip()
    return bool(_NUM_RE.match(s) or _COMMA_NUM_RE.match(s) or _GROUPED_NUM_RE.match(s))


def header_names(row: Sequence[Any]) -> list[str]:
    """Unique, non-empty column names from a header row (``column_3``, ``name_2``…)."""
    names: list[str] = []
    used: set[str] = set()
    for i, cell in enumerate(row):
        base = cell_text(cell).strip() or f"column_{i + 1}"
        name = base
        n = 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
        names.append(name)
    return names


def records_from_rows(rows: Sequence[Sequence[Any]], *, infer: bool = False, decimal_comma: bool = False,
                      empty_as_null: bool | None = None) -> list[dict[str, Any]]:
    """``[{header: value}]`` from rows whose first row is the header.

    ``infer`` turns numeric-looking strings into numbers (CSV sources; ``decimal_comma`` is the
    file-wide hint, see ``decimal_comma_columns``); empty cells become ``None`` when
    ``empty_as_null`` (default: same as ``infer``), else ``""``.
    """
    if empty_as_null is None:
        empty_as_null = infer
    if not rows:
        return []
    width = max(len(r) for r in rows)
    names = header_names(list(rows[0]) + [None] * (width - len(rows[0])))
    commas = decimal_comma_columns(rows[1:], decimal_comma) if infer else []
    out: list[dict[str, Any]] = []
    for row in rows[1:]:
        rec: dict[str, Any] = {}
        for i, name in enumerate(names):
            value = row[i] if i < len(row) else None
            if isinstance(value, str):
                if infer:
                    value = infer_cell(value, decimal_comma=i < len(commas) and commas[i])
            elif isinstance(value, (_dt.date, _dt.time, _dt.datetime, _dt.timedelta)):
                value = cell_text(value)
            elif isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
                value = int(value)
            if value == "" and empty_as_null:
                value = None
            if value is None and not empty_as_null:
                value = ""
            rec[name] = value
        out.append(rec)
    return out


def write_csv_rows(path: str | os.PathLike, rows: Iterable[Sequence[Any]], *, delimiter: str = ",",
                   bom: bool = False, crlf: bool = False) -> int:
    """Write rows as CSV (UTF-8, optional BOM for Excel); returns the number of rows written."""
    n = 0
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter, lineterminator="\r\n" if crlf else "\n",
                            quoting=csv.QUOTE_MINIMAL)
        for row in rows:
            writer.writerow([cell_text(v) for v in row])
            n += 1
    return n


def _md_cell(text: str) -> str:
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip().replace("\n", "<br>")
    return text


def markdown_table(rows: Sequence[Sequence[Any]]) -> str:
    """GitHub-flavoured Markdown table (first row = header; numeric columns right-aligned)."""
    rows = [list(r) for r in rows]
    if not rows:
        return ""
    width = max((len(r) for r in rows), default=0) or 1
    norm = [[_md_cell(cell_text(c)) for c in r] + [""] * (width - len(r)) for r in rows]
    header, body = norm[0], norm[1:]
    numeric = []
    for i in range(width):
        values = [r[i] for r in body if r[i]]
        numeric.append(bool(values) and all(_is_number_text(v) for v in values))
    header = [h if h else " " for h in header]
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---:" if numeric[i] else "---" for i in range(width)) + " |"]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


TABLE_CSS = """
:root { color-scheme: light dark; }
body { font-family: "Inter", "Noto Sans", "DejaVu Sans", system-ui, sans-serif; margin: 2rem; color: #1d1b20;
  background: #fff; line-height: 1.4; }
h1 { font-size: 1.4rem; margin: 0 0 1rem; } h2 { font-size: 1.15rem; margin: 2rem 0 .6rem; }
.wrap { overflow-x: auto; }
table { border-collapse: collapse; font-size: .92rem; font-variant-numeric: tabular-nums; }
th, td { border: 1px solid #cac4d0; padding: .35rem .6rem; text-align: left; vertical-align: top; white-space: pre-wrap; }
thead th { background: #ece6f0; position: sticky; top: 0; }
tbody tr:nth-child(even) { background: #f7f2fa; }
td.num { text-align: right; }
@media (prefers-color-scheme: dark) {
  body { background: #141218; color: #e6e0e9; } th, td { border-color: #49454f; }
  thead th { background: #2b2930; } tbody tr:nth-child(even) { background: #1d1b20; }
}
"""


def html_tables_document(sheets: Sequence[Sheet], *, title: str, lang: str = "en") -> str:
    """A standalone HTML page with one table per sheet (first row = header)."""
    esc = html.escape
    parts = [f"<!DOCTYPE html>\n<html lang=\"{esc(lang)}\">\n<head>\n<meta charset=\"utf-8\">\n"
             f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
             f"<title>{esc(title)}</title>\n<style>{TABLE_CSS}</style>\n</head>\n<body>\n<h1>{esc(title)}</h1>\n"]
    for name, rows in sheets:
        if len(sheets) > 1:
            parts.append(f"<h2>{esc(name)}</h2>\n")
        rows = [list(r) for r in rows]
        if not rows:
            parts.append("<p>—</p>\n")
            continue
        width = max(len(r) for r in rows) or 1
        texts = [[cell_text(c) for c in r] + [""] * (width - len(r)) for r in rows]
        parts.append("<div class=\"wrap\"><table>\n<thead><tr>")
        parts.extend(f"<th>{esc(h)}</th>" for h in texts[0])
        parts.append("</tr></thead>\n<tbody>\n")
        for r in texts[1:]:
            parts.append("<tr>")
            for v in r:
                cls = " class=\"num\"" if v and _is_number_text(v) else ""
                parts.append(f"<td{cls}>{esc(v)}</td>")
            parts.append("</tr>\n")
        parts.append("</tbody></table></div>\n")
    parts.append("</body>\n</html>\n")
    return "".join(parts)


_SHEET_BAD_RE = re.compile(r"[\[\]:*?/\\]")


def sanitize_sheet_name(name: str) -> str:
    """A valid Excel sheet name (≤ 31 characters, no ``[]:*?/\\``, no leading/trailing quote)."""
    text = _SHEET_BAD_RE.sub(" ", str(name))
    text = re.sub(r"\s+", " ", text).strip().strip("'").strip()
    if len(text) > 31:
        text = text[:31].rstrip()
    return text or "Sheet"


def unique_sheet_names(names: Iterable[str]) -> list[str]:
    """Sanitised, case-insensitively unique sheet names."""
    out: list[str] = []
    used: set[str] = set()
    for name in names:
        base = sanitize_sheet_name(name)
        cand = base
        n = 2
        while cand.lower() in used:
            tail = f" ({n})"
            cand = base[: 31 - len(tail)].rstrip() + tail
            n += 1
        used.add(cand.lower())
        out.append(cand)
    return out


# ---------------------------------------------------------------------------------- ODS

_ODF_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
}


def _q(prefix: str, tag: str) -> str:
    return f"{{{_ODF_NS[prefix]}}}{tag}"


def odf_text(el: ET.Element) -> str:
    """Text of an ODF paragraph element (``text:s``, tabs, line breaks honoured; annotations skipped)."""
    parts: list[str] = []
    skip = {_q("office", "annotation"), _q("office", "annotation-end"), _q("text", "note-citation"),
            _q("text", "bookmark-start"), _q("text", "bookmark-end")}

    def walk(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = child.tag
            if tag == _q("text", "s"):
                try:
                    parts.append(" " * max(1, int(child.get(_q("text", "c"), "1"))))
                except ValueError:
                    parts.append(" ")
            elif tag == _q("text", "tab"):
                parts.append("\t")
            elif tag == _q("text", "line-break"):
                parts.append("\n")
            elif tag in skip:
                pass
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return "".join(parts)


def _ods_value(cell: ET.Element) -> Any:
    vt = cell.get(_q("office", "value-type"))
    if vt in ("float", "percentage", "currency"):
        raw = cell.get(_q("office", "value"))
        try:
            value = float(raw) if raw is not None else None
        except ValueError:
            value = None
        if value is not None:
            return int(value) if value.is_integer() and abs(value) < 1e15 else value
    elif vt == "date":
        raw = cell.get(_q("office", "date-value")) or ""
        try:
            parsed = _dt.datetime.fromisoformat(raw)
            return parsed.date() if len(raw) <= 10 else parsed
        except ValueError:
            pass
    elif vt == "time":
        raw = cell.get(_q("office", "time-value")) or ""
        m = re.match(r"^-?PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$", raw)
        if m:
            h, mi, s = int(m.group(1) or 0), int(m.group(2) or 0), float(m.group(3) or 0)
            return f"{h:02d}:{mi:02d}:{int(s):02d}"
    elif vt == "boolean":
        return (cell.get(_q("office", "boolean-value")) or "").lower() == "true"
    paragraphs = [odf_text(p) for p in cell if p.tag in (_q("text", "p"), _q("text", "h"))]
    text = "\n".join(paragraphs)
    return text if text else None


def _ods_rows(container: ET.Element) -> Iterable[tuple[ET.Element, int]]:
    for child in container:
        if child.tag == _q("table", "table-row"):
            try:
                repeat = max(1, int(child.get(_q("table", "number-rows-repeated"), "1")))
            except ValueError:
                repeat = 1
            yield child, repeat
        elif child.tag in (_q("table", "table-header-rows"), _q("table", "table-row-group"), _q("table", "table-rows")):
            yield from _ods_rows(child)


def read_ods(path: str | os.PathLike) -> list[Sheet]:
    """Sheets of an ``.ods`` file as ``[(name, rows)]`` (values typed; trailing empties trimmed)."""
    try:
        with zipfile.ZipFile(path) as zf:
            data = _read_member(zf, "content.xml", _SHEET_LIMIT)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise FormatError("err.damaged", details=str(exc)) from None
    root = safe_xml_fromstring(data)
    sheets: list[Sheet] = []
    for spreadsheet in root.iter(_q("office", "spreadsheet")):
        for table in spreadsheet.findall(_q("table", "table")):
            name = table.get(_q("table", "name")) or f"Sheet{len(sheets) + 1}"
            rows: list[list[Any]] = []
            pending_rows = 0
            for row, repeat in _ods_rows(table):
                cells: list[Any] = []
                pending_cells = 0
                for cell in row:
                    if cell.tag not in (_q("table", "table-cell"), _q("table", "covered-table-cell")):
                        continue
                    try:
                        rep = max(1, int(cell.get(_q("table", "number-columns-repeated"), "1")))
                    except ValueError:
                        rep = 1
                    value = _ods_value(cell) if cell.tag == _q("table", "table-cell") else None
                    if value is None or value == "":
                        pending_cells += rep
                    else:
                        cells.extend([None] * pending_cells)
                        pending_cells = 0
                        cells.extend([value] * min(rep, XLSX_MAX_COLS))
                if not cells:
                    pending_rows += repeat
                else:
                    rows.extend([] for _ in range(pending_rows))
                    pending_rows = 0
                    rows.extend(list(cells) for _ in range(min(repeat, 100_000)))
            sheets.append((name, rows))
        break
    return sheets


def _odf_text_xml(line: str) -> str:
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == " ":
            j = i
            while j < n and line[j] == " ":
                j += 1
            count = j - i
            if i == 0 or j == n:
                out.append(f'<text:s text:c="{count}"/>')
            else:
                out.append(" ")
                if count > 1:
                    out.append(f'<text:s text:c="{count - 1}"/>')
            i = j
            continue
        if ch == "\t":
            out.append("<text:tab/>")
        else:
            out.append(html.escape(ch, quote=False))
        i += 1
    return "".join(out)


def _xml_attr(value: str) -> str:
    return html.escape(_strip_xml_illegal(value), quote=True)


_XML_ILLEGAL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _strip_xml_illegal(text: str) -> str:
    return _XML_ILLEGAL_RE.sub("", text)


def _ods_cell_xml(value: Any) -> str:
    if value is None or value == "":
        return "<table:table-cell/>"
    if isinstance(value, bool):
        v = "true" if value else "false"
        return (f'<table:table-cell office:value-type="boolean" office:boolean-value="{v}">'
                f"<text:p>{'TRUE' if value else 'FALSE'}</text:p></table:table-cell>")
    if isinstance(value, (int, float)) and not (isinstance(value, float) and not math.isfinite(value)):
        return (f'<table:table-cell office:value-type="float" office:value="{cell_text(value)}">'
                f"<text:p>{cell_text(value)}</text:p></table:table-cell>")
    if isinstance(value, (_dt.date, _dt.datetime)):
        iso = value.isoformat() if isinstance(value, _dt.datetime) else value.isoformat()
        return (f'<table:table-cell office:value-type="date" office:date-value="{iso}">'
                f"<text:p>{html.escape(cell_text(value))}</text:p></table:table-cell>")
    text = _strip_xml_illegal(cell_text(value))
    paras = "".join(f"<text:p>{_odf_text_xml(line)}</text:p>" for line in text.replace("\r\n", "\n").split("\n"))
    return f'<table:table-cell office:value-type="string">{paras}</table:table-cell>'


def write_ods(path: str | os.PathLike, sheets: Sequence[Sheet]) -> None:
    """Write a minimal, valid OpenDocument spreadsheet (values only, one table per sheet)."""
    ns = _ODF_NS
    body: list[str] = []
    for name, rows in sheets:
        rows = [list(r) for r in rows]
        width = max((len(r) for r in rows), default=1) or 1
        body.append(f'<table:table table:name="{_xml_attr(name)}">'
                    f'<table:table-column table:number-columns-repeated="{width}"/>')
        if not rows:
            body.append("<table:table-row><table:table-cell/></table:table-row>")
        for row in rows:
            body.append("<table:table-row>")
            body.extend(_ods_cell_xml(v) for v in row)
            if not row:
                body.append("<table:table-cell/>")
            body.append("</table:table-row>")
        body.append("</table:table>")
    content = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               f'<office:document-content xmlns:office="{ns["office"]}" xmlns:table="{ns["table"]}" '
               f'xmlns:text="{ns["text"]}" office:version="1.3"><office:body><office:spreadsheet>'
               + "".join(body) + "</office:spreadsheet></office:body></office:document-content>")
    manifest = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<manifest:manifest xmlns:manifest="{ns["manifest"]}" manifest:version="1.3">'
                '<manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
                'manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
                "</manifest:manifest>")
    styles = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              f'<office:document-styles xmlns:office="{ns["office"]}" office:version="1.3"/>')
    meta = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<office:document-meta xmlns:office="{ns["office"]}" xmlns:meta="{ns["meta"]}" office:version="1.3">'
            "<office:meta><meta:generator>Dropwheel</meta:generator></office:meta></office:document-meta>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype", (1980, 1, 1, 0, 0, 0)), "application/vnd.oasis.opendocument.spreadsheet",
                    compress_type=zipfile.ZIP_STORED)
        for name, data in (("META-INF/manifest.xml", manifest), ("content.xml", content), ("styles.xml", styles),
                           ("meta.xml", meta)):
            zf.writestr(name, data.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)


# ---------------------------------------------------------------------------- XLSX / XLS

def read_xlsx(path: str | os.PathLike) -> list[Sheet]:
    """Sheets of an ``.xlsx``/``.xlsm`` workbook (cached values; ``ImportError`` without openpyxl)."""
    import openpyxl  # type: ignore

    try:
        wb = openpyxl.load_workbook(os.fspath(path), read_only=True, data_only=True)
    except Exception as exc:
        raise FormatError("err.damaged", details=str(exc)) from None
    try:
        out: list[Sheet] = []
        for ws in wb.worksheets:
            # read-only mode bounds the rows/columns by the <dimension> the writer declared, and
            # several writers (Apache POI streaming, many ERP/bank exports) declare just "A1":
            # forget it and read every row that is actually stored
            reset = getattr(ws, "reset_dimensions", None)
            if reset is not None:
                reset()
            rows = [list(row) for row in ws.iter_rows(values_only=True)]
            out.append((ws.title, _trim_rows(rows)))
        return out
    finally:
        try:
            wb.close()
        except Exception:
            pass


def read_xls(path: str | os.PathLike) -> list[Sheet]:
    """Sheets of a legacy ``.xls`` workbook (``ImportError`` without xlrd)."""
    import xlrd  # type: ignore

    try:
        book = xlrd.open_workbook(os.fspath(path), on_demand=True)
    except Exception as exc:
        raise FormatError("err.damaged", details=str(exc)) from None
    try:
        out: list[Sheet] = []
        for sheet in book.sheets():
            rows: list[list[Any]] = []
            for r in range(sheet.nrows):
                row: list[Any] = []
                for c in range(sheet.ncols):
                    cell = sheet.cell(r, c)
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        row.append(None)
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                        except Exception:
                            row.append(cell.value)
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        row.append(bool(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        row.append("#ERR")
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        v = float(cell.value)
                        row.append(int(v) if v.is_integer() and abs(v) < 1e15 else v)
                    else:
                        row.append(cell.value)
                rows.append(row)
            out.append((sheet.name, _trim_rows(rows)))
        return out
    finally:
        try:
            book.release_resources()
        except Exception:
            pass


def write_xlsx(path: str | os.PathLike, sheets: Sequence[Sheet]) -> None:
    """Write sheets with openpyxl (strings starting with "=" stay text, never formulas)."""
    from openpyxl import Workbook  # type: ignore
    from openpyxl.cell import WriteOnlyCell  # type: ignore
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE  # type: ignore

    wb = Workbook(write_only=True)
    names = unique_sheet_names([name for name, _rows in sheets]) or ["Sheet"]
    for title, (_name, rows) in zip(names, sheets):
        if len(rows) > XLSX_MAX_ROWS:
            raise FormatError("docs.err.too_many_rows", rows=len(rows), max=XLSX_MAX_ROWS)
        ws = wb.create_sheet(title=title)
        for row in rows:
            out: list[Any] = []
            for value in list(row)[:XLSX_MAX_COLS]:
                if isinstance(value, str):
                    value = ILLEGAL_CHARACTERS_RE.sub("", value)
                    cell = WriteOnlyCell(ws, value=value)
                    if value.startswith("="):
                        cell.data_type = "s"
                    out.append(cell)
                elif isinstance(value, float) and not math.isfinite(value):
                    out.append(str(value))
                elif isinstance(value, _dt.datetime) and value.tzinfo is not None:
                    out.append(value.replace(tzinfo=None))
                else:
                    out.append(value)
            ws.append(out)
    if not sheets:
        wb.create_sheet(title="Sheet")
    wb.save(os.fspath(path))


# ============================================================================== structured data

#: extension → loader family.
DATA_FORMATS: dict[str, str] = {
    "json": "json", "geojson": "json", "har": "json", "jsonc": "jsonc", "json5": "json5", "ndjson": "ndjson",
    "jsonl": "ndjson", "yaml": "yaml", "yml": "yaml", "toml": "toml", "xml": "xml", "gpx": "xml", "kml": "xml",
    "rss": "xml", "atom": "xml", "xsd": "xml", "xsl": "xml", "xslt": "xml", "opml": "xml", "csl": "xml",
    "plist": "plist", "ini": "ini", "cfg": "ini", "conf": "ini", "desktop": "ini", "service": "ini",
    "env": "env", "properties": "properties",
}
#: family → output format used when the data is written back in its own format.
NATIVE_OUTPUT = {"json": "json", "jsonc": "json", "json5": "json", "ndjson": "json", "yaml": "yaml",
                 "toml": "toml", "xml": "xml", "plist": "json", "ini": "json", "env": "json",
                 "properties": "json"}
FORMAT_LABELS = {"json": "JSON", "jsonc": "JSONC", "json5": "JSON5", "ndjson": "NDJSON", "yaml": "YAML",
                 "toml": "TOML", "xml": "XML", "plist": "plist", "ini": "INI", "env": ".env",
                 "properties": "properties", "csv": "CSV", "xlsx": "XLSX"}

Warn = tuple[str, dict]


def data_format(ext: str, name: str = "") -> str | None:
    """Loader family of an extension (``.env`` files without extension too)."""
    if not ext and name.lower().startswith(".env"):
        return "env"
    return DATA_FORMATS.get((ext or "").lower())


def _line_col(text: str, pos: int) -> tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    col = pos - (text.rfind("\n", 0, pos) + 1) + 1
    return line, col


def _parse_error(fmt: str, message: str, line: Any = None, col: Any = None) -> FormatError:
    return FormatError("docs.err.parse_at" if line else "docs.err.parse", details=message,
                       format=FORMAT_LABELS.get(fmt, fmt.upper()), line=line or "?", col=col or "?",
                       error=message)


def _json_loads(text: str, warnings: list[Warn], fmt: str = "json") -> Any:
    dups: list[str] = []

    def hook(pairs: list[tuple[str, Any]]) -> dict:
        d: dict = {}
        for k, v in pairs:
            if k in d:
                dups.append(k)
            d[k] = v
        return d

    try:
        value = json.loads(text, object_pairs_hook=hook)
    except json.JSONDecodeError as exc:
        raise _parse_error(fmt, exc.msg, exc.lineno, exc.colno) from None
    except RecursionError:
        raise _parse_error(fmt, "nesting too deep") from None
    if dups:
        warnings.append(("docs.warn.duplicate_keys", {"keys": ", ".join(sorted(set(dups))[:5])}))
    return value


def _blank_comments(text: str) -> str:
    """Replace ``//`` and ``/* */`` comments outside strings with spaces (newlines kept)."""
    out: list[str] = []
    i, n = 0, len(text)
    quote = ""
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if c == "\n" else " " for c in text[i:j]))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _drop_trailing_commas(text: str) -> str:
    out = list(text)
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                out[i] = " "
        i += 1
    return "".join(out)


_J5_NUM_RE = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|Infinity|NaN|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_J5_ESCAPES = {"v": "\\u000b", "0": "\\u0000", "'": "'", '"': '\\"'}


def _json5_number(tok: str) -> str:
    sign = ""
    if tok[0] in "+-":
        sign, tok = ("-" if tok[0] == "-" else ""), tok[1:]
    if tok in ("Infinity", "NaN"):
        return ("-" if sign and tok == "Infinity" else "") + tok
    if tok[:2].lower() == "0x":
        return sign + str(int(tok, 16))
    if tok.startswith("."):
        tok = "0" + tok
    if re.match(r"^\d+\.(?:[eE]|$)", tok):
        tok = tok.replace(".", ".0", 1)
    return sign + tok


def _json5_to_json(text: str) -> str:
    """Rewrite JSON5 (comments, single quotes, unquoted keys, hex, trailing commas…) as JSON."""
    text = _blank_comments(text)
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            quote = ch
            j = i + 1
            buf: list[str] = []
            while j < n and text[j] != quote:
                c = text[j]
                if c == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    if nxt == "\n":
                        j += 2
                        continue
                    if nxt == "\r":
                        j += 3 if j + 2 < n and text[j + 2] == "\n" else 2
                        continue
                    if nxt == "x" and j + 3 < n:
                        buf.append("\\u00" + text[j + 2:j + 4])
                        j += 4
                        continue
                    buf.append(_J5_ESCAPES.get(nxt, c + nxt))
                    j += 2
                    continue
                if c == '"':
                    buf.append('\\"')
                elif c == "\n":
                    buf.append("\\n")
                elif c == "\t":
                    buf.append("\\t")
                else:
                    buf.append(c)
                j += 1
            out.append('"' + "".join(buf) + '"')
            i = j + 1
            continue
        if ch.isalpha() or ch in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            word = text[i:j]
            k = j
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k < n and text[k] == ":":
                out.append(json.dumps(word))
            else:
                out.append(word)
            i = j
            continue
        if ch in "+-.0123456789":
            m = _J5_NUM_RE.match(text, i)
            if m:
                out.append(_json5_number(m.group(0)))
                i = m.end()
                continue
        out.append(ch)
        i += 1
    return _drop_trailing_commas("".join(out))


_YAML_LOADER: Any = None


def _yaml_loader() -> Any:
    """A SafeLoader with YAML 1.2 booleans (``yes``/``no``/``on``/``off`` stay strings)."""
    global _YAML_LOADER
    if _YAML_LOADER is not None:
        return _YAML_LOADER
    import yaml  # type: ignore

    base = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader

    class Loader(base):  # type: ignore[misc, valid-type]
        pass

    resolvers = {}
    for first, items in yaml.SafeLoader.yaml_implicit_resolvers.items():
        resolvers[first] = [(tag, rx) for tag, rx in items if tag != "tag:yaml.org,2002:bool"]
    Loader.yaml_implicit_resolvers = resolvers
    Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                                 list("tTfF"))
    _YAML_LOADER = Loader
    return Loader


def _load_yaml(text: str) -> Any:
    import yaml  # type: ignore

    try:
        docs = list(yaml.load_all(text, Loader=_yaml_loader()))
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        line = mark.line + 1 if mark is not None else None
        col = mark.column + 1 if mark is not None else None
        raise _parse_error("yaml", str(exc.problem or exc), line, col) from None
    except yaml.YAMLError as exc:
        raise _parse_error("yaml", str(exc)) from None
    if not docs:
        return None
    return docs[0] if len(docs) == 1 else docs


def _load_toml(text: str) -> Any:
    import tomllib

    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        line = getattr(exc, "lineno", None)
        col = getattr(exc, "colno", None)
        msg = getattr(exc, "msg", None) or str(exc)
        if line is None:
            m = re.search(r"line (\d+), column (\d+)", str(exc))
            if m:
                line, col = int(m.group(1)), int(m.group(2))
            msg = re.sub(r"\s*\(at line \d+, column \d+\)", "", msg)
        raise _parse_error("toml", msg, line, col) from None


_ENTITY_RE = re.compile(r"<!ENTITY", re.I)


def check_xml_safety(data: bytes | str) -> None:
    """Refuse XML with entity declarations (billion laughs / XXE) before parsing."""
    if isinstance(data, bytes):
        head = data[:262144]
        if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) or head[:2] in (b"<\x00", b"\x00<"):
            try:
                text = head.decode("utf-16", "ignore")
            except UnicodeDecodeError:
                text = ""
        else:
            text = head.decode("utf-8", "ignore")
    else:
        text = data[:262144]
    if _ENTITY_RE.search(text):
        raise FormatError("docs.err.xml_entities")


def safe_xml_fromstring(data: bytes | str, *, comments: bool = False) -> ET.Element:
    """Parse XML safely (entity declarations refused; defusedxml used when installed)."""
    check_xml_safety(data)
    try:
        if comments:
            parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
            parser.feed(data)
            return parser.close()
        try:
            from defusedxml import ElementTree as DET  # type: ignore

            return DET.fromstring(data)
        except ImportError:
            return ET.fromstring(data)
    except ET.ParseError as exc:
        line, col = getattr(exc, "position", (None, None))
        raise _parse_error("xml", str(exc), line, (col + 1) if col is not None else None) from None
    except FormatError:
        raise
    except Exception as exc:  # defusedxml's EntitiesForbidden & co.
        if type(exc).__name__ in ("EntitiesForbidden", "DTDForbidden", "ExternalReferenceForbidden"):
            raise FormatError("docs.err.xml_entities") from None
        raise


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else str(tag)


def _xml_to_obj(el: ET.Element, warnings: list[Warn]) -> Any:
    obj: dict[str, Any] = {}
    for k, v in el.attrib.items():
        obj["@" + _local(k)] = v
    for child in el:
        if not isinstance(child.tag, str):
            continue
        key = _local(child.tag)
        val = _xml_to_obj(child, warnings)
        if key in obj:
            if isinstance(obj[key], list) and obj.get("\0list:" + key):
                obj[key].append(val)
            else:
                obj[key] = [obj[key], val]
                obj["\0list:" + key] = True
        else:
            obj[key] = val
        if child.tail and child.tail.strip() and not any(w[0] == "docs.warn.xml_mixed" for w in warnings):
            warnings.append(("docs.warn.xml_mixed", {}))
    for marker in [k for k in obj if k.startswith("\0list:")]:
        del obj[marker]
    text = (el.text or "").strip()
    if text:
        if obj:
            obj["#text"] = text
        else:
            return text
    elif not obj:
        return ""
    return obj


_XML_NAME_START = re.compile(r"[A-Za-z_À-˿Ͱ-ͽͿ-῿‌-‍⁰-↏"
                             r"Ⰰ-⿯、-퟿豈-﷏ﷰ-�]")
_XML_NAME_BAD = re.compile(r"[^A-Za-z0-9_.\-·À-˿̀-ͽͿ-῿‌-‍"
                           r"‿-⁀⁰-↏Ⰰ-⿯、-퟿豈-﷏ﷰ-�]")


def _xml_name(name: Any) -> str:
    text = _XML_NAME_BAD.sub("_", str(name))
    if not text:
        return "item"
    if not _XML_NAME_START.match(text[0]):
        text = "_" + text
    return text


def _xml_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (_dt.date, _dt.time, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return repr(value)
    return _strip_xml_illegal(str(value))


def _fill_xml(el: ET.Element, value: Any) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k)
            if key.startswith("@") and not isinstance(v, (dict, list)):
                el.set(_xml_name(key[1:]), _xml_scalar(v))
            elif key == "#text" and not isinstance(v, (dict, list)):
                el.text = _xml_scalar(v)
            elif isinstance(v, list):
                for item in v:
                    _fill_xml(ET.SubElement(el, _xml_name(key)), item)
            else:
                _fill_xml(ET.SubElement(el, _xml_name(key)), v)
    elif isinstance(value, list):
        for item in value:
            _fill_xml(ET.SubElement(el, "item"), item)
    else:
        el.text = _xml_scalar(value)


def obj_to_xml(value: Any) -> str:
    """Serialise data as XML (``@attr`` keys → attributes, ``#text`` → text, lists → repeated elements)."""
    if (isinstance(value, dict) and len(value) == 1):
        (key, inner), = value.items()
        if not str(key).startswith(("@", "#")) and not isinstance(inner, list):
            root = ET.Element(_xml_name(key))
            _fill_xml(root, inner)
        else:
            root = ET.Element("root")
            _fill_xml(root, value)
    else:
        root = ET.Element("root")
        _fill_xml(root, value)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _load_ini(text: str) -> dict:
    def parser() -> configparser.ConfigParser:
        cp = configparser.ConfigParser(interpolation=None, strict=False, allow_no_value=True,
                                       delimiters=("=", ":"), comment_prefixes=("#", ";"),
                                       empty_lines_in_values=False, default_section="\0default")
        cp.optionxform = str  # type: ignore[assignment,method-assign]
        return cp

    cp = parser()
    top = False
    try:
        cp.read_string(text)
    except configparser.MissingSectionHeaderError:
        cp = parser()
        top = True
        try:
            cp.read_string("[\0top]\n" + text)
        except configparser.Error as exc:
            raise _parse_error("ini", str(exc).splitlines()[0], (getattr(exc, "lineno", 1) or 1) - 1) from None
    except configparser.Error as exc:
        raise _parse_error("ini", str(exc).splitlines()[0], getattr(exc, "lineno", None)) from None
    out: dict[str, Any] = {}
    for section in cp.sections():
        values = {k: ("" if v is None else v) for k, v in cp.items(section, raw=True)}
        if top and section == "\0top":
            out.update(values)
        else:
            out[section] = values
    return out


_PROP_ESC_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)")


def _load_env(text: str, *, properties: bool) -> dict:
    out: dict[str, str] = {}
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        s = raw.strip()
        if not s or s.startswith("#") or (properties and s.startswith("!")):
            continue
        if properties:
            while s.endswith("\\") and not s.endswith("\\\\") and i < len(lines):
                s = s[:-1] + lines[i].lstrip()
                i += 1
            m = re.match(r"^((?:\\.|[^=:\s\\])+)\s*[=:\s]\s*(.*)$", s) or re.match(r"^((?:\\.|[^=:\s\\])+)()$", s)
            if not m:
                raise _parse_error("properties", s[:60], i)
            key = _PROP_ESC_RE.sub(lambda mm: _prop_unescape(mm.group(1)), m.group(1))
            out[key] = _PROP_ESC_RE.sub(lambda mm: _prop_unescape(mm.group(1)), m.group(2))
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_.\-]*)\s*=\s*(.*)$", s)
        if not m:
            raise _parse_error("env", s[:60], i)
        key, val = m.group(1), m.group(2)
        if val[:1] in ("'", '"'):
            quote = val[0]
            body = val[1:]
            while quote not in body.replace("\\" + quote, "") and i < len(lines):
                body += "\n" + lines[i]
                i += 1
            end = body.rfind(quote)
            body = body[:end] if end >= 0 else body
            if quote == '"':
                body = (body.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\"))
            val = body
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        out[key] = val
    return out


def _prop_unescape(seq: str) -> str:
    if seq.startswith("u") and len(seq) == 5:
        try:
            return chr(int(seq[1:], 16))
        except ValueError:
            return seq
    return {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(seq, seq)


def load_data(path: str | os.PathLike, ext: str, *, name: str = "",
              limit: int = 200 * 1024 * 1024) -> tuple[Any, str, list[Warn]]:
    """Parse a data file: ``(value, family, warnings)``; raises :class:`FormatError` (with line/column)."""
    fmt = data_format(ext, name)
    if fmt is None:
        raise FormatError("err.not_supported")
    warnings: list[Warn] = []
    p = Path(path)
    if fmt == "plist":
        try:
            data = p.read_bytes()
        except OSError as exc:
            raise FormatError("err.not_readable", details=str(exc), name=p.name) from None
        try:
            if data.lstrip().startswith(b"<"):
                check_xml_safety(data)
            return plistlib.loads(data), fmt, warnings
        except FormatError:
            raise
        except Exception as exc:
            raise _parse_error("plist", str(exc)) from None
    if fmt == "xml":
        try:
            data = p.read_bytes()
        except OSError as exc:
            raise FormatError("err.not_readable", details=str(exc), name=p.name) from None
        if len(data) > limit:
            raise FormatError("docs.err.too_large_text", size=len(data), limit=limit)
        root = safe_xml_fromstring(data)
        return {_local(root.tag): _xml_to_obj(root, warnings)}, fmt, warnings
    dec = read_text(p, limit=limit)
    text = dec.text
    if fmt == "json":
        return _json_loads(text, warnings), fmt, warnings
    if fmt == "jsonc":
        stripped = _drop_trailing_commas(_blank_comments(text))
        if stripped != text:
            warnings.append(("docs.warn.comments_lost", {}))
        return _json_loads(stripped, warnings, "jsonc"), fmt, warnings
    if fmt == "json5":
        converted = _json5_to_json(text)
        if _blank_comments(text) != text:
            warnings.append(("docs.warn.comments_lost", {}))
        return _json_loads(converted, warnings, "json5"), fmt, warnings
    if fmt == "ndjson":
        items = []
        for lineno, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise _parse_error("ndjson", exc.msg, lineno, exc.colno) from None
        return items, fmt, warnings
    if fmt == "yaml":
        if re.search(r"(?m)^\s*#", text):
            warnings.append(("docs.warn.comments_lost", {}))
        return _load_yaml(text), fmt, warnings
    if fmt == "toml":
        if re.search(r"(?m)^\s*#", text):
            warnings.append(("docs.warn.comments_lost", {}))
        return _load_toml(text), fmt, warnings
    if fmt == "ini":
        return _load_ini(text), fmt, warnings
    if fmt in ("env", "properties"):
        return _load_env(text, properties=(fmt == "properties")), fmt, warnings
    raise FormatError("err.not_supported")  # pragma: no cover


def to_json_compatible(value: Any, warnings: list[Warn] | None = None) -> Any:
    """Convert dates, bytes, tuples, sets, Decimals and non-finite floats into JSON-friendly values."""
    def conv(v: Any) -> Any:
        if isinstance(v, dict):
            return {(k if isinstance(k, str) else cell_text(k)): conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple, set, frozenset)):
            return [conv(x) for x in v]
        if isinstance(v, bool) or v is None or isinstance(v, (int, str)):
            return v
        if isinstance(v, float):
            if not math.isfinite(v):
                if warnings is not None and not any(w[0] == "docs.warn.nan" for w in warnings):
                    warnings.append(("docs.warn.nan", {}))
                return None
            return v
        if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
            return v.isoformat()
        if isinstance(v, bytes):
            return base64.b64encode(v).decode("ascii")
        try:
            import decimal

            if isinstance(v, decimal.Decimal):
                return float(v)
        except ImportError:  # pragma: no cover
            pass
        return str(v)

    return conv(value)


def _json_path(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    k = str(key)
    return f"{path}.{k}" if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k) else f"{path}[{json.dumps(k, ensure_ascii=False)}]"


def _find_none(value: Any, path: str = "$") -> str | None:
    if value is None:
        return path
    if isinstance(value, dict):
        for k, v in value.items():
            hit = _find_none(v, _json_path(path, k))
            if hit:
                return hit
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            hit = _find_none(v, _json_path(path, i))
            if hit:
                return hit
    return None


_TOML_BARE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOML_ESC = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"}


def _toml_str(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _TOML_ESC:
            out.append(_TOML_ESC[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_key(key: Any) -> str:
    k = str(key)
    return k if _TOML_BARE_RE.match(k) else _toml_str(k)


def _toml_value(v: Any, path: str) -> str:
    if v is None:
        raise FormatError("docs.err.toml_null", path=path)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        text = repr(v)
        return text if any(c in text for c in ".eEn") else text + ".0"
    if isinstance(v, str):
        return _toml_str(v)
    if isinstance(v, _dt.datetime):
        return v.isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, _dt.time):
        return v.replace(tzinfo=None).isoformat()
    if isinstance(v, bytes):
        return _toml_str(base64.b64encode(v).decode("ascii"))
    if isinstance(v, (list, tuple, set, frozenset)):
        return "[" + ", ".join(_toml_value(x, _json_path(path, i)) for i, x in enumerate(v)) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_toml_key(k)} = {_toml_value(x, _json_path(path, k))}" for k, x in v.items()) + "}"
    return _toml_str(str(v))


def _is_aot(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _toml_table(table: dict, path: tuple, jpath: str, lines: list[str], header: bool, aot: bool = False) -> None:
    if header:
        name = ".".join(_toml_key(p) for p in path)
        if lines:
            lines.append("")
        lines.append(f"[[{name}]]" if aot else f"[{name}]")
    for k, v in table.items():
        if not isinstance(v, dict) and not _is_aot(v):
            lines.append(f"{_toml_key(k)} = {_toml_value(v, _json_path(jpath, k))}")
    for k, v in table.items():
        if isinstance(v, dict):
            has_simple = any(not isinstance(x, dict) and not _is_aot(x) for x in v.values())
            _toml_table(v, path + (str(k),), _json_path(jpath, k), lines, header=has_simple or not v)
    for k, v in table.items():
        if _is_aot(v):
            for i, item in enumerate(v):
                _toml_table(item, path + (str(k),), _json_path(_json_path(jpath, k), i), lines, header=True, aot=True)


def toml_dumps(data: Any) -> str:
    """Serialise a dict as TOML 1.0 (``tomli_w`` when installed, a built-in writer otherwise).

    Raises :class:`FormatError` ``docs.err.toml_null`` (with the JSON path) for ``None`` values and
    ``docs.err.toml_top`` when the top level is not a table.
    """
    if not isinstance(data, dict):
        raise FormatError("docs.err.toml_top")
    missing = _find_none(data)
    if missing:
        raise FormatError("docs.err.toml_null", path=missing)
    try:
        import tomli_w  # type: ignore

        return tomli_w.dumps(_toml_prepare(data))
    except ImportError:
        pass
    except (TypeError, ValueError):
        pass
    lines: list[str] = []
    _toml_table({str(k): v for k, v in data.items()}, (), "$", lines, header=False)
    return "\n".join(lines).strip() + "\n"


def _toml_prepare(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _toml_prepare(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_toml_prepare(x) for x in v]
    if isinstance(v, bytes):
        return base64.b64encode(v).decode("ascii")
    return v


def yaml_dumps(value: Any) -> str:
    """YAML (block style, unicode, key order kept; ambiguous strings like "no" are quoted)."""
    import yaml  # type: ignore

    def prep(v: Any) -> Any:
        if isinstance(v, dict):
            return {(k if isinstance(k, (str, int, float, bool)) or k is None else str(k)): prep(x) for k, x in v.items()}
        if isinstance(v, (list, tuple, set, frozenset)):
            return [prep(x) for x in v]
        return v

    dumper = getattr(yaml, "CSafeDumper", None) or yaml.SafeDumper
    return yaml.dump(prep(value), Dumper=dumper, sort_keys=False, allow_unicode=True, default_flow_style=False,
                     width=4096, indent=2)


def dump_data(value: Any, fmt: str, warnings: list[Warn] | None = None, *, indent: int = 2) -> str:
    """Serialise ``value`` as ``json`` / ``yaml`` / ``toml`` / ``xml`` text."""
    warnings = warnings if warnings is not None else []
    if fmt == "json":
        return json.dumps(to_json_compatible(value, warnings), ensure_ascii=False, indent=indent) + "\n"
    if fmt == "yaml":
        return yaml_dumps(value)
    if fmt == "toml":
        if isinstance(value, list):
            warnings.append(("docs.warn.toml_wrapped", {}))
            value = {"items": value}
        return toml_dumps(value)
    if fmt == "xml":
        return obj_to_xml(value)
    raise FormatError("err.not_supported")


def json_minify(value: Any, warnings: list[Warn] | None = None) -> str:
    """Compact JSON text."""
    return json.dumps(to_json_compatible(value, warnings), ensure_ascii=False, separators=(",", ":")) + "\n"


def json_pretty(value: Any, warnings: list[Warn] | None = None, indent: int = 2) -> str:
    """Indented JSON text."""
    return json.dumps(to_json_compatible(value, warnings), ensure_ascii=False, indent=indent) + "\n"


def sort_keys(value: Any) -> Any:
    """Recursively sort dictionary keys (lists keep their order)."""
    if isinstance(value, dict):
        return {k: sort_keys(value[k]) for k in sorted(value, key=lambda x: (str(x).lower(), str(x)))}
    if isinstance(value, list):
        return [sort_keys(v) for v in value]
    return value


def _flatten(value: Any, prefix: str = "", out: dict | None = None) -> dict:
    out = {} if out is None else out
    if isinstance(value, dict):
        if not value and prefix:
            out[prefix] = ""
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            _flatten(v, key, out)
    elif isinstance(value, (list, tuple)):
        out[prefix or "value"] = json.dumps(to_json_compatible(value), ensure_ascii=False)
    else:
        out[prefix or "value"] = value
    return out


def rows_from_data(value: Any) -> list[list[Any]]:
    """Table rows (header first) from a list of flat objects; nested keys become dotted columns."""
    items: Any = value
    if isinstance(value, dict):
        lists = [v for v in value.values() if isinstance(v, list)]
        if len(value) == 1 and lists and all(isinstance(x, dict) for x in lists[0]):
            items = lists[0]
        else:
            items = [value]
    if not isinstance(items, list):
        items = [items]
    if not items:
        return []
    if all(not isinstance(x, (dict, list)) for x in items):
        return [["value"]] + [[x] for x in items]
    flat = [_flatten(x) if isinstance(x, dict) else _flatten(x, "value") for x in items]
    header: list[str] = []
    seen: set[str] = set()
    for rec in flat:
        for k in rec:
            if k not in seen:
                seen.add(k)
                header.append(k)
    rows: list[list[Any]] = [list(header)]
    for rec in flat:
        row = []
        for h in header:
            v = rec.get(h)
            if isinstance(v, (_dt.date, _dt.time, _dt.datetime)):
                v = v.isoformat()
            elif isinstance(v, bytes):
                v = base64.b64encode(v).decode("ascii")
            row.append(v)
        rows.append(row)
    return rows


# ------------------------------------------------------------------------- XML pretty / minify

def _parse_dom(data: bytes) -> Any:
    check_xml_safety(data)
    from xml.parsers.expat import ExpatError

    try:
        try:
            import defusedxml.minidom as dmini  # type: ignore

            return dmini.parseString(data)
        except ImportError:
            from xml.dom import minidom

            return minidom.parseString(data)
    except ExpatError as exc:
        raise _parse_error("xml", str(exc).split(":")[0], getattr(exc, "lineno", None),
                           (getattr(exc, "offset", 0) or 0) + 1) from None


def _esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;").replace("\n", "&#10;")
            .replace("\t", "&#9;").replace("\r", "&#13;"))


def _ser_dom(node: Any, out: list[str], indent: str, level: int, pretty: bool, comments: bool) -> None:
    from xml.dom import Node

    t = node.nodeType
    if t == Node.ELEMENT_NODE:
        attrs = "".join(f' {name}="{_esc_attr(value)}"' for name, value in node.attributes.items())
        if node.getAttribute("xml:space") == "preserve":
            inner = "".join(c.toxml() for c in node.childNodes)
            out.append(f"<{node.tagName}{attrs}>{inner}</{node.tagName}>" if inner else f"<{node.tagName}{attrs}/>")
            return
        kids = [c for c in node.childNodes
                if not (c.nodeType == Node.TEXT_NODE and not c.data.strip())
                and (comments or c.nodeType != Node.COMMENT_NODE)]
        if not kids:
            out.append(f"<{node.tagName}{attrs}/>")
            return
        has_text = any(c.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE) for c in kids)
        has_elem = any(c.nodeType == Node.ELEMENT_NODE for c in kids)
        if has_text and has_elem:
            # mixed content: whitespace is significant, keep it untouched
            inner = "".join(c.toxml() for c in node.childNodes if comments or c.nodeType != Node.COMMENT_NODE)
            out.append(f"<{node.tagName}{attrs}>{inner}</{node.tagName}>")
            return
        if not has_elem and all(c.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE) for c in kids):
            text = "".join(_esc_text(c.data) if c.nodeType == Node.TEXT_NODE else f"<![CDATA[{c.data}]]>"
                           for c in node.childNodes if c.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE))
            if not pretty:
                text = text.strip()
            out.append(f"<{node.tagName}{attrs}>{text}</{node.tagName}>")
            return
        out.append(f"<{node.tagName}{attrs}>")
        for child in kids:
            if pretty:
                out.append("\n" + indent * (level + 1))
            _ser_dom(child, out, indent, level + 1, pretty, comments)
        if pretty:
            out.append("\n" + indent * level)
        out.append(f"</{node.tagName}>")
    elif t == Node.TEXT_NODE:
        out.append(_esc_text(node.data.strip() if not pretty else node.data.strip()))
    elif t == Node.CDATA_SECTION_NODE:
        out.append(f"<![CDATA[{node.data}]]>")
    elif t == Node.COMMENT_NODE:
        out.append(f"<!--{node.data}-->")
    elif t == Node.PROCESSING_INSTRUCTION_NODE:
        out.append(f"<?{node.target} {node.data}?>" if node.data else f"<?{node.target}?>")


def _xml_rewrite(data: bytes, pretty: bool, indent: str = "  ") -> str:
    from xml.dom import Node

    doc = _parse_dom(data)
    out: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    for child in doc.childNodes:
        if child.nodeType == Node.DOCUMENT_TYPE_NODE:
            out.append("\n" if pretty else "")
            out.append(child.toxml())
            continue
        if child.nodeType == Node.COMMENT_NODE and not pretty:
            continue
        out.append("\n" if pretty else "")
        _ser_dom(child, out, indent, 0, pretty, comments=pretty)
    return "".join(out) + "\n"


def xml_pretty(data: bytes, indent: str = "  ") -> str:
    """Re-indent XML (prefixes, comments, PIs, DOCTYPE and mixed content preserved)."""
    return _xml_rewrite(data, True, indent)


def xml_minify(data: bytes) -> str:
    """Remove indentation whitespace and comments from XML (mixed content preserved)."""
    return _xml_rewrite(data, False)


# ================================================================================ office zips

OOXML_EXTS = frozenset({"docx", "docm", "dotx", "dotm", "xlsx", "xlsm", "xltx", "xltm", "pptx", "pptm", "ppsx",
                        "ppsm", "potx", "potm"})
ODF_EXTS = frozenset({"odt", "ott", "ods", "ots", "odp", "otp", "odg", "otg"})
#: Folders that hold embedded pictures in OOXML (word/ppt/xl) and ODF (Pictures/) packages.
OFFICE_MEDIA_PREFIXES = ("word/media/", "ppt/media/", "xl/media/", "Pictures/")
IMAGE_EXTS = frozenset({"jpg", "jpeg", "jpe", "jfif", "png", "gif", "bmp", "tif", "tiff", "webp", "svg", "emf", "wmf",
                        "avif", "heic", "jxl"})
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _zip(path: str | os.PathLike) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FormatError("err.damaged", details=str(exc)) from None


#: largest package member read into memory (an XML part, a picture, a cover…)
_MEMBER_LIMIT = 64 * 1024 * 1024
#: content.xml of an ODS holds every cell of the workbook, so a big sheet legitimately goes far beyond the above
_SHEET_LIMIT = 256 * 1024 * 1024
#: sum of the embedded pictures held in memory while they are extracted
_MEDIA_LIMIT = 1024 * 1024 * 1024
#: sum of the declared sizes a whole-package rewrite accepts: real packages embed long videos, which are
#: streamed (see _copy_member), but this much zeros from a tiny file is plainly not a document
_PACKAGE_LIMIT = 8 * 1024 * 1024 * 1024
#: Real office and EPUB packages use only these. For bzip2 and LZMA, zipfile decompresses a whole compressed chunk
#: in one call whatever size is passed to read(), so a read cap would not bound them.
_SAFE_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)


def _read_member(zf: zipfile.ZipFile, member: str | zipfile.ZipInfo, limit: int | None = None) -> bytes:
    """One package member, bounded: a 247-byte zip can declare gigabytes of zeros.

    Raises :class:`FormatError` ``docs.err.too_big`` (over ``limit``, or an unsafe compression method) or
    ``err.damaged``; ``KeyError`` for a missing member, like :meth:`zipfile.ZipFile.read`."""
    limit = _MEMBER_LIMIT if limit is None else limit
    info = member if isinstance(member, zipfile.ZipInfo) else zf.getinfo(member)
    if info.compress_type not in _SAFE_METHODS or info.file_size > limit:
        raise FormatError("docs.err.too_big")
    try:
        with zf.open(info) as fh:
            return fh.read(limit)
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise FormatError("err.damaged", details=str(exc)) from None


def _check_package(infos: Iterable[zipfile.ZipInfo]) -> None:
    """Refuse a package before a whole-package rewrite starts writing."""
    total = 0
    for info in infos:
        total += info.file_size
        if info.compress_type not in _SAFE_METHODS or total > _PACKAGE_LIMIT:
            raise FormatError("docs.err.too_big")


def _copy_member(zin: zipfile.ZipFile, info: zipfile.ZipInfo, zout: zipfile.ZipFile, zi: zipfile.ZipInfo,
                 check: Callable[[], None] | None = None) -> None:
    """Copy a member too large to hold in memory (an embedded video); ``zi`` says how to store it."""
    zi.file_size = info.file_size                     # lets zipfile choose ZIP64 before the data is written
    try:
        with zin.open(info) as src, zout.open(zi, "w") as dst:
            while chunk := src.read(1024 * 1024):
                if check is not None:
                    check()
                dst.write(chunk)
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise FormatError("err.damaged", details=str(exc)) from None


def office_kind(path: str | os.PathLike) -> str | None:
    """``"ooxml"``, ``"odf"``, ``"cfb"`` (OLE2: legacy binary or encrypted OOXML) or ``None``."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head == _CFB_MAGIC:
        return "cfb"
    if not head.startswith(b"PK"):
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" in names:
                return "ooxml"
            if "mimetype" in names:
                # bounded: classifying a file must not inflate it (a bomb simply is not this kind)
                mt = _zip_member_head(zf, "mimetype", 200)
                if mt and mt.startswith(b"application/vnd.oasis.opendocument"):
                    return "odf"
            if "content.xml" in names and "META-INF/manifest.xml" in names:
                return "odf"
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
    return None


def office_encrypted(path: str | os.PathLike, ext: str) -> bool:
    """True for password-protected OOXML (stored in an OLE2 container) and encrypted ODF packages."""
    kind = office_kind(path)
    if kind == "cfb":
        return ext.lower() in OOXML_EXTS
    if kind == "odf":
        try:
            with zipfile.ZipFile(path) as zf:
                manifest = _read_member(zf, "META-INF/manifest.xml")
            return b"encryption-data" in manifest
        except (zipfile.BadZipFile, KeyError, OSError, FormatError):
            return False
    return False


def _sorted_infos(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = [i for i in zf.infolist()]
    infos.sort(key=lambda i: 0 if i.filename == "mimetype" else 1)
    return infos


def _docx_paragraphs(data: bytes) -> list[str]:
    paras: list[str] = []
    for _ev, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag == f"{{{_W}}}p":
            parts: list[str] = []
            for node in el.iter():
                tag = node.tag
                if tag == f"{{{_W}}}t":
                    parts.append(node.text or "")
                elif tag == f"{{{_W}}}tab":
                    parts.append("\t")
                elif tag in (f"{{{_W}}}br", f"{{{_W}}}cr"):
                    parts.append("\n")
                elif tag == f"{{{_W}}}noBreakHyphen":
                    parts.append("‑")
            paras.append("".join(parts))
            el.clear()
    return paras


def _a_paragraphs(root: ET.Element) -> list[str]:
    out = []
    for p in root.iter(f"{{{_A}}}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{{{_A}}}t":
                parts.append(node.text or "")
            elif node.tag == f"{{{_A}}}br":
                parts.append("\n")
        text = "".join(parts)
        if text.strip():
            out.append(text)
    return out


def office_text(path: str | os.PathLike, ext: str) -> str:
    """Plain text of a DOCX/ODT/ODS/ODP/PPTX package (paragraphs separated by newlines)."""
    ext = ext.lower()
    with _zip(path) as zf:
        names = set(zf.namelist())
        if "word/document.xml" in names:
            data = _read_member(zf, "word/document.xml")
            check_xml_safety(data[:262144])
            return "\n".join(_docx_paragraphs(data))
        if "ppt/presentation.xml" in names:
            slides = pptx_slides(path)
            return "\n\n".join("\n".join(s["paragraphs"]) for s in slides)
        if "content.xml" in names:
            root = safe_xml_fromstring(_read_member(zf, "content.xml"))
            return "\n".join(odf_text(p) for p in odf_paragraphs(root))
    raise FormatError("err.not_supported")


def odf_paragraphs(node: ET.Element) -> Iterable[ET.Element]:
    """Top-level ``text:p``/``text:h`` elements under ``node`` (comments and nested paragraphs excluded;
    nested text such as text boxes is read through :func:`odf_text` of the outer paragraph)."""
    for child in node:
        if child.tag in (_q("text", "p"), _q("text", "h")):
            yield child
        elif child.tag in (_q("office", "annotation"), _q("office", "annotation-end")):
            continue
        else:
            yield from odf_paragraphs(child)


def office_media(path: str | os.PathLike, skipped: list[str] | None = None) -> list[tuple[str, bytes]]:
    """Embedded pictures of an office package as ``[(file name, bytes)]`` in natural order.

    Members too large to hold in memory (embedded videos above all) are left out and their names added to
    ``skipped``; :class:`FormatError` ``docs.err.too_big`` when nothing else is left, or the rest is too much."""
    out: list[tuple[str, bytes]] = []
    with _zip(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and i.filename.startswith(OFFICE_MEDIA_PREFIXES)]
        infos.sort(key=lambda i: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", i.filename)])
        fit = [i for i in infos if i.compress_type in _SAFE_METHODS and i.file_size <= _MEMBER_LIMIT]
        if (infos and not fit) or sum(i.file_size for i in fit) > _MEDIA_LIMIT:
            raise FormatError("docs.err.too_big")
        if skipped is not None:
            kept = {id(i) for i in fit}
            skipped.extend(os.path.basename(i.filename) for i in infos if id(i) not in kept)
        for info in fit:
            out.append((os.path.basename(info.filename), _read_member(zf, info)))
    return out


def office_fonts(path: str | os.PathLike) -> list[str]:
    """Font family names referenced by an office package (theme references skipped)."""
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str | None) -> None:
        if not name:
            return
        n = name.strip().strip("'\"").strip()
        if not n or n.startswith(("+", "@")) or n.lower() in seen:
            return
        seen.add(n.lower())
        names.append(n)

    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return []
    with zf:
        files = set(zf.namelist())
        try:
            if "word/fontTable.xml" in files:
                root = safe_xml_fromstring(_read_member(zf, "word/fontTable.xml"))
                for font in root.iter(f"{{{_W}}}font"):
                    add(font.get(f"{{{_W}}}name"))
            if "xl/styles.xml" in files:
                root = safe_xml_fromstring(_read_member(zf, "xl/styles.xml"))
                for font in root.iter(f"{{{_SML}}}name"):
                    add(font.get("val"))
            for name in sorted(files):
                if re.match(r"^ppt/(theme/theme\d+|slides/slide\d+)\.xml$", name):
                    root = safe_xml_fromstring(_read_member(zf, name))
                    for el in root.iter(f"{{{_A}}}latin"):
                        add(el.get("typeface"))
            for name in ("styles.xml", "content.xml"):
                if name in files and "mimetype" in files:
                    root = safe_xml_fromstring(_read_member(zf, name))
                    for el in root.iter(_q("style", "font-face")):
                        add(el.get(_q("svg", "font-family")) or el.get(_q("style", "name")))
        except (FormatError, KeyError, zipfile.BadZipFile):
            return names
    return names


def office_app_stats(path: str | os.PathLike) -> dict[str, int]:
    """Statistics saved by the authoring application (docProps/app.xml or ODF meta.xml)."""
    out: dict[str, int] = {}
    try:
        with zipfile.ZipFile(path) as zf:
            files = set(zf.namelist())
            if "docProps/app.xml" in files:
                root = safe_xml_fromstring(_read_member(zf, "docProps/app.xml"))
                for el in root:
                    key = _local(el.tag)
                    if key in ("Pages", "Words", "Characters", "CharactersWithSpaces", "Paragraphs", "Lines",
                               "Slides", "Notes", "HiddenSlides", "TotalTime") and (el.text or "").strip().isdigit():
                        out[key] = int(el.text.strip())
            elif "meta.xml" in files:
                root = safe_xml_fromstring(_read_member(zf, "meta.xml"))
                for el in root.iter(_q("meta", "document-statistic")):
                    for attr, value in el.attrib.items():
                        if value.isdigit():
                            out[_local(attr)] = int(value)
    except (zipfile.BadZipFile, OSError, KeyError, FormatError):
        pass
    return out


_CORE_EMPTY = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
               b' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
               b' xmlns:dcmitype="http://purl.org/dc/dcmitype/"'
               b' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"></cp:coreProperties>')
_ODF_META_EMPTY = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
                   b'<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
                   b' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"'
                   b' xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.3"><office:meta/>'
                   b'</office:document-meta>')
_APP_PRIVATE_RE = re.compile(rb"<(Company|Manager|Template|HyperlinkBase|TotalTime)>.*?</\1>"
                             rb"|<(?:Company|Manager|Template|HyperlinkBase|TotalTime)\s*/>", re.S)
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)
#: the relationship that holds the path of the template a DOCX was created from
#: (``file:///C:\Users\<name>\…\Template.dotx``, TargetMode="External")
_TEMPLATE_REL_RE = re.compile(rb"<Relationship\b[^>]*\bType=([\"'])[^\"']*/attachedTemplate\1[^>]*/>")
#: Excel's ``<x15ac:absPath url="C:\Users\<name>\…\"/>`` (the folder the workbook was saved in),
#: wrapped in an ``<mc:AlternateContent>`` block, plus any bare occurrence
_ABSPATH_BLOCK_RE = re.compile(rb"<([\w.-]+):AlternateContent\b[^>]*>(?:(?!</\1:AlternateContent>).)*?"
                               rb"<[\w.-]+:absPath\b(?:(?!</\1:AlternateContent>).)*</\1:AlternateContent>", re.S)
_ABSPATH_RE = re.compile(rb"<[\w.-]+:absPath\b[^>]*?(?:/>|>.*?</[\w.-]+:absPath>)", re.S)
#: SharePoint document-library properties mirrored into customXml (people columns)
_SP_PROPERTIES_NS = b"schemas.microsoft.com/office/2006/metadata/properties"
_SP_DISPLAY_NAME_RE = re.compile(rb"<((?:[\w.-]+:)?DisplayName)>[^<]*</\1>")


def _anonymize_part(name: str, data: bytes) -> bytes:
    if name.startswith("word/") and name.endswith(".xml"):
        data = re.sub(rb'\b(w:author|w:initials|w15:author|w15:userId|w15:providerId)="[^"]*"', rb'\1=""', data)
        data = re.sub(rb'\sw:date="[^"]*"', b"", data)
        data = re.sub(rb"<w15:presenceInfo\b[^>]*/>", b"", data)
        if name == "word/settings.xml":
            data = re.sub(rb"<w:attachedTemplate\b[^>]*/>", b"", data)
    elif name == "word/_rels/settings.xml.rels":
        data = _TEMPLATE_REL_RE.sub(b"", data)
    elif re.match(r"^ppt/(commentAuthors|authors)\.xml$", name):
        data = re.sub(rb'\b(name|initials|userId|providerId)="[^"]*"', rb'\1=""', data)
    elif re.match(r"^xl/comments\d*\.xml$", name):
        data = re.sub(rb"<author>[^<]*</author>", b"<author></author>", data)
    elif name.startswith("xl/persons/") and name.endswith(".xml"):
        data = re.sub(rb'\b(displayName|userId|providerId)="[^"]*"', rb'\1=""', data)
    elif name == "xl/workbook.xml":
        data = _ABSPATH_RE.sub(b"", _ABSPATH_BLOCK_RE.sub(b"", data))
        data = re.sub(rb'(<(?:[\w.-]+:)?fileSharing\b[^>]*\buserName=)"[^"]*"', rb'\1""', data)
    elif re.match(r"^customXml/item\d+\.xml$", name) and _SP_PROPERTIES_NS in data:
        data = _SP_DISPLAY_NAME_RE.sub(rb"<\1></\1>", data)
    return data


def strip_office_metadata(src: str | os.PathLike, dst: str | os.PathLike) -> int:
    """Copy an OOXML/ODF package without personal metadata; returns the number of rewritten parts.

    Removed: core properties (author, last editor, dates, title, subject, keywords…), company /
    manager / template / editing time, custom properties, comment and tracked-change author names
    and dates, the attached template path (setting and relationship), Excel's save folder
    (``absPath``) and file-sharing user, SharePoint people names, ODF ``meta.xml``; ZIP entry
    timestamps are reset.
    """
    kind = office_kind(src)
    if kind not in ("ooxml", "odf"):
        raise FormatError("err.not_supported")
    changed = 0
    with _zip(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        names = set(zin.namelist())
        custom = "docProps/custom.xml" in names
        infos = _sorted_infos(zin)
        _check_package(infos)
        for info in infos:
            name = info.filename
            # Everything rewritten below is an XML part. Anything else may be a long embedded video.
            if info.file_size > _MEMBER_LIMIT and not name.endswith((".xml", ".rels")):
                zi = zipfile.ZipInfo(name, date_time=_FIXED_DATE)
                zi.external_attr = info.external_attr
                zi.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
                _copy_member(zin, info, zout, zi)
                continue
            data = _read_member(zin, info)
            original = data
            if kind == "ooxml":
                if name == "docProps/custom.xml":
                    changed += 1
                    continue
                if name == "docProps/core.xml":
                    data = _CORE_EMPTY
                elif name == "docProps/app.xml":
                    data = _APP_PRIVATE_RE.sub(b"", data)
                elif name == "_rels/.rels" and custom:
                    data = re.sub(rb"<Relationship\b[^>]*Target=\"/?docProps/custom\.xml\"[^>]*/>", b"", data)
                elif name == "[Content_Types].xml" and custom:
                    data = re.sub(rb"<Override\b[^>]*PartName=\"/docProps/custom\.xml\"[^>]*/>", b"", data)
                else:
                    data = _anonymize_part(name, data)
            else:
                if name == "meta.xml":
                    data = _ODF_META_EMPTY
                elif name in ("content.xml", "styles.xml"):
                    data = re.sub(rb"<dc:creator>[^<]*</dc:creator>", b"<dc:creator/>", data)
                    data = re.sub(rb"<dc:date>[^<]*</dc:date>", b"<dc:date>1970-01-01T00:00:00</dc:date>", data)
                    data = re.sub(rb"<meta:creator-initials>[^<]*</meta:creator-initials>", b"", data)
            if data != original:
                changed += 1
            zi = zipfile.ZipInfo(name, date_time=_FIXED_DATE)
            zi.external_attr = info.external_attr
            zi.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)
    return changed


_STORED_EXTS = {"jpg", "jpeg", "png", "gif", "mp3", "mp4", "m4a", "m4v", "webp", "avif", "zip", "jxl", "heic"}


def _recompress_image(data: bytes, ext: str, quality: int, max_px: int) -> bytes | None:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        raise
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    fmt = (im.format or "").upper()
    resample = getattr(Image, "Resampling", Image).LANCZOS
    try:
        if fmt == "JPEG" and ext in ("jpg", "jpeg", "jpe", "jfif"):
            exif = im.info.get("exif") or b""
            icc = im.info.get("icc_profile")
            if max_px and max(im.size) > max_px:
                im = im.copy()
                im.thumbnail((max_px, max_px), resample)
            if im.mode not in ("RGB", "L", "CMYK"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            kwargs: dict[str, Any] = {"quality": quality, "optimize": True, "progressive": True}
            if exif:
                kwargs["exif"] = exif
            if icc:
                kwargs["icc_profile"] = icc
            im.save(buf, "JPEG", **kwargs)
            return buf.getvalue()
        if fmt == "PNG" and ext == "png":
            icc = im.info.get("icc_profile")
            if max_px and max(im.size) > max_px:
                if im.mode in ("P", "1"):
                    im = im.convert("RGBA" if "transparency" in im.info else "RGB")
                im = im.copy()
                im.thumbnail((max_px, max_px), resample)
            buf = io.BytesIO()
            kwargs = {"optimize": True}
            if icc:
                kwargs["icc_profile"] = icc
            if im.mode == "P" and "transparency" in im.info:
                kwargs["transparency"] = im.info["transparency"]
            im.save(buf, "PNG", **kwargs)
            return buf.getvalue()
    except Exception:
        return None
    return None


def compress_office_media(src: str | os.PathLike, dst: str | os.PathLike, *, quality: int = 80,
                          max_px: int = 2000, progress: Callable[[float], None] | None = None,
                          check: Callable[[], None] | None = None) -> tuple[int, int]:
    """Re-encode the JPEG/PNG pictures of an office package (names kept, so references stay valid)
    and re-zip it at maximum deflate; returns ``(pictures, pictures_shrunk)``."""
    with _zip(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        infos = _sorted_infos(zin)
        _check_package(infos)
        media = [i for i in infos if not i.is_dir() and i.filename.startswith(OFFICE_MEDIA_PREFIXES)
                 and i.filename.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "jpe", "jfif", "png")
                 and i.file_size <= _MEMBER_LIMIT]
        media_names = {i.filename for i in media}
        done = shrunk = 0
        for info in infos:
            if check is not None:
                check()
            ext = info.filename.rsplit(".", 1)[-1].lower() if "." in info.filename else ""
            if info.file_size > _MEMBER_LIMIT:              # a long embedded video: kept as it is, not in memory
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.external_attr = info.external_attr
                stored = info.filename == "mimetype" or ext in _STORED_EXTS
                zi.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
                _copy_member(zin, info, zout, zi, check)
                continue
            data = _read_member(zin, info)
            if info.filename in media_names:
                new = _recompress_image(data, ext, quality, max_px)
                if new is not None and len(new) < len(data) * 0.97:
                    data = new
                    shrunk += 1
                done += 1
                if progress is not None:
                    progress(done / max(1, len(media)))
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.external_attr = info.external_attr
            if info.filename == "mimetype":
                zout.writestr(zi, data, compress_type=zipfile.ZIP_STORED)
            elif ext in _STORED_EXTS:
                zout.writestr(zi, data, compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(zi, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return len(media), shrunk


def _rels(zf: zipfile.ZipFile, rels_path: str) -> dict[str, tuple[str, str]]:
    """``{rId: (type, target)}`` of a ``.rels`` part."""
    try:
        root = safe_xml_fromstring(_read_member(zf, rels_path))
    except KeyError:
        return {}
    out = {}
    for rel in root.iter(f"{{{_REL}}}Relationship"):
        out[rel.get("Id", "")] = (rel.get("Type", ""), rel.get("Target", ""))
    return out


def _resolve(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return os.path.normpath(os.path.join(base_dir, target)).replace("\\", "/")


def _placeholder_type(sp: ET.Element) -> str:
    ph = sp.find(f".//{{{_P}}}nvPr/{{{_P}}}ph")
    if ph is None:
        return ""
    return ph.get("type", "body")


def pptx_slides(path: str | os.PathLike) -> list[dict[str, Any]]:
    """Slides of a PPTX in presentation order: ``[{title, paragraphs, notes, hidden}]``."""
    slides: list[dict[str, Any]] = []
    with _zip(path) as zf:
        try:
            pres = safe_xml_fromstring(_read_member(zf, "ppt/presentation.xml"))
        except KeyError:
            raise FormatError("err.damaged") from None
        rels = _rels(zf, "ppt/_rels/presentation.xml.rels")
        targets = []
        for sld in pres.iter(f"{{{_P}}}sldId"):
            rid = sld.get(f"{{{_R}}}id")
            if rid in rels:
                targets.append(_resolve("ppt", rels[rid][1]))
        for target in targets:
            try:
                root = safe_xml_fromstring(_read_member(zf, target))
            except KeyError:
                continue
            title = ""
            paragraphs: list[str] = []
            for sp in root.iter(f"{{{_P}}}sp"):
                kind = _placeholder_type(sp)
                if kind in ("sldNum", "dt", "ftr"):
                    continue
                paras = _a_paragraphs(sp)
                if kind in ("title", "ctrTitle") and not title:
                    title = " ".join(p.replace("\n", " ") for p in paras).strip()
                    continue
                paragraphs.extend(paras)
            for frame in root.iter(f"{{{_P}}}graphicFrame"):
                for tbl in frame.iter(f"{{{_A}}}tbl"):
                    for tr in tbl.iter(f"{{{_A}}}tr"):
                        cells = [" ".join(_a_paragraphs(tc)) for tc in tr.iter(f"{{{_A}}}tc")]
                        if any(c.strip() for c in cells):
                            paragraphs.append(" | ".join(cells))
            notes = ""
            base = os.path.dirname(target)
            srels = _rels(zf, f"{base}/_rels/{os.path.basename(target)}.rels")
            for typ, tgt in srels.values():
                if typ.endswith("/notesSlide"):
                    try:
                        nroot = safe_xml_fromstring(_read_member(zf, _resolve(base, tgt)))
                    except KeyError:
                        continue
                    note_paras: list[str] = []
                    for sp in nroot.iter(f"{{{_P}}}sp"):
                        if _placeholder_type(sp) in ("body", ""):
                            note_paras.extend(_a_paragraphs(sp))
                    notes = "\n".join(note_paras).strip()
            slides.append({"title": title, "paragraphs": paragraphs, "notes": notes,
                           "hidden": root.get("show") == "0"})
    return slides


def odp_slides(path: str | os.PathLike) -> list[dict[str, Any]]:
    """Slides of an ODP: ``[{title, paragraphs, notes, hidden}]``."""
    with _zip(path) as zf:
        try:
            root = safe_xml_fromstring(_read_member(zf, "content.xml"))
        except KeyError:
            raise FormatError("err.damaged") from None
    slides: list[dict[str, Any]] = []
    notes_tag = _q("presentation", "notes")
    para_tags = (_q("text", "p"), _q("text", "h"))
    for page in root.iter(_q("draw", "page")):
        title = ""
        paragraphs: list[str] = []
        notes: list[str] = []

        def walk(node: ET.Element, in_notes: bool, cls: str) -> None:
            nonlocal title
            for child in node:
                if child.tag == notes_tag:
                    walk(child, True, "")
                    continue
                if child.tag == _q("draw", "frame"):
                    walk(child, in_notes, child.get(_q("presentation", "class"), ""))
                    continue
                if child.tag in para_tags:
                    text = odf_text(child)
                    if not text.strip():
                        continue
                    if in_notes:
                        notes.append(text)
                    elif cls == "title" and not title:
                        title = text.strip()
                    elif cls not in ("page-number", "date-time", "footer", "page"):
                        paragraphs.append(text)
                    continue
                walk(child, in_notes, cls)

        walk(page, False, "")
        slides.append({"title": title or page.get(_q("draw", "name"), ""), "paragraphs": paragraphs,
                       "notes": "\n".join(notes).strip(), "hidden": False})
    return slides


# ===================================================================================== ebooks

_OPF = "http://www.idpf.org/2007/opf"
_DC = "http://purl.org/dc/elements/1.1/"
_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
_FB2 = "http://www.gribuser.ru/xml/fictionbook/2.0"
_XLINK = "http://www.w3.org/1999/xlink"
_FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
_IMG_MIME_EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/gif": "gif",
                 "image/webp": "webp", "image/svg+xml": "svg", "image/avif": "avif"}


def _epub_opf(zf: zipfile.ZipFile) -> tuple[str, ET.Element]:
    try:
        container = safe_xml_fromstring(_read_member(zf, "META-INF/container.xml"))
    except KeyError:
        raise FormatError("err.damaged", details="META-INF/container.xml missing") from None
    rootfile = container.find(f".//{{{_CONTAINER}}}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise FormatError("err.damaged", details="no rootfile")
    opf_path = rootfile.get("full-path", "")
    try:
        return opf_path, safe_xml_fromstring(_read_member(zf, opf_path))
    except KeyError:
        raise FormatError("err.damaged", details=f"{opf_path} missing") from None


def epub_drm(path: str | os.PathLike) -> bool:
    """True when the EPUB is DRM-protected (encrypted content beyond font obfuscation, or rights.xml)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "META-INF/rights.xml" in names:
                return True
            if "META-INF/encryption.xml" in names:
                data = _read_member(zf, "META-INF/encryption.xml")
                algos = set(re.findall(rb'Algorithm="([^"]+)"', data))
                return any(a.decode("utf-8", "replace") not in _FONT_OBFUSCATION for a in algos)
    except (zipfile.BadZipFile, OSError, KeyError, FormatError):
        return False
    return False


def _strip_html(text: str) -> str:
    text = re.sub(r"<(br|p|div|li)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def epub_info(path: str | os.PathLike) -> list[tuple[str, str]]:
    """``[(field, value)]`` from the EPUB package metadata (field = i18n suffix: title, author…)."""
    rows: list[tuple[str, str]] = []
    with _zip(path) as zf:
        _opf_path, opf = _epub_opf(zf)
        meta = opf.find(f"{{{_OPF}}}metadata")
        if meta is None:
            meta = opf
        version = opf.get("version", "")

        def texts(tag: str) -> list[str]:
            return [(el.text or "").strip() for el in meta.iter(f"{{{_DC}}}{tag}") if (el.text or "").strip()]

        for key, tag in (("title", "title"), ("author", "creator"), ("publisher", "publisher"),
                         ("language", "language"), ("date", "date"), ("identifier", "identifier"),
                         ("subject", "subject")):
            values = texts(tag)
            if values:
                rows.append((key, ", ".join(values) if key in ("author", "subject") else values[0]))
        for el in meta.iter(f"{{{_OPF}}}meta"):
            name = el.get("name") or el.get("property") or ""
            value = el.get("content") or (el.text or "").strip()
            if name in ("calibre:series", "belongs-to-collection") and value:
                rows.append(("series", value))
                break
        desc = texts("description")
        if desc:
            rows.append(("description", _strip_html(desc[0])[:600]))
        spine = opf.find(f"{{{_OPF}}}spine")
        if spine is not None:
            rows.append(("chapters", str(len(list(spine)))))
        if version:
            rows.append(("format", f"EPUB {version}"))
    return rows


def epub_cover(path: str | os.PathLike) -> tuple[bytes, str] | None:
    """``(image bytes, ext)`` of the EPUB cover (EPUB 3 cover-image, EPUB 2 meta, then heuristics)."""
    with _zip(path) as zf:
        opf_path, opf = _epub_opf(zf)
        base = os.path.dirname(opf_path)
        manifest = opf.find(f"{{{_OPF}}}manifest")
        items = list(manifest) if manifest is not None else []
        by_id = {it.get("id", ""): it for it in items}
        candidates: list[ET.Element] = [it for it in items if "cover-image" in (it.get("properties") or "").split()]
        for el in opf.iter(f"{{{_OPF}}}meta"):
            if el.get("name") == "cover" and el.get("content") in by_id:
                candidates.append(by_id[el.get("content", "")])
        images = [it for it in items if (it.get("media-type") or "").startswith("image/")]
        candidates += [it for it in images if "cover" in (it.get("id", "") + it.get("href", "")).lower()]
        candidates += images[:1]
        for item in candidates:
            mt = item.get("media-type") or ""
            if not mt.startswith("image/"):
                continue
            href = item.get("href", "")
            from urllib.parse import unquote

            full = _resolve(base, unquote(href)) if base else unquote(href)
            try:
                data = _read_member(zf, full)
            except KeyError:
                continue
            ext = _IMG_MIME_EXT.get(mt, href.rsplit(".", 1)[-1].lower() if "." in href else "img")
            return data, ext
    return None


def _fb2_root(data: bytes) -> ET.Element:
    return safe_xml_fromstring(data)


def _fb2_bytes(path: str | os.PathLike) -> bytes:
    p = Path(path)
    data = p.read_bytes()
    if data.startswith(b"PK"):
        with _zip(p) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".fb2"):
                    return _read_member(zf, name)
        raise FormatError("err.damaged", details="no .fb2 inside")
    return data


def fb2_info(path: str | os.PathLike) -> list[tuple[str, str]]:
    """``[(field, value)]`` from a FictionBook ``title-info`` / ``publish-info``."""
    root = _fb2_root(_fb2_bytes(path))
    rows: list[tuple[str, str]] = []
    ti = root.find(f".//{{{_FB2}}}title-info")
    if ti is not None:
        title = ti.findtext(f"{{{_FB2}}}book-title")
        if title:
            rows.append(("title", title.strip()))
        authors = []
        for a in ti.findall(f"{{{_FB2}}}author"):
            parts = [a.findtext(f"{{{_FB2}}}{k}") or "" for k in ("first-name", "middle-name", "last-name")]
            name = " ".join(p.strip() for p in parts if p.strip()) or (a.findtext(f"{{{_FB2}}}nickname") or "").strip()
            if name:
                authors.append(name)
        if authors:
            rows.append(("author", ", ".join(authors)))
        lang = ti.findtext(f"{{{_FB2}}}lang")
        if lang:
            rows.append(("language", lang.strip()))
        genres = [g.text.strip() for g in ti.findall(f"{{{_FB2}}}genre") if g.text and g.text.strip()]
        if genres:
            rows.append(("subject", ", ".join(genres)))
        seq = ti.find(f"{{{_FB2}}}sequence")
        if seq is not None and seq.get("name"):
            rows.append(("series", seq.get("name", "") + (f" #{seq.get('number')}" if seq.get("number") else "")))
        ann = ti.find(f"{{{_FB2}}}annotation")
        if ann is not None:
            text = " ".join(t.strip() for t in ann.itertext() if t.strip())
            if text:
                rows.append(("description", text[:600]))
    pi = root.find(f".//{{{_FB2}}}publish-info")
    if pi is not None:
        for key, tag in (("publisher", "publisher"), ("date", "year"), ("identifier", "isbn")):
            value = pi.findtext(f"{{{_FB2}}}{tag}")
            if value and value.strip():
                rows.append((key, value.strip()))
    rows.append(("format", "FB2"))
    return rows


def fb2_cover(path: str | os.PathLike) -> tuple[bytes, str] | None:
    """Cover image of a FictionBook (``coverpage`` → ``binary``)."""
    root = _fb2_root(_fb2_bytes(path))
    ref = None
    cp = root.find(f".//{{{_FB2}}}coverpage")
    if cp is not None:
        img = cp.find(f"{{{_FB2}}}image")
        if img is not None:
            ref = (img.get(f"{{{_XLINK}}}href") or img.get("href") or "").lstrip("#")
    binaries = root.findall(f"{{{_FB2}}}binary")
    chosen = None
    for b in binaries:
        if ref and b.get("id") == ref:
            chosen = b
            break
    if chosen is None:
        chosen = next((b for b in binaries if "cover" in (b.get("id") or "").lower()), None)
    if chosen is None or not (chosen.text or "").strip():
        return None
    try:
        data = base64.b64decode(re.sub(r"\s+", "", chosen.text or ""))
    except (ValueError, TypeError):
        return None
    ext = _IMG_MIME_EXT.get(chosen.get("content-type") or "", "jpg")
    return data, ext


def _comic_images(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = [i for i in zf.infolist() if not i.is_dir() and not os.path.basename(i.filename).startswith(".")
             and not i.filename.startswith("__MACOSX/")
             and i.filename.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "jxl")]
    infos.sort(key=lambda i: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", i.filename)])
    return infos


def cbz_info(path: str | os.PathLike) -> list[tuple[str, str]]:
    """``[(field, value)]`` from ComicInfo.xml plus the page count."""
    rows: list[tuple[str, str]] = []
    with _zip(path) as zf:
        pages = len(_comic_images(zf))
        info_name = next((n for n in zf.namelist() if n.lower().endswith("comicinfo.xml")), None)
        if info_name:
            try:
                root = safe_xml_fromstring(_read_member(zf, info_name))
            except FormatError:
                root = None
            if root is not None:
                for key, tag in (("title", "Title"), ("series", "Series"), ("author", "Writer"),
                                 ("publisher", "Publisher"), ("date", "Year"), ("language", "LanguageISO"),
                                 ("description", "Summary")):
                    value = (root.findtext(tag) or "").strip()
                    if value:
                        if key == "series" and (root.findtext("Number") or "").strip():
                            value += f" #{root.findtext('Number').strip()}"
                        rows.append((key, value[:600]))
    rows.append(("pages", str(pages)))
    rows.append(("format", "CBZ"))
    return rows


def cbz_cover(path: str | os.PathLike) -> tuple[bytes, str] | None:
    """First page (natural order) of a CBZ."""
    with _zip(path) as zf:
        images = _comic_images(zf)
        if not images:
            return None
        info = images[0]
        ext = info.filename.rsplit(".", 1)[-1].lower()
        return _read_member(zf, info), "jpg" if ext == "jpeg" else ext


_EBOOK_META_KEYS = {"title": "title", "author(s)": "author", "publisher": "publisher", "languages": "language",
                    "language": "language", "published": "date", "identifiers": "identifier", "tags": "subject",
                    "series": "series", "comments": "description", "title sort": "", "author sort": "",
                    "book producer": "", "timestamp": "", "rights": ""}


def parse_ebook_meta(text: str) -> list[tuple[str, str]]:
    """``[(field, value)]`` from the output of calibre's ``ebook-meta``."""
    rows: list[tuple[str, str]] = []
    current: list[Any] | None = None
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z ()]*?)\s*:\s(.*)$", line)
        if m and m.group(1).strip().lower() in _EBOOK_META_KEYS:
            key = _EBOOK_META_KEYS[m.group(1).strip().lower()]
            current = [key, m.group(2).strip()] if key else None
            if current is not None:
                rows.append(current)  # type: ignore[arg-type]
        elif current is not None and line.strip():
            current[1] = (current[1] + " " + line.strip()).strip()
    out = []
    for key, value in rows:
        if key == "author":
            value = re.sub(r"\s*\[[^\]]*\]\s*$", "", value)
        if key == "description":
            value = _strip_html(value)[:600]
        if value:
            out.append((key, value))
    return out


# ================================================================================== subtitles

SUBTITLE_FORMATS = {"srt": "srt", "vtt": "vtt", "ass": "ass", "ssa": "ass", "sbv": "sbv"}


@dataclass
class Cue:
    """One subtitle: times in seconds, ``text`` with SRT-style markup (``<i> <b> <u> <font>``)."""

    start: float
    end: float
    text: str
    style: str = ""
    name: str = ""
    ident: str = ""


_TS_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.:](\d{1,3}))?\s*$")
_TIMING_RE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)(.*)$")
_SBV_RE = re.compile(r"^\s*(\d+:\d{1,2}:\d{1,2}\.\d{1,3}),(\d+:\d{1,2}:\d{1,2}\.\d{1,3})\s*$")
_ASS_FIELDS = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]


def parse_timestamp(value: str) -> float | None:
    """Seconds from ``HH:MM:SS,mmm`` / ``MM:SS.mmm`` / ``H:MM:SS.cc`` (``None`` when invalid)."""
    m = _TS_RE.match(value or "")
    if not m:
        return None
    h, mi, s, frac = m.groups()
    total = int(h or 0) * 3600 + int(mi) * 60 + int(s)
    if frac:
        total += float("0." + frac)
    return float(total)


def format_timestamp(seconds: float, fmt: str) -> str:
    """Timestamp for ``srt`` (``00:00:01,500``), ``vtt``/``sbv`` (``00:00:01.500``) or ``ass`` (``0:00:01.50``)."""
    seconds = max(0.0, seconds)
    if fmt == "ass":
        cs = int(round(seconds * 100))
        h, rem = divmod(cs, 360000)
        m, rem = divmod(rem, 6000)
        s, c = divmod(rem, 100)
        return f"{h}:{m:02d}:{s:02d}.{c:02d}"
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    if fmt == "srt":
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
    if fmt == "sbv":
        return f"{h}:{m:02d}:{s:02d}.{milli:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}.{milli:03d}"


def subtitle_format(ext: str, text: str = "") -> str | None:
    """``srt``/``vtt``/``ass``/``sbv`` from the extension, else sniffed from the content."""
    fmt = SUBTITLE_FORMATS.get((ext or "").lower())
    if fmt:
        return fmt
    head = text.lstrip("\ufeff")[:2000]
    if head.startswith("WEBVTT"):
        return "vtt"
    if "[Script Info]" in head or "[Events]" in head:
        return "ass"
    if "-->" in head:
        return "srt"
    if any(_SBV_RE.match(ln) for ln in head.splitlines()[:5]):
        return "sbv"
    return None


def _norm_lines(text: str) -> list[str]:
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _parse_srt(text: str) -> list[Cue]:
    lines = _norm_lines(text)
    cues: list[Cue] = []
    i = 0
    while i < len(lines):
        m = _TIMING_RE.match(lines[i])
        start = parse_timestamp(m.group(1)) if m else None
        end = parse_timestamp(m.group(2)) if m else None
        if start is None or end is None:
            i += 1
            continue
        i += 1
        buf: list[str] = []
        while i < len(lines) and lines[i].strip():
            if _TIMING_RE.match(lines[i]) and parse_timestamp(_TIMING_RE.match(lines[i]).group(1)) is not None:
                break
            buf.append(lines[i].rstrip())
            i += 1
        if buf and buf[-1].strip().isdigit() and i < len(lines) and _TIMING_RE.match(lines[i]):
            buf.pop()
        cues.append(Cue(start, end, "\n".join(buf)))
    return cues


_VTT_KEEP_RE = re.compile(r"<(/?)(i|b|u)(?:\.[^>\s]*)?>")


def _vtt_to_internal(text: str) -> str:
    text = re.sub(r"<rt>.*?</rt>", "", text, flags=re.S)
    text = _VTT_KEEP_RE.sub(lambda m: f"<{m.group(1)}{m.group(2)}>", text)
    text = re.sub(r"<(?!/?(?:i|b|u)>)[^>]*>", "", text)
    return html.unescape(text.replace("&lrm;", "").replace("&rlm;", ""))


def _parse_vtt(text: str) -> list[Cue]:
    blocks = re.split(r"\n[ \t]*\n", "\n".join(_norm_lines(text)))
    cues: list[Cue] = []
    for n, block in enumerate(blocks):
        lines = [ln for ln in block.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            continue
        if n == 0 and lines[0].startswith("WEBVTT"):
            continue
        if "-->" not in lines[0] and lines[0].split(" ", 1)[0] in ("NOTE", "STYLE", "REGION"):
            continue
        idx = 0
        ident = ""
        if "-->" not in lines[0]:
            ident = lines[0].strip()
            idx = 1
        if idx >= len(lines):
            continue
        m = _TIMING_RE.match(lines[idx])
        if not m:
            continue
        start, end = parse_timestamp(m.group(1)), parse_timestamp(m.group(2))
        if start is None or end is None:
            continue
        cues.append(Cue(start, end, _vtt_to_internal("\n".join(lines[idx + 1:])), ident=ident))
    return cues


def _ass_to_internal(raw: str) -> str:
    def block(m: re.Match) -> str:
        out = []
        for name, value in re.findall(r"\\(i|b|u)(\d+)", m.group(1)):
            out.append(f"<{name}>" if value != "0" else f"</{name}>")
        return "".join(out)

    text = re.sub(r"\{([^}]*)\}", block, raw)
    return text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", "\u00a0")


def _ass_events(lines: Sequence[str]) -> Iterable[tuple[int, str, list[str], list[str]]]:
    """Yield ``(line index, kind, fields, values)`` for Dialogue/Comment lines of [Events]."""
    section = ""
    fields = list(_ASS_FIELDS)
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.lower()
            continue
        if section != "[events]":
            continue
        low = s.lower()
        if low.startswith("format:"):
            fields = [f.strip().lower() for f in s[7:].split(",")]
            continue
        for kind in ("dialogue", "comment"):
            if low.startswith(kind + ":"):
                values = s[len(kind) + 1:].lstrip().split(",", len(fields) - 1)
                yield i, kind, fields, values
                break


def _parse_ass(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for _i, kind, fields, values in _ass_events(_norm_lines(text)):
        if kind != "dialogue":
            continue
        rec = dict(zip(fields, values))
        start, end = parse_timestamp(rec.get("start", "")), parse_timestamp(rec.get("end", ""))
        if start is None or end is None:
            continue
        raw = rec.get("text", "")
        if re.search(r"\{[^}]*\\p[1-9]", raw):
            continue  # vector drawing, not text
        cues.append(Cue(start, end, _ass_to_internal(raw), style=rec.get("style", "").strip(),
                        name=rec.get("name", "").strip()))
    return cues


def _parse_sbv(text: str) -> list[Cue]:
    lines = _norm_lines(text)
    cues: list[Cue] = []
    i = 0
    while i < len(lines):
        m = _SBV_RE.match(lines[i])
        if not m:
            i += 1
            continue
        start, end = parse_timestamp(m.group(1)), parse_timestamp(m.group(2))
        i += 1
        buf = []
        while i < len(lines) and lines[i].strip():
            buf.append(lines[i].rstrip())
            i += 1
        if start is not None and end is not None:
            cues.append(Cue(start, end, "\n".join(buf)))
    return cues


def parse_subtitles(text: str, fmt: str) -> list[Cue]:
    """Cues of an SRT/VTT/ASS/SBV text; :class:`FormatError` when nothing could be read."""
    parser = {"srt": _parse_srt, "vtt": _parse_vtt, "ass": _parse_ass, "sbv": _parse_sbv}.get(fmt)
    if parser is None:
        raise FormatError("err.not_supported")
    cues = parser(text)
    if not cues:
        raise FormatError("docs.err.no_cues")
    return cues


_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _plain(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).replace("\u00a0", " ")


def _srt_text(text: str) -> str:
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln if ln.strip() else " " for ln in lines]
    while lines and lines[-1] == " ":
        lines.pop()
    return "\n".join(lines)


def _vtt_text(text: str) -> str:
    parts = re.split(r"(</?(?:i|b|u)>)", text)
    out = []
    for part in parts:
        if re.fullmatch(r"</?(?:i|b|u)>", part or ""):
            out.append(part)
        else:
            out.append(html.escape(html.unescape(_TAG_RE.sub("", part)), quote=False))
    result = "".join(out)
    lines = [ln.rstrip() for ln in result.split("\n")]
    return "\n".join(ln if ln.strip() else " " for ln in lines).rstrip()


def _ass_text(text: str) -> str:
    text = text.replace("{", "(").replace("}", ")")
    text = re.sub(r"<(/?)(i|b|u)>", lambda m: "{\\%s%s}" % (m.group(2), "0" if m.group(1) else "1"), text)
    text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
    return html.unescape(text).replace("\r", "").replace("\n", "\\N")


ASS_HEADER = """[Script Info]
; Script generated by Dropwheel
Title: {title}
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_subtitles(cues: Sequence[Cue], fmt: str, *, title: str = "") -> str:
    """Serialise cues as ``srt``/``vtt``/``ass``/``sbv``/``txt`` text."""
    ordered = sorted(cues, key=lambda c: (c.start, c.end))
    if fmt == "srt":
        blocks = []
        for n, c in enumerate(ordered, 1):
            blocks.append(f"{n}\n{format_timestamp(c.start, 'srt')} --> {format_timestamp(c.end, 'srt')}\n"
                          f"{_srt_text(c.text)}")
        return "\n\n".join(blocks) + "\n"
    if fmt == "vtt":
        blocks = ["WEBVTT"]
        for c in ordered:
            ident = f"{c.ident}\n" if c.ident and "-->" not in c.ident else ""
            blocks.append(f"{ident}{format_timestamp(c.start, 'vtt')} --> {format_timestamp(c.end, 'vtt')}\n"
                          f"{_vtt_text(c.text)}")
        return "\n\n".join(blocks) + "\n"
    if fmt == "ass":
        safe_title = re.sub(r"[\r\n]+", " ", title or "Dropwheel")
        lines = [ASS_HEADER.format(title=safe_title).rstrip("\n")]
        for c in ordered:
            style = c.style or "Default"
            name = c.name.replace(",", " ")
            lines.append(f"Dialogue: 0,{format_timestamp(c.start, 'ass')},{format_timestamp(c.end, 'ass')},"
                         f"{style},{name},0,0,0,,{_ass_text(c.text)}")
        return "\n".join(lines) + "\n"
    if fmt == "sbv":
        blocks = [f"{format_timestamp(c.start, 'sbv')},{format_timestamp(c.end, 'sbv')}\n{_srt_text(_plain(c.text))}"
                  for c in ordered]
        return "\n\n".join(blocks) + "\n"
    if fmt == "txt":
        return cues_to_text(ordered)
    raise FormatError("err.not_supported")


def cues_to_text(cues: Sequence[Cue]) -> str:
    """Plain transcript: markup removed, one cue per line, consecutive duplicates dropped."""
    out: list[str] = []
    for c in sorted(cues, key=lambda c: (c.start, c.end)):
        text = " ".join(part.strip() for part in _plain(c.text).split("\n") if part.strip())
        text = re.sub(r"\s{2,}", " ", text).strip()
        if text and (not out or out[-1] != text):
            out.append(text)
    return "\n".join(out) + ("\n" if out else "")


def _map_cue(start: float, end: float, fn: Callable[[float], float]) -> tuple[float, float] | None:
    s, e = fn(start), fn(end)
    if e <= 0:
        return None
    return max(0.0, s), e


def remap_subtitle_times(text: str, fmt: str, fn: Callable[[float], float]) -> tuple[str, int, int]:
    """Apply ``fn`` to every timestamp keeping everything else (styles, VTT settings, ASS headers).

    Cues ending at or before 0 are dropped; returns ``(new_text, kept, dropped)``.
    """
    if fmt in ("srt", "sbv"):
        cues = parse_subtitles(text, fmt)
        kept: list[Cue] = []
        for c in cues:
            mapped = _map_cue(c.start, c.end, fn)
            if mapped is not None:
                kept.append(Cue(mapped[0], mapped[1], c.text, c.style, c.name, c.ident))
        if fmt == "srt":
            out = []
            for n, c in enumerate(kept, 1):
                out.append(f"{n}\n{format_timestamp(c.start, 'srt')} --> {format_timestamp(c.end, 'srt')}\n"
                           f"{_srt_text(c.text)}")
            return "\n\n".join(out) + "\n", len(kept), len(cues) - len(kept)
        return write_subtitles(kept, "sbv"), len(kept), len(cues) - len(kept)
    if fmt == "vtt":
        blocks = re.split(r"\n[ \t]*\n", "\n".join(_norm_lines(text)))
        out_blocks: list[str] = []
        kept_n = dropped = 0
        for n, block in enumerate(blocks):
            lines = block.split("\n")
            timing_idx = next((i for i, ln in enumerate(lines[:2]) if "-->" in ln), None)
            if n == 0 and block.lstrip().startswith("WEBVTT") or timing_idx is None:
                if block.strip():
                    out_blocks.append(block)
                continue
            m = _TIMING_RE.match(lines[timing_idx])
            start = parse_timestamp(m.group(1)) if m else None
            end = parse_timestamp(m.group(2)) if m else None
            if m is None or start is None or end is None:
                out_blocks.append(block)
                continue
            mapped = _map_cue(start, end, fn)
            if mapped is None:
                dropped += 1
                continue
            lines[timing_idx] = (f"{format_timestamp(mapped[0], 'vtt')} --> {format_timestamp(mapped[1], 'vtt')}"
                                 f"{m.group(3)}")
            out_blocks.append("\n".join(lines))
            kept_n += 1
        if not kept_n and not dropped:
            raise FormatError("docs.err.no_cues")
        return "\n\n".join(out_blocks).rstrip("\n") + "\n", kept_n, dropped
    if fmt == "ass":
        lines = _norm_lines(text)
        drop: set[int] = set()
        kept_n = dropped = 0
        for i, kind, fields, values in list(_ass_events(lines)):
            try:
                si, ei = fields.index("start"), fields.index("end")
            except ValueError:
                continue
            if si >= len(values) or ei >= len(values):
                continue
            start, end = parse_timestamp(values[si]), parse_timestamp(values[ei])
            if start is None or end is None:
                continue
            mapped = _map_cue(start, end, fn)
            if mapped is None:
                drop.add(i)
                if kind == "dialogue":
                    dropped += 1
                continue
            values = list(values)
            values[si] = format_timestamp(mapped[0], "ass")
            values[ei] = format_timestamp(mapped[1], "ass")
            prefix = "Dialogue" if kind == "dialogue" else "Comment"
            lines[i] = f"{prefix}: " + ",".join(values)
            if kind == "dialogue":
                kept_n += 1
        if not kept_n and not dropped:
            raise FormatError("docs.err.no_cues")
        return "\n".join(ln for i, ln in enumerate(lines) if i not in drop).rstrip("\n") + "\n", kept_n, dropped
    raise FormatError("err.not_supported")


_HI_SPEAKER_RE = re.compile(r"^(-\s*)?[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ0-9 .'’\-]{1,30}:\s+")


def clean_hearing_impaired(cues: Sequence[Cue]) -> tuple[list[Cue], int]:
    """Remove ``[sounds]``, ``(sounds)``, music lines and ``SPEAKER:`` labels; returns ``(cues, changed)``."""
    out: list[Cue] = []
    changed = 0
    for c in cues:
        text = re.sub(r"\[[^\]\n]*\]", "", c.text)
        text = re.sub(r"\([^)\n]*\)", "", text)
        lines = []
        for ln in text.split("\n"):
            s = ln.strip()
            s = _HI_SPEAKER_RE.sub(lambda m: m.group(1) or "", s)
            if re.fullmatch(r"(?:</?[a-z]+>|[-\s♪♫#*])*", s):
                continue
            bare = _plain(s).strip(" -")
            if bare[:1] in ("♪", "♫", "#") or bare[-1:] in ("♪", "♫"):
                continue  # sung lyrics / music cues
            lines.append(s)
        new_text = "\n".join(lines)
        if new_text != c.text:
            changed += 1
        if _plain(new_text).strip(" -\n"):
            out.append(Cue(c.start, c.end, new_text, c.style, c.name, c.ident))
    return out, changed
