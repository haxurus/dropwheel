[Documentazione](README.it.md) · [English](GNOME.md)

# Dropwheel su GNOME (Debian, Ubuntu, Arch)

> [!WARNING]
> **Questa versione è nuova e non è ancora stata eseguita in una vera sessione GNOME.** L'interfaccia ha
> girato su Linux attraverso lo stesso host, l'estensione di GNOME è stata provata con un sostituto e
> l'installer con un Ubuntu simulato, ma ciò che solo GNOME può mostrare (Shift durante un trascinamento da
> File, l'estensione, la scorciatoia) non è verificato. [Segnala ciò che trovi](https://github.com/haxurus/dropwheel/issues).

Su GNOME Dropwheel fa girare **la stessa ruota di Hyprland**: l'interfaccia (ruota, schede dei lavori,
editor) è lo stesso codice QML, ospitato da PySide6 invece che da Quickshell, e le azioni sono lo stesso
motore Python. Una piccola estensione di GNOME Shell gli dice dove si trova il puntatore e se Shift è premuto,
cose che GNOME non dice ai programmi normali.

## Cosa funziona

| Parte | Stato su GNOME |
|---|---|
| La ruota, le schede dei lavori, gli editor, il codice QR per la condivisione | **Nuovo** |
| Shift durante un trascinamento da File (Nautilus) o dal desktop | **Nuovo**: serve l'estensione |
| Angolo attivo | **Nuovo**: in alto a destra, sotto la barra superiore |
| Scorciatoia (<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd> come predefinita) | **Nuovo** |
| File: tasto destro → *Script* → *Dropwheel* | **Supportato** |
| Riga di comando | **Supportata**: ogni comando |
| Colori di Caelestia | Non disponibili: Dropwheel usa la sua tavolozza scura integrata |

## Installazione

**Requisiti**

| | |
|---|---|
| **Sistema** | Debian 13, Ubuntu 24.04 o successivo, Arch Linux, con GNOME 45 o successivo |
| **Python** | 3.11 o successivo (già presente su questi sistemi) |
| **Sessione** | GNOME su Wayland o su X11 |

**Installare con un solo comando**

```sh
curl -fsSL https://raw.githubusercontent.com/haxurus/dropwheel/main/install.sh | bash
```

L'installer mostra cosa ha trovato (sistema, desktop, cartella) e chiede prima di ogni passo:

- i pacchetti che servono a Dropwheel, installati con apt o pacman (sudo chiede la password una volta);
- quali gruppi di funzioni facoltative vuoi (immagini, PDF, OCR, documenti, audio e video, archivi, font,
  condivisione): un elenco numerato, <kbd>Invio</kbd> li prende tutti;
- su Debian e Ubuntu, un ambiente Python privato in `~/.local/share/dropwheel/venv` con PySide6 (80 MB da
  scaricare) e i moduli Python dei gruppi scelti, così il Python di sistema non viene mai toccato;
- `dropwheel setup gnome`: l'estensione, la scorciatoia, l'avvio all'accesso e il font delle icone.

Poi **esci e rientra una volta**: GNOME carica una nuova estensione solo all'accesso. Da una copia del
repository, `./install.sh` fa lo stesso. `./install.sh --help` elenca le opzioni; `--no-packages` mostra solo
i comandi.

**Cosa cambia `dropwheel setup gnome`**

| Cosa | Dove |
|---|---|
| L'estensione di GNOME Shell | `~/.local/share/gnome-shell/extensions/dropwheel@haxurus.github.io`, attivata in `org.gnome.shell` |
| La scorciatoia | Una scorciatoia personalizzata chiamata *Dropwheel* (Impostazioni → Tastiera), che esegue `dropwheel open` |
| L'avvio all'accesso | `~/.config/autostart/dropwheel.desktop` |
| Il font delle icone | `~/.local/share/dropwheel/fonts`, scaricato una volta da una versione fissa e controllato con il suo SHA-256 |

`dropwheel setup gnome --dry-run` mostra cosa cambierebbe, `--remove` toglie tutto, e
`~/.local/share/dropwheel/uninstall.sh` rimuove Dropwheel stesso.

## Uso

| Modo | Cosa fai |
|---|---|
| **Shift durante un trascinamento** | Inizia a trascinare dei file in File o sul desktop e tocca <kbd>Shift</kbd>: la ruota si apre sotto il puntatore. Spostati su un settore e rilascia |
| **Angolo attivo** | Trascina dei file nell'angolo in alto a destra, appena sotto la barra superiore: la ruota si apre lì accanto |
| **Scorciatoia** | Copia dei file in File (<kbd>Ctrl</kbd>+<kbd>C</kbd>), poi premi <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd>: la ruota si apre sul puntatore con quei file |
| **Menu di File** | Tasto destro sui file → *Script* → *Dropwheel* |
| **Riga di comando** | `dropwheel open photo.jpg`, `dropwheel run image.compress.balanced photo.jpg` |

La scorciatoia è la chiave `shortcut` di [`[trigger]`](CONFIG.it.md#trigger); dopo averla cambiata esegui di
nuovo `dropwheel setup gnome`. La [guida all'uso](USAGE.it.md) descrive la ruota.

## Differenze rispetto a Hyprland

- **Come viene riconosciuto un trascinamento.** GNOME non dice agli altri programmi che è in corso un
  trascinamento, quindi Dropwheel considera tale "Shift mentre il tasto del mouse è premuto", come su Windows.
  Anche Shift mentre selezioni del testo col mouse apre la ruota, che si richiude quando rilasci il tasto.
- **La finestra passa da XWayland**, così può posizionarsi e restare sopra le altre finestre.
- **Senza l'estensione** (prima del primo nuovo accesso, o se è spenta nell'app Estensioni) la ruota si apre
  comunque dalla scorciatoia, dal menu di File e dalla riga di comando, ma Shift durante un trascinamento non fa
  nulla.
- **Colori.** I colori di Caelestia non esistono su GNOME: l'interfaccia usa la sua tavolozza scura integrata.

## Risoluzione dei problemi

| Problema | Cosa fare |
|---|---|
| Non succede nulla | `dropwheel status` dice se Dropwheel è in esecuzione, `dropwheel doctor` cosa manca. Il log è `~/.cache/dropwheel/qtshell.log` |
| Shift durante un trascinamento non fa nulla | Esci e rientra una volta dopo l'installazione. Nell'app Estensioni, *Dropwheel* deve essere attiva |
| La scorciatoia non fa nulla | Un'altra azione potrebbe usarla: cambia `trigger.shortcut`, poi `dropwheel setup gnome` |
| Le icone appaiono come parole | Manca il font delle icone: esegui di nuovo `dropwheel setup gnome` (serve la rete una volta) |
| La ruota è sfocata, o nel posto sbagliato, con il ridimensionamento dello schermo | Segnalalo indicando il tuo ridimensionamento: quello frazionario non è ancora stato provato |

## Prossimi passi

1. Provare su veri computer con Debian e Ubuntu, con e senza ridimensionamento dello schermo.
2. Altri desktop, partendo dallo stesso host (KDE Plasma: [Dropwheel su KDE Plasma](KDE.it.md)).
3. Colori che seguono lo stile chiaro o scuro e il colore d'accento di GNOME.

I progressi sono nel [registro delle modifiche](../CHANGELOG.it.md).

---

← [Documentazione](README.it.md)
