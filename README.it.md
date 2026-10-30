<p align="center">
  <img src="assets/dropwheel.svg" width="128" height="128" alt="Icona di Dropwheel">
</p>

<h1 align="center">Dropwheel</h1>

<p align="center">
  <a href="README.md">English</a> · <b>Italiano</b>
</p>

<p align="center">
  <b>La ruota delle azioni sui file per Hyprland e Caelestia.</b><br>
  Trascina dei file, tocca <kbd>Shift</kbd>, rilascia su un settore: convertiti, compressi, ritagliati, oscurati, condivisi.
</p>

---

Dropwheel è l'equivalente per Linux di **[Tangerine](https://tangerineformac.com)** per macOS.
Mentre trascini uno o più file da qualsiasi applicazione (Thunar, Nautilus, Dolphin, Firefox,
un client di posta…) basta toccare **Shift**: sotto il puntatore compare una ruota con le azioni
adatte a quei file. Rilasci i file sul settore che ti serve e il lavoro parte; una piccola scheda
nell'angolo dello schermo mostra l'avanzamento e, alla fine, il risultato, che puoi aprire,
mostrare nella cartella, copiare o trascinare direttamente in un'altra applicazione.

Tutto avviene **sul tuo computer**, con programmi liberi che probabilmente hai già
(ffmpeg, Ghostscript, LibreOffice, Tesseract…). L'aspetto segue i colori e la trasparenza di
**Caelestia**, il tema Material You per Hyprland.

```
                                ┌──────────┐
                                │ CONVERTI │
               ┌─────────┐      └──────────┘      ╔══════════╗
               │  ALTRO  │                        ║ COMPRIMI ║  ← settore sotto il puntatore
               └─────────┘     ╭────────────╮     ╚══════════╝
                               │     ▣      │
        ┌─────────┐            │  foto.jpg  │            ┌──────────────┐
        │ PRIVACY │            │   2,4 MB   │            │ RIDIMENSIONA │
        └─────────┘            ╰────────────╯            └──────────────┘
               ┌─────────┐                        ┌──────────┐
               │ SFONDO  │      ┌──────────┐      │ RITAGLIA │
               └─────────┘      │ MODIFICA │      └──────────┘
                                └──────────┘
                           ( Comprimi · foto.jpg 2,4 MB )
```

*La ruota di un'immagine: otto settori in senso orario a partire da mezzogiorno, l'anteprima al
centro. Passando sopra «Comprimi» durante il trascinamento si apre l'anello interno con
Bilanciata, Leggera, Forte, Dimensione file…*

---

## Indice

