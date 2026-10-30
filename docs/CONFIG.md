# Configurazione di Dropwheel

Questo documento descrive **tutte** le impostazioni di Dropwheel. Per l'uso generale vedi il
[README](../README.it.md).

- **File:** `~/.config/dropwheel/config.toml` (più precisamente
  `$XDG_CONFIG_HOME/dropwheel/config.toml`). La variabile d'ambiente `DROPWHEEL_CONFIG` permette
  di indicarne un altro.
- **Formato:** [TOML](https://toml.io/it/). Il file è facoltativo: ogni chiave mancante ha un
  valore predefinito.
- **File di partenza commentato** (italiano e inglese):

  ```sh
  dropwheel config --init
  ```

- **Quando vale:** la configurazione viene riletta a ogni apertura della ruota; di solito non
  serve riavviare. Se una modifica all'aspetto (per esempio l'angolo attivo) non si vede subito,
  riavvia con `dropwheel stop && dropwheel start`.
- **Errori:** un valore non valido (tipo sbagliato, fuori intervallo, parola sconosciuta) viene
  sostituito dal valore predefinito e segnalato con un avviso; un file TOML illeggibile fa usare
  tutti i valori predefiniti. Dropwheel non si blocca mai per colpa della configurazione.
- **Chiavi sconosciute** vengono conservate così come sono: le azioni possono leggere opzioni
  proprie aggiuntive.

> **Attenzione all'ordine in TOML.** Le chiavi «di primo livello» (`language`,
> `hidden_actions`) vanno scritte **prima** della prima sezione `[…]`. Una chiave scritta dopo
> `[share]`, per esempio, appartiene a `[share]` e non viene letta come impostazione generale.

## Indice

