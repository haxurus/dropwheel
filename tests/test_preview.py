"""Tests for dropwheel.preview (SPEC §5.5).  Kinds that need external tools skip without them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from dropwheel import preview, tools
from dropwheel.errors import MissingTool, ToolError, UserError

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _jpeg_with_orientation(path: Path, size=(200, 100), orientation=6) -> Path:
    im = Image.new("RGB", size, (200, 50, 50))
    for x in range(size[0] // 2):
        for y in range(size[1]):
            im.putpixel((x, y), (20, 200, 20))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "DropwheelCam"  # Make
    im.save(path, "JPEG", exif=exif.tobytes(), quality=90)
    return path


def _png(path: Path, size=(300, 150), mode="RGBA") -> Path:
    Image.new(mode, size, (10, 20, 30, 128) if mode == "RGBA" else (10, 20, 30)).save(path)
    return path


def _pdf(path: Path, pages: int = 3, rotate_last: bool = False) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72 + 20 * i), f"Invoice number {1000 + i} for Dropwheel", fontsize=12)
        if rotate_last and i == pages - 1:
            page.set_rotation(90)
    doc.save(str(path))
    doc.close()
    return path


# ================================================================================ thumbnails

def test_thumb_png(dropwheel_env):
    src = _png(dropwheel_env.work / "a b.png")
    res = preview.thumb(str(src), 64)
    assert res is not None
    assert (res["width"], res["height"]) == (64, 32)
    assert Path(res["png"]).is_file()
    assert Path(res["png"]).is_relative_to(preview.cache_root())
    with Image.open(res["png"]) as im:
        assert im.mode == "RGBA"  # transparency kept


def test_thumb_cache_hit_and_invalidation(dropwheel_env):
    src = _png(dropwheel_env.work / "c.png")
    first = preview.thumb(str(src), 64)
    second = preview.thumb(str(src), 64)
    assert first == second
    other_size = preview.thumb(str(src), 32)
    assert other_size["png"] != first["png"]
    # a changed file never returns the stale preview
    _png(src, size=(100, 100))
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    third = preview.thumb(str(src), 64)
    assert third["png"] != first["png"]
    assert (third["width"], third["height"]) == (64, 64)


def test_thumb_never_upscales(dropwheel_env):
    src = _png(dropwheel_env.work / "tiny.png", size=(20, 10))
    res = preview.thumb(str(src), 256)
    assert (res["width"], res["height"]) == (20, 10)


def test_thumb_exif_transpose(dropwheel_env):
    src = _jpeg_with_orientation(dropwheel_env.work / "rot.jpg")
    res = preview.thumb(str(src), 100)
    assert (res["width"], res["height"]) == (50, 100)  # 200x100 rotated by 90°


def test_thumb_path_contract(dropwheel_env):
    from dropwheel.detect import classify

    src = _png(dropwheel_env.work / "p.png")
    path = preview.thumb_path(classify(str(src)))
    assert path and os.path.isfile(path)
    assert preview.thumb_path(str(src)) == path
    assert preview.thumb_path(Path(src)) == path
    txt = dropwheel_env.work / "t.txt"
    txt.write_text("hello", encoding="utf-8")
    assert preview.thumb_path(str(txt)) is None
    assert preview.thumb_path(str(dropwheel_env.work / "missing.png")) is None
    assert preview.thumb_path(str(dropwheel_env.work)) is None
    broken = dropwheel_env.work / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20)
    assert preview.thumb_path(str(broken)) is None  # never raises
    empty = dropwheel_env.work / "empty.jpg"
    empty.write_bytes(b"")
    assert preview.thumb_path(str(empty)) is None


def test_thumb_other_files_is_none(dropwheel_env):
    zf = dropwheel_env.work / "a.zip"
    import zipfile

    with zipfile.ZipFile(zf, "w") as z:
        z.writestr("x.txt", "x")
    assert preview.thumb(str(zf)) is None


# ===================================================================================== image

def test_image_reports_original_size(dropwheel_env):
    src = _jpeg_with_orientation(dropwheel_env.work / "big.jpg", size=(400, 200))
    res = preview.image(str(src), 100)
    assert (res["width"], res["height"]) == (200, 400)  # original, EXIF-rotated
    assert res["scale"] == pytest.approx(0.25, abs=0.01)
    with Image.open(res["png"]) as im:
        assert im.size == (50, 100)


def test_image_rejects_non_images(dropwheel_env):
    txt = dropwheel_env.work / "x.txt"
    txt.write_text("x", encoding="utf-8")
    with pytest.raises(UserError) as exc:
        preview.image(str(txt))
    assert exc.value.message == "preview.err.not_image"


def _embedded_jpeg(size=(1200, 800), exif_orientation=None) -> bytes:
    """A camera's embedded preview: left half green, right half red."""
    import io
    import random

    rng = random.Random(1)
    im = Image.effect_noise(size, 40).convert("RGB")
    im.paste((20, 200, 20), (0, 0, size[0] // 2, size[1]))
    im.paste((200, 30, 30), (size[0] // 2, 0, size[0], size[1]))
    for _ in range(4000):
        im.putpixel((rng.randrange(size[0]), rng.randrange(size[1])), (rng.randrange(255), 0, 0))
    buf = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        im.save(buf, "JPEG", quality=85, exif=exif.tobytes())
    else:
        im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _raw_file(env, name="shot.nef") -> Path:
    path = env.work / name
    path.write_bytes(b"II*\x00" + b"\x00" * 4096)  # a TIFF container with nothing Pillow can read
    return path


def _raw_exiftool(embedded, props):
    def fake(args, _input):
        if "-b" in args:
            return embedded if "-JpgFromRaw" in args else b""
        return json.dumps([props])

    return fake


def _is_green(pixel):
    return pixel[1] > pixel[0]


def test_raw_preview_applies_orientation_and_full_size(dropwheel_env, monkeypatch):
    # portrait NEF: the decoder (darktable-cli) applies Orientation 8 and returns the full sensor
    raw = _raw_file(dropwheel_env)
    fake = _FakeTools(monkeypatch, have=("exiftool", "darktable-cli", "py:PIL"), outputs={
        "exiftool": _raw_exiftool(_embedded_jpeg(), {"ImageSize": "6000 4000", "Orientation": 8})})
    res = preview.image(str(raw), 300)
    assert (res["width"], res["height"]) == (4000, 6000)
    assert res["scale"] == pytest.approx(200 / 4000, abs=0.001)
    with Image.open(res["png"]) as im:
        assert im.size == (200, 300)
        # 90° counter-clockwise: the left half of the sensor ends up at the bottom
        assert _is_green(im.getpixel((100, 280))) and not _is_green(im.getpixel((100, 20)))
    assert any("-Composite:ImageSize" in c for c in fake.calls)
    th = preview.thumb(str(raw), 64)
    assert (th["width"], th["height"]) == (43, 64)


def test_raw_preview_ignores_the_embedded_jpegs_own_tag(dropwheel_env, monkeypatch):
    # same rule as the action's decoder: only the RAW's Orientation counts
    raw = _raw_file(dropwheel_env, "shot.arw")
    _FakeTools(monkeypatch, have=("exiftool", "magick", "py:PIL"), outputs={
        "exiftool": _raw_exiftool(_embedded_jpeg((1616, 1080), exif_orientation=6),
                                  {"ImageSize": "6000x4000", "Orientation": 1})})
    res = preview.image(str(raw), 800)
    assert (res["width"], res["height"]) == (6000, 4000)
    assert (res["png_width"], res["png_height"]) == (800, 535)


def test_raw_preview_size_when_the_preview_is_the_only_decoder(dropwheel_env, monkeypatch):
    # without darktable-cli and ImageMagick the action decodes this very preview
    raw = _raw_file(dropwheel_env)
    _FakeTools(monkeypatch, have=("exiftool", "py:PIL"), outputs={
        "exiftool": _raw_exiftool(_embedded_jpeg(), {"ImageSize": "6000 4000", "Orientation": 6})})
    res = preview.image(str(raw), 400)
    assert (res["width"], res["height"]) == (800, 1200)


def test_raw_cropped_in_camera_does_not_use_the_preview(dropwheel_env, monkeypatch):
    # a 16:9 embedded preview of a 3:2 sensor is not the decoded frame: rectangles would land elsewhere
    raw = _raw_file(dropwheel_env)

    def magick(args, _input):
        if args[1] == "identify":
            return "6000 4000"
        Image.new("RGB", (300, 200)).save(args[-1].removeprefix("png:"), "PNG")
        return b""

    _FakeTools(monkeypatch, have=("exiftool", "magick", "py:PIL"), outputs={
        "magick": magick, "exiftool": _raw_exiftool(_embedded_jpeg((1600, 900)), {"ImageSize": "6000 4000"})})
    res = preview.image(str(raw), 300)
    assert (res["width"], res["height"]) == (6000, 4000) and res["png_height"] == 200


def test_raw_without_exiftool_is_never_sized_by_its_thumbnail(dropwheel_env, monkeypatch):
    raw = dropwheel_env.work / "shot.dng"
    Image.new("RGB", (160, 120), (1, 2, 3)).save(raw, "TIFF")  # what Pillow sees in a TIFF-based RAW
    _FakeTools(monkeypatch, have=("py:PIL",))
    assert preview.thumb(str(raw), 64)["width"] == 64
    with pytest.raises(MissingTool) as exc:
        preview.image(str(raw))
    assert "exiftool" in exc.value.install


def test_raw_through_magick_follows_the_rendered_orientation(dropwheel_env, monkeypatch):
    # ImageMagick reports the stored size, while its decode comes out auto-oriented
    raw = dropwheel_env.work / "shot.dng"
    raw.write_bytes(b"II*\x00" + b"\x00" * 64)

    def magick(args, _input):
        if args[1] == "identify":
            return "6000 4000"
        Image.new("RGB", (200, 300)).save(args[-1].removeprefix("png:"), "PNG")
        return b""

    _FakeTools(monkeypatch, have=("magick", "py:PIL"), outputs={"magick": magick})
    res = preview.image(str(raw), 300)
    assert (res["width"], res["height"]) == (4000, 6000)


def test_heic_without_plugin_reports_the_real_size(dropwheel_env, monkeypatch):
    heic = dropwheel_env.work / "photo.heic"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)

    def magick(args, _input):
        if args[1] == "identify":
            assert args[-1].endswith("photo.heic[0]")
            return "4032 3024"
        Image.new("RGB", (1600, 1200)).save(args[-1].removeprefix("png:"), "PNG")
        return b""

    _FakeTools(monkeypatch, have=("magick", "py:PIL"), outputs={"magick": magick})
    res = preview.image(str(heic))
    assert (res["width"], res["height"]) == (4032, 3024)  # not the 1600 px PNG
    assert res["scale"] == pytest.approx(1600 / 4032, abs=0.001)


def test_heic_through_vips_reports_the_real_size(dropwheel_env, monkeypatch):
    jxl = dropwheel_env.work / "photo.jxl"
    jxl.write_bytes(b"\xff\x0a" + b"\x00" * 64)

    def vips(args, _input):
        Image.new("RGB", (800, 600)).save(args[3], "PNG")
        return b""

    _FakeTools(monkeypatch, have=("vips", "vipsheader", "py:PIL"), outputs={
        "vips": vips, "vipsheader": lambda args, _i: "4000\n" if "width" in args else "3000\n"})
    res = preview.image(str(jxl), 800)
    assert (res["width"], res["height"]) == (4000, 3000)


def test_unknown_size_never_reported_as_the_png_size(dropwheel_env, monkeypatch):
    heic = dropwheel_env.work / "photo.heic"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)

    def magick(args, _input):
        if args[1] == "identify":
            return ToolError("err.tool_failed", tool="magick", code=1)
        Image.new("RGB", (160, 120)).save(args[-1].removeprefix("png:"), "PNG")
        return b""

    _FakeTools(monkeypatch, have=("magick", "py:PIL"), outputs={"magick": magick})
    with pytest.raises(UserError) as exc:
        preview.image(str(heic), 160)
    assert exc.value.message == "preview.err.unreadable"
    # …but the picture is still good as a thumbnail
    assert preview.thumb(str(heic), 160)["width"] == 160


def test_svg_thumb(dropwheel_env):
    if not any(tools.have(t) for t in ("rsvg-convert", "resvg", "magick")):
        pytest.skip("no SVG renderer")
    svg = dropwheel_env.work / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
                   '<rect width="200" height="100" fill="red"/></svg>', encoding="utf-8")
    res = preview.thumb(str(svg), 100)
    assert res and max(res["width"], res["height"]) == 100


# ======================================================================================= PDF

def test_pdf_info_page_search(dropwheel_env):
    pdf = _pdf(dropwheel_env.work / "doc.pdf", pages=3, rotate_last=True)
    info = preview.pdf_info(str(pdf))
    assert info["pages"] == 3
    assert info["sizes"][0] == [612.0, 792.0]
    assert info["sizes"][2] == [792.0, 612.0]  # rotation applied
    assert info["encrypted"] is False and info["needs_password"] is False
    page = preview.pdf_page(str(pdf), 0, 72)
    with Image.open(page["png"]) as im:
        assert im.size == (612, 792)
    assert (page["width_pt"], page["height_pt"]) == (612.0, 792.0)
    res = preview.pdf_search(str(pdf), "INVOICE NUMBER 1001")
    assert [m["page"] for m in res["matches"]] == [1]
    x0, y0, x1, y1 = res["matches"][0]["rects"][0]
    assert 60 < x0 < x1 and y0 < y1 < 140  # top-left origin, near the text baseline at y=92
    assert preview.pdf_search(str(pdf), "   ")["matches"] == []
    # rotated page: rectangles are in the displayed (rotated) space, like pdf_page
    rot = preview.pdf_search(str(pdf), "Invoice number 1002")["matches"]
    assert [m["page"] for m in rot] == [2]
    rx0, ry0, rx1, ry1 = rot[0]["rects"][0]
    assert 0 <= rx0 < rx1 <= 792 and 0 <= ry0 < ry1 <= 612
    assert rx0 > 600  # text near the top-left of the unrotated page ends up on the right
    assert ry1 - ry0 > rx1 - rx0  # the line runs vertically after a 90° rotation
    with pytest.raises(UserError) as exc:
        preview.pdf_page(str(pdf), 7)
    assert exc.value.message == "preview.err.bad_page"


def test_pdf_thumb(dropwheel_env):
    pdf = _pdf(dropwheel_env.work / "t.pdf", pages=1)
    res = preview.thumb(str(pdf), 128)
    assert res and res["height"] == 128 and res["width"] == pytest.approx(99, abs=1)


def test_pdf_encrypted(dropwheel_env):
    pymupdf = pytest.importorskip("pymupdf")
    src = dropwheel_env.work / "secret.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(str(src), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    doc.close()
    info = preview.pdf_info(str(src))
    assert info["needs_password"] is True and info["pages"] == 0
    unlocked = preview.pdf_info(str(src), password="pw")
    assert unlocked["pages"] == 1 and unlocked["needs_password"] is False
    wrong = preview.pdf_info(str(src), password="nope")
    assert wrong["needs_password"] is True and wrong["wrong_password"] is True


def test_pdf_info_pypdf_fallback(dropwheel_env, monkeypatch):
    pytest.importorskip("pypdf")
    pdf = _pdf(dropwheel_env.work / "fallback.pdf", pages=2, rotate_last=True)
    real_have = tools.have
    monkeypatch.setattr(preview, "_have_pymupdf", lambda: False)
    monkeypatch.setattr(preview.tools, "have", lambda name: False if name == "pdfinfo" else real_have(name))
    info = preview.pdf_info(str(pdf))
    assert info["pages"] == 2
    assert info["sizes"] == [[612.0, 792.0], [792.0, 612.0]]


@pytest.fixture
def locked_pdf(dropwheel_env):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("pypdf")
    src = dropwheel_env.work / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page(width=300, height=400).insert_text((72, 72), "secret text")
    doc.save(str(src), encryption=pymupdf.PDF_ENCRYPT_RC4_128, user_pw="s3cret pw", owner_pw="owner")
    doc.close()
    return src


def _poppler_stand_ins(locked):
    """pdfinfo/pdftoppm/pdftotext that, like the real ones, only read what they can open."""
    from pypdf import PdfReader

    seen = {"files": []}

    def opens(path):
        seen["files"].append(path)
        seen["exists_then"] = os.path.exists(path)
        return not PdfReader(path).is_encrypted

    def pdfinfo(args, _input):
        if not opens(args[-1]):
            return subprocess.CompletedProcess(args, 1, b"", b"Command Line Error: Incorrect password\n")
        if "-f" in args:
            return "Page    1 size:      300 x 400 pts\nPage    1 rot:       0\n"
        return "Encrypted:      no\nPages:          1\n"

    def pdftoppm(args, _input):
        assert opens(args[-2])
        Image.new("RGB", (30, 40)).save(args[-1] + ".png", "PNG")
        return b""

    def pdftotext(args, _input):
        assert opens(args[-2])
        return ('<doc><page width="300" height="400"><word xMin="72" yMin="60" xMax="100" yMax="72">secret</word>'
                '</page></doc>')

    return seen, {"pdfinfo": pdfinfo, "pdftoppm": pdftoppm, "pdftotext": pdftotext}


@pytest.mark.parametrize("decrypter", ["qpdf", "pypdf"])
def test_pdf_password_never_on_a_command_line(locked_pdf, monkeypatch, decrypter):
    seen, outputs = _poppler_stand_ins(locked_pdf)
    stdin = []

    def qpdf(args, data):
        from pypdf import PdfReader, PdfWriter

        stdin.append(data)
        assert "--password-file=-" in args and "--decrypt" in args
        reader = PdfReader(args[-2])
        assert reader.decrypt(data.decode())
        with open(args[-1], "wb") as fh:
            PdfWriter(clone_from=reader).write(fh)
        return b""

    outputs["qpdf"] = qpdf
    have = {"pdfinfo", "pdftoppm", "pdftotext", "py:pypdf", "py:PIL"} | ({"qpdf"} if decrypter == "qpdf" else set())
    fake = _FakeTools(monkeypatch, have=have, outputs=outputs)
    monkeypatch.setattr(preview, "_have_pymupdf", lambda: False)

    assert preview.pdf_info(str(locked_pdf))["needs_password"] is True
    info = preview.pdf_info(str(locked_pdf), password="s3cret pw")
    assert info["pages"] == 1 and info["needs_password"] is False and info["encrypted"] is True
    assert info["sizes"] == [[300.0, 400.0]]
    page = preview.pdf_page(str(locked_pdf), 0, 72, password="s3cret pw")
    assert page["width_pt"] == 300.0 and os.path.isfile(page["png"])
    found = preview.pdf_search(str(locked_pdf), "secret", password="s3cret pw")
    assert found["count"] == 1

    assert fake.calls and not [c for c in fake.calls if any("s3cret" in a for a in c)]
    assert not [c for c in fake.calls if {"-upw", "-p"} & set(c)]
    if decrypter == "qpdf":
        assert set(stdin) == {b"s3cret pw"}
    # the tools read a decrypted copy, which is gone afterwards
    copies = [p for p in seen["files"] if p != str(locked_pdf)]
    assert copies and not [p for p in copies if os.path.exists(p)]


@pytest.mark.parametrize("decrypter", ["qpdf", "pypdf"])
def test_pdf_wrong_password_through_the_copy(locked_pdf, monkeypatch, decrypter):
    _seen, outputs = _poppler_stand_ins(locked_pdf)
    outputs["qpdf"] = lambda args, _i: ToolError("err.tool_failed", details="invalid password", tool="qpdf", code=2)
    have = {"pdfinfo", "py:pypdf"} | ({"qpdf"} if decrypter == "qpdf" else set())
    _FakeTools(monkeypatch, have=have, outputs=outputs)
    monkeypatch.setattr(preview, "_have_pymupdf", lambda: False)
    wrong = preview.pdf_info(str(locked_pdf), password="nope")
    assert wrong["needs_password"] is True and wrong["wrong_password"] is True


def test_pdf_password_needs_a_decrypter(locked_pdf, monkeypatch):
    _seen, outputs = _poppler_stand_ins(locked_pdf)
    _FakeTools(monkeypatch, have=("pdfinfo",), outputs=outputs)
    monkeypatch.setattr(preview, "_have_pymupdf", lambda: False)
    with pytest.raises(MissingTool):
        preview.pdf_info(str(locked_pdf), password="s3cret pw")


def test_pdf_metadata(dropwheel_env):
    pymupdf = pytest.importorskip("pymupdf")
    src = dropwheel_env.work / "meta.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"title": "Relazione", "author": "Mario"})
    doc.save(str(src))
    doc.close()
    res = preview.metadata(str(src))
    values = {item["key"]: item["value"] for g in res["groups"] for item in g["items"]}
    assert values.get("Title") == "Relazione"
    assert values.get("Author") == "Mario"
    assert res["editable"] is True


# ================================================================================== metadata

def test_image_metadata(dropwheel_env):
    src = _jpeg_with_orientation(dropwheel_env.work / "m.jpg")
    res = preview.metadata(str(src), "en")
    assert res["groups"]
    flat = {(g["name"], item["key"]): item["value"] for g in res["groups"] for item in g["items"]}
    assert any(key == "Make" and value == "DropwheelCam" for (_g, key), value in flat.items())
    for g in res["groups"]:
        for item in g["items"]:
            assert set(item) >= {"key", "value", "editable"}


class _FakeTools:
    """``tools.have`` / ``preview._run`` stand-ins for programs that are not installed here."""

    def __init__(self, monkeypatch, have=(), outputs=None):
        self.have_names = set(have)
        self.outputs = outputs or {}
        self.calls = []
        monkeypatch.setattr(preview.tools, "have", lambda name: name in self.have_names)
        monkeypatch.setattr(preview, "_run", self.run)

    def run(self, argv, *, timeout=None, input=None, ok_codes=(0,)):
        args = [str(a) for a in argv]
        self.calls.append(args)
        out = self.outputs.get(args[0])
        if callable(out):
            out = out(args, input)
        if isinstance(out, BaseException):
            raise out
        if out is None:
            raise MissingTool(args[0])
        if isinstance(out, subprocess.CompletedProcess):
            return out
        return subprocess.CompletedProcess(args, 0, out if isinstance(out, bytes) else out.encode(), b"")


def _exiftool_json(path, tags):
    return json.dumps([{"SourceFile": str(path), **tags}])


def _flat(result):
    return {item["id"]: item for g in result["groups"] for item in g["items"]}


def _fake_mutagen(monkeypatch, tags, type_name="EasyMP3"):
    """A ``mutagen`` module whose ``File()`` returns ``tags`` through the "easy" interface."""
    info = types.SimpleNamespace(length=2.0, bitrate=128000, sample_rate=44100, channels=2)
    audio_type = type(type_name, (), {"tags": tags, "info": info})
    module = types.ModuleType("mutagen")
    module.File = lambda path, easy=False: audio_type()
    monkeypatch.setitem(sys.modules, "mutagen", module)


def _ids_understood(result, category):
    """Every editable id must reach the action as a real tag name (never a group or a colon)."""
    from dropwheel.actions._media_util import normalize_tag_key

    for id_, item in _flat(result).items():
        if item["editable"]:
            assert normalize_tag_key(id_) == normalize_tag_key(item["key"]), (category, id_)


MP3_EXIFTOOL = {"System:FileSize": "4 kB", "File:MIMEType": "audio/mpeg", "MPEG:AudioBitrate": "128 kbps",
                "ID3v2_3:Title": "Old", "ID3v2_3:Artist": "X", "ID3v2_3:Band": "Y", "ID3v2_3:Year": 2001,
                "ID3v2_3:Track": "3/12", "ID3v2_3:EncoderSettings": "LAME", "ID3v1:Title": "Old",
                "ID3v2_3:Picture": "(Binary data 12345 bytes, use -b option to extract)",
                "Composite:Duration": "0:02"}


def test_mp3_tags_editable_with_exiftool_and_mutagen(dropwheel_env, monkeypatch):
    song = dropwheel_env.work / "song.mp3"
    song.write_bytes(b"ID3\x03" + b"\x00" * 64)
    _FakeTools(monkeypatch, have=("exiftool", "py:mutagen"), outputs={"exiftool": _exiftool_json(song, MP3_EXIFTOOL)})
    _fake_mutagen(monkeypatch, {"title": ["Old"], "artist": ["X", "Z"], "albumartist": ["Y"], "date": ["2001"],
                                "tracknumber": ["3/12"]})
    res = preview.metadata(str(song), "en")
    assert res["editable"] is True and res["source"] == "exiftool"
    assert res["groups"][0]["name"] == "Tags"
    flat = _flat(res)
    assert flat["Tag:title"]["value"] == "Old" and flat["Tag:title"]["editable"] is True
    assert flat["Tag:artist"]["value"] == "X; Z"
    # a tag the file does not have yet can be filled in
    assert flat["Tag:album"] == {"key": "album", "value": "", "editable": True, "removable": False, "id": "Tag:album"}
    # exiftool's copies of those tags would not follow the edits, the other rows stay as information
    for gone in ("ID3v2_3:Title", "ID3v1:Title", "ID3v2_3:Band", "ID3v2_3:Year", "ID3v2_3:Track"):
        assert gone not in flat
    assert flat["ID3v2_3:EncoderSettings"]["editable"] is False
    assert flat["MPEG:AudioBitrate"]["value"] == "128 kbps"
    assert flat["Composite:Duration"]["editable"] is False
    # the cover is removed through audio.tags, it cannot be typed
    assert flat["ID3v2_3:Picture"]["editable"] is False and flat["ID3v2_3:Picture"]["removable"] is True
    _ids_understood(res, "audio")


def test_audio_tags_from_ffprobe_without_mutagen(dropwheel_env, monkeypatch):
    # Ogg keeps the comments on the stream; audio.tags then writes with ffmpeg
    ogg = dropwheel_env.work / "song.ogg"
    ogg.write_bytes(b"OggS" + b"\x00" * 64)
    probe = {"format": {"format_name": "ogg", "tags": {"ENCODER": "Lavf"}},
             "streams": [{"codec_type": "audio", "tags": {"TITLE": "Vecchio", "ALBUM_ARTIST": "Y", "encoder": "Lavc"}}]}
    _FakeTools(monkeypatch, have=("exiftool", "ffprobe"), outputs={
        "exiftool": _exiftool_json(ogg, {"Vorbis:Title": "Vecchio", "Vorbis:Vendor": "Lavf"}),
        "ffprobe": json.dumps(probe)})
    res = preview.metadata(str(ogg))
    flat = _flat(res)
    assert flat["Tag:TITLE"]["value"] == "Vecchio" and flat["Tag:TITLE"]["editable"] is True
    assert flat["Tag:ALBUM_ARTIST"]["editable"] is True
    assert "Tag:title" not in flat and "Tag:albumartist" not in flat  # no empty twin of an existing tag
    assert "Tag:artist" in flat and "Tag:ENCODER" not in flat and "Tag:encoder" not in flat
    assert "Vorbis:Title" not in flat and flat["Vorbis:Vendor"]["editable"] is False
    _ids_understood(res, "audio")


def test_mutagen_types_without_easy_keys_use_ffprobe_names(dropwheel_env, monkeypatch):
    wav = dropwheel_env.work / "take.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)
    _FakeTools(monkeypatch, have=("py:mutagen", "ffprobe"),
               outputs={"ffprobe": json.dumps({"format": {"tags": {"title": "Take 1"}}, "streams": []})})
    _fake_mutagen(monkeypatch, {"TIT2": "Take 1"}, type_name="WAVE")
    flat = _flat(preview.metadata(str(wav)))
    assert flat["Tag:title"]["value"] == "Take 1" and "Tag:TIT2" not in flat


def test_mutagen_cover_key_is_not_a_text_field(dropwheel_env, monkeypatch):
    flac = dropwheel_env.work / "song.flac"
    flac.write_bytes(b"fLaC" + b"\x00" * 64)
    _FakeTools(monkeypatch, have=("py:mutagen",))
    _fake_mutagen(monkeypatch, {"metadata_block_picture": ["QUJD" * 1000], "title": ["T"]}, type_name="OggVorbis")
    res = preview.metadata(str(flac))
    cover = _flat(res)["Tag:metadata_block_picture"]
    assert cover["editable"] is False and cover["removable"] is True and len(cover["value"]) < 20
    assert res["source"] == "mutagen" and any(g["name"] == "Audio" for g in res["groups"])


@pytest.mark.parametrize("with_exiftool", [True, False])
def test_mkv_tags_editable(dropwheel_env, monkeypatch, with_exiftool):
    mkv = dropwheel_env.work / "clip.mkv"
    mkv.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    probe = {"format": {"format_long_name": "Matroska / WebM", "duration": "2.0", "format_name": "matroska,webm",
                        "tags": {"title": "Film", "ENCODER": "Lavf", "ARTIST": "Io"}},
             "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "tags": {"DURATION": "00:00:02"}}]}
    outputs = {"ffprobe": json.dumps(probe)}
    if with_exiftool:
        outputs["exiftool"] = _exiftool_json(mkv, {"Matroska:Title": "Film", "Matroska:MuxingApp": "Lavf",
                                                   "Matroska:ImageWidth": 320, "File:MIMEType": "video/x-matroska"})
    _FakeTools(monkeypatch, have=("exiftool", "ffprobe") if with_exiftool else ("ffprobe",), outputs=outputs)
    res = preview.metadata(str(mkv))
    assert res["editable"] is True and res["source"] == ("exiftool" if with_exiftool else "ffprobe")
    flat = _flat(res)
    assert flat["Tag:title"]["value"] == "Film" and flat["Tag:title"]["editable"] is True
    assert flat["Tag:ARTIST"]["editable"] is True and flat["Tag:comment"]["value"] == ""
    assert "Tag:ENCODER" not in flat and "Tag:tracknumber" not in flat
    assert not [i for i in flat.values() if i["editable"] and not i["id"].startswith("Tag:")]
    if with_exiftool:
        assert "Matroska:Title" not in flat and flat["Matroska:MuxingApp"]["editable"] is False
    else:
        assert flat["Format:ENCODER"]["editable"] is False and flat["Stream0:DURATION"]["editable"] is False
    _ids_understood(res, "video")


def test_mp4_rows_are_tags_not_exiftool_writes(dropwheel_env, monkeypatch):
    # video.metadata.edit rewrites with ffmpeg: ItemList:* are not editable next to the same tags
    mp4 = dropwheel_env.work / "clip.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    probe = {"format": {"format_name": "mov,mp4,m4a", "streams": [],
                        "tags": {"major_brand": "mp42", "title": "Film", "com.apple.quicktime.model": "Cam"}}}
    _FakeTools(monkeypatch, have=("exiftool", "ffprobe"), outputs={
        "exiftool": _exiftool_json(mp4, {"ItemList:Title": "Film", "ItemList:Encoder": "Lavf", "Keys:Model": "Cam"}),
        "ffprobe": json.dumps(probe)})
    flat = _flat(preview.metadata(str(mp4)))
    assert flat["Tag:title"]["editable"] is True and "ItemList:Title" not in flat
    assert flat["ItemList:Encoder"]["editable"] is False and flat["Keys:Model"]["editable"] is False
    assert "Tag:major_brand" not in flat
    assert flat["Tag:com.apple.quicktime.model"]["editable"] is False  # a default remux does not write it back


