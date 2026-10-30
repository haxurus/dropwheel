"""Cross-module integration: every action module loads, and the wheel menu built for a sample of
every file category (single and multi selections) respects the SPEC §3.2 / §5.3 / §5.6 contracts.

For each sample selection and for both UI languages the test builds the real menu with
``registry.build_menu`` (all action modules loaded) and checks:

* the category detected for the selection;
* no ring holds more than 8 items, no group is empty, single-child groups are flattened
  (except ``*.convert``), every group default points to a leaf of that group;
* every visible string (page, node, param, option, hint, description) is translated: no raw dotted
  i18n key ever reaches the shell, and every key-like label used by a menu node exists in **both**
  the Italian and the English fragments (format names such as ``"PNG"`` are literal labels);
* ids: action ids are registered, unique inside each ring, group ids are unique in the menu;
* every ``mode``/``dialog`` value and every param ``type`` is one of the SPEC names; dialog actions
  declare the structured param their editor produces (SPEC §5.6);
* unavailable actions carry an install hint; multi-file menus never offer ``multi="single"`` actions;
* the whole menu is JSON-serialisable.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import struct
import tomllib
import zipfile
from pathlib import Path

import pytest

from dropwheel import config as cfgmod
from dropwheel import i18n, registry

#: An i18n key shape ("img.act.png", "grp.more"); a label like this must never reach the UI.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
#: SPEC §5.6 dialog names (None = no dialog).
SPEC_DIALOGS = {None, "form", "crop", "redact", "trim", "pages", "metadata", "adjust", "background"}
#: SPEC §3.2 param types.
SPEC_PARAM_TYPES = {"int", "float", "slider", "bool", "select", "text", "password", "color", "time", "timerange",
                    "pages", "path", "dir", "textarea", "rect", "boxes", "pageops", "metadata", "adjust",
                    "background"}
SPEC_MODES = {"instant", "quick", "dialog"}
#: Structured param each editor produces (SPEC §5.6).
DIALOG_PARAM = {"crop": {"rect"}, "redact": {"boxes"}, "pages": {"pageops"}, "metadata": {"metadata"},
                "adjust": {"adjust"}, "background": {"background"}}
MAX_RING = 8


# ------------------------------------------------------------------------------------ samples

def _font_file(tmp: Path) -> Path | None:
    """A real TrueType font: a system one, else one generated with fontTools, else None."""
    candidates = [Path(r"C:\Windows\Fonts\arial.ttf")]
    for root in ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.local/share/fonts")):
        if os.path.isdir(root):
            for dirpath, _dirs, names in os.walk(root):
                candidates.extend(Path(dirpath) / n for n in sorted(names) if n.lower().endswith(".ttf"))
                if len(candidates) > 4:
                    break
    for cand in candidates:
        if cand.is_file():
            dest = tmp / "font.ttf"
            shutil.copyfile(cand, dest)
            return dest
    try:
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen
    except ImportError:
        return None
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({0x41: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)); pen.lineTo((500, 700)); pen.lineTo((1000, 0)); pen.closePath()
    glyph = pen.glyph()
    empty = TTGlyphPen(None).glyph()
    fb.setupGlyf({".notdef": empty, "A": glyph})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "A": (1000, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Dropwheel Test", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    dest = tmp / "font.ttf"
    fb.save(str(dest))
    return dest


def _ooxml(path: Path, part: str, content_type: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   f'<Override PartName="/{part}" ContentType="{content_type}"/></Types>')
        z.writestr(part, '<?xml version="1.0" encoding="UTF-8"?><root/>')
    return path


@pytest.fixture(scope="module")
def samples(tmp_path_factory) -> dict[str, list[str]]:
    """One sample per category (+ the multi selections), as path / URL strings."""
    from PIL import Image

    d = tmp_path_factory.mktemp("integration samples")
    Image.new("RGB", (64, 48), (220, 40, 40)).save(d / "foto rossa.png")
    Image.new("RGB", (48, 64), (40, 200, 40)).save(d / "-verde.png")
    frames = [Image.new("RGB", (32, 32), c) for c in ((255, 0, 0), (0, 0, 255), (0, 255, 0))]
    frames[0].save(d / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    (d / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
                                '<rect width="20" height="10" fill="red"/></svg>', encoding="utf-8")
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    for name in ("documento.pdf", "altro.pdf"):
        if pymupdf is not None:
            doc = pymupdf.open()
            doc.new_page().insert_text((72, 72), "Ciao")
            doc.save(d / name)
            doc.close()
        else:
            (d / name).write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages"
                                   b"/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox"
                                   b"[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    _ooxml(d / "lettera.docx", "word/document.xml",
           "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
    _ooxml(d / "conti.xlsx", "xl/workbook.xml",
           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    _ooxml(d / "slide.pptx", "ppt/presentation.xml",
           "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml")
    (d / "note.md").write_text("# Titolo\n\nCiao *mondo*\n", encoding="utf-8")
    (d / "appunti.txt").write_text("caffè e cornetto\n", encoding="utf-8")
    (d / "script.py").write_text("import os\nprint(os.name)\n", encoding="utf-8")
    (d / "dati.json").write_text('{"a": 1, "b": [1, 2]}', encoding="utf-8")
    with zipfile.ZipFile(d / "libro.epub", "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container/>')
    (d / "film.srt").write_text("1\n00:00:01,000 --> 00:00:02,500\nCiao\n\n", encoding="utf-8")
    (d / "canzone.mp3").write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 4000)
    mp4 = (struct.pack(">I", 24) + b"ftypisom" + b"\x00\x00\x02\x00" + b"isomiso2"
           + struct.pack(">I", 8) + b"free" + b"\x00" * 2000)
    (d / "video uno.mp4").write_bytes(mp4)
    (d / "video due.mp4").write_bytes(mp4)
    with zipfile.ZipFile(d / "archivio.zip", "w") as z:
        z.writestr("dentro.txt", "x")
    (d / "cartella").mkdir()
    (d / "cartella" / "file.txt").write_text("x", encoding="utf-8")
    (d / "App-x86_64.AppImage").write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 4096)
    iso = bytearray(0x8800)
    iso[0x8001:0x8006] = b"CD001"
    iso[0x8006] = 1
    (d / "disco.iso").write_bytes(bytes(iso))
    (d / "dati.bin").write_bytes(bytes(range(256)) * 16)
    font = _font_file(d)

    s = lambda *names: [str(d / n) for n in names]  # noqa: E731
    out = {
        "png": s("foto rossa.png"), "gif": s("anim.gif"), "svg": s("logo.svg"), "pdf": s("documento.pdf"),
        "docx": s("lettera.docx"), "xlsx": s("conti.xlsx"), "pptx": s("slide.pptx"), "md": s("note.md"),
        "txt": s("appunti.txt"), "py": s("script.py"), "json": s("dati.json"), "epub": s("libro.epub"),
        "srt": s("film.srt"), "mp3": s("canzone.mp3"), "mp4": s("video uno.mp4"), "zip": s("archivio.zip"),
        "folder": s("cartella"), "url": ["https://example.com/articolo?id=1"],
        "appimage": s("App-x86_64.AppImage"), "iso": s("disco.iso"), "bin": s("dati.bin"),
        "2png": s("foto rossa.png", "-verde.png"), "2pdf": s("documento.pdf", "altro.pdf"),
        "png+pdf": s("foto rossa.png", "documento.pdf"), "png+mp3": s("foto rossa.png", "canzone.mp3"),
        "2mp4": s("video uno.mp4", "video due.mp4"),
    }
    if font is not None:
        out["ttf"] = [str(font)]
    return out


EXPECTED_CATEGORY = {
    "png": "image", "gif": "image", "svg": "svg", "pdf": "pdf", "docx": "document", "xlsx": "spreadsheet",
    "pptx": "presentation", "md": "markdown", "txt": "text", "py": "code", "json": "data", "epub": "ebook",
    "srt": "subtitle", "mp3": "audio", "mp4": "video", "zip": "archive", "ttf": "font", "folder": "folder",
    "url": "url", "appimage": "appimage", "iso": "disk", "bin": "unknown", "2png": "image", "2pdf": "pdf",
    "png+pdf": "mixed", "png+mp3": "mixed", "2mp4": "video",
}
SAMPLES = list(EXPECTED_CATEGORY)


@pytest.fixture(scope="module")
def loaded():
    """Every action module imported and registered.

    ``reload=True``: a test that snapshots and restores the registry around code which imported an
    action module for the first time leaves that module in ``sys.modules`` without its actions.
    """
    import dropwheel.actions as actions

    actions.load_all(reload=True)
    return actions


# --------------------------------------------------------------------------------- helpers

def _raw(lang: str) -> dict[str, str]:
    """The strings of ``lang`` only (no English fallback)."""
    return i18n.load(lang)


def _key_translated(key: str) -> bool:
    """A key-like label must exist in both languages; anything else is a literal label."""
    if not key or not KEY_RE.match(key):
        return True
    return key in _raw("it") and key in _raw("en")


def _check_text(value, where: str, problems: list[str]) -> None:
    if value is None or value == "":
        return
    if not isinstance(value, str):
        problems.append(f"{where}: not a string ({value!r})")
    elif KEY_RE.match(value):
        problems.append(f"{where}: raw i18n key {value!r}")


def _leaf_ids(nodes: list[dict]) -> list[str]:
    out: list[str] = []
    for n in nodes:
        if n.get("type") == "group":
            out.extend(_leaf_ids(n.get("items") or []))
        else:
            out.append(n.get("id"))
    return out


def _check_param(p: dict, where: str, problems: list[str], dynamic_options: bool = False) -> None:
    for field in ("name", "type", "label", "default", "min", "max", "step", "unit", "options", "help",
                  "required", "visible_if"):
        if field not in p:
            problems.append(f"{where}: param JSON lacks {field!r}")
    if p.get("type") not in SPEC_PARAM_TYPES:
        problems.append(f"{where}: param type {p.get('type')!r} not in SPEC §3.2")
    _check_text(p.get("label"), f"{where}.label", problems)
    _check_text(p.get("help"), f"{where}.help", problems)
    _check_text(p.get("unit"), f"{where}.unit", problems)
    options = p.get("options") or []
    for opt in options:
        if not isinstance(opt, dict) or "value" not in opt or "label" not in opt:
            problems.append(f"{where}: malformed option {opt!r}")
            continue
        _check_text(opt["label"], f"{where}.option[{opt['value']!r}]", problems)
    if p.get("type") == "select":
        # Options computed at menu time (desktop apps, KDE Connect devices…) may legitimately be empty
        # on a machine without them; static option lists never may.
        if not options and not dynamic_options:
            problems.append(f"{where}: select without options")
        default = p.get("default")
        values = [o.get("value") for o in options if isinstance(o, dict)]
        if default not in (None, "") and values and default not in values \
                and str(default) not in [str(v) for v in values]:
            problems.append(f"{where}: select default {default!r} not among {values!r}")
    vis = p.get("visible_if")
    if vis is not None and not isinstance(vis, dict):
        problems.append(f"{where}: visible_if must be an object")


def _check_action(n: dict, where: str, n_files: int, problems: list[str]) -> None:
    ident = n.get("id")
    act = registry.get(ident)
    if act is None:
        problems.append(f"{where}: action {ident!r} is not registered")
        return
    for field in ("label", "icon", "text", "available", "hint", "install", "danger", "mode", "dialog", "params",
                  "description", "default"):
        if field not in n:
            problems.append(f"{where}: action JSON lacks {field!r}")
    if n.get("mode") not in SPEC_MODES:
        problems.append(f"{where}: mode {n.get('mode')!r}")
    if n.get("dialog") not in SPEC_DIALOGS:
        problems.append(f"{where}: dialog {n.get('dialog')!r} not in SPEC §5.6")
    if n.get("mode") == "dialog" and not n.get("dialog"):
        problems.append(f"{where}: dialog action without a dialog name")
    if not n.get("icon") and not n.get("text"):
        problems.append(f"{where}: neither icon nor text")
    _check_text(n.get("label"), f"{where}.label", problems)
    _check_text(n.get("text"), f"{where}.text", problems)
    _check_text(n.get("description"), f"{where}.description", problems)
    _check_text(n.get("hint"), f"{where}.hint", problems)
    for key, what in ((act.label, "label"), (act.description, "description"), (act.text, "text")):
        if key and not _key_translated(key):
            problems.append(f"{where}: {what} key {key!r} missing in it or en")
    if not n.get("available") and not (n.get("install") and n.get("hint")):
        problems.append(f"{where}: unavailable without install hint")
    if n_files >= 2 and act.multi == "single":
        problems.append(f"{where}: multi='single' offered for {n_files} files")
    names = set()
    dynamic = {decl.name for decl in act.all_params() if callable(decl.options)}
    for p in n.get("params") or []:
        _check_param(p, f"{where}.param[{p.get('name')}]", problems, p.get("name") in dynamic)
        if p.get("name") in names:
            problems.append(f"{where}: duplicate param {p.get('name')!r}")
        names.add(p.get("name"))
    for decl in act.all_params():
        for key, what in ((decl.label, "label"), (decl.help, "help"), (decl.unit, "unit")):
            if key and not _key_translated(key):
                problems.append(f"{where}.param[{decl.name}]: {what} key {key!r} missing in it or en")
    needed = DIALOG_PARAM.get(n.get("dialog"))
    if needed:
        types = {p.get("type") for p in n.get("params") or []}
        if not types & needed:
            problems.append(f"{where}: dialog {n.get('dialog')!r} but no {sorted(needed)} param")
    if n.get("dialog") == "trim":
        pnames = {p.get("name") for p in n.get("params") or []}
        if not {"start", "end"} <= pnames:
            problems.append(f"{where}: trim dialog without start/end params")


def _walk(nodes: list[dict], where: str, n_files: int, problems: list[str], group_ids: list[str],
          action_ids: list[str]) -> None:
    if len(nodes) > MAX_RING:
        problems.append(f"{where}: ring has {len(nodes)} items (> {MAX_RING})")
    ring_ids = [n.get("id") for n in nodes]
    dup = sorted({i for i in ring_ids if ring_ids.count(i) > 1})
    if dup:
        problems.append(f"{where}: duplicate ids in one ring {dup}")
    for n in nodes:
        ident = n.get("id")
        here = f"{where}>{ident}"
        if not ident:
            problems.append(f"{where}: node without id")
        if n.get("type") == "group":
            group_ids.append(ident)
            items = n.get("items") or []
            _check_text(n.get("label"), f"{here}.label", problems)
            if not n.get("label"):
                problems.append(f"{here}: group without label")
            spec = registry.get_group(ident)
            if spec is not None and spec.label and not _key_translated(spec.label):
                problems.append(f"{here}: group label key {spec.label!r} missing in it or en")
            if not items:
                problems.append(f"{here}: empty group")
            elif len(items) == 1 and not str(ident).endswith(".convert"):
                problems.append(f"{here}: single-item group not flattened")
            default = n.get("default")
            if default is not None and default not in _leaf_ids(items):
                problems.append(f"{here}: default {default!r} is not a leaf of the group")
            _walk(items, here, n_files, problems, group_ids, action_ids)
        elif n.get("type") == "action":
            action_ids.append(ident)
            _check_action(n, here, n_files, problems)
        else:
            problems.append(f"{here}: unknown node type {n.get('type')!r}")


def _menu_problems(menu: dict, n_files: int) -> list[str]:
    problems: list[str] = []
    pages = menu.get("pages") or []
    if [p.get("id") for p in pages] != ["main", "file"]:
        problems.append(f"pages are {[p.get('id') for p in pages]}")
    for page in pages:
        _check_text(page.get("label"), f"page {page.get('id')}.label", problems)
        # The shell navigates sub-rings by node object and looks ids up only inside one ring, so an
        # action may be reachable from two rings (e.g. Convert ▸ PDF and a top-level PDF shortcut,
        # or Checksum on both the main ring and the File page).  Within one page a group id is unique.
        group_ids: list[str] = []
        action_ids: list[str] = []
        _walk(page.get("items") or [], page.get("id", "?"), n_files, problems, group_ids, action_ids)
        dup_groups = sorted({g for g in group_ids if group_ids.count(g) > 1})
        if dup_groups:
            problems.append(f"page {page.get('id')}: group ids used twice: {dup_groups}")
    if menu.get("count") != n_files or len(menu.get("files") or []) != n_files:
        problems.append("count/files do not match the selection")
    if not menu.get("icon"):
        problems.append("menu without icon")
    if not menu.get("title"):
        problems.append("menu without title")
    try:
        json.dumps(menu, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        problems.append(f"menu is not JSON-serialisable: {exc}")
    return problems


# ----------------------------------------------------------------------------------- tests

def test_all_action_modules_load(loaded):
    assert loaded.LOAD_ERRORS == {}, loaded.LOAD_ERRORS
    ids = [a.id for a in registry.all_actions()]
    assert len(ids) == len(set(ids))
    assert len(ids) > 300
    modules = {a.module.rsplit(".", 1)[-1] for a in registry.all_actions()}
    for name in ("common", "image", "pdf", "docs", "media", "archive", "fonts", "folder", "web", "system"):
        assert name in modules, f"no action registered by actions.{name}"


def test_install_hints_name_real_packages(loaded):
    """A module path such as PIL._avif must not turn into a package name like python-pil.-avif."""
    from dropwheel import tools

    bad = []
    for act in registry.all_actions():
        for item in act.requires or []:
            for name in item if isinstance(item, (tuple, list)) else [item]:
                bad += [f"{act.id}: {name} -> {pkg}" for pkg in tools._tool_for(name).packages if "." in pkg]
    assert not bad, "\n".join(bad)


def test_registered_actions_follow_the_spec(loaded):
    """Static contract of every registered action (independent of a selection)."""
    problems: list[str] = []
    for act in registry.all_actions():
        if act.mode not in SPEC_MODES:
            problems.append(f"{act.id}: mode {act.mode!r}")
        if act.dialog not in SPEC_DIALOGS:
            problems.append(f"{act.id}: dialog {act.dialog!r}")
        if act.multi not in ("single", "each", "combine", "any"):
            problems.append(f"{act.id}: multi {act.multi!r}")
        for key in (act.label, act.description, act.text):
            if key and not _key_translated(key):
                problems.append(f"{act.id}: key {key!r} missing in it or en")
        for p in act.all_params():
            if p.type not in SPEC_PARAM_TYPES:
                problems.append(f"{act.id}.{p.name}: type {p.type!r}")
            for key in (p.label, p.help, p.unit):
                if key and not _key_translated(key):
                    problems.append(f"{act.id}.{p.name}: key {key!r} missing in it or en")
            if not callable(p.options):
                for _value, label in p.option_pairs([]):
                    if not _key_translated(label):
                        problems.append(f"{act.id}.{p.name}: option key {label!r} missing in it or en")
    for spec in registry.all_groups():
        for key in (spec.label, spec.text):
            if key and not _key_translated(key):
                problems.append(f"group {spec.id}: key {key!r} missing in it or en")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("lang", ["it", "en"])
@pytest.mark.parametrize("sample", SAMPLES)
def test_menu_contract(loaded, samples, sample, lang):
    if sample not in samples:
        pytest.skip("no TrueType font available")
    files = samples[sample]
    menu = registry.build_menu(files, cfgmod.load(), lang, "drag")
    assert menu["category"] == EXPECTED_CATEGORY[sample], (sample, menu["category"])
    if sample == "gif":
        assert menu["files"][0]["animated"] is True
    main = menu["pages"][0]["items"]
    assert main, f"{sample}: empty main ring"
    problems = _menu_problems(menu, len(files))
    assert not problems, f"{sample} [{lang}]:\n" + "\n".join(problems)


@pytest.mark.parametrize("sample", ["png", "pdf", "2png", "png+pdf", "mp4"])
def test_menu_contract_click_mode_and_hidden_unavailable(loaded, samples, sample):
    """Click context and ``show_unavailable = false`` keep the same guarantees."""
    cfg = cfgmod.load()
    cfg["wheel"]["show_unavailable"] = False
    files = samples[sample]
    menu = registry.build_menu(files, cfg, "it", "click")
    assert menu["context"] == "click"
    problems = _menu_problems(menu, len(files))
    for page in menu["pages"]:
        for leaf_id in _leaf_ids(page["items"]):
            node = registry.find_node(menu, leaf_id)
            if node and node.get("available") is False:
                problems.append(f"{leaf_id}: unavailable action shown with show_unavailable=false")
    assert not problems, "\n".join(problems)


def test_italian_and_english_menus_differ(loaded, samples):
    """The Italian menu is really translated (not the English fallback)."""
    it_menu = registry.build_menu(samples["png"], cfgmod.load(), "it")
    en_menu = registry.build_menu(samples["png"], cfgmod.load(), "en")
    assert it_menu["pages"][0]["label"] != en_menu["pages"][0]["label"]
    it_labels = [n["label"] for n in it_menu["pages"][0]["items"]]
    en_labels = [n["label"] for n in en_menu["pages"][0]["items"]]
    assert it_labels != en_labels
    assert i18n.t("grp.convert", "it") in it_labels


def test_node_extra_hook(clean_registry):
    """``Action.node_extra`` adds per-selection fields but can never change type/id/default."""
    seen = []

    def extra(files, lang):
        seen.append((len(files), lang))
        return {"locked": True, "description": "protetto", "id": "hijack", "type": "group"}

    clean_registry.action(id="image.thing", label="Thing", icon="star", categories={"image"},
                          node_extra=extra)(lambda ctx: None)
    clean_registry.action(id="image.broken", label="Broken", icon="star", categories={"image"},
                          node_extra=lambda files, lang: 1 / 0)(lambda ctx: None)
    from dropwheel.detect import FileInfo

    f = FileInfo(path="/x/a.png", uri="file:///x/a.png", name="a.png", stem="a", ext="png", category="image",
                 exists=True)
    menu = registry.build_menu([f], cfgmod.defaults(), "it")
    node = registry.find_node(menu, "image.thing")
    assert node["type"] == "action" and node["id"] == "image.thing"
    assert node["locked"] is True and node["description"] == "protetto"
    assert seen == [(1, "it")]
    assert registry.find_node(menu, "image.broken") is not None  # a failing hook never breaks the menu


def test_locked_pdf_marks_actions(loaded, tmp_path):
    """A password-protected PDF: content actions are ``locked`` with an explanatory description, and
    their password param is required (the shell then opens the form instead of running)."""
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "segreto.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "riservato")
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="5678")
    doc.close()
    menu = registry.build_menu([str(path)], cfgmod.load(), "it")
    locked = [n for p in menu["pages"] for n in _iter_nodes(p["items"]) if n.get("locked")]
    assert locked, "no action marked as locked"
    for node in locked:
        assert "segreto.pdf" in node["description"]
        pw = [p for p in node["params"] if p["name"] == "password"]
        assert pw and pw[0]["required"] is True
    plain = tmp_path / "libero.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(plain)
    doc.close()
    menu = registry.build_menu([str(plain)], cfgmod.load(), "it")
    assert not [n for p in menu["pages"] for n in _iter_nodes(p["items"]) if n.get("locked")]


def _iter_nodes(nodes):
    for n in nodes:
        if n.get("type") == "group":
            yield from _iter_nodes(n.get("items") or [])
        else:
            yield n


ROOT = Path(__file__).resolve().parent.parent


def _toml_blocks(path: Path) -> list[str]:
    return re.findall(r"```toml\n(.*?)```", path.read_text(encoding="utf-8"), flags=re.S)


@pytest.mark.parametrize("doc", ["README.md", "README.it.md", "docs/CONFIG.md", "docs/CONFIG.it.md"])
def test_documented_toml_examples_load_cleanly(tmp_path, doc):
    """Every TOML example of the user documentation is a valid configuration (no warnings)."""
    blocks = _toml_blocks(ROOT / doc)
    assert blocks, f"no TOML example in {doc}"
    for i, block in enumerate(blocks):
        path = tmp_path / f"example-{i}.toml"
        path.write_text(block, encoding="utf-8")
        cfg = cfgmod.load(path)
        assert cfg["_warnings"] == [], f"{doc} example #{i + 1}: {cfg['_warnings']}\n{block}"


@pytest.mark.parametrize("doc", ["README.md", "README.it.md", "docs/CONFIG.md", "docs/CONFIG.it.md"])
def test_documented_action_ids_exist(loaded, doc):
    """The ids the documentation tells users to write: a wrong one does nothing, silently."""
    known = {spec.id for spec in registry.all_actions()}
    groups = {spec.id for spec in registry.all_groups()}
    patterns: list[str] = []
    for block in _toml_blocks(ROOT / doc):
        patterns += tomllib.loads(block).get("hidden_actions", [])
    assert not [p for p in patterns if not fnmatch.filter(known, p)], f"{doc}: hidden_actions match nothing"
    commands = re.findall(r"dropwheel run ([a-z][a-z0-9_.]*)", (ROOT / doc).read_text(encoding="utf-8"))
    assert not [c for c in commands if c not in known and c not in groups], f"{doc}: unknown ids in {commands}"


@pytest.mark.parametrize("doc", ["CONFIG.md", "CONFIG.it.md"])
def test_config_reference_lists_every_default(doc):
    """The "full defaults" block of the config reference and the commented default file match DEFAULTS."""
    import tomllib

    blocks = _toml_blocks(ROOT / "docs" / doc)
    full = tomllib.loads(blocks[-1])
    expected = {k: v for k, v in cfgmod.DEFAULTS.items() if k != "custom"}
    assert full == expected
    assert tomllib.loads(cfgmod.default_toml()) == expected
    for key, value in expected.items():
        if isinstance(value, dict):
            for sub in value:
                assert f"`{sub}`" in (ROOT / "docs" / doc).read_text(encoding="utf-8"), f"{key}.{sub}"


@pytest.mark.parametrize("doc, word", [("FUNCTIONS.md", "actions"), ("FUNCTIONS.it.md", "azioni")])
def test_function_reference_is_current(loaded, doc, word):
    """A stale count means tools/gen_functions_doc.py has to run again."""
    text = (ROOT / "docs" / doc).read_text(encoding="utf-8")
    assert f"**{len(registry.all_actions())} {word}**" in text


@pytest.mark.parametrize("doc", ["README.md", "README.it.md", "docs/CONFIG.md", "docs/CONFIG.it.md",
                                 "docs/ARCHITECTURE.md", "docs/FUNCTIONS.md", "docs/FUNCTIONS.it.md"])
def test_documentation_links_resolve(doc):
    """Local links point at real files, and anchors at real headings of the English and Italian docs."""
    path = ROOT / doc
    problems = []
    for target in re.findall(r"(?<!\\)\[[^\]]*\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://")):
            continue
        name, _, anchor = target.partition("#")
        linked = path if not name else (path.parent / name)
        if not linked.exists():
            problems.append(target)
        elif anchor and linked.suffix == ".md":
            headings = re.findall(r"^#+ (.+)$", linked.read_text(encoding="utf-8"), flags=re.M)
            slugs = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
            if anchor not in slugs:
                problems.append(target)
    assert not problems, f"{doc}: broken links {problems}"


def _doc_structure(path: Path) -> dict:
    """What a translation must share with its original, whatever the wording."""
    text = path.read_text(encoding="utf-8")
    prose = re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)
    links = [t.partition("#")[0] for t in re.findall(r"(?<!\\)\[[^\]]*\]\(([^)\s]+)\)", prose)]
    return {
        "heading levels": [len(h) for h in re.findall(r"^(#+) ", prose, flags=re.M)],
        "images": [src.replace(".it.svg", ".svg") for src in re.findall(r'<img[^>]*?src="([^"]+)"', prose)],
        "local links": sorted(t.replace(".it.md", ".md") for t in links if t and not t.startswith("http")),
        "collapsible sections": prose.count("<details>"),
        "table rows": len(re.findall(r"^\|", prose, flags=re.M)),
        "list items": len(re.findall(r"^\s*(?:-|\d+\.) ", prose, flags=re.M)),
        "code blocks": re.findall(r"^```(\w*)", text, flags=re.M),
        "keys": text.count("<kbd>"),
    }


@pytest.mark.parametrize("original, translation", [("README.md", "README.it.md"), ("docs/CONFIG.md", "docs/CONFIG.it.md"),
                                                   ("docs/FUNCTIONS.md", "docs/FUNCTIONS.it.md")])
def test_translations_stay_in_sync(original, translation):
    """Changing one language only is a bug: both versions have the same structure, images and links."""
    assert _doc_structure(ROOT / original) == _doc_structure(ROOT / translation)


def test_italian_readme_uses_the_italian_illustration():
    images = _doc_structure(ROOT / "README.it.md")["images"]
    text = (ROOT / "README.it.md").read_text(encoding="utf-8")
    assert "assets/wheel-preview.it.svg" in text and "assets/wheel-preview.svg" not in text
    assert all((ROOT / i).exists() for i in images if not i.startswith("http"))
    assert (ROOT / "assets" / "wheel-preview.it.svg").exists()


# ------------------------------------------------------------------------- protocol (SPEC §5)

def _shell_sources() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "shell").rglob("*.qml")))


def test_backend_methods_exist_in_the_daemon():
    """Every RPC the shell issues is served by the daemon (SPEC §5.1)."""
    from dropwheel.daemon import Daemon

    backend = (ROOT / "shell" / "services" / "Backend.qml").read_text(encoding="utf-8")
    called = set(re.findall(r'\bcall\(\s*"([a-z_]+)"', backend))
    called |= set(re.findall(r'method:\s*"([a-z_]+)"', backend))
    called |= set(re.findall(r'Backend\.call\(\s*"([a-z_]+)"', _shell_sources()))
    assert {"hello", "menu", "run", "cancel", "undo", "preview", "apps", "util", "clipboard_files", "share_stop",
            "config", "doctor"} <= called
    daemon = Daemon.__new__(Daemon)
    methods = {name[2:] for name in dir(daemon) if name.startswith("m_")}
    assert called <= methods, called - methods


def test_util_ops_and_preview_kinds_are_served():
    """``util`` ops and ``preview`` kinds used by the QML exist in daemon.py / preview.py."""
    src = (ROOT / "src" / "dropwheel" / "daemon.py").read_text(encoding="utf-8")
    ops = set(re.findall(r'op == "([a-z_]+)"', src))
    shell = _shell_sources()
    used_ops = set(re.findall(r'Backend\.util\(\s*"([a-z_]+)"', shell))
    used_ops |= set(re.findall(r'utilCall\(\s*"([a-z_]+)"', shell))
    assert used_ops and used_ops <= ops, used_ops - ops
    kinds = set(re.findall(r'kind == "([a-z_]+)"', (ROOT / "src" / "dropwheel" / "preview.py").read_text(encoding="utf-8")))
    used_kinds = set(re.findall(r'Backend\.preview\(\s*"([a-z_]+)"', shell))
    assert used_kinds and used_kinds <= kinds, used_kinds - kinds
    assert {"thumb", "image", "pdf_info", "pdf_page", "pdf_search", "video_info", "video_frames", "audio_waveform",
            "metadata", "text"} <= kinds


def test_events_handled_by_the_shell_are_emitted():
    """Event names: the daemon emits what Backend.qml handles (ready, job, config, config_changed)."""
    backend = (ROOT / "shell" / "services" / "Backend.qml").read_text(encoding="utf-8")
    handled = set(re.findall(r'(?<!typeof )msg\.event === "([a-z_]+)"', backend))
    emitted = set()
    for name in ("daemon.py", "jobs.py"):
        emitted |= set(re.findall(r'"event":\s*"([a-z_]+)"', (ROOT / "src" / "dropwheel" / name).read_text(
            encoding="utf-8")))
    assert {"ready", "job", "config", "config_changed"} <= handled
    assert handled <= emitted, handled - emitted


def test_job_event_fields_match_jobs_qml(loaded, make_runner, tmp_path):
    """A real job event carries every field Jobs.qml reads (SPEC §5.2)."""
    src = tmp_path / "nota.txt"
    src.write_text("ciao\n", encoding="utf-8")
    act = registry.get("text.copy") or next(a for a in registry.applicable_actions([str(src)])
                                             if not a.missing() and a.mode != "dialog")
    runner, events = make_runner()
    job = runner.run_sync(act.id, [str(src)], {})
    assert job.state in ("done", "failed"), job.state
    final = [e for e in events if e.get("event") == "job" and e.get("state") in ("done", "failed")][-1]
    jobs_qml = (ROOT / "shell" / "services" / "Jobs.qml").read_text(encoding="utf-8")
    for field in set(re.findall(r"\bev\.([a-z_]+)", jobs_qml)) - {"result", "error"}:
        assert field in final, f"job event lacks {field!r} read by Jobs.qml"
    if final["state"] == "done":
        for field in ("kind", "message", "outputs", "undoable"):
            assert field in final["result"]


def test_ipc_functions_used_by_the_cli_exist_in_shell_qml():
    """``qs ipc call dropwheel <fn>``: every function the CLI calls is declared by the IpcHandler (SPEC §5.4)."""
    shell = (ROOT / "shell" / "shell.qml").read_text(encoding="utf-8")
    handler = shell[shell.index("IpcHandler {"):]
    handler = handler[: handler.index("\n    }\n") + 6]
    declared = set(re.findall(r"function\s+([A-Za-z]+)\(", handler))
    assert {"open", "openAtCursor", "close", "setHotzone", "reload", "ping"} <= declared
    used = set()
    for name in ("cli.py", "shellctl.py"):
        used |= set(re.findall(r'ipc_call\(\s*"([A-Za-z]+)"', (ROOT / "src" / "dropwheel" / name).read_text(
            encoding="utf-8")))
    assert used and used <= declared, used - declared
    assert re.search(r'IpcHandler\s*\{\s*target:\s*"dropwheel"', shell)


def test_empty_selection_menu(loaded):
    menu = registry.build_menu([], cfgmod.load(), "it", "click")
    assert menu["category"] == "empty" and menu["count"] == 0
    assert all(not p["items"] for p in menu["pages"])
    assert menu["title"] and not KEY_RE.match(menu["title"])
