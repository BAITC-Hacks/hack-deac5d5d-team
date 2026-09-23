"""Independent review priority, its component contributions, and ranked output."""

import json

import pandas as pd

from contracts import AnalysisConfig
from features import positive_percentile
from rules import decide_role, format_tiyn, render_evidence
from validation import TOP_COLUMNS, ValidationError


def assign_roles_and_priorities(
    frame: pd.DataFrame, config: AnalysisConfig | None = None
) -> pd.DataFrame:
    cfg = config or AnalysisConfig()
    result = frame.copy()
    decisions = [decide_role(row, cfg) for row in result.itertuples(index=False)]
    result["role"] = [decision.role for decision in decisions]
    result["role_score"] = [decision.score for decision in decisions]
    result["role_score_uncapped"] = [decision.facts["score_uncapped"] for decision in decisions]
    result["role_score_cap"] = [decision.facts["score_cap"] for decision in decisions]
    result["evidence"] = [render_evidence(decision) for decision in decisions]
    result["role_facts"] = [
        json.dumps(decision.facts, ensure_ascii=False, sort_keys=True, allow_nan=False)
        for decision in decisions
    ]
    active = result.in_deg.add(result.out_deg).gt(0)
    priority = pd.Series(0.0, index=result.index)
    for column, weight in cfg.priority_weights:
        contribution = weight * positive_percentile(result[column].where(active, 0))
        result[f"priority_{column}"] = contribution
        priority += contribution
    result["priority_score"] = priority.clip(0, 1).round(6)
    required = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
    return result[required + [column for column in result if column not in required]]


def rank_nodes(roles: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    if top_n < 20:
        raise ValidationError("top_nodes: нужно запросить минимум 20 клиентов")
    leaders = roles.sort_values(["priority_score", "gid"], ascending=[False, True]).head(top_n)
    top = leaders[["gid", "role", "priority_score"]].copy()
    top.insert(0, "rank", range(1, len(top) + 1))
    explanations = []
    for row in leaders.itertuples(index=False):
        text = (
            f"Приоритет={row.priority_score:.6f}; внешний оборот={format_tiyn(row.turnover_tiyn)} KZT; "
            f"посредничество={row.betweenness:.5f}; PageRank={row.pagerank:.5f}; "
            f"контрагентов={row.n_peers}; внешних операций={row.operation_count}"
        )
        if row.truncated_by_depth:
            text += f"; глубина={row.depth}, исходящий поток ограничен выборкой"
        if row.operation_count == 0:
            text += "; нет внешних операций для приоритизации"
        if row.self_transfer_n_tx:
            text += f"; самопереводов={row.self_transfer_n_tx}, в приоритет не включены"
        if row.seed_inflow_incomplete:
            text += "; seed: входящие потоки неполны"
            if row.role_score_cap < 1:
                text += f"; уверенность роли ограничена {row.role_score_cap:.2f}"
        explanations.append(text)
    top["why"] = explanations
    return top.loc[:, list(TOP_COLUMNS)].reset_index(drop=True)