- [In breve](#in-breve)
- [Funzionalità](#funzionalità)
- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Configurare Hyprland e Caelestia](#configurare-hyprland-e-caelestia)
- [Come si usa](#come-si-usa)
- [Tastiera e mouse](#tastiera-e-mouse)
- [Le schede dei lavori (HUD)](#le-schede-dei-lavori-hud)
- [Dipendenze per funzionalità](#dipendenze-per-funzionalità)
- [Configurazione](#configurazione)
- [Integrazione con i file manager](#integrazione-con-i-file-manager)
- [Riga di comando](#riga-di-comando)
- [Risoluzione dei problemi](#risoluzione-dei-problemi)
- [Privacy e sicurezza](#privacy-e-sicurezza)
- [Disinstallazione](#disinstallazione)
- [Sviluppo](#sviluppo)
- [Crediti e licenza](#crediti-e-licenza)

---

## In breve

1. **Inizia a trascinare** uno o più file, da qualunque applicazione.
2. **Tocca Shift**: la ruota compare centrata sotto il puntatore.
3. **Scegli**: passando sopra un gruppo (Converti, Comprimi, …) per mezzo secondo si apre il
   suo anello interno; il centro diventa «Indietro».
4. **Rilascia** sul settore: l'azione parte subito, oppure si apre una finestra per regolarla
   (ritaglio, oscuramento, pagine di un PDF…). Rilasciare al centro o fuori annulla.

Altri modi per aprire la ruota: l'**angolo attivo** in alto a destra (trascinaci sopra i file),
la scorciatoia **SUPER+ALT+D** (con dei file copiati negli appunti), il menu del **file manager**
e la **riga di comando**.

---

## Funzionalità

Qui sotto c'è la sintesi per tipo di file. L'elenco completo di tutte le azioni, anello per anello
e con i programmi che richiedono, è in **[docs/FUNCTIONS.it.md](docs/FUNCTIONS.it.md)**.

La ruota mostra solo ciò che ha senso per i file trascinati: un PDF non propone «Converti in
PDF», una foto JPG non propone «JPG». Ogni anello ha al massimo otto settori; quando le azioni
sono di più, l'ultimo settore è **Altro**. Le azioni che richiedono un programma non installato
compaiono in grigio, con il comando per installarlo (vedi
[Dipendenze per funzionalità](#dipendenze-per-funzionalità)); con
`wheel.show_unavailable = false` vengono nascoste.

**I file originali non vengono mai modificati né cancellati.** I risultati vengono salvati
accanto all'originale con nomi chiari:

| Operazione | Esempio di risultato |
|---|---|
| Conversione | `foto.heic` → `foto.jpg` |
| Strumento sullo stesso formato | `foto.jpg` → `foto (compresso).jpg`, `contratto (oscurato).pdf` |
| Nome già esistente | `foto 2.jpg`, `foto 3.jpg`, … (mai sovrascritto) |
| Più file in uscita | cartella `documento (pagine)/` con `page-001.png`, `page-002.png`, … |

Se la cartella di origine non è scrivibile o è temporanea (download del browser, `/tmp`, file
condivisi da app Flatpak) il risultato va in **Scaricati/Dropwheel**. Con
`output.location = "folder"` va sempre lì. Ogni lavoro si può **annullare**: i file creati
finiscono nel cestino.

Oltre all'anello «Strumenti», ogni file ha una seconda pagina, **File** (si passa da una
all'altra con Shift): Apri, Apri con…, Mostra nella cartella, Copia il file, Copia il percorso,
Sposta in…, Copia in…, Rinomina…, Duplica, Comprimi in ZIP, Condividi (QR per il telefono,
KDE Connect, LocalSend, e-mail), Proprietà, Checksum, Cifra (GnuPG), Apri un terminale qui,
Rendi eseguibile (quando serve) e Sposta nel cestino.

### Immagini — JPG, PNG, WebP, AVIF, HEIC/HEIF, JPEG XL, TIFF, BMP, ICO, PSD

| Settore | Cosa fa |
|---|---|
| **Converti** | PNG, JPG, WebP, AVIF, PDF, JPEG XL, TIFF; in «Altro» HEIC, GIF, BMP, ICO, SVG (vettorializzata) |
| **Comprimi** | Bilanciata, Leggera, Forte, Per il web, Senza perdita, Dimensione file (da 100 KB a 5 MB o a scelta) |
| **Ridimensiona** | lato lungo 1920/2560/1280/3840 px, 50 %, 25 %, su misura, formati social (Instagram, storie, miniatura YouTube, X, LinkedIn, il tuo monitor) |
| **Ritaglia** | editor con proporzioni libere, originali, 1:1, 4:3, 3:2, 16:9, 9:16, 4:5, 2:3, 21:9, griglia dei terzi e ritaglio JPEG senza perdita |
| **Modifica** | ruota e rifletti (senza perdita per i JPEG), regolazioni (esposizione, contrasto, saturazione, temperatura, nitidezza, vignettatura) con anteprima, annota, filigrana (testo o logo), bianco e nero, inverti i colori, rifila i bordi |
| **Sfondo** | rimuovi lo sfondo (AI, in locale), aggiungi dietro un colore, una sfumatura, un'immagine o una copia sfocata (con margini, angoli arrotondati e ombra), bianco → trasparente; con le immagini trasparenti anche trasparente → colore e ritaglia sul soggetto |
| **Privacy** | togli la posizione GPS, togli tutti i metadati, oscura parti dell'immagine (riquadro pieno, pixel o sfocatura), mostra e modifica i metadati, rinomina con la data di scatto |
| **Altro** | OCR (copia il testo, TXT, PDF ricercabile), leggi QR e codici a barre, ingrandisci ×2/×4 con l'AI, vettorizza in SVG, crea icone (ICO, serie di PNG, favicon), copia l'immagine negli appunti, imposta come sfondo, stampa |

Le foto vengono raddrizzate secondo l'orientamento EXIF, i profili colore vengono conservati e la
trasparenza viene appiattita su bianco quando il formato di destinazione non la supporta.

**Immagini animate** (GIF, WebP animate, APNG): converti in MP4, WebM, WebP, GIF o primo
fotogramma; comprimi; ridimensiona; ritaglia; ruota e rifletti; velocità e riproduzione al
contrario; estrai i fotogrammi.

**SVG**: converti in PNG (dimensione originale, da 512 a 4096 px o a una misura precisa), PDF,
JPG, WebP, ICO; ottimizza (toglie i dati dell'editor e i commenti); copia come codice o immagine;
crea icone; OCR; leggi QR; stampa.

### PDF

| Settore | Cosa fa |
|---|---|
| **Converti** | Word (DOCX), JPG e PNG (una immagine per pagina, alla risoluzione che scegli), TXT, Markdown, EPUB, HTML; in «Altro» PDF/A e scala di grigi |
| **Comprimi** | Bilanciata (150 dpi), Leggera (senza ridurre la risoluzione), Forte (72 dpi), Su misura (risoluzione e qualità delle immagini) |
| **Pagine** | organizzatore con miniature (riordina trascinando, ruota, elimina, duplica); dividi (ogni pagina, ogni N pagine, intervalli come `1-3, 5, 7-`, segnalibri); estrai o elimina pagine; ruota; inverti l'ordine; inserisci pagine vuote |
| **Sicurezza** | proteggi con password (AES-256) o sblocca; **oscura** con riquadri neri disegnati o con la ricerca di testo («oscura tutte le occorrenze»): il testo sotto i riquadri viene **rimosso davvero** e il risultato viene verificato; togli o modifica i metadati |
| **OCR** | rendi ricercabile un PDF scansionato (italiano + inglese), rifai l'OCR, raddrizza le pagine, copia il testo |
| **Estrai** | immagini e allegati |
| **Annota** | apre il PDF in Xournal++ per evidenziare, scrivere note, disegnare e firmare |
| **Altro** | filigrana, numeri di pagina, appiattisci, ripara, stampa, informazioni |

Con più PDF: **unisci** (con un segnalibro per file), fascicola scansioni fronte/retro, comprimi,
converti o proteggi tutto insieme.

### Documenti, fogli di calcolo e presentazioni

- **Documenti** (DOCX, DOC, ODT, RTF): PDF con un tocco, conversione in DOCX/ODT, Markdown, TXT,
  HTML, EPUB, RTF, DOC, immagini delle pagine; compressione delle immagini incluse; rimozione dei
  dati personali; estrazione di immagini e testo; PDF protetto da password; statistiche (parole,
  caratteri, tempo di lettura). Con più documenti: un unico PDF o un unico DOCX.
- **Fogli di calcolo** (XLSX, ODS, XLS, CSV, TSV): converti in XLSX, ODS, CSV (un file per
  foglio), TSV, PDF, JSON, tabella Markdown, HTML; strumenti CSV (UTF-8, separatore `,` o `;`,
  formato «Per Excel» con `;` e BOM, fine riga); rimozione dei metadati; informazioni sul foglio.
  Con più file: unisci i CSV o combina in un unico XLSX.
- **Presentazioni** (PPTX, ODP, PPT): PDF, conversione in PPTX/ODP, immagini delle diapositive
  (PNG o JPG), compressione dei media, testo e note in Markdown, note del relatore, estrazione
  delle immagini, rimozione dei metadati, statistiche.

Le conversioni usano LibreOffice in modalità invisibile con un profilo privato, quindi
funzionano anche se LibreOffice è già aperto. Le macro non vengono mai eseguite.

### Markdown, testo e codice

- **Markdown**: PDF (con Typst, WeasyPrint, LaTeX o LibreOffice), DOCX, HTML, ODT, EPUB, TXT, RTF;
  copia come testo semplice o formattato; indice (mostralo o inseriscilo nel file); togli gli
  spazi finali, fine riga, UTF-8; diapositive (PDF, PPTX, pagina web); statistiche. Le immagini
  remote non vengono scaricate.
- **Testo**: converti in PDF/DOCX/ODT/MD/HTML, copia il contenuto, converti in UTF-8 (la codifica
  originale viene rilevata), fine riga LF/CRLF, togli gli spazi finali, statistiche. Con più file:
  unisci.
- **Codice sorgente**: esporta in PDF/HTML con evidenziazione e numeri di riga (anche in Markdown,
  DOCX e ODT), copia il contenuto, fine riga, UTF-8, togli gli spazi finali, statistiche, rendi
  eseguibile (solo se ha lo shebang). Dropwheel non esegue mai il codice trascinato.

### Dati strutturati — JSON, YAML, TOML, XML, NDJSON

Converti tra JSON, YAML, TOML, XML, CSV e XLSX; formatta, minimizza, verifica, ordina le chiavi;
con più file, unisci in JSON. Gli avvisi segnalano ciò che una conversione non può rappresentare
(commenti, `null` in TOML…). Il parsing è sicuro (niente XXE, YAML solo `safe_load`).

### Ebook — EPUB, MOBI, AZW3, FB2, CBZ/CBR

Converti (EPUB, AZW3, MOBI, PDF, DOCX, TXT, HTMLZ), estrai la copertina, informazioni (titolo,
autore, lingua, editore, serie); per i fumetti CBZ, estrai le pagine. I libri protetti da DRM
vengono segnalati e non toccati.

### Audio — MP3, M4A, FLAC, WAV, Opus, OGG

| Settore | Cosa fa |
|---|---|
| **Converti** | MP3, M4A (AAC), Opus, OGG, FLAC, WAV, AIFF, M4R (suoneria per iPhone) |
| **Comprimi** | Bilanciata (128 kbit/s), Alta qualità (192 kbit/s), Massima (96 kbit/s), Voce (Opus mono) |
| **Taglia** | editor con forma d'onda: tieni o elimina un intervallo |
| **Normalizza** | streaming −14 LUFS, podcast −16, radio/TV −23 (EBU R128) |
| **Tag** | titolo, artista, album, anno, traccia, genere e copertina |
| **Trascrivi** | testo, sottotitoli SRT/VTT con whisper.cpp, in locale (lingua automatica, italiano o inglese) |
| **Modifica** | velocità senza cambiare l'intonazione, dissolvenza, togli i silenzi all'inizio e alla fine, riduzione del rumore, volume, mono o stereo, al contrario |
| **Altro** | video con la forma d'onda (quadrato, orizzontale, verticale), estrai la copertina |

Con più file audio: unisci, crea una playlist M3U.

### Video — MP4, MKV, WebM, MOV, AVI

| Settore | Cosa fa |
|---|---|
| **Converti** | MP4 (H.264 o HEVC), WebM, MKV (senza ricodifica), MOV, GIF, MP3, M4A; in «Audio» Opus, FLAC, WAV |
| **Comprimi** | Bilanciata (≤1080p), Alta qualità, Massima (≤720p), WhatsApp/Telegram (≤720p, sotto 16 MB), Dimensione a scelta |
| **Taglia** | editor con miniature dei punti di taglio: tieni o elimina un intervallo; veloce (senza ricodifica) o preciso |
| **Ritaglia** | tieni solo una parte dell'inquadratura, anche con proporzioni fisse |
| **Ridimensiona** | 1080p, 720p, 480p, 1440p, 2160p |
| **Modifica** | togli l'audio, ruota e rifletti (anche solo l'orientamento, senza ricodifica), velocità, al contrario, ripeti, stabilizza, dissolvenza |
| **Estrai** | audio (senza ricodifica), istantanea, fotogrammi, foglio provini (da 3×3 a 6×6), sottotitoli |
| **Altro** | sottotitoli (genera con whisper.cpp, aggiungi come traccia, imprimi un file nell'immagine, estrai), dividi in clip uguali, metadati (modifica, togli, togli il GPS) |

Con più video: unisci (senza ricodifica quando i formati coincidono), estrai l'audio, comprimi
tutto. Le risoluzioni non vengono mai aumentate; l'accelerazione hardware (VA-API, NVENC) si
sceglie in `[video] hwaccel`.

**Sottotitoli** (SRT, VTT, ASS): converti, sposta i tempi, cambia gli fps, UTF-8, togli i tag per
non udenti, copia il testo, statistiche. Per imprimerli in un video, usa **Altro → Sottotitoli**
sul video.

### Archivi — ZIP, 7Z, RAR, TAR.GZ/XZ/ZST, GZ, XZ, ZST

Estrai qui (crea una cartella solo se l'archivio contiene più elementi), estrai in…, mostra il
contenuto, converti in ZIP/7Z/TAR.ZST/TAR.GZ/TAR.XZ, verifica l'integrità, proteggi con password
(7z AES-256 con nomi cifrati, ZIP AES-256) o togli la password, dividi in parti (100 MB, 1 GB,
4 GB o a scelta) e riunisci. Le voci pericolose (percorsi assoluti, `..`, link fuori
dall'archivio) vengono rifiutate; prima di estrarre archivi enormi o sospetti viene chiesta
conferma.

### Font — TTF, OTF, WOFF, WOFF2

Installa (o disinstalla) per l'utente, anteprima (un foglio PNG con alfabeto, frase di prova e
glifi), converti in WOFF2/WOFF/TTF/OTF, kit per il web con CSS `@font-face`, sottoinsieme (latino,
latino esteso con le lettere accentate, testo personalizzato, intervalli Unicode), informazioni
(con avviso sulle licenze che vietano la modifica).

### Cartelle

Comprimi (ZIP, 7Z, TAR.ZST, TAR.GZ, TAR.XZ, anche con password o in più parti), immagini → un PDF,
rinomina i file in blocco (modello con numerazione e data, trova e sostituisci, maiuscole),
dimensioni, duplicati, conta per tipo, checksum (`SHA256SUMS`: crea e verifica), elenco del
contenuto in TXT, appiattisci le sottocartelle, apri un terminale qui.

### Link trascinati dal browser

Scarica il file (nella cartella Scaricati), scarica video o audio con yt-dlp (MP4 fino a 1080p,
MP3, oppure scegli qualità, sottotitoli e miniatura), QR del link, salva la pagina in PDF (serve
Chromium), copia come link semplice o Markdown, apri nel browser, pulisci il link dai parametri
di tracciamento (`utm_*`, `fbclid`, …), crea un collegamento. Con più link: salva l'elenco.

### AppImage, immagini disco, pacchetti, file sconosciuti

- **AppImage**: integra nel menu delle applicazioni (e togli), rendi eseguibile, avvia, estrai
  (senza eseguirla), informazioni, verifica del checksum.
- **ISO/IMG**: monta in sola lettura, verifica il checksum, estrai, smonta.
- **Pacchetti** (`.pkg.tar.zst`, `.deb`, `.rpm`): informazioni, elenco dei file, estrai, checksum;
  «Installa» apre un terminale con il comando già scritto (Dropwheel non chiede mai la password).
- **File sconosciuti**: identifica il tipo reale, checksum (SHA-256, SHA-1, MD5, BLAKE2b, XXH128;
  nella pagina File anche il confronto con un valore incollato o negli appunti), comprimi, vista
  esadecimale, stringhe, apri con…, cifra.

### Più file insieme

Se i file sono tutti dello stesso tipo, la ruota propone le azioni di gruppo: immagini → **PDF**,
GIF/WebP animata, presentazione MP4, collage; PDF → **unisci**; video e audio → **unisci**;
documenti → **un unico PDF**. Con file di tipi diversi: ZIP, checksum, rinomina, sposta in…, invia
al telefono e, quando ha senso, un unico PDF (immagini, PDF, documenti e testi convertiti e uniti),
un PDF per file, tutto in MP3 o un video con copertina (un'immagine e un audio). Un file che non
riesce non ferma gli altri: alla fine la scheda riporta, per esempio, «18 riusciti, 2 non riusciti,
3 saltati».

---

## Requisiti

| | |
|---|---|
| **Sistema** | Arch Linux (o derivate) con Wayland |
| **Hyprland** | 0.57 o successivo, **configurazione Lua** (dalla 0.57 il vecchio `hyprland.conf` non è più letto) |
| **Quickshell** | 0.3.2 o successivo: il pacchetto `quickshell` oppure `quickshell-git` (quello usato da Caelestia) |
| **Python** | 3.11 o successivo (Arch: 3.14) con Pillow |
| **Altri pacchetti** | `wl-clipboard`, `libnotify`, `ttf-material-symbols-variable`, `glib2`, `xdg-utils` |
| **Caelestia** | consigliato, non obbligatorio: senza Caelestia Dropwheel usa una tavolozza scura Material 3 |

Tutto il resto è facoltativo e abilita le singole funzioni: vedi
[Dipendenze per funzionalità](#dipendenze-per-funzionalità).

---

## Installazione

Scarica il codice (con `git clone` o come archivio) ed entra nella cartella del progetto. Poi
scegli **una** delle due strade.

### A. Pacchetto Arch (consigliata)

```sh
makepkg -si
```

Il `PKGBUILD` costruisce il pacchetto **dalla cartella in cui ti trovi** (nessun download) e
pacman tiene traccia di ogni file: `/usr/bin/dropwheel`, `/usr/share/dropwheel/`, la voce del
menu, l'icona e le azioni per Dolphin e Nemo. Le dipendenze facoltative, ognuna con la funzione
che abilita, compaiono al termine dell'installazione e con `pacman -Qi dropwheel`.

Poi, **con il tuo utente** (non root):

```sh
dropwheel setup hyprland   # Shift, SUPER+ALT+D e regole dei layer (vedi sotto)
dropwheel start            # avvia subito; dal prossimo login parte da sola
dropwheel doctor           # cosa è installato e cosa manca
```

> **Nota su `makepkg -c` / `-C`.** In questo progetto `src/` contiene il codice Python, mentre
> makepkg usa normalmente `src/` come cartella di lavoro. Il `PKGBUILD` sposta quindi la
> cartella di lavoro in `.makepkg/` (a meno che tu non abbia impostato `BUILDDIR` in
> `makepkg.conf`), così anche le opzioni di pulizia sono sicure.
>
> Se i test falliscono per un motivo che non ti interessa (per esempio uno strumento opzionale
> mancante) puoi saltarli con `makepkg -si --nocheck`.

### B. Installazione nella home, senza root

```sh
./install.sh          # oppure: bash install.sh
```

Lo script copia Dropwheel in `~/.local` (nessun file fuori dalla tua home, nessun `sudo`):

| Cosa | Dove |
|---|---|
| pacchetto Python | `~/.local/share/dropwheel/python/dropwheel` |
| shell Quickshell | `~/.local/share/dropwheel/shell/` |
| integrazioni | `~/.local/share/dropwheel/integrations/` |
| comando | `~/.local/bin/dropwheel` (avvia `python3 -P -m dropwheel` con il `PYTHONPATH` giusto) |
| voce del menu e icona | `~/.local/share/applications/`, `~/.local/share/icons/hicolor/scalable/apps/` |
| azioni dei file manager | Nautilus, Dolphin e Nemo, se installati |

Durante l'installazione lo script:

- controlla le dipendenze con `pacman -Qq` e **stampa** il comando per installare quelle
  mancanti e una selezione di quelle facoltative (non esegue mai `sudo`);
- protegge le applicazioni predefinite: se la nuova voce del menu diventasse l'applicazione
  predefinita per un tipo di file che ne aveva già una, la precedente viene rimessa in
  `~/.config/mimeapps.list` (Dropwheel resta nel menu «Apri con»);
- propone `dropwheel setup hyprland` e infine esegue `dropwheel start`.

Opzioni utili: `--yes` (nessuna domanda), `--no-hyprland`, `--no-start`, `--no-file-managers`,
`--no-mime-guard`, `--prefix DIR`, `--python PERCORSO`, `--uninstall`. Elenco completo con
`./install.sh --help`.

Se il terminale non trova `dropwheel`, aggiungi `~/.local/bin` al `PATH`
(con fish, la shell predefinita di Caelestia: `fish_add_path ~/.local/bin`).

### Aggiornare

```sh
git pull
makepkg -sif         # oppure: ./install.sh
dropwheel stop && dropwheel start
```

La `f` di `makepkg -sif` è necessaria: la versione del pacchetto non cambia a ogni modifica e,
senza `f`, makepkg trova il pacchetto costruito la volta precedente («A package has already been
built») e reinstalla quello, senza gli aggiornamenti.

---

## Configurare Hyprland e Caelestia

Dropwheel ha bisogno di tre cose da Hyprland: inoltrare **Shift** (e la scorciatoia di apertura)
alla shell di Dropwheel, applicare la sfocatura alle sue superfici e avviarla al login. Tutto è
nel modulo Lua [`integrations/hyprland/dropwheel.lua`](integrations/hyprland/dropwheel.lua).

### In automatico

```sh
dropwheel setup hyprland
```

Con Caelestia il comando:

1. copia `dropwheel.lua` in `~/.config/caelestia/dropwheel.lua` (la tua copia, che puoi
   modificare);
2. aggiunge in fondo a `~/.config/caelestia/hypr-user.lua`, dopo averne fatto un backup, la riga

   ```lua
   require("dropwheel")
   ```

   Caelestia aggiunge già `~/.config/caelestia/?.lua` al percorso dei moduli Lua e carica
   `hypr-user.lua` per ultimo; né `caelestia install` né `caelestia update` toccano quel file.

Il modulo registra:

| Cosa | Dettagli |
|---|---|
| `Shift_L`, `Shift_R` | `hl.dsp.global("dropwheel:shift")` con `non_consuming` (le applicazioni ricevono comunque Shift) e `ignore_mods` (funziona anche tenendo Ctrl o Alt) |
| `SUPER + ALT + D` | `hl.dsp.global("dropwheel:open")`: apre la ruota con i file negli appunti |
| regole dei layer | `dropwheel-wheel`, `dropwheel-hud` con sfocatura (`blur`, `ignore_alpha = 0.3`) e senza animazioni di Hyprland; `dropwheel-dialog` e `dropwheel-hotzone` solo senza animazioni (il dialogo ha un suo velo: sfocarlo sfocherebbe tutto lo schermo) |
| avvio automatico | `hl.on("hyprland.start", …)` esegue `dropwheel start` |

Le scorciatoie hanno una descrizione, quindi compaiono in `hyprctl binds`.

> L'evento `hyprland.start` scatta **solo al login**, non quando la configurazione viene
> ricaricata: la prima volta avvia Dropwheel a mano con `dropwheel start`. Se le scorciatoie non
> compaiono subito, ricarica Hyprland con `hyprctl reload`.

### A mano

Con Caelestia (pacchetto di sistema; con `install.sh` il file si trova in
`~/.local/share/dropwheel/integrations/hyprland/`):

```sh
cp /usr/share/dropwheel/integrations/hyprland/dropwheel.lua ~/.config/caelestia/dropwheel.lua
echo 'require("dropwheel")' >> ~/.config/caelestia/hypr-user.lua
hyprctl reload
dropwheel start
```

Senza Caelestia, copia il file in `~/.config/hypr/` e aggiungi a `~/.config/hypr/hyprland.lua`:

```lua
package.path = package.path .. ";" .. os.getenv("HOME") .. "/.config/hypr/?.lua"
require("dropwheel")
```

Se preferisci non usare il modulo, queste righe fanno lo stesso:

```lua
for _, key in ipairs({ "Shift_L", "Shift_R" }) do
    hl.bind(key, hl.dsp.global("dropwheel:shift"),
        { non_consuming = true, ignore_mods = true, description = "Dropwheel: Shift durante il trascinamento" })
end
hl.bind("SUPER + ALT + D", hl.dsp.global("dropwheel:open"), { description = "Dropwheel: apri la ruota" })
hl.layer_rule({ match = { namespace = "dropwheel-(wheel|hud)" }, blur = true, ignore_alpha = 0.3, no_anim = true })
hl.layer_rule({ match = { namespace = "dropwheel-(dialog|hotzone)" }, no_anim = true })
hl.on("hyprland.start", function() hl.exec_cmd("dropwheel start") end)
```

### Cambiare la scorciatoia e le altre opzioni del modulo

In cima a `~/.config/caelestia/dropwheel.lua`:

```lua
local OPEN_KEY = "SUPER + ALT + D"          -- per esempio "SUPER + D"; nil = nessuna scorciatoia
local SHIFT_KEYS = { "Shift_L", "Shift_R" } -- {} = non usare Shift
local AUTOSTART = true                      -- avvio al login
local BLUR = true                           -- sfocatura dietro la ruota e le schede
local DROPWHEEL_CMD = nil                   -- nil = ~/.local/bin/dropwheel se esiste, altrimenti "dropwheel"
```

Il modulo restituisce una tabella: `require("dropwheel").set_enabled(false)` disattiva
scorciatoie e regole. Un errore nel modulo (per esempio una scorciatoia scritta male) non blocca
il resto della configurazione: viene mostrato con una notifica.

---

## Come si usa

### 1. Shift durante il trascinamento

È il modo principale. Inizia a trascinare dei file e **tocca Shift** (premi e rilascia): la
ruota compare centrata sotto il puntatore. Spostati su un settore e rilascia.

- I gruppi si aprono da soli se ci resti sopra mezzo secondo (`wheel.spring_ms`); il centro
  diventa **Indietro** (restaci sopra o cliccalo per tornare indietro).
- Rilasciare su un **gruppo** lascia la ruota aperta, in modalità clic, sull'anello di quel gruppo.
- Rilasciare **al centro o fuori** dalla ruota annulla. Anche **Esc** annulla: Hyprland
  interrompe il trascinamento e la ruota si chiude.
- Toccare di nuovo Shift con la ruota aperta passa dalla pagina **Strumenti** alla pagina
  **File** e viceversa.

Dropwheel controlla che un trascinamento sia davvero in corso (`hyprctl -j monitors`), quindi
Shift continua a funzionare normalmente quando scrivi.

### 2. L'angolo attivo

Un'area di 6×6 pixel in alto a destra dello schermo (configurabile: angolo o bordo, dimensione,
disattivabile). Trascina i file **sopra l'angolo**: la ruota si apre lì vicino e ti basta
spostarti sopra. Se invece **rilasci i file direttamente sull'angolo**, la ruota si apre in
modalità clic con quei file.

### 3. SUPER+ALT+D, senza trascinare

Copia dei file (<kbd>Ctrl</kbd>+<kbd>C</kbd> nel file manager) e premi
<kbd>SUPER</kbd>+<kbd>ALT</kbd>+<kbd>D</kbd>: la ruota si apre sotto il puntatore con quei file,
in modalità clic. Se negli appunti non ci sono file, compare un'area «Trascina qui dei file».

### 4. Dal file manager

Tasto destro sui file → **Apri in Dropwheel** (Dolphin, Nemo, Thunar) oppure
**Script → Dropwheel** (Nautilus). Vedi [Integrazione con i file manager](#integrazione-con-i-file-manager).

### 5. Dal terminale

```sh
dropwheel open ~/Immagini/foto.heic        # ruota in modalità clic su quel file
dropwheel menu --json documento.pdf        # le azioni disponibili, in JSON
dropwheel run image.convert.png foto.heic  # esegue un'azione senza interfaccia
```

Vedi [Riga di comando](#riga-di-comando).

---

## Tastiera e mouse

### Durante il trascinamento

| Gesto | Effetto |
|---|---|
| tocca **Shift** | apre la ruota sotto il puntatore; con la ruota aperta cambia pagina (Strumenti ⇄ File) |
| resta su un gruppo per 450 ms | apre l'anello del gruppo |
| resta al centro («Indietro») per 450 ms | torna all'anello precedente |
| rilascia su un'azione | la esegue (o apre la sua finestra di regolazione) |
| rilascia su un gruppo | la ruota resta aperta in modalità clic sull'anello del gruppo |
| rilascia al centro o fuori | annulla |
| **Esc** | annulla il trascinamento e chiude la ruota |

### In modalità clic (dopo SUPER+ALT+D, l'angolo attivo, un gruppo o il file manager)

| Tasto o gesto | Effetto |
|---|---|
| frecce, **Tab**, rotellina | spostano l'evidenziazione tra i settori |
| **1** … **8** | esegue il settore N (in senso orario da mezzogiorno) |
| **Invio** o clic | esegue l'azione o apre il gruppo |
| **Alt+Invio** o clic destro | apre i parametri di un'azione rapida prima di eseguirla |
| **Backspace** o clic al centro | torna indietro |
| **Esc** | torna indietro; all'anello principale chiude la ruota |
| **Shift** o **Ctrl+Tab** | cambia pagina (Strumenti ⇄ File) |

Le azioni **rapide** partono subito con i valori predefiniti (quelli di `config.toml`); con
Alt+Invio o il clic destro puoi cambiarli per una volta. Le azioni con una **finestra**
(Ritaglia, Oscura, Taglia, Pagine, Metadati, Regolazioni, Sfondo) aprono il loro editor: Invio
applica, Esc annulla.

---

## Le schede dei lavori (HUD)

Dopo il rilascio la ruota si rimpicciolisce in una scheda nell'angolo in basso a destra
(`hud.position`). Le schede si impilano (al massimo cinque visibili):

- **in corso**: nome dell'azione, file, barra di avanzamento e ✕ per interrompere;
- **completata**: anteprima, nome e dimensione del risultato (con il risparmio, per esempio
  «−78 %»). **Trascina il risultato** direttamente in un'altra applicazione (browser, chat,
  e-mail) oppure usa **Apri**, **Mostra nella cartella**, **Copia**, **Annulla** (sposta i file
  creati nel cestino). Si chiude da sola dopo 7 secondi (`hud.timeout_ms`), ma non mentre ci passi
  sopra;
- **testo, QR, informazioni**: anteprima compatta con **Copia** ed **Espandi**;
- **non riuscita**: messaggio chiaro, dettagli espandibili e, se manca un programma, **Copia il
  comando di installazione**. Resta finché non la chiudi.

I lavori lunghi (più di 10 secondi) o terminati con la scheda non visibile mandano anche una
notifica (`notifications.mode`).

---

## Dipendenze per funzionalità

Le dipendenze obbligatorie sono poche; il resto si installa solo quando serve. Una funzione
senza il suo programma appare in grigio e mostra il comando giusto; `dropwheel doctor` elenca
tutto. Nessun comando viene mai eseguito con `sudo` al posto tuo.

### Pacchetti ufficiali (`sudo pacman -S --needed …`)

| Funzionalità | Pacchetti |
|---|---|
| **Base (obbligatori)** | `python python-pillow quickshell hyprland wl-clipboard libnotify ttf-material-symbols-variable glib2 xdg-utils` |
| Foto HEIC/HEIF e JPEG XL | `python-pillow-heif python-pillow-jpegxl-plugin` |
| Compressione e ottimizzazione immagini | `oxipng pngquant jpegoptim libjpeg-turbo` |
| GIF di qualità, GIF animate | `gifski gifsicle libwebp-utils` |
| Formati insoliti, immagini enormi | `imagemagick libheif libvips` |
| SVG | `librsvg resvg potrace inkscape scour` (o `svgo`) |
| Metadati e privacy | `perl-image-exiftool mat2` |
| OCR (italiano e inglese) | `tesseract tesseract-data-ita tesseract-data-eng` |
| QR e codici a barre | `zbar python-qrcode qrencode` |
| Annotazioni | `satty` (o `swappy`), `xournalpp` per i PDF |
| Ingrandimento con l'AI | `waifu2x-ncnn-vulkan` |
| PDF | `python-pymupdf python-pypdf python-pikepdf python-cryptography qpdf ghostscript poppler mupdf-tools img2pdf` |
| Documenti Office | `libreoffice-fresh` (o `libreoffice-still`) `python-openpyxl python-docx` |
| Markdown, testo, codice | `pandoc-cli typst python-weasyprint python-pygments python-charset-normalizer` |
| Dati strutturati | `python-yaml python-tomli-w python-defusedxml` |
| Ebook | `calibre` |
| Audio e video | `ffmpeg python-mutagen` |
| Trascrizione e sottotitoli | `whisper-cpp` |
| Download da link | `yt-dlp deno` (deno serve a yt-dlp per YouTube) |
| Archivi | `7zip unrar unarchiver` |
| Font | `python-fonttools python-brotli` |
| AppImage | `squashfs-tools` |
| Invio al telefono | `kdeconnect` (KDE Connect); il QR in rete locale non richiede altro oltre a `python-qrcode` |
| Sistema | `gnupg udisks2 trash-cli zenity gtk3 xdg-terminal-exec` |
| Contagocce nei dialoghi (selettore colore) | `hyprpicker` |
| Stampa | `cups` |
| Checksum XXH128 | `xxhash` |
| Pagina web → PDF | `chromium` (o un altro browser basato su Chromium) |

Una buona base per quasi tutto:

```sh
sudo pacman -S --needed python-pillow-heif python-pillow-jpegxl-plugin oxipng pngquant jpegoptim \
    gifski perl-image-exiftool tesseract tesseract-data-ita tesseract-data-eng zbar python-qrcode \
    python-pymupdf python-pypdf qpdf ghostscript poppler libreoffice-fresh pandoc-cli typst \
    ffmpeg python-mutagen 7zip python-fonttools python-brotli
```

### Dall'AUR (`paru -S …` o `yay -S …`)

| Pacchetto | Per cosa |
|---|---|
| `ocrmypdf` | rendere ricercabili i PDF scansionati (non è più nei repository ufficiali) |
| `python-pdf2docx` | conversione PDF → Word più fedele (altrimenti si usa LibreOffice) |
| `vtracer` | vettorializzazione a colori di un'immagine in SVG |
| `realesrgan-ncnn-vulkan-bin` | ingrandimento ×4 con Real-ESRGAN |
| `localsend-cli-bin` | invio con LocalSend |

### Con pipx

| Programma | Comando | Per cosa |
|---|---|---|
| rembg | `pipx install "rembg[cpu,cli]"` | rimozione dello sfondo; al primo uso scarica un modello AI |

Se pipx non riesce a installare rembg con Python 3.14, usa un Python precedente, per esempio
con [uv](https://docs.astral.sh/uv/): `uv tool install --python 3.13 "rembg[cpu,cli]"`.
Per l'accelerazione con la CPU basta `python-onnxruntime-cpu` dei repository ufficiali.

---

## Configurazione

Il file è `~/.config/dropwheel/config.toml` (formato TOML). Non serve crearlo: senza file valgono
i valori predefiniti. Per partire da un file commentato:

```sh
dropwheel config --init
```

Le modifiche valgono alla successiva apertura della ruota. Un valore non valido viene sostituito
da quello predefinito e segnalato con un avviso, senza bloccare nulla.

Un esempio:

```toml
language = "it"                     # auto | it | en
hidden_actions = ["image.background.*"]

[output]
location = "source"                 # accanto all'originale; "folder" = sempre nella cartella sotto
folder = "~/Scaricati/Dropwheel"

[hotzone]
corner = "bottom-left"

[image]
jpeg_quality = 82

[[custom]]
id = "ottimizza-per-blog"
label = "Per il blog"
icon = "web"
categories = ["image"]
command = "magick {file} -resize '1600x1600>' -quality 80 {dir}/{stem}-blog.jpg"
output = "files"
```

Tutte le chiavi, con valori ammessi ed esempi di azioni personalizzate, sono in
**[docs/CONFIG.it.md](docs/CONFIG.it.md)**.

---

## Integrazione con i file manager

| File manager | Dove | Come si usa |
|---|---|---|
| **Thunar** (predefinito in Caelestia) | `~/.config/Thunar/uca.xml` | tasto destro → **Apri in Dropwheel** |
| **Nautilus** | `~/.local/share/nautilus/scripts/Dropwheel` | tasto destro → **Script → Dropwheel** |
| **Dolphin** | `/usr/share/kio/servicemenus/` o `~/.local/share/kio/servicemenus/` | tasto destro → **Apri in Dropwheel** |
| **Nemo** | `/usr/share/nemo/actions/` o `~/.local/share/nemo/actions/` | tasto destro → **Apri in Dropwheel** |

Tutte eseguono `dropwheel open %F` sui file selezionati (su una cartella vuota, la cartella stessa).

- **Dolphin e Nemo**: il pacchetto installa le azioni per tutti gli utenti; `install.sh` le
  installa nella tua home se il file manager è presente.
- **Nautilus**: gli script sono solo per utente; `install.sh` lo installa. Con il pacchetto:
  `install -Dm755 /usr/share/dropwheel/integrations/nautilus/Dropwheel ~/.local/share/nautilus/scripts/Dropwheel`.
- **Thunar** legge le azioni personalizzate da un unico file, `~/.config/Thunar/uca.xml`, che è
  gestito da Caelestia: per questo Dropwheel non lo modifica da solo. Per aggiungere l'azione:

  ```sh
  dropwheel setup thunar --apply   # aggiunge l'azione a uca.xml, con backup
  ```

  oppure segui le istruzioni manuali in
  [`integrations/thunar/README.md`](integrations/thunar/README.md). Dopo un `caelestia update`
  potresti trovare `uca.xml.new`: il README spiega come unire le modifiche.

La voce **Dropwheel** compare anche in «Apri con» per i tipi di file più comuni, e il menu delle
applicazioni ha le azioni **Avvia Dropwheel** e **Arresta Dropwheel**.

---

## Riga di comando

| Comando | Cosa fa |
|---|---|
| `dropwheel start [--foreground\|--no-wait]` | avvia la shell (Quickshell) e il servizio in background; non fa nulla se sono già attivi |
| `dropwheel stop` | li arresta (esce con 0 anche se non erano attivi) |
| `dropwheel restart` | arresta e riavvia la shell |
| `dropwheel status [--json]` | mostra se Dropwheel è in esecuzione (codice di uscita 1 se non lo è) |
| `dropwheel open [FILE…] [--at X,Y]` | apre la ruota in modalità clic sui file indicati (percorsi o URL); senza file usa quelli copiati negli appunti |
| `dropwheel menu FILE… [--json]` | mostra le azioni disponibili per quei file (`--json` per i programmi) |
| `dropwheel actions [-c CATEGORIA] [--json]` | elenca tutte le azioni e se sono disponibili |
| `dropwheel run AZIONE FILE… [-p CHIAVE=VALORE]… [--json] [-q]` | esegue un'azione senza interfaccia, per esempio `image.compress.balanced`; l'id di un gruppo (`image.convert`) esegue l'azione predefinita ★; Ctrl+C interrompe (codice 130) |
| `dropwheel undo [LAVORO]` | annulla un lavoro (di norma l'ultimo): i file prodotti vanno nel cestino, gli originali sostituiti tornano al loro posto |
| `dropwheel history [-n N]` | mostra gli ultimi lavori |
| `dropwheel doctor [--json] [--full]` | stato di Hyprland, Quickshell, dipendenze e strumenti opzionali, con i comandi per installarli |
| `dropwheel setup hyprland` | collega Dropwheel alla configurazione di Hyprland/Caelestia |
| `dropwheel setup nautilus\|dolphin\|nemo\|desktop\|all [--dry-run]` | installa l'azione per un file manager, la voce del menu applicazioni o tutto quanto |
| `dropwheel setup thunar [--apply]` | mostra (o aggiunge) l'azione per Thunar |
| `dropwheel shares [--stop ID\|--stop-all]` | elenca o interrompe le condivisioni in rete locale attive |
| `dropwheel config [--path\|--init\|--edit] [--force] [--json]` | mostra la configurazione in uso, ne scrive una commentata (`--init`) o la apre nell'editor |
| `dropwheel daemon` | il servizio che parla con la shell (lo avvia la shell stessa, non serve usarlo a mano) |

`dropwheel --help` e `dropwheel COMANDO --help` mostrano tutte le opzioni. I codici di uscita sono
`0` (riuscito), `1` (errore), `2` (uso sbagliato) e `130` (interrotto).

`dropwheel setup` non sovrascrive mai un file senza averne fatto una copia
`<file>.dropwheel-bak-AAAAMMGG-HHMMSS` (lo stesso schema di `uninstall.sh`).

Variabili d'ambiente:

| Variabile | Effetto |
|---|---|
| `DROPWHEEL_CONFIG` | percorso alternativo di `config.toml` |
| `DROPWHEEL_DATA` | cartella dei dati (shell e integrazioni); normalmente `~/.local/share/dropwheel` o `/usr/share/dropwheel` |
| `DROPWHEEL_BACKEND` | comando del servizio usato dalla shell (lo imposta `dropwheel start`) |

---

## Risoluzione dei problemi

Il primo passo è sempre:

```sh
dropwheel doctor
```

### La ruota non compare quando premo Shift

1. **Dropwheel è in esecuzione?** `hyprctl globalshortcuts` deve elencare `dropwheel:shift` e
   `dropwheel:open`. Se mancano, la shell non è attiva: `dropwheel start` (gli eventuali errori
   compaiono nel terminale).
2. **Le scorciatoie sono caricate?** `hyprctl binds | grep -i -B2 -A8 dropwheel` deve mostrare
   `Shift_L`, `Shift_R` e `SUPER + ALT + D` con la descrizione di Dropwheel. Se mancano:
   `dropwheel setup hyprland`, poi `hyprctl reload`, e controlla che Hyprland non segnali errori
   nella configurazione.
3. **La ruota compare solo quando rilasci Shift?** È normale se nella tua configurazione ci sono
   scorciatoie che iniziano con Shift: il risolutore degli accordi di Hyprland aspetta il rilascio
   per capire se stai componendo una combinazione più lunga. Dropwheel gestisce entrambi i casi:
   tocca Shift (premi e rilascia) invece di tenerlo premuto.
4. **Trascini da un'applicazione XWayland?** In Hyprland 0.57 il trascinamento da applicazioni
   X11 (XWayland) verso finestre Wayland non funziona: la ruota non riceve i file. Lo vedi con
   `hyprctl clients` (`xwayland: 1`). Avvia l'applicazione in modalità Wayland (per le app
   Electron: `--ozone-platform=wayland` oppure `ELECTRON_OZONE_PLATFORM_HINT=auto`), oppure copia
   i file e usa SUPER+ALT+D.
5. **La ruota compare e sparisce subito, oppure mai durante il trascinamento?** Il controllo del
   trascinamento usa `hyprctl -j monitors`. Come prova imposta `dnd_check = false` nella sezione
   `[trigger]` di `config.toml`: la ruota si apre a ogni Shift e si richiude da sola dopo poco se
   non arriva nessun trascinamento.

### Dove trovo i log?

```sh
qs log -p ~/.local/share/dropwheel/shell -f    # installazione con install.sh
qs log -p /usr/share/dropwheel/shell -f        # pacchetto di sistema
qs list                                        # istanze di Quickshell attive
```

Il registro della shell contiene anche i messaggi d'errore del servizio Python. L'output dei
comandi avviati da Hyprland (`hl.exec_cmd`) finisce in `/dev/null`: per vedere gli errori
dell'avvio esegui `dropwheel start` da un terminale.

### Ho un file `~/.config/quickshell/shell.qml`

Con un `shell.qml` direttamente in `~/.config/quickshell/`, Quickshell non trova più le
configurazioni nelle sottocartelle (`qs -c nome`). Dropwheel **non ne è toccato** perché viene
sempre avviato indicando il percorso (`qs -p …/dropwheel/shell`); non spostare la sua shell in
`~/.config/quickshell/`.

### Le icone della ruota sono quadratini o lettere

Manca il font delle icone: `sudo pacman -S ttf-material-symbols-variable`, poi `fc-cache -f` e
`dropwheel stop && dropwheel start`.

### Il comando `dropwheel` non esiste

Con `install.sh` il comando è in `~/.local/bin`, che deve essere nel `PATH`
(fish: `fish_add_path ~/.local/bin`; bash/zsh: `export PATH="$HOME/.local/bin:$PATH"` nel file di
avvio della shell). Hyprland e i file manager usano comunque il percorso completo.

### Il risultato è finito in Scaricati/Dropwheel

Succede quando l'originale si trova in una cartella non scrivibile o temporanea: download in corso
del browser, `/tmp`, archivi aperti da un altro programma, **file condivisi da app Flatpak**
(arrivano dal portale dei documenti come `/run/user/1000/doc/…`, una copia temporanea). Dropwheel
copia subito questi file in un'area sicura e salva il risultato in Scaricati/Dropwheel; la scheda
lo segnala. Se vuoi sempre una cartella fissa usa `output.location = "folder"`.

### Le conversioni di documenti non producono nulla

LibreOffice in modalità invisibile non fa nulla (senza errori) se il suo profilo è già in uso,
per esempio quando LibreOffice è aperto. Dropwheel usa un **profilo privato** per le conversioni,
quindi non ne è toccato; la prima conversione è più lenta perché il profilo viene creato. Se non
funziona ancora: `soffice --version` deve rispondere, e `dropwheel doctor` deve vedere `soffice`.

### Alcuni file si aprono con Dropwheel

La voce di Dropwheel dichiara i tipi di file più comuni per comparire in «Apri con» (non le cartelle
e non i file di tipo sconosciuto): se uno di questi tipi non ha un'applicazione predefinita, un
programma potrebbe scegliere Dropwheel. Imposta quella giusta, per esempio:

```sh
xdg-mime default org.gnome.Loupe.desktop image/png
```

`install.sh` lo fa da solo per i tipi che avevano già un'applicazione predefinita.

### Le azioni dal file manager non fanno nulla

Prova `dropwheel open ~/un/file.jpg` da un terminale. La comunicazione con la shell di Quickshell
avviene solo all'interno dello stesso display Wayland: il file manager deve avere
`WAYLAND_DISPLAY` nel suo ambiente, cosa normale se è stato avviato dalla sessione Hyprland.

### Premendo Shift l'applicazione di origine «sposta» i file?

Mentre trascini, Hyprland inoltra Shift anche all'applicazione di origine, e in GTK e Qt
Shift significa di solito «sposta». Dropwheel **legge sempre i file come copia** e non sposta
né cancella mai gli originali; Hyprland, inoltre, non comunica all'origine l'esito del
rilascio. Se invece, dopo aver aperto la ruota, rilasci i file in un'altra applicazione, lascia
prima Shift.

### Una funzione è grigia

Manca il programma che la esegue: passa sopra il settore per vedere quale e il comando per
installarlo (la scheda di errore ha **Copia il comando di installazione**). Dopo l'installazione
non serve riavviare: Dropwheel ricontrolla gli strumenti ogni dieci minuti, oppure subito con
`dropwheel stop && dropwheel start`.

### Dropwheel si chiude da solo all'avvio

Il protocollo delle scorciatoie globali non ammette due client con lo stesso nome: se parte una
seconda copia di Dropwheel, una delle due viene chiusa. Controlla con `qs list`, poi
`dropwheel stop && dropwheel start`.

### Un documento viene rifiutato perché «troppo grande»

Un documento Office, un EPUB o un CBZ può dichiarare parti enormi in un file minuscolo. Per
proteggere la memoria del sistema Dropwheel non apre parti compresse in modo insolito (bzip2, LZMA)
né legge parti di testo oltre i 64 MiB (256 MiB per il contenuto di un foglio ODS). **Comprimi** e
**Rimuovi metadati** copiano comunque i video incorporati, fino a 8 GiB in tutto; **Estrai
immagini** salta le parti oltre i 64 MiB e dice quali.

---

## Privacy e sicurezza

- **Tutto avviene in locale.** Nessuna telemetria, nessun servizio cloud, nessun account.
- La rete viene usata **solo** quando lo chiedi tu:
  - **Condividi → QR per il telefono**: un piccolo server HTTP temporaneo, visibile solo nella
    rete locale, su una porta e un indirizzo casuali, che offre solo i file scelti e si spegne da
    solo dopo 15 minuti (`share.minutes`) o quando chiudi la scheda;
  - **KDE Connect / LocalSend**: invio ai tuoi dispositivi già associati;
  - **link trascinati dal browser**: il download avviene solo dal sito di quel link (yt-dlp o
    download diretto);
  - **modelli AI** (rimozione dello sfondo, trascrizione): vengono scaricati solo quando installi
    o usi quella funzione per la prima volta.
- Le immagini remote nei file Markdown e i riferimenti esterni negli SVG non vengono mai
  scaricati; macro dei documenti, JavaScript dei PDF e contenuti trascinati non vengono mai
  eseguiti (un'AppImage viene ispezionata senza avviarla).
- **Oscurare un PDF** rimuove davvero il testo e le immagini sotto i riquadri, poi Dropwheel
  rilegge il risultato per verificarlo; vengono tolti anche metadati, allegati e la cronologia
  delle modifiche. Per le immagini, sfocatura e pixel su un testo possono essere reversibili:
  per dati sensibili usa il riquadro pieno.
- La cronologia dei lavori (per **Annulla**) è in `~/.local/state/dropwheel/`, i file temporanei
  e le anteprime in `~/.cache/dropwheel/`; le password (PDF, archivi) restano in memoria solo per
  il lavoro che le usa.

---

## Disinstallazione

**Pacchetto di sistema**

```sh
sudo pacman -Rns dropwheel
```

Poi togli la riga `require("dropwheel")` da `~/.config/caelestia/hypr-user.lua`, cancella
`~/.config/caelestia/dropwheel.lua` ed esegui `hyprctl reload`. pacman non tocca la tua home:
impostazioni, cronologia, cache, modelli Whisper e sfondi salvati (le cartelle sono elencate qui
sotto) restano dove sono finché non li cancelli tu.

**Installazione con install.sh**

```sh
~/.local/share/dropwheel/uninstall.sh            # oppure ./uninstall.sh dalla cartella del progetto
~/.local/share/dropwheel/uninstall.sh --purge    # cancella anche i tuoi dati, dopo una conferma
```

Lo script arresta Dropwheel, toglie la riga `require("dropwheel")` da `hypr-user.lua` (dopo
averne fatto un backup `hypr-user.lua.dropwheel-bak-…`), rimuove la copia di `dropwheel.lua`
(se l'avevi modificata la conserva come backup) e tutti i file installati. Restano, a meno di
`--purge`: le impostazioni in `~/.config/dropwheel`, la cronologia in `~/.local/state/dropwheel`,
la cache in `~/.cache/dropwheel` e, in `~/.local/share/dropwheel`, i modelli Whisper che hai
scaricato (`whisper/`) e le copie degli sfondi impostati con Dropwheel (`wallpapers/`: se ne stai
usando uno, sceglierne un altro prima di `--purge`). **I file creati con Dropwheel non vengono mai
cancellati.**

Con un'installazione in un'altra cartella (`--prefix`), `uninstall.sh` copiato al suo interno
rimuove quella; lo script nella cartella del progetto vuole lo stesso `--prefix`.

---

## Sviluppo

Dal codice sorgente, senza installare nulla:

```sh
PYTHONPATH=src python -m dropwheel doctor
PYTHONPATH=src python -m dropwheel start      # usa shell/ e integrations/ di questa cartella
make test                                      # pytest
make lint                                      # Python, script, Lua, XML, voci .desktop, QML
```

L'architettura (shell Quickshell, servizio Python, protocollo JSON, come aggiungere un'azione o
una traduzione) è descritta in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**; il contratto
completo è in [docs/SPEC.md](docs/SPEC.md).

---

## Crediti e licenza

- Idea e flusso d'uso: **[Tangerine](https://tangerineformac.com)** per macOS; spunti anche da
  OpenShelf e DragShift. Dropwheel non è affiliato a questi progetti.
- Linguaggio visivo: **[Caelestia](https://github.com/caelestia-dots)** (Material You per
  Hyprland). Costruito con **[Hyprland](https://hypr.land)** e
  **[Quickshell](https://quickshell.org)**; icone Material Symbols di Google.
- Il lavoro vero lo fanno programmi liberi straordinari: FFmpeg, Pillow, ImageMagick, libvips,
  PyMuPDF, pypdf, qpdf, Ghostscript, Poppler, LibreOffice, pandoc, Typst, Tesseract, OCRmyPDF,
  whisper.cpp, ExifTool, 7-Zip, calibre, fontTools, yt-dlp e molti altri, ciascuno con la propria
  licenza.

Dropwheel è distribuito con licenza **MIT** — © 2026 Haxurus. Vedi [LICENSE](LICENSE).
