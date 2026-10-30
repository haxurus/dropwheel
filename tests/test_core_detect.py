"""Tests for dropwheel.detect (content sniffing, extension fallback, URIs, volatility)."""

from __future__ import annotations

import io
import json
import struct
import tarfile
import zipfile
from pathlib import Path

import pytest

from dropwheel import util
from dropwheel.detect import CATEGORIES, CATEGORY_ICONS, FileInfo, classify, common_category

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def w(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------------------------- images

def test_png(dropwheel_env):
    p = dropwheel_env.work / "photo.png"
    Image.new("RGB", (32, 20), "red").save(p)
    info = classify(p)
    assert info.category == "image" and info.format == "png" and info.mime == "image/png"
    assert (info.width, info.height) == (32, 20)
    assert info.animated is False
    assert info.exists and info.readable and not info.is_dir
    assert info.stem == "photo" and info.ext == "png" and info.name == "photo.png"
    assert info.icon == "image"
    assert info.uri.startswith("file://")


def test_animated_gif_and_static_gif(dropwheel_env):
    frames = [Image.new("RGB", (10, 10), c) for c in ("red", "green", "blue")]
    anim = dropwheel_env.work / "anim.gif"
    frames[0].save(anim, save_all=True, append_images=frames[1:], duration=100, loop=0)
    static = dropwheel_env.work / "static.gif"
    frames[0].save(static)
    a = classify(anim)
    s = classify(static)
    assert a.category == "image" and a.animated is True and a.format == "gif"
    assert s.category == "image" and s.animated is False
    assert (a.width, a.height) == (10, 10)


def test_apng_detected_as_animated(dropwheel_env):
    frames = [Image.new("RGBA", (8, 8), c) for c in ("red", "blue")]
    p = dropwheel_env.work / "anim.png"
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=50, loop=0)
    info = classify(p)
    assert info.category == "image" and info.animated is True and info.mime == "image/apng"


def test_animated_webp(dropwheel_env):
    from PIL import features

    if not features.check("webp"):
        pytest.skip("Pillow without WebP")
    frames = [Image.new("RGB", (12, 12), c) for c in ("red", "blue")]
    p = dropwheel_env.work / "anim.webp"
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=50, loop=0)
    info = classify(p)
    assert info.category == "image" and info.animated is True and info.format == "webp"
    still = dropwheel_env.work / "still.webp"
    frames[0].save(still)
    assert classify(still).animated is False


def test_jpeg_exif_orientation_swaps_size(dropwheel_env):
    p = dropwheel_env.work / "rot.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (40, 20), "white").save(p, exif=exif)
    info = classify(p)
    assert info.category == "image" and info.format == "jpg"
    assert (info.width, info.height) == (20, 40)


def test_wrong_extension_content_wins(dropwheel_env):
    p = dropwheel_env.work / "really_png.jpg"
    Image.new("RGB", (4, 4)).save(p, format="PNG")
    info = classify(p)
    assert info.category == "image" and info.format == "png" and info.ext == "jpg"


def test_svg_with_size(dropwheel_env):
    p = w(dropwheel_env.work / "logo.svg",
          b'<?xml version="1.0"?>\n<!-- c -->\n<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"></svg>')
    info = classify(p)
    assert info.category == "svg" and (info.width, info.height) == (64, 32)
    q = w(dropwheel_env.work / "noext", b'<svg viewBox="0 0 100 50" xmlns="http://www.w3.org/2000/svg"/>')
    info = classify(q)
    assert info.category == "svg" and (info.width, info.height) == (100, 50)


def test_bmp_ico_psd_heic_avif_jxl_magic(dropwheel_env):
    bmp = dropwheel_env.work / "a.bmp"
    Image.new("RGB", (3, 3)).save(bmp)
    assert classify(bmp).category == "image"
    ico = dropwheel_env.work / "a.ico"
    Image.new("RGBA", (16, 16)).save(ico)
    assert classify(ico).format == "ico"
    assert classify(w(dropwheel_env.work / "x.psd", b"8BPS" + b"\x00" * 60)).format == "psd"
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 40
    assert classify(w(dropwheel_env.work / "x.heic", heic)).format == "heic"
    avif = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf" + b"\x00" * 40
    info = classify(w(dropwheel_env.work / "x.avif", avif))
    assert info.category == "image" and info.format == "avif" and not info.animated
    avis = b"\x00\x00\x00\x1cftypavis\x00\x00\x00\x00avisavifmif1" + b"\x00" * 40
    assert classify(w(dropwheel_env.work / "anim.avif", avis)).animated is True
    assert classify(w(dropwheel_env.work / "x.jxl", b"\xff\x0a" + b"\x01" * 30)).format == "jxl"
    tiff = w(dropwheel_env.work / "cam.dng", b"II*\x00" + b"\x00" * 100)
    assert classify(tiff).category == "image" and classify(tiff).format == "dng"


