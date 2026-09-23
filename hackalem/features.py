"""Pure graph construction and externally observed client activity features."""

import networkx as nx
import numpy as np
import pandas as pd

from .contracts import AnalysisConfig
from .temporal import amount_temporal_support, temporal_support
from .validation import ValidationError, money_cents


def external_graph(graph: nx.DiGraph) -> nx.DiGraph:
    """Self transfers stay in the raw graph, but cannot create network importance."""
    loops = list(nx.selfloop_edges(graph))
    if not loops:
        return graph
    external = graph.copy()
    external.remove_edges_from(loops)
    return external


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    for row in nodes.sort_values("gid").itertuples(index=False):
        graph.add_node(int(row.gid), depth=int(row.depth), is_seed=bool(row.is_seed))
    ordered = edges.sort_values(["src", "dst"]).copy()
    ordered["sum_tiyn"] = money_cents(ordered.sum_kzt, "edges.sum_kzt", positive=True)
    for row in ordered.itertuples(index=False):
        if row.src not in graph or row.dst not in graph:
            raise ValidationError(
                "edges: unknown client IDs; validate inputs before building the graph"
            )
        graph.add_edge(
            int(row.src),
            int(row.dst),
            sum_tiyn=int(row.sum_tiyn),
            sum_kzt=float(row.sum_kzt),
            n_tx=int(row.n_tx),
            depth=int(row.depth),
        )
    return graph


def _exact_sum(graph: nx.DiGraph, gid: int, incoming: bool) -> int:
    edges = graph.in_edges(gid, data=True) if incoming else graph.out_edges(gid, data=True)
    return sum(int(attrs["sum_tiyn"]) for _, _, attrs in edges)


def _ensure_tiyn(graph: nx.DiGraph) -> nx.DiGraph:
    """Compatibility for callers constructing NetworkX graphs directly."""
    if all("sum_tiyn" in attrs for _, _, attrs in graph.edges(data=True)):
        return graph
    graph = graph.copy()
    for _, _, attrs in graph.edges(data=True):
        if "sum_tiyn" not in attrs:
            attrs["sum_tiyn"] = money_cents(
                pd.Series([attrs["sum_kzt"]]), "graph.sum_kzt", positive=True
            ).iloc[0]
    return graph


def basic_features(graph: nx.DiGraph, nodes: pd.DataFrame) -> pd.DataFrame:
    raw = _ensure_tiyn(graph)
    external = external_graph(raw)
    frame = nodes[["gid", "depth", "is_seed"]].sort_values("gid").reset_index(drop=True).copy()
    for name, values in [
        ("in_deg", dict(external.in_degree())),
        ("out_deg", dict(external.out_degree())),
        ("in_tx", dict(external.in_degree(weight="n_tx"))),
        ("out_tx", dict(external.out_degree(weight="n_tx"))),
    ]:
        frame[name] = frame.gid.map(values).astype("int64")
    for name, incoming in [("in_tiyn", True), ("out_tiyn", False)]:
        frame[name] = pd.Series(
            [_exact_sum(external, int(gid), incoming) for gid in frame.gid], dtype=object
        )
        frame[name.replace("_tiyn", "_kzt")] = (
            frame[name].map(lambda value: int(value) / 100).astype(float)
        )
    frame["pagerank"] = frame.gid.map(nx.pagerank(external, weight="sum_kzt"))
    frame["pass_through"] = [
        int(outgoing) / int(incoming) if incoming else np.nan
        for incoming, outgoing in zip(frame.in_tiyn, frame.out_tiyn)
    ]
    frame["truncated_by_depth"] = frame.depth.eq(4) & frame.out_deg.eq(0)
    frame["self_transfer_tiyn"] = pd.Series(
        [int(raw.get_edge_data(gid, gid, {}).get("sum_tiyn", 0)) for gid in frame.gid], dtype=object
    )
    frame["self_transfer_kzt"] = frame.self_transfer_tiyn.map(
        lambda value: int(value) / 100
    ).astype(float)
    frame["self_transfer_n_tx"] = [
        int(raw.get_edge_data(gid, gid, {}).get("n_tx", 0)) for gid in frame.gid
    ]
    return frame


def positive_percentile(values: pd.Series) -> pd.Series:
    """Average ties with exact ordering, including Python integers beyond int64."""
    positive = sorted(value for value in values if value > 0)
    count = len(positive)
    scores = {}
    start = 0
    while start < count:
        end = start + 1
        while end < count and positive[end] == positive[start]:
            end += 1
        scores[positive[start]] = (start + 1 + end) / (2 * count)
        start = end
    return pd.Series([scores.get(value, 0.0) for value in values], index=values.index, dtype=float)


