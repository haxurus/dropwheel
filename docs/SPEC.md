# Dropwheel — technical specification (v1)

Dropwheel is a radial "drag + Shift" file-action wheel for **Arch Linux + Hyprland (≥0.57, Lua config) + Caelestia (Quickshell)**.
It is the Linux equivalent of the macOS app *Tangerine* (tangerineformac.com): while dragging files, tap **Shift** and a wheel
appears under the cursor; drop the files on a segment to convert / compress / crop / redact / extract / share them.

This document is the single source of truth for every contributor. Paths are relative to the repository root.
Research notes (read them, they contain verified facts, exact commands and pitfalls) live in
`%RESEARCH%` (absolute path given in your brief): `mac-app.md`, `hyprland(.verify).md`, `quickshell(.verify).md`,
`caelestia(.verify).md`, `tools-docs-images.md`, `tools-media-archives.md`, `feature-catalog.md`.

Target platform facts (verified, do not re-litigate):
- Hyprland 0.57.0 (Lua-only config; legacy hyprland.conf removed), Quickshell 0.3.2 / quickshell-git (same QML API),
  Qt 6.12, Python 3.14 (code must also run on 3.11+), Pillow 13 (AVIF/WebP native), PyMuPDF 2.0 (`import pymupdf`, not `fitz`),
  ffmpeg 9 (`-vsync` removed, use `-fps_mode`), exiftool lives at `/usr/bin/vendor_perl/exiftool`, ocrmypdf is AUR-only,
  LibreOffice headless needs `-env:UserInstallation=file:///…` (private profile) or it silently does nothing.
- Caelestia scheme: `${XDG_STATE_HOME:-~/.local/state}/caelestia/scheme.json` `{name,flavour,mode,variant,colours{key: "rrggbb"}}`.

---------------------------------------------------------------------------------------------------------------------
## 1. Repository layout and ownership

```
pyproject.toml                       packaging (setuptools), entry point dropwheel = dropwheel.cli:main
Makefile, PKGBUILD, install.sh, uninstall.sh
README.md (Italian, main doc), docs/*.md
src/dropwheel/
  __init__.py  __main__.py
  cli.py            CLI (argparse)                                   [core-B]
  daemon.py         stdio JSON-RPC server for the QML shell          [core-B]
  preview.py        previews/thumbnails/metadata/pdf search          [core-B]
  share.py          LAN share server + QR                            [core-B]
  shellctl.py       locate shell dir, start/stop qs, ipc calls       [core-B]
  config.py         config load/defaults/validation                  [core-A]
  i18n.py           translations (merges locales/<lang>/*.json)      [core-A]
  detect.py         FileInfo + classification                        [core-A]
  tools.py          dependency detection + install hints             [core-A]
  registry.py       Action/Param/Group/Layout + menu builder         [core-A]
  jobs.py           Context, JobRunner, naming, undo/history, staging[core-A]
  errors.py         exception types                                  [core-A]
  util.py           shared helpers (clipboard, open, reveal, trash)  [core-A]
  notify.py         desktop notifications                            [core-A]
  locales/it/*.json, locales/en/*.json   one fragment per module (e.g. core.json, image.json, ui_wheel.json)
  actions/__init__.py   imports every action module                  [core-A creates; lists all modules below]
  actions/common.py     generic file actions ("file" page)           [act-misc]
  actions/image.py      raster images (+animated), svg               [act-image]
  actions/pdf.py        PDF                                          [act-pdf]
  actions/docs.py       office docs, spreadsheets, presentations, markdown, text, code, data, ebooks [act-docs]
  actions/media.py      video + audio + media combine                [act-media]
  actions/archive.py    archives (extract/create/convert)            [act-misc]
  actions/fonts.py      fonts                                        [act-misc]
  actions/folder.py     folders                                      [act-misc]
  actions/web.py        URLs dropped from a browser                  [act-misc]
  actions/system.py     AppImage, ISO/disk images, packages, unknown binaries [act-misc]
  actions/custom.py     user-defined actions from config             [act-misc]
shell/                  Quickshell config (run with `qs -p shell/`)
  shell.qml                                                          [ui-wheel]
  services/  Theme.qml Backend.qml Hypr.qml I18n.qml Jobs.qml qmldir [ui-base]
  components/ (MaterialIcon, StyledText, StyledRect, StateLayer, Anim, CAnim, Elevation, IconButton, TextButton,
               M3Slider, M3Switch, M3TextField, M3Dropdown, ColorPicker, Chip, CircularProgress, LinearProgress,
               Tooltip, FileIcon, ScrollArea, qmldir)                [ui-base]
  wheel/   WheelWindow.qml Wheel.qml Segment.qml CenterHub.qml HotZone.qml qmldir      [ui-wheel]
  hud/     HudWindow.qml JobCard.qml ResultChip.qml qmldir                             [ui-wheel]
  dialogs/ DialogWindow.qml ParamForm.qml CropEditor.qml RedactEditor.qml TrimEditor.qml PageOrganizer.qml
           MetadataEditor.qml AdjustEditor.qml BackgroundEditor.qml TextResult.qml InfoResult.qml ShareResult.qml qmldir [ui-dialogs]
integrations/  hyprland/dropwheel.lua, nautilus/, dolphin/, nemo/, thunar/, desktop/dropwheel.desktop  [pack]
tests/  test_*.py  (pytest; each agent adds tests for its own modules: tests/test_<module>.py)
```
**Never edit a file you do not own.** If you need something from another module, code against the contract in this
document; if the contract is insufficient, write the request in your final report (the lead integrates).

