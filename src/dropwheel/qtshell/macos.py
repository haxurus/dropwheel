"""What the host needs from macOS, with the same names as :mod:`win32`, through ctypes (no PyObjC).

* Shift and the mouse buttons: Quartz ``CGEventSourceFlagsState`` / ``CGEventSourceButtonState``
  on the combined session state.  They read the current state and install no event tap: Apple
  documents the Input Monitoring / Accessibility prompts for event taps, not for these calls
  (not checked on a real Mac);
* the cursor: ``CGEventGetLocation(CGEventCreate(NULL))``, global display points with the origin
  at the top left of the main display, y down: the same space as Qt's logical coordinates on
  macOS, Retina included (points, not pixels);
* the windows: ``winId()`` is an ``NSView``; its ``NSWindow`` gets ``ignoresMouseEvents`` (input
  pass-through), a level above the Dock and menu bar, ``canJoinAllSpaces | fullScreenAuxiliary``
  (it shows on every Space and over full-screen apps) and ``hidesOnDeactivate = NO`` (Qt makes
  ``Qt.Tool`` windows panels that hide whenever the app is not frontmost, and the host never is);
* no global shortcut: ``RegisterEventHotKey`` (Carbon) is left out, see :func:`register_hotkey`.

Every function is a harmless no-op off macOS, so the host also runs on Linux for tests.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

MACOS = sys.platform == "darwin"
#: set by the host once Qt runs on the cocoa platform (``False`` offscreen, in the tests)
ACTIVE = False

VK_SHIFT = 0x10  # the win32 name, so that callers stay platform-neutral
COMBINED_SESSION_STATE = 0          # kCGEventSourceStateCombinedSessionState
FLAG_SHIFT = 0x00020000             # kCGEventFlagMaskShift
BUTTON_LEFT, BUTTON_RIGHT = 0, 1    # kCGMouseButtonLeft, kCGMouseButtonRight
STATUS_WINDOW_LEVEL = 25            # NSStatusWindowLevel: above the Dock (20) and the menu bar (24)
JOIN_ALL_SPACES, FULL_SCREEN_AUXILIARY = 1 << 0, 1 << 8  # NSWindowCollectionBehavior
ACTIVATION_ACCESSORY = 1            # NSApplicationActivationPolicyAccessory: no Dock icon, no menu bar


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


_FRAMEWORK = "/System/Library/Frameworks/{0}.framework/{0}"
if MACOS:
    _cg = ctypes.CDLL(_FRAMEWORK.format("CoreGraphics"))
    _cf = ctypes.CDLL(_FRAMEWORK.format("CoreFoundation"))
    _objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    _cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
    _cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
    _cg.CGEventSourceButtonState.argtypes = [ctypes.c_int32, ctypes.c_uint32]
    _cg.CGEventSourceButtonState.restype = ctypes.c_bool
    _cg.CGEventCreate.argtypes = [ctypes.c_void_p]
    _cg.CGEventCreate.restype = ctypes.c_void_p
    _cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
    _cg.CGEventGetLocation.restype = CGPoint
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
    _objc.objc_getClass.argtypes = [ctypes.c_char_p]
    _objc.objc_getClass.restype = ctypes.c_void_p
    _objc.sel_registerName.argtypes = [ctypes.c_char_p]
    _objc.sel_registerName.restype = ctypes.c_void_p


def _msg(receiver: int, selector: str, *args: Any, restype: Any = ctypes.c_void_p,
         argtypes: tuple[Any, ...] = ()) -> Any:
    """``[receiver selector args…]``.  objc_msgSend is cast to the exact prototype each time:
    on Apple Silicon a variadic call would pass the arguments in the wrong registers."""
    fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
        ctypes.cast(_objc.objc_msgSend, ctypes.c_void_p).value)
    return fn(receiver, _objc.sel_registerName(selector.encode()), *args)


def key_down(vk: int) -> bool:
    if not MACOS or vk != VK_SHIFT:
        return False
    return bool(_cg.CGEventSourceFlagsState(COMBINED_SESSION_STATE) & FLAG_SHIFT)


def _button(button: int) -> bool:
    return MACOS and bool(_cg.CGEventSourceButtonState(COMBINED_SESSION_STATE, button))


def primary_button_down() -> bool:
    # shortcut: assumes the left button is the primary one; macOS swaps buttons only for some devices
    return _button(BUTTON_LEFT)


def any_button_down() -> bool:
    return _button(BUTTON_LEFT) or _button(BUTTON_RIGHT)


def cursor() -> tuple[int, int] | None:
    if not MACOS:
        return None
    event = _cg.CGEventCreate(None)
    if not event:
        return None
    try:
        p = _cg.CGEventGetLocation(event)
    finally:
        _cf.CFRelease(event)
    return round(p.x), round(p.y)


_styled: dict[int, tuple[bool, bool]] = {}


def set_window_style(wid: int, *, click_through: bool, no_activate: bool) -> None:
    """Input pass-through and the always-on-top behaviour of the window of the ``NSView`` ``wid``.
    (``no_activate`` is the ``Qt.WindowDoesNotAcceptFocus`` flag, set by the caller.)"""
    if not MACOS or not wid or _styled.get(wid) == (click_through, no_activate):
        return
    window = _msg(wid, "window")
    if not window:
        return
    _msg(window, "setIgnoresMouseEvents:", click_through, restype=None, argtypes=(ctypes.c_bool,))
    _msg(window, "setLevel:", STATUS_WINDOW_LEVEL, restype=None, argtypes=(ctypes.c_long,))
    _msg(window, "setCollectionBehavior:", JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY, restype=None,
         argtypes=(ctypes.c_ulong,))
    _msg(window, "setHidesOnDeactivate:", False, restype=None, argtypes=(ctypes.c_bool,))
    _styled[wid] = (click_through, no_activate)


def forget_window(wid: int) -> None:
    """Qt may reset the window's settings when it hides it or changes its flags: apply them again."""
    _styled.pop(wid, None)


def _app() -> int | None:
    cls = _objc.objc_getClass(b"NSApplication")
    return _msg(cls, "sharedApplication") if cls else None


def init_app() -> None:
    """An accessory app: no Dock icon and no menu bar, like a menu bar utility."""
    app = _app() if MACOS else None
    if app:
        _msg(app, "setActivationPolicy:", ACTIVATION_ACCESSORY, restype=ctypes.c_bool, argtypes=(ctypes.c_long,))


def foreground(wid: int) -> None:  # noqa: ARG001
    """Bring the host forward, so that the editor windows get the keyboard.
    ``activateIgnoringOtherApps:`` is deprecated since macOS 14 but still honoured (not checked there)."""
    app = _app() if MACOS else None
    if app:
        _msg(app, "activateIgnoringOtherApps:", True, restype=None, argtypes=(ctypes.c_bool,))


def register_hotkey(ident: int, mods: int, vk: int) -> bool:  # noqa: ARG001
    # shortcut: no global shortcut on macOS; Carbon RegisterEventHotKey through ctypes needs an event
    # handler of its own. Shift during a drag, the Finder Quick Action and "dropwheel open" cover it.
    return False


def unregister_hotkey(ident: int) -> None:  # noqa: ARG001
    return None
