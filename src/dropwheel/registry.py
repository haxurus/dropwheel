"""Action registry and wheel menu builder.

Action modules register functions with :func:`action`, define shared sub-rings with
:func:`group` and curated rings with :func:`layout`.  :func:`build_menu` turns a file selection
into the menu JSON of SPEC §5.3:

* the layout key is the category (1 file), ``multi:<category>`` (≥2 files of one category,
  falling back to the category layout) or ``mixed``; the File page uses ``layout("file", …)``
  (or ``layout(<key>, …, page="file")`` for a category-specific File page);
* non-applicable / hidden items are dropped, empty groups removed, single-item groups flattened
  (except ids ending in ``.convert``), every ring is capped at 8 with an automatic "More";
* without a layout, applicable actions are auto-grouped by id prefix (``<cat>.<group>.<leaf>``
  → group ``<cat>.<group>`` labelled ``grp.<group>``); applicable actions that a layout forgot
  are appended the same way (category-specific ones on the main page, wildcard ones on the File page);
* ``mixed`` is auto-built from the actions with ``mixed_ok=True`` that accept every file.

Categories: an action's ``categories`` may contain ``"*"`` (every category except ``url``);
an empty set means the same.
"""

from __future__ import annotations

import fnmatch
import inspect
import itertools
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from . import i18n, tools, util
from .detect import CATEGORIES, CATEGORY_ICONS, FileInfo, classify, common_category

log = logging.getLogger("dropwheel.registry")

__all__ = [
    "Param", "P", "Action", "GroupSpec", "action", "group", "layout", "get", "get_group", "all_actions",
    "all_groups", "layouts", "register", "unregister", "build_menu", "applicable_actions", "display_label",
    "find_node", "resolve_target", "clear", "snapshot", "restore", "MAX_RING", "MULTI_MODES", "MODES",
    "DIALOGS", "PARAM_TYPES", "GROUP_ICONS", "WILDCARDS",
]

MAX_RING = 8
MULTI_MODES = ("single", "each", "combine", "any")
MODES = ("instant", "quick", "dialog")
DIALOGS = (None, "form", "crop", "redact", "trim", "pages", "metadata", "adjust", "background")
PARAM_TYPES = ("int", "float", "slider", "bool", "select", "text", "password", "color", "time", "timerange",
               "pages", "path", "dir", "textarea", "rect", "boxes", "pageops", "metadata", "adjust", "background")
WILDCARDS = frozenset({"*", "any", "all"})

#: Default icons for auto-built groups (``grp.<leaf>``).
GROUP_ICONS: dict[str, str] = {
    "convert": "swap_horiz", "compress": "compress", "resize": "photo_size_select_large", "edit": "edit",
    "background": "wallpaper", "privacy": "privacy_tip", "pages": "auto_stories", "extract": "unarchive",
    "combine": "join", "audio": "graphic_eq", "video": "movie", "ocr": "document_scanner", "share": "share",
    "secure": "lock", "open": "open_in_new", "file": "draft", "info": "info", "more": "more_horiz",
    "transform": "transform", "trim": "content_cut", "tools": "build", "metadata": "sell", "split": "call_split",
    "merge": "merge", "frames": "burst_mode", "subtitles": "subtitles", "tags": "sell", "normalize": "equalizer",
    "transcribe": "record_voice_over", "copy": "content_copy", "format": "format_align_left", "export": "ios_share",
    "checksum": "fingerprint", "social": "groups", "upscale": "hd", "vectorize": "polyline", "icon": "apps",
    "media": "video_library", "qr": "qr_code", "save": "save", "cover": "image", "send": "send",
    "sheets": "tab", "csv": "csv", "encoding": "translate", "line_endings": "wrap_text", "slides": "slideshow",
    "fix": "build", "rotate": "rotate_right", "protect": "lock", "verify": "verified", "batch": "dynamic_feed",
    "download": "download", "specimen": "text_fields", "crop": "crop", "speed": "speed", "sign": "draw",
    "annotate": "draw", "redact": "ink_eraser", "watermark": "branding_watermark", "organize": "view_module",
    "unlock": "lock_open", "flip": "flip", "adjust": "tune", "stats": "analytics", "preview": "visibility",
    "mount": "album", "install": "install_desktop", "rename": "drive_file_rename_outline", "move": "drive_file_move",
    "trash": "delete", "terminal": "terminal", "encrypt": "enhanced_encryption", "open_with": "apps",
    "protect_pdf": "lock",
}
_GROUP_RANK = {"convert": 0, "compress": 1}

_lock = threading.RLock()
_ACTIONS: dict[str, "Action"] = {}
_GROUPS: dict[str, "GroupSpec"] = {}
_LAYOUTS: dict[tuple[str, str], list] = {}
_SEQ = itertools.count()
_label_cache: dict[tuple[str, str], str] = {}


# ======================================================================================= Param

def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "y", "on", "si", "sì"):
            return True
        if low in ("0", "false", "no", "n", "off", ""):
            return False
    raise ValueError(f"not a boolean: {value!r}")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_jsonable(v) for v in sorted(value, key=str)]
    return value