def test_exiftool_groups_of_images_still_editable(dropwheel_env, monkeypatch):
    src = _jpeg_with_orientation(dropwheel_env.work / "cam.jpg")
    _FakeTools(monkeypatch, have=("exiftool",), outputs={
        "exiftool": _exiftool_json(src, {"IFD0:Artist": "Ale", "XMP-dc:Title": "T", "Composite:ImageSize": "1x1"})})
    flat = _flat(preview.metadata(str(src)))
    assert flat["IFD0:Artist"]["editable"] is True and flat["XMP-dc:Title"]["editable"] is True
    assert flat["Composite:ImageSize"]["editable"] is False and not [k for k in flat if k.startswith("Tag:")]


# ====================================================================================== text

def test_text_encodings(dropwheel_env):
    utf8 = dropwheel_env.work / "u.txt"
    utf8.write_bytes("perché è così\r\nseconda riga\n".encode("utf-8"))
    res = preview.text(str(utf8))
    assert res == {"text": "perché è così\nseconda riga\n", "encoding": "utf-8", "truncated": False,
                   "size": utf8.stat().st_size}
    latin = dropwheel_env.work / "l.txt"
    latin.write_bytes("caffè e città, così è".encode("cp1252"))
    res = preview.text(str(latin))
    assert res["text"] == "caffè e città, così è"
    assert res["encoding"].replace("_", "-").lower() in ("cp1252", "windows-1252", "latin-1", "iso8859-1",
                                                         "iso-8859-1", "iso8859-15", "cp1250")
    utf16 = dropwheel_env.work / "w.txt"
    utf16.write_bytes(b"\xff\xfe" + "ciao ☃".encode("utf-16-le"))
    res = preview.text(str(utf16))
    assert res["text"] == "ciao ☃" and res["encoding"] == "utf-16-le"
    bom = dropwheel_env.work / "b.txt"
    bom.write_bytes(b"\xef\xbb\xbfhello")
    assert preview.text(str(bom))["text"] == "hello"