| Sezione | Contenuto |
|---|---|
| [primo livello](#primo-livello) | `language`, `hidden_actions` |
| [`[output]`](#output) | dove vengono salvati i risultati |
| [`[trigger]`](#trigger) | Shift durante il trascinamento |
| [`[hotzone]`](#hotzone) | l'angolo attivo |
| [`[wheel]`](#wheel) | aspetto e comportamento della ruota |
| [`[hud]`](#hud) | le schede dei lavori |
| [`[notifications]`](#notifications) | notifiche desktop |
| [`[jobs]`](#jobs) | lavori in parallelo |
| [`[image]`](#image) | qualità delle immagini e metadati |
| [`[video]`](#video) | codifica video |
| [`[audio]`](#audio) | codifica audio |
| [`[pdf]`](#pdf) | risoluzione e compressione dei PDF |
| [`[ocr]`](#ocr) | lingue del riconoscimento del testo |
| [`[whisper]`](#whisper) | modello per la trascrizione dell'audio |
| [`[apps]`](#apps) | programmi preferiti |
| [`[share]`](#share) | condivisione in rete locale con QR |
| [`[[custom]]`](#custom--azioni-personalizzate) | azioni personalizzate |
| [testi personalizzati](#testi-personalizzati) | cambiare le etichette della ruota |

---

## Primo livello

### `language`

| Tipo | Predefinito | Valori |
|---|---|---|
| stringa | `"auto"` | `"auto"`, `"it"`, `"en"` |

Lingua dell'interfaccia e dei nomi dei file prodotti (`foto (compresso).jpg` oppure
`foto (Compressed).jpg`). Con `"auto"` Dropwheel guarda `LC_ALL`, poi `LC_MESSAGES`, poi `LANG`:
se iniziano con `it` usa l'italiano, altrimenti l'inglese.

```toml
language = "it"
```

### `hidden_actions`

| Tipo | Predefinito |
|---|---|
| lista di stringhe | `[]` |

Azioni da togliere dalla ruota. Ogni elemento è l'identificativo di un'azione o un modello con
`*` e `?`. Gli identificativi sono stabili e hanno la forma `categoria.gruppo.azione`: li vedi
con `dropwheel menu --json FILE` (campo `id`). I gruppi rimasti vuoti spariscono da soli.

```toml
hidden_actions = [
  "image.background.*",      # per esempio tutto il gruppo Sfondo delle immagini
  "pdf.convert.epub",        # oppure una singola azione
]
```

---

## `[output]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `location` | stringa | `"source"` | `"source"`, `"folder"` |
| `folder` | stringa | `""` | un percorso; `~` e le variabili `$HOME` sono espanse |
| `open_after` | booleano | `false` | |

- **`location`**: `"source"` salva il risultato accanto all'originale; `"folder"` lo salva sempre
  in `folder`.
- **`folder`**: la cartella usata con `location = "folder"` e, in ogni caso, quando la cartella di
  origine non è scrivibile o è temporanea (download del browser, `/tmp`, file condivisi da app
  Flatpak). Vuota significa **Scaricati/Dropwheel** (la cartella Scaricati di
  `xdg-user-dirs`, per esempio `~/Scaricati/Dropwheel`).
- **`open_after`**: apre automaticamente il risultato con l'applicazione predefinita.

I nomi non sovrascrivono mai un file esistente (`foto 2.jpg`, `foto 3.jpg`, …) e non coincidono
mai con l'originale.

```toml
[output]
location = "folder"
folder = "~/Documenti/Convertiti"
open_after = true
```

---

## `[trigger]`

| Chiave | Tipo | Predefinito |
|---|---|---|
| `shift` | booleano | `true` |
| `dnd_check` | booleano | `true` |

- **`shift`**: `false` ignora Shift (restano l'angolo attivo, SUPER+ALT+D, il file manager e la
  riga di comando). Per non far arrivare proprio Shift a Dropwheel puoi anche svuotare
  `SHIFT_KEYS` nel modulo Lua (vedi il README).
- **`dnd_check`**: prima di aprire la ruota Dropwheel verifica con `hyprctl -j monitors` che un
  trascinamento sia davvero in corso, così Shift non apre la ruota mentre scrivi. Con `false` la
  ruota si apre a ogni Shift e si richiude da sola dopo 1,2 secondi se non arriva nessun
  trascinamento: utile solo per diagnosticare problemi.

---

## `[hotzone]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `enabled` | booleano | `true` | |
| `corner` | stringa | `"top-right"` | `"top-left"`, `"top-right"`, `"bottom-left"`, `"bottom-right"`, `"top"`, `"bottom"`, `"left"`, `"right"` |
| `size` | intero | `6` | da `1` a `64` (pixel) |

L'angolo attivo è una piccola area trasparente sempre presente sul bordo dello schermo.
Trascinandoci sopra dei file la ruota si apre lì vicino; rilasciandoli direttamente sull'area la
ruota si apre in modalità clic. Con un bordo (`"top"`, `"left"`, …) l'area è al centro di quel
bordo.

```toml
[hotzone]
corner = "bottom-left"
size = 10
```

---

## `[wheel]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `scale` | decimale | `1.0` | da `0.5` a `2.5` (consigliato da `0.8` a `1.5`) |
| `spring_ms` | intero | `450` | da `100` a `3000` (millisecondi) |
| `show_unavailable` | booleano | `true` | |
| `sound` | booleano | `false` | |

- **`scale`**: dimensione della ruota (a `1.0` il raggio esterno è 132 pixel).
- **`spring_ms`**: quanto restare su un gruppo, durante il trascinamento, perché si apra il suo
  anello; vale anche per il centro «Indietro».
- **`show_unavailable`**: `true` mostra in grigio le azioni il cui programma non è installato,
  con il comando per installarlo; `false` le nasconde.
- **`sound`**: un breve suono quando cambia il settore evidenziato.

---

## `[hud]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `position` | stringa | `"bottom-right"` | `"top-left"`, `"top-right"`, `"bottom-left"`, `"bottom-right"`, `"top"`, `"bottom"`, `"left"`, `"right"` |
| `timeout_ms` | intero | `7000` | da `0` a `600000`; `0` = non chiudere mai |

Posizione delle schede dei lavori e tempo dopo il quale una scheda **completata** si chiude da
sola (il conto si ferma mentre il puntatore è sopra la scheda). Le schede con un errore restano
finché non le chiudi.

---

## `[notifications]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `mode` | stringa | `"long"` | `"never"`, `"long"`, `"always"` |

Notifiche desktop (tramite `notify-send`, con le azioni Apri / Mostra nella cartella):
`"never"` mai, `"long"` per i lavori che durano più di 10 secondi o quando la scheda non è
visibile, `"always"` per ogni lavoro.

---

## `[jobs]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `max_parallel` | intero | `2` | da `1` a `16` |

Quanti lavori possono girare insieme; gli altri aspettano in coda («In coda» sulla scheda).
Video e documenti Office sono pesanti: alzalo solo se hai molti core.

---

## `[image]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `jpeg_quality` | intero | `85` | da `1` a `100` |
| `webp_quality` | intero | `80` | da `1` a `100` |
| `avif_quality` | intero | `60` | da `1` a `100` |
| `keep_metadata` | booleano | `true` | |

- Le qualità valgono per le **conversioni** in quel formato (le preimpostazioni di «Comprimi»
  hanno valori propri: Leggera ≈ 90, Bilanciata ≈ 80, Forte ≈ 65).
- **`keep_metadata`**: nelle conversioni conserva EXIF, XMP e profilo colore. La preimpostazione
  «Per il web» e le azioni del gruppo Privacy tolgono comunque la posizione GPS o tutti i
  metadati.

---

## `[video]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `crf` | intero | `23` | da `0` a `63`; più basso = qualità più alta, file più grande |
| `preset` | stringa | `"medium"` | `"ultrafast"`, `"superfast"`, `"veryfast"`, `"faster"`, `"fast"`, `"medium"`, `"slow"`, `"slower"`, `"veryslow"` |
| `hwaccel` | stringa | `"auto"` | `"auto"`, `"vaapi"`, `"nvenc"`, `"none"` |

- **`crf`** e **`preset`** valgono per le conversioni in MP4 H.264 (le preimpostazioni di
  «Comprimi» hanno valori propri). Per H.264 un CRF tra 18 e 28 è il campo utile; +6 dimezza
  circa la dimensione.
- **`hwaccel`**: codifica con la GPU. `"auto"` la usa solo se disponibile e affidabile, `"vaapi"`
  (Intel/AMD) e `"nvenc"` (NVIDIA) la forzano, `"none"` usa solo la CPU (più lenta, qualità
  migliore a parità di dimensione).

---

## `[audio]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `mp3_quality` | intero | `2` | da `0` (migliore) a `9` (peggiore), qualità VBR di LAME |
| `opus_bitrate` | stringa | `"128k"` | un numero seguito da `k`, per esempio `"96k"` |

---

## `[pdf]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `dpi` | intero | `150` | da `36` a `1200` |
| `compress` | stringa | `"ebook"` | `"screen"`, `"ebook"`, `"printer"`, `"prepress"`, `"default"` |

- **`dpi`**: risoluzione predefinita per esportare le pagine come immagini e per le anteprime ad
  alta qualità.
- **`compress`**: preimpostazione di Ghostscript usata da «Comprimi»: `"screen"` (72 dpi, file
  piccolissimi), `"ebook"` (150 dpi, buon compromesso), `"printer"` e `"prepress"` (non
  riducono la risoluzione delle immagini), `"default"` (il più vicino all'originale).

---

## `[ocr]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `languages` | stringa | `"ita+eng"` | codici di Tesseract uniti da `+` |

Le lingue devono essere installate: `tesseract-data-ita`, `tesseract-data-eng`,
`tesseract-data-deu`, … (`tesseract --list-langs` mostra quelle presenti).

```toml
[ocr]
languages = "ita+eng+deu"
```

---

## `[whisper]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `model` | stringa | `""` | percorso di un modello `ggml-*.bin` oppure un nome come `"base"` o `"small"` |

Modello di [whisper.cpp](https://github.com/ggml-org/whisper.cpp) usato da **Audio → Trascrivi**
e da **Video → Sottotitoli → Genera**. Vuoto significa: `~/.local/share/dropwheel/whisper/ggml-base.bin`,
altrimenti il primo modello `ggml-*.bin` trovato. Se non c'è alcun modello, la scheda del lavoro
mostra il comando per scaricarlo. È accettata anche la vecchia chiave `whisper_model` nella
sezione `[audio]`. La disinstallazione lascia i modelli al loro posto, salvo `uninstall.sh --purge`.

```toml
[whisper]
model = "~/.local/share/dropwheel/whisper/ggml-small.bin"
```

---

## `[apps]`

| Chiave | Tipo | Predefinito |
|---|---|---|
| `terminal` | stringa | `""` |
| `file_manager` | stringa | `""` |
| `image_editor` | stringa | `""` |
| `pdf_editor` | stringa | `""` |

Programmi da usare per «Apri un terminale qui», «Mostra nella cartella», per aprire
un'immagine in un editor e per **PDF → Annota**. Vuoto significa rilevamento automatico (per il
terminale, il primo installato fra foot, kitty, alacritty, wezterm, ghostty, konsole,
gnome-terminal, xfce4-terminal e xterm; per il file manager, quello predefinito del sistema; per
i PDF, Xournal++ e poi Okular). Indica il nome del comando. `file_manager` decide anche il formato
in cui **Copia** mette i file negli appunti, perché Thunar, Nautilus e Nemo incollano da
`x-special/gnome-copied-files` e Dolphin da `text/uri-list`.

```toml
[apps]
terminal = "foot"
file_manager = "thunar"
image_editor = "gimp"
pdf_editor = "okular"
```

---

## `[share]`

| Chiave | Tipo | Predefinito | Valori |
|---|---|---|---|
| `minutes` | intero | `15` | da `1` a `1440` |
| `port` | intero | `0` | da `0` a `65535`; `0` = porta casuale |
| `localsend_target` | stringa | `""` | alias o indirizzo IP del dispositivo LocalSend proposto di solito |

Impostazioni di **Condividi → QR per il telefono**: un server HTTP temporaneo nella rete locale
offre solo i file scelti, a un indirizzo casuale, e si spegne da solo dopo `minutes` minuti (o
quando chiudi la scheda). Imposta `port` solo se il firewall deve aprire una porta precisa.
`localsend_target` precompila il destinatario di **Condividi → LocalSend** (vuoto = da scrivere
ogni volta).

---

## `[[custom]]` — azioni personalizzate

Ogni blocco `[[custom]]` aggiunge un'azione alla ruota dei file delle categorie indicate.

| Chiave | Tipo | Obbligatoria | Predefinito | Descrizione |
|---|---|---|---|---|
| `id` | stringa | sì | | identificativo unico: lettere, cifre, `_`, `.`, `-` (per esempio `"blog-resize"`) |
| `label` | stringa | no | l'`id` | testo mostrato sul settore; brevi è meglio (≤ 12 caratteri) |
| `icon` | stringa | no | `"terminal"` | nome di un'icona [Material Symbols](https://fonts.google.com/icons) |
| `categories` | lista o stringa | no | `[]` = tutte | categorie di file a cui si applica (vedi sotto); `"*"` equivale a tutte |
| `command` | stringa o lista | sì | | il comando da eseguire, con i segnaposto |
| `multi` | stringa | no | `"each"` | come trattare più file (vedi sotto) |
| `output` | stringa | no | `"none"` | cosa mostrare alla fine (vedi sotto) |

### Segnaposto nel comando

| Segnaposto | Valore |
|---|---|
| `{file}` | percorso completo del file |
| `{files}` | tutti i file (con `multi = "combine"` o `"any"`), ognuno come argomento separato |
| `{dir}` | cartella che contiene il file |
| `{name}` | nome del file con l'estensione (`foto.jpg`) |
| `{stem}` | nome del file senza estensione (`foto` per `foto.jpg`) |
| `{ext}` | estensione in minuscolo, senza punto (`jpg`) |
| `{out}` | cartella dei risultati: accanto all'originale, `[output] folder` oppure `~/Scaricati/Dropwheel` |

I percorsi sono sempre assoluti, quindi un nome che comincia con un trattino non viene scambiato
per un'opzione. `{{` e `}}` producono le graffe letterali `{` e `}`.

Un comando scritto come **stringa** viene eseguito con `sh -c`: pipe (`|`), redirezioni (`>`) e
`&&` funzionano. I nomi dei file non diventano mai testo della shell: ogni segnaposto è sostituito
da un riferimento a una variabile d'ambiente, con le virgolette giuste per il punto in cui compare
(`{file}` diventa `"${DROPWHEEL_FILE}"`), quindi nomi con spazi, apostrofi, `$` o `;` sono sicuri
ovunque, anche dentro `"$(comando {file})"` e tra apici inversi. `${file}` con il dollaro resta una
normale variabile della shell. Dentro `$(( ))` si usano solo le cifre iniziali del valore.
Attenzione ai programmi che ricevono **codice** come argomento e lo interpretano di nuovo
(`sh -c '… {file}'`, `eval`, `python -c`, `ssh host "…"`): lì il nome verrebbe interpretato, quindi
usa direttamente le variabili, per esempio `sh -c 'cat "$DROPWHEEL_FILE"'`. Ogni comando riceve
nell'ambiente `DROPWHEEL_FILE`, `DROPWHEEL_DIR`, `DROPWHEEL_NAME`, `DROPWHEEL_STEM`, `DROPWHEEL_EXT`,
`DROPWHEEL_OUT`, `DROPWHEEL_LANG` e `DROPWHEEL_FILES` (un percorso per riga, omessa oltre i 128 KiB).
Un comando scritto come **lista** viene eseguito direttamente, senza shell: ogni elemento è un
argomento e un elemento uguale a `{files}` diventa un argomento per ogni file.
Una riga `NN%` sullo standard output aggiorna la barra di avanzamento della scheda.

### `multi`

| Valore | Significato |
|---|---|
| `"each"` | il comando viene eseguito una volta per ogni file (da 1 a N file) |
| `"single"` | l'azione compare solo quando trascini esattamente un file |
| `"combine"` | l'azione compare con almeno due file; il comando viene eseguito una volta con tutti (`{files}`) |
| `"any"` | una sola esecuzione con tutti i file, anche se è uno solo |

### `output`

| Valore | Cosa succede alla fine |
|---|---|
| `"none"` (o vuoto) | la scheda mostra solo l'esito (riuscito / errore con l'output d'errore del comando) |
| `"text"` | ciò che il comando scrive sullo standard output diventa un risultato di testo, con **Copia** ed **Espandi** |
| `"files"` | i file **nuovi** comparsi in `{dir}` (e in `{out}`, se diversa) durante l'esecuzione diventano il risultato: anteprima, trascinamento e **Apri**. **Annulla** non è disponibile: di un file nuovo si sa solo che è comparso mentre il comando era in esecuzione, e lo stesso vale per un download appena finito nella stessa cartella, che finirebbe nel cestino. |

Un comando che termina con un codice diverso da zero viene mostrato come errore, con la parte
finale del suo output d'errore nei dettagli. I comandi possono essere interrotti con la ✕ della
scheda.

### Categorie

`image`, `svg`, `pdf`, `document`, `spreadsheet`, `presentation`, `markdown`, `text`, `code`,
`data`, `ebook`, `subtitle`, `audio`, `video`, `archive`, `font`, `folder`, `url`, `appimage`,
`disk`, `package`, `unknown`.

La categoria dipende dal **contenuto** del file, non solo dall'estensione: un CSV è
`spreadsheet`, un JSON/YAML/TOML/XML è `data`, una GIF animata resta `image`.

### Esempi

```toml
# Ridimensiona per il blog (ImageMagick): crea "foto-blog.jpg" accanto all'originale.
[[custom]]
id = "blog-resize"
label = "Per il blog"
icon = "web"
categories = ["image"]
command = "magick {file} -resize '1600x1600>' -quality 80 {dir}/{stem}-blog.jpg"
multi = "each"
output = "files"

# Carica su un server con scp (una volta per file).
[[custom]]
id = "upload-server"
label = "Sul server"
icon = "cloud_upload"
categories = ["image", "pdf", "video"]
command = "scp {file} utente@server.lan:/srv/inbox/"

# Conta le parole di un PDF e mostra il risultato come testo (la stringa passa da sh -c: la pipe funziona).
[[custom]]
id = "pdf-words"
label = "Parole"
icon = "functions"
categories = "pdf"
multi = "single"
command = "pdftotext {file} - | wc -w"
output = "text"

# Apri tutti i file selezionati in GIMP, in un'unica finestra.
[[custom]]
id = "gimp"
label = "GIMP"
icon = "brush"
categories = ["image"]
command = "gimp {files}"
multi = "any"

# Accoda più video con mkvmerge in "unione.mkv" (la sintassi "[ file… ]" di mkvmerge
# unisce i file uno dopo l'altro).
[[custom]]
id = "mkv-join"
label = "Unisci MKV"
icon = "movie"
categories = ["video"]
multi = "combine"
command = "mkvmerge -o {dir}/unione.mkv [ {files} ]"
output = "files"

# Per qualsiasi file: calcola il BLAKE2 e lo mostra.
[[custom]]
id = "b2sum"
label = "BLAKE2"
icon = "fingerprint"
command = ["b2sum", "{file}"]
multi = "single"
output = "text"
```

Le azioni personalizzate con errori di configurazione (manca `id` o `command`, `id` duplicato,
`multi` sconosciuto, categoria inesistente) vengono ignorate o corrette con un avviso; le altre
continuano a funzionare.

---

## Testi personalizzati

Ogni testo della ruota, delle schede e delle finestre si può cambiare senza modificare Dropwheel:
crea un file JSON in `~/.config/dropwheel/locales/<lingua>/` (per esempio
`~/.config/dropwheel/locales/it/miei.json`) con le chiavi da sostituire:

```json
{
  "grp.background": "Sfondo",
  "sfx.compressed": "ridotto"
}
```

Le chiavi sono quelle dei file in `src/dropwheel/locales/it/` (per esempio `grp.*` per i
gruppi, `sfx.*` per i suffissi dei file prodotti, come «foto (ridotto).jpg»). I tuoi file vengono
letti dopo quelli di Dropwheel e hanno la precedenza.

---

## File completo con i valori predefiniti

```toml
language = "auto"
hidden_actions = []

[output]
location = "source"
folder = ""
open_after = false

[trigger]
shift = true
dnd_check = true

[hotzone]
enabled = true
corner = "top-right"
size = 6

[wheel]
scale = 1.0
spring_ms = 450
show_unavailable = true
sound = false

[hud]
position = "bottom-right"
timeout_ms = 7000

[notifications]
mode = "long"

[jobs]
max_parallel = 2

[image]
jpeg_quality = 85
webp_quality = 80
avif_quality = 60
keep_metadata = true

[video]
crf = 23
preset = "medium"
hwaccel = "auto"

[audio]
mp3_quality = 2
opus_bitrate = "128k"

[pdf]
dpi = 150
compress = "ebook"

[ocr]
languages = "ita+eng"

[whisper]
model = ""

[apps]
terminal = ""
file_manager = ""
image_editor = ""
pdf_editor = ""

[share]
minutes = 15
port = 0
localsend_target = ""
```

## Altri file di Dropwheel

| Percorso | Contenuto |
|---|---|
| `~/.config/dropwheel/config.toml` | questa configurazione |
| `~/.local/state/dropwheel/history.jsonl` | cronologia dei lavori, usata da **Annulla** |
| `~/.cache/dropwheel/` | anteprime, copie temporanee dei file, profili privati di LibreOffice |
| `~/.config/caelestia/dropwheel.lua` | il modulo Hyprland (scorciatoie, regole dei layer, avvio) |

I colori vengono da Caelestia (`~/.local/state/caelestia/scheme.json`) e la trasparenza da
`~/.config/caelestia/shell.json`: cambiando tema in Caelestia cambia anche Dropwheel, senza
configurare nulla.