---------------------------------------------------------------------------------------------------------------------
## 2. Runtime architecture

```
Hyprland ──Shift_L/R bind (non_consuming)──► hl.dsp.global("dropwheel:shift") ──► Quickshell GlobalShortcut
          ──SUPER+ALT+D──────────────────────► hl.dsp.global("dropwheel:open")
Quickshell shell (qs -p <shelldir>) ── Process(stdin/stdout JSON lines) ──► python -P -m dropwheel daemon
     wheel / hud / dialogs windows                                        registry + actions + jobs (threads)
CLI `dropwheel open|run|menu|doctor|…`  ──► `qs -p <shelldir> ipc call dropwheel <fn> <json>`  (or headless run)
File-manager actions (Nautilus/Dolphin/Nemo/Thunar) ──► `dropwheel open %F`
```

### 2.1 Triggers (shell side)
1. **Shift during a drag** (primary). On `GlobalShortcut{appid:"dropwheel"; name:"shift"}.pressed` (the bind may be deferred
   to key release by Hyprland's chord resolver — treat whichever signal arrives first, debounce 250 ms):
   run `hyprctl -j monitors` and `hyprctl -j cursorpos` (two Process objects, in parallel). A drag is active iff some monitor's
   `solitaryBlockedBy` array contains `"DND"` (field may be null). If active → open the wheel in **drag mode** centred on the
   cursor. If the wheel is already open in drag mode, Shift toggles the wheel page (main ⇄ file).
   Config `trigger.dnd_check=false` → skip the check, open the wheel and close it again after 1200 ms if no drag enters.
2. **Hot zone** (optional, `hotzone.enabled`, default true): a tiny always-mapped Overlay PanelWindow (namespace
   `dropwheel-hotzone`, default top-right corner, 6×6 px, configurable corner/edge/size) with a DropArea. Drag enter → open the wheel in
   drag mode near the hot zone (clamped on screen; the user moves onto it). Dropping directly on the hot zone → wheel in click mode
   with the dropped files.
3. **Shortcut** `dropwheel:open` (SUPER+ALT+D): wheel in **click mode** at the cursor with the files currently in the clipboard
   (`clipboard_files` RPC, i.e. `wl-paste -t text/uri-list` / `x-special/gnome-copied-files`); if none, the wheel shows an empty
   drop target ("Trascina qui dei file") that accepts drops.
4. **IPC/CLI**: `qs -p <dir> ipc call dropwheel open '<json>'` with `{"files":[…],"x":int?,"y":int?}` → click mode.

### 2.2 Wheel window facts (verified — follow them)
- One PanelWindow per open wheel, `WlrLayershell.layer: WlrLayer.Overlay`, `exclusionMode: ExclusionMode.Ignore`,
  `color: "transparent"`, namespace `dropwheel-wheel`, anchors top+left with margins so the **surface centre is the cursor**
  (only the disc is clamped to the screen; the surface also hosts the labels and may hang off the screen through negative
  margins). The screen is the `Quickshell.screens` entry whose logical rect
  contains the global cursor (`hyprctl cursorpos` is global logical coords; screen.x/y/width/height are logical).
- `mask: Region { item: ring; shape: RegionShape.Ellipse }` so only the disc takes input; drops outside fall through.
- **Drag mode: `WlrLayershell.keyboardFocus: WlrKeyboardFocus.None`** (otherwise the overlay steals focus from the drag source).
  Dialogs and a wheel surface **created** in click mode: `Exclusive`. A surface already mapped (drag→click, reopen during
  the close animation) and the empty drop target use `OnDemand`: Hyprland ends the seat grab when a mapped layer surface
  turns `Exclusive` (`CLayerSurface::onCommit` → `setGrab(nullptr)`).
- Hyprland sends the DnD `enter` at the **surface centre** and no motion until the pointer moves → that is why the surface is
  centred on the cursor; ignore `onEntered` coordinates, hit-test on `onPositionChanged` and on `onDropped` coordinates.
- `DropArea { keys: ["text/uri-list"] }`; in `onDropped` **read `drop.urls` first**, then `drop.accept(Qt.CopyAction)`;
  never hide the window synchronously inside onDropped (use `Qt.callLater`/0 ms Timer).
- Hyprland may tell sources the action was MOVE: **we always treat drops as copy and never delete/move inputs.**
- Escape during a drag aborts the DnD in Hyprland → `exited` fires; while a drag-mode wheel is open poll `hyprctl -j monitors`
  every 300 ms when the drag is not over the wheel; when DND disappears, close the wheel.
- Dragging from XWayland apps into Wayland targets is not bridged by Hyprland 0.57 (document it, nothing to do).
- `visible:false` destroys the surface; showing maps it again (that is what we want for the wheel).

### 2.3 Wheel UX
- Up to 8 segments per ring (backend guarantees it, overflow goes to a "More" group). Segment 0 is centred at 12 o'clock,
  clockwise. Geometry (at scale 1): inner radius 52, outer radius 132, gap 4 px, rounded segment corners (~10 px), labels at
  mid-radius with icon (Material Symbols Rounded 22 px) above a short uppercase label (font ~9.5 pt, weight 600); format targets
  (`node.text` set, e.g. "PNG") render the text big (15 pt, weight 600) without icon. Backdrop disc (radius outer+8) in
  `surface` with Caelestia transparency, blur via Hyprland layer rule, Elevation shadow.
- Highlight: hovered segment fill `primary`, content `onPrimary`; idle fill `surfaceContainerHigh`, content `onSurface`;
  unavailable at 38 % opacity with a "download" badge and a tooltip/centre text showing the install hint; danger actions
  hover in `error`/`onError`.
- Centre hub (radius 48): thumbnail (image/pdf thumb from backend) or category icon; multi-file badge with count; a pill below
  the wheel shows the hovered item label (or file name + size when nothing is hovered); page dots (main/file).
- Open animation: scale 0.45→1 + opacity 0→1 in 350 ms, `expressiveFastSpatial` curve; segment hover colour `CAnim`.
- Sub-rings: hovering a group for `wheel.spring_ms` (default 450) in drag mode, or clicking it in click mode, morphs the ring into
  the group's items (cross-fade + rotate 12°, 300 ms); the centre becomes **Back** (hover it 450 ms or click it). Dropping on a
  group keeps the wheel open in click mode showing that sub-ring.
- Drop on an action: instant/quick → `run`; dialog → open the dialog (see §6). Drop on the centre or outside → cancel.
- Click mode keyboard: arrows / Tab / mouse wheel move the highlight, 1–8 run segment N, Enter runs/opens, Backspace = back,
  Esc = back or close, Alt+Enter or right-click = open the parameter form of a "quick" action, Shift or Ctrl+Tab = switch page.
- After `run`, the wheel shrinks into the HUD (job card) and closes.

### 2.4 HUD (job cards)
- PanelWindow namespace `dropwheel-hud`, Overlay, anchored to `hud.position` (default bottom-right) with a margin, keyboardFocus
  None, mask = the cards column. Cards stack (newest at bottom), max 5 visible.
- Running: action label, file name(s), linear progress (indeterminate when progress=null), cancel ×.
- Done (files): output thumbnail/icon + name + size (and "−78 %" when size_before/after given); each output is a **draggable
  result chip** (`Drag.dragType: Drag.Automatic`, `Drag.mimeData: {"text/uri-list": uri + "\r\n", "text/plain": path}`,
  `Drag.supportedActions: Qt.CopyAction`); buttons Open, Show in folder, Copy, Undo (if undoable), ×. Auto-dismiss after
  `hud.timeout_ms` (default 7000, paused on hover).
- Done (text/qr/info/share): compact preview + Copy + "Expand" → opens TextResult / InfoResult / ShareResult dialog.
- Failed: error container colours, message, expandable details, "Copy install command" when `error.install` exists. Stays until ×.
- Warnings appear as a small line on the card.

---------------------------------------------------------------------------------------------------------------------
## 3. Backend core API (Python) — contract for action modules

```python
from dropwheel.registry import action, Param, group, layout, P   # P = param helpers
from dropwheel.jobs import Context, Result
from dropwheel.errors import UserError, MissingTool, Cancelled
from dropwheel.detect import FileInfo
from dropwheel import tools, util
```

### 3.1 FileInfo (detect.py)
```python
@dataclass
class FileInfo:
    path: str            # absolute local path ("" for urls)
    uri: str             # file:///… (percent-encoded) or the http(s) URL
    name: str; stem: str; ext: str          # ext lowercase without dot; multi-ext like "tar.gz" kept together
    category: str        # see CATEGORIES
    mime: str
    size: int            # bytes (dirs: 0, computed lazily in properties)
    is_dir: bool; exists: bool
    animated: bool       # gif/webp/apng with >1 frame
    writable_dir: bool   # parent dir writable
    volatile: bool       # lives in /tmp, /var/tmp, $XDG_RUNTIME_DIR, browser caches → staged copy is used
    width: int|None; height: int|None; pages: int|None; duration: float|None   # filled when cheap
    icon: str            # Material Symbols name for the category
    @property p -> Path
    def to_json() -> dict
CATEGORIES = ["image","svg","pdf","document","spreadsheet","presentation","markdown","text","code","data","ebook",
              "subtitle","audio","video","archive","font","folder","url","appimage","disk","package","unknown"]
def classify(path_or_uri: str) -> FileInfo      # never raises; unknown → "unknown"
```
Classification by content first (magic), then extension (see feature-catalog R6). CSV/TSV → spreadsheet. JSON/YAML/TOML/XML →
data. SRT/VTT/ASS → subtitle. Animated GIF stays category "image" with `animated=True`.

### 3.2 Registry (registry.py)
```python
@action(
    id: str,                  # "image.convert.png" — dotted, unique, stable (used by CLI and history)
    label: str,               # i18n key; if the key is missing in the locale the key itself is shown (so "PNG" works)
    icon: str = "",           # Material Symbols Rounded ligature name
    text: str | None = None,  # big text instead of icon (format targets): e.g. "PNG"
    categories: set[str],     # accepted categories
    when: Callable[[list[FileInfo]], bool] | None = None,   # extra applicability predicate (e.g. not same format)
    multi: str = "each",      # "single" (exactly 1 file) | "each" (1..n, runner calls fn once per file)
                              # | "combine" (≥2 files, fn called once with all) | "any" (fn called once with all, n≥1)
    mode: str = "instant",    # "instant" | "quick" (runs with defaults, params editable via Alt) | "dialog"
    dialog: str | None = None,# None|"form"|"crop"|"redact"|"trim"|"pages"|"metadata"|"adjust"|"background"
    params: list[Param] = (),
    requires: list = (),      # tool names (tools.py) or ("a","b") alternatives or "py:module"
    danger: bool = False,
    undoable: bool = True,    # outputs can be trashed by Undo
    replace_ok: bool = False, # adds the standard "Replace original" bool param (original backed up for undo)
    description: str = "",    # i18n key (tooltip)
    mixed_ok: bool = False,   # may appear in the auto-built "mixed" menu
    order: int = 50,
)
def fn(ctx: Context) -> Result | None

@dataclass class Param:
    name: str; type: str      # int|float|slider|bool|select|text|password|color|time|timerange|pages|path|dir|textarea|
                              # rect|boxes|pageops|metadata|adjust|background (structured types are produced by dialogs)
    label: str = ""           # i18n key
    default: Any = None; min=None; max=None; step=None; unit: str = ""
    options: list | Callable[[list[FileInfo]], list] | None = None   # [(value, label_key)] or plain values
    help: str = ""; required: bool = False; visible_if: dict | None = None   # {"other_param": value}

group(id, label, icon, items: list[str | GroupSpec], default: str | None = None) -> GroupSpec
layout(key: str, items: list[str | GroupSpec], page: str = "main")   # register a ring layout
#   key = category ("image"), "multi:<category>" (≥2 files, all same category), "mixed", or "file" (the File page, all files)
#   items reference action ids or groups; non-applicable/hidden items are dropped, empty groups removed,
#   single-item groups flattened (except "convert"), rings capped at 8 with an automatic "More" group.

build_menu(files: list[FileInfo], config: dict, lang: str, context: str) -> dict   # JSON in §5.3
```
When no layout exists for a key, the builder auto-groups applicable actions by their id prefix (`<cat>.<group>.<leaf>` →
group `<cat>.<group>` labelled `grp.<group>`). Shared group label keys exist in `locales/*/core.json`:
`grp.convert grp.compress grp.resize grp.edit grp.background grp.privacy grp.pages grp.extract grp.combine grp.audio
grp.video grp.ocr grp.share grp.secure grp.open grp.file grp.info grp.more grp.transform grp.trim grp.tools grp.metadata`.

### 3.3 Context and Result (jobs.py)
```python
class Context:
    job_id: str; action: Action; files: list[FileInfo]; params: dict; config: dict; lang: str
    @property file -> FileInfo                    # first file
    def t(key, **fmt) -> str                      # i18n
    def p(name, default=None); pint(); pfloat(); pbool(); pstr()
    def progress(frac: float | None, message: str | None = None)   # None = indeterminate; batch-aware (scaled per file)
    def log(msg); warn(msg)                       # warn → shown on the HUD card
    def check_cancel()                            # raises Cancelled
    def tmpdir() -> Path                          # per-job temp dir, removed at the end
    def output_path(src: FileInfo|Path|None=None, *, ext: str|None=None, suffix: str|None=None,
                    name: str|None=None, directory: Path|None=None) -> Path
        # conversions: suffix=None → "<stem>.<ext>"; tools: suffix=<i18n key> → "<stem> (<translated>).<ext>"
        # unique: "<stem> 2.<ext>", "<stem> 3.<ext>"…; never equals any input; honours config.output.location
        # (source|folder) and falls back to ~/Downloads/Dropwheel when the source dir is not writable or volatile.
    def output_dir(src=None, *, suffix=None, name=None) -> Path   # creates a unique folder
    def run(argv: list, *, input: bytes|None=None, timeout: float|None=None, env: dict|None=None, cwd=None,
            ok_codes=(0,), on_line: Callable[[str], None]|None=None, stderr_lines: bool=False) -> CompletedProcess
        # argv[0] is a tools.py name or a binary; resolved via tools.path(); raises MissingTool / ToolError (readable
        # tail of stderr in .details); kills the process group on cancel.
    def ffmpeg(args: list, *, src: Path|None=None, duration: float|None=None, label: str|None=None)
        # prepends ffmpeg -hide_banner -nostdin -y -loglevel error -progress pipe:1 -nostats; parses out_time_us
    def probe(path) -> dict                       # ffprobe -show_format -show_streams JSON (cached)
    def replace_original(src: FileInfo, produced: Path) -> Path     # when params["replace"]: back up + swap; else no-op
    def result(outputs=(), *, message: str = "", kind: str = "files", text: str|None=None,
               info: list[dict]|None=None, size_before: int|None=None, **extra) -> Result
class Result: outputs: list[Path]; kind: str ("files"|"text"|"info"|"qr"|"share"|"none"); message; text; info; extra
```
Errors: `UserError(message_key_or_text, details="", **fmt)` (shown to the user, translated if it is a key), `MissingTool(name)`
(→ install hint), `ToolError` (subclass of UserError), `Cancelled`. Any other exception = bug → message "Unexpected error"
plus traceback in details.

Runner guarantees: validates inputs exist/readable; stages volatile inputs; batching for multi="each" (continues on per-file
failure, aggregates "3/4 done, 1 failed"); per-job temp dir; history entry (`$XDG_STATE_HOME/dropwheel/history.jsonl`) with
outputs and backups for Undo; never deletes inputs; writes outputs atomically (write to `.part` in the target dir, then rename —
helper `jobs.atomic_target(path)` context manager); disk-space warning; cancellation.

### 3.4 tools.py
`tools.path(name) -> str|None`, `tools.have(name) -> bool`, `tools.missing(requires) -> list[str]`,
`tools.install_hint(missing) -> str` ("sudo pacman -S …" / "paru -S …" for AUR / "pipx install …"),
`tools.report() -> dict`. Names map to binaries + Arch packages (+ repo: extra/aur/pip). Includes at least:
magick ffmpeg ffprobe gs qpdf pdftoppm pdftotext pdfimages pdfinfo mutool soffice pandoc typst weasyprint exiftool(abs path
/usr/bin/vendor_perl/exiftool first) tesseract ocrmypdf(aur) 7z bsdtar unrar zstd xz gzip rsvg-convert resvg potrace vtracer(aur)
rembg(pip) realesrgan(aur) waifu2x oxipng pngquant jpegoptim jpegtran cwebp avifenc heif-enc cjxl gifski gifsicle
whisper-cli zbarimg qrencode ebook-convert(calibre) fc-cache wl-copy wl-paste notify-send gio gdbus xdg-open gtk-launch
hyprctl caelestia satty swappy xournalpp yt-dlp curl gpg kdeconnect-cli localsend-cli udisksctl trash-put img2pdf pdftk
and python modules py:PIL py:pymupdf py:pypdf py:pikepdf py:pillow_heif py:pillow_jxl py:fontTools py:brotli py:yaml
py:tomli_w py:openpyxl py:pdf2docx py:docx py:mutagen py:qrcode py:pygments py:charset_normalizer py:py7zr py:rarfile.

### 3.5 i18n
`locales/<lang>/<module>.json` flat `{key: text}`; `{name}` placeholders. Languages `it` and `en`; `config.language="auto"`
→ from `LC_ALL`/`LC_MESSAGES`/`LANG` (it* → it, else en). Missing key → English → key itself.
Italian strings: natural and concise, imperative for actions ("Comprimi", "Ritaglia", "Converti"). Output suffix keys are
`sfx.<name>`; Italian suffixes are short past participles in the masculine singular, referring to "file" ("compresso",
"ritagliato", "ruotato", "senza metadati", "oscurato", "unito", "estratto"). English suffixes are capitalised ("Compressed",
"Cropped"). Pattern: `"<stem> (<suffix>).<ext>"`.

### 3.6 config.py — `~/.config/dropwheel/config.toml` (TOML, read with tomllib; `dropwheel config --init` writes a commented default)
```toml
language = "auto"            # auto | it | en
[output]   location = "source" # source | folder ;  folder = "~/Downloads/Dropwheel" ; open_after = false
[trigger]  shift = true ; dnd_check = true
[hotzone]  enabled = true ; corner = "top-right" ; size = 6
[wheel]    scale = 1.0 ; spring_ms = 450 ; show_unavailable = true ; sound = false
[hud]      position = "bottom-right" ; timeout_ms = 7000
[notifications] mode = "long"   # never | long (jobs > 10 s or when HUD hidden) | always
[jobs]     max_parallel = 2
[image]    jpeg_quality = 85 ; webp_quality = 80 ; avif_quality = 60 ; keep_metadata = true
[video]    crf = 23 ; preset = "medium" ; hwaccel = "auto"   # auto | vaapi | nvenc | none
[audio]    mp3_quality = 2 ; opus_bitrate = "128k"
[pdf]      dpi = 150 ; compress = "ebook"
[ocr]      languages = "ita+eng"
[apps]     terminal = "" ; file_manager = "" ; image_editor = ""   # "" = autodetect
[share]    minutes = 15 ; port = 0
hidden_actions = []
[[custom]]  # id, label, icon, categories, command (template with {file} {files} {dir} {stem} {ext}), multi, output
```
`config.load() -> dict` (defaults merged, invalid values replaced by defaults with a warning list in `config["_warnings"]`).

---------------------------------------------------------------------------------------------------------------------
## 4. Action catalogue
Implement the catalogue in `feature-catalog.md` (top rings, sub-rings, parameters, defaults, multi-file behaviour, edge cases),
cross-checked with Tangerine's real wheels in `mac-app.md` §2.3, using the commands in `tools-*.md`. Ring order rule: slot 1 =
Convert (or main transform), slot 2 = Compress (or nearest), then by frequency; ≤ 8 per ring.
Every category also gets the **File page** (layout key "file", owned by act-misc): Open, Open with…, Show in folder, Copy file,
Copy path, Move to…, Copy to…, Rename…, Duplicate, Compress to ZIP, Share→ (phone QR / KDE Connect / LocalSend / email),
Properties, Checksum, Encrypt (gpg), Trash (danger), Open terminal here, Make executable (when relevant).
Output naming follows Tangerine: conversions "<stem>.<ext>", tools "<stem> (<suffix>).<ext>", collisions " 2", " 3"…;
folders for multi-output ("<stem> (pagine)/page-001.png").

---------------------------------------------------------------------------------------------------------------------
## 5. QML ⇄ daemon protocol (JSON lines over stdin/stdout, UTF-8, one object per line)

The shell starts the daemon with `Process { command: Backend.command; stdinEnabled: true; stdout: SplitParser{} }` where
`command` = `Quickshell.env("DROPWHEEL_BACKEND")` split on spaces, default `["dropwheel","daemon"]`. Restart on exit with
back-off (1 s, 2 s, 5 s, max 5 tries/min). stderr is logged.

### 5.1 Requests / responses
```
→ {"id": 7, "method": "menu", "params": {...}}
← {"id": 7, "result": {...}}            or   {"id": 7, "error": {"message": "...", "details": "...", "install": "..."}}
← {"event": "...", ...}                 (unsolicited, no id)
```
On start the daemon emits `{"event":"ready","version":1}` (always the first line).
Other unsolicited events: `{"event":"log","level","logger","message"}` (warnings forwarded to the shell log),
`{"event":"config","config"}` and `{"event":"config_changed","config","lang","lang_changed","warnings"}` (emitted only
when config.toml really changed; on `lang_changed` the shell re-runs `hello` to get the other strings table).
Errors also carry `code` (a short machine id: `unknown_method`, `bad_params`, `timeout`, an i18n key…).

| method | params | result |
|---|---|---|
| hello | {} | {version, lang, strings: {key: text, …all locales merged incl. ui_*}, config: {…}, warnings: [..]} |
| menu | {files: [path or uri…], context: "drag"\|"click"} | Menu (§5.3) |
| run | {action, files: [paths], params: {}} | {job: "j12"} |
| cancel | {job} | {ok: true} |
| undo | {job} | {ok, message} |
| preview | {kind, path, …} (§5.5) | kind-specific |
| apps | {files: [paths]} | [{id, name, icon}] (desktop entries supporting all mimes) |
| util | {op: "open"\|"reveal"\|"copy_files"\|"copy_text"\|"open_with"\|"terminal"\|"save_text", paths?, text?, app?} | {ok, path?} |
| clipboard_files | {} | {files: [paths]} |
| share_stop | {id} | {ok} |
| config | {} | config dict (reloads from disk) |
| doctor | {} | tools.report() |

### 5.2 Job events
```
{"event":"job","job":"j12","state":"queued"|"running"|"done"|"failed"|"cancelled",
 "action":"image.compress.balanced","label":"Comprimi · Bilanciata","files":["foto.jpg"],
 "progress": 0.42 | null, "message": "…", "warnings": ["…"],
 "result": Result JSON (state done), "error": {"message","details","install"} (state failed)}
```
`progress` events are throttled to ≥ 80 ms apart. Result JSON:
`{kind, message, outputs:[{path, uri, name, size, size_h, category, mime, thumb}], size_before, size_after, undoable,
text?, info?: [{group, key, value}], url?, qr?: path, share_id?}`.

### 5.3 Menu JSON
```json
{ "files": [FileInfo…], "category": "image" | "mixed", "count": 1,
  "title": "foto.jpg" | "3 file", "subtitle": "2,4 MB", "thumb": "/home/u/.cache/dropwheel/thumbs/ab12.png" | null,
  "icon": "image",
  "pages": [ {"id":"main","label":"Strumenti","items":[Node…]}, {"id":"file","label":"File","items":[Node…]} ] }
Node action: {"type":"action","id":"…","label":"Bilanciata","title":"Comprimi · Bilanciata","icon":"compress","text":null,"available":true,"hint":"",
  "install":"","danger":false,"mode":"instant"|"quick"|"dialog","dialog":null|"form"|"crop"|…,"params":[Param…],
  "description":"…","default":false}
Node group:  {"type":"group","id":"image.convert","label":"Converti","icon":"swap_horiz","text":null,"items":[Node…],
  "default":"image.convert.jpg"}
Param JSON: {name,type,label,default,min,max,step,unit,options:[{value,label}],help,required,visible_if}
```
`title` is the action in context ("<group> · <action>", or the label alone): the title of its dialog and of its job.
Extensions (all optional, the shell tolerates their absence): action nodes also carry `multi`, `undoable` and whatever
the action's `node_extra(files, lang)` hook adds for this selection (pdf: `locked`, `needs_password`, `description`
for password-protected PDFs); group nodes carry `available`; params may carry `locked: true`; the menu carries
`context`, and an empty selection has `category: "empty"`. The shell opens the parameter form instead of running an
instant/quick action when the node or one of its params is `locked`, or a `required` param has no default.
Job events also carry `paths` (full input paths/URLs); result JSON may add `size_before_h`, `size_after_h`, `delta`
(signed percent), `outputs[].is_dir` and, on partial batches, `errors: [{file, message, details, install}]`.