def structural_betweenness(
    graph: nx.DiGraph, config: AnalysisConfig | None = None
) -> tuple[dict[int, float], str, int]:
    cfg = config or AnalysisConfig()
    external = external_graph(graph)
    ordered = nx.DiGraph()
    ordered.add_nodes_from(sorted(external))
    ordered.add_edges_from(sorted(external.edges()))
    exact = len(ordered) <= cfg.exact_max_nodes and ordered.number_of_edges() <= cfg.exact_max_edges
    sources = len(ordered) if exact else min(cfg.betweenness_sources, len(ordered))
    exact = sources == len(ordered)
    values = nx.betweenness_centrality(
        ordered, k=None if exact else sources, weight=None, seed=cfg.random_seed
    )
    return values, "exact" if exact else "sampled", sources


def observation_status(row: object, max_depth: int = 4) -> str:
    flags = []
    if row.in_deg + row.out_deg == 0:
        flags.append("no_external_operations")
    if row.self_transfer_n_tx:
        flags.append("self_transfers_excluded")
    if row.is_seed:
        flags.append("seed_inflows_incomplete")
    if row.depth == max_depth:
        flags.append("depth_outflows_incomplete")
    return ";".join(flags) if flags else "sample_only"


def analysis_features(
    graph: nx.DiGraph,
    features: pd.DataFrame,
    tx: pd.DataFrame,
    assignment: dict[int, int],
    config: AnalysisConfig | None = None,
) -> pd.DataFrame:
    cfg = config or AnalysisConfig()
    graph = external_graph(_ensure_tiyn(graph))
    frame = features.sort_values("gid").reset_index(drop=True).copy()
    frame["cluster_id"] = frame.gid.map(assignment).astype("int64")
    bridge, mode, sources = structural_betweenness(graph, cfg)
    frame["betweenness"] = frame.gid.map(bridge)
    frame["betweenness_mode"], frame["betweenness_sources"] = mode, sources
    frame["betweenness_percentile"] = positive_percentile(frame.betweenness)
    peers, external, largest_in, largest_out = {}, {}, {}, {}
    for gid in sorted(graph):
        neighbors = set(graph.predecessors(gid)) | set(graph.successors(gid))
        peers[gid] = len(neighbors)
        external[gid] = len({assignment[peer] for peer in neighbors} - {assignment[gid]})
        largest_in[gid] = max(
            (int(attrs["sum_tiyn"]) for _, _, attrs in graph.in_edges(gid, data=True)), default=0
        )
        largest_out[gid] = max(
            (int(attrs["sum_tiyn"]) for _, _, attrs in graph.out_edges(gid, data=True)), default=0
        )
    frame["n_peers"] = frame.gid.map(peers).astype("int64")
    frame["external_communities"] = frame.gid.map(external).astype("int64")
    for direction, mapping in [("in", largest_in), ("out", largest_out)]:
        frame[f"largest_{direction}_tiyn"] = pd.Series(
            [mapping[gid] for gid in frame.gid], dtype=object
        )
        frame[f"largest_{direction}_share"] = [
            int(largest) / int(total) if total else 0.0
            for largest, total in zip(
                frame[f"largest_{direction}_tiyn"], frame[f"{direction}_tiyn"]
            )
        ]
    if cfg.diagnostics:
        frame["temporal_out_share"] = frame.gid.map(
            temporal_support(tx, cfg.temporal_window_days)
        ).fillna(0.0)
    matched = amount_temporal_support(tx, cfg.temporal_window_days)
    for column in (
        "temporal_matched_tiyn",
        "same_day_uncertain_tiyn",
        "temporal_outgoing_tiyn",
        "temporal_matched_kzt",
        "temporal_matched_out_share",
        "same_day_uncertain_kzt",
        "same_day_uncertain_out_share",
        "temporal_unmatched_out_share",
    ):
        values = [matched.get(int(gid), {}).get(column, 0) for gid in frame.gid]
        frame[column] = pd.Series(values, dtype=object if column.endswith("_tiyn") else float)
    frame["seed_inflow_incomplete"] = frame.is_seed.astype(bool)
    frame["depth_outflow_incomplete"] = frame.depth.eq(cfg.input_profile.max_depth)
    frame["truncated_by_depth"] = frame.depth_outflow_incomplete & frame.out_deg.eq(0)
    frame["observation_completeness"] = [
        observation_status(row, cfg.input_profile.max_depth)
        for row in frame.itertuples(index=False)
    ]
    frame["turnover_tiyn"] = pd.Series(
        [
            int(incoming) + int(outgoing)
            for incoming, outgoing in zip(frame.in_tiyn, frame.out_tiyn)
        ],
        dtype=object,
    )
    frame["turnover_kzt"] = frame.turnover_tiyn.map(lambda value: int(value) / 100).astype(float)
    frame["operation_count"] = frame.in_tx + frame.out_tx
    return frame
