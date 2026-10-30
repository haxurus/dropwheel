"""Configuration: ``~/.config/dropwheel/config.toml`` (TOML, read with :mod:`tomllib`).

:func:`load` returns the defaults merged with the user file.  Invalid values are replaced by
their default and reported in ``config["_warnings"]`` (translated messages); a broken file
yields the defaults plus a warning.  Unknown keys are preserved untouched so that action modules
can read their own extra options.  :func:`write_default` writes a fully commented file
(Italian + English); :func:`public_config` is what the UI receives.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Callable

from . import i18n, util

log = logging.getLogger("dropwheel.config")

__all__ = ["DEFAULTS", "config_path", "load", "defaults", "get", "public_config", "default_toml",
           "write_default", "output_folder"]

DEFAULTS: dict[str, Any] = {
    "language": "auto",
    "hidden_actions": [],
    "output": {"location": "source", "folder": "", "open_after": False},
    "trigger": {"shift": True, "dnd_check": True},
    "hotzone": {"enabled": True, "corner": "top-right", "size": 6},
    "wheel": {"scale": 1.0, "spring_ms": 450, "show_unavailable": True, "sound": False},
    "hud": {"position": "bottom-right", "timeout_ms": 7000},
    "notifications": {"mode": "long"},
    "jobs": {"max_parallel": 2},
    "image": {"jpeg_quality": 85, "webp_quality": 80, "avif_quality": 60, "keep_metadata": True},
    "video": {"crf": 23, "preset": "medium", "hwaccel": "auto"},
    "audio": {"mp3_quality": 2, "opus_bitrate": "128k"},
    "pdf": {"dpi": 150, "compress": "ebook"},
    "ocr": {"languages": "ita+eng"},
    "whisper": {"model": ""},
    "apps": {"terminal": "", "file_manager": "", "image_editor": "", "pdf_editor": ""},
    "share": {"minutes": 15, "port": 0, "localsend_target": ""},
    "custom": [],
}

_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right", "top", "bottom", "left", "right")
_X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")

# dotted key -> (kind, constraint)
_SCHEMA: dict[str, tuple[str, Any]] = {
    "language": ("enum", ("auto", "it", "en")),
    "hidden_actions": ("strlist", None),
    "output.location": ("enum", ("source", "folder")),
    "output.folder": ("str", None),
    "output.open_after": ("bool", None),
    "trigger.shift": ("bool", None),
    "trigger.dnd_check": ("bool", None),
    "hotzone.enabled": ("bool", None),
    "hotzone.corner": ("enum", _CORNERS),
    "hotzone.size": ("int", (1, 64)),
    "wheel.scale": ("float", (0.5, 2.5)),
    "wheel.spring_ms": ("int", (100, 3000)),
    "wheel.show_unavailable": ("bool", None),
    "wheel.sound": ("bool", None),
    "hud.position": ("enum", _CORNERS),
    "hud.timeout_ms": ("int", (0, 600000)),
    "notifications.mode": ("enum", ("never", "long", "always")),
    "jobs.max_parallel": ("int", (1, 16)),
    "image.jpeg_quality": ("int", (1, 100)),
    "image.webp_quality": ("int", (1, 100)),
    "image.avif_quality": ("int", (1, 100)),
    "image.keep_metadata": ("bool", None),
    "video.crf": ("int", (0, 63)),
    "video.preset": ("enum", _X264_PRESETS),
    "video.hwaccel": ("enum", ("auto", "vaapi", "nvenc", "none")),
    "audio.mp3_quality": ("int", (0, 9)),
    "audio.opus_bitrate": ("regex", r"^\d{1,3}k$"),
    "pdf.dpi": ("int", (36, 1200)),
    "pdf.compress": ("enum", ("screen", "ebook", "printer", "prepress", "default")),
    "ocr.languages": ("regex", r"^[A-Za-z_]+(\+[A-Za-z_]+)*$"),
    "whisper.model": ("str", None),
    "apps.terminal": ("str", None),
    "apps.file_manager": ("str", None),
    "apps.image_editor": ("str", None),
    "apps.pdf_editor": ("str", None),
    "share.minutes": ("int", (1, 1440)),
    "share.port": ("int", (0, 65535)),
    "share.localsend_target": ("str", None),
    "custom": ("custom", None),
}

_CUSTOM_MULTI = ("single", "each", "combine", "any")
_CUSTOM_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def config_path() -> Path:
    """``$DROPWHEEL_CONFIG`` or ``$XDG_CONFIG_HOME/dropwheel/config.toml``."""
    env = os.environ.get("DROPWHEEL_CONFIG", "")
    if env:
        return Path(os.path.expanduser(env))
    return util.config_dir() / "config.toml"


def defaults() -> dict[str, Any]:
    """A deep copy of :data:`DEFAULTS`."""
    return copy.deepcopy(DEFAULTS)


def _get_dotted(data: dict, dotted: str) -> tuple[bool, Any]:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _set_dotted(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def get(config: dict | None, dotted: str, default: Any = None) -> Any:
    """Read ``"image.jpeg_quality"`` style keys from a config dict (falls back to :data:`DEFAULTS`)."""
    if config:
        found, value = _get_dotted(config, dotted)
        if found:
            return value
    found, value = _get_dotted(DEFAULTS, dotted)
    if found:
        return copy.deepcopy(value)
    return default


def _check(kind: str, constraint: Any, value: Any) -> tuple[bool, Any]:
    """Validate one value; returns ``(ok, normalised_value)``."""
    if kind == "bool":
        return (isinstance(value, bool), value)
    if kind == "int":
        if isinstance(value, bool):
            return False, value
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int):
            return False, value
        lo, hi = constraint
        return (lo <= value <= hi, value)
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, value
        lo, hi = constraint
        value = float(value)
        return (lo <= value <= hi, value)
    if kind == "enum":
        if not isinstance(value, str):
            return False, value
        low = value.strip().lower()
        return (low in constraint, low)
    if kind == "str":
        return (isinstance(value, str), value)
    if kind == "regex":
        return (isinstance(value, str) and re.match(constraint, value.strip()) is not None,
                value.strip() if isinstance(value, str) else value)
    if kind == "strlist":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return False, value
        return True, [v.strip() for v in value if v.strip()]
    return True, value


def _validate_custom(entries: Any, warn: Callable[..., None]) -> list[dict]:
    from .detect import CATEGORIES

    if not isinstance(entries, list):
        warn("cfg.invalid", key="custom", value=type(entries).__name__, default="[]")
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            warn("cfg.custom_invalid", index=index, reason="not a table")
            continue
        ident = entry.get("id")
        command = entry.get("command")
        if not isinstance(ident, str) or not _CUSTOM_ID_RE.match(ident):
            warn("cfg.custom_invalid", index=index, reason="id")
            continue
        if ident in seen:
            warn("cfg.custom_invalid", index=index, reason=f"duplicate id {ident}")
            continue
        if isinstance(command, list):
            if not command or not all(isinstance(c, str) for c in command):
                warn("cfg.custom_invalid", index=index, reason="command")
                continue
        elif not isinstance(command, str) or not command.strip():
            warn("cfg.custom_invalid", index=index, reason="command")
            continue
        clean = dict(entry)
        label = entry.get("label", ident)
        clean["label"] = label if isinstance(label, str) and label.strip() else ident
        icon = entry.get("icon", "terminal")
        clean["icon"] = icon if isinstance(icon, str) and icon.strip() else "terminal"
        cats = entry.get("categories", [])
        if isinstance(cats, str):
            cats = [cats]
        if not isinstance(cats, list):
            warn("cfg.custom_invalid", index=index, reason="categories")
            cats = []
        good = []
        for c in cats:
            if isinstance(c, str) and (c in CATEGORIES or c in ("*", "any", "all")):
                good.append(c)
            else:
                warn("cfg.custom_invalid", index=index, reason=f"category {c!r}")
        clean["categories"] = good
        multi = entry.get("multi", "each")
        if multi not in _CUSTOM_MULTI:
            warn("cfg.custom_invalid", index=index, reason=f"multi {multi!r}")
            multi = "each"
        clean["multi"] = multi
        output = entry.get("output", "")
        clean["output"] = output if isinstance(output, str) else ""
        seen.add(ident)
        out.append(clean)
    return out


def _merge_unknown(target: dict, source: dict) -> None:
    """Copy keys of ``source`` that the schema does not know (kept as-is for action modules)."""
    for key, value in source.items():
        if key.startswith("_"):
            continue
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            for sub, sv in value.items():
                if sub not in target[key]:
                    target[key][sub] = copy.deepcopy(sv)


def load(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load the configuration (defaults merged, invalid values replaced).

    The result also contains ``_warnings`` (list of translated messages) and ``_path``.
    """
    cfg_path = Path(path) if path is not None else config_path()
    raw_warnings: list[tuple[str, dict]] = []

    def warn(msg_key: str, /, **fmt: Any) -> None:
        raw_warnings.append((msg_key, fmt))

    data: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            with open(cfg_path, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            warn("cfg.parse_error", error=str(exc), path=str(cfg_path))
            data = {}
        except OSError as exc:
            warn("cfg.read_error", error=str(exc), path=str(cfg_path))
            data = {}
    result = defaults()
    for dotted, (kind, constraint) in _SCHEMA.items():
        found, value = _get_dotted(data, dotted)
        if not found:
            continue
        if kind == "custom":
            result["custom"] = _validate_custom(value, warn)
            continue
        ok, norm = _check(kind, constraint, value)
        if ok:
            _set_dotted(result, dotted, norm)
        else:
            _found, default_value = _get_dotted(DEFAULTS, dotted)
            warn("cfg.invalid", key=dotted, value=repr(value), default=repr(default_value))
    for section in DEFAULTS:
        if section in data and isinstance(DEFAULTS[section], dict) and not isinstance(data[section], dict):
            warn("cfg.invalid", key=section, value=repr(data[section]), default="{…}")
    _merge_unknown(result, {k: v for k, v in data.items()
                            if not (k in DEFAULTS and isinstance(DEFAULTS[k], dict) and not isinstance(v, dict))})
    lang = i18n.detect_lang(result)
    result["_warnings"] = [i18n.t(key, lang, **fmt) for key, fmt in raw_warnings]
    result["_path"] = str(cfg_path)
    for key, fmt in raw_warnings:
        log.warning("config: %s %s", key, fmt)
    return result


def output_folder(config: dict | None) -> Path:
    """The folder used when ``output.location = "folder"`` (``""`` → ``<Downloads>/Dropwheel``)."""
    value = str(get(config, "output.folder", "") or "").strip()
    if not value:
        return util.fallback_output_dir()
    return Path(os.path.expandvars(os.path.expanduser(value)))


def public_config(config: dict | None = None) -> dict[str, Any]:
    """Configuration for the UI: a deep copy without private ``_`` keys, plus resolved helpers."""
    cfg = config if config is not None else load()
    out = {k: copy.deepcopy(v) for k, v in cfg.items() if not str(k).startswith("_")}
    out["language_resolved"] = i18n.detect_lang(cfg)
    out.setdefault("output", {})
    if isinstance(out["output"], dict):
        out["output"]["folder_resolved"] = str(output_folder(cfg))
    return out


_DEFAULT_TOML = '''\
# Dropwheel — configurazione / configuration
# File: ~/.config/dropwheel/config.toml   (ricaricato a ogni apertura della ruota / reloaded when the wheel opens)
# Valori non validi vengono sostituiti dal default con un avviso.
# Invalid values are replaced by their default and reported as a warning.

# Lingua dell'interfaccia: "auto" (dal sistema), "it", "en"
# UI language: "auto" (from the system locale), "it", "en"
language = "auto"

# Azioni da nascondere (id o modelli glob, es. "image.background.*")
# Actions to hide (ids or glob patterns, e.g. "image.background.*")
hidden_actions = []

[output]
# Dove salvare i risultati: "source" = accanto all'originale, "folder" = sempre nella cartella sotto
# Where results go: "source" = next to the original, "folder" = always in the folder below
location = "source"
# Cartella per location = "folder" (vuoto = Scaricati/Dropwheel). Usata anche quando l'origine non è scrivibile.
# Folder for location = "folder" (empty = Downloads/Dropwheel). Also used when the source is not writable.
folder = ""
# Apri il risultato al termine / open the result when done
open_after = false

[trigger]
# Tocca Shift durante un trascinamento per aprire la ruota / tap Shift while dragging to open the wheel
shift = true
# Controlla che un trascinamento sia davvero in corso (hyprctl) / check that a drag is really active (hyprctl)
dnd_check = true

[hotzone]
# Piccola zona sensibile sul bordo dello schermo / tiny drop zone on the screen edge
enabled = true
# Angolo o bordo: top-left, top-right, bottom-left, bottom-right, top, bottom, left, right
# Corner or edge: top-left, top-right, bottom-left, bottom-right, top, bottom, left, right
corner = "top-right"
# Dimensione in pixel (1-64) / size in pixels (1-64)
size = 6

[wheel]
# Scala della ruota (0.5-2.5) / wheel scale (0.5-2.5)
scale = 1.0
# Attesa prima di aprire un sottomenu passandoci sopra durante il trascinamento (ms)
# Hover delay before a group opens while dragging (ms)
spring_ms = 450
# Mostra in grigio le azioni che richiedono programmi non installati (false = nascondile)
# Show actions whose tools are missing greyed out (false = hide them)
show_unavailable = true
# Suono al passaggio sui settori / tick sound when hovering segments
sound = false

[hud]
# Posizione delle schede dei lavori: bottom-right, bottom-left, top-right, top-left, top, bottom
# Job card position: bottom-right, bottom-left, top-right, top-left, top, bottom
position = "bottom-right"
# Chiusura automatica delle schede completate (ms, 0 = mai) / auto-dismiss finished cards (ms, 0 = never)
timeout_ms = 7000

[notifications]
# Notifiche desktop: "never", "long" (lavori > 10 s o HUD nascosto), "always"
# Desktop notifications: "never", "long" (jobs > 10 s or HUD hidden), "always"
mode = "long"

[jobs]
# Lavori eseguiti in parallelo (1-16) / jobs running in parallel (1-16)
max_parallel = 2

[image]
# Qualità predefinite (1-100) / default qualities (1-100)
jpeg_quality = 85
webp_quality = 80
avif_quality = 60
# Mantieni EXIF/XMP/ICC nelle conversioni / keep EXIF/XMP/ICC when converting
keep_metadata = true

[video]
# CRF x264 (0-63, più basso = qualità più alta) / x264 CRF (0-63, lower = better quality)
crf = 23
# Preset x264: ultrafast … veryslow / x264 preset: ultrafast … veryslow
preset = "medium"
# Accelerazione hardware: "auto", "vaapi", "nvenc", "none" / hardware acceleration
hwaccel = "auto"

[audio]
# Qualità VBR MP3 (0 = migliore, 9 = peggiore) / MP3 VBR quality (0 = best, 9 = worst)
mp3_quality = 2
# Bitrate Opus / Opus bitrate
opus_bitrate = "128k"

[pdf]
# Risoluzione per esportare immagini / resolution for image export (36-1200)
dpi = 150
# Preset di compressione Ghostscript: screen, ebook, printer, prepress, default
# Ghostscript compression preset: screen, ebook, printer, prepress, default
compress = "ebook"

[ocr]
# Lingue Tesseract (pacchetti tesseract-data-*) / Tesseract languages (tesseract-data-* packages)
languages = "ita+eng"

[whisper]
# Modello whisper.cpp per «Trascrivi»: percorso di un file ggml-*.bin o nome ("base", "small"…);
# vuoto = ~/.local/share/dropwheel/whisper/ggml-base.bin o il primo modello trovato
# whisper.cpp model for "Transcribe": path of a ggml-*.bin file or a name ("base", "small"…);
# empty = ~/.local/share/dropwheel/whisper/ggml-base.bin or the first model found
model = ""

[apps]
# Programmi preferiti (vuoto = rilevamento automatico), es. terminal = "foot", file_manager = "thunar"
# Preferred programs (empty = autodetect), e.g. terminal = "foot", file_manager = "thunar"
terminal = ""
file_manager = ""
image_editor = ""
# Editor per «Annota» sui PDF (vuoto = Xournal++, poi Okular) / PDF annotation editor (empty = Xournal++, then Okular)
pdf_editor = ""

[share]
# Durata della condivisione LAN con QR (minuti) / LAN QR share lifetime (minutes)
minutes = 15
# Porta (0 = casuale) / port (0 = random)
port = 0
# Destinatario LocalSend predefinito (alias o IP, vuoto = chiedi) / default LocalSend target (alias or IP, empty = ask)
localsend_target = ""

# Azioni personalizzate / custom actions
# Segnaposto / placeholders: {file} {files} {dir} {name} {stem} {ext} {out}
# Un comando stringa passa da "sh -c" (segnaposto già quotati); una lista viene eseguita direttamente.
# A string command runs through "sh -c" (placeholders are quoted for you); a list runs directly.
# multi: "single" | "each" | "combine" | "any" ;  categories: [] = tutte / all
# output: "none" | "text" | "files"
#
# [[custom]]
# id = "upload-scp"
# label = "Carica sul server"
# icon = "cloud_upload"
# categories = ["image", "pdf"]
# command = "scp {file} server:/srv/inbox/"
# multi = "each"
# output = "none"
'''


def default_toml() -> str:
    """The commented default configuration file (Italian + English)."""
    return _DEFAULT_TOML


def write_default(path: str | os.PathLike | None = None, force: bool = False) -> Path:
    """Write the commented default configuration and return its path.

    Raises :class:`FileExistsError` when the file exists and ``force`` is false.
    """
    target = Path(path) if path is not None else config_path()
    if target.exists() and not force:
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.part-{os.getpid()}")
    tmp.write_text(_DEFAULT_TOML, encoding="utf-8")
    os.replace(tmp, target)
    return target
