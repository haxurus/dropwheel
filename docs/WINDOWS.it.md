[Documentazione](README.it.md) · [English](WINDOWS.md)

# Dropwheel su Windows (anteprima)

> [!WARNING]
> **Questa è un'anteprima e non è ancora stata eseguita su un vero computer Windows.** Le parti per Windows
> sono coperte da test automatici che simulano Windows su Linux: verificano la logica, non il comportamento
> di Windows stesso. [Segnala ciò che trovi](https://github.com/haxurus/dropwheel/issues).

Su Windows 10 e 11 Dropwheel offre per ora il suo **motore delle azioni e la riga di comando**: le stesse
azioni di Linux, lanciate da un terminale. La ruota che si apre toccando <kbd>Shift</kbd> durante un
trascinamento non è ancora disponibile, perché la sua interfaccia è costruita su Quickshell, che esiste solo su Linux.

## Cosa funziona

| Parte | Stato su Windows |
|---|---|
| Azioni dalla riga di comando | **Anteprima.** `dropwheel run`, `menu`, `actions`, `undo`, `history`, `config`, `doctor` |
| Programmi esterni | Cercati nel `PATH`, in `Program Files` e nella cartella dei collegamenti di winget |
| Indicazioni di installazione | `winget install` con l'id del pacchetto, `python -m pip install` per i moduli Python |
| Appunti | Testo, file e immagini tramite Windows PowerShell 5.1 |
| Apri e mostra nella cartella | L'applicazione predefinita, ed Esplora file con il file selezionato |
| Cestino | Il Cestino di Windows, tramite `send2trash` |
| Condivisione in rete locale con QR | Anteprima |
| La ruota, le schede dei lavori e gli editor | **Non ancora** |
| Shift durante un trascinamento, angolo attivo, SUPER+ALT+D | **Non ancora** |
| Integrazione con Esplora file | **Non ancora** |

## Installazione

**Requisiti**

| | |
|---|---|
| **Sistema** | Windows 10 o 11 |
| **Python** | 3.11 o successivo, da [python.org](https://www.python.org/downloads/windows/) oppure `winget install --id Python.Python.3.12 -e` |
| **Windows PowerShell** | 5.1, incluso in Windows |

**Installare dal repository**

```powershell
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
py -m pip install .
dropwheel doctor
```

Così si installano anche Pillow e `send2trash`, obbligatori su Windows. I moduli Python facoltativi
abilitano altre azioni, per famiglia:

```powershell
py -m pip install ".[image,pdf,docs,media,fonts,archive,qr]"
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
Dopo aver installato un programma apri un nuovo terminale, così il `PATH` è aggiornato.

## Uso

```powershell
dropwheel menu photo.heic                        # le azioni disponibili per un file
dropwheel run image.compress.balanced photo.jpg  # lanciarne una
dropwheel undo                                   # mandare nel Cestino i risultati dell'ultimo lavoro
```

I risultati vengono scritti accanto all'originale, esattamente come su Linux, e un file esistente non viene mai
sovrascritto. Ogni comando è descritto nella [guida alla riga di comando](CLI.it.md).

**Dove Dropwheel tiene i suoi file**

| Cosa | Cartella |
|---|---|
| Configurazione | `%APPDATA%\dropwheel\config.toml` |
| Cronologia e copie per annullare | `%LOCALAPPDATA%\dropwheel` |
| Cache e miniature | `%LOCALAPPDATA%\Temp\dropwheel` |

## Differenze rispetto a Linux

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

## Prossimi passi

1. Provare l'anteprima su veri computer con Windows 10 e 11.
2. Un'interfaccia per Windows che riusi la ruota, le schede dei lavori e gli editor.
3. Aprire la ruota con <kbd>Shift</kbd> durante un trascinamento da Esplora file. In Esplora file Shift
   significa *sposta*, quindi il rilascio verrà sempre trattato come una copia.
4. Le voci **Invia a** e del menu contestuale in Esplora file.

I progressi sono nel [registro delle modifiche](../CHANGELOG.it.md).

---

← [Documentazione](README.it.md)
