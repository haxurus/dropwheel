"""Shared pytest fixtures.

* every test runs with private XDG directories (config/cache/state/data/downloads), a dedicated
  "volatile" directory (``DROPWHEEL_VOLATILE_DIRS``), English locale and notifications disabled;
* ``clean_registry`` gives a test an empty action registry and restores it afterwards;
* ``make_runner`` builds a :class:`dropwheel.jobs.JobRunner` that records its events.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def dropwheel_env(tmp_path_factory, monkeypatch):
    """Isolated XDG environment for every test."""
    base = tmp_path_factory.mktemp("dwenv")
    dirs = {}
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"),
                     ("XDG_DATA_HOME", "data")):
        d = base / sub
        d.mkdir()
        monkeypatch.setenv(var, str(d))
        dirs[sub] = d
    downloads = base / "Downloads"
    downloads.mkdir()
    volatile = base / "volatile"
    volatile.mkdir()
    work = base / "work"
    work.mkdir()
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(downloads))
    monkeypatch.setenv("DROPWHEEL_VOLATILE_DIRS", str(volatile))
    monkeypatch.setenv("DROPWHEEL_NO_NOTIFY", "1")
    monkeypatch.delenv("DROPWHEEL_CONFIG", raising=False)
    # No display: a pytest started from a Hyprland terminal must leave the clipboard and the compositor alone.
    for var in ("WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    from dropwheel import i18n

    i18n.reload()
    yield SimpleNamespace(base=base, downloads=downloads, volatile=volatile, work=work, **dirs)
    i18n.reload()


@pytest.fixture
def clean_registry():
    """An empty action registry for the duration of the test."""
    from dropwheel import registry

    state = registry.snapshot()
    registry.clear()
    try:
        yield registry
    finally:
        registry.restore(state)


@pytest.fixture
def make_runner():
    """Factory: ``make_runner(config=None, lang="en")`` -> (runner, events list)."""
    from dropwheel import config as cfgmod
    from dropwheel.jobs import JobRunner

    created = []

    def factory(config=None, lang="en", **kw):
        events: list[dict] = []
        cfg = config if config is not None else cfgmod.load()
        runner = JobRunner(cfg, lang, events.append, notify_fn=False, **kw)
        created.append(runner)
        return runner, events

    yield factory
    for runner in created:
        runner.shutdown(wait=True)
