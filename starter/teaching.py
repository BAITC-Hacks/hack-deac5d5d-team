"""Optional educational helpers; never called by the production pipeline."""

from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from validation import CLUSTER_COLUMNS, TOP_COLUMNS, ValidationError


def write_templates(df: pd.DataFrame, out_dir: Path):
    """Учебные шаблоны. Строгую проверку готового результата НЕ проходят."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. nodes_roles.csv — схема из ТЗ, роли не заполнены
    roles = df[["gid"]].copy()
    roles["role"] = ""  # TODO: одна из ROLES
    roles["role_score"] = 0.0  # TODO: 0..1
    roles["cluster_id"] = -1  # TODO: номер кластера
    roles["priority_score"] = 0.0  # TODO: 0..1
    roles["evidence"] = ""  # TODO: почему — с числами, до 200 символов
    roles = roles.merge(
        df[
            [
                "gid",
                "in_deg",
                "out_deg",
                "in_kzt",
                "out_kzt",
                "pagerank",
                "pass_through",
                "depth",
                "is_seed",
                "truncated_by_depth",
            ]
        ],
        on="gid",
        how="left",
    )
    roles.to_csv(out_dir / "nodes_roles.csv", index=False)

    # 2. clusters.csv — пустой каркас
    pd.DataFrame(columns=CLUSTER_COLUMNS).to_csv(out_dir / "clusters.csv", index=False)

    # 3. top_nodes.csv — пустой каркас, нужно ≥20 строк
    pd.DataFrame(columns=TOP_COLUMNS).to_csv(out_dir / "top_nodes.csv", index=False)

    print(f"ШАБЛОНЫ записаны в {out_dir}/; это НЕ готовое решение, строгая проверка их отклонит.")


def betweenness_features(G: nx.DiGraph, distance_mode="hops", sample_size=None, seed=42):
    """Explicit distance choice. Amount strength is not a shortest-path length."""
    if distance_mode == "hops":
        graph, weight = G, None
    elif distance_mode == "inverse_amount":
        graph, weight = G.copy(), "distance"
        for _, _, attrs in graph.edges(data=True):
            amount = attrs["sum_kzt"]
            if not np.isfinite(amount) or amount <= 0:
                raise ValidationError("inverse_amount requires finite positive sums")
            attrs[weight] = 1.0 / amount
    else:
        raise ValueError("distance_mode must be 'hops' or 'inverse_amount'")
    return nx.betweenness_centrality(graph, k=sample_size, weight=weight, seed=seed)


def hints(G: nx.DiGraph, df: pd.DataFrame):
    """Куда смотреть дальше. Ответов здесь нет — только направления."""
    print("\nС ЧЕГО НАЧАТЬ")
    print("-" * 64)
    print(f"  узлов, получающих от 3+ разных плательщиков : {(df.in_deg >= 3).sum()}")
    print(f"  узлов, рассылающих на 10+ получателей       : {(df.out_deg >= 10).sum()}")
    print(
        f"  узлов и с входом, и с выходом               : {((df.in_deg > 0) & (df.out_deg > 0)).sum()}"
    )
    print(
        f"  узлов, обрезанных 4-м коленом               : {df.truncated_by_depth.sum()}  <- разберитесь"
    )
    print(
        f"  слабосвязных компонент                      : {nx.number_weakly_connected_components(G)}"
    )
    print("""
  Вопросы, на которые стоит ответить метриками:
    * чем «деньги пришли и остались» отличается от «пришли и ушли дальше»?
    * что важнее для роли — количество плательщиков или сумма?
    * узел собирает средства от нескольких SEED — это случайность или структура?
    * если убрать узел, сеть распадётся или переживёт?

  Полезное в networkx: pagerank, hits, betweenness_centrality,
  community.louvain_communities, simple_cycles, all_simple_paths.
  Не забудьте: граф НАПРАВЛЕННЫЙ и ВЗВЕШЕННЫЙ.
  Для betweenness сумма — НЕ расстояние. Используйте betweenness_features:
  hops (по числу шагов) или inverse_amount (явная гипотеза: большой поток ближе).
  Ни один вариант не доказывает движение тех же денег; нужны даты переводов.
""")
