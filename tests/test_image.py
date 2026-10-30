"""Tests for dropwheel.actions.image and its helper module _image_ops.

Pure helpers are tested directly; actions run through the real JobRunner (``make_runner``) on
sample files created in the per-test work directory.  Actions that need Linux-only tools
(exiftool, tesseract, zbarimg, rembg, ffmpeg, SVG renderers…) are skipped when the tool is missing,
or checked for a readable "missing tool" failure with the right install hint.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import random
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageOps, PngImagePlugin  # noqa: E402

from dropwheel import config as cfgmod  # noqa: E402
from dropwheel import registry, tools  # noqa: E402
from dropwheel.actions import _image_ops as ops  # noqa: E402
from dropwheel.actions import image as img  # noqa: E402
from dropwheel.detect import classify  # noqa: E402

SVG_RENDERER = any(tools.have(t) for t in ("rsvg-convert", "resvg", "magick", "py:cairosvg", "inkscape"))


# =========================================================================================== helpers

def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def noise(size=(320, 240), seed=1) -> Image.Image:
    rnd = random.Random(seed)
    data = bytes(rnd.randrange(256) for _ in range(size[0] * size[1] * 3))
    return Image.frombytes("RGB", size, data)


def make_jpeg(path: Path, im: Image.Image | None = None, *, orientation: int | None = None, gps: bool = False,
              date: str | None = None, artist: str | None = None, quality: int = 92, comment: bytes | None = None,
              icc: bytes | None = None) -> Path:
    im = im if im is not None else noise()
    ex = Image.Exif()
    if orientation:
        ex[0x0112] = orientation
    if artist:
        ex[0x013B] = artist
    if gps:
        g = ex.get_ifd(0x8825)
        g[1] = "N"
        g[2] = (45.0, 30.0, 12.0)
        g[3] = "E"
        g[4] = (9.0, 11.0, 5.0)
    if date:
        ex.get_ifd(0x8769)[0x9003] = date
    kw = {"quality": quality}
    if orientation or artist or gps or date:
        kw["exif"] = ex.tobytes()           # len(ex) ignores sub-IFDs created with get_ifd()
    if comment:
        kw["comment"] = comment
    if icc:
        kw["icc_profile"] = icc
    im.save(path, "JPEG", **kw)
    return path


def rgba_logo(size=(120, 80)) -> Image.Image:
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    for x in range(size[0] // 4, 3 * size[0] // 4):
        for y in range(size[1] // 4, 3 * size[1] // 4):
            im.putpixel((x, y), (200, 30, 30, 255))
    return im


def make_gif(path: Path, colors=((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)),
             durations=(100, 200, 100, 200), size=(40, 30)) -> Path:
    frames = [Image.new("RGB", size, c) for c in colors]
    frames[0].save(path, "GIF", save_all=True, append_images=frames[1:], duration=list(durations), loop=0)
    return path


def run(make_runner, action_id, files, params=None, config=None, lang="en"):
    runner, _events = make_runner(config=config, lang=lang)
    return runner.run_sync(action_id, [str(f) for f in files], params or {})


def outputs(job) -> list[Path]:
    return [Path(o["path"]) for o in ((job.result or {}).get("outputs") or [])]


def done(job):
    assert job.state == "done", (job.state, job.error)
    return job


def frame_durations(path: Path) -> list[int]:
    with Image.open(path) as im:
        out = []
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            im.load()       # WebP/AVIF refresh info["duration"] only when the frame is decoded
            out.append(int(im.info.get("duration", 0)))
        return out


def dominant(pixel) -> int:
    """Index of the strongest RGB channel."""
    rgb = list(pixel[:3])
    return rgb.index(max(rgb))


@pytest.fixture
def work(dropwheel_env) -> Path:
    return dropwheel_env.work


# ====================================================================================== pure: formats

def test_norm_fmt_and_fmt_of(work):
    assert ops.norm_fmt("JPEG") == "jpg" and ops.norm_fmt(".jfif") == "jpg"
    assert ops.norm_fmt("tif") == "tiff" and ops.norm_fmt("HEIF") == "heic" and ops.norm_fmt("apng") == "png"
    p = make_jpeg(work / "a.jpeg")
    assert ops.fmt_of(classify(str(p))) == "jpg"
    wrong = work / "really-png.jpg"
    Image.new("RGB", (4, 4)).save(wrong, "PNG")
    assert ops.fmt_of(classify(str(wrong))) == "png"


def test_parse_color_variants():
    assert ops.parse_color("#abc") == (170, 187, 204, 255)
    assert ops.parse_color("#11223344") == (17, 34, 51, 68)
    assert ops.parse_color("ff0000") == (255, 0, 0, 255)
    assert ops.parse_color("rgba(1, 2, 3, 0.5)") == (1, 2, 3, 128)
    assert ops.parse_color("white") == (255, 255, 255, 255)
    assert ops.parse_color("nonsense", (1, 2, 3)) == (1, 2, 3, 255)
    assert ops.parse_color([10, 20, 30]) == (10, 20, 30, 255)
    assert ops.hex_color((255, 0, 16)) == "#ff0010" and ops.hex_color((0, 0, 0, 128)) == "#00000080"


# ===================================================================================== pure: geometry

def test_resize_planning():
    assert ops.shrink_long_edge(4000, 3000, 1920) == (1920, 1440)
    assert ops.shrink_long_edge(1000, 800, 1920) is None
    assert ops.scale_pct(400, 300, 50) == (200, 150) and ops.scale_pct(3, 3, 1) == (1, 1)
    fit = ops.plan_resize(4000, 3000, 1000, 1000, "fit")
    assert fit.size == (1000, 750) and fit.crop is None
    fill = ops.plan_resize(4000, 3000, 1080, 1350, "fill")
    assert fill.crop is not None and fill.crop[2] - fill.crop[0] == 1080 and fill.crop[3] - fill.crop[1] == 1350
    assert ops.plan_resize(400, 300, 800, 600, "fit", upscale=False) is None
    assert ops.plan_resize(400, 300, 800, 0, "fit").size == (800, 600)
    assert ops.plan_resize(400, 300, 100, 100, "stretch").size == (100, 100)
    assert ops.parse_ratio("16:9") == pytest.approx(16 / 9) and ops.parse_ratio("auto") is None


def test_rect_parsing_and_clamping():
    assert img.parse_rect({"x": 1, "y": 2, "w": 3, "h": 4}) == {"x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0}
    assert img.parse_rect("10,20,30,40") == {"x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0}
    assert img.parse_rect("300x200+5+6") == {"x": 5.0, "y": 6.0, "w": 300.0, "h": 200.0}
    assert img.parse_rect('{"x": 1, "y": 1, "w": 2, "h": 2}')["w"] == 2.0
    assert img.parse_rect("bogus") is None
    assert ops.clamp_rect({"x": -5, "y": 10, "w": 50, "h": 500}, 100, 100) == (0, 10, 45, 100)
    assert ops.clamp_rect({"x": 200, "y": 0, "w": 5, "h": 5}, 100, 100) is None


def test_d4_composition_matches_pillow():
    base = noise((7, 5), seed=3)
    names = list(ops.D4)
    for a in names:
        for b in names:
            expected = ops.pil_transpose(ops.pil_transpose(base, a), b)
            got = ops.pil_transpose(base, ops.compose_ops(a, b))
            assert got.tobytes() == expected.tobytes(), (a, b)
    assert ops.compose_ops(ops.orientation_op(6), "rot270") == "none"
    assert ops.jpegtran_args("rot90") == ["-rotate", "90"] and ops.jpegtran_args("transverse") == ["-transverse"]


@pytest.mark.parametrize("orientation", range(1, 9))
def test_orientation_ops_match_exif_transpose(orientation):
    raw = noise((6, 4), seed=orientation)
    ex = Image.Exif()
    ex[0x0112] = orientation
    buf = io.BytesIO()
    raw.save(buf, "PNG", exif=ex.tobytes())
    with Image.open(io.BytesIO(buf.getvalue())) as im:
        expected = ImageOps.exif_transpose(im)
        got = ops.pil_transpose(im.convert("RGB"), ops.orientation_op(orientation))
    assert got.tobytes() == expected.convert("RGB").tobytes()


@pytest.mark.parametrize("orientation", [1, 3, 6, 8, 5])
def test_map_rect_to_raw(orientation):
    raw = noise((24, 16), seed=11)
    shown = ops.pil_transpose(raw, ops.orientation_op(orientation))
    box = (2, 3, 9, 13) if shown.size[1] > 13 else (2, 3, 9, 12)
    raw_box = ops.map_rect_to_raw(box, raw.size, orientation)
    piece = ops.pil_transpose(raw.crop(raw_box), ops.orientation_op(orientation))
    assert piece.tobytes() == shown.crop(box).tobytes()


def test_trim_and_alpha_bbox():
    im = Image.new("RGB", (100, 60), (255, 255, 255))
    im.paste((10, 10, 200), (20, 15, 70, 40))
    assert ops.trim_box(im) == (20, 15, 70, 40)
    assert ops.trim_box(Image.new("RGB", (10, 10), (5, 5, 5))) is None
    logo = rgba_logo()
    assert ops.alpha_bbox(logo) == (30, 20, 90, 60)
    assert ops.trim_box(logo) == (30, 20, 90, 60)
    assert ops.alpha_bbox(Image.new("RGB", (4, 4))) is None


# ======================================================================================= pure: edits

def test_adjust_neutral_and_exposure():
    base = noise((40, 30))
    assert ops.adjust_image(base, {}).tobytes() == base.tobytes()
    brighter = ops.adjust_image(Image.new("RGB", (4, 4), (100, 100, 100)), {"exposure": 50})
    assert brighter.getpixel((0, 0))[0] > 100
    warm = ops.adjust_image(Image.new("RGB", (4, 4), (100, 100, 100)), {"temperature": 100})
    r, _g, b = warm.getpixel((0, 0))
    assert r > b
    rgba = ops.adjust_image(rgba_logo(), {"contrast": 30, "vignette": 40, "sharpen": 20})
    assert rgba.mode == "RGBA" and rgba.getpixel((0, 0))[3] == 0


def test_redact_styles():
    base = noise((100, 100))
    out, used = ops.redact_image(base, [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
                                        {"x": 0.5, "y": 0.5, "w": 0.3, "h": 0.3, "style": "pixelate"},
                                        {"x": 2, "y": 2, "w": 1, "h": 1}, "junk"])
    assert used == {"solid", "pixelate"}
    assert out.getpixel((15, 15)) == (0, 0, 0)
    assert out.crop((50, 50, 80, 80)).tobytes() != base.crop((50, 50, 80, 80)).tobytes()
    assert out.getpixel((95, 5)) == base.getpixel((95, 5))
    blurred, used = ops.redact_image(base, [{"x": 0, "y": 0, "w": 0.5, "h": 0.5, "style": "blur",
                                             "color": "#ff0000"}])
    assert used == {"blur"}


def test_watermark_and_background_helpers():
    base = Image.new("RGB", (300, 200), (20, 20, 20))
    marked = ops.watermark_image(base, text="Test", position="br", opacity=100, scale=30)
    assert marked.size == base.size
    assert marked.crop((150, 100, 300, 200)).tobytes() != base.crop((150, 100, 300, 200)).tobytes()
    assert marked.crop((0, 0, 100, 60)).tobytes() == base.crop((0, 0, 100, 60)).tobytes()
    tiled = ops.watermark_image(base, logo=rgba_logo(), tile=True, opacity=80)
    assert tiled.crop((0, 0, 100, 60)).tobytes() != base.crop((0, 0, 100, 60)).tobytes()
    bg = ops.compose_background(base, {"padding": 20, "radius": 12, "shadow": 10, "ratio": "1:1",
                                       "fill": {"type": "gradient", "colors": ["#ff0000", "#0000ff"], "angle": 90}})
    assert bg.size == (340, 340) and bg.mode == "RGBA"
    assert dominant(bg.getpixel((1, 170))) == 0 and dominant(bg.getpixel((338, 170))) == 2
    blur = ops.compose_background(noise((100, 50)), {"padding": 10, "fill": {"type": "blur"}})
    assert blur.size == (120, 70) and blur.getchannel("A").getextrema() == (255, 255)


def test_white_to_transparent_keeps_enclosed_white():
    im = Image.new("RGB", (60, 60), (255, 255, 255))
    im.paste((0, 0, 0), (10, 10, 50, 50))
    im.paste((255, 255, 255), (25, 25, 35, 35))      # white hole inside the black square
    out = ops.white_to_transparent(im, 0.15, edges_only=True)
    assert out.getpixel((2, 2))[3] == 0
    assert out.getpixel((30, 30))[3] == 255
    everywhere = ops.white_to_transparent(im, 0.15, edges_only=False)
    assert everywhere.getpixel((30, 30))[3] == 0


def test_collage_layouts():
    tiles = [noise((80, 60)), rgba_logo(), noise((50, 100))]
    grid = ops.make_collage(tiles, cell=50, spacing=5, labels=["a.png", "b" * 80, "c.png"])
    assert grid.size[0] == 2 * 50 + 3 * 5
    row = ops.make_collage(tiles, layout="row", cell=40, spacing=0)
    assert row.size[1] == 40
    col = ops.make_collage(tiles, layout="column", cell=40, spacing=2)
    assert col.size[0] == 44


def test_retime_preserves_total_and_minimum():
    plan = ops.retime([100, 200, 100, 200], 2.0, quantum=10)
    assert [d for _, d in plan] == [50, 100, 50, 100]
    fast = ops.retime([20] * 20, 4.0, min_ms=20, quantum=10)
    assert sum(d for _, d in fast) == 100 and all(d >= 20 for _, d in fast)
    slow = ops.retime([40, 40], 0.5)
    assert slow == [(0, 80), (1, 80)]
    with pytest.raises(ValueError):
        ops.retime([10], 0)


# ===================================================================================== pure: metadata

def _jpeg_scan(data: bytes) -> bytes:
    return data[data.index(b"\xff\xda"):]


def test_jpeg_rewrite_lossless_gps_and_all(work):
    p = make_jpeg(work / "gps.jpg", orientation=6, gps=True, date="2024:05:01 14:32:10", comment=b"secret")
    data = p.read_bytes()
    exif, _xmp = ops.read_jpeg_exif_xmp(data)
    no_gps = ops.jpeg_rewrite(data, exif=ops.strip_gps_exif(exif))
    assert _jpeg_scan(no_gps) == _jpeg_scan(data)
    with Image.open(io.BytesIO(no_gps)) as im:
        ex = im.getexif()
        assert ex.get_ifd(0x8825) == {}
        assert ex.get(0x0112) == 6
        assert ex.get_ifd(0x8769).get(0x9003) == "2024:05:01 14:32:10"
    stripped = ops.jpeg_rewrite(data, exif=None, xmp=None, drop_iptc=True, drop_comments=True, drop_other_app=True,
                                drop_trailer=True)
    assert b"secret" not in stripped and b"Exif\x00\x00" not in stripped
    assert _jpeg_scan(stripped) == _jpeg_scan(data)
    added = ops.jpeg_rewrite(stripped, exif=_orientation_exif(3))
    with Image.open(io.BytesIO(added)) as im:
        assert im.getexif().get(0x0112) == 3


def _orientation_exif(value: int) -> bytes:
    ex = Image.Exif()
    ex[0x0112] = value
    return ex.tobytes()


def test_png_rewrite(work):
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Mario Rossi")
    info.add_itxt("XML:com.adobe.xmp", '<x:xmpmeta><rdf:Description exif:GPSLatitude="45,1N"/></x:xmpmeta>')
    p = work / "meta.png"
    noise((20, 10)).save(p, "PNG", pnginfo=info, exif=_orientation_exif(1))
    data = p.read_bytes()
    exif, xmp = ops.read_png_exif_xmp(data)
    assert xmp and b"GPSLatitude" in xmp
    cleaned = ops.png_rewrite(data, exif=None, xmp=None, drop_text=True, drop_time=True)
    assert b"Mario Rossi" not in cleaned and b"GPSLatitude" not in cleaned
    with Image.open(io.BytesIO(cleaned)) as a, Image.open(p) as b:
        assert a.convert("RGB").tobytes() == b.convert("RGB").tobytes()
    gps_free = ops.png_rewrite(data, xmp=ops.xmp_strip_gps(xmp))
    assert b"GPSLatitude" not in gps_free and b"Mario Rossi" in gps_free


def test_exif_dates_and_gps_helpers(work):
    assert ops.parse_exif_datetime("2024:05:01 14:32:10").isoformat() == "2024-05-01T14:32:10"
    assert ops.parse_exif_datetime("2024-05-01T14:32:10.123+02:00").minute == 32
    assert ops.parse_exif_datetime("0000:00:00 00:00:00") is None and ops.parse_exif_datetime(None) is None
    p = make_jpeg(work / "d.jpg", date="2023:12:24 20:15:00", gps=True)
    assert ops.exif_datetime(p).year == 2023
    with Image.open(p) as im:
        raw = im.getexif().tobytes()
    cleaned = ops.strip_gps_exif(raw)
    ex = Image.Exif()
    ex.load(cleaned)
    assert 0x8825 not in ex
    assert ops.xmp_strip_gps(b'<a exif:GPSLongitude="1" b="2"><exif:GPSAltitude>3</exif:GPSAltitude></a>') == \
        b'<a b="2"></a>'


def test_misc_parsers():
    langs = ops.parse_tesseract_langs('List of available languages in "/usr/share/tessdata/" (3):\neng\nita\nosd\n')
    assert langs == {"eng", "ita", "osd"}
    assert ops.pick_ocr_languages("ita+eng", langs) == (["ita", "eng"], [])
    assert ops.pick_ocr_languages("deu+fra", {"eng"}) == (["eng"], ["deu", "fra"])
    assert ops.pick_ocr_languages("deu", {"spa", "osd"}) == (["spa"], ["deu"])
    xml = ("<barcodes xmlns='http://zbar.sourceforge.net/2008/barcode'><source href='x.png'><index num='0'>"
           "<symbol type='QR-Code' quality='1'><data><![CDATA[https://example.org/a?b=1]]></data></symbol>"
           "<symbol type='EAN-13' quality='1'><data format='base64' length='4'><![CDATA[dGVzdA==]]></data></symbol>"
           "</index></source></barcodes>")
    assert ops.parse_zbar_xml(xml) == [("QR-Code", "https://example.org/a?b=1"), ("EAN-13", "test")]
    assert ops.find_urls("vai su www.esempio.it, poi https://a.b/c).") == ["https://www.esempio.it", "https://a.b/c"]


def test_svg_helpers(work):
    text = ('<?xml version="1.0"?>\n<!-- comment -->\n<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="10mm" height="5mm" '
            'viewBox="0 0 100 50" inkscape:export-filename="/home/me/x.png">\n  <metadata>rdf</metadata>\n'
            '  <text x="1" y="2">Ciao  <tspan>mondo</tspan>\n    <tspan>!</tspan></text>\n</svg>\n')
    assert ops.svg_intrinsic_size(text) == pytest.approx((37.795, 18.898), rel=1e-3)
    assert ops.svg_intrinsic_size('<svg viewBox="0 0 24 12"/>') == (24.0, 12.0)
    assert ops.svg_intrinsic_size('<svg width="100%">') is None
    small = ops.svg_minify(text)
    assert "inkscape" not in small and "<metadata" not in small and "comment" not in small
    assert "Ciao  <tspan>mondo</tspan>\n    <tspan>!</tspan>" in small and 'viewBox="0 0 100 50"' in small
    z = work / "a.svgz"
    z.write_bytes(gzip.compress(text.encode("utf-8")))
    assert ops.svg_text(z) == text and ops.is_svgz(z.read_bytes())


# ======================================================================================= pure: saving

def test_save_image_formats(work):
    logo = rgba_logo()
    warns = ops.save_image(logo, work / "l.jpg", "jpg", quality=80)
    assert warns == ["warn.transparency_flattened"]
    with Image.open(work / "l.jpg") as im:
        assert im.mode == "RGB" and im.getpixel((2, 2))[0] > 240
    assert ops.save_image(Image.new("RGBA", (8, 8), (1, 2, 3, 255)), work / "o.jpg", "jpg") == []
    ops.save_image(logo, work / "l.gif", "gif")
    with Image.open(work / "l.gif") as im:
        assert "transparency" in im.info and im.convert("RGBA").getpixel((0, 0))[3] == 0
    ops.save_image(logo, work / "l.ico", "ico")
    with Image.open(work / "l.ico") as im:
        assert (256, 256) in im.ico.sizes() and (16, 16) in im.ico.sizes()
    sixteen = Image.new("I;16", (8, 8), 40000)
    ops.save_image(sixteen, work / "s.png", "png")
    with Image.open(work / "s.png") as im:
        assert im.mode.startswith("I")
    ops.save_image(sixteen, work / "s.webp", "webp")
    cmyk = Image.new("CMYK", (8, 8), (0, 255, 255, 0))
    ops.save_image(cmyk, work / "c.jpg", "jpg")
    with Image.open(work / "c.jpg") as im:
        assert im.mode == "RGB" and dominant(im.getpixel((1, 1))) == 0
    ops.save_image(noise((30, 20)), work / "n.pdf", "pdf")
    assert (work / "n.pdf").read_bytes().startswith(b"%PDF")
    ops.save_image(logo, work / "lossless.webp", "webp", lossless=True)
    assert ops.webp_is_lossless(work / "lossless.webp")
    ops.save_image(noise((30, 20)), work / "lossy.webp", "webp", quality=50)
    assert not ops.webp_is_lossless(work / "lossy.webp")


def test_prepare_drops_mismatched_icc():
    icc = ops.srgb_icc()
    assert icc and ops.icc_colorspace(icc) == "RGB"
    im, emb, flat = ops.prepare_for(Image.new("L", (4, 4)), "jpg", icc=icc)
    assert emb is None and not flat
    im, emb, _ = ops.prepare_for(Image.new("RGB", (4, 4)), "jpg", icc=icc)
    assert emb == icc
    im, emb, _ = ops.prepare_for(Image.new("RGB", (4, 4)), "bmp", icc=icc)
    assert emb is None


def test_animation_roundtrip(work):
    src = make_gif(work / "a.gif")
    fr = ops.read_frames(src)
    assert len(fr.frames) == 4 and fr.durations == [100, 200, 100, 200] and fr.loop == 0
    ops.save_animation(fr.frames, fr.durations, work / "a.webp", "webp", loop=fr.loop, quality=80)
    with Image.open(work / "a.webp") as im:
        assert im.n_frames == 4
    ops.save_animation(fr.frames, fr.durations, work / "b.gif", "gif", loop=None)
    with Image.open(work / "b.gif") as im:
        assert im.n_frames == 4 and "loop" not in im.info
    ops.save_animation(fr.frames, fr.durations, work / "c.png", "png", loop=0)
    with Image.open(work / "c.png") as im:
        assert getattr(im, "n_frames", 1) == 4


@pytest.mark.parametrize("fmt", ["gif", "webp", "png", "avif"])
def test_read_frames_keeps_each_frame_duration(work, fmt):
    # WebP/AVIF update info["duration"] only in load(): reading it right after seek() used to give
    # every frame the previous frame's delay ([2000, 2000, 50, 50] instead of [2000, 50, 50, 500])
    if fmt == "avif" and not img._can_write("avif"):
        pytest.skip("no AVIF encoder")
    frames = [Image.new("RGB", (16, 12), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))]
    src = work / f"timed.{fmt}"
    frames[0].save(src, ops.FORMATS[fmt].pil, save_all=True, append_images=frames[1:],
                   duration=[2000, 50, 50, 500], loop=0)
    fr = ops.read_frames(src)
    assert fr.durations == [2000, 50, 50, 500]
    assert [dominant(f.getpixel((1, 1))) for f in fr.frames[:3]] == [0, 1, 2]


# ============================================================================================== menus

def _top(files, lang="en"):
    menu = registry.build_menu([str(f) for f in files], {}, lang)
    return menu, [n["id"] for n in menu["pages"][0]["items"]]


def _find(nodes, ident):
    for n in nodes:
        if n["id"] == ident:
            return n
        if n["type"] == "group":
            hit = _find(n["items"], ident)
            if hit:
                return hit
    return None


def test_menu_static_image(work):
    p = make_jpeg(work / "foto.jpg")
    menu, ids = _top([p])
    assert ids == ["image.convert", "image.compress", "image.resize", "image.crop.crop", "image.edit",
                   "image.background", "image.privacy", "image.more"]
    convert = menu["pages"][0]["items"][0]
    assert _find([convert], "image.convert.jpg") is None          # never the source format
    assert convert["default"] == "image.convert.png"
    crop = _find(menu["pages"][0]["items"], "image.crop.crop")
    assert crop["mode"] == "dialog" and crop["dialog"] == "crop"
    assert any(prm["name"] == "replace" for prm in crop["params"])
    assert all(len(n.get("items", [])) <= 8 for n in menu["pages"][0]["items"])


def test_menu_animated_svg_multi_mixed(work):
    gif = make_gif(work / "anim.gif")
    _, ids = _top([gif])
    assert ids == ["image.anim.convert", "image.anim.compress", "image.anim.resize", "image.crop.crop", "image.edit",
                   "image.anim.frames", "image.privacy", "image.anim.copy"]
    svg = work / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"/>', encoding="utf-8")
    _, ids = _top([svg])
    assert ids[:3] == ["svg.convert", "svg.optimize", "svg.copy_text"] and len(ids) <= 8
    a, b = make_jpeg(work / "a.jpg"), make_jpeg(work / "b.jpg")
    _, ids = _top([a, b])
    assert ids[0] == "image.combine" and len(ids) <= 8 and "image.convert" in ids
    menu, ids = _top([a, svg])
    assert menu["category"] == "mixed"
    conv = _find(menu["pages"][0]["items"], "image.convert.mixed_png")
    assert conv is not None
    assert _find(menu["pages"][0]["items"], "image.combine.collage") is not None
    assert _find(menu["pages"][0]["items"], "image.combine.pdf") is None


def test_menu_still_plus_animated_shows_each_family_once(make_runner, work):
    still = work / "foto.png"
    noise((40, 30)).save(still)
    gif = make_gif(work / "meme.gif")
    menu, ids = _top([still, gif], lang="it")
    # one Convert / Compress / Resize (the still-image ones), Crop · Privacy · Edit still on the top ring
    assert ids == ["image.combine", "image.convert", "image.compress", "image.resize", "image.crop.crop",
                   "image.privacy", "image.edit", "image.multi.more"]
    top = menu["pages"][0]["items"]
    assert len({n["label"] for n in top}) == len(top)
    assert _find(top, "image.anim.convert") is None and _find(top, "image.anim.compress") is None
    assert _find(top, "image.anim.resize") is None
    # animation-only tools stay in one flat More (no "More" inside "More"), within the ring limit
    more = top[-1]["items"]
    assert "image.anim.frames" in [n["id"] for n in more] and "image.background" in [n["id"] for n in more]
    assert all(n["label"] != top[-1]["label"] for n in more) and len(more) <= 8
    assert _find(top, "image.anim.frames.speed") is not None
    # pure animated selections keep the animated families, and Frames on the top ring
    _, ids = _top([gif, make_gif(work / "other.gif")])
    assert ids == ["image.combine", "image.anim.convert", "image.anim.compress", "image.anim.resize",
                   "image.crop.crop", "image.privacy", "image.edit", "image.anim.frames"]
    # pure still selections keep the still families and never show Frames
    menu, ids = _top([still, make_jpeg(work / "other.jpg")])
    assert ids == ["image.combine", "image.convert", "image.compress", "image.resize", "image.crop.crop",
                   "image.privacy", "image.edit", "image.multi.more"]
    assert _find(menu["pages"][0]["items"], "image.anim.frames") is None
    # the still-image action processes the still image and skips the animation (per-file `when`)
    job = done(run(make_runner, "image.resize.50pct", [still, gif]))
    assert [p.name for p in outputs(job)] == ["foto (50%).png"]
    assert job.warnings
    # an animated action run directly (CLI) on the same selection still takes the animation
    job = done(run(make_runner, "image.anim.resize.50pct", [still, gif]))
    assert [p.suffix for p in outputs(job)] == [".gif"] and job.warnings
    # a still image the still Compress cannot take (e.g. PSD) leaves Compress to the animation
    from types import SimpleNamespace

    psd = SimpleNamespace(category="image", animated=False, format="psd", ext="psd")
    anim = SimpleNamespace(category="image", animated=True, format="gif", ext="gif")
    png = SimpleNamespace(category="image", animated=False, format="png", ext="png")
    still_c, anim_c = registry.get("image.compress.balanced"), registry.get("image.anim.compress.balanced")
    assert not still_c.when_ok([psd, anim]) and anim_c.when_ok([psd, anim])
    assert still_c.when_ok([png, anim]) and not anim_c.when_ok([png, anim])


def test_italian_labels(work):
    p = make_jpeg(work / "foto.jpg")
    menu, _ = _top([p], lang="it")
    compress = _find(menu["pages"][0]["items"], "image.compress.balanced")
    assert compress["label"] == "Bilanciata"
    assert registry.display_label("image.edit.flip_h", "it") == "Rifletti · Orizzontale"
    assert registry.display_label("image.convert.heic", "it") == "Converti · HEIC"


# ============================================================================================ convert

def test_convert_png_with_alpha_to_jpg(make_runner, work):
    src = work / "logo è.png"
    rgba_logo().save(src)
    before = sha(src)
    job = done(run(make_runner, "image.convert.jpg", [src]))
    out = outputs(job)
    assert out == [work / "logo è.jpg"]
    with Image.open(out[0]) as im:
        assert im.mode == "RGB" and im.getpixel((1, 1))[0] > 240
    assert any("white" in w for w in job.warnings)
    assert sha(src) == before


@pytest.mark.parametrize("target,ext", [("png", "png"), ("webp", "webp"), ("tiff", "tiff"), ("gif", "gif"),
                                        ("bmp", "bmp"), ("ico", "ico"), ("pdf", "pdf"), ("avif", "avif")])
def test_convert_targets(make_runner, work, target, ext):
    if target == "avif" and not img._can_write("avif"):
        pytest.skip("no AVIF encoder")
    src = make_jpeg(work / "-strano nome ü.jpg", noise((64, 48)))
    job = done(run(make_runner, f"image.convert.{target}", [src]))
    out = outputs(job)
    assert out == [work / f"-strano nome ü.{ext}"]
    if ext == "pdf":
        assert out[0].read_bytes().startswith(b"%PDF")
    else:
        with Image.open(out[0]) as im:
            assert im.size[0] in (64, 256)


def test_convert_keeps_or_drops_metadata(make_runner, work):
    src = make_jpeg(work / "meta.jpg", artist="Mario", orientation=6)
    job = done(run(make_runner, "image.convert.webp", [src]))
    with Image.open(outputs(job)[0]) as im:
        ex = im.getexif()
        assert ex.get(0x013B) == "Mario" and ex.get(0x0112) in (None, 1)
        assert im.size == (240, 320)                          # orientation applied to the pixels
    cfg = cfgmod.defaults()
    cfg["image"]["keep_metadata"] = False
    job = done(run(make_runner, "image.convert.png", [src], config=cfg))
    with Image.open(outputs(job)[0]) as im:
        assert im.getexif().get(0x013B) is None


def test_convert_missing_plugin_reports_install(make_runner, work):
    if img._can_write("heic"):
        pytest.skip("HEIC encoder available")
    src = make_jpeg(work / "x.jpg")
    job = run(make_runner, "image.convert.heic", [src])
    assert job.state == "failed" and job.error["install"]


# =========================================================================================== compress

def test_compress_balanced_jpeg(make_runner, work):
    src = make_jpeg(work / "big.jpg", noise((400, 300)), quality=97)
    job = done(run(make_runner, "image.compress.balanced", [src]))
    out = outputs(job)
    assert out == [work / "big (Compressed).jpg"]
    assert out[0].stat().st_size < src.stat().st_size
    assert job.result["size_before"] == src.stat().st_size and job.result["delta"] < 0


def test_compress_never_enlarges(make_runner, work):
    src = work / "lossy.webp"
    noise((50, 40)).save(src, "WEBP", quality=40)
    job = done(run(make_runner, "image.compress.lossless", [src]))
    assert outputs(job) == [] and job.result["kind"] == "none"
    assert "already optimized" in job.result["message"]


def test_compress_lossless_png_and_web(make_runner, work):
    src = work / "raw.png"
    Image.new("RGB", (300, 200), (30, 120, 200)).save(src, "PNG", compress_level=0)
    job = done(run(make_runner, "image.compress.lossless", [src]))
    out = outputs(job)[0]
    with Image.open(out) as a, Image.open(src) as b:
        assert a.convert("RGB").tobytes() == b.convert("RGB").tobytes()
    wide = work / "wide.png"
    noise((2500, 60)).save(wide, "PNG")
    job = done(run(make_runner, "image.compress.web", [wide]))
    out = outputs(job)[0]
    assert out.suffix == ".webp"
    with Image.open(out) as im:
        assert im.size[0] == 1920


def test_compress_target_size(make_runner, work):
    src = make_jpeg(work / "target.jpg", noise((500, 400)), quality=95)
    job = done(run(make_runner, "image.compress.target", [src], {"target": "custom", "custom_kb": 40}))
    out = outputs(job)
    assert out and out[0].stat().st_size <= 40_000
    job = done(run(make_runner, "image.compress.target", [src], {"target": 5000}))
    assert outputs(job) == [] and job.result["kind"] == "none"


def test_compress_batch_continues(make_runner, work):
    good = make_jpeg(work / "g.jpg", noise((200, 150)), quality=97)
    small = work / "s.webp"
    noise((20, 20)).save(small, "WEBP", quality=30)
    job = done(run(make_runner, "image.compress.lossless", [good, small]))
    assert job.result is not None


# ============================================================================================= resize

def test_resize_actions(make_runner, work):
    src = work / "r.png"
    noise((400, 300)).save(src)
    job = done(run(make_runner, "image.resize.1920", [src]))
    assert outputs(job) == [] and "400×300" in job.result["message"]
    job = done(run(make_runner, "image.resize.50pct", [src]))
    out = outputs(job)
    assert out == [work / "r (50%).png"]
    with Image.open(out[0]) as im:
        assert im.size == (200, 150)
    job = done(run(make_runner, "image.resize.custom", [src], {"width": 100, "height": 100, "fit": "fill"}))
    with Image.open(outputs(job)[0]) as im:
        assert im.size == (100, 100)
    job = done(run(make_runner, "image.resize.custom", [src], {"width": 50, "unit": "pct"}))
    with Image.open(outputs(job)[0]) as im:
        assert im.size == (200, 150)
    job = done(run(make_runner, "image.resize.social.ig_square", [src]))
    with Image.open(outputs(job)[0]) as im:
        assert im.size == (1080, 1080)
    job = run(make_runner, "image.resize.custom", [src], {"width": 0, "height": 0})
    assert job.state == "failed"


# =============================================================================================== crop

def test_crop_rect_and_per_file(make_runner, work):
    a, b = work / "a.png", work / "b.png"
    noise((200, 100)).save(a)
    noise((120, 90)).save(b)
    job = done(run(make_runner, "image.crop.crop", [a], {"rect": {"x": 10, "y": 10, "w": 50, "h": 40}}))
    out = outputs(job)
    assert out == [work / "a (Cropped).png"]
    with Image.open(out[0]) as im, Image.open(a) as orig:
        assert im.size == (50, 40) and im.tobytes() == orig.crop((10, 10, 60, 50)).tobytes()
    per = {str(a): {"rect": {"x": 0, "y": 0, "w": 20, "h": 20}}, str(b): {"rect": {"x": 5, "y": 5, "w": 30, "h": 10}}}
    job = done(run(make_runner, "image.crop.crop", [a, b], {"rect": {"x": 0, "y": 0, "w": 20, "h": 20},
                                                             "per_file": per}))
    sizes = sorted(Image.open(p).size for p in outputs(job))
    assert sizes == [(20, 20), (30, 10)]
    job = run(make_runner, "image.crop.crop", [a], {"rect": {"x": 500, "y": 0, "w": 10, "h": 10}})
    assert job.state == "failed" and "crop area" in job.error["message"]


def test_crop_jpeg_with_orientation(make_runner, work):
    raw = Image.new("RGB", (64, 32), (255, 0, 0))
    raw.paste((0, 0, 255), (32, 0, 64, 32))
    src = make_jpeg(work / "o.jpg", raw, orientation=6, quality=95)
    # displayed 32×64: top half red, bottom half blue
    job = done(run(make_runner, "image.crop.crop", [src], {"rect": {"x": 0, "y": 32, "w": 32, "h": 32}}))
    with Image.open(outputs(job)[0]) as im:
        shown = ImageOps.exif_transpose(im)
        assert shown.size == (32, 32) and dominant(shown.getpixel((16, 16))) == 2


# =============================================================================================== edit

def test_rotate_jpeg_respects_orientation(make_runner, work):
    raw = Image.new("RGB", (48, 32), (255, 0, 0))
    raw.paste((0, 0, 255), (24, 0, 48, 32))
    src = make_jpeg(work / "rot.jpg", raw, orientation=6, quality=95)
    before = sha(src)
    job = done(run(make_runner, "image.edit.rotate_cw", [src]))
    out = outputs(job)
    assert out == [work / "rot (Rotated).jpg"]
    with Image.open(out[0]) as im:
        assert im.getexif().get(0x0112) in (None, 1)
        shown = ImageOps.exif_transpose(im)
        assert shown.size == (48, 32)
        assert dominant(shown.getpixel((40, 16))) == 0 and dominant(shown.getpixel((6, 16))) == 2
    assert sha(src) == before


def test_flip_grayscale_invert_trim(make_runner, work):
    src = work / "e.png"
    im = Image.new("RGB", (60, 40), (255, 255, 255))
    im.paste((255, 0, 0), (10, 10, 30, 30))
    im.save(src)
    with Image.open(outputs(done(run(make_runner, "image.edit.flip_h", [src])))[0]) as out:
        assert dominant(out.getpixel((40, 20))) == 0 and out.getpixel((15, 20)) == (255, 255, 255)
    with Image.open(outputs(done(run(make_runner, "image.edit.grayscale", [src])))[0]) as out:
        assert out.mode == "L"
    with Image.open(outputs(done(run(make_runner, "image.edit.invert", [src])))[0]) as out:
        assert out.getpixel((0, 0)) == (0, 0, 0)
    with Image.open(outputs(done(run(make_runner, "image.edit.trim", [src])))[0]) as out:
        assert out.size == (20, 20)
    plain = work / "plain.png"
    Image.new("RGB", (10, 10), (9, 9, 9)).save(plain)
    job = done(run(make_runner, "image.edit.trim", [plain]))
    assert outputs(job) == []


def test_adjust_and_watermark(make_runner, work):
    src = work / "adj.png"
    Image.new("RGB", (80, 60), (100, 100, 100)).save(src)
    job = done(run(make_runner, "image.edit.adjust", [src], {"adjust": {"brightness": 50, "contrast": 0}}))
    with Image.open(outputs(job)[0]) as out:
        assert out.getpixel((40, 30))[0] > 120
    job = run(make_runner, "image.edit.adjust", [src], {"adjust": {"brightness": 0}})
    assert job.state == "failed"
    job = done(run(make_runner, "image.edit.watermark", [src], {"text": "© Test", "position": "c", "opacity": 100}))
    out = outputs(job)
    assert out == [work / "adj (Watermarked).png"]
    logo = work / "logo.png"
    rgba_logo().save(logo)
    job = done(run(make_runner, "image.edit.watermark", [src], {"kind": "logo", "logo": str(logo), "tile": True}))
    assert outputs(job)
    job = run(make_runner, "image.edit.watermark", [src], {"kind": "logo", "logo": str(work / "nope.png")})
    assert job.state == "failed"


# ========================================================================================= background

def test_background_actions(make_runner, work):
    logo = work / "logo.png"
    rgba_logo().save(logo)
    job = done(run(make_runner, "image.background.flatten", [logo], {"color": "#00ff00"}))
    with Image.open(outputs(job)[0]) as out:
        assert out.mode == "RGB" and dominant(out.getpixel((1, 1))) == 1
    job = done(run(make_runner, "image.background.crop_subject", [logo]))
    with Image.open(outputs(job)[0]) as out:
        assert out.size == (60, 40)
    white = work / "white.png"
    im = Image.new("RGB", (60, 60), (255, 255, 255))
    im.paste((0, 0, 0), (20, 20, 40, 40))
    im.save(white)
    job = done(run(make_runner, "image.background.white", [white]))
    with Image.open(outputs(job)[0]) as out:
        assert out.mode == "RGBA" and out.getpixel((1, 1))[3] == 0 and out.getpixel((30, 30))[3] == 255
    job = done(run(make_runner, "image.background.add", [white],
                   {"background": {"padding": 20, "radius": 8, "shadow": 6, "ratio": "16:9",
                                   "fill": {"type": "color", "colors": ["#ff8800"]}}}))
    with Image.open(outputs(job)[0]) as out:
        assert out.size == (178, 100)
    job = run(make_runner, "image.background.add", [white], {"background": "nonsense"})
    assert job.state == "failed"
    job = done(run(make_runner, "image.background.flatten", [white]))
    assert outputs(job) == []


def test_background_remove_reports_missing_rembg(make_runner, work):
    if tools.have("rembg") or tools.have("py:rembg"):
        pytest.skip("rembg installed")
    src = make_jpeg(work / "p.jpg")
    job = run(make_runner, "image.background.remove", [src])
    assert job.state == "failed" and "rembg" in job.error["install"]


# ============================================================================================ privacy

def test_strip_location_lossless(make_runner, work):
    src = make_jpeg(work / "gps.jpg", gps=True, date="2024:05:01 14:32:10", artist="Mario")
    before = sha(src)
    job = done(run(make_runner, "image.privacy.strip_gps", [src]))
    out = outputs(job)
    assert out == [work / "gps (No Location).jpg"]
    with Image.open(out[0]) as a, Image.open(src) as b:
        ex = a.getexif()
        assert ex.get_ifd(0x8825) == {} and ex.get(0x013B) == "Mario"
        assert a.tobytes() == b.tobytes()
    assert sha(src) == before
    clean = make_jpeg(work / "clean.jpg")
    job = done(run(make_runner, "image.privacy.strip_gps", [clean]))
    assert outputs(job) == [] and "location" in job.result["message"].lower()


def test_strip_all_keeps_orientation(make_runner, work):
    src = make_jpeg(work / "all.jpg", gps=True, date="2024:05:01 14:32:10", artist="Mario", orientation=6,
                    comment=b"segreto")
    job = done(run(make_runner, "image.privacy.strip_all", [src]))
    out = outputs(job)[0]
    assert out.name == "all (No Metadata).jpg"
    data = out.read_bytes()
    assert b"segreto" not in data and b"Mario" not in data
    with Image.open(out) as im:
        ex = im.getexif()
        assert ex.get(0x0112) == 6 and ex.get_ifd(0x8825) == {} and not ex.get_ifd(0x8769).get(0x9003)
    png = work / "t.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "privato")
    noise((10, 10)).save(png, pnginfo=info)
    job = done(run(make_runner, "image.privacy.strip_all", [png]))
    assert b"privato" not in outputs(job)[0].read_bytes()


def test_redact_strips_metadata(make_runner, work):
    src = make_jpeg(work / "r.jpg", noise((100, 80)), gps=True, artist="Mario")
    job = done(run(make_runner, "image.privacy.redact", [src],
                   {"boxes": [{"page": None, "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3, "style": "solid"},
                              {"page": None, "x": 0.6, "y": 0.6, "w": 0.3, "h": 0.3, "style": "blur"}]}))
    out = outputs(job)[0]
    assert out.name == "r (Redacted).jpg"
    with Image.open(out) as im:
        assert max(im.getpixel((20, 20))) < 40
        assert len(im.getexif()) == 0
    assert any("solid" in w.lower() for w in job.warnings)
    job = run(make_runner, "image.privacy.redact", [src], {"boxes": []})
    assert job.state == "failed"


def test_metadata_args_and_missing_exiftool(make_runner, work):
    args, skipped = img._metadata_args({"set": {"IFD0:Artist": "Anna", "IPTC:Keywords": ["a", "b"],
                                                "File:FileName": "x.jpg", "-o": "y"},
                                        "remove": ["GPS:GPSLatitude", "System:Directory"], "remove_all": True},
                                       raw=False)
    assert args[:5] == ["-all=", "-tagsfromfile", "@", "-icc_profile", "-orientation"]
    assert "-GPS:GPSLatitude=" in args and "-IFD0:Artist=Anna" in args
    assert "-IPTC:Keywords=a" in args and "-IPTC:Keywords=b" in args and "-IPTC:CodedCharacterSet=UTF8" in args
    assert set(skipped) == {"File:FileName", "-o", "System:Directory"}
    if tools.have("exiftool"):
        src = make_jpeg(work / "m.jpg")
        job = done(run(make_runner, "image.privacy.metadata", [src], {"metadata": {"set": {"IFD0:Artist": "Anna"}}}))
        with Image.open(outputs(job)[0]) as im:
            assert im.getexif().get(0x013B) == "Anna"
    else:
        src = make_jpeg(work / "m.jpg")
        job = run(make_runner, "image.privacy.metadata", [src], {"metadata": {"set": {"IFD0:Artist": "Anna"}}})
        assert job.state == "failed" and "perl-image-exiftool" in job.error["install"]


def test_rename_by_date(make_runner, work):
    a = make_jpeg(work / "IMG_1.jpg", date="2024:05:01 14:32:10")
    b = make_jpeg(work / "IMG_2.jpg", date="2024:05:01 14:32:10")
    job = done(run(make_runner, "image.privacy.rename_date", [a, b], {"keep_name": False}))
    names = sorted(p.name for p in outputs(job))
    assert names == ["2024-05-01 14.32.10 2.jpg", "2024-05-01 14.32.10.jpg"]
    assert not a.exists() and not b.exists()
    assert "→" in job.result["text"]


# =============================================================================================== more

def test_upscale_fallback(make_runner, work):
    src = work / "u.png"
    noise((40, 30)).save(src)
    job = done(run(make_runner, "image.upscale.x2", [src]))
    out = outputs(job)
    assert out == [work / "u (×2).png"]
    with Image.open(out[0]) as im:
        assert im.size == (80, 60)
    if not (tools.have("realesrgan") or tools.have("waifu2x")):
        assert job.warnings


def test_icons(make_runner, work):
    src = work / "i.png"
    rgba_logo((300, 200)).save(src)
    job = done(run(make_runner, "image.icon.ico", [src]))
    with Image.open(outputs(job)[0]) as im:
        assert (256, 256) in im.ico.sizes()
    job = done(run(make_runner, "image.icon.png_set", [src]))
    folder = outputs(job)[0]
    assert folder.is_dir() and sorted(p.name for p in folder.iterdir()) == sorted(
        f"icon-{s}.png" for s in ops.PNG_SET_SIZES)
    job = done(run(make_runner, "image.icon.favicon", [src]))
    folder = outputs(job)[0]
    names = {p.name for p in folder.iterdir()}
    assert {"favicon.ico", "favicon-32x32.png", "apple-touch-icon.png", "site.webmanifest",
            "android-chrome-512x512.png", "favicon.html"} <= names
    json.loads((folder / "site.webmanifest").read_text(encoding="utf-8"))


@pytest.mark.skipif(not tools.have("tesseract"), reason="tesseract not installed")
def test_ocr_copy_runs(make_runner, work):
    src = work / "ocr.png"
    from PIL import ImageDraw

    im = Image.new("RGB", (600, 120), "white")
    ImageDraw.Draw(im).text((10, 30), "HELLO WORLD", fill="black", font=ops.label_font(48))
    im.save(src)
    job = done(run(make_runner, "image.ocr.txt", [src]))
    assert job.result["kind"] in ("files", "none")


@pytest.mark.skipif(not tools.have("zbarimg"), reason="zbarimg not installed")
def test_qr_none_found(make_runner, work):
    src = work / "blank.png"
    Image.new("RGB", (100, 100), "white").save(src)
    job = done(run(make_runner, "image.qr", [src]))
    assert job.result["kind"] == "none"


def test_vectorize_requires_tools(make_runner, work):
    src = work / "v.png"
    rgba_logo().save(src)
    if tools.have("potrace"):
        job = done(run(make_runner, "image.vectorize.bw", [src]))
        assert outputs(job)[0].read_text(encoding="utf-8").lstrip().startswith(("<?xml", "<svg"))
    else:
        job = run(make_runner, "image.vectorize.bw", [src])
        assert job.state == "failed" and "potrace" in job.error["install"]


# =========================================================================================== animated

def test_animated_frames_actions(make_runner, work):
    gif = make_gif(work / "anim.gif")
    before = sha(gif)
    job = done(run(make_runner, "image.anim.convert.first_frame", [gif]))
    with Image.open(outputs(job)[0]) as im:
        assert im.format == "PNG" and dominant(im.convert("RGB").getpixel((1, 1))) == 0
    job = done(run(make_runner, "image.anim.frames.extract", [gif]))
    folder = outputs(job)[0]
    assert sorted(p.name for p in folder.iterdir()) == [f"frame-{i:04d}.png" for i in range(1, 5)]
    job = done(run(make_runner, "image.anim.frames.reverse", [gif]))
    rev = outputs(job)[0]
    assert rev.name == "anim (Reversed).gif"
    with Image.open(rev) as im:
        assert im.n_frames == 4
        assert im.convert("RGB").getpixel((1, 1)) == (255, 255, 0)
    assert frame_durations(rev) == [200, 100, 200, 100]
    job = done(run(make_runner, "image.anim.frames.speed", [gif], {"factor": 2.0}))
    sped = outputs(job)[0]
    assert sped.name == "anim (Speed 2x).gif" and sum(frame_durations(sped)) == 300
    assert sha(gif) == before


def test_animated_convert_transform_compress(make_runner, work):
    gif = make_gif(work / "anim.gif")
    job = done(run(make_runner, "image.anim.convert.webp", [gif]))
    webp = outputs(job)[0]
    with Image.open(webp) as im:
        assert im.n_frames == 4
    job = done(run(make_runner, "image.anim.convert.gif", [webp]))
    with Image.open(outputs(job)[0]) as im:
        assert im.n_frames >= 2
    job = done(run(make_runner, "image.edit.rotate_cw", [gif]))
    with Image.open(outputs(job)[0]) as im:
        assert im.size == (30, 40) and im.n_frames == 4
    job = done(run(make_runner, "image.crop.crop", [gif], {"rect": {"x": 0, "y": 0, "w": 20, "h": 10}}))
    with Image.open(outputs(job)[0]) as im:
        assert im.size == (20, 10) and im.n_frames == 4
    job = done(run(make_runner, "image.anim.resize.480", [gif]))
    assert outputs(job) == []
    job = done(run(make_runner, "image.anim.compress.balanced", [gif]))
    assert job.result["kind"] in ("files", "none")


@pytest.mark.skipif(not tools.have("ffmpeg"), reason="ffmpeg not installed")
def test_animated_to_mp4(make_runner, work):
    gif = make_gif(work / "anim.gif")
    job = done(run(make_runner, "image.anim.convert.mp4", [gif]))
    assert outputs(job)[0].suffix == ".mp4" and outputs(job)[0].stat().st_size > 0


# ============================================================================================ combine

def test_combine_pdf(make_runner, work):
    pypdf = pytest.importorskip("pypdf")
    a = make_jpeg(work / "1.jpg", noise((60, 40)))
    b = work / "2.png"
    rgba_logo().save(b)
    c = work / "10.webp"
    noise((30, 30)).save(c, "WEBP")
    job = done(run(make_runner, "image.combine.pdf", [c, b, a]))
    out = outputs(job)
    assert out == [work / "1 (Merged).pdf"]
    assert len(pypdf.PdfReader(str(out[0])).pages) == 3
    job = done(run(make_runner, "image.combine.pdf", [a, b], {"page": "a4", "margin": 10}))
    page = pypdf.PdfReader(str(outputs(job)[0])).pages[0]
    assert round(float(page.mediabox.width)) in (595, 842)


def test_combine_pdf_img2pdf_turns_pages_like_the_image(make_runner, work, monkeypatch):
    # img2pdf (the preferred backend) must put landscape images on landscape A4/Letter pages, like
    # the PyMuPDF/Pillow fallbacks do through _page_box: that is its --auto-orient switch
    import subprocess

    from dropwheel import jobs

    real_have = tools.have
    monkeypatch.setattr(tools, "have", lambda name: True if name == "img2pdf" else real_have(name))
    calls: list[list[str]] = []

    def fake_run(self, argv, **kw):
        argv = [str(a) for a in argv]
        calls.append(argv)
        Path(argv[argv.index("-o") + 1]).write_bytes(b"%PDF-1.4\n%%EOF\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(jobs.Context, "run", fake_run)
    wide = make_jpeg(work / "wide.jpg", noise((60, 40)))
    tall = make_jpeg(work / "tall.jpg", noise((40, 60)))
    done(run(make_runner, "image.combine.pdf", [wide, tall], {"page": "a4", "margin": 5}))
    argv = calls[-1]
    assert argv[0] == "img2pdf" and "--auto-orient" in argv
    assert argv[argv.index("--pagesize") + 1] == "A4" and argv[argv.index("--border") + 1] == "5mm:5mm"
    assert [Path(p).name for p in argv[-2:]] == ["tall.jpg", "wide.jpg"]   # original JPEGs, by name
    done(run(make_runner, "image.combine.pdf", [wide, tall], {"page": "letter", "margin": 0}))
    assert "--auto-orient" in calls[-1] and calls[-1][calls[-1].index("--pagesize") + 1] == "Letter"
    done(run(make_runner, "image.combine.pdf", [wide, tall]))      # page = image size: no page box at all
    assert "--auto-orient" not in calls[-1] and "--pagesize" not in calls[-1]


def test_combine_collage_and_anim(make_runner, work):
    files = []
    for i, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255))):
        p = work / f"c{i}.png"
        Image.new("RGB", (80, 60), color).save(p)
        files.append(p)
    job = done(run(make_runner, "image.combine.collage", files, {"cell": 100, "spacing": 10, "labels": True}))
    out = outputs(job)[0]
    assert out.name == "c0 (Collage).jpg"
    with Image.open(out) as im:
        assert im.size[0] == 2 * 100 + 3 * 10
    job = done(run(make_runner, "image.combine.anim", files, {"format": "gif", "fps": 2, "width": 80}))
    with Image.open(outputs(job)[0]) as im:
        assert im.n_frames == 3 and im.size == (80, 60)
    job = done(run(make_runner, "image.combine.anim", files, {"format": "webp", "fps": 4, "width": 0}))
    assert outputs(job)[0].suffix == ".webp"


@pytest.mark.skipif(not tools.have("ffmpeg"), reason="ffmpeg not installed")
def test_combine_video(make_runner, work):
    files = []
    for i in range(2):
        p = work / f"v{i}.png"
        noise((64, 48), seed=i).save(p)
        files.append(p)
    job = done(run(make_runner, "image.combine.video", files, {"duration": 0.5, "resolution": "1280x720"}))
    assert outputs(job)[0].suffix == ".mp4"


# ================================================================================================ svg

def test_svg_optimize_and_mixed(make_runner, work):
    svg = work / "draw.svg"
    body = "".join(f'  <rect x="{i}" y="{i}" width="10" height="10" fill="#ff0000"/>\n' for i in range(30))
    svg.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!-- Created with Inkscape -->\n'
                   '<svg xmlns="http://www.w3.org/2000/svg" '
                   'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                   'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" width="100" height="100" '
                   'viewBox="0 0 100 100" sodipodi:docname="/home/user/segreto.svg">\n'
                   '  <sodipodi:namedview id="nv" inkscape:zoom="1"/>\n  <metadata>\n    <rdf/>\n  </metadata>\n'
                   + body + '</svg>\n', encoding="utf-8")
    job = done(run(make_runner, "svg.optimize", [svg]))
    out = outputs(job)[0]
    text = out.read_text(encoding="utf-8")
    assert out.name == "draw (Optimized).svg" and "segreto" not in text and "viewBox" in text
    png = work / "pic.png"
    noise((20, 20)).save(png)
    job = run(make_runner, "image.convert.mixed_jpg", [png, svg])
    if SVG_RENDERER:
        done(job)
        assert len(outputs(job)) == 2
    else:
        done(job)
        assert outputs(job) == [work / "pic.jpg"] and job.warnings


@pytest.mark.skipif(not SVG_RENDERER, reason="no SVG renderer installed")
def test_svg_render(make_runner, work):
    svg = work / "s.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" '
                   'fill="red"/></svg>', encoding="utf-8")
    job = done(run(make_runner, "svg.convert.png.512", [svg]))
    with Image.open(outputs(job)[0]) as im:
        assert im.size == (512, 256)
    job = done(run(make_runner, "svg.convert.jpg", [svg], {"size": 512}))
    assert outputs(job)[0].suffix == ".jpg"


# ======================================================================================= robustness

def test_inputs_never_modified_and_replace_original(make_runner, work):
    src = make_jpeg(work / "keep.jpg", noise((120, 90)), quality=97)
    before = sha(src)
    for action_id in ("image.compress.balanced", "image.edit.flip_v", "image.resize.50pct", "image.privacy.strip_all"):
        done(run(make_runner, action_id, [src]))
        assert sha(src) == before
    runner, _ = make_runner()
    job = done(runner.run_sync("image.compress.balanced", [str(src)], {"replace": True}))
    assert src.exists() and sha(src) != before
    assert job.result["undoable"]
    res = runner.undo(job.id)
    assert res.get("ok") and sha(src) == before


def test_unreadable_image_fails_cleanly(make_runner, work):
    bad = work / "broken.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    job = run(make_runner, "image.edit.grayscale", [bad])
    assert job.state == "failed" and job.error["message"]
    assert not list(work.glob("broken (*"))


# ==================================================================================== gifski (F19)

def _fake_gifski(monkeypatch) -> list[list[str]]:
    import subprocess

    from dropwheel import jobs

    real_have = tools.have
    monkeypatch.setattr(tools, "have", lambda name: True if name == "gifski" else real_have(name))
    calls: list[list[str]] = []

    def fake_run(self, argv, **kw):
        argv = [str(a) for a in argv]
        calls.append(argv)
        Path(argv[argv.index("-o") + 1]).write_bytes(b"GIF89a")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(jobs.Context, "run", fake_run)
    return calls


def test_gifski_gets_the_frame_width(make_runner, work, monkeypatch):
    """Without --width gifski halves anything above about 800×600; --height too would stretch."""
    webp = work / "wide.webp"
    frames = [Image.new("RGB", (1280, 720), c) for c in ("red", "blue", "lime")]
    frames[0].save(webp, save_all=True, append_images=frames[1:], duration=100, loop=0)
    portraits = []
    for i, color in enumerate(("red", "blue")):
        p = work / f"p{i}.png"
        Image.new("RGB", (600, 800), color).save(p)
        portraits.append(p)
    calls = _fake_gifski(monkeypatch)
    done(run(make_runner, "image.anim.convert.gif", [webp]))
    argv = calls[-1]
    assert argv[0] == "gifski" and argv[argv.index("--width") + 1] == "1280" and "--height" not in argv
    done(run(make_runner, "image.combine.anim", portraits, {"format": "gif", "fps": 2, "width": 800}))
    argv = calls[-1]
    assert argv[0] == "gifski" and argv[argv.index("--width") + 1] == "800" and "--height" not in argv


# ================================================================================== RAW crop (F13)

@pytest.mark.parametrize(("reference", "expected"), [
    ((2000, 1000), (500, 250)),         # the embedded preview is what got decoded: a quarter of the pixels
    ((1000, 500), (1000, 500)),         # same size: untouched
    (None, (1000, 500)),                # no reference: the rectangle stays as it is
])
def test_crop_of_a_raw_follows_the_decoded_size(make_runner, work, monkeypatch, reference, expected):
    from dropwheel import preview
    from dropwheel.actions import image as image_actions
    from dropwheel.errors import MissingTool

    raw = work / "shot.nef"
    raw.write_bytes(b"II*\0" + b"\0" * 256)
    decoded = work / "decoded.png"
    Image.linear_gradient("L").resize((1000, 500)).convert("RGB").save(decoded)
    monkeypatch.setattr(image_actions, "_decode_cli", lambda ctx, f: decoded)

    def fake_image(path, max=1600):  # noqa: A002 - protocol name
        assert path == str(raw) and max == 1600
        if reference is None:
            raise MissingTool("exiftool")
        return {"width": reference[0], "height": reference[1]}

    monkeypatch.setattr(preview, "image", fake_image)
    job = done(run(make_runner, "image.crop.crop", [raw], {"rect": {"x": 0, "y": 0, "w": 1000, "h": 500}}))
    if expected == (1000, 500):                 # the whole decoded picture: nothing to do
        assert job.result["kind"] == "none"
        return
    with Image.open(outputs(job)[0]) as im:
        assert im.size == expected


@pytest.mark.parametrize("progressive", [False, True])
def test_jpeg_save_survives_detail_beyond_the_pillow_buffer(tmp_path, progressive):
    """High-entropy pictures at 4:4:4 used to fail with 'broken data stream when writing image file'."""
    rng = random.Random(7)
    noise = Image.frombytes("RGB", (500, 250), bytes(rng.randrange(256) for _ in range(500 * 250 * 3)))
    out = tmp_path / "noise.jpg"
    ops.save_image(noise, out, "jpg", quality=95, progressive=progressive)
    with Image.open(out) as back:
        back.load()
        assert back.size == (500, 250)
    data = ops.encode(noise, "jpg", quality=90, progressive=progressive)
    assert data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"
    assert Image.open(io.BytesIO(data)).size == (500, 250)
    from PIL import ImageFile
    assert ImageFile.MAXBLOCK == 65536, "the larger buffer must only last for the retry"
