"""Tests for the act-misc modules: actions/{common,archive,fonts,folder,web,system,custom}.py.

Pure helpers are tested directly; actions run through a real :class:`dropwheel.jobs.JobRunner`
on sample inputs created in ``tmp_path``.  Anything that needs Linux-only tools (fc-cache,
udisksctl, unsquashfs, 7z, gpg…) is skipped when the tool is missing.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import os
import shutil
import stat
import struct
import subprocess
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

from dropwheel import config as cfgmod
from dropwheel import i18n, registry, tools
from dropwheel.errors import UserError

POSIX = os.name == "posix"
MY_MODULES = ("common", "archive", "fonts", "folder", "web", "system", "custom")


@pytest.fixture(scope="module", autouse=True)
def _actions_loaded():
    from dropwheel.actions import load_all

    load_all({})
    probes = ("file.zip", "archive.extract_here", "font.info", "folder.size", "url.download", "unknown.hex")
    if any(registry.get(p) is None for p in probes) or not registry.get_group("folder.compress"):
        # another test module emptied the registry after our modules were imported: register again
        load_all({}, reload=True)
    yield


@pytest.fixture
def runner(make_runner):
    r, _events = make_runner(lang="en")
    return r


def run(r, action_id, files, params=None):
    job = r.run_sync(action_id, [str(f) for f in files], params or {})
    return job


def ok(job):
    assert job.state == "done", (job.action.id, job.state, job.error)
    return job.result


def outputs(job):
    return [Path(o["path"]) for o in (job.result or {}).get("outputs", [])]


# ======================================================================= registry / locales

def _my_actions():
    return [a for a in registry.all_actions() if a.module.rsplit(".", 1)[-1] in MY_MODULES]


def test_modules_load_without_errors():
    from dropwheel.actions import LOAD_ERRORS

    for name in MY_MODULES:
        assert name not in LOAD_ERRORS, LOAD_ERRORS.get(name)


def test_every_action_has_translations_and_valid_requires():
    for lang in ("en", "it"):
        table = i18n.strings(lang)
        for act in _my_actions():
            if not act.id.startswith("custom.user."):
                if act.label.startswith("act."):
                    assert act.label in table, (lang, act.id, act.label)
                if act.description:
                    assert act.description in table, (lang, act.id, act.description)
            for p in act.all_params():
                if p.label:
                    assert p.label in table, (lang, act.id, p.name, p.label)
                if p.help:
                    assert p.help in table, (lang, act.id, p.help)
                for _value, opt_label in p.option_pairs([]):
                    if "." in opt_label and opt_label.split(".", 1)[0] in ("opt", "prm"):
                        assert opt_label in table, (lang, act.id, opt_label)
            for req in act.requires:
                names = req if isinstance(req, (list, tuple)) else [req]
                for name in names:
                    assert tools.display_name(name)


def test_locale_fragments_parity():
    base = Path(__file__).resolve().parent.parent / "src" / "dropwheel" / "locales"
    for mod in MY_MODULES:
        en = json.loads((base / "en" / f"{mod}.json").read_text(encoding="utf-8"))
        it = json.loads((base / "it" / f"{mod}.json").read_text(encoding="utf-8"))
        assert set(en) == set(it), mod


MY_LAYOUT_KEYS = {"file", "mixed", "archive", "multi:archive", "font", "multi:font", "folder", "multi:folder",
                  "url", "multi:url", "appimage", "disk", "package", "unknown"}


def test_layout_references_exist():
    def check(items, where):
        for it in items:
            if isinstance(it, registry.GroupSpec):
                check(it.items, where)
            elif registry.get(it) is None:
                spec = registry.get_group(it)
                assert spec is not None, (where, it)
                check(spec.items, where)

    mine = {k: v for k, v in registry.layouts().items() if k[1] in MY_LAYOUT_KEYS}
    assert len(mine) >= 12
    for key, items in mine.items():
        check(items, key)


def _ring_ids(menu, page=0):
    return [n["id"] for n in menu["pages"][page]["items"]]


def test_menus_for_my_categories(tmp_path):
    cfg = cfgmod.load()
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("x.txt", "x")
    d = tmp_path / "dir"
    d.mkdir()
    menu = registry.build_menu([str(z)], cfg, "it")
    assert _ring_ids(menu)[0] == "archive.extract_here"
    assert all(len(p["items"]) <= 8 for p in menu["pages"])
    file_ids = _ring_ids(menu, 1)
    assert "file.copy" in file_ids and "file.zip" in file_ids
    menu = registry.build_menu([str(d)], cfg, "it")
    assert _ring_ids(menu)[0] == "folder.compress"
    menu = registry.build_menu(["https://example.com/a/file.pdf"], cfg, "en")
    assert menu["category"] == "url"
    assert _ring_ids(menu)[0] == "url.download"
    for f in (z, d):
        m = registry.build_menu([str(f), str(f)], cfg, "en")
        assert all(len(p["items"]) <= 8 for p in m["pages"])


# ======================================================================= common: pure helpers

def test_parse_expected_checksum_formats():
    from dropwheel.actions.common import parse_expected_checksum

    h = "a" * 64
    assert parse_expected_checksum(h) == (["sha256"], h)
    assert parse_expected_checksum(f"sha256:{h.upper()}") == (["sha256"], h)
    assert parse_expected_checksum(f"{h}  file.iso\n{'b' * 64}  other.iso", "other.iso") == (["sha256"], "b" * 64)
    assert parse_expected_checksum(f"SHA256 (file.iso) = {h}", "file.iso") == (["sha256"], h)
    assert parse_expected_checksum("c" * 32)[0] == ["md5", "xxh128"]
    assert parse_expected_checksum("not a hash") is None


def test_find_checksum_for_sidecar_and_sums(tmp_path):
    from dropwheel.actions.common import find_checksum_for

    f = tmp_path / "image.iso"
    f.write_bytes(b"data")
    digest = hashlib.sha256(b"data").hexdigest()
    (tmp_path / "SHA256SUMS").write_text(f"{'0' * 64}  other.iso\n{digest} *image.iso\n", encoding="utf-8")
    algos, hexd, src = find_checksum_for(f)
    assert algos == ["sha256"] and hexd == digest and src.name == "SHA256SUMS"
    (tmp_path / "image.iso.md5").write_text(hashlib.md5(b"data").hexdigest() + "  image.iso\n")
    algos, hexd, src = find_checksum_for(f)
    assert src.name == "image.iso.md5" and algos == ["md5"]


def test_render_pattern_and_plan_renames(tmp_path):
    from dropwheel.actions.common import apply_renames, plan_renames, render_pattern

    assert render_pattern("{name} {n:03}", name="foto", ext="jpg", n=7) == "foto 007"
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_text("A")
    b.write_text("B")
    plan = plan_renames([a, b], "holiday {n:02}")
    assert [d.name for _s, d in plan.pairs] == ["holiday 01.jpg", "holiday 02.jpg"]
    # typing a known extension keeps it, unknown "extensions" do not count
    plan = plan_renames([a], "v1.2 final")
    assert plan.pairs[0][1].name == "v1.2 final.jpg"
    plan = plan_renames([a], "new.png")
    assert plan.pairs[0][1].name == "new.png"
    # swapping names works atomically
    plan = plan_renames([a, b], "{name}", find="a", replace="b")
    assert plan.conflicts  # b.jpg would be produced twice
    swap = [(a, tmp_path / "tmpname.jpg")]
    apply_renames(swap)
    assert (tmp_path / "tmpname.jpg").read_text() == "A"


def test_tree_render():
    from dropwheel.actions.common import TreeNode, render_tree

    root = TreeNode("")
    root.add_path(["a", "b.txt"], 10, False)
    root.add_path(["a", "c"], None, True)
    root.add_path(["z.bin"], 5, False)
    assert root.count() == (2, 2)
    assert root.total() == 15
    lines = render_tree(root, "en")
    assert lines[0].startswith("├── a/")
    assert lines[-1].startswith("└── z.bin")


# ======================================================================= common: actions

def test_file_checksum_verify_and_properties(runner, tmp_path):
    f = tmp_path / "-dash name.txt"
    f.write_bytes(b"hello")
    res = ok(run(runner, "file.checksum.sha256", [f]))
    assert res["text"] == hashlib.sha256(b"hello").hexdigest()
    g = tmp_path / "second.txt"
    g.write_bytes(b"x")
    res = ok(run(runner, "file.checksum.md5", [f, g]))
    assert res["text"].splitlines()[1].endswith("  second.txt")
    res = ok(run(runner, "file.checksum.blake2", [f]))
    assert res["text"] == hashlib.blake2b(b"hello").hexdigest()
    ok(run(runner, "file.verify", [f], {"expected": hashlib.sha256(b"hello").hexdigest()}))
    job = run(runner, "file.verify", [f], {"expected": "0" * 64})
    assert job.state == "failed" and "NOT match" in job.error["message"]
    res = ok(run(runner, "file.properties", [tmp_path]))
    keys = {row["key"] for row in res["info"]}
    assert {"Name", "Size", "Contents", "Modified"} <= keys


def test_duplicate_copy_to_move_to_rename(runner, tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text("content")
    dup = outputs(ok_job := run(runner, "file.organize.duplicate", [src]))
    assert ok_job.state == "done" and dup[0].name == "doc (Copy).txt"
    dest = tmp_path / "dest"
    res = ok(run(runner, "file.organize.copy_to", [src], {"dest": str(dest)}))
    assert (dest / "doc.txt").read_text() == "content" and src.exists()
    res = ok(run(runner, "file.organize.copy_to", [src], {"dest": str(dest)}))
    assert Path(res["outputs"][0]["path"]).name == "doc 2.txt"
    mv = tmp_path / "move-me.txt"
    mv.write_text("m")
    ok(run(runner, "file.organize.move_to", [mv], {"dest": str(dest)}))
    assert not mv.exists() and (dest / "move-me.txt").exists()
    res = ok(run(runner, "file.organize.rename", [dest / "move-me.txt"], {"name": "renamed"}))
    assert (dest / "renamed.txt").exists()
    one = tmp_path / "p1.jpg"
    two = tmp_path / "p2.jpg"
    one.write_text("1")
    two.write_text("2")
    res = ok(run(runner, "file.organize.rename", [one, two], {"name": "trip {n:02}", "dry_run": True}))
    assert res["kind"] == "text" and "trip 01.jpg" in res["text"] and one.exists()
    ok(run(runner, "file.organize.rename", [one, two], {"name": "trip {n:02}"}))
    assert (tmp_path / "trip 01.jpg").read_text() == "1" and (tmp_path / "trip 02.jpg").read_text() == "2"


def test_move_into_itself_is_refused(runner, tmp_path):
    d = tmp_path / "folder"
    (d / "sub").mkdir(parents=True)
    job = run(runner, "file.organize.move_to", [d], {"dest": str(d / "sub")})
    assert job.state == "failed"
    assert d.exists()


def test_data_uri_and_copy_path(runner, tmp_path):
    f = tmp_path / "pixel.png"
    from PIL import Image

    Image.new("RGB", (1, 1), (255, 0, 0)).save(f)
    res = ok(run(runner, "file.data_uri", [f]))
    assert res["text"].startswith("data:image/png;base64,")
    assert base64.b64decode(res["text"].split(",", 1)[1]) == f.read_bytes()
    res = ok(run(runner, "file.copy_path", [f]))
    assert res["text"] == str(f)


def test_zip_multi_name(runner, tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")
    res = ok(run(runner, "file.zip", [a, b]))
    out = Path(res["outputs"][0]["path"])
    assert out.name.startswith("Archive ") and out.suffix == ".zip"
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["a.txt", "b.txt"]


@pytest.mark.skipif(not POSIX or shutil.which("gpg") is None, reason="needs gpg on Linux")
def test_gpg_encrypt_decrypt_roundtrip(runner, tmp_path, monkeypatch):
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))
    f = tmp_path / "secret.txt"
    f.write_text("top secret")
    enc = outputs(job := run(runner, "file.encrypt", [f], {"password": "pw 123", "confirm": "pw 123"}))
    assert job.state == "done" and enc[0].name == "secret.txt.gpg"
    f.rename(tmp_path / "original.txt")
    dec = outputs(job := run(runner, "file.decrypt", [enc[0]], {"password": "pw 123"}))
    assert job.state == "done" and dec[0].read_text() == "top secret"
    job = run(runner, "file.decrypt", [enc[0]], {"password": "wrong"})
    assert job.state == "failed"


# ======================================================================= archive

def _patch_zip_encrypted(path: Path) -> None:
    data = bytearray(path.read_bytes())
    i = data.find(b"PK\x03\x04")
    struct.pack_into("<H", data, i + 6, struct.unpack_from("<H", data, i + 6)[0] | 1)
    j = data.find(b"PK\x01\x02")
    struct.pack_into("<H", data, j + 8, struct.unpack_from("<H", data, j + 8)[0] | 1)
    path.write_bytes(bytes(data))


def test_detect_kind_and_safety_helpers(tmp_path):
    from dropwheel.actions import archive

    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "a")
    assert archive.detect_kind(z).kind == "zip"
    t = tmp_path / "a.tar.gz"
    with tarfile.open(t, "w:gz") as tf:
        info = tarfile.TarInfo("a.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"a"))
    k = archive.detect_kind(t)
    assert (k.kind, k.codec) == ("tar", "gz")
    import gzip

    g = tmp_path / "notes.txt.gz"
    g.write_bytes(gzip.compress(b"hello"))
    assert archive.detect_kind(g).kind == "single"
    assert archive._safe_rel("../evil") is None
    assert archive._safe_rel("/etc/passwd") == ["etc", "passwd"]
    assert archive._safe_rel("C:/x/y") == ["x", "y"]
    assert archive._link_ok(["a", "b"], "../c")
    assert not archive._link_ok(["a"], "../../etc")
    assert not archive._link_ok(["a"], "/etc/passwd")


def test_bomb_check_rejects_suspicious_ratio(runner, tmp_path):
    from dropwheel.actions import archive

    class Ctx:
        lang = "en"

    with pytest.raises(UserError) as exc:
        archive._bomb_check(Ctx(), 5000 * 1000 * 1000, 1000 * 1000, tmp_path, force=False)
    assert exc.value.message in ("err.arc.bomb", "err.arc.no_space")
    archive._bomb_check(Ctx(), 10, 10, tmp_path, force=False)


def _make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_extract_here_smart_placement(runner, tmp_path):
    single = _make_zip(tmp_path / "proj.zip", {"proj/a.txt": b"a", "proj/sub/b.txt": b"b"})
    res = ok(run(runner, "archive.extract_here", [single]))
    out = Path(res["outputs"][0]["path"])
    assert out == tmp_path / "proj" and (out / "sub" / "b.txt").read_bytes() == b"b"
    loose = _make_zip(tmp_path / "loose.zip", {"x.txt": b"x", "y.txt": b"y"})
    res = ok(run(runner, "archive.extract_here", [loose]))
    out = Path(res["outputs"][0]["path"])
    assert out.name == "loose" and sorted(p.name for p in out.iterdir()) == ["x.txt", "y.txt"]
    again = ok(run(runner, "archive.extract_here", [loose]))
    assert Path(again["outputs"][0]["path"]).name == "loose 2"
    assert single.exists() and loose.exists()


def test_extract_skips_traversal_entries(runner, tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../outside.txt", "bad")
        zf.writestr("ok/inside.txt", "good")
    job = run(runner, "archive.extract_here", [evil])
    ok(job)
    assert not (tmp_path.parent / "outside.txt").exists()
    assert (tmp_path / "ok" / "inside.txt").read_text() == "good"
    assert any("unsafe" in w for w in job.warnings)


def _zip_entry(name: str, link: str | None = None) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name)
    zi.create_system = 3
    zi.external_attr = ((stat.S_IFLNK | 0o777) if link is not None else (stat.S_IFREG | 0o644)) << 16
    return zi


def _make_link_zip(path: Path, members: list[tuple[str, str | None, bytes]]) -> Path:
    """``members``: (name, link target or None, data); written in this order."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, link, data in members:
            zf.writestr(_zip_entry(name, link), link.encode() if link is not None else data)
    return path


