"""Action modules.

:func:`load_all` imports every action module (in a fixed order) so their ``@action`` /
``group()`` / ``layout()`` calls register with :mod:`dropwheel.registry`.  A module that fails to
import does not break the others: the failure is recorded in :data:`LOAD_ERRORS`
(``{module: traceback text}``) and reported by ``dropwheel doctor``.

After importing ``custom``, ``load_all(config)`` calls ``custom.register(config)`` (or
``custom.setup(config)``) when that function exists, so user-defined actions from the
configuration are (re)registered.
"""

from __future__ import annotations

import importlib
import logging
import sys
import threading
import traceback
from typing import Any

log = logging.getLogger("dropwheel.actions")

__all__ = ["MODULES", "LOAD_ERRORS", "load_all", "loaded_modules"]

#: Import order (common first: it defines the shared File page).
MODULES: tuple[str, ...] = ("common", "image", "pdf", "docs", "media", "archive", "fonts", "folder", "web",
                            "system", "custom")

#: module name -> error text for modules that failed to import.
LOAD_ERRORS: dict[str, str] = {}

_loaded: list[str] = []
_lock = threading.Lock()


def loaded_modules() -> list[str]:
    """Names of the modules imported successfully."""
    return list(_loaded)


def _short_traceback(exc: BaseException) -> str:
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    text = "".join(lines)
    return text[-6000:]


def load_all(config: dict | None = None, *, reload: bool = False) -> dict[str, str]:
    """Import every action module; returns :data:`LOAD_ERRORS`.

    ``reload=True`` re-imports modules that were already loaded (used after a config change).
    """
    with _lock:
        for name in MODULES:
            full = f"{__name__}.{name}"
            try:
                if full in sys.modules:
                    if reload:
                        importlib.reload(sys.modules[full])
                    module = sys.modules[full]
                else:
                    module = importlib.import_module(full)
            except ModuleNotFoundError as exc:
                if exc.name == full:
                    LOAD_ERRORS[name] = f"module {full} not found"
                else:
                    LOAD_ERRORS[name] = _short_traceback(exc)
                log.warning("action module %s not loaded: %s", name, exc)
                continue
            except Exception as exc:  # any import-time bug must not break the other modules
                LOAD_ERRORS[name] = _short_traceback(exc)
                log.error("action module %s failed to import: %s", name, exc)
                continue
            LOAD_ERRORS.pop(name, None)
            if name not in _loaded:
                _loaded.append(name)
            if name == "custom":
                hook: Any = getattr(module, "register", None) or getattr(module, "setup", None)
                if callable(hook):
                    try:
                        hook(config)
                    except Exception as exc:
                        LOAD_ERRORS[name] = _short_traceback(exc)
                        log.error("custom actions failed to register: %s", exc)
        try:
            from .. import i18n

            i18n.reload()
        except Exception:  # pragma: no cover
            pass
        return LOAD_ERRORS
