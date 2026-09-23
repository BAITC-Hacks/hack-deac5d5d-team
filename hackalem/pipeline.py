"""Sequence validation, preparation and pure analysis; no printing or publication."""

import hashlib
from pathlib import Path

import pandas as pd

from .analysis import analyze
from .contracts import AnalysisConfig, AnalysisResult, Dataset
from .features import basic_features, build_graph
from .validation import validate_inputs, validate_outputs


def load(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (
        pd.read_parquet(data_dir / "edges.parquet"),
        pd.read_parquet(data_dir / "nodes.parquet"),
        pd.read_parquet(data_dir / "transactions.parquet"),
    )


def prepare_dataset(
    edges: pd.DataFrame, nodes: pd.DataFrame, tx: pd.DataFrame, config: AnalysisConfig | None = None
) -> Dataset:
    cfg = config or AnalysisConfig()
    return validate_inputs(edges, nodes, tx, cfg.input_profile, return_dataset=True)


def run_analysis(
    dataset: Dataset,
    config: AnalysisConfig | None = None,
    input_hashes: dict[str, str] | None = None,
    cluster_strategy=None,
) -> AnalysisResult:
    cfg = config or AnalysisConfig()
    graph = build_graph(dataset.edges, dataset.nodes)
    features = basic_features(graph, dataset.nodes)
    roles, clusters, top = analyze(
        graph, features, dataset.edges, dataset.transactions, cfg, cluster_strategy
    )
    validate_outputs(roles, clusters, top, dataset.nodes, dataset.edges)
    return AnalysisResult(
        graph, roles, clusters, top, dataset.nodes, dataset.edges, cfg, input_hashes or {}
    )


def load_dataset(
    data_dir: Path, config: AnalysisConfig | None = None
) -> tuple[Dataset, dict[str, str]]:
    # Hash exactly the bytes read by pandas, even if a producer replaces a source concurrently.
    from io import BytesIO

    frames, hashes = {}, {}
    for name in ("edges", "nodes", "transactions"):
        data = (data_dir / f"{name}.parquet").read_bytes()
        hashes[f"{name}.parquet"] = hashlib.sha256(data).hexdigest()
        frames[name] = pd.read_parquet(BytesIO(data))
    return prepare_dataset(frames["edges"], frames["nodes"], frames["transactions"], config), hashes
