"""The Qt front end for Windows, GNOME and KDE Plasma: the Linux QML shell hosted by PySide6
(``python -m dropwheel.qtshell``).

:mod:`.host` loads ``shell/shell.qml`` with the Quickshell stand-ins in ``qml/`` (backed by
:mod:`.native` and :mod:`.virtual`); :mod:`.control` starts it and talks to it.  The desktop's
answers come from :mod:`.win32`, :mod:`.gnome` or :mod:`.kde`; :mod:`.explorer`, :mod:`.gnome_setup`
and :mod:`.kde_setup` add the Explorer, GNOME and Plasma entries.  On Hyprland, Quickshell runs the shell and nothing here is used.
"""
