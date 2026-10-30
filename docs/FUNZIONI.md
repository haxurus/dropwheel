# Funzioni per tipo di file

> Generato da `tools/gen_functions_doc.py` a partire dai menu reali della ruota: non modificarlo
> a mano. Rigeneralo con `PYTHONPATH=src python tools/gen_functions_doc.py`.

Dropwheel registra **448 azioni**. La ruota mostra solo quelle che
hanno senso per i file trascinati, al massimo otto per anello: un gruppo apre un altro anello, e
quando le voci sono troppe l'ultimo settore diventa **Altro**.

Come leggere gli elenchi:

- **Grassetto**: un gruppo, cioè un anello con altre voci.
- **★**: la voce predefinita del gruppo, eseguita rilasciando il file sul gruppo stesso.
- *finestra*: apre una finestra di modifica o di parametri prima di partire.
- *richiede*: il programma necessario. Se manca, la voce compare in grigio con il comando per
  installarlo. Dove ci sono alternative, ne basta una.

I formati di conversione cambiano con il file di partenza: una foto JPG non propone «JPG», un
PDF non propone «PDF». Gli elenchi qui sotto usano il formato di esempio indicato in ogni sezione.

## Indice

- [Immagini](#immagini)
- [Immagini animate](#immagini-animate)
- [SVG](#svg)
- [PDF](#pdf)
- [Documenti](#documenti)
- [Fogli di calcolo](#fogli-di-calcolo)
- [Presentazioni](#presentazioni)
- [Markdown](#markdown)
- [Testo](#testo)
- [Codice](#codice)
- [Dati strutturati](#dati-strutturati)
- [Ebook](#ebook)
- [Sottotitoli](#sottotitoli)
- [Audio](#audio)
- [Video](#video)
- [Archivi](#archivi)
- [Font](#font)
- [Cartelle](#cartelle)
- [Link](#link)
- [AppImage](#appimage)
- [Immagini disco](#immagini-disco)
- [Pacchetti](#pacchetti)
- [File sconosciuti](#file-sconosciuti)
- [Selezioni miste](#selezioni-miste)
- [Pagina File](#pagina-file)

## Immagini

Formati: JPG, PNG, WebP, AVIF, HEIC, JPEG XL, TIFF, BMP, ICO, PSD, RAW (l'esempio è un PNG).

- **Converti**
  - JPG ★ — JPEG con la qualità impostata; la trasparenza diventa il colore di sfondo
  - WEBP — WebP, con o senza perdita
  - AVIF — AVIF, file piccoli e alta qualità
  - PDF — PDF di una pagina grande quanto l'immagine
  - JXL · richiede python-pillow-jpegxl-plugin o cjxl o magick — JPEG XL (i JPEG sono convertiti senza perdita)
  - TIFF — TIFF con compressione senza perdita
  - **Altro**
    - HEIC · richiede python-pillow-heif o heif-enc o magick — HEIC come sui telefoni
    - GIF ★ — GIF statica (256 colori)
    - BMP — BMP non compresso
    - ICO — Icona Windows con tutte le misure da 16 a 256 px
    - SVG · richiede vtracer o python-vtracer o potrace — Ricalca l'immagine in un vettoriale SVG
- **Comprimi**
  - Bilanciata ★ — Buona qualità, file molto più piccolo
  - Leggera — Differenze quasi invisibili
  - Forte — File minimo, lato lungo fino a 2048 px
  - Per il web — WebP, massimo 1920 px, sRGB, senza posizione GPS
  - Senza perdita — Più piccolo senza cambiare un pixel
  - Dimensione file… · *finestra* — Raggiungi un peso, per esempio 500 KB
- **Ridimensiona**
  - 1920 px ★ — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 2560 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 1280 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 3840 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 50 % — Scala di questa percentuale
  - 25 % — Scala di questa percentuale
  - Su misura… · *finestra* — Larghezza e altezza: adatta / riempi / deforma
  - **Social**
    - Instagram 4:5 ★ — Misura esatta per i social (riempie e ritaglia al centro)
    - Instagram 1:1 — Misura esatta per i social (riempie e ritaglia al centro)
    - Storia / Reel — Misura esatta per i social (riempie e ritaglia al centro)
    - Miniatura YouTube — Misura esatta per i social (riempie e ritaglia al centro)
    - X — Misura esatta per i social (riempie e ritaglia al centro)
    - LinkedIn — Misura esatta per i social (riempie e ritaglia al centro)
    - Il mio monitor · richiede hyprctl — Esattamente la risoluzione del monitor attuale
- Ritaglia… · *finestra* — Disegna l'area da tenere, anche con proporzioni fisse
- **Modifica**
  - Ruota a destra ★ — 90° in senso orario (senza perdita per i JPEG)
  - Ruota a sinistra — 90° in senso antiorario (senza perdita per i JPEG)
  - **Rifletti**
    - Orizzontale ★ — Specchia sinistra ↔ destra
    - Verticale — Specchia alto ↕ basso
  - Regola… · *finestra* — Esposizione, contrasto, colori, nitidezza, vignettatura
  - Annota · richiede satty o swappy — Disegna frecce, testo ed evidenziazioni su una copia
  - Filigrana… · *finestra* — Testo o logo, posizione, opacità, ripetizione
  - Bianco e nero — Copia in bianco e nero
  - **Altro**
    - Inverti colori ★ — Copia in negativo
    - Rifila bordi — Togli i bordi uniformi attorno all'immagine
- **Sfondo**
  - Rimuovi · richiede rembg o rembg — Scontorna il soggetto (PNG trasparente)
  - Aggiungi sfondo… ★ · *finestra* — Margini, angoli arrotondati, ombra e colore, sfumatura o immagine dietro
  - Trasparente → colore — Riempi le zone trasparenti con un colore
  - Bianco → trasparente — Rendi trasparente lo sfondo bianco
  - Ritaglia sul soggetto — Togli i margini trasparenti
- **Privacy**
  - Rimuovi posizione ★ — Togli la posizione GPS e lascia il resto
  - Rimuovi tutti i metadati — Togli fotocamera, data, posizione e autore
  - Oscura… · *finestra* — Copri parti dell'immagine con riquadri, sfocatura o pixel
  - Metadati… · *finestra* · richiede exiftool — Vedi e modifica i dati EXIF / XMP / IPTC
  - Rinomina per data — Rinomina con la data di scatto
- **Altro**
  - **OCR**
    - Copia testo ★ · richiede tesseract — Riconosci il testo e copialo
    - File di testo · richiede tesseract — Testo riconosciuto in un file .txt
    - PDF ricercabile · richiede tesseract — PDF con testo selezionabile e ricercabile
  - Leggi QR · richiede zbarimg — Leggi codici QR e a barre; il contenuto viene copiato
  - **Ingrandisci**
    - Ingrandisci ×2 ★ — Doppia dimensione con l'ingrandimento IA
    - Ingrandisci ×4 — Quadrupla dimensione con l'ingrandimento IA
    - Disegni ×4 — ×4 ottimizzato per disegni, anime e fumetti
  - **Vettorizza**
    - Bianco e nero ★ · richiede potrace — Ricalco in bianco e nero (loghi, schizzi)
    - A colori · richiede vtracer o python-vtracer — Ricalco a colori in forme
  - **Icone**
    - Icona ICO ★ — Icona Windows, misure 16–256 px
    - Serie PNG — Icone PNG da 16 a 512 px in una cartella
    - Pacchetto favicon — Icone per siti web con manifest e codice HTML
  - Copia immagine · richiede wl-copy — Copia l'immagine negli appunti
  - Usa come sfondo · richiede caelestia o swww o hyprctl — Usa come sfondo del desktop (aggiorna i colori di Caelestia)
  - Stampa · richiede lp — Invia alla stampante predefinita

### Con più file

- **Combina**
  - Immagini → PDF ★ — Una pagina per immagine
  - GIF/WebP animata… · *finestra* — Immagini in sequenza come animazione
  - Video presentazione… · *finestra* · richiede ffmpeg — Presentazione MP4 con durata per immagine
  - Collage… · *finestra* — Griglia, riga o colonna in un'unica immagine
- **Converti**
  - JPG ★ — JPEG con la qualità impostata; la trasparenza diventa il colore di sfondo
  - WEBP — WebP, con o senza perdita
  - AVIF — AVIF, file piccoli e alta qualità
  - PDF — PDF di una pagina grande quanto l'immagine
  - JXL · richiede python-pillow-jpegxl-plugin o cjxl o magick — JPEG XL (i JPEG sono convertiti senza perdita)
  - TIFF — TIFF con compressione senza perdita
  - **Altro**
    - HEIC · richiede python-pillow-heif o heif-enc o magick — HEIC come sui telefoni
    - GIF ★ — GIF statica (256 colori)
    - BMP — BMP non compresso
    - ICO — Icona Windows con tutte le misure da 16 a 256 px
    - SVG · richiede vtracer o python-vtracer o potrace — Ricalca l'immagine in un vettoriale SVG
- **Comprimi**
  - Bilanciata ★ — Buona qualità, file molto più piccolo
  - Leggera — Differenze quasi invisibili
  - Forte — File minimo, lato lungo fino a 2048 px
  - Per il web — WebP, massimo 1920 px, sRGB, senza posizione GPS
  - Senza perdita — Più piccolo senza cambiare un pixel
  - Dimensione file… · *finestra* — Raggiungi un peso, per esempio 500 KB
- **Ridimensiona**
  - 1920 px ★ — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 2560 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 1280 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 3840 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 50 % — Scala di questa percentuale
  - 25 % — Scala di questa percentuale
  - Su misura… · *finestra* — Larghezza e altezza: adatta / riempi / deforma
  - **Social**
    - Instagram 4:5 ★ — Misura esatta per i social (riempie e ritaglia al centro)
    - Instagram 1:1 — Misura esatta per i social (riempie e ritaglia al centro)
    - Storia / Reel — Misura esatta per i social (riempie e ritaglia al centro)
    - Miniatura YouTube — Misura esatta per i social (riempie e ritaglia al centro)
    - X — Misura esatta per i social (riempie e ritaglia al centro)
    - LinkedIn — Misura esatta per i social (riempie e ritaglia al centro)
    - Il mio monitor · richiede hyprctl — Esattamente la risoluzione del monitor attuale
- Ritaglia… · *finestra* — Disegna l'area da tenere, anche con proporzioni fisse
- **Privacy**
  - Rimuovi posizione ★ — Togli la posizione GPS e lascia il resto
  - Rimuovi tutti i metadati — Togli fotocamera, data, posizione e autore
  - Oscura… · *finestra* — Copri parti dell'immagine con riquadri, sfocatura o pixel
  - Metadati… · *finestra* · richiede exiftool — Vedi e modifica i dati EXIF / XMP / IPTC
  - Rinomina per data — Rinomina con la data di scatto
- **Modifica**
  - Ruota a destra ★ — 90° in senso orario (senza perdita per i JPEG)
  - Ruota a sinistra — 90° in senso antiorario (senza perdita per i JPEG)
  - **Rifletti**
    - Orizzontale ★ — Specchia sinistra ↔ destra
    - Verticale — Specchia alto ↕ basso
  - Regola… · *finestra* — Esposizione, contrasto, colori, nitidezza, vignettatura
  - Filigrana… · *finestra* — Testo o logo, posizione, opacità, ripetizione
  - Bianco e nero — Copia in bianco e nero
  - **Altro**
    - Inverti colori ★ — Copia in negativo
    - Rifila bordi — Togli i bordi uniformi attorno all'immagine
- **Altro**
  - **Sfondo**
    - Rimuovi · richiede rembg o rembg — Scontorna il soggetto (PNG trasparente)
    - Aggiungi sfondo… ★ · *finestra* — Margini, angoli arrotondati, ombra e colore, sfumatura o immagine dietro
    - Trasparente → colore — Riempi le zone trasparenti con un colore
    - Bianco → trasparente — Rendi trasparente lo sfondo bianco
    - Ritaglia sul soggetto — Togli i margini trasparenti
  - **OCR**
    - Copia testo ★ · richiede tesseract — Riconosci il testo e copialo
    - File di testo · richiede tesseract — Testo riconosciuto in un file .txt
    - PDF ricercabile · richiede tesseract — PDF con testo selezionabile e ricercabile
  - **Ingrandisci**
    - Ingrandisci ×2 ★ — Doppia dimensione con l'ingrandimento IA
    - Ingrandisci ×4 — Quadrupla dimensione con l'ingrandimento IA
    - Disegni ×4 — ×4 ottimizzato per disegni, anime e fumetti
  - **Vettorizza**
    - Bianco e nero ★ · richiede potrace — Ricalco in bianco e nero (loghi, schizzi)
    - A colori · richiede vtracer o python-vtracer — Ricalco a colori in forme
  - **Icone**
    - Icona ICO ★ — Icona Windows, misure 16–256 px
    - Serie PNG — Icone PNG da 16 a 512 px in una cartella
    - Pacchetto favicon — Icone per siti web con manifest e codice HTML
  - Leggi QR · richiede zbarimg — Leggi codici QR e a barre; il contenuto viene copiato
  - Stampa · richiede lp — Invia alla stampante predefinita

## Immagini animate

Formati: GIF, WebP e APNG animati.

- **Converti**
  - MP4 · richiede ffmpeg — Video MP4, si apre ovunque
  - WEBM · richiede ffmpeg — Video WebM, mantiene la trasparenza
  - WEBP ★ — WebP animata, molto più leggera della GIF
  - Primo fotogramma — Salva il primo fotogramma in PNG
- **Comprimi**
  - Bilanciata ★ — Ottimizzazione GIF con perdita, buona qualità
  - Leggera — Ottimizzazione senza perdita
  - Forte — Meno colori e dimensione al 75 %
- **Ridimensiona**
  - 480 px ★ — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 720 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 320 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 50 % — Scala di questa percentuale
  - Su misura… · *finestra* — Larghezza e altezza: adatta / riempi / deforma
- Ritaglia… · *finestra* — Disegna l'area da tenere, anche con proporzioni fisse
- **Modifica**
  - Ruota a destra ★ — 90° in senso orario (senza perdita per i JPEG)
  - Ruota a sinistra — 90° in senso antiorario (senza perdita per i JPEG)
  - **Rifletti**
    - Orizzontale ★ — Specchia sinistra ↔ destra
    - Verticale — Specchia alto ↕ basso
- **Frame**
  - Estrai fotogrammi ★ — Ogni fotogramma in PNG in una cartella
  - Al contrario — Riproduci al contrario
  - Velocità — Riproduzione più veloce o più lenta
- **Privacy**
  - Rimuovi posizione ★ — Togli la posizione GPS e lascia il resto
  - Rimuovi tutti i metadati — Togli fotocamera, data, posizione e autore
  - Metadati… · *finestra* · richiede exiftool — Vedi e modifica i dati EXIF / XMP / IPTC
  - Rinomina per data — Rinomina con la data di scatto
- Copia immagine · richiede wl-copy — Copia l'immagine negli appunti

### Con più file

- **Combina**
  - Immagini → PDF ★ — Una pagina per immagine
  - GIF/WebP animata… · *finestra* — Immagini in sequenza come animazione
  - Video presentazione… · *finestra* · richiede ffmpeg — Presentazione MP4 con durata per immagine
  - Collage… · *finestra* — Griglia, riga o colonna in un'unica immagine
- **Converti**
  - MP4 · richiede ffmpeg — Video MP4, si apre ovunque
  - WEBM · richiede ffmpeg — Video WebM, mantiene la trasparenza
  - WEBP ★ — WebP animata, molto più leggera della GIF
  - Primo fotogramma — Salva il primo fotogramma in PNG
- **Comprimi**
  - Bilanciata ★ — Ottimizzazione GIF con perdita, buona qualità
  - Leggera — Ottimizzazione senza perdita
  - Forte — Meno colori e dimensione al 75 %
- **Ridimensiona**
  - 480 px ★ — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 720 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 320 px — Lato lungo a questa misura; le immagini più piccole restano invariate
  - 50 % — Scala di questa percentuale
  - Su misura… · *finestra* — Larghezza e altezza: adatta / riempi / deforma
- Ritaglia… · *finestra* — Disegna l'area da tenere, anche con proporzioni fisse
- **Privacy**
  - Rimuovi posizione ★ — Togli la posizione GPS e lascia il resto
  - Rimuovi tutti i metadati — Togli fotocamera, data, posizione e autore
  - Metadati… · *finestra* · richiede exiftool — Vedi e modifica i dati EXIF / XMP / IPTC
  - Rinomina per data — Rinomina con la data di scatto
- **Modifica**
  - Ruota a destra ★ — 90° in senso orario (senza perdita per i JPEG)
  - Ruota a sinistra — 90° in senso antiorario (senza perdita per i JPEG)
  - **Rifletti**
    - Orizzontale ★ — Specchia sinistra ↔ destra
    - Verticale — Specchia alto ↕ basso
- **Frame**
  - Estrai fotogrammi ★ — Ogni fotogramma in PNG in una cartella
  - Al contrario — Riproduci al contrario
  - Velocità — Riproduzione più veloce o più lenta

## SVG

Formati: SVG.

- **Converti**
  - **PNG**
    - Dimensione originale ★ · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - 512 px · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - 1024 px · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - 2048 px · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - 4096 px · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - Su misura… · *finestra* · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Scegli larghezza, altezza e sfondo
  - PDF · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — PDF vettoriale, nitido a ogni zoom
  - JPG · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale alla misura scelta
  - WEBP · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale alla misura scelta
  - ICO · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Icona Windows, misure 16–256 px
- Ottimizza — SVG più leggero, senza dati dell'editor e commenti
- Copia come testo · richiede wl-copy — Copia il codice SVG
- Copia immagine · richiede wl-copy — Copia l'immagine negli appunti
- **Icone**
  - Icona ICO ★ — Icona Windows, misure 16–256 px
  - Serie PNG — Icone PNG da 16 a 512 px in una cartella
  - Pacchetto favicon — Icone per siti web con manifest e codice HTML
- Leggi QR · richiede zbarimg — Leggi codici QR e a barre; il contenuto viene copiato
- **OCR**
  - Copia testo ★ · richiede tesseract — Riconosci il testo e copialo
  - File di testo · richiede tesseract — Testo riconosciuto in un file .txt
  - PDF ricercabile · richiede tesseract — PDF con testo selezionabile e ricercabile
- Stampa · richiede lp — Invia alla stampante predefinita

### Con più file

- **Converti**
  - **PNG**
    - Dimensione originale ★ · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - 512 px · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - 1024 px · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - 2048 px · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - 4096 px · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale in PNG
    - Su misura… · *finestra* · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Scegli larghezza, altezza e sfondo
  - PDF · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — PDF vettoriale, nitido a ogni zoom
  - JPG · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale alla misura scelta
  - WEBP · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Rasterizza il vettoriale alla misura scelta
  - ICO · richiede rsvg-convert o resvg o magick o python-cairosvg o inkscape — Icona Windows, misure 16–256 px
- **Combina**
  - Immagini → PDF ★ — Una pagina per immagine
  - GIF/WebP animata… · *finestra* — Immagini in sequenza come animazione
  - Video presentazione… · *finestra* · richiede ffmpeg — Presentazione MP4 con durata per immagine
  - Collage… · *finestra* — Griglia, riga o colonna in un'unica immagine
- Ottimizza — SVG più leggero, senza dati dell'editor e commenti
- **Icone**
  - Icona ICO ★ — Icona Windows, misure 16–256 px
  - Serie PNG — Icone PNG da 16 a 512 px in una cartella
  - Pacchetto favicon — Icone per siti web con manifest e codice HTML
- Stampa · richiede lp — Invia alla stampante predefinita
- **OCR**
  - Copia testo ★ · richiede tesseract — Riconosci il testo e copialo
  - File di testo · richiede tesseract — Testo riconosciuto in un file .txt
  - PDF ricercabile · richiede tesseract — PDF con testo selezionabile e ricercabile
- Leggi QR · richiede zbarimg — Leggi codici QR e a barre; il contenuto viene copiato

## PDF

Formati: PDF.

- **Converti**
  - DOCX · richiede python-pdf2docx o soffice — Documento Word modificabile
  - JPG ★ · richiede python-pymupdf o pdftoppm — Un'immagine per pagina (in una cartella se le pagine sono più di una)
  - PNG · richiede python-pymupdf o pdftoppm — Un'immagine per pagina (in una cartella se le pagine sono più di una)
  - TXT · richiede pdftotext o python-pymupdf — Testo semplice, mantenendo l'impaginazione
  - Markdown · richiede python-pymupdf — Markdown con titoli, elenchi e tabelle
  - EPUB · richiede python-pymupdf — Ebook a testo adattabile (EPUB 3)
  - HTML · richiede python-pymupdf — Un'unica pagina HTML con le immagini incorporate
  - **Altro**
    - PDF/A ★ · richiede gs — PDF/A-2b per l'archiviazione a lungo termine
    - Scala di grigi · richiede gs — Converte tutte le pagine in scala di grigi
- **Comprimi**
  - Bilanciata ★ · richiede gs o python-pymupdf — Immagini a 150 dpi: ideale per schermo ed email
  - Leggera · richiede gs o python-pymupdf — Ottimizza senza ridurre la risoluzione delle immagini
  - Forte · richiede gs o python-pymupdf — Immagini a 72 dpi: il file più piccolo
  - Su misura… · *finestra* · richiede gs o python-pymupdf — Scegli risoluzione e qualità delle immagini
- **Pagine**
  - Organizza… ★ · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Riordina, ruota, duplica ed elimina pagine
  - **Dividi**
    - Ogni pagina ★ · richiede python-pymupdf o python-pypdf o qpdf — Un PDF per ogni pagina
    - Ogni N pagine… · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Un PDF ogni N pagine
    - Per intervalli… · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Un PDF per intervallo, es. 1-3, 5, 7-
    - Per segnalibri · richiede python-pymupdf o python-pypdf — Un PDF per capitolo (segnalibro)
  - Estrai pagine… · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Un nuovo PDF con le pagine scelte
  - Elimina pagine… · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Una copia senza le pagine scelte
  - **Ruota**
    - Ruota a destra ★ · richiede python-pymupdf o python-pypdf o qpdf — Ruota tutte le pagine (o quelle scelte)
    - Ruota di 180° · richiede python-pymupdf o python-pypdf o qpdf — Ruota tutte le pagine (o quelle scelte)
    - Ruota a sinistra · richiede python-pymupdf o python-pypdf o qpdf — Ruota tutte le pagine (o quelle scelte)
  - Inverti ordine · richiede python-pymupdf o python-pypdf o qpdf — Pagine in ordine inverso
  - Pagina vuota · richiede python-pymupdf o python-pypdf o qpdf — Aggiunge pagine vuote
- **Sicurezza**
  - Proteggi… ★ · *finestra* · richiede qpdf o python-pikepdf o python-pymupdf — Crittografia AES-256 con password e permessi
  - Oscura… · *finestra* · richiede python-pymupdf — Rimuove definitivamente testo, immagini e grafica sotto le aree
  - Rimuovi metadati · richiede python-pymupdf o qpdf o python-pikepdf — Rimuove autore, software, date e dati XMP
  - Metadati… · *finestra* · richiede python-pymupdf o python-pypdf — Modifica titolo, autore, oggetto, parole chiave e date
- **OCR**
  - PDF ricercabile · richiede ocrmypdf o tesseract — Aggiunge un livello di testo selezionabile alle pagine scansionate
  - Copia testo ★ · richiede python-pymupdf o pdftotext — Copia il testo negli appunti (OCR per le scansioni)
- **Estrai**
  - Immagini ★ · richiede python-pymupdf o pdfimages — Immagini incorporate nel formato originale
  - Allegati · richiede python-pymupdf o pdfdetach o python-pypdf — File incorporati nel PDF (es. l'XML della fattura elettronica)
- Annota · richiede xournalpp o okular — Apri in Xournal++ per annotare o firmare
- **Altro**
  - Filigrana… · *finestra* · richiede python-pymupdf — Un timbro di testo sulle pagine
  - Numera pagine… · *finestra* · richiede python-pymupdf — Aggiunge i numeri di pagina
  - Appiattisci · richiede python-pymupdf o qpdf — Fonde campi modulo e annotazioni nelle pagine
  - Ripara · richiede qpdf o mutool o python-pymupdf o gs — Ricostruisce un PDF danneggiato
  - Stampa · richiede lp — Invia alla stampante
  - Informazioni ★ · richiede python-pymupdf o pdfinfo — Pagine, formati, versione, sicurezza e font

### Con più file

- Unisci · richiede python-pymupdf o python-pypdf o qpdf — Un unico PDF nell'ordine di trascinamento, con un segnalibro per file
- **Comprimi**
  - Bilanciata ★ · richiede gs o python-pymupdf — Immagini a 150 dpi: ideale per schermo ed email
  - Leggera · richiede gs o python-pymupdf — Ottimizza senza ridurre la risoluzione delle immagini
  - Forte · richiede gs o python-pymupdf — Immagini a 72 dpi: il file più piccolo
  - Su misura… · *finestra* · richiede gs o python-pymupdf — Scegli risoluzione e qualità delle immagini
- **Converti**
  - DOCX · richiede python-pdf2docx o soffice — Documento Word modificabile
  - JPG ★ · richiede python-pymupdf o pdftoppm — Un'immagine per pagina (in una cartella se le pagine sono più di una)
  - PNG · richiede python-pymupdf o pdftoppm — Un'immagine per pagina (in una cartella se le pagine sono più di una)
  - TXT · richiede pdftotext o python-pymupdf — Testo semplice, mantenendo l'impaginazione
  - Markdown · richiede python-pymupdf — Markdown con titoli, elenchi e tabelle
  - EPUB · richiede python-pymupdf — Ebook a testo adattabile (EPUB 3)
  - HTML · richiede python-pymupdf — Un'unica pagina HTML con le immagini incorporate
  - **Altro**
    - PDF/A ★ · richiede gs — PDF/A-2b per l'archiviazione a lungo termine
    - Scala di grigi · richiede gs — Converte tutte le pagine in scala di grigi
- **OCR**
  - PDF ricercabile · richiede ocrmypdf o tesseract — Aggiunge un livello di testo selezionabile alle pagine scansionate
  - Copia testo ★ · richiede python-pymupdf o pdftotext — Copia il testo negli appunti (OCR per le scansioni)
- Fronte-retro · richiede python-pymupdf o python-pypdf o qpdf — Fronti dal primo PDF, retri dal secondo (scansionati al contrario)
- Proteggi… · *finestra* · richiede qpdf o python-pikepdf o python-pymupdf — Crittografia AES-256 con password e permessi
- Rimuovi metadati · richiede python-pymupdf o qpdf o python-pikepdf — Rimuove autore, software, date e dati XMP
- **Altro**
  - **Ruota**
    - Ruota a destra ★ · richiede python-pymupdf o python-pypdf o qpdf — Ruota tutte le pagine (o quelle scelte)
    - Ruota di 180° · richiede python-pymupdf o python-pypdf o qpdf — Ruota tutte le pagine (o quelle scelte)
    - Ruota a sinistra · richiede python-pymupdf o python-pypdf o qpdf — Ruota tutte le pagine (o quelle scelte)
  - **Dividi**
    - Ogni pagina ★ · richiede python-pymupdf o python-pypdf o qpdf — Un PDF per ogni pagina
    - Ogni N pagine… · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Un PDF ogni N pagine
    - Per intervalli… · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Un PDF per intervallo, es. 1-3, 5, 7-
    - Per segnalibri · richiede python-pymupdf o python-pypdf — Un PDF per capitolo (segnalibro)
  - Inverti ordine · richiede python-pymupdf o python-pypdf o qpdf — Pagine in ordine inverso
  - Estrai pagine… · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Un nuovo PDF con le pagine scelte
  - Elimina pagine… · *finestra* · richiede python-pymupdf o python-pypdf o qpdf — Una copia senza le pagine scelte
  - Pagina vuota · richiede python-pymupdf o python-pypdf o qpdf — Aggiunge pagine vuote
  - **Estrai**
    - Immagini ★ · richiede python-pymupdf o pdfimages — Immagini incorporate nel formato originale
    - Allegati · richiede python-pymupdf o pdfdetach o python-pypdf — File incorporati nel PDF (es. l'XML della fattura elettronica)
  - **Altro**
    - Filigrana… · *finestra* · richiede python-pymupdf — Un timbro di testo sulle pagine
    - Numera pagine… · *finestra* · richiede python-pymupdf — Aggiunge i numeri di pagina
    - Appiattisci · richiede python-pymupdf o qpdf — Fonde campi modulo e annotazioni nelle pagine
    - Ripara · richiede qpdf o mutool o python-pymupdf o gs — Ricostruisce un PDF danneggiato
    - Stampa · richiede lp — Invia alla stampante
    - Informazioni ★ · richiede python-pymupdf o pdfinfo — Pagine, formati, versione, sicurezza e font

## Documenti

Formati: DOCX, DOC, ODT, RTF, WPS, Pages.

- **Converti**
  - PDF · richiede soffice — PDF con LibreOffice: segnalibri dai titoli, PDF/A opzionale per l'archiviazione
  - ODT · richiede soffice
  - Markdown · richiede pandoc — Testo Markdown; le immagini incorporate vengono salvate in una cartella media accanto
  - TXT ★
  - HTML · richiede pandoc o soffice
  - EPUB · richiede pandoc o soffice
  - RTF · richiede soffice
  - **Altro**
    - DOC · richiede soffice
    - Immagini delle pagine · richiede soffice, python-pymupdf o pdftoppm
- PDF · richiede soffice — PDF con LibreOffice: segnalibri dai titoli, PDF/A opzionale per l'archiviazione
- Comprimi — Ricomprime le immagini nel file; testo e impaginazione restano invariati
- Rimuovi metadati — Rimuove autore, azienda, date, percorso del modello e autori dei commenti
- **Estrai**
  - Estrai immagini ★
  - TXT
  - Markdown · richiede pandoc — Testo Markdown; le immagini incorporate vengono salvate in una cartella media accanto
- PDF con password · *finestra* · richiede soffice, python-pymupdf o python-pypdf — PDF che chiede una password per aprirsi (AES-256)
- Statistiche — Parole, caratteri, paragrafi e tempo di lettura

### Con più file

- Combina in PDF · richiede python-pymupdf o python-pypdf — Converte ogni file in PDF e li unisce, con un segnalibro per file
- **Converti**
  - PDF · richiede soffice — PDF con LibreOffice: segnalibri dai titoli, PDF/A opzionale per l'archiviazione
  - ODT · richiede soffice
  - Markdown · richiede pandoc — Testo Markdown; le immagini incorporate vengono salvate in una cartella media accanto
  - TXT ★
  - HTML · richiede pandoc o soffice
  - EPUB · richiede pandoc o soffice
  - RTF · richiede soffice
  - **Altro**
    - DOC · richiede soffice
    - Immagini delle pagine · richiede soffice, python-pymupdf o pdftoppm
- Unisci in DOCX · richiede pandoc — Unisce documenti, Markdown e testo in un unico documento Word
- Comprimi — Ricomprime le immagini nel file; testo e impaginazione restano invariati
- Rimuovi metadati — Rimuove autore, azienda, date, percorso del modello e autori dei commenti
- Statistiche — Parole, caratteri, paragrafi e tempo di lettura
- Estrai immagini
- PDF con password · *finestra* · richiede soffice, python-pymupdf o python-pypdf — PDF che chiede una password per aprirsi (AES-256)

## Fogli di calcolo

Formati: XLSX, XLS, ODS, CSV, TSV.

- **Converti**
  - ODS ★
  - CSV — Un CSV per foglio, in una cartella se i fogli sono più di uno
  - PDF · richiede soffice
  - JSON — Un elenco di record: la prima riga fornisce le chiavi
  - Tabella Markdown
  - HTML
  - TSV
- PDF · richiede soffice
- Rimuovi metadati — Rimuove autore, azienda, date, percorso del modello e autori dei commenti
- Info foglio

### Con più file

- Combina in XLSX · richiede python-openpyxl o soffice — Un'unica cartella di lavoro con un foglio per ogni file
- **Converti**
  - ODS ★
  - CSV — Un CSV per foglio, in una cartella se i fogli sono più di uno
  - PDF · richiede soffice
  - JSON — Un elenco di record: la prima riga fornisce le chiavi
  - Tabella Markdown
  - HTML
  - TSV
- Combina in PDF · richiede python-pymupdf o python-pypdf — Converte ogni file in PDF e li unisce, con un segnalibro per file
- Rimuovi metadati — Rimuove autore, azienda, date, percorso del modello e autori dei commenti
- Info foglio

## Presentazioni

Formati: PPTX, PPT, ODP, Keynote.

- **Converti**
  - PDF · richiede soffice
  - ODP · richiede soffice
  - Slide in PNG · richiede soffice, python-pymupdf o pdftoppm — Un'immagine per slide, in una cartella
  - Slide in JPG · richiede soffice, python-pymupdf o pdftoppm — Un'immagine per slide, in una cartella
  - Testo e note ★ — Schema Markdown con il testo e le note di ogni slide
- PDF · richiede soffice
- **Immagini slide**
  - Slide in PNG ★ · richiede soffice, python-pymupdf o pdftoppm — Un'immagine per slide, in una cartella
  - Slide in JPG · richiede soffice, python-pymupdf o pdftoppm — Un'immagine per slide, in una cartella
- Comprimi — Ricomprime le immagini nel file; testo e impaginazione restano invariati
- **Estrai**
  - Testo e note ★ — Schema Markdown con il testo e le note di ogni slide
  - Note del relatore
  - Estrai immagini
- Rimuovi metadati — Rimuove autore, azienda, date, percorso del modello e autori dei commenti
- Statistiche

### Con più file

- Combina in PDF · richiede python-pymupdf o python-pypdf — Converte ogni file in PDF e li unisce, con un segnalibro per file
- **Converti**
  - PDF · richiede soffice
  - ODP · richiede soffice
  - Slide in PNG · richiede soffice, python-pymupdf o pdftoppm — Un'immagine per slide, in una cartella
  - Slide in JPG · richiede soffice, python-pymupdf o pdftoppm — Un'immagine per slide, in una cartella
  - Testo e note ★ — Schema Markdown con il testo e le note di ogni slide
- Comprimi — Ricomprime le immagini nel file; testo e impaginazione restano invariati
- **Estrai**
  - Testo e note ★ — Schema Markdown con il testo e le note di ogni slide
  - Note del relatore
  - Estrai immagini
- Rimuovi metadati — Rimuove autore, azienda, date, percorso del modello e autori dei commenti
- Statistiche

## Markdown

Formati: MD, Markdown.

- **Converti**
  - PDF · richiede pandoc o python-markdown, typst o weasyprint o xelatex o tectonic o soffice, pandoc o weasyprint o soffice — PDF leggibile con codice evidenziato; usa Typst, WeasyPrint, LaTeX o LibreOffice
  - HTML · richiede pandoc o python-markdown — Pagina HTML unica con uno stile pulito e le immagini incorporate
  - DOCX · richiede pandoc o soffice, pandoc o python-markdown
  - ODT · richiede pandoc o soffice, pandoc o python-markdown
  - EPUB · richiede pandoc
  - TXT ★
  - RTF · richiede pandoc
- PDF · richiede pandoc o python-markdown, typst o weasyprint o xelatex o tectonic o soffice, pandoc o weasyprint o soffice — PDF leggibile con codice evidenziato; usa Typst, WeasyPrint, LaTeX o LibreOffice
- **Copia**
  - Copia testo ★ · richiede wl-copy — Copia tutto il contenuto negli appunti
  - Copia formattato · richiede wl-copy, pandoc o python-markdown — Copia il testo formattato da incollare in email, documenti o chat
- **Formatta**
  - Inserisci indice ★ — Aggiunge l'indice dopo il titolo (aggiornato se c'è già)
  - Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
  - LF (Linux) — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
  - UTF-8 — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
- **Slide**
  - PDF ★ · richiede pandoc, xelatex o tectonic — Slide Beamer: ogni titolo ## inizia una nuova slide
  - PPTX · richiede pandoc
  - Slide web · richiede pandoc — Presentazione reveal.js da aprire nel browser (serve internet per caricare reveal.js)
- Statistiche
- Indice — Mostra l'indice come elenco di link, pronto da copiare

### Con più file

- Combina in PDF · richiede python-pymupdf o python-pypdf — Converte ogni file in PDF e li unisce, con un segnalibro per file
- Unisci Markdown — Unisce i file Markdown in ordine di nome
- Unisci in DOCX · richiede pandoc — Unisce documenti, Markdown e testo in un unico documento Word
- **Converti**
  - PDF · richiede pandoc o python-markdown, typst o weasyprint o xelatex o tectonic o soffice, pandoc o weasyprint o soffice — PDF leggibile con codice evidenziato; usa Typst, WeasyPrint, LaTeX o LibreOffice
  - HTML · richiede pandoc o python-markdown — Pagina HTML unica con uno stile pulito e le immagini incorporate
  - DOCX · richiede pandoc o soffice, pandoc o python-markdown
  - ODT · richiede pandoc o soffice, pandoc o python-markdown
  - EPUB · richiede pandoc
  - TXT ★
  - RTF · richiede pandoc
- **Formatta**
  - Inserisci indice ★ — Aggiunge l'indice dopo il titolo (aggiornato se c'è già)
  - Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
  - LF (Linux) — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
  - UTF-8 — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
- **Slide**
  - PDF ★ · richiede pandoc, xelatex o tectonic — Slide Beamer: ogni titolo ## inizia una nuova slide
  - PPTX · richiede pandoc
  - Slide web · richiede pandoc — Presentazione reveal.js da aprire nel browser (serve internet per caricare reveal.js)
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- Statistiche

## Testo

Formati: TXT, LOG.

- **Converti**
  - PDF ★ · richiede weasyprint o soffice o python-pymupdf — PDF stampabile; il codice ha colori e numeri di riga
  - DOCX · richiede python-docx o soffice o pandoc
  - ODT · richiede soffice o pandoc
  - Markdown — Markdown con il codice in un blocco recintato
  - HTML — Pagina HTML con il codice evidenziato
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- UTF-8 — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
- **Fine riga**
  - LF (Linux) ★ — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
- Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
- Statistiche

### Con più file

- Combina in PDF · richiede python-pymupdf o python-pypdf — Converte ogni file in PDF e li unisce, con un segnalibro per file
- Unisci testo — Unisce i file in un unico testo, in ordine di nome
- Unisci in DOCX · richiede pandoc — Unisce documenti, Markdown e testo in un unico documento Word
- **Converti**
  - PDF ★ · richiede weasyprint o soffice o python-pymupdf — PDF stampabile; il codice ha colori e numeri di riga
  - DOCX · richiede python-docx o soffice o pandoc
  - ODT · richiede soffice o pandoc
  - Markdown — Markdown con il codice in un blocco recintato
  - HTML — Pagina HTML con il codice evidenziato
- **Codifica**
  - UTF-8 ★ — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
  - LF (Linux) — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
  - Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- Statistiche

## Codice

Formati: Sorgenti e script.

- **Esporta**
  - PDF ★ · richiede weasyprint o soffice o python-pymupdf — PDF stampabile; il codice ha colori e numeri di riga
  - HTML — Pagina HTML con il codice evidenziato
  - Markdown — Markdown con il codice in un blocco recintato
  - DOCX · richiede python-docx o soffice o pandoc
  - ODT · richiede soffice o pandoc
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- **Fine riga**
  - LF (Linux) ★ — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
- UTF-8 — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
- Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
- Statistiche

In più nella pagina File: Rendi eseguibile.

### Con più file

- Combina in PDF · richiede python-pymupdf o python-pypdf — Converte ogni file in PDF e li unisce, con un segnalibro per file
- Unisci testo — Unisce i file in un unico testo, in ordine di nome
- **Esporta**
  - PDF ★ · richiede weasyprint o soffice o python-pymupdf — PDF stampabile; il codice ha colori e numeri di riga
  - HTML — Pagina HTML con il codice evidenziato
  - Markdown — Markdown con il codice in un blocco recintato
  - DOCX · richiede python-docx o soffice o pandoc
  - ODT · richiede soffice o pandoc
- **Codifica**
  - UTF-8 ★ — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
  - LF (Linux) — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
  - Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
- Unisci in DOCX · richiede pandoc — Unisce documenti, Markdown e testo in un unico documento Word
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- Statistiche

## Dati strutturati

Formati: JSON, YAML, TOML, XML, INI.

- **Converti**
  - YAML ★ · richiede python-yaml
  - TOML
  - XML
  - CSV — Funziona con elenchi di oggetti: le chiavi annidate diventano colonne con il punto
  - XLSX · richiede python-openpyxl o soffice — Funziona con elenchi di oggetti: le chiavi annidate diventano colonne con il punto
- Formatta — Indenta il contenuto per renderlo leggibile
- Minimizza — Toglie spazi e a capo per ridurlo al minimo
- Valida — Controlla la sintassi e mostra dov'è l'errore
- Ordina chiavi — Ordina le chiavi alfabeticamente a ogni livello
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- Statistiche
- **Codifica**
  - UTF-8 ★ — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
  - LF (Linux) — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
  - Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga

### Con più file

- Unisci in JSON — Gli elenchi vengono concatenati, gli oggetti fusi (vincono i file successivi)
- **Converti**
  - YAML ★ · richiede python-yaml
  - TOML
  - XML
  - CSV — Funziona con elenchi di oggetti: le chiavi annidate diventano colonne con il punto
  - XLSX · richiede python-openpyxl o soffice — Funziona con elenchi di oggetti: le chiavi annidate diventano colonne con il punto
- Formatta — Indenta il contenuto per renderlo leggibile
- Minimizza — Toglie spazi e a capo per ridurlo al minimo
- Valida — Controlla la sintassi e mostra dov'è l'errore
- Ordina chiavi — Ordina le chiavi alfabeticamente a ogni livello
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- **Altro**
  - UTF-8 — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
  - **Fine riga**
    - LF (Linux) ★ — Usa i fine riga di Linux/macOS
    - CRLF (Windows) — Usa i fine riga di Windows
  - Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
  - Statistiche

## Ebook

Formati: EPUB, MOBI, AZW3, FB2, DjVu, CBZ.

- **Converti**
  - AZW3 ★ · richiede ebook-convert
  - PDF · richiede ebook-convert
  - DOCX · richiede ebook-convert
  - TXT · richiede ebook-convert
  - MOBI · richiede ebook-convert — Vecchio formato Kindle: meglio EPUB o AZW3
  - HTMLZ · richiede ebook-convert
- PDF · richiede ebook-convert
- Estrai copertina — Salva l'immagine di copertina
- Info libro — Titolo, autore, lingua, editore e serie

### Con più file

- **Converti**
  - AZW3 ★ · richiede ebook-convert
  - PDF · richiede ebook-convert
  - DOCX · richiede ebook-convert
  - TXT · richiede ebook-convert
  - MOBI · richiede ebook-convert — Vecchio formato Kindle: meglio EPUB o AZW3
  - HTMLZ · richiede ebook-convert
- Estrai copertina — Salva l'immagine di copertina
- Info libro — Titolo, autore, lingua, editore e serie

## Sottotitoli

Formati: SRT, VTT, ASS.

- **Converti**
  - VTT ★
  - ASS
  - Testo semplice
- **Tempi**
  - Sposta tempi… ★ · *finestra* — Anticipa o ritarda tutti i sottotitoli
  - Cambia fps… · *finestra* — Corregge sottotitoli fatti per un video con fps diversi
- Togli tag per non udenti — Rimuove [suoni], (rumori), righe di musica e nomi di chi parla
- **Codifica**
  - UTF-8 ★ — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
  - LF (Linux) — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
  - Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- Statistiche

### Con più file

- **Converti**
  - VTT ★
  - ASS
  - Testo semplice
- **Tempi**
  - Sposta tempi… ★ · *finestra* — Anticipa o ritarda tutti i sottotitoli
  - Cambia fps… · *finestra* — Corregge sottotitoli fatti per un video con fps diversi
- Togli tag per non udenti — Rimuove [suoni], (rumori), righe di musica e nomi di chi parla
- **Codifica**
  - UTF-8 ★ — Converte il testo in UTF-8 (la codifica originale è rilevata automaticamente)
  - LF (Linux) — Usa i fine riga di Linux/macOS
  - CRLF (Windows) — Usa i fine riga di Windows
  - Togli spazi finali — Rimuove spazi e tabulazioni alla fine di ogni riga
- Copia testo · richiede wl-copy — Copia tutto il contenuto negli appunti
- Statistiche

## Audio

Formati: MP3, M4A, FLAC, WAV, Opus, OGG.

- **Converti**
  - M4A ★ · richiede ffmpeg, ffprobe — M4A AAC, ideale per i dispositivi Apple
  - OPUS · richiede ffmpeg, ffprobe — Opus: la qualità migliore a bitrate bassi
  - OGG · richiede ffmpeg, ffprobe — OGG Vorbis
  - FLAC · richiede ffmpeg, ffprobe — FLAC senza perdita
  - WAV · richiede ffmpeg, ffprobe — WAV non compresso
  - AIFF · richiede ffmpeg, ffprobe — AIFF non compresso
  - M4R · richiede ffmpeg, ffprobe — Suoneria per iPhone (fino a 40 s)
- **Comprimi**
  - Bilanciata ★ · richiede ffmpeg, ffprobe — 128 kbit/s nello stesso formato
  - Alta qualità · richiede ffmpeg, ffprobe — 192 kbit/s; WAV/AIFF diventano FLAC senza perdita
  - Massima · richiede ffmpeg, ffprobe — 96 kbit/s
  - Voce · richiede ffmpeg, ffprobe — Opus 32 kbit/s mono, per il parlato
- Taglia… · *finestra* · richiede ffmpeg, ffprobe — Tieni o elimina un intervallo di tempo (taglio preciso)
- **Normalizza**
  - Streaming −14 LUFS ★ · richiede ffmpeg, ffprobe — Volume per Spotify/YouTube (EBU R128, due passaggi)
  - Podcast −16 LUFS · richiede ffmpeg, ffprobe — Volume per podcast (EBU R128, due passaggi)
  - Radio/TV −23 LUFS · richiede ffmpeg, ffprobe — Volume per radio e TV (EBU R128, due passaggi)
- Tag… · *finestra* · richiede python-mutagen o ffmpeg — Titolo, artista, album, anno, traccia, genere e copertina
- **Trascrivi**
  - TXT ★ · richiede whisper-cli, ffmpeg, ffprobe — Dal parlato al testo semplice con whisper.cpp
  - SRT · richiede whisper-cli, ffmpeg, ffprobe — Dal parlato ai sottotitoli SRT con whisper.cpp
  - VTT · richiede whisper-cli, ffmpeg, ffprobe — Dal parlato ai sottotitoli WebVTT con whisper.cpp
- **Modifica**
  - **Velocità**
    - 1,25× ★ · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - 1,5× · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - 2× · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - 0,75× · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - 0,5× · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - Su misura… · *finestra* · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
  - Dissolvenza · richiede ffmpeg, ffprobe — Volume in dissolvenza all'inizio e alla fine
  - Togli silenzi · richiede ffmpeg, ffprobe — Togli il silenzio all'inizio e alla fine
  - Riduci rumore · richiede ffmpeg, ffprobe — Riduci il rumore di fondo costante
  - **Volume**
    - +3 dB ★ · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
    - +6 dB · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
    - −3 dB · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
    - −6 dB · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
    - Personalizzato… · *finestra* · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
  - **Canali**
    - Mono ★ · richiede ffmpeg, ffprobe — Unisci in un solo canale
    - Stereo · richiede ffmpeg, ffprobe — Converti in due canali
  - Al contrario · richiede ffmpeg, ffprobe — Riproduci l'audio al contrario
- **Altro**
  - **Visualizza**
    - Quadrato ★ · richiede ffmpeg, ffprobe — Video con la forma d'onda, pronto per i social
    - Orizzontale · richiede ffmpeg, ffprobe — Video con la forma d'onda, pronto per i social
    - Verticale · richiede ffmpeg, ffprobe — Video con la forma d'onda, pronto per i social
  - Estrai copertina · richiede python-mutagen o ffmpeg — Salva come immagine la copertina incorporata

### Con più file

- Unisci · richiede ffmpeg, ffprobe — Un solo file audio da tutte le tracce, in ordine di nome
- **Converti**
  - M4A ★ · richiede ffmpeg, ffprobe — M4A AAC, ideale per i dispositivi Apple
  - OPUS · richiede ffmpeg, ffprobe — Opus: la qualità migliore a bitrate bassi
  - OGG · richiede ffmpeg, ffprobe — OGG Vorbis
  - FLAC · richiede ffmpeg, ffprobe — FLAC senza perdita
  - WAV · richiede ffmpeg, ffprobe — WAV non compresso
  - AIFF · richiede ffmpeg, ffprobe — AIFF non compresso
  - M4R · richiede ffmpeg, ffprobe — Suoneria per iPhone (fino a 40 s)
- **Normalizza**
  - Streaming −14 LUFS ★ · richiede ffmpeg, ffprobe — Volume per Spotify/YouTube (EBU R128, due passaggi)
  - Podcast −16 LUFS · richiede ffmpeg, ffprobe — Volume per podcast (EBU R128, due passaggi)
  - Radio/TV −23 LUFS · richiede ffmpeg, ffprobe — Volume per radio e TV (EBU R128, due passaggi)
- **Comprimi**
  - Bilanciata ★ · richiede ffmpeg, ffprobe — 128 kbit/s nello stesso formato
  - Alta qualità · richiede ffmpeg, ffprobe — 192 kbit/s; WAV/AIFF diventano FLAC senza perdita
  - Massima · richiede ffmpeg, ffprobe — 96 kbit/s
  - Voce · richiede ffmpeg, ffprobe — Opus 32 kbit/s mono, per il parlato
- Playlist — Playlist M3U con le tracce selezionate
- **Trascrivi**
  - TXT ★ · richiede whisper-cli, ffmpeg, ffprobe — Dal parlato al testo semplice con whisper.cpp
  - SRT · richiede whisper-cli, ffmpeg, ffprobe — Dal parlato ai sottotitoli SRT con whisper.cpp
  - VTT · richiede whisper-cli, ffmpeg, ffprobe — Dal parlato ai sottotitoli WebVTT con whisper.cpp
- **Modifica**
  - **Velocità**
    - 1,25× ★ · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - 1,5× · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - 2× · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - 0,75× · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - 0,5× · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
    - Su misura… · *finestra* · richiede ffmpeg, ffprobe — Cambia la velocità senza alterare il tono
  - Dissolvenza · richiede ffmpeg, ffprobe — Volume in dissolvenza all'inizio e alla fine
  - Togli silenzi · richiede ffmpeg, ffprobe — Togli il silenzio all'inizio e alla fine
  - Riduci rumore · richiede ffmpeg, ffprobe — Riduci il rumore di fondo costante
  - **Volume**
    - +3 dB ★ · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
    - +6 dB · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
    - −3 dB · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
    - −6 dB · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
    - Personalizzato… · *finestra* · richiede ffmpeg, ffprobe — Alza o abbassa il volume (un limitatore evita la distorsione)
  - **Canali**
    - Mono ★ · richiede ffmpeg, ffprobe — Unisci in un solo canale
    - Stereo · richiede ffmpeg, ffprobe — Converti in due canali
  - Al contrario · richiede ffmpeg, ffprobe — Riproduci l'audio al contrario
- **Altro**
  - **Visualizza**
    - Quadrato ★ · richiede ffmpeg, ffprobe — Video con la forma d'onda, pronto per i social
    - Orizzontale · richiede ffmpeg, ffprobe — Video con la forma d'onda, pronto per i social
    - Verticale · richiede ffmpeg, ffprobe — Video con la forma d'onda, pronto per i social
  - Estrai copertina · richiede python-mutagen o ffmpeg — Salva come immagine la copertina incorporata

## Video

Formati: MP4, MKV, WebM, MOV, AVI.

- **Converti**
  - WEBM ★ · richiede ffmpeg, ffprobe — WebM con video VP9 o AV1 e audio Opus
  - MKV · richiede ffmpeg, ffprobe — Matroska senza ricodifica: tutte le tracce vengono mantenute
  - MOV · richiede ffmpeg, ffprobe — QuickTime MOV (copia diretta quando possibile)
  - GIF · richiede ffmpeg, ffprobe — GIF animata con tavolozza ottimizzata
  - MP3 · richiede ffmpeg, ffprobe — Salva l'audio in MP3
  - M4A · richiede ffmpeg, ffprobe — Salva l'audio in M4A (AAC)
  - **Audio**
    - OPUS ★ · richiede ffmpeg, ffprobe — Salva l'audio in Opus
    - FLAC · richiede ffmpeg, ffprobe — Salva l'audio in FLAC senza perdita
    - WAV · richiede ffmpeg, ffprobe — Salva l'audio in WAV non compresso
- **Comprimi**
  - Bilanciata ★ · richiede ffmpeg, ffprobe — Buona qualità, file molto più piccolo (≤ 1080p)
  - Alta qualità · richiede ffmpeg, ffprobe — Perdita quasi invisibile, risoluzione originale
  - Massima · richiede ffmpeg, ffprobe — File più piccolo possibile (≤ 720p)
  - WhatsApp/Telegram · richiede ffmpeg, ffprobe — MP4 ≤ 720p sotto 16 MB, pronto da inviare in chat
  - Dimensione… · *finestra* · richiede ffmpeg, ffprobe — Codifica in due passaggi per stare in una dimensione
- Taglia… · *finestra* · richiede ffmpeg, ffprobe — Tieni o elimina un intervallo di tempo
- Ritaglia… · *finestra* · richiede ffmpeg, ffprobe — Tieni solo una parte dell'inquadratura
- **Ridimensiona**
  - 1080p ★ · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
  - 720p · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
  - 480p · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
  - 1440p · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
  - 2160p · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
- **Modifica**
  - Togli audio ★ · richiede ffmpeg, ffprobe — Togli le tracce audio senza ricodificare
  - **Ruota**
    - 90° orario ★ · richiede ffmpeg, ffprobe — Ruota l'immagine (ricodifica il video)
    - 90° antiorario · richiede ffmpeg, ffprobe — Ruota l'immagine (ricodifica il video)
    - 180° · richiede ffmpeg, ffprobe — Ruota l'immagine (ricodifica il video)
    - Correggi orientamento · richiede ffmpeg, ffprobe — Cambia solo l'indicazione di orientamento: istantaneo e senza perdita
  - **Rifletti**
    - Orizzontale ★ · richiede ffmpeg, ffprobe — Specchia l'immagine
    - Verticale · richiede ffmpeg, ffprobe — Specchia l'immagine
  - **Velocità**
    - 2× ★ · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
    - 1,5× · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
    - 4× · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
    - 0,5× · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
    - Su misura… · *finestra* · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
  - Al contrario · richiede ffmpeg, ffprobe — Riproduci il video al contrario
  - Ripeti · richiede ffmpeg, ffprobe — Ripeti la clip più volte
  - Stabilizza · richiede ffmpeg, ffprobe — Riduci il tremolio della ripresa (due passaggi)
  - Dissolvenza · richiede ffmpeg, ffprobe — Dissolvenza in entrata dal nero e in uscita al nero
- **Estrai**
  - Estrai audio ★ · richiede ffmpeg, ffprobe — Salva ogni traccia audio senza ricodificare
  - Istantanea · richiede ffmpeg, ffprobe — Fotogramma PNG a piena risoluzione
  - Fotogrammi… · *finestra* · richiede ffmpeg, ffprobe — Esporta un'immagine ogni pochi secondi in una cartella
  - Provini · richiede ffmpeg, ffprobe — Griglia di miniature con i tempi
  - Estrai sottotitoli · richiede ffmpeg, ffprobe — Salva i sottotitoli incorporati in SRT
- **Altro**
  - **Sottotitoli**
    - Imprimi file… ★ · *finestra* · richiede ffmpeg, ffprobe — Imprimi nell'immagine un file di sottotitoli a scelta
    - Aggiungi traccia · richiede ffmpeg, ffprobe — Aggiungi un file di sottotitoli come traccia selezionabile
    - Genera (Whisper) · richiede whisper-cli, ffmpeg, ffprobe — Crea i sottotitoli dal parlato con whisper.cpp
    - Estrai sottotitoli · richiede ffmpeg, ffprobe — Salva i sottotitoli incorporati in SRT
  - Dividi… · *finestra* · richiede ffmpeg, ffprobe — Dividi in clip di uguale durata, senza ricodificare
  - **Metadati**
    - Modifica… ★ · *finestra* · richiede ffmpeg, ffprobe — Mostra e modifica i metadati
    - Togli metadati · richiede ffmpeg, ffprobe — Rimuovi tag, capitoli, copertina e tracce GPS
    - Rimuovi GPS · richiede ffmpeg, ffprobe — Rimuovi solo i dati di posizione

### Con più file

- Unisci · richiede ffmpeg, ffprobe — Un solo video da tutte le clip, in ordine di nome
- **Converti**
  - WEBM ★ · richiede ffmpeg, ffprobe — WebM con video VP9 o AV1 e audio Opus
  - MKV · richiede ffmpeg, ffprobe — Matroska senza ricodifica: tutte le tracce vengono mantenute
  - MOV · richiede ffmpeg, ffprobe — QuickTime MOV (copia diretta quando possibile)
  - GIF · richiede ffmpeg, ffprobe — GIF animata con tavolozza ottimizzata
  - MP3 · richiede ffmpeg, ffprobe — Salva l'audio in MP3
  - M4A · richiede ffmpeg, ffprobe — Salva l'audio in M4A (AAC)
  - **Audio**
    - OPUS ★ · richiede ffmpeg, ffprobe — Salva l'audio in Opus
    - FLAC · richiede ffmpeg, ffprobe — Salva l'audio in FLAC senza perdita
    - WAV · richiede ffmpeg, ffprobe — Salva l'audio in WAV non compresso
- **Comprimi**
  - Bilanciata ★ · richiede ffmpeg, ffprobe — Buona qualità, file molto più piccolo (≤ 1080p)
  - Alta qualità · richiede ffmpeg, ffprobe — Perdita quasi invisibile, risoluzione originale
  - Massima · richiede ffmpeg, ffprobe — File più piccolo possibile (≤ 720p)
  - WhatsApp/Telegram · richiede ffmpeg, ffprobe — MP4 ≤ 720p sotto 16 MB, pronto da inviare in chat
  - Dimensione… · *finestra* · richiede ffmpeg, ffprobe — Codifica in due passaggi per stare in una dimensione
- Estrai audio · richiede ffmpeg, ffprobe — Salva ogni traccia audio senza ricodificare
- Togli audio · richiede ffmpeg, ffprobe — Togli le tracce audio senza ricodificare
- Togli metadati · richiede ffmpeg, ffprobe — Rimuovi tag, capitoli, copertina e tracce GPS
- **Ridimensiona**
  - 1080p ★ · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
  - 720p · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
  - 480p · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
  - 1440p · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
  - 2160p · richiede ffmpeg, ffprobe — Riduci la risoluzione (mai ingrandito)
- **Altro**
  - **Modifica**
    - Togli audio ★ · richiede ffmpeg, ffprobe — Togli le tracce audio senza ricodificare
    - **Ruota**
      - 90° orario ★ · richiede ffmpeg, ffprobe — Ruota l'immagine (ricodifica il video)
      - 90° antiorario · richiede ffmpeg, ffprobe — Ruota l'immagine (ricodifica il video)
      - 180° · richiede ffmpeg, ffprobe — Ruota l'immagine (ricodifica il video)
      - Correggi orientamento · richiede ffmpeg, ffprobe — Cambia solo l'indicazione di orientamento: istantaneo e senza perdita
    - **Rifletti**
      - Orizzontale ★ · richiede ffmpeg, ffprobe — Specchia l'immagine
      - Verticale · richiede ffmpeg, ffprobe — Specchia l'immagine
    - **Velocità**
      - 2× ★ · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
      - 1,5× · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
      - 4× · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
      - 0,5× · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
      - Su misura… · *finestra* · richiede ffmpeg, ffprobe — Cambia la velocità mantenendo il tono dell'audio
    - Al contrario · richiede ffmpeg, ffprobe — Riproduci il video al contrario
    - Ripeti · richiede ffmpeg, ffprobe — Ripeti la clip più volte
    - Stabilizza · richiede ffmpeg, ffprobe — Riduci il tremolio della ripresa (due passaggi)
    - Dissolvenza · richiede ffmpeg, ffprobe — Dissolvenza in entrata dal nero e in uscita al nero
  - **Estrai**
    - Estrai audio ★ · richiede ffmpeg, ffprobe — Salva ogni traccia audio senza ricodificare
    - Istantanea · richiede ffmpeg, ffprobe — Fotogramma PNG a piena risoluzione
    - Fotogrammi… · *finestra* · richiede ffmpeg, ffprobe — Esporta un'immagine ogni pochi secondi in una cartella
    - Provini · richiede ffmpeg, ffprobe — Griglia di miniature con i tempi
    - Estrai sottotitoli · richiede ffmpeg, ffprobe — Salva i sottotitoli incorporati in SRT
  - **Sottotitoli**
    - Aggiungi traccia ★ · richiede ffmpeg, ffprobe — Aggiungi un file di sottotitoli come traccia selezionabile
    - Genera (Whisper) · richiede whisper-cli, ffmpeg, ffprobe — Crea i sottotitoli dal parlato con whisper.cpp
    - Estrai sottotitoli · richiede ffmpeg, ffprobe — Salva i sottotitoli incorporati in SRT
  - **Metadati**
    - Togli metadati ★ · richiede ffmpeg, ffprobe — Rimuovi tag, capitoli, copertina e tracce GPS
    - Rimuovi GPS · richiede ffmpeg, ffprobe — Rimuovi solo i dati di posizione

## Archivi

Formati: ZIP, 7Z, RAR, TAR, GZ, XZ, ZST.

- Estrai qui — Estrai accanto all'archivio (crea una cartella solo se serve)
- Estrai in… · *finestra* — Estrai in una cartella a scelta
- Contenuto — Mostra i file contenuti, con le dimensioni
- **Converti**
  - 7Z · richiede 7z — Ricrea l'archivio in questo formato
  - TAR.ZST · richiede zstd o python-zstandard — Ricrea l'archivio in questo formato
  - TAR.GZ ★ — Ricrea l'archivio in questo formato
  - TAR.XZ — Ricrea l'archivio in questo formato
- Verifica — Controlla che l'archivio sia integro senza estrarlo
- **Proteggi**
  - 7z con password ★ · *finestra* · richiede 7z — Ricrea come 7z con AES-256: contenuto e nomi dei file cifrati
  - ZIP con password · *finestra* · richiede 7z — Ricrea come ZIP con AES-256 (i nomi dei file restano visibili)

### Con più file

- Estrai qui — Estrai accanto all'archivio (crea una cartella solo se serve)
- Estrai in… · *finestra* — Estrai in una cartella a scelta
- **Converti**
  - 7Z · richiede 7z — Ricrea l'archivio in questo formato
  - TAR.ZST · richiede zstd o python-zstandard — Ricrea l'archivio in questo formato
  - TAR.GZ ★ — Ricrea l'archivio in questo formato
  - TAR.XZ — Ricrea l'archivio in questo formato
- ZIP — Comprimi tutto in un unico archivio ZIP
- Verifica — Controlla che l'archivio sia integro senza estrarlo
- **Proteggi**
  - 7z con password ★ · *finestra* · richiede 7z — Ricrea come 7z con AES-256: contenuto e nomi dei file cifrati
  - ZIP con password · *finestra* · richiede 7z — Ricrea come ZIP con AES-256 (i nomi dei file restano visibili)

## Font

Formati: TTF, OTF, WOFF, WOFF2.

- Installa · richiede fc-cache — Installa per il tuo utente (~/.local/share/fonts) e aggiorna la cache dei font
- Anteprima — Foglio PNG con alfabeto, una frase di prova in più dimensioni e tutti i glifi
- **Converti**
  - WOFF2 ★ · richiede python-fonttools, python-brotli — Converti il font in questo formato
  - WOFF · richiede python-fonttools — Converti il font in questo formato
  - OTF · richiede python-fonttools — Converti il font in questo formato
- Kit web · richiede python-fonttools, python-brotli — WOFF2 + WOFF con la regola CSS @font-face pronta e una pagina di prova
- Riduci… · *finestra* · richiede python-fonttools — Tieni solo i caratteri che servono, per font web più leggeri
- Info · richiede python-fonttools o fc-query — Famiglia, stile, versione, glifi, assi variabili e licenza

### Con più file

- Installa · richiede fc-cache — Installa per il tuo utente (~/.local/share/fonts) e aggiorna la cache dei font
- **Converti**
  - WOFF2 ★ · richiede python-fonttools, python-brotli — Converti il font in questo formato
  - WOFF · richiede python-fonttools — Converti il font in questo formato
  - OTF · richiede python-fonttools — Converti il font in questo formato
- Kit web · richiede python-fonttools, python-brotli — WOFF2 + WOFF con la regola CSS @font-face pronta e una pagina di prova
- Anteprima — Foglio PNG con alfabeto, una frase di prova in più dimensioni e tutti i glifi
- ZIP — Comprimi tutto in un unico archivio ZIP
- Riduci… · *finestra* · richiede python-fonttools — Tieni solo i caratteri che servono, per font web più leggeri
- Info · richiede python-fonttools o fc-query — Famiglia, stile, versione, glifi, assi variabili e licenza

## Cartelle

Formati: Cartelle.

- **Comprimi**
  - ZIP ★ — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - 7Z · richiede 7z — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - TAR.ZST · richiede zstd o python-zstandard — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - TAR.GZ — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - TAR.XZ — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - ZIP con password · *finestra* · richiede 7z — Comprimi in un archivio protetto da password (AES-256)
  - 7z con password · *finestra* · richiede 7z — Comprimi in un archivio protetto da password (AES-256)
- Rinomina file… · *finestra* — Rinomina i file contenuti con un modello, trova/sostituisci e maiuscole
- Dimensioni — Dimensione totale, gli elementi e i file più grandi
- Duplicati — Trova i file con contenuto identico (non elimina niente)
- Crea SHA256SUMS — Salva lo SHA-256 di ogni file in “<cartella>.sha256” accanto alla cartella
- Contenuto → TXT — Salva l'albero della cartella, con le dimensioni, in un file di testo
- **Altro**
  - Appiattisci ★ — Copia tutti i file in una nuova cartella senza sottocartelle
  - Conta per tipo — Quante immagini, documenti, video… contiene la cartella
  - Terminale qui · richiede foot o kitty o alacritty o wezterm o ghostty o konsole o gnome-terminal o xfce4-terminal o xdg-terminal-exec o xterm — Apri un terminale in questa cartella

### Con più file

- **Comprimi**
  - ZIP ★ — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - 7Z · richiede 7z — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - TAR.ZST · richiede zstd o python-zstandard — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - TAR.GZ — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - TAR.XZ — Comprimi in un unico archivio (più cartelle finiscono insieme)
  - ZIP con password · *finestra* · richiede 7z — Comprimi in un archivio protetto da password (AES-256)
  - 7z con password · *finestra* · richiede 7z — Comprimi in un archivio protetto da password (AES-256)
- Duplicati — Trova i file con contenuto identico (non elimina niente)
- Dimensioni — Dimensione totale, gli elementi e i file più grandi
- Rinomina file… · *finestra* — Rinomina i file contenuti con un modello, trova/sostituisci e maiuscole
- Crea SHA256SUMS — Salva lo SHA-256 di ogni file in “<cartella>.sha256” accanto alla cartella
- Contenuto → TXT — Salva l'albero della cartella, con le dimensioni, in un file di testo
- **Altro**
  - Appiattisci ★ — Copia tutti i file in una nuova cartella senza sottocartelle
  - Conta per tipo — Quante immagini, documenti, video… contiene la cartella
  - Terminale qui · richiede foot o kitty o alacritty o wezterm o ghostty o konsole o gnome-terminal o xfce4-terminal o xdg-terminal-exec o xterm — Apri un terminale in questa cartella

## Link

Formati: Indirizzi http, https e ftp trascinati dal browser.

- Scarica — Scarica il file nella cartella Scaricati
- **Media**
  - Video MP4 ★ · richiede yt-dlp, ffmpeg — Scarica il video (fino a 1080p) in MP4 con yt-dlp
  - Audio MP3 · richiede yt-dlp, ffmpeg — Scarica solo l'audio, in MP3
  - Scegli qualità… · *finestra* · richiede yt-dlp, ffmpeg — Scegli qualità, sottotitoli e le altre opzioni di download
- **Codice QR**
  - Mostra QR ★ · richiede python-qrcode o qrencode — Mostra un codice QR per aprire il link dal telefono
  - Salva QR · richiede python-qrcode o qrencode — Salva il codice QR del link in PNG
- **Copia**
  - Link ★ — Copia il link
  - Link Markdown — Copia [titolo della pagina](link)
  - Link pulito — Copia il link senza parametri di tracciamento (utm, fbclid…)
- Apri nel browser · richiede xdg-open o gio — Apri il link nel browser predefinito
- Pagina → PDF · richiede chromium o google-chrome-stable o google-chrome o brave o brave-browser o microsoft-edge-stable o vivaldi-stable o thorium-browser — Salva la pagina web in PDF (serve Chromium)
- Crea link — Salva un file che apre questa pagina

## AppImage

Formati: AppImage.

- Aggiungi al menu · richiede unsquashfs o 7z — Aggiungi l'app al menu delle applicazioni, con la sua icona (l'AppImage non viene avviata)
- Avvia — Rendila eseguibile e avviala
- Rendi eseguibile — Permetti di avviare questo file come programma
- Estrai · richiede unsquashfs o 7z — Estrai il contenuto dell'AppImage in una cartella
- Info — Nome, versione, architettura e tipo di AppImage
- Verifica — Confronta con SHA256SUMS accanto al file, un hash incollato o gli appunti
- Esadecimale — Dump esadecimale dei primi 4 KB
- Stringhe — Salva il testo leggibile nascosto nel file

In più nella pagina File: Rendi eseguibile.

## Immagini disco

Formati: ISO, IMG, QCOW2, VHD.

- Monta · richiede udisksctl — Monta l'immagine in sola lettura
- Verifica — Confronta con SHA256SUMS accanto al file, un hash incollato o gli appunti
- Estrai · richiede 7z o bsdtar — Copia i file dell'immagine in una cartella
- Info — Formato, dimensione, etichetta e se è avviabile
- **Checksum**
  - SHA-256 ★ — Calcola il checksum e copialo
  - SHA-1 — Calcola il checksum e copialo
  - MD5 — Calcola il checksum e copialo
  - BLAKE2b — Calcola il checksum e copialo
  - XXH128 · richiede python-xxhash o xxh128sum — Calcola il checksum e copialo
- Esadecimale — Dump esadecimale dei primi 4 KB

## Pacchetti

Formati: pkg.tar.zst, DEB, RPM.

- Info — Nome, versione, descrizione e dipendenze del pacchetto
- File — I file che il pacchetto installa
- Estrai — Spacchetta in una cartella senza installare
- Verifica — Confronta con SHA256SUMS accanto al file, un hash incollato o gli appunti
- **Checksum**
  - SHA-256 ★ — Calcola il checksum e copialo
  - SHA-1 — Calcola il checksum e copialo
  - MD5 — Calcola il checksum e copialo
  - BLAKE2b — Calcola il checksum e copialo
  - XXH128 · richiede python-xxhash o xxh128sum — Calcola il checksum e copialo
- Identifica — Scopri che cosa è davvero questo file
- Stringhe — Salva il testo leggibile nascosto nel file
- Esadecimale — Dump esadecimale dei primi 4 KB

## File sconosciuti

Formati: Tutto ciò che non viene riconosciuto.

- Identifica — Scopri che cosa è davvero questo file
- **Checksum**
  - SHA-256 ★ — Calcola il checksum e copialo
  - SHA-1 — Calcola il checksum e copialo
  - MD5 — Calcola il checksum e copialo
  - BLAKE2b — Calcola il checksum e copialo
  - XXH128 · richiede python-xxhash o xxh128sum — Calcola il checksum e copialo
- ZIP — Comprimi tutto in un unico archivio ZIP
- Esadecimale — Dump esadecimale dei primi 4 KB
- Stringhe — Salva il testo leggibile nascosto nel file
- Apri con… · *finestra* — Scegli l'applicazione con cui aprire i file
- Cifra · *finestra* · richiede gpg — Cifra con una password (OpenPGP, AES-256)

In più nella pagina File: Rendi eseguibile.

## Selezioni miste

Quando i file sono di tipi diversi, la ruota propone solo le azioni che valgono per tutti.

### Immagini e PDF insieme

- ZIP — Comprimi tutto in un unico archivio ZIP
- SHA-256 — Calcola il checksum e copialo
- Combina in PDF · richiede python-pymupdf — PDF e immagini in un unico PDF, una pagina per immagine
- **Organizza**
  - Sposta in… ★ · *finestra* — Sposta gli originali in un'altra cartella
  - Rinomina… · *finestra* — Rinomina; con più file usa un modello come {name} {n:02}
- Al telefono — Condividi in rete locale con un codice QR per il telefono

### Immagine e audio insieme

- ZIP — Comprimi tutto in un unico archivio ZIP
- SHA-256 — Calcola il checksum e copialo
- **Organizza**
  - Sposta in… ★ · *finestra* — Sposta gli originali in un'altra cartella
  - Rinomina… · *finestra* — Rinomina; con più file usa un modello come {name} {n:02}
- Al telefono — Condividi in rete locale con un codice QR per il telefono
- Video con copertina · richiede ffmpeg, ffprobe — MP4 con l'immagine fissa sopra l'audio

### Documenti e PDF insieme

- ZIP — Comprimi tutto in un unico archivio ZIP
- Combina in PDF · richiede python-pymupdf o python-pypdf — Converte ogni file in PDF e li unisce, con un segnalibro per file
- SHA-256 — Calcola il checksum e copialo
- Un PDF per file — Crea un PDF per ogni file; i PDF restano come sono
- **Organizza**
  - Sposta in… ★ · *finestra* — Sposta gli originali in un'altra cartella
  - Rinomina… · *finestra* — Rinomina; con più file usa un modello come {name} {n:02}
- Al telefono — Condividi in rete locale con un codice QR per il telefono

### Tipi diversi senza relazione

- ZIP — Comprimi tutto in un unico archivio ZIP
- SHA-256 — Calcola il checksum e copialo
- **Organizza**
  - Sposta in… ★ · *finestra* — Sposta gli originali in un'altra cartella
  - Rinomina… · *finestra* — Rinomina; con più file usa un modello come {name} {n:02}
- Al telefono — Condividi in rete locale con un codice QR per il telefono

## Pagina File

Premendo di nuovo Shift, o con il pallino di pagina, la ruota passa alle azioni comuni a
tutti i tipi di file.

- Copia · richiede wl-copy — Copia i file negli appunti, pronti da incollare nel file manager
- Copia percorso — Copia il percorso completo come testo
- **Organizza**
  - Sposta in… ★ · *finestra* — Sposta gli originali in un'altra cartella
  - Copia in… · *finestra* — Copia i file in un'altra cartella
  - Rinomina… · *finestra* — Rinomina; con più file usa un modello come {name} {n:02}
  - Duplica — Crea una copia accanto all'originale
- **Condividi**
  - Al telefono ★ — Condividi in rete locale con un codice QR per il telefono
  - KDE Connect · richiede kdeconnect-cli — Invia a un dispositivo associato con KDE Connect
  - LocalSend · *finestra* · richiede localsend-cli — Invia con LocalSend a un dispositivo vicino
  - E-mail · richiede xdg-email — Allega a una nuova e-mail
- Apri con… · *finestra* — Scegli l'applicazione con cui aprire i file
- ZIP — Comprimi tutto in un unico archivio ZIP
- **Info**
  - Proprietà ★ — Dimensione, tipo, date, permessi e proprietario
  - **Checksum**
    - SHA-256 ★ — Calcola il checksum e copialo
    - SHA-1 — Calcola il checksum e copialo
    - MD5 — Calcola il checksum e copialo
    - BLAKE2b — Calcola il checksum e copialo
    - XXH128 · richiede python-xxhash o xxh128sum — Calcola il checksum e copialo
  - Verifica — Confronta con un checksum atteso (incollato, SHA256SUMS accanto al file o dagli appunti)
- **Altro**
  - Apri · richiede xdg-open o gio — Apri con l'applicazione predefinita
  - Mostra nella cartella ★ — Mostra i file selezionati nel file manager
  - Terminale qui · richiede foot o kitty o alacritty o wezterm o ghostty o konsole o gnome-terminal o xfce4-terminal o xdg-terminal-exec o xterm — Apri un terminale in questa cartella
  - Cifra · *finestra* · richiede gpg — Cifra con una password (OpenPGP, AES-256)
  - Data URI — Copia il file come data: URI in base64 (fino a 1 MB)
  - Cestina — Sposta gli originali nel cestino
