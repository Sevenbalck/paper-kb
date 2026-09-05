# paper-kb

Pipeline locale per lavorare con paper scientifici (PDF anche complessi, con tabelle
e figure) minimizzando i token spesi con Claude, e per costruire un knowledge graph
delle correlazioni tra i paper con [Graphify](https://github.com/Graphify-Labs/graphify).

Distribuito sotto licenza [MIT](LICENSE).

## Come funziona (in breve)

```
PDF ──Docling (locale)──> Markdown pulito ──Graphify extract──> knowledge graph
                                                                 (community detection,
                                                                  dedup nativo)

Domande sul grafo ──> Graphify query / Graphify path (locale, gratuito)
Riassunto di un paper ──> 1 chiamata Claude (on demand, cachata)
```

Le uniche chiamate a Claude sono: estrazione/aggiornamento del grafo Graphify (una
tantum per i paper nuovi, incrementale), e riassunto di un paper (1 per paper, on
demand, cachato). Il parsing PDF è locale e gratuito; le query sul grafo già costruito
sono locali e gratuite (navigazione del grafo, non una nuova chiamata a Claude).

**Le chiamate a Claude usano la tua sottoscrizione Pro/Max, non una API key**: passano
dal comando `claude -p` di [Claude Code](https://docs.claude.com/en/docs/claude-code/overview)
in modalità headless (pensata apposta per essere richiamata da script), autenticato con
il login del tuo account — non c'è fatturazione a consumo separata. Graphify stesso usa
lo stesso meccanismo (`--backend claude-cli`), non una API key separata.

> Nota: nell'aprile 2026 Anthropic aveva annunciato che dal 15 giugno l'uso "headless"
> sarebbe uscito dal pool dell'abbonamento per finire su un credito separato a tariffa
> API, ma la modifica è stata sospesa prima di entrare in vigore. Al momento l'uso
> headless attinge ancora ai limiti normali del piano Pro/Max, ma vale la pena
> ricontrollare su https://support.claude.com se qualcosa cambia in futuro.

---

## Setup (replicabile su qualsiasi computer)

Richiede Python 3.11+, Node.js (per Claude Code) e un abbonamento Claude Pro o Max attivo.

### 1. Componenti base — servono per CLI, dashboard Streamlit e frontend web

Un solo giro di installazione basta per **tutti e tre** i modi di usare il progetto
(`main.py`, `dashboard.py`, `backend.py`+frontend): nessuno di questi richiede passi
aggiuntivi oltre a questi.

```bash
# 1a. installa Claude Code e autenticati con la tua sottoscrizione Pro/Max
npm install -g @anthropic-ai/claude-code
claude login

# 1b. IMPORTANTE: assicurati che non ci sia una API key nell'ambiente,
#     altrimenti Claude Code la userebbe al posto dell'abbonamento
unset ANTHROPIC_API_KEY        # macOS/Linux
# oppure: $Env:ANTHROPIC_API_KEY = $null   # Windows PowerShell

# 1c. installa uv (gestore ambiente Python, una tantum sulla macchina)
curl -LsSf https://astral.sh/uv/install.sh | sh    # macOS/Linux
# oppure: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows

# 1d. entra nella cartella del progetto e sincronizza l'ambiente Python — installa in
#     un colpo solo TUTTE le dipendenze: Docling, FastAPI/uvicorn, Streamlit e Graphify
#     (come libreria per l'uso headless, non serve alcun passo separato per quello)
cd paper-kb
uv sync

# 1e. verifica che tutto funzioni
uv run python main.py check
```

`uv sync` legge `pyproject.toml` e `uv.lock`, garantendo le stesse versioni delle
dipendenze Python su ogni macchina su cui clonerai il progetto — Graphify incluso.
Solo Claude Code va installato/autenticato separatamente su ogni computer (`npm
install` + `claude login`), un paio di minuti, legato al tuo account non alla macchina.

Senza `uv`, in alternativa: `python -m venv .venv && source .venv/bin/activate && pip install -e .`

A questo punto hai già tutto il necessario per:
- **CLI**: `uv run python main.py <comando>` — vedi [Elenco comandi](#elenco-comandi)
- **Dashboard Streamlit**: `uv run streamlit run dashboard.py` — vedi [Dashboard interattiva](#dashboard-interattiva)
- **Frontend web**: avvia `backend.py`, poi apri un file in `frontend/` — vedi [Frontend web](#frontend-web-fastapi--html-statico)

### 2. Skill interattiva `/graphify` in Claude Code (opzionale, non serve per paper-kb)

Il comando `graphify` usato da questo progetto (extract/cluster-only/query/path,
invocato sempre in modalità **headless** da `src/graphify_kb.py` via subprocess) è già
installato al passo 1d come dipendenza normale — **non serve nient'altro per usare
paper-kb**, questo passo è indipendente e opzionale. Serve solo se vuoi *anche* poter
scrivere `/graphify` come skill interattiva dentro una sessione Claude Code qualsiasi,
fuori da questo progetto (per esplorazioni manuali su un knowledge graph a piacere):

```bash
uv tool install graphifyy
graphify install
```

Registra la skill **globalmente per il tuo utente** (non per il singolo progetto) — va
rifatto una volta per macchina se lavori su più computer, non per ogni progetto che usa
Graphify.

### Primo avvio: cosa aspettarsi

Al primo `main.py ingest` (o `main.py all`), Docling scarica i suoi modelli ML da
Hugging Face (qualche centinaio di MB). Con connessione media possono volerci **5-10
minuti la prima volta** — il terminale può sembrare fermo, non lo è. Da lì in poi i
modelli restano in cache locale e tutto è immediato.

**OCR disattivato di default** (`config.yaml`, `docling.ocr: false`): se i tuoi PDF sono
digitali (testo selezionabile, non scansioni/foto), l'OCR è puro overhead — Docling
inizializzerebbe comunque un modello separato (RapidOCR) e ci farebbe inferenza su ogni
pagina anche quando il testo è già estraibile direttamente. Disattivarlo è il guadagno di
velocità più consistente sull'ingest. Se in futuro aggiungi PDF scansionati o senza testo
incorporato, rimetti `docling.ocr: true`, altrimenti Docling non riuscirebbe a estrarne
il contenuto.

---

## Uso

```bash
# metti i tuoi PDF in papers/
cp ~/Downloads/qualche-paper.pdf papers/

# esegui tutta la pipeline (parsing + grafo Graphify)
uv run python main.py all
```

Se aggiungi nuovi PDF in seguito, ripeti solo `uv run python main.py all` — il manifest
(`data/manifest.json`) si ricorda quali PDF sono già stati parsati e salta il resto;
Graphify gestisce la propria incrementalità separatamente.

---

## Struttura del progetto

```
paper-kb/
├── papers/                  ← i tuoi PDF (input)
├── data/
│   ├── parsed/                ← markdown puliti prodotti da Docling — versionabili in git
│   │   └── graphify-out/        ← output di Graphify: graph.json, graph.html, GRAPH_REPORT.md
│   ├── summaries/               ← riassunti generati da Claude — versionabili
│   └── manifest.json              ← stato di avanzamento per paper, rende tutto idempotente
├── src/
│   ├── utils.py               ← config, manifest, hashing, chiamata a Claude Code headless
│   ├── ingest.py                ← PDF -> markdown (Docling)
│   ├── graphify_kb.py             ← integrazione con Graphify (extract/query/path)
│   └── summarize.py                 ← riassunto di un paper (1 chiamata Claude, cachata)
├── main.py                    ← CLI unico che orchestra tutto
├── dashboard.py                 ← dashboard web interattiva (Streamlit), stesse azioni della CLI
├── backend.py                     ← API REST (FastAPI) equivalente, per il frontend statico in frontend/
├── frontend/
│   ├── index.html                   ← frontend HTML/JS statico (nessuna build), collegato a backend.py
│   └── paper-kb-standalone.html       ← variante costruita con Claude Design/Artifacts, stesso backend
├── start_backend.bat              ← avvio rapido di backend.py su Windows (doppio click)
├── config.yaml                    ← tutti i parametri della pipeline in un posto solo
└── pyproject.toml                 ← dipendenze Python
```

---

## Elenco comandi

| Comando | Cosa fa | Chiama Claude? |
|---|---|---|
| `main.py check` | Verifica che `claude` sia installato e risponda (sottoscrizione Pro/Max) | Sì (1 chiamata di test) |
| `main.py ingest` | PDF → markdown pulito (Docling) | No |
| `main.py graphify-extract` | Costruisce/aggiorna il grafo Graphify da data/parsed/ | Sì (via `--backend claude-cli`, incrementale) |
| `main.py all` | Esegue `ingest` → `graphify-extract` in sequenza | Sì (solo in `graphify-extract`) |
| `main.py status` | Mostra a che stadio è ogni paper (parsed/summarized) | No |
| `main.py summarize <id>` | Riassunto strutturato di un paper (cachato) | Sì (1, poi cache) |
| `main.py graphify-query "<domanda>"` | Sottografo grezza (nodi/edge) dal grafo Graphify | No (locale) |
| `main.py graphify-ask "<domanda>"` | Risposta in prosa, sintetizzata da Claude sulla sottografo | Sì (2 per domanda: riformulazione + sintesi) |
| `main.py ask-fulltext "<domanda>"` | Risposta in prosa cercando nei markdown grezzi (non nel grafo) — vedi [Ricerca full-text](#ricerca-full-text-nei-paper-ask-fulltext) | Sì (2 per domanda: keyword + sintesi) |
| `main.py graphify-path <a> <b>` | Percorso più breve tra due concetti nel grafo | No (locale) |
| `main.py graphify-merge-dupes` | Fonde nodi duplicati su un grafo già costruito (già incluso in `graphify-extract`/`all`) | No (locale) |

Tutti questi comandi sono disponibili anche da interfaccia web — vedi la sezione
[Dashboard interattiva](#dashboard-interattiva) più sotto — **tranne `graphify-query` e
`graphify-ask`**: restano disponibili da CLI e da chiamata diretta all'API
(`POST /graphify/ask` in `backend.py`), ma non hanno più un pulsante in dashboard/frontend.
Sul corpus di questo progetto la ricerca full-text dava risposte più utili e i nodi del
grafo erano difficili da azzeccare come punto di partenza — vedi
[Ricerca full-text](#ricerca-full-text-nei-paper-ask-fulltext) per il motivo strutturale.

---

## Dashboard interattiva

In alternativa alla CLI, `dashboard.py` offre un'interfaccia web locale (Streamlit) con
le stesse azioni di `main.py`, su 4 tab: **Stato** (verifica setup, tabella paper,
eliminazione), **Pipeline** (upload PDF, esecuzione `all`/`ingest`/`graphify-extract`
con log in tempo reale), **Graphify** (costruzione grafo, [ricerca full-text nei
paper](#ricerca-full-text-nei-paper-ask-fulltext), percorso tra due concetti,
`graph.html`/`GRAPH_REPORT.md` incorporati nella pagina) e **Riassunti**. Non c'è un
pulsante per interrogare direttamente il grafo (`graphify-ask`/`graphify-query`): la
ricerca full-text ha dato risultati migliori su questo corpus, vedi la sezione
[Graphify](#graphify-grafo-delle-correlazioni-e-qa) per i dettagli.

```bash
uv run streamlit run dashboard.py
```

Si apre nel browser su `http://localhost:8501`. Non è un'interfaccia separata: chiama
direttamente le stesse funzioni Python usate da `main.py` (`ingest_all`,
`graphify_kb.run_extract/run_query/run_path`, `summarize.summarize`), quindi consuma
le stesse chiamate Claude e rispetta lo stesso manifest — è idempotente e sicuro
alternarla alla CLI sullo stesso progetto.

---

## Frontend web (FastAPI + HTML statico)

Terza alternativa alla CLI e alla dashboard Streamlit: `backend.py` espone le stesse
azioni come API REST, pensata per essere richiamata da un frontend HTML separato invece
che girare dentro Streamlit. Stessa logica di business, stesso manifest, stesse chiamate
Claude — è solo un altro modo di arrivarci, utile se vuoi un'interfaccia personalizzata
(es. costruita con Claude Design/Artifacts) invece del layout fisso di Streamlit.

### Avvio del backend

```bash
uv run uvicorn backend:app --reload --port 8000
```

Su Windows, in alternativa, doppio click su `start_backend.bat` (nella root del
progetto): apre un terminale con il backend attivo su `http://127.0.0.1:8000` — lascialo
aperto finché usi il frontend; `Ctrl+C` (o chiudi la finestra) per fermarlo. Documentazione
interattiva delle route su `http://127.0.0.1:8000/docs` (Swagger, generata da FastAPI).

Le azioni lunghe (`all`, `graphify-extract`) girano in background e trasmettono i log in
tempo reale via Server-Sent Events (`GET /jobs/{job_id}/stream`) — l'equivalente del box
"live output" della dashboard Streamlit. CORS aperto (`*`) per semplicità in locale; da
restringere se il frontend viene servito da un dominio diverso in produzione.

### Frontend

Nella cartella `frontend/` trovi **due file HTML statici indipendenti**, entrambi
collegati allo stesso `backend.py` — nessuna build, nessuna dipendenza esterna, si aprono
direttamente nel browser (doppio click, o `file://…/index.html`):

- **`index.html`** — frontend essenziale scritto a mano (HTML/CSS/JS vanilla), con le
  stesse 4 sezioni della dashboard Streamlit (Stato, Pipeline, Graphify, Riassunti) più
  una pagina di introduzione con un'illustrazione del grafo generata dai dati reali.
- **`paper-kb-standalone.html`** — variante costruita con Claude Design (Artifacts),
  esportata come pagina "bundlata" a sé stante (font e risorse incorporati, per questo
  pesa qualche MB); stessa logica di collegamento al backend dell'altro file. Si
  **aggiunge** a `index.html`, non lo sostituisce: sono due interfacce alternative sullo
  stesso `backend.py`, usa quella che preferisci.

Entrambi salvano l'URL del backend in `localStorage` (chiave `paperkb_api_base`,
default `http://127.0.0.1:8000`) — cambialo dal pannello impostazioni nell'interfaccia se
il backend gira su una porta o una macchina diversa. Il backend va sempre avviato
**prima** di aprire il frontend, altrimenti le richieste falliscono con "Failed to
fetch"/"non raggiungibile".

---

## Graphify (grafo delle correlazioni e Q&A)

Il grafo delle correlazioni tra paper e le domande in linguaggio naturale sono gestiti
interamente da [Graphify](https://github.com/Graphify-Labs/graphify): estrazione
semantica, community detection (Leiden), deduplicazione nativa delle entità (niente più
matching manuale per stringhe). Non esiste più un grafo "fatto in casa": `entities.py`,
`chunk.py`, `embed.py`, `query.py`, `graph.py`, `graph_query.py` sono stati rimossi
insieme a ChromaDB, sentence-transformers, networkx e pyvis come dipendenze — nessuno
di questi aveva più consumatori una volta spostata la logica di grafo/Q&A su Graphify.

> **Nota:** `onnxruntime` è stato reintrodotto come dipendenza esplicita (non lo è più
> "per caso" tramite ChromaDB, che la portava con sé). RapidOCR — l'OCR usato da Docling
> in `ingest` — ha `onnxruntime` come motore di default, molto più veloce di `torch` puro
> per l'inferenza su CPU; senza questa dipendenza esplicita, ripiega silenziosamente su
> `torch` e l'ingestion dei PDF diventa sensibilmente più lenta.

### Setup

`graphify` è incluso tra le dipendenze del progetto (`pyproject.toml`): `uv sync` lo
installa già, nessun passo separato. Va invocato sempre tramite `uv run` (es. `uv run
python main.py graphify-extract`, o dashboard avviata con `uv run streamlit`), così che
il comando venga trovato nel `.venv` del progetto.

Se preferisci comunque usare anche la modalità interattiva `/graphify` dentro Claude
Code (per esplorazioni manuali fuori da questo progetto) — vedi
[Setup, punto 2](#2-skill-interattiva-graphify-in-claude-code-opzionale-non-serve-per-paper-kb)
per come installarla: è un passo indipendente e opzionale, non necessario per il
funzionamento di questo progetto — `main.py` e la dashboard usano sempre e solo la
modalità headless (`--backend claude-cli`).

### Uso

```bash
python main.py graphify-extract              # costruisce/aggiorna il grafo (consuma la sottoscrizione Claude)
python main.py graphify-query "domanda"       # sottografo grezza: nodi/edge (locale, gratuito)
python main.py graphify-ask "domanda"         # risposta in prosa (2 chiamate Claude: riformulazione + sintesi)
python main.py graphify-path "PD-1" "MCT1"    # percorso più breve tra due concetti (locale, gratuito)
```

`graphify-query`/`graphify-ask` restano disponibili da CLI e da `POST /graphify/ask` in
`backend.py`, ma **non hanno più un pulsante in dashboard.py/frontend**: sul corpus di
questo progetto la [ricerca full-text](#ricerca-full-text-nei-paper-ask-fulltext) dava
risposte più utili sulle stesse domande, e i nodi del grafo erano difficili da azzeccare
come punto di partenza (serviva sfogliare la terminologia esatta prima di poter
formulare una domanda che agganciasse qualcosa). Il browser di nodi/community
(filtrabile per community o per testo) resta comunque disponibile in dashboard/frontend,
tab **Graphify** — utile per esplorare il grafo anche senza interrogarlo con domande, o
per usare `graphify-path` con i nomi esatti dei nodi.

`graphify-extract` esegue in realtà **due passi** in sequenza, entrambi con `--backend
claude-cli` (instrada tramite lo stesso `claude` CLI headless già usato da
`utils.call_claude_code()` — nessuna API key separata, consuma la tua sottoscrizione
Pro/Max):
1. `graphify extract data/parsed` — estrazione semantica, scrive `graph.json`
2. `graphify cluster-only data/parsed` — community detection e naming, scrive
   `GRAPH_REPORT.md` e `graph.html`

Il primo passo da solo produce solo il grafo grezzo, non la visualizzazione né il
report — serve sempre il secondo. **L'output finisce dentro la cartella scandita**, non
nella root del progetto: `data/parsed/graphify-out/{graph.json,graph.html,GRAPH_REPORT.md}`.
L'estrazione lavora su `data/parsed/` (i markdown già puliti da Docling), non sui PDF
grezzi in `papers/`, coerentemente con la filosofia del progetto di non rimandare mai a
Claude testo non ancora ripulito localmente.

`graphify-query` naviga il grafo già costruito e ritorna la **sottografo grezza**
(elenco di nodi/edge da una traversata BFS): locale, gratuito, nessuna chiamata Claude.
Non è una risposta in prosa — quella sintesi è normalmente compito dell'assistente AI
che legge questo output dentro `/graphify` in Claude Code; chiamato headless da uno
script (come facciamo qui), quel passaggio non avviene automaticamente. Per una vera
risposta discorsiva, `graphify-ask` fa **due** chiamate Claude: una piccola per
riformulare la domanda nel vocabolario inglese del grafo (i paper sono in inglese anche
se scrivi in italiano — senza questo passo, domande come "cosa sono i macrofagi?" non
trovano nodi anche se "Macrophages" esiste nel grafo; replica quello che la modalità
interattiva `/graphify` fa sempre prima di interrogare, documentato nel loro `skill.md`),
e una per sintetizzare la risposta finale dalla sottografo trovata. Se la riformulazione
non trova nulla, riprova automaticamente con la domanda originale prima di arrendersi.
🔴 **consuma 2 chiamate Claude per domanda**: il risparmio rispetto al vecchio sistema
RAG non è "zero chiamate", ma un contesto molto più compatto (poche righe di sottografo
pertinente invece di chunk di testo interi). `graphify-path` resta sempre locale e
gratuito (nessuna sintesi prevista, solo il percorso tra due nodi). Nessuno dei tre
accetta un percorso target — cercano `./graphify-out/graph.json` relativo alla cartella
da cui vengono lanciati — quindi `graphify_kb.py` li esegue sempre con la working
directory dentro `data/parsed/`.

`answer_question()` accoda sempre in fondo alla risposta un elenco **"Paper usati per
questa risposta"**, estratto con una regex dai campi `src=` della sottografo grezza —
non dipende da quanto Claude citi le fonti inline nella prosa (osservato inconsistente
nella pratica), è garantito corretto e completo indipendentemente dal modello.

`graphify_kb.py` genera automaticamente `data/parsed/.graphifyignore` (se non esiste già)
per escludere i `.meta.json` scritti da `ingest.py` accanto a ogni markdown: sono puri
metadati (paper_id/title/n_pages/hash), mai contenuto semantico — senza questa esclusione
Graphify li scansionerebbe comunque, trovando sempre zero nodi e sprecando una chiamata
Claude a ogni `extract` (il warning `"produced zero nodes"` non viene cachato tra le run).

### Fusione dei nodi duplicati

La "deduplicazione nativa" di Graphify opera **entro il singolo documento** processato in
una run di `extract`, non sull'intero corpus: lo stesso concetto estratto da paper diversi
può restare frammentato in nodi distinti — osservato empiricamente con label anche solo
diverse per punteggiatura/maiuscole, es. `"GLUT1 (Glucose Transporter)"` (da un paper) e
`"GLUT1 Glucose Transporter"` (da un altro), mai fusi da Graphify stesso.

`merge_duplicate_nodes()` in `src/graphify_kb.py` corregge questo caso a valle, sul
`graph.json` già costruito:

1. Normalizza ogni label (minuscolo, punteggiatura rimossa, spazi collassati) e raggruppa
   i nodi che normalizzano allo stesso testo.
2. Per ogni gruppo di duplicati, sceglie come sopravvissuto il nodo la cui **community è
   più popolata sull'intero grafo** (euristica: preferisce il cluster tematico più
   consolidato a uno frammentario) e vi rimappa tutti gli archi degli altri, scartando i
   self-loop generati dal merge e deduplicando archi identici.
3. Arricchisce il sopravvissuto con `source_files_merged` e `merged_from_ids`, per non
   perdere tracciabilità di quali nodi originali sono confluiti lì.
4. Scrive un backup **prima** di sovrascrivere: `graphify-out/graph.pre-merge.json`.

100% locale, nessuna chiamata Claude. Tre modi per eseguirlo:

- **Automatico**: `run_extract()` (quindi anche `main.py graphify-extract` e `main.py
  all`) lo esegue sempre come terzo passo, dopo `extract` e `cluster-only` — disattivabile
  passando `merge_dupes=False` (dashboard/frontend: checkbox "fondi automaticamente i nodi
  duplicati", spuntata di default).
- **Da CLI, da solo**: `main.py graphify-merge-dupes` — utile per rilanciare la fusione su
  un grafo costruito prima che questo step fosse integrato, senza rifare l'estrazione.
- **Da dashboard/frontend**: pulsante dedicato "Fondi nodi duplicati ora" nella tab
  Graphify, sotto la sezione di costruzione del grafo (mostra quanti gruppi sono stati
  fusi e il conteggio nodi/archi prima→dopo).

### Ricerca full-text nei paper (ask-fulltext)

`fulltext_qa.py` è un secondo canale di Q&A, indipendente da Graphify: non tocca mai
`graph.json`, non sa nemmeno che il grafo esiste. Cerca direttamente nei markdown grezzi
in `data/parsed/` (quelli prodotti da Docling, prima di Graphify):

1. Riformula la domanda in parole chiave inglesi (🔴 1 chiamata Claude).
2. Divide ogni markdown in paragrafi e li punteggia per corrispondenza di parole chiave
   — locale, gratuito. Bonus di punteggio se il paragrafo contiene anche un valore
   numerico con unità di misura (segnale forte per domande tipo "quale concentrazione").
3. Sintetizza una risposta in prosa **solo** dai paragrafi trovati, citando la fonte per
   ogni affermazione (🔴 1 chiamata Claude). Se non trova nessun paragrafo pertinente,
   lo dice esplicitamente invece di rispondere — Claude non viene nemmeno interpellato
   in quel caso.

**Perché esiste**: `graphify-ask` sintetizza da nodi/edge — un'astrazione del testo
prodotta da un'altra chiamata Claude durante `graphify-extract` — e proprio i dettagli
quantitativi (concentrazioni, dosi, N di pazienti) sono ciò che quell'estrazione tende a
perdere per prima. La ricerca full-text passa a Claude il testo verbatim del paper: più
vicino a "leggi e cita" che a "narra una storia plausibile partendo da etichette
astratte", quindi meno soggetto ad allucinazioni sui dettagli quantitativi — non
zero, però: i rischi residui sono keyword mal riformulate che pescano paragrafi veri ma
irrilevanti, e soprattutto **mescolare paper diversi** nella stessa risposta (due
condizioni sperimentali distinte presentate come se fossero la stessa). Per questo la
UI mostra un avviso quando i paragrafi trovati provengono da più di un paper, e un
checkbox "mostra paragrafi grezzi trovati" per verificare a colpo d'occhio cosa Claude
aveva davvero sotto mano.

In dashboard/frontend è gestita come una **chat con memoria**: ogni nuova domanda può
fare riferimento a quelle precedenti ("e nei topi invece?"), perché il client rimanda
per intero lo storico della conversazione a ogni richiesta (`POST /fulltext/ask`, campo
`history`). Per evitare che una conversazione lunga faccia crescere senza limite il
testo (e i token) mandati a Claude, `_format_history()` in `fulltext_qa.py` applica
sempre due tetti, configurabili in `config.yaml`:

```yaml
claude:
  fulltext_chat_max_turns: 6           # quanti turni recenti tenere come contesto
  fulltext_chat_max_answer_chars: 500  # quanti caratteri di ogni risposta passata mantenere
```

I turni più vecchi e la coda delle risposte più lunghe vengono scartati silenziosamente:
la cronologia serve solo a interpretare a cosa si riferisce la domanda nuova, non è mai
la fonte dei fatti (quella resta sempre il contesto di paragrafi della domanda corrente).

Disponibile da CLI (`main.py ask-fulltext "domanda"`), da dashboard/frontend (tab
Graphify) e da `POST /fulltext/ask` in `backend.py`.

### Eliminare un paper

La dashboard (tab Stato → 🗑️ Elimina un paper) e rimuove PDF, markdown parsato e
riassunto cachato, ma **non** aggiorna automaticamente il grafo Graphify — non esiste
un comando documentato per rimuovere un singolo documento dal grafo. Se serve che il
paper eliminato sparisca anche dal grafo, rilancia `graphify-extract` dopo
l'eliminazione (Graphify potrebbe richiedere una ricostruzione completa a seconda di
come gestisce la propria incrementalità).

---

## Problemi riscontrati durante l'installazione (Windows) e come sono stati risolti

Se hai clonato il progetto e qualcosa non va, controlla prima se rientra in uno di
questi casi — sono tutti già corretti nella versione attuale del codice, ma è utile
sapere cosa aspettarsi.

### 1. `[WinError 2] Impossibile trovare il file specificato` chiamando Claude

**Causa:** su Windows, `npm install -g` installa `claude` come script `claude.cmd`, non
come eseguibile nativo `.exe`. Python riesce a *trovarlo* nel PATH ma non a *lanciarlo*
direttamente con `subprocess.run(["claude", ...])`, perché un file `.cmd` va interpretato
da `cmd.exe`, non eseguito come processo autonomo.

**Fix:** in `src/utils.py`, `_resolve_claude_cmd()` rileva se il path risolto termina in
`.cmd`/`.bat` e in quel caso antepone `cmd /c` al comando. Su macOS/Linux il comportamento
resta invariato (esecuzione diretta).

### 2. `OSError: [WinError 1314] Il privilegio richiesto non appartiene al client` durante il download dei modelli

**Causa:** `huggingface_hub` (usato da Docling per scaricare i modelli) prova per
default a creare **symlink** nella cache locale. Su Windows questo richiede "Developer
Mode" attivo o privilegi da amministratore; senza, l'operazione fallisce con un errore
di permessi.

**Fix:** in `src/utils.py`, in cima al file, viene impostata `os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"`
prima di qualunque import di Docling. Si usano copie normali dei file invece dei
symlink — un po' più spazio su disco, ma funziona ovunque senza configurazione speciale.

### 3. `TypeError: Object of type method is not JSON serializable` durante il parsing

**Causa:** in una versione di Docling, `doc.num_pages` è un **metodo** e non un attributo
numerico diretto; il codice originale lo leggeva con `getattr()` senza chiamarlo, e il
metodo (non serializzabile in JSON) finiva nei metadati.

**Fix:** in `src/ingest.py`, prima di salvare i metadati si controlla `callable(n_pages)`
e in tal caso lo si invoca (`n_pages()`), con un fallback a `None` se anche questo fallisce.

### 4. `UnicodeEncodeError: 'charmap' codec can't encode character` chiamando Claude

**Causa:** su Windows, Python usa di default la code page locale del sistema (spesso
`cp1252`) per i flussi di I/O dei sottoprocessi, invece di UTF-8. I paper scientifici
contengono spesso simboli (β, µ, ≥, ecc.) che `cp1252` non sa rappresentare, causando un
crash quando il testo veniva scritto sullo stdin di Claude Code.

**Fix:** in `src/utils.py`, `call_claude_code()` passa esplicitamente `encoding="utf-8"`
(con `errors="replace"` come rete di sicurezza) a `subprocess.run()`, invece di affidarsi
alla codifica di default del sistema.

### 5. `--bare` di Claude Code rompe l'autenticazione da sottoscrizione

**Causa:** in un tentativo di isolare le chiamate headless da skill/CLAUDE.md locali
auto-scoperti, era stato aggiunto il flag `--bare` a `call_claude_code()`. Su
autenticazione via `claude login` (Pro/Max, non API key), questo produce l'errore
`"Not logged in · Please run /login"` — un bug noto di Claude Code, non specifico di
questo progetto: `--bare` salta anche il caricamento delle credenziali OAuth, non solo
skill/CLAUDE.md come documentato.

**Fix:** il flag è stato rimosso da `src/utils.py`. `call_claude_code()` non usa `--bare`.

---

## File di configurazione

Tutti i parametri (OCR on/off, modello Claude usato per il riassunto, soglia di
condensazione dei paper lunghi) sono in `config.yaml`. Il grafo/Q&A (Graphify) non è
configurato da qui: usa i propri parametri interni, invocato via CLI da `src/graphify_kb.py`.

## Portare il progetto su un altro computer

Versiona con git l'intera cartella **tranne** ciò che è già in `.gitignore`
(ambiente virtuale e i PDF originali, entrambi rigenerabili/personali). Sono invece
pensati per essere versionati — piccoli, testuali, e sono il "prodotto" delle chiamate
a Claude già fatte:

- `data/parsed/` — i markdown estratti dai PDF (non serve rifare Docling)
- `data/summaries/` — i riassunti già generati
- `data/manifest.json` — lo stato della pipeline, la rende idempotente
- `data/parsed/graphify-out/graph.json` — il grafo già costruito (se Graphify lo supporta in git;
  verifica la documentazione di Graphify per il merge driver anti-conflitti)

Sull'altro computer basta: `git clone`, `uv sync` (installa anche Graphify), `claude
login` (sottoscrizione Pro/Max). Se aggiungi nuovi PDF, `uv run python main.py
all` processerà solo quelli nuovi grazie al manifest.

### Senza git: zip portabile

Se non hai (ancora) un repository git, copia l'intera cartella `paper-kb/` **esclusi**
`.venv/`, `__pycache__/` e `.claude/` (tutti rigenerati automaticamente: il primo da `uv
sync`, gli altri due da Python/Claude Code al primo utilizzo). Tutto il resto — inclusi
`papers/` (i PDF originali, non rigenerabili), `data/parsed/`, `data/summaries/` e
`data/parsed/graphify-out/` (grafo già costruito) — va portato così com'è: evita di
rifare parsing e chiamate Claude già pagate sul nuovo computer.

Sul nuovo PC, estrai lo zip e ripeti dal punto 1 del [Setup](#setup-replicabile-su-qualsiasi-computer):
`npm install -g @anthropic-ai/claude-code`, `claude login`, installa `uv`, poi dentro la
cartella estratta `uv sync` e `uv run python main.py check` per verificare che sia tutto
a posto.
