"""The few Win32 calls the Windows host needs, through ctypes (no pywin32).

Every function is a harmless no-op off Windows, so the host also runs on Linux for tests.
"""

from __future__ import annotations

import ctypes
import os

WINDOWS = os.name == "nt"

VK_LBUTTON, VK_RBUTTON, VK_SHIFT, VK_ESCAPE = 0x01, 0x02, 0x10, 0x1B
SM_SWAPBUTTON = 23
GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE = 0x00080000, 0x00000020, 0x08000000
LWA_ALPHA = 0x2
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
ASFW_ANY = -1

if WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]


def key_down(vk: int) -> bool:
    """``True`` while the key (or mouse button) ``vk`` is physically held."""
    if not WINDOWS:
        return False
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def primary_button_down() -> bool:
    """The primary mouse button is held (the right one when the buttons are swapped)."""
    if not WINDOWS:
        return False
    vk = VK_RBUTTON if _user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
    return key_down(vk)


def any_button_down() -> bool:
    return key_down(VK_LBUTTON) or key_down(VK_RBUTTON)


def set_window_style(hwnd: int, *, click_through: bool, no_activate: bool) -> None:
    """Toggle input pass-through and activation on a top-level window.

    Pass-through needs WS_EX_LAYERED as well as WS_EX_TRANSPARENT (what Qt itself does for
    ``Qt.WindowTransparentForInput``); a window turned layered gets full opacity right away, or
    it would stop being drawn.  The layered bit is kept once set, only the other two change."""
    if not WINDOWS or not hwnd:
        return
    old = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    new = old & ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
    if click_through:
        new |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    if no_activate:
        new |= WS_EX_NOACTIVATE
    if new == old:
        return
    _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
    if new & WS_EX_LAYERED and not old & WS_EX_LAYERED:
        _user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)


def register_hotkey(ident: int, mods: int, vk: int) -> bool:
    """``RegisterHotKey`` on the thread queue (WM_HOTKEY reaches the native event filter)."""
    if not WINDOWS:
        return False
    return bool(_user32.RegisterHotKey(None, ident, mods | MOD_NOREPEAT, vk))


def unregister_hotkey(ident: int) -> None:
    if WINDOWS:
        _user32.UnregisterHotKey(None, ident)


def foreground(hwnd: int) -> None:
    """Best effort: Windows only lets a process that received the last input (or was allowed to)
    take the foreground."""
    if WINDOWS and hwnd:
        _user32.SetForegroundWindow(hwnd)


def allow_foreground() -> None:
    """Let the host take the foreground (called by the process the user just interacted with)."""
    if WINDOWS:
        _user32.AllowSetForegroundWindow(ASFW_ANY)


def hotkey_id(msg_address: int) -> int | None:
    """The id of a WM_HOTKEY ``MSG`` at ``msg_address``, else ``None``."""
    if not WINDOWS or not msg_address:
        return None
    msg = wintypes.MSG.from_address(msg_address)
    return int(msg.wParam) if msg.message == WM_HOTKEY else None


_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN,
         "super": MOD_WIN}
_NAMED = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
          "pageup": 0x21, "pagedown": 0x22}


def parse_shortcut(text: str) -> tuple[int, int] | None:
    """``"Ctrl+Alt+D"`` → ``(modifiers, virtual key)``; ``None`` when it is not valid.

    At least one modifier is required, except for function keys."""
    parts = [p.strip().lower() for p in str(text).split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods |= _MODS[p]
    key = parts[-1]
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        vk = ord(key.upper())
    elif key[0] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in _NAMED:
        vk = _NAMED[key]
    else:
        return None
    if not mods and not 0x70 <= vk <= 0x87:
        return None
    return mods, vk
