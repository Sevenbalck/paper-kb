#!/usr/bin/env python3
"""
Ispeziona graph.json di Graphify per trovare label duplicati che finiscono
in community diverse — utile per capire se la deduplicazione delle entità
sta fallendo tra un paper e l'altro.

Uso:
    python inspect_graphify_dupes.py [path/al/graph.json] [--label GLUT1]

Se non passi un path, cerca in data/parsed/graphify-out/graph.json relativo
alla cwd (stesso default usato da graphify_kb.py).
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def load_graph(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"File non trovato: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"Errore nel parsing di {path}: {e}")


def main():
    args = sys.argv[1:]
    label_filter = None
    if "--label" in args:
        idx = args.index("--label")
        label_filter = args[idx + 1].lower()
        args = args[:idx] + args[idx + 2:]

    path = Path(args[0]) if args else Path("data/parsed/graphify-out/graph.json")
    data = load_graph(path)

    raw_nodes = data.get("nodes", [])
    if not isinstance(raw_nodes, list):
        sys.exit("Struttura di graph.json inattesa: 'nodes' non è una lista.")

    print(f"Totale nodi nel grafo: {len(raw_nodes)}\n")

    # Raggruppa per label normalizzato: case-insensitive, e con TUTTA la
    # punteggiatura (parentesi, virgole, trattini, underscore, ecc.) rimossa
    # e spazi multipli collassati. Questo intercetta anche casi come
    # "GLUT1 (Glucose Transporter)" vs "GLUT1 Glucose Transporter", che con
    # una normalizzazione più naive (solo trattini/underscore) restavano
    # invisibili perché finivano in due gruppi da 1 elemento ciascuno.
    def norm(label: str) -> str:
        s = label.strip().lower()
        s = re.sub(r"[^\w\s]", " ", s)  # rimuove tutta la punteggiatura
        s = re.sub(r"\s+", " ", s).strip()  # collassa spazi multipli
        return s

    groups = defaultdict(list)
    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        label = n.get("label") or n.get("id") or "(senza nome)"
        groups[norm(label)].append(n)

    # Trova gruppi con più di 1 nodo (potenziali duplicati non deduplicati)
    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    if label_filter:
        dupes = {k: v for k, v in dupes.items() if label_filter in k}

    if not dupes:
        print("Nessun label duplicato trovato" + (f" per '{label_filter}'." if label_filter else "."))
        return

    print(f"Trovati {len(dupes)} label con più istanze (non deduplicate):\n")
    print("=" * 80)

    for label_norm, nodes in sorted(dupes.items(), key=lambda x: -len(x[1])):
        communities = {str(n.get("community")) for n in nodes}
        labels_originali = {n.get("label") for n in nodes}
        print(f"\nLabel: {labels_originali}")
        print(f"  Istanze: {len(nodes)}   Community distinte: {len(communities)}")
        print(f"  {'id':<40} {'community':<15} {'source_file':<30}")
        print(f"  {'-'*40} {'-'*15} {'-'*30}")
        for n in nodes:
            node_id = str(n.get("id", ""))[:38]
            community = str(n.get("community", ""))[:13]
            source = str(n.get("source_file") or n.get("src") or "")[:28]
            print(f"  {node_id:<40} {community:<15} {source:<30}")
        print("-" * 80)

    print(f"\nRiepilogo: {len(dupes)} concetti risultano frammentati in più nodi.")
    print("Se le community sono diverse per lo stesso concetto, la deduplicazione")
    print("di Graphify non li ha uniti (probabile causa dei tuoi '11 community').")


if __name__ == "__main__":
    main()