def test_text_truncation_keeps_valid_utf8(dropwheel_env):
    src = dropwheel_env.work / "long.txt"
    src.write_text("è" * 100, encoding="utf-8")  # 200 bytes
    res = preview.text(str(src), 7)  # cuts in the middle of a 2-byte character
    assert res["truncated"] is True
    assert res["text"] == "èèè"


def test_text_binary_rejected(dropwheel_env):
    src = dropwheel_env.work / "bin.dat"
    src.write_bytes(b"\x00\x01\x02binary\x00")
    with pytest.raises(UserError) as exc:
        preview.text(str(src))
    assert exc.value.message == "preview.err.binary"


# ================================================================================ dispatcher

def test_dispatcher_errors(dropwheel_env):
    src = _png(dropwheel_env.work / "d.png")
    with pytest.raises(UserError) as exc:
        preview.preview("nope", {"path": str(src)})
    assert exc.value.message == "preview.err.unknown_kind"
    with pytest.raises(UserError) as exc:
        preview.preview("thumb", {"path": str(dropwheel_env.work / "missing.png")})
    assert exc.value.message == "err.file_not_found"
    with pytest.raises(UserError) as exc:
        preview.preview("thumb", {"path": str(dropwheel_env.work)})
    assert exc.value.message == "preview.err.is_dir"
    with pytest.raises(UserError) as exc:
        preview.preview("thumb", {})
    assert exc.value.message == "preview.err.no_path"
    with pytest.raises(UserError) as exc:
        preview.preview("thumb", {"path": "https://example.com/a.png"})
    assert exc.value.message == "preview.err.not_local"


