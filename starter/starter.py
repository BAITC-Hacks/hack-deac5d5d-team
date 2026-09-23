#!/usr/bin/env python3
"""CLI entry point and backward-compatible library imports.

Run: python starter.py --data ../data --out ./out
See pipeline.py for orchestration and storage.py for validated publication.
"""

from pathlib import Path

import networkx as nx
import pandas as pd

from contracts import AnalysisConfig, AnalysisResult
from features import basic_features, build_graph
from pipeline import load
from storage import save_result, write_graph_json
from teaching import betweenness_features, hints, write_templates
from validation import validate_inputs

__all__ = [
    "basic_features",
    "build_graph",
    "load",
    "write_graph_json",
    "write_outputs",
    "sanity_check",
    "betweenness_features",
    "hints",
    "write_templates",
    "main",
]


def sanity_check(edges: pd.DataFrame, nodes: pd.DataFrame, tx: pd.DataFrame) -> set[int]:
    """Compatibility input check; CLI uses pipeline preparation exactly once."""
    orphans = validate_inputs(edges, nodes, tx)
    print(
        f"Узлов: {len(nodes)}; рёбер: {len(edges)}; операций: {len(tx)}; изолятов: {len(orphans)}"
    )
    return orphans


def write_outputs(
    roles: pd.DataFrame,
    clusters: pd.DataFrame,
    top: pd.DataFrame,
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    out_dir: Path,
    graph: nx.DiGraph | None = None,
) -> None:
    """Legacy adapter. New callers pass one AnalysisResult to save_result()."""
    result = AnalysisResult(
        build_graph(edges, nodes) if graph is None else graph,
        roles,
        clusters,
        top,
        nodes,
        edges,
        AnalysisConfig(),
    )
    save_result(result, out_dir, graph_writer=write_graph_json)


def main() -> None:
    from cli import main as run_cli

    run_cli()


if __name__ == "__main__":
    main()
