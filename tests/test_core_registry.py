"""Tests for dropwheel.registry (applicability, layouts, 8-cap/More, flattening, availability, mixed)."""

from __future__ import annotations

import pytest

from dropwheel import config as cfgmod
from dropwheel.detect import FileInfo
from dropwheel.errors import UserError

MISSING_TOOL = "dropwheel-test-missing-tool"


def fi(name: str, category: str, size: int = 100, **kw) -> FileInfo:
    stem, _, ext = name.rpartition(".")
    return FileInfo(path=f"/x/{name}", uri=f"file:///x/{name}", name=name, stem=stem or name, ext=ext.lower(),
                    category=category, size=size, exists=True, readable=True, writable_dir=True, **kw)


def noop(ctx):
    return None


def reg(registry, ident, label=None, categories=("image",), **kw):
    registry.action(id=ident, label=label or ident.rsplit(".", 1)[-1].upper(), categories=categories, **kw)(noop)


def page(menu, pid="main"):
    return next(p for p in menu["pages"] if p["id"] == pid)["items"]


def ids(nodes):
    return [n["id"] for n in nodes]


def cfg(**over):
    c = cfgmod.defaults()
    for k, v in over.items():
        if k == "hidden_actions":
            c["hidden_actions"] = v
        elif k == "show_unavailable":
            c["wheel"]["show_unavailable"] = v
    return c


# ------------------------------------------------------------------------------- validation

def test_action_validation(clean_registry):
    registry = clean_registry
    with pytest.raises(ValueError):
        reg(registry, "x.bad", multi="many")
    with pytest.raises(ValueError):
        reg(registry, "x.bad2", mode="slow")
    with pytest.raises(ValueError):
        reg(registry, "x.bad3", categories=("nope",))
    reg(registry, "x.dlg", mode="dialog")
    assert registry.get("x.dlg").dialog == "form"
    with pytest.raises(ValueError):
        registry.Param(name="p", type="weird")


def test_decorator_returns_function(clean_registry):
    registry = clean_registry

    @registry.action(id="image.thing", label="Thing", categories={"image"})
    def thing(ctx):
        return None

    assert callable(thing) and thing.action.id == "image.thing"
    assert registry.get("image.thing").module == __name__
    assert [a.id for a in registry.all_actions()] == ["image.thing"]


# ---------------------------------------------------------------------------- applicability

def test_multi_rules_and_when(clean_registry):
    registry = clean_registry
    reg(registry, "image.single", multi="single")
    reg(registry, "image.each", multi="each")
    reg(registry, "image.combine", multi="combine")
    reg(registry, "image.any", multi="any")
    reg(registry, "image.notpng", when=lambda files: all(f.ext != "png" for f in files))
    reg(registry, "image.boom", when=lambda files: 1 / 0)
    one = [fi("a.png", "image")]
    two = [fi("a.jpg", "image"), fi("b.jpg", "image")]
    a = {x.id for x in registry.applicable_actions(one)}
    assert a == {"image.single", "image.each", "image.any"}
    b = {x.id for x in registry.applicable_actions(two)}
    assert b == {"image.each", "image.combine", "image.any", "image.notpng"}


def test_wildcard_excludes_url(clean_registry):
    registry = clean_registry
    registry.action(id="file.open", label="Open", categories={"*"})(noop)
    registry.action(id="file.empty", label="Any", categories=())(noop)
    assert {a.id for a in registry.applicable_actions([fi("a.pdf", "pdf")])} == {"file.open", "file.empty"}
    url = FileInfo(path="", uri="https://e.com", name="e.com", category="url")
    assert registry.applicable_actions([url]) == []


# ---------------------------------------------------------------------------------- layouts

def test_cap_and_more(clean_registry):
    registry = clean_registry
    names = [f"image.t.a{i:02d}" for i in range(10)]
    for n in names:
        reg(registry, n)
    registry.layout("image", names)
    menu = registry.build_menu([fi("p.png", "image")], cfg(), "en", "drag")
    items = page(menu)
    assert len(items) == 8
    assert ids(items[:7]) == names[:7]
    more = items[7]
    assert more["type"] == "group" and more["id"] == "main.more" and more["label"] == "More"
    assert ids(more["items"]) == names[7:]


