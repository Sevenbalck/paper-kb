"""
Backend FastAPI per paper-kb — sostituisce la logica di dashboard.py
esponendola come API REST, così un frontend web (es. React costruito con
Claude Design) puo' chiamarla al posto di girare dentro Streamlit.

Avvio (dalla root del progetto, dove sta anche main.py):
    uv run uvicorn backend:app --reload --port 8000

Design:
- Nessuna logica di business qui dentro: si limita a chiamare le stesse
  funzioni di src/ già usate da dashboard.py (ingest, summarize,
  graphify_kb, utils). Se main.py e dashboard.py restano invariati,
  questo file può convivere con loro senza conflitti.
- Le azioni lunghe/streaming (pipeline "all", graphify extract) girano in
  background e pubblicano i log su una coda per-job, letta dal frontend via
  Server-Sent Events (SSE) — equivalente del box "live output" di Streamlit.
- CORS aperto in dev per semplicità: da restringere se il frontend gira su
  un dominio diverso in produzione.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import graphify_kb as graphify_mod  # noqa: E402
import ingest as ingest_mod  # noqa: E402
import summarize as summarize_mod  # noqa: E402
import utils  # noqa: E402

app = FastAPI(title="paper-kb API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restringere in produzione
    allow_methods=["*"],
    allow_headers=["*"],
)

cfg = utils.load_config()

# --------------------------------------------------------------------------
# Gestione job in background con streaming dei log (sostituisce _LivePrinter)
# --------------------------------------------------------------------------

_jobs: dict[str, asyncio.Queue] = {}


class _QueuePrinter:
    """Come _LivePrinter di dashboard.py, ma scrive su una asyncio.Queue
    invece che su un placeholder Streamlit."""

    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.queue = queue
        self.loop = loop

    def write(self, s: str) -> int:
        if s.strip():
            asyncio.run_coroutine_threadsafe(self.queue.put(s), self.loop)
        return len(s)

    def flush(self) -> None:
        pass


async def _run_job(job_id: str, steps: list[tuple[str, callable]]) -> None:
    """Esegue una lista di (nome_fase, funzione) in un thread, pubblicando
    log e stato sulla coda del job. 'steps' con un solo elemento equivale
    alle azioni 'single' di dashboard.py."""
    queue = _jobs[job_id]
    loop = asyncio.get_running_loop()
    printer = _QueuePrinter(queue, loop)

    def _blocking_run():
        for name, fn in steps:
            printer.write(f"\n=== {name} ===\n")
            with redirect_stdout(printer):  # type: ignore[arg-type]
                fn()

    try:
        await asyncio.to_thread(_blocking_run)
        await queue.put(json.dumps({"event": "done"}))
    except Exception as e:  # noqa: BLE001
        await queue.put(json.dumps({"event": "error", "message": str(e)}))
    finally:
        await queue.put(None)  # sentinella di fine stream


def _start_job(steps: list[tuple[str, callable]]) -> str:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = asyncio.Queue()
    asyncio.create_task(_run_job(job_id, steps))
    return job_id


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    """SSE: il frontend si iscrive qui per vedere i log live di un job,
    esattamente come il box 'live output' nella tab Pipeline/Graphify."""
    if job_id not in _jobs:
        raise HTTPException(404, "job non trovato")

    async def event_gen():
        queue = _jobs[job_id]
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {item}\n\n"
        del _jobs[job_id]

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# --------------------------------------------------------------------------
# Tab "Stato"
# --------------------------------------------------------------------------

@app.get("/status")
def get_status():
    """Equivalente a load_status_rows(): righe del manifest con stato
    parsed/summarized per ogni paper."""
    manifest = utils.Manifest(cfg["paths"]["manifest"])
    rows = []
    for pid, entry in sorted(manifest.data.items()):
        meta_path = cfg["paths"]["parsed_dir"] / f"{pid}.meta.json"
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
                "parsed": "parsed" in entry,
                "summarized": "summarized" in entry,
            }
        )
    return {"manifest_path": str(cfg["paths"]["manifest"]), "papers": rows}


@app.post("/check")
def post_check():
    """Equivalente a do_check(): verifica setup Claude Code."""
    try:
        utils.check_claude_code()
        reply = utils.call_claude_code("Rispondi solo con la parola: pronto")
        return {"ok": True, "reply": reply}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


@app.get("/papers/{paper_id}/file")
def get_paper_file(paper_id: str):
    """Serve il PDF originale di un paper, per il link "Apri il PDF" nella
    tab Stato — stesso matching per slug di delete_paper(). Nessun
    Content-Disposition: il browser lo apre inline nella scheda invece di
    scaricarlo, coerente col target="_blank" del frontend."""
    for pdf in cfg["paths"]["papers_dir"].glob("*.pdf"):
        if utils.slugify(pdf.stem) == paper_id:
            return FileResponse(pdf, media_type="application/pdf")
    raise HTTPException(404, f"Nessun PDF trovato per '{paper_id}'")


@app.delete("/papers/{paper_id}")
def delete_paper(paper_id: str):
    """Equivalente a do_delete_paper(). Irreversibile, come in dashboard.py:
    il frontend deve chiedere conferma PRIMA di chiamare questo endpoint."""
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

    if not removed:
        raise HTTPException(404, f"Nessun file trovato per '{paper_id}'")
    return {"removed": removed}


# --------------------------------------------------------------------------
# Tab "Pipeline"
# --------------------------------------------------------------------------

@app.post("/papers/upload")
async def upload_papers(files: list[UploadFile]):
    papers_dir = cfg["paths"]["papers_dir"]
    papers_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        dest = papers_dir / f.filename
        dest.write_bytes(await f.read())
        saved.append(f.filename)
    return {"saved": saved, "dir": str(papers_dir)}


class PipelineStep(BaseModel):
    step: Literal["all", "ingest", "graphify-extract", "graphify-merge-dupes"]
    merge_dupes: bool = True


@app.post("/pipeline/run")
async def run_pipeline(body: PipelineStep):
    """Avvia uno step della pipeline in background e ritorna un job_id da
    seguire su /jobs/{job_id}/stream — equivalente a run_multistage() /
    run_with_live_output() di dashboard.py.

    'merge_dupes' (default True) controlla se run_extract fonde
    automaticamente i nodi duplicati come terzo passo (locale, gratuito,
    nessuna chiamata Claude aggiuntiva) — vedi merge_duplicate_nodes() in
    graphify_kb.py. 'graphify-merge-dupes' come step a sé rilancia SOLO la
    fusione su un grafo già costruito, senza rifare l'estrazione."""
    def _extract():
        graphify_mod.run_extract(merge_dupes=body.merge_dupes)

    steps_map: dict[str, list[tuple[str, callable]]] = {
        "all": [
            ("Ingest (PDF → markdown)", ingest_mod.ingest_all),
            ("Graphify extract (chiama Claude)", _extract),
        ],
        "ingest": [("Ingest (PDF → markdown)", ingest_mod.ingest_all)],
        "graphify-extract": [("Graphify extract (chiama Claude)", _extract)],
        "graphify-merge-dupes": [
            ("Fusione nodi duplicati (locale, gratuito)", graphify_mod.merge_duplicate_nodes),
        ],
    }
    job_id = _start_job(steps_map[body.step])
    return {"job_id": job_id}


