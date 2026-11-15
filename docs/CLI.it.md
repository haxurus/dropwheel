[Documentazione](README.it.md) · [English](CLI.md)

# Riga di comando

```sh
dropwheel open ~/Immagini/foto.heic              # la ruota, in modalità clic
dropwheel menu --json documento.pdf              # le azioni disponibili, in JSON
dropwheel run image.compress.balanced foto.jpg   # esegue un'azione senza interfaccia
```

| Comando | Descrizione |
|---|---|
| `dropwheel start`, `stop`, `restart`, `status` | Controllano la shell e il servizio in background |
| `dropwheel open [FILE...]` | Apre la ruota per file o URL; senza argomenti usa gli appunti |
| `dropwheel menu FILE... [--json]` | Mostra le azioni disponibili per i file |
| `dropwheel actions [-c CATEGORIA] [--json]` | Elenca tutte le azioni e se sono disponibili |
| `dropwheel run AZIONE FILE... [-p CHIAVE=VALORE]... [--json]` | Esegue un'azione; l'id di un gruppo esegue la sua predefinita |
| `dropwheel undo [LAVORO]`, `history [-n N]` | Annulla un lavoro (di norma l'ultimo), mostra i lavori recenti |
| `dropwheel doctor [--json] [--full]` | Controlla Hyprland, Quickshell e gli strumenti, con i comandi di installazione |
| `dropwheel setup hyprland\|nautilus\|dolphin\|nemo\|thunar\|desktop\|all` | Installa un'integrazione |
| `dropwheel shares [--stop ID\|--stop-all]` | Elenca o interrompe le condivisioni attive in rete locale |
| `dropwheel config [--path\|--init\|--edit]` | Mostra, crea o modifica la configurazione |

I codici di uscita sono `0` per il successo, `1` per un errore, `2` per un uso sbagliato e `130` per
un'interruzione. `dropwheel setup` non sovrascrive mai un file senza prima salvarne una copia chiamata
`<file>.dropwheel-bak-AAAAMMGG-HHMMSS`.

| Variabile d'ambiente | Effetto |
|---|---|
| `DROPWHEEL_CONFIG` | Percorso alternativo di `config.toml` |
| `DROPWHEEL_DATA` | Cartella dei dati con la shell e le integrazioni, di norma `~/.local/share/dropwheel` o `/usr/share/dropwheel` |
| `DROPWHEEL_BACKEND` | Il comando del servizio usato dalla shell, impostato da `dropwheel start` |

---

← [Precedente](FEATURES.it.md) · [Successivo](TROUBLESHOOTING.it.md) →
