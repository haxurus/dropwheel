[Documentazione](README.it.md) · [English](WINDOWS.md)

# Dropwheel su Windows (anteprima)

> [!WARNING]
> **Questa è un'anteprima e non è ancora stata eseguita su un vero computer Windows.** L'interfaccia
> ha girato su Linux attraverso lo stesso host di Windows e le parti per Windows sono coperte da test
> automatici, ma ciò che solo Windows può mostrare (Shift durante un trascinamento da Esplora file, la
> scorciatoia, i menu di Esplora file) non è verificato. [Segnala ciò che trovi](https://github.com/haxurus/dropwheel/issues).

Su Windows 10 e 11 Dropwheel fa girare **la stessa ruota di Linux**: l'interfaccia (ruota, schede dei
lavori, editor) è lo stesso codice QML, ospitato da PySide6 invece che da Quickshell, e le azioni sono lo
stesso motore Python.

## Cosa funziona

| Parte | Stato su Windows |
|---|---|
| La ruota, le schede dei lavori, gli editor, il codice QR per la condivisione | **Anteprima** |
| Shift durante un trascinamento da Esplora file o dal desktop | **Anteprima** |
| Angolo attivo | **Anteprima** |
| Scorciatoia (<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd> come predefinita) | **Anteprima** |
| Esplora file: *Apri in Dropwheel* nel menu contestuale, *Invia a* | **Anteprima** |
| Riga di comando | **Anteprima**: ogni comando, compresi `start`, `stop`, `open` |
| Selettore di colore e di file negli editor | **Anteprima**: il selettore di Dropwheel e la finestra dei file di Windows |
| Colori di Caelestia | Non disponibili: Dropwheel usa la sua tavolozza scura integrata |
| Notifiche del desktop | Non ancora: le schede dei lavori mostrano ogni risultato |

## Installazione

**Requisiti**

