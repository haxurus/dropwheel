# Dropwheel in Thunar

Thunar (il file manager predefinito di Caelestia) non ha una cartella per le azioni
aggiuntive: legge le **azioni personalizzate da un solo file**, `~/.config/Thunar/uca.xml`.

Caelestia installa e tiene aggiornato proprio quel file (componente `thunar` dei dotfile, con
l'azione «Open Terminal Here»). Per questo Dropwheel **non lo modifica mai da solo** durante
l'installazione: se il file viene cambiato a mano, al prossimo `caelestia update` Caelestia
non lo sovrascrive ma crea `uca.xml.new` e segnala un conflitto. Unire l'azione è quindi una
scelta tua, da fare una volta sola.

## Opzione 1 — automatica

```sh
dropwheel setup thunar           # mostra cosa verrebbe aggiunto, senza toccare nulla
dropwheel setup thunar --apply   # aggiunge l'azione a ~/.config/Thunar/uca.xml (con backup)
```

Poi chiudi Thunar del tutto (`thunar -q`) e riaprilo.

## Opzione 2 — a mano dall'interfaccia

1. In Thunar apri **Modifica → Configura azioni personalizzate…**
2. Premi **+** e compila:
   - **Nome:** `Apri in Dropwheel`
   - **Comando:** `dropwheel open %F`
   - **Icona:** `dropwheel`
3. Nella scheda **Condizioni di visualizzazione**:
   - **Modello file:** `*`
   - spunta **tutte** le caselle: Cartelle, File audio, File immagine, File di testo,
     File video, Altri file.
4. Conferma con **OK**.

## Opzione 3 — a mano nel file

1. Chiudi Thunar: `thunar -q`
2. Fai una copia: `cp ~/.config/Thunar/uca.xml ~/.config/Thunar/uca.xml.bak`
3. Apri `~/.config/Thunar/uca.xml` e incolla l'elemento `<action>…</action>` di
   [`uca-action.xml`](uca-action.xml) **dentro** `<actions>`, prima di `</actions>`.
   Il file risultante ha questa forma:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <actions>
   <action>
   	<!-- … l'azione già presente (Open Terminal Here) … -->
   </action>
   <action>
   	<icon>dropwheel</icon>
   	<name>Open in Dropwheel</name>
   	<!-- … resto dell'azione di Dropwheel … -->
   </action>
   </actions>
   ```
4. Riapri Thunar.

## Se `dropwheel` non viene trovato

Con l'installazione utente (`install.sh`) il comando si trova in `~/.local/bin/dropwheel`.
Se Thunar è stato avviato con un `PATH` che non contiene `~/.local/bin`, nel campo
**Comando** usa il percorso completo, per esempio
`/home/<utente>/.local/bin/dropwheel open %F`.

## Aggiornamenti di Caelestia

Se dopo un `caelestia update` trovi `~/.config/Thunar/uca.xml.new`, confrontalo con il tuo
`uca.xml` (per esempio con `diff`), riporta nel tuo file le novità di Caelestia e cancella il
`.new`. L'azione di Dropwheel resta nel tuo `uca.xml`.
