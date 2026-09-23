"""Functional analysis pipeline and compatibility exports for existing callers."""

import networkx as nx
import pandas as pd

from .communities import assign_clusters, summarize_clusters, undirected_projection
from .contracts import AnalysisConfig
from .features import analysis_features, external_graph, positive_percentile, structural_betweenness
from .ranking import assign_roles_and_priorities, rank_nodes
from .rules import role_hypothesis
from .temporal import amount_temporal_support, temporal_support
from .validation import ValidationError

__all__ = [
    "analyze",
    "assign_clusters",
    "summarize_clusters",
    "undirected_projection",
    "analysis_features",
    "positive_percentile",
    "structural_betweenness",
    "assign_roles_and_priorities",
    "rank_nodes",
    "role_hypothesis",
    "amount_temporal_support",
    "temporal_support",
]


def analyze(
    graph: nx.DiGraph,
    features: pd.DataFrame,
    edges: pd.DataFrame,
    tx: pd.DataFrame,
    config: AnalysisConfig | None = None,
    cluster_strategy=None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cfg = config or AnalysisConfig()
    if len(features) < cfg.top_n:
        raise ValidationError(
            f"Для обязательного топа нужно минимум {cfg.top_n} уникальных клиентов"
        )
    assignment = assign_clusters(external_graph(graph), cfg, strategy=cluster_strategy)
    enriched = analysis_features(graph, features, tx, assignment, cfg)
    roles = assign_roles_and_priorities(enriched, cfg)
    return roles, summarize_clusters(roles, edges), rank_nodes(roles, cfg.top_n)
