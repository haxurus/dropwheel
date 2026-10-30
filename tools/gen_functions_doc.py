#!/usr/bin/env python3
"""Generate docs/FUNCTIONS.md (English) and docs/FUNCTIONS.it.md (Italian).

They list every action of the wheel per source file type. The text comes from the real registry
(the same menus the wheel shows), so it cannot drift from the code as long as it is regenerated:

    PYTHONPATH=src python tools/gen_functions_doc.py

The output does not depend on the tools installed on this machine: availability is not listed,
only what each action needs. Requires Pillow (sample images).
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUTPUTS = {"en": ROOT / "docs" / "FUNCTIONS.md", "it": ROOT / "docs" / "FUNCTIONS.it.md"}

# Section title and formats per language, sample names of the single-file menu, of the multi-file menu.
SECTIONS = [
    ({"en": "Images", "it": "Immagini"},
     {"en": "JPG, PNG, WebP, AVIF, HEIC, JPEG XL, TIFF, BMP, ICO, PSD, RAW (the example is a PNG)",
      "it": "JPG, PNG, WebP, AVIF, HEIC, JPEG XL, TIFF, BMP, ICO, PSD, RAW (l'esempio è un PNG)"},
     ["foto.png"], ["foto.png", "altra.png"]),
    ({"en": "Animated images", "it": "Immagini animate"},
     {"en": "Animated GIF, WebP and APNG", "it": "GIF, WebP e APNG animati"}, ["anim.gif"], ["anim.gif", "anim2.gif"]),
    ({"en": "SVG", "it": "SVG"}, {"en": "SVG", "it": "SVG"}, ["logo.svg"], ["logo.svg", "logo2.svg"]),
    ({"en": "PDF", "it": "PDF"}, {"en": "PDF", "it": "PDF"}, ["documento.pdf"], ["documento.pdf", "altro.pdf"]),
    ({"en": "Documents", "it": "Documenti"}, {"en": "DOCX, DOC, ODT, RTF, WPS, Pages", "it": "DOCX, DOC, ODT, RTF, WPS, Pages"},
     ["lettera.docx"], ["lettera.docx", "lettera2.docx"]),
    ({"en": "Spreadsheets", "it": "Fogli di calcolo"}, {"en": "XLSX, XLS, ODS, CSV, TSV", "it": "XLSX, XLS, ODS, CSV, TSV"},
     ["conti.xlsx"], ["conti.xlsx", "conti2.xlsx"]),
    ({"en": "Presentations", "it": "Presentazioni"}, {"en": "PPTX, PPT, ODP, Keynote", "it": "PPTX, PPT, ODP, Keynote"},
     ["slide.pptx"], ["slide.pptx", "slide2.pptx"]),
    ({"en": "Markdown", "it": "Markdown"}, {"en": "MD, Markdown", "it": "MD, Markdown"}, ["note.md"], ["note.md", "note2.md"]),
    ({"en": "Text", "it": "Testo"}, {"en": "TXT, LOG", "it": "TXT, LOG"}, ["appunti.txt"], ["appunti.txt", "appunti2.txt"]),
    ({"en": "Code", "it": "Codice"}, {"en": "Source files and scripts", "it": "Sorgenti e script"},
     ["script.py"], ["script.py", "script2.py"]),
    ({"en": "Structured data", "it": "Dati strutturati"}, {"en": "JSON, YAML, TOML, XML, INI", "it": "JSON, YAML, TOML, XML, INI"},
     ["dati.json"], ["dati.json", "dati2.json"]),
    ({"en": "Ebooks", "it": "Ebook"}, {"en": "EPUB, MOBI, AZW3, FB2, DjVu, CBZ", "it": "EPUB, MOBI, AZW3, FB2, DjVu, CBZ"},
     ["libro.epub"], ["libro.epub", "libro2.epub"]),
    ({"en": "Subtitles", "it": "Sottotitoli"}, {"en": "SRT, VTT, ASS", "it": "SRT, VTT, ASS"}, ["film.srt"], ["film.srt", "film2.srt"]),
    ({"en": "Audio", "it": "Audio"}, {"en": "MP3, M4A, FLAC, WAV, Opus, OGG", "it": "MP3, M4A, FLAC, WAV, Opus, OGG"},
     ["canzone.mp3"], ["canzone.mp3", "canzone2.mp3"]),
    ({"en": "Video", "it": "Video"}, {"en": "MP4, MKV, WebM, MOV, AVI", "it": "MP4, MKV, WebM, MOV, AVI"},
     ["video.mp4"], ["video.mp4", "video2.mp4"]),
    ({"en": "Archives", "it": "Archivi"}, {"en": "ZIP, 7Z, RAR, TAR, GZ, XZ, ZST", "it": "ZIP, 7Z, RAR, TAR, GZ, XZ, ZST"},
     ["archivio.zip"], ["archivio.zip", "archivio2.zip"]),
    ({"en": "Fonts", "it": "Font"}, {"en": "TTF, OTF, WOFF, WOFF2", "it": "TTF, OTF, WOFF, WOFF2"}, ["font"], ["font", "font2"]),
    ({"en": "Folders", "it": "Cartelle"}, {"en": "Folders", "it": "Cartelle"}, ["cartella"], ["cartella", "cartella2"]),
    ({"en": "Links", "it": "Link"},
     {"en": "http, https and ftp addresses dragged from the browser", "it": "Indirizzi http, https e ftp trascinati dal browser"},
     ["https://example.com/articolo?id=1"], None),
    ({"en": "AppImage", "it": "AppImage"}, {"en": "AppImage", "it": "AppImage"}, ["App.AppImage"], None),
    ({"en": "Disk images", "it": "Immagini disco"}, {"en": "ISO, IMG, QCOW2, VHD", "it": "ISO, IMG, QCOW2, VHD"}, ["disco.iso"], None),
    ({"en": "Packages", "it": "Pacchetti"}, {"en": "pkg.tar.zst, DEB, RPM", "it": "pkg.tar.zst, DEB, RPM"}, ["pacchetto.deb"], None),
    ({"en": "Unknown files", "it": "File sconosciuti"},
     {"en": "Anything that is not recognised", "it": "Tutto ciò che non viene riconosciuto"}, ["dati.bin"], None),
]

MIXED = [
    ({"en": "Images and PDF together", "it": "Immagini e PDF insieme"}, ["foto.png", "documento.pdf"]),
    ({"en": "Image and audio together", "it": "Immagine e audio insieme"}, ["foto.png", "canzone.mp3"]),
    ({"en": "Documents and PDF together", "it": "Documenti e PDF insieme"}, ["lettera.docx", "documento.pdf"]),
    ({"en": "Unrelated types", "it": "Tipi diversi senza relazione"}, ["foto.png", "archivio.zip"]),
]

TEXT = {
    "en": {
        "title": "# Functions by file type",
        "switch": "**English** · [Italiano](FUNCTIONS.it.md)",
        "generated": ["> Generated by `tools/gen_functions_doc.py` from the wheel's real menus. Do not edit by hand:",
                      "> regenerate with `PYTHONPATH=src python tools/gen_functions_doc.py`."],
        "intro": ["Dropwheel registers **{n} actions**. The wheel shows only those that make sense for the dragged",
                  "files, at most eight per ring: a group opens another ring, and when there are too many entries the",
                  "last segment becomes **More**."],
        "legend_title": "How to read the lists:",
        "legend": ["- **Bold**: a group, that is a ring with more entries.",
                   "- **★**: the group's default entry. It is preselected when you open the ring from the keyboard, and",
                   "  it is what `dropwheel run GROUP` runs.",
                   "- *dialog*: opens an editor or a parameter form before starting.",
                   "- *requires*: the program it needs. If it is missing, the entry is greyed out and shows the command",
                   "  that installs it. Where there are alternatives, one is enough. Pillow, which Dropwheel always",
                   "  needs, is not listed."],
        "note": ["Conversion targets change with the source file: a JPG photo is not offered \"JPG\", and a PDF is not",
                 "offered \"PDF\". The lists below use the example format given in each section."],
        "contents": "## Contents",
        "mixed": "Mixed selections",
        "file": "File page",
        "formats": "Formats",
        "own": "Also on the File page",
        "multi": "### With several files",
        "mixed_intro": "When the files are of different types, the wheel offers only the actions that apply to all of them.",
        "only_file": "Only the actions of the File page.",
        "file_intro": ["Tapping Shift again, or using the page dot, switches the wheel to the actions common to every file",
                       "type."],
        "dialog": "dialog", "requires": "requires", "or": " or ",
    },
    "it": {
        "title": "# Funzioni per tipo di file",
        "switch": "[English](FUNCTIONS.md) · **Italiano**",
        "generated": ["> Generato da `tools/gen_functions_doc.py` a partire dai menu reali della ruota: non modificarlo",
                      "> a mano. Rigeneralo con `PYTHONPATH=src python tools/gen_functions_doc.py`."],
        "intro": ["Dropwheel registra **{n} azioni**. La ruota mostra solo quelle che hanno senso per i file",
                  "trascinati, al massimo otto per anello: un gruppo apre un altro anello, e quando le voci sono troppe",
                  "l'ultimo settore diventa **Altro**."],
        "legend_title": "Come leggere gli elenchi:",
        "legend": ["- **Grassetto**: un gruppo, cioè un anello con altre voci.",
                   "- **★**: la voce predefinita del gruppo. È preselezionata quando apri l'anello da tastiera, ed è",
                   "  quella che esegue `dropwheel run GRUPPO`.",
                   "- *finestra*: apre una finestra di modifica o di parametri prima di partire.",
                   "- *richiede*: il programma necessario. Se manca, la voce compare in grigio con il comando per",
                   "  installarlo. Dove ci sono alternative, ne basta una. Pillow, sempre necessario a Dropwheel, non",
                   "  è elencato."],
        "note": ["I formati di conversione cambiano con il file di partenza: una foto JPG non propone «JPG», un",
                 "PDF non propone «PDF». Gli elenchi qui sotto usano il formato di esempio indicato in ogni sezione."],
        "contents": "## Indice",
        "mixed": "Selezioni miste",
        "file": "Pagina File",
        "formats": "Formati",
        "own": "In più nella pagina File",
        "multi": "### Con più file",
        "mixed_intro": "Quando i file sono di tipi diversi, la ruota propone solo le azioni che valgono per tutti.",
        "only_file": "Solo le azioni della pagina File.",
        "file_intro": ["Premendo di nuovo Shift, o con il pallino di pagina, la ruota passa alle azioni comuni a",
                       "tutti i tipi di file."],
        "dialog": "finestra", "requires": "richiede", "or": " o ",
    },
}


def _ooxml(path: Path, part: str, content_type: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   f'<Override PartName="/{part}" ContentType="{content_type}"/></Types>')
        z.writestr(part, '<?xml version="1.0" encoding="UTF-8"?><root/>')


def _find_font() -> Path | None:
    candidates = [Path(r"C:\Windows\Fonts\arial.ttf")]
    for root in ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.local/share/fonts")):
        for dirpath, _dirs, names in os.walk(root):
            candidates.extend(Path(dirpath) / n for n in sorted(names) if n.lower().endswith(".ttf"))
    return next((c for c in candidates if c.is_file()), None)


def make_samples(d: Path) -> dict[str, str]:
    """Create one minimal real file per category and return {sample name: path or URL}."""
    from PIL import Image

    for name in ("foto.png", "altra.png"):
        Image.new("RGB", (64, 48), (220, 40, 40)).save(d / name)
    frames = [Image.new("RGB", (32, 32), c) for c in ((255, 0, 0), (0, 0, 255), (0, 255, 0))]
    for name in ("anim.gif", "anim2.gif"):
        frames[0].save(d / name, save_all=True, append_images=frames[1:], duration=100, loop=0)
    for name in ("logo.svg", "logo2.svg"):
        (d / name).write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
                              '<rect width="20" height="10" fill="red"/></svg>', encoding="utf-8")
    pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages"
           b"/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox"
           b"[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    for name in ("documento.pdf", "altro.pdf"):
        (d / name).write_bytes(pdf)
    office = {
        "lettera": ("docx", "word/document.xml", "wordprocessingml.document.main+xml"),
        "conti": ("xlsx", "xl/workbook.xml", "spreadsheetml.sheet.main+xml"),
        "slide": ("pptx", "ppt/presentation.xml", "presentationml.presentation.main+xml"),
    }
    for stem, (ext, part, kind) in office.items():
        for suffix in ("", "2"):
            _ooxml(d / f"{stem}{suffix}.{ext}", part, f"application/vnd.openxmlformats-officedocument.{kind}")
    plain = {
        "note.md": "# Titolo\n\nCiao *mondo*\n", "note2.md": "# Altro\n",
        "appunti.txt": "caffè e cornetto\n", "appunti2.txt": "altro testo\n",
        "script.py": "import os\nprint(os.name)\n", "script2.py": "print(1)\n",
        "dati.json": '{"a": 1, "b": [1, 2]}', "dati2.json": '{"c": 2}',
        "film.srt": "1\n00:00:01,000 --> 00:00:02,500\nCiao\n\n", "film2.srt": "1\n00:00:03,000 --> 00:00:04,000\nAltro\n\n",
    }
    for name, content in plain.items():
        (d / name).write_text(content, encoding="utf-8")
    for name in ("libro.epub", "libro2.epub"):
        with zipfile.ZipFile(d / name, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
            z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container/>')
    for name in ("canzone.mp3", "canzone2.mp3"):
        (d / name).write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 4000)
    mp4 = (struct.pack(">I", 24) + b"ftypisom" + b"\x00\x00\x02\x00" + b"isomiso2"
           + struct.pack(">I", 8) + b"free" + b"\x00" * 2000)
    for name in ("video.mp4", "video2.mp4"):
        (d / name).write_bytes(mp4)
    for name in ("archivio.zip", "archivio2.zip"):
        with zipfile.ZipFile(d / name, "w") as z:
            z.writestr("dentro.txt", "x")
    for name in ("cartella", "cartella2"):
        (d / name).mkdir()
        (d / name / "file.txt").write_text("x", encoding="utf-8")
    (d / "App.AppImage").write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 4096)
    iso = bytearray(0x8800)
    iso[0x8001:0x8006] = b"CD001"
    iso[0x8006] = 1
    (d / "disco.iso").write_bytes(bytes(iso))
    (d / "pacchetto.deb").write_bytes(b"!<arch>\ndebian-binary   " + b"0" * 200)
    (d / "dati.bin").write_bytes(bytes(range(256)) * 16)

    samples = {p.name: str(p) for p in d.iterdir()}
    font = _find_font()
    if font is not None:
        for name in ("font", "font2"):
            target = d / f"{name}.ttf"
            target.write_bytes(font.read_bytes())
            samples[name] = str(target)
    samples["https://example.com/articolo?id=1"] = "https://example.com/articolo?id=1"
    return samples


def requires_text(action_id: str, registry, tools, joiner: str) -> str:
    """The programs an action needs; alternatives are joined with ``joiner``."""
    try:
        requires = registry.get(action_id).requires or []
    except Exception:
        return ""
    parts = []
    for item in requires:
        names = [tools.display_name(x) for x in (item if isinstance(item, (tuple, list)) else [item])]
        # Pillow is a hard dependency of Dropwheel: an action that can use it always works.
        if "python-pillow" not in names:
            parts.append(joiner.join(names))
    return ", ".join(parts)


def render_nodes(nodes: list[dict], registry, tools, text: dict, depth: int = 0) -> list[str]:
    lines: list[str] = []
    pad = "  " * depth
    for node in nodes:
        label = node.get("label") or node.get("text") or node.get("id")
        if node.get("type") == "group":
            lines.append(f"{pad}- **{label}**")
            lines.extend(render_nodes(node.get("items") or [], registry, tools, text, depth + 1))
            continue
        marks = " ★" if node.get("default") else ""
        extra = ""
        if node.get("mode") == "dialog":
            extra += f" · *{text['dialog']}*"
        needs = requires_text(node["id"], registry, tools, text["or"])
        if needs:
            extra += f" · {text['requires']} {needs}"
        # A description may quote Markdown syntax: it must show as text, not become a link.
        description = (node.get("description") or "").strip().replace("[", "\\[")
        if description:
            extra += f" — {description}"
        lines.append(f"{pad}- {label}{marks}{extra}")
    return lines


def leaves(nodes: list[dict]) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in nodes:
        if node.get("type") == "group":
            found.update(leaves(node.get("items") or []))
        else:
            found[node["id"]] = node.get("title") or node.get("label") or node["id"]
    return found


def page(menu: dict, page_id: str) -> list[dict]:
    for p in menu.get("pages") or []:
        if p.get("id") == page_id:
            return p.get("items") or []
    return []


def build_document(lang: str, samples: dict[str, str], config: dict, registry, tools) -> list[str]:
    text = TEXT[lang]

    def menu_for(names: list[str]) -> dict:
        return registry.build_menu([samples[n] for n in names], config, lang, "drag")

    def render(nodes: list[dict]) -> list[str]:
        return render_nodes(nodes, registry, tools, text)

    common = page(menu_for(["foto.png"]), "file")
    common_ids = set(leaves(common))
    out = [text["title"], "", text["switch"], "", *text["generated"], "",
           *(line.format(n=len(registry.all_actions())) for line in text["intro"]), "",
           text["legend_title"], "", *text["legend"], "", *text["note"], "", text["contents"], ""]
    for title in [s[0][lang] for s in SECTIONS] + [text["mixed"], text["file"]]:
        out.append(f"- [{title}](#{title.lower().replace(' ', '-')})")
    out.append("")

    for titles, formats, single, multi in SECTIONS:
        title = titles[lang]
        if any(n not in samples for n in single):
            print(f"skipped {title}: no sample on this machine", file=sys.stderr)
            continue
        menu = menu_for(single)
        out += [f"## {title}", "", f"{text['formats']}: {formats[lang]}.", ""]
        out += render(page(menu, "main"))
        out.append("")
        own = {k: v for k, v in leaves(page(menu, "file")).items() if k not in common_ids}
        if own:
            out += [f"{text['own']}: " + ", ".join(sorted(own.values())) + ".", ""]
        if multi and all(n in samples for n in multi):
            out += [text["multi"], ""]
            out += render(page(menu_for(multi), "main"))
            out.append("")

    out += [f"## {text['mixed']}", "", text["mixed_intro"], ""]
    for titles, names in MIXED:
        out += [f"### {titles[lang]}", ""]
        nodes = page(menu_for(names), "main")
        out += render(nodes) if nodes else [text["only_file"]]
        out.append("")

    out += [f"## {text['file']}", "", *text["file_intro"], ""]
    out += render(common)
    out.append("")
    return out


def main() -> int:
    os.environ.setdefault("DROPWHEEL_NO_NOTIFY", "1")
    from dropwheel import actions, registry, tools
    from dropwheel import config as cfgmod

    with tempfile.TemporaryDirectory(prefix="dropwheel-doc-") as tmp:
        samples = make_samples(Path(tmp))
        config = cfgmod.load(Path(tmp) / "no-config.toml")
        actions.load_all(config)
        if actions.LOAD_ERRORS:
            print("load errors:", actions.LOAD_ERRORS, file=sys.stderr)
            return 1
        for lang, output in OUTPUTS.items():
            lines = build_document(lang, samples, config, registry, tools)
            output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
            print(f"{output}: {len(lines)} lines, {len(registry.all_actions())} actions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
