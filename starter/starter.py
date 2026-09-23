#!/usr/bin/env python3
"""CLI entry point and backward-compatible library imports.

Run: python starter.py --data ../data --out ./out
Canonical entry point from the project root: python -m hackalem.
"""

import sys
from pathlib import Path

# Keep the original script usable when launched from starter/ or another directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import networkx as nx
import pandas as pd

from hackalem.contracts import AnalysisConfig, AnalysisResult
from hackalem.features import basic_features, build_graph
from hackalem.pipeline import load
from hackalem.storage import save_result, write_graph_json
from hackalem.teaching import betweenness_features, hints, write_templates
from hackalem.validation import validate_inputs

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
    from hackalem.cli import main as run_cli

    run_cli(default_data=Path("../data"))


if __name__ == "__main__":
    main()
