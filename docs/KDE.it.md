[Documentazione](README.it.md) · [English](KDE.md)

# Dropwheel su KDE Plasma (Debian, Ubuntu, Arch)

> [!WARNING]
> **Questa versione è nuova e non è ancora stata eseguita in una vera sessione Plasma.** L'interfaccia ha
> girato su Linux attraverso lo stesso host, il servizio D-Bus chiamato dallo script di KWin è stato provato
> con un sostituto e l'installer in una cartella home privata, ma ciò che solo Plasma può mostrare (Shift
> durante un trascinamento da Dolphin, lo script di KWin, la scorciatoia) non è verificato. [Segnala ciò che trovi](https://github.com/haxurus/dropwheel/issues).

Su KDE Plasma Dropwheel fa girare **la stessa ruota di Hyprland e GNOME**: l'interfaccia (ruota, schede dei
lavori, editor) è lo stesso codice QML, ospitato da PySide6 invece che da Quickshell, e le azioni sono lo
stesso motore Python. Su Wayland un piccolo script di KWin gli dice dove si trova il puntatore, cosa che KWin
non dice ai programmi normali; su X11 per questo non serve altro.

## Cosa funziona

| Parte | Stato su KDE Plasma |
|---|---|
| La ruota, le schede dei lavori, gli editor, il codice QR per la condivisione | **Nuovo** |
| Shift durante un trascinamento da Dolphin o dal desktop | **Nuovo**: su Wayland serve lo script di KWin |
| Angolo attivo | **Nuovo**: in alto a destra |
| Scorciatoia (<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd> come predefinita) | **Nuovo**: registrata dallo script di KWin |
| Dolphin: tasto destro → *Apri in Dropwheel* | **Supportato** |
| Riga di comando | **Supportata**: ogni comando |
| Colori di Caelestia | Non disponibili: Dropwheel usa la sua tavolozza scura integrata |

## Installazione

**Requisiti**

| | |
|---|---|
| **Sistema** | Debian 13, Ubuntu o Kubuntu 25.04 o successivo, Arch Linux, con Plasma 6 (Plasma 5, come su Kubuntu 24.04, non è supportato) |
| **Python** | 3.11 o successivo (già presente su questi sistemi) |
| **Sessione** | Plasma su Wayland o su X11 |

**Installare con un solo comando**

```sh
curl -fsSL https://raw.githubusercontent.com/haxurus/dropwheel/main/install.sh | bash
```

L'installer riconosce la sessione Plasma e chiede prima di ogni passo:

- i pacchetti che servono a Dropwheel, installati con apt o pacman (sudo chiede la password una volta);
- quali gruppi di funzioni facoltative vuoi (immagini, PDF, OCR, documenti, audio e video, archivi, font,
  condivisione): un elenco numerato, <kbd>Invio</kbd> li prende tutti;
- su Debian e Ubuntu, un ambiente Python privato in `~/.local/share/dropwheel/venv` con PySide6 (80 MB da
  scaricare) e i moduli Python dei gruppi scelti, così il Python di sistema non viene mai toccato;
- la voce di Dolphin (*Apri in Dropwheel*);
- `dropwheel setup kde`: lo script di KWin, la scorciatoia, l'avvio all'accesso e il font delle icone.

KWin carica subito lo script, senza uscire dalla sessione. Da una copia del repository, `./install.sh` fa lo
stesso; `./install.sh --desktop kde` sceglie Plasma quando l'installer gira fuori da una sessione Plasma.
`./install.sh --help` elenca le opzioni; `--no-packages` mostra solo i comandi.

**Cosa cambia `dropwheel setup kde`**

| Cosa | Dove |
|---|---|
| Lo script di KWin | `~/.local/share/kwin/scripts/dropwheel`, attivato in `~/.config/kwinrc` (`[Plugins] dropwheelEnabled`) |
| La scorciatoia | Registrata dallo script: Impostazioni di sistema → Tastiera → Scorciatoie → KWin → *Dropwheel* |
| L'avvio all'accesso | `~/.config/autostart/dropwheel.desktop` |
| Il font delle icone | `~/.local/share/dropwheel/fonts`, scaricato una volta da una versione fissa e controllato con il suo SHA-256 |

