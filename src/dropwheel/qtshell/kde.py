"""What the host needs from a KDE Plasma session, with the same names as :mod:`win32` and :mod:`gnome`.

The host runs under X11 (Plasma on X11, or XWayland on Wayland), so window placement, keep-above
and click-through are :mod:`gnome`'s X11 code.  The questions are answered like this:

* **Shift and the mouse buttons**: ``XQueryPointer``'s mask.  On X11 that is the whole truth.  On
  Wayland, KWin forwards the modifier keys to XWayland even while no X window has the focus
  (System Settings → Legacy X11 App Support, ``XwaylandEavesdrops``, on by default since Plasma
  6.2), so Shift is seen during a drag from Dolphin; the mouse buttons are seen only over X windows.
* **a drag in progress** (Wayland): KWin claims the ``XdndSelection`` of XWayland every time a
  Wayland drag starts, which XFixes reports.  The end of a drag is not reported, so a drag
  counts as held for :data:`DRAG_S` seconds after it started: wrongly so after a short drag,
  and a drag longer than that is no longer seen.
* **the pointer position** (Wayland): the Dropwheel KWin script (``integrations/kde/``) calls
  ``io.github.haxurus.Dropwheel.SetPointer(x, y, width, height)`` on the host while the
  pointer moves (KWin's ``workspace.cursorPos``; XWayland only knows it over X windows).  The
  same script registers the keyboard shortcut, which calls ``Open()``.

Same D-Bus name, path and interface as the GNOME extension, the other way round: here the host
owns the name and KWin calls it.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import time
from typing import Any, Callable

from . import gnome
from .gnome import BUTTON1_MASK, BUTTON2_MASK, BUTTON3_MASK, INTERFACE, PATH, SERVICE, SHIFT_MASK, VK_SHIFT
# the X11 side is the same as on GNOME (the KWin script registers the shortcut, see serve())
from .gnome import forget_window, foreground, register_hotkey, set_window_style, unregister_hotkey  # noqa: F401

log = logging.getLogger(__name__)

SCRIPT_ID = "dropwheel"
#: the host runs in a Plasma session (``XDG_CURRENT_DESKTOP`` is ``KDE``)
SESSION = "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")

# shortcut: KWin reports when a Wayland drag starts, not when it ends; a Shift tap within this many
# seconds counts as "during a drag". Upgrade if KWin's scripting API ever exposes the button state.
DRAG_S = 10.0
_CACHE_S = 0.010

_state: dict[str, Any] = {"at": 0.0, "mask": 0, "drag_at": -DRAG_S, "pushed": None, "warned": False,
                          "xfixes": None, "service": None}


def wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


# ================================================================================ X11 queries

def _x() -> dict[str, Any] | None:
    """:mod:`gnome`'s display connection, plus XQueryPointer and the XFixes selection events."""
    x = gnome._xlib()
    if x is None or "query" in x:
        return x
    x11 = x["x11"]
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
    x11.XPending.argtypes = [ctypes.c_void_p]
    x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x["root"] = x11.XDefaultRootWindow(x["dpy"])
    x["query"] = True
    try:
        xfixes = ctypes.CDLL(ctypes.util.find_library("Xfixes") or "libXfixes.so.3")
        base, error = ctypes.c_int(), ctypes.c_int()
        if xfixes.XFixesQueryExtension(ctypes.c_void_p(x["dpy"]), ctypes.byref(base), ctypes.byref(error)):
            xfixes.XFixesSelectSelectionInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                                          ctypes.c_ulong]
            atom = x11.XInternAtom(x["dpy"], b"XdndSelection", 0)
            xfixes.XFixesSelectSelectionInput(x["dpy"], x["root"], atom, 1)  # SetSelectionOwnerNotifyMask
            x11.XFlush(x["dpy"])
            _state["xfixes"] = base.value  # XFixesSelectionNotify = event base + 0
    except OSError:
        log.info("libXfixes not found: drags from Wayland programs are not seen")
    return x


def _query(x: dict[str, Any], now: float) -> int:
    x11 = x["x11"]
    win = ctypes.c_ulong()
    pos = [ctypes.c_int() for _ in range(4)]
    mask = ctypes.c_uint()
    ok = x11.XQueryPointer(x["dpy"], x["root"], ctypes.byref(win), ctypes.byref(win),
                           *[ctypes.byref(p) for p in pos], ctypes.byref(mask))
    if _state["xfixes"] is not None:
        event = (ctypes.c_long * 24)()  # sizeof(XEvent)
        while x11.XPending(x["dpy"]):
            x11.XNextEvent(x["dpy"], ctypes.byref(event))
            if ctypes.c_int.from_buffer(event).value == _state["xfixes"]:
                _state["drag_at"] = now
    return mask.value if ok else 0


