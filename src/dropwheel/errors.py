"""Exception types shared by the backend.

* :class:`UserError` - an expected failure whose message is shown to the user.  The message is
  an i18n key (translated when the key exists) or literal text; ``**fmt`` fills placeholders.
* :class:`ToolError` - an external program failed (subclass of :class:`UserError`).
* :class:`MissingTool` - a required program / Python module is not installed; carries the
  install command in :attr:`UserError.install`.
* :class:`Cancelled` - the job was cancelled by the user.

Any other exception escaping an action is treated as a bug ("Unexpected error" + traceback).
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = ["DropwheelError", "UserError", "ToolError", "MissingTool", "Cancelled"]


class DropwheelError(Exception):
    """Base class of every Dropwheel exception."""


class UserError(DropwheelError):
    """An error meant for the user.

    Args:
        message: i18n key (e.g. ``"err.file_not_found"``) or literal text.
        details: extra technical text (stderr tail, traceback...), shown in an expandable area.
        install: shell command that installs what is missing (shown with a "copy" button).
        **fmt: placeholder values for the translated message (``{name}`` ...).
    """

    def __init__(self, message: str = "", details: Any = "", install: str = "", **fmt: Any) -> None:
        self.message = str(message or "")
        if isinstance(details, (list, tuple)):
            details = "\n".join(str(d) for d in details)
        self.details = "" if details is None else str(details)
        self.install = install or ""
        self.fmt = dict(fmt)
        super().__init__(self.message)

    def text(self, lang: str | None = None) -> str:
        """Return the message translated into ``lang`` (English when ``None`` is not set)."""
        from . import i18n

        return i18n.t(self.message, lang, **self.fmt)

    def to_json(self, lang: str | None = None) -> dict:
        """Return the protocol representation ``{"message", "details", "install"}``."""
        return {"message": self.text(lang), "details": self.details, "install": self.install}

    def __str__(self) -> str:
        try:
            return self.text("en")
        except Exception:  # pragma: no cover - i18n must never break error reporting
            return self.message


class ToolError(UserError):
    """An external tool failed (non-zero exit, timeout, unreadable output)."""


class MissingTool(UserError):
    """One or more required tools are missing.

    Args:
        name: a tool name from :mod:`dropwheel.tools` (``"ffmpeg"``, ``"py:pymupdf"``) or a list of them.
        details: optional extra text.
    """

    def __init__(self, name: str | Iterable[str], details: str = "") -> None:
        if isinstance(name, str):
            names = [name]
        else:
            names = [str(n) for n in name]
        from . import tools

        install = tools.install_hint(names)
        shown = ", ".join(tools.display_name(n) for n in names) or "?"
        super().__init__("err.missing_tool", details=details, install=install, tool=shown)
        self.names = names


class Cancelled(DropwheelError):
    """The running job was cancelled."""

    def __init__(self, message: str = "job.cancelled") -> None:
        super().__init__(message)
        self.message = message
        #: Partial result collected before cancellation (set by the job runner for batches).
        self.partial: Any = None
