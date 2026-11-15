[Documentazione](README.it.md) · [English](PRIVACY.md)

# Privacy e sicurezza

- **Tutto è locale.** Niente telemetria, servizi cloud o account.
- **La rete viene usata solo quando lo chiedi:**
  - *Il codice QR per il telefono* avvia un server HTTP temporaneo nella rete locale, su una porta e un
    indirizzo casuali, che offre solo i file scelti e si spegne dopo 15 minuti o quando chiudi la scheda.
  - *KDE Connect e LocalSend* inviano ai tuoi dispositivi associati.
  - *I link trascinati* vengono scaricati solo dal sito di quel link.
  - *I modelli AI* vengono scaricati solo quando installi o usi per la prima volta la funzione.
  - *Su Windows, macOS, GNOME e KDE Plasma* `dropwheel setup windows|macos|gnome|kde` scarica una volta il font delle icone, da una versione fissa su
    GitHub, e ne controlla lo SHA-256 prima di usarlo.
- **Su GNOME l'estensione della Shell di Dropwheel** risponde a una sola domanda sul bus della sessione: dove
  si trova il puntatore e quali tasti modificatori e tasti del mouse sono premuti in quel momento. Non registra
  e non invia nulla.
- **Su KDE Plasma lo script di KWin di Dropwheel** manda la posizione del puntatore a Dropwheel sul bus della
  sessione mentre il puntatore si muove (al massimo 50 volte al secondo), e lo chiama quando premi la sua
  scorciatoia. Non legge tasti. Su Wayland Dropwheel vede Shift grazie all'impostazione di KWin per il supporto
  delle applicazioni X11, che passa i tasti modificatori a ogni programma X11; Dropwheel legge solo se Shift è premuto.
- **Su Windows, macOS, GNOME e KDE Plasma la riga di comando parla con l'interfaccia solo su `127.0.0.1`**, con un token che cambia a
  ogni avvio ed è salvato nella tua cartella della cache. Dropwheel legge lo stato di Shift e dei tasti del
  mouse; non installa hook della tastiera e non registra tasti. Su macOS l'azione rapida del Finder e il
  LaunchAgent scritti da `dropwheel setup macos` si limitano ad avviare Dropwheel, e appunti e Cestino passano
  da macOS stesso (`pbcopy`, `osascript`), senza Apple Events verso altre app.
- **Nulla di ciò che trascini viene eseguito.** Macro dei documenti e JavaScript dei PDF non partono mai,
  un'AppImage viene ispezionata senza avviarla, e le immagini remote nel Markdown e i riferimenti esterni negli
  SVG non vengono scaricati.
- **I file non fidati sono trattati con prudenza.** Le voci pericolose degli archivi, come percorsi assoluti,
  `..` e link che puntano fuori dall'archivio, vengono rifiutate, e ci sono protezioni contro gli archivi e i
  documenti bomba.
- **Le password** restano in memoria solo per il lavoro che le usa.

Quando oscuri un'immagine, sfocatura e pixel possono essere reversibili sul testo. Per i dati sensibili usa un
riquadro pieno.

---

← [Precedente](TROUBLESHOOTING.it.md) · [Successivo](DEVELOPMENT.it.md) →
