[Documentazione](README.it.md) · [English](INSTALL.md)

# Installare Dropwheel

## Per iniziare

**Requisiti**

| | |
|---|---|
| **Sistema** | Arch Linux o una derivata, su Wayland |
| **Hyprland** | 0.57 o successivo, con configurazione Lua |
| **Quickshell** | 0.3.2 o successivo (`quickshell` o `quickshell-git`) |
| **Python** | 3.11 o successivo, con Pillow |
| **Altri pacchetti** | `wl-clipboard`, `libnotify`, `ttf-material-symbols-variable`, `glib2`, `xdg-utils` |
| **Caelestia** | Consigliato, non obbligatorio |

**Installa come pacchetto Arch (consigliato)**

```sh
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
makepkg -si
```

Il `PKGBUILD` costruisce il pacchetto dalla cartella in cui ti trovi e pacman tiene traccia di ogni file.

**Oppure installa nella tua home, senza root**

```sh
git clone https://github.com/haxurus/dropwheel.git
cd dropwheel
./install.sh
```

Lo script installa in `~/.local`, controlla le dipendenze e stampa il comando per ciò che manca. Non esegue
mai `sudo`. Le opzioni sono in `./install.sh --help`.

**Poi, con il tuo utente normale, collegalo a Hyprland**

```sh
dropwheel setup hyprland   # Shift, SUPER+ALT+D, regole dei layer e avvio automatico
hyprctl reload
dropwheel start            # dal prossimo login parte da solo
dropwheel doctor           # mostra cosa è installato e cosa manca
```

Ora inizia a trascinare un file e tocca <kbd>Shift</kbd>.

<details>
<summary><b>Cosa cambia <code>dropwheel setup hyprland</code></b></summary>

<br>

Con Caelestia copia il modulo Lua in `~/.config/caelestia/dropwheel.lua` e, dopo averne fatto un backup,
aggiunge una riga a `~/.config/caelestia/hypr-user.lua`:

```lua
require("dropwheel")
```

Il modulo registra:

| Cosa | Dettagli |
|---|---|
| `Shift_L`, `Shift_R` | `hl.dsp.global("dropwheel:shift")`, `non_consuming` perché le applicazioni ricevano comunque Shift, `ignore_mods` perché funzioni anche tenendo Ctrl o Alt |
| `SUPER + ALT + D` | `hl.dsp.global("dropwheel:open")`, apre la ruota con i file negli appunti |
| Regole dei layer | Sfocatura e nessuna animazione di Hyprland su `dropwheel-wheel` e `dropwheel-hud`; nessuna animazione su `dropwheel-dialog` e `dropwheel-hotzone` |
| Avvio automatico | `hl.on("hyprland.start", ...)` esegue `dropwheel start` |

Le opzioni sono in cima al file copiato: la scorciatoia di apertura, i tasti Shift, l'avvio automatico e la
sfocatura.

Senza Caelestia, copia il modulo in `~/.config/hypr/` e aggiungi a `~/.config/hypr/hyprland.lua`:

```lua
package.path = package.path .. ";" .. os.getenv("HOME") .. "/.config/hypr/?.lua"
require("dropwheel")
```

</details>

**Aggiornare**

```sh
git pull
makepkg -sif               # oppure: ./install.sh
dropwheel stop && dropwheel start
```

La `f` è importante: la versione non cambia a ogni commit, quindi senza di essa makepkg reinstalla il
pacchetto costruito la volta precedente.

## Dipendenze facoltative

