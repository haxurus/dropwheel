# Dropwheel configuration

**English** · [Italiano](CONFIG.it.md)

This document describes **every** Dropwheel setting. For general use, see the
[README](../README.md).

- **File:** `~/.config/dropwheel/config.toml` (more precisely
  `$XDG_CONFIG_HOME/dropwheel/config.toml`). The `DROPWHEEL_CONFIG` environment variable points to a
  different file.
- **Format:** [TOML](https://toml.io/en/). The file is optional: every missing key has a default.
- **Commented starting file** (Italian and English):

  ```sh
  dropwheel config --init
  ```

- **When it applies:** the configuration is read again each time the wheel opens, so a restart is
  rarely needed. If a visual change (the hot corner, for example) does not show up straight away,
  restart with `dropwheel stop && dropwheel start`.
- **Errors:** an invalid value (wrong type, out of range, unknown word) is replaced by the default and
  reported with a warning. An unreadable TOML file means all defaults are used. A bad configuration
  never stops Dropwheel.
- **Unknown keys** are kept as they are, because actions can read additional options of their own.

> **Mind the order in TOML.** Top-level keys (`language`, `hidden_actions`) must come **before** the
> first `[...]` section. A key written after `[share]`, for example, belongs to `[share]` and is not
> read as a general setting.

## Contents

| Section | Purpose |
|---|---|
| [top level](#top-level) | `language`, `hidden_actions` |
| [`[output]`](#output) | where results are saved |
| [`[trigger]`](#trigger) | Shift during a drag |
| [`[hotzone]`](#hotzone) | the hot corner |
| [`[wheel]`](#wheel) | look and behaviour of the wheel |
| [`[hud]`](#hud) | the job cards |
| [`[notifications]`](#notifications) | desktop notifications |
| [`[jobs]`](#jobs) | parallel jobs |
| [`[image]`](#image) | image quality and metadata |
| [`[video]`](#video) | video encoding |
| [`[audio]`](#audio) | audio encoding |
| [`[pdf]`](#pdf) | PDF resolution and compression |
| [`[ocr]`](#ocr) | text recognition languages |
| [`[whisper]`](#whisper) | model for audio transcription |
| [`[apps]`](#apps) | preferred programs |
| [`[share]`](#share) | LAN sharing with a QR code |
| [`[[custom]]`](#custom--custom-actions) | custom actions |
| [custom text](#custom-text) | changing the wheel's labels |

---

## Top level

### `language`

| Type | Default | Values |
|---|---|---|
| string | `"auto"` | `"auto"`, `"it"`, `"en"` |

Language of the interface and of the names of produced files (`photo (Compressed).jpg` or
`foto (compresso).jpg`). With `"auto"`, Dropwheel looks at `LC_ALL`, then `LC_MESSAGES`, then `LANG`:
if they start with `it` it uses Italian, otherwise English.

```toml
language = "en"
```

### `hidden_actions`

| Type | Default |
|---|---|
| list of strings | `[]` |

Actions to remove from the wheel. Each item is an action id or a pattern with `*` and `?`. Ids are
stable and have the form `category.group.action`: you can see them with
`dropwheel menu --json FILE` (the `id` field). Groups left empty disappear by themselves.

```toml
hidden_actions = [
  "image.background.*",      # for example, the whole image Backdrop group
  "pdf.convert.epub",        # or a single action
]
```

---

## `[output]`

| Key | Type | Default | Values |
|---|---|---|---|
| `location` | string | `"source"` | `"source"`, `"folder"` |
| `folder` | string | `""` | a path; `~` and variables such as `$HOME` are expanded |
| `open_after` | boolean | `false` | |

- **`location`**: `"source"` saves the result next to the original; `"folder"` always saves it in
  `folder`.
- **`folder`**: the folder used with `location = "folder"` and, in any case, when the source folder is
  read-only or temporary (browser downloads, `/tmp`, files shared by Flatpak apps). Empty means
  **Downloads/Dropwheel** (the `xdg-user-dirs` Downloads folder, for example
  `~/Downloads/Dropwheel`).
- **`open_after`**: automatically opens the result with the default application.

Names never overwrite an existing file (`photo 2.jpg`, `photo 3.jpg`, ...) and never match the
original.

```toml
[output]
location = "folder"
folder = "~/Documents/Converted"
open_after = true
```

---

## `[trigger]`

| Key | Type | Default |
|---|---|---|
| `shift` | boolean | `true` |
| `dnd_check` | boolean | `true` |

- **`shift`**: `false` ignores Shift (the hot corner, SUPER+ALT+D, the file manager and the command
  line remain). To stop Shift from reaching Dropwheel at all, you can also empty `SHIFT_KEYS` in the
  Lua module (see the README).
- **`dnd_check`**: before opening the wheel, Dropwheel uses `hyprctl -j monitors` to check that a drag
  is really in progress, so Shift does not open the wheel while you type. With `false`, the wheel opens
  on every Shift and closes by itself after 1.2 seconds if no drag arrives. This is useful only for
  diagnosing problems.

---

## `[hotzone]`

| Key | Type | Default | Values |
|---|---|---|---|
| `enabled` | boolean | `true` | |
| `corner` | string | `"top-right"` | `"top-left"`, `"top-right"`, `"bottom-left"`, `"bottom-right"`, `"top"`, `"bottom"`, `"left"`, `"right"` |
| `size` | integer | `6` | `1` to `64` (pixels) |

The hot corner is a small transparent area that is always present on the edge of the screen. Dragging
files over it opens the wheel nearby; dropping them directly on the area opens the wheel in click
mode. With an edge (`"top"`, `"left"`, ...), the area sits in the middle of that edge.

```toml
[hotzone]
corner = "bottom-left"
size = 10
```

---

## `[wheel]`

| Key | Type | Default | Values |
|---|---|---|---|
| `scale` | decimal | `1.0` | `0.5` to `2.5` (`0.8` to `1.5` recommended) |
| `spring_ms` | integer | `450` | `100` to `3000` (milliseconds) |
| `show_unavailable` | boolean | `true` | |
| `sound` | boolean | `false` | |

- **`scale`**: size of the wheel (at `1.0` the outer radius is 132 pixels).
- **`spring_ms`**: how long to rest on a group during a drag before its ring opens. It also applies to
  the Back centre.
- **`show_unavailable`**: `true` greys out actions whose program is not installed and shows the command
  that installs it; `false` hides them.
- **`sound`**: a short sound when the highlighted segment changes.

---

## `[hud]`

| Key | Type | Default | Values |
|---|---|---|---|
| `position` | string | `"bottom-right"` | `"top-left"`, `"top-right"`, `"bottom-left"`, `"bottom-right"`, `"top"`, `"bottom"`, `"left"`, `"right"` |
| `timeout_ms` | integer | `7000` | `0` to `600000`; `0` = never close |

Position of the job cards, and the time after which a **completed** card closes by itself (the
countdown pauses while the pointer is over the card). Cards with an error stay until you close them.

---

## `[notifications]`

| Key | Type | Default | Values |
|---|---|---|---|
| `mode` | string | `"long"` | `"never"`, `"long"`, `"always"` |

Desktop notifications (through `notify-send`, with the Open and Show in folder actions): `"never"`,
`"long"` for jobs lasting more than 10 seconds or when the card is not visible, `"always"` for every
job.

---

## `[jobs]`

| Key | Type | Default | Values |
|---|---|---|---|
| `max_parallel` | integer | `2` | `1` to `16` |

How many jobs can run at once. The others wait in the queue, and the card says so. Video and Office
documents are heavy, so raise this only if you have many cores.

---

## `[image]`

| Key | Type | Default | Values |
|---|---|---|---|
| `jpeg_quality` | integer | `85` | `1` to `100` |
| `webp_quality` | integer | `80` | `1` to `100` |
| `avif_quality` | integer | `60` | `1` to `100` |
| `keep_metadata` | boolean | `true` | |

- The qualities apply to **conversions** to that format. The Compress presets have their own values:
  Light about 90, Balanced about 80, Strong about 65.
- **`keep_metadata`**: keeps EXIF, XMP and the colour profile in conversions. The For web preset and the
  actions of the Privacy group still remove the GPS location or all metadata.

---

## `[video]`

| Key | Type | Default | Values |
|---|---|---|---|
| `crf` | integer | `23` | `0` to `63`; lower = higher quality, larger file |
| `preset` | string | `"medium"` | `"ultrafast"`, `"superfast"`, `"veryfast"`, `"faster"`, `"fast"`, `"medium"`, `"slow"`, `"slower"`, `"veryslow"` |
| `hwaccel` | string | `"auto"` | `"auto"`, `"vaapi"`, `"nvenc"`, `"none"` |

- **`crf`** and **`preset`** apply to conversions to H.264 MP4 (the Compress presets have their own
  values). For H.264, a CRF between 18 and 28 is the useful range, and +6 roughly halves the size.
- **`hwaccel`**: GPU encoding. `"auto"` uses it only when it is available and reliable, `"vaapi"`
  (Intel/AMD) and `"nvenc"` (NVIDIA) force it, and `"none"` uses only the CPU (slower, with better
  quality for the same size).

---

## `[audio]`

| Key | Type | Default | Values |
|---|---|---|---|
| `mp3_quality` | integer | `2` | `0` (best) to `9` (worst), LAME VBR quality |
| `opus_bitrate` | string | `"128k"` | a number followed by `k`, for example `"96k"` |

---

## `[pdf]`

| Key | Type | Default | Values |
|---|---|---|---|
| `dpi` | integer | `150` | `36` to `1200` |
| `compress` | string | `"ebook"` | `"screen"`, `"ebook"`, `"printer"`, `"prepress"`, `"default"` |

- **`dpi`**: default resolution for exporting pages as images and for high-quality previews.
- **`compress`**: Ghostscript preset used by Compress: `"screen"` (72 dpi, very small files), `"ebook"`
  (150 dpi, a good compromise), `"printer"` and `"prepress"` (do not reduce image resolution),
  `"default"` (closest to the original).

---

## `[ocr]`

| Key | Type | Default | Values |
|---|---|---|---|
| `languages` | string | `"ita+eng"` | Tesseract codes joined by `+` |

The languages must be installed: `tesseract-data-eng`, `tesseract-data-ita`, `tesseract-data-deu`, and
so on (`tesseract --list-langs` shows those present).

```toml
[ocr]
languages = "eng+deu"
```

---

## `[whisper]`

| Key | Type | Default | Values |
|---|---|---|---|
| `model` | string | `""` | path of a `ggml-*.bin` model, or a name such as `"base"` or `"small"` |

The [whisper.cpp](https://github.com/ggml-org/whisper.cpp) model used by **Audio, Transcribe** and by
**Video, More, Subtitles, Generate**. Empty means `~/.local/share/dropwheel/whisper/ggml-base.bin`,
otherwise the first `ggml-*.bin` model found. If there is no model, the job card shows the command to
download one. The old `whisper_model` key in the `[audio]` section is also accepted. Uninstalling
leaves models in place, except with `uninstall.sh --purge`.

```toml
[whisper]
model = "~/.local/share/dropwheel/whisper/ggml-small.bin"
```

---

## `[apps]`

| Key | Type | Default |
|---|---|---|
| `terminal` | string | `""` |
| `file_manager` | string | `""` |
| `image_editor` | string | `""` |
| `pdf_editor` | string | `""` |

Programs to use for Terminal here, Show in folder, opening an image in an editor, and **PDF, Annotate**.
Empty means automatic detection: for the terminal, the first one installed among foot, kitty, alacritty,
wezterm, ghostty, konsole, gnome-terminal, xfce4-terminal and xterm; for the file manager, the system
default; for PDF, Xournal++ and then Okular. Give the command name. `file_manager` also decides the
format in which **Copy** puts files on the clipboard, because Thunar, Nautilus and Nemo paste from
`x-special/gnome-copied-files` and Dolphin from `text/uri-list`.

```toml
[apps]
terminal = "foot"
file_manager = "thunar"
image_editor = "gimp"
pdf_editor = "okular"
```

---

## `[share]`

| Key | Type | Default | Values |
|---|---|---|---|
| `minutes` | integer | `15` | `1` to `1440` |
| `port` | integer | `0` | `0` to `65535`; `0` = random port |
| `localsend_target` | string | `""` | alias or IP address of the LocalSend device to suggest |

Settings for **Share, To phone**: a temporary HTTP server on the local network offers only the chosen
files, at a random address, and stops by itself after `minutes` minutes or when you close the card. Set
`port` only if your firewall has to open a specific port. `localsend_target` prefills the receiver of
**Share, LocalSend** (empty = type it each time).

---

## `[[custom]]` — custom actions

Each `[[custom]]` block adds an action to the wheel for files of the given categories.

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `id` | string | yes | | unique id: letters, digits, `_`, `.`, `-` (for example `"blog-resize"`) |
| `label` | string | no | the `id` | text shown on the segment; shorter is better (12 characters or fewer) |
| `icon` | string | no | `"terminal"` | name of a [Material Symbols](https://fonts.google.com/icons) icon |
| `categories` | list or string | no | `[]` = all | file categories it applies to (see below); `"*"` means all |
| `command` | string or list | yes | | the command to run, with placeholders |
| `multi` | string | no | `"each"` | how to treat several files (see below) |
| `output` | string | no | `"none"` | what to show at the end (see below) |

### Placeholders in the command

| Placeholder | Value |
|---|---|
| `{file}` | full path of the file |
| `{files}` | all files (with `multi = "combine"` or `"any"`), each as a separate argument |
| `{dir}` | folder containing the file |
| `{name}` | file name with extension (`photo.jpg`) |
| `{stem}` | file name without extension (`photo` for `photo.jpg`) |
| `{ext}` | lower-case extension, without the dot (`jpg`) |
| `{out}` | results folder: next to the original, `[output] folder` or `~/Downloads/Dropwheel` |

Paths are always absolute, so a name starting with a dash is not mistaken for an option. `{{` and `}}`
produce literal `{` and `}` braces.

A command written as a **string** runs through `sh -c`, so pipes (`|`), redirections (`>`) and `&&`
work. File names never become shell text: each placeholder is replaced by a reference to an environment
variable, with the right quoting for where it appears (`{file}` becomes `"${DROPWHEEL_FILE}"`), so names
containing spaces, apostrophes, `$` or `;` are safe everywhere, even inside `"$(command {file})"` and
backticks. `${file}` with the dollar sign stays a normal shell variable. Inside `$(( ))`, only the
leading digits of the value are used.

Be careful with programs that receive **code** as an argument and interpret it again
(`sh -c '... {file}'`, `eval`, `python -c`, `ssh host "..."`), because there the name would be
interpreted. Use the variables directly instead, for example `sh -c 'cat "$DROPWHEEL_FILE"'`. Every
command receives `DROPWHEEL_FILE`, `DROPWHEEL_DIR`, `DROPWHEEL_NAME`, `DROPWHEEL_STEM`, `DROPWHEEL_EXT`,
`DROPWHEEL_OUT`, `DROPWHEEL_LANG` and `DROPWHEEL_FILES` (one path per line, omitted above 128 KiB) in
its environment.

A command written as a **list** runs directly, without a shell: each item is an argument, and an item
equal to `{files}` becomes one argument per file.

A `NN%` line on standard output updates the progress bar of the card.

### `multi`

| Value | Meaning |
|---|---|
| `"each"` | the command runs once per file (1 to N files) |
| `"single"` | the action appears only when you drag exactly one file |
| `"combine"` | the action appears with at least two files; the command runs once with all of them (`{files}`) |
| `"any"` | a single run with all the files, even if there is only one |

### `output`

| Value | What happens at the end |
|---|---|
| `"none"` (or empty) | the card shows only the outcome (success, or an error with the command's error output) |
| `"text"` | what the command writes to standard output becomes a text result, with **Copy** and **Expand** |
| `"files"` | the **new** files that appeared in `{dir}` (and in `{out}`, if different) during the run become the result, with preview, drag and **Open**. **Undo** is not available: all that is known about a new file is that it appeared while the command was running, and the same is true of a download that just finished in that folder, which would end up in the trash. |

A command that exits with a non-zero code is shown as an error, with the end of its error output in the
details. Commands can be stopped with the card's ✕.

### Categories

`image`, `svg`, `pdf`, `document`, `spreadsheet`, `presentation`, `markdown`, `text`, `code`,
`data`, `ebook`, `subtitle`, `audio`, `video`, `archive`, `font`, `folder`, `url`, `appimage`,
`disk`, `package`, `unknown`.

The category depends on the file's **content**, not only its extension: a CSV is `spreadsheet`, a
JSON/YAML/TOML/XML file is `data`, and an animated GIF stays `image`.

### Examples

```toml
# Resize for a blog (ImageMagick): creates "photo-blog.jpg" next to the original.
[[custom]]
id = "blog-resize"
label = "For the blog"
icon = "web"
categories = ["image"]
command = "magick {file} -resize '1600x1600>' -quality 80 {dir}/{stem}-blog.jpg"
multi = "each"
output = "files"

# Upload to a server with scp (once per file).
[[custom]]
id = "upload-server"
label = "To server"
icon = "cloud_upload"
categories = ["image", "pdf", "video"]
command = "scp {file} user@server.lan:/srv/inbox/"

# Count the words of a PDF and show the result as text (the string goes through sh -c, so the pipe works).
[[custom]]
id = "pdf-words"
label = "Words"
icon = "functions"
categories = "pdf"
multi = "single"
command = "pdftotext {file} - | wc -w"
output = "text"

# Open all selected files in GIMP, in a single window.
[[custom]]
id = "gimp"
label = "GIMP"
icon = "brush"
categories = ["image"]
command = "gimp {files}"
multi = "any"

# Append several videos with mkvmerge into "joined.mkv" (mkvmerge's "[ file... ]" syntax
# joins the files one after the other).
[[custom]]
id = "mkv-join"
label = "Join MKV"
icon = "movie"
categories = ["video"]
multi = "combine"
command = "mkvmerge -o {dir}/joined.mkv [ {files} ]"
output = "files"

# For any file: compute the BLAKE2 hash and show it.
[[custom]]
id = "b2sum"
label = "BLAKE2"
icon = "fingerprint"
command = ["b2sum", "{file}"]
multi = "single"
output = "text"
```

Custom actions with configuration errors (missing `id` or `command`, duplicate `id`, unknown `multi`,
non-existent category) are ignored or corrected with a warning. The others keep working.

---

## Custom text

Every text of the wheel, cards and dialogs can be changed without modifying Dropwheel. Create a JSON
file in `~/.config/dropwheel/locales/<language>/` (for example
`~/.config/dropwheel/locales/en/mine.json`) with the keys to replace:

```json
{
  "grp.background": "Background",
  "sfx.compressed": "Smaller"
}
```

The keys are those of the files in `src/dropwheel/locales/en/` (for example `grp.*` for groups and
`sfx.*` for the suffixes of produced files, as in `photo (Smaller).jpg`). Your files are read after
Dropwheel's and take priority.

---

## Complete file with the default values

```toml
language = "auto"
hidden_actions = []

[output]
location = "source"
folder = ""
open_after = false

[trigger]
shift = true
dnd_check = true

[hotzone]
enabled = true
corner = "top-right"
size = 6

[wheel]
scale = 1.0
spring_ms = 450
show_unavailable = true
sound = false

[hud]
position = "bottom-right"
timeout_ms = 7000

[notifications]
mode = "long"

[jobs]
max_parallel = 2

[image]
jpeg_quality = 85
webp_quality = 80
avif_quality = 60
keep_metadata = true

[video]
crf = 23
preset = "medium"
hwaccel = "auto"

[audio]
mp3_quality = 2
opus_bitrate = "128k"

[pdf]
dpi = 150
compress = "ebook"

[ocr]
languages = "ita+eng"

[whisper]
model = ""

[apps]
terminal = ""
file_manager = ""
image_editor = ""
pdf_editor = ""

[share]
minutes = 15
port = 0
localsend_target = ""
```

## Other Dropwheel files

| Path | Contents |
|---|---|
| `~/.config/dropwheel/config.toml` | this configuration |
| `~/.local/state/dropwheel/history.jsonl` | job history, used by **Undo** |
| `~/.cache/dropwheel/` | previews, temporary copies of files, private LibreOffice profiles |
| `~/.config/caelestia/dropwheel.lua` | the Hyprland module (shortcuts, layer rules, autostart) |

Colours come from Caelestia (`~/.local/state/caelestia/scheme.json`) and transparency from
`~/.config/caelestia/shell.json`, so changing theme in Caelestia also changes Dropwheel, with nothing to
configure.
