"""Deterministic, interpretable graph hypotheses; see METHODOLOGY.md."""

import json
from collections import deque

import networkx as nx
import numpy as np
import pandas as pd

from validation import CLUSTER_COLUMNS, TOP_COLUMNS, ValidationError, money_cents


RANDOM_SEED = 42
EXACT_MAX_NODES = 5000
EXACT_MAX_EDGES = 50000
BETWEENNESS_SOURCES = 256
SEED_FLOW_SCORE_CAP = 0.65


def undirected_projection(graph):
    """Sum both directions, retaining isolated clients and canonical ordering."""
    projection = nx.Graph()
    projection.add_nodes_from(sorted(graph))
    for src, dst, attrs in sorted(graph.edges(data=True)):
        old = projection.get_edge_data(src, dst, {}).get("sum_kzt", 0.0)
        projection.add_edge(src, dst, sum_kzt=old + attrs["sum_kzt"])
    return projection


def assign_clusters(graph):
    projection = undirected_projection(graph)
    isolated = set(nx.isolates(projection))
    active = projection.subgraph(sorted(set(projection) - isolated)).copy()
    communities = ([set(group) for group in nx.community.louvain_communities(
        active, weight="sum_kzt", resolution=1.0, seed=RANDOM_SEED)]
        if active.number_of_edges() else [])
    communities.extend({gid} for gid in isolated)
    communities.sort(key=min)
    return {gid: cluster_id for cluster_id, group in enumerate(communities) for gid in group}


def positive_percentile(values):
    """Zero stays zero; positive observations receive an average percentile rank."""
    result = pd.Series(0.0, index=values.index)
    positive = values > 0
    result.loc[positive] = values.loc[positive].rank(method="average", pct=True)
    return result


def temporal_support(tx):
    """Outgoing amount with an observed incoming operation 1–7 days earlier.

    Dates within one day have unknown order. No matching of the same funds is
    claimed, and self transfers cannot support a transit hypothesis.
    """
    dated = tx.loc[tx.src.ne(tx.dst), ["src", "dst", "date", "sum_kzt"]].copy()
    dated["day"] = pd.to_datetime(dated.date).dt.normalize()
    dated = dated.sort_values(["src", "dst", "day", "sum_kzt"])
    incoming = {gid: np.sort(group.day.to_numpy()) for gid, group in dated.groupby("dst")}
    result = {}
    for gid, outgoing in dated.groupby("src"):
        dates = incoming.get(gid)
        if dates is None:
            result[gid] = 0.0
            continue
        days = outgoing.day.to_numpy()
        previous = np.searchsorted(dates, days, side="left") - 1
        valid = previous >= 0
        lag = days - dates[np.maximum(previous, 0)]
        supported = valid & (lag <= np.timedelta64(7, "D"))
        result[gid] = float(outgoing.loc[supported, "sum_kzt"].sum() / outgoing.sum_kzt.sum())
    return result


def amount_temporal_support(tx):
    """FIFO in integer tiyn; same-day coverage is uncertain and also consumed.

    Expire receipts older than seven calendar days. Allocate earlier receipts
    first, then consume possible same-day coverage without calling it support.
    Only unused same-day receipts are available on subsequent days. This is a
    matching convention for observed flows, not a reconstructed account balance.
    """
    dated = tx.loc[tx.src.ne(tx.dst), ["src", "dst", "date", "sum_kzt"]].copy()
    dated["day"] = pd.to_datetime(dated.date).dt.date
    dated["cents"] = money_cents(dated.sum_kzt, "transactions.sum_kzt", positive=True)
    incoming, outgoing = {}, {}
    for row in dated.itertuples(index=False):
        for mapping, gid in ((incoming, row.dst), (outgoing, row.src)):
            daily = mapping.setdefault(gid, {})
            daily[row.day] = daily.get(row.day, 0) + row.cents
    result = {}
    for gid in sorted(set(incoming) | set(outgoing)):
        credits, debits = incoming.get(gid, {}), outgoing.get(gid, {})
        available = deque()
        matched = uncertain = 0
        for day in sorted(set(credits) | set(debits)):
            while available and (day - available[0][0]).days > 7:
                available.popleft()
            remaining = debits.get(day, 0)
            while remaining and available:
                used = min(remaining, available[0][1])
                matched += used
                remaining -= used
                available[0][1] -= used
                if available[0][1] == 0:
                    available.popleft()
            same_day = min(remaining, credits.get(day, 0))
            uncertain += same_day
            unused_credit = credits.get(day, 0) - same_day
            if unused_credit:
                available.append([day, unused_credit])
        total = sum(debits.values())
        result[gid] = {
            "temporal_matched_kzt": matched / 100,
            "temporal_matched_out_share": matched / total if total else 0.0,
            "same_day_uncertain_kzt": uncertain / 100,
            "same_day_uncertain_out_share": uncertain / total if total else 0.0,
            "temporal_unmatched_out_share": (total - matched - uncertain) / total if total else 0.0,
        }
    return result


