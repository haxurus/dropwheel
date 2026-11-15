[Documentation](README.md) · [Italiano](INSTALL.it.md)

# Installing Dropwheel

This page covers Arch Linux with Hyprland. For GNOME on Debian, Ubuntu, Fedora, openSUSE or Arch see
[Dropwheel on GNOME](GNOME.md); for Windows see [Dropwheel on Windows](WINDOWS.md); for macOS see
[Dropwheel on macOS](MACOS.md).

## Quick start

**Requirements**

| | |
|---|---|
| **System** | Arch Linux or a derivative, on Wayland |
| **Hyprland** | 0.57 or later, with Lua configuration |
| **Quickshell** | 0.3.2 or later (`quickshell` or `quickshell-git`) |
| **Python** | 3.11 or later, with Pillow |
| **Other packages** | `wl-clipboard`, `libnotify`, `ttf-material-symbols-variable`, `glib2`, `xdg-utils` |
| **Caelestia** | Recommended, not required |

**Install as an Arch package (recommended)**

```sh
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
makepkg -si
```

The `PKGBUILD` builds from the folder you are in, and pacman tracks every file.

**Or install into your home directory, without root**

```sh
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
./install.sh
```

The script installs under `~/.local` and asks before every step: the missing packages (installed with
pacman, sudo asks for your password once), the groups of optional features, the connection to Hyprland.
Without a terminal, or with `--no-packages`, it only prints the commands. Without cloning first:
`curl -fsSL https://raw.githubusercontent.com/haxurus/dropwheel/main/install.sh | bash`. See
`./install.sh --help` for the options.

**Then, as your normal user, connect it to Hyprland**

```sh
dropwheel setup hyprland   # Shift, SUPER+ALT+D, layer rules and autostart
hyprctl reload
dropwheel start            # from the next login it starts by itself
dropwheel doctor           # shows what is installed and what is missing
```

Now start dragging a file and tap <kbd>Shift</kbd>.

<details>
<summary><b>What <code>dropwheel setup hyprland</code> changes</b></summary>

<br>

With Caelestia, it copies the Lua module to `~/.config/caelestia/dropwheel.lua` and, after making a
backup, appends one line to `~/.config/caelestia/hypr-user.lua`:

```lua
require("dropwheel")
```

The module registers:

| What | Details |
|---|---|
| `Shift_L`, `Shift_R` | `hl.dsp.global("dropwheel:shift")`, `non_consuming` so applications still receive Shift, `ignore_mods` so it also works while holding Ctrl or Alt |
| `SUPER + ALT + D` | `hl.dsp.global("dropwheel:open")`, opens the wheel with the files on the clipboard |
| Layer rules | Blur and no Hyprland animation on `dropwheel-wheel` and `dropwheel-hud`; no animation on `dropwheel-dialog` and `dropwheel-hotzone` |
| Autostart | `hl.on("hyprland.start", ...)` runs `dropwheel start` |

The options are at the top of the copied file: the open shortcut, the Shift keys, autostart and blur.

Without Caelestia, copy the module to `~/.config/hypr/` and add this to
`~/.config/hypr/hyprland.lua`:

```lua
package.path = package.path .. ";" .. os.getenv("HOME") .. "/.config/hypr/?.lua"
require("dropwheel")
```

</details>

**Updating**

```sh
git pull
makepkg -sif               # or: ./install.sh
dropwheel stop && dropwheel start
```

The `f` matters: the version does not change on every commit, so without it makepkg reinstalls the
package it built last time.

## Optional dependencies

Only the [requirements](#quick-start) are mandatory. Everything else enables individual features, and
`dropwheel doctor` shows what is missing. Dropwheel never runs `sudo` for you.

A good base that covers most features:

```sh
sudo pacman -S --needed python-pillow-heif python-pillow-jpegxl-plugin oxipng pngquant jpegoptim \
    gifski perl-image-exiftool tesseract tesseract-data-eng zbar python-qrcode \
    python-pymupdf python-pypdf qpdf ghostscript poppler libreoffice-fresh pandoc-cli typst \
    ffmpeg python-mutagen 7zip python-fonttools python-brotli
```

<details>
<summary><b>Full list by feature</b></summary>

<br>

**Official repositories**

| Feature | Packages |
|---|---|
| HEIC and JPEG XL | `python-pillow-heif python-pillow-jpegxl-plugin` |
| Image optimization | `oxipng pngquant jpegoptim libjpeg-turbo` |
| GIF and animation | `gifski gifsicle libwebp-utils` |
| Unusual formats, huge images | `imagemagick libheif libvips` |
| SVG | `librsvg resvg potrace inkscape scour` or `svgo` |
| Metadata and privacy | `perl-image-exiftool mat2` |
| OCR | `tesseract tesseract-data-eng`, plus your languages |
| QR codes and barcodes | `zbar python-qrcode qrencode` |
| Annotation | `satty` or `swappy`, `xournalpp` for PDF |
| AI upscaling | `waifu2x-ncnn-vulkan` |
| PDF | `python-pymupdf python-pypdf python-pikepdf python-cryptography qpdf ghostscript poppler mupdf-tools img2pdf` |
| Office documents | `libreoffice-fresh` or `libreoffice-still`, `python-openpyxl python-docx` |
| Markdown, text, code | `pandoc-cli typst python-weasyprint python-pygments python-charset-normalizer` |
| Structured data | `python-yaml python-tomli-w python-defusedxml` |
| Ebooks | `calibre` |
| Audio and video | `ffmpeg python-mutagen` |
| Transcription and subtitles | `whisper-cpp` |
| Downloads from links | `yt-dlp deno` |
| Archives | `7zip unrar unarchiver` |
| Fonts | `python-fonttools python-brotli` |
| AppImage | `squashfs-tools` |
| Sending to a phone | `kdeconnect` |
| System | `gnupg udisks2 trash-cli zenity gtk3 xdg-terminal-exec` |
| Colour picker in dialogs | `hyprpicker` |
| Printing | `cups` |
| XXH128 checksums | `xxhash` |
| Web page to PDF | `chromium` or another Chromium-based browser |

**AUR**

| Package | Purpose |
|---|---|
| `ocrmypdf` | Making scanned PDFs searchable |
| `python-pdf2docx` | More faithful PDF to Word conversion |
| `vtracer` | Colour vectorization |
| `realesrgan-ncnn-vulkan-bin` | ×4 upscaling with Real-ESRGAN |
| `localsend-cli-bin` | Sending with LocalSend |

**pipx**

| Command | Purpose |
|---|---|
| `pipx install "rembg[cpu,cli]"` | Background removal; downloads an AI model on first use |

</details>

## Uninstalling

**System package**

```sh
sudo pacman -Rns dropwheel
```

Then remove the `require("dropwheel")` line from `~/.config/caelestia/hypr-user.lua`, delete
`~/.config/caelestia/dropwheel.lua` and run `hyprctl reload`. pacman does not touch your home folder, so
your settings, history, cache and Whisper models stay until you delete them.

**install.sh**

```sh
~/.local/share/dropwheel/uninstall.sh           # keeps your settings and data
~/.local/share/dropwheel/uninstall.sh --purge   # also deletes them, after asking
```

Files you created with Dropwheel are never deleted.

---

← [Documentation](README.md) · [Next](USAGE.md) →