def test_dispatcher_file_uri(dropwheel_env):
    from dropwheel import util

    src = _png(dropwheel_env.work / "uri file.png")
    res = preview.preview("thumb", {"path": util.path_to_uri(src), "size": 32})
    assert res["width"] == 32


def test_media_kinds_need_ffprobe(dropwheel_env, monkeypatch):
    src = _png(dropwheel_env.work / "v.png")
    real_have = tools.have
    monkeypatch.setattr(preview.tools, "have", lambda name: False if name in ("ffprobe", "ffmpeg") else real_have(name))
    for kind in ("video_info", "video_frames", "audio_waveform"):
        with pytest.raises(MissingTool) as exc:
            preview.preview(kind, {"path": str(src)})
        assert "ffmpeg" in exc.value.install


def test_ffmpeg_color():
    assert preview._ffmpeg_color(None) == "0xffffffff"
    assert preview._ffmpeg_color("#d0bcff") == "0xd0bcffff"
    assert preview._ffmpeg_color("#80d0bcff") == "0xd0bcff80"  # Qt #AARRGGBB
    assert preview._ffmpeg_color("abc") == "0xaabbccff"
    assert preview._ffmpeg_color("white") == "white"
    assert preview._ffmpeg_color("rgb(1,2,3)") == "0xffffffff"