### 5.4 IPC handler (shell.qml) — `IpcHandler { target: "dropwheel" }`
`open(payload: string): void` (JSON `{"files":[…],"x":?,"y":?}`), `openAtCursor(): void`, `close(): void`,
`setHotzone(enabled: bool): void`, `reload(): void`, `ping(): string` ("pong").

### 5.5 Previews (preview.py, cached under `$XDG_CACHE_HOME/dropwheel/previews`, key = path+mtime+opts)
| kind | params | result |
|---|---|---|
| thumb | path, size=256 | {png, width, height} (images, svg, pdf page 1, video frame at 10 %; null for others) |
| image | path, max=1600 | {png, width, height (ORIGINAL pixel size, EXIF-rotated), scale} |
| pdf_info | path | {pages, sizes:[[w_pt,h_pt]…], encrypted, needs_password} |
| pdf_page | path, page (0-based), dpi=110 | {png, width_pt, height_pt} |
| pdf_search | path, query | {matches:[{page, rects:[[x0,y0,x1,y1]…]}]} (PDF points) |
| video_info | path | {duration, width, height, fps, has_audio, codec} |
| video_frames | path, count=12, height=72 | {frames:[{t, png}]} |
| audio_waveform | path, width=1200, height=160 | {png, duration} |
| metadata | path | {groups: [{name, items:[{key, value, editable}]}], editable: bool} |
| text | path, max_bytes=65536 | {text, encoding, truncated} |