def test_nested_more(clean_registry):
    registry = clean_registry
    names = [f"image.t.b{i:02d}" for i in range(20)]
    for n in names:
        reg(registry, n)
    registry.layout("image", names)
    items = page(registry.build_menu([fi("p.png", "image")], cfg(), "en", "drag"))
    assert len(items) == 8
    lvl2 = items[7]["items"]
    assert len(lvl2) == 8 and lvl2[7]["id"] == "main.more.more"
    assert ids(lvl2[7]["items"]) == names[14:]


def test_group_flatten_convert_and_default(clean_registry):
    registry = clean_registry
    reg(registry, "image.convert.png", label="PNG", text="PNG")
    reg(registry, "image.edit.rotate", label="Rotate")
    reg(registry, "image.compress.a", label="A", requires=[MISSING_TOOL])
    reg(registry, "image.compress.b", label="B")
    conv = registry.group("image.convert", "grp.convert", "swap_horiz", ["image.convert.png", "image.convert.jpg"])
    edit = registry.group("image.edit", "grp.edit", "edit", ["image.edit.rotate"])
    comp = registry.group("image.compress", "grp.compress", "compress", ["image.compress.a", "image.compress.b"],
                          default="image.compress.a")
    registry.layout("image", [conv, edit, comp])
    items = page(registry.build_menu([fi("p.png", "image")], cfg(), "it", "drag"))
    assert items[0]["type"] == "group" and items[0]["id"] == "image.convert" and items[0]["label"] == "Converti"
    assert items[0]["default"] == "image.convert.png" and items[0]["items"][0]["default"] is True
    assert items[0]["items"][0]["text"] == "PNG"
    assert items[1]["type"] == "action" and items[1]["id"] == "image.edit.rotate"
    # default "a" is unavailable -> first available child
    assert items[2]["default"] == "image.compress.b"
    b = next(n for n in items[2]["items"] if n["id"] == "image.compress.b")
    assert b["default"] is True


def test_group_referenced_by_id_and_callable_default(clean_registry):
    registry = clean_registry
    reg(registry, "image.convert.png")
    reg(registry, "image.convert.jpg", when=lambda fs: all(f.ext != "jpg" for f in fs))
    registry.group("image.convert", "grp.convert", "swap_horiz", ["image.convert.png", "image.convert.jpg"],
                   default=lambda files: "image.convert.jpg")
    registry.layout("image", ["image.convert"])
    png = page(registry.build_menu([fi("p.png", "image")], cfg(), "en", "drag"))
    assert png[0]["default"] == "image.convert.jpg"
    jpg = page(registry.build_menu([fi("p.jpg", "image")], cfg(), "en", "drag"))
    # jpg target not applicable: convert group keeps a single item but is not flattened
    assert jpg[0]["type"] == "group" and ids(jpg[0]["items"]) == ["image.convert.png"]
    assert jpg[0]["default"] == "image.convert.png"


def test_availability_hidden_and_show_unavailable(clean_registry):
    registry = clean_registry
    reg(registry, "image.ok")
    reg(registry, "image.needs", requires=[MISSING_TOOL])
    reg(registry, "image.alt", requires=[(MISSING_TOOL, "another-missing-xyz")])
    reg(registry, "image.secret")
    registry.layout("image", ["image.ok", "image.needs", "image.alt", "image.secret"])
    menu = registry.build_menu([fi("p.png", "image")], cfg(hidden_actions=["image.sec*"]), "en", "drag")
    items = page(menu)
    assert ids(items) == ["image.ok", "image.needs", "image.alt"]
    needs = items[1]
    assert needs["available"] is False
    assert needs["hint"] == f"Requires {MISSING_TOOL}"
    assert needs["install"] == f"sudo pacman -S --needed {MISSING_TOOL}"
    assert items[2]["available"] is False and items[2]["hint"] == f"Requires {MISSING_TOOL}"
    hidden = page(registry.build_menu([fi("p.png", "image")], cfg(show_unavailable=False), "en", "drag"))
    assert ids(hidden) == ["image.ok", "image.secret"]


