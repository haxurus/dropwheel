# Registro delle modifiche

[English](CHANGELOG.md)

Le modifiche rilevanti di Dropwheel. Il formato segue [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/)
e il progetto usa il [Versionamento Semantico](https://semver.org/lang/it/).

## [Non rilasciato]

### Aggiunto

- **Anteprima per Windows del motore e della riga di comando**, non ancora provata su un vero Windows. Vedi
  [Dropwheel su Windows](docs/WINDOWS.it.md). Ogni parte per Windows è separata da quella per Linux, che non
  cambia.
  - Configurazione in `%APPDATA%\dropwheel`, cronologia e copie per annullare in `%LOCALAPPDATA%\dropwheel`,
    cache in `%LOCALAPPDATA%\Temp\dropwheel`.
  - Apertura dei file con l'applicazione predefinita, e visualizzazione selezionata in Esplora file.
  - Appunti per testo, file e immagini tramite Windows PowerShell 5.1, con i percorsi passati sullo standard
    input invece che sulla riga di comando.
  - Il Cestino tramite `send2trash`, ora una dipendenza su Windows.
  - Programmi esterni avviati senza finestra di console e fermati insieme ai loro figli.
  - Ricerca dei programmi nel `PATH`, in `Program Files` e nella cartella dei collegamenti di winget;
    Ghostscript trovato come `gswin64c`.
  - Indicazioni di installazione con `winget install` e `python -m pip install`.
  - *Apri un terminale qui* apre una console `cmd.exe`.
- `tests/test_windows.py`: 16 test che esercitano le parti per Windows su Linux.
- Documentazione divisa in guide nella cartella [docs](docs/README.it.md): installazione, uso, funzioni, riga di
  comando, risoluzione dei problemi, privacy, sviluppo e Windows, in inglese e in italiano.
- Questo registro delle modifiche.

### Modificato

- Il README principale è più corto. Tiene la panoramica, la tabella delle piattaforme e un avvio rapido, e
  rimanda alle guide.

### Sicurezza

- Su Windows i programmi vengono cercati solo come `.exe` o `.com` in cartelle assolute, mai nella cartella
  corrente, così un programma che si trova accanto ai file da elaborare non viene mai eseguito. Gli script
  `.cmd` e `.bat` vengono ignorati, perché `cmd.exe` leggerebbe `&` o `%` in un nome di file come un comando. I
  programmi di sistema (`powershell`, `cmd`, `taskkill`, `explorer`) vengono presi solo da `%SystemRoot%`.
- Su Windows il cestino rifiuta le condivisioni di rete e le unità rimovibili, che non hanno Cestino e dove la
  shell cancellerebbe i file per sempre. Senza `send2trash` rifiuta invece di ripiegare su una cartella nascosta.
- Su Windows una condivisione in rete locale viene fermata tramite il suo file di stato, mai con `os.kill`, che lì
  termina qualunque processo abbia ora quell'identificativo.
- Su Windows il terminale non esegue mai una riga di comando, e `System32\convert.exe` (uno strumento di
  conversione dei dischi) non viene mai scambiato per ImageMagick.

### Rimosso

- Su Windows le azioni per AppImage, immagini disco e pacchetti Linux non vengono caricate, e le azioni che
  dipendono da programmi del desktop Linux (sfondo, formato per lo schermo, annotazione con satty e swappy,
  stampa, installazione dei font, KDE Connect) non vengono né mostrate né eseguite.
- Import inutilizzati e una variabile inutilizzata in un test.

### Corretto

- `actions/pdf.py` usava una barra rovesciata dentro un'espressione di una f-string, che Python 3.11 rifiuta
  anche se il progetto lo supporta.

## [1.0.0] - 2026-10-30

### Aggiunto

- Prima versione: una ruota radiale di azioni sui file *trascina + Shift* per Arch Linux con Hyprland 0.57+ e
  Quickshell, con 448 azioni per immagini, PDF, documenti, dati, ebook, sottotitoli, audio, video, archivi,
  font, cartelle, link, AppImage e immagini disco.
- Editor per ritaglio, oscuramento, taglio, pagine PDF, metadati, regolazione dei colori e sfondi.
- Riga di comando con output JSON, integrazioni con i file manager, interfaccia in inglese e italiano.

[Non rilasciato]: https://github.com/haxurus/dropwheel/compare/1bfaa50...HEAD
[1.0.0]: https://github.com/haxurus/dropwheel/tree/1bfaa50
