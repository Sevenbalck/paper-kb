#!/usr/bin/env python3
"""
Ispeziona un nodo specifico nel graph.json di Graphify: mostra i suoi dati
completi, tutti gli archi che lo toccano, e le community dei nodi vicini.

Utile per capire cosa significa un numero come "11 community" mostrato altrove
(es. graph.html / GRAPH_REPORT.md) quando il nodo stesso appartiene a UNA sola
community: probabilmente indica quante community diverse sono raggiunte dai
suoi vicini diretti (= nodo "ponte"/hub tra più cluster tematici).

Uso:
    python inspect_graphify_node.py [path/al/graph.json] --node GLUT1
"""

import json
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


def node_label(n: dict) -> str:
    return n.get("label") or n.get("id") or "(senza nome)"


def main():
    args = sys.argv[1:]
    node_filter = None
    if "--node" in args:
        idx = args.index("--node")
        node_filter = args[idx + 1].lower()
        args = args[:idx] + args[idx + 2:]

    if not node_filter:
        sys.exit("Serve --node <nome>, es: --node GLUT1")

    path = Path(args[0]) if args else Path("data/parsed/graphify-out/graph.json")
    data = load_graph(path)

    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("links") or data.get("edges") or []

    if not isinstance(raw_nodes, list):
        sys.exit("Struttura di graph.json inattesa: 'nodes' non è una lista.")

    def norm(s: str) -> str:
        return str(s).strip().lower().replace("-", " ").replace("_", " ")

    # Trova il/i nodo/i che matchano
    matches = [n for n in raw_nodes if isinstance(n, dict) and node_filter in norm(node_label(n))]

    if not matches:
        sys.exit(f"Nessun nodo trovato che contenga '{node_filter}' nel label.")

    # Indice id -> nodo, per risolvere i vicini
    by_id = {n.get("id"): n for n in raw_nodes if isinstance(n, dict)}

    for target in matches:
        target_id = target.get("id")
        print("=" * 80)
        print(f"NODO TROVATO: {node_label(target)}")
        print("=" * 80)
        print("Campi completi:")
        for k, v in target.items():
            print(f"  {k}: {v}")

        # Trova tutti gli archi che toccano questo nodo (in entrambe le direzioni)
        touching = []
        for e in raw_edges:
            if not isinstance(e, dict):
                continue
            src = e.get("source")
            tgt = e.get("target")
            if src == target_id or tgt == target_id:
                other_id = tgt if src == target_id else src
                other = by_id.get(other_id)
                touching.append((e, other))

        print(f"\nArchi collegati: {len(touching)}")

        # Community dei vicini
        neighbor_communities = defaultdict(list)
        for e, other in touching:
            if other is None:
                neighbor_communities["(vicino non trovato)"].append(str(e))
                continue
            comm = str(other.get("community", "(nessuna)"))
            neighbor_communities[comm].append(node_label(other))

        print(f"Community distinte raggiunte dai vicini diretti: {len(neighbor_communities)}\n")
        for comm, neighbors in sorted(neighbor_communities.items()):
            print(f"  Community '{comm}' ({len(neighbors)} vicini):")
            for nb in neighbors:
                print(f"    - {nb}")
        print()


if __name__ == "__main__":
    main()
