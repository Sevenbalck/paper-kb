#!/usr/bin/env python3
"""
Fonde i nodi duplicati nel graph.json di Graphify: quando lo stesso concetto
appare come nodi distinti (perché estratti da paper diversi e mai deduplicati
tra loro — vedi inspect_graphify_dupes.py), questo script li unisce in un
unico nodo, rimappando tutti gli archi verso il sopravvissuto.

NON modifica il file originale: scrive un nuovo graph.json accanto
(graph.merged.json) e stampa un report di cosa è stato fuso. Rivedi il report
prima di sostituire il file originale.

Uso:
    python merge_duplicate_nodes.py [path/al/graph.json]

Poi, se il risultato ti convince:
    copy graph.merged.json graph.json   (Windows)
    cp graph.merged.json graph.json     (Linux/Mac)
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_graph(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"File non trovato: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"Errore nel parsing di {path}: {e}")


def norm(label: str) -> str:
    """Stessa normalizzazione di inspect_graphify_dupes.py: case-insensitive,
    punteggiatura rimossa, spazi collassati. Deve restare identica ai due
    script per coerenza tra ispezione e merge."""
    s = str(label).strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def edge_endpoints(e: dict) -> tuple:
    """Gli archi di NetworkX node-link possono usare 'source'/'target' oppure
    a volte 'from'/'to' a seconda della versione di export; copriamo entrambi
    per non perdere silenziosamente archi con schema diverso."""
    src = e.get("source", e.get("from"))
    tgt = e.get("target", e.get("to"))
    return src, tgt


def main():
    args = sys.argv[1:]
    path = Path(args[0]) if args else Path("data/parsed/graphify-out/graph.json")
    data = load_graph(path)

    raw_nodes = data.get("nodes", [])
    edges_key = "links" if "links" in data else "edges"
    raw_edges = data.get(edges_key, [])

    if not isinstance(raw_nodes, list):
        sys.exit("Struttura di graph.json inattesa: 'nodes' non è una lista.")

    print(f"Nodi originali: {len(raw_nodes)}   Archi originali: {len(raw_edges)}\n")

    # 1. Raggruppa i nodi per label normalizzato
    groups = defaultdict(list)
    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        label = n.get("label") or n.get("id") or "(senza nome)"
        groups[norm(label)].append(n)

    dupe_groups = {k: v for k, v in groups.items() if len(v) > 1}

    if not dupe_groups:
        print("Nessun duplicato trovato: nulla da fondere. File non riscritto.")
        return

    # 2. Conta quanti nodi appartengono a ciascuna community, sull'INTERO
    #    grafo: serve per scegliere, in ogni gruppo di duplicati, il nodo la
    #    cui community è più "rappresentativa" (più popolata) come sopravvissuto —
    #    euristica ragionevole per preferire il cluster tematico più consolidato
    #    invece di uno frammentario/piccolo come poteva essere community 11
    #    del caso GLUT1 (nominata come il nodo stesso, segno di cluster minuscolo).
    community_size = Counter(
        str(n.get("community")) for n in raw_nodes if isinstance(n, dict)
    )

    # 3. Per ogni gruppo di duplicati, scegli il sopravvissuto e costruisci la
    #    mappa id_duplicato -> id_sopravvissuto
    id_remap = {}
    survivors = []
    merge_report = []

    kept_ids = set()
    for label_norm, nodes in dupe_groups.items():
        # Ordina per popolarità della community (decrescente), poi per id
        # (per determinismo a parità di popolarità)
        nodes_sorted = sorted(
            nodes,
            key=lambda n: (-community_size[str(n.get("community"))], str(n.get("id"))),
        )
        primary = dict(nodes_sorted[0])  # copia, la arricchiamo sotto
        others = nodes_sorted[1:]

        primary_id = primary.get("id")
        merged_sources = {primary.get("source_file")} | {o.get("source_file") for o in others}
        merged_sources.discard(None)
        merged_ids = [primary_id] + [o.get("id") for o in others]

        primary["source_files_merged"] = sorted(merged_sources)
        primary["merged_from_ids"] = merged_ids
        primary["merge_note"] = (
            f"Nodo risultante dalla fusione di {len(nodes)} istanze duplicate "
            f"con label equivalente (vedi merged_from_ids)."
        )

        for o in others:
            id_remap[o.get("id")] = primary_id

        survivors.append(primary)
        kept_ids.add(primary_id)

        merge_report.append(
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

    # 4. Costruisci la nuova lista di nodi: tutti i non-duplicati + i sopravvissuti
    duplicate_original_ids = {n.get("id") for group in dupe_groups.values() for n in group}
    new_nodes = [n for n in raw_nodes if n.get("id") not in duplicate_original_ids]
    new_nodes.extend(survivors)

    # 5. Rimappa gli archi: sostituisci gli id assorbiti con l'id del
    #    sopravvissuto, poi scarta self-loop generati dal merge e deduplica
    #    archi identici (stessa coppia source/target, stesso 'type' se presente)
    new_edges = []
    seen_edges = set()
    dropped_self_loops = 0
    dropped_edge_dupes = 0

    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        src, tgt = edge_endpoints(e)
        new_src = id_remap.get(src, src)
        new_tgt = id_remap.get(tgt, tgt)

        if new_src == new_tgt:
            dropped_self_loops += 1
            continue

        edge_type = e.get("type") or e.get("label") or ""
        edge_key = (new_src, new_tgt, edge_type)
        edge_key_rev = (new_tgt, new_src, edge_type)  # tratta il grafo come non diretto per la dedup
        if edge_key in seen_edges or edge_key_rev in seen_edges:
            dropped_edge_dupes += 1
            continue
        seen_edges.add(edge_key)

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

    # 6. Scrivi il nuovo graph.json (nome diverso, non tocchiamo l'originale)
    new_data = dict(data)
    new_data["nodes"] = new_nodes
    new_data[edges_key] = new_edges

    out_path = path.with_name(path.stem + ".merged" + path.suffix)
    out_path.write_text(json.dumps(new_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 7. Report leggibile
    print(f"Gruppi di duplicati fusi: {len(dupe_groups)}\n")
    print("=" * 80)
    for r in merge_report:
        print(f"\n'{r['label']}'")
        print(f"  Sopravvissuto: {r['survivor_id']} (community {r['survivor_community']})")
        for a in r["absorbed"]:
            print(f"  Assorbito:     {a['id']} (community {a['community']}) — '{a['label']}'")
    print("\n" + "=" * 80)
    print(f"\nNodi: {len(raw_nodes)} -> {len(new_nodes)}  (-{len(raw_nodes) - len(new_nodes)})")
    print(f"Archi: {len(raw_edges)} -> {len(new_edges)}  "
          f"(self-loop scartati: {dropped_self_loops}, archi duplicati scartati: {dropped_edge_dupes})")
    print(f"\nScritto: {out_path}")
    print("\nControlla il risultato, poi se ti convince sostituisci il file originale:")
    print(f"  copy {out_path.name} {path.name}   (Windows)")
    print(f"  cp {out_path.name} {path.name}     (Linux/Mac)")


if __name__ == "__main__":
    main()
