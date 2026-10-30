"""File classification.

:func:`classify` turns a path, ``file://`` URI or http(s) URL into a :class:`FileInfo`.  The
category comes from the **content** first (magic bytes: images incl. animation checks, PDF, the
ZIP family split into OOXML/ODF/EPUB/…, archives, ELF/AppImage, ISO, fonts, audio/video
containers, shebang, UTF-8 text) and only then from the extension.  It never raises: anything
unresolved is ``"unknown"``.

Cheap extras are filled when ``deep=True``: image width/height (header only, EXIF-rotated when
Pillow can read it), PDF page count (PyMuPDF, then pypdf), office page/slide counts.  ``duration``
is left ``None`` (previews fill it when needed).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import mimetypes
import os
import re
import stat
import struct
import urllib.parse
import warnings
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from . import util

log = logging.getLogger("dropwheel.detect")

__all__ = ["FileInfo", "CATEGORIES", "CATEGORY_ICONS", "classify", "classify_many", "common_category",
           "ext_category", "mime_for_ext"]

CATEGORIES: list[str] = [
    "image", "svg", "pdf", "document", "spreadsheet", "presentation", "markdown", "text", "code", "data",
    "ebook", "subtitle", "audio", "video", "archive", "font", "folder", "url", "appimage", "disk",
    "package", "unknown",
]

#: Material Symbols Rounded icon per category.
CATEGORY_ICONS: dict[str, str] = {
    "image": "image", "svg": "image", "pdf": "picture_as_pdf", "document": "description",
    "spreadsheet": "table_chart", "presentation": "slideshow", "markdown": "article", "text": "article",
    "code": "code", "data": "data_object", "ebook": "menu_book", "subtitle": "subtitles",
    "audio": "music_note", "video": "movie", "archive": "folder_zip", "font": "font_download",
    "folder": "folder", "url": "link", "appimage": "deployed_code", "disk": "album",
    "package": "inventory_2", "unknown": "draft",
}


@dataclass
class FileInfo:
    """Everything the menu builder and the actions need to know about one dropped item."""

    path: str = ""                 #: absolute local path ("" for URLs)
    uri: str = ""                  #: percent-encoded file:// URI, or the http(s) URL
    name: str = ""
    stem: str = ""
    ext: str = ""                  #: lower case, no dot, compound kept ("tar.gz")
    category: str = "unknown"
    mime: str = "application/octet-stream"
    size: int = 0                  #: bytes (0 for folders)
    is_dir: bool = False
    exists: bool = False
    animated: bool = False         #: GIF/WebP/APNG/AVIS with more than one frame
    writable_dir: bool = False     #: parent directory writable
    volatile: bool = False         #: lives in /tmp, $XDG_RUNTIME_DIR, caches… (inputs are staged)
    width: int | None = None
    height: int | None = None
    pages: int | None = None
    duration: float | None = None
    icon: str = "draft"
    format: str = ""               #: format detected from the content ("png", "docx", "zip"…), "" if unknown
    is_link: bool = False          #: the path itself is a symlink
    readable: bool = False
    mtime: float | None = None

    @property
    def p(self) -> Path:
        """The path as :class:`pathlib.Path`."""
        return Path(self.path)

    def to_json(self) -> dict:
        """JSON-serialisable dict of every field."""
        return dataclasses.asdict(self)


# ============================================================================ extension tables

def _tbl(category: str, entries: dict[str, str]) -> dict[str, tuple[str, str]]:
    return {ext: (category, mime) for ext, mime in entries.items()}


_RAW_EXTS = ("cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "dng", "raf", "orf", "rw2", "pef", "srw",
             "x3f", "3fr", "iiq", "rwl", "erf", "kdc", "mrw", "mos", "dcr")

EXT_TABLE: dict[str, tuple[str, str]] = {}
EXT_TABLE.update(_tbl("image", {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "jpe": "image/jpeg", "jfif": "image/jpeg", "pjpeg": "image/jpeg",
    "png": "image/png", "apng": "image/apng", "gif": "image/gif", "webp": "image/webp", "avif": "image/avif",
    "heic": "image/heic", "heif": "image/heif", "hif": "image/heif", "jxl": "image/jxl", "tif": "image/tiff",
    "tiff": "image/tiff", "bmp": "image/bmp", "dib": "image/bmp", "ico": "image/vnd.microsoft.icon",
    "cur": "image/x-win-bitmap", "tga": "image/x-tga", "psd": "image/vnd.adobe.photoshop", "xcf": "image/x-xcf",
    "qoi": "image/qoi", "ppm": "image/x-portable-pixmap", "pgm": "image/x-portable-graymap",
    "pbm": "image/x-portable-bitmap", "pnm": "image/x-portable-anymap", "exr": "image/x-exr",
    "hdr": "image/vnd.radiance", "dds": "image/vnd-ms.dds", "jp2": "image/jp2", "j2k": "image/jp2",
    "jpf": "image/jpx", "eps": "image/x-eps", "pcx": "image/vnd.zbrush.pcx", "xbm": "image/x-xbitmap",
    "xpm": "image/x-xpixmap", "icns": "image/x-icns", "wbmp": "image/vnd.wap.wbmp",
}))
EXT_TABLE.update({ext: ("image", f"image/x-{ext}") for ext in _RAW_EXTS})
EXT_TABLE["dng"] = ("image", "image/x-adobe-dng")
EXT_TABLE.update(_tbl("svg", {"svg": "image/svg+xml", "svgz": "image/svg+xml-compressed"}))
EXT_TABLE.update(_tbl("pdf", {"pdf": "application/pdf"}))
EXT_TABLE.update(_tbl("document", {
    "doc": "application/msword", "dot": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "docm": "application/vnd.ms-word.document.macroenabled.12",
    "dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    "dotm": "application/vnd.ms-word.template.macroenabled.12",
    "odt": "application/vnd.oasis.opendocument.text", "ott": "application/vnd.oasis.opendocument.text-template",
    "fodt": "application/vnd.oasis.opendocument.text-flat-xml", "odg": "application/vnd.oasis.opendocument.graphics",
    "fodg": "application/vnd.oasis.opendocument.graphics-flat-xml",
    "rtf": "application/rtf", "wpd": "application/vnd.wordperfect", "pages": "application/vnd.apple.pages",
    "abw": "application/x-abiword", "wps": "application/vnd.ms-works", "ps": "application/postscript",
    "xps": "application/oxps", "oxps": "application/oxps", "lwp": "application/vnd.lotus-wordpro",
}))
EXT_TABLE.update(_tbl("spreadsheet", {
    "xls": "application/vnd.ms-excel", "xlt": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    "xlsb": "application/vnd.ms-excel.sheet.binary.macroenabled.12",
    "xltx": "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "ots": "application/vnd.oasis.opendocument.spreadsheet-template",
    "fods": "application/vnd.oasis.opendocument.spreadsheet-flat-xml",
    "csv": "text/csv", "tsv": "text/tab-separated-values", "tab": "text/tab-separated-values",
    "numbers": "application/vnd.apple.numbers",
}))
EXT_TABLE.update(_tbl("presentation", {
    "ppt": "application/vnd.ms-powerpoint", "pps": "application/vnd.ms-powerpoint",
    "pot": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pptm": "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    "potx": "application/vnd.openxmlformats-officedocument.presentationml.template",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "otp": "application/vnd.oasis.opendocument.presentation-template",
    "fodp": "application/vnd.oasis.opendocument.presentation-flat-xml",
    "key": "application/vnd.apple.keynote",
}))
EXT_TABLE.update(_tbl("markdown", {
    "md": "text/markdown", "markdown": "text/markdown", "mdown": "text/markdown", "mkd": "text/markdown",
    "mkdn": "text/markdown", "mdwn": "text/markdown", "mdx": "text/markdown", "rmd": "text/markdown",
    "qmd": "text/markdown",
}))
EXT_TABLE.update(_tbl("text", {
    "txt": "text/plain", "text": "text/plain", "log": "text/x-log", "nfo": "text/x-nfo", "diz": "text/plain",
    "me": "text/plain", "1st": "text/plain", "asc": "text/plain", "rst": "text/x-rst", "org": "text/x-org",
    "adoc": "text/asciidoc", "asciidoc": "text/asciidoc", "textile": "text/x-textile", "wiki": "text/x-wiki",
    "lst": "text/plain", "pem": "application/x-pem-file", "crt": "application/x-x509-ca-cert",
    "lrc": "text/x-lrc", "srt~": "text/plain",
}))
EXT_TABLE.update(_tbl("code", {
    "py": "text/x-python", "pyw": "text/x-python", "pyi": "text/x-python", "js": "text/javascript",
    "mjs": "text/javascript", "cjs": "text/javascript", "jsx": "text/jsx", "ts": "text/x-typescript",
    "tsx": "text/x-typescript", "mts": "text/x-typescript", "cts": "text/x-typescript",
    "c": "text/x-csrc", "h": "text/x-chdr", "cpp": "text/x-c++src", "cc": "text/x-c++src",
    "cxx": "text/x-c++src", "c++": "text/x-c++src", "hpp": "text/x-c++hdr", "hh": "text/x-c++hdr",
    "hxx": "text/x-c++hdr", "cs": "text/x-csharp", "java": "text/x-java", "kt": "text/x-kotlin",
    "kts": "text/x-kotlin", "go": "text/x-go", "rs": "text/rust", "rb": "text/x-ruby", "php": "application/x-php",
    "pl": "text/x-perl", "pm": "text/x-perl", "lua": "text/x-lua", "sh": "application/x-shellscript",
    "bash": "application/x-shellscript", "zsh": "application/x-shellscript", "fish": "application/x-shellscript",
    "ksh": "application/x-shellscript", "ps1": "text/x-powershell", "psm1": "text/x-powershell",
    "bat": "application/x-bat", "cmd": "application/x-bat", "swift": "text/x-swift", "m": "text/x-objcsrc",
    "mm": "text/x-objc++src", "scala": "text/x-scala", "sc": "text/x-scala", "clj": "text/x-clojure",
    "cljs": "text/x-clojure", "hs": "text/x-haskell", "ml": "text/x-ocaml", "mli": "text/x-ocaml",
    "fs": "text/x-fsharp", "fsx": "text/x-fsharp", "ex": "text/x-elixir", "exs": "text/x-elixir",
    "erl": "text/x-erlang", "hrl": "text/x-erlang", "dart": "text/x-dart", "r": "text/x-r", "jl": "text/x-julia",
    "nim": "text/x-nim", "zig": "text/x-zig", "vhd": "text/x-vhdl", "vhdl": "text/x-vhdl", "sv": "text/x-verilog",
    "asm": "text/x-asm", "s": "text/x-asm", "sql": "application/sql", "css": "text/css", "scss": "text/x-scss",
    "sass": "text/x-sass", "less": "text/x-less", "html": "text/html", "htm": "text/html",
    "xhtml": "application/xhtml+xml", "vue": "text/x-vue", "svelte": "text/x-svelte", "astro": "text/x-astro",
    "qml": "text/x-qml", "cmake": "text/x-cmake", "mk": "text/x-makefile", "mak": "text/x-makefile",
    "dockerfile": "text/x-dockerfile", "gradle": "text/x-groovy", "groovy": "text/x-groovy", "tf": "text/x-hcl",
    "hcl": "text/x-hcl", "nix": "text/x-nix", "el": "text/x-emacs-lisp", "lisp": "text/x-common-lisp",
    "scm": "text/x-scheme", "rkt": "text/x-racket", "vim": "text/x-vim", "ipynb": "application/x-ipynb+json",
    "tex": "text/x-tex", "latex": "text/x-tex", "sty": "text/x-tex", "cls": "text/x-tex", "bib": "text/x-bibtex",
    "diff": "text/x-diff", "patch": "text/x-patch", "proto": "text/x-protobuf", "graphql": "application/graphql",
    "gql": "application/graphql", "glsl": "text/x-glsl", "frag": "text/x-glsl", "vert": "text/x-glsl",
    "hlsl": "text/x-hlsl", "wgsl": "text/x-wgsl", "cu": "text/x-cuda", "f90": "text/x-fortran",
    "f": "text/x-fortran", "for": "text/x-fortran", "pas": "text/x-pascal", "d": "text/x-d", "cr": "text/x-crystal",
    "coffee": "text/x-coffeescript", "elm": "text/x-elm", "sol": "text/x-solidity", "awk": "text/x-awk",
    "sed": "text/x-sed", "tcl": "text/x-tcl", "v": "text/x-v", "gd": "text/x-gdscript", "lean": "text/x-lean",
    "kdl": "text/x-kdl", "just": "text/x-just", "meson": "text/x-meson",
}))
EXT_TABLE.update(_tbl("data", {
    "json": "application/json", "jsonc": "application/json", "json5": "application/json5",
    "ndjson": "application/x-ndjson", "jsonl": "application/jsonl", "geojson": "application/geo+json",
    "yaml": "application/yaml", "yml": "application/yaml", "toml": "application/toml", "xml": "application/xml",
    "plist": "application/x-plist", "ini": "text/x-ini", "cfg": "text/x-ini", "conf": "text/x-conf",
    "env": "text/x-dotenv", "properties": "text/x-java-properties", "sqlite": "application/vnd.sqlite3",
    "sqlite3": "application/vnd.sqlite3", "db": "application/vnd.sqlite3", "parquet": "application/vnd.apache.parquet",
    "gpx": "application/gpx+xml", "kml": "application/vnd.google-earth.kml+xml", "rss": "application/rss+xml",
    "atom": "application/atom+xml", "opml": "text/x-opml", "xsd": "application/xml", "xsl": "application/xslt+xml",
    "xslt": "application/xslt+xml", "desktop": "application/x-desktop", "service": "text/x-systemd-unit",
    "reg": "text/x-ms-regedit", "csl": "application/vnd.citationstyles.style+xml", "har": "application/json",
    "lock": "text/plain",
}))
EXT_TABLE.update(_tbl("ebook", {
    "epub": "application/epub+zip", "kepub": "application/epub+zip", "mobi": "application/x-mobipocket-ebook",
    "azw": "application/vnd.amazon.ebook", "azw3": "application/vnd.amazon.mobi8-ebook",
    "kfx": "application/vnd.amazon.ebook", "fb2": "application/x-fictionbook+xml", "fbz": "application/x-zip-compressed-fb2",
    "djvu": "image/vnd.djvu", "djv": "image/vnd.djvu", "cbz": "application/vnd.comicbook+zip",
    "cbr": "application/vnd.comicbook-rar", "cb7": "application/x-cb7", "cbt": "application/x-cbt",
    "lit": "application/x-ms-reader", "lrf": "application/x-sony-bbeb",
}))
EXT_TABLE.update(_tbl("subtitle", {
    "srt": "application/x-subrip", "vtt": "text/vtt", "ass": "text/x-ssa", "ssa": "text/x-ssa",
    "sbv": "text/x-sbv", "sub": "text/x-microdvd", "ttml": "application/ttml+xml", "dfxp": "application/ttml+xml",
    "smi": "application/x-sami", "sami": "application/x-sami",
}))
EXT_TABLE.update(_tbl("audio", {
    "mp3": "audio/mpeg", "mp2": "audio/mpeg", "mpga": "audio/mpeg", "m4a": "audio/mp4", "m4b": "audio/mp4",
    "m4r": "audio/mp4", "aac": "audio/aac", "flac": "audio/flac", "ogg": "audio/ogg", "oga": "audio/ogg",
    "opus": "audio/ogg", "spx": "audio/ogg", "wav": "audio/wav", "wave": "audio/wav", "aif": "audio/aiff",
    "aiff": "audio/aiff", "aifc": "audio/aiff", "wma": "audio/x-ms-wma", "ape": "audio/x-ape", "wv": "audio/x-wavpack",
    "mka": "audio/x-matroska", "mid": "audio/midi", "midi": "audio/midi", "amr": "audio/amr", "au": "audio/basic",
    "snd": "audio/basic", "caf": "audio/x-caf", "dsf": "audio/x-dsf", "dff": "audio/x-dff", "ac3": "audio/ac3",
    "eac3": "audio/eac3", "dts": "audio/vnd.dts", "tta": "audio/x-tta", "mpc": "audio/x-musepack",
    "weba": "audio/webm", "alac": "audio/mp4", "m3u": "audio/x-mpegurl", "m3u8": "application/vnd.apple.mpegurl",
    "pls": "audio/x-scpls", "cue": "application/x-cue",
}))
EXT_TABLE.update(_tbl("video", {
    "mp4": "video/mp4", "m4v": "video/x-m4v", "mov": "video/quicktime", "qt": "video/quicktime",
    "mkv": "video/x-matroska", "mk3d": "video/x-matroska", "webm": "video/webm", "avi": "video/x-msvideo",
    "wmv": "video/x-ms-wmv", "asf": "video/x-ms-asf", "flv": "video/x-flv", "f4v": "video/mp4",
    "mpg": "video/mpeg", "mpeg": "video/mpeg", "mpe": "video/mpeg", "m2v": "video/mpeg", "m2ts": "video/mp2t",
    "mts": "video/mp2t", "vob": "video/mpeg", "3gp": "video/3gpp", "3g2": "video/3gpp2", "ogv": "video/ogg",
    "rm": "application/vnd.rn-realmedia", "rmvb": "application/vnd.rn-realmedia-vbr", "divx": "video/x-msvideo",
    "mxf": "application/mxf", "y4m": "video/x-yuv4mpeg", "dv": "video/dv", "h264": "video/h264", "hevc": "video/h265",
}))
EXT_TABLE.update(_tbl("archive", {
    "zip": "application/zip", "zipx": "application/zip", "7z": "application/x-7z-compressed",
    "rar": "application/vnd.rar", "tar": "application/x-tar", "tar.gz": "application/x-compressed-tar",
    "tgz": "application/x-compressed-tar", "tar.xz": "application/x-xz-compressed-tar",
    "txz": "application/x-xz-compressed-tar", "tar.zst": "application/x-zstd-compressed-tar",
    "tzst": "application/x-zstd-compressed-tar", "tar.bz2": "application/x-bzip2-compressed-tar",
    "tbz2": "application/x-bzip2-compressed-tar", "tbz": "application/x-bzip2-compressed-tar",
    "tar.lz4": "application/x-lz4-compressed-tar", "tar.lz": "application/x-lzip-compressed-tar",
    "tar.lzma": "application/x-lzma-compressed-tar", "tlz": "application/x-lzma-compressed-tar",
    "tar.z": "application/x-tarz", "tar.br": "application/x-tar", "gz": "application/gzip",
    "xz": "application/x-xz", "zst": "application/zstd", "bz2": "application/x-bzip2", "lz4": "application/x-lz4",
    "lz": "application/x-lzip", "lzma": "application/x-lzma", "z": "application/x-compress", "br": "application/x-brotli",
    "cab": "application/vnd.ms-cab-compressed", "arj": "application/x-arj", "lzh": "application/x-lha",
    "lha": "application/x-lha", "ace": "application/x-ace", "cpio": "application/x-cpio",
    "jar": "application/java-archive", "war": "application/java-archive", "ear": "application/java-archive",
    "xpi": "application/x-xpinstall", "whl": "application/zip", "7z.001": "application/x-7z-compressed",
    "zip.001": "application/zip", "tar.001": "application/x-tar", "sit": "application/x-stuffit",
    "sitx": "application/x-stuffitx", "xar": "application/x-xar", "crx": "application/x-chrome-extension",
    "nupkg": "application/zip", "vsix": "application/zip", "ipa": "application/zip",
}))
EXT_TABLE.update(_tbl("font", {
    "ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2", "ttc": "font/collection",
    "otc": "font/collection", "pfb": "application/x-font-type1", "pfa": "application/x-font-type1",
    "pcf": "application/x-font-pcf", "bdf": "application/x-font-bdf", "eot": "application/vnd.ms-fontobject",
}))
EXT_TABLE.update(_tbl("appimage", {"appimage": "application/vnd.appimage"}))
EXT_TABLE.update(_tbl("disk", {
    "iso": "application/x-cd-image", "img": "application/x-raw-disk-image", "ima": "application/x-raw-disk-image",
    "nrg": "application/x-nrg", "mdf": "application/x-cd-image", "vhd": "application/x-vhd-disk",
    "vhdx": "application/x-vhdx-disk", "vmdk": "application/x-vmdk-disk", "qcow2": "application/x-qemu-disk",
    "qcow": "application/x-qemu-disk", "vdi": "application/x-virtualbox-vdi", "dmg": "application/x-apple-diskimage",
    "udf": "application/x-cd-image", "wim": "application/x-ms-wim", "squashfs": "application/x-squashfs",
    "sfs": "application/x-squashfs", "raw": "application/x-raw-disk-image",
}))
EXT_TABLE.update(_tbl("package", {
    "pkg.tar.zst": "application/x-alpm-package", "pkg.tar.xz": "application/x-alpm-package",
    "pkg.tar.gz": "application/x-alpm-package", "pkg.tar.bz2": "application/x-alpm-package",
    "deb": "application/vnd.debian.binary-package", "rpm": "application/x-rpm",
    "flatpak": "application/vnd.flatpak", "flatpakref": "application/vnd.flatpak.ref",
    "flatpakrepo": "application/vnd.flatpak.repo", "snap": "application/vnd.snap", "exe": "application/x-msdownload",
    "msi": "application/x-msi", "msix": "application/msix", "appx": "application/appx",
    "apk": "application/vnd.android.package-archive", "aab": "application/x-authorware-bin",
    "xapk": "application/vnd.android.package-archive", "run": "application/x-makeself",
}))

#: Extension-less file names that are source code / data.
NAME_TABLE: dict[str, tuple[str, str]] = {
    "makefile": ("code", "text/x-makefile"), "gnumakefile": ("code", "text/x-makefile"),
    "dockerfile": ("code", "text/x-dockerfile"), "containerfile": ("code", "text/x-dockerfile"),
    "pkgbuild": ("code", "application/x-shellscript"), "vagrantfile": ("code", "text/x-ruby"),
    "jenkinsfile": ("code", "text/x-groovy"), "rakefile": ("code", "text/x-ruby"), "gemfile": ("code", "text/x-ruby"),
    "justfile": ("code", "text/x-just"), "cmakelists.txt": ("code", "text/x-cmake"),
    "meson.build": ("code", "text/x-meson"), ".bashrc": ("code", "application/x-shellscript"),
    ".zshrc": ("code", "application/x-shellscript"), ".profile": ("code", "application/x-shellscript"),
    ".bash_profile": ("code", "application/x-shellscript"), ".gitignore": ("text", "text/plain"),
    ".env": ("data", "text/x-dotenv"), "license": ("text", "text/plain"), "readme": ("text", "text/plain"),
    "copying": ("text", "text/plain"), "changelog": ("text", "text/plain"),
}

#: Extensions whose content is a ZIP container (the extension decides the category).
_ZIP_BASED = {"docx", "docm", "dotx", "dotm", "xlsx", "xlsm", "xltx", "pptx", "pptm", "ppsx", "potx", "odt", "ott",
              "ods", "ots", "odp", "otp", "odg", "epub", "kepub", "cbz", "jar", "war", "ear", "apk", "aab", "xapk",
              "xpi", "whl", "pages", "key", "numbers", "msix", "appx", "nupkg", "vsix", "ipa", "fbz", "zipx", "kmz"}

_TEXTY = {"text", "markdown", "code", "data", "subtitle", "svg"}
_TEXT_EXTS_OTHER = {"csv", "tsv", "tab", "fodt", "fods", "fodp", "fodg", "fb2", "rtf", "ps", "eps", "lrc", "pem",
                    "crt", "m3u", "m3u8", "pls", "cue", "html", "htm", "xhtml", "ttml", "dfxp", "smi", "sami"}


def ext_category(ext: str, name: str = "") -> tuple[str, str] | None:
    """``(category, mime)`` for an extension (or special file name), ``None`` when unknown."""
    if name:
        hit = NAME_TABLE.get(name.lower())
        if hit:
            return hit
    if not ext:
        return None
    return EXT_TABLE.get(ext.lower())


def mime_for_ext(ext: str) -> str:
    """MIME type for an extension (own table, then :mod:`mimetypes`)."""
    hit = EXT_TABLE.get(ext.lower())
    if hit:
        return hit[1]
    guess, _ = mimetypes.guess_type("x." + ext)
    return guess or "application/octet-stream"


# =================================================================================== sniffing

@dataclass
class _Sniff:
    category: str
    mime: str
    fmt: str
    animated: bool = False
    width: int | None = None
    height: int | None = None
    pages: int | None = None


_HEAD = 65536


def _png_info(f: IO[bytes], head: bytes) -> _Sniff:
    width = height = None
    if len(head) >= 24 and head[12:16] == b"IHDR":
        width, height = struct.unpack(">II", head[16:24])
    animated = False
    pos = 8
    try:
        for _ in range(4096):
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            length, ctype = struct.unpack(">I4s", hdr)
            if ctype == b"acTL":
                data = f.read(4)
                if len(data) == 4 and struct.unpack(">I", data)[0] > 1:
                    animated = True
                break
            if ctype in (b"IDAT", b"IEND"):
                break
            pos += 12 + length
    except (OSError, struct.error):
        pass
    return _Sniff("image", "image/apng" if animated else "image/png", "png", animated, width, height)


def _skip_subblocks(f: IO[bytes]) -> bool:
    for _ in range(1_000_000):
        b = f.read(1)
        if not b:
            return False
        if b[0] == 0:
            return True
        f.seek(b[0], os.SEEK_CUR)
    return False


def _gif_frames(f: IO[bytes], limit: int = 2) -> int:
    try:
        f.seek(0)
        hdr = f.read(13)
        if len(hdr) < 13:
            return 0
        flags = hdr[10]
        if flags & 0x80:
            f.seek(3 * (2 ** ((flags & 7) + 1)), os.SEEK_CUR)
        frames = 0
        for _ in range(1_000_000):
            b = f.read(1)
            if not b or b == b";":
                break
            if b == b"!":
                if not f.read(1) or not _skip_subblocks(f):
                    break
            elif b == b",":
                frames += 1
                if frames >= limit:
                    break
                desc = f.read(9)
                if len(desc) < 9:
                    break
                if desc[8] & 0x80:
                    f.seek(3 * (2 ** ((desc[8] & 7) + 1)), os.SEEK_CUR)
                if not f.read(1) or not _skip_subblocks(f):
                    break
            else:
                break
        return frames
    except (OSError, ValueError):
        return 0


def _webp_info(head: bytes) -> _Sniff:
    animated = False
    width = height = None
    chunk = head[12:16]
    try:
        if chunk == b"VP8X" and len(head) >= 30:
            animated = bool(head[20] & 0x02)
            width = 1 + int.from_bytes(head[24:27], "little")
            height = 1 + int.from_bytes(head[27:30], "little")
        elif chunk == b"VP8 " and len(head) >= 30:
            width = struct.unpack("<H", head[26:28])[0] & 0x3FFF
            height = struct.unpack("<H", head[28:30])[0] & 0x3FFF
        elif chunk == b"VP8L" and len(head) >= 25:
            bits = int.from_bytes(head[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
    except struct.error:
        pass
    if not animated and b"ANIM" in head[12:4096]:
        animated = True
    return _Sniff("image", "image/webp", "webp", animated, width, height)


def _ftyp_info(head: bytes, ext: str) -> _Sniff | None:
    try:
        box_len = struct.unpack(">I", head[0:4])[0]
    except struct.error:
        return None
    major = head[8:12]
    end = min(len(head), box_len if 16 <= box_len <= 4096 else 64)
    brands = {major} | {head[i:i + 4] for i in range(16, end - 3, 4)}
    if major in (b"avif", b"avis") or (b"avif" in brands and major in (b"mif1", b"msf1", b"miaf")):
        return _Sniff("image", "image/avif", "avif", animated=major == b"avis" or b"avis" in brands)
    if brands & {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"} and major in (
            b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"mif2"):
        return _Sniff("image", "image/heif" if major == b"mif1" else "image/heic", "heic",
                      animated=major in (b"hevc", b"hevx", b"msf1"))
    if major == b"crx " or b"crx " in brands:
        return _Sniff("image", "image/x-canon-cr3", "cr3")
    if major in (b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "):
        return _Sniff("audio", "audio/mp4", "m4a")
    if major == b"qt  ":
        return _Sniff("video", "video/quicktime", "mov")
    if major.startswith(b"3g2"):
        return _Sniff("video", "video/3gpp2", "3g2")
    if major.startswith(b"3gp") or major.startswith(b"3ge") or major.startswith(b"3gs"):
        return _Sniff("video", "video/3gpp", "3gp")
    if major in (b"jp2 ", b"jpx ", b"jpm "):
        return _Sniff("image", "image/jp2", "jp2")
    if ext in ("m4a", "m4b", "m4r", "aac", "alac"):
        return _Sniff("audio", "audio/mp4", "m4a")
    if major in (b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"dash", b"M4V ", b"M4VH",
                 b"M4VP", b"mmp4", b"MSNV", b"NDAS", b"f4v ", b"XAVC", b"MQT ", b"FACE", b"av01", b"cmfc") \
            or major.startswith(b"mp4") or major.startswith(b"iso"):
        return _Sniff("video", "video/mp4" if ext != "m4v" else "video/x-m4v", "mp4")
    return _Sniff("video", "video/mp4", "mp4")


def _cfb_info(head: bytes, ext: str) -> _Sniff:
    mapping = {"doc": "document", "dot": "document", "xls": "spreadsheet", "xlt": "spreadsheet",
               "ppt": "presentation", "pps": "presentation", "pot": "presentation", "msi": "package",
               "docx": "document", "xlsx": "spreadsheet", "pptx": "presentation", "docm": "document",
               "xlsm": "spreadsheet", "pptm": "presentation", "msg": "document", "wps": "document"}
    if ext in mapping:
        cat = mapping[ext]
        return _Sniff(cat, mime_for_ext(ext) if ext != "msg" else "application/vnd.ms-outlook", ext)
    utf16 = head.decode("utf-16-le", "ignore")
    if "EncryptedPackage" in utf16:
        return _Sniff("document", "application/x-ole-storage", "cfb")
    if "WordDocument" in utf16:
        return _Sniff("document", "application/msword", "doc")
    if "Workbook" in utf16 or "Book" in utf16:
        return _Sniff("spreadsheet", "application/vnd.ms-excel", "xls")
    if "PowerPoint Document" in utf16:
        return _Sniff("presentation", "application/vnd.ms-powerpoint", "ppt")
    return _Sniff("unknown", "application/x-ole-storage", "cfb")


def _zip_member_head(zf: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    """At most ``limit`` bytes of member ``name``, or ``None`` (missing, larger, unreadable, unsafe method).

    Classification runs on every drag, so a crafted archive must not make it inflate gigabytes:
    only stored / deflated members are read (zipfile inflates those in bounded steps), never bzip2,
    LZMA or zstd ones, whose every 4 KB compressed chunk is decompressed in one call whatever the
    sizes declared in the central directory.
    """
    try:
        info = zf.getinfo(name)
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or info.file_size > limit:
            return None
        with zf.open(info) as fh:
            return fh.read(limit)
    except (KeyError, OSError, EOFError, zipfile.BadZipFile, RuntimeError, NotImplementedError, ValueError,
            zlib.error):
        return None


def _office_pages(zf: zipfile.ZipFile, names: set[str], tag: str) -> int | None:
    if "docProps/app.xml" not in names:
        return None
    data = _zip_member_head(zf, "docProps/app.xml", 2_000_000)
    if data is None:
        return None
    m = re.search(rf"<(?:\w+:)?{tag}>(\d+)</(?:\w+:)?{tag}>", data.decode("utf-8", "ignore"))
    return int(m.group(1)) if m else None


def _odf_pages(zf: zipfile.ZipFile, names: set[str]) -> int | None:
    if "meta.xml" not in names:
        return None
    data = _zip_member_head(zf, "meta.xml", 2_000_000)
    if data is None:
        return None
    m = re.search(r'meta:page-count="(\d+)"', data.decode("utf-8", "ignore"))
    return int(m.group(1)) if m else None


def _zip_info(path: str, ext: str) -> _Sniff:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError):
        if ext in _ZIP_BASED and ext in EXT_TABLE:
            cat, mime = EXT_TABLE[ext]
            return _Sniff(cat, mime, ext)
        return _Sniff("archive", "application/zip", "zip")
    with zf:
        try:
            names_list = zf.namelist()
        except (OSError, RuntimeError):
            names_list = []
        names = set(names_list)
        if "mimetype" in names:
            # EPUB / ODF store it uncompressed (~40 bytes); anything bigger is not a real mimetype
            mt = (_zip_member_head(zf, "mimetype", 200) or b"").decode("ascii", "ignore").strip()
            if mt == "application/epub+zip":
                return _Sniff("ebook", "application/epub+zip", "epub")
            odf = {
                "application/vnd.oasis.opendocument.text": ("document", "odt"),
                "application/vnd.oasis.opendocument.text-template": ("document", "ott"),
                "application/vnd.oasis.opendocument.text-master": ("document", "odm"),
                "application/vnd.oasis.opendocument.spreadsheet": ("spreadsheet", "ods"),
                "application/vnd.oasis.opendocument.spreadsheet-template": ("spreadsheet", "ots"),
                "application/vnd.oasis.opendocument.presentation": ("presentation", "odp"),
                "application/vnd.oasis.opendocument.presentation-template": ("presentation", "otp"),
                "application/vnd.oasis.opendocument.graphics": ("document", "odg"),
            }
            if mt in odf:
                cat, fmt = odf[mt]
                pages = _odf_pages(zf, names) if cat == "document" else None
                return _Sniff(cat, mt, fmt, pages=pages)
        if "[Content_Types].xml" in names:
            if any(n.startswith("word/") for n in names_list):
                fmt = ext if ext in ("docx", "docm", "dotx", "dotm") else "docx"
                return _Sniff("document", mime_for_ext(fmt), fmt, pages=_office_pages(zf, names, "Pages"))
            if any(n.startswith("xl/") for n in names_list):
                fmt = ext if ext in ("xlsx", "xlsm", "xltx") else "xlsx"
                return _Sniff("spreadsheet", mime_for_ext(fmt), fmt)
            if any(n.startswith("ppt/") for n in names_list):
                fmt = ext if ext in ("pptx", "pptm", "ppsx", "potx") else "pptx"
                return _Sniff("presentation", mime_for_ext(fmt), fmt, pages=_office_pages(zf, names, "Slides"))
            if "AppxManifest.xml" in names:
                fmt = ext if ext in ("msix", "appx") else "msix"
                return _Sniff("package", mime_for_ext(fmt), fmt)
        if "AndroidManifest.xml" in names or "base/manifest/AndroidManifest.xml" in names:
            fmt = ext if ext in ("apk", "aab", "xapk") else "apk"
            return _Sniff("package", "application/vnd.android.package-archive", fmt)
        if "META-INF/MANIFEST.MF" in names and (ext in ("jar", "war", "ear") or any(n.endswith(".class") for n in names_list)):
            fmt = ext if ext in ("jar", "war", "ear") else "jar"
            return _Sniff("archive", "application/java-archive", fmt)
        if ext in _ZIP_BASED and ext in EXT_TABLE and ext not in (
                "docx", "docm", "dotx", "dotm", "xlsx", "xlsm", "xltx", "pptx", "pptm", "ppsx", "potx",
                "odt", "ott", "ods", "ots", "odp", "otp", "odg", "epub", "kepub", "msix", "appx", "apk", "aab"):
            cat, mime = EXT_TABLE[ext]
            return _Sniff(cat, mime, ext)
        if ext == "kepub":
            return _Sniff("ebook", "application/epub+zip", "epub")
    return _Sniff("archive", "application/zip", "zip")


def _ogg_info(head: bytes, ext: str) -> _Sniff:
    window = head[:4096]
    if b"OpusHead" in window:
        return _Sniff("audio", "audio/ogg", "opus")
    if b"\x80theora" in window or b"\x80kate" in window:
        return _Sniff("video", "video/ogg", "ogv")
    if b"\x01vorbis" in window:
        if ext == "ogv":
            return _Sniff("video", "video/ogg", "ogv")
        return _Sniff("audio", "audio/ogg", "ogg")
    if b"Speex" in window:
        return _Sniff("audio", "audio/ogg", "spx")
    if b"\x7fFLAC" in window:
        return _Sniff("audio", "audio/ogg", "oga")
    return _Sniff("audio" if ext != "ogv" else "video", "audio/ogg" if ext != "ogv" else "video/ogg", "ogg")


def _ebml_info(head: bytes, ext: str) -> _Sniff:
    window = head[:4096]
    is_webm = b"webm" in window[:64]
    if ext in ("mka", "weba"):
        return _Sniff("audio", "audio/webm" if is_webm else "audio/x-matroska", ext)
    if ext == "mks":
        return _Sniff("subtitle", "text/x-matroska", "mks")
    if is_webm:
        return _Sniff("video", "video/webm", "webm")
    return _Sniff("video", "video/x-matroska", "mkv")


def _bin(head: bytes, n: int = 64) -> bool:
    """``True`` when the first ``n`` bytes contain a NUL (i.e. the data is not plain text)."""
    return b"\x00" in head[:n]


def _font_numtables_ok(head: bytes) -> bool:
    if len(head) < 12:
        return False
    num = struct.unpack(">H", head[4:6])[0]
    return 1 <= num <= 200


def _sniff_binary(f: IO[bytes], head: bytes, path: str, ext: str, size: int) -> _Sniff | None:
    h = head
    # ------------------------------------------------------------------ images
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_info(f, h)
    if h.startswith(b"\xff\xd8\xff"):
        return _Sniff("image", "image/jpeg", "jpg")
    if h[:6] in (b"GIF87a", b"GIF89a"):
        width = height = None
        if len(h) >= 10:
            width, height = struct.unpack("<HH", h[6:10])
        frames = _gif_frames(f)
        return _Sniff("image", "image/gif", "gif", frames > 1, width, height)
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return _webp_info(h)
    if h[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        if ext in _RAW_EXTS:
            return _Sniff("image", EXT_TABLE[ext][1], ext)
        return _Sniff("image", "image/tiff", "tiff")
    if (h[:4] in (b"IIRO", b"IIRS") and h[4:8] == b"\x08\x00\x00\x00") or h.startswith(b"FUJIFILMCCD-RAW"):
        return _Sniff("image", EXT_TABLE.get(ext, ("image", "image/x-raw"))[1], ext or "raw")
    if h[:2] == b"BM" and len(h) >= 18 and struct.unpack("<I", h[14:18])[0] in (12, 16, 40, 52, 56, 64, 108, 124):
        return _Sniff("image", "image/bmp", "bmp")
    if h[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00") and len(h) >= 6 and 0 < struct.unpack("<H", h[4:6])[0] < 256 \
            and ext in ("ico", "cur", ""):
        return _Sniff("image", "image/vnd.microsoft.icon" if h[2] == 1 else "image/x-win-bitmap",
                      "ico" if h[2] == 1 else "cur")
    if h[:4] == b"8BPS" and h[4:6] in (b"\x00\x01", b"\x00\x02"):
        return _Sniff("image", "image/vnd.adobe.photoshop", "psd")
    if h[:2] == b"\xff\x0a" or h.startswith(b"\x00\x00\x00\x0cJXL \r\n\x87\n"):
        return _Sniff("image", "image/jxl", "jxl")
    if h[:4] == b"qoif" and len(h) >= 14 and h[12] in (3, 4) and h[13] in (0, 1):
        return _Sniff("image", "image/qoi", "qoi")
    if h[:4] == b"\x76\x2f\x31\x01":
        return _Sniff("image", "image/x-exr", "exr")
    if h.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n"):
        return _Sniff("image", "image/jp2", "jp2")
    if h.startswith(b"gimp xcf") and _bin(h, 32):
        return _Sniff("image", "image/x-xcf", "xcf")
    if h[:4] == b"DDS " and h[4:8] == b"\x7c\x00\x00\x00":
        return _Sniff("image", "image/vnd-ms.dds", "dds")
    if h[:4] == b"icns" and len(h) >= 8 and struct.unpack(">I", h[4:8])[0] == size:
        return _Sniff("image", "image/x-icns", "icns")
    if h.startswith(b"#?RADIANCE") or h.startswith(b"#?RGBE"):
        return _Sniff("image", "image/vnd.radiance", "hdr")
    # ------------------------------------------------------------------ ISO-BMFF
    if h[4:8] == b"ftyp":
        return _ftyp_info(h, ext)
    # ------------------------------------------------------------------ PDF
    if h.startswith(b"%PDF-") or (b"%PDF-" in h[:1024] and (ext == "pdf" or _bin(h, 1024))):
        return _Sniff("pdf", "application/pdf", "pdf")
    # ------------------------------------------------------------------ ZIP family
    if h[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return _zip_info(path, ext)
    # ------------------------------------------------------------------ archives / packages
    if h.startswith(b"7z\xbc\xaf\x27\x1c"):
        if ext in ("cb7",):
            return _Sniff("ebook", "application/x-cb7", "cb7")
        return _Sniff("archive", "application/x-7z-compressed", "7z" if ext != "7z.001" else "7z.001")
    if h.startswith(b"Rar!\x1a\x07"):
        if ext == "cbr":
            return _Sniff("ebook", "application/vnd.comicbook-rar", "cbr")
        return _Sniff("archive", "application/vnd.rar", "rar")
    if h[:2] == b"\x1f\x8b":
        if ext == "svgz":
            return _Sniff("svg", "image/svg+xml-compressed", "svgz")
        if ext in ("pkg.tar.gz",):
            return _Sniff("package", "application/x-alpm-package", ext)
        if ext in ("tar.gz", "tgz"):
            return _Sniff("archive", "application/x-compressed-tar", ext)
        return _Sniff("archive", "application/gzip", "gz")
    if h.startswith(b"\xfd7zXZ\x00"):
        if ext == "pkg.tar.xz":
            return _Sniff("package", "application/x-alpm-package", ext)
        if ext in ("tar.xz", "txz"):
            return _Sniff("archive", "application/x-xz-compressed-tar", ext)
        return _Sniff("archive", "application/x-xz", "xz")
    if h.startswith(b"\x28\xb5\x2f\xfd"):
        if ext == "pkg.tar.zst":
            return _Sniff("package", "application/x-alpm-package", ext)
        if ext in ("tar.zst", "tzst"):
            return _Sniff("archive", "application/x-zstd-compressed-tar", ext)
        return _Sniff("archive", "application/zstd", "zst")
    if h[:3] == b"BZh" and h[3:4] in b"123456789" and h[4:10] in (b"\x31\x41\x59\x26\x53\x59",
                                                                     b"\x17\x72\x45\x38\x50\x90"):
        if ext == "pkg.tar.bz2":
            return _Sniff("package", "application/x-alpm-package", ext)
        if ext in ("tar.bz2", "tbz2", "tbz"):
            return _Sniff("archive", "application/x-bzip2-compressed-tar", ext)
        return _Sniff("archive", "application/x-bzip2", "bz2")
    if h.startswith(b"\x04\x22\x4d\x18"):
        return _Sniff("archive", "application/x-lz4" if ext != "tar.lz4" else "application/x-lz4-compressed-tar",
                      "tar.lz4" if ext == "tar.lz4" else "lz4")
    if h.startswith(b"LZIP") and h[4:5] in (b"\x00", b"\x01"):
        return _Sniff("archive", "application/x-lzip", "tar.lz" if ext == "tar.lz" else "lz")
    if h.startswith(b"MSCF") and h[4:8] == b"\x00\x00\x00\x00":
        return _Sniff("archive", "application/vnd.ms-cab-compressed", "cab")
    if h.startswith(b"!<arch>\n"):
        if h[8:21] == b"debian-binary":
            return _Sniff("package", "application/vnd.debian.binary-package", "deb")
        return _Sniff("archive", "application/x-archive", "ar")
    if h.startswith(b"\xed\xab\xee\xdb"):
        return _Sniff("package", "application/x-rpm", "rpm")
    if h.startswith(b"xar!") and h[4:6] == b"\x00\x1c":
        return _Sniff("archive", "application/x-xar", "xar")
    if re.match(rb"07070[12][0-9A-Fa-f]{104}", h[:110]) or re.match(rb"070707[0-7]{70}", h[:76]):
        return _Sniff("archive", "application/x-cpio", "cpio")
    if len(h) >= 262 and h[257:262] == b"ustar":
        if ext == "cbt":
            return _Sniff("ebook", "application/x-cbt", "cbt")
        return _Sniff("archive", "application/x-tar", "tar")
    if (h.startswith(b"hsqs") or h.startswith(b"sqsh")) and _bin(h, 96):
        return _Sniff("disk", "application/x-squashfs", "squashfs")
    if h.startswith(b"QFI\xfb"):
        return _Sniff("disk", "application/x-qemu-disk", "qcow2")
    if h.startswith(b"KDMV") and _bin(h, 64):
        return _Sniff("disk", "application/x-vmdk-disk", "vmdk")
    if h.startswith(b"vhdxfile") and _bin(h, 512):
        return _Sniff("disk", "application/x-vhdx-disk", "vhdx")
    if h.startswith(b"conectix") and _bin(h, 64):
        return _Sniff("disk", "application/x-vhd-disk", "vhd")
    if h.startswith(b"<<< ") and b"VirtualBox" in h[:64]:
        return _Sniff("disk", "application/x-virtualbox-vdi", "vdi")
    if h.startswith(b"MSWIM\x00\x00\x00"):
        return _Sniff("disk", "application/x-ms-wim", "wim")
    # ------------------------------------------------------------------ executables
    if h.startswith(b"\x7fELF"):
        if h[8:11] in (b"AI\x02", b"AI\x01"):
            return _Sniff("appimage", "application/vnd.appimage", "appimage")
        return _Sniff("unknown", "application/x-executable", "elf")
    if h[:2] == b"MZ" and len(h) >= 64:
        pe = struct.unpack("<I", h[60:64])[0]
        is_pe = pe + 4 <= len(h) and h[pe:pe + 4] == b"PE\x00\x00"
        if is_pe or (ext in ("exe", "com", "scr", "dll", "sys") and _bin(h, 64)):
            if ext in ("exe", "msi", "scr", "com"):
                return _Sniff("package", "application/x-msdownload", "exe")
            return _Sniff("unknown", "application/x-msdownload", "exe")
    if h.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _cfb_info(h, ext)
    # ------------------------------------------------------------------ ISO 9660 / UDF
    if size > 0x8006:
        try:
            f.seek(0x8001)
            marker = f.read(6)
        except OSError:
            marker = b""
        if marker[:5] in (b"CD001", b"BEA01", b"NSR02", b"NSR03") and marker[5:6] in (b"\x01", b"\x02"):
            return _Sniff("disk", "application/x-cd-image", "iso")
    # ------------------------------------------------------------------ fonts
    if h[:4] == b"OTTO" and _font_numtables_ok(h):
        return _Sniff("font", "font/otf", "otf")
    if h[:4] in (b"\x00\x01\x00\x00", b"true", b"typ1") and _font_numtables_ok(h):
        return _Sniff("font", "font/ttf", "ttf")
    _flavours = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")
    if h[:4] == b"wOFF" and h[4:8] in _flavours:
        return _Sniff("font", "font/woff", "woff")
    if h[:4] == b"wOF2" and h[4:8] in _flavours:
        return _Sniff("font", "font/woff2", "woff2")
    if h[:4] == b"ttcf" and h[4:6] in (b"\x00\x01", b"\x00\x02"):
        return _Sniff("font", "font/collection", "ttc")
    if h[:2] == b"\x80\x01" and b"%!PS-AdobeFont" in h[:64]:
        return _Sniff("font", "application/x-font-type1", "pfb")
    # ------------------------------------------------------------------ audio / video
    if h[:3] == b"ID3" and len(h) > 5 and h[3] in (2, 3, 4) and h[4] != 0xFF:
        return _Sniff("audio", "audio/mpeg", "mp3")
    if h[:4] == b"fLaC" and len(h) > 4 and (h[4] & 0x7F) == 0:
        return _Sniff("audio", "audio/flac", "flac")
    if h[:4] == b"OggS" and h[4:5] == b"\x00":
        return _ogg_info(h, ext)
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return _Sniff("audio", "audio/wav", "wav")
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return _Sniff("video", "video/x-msvideo", "avi")
    if h[:4] == b"RF64" and h[8:12] == b"WAVE":
        return _Sniff("audio", "audio/wav", "wav")
    if h[:4] == b"FORM" and h[8:12] in (b"AIFF", b"AIFC"):
        return _Sniff("audio", "audio/aiff", "aiff")
    if h[:4] == b"FORM" and h[8:12] == b"DJVM" or (h[:8] == b"AT&TFORM" and h[12:16] in (b"DJVU", b"DJVM")):
        return _Sniff("ebook", "image/vnd.djvu", "djvu")
    if h[:4] == b"MThd" and h[4:8] == b"\x00\x00\x00\x06":
        return _Sniff("audio", "audio/midi", "mid")
    if h.startswith(b"#!AMR") and (ext in ("amr", "") or _bin(h, 64)):
        return _Sniff("audio", "audio/amr", "amr")
    if h[:4] == b".snd" and b"\x00" in h[4:8]:
        return _Sniff("audio", "audio/basic", "au")
    if h[:4] == b"wvpk" and _bin(h, 32):
        return _Sniff("audio", "audio/x-wavpack", "wv")
    if h[:4] == b"MAC " and (ext == "ape" or _bin(h, 32)):
        return _Sniff("audio", "audio/x-ape", "ape")
    if h[:4] == b"caff" and h[4:6] == b"\x00\x01":
        return _Sniff("audio", "audio/x-caf", "caf")
    if h[:4] == b"DSD " and h[4:12] == struct.pack("<Q", 28):
        return _Sniff("audio", "audio/x-dsf", "dsf")
    if h[:4] == b"TTA1" and _bin(h, 32):
        return _Sniff("audio", "audio/x-tta", "tta")
    if h[:16] == b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c":
        if ext == "wma":
            return _Sniff("audio", "audio/x-ms-wma", "wma")
        return _Sniff("video", "video/x-ms-wmv", "wmv" if ext != "asf" else "asf")
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return _ebml_info(h, ext)
    if h[:3] == b"FLV" and h[3:4] == b"\x01":
        return _Sniff("video", "video/x-flv", "flv")
    if h[:4] == b".RMF" and _bin(h, 32):
        return _Sniff("video", "application/vnd.rn-realmedia", "rm")
    if h[:4] == b"\x00\x00\x01\xba" or h[:4] == b"\x00\x00\x01\xb3":
        return _Sniff("video", "video/mpeg", "mpg")
    looks_binary = b"\x00" in h[:1024] or b"\xff" in h[:1024]
    if len(h) >= 377 and h[0] == 0x47 and h[188] == 0x47 and h[376] == 0x47 and looks_binary:
        return _Sniff("video", "video/mp2t", "ts")
    if len(h) >= 389 and h[4] == 0x47 and h[196] == 0x47 and h[388] == 0x47 and looks_binary:
        return _Sniff("video", "video/mp2t", "m2ts")
    if h[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa", b"\xff\xe3") and ext in ("mp3", "mp2", "mpga", ""):
        return _Sniff("audio", "audio/mpeg", "mp3")
    if h[:2] in (b"\xff\xf1", b"\xff\xf9") and ext in ("aac", ""):
        return _Sniff("audio", "audio/aac", "aac")
    if h[:2] == b"\x0b\x77" and ext in ("ac3", "eac3"):
        return _Sniff("audio", "audio/ac3", ext)
    # ------------------------------------------------------------------ ebooks / data
    if len(h) >= 68 and h[60:68] in (b"BOOKMOBI", b"TEXtREAd"):
        fmt = ext if ext in ("mobi", "azw", "azw3", "prc") else "mobi"
        return _Sniff("ebook", "application/x-mobipocket-ebook", fmt)
    if h.startswith(b"SQLite format 3\x00"):
        return _Sniff("data", "application/vnd.sqlite3", "sqlite")
    if h[:4] == b"PAR1" and (ext == "parquet" or _bin(h, 64)):
        return _Sniff("data", "application/vnd.apache.parquet", "parquet")
    if h.startswith(b"{\\rtf"):
        return _Sniff("document", "application/rtf", "rtf")
    if h.startswith(b"%!PS"):
        if b"EPSF" in h[:40]:
            return _Sniff("image", "image/x-eps", "eps")
        return _Sniff("document", "application/postscript", "ps")
    if h.startswith(b"\xc5\xd0\xd3\xc6"):
        return _Sniff("image", "image/x-eps", "eps")
    return None


# -------------------------------------------------------------------------------------- text

_SRT_RE = re.compile(r"^\s*\d+\s*\r?\n\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->")
_SVG_RE = re.compile(r"<svg[\s>/]", re.I)
_PRE_ROOT_RE = re.compile(r"^(?:\s|<\?xml[^>]*\?>|<!--.*?-->|<!DOCTYPE[^>]*>)*", re.S | re.I)


def _decode_text(head: bytes, complete: bool) -> tuple[str, str] | None:
    """Return ``(text, encoding)`` when ``head`` looks like text, else ``None``."""
    if not head:
        return "", "utf-8"
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
                     (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if head.startswith(bom):
            try:
                return head.decode(enc, "replace").lstrip("\ufeff"), enc.replace("-sig", "")
            except LookupError:  # pragma: no cover - codecs always present
                return None
    if b"\x00" in head:
        return None
    try:
        text = head.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError as exc:
        if not complete and exc.start >= len(head) - 3 and exc.reason in ("unexpected end of data", "invalid continuation byte"):
            try:
                text = head[: exc.start].decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                text = None  # type: ignore[assignment]
                encoding = ""
        else:
            text = None  # type: ignore[assignment]
            encoding = ""
        if text is None:
            high = sum(1 for b in head if b >= 0x80)
            if high > len(head) * 0.3:
                return None
            text = head.decode("cp1252", "replace")
            encoding = "cp1252"
    ctrl = sum(1 for ch in text if (ord(ch) < 32 and ch not in "\t\n\r\f\b\x1b\v") or ord(ch) == 0x7f)
    if text and ctrl > max(2, len(text) // 100):
        return None
    return text, encoding


def _shebang_mime(first_line: str) -> str:
    line = first_line.lower()
    for key, mime in (("python", "text/x-python"), ("node", "text/javascript"), ("deno", "text/javascript"),
                      ("bun", "text/javascript"), ("perl", "text/x-perl"), ("ruby", "text/x-ruby"),
                      ("php", "application/x-php"), ("lua", "text/x-lua"), ("fish", "application/x-shellscript"),
                      ("zsh", "application/x-shellscript"), ("bash", "application/x-shellscript"),
                      ("/sh", "application/x-shellscript"), (" sh", "application/x-shellscript"),
                      ("awk", "text/x-awk"), ("tcl", "text/x-tcl")):
        if key in line:
            return mime
    return "text/x-script"


def _sniff_text(text: str, ext: str, name: str, complete: bool) -> _Sniff:
    s = text.lstrip("\ufeff \t\r\n")
    by_ext = ext_category(ext, name)
    if s.startswith("#!") and not s.startswith("#!["):
        first = s.split("\n", 1)[0]
        return _Sniff("code", _shebang_mime(first), ext or "script")
    if s.startswith("<"):
        root = _PRE_ROOT_RE.sub("", s[:8192], count=1)
        low = root[:200].lower()
        if low.startswith("<svg") or (_SVG_RE.match(root) is not None):
            return _Sniff("svg", "image/svg+xml", "svg")
        if low.startswith("<fictionbook"):
            return _Sniff("ebook", "application/x-fictionbook+xml", "fb2")
        if low.startswith("<tt") and "ttml" in root[:500].lower():
            return _Sniff("subtitle", "application/ttml+xml", "ttml")
        if low.startswith("<html") or s[:200].lower().startswith("<!doctype html"):
            return _Sniff("code", "text/html", ext if ext in ("htm", "xhtml") else "html")
        if by_ext and by_ext[0] in _TEXTY | {"document", "spreadsheet", "presentation", "ebook"} and (
                by_ext[0] in _TEXTY or ext in _TEXT_EXTS_OTHER):
            return _Sniff(by_ext[0], by_ext[1], ext)
        if low.startswith(("<rss", "<feed", "<opml")):
            return _Sniff("data", "application/rss+xml" if low.startswith("<rss") else "application/xml", "xml")
        if s.startswith("<?xml") or re.match(r"<[A-Za-z_][\w:.-]*[\s>/]", root[:200]):
            if "<svg" in root[:2000].lower() and root[:2000].lower().find("<svg") < 300:
                return _Sniff("svg", "image/svg+xml", "svg")
            return _Sniff("data", "application/xml", "xml")
    if s.startswith("WEBVTT"):
        return _Sniff("subtitle", "text/vtt", "vtt")
    if s.startswith("[Script Info]"):
        return _Sniff("subtitle", "text/x-ssa", "ass")
    if _SRT_RE.match(s[:400]):
        return _Sniff("subtitle", "application/x-subrip", "srt")
    if by_ext:
        cat, mime = by_ext
        if cat in _TEXTY or ext in _TEXT_EXTS_OTHER or name.lower() in NAME_TABLE:
            return _Sniff(cat, mime, ext or name.lower())
    if s[:1] in ("{", "[") and complete:
        try:
            json.loads(text)
            return _Sniff("data", "application/json", "json")
        except ValueError:
            pass
    return _Sniff("text", "text/plain", "txt")


# ================================================================================== details

_heif_registered = False
_jxl_registered = False


def _register_plugins(fmt: str) -> None:
    global _heif_registered, _jxl_registered
    if fmt in ("heic", "heif") and not _heif_registered:
        _heif_registered = True
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
        except Exception:
            pass
    if fmt == "jxl" and not _jxl_registered:
        _jxl_registered = True
        try:
            import pillow_jxl  # type: ignore  # noqa: F401  (registers the plugin on import)
        except Exception:
            pass


def _jpeg_size(f: IO[bytes]) -> tuple[int, int] | None:
    try:
        f.seek(2)
        for _ in range(10000):
            b = f.read(1)
            while b and b != b"\xff":
                b = f.read(1)
            while b == b"\xff":
                b = f.read(1)
            if not b:
                return None
            marker = b[0]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            seg = f.read(2)
            if len(seg) < 2:
                return None
            length = struct.unpack(">H", seg)[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                data = f.read(5)
                if len(data) < 5:
                    return None
                h, w = struct.unpack(">HH", data[1:5])
                return w, h
            f.seek(length - 2, os.SEEK_CUR)
    except (OSError, struct.error):
        return None
    return None


def _image_size(path: str, sniff: _Sniff) -> tuple[int | None, int | None]:
    if sniff.fmt in ("png", "gif", "webp") and sniff.width and sniff.height:
        return sniff.width, sniff.height
    _register_plugins(sniff.fmt)
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with Image.open(path) as im:
                w, h = im.size
                try:
                    orientation = im.getexif().get(0x0112)
                except Exception:
                    orientation = None
                if orientation in (5, 6, 7, 8):
                    w, h = h, w
                return int(w), int(h)
    except Exception:
        pass
    if sniff.fmt == "jpg":
        try:
            with open(path, "rb") as f:
                size = _jpeg_size(f)
            if size:
                return size
        except OSError:
            pass
    return sniff.width, sniff.height


_SVG_LEN_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")


def _svg_size(path: str, fmt: str) -> tuple[int | None, int | None]:
    try:
        if fmt == "svgz":
            import gzip

            with gzip.open(path, "rb") as fh:
                data = fh.read(16384)
        else:
            with open(path, "rb") as fh:
                data = fh.read(16384)
    except (OSError, EOFError):
        return None, None
    text = data.decode("utf-8", "ignore")
    m = re.search(r"<svg\b([^>]*)>", text, re.I | re.S)
    if not m:
        return None, None
    attrs = m.group(1)

    def attr(name: str) -> str | None:
        am = re.search(rf'\b{name}\s*=\s*["\']([^"\']*)["\']', attrs)
        return am.group(1) if am else None

    w, h = attr("width"), attr("height")
    wm = _SVG_LEN_RE.match(w) if w else None
    hm = _SVG_LEN_RE.match(h) if h else None
    if wm and hm:
        return int(round(float(wm.group(1)))), int(round(float(hm.group(1))))
    vb = attr("viewBox")
    if vb:
        parts = re.split(r"[\s,]+", vb.strip())
        if len(parts) == 4:
            try:
                return int(round(float(parts[2]))), int(round(float(parts[3])))
            except ValueError:
                pass
    return None, None


def _pdf_pages(path: str, size: int) -> int | None:
    try:
        import pymupdf  # type: ignore
    except Exception:
        pymupdf = None  # type: ignore[assignment]
    if pymupdf is not None:
        try:
            try:
                pymupdf.TOOLS.mupdf_display_errors(False)
            except Exception:
                pass
            with pymupdf.open(path) as doc:
                if getattr(doc, "needs_pass", False):
                    doc.authenticate("")
                count = int(doc.page_count)
                return count if count > 0 else None
        except Exception:
            return None
    if size > 150 * 1024 * 1024:
        return None
    try:
        from pypdf import PdfReader  # type: ignore

        logging.getLogger("pypdf").setLevel(logging.ERROR)
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return None
        return len(reader.pages)
    except Exception:
        return None


# ================================================================================= classify

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]+):")


#: Extensions of server-side pages: a link ending in them is a page, not a file to name it after.
_PAGE_EXTS = frozenset({"html", "htm", "xhtml", "shtml", "php", "asp", "aspx", "jsp", "cgi"})
_URL_LABEL_MAX = 64
_URL_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _url_label(host: str, segs: list[str], query: str) -> str:
    """Readable label of a page link: host without ``www.`` + path (+ query), shortened in the middle."""
    shown = host[4:] if host.startswith("www.") else host
    tail = f"?{query}" if query else ""
    label = shown + "".join(f"/{s}" for s in segs) + tail
    if len(label) > _URL_LABEL_MAX and len(segs) > 1:
        label = f"{shown}/…/{segs[-1]}{tail}"
    if len(label) > _URL_LABEL_MAX:
        label = label[: _URL_LABEL_MAX - 1].rstrip() + "…"
    return label


def _url_info(url: str) -> FileInfo:
    url = url.strip()
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        parts = None
    name = ""
    host = ""
    segs: list[str] = []
    if parts is not None:
        try:
            host = parts.hostname or ""
        except ValueError:
            host = ""
        segs = [urllib.parse.unquote(s) for s in (parts.path or "").split("/") if s]
        if segs:
            name = segs[-1]
    if not name:
        name = host or url
    stem, ext = util.split_name(name)
    if ext and ext not in EXT_TABLE:
        stem, ext = name, ""
    if host and (not ext or ext in _PAGE_EXTS):
        # a page ("…/watch?v=…", "…/wiki/X", the bare host): the wheel title, the dialogs and the HUD show
        # host + path, since the last segment alone ("watch", "index.php") says nothing; ``stem`` keeps
        # naming the outputs as before
        name = _url_label(host, segs, urllib.parse.unquote(parts.query) if parts is not None else "")
    name = _URL_CTRL_RE.sub(" ", name).strip() or url  # a percent-encoded line break must not split the title
    return FileInfo(path="", uri=url, name=name, stem=stem, ext=ext, category="url", mime="text/x-uri",
                    size=0, is_dir=False, exists=False, icon=CATEGORY_ICONS["url"], format="url", readable=True)


def _special_info(text: str, scheme: str) -> FileInfo:
    name = text.rstrip("/").rsplit("/", 1)[-1] or text
    name = urllib.parse.unquote(name)[:200]
    stem, ext = util.split_name(name)
    return FileInfo(path="", uri=text, name=name, stem=stem, ext=ext, category="unknown",
                    mime="text/x-uri" if scheme else "application/octet-stream", icon=CATEGORY_ICONS["unknown"])


def classify(path_or_uri: str | os.PathLike | bytes, *, deep: bool = True) -> FileInfo:
    """Classify a path, ``file://`` URI or http(s) URL.  Never raises.

    ``deep=False`` skips image dimensions and page counts (use it for very large selections).
    """
    try:
        return _classify(path_or_uri, deep)
    except Exception:  # pragma: no cover - defensive: classification must never break the menu
        log.exception("classify failed for %r", path_or_uri)
        if isinstance(path_or_uri, bytes):
            text = os.fsdecode(path_or_uri)
        elif isinstance(path_or_uri, os.PathLike):
            text = os.fspath(path_or_uri)
        else:
            text = str(path_or_uri)
        name = os.path.basename(text) or text
        stem, ext = util.split_name(name)
        return FileInfo(path=text, uri=text, name=name, stem=stem, ext=ext, category="unknown",
                        icon=CATEGORY_ICONS["unknown"])


def classify_many(items, *, deep: bool | None = None) -> list[FileInfo]:
    """Classify several items; ``deep`` defaults to ``True`` for up to 50 items."""
    items = list(items)
    if deep is None:
        deep = len(items) <= 50
    return [classify(i, deep=deep) for i in items]


def common_category(files: list[FileInfo]) -> str:
    """The shared category of ``files`` or ``"mixed"`` (``"empty"`` for no files)."""
    if not files:
        return "empty"
    cats = {f.category for f in files}
    return cats.pop() if len(cats) == 1 else "mixed"


def _classify(path_or_uri: Any, deep: bool) -> FileInfo:
    if isinstance(path_or_uri, FileInfo):
        return path_or_uri
    if isinstance(path_or_uri, bytes):
        text = os.fsdecode(path_or_uri)
    elif isinstance(path_or_uri, os.PathLike):
        text = os.fspath(path_or_uri)
    else:
        text = str(path_or_uri)
    m = _SCHEME_RE.match(text)
    scheme = m.group(1).lower() if m else ""
    if scheme in ("http", "https"):
        return _url_info(text.strip())
    if scheme == "file":
        path = util.uri_to_path(text.strip("\r\n"))
    elif scheme:
        mapped = util.gvfs_path(text.strip())
        if mapped is None:
            return _special_info(text.strip(), scheme)
        path = mapped
    else:
        path = os.path.expanduser(text)
    path = os.path.abspath(path)
    return _classify_path(path, deep)


def _set_category(info: FileInfo, category: str, mime: str, fmt: str = "") -> None:
    info.category = category if category in CATEGORIES else "unknown"
    info.mime = mime or "application/octet-stream"
    info.format = fmt
    info.icon = CATEGORY_ICONS.get(info.category, "draft")


def _classify_path(path: str, deep: bool) -> FileInfo:
    name = os.path.basename(path.rstrip("/\\")) or path
    info = FileInfo(path=path, uri=util.path_to_uri(path), name=name)
    parent = os.path.dirname(path.rstrip("/\\")) or path
    info.volatile = util.is_volatile(path)
    try:
        info.writable_dir = os.access(parent, os.W_OK)
    except (OSError, ValueError):
        info.writable_dir = False
    try:
        lst = os.lstat(path)
    except (OSError, ValueError):
        info.stem, info.ext = util.split_name(name)
        hit = ext_category(info.ext, name)
        if hit:
            _set_category(info, hit[0], hit[1], info.ext)
        else:
            _set_category(info, "unknown", mimetypes.guess_type(name)[0] or "application/octet-stream")
        return info
    info.is_link = stat.S_ISLNK(lst.st_mode)
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        info.stem, info.ext = util.split_name(name)
        _set_category(info, "unknown", "inode/symlink")
        return info
    info.exists = True
    info.mtime = st.st_mtime
    if stat.S_ISDIR(st.st_mode):
        info.is_dir = True
        info.stem, info.ext = name, ""
        info.readable = os.access(path, os.R_OK | os.X_OK)
        _set_category(info, "folder", "inode/directory", "folder")
        return info
    info.stem, info.ext = util.split_name(name)
    info.size = st.st_size
    if not stat.S_ISREG(st.st_mode):
        kind = ("inode/fifo" if stat.S_ISFIFO(st.st_mode) else "inode/socket" if stat.S_ISSOCK(st.st_mode)
                else "inode/blockdevice" if stat.S_ISBLK(st.st_mode) else "inode/chardevice")
        _set_category(info, "unknown", kind)
        return info
    info.readable = os.access(path, os.R_OK)
    if info.size == 0:
        _set_category(info, "unknown", "application/x-zerosize")
        return info
    sniff: _Sniff | None = None
    if info.readable:
        try:
            with open(path, "rb") as f:
                head = f.read(_HEAD)
                sniff = _sniff_binary(f, head, path, info.ext, info.size)
                if sniff is None:
                    decoded = _decode_text(head, complete=len(head) >= info.size)
                    if decoded is not None:
                        sniff = _sniff_text(decoded[0], info.ext, name, complete=len(head) >= info.size)
        except (OSError, ValueError) as exc:
            log.debug("cannot read %s: %s", path, exc)
            sniff = None
    if sniff is None:
        # binary content no magic matched (or unreadable): trust the extension unless it claims
        # a text format, which the content just contradicted
        hit = ext_category(info.ext, name)
        if hit and (not info.readable or hit[0] not in _TEXTY):
            sniff = _Sniff(hit[0], hit[1], info.ext)
        else:
            sniff = _Sniff("unknown", "application/octet-stream", "")
    _set_category(info, sniff.category, sniff.mime, sniff.fmt)
    info.animated = bool(sniff.animated)
    info.width, info.height, info.pages = sniff.width, sniff.height, sniff.pages
    if deep and info.readable:
        try:
            if info.category == "image":
                info.width, info.height = _image_size(path, sniff)
            elif info.category == "svg":
                info.width, info.height = _svg_size(path, sniff.fmt)
            elif info.category == "pdf":
                info.pages = _pdf_pages(path, info.size)
        except Exception as exc:  # pragma: no cover - extras must never break classification
            log.debug("details failed for %s: %s", path, exc)
    return info
