"""External dependency discovery and Arch Linux install hints.

Every external program or optional Python module an action may need has a *tool name*
(``"ffmpeg"``, ``"exiftool"``, ``"py:pymupdf"``).  Actions list them in ``requires=``; the menu
greys out actions whose tools are missing and shows :func:`install_hint` (e.g.
``sudo pacman -S --needed ffmpeg`` / ``paru -S ocrmypdf`` / ``pipx install 'rembg[cpu,cli]'``).

Lookups are cached (:func:`functools.lru_cache`) and expire every 10 minutes so that a tool
installed while the daemon runs is picked up; :func:`refresh` clears the cache immediately.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

__all__ = [
    "Tool", "TOOLS", "path", "have", "missing", "install_hint", "report", "refresh",
    "display_name", "packages_for", "aur_helper",
]

#: Directories searched after ``$PATH`` (Arch puts some perl tools outside the default PATH).
EXTRA_DIRS = (
    "/usr/bin/vendor_perl",
    "/usr/bin/core_perl",
    "/usr/bin/site_perl",
    os.path.expanduser("~/.local/bin"),
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/var/lib/flatpak/exports/bin",
    os.path.expanduser("~/.local/share/flatpak/exports/bin"),
    "/opt/bin",
)

_CACHE_TTL = 600.0
_stamp = time.monotonic()
_stamp_lock = threading.Lock()


@dataclass(frozen=True)
class Tool:
    """Description of one dependency.

    Attributes:
        name: tool name used in ``requires=`` lists.
        binaries: executable names tried in order (empty for Python modules).
        packages: Arch packages (or pip distributions) that provide it.
        repo: ``core`` | ``extra`` | ``aur`` | ``pip`` (application installed with pipx) |
            ``pypi`` (library installed with pip).
        group: category for ``dropwheel doctor`` (image, pdf, docs, media, archive, fonts, system, python).
        paths: absolute paths tried before ``$PATH``.
        module: importable module name for ``py:`` tools.
        install: explicit install command overriding the generated one.
        description: short human description (English).
    """

    name: str
    binaries: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    repo: str = "extra"
    group: str = "system"
    paths: tuple[str, ...] = ()
    module: str = ""
    install: str = ""
    description: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _b(name: str, pkg: str | tuple[str, ...], group: str, *, repo: str = "extra", binaries: tuple[str, ...] = (),
       paths: tuple[str, ...] = (), install: str = "", description: str = "") -> Tool:
    pkgs = (pkg,) if isinstance(pkg, str) else tuple(pkg)
    return Tool(name=name, binaries=binaries or (name,), packages=pkgs, repo=repo, group=group,
                paths=paths, install=install, description=description)


def _py(module: str, pkg: str, *, repo: str = "extra", install: str = "", description: str = "") -> Tool:
    return Tool(name=f"py:{module}", binaries=(), packages=(pkg,), repo=repo, group="python",
                module=module, install=install, description=description)


_TOOL_LIST: list[Tool] = [
    # ---------------------------------------------------------------- images
    _b("magick", "imagemagick", "image", description="ImageMagick 7"),
    _b("convert", "imagemagick", "image", description="ImageMagick legacy CLI"),
    _b("vips", "libvips", "image", description="libvips CLI"),
    _b("rsvg-convert", "librsvg", "image", description="SVG renderer"),
    _b("resvg", "resvg", "image", description="SVG renderer"),
    _b("inkscape", "inkscape", "image", description="vector editor"),
    _b("scour", "scour", "image", description="SVG optimiser"),
    _b("svgo", "svgo", "image", description="SVG optimiser (Node.js)"),
    _b("potrace", "potrace", "image", description="bitmap tracer"),
    _b("mkbitmap", "potrace", "image"),
    _b("vtracer", "vtracer", "image", repo="aur", description="colour vectoriser"),
    _b("rembg", "rembg", "image", repo="pip", install="pipx install 'rembg[cpu,cli]'",
       description="background removal"),
    _b("realesrgan", "realesrgan-ncnn-vulkan-bin", "image", repo="aur",
       binaries=("realesrgan-ncnn-vulkan",), description="AI upscaler"),
    _b("waifu2x", "waifu2x-ncnn-vulkan", "image", binaries=("waifu2x-ncnn-vulkan",), description="AI upscaler"),
    _b("upscayl", "upscayl-bin", "image", repo="aur", binaries=("upscayl-bin", "upscayl"),
       paths=("/usr/lib/upscayl/upscayl",)),
    _b("oxipng", "oxipng", "image", description="lossless PNG optimiser"),
    _b("pngquant", "pngquant", "image", description="lossy PNG quantiser"),
    _b("optipng", "optipng", "image"),
    _b("jpegoptim", "jpegoptim", "image", description="JPEG optimiser"),
    _b("jpegtran", "libjpeg-turbo", "image", description="lossless JPEG transforms"),
    _b("cwebp", "libwebp-utils", "image"),
    _b("dwebp", "libwebp-utils", "image"),
    _b("gif2webp", "libwebp-utils", "image"),
    _b("img2webp", "libwebp-utils", "image"),
    _b("webpmux", "libwebp-utils", "image"),
    _b("avifenc", "libavif", "image"),
    _b("avifdec", "libavif", "image"),
    _b("heif-enc", "libheif", "image"),
    _b("heif-dec", "libheif", "image", binaries=("heif-dec", "heif-convert")),
    _b("cjxl", "libjxl", "image"),
    _b("djxl", "libjxl", "image"),
    _b("gifski", "gifski", "image", description="high quality GIF encoder"),
    _b("gifsicle", "gifsicle", "image", description="GIF optimiser"),
    _b("exiftool", "perl-image-exiftool", "image", paths=("/usr/bin/vendor_perl/exiftool",),
       description="metadata reader/writer"),
    _b("exiv2", "exiv2", "image"),
    _b("mat2", "mat2", "image", description="metadata anonymiser"),
    _b("satty", "satty", "image", description="annotation tool"),
    _b("swappy", "swappy", "image", description="annotation tool"),
    _b("darktable-cli", "darktable", "image", description="RAW developer"),
    _b("zbarimg", "zbar", "image", description="QR/barcode reader"),
    _b("qrencode", "qrencode", "image", description="QR encoder"),
    _b("tesseract", ("tesseract", "tesseract-data-ita", "tesseract-data-eng"), "image", description="OCR engine"),
    _b("img2pdf", "img2pdf", "image", description="lossless images to PDF"),
    _b("grim", "grim", "image"),
    _b("swww", "swww", "image", description="wallpaper daemon"),
    # ---------------------------------------------------------------- pdf
    _b("gs", "ghostscript", "pdf", description="Ghostscript"),
    _b("qpdf", "qpdf", "pdf"),
    _b("pdftoppm", "poppler", "pdf"),
    _b("pdftocairo", "poppler", "pdf"),
    _b("pdftotext", "poppler", "pdf"),
    _b("pdfimages", "poppler", "pdf"),
    _b("pdfinfo", "poppler", "pdf"),
    _b("pdfunite", "poppler", "pdf"),
    _b("pdfseparate", "poppler", "pdf"),
    _b("pdfattach", "poppler", "pdf"),
    _b("pdfdetach", "poppler", "pdf"),
    _b("mutool", "mupdf-tools", "pdf"),
    _b("ocrmypdf", "ocrmypdf", "pdf", repo="aur", description="OCR for PDF"),
    _b("pdftk", "pdftk", "pdf"),
    _b("unpaper", "unpaper", "pdf"),
    _b("xournalpp", "xournalpp", "pdf", description="PDF annotation"),
    _b("okular", "okular", "pdf"),
    _b("pdfarranger", "pdfarranger", "pdf"),
    # ---------------------------------------------------------------- documents
    _b("soffice", "libreoffice-fresh", "docs", binaries=("soffice", "libreoffice"),
       paths=("/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice"),
       description="LibreOffice headless"),
    _b("pandoc", "pandoc-cli", "docs"),
    _b("typst", "typst", "docs"),
    _b("tectonic", "tectonic", "docs"),
    _b("xelatex", "texlive-xetex", "docs"),
    _b("weasyprint", "python-weasyprint", "docs"),
    _b("ebook-convert", "calibre", "docs"),
    _b("ebook-meta", "calibre", "docs"),
    _b("ebook-polish", "calibre", "docs"),
    _b("pygmentize", "python-pygments", "docs"),
    _b("dos2unix", "dos2unix", "docs"),
    _b("unix2dos", "dos2unix", "docs"),
    _b("uchardet", "uchardet", "docs"),
    _b("iconv", "glibc", "docs", repo="core"),
    _b("jq", "jq", "docs"),
    _b("yq", "go-yq", "docs"),
    _b("prettier", "prettier", "docs"),
    _b("ruff", "ruff", "docs"),
    _b("shfmt", "shfmt", "docs"),
    _b("shellcheck", "shellcheck", "docs"),
    _b("clang-format", "clang", "docs"),
    _b("tokei", "tokei", "docs"),
    _b("silicon", "silicon", "docs", repo="aur"),
    _b("markitdown", "python-markitdown", "docs", repo="aur"),
    _b("pdf2docx", "python-pdf2docx", "docs", repo="aur"),
    _b("epubcheck", "epubcheck", "docs", repo="aur"),
    _b("lp", "cups", "docs"),
    # ---------------------------------------------------------------- media
    _b("ffmpeg", "ffmpeg", "media"),
    _b("ffprobe", "ffmpeg", "media"),
    _b("whisper-cli", "whisper-cpp", "media", description="speech to text"),
    _b("whisper", "python-openai-whisper", "media"),
    _b("yt-dlp", "yt-dlp", "media"),
    _b("deno", "deno", "media"),
    _b("mkvmerge", "mkvtoolnix-cli", "media"),
    _b("mkvextract", "mkvtoolnix-cli", "media"),
    _b("sox", "sox", "media"),
    _b("mpv", "mpv", "media"),
    # ---------------------------------------------------------------- archives
    _b("7z", "7zip", "archive", binaries=("7z", "7zz", "7za")),
    _b("bsdtar", "libarchive", "archive", repo="core"),
    _b("tar", "tar", "archive", repo="core"),
    _b("unrar", "unrar", "archive"),
    _b("unar", "unarchiver", "archive"),
    _b("lsar", "unarchiver", "archive"),
    _b("zip", "zip", "archive"),
    _b("unzip", "unzip", "archive"),
    _b("zstd", "zstd", "archive", repo="core"),
    _b("xz", "xz", "archive", repo="core"),
    _b("gzip", "gzip", "archive", repo="core"),
    _b("pigz", "pigz", "archive"),
    _b("bzip2", "bzip2", "archive", repo="core"),
    _b("lz4", "lz4", "archive", repo="core"),
    _b("split", "coreutils", "archive", repo="core"),
    _b("unsquashfs", "squashfs-tools", "archive"),
    _b("rpm2cpio", "rpm-tools", "archive"),
    _b("dpkg-deb", "dpkg", "archive"),
    # ---------------------------------------------------------------- fonts
    _b("fc-cache", "fontconfig", "fonts"),
    _b("fc-list", "fontconfig", "fonts"),
    _b("fc-match", "fontconfig", "fonts"),
    _b("fc-query", "fontconfig", "fonts"),
    _b("fonttools", "python-fonttools", "fonts"),
    _b("pyftsubset", "python-fonttools", "fonts"),
    _b("woff2_compress", "woff2", "fonts"),
    # ---------------------------------------------------------------- system / desktop
    _b("wl-copy", "wl-clipboard", "system"),
    _b("wl-paste", "wl-clipboard", "system"),
    _b("notify-send", "libnotify", "system"),
    _b("gio", "glib2", "system", repo="core"),
    _b("gdbus", "glib2", "system", repo="core"),
    _b("xdg-open", "xdg-utils", "system"),
    _b("xdg-email", "xdg-utils", "system"),
    _b("xdg-mime", "xdg-utils", "system"),
    _b("gtk-launch", "gtk3", "system", binaries=("gtk-launch", "gtk4-launch")),
    _b("dex", "dex", "system"),
    _b("xdg-terminal-exec", "xdg-terminal-exec", "system"),
    _b("hyprctl", "hyprland", "system"),
    _b("qs", "quickshell", "system", binaries=("qs", "quickshell")),
    _b("caelestia", "caelestia-cli", "system", repo="aur"),
    _b("curl", "curl", "system", repo="core"),
    _b("wget", "wget", "system"),
    _b("gpg", "gnupg", "system", repo="core"),
    _b("age", "age", "system"),
    _b("kdeconnect-cli", "kdeconnect", "system"),
    _b("localsend-cli", "localsend-cli-bin", "system", repo="aur"),
    _b("localsend", "localsend-bin", "system", repo="aur", binaries=("localsend", "localsend_app")),
    _b("udisksctl", "udisks2", "system"),
    _b("trash-put", "trash-cli", "system"),
    _b("file", "file", "system", repo="core"),
    _b("sha256sum", "coreutils", "system", repo="core"),
    _b("b2sum", "coreutils", "system", repo="core"),
    _b("xxh128sum", "xxhash", "system"),
    _b("clamscan", "clamav", "system"),
    _b("pacman", "pacman", "system", repo="core"),
    _b("wine", "wine", "system"),
    _b("flatpak", "flatpak", "system"),
    _b("chmod", "coreutils", "system", repo="core"),
    _b("foot", "foot", "system"),
    _b("kitty", "kitty", "system"),
    _b("alacritty", "alacritty", "system"),
    _b("wezterm", "wezterm", "system"),
    _b("ghostty", "ghostty", "system"),
    _b("konsole", "konsole", "system"),
    _b("gnome-terminal", "gnome-terminal", "system"),
    _b("xterm", "xterm", "system"),
    _b("thunar", "thunar", "system", binaries=("thunar", "Thunar")),
    _b("nautilus", "nautilus", "system"),
    _b("dolphin", "dolphin", "system"),
    _b("nemo", "nemo", "system"),
    _b("paru", "paru", "system", repo="aur"),
    _b("yay", "yay", "system", repo="aur"),
    _b("pipx", "python-pipx", "system"),
    # ---------------------------------------------------------------- python modules
    _py("PIL", "python-pillow"),
    _py("PIL._avif", "python-pillow"),
    _py("pymupdf", "python-pymupdf"),
    _py("pypdf", "python-pypdf"),
    _py("pikepdf", "python-pikepdf"),
    _py("cryptography", "python-cryptography", description="AES support for pypdf"),
    _py("pillow_heif", "python-pillow-heif"),
    _py("pillow_jxl", "python-pillow-jpegxl-plugin"),
    _py("fontTools", "python-fonttools"),
    _py("brotli", "python-brotli"),
    _py("yaml", "python-yaml"),
    _py("tomli_w", "python-tomli-w"),
    _py("tomlkit", "python-tomlkit"),
    _py("openpyxl", "python-openpyxl"),
    _py("xlrd", "python-xlrd"),
    _py("odf", "python-odfpy"),
    _py("pandas", "python-pandas"),
    _py("pdf2docx", "python-pdf2docx", repo="aur"),
    _py("docx", "python-docx"),
    _py("mutagen", "python-mutagen"),
    _py("qrcode", "python-qrcode"),
    _py("pygments", "python-pygments"),
    _py("charset_normalizer", "python-charset-normalizer"),
    _py("py7zr", "python-py7zr"),
    _py("rarfile", "python-rarfile"),
    _py("send2trash", "python-send2trash"),
    _py("defusedxml", "python-defusedxml"),
    _py("markdown", "python-markdown"),
    _py("gi", "python-gobject"),
    _py("numpy", "python-numpy"),
    _py("cv2", "python-opencv"),
    _py("piexif", "python-piexif"),
    _py("cairosvg", "python-cairosvg"),
    _py("weasyprint", "python-weasyprint"),
    _py("vtracer", "python-vtracer", repo="aur"),
    _py("markitdown", "python-markitdown", repo="aur"),
    _py("rembg", "rembg", repo="pip", install="pipx install 'rembg[cpu,cli]'"),
    _py("onnxruntime", "python-onnxruntime-cpu"),
    _py("ruamel.yaml", "python-ruamel-yaml"),
]

#: name -> Tool
TOOLS: dict[str, Tool] = {t.name: t for t in _TOOL_LIST}

# Arch package names are case sensitive; aliases help users that type a package or binary name.
_ALIASES = {
    "imagemagick": "magick",
    "ghostscript": "gs",
    "libreoffice": "soffice",
    "7zip": "7z",
    "7zz": "7z",
    "realesrgan-ncnn-vulkan": "realesrgan",
    "waifu2x-ncnn-vulkan": "waifu2x",
    "exiftool.pl": "exiftool",
    "pillow": "py:PIL",
    "py:pillow": "py:PIL",
    "py:fitz": "py:pymupdf",
}


def _canonical(name: str) -> str:
    name = str(name).strip()
    return _ALIASES.get(name, name)


def _maybe_expire() -> None:
    global _stamp
    now = time.monotonic()
    if now - _stamp > _CACHE_TTL:
        with _stamp_lock:
            if now - _stamp > _CACHE_TTL:
                _stamp = now
                _lookup.cache_clear()


def refresh() -> None:
    """Forget every cached lookup (call after installing packages)."""
    global _stamp
    with _stamp_lock:
        _stamp = time.monotonic()
        _lookup.cache_clear()
    importlib.invalidate_caches()


def _is_exe(p: str) -> bool:
    try:
        return os.path.isfile(p) and os.access(p, os.X_OK)
    except (OSError, ValueError):
        return False


def _which(binary: str) -> str | None:
    found = shutil.which(binary)
    if found:
        return found
    extra = os.pathsep.join(d for d in EXTRA_DIRS if d and os.path.isdir(d))
    if extra:
        found = shutil.which(binary, path=extra)
        if found:
            return found
    return None


@lru_cache(maxsize=1024)
def _lookup(name: str) -> str | None:
    if name.startswith("py:"):
        module = name[3:]
        tool = TOOLS.get(name)
        if tool and tool.module:
            module = tool.module
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError, AttributeError):
            return None
        if spec is None:
            return None
        return spec.origin or f"py:{module}"
    tool = TOOLS.get(name)
    if tool is None:
        if os.path.isabs(name):
            return name if _is_exe(name) else None
        return _which(name)
    for candidate in tool.paths:
        if _is_exe(candidate):
            return candidate
    for binary in tool.binaries:
        if os.path.isabs(binary):
            if _is_exe(binary):
                return binary
            continue
        found = _which(binary)
        if found:
            return found
    return None


def path(name: str) -> str | None:
    """Return the absolute path of tool ``name`` (or the module origin for ``py:`` names).

    Unknown names are looked up as plain binaries in ``$PATH`` (plus :data:`EXTRA_DIRS`);
    absolute paths are returned when executable.  Returns ``None`` when not found.
    """
    if not name:
        return None
    _maybe_expire()
    return _lookup(_canonical(name))


def have(name: str) -> bool:
    """``True`` when tool ``name`` is available."""
    return path(name) is not None


def missing(requires: Iterable | str | None) -> list[str]:
    """Return the missing tools of a ``requires`` list.

    Items are tool names, ``"py:module"`` names, or tuples/lists of alternatives (any one is
    enough; when none is present the first alternative is reported).
    """
    if not requires:
        return []
    if isinstance(requires, str):
        requires = [requires]
    out: list[str] = []
    for item in requires:
        if not item:
            continue
        if isinstance(item, (tuple, list, set, frozenset)):
            alts = [str(a) for a in item if a]
            if alts and not any(have(a) for a in alts):
                name = _canonical(alts[0])
                if name not in out:
                    out.append(name)
            continue
        name = _canonical(str(item))
        if not have(name) and name not in out:
            out.append(name)
    return out


def _tool_for(name: str) -> Tool:
    name = _canonical(name)
    tool = TOOLS.get(name)
    if tool is not None:
        return tool
    if name.startswith("py:"):
        mod = name[3:]
        return Tool(name=name, packages=("python-" + mod.lower().replace("_", "-"),), repo="extra",
                    group="python", module=mod)
    base = os.path.basename(name)
    return Tool(name=name, binaries=(base,), packages=(base,), repo="extra", group="system")


def display_name(name: str) -> str:
    """Name to show to users: the binary for programs, the package for Python modules."""
    tool = _tool_for(name)
    if tool.name.startswith("py:"):
        return tool.packages[0] if tool.packages else tool.name[3:]
    return tool.binaries[0] if tool.binaries else tool.name


def packages_for(names: Iterable[str]) -> dict[str, list[str]]:
    """Group the packages that provide ``names`` by repository (``core``/``extra`` merged as ``repo``)."""
    groups: dict[str, list[str]] = {"repo": [], "aur": [], "pip": [], "pypi": [], "custom": []}
    for n in names:
        tool = _tool_for(n)
        if tool.install:
            if tool.install not in groups["custom"]:
                groups["custom"].append(tool.install)
            continue
        key = "repo" if tool.repo in ("core", "extra", "multilib") else tool.repo
        if key not in groups:
            key = "repo"
        for pkg in tool.packages:
            if pkg not in groups[key]:
                groups[key].append(pkg)
    return groups


def aur_helper() -> str:
    """The installed AUR helper (``paru`` preferred, then ``yay``); ``paru`` when none is installed."""
    for helper in ("paru", "yay"):
        if have(helper):
            return helper
    return "paru"


def install_hint(names: Iterable[str] | str | None) -> str:
    """Return a copy-pasteable command installing ``names``.

    Examples: ``sudo pacman -S --needed ffmpeg``, ``paru -S ocrmypdf``,
    ``pipx install 'rembg[cpu,cli]'``; several commands are joined with `` && ``.
    """
    if not names:
        return ""
    if isinstance(names, str):
        names = [names]
    groups = packages_for(names)
    cmds: list[str] = []
    if groups["repo"]:
        cmds.append("sudo pacman -S --needed " + " ".join(groups["repo"]))
    if groups["aur"]:
        cmds.append(f"{aur_helper()} -S " + " ".join(groups["aur"]))
    for pkg in groups["pip"]:
        cmds.append(f"pipx install {pkg}")
    if groups["pypi"]:
        cmds.append("python -m pip install --user --break-system-packages " + " ".join(groups["pypi"]))
    cmds.extend(groups["custom"])
    return " && ".join(cmds)


def report() -> dict:
    """Return a dependency report for ``dropwheel doctor`` / the ``doctor`` RPC.

    ``{"tools": [{name, path, available, packages, repo, group, install, description}],
    "available": int, "missing": [names], "install_missing": str, "python": str,
    "executable": str, "platform": str}``
    """
    refresh()
    rows = []
    missing_names = []
    for tool in sorted(TOOLS.values(), key=lambda t: (t.group, t.name.lower())):
        p = path(tool.name)
        if p is None:
            missing_names.append(tool.name)
        rows.append({
            "name": tool.name,
            "path": p,
            "available": p is not None,
            "packages": list(tool.packages),
            "repo": tool.repo,
            "group": tool.group,
            "install": "" if p else install_hint([tool.name]),
            "description": tool.description,
        })
    return {
        "tools": rows,
        "available": sum(1 for r in rows if r["available"]),
        "missing": missing_names,
        "install_missing": install_hint(missing_names),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
        "aur_helper": aur_helper(),
    }