def mods(now: float | None = None) -> int:
    """The Clutter-style mask of :mod:`gnome`: Shift and buttons 1–3, button 1 also for a recent drag."""
    if not gnome.ACTIVE:
        return 0
    now = time.monotonic() if now is None else now
    if now - _state["at"] >= _CACHE_S:
        x = _x()
        _state.update(at=now, mask=_query(x, now) if x is not None else 0)
    value = _state["mask"] & (SHIFT_MASK | BUTTON1_MASK | BUTTON2_MASK | BUTTON3_MASK)
    if wayland() and now - _state["drag_at"] < DRAG_S:
        value |= BUTTON1_MASK
    return value


def key_down(vk: int) -> bool:
    return vk == VK_SHIFT and bool(mods() & SHIFT_MASK)


def primary_button_down() -> bool:
    return bool(mods() & BUTTON1_MASK)


def any_button_down() -> bool:
    return bool(mods() & (BUTTON1_MASK | BUTTON2_MASK | BUTTON3_MASK))


def cursor() -> tuple[int, int] | None:
    """The position the KWin script sent, in Qt's coordinates; ``None`` (Qt's own cursor) on X11."""
    if not (gnome.ACTIVE and wayland()):
        return None
    pushed = _state["pushed"]
    if pushed is None:
        if not _state["warned"]:
            _state["warned"] = True
            log.warning("the Dropwheel KWin script has not sent the pointer position yet: "
                        "dropwheel setup kde (then move the mouse)")
        return None
    return pushed


def set_pointer(x: float, y: float, width: float, height: float) -> None:
    """``SetPointer``: KWin's logical pixels, relative to the virtual screen, to Qt's (XWayland
    may scale the X11 screen when "Legacy applications: apply scaling themselves" is chosen)."""
    from PySide6.QtGui import QGuiApplication

    rects = [(s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height(),
              s.devicePixelRatio()) for s in QGuiApplication.screens()]
    pushed = to_qt(x, y, width, height, rects)
    if pushed is not None:
        _state["pushed"] = pushed


def to_qt(x: float, y: float, width: float, height: float,
          rects: list[tuple[int, int, int, int, float]]) -> tuple[int, int] | None:
    """KWin's position to Qt's, given each screen's Qt geometry and device pixel ratio.  Qt's xcb
    plugin keeps every screen's origin in X11 pixels but divides its size by the ratio, so the X11
    pixel is found first (linear over the X11 root), then made relative to its screen's origin."""
    if not rects or width <= 0 or height <= 0:
        return None
    nx = x * max(gx + gw * d for gx, _, gw, _, d in rects) / width
    ny = y * max(gy + gh * d for _, gy, _, gh, d in rects) / height
    gx, gy, _, _, d = next((r for r in rects if r[0] <= nx < r[0] + r[2] * r[4] and r[1] <= ny < r[1] + r[3] * r[4]),
                           rects[0])
    return gx + round((nx - gx) / d), gy + round((ny - gy) / d)


# ================================================================================ D-Bus service

def serve(on_open: Callable[[], None]) -> bool:
    """Own ``io.github.haxurus.Dropwheel`` on the session bus for the KWin script."""
    from PySide6.QtCore import ClassInfo, QObject, Slot
    from PySide6.QtDBus import QDBusConnection, QDBusConnectionInterface

    @ClassInfo({"D-Bus Interface": INTERFACE})
    class Service(QObject):
        # KWin turns the script's numbers into D-Bus integers or doubles: accept both
        @Slot(int, int, int, int)
        @Slot(float, float, float, float)
        def SetPointer(self, x: float, y: float, width: float, height: float) -> None:  # noqa: N802
            set_pointer(x, y, width, height)

        @Slot()
        def Open(self) -> None:  # noqa: N802
            on_open()

    bus = QDBusConnection.sessionBus()
    service = Service()
    if not (bus.isConnected() and bus.registerObject(PATH, service, QDBusConnection.RegisterOption.ExportAllSlots)):
        log.warning("cannot own %s on the session bus: no pointer position or shortcut from KWin", SERVICE)
        return False
    # queued: a host that is still quitting keeps the name a moment longer, then the bus hands it over
    reply = bus.interface().registerService(SERVICE, QDBusConnectionInterface.ServiceQueueOptions.QueueService,
                                            QDBusConnectionInterface.ServiceReplacementOptions.DontAllowReplacement)
    if not reply.isValid() or reply.value() not in (1, 2):  # ServiceRegistered, ServiceQueued
        log.warning("cannot own %s on the session bus: no pointer position or shortcut from KWin", SERVICE)
        return False
    _state["service"] = service  # keep it alive
    return True
