"""Translations.

Strings live in flat JSON fragments ``dropwheel/locales/<lang>/<module>.json`` (``core.json``,
``image.json``, ``ui_wheel.json``…).  All fragments of a language are merged (``core.json``
first, then alphabetical; later fragments win), then user overrides from
``~/.config/dropwheel/locales/<lang>/*.json``.  Lookup order: requested language → English →
the key itself (so labels such as ``"PNG"`` can be used directly as keys).

Fragments are read through :mod:`importlib.resources`, so this works both from an installed
package and from a source checkout (``PYTHONPATH=src``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger("dropwheel.i18n")

__all__ = [
    "LANGS", "DEFAULT_LANG", "t", "tn", "has", "strings", "all_strings", "load", "reload",
    "detect_lang", "normalize_lang", "set_lang", "get_lang",
]

#: Supported UI languages.
LANGS: tuple[str, ...] = ("it", "en")
#: Fallback language.
DEFAULT_LANG = "en"

_lock = threading.RLock()
_raw: dict[str, dict[str, str]] = {}
_merged: dict[str, dict[str, str]] = {}
_current = DEFAULT_LANG


class _SafeDict(dict):
    """``str.format_map`` helper leaving unknown placeholders untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def normalize_lang(lang: str | None) -> str:
    """Map ``"it_IT.UTF-8"`` → ``"it"``; unsupported or empty → ``"en"``."""
    if not lang:
        return DEFAULT_LANG
    code = str(lang).strip().split(".")[0].split("@")[0].replace("-", "_").split("_")[0].lower()
    return code if code in LANGS else DEFAULT_LANG


def _user_locale_dir(lang: str) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if base and os.path.isabs(base) else Path(os.path.expanduser("~/.config"))
    return root / "dropwheel" / "locales" / lang


def _order_key(name: str) -> tuple[int, str]:
    return (0 if name == "core.json" else 1, name)


def _read_fragment(name: str, text: str, lang: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        log.warning("invalid locale fragment %s/%s: %s", lang, name, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("locale fragment %s/%s is not an object", lang, name)
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, (str, int, float)):
            out[k] = str(v)
    return out


def load(lang: str) -> dict[str, str]:
    """Return the strings of ``lang`` only (no English fallback), cached."""
    lang = normalize_lang(lang) if lang not in LANGS else lang
    with _lock:
        cached = _raw.get(lang)
        if cached is not None:
            return cached
        merged: dict[str, str] = {}
        fragments: list[tuple[str, str]] = []
        try:
            base = resources.files("dropwheel").joinpath("locales").joinpath(lang)
            if base.is_dir():
                for entry in base.iterdir():
                    if entry.name.endswith(".json") and entry.is_file():
                        try:
                            fragments.append((entry.name, entry.read_text(encoding="utf-8")))
                        except (OSError, UnicodeDecodeError) as exc:
                            log.warning("cannot read locale %s/%s: %s", lang, entry.name, exc)
        except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError, OSError) as exc:
            log.debug("no packaged locales for %s: %s", lang, exc)
        for name, text in sorted(fragments, key=lambda f: _order_key(f[0])):
            merged.update(_read_fragment(name, text, lang))
        user_dir = _user_locale_dir(lang)
        if user_dir.is_dir():
            for path in sorted(user_dir.glob("*.json")):
                try:
                    merged.update(_read_fragment(path.name, path.read_text(encoding="utf-8"), lang))
                except (OSError, UnicodeDecodeError) as exc:
                    log.warning("cannot read user locale %s: %s", path, exc)
        _raw[lang] = merged
        return merged


def strings(lang: str | None = None) -> dict[str, str]:
    """Strings of ``lang`` merged over English (cached)."""
    lang = normalize_lang(lang or _current)
    with _lock:
        cached = _merged.get(lang)
        if cached is not None:
            return cached
        merged = dict(load(DEFAULT_LANG))
        if lang != DEFAULT_LANG:
            merged.update(load(lang))
        _merged[lang] = merged
        return merged


def all_strings(lang: str | None = None) -> dict[str, str]:
    """Copy of every string for ``lang`` (English fallback included) — sent to QML in ``hello``."""
    return dict(strings(lang))


def reload() -> None:
    """Drop the caches (locale files changed, or action modules were added)."""
    with _lock:
        _raw.clear()
        _merged.clear()


def has(key: str, lang: str | None = None) -> bool:
    """``True`` when ``key`` exists in ``lang`` or English."""
    return bool(key) and key in strings(lang)


def _format(text: str, fmt: Mapping[str, Any]) -> str:
    if not fmt:
        return text
    try:
        return text.format_map(_SafeDict(fmt))
    except (ValueError, IndexError, AttributeError, TypeError):
        return text


def t(key: str, lang: str | None = None, /, **fmt: Any) -> str:
    """Translate ``key`` into ``lang`` (default: :func:`get_lang`) and fill ``{placeholders}``.

    Missing keys fall back to English, then to the key itself.  ``key`` and ``lang`` are
    positional-only, so any placeholder name (even ``key``) can be passed as keyword.
    """
    if key is None:
        return ""
    key = str(key)
    if not key:
        return ""
    lang, fmt = _lang_kw(lang, fmt)
    text = strings(lang).get(key, key)
    return _format(text, fmt)


def _lang_kw(lang: str | None, fmt: dict) -> tuple[str | None, dict]:
    """Accept ``t(key, lang="it")`` too: a ``lang`` keyword holding a language code selects it."""
    if lang is None and isinstance(fmt.get("lang"), str) and fmt["lang"] in LANGS:
        fmt = dict(fmt)
        lang = fmt.pop("lang")
    return lang, fmt


def tn(key: str, n: int, lang: str | None = None, /, **fmt: Any) -> str:
    """Plural-aware lookup: ``<key>.one`` when ``n == 1`` else ``<key>.other`` (``n`` is passed as ``{n}``)."""
    lang, fmt = _lang_kw(lang, fmt)
    table = strings(lang)
    form = f"{key}.one" if n == 1 else f"{key}.other"
    text = table.get(form) or table.get(key) or key
    fmt = dict(fmt)
    fmt.setdefault("n", n)
    return _format(text, fmt)


def detect_lang(config: Mapping[str, Any] | None = None) -> str:
    """Resolve the UI language: ``config["language"]`` when it is ``it``/``en``, otherwise the
    environment (``LC_ALL`` → ``LC_MESSAGES`` → ``LANG``; ``it*`` → ``it``, anything else → ``en``)."""
    if config:
        value = str(config.get("language", "auto") or "auto").lower()
        if value in LANGS:
            return value
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            return "it" if value.lower().startswith("it") else "en"
    return DEFAULT_LANG


def set_lang(lang: str) -> None:
    """Set the default language used by :func:`t` when no language is passed."""
    global _current
    _current = normalize_lang(lang)


def get_lang() -> str:
    """The default language used by :func:`t`."""
    return _current