Solo i [requisiti](#per-iniziare) sono obbligatori. Tutto il resto abilita singole funzioni, e
`dropwheel doctor` mostra cosa manca. Dropwheel non esegue mai `sudo` al posto tuo.

Una buona base che copre la maggior parte delle funzioni:

```sh
sudo pacman -S --needed python-pillow-heif python-pillow-jpegxl-plugin oxipng pngquant jpegoptim \
    gifski perl-image-exiftool tesseract tesseract-data-ita tesseract-data-eng zbar python-qrcode \
    python-pymupdf python-pypdf qpdf ghostscript poppler libreoffice-fresh pandoc-cli typst \
    ffmpeg python-mutagen 7zip python-fonttools python-brotli
```

<details>
<summary><b>Elenco completo per funzione</b></summary>

<br>

**Repository ufficiali**

| Funzione | Pacchetti |
|---|---|
| HEIC e JPEG XL | `python-pillow-heif python-pillow-jpegxl-plugin` |
| Ottimizzazione delle immagini | `oxipng pngquant jpegoptim libjpeg-turbo` |
| GIF e animazioni | `gifski gifsicle libwebp-utils` |
| Formati insoliti, immagini enormi | `imagemagick libheif libvips` |
| SVG | `librsvg resvg potrace inkscape scour` o `svgo` |
| Metadati e privacy | `perl-image-exiftool mat2` |
| OCR | `tesseract tesseract-data-ita tesseract-data-eng`, più le tue lingue |
| Codici QR e a barre | `zbar python-qrcode qrencode` |
| Annotazioni | `satty` o `swappy`, `xournalpp` per i PDF |
| Ingrandimento con l'AI | `waifu2x-ncnn-vulkan` |
| PDF | `python-pymupdf python-pypdf python-pikepdf python-cryptography qpdf ghostscript poppler mupdf-tools img2pdf` |
| Documenti Office | `libreoffice-fresh` o `libreoffice-still`, `python-openpyxl python-docx` |
| Markdown, testo, codice | `pandoc-cli typst python-weasyprint python-pygments python-charset-normalizer` |
| Dati strutturati | `python-yaml python-tomli-w python-defusedxml` |
| Ebook | `calibre` |
| Audio e video | `ffmpeg python-mutagen` |
| Trascrizione e sottotitoli | `whisper-cpp` |
| Download dai link | `yt-dlp deno` |
| Archivi | `7zip unrar unarchiver` |
| Font | `python-fonttools python-brotli` |
| AppImage | `squashfs-tools` |
| Invio al telefono | `kdeconnect` |
| Sistema | `gnupg udisks2 trash-cli zenity gtk3 xdg-terminal-exec` |
| Selettore colore nelle finestre | `hyprpicker` |
| Stampa | `cups` |
| Checksum XXH128 | `xxhash` |
| Pagina web in PDF | `chromium` o un altro browser basato su Chromium |

**AUR**

| Pacchetto | Scopo |
|---|---|
| `ocrmypdf` | Rendere ricercabili i PDF scansionati |
| `python-pdf2docx` | Conversione da PDF a Word più fedele |
| `vtracer` | Vettorializzazione a colori |
| `realesrgan-ncnn-vulkan-bin` | Ingrandimento ×4 con Real-ESRGAN |
| `localsend-cli-bin` | Invio con LocalSend |

**pipx**

| Comando | Scopo |
|---|---|
| `pipx install "rembg[cpu,cli]"` | Rimozione dello sfondo; al primo uso scarica un modello AI |

</details>

## Disinstallazione

**Pacchetto di sistema**

```sh
sudo pacman -Rns dropwheel
```

Poi togli la riga `require("dropwheel")` da `~/.config/caelestia/hypr-user.lua`, cancella
`~/.config/caelestia/dropwheel.lua` ed esegui `hyprctl reload`. pacman non tocca la tua home, quindi
impostazioni, cronologia, cache e modelli Whisper restano finché non li cancelli.

**install.sh**

```sh
~/.local/share/dropwheel/uninstall.sh           # conserva impostazioni e dati
~/.local/share/dropwheel/uninstall.sh --purge   # cancella anche quelli, dopo una conferma
```

I file che hai creato con Dropwheel non vengono mai cancellati.

---

← [Documentazione](README.it.md) · [Successivo](USAGE.it.md) →
