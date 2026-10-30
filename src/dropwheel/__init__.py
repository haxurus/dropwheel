"""Dropwheel: a radial "drag + Shift" file-action wheel for Hyprland + Quickshell (Caelestia).

The package is split into a backend core (config, i18n, detection, tool discovery, action
registry, job runner) and action modules under :mod:`dropwheel.actions`.  Importing the
package itself is cheap: no optional third-party module is imported here.
"""

from __future__ import annotations

__version__ = "1.0.0"

#: Version of the QML <-> daemon JSON-lines protocol (``{"event": "ready", "version": 1}``).
PROTOCOL_VERSION = 1

#: Human readable application name (notifications, window titles).
APP_NAME = "Dropwheel"

#: Machine identifier (config/cache/state directory names, desktop entry, global shortcut appid).
APP_ID = "dropwheel"

__all__ = ["__version__", "PROTOCOL_VERSION", "APP_NAME", "APP_ID"]
