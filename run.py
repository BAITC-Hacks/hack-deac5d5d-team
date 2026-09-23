#!/usr/bin/env python3
"""One command from Parquet (or team analysis) to validated CSVs and offline UI."""

import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "starter"))

import pandas as pd
from starter import load, sanity_check, build_graph, basic_features, write_outputs
from validation import validate_output_files
from interface.baseline import analyze
from interface.build import build_payload, write_interface


def run(data_dir, out_dir, analysis_dir=None, top_count=50):
    started = time.perf_counter()
    edges, nodes, tx = load(Path(data_dir))
    sanity_check(edges, nodes, tx)
    nodes = nodes.sort_values("gid").reset_index(drop=True)
    edges = edges.sort_values(["src", "dst"]).reset_index(drop=True)
    graph = build_graph(edges, nodes)
    features = basic_features(graph, nodes)
    if analysis_dir is None:
        roles, clusters, top = analyze(graph, features, top_count)
        source = "baseline_rules_v1"
    else:
        analysis_dir = Path(analysis_dir)
        validate_output_files(analysis_dir, nodes, edges)
        roles = pd.read_csv(analysis_dir / "nodes_roles.csv", dtype={"gid": "int64"}, float_precision="round_trip")
        clusters = pd.read_csv(analysis_dir / "clusters.csv", float_precision="round_trip")
        top = pd.read_csv(analysis_dir / "top_nodes.csv", dtype={"gid": "int64"}, float_precision="round_trip")
        source = "team_csv"
    out_dir = Path(out_dir)
    write_outputs(roles, clusters, top, nodes, edges, out_dir)
    payload = build_payload(roles, clusters, top, features, edges, tx, source)
    write_interface(payload, out_dir)
    print(f"Готово за {time.perf_counter() - started:.2f} с: {len(nodes)} клиентов, "
          f"{len(edges)} связей, топ {len(top)}. Откройте {out_dir.resolve() / 'index.html'}")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--analysis", type=Path, help="Папка с тремя готовыми CSV участников 1–2")
    parser.add_argument("--top", type=int, default=50, help="Размер топа baseline, минимум 20")
    args = parser.parse_args()
    try:
        run(args.data, args.out, args.analysis, args.top)
    except (ValueError, OSError, KeyError, pd.errors.ParserError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    main()