def _call_flexible(fn: Callable, files: list[FileInfo], config: dict | None) -> Any:
    """Call ``fn(files, config)`` or ``fn(files)`` or ``fn()`` depending on its signature."""
    try:
        sig = inspect.signature(fn)
        positional = [p for p in sig.parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty]
        varargs = any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())
        count = len(positional)
    except (TypeError, ValueError):
        count, varargs = 1, False
    if varargs or count >= 2:
        return fn(files, config or {})
    if count == 1:
        return fn(files)
    return fn()


@dataclass
class Param:
    """A user-editable parameter of an action (rendered by the generic form / dialogs).

    ``default`` may be a value, a callable (``fn(files)`` or ``fn(files, config)``) or a string
    ``"cfg:<dotted.key>"`` read from the configuration (e.g. ``"cfg:image.jpeg_quality"``).
    ``options`` is ``[(value, label_key)]``, plain values, or a callable ``fn(files)``.
    """

    name: str
    type: str
    label: str = ""
    default: Any = None
    min: Any = None
    max: Any = None
    step: Any = None
    unit: str = ""
    options: list | Callable[[list[FileInfo]], list] | None = None
    help: str = ""
    required: bool = False
    visible_if: dict | None = None

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"param {self.name!r}: unknown type {self.type!r}")

    def resolved_default(self, files: list[FileInfo] | None = None, config: dict | None = None) -> Any:
        """The default value for these files/config."""
        value = self.default
        if isinstance(value, str) and value.startswith("cfg:"):
            from . import config as cfgmod

            return cfgmod.get(config, value[4:])
        if callable(value):
            try:
                return _call_flexible(value, list(files or []), config)
            except Exception:
                log.exception("default of param %s failed", self.name)
                return None
        return value

    def option_pairs(self, files: list[FileInfo] | None = None) -> list[tuple[Any, str]]:
        """Options as ``[(value, label_key)]``."""
        opts = self.options
        if callable(opts):
            try:
                opts = opts(list(files or []))
            except Exception:
                log.exception("options of param %s failed", self.name)
                opts = []
        out: list[tuple[Any, str]] = []
        for opt in opts or []:
            if isinstance(opt, (tuple, list)) and len(opt) >= 2:
                out.append((opt[0], str(opt[1])))
            elif isinstance(opt, dict) and "value" in opt:
                out.append((opt["value"], str(opt.get("label", opt["value"]))))
            else:
                out.append((opt, str(opt)))
        return out

    def to_json(self, files: list[FileInfo] | None = None, lang: str | None = None,
                config: dict | None = None) -> dict:
        """Protocol JSON ``{name,type,label,default,min,max,step,unit,options,help,required,visible_if}``."""
        return {
            "name": self.name,
            "type": self.type,
            "label": i18n.t(self.label, lang) if self.label else self.name,
            "default": _jsonable(self.resolved_default(files, config)),
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "unit": i18n.t(self.unit, lang) if self.unit else "",
            "options": [{"value": _jsonable(v), "label": i18n.t(lbl, lang)} for v, lbl in self.option_pairs(files)],
            "help": i18n.t(self.help, lang) if self.help else "",
            "required": bool(self.required),
            "visible_if": _jsonable(self.visible_if) if self.visible_if else None,
        }

    def coerce(self, value: Any, files: list[FileInfo] | None = None) -> Any:
        """Convert a raw value (e.g. a CLI string) to this parameter's type; raises ``ValueError``."""
        kind = self.type
        if value is None:
            return None
        if kind in ("int",):
            v = int(round(float(str(value).replace(",", ".")))) if isinstance(value, str) else int(round(float(value)))
            return self._clamp(v)
        if kind in ("float", "slider"):
            v = float(str(value).replace(",", ".")) if isinstance(value, str) else float(value)
            if v != v:
                raise ValueError("NaN")
            if kind == "slider" and isinstance(self.step, int) and isinstance(self.min, int) and float(v).is_integer():
                return self._clamp(int(v))
            return self._clamp(v)
        if kind == "bool":
            return _to_bool(value)
        if kind == "select":
            pairs = self.option_pairs(files)
            values = [v for v, _ in pairs]
            if not values or value in values:
                return value
            for v in values:
                if str(v) == str(value):
                    return v
            raise ValueError(f"{value!r} not in options")
        if kind == "time":
            parsed = util.parse_time(value)
            if parsed is None:
                raise ValueError(f"invalid time {value!r}")
            return self._clamp(parsed)
        if kind in ("text", "password", "textarea", "color", "pages", "path", "dir"):
            return str(value)
        return value

    def _clamp(self, v: Any) -> Any:
        if self.min is not None and isinstance(self.min, (int, float)) and v < self.min:
            v = type(v)(self.min)
        if self.max is not None and isinstance(self.max, (int, float)) and v > self.max:
            v = type(v)(self.max)
        return v


def _param(type_: str, name: str, label: str = "", default: Any = None, **kw: Any) -> Param:
    return Param(name=name, type=type_, label=label, default=default, **kw)


