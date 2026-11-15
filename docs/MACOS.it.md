[Documentazione](README.it.md) · [English](MACOS.md)

# Dropwheel su macOS (anteprima)

> [!WARNING]
> **Questa è un'anteprima, e non è mai stata eseguita su un Mac vero.** È stata scritta e provata solo
> su Linux: le parti per macOS sono coperte da test automatici che fingono di essere su macOS, ma niente
> di ciò che solo un Mac può mostrare (Shift durante un trascinamento dal Finder, la ruota sopra le altre
> finestre, l'azione rapida, l'avvio all'accesso) è stato visto funzionare. Aspettati qualche asperità, e
> [segnala ciò che trovi](https://github.com/haxurus/dropwheel/issues).

Su macOS 13 Ventura o successivo, sui Mac con Apple silicon e Intel, Dropwheel esegue **la stessa ruota
di Linux e Windows**: l'interfaccia (ruota, schede dei lavori, editor) è lo stesso codice QML, ospitato
da PySide6, e le azioni sono lo stesso motore Python.

## Cosa funziona

| Parte | Stato su macOS |
|---|---|
| La ruota, le schede dei lavori, gli editor, il codice QR per la condivisione | **Anteprima** |
| Shift durante un trascinamento dal Finder o dalla scrivania | **Anteprima** |
| Angolo attivo | **Anteprima** |
| Finder: *Apri in Dropwheel* tra le *Azioni rapide* | **Anteprima** |
| Avvio all'accesso | **Anteprima**: un LaunchAgent |
| Riga di comando | **Anteprima**: tutti i comandi, compresi `start`, `stop`, `open` |
| Selettore di colore negli editor | **Anteprima, non provata**: serve il permesso di Registrazione schermo, vedi sotto |
| Selettore di file negli editor | **Anteprima**: la finestra dei file di macOS |
| Scorciatoia | Non ancora: usa Shift durante un trascinamento, l'azione rapida oppure `dropwheel open` |
| Colori di Caelestia | Non disponibili: Dropwheel usa la sua tavolozza scura |
| Notifiche del desktop | Non ancora: le schede dei lavori mostrano ogni risultato |

## Installazione

**Requisiti**

| | |
|---|---|
| **Sistema** | macOS 13 Ventura o successivo, Apple silicon o Intel |
| **Homebrew** | Da [brew.sh](https://brew.sh): Python e i programmi facoltativi arrivano da lì |
| **Python** | Da 3.11 a 3.15 (le versioni per cui PySide6-Essentials ha i wheel): `brew install python@3.13`. Il `python3` dei Command Line Tools è troppo vecchio |

**Installazione con un comando**

```bash
curl -fsSL https://raw.githubusercontent.com/haxurus/dropwheel/main/install.sh | bash
```

È lo stesso installatore di Linux, e funziona con il `bash` 3.2 che c'è in macOS. Chiede prima di ogni
passo. Se manca Homebrew mostra il comando ufficiale di installazione di Homebrew e lo esegue solo se
rispondi sì. Installa Python con Homebrew quando serve, crea un ambiente Python privato
(`~/.local/share/dropwheel/venv`) con PySide6-Essentials (80 MB da scaricare) e Pillow, e ti fa scegliere
i programmi facoltativi da elenchi numerati. Poi installa Dropwheel in `~/.local`, propone di eseguire
`dropwheel setup macos` e lo avvia. Da una copia clonata, `./install.sh` fa lo stesso.

**Oppure installa a mano dal repository**

```bash
brew install python@3.13
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
python3.13 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/dropwheel setup macos
.venv/bin/dropwheel start
```

`pip install -e` tiene Dropwheel collegato alla cartella clonata, dove ci sono i file dell'interfaccia:
lascia la cartella dov'è. Per aggiornare, esegui `git pull` nella cartella, poi `dropwheel restart`.

`dropwheel setup macos` prepara tutto solo per l'utente corrente:

- *Apri in Dropwheel* tra le *Azioni rapide* del Finder (tasto destro su file o cartelle): un piccolo
  flusso di lavoro di Automator in `~/Library/Services` che esegue `dropwheel open` con gli elementi scelti;
- un LaunchAgent in `~/Library/LaunchAgents`, così Dropwheel parte a ogni accesso. Viene scritto, non
  caricato: `dropwheel start` avvia Dropwheel adesso, e launchd legge il file al prossimo accesso;
- il font delle icone dell'interfaccia (Material Symbols Rounded, 15 MB): scaricato una volta da una
  versione fissa su GitHub e controllato con il suo SHA-256 prima di usarlo. Dropwheel lo carica solo
  per sé, non viene aggiunto ai Font del sistema.

`--dry-run` mostra cosa cambierebbe, `--remove` toglie tutto. `dropwheel doctor` controlla
l'installazione ed elenca cosa manca.

**Programmi esterni**

Come su Linux, ogni programma è facoltativo e abilita le sue azioni. `dropwheel doctor` elenca cosa
manca, con il comando `brew` che lo installa. I più utili:

```bash
brew install ffmpeg imagemagick exiftool qpdf poppler ghostscript tesseract tesseract-lang
brew install pandoc sevenzip yt-dlp qrencode
brew install --cask libreoffice
```

Dropwheel guarda anche in `/opt/homebrew/bin` (Apple silicon) e `/usr/local/bin` (Intel), così trova i
programmi di Homebrew anche quando parte all'accesso, senza il `PATH` della tua shell.

## Uso

| Modo | Cosa fai |
|---|---|
| **Shift durante un trascinamento** | Inizia a trascinare dei file nel Finder o sulla scrivania, premi <kbd>Shift</kbd>: la ruota si apre sotto il puntatore. Spostati su un settore e rilascia. Premi di nuovo <kbd>Shift</kbd> per cambiare pagina |
| **Angolo attivo** | Trascina dei file nell'angolo in alto a destra dello schermo: la ruota si apre lì accanto. Rilasciandoli proprio sull'angolo la ruota si apre in modalità clic |
| **Azione rapida** | Tasto destro su file o cartelle → *Azioni rapide* → *Apri in Dropwheel* (oppure *Servizi*, secondo la versione di macOS) |
| **Riga di comando** | `dropwheel open foto.jpg`, `dropwheel run image.compress.balanced foto.jpg` |

Dropwheel lavora sempre su una copia, quindi l'originale resta dov'è. I risultati vengono scritti
accanto all'originale e un file esistente non viene mai sovrascritto, come su Linux. La
[guida all'uso](USAGE.it.md) descrive la ruota; tutti i comandi sono nella
[guida alla riga di comando](CLI.it.md).

**Dove Dropwheel tiene i suoi file**

| Cosa | Cartella |
|---|---|
| Configurazione | `~/Library/Application Support/dropwheel/config.toml` |
| Cronologia, copie per l'annullamento e font delle icone | `~/Library/Application Support/dropwheel` |
| Cache, miniature e registro (`qtshell.log`) | `~/Library/Caches/dropwheel` |
| Azione rapida | `~/Library/Services/Open in Dropwheel.workflow` |
| Avvio all'accesso | `~/Library/LaunchAgents/io.github.haxurus.dropwheel.plist` |

Le variabili `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` e `XDG_CACHE_HOME`, se impostate, hanno comunque la precedenza.

## Differenze da Linux

- **Come si riconosce un trascinamento.** macOS non dice agli altri programmi che è in corso un
  trascinamento, quindi Dropwheel considera tale "Shift mentre il tasto del mouse è premuto". Legge lo
  stato di Shift e dei tasti del mouse (lo stato delle sorgenti di eventi Quartz); non installa nessun
  event tap né hook della tastiera. Per quanto documenta Apple, non servono i permessi di Accessibilità
  né di Monitoraggio dell'input, ma non è stato verificato su un Mac vero.
- **Ancora nessuna scorciatoia.** Una scorciatoia globale richiede `RegisterEventHotKey` di Carbon, che
  non è ancora fatto. Su macOS `trigger.shortcut` viene ignorato.
- **Nessuna icona nel Dock.** Dropwheel gira come aiutante in background, senza icona nel Dock. Mentre una
  delle sue finestre ha il focus (un editor, per esempio) la barra dei menu mostra il menu, quasi vuoto, di Dropwheel.
- **Non firmato.** Dropwheel non è un pacchetto app e non è firmato né autenticato da Apple: è il Python
  di Homebrew che esegue il codice di Dropwheel. Gatekeeper non interviene, perché niente viene scaricato
  da un browser, ma macOS può mostrare *Elementi in background aggiunti* per il LaunchAgent (elencato come
  Python, in Impostazioni di Sistema → Generali → Elementi login), e può chiedere se Python può aprire i
  file di Scrivania, Documenti o Download la prima volta che un'azione lavora lì.
- **Selettore di colore.** Per macOS leggere il colore sotto il puntatore è una registrazione dello schermo:
  la prima volta chiede di dare a Python il permesso di Registrazione schermo (Impostazioni di Sistema →
  Privacy e sicurezza → Registrazione schermo e audio di sistema). Senza, si può campionare solo lo sfondo
  della scrivania. Non provato.
- **Colori.** I colori di Caelestia non esistono su macOS: l'interfaccia usa la sua tavolozza scura.
- **Azioni non disponibili.** AppImage, immagini disco Linux e pacchetti delle distribuzioni non vengono
  caricati affatto. Impostare lo sfondo, il formato delle dimensioni del monitor, annotare con satty o
  swappy e KDE Connect sono nascosti e non si possono eseguire. La stampa funziona con CUPS, che fa parte
  di macOS.
- **I font** si installano in `~/Library/Fonts`, dove macOS li trova senza fontconfig.
- **Solo il Cestino del Finder.** I file vanno nel Cestino tramite macOS stesso; quando un volume non ha
  un Cestino, Dropwheel rifiuta e lascia il file dov'è.
- **Appunti.** Il testo passa da `pbcopy` e `pbpaste`, file e immagini dagli appunti di sistema, così il
  Finder incolla i file copiati.
- **Terminale.** *Apri un terminale qui* apre Terminale nella cartella; `apps.terminal` può indicare
  un'altra app, per esempio `iTerm`.
- **Mostra nella cartella** seleziona i file nel Finder.

## Risoluzione dei problemi

| Problema | Cosa fare |
|---|---|
| Non succede niente | `dropwheel status` dice se Dropwheel è in esecuzione. `dropwheel start --foreground` lo esegue nel terminale con i suoi messaggi; il registro è `~/Library/Caches/dropwheel/qtshell.log` |
| Le icone appaiono come parole | Manca il font delle icone: esegui di nuovo `dropwheel setup macos` (serve la rete una volta) |
| *Apri in Dropwheel* non è nel menu | Guarda anche tra i *Servizi*. Attivalo in Impostazioni di Sistema → Tastiera → Abbreviazioni da tastiera → Servizi, oppure esci e rientra una volta |
| Shift durante un trascinamento non fa niente | Controlla che Dropwheel sia in esecuzione, poi segnala la tua versione di macOS: non è verificato che macOS gli lasci leggere il tasto Shift |
| La ruota si apre nel posto sbagliato, o dietro un'app a tutto schermo | Segnalalo indicando i tuoi schermi e il loro ridimensionamento: niente di questo è stato provato su un Mac |
| Il selettore di colore restituisce il colore dello sfondo | Dai a Python il permesso di Registrazione schermo in Impostazioni di Sistema → Privacy e sicurezza, poi `dropwheel restart` |
| Un programma risulta mancante anche se è installato | Esegui `dropwheel restart`; i programmi fuori dalle cartelle di Homebrew devono essere nel `PATH` della sessione |

## Prossimi passi

1. Provare l'anteprima su Mac veri, Apple silicon e Intel, e correggere ciò che non va.
2. Una scorciatoia globale con `RegisterEventHotKey` di Carbon.
3. Un pacchetto app firmato e autenticato, così che Dropwheel compaia con il suo nome nelle impostazioni di sistema.
4. Notifiche del desktop per i lavori lunghi.

I progressi sono nel [registro delle modifiche](../CHANGELOG.it.md).

---

← [Documentazione](README.it.md)