### 5.6 Dialog → params contracts (what each editor sends in `run.params`)
| dialog | params produced |
|---|---|
| form | the action's own params (generic form) |
| crop | `rect: {x,y,w,h}` in original pixels (images) or source video pixels; plus `ratio`. On a RAW, original pixels are the size `preview image` reports: the action scales the rect when the decoded picture differs |
| redact | `boxes: [{page:int|null, x,y,w,h (0..1 normalised), style:"solid"\|"blur"\|"pixelate"}]`, optional `terms: [str]` (PDF text search) |
| trim | `start`, `end` (seconds, float); optional `mode: "keep"|"cut"` |
| pages | `pageops: [{src:int (0-based), rotate: 0|90|180|270}]` (order = new order; missing pages = deleted) |
| metadata | `metadata: {set: {key: value}, remove: [keys], remove_all: bool}` |
| adjust | `adjust: {exposure, brightness, contrast, saturation, temperature, tint, sharpen, vignette}` (−100..100, 0 = neutral) |
| background | `background: {padding, radius, shadow, ratio: "auto"\|"1:1"\|"16:9"\|"4:5", fill: {type:"color"\|"gradient"\|"blur"\|"image", colors:[…], angle, image}}` |

---------------------------------------------------------------------------------------------------------------------
## 6. QML shell contracts