def _can_symlink(where: Path) -> bool:
    probe = where / ".symlink-probe"
    try:
        os.symlink("x", probe)
    except (OSError, NotImplementedError, AttributeError):
        return False
    probe.unlink()
    return True


def test_zip_symlink_chain_cannot_write_outside_the_folder(runner, tmp_path):
    """a -> ., a/a/b2 -> ../.. would create work/b2 -> <home>, and the next entry writes through it."""
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    if not _can_symlink(tmp_path):
        pytest.skip("needs symlinks")
    evil = _make_link_zip(downloads / "photos.zip", [
        ("a", ".", b""),
        ("a/a/b2", "../..", b""),
        ("b2/.config/fish/conf.d/pwn.fish", None, b"pwned"),
    ])
    job = run(runner, "archive.extract_here", [evil])
    ok(job)
    assert not (home / ".config").exists(), "a member was written outside the extraction folder"
    assert not os.path.lexists(home / "b2") and not os.path.lexists(downloads / "b2")
    out = downloads / "photos"
    assert (out / "b2" / ".config" / "fish" / "conf.d" / "pwn.fish").read_bytes() == b"pwned"
    assert not (out / "b2").is_symlink()
    assert (out / "a").is_symlink() and os.readlink(out / "a") == "."
    assert any("link" in w.lower() for w in job.warnings)


