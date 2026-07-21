"""Riassunto di un singolo paper. Una chiamata Claude (via Claude Code headless,
sottoscrizione Pro/Max) sul markdown pulito (condensato se molto lungo), mai sul
PDF originale. Risultato cachato su disco: richiederlo di nuovo per lo stesso
paper non ripete la chiamata.
"""
from __future__ import annotations

from utils import Manifest, call_claude_code, condense_markdown, ensure_dirs, load_config

SYSTEM_PROMPT = """Sei un assistente che riassume paper scientifici in italiano, in modo denso e preciso.
Struttura la risposta in markdown con queste sezioni: Obiettivo, Metodo, Risultati principali,
Limiti, Rilevanza/possibili applicazioni. Sii conciso: niente ripetizioni, niente frasi di riempimento."""


def summarize(paper_id: str, force: bool = False) -> str:
    cfg = load_config()
    ensure_dirs(cfg)
    manifest = Manifest(cfg["paths"]["manifest"])

    md_path = cfg["paths"]["parsed_dir"] / f"{paper_id}.md"
    if not md_path.exists():
        raise FileNotFoundError(f"Paper '{paper_id}' non trovato in {cfg['paths']['parsed_dir']}. "
                                 f"Controlla l'id con: python main.py status")

    out_path = cfg["paths"]["summaries_dir"] / f"{paper_id}.md"
    h = manifest.get(paper_id).get("parsed")
    if not force and manifest.stage_done(paper_id, "summarized", h) and out_path.exists():
        return out_path.read_text(encoding="utf-8")

    content = md_path.read_text(encoding="utf-8")
    condensed = condense_markdown(content, cfg["claude"]["condense_threshold_chars"])

    summary = call_claude_code(
        instruction="Riassumi questo paper seguendo la struttura indicata nelle istruzioni di sistema.",
        input_text=condensed,
        system_prompt=SYSTEM_PROMPT,
        model=cfg["claude"]["model_summary"],
    )
    out_path.write_text(summary, encoding="utf-8")
    manifest.mark_done(paper_id, "summarized", h)
    return summary


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python summarize.py <paper_id>")
    else:
        print(summarize(sys.argv[1]))
