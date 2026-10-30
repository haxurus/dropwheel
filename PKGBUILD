# Maintainer: Haxurus
#
# Builds Dropwheel from THIS checkout:
#
#     cd /path/to/dropwheel && makepkg -si      # first install
#     git pull && makepkg -sif                  # update or rebuild
#
# The -f matters: pkgver stays the same between commits, so without it makepkg finds the
# package it built last time ("A package has already been built") and installs that one again.
#
# There is no download: source=() is empty and prepare() copies the working tree (including
# uncommitted changes) from $startdir into $srcdir. To build a published release instead,
# comment out the empty source=() below and use the git variant:
#
#     source=("$pkgname::git+https://github.com/haxurus/dropwheel.git#tag=v$pkgver")
#     sha256sums=('SKIP')
#     makedepends+=(git)
#
# prepare() notices the git clone in "$srcdir/$pkgname" and skips the copy; build(),
# check() and package() are the same for both variants.
#
# Why BUILDDIR is redirected: by default makepkg uses "$startdir/src" as $srcdir, and in this
# repository src/ holds the Python sources, so "makepkg -C" or "makepkg -c" would delete
# them. Unless you chose another BUILDDIR (makepkg.conf or environment), makepkg works in
# .makepkg/ inside the checkout instead. makepkg derives $srcdir from BUILDDIR after sourcing
# the PKGBUILD; prepare() refuses to run if that ever stops being true.
if [[ -n ${startdir:-} && ( -z ${BUILDDIR:-} || ${BUILDDIR:-} -ef ${startdir:-} ) ]] \
   && (BUILDDIR=probe) 2>/dev/null; then
  BUILDDIR="$startdir/.makepkg"
fi

pkgname=dropwheel
pkgver=1.0.0
pkgrel=1
pkgdesc='Radial drag+Shift file-action wheel for Hyprland and Caelestia (Quickshell)'
arch=('any')
url='https://github.com/haxurus/dropwheel'
license=('MIT')
depends=(
  'python'
  'python-pillow'
  'quickshell'                       # quickshell-git (used by Caelestia) provides it too
  'hyprland'
  'wl-clipboard'
  'libnotify'
  'ttf-material-symbols-variable'
  'glib2'
  'xdg-utils'
)
makedepends=(
  'python-build'
  'python-installer'
  'python-setuptools'
  'python-wheel'
)
checkdepends=('python-pytest')
optdepends=(
  # Images
  'python-pillow-heif: HEIC/HEIF photos (iPhone)'
  'python-pillow-jpegxl-plugin: JPEG XL images'
  'imagemagick: fallback for exotic image formats (PSD, TGA, EPS, RAW previews)'
  'libvips: fast, low-memory processing of very large images'
  'libheif: HEIC/HEIF support for ImageMagick'
  'libwebp-utils: animated WebP (gif2webp, cwebp)'
  'librsvg: SVG rendering (rsvg-convert)'
  'resvg: high-fidelity SVG rendering'
  'inkscape: SVG text to paths and EPS/PDF export'
  'potrace: trace images to black-and-white SVG'
  'scour: SVG optimisation'
  'svgo: SVG optimisation (alternative to scour)'
  'oxipng: lossless PNG optimisation'
  'pngquant: lossy PNG compression'
  'jpegoptim: JPEG optimisation and compression to a target size'
  'libjpeg-turbo: lossless JPEG crop and rotation (jpegtran)'
  'gifski: high-quality GIFs from videos'
  'gifsicle: GIF compression and editing'
  'waifu2x-ncnn-vulkan: AI image upscaling (Vulkan GPU)'
  'python-onnxruntime-cpu: runtime for background removal with rembg (rembg itself via pipx)'
  # Metadata and privacy
  'perl-image-exiftool: show, edit and strip metadata'
  'mat2: strip metadata from documents and media'
  # OCR, QR codes and barcodes
  'tesseract: OCR (text recognition)'
  'tesseract-data-ita: Italian OCR language'
  'tesseract-data-eng: English OCR language'
  'zbar: read QR codes and barcodes'
  'python-qrcode: create QR codes'
  'qrencode: create QR codes (command line)'
  # PDF
  'python-pymupdf: PDF previews, redaction, page organiser, search, text extraction'
  'python-pypdf: merge, split, rotate and protect PDFs'
  'python-pikepdf: PDF repair, metadata cleanup and encryption'
  'python-cryptography: open and protect AES-256 encrypted PDFs with pypdf'
  'qpdf: lossless PDF compression, encryption, splitting and linearisation'
  'ghostscript: PDF compression, grayscale and PDF/A'
  'poppler: PDF to images and text (pdftoppm, pdftotext, pdfimages)'
  'mupdf-tools: PDF repair and rendering (mutool)'
  'img2pdf: lossless images to PDF'
  'xournalpp: annotate and sign PDFs'
  # Office documents, Markdown, text and data
  'libreoffice-fresh: convert office documents, spreadsheets and presentations (or libreoffice-still)'
  'pandoc-cli: convert Markdown and text documents'
  'typst: PDF engine for Markdown to PDF'
  'python-weasyprint: HTML/Markdown to PDF fallback'
  'calibre: ebook conversion and metadata (ebook-convert)'
  'python-openpyxl: Excel spreadsheets'
  'python-docx: Word (.docx) document support'
  'python-defusedxml: safe parsing of XML and SVG files'
  'python-charset-normalizer: text encoding detection and conversion'
  'python-pygments: syntax-highlighted code export'
  'python-yaml: YAML conversion'
  'python-tomli-w: write TOML files'
  # Audio and video
  'ffmpeg: video and audio conversion, compression, trimming and GIFs'
  'python-mutagen: audio tags and cover art'
  'whisper-cpp: speech to text, subtitles (whisper-cli)'
  'yt-dlp: download videos and audio from links'
  'deno: JavaScript runtime that yt-dlp needs for YouTube'
  # Archives
  '7zip: 7z archives, encrypted ZIP and RAR extraction'
  'libarchive: extract TAR, ISO, RPM and CPIO archives (bsdtar)'
  'unrar: RAR extraction'
  'unarchiver: smart extraction with file name encoding detection (unar)'
  'python-py7zr: 7z support without 7zip'
  'python-rarfile: RAR listing'
  'squashfs-tools: inspect and extract AppImages without running them'
  # Fonts
  'python-fonttools: font conversion, subsetting and info'
  'python-brotli: WOFF2 web fonts'
  # Annotation
  'satty: annotate images'
  'swappy: annotate images (alternative to satty)'
  # Sharing and system
  'kdeconnect: send files to a paired phone'
  'gnupg: encrypt and decrypt files'
  'udisks2: mount ISO and disk images'
  'trash-cli: move files to the trash when gio is not enough'
  'zenity: folder pickers for "Move to" and "Copy to"'
  'hyprpicker: colour picker eyedropper in the dialogs'
  'gtk3: "Open with" another application (gtk-launch)'
  'xdg-terminal-exec: open a terminal in a folder'
  'cups: print images and PDFs (lp)'
  'xxhash: XXH128 checksums'
  'chromium: save a web page as PDF'
)
install=dropwheel.install
# Local build from the checkout (see the comment at the top for the git variant).
source=()
sha256sums=()

