# Dropwheel — architecture and developer guide

This document is the developer-oriented summary of Dropwheel. The binding contract (exact
APIs, protocol fields, UX numbers) is [SPEC.md](SPEC.md); when the two disagree, SPEC.md wins.
User documentation: the [README](../README.md), [CONFIG.md](CONFIG.md) and [FUNCTIONS.md](FUNCTIONS.md) in
English, each with an Italian version (`.it.md`).

## Contents

1. [What Dropwheel is](#1-what-dropwheel-is)
2. [Repository layout](#2-repository-layout)
3. [Runtime architecture](#3-runtime-architecture)
4. [Platform facts the design depends on](#4-platform-facts-the-design-depends-on)
5. [The Python backend](#5-the-python-backend)
6. [The shell ⇄ daemon protocol](#6-the-shell--daemon-protocol)
7. [The QML shell](#7-the-qml-shell)
8. [Install layout and process wiring](#8-install-layout-and-process-wiring)
9. [How to add an action](#9-how-to-add-an-action)
10. [How to add or change a translation](#10-how-to-add-or-change-a-translation)
11. [Development workflow](#11-development-workflow)
12. [Packaging and releases](#12-packaging-and-releases)

---

## 1. What Dropwheel is

A radial "drag + Shift" file-action wheel for **Arch Linux + Hyprland ≥ 0.57 (Lua config) +
Caelestia/Quickshell**, the Linux counterpart of the macOS app *Tangerine*. While the user drags
files, a Shift tap opens a wheel under the pointer; dropping the files on a segment runs an
action (convert, compress, crop, redact, extract, share, …). Outputs are written next to the
originals with Tangerine-style names and the originals are never modified.

Design principles:

- **Everything local**, built on stock Arch packages; every optional tool degrades gracefully
  (the segment is greyed out with an install hint instead of failing).
- **One registry, three front ends**: the QML wheel, the CLI and the file-manager actions all use
  the same action ids and the same menu builder.
- **No crash paths**: every subprocess has a timeout, cancellation and a readable error; QML never
  throws on missing data.

## 2. Repository layout

```
pyproject.toml                 setuptools packaging; entry point dropwheel = dropwheel.cli:main
Makefile, PKGBUILD, dropwheel.install, install.sh, uninstall.sh
README.md (English), README.it.md (Italian), docs/SPEC.md, docs/ARCHITECTURE.md, docs/CONFIG.md, docs/CONFIG.it.md, docs/FUNCTIONS.md, docs/FUNCTIONS.it.md
assets/dropwheel.svg           app icon
src/dropwheel/
  cli.py        command line (argparse)
  daemon.py     stdio JSON-lines server used by the QML shell
  shellctl.py   locate the shell dir, start/stop qs, IPC calls
  preview.py    thumbnails, page renders, metadata, waveforms (cached)
  share.py      LAN share server + QR
  config.py     config.toml loading, defaults, validation
  i18n.py       translations (locales/<lang>/*.json)
  detect.py     FileInfo + content-based classification
  tools.py      external tool discovery + Arch install hints
  registry.py   @action, Param, group(), layout(), build_menu()
  jobs.py       Context, Result, JobRunner, naming, undo/history, staging, atomic writes
  errors.py     UserError, MissingTool, ToolError, Cancelled
  util.py       clipboard, open/reveal, trash, XDG paths, desktop entries
  notify.py     desktop notifications
  locales/{it,en}/*.json
  actions/      common, image, pdf, docs, media, archive, fonts, folder, web, system, custom
shell/                         Quickshell config, always started by path (qs -p shell/)
  shell.qml                    root: triggers, IpcHandler, wheel controller
  services/                    singletons: Theme, Backend, I18n, Hypr, Jobs
  components/                  Caelestia look-alike controls (MaterialIcon, StateLayer, M3Slider…)
  wheel/  hud/  dialogs/       the wheel, the job cards, the editors (crop, redact, trim, pages…)
integrations/                  hyprland/dropwheel.lua, nautilus/, dolphin/, nemo/, thunar/, desktop/
tests/                         pytest, one test_<module>.py per module
```

## 3. Runtime architecture

```
                Shift_L / Shift_R (non_consuming, ignore_mods)
 Hyprland ───── hl.dsp.global("dropwheel:shift") ─────────┐
          ───── SUPER+ALT+D → hl.dsp.global("dropwheel:open")
                                                          ▼
                                  Quickshell shell  (qs -p <data>/shell -n -d)
                                  ├─ GlobalShortcut appid "dropwheel": shift, open
                                  ├─ hot zone (layer dropwheel-hotzone, DropArea)
                                  ├─ wheel / HUD / dialog layer surfaces
                                  └─ Process: stdin/stdout JSON lines
                                                          │
                                                          ▼
                                  python -P -m dropwheel daemon
                                  ├─ registry + build_menu
                                  ├─ JobRunner (thread pool, subprocesses)
                                  └─ previews, share server, clipboard
 CLI: dropwheel open|run|menu|doctor|start|stop|setup|config
      └─ qs ipc -p <data>/shell call dropwheel <fn> <json>   (or a headless JobRunner)
 File managers (Nautilus, Dolphin, Nemo, Thunar) ─ dropwheel open %F
```

- **Hyprland** only forwards key presses: the Shift binds call the global shortcut
  `dropwheel:shift` (press and release are both delivered), `SUPER+ALT+D` calls `dropwheel:open`.
  The Lua module also adds layer rules (blur for `dropwheel-wheel|hud`, no Hyprland
  animation for every Dropwheel layer; the full-screen dialog draws its own scrim and is never blurred) and starts Dropwheel on `hyprland.start`.
- **The QML shell** owns all UI. It decides whether a drag is active (`hyprctl -j monitors`,
  `solitaryBlockedBy` contains `"DND"`), where the cursor is (`hyprctl -j cursorpos`) and on
  which screen, then maps the wheel centred on the cursor.
- **The daemon** is a plain Python process started by the shell (`Backend.qml`), restarted with
  back-off if it exits. It never touches the UI; it answers requests and emits job events.
- **The CLI** talks to a running shell over Quickshell IPC (`IpcHandler { target: "dropwheel" }`:
  `open(json)`, `openAtCursor()`, `close()`, `setHotzone(bool)`, `reload()`, `ping()`), or runs
  actions headless with its own `JobRunner`.

### Triggers (SPEC §2.1)

| Trigger | Mode | Details |
|---|---|---|
| Shift during a drag | drag | `GlobalShortcut "shift"`; the first of pressed/released wins (the bind may be deferred to release by Hyprland's chord resolver), 250 ms debounce; drag check unless `trigger.dnd_check = false` |
| Hot zone | drag / click | always-mapped 6×6 px Overlay surface (`dropwheel-hotzone`); drag-enter opens the wheel nearby, a drop on the zone opens it in click mode |
| `dropwheel:open` | click | files from the clipboard (`clipboard_files` RPC); empty → "drop files here" target |
| IPC/CLI | click | `open('{"files":[…],"x":…,"y":…}')` |

## 4. Platform facts the design depends on

These were verified in the Hyprland 0.57 / Quickshell 0.3.2 / Qt 6.12 sources; do not change
the related code without re-checking them.

- **Hyprland 0.57 is Lua-only.** Binds are `hl.bind(keys, dispatcher, opts)`, rules are
  `hl.layer_rule{…}`, autostart is `hl.on("hyprland.start", …)` (fires once per session, not on
  reload). `hl.exec_cmd` runs through `sh -c` with output discarded.
- **Binds work during a Wayland drag-and-drop**; Escape aborts any DnD globally. There is no DnD
  event in Lua or on socket2, hence the `hyprctl -j monitors` check.
- **A layer surface mapped mid-drag receives the DnD `enter` at its centre and no motion until
  the pointer moves.** The wheel surface is therefore centred on the cursor, `onEntered`
  coordinates are ignored and hit-testing uses `onPositionChanged` / `onDropped`.
- **Drag mode needs `WlrLayershell.keyboardFocus: WlrKeyboardFocus.None`**, otherwise the overlay
  takes keyboard focus from the drag source; click mode and dialogs use `Exclusive`.
- **Drops must read the data before accepting** (`drop.urls`, then `drop.accept(Qt.CopyAction)`),
  otherwise Hyprland aborts the drag; never hide the window synchronously inside `onDropped`.
- **Hyprland may report MOVE to the drop target and never tells the source the final action:
  drops are always treated as copies and inputs are never deleted or moved.**
- `mask: Region { item: ring; shape: RegionShape.Ellipse }` limits input to the disc; drops
  outside fall through. An empty mask makes a surface click-through and drop-through.
- XWayland drag sources are not bridged into Wayland targets by Hyprland 0.57 (documented).
- `visible: false` destroys a `PanelWindow`'s surface; showing it maps a new one (wanted).
- `qs -c <name>` cannot find sub-folder configs when `~/.config/quickshell/shell.qml` exists:
  Dropwheel is always started **by path** (`qs -p`).
- Two clients registering the same global-shortcut appid+name crash the later one: the appid is
  always `"dropwheel"`.
- Quickshell IPC is filtered by `WAYLAND_DISPLAY` (since 0.3.0).
- LibreOffice headless silently does nothing when its profile is in use: always pass
  `-env:UserInstallation=file://…` with a private profile.
- Arch specifics: exiftool is `/usr/bin/vendor_perl/exiftool`, `ocrmypdf` is AUR-only, PyMuPDF 2.0
  is imported as `pymupdf`, ffmpeg 9 removed `-vsync` (use `-fps_mode`).

## 5. The Python backend

| Module | Responsibility |
|---|---|
| `config` | `load()` merges `~/.config/dropwheel/config.toml` (or `$DROPWHEEL_CONFIG`) with `DEFAULTS`; invalid values fall back to defaults and are listed in `config["_warnings"]`; unknown keys are kept for action modules; `write_default()` writes the commented file used by `dropwheel config --init` |
| `i18n` | merges `locales/<lang>/*.json` (`core.json` first, then alphabetical) plus user overrides in `~/.config/dropwheel/locales/<lang>/`; lookup: language → English → key |
| `detect` | `classify(path_or_uri) -> FileInfo` (never raises); content first (magic, animated check, ZIP family split, AppImage/ISO signatures), extension second |
| `tools` | `path()`, `have()`, `missing(requires)`, `install_hint(missing)`, `report()`; tool names map to binaries and Arch packages (extra/aur/pip); lookups cached for 10 minutes |
| `registry` | `@action`, `Param`, `group()`, `layout()` and `build_menu(files, config, lang, context)` (SPEC §3.2, §5.3) |
| `jobs` | `Context` (the API actions use), `Result`, `JobRunner` (thread pool, batching, staging of volatile inputs, atomic outputs, history and Undo, cancellation of process groups) |
| `actions/*` | the catalogue; `actions.load_all(config)` imports every module and records import errors for `dropwheel doctor` instead of failing |
| `daemon` | JSON-lines server for the shell |
| `cli` | `dropwheel …` sub-commands |
| `shellctl` | data-dir resolution, `qs` start/stop, IPC |
| `preview` | thumbnails, page renders, waveforms, metadata (cached in `$XDG_CACHE_HOME/dropwheel/previews`) |
| `share` | temporary LAN HTTP server + QR |
| `util`, `notify`, `errors` | shared helpers |

Rules for backend code: Python ≥ 3.11 (target 3.14), type hints and docstrings on public
functions, **no optional third-party imports at module level** (import Pillow, PyMuPDF, … inside
the functions that need them), argv lists only (never `shell=True`), every path may contain
spaces, unicode, newlines or a leading dash.

### Menu building

`build_menu(files, config, lang, context)` classifies the files, picks a layout key —
the category (`"image"`), `"multi:<category>"` for ≥ 2 files of one category, `"mixed"` — plus the
`"file"` page, then:

1. keeps the actions whose `categories`, `multi` and `when` accept the files and that are not in
   `hidden_actions`;
2. resolves the layout: unknown or non-applicable items are dropped, empty groups removed,
   single-item groups flattened (except Convert), rings capped at 8 with an automatic **More**;
3. marks actions with missing tools `available: false` with the install hint (or drops them when
   `wheel.show_unavailable = false`);
4. translates labels (a missing key is shown as-is, so `"PNG"` works as a label).

When no layout is registered for a key, applicable actions are grouped automatically by id prefix
(`<cat>.<group>.<leaf>` → group `<cat>.<group>` labelled `grp.<group>`).

### Jobs

`run` → `JobRunner.submit(action, files, params)` → events `queued → running → done | failed |
cancelled`. The runner validates and stages inputs, calls the action once per file for
`multi="each"` (continuing on per-file failures and aggregating "3/4 done, 1 failed"), gives each
job a temp dir, throttles progress events to ≥ 80 ms, records outputs and backups in
`$XDG_STATE_HOME/dropwheel/history.jsonl` for Undo, and kills the whole process group on cancel
(SIGTERM, SIGKILL after 3 s).

## 6. The shell ⇄ daemon protocol

UTF-8 JSON, one object per line, over the daemon's stdin/stdout (stderr is logged by the shell).
The daemon starts with `{"event":"ready","version":1}`.

```
→ {"id": 7, "method": "menu", "params": {"files": ["/home/u/foto.jpg"], "context": "drag"}}
← {"id": 7, "result": {…menu…}}
← {"id": 7, "error": {"message": "…", "details": "…", "install": "sudo pacman -S --needed ffmpeg"}}
← {"event": "job", "job": "j12", "state": "running", "progress": 0.42, …}
```

| Method | Params | Result |
|---|---|---|
| `hello` | `{}` | `{version, lang, strings, config, warnings}` |
| `menu` | `{files, context: "drag"\|"click"}` | menu JSON (SPEC §5.3) |
| `run` | `{action, files, params}` | `{job}` |
| `cancel` / `undo` | `{job}` | `{ok}` / `{ok, message}` |
| `preview` | `{kind, path, …}` | thumb, image, pdf_info, pdf_page, pdf_search, video_info, video_frames, audio_waveform, metadata, text (SPEC §5.5) |
| `apps` | `{files}` | desktop entries that open all the files |
| `util` | `{op, paths?, text?, app?}` | open, reveal, copy_files, copy_text, open_with, terminal, save_text |
| `clipboard_files` | `{}` | `{files}` |
| `share_stop` | `{id}` | `{ok}` |
| `config` | `{}` | reloaded config |
| `doctor` | `{}` | `tools.report()` |

Dialogs return structured params (SPEC §5.6): `rect` (crop), `boxes`/`terms` (redact),
`start`/`end` (trim), `pageops` (pages), `metadata`, `adjust`, `background`.

## 7. The QML shell

- `shell.qml` holds the wheel controller (`openWheel`, `closeWheel`, `openDialog`), the triggers
  and the `IpcHandler`.
- `services/` singletons: **Theme** (Caelestia `scheme.json` + `shell.json` transparency, built-in
  dark M3 fallback, tokens and animation curves), **Backend** (daemon process, `call()`,
  `jobEvent`), **I18n** (`t()` over `Backend.strings`), **Hypr** (`queryDrag`, `cursorPos`,
  `screenAt`, `watchDrag`), **Jobs** (job list, `run/cancel/undo/dismiss`).
- `wheel/` (one `PanelWindow` per open wheel, namespace `dropwheel-wheel`), `hud/`
  (`dropwheel-hud`, draggable result chips), `dialogs/` (`dropwheel-dialog`, one editor per
  `node.dialog`). Every editor has `required property var node; required property var files;
  signal apply(var params); signal cancel()`.
- Components mirror Caelestia's look (MaterialIcon, StateLayer, Anim/CAnim, Elevation, M3 controls)
  and take every colour from `Theme`; layouts must work for `wheel.scale` 0.8–1.5.

Quickshell cannot run on the development machine used for most of the code, so QML must be
written carefully (no runtime to catch typos): `pragma ComponentBehavior: Bound`, optional
chaining (`?.`, `??`) on all backend data, implicit sizes on windows. `make lint` runs `qmllint`
when it is installed.

## 8. Install layout and process wiring

| Item | System package (`PREFIX=/usr`) | User-local (`install.sh`) | Dev checkout |
|---|---|---|---|
| Python package | site-packages (`python -m installer`) | `~/.local/share/dropwheel/python/dropwheel` | `src/dropwheel` |
| `dropwheel` command | `/usr/bin/dropwheel` (entry point) | `~/.local/bin/dropwheel` wrapper (sets `PYTHONPATH`, `DROPWHEEL_DATA`) | `PYTHONPATH=src python -m dropwheel` |
| QML shell | `/usr/share/dropwheel/shell/` | `~/.local/share/dropwheel/shell/` | `shell/` |
| Integrations | `/usr/share/dropwheel/integrations/` | `~/.local/share/dropwheel/integrations/` | `integrations/` |
| Desktop entry, icon | `/usr/share/applications/`, `/usr/share/icons/hicolor/scalable/apps/` | same under `~/.local/share/` | — |
| Dolphin / Nemo actions | `/usr/share/kio/servicemenus/`, `/usr/share/nemo/actions/` | same under `~/.local/share/` (if installed) | — |

- **Data dir** (`shellctl.data_dir()`): `$DROPWHEEL_DATA` → the directory two levels above the
  imported package, when it holds `shell/shell.qml` (a checkout or the `install.sh` layout) →
  `$XDG_DATA_HOME/dropwheel` → `$XDG_DATA_DIRS/dropwheel` → `/usr/local/share/dropwheel` →
  `/usr/share/dropwheel`.
- **User data next to the program**: `$XDG_DATA_HOME/dropwheel/whisper/` (the Whisper models the
  user downloads) and `$XDG_DATA_HOME/dropwheel/wallpapers/` (the only copy of a wallpaper set
  from a volatile source) share the folder with the `install.sh` layout. Installers and
  `uninstall.sh` touch only what they installed (`python/`, `shell/`, `integrations/`,
  `uninstall.sh`, `install-manifest.txt`); these two go only with `uninstall.sh --purge`.
- **Launcher**: the `install.sh` wrapper runs `python3 -P -m dropwheel`: without `-P`, `-m` would
  put the current directory first on `sys.path`, and file managers start it from the folder being
  shown.
- **Start**: `dropwheel start` runs `qs -p <data>/shell -n -d` with
  `DROPWHEEL_BACKEND="<sys.executable> -P -m dropwheel daemon"` and the matching `PYTHONPATH`, so
  the shell always talks to the same Python package that started it. IPC uses
  `qs ipc -p <data>/shell call dropwheel <fn> <arg>`.
- **Hyprland**: `dropwheel setup hyprland` copies `integrations/hyprland/dropwheel.lua` to
  `~/.config/caelestia/dropwheel.lua` and appends `require("dropwheel")` to
  `~/.config/caelestia/hypr-user.lua` (Caelestia already puts `~/.config/caelestia/?.lua` on
  `package.path`). The module returns `{binds, rules, errors, command, set_enabled()}`; every
  Hyprland call is wrapped in `pcall`, so a bad bind never stops the rest of the user's config.
  Its launcher detection prefers `~/.local/bin/dropwheel`, because Hyprland's environment does
  not always include `~/.local/bin` in `PATH`.
- **Thunar**: Thunar reads custom actions only from `~/.config/Thunar/uca.xml`, which Caelestia
  deploys; installers never edit it. `dropwheel setup thunar --apply` merges
  `integrations/thunar/uca-action.xml` on request.
- **MIME guard** (`install.sh`): `~/.local/share/applications` comes first in the lookup order,
  so a new desktop entry listing many MIME types could become the default handler for types
  without an explicit default. The installer records `xdg-mime query default` for every listed
  type and re-pins the previous default when it changed to `dropwheel.desktop`.

## 9. How to add an action

Actions are plain functions registered with `@action`. The example adds **Invert colours** to
the image Edit group.

### 9.1 The function

In the module that owns the category (`src/dropwheel/actions/image.py` for images):

```python
from __future__ import annotations

from dropwheel.jobs import Context, Result
from dropwheel.registry import Param, action


@action(
    id="image.edit.invert",              # <category>.<group>.<leaf>: stable, used by CLI/history
    label="act.image.invert",            # i18n key (a missing key is displayed verbatim)
    icon="invert_colors",                # Material Symbols Rounded ligature
    categories={"image"},
    when=lambda files: not any(f.animated for f in files),   # animated images have their own ring
    multi="each",                        # the runner calls the function once per file
    mode="quick",                        # runs with defaults; Alt+Enter / right click edits params
    params=[
        Param("keep_alpha", "bool", label="param.keep_alpha", default=True),
    ],
    requires=["py:PIL"],                 # tools.py names; missing → greyed with install hint
    replace_ok=True,                     # adds the standard "Replace original" parameter
    description="desc.image.invert",
    order=45,
)
def invert(ctx: Context) -> Result:
    """Invert the colours of one image, keeping transparency."""
    from PIL import Image, ImageOps      # optional libraries are imported inside the function

    src = ctx.file
    out = ctx.output_path(src, suffix="sfx.inverted")        # "foto (invertito).png"
    ctx.progress(None)                                       # indeterminate progress bar
    with Image.open(src.p) as im:
        im = ImageOps.exif_transpose(im)
        alpha = im.getchannel("A") if "A" in im.getbands() and ctx.pbool("keep_alpha", True) else None
        inverted = ImageOps.invert(im.convert("RGB"))
        if alpha is not None:
            inverted.putalpha(alpha)
        ctx.check_cancel()
        with ctx.atomic(out) as tmp:                         # ".foto (invertito).png.part-…"
            inverted.save(tmp, format=im.format or "PNG")
    final = ctx.replace_original(src, out)                   # no-op unless "Replace original"
    return ctx.result([final], size_before=src.size)
```

What the runner already does for you: validation and staging of inputs, one call per file for
`multi="each"`, the temp dir (`ctx.tmpdir()`), unique output names, atomic commit, history and
Undo, cancellation, error reporting. Raise `UserError("err.some_key", details=…)` for problems the
user can fix, `MissingTool("ffmpeg")` when a tool turns out to be missing at run time; external
commands go through `ctx.run([...])` / `ctx.ffmpeg([...])`, which resolve tool names, apply
timeouts, parse progress and kill the process group on cancel.

Choosing the options:

| Option | Use |
|---|---|
| `multi` | `"each"` per-file transforms; `"single"` editors and things that only make sense for one file; `"combine"` merges (≥ 2 files, one call); `"any"` one call with all files (ZIP, checksums) |
| `mode` | `"instant"` no parameters; `"quick"` sensible defaults plus an optional parameter form; `"dialog"` needs an editor (`dialog="crop"`, `"redact"`, `"trim"`, `"pages"`, `"metadata"`, `"adjust"`, `"background"` or `"form"`) |
| `danger` | destructive or irreversible-looking actions (Trash): hover colour becomes `error` |
| `undoable` | `False` when Undo makes no sense (copy to clipboard, open a terminal) |
| `mixed_ok` | the action may appear in the auto-built ring for mixed selections |
| `text` | big text instead of an icon for format targets (`text="PNG"`) |

### 9.2 Put it on the wheel

If the category has a layout, reference the new id where it belongs (rings hold at most eight
items; the builder adds **More** automatically):

```python
from dropwheel.registry import group, layout

layout("image", [
    group("image.convert", "grp.convert", "swap_horiz", [...], default="image.convert.jpg"),
    group("image.compress", "grp.compress", "compress", [...], default="image.compress.balanced"),
    ...
    group("image.edit", "grp.edit", "edit", [
        "image.edit.rotate_cw", "image.edit.rotate_ccw", "image.edit.flip_h", "image.edit.flip_v",
        "image.edit.annotate", "image.edit.adjust", "image.edit.grayscale",
        "image.edit.invert",                 # ← new
    ]),
    ...
])
```

Ring order rule: slot 1 = Convert (or the main transform), slot 2 = Compress (or the nearest
equivalent), then by expected frequency. Without a layout for the key, the action lands in the
auto-generated group `image.edit` (from its id prefix).

### 9.3 Strings

Add the keys to **both** `src/dropwheel/locales/it/image.json` and
`src/dropwheel/locales/en/image.json`:

```json
{
  "act.image.invert": "Inverti colori",
  "desc.image.invert": "Inverte i colori mantenendo la trasparenza",
  "param.keep_alpha": "Mantieni la trasparenza",
  "sfx.inverted": "invertito"
}
```

```json
{
  "act.image.invert": "Invert colours",
  "desc.image.invert": "Invert the colours, keeping transparency",
  "param.keep_alpha": "Keep transparency",
  "sfx.inverted": "Inverted"
}
```

Conventions: Italian action labels are short imperatives ("Comprimi", "Ritaglia"); output
suffixes (`sfx.*`) are masculine singular past participles referring to "file" ("compresso",
"ritagliato", "senza metadati"); English suffixes are capitalised ("Compressed"). Ring labels
should stay within 10–12 characters.

### 9.4 Test it

Each module has its own `tests/test_<module>.py`. Run the action through the real runner so that
naming, atomic writes and history are exercised too:

```python
import pytest

from dropwheel import actions, config
from dropwheel.jobs import JobRunner


def test_invert_keeps_alpha(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    src = tmp_path / "a b.png"
    Image.new("RGBA", (4, 4), (10, 20, 30, 128)).save(src)

    cfg = config.defaults()
    actions.load_all(cfg)
    runner = JobRunner(cfg, "en", notify_fn=False)
    job = runner.run_sync("image.edit.invert", [str(src)])

    assert job.state == "done", job.error
    out = tmp_path / "a b (Inverted).png"
    with Image.open(out) as im:
        assert im.getpixel((0, 0)) == (245, 235, 225, 128)
```

Tests must not need Linux-only tools on the development machine: guard them with
`pytest.importorskip(...)` or `pytest.mark.skipif(not tools.have("ffmpeg"), ...)`.

### 9.5 Checklist

- [ ] Works with names containing spaces, unicode, newlines and a leading `-` (pass `--` or
      absolute paths to tools).
- [ ] Never writes next to the input with the same name; never deletes or modifies inputs.
- [ ] Every external command has a timeout and reports a readable error (`ToolError` keeps the
      tail of stderr in `details`).
- [ ] Long work calls `ctx.progress()` and `ctx.check_cancel()`.
- [ ] `requires=` lists every tool, so the segment is greyed (not broken) when one is missing.
- [ ] Both locales updated; `make test` and `make lint` pass.

User-level actions that do not need Python live in the configuration (`[[custom]]`, see
[CONFIG.md](CONFIG.md#custom--custom-actions)).

## 10. How to add or change a translation

**Strings.** Translations are flat JSON objects in `src/dropwheel/locales/<lang>/<module>.json`
with `{name}` placeholders. Each module owns its fragment (`core.json`, `image.json`,
`ui_wheel.json`, …); all fragments of a language are merged and sent to the shell in
`hello.strings`, so QML and Python share one table. Lookup falls back to English, then to the
key itself. Add every new key to both `it` and `en`.

Users can override any string without touching the package by dropping a JSON file in
`~/.config/dropwheel/locales/<lang>/` (for example `~/.config/dropwheel/locales/it/mine.json`
with `{"act.image.invert": "Negativo"}`).

**A new language** (for example German):

1. Create `src/dropwheel/locales/de/` with a translated copy of every fragment of `en/`.
2. Add `"de"` to `i18n.LANGS` and to the allowed values of `language` in `config.py`
   (`_SCHEMA["language"]`), and document it in `docs/CONFIG.md` and `docs/CONFIG.it.md`.
3. Translate the output suffixes (`sfx.*`) with care: they become part of file names.
4. Add the language to the built-in fallback map of `shell/services/I18n.qml` only if the shell
   must show strings before the daemon answers (normally English is enough there).
5. Add a test that every key of `en` exists in the new language.

## 11. Development workflow

```sh
PYTHONPATH=src python -m dropwheel doctor          # tools, dependencies, import errors
PYTHONPATH=src python -m dropwheel start           # runs ./shell with this checkout's backend
qs log -p shell -f                                 # shell + daemon log
qs ipc -p shell call dropwheel ping                # → pong
qs ipc -p shell call dropwheel open '{"files":["/home/u/foto.jpg"]}'
PYTHONPATH=src python -m dropwheel daemon          # talk to the daemon by hand:
{"id":1,"method":"hello","params":{}}
{"id":2,"method":"menu","params":{"files":["/home/u/foto.jpg"],"context":"click"}}
make test                                          # pytest (Linux-only parts skip elsewhere)
make lint                                          # compileall, bash -n, luac, XML, desktop-file-validate, qmllint
```

`DROPWHEEL_DATA` points the CLI at another shell/integrations directory; `DROPWHEEL_CONFIG` at
another config file. Quickshell normally reloads QML files on save while the shell runs; the
daemon must be restarted (`dropwheel stop && dropwheel start`) after Python changes.

## 12. Packaging and releases

| File | Purpose |
|---|---|
| `Makefile` | `wheel` (`python -m build --wheel --no-isolation`), `install` (`python -m installer` + data files; `PREFIX`, `DESTDIR`), `install-user`, `uninstall`, `test`, `lint`, `dist`, `clean` |
| `PKGBUILD` | builds from the local checkout (`source=()`; `prepare()` copies the tree into `$srcdir`); a commented git source variant is included. It redirects makepkg's `BUILDDIR` to `.makepkg/` because `src/` is the Python source tree |
| `dropwheel.install` | pacman messages (run `dropwheel setup hyprland` and `dropwheel start` as the user) |
| `install.sh` / `uninstall.sh` | user-local install in `~/.local` with a manifest, dependency report via `pacman -Qq`, MIME guard, optional Hyprland setup; uninstall removes the `require` line (with a backup) and keeps the config unless `--purge` |

Release checklist:

1. Bump the version in `pyproject.toml`, `src/dropwheel/__init__.py` (`__version__`) and
   `PKGBUILD` (`pkgver`, reset `pkgrel=1`).
2. `make lint test`.
3. `makepkg -f` in a clean checkout and `namcap dropwheel-*.pkg.tar.zst`.
4. `make dist` for the source tarball; tag `v<version>`.

## Regenerating the function reference

`docs/FUNCTIONS.md` and `docs/FUNCTIONS.it.md` list every action per file type. They are generated from the real menus, so
regenerate them after changing actions, layouts or labels:

```sh
PYTHONPATH=src python tools/gen_functions_doc.py
```
