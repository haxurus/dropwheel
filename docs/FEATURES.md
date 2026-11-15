[Documentation](README.md) · [Italiano](FEATURES.it.md)

# What it can do

The ring adapts to the file, so the exact entries vary. Rings hold at most eight segments, and extra
entries move to **More**.

| File type | Actions |
|---|---|
| **Images** (JPG, PNG, WebP, AVIF, HEIC, JPEG XL, TIFF, BMP, ICO, PSD) | Convert, compress (including to a target size), resize (with social media presets), crop, rotate, flip, adjust colours, annotate, watermark, remove or add a background, strip location or all metadata, redact, OCR, read QR codes, AI upscale, vectorize, make icons, set as wallpaper, print |
| **Animated images** (GIF, WebP, APNG) | Convert to MP4 or WebM, compress, resize, crop, rotate, extract frames, reverse, change speed |
| **SVG** | Convert to PNG, PDF, JPG, WebP or ICO, optimize, copy as text or image, make icons |
| **PDF** | Convert to DOCX, JPG, PNG, TXT, Markdown, EPUB, HTML or PDF/A; compress; organize, split, extract, delete and rotate pages; protect with AES-256 or unlock; true redaction; edit metadata; OCR; extract images and attachments; annotate; watermark; page numbers; flatten; repair |
| **Documents** (DOCX, DOC, ODT, RTF) | Convert to PDF, ODT, Markdown, TXT, HTML, EPUB or RTF, compress embedded images, remove personal data, extract images and text, password-protected PDF, statistics |
| **Spreadsheets** (XLSX, ODS, XLS, CSV, TSV) | Convert to ODS, CSV, PDF, JSON, Markdown table, HTML or TSV, remove metadata, sheet info |
| **Presentations** (PPTX, ODP, PPT) | Convert to PDF, ODP or images, compress media, extract text, speaker notes and images, remove metadata |
| **Markdown** | Convert to PDF, HTML, DOCX, ODT, EPUB, TXT or RTF, slides, table of contents, formatting, statistics |
| **Text and code** | Export to PDF, HTML, DOCX or Markdown with syntax highlighting, convert to UTF-8, fix line endings, trim spaces, statistics |
| **Data** (JSON, YAML, TOML, XML) | Convert between formats and to CSV or XLSX, format, minify, validate, sort keys |
| **Ebooks** (EPUB, MOBI, AZW3, FB2, CBZ) | Convert, extract the cover, book info |
| **Subtitles** (SRT, VTT, ASS) | Convert, shift timing, change frame rate, remove SDH tags, plain text |
| **Audio** (MP3, M4A, FLAC, WAV, Opus, OGG) | Convert, compress, trim, normalize to EBU R128 targets, edit tags and cover, transcribe locally with whisper.cpp, change speed, fade, remove silence, reduce noise, waveform video |
| **Video** (MP4, MKV, WebM, MOV, AVI) | Convert, compress (including a WhatsApp/Telegram preset and target size), trim, crop, resize, mute, rotate, change speed, reverse, loop, stabilize, extract audio, frames and subtitles, contact sheet, generate or burn in subtitles, split, edit metadata |
| **Archives** (ZIP, 7Z, RAR, TAR, GZ, XZ, ZST) | Smart extract, extract to a folder, list, test, convert, add a password |
| **Fonts** (TTF, OTF, WOFF, WOFF2) | Install, preview sheet, convert, web kit, subset, info |
| **Folders** | Compress, batch rename, size breakdown, find duplicates, create SHA256SUMS, list contents, flatten |
| **Web links** | Download, save video or audio with yt-dlp, QR code, copy clean link, save page as PDF, create a shortcut |
| **AppImage, disk images, packages** | Add to menu, run, mount read-only, verify, inspect, extract |
| **Unknown files** | Identify the real type, checksums, hex view, strings, encrypt |

Every file also has a **File** page: copy, copy path, move, rename, duplicate, ZIP, open with, share
(QR code for your phone, KDE Connect, LocalSend, email), properties, checksums, encrypt with GnuPG,
open a terminal here, and trash.

**Several files at once.** Files of the same type get group actions, for example images to PDF, GIF,
slideshow or collage; merging PDFs, videos or audio; or combining documents into one PDF. Mixed
selections get what applies to all of them.

**Where results go**

| Operation | Example |
|---|---|
| Conversion | `photo.heic` produces `photo.jpg` |
| Tool on the same format | `photo.jpg` produces `photo (Compressed).jpg` |
| Name already taken | `photo 2.jpg`, `photo 3.jpg`, and so on, never an overwrite |
| Several outputs | A folder such as `document (Pages)/` |
| Read-only or temporary source | `~/Downloads/Dropwheel`, and the card tells you |

The complete list of actions, ring by ring and with the programs they need, is in
[docs/FUNCTIONS.md](FUNCTIONS.md).

---

← [Previous](USAGE.md) · [Next](CLI.md) →
