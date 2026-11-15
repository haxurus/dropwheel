# Registro delle modifiche

[English](CHANGELOG.md)

Le modifiche rilevanti di Dropwheel. Il formato segue [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/)
e il progetto usa il [Versionamento Semantico](https://semver.org/lang/it/).

## [Non rilasciato]

### Aggiunto

- **KDE Plasma 6 su Debian, Ubuntu e Arch**, Wayland e X11, non ancora provato in una vera sessione Plasma. Vedi
  [Dropwheel su KDE Plasma](docs/KDE.it.md). La stessa ruota e lo stesso host di GNOME.
  - Uno script di KWin dice a Dropwheel dove si trova il puntatore (su Wayland) tramite D-Bus e registra la
    scorciatoia (`trigger.shortcut`, poi Impostazioni di sistema → Scorciatoie → KWin).
  - Shift e i tasti del mouse arrivano da XWayland o X11; un trascinamento da un programma Wayland conta dal suo inizio.
  - `dropwheel setup kde [--remove]`: lo script di KWin, l'avvio all'accesso e il font delle icone; `dropwheel doctor`
    controlla lo script e l'impostazione di KWin per il supporto delle applicazioni X11. `install.sh` riconosce
    Plasma (`--desktop kde`) e `uninstall.sh` rimuove lo script.
- **GNOME su Debian, Ubuntu e Arch**, non ancora provato in una vera sessione GNOME. Vedi
  [Dropwheel su GNOME](docs/GNOME.it.md). La stessa ruota, ospitata dall'host PySide6 dell'anteprima per Windows.
  - Un'estensione di GNOME Shell (GNOME 45 o successivo) dice a Dropwheel dove si trova il puntatore e se Shift
    e i tasti del mouse sono premuti, con un solo metodo D-Bus.
  - `dropwheel setup gnome [--remove]`: l'estensione, una scorciatoia personalizzata (`trigger.shortcut`) che
    esegue `dropwheel open`, l'avvio all'accesso e il font delle icone.
  - Indicazioni di installazione con `apt` su Debian e Ubuntu, e pip per i moduli Python.
- **Fedora e openSUSE con GNOME** (Fedora 43 o successivo, openSUSE Tumbleweed e Leap 16), non ancora provati su
  un vero computer. `install.sh` li riconosce (e i derivati di RHEL), installa i pacchetti con dnf o zypper,
  PySide6 compreso, e tiene un ambiente Python privato solo per ciò che installa pip; le indicazioni di
  installazione e `dropwheel doctor` danno i nomi dei pacchetti di dnf e zypper.
- **Installer interattivi.** `install.sh` (Arch, Debian, Ubuntu) e `install.ps1` (Windows) si aprono con il nome
  del progetto e i link, riconoscono il sistema e il desktop, e chiedono prima di ogni passo: i pacchetti
  obbligatori (installati con pacman, apt o winget dopo il tuo sì), i gruppi di funzioni facoltative da un
  elenco numerato, l'integrazione con il desktop, il `PATH`. `curl … | bash` e `irm … | iex` funzionano senza
  clonare prima.
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
- **Interfaccia per Windows (anteprima)**: la stessa ruota, le stesse schede, gli stessi editor e la scheda del
  codice QR di Linux, ospitati da PySide6 (`dropwheel.qtshell`). Si apre con Shift durante un trascinamento,
  l'angolo attivo, una scorciatoia globale e *Apri in Dropwheel* / *Invia a* in Esplora file.
  - `dropwheel setup windows [--remove]`: menu di Esplora file, *Invia a*, collegamenti nel menu Start e
    all'accesso, e il font delle icone (scaricato una volta da una versione fissa, controllato con il suo SHA-256).
  - `trigger.shortcut` (predefinita `Ctrl+Alt+D`), usata su Windows.
  - `dropwheel start`, `stop`, `status` e `open` controllano l'host su `127.0.0.1` con un token per ogni avvio.
  - `tests/test_qtshell.py`: 20 test, compreso il caricamento della vera shell senza schermo e senza avvisi QML.
- **Anteprima per macOS (macOS 13 o successivo, Apple silicon e Intel)**, mai eseguita su un Mac vero. Vedi
  [Dropwheel su macOS](docs/MACOS.it.md). La stessa ruota, ospitata dall'host PySide6.
  - Configurazione, cronologia e font delle icone in `~/Library/Application Support/dropwheel`, cache in
    `~/Library/Caches/dropwheel`.
  - `open` e `open -R`, `pbcopy` e `pbpaste`, file e immagini negli appunti e il Cestino tramite
    `osascript` con i percorsi come argomenti, Terminale per *Apri un terminale qui*, font in `~/Library/Fonts`.
  - Shift durante un trascinamento dallo stato delle sorgenti di eventi Quartz, letto con `ctypes`; la ruota
    resta sopra ogni Spazio e ogni app a tutto schermo senza prendere il focus. Ancora nessuna scorciatoia globale.
  - `dropwheel setup macos [--remove] [--dry-run]`: un'azione rapida del Finder (*Apri in Dropwheel*), un
    LaunchAgent per l'avvio all'accesso e il font delle icone.
  - Indicazioni di installazione con `brew install` e `brew install --cask`; `install.sh` funziona con il bash
    3.2 di macOS, usa Homebrew e propone di installare Homebrew solo mostrando il suo comando ufficiale e chiedendo.
  - `tests/test_macos.py`: test che esercitano le parti per macOS su Linux.
- `tests/test_windows.py`: 16 test che esercitano le parti per Windows su Linux.
- Documentazione divisa in guide nella cartella [docs](docs/README.it.md): installazione, uso, funzioni, riga di
  comando, risoluzione dei problemi, privacy, sviluppo e Windows, in inglese e in italiano.
- Questo registro delle modifiche.

### Modificato

- Su Windows il servizio scambia i percorsi con la shell nella forma `/C:/…`; PySide6-Essentials è una
  dipendenza su Windows.
- `install.sh` installa i pacchetti mancanti dopo averlo chiesto, da un terminale; prima stampava solo i
  comandi. Senza un terminale, o con `--no-packages`, continua a stamparli soltanto.
- L'interfaccia PySide6 è passata da `dropwheel.winshell` a `dropwheel.qtshell`, perché ora serve anche GNOME.
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