# Directory holding the sources to build: the git clone (git variant) or the copy that
# prepare() makes of the local checkout.
_srcroot() {
  printf '%s\n' "$srcdir/$pkgname"
}

prepare() {
  local root
  root=$(_srcroot)

  # Git variant: makepkg already cloned the sources.
  if [[ -d $root/.git ]]; then
    return 0
  fi

  # Local variant: copy the checkout. Refuse to run if makepkg ignored the BUILDDIR
  # redirect above, because $srcdir would then be the repository's own src/ directory.
  if [[ $srcdir -ef $startdir/src ]]; then
    error "\$srcdir is the repository's src/ directory; run: BUILDDIR=\"\$PWD/.makepkg\" makepkg -si"
    return 1
  fi
  if [[ ! -f $startdir/pyproject.toml || ! -d $startdir/src/dropwheel ]]; then
    error "run makepkg from the root of the Dropwheel checkout"
    return 1
  fi

  rm -rf "$root"
  mkdir -p "$root"
  local item
  for item in pyproject.toml setup.cfg setup.py MANIFEST.in README.md README.it.md LICENSE Makefile \
              PKGBUILD dropwheel.install install.sh uninstall.sh \
              src shell integrations assets docs tests; do
    if [[ -e $startdir/$item ]]; then
      cp -a "$startdir/$item" "$root/"
    fi
  done
  # Drop byte-code and build leftovers that came with the copy.
  find "$root" -depth \( -name __pycache__ -o -name '*.egg-info' -o -name .pytest_cache \) \
    -exec rm -rf {} +
}

build() {
  cd "$(_srcroot)"
  make wheel PYTHON=python WHEELDIR="$srcdir/dist"
}

check() {
  cd "$(_srcroot)"
  if [[ -d tests ]]; then
    # "make test" hides the session from the tests (they would overwrite the clipboard).
    make test PYTHON=python
  fi
}

package() {
  cd "$(_srcroot)"
  make install PYTHON=python DESTDIR="$pkgdir" PREFIX=/usr WHEELDIR="$srcdir/dist"
}