def test_zip_file_after_symlink_goes_to_a_real_folder(runner, tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("needs symlinks")
    base = tmp_path / "x" / "y"
    base.mkdir(parents=True)
    # every target passes the lexical check, then the first link is used as a parent folder
    evil = _make_link_zip(base / "m.zip", [
        ("deep/er/.keep", None, b""),
        ("deep/er/hop", "../..", b""),                      # the root of the extraction
        ("deep/er/hop/x/y/up", "../../../..", b""),         # "deep" for the check, x/y/up -> above the root really
        ("x/y/up/f.txt", None, b"data"),
    ])
    res = ok(run(runner, "archive.extract_here", [evil]))
    out = Path(res["outputs"][0]["path"])
    assert (out / "x" / "y" / "up" / "f.txt").read_bytes() == b"data" and not (out / "x" / "y" / "up").is_symlink()
    assert not (tmp_path / "x" / "f.txt").exists()
    assert not os.path.lexists(out / "deep" / "er" / "x")


@pytest.mark.skipif(not POSIX, reason="Windows resolves '..' in a link target lexically")
@pytest.mark.parametrize("order", ["link_first", "link_last"])
def test_zip_link_resolving_outside_through_another_link_is_removed(runner, tmp_path, order):
    base = tmp_path / "x"
    base.mkdir()
    # s/t/l -> ../.. is the root; k -> s/t/l/.. is lexically s/t but really the parent of the root
    links = [("s/t/l", "../..", b""), ("k", "s/t/l/..", b"")]
    evil = _make_link_zip(base / "m.zip", [
        ("s/t/.keep", None, b""),
        *(links if order == "link_first" else links[::-1]),
        ("other.txt", None, b"o"),
    ])
    job = run(runner, "archive.extract_here", [evil])
    res = ok(job)
    out = Path(res["outputs"][0]["path"])
    assert not os.path.lexists(out / "k")
    assert (out / "other.txt").read_bytes() == b"o"


def test_zip_valid_symlinks_survive_and_stay_relative(runner, tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("needs symlinks")
    z = _make_link_zip(tmp_path / "ok.zip", [
        ("lib/current", "v1", b""),                         # the target comes later in the archive
        ("lib/v1/a.txt", None, b"a"),
        ("alias.txt", "lib/v1/a.txt", b""),
        ("abs", "/etc/passwd", b""),
    ])
    job = run(runner, "archive.extract_here", [z])
    res = ok(job)
    out = Path(res["outputs"][0]["path"])
    assert os.readlink(out / "lib" / "current") == "v1" and (out / "lib" / "current" / "a.txt").read_bytes() == b"a"
    assert os.readlink(out / "alias.txt") == "lib/v1/a.txt"
    if POSIX:   # a target written with "/" does not resolve on Windows
        assert (out / "alias.txt").read_bytes() == b"a"
    assert not os.path.lexists(out / "abs")
    assert any("link" in w.lower() for w in job.warnings)


def test_zip_oversized_symlink_entry_is_not_loaded(runner, tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("needs symlinks")
    z = tmp_path / "big.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_zip_entry("huge", "x"), b"a" * 200_000)
        zf.writestr("keep.txt", b"k")
    job = run(runner, "archive.extract_here", [z])
    res = ok(job)
    out = Path(res["outputs"][0]["path"])
    assert (out if out.is_file() else out / "keep.txt").read_bytes() == b"k"
    assert not os.path.lexists(out.parent / "huge") and any("link" in w.lower() for w in job.warnings)


def test_zip_file_and_folder_name_clash_is_not_fatal(runner, tmp_path):
    z = _make_zip(tmp_path / "clash.zip", {"same": b"file", "same/inner.txt": b"x", "same2/": b"", "same2": b"y",
                                           "good.txt": b"g"})
    job = run(runner, "archive.extract_here", [z])
    res = ok(job)
    out = Path(res["outputs"][0]["path"])
    assert (out / "same").read_bytes() == b"file" and (out / "good.txt").read_bytes() == b"g"
    assert any("name" in w.lower() or "duplicate" in w.lower() for w in job.warnings)


@pytest.mark.parametrize("code,message", [(28, "err.disk_full"), (13, "err.arc.extract_failed")])
def test_zip_write_errors_are_explained(monkeypatch, runner, tmp_path, code, message):
    from dropwheel.actions import archive

    z = _make_zip(tmp_path / "a.zip", {"a.txt": b"a"})

    def fail(_self, _n):
        raise OSError(code, "fail")

    monkeypatch.setattr(archive._Guard, "add", fail)
    job = run(runner, "archive.extract_here", [z])
    assert job.state == "failed" and job.error["message"] == i18n.t(message, "en", size="?", name="a.txt")


def test_smart_placement_drops_links_that_escape_after_the_move(runner, tmp_path):
    """proj/up -> .. is inside the temporary folder but would point at the user's folder once proj moves out."""
    if not _can_symlink(tmp_path):
        pytest.skip("needs symlinks")
    z = _make_link_zip(tmp_path / "proj.zip", [
        ("proj/a.txt", None, b"a"),
        ("proj/up", "..", b""),
        ("proj/sibling", "../other", b""),
        ("proj/fine", "a.txt", b""),
    ])
    job = run(runner, "archive.extract_here", [z])
    res = ok(job)
    out = Path(res["outputs"][0]["path"])
    assert out == tmp_path / "proj"
    assert not os.path.lexists(out / "up") and not os.path.lexists(out / "sibling")
    assert os.readlink(out / "fine") == "a.txt"
    assert any("link" in w.lower() for w in job.warnings)


def test_tar_symlink_chain_is_refused_too(runner, tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("needs symlinks")
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    t = downloads / "chain.tar"
    with tarfile.open(t, "w") as tf:
        for name, target in (("a", "."), ("a/a/b2", "../..")):
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
        info = tarfile.TarInfo("b2/pwn.txt")
        info.size = 5
        tf.addfile(info, io.BytesIO(b"pwned"))
    run(runner, "archive.extract_here", [t])
    assert not (home / "pwn.txt").exists()


def test_tar_traversal_and_absolute_links_are_filtered(runner, tmp_path):
    t = tmp_path / "bad.tar"
    with tarfile.open(t, "w") as tf:
        for name, data in (("good/a.txt", b"a"), ("../escape.txt", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("good/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    ok(run(runner, "archive.extract_here", [t]))
    assert not (tmp_path.parent / "escape.txt").exists() and not (tmp_path / "escape.txt").exists()
    assert (tmp_path / "good" / "a.txt").read_bytes() == b"a"
    assert not os.path.lexists(tmp_path / "good" / "link")


def test_encrypted_zip_needs_password(runner, tmp_path):
    z = _make_zip(tmp_path / "locked.zip", {"a.txt": b"aaaa"})
    _patch_zip_encrypted(z)
    job = run(runner, "archive.extract_here", [z])
    assert job.state == "failed"
    assert "password" in job.error["message"].lower()
    assert not (tmp_path / "a.txt").exists()


def test_list_test_convert_and_join(runner, tmp_path):
    z = _make_zip(tmp_path / "data.zip", {"d/a.txt": b"a" * 100, "d/b.txt": b"b"})
    res = ok(run(runner, "archive.list", [z]))
    assert "a.txt" in res["text"] and "b.txt" in res["text"]
    res = ok(run(runner, "archive.test", [z]))
    assert res["kind"] == "info"
    tgz = Path(ok(run(runner, "archive.convert.tar_gz", [z]))["outputs"][0]["path"])
    assert tgz.name == "data.tar.gz"
    with tarfile.open(tgz) as tf:
        assert sorted(m.name for m in tf.getmembers() if m.isfile()) == ["d/a.txt", "d/b.txt"]
    txz = Path(ok(run(runner, "archive.convert.tar_xz", [tgz]))["outputs"][0]["path"])
    with tarfile.open(txz) as tf:
        assert "d/a.txt" in tf.getnames()
    blob = os.urandom(5000)
    (tmp_path / "big.bin.001").write_bytes(blob[:2000])
    (tmp_path / "big.bin.002").write_bytes(blob[2000:4000])
    (tmp_path / "big.bin.003").write_bytes(blob[4000:])
    res = ok(run(runner, "archive.join", [tmp_path / "big.bin.001"]))
    assert Path(res["outputs"][0]["path"]).read_bytes() == blob


def test_split_and_join_roundtrip(runner, tmp_path):
    from dropwheel.actions.archive import split_names

    assert split_names("a.zip", 3) == ["a.zip.001", "a.zip.002", "a.zip.003"]
    assert split_names("a", 1200)[-1] == "a.1200"
    blob = os.urandom(2_500_000)
    z = _make_zip(tmp_path / "split me.zip", {"r.bin": blob})
    original = z.read_bytes()
    menu = registry.build_menu([str(z)], cfgmod.load(), "en")
    ids = {n["id"] for n in menu["pages"][0]["items"]}
    assert "archive.split.custom" in ids  # only the custom size applies to a 2.5 MB file (group flattened)
    res = ok(run(runner, "archive.split.custom", [z], {"size_mb": 1}))
    parts = [Path(o["path"]) for o in res["outputs"]]
    assert [p.name for p in parts] == ["split me.zip.001", "split me.zip.002", "split me.zip.003"]
    assert all(p.parent == tmp_path for p in parts)
    assert b"".join(p.read_bytes() for p in parts) == original
    assert z.read_bytes() == original  # the input is untouched
    # a second split never mixes with the existing series: it goes into its own folder
    res = ok(run(runner, "archive.split.custom", [z], {"size_mb": 2}))
    again = [Path(o["path"]) for o in res["outputs"]]
    assert len(again) == 2 and again[0].parent != tmp_path and again[0].name == "split me.zip.001"
    # the first part of a series offers Join (default of the Split/Join group) and rebuilds the file
    menu = registry.build_menu([str(parts[0])], cfgmod.load(), "en")
    assert registry.find_node(menu, "archive.join") is not None
    joined = Path(ok(run(runner, "archive.join", [parts[0]]))["outputs"][0]["path"])
    assert joined.read_bytes() == original and joined != z


_FAKE_7Z = r"""
import sys, time
password = sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else ""
mode = sys.argv[1]
if mode == "progress":
    out = sys.stdout.buffer
    for pct in (5, 30, 60, 90):
        text = (f"{pct:3d}% 1 + file.bin").encode()
        out.write(text + b"\b" * len(text))
        out.flush()
        time.sleep(0.35)
    out.write(b"Everything is Ok\n")
    sys.exit(0)
if mode == "password":
    if password == "pw":
        sys.exit(0)
    sys.stderr.write("ERROR: Wrong password : file.bin\n")
    sys.exit(2)
if mode == "slow":
    time.sleep(30)
"""


class _FakeCtx:
    """The parts of a job Context that ``run_7z`` and ``Progress`` use."""

    lang = "en"

    def __init__(self):
        self.reports = []
        self.cancel = False
        self.logs = []

    @property
    def cancelled(self):
        return self.cancel

    def check_cancel(self):
        from dropwheel.errors import Cancelled

        if self.cancel:
            raise Cancelled()

    def progress(self, frac, message=None):
        self.reports.append(frac)

    def log(self, msg):
        self.logs.append(msg)

    def t(self, key, **fmt):
        return key


@pytest.fixture
def fake_7z(tmp_path, monkeypatch):
    import sys

    from dropwheel import tools as tools_mod

    script = tmp_path / "fake7z.py"
    script.write_text(_FAKE_7Z, encoding="utf-8")
    real = tools_mod.path
    monkeypatch.setattr(tools_mod, "path", lambda name: sys.executable if name == "7z" else real(name))
    return str(script)


def test_run_7z_live_progress_password_and_errors(fake_7z):
    import time

    from dropwheel.actions.archive import _map_7z_error, run_7z
    from dropwheel.actions.common import Progress
    from dropwheel.errors import Cancelled, ToolError

    ctx = _FakeCtx()
    out = run_7z(ctx, ["7z", fake_7z, "progress"], prog=Progress(ctx))
    assert "Everything is Ok" in out
    live = [r for r in ctx.reports if r is not None and 0 < r < 1]
    assert live and max(live) >= 0.3  # percentages arrived while 7-Zip was still running
    assert ctx.reports[-1] == 1.0
    ctx = _FakeCtx()
    run_7z(ctx, ["7z", fake_7z, "password"], password="pw")  # the password travels on stdin
    with pytest.raises(ToolError) as info:
        run_7z(ctx, ["7z", fake_7z, "password"], password="bad", ok_codes=(0,))
    assert "Wrong password" in info.value.details
    assert _map_7z_error(info.value, "bad").message == "err.wrong_password"
    assert not any("bad" in line for line in ctx.logs)  # never logged
    # cancellation and a failing tick (zip-bomb guard) stop 7-Zip at once
    ctx = _FakeCtx()
    started = time.monotonic()

    def cancel_soon():
        if time.monotonic() - started > 0.3:
            ctx.cancel = True

    with pytest.raises(Cancelled):
        run_7z(ctx, ["7z", fake_7z, "slow"], tick=cancel_soon)
    assert time.monotonic() - started < 10

    def boom():
        raise UserError("err.arc.too_big", size="1 GB")

    with pytest.raises(UserError) as info:
        run_7z(_FakeCtx(), ["7z", fake_7z, "slow"], tick=boom)
    assert info.value.message == "err.arc.too_big"


def test_extract_to_and_single_stream(runner, tmp_path):
    import gzip

    z = _make_zip(tmp_path / "pkg.zip", {"one.txt": b"1"})
    dest = tmp_path / "target dir"
    res = ok(run(runner, "archive.extract_to", [z], {"dest": str(dest)}))
    assert (dest / "one.txt").read_bytes() == b"1"
    g = tmp_path / "notes.txt.gz"
    g.write_bytes(gzip.compress(b"hello world"))
    res = ok(run(runner, "archive.extract_here", [g]))
    assert Path(res["outputs"][0]["path"]).read_bytes() == b"hello world"


def test_7z_commands_take_names_literally(monkeypatch, tmp_path):
    """'?' and '*' are legal in names: without -spd 7-Zip packs or opens everything that matches."""
    from types import SimpleNamespace

    from dropwheel.actions import archive, system

    seen: list[tuple[list[str], dict]] = []

    def fake_run_7z(_ctx, argv, **kw):
        seen.append(([str(a) for a in argv], kw))
        return ""

    class Ctx:
        def run(self, argv, **kw):
            seen.append(([str(a) for a in argv], kw))
            return SimpleNamespace(stdout="")

    monkeypatch.setattr(archive, "run_7z", fake_run_7z)
    monkeypatch.setattr(archive.tools, "have", lambda _name: True)
    ctx = Ctx()
    work = tmp_path / "with-star"
    name = str(tmp_path / "Foto-q.zip")
    payload = tmp_path / "payload.squashfs"
    payload.write_bytes(b"x")
    monkeypatch.setattr(system, "_payload_copy", lambda *_a, **_k: payload)
    archive._create_7z(ctx, [Path(name)], tmp_path / "out.part", "zip", 5, "", None)
    archive._list_7z(ctx, name, "")
    archive._extract_7z(ctx, name, work, "", None, None)
    archive._test_7z(ctx, name, "", None)
    system._extract_7z(ctx, name, 0, work)
    assert {argv[1] for argv, _kw in seen} == {"a", "l", "x", "t"}
    for argv, kw in seen:
        assert argv[0] == "7z" and "-spd" in argv and argv.index("-spd") < argv.index("--"), argv
        if argv[1] == "x":      # "-o" would replace a "*" of the destination with the archive name
            assert "-o." in argv and not any(str(work) in a for a in argv) and kw["cwd"] == work


@pytest.mark.skipif(not tools.have("7z"), reason="needs 7-Zip")
def test_7z_roundtrip_with_literal_names(runner, tmp_path):
    d = tmp_path / "plain"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("a")
    (d / "sub" / "b.txt").write_text("b")
    res = ok(run(runner, "folder.compress.7z", [d]))
    arc = Path(res["outputs"][0]["path"])
    assert arc.name == "plain.7z"
    shutil.rmtree(d)
    assert ok(run(runner, "archive.test", [arc]))["message"]
    listing = ok(run(runner, "archive.list", [arc]))
    assert "b.txt" in listing["text"]
    res = ok(run(runner, "archive.extract_here", [arc]))
    assert Path(res["outputs"][0]["path"]) == d and (d / "sub" / "b.txt").read_text() == "b"


@pytest.mark.skipif(not POSIX or not tools.have("7z"), reason="needs 7-Zip and '?' in file names")
def test_7z_wildcard_names_end_to_end(runner, tmp_path):
    for name in ("Contratto?", "Contratto1", "ContrattoB"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name}.txt").write_text(name)
    res = ok(run(runner, "folder.compress.zip_password", [tmp_path / "Contratto?"],
                 {"password": "pw", "confirm": "pw"}))
    with zipfile.ZipFile(res["outputs"][0]["path"]) as zf:
        names = zf.namelist()
    assert any(n.endswith(".txt") for n in names) and not any("Contratto1" in n or "ContrattoB" in n for n in names)
    dest = tmp_path / "with*star"
    dest.mkdir()
    for folder, arc_name in (("one", "Foto?.7z"), ("two", "FotoA.7z")):
        d = tmp_path / folder
        d.mkdir()
        (d / "a.txt").write_text(folder)
        res = ok(run(runner, "folder.compress.7z", [d]))
        os.rename(res["outputs"][0]["path"], dest / arc_name)
    res = ok(run(runner, "archive.extract_to", [dest / "Foto?.7z"], {"dest": str(dest)}))
    assert Path(res["outputs"][0]["path"]) == dest / "one"
    assert sorted(p.name for p in dest.iterdir()) == ["Foto?.7z", "FotoA.7z", "one"]


def test_unrar_gets_an_archive_name_without_wildcards(monkeypatch, tmp_path):
    from dropwheel.actions import archive

    if not _can_symlink(tmp_path):
        pytest.skip("needs symlinks")
    seen: list[list[str]] = []
    jobtmp = tmp_path / "tmp"
    jobtmp.mkdir()

    class Ctx:
        def tmpdir(self):
            return jobtmp

        def run(self, argv, **_kw):
            seen.append([str(a) for a in argv])

    name = str(tmp_path / "Foto?.rar")
    archive._extract_unrar(Ctx(), name, tmp_path, None, None)
    used = seen[0][5]
    assert used != name and "?" not in used and os.path.islink(used) and Path(used).parent == jobtmp
    assert os.readlink(used).endswith(name)
    seen.clear()
    plain = str(tmp_path / "plain.rar")
    archive._extract_unrar(Ctx(), plain, tmp_path, None, None)
    assert seen[0][5] == plain


def test_create_archive_formats_and_split(runner, tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    (d / "f.bin").write_bytes(os.urandom(300_000))
    res = ok(run(runner, "folder.compress.tar_gz", [d]))
    assert Path(res["outputs"][0]["path"]).name == "src.tar.gz"
    res = ok(run(runner, "folder.compress.zip", [d], {"split": 0, "level": "fast"}))
    assert Path(res["outputs"][0]["path"]).name == "src.zip"


@pytest.mark.skipif(not tools.have("7z"), reason="needs 7-Zip")
def test_7z_password_roundtrip(runner, tmp_path):
    d = tmp_path / "secret"
    d.mkdir()
    (d / "a.txt").write_text("hidden")
    res = ok(run(runner, "folder.compress.7z_password", [d], {"password": "pw", "confirm": "pw"}))
    arc = Path(res["outputs"][0]["path"])
    job = run(runner, "archive.extract_here", [arc])
    assert job.state == "failed"
    job = run(runner, "archive.extract_to", [arc], {"dest": str(tmp_path / "out"), "password": "bad"})
    assert job.state == "failed"
    ok(run(runner, "archive.extract_to", [arc], {"dest": str(tmp_path / "out"), "password": "pw"}))
    assert (tmp_path / "out" / "secret" / "a.txt").read_text() == "hidden"


# ======================================================================= fonts

def _make_font(path: Path, family: str = "Dropwheel Test") -> Path:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def box(x0, y0, x1, y1):
        pen = TTGlyphPen(None)
        pen.moveTo((x0, y0))
        pen.lineTo((x0, y1))
        pen.lineTo((x1, y1))
        pen.lineTo((x1, y0))
        pen.closePath()
        return pen.glyph()

    order = [".notdef", "space", "A", "B", "a", "egrave"]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x20: "space", 0x41: "A", 0x42: "B", 0x61: "a", 0xE8: "egrave"})
    glyphs = {".notdef": box(50, 0, 450, 700), "space": TTGlyphPen(None).glyph(), "A": box(50, 0, 550, 700),
              "B": box(60, 0, 520, 700), "a": box(50, 0, 450, 500), "egrave": box(50, 0, 450, 650)}
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({g: (600, 50) for g in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular", "version": "Version 1.000",
                       "uniqueFontIdentifier": "dw-test", "fullName": f"{family} Regular",
                       "psName": "DropwheelTest-Regular", "licenseDescription": "Test licence"})
    fb.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200, usWeightClass=400, fsType=0)
    fb.setupPost()
    fb.save(str(path))
    return path


def test_font_pure_helpers():
    from dropwheel.actions import fonts

    cps = fonts.parse_unicode_ranges("U+0041-0043, U+00E8, 20, U+01F?")
    assert cps[:3] == [0x20, 0x41, 0x42] and 0xE8 in cps and 0x1F0 in cps and 0x1FF in cps
    with pytest.raises(UserError):
        fonts.parse_unicode_ranges("U+00?A")
    with pytest.raises(UserError):
        fonts.parse_unicode_ranges("U+ZZZZ")
    with pytest.raises(UserError):
        fonts.parse_unicode_ranges("U+0050-0040")
    uni, text = fonts.subset_unicodes("latin")
    assert 0x41 in uni and text == ""
    uni, text = fonts.subset_unicodes("text", text="Ciao")
    assert uni == [] and text == "Ciao"
    css = fonts.css_font_face('My "Font"', "my font", "400", "normal")
    assert 'font-family: "My \\"Font\\"";' in css and 'url("my%20font.woff2")' in css
    t = lambda k: k  # noqa: E731
    assert fonts.embedding_text(0x0002 | 0x0100, t) == "info.font.embed_restricted · info.font.embed_no_subset"
    assert fonts.pangram("it").startswith("Ma la volpe")


def test_font_actions(runner, tmp_path):
    pytest.importorskip("fontTools")
    font = _make_font(tmp_path / "DW Test.ttf")
    from dropwheel.actions import fonts

    assert fonts.sniff(font) == "ttf"
    info = ok(run(runner, "font.info", [font]))
    values = {row["key"]: row["value"] for row in info["info"]}
    assert values["Family"] == "Dropwheel Test" and values["Glyphs"] == "6"
    otf = Path(ok(run(runner, "font.convert.otf", [font]))["outputs"][0]["path"])
    assert fonts.sniff(otf) == "otf"
    back = Path(ok(run(runner, "font.convert.ttf", [otf]))["outputs"][0]["path"])
    assert fonts.sniff(back) == "ttf"
    woff = Path(ok(run(runner, "font.convert.woff", [font]))["outputs"][0]["path"])
    assert fonts.sniff(woff) == "woff"
    sheet = Path(ok(run(runner, "font.preview", [font]))["outputs"][0]["path"])
    from PIL import Image

    with Image.open(sheet) as im:
        assert im.width == 1600 and im.height > 400
    sub = Path(ok(run(runner, "font.subset", [font], {"preset": "text", "text": "AB", "format": "same"}))
               ["outputs"][0]["path"])
    assert sub.name == "DW Test (Subset).ttf"
    from fontTools.ttLib import TTFont

    with TTFont(str(sub)) as f:
        assert set(f.getBestCmap()) == {0x41, 0x42}
    job = run(runner, "font.subset", [font], {"preset": "unicodes", "unicodes": "U+4E00", "format": "same"})
    assert job.state == "failed"


def test_font_woff2_and_webkit(runner, tmp_path):
    pytest.importorskip("fontTools")
    pytest.importorskip("brotli")
    font = _make_font(tmp_path / "Web.ttf")
    woff2 = Path(ok(run(runner, "font.convert.woff2", [font]))["outputs"][0]["path"])
    from dropwheel.actions import fonts

    assert fonts.sniff(woff2) == "woff2"
    kit = Path(ok(run(runner, "font.webkit", [font]))["outputs"][0]["path"])
    assert sorted(p.name for p in kit.iterdir()) == ["Web.css", "Web.woff", "Web.woff2", "demo.html"]
    assert "font-display: swap" in (kit / "Web.css").read_text(encoding="utf-8")


def test_font_install_copies_and_refreshes(runner, tmp_path, monkeypatch, dropwheel_env):
    pytest.importorskip("fontTools")
    from dropwheel.jobs import Context

    calls = []
    real_run = Context.run

    def fake_run(self, argv, **kw):
        if argv and argv[0] == "fc-cache":
            calls.append(list(argv))
            import subprocess

            return subprocess.CompletedProcess(argv, 0, "", "")
        return real_run(self, argv, **kw)

    monkeypatch.setattr(Context, "run", fake_run)
    act = registry.get("font.install")
    monkeypatch.setattr(act, "requires", [])
    font = _make_font(tmp_path / "Inst.ttf", family="Install Me")
    res = ok(run(runner, "font.install", [font]))
    installed = Path(res["outputs"][0]["path"])
    assert installed == dropwheel_env.data / "fonts" / "Install Me" / "Inst.ttf"
    assert installed.read_bytes() == font.read_bytes() and font.exists()
    assert calls and calls[0][:2] == ["fc-cache", "-f"]
    again = ok(run(runner, "font.install", [font]))
    assert again["kind"] == "none"
    menu = registry.build_menu([str(installed)], cfgmod.load(), "en")
    ids = [n["id"] for n in menu["pages"][0]["items"]]
    assert "font.uninstall" in ids and "font.install" not in ids


# ======================================================================= folder

def _make_tree(root: Path) -> Path:
    from PIL import Image

    (root / "sub").mkdir(parents=True)
    (root / ".hidden").write_text("h")
    for i in (10, 2, 1):
        Image.new("RGB", (40 + i, 30), (i * 20, 90, 120)).save(root / f"img {i}.jpg")
    (root / "a.txt").write_text("same" * 50)
    (root / "sub" / "b.txt").write_text("same" * 50)
    (root / "sub" / "c.txt").write_text("other")
    return root


def test_walk_and_helpers(tmp_path):
    from dropwheel.actions import folder

    root = _make_tree(tmp_path / "tree")
    names = [p.name for p, _st in folder.walk_files(root)]
    assert ".hidden" not in names and names[0] == "a.txt"
    assert [p.name for p in folder.images_in(root)] == ["img 1.jpg", "img 2.jpg", "img 10.jpg"]
    groups = folder.find_duplicates([root])
    assert len(groups) == 1 and sorted(p.name for p in groups[0].paths) == ["a.txt", "b.txt"]
    br = folder.size_breakdown(root)
    assert br.files == 7 and br.folders == 1
    line = folder.sums_line("ab" * 32, "dir/new\nline.txt")
    assert line.startswith("\\") and "\\n" in line
    parsed = folder.parse_sums(line + "\n" + "cd" * 32 + "  dir/plain.txt\n")
    assert parsed[0] == ("sha256", "ab" * 32, "dir/new\nline.txt")
    assert parsed[1][2] == "dir/plain.txt"


def test_folder_actions(runner, tmp_path):
    root = _make_tree(tmp_path / "Holiday photos")
    res = ok(run(runner, "folder.size", [root]))
    assert res["kind"] == "info"
    res = ok(run(runner, "folder.types", [root]))
    assert any(row["key"] == ".jpg" for row in res["info"])
    listing = Path(ok(run(runner, "folder.list_txt", [root]))["outputs"][0]["path"])
    assert listing.name == "Holiday photos (Listing).txt" and "img 10.jpg" in listing.read_text(encoding="utf-8")
    pdf = Path(ok(run(runner, "folder.images_pdf", [root]))["outputs"][0]["path"])
    assert pdf.name == "Holiday photos.pdf"
    from pypdf import PdfReader

    assert len(PdfReader(str(pdf)).pages) == 3
    res = ok(run(runner, "folder.duplicates", [root]))
    assert "a.txt" in res["text"] and "sub/b.txt" in res["text"]
    flat = Path(ok(run(runner, "folder.flatten", [root]))["outputs"][0]["path"])
    assert sorted(p.name for p in flat.iterdir()) == ["a.txt", "b.txt", "c.txt", "img 1.jpg", "img 10.jpg",
                                                      "img 2.jpg"]
    sums = Path(ok(run(runner, "folder.checksum.create", [root]))["outputs"][0]["path"])
    assert sums.name == "Holiday photos.sha256"
    res = ok(run(runner, "folder.checksum.verify", [root]))
    assert "6 correct" in res["text"]
    (root / "sub" / "c.txt").write_text("tampered")
    res = ok(run(runner, "folder.checksum.verify", [root]))
    assert "c.txt" in res["text"] and "1 different" in res["message"]
    res = ok(run(runner, "folder.rename", [root], {"pattern": "pic {n:03}", "dry_run": True}))
    assert "pic 001.txt" in res["text"] and (root / "a.txt").exists()
    ok(run(runner, "folder.rename", [root], {"pattern": "pic {n:03}"}))
    assert (root / "pic 002.jpg").exists() and (root / ".hidden").exists()


def test_images_pdf_with_pillow_only(runner, tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "imgs")
    real_have = tools.have
    monkeypatch.setattr(tools, "have", lambda name: False if name == "py:pymupdf" else real_have(name))
    pdf = Path(ok(run(runner, "folder.images_pdf", [root], {"page": "image"}))["outputs"][0]["path"])
    from pypdf import PdfReader

    assert len(PdfReader(str(pdf)).pages) == 3


# ======================================================================= web

def test_web_name_helpers():
    from dropwheel.actions import web

    cd = "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf; filename=\"fallback.pdf\""
    assert web.filename_from_content_disposition(cd) == "résumé.pdf"
    assert web.filename_from_content_disposition('attachment; filename="../../.bashrc"') == "bashrc"
    from dropwheel import util

    assert web.filename_from_content_disposition('inline; filename="a \\"b\\".txt"') == util.safe_filename('a "b".txt')
    assert web.filename_from_content_disposition(None) == ""
    assert web.filename_from_url("https://x.org/dir/My%20File.tar.gz?x=1") == "My File.tar.gz"
    assert web.filename_from_url("https://x.org/") == ""
    assert web.guess_extension("text/html; charset=utf-8") == "html"
    assert web.guess_extension("application/octet-stream") == ""
    assert web.choose_name(None, "https://x.org/report", "application/pdf") == "report.pdf"
    assert web.choose_name(None, "https://x.org/", "").startswith("download-")
    hdrs = web.parse_headers("HTTP/1.1 302 Found\r\nLocation: /b\r\n\r\nHTTP/1.1 200 OK\r\n"
                             "Content-Type: text/plain\r\nContent-Disposition: attachment; filename=x.txt\r\n\r\n")
    assert hdrs["content-type"] == "text/plain" and "location" not in hdrs
    assert web.clean_url("https://s.com/p?utm_source=a&id=3&fbclid=z#frag") == "https://s.com/p?id=3#frag"
    assert web.clean_url("https://www.youtube.com/watch?v=abc&si=xyz&feature=share") == \
        "https://www.youtube.com/watch?v=abc"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/file"):
            body = b"PDFDATA" * 1000
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/page"):
            body = "<html><head><title>Pagina di prova</title></head><body>ciao</body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


@pytest.fixture
def http_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("engine", ["curl", "python"])
def test_url_download(runner, http_server, dropwheel_env, monkeypatch, engine):
    if engine == "curl" and not tools.have("curl"):
        pytest.skip("curl missing")
    if engine == "python":
        real_have = tools.have
        monkeypatch.setattr(tools, "have", lambda name: False if name == "curl" else real_have(name))
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    res = ok(run(runner, "url.download", [f"{http_server}/file?id=1"]))
    out = Path(res["outputs"][0]["path"])
    assert out.parent == dropwheel_env.downloads and out.name == "résumé.pdf"
    assert out.read_bytes() == b"PDFDATA" * 1000
    assert not [p for p in dropwheel_env.downloads.iterdir() if p.name.startswith(".dropwheel")]
    job = run(runner, "url.download", [f"{http_server}/missing"])
    assert job.state == "failed" and "404" in job.error["message"]


def test_url_copy_shortcut_and_list(runner, http_server, dropwheel_env, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    res = ok(run(runner, "url.copy.clean", ["https://example.com/a?utm_medium=x&q=1"]))
    assert res["text"] == "https://example.com/a?q=1"
    res = ok(run(runner, "url.copy.markdown", [f"{http_server}/page"]))
    assert res["text"] == f"[Pagina di prova]({http_server}/page)"
    sc = Path(ok(run(runner, "url.shortcut", [f"{http_server}/page"], {"format": "desktop"}))["outputs"][0]["path"])
    assert sc.name == "Pagina di prova.desktop"
    text = sc.read_text(encoding="utf-8")
    assert "Type=Link" in text and f"URL={http_server}/page" in text
    lst = Path(ok(run(runner, "url.save_list", ["https://a.org/1", "https://b.org/2"]))["outputs"][0]["path"])
    assert lst.read_text(encoding="utf-8").splitlines() == ["https://a.org/1", "https://b.org/2"]


@pytest.mark.skipif(not (tools.have("py:qrcode") or tools.have("qrencode")), reason="needs qrcode or qrencode")
def test_url_qr(runner):
    res = ok(run(runner, "url.qr.show", ["https://example.com/x"]))
    assert res["kind"] == "qr" and Path(res["qr"]).is_file()


# ======================================================================= system

def _fake_appimage(path: Path) -> Path:
    head = bytearray(64)
    head[:4] = b"\x7fELF"
    head[4] = 2
    head[5] = 1
    head[8:11] = b"AI\x02"
    struct.pack_into("<H", head, 0x12, 0x3E)
    struct.pack_into("<Q", head, 0x28, 100)
    struct.pack_into("<HH", head, 0x3A, 64, 2)
    body = bytes(head) + bytes(228 - 64) + b"hsqs" + bytes(60)
    path.write_bytes(body)
    return path


def test_appimage_helpers(tmp_path):
    from dropwheel.actions import system

    app = _fake_appimage(tmp_path / "Tool.AppImage")
    assert system.elf_payload_offset(app) == 228
    assert system.appimage_type(app) == 2
    assert system._elf_machine(app) == "x86-64"
    assert system.desktop_quote("/opt/a.AppImage") == "/opt/a.AppImage"
    assert system.desktop_quote("/home/u/My Apps/a$b.AppImage") == '"/home/u/My Apps/a\\\\$b.AppImage"'
    text = system.rewrite_desktop("[Desktop Entry]\nName=Tool\nExec=AppRun %F\nTryExec=AppRun\nIcon=tool\n\n"
                                  "[Desktop Action N]\nExec=AppRun --new\n", "/apps/Tool.AppImage", "/i/tool.png")
    assert "Exec=/apps/Tool.AppImage %F" in text and "TryExec" not in text and "Icon=/i/tool.png" in text
    assert "Exec=/apps/Tool.AppImage --new" in text
    assert text.index("X-AppImage-Path=/apps/Tool.AppImage") < text.index("[Desktop Action N]")
    assert system._resolve_inside("", "usr/share/x.png") == "usr/share/x.png"
    assert system._resolve_inside("", "../../etc/passwd") is None


def test_binary_helpers():
    from dropwheel.actions import system

    assert system.shannon_entropy(b"\x00" * 100) == 0
    assert system.shannon_entropy(bytes(range(256)) * 4) == pytest.approx(8.0)
    dump = system.hexdump(b"Hello, world!\x00\x01")
    assert dump.startswith("00000000  48 65 6c 6c 6f 2c 20 77  6f") and dump.endswith("Hello, world!..")
    data = b"\x01\x02abc\x00Hello there\xffmore text here\x00\x01\x01" + "wide str".encode("utf-16-le")
    for chunk in (5, 16, 64, 1 << 20):
        found = [t for _o, t in system.iter_strings(io.BytesIO(data), 4, True, chunk=chunk)]
        assert found == ["Hello there", "more text here", "wide str"], chunk
    rows = system.parse_kv_output("Name            : hello\nDescription     : first\n    second line\nEmpty :\n")
    assert rows[0] == ("Name", "hello") and rows[1] == ("Description", "first second line")


def test_disk_format_parsing(tmp_path):
    from dropwheel.actions import system

    pvd = bytearray(2048)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[8:40] = b"LINUX".ljust(32)
    pvd[40:72] = b"MY_LABEL".ljust(32)
    struct.pack_into("<I", pvd, 80, 20)
    struct.pack_into("<H", pvd, 128, 2048)
    pvd[813:829] = b"2024010112300000"
    boot = bytearray(2048)
    boot[0] = 0
    boot[1:6] = b"CD001"
    term = bytearray(2048)
    term[0] = 255
    term[1:6] = b"CD001"
    iso = tmp_path / "x.iso"
    iso.write_bytes(bytes(16 * 2048) + bytes(pvd) + bytes(boot) + bytes(term) + bytes(2048))
    info = system.disk_format(iso)
    assert info["format"] == "iso9660" and info["iso_label"] == "MY_LABEL" and info["iso_bootable"] is True
    assert info["iso_size"] == 20 * 2048 and info["iso_created"] == "2024-01-01 12:30"
    q = tmp_path / "vm.qcow2"
    head = bytearray(512)
    head[:4] = b"QFI\xfb"
    struct.pack_into(">I", head, 4, 3)
    struct.pack_into(">Q", head, 24, 10 * 1024 ** 3)
    q.write_bytes(bytes(head))
    assert system.disk_format(q) == {"format": "qcow2", "virtual_size": 10 * 1024 ** 3}


def _tar_bytes(files: dict[str, bytes], mode: str = "w:gz") -> bytes:
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode=mode) as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return bio.getvalue()


def _ar(members) -> bytes:
    out = b"!<arch>\n"
    for name, data in members:
        out += f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(data):<10}`\n".encode() + data
        if len(data) % 2:
            out += b"\n"
    return out


def test_package_actions(runner, tmp_path):
    from dropwheel.actions import system

    control = _tar_bytes({"./control": b"Package: hello\nVersion: 1.0-1\nDescription: Greeter\n longer text\n"})
    data = _tar_bytes({"./usr/bin/hello": b"#!/bin/sh\necho hi\n", "./usr/share/doc/hello/README": b"r"}, "w:xz")
    deb = tmp_path / "hello_1.0-1_amd64.deb"
    deb.write_bytes(_ar([("debian-binary", b"2.0\n"), ("control.tar.gz", control), ("data.tar.xz", data)]))
    assert [m[0] for m in system.ar_members(deb)] == ["debian-binary", "control.tar.gz", "data.tar.xz"]
    res = ok(run(runner, "package.info", [deb]))
    values = {row["key"]: row["value"] for row in res["info"]}
    assert values["Package"] == "hello" and values["Description"] == "Greeter longer text"
    res = ok(run(runner, "package.files", [deb]))
    assert "README" in res["text"]
    out = Path(ok(run(runner, "package.extract", [deb]))["outputs"][0]["path"])
    assert (out / "usr" / "bin" / "hello").exists() and (out / "DEBIAN" / "control").exists()
    pkg = tmp_path / "hello-1.0-1-x86_64.pkg.tar.xz"
    pkg.write_bytes(_tar_bytes({".PKGINFO": b"pkgname = hello\npkgver = 1.0-1\ndepend = glibc\ndepend = bash\n",
                                "usr/bin/hello": b"x"}, "w:xz"))
    if not tools.have("pacman"):
        res = ok(run(runner, "package.info", [pkg]))
        values = {row["key"]: row["value"] for row in res["info"]}
        assert values["Name"] == "hello" and values["Depends on"] == "glibc, bash"
    res = ok(run(runner, "package.files", [pkg]))
    assert "hello" in res["text"] and ".PKGINFO" not in res["text"]


def test_install_command(monkeypatch):
    from dropwheel.actions import system
    from dropwheel.detect import FileInfo

    monkeypatch.setattr(tools, "have", lambda name: name in ("pacman", "flatpak"))
    pkg = FileInfo(path="/x/a b.pkg.tar.zst", name="a b.pkg.tar.zst", stem="a b", ext="pkg.tar.zst",
                   category="package")
    assert system.install_command(pkg, "/x/a b.pkg.tar.zst") == ["sudo", "pacman", "-U", "--", "/x/a b.pkg.tar.zst"]
    ref = FileInfo(path="/x/app.flatpakref", name="app.flatpakref", stem="app", ext="flatpakref", category="package")
    assert system.install_command(ref, "/x/app.flatpakref")[:3] == ["flatpak", "install", "--user"]
    deb = FileInfo(path="/x/a.deb", name="a.deb", stem="a", ext="deb", category="package")
    assert system.install_command(deb, "/x/a.deb") is None


def test_unknown_actions(runner, tmp_path):
    blob = tmp_path / "blob.dat"
    blob.write_bytes(os.urandom(2000) + b"\x00secret marker text\x00" + os.urandom(50))
    res = ok(run(runner, "unknown.identify", [blob]))
    keys = {row["key"] for row in res["info"]}
    assert "Entropy" in keys and "First bytes" in keys
    res = ok(run(runner, "unknown.hex", [blob]))
    assert res["text"].startswith("00000000  ")
    out = Path(ok(run(runner, "unknown.strings", [blob]))["outputs"][0]["path"])
    assert out.name == "blob (Strings).txt" and "secret marker text" in out.read_text(encoding="utf-8")


def test_disk_info_action(runner, tmp_path):
    img = tmp_path / "disk.img"
    data = bytearray(4096)
    data[510:512] = b"\x55\xaa"
    img.write_bytes(bytes(data))
    res = ok(run(runner, "disk.info", [img]))
    values = {row["key"]: row["value"] for row in res["info"]}
    assert values["Format"] == "RAW" and values["Partition table"] == "MBR"


# ======================================================================= custom

def test_render_shell_contexts():
    from dropwheel.actions import custom

    values = {"file": ["/tmp/it's a file.txt"], "files": ["/a b", "/c"], "dir": ["/tmp"], "stem": ["x"],
              "ext": ["txt"], "name": ["n"], "out": ["/o"]}
    file = "${DROPWHEEL_FILE}"
    assert custom.render_shell("cat {file}", values) == f'cat "{file}"'
    assert custom.render_shell("cat '{file}'", values) == f"cat ''\"{file}\"''"
    assert custom.render_shell('cat "{file}"', values) == f'cat "{file}"'
    assert custom.render_shell('echo "$X {stem}" {files}', values) == \
        'echo "$X ${DROPWHEEL_STEM}" "${DROPWHEEL_FILE_1}" "${DROPWHEEL_FILE_2}"'
    assert custom.render_shell('echo "{files}"', values) == 'echo "${DROPWHEEL_FILE_1} ${DROPWHEEL_FILE_2}"'
    # substitutions start a new command line, also inside double quotes
    assert custom.render_shell('echo "$(du -h {file})"', values) == f'echo "$(du -h "{file}")"'
    assert custom.render_shell('echo "`basename {file}`"', values) == f'echo "`basename "{file}"`"'
    assert custom.render_shell('echo "$(a "$(b {file})" {file}) {file}"', values) == \
        f'echo "$(a "$(b "{file}")" "{file}") {file}"'
    assert custom.render_shell('echo "$(( ({stem} + 1) * 2 )) {stem}"', values) == \
        'echo "$(( (${DROPWHEEL_STEM%%[!0-9]*} + 1) * 2 )) ${DROPWHEEL_STEM}"'
    # no placeholder is read inside comments, an apostrophe there opens nothing
    assert custom.render_shell("# it's {file}\ncat {file}", values) == f"# it's {{file}}\ncat \"{file}\""
    assert custom.render_shell("echo $# {file}", values) == f'echo $# "{file}"'
    # here-documents: expansions without quotes, or the text when nothing expands
    assert custom.render_shell("cat <<EOF\nit's {stem} \"{stem}\"\nEOF\ncat {stem}", values) == \
        'cat <<EOF\nit\'s ${DROPWHEEL_STEM} "${DROPWHEEL_STEM}"\nEOF\ncat "${DROPWHEEL_STEM}"'
    assert custom.render_shell("cat <<'EOF'\nprint(\"{stem}\")\nEOF\ncat {stem}", values) == \
        "cat <<'EOF'\nprint(\"x\")\nEOF\ncat \"${DROPWHEEL_STEM}\""
    assert custom.render_shell("cat <<-'E' <<F\n\tE\n{stem}\nF\n{stem}", values) == \
        "cat <<-'E' <<F\n\tE\n${DROPWHEEL_STEM}\nF\n\"${DROPWHEEL_STEM}\""
    # bash's $'...' has its own escapes
    assert custom.render_shell("echo $'a\\'b' {stem} $'x{stem}y'", values) == \
        "echo $'a\\'b' \"${DROPWHEEL_STEM}\" $'x'\"${DROPWHEEL_STEM}\"$'y'"
    # literal braces and shell variables
    assert custom.render_shell("awk '{{print $1}}' {stem}", values) == "awk '{print $1}' \"${DROPWHEEL_STEM}\""
    assert custom.render_shell('for file in *; do echo "${file}"; done', values) == \
        'for file in *; do echo "${file}"; done'
    assert custom.shell_env(values)["DROPWHEEL_FILE"] == "/tmp/it's a file.txt"
    assert custom.shell_env(values)["DROPWHEEL_FILE_2"] == "/c"
    assert custom.shell_env(values)["DROPWHEEL_OUT"] == "/o"
    argv = custom.render_argv(["cp", "{files}", "{dir}/{stem}-copy.{ext}"], values)
    assert argv == ["cp", "/a b", "/c", "/tmp/x-copy.txt"]


SHELL_NAMES = [
    "with space and 'quote'.txt",
    "x;touch INJECTED;.txt",
    "$(touch INJECTED).txt",
    "`touch INJECTED`.txt",
    "'$(touch INJECTED)'.txt",
    "\\\"$(touch INJECTED)\\\".txt",
    "a\ntouch INJECTED\nb.txt",
    "*?[a].txt",
]
SHELL_TEMPLATES = [
    ("printf %s {file}", True),
    ("printf %s '{file}'", True),
    ('printf %s "{file}"', True),
    ('printf %s "$(printf %s {file})"', True),
    ("""printf %s "$(printf %s '{file}')" """, True),
    ('printf %s "$(printf %s "{file}")"', True),
    ('printf %s "`printf %s {file}`"', True),
    ('printf %s "$(printf %s "$(printf %s {file})")"', True),
    ("x={file}; printf %s \"$x\"", True),
    ('x="$(printf %s {file})"; printf %s "$x"', True),
    ("printf %s $(printf %s {file})", False),
    ("printf %s `printf %s {file}`", False),
    ("(printf %s {file})", True),
    ('printf %s "${{x:-{file}}}"', True),
    ("printf %s ${{x:-{file}}}", True),
    ("# it's a comment\nprintf %s {file}", True),
    ("# no {file} here\nprintf ok", False),
    ("printf %s {file} # don't", True),
    ("cat <<EOF\n{file}\nEOF", False),
    ("cat <<EOF\nit's {file}\nEOF", False),
    ("cat <<'EOF'\n{file}\nEOF", False),
    ('printf %s "$(cat <<EOF\n{file}\nEOF\n)"', False),
    ("printf '%s' \"it's\" {file}", False),
    ("""case {file} in *) printf %s {file};; esac""", True),
    ("""f() {{ printf %s {file}; }}; f""", True),
]


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
@pytest.mark.parametrize("template,exact", SHELL_TEMPLATES, ids=[t[0][:32] for t in SHELL_TEMPLATES])
def test_file_names_never_run_as_commands(tmp_path, template, exact):
    from dropwheel.actions import custom

    for name in SHELL_NAMES:
        values = {"file": [name], "files": [name, "other"], "dir": ["d"], "name": [name], "stem": [name], "ext": ["e"],
                  "out": ["o"]}
        script = custom.render_shell(template, values)
        assert name not in script or "<<'" in template, script      # only a quoted here-document holds the text
        proc = subprocess.run(["sh", "-c", script], cwd=tmp_path, capture_output=True,
                              env={**os.environ, **custom.shell_env(values)})
        assert not (tmp_path / "INJECTED").exists(), (template, name, script)
        if exact:
            assert proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n") == name, (template, name, script)
            assert proc.returncode == 0, (template, name, script, proc.stderr)


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_arithmetic_takes_only_the_digits_of_a_name(tmp_path):
    """bash evaluates the expanded text: $(( $x )) with x='a[$(cmd)]' runs cmd."""
    from dropwheel.actions import custom

    def out(stem):
        script = custom.render_shell('printf %s $(({stem} + 1))', {"stem": [stem]})
        return subprocess.run(["sh", "-c", script], cwd=tmp_path, capture_output=True, text=True,
                              env={**os.environ, **custom.shell_env({"stem": [stem]})}).stdout

    assert out("41") == "42"
    assert out("7-scan") == "8"
    assert out("a[$(touch INJECTED)]") == "1"
    assert not (tmp_path / "INJECTED").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_quoted_heredoc_keeps_the_name_and_cannot_be_closed_by_it(tmp_path):
    from dropwheel.actions import custom

    template = "cat <<'EOF'\n{file}\nEOF\nprintf end"
    run_it = lambda value: subprocess.run(  # noqa: E731
        ["sh", "-c", custom.render_shell(template, {"file": [value]})], cwd=tmp_path, capture_output=True, text=True)
    assert run_it("$(touch INJECTED) a'b").stdout.replace("\r\n", "\n") == "$(touch INJECTED) a'b\nend"
    assert run_it("x\nEOF\ntouch INJECTED\ncat <<'EOF'").stdout.endswith("end")
    assert not (tmp_path / "INJECTED").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_files_placeholder_stays_one_word_per_file_when_nested(tmp_path):
    from dropwheel.actions import custom

    values = {"files": ["a b", "c;d", "$(e)"]}
    env = {**os.environ, **custom.shell_env(values)}

    def out(template):
        res = subprocess.run(["sh", "-c", custom.render_shell(template, values)], cwd=tmp_path, capture_output=True,
                             text=True, env=env)
        return res.stdout

    assert out("printf '[%s]' {files}") == "[a b][c;d][$(e)]"
    assert out("for f in {files}; do printf '[%s]' \"$f\"; done") == "[a b][c;d][$(e)]"
    assert out('printf "%s" "$(set -- {files}; echo $#)"') == "3"
    assert out('printf "[%s]" "{files}"') == "[a b c;d $(e)]"
    assert out("printf '[%s]' '{files}'") == "[a b c;d $(e)]"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_custom_action_with_hostile_name_and_env(runner, tmp_path):
    from dropwheel.actions import custom

    cfg = cfgmod.defaults()
    cfg["custom"] = [
        {"id": "nested", "label": "Nested", "categories": ["*"], "multi": "each",
         "command": 'printf "%s|%s|%s" "$(basename {file})" "$DROPWHEEL_FILE" "$DROPWHEEL_STEM"', "output": "text"},
        {"id": "many", "label": "Many", "categories": ["*"], "multi": "any",
         "command": 'printf "[%s]" {files}; printf "%s" "$DROPWHEEL_FILES" | wc -l', "output": "text"},
    ]
    try:
        custom.reload_custom(cfg)
        name = "foto;touch INJECTED;.jpg" if POSIX else "foto;touch INJECTED.jpg"
        f = tmp_path / name
        f.write_bytes(b"x")
        res = ok(run(runner, "custom.user.nested", [f]))
        assert res["text"] == f"{name}|{f}|{f.stem}"
        assert not (tmp_path / "INJECTED").exists()
        g = tmp_path / "second file.jpg"
        g.write_bytes(b"x")
        res = ok(run(runner, "custom.user.many", [f, g]))
        assert res["text"].split("\n")[0].startswith(f"[{f}][{g}]") and res["text"].split("]")[-1].strip() == "1"
    finally:
        custom.reload_custom({"custom": []})


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_custom_outputs_cannot_be_undone(runner, tmp_path):
    """A file that appears in the folder while the command runs is listed but is never trashed by Undo."""
    from dropwheel.actions import custom

    cfg = cfgmod.defaults()
    cfg["custom"] = [{"id": "busy", "label": "Busy", "categories": ["*"], "multi": "each", "output": "files",
                      "command": "printf x > {dir}/result.bin; printf y > {dir}/download.pdf"}]
    try:
        custom.reload_custom(cfg)
        assert registry.get("custom.user.busy").undoable is False
        f = tmp_path / "video.txt"
        f.write_text("x")
        job = run(runner, "custom.user.busy", [f])
        res = ok(job)
        assert res["undoable"] is False
        assert sorted(Path(o["path"]).name for o in res["outputs"]) == ["download.pdf", "result.bin"]
        assert runner.undo(job.id)["ok"] is False
        assert (tmp_path / "download.pdf").exists() and (tmp_path / "result.bin").exists()
    finally:
        custom.reload_custom({"custom": []})


def test_custom_env_skips_a_files_list_too_long_for_linux(monkeypatch, runner, tmp_path):
    from dropwheel.actions import custom
    from dropwheel.jobs import Context

    cfg = cfgmod.defaults()
    cfg["custom"] = [{"id": "list", "label": "List", "categories": ["*"], "multi": "any", "command": ["echo", "{file}"]},
                     {"id": "text", "label": "Text", "categories": ["*"], "multi": "any", "command": "echo {file}"},
                     {"id": "all", "label": "All", "categories": ["*"], "multi": "any", "command": "echo {files}"}]
    seen: dict[str, dict] = {}

    def fake_run(self, argv, *, env=None, **_kw):
        seen[self.action.id] = env
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(Context, "run", fake_run)
    monkeypatch.setattr(custom, "placeholder_values", lambda _ctx: {
        "file": ["/a"], "files": ["/" + "x" * 200 for _ in range(1000)], "dir": ["/"], "name": ["a"], "stem": ["a"],
        "ext": [""], "out": ["/"]})
    try:
        custom.reload_custom(cfg)
        f = tmp_path / "a.txt"
        f.write_text("x")
        for ident in ("list", "text", "all"):
            ok(run(runner, f"custom.user.{ident}", [f]))
    finally:
        custom.reload_custom({"custom": []})
    for ident in ("list", "text", "all"):
        env = seen[f"custom.user.{ident}"]
        assert "DROPWHEEL_FILES" not in env and env["DROPWHEEL_FILE"] == "/a" and env["DROPWHEEL_OUT"] == "/"
    assert "DROPWHEEL_FILE_1" not in seen["custom.user.list"] and "DROPWHEEL_FILE_1" not in seen["custom.user.text"]
    assert seen["custom.user.all"]["DROPWHEEL_FILE_1000"].startswith("/x")


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_custom_actions_register_and_run(runner, tmp_path):
    from dropwheel.actions import custom

    cfg = cfgmod.defaults()
    cfg["custom"] = [
        {"id": "upper", "label": "Upper", "icon": "text_fields", "categories": ["text"], "multi": "each",
         "command": "tr a-z A-Z < {file}", "output": "text"},
        {"id": "copy-it", "label": "Copy it", "categories": ["text"], "multi": "each",
         "command": "cp {file} {dir}/{stem}-copy.{ext}", "output": "files"},
        {"id": "fail", "label": "Fail", "categories": ["*"], "command": "exit 3"},
    ]
    try:
        ids = custom.reload_custom(cfg)
        assert ids == ["custom.user.upper", "custom.user.copy-it", "custom.user.fail"]
        f = tmp_path / "it's -x.txt"
        f.write_text("hello")
        menu = registry.build_menu([str(f)], cfg, "en")
        node = registry.find_node(menu, "custom.user")
        assert node is not None and node["label"] == "Custom"
        res = ok(run(runner, "custom.user.upper", [f]))
        assert res["text"].strip() == "HELLO"
        res = ok(run(runner, "custom.user.copy-it", [f]))
        assert Path(res["outputs"][0]["path"]).name == "it's -x-copy.txt" and not res["undoable"]
        job = run(runner, "custom.user.fail", [f])
        assert job.state == "failed" and "Fail failed" in job.error["message"]
        cfg["custom"] = cfg["custom"][:1]
        assert custom.reload_custom(cfg) == ["custom.user.upper"]
        assert registry.get("custom.user.copy-it") is None
    finally:
        custom.reload_custom({"custom": []})
    assert custom.registered_ids() == []
