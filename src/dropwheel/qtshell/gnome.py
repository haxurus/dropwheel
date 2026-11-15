"""What the host needs from a GNOME session, with the same names as :mod:`win32`.

GNOME on Wayland tells no ordinary program where the pointer is or whether Shift is held, so the
Dropwheel GNOME Shell extension (``integrations/gnome/``) answers one D-Bus call,
``GetPointer() → (x, y, mods)``, with ``mods`` a Clutter modifier mask (Shift = 1, buttons 1–3 =
256, 512, 1024).  The host runs under XWayland (``QT_QPA_PLATFORM=xcb``) so that it can place its
windows and keep them above the others; input pass-through is the X11 input shape (libXext).

Without the extension (before the first logout after installing it) every function answers
"nothing held" and the cursor falls back to Qt's, which XWayland only updates over X11 windows.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

#: set by the host once Qt runs on X11 under Linux (``False`` offscreen, in the tests)
ACTIVE = False

SERVICE = "io.github.haxurus.Dropwheel"
PATH = "/io/github/haxurus/Dropwheel"
INTERFACE = "io.github.haxurus.Dropwheel"
EXTENSION_UUID = "dropwheel@haxurus.github.io"

SHIFT_MASK, BUTTON1_MASK, BUTTON2_MASK, BUTTON3_MASK = 1, 1 << 8, 1 << 9, 1 << 10
VK_SHIFT = 0x10  # the win32 name, so that callers stay platform-neutral

_CACHE_S = 0.010   # one D-Bus round trip serves every question asked within the same tick
_RETRY_S = 2.0     # after a failure (extension not loaded yet), ask again only this often

_state: dict[str, Any] = {"at": 0.0, "value": None, "failed_at": 0.0, "warned": False}


def _call() -> tuple[int, int, int] | None:
    from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage

    msg = QDBusMessage.createMethodCall(SERVICE, PATH, INTERFACE, "GetPointer")
    reply = QDBusConnection.sessionBus().call(msg, QDBus.CallMode.Block, 100)
    if reply.type() != QDBusMessage.MessageType.ReplyMessage:
        return None
    args = reply.arguments()
    if len(args) != 3:
        return None
    return int(args[0]), int(args[1]), int(args[2])


def pointer() -> tuple[int, int, int] | None:
    """``(x, y, mods)`` from the extension, or ``None`` when it does not answer."""
    if not ACTIVE:
        return None
    now = time.monotonic()
    if now - _state["at"] < _CACHE_S:
        return _state["value"]
    if _state["failed_at"] and now - _state["failed_at"] < _RETRY_S:
        return None
    try:
        value = _call()
    except Exception:  # noqa: BLE001 - no QtDBus, no session bus: same as "no extension"
        value = None
    _state.update(at=now, value=value, failed_at=0.0 if value else now)
    if value is None and not _state["warned"]:
        _state["warned"] = True
        log.warning("the Dropwheel GNOME Shell extension does not answer: Shift during a drag and the "
                    "pointer position need it (log out and back in after installing it)")
    elif value is not None:
        _state["warned"] = False
    return value


def extension_ready() -> bool:
    return pointer() is not None


def _mods() -> int:
    p = pointer()
    return p[2] if p else 0


def key_down(vk: int) -> bool:
    return vk == VK_SHIFT and bool(_mods() & SHIFT_MASK)


def primary_button_down() -> bool:
    return bool(_mods() & BUTTON1_MASK)


def any_button_down() -> bool:
    return bool(_mods() & (BUTTON1_MASK | BUTTON2_MASK | BUTTON3_MASK))


def cursor() -> tuple[int, int] | None:
    p = pointer()
    return (p[0], p[1]) if p else None


# ================================================================================ X11 shapes

_SHAPE_INPUT, _SHAPE_SET = 2, 0
_x11: dict[str, Any] = {}
_shaped: dict[int, bool] = {}


def _xlib() -> dict[str, Any] | None:
    """libX11 + libXext and a display connection of our own (shapes are server-side state)."""
    if _x11:
        return _x11 if _x11.get("dpy") else None
    _x11["dpy"] = None
    try:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() != "xcb":
            return None
        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        xext = ctypes.CDLL(ctypes.util.find_library("Xext") or "libXext.so.6")
    except (OSError, ImportError):
        return None
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XFlush.argtypes = [ctypes.c_void_p]
    xext.XShapeCombineRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int]
    xext.XShapeCombineMask.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_ulong, ctypes.c_int]
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return None
    _x11.update(dpy=dpy, x11=x11, xext=xext)
    return _x11


def set_window_style(wid: int, *, click_through: bool, no_activate: bool) -> None:  # noqa: ARG001
    """Input pass-through: an empty X11 input shape, or back to the whole window.
    (``no_activate`` is the ``Qt.WindowDoesNotAcceptFocus`` flag, set by the caller.)"""
    if _shaped.get(wid) == click_through:
        return
    x = _xlib()
    if x is None:
        return
    if click_through:
        x["xext"].XShapeCombineRectangles(x["dpy"], wid, _SHAPE_INPUT, 0, 0, None, 0, _SHAPE_SET, 0)
    else:
        x["xext"].XShapeCombineMask(x["dpy"], wid, _SHAPE_INPUT, 0, 0, 0, _SHAPE_SET)
    x["x11"].XFlush(x["dpy"])
    _shaped[wid] = click_through


def forget_window(wid: int) -> None:
    """A hidden window loses its shape on X11: apply it again when it is shown."""
    _shaped.pop(wid, None)


def register_hotkey(ident: int, mods: int, vk: int) -> bool:  # noqa: ARG001
    return False  # GNOME: a custom keyboard shortcut runs "dropwheel open" (dropwheel setup gnome)


def unregister_hotkey(ident: int) -> None:  # noqa: ARG001
    return None


def foreground(wid: int) -> None:  # noqa: ARG001
    # shortcut: Qt's requestActivate only; Mutter's focus-stealing prevention may leave the
    # editor unfocused until clicked. Upgrade with _NET_ACTIVE_WINDOW (source = pager) if it does.
    return None