def structural_betweenness(graph):
    """Exact on small graphs; deterministic source sampling above either limit."""
    ordered = nx.DiGraph()
    ordered.add_nodes_from(sorted(graph))
    ordered.add_edges_from(sorted(graph.edges()))
    exact = (len(ordered) <= EXACT_MAX_NODES
             and ordered.number_of_edges() <= EXACT_MAX_EDGES)
    sources = len(ordered) if exact else min(BETWEENNESS_SOURCES, len(ordered))
    values = nx.betweenness_centrality(
        ordered, k=None if exact else sources, weight=None, seed=RANDOM_SEED)
    return values, "exact" if exact else "sampled", sources


def observation_status(row):
    """Describe known blind spots without claiming measured completeness."""
    flags = []
    if row.in_deg + row.out_deg == 0:
        flags.append("no_operations")
    if row.is_seed:
        flags.append("seed_inflows_incomplete")
    if row.depth == 4:
        flags.append("depth_outflows_incomplete")
    return ";".join(flags) if flags else "sample_only"


def analysis_features(graph, features, tx, assignment):
    frame = features.sort_values("gid").reset_index(drop=True).copy()
    frame["cluster_id"] = frame.gid.map(assignment).astype("int64")
    bridge, mode, sources = structural_betweenness(graph)
    frame["betweenness"] = frame.gid.map(bridge)
    frame["betweenness_mode"] = mode
    frame["betweenness_sources"] = sources
    frame["betweenness_percentile"] = positive_percentile(frame.betweenness)
    peers, external, in_share, out_share = {}, {}, {}, {}
    for gid in sorted(graph):
        neighbors = (set(graph.predecessors(gid)) | set(graph.successors(gid))) - {gid}
        peers[gid] = len(neighbors)
        external[gid] = len({assignment[n] for n in neighbors} - {assignment[gid]})
        incoming = [attrs["sum_kzt"] for _, _, attrs in graph.in_edges(gid, data=True)]
        outgoing = [attrs["sum_kzt"] for _, _, attrs in graph.out_edges(gid, data=True)]
        in_share[gid] = max(incoming) / sum(incoming) if incoming else 0.0
        out_share[gid] = max(outgoing) / sum(outgoing) if outgoing else 0.0
    frame["n_peers"] = frame.gid.map(peers).astype("int64")
    frame["external_communities"] = frame.gid.map(external).astype("int64")
    frame["largest_in_share"] = frame.gid.map(in_share)
    frame["largest_out_share"] = frame.gid.map(out_share)
    frame["temporal_out_share"] = frame.gid.map(temporal_support(tx)).fillna(0.0)
    matched = amount_temporal_support(tx)
    for column in ("temporal_matched_kzt", "temporal_matched_out_share", "same_day_uncertain_kzt",
                   "same_day_uncertain_out_share", "temporal_unmatched_out_share"):
        frame[column] = frame.gid.map({gid: values[column] for gid, values in matched.items()}).fillna(0.0)
    frame["seed_inflow_incomplete"] = frame.is_seed.astype(bool)
    frame["depth_outflow_incomplete"] = frame.depth.eq(4)
    frame["observation_completeness"] = [observation_status(row) for row in frame.itertuples(index=False)]
    frame["turnover_kzt"] = frame.in_kzt + frame.out_kzt
    frame["operation_count"] = frame.in_tx + frame.out_tx
    return frame