| | |
|---|---|
| **Sistema** | Windows 10 o 11 |
| **Python** | 3.11 o successivo, da [python.org](https://www.python.org/downloads/windows/) oppure `winget install --id Python.Python.3.12 -e` |
| **Git** | Per scaricare il codice: `winget install --id Git.Git -e` |

**Installare con un solo comando**

```powershell
irm https://raw.githubusercontent.com/haxurus/dropwheel/main/install.ps1 | iex
```

L'installer chiede prima di ogni passo: installa Python e Git con winget se mancano, scarica il codice in
`%LOCALAPPDATA%\dropwheel\src`, ti fa scegliere i moduli Python e i programmi facoltativi da elenchi
numerati, può aggiungere il comando `dropwheel` al `PATH`, poi esegue `dropwheel setup windows` e avvia
Dropwheel. Da una copia del repository, `powershell -ExecutionPolicy Bypass -File install.ps1` fa lo stesso.

**Oppure installare dal repository a mano**

```powershell
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
py -m pip install -e .
dropwheel setup windows
dropwheel start
```

`pip install -e` lascia Dropwheel collegato alla cartella clonata, dove si trovano i file
dell'interfaccia: lascia la cartella dov'è. Così si installano anche Pillow, `send2trash` e PySide6-Essentials
(80 MB da scaricare). Per aggiornare esegui `git pull` nella cartella, poi `dropwheel restart`.

`dropwheel setup windows` prepara tutto solo per l'utente corrente:

- *Apri in Dropwheel* nel menu contestuale di file e cartelle, e *Dropwheel* in *Invia a*;
- un collegamento *Dropwheel* nel menu Start e uno nella cartella Esecuzione automatica, così Dropwheel
  parte all'accesso;
- il font delle icone dell'interfaccia (Material Symbols Rounded, 15 MB): scaricato una volta da una
  versione fissa su GitHub e controllato con il suo SHA-256 prima di usarlo.

`--dry-run` mostra cosa cambierebbe, `--remove` toglie tutto. `dropwheel doctor` controlla
l'installazione ed elenca ciò che manca.

I moduli Python facoltativi abilitano altre azioni, per famiglia:

```powershell
py -m pip install -e ".[image,pdf,docs,media,fonts,archive,qr]"
```

Se uno di questi non si installa, lascialo fuori: ne risentono solo le azioni che lo usano.

**Programmi esterni**

Come su Linux, ogni programma è facoltativo e abilita le sue azioni. `dropwheel doctor` elenca ciò che manca,
con il comando che lo installa. I più utili:

```powershell
winget install --id Gyan.FFmpeg -e
winget install --id ImageMagick.ImageMagick -e
winget install --id OliverBetz.ExifTool -e
winget install --id TheDocumentFoundation.LibreOffice -e
winget install --id JohnMacFarlane.Pandoc -e
winget install --id QPDF.QPDF -e
winget install --id oschwartz10612.Poppler -e
winget install --id UB-Mannheim.TesseractOCR -e
winget install --id 7zip.7zip -e
winget install --id yt-dlp.yt-dlp -e
```

Ghostscript viene trovato come `gswin64c` dopo averlo installato dal [sito ufficiale](https://ghostscript.com/releases/).
Dopo aver installato un programma esegui `dropwheel restart`, così Dropwheel vede il nuovo `PATH`.

## Uso

| Modo | Cosa fai |
|---|---|
| **Shift durante un trascinamento** | Inizia a trascinare dei file in Esplora file o sul desktop e tocca <kbd>Shift</kbd>: la ruota si apre sotto il puntatore. Spostati su un settore e rilascia. Tocca di nuovo <kbd>Shift</kbd> per cambiare pagina |
| **Angolo attivo** | Trascina dei file nell'angolo in alto a destra dello schermo: la ruota si apre lì accanto. Rilasciandoli proprio sull'angolo, la ruota si apre in modalità clic |
| **Scorciatoia** | Copia dei file in Esplora file (<kbd>Ctrl</kbd>+<kbd>C</kbd>), poi premi <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd>: la ruota si apre sul puntatore con quei file. Se non hai copiato nulla, è un bersaglio vuoto su cui rilasciare i file |
| **Menu contestuale** | Clic destro su file o cartelle → *Apri in Dropwheel* (su Windows 11, sotto *Mostra altre opzioni*) |
| **Invia a** | Clic destro → *Invia a* → *Dropwheel* |
| **Riga di comando** | `dropwheel open photo.jpg`, `dropwheel run image.compress.balanced photo.jpg` |

Mentre tieni premuto <kbd>Shift</kbd>, Esplora file mostra *Sposta*: Dropwheel lavora sempre su una
copia, quindi l'originale resta dov'è. I risultati vengono scritti accanto all'originale e un file
esistente non viene mai sovrascritto, come su Linux. La [guida all'uso](USAGE.it.md) descrive la ruota;
ogni comando è nella [guida alla riga di comando](CLI.it.md).

La scorciatoia è la chiave `shortcut` di [`[trigger]`](CONFIG.it.md#trigger), per esempio
`shortcut = "Win+Shift+D"`; un valore vuoto la disattiva. Dopo averla cambiata esegui `dropwheel restart`.

**Dove Dropwheel tiene i suoi file**

| Cosa | Cartella |
|---|---|
| Configurazione | `%APPDATA%\dropwheel\config.toml` |
| Cronologia e copie per annullare | `%LOCALAPPDATA%\dropwheel` |
| Icona e font delle icone | `%LOCALAPPDATA%\dropwheel` |
| Cache, miniature e log (`qtshell.log`) | `%LOCALAPPDATA%\Temp\dropwheel` |

## Differenze rispetto a Linux

- **Come viene riconosciuto un trascinamento.** Windows non dice agli altri programmi che è in corso un
  trascinamento, quindi Dropwheel considera tale "Shift mentre il tasto del mouse è premuto". Anche
  Shift mentre selezioni del testo col mouse apre la ruota, che si richiude quando rilasci il tasto.
- **Colori.** I colori di Caelestia non esistono su Windows: l'interfaccia usa la sua tavolozza scura
  integrata.
- **Azioni non disponibili.** AppImage, immagini disco Linux e pacchetti delle distribuzioni non vengono
  nemmeno caricati. Impostare lo sfondo, il formato per lo schermo, annotare con satty o swappy, stampare,
  installare font e KDE Connect sono nascosti e non si possono lanciare.
- **Solo il Cestino.** Se `send2trash` manca, o il file sta su una condivisione di rete o su un'unità
  rimovibile, che non hanno Cestino, Dropwheel rifiuta e lascia il file dov'è.
- **Solo eseguibili veri.** I programmi vengono cercati come `.exe` o `.com`, mai nella cartella corrente e mai
  come script `.cmd` o `.bat`, perché questi passano da `cmd.exe`, che leggerebbe `&` o `%` in un nome di file
  come un comando. Gli strumenti installati con npm (`svgo`, `prettier`) risultano quindi mancanti. Per un
  editor, scrivi il percorso completo del suo `.exe`.
- **Azioni personalizzate.** Un comando scritto come lista viene eseguito direttamente. Un comando scritto come
  stringa unica richiede `sh`, per esempio quello di Git for Windows; altrimenti appare in grigio.
- **Terminale.** *Apri un terminale qui* apre una console `cmd.exe` nella cartella.
- **Mostra nella cartella** seleziona il primo file quando sono più di uno.
- **I comandi configurati** seguono le virgolette di Windows: `"C:\Program Files\App\app.exe" --flag`.

## Risoluzione dei problemi

| Problema | Cosa fare |
|---|---|
| Non succede nulla | `dropwheel status` dice se Dropwheel è in esecuzione. `dropwheel start --foreground` lo avvia nel terminale con i suoi messaggi; il log è `%LOCALAPPDATA%\Temp\dropwheel\qtshell.log` |
| Le icone appaiono come parole | Manca il font delle icone: esegui di nuovo `dropwheel setup windows` (serve la rete una volta) |
| La scorciatoia non fa nulla | Un altro programma la usa già: il log lo dice. Scegline un'altra in `trigger.shortcut` |
| *Apri in Dropwheel* non è nel menu | Su Windows 11 è sotto *Mostra altre opzioni* (oppure <kbd>Shift</kbd>+<kbd>F10</kbd>). Se manca anche lì, esegui di nuovo `dropwheel setup windows` |
| La ruota si apre sullo schermo sbagliato, o sfocata | Segnalalo indicando i tuoi schermi e il loro ridimensionamento: schermi con scale diverse non sono ancora stati provati |
| L'antivirus avvisa | Dropwheel legge lo stato di Shift e dei tasti del mouse (nessun hook della tastiera) e ascolta solo su `127.0.0.1`, con un token che cambia a ogni avvio. Segnala l'avviso |

## Prossimi passi

1. Provare l'anteprima su veri computer con Windows 10 e 11, anche con schermi a scale diverse.
2. Notifiche del desktop per i lavori lunghi.
3. Una voce nel nuovo menu contestuale di Windows 11, che richiede un pacchetto firmato.
4. Colori che seguono la modalità chiara o scura e il colore d'accento di Windows.

I progressi sono nel [registro delle modifiche](../CHANGELOG.it.md).

---

← [Documentazione](README.it.md)
