[Documentazione](README.it.md) · [English](TROUBLESHOOTING.md)

# Risoluzione dei problemi

Inizia sempre con `dropwheel doctor`.

<details>
<summary><b>La ruota non compare quando premo Shift</b></summary>

<br>

1. **È in esecuzione?** `hyprctl globalshortcuts` deve elencare `dropwheel:shift` e `dropwheel:open`. Se
   non li elenca, esegui `dropwheel start` da un terminale e leggi gli eventuali errori.
2. **Le scorciatoie sono caricate?** `hyprctl binds | grep -i -B2 -A8 dropwheel` deve mostrarle. Se non
   compaiono, esegui `dropwheel setup hyprland` e poi `hyprctl reload`.
3. **Compare solo quando rilasci Shift?** È normale se hai scorciatoie che iniziano con Shift, perché
   Hyprland aspetta di capire se stai componendo una combinazione più lunga. Tocca Shift invece di tenerlo
   premuto.
4. **Stai trascinando da un'applicazione XWayland?** Il trascinamento da client X11 verso superfici Wayland
   non funziona in Hyprland 0.57. Controlla con `hyprctl clients` (`xwayland: 1`). Avvia l'applicazione in
   modalità Wayland, oppure copia i file e usa SUPER+ALT+D.
5. **Ancora niente?** Per prova, imposta `dnd_check = false` in `[trigger]` nel file `config.toml`. La ruota
   si apre allora a ogni Shift e si richiude da sola se non arriva nessun trascinamento.

</details>

<details>
<summary><b>Dove trovo i log?</b></summary>

<br>

```sh
qs log -p /usr/share/dropwheel/shell -f            # pacchetto di sistema
qs log -p ~/.local/share/dropwheel/shell -f        # install.sh
```

Il log della shell comprende gli errori del servizio Python. I comandi avviati da Hyprland mandano il loro
output in `/dev/null`, quindi esegui `dropwheel start` da un terminale per vedere gli errori di avvio.

</details>

<details>
<summary><b>Le icone sono quadratini o lettere</b></summary>

<br>

Manca il font delle icone. Esegui `sudo pacman -S ttf-material-symbols-variable`, poi `fc-cache -f` e infine
`dropwheel stop && dropwheel start`.

</details>

<details>
<summary><b>Una funzione è grigia</b></summary>

<br>

Il programma che le serve non è installato. Passa sopra il settore per vedere quale e il comando per
installarlo. Dropwheel ricontrolla ogni dieci minuti, oppure subito dopo `dropwheel stop && dropwheel start`.

</details>

<details>
<summary><b>Il risultato è finito in ~/Scaricati/Dropwheel</b></summary>

<br>

Succede quando l'originale si trova in un punto in sola lettura o temporaneo, come un download del browser in
corso, `/tmp` o un file condiviso da un'app Flatpak. Per usare sempre una sola cartella, imposta
`output.location = "folder"`.

</details>

<details>
<summary><b>Premendo Shift l'applicazione di origine sposta i miei file?</b></summary>

<br>

Hyprland inoltra Shift anche all'applicazione di origine, dove spesso significa «sposta». Dropwheel legge
sempre i file come copia e non li sposta né li cancella al rilascio. Se apri la ruota e poi rilasci in
un'altra applicazione, lascia prima Shift.

</details>

<details>
<summary><b>Il comando <code>dropwheel</code> non esiste</b></summary>

<br>

Con `install.sh` il comando è in `~/.local/bin`, che deve essere nel `PATH`. Su fish, esegui
`fish_add_path ~/.local/bin`. Su bash o zsh, aggiungi `export PATH="$HOME/.local/bin:$PATH"` al file di avvio
della shell. Hyprland e i file manager usano comunque il percorso completo.

</details>

<details>
<summary><b>Ho un file in <code>~/.config/quickshell/shell.qml</code></b></summary>

<br>

Con un `shell.qml` direttamente in `~/.config/quickshell/`, Quickshell non trova più le configurazioni nelle
sottocartelle. Dropwheel **non ne è toccato**, perché viene sempre avviato indicando il percorso. Non spostare
la sua shell in `~/.config/quickshell/`.

</details>

<details>
<summary><b>Le conversioni di documenti non producono nulla</b></summary>

<br>

LibreOffice in modalità invisibile non fa nulla, senza errori, se il suo profilo è già in uso. Dropwheel usa un
**profilo privato**, quindi non ne è toccato, ma la prima conversione è più lenta perché il profilo viene creato.
Se ancora non funziona, `soffice --version` deve rispondere e `dropwheel doctor` deve vedere `soffice`.

</details>

<details>
<summary><b>Alcuni file si aprono con Dropwheel</b></summary>

<br>

La voce di Dropwheel dichiara i tipi di file più comuni per comparire in «Apri con». Se uno di questi tipi non ha
un'applicazione predefinita, un programma potrebbe scegliere Dropwheel. Imposta quella giusta, per esempio:

```sh
xdg-mime default org.gnome.Loupe.desktop image/png
```

`install.sh` lo fa da solo per i tipi che avevano già un'applicazione predefinita.

</details>

<details>
<summary><b>Le azioni dal file manager non fanno nulla</b></summary>

<br>

Prova `dropwheel open ~/un/file.jpg` da un terminale. La comunicazione con la shell funziona solo sullo stesso
display Wayland, quindi il file manager deve avere `WAYLAND_DISPLAY` nel suo ambiente. È normale se è stato
avviato dalla sessione Hyprland.

</details>

<details>
<summary><b>Dropwheel si chiude da solo all'avvio</b></summary>

<br>

Il protocollo delle scorciatoie globali non ammette due client con lo stesso nome, quindi se parte una seconda
copia, una delle due viene chiusa. Controlla con `qs list`, poi esegui `dropwheel stop && dropwheel start`.

</details>

<details>
<summary><b>Un documento viene rifiutato perché troppo grande</b></summary>

<br>

Un documento Office, un EPUB o un CBZ può dichiarare parti enormi in un file minuscolo. Per proteggere la
memoria, Dropwheel non apre parti compresse in modo insolito (bzip2, LZMA) né legge parti di testo oltre i
64 MiB (256 MiB per il contenuto di un foglio ODS). **Comprimi** e **Rimuovi metadati** copiano comunque i video
incorporati, fino a 8 GiB in tutto, ed **Estrai immagini** salta le parti oltre i 64 MiB e dice quali.

</details>

---

← [Precedente](CLI.it.md) · [Successivo](PRIVACY.it.md) →
