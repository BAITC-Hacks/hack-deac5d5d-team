"""Deterministic structural hypotheses; no labels or guilt probabilities.

The interface can consume the team's validated CSVs instead of this baseline.
See README.md for the thresholds, score formula and limitations.
"""

import json
from decimal import Decimal

import networkx as nx
import numpy as np
import pandas as pd


def normalize(values):
    """Log scaling with zero preserved, including the all-zero case."""
    values = np.log1p(values.astype(float))
    maximum = values.max()
    return values / maximum if maximum > 0 else values * 0


def assign_role(row):
    """First matching rule wins. role_score measures rule strength only."""
    incoming, outgoing = row.in_deg, row.out_deg
    if row.truncated_by_depth:
        return "peripheral", 0.2, "Колено 4; исходящих 0: граница наблюдения, роль неопределённа."
    if incoming + outgoing == 0:
        return "peripheral", 0.1, "Связей 0; данных для финансовой роли недостаточно."
    if (incoming >= 3 and outgoing >= 3 and row.seed_reach >= 2
            and row.neighbor_clusters >= 2 and row.betweenness > 0):
        strength = min(incoming, outgoing) / 10 + min(row.seed_reach, 5) / 10
        return "coordinator", round(min(0.9, 0.45 + 0.3 * strength), 4), (
            f"Вход/выход: {incoming}/{outgoing}; seed≤4: {row.seed_reach}; "
            f"кластеров соседей: {row.neighbor_clusters}; посредничество {row.betweenness:.5f}.")
    if incoming >= 3 and row.pass_through <= 0.5:
        return "consolidator", round(min(0.9, 0.5 + 0.2 * min(incoming / 10, 1)
                                        + 0.2 * (1 - row.pass_through)), 4), (
            f"Отправителей {incoming}; вход {row.in_kzt:,.0f} ₸; "
            f"выход {row.out_kzt:,.0f} ₸ ({row.pass_through:.0%} входа).")
    if outgoing >= 5 and outgoing >= 2 * incoming:
        return "distributor", round(min(0.9, 0.5 + 0.4 * min(outgoing / 20, 1)), 4), (
            f"Получателей {outgoing}, отправителей {incoming}; "
            f"исходящих операций {row.out_tx}; сумма {row.out_kzt:,.0f} ₸.")
    if incoming > 0 and outgoing > 0 and 0.8 <= row.pass_through <= 1.2:
        return "transit", round(0.5 + 0.3 * (1 - abs(1 - row.pass_through) / 0.2), 4), (
            f"Вход {row.in_kzt:,.0f} ₸; выход {row.out_kzt:,.0f} ₸; "
            f"отношение {row.pass_through:.2f}; порядок переводов не проверен.")
    if incoming > 0 and outgoing == 0:
        return "terminal", 0.55, (
            f"Вход {row.in_kzt:,.0f} ₸ от {incoming}; исходящих 0; "
            f"колено {row.depth}. Гипотеза в пределах выгрузки.")
    return "peripheral", 0.3, (
        f"Входящих связей {incoming}, исходящих {outgoing}; "
        "пороги других ролей не достигнуты, роль неопределённа.")


def analyze(graph, features, top_count=50):
    if len(features) < 20:
        raise ValueError("Для топа минимум из 20 клиентов нужны минимум 20 узлов")
    if top_count < 20:
        raise ValueError("--top должен быть не меньше 20")
    # Sorted insertion order keeps community numbering and random sampling stable.
    projection = nx.Graph()
    projection.add_nodes_from(sorted(graph))
    for src, dst, attrs in sorted(graph.edges(data=True)):
        old = projection.get_edge_data(src, dst, {}).get("weight", 0.0)
        projection.add_edge(src, dst, weight=old + attrs["sum_kzt"])
    if projection.number_of_edges():
        communities = nx.community.louvain_communities(
            projection, weight="weight", resolution=1.0, threshold=1e-7, seed=42)
    else:
        communities = [{gid} for gid in projection]
    communities = sorted(communities, key=lambda group: (-len(group), min(group)))
    assignment = {gid: index for index, group in enumerate(communities) for gid in group}
    frame = features.sort_values("gid").reset_index(drop=True).copy()
    frame["cluster_id"] = frame.gid.map(assignment).astype("int64")
    between = nx.betweenness_centrality(graph, weight=None, normalized=True)
    frame["betweenness"] = frame.gid.map(between)
    seed_reach = dict.fromkeys(graph, 0)
    for gid in sorted(frame.loc[frame.is_seed, "gid"]):
        for target in nx.single_source_shortest_path_length(graph, gid, cutoff=4):
            if target != gid:
                seed_reach[target] += 1
    frame["seed_reach"] = frame.gid.map(seed_reach).astype("int64")
    frame["neighbor_clusters"] = frame.gid.map(lambda gid: len({
        assignment[other] for other in set(graph.predecessors(gid)) | set(graph.successors(gid))
    })).astype("int64")
    frame["in_concentration"] = frame.gid.map(lambda gid: max(
        (attrs["sum_kzt"] for _, _, attrs in graph.in_edges(gid, data=True)), default=0))
    frame["in_concentration"] = frame.in_concentration / frame.in_kzt.replace(0, np.nan)
    frame["in_concentration"] = frame.in_concentration.fillna(0)

    # Structural importance, deliberately independent of role confidence.
    frame["priority_score"] = (
        0.35 * normalize(frame.in_kzt + frame.out_kzt)
        + 0.25 * normalize(frame.in_deg + frame.out_deg)
        + 0.25 * normalize(frame.seed_reach)
        + 0.15 * (frame.betweenness / frame.betweenness.max()
                  if frame.betweenness.max() > 0 else frame.betweenness * 0)
    ).clip(0, 1).round(6)
    assignments = [assign_role(row) for row in frame.itertuples(index=False)]
    frame["role"] = [item[0] for item in assignments]
    frame["role_score"] = [item[1] for item in assignments]
    frame["evidence"] = [item[2] for item in assignments]
    frame["priority_reason"] = [
        f"Оборот {r.in_kzt + r.out_kzt:,.0f} ₸; связи {r.in_deg + r.out_deg}; "
        f"seed≤4: {r.seed_reach}; посредничество {r.betweenness:.5f}."
        for r in frame.itertuples(index=False)]
    ranked = frame.sort_values(["priority_score", "gid"], ascending=[False, True])
    top = ranked.head(top_count)[["gid", "role", "priority_score", "priority_reason"]].copy()
    top = top.rename(columns={"priority_reason": "why"})
    top.insert(0, "rank", range(1, len(top) + 1))

    internal_cents = dict.fromkeys(range(len(communities)), 0)
    for src, dst, attrs in graph.edges(data=True):
        if assignment[src] == assignment[dst]:
            internal_cents[assignment[src]] += int(Decimal(str(attrs["sum_kzt"])) * 100)
    seeds = set(frame.loc[frame.is_seed, "gid"])
    clusters = pd.DataFrame([
        {"cluster_id": index, "n_nodes": len(group), "n_seed": len(group & seeds),
         "sum_kzt_internal": internal_cents[index] / 100,
         "top_gids": json.dumps([str(gid) for gid in ranked.loc[ranked.gid.isin(group), "gid"].head(3)]),
         "hypothesis": f"Сообщество потоков: {len(group)} клиентов, {len(group & seeds)} seed. "
                       "Louvain, веса — суммы. Не доказательство общей организации."}
        for index, group in enumerate(communities)])
    return frame, clusters, top
