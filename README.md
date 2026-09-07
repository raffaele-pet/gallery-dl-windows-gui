# Gallery-DL Windows GUI

Interfaccia grafica non ufficiale per [gallery-dl](https://codeberg.org/mikf/gallery-dl), pensata per scaricare immagini, gallerie, raccolte, profili e contenuti supportati su Windows senza usare manualmente il terminale.

L’interfaccia riprende il flusso del progetto [yt-dlp Windows GUI](https://github.com/raffaele-pet/yt-dlp-windows-gui) e usa la palette di [gallery-dl.com](https://gallery-dl.com/): blu/viola profondo, bianco e verde lime.

## Funzionalità

- uno o più URL nella stessa operazione;
- file di input, con possibilità di commentare o rimuovere gli URL completati;
- download normale, simulazione, estrazione URL, JSON, informazioni extractor e keyword;
- destinazione, struttura delle cartelle e formato dei nomi personalizzabili;
- cookie direttamente da Chrome, Edge, Firefox, Brave e Opera;
- cookie da file, esportazione cookie, username/password e `.netrc`;
- proxy HTTP/SOCKS, User-Agent, IPv4/IPv6, retry, timeout e limiti di velocità;
- intervalli e filtri per file, post e child extractor;
- filtri per data, dimensione, categoria e tag;
- archivio SQLite anti-duplicati;
- metadati JSON, `info.json`, tag, ZIP/CBZ e conversione Pixiv Ugoira;
- postprocessor, comandi per-file/finali e template `--Print`;
- configurazioni JSON/YAML/TOML e editor JSON integrato;
- opzioni arbitrarie `KEY=VALUE` e argomenti CLI aggiuntivi per coprire anche opzioni specifiche dei singoli siti e funzionalità future;
- elenco degli extractor/moduli, stato e manutenzione della cache;
- log, avanzamento, annullamento sicuro e verifica dei file creati;
- aggiornamento stabile o versione di sviluppo dall’interfaccia.

## Requisiti

- Windows 10 o Windows 11;
- connessione Internet;
- [WinGet](https://learn.microsoft.com/windows/package-manager/winget/), normalmente incluso tramite **App Installer**.

Python, `gallery-dl`, `yt-dlp`, le dipendenze opzionali principali e FFmpeg vengono installati o aggiornati automaticamente.

## Installazione

1. Scarica la repository come ZIP ed estraila.
2. Fai doppio clic su `INSTALL.bat`.
3. Attendi la verifica finale.
4. Avvia **Gallery-DL** dal collegamento creato sul Desktop oppure tramite `RUN.vbs`.

L’installer crea un ambiente Python isolato nella cartella `.venv`, quindi non modifica i pacchetti degli altri progetti. Ogni nuova esecuzione di `INSTALL.bat` controlla e aggiorna i componenti all’ultima versione disponibile.

Controllo senza modifiche:

```bat
INSTALL.bat --check
```

## Uso rapido

1. Incolla un URL nella scheda **Download**.
2. Scegli la destinazione.
3. Per siti che richiedono accesso, apri **Accesso e rete** e scegli il browser nel quale hai già effettuato il login.
4. Premi **Avvia**.

Per Instagram, ad esempio, imposta `chrome`, `edge` o `firefox` nel campo **Cookie dal browser**. Il browser potrebbe dover essere chiuso durante la lettura del database dei cookie.

Il pulsante **Anteprima comando** mostra esattamente gli argomenti che verranno passati a `gallery-dl`, senza eseguirli.

## Configurazione completa

La scheda **Avanzate** offre tre livelli:

1. controlli grafici per le opzioni più usate;
2. righe `KEY=VALUE`, convertite in opzioni `--option`;
3. argomenti CLI aggiuntivi, interpretati con le regole di quoting di Windows.

L’editor integrato usa per impostazione predefinita:

```text
%APPDATA%\gallery-dl\config.json
```

La documentazione completa delle opzioni è disponibile sul [sito della documentazione ufficiale](https://gdl-org.github.io/docs/).

## Struttura

```text
app.py                         interfaccia e orchestrazione gallery-dl
assets/gallery-dl-logo.png     logo dell’applicazione
INSTALL.bat                    installazione, aggiornamento e verifica
RUN.vbs                        avvio senza finestra del terminale
RUN_DEBUG.cmd                  avvio diagnostico
tests/test_app.py              test della costruzione dei comandi
```

## Note legali e di sicurezza

Usa il programma soltanto per contenuti che hai il diritto di scaricare e nel rispetto dei termini dei servizi interessati. Le credenziali non vengono salvate nelle preferenze della GUI. I comandi personalizzati `--exec` e gli argomenti avanzati vengono eseguiti soltanto se inseriti esplicitamente dall’utente.

Questo progetto non è affiliato né approvato dal progetto ufficiale gallery-dl o dai siti supportati.

## Licenza

[MIT](LICENSE)
