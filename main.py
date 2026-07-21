#!/usr/bin/env python
"""CLI unico della pipeline paper-kb.

Il grafo delle correlazioni tra paper e le query in linguaggio naturale sono
gestiti interamente da Graphify (comando esterno 'graphify', non incluso tra
le dipendenze del progetto: uv tool install graphifyy). Vedi README.

Uso tipico:
    python main.py ingest              # PDF -> markdown (Docling, locale)
    python main.py graphify-extract    # costruisce/aggiorna il grafo Graphify
    python main.py graphify-query "domanda"   # sottografo grezza (locale, gratuito)
    python main.py graphify-ask "domanda"     # risposta in prosa (1 chiamata Claude)
    python main.py graphify-merge-dupes       # fonde nodi duplicati sul grafo esistente (locale, gratuito)
    python main.py summarize <id>      # riassunto di un paper (1 chiamata Claude, cachata)
    python main.py status              # cosa è stato processato finora
    python main.py all                 # ingest + graphify-extract in sequenza
"""
from __future__ import annotations

import sys

sys.path.insert(0, "src")

import click


@click.group()
def cli():
    pass


@cli.command()
def ingest():
    """PDF -> markdown pulito (Docling, locale)."""
    from ingest import ingest_all
    ingest_all()


@cli.command()
@click.argument("paper_id")
@click.option("--force", is_flag=True, help="Rigenera anche se già in cache.")
def summarize(paper_id: str, force: bool):
    """Riassume un paper (chiamata Claude, cachata su disco)."""
    from summarize import summarize as do_summarize
    print(do_summarize(paper_id, force=force))


@cli.command()
def check():
    """Verifica che Claude Code sia installato e pronto a usare la sottoscrizione Pro/Max."""
    from utils import check_claude_code
    try:
        check_claude_code()
        print("OK: comando 'claude' trovato.")
    except RuntimeError as e:
        print(e)
        return
    from utils import call_claude_code
    try:
        answer = call_claude_code("Rispondi solo con la parola: pronto")
        print(f"OK: Claude Code risponde correttamente -> '{answer.strip()}'")
    except Exception as e:
        print(f"Errore nel chiamare Claude Code: {e}\n"
              f"Prova ad autenticarti con: claude login")


@cli.command(name="graphify-extract")
def graphify_extract_cmd():
    """Costruisce/aggiorna il grafo Graphify a partire da data/parsed/.
    Richiede 'graphify' nel PATH: uv tool install graphifyy.
    Consuma la sottoscrizione Claude per l'estrazione semantica (--backend claude-cli)."""
    from graphify_kb import run_extract
    run_extract()


@cli.command(name="graphify-query")
@click.argument("question")
def graphify_query_cmd(question: str):
    """Interroga il grafo Graphify e mostra la sottografo GREZZA (nodi/edge da
    una traversata BFS) — locale, gratuito, nessuna chiamata Claude. Per una
    risposta in prosa vedi 'graphify-ask'."""
    from graphify_kb import run_query
    print(run_query(question))


@cli.command(name="graphify-ask")
@click.argument("question")
def graphify_ask_cmd(question: str):
    """Come 'graphify-query', ma la sottografo viene sintetizzata in una
    risposta in prosa da Claude. 🔴 consuma 1 chiamata Claude (contesto
    ridotto al solo sottografo pertinente, non l'intero paper)."""
    from graphify_kb import answer_question
    print(answer_question(question))


@cli.command(name="graphify-path")
@click.argument("node_a")
@click.argument("node_b")
def graphify_path_cmd(node_a: str, node_b: str):
    """Traccia il percorso più breve tra due nodi del grafo Graphify (locale, gratuito).
    Esempio: python main.py graphify-path "PD-1" "MCT1" """
    from graphify_kb import run_path
    print(run_path(node_a, node_b))


@cli.command(name="graphify-merge-dupes")
def graphify_merge_dupes_cmd():
    """Fonde nodi duplicati (stesso concetto, label diverso solo per
    punteggiatura/maiuscole, es. 'GLUT1 (Glucose Transporter)' vs 'GLUT1
    Glucose Transporter') su un grafo Graphify GIÀ COSTRUITO, senza rifare
    l'estrazione. Locale, gratuito, nessuna chiamata Claude. Scrive un
    backup (graph.pre-merge.json) prima di sovrascrivere graph.json.
    Viene già eseguito automaticamente da 'graphify-extract' e da 'all':
    usa questo comando solo per rilanciare la fusione da sola, ad es. su un
    grafo costruito prima che questo step fosse integrato."""
    from graphify_kb import merge_duplicate_nodes
    merge_duplicate_nodes()


@cli.command()
def status():
    """Mostra quali paper sono stati processati e a che stadio."""
    from utils import load_config, Manifest
    cfg = load_config()
    manifest = Manifest(cfg["paths"]["manifest"])
    if not manifest.data:
        print("Nessun paper processato ancora. Metti dei PDF in papers/ e lancia: python main.py all")
        return
    stages = ["parsed", "summarized"]
    print(f"{'paper_id':<40}" + "".join(f"{s:<12}" for s in stages))
    for paper_id, entry in manifest.data.items():
        row = f"{paper_id:<40}"
        for s in stages:
            row += f"{'OK' if s in entry else '-':<12}"
        print(row)
    print(
        "\nNota: lo stato del grafo Graphify non è tracciato qui (Graphify gestisce "
        "il proprio stato incrementale). Vedi 'graphify-extract' o la tab Graphify "
        "della dashboard per il report."
    )


@cli.command(name="all")
def run_all():
    """Esegue ingest + graphify-extract in sequenza."""
    from ingest import ingest_all
    from graphify_kb import run_extract

    ingest_all()
    run_extract()


if __name__ == "__main__":
    cli()
