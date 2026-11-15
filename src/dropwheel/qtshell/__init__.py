"""The Qt front end for Windows, macOS and GNOME: the Linux QML shell hosted by PySide6
(``python -m dropwheel.qtshell``).

:mod:`.host` loads ``shell/shell.qml`` with the Quickshell stand-ins in ``qml/`` (backed by
:mod:`.native` and :mod:`.virtual`); :mod:`.control` starts it and talks to it.  The desktop's
answers come from :mod:`.win32` or :mod:`.gnome`; :mod:`.explorer` and :mod:`.gnome_setup` add
the Explorer and GNOME entries.  On Hyprland, Quickshell runs the shell and nothing here is used.
"""
