"""
Dashboard interattiva per paper-kb.

Avvio (dalla root del progetto, dove sta anche main.py):
    uv run streamlit run dashboard.py

Il grafo delle correlazioni tra paper e le query in linguaggio naturale sono
gestiti interamente da Graphify (comando esterno 'graphify', non incluso tra
le dipendenze del progetto: incluso in pyproject.toml, installato da uv sync).
Non esistono più un
grafo "fatto in casa" né un sistema di Q&A via RAG/ChromaDB: sono stati
rimossi insieme a chunk.py, embed.py, entities.py, graph.py, graph_query.py,
query.py, che non hanno più consumatori nella pipeline.

Si importano direttamente i moduli in src/ (utils, ingest, summarize,
graphify_kb) e si chiamano le loro funzioni Python — niente subprocess/CLI
per queste, a differenza di 'graphify' stesso (comando esterno, chiamato via
subprocess da graphify_kb.py).

Ogni azione della dashboard replica esattamente il comando corrispondente in
main.py:
  - check            -> utils.check_claude_code() + call_claude_code("...pronto")
  - status           -> stessa lista di stage ["parsed","summarized"] dal manifest
  - all              -> ingest_all() + graphify_kb.run_extract(), stesso ordine di main.py
  - summarize        -> summarize.summarize()
  - graphify-extract -> graphify_kb.run_extract() (extract + cluster-only)
  - ask-fulltext      -> fulltext_qa.answer_question_fulltext() (unico canale di Q&A
                         della dashboard: ricerca full-text su data/parsed/, 2 chiamate
                         Claude). graphify-ask/graphify-query restano disponibili da CLI
                         (main.py) e da backend.py, ma non hanno più un pulsante qui: sul
                         corpus attuale il full-text dava risposte più utili e i nodi del
                         grafo erano difficili da azzeccare come punto di partenza.
  - graphify-path     -> graphify_kb.run_path() (locale)
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import streamlit as st

# --- aggancio ai moduli reali del progetto (src/) -------------------------
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import graphify_kb as graphify_mod
    import ingest as ingest_mod
    import summarize as summarize_mod
    import fulltext_qa as fulltext_mod
    import utils
except ImportError as e:
    st.error(
        f"Import fallito ({e}).\n\n"
        f"Questo file assume che i moduli stiano in `{SRC}` (struttura del "
        f"README: paper-kb/src/*.py) e che `dashboard.py` sia nella root del "
        f"progetto, accanto a main.py. Se la tua struttura è diversa, "
        f"aggiusta il percorso `SRC` in cima al file."
    )
    st.stop()


st.set_page_config(page_title="paper-kb", page_icon="📚", layout="wide")


# --- helper: cattura live dei print() nelle funzioni di pipeline ----------
class _LivePrinter(io.StringIO):
    """StringIO che aggiorna un placeholder Streamlit a ogni write(),
    per mostrare i print() delle funzioni di pipeline mentre girano.
    Nota: le barre di progresso tqdm vanno di default su stderr, quindi
    qui non compaiono: si vedono solo i print() veri e propri.

    contextlib.redirect_stdout() reindirizza sys.stdout per l'intero processo,
    non solo per il thread corrente. Se l'utente interrompe con Ctrl+C proprio
    mentre siamo dentro quel reindirizzamento (es. bloccati in lettura da un
    subprocess), il gestore di shutdown interno di Streamlit può provare a
    scrivere "Stopping..." su sys.stdout PRIMA che il blocco 'with' faccia in
    tempo a ripristinarlo — finendo per chiamare .code() da un contesto senza
    una sessione Streamlit attiva (NoSessionContext), con una cascata di
    eccezioni che non c'entra nulla con la pipeline stessa. La try/except qui
    sotto la rende innocua: il contenuto resta comunque nel buffer."""

    def __init__(self, placeholder):
        super().__init__()
        self.placeholder = placeholder

    def write(self, s):
        n = super().write(s)
        content = self.getvalue().strip()
        if content:
            try:
                self.placeholder.code(content, language="text")
            except Exception:
                pass
        return n

    def flush(self):
        pass


def run_with_live_output(fn, *args, **kwargs):
    """Esegue fn(*args, **kwargs) catturando i suoi print() in un box live.
    Ritorna (risultato, errore) — errore è None se tutto ok."""
    ph = st.empty()
    printer = _LivePrinter(ph)
    try:
        with redirect_stdout(printer):
            result = fn(*args, **kwargs)
        return result, None
    except Exception as e:  # noqa: BLE001 - vogliamo mostrare qualunque errore in UI
        return None, e


def run_multistage(substeps, progress_bar, status_ph, log_ph):
    """Esegue in sequenza una lista di (nome_fase, funzione), aggiornando una
    progress bar a step discreti (una tacca per fase completata, NON
    proporzionale al tempo reale) e un log continuo unico su tutte le fasi.
    Ritorna (successo: bool, nome_fase_fallita_o_None, errore_o_None)."""
    printer = _LivePrinter(log_ph)
    n = len(substeps)

    for i, (name, fn) in enumerate(substeps):
        status_ph.markdown(f"**[{i + 1}/{n}] {name}...**")
        printer.write(f"\n=== {name} ===\n")
        try:
            with redirect_stdout(printer):
                fn()
        except Exception as e:  # noqa: BLE001
            printer.write(f"\n!! ERRORE in '{name}': {e}\n")
            return False, name, e
        progress_bar.progress((i + 1) / n)

    status_ph.markdown("**Completato ✅**")
    return True, None, None


# --- stato / manifest ------------------------------------------------------
@st.cache_data(ttl=5)
def load_status_rows(_cfg):
    manifest = utils.Manifest(_cfg["paths"]["manifest"])
    rows = []
    for pid, entry in sorted(manifest.data.items()):
        meta_path = _cfg["paths"]["parsed_dir"] / f"{pid}.meta.json"
        title = pid
        if meta_path.exists():
            try:
                title = json.loads(meta_path.read_text(encoding="utf-8")).get("title", pid)
            except Exception:
                pass
        rows.append(
            {
                "id": pid,
                "titolo": title,
                "parsed": "OK" if "parsed" in entry else "-",
                "summarized": "OK" if "summarized" in entry else "-",
            }
        )
    return rows


def do_check() -> str:
    """Identico a `main.py check`."""
    utils.check_claude_code()
    return utils.call_claude_code("Rispondi solo con la parola: pronto")


def do_all():
    """Identico a `main.py all`."""
    ingest_mod.ingest_all()
    graphify_mod.run_extract()


def do_delete_paper(paper_id: str) -> list[str]:
    """Rimuove un paper dalla knowledge base locale: PDF sorgente in papers/,
    markdown+meta in data/parsed/, riassunto cachato, e la sua voce nel
    manifest. NON tocca il grafo Graphify: Graphify gestisce il proprio stato
    incrementale e non offre un comando documentato per rimuovere un singolo
    documento — se serve, va ricostruito da capo con 'graphify-extract' dopo
    aver ripulito data/parsed/."""
    cfg = utils.load_config()
    removed: list[str] = []

    for pdf in cfg["paths"]["papers_dir"].glob("*.pdf"):
        if utils.slugify(pdf.stem) == paper_id:
            pdf.unlink()
            removed.append(str(pdf))

    for suffix in (".md", ".meta.json"):
        p = cfg["paths"]["parsed_dir"] / f"{paper_id}{suffix}"
        if p.exists():
            p.unlink()
            removed.append(str(p))

    summ_file = cfg["paths"]["summaries_dir"] / f"{paper_id}.md"
    if summ_file.exists():
        summ_file.unlink()
        removed.append(str(summ_file))

    manifest = utils.Manifest(cfg["paths"]["manifest"])
    if paper_id in manifest.data:
        del manifest.data[paper_id]
        manifest.save()
        removed.append("voce nel manifest")

    for r in removed:
        print(f"  -> rimosso: {r}")
    if not removed:
        print(f"Nessun file trovato per '{paper_id}' (già rimosso?).")
    print(
        "\nNota: il grafo Graphify non è stato aggiornato automaticamente — "
        "se vuoi che il paper eliminato sparisca anche da lì, rilancia "
        "'graphify-extract' dopo questa operazione."
    )
    return removed


PIPELINE_STEPS = [
    (
        "all",
        "all — pipeline completa",
        "Esegue in ordine ingest → graphify-extract. È il comando da usare più "
        "spesso: grazie al manifest 'ingest' salta da solo i PDF già processati "
        "(Graphify gestisce la propria incrementalità separatamente). "
        "🔴 consuma token Claude nella fase Graphify extract.",
        "multi",
        [
            ("Ingest (PDF → markdown)", ingest_mod.ingest_all),
            ("Graphify extract 🔴 chiama Claude", graphify_mod.run_extract),
        ],
    ),
    (
        "ingest",
        "ingest",
        "PDF → markdown pulito (Docling). Solo i PDF nuovi o modificati vengono "
        "processati. 100% locale, nessuna chiamata Claude.",
        "single",
        ingest_mod.ingest_all,
    ),
    (
        "graphify-extract",
        "graphify-extract 🔴 consuma token",
        "Costruisce/aggiorna il grafo Graphify a partire da data/parsed/. 🔴 "
        "consuma la sottoscrizione Claude (--backend claude-cli). Incluso in "
        "uv sync, nessuna installazione separata.",
        "single",
        graphify_mod.run_extract,
    ),
]


cfg = utils.load_config()

st.title("📚 paper-kb — dashboard")
st.caption(
    "Interfaccia locale sopra src/. Grafo e query in linguaggio naturale sono "
    "gestiti da Graphify. Le chiamate a Claude restano quelle già previste "
    "dalla pipeline (Graphify extract, 1 cachata per summarize)."
)

tab_status, tab_pipeline, tab_graphify, tab_summary = st.tabs(
    ["Stato", "Pipeline", "Graphify", "Riassunti"]
)

# ---------------------------------------------------------------- Stato ----
with tab_status:
    st.subheader("Stato della libreria")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔍 Verifica setup Claude Code (check)"):
            with st.spinner("Chiamata di test a Claude..."):
                reply, err = None, None
                try:
                    reply = do_check()
                except Exception as e:  # noqa: BLE001
                    err = e
            if err:
                st.error(f"Errore: {err}")
            else:
                st.success(f"Claude Code OK, ha risposto: {reply!r}")
    with col2:
        if st.button("🔄 Aggiorna tabella stato"):
            st.cache_data.clear()

    st.divider()
    rows = load_status_rows(cfg)
    if rows:
        st.write(f"**{len(rows)} paper** nel manifest ({cfg['paths']['manifest']}):")
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info(
            "Nessun paper nel manifest ancora. Aggiungi dei PDF nella tab "
            "**Pipeline** e lancia `all`."
        )

    st.divider()
    st.subheader("🗑️ Elimina un paper")
    st.caption(
        "Rimuove il paper da papers/, data/parsed/, data/summaries/ e il "
        "manifest. NON tocca il grafo Graphify — se vuoi che il paper sparisca "
        "anche da lì, rilancia 'graphify-extract' dopo. Operazione irreversibile."
    )
    paper_ids_for_delete = sorted(utils.Manifest(cfg["paths"]["manifest"]).data.keys())
    if paper_ids_for_delete:
        to_delete = st.selectbox("Paper da eliminare", paper_ids_for_delete, key="delete_select")
        confirm_delete = st.checkbox(
            f"Confermo: voglio eliminare definitivamente '{to_delete}' e i suoi dati derivati"
        )
        if st.button("🗑️ Elimina definitivamente", disabled=not confirm_delete):
            with st.spinner(f"Eliminazione di '{to_delete}'..."):
                _, err = run_with_live_output(do_delete_paper, to_delete)
            if err:
                st.error(f"Errore durante l'eliminazione: {err}")
            else:
                st.success(f"'{to_delete}' eliminato dai dati locali.")
                st.cache_data.clear()
    else:
        st.info("Nessun paper da eliminare.")

# ------------------------------------------------------------- Pipeline ----
with tab_pipeline:
    st.subheader("Aggiungi PDF ed esegui la pipeline")

    uploaded = st.file_uploader(
        "Carica nuovi PDF (finiscono in papers/)", type=["pdf"], accept_multiple_files=True
    )
    if uploaded:
        papers_dir = cfg["paths"]["papers_dir"]
        papers_dir.mkdir(parents=True, exist_ok=True)
        for f in uploaded:
            (papers_dir / f.name).write_bytes(f.read())
        st.success(f"{len(uploaded)} PDF salvati in {papers_dir}")

    st.divider()
    step_labels = [s[1] for s in PIPELINE_STEPS]
    step_captions = [s[2] for s in PIPELINE_STEPS]
    selected_label = st.radio(
        "Passo da eseguire",
        options=step_labels,
        captions=step_captions,
    )
    step_key, _, _, step_kind, step_payload = next(s for s in PIPELINE_STEPS if s[1] == selected_label)

    if st.button("▶️ Esegui", type="primary"):
        if step_kind == "multi":
            progress_bar = st.progress(0.0)
            status_ph = st.empty()
            log_ph = st.empty()
            ok, failed_at, err = run_multistage(step_payload, progress_bar, status_ph, log_ph)
            if ok:
                st.success("Pipeline completata.")
                st.cache_data.clear()
            else:
                st.error(f"Interrotto durante '{failed_at}': {err}")
        else:
            with st.spinner(f"Esecuzione: {selected_label}..."):
                _, err = run_with_live_output(step_payload)
            if err:
                st.error(f"Terminato con errore: {err}")
            else:
                st.success("Completato")
                st.cache_data.clear()

# ------------------------------------------------------------- Graphify ----
with tab_graphify:
    st.subheader("🕸️ Graphify — grafo delle correlazioni e query")
    st.caption(
        "Integrazione con [Graphify](https://github.com/Graphify-Labs/graphify): "
        "estrazione semantica + community detection (Leiden) su data/parsed/. "
        "Unico meccanismo di grafo/Q&A del progetto. Nota: la deduplicazione "
        "nativa di Graphify opera entro il singolo documento, non sull'intero "
        "corpus — lo stesso concetto in paper diversi può restare frammentato "
        "in più nodi; un passo di fusione locale (sotto) corregge questo caso."
    )

    graphify_installed = bool(shutil.which("graphify"))
    if not graphify_installed:
        st.warning(
            "Comando `graphify` non trovato nel PATH. È incluso tra le "
            "dipendenze del progetto: lancia `uv sync`, poi riavvia la "
            "dashboard con `uv run streamlit run dashboard.py` (deve girare "
            "dentro `uv run`, non con uno streamlit generico fuori dal venv)."
        )

    st.divider()
    auto_merge = st.checkbox(
        "Fondi automaticamente i nodi duplicati dopo l'estrazione",
        value=True,
        key="graphify_auto_merge",
        help="Graphify non deduplica sempre lo stesso concetto tra paper diversi "
             "(es. 'GLUT1 (Glucose Transporter)' e 'GLUT1 Glucose Transporter' "
             "restano due nodi separati). Questo passo aggiuntivo, locale e "
             "gratuito, li fonde in un unico nodo dopo cluster-only.",
    )
    if st.button(
        "🕸️ Costruisci / aggiorna grafo Graphify",
        disabled=not graphify_installed,
        help="Estrae da data/parsed/ via 'graphify extract --backend claude-cli'. "
             "🔴 consuma la sottoscrizione Claude (stesso meccanismo di call_claude_code).",
    ):
        with st.spinner("Graphify sta estraendo ed elaborando il grafo..."):
            _, err = run_with_live_output(graphify_mod.run_extract, merge_dupes=auto_merge)
        if err:
            st.error(f"Errore: {err}")
        else:
            st.success("Grafo Graphify aggiornato.")

    with st.expander("🔀 Fondi nodi duplicati su un grafo già esistente"):
        st.caption(
            "Rilancia solo la fusione dei duplicati senza rifare l'estrazione — "
            "utile su un grafo costruito prima che questo step fosse integrato, "
            "o se avevi disattivato il merge automatico sopra. Locale, gratuito, "
            "nessuna chiamata Claude. Scrive un backup (graph.pre-merge.json) "
            "prima di sovrascrivere graph.json."
        )
        if st.button("🔀 Fondi nodi duplicati ora", disabled=not graphify_installed):
            with st.spinner("Fusione dei nodi duplicati in corso..."):
                report, err = run_with_live_output(graphify_mod.merge_duplicate_nodes)
            if err:
                st.error(f"Errore: {err}")
            elif report and report.get("merged_groups", 0) > 0:
                st.success(
                    f"Fusi {report['merged_groups']} gruppi di nodi duplicati. "
                    f"Nodi: {report['nodes_before']} → {report['nodes_after']}."
                )
                with st.expander("Dettaglio fusioni"):
                    for d in report["details"]:
                        absorbed = ", ".join(a["label"] for a in d["absorbed"])
                        st.write(f"**{d['label']}** ← fuso con: {absorbed}")
            else:
                st.info("Nessun nodo duplicato trovato: grafo già pulito.")

    st.divider()
    st.subheader("Fai una domanda ai paper (ricerca full-text)")
    st.caption(
        "Cerca direttamente nei markdown grezzi in data/parsed/ (non nel grafo): "
        "trova i paragrafi più rilevanti per parole chiave e sintetizza una "
        "risposta citando le fonti. Più affidabile del grafo su domande "
        "fattuali/quantitative puntuali ('quale concentrazione', 'quanti "
        "pazienti', 'quale dose') — quei valori numerici, incastonati in "
        "didascalie di figure o metodi, spesso non sopravvivono come attributo "
        "di un nodo durante l'estrazione semantica del grafo. 🔴 Consuma 2 "
        "chiamate Claude (estrazione keyword + sintesi finale)."
    )
    ft_question = st.text_input(
        "Domanda", placeholder="quale concentrazione di lattato è usata nelle T Reg?",
        key="fulltext_question",
    )
    show_snippets = st.checkbox(
        "Mostra anche i paragrafi grezzi trovati", key="fulltext_show_snippets"
    )
    if st.button("🔎 Cerca nel testo grezzo", disabled=not ft_question.strip()):
        with st.spinner("Ricerca nei markdown ed elaborazione della risposta..."):
            try:
                if show_snippets:
                    snippets = fulltext_mod.search_fulltext(ft_question)
                    with st.expander("Paragrafi grezzi trovati (ordinati per rilevanza)"):
                        for source, paragraph in snippets:
                            st.markdown(f"**[{source}]**")
                            st.text(paragraph)
                            st.divider()
                    answer = fulltext_mod.answer_from_snippets(ft_question, snippets)
                else:
                    answer = fulltext_mod.answer_question_fulltext(ft_question)
                st.markdown(answer)
            except Exception as e:  # noqa: BLE001
                st.error(f"Errore: {e}")

    col_a, col_b, col_btn = st.columns([2, 2, 1])
    with col_a:
        path_a = st.text_input("Nodo A", placeholder="PD-1", key="graphify_path_a")
    with col_b:
        path_b = st.text_input("Nodo B", placeholder="MCT1", key="graphify_path_b")
    with col_btn:
        st.write("")
        st.write("")
        path_go = st.button(
            "🔗 Percorso", disabled=not (graphify_installed and path_a.strip() and path_b.strip())
        )
    if path_go:
        try:
            st.code(graphify_mod.run_path(path_a, path_b), language="text")
        except Exception as e:  # noqa: BLE001
            st.error(f"Errore: {e}")

    st.divider()
    st.subheader("📋 Sfoglia nodi e community")
    st.caption(
        "Terminologia ESATTA presente nel grafo — usala nelle domande invece di "
        "indovinare (es. 'macrophages' funziona meglio di 'macrofagi' o di una "
        "domanda generica come 'cosa sono i macrofagi'). 100% locale, gratuito."
    )
    nodes = graphify_mod.list_nodes()
    if nodes:
        communities = sorted({n["community"] for n in nodes if n["community"]})
        col_filter, col_search = st.columns([2, 2])
        with col_filter:
            community_filter = st.selectbox(
                "Filtra per community", ["(tutte)"] + communities, key="nodes_community_filter"
            )
        with col_search:
            text_filter = st.text_input(
                "Filtra per testo nel nome", key="nodes_text_filter", placeholder="es. macro"
            )

        filtered = nodes
        if community_filter != "(tutte)":
            filtered = [n for n in filtered if n["community"] == community_filter]
        if text_filter.strip():
            needle = text_filter.strip().lower()
            filtered = [n for n in filtered if needle in n["label"].lower()]

        st.write(f"**{len(filtered)}** / {len(nodes)} nodi")
        st.dataframe(
            [{"nodo": n["label"], "community": n["community"], "paper": n["source"]} for n in filtered],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("Nessun grafo Graphify trovato ancora, o graph.json ha una struttura imprevista.")

    st.divider()
    st.subheader("Report ed esplorazione visiva")
    report_path = graphify_mod.graph_report_path()
    if report_path.exists():
        with st.expander("📄 GRAPH_REPORT.md", expanded=False):
            st.markdown(report_path.read_text(encoding="utf-8"))
    graphify_html = graphify_mod.graph_html_path()
    if graphify_html.exists():
        st.iframe(graphify_html, height=700)
    else:
        st.info("Nessun grafo Graphify trovato ancora. Usa il pulsante sopra per costruirlo.")

# ----------------------------------------------------------- Riassunti -----
with tab_summary:
    st.subheader("Riassunto di un paper")
    st.caption("Cachato su disco: la prima generazione consuma 1 chiamata Claude, le successive sono gratis.")

    paper_ids = sorted(utils.Manifest(cfg["paths"]["manifest"]).data.keys())
    if paper_ids:
        selected = st.selectbox("Paper", paper_ids)
        force = st.checkbox("Rigenera anche se già cachato (nuova chiamata Claude)")
        if st.button("📄 Genera / mostra riassunto"):
            with st.spinner("..."):
                summary, err = run_with_live_output(summarize_mod.summarize, selected, force)
            if err:
                st.error(f"Errore: {err}")
            elif summary:
                st.divider()
                st.markdown(summary)
    else:
        st.info("Nessun paper disponibile.")