# ================================================================================ video/audio

@pytest.fixture
def media_files(dropwheel_env):
    if not (tools.have("ffmpeg") and tools.have("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")
    video = dropwheel_env.work / "clip.mp4"
    audio = dropwheel_env.work / "tone.ogg"
    ffmpeg = tools.path("ffmpeg")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=25:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-shortest", "-pix_fmt", "yuv420p", str(video)], check=True, timeout=120)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", str(audio)], check=True, timeout=120)
    return video, audio


def test_video_info_frames_thumb(media_files):
    video, _audio = media_files
    info = preview.video_info(str(video))
    assert (info["width"], info["height"]) == (320, 240)
    assert info["duration"] == pytest.approx(2.0, abs=0.2)
    assert info["fps"] == pytest.approx(25, abs=0.1)
    assert info["has_audio"] is True
    frames = preview.video_frames(str(video), count=4, height=48)
    assert len(frames["frames"]) == 4
    assert all(os.path.isfile(f["png"]) for f in frames["frames"])
    assert [f["t"] for f in frames["frames"]] == sorted(f["t"] for f in frames["frames"])
    th = preview.thumb(str(video), 64)
    assert th and max(th["width"], th["height"]) == 64


def test_audio_waveform(media_files):
    _video, audio = media_files
    res = preview.audio_waveform(str(audio), 400, 60, "#d0bcff")
    with Image.open(res["png"]) as im:
        assert im.size == (400, 60)
    assert res["duration"] == pytest.approx(2.0, abs=0.2)
    with pytest.raises(UserError):
        preview.audio_waveform(str(_png(audio.with_suffix(".png"))))