# ---------------------------------------------------------------------------------------- pdf

def test_pdf_pages(dropwheel_env):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=200, height=200)
    p = dropwheel_env.work / "doc.pdf"
    with open(p, "wb") as fh:
        writer.write(fh)
    info = classify(p)
    assert info.category == "pdf" and info.mime == "application/pdf" and info.pages == 3
    assert info.icon == "picture_as_pdf"
    assert classify(p, deep=False).pages is None


# ------------------------------------------------------------------------------- zip family

def make_zip(path: Path, entries: dict[str, bytes], stored_first: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        if stored_first:
            zf.writestr(zipfile.ZipInfo(stored_first), entries.pop(stored_first), compress_type=zipfile.ZIP_STORED)
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_zip_family(dropwheel_env):
    d = dropwheel_env.work
    plain = make_zip(d / "a.zip", {"x.txt": b"hello"})
    assert (classify(plain).category, classify(plain).format) == ("archive", "zip")
    docx = make_zip(d / "letter.docx", {"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>",
                                         "docProps/app.xml": b"<Properties><Pages>7</Pages></Properties>"})
    info = classify(docx)
    assert (info.category, info.format, info.pages) == ("document", "docx", 7)
    xlsx = make_zip(d / "t.xlsx", {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>"})
    assert classify(xlsx).category == "spreadsheet"
    pptx = make_zip(d / "s.pptx", {"[Content_Types].xml": b"<Types/>", "ppt/presentation.xml": b"<p/>",
                                    "docProps/app.xml": b"<Properties><Slides>5</Slides></Properties>"})
    info = classify(pptx)
    assert info.category == "presentation" and info.pages == 5
    epub = make_zip(d / "book.epub", {"mimetype": b"application/epub+zip", "META-INF/container.xml": b"<c/>"},
                    stored_first="mimetype")
    assert (classify(epub).category, classify(epub).format) == ("ebook", "epub")
    odt = make_zip(d / "x.odt", {"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": b"<c/>",
                                  "meta.xml": b'<m meta:page-count="2"/>'}, stored_first="mimetype")
    info = classify(odt)
    assert (info.category, info.format, info.pages) == ("document", "odt", 2)
    ods = make_zip(d / "x.ods", {"mimetype": b"application/vnd.oasis.opendocument.spreadsheet"}, stored_first="mimetype")
    assert classify(ods).category == "spreadsheet"
    odp = make_zip(d / "x.odp", {"mimetype": b"application/vnd.oasis.opendocument.presentation"},
                   stored_first="mimetype")
    assert classify(odp).category == "presentation"
    cbz = make_zip(d / "comic.cbz", {"001.jpg": b"\xff\xd8\xff"})
    assert classify(cbz).category == "ebook"
    jar = make_zip(d / "app.jar", {"META-INF/MANIFEST.MF": b"Manifest-Version: 1.0", "a/B.class": b"\xca\xfe"})
    assert (classify(jar).category, classify(jar).format) == ("archive", "jar")
    apk = make_zip(d / "app.apk", {"AndroidManifest.xml": b"\x03\x00", "classes.dex": b"dex"})
    assert classify(apk).category == "package"
    fake_docx = make_zip(d / "fake.docx", {"readme.txt": b"not office"})
    assert classify(fake_docx).category == "archive"


def test_zip_metadata_members_are_read_bounded(dropwheel_env):
    """A tiny zip whose 'mimetype' is a bzip2 bomb must not be inflated just to classify it."""
    import tracemalloc

    d = dropwheel_env.work
    bomb = d / "holiday.zip"
    with zipfile.ZipFile(bomb, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_BZIP2
        with zf.open(info, "w", force_zip64=True) as fh:
            chunk = b"\0" * (1 << 20)
            for _ in range(48):
                fh.write(chunk)
        zf.writestr(zipfile.ZipInfo("meta.xml"), b'<m meta:page-count="2"/>')
    assert bomb.stat().st_size < 4096
    tracemalloc.start()
    try:
        got = classify(bomb)
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert (got.category, got.format) == ("archive", "zip")
    assert peak < 8_000_000
    # deflated metadata (what real office suites write) is still read; oversized metadata is skipped
    docx = d / "deflated.docx"
    with zipfile.ZipFile(docx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("word/document.xml", b"<w/>")
        zf.writestr("docProps/app.xml", b"<Properties><Pages>4</Pages></Properties>")
    assert classify(docx).pages == 4
    odt = d / "deflated.odt"
    with zipfile.ZipFile(odt, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
        zf.writestr("meta.xml", b'<m meta:page-count="3"/>' + b" " * 3_000_000)
    info = classify(odt)
    assert (info.category, info.format, info.pages) == ("document", "odt", None)


# ----------------------------------------------------------------------------------- archives

def test_archives_and_multi_ext(dropwheel_env):
    d = dropwheel_env.work
    src = d / "in.txt"
    src.write_text("data")
    tar_path = d / "plain.tar"
    with tarfile.open(tar_path, "w") as tf:
        tf.add(src, arcname="in.txt")
    assert (classify(tar_path).category, classify(tar_path).format) == ("archive", "tar")
    tgz = d / "backup.2024.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(src, arcname="in.txt")
    info = classify(tgz)
    assert info.category == "archive" and info.ext == "tar.gz" and info.stem == "backup.2024"
    assert classify(w(d / "x.7z", b"7z\xbc\xaf\x27\x1c" + b"\x00" * 30)).format == "7z"
    assert classify(w(d / "x.rar", b"Rar!\x1a\x07\x01\x00" + b"\x00" * 30)).format == "rar"
    assert classify(w(d / "x.zst", b"\x28\xb5\x2f\xfd" + b"\x00" * 30)).format == "zst"
    assert classify(w(d / "x.xz", b"\xfd7zXZ\x00" + b"\x00" * 30)).category == "archive"
    assert classify(w(d / "x.bz2", b"BZh9" + b"\x00" * 30)).category == "archive"
    pkg = classify(w(d / "foo-1.0-1-x86_64.pkg.tar.zst", b"\x28\xb5\x2f\xfd" + b"\x00" * 30))
    assert pkg.category == "package" and pkg.ext == "pkg.tar.zst"
    deb = classify(w(d / "x.deb", b"!<arch>\ndebian-binary   " + b"\x00" * 40))
    assert deb.category == "package"
    rpm = classify(w(d / "x.rpm", b"\xed\xab\xee\xdb" + b"\x00" * 40))
    assert rpm.category == "package"


def test_appimage_elf_iso(dropwheel_env):
    d = dropwheel_env.work
    app = w(d / "Tool-x86_64.AppImage", b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 100)
    info = classify(app)
    assert info.category == "appimage" and info.icon == "deployed_code" and info.ext == "appimage"
    elf = w(d / "binary", b"\x7fELF\x02\x01\x01\x00\x00\x00\x00" + b"\x00" * 100)
    assert classify(elf).category == "unknown"
    iso = bytearray(0x8001 + 64)
    iso[0x8000] = 1                      # primary volume descriptor
    iso[0x8001:0x8007] = b"CD001\x01"    # identifier + version
    info = classify(w(d / "distro.iso", bytes(iso)))
    assert info.category == "disk" and info.format == "iso"
    noext = classify(w(d / "image_no_ext", bytes(iso)))
    assert noext.category == "disk"


# -------------------------------------------------------------------------------- fonts/media

def test_fonts(dropwheel_env):
    d = dropwheel_env.work
    otf = b"OTTO" + struct.pack(">HHHH", 10, 128, 3, 32) + b"\x00" * 64
    assert classify(w(d / "f.otf", otf)).category == "font"
    ttf = b"\x00\x01\x00\x00" + struct.pack(">HHHH", 12, 128, 3, 64) + b"\x00" * 64
    assert classify(w(d / "f.ttf", ttf)).format == "ttf"
    assert classify(w(d / "f.woff2", b"wOF2\x00\x01\x00\x00" + b"\x00" * 60)).format == "woff2"
    assert classify(w(d / "f.woff", b"wOFFOTTO" + b"\x00" * 60)).format == "woff"
    # text that merely starts like a magic number stays text
    assert classify(w(d / "caffe.txt", "caffè perché\n".encode("utf-8"))).category == "text"
    assert classify(w(d / "id3notes", b"ID3 tags are metadata\n")).category == "text"
    assert classify(w(d / "mznotes.txt", b"MZ is the DOS header\n" * 10)).category == "text"


def test_audio_video(dropwheel_env):
    d = dropwheel_env.work
    assert classify(w(d / "song.mp3", b"ID3\x04\x00" + b"\x00" * 60)).category == "audio"
    assert classify(w(d / "song.flac", b"fLaC" + b"\x00" * 60)).category == "audio"
    wav = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + b"\x00" * 40
    assert classify(w(d / "a.wav", wav)).category == "audio"
    mp4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 40
    assert classify(w(d / "clip.mp4", mp4)).category == "video"
    m4a = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x00" * 40
    assert classify(w(d / "voice.m4a", m4a)).category == "audio"
    mov = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  " + b"\x00" * 40
    assert classify(w(d / "clip.mov", mov)).format == "mov"
    webm = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm" + b"\x00" * 40
    assert classify(w(d / "v.webm", webm)).mime == "video/webm"
    mkv = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x88matroska" + b"\x00" * 40
    assert classify(w(d / "v.mkv", mkv)).category == "video"
    opus = b"OggS\x00\x02" + b"\x00" * 22 + b"OpusHead" + b"\x00" * 30
    assert classify(w(d / "a.opus", opus)).category == "audio"
    avi = b"RIFF" + struct.pack("<I", 36) + b"AVI LIST" + b"\x00" * 40
    assert classify(w(d / "v.avi", avi)).category == "video"


# ------------------------------------------------------------------------------------- texts

@pytest.mark.parametrize("name,content,category", [
    ("notes.txt", "ciao mondo àèìòù\n", "text"),
    ("README.md", "# Title\n\ntext\n", "markdown"),
    ("table.csv", "a;b;c\n1;2;3\n", "spreadsheet"),
    ("data.tsv", "a\tb\n1\t2\n", "spreadsheet"),
    ("conf.yaml", "a: 1\nb: [1, 2]\n", "data"),
    ("conf.toml", "a = 1\n", "data"),
    ("blob", '{"a": [1, 2, 3]}', "data"),
    ("subs", "1\n00:00:01,000 --> 00:00:02,000\nCiao\n", "subtitle"),
    ("track.vtt", "WEBVTT\n\n00:01.000 --> 00:02.000\nhi\n", "subtitle"),
    ("script", "#!/usr/bin/env python3\nprint('x')\n", "code"),
    ("main.ts", "const x: number = 1;\n", "code"),
    ("page.html", "<!DOCTYPE html><html><body>x</body></html>", "code"),
    ("feed.xml", '<?xml version="1.0"?><root><a/></root>', "data"),
    ("Makefile", "all:\n\techo hi\n", "code"),
    ("story.rtf", "{\\rtf1\\ansi hello}", "document"),
])
def test_text_formats(dropwheel_env, name, content, category):
    p = dropwheel_env.work / name
    p.write_text(content, encoding="utf-8")
    info = classify(p)
    assert info.category == category, (name, info)


def test_shebang_mime_and_text_named_jpg(dropwheel_env):
    p = dropwheel_env.work / "run"
    p.write_text("#!/bin/bash\necho hi\n")
    assert classify(p).mime == "application/x-shellscript"
    fake = dropwheel_env.work / "notreally.jpg"
    fake.write_text("just some text\n")
    assert classify(fake).category == "text"


def test_utf16_and_latin1_text(dropwheel_env):
    p = dropwheel_env.work / "u16.txt"
    p.write_bytes("ciao".encode("utf-16"))
    assert classify(p).category == "text"
    q = dropwheel_env.work / "latin.txt"
    q.write_bytes("caff\xe8 perch\xe9\n".encode("latin-1"))
    assert classify(q).category == "text"


def test_binary_unknown_and_empty(dropwheel_env):
    d = dropwheel_env.work
    blob = bytes(range(256)) * 4
    info = classify(w(d / "blob.bin", blob))
    assert info.category == "unknown" and info.icon == "draft"
    empty = classify(w(d / "empty.png", b""))
    assert empty.category == "unknown" and empty.mime == "application/x-zerosize"
    # binary content but a text extension: unknown, not text
    assert classify(w(d / "weird.txt", blob)).category == "unknown"


# ------------------------------------------------------------------------- paths / URIs / misc

def test_folder_missing_and_link(dropwheel_env):
    d = dropwheel_env.work
    folder = d / "Foto 2024"
    folder.mkdir()
    info = classify(folder)
    assert info.category == "folder" and info.is_dir and info.ext == "" and info.stem == "Foto 2024"
    missing = classify(d / "ghost.pdf")
    assert missing.exists is False and missing.category == "pdf"
    unknown_missing = classify(d / "ghost")
    assert unknown_missing.category == "unknown"


def test_file_uri_percent_decoding(dropwheel_env):
    p = dropwheel_env.work / "my file è.txt"
    p.write_text("x")
    uri = util.path_to_uri(p)
    assert "%20" in uri
    info = classify(uri)
    assert info.exists and Path(info.path) == p and info.name == "my file è.txt"
    info2 = classify(uri + "\r\n")
    assert info2.exists


def test_http_url(dropwheel_env):
    info = classify("https://example.com/files/report%202024.pdf?x=1")
    assert info.category == "url" and info.path == "" and info.name == "report 2024.pdf" and info.ext == "pdf"
    assert info.icon == "link"
    bare = classify("http://example.com")
    assert bare.category == "url" and bare.name == "example.com" and bare.ext == ""


def test_url_page_label_keeps_output_stem(dropwheel_env):
    page = classify("https://www.youtube.com/watch?v=abc")
    assert page.name == "youtube.com/watch?v=abc" and page.stem == "watch" and page.ext == ""
    deep = classify("https://example.com/" + "/".join(["segmento"] * 10) + "/fine")
    assert deep.name == "example.com/\u2026/fine" and deep.stem == "fine"
    cut = classify("https://example.com/" + "x" * 200)
    assert len(cut.name) == 64 and cut.name.endswith("\u2026")
    # a percent-encoded line break stays out of the title
    assert classify("https://example.com/a%0Ab?c=%0Ad").name == "example.com/a b?c= d"
    assert classify("https://example.com/a%0Ab.png").name == "a b.png"


def test_other_scheme_is_unknown(dropwheel_env):
    info = classify("sftp://host.invalid/home/u/file.txt")
    assert info.category == "unknown" and info.exists is False and info.uri.startswith("sftp://")


def test_volatile_detection(dropwheel_env):
    tmpfile = dropwheel_env.volatile / "dragged.png"
    Image.new("RGB", (2, 2)).save(tmpfile)
    assert classify(tmpfile).volatile is True
    normal = dropwheel_env.work / "kept.png"
    Image.new("RGB", (2, 2)).save(normal)
    assert classify(normal).volatile is False
    assert classify(normal).writable_dir is True


def test_never_raises_and_json(dropwheel_env):
    for weird in ("", "\x00bad", "relative/does/not/exist.txt", b"bytes-path.txt", Path("x.jpg")):
        info = classify(weird)
        assert isinstance(info, FileInfo)
        json.dumps(info.to_json())


def test_leading_dash_name(dropwheel_env):
    p = dropwheel_env.work / "-rf important.txt"
    p.write_text("hello")
    info = classify(p)
    assert info.category == "text" and info.name == "-rf important.txt"


def test_tables_consistent():
    assert set(CATEGORY_ICONS) == set(CATEGORIES)
    assert len(CATEGORIES) == 22


def test_common_category(dropwheel_env):
    a = FileInfo(category="image")
    b = FileInfo(category="image")
    c = FileInfo(category="pdf")
    assert common_category([a, b]) == "image"
    assert common_category([a, c]) == "mixed"
    assert common_category([]) == "empty"
