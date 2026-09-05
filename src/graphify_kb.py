"""Integrazione con Graphify (https://github.com/Graphify-Labs/graphify): il knowledge
graph delle correlazioni tra paper e il meccanismo di query in linguaggio naturale di
questo progetto. È l'UNICO meccanismo di grafo/Q&A: entities.py, chunk.py, embed.py,
query.py, graph.py, graph_query.py (e le relative dipendenze — ChromaDB,
sentence-transformers, networkx, pyvis) sono stati rimossi perché non avevano più
consumatori una volta spostata questa logica su Graphify.

Prerequisito: il comando 'graphify' è incluso come dipendenza normale del
progetto (pyproject.toml), installato automaticamente da `uv sync` — non serve
più `uv tool install` separato. Va invocato sempre tramite `uv run` (o da uno
script/dashboard già avviato con `uv run`), così che il PATH includa il
.venv del progetto dove `graphify` è stato installato.

Backend: --backend claude-cli instrada l'estrazione tramite lo stesso 'claude'
CLI headless già usato da utils.call_claude_code() in questo progetto — niente
API key separata (Gemini/OpenAI/ecc.), consuma la tua sottoscrizione Pro/Max
esattamente come 'graphify-extract' consuma già per l'estrazione.

Target dell'estrazione: data/parsed/ (i markdown già puliti da Docling), NON
i PDF grezzi in papers/ — coerente con la filosofia del progetto di non
rimandare mai a Claude testo che è già stato ripulito localmente.

IMPORTANTE sui path di output (verificato con l'uso reale, non assunto dalla
doc): Graphify scrive SEMPRE `graphify-out/` DENTRO la cartella target passata
a 'extract' (qui: data/parsed/graphify-out/), non nella root del progetto.
Inoltre 'graphify extract' da solo produce solo graph.json — graph.html e
GRAPH_REPORT.md richiedono un passo successivo 'graphify cluster-only <target>'
(fatto automaticamente da run_extract() qui sotto). 'graphify query'/'path' non
accettano un target: cercano ./graphify-out/graph.json relativo alla cartella
da cui vengono lanciati, quindi vanno eseguiti con cwd dentro data/parsed/.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from utils import ROOT, load_config


def _resolve_graphify_cmd() -> str:
    path = shutil.which("graphify")
    if path is None:
        raise RuntimeError(
            "Comando 'graphify' non trovato nel PATH. È una dipendenza del "
            "progetto: lancia 'uv sync' per installarlo, e assicurati di "
            "eseguire questo script con 'uv run' (es. 'uv run python main.py "
            "graphify-extract' o 'uv run streamlit run dashboard.py'), non con "
            "un python/streamlit generico fuori dal venv del progetto."
        )
    return path


def _target_dir(target: Path | str | None = None) -> Path:
    """Cartella scandita da Graphify: data/parsed/ di default (i markdown già
    puliti da Docling). È anche la cartella dentro cui Graphify scrive il
    proprio graphify-out/ e da cui vanno lanciate query/path (vedi doc modulo)."""
    if target is not None:
        return Path(target)
    cfg = load_config()
    return Path(cfg["paths"]["parsed_dir"])


def graphify_out_dir(target: Path | str | None = None) -> Path:
    """Directory di output di Graphify: SEMPRE dentro la cartella target
    scandita (es. data/parsed/graphify-out/), non nella root del progetto."""
    return _target_dir(target) / "graphify-out"


def graph_html_path(target: Path | str | None = None) -> Path:
    return graphify_out_dir(target) / "graph.html"


def graph_json_path(target: Path | str | None = None) -> Path:
    return graphify_out_dir(target) / "graph.json"


def list_nodes(target: Path | str | None = None) -> list[dict]:
    """Legge graphify-out/graph.json (formato node-link di NetworkX:
    {"nodes": [...], "links"/"edges": [...]}, verificato dalla doc ufficiale
    di Graphify — ogni nodo ha id, label, community, source_file, file_type)
    e ritorna la lista di nodi con i campi principali normalizzati, per
    sfogliare la terminologia ESATTA del grafo invece di indovinarla nelle
    domande (es. "macrophages" vs "macrofagi" — vedi answer_question()).
    100% locale, nessuna chiamata Claude. Ritorna lista vuota se il grafo non
    esiste ancora o il JSON ha una struttura imprevista (nel dubbio non fa
    crashare la dashboard, semplicemente non mostra nulla).

    'merged_sources': popolato solo sui nodi sopravvissuti a
    merge_duplicate_nodes() (campo 'source_files_merged' scritto lì) — è il
    segnale più concreto di una correlazione TRA paper diversi (stesso
    concetto trovato in più documenti), a differenza della sola community
    (che può benissimo contenere nodi di un solo paper)."""
    graph_path = graph_json_path(target)
    if not graph_path.exists():
        return []
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    raw_nodes = data.get("nodes", [])
    if not isinstance(raw_nodes, list):
        return []

    nodes = []
    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        community = n.get("community")
        merged_sources = n.get("source_files_merged")
        nodes.append(
            {
                "label": n.get("label") or n.get("id") or "(senza nome)",
                # Sempre stringa: alcuni nodi hanno community come intero (non
                # ancora "nominata" dal passo di naming di cluster-only), altri
                # come stringa (nome friendly), altri assente — una colonna con
                # tipi misti (int/str/vuoto) manda in crash la conversione ad
                # Arrow di Streamlit (ArrowInvalid: "tried to convert to int64").
                "community": str(community) if community not in (None, "") else "",
                "source": n.get("source_file") or n.get("src") or "",
                "file_type": n.get("file_type") or n.get("type") or "",
                "merged_sources": merged_sources if isinstance(merged_sources, list) else [],
            }
        )
    return nodes


def graph_report_path(target: Path | str | None = None) -> Path:
    return graphify_out_dir(target) / "GRAPH_REPORT.md"


def _extract_report_section(report_text: str, heading_prefix: str) -> str:
    """Estrae il corpo di una sezione '## <heading_prefix>...' da GRAPH_REPORT.md,
    fino alla prossima intestazione di pari livello o alla fine del documento.
    Il prefisso (non il testo esatto dopo, es. il suffisso parentetico che
    Graphify aggiunge all'intestazione) evita di rompersi se quel dettaglio
    cambia tra versioni di Graphify."""
    # [^\n]* per il resto della riga di intestazione (mai DOTALL li', altrimenti
    # '.'  attraversa le righe e '$' finisce per ancorarsi molto piu' avanti nel
    # documento invece che a fine riga); [\s\S]*? per il corpo catturato, che
    # invece deve poter contenere newline.
    pattern = re.compile(
        rf"^## {re.escape(heading_prefix)}[^\n]*\n([\s\S]*?)(?=^## |\Z)",
        re.MULTILINE,
    )
    m = pattern.search(report_text)
    return m.group(1).strip() if m else ""


def report_highlights(target: Path | str | None = None) -> dict:
    """Estrae da GRAPH_REPORT.md le due sezioni più utili per individuare a
    colpo d'occhio le correlazioni TRA paper diversi, senza dover scorrere
    l'intero report (Corpus Check, Community Hubs, God Nodes, il dettaglio di
    tutte le community, Ambiguous Edges, Knowledge Gaps...):

    - 'surprising_connections': edge INFERRED/AMBIGUOUS tra concetti di paper
      diversi, con la fonte esplicita ('paperA.md -> paperB.md') — il segnale
      più diretto e concreto di correlazione cross-paper che il grafo offre,
      più immediato della sola colonna 'paper collegati' di list_nodes()
      (quella cattura solo label quasi identiche fuse da merge_duplicate_nodes()).
    - 'hyperedges': gruppi tematici multi-nodo, alcuni dei quali multi-paper.

    Stringhe vuote se il report non esiste ancora o non contiene quelle
    sezioni (Graphify le omette se non ha trovato nulla di rilevante)."""
    report_path = graph_report_path(target)
    if not report_path.exists():
        return {"surprising_connections": "", "hyperedges": ""}
    text = report_path.read_text(encoding="utf-8")
    return {
        "surprising_connections": _extract_report_section(text, "Surprising Connections"),
        "hyperedges": _extract_report_section(text, "Hyperedges"),
    }


def _normalize_label(label) -> str:
    """Normalizza un label per il confronto: case-insensitive, TUTTA la
    punteggiatura rimossa (parentesi, virgole, trattini, underscore, ecc.),
    spazi multipli collassati. Intercetta casi come 'GLUT1 (Glucose
    Transporter)' vs 'GLUT1 Glucose Transporter', che Graphify NON deduplica
    tra un paper e l'altro nonostante la 'deduplicazione nativa' dichiarata —
    osservato che opera solo entro il singolo documento processato in una
    run di extract, non globalmente sull'intero corpus."""
    s = str(label).strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def merge_duplicate_nodes(target: Path | str | None = None, backup: bool = True) -> dict:
    """Fonde nodi duplicati nel graph.json già costruito: quando lo stesso
    concetto appare come nodi distinti (stesso significato, label diverso
    solo per punteggiatura/maiuscole, provenienti da paper diversi), li
    unisce in un unico nodo e rimappa tutti gli archi verso il sopravvissuto.

    Per ogni gruppo di duplicati, il sopravvissuto è il nodo la cui community
    è più popolata sull'intero grafo (euristica: preferisce il cluster
    tematico più consolidato invece di uno frammentario — es. una community
    minuscola "nominata" come il nodo stesso è quasi sempre segno di scarsa
    numerosità). Il nodo sopravvissuto viene arricchito con
    'source_files_merged' e 'merged_from_ids' per non perdere tracciabilità.

    Scrive un backup del grafo pre-merge (graphify-out/graph.pre-merge.json)
    prima di sovrascrivere graph.json, a meno di backup=False. Locale,
    nessuna chiamata Claude. Ritorna un report dict con i dettagli di cosa è
    stato fuso (usato sia dalla CLI sia dalla dashboard per mostrare l'esito)."""
    graph_path = graph_json_path(target)
    if not graph_path.exists():
        print(f"Nessun graph.json trovato in {graph_path}: niente da fondere.")
        return {"merged_groups": 0, "nodes_before": 0, "nodes_after": 0, "details": []}

    data = json.loads(graph_path.read_text(encoding="utf-8"))
    raw_nodes = data.get("nodes", [])
    edges_key = "links" if "links" in data else "edges"
    raw_edges = data.get(edges_key, [])

    if not isinstance(raw_nodes, list):
        print("Struttura di graph.json inattesa: 'nodes' non è una lista. Merge saltato.")
        return {"merged_groups": 0, "nodes_before": 0, "nodes_after": 0, "details": []}

    groups = defaultdict(list)
    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        label = n.get("label") or n.get("id") or "(senza nome)"
        groups[_normalize_label(label)].append(n)

    dupe_groups = {k: v for k, v in groups.items() if len(v) > 1}

    report = {
        "nodes_before": len(raw_nodes),
        "edges_before": len(raw_edges),
        "merged_groups": len(dupe_groups),
        "nodes_after": len(raw_nodes),
        "edges_after": len(raw_edges),
        "details": [],
    }

    if not dupe_groups:
        print("Nessun nodo duplicato trovato: grafo già pulito, nulla da fondere.")
        return report

    # Community più popolata sull'intero grafo -> preferita come sopravvissuto
    community_size = Counter(str(n.get("community")) for n in raw_nodes if isinstance(n, dict))

    id_remap: dict = {}
    survivors = []
    for nodes in dupe_groups.values():
        nodes_sorted = sorted(
            nodes,
            key=lambda n: (-community_size[str(n.get("community"))], str(n.get("id"))),
        )
        primary = dict(nodes_sorted[0])
        others = nodes_sorted[1:]
        primary_id = primary.get("id")

        merged_sources = {primary.get("source_file")} | {o.get("source_file") for o in others}
        merged_sources.discard(None)
        primary["source_files_merged"] = sorted(merged_sources)
        primary["merged_from_ids"] = [primary_id] + [o.get("id") for o in others]

        for o in others:
            id_remap[o.get("id")] = primary_id
        survivors.append(primary)

        report["details"].append(
            {
                "label": primary.get("label"),
                "survivor_id": primary_id,
                "survivor_community": primary.get("community"),
                "absorbed": [
                    {"id": o.get("id"), "label": o.get("label"), "community": o.get("community")}
                    for o in others
                ],
            }
        )

    duplicate_original_ids = {n.get("id") for group in dupe_groups.values() for n in group}
    new_nodes = [n for n in raw_nodes if n.get("id") not in duplicate_original_ids]
    new_nodes.extend(survivors)

    # Rimappa gli archi verso i sopravvissuti; scarta i self-loop generati dal
    # merge e deduplica archi identici (trattando il grafo come non diretto)
    new_edges = []
    seen_edges = set()
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        src = e.get("source", e.get("from"))
        tgt = e.get("target", e.get("to"))
        new_src = id_remap.get(src, src)
        new_tgt = id_remap.get(tgt, tgt)
        if new_src == new_tgt:
            continue
        edge_type = e.get("type") or e.get("label") or ""
        key, key_rev = (new_src, new_tgt, edge_type), (new_tgt, new_src, edge_type)
        if key in seen_edges or key_rev in seen_edges:
            continue
        seen_edges.add(key)
        new_e = dict(e)
        if "source" in new_e:
            new_e["source"] = new_src
        if "target" in new_e:
            new_e["target"] = new_tgt
        if "from" in new_e:
            new_e["from"] = new_src
        if "to" in new_e:
            new_e["to"] = new_tgt
        new_edges.append(new_e)

    if backup:
        backup_path = graph_path.with_name(graph_path.stem + ".pre-merge" + graph_path.suffix)
        backup_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Backup pre-merge salvato in: {backup_path}")

    data["nodes"] = new_nodes
    data[edges_key] = new_edges
    graph_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    report["nodes_after"] = len(new_nodes)
    report["edges_after"] = len(new_edges)

    print(f"Fusi {len(dupe_groups)} gruppi di nodi duplicati:")
    for d in report["details"]:
        absorbed_ids = [a["id"] for a in d["absorbed"]]
        print(f"  '{d['label']}': sopravvissuto {d['survivor_id']} "
              f"(community {d['survivor_community']}) <- assorbiti {absorbed_ids}")
    print(f"Nodi: {report['nodes_before']} -> {report['nodes_after']}  "
          f"Archi: {report['edges_before']} -> {report['edges_after']}")

    return report