def test_hidden_group(clean_registry):
    registry = clean_registry
    reg(registry, "image.convert.png")
    reg(registry, "image.convert.webp")
    reg(registry, "image.crop")
    registry.layout("image", [registry.group("image.convert", "grp.convert", "", ["image.convert.png",
                                                                                   "image.convert.webp"]),
                              "image.crop"])
    items = page(registry.build_menu([fi("p.png", "image")], cfg(hidden_actions=["image.convert"]), "en", "drag"))
    assert ids(items) == ["image.crop"]


def test_auto_grouping_without_layout(clean_registry):
    registry = clean_registry
    reg(registry, "image.edit.rotate", order=10)
    reg(registry, "image.compress.light")
    reg(registry, "image.convert.png")
    reg(registry, "image.convert.jpg")
    reg(registry, "image.crop", order=20)
    items = page(registry.build_menu([fi("p.png", "image")], cfg(), "it", "drag"))
    assert items[0]["id"] == "image.convert" and items[0]["label"] == "Converti"
    assert ids(items[0]["items"]) == ["image.convert.png", "image.convert.jpg"]
    # single-member auto groups are flattened
    assert ids(items[1:]) == ["image.compress.light", "image.edit.rotate", "image.crop"]


def test_leftovers_appended_and_file_page(clean_registry):
    registry = clean_registry
    reg(registry, "image.a")
    reg(registry, "image.forgotten")
    registry.action(id="common.open", label="Open", categories={"*"}, order=1)(noop)
    registry.action(id="common.copy_path", label="Copy path", categories={"*"}, order=2)(noop)
    registry.action(id="common.extra", label="Extra", categories={"*"}, order=3)(noop)
    registry.layout("image", ["image.a"])
    registry.layout("file", ["common.open", "common.copy_path"])
    menu = registry.build_menu([fi("p.png", "image")], cfg(), "en", "drag")
    assert ids(page(menu)) == ["image.a", "image.forgotten"]
    assert ids(page(menu, "file")) == ["common.open", "common.copy_path", "common.extra"]
    assert [p["label"] for p in menu["pages"]] == ["Tools", "File"]


def test_category_specific_file_layout(clean_registry):
    registry = clean_registry
    registry.action(id="common.open", label="Open", categories={"*"})(noop)
    registry.action(id="folder.terminal", label="Terminal", categories={"folder"})(noop)
    registry.layout("file", ["common.open"])
    registry.layout("folder", ["folder.terminal", "common.open"], page="file")
    folder = FileInfo(path="/x/dir", name="dir", stem="dir", category="folder", is_dir=True, exists=True)
    menu = registry.build_menu([folder], cfg(), "en", "drag")
    assert ids(page(menu, "file")) == ["folder.terminal", "common.open"]
    assert ids(page(menu)) == []


def test_multi_layout_and_fallback(clean_registry):
    registry = clean_registry
    reg(registry, "image.convert.png")
    reg(registry, "image.single_only", multi="single")
    reg(registry, "image.combine.pdf", multi="combine")
    registry.layout("image", ["image.convert.png", "image.single_only"])
    two = [fi("a.png", "image"), fi("b.png", "image")]
    items = page(registry.build_menu(two, cfg(), "en", "drag"))
    # fallback to the category layout; combine actions first
    assert ids(items) == ["image.combine.pdf", "image.convert.png"]
    registry.layout("multi:image", ["image.convert.png", "image.combine.pdf"])
    items = page(registry.build_menu(two, cfg(), "en", "drag"))
    assert ids(items) == ["image.convert.png", "image.combine.pdf"]


