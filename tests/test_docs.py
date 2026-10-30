"""Tests for the document actions (dropwheel.actions.docs and its helpers _docs_formats / _docs_engines).

Pure logic is tested directly; actions run through the real JobRunner on files created in the
test's work directory.  Everything that needs LibreOffice, pandoc, calibre, typst, WeasyPrint or
wl-copy is skipped when the tool is missing (LibreOffice is exercised through a fake ``ctx.run``).
"""

from __future__ import annotations

import base64
import importlib
import io
import json
import os
import plistlib
import re
import tomllib
import zipfile
from pathlib import Path

import pytest

from dropwheel import i18n, registry, tools
from dropwheel.errors import MissingTool, ToolError, UserError
from dropwheel.jobs import Context

import dropwheel.actions.docs as docs_mod
from dropwheel.actions import _docs_engines as E
from dropwheel.actions import _docs_formats as F

ROOT = Path(__file__).resolve().parent.parent / "src" / "dropwheel"


# ================================================================================== fixtures

@pytest.fixture(autouse=True)
def docs_registered():
    """Make sure the docs actions are registered (other tests may have cleared the registry)."""
    if registry.get("data.pretty") is None or registry.get("text.stats") is None:
        importlib.reload(docs_mod)
    yield


@pytest.fixture(autouse=True)
def posix_newlines(monkeypatch):
    """Write test files (and pages produced by the code under test) without newline translation,
    as on the Linux target, so byte-exact assertions hold on every platform."""
    original = Path.write_text

    def write_text(self, data, encoding=None, errors=None, newline=""):
        return original(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", write_text)
    yield


@pytest.fixture
def work(dropwheel_env) -> Path:
    return dropwheel_env.work


def run(make_runner, action_id, files, params=None, lang="en"):
    runner, events = make_runner(lang=lang)
    job = runner.run_sync(action_id, [str(f) for f in files], params or {})
    return job


def ok(job):
    assert job.state == "done", (job.state, job.error)
    return job.result


def out_paths(job) -> list[Path]:
    return [Path(o["path"]) for o in (job.result or {}).get("outputs", [])]


def make_ctx(make_runner, action_id, files, params=None):
    runner, _events = make_runner()
    job = runner._create(action_id, [str(f) for f in files], params or {})
    job.params = job.action.resolve_params(job.files, job.given_params, runner.config)
    return Context(runner, job, job.files)


def need(*names: str) -> None:
    for name in names:
        if not tools.have(name):
            pytest.skip(f"{name} not installed")


def jpeg_bytes(size=(1600, 1200), quality=95) -> bytes:
    pil = pytest.importorskip("PIL.Image")
    import random

    rnd = random.Random(1)
    im = pil.new("RGB", size)
    px = im.load()
    for x in range(0, size[0], 4):
        for y in range(0, size[1], 4):
            c = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
            for dx in range(4):
                for dy in range(4):
                    if x + dx < size[0] and y + dy < size[1]:
                        px[x + dx, y + dy] = c
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def png_bytes(size=(40, 30), color=(200, 30, 30)) -> bytes:
    pil = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    pil.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(path: Path, *, paragraphs=("Ciao mondo", "Seconda riga di testo"), image: bytes | None = None) -> Path:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    body += ('<w:p><w:ins w:id="1" w:author="Mario Rossi" w:date="2024-05-01T10:00:00Z">'
             '<w:r><w:t>aggiunto</w:t></w:r></w:ins></w:p>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" Target="docProps/custom.xml"/>'
                   "</Relationships>")
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Mario Rossi</dc:creator>'
                   "<dc:title>Segreto</dc:title></cp:coreProperties>")
        z.writestr("docProps/app.xml",
                   '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                   "<Template>C:\\Users\\mario\\Normal.dotm</Template><TotalTime>42</TotalTime><Pages>3</Pages>"
                   "<Words>120</Words><Company>ACME Spa</Company></Properties>")
        z.writestr("docProps/custom.xml", "<Properties><property name='Cliente'>Bianchi</property></Properties>")
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        z.writestr("word/comments.xml", f'<w:comments xmlns:w="{W}"><w:comment w:id="0" w:author="Mario Rossi" '
                                        f'w:initials="MR" w:date="2024-05-01T10:00:00Z"><w:p/></w:comment></w:comments>')
        z.writestr("word/settings.xml", f'<w:settings xmlns:w="{W}"><w:attachedTemplate r:id="rId1"/></w:settings>')
        z.writestr("word/fontTable.xml", f'<w:fonts xmlns:w="{W}"><w:font w:name="Calibri"/><w:font w:name="Times New Roman"/>'
                                         f'<w:font w:name="+mj-lt"/></w:fonts>')
        if image is not None:
            z.writestr("word/media/image1.jpeg", image)
            z.writestr("word/media/image2.png", png_bytes())
    return path


P_NS = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
REL_NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def _sp(ph: str | None, *texts: str) -> str:
    phx = f'<p:ph type="{ph}"/>' if ph else ""
    paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
    return f"<p:sp><p:nvSpPr><p:cNvPr id='1' name='x'/><p:cNvSpPr/><p:nvPr>{phx}</p:nvPr></p:nvSpPr><p:txBody>{paras}</p:txBody></p:sp>"


def make_pptx(path: Path, *, image: bytes | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/presentation.xml", f"<p:presentation {P_NS}><p:sldIdLst><p:sldId id='256' r:id='rId2'/>"
                                           f"<p:sldId id='257' r:id='rId3'/></p:sldIdLst></p:presentation>")
        z.writestr("ppt/_rels/presentation.xml.rels",
                   f"<Relationships {REL_NS}><Relationship Id='rId3' Type='x/slide' Target='slides/slide2.xml'/>"
                   f"<Relationship Id='rId2' Type='x/slide' Target='slides/slide1.xml'/></Relationships>")
        z.writestr("ppt/slides/slide1.xml", f"<p:sld {P_NS}><p:cSld><p:spTree>{_sp('title', 'Benvenuti')}"
                                            f"{_sp('body', 'Primo punto', 'Secondo punto')}{_sp('sldNum', '1')}"
                                            f"</p:spTree></p:cSld></p:sld>")
        z.writestr("ppt/slides/slide2.xml", f"<p:sld {P_NS} show='0'><p:cSld><p:spTree>{_sp('title', 'Fine')}"
                                            f"</p:spTree></p:cSld></p:sld>")
        z.writestr("ppt/slides/_rels/slide1.xml.rels",
                   f"<Relationships {REL_NS}><Relationship Id='rId1' "
                   f"Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide' "
                   f"Target='../notesSlides/notesSlide1.xml'/></Relationships>")
        z.writestr("ppt/notesSlides/notesSlide1.xml", f"<p:notes {P_NS}><p:cSld><p:spTree>{_sp('sldImg')}"
                                                      f"{_sp('body', 'Ricordati di salutare')}</p:spTree></p:cSld></p:notes>")
        z.writestr("docProps/core.xml", "<cp:coreProperties xmlns:cp='x' xmlns:dc='http://purl.org/dc/elements/1.1/'>"
                                        "<dc:creator>Anna</dc:creator></cp:coreProperties>")
        if image is not None:
            z.writestr("ppt/media/image1.jpeg", image)
    return path


ODF = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
       'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
       'xmlns:dc="http://purl.org/dc/elements/1.1/" '
       'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"')


def make_odt(path: Path) -> Path:
    content = (f'<?xml version="1.0"?><office:document-content {ODF}><office:body><office:text>'
               '<text:h>Titolo</text:h><text:p>Uno<text:s text:c="2"/>due<text:tab/>tre</text:p>'
               '<text:p>Nota <office:annotation><dc:creator>Luca</dc:creator><dc:date>2024-01-01T00:00:00</dc:date>'
               '<text:p>commento</text:p></office:annotation>fine</text:p>'
               "</office:text></office:body></office:document-content>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml", "<manifest/>")
        z.writestr("content.xml", content)
        z.writestr("meta.xml", f'<office:document-meta {ODF}><office:meta><meta:initial-creator>Luca</meta:initial-creator>'
                               '<meta:document-statistic meta:word-count="5" meta:page-count="1"/></office:meta>'
                               "</office:document-meta>")
    return path


def make_epub(path: Path, *, drm: bool = False) -> Path:
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Il Libro</dc:title>'
           "<dc:creator>Italo Calvino</dc:creator><dc:language>it</dc:language><dc:publisher>Einaudi</dc:publisher>"
           "<dc:description>&lt;p&gt;Una &lt;b&gt;storia&lt;/b&gt;&lt;/p&gt;</dc:description>"
           '<meta name="calibre:series" content="Romanzi"/></metadata>'
           '<manifest><item id="c" href="images/cover image.png" media-type="image/png" properties="cover-image"/>'
           '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
           '<spine><itemref idref="ch1"/></spine></package>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/images/cover image.png", png_bytes((60, 90)))
        z.writestr("OEBPS/ch1.xhtml", "<html><body><p>Testo</p></body></html>")
        if drm:
            z.writestr("META-INF/encryption.xml",
                       '<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>'
                       "</EncryptedData></encryption>")
    return path


# =============================================================================== text helpers

def test_decode_bytes_variants():
    assert F.decode_bytes("perché".encode("utf-8")).is_utf8
    d = F.decode_bytes("\ufeffciao".encode("utf-8"))
    assert d.text == "ciao" and d.bom and d.label == "UTF-8 (BOM)" and not d.is_utf8
    d = F.decode_bytes("caffè già".encode("cp1252"))
    assert d.text == "caffè già" and d.encoding == "cp1252"
    d = F.decode_bytes("ciao mondo".encode("utf-16-le"))
    assert d.text == "ciao mondo" and d.encoding == "utf-16-le"
    d = F.decode_bytes(b"\xff\xfe" + "è".encode("utf-16-le"))
    assert d.text == "è" and d.bom
    assert F.encoding_label("cp1252") == "Windows-1252"


ITALIAN = "Perché la città è così bella? Caffè e più.\n"


def test_decode_utf8_with_a_stray_legacy_byte(monkeypatch):
    # one "€" pasted from a Windows-1252 source must not turn every "è" into "Ã¨"
    d = F.decode_bytes((ITALIAN * 5).encode("utf-8") + b"Prezzo: 10\x80\n")
    assert d.text == ITALIAN * 5 + "Prezzo: 10€\n"
    assert d.encoding == "utf-8" and d.repaired and not d.confident and not d.is_utf8
    assert d.label == "UTF-8 + Windows-1252"
    d = F.decode_bytes(b"\xef\xbb\xbf" + "città".encode("utf-8") + b"\xe8")
    assert d.text == "cittàè" and d.bom and d.repaired
    # the valid sequences are counted over the whole file, not just the detection sample
    monkeypatch.setattr(F, "DETECT_SAMPLE", 16)
    d = F.decode_bytes(b"x" * 64 + ITALIAN.encode("utf-8") + b"\x80")
    assert d.text == "x" * 64 + ITALIAN + "€" and d.repaired
    monkeypatch.undo()
    # plain Windows-1252 stays what it is: only a guess when a few bytes also look like UTF-8
    d = F.decode_bytes("caffè già più".encode("cp1252"))
    assert (d.text, d.encoding, d.confident, d.repaired) == ("caffè già più", "cp1252", True, False)
    d = F.decode_bytes("caffè già più".encode("cp1252") + "è".encode("utf-8"))
    assert d.text == "caffè già piùÃ¨" and d.encoding == "cp1252" and not d.confident and not d.repaired
    big = F.decode_bytes((ITALIAN * 200).encode("cp1252"))
    assert big.text == ITALIAN * 200 and big.encoding == "cp1252" and not big.repaired