def _stream_subprocess(cmd: list[str], cwd: Path) -> int:
    """Esegue cmd stampando l'output riga per riga (via print, così
    run_with_live_output/run_multistage nella dashboard lo catturano).
    Ritorna il codice di uscita."""
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    for line in process.stdout:
        print(line, end="")
    process.wait()
    return process.returncode


def run_extract(
    target: Path | str | None = None,
    backend: str = "claude-cli",
    merge_dupes: bool = True,
) -> None:
    """Costruisce/aggiorna il grafo Graphify in tre passi:
    1) 'graphify extract' — estrazione semantica, scrive graph.json.
       🔴 consuma la sottoscrizione Claude (stesso meccanismo di
       call_claude_code(), ma via 'graphify' invece che diretto).
    2) 'graphify cluster-only' — community detection + naming, scrive
       GRAPH_REPORT.md e graph.html. Anche questo passo può chiamare Claude
       per dare un nome leggibile alle community rilevate, quindi passiamo
       lo stesso --backend per coerenza.
    3) merge_duplicate_nodes() — locale, nessuna chiamata Claude. Fonde i
       nodi che rappresentano lo stesso concetto ma sono rimasti separati
       perché estratti da paper diversi (la 'deduplicazione nativa' di
       Graphify opera entro il singolo documento, non sull'intero corpus —
       osservato empiricamente: due nodi con label IDENTICO ma source_file
       diverso restano comunque distinti). Disattivabile con
       merge_dupes=False se si preferisce ispezionare il grafo grezzo prima.

    Il subcomando 'extract' da solo produce SOLO graph.json (verificato con
    l'uso reale): senza il passo 2 non si vede alcun grafo/report."""
    target_dir = _target_dir(target)
    if not target_dir.exists() or not any(target_dir.glob("*.md")):
        print(f"Nessun markdown trovato in {target_dir}. Esegui prima 'ingest' nella pipeline.")
        return

    _ensure_graphifyignore(target_dir)

    print(f"=== graphify extract ({target_dir}) ===")
    cmd_extract = [_resolve_graphify_cmd(), "extract", str(target_dir), "--backend", backend]
    rc = _stream_subprocess(cmd_extract, cwd=ROOT)
    if rc != 0:
        raise RuntimeError(f"'graphify extract' ha restituito exit {rc}")

    print(f"\n=== graphify cluster-only ({target_dir}) — genera report e visualizzazione ===")
    cmd_cluster = [_resolve_graphify_cmd(), "cluster-only", str(target_dir), "--backend", backend]
    rc = _stream_subprocess(cmd_cluster, cwd=ROOT)
    if rc != 0:
        raise RuntimeError(f"'graphify cluster-only' ha restituito exit {rc}")

    if merge_dupes:
        print(f"\n=== fusione nodi duplicati ({target_dir}) — locale, gratuito ===")
        merge_duplicate_nodes(target=target_dir)