### 6.1 Singletons (`shell/services`, `pragma Singleton`, registered in `services/qmldir`; import with `import "services"` / `import "../services"`)
- **Theme**: watches Caelestia `scheme.json` (FileView watchChanges) and `~/.config/caelestia/shell.json` (transparency);
  falls back to a built-in dark M3 palette. Exposes `palette.m3<role>` colours (all M3 roles + success*), `tPalette` (layered,
  Caelestia `layer()` algorithm), `light`, `rounding.{small,medium,large,extraLarge,full}`, `spacing.*`, `padding.*`,
  `font.family.{sans,mono,icon}` ("GoogleSansFlex" loaded with FontLoader from `/etc/xdg/quickshell/caelestia/assets/google-sans-flex/…`
  or `~/.config/quickshell/caelestia/assets/…`, fallback "Rubik", then default; icon font "Material Symbols Rounded"),
  `font.size.{small:11,smaller:12,normal:13,larger:15,large:18,extraLarge:28}`, `anim.durations.*`, `anim.curves.*`
  (BezierSpline arrays from caelestia.md §2), `scale` (config wheel.scale).
- **Backend**: owns the daemon Process; `call(method, params, callback(result, error))`; signals `ready()`, `event(var ev)`,
  `jobEvent(var ev)`; properties `connected`, `strings`, `config`; auto-restart.