def role_hypothesis(row):
    """First matching rule wins. Scores measure rule support, not probability."""
    if row.in_deg + row.out_deg == 0:
        return "peripheral", 0.0, "Связей=0, операций=0; изолирован, данных для роли нет"
    if row.truncated_by_depth:
        return ("peripheral", 0.2,
                f"Вход={row.in_deg}, {row.in_kzt:.2f} KZT; выход=0; глубина=4: обход обрезан, конечная роль неизвестна")
    if (row.in_deg >= 2 and row.out_deg >= 2 and row.n_peers >= 5
            and row.external_communities >= 2 and row.betweenness_percentile >= 0.9):
        score = min(0.9, 0.55 + 0.2 * row.betweenness_percentile
                    + 0.15 * min(row.external_communities / 4, 1))
        return ("coordinator", score,
                f"Гипотеза связующего: соседей={row.n_peers}, внешних сообществ={row.external_communities}; "
                f"посредничество={row.betweenness:.5f}; не доказательство управления")
    if (row.in_deg >= 3 and row.pass_through <= 0.35 and row.largest_in_share <= 0.8):
        score = min(0.95, 0.55 + 0.2 * min(row.in_deg / 10, 1)
                    + 0.15 * (1 - row.pass_through) + 0.1 * (1 - row.largest_in_share))
        return ("consolidator", score,
                f"Гипотеза сбора: отправителей={row.in_deg}; вход={row.in_kzt:.2f} KZT; "
                f"выход/вход={row.pass_through:.2f}; крупнейший вход={row.largest_in_share:.0%}")
    if (row.out_deg >= 3 and row.out_deg >= 2 * max(row.in_deg, 1)
            and (row.in_deg == 0 or row.pass_through >= 1.5) and row.largest_out_share <= 0.8):
        score = min(0.9, 0.55 + 0.2 * min(row.out_deg / 10, 1)
                    + 0.15 * (1 - row.largest_out_share))
        return ("distributor", score,
                f"Гипотеза распределения: входов={row.in_deg}, получателей={row.out_deg}; "
                f"выход={row.out_kzt:.2f} KZT; крупнейший выход={row.largest_out_share:.0%}; источник средств неизвестен")
    if (row.in_deg > 0 and row.out_deg > 0 and 0.65 <= row.pass_through <= 1.35
            and row.temporal_matched_out_share >= 0.5):
        balance = min(row.in_kzt, row.out_kzt) / max(row.in_kzt, row.out_kzt)
        score = 0.45 + 0.10 * balance + 0.35 * row.temporal_matched_out_share
        return ("transit", score,
                f"Гипотеза транзита: вход={row.in_kzt:.2f}, выход={row.out_kzt:.2f} KZT; "
                f"FIFO 1–7д={row.temporal_matched_out_share:.0%}; в тот же день=?{row.same_day_uncertain_out_share:.0%}; те же деньги не установлены")
    if row.in_deg > 0 and row.out_deg == 0:
        return ("terminal", min(0.75, 0.55 + 0.04 * row.in_deg),
                f"Наблюдаемый получатель: входов={row.in_deg}, вход={row.in_kzt:.2f} KZT; "
                f"выход=0; глубина={row.depth}; вне выборки операции неизвестны")
    sparse = row.n_peers <= 2
    score = 0.55 if sparse else 0.3
    return ("peripheral", score,
            f"{'Мало связей' if sparse else 'Роль неоднозначна'}: входов={row.in_deg}, выходов={row.out_deg}; "
            f"оборот={row.turnover_kzt:.2f} KZT; FIFO 1–7д={row.temporal_matched_out_share:.0%}; "
            f"в тот же день=?{row.same_day_uncertain_out_share:.0%}; "
            "правила иных ролей не выполнены")