# ===================================================================================== cache

def test_prune_cache(dropwheel_env):
    src = _png(dropwheel_env.work / "z.png")
    res = preview.thumb(str(src), 64)
    png = Path(res["png"])
    old = time.time() - 40 * 86400
    for f in png.parent.iterdir():
        os.utime(f, (old, old))
    removed = preview.prune_cache()
    assert removed >= 2  # the png and its json
    assert not png.exists()
    # the next request recomputes it
    again = preview.thumb(str(src), 64)
    assert Path(again["png"]).is_file()


def test_prune_cache_size_limit(dropwheel_env):
    for i in range(3):
        preview.thumb(str(_png(dropwheel_env.work / f"s{i}.png", size=(200 + i, 100))), 128)
    total = sum(f.stat().st_size for f in preview.cache_root().rglob("*") if f.is_file())
    preview.prune_cache(max_bytes=total // 2)
    left = sum(f.stat().st_size for f in preview.cache_root().rglob("*") if f.is_file())
    assert left <= total // 2


def test_cache_dir_removed_files_are_recomputed(dropwheel_env):
    src = _png(dropwheel_env.work / "r.png")
    first = preview.thumb(str(src), 48)
    shutil.rmtree(preview.cache_root())
    second = preview.thumb(str(src), 48)
    assert Path(second["png"]).is_file() and second["width"] == first["width"]