- **I18n**: `t(key, args)` using `Backend.strings` with a small built-in English fallback map.
- **Hypr**: `queryDrag(callback(isDragging, cursor{x,y}))`, `cursorPos(callback)`, `screenAt(x, y) -> ShellScreen`,
  `dragActive` polling helper (`watchDrag(onEnd)`).
- **Jobs**: ListModel-like JS array of jobs updated from `Backend.jobEvent`; `run(action, files, params)`, `cancel(id)`,
  `undo(id)`, `dismiss(id)`; signal `jobFinished(var job)`.

### 6.2 Components (`shell/components`) — Caelestia look-alikes, all colours from Theme
MaterialIcon (font.variableAxes FILL/GRAD/opsz/wght, `verticalCenterOffset: 1`), StyledText, StyledRect (Behavior on color CAnim),
StateLayer (hover 0.08 + ripple), Anim/CAnim (Caelestia durations/curves), Elevation (RectangularShadow levels 0–5), IconButton
(filled/tonal/text), TextButton, M3Slider, M3Switch, M3TextField (incl. password), M3Dropdown, ColorPicker (swatches from
scheme + hex field), Chip (filter/assist), CircularProgress, LinearProgress (determinate + indeterminate), Tooltip, FileIcon
(category → icon + optional thumbnail), ScrollArea.

