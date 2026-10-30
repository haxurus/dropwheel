"""Tests for dropwheel.tools, dropwheel.errors, dropwheel.notify and the action loader."""

from __future__ import annotations

import sys

import pytest

from dropwheel import actions, errors, notify, tools


@pytest.fixture(autouse=True)
def fresh_tools():
    tools.refresh()
    yield
    tools.refresh()


def test_install_hint_grouping(monkeypatch):
    monkeypatch.setattr(tools, "aur_helper", lambda: "paru")
    assert tools.install_hint(["ffmpeg"]) == "sudo pacman -S --needed ffmpeg"
    assert tools.install_hint(["ocrmypdf"]) == "paru -S ocrmypdf"
    assert tools.install_hint(["rembg"]) == "pipx install 'rembg[cpu,cli]'"
    combined = tools.install_hint(["ffmpeg", "ffprobe", "ocrmypdf", "tesseract", "py:yaml"])
    assert combined == ("sudo pacman -S --needed ffmpeg tesseract tesseract-data-ita tesseract-data-eng python-yaml"
                        " && paru -S ocrmypdf")
    assert tools.install_hint([]) == ""
    assert tools.install_hint("pdftotext") == "sudo pacman -S --needed poppler"
    monkeypatch.setitem(tools.TOOLS, "py:libonpypi", tools._py("libonpypi", "libonpypi", repo="pypi"))
    assert tools.install_hint(["py:libonpypi"]).startswith("python -m pip install")
    assert "py:pymupdf4llm" not in tools.TOOLS  # doctor must not advise what cannot be done on Arch (see pdf._markdown_llm)
    assert tools.install_hint(["some-unknown-binary"]) == "sudo pacman -S --needed some-unknown-binary"


def test_path_have_missing():
    assert tools.path(sys.executable) == sys.executable
    assert tools.have(sys.executable)
    assert tools.path("py:json") is not None
    assert not tools.have("dropwheel-test-missing-tool")
    assert not tools.have("py:dropwheel_no_such_module")
    assert tools.path("") is None
    assert tools.missing(["dropwheel-test-missing-tool", sys.executable]) == ["dropwheel-test-missing-tool"]
    assert tools.missing([("dropwheel-test-missing-tool", sys.executable)]) == []
    assert tools.missing([("dropwheel-a-missing", "dropwheel-b-missing")]) == ["dropwheel-a-missing"]
    assert tools.missing("py:json") == []
    assert tools.missing(None) == []


def test_exiftool_absolute_path_first(monkeypatch):
    target = "/usr/bin/vendor_perl/exiftool"
    monkeypatch.setattr(tools, "_is_exe", lambda p: p == target)
    monkeypatch.setattr(tools, "_which", lambda b: None)
    tools.refresh()
    assert tools.path("exiftool") == target


def test_display_name_and_report():
    assert tools.display_name("py:yaml") == "python-yaml"
    assert tools.display_name("realesrgan") == "realesrgan-ncnn-vulkan"
    assert tools.display_name("ffmpeg") == "ffmpeg"
    rep = tools.report()
    names = {r["name"] for r in rep["tools"]}
    for required in ("magick", "ffmpeg", "ffprobe", "gs", "qpdf", "soffice", "exiftool", "ocrmypdf", "7z", "rembg",
                     "wl-copy", "notify-send", "py:PIL", "py:pymupdf", "py:rarfile"):
        assert required in names
    assert rep["available"] + len(rep["missing"]) == len(rep["tools"])
    assert rep["python"] and rep["executable"]


def test_errors():
    e = errors.UserError("err.file_not_found", details=["a", "b"], name="x.pdf")
    assert e.to_json("it") == {"message": "File non trovato: x.pdf", "details": "a\nb", "install": ""}
    assert str(e) == "File not found: x.pdf"
    lit = errors.UserError("Plain text message")
    assert lit.text("it") == "Plain text message"
    assert issubclass(errors.ToolError, errors.UserError)
    mt = errors.MissingTool(["ffmpeg", "py:yaml"])
    assert mt.install == "sudo pacman -S --needed ffmpeg python-yaml"
    assert mt.text("en") == "Missing tool: ffmpeg, python-yaml"
    assert mt.names == ["ffmpeg", "py:yaml"]
    c = errors.Cancelled()
    assert isinstance(c, errors.DropwheelError) and c.partial is None


def test_notify_disabled_and_actions():
    assert notify.notify("x", "y") is False
    acts = notify.normalize_actions([("open", "Apri", "open", "/a.png"),
                                     {"id": "reveal", "label": "Mostra", "paths": ["/a.png", "/b.png"]},
                                     {"id": "bad=id", "label": "x"}, "junk"])
    assert acts == [{"id": "open", "label": "Apri", "op": "open", "paths": ["/a.png"]},
                    {"id": "reveal", "label": "Mostra", "op": "reveal", "paths": ["/a.png", "/b.png"]}]
    argv = notify._notify_send_argv("Done", "a<b", "icon", "normal", 5000, "Dropwheel", acts)
    assert argv[:2] == ["notify-send", "--app-name=Dropwheel"]
    assert "--action=open=Apri" in argv and "--expire-time=5000" in argv
    assert argv[-3:] == ["--", "Done", "a&lt;b"]


def test_notify_without_tools(monkeypatch):
    monkeypatch.delenv("DROPWHEEL_NO_NOTIFY")
    monkeypatch.setattr(tools, "have", lambda name: False)
    assert notify.notify("x", "y") is False
    assert notify.toast("x", "y", kind="error") is False


def test_load_all_records_failures(monkeypatch):
    # a private error table: the failures recorded here must not leak into later tests
    monkeypatch.setattr(actions, "LOAD_ERRORS", {})
    errs = actions.load_all()
    assert isinstance(errs, dict)
    for name in actions.MODULES:
        assert name in actions.loaded_modules() or name in errs
    monkeypatch.setattr(actions, "MODULES", ("definitely_missing_module",))
    errs = actions.load_all()
    assert "definitely_missing_module" in errs and "not found" in errs["definitely_missing_module"]


def test_load_all_broken_module(monkeypatch, tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    import importlib

    real_import = importlib.import_module

    def fake_import(name, *a, **kw):
        if name.endswith(".broken_mod"):
            raise RuntimeError("import-time bug")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(actions.importlib, "import_module", fake_import)
    monkeypatch.setattr(actions, "MODULES", ("broken_mod",))
    monkeypatch.setattr(actions, "LOAD_ERRORS", {})
    errs = actions.load_all()
    assert "RuntimeError: import-time bug" in errs["broken_mod"]


def test_notify_helper_ignores_the_current_directory(monkeypatch):
    """With -m Python puts the cwd first on sys.path: a stray json.py there would run. -P turns that off."""
    monkeypatch.delenv("DROPWHEEL_NO_NOTIFY")
    monkeypatch.setattr(tools, "have", lambda name: name == "notify-send")
    spawned: list[list[str]] = []
    monkeypatch.setattr(notify.util, "detach", lambda argv, **kw: spawned.append(argv) or True)
    assert notify.notify("Done", "x", actions=[("open", "Apri", "open", "/a.png")]) is True
    assert spawned[0][1:4] == ["-P", "-m", "dropwheel.notify"]