class P:
    """Param helpers: ``P.int("quality", "prm.quality", 80, min=1, max=100)``, ``P.select(...)``…"""

    @staticmethod
    def int(name: str, label: str = "", default: Any = None, min: Any = None, max: Any = None, step: Any = 1,
            unit: str = "", **kw: Any) -> Param:
        return _param("int", name, label, default, min=min, max=max, step=step, unit=unit, **kw)

    @staticmethod
    def float(name: str, label: str = "", default: Any = None, min: Any = None, max: Any = None, step: Any = None,
              unit: str = "", **kw: Any) -> Param:
        return _param("float", name, label, default, min=min, max=max, step=step, unit=unit, **kw)

    @staticmethod
    def slider(name: str, label: str = "", default: Any = None, min: Any = 0, max: Any = 100, step: Any = 1,
               unit: str = "", **kw: Any) -> Param:
        return _param("slider", name, label, default, min=min, max=max, step=step, unit=unit, **kw)

    @staticmethod
    def bool(name: str, label: str = "", default: Any = False, **kw: Any) -> Param:
        return _param("bool", name, label, default, **kw)

    @staticmethod
    def select(name: str, label: str = "", options: Any = None, default: Any = None, **kw: Any) -> Param:
        return _param("select", name, label, default, options=options, **kw)

    @staticmethod
    def text(name: str, label: str = "", default: Any = "", **kw: Any) -> Param:
        return _param("text", name, label, default, **kw)

    @staticmethod
    def password(name: str, label: str = "", default: Any = "", **kw: Any) -> Param:
        return _param("password", name, label, default, **kw)

    @staticmethod
    def textarea(name: str, label: str = "", default: Any = "", **kw: Any) -> Param:
        return _param("textarea", name, label, default, **kw)

    @staticmethod
    def color(name: str, label: str = "", default: Any = "#000000", **kw: Any) -> Param:
        return _param("color", name, label, default, **kw)

    @staticmethod
    def time(name: str, label: str = "", default: Any = 0.0, **kw: Any) -> Param:
        return _param("time", name, label, default, **kw)

    @staticmethod
    def timerange(name: str, label: str = "", default: Any = None, **kw: Any) -> Param:
        return _param("timerange", name, label, default, **kw)

    @staticmethod
    def pages(name: str = "pages", label: str = "", default: Any = "", **kw: Any) -> Param:
        return _param("pages", name, label, default, **kw)

    @staticmethod
    def path(name: str, label: str = "", default: Any = "", **kw: Any) -> Param:
        return _param("path", name, label, default, **kw)

    @staticmethod
    def dir(name: str, label: str = "", default: Any = "", **kw: Any) -> Param:
        return _param("dir", name, label, default, **kw)

    @staticmethod
    def rect(name: str = "rect", label: str = "", default: Any = None, **kw: Any) -> Param:
        return _param("rect", name, label, default, **kw)

    @staticmethod
    def boxes(name: str = "boxes", label: str = "", default: Any = None, **kw: Any) -> Param:
        return _param("boxes", name, label, default, **kw)

    @staticmethod
    def pageops(name: str = "pageops", label: str = "", default: Any = None, **kw: Any) -> Param:
        return _param("pageops", name, label, default, **kw)

    @staticmethod
    def metadata(name: str = "metadata", label: str = "", default: Any = None, **kw: Any) -> Param:
        return _param("metadata", name, label, default, **kw)

    @staticmethod
    def adjust(name: str = "adjust", label: str = "", default: Any = None, **kw: Any) -> Param:
        return _param("adjust", name, label, default, **kw)

    @staticmethod
    def background(name: str = "background", label: str = "", default: Any = None, **kw: Any) -> Param:
        return _param("background", name, label, default, **kw)

    @staticmethod
    def replace(default: Any = False) -> Param:
        """The standard "Replace original" switch (added automatically when ``replace_ok=True``)."""
        return Param(name="replace", type="bool", label="prm.replace", default=default, help="prm.replace.help")

    # friendly aliases
    integer = int
    number = float
    boolean = bool
    choice = select


# ================================================================================= Action/Group

@dataclass(eq=False)
class GroupSpec:
    """A sub-ring: ``items`` are action ids, group ids or nested :class:`GroupSpec`.

    ``default`` (★ child) is an action id, a list of ids (first applicable wins) or
    ``fn(files) -> id``; when it is not applicable the first available leaf is used.
    """

    id: str
    label: str
    icon: str = ""
    items: list = field(default_factory=list)
    default: Any = None
    text: str | None = None


