"""Fase 1: PDF -> Markdown strutturato via Docling. 100% locale, nessuna chiamata Claude.

Docling gestisce bene layout a due colonne, tabelle con celle unite/annidate
e formule tipiche dei paper scientifici -> è il punto in cui si guadagna il
risparmio di token, perché a Claude non arriverà mai un PDF grezzo.
"""
from __future__ import annotations

import json
from pathlib import Path

from tqdm import tqdm

from utils import Manifest, ensure_dirs, file_hash, load_config, slugify


def _build_converter(cfg):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # OCR disattivabile da config.yaml (docling.ocr): per PDF già digitali
    # (testo incorporato, non scansioni) l'OCR è puro overhead — inizializza e
    # fa inferenza con un modello separato (RapidOCR) su ogni pagina senza
    # bisogno, aumentando sensibilmente i tempi di ingest. Default: disattivato.
    # Riattivalo (docling.ocr: true) se aggiungi PDF scansionati o senza testo
    # incorporato, altrimenti Docling non riuscirebbe a estrarne il contenuto.
    do_ocr = bool(cfg.get("docling", {}).get("ocr", False))
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def ingest_all() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    manifest = Manifest(cfg["paths"]["manifest"])

    pdfs = sorted(cfg["paths"]["papers_dir"].glob("*.pdf"))
    if not pdfs:
        print(f"Nessun PDF trovato in {cfg['paths']['papers_dir']}. Mettici i tuoi paper e rilancia.")
        return

    converter = _build_converter(cfg)

    for pdf_path in tqdm(pdfs, desc="Parsing PDF con Docling"):
        h = file_hash(pdf_path)
        paper_id = slugify(pdf_path.stem)

        if manifest.stage_done(paper_id, "parsed", h):
            continue  # già parsato in una run precedente (anche su un altro computer, se importi data/parsed)

        result = converter.convert(str(pdf_path))
        doc = result.document

        md_text = doc.export_to_markdown()
        out_md = cfg["paths"]["parsed_dir"] / f"{paper_id}.md"
        out_md.write_text(md_text, encoding="utf-8")

        title = None
        for line in md_text.splitlines():
            if line.strip().startswith("#"):
                title = line.lstrip("#").strip()
                break

        n_pages = getattr(doc, "num_pages", None)
        if callable(n_pages):
            try:
                n_pages = n_pages()
            except Exception:
                n_pages = None

        meta = {
            "paper_id": paper_id,
            "source_pdf": pdf_path.name,
            "title": title or pdf_path.stem,
            "n_pages": n_pages,
            "source_hash": h,
        }
        out_meta = cfg["paths"]["parsed_dir"] / f"{paper_id}.meta.json"
        out_meta.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        manifest.mark_done(paper_id, "parsed", h)
        print(f"  -> {paper_id}: {title or pdf_path.stem}")

    print("Ingest completato.")


if __name__ == "__main__":
    ingest_all()
