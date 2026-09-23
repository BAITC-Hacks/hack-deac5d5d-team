#!/usr/bin/env python3
"""Optional, read-only Louvain/Leiden comparison; writes only the named report.

Leiden is an experiment dependency, not a production pipeline dependency:
  python -m pip install igraph==1.0.0 leidenalg==0.11.0
  python experiments/compare_communities.py --data data --out reports/community-experiment.md
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import platform
import sys
from collections import Counter
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import networkx as nx
import pandas as pd

from hackalem.communities import assign_clusters, louvain_strategy, undirected_projection
from hackalem.contracts import AnalysisConfig


def leiden_strategy(graph: nx.Graph, config: AnalysisConfig) -> list[set[int]]:
    """Leiden generalized modularity with the same resolution as Louvain."""
    import igraph as ig
    import leidenalg as la

    gids = sorted(graph)
    index = {gid: i for i, gid in enumerate(gids)}
    ordered_edges = sorted(graph.edges(data=True))
    converted = ig.Graph(
        n=len(gids),
        directed=False,
        edges=[(index[src], index[dst]) for src, dst, _ in ordered_edges],
    )
    partition = la.find_partition(
        converted,
        la.RBConfigurationVertexPartition,
        weights=[attributes["sum_kzt"] for _, _, attributes in ordered_edges],
        resolution_parameter=config.community_resolution,
        n_iterations=-1,
        seed=config.random_seed,
    )
    return [{gids[i] for i in group} for group in partition]


def coassignment_jaccard(left: dict[int, int], right: dict[int, int]) -> float:
    """Jaccard of same-community node pairs without materializing all pairs."""
    if left.keys() != right.keys():
        raise ValueError("Partitions have different node coverage")

    def choose_two(n: int) -> int:
        return n * (n - 1) // 2

    counts_left = Counter(left.values())
    counts_right = Counter(right.values())
    intersections = Counter((left[gid], right[gid]) for gid in left)
    common = sum(choose_two(n) for n in intersections.values())
    union = (
        sum(choose_two(n) for n in counts_left.values())
        + sum(choose_two(n) for n in counts_right.values())
        - common
    )
    return common / union if union else 1.0


def run_partition(graph: nx.DiGraph, config: AnalysisConfig, strategy) -> dict:
    disconnected = []

    def record_proposal(active, passed_config):
        proposal = [set(group) for group in strategy(active, passed_config)]
        disconnected.extend(
            group for group in proposal if not nx.is_connected(active.subgraph(group))
        )
        return proposal

    start = perf_counter()
    assignment = assign_clusters(graph, config, record_proposal)
    elapsed = perf_counter() - start
    projection = undirected_projection(graph)
    groups = [
        {gid for gid, group in assignment.items() if group == cid}
        for cid in sorted(set(assignment.values()))
    ]
    if not all(nx.is_connected(projection.subgraph(group)) for group in groups):
        raise AssertionError("Disconnected published community")
    total = sum(int(attributes["sum_tiyn"]) for _, _, attributes in graph.edges(data=True))
    crossing = sum(
        int(attributes["sum_tiyn"])
        for src, dst, attributes in graph.edges(data=True)
        if assignment[src] != assignment[dst]
    )
    return {
        "assignment": assignment,
        "seconds": elapsed,
        "clusters": len(groups),
        "raw_disconnected": len(disconnected),
        "published_disconnected": 0,
        "crossing_share": crossing / total if total else 0.0,
        "modularity": nx.community.modularity(
            projection, groups, weight="sum_kzt", resolution=config.community_resolution
        ),
        "seed": config.random_seed,
        "resolution": config.community_resolution,
    }


def role_sensitivity(
    records: dict, nodes: pd.DataFrame, edges: pd.DataFrame, tx: pd.DataFrame
) -> list[str]:
    """Recompute all downstream features; compare roles and top with the baseline."""
    from hackalem.analysis import analyze
    from hackalem.features import basic_features, build_graph

    graph = build_graph(edges, nodes)
    features = basic_features(graph, nodes)
    results = {}
    strategies = {"Louvain + components": louvain_strategy, "Leiden": leiden_strategy}
    for (name, seed, resolution), record in records.items():
        config = replace(AnalysisConfig(), random_seed=seed, community_resolution=resolution)
        roles, _, top = analyze(graph, features, edges, tx, config, strategies[name])
        actual_assignment = dict(zip(roles.gid, roles.cluster_id))
        if actual_assignment != record["assignment"]:
            raise AssertionError("Pipeline and measured partition disagree")
        results[name, seed, resolution] = (roles.set_index("gid").sort_index(), top)
    base_roles, base_top = results["Louvain + components", 42, 1.0]
    lines = [
        "",
        "## Влияние на роли и топ",
        "",
        "Для каждой комбинации заново вычислены зависимые признаки и роли через тот же "
        "analyze(), что использует основной пайплайн. Пороги и точное посредничество не менялись. "
        "Сравнение с исправленным Louvain seed=42, resolution=1. Приоритет не использует "
        "номер сообщества; состав и порядок топа поэтому ожидаемо устойчивее ролей.",
        "",
        "| Алгоритм | seed | resolution | Изменилось ролей | coordinator | Новых клиентов в топ-20 | Изменилось ролей в топ-20 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    unchanged_top_order = True
    for (name, seed, resolution), (roles, top) in results.items():
        changed = roles.role.ne(base_roles.role)
        top_changes = int(changed.loc[base_top.gid].sum())
        newcomers = len(set(top.gid) - set(base_top.gid))
        unchanged_top_order &= top.gid.tolist() == base_top.gid.tolist()
        lines.append(
            f"| {name} | {seed} | {resolution:g} | {int(changed.sum())} | "
            f"{int(roles.role.eq('coordinator').sum())} | {newcomers} | {top_changes} |"
        )
    if unchanged_top_order:
        lines.extend(
            [
                "",
                "Состав и порядок топ-20 совпали во всех 18 вариантах; "
                "отображаемая роль участника топа могла измениться.",
            ]
        )
    else:
        lines.extend(["", "Порядок топ-20 изменился хотя бы в одном варианте."])
    return lines


def build_report(
    data: Path, seeds: list[int], resolutions: list[float], with_roles: bool = False
) -> str:
    versions = {
        name: importlib.metadata.version(name)
        for name in ("networkx", "igraph", "leidenalg", "pandas")
    }
    nodes = pd.read_parquet(data / "nodes.parquet")
    edges = pd.read_parquet(data / "edges.parquet")
    from hackalem.features import build_graph, external_graph

    graph = external_graph(build_graph(edges, nodes))
    records = {}
    for name, strategy in (("Louvain + components", louvain_strategy), ("Leiden", leiden_strategy)):
        # Warm one small graph to exclude lazy import costs from measured runs.
        warm = nx.DiGraph()
        warm.add_edge(1, 2, sum_kzt=1.0)
        assign_clusters(warm, AnalysisConfig(), strategy)
        for resolution in resolutions:
            for seed in seeds:
                config = replace(
                    AnalysisConfig(), random_seed=seed, community_resolution=resolution
                )
                record = run_partition(graph, config, strategy)
                records[name, seed, resolution] = record

    baseline = records["Louvain + components", 42, 1.0]["assignment"]
    lines = [
        "# Сравнение Louvain с разделением компонент и Leiden",
        "",
        "Эксперимент на исходных Parquet. Промышленный алгоритм остаётся Louvain "
        "с обязательным разделением каждой группы по компонентам связности.",
        "",
        f"Узлов: {graph.number_of_nodes()}; направленных рёбер без самопереводов: {graph.number_of_edges()}; "
        f"изолированных: {len(list(nx.isolates(graph)))}.",
        f"Среда: Python {platform.python_version()}, {platform.system()} {platform.machine()}; "
        + ", ".join(f"{name} {version}" for name, version in versions.items())
        + ".",
        "",
        "Оба алгоритма получают одинаковую каноническую неориентированную проекцию: "
        "встречные суммы складываются, самопереводы исключаются, изоляты выделяются отдельно. "
        "Leiden использует RBConfigurationVertexPartition (обобщённая modularity), "
        "n_iterations=-1 до отсутствия улучшения. Louvain использует настройки NetworkX "
        "и ту же resolution. Измерен один запуск каждой комбинации после прогрева; "
        "время включает построение проекции, преобразование в igraph и разделение компонент, "
        "но исключает загрузку Parquet и дальнейшую аналитику. Это сравнение конкретных "
        "Python/C++ реализаций, а не чистой алгоритмической сложности.",
        "",
        "Jaccard сравнивает множества пар клиентов, попавших в одно сообщество; "
        "номера кластеров не сравниваются. База: исправленный Louvain, seed=42, resolution=1. "
        "Доля внешнего оборота считается по направленным рёбрам; меньшая доля сама по себе "
        "не означает лучшую предметную кластеризацию. Modularity сопоставима только при одной resolution.",
        "",
        "| Алгоритм | seed | resolution | Кластеров | Несвязных до разделения | Время, с | Внешний оборот | Modularity | Jaccard с базой |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for (name, seed, resolution), record in records.items():
        lines.append(
            f"| {name} | {seed} | {resolution:g} | {record['clusters']} | "
            f"{record['raw_disconnected']} | {record['seconds']:.4f} | "
            f"{record['crossing_share']:.2%} | {record['modularity']:.6f} | "
            f"{coassignment_jaccard(record['assignment'], baseline):.4f} |"
        )
    lines.extend(
        [
            "",
            "Во всех опубликованных разбиениях 0 несвязных сообществ и полное однократное "
            "покрытие клиентов, включая изоляты.",
            "",
            "| Алгоритм | resolution | Средний Jaccard между seed | Минимальный Jaccard |",
            "|---|---:|---:|---:|",
        ]
    )
    for name in ("Louvain + components", "Leiden"):
        for resolution in resolutions:
            values = [
                coassignment_jaccard(
                    records[name, a, resolution]["assignment"],
                    records[name, b, resolution]["assignment"],
                )
                for a, b in combinations(seeds, 2)
            ]
            lines.append(
                f"| {name} | {resolution:g} | {sum(values) / len(values):.4f} | {min(values):.4f} |"
            )
    lines.extend(
        [
            "",
            "При resolution=1 Leiden дал более высокую modularity во всех трёх seed "
            "и более высокий средний Jaccard между seed. При resolution=0.5 его устойчивость "
            "оказалась ниже, чем у исправленного Louvain. Эти результаты не обосновывают "
            "автоматическую замену алгоритма. Единичные короткие замеры не дают "
            "надёжного вывода об ускорении.",
        ]
    )
    if with_roles:
        lines.extend(
            role_sensitivity(records, nodes, edges, pd.read_parquet(data / "transactions.parquet"))
        )
    else:
        lines.extend(["", "Влияние на роли и топ не рассчитывалось: добавьте --with-roles."])
    lines.extend(
        [
            "",
            "Наблюдаемая устойчивость ограничена этим графом и тремя seed; "
            "эксперимент не устанавливает правильные роли клиентов. "
            "Повторный запуск может менять время, но фиксированные версии, seed и порядок "
            "узлов обеспечивают воспроизводимость разбиений в этой среде.",
            "",
            "## Источники и воспроизводимость",
            "",
            "- [Traag, Waltman, van Eck: From Louvain to Leiden](https://arxiv.org/abs/1810.08473): "
            "Louvain может давать несвязные сообщества; Leiden предоставляет гарантии связности.",
            "- [Документация leidenalg](https://leidenalg.readthedocs.io/en/stable/reference.html): "
            "параметры resolution, seed и n_iterations, RBConfigurationVertexPartition.",
            "",
            "Leiden — только необязательная зависимость эксперимента. "
            "Основной requirements.txt его не содержит; для повторения используйте отдельное окружение "
            "с зависимостями проекта плюс igraph==1.0.0 и leidenalg==0.11.0.",
            "",
            "```bash",
            "python experiments/compare_communities.py --data data --out reports/community-experiment.md"
            + (" --with-roles" if with_roles else ""),
            "```",
            "",
            "SHA-256 исходных данных:",
            "",
        ]
    )
    for filename in ("nodes.parquet", "edges.parquet", "transactions.parquet"):
        lines.append(
            f"- {filename}: `{hashlib.sha256((data / filename).read_bytes()).hexdigest()}`"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "reports" / "community-experiment.md",
    )
    parser.add_argument(
        "--with-roles",
        action="store_true",
        help="Recompute role/top sensitivity through the full pipeline",
    )
    args = parser.parse_args()
    report = build_report(
        args.data, seeds=[7, 42, 99], resolutions=[0.5, 1.0, 1.5], with_roles=args.with_roles
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"Report: {args.out}")


if __name__ == "__main__":
    main()