@dataclass(eq=False)
class Action:
    """A registered action (see :func:`action` for the meaning of every field)."""

    id: str
    label: str
    fn: Callable
    icon: str = ""
    text: str | None = None
    categories: frozenset = frozenset()
    when: Callable[[list[FileInfo]], bool] | None = None
    multi: str = "each"
    mode: str = "instant"
    dialog: str | None = None
    params: list[Param] = field(default_factory=list)
    requires: list = field(default_factory=list)
    danger: bool = False
    undoable: bool = True
    replace_ok: bool = False
    description: str = ""
    mixed_ok: bool = False
    order: int = 50
    module: str = ""
    seq: int = 0
    #: Optional ``fn(files, lang) -> dict`` merged into the action's menu node for this selection
    #: (e.g. ``{"locked": True, "description": "…"}`` for a password-protected PDF).  It may not
    #: change ``type``/``id``; exceptions are logged and ignored.
    node_extra: Callable[[list[FileInfo], str], dict] | None = None

    @property
    def wildcard(self) -> bool:
        """``True`` when the action accepts every (non-url) category."""
        return not self.categories or bool(self.categories & WILDCARDS)

    def matches_category(self, category: str) -> bool:
        """Category test for one file."""
        if category in self.categories:
            return True
        if self.wildcard and category != "url":
            return True
        return False

    def multi_ok(self, n: int) -> bool:
        """Multi-file rule: single → 1, each/any → ≥1, combine → ≥2."""
        if self.multi == "single":
            return n == 1
        if self.multi == "combine":
            return n >= 2
        return n >= 1

    def when_ok(self, files: list[FileInfo]) -> bool:
        """Evaluate the ``when`` predicate (``False`` if it raises)."""
        if self.when is None:
            return True
        try:
            return bool(self.when(files))
        except Exception:
            log.exception("when() of %s failed", self.id)
            return False

    def accepts(self, files: Sequence[FileInfo]) -> bool:
        """Full applicability: categories ∩, multi rule and ``when``."""
        files = list(files)
        if not files or not self.multi_ok(len(files)):
            return False
        if not all(self.matches_category(f.category) for f in files):
            return False
        return self.when_ok(files)

    def all_params(self) -> list[Param]:
        """Declared params plus the standard ``replace`` switch when ``replace_ok``."""
        params = list(self.params)
        if self.replace_ok and not any(p.name == "replace" for p in params):
            params.append(P.replace())
        return params

    def missing(self) -> list[str]:
        """Missing tools of ``requires``."""
        return tools.missing(self.requires)

    def resolve_params(self, files: Sequence[FileInfo], given: dict | None, config: dict | None) -> dict:
        """Defaults merged with ``given`` values coerced to their types.

        Raises :class:`~dropwheel.errors.UserError` for invalid or missing required values.
        Unknown keys in ``given`` are kept unchanged (dialogs may send extras).
        """
        from .errors import UserError

        files = list(files)
        given = dict(given or {})
        out: dict = {}
        for p in self.all_params():
            if p.name in given and given[p.name] is not None and given[p.name] != "":
                try:
                    out[p.name] = p.coerce(given.pop(p.name), files)
                except (ValueError, TypeError) as exc:
                    raise UserError("err.invalid_param", details=str(exc),
                                    param=i18n.t(p.label) if p.label else p.name) from None
            else:
                given.pop(p.name, None)
                value = p.resolved_default(files, config)
                if (value is None or value == "") and p.required:
                    raise UserError("err.missing_param", param=i18n.t(p.label) if p.label else p.name)
                out[p.name] = value
        out.update(given)
        return out


def _as_set(categories: Any) -> frozenset:
    if not categories:
        return frozenset()
    if isinstance(categories, str):
        return frozenset({categories})
    return frozenset(str(c) for c in categories)