`dropwheel setup kde --dry-run` mostra cosa cambierebbe, `--remove` toglie tutto, e
`~/.local/share/dropwheel/uninstall.sh` rimuove Dropwheel stesso. La voce di Dolphin è
`dropwheel setup dolphin` (`~/.local/share/kio/servicemenus/dropwheel.desktop`, eseguibile come richiede Plasma 6).

## Uso

| Modo | Cosa fai |
|---|---|
| **Shift durante un trascinamento** | Inizia a trascinare dei file in Dolphin o sul desktop e tocca <kbd>Shift</kbd>: la ruota si apre sotto il puntatore. Spostati su un settore e rilascia |
| **Angolo attivo** | Trascina dei file nell'angolo in alto a destra: la ruota si apre lì accanto |
| **Scorciatoia** | Copia dei file in Dolphin (<kbd>Ctrl</kbd>+<kbd>C</kbd>), poi premi <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd>: la ruota si apre sul puntatore con quei file |
| **Menu di Dolphin** | Tasto destro sui file → *Apri in Dropwheel* |
| **Riga di comando** | `dropwheel open photo.jpg`, `dropwheel run image.compress.balanced photo.jpg` |

La scorciatoia parte dalla chiave `shortcut` di [`[trigger]`](CONFIG.it.md#trigger); una volta registrata dallo
script, si cambia in Impostazioni di sistema → Tastiera → Scorciatoie → KWin. La [guida all'uso](USAGE.it.md)
descrive la ruota.

## Differenze rispetto a Hyprland

- **Come viene riconosciuto un trascinamento.** Su X11, come su GNOME e Windows, "Shift mentre il tasto del
  mouse è premuto". Su Wayland KWin dice agli altri programmi quando un trascinamento inizia ma non quando
  finisce, quindi un tocco di Shift entro 10 secondi dall'inizio di un trascinamento vale come "durante un
  trascinamento". Un trascinamento che finisce altrove lascia la ruota aperta fino allo scadere di quei 10 secondi.
- **Shift su Wayland** arriva a Dropwheel perché KWin passa i tasti modificatori ai programmi X11
  (Impostazioni di sistema → Supporto per le applicazioni X11, predefinito da Plasma 6.2). Con *Mai*, Shift
  durante un trascinamento non fa nulla; `dropwheel doctor` lo segnala.
- **La finestra passa da XWayland**, così può posizionarsi e restare sopra le altre finestre.
- **Senza lo script di KWin** la ruota si apre comunque dal menu di Dolphin e dalla riga di comando; su
  Wayland si apre dove il puntatore è passato l'ultima volta su una finestra di Dropwheel, e la scorciatoia non c'è.
- **Colori.** I colori di Caelestia non esistono su Plasma: l'interfaccia usa la sua tavolozza scura integrata.

## Risoluzione dei problemi

| Problema | Cosa fare |
|---|---|
| Non succede nulla | `dropwheel status` dice se Dropwheel è in esecuzione, `dropwheel doctor` cosa manca. Il log è `~/.cache/dropwheel/qtshell.log` |
| Shift durante un trascinamento non fa nulla | `dropwheel doctor` controlla lo script di KWin e l'impostazione del supporto per le applicazioni X11. In Impostazioni di sistema → Gestione delle finestre → Script di KWin, *Dropwheel* deve essere attivo |
| La ruota si apre nel posto sbagliato | Lo script di KWin non è in esecuzione: `dropwheel setup kde`, poi muovi il mouse |
| La scorciatoia non fa nulla | Un'altra azione potrebbe usarla: cambiala in Impostazioni di sistema → Tastiera → Scorciatoie → KWin |
| Le icone appaiono come parole | Manca il font delle icone: esegui di nuovo `dropwheel setup kde` (serve la rete una volta) |
| La ruota è sfocata, o nel posto sbagliato, con il ridimensionamento dello schermo | Segnalalo indicando il tuo ridimensionamento e la scelta per le applicazioni X11: non sono ancora stati provati |

## Prossimi passi

1. Provare su veri computer con Debian, Kubuntu e Arch, su Wayland e X11, con e senza ridimensionamento dello schermo.
2. Riconoscere la fine di un trascinamento su Wayland, se l'interfaccia di scripting di KWin darà i tasti del mouse.
3. Colori che seguono lo schema di colori di Plasma.

I progressi sono nel [registro delle modifiche](../CHANGELOG.it.md).

---

← [Documentazione](README.it.md)