def test_mixed_menu(clean_registry):
    registry = clean_registry
    registry.action(id="common.zip", label="ZIP", categories={"*"}, multi="any", mixed_ok=True, order=1)(noop)
    registry.action(id="pdf.combine.merge", label="Merge", categories={"pdf", "image"}, multi="combine",
                    mixed_ok=True)(noop)
    registry.action(id="image.only", label="Only", categories={"image"}, mixed_ok=True)(noop)
    registry.action(id="image.not_mixed", label="No", categories={"image", "pdf"})(noop)
    files = [fi("a.png", "image"), fi("b.pdf", "pdf")]
    menu = registry.build_menu(files, cfg(), "it", "drag")
    assert menu["category"] == "mixed" and menu["icon"] == "stacks" and menu["count"] == 2
    assert ids(page(menu)) == ["common.zip", "pdf.combine.merge"]
    assert menu["title"] == "2 file"
    registry.layout("mixed", ["pdf.combine.merge"])
    menu = registry.build_menu(files, cfg(), "en", "drag")
    assert ids(page(menu)) == ["pdf.combine.merge", "common.zip"]
    assert menu["title"] == "2 files"


# ------------------------------------------------------------------------------ menu header

def test_title_subtitle_thumb(clean_registry, dropwheel_env):
    registry = clean_registry
    p = dropwheel_env.work / "foto.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 1231)
    menu = registry.build_menu([str(p)], cfg(), "it", "click", thumb_fn=lambda f: "/tmp/thumb.png")
    assert menu["title"] == "foto.jpg" and menu["subtitle"] == "1,2 kB" and menu["thumb"] == "/tmp/thumb.png"
    assert menu["context"] == "click" and menu["category"] == "image" and menu["icon"] == "image"
    assert menu["files"][0]["name"] == "foto.jpg"

    def boom(_f):
        raise RuntimeError("no thumbs")

    assert registry.build_menu([str(p)], cfg(), "en", "drag", thumb_fn=boom)["thumb"] is None
    d1 = FileInfo(path="/x/a", name="a", category="folder", is_dir=True, exists=True)
    d2 = FileInfo(path="/x/b", name="b", category="folder", is_dir=True, exists=True)
    assert registry.build_menu([d1, d2], cfg(), "it", "drag")["title"] == "2 cartelle"
    assert registry.build_menu([d1], cfg(), "en", "drag")["subtitle"] == "Folder"
    mixed = registry.build_menu([d1, fi("x.png", "image")], cfg(), "en", "drag")
    assert mixed["title"] == "2 items"
    empty = registry.build_menu([], cfg(), "it", "click")
    assert empty["count"] == 0 and empty["title"] == "Trascina qui dei file" and empty["category"] == "empty"
    url = registry.build_menu(["https://example.org/a.png"], cfg(), "en", "drag")
    assert url["category"] == "url" and url["subtitle"] == "example.org"


@pytest.mark.parametrize("url,title,subtitle", [
    ("https://example.com/", "example.com", ""),
    ("https://www.youtube.com/watch?v=abc", "youtube.com/watch?v=abc", ""),
    ("https://example.com?q=1", "example.com?q=1", ""),
    ("https://www.example.org/wiki/Pagina%20uno", "example.org/wiki/Pagina uno", ""),
    ("https://example.org/index.php", "example.org/index.php", ""),
    ("https://www.example.org/a.png?w=1", "a.png", "example.org"),
])
def test_url_title_says_something_and_is_not_repeated(clean_registry, url, title, subtitle):
    menu = clean_registry.build_menu([url], cfg(), "it", "drag")
    assert (menu["title"], menu["subtitle"]) == (title, subtitle)


# -------------------------------------------------------------------------------------- params

