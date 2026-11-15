[Documentazione](README.it.md) · [English](FEATURES.md)

# Cosa può fare

L'anello si adatta al file, quindi le voci esatte possono cambiare. Gli anelli hanno al massimo otto
settori, e le voci in più passano in **Altro**.

| Tipo di file | Azioni |
|---|---|
| **Immagini** (JPG, PNG, WebP, AVIF, HEIC, JPEG XL, TIFF, BMP, ICO, PSD) | Converti, comprimi (anche a una dimensione precisa), ridimensiona (con formati per i social), ritaglia, ruota, rifletti, regola i colori, annota, filigrana, rimuovi o aggiungi uno sfondo, togli la posizione o tutti i metadati, oscura, OCR, leggi i codici QR, ingrandisci con l'AI, vettorizza, crea icone, imposta come sfondo, stampa |
| **Immagini animate** (GIF, WebP, APNG) | Converti in MP4 o WebM, comprimi, ridimensiona, ritaglia, ruota, estrai i fotogrammi, al contrario, cambia la velocità |
| **SVG** | Converti in PNG, PDF, JPG, WebP o ICO, ottimizza, copia come testo o immagine, crea icone |
| **PDF** | Converti in DOCX, JPG, PNG, TXT, Markdown, EPUB, HTML o PDF/A; comprimi; organizza, dividi, estrai, elimina e ruota le pagine; proteggi con AES-256 o sblocca; oscuramento vero; modifica i metadati; OCR; estrai immagini e allegati; annota; filigrana; numeri di pagina; appiattisci; ripara |
| **Documenti** (DOCX, DOC, ODT, RTF) | Converti in PDF, ODT, Markdown, TXT, HTML, EPUB o RTF, comprimi le immagini incluse, rimuovi i dati personali, estrai immagini e testo, PDF protetto da password, statistiche |
| **Fogli di calcolo** (XLSX, ODS, XLS, CSV, TSV) | Converti in ODS, CSV, PDF, JSON, tabella Markdown, HTML o TSV, rimuovi i metadati, informazioni sul foglio |
| **Presentazioni** (PPTX, ODP, PPT) | Converti in PDF, ODP o immagini, comprimi i media, estrai testo, note del relatore e immagini, rimuovi i metadati |
| **Markdown** | Converti in PDF, HTML, DOCX, ODT, EPUB, TXT o RTF, diapositive, indice, formattazione, statistiche |
| **Testo e codice** | Esporta in PDF, HTML, DOCX o Markdown con evidenziazione della sintassi, converti in UTF-8, correggi i fine riga, togli gli spazi finali, statistiche |
| **Dati** (JSON, YAML, TOML, XML) | Converti tra i formati e in CSV o XLSX, formatta, minimizza, verifica, ordina le chiavi |
| **Ebook** (EPUB, MOBI, AZW3, FB2, CBZ) | Converti, estrai la copertina, informazioni sul libro |
| **Sottotitoli** (SRT, VTT, ASS) | Converti, sposta i tempi, cambia gli fps, togli i tag per non udenti, testo semplice |
| **Audio** (MP3, M4A, FLAC, WAV, Opus, OGG) | Converti, comprimi, taglia, normalizza secondo EBU R128, modifica tag e copertina, trascrivi in locale con whisper.cpp, cambia la velocità, dissolvenza, togli i silenzi, riduci il rumore, video con la forma d'onda |
| **Video** (MP4, MKV, WebM, MOV, AVI) | Converti, comprimi (anche per WhatsApp/Telegram e a una dimensione precisa), taglia, ritaglia, ridimensiona, togli l'audio, ruota, cambia la velocità, al contrario, ripeti, stabilizza, estrai audio, fotogrammi e sottotitoli, foglio provini, genera o imprimi i sottotitoli, dividi, modifica i metadati |
| **Archivi** (ZIP, 7Z, RAR, TAR, GZ, XZ, ZST) | Estrazione intelligente, estrai in una cartella, elenca, verifica, converti, aggiungi una password |
| **Font** (TTF, OTF, WOFF, WOFF2) | Installa, foglio di anteprima, converti, kit per il web, sottoinsieme, informazioni |
| **Cartelle** | Comprimi, rinomina in blocco, dimensioni, trova i duplicati, crea SHA256SUMS, elenca il contenuto, appiattisci |
| **Link** | Scarica, salva video o audio con yt-dlp, codice QR, copia il link pulito, salva la pagina in PDF, crea un collegamento |
| **AppImage, immagini disco, pacchetti** | Aggiungi al menu, avvia, monta in sola lettura, verifica, ispeziona, estrai |
| **File sconosciuti** | Identifica il tipo reale, checksum, vista esadecimale, stringhe, cifra |

Ogni file ha anche una pagina **File**: copia, copia il percorso, sposta, rinomina, duplica, ZIP, apri con,
condividi (codice QR per il telefono, KDE Connect, LocalSend, e-mail), proprietà, checksum, cifra con
GnuPG, apri un terminale qui e cestino.

**Più file insieme.** I file dello stesso tipo hanno azioni di gruppo, per esempio immagini in PDF, GIF,
presentazione o collage; unione di PDF, video o audio; documenti in un unico PDF. Le selezioni miste
ricevono ciò che vale per tutti.

**Dove finiscono i risultati**

| Operazione | Esempio |
|---|---|
| Conversione | `foto.heic` produce `foto.jpg` |
| Strumento sullo stesso formato | `foto.jpg` produce `foto (compresso).jpg` |
| Nome già esistente | `foto 2.jpg`, `foto 3.jpg` e così via, mai una sovrascrittura |
| Più file in uscita | Una cartella come `documento (pagine)/` |
| Origine in sola lettura o temporanea | `~/Scaricati/Dropwheel`, e la scheda te lo dice |

L'elenco completo delle azioni, anello per anello e con i programmi che richiedono, è in
[docs/FUNCTIONS.it.md](FUNCTIONS.it.md).

---

← [Precedente](USAGE.it.md) · [Successivo](CLI.it.md) →