def test_decode_short_foreign_scripts_are_not_windows_1252():
    pytest.importorskip("charset_normalizer")
    text = "Привет, как дела? Всё хорошо"
    assert F.decode_bytes(text.encode("cp1251")).text == text
    text = "Γειά σου Κόσμε, τι κάνεις;"
    assert F.decode_bytes(text.encode("cp1253")).text == text
    # few letters prove nothing: short Italian words remain Windows-1252
    for text in ("èàù", "è più già così", "più è già"):
        d = F.decode_bytes(text.encode("cp1252"))
        assert d.text == text and d.encoding == "cp1252"


def test_stray_legacy_byte_warns_and_is_repaired(make_runner, work):
    src = work / "misto.txt"
    src.write_bytes(ITALIAN.encode("utf-8") * 3 + b"10\x80\n")
    job = run(make_runner, "text.encoding.utf8", [src], lang="it")
    res = ok(job)
    assert out_paths(job)[0].read_text("utf-8") == ITALIAN * 3 + "10€\n"
    assert "UTF-8 + Windows-1252" in res["message"]
    assert any("Codifica stimata" in w for w in job.warnings), job.warnings


def test_eol_and_trailing():
    assert F.detect_eol("a\r\nb\r\n") == "CRLF"
    assert F.detect_eol("a\nb") == "LF"
    assert F.detect_eol("a\r\nb\n") == "mixed"
    assert F.detect_eol("abc") == "none"
    assert F.convert_eol("a\r\nb\rc\n", "crlf") == "a\r\nb\r\nc\r\n"
    assert F.convert_eol("a\r\nb\rc", "lf") == "a\nb\nc"
    assert F.strip_trailing("a  \nb\t\r\nc ") == ("a\nb\r\nc", 3)
    text, n = F.strip_trailing("riga  \nfine   \n", markdown=True)
    assert text == "riga\\\nfine\\\n" and n == 2


def test_text_stats_and_words():
    s = F.text_stats("Ciao l'amico — come stai?\n\nBene.\n")
    assert s["words"] == 5 and s["lines"] == 3 and s["paragraphs"] == 2 and s["blank_lines"] == 1
    assert F.count_words("— | 123 abc") == 2


MD = """---
title: Prova
---
# Titolo

Intro con **grassetto** e [link](https://example.com) e ![foto](img.png).

## Primo *punto*

```python
# non è un titolo
print("x")
```

Secondo
-------

### Dettagli
"""


def test_markdown_headings_toc_and_insert():
    heads = F.markdown_headings(MD)
    assert heads == [(1, "Titolo"), (2, "Primo punto"), (2, "Secondo"), (3, "Dettagli")]
    toc = F.toc_markdown(heads)
    assert toc.splitlines() == ["- [Primo punto](#primo-punto)", "- [Secondo](#secondo)", "  - [Dettagli](#dettagli)"]
    inserted = F.insert_toc(MD, toc, title="Indice")
    assert inserted.startswith("---\ntitle: Prova\n---\n# Titolo\n\n<!-- toc -->")
    assert inserted.count("<!-- toc -->") == 1
    again = F.insert_toc(inserted, "- [X](#x)")
    assert again.count("<!-- toc -->") == 1 and "- [X](#x)" in again and "primo-punto" not in again.split("tocstop")[0]
    assert F.slugify("Perché è così?", {}) == "perché-è-così"
    used: dict = {}
    assert [F.slugify("A", used), F.slugify("A", used)] == ["a", "a-1"]


def test_strip_markdown():
    plain = F.strip_markdown(MD)
    assert "Intro con grassetto e link e foto." in plain
    assert "# non è un titolo" in plain and "```" not in plain and "title: Prova" not in plain
    assert "Titolo" in plain and "-------" not in plain


def test_rtf_to_text():
    rtf = r"{\rtf1\ansi\ansicpg1252{\fonttbl{\f0 Arial;}}{\*\generator X;}\f0 Caff\'e8 \b bello\b0\par Euro \u8364? fine\par}"
    assert F.rtf_to_text(rtf) == "Caffè bello\nEuro € fine\n"


# ===================================================================================== tables

def test_csv_sniff_read_infer(tmp_path):
    p = tmp_path / "a.csv"
    p.write_bytes("nome;cap;importo\nMario Città;00123;3,50\nLuisa;20100;10\n".encode("cp1252"))
    rows, delim, dec = F.read_csv_rows(p, "csv")
    assert delim == ";" and rows[1] == ["Mario Città", "00123", "3,50"] and dec.encoding == "cp1252"
    recs = F.records_from_rows(rows, infer=True, decimal_comma=True)
    assert recs[0] == {"nome": "Mario Città", "cap": "00123", "importo": 3.5}
    assert recs[1]["cap"] == 20100
    assert F.infer_cell("007") == "007" and F.infer_cell("1e3") == 1000.0 and F.infer_cell("") is None
    assert F.sniff_delimiter("a\tb\n1\t2\n", "") == "\t"
    assert F.header_names(["a", "", "a"]) == ["a", "column_2", "a_2"]


@pytest.mark.parametrize("text,expected", [
    ("1.250", 1250), ("12.345", 12345), ("-1.250", -1250), ("1.234.567", 1234567), ("3,5", 3.5), ("0,5", 0.5),
    ("1.234,56", 1234.56), ("1.234.567.890,12", 1234567890.12), ("10", 10), ("0", 0),
    # a dot that is not a thousands separator, or a code: stays text, never read as a decimal point
    ("1.25", "1.25"), ("0.5", "0.5"), ("01.250", "01.250"), ("1.2.3", "1.2.3"), ("12.34.56", "12.34.56"),
    ("0012", "0012"), ("192.168.100.200", "192.168.100.200"), ("10.0.0.1", "10.0.0.1"), ("1.000,", "1.000,"),
])
def test_infer_cell_italian_numbers(text, expected):
    value = F.infer_cell(text, decimal_comma=True)
    assert value == expected and type(value) is type(expected)


def test_infer_italian_thousands_per_column():
    # the finding: "1.250" is one thousand two hundred and fifty, not 1.25
    assert F.infer_cell("1.250") == 1.25  # without the ";" hint the dot stays a decimal point
    assert F.records_from_rows([["a", "b"], ["1.250", "0012"]], infer=True, decimal_comma=True) == [{"a": 1250, "b": "0012"}]
    rows = [["qta", "importo", "peso", "ip"],
            ["1.250", "1.234,56", "1.5", "192.168.100.200"],
            ["12.345", "10", "2.250", "10.0.0.1"]]
    assert F.infer_rows(rows, decimal_comma=True)[1:] == [[1250, 1234.56, 1.5, "192.168.100.200"],
                                                          [12345, 10, 2.25, "10.0.0.1"]]
    # a ";" CSV written with "." decimals keeps them: the column proves it
    assert F.decimal_comma_columns([["3.5"], ["1.250"]], True) == [False]
    assert F.decimal_comma_columns([["2.000"], ["1.250"]], True) == [True]
    assert F.decimal_comma_columns([["1.5"]], False) == [False]


def test_italian_thousands_through_the_actions(make_runner, work):
    src = work / "ordini.csv"
    src.write_bytes("articolo;qta;importo\nviti;1.250;1.234,56\nbulloni;12.345;10\n".encode("utf-8"))
    job = run(make_runner, "spreadsheet.convert.json", [src])
    ok(job)
    assert json.loads(out_paths(job)[0].read_text("utf-8")) == [
        {"articolo": "viti", "qta": 1250, "importo": 1234.56}, {"articolo": "bulloni", "qta": 12345, "importo": 10}]
    pytest.importorskip("openpyxl")
    job = run(make_runner, "spreadsheet.merge.xlsx", [src, src])
    ok(job)
    assert F.read_xlsx(out_paths(job)[0])[0][1][1] == ["viti", 1250, 1234.56]


def test_markdown_and_html_tables():
    md = F.markdown_table([["Nome", "Valore"], ["a|b", 3], ["c", 4.5]])
    assert md.splitlines() == ["| Nome | Valore |", "| --- | ---: |", "| a\\|b | 3 |", "| c | 4.5 |"]
    page = F.html_tables_document([("S1", [["h"], ["<x>"]]), ("S2", [])], title="T & C", lang="it")
    assert "<h2>S1</h2>" in page and "&lt;x&gt;" in page and "T &amp; C" in page and 'lang="it"' in page


def test_ods_roundtrip(tmp_path):
    import datetime as dt

    p = tmp_path / "x.ods"
    rows = [["nome", "n", "ok", "data"], ["  due spazi", 3, True, dt.date(2024, 5, 1)], [None, 2.5, None, "a\nb"]]
    F.write_ods(p, [("Foglio 1", rows), ("Vuoto", [])])
    with zipfile.ZipFile(p) as z:
        assert z.infolist()[0].filename == "mimetype" and z.infolist()[0].compress_type == zipfile.ZIP_STORED
    sheets = F.read_ods(p)
    assert sheets[0][0] == "Foglio 1"
    assert sheets[0][1] == [["nome", "n", "ok", "data"], ["  due spazi", 3, True, dt.date(2024, 5, 1)],
                            [None, 2.5, None, "a\nb"]]
    assert sheets[1] == ("Vuoto", [])


def test_xlsx_roundtrip_formula_safety(tmp_path):
    pytest.importorskip("openpyxl")
    p = tmp_path / "x.xlsx"
    F.write_xlsx(p, [("a/b:c*?", [["h1", "h2"], ["=1+1", 5]]), ("a/b:c*?", [["x"]])])
    sheets = F.read_xlsx(p)
    assert [s[0] for s in sheets] == ["a b c", "a b c (2)"]
    assert sheets[0][1] == [["h1", "h2"], ["=1+1", 5]]
    assert F.unique_sheet_names(["x" * 40, "x" * 40])[1].endswith("(2)")