def test_params_json_and_resolution(clean_registry):
    registry = clean_registry
    P = registry.P
    registry.action(
        id="image.compress.q", label="Q", categories={"image"}, mode="quick", replace_ok=True,
        params=[P.int("quality", "grp.compress", "cfg:image.jpeg_quality", min=1, max=100),
                P.select("fmt", "Format", [("jpg", "JPG"), ("png", "PNG")], default="jpg"),
                P.bool("strip", "Strip", True),
                P.float("scale", "Scale", lambda files: 0.5 * len(files)),
                P.text("name", "Name", "", required=True)],
    )(noop)
    files = [fi("p.png", "image")]
    menu = registry.build_menu(files, cfg(), "it", "drag")
    node = page(menu)[0]
    params = {p["name"]: p for p in node["params"]}
    assert params["quality"]["default"] == 85 and params["quality"]["label"] == "Comprimi"
    assert params["fmt"]["options"] == [{"value": "jpg", "label": "JPG"}, {"value": "png", "label": "PNG"}]
    assert params["scale"]["default"] == 0.5
    assert params["replace"]["type"] == "bool" and params["replace"]["default"] is False
    assert params["replace"]["label"] == "Sostituisci l'originale"
    act = registry.get("image.compress.q")
    resolved = act.resolve_params(files, {"quality": "70", "strip": "no", "name": "x", "extra": 1}, cfg())
    assert resolved["quality"] == 70 and resolved["strip"] is False and resolved["fmt"] == "jpg"
    assert resolved["replace"] is False and resolved["extra"] == 1
    assert act.resolve_params(files, {"quality": 500, "name": "x"}, cfg())["quality"] == 100
    with pytest.raises(UserError):
        act.resolve_params(files, {"fmt": "gif", "name": "x"}, cfg())
    with pytest.raises(UserError):
        act.resolve_params(files, {"quality": "abc", "name": "x"}, cfg())
    with pytest.raises(UserError):
        act.resolve_params(files, {}, cfg())


# ------------------------------------------------------------------------------- labels/target

def test_display_label_and_resolve_target(clean_registry):
    registry = clean_registry
    reg(registry, "image.convert.png", label="PNG")
    reg(registry, "image.convert.webp", label="WebP")
    reg(registry, "image.compress.balanced", label="Balanced")
    registry.layout("image", [registry.group("image.convert", "grp.convert", "", ["image.convert.png",
                                                                                   "image.convert.webp"],
                                             default="image.convert.webp")])
    assert registry.display_label("image.convert.png", "it") == "Converti · PNG"
    assert registry.display_label("image.compress.balanced", "it") == "Comprimi · Balanced"
    assert registry.display_label("nope", "en") == "nope"
    files = [fi("p.png", "image")]
    assert registry.resolve_target("image.convert", files, cfg()) == "image.convert.webp"
    assert registry.resolve_target("image.convert.png", files, cfg()) == "image.convert.png"
    assert registry.resolve_target("image.unknown", files, cfg()) is None


def test_find_node_and_snapshot(clean_registry):
    registry = clean_registry
    reg(registry, "image.a")
    menu = registry.build_menu([fi("p.png", "image")], cfg(), "en", "drag")
    assert registry.find_node(menu, "image.a")["type"] == "action"
    state = registry.snapshot()
    registry.clear()
    assert registry.get("image.a") is None
    registry.restore(state)
    assert registry.get("image.a") is not None


def test_menu_is_json_serialisable(clean_registry):
    import json

    registry = clean_registry
    P = registry.P
    registry.action(id="image.x", label="X", categories={"image"}, params=[P.rect(), P.pages()])(noop)
    json.dumps(registry.build_menu([fi("p.png", "image")], cfg(), "en", "drag"))


def test_action_nodes_carry_their_title(clean_registry):
    registry = clean_registry
    reg(registry, "image.convert.jpg", label="JPG",
        node_extra=lambda files, lang: {"title": "spoofed", "hint": "from the hook"})
    reg(registry, "image.convert.png", label="PNG")
    reg(registry, "image.alone", label="grp.convert")
    conv = registry.group("image.convert", "grp.convert", "swap_horiz", ["image.convert.jpg", "image.convert.png"])
    registry.layout("image", [conv, "image.alone"])
    items = page(registry.build_menu([fi("p.png", "image")], cfg(), "it", "drag"))
    jpg = next(n for n in items[0]["items"] if n["id"] == "image.convert.jpg")
    assert jpg["title"] == "Converti · JPG" and jpg["label"] == "JPG"
    assert jpg["hint"] == "from the hook"       # the hook may add keys, but not this one
    alone = next(n for n in items if n["id"] == "image.alone")
    assert alone["title"] == alone["label"] == "Converti"