### 6.3 Window/dialog API
- `shell.qml` holds `WheelController` logic: `openWheel({files, menu, mode:"drag"|"click", x, y, screen})`, `closeWheel()`,
  `openDialog(node, files)`, and connects triggers (§2.1).
- `dialogs/DialogWindow.qml`: `function open(node, files)` → picks editor by `node.dialog`; on Apply calls
  `Jobs.run(node.id, files, params)` and closes; Esc/scrim click cancels.
- Every editor component: `required property var node; required property var files; signal apply(var params); signal cancel()`.

---------------------------------------------------------------------------------------------------------------------
## 7. Quality bar
- No crash paths: every external call has timeouts, readable errors, cancellation; QML never throws on null/undefined data
  (use `?.` and `??`).
- Every action works on file names with spaces, unicode, leading dashes (pass `--` or `./` prefixes), newlines.
- Missing optional tools never break the menu: the action is shown greyed with the install command.
- Python: type hints, docstrings on public functions, no third-party imports at module top level except Pillow-free stdlib
  (import optional libs inside functions). Run `python -m pytest -q` for your tests before finishing.
- QML: Qt 6 syntax, `pragma ComponentBehavior: Bound` where useful, no deprecated `width/height` on windows (use implicit*),
  property bindings over imperative code, animations with Theme curves, everything works at `wheel.scale` 0.8–1.5.