def assign_roles_and_priorities(frame):
    result = frame.copy()
    hypotheses = [role_hypothesis(row) for row in result.itertuples(index=False)]
    result["role"] = [item[0] for item in hypotheses]
    result["role_score_uncapped"] = [round(item[1], 6) for item in hypotheses]
    flow_roles = result.role.isin(["transit", "consolidator", "distributor", "terminal"])
    result["role_score_cap"] = np.where(result.seed_inflow_incomplete & flow_roles, SEED_FLOW_SCORE_CAP, 1.0)
    result["role_score"] = result[["role_score_uncapped", "role_score_cap"]].min(axis=1).round(6)
    result["evidence"] = [item[2] for item in hypotheses]
    seed = result.seed_inflow_incomplete
    result.loc[seed, "evidence"] += "; seed: вход неполон"
    # Independent of role and role_score; PageRank teleportation cannot rank isolates.
    active = result.in_deg.add(result.out_deg).gt(0)
    priority = pd.Series(0.0, index=result.index)
    for column, weight in (("turnover_kzt", 0.30), ("betweenness", 0.25),
                           ("pagerank", 0.20), ("n_peers", 0.15), ("operation_count", 0.10)):
        priority += weight * positive_percentile(result[column].where(active, 0.0))
    result["priority_score"] = priority.clip(0, 1).round(6)
    required = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
    return result[required + [column for column in result if column not in required]]


def summarize_clusters(roles, edges):
    assignment = roles.set_index("gid").cluster_id.to_dict()
    sums = dict.fromkeys(assignment.values(), 0)
    for edge, cents in zip(edges.itertuples(index=False), money_cents(edges.sum_kzt, "edges.sum_kzt", positive=True)):
        if assignment[edge.src] == assignment[edge.dst]:
            sums[assignment[edge.src]] += cents
    records = []
    for cluster_id, group in roles.groupby("cluster_id", sort=True):
        leaders = group.sort_values(["priority_score", "gid"], ascending=[False, True]).head(5)
        counts = group.role.value_counts()
        mix = ", ".join(f"{role}={int(counts[role])}" for role in sorted(counts.index))
        limited = int(group.truncated_by_depth.sum())
        if group.operation_count.eq(0).all():
            hypothesis = "Гипотеза не определена: изолированный клиент, наблюдаемых операций=0"
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
            hypothesis = (f"Гипотеза: {purpose}; {mix}; "
                          f"граница глубины=4 у {limited}; роли описывают наблюдаемые потоки")
        records.append({"cluster_id": cluster_id, "n_nodes": len(group),
                        "n_seed": int(group.is_seed.sum()), "sum_kzt_internal": sums[cluster_id] / 100,
                        "top_gids": json.dumps([str(gid) for gid in leaders.gid]), "hypothesis": hypothesis})
    return pd.DataFrame(records, columns=CLUSTER_COLUMNS)


def rank_nodes(roles, top_n=20):
    if top_n < 20:
        raise ValidationError("top_nodes: нужно запросить минимум 20 клиентов")
    leaders = roles.sort_values(["priority_score", "gid"], ascending=[False, True]).head(top_n)
    top = leaders[["gid", "role", "priority_score"]].copy()
    top.insert(0, "rank", range(1, len(top) + 1))
    explanations = []
    for row in leaders.itertuples(index=False):
        text = (f"Приоритет={row.priority_score:.6f}; оборот={row.turnover_kzt:.2f} KZT; "
                f"посредничество={row.betweenness:.5f}; PageRank={row.pagerank:.5f}; "
                f"контрагентов={row.n_peers}; операций={row.operation_count}")
        if row.truncated_by_depth:
            text += "; глубина=4, исходящий поток не наблюдается полностью"
        elif row.operation_count == 0:
            text += "; изолирован, нет операций для приоритизации"
        if row.seed_inflow_incomplete:
            text += "; seed: входящие потоки неполны"
            if row.role_score_cap < 1:
                text += f"; уверенность роли ограничена {row.role_score_cap:.2f}"
        explanations.append(text)
    top["why"] = explanations
    return top.loc[:, list(TOP_COLUMNS)].reset_index(drop=True)


def analyze(graph, features, edges, tx):
    if len(features) < 20:
        raise ValidationError("Для обязательного топа из 20 уникальных клиентов нужно минимум 20 узлов")
    assignment = assign_clusters(graph)
    enriched = analysis_features(graph, features, tx, assignment)
    roles = assign_roles_and_priorities(enriched)
    return roles, summarize_clusters(roles, edges), rank_nodes(roles)
