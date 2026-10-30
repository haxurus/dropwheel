"""Tests for dropwheel.actions.pdf (pure helpers, menu layout, locales and every action that can run
with PyMuPDF / pypdf / Pillow; actions needing Linux-only tools are skipped when the tool is missing)."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from dropwheel import config as cfgmod
from dropwheel import i18n, registry, tools
from dropwheel.actions import pdf as P
from dropwheel.detect import classify

ROOT = Path(__file__).resolve().parent.parent
PDF_SRC = ROOT / "src" / "dropwheel" / "actions" / "pdf.py"
LOCALES = ROOT / "src" / "dropwheel" / "locales"


# ============================================================================ helpers

def _mupdf():
    return pytest.importorskip("pymupdf")


def make_pdf(path: Path, pages: int = 3, *, text: str = "Page {n} secret ABC", size=(595, 842), toc: bool = False,
             meta: dict | None = None, image: bool = False, encrypt: str | None = None, owner: str = "owner-pw",
             rotate: int = 0) -> Path:
    """Write a test PDF with one text line per page."""
    m = _mupdf()
    doc = m.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((72, 100), text.format(n=i + 1), fontsize=14)
        if image:
            page.insert_image(m.Rect(72, 200, 372, 500), stream=_png_bytes(600, 600))
        if rotate:
            page.set_rotation(rotate)
    if toc:
        doc.set_toc([[1, f"Chapter {i + 1}", i + 1] for i in range(pages)])
    if meta:
        doc.set_metadata(meta)
    if encrypt is not None:
        doc.save(str(path), encryption=m.PDF_ENCRYPT_AES_256, user_pw=encrypt, owner_pw=owner,
                 permissions=4 | 16)
    else:
        doc.save(str(path))
    doc.close()
    return path


def _png_bytes(w: int, h: int) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 7 + y * 3) % 256, (x * y) % 256, (x ^ y) % 256)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def page_texts(path: Path, password: str = "") -> list[str]:
    m = _mupdf()
    with m.open(str(path)) as doc:
        if doc.needs_pass:
            assert doc.authenticate(password)
        return [doc[i].get_text().strip() for i in range(doc.page_count)]


def outputs(job) -> list[Path]:
    assert job.result is not None, job.error
    return [Path(o["path"]) for o in job.result["outputs"]]


@pytest.fixture
def run(make_runner):
    runner, events = make_runner()

    def _run(action_id: str, files, params: dict | None = None):
        return runner.run_sync(action_id, [str(f) for f in files], params or {})

    _run.runner = runner
    _run.events = events
    return _run


@pytest.fixture
def work(dropwheel_env) -> Path:
    return dropwheel_env.work


def done(job):
    assert job.state == "done", (job.state, job.error)
    return job


# ============================================================================ pure helpers

class TestParsePages:
    def test_basic_ranges(self):
        assert P.parse_pages("1-3,5", 10) == [0, 1, 2, 4]
        assert P.parse_pages("8-z", 10) == [7, 8, 9]
        assert P.parse_pages("7-", 9) == [6, 7, 8]
        assert P.parse_pages("-3", 9) == [0, 1, 2]
        assert P.parse_pages("r1", 9) == [8]
        assert P.parse_pages("3-1", 9) == [2, 1, 0]

    def test_words_and_exclusions(self):
        assert P.parse_pages("dispari", 5) == [0, 2, 4]
        assert P.parse_pages("even", 5) == [1, 3]
        assert P.parse_pages("x2", 4) == [0, 2, 3]
        assert P.parse_pages("1-4,x2-3", 4) == [0, 3]
        assert P.parse_pages("tutte", 3) == [0, 1, 2]

    def test_order_and_duplicates_kept(self):
        assert P.parse_pages("3, 1, 3", 3) == [2, 0, 2]

    def test_unicode_dashes_and_spaces(self):
        assert P.parse_pages("1 – 3 ; 5", 6) == [0, 1, 2, 4]

    def test_open_upper_bound_clamped(self):
        assert P.parse_pages("2-100", 4) == [1, 2, 3]

    def test_empty(self):
        assert P.parse_pages("", 3, empty_means_all=True) == [0, 1, 2]
        with pytest.raises(ValueError):
            P.parse_pages("", 3)

    @pytest.mark.parametrize("spec", ["0", "12", "a-b", "x1-3", "1-3,x1-3"])
    def test_invalid(self, spec):
        with pytest.raises(ValueError):
            P.parse_pages(spec, 3)

    def test_parse_ranges(self):
        assert P.parse_ranges("1-3,5,7-", 8) == [[0, 1, 2], [4], [6, 7]]
        with pytest.raises(ValueError):
            P.parse_ranges(" , ", 3)


def test_chunk_pages():
    assert P.chunk_pages(5, 2) == [[0, 1], [2, 3], [4]]
    assert P.chunk_pages(3, 0) == [[0], [1], [2]]


def test_interleave_order():
    assert P.interleave_order(3, 3) == [(0, 0), (1, 2), (0, 1), (1, 1), (0, 2), (1, 0)]
    assert P.interleave_order(2, 2, reverse_backs=False) == [(0, 0), (1, 0), (0, 1), (1, 1)]
    assert P.interleave_order(3, 2) == [(0, 0), (1, 1), (0, 1), (1, 0), (0, 2)]


def test_piece_label():
    assert P.piece_label([2], 10) == "p003"
    assert P.piece_label([0, 1, 2, 3], 10) == "p001-004"
    assert P.piece_label([0, 2], 10) == "p001+003"
    assert P.piece_label([0], 12345) == "p00001"


def test_format_page_label():
    assert P.format_page_label("{n}/{total}", 3, 12) == "3/12"
    assert P.format_page_label("Pagina {n} di {total}", 1, 2) == "Pagina 1 di 2"
    assert P.format_page_label("{n} {unknown}", 1, 2) == "1 {unknown}"
    assert P.format_page_label("{n} di {total} {", 1, 2) == "1 di 2 {"      # malformed: literal fallback


def test_pdf_dates():
    assert P.pdf_date("2026-10-29") == "D:20261029000000"
    assert P.pdf_date("2026-10-29T14:03:00+01:00") == "D:20261029140300+01'00'"
    assert P.pdf_date("2026-10-29T14:03:00Z") == "D:20261029140300Z"
    assert P.pdf_date("29/10/2026 08:05") == "D:20261029080500"
    assert P.pdf_date("D:20261029") == "D:20261029"
    with pytest.raises(ValueError):
        P.pdf_date("ieri")
    assert P.format_pdf_date("D:20261029203902+02'00'") == "2026-10-29 20:39:02 +02:00"
    assert P.format_pdf_date("D:20261029Z") == "2026-10-29 UTC"
    assert P.format_pdf_date("garbage") == "garbage"


@pytest.mark.parametrize("shown,expected", [
    ("2024:05:01 14:32:10+02:00", "D:20240501143210+02'00'"),         # exiftool, PDF:CreateDate
    ("2024:05:01 14:32:10-05:30", "D:20240501143210-05'30'"),
    ("2024:05:01 14:32:10Z", "D:20240501143210Z"),
    ("2024:05:01 14:32:10", "D:20240501143210"),
    ("2024:05:01 14:32:10.25+02:00", "D:20240501143210+02'00'"),      # XMP keeps fractions
    ("2024:05:01 14:32+02:00", "D:20240501143200+02'00'"),
    ("2024:05:01", "D:20240501000000"),
    ("2024-05-01 14:32:10 +02:00", "D:20240501143210+02'00'"),        # format_pdf_date()
    ("2024-05-01 14:32:10 UTC", "D:20240501143210Z"),
    ("2024-05-01 UTC", "D:20240501000000Z"),
    ("2024-05-01 14:32:10+02:00", "D:20240501143210+02'00'"),
    ("2024-05-01+02:00", "D:20240501000000+02'00'"),
    ("20240501143210+02'00'", "D:20240501143210+02'00'"),
    ("20240501:10", "D:20240501100000"),                              # no longer taken for a PDF date
])
def test_pdf_date_from_what_the_dialogs_show(shown, expected):
    assert P.pdf_date(shown) == expected


@pytest.mark.parametrize("bad", ["2024:13:01 10:00:00", "2024:05:01 25:00:00", "0000:00:00 00:00:00", "2024:05"])
def test_pdf_date_rejects_bad_dates(bad):
    with pytest.raises(ValueError):
        P.pdf_date(bad)


def test_edit_metadata_date_in_exiftool_form(run, work):
    m = _mupdf()
    src = make_pdf(work / "doc.pdf", pages=1)
    changes = {"set": {"PDF:CreateDate": "2023:05:01 14:32:10+02:00", "XMP-xmp:ModifyDate": "2022:01:02 03:04:05Z"}}
    out = outputs(done(run("pdf.privacy.metadata", [src], {"metadata": changes})))[0]
    with m.open(str(out)) as doc:
        assert doc.metadata["creationDate"] == "D:20230501143210+02'00'"
        assert doc.metadata["modDate"] == "D:20220102030405Z"


def test_fix_toc_levels():
    assert P.fix_toc_levels([[2, "a", 1], [4, "b", 2], [1, "c", 3]]) == [[1, "a", 1], [2, "b", 2], [1, "c", 3]]


@pytest.mark.parametrize("key,expected", [
    ("PDF:Title", "title"), ("XMP-dc:Creator", "author"), ("XMP-xmp:CreateDate", "creationDate"),
    ("modDate", "modDate"), ("Keywords", "keywords"), ("PDF:Producer", "producer"), ("whatever", None),
])
def test_normalize_metadata_key(key, expected):
    assert P.normalize_metadata_key(key) == expected


def test_perms_roundtrip():
    perms = P.Perms(print=True, print_hq=False, copy=False, modify=True, annotate=False)
    raw = perms.to_int()
    assert raw < 0                       # reserved high bits set → signed
    back = P.Perms.from_int(raw)
    assert (back.print, back.print_hq, back.copy, back.modify, back.annotate) == (True, False, False, True, False)
    assert P.Perms().all_allowed and not perms.all_allowed
    assert "--print=low" in perms.qpdf_args() and "--extract=n" in perms.qpdf_args()
    assert P.Perms.from_int(None).all_allowed


def test_blank_pdf_bytes_is_valid():
    pypdf = pytest.importorskip("pypdf")
    data = P.blank_pdf_bytes(*P.A4, pages=2)
    reader = pypdf.PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 2
    assert round(float(reader.pages[0].mediabox.width)) == 595


def test_paper_name():
    assert P.paper_name(*P.A4) == "A4"
    assert P.paper_name(842, 595) == "A4"
    assert P.paper_name(*P.LETTER) == "Letter"
    assert P.paper_name(500, 500) == ""


def test_gs_compress_args():
    light = P.gs_compress_args("light")
    assert "-dPDFSETTINGS=/printer" in light and "-dDownsampleColorImages=false" in light
    assert "-dPDFSETTINGS=/ebook" in P.gs_compress_args("balanced")
    assert "-dPDFSETTINGS=/screen" in P.gs_compress_args("strong")
    custom = P.gs_compress_args("custom", 100)
    assert "-dColorImageResolution=100" in custom and "-dMonoImageResolution=200" in custom
    assert "-dColorImageResolution=36" in P.gs_compress_args("custom", 5)
    assert not any("DCTEncode" in a or "PassThrough" in a for a in custom + light)
    custom = P.gs_compress_args("custom", 100, 30)
    for flag in ("-dAutoFilterColorImages=false", "-dAutoFilterGrayImages=false", "-dColorImageFilter=/DCTEncode",
                 "-dGrayImageFilter=/DCTEncode", "-dPassThroughJPEGImages=false"):
        assert flag in custom
    assert not any("DCTEncode" in a for a in P.gs_compress_args("light", 100, 30))


@pytest.mark.parametrize("quality,qfactor,samples", [(95, "0.100", "[1 1 1 1]"), (90, "0.200", "[1 1 1 1]"),
                                                     (70, "0.600", "[2 1 1 2]"), (50, "1.000", "[2 1 1 2]"),
                                                     (30, "1.667", "[2 1 1 2]"), (10, "5.000", "[2 1 1 2]")])
def test_gs_image_quality_ps(quality, qfactor, samples):
    ps = P.gs_image_quality_ps(quality)
    assert ps.endswith(" >> setdistillerparams") and "/LockDistillerParams true" in ps
    assert ps.count(f"/QFactor {qfactor} ") == 2 and ps.count(f"/HSamples {samples}") == 2
    assert "/ColorImageDict <<" in ps and "/GrayImageDict <<" in ps
    assert ps.count("<<") == ps.count(">>") == 3 and ps.count("[") == ps.count("]") == 4


def test_compress_default_from_config():
    assert P.compress_default([], {"pdf": {"compress": "screen"}})[0] == "pdf.compress.strong"
    assert P.compress_default([], {"pdf": {"compress": "printer"}})[0] == "pdf.compress.light"
    assert P.compress_default([], {})[0] == "pdf.compress.balanced"


def test_markdown_from_pages_structure():
    def span(text, size, flags=0, font="Helvetica"):
        return {"text": text, "size": size, "flags": flags, "font": font}

    def block(y, *lines):
        return {"type": 0, "bbox": (72, y, 500, y + 20), "lines": [{"spans": list(spans)} for spans in lines]}

    pages = [{"blocks": [
        block(50, [span("Relazione annuale", 22, 16)]),
        block(90, [span("Introduzione", 16, 16)]),
        block(120, [span("Questo è il testo del corpo ", 11)], [span("su due righe.", 11)]),
        block(160, [span("• primo punto", 11)], [span("• secondo punto", 11)]),
        block(200, [span("print('ciao')", 10, 8, "Courier")]),
        block(240, [span("Testo con ", 11), span("grassetto", 11, 16), span(" dentro.", 11)]),
    ], "tables": []}]
    md = P.markdown_from_pages(pages)
    assert md.startswith("# Relazione annuale\n")
    assert "## Introduzione" in md
    assert "Questo è il testo del corpo su due righe." in md
    assert "- primo punto\n- secondo punto" in md
    assert "```\nprint('ciao')\n```" in md
    assert "Testo con **grassetto** dentro." in md
    assert P.markdown_from_pages([{"blocks": [], "tables": []}]) == ""


def test_build_epub_is_valid(tmp_path):
    dst = tmp_path / "book.epub"
    P.build_epub(dst, title="Libro & co", author="Anna", lang="it",
                 chapters=[("Capitolo 1", "<p>Ciao</p>"), ("Capitolo 2", '<p><img alt="" src="images/a.png"/></p>')],
                 images={"images/a.png": _png_bytes(4, 4)})
    with zipfile.ZipFile(dst) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype" and infos[0].compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"
        names = zf.namelist()
        for name in ("META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml", "OEBPS/toc.ncx",
                     "OEBPS/ch001.xhtml", "OEBPS/ch002.xhtml", "OEBPS/images/a.png"):
            assert name in names
        for name in names:
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(zf.read(name))          # well-formed XML
        assert "Libro &amp; co" in zf.read("OEBPS/content.opf").decode()


def test_build_html_and_xml_entities():
    doc = P.build_html("A <b>", "it", "<p>x</p>")
    assert doc.startswith("<!DOCTYPE html>") and "A &lt;b&gt;" in doc and 'lang="it"' in doc
    assert P._xml_entities("&nbsp;&amp;&eacute; & x") == "&#160;&amp;&#233; &amp; x"


def test_epub_chapters_from_toc():
    frags = [f"<p>{i}</p>" for i in range(5)]
    chapters = P._epub_chapters(frags, [[1, "A", 2], [2, "A.1", 3], [1, "B", 4]], "Titolo")
    assert chapters == [("Titolo", [0]), ("A", [1, 2]), ("B", [3, 4])]
    assert P._epub_chapters(frags, [], "T") == [("T", [0, 1, 2, 3, 4])]


def test_place_positions():
    assert P._place("center", 100, 200, 20, 10, 5) == (50, 100)
    assert P._place("top-left", 100, 200, 20, 10, 5) == (15, 10)
    assert P._place("bottom-right", 100, 200, 20, 10, 5) == (85, 190)
    assert P._place("bottom", 100, 200, 20, 10, 5) == (50, 190)


def test_rgb():
    assert P._rgb("#ff0000") == (1.0, 0.0, 0.0)
    assert P._rgb("0f0") == (0.0, 1.0, 0.0)
    assert P._rgb("nope", (0.5, 0.5, 0.5)) == (0.5, 0.5, 0.5)


# ============================================================================ locales

def _locale(lang: str) -> dict:
    data = json.loads((LOCALES / lang / "pdf.json").read_text(encoding="utf-8"))
    core = json.loads((LOCALES / lang / "core.json").read_text(encoding="utf-8"))
    return {**core, **data}


def _used_keys() -> set[str]:
    src = PDF_SRC.read_text(encoding="utf-8")
    keys = set(re.findall(r'"((?:pdf|sfx|grp|err|warn|prm)\.[A-Za-z0-9_.]+)"', src))
    action_ids = {a.id for a in registry.all_actions()} | {g.id for g in registry.all_groups()}
    not_i18n = {"pdf.dpi", "pdf.compress", "pdf.convert.more"}        # config keys / nested group ids
    keys = {k for k in keys if k not in action_ids and k not in not_i18n and not k.startswith("pdf.multi.")}
    plural = {"pdf.msg.pages_exported", "pdf.msg.split", "pdf.msg.merged", "pdf.msg.redacted",
              "pdf.msg.images_extracted", "pdf.msg.attachments_extracted", "pdf.warn.skipped_files",
              "pdf.prm.password.locked_many"}
    out = set()
    for k in keys:
        if k in plural:
            out |= {k + ".one", k + ".other"}
        else:
            out.add(k)
    for grp in ("general", "document", "security", "fonts"):
        out.add(f"pdf.info.grp.{grp}")
    for key in ("file", "size", "version", "pages", "page_size", "linearized", "tagged", "forms", "annotations",
                "attachments", "bookmarks", "signed", "title", "author", "subject", "keywords", "creator", "producer",
                "created", "modified", "encrypted", "open_password", "perm_print", "perm_copy", "perm_modify",
                "perm_annotate"):
        out.add(f"pdf.info.{key}")
    out |= {"pdf.prm.password.locked"}
    return out


@pytest.mark.parametrize("lang", ["en", "it"])
def test_locale_has_every_key(lang):
    table = _locale(lang)
    missing = sorted(k for k in _used_keys() if k not in table)
    assert not missing, missing


def test_locales_have_same_keys():
    en = json.loads((LOCALES / "en" / "pdf.json").read_text(encoding="utf-8"))
    it = json.loads((LOCALES / "it" / "pdf.json").read_text(encoding="utf-8"))
    assert set(en) == set(it)
    for key, text in en.items():
        assert set(re.findall(r"\{(\w+)\}", text)) == set(re.findall(r"\{(\w+)\}", it[key])), key


def test_action_labels_translated():
    it = _locale("it")
    for act in registry.all_actions():
        if not act.id.startswith("pdf."):
            continue
        for key in (act.label, act.description):
            if key.startswith("pdf."):
                assert key in it, (act.id, key)
        for p in act.all_params():
            for key in (p.label, p.help, p.unit):
                if key and key.startswith("pdf."):
                    assert key in it, (act.id, key)
            for _value, label in p.option_pairs([]):
                if label.startswith("pdf."):
                    assert label in it, (act.id, label)


# ============================================================================ registry / menu

def test_actions_registered_with_valid_metadata():
    ids = {a.id for a in registry.all_actions() if a.id.startswith("pdf.")}
    for expected in ("pdf.convert.png", "pdf.convert.jpg", "pdf.convert.txt", "pdf.convert.md", "pdf.convert.docx",
                     "pdf.convert.epub", "pdf.convert.html", "pdf.convert.pdfa", "pdf.convert.gray",
                     "pdf.compress.balanced", "pdf.compress.light", "pdf.compress.strong", "pdf.compress.custom",
                     "pdf.pages.organize", "pdf.split.pages", "pdf.split.ranges", "pdf.split.every",
                     "pdf.split.bookmarks", "pdf.pages.extract", "pdf.pages.delete", "pdf.rotate.cw",
                     "pdf.rotate.180", "pdf.rotate.ccw", "pdf.pages.reverse", "pdf.pages.blank", "pdf.merge",
                     "pdf.combine", "pdf.interleave", "pdf.secure.protect", "pdf.secure.unlock",
                     "pdf.privacy.redact", "pdf.privacy.strip", "pdf.privacy.metadata", "pdf.ocr.searchable",
                     "pdf.ocr.copy_text", "pdf.watermark", "pdf.page_numbers", "pdf.flatten", "pdf.extract.images",
                     "pdf.extract.attachments", "pdf.repair", "pdf.annotate", "pdf.print", "pdf.info"):
        assert expected in ids
    assert registry.get("pdf.pages.organize").dialog == "pages"
    assert registry.get("pdf.privacy.redact").dialog == "redact"
    assert registry.get("pdf.privacy.metadata").dialog == "metadata"
    assert registry.get("pdf.combine").mixed_ok
    assert registry.get("pdf.merge").multi == "combine"


def test_content_actions_accept_a_password():
    no_password = {"pdf.secure.protect", "pdf.annotate", "pdf.combine"}
    for act in registry.all_actions():
        if act.id.startswith("pdf.") and act.id not in no_password:
            names = {p.name: p for p in act.all_params()}
            assert "password" in names, act.id
            assert names["password"].type == "password"


def _ids(nodes) -> list[str]:
    return [n["id"] for n in nodes]


def test_single_pdf_menu(work):
    path = make_pdf(work / "doc.pdf")
    menu = registry.build_menu([str(path)], cfgmod.defaults(), "en")
    main = menu["pages"][0]["items"]
    assert len(main) <= 8
    assert _ids(main)[:3] == ["pdf.convert", "pdf.compress", "pdf.pages"]
    assert "pdf.secure" in _ids(main)
    secure = registry.find_node(menu, "pdf.secure")
    assert "pdf.secure.unlock" not in _ids(secure["items"])        # not encrypted
    assert secure["default"] == "pdf.secure.protect"
    png = registry.find_node(menu, "pdf.convert.png")
    pw = next(p for p in png["params"] if p["name"] == "password")
    assert pw["required"] is False and pw["type"] == "password"
    dpi = next(p for p in png["params"] if p["name"] == "dpi")
    assert dpi["default"] == 150


def test_compress_star_follows_config(work):
    path = make_pdf(work / "doc.pdf", pages=1)
    cfg = cfgmod.defaults()
    cfg["pdf"]["compress"] = "screen"
    menu = registry.build_menu([str(path)], cfg, "en")
    node = registry.find_node(menu, "pdf.compress")
    if node["default"] is not None and registry.find_node(menu, "pdf.compress.strong")["available"]:
        assert node["default"] == "pdf.compress.strong"


def test_encrypted_pdf_menu_marks_password(work):
    path = make_pdf(work / "locked.pdf", encrypt="segreto")
    assert P.encryption_state(path) == {"encrypted": True, "needs_password": True}
    menu = registry.build_menu([str(path)], cfgmod.defaults(), "it")
    png = registry.find_node(menu, "pdf.convert.png")
    pw = next(p for p in png["params"] if p["name"] == "password")
    assert pw["required"] is True and pw["locked"] is True and "locked.pdf" in pw["help"]
    assert registry.find_node(menu, "pdf.secure.unlock") is not None
    assert registry.find_node(menu, "pdf.secure.protect") is None


def test_multi_pdf_menu(work):
    a = make_pdf(work / "a.pdf", pages=1)
    b = make_pdf(work / "b.pdf", pages=1)
    menu = registry.build_menu([str(a), str(b)], cfgmod.defaults(), "en")
    main = _ids(menu["pages"][0]["items"])
    assert main[0] == "pdf.merge"
    assert "pdf.interleave" in main
    c = make_pdf(work / "c.pdf", pages=1)
    menu3 = registry.build_menu([str(a), str(b), str(c)], cfgmod.defaults(), "en")
    assert registry.find_node(menu3, "pdf.interleave") is None


def test_mixed_menu_offers_combine(work):
    pdf = make_pdf(work / "a.pdf", pages=1)
    png = work / "b.png"
    png.write_bytes(_png_bytes(20, 10))
    menu = registry.build_menu([str(pdf), str(png)], cfgmod.defaults(), "en")
    assert menu["category"] == "mixed"
    assert registry.find_node(menu, "pdf.combine") is not None


def test_password_param_json_without_files():
    param = P._password_param()
    data = param.to_json([], "en", {})
    assert data["required"] is False and "locked" not in data


def test_encryption_state_plain_and_owner_only(work):
    m = _mupdf()
    plain = make_pdf(work / "plain.pdf", pages=1)
    assert P.encryption_state(plain) == {"encrypted": False, "needs_password": False}
    owner_only = make_pdf(work / "owner.pdf", pages=1, encrypt="")
    state = P.encryption_state(owner_only)
    assert state["encrypted"] is True and state["needs_password"] is False
    assert P.encryption_state(work / "missing.pdf") == {"encrypted": False, "needs_password": False}
    del m


# ============================================================================ convert

def test_convert_png_folder_and_single(run, work):
    src = make_pdf(work / "-strano nome è.pdf", pages=3)
    before = sha(src)
    job = done(run("pdf.convert.png", [src], {"dpi": 50}))
    folder = outputs(job)[0]
    assert folder.is_dir() and folder.name == "-strano nome è (Pages)"
    assert sorted(p.name for p in folder.iterdir()) == ["page-001.png", "page-002.png", "page-003.png"]
    from PIL import Image

    with Image.open(folder / "page-001.png") as im:
        assert abs(im.size[0] - 595 * 50 / 72) <= 1 and abs(im.size[1] - 842 * 50 / 72) <= 1
    job = done(run("pdf.convert.png", [src], {"first_only": True, "dpi": 40}))
    assert outputs(job)[0].name == "-strano nome è.png"
    assert sha(src) == before


def test_convert_jpg_page_selection(run, work):
    src = make_pdf(work / "doc.pdf", pages=4)
    job = done(run("pdf.convert.jpg", [src], {"pages": "2,4", "dpi": 36, "quality": 60}))
    folder = outputs(job)[0]
    assert sorted(p.name for p in folder.iterdir()) == ["page-002.jpg", "page-004.jpg"]
    assert (folder / "page-002.jpg").read_bytes()[:3] == b"\xff\xd8\xff"


def test_convert_bad_pages_is_user_error(run, work):
    src = make_pdf(work / "doc.pdf", pages=2)
    job = run("pdf.convert.png", [src], {"pages": "9"})
    assert job.state == "failed" and "Invalid page selection" in job.error["message"]


def test_convert_txt(run, work):
    src = make_pdf(work / "doc.pdf", pages=2)
    job = done(run("pdf.convert.txt", [src]))
    out = outputs(job)[0]
    assert out.name == "doc.txt"
    text = out.read_text(encoding="utf-8")
    assert "Page 1 secret ABC" in text and "Page 2 secret ABC" in text


def test_convert_markdown_fallback(run, work):
    src = make_pdf(work / "doc.pdf", pages=2)
    job = done(run("pdf.convert.md", [src]))
    md = outputs(job)[0].read_text(encoding="utf-8")
    assert "Page 1 secret ABC" in md
    try:
        import pymupdf4llm  # noqa: F401
    except ImportError:
        assert any("PyMuPDF" in w and "PyPI" in w for w in job.warnings)
        assert not any("install pymupdf4llm" in w for w in job.warnings)


def _no_pymupdf4llm(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "pymupdf4llm", None)       # "import pymupdf4llm" raises ImportError


def test_convert_markdown_uses_document_to_markdown(run, work, monkeypatch):
    # PyMuPDF ≥ 2.0 from PyPI: Document.to_markdown runs the code, page by page and without OCR
    m = _mupdf()
    _no_pymupdf4llm(monkeypatch)
    calls = []

    def to_markdown(self, **kw):
        calls.append(kw)
        return f"# Pagina {kw['pages'][0] + 1}\n"

    monkeypatch.setattr(m.Document, "to_markdown", to_markdown, raising=False)
    job = done(run("pdf.convert.md", [make_pdf(work / "doc.pdf", pages=2)]))
    assert outputs(job)[0].read_text(encoding="utf-8") == "# Pagina 1\n\n# Pagina 2\n"
    assert calls == [{"pages": [0], "show_progress": False, "use_ocr": False},
                     {"pages": [1], "show_progress": False, "use_ocr": False}]
    assert not any("PyMuPDF" in w for w in job.warnings)


def test_convert_markdown_of_a_long_document_is_split_in_chunks(run, work, monkeypatch):
    # each call may scan the whole document, so the calls stay few
    m = _mupdf()
    _no_pymupdf4llm(monkeypatch)
    calls = []

    def to_markdown(self, *, pages, **kw):
        calls.append(pages)
        return f"{pages[0]}\n"

    monkeypatch.setattr(m.Document, "to_markdown", to_markdown, raising=False)
    done(run("pdf.convert.md", [make_pdf(work / "doc.pdf", pages=45)]))
    assert len(calls) == 23 and [p for pages in calls for p in pages] == list(range(45))


@pytest.mark.parametrize("error", [NotImplementedError("pymupdf4llm functions are not available."), ImportError("x")])
def test_convert_markdown_falls_back_when_the_code_is_missing(run, work, monkeypatch, error):
    # Arch's python-pymupdf 2.0: the method exists but the code does not
    m = _mupdf()
    _no_pymupdf4llm(monkeypatch)

    def to_markdown(self, **kw):
        raise error

    monkeypatch.setattr(m.Document, "to_markdown", to_markdown, raising=False)
    job = done(run("pdf.convert.md", [make_pdf(work / "doc.pdf", pages=2)]))
    assert "Page 2 secret ABC" in outputs(job)[0].read_text(encoding="utf-8")
    assert any("PyMuPDF" in w for w in job.warnings)


def test_convert_markdown_with_the_pymupdf4llm_module(run, work, monkeypatch):
    # AUR python-pymupdf4llm: the module with its header detection, also in versions without use_ocr
    import sys
    import types

    calls = []

    def to_markdown(doc, *, pages, show_progress, hdr_info):
        calls.append((pages, show_progress, hdr_info))
        return f"pagina {pages[0]}"

    module = types.SimpleNamespace(IdentifyHeaders=lambda doc: "headers", to_markdown=to_markdown)
    monkeypatch.setitem(sys.modules, "pymupdf4llm", module)
    job = done(run("pdf.convert.md", [make_pdf(work / "doc.pdf", pages=2)]))
    assert outputs(job)[0].read_text(encoding="utf-8") == "pagina 0\n\npagina 1\n"
    assert calls == [([0], False, "headers"), ([1], False, "headers")]
    assert not job.warnings


def test_convert_html(run, work):
    src = make_pdf(work / "doc.pdf", pages=2, meta={"title": "Titolo <prova>"})
    job = done(run("pdf.convert.html", [src]))
    html_text = outputs(job)[0].read_text(encoding="utf-8")
    assert "<title>Titolo &lt;prova&gt;</title>" in html_text and "secret ABC" in html_text


def test_convert_epub(run, work):
    src = make_pdf(work / "doc.pdf", pages=3, toc=True, image=True, meta={"author": "Anna"})
    job = done(run("pdf.convert.epub", [src]))
    out = outputs(job)[0]
    assert out.suffix == ".epub"
    with zipfile.ZipFile(out) as zf:
        assert zf.read("mimetype") == b"application/epub+zip"
        assert any(n.endswith(".opf") for n in zf.namelist())


@pytest.mark.skipif(not (tools.have("py:pdf2docx") or tools.have("soffice")), reason="pdf2docx/LibreOffice missing")
def test_convert_docx(run, work):
    src = make_pdf(work / "doc.pdf", pages=1)
    job = done(run("pdf.convert.docx", [src]))
    assert zipfile.is_zipfile(outputs(job)[0])


@pytest.mark.skipif(not tools.have("gs"), reason="ghostscript missing")
def test_convert_grayscale_and_pdfa(run, work):
    src = make_pdf(work / "doc.pdf", pages=1)
    assert outputs(done(run("pdf.convert.gray", [src])))[0].name == "doc (Grayscale).pdf"
    job = run("pdf.convert.pdfa", [src])
    assert job.state in ("done", "failed")


# ============================================================================ compress

def test_compress_reduces_or_keeps_original(run, work):
    big = make_pdf(work / "big.pdf", pages=1, image=True)
    job = done(run("pdf.compress.strong", [big]))
    outs = outputs(job)
    if outs:
        assert outs[0].name == "big (Compressed).pdf"
        assert job.result["size_before"] == big.stat().st_size
        assert job.result["size_after"] < job.result["size_before"]
        assert page_texts(outs[0]) == ["Page 1 secret ABC"]
    else:
        assert job.result["kind"] == "none"
    tiny = make_pdf(work / "tiny.pdf", pages=1)
    job = done(run("pdf.compress.light", [tiny]))
    for out in outputs(job):
        assert out.stat().st_size < tiny.stat().st_size


def test_compress_custom_each(run, work):
    a = make_pdf(work / "a.pdf", pages=1, image=True)
    b = make_pdf(work / "b.pdf", pages=1, image=True)
    job = done(run("pdf.compress.custom", [a, b], {"dpi": 50, "quality": 40}))
    assert job.result is not None


def _gs_argv(run, monkeypatch, src: Path, action: str, params: dict) -> list[str]:
    """The Ghostscript command line of a compression action (gs itself is faked)."""
    from dropwheel.jobs import Context

    calls: list[list[str]] = []

    def fake_run(self, argv, **kw):
        import subprocess

        args = [str(a) for a in argv]
        calls.append(args)
        out = next(a for a in args if a.startswith("-sOutputFile="))
        Path(out.split("=", 1)[1].replace("%%", "%")).write_bytes(b"%PDF-1.7\n")
        return subprocess.CompletedProcess(args, 0, "", "")

    real_have = P._have
    monkeypatch.setattr(P, "_have", lambda name: name == "gs" or real_have(name))
    monkeypatch.setattr(tools, "have", lambda name: name == "gs" or real_have(name))
    monkeypatch.setattr(Context, "run", fake_run)
    done(run(action, [src], params))
    return next(c for c in calls if c[0] == "gs")


def test_compress_custom_gives_the_quality_to_ghostscript(run, work, monkeypatch):
    src = make_pdf(work / "doc.pdf", pages=1, image=True)
    low = _gs_argv(run, monkeypatch, src, "pdf.compress.custom", {"dpi": 120, "quality": 30})
    high = _gs_argv(run, monkeypatch, src, "pdf.compress.custom", {"dpi": 120, "quality": 90})
    assert low != high
    assert "-dColorImageResolution=120" in low and "-dPassThroughJPEGImages=false" in low
    ps = low[low.index("-c") + 1]
    assert ps == P.gs_image_quality_ps(30) and "/QFactor 1.667 " in ps
    assert "/QFactor 0.200 " in high[high.index("-c") + 1]
    # the code runs once the output device exists, and "-f" ends it before the input
    assert next(i for i, a in enumerate(low) if a.startswith("-sOutputFile=")) < low.index("-c")
    assert low[low.index("-c") + 2] == "-f" and low[-1] == str(src)


def test_compress_presets_do_not_touch_the_quality(run, work, monkeypatch):
    src = make_pdf(work / "doc.pdf", pages=1, image=True)
    argv = _gs_argv(run, monkeypatch, src, "pdf.compress.balanced", {})
    assert "-c" not in argv and not any("DCTEncode" in a for a in argv)
    assert "-dPDFSETTINGS=/ebook" in argv and argv[-2:] == ["-f", str(src)]


# ============================================================================ pages

def test_organize_pageops_with_duplicates(run, work):
    src = make_pdf(work / "doc.pdf", pages=3)
    ops = [{"src": 2, "rotate": 0}, {"src": 0, "rotate": 90}, {"src": 0, "rotate": 0}]
    job = done(run("pdf.pages.organize", [src], {"pageops": ops}))
    out = outputs(job)[0]
    assert out.name == "doc (Organized).pdf"
    m = _mupdf()
    with m.open(str(out)) as doc:
        assert [doc[i].get_text().strip() for i in range(3)] == ["Page 3 secret ABC", "Page 1 secret ABC",
                                                               "Page 1 secret ABC"]
        assert [doc[i].rotation for i in range(3)] == [0, 90, 0]


def test_organize_pageops_json_string_and_blank(run, work):
    src = make_pdf(work / "doc.pdf", pages=2)
    job = done(run("pdf.pages.organize", [src], {"pageops": json.dumps([{"src": 1}, {"blank": True}, {"src": 0}])}))
    texts = page_texts(outputs(job)[0])
    assert texts == ["Page 2 secret ABC", "", "Page 1 secret ABC"]


def test_organize_invalid(run, work):
    src = make_pdf(work / "doc.pdf", pages=2)
    job = run("pdf.pages.organize", [src], {"pageops": [{"src": 7}]})
    assert job.state == "failed"
    job = run("pdf.pages.organize", [src], {"pageops": [{"src": 0, "rotate": 45}]})
    assert job.state == "failed"


def test_extract_delete_reverse(run, work):
    src = make_pdf(work / "doc.pdf", pages=4)
    before = sha(src)
    out = outputs(done(run("pdf.pages.extract", [src], {"pages": "3,1"})))[0]
    assert page_texts(out) == ["Page 3 secret ABC", "Page 1 secret ABC"]
    out = outputs(done(run("pdf.pages.delete", [src], {"pages": "2-3"})))[0]
    assert page_texts(out) == ["Page 1 secret ABC", "Page 4 secret ABC"]
    job = run("pdf.pages.delete", [src], {"pages": "1-z"})
    assert job.state == "failed" and "No pages" in job.error["message"]
    out = outputs(done(run("pdf.pages.reverse", [src])))[0]
    assert page_texts(out)[0] == "Page 4 secret ABC"
    assert sha(src) == before


def test_insert_blank_pages(run, work):
    src = make_pdf(work / "doc.pdf", pages=2, size=(400, 300))
    out = outputs(done(run("pdf.pages.blank", [src], {"position": "after", "after": 1, "count": 2})))[0]
    m = _mupdf()
    with m.open(str(out)) as doc:
        assert doc.page_count == 4
        assert doc[1].get_text().strip() == "" and doc[2].get_text().strip() == ""
        assert (round(doc[1].rect.width), round(doc[1].rect.height)) == (400, 300)
    out = outputs(done(run("pdf.pages.blank", [src], {"position": "start", "size": "a4"})))[0]
    with m.open(str(out)) as doc:
        assert doc[0].get_text().strip() == "" and round(doc[0].rect.width) == 595


def test_rotate_all_and_selected(run, work):
    src = make_pdf(work / "doc.pdf", pages=3)
    m = _mupdf()
    out = outputs(done(run("pdf.rotate.cw", [src])))[0]
    assert out.name == "doc (Rotated).pdf"
    with m.open(str(out)) as doc:
        assert [p.rotation for p in doc] == [90, 90, 90]
    out = outputs(done(run("pdf.rotate.ccw", [src], {"pages": "2"})))[0]
    with m.open(str(out)) as doc:
        assert [p.rotation for p in doc] == [0, 270, 0]
    out = outputs(done(run("pdf.rotate.180", [src])))[0]
    with m.open(str(out)) as doc:
        assert [p.rotation for p in doc] == [180, 180, 180]


def test_rotate_replace_original(run, work):
    src = make_pdf(work / "doc.pdf", pages=1)
    job = done(run("pdf.rotate.cw", [src], {"replace": True}))
    assert outputs(job)[0] == src
    m = _mupdf()
    with m.open(str(src)) as doc:
        assert doc[0].rotation == 90
    assert job.result["undoable"]


# ============================================================================ split

def test_split_every_page(run, work):
    src = make_pdf(work / "doc.pdf", pages=3)
    outs = outputs(done(run("pdf.split.pages", [src])))
    assert [o.name for o in outs] == ["doc p001.pdf", "doc p002.pdf", "doc p003.pdf"]
    assert page_texts(outs[1]) == ["Page 2 secret ABC"]


def test_split_many_pieces_go_to_folder(run, work):
    src = make_pdf(work / "doc.pdf", pages=7)
    outs = outputs(done(run("pdf.split.pages", [src])))
    assert len(outs) == 1 and outs[0].is_dir() and outs[0].name == "doc (Split)"
    assert len(list(outs[0].iterdir())) == 7


def test_split_every_n_and_ranges(run, work):
    src = make_pdf(work / "doc.pdf", pages=5)
    outs = outputs(done(run("pdf.split.every", [src], {"every": 2})))
    assert [o.name for o in outs] == ["doc p001-002.pdf", "doc p003-004.pdf", "doc p005.pdf"]
    outs = outputs(done(run("pdf.split.ranges", [src], {"ranges": "1-2, 4-"})))
    assert [len(page_texts(o)) for o in outs] == [2, 2]
    single = outputs(done(run("pdf.split.ranges", [src], {"ranges": "2-3"})))
    assert len(single) == 1 and page_texts(single[0]) == ["Page 2 secret ABC", "Page 3 secret ABC"]
    job = run("pdf.split.every", [src], {"every": 10})
    assert job.state == "failed"


def test_split_by_bookmarks(run, work):
    src = make_pdf(work / "doc.pdf", pages=3, toc=True)
    outs = outputs(done(run("pdf.split.bookmarks", [src])))
    assert [o.name for o in outs] == ["doc 01 Chapter 1.pdf", "doc 02 Chapter 2.pdf", "doc 03 Chapter 3.pdf"]
    nobm = make_pdf(work / "nobm.pdf", pages=2)
    job = run("pdf.split.bookmarks", [nobm])
    assert job.state == "failed" and "bookmarks" in job.error["message"]


# ============================================================================ multi-file

def test_merge_in_drop_order_with_bookmarks(run, work):
    a = make_pdf(work / "zeta.pdf", pages=2, text="Z{n}")
    b = make_pdf(work / "alfa.pdf", pages=1, text="A{n}")
    job = done(run("pdf.merge", [a, b]))
    out = outputs(job)[0]
    assert out.name == "zeta (Merged).pdf"
    assert page_texts(out) == ["Z1", "Z2", "A1"]
    m = _mupdf()
    with m.open(str(out)) as doc:
        assert [e[1:] for e in doc.get_toc()] == [["zeta", 1], ["alfa", 3]]


def test_merge_keeps_source_bookmarks(run, work):
    a = make_pdf(work / "a.pdf", pages=2, toc=True)
    b = make_pdf(work / "b.pdf", pages=1)
    out = outputs(done(run("pdf.merge", [a, b])))[0]
    m = _mupdf()
    with m.open(str(out)) as doc:
        toc = doc.get_toc()
    assert [1, "a", 1] in toc and [2, "Chapter 2", 2] in toc and [1, "b", 3] in toc


def test_interleave_duplex(run, work):
    fronts = make_pdf(work / "fronti.pdf", pages=2, text="F{n}")
    backs = make_pdf(work / "retri.pdf", pages=2, text="B{n}")
    out = outputs(done(run("pdf.interleave", [fronts, backs])))[0]
    assert page_texts(out) == ["F1", "B2", "F2", "B1"]
    out = outputs(done(run("pdf.interleave", [fronts, backs], {"reverse_second": False})))[0]
    assert page_texts(out) == ["F1", "B1", "F2", "B2"]


def test_combine_pdf_images_svg(run, work):
    pdf = make_pdf(work / "a.pdf", pages=2)
    png = work / "foto.png"
    png.write_bytes(_png_bytes(64, 32))
    svg = work / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
                   '<rect width="40" height="20" fill="red"/></svg>', encoding="utf-8")
    assert classify(str(svg)).category == "svg"
    job = done(run("pdf.combine", [png, pdf, svg]))
    out = outputs(job)[0]
    assert out.name == "foto (Merged).pdf"
    m = _mupdf()
    with m.open(str(out)) as doc:
        assert doc.page_count == 4
        assert doc[0].get_images()
        assert [e[1] for e in doc.get_toc()] == ["foto", "a", "logo"]
    job = done(run("pdf.combine", [png, pdf], {"page_size": "a4", "margin": 10}))
    with m.open(str(outputs(job)[0])) as doc:
        assert round(doc[0].rect.width) == 842          # landscape image → landscape A4


def test_combine_exif_rotated_jpeg(run, work):
    from PIL import Image

    jpg = work / "ruotata.jpg"
    img = Image.new("RGB", (80, 40), (10, 200, 10))
    exif = img.getexif()
    exif[0x0112] = 6                                     # rotate 90° CW on display
    img.save(jpg, "JPEG", exif=exif.tobytes())
    pdf = make_pdf(work / "a.pdf", pages=1)
    out = outputs(done(run("pdf.combine", [jpg, pdf])))[0]
    m = _mupdf()
    with m.open(str(out)) as doc:
        r = doc[0].rect
        assert r.height > r.width                         # displayed portrait


# ============================================================================ security

def test_protect_and_unlock(run, work):
    m = _mupdf()
    src = make_pdf(work / "doc.pdf", pages=1)
    job = done(run("pdf.secure.protect", [src], {"user_password": "s3greto", "user_password2": "s3greto",
                                                 "allow_copy": False}))
    out = outputs(job)[0]
    assert out.name == "doc (Protected).pdf"
    with m.open(str(out)) as doc:
        assert doc.needs_pass and doc.authenticate("s3greto")
    assert P.encryption_state(out)["needs_password"]

    job = run("pdf.secure.unlock", [out])
    assert job.state == "failed" and "password protected" in job.error["message"]
    job = run("pdf.secure.unlock", [out], {"password": "sbagliata"})
    assert job.state == "failed" and "Wrong password" in job.error["message"]
    job = done(run("pdf.secure.unlock", [out], {"password": "s3greto"}))
    unlocked = outputs(job)[0]
    with m.open(str(unlocked)) as doc:
        assert not doc.needs_pass and not (doc.metadata or {}).get("encryption")
        assert doc[0].get_text().strip() == "Page 1 secret ABC"


def test_protect_validation(run, work):
    src = make_pdf(work / "doc.pdf", pages=1)
    job = run("pdf.secure.protect", [src], {"user_password": "a", "user_password2": "b"})
    assert job.state == "failed" and "do not match" in job.error["message"]
    job = run("pdf.secure.protect", [src], {})
    assert job.state == "failed"
    # owner-only restrictions without an open password are allowed
    job = done(run("pdf.secure.protect", [src], {"allow_print": False}))
    st = P.encryption_state(outputs(job)[0])
    assert st["encrypted"] and not st["needs_password"]


def test_encrypted_input_is_reprotected(run, work):
    m = _mupdf()
    src = make_pdf(work / "locked.pdf", pages=2, encrypt="pw123")
    job = run("pdf.rotate.cw", [src])
    assert job.state == "failed" and "password" in job.error["message"].lower()
    job = done(run("pdf.rotate.cw", [src], {"password": "pw123"}))
    out = outputs(job)[0]
    with m.open(str(out)) as doc:
        assert doc.needs_pass and doc.authenticate("pw123")
        assert [p.rotation for p in doc] == [90, 90]
    assert any("same password" in w for w in job.warnings)


def test_encrypted_input_conversion(run, work):
    src = make_pdf(work / "locked.pdf", pages=1, encrypt="pw")
    job = done(run("pdf.convert.txt", [src], {"password": "pw"}))
    assert "secret ABC" in outputs(job)[0].read_text(encoding="utf-8")
    job = run("pdf.convert.txt", [src], {"password": "no"})
    assert job.state == "failed" and "Wrong password" in job.error["message"]


def test_redact_terms_and_boxes(run, work):
    m = _mupdf()
    src = make_pdf(work / "doc.pdf", pages=2, text="Mario Rossi pagina {n}",
                   meta={"author": "Mario Rossi", "title": "Riservato"})
    before = sha(src)
    boxes = [{"page": 1, "x": 0.0, "y": 0.0, "w": 1.0, "h": 0.2, "style": "solid"}]
    job = done(run("pdf.privacy.redact", [src], {"boxes": boxes, "terms": ["rossi"]}))
    out = outputs(job)[0]
    assert out.name == "doc (Redacted).pdf"
    with m.open(str(out)) as doc:
        assert "Rossi" not in doc[0].get_text() and "Mario" in doc[0].get_text()
        assert doc[1].get_text().strip() == ""          # whole top band of page 2 removed
        assert not (doc.metadata or {}).get("author")
        assert doc[0].get_pixmap(dpi=72, clip=m.Rect(140, 92, 150, 98)).pixel(2, 2) == (0, 0, 0)
    assert sha(src) == before


def test_redact_errors(run, work):
    src = make_pdf(work / "doc.pdf", pages=1)
    job = run("pdf.privacy.redact", [src], {"boxes": []})
    assert job.state == "failed" and "Draw at least one area" in job.error["message"]
    job = run("pdf.privacy.redact", [src], {"terms": ["inesistente"]})
    assert job.state == "failed" and "not found" in job.error["message"]
    job = run("pdf.privacy.redact", [src], {"boxes": [{"page": 5, "x": 0, "y": 0, "w": 1, "h": 1}]})
    assert job.state == "failed"


def test_redact_on_rotated_page_and_image(run, work):
    m = _mupdf()
    src = make_pdf(work / "rot.pdf", pages=1, rotate=90, image=True)
    with m.open(str(src)) as doc:
        page = doc[0]
        words = page.get_text("words")
        vis = m.Rect(words[0][:4]) * page.rotation_matrix
        for w in words[1:]:
            vis |= m.Rect(w[:4]) * page.rotation_matrix
        W, H = page.rect.width, page.rect.height
        box = {"page": 0, "x": (vis.x0 - 2) / W, "y": (vis.y0 - 2) / H, "w": (vis.width + 4) / W,
               "h": (vis.height + 4) / H}
        img_box = {"page": 0, "x": 0.3, "y": 0.3, "w": 0.1, "h": 0.1}
    out = outputs(done(run("pdf.privacy.redact", [src], {"boxes": [box, img_box]})))[0]
    with m.open(str(out)) as doc:
        assert doc[0].get_text().strip() == ""
        assert doc[0].rotation == 90


def _raw_text_pdf(path: Path, content: bytes, *, font_encoding: str = "/WinAnsiEncoding") -> Path:
    """One page with the given content stream and font /F1 (Helvetica, encoding as given)."""
    m = _mupdf()
    doc = m.open()
    page = doc.new_page()
    font = doc.get_new_xref()
    doc.update_object(font, f"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding{font_encoding}>>")
    doc.xref_set_key(page.xref, "Resources", f"<</Font<</F1 {font} 0 R>>>>")
    stream = doc.get_new_xref()
    doc.update_object(stream, "<<>>")
    doc.update_stream(stream, content, new=True)
    doc.xref_set_key(page.xref, "Contents", f"{stream} 0 R")
    doc.save(str(path))
    doc.close()
    return path


def _all_text(doc) -> str:
    """Everything the text of a document can show: ligatures expanded, with and without /ActualText."""
    m = _mupdf()
    flags = m.TEXT_PRESERVE_WHITESPACE
    return "\n".join(page.get_text(flags=flags, clip=m.INFINITE_RECT())
                     + page.get_text(flags=flags | m.TEXT_IGNORE_ACTUALTEXT, clip=m.INFINITE_RECT()) for page in doc)


def test_redact_dialog_search_sees_ligature_words(work):
    from dropwheel import preview

    src = _raw_text_pdf(work / "lig.pdf", b"BT /F1 14 Tf 72 700 Td (Il signor Ra\\001aele e Co\\002ni qui) Tj "
                                          b"0 -30 Td (Raffaele scritto per esteso) Tj ET",
                        font_encoding="<</BaseEncoding/WinAnsiEncoding/Differences[1/ff/fi]>>")
    found = preview.pdf_search(str(src), "Raffaele")["matches"]
    assert len(found) == 1 and len(found[0]["rects"]) == 2          # the same hits as the redaction itself


def test_redact_term_set_with_ligature_glyphs(run, work):
    # a font like pdfLaTeX's: codes 1 and 2 are the "ff" and "fi" ligature glyphs
    m = _mupdf()
    src = _raw_text_pdf(work / "lig.pdf", b"BT /F1 14 Tf 72 700 Td (Il signor Ra\\001aele e Co\\002ni qui) Tj "
                                          b"0 -30 Td (Raffaele scritto per esteso) Tj ET",
                        font_encoding="<</BaseEncoding/WinAnsiEncoding/Differences[1/ff/fi]>>")
    with m.open(str(src)) as doc:
        assert "\ufb00" in doc[0].get_text()        # what the default extraction and search see
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Raffaele", "cofini"]}))
    assert "3 areas" in job.result["message"]
    with m.open(str(outputs(job)[0])) as doc:
        text = doc[0].get_text(flags=m.TEXT_PRESERVE_WHITESPACE)
        assert "Il signor" in text and "scritto per esteso" in text
        assert "\ufb00" not in doc[0].get_text() and "\ufb01" not in doc[0].get_text()
        assert "affaele" not in text and "ofini" not in text
    # the case-sensitive search reads the same expanded text
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Raffaele"], "case_sensitive": True}))
    assert "2 areas" in job.result["message"]
    job = run("pdf.privacy.redact", [src], {"terms": ["RAFFAELE"], "case_sensitive": True})
    assert job.state == "failed" and "not found" in job.error["message"]


@pytest.mark.parametrize("params", [{}, {"case_sensitive": True}])
def test_redact_term_split_by_a_line_end_hyphen(run, work, params):
    m = _mupdf()
    src = make_pdf(work / "h.pdf", pages=1, text="Il signor Raf-\nfaele abita qui")
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Raffaele"], **params}))
    with m.open(str(outputs(job)[0])) as doc:
        text = _all_text(doc)
    assert "Il signor" in text and "abita qui" in text
    assert "Raf" not in text and "faele" not in text


def test_redact_hyphen_cases_on_a_rotated_page_and_in_a_hyphenated_term(run, work):
    m = _mupdf()
    src = make_pdf(work / "h.pdf", pages=1, text="Il signor Ros-\nsi usa anti-\nvirus ogni giorno", rotate=90)
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Rossi", "anti-virus"]}))
    with m.open(str(outputs(job)[0])) as doc:
        text = _all_text(doc)
    assert "Il signor" in text and "ogni giorno" in text and "usa" in text
    assert "Ros" not in text and "anti" not in text and "virus" not in text


def test_redact_term_outside_the_visible_page(run, work):
    m = _mupdf()
    src = make_pdf(work / "off.pdf", pages=1, size=(300, 300), text="Visibile Rossi")
    with m.open(str(src)) as doc:
        doc[0].insert_text((400, 100), "Nascosto Rossi", fontsize=14)
        doc.save(str(work / "off2.pdf"))
    job = done(run("pdf.privacy.redact", [work / "off2.pdf"], {"terms": ["Rossi"]}))
    with m.open(str(outputs(job)[0])) as doc:
        text = _all_text(doc)
    assert "Visibile" in text and "Rossi" not in text


def test_redact_term_on_a_rotated_page(run, work):
    m = _mupdf()
    src = make_pdf(work / "r.pdf", pages=1, text="Mario Rossi pagina {n}", rotate=90)
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Rossi"]}))
    with m.open(str(outputs(job)[0])) as doc:
        text = _all_text(doc)
    assert "Mario" in text and "pagina" in text and "Rossi" not in text


def _doc_with_hidden_layer(path: Path, *, indirect: bool) -> Path:
    m = _mupdf()
    doc = m.open()
    page = doc.new_page()
    hidden = doc.add_ocg("Bozza", on=False)
    design = doc.add_ocg("Disegno", intent="Design")
    page.insert_text((72, 100), "Testo visibile Rossi", fontsize=12)
    page.insert_text((72, 140), "Nascosto Rossi nel livello", fontsize=12, oc=hidden)
    page.insert_text((72, 180), "Progetto Rossi nel disegno", fontsize=12, oc=design)
    if indirect:
        props = doc.get_new_xref()
        doc.update_object(props, doc.xref_get_key(doc.pdf_catalog(), "OCProperties")[1])
        doc.xref_set_key(doc.pdf_catalog(), "OCProperties", f"{props} 0 R")
    doc.save(str(path))
    doc.close()
    return path


@pytest.mark.parametrize("indirect", [False, True])
def test_redact_term_in_a_hidden_layer(run, work, indirect):
    m = _mupdf()
    src = _doc_with_hidden_layer(work / "layer.pdf", indirect=indirect)
    with m.open(str(src)) as doc:
        assert "Nascosto" not in doc[0].get_text()
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Rossi"]}))
    with m.open(str(outputs(job)[0])) as doc:
        contents = doc[0].read_contents()
        assert b"Rossi" not in contents and b"Nascosto" in contents and b"Progetto" in contents
        assert "Nascosto" not in doc[0].get_text()                      # the layer stays off
        assert doc.get_layer()["off"] and doc.get_ocgs()
    assert "3 areas" in job.result["message"]


def test_redact_box_over_a_hidden_layer(run, work):
    m = _mupdf()
    src = work / "box.pdf"
    with m.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 100), "Visibile", fontsize=12)
        page.insert_text((72, 140), "Segreto sotto il riquadro", fontsize=12, oc=doc.add_ocg("Bozza", on=False))
        doc.save(str(src))
    box = {"page": 0, "x": 0, "y": 120 / 842, "w": 1, "h": 40 / 842}
    job = done(run("pdf.privacy.redact", [src], {"boxes": [box]}))
    with m.open(str(outputs(job)[0])) as doc:
        contents = doc[0].read_contents()
    assert b"Segreto" not in contents and b"Visibile" in contents


def test_redact_term_only_in_a_hidden_layer_is_found(run, work):
    m = _mupdf()
    src = work / "only.pdf"
    with m.open() as doc:
        doc.new_page().insert_text((72, 140), "Nascosto Rossi", fontsize=12, oc=doc.add_ocg("Bozza", on=False))
        doc.save(str(src))
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Rossi"]}))
    with m.open(str(outputs(job)[0])) as doc:
        assert b"Rossi" not in doc[0].read_contents()


@pytest.mark.parametrize("indirect", [False, True])
def test_redact_removes_xfa_form_data(run, work, indirect):
    m = _mupdf()
    src = make_pdf(work / "xfa.pdf", pages=1, text="Mario Rossi")
    with m.open(str(src)) as doc:
        data = doc.get_new_xref()
        doc.update_object(data, "<<>>")
        doc.update_stream(data, b"<xfa:data><nome>Rossi Segreto</nome></xfa:data>", new=True, compress=False)
        form = f"<</Fields[]/XFA[(datasets){data} 0 R]>>"
        if indirect:
            holder = doc.get_new_xref()
            doc.update_object(holder, form)
            form = f"{holder} 0 R"
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm", form)
        doc.save(str(work / "xfa2.pdf"))
    assert b"Segreto" in (work / "xfa2.pdf").read_bytes()
    job = done(run("pdf.privacy.redact", [work / "xfa2.pdf"], {"terms": ["Rossi"]}))
    assert b"Segreto" not in outputs(job)[0].read_bytes()


def test_redact_glyphs_hidden_behind_a_different_actualtext(run, work):
    # viewers extract "testo" here, but the page shows "Mario Rossi": the search must see the glyphs too
    m = _mupdf()
    src = _raw_text_pdf(work / "at.pdf", b"/Span <</ActualText (testo)>> BDC "
                                         b"BT /F1 14 Tf 72 700 Td (Mario Rossi qui) Tj ET EMC")
    with m.open(str(src)) as doc:
        assert "Rossi" not in doc[0].get_text()
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Rossi"]}))
    with m.open(str(outputs(job)[0])) as doc:
        assert "Rossi" not in doc[0].get_text(flags=m.TEXT_IGNORE_ACTUALTEXT)
        assert "Mario" in doc[0].get_text(flags=m.TEXT_IGNORE_ACTUALTEXT)


def test_redact_fails_when_actualtext_keeps_the_term(run, work):
    # the replacement text stays in the page content after its glyphs go: nothing may be delivered
    src = _raw_text_pdf(work / "at.pdf", b"/Span <</ActualText (Mario Rossi)>> BDC "
                                         b"BT /F1 14 Tf 72 700 Td (abcde fghij klmn) Tj ET EMC")
    job = run("pdf.privacy.redact", [src], {"terms": ["Rossi"]})
    assert job.state == "failed" and "Redaction check failed" in job.error["message"]
    assert not list(work.glob("at (*"))


def _doc_with_terms_outside_the_pages(path: Path) -> Path:
    m = _mupdf()
    doc = m.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), f"Contratto di Mario Rossi pagina {i + 1}", fontsize=12)
    doc[0].insert_link({"kind": m.LINK_URI, "from": m.Rect(72, 300, 300, 320), "uri": "mailto:mario.rossi@example.com"})
    doc[1].insert_link({"kind": m.LINK_URI, "from": m.Rect(72, 300, 300, 320), "uri": "https://example.com/x"})
    doc.set_toc([[1, "Contratto Mario Rossi", 1],
                 [2, "Allegato", 2, {"kind": m.LINK_URI, "uri": "https://rossi.example.com"}],
                 [1, "Altro", 2]])
    # page labels: a literal prefix with escapes, and a UTF-16 one (not what set_page_labels() writes)
    labels = doc.get_new_xref()
    doc.update_object(labels, "<</Nums[0<</S/D/P(Mario \\(Rossi\\)\\350-)>>"
                              "1<</S/r/P<FEFF0052006F007300730069>/St 3>>]>>")
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", f"{labels} 0 R")
    # tagged content, a layer and a named destination
    elem = doc.get_new_xref()
    root = doc.get_new_xref()
    doc.update_object(elem, f"<</Type/StructElem/S/Figure/P {root} 0 R/Alt(Foto di Rossi)/ActualText(Rossi)"
                            f"/T(Titolo Rossi)/Pg {doc[0].xref} 0 R>>")
    doc.update_object(root, f"<</Type/StructTreeRoot/K {elem} 0 R>>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
    doc.add_ocg("Livello Rossi")
    names = doc.get_new_xref()
    doc.update_object(names, f"<</Names[(Rossi)<</D[{doc[0].xref} 0 R/Fit]>>]>>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</Dests {names} 0 R>>")
    doc.save(str(path))
    doc.close()
    return path


def test_redact_terms_outside_the_page_text(run, work):
    m = _mupdf()
    src = _doc_with_terms_outside_the_pages(work / "toc.pdf")
    job = done(run("pdf.privacy.redact", [src], {"terms": ["Rossi"]}))
    out = outputs(job)[0]
    assert "Rossi" not in out.read_bytes().decode("latin-1") and "rossi" not in out.read_bytes().decode("latin-1")
    with m.open(str(out)) as doc:
        toc = doc.get_toc(simple=False)
        assert [e[1] for e in toc] == ["Contratto Mario █████", "Allegato", "Altro"]
        assert "uri" not in toc[1][3]                                   # the bookmark lost its target
        assert [link.get("uri") for link in doc[0].get_links()] == []
        assert [link.get("uri") for link in doc[1].get_links()] == ["https://example.com/x"]
        assert "Rossi" not in _all_text(doc) and "Mario" in _all_text(doc)
        # the page label rules keep working, the prefixes hide the term
        rules = dict(doc._get_page_labels())
        assert "/S/D" in rules[0] and "/S/r" in rules[1] and "/St 3" in rules[1]
        prefix = [s for s in P._object_strings(rules[0]) if s[2] == "P"][0][3]
        assert prefix == "Mario (█████)è-"
        assert [s[3] for s in P._object_strings(rules[1]) if s[2] == "P"] == ["█████"]
        assert doc.get_ocgs() and [o["name"] for o in doc.get_ocgs().values()] == ["Livello █████"]
        assert list(doc.resolve_names()) == ["█████"]
        assert P._doc_string_leaks(doc, P._terms_pattern(["rossi"])) == []
        assert P._doc_string_leaks(doc, P._terms_pattern(["Livello"]))        # the check does read those strings


def test_redact_drops_attachments_named_after_a_term(run, work):
    m = _mupdf()
    src = make_pdf(work / "a.pdf", pages=1, text="Mario Rossi")
    with m.open(str(src)) as doc:
        doc.embfile_add("Rossi.txt", b"allegato riservato", filename="Rossi.txt", ufilename="Rossi.txt",
                        desc="di Rossi")
        doc.save(str(work / "a2.pdf"))
    job = done(run("pdf.privacy.redact", [work / "a2.pdf"], {"terms": ["Rossi"]}))
    out = outputs(job)[0]
    assert b"Rossi" not in out.read_bytes()
    with m.open(str(out)) as doc:
        assert doc.embfile_count() == 0


def test_redact_keeps_named_destinations_working(run, work):
    # a link to a named destination follows the rename of that destination
    m = _mupdf()
    src = make_pdf(work / "n.pdf", pages=2, text="Mario Rossi pagina {n}")
    with m.open(str(src)) as doc:
        names = doc.get_new_xref()
        doc.update_object(names, f"<</Names[(Rossi)<</D[{doc[1].xref} 0 R/Fit]>>]>>")
        doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</Dests {names} 0 R>>")
        doc[0].insert_link({"kind": m.LINK_NAMED, "from": m.Rect(72, 300, 300, 320), "name": "Rossi",
                            "nameddest": "Rossi"})
        doc.save(str(work / "n2.pdf"))
    job = done(run("pdf.privacy.redact", [work / "n2.pdf"], {"terms": ["Rossi"]}))
    with m.open(str(outputs(job)[0])) as doc:
        assert list(doc.resolve_names()) == ["█████"]
        assert doc.resolve_names()["█████"]["page"] == 1
        assert not any("Rossi" in doc.xref_object(x, compressed=True) for x in range(1, doc.xref_length()))
        assert len(doc[0].get_links()) == 1 and "Rossi" not in doc[0].get_links()[0]["nameddest"]


def test_redact_term_only_in_a_bookmark_is_found(run, work):
    src = make_pdf(work / "t.pdf", pages=1, text="Niente qui")
    m = _mupdf()
    with m.open(str(src)) as doc:
        doc.set_toc([[1, "Capitolo Rossi", 1]])
        doc.save(str(work / "t2.pdf"))
    job = done(run("pdf.privacy.redact", [work / "t2.pdf"], {"terms": ["Rossi"]}))
    with m.open(str(outputs(job)[0])) as doc:
        assert doc.get_toc()[0][1] == "Capitolo █████"


def test_redact_leaves_machine_strings_alone(run, work):
    # a font registry and an /Indexed colour table are not text to redact
    m = _mupdf()
    src = make_pdf(work / "f.pdf", pages=1, text="Adobe 12 Rossi")
    with m.open(str(src)) as doc:
        cid = doc.get_new_xref()
        doc.update_object(cid, "<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>")
        cs = doc.get_new_xref()
        doc.update_object(cs, "[/Indexed/DeviceRGB 1<41646F62650000>]")
        doc.xref_set_key(doc[0].xref, "Keep", f"[{cid} 0 R {cs} 0 R]")
        doc.save(str(work / "f2.pdf"))
    job = done(run("pdf.privacy.redact", [work / "f2.pdf"], {"terms": ["Adobe"]}))
    with m.open(str(outputs(job)[0])) as doc:
        objects = [doc.xref_object(x, compressed=True) for x in range(1, doc.xref_length())]
    assert any("/Registry(Adobe)" in o for o in objects)
    assert any(s[3] == "Adobe\0\0" for o in objects for s in P._object_strings(o))


def test_object_strings_tokenizer():
    src = ("<</Alt(a\\)b(c\\)))/K[<</T<FEFF0052>/Lang(it)>>]/ColorSpace[/Indexed/DeviceRGB 1<526F7373>]"
           "/Names[(x)<</D[1 0 R/Fit]>>(y)]/Limits[(x)(y)]/Title(t\\350)>>")
    assert [(s[2], s[3]) for s in P._object_strings(src)] == [
        ("Alt", "a)b(c))"), ("T", "R"), ("Lang", "it"), ("ColorSpace", "Ross"), ("Names", "x"), ("Names", "y"),
        ("Limits", "x"), ("Limits", "y"), ("Title", "tè")]
    for start, end, _key, _text in P._object_strings(src):
        assert src[start] in "(<" and src[end - 1] in ")>"
    assert list(P._object_strings("<</V<nothex>/Next(ok)>>"))[-1][3] == "ok"     # a bad token is skipped


def test_terms_pattern_finds_split_and_folded_forms():
    pat = P._terms_pattern(["Raffaele", "Mario Rossi", "Straße", "anti-virus"])
    for text in ("il Raf-\nfaele", "Raf\u00ad \n faele", "RA\ufb00AELE", "Mario\nRossi", "Mario\u00a0ROSSI", "STRASSE",
                 "anti-\nvirus", "Stras-\nse"):
        assert P._term_hit(pat, text), text
    for text in ("Raf-faele", "Raf faele", "Raf\nfaele", "antivirus", "Mario-\nRossi"):
        assert not P._term_hit(pat, text), text
    assert not P._term_hit(P._terms_pattern(["Rossi"], case_sensitive=True), "ROSSI")
    assert P._term_hit(P._terms_pattern(["Rossi"], case_sensitive=True), "Ros-\nsi")
    assert P._terms_pattern([]) is None and not P._term_hit(None, "x")


def test_strip_metadata(run, work):
    m = _mupdf()
    src = make_pdf(work / "doc.pdf", pages=1, meta={"author": "Anna", "title": "T", "producer": "X"})
    out = outputs(done(run("pdf.privacy.strip", [src])))[0]
    assert out.name == "doc (No Metadata).pdf"
    with m.open(str(out)) as doc:
        meta = doc.metadata or {}
        assert not meta.get("author") and not meta.get("title")
        assert not doc.get_xml_metadata()
    assert b"Anna" not in out.read_bytes()


def test_edit_metadata(run, work):
    m = _mupdf()
    src = make_pdf(work / "doc.pdf", pages=1, meta={"author": "Anna", "keywords": "a, b"})
    changes = {"set": {"PDF:Title": "Città è bella", "author": "Bruno", "creationDate": "2026-10-29"},
               "remove": ["Keywords"]}
    out = outputs(done(run("pdf.privacy.metadata", [src], {"metadata": changes})))[0]
    with m.open(str(out)) as doc:
        meta = doc.metadata
        assert meta["title"] == "Città è bella" and meta["author"] == "Bruno"
        assert not meta.get("keywords") and meta["creationDate"].startswith("D:20261029")
    job = run("pdf.privacy.metadata", [src], {"metadata": {"set": {"modDate": "ieri"}}})
    assert job.state == "failed" and "Invalid date" in job.error["message"]
    job = run("pdf.privacy.metadata", [src], {"metadata": {}})
    assert job.state == "failed"
    out = outputs(done(run("pdf.privacy.metadata", [src], {"metadata": {"remove_all": True}})))[0]
    with m.open(str(out)) as doc:
        assert not doc.metadata.get("author")


# ============================================================================ OCR / text

def test_copy_text(run, work):
    src = make_pdf(work / "doc.pdf", pages=2)
    job = done(run("pdf.ocr.copy_text", [src]))
    assert job.result["kind"] == "text" and "Page 2 secret ABC" in job.result["text"]


def test_copy_text_scan_without_tesseract(run, work):
    if tools.have("tesseract"):
        pytest.skip("tesseract installed")
    src = make_pdf(work / "scan.pdf", pages=1, text=" ")
    job = run("pdf.ocr.copy_text", [src])
    assert job.state == "failed" and job.error["install"]


@pytest.mark.skipif(not (tools.have("ocrmypdf") or tools.have("tesseract")), reason="OCR tools missing")
def test_ocr_searchable(run, work):
    src = make_pdf(work / "doc.pdf", pages=1)
    job = run("pdf.ocr.searchable", [src], {"language": "eng"})
    assert job.state in ("done", "failed")


# ============================================================================ more

def test_watermark(run, work):
    src = make_pdf(work / "doc.pdf", pages=2)
    job = done(run("pdf.watermark", [src], {"text": "BOZZA", "opacity": 30, "rotation": 45}))
    out = outputs(job)[0]
    assert out.name == "doc (Watermarked).pdf"
    assert all("BOZZA" in t for t in page_texts(out))
    out = outputs(done(run("pdf.watermark", [src], {"text": "Città €", "pages": "2", "layer": "under",
                                                   "rotation": 0, "position": "top-left"})))[0]
    texts = page_texts(out)
    assert "Città €" not in texts[0] and "Città €" in texts[1]


def test_watermark_on_rotated_page_is_upright(run, work):
    m = _mupdf()
    src = make_pdf(work / "rot.pdf", pages=1, rotate=90)
    out = outputs(done(run("pdf.watermark", [src], {"text": "STAMP", "rotation": 0})))[0]
    with m.open(str(out)) as doc:
        page = doc[0]
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                if "STAMP" in "".join(s["text"] for s in line["spans"]):
                    d = m.Point(line["dir"]) * page.rotation_matrix
                    assert d.x > 0.99 and abs(d.y) < 0.01
                    break


def test_page_numbers(run, work):
    src = make_pdf(work / "doc.pdf", pages=3)
    out = outputs(done(run("pdf.page_numbers", [src], {"format": "Pagina {n} di {total}"})))[0]
    assert out.name == "doc (Numbered).pdf"
    texts = page_texts(out)
    assert "Pagina 1 di 3" in texts[0] and "Pagina 3 di 3" in texts[2]
    out = outputs(done(run("pdf.page_numbers", [src], {"skip_first": True, "start": 1})))[0]
    texts = page_texts(out)
    assert "/" not in texts[0] and "1/2" in texts[1]


def test_flatten(run, work):
    m = _mupdf()
    src = make_pdf(work / "doc.pdf", pages=1)
    job = done(run("pdf.flatten", [src]))
    assert not outputs(job) and job.result["message"] == "Nothing to flatten"
    with m.open(str(src)) as doc:
        doc[0].add_freetext_annot(m.Rect(72, 300, 300, 340), "Nota a margine")
        doc.saveIncr()
    out = outputs(done(run("pdf.flatten", [src])))[0]
    with m.open(str(out)) as doc:
        assert not list(doc[0].annots()) and "Nota a margine" in doc[0].get_text()


def test_extract_images(run, work):
    src = make_pdf(work / "doc.pdf", pages=2, image=True)
    job = done(run("pdf.extract.images", [src]))
    folder = outputs(job)[0]
    assert folder.is_dir() and folder.name == "doc (Images)"
    assert len(list(folder.iterdir())) == 1               # same image on both pages: once
    job = run("pdf.extract.images", [make_pdf(work / "noimg.pdf", pages=1)])
    assert job.state == "failed"


def test_extract_attachments(run, work):
    m = _mupdf()
    src = make_pdf(work / "fattura.pdf", pages=1)
    with m.open(str(src)) as doc:
        doc.embfile_add("fattura.xml", b"<FatturaElettronica/>", filename="fattura.xml")
        doc.saveIncr()
    out = outputs(done(run("pdf.extract.attachments", [src])))[0]
    assert out.name == "fattura.xml" and out.read_bytes() == b"<FatturaElettronica/>"
    with m.open(str(src)) as doc:
        doc.embfile_add("../evil.txt", b"x", filename="../evil.txt")
        doc.saveIncr()
    folder = outputs(done(run("pdf.extract.attachments", [src])))[0]
    assert folder.is_dir() and sorted(p.name for p in folder.iterdir()) == ["evil.txt", "fattura.xml"]


def test_repair(run, work):
    src = make_pdf(work / "ok.pdf", pages=2)
    job = done(run("pdf.repair", [src]))
    if not outputs(job):
        assert job.result["message"] == "No problems found"
    broken = work / "rotto.pdf"
    data = src.read_bytes()
    broken.write_bytes(data[: data.rfind(b"xref")] + b"%%EOF\n")
    job = run("pdf.repair", [broken])
    if job.state == "done" and outputs(job):
        assert page_texts(outputs(job)[0])[0] == "Page 1 secret ABC"


def test_info(run, work):
    src = make_pdf(work / "doc.pdf", pages=3, meta={"producer": "Dropwheel test", "author": "Anna"})
    job = done(run("pdf.info", [src]))
    assert job.result["kind"] == "info"
    rows = {(r["group"], r["key"]): r["value"] for r in job.result["info"]}
    assert rows[("General", "Pages")] == "3"
    assert any("A4" in v for (g, k), v in rows.items() if k == "Page size")
    assert rows[("Document", "Producer")] == "Dropwheel test"
    assert rows[("Security", "Encryption")] == "None"
    locked = make_pdf(work / "locked.pdf", pages=1, encrypt="x")
    job = done(run("pdf.info", [locked]))
    assert any(r["value"] == "Password protected" for r in job.result["info"])


def test_print_and_annotate_report_missing_tools(run, work):
    src = make_pdf(work / "doc.pdf", pages=1)
    if not tools.have("lp"):
        job = run("pdf.print", [src])
        assert job.state == "failed" and "cups" in job.error["install"]
    if not (tools.have("xournalpp") or tools.have("okular")):
        job = run("pdf.annotate", [src])
        assert job.state == "failed" and "xournalpp" in job.error["install"]


def test_inputs_are_never_modified(run, work):
    src = make_pdf(work / "doc.pdf", pages=3, toc=True, image=True, meta={"author": "Anna"})
    before = sha(src)
    for action_id, params in (("pdf.convert.png", {"dpi": 30}), ("pdf.pages.reverse", {}),
                              ("pdf.split.pages", {}), ("pdf.watermark", {"text": "X"}),
                              ("pdf.privacy.strip", {}), ("pdf.page_numbers", {}), ("pdf.compress.balanced", {}),
                              ("pdf.extract.images", {}), ("pdf.info", {})):
        job = run(action_id, [src], params)
        assert job.state == "done", (action_id, job.error)
    assert sha(src) == before
    assert {p.name for p in work.iterdir()} >= {"doc.pdf"}


def test_italian_output_names(make_runner, work):
    runner, _events = make_runner(lang="it")
    src = make_pdf(work / "doc.pdf", pages=2)
    job = runner.run_sync("pdf.convert.png", [str(src)], {"dpi": 30})
    assert job.state == "done"
    assert Path(job.result["outputs"][0]["path"]).name == "doc (pagine)"
    job = runner.run_sync("pdf.rotate.cw", [str(src)], {})
    assert Path(job.result["outputs"][0]["path"]).name == "doc (ruotato).pdf"
    job = runner.run_sync("pdf.convert.png", [str(src)], {"pages": "9"})
    assert job.state == "failed" and "Selezione di pagine non valida" in job.error["message"]
    assert i18n.t("pdf.err.password_needed", "it", name="x.pdf").startswith("«x.pdf»")


# ============================================================================ external command lines

class FakeCtx:
    """Minimal stand-in for jobs.Context that records ``run`` calls instead of executing them."""

    def __init__(self, tmp: Path, stdout: dict | None = None):
        self._tmp = tmp
        self.calls: list[list[str]] = []
        self.files_read: dict[str, str] = {}
        self.stdout = stdout or {}
        self.lang = "en"
        self.params: dict = {}

    def tmpdir(self) -> Path:
        return self._tmp

    def t(self, key, **fmt):
        return key

    def progress(self, *a, **k):
        pass

    def check_cancel(self):
        pass

    def warn(self, msg):
        pass

    def run(self, argv, **kw):
        import subprocess

        args = [str(a) for a in argv]
        self.calls.append(args)
        for a in args:
            if a.startswith("@"):
                self.files_read[a[1:]] = Path(a[1:]).read_text(encoding="utf-8")
            if a.startswith("-sOutputFile="):
                Path(a.split("=", 1)[1].replace("%%", "%")).write_bytes(b"%PDF-1.7\n")
        out = next((v for k, v in self.stdout.items() if k in args), "")
        return subprocess.CompletedProcess(args, 0, out, "")


def test_gs_prelude_runs_after_output_file(tmp_path):
    ctx = FakeCtx(tmp_path)
    deffile = tmp_path / "PDFA_def.ps"
    src = tmp_path / "-in 100%.pdf"
    dst = tmp_path / "out 100%.pdf"
    P._run_gs(ctx, ["-dPDFA=2"], src, dst, 3, "x", prelude=[deffile])
    argv = ctx.calls[0]
    assert argv[0] == "gs" and "-dSAFER" in argv
    out_i = argv.index(f"-sOutputFile={str(dst).replace('%', '%%')}")
    def_i = argv.index(str(deffile))
    src_i = argv.index(str(src))
    assert out_i < def_i < src_i
    assert argv[def_i - 1] == "-f" and argv[src_i - 1] == "-f"


@pytest.mark.parametrize("backend", ["py:pymupdf", "py:pypdf"])
def test_pdfinfo_never_gets_the_password(work, monkeypatch, backend):
    pytest.importorskip(backend[3:])
    m = _mupdf()
    src = work / "locked.pdf"
    with m.open() as doc:
        doc.new_page().insert_text((72, 100), "riservato")
        # RC4: pypdf needs "cryptography" for AES
        doc.save(str(src), encryption=m.PDF_ENCRYPT_RC4_128, user_pw="segreto", owner_pw="padrone")
    monkeypatch.setattr(P, "_have", lambda name: name == backend)
    report = "Title:  x\nPages:  1\nEncrypted:  no\nFile size:  10 bytes\nProducer:   \n"
    ctx = FakeCtx(work, stdout={"pdfinfo": report})
    ctx.params = {"password": "segreto"}
    ctx.file = classify(str(src))
    rows = P._info_rows_pdfinfo(ctx, ctx.file)
    argv = ctx.calls[-1]
    assert argv[:2] == ["pdfinfo", "-isodates"] and "-upw" not in argv and "segreto" not in " ".join(argv)
    assert Path(argv[-1]) != src
    with m.open(argv[-1]) as doc:
        assert not doc.needs_pass and "riservato" in doc[0].get_text()
    values = {r["key"]: r["value"] for r in rows}
    assert values == {"Title": "x", "Pages": "1", "Encrypted": "yes", "File size": f"{src.stat().st_size} bytes"}


def test_pdfinfo_reads_files_without_an_open_password_directly(work, monkeypatch):
    pytest.importorskip("pypdf")
    src = make_pdf(work / "plain.pdf", pages=1)
    monkeypatch.setattr(P, "_have", lambda name: name == "py:pypdf")
    ctx = FakeCtx(work, stdout={"pdfinfo": "Encrypted:  no\nFile size:  10 bytes\n"})
    ctx.file = classify(str(src))
    rows = P._info_rows_pdfinfo(ctx, ctx.file)
    assert ctx.calls[-1] == ["pdfinfo", "-isodates", str(src)]
    assert {r["key"]: r["value"] for r in rows} == {"Encrypted": "no", "File size": "10 bytes"}


@pytest.mark.parametrize("password,expected", [("", "pdf.err.password_needed"), ("segreto", "err.missing_tool")])
def test_pdfinfo_on_a_locked_file_without_any_library(work, monkeypatch, password, expected):
    from dropwheel.errors import ToolError, UserError

    src = make_pdf(work / "locked.pdf", pages=1, encrypt="segreto")
    monkeypatch.setattr(P, "_have", lambda name: False)
    ctx = FakeCtx(work)
    ctx.params = {"password": password}
    ctx.file = classify(str(src))

    def refuse(argv, **kw):
        ctx.calls.append([str(a) for a in argv])
        raise ToolError("err.tool_failed", details="Command Line Error: Incorrect password")

    ctx.run = refuse
    with pytest.raises(UserError) as exc:
        P._info_rows_pdfinfo(ctx, ctx.file)
    assert exc.value.message == expected
    assert all("segreto" not in " ".join(c) for c in ctx.calls)


def test_qpdf_rebuild_command(tmp_path, monkeypatch):
    ctx = FakeCtx(tmp_path, stdout={"--show-npages": "3\n"})
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    specs = [P._Spec(2), P._Spec(0, 90), P._Spec(None), P._Spec(1, 180)]
    P._rebuild_qpdf(ctx, src, specs, dst)
    argv = ctx.calls[-1]
    assert argv[0] == "qpdf"
    assert "--rotate=+90:2" in argv and "--rotate=+180:4" in argv
    pages = argv[argv.index("--pages") + 1: argv.index("--")]
    assert pages[:2] == [".", "3,1"] and pages[3] == "1" and pages[4:] == [".", "2"]
    assert Path(pages[2]).read_bytes().startswith(b"%PDF")      # blank page file
    assert argv[-1] == str(dst) and argv.index(str(src)) < argv.index("--pages")


def test_qpdf_encrypt_uses_argument_file(tmp_path, monkeypatch):
    ctx = FakeCtx(tmp_path)
    monkeypatch.setattr(P, "_have", lambda name: name == "qpdf")
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    P._encrypt_to(ctx, src, dst, user="pass word", owner="", perms=P.Perms(copy=False))
    argv = ctx.calls[-1]
    assert argv[0] == "qpdf" and argv[1].startswith("@")
    lines = list(ctx.files_read.values())[0].splitlines()
    assert lines[:2] == ["--encrypt", "--user-password=pass word"]
    assert lines[2].startswith("--owner-password=") and len(lines[2]) > len("--owner-password=") + 10
    assert "--bits=256" in lines and "--extract=n" in lines
    assert lines[-3:] == ["--", str(src), str(dst)]
    assert not Path(argv[1][1:]).exists()                         # password file removed


# ============================================================================ backend fallbacks

def test_pypdf_fallback_for_page_assembly(run, work, monkeypatch):
    pytest.importorskip("pypdf")
    src = make_pdf(work / "doc.pdf", pages=3)
    monkeypatch.setattr(P, "_DISABLED", {"py:pymupdf", "py:pikepdf"})
    monkeypatch.setattr(P, "_have", lambda name: name in ("py:pypdf",))
    out = outputs(done(run("pdf.pages.extract", [src], {"pages": "3,1"})))[0]
    monkeypatch.setattr(P, "_DISABLED", set())
    monkeypatch.undo()
    assert page_texts(out) == ["Page 3 secret ABC", "Page 1 secret ABC"]


def test_pypdf_fallback_rotation_and_blank(run, work, monkeypatch):
    pytest.importorskip("pypdf")
    src = make_pdf(work / "doc.pdf", pages=2)
    monkeypatch.setattr(P, "_have", lambda name: name in ("py:pypdf",))
    out = outputs(done(run("pdf.pages.blank", [src], {"position": "end"})))[0]
    out2 = outputs(done(run("pdf.rotate.cw", [src])))[0]
    monkeypatch.undo()
    m = _mupdf()
    with m.open(str(out)) as doc:
        assert doc.page_count == 3 and doc[2].get_text().strip() == ""
    with m.open(str(out2)) as doc:
        assert [p.rotation for p in doc] == [90, 90]


def test_pypdf_prepare_encrypted(run, work, monkeypatch):
    pytest.importorskip("pypdf")
    src = make_pdf(work / "locked.pdf", pages=1, encrypt="pw")
    monkeypatch.setattr(P, "_have", lambda name: name in ("py:pypdf",))
    job = run("pdf.pages.reverse", [src])
    assert job.state == "failed"
    try:
        import cryptography  # noqa: F401
    except ImportError:
        # pypdf needs "cryptography" for AES-256: reported as a missing dependency with its package
        assert "python-cryptography" in job.error["install"]
        return
    assert "password protected" in job.error["message"]
    job = run("pdf.pages.reverse", [src], {"password": "bad"})
    assert job.state == "failed" and "Wrong password" in job.error["message"]
    job = done(run("pdf.pages.reverse", [src], {"password": "pw"}))
    assert outputs(job)
