"""External dependency discovery and install hints (Arch, Debian and Ubuntu, Fedora, openSUSE, Windows).

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
    "display_name", "packages_for", "aur_helper", "WINDOWS", "MACOS", "distro", "qt_frontend",
    "extend_path",
]

#: Running on Windows: binary names, search dirs and install hints differ (see the ``_NT_*`` tables).
WINDOWS = os.name == "nt"
#: Running on macOS: Homebrew hints, ``pbcopy``/``open`` (see the ``_MAC_*`` tables).
MACOS = sys.platform == "darwin"

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


def _nt_extra_dirs() -> tuple[str, ...]:
    """Install folders of the Windows builds of the tools that do not add themselves to ``PATH``."""
    import glob

    program_files = ("*", "*/bin", "*/program", "gs/*/bin")
    local = ("Programs/*", "Programs/*/bin", "Microsoft/WinGet/Links")  # not "*": that would take in Temp
    out: list[str] = []
    for var, patterns in (("ProgramFiles", program_files), ("ProgramFiles(x86)", program_files),
                          ("LOCALAPPDATA", local)):
        root = os.environ.get(var, "")
        if root and os.path.isabs(root):
            for pat in patterns:
                out += sorted(glob.glob(os.path.join(root, pat)), reverse=True)  # newest versioned folder first
    return tuple(out)


if WINDOWS:
    EXTRA_DIRS = _nt_extra_dirs()
elif MACOS:
    # a LaunchAgent or a Quick Action runs with PATH=/usr/bin:/bin:/usr/sbin:/sbin: add Homebrew
    # (Apple Silicon, then Intel) and the app bundles that ship a command line
    EXTRA_DIRS = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/local/sbin",
                  "/Applications/LibreOffice.app/Contents/MacOS", "/Applications/Inkscape.app/Contents/MacOS",
                  "/Applications/calibre.app/Contents/MacOS", os.path.expanduser("~/.local/bin"),
                  "/usr/bin", "/bin", "/usr/sbin")


def _os_ids() -> set[str]:
    """ID and ID_LIKE of /etc/os-release (``DROPWHEEL_OS_RELEASE`` for the tests, as in install.sh)."""
    try:
        with open(os.environ.get("DROPWHEEL_OS_RELEASE") or "/etc/os-release", encoding="utf-8") as fh:
            fields = dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)
    except OSError:
        return set()
    return set((fields.get("ID", "") + " " + fields.get("ID_LIKE", "")).replace('"', "").lower().split())


@lru_cache(maxsize=1)
def distro() -> str:
    """``"debian"`` for Debian, Ubuntu and their derivatives (apt), ``"fedora"`` for Fedora, RHEL and
    their derivatives (dnf), ``"suse"`` for openSUSE and SLE (zypper), otherwise ``"arch"``."""
    ids = _os_ids()
    if {"debian", "ubuntu"} & ids:
        return "debian"
    if {"fedora", "rhel", "centos"} & ids:
        return "fedora"
    return "suse" if any("suse" in i for i in ids) else "arch"  # opensuse-tumbleweed, opensuse-leap, sles


def qt_frontend() -> bool:
    """The wheel runs in the PySide6 host (``dropwheel.qtshell``) rather than Quickshell: on
    Windows, macOS, and in a GNOME or KDE Plasma session without Hyprland.  ``DROPWHEEL_FRONTEND=qt|quickshell``
    forces it."""
    if WINDOWS or MACOS:
        return True
    forced = os.environ.get("DROPWHEEL_FRONTEND", "").strip().lower()
    if forced in ("qt", "quickshell"):
        return forced == "qt"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return False
    return bool({"gnome", "kde"} & set(os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")))

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
    _b("gs", "ghostscript", "pdf", binaries=("gs", "gswin64c", "gswin32c"), description="Ghostscript"),
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
# the Qt front end (dropwheel.qtshell) on Windows, macOS, GNOME and KDE; on Hyprland Quickshell hosts the shell
_TOOL_LIST.append(_py("PySide6", "pyside6", description="Qt for Python: the wheel on Windows, macOS, GNOME and KDE"))

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


#: Windows: programs the Linux names stand for (clipboard via PowerShell, opening via Explorer).
#: ``util`` checks :data:`WINDOWS` before running any of these names, so they never run with Linux arguments.
_NT_PROVIDED = {"wl-copy": "powershell", "wl-paste": "powershell", "xdg-open": "explorer"}

#: Windows: names that would find an unrelated System32 program (``convert.exe`` converts FAT to NTFS).
_NT_SHADOWED = {"convert"}

#: Windows: winget package ids (checked in the winget-pkgs index) for the tools that have one.
_NT_WINGET = {
    "ffmpeg": "Gyan.FFmpeg", "ffprobe": "Gyan.FFmpeg", "magick": "ImageMagick.ImageMagick",
    "exiftool": "OliverBetz.ExifTool", "soffice": "TheDocumentFoundation.LibreOffice",
    "pandoc": "JohnMacFarlane.Pandoc", "qpdf": "QPDF.QPDF", "tesseract": "UB-Mannheim.TesseractOCR",
    "7z": "7zip.7zip", "yt-dlp": "yt-dlp.yt-dlp",
    **dict.fromkeys(("pdftoppm", "pdftocairo", "pdftotext", "pdfimages", "pdfinfo", "pdfunite", "pdfseparate",
                     "pdfattach", "pdfdetach"), "oschwartz10612.Poppler"),
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


#: Windows: only real executables.  ``.cmd``/``.bat`` shims (npm's svgo, ``code.cmd``) run through
#: cmd.exe, which reads ``&`` or ``%`` in a file name as syntax.
_NT_EXE_EXTS = (".exe", ".com")

#: Windows programs taken from ``%SystemRoot%`` only, never from ``PATH`` or the current folder.
_NT_SYSTEM = {"powershell": r"System32\WindowsPowerShell\v1.0\powershell.exe", "cmd": r"System32\cmd.exe",
              "taskkill": r"System32\taskkill.exe", "explorer": "explorer.exe", "tar": r"System32\tar.exe"}


def _is_exe(p: str) -> bool:
    try:
        if WINDOWS and not p.lower().endswith(_NT_EXE_EXTS):
            return False
        return os.path.isfile(p) and os.access(p, os.X_OK)
    except (OSError, ValueError):
        return False


def _nt_which(binary: str) -> str | None:
    """Windows lookup: absolute ``PATH`` entries and :data:`EXTRA_DIRS` only.

    ``shutil.which`` would try the current folder first, so a ``magick.exe`` lying next to the
    files being converted would run.
    """
    if binary in _NT_SYSTEM:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        candidate = os.path.join(root, _NT_SYSTEM[binary])
        return candidate if _is_exe(candidate) else None
    names = [binary] if binary.lower().endswith(_NT_EXE_EXTS) else [binary + ext for ext in _NT_EXE_EXTS]
    for d in [*os.environ.get("PATH", "").split(os.pathsep), *EXTRA_DIRS]:
        d = d.strip().strip('"')
        if not d or not os.path.isabs(d):
            continue
        for name in names:
            candidate = os.path.join(d, name)
            if _is_exe(candidate):
                return candidate
    return None


def extend_path() -> None:
    """macOS: append :data:`EXTRA_DIRS` to ``PATH`` (launchd's is /usr/bin:/bin:/usr/sbin:/sbin), so the
    tools Dropwheel starts find the ones they start in turn (ocrmypdf → tesseract, yt-dlp → ffmpeg)."""
    if not MACOS:
        return
    parts = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    os.environ["PATH"] = os.pathsep.join(parts + [d for d in EXTRA_DIRS if d not in parts])


def _which(binary: str) -> str | None:
    if WINDOWS:
        return _nt_which(binary)
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
    if WINDOWS:
        if name in _NT_SHADOWED:
            return None
        if name in _NT_PROVIDED:
            return _which(_NT_PROVIDED[name])
    if MACOS and name in _MAC_PROVIDED:
        return _which(_MAC_PROVIDED[name])
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
    if WINDOWS:
        return _nt_install_hint(names)
    if MACOS:
        return _mac_install_hint(names)
    system = distro()
    if system in _NATIVE:
        install, table = _NATIVE[system]
        if system == "fedora" and have("dnf5"):
            install += " --skip-unavailable"  # dnf5 (Fedora 41+); dnf 4 (RHEL) has no such install option
        if system == "suse" and not any(r in i for i in _os_ids() for r in _SUSE_ROLLING):
            table = _ZYPPER_LEAP
        return _deb_install_hint(names, install, table)
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


#: Windows: pip distributions whose name is not the Arch package minus ``python-``.
_NT_PIP = {"PySide6": "PySide6-Essentials",
           "PIL": "pillow", "PIL._avif": "pillow", "pillow_jxl": "pillow-jxl-plugin", "docx": "python-docx",
           "yaml": "PyYAML", "cv2": "opencv-python", "gi": "PyGObject", "onnxruntime": "onnxruntime",
           "odf": "odfpy", "ruamel.yaml": "ruamel.yaml"}

#: Debian and Ubuntu: apt packages whose name is not the Arch one; ``""`` = not packaged there.
#: shortcut: written from the Debian 13 / Ubuntu 24.04 names, not checked against every release;
#: install.sh skips what apt does not know.
_DEB = {
    "glib2": "libglib2.0-bin", "glibc": "libc-bin", "gtk3": "libgtk-3-bin", "libarchive": "libarchive-tools",
    "libavif": "libavif-bin", "libheif": "libheif-examples", "libjpeg-turbo": "libjpeg-turbo-progs",
    "libjxl": "libjxl-tools", "libnotify": "libnotify-bin", "libreoffice-fresh": "libreoffice",
    "librsvg": "librsvg2-bin", "libvips": "libvips-tools", "libwebp-utils": "webp", "mkvtoolnix-cli": "mkvtoolnix",
    "pandoc-cli": "pandoc", "pdftk": "pdftk-java", "perl-image-exiftool": "libimage-exiftool-perl",
    "poppler": "poppler-utils", "python-fonttools": "fonttools", "python-pipx": "pipx",
    "python-pygments": "python3-pygments", "python-weasyprint": "weasyprint", "rpm-tools": "rpm2cpio",
    "tesseract": "tesseract-ocr", "tesseract-data-eng": "tesseract-ocr-eng", "tesseract-data-ita": "tesseract-ocr-ita",
    "xz": "xz-utils", "zbar": "zbar-tools", "go-yq": "yq", "whisper-cpp": "", "pacman": "", "paru": "", "yay": "",
    "caelestia-cli": "", "deno": "", "ghostty": "", "prettier": "", "svgo": "", "upscayl-bin": "",
    "realesrgan-ncnn-vulkan-bin": "", "waifu2x-ncnn-vulkan": "", "vtracer": "", "localsend-bin": "",
    "localsend-cli-bin": "", "python-markitdown": "", "python-openai-whisper": "", "python-pdf2docx": "",
}


#: Arch packages that no Fedora or openSUSE repository has (AUR, Arch-only or not packaged there).
_NO_RPM = dict.fromkeys((
    "caelestia-cli", "epubcheck", "gifski", "localsend-bin", "localsend-cli-bin", "pacman", "paru", "yay",
    "prettier", "python-markitdown", "python-openai-whisper", "python-pdf2docx", "realesrgan-ncnn-vulkan-bin",
    "satty", "silicon", "svgo", "upscayl-bin", "vtracer", "waifu2x-ncnn-vulkan", "whisper-cpp"), "")

#: Fedora (and RHEL derivatives): dnf packages whose name is not the Arch one; ``""`` = not packaged.
#: Checked against the Fedora 44 repository (binaries in /usr/bin); "py:" keys are Python modules
#: that dnf provides.  ffmpeg-free is Fedora's own build: RPM Fusion's "ffmpeg" replaces it.
#: A key that is a tool name (not one of its packages) overrides that tool's packages.
_DNF = {
    **_NO_RPM,
    "clang": "clang-tools-extra", "cups": "cups-client", "ffmpeg": "ffmpeg-free", "glibc": "glibc-common",
    "gnupg": "gnupg2", "go-yq": "yq", "imagemagick": "ImageMagick", "img2pdf": "python3-img2pdf",
    "kdeconnect": "kde-connect", "libarchive": "bsdtar", "libavif": "libavif-tools", "libheif": "libheif-tools",
    "libjpeg-turbo": "libjpeg-turbo-utils", "libjxl": "libjxl-utils", "libreoffice-fresh": "libreoffice",
    "librsvg": "librsvg2-tools", "libvips": "vips-tools", "libwebp-utils": "libwebp-tools",
    "mkvtoolnix-cli": "mkvtoolnix", "mupdf-tools": "mupdf", "pdftk": "pdftk-java",
    "perl-image-exiftool": "perl-Image-ExifTool", "poppler": "poppler-utils", "python-fonttools": "fonttools",
    "python-pipx": "pipx", "python-pygments": "python3-pygments", "python-weasyprint": "weasyprint",
    "rpm-tools": "rpm", "scour": "python3-scour", "shellcheck": "ShellCheck",
    "tesseract-data-eng": "tesseract-langpack-eng", "tesseract-data-ita": "tesseract-langpack-ita",
    "thunar": "Thunar", "unarchiver": "unar", "woff2": "woff2-tools",
    "deno": "", "dex": "", "ghostty": "", "hyprland": "", "resvg": "", "swww": "", "tectonic": "", "typst": "",
    "wezterm": "", "quickshell": "",  # not in Fedora 43; the Qt host desktops do not need it
    "py:PIL": "python3-pillow", "py:PySide6": "python3-pyside6", "py:pymupdf": "python3-PyMuPDF",
    "py:pypdf": "python3-pypdf", "py:pikepdf": "python3-pikepdf", "py:pillow_jxl": "python3-pillow-jxl-plugin",
    "py:fontTools": "python3-fonttools", "py:brotli": "python3-brotli", "py:yaml": "python3-pyyaml",
    "py:tomli_w": "python3-tomli-w", "py:openpyxl": "python3-openpyxl", "py:mutagen": "python3-mutagen",
    "py:qrcode": "python3-qrcode", "py:pygments": "python3-pygments",
    "py:charset_normalizer": "python3-charset-normalizer", "py:rarfile": "python3-rarfile",
    "py:send2trash": "python3-send2trash", "py:defusedxml": "python3-defusedxml",
}

#: openSUSE Tumbleweed: zypper packages (or capabilities) whose name is not the Arch one; ``""`` = not
#: packaged.  Checked against the Tumbleweed repository.  QML modules are a package of their own there;
#: the Python modules are those packaged in Tumbleweed and Leap 16.0 alike.
_ZYPPER = {
    **_NO_RPM,
    "cups": "cups-client", "glib2": "glib2-tools", "gnupg": "gpg2", "go-yq": "yq", "gtk3": "gtk3-tools",
    "imagemagick": "ImageMagick", "img2pdf": "python3-img2pdf", "kdeconnect": "kdeconnect-kde",
    "libarchive": "bsdtar", "libavif": "avif-tools", "libheif": "", "libjxl": "libjxl-tools",
    "libnotify": "libnotify-tools", "libreoffice-fresh": "libreoffice", "librsvg": "rsvg-convert",
    "libvips": "vips-tools", "libwebp-utils": "libwebp-tools", "mat2": "", "mkvtoolnix-cli": "mkvtoolnix",
    "mupdf-tools": "mupdf", "ocrmypdf": "python3-ocrmypdf", "perl-image-exiftool": "exiftool",
    "poppler": "poppler-tools", "python-fonttools": "python3-FontTools", "python-pipx": "python3-pipx",
    "python-pygments": "python3-Pygments", "python-weasyprint": "python3-weasyprint", "rpm-tools": "rpm",
    "ruff": "python3-ruff", "scour": "python3-scour", "shellcheck": "ShellCheck", "squashfs-tools": "squashfs",
    "tesseract": "tesseract-ocr", "tesseract-data-eng": "tesseract-ocr-traineddata-eng",
    "tesseract-data-ita": "tesseract-ocr-traineddata-ita", "unarchiver": "unar", "tectonic": "",
    "gif2webp": "",  # a tool: libwebp-tools has no gif2webp there
    "py:PIL": "python3-Pillow", "py:PySide6": "python3-pyside6 qt6-declarative-imports",
    "py:pymupdf": "python3-PyMuPDF", "py:pypdf": "python3-pypdf", "py:pikepdf": "python3-pikepdf",
    "py:pillow_heif": "python3-pillow-heif", "py:fontTools": "python3-FontTools", "py:brotli": "python3-Brotli",
    "py:yaml": "python3-PyYAML", "py:tomli_w": "python3-tomli-w", "py:mutagen": "python3-mutagen",
    "py:qrcode": "python3-qrcode", "py:pygments": "python3-Pygments",
    "py:charset_normalizer": "python3-charset-normalizer", "py:py7zr": "python3-py7zr",
    "py:rarfile": "python3-rarfile", "py:send2trash": "python3-Send2Trash", "py:defusedxml": "python3-defusedxml",
}

#: openSUSE Leap (and SLE): Tumbleweed's table minus what Leap 16.0 does not package.
_ZYPPER_LEAP = {
    **_ZYPPER, "tectonic": "tectonic",
    **dict.fromkeys(("darktable", "deno", "dex", "ghostty", "hyprland", "img2pdf", "libvips", "ocrmypdf",
                     "quickshell", "resvg", "trash-cli"), ""),
}

#: os-release ids of the rolling openSUSE systems, which use the Tumbleweed repository.
_SUSE_ROLLING = ("tumbleweed", "microos", "aeon", "kalpa", "slowroll")

#: Linux systems other than Arch: (install command, Arch → native package names).
_NATIVE = {"debian": ("sudo apt install", _DEB), "fedora": ("sudo dnf install", _DNF),
           "suse": ("sudo zypper install", _ZYPPER)}


def _pip_name(tool: Tool) -> str:
    module = tool.module or tool.name[3:]
    return _NT_PIP.get(module) or tool.packages[0].removeprefix("python-")


def _deb_install_hint(names: Iterable[str], install: str = "sudo apt install", table: dict = _DEB) -> str:
    """Debian and Ubuntu, Fedora, openSUSE: ``install`` (``sudo apt install``…) for programs and for the
    Python modules ``table`` lists, pip for the other modules (in Dropwheel's own environment when it
    has one)."""
    apt: list[str] = []
    pip: list[str] = []
    custom: list[str] = []
    for n in names:
        tool = _tool_for(n)
        if tool.install:
            custom.append(tool.install)
        elif tool.name in table and tool.name not in tool.packages:  # py:PIL, gif2webp
            apt += table[tool.name].split()
        elif tool.name.startswith("py:"):
            pip.append(_pip_name(tool))
        else:
            apt += [p for pkg in tool.packages for p in table.get(pkg, pkg).split()]
    cmds = []
    if apt:
        cmds.append(f"{install} " + " ".join(dict.fromkeys(apt)))
    if pip:
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        python = sys.executable if in_venv else "python3"
        extra = "" if in_venv else " --user --break-system-packages"
        cmds.append(f"{python} -m pip install{extra} " + " ".join(dict.fromkeys(pip)))
    cmds += list(dict.fromkeys(custom))
    return " && ".join(cmds)


#: Windows: ``winget search`` terms clearer than the binary name.
_NT_SEARCH = {"gs": "Ghostscript"}


def _nt_install_hint(names: Iterable[str]) -> str:
    """Windows: ``winget install`` for known ids, ``pip install`` for modules, ``winget search`` otherwise."""
    winget: list[str] = []
    pip: list[str] = []
    other: list[str] = []
    for n in names:
        tool = _tool_for(n)
        if tool.name in _NT_WINGET:
            winget.append(_NT_WINGET[tool.name])
        elif tool.name.startswith("py:"):
            pip.append(_pip_name(tool))
        else:
            other.append(_NT_SEARCH.get(tool.name) or display_name(tool.name))
    cmds = [f"winget install --id {i} -e" for i in dict.fromkeys(winget)]
    if pip:
        cmds.append("python -m pip install " + " ".join(dict.fromkeys(pip)))
    cmds += [f"winget search {o}" for o in dict.fromkeys(other)]
    return "; ".join(cmds)  # Windows PowerShell 5.1 has no "&&"


#: macOS: programs the Linux names stand for.  ``util`` checks :data:`MACOS` before running any of
#: these names, so they never run with Linux arguments.
_MAC_PROVIDED = {"wl-copy": "pbcopy", "wl-paste": "pbpaste", "xdg-open": "open"}

#: macOS: Homebrew names (checked on formulae.brew.sh in 2026-11) for the tools that have one;
#: ``--cask`` marks an app.  Tesseract's Italian data is the ``tesseract-lang`` formula.
_MAC_BREW = {
    "ffmpeg": "ffmpeg", "ffprobe": "ffmpeg", "magick": "imagemagick", "convert": "imagemagick",
    "exiftool": "exiftool", "qpdf": "qpdf", "tesseract": "tesseract", "pandoc": "pandoc", "7z": "sevenzip",
    "yt-dlp": "yt-dlp", "gs": "ghostscript", "qrencode": "qrencode", "soffice": "--cask libreoffice",
    "zbarimg": "zbar", "rsvg-convert": "librsvg", "resvg": "resvg", "vips": "vips", "potrace": "potrace",
    "oxipng": "oxipng", "pngquant": "pngquant", "optipng": "optipng", "jpegoptim": "jpegoptim",
    "jpegtran": "jpeg-turbo", "gifski": "gifski", "gifsicle": "gifsicle", "exiv2": "exiv2", "mat2": "mat2",
    "img2pdf": "img2pdf", "mutool": "mupdf-tools", "ocrmypdf": "ocrmypdf", "pdftk": "pdftk-java",
    "unpaper": "unpaper", "typst": "typst", "tectonic": "tectonic", "jq": "jq", "yq": "yq", "unar": "unar",
    "lsar": "unar", "whisper-cli": "whisper.cpp", "mkvmerge": "mkvtoolnix", "mkvextract": "mkvtoolnix",
    "sox": "sox", "zstd": "zstd", "pigz": "pigz", "lz4": "lz4", "inkscape": "--cask inkscape",
    "ebook-convert": "--cask calibre", "ebook-meta": "--cask calibre", "gpg": "gnupg", "woff2_compress": "woff2",
    **dict.fromkeys(("cwebp", "dwebp", "gif2webp", "img2webp", "webpmux"), "webp"),
    **dict.fromkeys(("avifenc", "avifdec"), "libavif"),
    **dict.fromkeys(("heif-enc", "heif-dec"), "libheif"),
    **dict.fromkeys(("cjxl", "djxl"), "jpeg-xl"),
    **dict.fromkeys(("pdftoppm", "pdftocairo", "pdftotext", "pdfimages", "pdfinfo", "pdfunite", "pdfseparate",
                     "pdfattach", "pdfdetach"), "poppler"),
}


def _mac_install_hint(names: Iterable[str]) -> str:
    """macOS: ``brew install`` for known formulae, pip for Python modules (in Dropwheel's own
    environment when it has one), ``brew search`` otherwise."""
    brew: list[str] = []
    casks: list[str] = []
    pip: list[str] = []
    other: list[str] = []
    for n in names:
        tool = _tool_for(n)
        formula = _MAC_BREW.get(tool.name, "")
        if formula.startswith("--cask "):
            casks.append(formula.removeprefix("--cask "))
        elif formula:
            brew += [formula, "tesseract-lang"] if formula == "tesseract" else [formula]
        elif tool.name.startswith("py:"):
            pip.append(_pip_name(tool))
        else:
            other.append(tool.install or "brew search " + display_name(tool.name))
    cmds = []
    if brew:
        cmds.append("brew install " + " ".join(dict.fromkeys(brew)))
    if casks:
        cmds.append("brew install --cask " + " ".join(dict.fromkeys(casks)))
    if pip:
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        cmds.append(f"{sys.executable if in_venv else 'python3'} -m pip install " + " ".join(dict.fromkeys(pip)))
    return " && ".join([*cmds, *dict.fromkeys(other)])


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
