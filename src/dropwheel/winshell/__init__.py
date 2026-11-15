"""The Windows front end: the Linux QML shell hosted by PySide6 (``python -m dropwheel.winshell``).

:mod:`.host` loads ``shell/shell.qml`` with the Quickshell stand-ins in ``qml/`` (backed by
:mod:`.native` and :mod:`.virtual`); :mod:`.control` starts it and talks to it; :mod:`.explorer`
adds the Explorer entries.  Nothing here is imported on Linux, where Quickshell runs the shell.
"""
