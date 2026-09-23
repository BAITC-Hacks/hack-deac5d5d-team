"""Connected, canonical communities with a replaceable proposal strategy."""

import json
from collections.abc import Callable, Iterable

import networkx as nx
import pandas as pd

from .contracts import AnalysisConfig
from .validation import CLUSTER_COLUMNS, ValidationError, money_cents

CommunityStrategy = Callable[[nx.Graph, AnalysisConfig], Iterable[Iterable[int]]]


def undirected_projection(graph: nx.DiGraph) -> nx.Graph:
    """Sum both directions; retain all clients and exclude self transfers.

    Integer tiyn remain exact where available. Float KZT weights are only used
    by the numerical community optimizer, never by financial threshold rules.
    """
    projection = nx.Graph()
    projection.add_nodes_from(sorted(graph))
    for src, dst, attrs in sorted(graph.edges(data=True)):
        if src == dst:
            continue
        previous = projection.get_edge_data(src, dst, {})
        total_kzt = previous.get("sum_kzt", 0.0) + attrs["sum_kzt"]
        attributes = {"sum_kzt": total_kzt}
        # Carry exact amounts only when every contributing edge has them.
        if "sum_tiyn" in attrs and (not previous or "sum_tiyn" in previous):
            attributes["sum_tiyn"] = previous.get("sum_tiyn", 0) + int(attrs["sum_tiyn"])
        if projection.has_edge(src, dst):
            projection[src][dst].clear()
        projection.add_edge(src, dst, **attributes)
    return projection


def louvain_strategy(projection: nx.Graph, config: AnalysisConfig) -> Iterable[set[int]]:
    """Propose communities for the active graph in canonical input order."""
    return nx.community.louvain_communities(
        projection,
        weight="sum_kzt",
        resolution=config.community_resolution,
        seed=config.random_seed,
    )


def assign_clusters(
    graph: nx.DiGraph,
    config: AnalysisConfig | None = None,
    strategy: CommunityStrategy | None = None,
) -> dict[int, int]:
    """Validate a partition, split disconnected groups, then number by min GID.

    The strategy receives the undirected graph after isolates are removed.
    Its groups must cover each active node exactly once. Isolates, including
    self-transfer-only clients, always receive their own singleton community.
    Splitting occurs before any downstream structural features are calculated.
    """
    config = config if config is not None else AnalysisConfig()
    projection = undirected_projection(graph)
    isolated = set(nx.isolates(projection))
    active = projection.subgraph(sorted(set(projection) - isolated)).copy()
    proposed = (strategy or louvain_strategy)(active, config) if active.number_of_edges() else []
    active_nodes = set(active)
    seen: set[int] = set()
    connected = []
    for proposed_group in proposed:
        members = list(proposed_group)
        group = set(members)
        if not group:
            raise ValidationError("communities: empty group")
        if len(group) != len(members) or seen.intersection(group):
            raise ValidationError("communities: duplicate node assignment")
        if not group <= active_nodes:
            raise ValidationError("communities: unknown or isolated node in active partition")
        seen.update(group)
        connected.extend(set(part) for part in nx.connected_components(active.subgraph(group)))
    if seen != active_nodes:
        raise ValidationError("communities: incomplete node coverage")
    connected.extend({gid} for gid in isolated)
    connected.sort(key=min)
    return {gid: cluster_id for cluster_id, group in enumerate(connected) for gid in sorted(group)}


def summarize_clusters(roles: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    assignment = roles.set_index("gid").cluster_id.to_dict()
    sums = dict.fromkeys(assignment.values(), 0)
    for edge, cents in zip(
        edges.itertuples(index=False), money_cents(edges.sum_kzt, "edges.sum_kzt", positive=True)
    ):
        if assignment[edge.src] == assignment[edge.dst]:
            sums[assignment[edge.src]] += cents
    records = []
    for cluster_id, group in roles.groupby("cluster_id", sort=True):
        leaders = group.sort_values(["priority_score", "gid"], ascending=[False, True]).head(5)
        counts = group.role.value_counts()
        mix = ", ".join(f"{role}={int(counts[role])}" for role in sorted(counts.index))
        limited = int(group.truncated_by_depth.sum())
        if group.operation_count.eq(0).all():
            hypothesis = f"Гипотеза внешней группы не определена: внешних операций=0; самопереводов={int(group.self_transfer_n_tx.sum())}"
        else:
            if counts.get("consolidator", 0) and counts.get("distributor", 0):
                purpose = "сбор и распределение средств внутри сообщества"
            elif counts.get("transit", 0) > 0:
                purpose = "сообщество с промежуточными переводами"
            elif counts.get("terminal", 0) >= len(group) / 2:
                purpose = "преимущественно получение средств в наблюдаемой выборке"
            elif counts.get("coordinator", 0) > 0:
                purpose = "сообщество со связями между группами клиентов"
            else:
                purpose = "смешанная структура переводов; единое назначение не установлено"
            hypothesis = (
                f"Гипотеза: {purpose}; {mix}; "
                f"граница глубины=4 у {limited}; роли описывают наблюдаемые потоки"
            )
        records.append(
            {
                "cluster_id": cluster_id,
                "n_nodes": len(group),
                "n_seed": int(group.is_seed.sum()),
                "sum_kzt_internal": sums[cluster_id] / 100,
                "top_gids": json.dumps([str(gid) for gid in leaders.gid]),
                "hypothesis": hypothesis,
            }
        )
    return pd.DataFrame(records, columns=CLUSTER_COLUMNS)