# --------------------------------------------------------------------------
# Tab "Graphify"
# --------------------------------------------------------------------------

@app.get("/graphify/status")
def graphify_status():
    import shutil

    return {"installed": bool(shutil.which("graphify"))}


class AskBody(BaseModel):
    question: str
    show_raw: bool = False


@app.post("/graphify/ask")
def graphify_ask(body: AskBody):
    try:
        raw = graphify_mod.run_query(body.question) if body.show_raw else None
        answer = graphify_mod.answer_question(body.question)
        return {"answer": answer, "raw": raw}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


@app.get("/graphify/path")
def graphify_path(node_a: str, node_b: str):
    try:
        return {"path": graphify_mod.run_path(node_a, node_b)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


@app.get("/graphify/nodes")
def graphify_nodes(community: str | None = None, text: str | None = None):
    nodes = graphify_mod.list_nodes()
    if community:
        nodes = [n for n in nodes if n["community"] == community]
    if text:
        needle = text.strip().lower()
        nodes = [n for n in nodes if needle in n["label"].lower()]
    return {"total": len(graphify_mod.list_nodes()), "filtered": len(nodes), "nodes": nodes}


@app.get("/graphify/report")
def graphify_report():
    report_path = graphify_mod.graph_report_path()
    if not report_path.exists():
        raise HTTPException(404, "GRAPH_REPORT.md non trovato")
    return {"markdown": report_path.read_text(encoding="utf-8")}


@app.get("/graphify/graph-html-path")
def graphify_graph_html_path():
    """Metadati sul file locale (per debug): il frontend usa invece
    GET /graphify/graph-html per caricare il contenuto in un iframe."""
    html_path = graphify_mod.graph_html_path()
    return {"exists": html_path.exists(), "path": str(html_path)}


@app.get("/graphify/graph-html", response_class=HTMLResponse)
def graphify_graph_html():
    """Contenuto di graph.html (pyvis, self-contained: usa vis-network via
    CDN ma nessun asset locale relativo) — il frontend lo carica in un
    <iframe src=".../graphify/graph-html">, equivalente a st.iframe(path)
    che invece leggeva il file locale direttamente."""
    html_path = graphify_mod.graph_html_path()
    if not html_path.exists():
        raise HTTPException(404, "graph.html non trovato: costruisci prima il grafo")
    return html_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Tab "Riassunti"
# --------------------------------------------------------------------------

@app.get("/summary/{paper_id}")
def get_summary(paper_id: str, force: bool = False):
    try:
        return {"paper_id": paper_id, "summary": summarize_mod.summarize(paper_id, force)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e