---------------------------------------------------------------------------------------------------------------------
## 8. Install layout and process wiring
| item | system package (PREFIX=/usr) | user-local (`install.sh`) | dev checkout |
|---|---|---|---|
| python package | site-packages/dropwheel (`python -m installer`) | `~/.local/share/dropwheel/python/dropwheel` | `src/dropwheel` |
| `dropwheel` command | `/usr/bin/dropwheel` (entry point) | `~/.local/bin/dropwheel` wrapper (sets PYTHONPATH) | `python -m dropwheel` with `PYTHONPATH=src` |
| QML shell | `/usr/share/dropwheel/shell/` | `~/.local/share/dropwheel/shell/` | `shell/` |
| integrations | `/usr/share/dropwheel/integrations/` | `~/.local/share/dropwheel/integrations/` | `integrations/` |
| desktop entry / icon | `/usr/share/applications/dropwheel.desktop`, `/usr/share/icons/hicolor/scalable/apps/dropwheel.svg` | `~/.local/share/applications/…`, `~/.local/share/icons/hicolor/scalable/apps/…` | — |

Data-dir resolution (`shellctl.data_dir()`): `$DROPWHEEL_DATA` → the directory two levels above the imported package
when it contains `shell/shell.qml` (a dev checkout, or the `install.sh` layout, always uses its own shell) →
`$XDG_DATA_HOME/dropwheel` → `$XDG_DATA_DIRS/dropwheel` → `/usr/local/share/dropwheel` → `/usr/share/dropwheel`. The shell is always launched by path (never by config name, which avoids
Quickshell's "shell.qml in ~/.config/quickshell hides sub-configs" pitfall): `qs -p <data>/shell -n -d`, IPC with
`qs ipc -p <data>/shell call dropwheel <fn> <arg>`. `dropwheel start` exports `DROPWHEEL_BACKEND="<sys.executable> -P -m dropwheel daemon"`
and the needed `PYTHONPATH`, so the shell always talks to the same Python package that launched it.
Hyprland autostart (integrations/hyprland/dropwheel.lua, `require("dropwheel")` from `~/.config/caelestia/hypr-user.lua`):
binds Shift_L/Shift_R → `hl.dsp.global("dropwheel:shift")` with `{ non_consuming = true, ignore_mods = true }`,
`SUPER + ALT + D` → `hl.dsp.global("dropwheel:open")`, layer rules (`dropwheel-(wheel|hud)`: blur + no_anim;
`dropwheel-(dialog|hotzone)`: no_anim only — the dialog is full-screen with its own scrim), and
`hl.on("hyprland.start", function() hl.exec_cmd("dropwheel start") end)`.
Locale fragments owned by the UI: `src/dropwheel/locales/<lang>/ui_base.json`, `ui_wheel.json`, `ui_dialogs.json`
(served to QML through `hello.strings`).