def _ensure_graphifyignore(target_dir: Path) -> None:
    """Scrive (se non esiste già) un .graphifyignore nella cartella target che
    esclude i *.meta.json scritti da ingest.py accanto a ogni markdown: sono
    puri metadati (paper_id/title/n_pages/hash), mai contenuto semantico, e
    Graphify li scansionerebbe comunque provando a estrarne nodi — trovando
    sempre zero risultati e sprecando una chiamata Claude a ogni 'extract',
    dato che gli 'empty' non restano in cache tra le run."""
    ignore_path = target_dir / ".graphifyignore"
    if ignore_path.exists():
        return
    ignore_path.write_text(
        "# Generato automaticamente da graphify_kb.py: i .meta.json sono puri\n"
        "# metadati (paper_id/title/n_pages/hash) scritti da ingest.py, mai\n"
        "# contenuto semantico — escluderli evita chiamate Claude sprecate su\n"
        "# file che produrrebbero comunque zero nodi.\n"
        "*.meta.json\n",
        encoding="utf-8",
    )


def run_query(question: str, target: Path | str | None = None) -> str:
    """Interroga il grafo Graphify già costruito e ritorna la sottografo GREZZA
    (nodi/edge da una traversata BFS), non una risposta in prosa: la sintesi
    in linguaggio naturale è normalmente compito dell'assistente AI che legge
    questo output (dentro /graphify in Claude Code) — chiamato da qui, via
    subprocess, quel passaggio non esiste. Locale/gratuito, nessuna chiamata
    Claude. Per una risposta discorsiva vera e propria vedi answer_question().
    'graphify query' non accetta un target: cerca ./graphify-out/graph.json
    relativo alla cwd, quindi va lanciato con cwd dentro data/parsed/."""
    target_dir = _target_dir(target)
    cmd = [_resolve_graphify_cmd(), "query", question]
    result = subprocess.run(
        cmd, cwd=target_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"'graphify query' ha restituito exit {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


_ANSWER_SYSTEM_PROMPT = """Rispondi alla domanda dell'utente usando SOLO le informazioni nel
contesto fornito, che consiste in una sottografo (nodi ed edge) estratta dal knowledge graph
di Graphify a partire dai paper scientifici della libreria. Se il contesto non contiene la
risposta, dillo chiaramente invece di inventare.

REGOLA CRITICA sulle citazioni: cita la fonte (valore 'src=' del nodo) SUBITO DOPO ogni
singola affermazione a cui si riferisce, non una sola volta riassuntiva alla fine — la
sottografo mescola spesso nodi provenienti da paper diversi nella stessa risposta, e una
citazione unica in fondo non permette di sapere quale claim viene da quale paper.

Esempio CORRETTO (citazione per singola affermazione):
"PD-1 è espresso dai Treg in tumori altamente glicolitici (cancercell2022). Questo si lega
concettualmente al metabolismo lipidico dei macrofagi intestinali (spectralflow-metabolism-supp)."

Esempio SBAGLIATO (una citazione unica, generica, in fondo — NON fare così):
"PD-1 è espresso dai Treg... si lega al metabolismo dei macrofagi... (dati da spectralflow-metabolism-supp)"

Se un singolo paragrafo combina affermazioni da più paper, spezzalo in frasi più corte
così ogni citazione resta vicina al claim specifico a cui si riferisce. Rispondi in
italiano, in prosa discorsiva, non come lista di nodi/edge."""


_REFORMULATE_SYSTEM_PROMPT = """Riformula la domanda dell'utente in una breve query di ricerca
in INGLESE, adatta a trovare nodi in un knowledge graph le cui etichette sono quasi sempre in
inglese (i paper scientifici della libreria sono in inglese, anche se le domande arrivano in
italiano). Usa termini tecnici brevi (1-4 parole), non una frase completa — es. "macrophages",
non "cosa sono i macrofagi". Rispondi SOLO con la query riformulata, nient'altro: niente
spiegazioni, niente virgolette, niente punteggiatura finale."""


def _reformulate_for_graph(question: str, model: str) -> str:
    """Riformula la domanda nel vocabolario (inglese, termini brevi) del grafo
    prima di interrogarlo. Replica un passo che la modalità interattiva
    /graphify fa sempre prima della traversata (documentato nel loro
    skill.md: "expand the question against the graph's own vocabulary so a
    wording mismatch does not collapse the answer to noise") — passo che il
    semplice subprocess 'graphify query' headless non fa da solo, ed è la
    causa più probabile di ricerche che non trovano nodi pur esistendo (es.
    "macrofagi" in una domanda italiana non aggancia "Macrophages" nei nodi).
    🔴 consuma una piccola chiamata Claude aggiuntiva."""
    from utils import call_claude_code

    reformulated = call_claude_code(
        instruction=f"DOMANDA ORIGINALE: {question}",
        system_prompt=_REFORMULATE_SYSTEM_PROMPT,
        model=model,
    )
    reformulated = reformulated.strip().strip('"').strip("'")
    return reformulated or question


def answer_question(question: str, target: Path | str | None = None, model: str | None = None) -> str:
    """Risposta in prosa a una domanda, sintetizzata da Claude a partire dalla
    sottografo restituita da run_query(). 🔴 consuma 2 chiamate Claude per
    domanda: una piccola per riformulare la domanda nel vocabolario del grafo
    (_reformulate_for_graph, vedi perché sopra) e una per sintetizzare la
    risposta finale — è il prezzo per una risposta leggibile e robusta al
    mismatch linguistico, invece del dump grezzo di nodi/edge; il risparmio
    di token di Graphify sta nel mandare a Claude solo la sottografo
    pertinente (poche righe) invece di file interi, non nell'eliminare le
    chiamate stesse.

    L'elenco "Paper usati" in fondo alla risposta NON dipende da quanto Claude
    abbia citato inline (osservato inconsistente: a volte una citazione unica
    generica, a volte nessuna) — viene estratto con una regex direttamente
    dai campi 'src=' della sottografo grezza, quindi è sempre corretto e
    completo indipendentemente dall'aderenza del modello al prompt."""
    import re

    from utils import call_claude_code, load_config

    cfg = load_config()
    resolved_model = model or cfg["claude"]["model_summary"]

    search_query = _reformulate_for_graph(question, resolved_model)
    subgraph = run_query(search_query, target=target)
    if not subgraph.strip() and search_query != question:
        # la riformulazione potrebbe aver peggiorato le cose in casi limite:
        # riprova con la domanda originale prima di arrenderti
        subgraph = run_query(question, target=target)
    if not subgraph.strip():
        return (
            "Il grafo non ha restituito nessun nodo pertinente a questa domanda "
            f"(provata anche la riformulazione: {search_query!r})."
        )

    answer = call_claude_code(
        instruction=f"DOMANDA (rispondi in italiano a questa): {question}",
        input_text=f"SOTTOGRAFO estratta dal knowledge graph:\n{subgraph}",
        system_prompt=_ANSWER_SYSTEM_PROMPT,
        model=resolved_model,
    )

    # Fonti estratte deterministicamente dai campi 'src=' della sottografo
    # grezza (non dalla risposta di Claude): garantite corrette e complete a
    # prescindere da quanto il modello abbia citato inline.
    sources = sorted({
        re.sub(r"\.md$", "", src) for src in re.findall(r"\bsrc=([^\s\]]+)", subgraph)
    })
    if sources:
        answer += "\n\n**Paper usati per questa risposta:** " + ", ".join(sources)
    return answer


def run_path(node_a: str, node_b: str, target: Path | str | None = None) -> str:
    """Traccia il percorso più breve tra due nodi del grafo Graphify. Locale/gratuito.
    Stesso discorso di run_query() sulla cwd."""
    target_dir = _target_dir(target)
    cmd = [_resolve_graphify_cmd(), "path", node_a, node_b]
    result = subprocess.run(
        cmd, cwd=target_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"'graphify path' ha restituito exit {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(
            "Uso: python graphify_kb.py extract | query \"<domanda>\" | "
            "path <a> <b> | merge-dupes"
        )
    elif sys.argv[1] == "extract":
        run_extract()
    elif sys.argv[1] == "query" and len(sys.argv) > 2:
        print(run_query(sys.argv[2]))
    elif sys.argv[1] == "path" and len(sys.argv) > 3:
        print(run_path(sys.argv[2], sys.argv[3]))
    elif sys.argv[1] == "merge-dupes":
        merge_duplicate_nodes()
    else:
        print("Argomenti non validi.")