def _rewrite_zip(src: Path, dst: Path, edit) -> None:
    """Copy a ZIP package, ``edit(name, data) -> data`` applied to each part."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, edit(info.filename, zin.read(info)))


@pytest.mark.parametrize("declared", [b"A1", b"A1:B2", None])
def test_read_xlsx_does_not_trust_the_declared_dimension(tmp_path, declared):
    # Apache POI streaming and many ERP/bank exports declare <dimension ref="A1"/>
    openpyxl = pytest.importorskip("openpyxl")
    full = tmp_path / "full.xlsx"
    wb = openpyxl.Workbook()
    for row in (["a", "b", "c"], [1, 2, 3], ["x"], [4, 5, 6], [7, 8, 9, "oltre"]):
        wb.active.append(row)
    wb.save(full)
    expected = [["a", "b", "c"], [1, 2, 3], ["x"], [4, 5, 6], [7, 8, 9, "oltre"]]
    assert F.read_xlsx(full)[0][1] == expected

    def edit(name: str, data: bytes) -> bytes:
        if name.startswith("xl/worksheets/"):
            new = b"" if declared is None else b'<dimension ref="' + declared + b'"/>'
            data, n = re.subn(rb"<dimension\b[^>]*/>", new, data)
            assert n == 1
        return data

    lying = tmp_path / "lying.xlsx"
    _rewrite_zip(full, lying, edit)
    assert F.read_xlsx(lying)[0][1] == expected


# ============================================================================ structured data

def test_load_json_family(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"a": 1, "a": 2, "b": NaN}', encoding="utf-8")
    value, fmt, warns = F.load_data(p, "json")
    assert value["a"] == 2 and fmt == "json" and warns[0][0] == "docs.warn.duplicate_keys"
    assert json.loads(F.dump_data(value, "json")) == {"a": 2, "b": None}
    c = tmp_path / "a.jsonc"
    c.write_text('{\n // c\n "u": "http://x//y", /* b */ "l": [1, 2,],\n}', encoding="utf-8")
    assert F.load_data(c, "jsonc")[0] == {"u": "http://x//y", "l": [1, 2]}
    j5 = tmp_path / "a.json5"
    j5.write_text("{a: 1, 'b': 'it\\'s', c: 0x1F, d: .5, e: +3, f: [1,2,], g: Infinity}", encoding="utf-8")
    assert F.load_data(j5, "json5")[0] == {"a": 1, "b": "it's", "c": 31, "d": 0.5, "e": 3, "f": [1, 2], "g": float("inf")}
    nd = tmp_path / "a.ndjson"
    nd.write_text('{"x": 1}\n\n{"x": 2\n', encoding="utf-8")
    with pytest.raises(F.FormatError) as exc:
        F.load_data(nd, "ndjson")
    assert exc.value.key == "docs.err.parse_at" and exc.value.fmt["line"] == 3


def test_json_error_position(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{\n  "a": 1,\n  "b": \n}', encoding="utf-8")
    with pytest.raises(F.FormatError) as exc:
        F.load_data(p, "json")
    assert exc.value.fmt["line"] == 4 and exc.value.fmt["col"] == 1


def test_yaml_norway_and_dump(tmp_path):
    pytest.importorskip("yaml")
    p = tmp_path / "a.yaml"
    p.write_text("country: no\nflag: yes\nok: true\nwhen: 2024-05-01\n", encoding="utf-8")
    value, _fmt, _w = F.load_data(p, "yaml")
    assert value["country"] == "no" and value["flag"] == "yes" and value["ok"] is True
    out = F.dump_data(value, "yaml")
    assert "country: 'no'" in out
    assert json.loads(F.dump_data(value, "json"))["when"] == "2024-05-01"


@pytest.mark.parametrize("data", [
    {"a": 1, "b": "x\"y\n", "c": [1, 2.5, True], "d": {"e": {"f": "g"}}},
    {"arr": [{"n": 1}, {"n": 2, "sub": {"z": False}}], "empty": {}, "key with space": "v"},
    {"mixed": [1, "a", {"k": "v"}], "dt": __import__("datetime").datetime(2024, 1, 2, 3, 4, 5)},
])
def test_toml_writer_roundtrip(data, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_tomli_w(name, *args, **kwargs):
        if name == "tomli_w":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_tomli_w)
    text = F.toml_dumps(data)
    assert tomllib.loads(text) == data


def test_toml_errors():
    with pytest.raises(F.FormatError) as exc:
        F.toml_dumps({"a": {"b": [1, None]}})
    assert exc.value.key == "docs.err.toml_null" and exc.value.fmt["path"] == "$.a.b[1]"
    with pytest.raises(F.FormatError):
        F.toml_dumps([1, 2])
    warns: list = []
    assert tomllib.loads(F.dump_data([{"a": 1}], "toml", warns)) == {"items": [{"a": 1}]}
    assert warns[0][0] == "docs.warn.toml_wrapped"


def test_xml_mapping_and_safety(tmp_path):
    p = tmp_path / "a.xml"
    p.write_text('<?xml version="1.0"?><cat xmlns:x="u"><item id="1">a</item><item id="2"><x:n>b</x:n></item>'
                 "<empty/></cat>", encoding="utf-8")
    value, _fmt, _w = F.load_data(p, "xml")
    assert value == {"cat": {"item": [{"@id": "1", "#text": "a"}, {"@id": "2", "n": "b"}], "empty": ""}}
    xml = F.obj_to_xml(value)
    assert '<item id="1">a</item>' in xml and xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert F.obj_to_xml({"1 bad name": [1, 2]}).count("<_1_bad_name>") == 2
    evil = tmp_path / "evil.xml"
    evil.write_text('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><l>&b;</l>', encoding="utf-8")
    with pytest.raises(F.FormatError) as exc:
        F.load_data(evil, "xml")
    assert exc.value.key == "docs.err.xml_entities"


def test_xml_pretty_and_minify():
    src = (b'<?xml version="1.0"?><!-- c --><a xmlns:x="u"><x:b k="1">t</x:b><c><d/></c>'
           b"<p>mixed <b>bold</b> text</p><s xml:space=\"preserve\">  keep  </s></a>")
    pretty = F.xml_pretty(src)
    assert "<!-- c -->" in pretty and '  <x:b k="1">t</x:b>' in pretty and "    <d/>" in pretty
    assert "<p>mixed <b>bold</b> text</p>" in pretty and '<s xml:space="preserve">  keep  </s>' in pretty
    mini = F.xml_minify(b"<a>\n  <b> t </b>\n  <!-- c -->\n</a>")
    assert mini == '<?xml version="1.0" encoding="UTF-8"?><a><b>t</b></a>\n'


def test_ini_env_properties_plist(tmp_path):
    ini = tmp_path / "a.ini"
    ini.write_text("top=1\n[Sez]\nChiave = valore\nvuota\n", encoding="utf-8")
    assert F.load_data(ini, "ini")[0] == {"top": "1", "Sez": {"Chiave": "valore", "vuota": ""}}
    env = tmp_path / ".env"
    env.write_text('export A=1\nB="due\\nrighe" \nC=x # commento\n# no\n', encoding="utf-8")
    assert F.load_data(env, "", name=".env")[0] == {"A": "1", "B": "due\nrighe", "C": "x"}
    prop = tmp_path / "a.properties"
    prop.write_text("a.b = c\\u00e8\nlong = uno \\\n    due\n! comment\n", encoding="utf-8")
    assert F.load_data(prop, "properties")[0] == {"a.b": "cè", "long": "uno due"}
    pl = tmp_path / "a.plist"
    pl.write_bytes(plistlib.dumps({"k": [1, "x"], "d": b"\x00\x01"}, fmt=plistlib.FMT_BINARY))
    value = F.load_data(pl, "plist")[0]
    assert value["k"] == [1, "x"]
    assert json.loads(F.dump_data(value, "json"))["d"] == base64.b64encode(b"\x00\x01").decode()


def test_rows_from_data_and_sort():
    rows = F.rows_from_data({"items": [{"a": 1, "b": {"c": 2}}, {"a": 3, "d": [1, 2]}]})
    assert rows == [["a", "b.c", "d"], [1, 2, None], [3, None, "[1, 2]"]]
    assert F.rows_from_data([1, 2]) == [["value"], [1], [2]]
    assert list(F.sort_keys({"b": 1, "A": {"z": 1, "a": 2}})) == ["A", "b"]
    assert list(F.sort_keys({"b": 1, "A": {"z": 1, "a": 2}})["A"]) == ["a", "z"]


# ================================================================================ office zips

def test_office_text_media_fonts_stats(tmp_path):
    p = make_docx(tmp_path / "a.docx", image=jpeg_bytes((200, 100)))
    assert F.office_kind(p) == "ooxml"
    text = F.office_text(p, "docx")
    assert text.splitlines() == ["Ciao mondo", "Seconda riga di testo", "aggiunto"]
    media = F.office_media(p)
    assert [m[0] for m in media] == ["image1.jpeg", "image2.png"]
    assert F.office_fonts(p) == ["Calibri", "Times New Roman"]
    assert F.office_app_stats(p) == {"TotalTime": 42, "Pages": 3, "Words": 120}
    odt = make_odt(tmp_path / "b.odt")
    assert F.office_kind(odt) == "odf"
    assert F.office_text(odt, "odt") == "Titolo\nUno  due\ttre\nNota fine"
    assert F.office_app_stats(odt) == {"word-count": 5, "page-count": 1}


def test_office_encrypted(tmp_path):
    enc = tmp_path / "x.docx"
    enc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    assert F.office_encrypted(enc, "docx")
    legacy = tmp_path / "x.doc"
    legacy.write_bytes(enc.read_bytes())
    assert not F.office_encrypted(legacy, "doc")
    odt = tmp_path / "e.odt"
    with zipfile.ZipFile(odt, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml", "<manifest:encryption-data/>")
        z.writestr("content.xml", "x")
    assert F.office_encrypted(odt, "odt")


def test_strip_office_metadata(tmp_path):
    src = make_docx(tmp_path / "a.docx")
    dst = tmp_path / "b.docx"
    assert F.strip_office_metadata(src, dst) >= 5
    with zipfile.ZipFile(dst) as z:
        names = z.namelist()
        assert "docProps/custom.xml" not in names
        data = b"".join(z.read(n) for n in names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in z.infolist())
    for secret in (b"Mario", b"ACME", b"Segreto", b"Normal.dotm", b"2024-05-01", b"custom.xml", b"attachedTemplate",
                   b"TotalTime"):
        assert secret not in data, secret
    assert b"Ciao mondo" in data
    odt = make_odt(tmp_path / "c.odt")
    out = tmp_path / "d.odt"
    F.strip_office_metadata(odt, out)
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype"
        assert b"Luca" not in z.read("content.xml") and b"Luca" not in z.read("meta.xml")


REL_TYPES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def test_strip_office_metadata_template_relationship(tmp_path):
    src = make_docx(tmp_path / "a.docx")
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("word/_rels/settings.xml.rels",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships {REL_NS}>'
                   f'<Relationship Id="rId1" Type="{REL_TYPES}/attachedTemplate" '
                   'Target="file:///C:\\Users\\mario.rossi\\Modelli\\Carta intestata ACME.dotx" TargetMode="External"/>'
                   f'<Relationship Id="rId2" Target="altro.xml" Type="{REL_TYPES}/image"/></Relationships>')
        z.writestr("customXml/item1.xml",
                   '<p:properties xmlns:p="http://schemas.microsoft.com/office/2006/metadata/properties">'
                   "<documentManagement><Autore><UserInfo><DisplayName>Mario Rossi</DisplayName><AccountId>12</AccountId>"
                   "</UserInfo></Autore></documentManagement></p:properties>")
        z.writestr("customXml/item2.xml", "<dati><DisplayName>Listino</DisplayName></dati>")
    dst = tmp_path / "b.docx"
    F.strip_office_metadata(src, dst)
    with zipfile.ZipFile(dst) as z:
        rels = z.read("word/_rels/settings.xml.rels")
        assert b"mario" not in rels and b"attachedTemplate" not in rels and b"ACME" not in rels
        assert b'Id="rId2"' in rels  # other relationships are kept
        assert b"Mario Rossi" not in z.read("customXml/item1.xml")
        assert b"<DisplayName>Listino</DisplayName>" in z.read("customXml/item2.xml")  # not SharePoint: user data
        assert not [n for n in z.namelist() if b"mario" in z.read(n).lower()]


def test_strip_office_metadata_excel_save_path(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    plain = tmp_path / "plain.xlsx"
    wb = openpyxl.Workbook()
    wb.active["A1"] = "ciao"
    wb.save(plain)
    block = (b'<fileSharing userName="Mario Rossi" reservationPassword="ABCD"/>'
             b'<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
             b'<mc:Choice Requires="x15"><x15ac:absPath url="C:\\Users\\mario.rossi\\Documents\\Riservato\\" '
             b'xmlns:x15ac="http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"/></mc:Choice>'
             b'</mc:AlternateContent><mc:AlternateContent xmlns:mc="m"><mc:Choice Requires="x">keep</mc:Choice>'
             b'</mc:AlternateContent>')

    def edit(name: str, data: bytes) -> bytes:
        if name == "xl/workbook.xml":
            assert data.count(b"<workbookPr/>") == 1
            data = data.replace(b"<workbookPr/>", block + b"<workbookPr/>")
        return data

    src = tmp_path / "a.xlsx"
    _rewrite_zip(plain, src, edit)
    with zipfile.ZipFile(src) as z:
        assert b"mario.rossi" in z.read("xl/workbook.xml")
    dst = tmp_path / "b.xlsx"
    assert F.strip_office_metadata(src, dst) >= 1
    with zipfile.ZipFile(dst) as z:
        workbook = z.read("xl/workbook.xml")
    assert b"mario" not in workbook.lower() and b"absPath" not in workbook and b"Mario" not in workbook
    assert b"Requires=\"x15\"" not in workbook  # the wrapper goes too, an empty one would be invalid
    assert b"keep" in workbook and b'userName=""' in workbook  # unrelated content is untouched
    assert openpyxl.load_workbook(dst).active["A1"].value == "ciao"


def test_compress_office_media(tmp_path):
    big = jpeg_bytes((2400, 1600), quality=98)
    src = make_docx(tmp_path / "a.docx", image=big)
    dst = tmp_path / "b.docx"
    total, shrunk = F.compress_office_media(src, dst, quality=70, max_px=1000)
    assert total == 2 and shrunk >= 1
    with zipfile.ZipFile(dst) as z:
        new = z.read("word/media/image1.jpeg")
    from PIL import Image

    assert len(new) < len(big) / 2
    assert max(Image.open(io.BytesIO(new)).size) == 1000


def test_pptx_and_odp_slides(tmp_path):
    slides = F.pptx_slides(make_pptx(tmp_path / "a.pptx"))
    assert slides[0] == {"title": "Benvenuti", "paragraphs": ["Primo punto", "Secondo punto"],
                         "notes": "Ricordati di salutare", "hidden": False}
    assert slides[1]["title"] == "Fine" and slides[1]["hidden"]
    odp = tmp_path / "b.odp"
    content = (f'<office:document-content {ODF}><office:body><office:presentation>'
               '<draw:page draw:name="p1"><draw:frame presentation:class="title"><draw:text-box><text:p>Ciao</text:p>'
               '</draw:text-box></draw:frame><draw:frame presentation:class="outline"><draw:text-box><text:p>Punto</text:p>'
               '</draw:text-box></draw:frame><presentation:notes><draw:frame presentation:class="notes"><draw:text-box>'
               "<text:p>Nota</text:p></draw:text-box></draw:frame></presentation:notes></draw:page>"
               "</office:presentation></office:body></office:document-content>")
    with zipfile.ZipFile(odp, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        z.writestr("content.xml", content)
    assert F.odp_slides(odp) == [{"title": "Ciao", "paragraphs": ["Punto"], "notes": "Nota", "hidden": False}]


# ===================================================================================== ebooks

def test_epub_info_cover_drm(tmp_path):
    p = make_epub(tmp_path / "a.epub")
    info = dict(F.epub_info(p))
    assert info["title"] == "Il Libro" and info["author"] == "Italo Calvino" and info["series"] == "Romanzi"
    assert info["description"] == "Una storia" and info["format"] == "EPUB 3.0"
    data, ext = F.epub_cover(p)
    assert ext == "png" and data.startswith(b"\x89PNG")
    assert not F.epub_drm(p)
    assert F.epub_drm(make_epub(tmp_path / "b.epub", drm=True))


def test_fb2_and_cbz(tmp_path):
    cover = png_bytes()
    fb2 = tmp_path / "a.fb2"
    fb2.write_text(
        '<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><genre>prose</genre><author><first-name>Lev'
        "</first-name><last-name>Tolstoj</last-name></author><book-title>Guerra e pace</book-title>"
        '<coverpage><image l:href="#cov.png"/></coverpage><lang>it</lang></title-info></description><body/>'
        f'<binary id="cov.png" content-type="image/png">{base64.b64encode(cover).decode()}</binary></FictionBook>',
        encoding="utf-8")
    info = dict(F.fb2_info(fb2))
    assert info["title"] == "Guerra e pace" and info["author"] == "Lev Tolstoj" and info["language"] == "it"
    assert F.fb2_cover(fb2) == (cover, "png")
    cbz = tmp_path / "c.cbz"
    with zipfile.ZipFile(cbz, "w") as z:
        z.writestr("p10.png", png_bytes(color=(1, 1, 1)))
        z.writestr("p2.png", cover)
        z.writestr("__MACOSX/._p1.png", b"junk")
        z.writestr("ComicInfo.xml", "<ComicInfo><Title>Tex</Title><Series>Tex</Series><Number>3</Number></ComicInfo>")
    assert F.cbz_cover(cbz) == (cover, "png")
    info = dict(F.cbz_info(cbz))
    assert info["title"] == "Tex" and info["series"] == "Tex #3" and info["pages"] == "2"


def test_parse_ebook_meta():
    out = ("Title               : Il nome della rosa\nAuthor(s)           : Umberto Eco [Eco, Umberto]\n"
           "Languages           : ita\nComments            : <p>Un giallo</p>\n  medievale\nTimestamp : 2020\n")
    assert F.parse_ebook_meta(out) == [("title", "Il nome della rosa"), ("author", "Umberto Eco"),
                                       ("language", "ita"), ("description", "Un giallo medievale")]


# ================================================================================== subtitles

SRT = ("\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Ciao</i> mondo & co\r\n\r\n"
       "2\r\n00:00:03,000 --> 00:00:04,000\r\nRiga {due}\r\nterza\r\n")


def test_subtitle_parse_and_write():
    cues = F.parse_subtitles(SRT, "srt")
    assert [(c.start, c.end) for c in cues] == [(1.0, 2.5), (3.0, 4.0)]
    vtt = F.write_subtitles(cues, "vtt")
    assert vtt.startswith("WEBVTT\n\n00:00:01.000 --> 00:00:02.500\n<i>Ciao</i> mondo &amp; co")
    back = F.parse_subtitles(vtt, "vtt")
    assert back[0].text == "<i>Ciao</i> mondo & co"
    ass = F.write_subtitles(cues, "ass", title="T")
    assert "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\i1}Ciao{\\i0} mondo & co" in ass
    assert "Riga (due)\\Nterza" in ass
    assert F.parse_subtitles(ass, "ass")[1].text == "Riga (due)\nterza"
    assert F.write_subtitles(cues, "txt") == "Ciao mondo & co\nRiga {due} terza\n"
    sbv = "0:00:01.000,0:00:02.000\nUno\n\n0:00:03.000,0:00:04.000\nDue\n"
    assert [c.text for c in F.parse_subtitles(sbv, "sbv")] == ["Uno", "Due"]
    with pytest.raises(F.FormatError):
        F.parse_subtitles("niente", "srt")


def test_subtitle_timestamps():
    assert F.parse_timestamp("01:02:03,456") == pytest.approx(3723.456)
    assert F.parse_timestamp("02:03.5") == pytest.approx(123.5)
    assert F.parse_timestamp("x") is None
    assert F.format_timestamp(3723.456, "srt") == "01:02:03,456"
    assert F.format_timestamp(3723.456, "ass") == "1:02:03.46"
    assert F.format_timestamp(-1, "vtt") == "00:00:00.000"
    assert F.subtitle_format("", "WEBVTT\n\n") == "vtt"


def test_remap_keeps_settings_and_styles():
    vtt = ("WEBVTT\nKind: captions\n\nNOTE commento\n\nid1\n00:00:00.500 --> 00:00:01.000 align:start\nprima\n\n"
           "00:00:05.000 --> 00:00:06.000 line:90%\n<v Bob>dopo</v>\n")
    text, kept, dropped = F.remap_subtitle_times(vtt, "vtt", lambda t: t - 2)
    assert (kept, dropped) == (1, 1)
    assert "00:00:03.000 --> 00:00:04.000 line:90%" in text and "NOTE commento" in text and "prima" not in text
    ass = F.write_subtitles(F.parse_subtitles(SRT, "srt"), "ass").replace("Arial,64", "Comic,99")
    text, kept, dropped = F.remap_subtitle_times(ass, "ass", lambda t: t * 2)
    assert "Comic,99" in text and "0:00:02.00,0:00:05.00" in text and (kept, dropped) == (2, 0)
    srt, kept, dropped = F.remap_subtitle_times(SRT, "srt", lambda t: t - 2.6)
    assert srt.startswith("1\n00:00:00,400 --> 00:00:01,400\nRiga {due}") and dropped == 1


def test_clean_hearing_impaired():
    cues = [F.Cue(0, 1, "[MUSICA]\nJOHN: Ciao (ride)"), F.Cue(1, 2, "♪ la la ♪"), F.Cue(2, 3, "- MARIA: No.\n- Sì.")]
    cleaned, changed = F.clean_hearing_impaired(cues)
    assert [c.text for c in cleaned] == ["Ciao", "- No.\n- Sì."] and changed == 3


# ==================================================================================== engines

def test_lo_pdf_target_and_paper(monkeypatch):
    assert E.lo_pdf_target("calc") == "pdf:calc_pdf_Export"
    target = E.lo_pdf_target("writer", {"SelectPdfVersion": 2, "ExportBookmarks": True, "X": "y"})
    payload = json.loads(target.split(":", 2)[2])
    assert payload == {"SelectPdfVersion": {"type": "long", "value": "2"},
                       "ExportBookmarks": {"type": "boolean", "value": "true"}, "X": {"type": "string", "value": "y"}}
    monkeypatch.setenv("LC_PAPER", "en_US.UTF-8")
    assert E.paper_size() == "letter" and E.resolve_paper("auto") == "letter" and E.resolve_paper("a4") == "a4"
    monkeypatch.setenv("LC_PAPER", "it_IT.UTF-8")
    assert E.paper_size() == "a4"


class FakeLO:
    """Stands in for ``ctx.run`` when argv[0] is soffice: writes outputs according to a script."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        import subprocess

        self.calls.append(list(argv))
        action = self.script.pop(0) if self.script else "ok"
        outdir = Path(argv[argv.index("--outdir") + 1])
        target = argv[argv.index("--convert-to") + 1].split(":", 1)[0]
        if action == "ok":
            (outdir / f"source.{target}").write_bytes(b"%PDF-1.7 fake")
            return subprocess.CompletedProcess(argv, 0, "convert x -> y using filter : z", "")
        if action == "restart":
            profile = Path(argv[1].split("=", 1)[1].replace("file:///", "/").replace("file://", ""))
            return subprocess.CompletedProcess(argv, 81, "", "")
        if action == "noload":
            return subprocess.CompletedProcess(argv, 0, "Error: source file could not be loaded", "")
        if action == "nofilter":
            return subprocess.CompletedProcess(argv, 0, "Error: no export filter for x found, aborting.", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _lo_ctx(make_runner, work, monkeypatch, script):
    src = work / "-strano nome è.docx"
    make_docx(src)
    ctx = make_ctx(make_runner, "document.convert.pdf", [src])
    fake = FakeLO(script)
    monkeypatch.setattr(ctx, "run", fake)
    path = tools.path
    monkeypatch.setattr(E.tools, "path", lambda name: "/usr/bin/soffice" if name == "soffice" else path(name))
    return ctx, src, fake


def test_lo_convert_success_and_staging(make_runner, work, monkeypatch):
    ctx, src, fake = _lo_ctx(make_runner, work, monkeypatch, ["ok"])
    out = E.lo_convert(ctx, src, "pdf:writer_pdf_Export", out_ext="pdf")
    assert out[0].name == "source.pdf" and out[0].read_bytes().startswith(b"%PDF")
    argv = fake.calls[0]
    assert argv[0] == "soffice" and "--headless" in argv and "--norestore" in argv and "--nolockcheck" in argv
    assert argv[1].startswith("-env:UserInstallation=file:")
    staged = Path(argv[-1])
    assert staged.name == "source.docx" and staged.parent != src.parent and staged.read_bytes() == src.read_bytes()


def test_lo_convert_retries_and_errors(make_runner, work, monkeypatch):
    ctx, src, fake = _lo_ctx(make_runner, work, monkeypatch, ["restart", "ok"])
    assert E.lo_convert(ctx, src, "pdf", out_ext="pdf")[0].exists()
    assert len(fake.calls) == 2 and fake.calls[0][1] == fake.calls[1][1]  # same profile after a restart
    ctx, src, fake = _lo_ctx(make_runner, work, monkeypatch, ["empty", "ok"])
    assert E.lo_convert(ctx, src, "pdf", out_ext="pdf")[0].exists()
    assert fake.calls[0][1] != fake.calls[1][1]  # fell back to a fresh profile
    ctx, src, fake = _lo_ctx(make_runner, work, monkeypatch, ["empty", "empty"])
    with pytest.raises(UserError) as exc:
        E.lo_convert(ctx, src, "pdf", out_ext="pdf")
    assert exc.value.message == "docs.err.lo_no_output"
    ctx, src, fake = _lo_ctx(make_runner, work, monkeypatch, ["noload", "noload"])
    with pytest.raises(UserError) as exc:
        E.lo_convert(ctx, src, "pdf", out_ext="pdf")
    assert exc.value.message == "docs.err.lo_load"
    ctx, src, fake = _lo_ctx(make_runner, work, monkeypatch, ["nofilter"])
    with pytest.raises(UserError) as exc:
        E.lo_convert(ctx, src, "xyz", out_ext="xyz")
    assert exc.value.message == "docs.err.lo_filter" and len(fake.calls) == 1


def test_lo_convert_missing(make_runner, work, monkeypatch):
    src = make_docx(work / "a.docx")
    ctx = make_ctx(make_runner, "document.convert.pdf", [src])
    monkeypatch.setattr(E.tools, "path", lambda name: None)
    with pytest.raises(MissingTool):
        E.lo_convert(ctx, src, "pdf", out_ext="pdf")


def test_markdown_to_pdf_engine_selection(make_runner, work, monkeypatch):
    md = work / "a.md"
    md.write_text("# T\n", encoding="utf-8")
    ctx = make_ctx(make_runner, "markdown.stats", [md])
    monkeypatch.setattr(E.tools, "have", lambda name: False)
    with pytest.raises(MissingTool) as exc:
        E.markdown_to_pdf(ctx, md, work / "o.pdf", title="a", lang="it")
    assert exc.value.names == ["pandoc", "typst"]
    tried = []

    def fake_md_pdf(ctx, engine, src, out, **kw):
        tried.append(engine)
        if engine == "typst":
            raise ToolError("err.tool_failed", tool="typst", code=1, details="boom")
        out.write_bytes(b"%PDF")

    monkeypatch.setattr(E, "md_engine_available", lambda e: e in ("typst", "weasyprint"))
    monkeypatch.setattr(E, "_md_pdf", fake_md_pdf)
    assert E.markdown_to_pdf(ctx, md, work / "o.pdf", title="a", lang="it") == "weasyprint"
    assert tried == ["typst", "weasyprint"]
    monkeypatch.setattr(E, "_md_pdf", lambda *a, **k: (_ for _ in ()).throw(ToolError("err.tool_failed", tool="x", code=1)))
    with pytest.raises(ToolError) as exc:
        E.markdown_to_pdf(ctx, md, work / "o2.pdf", title="a", lang="it")
    assert exc.value.message == "docs.err.pdf_engines"


def test_code_html_highlight_and_numbers():
    pytest.importorskip("pygments")
    page = E.code_html('def f():\n    return "<x>"\n', filename="a.py", title="a.py", lang="it",
                       line_numbers=True, plain=False)
    assert "<span" in page and "&lt;x&gt;" in page and "a.py" in page and 'lang="it"' in page
    plain = E.code_html("riga\n", filename="a.txt", title="a.txt", lang="en", line_numbers=False, plain=True,
                        highlight=False)
    assert "<pre>riga</pre>" in plain
    assert E._hard_wrap("x" * 25, 10) == "xxxxxxxxxx\nxxxxxxxxxx\nxxxxx"


def test_pdf_helpers(make_runner, work):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")
    img = work / "foto.png"
    img.write_bytes(png_bytes((300, 200)))
    a = work / "a.pdf"
    E.image_to_pdf(img, a)
    b = work / "b.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(str(b))
    ctx = make_ctx(make_runner, "text.stats", [work / "foto.png"]) if False else None
    txt = work / "t.txt"
    txt.write_text("x", encoding="utf-8")
    ctx = make_ctx(make_runner, "text.stats", [txt])
    merged = work / "m.pdf"
    assert E.merge_pdfs(ctx, [(a, "Foto"), (b, "Due")], merged) == 3
    with pymupdf.open(str(merged)) as m:
        assert [t[1] for t in m.get_toc()] == ["Foto", "Due"]
    folder = work / "imgs"
    folder.mkdir()
    pages = E.pdf_to_images(ctx, merged, folder, fmt="png", dpi=40, prefix="page")
    assert [p.name for p in pages] == ["page-001.png", "page-002.png", "page-003.png"]
    enc = work / "enc.pdf"
    E.encrypt_pdf(ctx, merged, enc, "segreta")
    with pymupdf.open(str(enc)) as e:
        assert e.needs_pass and e.authenticate("segreta")


def test_story_pdf(make_runner, work):
    pymupdf = pytest.importorskip("pymupdf")
    txt = work / "t.txt"
    txt.write_text("x", encoding="utf-8")
    ctx = make_ctx(make_runner, "text.stats", [txt])
    page = E.code_html("riga uno\n" * 200 + "y" * 400, filename="t.txt", title="t.txt", lang="it",
                       line_numbers=True, plain=True, hard_wrap=110, highlight=False)
    out = work / "s.pdf"
    E.story_pdf(ctx, page, out)
    with pymupdf.open(str(out)) as d:
        assert d.page_count >= 2 and "riga uno" in d[0].get_text()


# ================================================================================ menus/i18n

def test_layout_references_and_locales():
    en = json.loads((ROOT / "locales/en/docs.json").read_text("utf-8"))
    it = json.loads((ROOT / "locales/it/docs.json").read_text("utf-8"))
    assert set(en) == set(it)
    core_en = json.loads((ROOT / "locales/en/core.json").read_text("utf-8"))
    known = set(en) | set(core_en)
    acts = [a for a in registry.all_actions() if a.module.endswith(".docs")]
    assert len(acts) > 90
    for a in acts:
        for key in [a.label, a.description] + [p.label for p in a.params] + [p.help for p in a.params]:
            if key and "." in key:
                assert key in known, (a.id, key)
    for (page, key), items in registry.layouts().items():
        if key.split(":")[-1] in ("document", "spreadsheet", "presentation", "markdown", "text", "code", "data",
                                  "ebook", "subtitle"):
            for it_ in items:
                assert registry.get(it_) or registry.get_group(it_), (key, it_)


def test_menus(work):
    from dropwheel import config

    make_docx(work / "a.docx")
    (work / "b.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (work / "c.json").write_text('{"a": 1}', encoding="utf-8")
    (work / "d.txt").write_text("x", encoding="utf-8")
    (work / "e.png").write_bytes(png_bytes())
    cfg = config.load()

    def ids(menu):
        out = []

        def walk(nodes):
            for n in nodes:
                out.append(n["id"])
                walk(n.get("items") or [])

        walk(menu["pages"][0]["items"])
        return out

    docx_menu = registry.build_menu([str(work / "a.docx")], cfg, "it")
    assert ids(docx_menu)[0] == "document.convert" and "document.convert.docx" not in ids(docx_menu)
    assert "document.privacy.strip" in ids(docx_menu)
    data_menu = registry.build_menu([str(work / "c.json")], cfg, "it")
    group = data_menu["pages"][0]["items"][0]
    assert group["id"] == "data.convert" and group["default"] == "data.convert.yaml" or not tools.have("py:yaml")
    assert "data.convert.json" not in ids(data_menu)
    mixed = registry.build_menu([str(work / "a.docx"), str(work / "e.png")], cfg, "it")
    assert "docs.combine.pdf" in ids(mixed) and "docs.mixed.pdf" in ids(mixed)
    multi = registry.build_menu([str(work / "b.csv"), str(work / "b.csv")], cfg, "it")
    assert ids(multi)[:2] == ["spreadsheet.merge.xlsx", "spreadsheet.merge.csv"]


@pytest.mark.parametrize("lang", ["it", "en"])
def test_rings_tell_their_segments_apart(work, lang):
    from dropwheel import config

    make_docx(work / "a.docx")
    (work / "b.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (work / "c.json").write_text('{"a": 1}', encoding="utf-8")
    (work / "d.txt").write_text("x", encoding="utf-8")
    (work / "e.md").write_text("# x\n", encoding="utf-8")
    (work / "f.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nciao\n", encoding="utf-8")
    (work / "g.png").write_bytes(png_bytes())
    (work / "h.yaml").write_text("a: 1\n", encoding="utf-8")
    cfg = config.load()

    def ring(*names):
        return registry.build_menu([str(work / n) for n in names], cfg, lang)["pages"][0]["items"]

    def node(items, node_id):
        return next(n for n in items if n["id"] == node_id)

    def check(items, where):
        labels = [n["label"] for n in items]
        assert len(labels) == len(set(labels)), (where, labels)
        for n in items:
            if n.get("items"):
                check(n["items"], (where, n["id"]))

    for names in (["a.docx"], ["b.csv"], ["c.json"], ["d.txt"], ["e.md"], ["f.srt"], ["h.yaml"], ["d.txt", "d.txt"],
                  ["c.json", "h.yaml"], ["f.srt", "f.srt"], ["b.csv", "b.csv"], ["g.png", "d.txt"]):
        check(ring(*names), names)

    # JSON: "Format" is the pretty-printer; the UTF-8 / line endings sub-ring has its own name and icon
    data = ring("c.json")
    pretty, encoding = node(data, "data.pretty"), node(data, "text.format")
    assert pretty["label"] != encoding["label"] and pretty["icon"] != encoding["icon"]
    assert encoding["label"] == i18n.t("grp.encoding", lang)

    # CSV: the two separators do not share a long prefix (the segment cuts labels off) nor an icon
    csv_ring = node(ring("b.csv"), "spreadsheet.csv")["items"]
    comma, semicolon = node(csv_ring, "spreadsheet.csv.comma"), node(csv_ring, "spreadsheet.csv.semicolon")
    assert comma["icon"] != semicolon["icon"]
    assert comma["label"][:3] != semicolon["label"][:3]
    assert len(comma["label"]) <= 15 and len(semicolon["label"]) <= 15
    assert comma["description"] and semicolon["description"] and comma["description"] != semicolon["description"]

    # mixed selection: one combined PDF versus one PDF per file
    mixed = ring("g.png", "d.txt")
    combine, each = node(mixed, "docs.combine.pdf"), node(mixed, "docs.mixed.pdf")
    assert combine["icon"] != each["icon"]
    assert each["label"] == {"it": "Un PDF per file", "en": "One PDF per file"}[lang]


# ======================================================================= actions: spreadsheets

@pytest.fixture
def csv_file(work) -> Path:
    p = work / "-dati è.csv"
    p.write_bytes("nome;cap;importo\r\nMario;00123;3,50\r\nLuisa;20100;10\r\n".encode("cp1252"))
    return p


def test_csv_to_json_md_html_tsv(make_runner, csv_file, work):
    job = run(make_runner, "spreadsheet.convert.json", [csv_file])
    ok(job)
    out = out_paths(job)[0]
    assert out == work / "-dati è.json"
    assert json.loads(out.read_text("utf-8"))[0] == {"nome": "Mario", "cap": "00123", "importo": 3.5}
    md = out_paths(run(make_runner, "spreadsheet.convert.md", [csv_file]))[0].read_text("utf-8")
    assert md.splitlines()[0] == "| nome | cap | importo |"
    html = out_paths(run(make_runner, "spreadsheet.convert.html", [csv_file]))[0].read_text("utf-8")
    assert "<td>Mario</td>" in html
    tsv = out_paths(run(make_runner, "spreadsheet.convert.tsv", [csv_file]))[0].read_text("utf-8")
    assert tsv.splitlines()[1] == "Mario\t00123\t3,50"


def test_csv_to_xlsx_and_back(make_runner, csv_file, work, monkeypatch):
    pytest.importorskip("openpyxl")
    have = tools.have
    monkeypatch.setattr(docs_mod.tools, "have", lambda n: False if n == "soffice" else have(n))
    xlsx = out_paths(ok_job := run(make_runner, "spreadsheet.convert.xlsx", [csv_file]))[0]
    ok(ok_job)
    sheets = F.read_xlsx(xlsx)
    assert sheets[0][1][1] == ["Mario", "00123", 3.5]
    job = run(make_runner, "spreadsheet.convert.csv", [xlsx], {"delimiter": ";", "excel": True})
    ok(job)
    data = out_paths(job)[0].read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") and b"Mario;00123;3.5\r\n" in data
    ods = out_paths(run(make_runner, "spreadsheet.convert.ods", [xlsx]))[0]
    assert F.read_ods(ods)[0][1][2] == ["Luisa", 20100, 10]
    info = ok(run(make_runner, "spreadsheet.info", [xlsx]))
    assert info["kind"] == "info" and any(r["value"] == "3" for r in info["info"])


def test_csv_tools_and_merge(make_runner, csv_file, work):
    job = run(make_runner, "spreadsheet.csv.comma", [csv_file])
    out = out_paths(job)[0]
    assert out.name == "-dati è (Comma).csv"
    assert out.read_text("utf-8").splitlines()[1] == 'Mario,00123,"3,50"'
    excel = out_paths(run(make_runner, "spreadsheet.csv.excel", [csv_file]))[0]
    assert excel.read_bytes().startswith(b"\xef\xbb\xbf")
    other = work / "altri.csv"
    other.write_text("importo;nome;cap\n7;Gino;00999\n", encoding="utf-8")
    job = run(make_runner, "spreadsheet.merge.csv", [csv_file, other], {"add_source": True})
    ok(job)
    rows = out_paths(job)[0].read_text("utf-8").splitlines()
    assert rows[0] == "nome;cap;importo;file" and rows[-1] == "Gino;00999;7;altri.csv"
    bad = work / "zzz.csv"
    bad.write_text("x,y\n1,2\n", encoding="utf-8")
    job = run(make_runner, "spreadsheet.merge.csv", [csv_file, bad])
    assert job.state == "failed" and "zzz.csv" in job.error["message"]


def test_combine_xlsx(make_runner, csv_file, work):
    pytest.importorskip("openpyxl")
    other = work / "b.csv"
    other.write_text("x\n1\n", encoding="utf-8")
    job = run(make_runner, "spreadsheet.merge.xlsx", [csv_file, other])
    ok(job)
    sheets = F.read_xlsx(out_paths(job)[0])
    assert [s[0] for s in sheets] == ["-dati è", "b"]


# ========================================================================== actions: documents

def test_docx_tools(make_runner, work):
    pytest.importorskip("PIL")
    src = make_docx(work / "Relazione finale.docx", image=jpeg_bytes((2400, 1600), quality=98))
    before = src.read_bytes()
    job = run(make_runner, "document.privacy.strip", [src], lang="it")
    ok(job)
    assert out_paths(job)[0].name == "Relazione finale (senza metadati).docx"
    job = run(make_runner, "document.compress", [src], {"quality": 70, "max_px": 1200}, lang="it")
    res = ok(job)
    assert out_paths(job)[0].name == "Relazione finale (compresso).docx" and res["size_after"] < res["size_before"]
    job = run(make_runner, "document.extract.images", [src], lang="it")
    folder = out_paths(job)[0]
    assert folder.name == "Relazione finale (immagini)" and sorted(p.name for p in folder.iterdir()) == [
        "image1.jpeg", "image2.png"]
    job = run(make_runner, "document.info", [src], lang="it")
    res = ok(job)
    values = {r["key"]: r["value"] for r in res["info"]}
    assert values["Parole"] in ("120", "7") and res["message"] == "7 parole"
    assert src.read_bytes() == before  # inputs never modified


def test_docx_replace_original(make_runner, work):
    src = make_docx(work / "a.docx")
    job = run(make_runner, "document.privacy.strip", [src], {"replace": True})
    ok(job)
    with zipfile.ZipFile(src) as z:
        assert b"Mario" not in z.read("docProps/core.xml")
    assert job.backups and Path(job.backups[0]["backup"]).exists()


def test_encrypted_docx_refused(make_runner, work):
    enc = work / "locked.docx"
    enc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 2000)
    job = run(make_runner, "document.privacy.strip", [enc])
    assert job.state == "failed"


def test_document_to_txt_builtin(make_runner, work, monkeypatch):
    """DOCX/ODT/RTF → TXT work without LibreOffice and pandoc; legacy formats need LibreOffice."""
    have = tools.have
    monkeypatch.setattr(docs_mod.tools, "have", lambda n: False if n in ("soffice", "pandoc") else have(n))
    src = make_docx(work / "-Lettera è.docx")
    job = run(make_runner, "document.convert.txt", [src])
    ok(job)
    out = out_paths(job)[0]
    assert out.name == "-Lettera è.txt"
    assert out.read_text("utf-8").splitlines()[:2] == ["Ciao mondo", "Seconda riga di testo"]
    odt = make_odt(work / "nota.odt")
    assert out_paths(run(make_runner, "document.convert.txt", [odt]))[0].read_text("utf-8").strip()
    rtf = work / "vecchio.rtf"
    rtf.write_text(r"{\rtf1\ansi Caff\'e8 pronto\par}", encoding="ascii")
    assert out_paths(run(make_runner, "document.convert.txt", [rtf]))[0].read_text("utf-8").startswith("Caffè pronto")
    legacy = registry.get("document.convert.txt_lo")
    assert legacy is not None and legacy.requires == ["soffice"]
    assert registry.get("document.convert.txt").requires == []
    doc = work / "vecchio.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 2000)
    from dropwheel.detect import classify

    info = classify(str(doc))
    assert not registry.get("document.convert.txt").accepts([info])
    if info.category == "document":
        assert legacy.accepts([info])


def test_markdown_requires_are_exact(monkeypatch):
    """Menu availability matches what the Markdown actions can really do."""
    present: set[str] = set()
    monkeypatch.setattr(tools, "have", lambda n: n in present)
    pdf = registry.get("markdown.convert.pdf")
    docx = registry.get("markdown.convert.docx")
    present.update({"py:markdown", "typst"})          # typst needs pandoc
    assert pdf.missing() and "pandoc" in pdf.missing()
    present.add("weasyprint")                          # python-markdown + WeasyPrint is enough
    assert pdf.missing() == []
    present.clear()
    present.add("soffice")                             # LibreOffice alone cannot read Markdown
    assert docx.missing() == ["pandoc"]
    present.add("py:markdown")
    assert docx.missing() == []


# ====================================================================== actions: presentations

def test_presentation_actions(make_runner, work):
    src = make_pptx(work / "deck.pptx", image=jpeg_bytes((300, 200)) if tools.have("py:PIL") else None)
    text = out_paths(run(make_runner, "presentation.extract.text", [src], lang="it"))[0]
    md = text.read_text("utf-8")
    assert text.name == "deck (testo).md"
    assert "## Slide 1 — Benvenuti" in md and "- Primo punto" in md and "Ricordati di salutare" in md
    notes = out_paths(run(make_runner, "presentation.extract.notes", [src]))[0].read_text("utf-8")
    assert "Ricordati di salutare" in notes and "Fine" not in notes
    res = ok(run(make_runner, "presentation.info", [src]))
    values = {r["key"]: r["value"] for r in res["info"]}
    assert values["Slides"] == "2" and values["Hidden slides"] == "1"
    strip = out_paths(run(make_runner, "presentation.privacy.strip", [src]))[0]
    with zipfile.ZipFile(strip) as z:
        assert b"Anna" not in z.read("docProps/core.xml")


# =========================================================================== actions: markdown

def test_markdown_actions(make_runner, work):
    src = work / "note.md"
    src.write_text(MD, encoding="utf-8")
    res = ok(run(make_runner, "markdown.toc", [src]))
    assert res["kind"] == "text" and "[Dettagli](#dettagli)" in res["text"]
    out = out_paths(run(make_runner, "markdown.toc.insert", [src], lang="it"))[0]
    assert out.name == "note (con indice).md" and "**Indice**" in out.read_text("utf-8")
    res = ok(run(make_runner, "markdown.stats", [src]))
    values = {r["key"]: r["value"] for r in res["info"]}
    assert values["Headings"] == "4" and values["Code blocks"] == "1" and values["Links"] == "1"
    if not tools.have("pandoc"):
        txt = out_paths(run(make_runner, "markdown.convert.txt", [src]))[0]
        assert "Intro con grassetto" in txt.read_text("utf-8")
    other = work / "altro.md"
    other.write_text("---\ntitle: X\n---\nSecondo file\n", encoding="utf-8")
    merged = out_paths(run(make_runner, "markdown.merge.md", [other, src], lang="it"))[0]
    text = merged.read_text("utf-8")
    assert merged.name == "altro (unito).md" and text.startswith("---\ntitle: X") and text.count("---\ntitle") == 1


def test_markdown_pdf_with_real_engine(make_runner, work):
    if not any(E.md_engine_available(e) for e in E.MD_PDF_ENGINES):
        pytest.skip("no Markdown PDF engine installed")
    src = work / "doc.md"
    src.write_text("# Titolo\n\nTesto con `codice`.\n\n```python\nprint('ciao')\n```\n", encoding="utf-8")
    job = run(make_runner, "markdown.convert.pdf", [src])
    ok(job)
    assert out_paths(job)[0].read_bytes().startswith(b"%PDF")


# ============================================================================ actions: text/code

def test_text_encoding_and_line_endings(make_runner, work):
    src = work / "vecchio.txt"
    src.write_bytes("perché caffè\r\nriga due  \r\n".encode("cp1252"))
    job = run(make_runner, "text.encoding.utf8", [src], lang="it")
    res = ok(job)
    out = out_paths(job)[0]
    assert out.name == "vecchio (UTF-8).txt" and out.read_bytes() == "perché caffè\r\nriga due  \r\n".encode("utf-8")
    assert "Windows-1252" in res["message"]
    lf = out_paths(run(make_runner, "text.line_endings.lf", [src]))[0]
    assert lf.read_bytes() == "perché caffè\nriga due  \n".encode("cp1252")  # encoding kept
    clean = out_paths(run(make_runner, "text.clean.trailing", [src]))[0]
    assert clean.read_bytes() == "perché caffè\r\nriga due\r\n".encode("cp1252")
    same = run(make_runner, "text.line_endings.crlf", [src])
    assert ok(same)["outputs"] == [] and "already" in same.result["message"]
    utf = work / "ok.txt"
    utf.write_text("già utf8\n", encoding="utf-8")
    assert ok(run(make_runner, "text.encoding.utf8", [utf]))["outputs"] == []


def test_text_stats_and_conversions(make_runner, work):
    code = work / "script.py"
    code.write_text("def f():\n    return '```'\n\n", encoding="utf-8")
    res = ok(run(make_runner, "text.stats", [code]))
    values = {r["key"]: r["value"] for r in res["info"]}
    assert values["Lines"] == "3" and values["Line endings"] == "LF (Linux)"
    if tools.have("py:pygments"):
        assert values["Programming language"] == "Python"
    md = out_paths(run(make_runner, "text.convert.md", [code]))[0].read_text("utf-8")
    assert md.startswith("# script.py\n\n````py") and md.rstrip().endswith("````")
    html = out_paths(run(make_runner, "text.convert.html", [code]))[0].read_text("utf-8")
    assert "<!DOCTYPE html>" in html and "script.py" in html
    note = work / "nota.txt"
    note.write_text("# non titolo\n* non elenco\n\nsecondo paragrafo\n", encoding="utf-8")
    md = out_paths(run(make_runner, "text.convert.md", [note]))[0].read_text("utf-8")
    assert md == "\\# non titolo\\\n\\* non elenco\n\nsecondo paragrafo\n"


def test_text_to_docx(make_runner, work):
    docx = pytest.importorskip("docx")
    src = work / "lettera.txt"
    src.write_text("Caro Mario,\nciao.\n\nSaluti\x07\n", encoding="utf-8")
    out = out_paths(ok_job := run(make_runner, "text.convert.docx", [src]))[0]
    ok(ok_job)
    d = docx.Document(str(out))
    assert [p.text for p in d.paragraphs] == ["Caro Mario,\nciao.", "Saluti"]


def test_text_to_pdf_story(make_runner, work, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    have = tools.have
    monkeypatch.setattr(E.tools, "have", lambda n: n == "py:pymupdf" or (n == "py:pygments" and have(n)))
    src = work / "programma.py"
    src.write_text("\n".join(f"x{i} = {i}  # " + "commento " * 20 for i in range(150)), encoding="utf-8")
    job = run(make_runner, "text.convert.pdf", [src])
    ok(job)
    with pymupdf.open(str(out_paths(job)[0])) as d:
        assert d.page_count >= 2 and "x0 = 0" in d[0].get_text()


def test_merge_text(make_runner, work):
    a = work / "a.txt"
    a.write_text("uno\r\n", encoding="utf-8")
    b = work / "b.log"
    b.write_bytes("due è".encode("cp1252"))
    job = run(make_runner, "text.merge.txt", [b, a])
    ok(job)
    out = out_paths(job)[0]
    assert out.name == "a (Merged).txt"
    assert out.read_bytes() == "===== a.txt =====\r\nuno\r\n\r\n===== b.log =====\r\ndue è\r\n".encode("utf-8")


# =============================================================================== actions: data

def test_data_conversions(make_runner, work):
    src = work / "conf.json"
    src.write_text('{"name": "x", "list": [1, 2], "nested": {"k": true}}', encoding="utf-8")
    toml_out = out_paths(run(make_runner, "data.convert.toml", [src]))[0]
    assert tomllib.loads(toml_out.read_text("utf-8")) == {"name": "x", "list": [1, 2], "nested": {"k": True}}
    xml_out = out_paths(run(make_runner, "data.convert.xml", [src]))[0]
    assert "<name>x</name>" in xml_out.read_text("utf-8")
    back = out_paths(run(make_runner, "data.convert.json", [xml_out]))[0]
    assert json.loads(back.read_text("utf-8"))["root"]["name"] == "x"
    if tools.have("py:yaml"):
        y = out_paths(run(make_runner, "data.convert.yaml", [src]))[0]
        assert "name: x" in y.read_text("utf-8")
    nulls = work / "n.json"
    nulls.write_text('{"a": {"b": null}}', encoding="utf-8")
    job = run(make_runner, "data.convert.toml", [nulls])
    assert job.state == "failed" and "$.a.b" in job.error["message"]


def test_data_tabular(make_runner, work):
    src = work / "people.json"
    src.write_text('[{"n": "Ann", "addr": {"city": "Roma"}}, {"n": "Bob", "tags": ["a"]}]', encoding="utf-8")
    csv_out = out_paths(run(make_runner, "data.convert.csv", [src]))[0]
    assert csv_out.read_text("utf-8").splitlines() == ["n,addr.city,tags", "Ann,Roma,", 'Bob,,"[""a""]"']
    if tools.have("py:openpyxl"):
        xlsx = out_paths(run(make_runner, "data.convert.xlsx", [src]))[0]
        assert F.read_xlsx(xlsx)[0][1][1] == ["Ann", "Roma"]
    scalar = work / "s.json"
    scalar.write_text("[]", encoding="utf-8")
    assert run(make_runner, "data.convert.csv", [scalar]).state == "failed"


def test_data_pretty_minify_validate_sort(make_runner, work):
    src = work / "a.json"
    src.write_text('{"b":1,"a":{"d":[1,2],"c":"è"}}', encoding="utf-8")
    pretty = out_paths(run(make_runner, "data.pretty", [src], lang="it"))[0]
    assert pretty.name == "a (formattato).json" and pretty.read_text("utf-8").startswith('{\n  "b": 1,')
    mini = out_paths(run(make_runner, "data.minify", [pretty]))[0]
    assert mini.read_text("utf-8") == '{"b":1,"a":{"d":[1,2],"c":"è"}}\n'
    sort = out_paths(run(make_runner, "data.sort_keys", [src]))[0]
    assert list(json.loads(sort.read_text("utf-8"))) == ["a", "b"]
    res = ok(run(make_runner, "data.validate", [src]))
    assert res["kind"] == "info" and res["message"] == "JSON valid"
    bad = work / "bad.json"
    bad.write_text('{\n "a": 1,\n}', encoding="utf-8")
    res = ok(run(make_runner, "data.validate", [bad], lang="it"))
    values = {r["key"]: r["value"] for r in res["info"]}
    assert values["Esito"] == "Non valido" and values["Riga"] == "3" and res["message"].startswith("JSON non valido")
    xml = work / "a.xml"
    xml.write_text("<a><b>x</b></a>", encoding="utf-8")
    pretty_xml = out_paths(run(make_runner, "data.pretty", [xml]))[0].read_text("utf-8")
    assert "\n  <b>x</b>\n" in pretty_xml


def test_merge_json(make_runner, work):
    a = work / "a.json"
    a.write_text('{"x": {"y": 1}, "k": 1}', encoding="utf-8")
    b = work / "b.json"
    b.write_text('{"x": {"z": 2}, "k": 2}', encoding="utf-8")
    job = run(make_runner, "data.merge.json", [b, a])
    ok(job)
    assert json.loads(out_paths(job)[0].read_text("utf-8")) == {"x": {"y": 1, "z": 2}, "k": 2}
    assert job.warnings
    c = work / "c.json"
    c.write_text("[1]", encoding="utf-8")
    d = work / "d.json"
    d.write_text("[2, 3]", encoding="utf-8")
    assert json.loads(out_paths(run(make_runner, "data.merge.json", [c, d]))[0].read_text("utf-8")) == [1, 2, 3]


# ============================================================================= actions: ebooks

def test_ebook_actions(make_runner, work):
    src = make_epub(work / "libro.epub")
    cover = out_paths(run(make_runner, "ebook.cover", [src], lang="it"))[0]
    assert cover.name == "libro (copertina).png"
    res = ok(run(make_runner, "ebook.info", [src], lang="it"))
    assert res["message"] == "Il Libro — Italo Calvino"
    assert {r["key"] for r in res["info"]} >= {"Titolo", "Autore", "Lingua", "Serie"}
    cbz = work / "fumetto.cbz"
    with zipfile.ZipFile(cbz, "w") as z:
        z.writestr("b/10.png", png_bytes())
        z.writestr("b/9.png", png_bytes())
    folder = out_paths(run(make_runner, "ebook.extract.images", [cbz]))[0]
    assert sorted(p.name for p in folder.iterdir()) == ["page-001.png", "page-002.png"]
    drm = make_epub(work / "protetto.epub", drm=True)
    if tools.have("ebook-convert"):
        job = run(make_runner, "ebook.convert.azw3", [drm])
        assert job.state == "failed"


# ========================================================================== actions: subtitles

def test_subtitle_actions(make_runner, work):
    src = work / "film.srt"
    src.write_bytes(SRT.encode("utf-8"))
    vtt = out_paths(run(make_runner, "subtitle.convert.vtt", [src]))[0]
    assert vtt.name == "film.vtt" and vtt.read_text("utf-8").startswith("WEBVTT")
    txt = out_paths(run(make_runner, "subtitle.convert.txt", [src]))[0]
    assert txt.read_text("utf-8") == "Ciao mondo & co\nRiga {due} terza\n"
    job = run(make_runner, "subtitle.timing.shift", [src], {"offset": -2.6}, lang="it")
    ok(job)
    shifted = out_paths(job)[0]
    assert shifted.name == "film (sincronizzato).srt" and job.warnings
    assert shifted.read_bytes().startswith("\ufeff1\r\n00:00:00,400 --> 00:00:01,400\r\n".encode("utf-8"))
    job = run(make_runner, "subtitle.timing.fps", [vtt], {"from_fps": "25", "to_fps": "23.976"})
    ok(job)
    assert "00:00:01.043 --> 00:00:02.607" in out_paths(job)[0].read_text("utf-8")
    assert run(make_runner, "subtitle.timing.shift", [src], {"offset": 0}).state == "failed"
    assert run(make_runner, "subtitle.timing.fps", [src], {"from_fps": "25", "to_fps": "25"}).state == "failed"
    hi = work / "sdh.srt"
    hi.write_text("1\n00:00:01,000 --> 00:00:02,000\n[MUSICA]\n\n2\n00:00:03,000 --> 00:00:04,000\nANNA: Ciao\n",
                  encoding="utf-8")
    clean = out_paths(run(make_runner, "subtitle.clean.hi", [hi]))[0]
    assert clean.read_text("utf-8") == "1\n00:00:03,000 --> 00:00:04,000\nCiao\n"


# ======================================================================== actions: multi / mixed

def test_combine_into_pdf(make_runner, work):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")
    txt = work / "b nota.txt"
    txt.write_text("Una nota di testo\n" * 5, encoding="utf-8")
    img = work / "a foto.png"
    img.write_bytes(png_bytes((200, 100)))
    pdf = work / "c.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(pdf))
    job = run(make_runner, "docs.combine.pdf", [txt, pdf, img], lang="it")
    res = ok(job)
    out = out_paths(job)[0]
    assert out.name == "a foto (unito).pdf"
    with pymupdf.open(str(out)) as d:
        assert [t[1] for t in d.get_toc()] == ["a foto", "b nota", "c"]
    assert "3 file" in res["message"]


def test_all_to_pdf_mixed(make_runner, work):
    pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")
    txt = work / "nota.txt"
    txt.write_text("ciao\n", encoding="utf-8")
    img = work / "foto.png"
    img.write_bytes(png_bytes())
    job = run(make_runner, "docs.mixed.pdf", [txt, img])
    ok(job)
    assert sorted(p.name for p in out_paths(job)) == ["foto.pdf", "nota.pdf"]


def test_merge_docx_with_pandoc(make_runner, work):
    need("pandoc")
    a = work / "a.md"
    a.write_text("# Uno\n", encoding="utf-8")
    b = work / "b.txt"
    b.write_text("Due\n", encoding="utf-8")
    job = run(make_runner, "docs.merge.docx", [a, b])
    ok(job)
    assert out_paths(job)[0].suffix == ".docx"


def test_libreoffice_real_conversion(make_runner, work):
    need("soffice")
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Ciao LibreOffice")
    src = work / "lo test.docx"
    d.save(str(src))
    job = run(make_runner, "document.convert.pdf", [src])
    ok(job)
    assert out_paths(job)[0].read_bytes().startswith(b"%PDF")


def test_clipboard_copy(make_runner, work):
    need("wl-copy")
    src = work / "a.txt"
    src.write_text("copiami", encoding="utf-8")
    assert ok(run(make_runner, "text.copy", [src]))["kind"] == "none"


def test_italian_labels():
    assert i18n.t("docs.act.combine_pdf", "it") == "Combina in PDF"
    assert i18n.tn("docs.msg.images", 1, "it") == "1 immagine"
    assert i18n.tn("docs.msg.images", 4, "it") == "4 immagini"
    assert registry.display_label("subtitle.timing.shift", "it") == "Tempi · Sposta tempi…"


# ================================================================== bounded package members (F16)

def _bomb(z: zipfile.ZipFile, name: str, size: int = 8 * 1024 * 1024) -> None:
    """A member zipfile cannot bound: for bzip2 a whole compressed chunk inflates in one read() call."""
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_BZIP2
    z.writestr(info, b"0" * size)


def _bomb_file(path: Path, member: str, *others: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name in others:
            z.writestr(name, "<x/>")
        _bomb(z, member)
    return path


def test_office_kind_does_not_inflate_a_bomb(tmp_path):
    import tracemalloc

    odt = _bomb_file(tmp_path / "bomb.odt", "mimetype")
    tracemalloc.start()
    try:
        assert F.office_kind(odt) is None          # "not this kind", not an error
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024
    both = _bomb_file(tmp_path / "both.odt", "mimetype", "content.xml", "META-INF/manifest.xml")
    assert F.office_kind(both) == "odf"            # the other evidence still counts


@pytest.mark.parametrize(("member", "read"), [
    ("word/document.xml", lambda p: F.office_text(p, "docx")),
    ("content.xml", lambda p: F.office_text(p, "odt")),
    ("content.xml", F.read_ods),
    ("content.xml", F.odp_slides),
    ("ppt/presentation.xml", F.pptx_slides),
    ("META-INF/container.xml", F.epub_info),
    ("META-INF/container.xml", F.epub_cover),
])
def test_package_readers_refuse_a_bomb(tmp_path, member, read):
    with pytest.raises(F.FormatError) as exc:
        read(_bomb_file(tmp_path / "bomb.zip", member))
    assert exc.value.key == "docs.err.too_big"


def test_best_effort_readers_ignore_a_bomb(tmp_path):
    odt = _bomb_file(tmp_path / "a.odt", "META-INF/manifest.xml", "content.xml")
    assert F.office_encrypted(odt, "odt") is False
    epub = _bomb_file(tmp_path / "a.epub", "META-INF/encryption.xml")
    assert F.epub_drm(epub) is False
    docx = _bomb_file(tmp_path / "a.docx", "word/fontTable.xml")
    assert F.office_fonts(docx) == []
    stats = _bomb_file(tmp_path / "b.docx", "docProps/app.xml")
    assert F.office_app_stats(stats) == {}
    cbz = _bomb_file(tmp_path / "a.cbz", "ComicInfo.xml")
    assert dict(F.cbz_info(cbz))["pages"] == "0"


def test_read_member_limit_and_damage(tmp_path, monkeypatch):
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.xml", b"x" * 5000)
    with zipfile.ZipFile(p) as z:
        assert F._read_member(z, "big.xml") == b"x" * 5000
        assert F._read_member(z, z.getinfo("big.xml"), limit=5000) == b"x" * 5000
        with pytest.raises(KeyError):
            F._read_member(z, "missing.xml")
        monkeypatch.setattr(F, "_MEMBER_LIMIT", 4999)
        with pytest.raises(F.FormatError) as exc:
            F._read_member(z, "big.xml")
        assert exc.value.key == "docs.err.too_big"
    stored = tmp_path / "s.zip"
    with zipfile.ZipFile(stored, "w") as z:
        z.writestr("part.xml", b"y" * 100)
    data = bytearray(stored.read_bytes())
    data[data.index(b"y" * 100)] = ord("z")        # the CRC no longer matches
    stored.write_bytes(bytes(data))
    with zipfile.ZipFile(stored) as z:
        with pytest.raises(F.FormatError) as exc:
            F._read_member(z, "part.xml")
    assert exc.value.key == "err.damaged"


@pytest.mark.parametrize("rewrite", [F.strip_office_metadata, F.compress_office_media],
                         ids=["strip", "compress"])
def test_rewriters_stream_a_long_embedded_video(tmp_path, monkeypatch, rewrite):
    video = os.urandom(200_000)
    src = make_docx(tmp_path / "v.docx")
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("word/media/movie.mp4", video)
        z.writestr("word/embeddings/big.bin", b"b" * 150_000)
    monkeypatch.setattr(F, "_MEMBER_LIMIT", 100_000)
    dst = tmp_path / "out.docx"
    rewrite(src, dst)
    with zipfile.ZipFile(dst) as z:
        assert z.testzip() is None
        assert z.read("word/media/movie.mp4") == video
        assert z.read("word/embeddings/big.bin") == b"b" * 150_000
        assert z.getinfo("word/media/movie.mp4").compress_type == (
            zipfile.ZIP_STORED if rewrite is F.compress_office_media else zipfile.ZIP_DEFLATED)
        assert b"Ciao mondo" in z.read("word/document.xml")


def test_rewriters_refuse_what_they_cannot_bound(tmp_path, monkeypatch):
    bomb = _bomb_file(tmp_path / "bomb.docx", "word/media/x.bin", "[Content_Types].xml")
    for rewrite in (F.strip_office_metadata, F.compress_office_media):
        with pytest.raises(F.FormatError) as exc:
            rewrite(bomb, tmp_path / "o.docx")
        assert exc.value.key == "docs.err.too_big"
    src = make_docx(tmp_path / "plain.docx")
    with monkeypatch.context() as m:
        m.setattr(F, "_PACKAGE_LIMIT", 100)        # the sum of the declared sizes
        for rewrite in (F.strip_office_metadata, F.compress_office_media):
            with pytest.raises(F.FormatError) as exc:
                rewrite(src, tmp_path / "o.docx")
            assert exc.value.key == "docs.err.too_big"
    with monkeypatch.context() as m:
        m.setattr(F, "_MEMBER_LIMIT", 100)         # an XML part cannot be rewritten without reading it
        with pytest.raises(F.FormatError) as exc:
            F.strip_office_metadata(src, tmp_path / "o.docx")
        assert exc.value.key == "docs.err.too_big"


def test_office_media_leaves_out_what_does_not_fit(tmp_path, monkeypatch):
    src = make_docx(tmp_path / "m.docx", image=jpeg_bytes((40, 20)))
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("word/media/movie.mp4", os.urandom(200_000))
    assert len(F.office_media(src)) == 3
    monkeypatch.setattr(F, "_MEMBER_LIMIT", 100_000)
    skipped: list[str] = []
    assert [name for name, _data in F.office_media(src, skipped)] == ["image1.jpeg", "image2.png"]
    assert skipped == ["movie.mp4"]
    monkeypatch.setattr(F, "_MEMBER_LIMIT", 1)
    with pytest.raises(F.FormatError) as exc:
        F.office_media(src)
    assert exc.value.key == "docs.err.too_big"
    monkeypatch.setattr(F, "_MEMBER_LIMIT", 100_000)
    monkeypatch.setattr(F, "_MEDIA_LIMIT", 10)
    with pytest.raises(F.FormatError) as exc:
        F.office_media(src)
    assert exc.value.key == "docs.err.too_big"


def test_bomb_reaches_the_user_as_a_message(make_runner, work):
    docx = _bomb_file(work / "bomb.docx", "word/document.xml", "[Content_Types].xml")
    job = run(make_runner, "document.info", [docx], lang="it")
    assert job.state == "failed" and "troppo grandi" in job.error["message"]
    job = run(make_runner, "document.info", [docx])
    assert job.state == "failed" and "too large" in job.error["message"]
    cbz = _bomb_file(work / "bomb.cbz", "001.jpg")
    job = run(make_runner, "ebook.extract.images", [cbz])
    assert job.state == "failed" and "too large" in job.error["message"]


def test_extract_images_tells_what_it_left_out(make_runner, work, monkeypatch):
    src = make_docx(work / "m.docx", image=jpeg_bytes((40, 20)))
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("word/media/movie.mp4", os.urandom(200_000))
    monkeypatch.setattr(F, "_MEMBER_LIMIT", 100_000)
    job = run(make_runner, "document.extract.images", [src])
    assert job.state == "done", job.error
    assert any("movie.mp4" in w for w in job.warnings)
