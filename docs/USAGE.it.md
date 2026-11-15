[Documentazione](README.it.md) · [English](USAGE.md)

# Usare Dropwheel

## Come si usa

Ci sono cinque modi per aprire la ruota.

1. **Shift durante un trascinamento.** Inizia a trascinare, tocca <kbd>Shift</kbd>, spostati su un settore
   e rilascia. È il modo principale. Dropwheel controlla che un trascinamento sia davvero in corso, quindi
   Shift continua a funzionare normalmente mentre scrivi.
2. **L'angolo attivo.** Trascina i file sull'area di 6×6 pixel nell'angolo in alto a destra dello schermo.
   Angolo, dimensione e presenza stessa sono configurabili.
3. **SUPER+ALT+D.** Copia dei file nel file manager e premi la scorciatoia. Se negli appunti non c'è nulla,
   ottieni un'area vuota su cui trascinare i file.
4. **Il file manager.** Fai clic destro sui file e scegli **Apri in Dropwheel**.
5. **Il terminale.** Esegui `dropwheel open foto.heic`.

**Durante un trascinamento**

| Gesto | Effetto |
|---|---|
| Tocca <kbd>Shift</kbd> | Apre la ruota; se è già aperta, passa dalla pagina **Strumenti** alla pagina **File** e viceversa |
| Resta su un gruppo per 450 ms | Apre l'anello di quel gruppo |
| Resta al centro per 450 ms | Torna all'anello precedente |
| Rilascia su un'azione | La esegue, oppure apre il suo editor |
| Rilascia su un gruppo | Lascia la ruota aperta su quell'anello, in modalità clic |
| Rilascia al centro o fuori | Annulla |
| <kbd>Esc</kbd> | Annulla il trascinamento e chiude la ruota |

**In modalità clic** (dopo SUPER+ALT+D, l'angolo attivo, il file manager o un rilascio su un gruppo)

| Tasto o gesto | Effetto |
|---|---|
| Frecce, <kbd>Tab</kbd>, rotellina | Spostano l'evidenziazione |
| Da <kbd>1</kbd> a <kbd>8</kbd> | Esegue il settore N, in senso orario da mezzogiorno |
| <kbd>Invio</kbd> o clic | Esegue l'azione o apre il gruppo |
| <kbd>Alt</kbd>+<kbd>Invio</kbd> o clic destro | Imposta prima i parametri di un'azione rapida |
| <kbd>Backspace</kbd> o clic al centro | Torna indietro |
| <kbd>Esc</kbd> | Torna indietro; sull'anello principale, chiude |
| <kbd>Shift</kbd> o <kbd>Ctrl</kbd>+<kbd>Tab</kbd> | Cambia pagina |

**Schede dei lavori**

Dopo il rilascio compare una scheda nell'angolo in basso a destra. Durante il lavoro mostra l'avanzamento e
un pulsante per interrompere. Alla fine mostra un'anteprima, lo spazio risparmiato e i pulsanti **Apri**,
**Mostra nella cartella**, **Copia** e **Annulla**. Puoi anche **trascinare il risultato** direttamente in
un browser, una chat o un'e-mail. I lavori non riusciti restano finché non li chiudi, con i dettagli e, se
manca un programma, un pulsante che copia il comando di installazione.

## Configurazione

Non serve alcuna configurazione. Per partire da un file commentato in `~/.config/dropwheel/config.toml`:

```sh
dropwheel config --init
```

Le modifiche valgono alla successiva apertura della ruota. Un valore non valido torna a quello predefinito
con un avviso e non blocca mai nulla. Per esempio:

```toml
language = "it"                     # auto | it | en
hidden_actions = ["image.background.*"]

[output]
location = "source"                 # accanto all'originale; "folder" = sempre la cartella qui sotto
folder = "~/Scaricati/Dropwheel"

[hotzone]
corner = "bottom-left"

[image]
jpeg_quality = 82

# Le tue azioni, mostrate nella ruota accanto a quelle integrate
[[custom]]
id = "immagine-blog"
label = "Per il blog"
icon = "web"
categories = ["image"]
command = "magick {file} -resize '1600x1600>' -quality 80 {dir}/{stem}-blog.jpg"
output = "files"
```

Tutte le chiavi, con esempi di azioni personalizzate, sono in [docs/CONFIG.it.md](CONFIG.it.md).

## Integrazione con i file manager

| File manager | Come | Nel menu |
|---|---|---|
| **Thunar** (predefinito in Caelestia) | `dropwheel setup thunar --apply` | Apri in Dropwheel |
| **Nautilus** | `dropwheel setup nautilus` | Script, poi Dropwheel |
| **Dolphin** | Incluso nel pacchetto, oppure `dropwheel setup dolphin` | Apri in Dropwheel |
| **Nemo** | Incluso nel pacchetto, oppure `dropwheel setup nemo` | Apri in Dropwheel |

Caelestia gestisce il file `uca.xml` di Thunar, quindi Dropwheel lo modifica solo se passi `--apply`, e prima
ne fa un backup. I passaggi manuali sono in [integrations/thunar/README.md](../integrations/thunar/README.md).

---

← [Precedente](INSTALL.it.md) · [Successivo](FEATURES.it.md) →