def _requires_list(value: Any) -> list:
    """Normalise ``requires``: a str → [str]; a list keeps tuples as alternatives."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def register(act: Action) -> Action:
    """Register (or replace) an :class:`Action`."""
    if act.multi not in MULTI_MODES:
        raise ValueError(f"action {act.id}: multi must be one of {MULTI_MODES}")
    if act.mode not in MODES:
        raise ValueError(f"action {act.id}: mode must be one of {MODES}")
    if act.mode == "dialog" and act.dialog is None:
        act.dialog = "form"
    if act.dialog not in DIALOGS:
        raise ValueError(f"action {act.id}: unknown dialog {act.dialog!r}")
    unknown = [c for c in act.categories if c not in CATEGORIES and c not in WILDCARDS]
    if unknown:
        raise ValueError(f"action {act.id}: unknown categories {unknown}")
    with _lock:
        if act.id in _ACTIONS:
            log.debug("action %s re-registered", act.id)
            act.seq = _ACTIONS[act.id].seq
        else:
            act.seq = next(_SEQ)
        _ACTIONS[act.id] = act
        _label_cache.clear()
    return act


def unregister(action_id: str) -> None:
    """Remove an action (no error when absent)."""
    with _lock:
        _ACTIONS.pop(action_id, None)
        _label_cache.clear()


def action(id: str, label: str, icon: str = "", text: str | None = None, categories: Iterable[str] | str = (),
           when: Callable[[list[FileInfo]], bool] | None = None, multi: str = "each", mode: str = "instant",
           dialog: str | None = None, params: Iterable[Param] = (), requires: Iterable = (), danger: bool = False,
           undoable: bool = True, replace_ok: bool = False, description: str = "", mixed_ok: bool = False,
           order: int = 50, node_extra: Callable[[list[FileInfo], str], dict] | None = None,
           ) -> Callable[[Callable], Callable]:
    """Decorator registering ``fn(ctx) -> Result | None`` as an action (see SPEC §3.2).

    ``node_extra(files, lang) -> dict`` (optional) adds per-selection fields to the menu node.
    """

    def decorator(fn: Callable) -> Callable:
        act = Action(
            id=id, label=label, fn=fn, icon=icon, text=text, categories=_as_set(categories), when=when,
            multi=multi, mode=mode, dialog=dialog, params=list(params or []), requires=_requires_list(requires),
            danger=danger, undoable=undoable, replace_ok=replace_ok, description=description, mixed_ok=mixed_ok,
            order=order, module=getattr(fn, "__module__", "") or "", node_extra=node_extra,
        )
        register(act)
        try:
            fn.action = act  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass
        return fn

    return decorator


def group(id: str, label: str, icon: str = "", items: Iterable = (), default: Any = None,
          text: str | None = None) -> GroupSpec:
    """Create and register a :class:`GroupSpec` (usable by id in other layouts)."""
    spec = GroupSpec(id=id, label=label, icon=icon, items=list(items or []), default=default, text=text)
    with _lock:
        _GROUPS[id] = spec
        _label_cache.clear()
    return spec


def layout(key: str, items: Iterable, page: str = "main") -> None:
    """Register the ring for ``key`` (category, ``multi:<cat>``, ``mixed`` or ``file``) on ``page``.

    ``layout("file", items)`` always targets the File page.
    """
    if key == "file":
        page = "file"
    if page not in ("main", "file"):
        raise ValueError(f"layout {key}: page must be 'main' or 'file'")
    with _lock:
        _LAYOUTS[(page, key)] = list(items or [])
        _label_cache.clear()


def get(action_id: str) -> Action | None:
    """The registered action ``action_id`` or ``None``."""
    return _ACTIONS.get(action_id)


def get_group(group_id: str) -> GroupSpec | None:
    """The registered group ``group_id`` or ``None``."""
    return _GROUPS.get(group_id)


def all_actions() -> list[Action]:
    """Every registered action in registration order."""
    return sorted(_ACTIONS.values(), key=lambda a: a.seq)


def all_groups() -> list[GroupSpec]:
    """Every registered group."""
    return list(_GROUPS.values())


def layouts() -> dict[tuple[str, str], list]:
    """Registered layouts keyed by ``(page, key)``."""
    return dict(_LAYOUTS)


def clear() -> None:
    """Remove every action, group and layout (tests)."""
    with _lock:
        _ACTIONS.clear()
        _GROUPS.clear()
        _LAYOUTS.clear()
        _label_cache.clear()


def snapshot() -> tuple[dict, dict, dict]:
    """Copy of the registries (tests)."""
    with _lock:
        return dict(_ACTIONS), dict(_GROUPS), {k: list(v) for k, v in _LAYOUTS.items()}


def restore(state: tuple[dict, dict, dict]) -> None:
    """Restore a :func:`snapshot` (tests)."""
    with _lock:
        _ACTIONS.clear()
        _ACTIONS.update(state[0])
        _GROUPS.clear()
        _GROUPS.update(state[1])
        _LAYOUTS.clear()
        _LAYOUTS.update(state[2])
        _label_cache.clear()


# ================================================================================ menu builder

def _collect_refs(items: Iterable, out: set[str], seen: set[str] | None = None) -> set[str]:
    """Every action id referenced (recursively) by layout items."""
    seen = seen if seen is not None else set()
    for it in items or []:
        if isinstance(it, GroupSpec):
            if it.id in seen:
                continue
            seen.add(it.id)
            _collect_refs(it.items, out, seen)
        elif isinstance(it, str):
            if it in _ACTIONS:
                out.add(it)
            elif it in _GROUPS and it not in seen:
                seen.add(it)
                _collect_refs(_GROUPS[it].items, out, seen)
    return out


class _MenuCtx:
    def __init__(self, files: list[FileInfo], config: dict | None, lang: str) -> None:
        self.files = files
        self.config = config or {}
        self.lang = lang
        hidden = self.config.get("hidden_actions") or []
        self.hidden_patterns = [str(h) for h in hidden if isinstance(h, str) and h]
        wheel = self.config.get("wheel") or {}
        self.show_unavailable = bool(wheel.get("show_unavailable", True)) if isinstance(wheel, dict) else True
        self._applicable: dict[str, bool] = {}
        self._missing: dict[str, list[str]] = {}

    def t(self, key: str, /, **fmt: Any) -> str:
        return i18n.t(key, self.lang, **fmt)

    def hidden(self, ident: str) -> bool:
        return any(ident == pat or fnmatch.fnmatchcase(ident, pat) for pat in self.hidden_patterns)

    def applicable(self, act: Action) -> bool:
        hit = self._applicable.get(act.id)
        if hit is None:
            hit = act.accepts(self.files)
            self._applicable[act.id] = hit
        return hit

    def missing(self, act: Action) -> list[str]:
        hit = self._missing.get(act.id)
        if hit is None:
            hit = act.missing()
            self._missing[act.id] = hit
        return hit


def _action_node(act: Action, ctx: _MenuCtx) -> dict | None:
    if ctx.hidden(act.id) or not ctx.applicable(act):
        return None
    miss = ctx.missing(act)
    available = not miss
    if not available and not ctx.show_unavailable:
        return None
    hint = ""
    install = ""
    if miss:
        hint = ctx.t("hint.requires", tool=", ".join(tools.display_name(m) for m in miss))
        install = tools.install_hint(miss)
    try:
        params = [p.to_json(ctx.files, ctx.lang, ctx.config) for p in act.all_params()]
    except Exception:
        log.exception("params of %s failed", act.id)
        params = []
    node = {
        "type": "action",
        "id": act.id,
        "label": ctx.t(act.label),
        "title": display_label(act.id, ctx.lang),
        "icon": act.icon or "",
        "text": ctx.t(act.text) if act.text else None,
        "available": available,
        "hint": hint,
        "install": install,
        "danger": bool(act.danger),
        "mode": act.mode,
        "dialog": act.dialog,
        "params": params,
        "description": ctx.t(act.description) if act.description else "",
        "default": False,
        "multi": act.multi,
        "undoable": bool(act.undoable),
    }
    if act.node_extra is not None:
        try:
            extra = act.node_extra(list(ctx.files), ctx.lang)
        except Exception:
            log.exception("node_extra of %s failed", act.id)
            extra = None
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key not in ("type", "id", "default", "title"):
                    node[str(key)] = _jsonable(value)
    return node


def _iter_leaves(nodes: list[dict]) -> Iterator[dict]:
    for node in nodes:
        if node.get("type") == "group":
            yield from _iter_leaves(node.get("items") or [])
        else:
            yield node


def _find_in(nodes: list[dict], ident: str) -> dict | None:
    for node in nodes:
        if node.get("id") == ident:
            return node
        if node.get("type") == "group":
            hit = _find_in(node.get("items") or [], ident)
            if hit is not None:
                return hit
    return None


def _pick_default(default: Any, children: list[dict], ctx: _MenuCtx) -> str | None:
    value = default
    if callable(value):
        try:
            value = _call_flexible(value, ctx.files, ctx.config)
        except Exception:
            log.exception("group default callable failed")
            value = None
    candidates: list[str] = []
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple)):
        candidates = [str(v) for v in value if v]
    for cand in candidates:
        node = _find_in(children, cand)
        if node is None:
            continue
        if node.get("type") == "group":
            inner = node.get("default")
            if inner:
                leaf = _find_in(node.get("items") or [], inner)
                if leaf is not None and leaf.get("available"):
                    leaf["default"] = True
                    return inner
            continue
        if node.get("available"):
            node["default"] = True
            return cand
    leaves = list(_iter_leaves(children))
    for leaf in leaves:
        if leaf.get("available"):
            leaf["default"] = True
            return leaf["id"]
    if leaves:
        leaves[0]["default"] = True
        return leaves[0]["id"]
    return None


def _any_available(nodes: list[dict]) -> bool:
    return any(n.get("available", True) for n in _iter_leaves(nodes))


def _cap(nodes: list[dict], more_id: str, ctx: _MenuCtx) -> list[dict]:
    if len(nodes) <= MAX_RING:
        return nodes
    head = nodes[: MAX_RING - 1]
    rest = _cap(nodes[MAX_RING - 1:], more_id + ".more", ctx)
    more = {
        "type": "group",
        "id": more_id,
        "label": ctx.t("grp.more"),
        "icon": "more_horiz",
        "text": None,
        "items": rest,
        "default": None,
        "available": _any_available(rest),
    }
    return head + [more]


def _group_node(spec: GroupSpec, ctx: _MenuCtx, seen: frozenset) -> dict | None:
    if spec.id in seen or ctx.hidden(spec.id):
        return None
    children = _resolve_items(spec.items, ctx, seen | {spec.id})
    if not children:
        return None
    if len(children) == 1 and not spec.id.endswith(".convert"):
        return children[0]
    children = _cap(children, spec.id + ".more", ctx)
    leaf = spec.id.rsplit(".", 1)[-1]
    node = {
        "type": "group",
        "id": spec.id,
        "label": ctx.t(spec.label),
        "icon": spec.icon or GROUP_ICONS.get(leaf, "more_horiz"),
        "text": ctx.t(spec.text) if spec.text else None,
        "items": children,
        "default": None,
        "available": _any_available(children),
    }
    node["default"] = _pick_default(spec.default, children, ctx)
    return node


def _resolve_items(items: Iterable, ctx: _MenuCtx, seen: frozenset = frozenset()) -> list[dict]:
    out: list[dict] = []
    for it in items or []:
        node: dict | None = None
        if isinstance(it, GroupSpec):
            node = _group_node(it, ctx, seen)
        elif isinstance(it, Action):
            node = _action_node(it, ctx)
        elif isinstance(it, str):
            act = _ACTIONS.get(it)
            if act is not None:
                node = _action_node(act, ctx)
            elif it in _GROUPS:
                node = _group_node(_GROUPS[it], ctx, seen)
            else:
                log.debug("layout item %s is not registered", it)
        if node is not None:
            out.append(node)
    return out


def _auto_items(actions: list[Action]) -> list:
    """Auto-group actions by id prefix (``<cat>.<group>.<leaf>`` → group ``<cat>.<group>``)."""
    buckets: dict[str, list[Action]] = {}
    standalone: list[Action] = []
    for act in sorted(actions, key=lambda a: (a.order, a.seq)):
        parts = act.id.split(".")
        if len(parts) >= 3:
            buckets.setdefault(".".join(parts[:2]), []).append(act)
        else:
            standalone.append(act)
    entries: list[tuple[int, int, int, Any]] = []
    for key, acts in buckets.items():
        leaf = key.rsplit(".", 1)[-1]
        reg = _GROUPS.get(key)
        if reg is not None:
            spec = GroupSpec(id=key, label=reg.label, icon=reg.icon, items=[a.id for a in acts],
                             default=reg.default, text=reg.text)
        else:
            spec = GroupSpec(id=key, label=f"grp.{leaf}", icon=GROUP_ICONS.get(leaf, acts[0].icon or "more_horiz"),
                             items=[a.id for a in acts])
        entries.append((_GROUP_RANK.get(leaf, 2), min(a.order for a in acts), min(a.seq for a in acts), spec))
    for act in standalone:
        leaf = act.id.rsplit(".", 1)[-1]
        entries.append((_GROUP_RANK.get(leaf, 2), act.order, act.seq, act.id))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    return [e[3] for e in entries]


def _file_layout(key: str, category: str) -> list | None:
    for k in (key, category, "file"):
        lay = _LAYOUTS.get(("file", k))
        if lay is not None:
            return lay
    return None


def _main_and_file(ctx: _MenuCtx, category: str) -> tuple[list[dict], list[dict]]:
    files = ctx.files
    n = len(files)
    key = "mixed" if category == "mixed" else (f"multi:{category}" if n >= 2 else category)
    file_layout = _file_layout(key, category)
    file_refs = _collect_refs(file_layout or [], set())
    candidates = [a for a in all_actions() if not ctx.hidden(a.id) and ctx.applicable(a)]

    # ------------------------------------------------------------------ main page
    main_items: list
    main_refs: set[str]
    if category == "mixed":
        pool = [a for a in candidates if a.mixed_ok]
        lay = _LAYOUTS.get(("main", "mixed"))
        layout_refs = _collect_refs(lay or [], set())
        if lay is not None:
            main_items = list(lay) + _auto_items([a for a in pool if a.id not in layout_refs])
        else:
            main_items = _auto_items(pool)
        main_refs = {a.id for a in pool} | layout_refs
    else:
        lay = _LAYOUTS.get(("main", key))
        fallback = False
        if lay is None and n >= 2:
            lay = _LAYOUTS.get(("main", category))
            fallback = lay is not None
        specific = [a for a in candidates if not a.wildcard and a.id not in file_refs]
        if lay is None:
            main_items = _auto_items(specific)
            main_refs = {a.id for a in specific}
        else:
            main_refs = _collect_refs(lay, set())
            left = [a for a in specific if a.id not in main_refs]
            if fallback:
                first = [a for a in left if a.multi in ("combine", "any")]
                rest = [a for a in left if a.multi not in ("combine", "any")]
                main_items = _auto_items(first) + list(lay) + _auto_items(rest)
            else:
                main_items = list(lay) + _auto_items(left)
            main_refs |= {a.id for a in left}
    main_nodes = _cap(_resolve_items(main_items, ctx), "main.more", ctx)

    # ------------------------------------------------------------------ file page
    file_items: list = list(file_layout or [])
    leftovers = [a for a in candidates if a.wildcard and a.id not in file_refs and a.id not in main_refs]
    file_items += _auto_items(leftovers)
    file_nodes = _cap(_resolve_items(file_items, ctx), "file.more", ctx)
    return main_nodes, file_nodes


def _title(files: list[FileInfo], lang: str) -> str:
    if not files:
        return i18n.t("menu.drop_here", lang)
    if len(files) == 1:
        return files[0].name
    if any(f.is_dir for f in files):
        if all(f.is_dir for f in files):
            return i18n.tn("menu.folders", len(files), lang)
        return i18n.tn("menu.items", len(files), lang)
    return i18n.tn("menu.files", len(files), lang)


def _subtitle(files: list[FileInfo], lang: str) -> str:
    if not files:
        return ""
    if len(files) == 1:
        f = files[0]
        if f.category == "url":
            from urllib.parse import urlsplit

            try:
                host = urlsplit(f.uri).hostname or ""
            except ValueError:
                host = ""
            shown = (host[4:] if host.startswith("www.") else host) or f.uri
            # a page link's title already starts with its host ("example.com", "youtube.com/watch?v=…")
            if f.name == shown or f.name.startswith((shown + "/", shown + "?")):
                return ""
            return shown
        if f.is_dir:
            return i18n.t("menu.folder", lang)
        if not f.exists:
            return i18n.t("menu.missing", lang)
        return util.human_size(f.size, lang)
    total = sum(f.size for f in files if f.exists and not f.is_dir)
    return util.human_size(total, lang) if total else ""


def _thumb(files: list[FileInfo], thumb_fn: Callable | None) -> str | None:
    if thumb_fn is None or not files:
        return None
    try:
        value = thumb_fn(files[0])
    except Exception:
        log.exception("thumbnail failed for %s", files[0].path)
        return None
    if isinstance(value, dict):
        value = value.get("png") or value.get("path")
    if isinstance(value, Path):
        value = str(value)
    return value if isinstance(value, str) and value else None


def build_menu(files: Sequence[FileInfo | str], config: dict | None, lang: str, context: str = "drag",
               thumb_fn: Callable[[FileInfo], Any] | None = None) -> dict:
    """Build the wheel menu JSON (SPEC §5.3) for ``files`` (FileInfo objects, paths or URIs)."""
    infos = [f if isinstance(f, FileInfo) else classify(f, deep=len(files) <= 50) for f in files]
    lang = i18n.normalize_lang(lang)
    ctx = _MenuCtx(infos, config, lang)
    category = common_category(infos)
    if infos:
        main_nodes, file_nodes = _main_and_file(ctx, category)
    else:
        main_nodes, file_nodes = [], []
    if category == "mixed":
        icon = "stacks"
    elif category == "empty":
        icon = "move_to_inbox"
    else:
        icon = CATEGORY_ICONS.get(category, "draft")
    return {
        "files": [f.to_json() for f in infos],
        "category": category,
        "count": len(infos),
        "title": _title(infos, lang),
        "subtitle": _subtitle(infos, lang),
        "thumb": _thumb(infos, thumb_fn),
        "icon": icon,
        "context": context,
        "pages": [
            {"id": "main", "label": i18n.t("page.tools", lang), "items": main_nodes},
            {"id": "file", "label": i18n.t("page.file", lang), "items": file_nodes},
        ],
    }


def applicable_actions(files: Sequence[FileInfo | str], config: dict | None = None) -> list[Action]:
    """Every registered, non-hidden action applicable to ``files``."""
    infos = [f if isinstance(f, FileInfo) else classify(f) for f in files]
    ctx = _MenuCtx(infos, config, "en")
    return [a for a in all_actions() if not ctx.hidden(a.id) and ctx.applicable(a)]


def find_node(menu: dict, ident: str) -> dict | None:
    """Find a node by id in a built menu (every page)."""
    for page in menu.get("pages") or []:
        hit = _find_in(page.get("items") or [], ident)
        if hit is not None:
            return hit
    return None


def resolve_target(ident: str, files: Sequence[FileInfo | str], config: dict | None = None,
                   lang: str = "en") -> str | None:
    """Action id for ``ident``: itself when it is an action, else the ★ default of that group."""
    if ident in _ACTIONS:
        return ident
    menu = build_menu(files, config, lang, "click")
    node = find_node(menu, ident)
    for _ in range(8):
        if node is None:
            return None
        if node.get("type") == "action":
            return node["id"]
        default = node.get("default")
        if not default:
            leaves = [leaf for leaf in _iter_leaves(node.get("items") or []) if leaf.get("available")]
            return leaves[0]["id"] if leaves else None
        node = _find_in(node.get("items") or [], default)
    return None


def _parent_groups(action_id: str) -> list[GroupSpec]:
    """Groups (registered or nested in layouts) that directly contain ``action_id``."""
    found: list[GroupSpec] = []
    seen: set[int] = set()

    def visit(items: Iterable) -> None:
        for it in items or []:
            spec = it if isinstance(it, GroupSpec) else _GROUPS.get(it) if isinstance(it, str) else None
            if spec is None or id(spec) in seen:
                continue
            seen.add(id(spec))
            if action_id in [x for x in spec.items if isinstance(x, str)]:
                found.append(spec)
            visit(spec.items)

    visit(list(_GROUPS.values()))
    for items in _LAYOUTS.values():
        visit(items)
    return found


def display_label(action_id: str, lang: str | None = None) -> str:
    """Job label ``"<group> · <action>"`` (e.g. "Comprimi · Bilanciata"), or the action label alone."""
    lang = i18n.normalize_lang(lang or i18n.get_lang())
    key = (action_id, lang)
    cached = _label_cache.get(key)
    if cached is not None:
        return cached
    act = _ACTIONS.get(action_id)
    if act is None:
        return action_id
    own = i18n.t(act.text, lang) if act.text and act.label in ("", act.id) else i18n.t(act.label, lang)
    parent_label = ""
    parents = [g for g in _parent_groups(action_id) if not g.id.endswith(".more")]
    if parents:
        parent_label = i18n.t(parents[0].label, lang)
    else:
        parts = action_id.split(".")
        if len(parts) >= 3:
            grp_key = f"grp.{parts[1]}"
            if i18n.has(grp_key, lang):
                parent_label = i18n.t(grp_key, lang)
    label = f"{parent_label} · {own}" if parent_label and parent_label != own else own
    _label_cache[key] = label
    return label
