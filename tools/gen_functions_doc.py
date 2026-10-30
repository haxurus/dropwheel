#!/usr/bin/env python3
"""Generate docs/FUNZIONI.md: every action of the wheel, per source file type, in Italian.

The text comes from the real registry (the same menus the wheel shows), so it cannot drift from
the code as long as it is regenerated:

    PYTHONPATH=src python tools/gen_functions_doc.py

The output does not depend on the tools installed on this machine: availability is not listed,
only what each action needs. Requires Pillow (sample images); PyMuPDF is optional.
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
OUTPUT = ROOT / "docs" / "FUNZIONI.md"
LANG = "it"

# Section title, formats covered by the section, sample names of the single-file menu, of the multi-file menu.
SECTIONS = [
    ("Immagini", "JPG, PNG, WebP, AVIF, HEIC, JPEG XL, TIFF, BMP, ICO, PSD, RAW (l'esempio è un PNG)", ["foto.png"], ["foto.png", "altra.png"]),
    ("Immagini animate", "GIF, WebP e APNG animati", ["anim.gif"], ["anim.gif", "anim2.gif"]),
    ("SVG", "SVG", ["logo.svg"], ["logo.svg", "logo2.svg"]),
    ("PDF", "PDF", ["documento.pdf"], ["documento.pdf", "altro.pdf"]),
    ("Documenti", "DOCX, DOC, ODT, RTF, WPS, Pages", ["lettera.docx"], ["lettera.docx", "lettera2.docx"]),
    ("Fogli di calcolo", "XLSX, XLS, ODS, CSV, TSV", ["conti.xlsx"], ["conti.xlsx", "conti2.xlsx"]),
    ("Presentazioni", "PPTX, PPT, ODP, Keynote", ["slide.pptx"], ["slide.pptx", "slide2.pptx"]),
    ("Markdown", "MD, Markdown", ["note.md"], ["note.md", "note2.md"]),
    ("Testo", "TXT, LOG", ["appunti.txt"], ["appunti.txt", "appunti2.txt"]),
    ("Codice", "Sorgenti e script", ["script.py"], ["script.py", "script2.py"]),
    ("Dati strutturati", "JSON, YAML, TOML, XML, INI", ["dati.json"], ["dati.json", "dati2.json"]),
    ("Ebook", "EPUB, MOBI, AZW3, FB2, DjVu, CBZ", ["libro.epub"], ["libro.epub", "libro2.epub"]),
    ("Sottotitoli", "SRT, VTT, ASS", ["film.srt"], ["film.srt", "film2.srt"]),
    ("Audio", "MP3, M4A, FLAC, WAV, Opus, OGG", ["canzone.mp3"], ["canzone.mp3", "canzone2.mp3"]),
    ("Video", "MP4, MKV, WebM, MOV, AVI", ["video.mp4"], ["video.mp4", "video2.mp4"]),
    ("Archivi", "ZIP, 7Z, RAR, TAR, GZ, XZ, ZST", ["archivio.zip"], ["archivio.zip", "archivio2.zip"]),
    ("Font", "TTF, OTF, WOFF, WOFF2", ["font"], ["font", "font2"]),
    ("Cartelle", "Cartelle", ["cartella"], ["cartella", "cartella2"]),
    ("Link", "Indirizzi http, https e ftp trascinati dal browser", ["https://example.com/articolo?id=1"], None),
    ("AppImage", "AppImage", ["App.AppImage"], None),
    ("Immagini disco", "ISO, IMG, QCOW2, VHD", ["disco.iso"], None),
    ("Pacchetti", "pkg.tar.zst, DEB, RPM", ["pacchetto.deb"], None),
    ("File sconosciuti", "Tutto ciò che non viene riconosciuto", ["dati.bin"], None),
]

MIXED = [
    ("Immagini e PDF insieme", ["foto.png", "documento.pdf"]),
    ("Immagine e audio insieme", ["foto.png", "canzone.mp3"]),
    ("Documenti e PDF insieme", ["lettera.docx", "documento.pdf"]),
    ("Tipi diversi senza relazione", ["foto.png", "archivio.zip"]),
]


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


def requires_text(action_id: str, registry, tools) -> str:
    """The programs an action needs, 'a o b' for alternatives."""
    try:
        requires = registry.get(action_id).requires or []
    except Exception:
        return ""
    parts = []
    for item in requires:
        names = [tools.display_name(x) for x in (item if isinstance(item, (tuple, list)) else [item])]
        # Pillow is a hard dependency of Dropwheel: an action that can use it always works.
        if "python-pillow" not in names:
            parts.append(" o ".join(names))
    return ", ".join(parts)


def render_nodes(nodes: list[dict], registry, tools, depth: int = 0) -> list[str]:
    lines: list[str] = []
    pad = "  " * depth
    for node in nodes:
        label = node.get("label") or node.get("text") or node.get("id")
        if node.get("type") == "group":
            lines.append(f"{pad}- **{label}**")
            lines.extend(render_nodes(node.get("items") or [], registry, tools, depth + 1))
            continue
        marks = " ★" if node.get("default") else ""
        extra = ""
        if node.get("mode") == "dialog":
            extra += " · *finestra*"
        needs = requires_text(node["id"], registry, tools)
        if needs:
            extra += f" · richiede {needs}"
        description = (node.get("description") or "").strip()
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


def main() -> int:
    os.environ.setdefault("DROPWHEEL_NO_NOTIFY", "1")
    from dropwheel import actions, registry, tools
    from dropwheel import config as cfgmod

    with tempfile.TemporaryDirectory(prefix="dropwheel-doc-") as tmp:
        samples = make_samples(Path(tmp))
        config = cfgmod.load(Path(tmp) / "nessuna-config.toml")
        config["show_unavailable"] = True
        actions.load_all(config)
        if actions.LOAD_ERRORS:
            print("load errors:", actions.LOAD_ERRORS, file=sys.stderr)
            return 1

        def menu_for(names: list[str]) -> dict:
            return registry.build_menu([samples[n] for n in names], config, LANG, "drag")

        common = page(menu_for(["foto.png"]), "file")
        common_ids = set(leaves(common))
        out = [
            "# Funzioni per tipo di file",
            "",
            "> Generato da `tools/gen_functions_doc.py` a partire dai menu reali della ruota: non modificarlo",
            "> a mano. Rigeneralo con `PYTHONPATH=src python tools/gen_functions_doc.py`.",
            "",
            f"Dropwheel registra **{len(registry.all_actions())} azioni**. La ruota mostra solo quelle che",
            "hanno senso per i file trascinati, al massimo otto per anello: un gruppo apre un altro anello, e",
            "quando le voci sono troppe l'ultimo settore diventa **Altro**.",
            "",
            "Come leggere gli elenchi:",
            "",
            "- **Grassetto**: un gruppo, cioè un anello con altre voci.",
            "- **★**: la voce predefinita del gruppo, eseguita rilasciando il file sul gruppo stesso.",
            "- *finestra*: apre una finestra di modifica o di parametri prima di partire.",
            "- *richiede*: il programma necessario. Se manca, la voce compare in grigio con il comando per",
            "  installarlo. Dove ci sono alternative, ne basta una.",
            "",
            "I formati di conversione cambiano con il file di partenza: una foto JPG non propone «JPG», un",
            "PDF non propone «PDF». Gli elenchi qui sotto usano il formato di esempio indicato in ogni sezione.",
            "",
            "## Indice",
            "",
        ]
        titles = [s[0] for s in SECTIONS] + ["Selezioni miste", "Pagina File"]
        for title in titles:
            anchor = title.lower().replace(" ", "-")
            out.append(f"- [{title}](#{anchor})")
        out.append("")

        for title, formats, single, multi in SECTIONS:
            if any(n not in samples for n in single):
                print(f"skipped {title}: no sample on this machine", file=sys.stderr)
                continue
            menu = menu_for(single)
            out += [f"## {title}", "", f"Formati: {formats}.", ""]
            out += render_nodes(page(menu, "main"), registry, tools)
            out.append("")
            own = {k: v for k, v in leaves(page(menu, "file")).items() if k not in common_ids}
            if own:
                out += ["In più nella pagina File: " + ", ".join(sorted(own.values())) + ".", ""]
            if multi and all(n in samples for n in multi):
                out += ["### Con più file", ""]
                out += render_nodes(page(menu_for(multi), "main"), registry, tools)
                out.append("")

        out += ["## Selezioni miste", "",
                "Quando i file sono di tipi diversi, la ruota propone solo le azioni che valgono per tutti.", ""]
        for title, names in MIXED:
            out += [f"### {title}", ""]
            nodes = page(menu_for(names), "main")
            out += render_nodes(nodes, registry, tools) if nodes else ["Solo le azioni della pagina File."]
            out.append("")

        out += ["## Pagina File", "",
                "Premendo di nuovo Shift, o con il pallino di pagina, la ruota passa alle azioni comuni a",
                "tutti i tipi di file.", ""]
        out += render_nodes(common, registry, tools)
        out.append("")

    OUTPUT.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"{OUTPUT}: {len(out)} lines, {len(registry.all_actions())} actions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
